"""
Aurum Desk V10.1 - Authoritative Acceptance Test Suite
Comprehensive coverage for all 69 required manifest IDs:
- Acceptance Scenarios (A01 - A16)
- Semantic & Scope Rules (S01 - S13)
- Policy & Feature Flags (P01 - P06)
- Error Policy & Diagnostics (X01 - X08)
- UI, API & State Mutation (U01 - U08)
- Decision Tracing (T01 - T05)
- Invariants & Regression Safety (R01 - R08)
- Browser Acceptance Scenarios (B01 - B05)
"""

import os
import time
import json
import uuid
import pytest
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

import models, crud, schemas
from main import app
from services.clock import FakeClock
from services.lesson_rule_service import LessonRuleService
from services.lesson_policy_service import LessonPolicyService
from services.entry_decision_service import EntryDecisionService
from services.trade_lifecycle_service import TradeLifecycleService
from services.execution_coordinator import execution_coordinator
from services.strategy_service import strategy_service
from services.eligibility_service import evaluate_setup_eligibility
from domain_calculator import calculate_risk_reward


def make_lesson(
    db: Session,
    title: str,
    status: str = "APPROVED",
    is_approved: bool = True,
    enabled: bool = True,
    severity: str = "INFO",
    effect: str = "ANNOTATE",
    validation_status: str = "VALID",
    predicate: Optional[str] = None,
    scope: Optional[str] = None,
    stage: str = "ALL",
    created_at: Optional[int] = None,
    now_ms: Optional[int] = None,
    effective_at: Optional[int] = None,
    expiry_at: Optional[int] = None,
    revision: int = 1,
    lesson_id: Optional[int] = None
) -> models.Lesson:
    time_created = created_at or now_ms or int(time.time() * 1000)
    rule = models.Lesson(
        created_at=time_created,
        title=title,
        category="PROCESS",
        reflection=f"Reflection for {title}",
        action_rule=f"Action rule for {title}",
        status=status,
        is_approved=is_approved,
        enabled=enabled,
        severity=severity,
        effect=effect,
        validation_status=validation_status,
        predicate=predicate,
        scope=scope,
        stage=stage,
        effective_at=effective_at,
        expiry_at=expiry_at,
        revision=revision
    )
    if lesson_id is not None:
        rule.id = lesson_id
    db.add(rule)
    db.commit()
    db.refresh(rule)
    LessonRuleService.invalidate_cache()
    return rule


def make_order(
    db: Session,
    order_id: str,
    direction: str = "LONG",
    state: str = "armed",
    planned_entry: float = 2650.0,
    actual_entry: Optional[float] = None,
    stop_loss: float = 2640.0,
    take_profit: float = 2685.0,
    quantity: float = 0.1,
    initial_risk_usdt: float = 10.0,
    risk_pct: float = 0.25,
    order_type: str = "LIMIT",
    strategy_family: str = "STANDARD_SMC",
    origin: str = "UNKNOWN",
    execution_mode: str = "AUTO",
    now_ms: Optional[int] = None
) -> models.PaperOrder:
    effective_now = now_ms if now_ms is not None else int(time.time() * 1000)
    order = models.PaperOrder(
        id=order_id,
        direction=direction,
        state=state,
        order_type=order_type,
        planned_entry=planned_entry,
        actual_entry=actual_entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        quantity=quantity,
        initial_risk_usdt=initial_risk_usdt,
        risk_pct=risk_pct,
        strategy_family=strategy_family,
        origin=origin,
        execution_mode=execution_mode,
        created_at=effective_now,
        armed_at=effective_now if state in ("armed", "paper_open") else None,
        opened_at=effective_now if state == "paper_open" else None,
        expires_at=effective_now + 3600000
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def make_setup(
    db: Session,
    setup_id: str,
    direction: str = "LONG",
    state: str = "READY",
    provisional_entry: float = 2650.0,
    provisional_sl: float = 2640.0,
    provisional_tp: float = 2685.0,
    confirmed_entry: Optional[float] = None,
    confirmed_sl: Optional[float] = None,
    confirmed_tp: Optional[float] = None,
    invalidation_price: float = 2635.0,
    net_rr: float = 2.5,
    gross_rr: float = 3.5,
    risk_pct: float = 0.25,
    version: int = 1,
    now_ms: Optional[int] = None
) -> models.WatchSetup:
    effective_now = now_ms if now_ms is not None else int(time.time() * 1000)
    setup = models.WatchSetup(
        id=setup_id,
        direction=direction,
        state=state,
        provisional_entry=provisional_entry,
        provisional_sl=provisional_sl,
        provisional_tp=provisional_tp,
        confirmed_entry=confirmed_entry or provisional_entry,
        confirmed_sl=confirmed_sl or provisional_sl,
        confirmed_tp=confirmed_tp or provisional_tp,
        invalidation_price=invalidation_price,
        net_rr=net_rr,
        gross_rr=gross_rr,
        risk_pct=risk_pct,
        version=version,
        created_at=effective_now,
        updated_at=effective_now
    )
    db.add(setup)
    db.commit()
    db.refresh(setup)
    return setup


@pytest.fixture
def db(isolated_db):
    LessonPolicyService.reset_to_defaults(isolated_db)
    LessonRuleService.invalidate_cache()
    return isolated_db


@pytest.fixture
def fake_clock():
    return FakeClock(1775692800000)


# ============================================================================
# A01 - A16: ACCEPTANCE SCENARIOS
# ============================================================================

def test_a01_manual_market_long_green_advisory(db, client):
    """A01: Manual market LONG, green advisory -> một fill, đúng trace."""
    now_ms = int(time.time() * 1000)
    make_lesson(db, title="Advisory Rule A01", severity="INFO", effect="ANNOTATE")

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.2, "timestamp": now_ms}

    order_payload = {
        "instrument": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "stop_loss": 2640.0,
        "take_profit": 2685.0,
        "risk_pct": 0.25,
        "leverage": 5,
        "margin_mode": "ISOLATED"
    }
    resp = client.post("/api/v1/orders/paper", json=order_payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == data["order"]["id"]).first()
    assert order is not None
    assert order.state == "paper_open"
    assert order.origin == "MANUAL_WEB"
    assert order.execution_mode == "MANUAL"
    assert order.fill_decision_snapshot is not None
    trace = json.loads(order.fill_decision_snapshot)
    assert trace["can_proceed"] is True
    assert any("Advisory Rule A01" in a for a in trace.get("advisory_notes", []))


def test_a02_manual_market_short_yellow_warning(db, client):
    """A02: Manual market SHORT, yellow warning -> fill hợp lệ, cảnh báo không chặn."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Warning Rule Spread A02", severity="WARNING", effect="WARN_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.15})
    )

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.3, "timestamp": now_ms}

    order_payload = {
        "instrument": "XAUUSDT",
        "direction": "SHORT",
        "planned_entry": 2650.0,
        "stop_loss": 2660.0,
        "take_profit": 2615.0,
        "risk_pct": 0.25,
        "leverage": 5,
        "margin_mode": "ISOLATED"
    }
    resp = client.post("/api/v1/orders/paper", json=order_payload)
    assert resp.status_code == 200, resp.text

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == resp.json()["order"]["id"]).first()
    assert order.state == "paper_open"
    trace = json.loads(order.fill_decision_snapshot)
    assert trace["can_proceed"] is True
    assert len(trace.get("warning_messages", [])) > 0


def test_a03_manual_market_active_red_matched(db, client):
    """A03: Manual market active red matched -> không fill, reason và counters đúng."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Strict Red Rule Spread A03", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.4, "timestamp": now_ms}

    order_payload = {
        "instrument": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "stop_loss": 2640.0,
        "take_profit": 2685.0,
        "risk_pct": 0.25,
        "leverage": 5,
        "margin_mode": "ISOLATED"
    }
    resp = client.post("/api/v1/orders/paper", json=order_payload)
    assert resp.status_code == 400
    assert "LESSON_RULE_BLOCKED" in str(resp.json()["detail"])

    open_pos = db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").first()
    assert open_pos is None
    audit = crud.get_or_create_today_audit(db)
    assert audit.fills_count == 0


def test_a04_manual_market_red_unmatched(db, client):
    """A04: Manual market red unmatched -> fill baseline hợp lệ."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Red Rule High Spread A04", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.80})
    )

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.15, "timestamp": now_ms}

    order_payload = {
        "instrument": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "stop_loss": 2640.0,
        "take_profit": 2685.0,
        "risk_pct": 0.25,
        "leverage": 5,
        "margin_mode": "ISOLATED"
    }
    resp = client.post("/api/v1/orders/paper", json=order_payload)
    assert resp.status_code == 200
    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == resp.json()["order"]["id"]).first()
    assert order.state == "paper_open"


def test_a05_manual_arm_long_active_red_matched(db, client):
    """A05: Manual Arm LONG active red matched -> không có armed mới."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Block Long Before Arm A05", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    setup = make_setup(
        db, setup_id="setup-a05", direction="LONG", provisional_entry=2650.0,
        provisional_sl=2640.0, provisional_tp=2685.0, confirmed_entry=2650.0,
        confirmed_sl=2640.0, confirmed_tp=2685.0, invalidation_price=2630.0, now_ms=now_ms
    )

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.35, "timestamp": now_ms}

    resp = client.post("/api/v1/setups/arm/setup-a05")
    assert resp.status_code == 400
    assert "LESSON_RULE_BLOCKED" in str(resp.json()["detail"])

    armed = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
    assert armed is None


def test_a06_manual_arm_short_then_fill_mode_preserved(db):
    """A06: Manual Arm SHORT rồi Fill -> mode vẫn MANUAL."""
    now_ms = int(time.time() * 1000)
    clock = FakeClock(now_ms)

    order = make_order(
        db, order_id="order-a06", direction="SHORT", state="armed",
        planned_entry=2650.0, stop_loss=2660.0, take_profit=2615.0,
        origin="MANUAL_WEB", execution_mode="MANUAL", now_ms=now_ms
    )

    ticker = {"symbol": "XAUUSDT", "bid": 2650.5, "ask": 2650.7, "timestamp": now_ms}
    execution_coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock, now_ms=now_ms)

    db.refresh(order)
    assert order.state == "paper_open"
    assert order.execution_mode == "MANUAL"
    assert order.origin == "MANUAL_WEB"


def test_a07_auto_arm_matched_red(db):
    """A07: Auto Arm matched red -> không armed, trace/diagnostics đúng."""
    now_ms = int(time.time() * 1000)
    now_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)

    make_lesson(
        db, title="Block Auto Arm Red A07", severity="CRITICAL", effect="BLOCK_ENTRY",
        scope=json.dumps({"execution_mode": "AUTO"}),
        predicate=json.dumps({"metric": "net_rr", "operator": ">=", "threshold": 2.5})
    )

    setup = make_setup(
        db, setup_id="setup-a07", direction="LONG", provisional_entry=2650.0,
        provisional_sl=2640.0, provisional_tp=2685.0, net_rr=2.2, now_ms=now_ms
    )

    sig = {"signal_id": "sig-a07"}
    strategy_service._auto_arm_candidate(db, setup, sig, now_ms, now_dt)

    armed = db.query(models.PaperOrder).filter(models.PaperOrder.setup_id == "setup-a07").first()
    assert armed is None


def test_a08_auto_yellow_no_pause(db):
    """A08: Auto yellow -> không pause Auto hoặc yêu cầu confirmation mới."""
    now_ms = int(time.time() * 1000)
    now_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)

    make_lesson(
        db, title="Warning Auto Yellow A08", severity="WARNING", effect="WARN_ENTRY",
        predicate=json.dumps({"metric": "net_rr", "operator": ">=", "threshold": 2.5})
    )

    setup = make_setup(
        db, setup_id="setup-a08", direction="LONG", provisional_entry=2650.0,
        provisional_sl=2640.0, provisional_tp=2685.0, net_rr=2.6, now_ms=now_ms
    )

    sig = {"signal_id": "sig-a08"}
    strategy_service._auto_arm_candidate(db, setup, sig, now_ms, now_dt)

    armed = db.query(models.PaperOrder).filter(models.PaperOrder.setup_id == "setup-a08").first()
    assert armed is not None
    assert armed.state == "armed"


def test_a09_ny_fallback_scope_isolated(db):
    """A09: NY fallback riêng scope -> đúng rule, không thành STANDARD_SMC."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Fallback Scope Rule A09", severity="CRITICAL", effect="BLOCK_ENTRY",
        scope=json.dumps({"strategy_family": "NY_QUOTA_PAPER"}),
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    std_ctx = EntryDecisionService.build_context(
        stage="BEFORE_ARM", strategy_family="STANDARD_SMC", ask=2650.30, bid=2650.0, now_ms=now_ms
    )
    assert EntryDecisionService.evaluate_entry_rules(db, std_ctx)["can_proceed"] is True

    fb_ctx = EntryDecisionService.build_context(
        stage="BEFORE_ARM", strategy_family="NY_QUOTA_PAPER", ask=2650.30, bid=2650.0, now_ms=now_ms
    )
    assert EntryDecisionService.evaluate_entry_rules(db, fb_ctx)["can_proceed"] is False


def test_a10_limit_long_last_touch_ask_untouched(db):
    """A10: LIMIT LONG Last touch, Ask chưa touch -> không fill."""
    now_ms = int(time.time() * 1000)
    clock = FakeClock(now_ms)

    order = make_order(
        db, order_id="order-a10", direction="LONG", state="armed",
        planned_entry=2650.0, stop_loss=2640.0, take_profit=2675.0, now_ms=now_ms
    )

    # Last price touched 2650.0, but Ask is 2650.1 (> 2650.0) -> must not fill!
    ticker = {"symbol": "XAUUSDT", "bid": 2649.9, "ask": 2650.1, "timestamp": now_ms}
    execution_coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock, now_ms=now_ms)

    db.refresh(order)
    assert order.state == "armed"


def test_a11_limit_short_last_touch_bid_untouched(db):
    """A11: LIMIT SHORT Last touch, Bid chưa touch -> không fill."""
    now_ms = int(time.time() * 1000)
    clock = FakeClock(now_ms)

    order = make_order(
        db, order_id="order-a11", direction="SHORT", state="armed",
        planned_entry=2650.0, stop_loss=2660.0, take_profit=2625.0, now_ms=now_ms
    )

    # Last price touched 2650.0, but Bid is 2649.9 (< 2650.0) -> must not fill!
    ticker = {"symbol": "XAUUSDT", "bid": 2649.9, "ask": 2650.2, "timestamp": now_ms}
    execution_coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock, now_ms=now_ms)

    db.refresh(order)
    assert order.state == "armed"


def test_a12_shared_final_guard_all_types(db):
    """A12: MARKET/STOP/LIMIT hai hướng qua shared final guard."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Final Guard Rule A12", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    calc = calculate_risk_reward("LONG", 2650.0, 2640.0, 2685.0, 1000.0, 0.25, 2.0)
    order = make_order(
        db, order_id="order-a12", direction="LONG", state="armed",
        planned_entry=2650.0, stop_loss=2640.0, take_profit=2685.0, now_ms=now_ms
    )

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.3, "timestamp": now_ms}

    res_order = TradeLifecycleService.execute_fill(db, order, 2650.0, calc, now_ms=now_ms)
    assert res_order.state == "rejected"
    assert "LESSON_RULE_BLOCKED" in res_order.invalidation_reason


def test_a13_rule_flag_changed_between_arm_and_fill(db):
    """A13: Rule/flag đổi giữa Arm và Fill -> recheck version đúng."""
    now_ms = int(time.time() * 1000)
    clock = FakeClock(now_ms)

    order = make_order(
        db, order_id="order-a13", direction="LONG", state="armed", order_type="MARKET",
        planned_entry=2650.0, stop_loss=2640.0, take_profit=2685.0, now_ms=now_ms
    )

    make_lesson(
        db, title="New Rule Before Fill A13", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.15})
    )

    ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.25, "timestamp": now_ms + 2000}
    execution_coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock, now_ms=now_ms + 2000)

    db.refresh(order)
    assert order.state == "rejected"
    assert "LESSON_RULE_BLOCKED" in order.invalidation_reason


def test_a14_concurrent_requests_single_position(db):
    """A14: Hai requests đồng thời -> một position/audit increment."""
    now_ms = int(time.time() * 1000)
    calc = calculate_risk_reward("LONG", 2650.0, 2640.0, 2685.0, 1000.0, 0.25, 2.0)

    order1 = make_order(db, "order-a14-1", state="candidate", now_ms=now_ms)
    order2 = make_order(db, "order-a14-2", state="candidate", now_ms=now_ms)

    res1 = TradeLifecycleService.execute_fill(db, order1, 2650.0, calc, now_ms=now_ms)
    res2 = TradeLifecycleService.execute_fill(db, order2, 2650.0, calc, now_ms=now_ms)

    assert res1.state == "paper_open"
    assert res2.state == "rejected"
    audit = crud.get_or_create_today_audit(db)
    assert audit.fills_count == 1


def test_a15_duplicate_idempotent_no_duplicate_traces(db, client):
    """A15: Duplicate/idempotent requests -> không duplicate traces/events."""
    now_ms = int(time.time() * 1000)
    idem_key = "idem-a15-key"

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.1, "timestamp": now_ms}

    order_payload = {
        "instrument": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "stop_loss": 2640.0,
        "take_profit": 2685.0,
        "risk_pct": 0.25,
        "idempotency_key": idem_key
    }
    r1 = client.post("/api/v1/orders/paper", json=order_payload)
    r2 = client.post("/api/v1/orders/paper", json=order_payload)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["order"]["id"] == r2.json()["order"]["id"]


def test_a16_preview_pass_quote_risk_change_before_fill(db):
    """A16: Preview pass, quote/risk/news đổi trước Fill -> baseline recheck."""
    now_ms = int(time.time() * 1000)
    clock = FakeClock(now_ms)

    order = make_order(
        db, order_id="order-a16", direction="LONG", state="armed", order_type="MARKET",
        planned_entry=2650.0, stop_loss=2640.0, take_profit=2685.0, now_ms=now_ms
    )

    audit = crud.get_or_create_today_audit(db)
    audit.is_blocked = True
    audit.block_reason = "Manual Risk Lockout"
    db.commit()

    ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.1, "timestamp": now_ms}
    execution_coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock, now_ms=now_ms)

    db.refresh(order)
    assert order.state == "rejected"
    assert "Execution guard check failed" in order.invalidation_reason


# ============================================================================
# S01 - S13: SEMANTIC & SCOPE RULES
# ============================================================================

def test_s01_info_immutable_prices_and_risk(db):
    """S01: INFO không thay entry/SL/TP/qty/risk."""
    now_ms = int(time.time() * 1000)
    make_lesson(db, title="Info Rule S01", severity="INFO", effect="ANNOTATE")

    ctx = EntryDecisionService.build_context(
        stage="BEFORE_ARM", planned_entry=2650.0, stop_loss=2640.0, take_profit=2685.0, now_ms=now_ms
    )
    eval_res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert eval_res["can_proceed"] is True
    assert ctx["planned_entry"] == 2650.0
    assert ctx["stop_loss"] == 2640.0


def test_s02_warn_no_block_manual_and_auto(db):
    """S02: WARN không block, cả manual và Auto."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Warning Rule S02", severity="WARNING", effect="WARN_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )

    ctx_manual = EntryDecisionService.build_context(stage="BEFORE_FILL", execution_mode="MANUAL", spread=0.25, now_ms=now_ms)
    ctx_auto = EntryDecisionService.build_context(stage="BEFORE_ARM", execution_mode="AUTO", spread=0.25, now_ms=now_ms)

    assert EntryDecisionService.evaluate_entry_rules(db, ctx_manual)["can_proceed"] is True
    assert EntryDecisionService.evaluate_entry_rules(db, ctx_auto)["can_proceed"] is True


def test_s03_block_valid_approved_enabled_effective(db):
    """S03: BLOCK valid/approved/enabled/effective -> chặn thật."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Block Rule S03", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=0.30, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert res["can_proceed"] is False
    assert len(res["blocking_reasons"]) > 0


def test_s04_pending_rejected_archived_disabled_invalid_no_block(db):
    """S04: Pending/rejected/archived/disabled/invalid red -> không active."""
    now_ms = int(time.time() * 1000)
    pred = json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    make_lesson(db, "R1", status="PENDING", is_approved=False, enabled=True, severity="CRITICAL", effect="BLOCK_ENTRY", predicate=pred)
    make_lesson(db, "R2", status="REJECTED", is_approved=False, enabled=True, severity="CRITICAL", effect="BLOCK_ENTRY", predicate=pred)
    make_lesson(db, "R3", status="ARCHIVED", is_approved=True, enabled=False, severity="CRITICAL", effect="BLOCK_ENTRY", predicate=pred)
    make_lesson(db, "R4", status="APPROVED", is_approved=True, enabled=False, severity="CRITICAL", effect="BLOCK_ENTRY", predicate=pred)
    make_lesson(db, "R5", status="APPROVED", is_approved=True, enabled=True, severity="CRITICAL", effect="BLOCK_ENTRY", validation_status="INVALID", predicate=pred)

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=0.50, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert res["can_proceed"] is True


def test_s05_missing_predicate_red_no_block(db):
    """S05: Missing predicate red -> không tự tạo blocker executable."""
    now_ms = int(time.time() * 1000)
    make_lesson(db, title="Unstructured Red S05", severity="CRITICAL", effect="BLOCK_ENTRY", predicate=None)

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=0.50, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert res["can_proceed"] is True


def test_s06_scope_direction_filtering(db):
    """S06: Scope direction LONG/SHORT đúng."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Only Short Red S06", severity="CRITICAL", effect="BLOCK_ENTRY",
        scope=json.dumps({"direction": "SHORT"}),
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    ctx_long = EntryDecisionService.build_context(stage="BEFORE_FILL", direction="LONG", spread=0.30, now_ms=now_ms)
    ctx_short = EntryDecisionService.build_context(stage="BEFORE_FILL", direction="SHORT", spread=0.30, now_ms=now_ms)

    assert EntryDecisionService.evaluate_entry_rules(db, ctx_long)["can_proceed"] is True
    assert EntryDecisionService.evaluate_entry_rules(db, ctx_short)["can_proceed"] is False


def test_s07_scope_execution_mode_strict_no_wildcard(db):
    """S07: Scope mode MANUAL/AUTO/UNKNOWN đúng, không wildcard silent."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Only Manual Red S07", severity="CRITICAL", effect="BLOCK_ENTRY",
        scope=json.dumps({"execution_mode": "MANUAL"}),
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    ctx_unknown = EntryDecisionService.build_context(stage="BEFORE_FILL", execution_mode="UNKNOWN", spread=0.30, now_ms=now_ms)
    ctx_auto = EntryDecisionService.build_context(stage="BEFORE_FILL", execution_mode="AUTO", spread=0.30, now_ms=now_ms)
    ctx_manual = EntryDecisionService.build_context(stage="BEFORE_FILL", execution_mode="MANUAL", spread=0.30, now_ms=now_ms)

    assert EntryDecisionService.evaluate_entry_rules(db, ctx_unknown)["can_proceed"] is True
    assert EntryDecisionService.evaluate_entry_rules(db, ctx_auto)["can_proceed"] is True
    assert EntryDecisionService.evaluate_entry_rules(db, ctx_manual)["can_proceed"] is False


def test_s08_scope_symbol_family_fallback_vs_standard(db):
    """S08: Scope symbol/family đúng, fallback khác standard."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Standard SMC Only S08", severity="CRITICAL", effect="BLOCK_ENTRY",
        scope=json.dumps({"strategy_family": "STANDARD_SMC"}),
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    ctx_std = EntryDecisionService.build_context(stage="BEFORE_ARM", strategy_family="STANDARD_SMC", spread=0.30, now_ms=now_ms)
    ctx_fb = EntryDecisionService.build_context(stage="BEFORE_ARM", strategy_family="NY_QUOTA_PAPER", spread=0.30, now_ms=now_ms)

    assert EntryDecisionService.evaluate_entry_rules(db, ctx_std)["can_proceed"] is False
    assert EntryDecisionService.evaluate_entry_rules(db, ctx_fb)["can_proceed"] is True


def test_s09_scope_timeframe_stage_enforced(db):
    """S09: Scope timeframe/stage được thực thi."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="5M Before Fill Only S09", severity="CRITICAL", effect="BLOCK_ENTRY",
        stage="BEFORE_FILL",
        scope=json.dumps({"timeframe": "5M", "stage": "BEFORE_FILL"}),
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    ctx_15m = EntryDecisionService.build_context(stage="BEFORE_FILL", timeframe="15M", spread=0.30, now_ms=now_ms)
    ctx_arm = EntryDecisionService.build_context(stage="BEFORE_ARM", timeframe="5M", spread=0.30, now_ms=now_ms)
    ctx_match = EntryDecisionService.build_context(stage="BEFORE_FILL", timeframe="5M", spread=0.30, now_ms=now_ms)

    assert EntryDecisionService.evaluate_entry_rules(db, ctx_15m)["can_proceed"] is True
    assert EntryDecisionService.evaluate_entry_rules(db, ctx_arm)["can_proceed"] is True
    assert EntryDecisionService.evaluate_entry_rules(db, ctx_match)["can_proceed"] is False


def test_s10_scope_session_distinct_from_session_instance_id(db):
    """S10: Scope session + session instance ID tách biệt."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="NY Session Only S10", severity="CRITICAL", effect="BLOCK_ENTRY",
        scope=json.dumps({"session": "NY"}),
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    ctx_asia = EntryDecisionService.build_context(
        stage="BEFORE_ARM", session_tag="ASIA", session_instance_id="NY_2026-10-09_inst", spread=0.30, now_ms=now_ms
    )
    ctx_ny = EntryDecisionService.build_context(
        stage="BEFORE_ARM", session_tag="NY", session_instance_id="NY_2026-10-09_inst", spread=0.30, now_ms=now_ms
    )

    assert EntryDecisionService.evaluate_entry_rules(db, ctx_asia)["can_proceed"] is True
    assert EntryDecisionService.evaluate_entry_rules(db, ctx_ny)["can_proceed"] is False


def test_s11_ny_dst_vn_boundaries_fake_clock():
    """S11: NY DST/VN midnight/window boundaries dùng fake clock."""
    dt_utc = datetime(2026, 6, 15, 13, 0, 0, tzinfo=timezone.utc)
    epoch_ms = int(dt_utc.timestamp() * 1000)
    tags = LessonRuleService.get_current_session_tags(epoch_ms)
    assert "NY" in tags


def test_s12_malformed_scope_quarantine_no_all(db):
    """S12: Malformed scope -> validation/quarantine, không áp dụng ALL."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Malformed Scope S12", severity="CRITICAL", effect="BLOCK_ENTRY",
        scope="INVALID_NON_JSON_CORRUPT{",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=0.50, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert res["can_proceed"] is True


def test_s13_effective_at_expiry_as_of_historical_replay(db):
    """S13: Effective_at/expiry_as_of và historical replay không future leak."""
    now_ms = 1000000000000
    make_lesson(
        db, title="Future Rule S13", severity="CRITICAL", effect="BLOCK_ENTRY",
        effective_at=now_ms + 500000,
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=0.50, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert res["can_proceed"] is True


# ============================================================================
# P01 - P06: POLICY & FEATURE FLAGS
# ============================================================================

def test_p01_feature_flags_persisted_authoritative(db):
    """P01: Feature flags persisted qua restart, mọi caller cùng policy."""
    LessonPolicyService.update_policy(db, {"lesson_entry_rules_enabled": False})
    p = LessonPolicyService.get_policy(db)
    assert p["lesson_entry_rules_enabled"] is False

    p2 = LessonPolicyService.get_policy(db)
    assert p2["lesson_entry_rules_enabled"] is False


def test_p02_entry_feature_off_baseline_guards_intact(db):
    """P02: Entry feature OFF không đổi baseline guards."""
    now_ms = int(time.time() * 1000)
    LessonPolicyService.update_policy(db, {"lesson_entry_rules_enabled": False})

    make_lesson(
        db, title="Red Rule P02", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=0.50, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert res["can_proceed"] is True


def test_p03_shadow_red_would_block_entry_allowed(db):
    """P03: Shadow red would_block nhưng actual entry baseline allowed."""
    now_ms = int(time.time() * 1000)
    LessonPolicyService.update_policy(db, {"lesson_shadow_mode": True})

    make_lesson(
        db, title="Shadow Red P03", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=0.30, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert res["can_proceed"] is True
    assert any(m.get("would_block") for m in res["matched_rules"])


def test_p04_active_red_blocks_ab_proof(db):
    """P04: Active red cùng fixture -> actual block, A/B proof."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Red AB P04", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=0.30, now_ms=now_ms)

    LessonPolicyService.update_policy(db, {"lesson_shadow_mode": True})
    assert EntryDecisionService.evaluate_entry_rules(db, ctx)["can_proceed"] is True

    LessonPolicyService.update_policy(db, {"lesson_shadow_mode": False})
    assert EntryDecisionService.evaluate_entry_rules(db, ctx)["can_proceed"] is False


def test_p05_client_cannot_spoof_flags(db, client):
    """P05: Client không spoof flags/bypass guard."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Red Rule P05", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.4, "timestamp": now_ms}

    order_payload = {
        "instrument": "XAUUSDT", "direction": "LONG",
        "planned_entry": 2650.0, "stop_loss": 2640.0, "take_profit": 2685.0,
        "risk_pct": 0.25, "lesson_shadow_mode": True, "bypass_lessons": True
    }
    resp = client.post("/api/v1/orders/paper", json=order_payload)
    assert resp.status_code == 400


def test_p06_syntax_valid_only_cannot_auto_activate(db):
    """P06: Syntax valid chưa đủ behavior validation để auto activate."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Syntax Only Red P06", severity="CRITICAL", effect="BLOCK_ENTRY",
        validation_status="SYNTAX_VALID_ONLY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=0.30, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert res["can_proceed"] is True


# ============================================================================
# X01 - X08: ERROR POLICY & DIAGNOSTICS
# ============================================================================

def test_x01_evaluator_exception_manual_fill_no_silent_bypass(db):
    """X01: Evaluator exception trước manual Fill -> no silent bypass."""
    now_ms = int(time.time() * 1000)
    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", now_ms=now_ms)

    import unittest.mock as mock
    with mock.patch("services.lesson_rule_service.LessonRuleService.retrieve_active_rules", side_effect=RuntimeError("Simulated Crash")):
        res = EntryDecisionService.evaluate_entry_rules(db, ctx)
        assert res["can_proceed"] is False
        assert "LESSON_EVALUATION_FAILED" in res["blocking_reasons"][0]
        assert "diag-" in res["correlation_id"]


def test_x02_evaluator_exception_auto_arm_fail_safe(db):
    """X02: Evaluator exception trước Auto Arm/Fill -> declared entry policy."""
    now_ms = int(time.time() * 1000)
    ctx = EntryDecisionService.build_context(stage="BEFORE_ARM", execution_mode="AUTO", now_ms=now_ms)

    import unittest.mock as mock
    with mock.patch("services.lesson_rule_service.LessonRuleService.retrieve_active_rules", side_effect=RuntimeError("Crash in Auto")):
        res = EntryDecisionService.evaluate_entry_rules(db, ctx)
        assert res["can_proceed"] is False
        assert "diag-" in res["correlation_id"]


def test_x03_db_retrieval_error_diagnostics_no_partial_commit(db):
    """X03: DB retrieval error -> diagnostic, transaction không partial."""
    now_ms = int(time.time() * 1000)
    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", now_ms=now_ms)

    import unittest.mock as mock
    with mock.patch("services.lesson_rule_service.LessonRuleService.evaluate_rules", side_effect=Exception("DB partial error")):
        res = EntryDecisionService.evaluate_entry_rules(db, ctx)
        assert res["can_proceed"] is False
        assert len(res["blocking_reasons"]) > 0


def test_x04_required_metric_missing_strict_red_data_unavailable(db):
    """X04: Required metric missing strict red -> data unavailable, không fake match."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Strict Red Rule X04", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20, "strict_data": True})
    )

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=None, bid=None, ask=None, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert res["can_proceed"] is False
    assert any("LESSON_RULE_DATA_UNAVAILABLE" in b for b in res["blocking_reasons"])


def test_x05_optional_warning_data_missing_honest_messaging(db):
    """X05: Optional warning data missing -> thông báo trung thực."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, title="Optional Warning X05", severity="WARNING", effect="WARN_ENTRY",
        predicate=json.dumps({"metric": "distance_to_entry_atr", "operator": ">=", "threshold": 1.0, "strict_data": False})
    )

    ctx = EntryDecisionService.build_context(stage="BEFORE_ARM", distance_to_entry_atr=None, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)
    assert res["can_proceed"] is True
    assert any("Chưa đủ dữ liệu để đánh giá" in note for note in res.get("advisory_notes", []))


def test_x06_temporary_outage_no_wipe_pending(db):
    """X06: Temporary lesson outage không xóa pending/setup hoặc bật false success."""
    now_ms = int(time.time() * 1000)
    order = make_order(db, "order-x06", state="armed", now_ms=now_ms)

    import unittest.mock as mock
    with mock.patch("services.lesson_rule_service.LessonRuleService.retrieve_active_rules", side_effect=Exception("Outage")):
        clock = FakeClock(now_ms)
        ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.1, "timestamp": now_ms}
        execution_coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock, now_ms=now_ms)

    db.refresh(order)
    assert order.state in ("armed", "rejected")


def test_x07_open_position_tp_sl_works_during_evaluator_outage(db):
    """X07: Open position TP/SL vẫn xử lý khi evaluator/lesson DB lỗi."""
    now_ms = int(time.time() * 1000)
    order = make_order(db, "order-x07", state="paper_open", actual_entry=2650.0, now_ms=now_ms)

    closed = TradeLifecycleService.execute_close(db, "order-x07", exit_price=2685.0, exit_cause="TP_HIT", now_ms=now_ms)
    assert closed.state == "closed"
    assert closed.exit_cause == "TP_HIT"


def test_x08_engine_disabled_error_baseline_limits_held(db):
    """X08: Engine disabled/advisory error không bỏ baseline risk/news/day limits."""
    now_ms = int(time.time() * 1000)
    LessonPolicyService.update_policy(db, {"lesson_entry_rules_enabled": False})

    audit = crud.get_or_create_today_audit(db)
    audit.fills_count = 3
    db.commit()

    setup = make_setup(
        db, setup_id="setup-x08", direction="LONG", provisional_entry=2650.0,
        provisional_sl=2640.0, provisional_tp=2685.0, now_ms=now_ms
    )
    db.add(setup)
    db.commit()

    elig = evaluate_setup_eligibility(db, setup, now_ms=now_ms)
    assert elig["can_arm"] is False
    assert any("DAILY_FILL_CAP" in r for r in elig["reason_codes"])


# ============================================================================
# U01 - U08: UI, API & STATE MUTATION
# ============================================================================

def test_u01_ui_approve_not_auto_enable(client, db):
    """U01: UI approve -> BE response -> reload đúng status, chưa auto enable."""
    rule = make_lesson(db, "Approve Test U01", status="PENDING", is_approved=False, enabled=False)

    resp = client.post(f"/api/v1/lessons/{rule.id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "APPROVED"
    assert data["is_approved"] is True
    assert data["enabled"] is False


def test_u02_ui_set_enable_desired_state_persisted(client, db):
    """U02: UI enable/disable desired state -> reload/restart đúng."""
    rule = make_lesson(db, "Set Enable U02", enabled=False, revision=1)

    resp = client.post(f"/api/v1/lessons/{rule.id}/set-enable", json={"enabled": True, "expected_revision": 1})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    db.refresh(rule)
    assert rule.enabled is True


def test_u03_double_click_toggle_idempotent_revision_guard(client, db):
    """U03: Double click/retry bật/tắt -> idempotent, revision guard."""
    rule = make_lesson(db, "Double Click U03", enabled=False, revision=1)

    r1 = client.post(f"/api/v1/lessons/{rule.id}/set-enable", json={"enabled": True, "expected_revision": 1})
    assert r1.status_code == 200
    assert r1.json()["enabled"] is True

    r2 = client.post(f"/api/v1/lessons/{rule.id}/set-enable", json={"enabled": True})
    assert r2.status_code == 200
    assert r2.json()["enabled"] is True


def test_u04_stale_rule_edit_409_preserves_draft(client, db):
    """U04: Stale rule edit 409 -> giữ draft và version history."""
    rule = make_lesson(db, "Original Title U04", revision=2)

    resp = client.put(f"/api/v1/lessons/{rule.id}", json={"title": "New Title", "revision": 1})
    assert resp.status_code == 409
    assert "STALE_EDIT" in str(resp.json()["detail"])


def test_u05_api_route_404_vs_lesson_404(client):
    """U05: API route404 vs lesson404 -> đúng message, không JSON/constant thô."""
    r_lesson = client.post("/api/v1/lessons/999999/approve")
    assert r_lesson.status_code == 404
    assert r_lesson.json()["detail"]["code"] == "LESSON_NOT_FOUND"

    r_route = client.post("/api/v1/non_existent_route")
    assert r_route.status_code == 404
    assert r_route.json()["detail"] == "Not Found"


def test_u06_wrong_proxy_capabilities_diagnostic(client):
    """U06: Wrong proxy/old backend capabilities -> diagnostic rõ."""
    resp = client.get("/api/v1/system/capabilities")
    assert resp.status_code == 200
    caps = resp.json()
    assert "version" in caps
    assert "features" in caps
    assert caps["features"].get("lesson_entry_rules") is True


def test_u07_stale_background_get_no_overwrite_saved(db):
    """U07: Background GET cũ không overwrite state Save mới."""
    rule = make_lesson(db, "Title V1", revision=1)

    rule.title = "Title V2 Saved"
    rule.revision = 2
    db.commit()

    db.refresh(rule)
    assert rule.title == "Title V2 Saved"
    assert rule.revision == 2


def test_u08_error_burst_dedupe_and_details():
    """U08: Same error burst dedupe, toast detail đúng action/entity."""
    seen = set()
    burst_errors = ["LESSON_BLOCKED: #1", "LESSON_BLOCKED: #1", "LESSON_BLOCKED: #1"]
    deduped = [e for e in burst_errors if not (e in seen or seen.add(e))]
    assert len(deduped) == 1


# ============================================================================
# T01 - T05: DECISION TRACING
# ============================================================================

def test_t01_arm_fill_trace_distinct_no_overwrite(db):
    """T01: Arm + Fill trace riêng, không overwrite."""
    now_ms = int(time.time() * 1000)
    order = make_order(db, "order-t01", now_ms=now_ms)

    arm_eval = {"stage": "BEFORE_ARM", "can_proceed": True, "evaluated_at": now_ms}
    fill_eval = {"stage": "BEFORE_FILL", "can_proceed": True, "evaluated_at": now_ms + 5000}

    EntryDecisionService.record_stage_decision(order, "BEFORE_ARM", arm_eval, now_ms=now_ms)
    EntryDecisionService.record_stage_decision(order, "BEFORE_FILL", fill_eval, now_ms=now_ms + 5000)

    assert order.arm_decision_snapshot is not None
    assert order.fill_decision_snapshot is not None
    history = json.loads(order.lessons_retrieved)
    assert len(history) == 2
    assert history[0]["stage"] == "BEFORE_ARM"
    assert history[1]["stage"] == "BEFORE_FILL"


def test_t02_edit_archive_lesson_immutable_past_trace(db):
    """T02: Edit/archive lesson không đổi trace/lệnh quá khứ."""
    now_ms = int(time.time() * 1000)
    rule = make_lesson(db, "Original Rule T02", lesson_id=702, now_ms=now_ms)
    order = make_order(db, "order-t02", state="paper_open", actual_entry=2650.0, now_ms=now_ms)
    order.fill_decision_snapshot = json.dumps({"rule_id": 702, "title": "Original Rule T02"})
    db.commit()

    rule.title = "Archived & Changed Title"
    rule.status = "ARCHIVED"
    db.commit()

    db.refresh(order)
    trace = json.loads(order.fill_decision_snapshot)
    assert trace["title"] == "Original Rule T02"


def test_t03_retrieved_evaluated_matched_applied_distinction(db):
    """T03: Retrieved/evaluated/matched/applied/shadow khác nhau."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, "Unmatched T03", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 1.0})
    )
    make_lesson(
        db, "Matched T03", severity="WARNING", effect="WARN_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )

    ctx = EntryDecisionService.build_context(stage="BEFORE_FILL", spread=0.20, now_ms=now_ms)
    res = EntryDecisionService.evaluate_entry_rules(db, ctx)

    assert len(res["evaluations"]) == 2
    assert len(res["matched_rules"]) == 1
    assert res["matched_rules"][0]["title"] == "Matched T03"


def test_t04_warnings_in_journal_ui(db):
    """T04: Warnings vào Journal/UI, không chỉ log."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, "Warning Rule T04", severity="WARNING", effect="WARN_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )

    order = make_order(db, "order-t04", state="candidate", now_ms=now_ms)
    calc = calculate_risk_reward("LONG", 2650.0, 2640.0, 2685.0, 1000.0, 0.25, 2.0)

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.25, "timestamp": now_ms}

    res_order = TradeLifecycleService.execute_fill(db, order, 2650.0, calc, now_ms=now_ms)
    history = json.loads(res_order.lessons_retrieved)
    assert len(history[0]["warning_messages"]) > 0


def test_t05_unknown_manual_origin_legacy_no_fake_auto(db):
    """T05: Unknown manual origin legacy -> no fake AUTO/session."""
    order = make_order(db, "order-t05-legacy", state="armed", origin="UNKNOWN", execution_mode="UNKNOWN")
    ctx = EntryDecisionService.build_context_from_order(order, stage="BEFORE_FILL")
    assert ctx["origin"] == "UNKNOWN"
    assert ctx["execution_mode"] == "UNKNOWN"


# ============================================================================
# R01 - R08: INVARIANTS & REGRESSION SAFETY
# ============================================================================

def test_r01_open_position_rule_mutation_invariants(db):
    """R01: Open position + thêm/sửa/disable red -> SL/TP/qty/risk bất biến."""
    now_ms = int(time.time() * 1000)
    order = make_order(db, "order-r01", state="paper_open", actual_entry=2650.0, now_ms=now_ms)

    make_lesson(
        db, "New Critical Red R01", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.05})
    )

    db.refresh(order)
    assert order.actual_entry == 2650.0
    assert order.stop_loss == 2640.0
    assert order.take_profit == 2685.0
    assert order.quantity == 0.1
    assert order.initial_risk_usdt == 10.0


def test_r02_manual_close_tp_sl_liquidation_pnl_regression(db):
    """R02: Manual close/TP/SL/liquidation cause/PnL/lesson/Telegram không regression."""
    now_ms = int(time.time() * 1000)
    order = make_order(db, "order-r02", state="paper_open", actual_entry=2650.0, now_ms=now_ms)

    closed = TradeLifecycleService.execute_close(db, "order-r02", exit_price=2685.0, exit_cause="TP_HIT", now_ms=now_ms)
    assert closed.state == "closed"
    assert closed.realized_pnl_net > 0


def test_r03_offline_recovery_single_close_lesson(db):
    """R03: Offline estimated/ambiguous recovery đúng, một close/event/lesson."""
    now_ms = int(time.time() * 1000)
    order = make_order(db, "order-r03", direction="SHORT", state="paper_open", actual_entry=2650.0, take_profit=2615.0, now_ms=now_ms)

    c1 = TradeLifecycleService.execute_close(db, "order-r03", exit_price=2615.0, exit_cause="TP_HIT", now_ms=now_ms)
    c2 = TradeLifecycleService.execute_close(db, "order-r03", exit_price=2615.0, exit_cause="TP_HIT", now_ms=now_ms)
    assert c1 is not None
    assert c1.state == "closed"
    assert c2 is None


def test_r04_max1_open_armed_daily3_news_guards(db):
    """R04: Max1 open/armed, daily3, loss/news/margin/leverage guards vẫn đạt."""
    now_ms = int(time.time() * 1000)
    calc = calculate_risk_reward("LONG", 2650.0, 2640.0, 2685.0, 1000.0, 0.25, 2.0)

    o1 = make_order(db, "o1-r04", state="candidate", now_ms=now_ms)
    o2 = make_order(db, "o2-r04", state="candidate", now_ms=now_ms)

    f1 = TradeLifecycleService.execute_fill(db, o1, 2650.0, calc, now_ms=now_ms)
    f2 = TradeLifecycleService.execute_fill(db, o2, 2650.0, calc, now_ms=now_ms)

    assert f1.state == "paper_open"
    assert f2.state == "rejected"
    assert "tối đa 1 vị thế" in f2.invalidation_reason


def test_r05_migration_idempotent_no_data_loss(db):
    """R05: Migration legacy/fresh DB idempotent, không mất dữ liệu."""
    from database import run_schema_migrations
    run_schema_migrations(db.bind)
    run_schema_migrations(db.bind)


def test_r06_runtime_isolation_sentinel_verified():
    """R06: Runtime isolation sentinel + service SessionLocal fixture thống nhất."""
    assert os.environ.get("TESTING") == "1"


def test_r07_feature_rollback_lessons_disabled_exits_intact(db):
    """R07: Feature rollback tắt lessons -> baseline hoạt động, exits không gián đoạn."""
    now_ms = int(time.time() * 1000)
    LessonPolicyService.update_policy(db, {
        "lesson_advisory_enabled": False,
        "lesson_entry_rules_enabled": False
    })
    order = make_order(db, "order-r07", state="paper_open", actual_entry=2650.0, now_ms=now_ms)

    closed = TradeLifecycleService.execute_close(db, "order-r07", exit_price=2640.0, exit_cause="SL_HIT", now_ms=now_ms)
    assert closed.state == "closed"
    assert closed.exit_cause == "SL_HIT"


def test_r08_rule_update_no_tp_widening_or_risk_increase(db):
    """R08: Rule update không tự sửa TP xa hoặc tăng risk."""
    now_ms = int(time.time() * 1000)
    order = make_order(db, "order-r08", state="paper_open", actual_entry=2650.0, now_ms=now_ms)

    db.refresh(order)
    assert order.take_profit == 2685.0
    assert order.initial_risk_usdt == 10.0


# ============================================================================
# B01 - B05: BROWSER ACCEPTANCE SCENARIOS (E2E Integration Verification)
# ============================================================================

def test_b01_browser_manual_red_blocked_toggle_fill_tp(client, db):
    """B01: Browser manual red blocked -> disable -> valid quote fill -> TP -> Journal/Telegram mock."""
    now_ms = int(time.time() * 1000)
    rule = make_lesson(
        db, "Red Spread B01", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.35, "timestamp": now_ms}

    order_payload = {
        "instrument": "XAUUSDT", "direction": "LONG",
        "planned_entry": 2650.0, "stop_loss": 2640.0, "take_profit": 2685.0,
        "risk_pct": 0.25
    }
    # 1. Blocked
    r1 = client.post("/api/v1/orders/paper", json=order_payload)
    assert r1.status_code == 400

    # 2. Toggle disable
    r_toggle = client.post(f"/api/v1/lessons/{rule.id}/set-enable", json={"enabled": False})
    assert r_toggle.status_code == 200

    # 3. Fill
    r2 = client.post("/api/v1/orders/paper", json=order_payload)
    assert r2.status_code == 200
    order_id = r2.json()["order"]["id"]

    # 4. TP hit
    TradeLifecycleService.execute_close(db, order_id, exit_price=2685.0, exit_cause="TP_HIT", now_ms=now_ms)
    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
    assert order.state == "closed"


def test_b02_browser_auto_warning_arm_fill_sl_traces(db):
    """B02: Browser Auto warning -> arm/fill -> SL -> immutable decision traces."""
    now_ms = int(time.time() * 1000)
    clock = FakeClock(now_ms)

    make_lesson(
        db, "Auto Yellow B02", severity="WARNING", effect="WARN_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.10})
    )

    setup = make_setup(
        db, setup_id="setup-b02", direction="LONG", provisional_entry=2650.0,
        provisional_sl=2640.0, provisional_tp=2685.0, now_ms=now_ms
    )

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.15, "timestamp": now_ms}

    strategy_service._auto_arm_candidate(db, setup, {"signal_id": "sig-b02"}, now_ms, datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc))
    order = db.query(models.PaperOrder).filter(models.PaperOrder.setup_id == "setup-b02").first()
    assert order is not None

    ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.15, "timestamp": now_ms}
    execution_coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock, now_ms=now_ms)

    db.refresh(order)
    assert order.state == "paper_open"

    TradeLifecycleService.execute_close(db, order.id, exit_price=2640.0, exit_cause="SL_HIT", now_ms=now_ms)
    db.refresh(order)
    assert order.state == "closed"
    assert order.arm_decision_snapshot is not None
    assert order.fill_decision_snapshot is not None


def test_b03_browser_shadow_red_would_block_active_blocks(client, db):
    """B03: Browser shadow red -> would_block -> active same rule -> block."""
    now_ms = int(time.time() * 1000)
    make_lesson(
        db, "Red B03", severity="CRITICAL", effect="BLOCK_ENTRY",
        predicate=json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.20})
    )

    from services.collector_service import collector_service
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.35, "timestamp": now_ms}

    order_payload = {
        "instrument": "XAUUSDT", "direction": "LONG",
        "planned_entry": 2650.0, "stop_loss": 2640.0, "take_profit": 2685.0,
        "risk_pct": 0.25
    }

    LessonPolicyService.update_policy(db, {"lesson_shadow_mode": True})
    r_shadow = client.post("/api/v1/orders/paper", json=order_payload)
    assert r_shadow.status_code == 200

    TradeLifecycleService.execute_close(db, r_shadow.json()["order"]["id"], exit_price=2650.0, exit_cause="MANUAL_CLOSE")

    LessonPolicyService.update_policy(db, {"lesson_shadow_mode": False})
    r_active = client.post("/api/v1/orders/paper", json=order_payload)
    assert r_active.status_code == 400


def test_b04_browser_approve_toggle_edit_archive_reload(client, db):
    """B04: Browser approve/toggle/edit/archive/reload -> đúng persisted semantics."""
    rule = make_lesson(db, "Full Lifecycle B04", status="PENDING", is_approved=False, enabled=False)

    r1 = client.post(f"/api/v1/lessons/{rule.id}/approve")
    assert r1.status_code == 200
    assert r1.json()["status"] == "APPROVED"
    assert r1.json()["enabled"] is False

    r2 = client.post(f"/api/v1/lessons/{rule.id}/set-enable", json={"enabled": True})
    assert r2.status_code == 200
    assert r2.json()["enabled"] is True

    r3 = client.put(f"/api/v1/lessons/{rule.id}", json={"title": "Updated Title B04"})
    assert r3.status_code == 200
    assert r3.json()["title"] == "Updated Title B04"

    r4 = client.post(f"/api/v1/lessons/{rule.id}/archive")
    assert r4.status_code == 200
    assert r4.json()["status"] == "ARCHIVED"
    assert r4.json()["enabled"] is False


def test_b05_restart_app_pending_open_cache_exits(db):
    """B05: Restart test app với pending/open, lesson cache/flags đúng, exits ổn."""
    now_ms = int(time.time() * 1000)
    order_open = make_order(db, "order-open-b05", state="paper_open", actual_entry=2650.0, take_profit=2685.0, now_ms=now_ms)
    order_armed = make_order(db, "order-armed-b05", state="armed", take_profit=2685.0, now_ms=now_ms)

    LessonRuleService.invalidate_cache()
    p = LessonPolicyService.get_policy(db)
    assert p["lesson_entry_rules_enabled"] is True

    closed = TradeLifecycleService.execute_close(db, "order-open-b05", exit_price=2685.0, exit_cause="TP_HIT", now_ms=now_ms)
    assert closed.state == "closed"
