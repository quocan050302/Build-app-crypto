"""
V14.2 - unified ENTRY guards (news / lessons / executable plans) for LONG and SHORT.

Every negative case has a positive control that really opens/arms an order, so a fix that blocks
everything cannot pass.  Assertions are made on persisted state (orders, counters, events,
outbox), not on helper booleans.  No network, no Telegram, no runtime DB.
"""
import json
import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import crud
import models
import schemas
from database import get_db, run_schema_migrations
from domain_calculator import calculate_risk_reward
from main import app
from paper_broker import PaperBroker
from services.clock import FakeClock
from services.entry_guard_service import EntryCheck, aggregate_entry_checks
from services.execution_coordinator import execution_coordinator
from services.lesson_policy_service import LessonPolicyService
from services.lesson_rule_service import LessonRuleService
from services.strategy_service import strategy_service
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService

DIRS = ["LONG", "SHORT"]

# Plans with net R:R comfortably >= 2.0 (same shapes already used by the V10 suites).
PLAN = {
    "LONG": dict(entry=2650.0, sl=2640.0, tp=2685.0),
    "SHORT": dict(entry=2650.0, sl=2660.0, tp=2615.0),
}
# Quotes that touch a LIMIT at the planned entry and are fresh.
TOUCH = {"LONG": (2649.8, 2650.0), "SHORT": (2650.0, 2650.2)}


# --------------------------------------------------------------------------- helpers

@pytest.fixture
def db(isolated_db):
    LessonPolicyService.reset_to_defaults(isolated_db)
    LessonRuleService.invalidate_cache()
    return isolated_db


def _now_ms() -> int:
    return int(time.time() * 1000)


def make_news(db, now_ms, *, title="Core CPI m/m", currency="USD", impact="High", offset_min=0, uid=None):
    """High-impact event scheduled `offset_min` minutes from now (default: right now -> in blackout)."""
    row = models.EconomicNews(
        source_id=uid or f"v142-{title}-{currency}-{offset_min}-{now_ms}",
        title=title, country=currency, currency=currency, impact=impact,
        scheduled_at=now_ms + offset_min * 60_000, received_at=now_ms - 86_400_000,
    )
    db.add(row)
    db.commit()
    return row


def make_lesson(db, title, *, severity="CRITICAL", effect="BLOCK_ENTRY", predicate=None, scope=None,
                status="APPROVED", is_approved=True, enabled=True, validation_status="VALID", stage="ALL"):
    rule = models.Lesson(
        created_at=_now_ms(), title=title, category="PROCESS", reflection="r", action_rule="a",
        status=status, is_approved=is_approved, enabled=enabled, severity=severity, effect=effect,
        validation_status=validation_status, predicate=predicate, scope=scope, stage=stage, revision=1,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    LessonRuleService.invalidate_cache()
    return rule


RED_ALWAYS = json.dumps({"metric": "spread", "operator": ">=", "threshold": 0.0})


def make_armed(db, order_id, direction, now_ms, *, order_type="LIMIT", mode="MANUAL", origin="MANUAL_WEB",
               family="STANDARD_SMC", qty=0.1, risk=10.0):
    p = PLAN[direction]
    order = models.PaperOrder(
        id=order_id, direction=direction, state="armed", order_type=order_type,
        planned_entry=p["entry"], stop_loss=p["sl"], take_profit=p["tp"], quantity=qty,
        initial_risk_usdt=risk, risk_pct=0.25, strategy_family=family, origin=origin,
        execution_mode=mode, created_at=now_ms, armed_at=now_ms, expires_at=now_ms + 3_600_000,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def tick(direction, now_ms):
    bid, ask = TOUCH[direction]
    return {"symbol": "XAUUSDT", "bid": bid, "ask": ask, "timestamp": now_ms, "observed_at": now_ms}


def run_tick(db, direction, now_ms):
    clock = FakeClock(now_ms)
    execution_coordinator.evaluate_orders_sync(db, ticker_override=tick(direction, now_ms), clock=clock, now_ms=now_ms)


def snapshot(db):
    """Counters that must not move on a blocked / read-only action."""
    audit = crud.get_or_create_today_audit(db)
    return {
        "orders": db.query(models.PaperOrder).count(),
        "open": db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").count(),
        "armed": db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").count(),
        "events": db.query(models.DomainEvent).count(),
        "outbox": db.query(models.NotificationOutbox).count(),
        "fills": audit.fills_count or 0,
    }


def events_of(db, event_type, aggregate_id=None):
    q = db.query(models.DomainEvent).filter(models.DomainEvent.event_type == event_type)
    if aggregate_id:
        q = q.filter(models.DomainEvent.aggregate_id == aggregate_id)
    return q.all()


def market_payload(direction, **over):
    p = PLAN[direction]
    body = {
        "instrument": "XAUUSDT", "direction": direction, "planned_entry": p["entry"],
        "stop_loss": p["sl"], "take_profit": p["tp"], "risk_pct": 0.25, "leverage": 5,
        "margin_mode": "ISOLATED",
    }
    body.update(over)
    return body


def make_setup(db, setup_id, direction, state="READY", *, entry=None, sl=None, tp=None, **extra):
    p = PLAN[direction]
    now = _now_ms()
    e = entry if entry is not None else p["entry"]
    s = sl if sl is not None else p["sl"]
    t = tp if tp is not None else p["tp"]
    setup = models.WatchSetup(
        id=setup_id, timeframe="15M", direction=direction, state=state,
        provisional_entry=e, provisional_sl=s, provisional_tp=t,
        confirmed_entry=e, confirmed_sl=s, confirmed_tp=t,
        invalidation_price=s, leverage=5, margin_mode="ISOLATED",
        risk_pct=0.25, version=1, created_at=now, updated_at=now, **extra,
    )
    db.add(setup)
    db.commit()
    db.refresh(setup)
    return setup


def arm(client, setup, direction, order_type="LIMIT"):
    return client.post("/api/v1/setups/arm", json={"setup_id": setup.id, "expected_direction": direction,
                                                   "order_type": order_type})


# --------------------------------------------------------------------------- aggregate contract

def test_aggregate_missing_check_fails_closed():
    d = aggregate_entry_checks("ARM", 1, {})
    assert d.allowed is False
    assert set(d.reason_codes) == {"ENTRY_GUARD_DATA_UNAVAILABLE"}
    assert len(d.blockers) > 0 and all(b["message_vi"] and b["next_step_vi"] for b in d.blockers)


def test_aggregate_all_pass_is_allowed_and_blockers_empty():
    from services.entry_guard_service import REQUIRED
    checks = {n: EntryCheck(n, "PASS", "ok", "") for n in REQUIRED["FILL"]}
    d = aggregate_entry_checks("FILL", 5, checks)
    assert d.allowed is True and d.blockers == [] and d.to_dict()["allowed"] is True


def test_aggregate_unknown_check_blocks_even_if_rest_pass():
    from services.entry_guard_service import REQUIRED
    checks = {n: EntryCheck(n, "PASS", "ok", "") for n in REQUIRED["ARM"]}
    checks["news"] = EntryCheck("ENTRY_GUARD_DATA_UNAVAILABLE", "UNKNOWN", "m", "n")
    d = aggregate_entry_checks("ARM", 5, checks)
    assert d.allowed is False and d.reason_codes == ["ENTRY_GUARD_DATA_UNAVAILABLE"]


# --------------------------------------------------------------------------- SHORT photo fixture

def test_short_photo_fixture_is_valid_geometry_but_economically_non_viable():
    r = calculate_risk_reward(
        "SHORT", entry=4198.29, sl=4203.29, tp=4198.00, capital=999.57, quantity_override=0.29, leverage=30,
    )
    assert r.is_valid is True                   # TP < entry < SL: geometry is right
    assert r.can_execute is False               # but the plan is not executable
    assert r.gross_rr == pytest.approx(0.058, abs=0.001)
    assert r.net_reward_usdt < 0 and r.net_rr < 0
    assert r.net_rr == pytest.approx(-0.37, abs=0.01)
    assert r.net_reward_usdt == pytest.approx(-0.92, abs=0.02)


@pytest.mark.parametrize("direction", DIRS)
def test_tight_target_plan_is_blocked_by_arm_and_target_is_kept(db, client, fresh_ticker, direction):
    fresh_ticker()
    if direction == "SHORT":
        setup = make_setup(db, "tight-s", direction, entry=4198.29, sl=4203.29, tp=4198.00)
    else:  # mirrored LONG: same tiny reward
        setup = make_setup(db, "tight-l", direction, entry=4198.29, sl=4193.29, tp=4198.58)
    before = snapshot(db)
    resp = arm(client, setup, direction)
    assert resp.status_code >= 400
    text = json.dumps(resp.json(), ensure_ascii=False)
    assert "NET_REWARD_NON_POSITIVE" in text or "INSUFFICIENT_RR" in text
    assert snapshot(db) == before and before["orders"] == 0
    db.refresh(setup)
    # The structural target is never stretched to make the ratio look good.
    expected_tp = 4198.00 if direction == "SHORT" else 4198.58
    assert setup.provisional_tp == expected_tp and setup.confirmed_tp == expected_tp


@pytest.mark.parametrize("direction", DIRS)
def test_manual_arm_positive_control_creates_frozen_plan(db, client, fresh_ticker, direction):
    fresh_ticker()
    setup = make_setup(db, f"ok-{direction}", direction)
    resp = arm(client, setup, direction)
    assert resp.status_code == 200, resp.text
    order = db.query(models.PaperOrder).filter(models.PaperOrder.setup_id == setup.id).one()
    p = PLAN[direction]
    assert (order.state, order.direction) == ("armed", direction)
    assert (order.planned_entry, order.stop_loss, order.take_profit) == (p["entry"], p["sl"], p["tp"])
    assert order.execution_mode == "MANUAL" and order.origin == "MANUAL_WEB"


# --------------------------------------------------------------------------- manual ARM gates

@pytest.mark.parametrize("direction", DIRS)
def test_manual_arm_blocked_by_news_blackout(db, client, fresh_ticker, direction):
    fresh_ticker()
    setup = make_setup(db, f"news-{direction}", direction)
    make_news(db, _now_ms())
    before = snapshot(db)
    resp = arm(client, setup, direction)
    assert resp.status_code >= 400
    assert "NEWS_BLACKOUT" in json.dumps(resp.json(), ensure_ascii=False)
    assert snapshot(db) == before and before["orders"] == 0


def test_manual_arm_news_outside_window_does_not_block(db, client, fresh_ticker):
    fresh_ticker()
    setup = make_setup(db, "news-far", "LONG")
    make_news(db, _now_ms(), offset_min=240)          # upcoming, far outside the window
    make_news(db, _now_ms(), currency="EUR")           # red but not USD
    resp = arm(client, setup, "LONG")
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("direction", DIRS)
def test_manual_arm_blocked_by_active_red_lesson(db, client, fresh_ticker, direction):
    fresh_ticker()
    setup = make_setup(db, f"les-{direction}", direction)
    make_lesson(db, "Red always", predicate=RED_ALWAYS)
    resp = arm(client, setup, direction)
    assert resp.status_code >= 400
    assert "LESSON_RULE_BLOCKED" in json.dumps(resp.json(), ensure_ascii=False)
    assert db.query(models.PaperOrder).count() == 0


@pytest.mark.parametrize("direction", DIRS)
def test_manual_arm_blocked_on_stale_quote(db, client, fresh_ticker, direction):
    fresh_ticker(now_ms=_now_ms() - 120_000)
    setup = make_setup(db, f"stale-{direction}", direction)
    resp = arm(client, setup, direction)
    assert resp.status_code >= 400
    assert "QUOTE" in json.dumps(resp.json(), ensure_ascii=False)
    assert db.query(models.PaperOrder).count() == 0


@pytest.mark.parametrize("state", ["WAITING_MSS", "WAITING_PRICE"])
def test_waiting_states_never_arm_even_when_price_is_at_entry(db, client, fresh_ticker, state):
    fresh_ticker(bid=2649.9, ask=2650.0)  # distance ~ 0 to entry
    setup = make_setup(db, f"wait-{state}", "LONG", state=state)
    resp = arm(client, setup, "LONG")
    assert resp.status_code >= 400
    assert db.query(models.PaperOrder).count() == 0


def test_waiting_retrace_without_structure_chain_is_blocked(db, client, fresh_ticker):
    fresh_ticker()
    setup = make_setup(db, "retrace-nochain", "LONG", state="WAITING_RETRACE")
    resp = arm(client, setup, "LONG", order_type="MARKET")
    assert resp.status_code >= 400
    assert "EVIDENCE_UNAVAILABLE" in json.dumps(resp.json(), ensure_ascii=False)
    assert db.query(models.PaperOrder).count() == 0


def test_eligibility_read_model_is_additive_and_bound(db, client, fresh_ticker):
    fresh_ticker()
    setup = make_setup(db, "elig-1", "SHORT")
    data = client.get(f"/api/v1/setups/eligibility/{setup.id}").json()
    for legacy in ("eligible", "can_arm", "can_execute_now", "reason_codes", "block_reasons"):
        assert legacy in data
    assert data["plan_kind"] in ("CONFIRMED", "PROVISIONAL")
    assert isinstance(data["blockers"], list) and "binding" in data and "news_view" in data
    assert data["allowed"] == (len(data["blockers"]) == 0)


# --------------------------------------------------------------------------- manual MARKET + broker

@pytest.mark.parametrize("direction", DIRS)
def test_market_positive_control_opens_position(db, client, fresh_ticker, direction):
    fresh_ticker(bid=2650.0, ask=2650.2)
    resp = client.post("/api/v1/orders/paper", json=market_payload(direction))
    assert resp.status_code == 200, resp.text
    order = db.query(models.PaperOrder).one()
    assert order.state == "paper_open" and order.direction == direction
    assert snapshot(db)["fills"] == 1


@pytest.mark.parametrize("direction", DIRS)
def test_market_blocked_by_news_blackout_leaves_no_order(db, client, fresh_ticker, direction):
    fresh_ticker(bid=2650.0, ask=2650.2)
    make_news(db, _now_ms())
    before = snapshot(db)
    resp = client.post("/api/v1/orders/paper", json=market_payload(direction))
    assert resp.status_code >= 400
    assert "NEWS_BLACKOUT" in json.dumps(resp.json(), ensure_ascii=False)
    assert snapshot(db) == before
    assert db.query(models.PaperOrder).count() == 0           # no orphan candidate


@pytest.mark.parametrize("direction", DIRS)
def test_direct_broker_call_is_also_guarded(db, direction, fresh_ticker):
    """The guard lives in PaperBroker, so callers that skip HTTP are protected too."""
    fresh_ticker()
    make_news(db, _now_ms())
    order_create = schemas.PaperOrderCreate(**market_payload(direction))
    with pytest.raises(ValueError, match="NEWS_BLACKOUT"):
        PaperBroker.execute_market_order(db, order_create, 2650.0, 2650.2)
    assert db.query(models.PaperOrder).count() == 0


@pytest.mark.parametrize("direction", DIRS)
def test_direct_broker_call_positive_control(db, direction):
    order_create = schemas.PaperOrderCreate(**market_payload(direction))
    order = PaperBroker.execute_market_order(db, order_create, 2650.0, 2650.2)
    assert order.state == "paper_open"


@pytest.mark.parametrize("direction", DIRS)
def test_market_blocked_by_red_lesson(db, client, fresh_ticker, direction):
    fresh_ticker(bid=2650.0, ask=2650.2)
    make_lesson(db, "Red always", predicate=RED_ALWAYS)
    resp = client.post("/api/v1/orders/paper", json=market_payload(direction))
    assert resp.status_code >= 400
    assert db.query(models.PaperOrder).count() == 0


@pytest.mark.parametrize("bad", ["stale", "inverted", "nan"])
def test_market_rejects_invalid_quotes(db, client, fresh_ticker, bad):
    now = _now_ms()
    if bad == "stale":
        fresh_ticker(bid=2650.0, ask=2650.2, now_ms=now - 120_000)
    elif bad == "inverted":
        fresh_ticker(bid=2650.5, ask=2650.0)
    else:
        fresh_ticker(bid=float("nan"), ask=2650.2)
    resp = client.post("/api/v1/orders/paper", json=market_payload("LONG"))
    assert resp.status_code >= 400
    assert db.query(models.PaperOrder).count() == 0


def test_market_manual_draft_is_not_labelled_as_smc(db, client, fresh_ticker):
    fresh_ticker(bid=2650.0, ask=2650.2)
    resp = client.post("/api/v1/orders/paper", json=market_payload("LONG"))
    assert resp.status_code == 200, resp.text
    order = db.query(models.PaperOrder).one()
    assert order.origin == "MANUAL_WEB" and order.execution_mode == "MANUAL"
    trace = json.loads(order.fill_decision_snapshot)
    assert trace.get("entry_gate", {}).get("plan_kind") == "DRAFT"


def test_market_insufficient_net_rr_is_blocked_and_levels_untouched(db, client, fresh_ticker):
    fresh_ticker(bid=4198.29, ask=4198.29)
    payload = market_payload("SHORT", planned_entry=4198.29, stop_loss=4203.29, take_profit=4198.00)
    resp = client.post("/api/v1/orders/paper", json=payload)
    assert resp.status_code >= 400
    assert db.query(models.PaperOrder).count() == 0


def test_market_idempotent_retry_creates_one_order(db, client, fresh_ticker):
    fresh_ticker(bid=2650.0, ask=2650.2)
    payload = market_payload("LONG", idempotency_key="idem-v142-1")
    r1 = client.post("/api/v1/orders/paper", json=payload)
    r2 = client.post("/api/v1/orders/paper", json=payload)
    assert r1.status_code == 200 and r2.status_code in (200, 409)
    assert db.query(models.PaperOrder).count() == 1


# --------------------------------------------------------------------------- pending fill re-check

@pytest.mark.parametrize("order_type", ["MARKET", "LIMIT", "STOP"])
@pytest.mark.parametrize("direction", DIRS)
def test_pending_fill_positive_control(db, direction, order_type):
    now = _now_ms()
    p = PLAN[direction]
    order = make_armed(db, f"pc-{direction}-{order_type}", direction, now, order_type=order_type)
    if order_type == "STOP":     # stop triggers when price runs through entry
        order.planned_entry = p["entry"] - 0.4 if direction == "LONG" else p["entry"] + 0.4
        db.commit()
    run_tick(db, direction, now)
    db.refresh(order)
    assert order.state == "paper_open", order.invalidation_reason
    assert snapshot(db)["fills"] == 1


@pytest.mark.parametrize("direction", DIRS)
def test_news_after_arm_rejects_once_before_fill(db, direction):
    now = _now_ms()
    order = make_armed(db, f"n-{direction}", direction, now)
    make_news(db, now)                                 # imported AFTER arming
    before = snapshot(db)
    run_tick(db, direction, now)
    db.refresh(order)
    assert order.state == "rejected" and "NEWS_BLACKOUT" in (order.invalidation_reason or "")
    after = snapshot(db)
    assert after["open"] == 0 and after["fills"] == before["fills"]
    assert events_of(db, "trade.opened") == []
    assert len(events_of(db, "order.rejected", order.id)) == 1
    outbox_added = db.query(models.NotificationOutbox).count() - before["outbox"]
    # repeated ticks never create another rejection or notification
    run_tick(db, direction, now + 6000)
    run_tick(db, direction, now + 12000)
    assert len(events_of(db, "order.rejected", order.id)) == 1
    assert db.query(models.NotificationOutbox).count() - before["outbox"] == outbox_added
    assert snapshot(db)["fills"] == before["fills"]
    db.refresh(order)
    assert order.state == "rejected"


def test_pending_fill_with_news_outside_window_still_fills(db):
    now = _now_ms()
    order = make_armed(db, "n-far", "LONG", now)
    make_news(db, now, offset_min=300)
    run_tick(db, "LONG", now)
    db.refresh(order)
    assert order.state == "paper_open"


@pytest.mark.parametrize("direction", DIRS)
def test_stale_quote_keeps_pending_without_reject(db, direction):
    now = _now_ms()
    order = make_armed(db, f"sq-{direction}", direction, now)
    clock = FakeClock(now)
    t = tick(direction, now - 120_000)
    execution_coordinator.evaluate_orders_sync(db, ticker_override=t, clock=clock, now_ms=now)
    db.refresh(order)
    assert order.state == "armed"
    assert events_of(db, "order.rejected") == []


@pytest.mark.parametrize("direction", DIRS)
def test_trigger_not_hit_keeps_pending(db, direction):
    now = _now_ms()
    order = make_armed(db, f"nt-{direction}", direction, now)
    px = 2700.0 if direction == "LONG" else 2600.0
    far = {"symbol": "XAUUSDT", "bid": px, "ask": px + 0.2, "timestamp": now, "observed_at": now}
    execution_coordinator.evaluate_orders_sync(db, ticker_override=far, clock=FakeClock(now), now_ms=now)
    db.refresh(order)
    assert order.state == "armed"


# --------------------------------------------------------------------------- lessons changed after arm

@pytest.mark.parametrize("direction", DIRS)
def test_red_rule_enabled_after_arm_blocks_fill_and_traces_before_fill(db, direction):
    now = _now_ms()
    order = make_armed(db, f"rl-{direction}", direction, now)
    rule = make_lesson(db, "Enabled after arm", predicate=RED_ALWAYS)
    run_tick(db, direction, now)
    db.refresh(order)
    assert order.state == "rejected" and "LESSON_RULE_BLOCKED" in (order.invalidation_reason or "")
    assert snapshot(db)["fills"] == 0 and events_of(db, "trade.opened") == []
    trace = json.loads(order.fill_decision_snapshot)
    assert trace["stage"] == "BEFORE_FILL"
    assert trace["entry_gate"]["stage"] == "FILL"
    assert str(rule.id) in json.dumps(trace)


@pytest.mark.parametrize("variant", ["disabled", "unapproved", "archived", "invalid"])
def test_non_effective_rules_do_not_block_fill(db, variant):
    now = _now_ms()
    order = make_armed(db, f"ne-{variant}", "LONG", now)
    kw = dict(predicate=RED_ALWAYS)
    kw.update({"disabled": dict(enabled=False), "unapproved": dict(is_approved=False, status="PENDING"),
               "archived": dict(status="ARCHIVED", enabled=False),
               "invalid": dict(validation_status="INVALID")}[variant])
    make_lesson(db, f"rule-{variant}", **kw)
    run_tick(db, "LONG", now)
    db.refresh(order)
    assert order.state == "paper_open"


def test_shadow_mode_red_does_not_block_fill(db):
    now = _now_ms()
    LessonPolicyService.update_policy(db, {"lesson_shadow_mode": True})
    order = make_armed(db, "shadow", "LONG", now)
    make_lesson(db, "Shadow red", predicate=RED_ALWAYS)
    run_tick(db, "LONG", now)
    db.refresh(order)
    assert order.state == "paper_open"


def test_entry_rules_switched_off_does_not_block_fill(db):
    now = _now_ms()
    LessonPolicyService.update_policy(db, {"lesson_entry_rules_enabled": False})
    order = make_armed(db, "off", "SHORT", now)
    make_lesson(db, "Red but feature off", predicate=RED_ALWAYS)
    run_tick(db, "SHORT", now)
    db.refresh(order)
    assert order.state == "paper_open"


def test_yellow_warns_but_fills_and_green_changes_nothing(db):
    now = _now_ms()
    make_lesson(db, "Yellow", severity="WARNING", effect="WARN_ENTRY", predicate=RED_ALWAYS)
    make_lesson(db, "Green", severity="INFO", effect="ANNOTATE")
    order = make_armed(db, "yg", "LONG", now)
    run_tick(db, "LONG", now)
    db.refresh(order)
    assert order.state == "paper_open"
    assert (order.stop_loss, order.take_profit) == (PLAN["LONG"]["sl"], PLAN["LONG"]["tp"])


@pytest.mark.parametrize("scope_dir,order_dir,blocked", [("SHORT", "LONG", False), ("LONG", "LONG", True),
                                                          ("SHORT", "SHORT", True), ("LONG", "SHORT", False)])
def test_rule_direction_scope(db, scope_dir, order_dir, blocked):
    now = _now_ms()
    make_lesson(db, "dir scoped", predicate=RED_ALWAYS, scope=json.dumps({"direction": scope_dir}))
    order = make_armed(db, "ds", order_dir, now)
    run_tick(db, order_dir, now)
    db.refresh(order)
    assert (order.state == "rejected") is blocked
    assert (order.state == "paper_open") is (not blocked)


@pytest.mark.parametrize("scope_mode,order_mode,blocked", [("AUTO", "MANUAL", False), ("MANUAL", "AUTO", False),
                                                            ("AUTO", "AUTO", True), ("MANUAL", "MANUAL", True)])
def test_rule_execution_mode_scope_is_preserved_from_order(db, scope_mode, order_mode, blocked):
    now = _now_ms()
    make_lesson(db, "mode scoped", predicate=RED_ALWAYS, scope=json.dumps({"execution_mode": scope_mode}))
    order = make_armed(db, "ms", "LONG", now, mode=order_mode,
                       origin="AUTO_STRATEGY" if order_mode == "AUTO" else "MANUAL_WEB")
    run_tick(db, "LONG", now)
    db.refresh(order)
    assert (order.state == "rejected") is blocked


def test_rule_strategy_family_scope(db):
    now = _now_ms()
    make_lesson(db, "family scoped", predicate=RED_ALWAYS, scope=json.dumps({"strategy_family": "NY_QUOTA_PAPER"}))
    std = make_armed(db, "fam-std", "LONG", now, family="STANDARD_SMC")
    run_tick(db, "LONG", now)
    db.refresh(std)
    assert std.state == "paper_open"


def test_new_lesson_text_does_not_affect_next_entry_until_activated(db):
    now = _now_ms()
    make_lesson(db, "Proposed only", status="PENDING", is_approved=False, enabled=False, predicate=RED_ALWAYS)
    order = make_armed(db, "prop", "LONG", now)
    run_tick(db, "LONG", now)
    db.refresh(order)
    assert order.state == "paper_open"


# --------------------------------------------------------------------------- policy

def test_policy_inactive_still_blocks_news_but_allows_when_safe(db):
    now = _now_ms()
    clock = FakeClock(now)
    safe = TradingPolicyService.evaluate_entry_policy(db, "MARKET", "STANDARD_SMC", clock=clock, now_ms=now)
    assert safe["allowed"] is True and safe["can_enter"] is True
    make_news(db, now)
    blocked = TradingPolicyService.evaluate_entry_policy(db, "MARKET", "STANDARD_SMC", clock=clock, now_ms=now)
    assert blocked["allowed"] is False and blocked["can_enter"] is False
    assert "NEWS_BLACKOUT" in json.dumps(blocked, default=str)


def test_policy_active_blocks_news(db):
    now = _now_ms()
    TradingPolicyService.update_policy(db, {"is_active": True}, clock=FakeClock(now))
    make_news(db, now)
    res = TradingPolicyService.evaluate_entry_policy(db, "MARKET", "STANDARD_SMC", clock=FakeClock(now), now_ms=now)
    assert res["allowed"] is False and res["can_enter"] is False
    assert "NEWS_BLACKOUT" in json.dumps(res, default=str)


# --------------------------------------------------------------------------- auto-arm

def _auto(db, setup, now):
    sig = {"signal_id": f"sig-{setup.id}"}
    strategy_service._last_auto_block = None
    strategy_service._auto_arm_candidate(db, setup, sig, now, datetime.fromtimestamp(now / 1000, tz=timezone.utc))


@pytest.mark.parametrize("direction", DIRS)
def test_auto_arm_positive_control_freezes_resolved_plan(db, fresh_ticker, direction):
    fresh_ticker()
    now = _now_ms()
    setup = make_setup(db, f"auto-ok-{direction}", direction)
    _auto(db, setup, now)
    order = db.query(models.PaperOrder).filter(models.PaperOrder.setup_id == setup.id).one()
    p = PLAN[direction]
    assert order.state == "armed" and order.execution_mode == "AUTO"
    assert (order.planned_entry, order.stop_loss, order.take_profit) == (p["entry"], p["sl"], p["tp"])


@pytest.mark.parametrize("direction", DIRS)
def test_auto_arm_blocked_by_news(db, fresh_ticker, direction):
    fresh_ticker()
    now = _now_ms()
    setup = make_setup(db, f"auto-news-{direction}", direction)
    make_news(db, now)
    _auto(db, setup, now)
    assert db.query(models.PaperOrder).count() == 0
    assert "NEWS_BLACKOUT" in strategy_service._last_auto_block[2]


@pytest.mark.parametrize("direction", DIRS)
def test_auto_arm_blocked_by_red_lesson(db, fresh_ticker, direction):
    fresh_ticker()
    now = _now_ms()
    setup = make_setup(db, f"auto-les-{direction}", direction)
    make_lesson(db, "Auto red", predicate=RED_ALWAYS, scope=json.dumps({"execution_mode": "AUTO"}))
    _auto(db, setup, now)
    assert db.query(models.PaperOrder).count() == 0
    assert "LESSON_RULE_BLOCKED" in strategy_service._last_auto_block[2]


def test_auto_arm_blocked_by_poor_rr_keeps_levels(db, fresh_ticker):
    fresh_ticker(bid=4198.0, ask=4198.3)
    now = _now_ms()
    setup = make_setup(db, "auto-rr", "SHORT", entry=4198.29, sl=4203.29, tp=4198.00)
    _auto(db, setup, now)
    assert db.query(models.PaperOrder).count() == 0
    db.refresh(setup)
    assert setup.provisional_tp == 4198.00


def test_auto_arm_blocked_by_stale_quote(db, fresh_ticker):
    fresh_ticker(now_ms=_now_ms() - 120_000)
    now = _now_ms()
    setup = make_setup(db, "auto-stale", "LONG")
    _auto(db, setup, now)
    assert db.query(models.PaperOrder).count() == 0


def test_auto_arm_repeated_block_is_deduplicated(db, fresh_ticker):
    fresh_ticker()
    now = _now_ms()
    setup = make_setup(db, "auto-dup", "LONG")
    make_news(db, now)
    _auto(db, setup, now)
    first = strategy_service._last_auto_block
    strategy_service._auto_arm_candidate(db, setup, {"signal_id": "x"}, now + 6000,
                                         datetime.fromtimestamp((now + 6000) / 1000, tz=timezone.utc))
    assert strategy_service._last_auto_block == first
    assert db.query(models.PaperOrder).count() == 0


# --------------------------------------------------------------------------- exits are never blocked

@pytest.mark.parametrize("direction", DIRS)
def test_open_position_still_exits_during_blackout_and_red_lesson(db, direction):
    now = _now_ms()
    order = make_armed(db, f"ex-{direction}", direction, now)
    run_tick(db, direction, now)
    db.refresh(order)
    assert order.state == "paper_open"
    make_news(db, now)
    make_lesson(db, "Red after open", predicate=RED_ALWAYS)
    sl = PLAN[direction]["sl"]
    bid, ask = (sl - 0.5, sl - 0.3) if direction == "LONG" else (sl + 0.3, sl + 0.5)
    closed = TradeLifecycleService.process_exit_tick(db, current_bid=bid, current_ask=ask, now_ms=now + 1000)
    assert closed is not None and closed.id == order.id
    assert closed.state == "closed"
    assert len(events_of(db, "trade.closed", order.id)) <= 1


def test_manual_close_still_works_during_blackout(db):
    now = _now_ms()
    order = make_armed(db, "mc", "LONG", now)
    run_tick(db, "LONG", now)
    make_news(db, now)
    db.refresh(order)
    assert order.state == "paper_open"
    closed = TradeLifecycleService.execute_close(db, order.id, exit_price=2651.0, exit_cause="MANUAL_CLOSE",
                                                 now_ms=now + 1000)
    assert closed.state == "closed"


# --------------------------------------------------------------------------- fail closed

def test_news_checker_failure_blocks_market_entry(db, client, fresh_ticker, monkeypatch):
    fresh_ticker(bid=2650.0, ask=2650.2)
    import services.news_blackout_service as nbs

    def boom(*a, **k):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(nbs, "evaluate_news_blackout", boom)
    resp = client.post("/api/v1/orders/paper", json=market_payload("LONG"))
    assert resp.status_code >= 400
    assert db.query(models.PaperOrder).count() == 0


def test_news_checker_failure_does_not_fill_pending_and_does_not_open(db, monkeypatch):
    now = _now_ms()
    order = make_armed(db, "fc", "LONG", now)
    import services.news_blackout_service as nbs

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(nbs, "evaluate_news_blackout", boom)
    run_tick(db, "LONG", now)
    db.refresh(order)
    assert order.state != "paper_open"
    assert snapshot(db)["fills"] == 0 and events_of(db, "trade.opened") == []


def test_lifecycle_failure_does_not_leave_double_fill_after_retry(db, monkeypatch):
    now = _now_ms()
    order = make_armed(db, "lf", "LONG", now)
    real = TradeLifecycleService.execute_fill
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(*a, **k)

    monkeypatch.setattr(TradeLifecycleService, "execute_fill", staticmethod(flaky))
    try:
        run_tick(db, "LONG", now)
    except Exception:
        db.rollback()
    run_tick(db, "LONG", now + 1000)
    db.refresh(order)
    assert order.state == "paper_open"
    assert snapshot(db)["fills"] == 1 and len(events_of(db, "trade.opened", order.id)) <= 1


# --------------------------------------------------------------------------- read-only actions

def test_read_only_endpoints_do_not_mutate_trading_state(db, client, fresh_ticker):
    fresh_ticker()
    setup = make_setup(db, "ro-1", "SHORT")
    before = snapshot(db)
    assert client.get(f"/api/v1/setups/eligibility/{setup.id}").status_code == 200
    client.get("/api/v1/setups/upcoming")
    client.post("/api/v1/orders/preview", json=market_payload("SHORT"))
    assert snapshot(db) == before


# --------------------------------------------------------------------------- concurrency

def test_concurrent_market_orders_open_exactly_one_position(tmp_path):
    from services.collector_service import collector_service

    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    prev = collector_service.latest_ticker
    ts = _now_ms()
    collector_service.latest_ticker = {"symbol": "XAUUSDT", "bid": 2650.0, "ask": 2650.2, "last": 2650.0,
                                       "timestamp": ts, "observed_at": ts}
    barrier = threading.Barrier(2)
    codes = []

    def worker(i):
        c = TestClient(app)
        barrier.wait()
        codes.append(c.post("/api/v1/orders/paper",
                            json=market_payload("LONG", idempotency_key=f"race-{i}")).status_code)

    try:
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        s = Session()
        try:
            opens = s.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").count()
            total = s.query(models.PaperOrder).count()
            fills = crud.get_or_create_today_audit(s).fills_count
        finally:
            s.close()
        assert codes.count(200) == 1, codes
        assert opens == 1 and total == 1 and fills == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        collector_service.latest_ticker = prev
        engine.dispose()


def test_two_pending_triggers_fill_only_one(db):
    now = _now_ms()
    a = make_armed(db, "two-a", "LONG", now)
    b = make_armed(db, "two-b", "LONG", now)
    run_tick(db, "LONG", now)
    db.refresh(a)
    db.refresh(b)
    assert sorted([a.state, b.state]).count("paper_open") == 1
    snap = snapshot(db)
    assert snap["open"] == 1 and snap["fills"] == 1


# --------------------------------------------------------------------------- WAITING_RETRACE evidence chain

def _evidence(db, setup, *, sweep=True, brk=True, fvg=True, h1="ALIGNED"):
    ev = models.StrategyEvidence(
        id=f"ev-{setup.id}", direction=setup.direction, setup_id=setup.id, h1_alignment=h1,
        sweep_evidence=json.dumps({"level": 2638.0}) if sweep else None,
        displacement_evidence=json.dumps({"break": 2652.0}) if brk else None,
        fvg_evidence=json.dumps({"low": 2646.0, "high": 2649.0}) if fvg else None,
        created_at=_now_ms(),
    )
    db.add(ev)
    setup.evidence_snapshot_id = ev.id
    db.commit()


@pytest.mark.parametrize("direction", DIRS)
def test_waiting_retrace_with_full_chain_arms_pending_limit_only(db, client, fresh_ticker, direction):
    fresh_ticker()
    setup = make_setup(db, f"rt-ok-{direction}", direction, state="WAITING_RETRACE")
    _evidence(db, setup)
    # Client asks for MARKET: a retrace plan must not be forced to fill immediately.
    resp = arm(client, setup, direction, order_type="MARKET")
    assert resp.status_code == 200, resp.text
    order = db.query(models.PaperOrder).filter(models.PaperOrder.setup_id == setup.id).one()
    assert order.state == "armed" and order.order_type == "LIMIT" and order.direction == direction


@pytest.mark.parametrize("missing", ["sweep", "brk", "fvg"])
def test_waiting_retrace_with_broken_chain_is_blocked_even_if_h1_aligned(db, client, fresh_ticker, missing):
    fresh_ticker()
    setup = make_setup(db, f"rt-bad-{missing}", "LONG", state="WAITING_RETRACE")
    _evidence(db, setup, **{missing: False}, h1="ALIGNED")
    resp = arm(client, setup, "LONG", order_type="LIMIT")
    assert resp.status_code >= 400
    assert "EVIDENCE_UNAVAILABLE" in json.dumps(resp.json(), ensure_ascii=False)
    assert db.query(models.PaperOrder).count() == 0


def test_retrace_ready_state_also_blocked_by_news(db, client, fresh_ticker):
    fresh_ticker()
    setup = make_setup(db, "rt-news", "SHORT", state="WAITING_RETRACE")
    _evidence(db, setup)
    make_news(db, _now_ms())
    resp = arm(client, setup, "SHORT", order_type="LIMIT")
    assert resp.status_code >= 400 and "NEWS_BLACKOUT" in json.dumps(resp.json(), ensure_ascii=False)
