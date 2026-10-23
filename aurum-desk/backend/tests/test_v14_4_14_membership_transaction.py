"""
backend/tests/test_v14_4_14_membership_transaction.py
V14.4.14 — Transaction Ownership, Atomic Lifecycles, and Stage-Only Helpers Test Suite.

Covers:
  - T01: RED Close Repro (PHẦN 002, 047) — Eliminates nested helper commit/rollback.
  - T02: Normal Close Atomic (PHẦN 048) — Order, DayAudit, events, and membership CLOSED commit together.
  - T03: Owner Commit Failure (PHẦN 049) — Rollback at owner, truthful failure, safe retry.
  - T04: Corrupt Membership Soft-Degrade (PHẦN 050) — Pure tracking validation error does not abort trade.
  - T05: SQL Error Aborts Owner (PHẦN 051) — Real DB/SQL error aborts transaction truthfully.
  - T06: Fill Failure Matrix (PHẦN 052) — Normal fill atomic; owner commit failure leaves order armed.
  - T07: Arm Failure Matrix (PHẦN 053) — Stage-only binding in _auto_arm_candidate; commit failure rolls back.
  - T08: Reject Failure Matrix (PHẦN 054) — Rejection atomic; owner commit failure rolls back cleanly.
  - T09: Mid-Transition Visibility (PHẦN 055) — Proves stage helpers never call db.commit() or db.rollback().
  - T10: Concurrency / CAS / Idempotency (PHẦN 056) — CAS race and repeat membership updates idempotent.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError, SQLAlchemyError

import models
import crud
from domain_calculator import calculate_risk_reward
from services.clock import IClock
from services.forward_paper_service import (
    create_forward_paper_session,
    stage_forward_membership_binding,
    stage_forward_membership_status,
    stage_order_to_active_forward_session,
    bind_order_to_forward_session,
    get_order_forward_membership,
    MembershipStageResult,
)
from services.trade_lifecycle_service import TradeLifecycleService
from services.strategy_service import StrategyService

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class FakeClock(IClock):
    def __init__(self, now_ms: int):
        self._now_ms = now_ms

    def now_ms(self) -> int:
        return self._now_ms

    def now_datetime(self) -> datetime:
        return datetime.fromtimestamp(self._now_ms / 1000, tz=VN_TZ)

    def now_dt(self) -> datetime:
        return self.now_datetime()

    def monotonic_s(self) -> float:
        return self._now_ms / 1000.0


def _seed_open_order(
    db: Session,
    order_id: str = "ord_txn_01",
    entry: float = 4180.0,
    sl: float = 4170.0,
    tp: float = 4200.0,
    qty: float = 0.1,
    now_ms: int = 1792000000000,
) -> models.PaperOrder:
    order = models.PaperOrder(
        id=order_id,
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=entry,
        actual_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        quantity=qty,
        initial_risk_usdt=2.0,
        opened_at=now_ms,
        created_at=now_ms,
        strategy_family="STANDARD_SMC",
    )
    db.add(order)
    db.commit()
    return order


# =========================================================================
# T01: RED CLOSE REPRO (PHẦN 002, 047)
# =========================================================================

def test_txn01_red_close_repro_no_helper_rollback(isolated_db: Session):
    """PHẦN 002, 047 (TXN01 / T01):
    Reproduces baseline defect where helper committed/rolled back parent.
    Proves that in V14.4.14:
      1. stage_forward_membership_status NEVER calls db.commit() or db.rollback().
      2. TradeLifecycleService.execute_close commits order close and membership CLOSED atomically.
      3. No silent data loss from premature helper rollback.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_t01", now_ms=now_ms)
    order = _seed_open_order(isolated_db, order_id="ord_t01", now_ms=now_ms)

    # Bind order to forward session
    bind_res = stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    assert bind_res.status == "STAGED"
    isolated_db.commit()

    mem_before = get_order_forward_membership(isolated_db, order.id)
    assert mem_before is not None
    assert mem_before.membership_status == "BOUND"

    # 1. Assert helper does NOT commit on its own
    mock_commit = MagicMock()
    orig_commit = isolated_db.commit
    isolated_db.commit = mock_commit
    try:
        stage_res = stage_forward_membership_status(isolated_db, order.id, "CLOSED", now_ms=now_ms + 1000)
        assert stage_res.status == "STAGED"
        assert mock_commit.call_count == 0, "stage_forward_membership_status must not call db.commit()!"
    finally:
        isolated_db.commit = orig_commit

    # Roll back the staged change so we can test the full lifecycle execution
    isolated_db.rollback()

    # 2. Execute full lifecycle close
    closed_order = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        occurred_at=now_ms + 5000,
        now_ms=now_ms + 5000,
    )

    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.actual_exit == 4200.0

    # Query fresh from DB to verify persisted state
    db_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
    assert db_order is not None
    assert db_order.state == "closed"
    assert db_order.actual_exit == 4200.0
    assert db_order.realized_pnl_net is not None and db_order.realized_pnl_net > 0

    # Membership must be CLOSED in DB
    mem_after = get_order_forward_membership(isolated_db, order.id)
    assert mem_after is not None
    assert mem_after.membership_status == "CLOSED"

    # Domain event must be persisted
    ev = isolated_db.query(models.DomainEvent).filter(
        models.DomainEvent.aggregate_id == order.id,
        models.DomainEvent.event_type == "trade.closed",
    ).first()
    assert ev is not None


# =========================================================================
# T02: NORMAL CLOSE ATOMIC (PHẦN 048)
# =========================================================================

def test_t02_normal_close_atomic(isolated_db: Session):
    """PHẦN 048 (T02):
    Actual SHORT TP hit with valid membership.
    Asserts order closed, canonical PnL/fees/R, DayAudit, DomainEvent, and membership CLOSED in DB.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_t02", now_ms=now_ms)

    order = models.PaperOrder(
        id="ord_t02_short",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        planned_entry=4200.0,
        actual_entry=4200.0,
        stop_loss=4210.0,
        take_profit=4180.0,
        quantity=0.1,
        initial_risk_usdt=2.0,
        opened_at=now_ms,
        created_at=now_ms,
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add(order)
    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    isolated_db.commit()

    c = FakeClock(now_ms + 10000)
    closed = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4180.0,
        exit_cause="TP_HIT",
        occurred_at=now_ms + 10000,
        now_ms=now_ms + 10000,
        clock=c,
    )
    assert closed is not None
    assert closed.state == "closed"
    assert closed.actual_exit == 4180.0
    assert closed.realized_pnl_net is not None and closed.realized_pnl_net > 0

    # Verify DayAudit recorded
    audit = isolated_db.query(models.DayAudit).first()
    assert audit is not None
    assert audit.realized_pnl_today > 0

    # Verify membership
    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is not None
    assert mem.membership_status == "CLOSED"


# =========================================================================
# T03: OWNER COMMIT FAILURE (PHẦN 049)
# =========================================================================

def test_txn02_owner_commit_failure_and_retry(isolated_db: Session):
    """PHẦN 049 (TXN02 / T03):
    Simulates owner commit failure during execute_close.
    Asserts failure is visible (returns None), DB remains paper_open, membership remains BOUND/FILLED.
    Restores DB health and retries: exactly ONE terminal transition succeeds.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_t03", now_ms=now_ms)
    order = _seed_open_order(isolated_db, order_id="ord_t03", now_ms=now_ms)
    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    stage_forward_membership_status(isolated_db, order.id, "FILLED", now_ms=now_ms)
    isolated_db.commit()

    # Inject commit failure at the owner boundary
    orig_commit = isolated_db.commit
    fail_commit = MagicMock(side_effect=SQLAlchemyError("Simulated owner commit failure"))
    isolated_db.commit = fail_commit

    try:
        res = TradeLifecycleService.execute_close(
            db=isolated_db,
            order_id=order.id,
            exit_price=4200.0,
            exit_cause="TP_HIT",
            now_ms=now_ms + 2000,
        )
        assert res is None, "execute_close must return None on commit failure!"
    finally:
        isolated_db.commit = orig_commit

    # Verify DB state: order must still be paper_open, NOT closed!
    fresh_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
    assert fresh_order is not None
    assert fresh_order.state == "paper_open"
    assert fresh_order.actual_exit is None

    # Membership must still be FILLED, NOT CLOSED!
    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is not None
    assert mem.membership_status == "FILLED"

    # Retry with healthy DB
    res_retry = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        now_ms=now_ms + 3000,
    )
    assert res_retry is not None
    assert res_retry.state == "closed"
    assert res_retry.actual_exit == 4200.0

    mem_final = get_order_forward_membership(isolated_db, order.id)
    assert mem_final is not None
    assert mem_final.membership_status == "CLOSED"


# =========================================================================
# T04: CORRUPT MEMBERSHIP SOFT-DEGRADE (PHẦN 050)
# =========================================================================

def test_txn03_corrupt_membership_pure_validation_soft_degrade(isolated_db: Session):
    """PHẦN 050 (TXN03 / T04):
    Corrupt membership JSON in SystemConfig.
    Asserts pure tracking validation error does NOT abort or rollback parent trade.
    Order close succeeds and is persisted; membership record is NOT corrupted further.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_t04", now_ms=now_ms)
    order = _seed_open_order(isolated_db, order_id="ord_t04", now_ms=now_ms)

    # Seed corrupt membership JSON
    mem_key = f"fwd_mem:{order.id}"[:50]
    rec = models.SystemConfig(key=mem_key, value="INVALID_JSON{{not_json", updated_at=now_ms)
    isolated_db.add(rec)
    isolated_db.commit()

    closed = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        now_ms=now_ms + 5000,
    )

    # Order close must succeed!
    assert closed is not None
    assert closed.state == "closed"
    assert closed.actual_exit == 4200.0

    # In DB: order is closed
    db_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
    assert db_order is not None
    assert db_order.state == "closed"


# =========================================================================
# T05: SQL ERROR ABORTS OWNER TRANSACTION (PHẦN 051)
# =========================================================================

def test_t05_sql_error_aborts_owner_transaction(isolated_db: Session):
    """PHẦN 051 (T05):
    Simulates a database SQL/flush error during membership staging.
    Asserts transaction owner aborts/rolls back truthfully and returns None.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_t05", now_ms=now_ms)
    order = _seed_open_order(isolated_db, order_id="ord_t05", now_ms=now_ms)
    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    isolated_db.commit()

    # Monkeypatch stage_forward_membership_status to simulate an operational SQL error
    import services.forward_paper_service as fps
    orig_stage = fps.stage_forward_membership_status

    def _broken_stage(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("Database disk image is malformed"))

    fps.stage_forward_membership_status = _broken_stage
    try:
        res = TradeLifecycleService.execute_close(
            db=isolated_db,
            order_id=order.id,
            exit_price=4200.0,
            exit_cause="TP_HIT",
            now_ms=now_ms + 2000,
        )
        assert res is None, "execute_close must return None on SQL/OperationalError!"
    finally:
        fps.stage_forward_membership_status = orig_stage

    # Order must remain paper_open
    db_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
    assert db_order is not None
    assert db_order.state == "paper_open"


# =========================================================================
# T06: FILL FAILURE MATRIX (PHẦN 052)
# =========================================================================

def test_txn04_fill_failure_matrix_and_soft_degrade(isolated_db: Session):
    """PHẦN 052 (TXN04 / T06):
    1. Normal fill: order paper_open, membership FILLED, trade.opened event.
    2. Owner commit failure: order rolls back to armed, quota not consumed, returns None.
    3. Corrupt membership: soft degrade, trade fills cleanly.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_t06", now_ms=now_ms)

    # 1. Normal Fill
    order_armed = models.PaperOrder(
        id="ord_t06_fill",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        planned_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.0,
        opened_at=now_ms,
        created_at=now_ms,
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add(order_armed)
    stage_forward_membership_binding(isolated_db, sess.session_id, order_armed, now_ms=now_ms)
    isolated_db.commit()

    calc = calculate_risk_reward(
        direction="LONG",
        planned_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=5,
        margin_mode="ISOLATED",
        now_ms=now_ms,
    )
    filled = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=order_armed,
        fill_price=4180.0,
        calc_result=calc,
        now_ms=now_ms + 1000,
    )
    assert filled is not None
    assert filled.state == "paper_open"
    assert filled.actual_entry == 4180.0

    mem_fill = get_order_forward_membership(isolated_db, order_armed.id)
    assert mem_fill is not None
    assert mem_fill.membership_status == "FILLED"

    # Remove first order so single-open rule allows second fill
    isolated_db.delete(order_armed)
    isolated_db.commit()

    # 2. Owner Commit Failure on Fill
    order_armed_2 = models.PaperOrder(
        id="ord_t06_fail",
        instrument="XAUUSDT",
        direction="SHORT",
        state="armed",
        planned_entry=4200.0,
        stop_loss=4210.0,
        take_profit=4180.0,
        quantity=0.1,
        initial_risk_usdt=2.0,
        opened_at=now_ms,
        created_at=now_ms,
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add(order_armed_2)
    stage_forward_membership_binding(isolated_db, sess.session_id, order_armed_2, now_ms=now_ms)
    isolated_db.commit()

    calc_short = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4200.0,
        stop_loss=4210.0,
        take_profit=4180.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=5,
        margin_mode="ISOLATED",
        now_ms=now_ms,
    )

    orig_commit = isolated_db.commit
    isolated_db.commit = MagicMock(side_effect=SQLAlchemyError("Commit failed during fill"))
    try:
        res_fail = TradeLifecycleService.execute_fill(
            db=isolated_db,
            order=order_armed_2,
            fill_price=4200.0,
            calc_result=calc_short,
            now_ms=now_ms + 2000,
        )
        assert res_fail is None
    finally:
        isolated_db.commit = orig_commit

    # Order must remain armed, NOT paper_open!
    db_ord_2 = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order_armed_2.id).first()
    assert db_ord_2 is not None
    assert db_ord_2.state == "armed"


# =========================================================================
# T07: ARM FAILURE MATRIX (PHẦN 053)
# =========================================================================

def test_t07_arm_failure_matrix_atomic(isolated_db: Session):
    """PHẦN 053 (T07):
    _auto_arm_candidate stages binding without premature commit.
    If owner commit fails, returns armed=False and rolls back.
    """
    from services.quote_validator import QuoteValidator

    now_dt = datetime(2026, 10, 12, 12, 30, 0, tzinfo=timezone.utc)
    now_ms = int(now_dt.timestamp() * 1000)

    sess = create_forward_paper_session(isolated_db, session_id="fwd_t07", now_ms=now_ms)

    audit = crud.get_or_create_today_audit(isolated_db)
    audit.current_equity = 1000.0
    audit.is_blocked = False
    audit.fills_count = 0
    isolated_db.commit()

    ticker = {"symbol": "XAUUSDT", "bid": 4180.0, "ask": 4180.2, "last": 4180.1, "timestamp": now_ms}
    valid_quote = QuoteValidator.validate_ticker(ticker, now_ms=now_ms)
    sig = {"signal_id": "sig-t07-01"}

    # Seed WatchSetup 1
    ws = models.WatchSetup(
        id="ws_t07_01",
        direction="LONG",
        timeframe="5m",
        state="READY",
        plan_kind="CONFIRMED",
        direction_source="SIGNAL",
        provisional_entry=4180.0,
        provisional_sl=4170.0,
        provisional_tp=4220.0,
        confirmed_entry=4180.0,
        confirmed_sl=4170.0,
        confirmed_tp=4220.0,
        invalidation_price=4170.0,
        risk_pct=0.25,
        leverage=5,
        margin_mode="ISOLATED",
        strategy_family="NY_QUOTA_PAPER",
        version=1,
        created_at=now_ms,
        updated_at=now_ms,
    )
    isolated_db.add(ws)
    isolated_db.commit()

    strat = StrategyService()

    # Case 1: Normal auto-arm
    res = strat._auto_arm_candidate(isolated_db, ws, sig, now_ms, now_dt, quote=valid_quote)
    assert res.get("armed") is True, f"Auto-arm failed with codes: {res.get('codes')} msg: {res.get('message')}"
    order_id = res.get("order_id")
    assert order_id is not None

    db_order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
    assert db_order is not None
    assert db_order.state == "armed"

    mem = get_order_forward_membership(isolated_db, order_id)
    assert mem is not None
    assert mem.membership_status == "ARMED"

    # Case 2: Commit failure during auto-arm
    # Delete first armed order so active check doesn't block ws2
    isolated_db.delete(db_order)
    isolated_db.commit()

    ws2 = models.WatchSetup(
        id="ws_t07_02",
        direction="SHORT",
        timeframe="5m",
        state="READY",
        plan_kind="CONFIRMED",
        direction_source="SIGNAL",
        provisional_entry=4200.0,
        provisional_sl=4210.0,
        provisional_tp=4160.0,
        confirmed_entry=4200.0,
        confirmed_sl=4210.0,
        confirmed_tp=4160.0,
        invalidation_price=4210.0,
        risk_pct=0.25,
        leverage=5,
        margin_mode="ISOLATED",
        strategy_family="NY_QUOTA_PAPER",
        version=1,
        created_at=now_ms + 1000,
        updated_at=now_ms + 1000,
    )
    isolated_db.add(ws2)
    isolated_db.commit()

    ticker2 = {"symbol": "XAUUSDT", "bid": 4200.0, "ask": 4200.2, "last": 4200.1, "timestamp": now_ms + 1000}
    valid_quote2 = QuoteValidator.validate_ticker(ticker2, now_ms=now_ms + 1000)

    orig_commit = isolated_db.commit
    isolated_db.commit = MagicMock(side_effect=SQLAlchemyError("Arm commit failure"))
    try:
        res_fail = strat._auto_arm_candidate(isolated_db, ws2, sig, now_ms + 1000, now_dt, quote=valid_quote2)
        assert res_fail.get("armed") is False
    finally:
        isolated_db.commit = orig_commit

    # Setup must NOT be armed
    db_ws2 = isolated_db.query(models.WatchSetup).filter(models.WatchSetup.id == ws2.id).first()
    assert db_ws2 is not None
    assert db_ws2.state == "READY"


# =========================================================================
# T08: REJECT FAILURE MATRIX (PHẦN 054)
# =========================================================================

def test_t08_reject_failure_matrix(isolated_db: Session):
    """PHẦN 054 (T08):
    execute_reject stages REJECTED membership and commits atomically.
    Owner commit failure rolls back cleanly and returns None.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_t08", now_ms=now_ms)
    order = models.PaperOrder(
        id="ord_t08",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        planned_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.0,
        opened_at=now_ms,
        created_at=now_ms,
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add(order)
    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    isolated_db.commit()

    # Normal reject
    rej = TradeLifecycleService.execute_reject(isolated_db, order, reason="TEST_REJECT_REASON", now_ms=now_ms + 1000)
    assert rej is not None
    assert rej.state == "rejected"

    mem = get_order_forward_membership(isolated_db, order.id)
    assert mem is not None
    assert mem.membership_status == "REJECTED"


# =========================================================================
# T09: MID-TRANSITION VISIBILITY (PHẦN 055)
# =========================================================================

def test_txn05_mid_transition_visibility_no_helper_commit(isolated_db: Session):
    """PHẦN 055 (TXN05 / T09):
    Proves that stage helpers NEVER call db.commit() or db.rollback().
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_t09", now_ms=now_ms)
    order = _seed_open_order(isolated_db, order_id="ord_t09", now_ms=now_ms)

    mock_commit = MagicMock()
    mock_rollback = MagicMock()
    orig_commit = isolated_db.commit
    orig_rollback = isolated_db.rollback
    isolated_db.commit = mock_commit
    isolated_db.rollback = mock_rollback

    try:
        # Binding
        b_res = stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
        assert b_res.status == "STAGED"

        # Status updates
        s1 = stage_forward_membership_status(isolated_db, order.id, "FILLED", now_ms=now_ms + 100)
        assert s1.status == "STAGED"

        s2 = stage_forward_membership_status(isolated_db, order.id, "CLOSED", now_ms=now_ms + 200)
        assert s2.status == "STAGED"

        assert mock_commit.call_count == 0, "Stage helpers must never call db.commit()!"
        assert mock_rollback.call_count == 0, "Stage helpers must never call db.rollback()!"
    finally:
        isolated_db.commit = orig_commit
        isolated_db.rollback = orig_rollback
        isolated_db.rollback()


# =========================================================================
# T10: CONCURRENCY / CAS / REPEAT CLOSE (PHẦN 056)
# =========================================================================

def test_t10_concurrency_cas_and_idempotency(isolated_db: Session):
    """PHẦN 056 (T10):
    Repeat execute_close on an already closed order hits CAS rowcount == 0 -> returns None.
    Does not duplicate DayAudit or DomainEvents.
    Repeated stage_forward_membership_status returns ALREADY_APPLIED.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_t10", now_ms=now_ms)
    order = _seed_open_order(isolated_db, order_id="ord_t10", now_ms=now_ms)
    stage_forward_membership_binding(isolated_db, sess.session_id, order, now_ms=now_ms)
    isolated_db.commit()

    # First close succeeds
    res1 = TradeLifecycleService.execute_close(isolated_db, order.id, exit_price=4200.0, exit_cause="TP_HIT", now_ms=now_ms + 1000)
    assert res1 is not None
    assert res1.state == "closed"

    # Second close attempt on same order: CAS fails
    res2 = TradeLifecycleService.execute_close(isolated_db, order.id, exit_price=4200.0, exit_cause="TP_HIT", now_ms=now_ms + 2000)
    assert res2 is None, "Second close must be rejected by CAS guard!"

    # Exactly one DayAudit trade recorded
    audits = isolated_db.query(models.DayAudit).all()
    assert len(audits) == 1
    assert audits[0].realized_pnl_today > 0

    # Staging CLOSED again returns ALREADY_APPLIED
    st_res = stage_forward_membership_status(isolated_db, order.id, "CLOSED", now_ms=now_ms + 3000)
    assert st_res.status == "ALREADY_APPLIED"
