import pytest
from lab.replay_contracts import ReplayPendingOrder, ReplayMarketEvent
from lab.replay_execution import try_fill_pending_order, reject_order
from domain_calculator import CostAssumptions
from lab.replay_evidence_utils import TERMINAL_ORDER_STATUSES, ACTIVE_ORDER_STATUSES, normalize_order_status

def test_terminal_order_cannot_be_filled():
    # PHẦN 23, 97: Parametrize terminal statuses REJECTED, FILLED, EXPIRED, CANCELLED
    for term_status in TERMINAL_ORDER_STATUSES:
        order = ReplayPendingOrder(
            order_id="ord-test-1",
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
        assert order.status == term_status  # Unchanged!

def test_order_rejection_marks_status_rejected():
    # PHẦN 24, 97: Geometry invalid marks status REJECTED
    order = ReplayPendingOrder(
        order_id="ord-test-2",
        setup_id="set-2",
        session_id="NY-2026-07-15",
        entry_type="SMC_CONTEXT_SCHEDULED_PAPER",
        direction="LONG",
        planned_entry=2650.0,
        planned_sl=2660.0,  # Invalid: SL above entry for LONG
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

def test_non_open_event_returns_waiting():
    # PHẦN 23: Event other than OPEN returns WAITING_EXECUTION_EVENT
    order = ReplayPendingOrder(
        order_id="ord-test-3",
        setup_id="set-3",
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
    assert order.status == "SUBMITTED"  # Still active!
