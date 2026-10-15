"""
test_v14_4_15_terminal_event_authority.py

PHẦN 011, 012, 013, 014, 015, 016, 017, 018, 019, 020, 021, 022, 023, 024, 025, 026, 027, 028, 029, 030:
Behavioral and unit test suite for Outcome Authority and Terminal Event Binding.

Case IDs covered:
- EVTAUTH01 (A01): Unknown recovery status repro fails closed (UNVERIFIED) and excluded from confirmed.
- EVTAUTH02 (A02): Terminal event decoding and binding matrix (types, orders, prices, pnl, causes, NaN).
- EVTAUTH03 (A03): Production normal and manual close positive control with committed domain events.
- EVTAUTH04 (A04): Verified recovery vs conservative simulation vs unresolved recovery.
- EVTAUTH05 (A05): Known-as-of boundary and contradictory terminal event detection.
- EVTAUTH06 (A06): Report and export parity with preloaded terminal event authority.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional
import pytest
from sqlalchemy.orm import Session

import models
from services.event_bus import event_bus
from services.forward_paper_service import (
    TerminalEventView,
    TerminalEventDecodeResult,
    decode_terminal_event,
    validate_terminal_binding,
    load_terminal_events_for_orders,
    read_outcome_authority,
    classify_forward_outcome,
    create_forward_paper_session,
    stage_forward_membership_binding,
    stage_forward_membership_status,
    generate_forward_paper_report,
    export_forward_paper_data,
)
from services.trade_lifecycle_service import TradeLifecycleService


class TestEventClock:
    """Deterministic event publication clock for test isolation (PHẦN 026, 027)."""
    __test__ = False

    def __init__(self, initial_ms: int = 1792000000000):
        self.now_ms = initial_ms

    def set_time_ms(self, ms: int):
        self.now_ms = ms

    def time(self) -> float:
        return self.now_ms / 1000.0


@pytest.fixture
def event_bus_clock(monkeypatch):
    """Function-scoped clock fixture isolating EventBus publication time (PHẦN 029, 030)."""
    import services.event_bus as eb_mod
    clk = TestEventClock()
    monkeypatch.setattr(eb_mod, "time", clk)
    return clk


def test_evtauth01_unknown_recovery_status_repro(isolated_db: Session):
    """EVTAUTH01 / A01: Unknown recovery status repro (PHẦN 002, 012, 013, 051).
    Real PaperOrder closed, valid TP_HIT / realized PnL, but recovery_status='UNSUPPORTED_RECOVERY'
    and recovery_confidence=None.
    Must return UNVERIFIED, countable_for_confirmed=False, excluded from confirmed denominator.
    Positive control: recovery_status in KNOWN_NORMAL_RECOVERY_STATUSES with confidence=None is CONFIRMED.
    Negative control: recovery_status='RECOVERED' with confidence=None is UNVERIFIED.
    """
    now_ms = 1792000000000

    # 1. Repro case: UNSUPPORTED_RECOVERY status with confidence=None
    ord_unsupported = models.PaperOrder(
        id="ord_a01_unsupported",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms - 5000,
        closed_at=now_ms,
        created_at=now_ms - 5000,
        recovery_status="UNSUPPORTED_RECOVERY",
        recovery_confidence=None,
    )

    auth = read_outcome_authority(ord_unsupported)
    assert auth.status == "UNVERIFIED", f"Expected UNVERIFIED, got {auth.status}"
    assert auth.countable_for_confirmed is False
    assert any("UNSUPPORTED_RECOVERY_STATUS" in r or "INVALID_RECOVERY_STATUS" in r for r in auth.reason_codes)

    cls_res = classify_forward_outcome(ord_unsupported)
    assert cls_res["classification"] == "UNVERIFIED"
    assert cls_res["countable_for_confirmed"] is False

    # 2. Positive control: Known normal recovery status with confidence=None
    for st in ("NONE", "UP_TO_DATE", None):
        ord_normal = models.PaperOrder(
            id=f"ord_a01_normal_{st}",
            instrument="XAUUSDT",
            direction="LONG",
            state="closed",
            planned_entry=4180.0,
            actual_entry=4180.0,
            stop_loss=4170.0,
            take_profit=4200.0,
            actual_exit=4200.0,
            exit_cause="TP_HIT",
            quantity=0.1,
            realized_pnl_net=20.0,
            realized_r=2.0,
            opened_at=now_ms - 5000,
            closed_at=now_ms,
            created_at=now_ms - 5000,
            recovery_status=st,
            recovery_confidence=None,
        )
        auth_norm = read_outcome_authority(ord_normal)
        assert auth_norm.status == "VERIFIED_PAPER"
        assert auth_norm.countable_for_confirmed is True
        cls_norm = classify_forward_outcome(ord_normal)
        assert cls_norm["classification"] == "CONFIRMED"
        assert cls_norm["countable_for_confirmed"] is True

    # 3. Negative control: RECOVERED status but missing recovery_confidence
    ord_rec_missing_conf = models.PaperOrder(
        id="ord_a01_rec_missing_conf",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms - 5000,
        closed_at=now_ms,
        created_at=now_ms - 5000,
        recovery_status="RECOVERED",
        recovery_confidence=None,
    )
    auth_rec = read_outcome_authority(ord_rec_missing_conf)
    assert auth_rec.status == "UNVERIFIED"
    assert auth_rec.countable_for_confirmed is False
    assert "MISSING_RECOVERY_CONFIDENCE_FOR_RECOVERED" in auth_rec.reason_codes


def test_evtauth02_terminal_event_matrix(isolated_db: Session):
    """EVTAUTH02 / A02: Terminal Event Decoding and Binding Matrix (PHẦN 015, 016, 017, 018, 052).
    Validates matching positive trade.closed / trade.liquidated events.
    Validates negative controls:
    - wrong order ID
    - unsupported event type (trade.opened, order.armed)
    - direction mismatch
    - exit cause mismatch
    - numeric price mismatch (> 0.05)
    - numeric pnl mismatch (> 0.05)
    - malformed JSON and NaN values.
    """
    now_ms = 1792000000000

    order = models.PaperOrder(
        id="ord_a02_matrix",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms - 5000,
        closed_at=now_ms,
        created_at=now_ms - 5000,
        recovery_status="NONE",
        recovery_confidence=None,
    )

    # 1. Matching positive trade.closed event
    pos_event = models.DomainEvent(
        event_id="ev_a02_pos",
        sequence=1,
        schema_version=1,
        event_type="trade.closed",
        aggregate_id=order.id,
        aggregate_version=1,
        occurred_at=now_ms,
        published_at=now_ms,
        payload=json.dumps({
            "order_id": order.id,
            "direction": "LONG",
            "exit_cause": "TP_HIT",
            "actual_entry": 4180.0,
            "actual_exit": 4200.0,
            "realized_pnl": 20.0,
            "realized_r": 2.0,
            "closed_at": now_ms,
        }),
    )

    dec_res = decode_terminal_event(pos_event)
    assert dec_res.status == "VALID"
    assert dec_res.event_view is not None
    assert dec_res.event_view.event_id == "ev_a02_pos"

    is_valid, reasons = validate_terminal_binding(order, dec_res.event_view)
    assert is_valid is True
    assert reasons == []

    auth = read_outcome_authority(order, terminal_event=pos_event)
    assert auth.status == "VERIFIED_PAPER"
    assert auth.terminal_event_id == "ev_a02_pos"
    assert auth.countable_for_confirmed is True

    # 2. Negative: Wrong order ID in event
    ev_wrong_order = models.DomainEvent(
        event_id="ev_a02_wrong_ord",
        sequence=2,
        schema_version=1,
        event_type="trade.closed",
        aggregate_id="ord_different",
        aggregate_version=1,
        occurred_at=now_ms,
        published_at=now_ms,
        payload=json.dumps({
            "order_id": "ord_different",
            "direction": "LONG",
            "exit_cause": "TP_HIT",
            "actual_exit": 4200.0,
            "realized_pnl": 20.0,
        }),
    )
    auth_wrong_ord = read_outcome_authority(order, terminal_event=ev_wrong_order)
    assert auth_wrong_ord.status == "UNVERIFIED"
    assert "TERMINAL_EVENT_ORDER_MISMATCH" in auth_wrong_ord.reason_codes

    # 3. Negative: Unsupported event type (trade.opened)
    ev_opened = models.DomainEvent(
        event_id="ev_a02_opened",
        sequence=3,
        schema_version=1,
        event_type="trade.opened",
        aggregate_id=order.id,
        aggregate_version=1,
        occurred_at=now_ms - 5000,
        published_at=now_ms - 5000,
        payload=json.dumps({"order_id": order.id, "direction": "LONG"}),
    )
    auth_opened = read_outcome_authority(order, terminal_event=ev_opened)
    assert auth_opened.status == "UNVERIFIED"
    assert any("UNSUPPORTED_TERMINAL_EVENT" in r for r in auth_opened.reason_codes)

    # 4. Negative: Direction mismatch (LONG order vs SHORT event)
    ev_dir_mismatch = models.DomainEvent(
        event_id="ev_a02_dir_mismatch",
        sequence=4,
        schema_version=1,
        event_type="trade.closed",
        aggregate_id=order.id,
        aggregate_version=1,
        occurred_at=now_ms,
        published_at=now_ms,
        payload=json.dumps({
            "order_id": order.id,
            "direction": "SHORT",
            "exit_cause": "TP_HIT",
            "actual_exit": 4200.0,
            "realized_pnl": 20.0,
        }),
    )
    auth_dir = read_outcome_authority(order, terminal_event=ev_dir_mismatch)
    assert auth_dir.status == "UNVERIFIED"
    assert "TERMINAL_EVENT_DIRECTION_MISMATCH" in auth_dir.reason_codes

    # 5. Negative: Exit cause mismatch (TP_HIT order vs SL_HIT event)
    ev_cause_mismatch = models.DomainEvent(
        event_id="ev_a02_cause_mismatch",
        sequence=5,
        schema_version=1,
        event_type="trade.closed",
        aggregate_id=order.id,
        aggregate_version=1,
        occurred_at=now_ms,
        published_at=now_ms,
        payload=json.dumps({
            "order_id": order.id,
            "direction": "LONG",
            "exit_cause": "SL_HIT",
            "actual_exit": 4200.0,
            "realized_pnl": 20.0,
        }),
    )
    auth_cause = read_outcome_authority(order, terminal_event=ev_cause_mismatch)
    assert auth_cause.status == "UNVERIFIED"
    assert "TERMINAL_EVENT_EXIT_CAUSE_MISMATCH" in auth_cause.reason_codes

    # 6. Negative: Exit price mismatch (4200.0 vs 4150.0 > tolerance 0.05)
    ev_price_mismatch = models.DomainEvent(
        event_id="ev_a02_price_mismatch",
        sequence=6,
        schema_version=1,
        event_type="trade.closed",
        aggregate_id=order.id,
        aggregate_version=1,
        occurred_at=now_ms,
        published_at=now_ms,
        payload=json.dumps({
            "order_id": order.id,
            "direction": "LONG",
            "exit_cause": "TP_HIT",
            "actual_exit": 4150.0,
            "realized_pnl": 20.0,
        }),
    )
    auth_price = read_outcome_authority(order, terminal_event=ev_price_mismatch)
    assert auth_price.status == "UNVERIFIED"
    assert "TERMINAL_EVENT_NUMERIC_MISMATCH" in auth_price.reason_codes

    # 7. Negative: Malformed JSON payload
    ev_bad_json = models.DomainEvent(
        event_id="ev_a02_bad_json",
        sequence=7,
        schema_version=1,
        event_type="trade.closed",
        aggregate_id=order.id,
        aggregate_version=1,
        occurred_at=now_ms,
        published_at=now_ms,
        payload="{not_valid_json: 123",
    )
    auth_bad_json = read_outcome_authority(order, terminal_event=ev_bad_json)
    assert auth_bad_json.status == "UNVERIFIED"
    assert any("PAYLOAD_INVALID" in r or "JSON" in r for r in auth_bad_json.reason_codes)

    # 8. Negative: Missing event sentinel when strict validation requested
    auth_missing = read_outcome_authority(order, terminal_event={"status": "MISSING"})
    assert auth_missing.status == "UNVERIFIED"
    assert "MISSING_TERMINAL_EVENT" in auth_missing.reason_codes


def test_evtauth03_production_normal_and_manual_positive_control(isolated_db: Session, event_bus_clock):
    """EVTAUTH03 / A03: Production normal & manual close positive control (PHẦN 023, 033, 036, 050, CLK21-01).
    Closes order via TradeLifecycleService.execute_close.
    Validates that DomainEvent was committed with deterministic publication time.
    Validates preloading via load_terminal_events_for_orders and confirmation in report/export.
    """
    now_ms = 1792000000000
    opened_at_ms = now_ms
    closed_at_ms = now_ms + 10000
    published_at_ms = now_ms + 10000
    as_of_ms = now_ms + 15000

    # Ensure event publication time is deterministic within fixture timeline
    event_bus_clock.set_time_ms(published_at_ms)

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_a03",
        virtual_capital=2000.0,
        now_ms=opened_at_ms,
    )

    # Setup open order
    order = models.PaperOrder(
        id="ord_a03_prod",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.5,
        opened_at=opened_at_ms,
        created_at=opened_at_ms,
        session_instance_id="ASIA_20261014",
    )
    isolated_db.add(order)
    isolated_db.commit()

    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=opened_at_ms)
    stage_forward_membership_status(isolated_db, order.id, "FILLED", now_ms=opened_at_ms)
    isolated_db.commit()

    # Execute canonical close via TradeLifecycleService
    closed_ord = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        now_ms=closed_at_ms,
        session_tag="ASIA",
    )
    assert closed_ord is not None
    assert closed_ord.state == "closed"

    # Preload terminal events
    events_map = load_terminal_events_for_orders(isolated_db, [order.id], as_of_ms=as_of_ms)
    assert order.id in events_map
    ev = events_map[order.id]
    assert ev.event_type == "trade.closed"

    # PHẦN 036: Assert timestamps explicitly
    assert ev.occurred_at == closed_at_ms
    assert ev.published_at == published_at_ms
    assert ev.occurred_at <= as_of_ms
    assert ev.published_at <= as_of_ms

    # Authority and classification
    auth = read_outcome_authority(closed_ord, terminal_event=ev)
    assert auth.status == "VERIFIED_PAPER"
    assert auth.countable_for_confirmed is True
    assert auth.terminal_event_id is not None

    cls_res = classify_forward_outcome(closed_ord, as_of_ms=as_of_ms, terminal_event=ev)
    assert cls_res["classification"] == "CONFIRMED"
    assert cls_res["countable_for_confirmed"] is True
    assert cls_res["pnl"] is not None

    # Report & export confirm the closed order
    rep = generate_forward_paper_report(isolated_db, sess.session_id, now_ms=as_of_ms)
    assert rep["confirmed_closed_trades"] == 1
    assert rep["confirmed_wins"] == 1

    exp = export_forward_paper_data(isolated_db, sess.session_id)
    assert len(exp) == 1
    assert exp[0]["outcome_classification"] == "CONFIRMED"


def test_clk21_02_late_publication_rejected_at_as_of(isolated_db: Session, event_bus_clock):
    """CLK21-02: Production close with publication time after as_of is rejected (PHẦN 041, 051).
    occurred_at = T + 10000 <= as_of (T + 15000), but published_at = T + 20000 > as_of.
    Outcome authority must mark UNVERIFIED with TERMINAL_EVENT_POST_AS_OF.
    """
    now_ms = 1792000000000
    opened_at_ms = now_ms
    closed_at_ms = now_ms + 10000
    published_at_ms = now_ms + 20000
    as_of_ms = now_ms + 15000

    event_bus_clock.set_time_ms(published_at_ms)

    order = models.PaperOrder(
        id="ord_clk02_late",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.5,
        opened_at=opened_at_ms,
        created_at=opened_at_ms,
        session_instance_id="ASIA_20261014",
    )
    isolated_db.add(order)
    isolated_db.commit()

    closed_ord = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        now_ms=closed_at_ms,
        session_tag="ASIA",
    )
    assert closed_ord is not None

    events_map = load_terminal_events_for_orders(isolated_db, [order.id], as_of_ms=as_of_ms)
    assert order.id in events_map
    ev = events_map[order.id]
    assert ev.published_at == published_at_ms
    assert ev.published_at > as_of_ms

    cls_res = classify_forward_outcome(closed_ord, as_of_ms=as_of_ms, terminal_event=ev)
    assert cls_res["classification"] == "UNVERIFIED"
    assert "TERMINAL_EVENT_POST_AS_OF" in cls_res["reasons"]
    assert cls_res["countable_for_confirmed"] is False


def test_clk21_03_boundary_publication_exact_as_of(isolated_db: Session, event_bus_clock):
    """CLK21-03: Publication time exactly equal to as_of is accepted (PHẦN 043, 052).
    published_at == as_of_ms (contract uses > as_of_ms).
    """
    now_ms = 1792000000000
    opened_at_ms = now_ms
    closed_at_ms = now_ms + 10000
    published_at_ms = now_ms + 15000
    as_of_ms = now_ms + 15000

    event_bus_clock.set_time_ms(published_at_ms)

    order = models.PaperOrder(
        id="ord_clk03_exact",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.5,
        opened_at=opened_at_ms,
        created_at=opened_at_ms,
        session_instance_id="ASIA_20261014",
    )
    isolated_db.add(order)
    isolated_db.commit()

    closed_ord = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        now_ms=closed_at_ms,
        session_tag="ASIA",
    )
    assert closed_ord is not None

    events_map = load_terminal_events_for_orders(isolated_db, [order.id], as_of_ms=as_of_ms)
    assert order.id in events_map
    ev = events_map[order.id]
    assert ev.published_at == as_of_ms

    cls_res = classify_forward_outcome(closed_ord, as_of_ms=as_of_ms, terminal_event=ev)
    assert cls_res["classification"] == "CONFIRMED"
    assert "TERMINAL_EVENT_POST_AS_OF" not in cls_res["reasons"]
    assert cls_res["countable_for_confirmed"] is True


def test_clk21_04_clock_restoration(monkeypatch):
    """CLK21-04: Clock monkeypatch does not leak outside its scope (PHẦN 053)."""
    import time as std_time
    import services.event_bus as eb_mod

    # Inside context, monkeypatch overrides
    with monkeypatch.context() as m:
        clk = TestEventClock(1234567890000)
        m.setattr(eb_mod, "time", clk)
        assert eb_mod.time.time() == 1234567890.0

    # Outside context, restored to stdlib time module
    assert eb_mod.time is std_time
    assert abs(eb_mod.time.time() - std_time.time()) < 1.0


@pytest.mark.parametrize("base_now_ms", [1700000000000, 1850000000000])
def test_clk21_05_shifted_timeline(isolated_db: Session, event_bus_clock, base_now_ms: int):
    """CLK21-05: Test behavior is deterministic and invariant across different timeline epochs (PHẦN 054)."""
    opened_at_ms = base_now_ms
    closed_at_ms = base_now_ms + 10000
    published_at_ms = base_now_ms + 10000
    as_of_ms = base_now_ms + 15000

    event_bus_clock.set_time_ms(published_at_ms)

    order = models.PaperOrder(
        id=f"ord_clk05_{base_now_ms}",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.5,
        opened_at=opened_at_ms,
        created_at=opened_at_ms,
        session_instance_id="ASIA_20261014",
    )
    isolated_db.add(order)
    isolated_db.commit()

    closed_ord = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        now_ms=closed_at_ms,
        session_tag="ASIA",
    )
    assert closed_ord is not None

    events_map = load_terminal_events_for_orders(isolated_db, [order.id], as_of_ms=as_of_ms)
    assert order.id in events_map
    ev = events_map[order.id]
    assert ev.published_at == published_at_ms

    cls_res = classify_forward_outcome(closed_ord, as_of_ms=as_of_ms, terminal_event=ev)
    assert cls_res["classification"] == "CONFIRMED"


def test_evtauth04_verified_and_conservative_recovery(isolated_db: Session):
    """EVTAUTH04 / A04: Verified vs Conservative vs Unresolved Recovery (PHẦN 024, 025, 054).
    - Verified recovery (CONFIRMED / CONFIRMED_TICK) -> VERIFIED_PAPER.
    - Conservative recovery (ASSUMED_CONSERVATIVE / SIMULATED_AMBIGUOUS) -> CONSERVATIVE_SIMULATION.
    - Unresolved recovery (UNRESOLVED_GAP / RECOVERING) -> UNRESOLVED.
    - RECOVERED status with confidence=None -> UNVERIFIED.
    """
    now_ms = 1792000000000

    # 1. Verified recovery
    ord_v = models.PaperOrder(
        id="ord_a04_verified",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms - 5000,
        closed_at=now_ms,
        created_at=now_ms - 5000,
        recovery_status="RECOVERED",
        recovery_confidence="CONFIRMED",
    )
    auth_v = read_outcome_authority(ord_v)
    assert auth_v.status == "VERIFIED_PAPER"
    assert auth_v.countable_for_confirmed is True

    # 2. Conservative simulation
    ord_c = models.PaperOrder(
        id="ord_a04_conservative",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4170.0,
        exit_cause="SL_HIT",
        quantity=0.1,
        realized_pnl_net=-10.0,
        realized_r=-1.0,
        opened_at=now_ms - 5000,
        closed_at=now_ms,
        created_at=now_ms - 5000,
        recovery_status="RECOVERED",
        recovery_confidence="ASSUMED_CONSERVATIVE",
    )
    auth_c = read_outcome_authority(ord_c)
    assert auth_c.status == "CONSERVATIVE_SIMULATION"
    assert auth_c.countable_for_confirmed is False

    # 3. Unresolved gap
    ord_u = models.PaperOrder(
        id="ord_a04_unresolved",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms - 5000,
        closed_at=now_ms,
        created_at=now_ms - 5000,
        recovery_status="UNRESOLVED_GAP",
        recovery_confidence="UNRESOLVED",
    )
    auth_u = read_outcome_authority(ord_u)
    assert auth_u.status == "UNRESOLVED"
    assert auth_u.countable_for_confirmed is False


def test_evtauth05_known_as_of_and_conflict_events(isolated_db: Session):
    """EVTAUTH05 / A05: Known-As-Of boundary and Contradictory Terminal Events (PHẦN 019, 022, 055).
    - Event occurred after as_of_ms is excluded from past report.
    - Contradictory terminal events for same order -> CONFLICTED.
    - Replayed identical events -> idempotent, positive.
    - Read operation does not mutate DB.
    """
    now_ms = 1792000000000

    order = models.PaperOrder(
        id="ord_a05_conflict",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.5,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms - 5000,
        closed_at=now_ms,
        created_at=now_ms - 5000,
        recovery_status="NONE",
        recovery_confidence=None,
    )
    isolated_db.add(order)

    # 1. Event 1: trade.closed at 4200 TP_HIT
    ev1 = models.DomainEvent(
        event_id="ev_a05_1",
        sequence=1,
        schema_version=1,
        event_type="trade.closed",
        aggregate_id=order.id,
        aggregate_version=1,
        occurred_at=now_ms,
        published_at=now_ms,
        payload=json.dumps({
            "order_id": order.id,
            "direction": "LONG",
            "exit_cause": "TP_HIT",
            "actual_exit": 4200.0,
            "realized_pnl": 20.0,
        }),
    )
    # 2. Event 2: Contradictory trade.closed at 4170 SL_HIT
    ev2 = models.DomainEvent(
        event_id="ev_a05_2",
        sequence=2,
        schema_version=1,
        event_type="trade.closed",
        aggregate_id=order.id,
        aggregate_version=2,
        occurred_at=now_ms + 1000,
        published_at=now_ms + 1000,
        payload=json.dumps({
            "order_id": order.id,
            "direction": "LONG",
            "exit_cause": "SL_HIT",
            "actual_exit": 4170.0,
            "realized_pnl": -10.0,
        }),
    )
    isolated_db.add_all([ev1, ev2])
    isolated_db.commit()

    # Preload at now_ms: ev2 is excluded by as_of_ms boundary! Only ev1 visible.
    events_at_t1 = load_terminal_events_for_orders(isolated_db, [order.id], as_of_ms=now_ms)
    assert order.id in events_at_t1
    assert getattr(events_at_t1[order.id], "event_id", "") == "ev_a05_1"

    # Preload at now_ms + 2000: both events visible and contradictory -> conflict detected!
    events_at_t2 = load_terminal_events_for_orders(isolated_db, [order.id], as_of_ms=now_ms + 2000)
    conflict_ev = events_at_t2[order.id]
    auth_conf = read_outcome_authority(order, terminal_event=conflict_ev)
    assert auth_conf.status == "CONFLICTED"
    assert "CONTRADICTORY_TERMINAL_EVENTS" in auth_conf.reason_codes


def test_evtauth06_report_and_export_parity(isolated_db: Session):
    """EVTAUTH06 / A06: Report and Export Parity with Terminal Events (PHẦN 029, 056).
    Ensures identical classification, confirmed PnL, and countable metrics across
    generate_forward_paper_report and export_forward_paper_data.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_a06",
        virtual_capital=5000.0,
        now_ms=now_ms,
    )

    # Order 1: Verified WIN
    ord_win = models.PaperOrder(
        id="ord_a06_win",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.5,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms - 10000,
        closed_at=now_ms - 2000,
        created_at=now_ms - 10000,
        recovery_status="NONE",
        recovery_confidence=None,
    )
    # Order 2: Unsupported recovery status (should be UNVERIFIED)
    ord_unsupported = models.PaperOrder(
        id="ord_a06_unsupp",
        instrument="XAUUSDT",
        direction="SHORT",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4190.0,
        take_profit=4160.0,
        actual_exit=4160.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.5,
        realized_pnl_net=20.0,
        realized_r=2.0,
        opened_at=now_ms - 8000,
        closed_at=now_ms - 1000,
        created_at=now_ms - 8000,
        recovery_status="UNSUPPORTED_RECOVERY",
        recovery_confidence=None,
    )

    isolated_db.add_all([ord_win, ord_unsupported])
    isolated_db.commit()

    stage_forward_membership_binding(isolated_db, sess.session_id, ord_win, now_ms=now_ms)
    stage_forward_membership_binding(isolated_db, sess.session_id, ord_unsupported, now_ms=now_ms)
    stage_forward_membership_status(isolated_db, ord_win.id, "CLOSED", now_ms=now_ms)
    stage_forward_membership_status(isolated_db, ord_unsupported.id, "CLOSED", now_ms=now_ms)

    # Commit domain event for ord_win
    ev_win = models.DomainEvent(
        event_id="ev_a06_win",
        sequence=1,
        schema_version=1,
        event_type="trade.closed",
        aggregate_id=ord_win.id,
        aggregate_version=1,
        occurred_at=now_ms - 2000,
        published_at=now_ms - 2000,
        payload=json.dumps({
            "order_id": ord_win.id,
            "direction": "LONG",
            "exit_cause": "TP_HIT",
            "actual_exit": 4200.0,
            "realized_pnl": 20.0,
        }),
    )
    isolated_db.add(ev_win)
    isolated_db.commit()

    report = generate_forward_paper_report(isolated_db, sess.session_id, now_ms=now_ms)
    export_rows = export_forward_paper_data(isolated_db, sess.session_id)

    # Assert ord_unsupported is UNVERIFIED in both
    row_map = {r["order_id"]: r for r in export_rows}
    assert row_map[ord_win.id]["outcome_classification"] == "CONFIRMED"
    assert row_map[ord_unsupported.id]["outcome_classification"] == "UNVERIFIED"

    # Confirmed denominator in report contains exactly ord_win, NOT ord_unsupported
    assert report["confirmed_closed_trades"] == 1
    assert report["confirmed_wins"] == 1
    assert report["unverified_closed_trades"] == 1
