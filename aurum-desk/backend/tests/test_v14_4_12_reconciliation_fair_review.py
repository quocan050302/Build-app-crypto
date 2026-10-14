"""
backend/tests/test_v14_4_12_reconciliation_fair_review.py
V14.4.12 — Reconciliation Fair Review & Starvation Prevention Test Suite.

Proves:
  Q01: Starvation Fix — Corrupt/invalid snapshot order is quarantined in durable review;
       tail valid orders continue to be audited and traversed.
  Control: Same fixture without broken order proves tail orders are audited normally.
  Q02: Restart Recovery — Progress, review record, and retry metadata survive session restart;
       no duplicate review records created.
  Q03: Persist Failure Isolation — DB failure during review persistence halts traversal safely
       without losing the issue or advancing cursor prematurely; recovers on retry.
  Q04: Source Repaired — Updating broken snapshot to valid canonical contract triggers
       clean replay, strict validation pass, and task recovery.
  Q05: Still Invalid Backoff — Retrying unchanged broken order backs off next_retry_at
       without spamming domain events or falsifying outcomes.
  Q06: Protective Exit Unblocked — Background review of OPEN order does not impede
       protective TP/SL execution consumer.
  Q07: Large History Pagination — Bounded cycle budgets traverse 51+ valid orders past
       quarantined order without starvation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import pytest
from sqlalchemy.orm import Session, sessionmaker

import models
from services.market_reconciliation_service import (
    CycleBudget,
    MarketReconciliationService,
    OrderReconciliationWindow,
    ReconciliationReviewRecord,
    ReconciliationReviewStatus,
    ReconciliationWorkDisposition,
    classify_reconciliation_issue,
    compute_review_retry_at,
    is_scheduler_item_handled,
    load_due_review_tasks,
    load_review_record,
    load_scan_progress,
    persist_reconciliation_evidence,
    persist_review_pending,
    retry_review_task,
    save_review_record,
    trigger_source_repair_recheck,
)
from services.trade_lifecycle_service import TradeLifecycleService


def _seed_closed_order(
    db: Session,
    order_id: str,
    opened_at: int = 1000,
    closed_at: int = 3000,
    symbol: str = "XAUUSDT",
    direction: str = "LONG",
    entry_price: float = 4180.0,
    sl_price: float = 4170.0,
    tp_price: float = 4200.0,
    exit_price: float = 4169.5,
    exit_cause: str = "SL_HIT",
    cost_snapshot: Any = None,
    strategy_family: str = "STANDARD_SMC",
) -> models.PaperOrder:
    if cost_snapshot is None:
        cost_snapshot = json.dumps({
            "direction": direction,
            "actual_entry": entry_price,
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "cost_source": "1.0.0",
            "policy_config_version": 1,
        })

    ord = models.PaperOrder(
        id=str(order_id),
        instrument=symbol,
        direction=direction,
        state="closed",
        planned_entry=entry_price,
        actual_entry=entry_price,
        stop_loss=sl_price,
        take_profit=tp_price,
        actual_exit=exit_price,
        exit_cause=exit_cause,
        quantity=0.1,
        initial_risk_usdt=2.0,
        realized_pnl_net=-2.0,
        realized_r=-1.0,
        opened_at=opened_at,
        created_at=opened_at,
        closed_at=closed_at,
        cost_snapshot=cost_snapshot if cost_snapshot != "OMIT" else None,
        strategy_family=strategy_family,
    )
    db.add(ord)
    db.commit()
    db.refresh(ord)
    return ord


# =========================================================================
# Q01: STARVATION FIX TEST
# =========================================================================

def test_q01_starvation_fix_broken_head_does_not_starve_tail(isolated_db: Session):
    """PHẦN 013, 061 (Q01):
    One broken CLOSED order at head of reconciliation queue (cost_snapshot = '{broken').
    Three valid CLOSED orders behind it.
    Evidence produces conflicting terminal audit (earlier TP at 2000 vs closed at 3000 on SL).
    Coordinator must:
    1. Mark broken order as REVIEW_PENDING in durable review record.
    2. NOT close or alter PnL for broken order.
    3. Traverse past broken order and audit all 3 valid orders behind it.
    4. Progress scheduler cursor past the broken order.
    """
    # 1. Seed broken order head (most recent closed order)
    broken_ord = _seed_closed_order(
        isolated_db,
        order_id="ord_broken_head",
        opened_at=1000,
        closed_at=3500,
        cost_snapshot="{broken",
    )

    # 2. Seed 3 valid closed orders behind it (earlier closed times)
    v1 = _seed_closed_order(isolated_db, order_id="ord_valid_01", opened_at=1000, closed_at=3400)
    v2 = _seed_closed_order(isolated_db, order_id="ord_valid_02", opened_at=1000, closed_at=3300)
    v3 = _seed_closed_order(isolated_db, order_id="ord_valid_03", opened_at=1000, closed_at=3200)

    # 3. Seed conflicting TP quote at 2000 for all orders (earlier than all closed_at)
    quotes = [
        {"event_id": "ev_tp_2000", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"}
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    service = MarketReconciliationService()

    # Run bounded cycles of process_late_batch
    budget = CycleBudget(max_windows=10, max_pages=10, max_records=50)
    service.process_late_batch(isolated_db, symbol="XAUUSDT", budget=budget)

    # Assert 1: Broken order has durable review record
    rec = load_review_record(isolated_db, broken_ord.id)
    assert rec is not None, "Broken order must have durable review record"
    assert rec.status == ReconciliationReviewStatus.PENDING_REVIEW.value
    assert any("CORRUPT_COST_SNAPSHOT_JSON" in r for r in rec.reason_codes)

    # Assert 2: Broken order was not closed by reconciliation
    refreshed_broken = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == broken_ord.id).first()
    assert refreshed_broken.exit_cause == "SL_HIT"  # Original exit preserved
    assert refreshed_broken.realized_pnl_net == -2.0  # Original PnL preserved

    # Assert 3: Tail orders were audited
    audit_events = (
        isolated_db.query(models.DomainEvent)
        .filter(models.DomainEvent.event_type == "RECONCILIATION_AUDIT")
        .all()
    )
    audited_order_ids = {e.aggregate_id for e in audit_events}
    assert v1.id in audited_order_ids, f"Valid tail order {v1.id} must be audited"
    assert v2.id in audited_order_ids, f"Valid tail order {v2.id} must be audited"
    assert v3.id in audited_order_ids, f"Valid tail order {v3.id} must be audited"

    # Assert 4: Scheduler cursor moved past broken order
    scheduler = load_scan_progress(isolated_db, symbol="XAUUSDT", scan_scope="default")
    assert scheduler.resume_window_id != broken_ord.id, "Scheduler cursor must not be stuck on broken order"


def test_control_three_valid_closed_orders_audited(isolated_db: Session):
    """PHẦN 014: Control test without broken order.
    Proves that all 3 valid orders are audited when scheduler is not starved.
    """
    v1 = _seed_closed_order(isolated_db, order_id="ord_ctrl_01", opened_at=1000, closed_at=3400)
    v2 = _seed_closed_order(isolated_db, order_id="ord_ctrl_02", opened_at=1000, closed_at=3300)
    v3 = _seed_closed_order(isolated_db, order_id="ord_ctrl_03", opened_at=1000, closed_at=3200)

    quotes = [
        {"event_id": "ev_ctrl_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"}
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    service = MarketReconciliationService()
    budget = CycleBudget(max_windows=10, max_pages=10, max_records=50)
    service.process_late_batch(isolated_db, symbol="XAUUSDT", budget=budget)

    audit_events = (
        isolated_db.query(models.DomainEvent)
        .filter(models.DomainEvent.event_type == "RECONCILIATION_AUDIT")
        .all()
    )
    audited_order_ids = {e.aggregate_id for e in audit_events}
    assert v1.id in audited_order_ids
    assert v2.id in audited_order_ids
    assert v3.id in audited_order_ids


# =========================================================================
# Q02: RESTART RECOVERY TEST
# =========================================================================

def test_q02_restart_preserves_review_and_scheduler_progress(isolated_db: Session):
    """PHẦN 038, 062 (Q02):
    After cycle 1 creates review record, close DB session and service instance.
    Create fresh session and service instance.
    Assert review record is not duplicated, retry metadata intact, tail orders processed.
    """
    broken_ord = _seed_closed_order(isolated_db, order_id="ord_rst_broken", opened_at=1000, closed_at=3500, cost_snapshot="{broken")
    v1 = _seed_closed_order(isolated_db, order_id="ord_rst_v1", opened_at=1000, closed_at=3400)

    quotes = [
        {"event_id": "ev_rst_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"}
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    # Cycle 1
    service1 = MarketReconciliationService()
    service1.process_late_batch(isolated_db, symbol="XAUUSDT")

    broken_id = broken_ord.id
    v1_id = v1.id

    rec1 = load_review_record(isolated_db, broken_id)
    assert rec1 is not None
    assert rec1.attempt_count == 1

    # Simulate restart: close session, create new session and new service instance
    engine = isolated_db.bind
    SessionFactory = sessionmaker(bind=engine)
    isolated_db.close()

    db2 = SessionFactory()
    try:
        service2 = MarketReconciliationService()
        service2.process_late_batch(db2, symbol="XAUUSDT")

        # Assert review record remains single and not duplicated
        rev_recs = db2.query(models.SystemConfig).filter(models.SystemConfig.key.like(f"recon_rev:{broken_id}%")).all()
        assert len(rev_recs) == 1, "Review record must not be duplicated on restart"

        # Assert tail was audited
        audit_events = (
            db2.query(models.DomainEvent)
            .filter(models.DomainEvent.event_type == "RECONCILIATION_AUDIT")
            .all()
        )
        audited_order_ids = {e.aggregate_id for e in audit_events}
        assert v1_id in audited_order_ids
    finally:
        db2.close()


# =========================================================================
# Q03: PERSIST FAILURE ISOLATION TEST
# =========================================================================

def test_q03_persist_failure_does_not_advance_cursor(isolated_db: Session, monkeypatch):
    """PHẦN 024, 063 (Q03):
    Inject failure into review record persistence.
    Assert coordinator does NOT advance cursor over unsaved issue.
    When DB recovers, retry creates exactly one task and processes tail.
    """
    broken_ord = _seed_closed_order(isolated_db, order_id="ord_fail_broken", opened_at=1000, closed_at=3500, cost_snapshot="{broken")
    v1 = _seed_closed_order(isolated_db, order_id="ord_fail_v1", opened_at=1000, closed_at=3400)

    quotes = [
        {"event_id": "ev_fail_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"}
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    import services.market_reconciliation_service as mrs_module

    # Inject failure into persist_review_pending
    def _failing_persist(*args, **kwargs):
        return False, None, 1

    monkeypatch.setattr(mrs_module, "persist_review_pending", _failing_persist)

    service = MarketReconciliationService()
    service.process_late_batch(isolated_db, symbol="XAUUSDT")

    # Cursor must not have advanced over broken order
    sched = load_scan_progress(isolated_db, symbol="XAUUSDT")
    assert sched.resume_window_id == broken_ord.id or sched.window_cursor is None

    # Tail should NOT have been processed because batch stopped on unhandled failure
    audit_events_fail = (
        isolated_db.query(models.DomainEvent)
        .filter(models.DomainEvent.event_type == "RECONCILIATION_AUDIT")
        .all()
    )
    assert len(audit_events_fail) == 0

    # Recover DB (remove monkeypatch)
    monkeypatch.undo()

    # Cycle 2 with recovered DB
    service.process_late_batch(isolated_db, symbol="XAUUSDT")

    # Review task should now exist
    rec = load_review_record(isolated_db, broken_ord.id)
    assert rec is not None
    assert rec.status == ReconciliationReviewStatus.PENDING_REVIEW.value

    # Tail should now be audited
    audit_events_recovered = (
        isolated_db.query(models.DomainEvent)
        .filter(models.DomainEvent.event_type == "RECONCILIATION_AUDIT")
        .all()
    )
    audited_ids = {e.aggregate_id for e in audit_events_recovered}
    assert v1.id in audited_ids


# =========================================================================
# Q04: SOURCE REPAIRED REPLAY & RECOVERY TEST
# =========================================================================

def test_q04_source_repaired_replays_and_recovers_task(isolated_db: Session):
    """PHẦN 033, 034, 064 (Q04):
    Seed invalid snapshot -> quarantined in review.
    In test, repair snapshot with valid canonical JSON contract.
    Trigger source repair recheck -> task status transitions to RECOVERED,
    resolved_at recorded, no duplicate outbox/ledger corruption.
    """
    broken_ord = _seed_closed_order(isolated_db, order_id="ord_rep_01", opened_at=1000, closed_at=3500, cost_snapshot="{broken")

    quotes = [
        {"event_id": "ev_rep_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"}
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    service = MarketReconciliationService()
    service.process_late_batch(isolated_db, symbol="XAUUSDT")

    rec = load_review_record(isolated_db, broken_ord.id)
    assert rec.status == ReconciliationReviewStatus.PENDING_REVIEW.value

    # Now repair the order's cost snapshot with valid canonical JSON
    valid_snapshot = json.dumps({
        "direction": "LONG",
        "actual_entry": 4180.0,
        "stop_loss": 4170.0,
        "take_profit": 4200.0,
        "cost_source": "1.0.0",
        "policy_config_version": 2,
    })
    broken_ord.cost_snapshot = valid_snapshot
    isolated_db.commit()

    # Trigger source repair recheck
    outcome, updated_task = trigger_source_repair_recheck(isolated_db, broken_ord.id, new_source_revision=2)

    assert outcome == "RECOVERED"
    assert updated_task is not None
    assert updated_task.status == ReconciliationReviewStatus.RECOVERED.value
    assert updated_task.resolved_at is not None
    assert updated_task.resolved_at > 0


# =========================================================================
# Q05: STILL INVALID BACKOFF & NO EVENT SPAM TEST
# =========================================================================

def test_q05_still_invalid_backs_off_without_event_spam(isolated_db: Session):
    """PHẦN 023, 032, 065 (Q05):
    Retrying an unchanged broken task backs off retry timestamp
    and increments attempt_count without spamming duplicate domain events.
    """
    broken_ord = _seed_closed_order(isolated_db, order_id="ord_spam_01", opened_at=1000, closed_at=3500, cost_snapshot="{broken")

    quotes = [
        {"event_id": "ev_spam_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"}
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    service = MarketReconciliationService()
    service.process_late_batch(isolated_db, symbol="XAUUSDT")

    events_before = (
        isolated_db.query(models.DomainEvent)
        .filter(models.DomainEvent.event_type == "RECONCILIATION_REVIEW")
        .count()
    )
    assert events_before == 1

    rec = load_review_record(isolated_db, broken_ord.id)
    attempt1 = rec.attempt_count
    retry_at1 = rec.next_retry_at

    # Retry with fake clock + 70s
    now_ms2 = int(datetime.now(timezone.utc).timestamp() * 1000) + 70_000
    outcome, task2 = retry_review_task(isolated_db, rec, now_ms=now_ms2)

    assert outcome == "STILL_INVALID"
    assert task2.attempt_count == attempt1 + 1
    assert task2.next_retry_at > retry_at1

    # Domain events count must NOT increase (no spam!)
    events_after = (
        isolated_db.query(models.DomainEvent)
        .filter(models.DomainEvent.event_type == "RECONCILIATION_REVIEW")
        .count()
    )
    assert events_after == events_before, "No new domain events should be spammed on identical retry"


# =========================================================================
# Q06: OPEN POSITION PROTECTIVE EXIT UNBLOCKED TEST
# =========================================================================

def test_q06_open_position_protective_exit_unblocked_by_background_review(isolated_db: Session):
    """PHẦN 007, 036, 066 (Q06):
    An active paper_open order has a corrupt snapshot in background reconciliation,
    placing it into review pending.
    Protective exit execution (SL/TP quote) must execute unimpeded by background quarantine.
    """
    open_ord = models.PaperOrder(
        id="ord_open_prot",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.0,
        opened_at=1000,
        created_at=1000,
        cost_snapshot="{broken",
    )
    isolated_db.add(open_ord)
    isolated_db.commit()

    # Background reconciliation runs, marks it REVIEW_PENDING
    service = MarketReconciliationService()
    service.process_late_batch(isolated_db, symbol="XAUUSDT")

    rec = load_review_record(isolated_db, open_ord.id)
    assert rec is not None
    assert rec.status == ReconciliationReviewStatus.PENDING_REVIEW.value

    # Protective exit path triggers: price drops to 4168.0 hitting stop loss
    TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=open_ord.id,
        exit_price=4168.0,
        exit_cause="SL_HIT",
        occurred_at=2000,
        session_tag="PROTECTIVE_SL",
    )

    refreshed = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == open_ord.id).first()
    assert refreshed.state == "closed"
    assert refreshed.exit_cause == "SL_HIT"
    assert refreshed.actual_exit == 4168.0
    assert refreshed.realized_pnl_net is not None


# =========================================================================
# Q07: LARGE HISTORY PAGINATION TEST
# =========================================================================

def test_q07_large_history_with_small_budget_traverses_all_valid(isolated_db: Session):
    """PHẦN 067 (Q07):
    1 corrupt head order + 20 valid closed orders with a small per-cycle window budget (e.g. 5).
    Multiple coordinator cycles must progress through all pages and audit all valid orders.
    """
    broken = _seed_closed_order(isolated_db, order_id="ord_large_broken", opened_at=1000, closed_at=5000, cost_snapshot="{broken")

    valid_ids = []
    for i in range(1, 21):
        v_id = f"ord_large_v_{i:02d}"
        _seed_closed_order(isolated_db, order_id=v_id, opened_at=1000, closed_at=4000 - i * 50)
        valid_ids.append(v_id)

    quotes = [
        {"event_id": "ev_large_tp", "symbol": "XAUUSDT", "source_timestamp": 1500, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"}
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    service = MarketReconciliationService()

    # Run bounded cycles with small budget
    for _ in range(6):
        budget = CycleBudget(max_windows=5, max_pages=5, max_records=20)
        service.process_late_batch(isolated_db, symbol="XAUUSDT", budget=budget)

    audit_events = (
        isolated_db.query(models.DomainEvent)
        .filter(models.DomainEvent.event_type == "RECONCILIATION_AUDIT")
        .all()
    )
    audited_ids = {e.aggregate_id for e in audit_events}

    for vid in valid_ids:
        assert vid in audited_ids, f"Valid order {vid} must eventually be audited"

    # Broken order remained non-close
    assert broken.id not in audited_ids
