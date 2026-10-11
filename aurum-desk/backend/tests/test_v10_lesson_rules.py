import time
import json
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

import models, crud
from main import app
from services.lesson_rule_service import LessonRuleService, WHITELIST_METRICS
from services.eligibility_service import evaluate_setup_eligibility
from services.execution_coordinator import execution_coordinator
from services.trade_lifecycle_service import TradeLifecycleService
from domain_calculator import calculate_risk_reward

@pytest.fixture
def db(isolated_db):
    return isolated_db


def test_l01_green_advisory_does_not_change_baseline_decision(db):
    """L01: Green advisory rule adds notes but never alters baseline can_arm/can_execute."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms,
        title="Quan sát thanh khoản phiên London",
        category="PROCESS",
        reflection="Phiên London thường quét đỉnh đáy phiên Á",
        action_rule="Quan sát kỹ tín hiệu sweep trước khi vào lệnh",
        status="APPROVED",
        is_approved=True,
        enabled=True,
        severity="INFO",
        effect="ANNOTATE",
        validation_status="VALID"
    )
    db.add(rule)
    db.commit()
    LessonRuleService.invalidate_cache()

    setup = models.WatchSetup(
        id="setup-l01",
        direction="LONG",
        state="READY",
        provisional_entry=2000.0,
        provisional_sl=1990.0,
        provisional_tp=2030.0,
        confirmed_entry=2000.0,
        confirmed_sl=1990.0,
        confirmed_tp=2030.0,
        invalidation_price=1980.0,
        risk_pct=0.25,
        version=1,
        created_at=now_ms,
        updated_at=now_ms
    )
    db.add(setup)
    db.commit()

    elig = evaluate_setup_eligibility(db, setup, now_ms=now_ms)
    # Green advisory must appear in notes
    assert any("Quan sát thanh khoản" in n for n in elig["lesson_advisories"])
    # But it must NOT block arming
    assert "LESSON_RULE_BLOCKED" not in elig["reason_codes"]
    assert len(elig["lesson_blockers"]) == 0


def test_l02_yellow_warning_does_not_block_or_modify_prices(db):
    """L02: Yellow warning displays metric warning before entry, but does not block."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms,
        title="Cảnh báo spread giãn trên 0.35$",
        category="EXECUTION",
        reflection="Spread giãn cao làm giảm net R:R",
        action_rule="Hạn chế vào lệnh khi spread trên 0.35$",
        status="APPROVED",
        is_approved=True,
        enabled=True,
        severity="WARNING",
        effect="WARN_ENTRY",
        validation_status="VALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.35})
    )
    db.add(rule)
    db.commit()
    LessonRuleService.invalidate_cache()

    ctx = {
        "symbol": "XAUUSDT",
        "spread": 0.40,
        "now_ms": now_ms
    }
    rules = LessonRuleService.retrieve_active_rules(db, ctx, decision_time=now_ms)
    res = LessonRuleService.evaluate_rules(ctx, rules)

    assert res["can_proceed"] is True
    assert len(res["warning_messages"]) == 1
    assert "spread = 0.4" in res["warning_messages"][0]
    assert len(res["blocking_reasons"]) == 0


def test_l03_red_structured_active_matched_blocks_arm_and_fill(db):
    """L03: Red structured rule (active, valid, approved, enabled) blocks arming without incrementing counters."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms,
        title="Chặn vào lệnh khi spread vượt 0.50$",
        category="RISK",
        reflection="Spread quá cao gây rủi ro trượt giá nặng",
        action_rule="Chặn mọi lệnh khi spread >= 0.50$",
        status="APPROVED",
        is_approved=True,
        enabled=True,
        severity="CRITICAL",
        effect="BLOCK_ENTRY",
        validation_status="VALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.50})
    )
    db.add(rule)
    db.commit()
    LessonRuleService.invalidate_cache()

    ctx = {"symbol": "XAUUSDT", "spread": 0.55, "now_ms": now_ms}
    rules = LessonRuleService.retrieve_active_rules(db, ctx, decision_time=now_ms)
    res = LessonRuleService.evaluate_rules(ctx, rules)

    assert res["can_proceed"] is False
    assert len(res["blocking_reasons"]) == 1
    assert "LESSON_RULE_BLOCKED" in res["blocking_reasons"][0]


def test_l04_red_pending_disabled_invalid_out_of_scope_does_not_block(db):
    """L04: Red rule does NOT block if pending, disabled, invalid, or out of scope."""
    now_ms = int(time.time() * 1000)

    # 1. Pending rule
    r_pending = models.Lesson(
        created_at=now_ms, title="Pending rule", reflection="Ref", action_rule="Rule",
        status="PENDING_REVIEW", is_approved=False, enabled=True,
        severity="CRITICAL", effect="BLOCK_ENTRY", validation_status="VALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )
    # 2. Disabled rule
    r_disabled = models.Lesson(
        created_at=now_ms, title="Disabled rule", reflection="Ref", action_rule="Rule",
        status="APPROVED", is_approved=True, enabled=False,
        severity="CRITICAL", effect="BLOCK_ENTRY", validation_status="VALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )
    # 3. Invalid rule
    r_invalid = models.Lesson(
        created_at=now_ms, title="Invalid rule", reflection="Ref", action_rule="Rule",
        status="APPROVED", is_approved=True, enabled=True,
        severity="CRITICAL", effect="BLOCK_ENTRY", validation_status="INVALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )
    # 4. Out of scope rule (scoped to EURUSD only)
    r_scope = models.Lesson(
        created_at=now_ms, title="EURUSD rule", reflection="Ref", action_rule="Rule",
        status="APPROVED", is_approved=True, enabled=True,
        severity="CRITICAL", effect="BLOCK_ENTRY", validation_status="VALID",
        scope=json.dumps({"symbol": "EURUSD"}),
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )
    db.add_all([r_pending, r_disabled, r_invalid, r_scope])
    db.commit()
    LessonRuleService.invalidate_cache()

    ctx = {"symbol": "XAUUSDT", "spread": 0.60, "now_ms": now_ms}
    active_rules = LessonRuleService.retrieve_active_rules(db, ctx, decision_time=now_ms)

    # None of these should be active blocking rules for XAUUSDT!
    active_ids = {r.id for r in active_rules}
    assert r_pending.id not in active_ids
    assert r_disabled.id not in active_ids
    assert r_scope.id not in active_ids

    res = LessonRuleService.evaluate_rules(ctx, active_rules)
    assert res["can_proceed"] is True


def test_l05_archive_reject_excluded_and_pending_filter_isolated(client, db):
    """L05: Archived and rejected lessons are excluded from active retrieval, and PENDING filter excludes rejected."""
    now_ms = int(time.time() * 1000)

    l_pending = models.Lesson(
        created_at=now_ms, title="Bài học chờ duyệt", reflection="Ref", action_rule="Rule",
        status="PENDING_REVIEW", is_approved=False
    )
    l_rejected = models.Lesson(
        created_at=now_ms, title="Bài học bị từ chối", reflection="Ref", action_rule="Rule",
        status="REJECTED", is_approved=False
    )
    l_archived = models.Lesson(
        created_at=now_ms, title="Bài học lưu trữ", reflection="Ref", action_rule="Rule",
        status="ARCHIVED", is_approved=False
    )
    db.add_all([l_pending, l_rejected, l_archived])
    db.commit()

    # Query pending lessons via API
    res = client.get("/api/v1/lessons?status=PENDING_REVIEW").json()
    ids = [x["id"] for x in res]
    assert l_pending.id in ids
    assert l_rejected.id not in ids, "REJECTED lessons must NOT appear in PENDING_REVIEW filter!"
    assert l_archived.id not in ids, "ARCHIVED lessons must NOT appear in PENDING_REVIEW filter!"

    # Strategy memory retrieval must NOT retrieve archived or rejected
    strat_rules = crud.get_approved_lessons_for_strategy(db)
    strat_ids = [r.id for r in strat_rules]
    assert l_rejected.id not in strat_ids
    assert l_archived.id not in strat_ids


def test_l06_predicate_validation_and_cannot_loosen_baseline_rr():
    """L06: Predicate validator rejects invalid schemas and prevents loosening baseline min R:R."""
    # 1. Invalid metric
    ok, msg, rep = LessonRuleService.validate_predicate({"metric": "unknown_metric", "operator": "=="})
    assert ok is False
    assert "không thuộc danh sách cho phép" in msg

    # 2. Loosening Net R:R below 2.0 baseline
    ok, msg, rep = LessonRuleService.validate_predicate({"metric": "net_rr", "operator": ">=", "threshold": 1.5})
    assert ok is False
    assert "không được nới lỏng dưới mức chuẩn" in msg

    # 3. Valid stricter Net R:R (e.g. >= 2.5)
    ok, msg, rep = LessonRuleService.validate_predicate({"metric": "net_rr", "operator": ">=", "threshold": 2.5})
    assert ok is True

    # 4. Critical severity requires predicate
    ok, msg, rep = LessonRuleService.validate_predicate(None, severity="CRITICAL", effect="BLOCK_ENTRY")
    assert ok is False
    assert "bắt buộc phải có điều kiện có cấu trúc" in msg


def test_l07_editing_prose_does_not_change_trading_rules(client, db):
    """L07: Editing reflection or prose action_rule increments version but does not alter predicate evaluation."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms, title="Quy tắc ban đầu", reflection="Ban đầu", action_rule="Prose 1",
        status="APPROVED", is_approved=True, enabled=True, severity="INFO", effect="ANNOTATE",
        validation_status="VALID", version=1, revision=1
    )
    db.add(rule)
    db.commit()

    # Edit prose via API
    res = client.put(f"/api/v1/lessons/{rule.id}", json={
        "reflection": "Đã ghi chép thêm chi tiết sâu sắc",
        "action_rule": "Prose 2 cập nhật",
        "revision": 1
    })
    assert res.status_code == 200
    data = res.json()
    assert data["revision"] == 2
    assert data["reflection"] == "Đã ghi chép thêm chi tiết sâu sắc"
    assert data["effect"] == "ANNOTATE"


def test_l08_active_rule_edit_with_stale_revision_returns_409(client, db):
    """L08: Editing a lesson with outdated revision returns HTTP 409 STALE_EDIT."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms, title="Quy tắc đồng quy", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, revision=5
    )
    db.add(rule)
    db.commit()

    # Submit with outdated revision (e.g. revision 4)
    res = client.put(f"/api/v1/lessons/{rule.id}", json={
        "title": "Sửa đổi xung đột",
        "revision": 4
    })
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "STALE_EDIT"


def test_l09_rule_block_in_ready_preserves_strategy_state(db):
    """L09: When rule blocks arming, strategy_state remains READY while can_arm becomes False."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms, title="Hạn chế entry phiên sáng", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, enabled=True,
        severity="CRITICAL", effect="BLOCK_ENTRY", validation_status="VALID",
        predicate=json.dumps({"metric": "net_rr", "operator": ">=", "threshold": 3.0})  # requires >= 3.0 R:R
    )
    db.add(rule)

    setup = models.WatchSetup(
        id="setup-l09",
        direction="LONG",
        state="READY",
        provisional_entry=2000.0,
        provisional_sl=1990.0,
        provisional_tp=2025.0,  # ~2.5 R:R, passes baseline 2.0 but violates rule 3.0
        confirmed_entry=2000.0,
        confirmed_sl=1990.0,
        confirmed_tp=2025.0,
        invalidation_price=1980.0,
        risk_pct=0.25,
        version=1,
        created_at=now_ms,
        updated_at=now_ms
    )
    db.add(setup)
    db.commit()
    LessonRuleService.invalidate_cache()

    elig = evaluate_setup_eligibility(db, setup, now_ms=now_ms)
    assert elig["strategy_state"] == "READY", "Strategy state must stay READY!"
    assert elig["can_arm"] is False, "can_arm must be False due to lesson rule blocker"
    assert "LESSON_RULE_BLOCKED" in elig["reason_codes"]


def test_l10_before_arm_passes_before_fill_matches_and_rejects_safely(db):
    """L10: Order armed successfully, but before fill quote spread widens, triggering BEFORE_FILL rejection safely."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms, title="Chặn fill khi spread giãn", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, enabled=True,
        severity="CRITICAL", effect="BLOCK_ENTRY", validation_status="VALID",
        stage="BEFORE_FILL",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.40})
    )
    db.add(rule)

    audit = crud.get_or_create_today_audit(db)
    initial_fills = audit.fills_count

    order = models.PaperOrder(
        id="order-l10",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="MARKET",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2030.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        risk_pct=0.25,
        leverage=5,
        margin_mode="ISOLATED",
        created_at=now_ms
    )
    db.add(order)
    db.commit()
    LessonRuleService.invalidate_cache()

    # Trigger fill with wide spread (ask 2000.50, bid 2000.00 -> spread 0.50 >= 0.40)
    wide_ticker = {
        "symbol": "XAUUSDT",
        "ask": "2000.50",
        "bid": "2000.00",
        "last": "2000.25",
        "timestamp": now_ms
    }
    execution_coordinator.evaluate_orders_sync(db, ticker_override=wide_ticker, now_ms=now_ms)

    db.refresh(order)
    assert order.state == "rejected", "Order must be rejected due to BEFORE_FILL lesson rule violation"
    assert "LESSON_RULE_BLOCKED" in order.invalidation_reason

    # Audit fills count must NOT be incremented!
    db.refresh(audit)
    assert audit.fills_count == initial_fills


def test_l11_open_position_with_new_red_rule_does_not_alter_levels_or_exit(db):
    """L11: Open position levels are immutable; exit monitor still closes on TP/SL despite new red rule."""
    now_ms = int(time.time() * 1000)

    # 1. Existing open position
    pos = models.PaperOrder(
        id="pos-l11",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2000.0,
        actual_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2030.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        risk_pct=0.25,
        leverage=5,
        margin_mode="ISOLATED",
        opened_at=now_ms - 10000,
        created_at=now_ms - 10000
    )
    db.add(pos)
    db.commit()

    # 2. Add an aggressive red rule afterwards
    rule = models.Lesson(
        created_at=now_ms, title="Khóa toàn bộ thị trường", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, enabled=True,
        severity="CRITICAL", effect="BLOCK_ENTRY", validation_status="VALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.01})
    )
    db.add(rule)
    db.commit()
    LessonRuleService.invalidate_cache()

    # 3. Position levels must NOT change
    db.refresh(pos)
    assert pos.actual_entry == 2000.0
    assert pos.stop_loss == 1990.0
    assert pos.take_profit == 2030.0

    # 4. Exit monitor still executes TP normally
    closed = TradeLifecycleService.process_exit_tick(db, current_bid=2030.50, current_ask=2030.60, now_ms=now_ms)
    assert closed is not None
    assert closed.id == "pos-l11"
    assert closed.exit_cause == "TP_HIT"
    assert closed.state == "closed"


def test_l12_missing_evidence_fail_policy_does_not_crash(db):
    """L12: Missing evidence in context handles gracefully without throwing exceptions."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms, title="Yêu cầu có sweep thanh khoản", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, enabled=True,
        severity="WARNING", effect="WARN_ENTRY", validation_status="VALID",
        predicate=json.dumps({"metric": "evidence.sweep_detected", "operator": "==", "value": True})
    )
    db.add(rule)
    db.commit()

    ctx = {"symbol": "XAUUSDT", "now_ms": now_ms}  # no evidence provided
    rules = LessonRuleService.retrieve_active_rules(db, ctx, decision_time=now_ms)
    res = LessonRuleService.evaluate_rules(ctx, rules)

    assert res["can_proceed"] is True
    assert res["evaluations"][0]["data_unavailable"] is True


def test_l13_multiple_rules_stricter_constraint_wins_and_green_never_overrides_red(db):
    """L13: Multiple rules evaluate together: Green advisory never bypasses Red blocker."""
    now_ms = int(time.time() * 1000)
    r_green = models.Lesson(
        created_at=now_ms, title="Xanh tham khảo", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, enabled=True, severity="INFO", effect="ANNOTATE", validation_status="VALID"
    )
    r_red = models.Lesson(
        created_at=now_ms, title="Đỏ chặn", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, enabled=True, severity="CRITICAL", effect="BLOCK_ENTRY",
        validation_status="VALID", predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.30})
    )
    db.add_all([r_green, r_red])
    db.commit()
    LessonRuleService.invalidate_cache()

    ctx = {"symbol": "XAUUSDT", "spread": 0.40, "now_ms": now_ms}
    rules = LessonRuleService.retrieve_active_rules(db, ctx, decision_time=now_ms)
    res = LessonRuleService.evaluate_rules(ctx, rules)

    # Green note must be present
    assert len(res["advisory_notes"]) == 1
    # But Red rule MUST strictly block
    assert res["can_proceed"] is False
    assert len(res["blocking_reasons"]) == 1


def test_l14_scope_filtering_manual_vs_auto_and_direction(db):
    """L14: Scope filtering correctly isolates AUTO vs MANUAL and LONG vs SHORT."""
    now_ms = int(time.time() * 1000)
    r_short = models.Lesson(
        created_at=now_ms, title="Chặn lệnh SHORT khi tin tức mạnh", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, enabled=True, severity="CRITICAL", effect="BLOCK_ENTRY",
        validation_status="VALID",
        scope=json.dumps({"direction": "SHORT", "execution_mode": "AUTO"}),
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )
    db.add(r_short)
    db.commit()
    LessonRuleService.invalidate_cache()

    # Context LONG -> should NOT match
    ctx_long = {"symbol": "XAUUSDT", "direction": "LONG", "execution_mode": "AUTO", "spread": 0.30, "now_ms": now_ms}
    rules_long = LessonRuleService.retrieve_active_rules(db, ctx_long, decision_time=now_ms)
    assert len(rules_long) == 0

    # Context SHORT AUTO -> matches
    ctx_short = {"symbol": "XAUUSDT", "direction": "SHORT", "execution_mode": "AUTO", "spread": 0.30, "now_ms": now_ms}
    rules_short = LessonRuleService.retrieve_active_rules(db, ctx_short, decision_time=now_ms)
    assert len(rules_short) == 1


def test_l16_lessons_retrieved_trace_snapshot_is_immutable(db):
    """L16: Lessons retrieved snapshot stored on trade is immutable and preserved when rule is edited later."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms, title="Quy tắc lịch sử", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, enabled=True, severity="INFO", effect="ANNOTATE",
        validation_status="VALID", version=1
    )
    db.add(rule)
    db.commit()

    snapshot = [{"lesson_id": rule.id, "version": 1, "title": "Quy tắc lịch sử", "severity": "INFO"}]
    order = models.PaperOrder(
        id="order-l16",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2000.0,
        actual_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2030.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        risk_pct=0.25,
        leverage=5,
        margin_mode="ISOLATED",
        created_at=now_ms,
        lessons_retrieved=json.dumps(snapshot)
    )
    db.add(order)
    db.commit()

    # Now modify the rule title and version
    rule.title = "Quy tắc đã bị đổi tên"
    rule.version = 2
    db.commit()

    # The order snapshot must still have original title and version 1
    db.refresh(order)
    order_snapshot = json.loads(order.lessons_retrieved)
    assert order_snapshot[0]["title"] == "Quy tắc lịch sử"
    assert order_snapshot[0]["version"] == 1


def test_l18_shadow_mode_records_would_block_without_blocking(db):
    """L18: Shadow mode records would_block=True but leaves can_proceed=True."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms, title="Quy tắc Shadow", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, enabled=True, severity="CRITICAL", effect="BLOCK_ENTRY",
        validation_status="VALID",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )
    db.add(rule)
    db.commit()
    LessonRuleService.invalidate_cache()

    ctx = {"symbol": "XAUUSDT", "spread": 0.35, "now_ms": now_ms}
    rules = LessonRuleService.retrieve_active_rules(db, ctx, decision_time=now_ms)

    res = LessonRuleService.evaluate_rules(ctx, rules, feature_flags={"lesson_shadow_mode": True})
    assert res["can_proceed"] is True, "Shadow mode must NOT block entry!"
    assert any("SHADOW MODE" in w for w in res["warning_messages"])
    assert res["evaluations"][0]["would_block"] is True


def test_l21_toggle_enable_endpoint(client, db):
    """L21: Toggle enable endpoint enables/disables a rule independently of approval."""
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms, title="Toggle rule", reflection="R", action_rule="A",
        status="APPROVED", is_approved=True, enabled=True
    )
    db.add(rule)
    db.commit()

    # Toggle off
    res = client.post(f"/api/v1/lessons/{rule.id}/toggle-enable")
    assert res.status_code == 200
    assert res.json()["enabled"] is False

    # Toggle back on
    res2 = client.post(f"/api/v1/lessons/{rule.id}/toggle-enable")
    assert res2.status_code == 200
    assert res2.json()["enabled"] is True
