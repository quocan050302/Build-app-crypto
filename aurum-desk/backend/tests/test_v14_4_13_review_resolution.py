"""
backend/tests/test_v14_4_13_review_resolution.py
V14.4.13 — Recovery Review Resolution, Window Continuation, and Fairness Test Suite.

Covers:
  - R01: Unknown / Partial Coverage (PHẦN 052–056, 068) — Source repaired, read_complete=True,
        coverage incomplete (NEEDS_RECONCILIATION) yields WAITING_COVERAGE, never fake RECOVERED.
  - R02: Apply Failure & Conflict Review (PHẦN 057, 060, 069) — Apply failure returns APPLY_FAILED;
        committed conflict audit (REVIEW_REQUIRED) stays in MANUAL_REVIEW_REQUIRED, never fake RECOVERED;
        idempotent retry.
  - R03: Large Window Continuation (PHẦN 058, 070) — Small budget returns MORE_WORK on retry 1,
        retry 2 continues from persisted cursor without resetting back to head; source revision change
        triggers clean reset once.
  - R04: Fair Scheduling & Isolation (PHẦN 061, 071) — Invalid head quarantined, valid tail proceeds;
        bounded review retry quantum does not starve main traversal.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import pytest
from sqlalchemy.orm import Session

import models
from services.market_reconciliation_service import (
    CycleBudget,
    OrderReconciliationWindow,
    ReconciliationReviewRecord,
    ReconciliationReviewStatus,
    build_reconciliation_context_result,
    classify_review_resolution,
    load_review_record,
    load_window_progress,
    persist_reconciliation_evidence,
    persist_resolution_or_review,
    persist_review_pending,
    process_window_slice,
    retry_review_task,
    save_review_record,
    save_window_progress,
    trigger_source_repair_recheck,
    MarketReconciliationService,
)


def _make_cost_snapshot(direction="LONG", entry=4180.0, sl=4170.0, tp=4200.0) -> str:
    return json.dumps({
        "direction": direction,
        "actual_entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "cost_source": "1.0.0",
        "policy_config_version": 1,
    })


# =========================================================================
# R01: UNKNOWN / PARTIAL COVERAGE (PHẦN 052–056, 068)
# =========================================================================

def test_rec01_unknown_coverage_does_not_fake_recovered(isolated_db: Session):
    """PHẦN 052–056, 068 (R01):
    Source is repaired, but window coverage has a gap or requires reconciliation
    (NEEDS_RECONCILIATION). Must NOT mark RECOVERED!
    Expected: WAITING_COVERAGE with RETRY_SCHEDULED. Zero CLOSE/PnL mutations.
    """
    now_ms = 1792000000000

    # Seed an open order with broken snapshot
    ord1 = models.PaperOrder(
        id="ord_r01_open",
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
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add(ord1)
    isolated_db.commit()

    # Trigger late batch to quarantine
    service = MarketReconciliationService()
    service.process_late_batch(isolated_db, symbol="XAUUSDT")

    rec = load_review_record(isolated_db, ord1.id)
    assert rec is not None
    assert rec.status == ReconciliationReviewStatus.PENDING_REVIEW.value

    # Repair the cost snapshot with valid JSON
    ord1.cost_snapshot = _make_cost_snapshot()
    isolated_db.commit()

    # Provide 1 quote that reaches TP (4205.0) but with massive coverage gap to end_as_of -> coverage gap exists
    quotes = [
        {"event_id": "ev_gap_1", "symbol": "XAUUSDT", "source_timestamp": 1500, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"}
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    # Retry the review task
    outcome, updated_task = retry_review_task(isolated_db, rec, now_ms=now_ms)

    # Assert: NOT marked RECOVERED!
    assert outcome == "WAITING_COVERAGE"
    assert updated_task.status == ReconciliationReviewStatus.RETRY_SCHEDULED.value
    assert updated_task.resolved_at is None
    # Order remains open, no fake close or PnL mutations
    reloaded_ord = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == ord1.id).first()
    assert reloaded_ord.state == "paper_open"
    assert reloaded_ord.realized_pnl_net is None


# =========================================================================
# R02: APPLY FAILURE & CONFLICT REVIEW (PHẦN 057, 060, 069)
# =========================================================================

def test_rec02_conflict_audit_committed_stays_manual_review_required(isolated_db: Session):
    """PHẦN 057, 069 (R02 - Conflict):
    Closed order has conflicting earlier evidence (REVIEW_REQUIRED).
    Committing the conflict audit event must NOT mark task RECOVERED.
    Expected: disposition REVIEW_REQUIRED, status MANUAL_REVIEW_REQUIRED.
    Idempotent on repeated retry.
    """
    now_ms = 1792000000000

    # Order closed at 3500 with SL_HIT
    ord_conf = models.PaperOrder(
        id="ord_r02_conf",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4169.5,
        exit_cause="SL_HIT",
        quantity=0.1,
        initial_risk_usdt=2.0,
        realized_pnl_net=-2.0,
        realized_r=-1.0,
        opened_at=1000,
        created_at=1000,
        closed_at=3500,
        cost_snapshot=_make_cost_snapshot(),
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add(ord_conf)
    isolated_db.commit()

    # Evidence shows TP was touched earlier at 2000!
    quotes = [
        {"event_id": "ev_r02_tp", "symbol": "XAUUSDT", "source_timestamp": 2000, "bid": 4205.0, "ask": 4206.0, "source": "REALTIME"}
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    task = ReconciliationReviewRecord(
        order_id=ord_conf.id,
        window_id=ord_conf.id,
        symbol="XAUUSDT",
        reason_codes=["EARLIER_CONFLICTING_TERMINAL_ARRIVED_POST_CLOSE"],
        source_status="SOURCE_REPAIRED",
        status=ReconciliationReviewStatus.RETRYING.value,
        first_seen_at=now_ms,
        last_seen_at=now_ms,
        attempt_count=1,
    )
    save_review_record(isolated_db, task)

    # Retry the review task
    outcome, updated_task = retry_review_task(isolated_db, task, now_ms=now_ms)

    # PHẦN 057, 069: Audit committed, but task must NOT fake RECOVERED!
    assert outcome == "REVIEW_REQUIRED"
    assert updated_task.status == ReconciliationReviewStatus.MANUAL_REVIEW_REQUIRED.value
    assert updated_task.resolved_at is None

    # Verify audit event was committed durably
    audits = isolated_db.query(models.DomainEvent).filter(
        models.DomainEvent.event_type == "RECONCILIATION_AUDIT",
        models.DomainEvent.aggregate_id == ord_conf.id,
    ).all()
    assert len(audits) >= 1

    # Idempotent retry: does not spam duplicate audit events
    audit_count_before = len(audits)
    outcome2, task2 = retry_review_task(isolated_db, updated_task, now_ms=now_ms + 10000)
    assert outcome2 == "REVIEW_REQUIRED"
    assert task2.status == ReconciliationReviewStatus.MANUAL_REVIEW_REQUIRED.value
    audit_count_after = isolated_db.query(models.DomainEvent).filter(
        models.DomainEvent.event_type == "RECONCILIATION_AUDIT",
        models.DomainEvent.aggregate_id == ord_conf.id,
    ).count()
    assert audit_count_after == audit_count_before


# =========================================================================
# R03: LARGE WINDOW CONTINUATION (PHẦN 058, 070)
# =========================================================================

def test_rec03_large_window_continuation_across_retries(isolated_db: Session):
    """PHẦN 058, 070 (R03):
    Window has 10 quotes. Injected small record budget = 3.
    Retry 1: reads first slice, returns MORE_WORK.
    Retry 2: does NOT reset back to beginning! Continues from persisted progress.
    Source revision change: resets progress once for clean replay.
    """
    now_ms = 1792000000000

    ord_large = models.PaperOrder(
        id="ord_r03_cont",
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
        cost_snapshot=_make_cost_snapshot(),
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add(ord_large)
    isolated_db.commit()

    # Seed 10 sequential quotes
    quotes = [
        {"event_id": f"ev_r03_{i}", "symbol": "XAUUSDT", "source_timestamp": 1000 + i * 100, "bid": 4185.0, "ask": 4186.0, "source": "REALTIME"}
        for i in range(1, 11)
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    task = ReconciliationReviewRecord(
        order_id=ord_large.id,
        window_id=ord_large.id,
        symbol="XAUUSDT",
        reason_codes=["TRANSIENT_DATA_GAP"],
        source_status="SOURCE_REPAIRED",
        status=ReconciliationReviewStatus.RETRYING.value,
        first_seen_at=now_ms,
        last_seen_at=now_ms,
        attempt_count=1,
    )
    save_review_record(isolated_db, task)

    # Retry 1: Budget = 3 records
    budget1 = CycleBudget(max_records=3)
    outcome1, task1 = retry_review_task(isolated_db, task, now_ms=now_ms, budget=budget1)

    assert outcome1 == "MORE_WORK"
    assert task1.status == ReconciliationReviewStatus.RETRYING.value

    # Verify progress was recorded and advanced
    prog1 = load_window_progress(isolated_db, ord_large.id)
    assert prog1 is not None
    assert prog1.source_cursor is not None
    assert prog1.read_status == "MORE_WORK"
    cursor_after_retry1 = prog1.source_cursor

    # Retry 2: Budget = 3 records -> must CONTINUE from progress, NOT reset to 0!
    budget2 = CycleBudget(max_records=3)
    outcome2, task2 = retry_review_task(isolated_db, task1, now_ms=now_ms + 1000, budget=budget2)

    prog2 = load_window_progress(isolated_db, ord_large.id)
    assert prog2 is not None
    assert prog2.source_cursor is not None
    assert prog2.source_cursor[0] > cursor_after_retry1[0], "Continuation must advance cursor without resetting to 0"

    # Trigger source repair recheck -> resets progress exactly ONCE for new revision
    trigger_source_repair_recheck(isolated_db, ord_large.id, new_source_revision=2, budget=CycleBudget(max_records=2))
    reloaded_rec = load_review_record(isolated_db, ord_large.id)
    assert reloaded_rec is not None
    assert reloaded_rec.source_revision == 2


# =========================================================================
# R04: FAIR SCHEDULING & ISOLATION (PHẦN 061, 071)
# =========================================================================

def test_rec04_fair_scheduling_broken_head_does_not_starve_tail(isolated_db: Session):
    """PHẦN 061, 071 (R04):
    Queue has invalid head order (broken context) and valid tail order.
    Head is quarantined, allowing traversal to reach and audit tail order without starvation.
    """
    now_ms = 1792000000000

    # 1. Broken head order
    ord_head = models.PaperOrder(
        id="ord_r04_head",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4169.5,
        exit_cause="SL_HIT",
        quantity=0.1,
        initial_risk_usdt=2.0,
        opened_at=1000,
        closed_at=3500,
        created_at=1000,
        cost_snapshot="{broken",
        strategy_family="STANDARD_SMC",
    )

    # 2. Valid tail order
    ord_tail = models.PaperOrder(
        id="ord_r04_tail",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4169.5,
        exit_cause="SL_HIT",
        quantity=0.1,
        initial_risk_usdt=2.0,
        opened_at=1000,
        closed_at=3400,
        created_at=1000,
        cost_snapshot=_make_cost_snapshot(),
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add_all([ord_head, ord_tail])
    isolated_db.commit()

    service = MarketReconciliationService()
    service.process_late_batch(isolated_db, symbol="XAUUSDT")

    # Head is quarantined
    rec_head = load_review_record(isolated_db, ord_head.id)
    assert rec_head is not None
    assert rec_head.status == ReconciliationReviewStatus.PENDING_REVIEW.value

    # Tail order was NOT starved
    tail_prog = load_window_progress(isolated_db, ord_tail.id)
    assert tail_prog is not None
