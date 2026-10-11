import pytest
from lab.replay_contracts import ReplayPendingOrder, ReplayMarketEvent
from lab.replay_execution import try_fill_pending_order, reject_order
from domain_calculator import CostAssumptions
from lab.replay_evidence_utils import TERMINAL_ORDER_STATUSES, ACTIVE_ORDER_STATUSES, normalize_order_status

def test_v13_8_terminal_orders_cannot_fill_and_cause_no_side_effects():
    # PHẦN 23, 97: Terminal statuses REJECTED, FILLED, EXPIRED, CANCELLED passed into try_fill
    # must NOT create position, must NOT create posting, must NOT mutate status or cash
    for term_status in TERMINAL_ORDER_STATUSES:
        order = ReplayPendingOrder(
            order_id=f"ord-term-{term_status}",
            setup_id="set-1",
            session_id="NY-2026-07-15",
            entry_type="SMC_CONTEXT_SCHEDULED_PAPER",
            direction="LONG",
            planned_entry=2650.0,
            planned_sl=2640.0,
            planned_tp=2670.0,
            planned_net_rr=2.0,
            quantity=1.0,
            decision_ms=1000,
            earliest_execution_ms=1000,
            expiry_ms=5000,
            status=term_status
        )
        event = ReplayMarketEvent(
            kind="OPEN",
            timestamp=1000,
            timeframe="15M",
            open_price=2650.0,
            high_price=2655.0,
            low_price=2648.0,
            close_price=2652.0
        )
        pos, posting, reason = try_fill_pending_order(
            order=order,
            event=event,
            costs=CostAssumptions(),
            capital=1000.0,
            risk_pct=0.5
        )
        assert pos is None
        assert posting is None
        assert "TERMINAL_ORDER" in reason
        assert order.status == term_status

def test_v13_8_order_rejection_marks_status_rejected_cleanly():
    # PHẦN 24, 97: Geometry invalid sets status REJECTED without cash or position mutation
    order = ReplayPendingOrder(
        order_id="ord-bad-geometry",
        setup_id="set-2",
        session_id="NY-2026-07-15",
        entry_type="SMC_CONTEXT_SCHEDULED_PAPER",
        direction="LONG",
        planned_entry=2650.0,
        planned_sl=2660.0,  # Invalid for LONG: SL above entry
        planned_tp=2670.0,
        planned_net_rr=2.0,
        quantity=1.0,
        decision_ms=1000,
        earliest_execution_ms=1000,
        expiry_ms=5000,
        status="SUBMITTED"
    )
    event = ReplayMarketEvent(
        kind="OPEN",
        timestamp=1000,
        timeframe="15M",
        open_price=2650.0,
        high_price=2655.0,
        low_price=2648.0,
        close_price=2652.0
    )
    pos, posting, reason = try_fill_pending_order(
        order=order,
        event=event,
        costs=CostAssumptions(),
        capital=1000.0,
        risk_pct=0.5
    )
    assert pos is None
    assert posting is None
    assert order.status == "REJECTED"
    assert "POST_FILL_GEOMETRY_INVALID" in reason

def test_v13_8_reject_order_helper_contract():
    # PHẦN 24: reject_order helper contract
    order = ReplayPendingOrder(
        order_id="ord-reject-test",
        setup_id="set-3",
        session_id="NY-2026-07-15",
        entry_type="SMC_CONTEXT_SCHEDULED_PAPER",
        direction="SHORT",
        planned_entry=2650.0,
        planned_sl=2660.0,
        planned_tp=2630.0,
        planned_net_rr=2.0,
        quantity=1.0,
        decision_ms=1000,
        earliest_execution_ms=1000,
        expiry_ms=5000,
        status="PENDING"
    )
    res_pos, res_post, reason = reject_order(order, "TEST_RISK_GUARD_REJECTION")
    assert res_pos is None
    assert res_post is None
    assert reason == "TEST_RISK_GUARD_REJECTION"
    assert order.status == "REJECTED"
    assert getattr(order, "rejection_reason", None) == "TEST_RISK_GUARD_REJECTION"

    # Calling reject_order on already terminal order returns TERMINAL_ORDER and does not mutate further
    _, _, reason2 = reject_order(order, "ANOTHER_REASON")
    assert "TERMINAL_ORDER" in reason2

def test_v13_8_non_open_event_leaves_order_active():
    # PHẦN 23: Non-OPEN event cannot fill MARKET order and returns WAITING_EXECUTION_EVENT
    order = ReplayPendingOrder(
        order_id="ord-waiting-test",
        setup_id="set-4",
        session_id="NY-2026-07-15",
        entry_type="SMC_CONTEXT_SCHEDULED_PAPER",
        direction="LONG",
        planned_entry=2650.0,
        planned_sl=2640.0,
        planned_tp=2670.0,
        planned_net_rr=2.0,
        quantity=1.0,
        decision_ms=1000,
        earliest_execution_ms=1000,
        expiry_ms=5000,
        status="SUBMITTED"
    )
    event_close = ReplayMarketEvent(
        kind="CLOSE",
        timestamp=1000,
        timeframe="15M",
        open_price=2650.0,
        high_price=2655.0,
        low_price=2648.0,
        close_price=2652.0
    )
    pos, posting, reason = try_fill_pending_order(
        order=order,
        event=event_close,
        costs=CostAssumptions(),
        capital=1000.0,
        risk_pct=0.5
    )
    assert pos is None
    assert posting is None
    assert reason == "WAITING_EXECUTION_EVENT"
    assert order.status == "SUBMITTED"
