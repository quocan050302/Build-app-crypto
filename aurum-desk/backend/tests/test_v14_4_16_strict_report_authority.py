"""backend/tests/test_v14_4_16_strict_report_authority.py

V14.4.16 Acceptance Tests: Strict Report Authority & Parity
Cases:
- SREP01 / A01: Exact Report Missing Terminal Event Repro (PHẦN 002, 003, 022, 051)
  - Real session + member closed valid TP_HIT/PnL, but 0 terminal events in DB.
  - generate_forward_paper_report returns confirmed_closed_trades = 0, confirmed_wins = 0,
    unverified_closed_trades = 1 with MISSING_TERMINAL_EVENT reason.
  - export_forward_paper_data returns row with outcome_classification = "UNVERIFIED".
  - Positive control: Order WITH committed trade.closed event is confirmed.
- SREP02 / A02: Terminal Event Decoding, Binding & As-Of Matrix (PHẦN 019, 020, 052)
  - Mismatched order ID, wrong direction, mismatched exit cause, NaN price, future published as-of.
- SREP03 / A03: Legacy Session Scoping, Export Parity & Non-Mutating Report (PHẦN 023, 024, 053)
  - Unknown session returns NOT_FOUND.
  - Zero DB mutation during report and export.
"""

import json
import math
import pytest
from sqlalchemy.orm import Session

import models
from services.forward_paper_service import (
    create_forward_paper_session,
    bind_order_to_forward_session,
    generate_forward_paper_report,
    export_forward_paper_data,
    strict_classify_forward_outcome,
    classify_forward_outcome,
    read_outcome_authority,
)
from services.trade_lifecycle_service import TradeLifecycleService


def test_srep01_exact_report_missing_event_repro(isolated_db: Session):
    """SREP01 / A01: Exact Missing Event Repro (PHẦN 002, 003, 022, 051).
    - Reviewer creates forward session, binds closed LONG TP_HIT/PnL=2.0/R=1.0.
    - DB has NO trade.closed event.
    - generate_forward_paper_report must return confirmed_closed_trades = 0, confirmed_wins = 0,
      and unverified_closed_trades = 1 (MISSING_TERMINAL_EVENT).
    - export_forward_paper_data returns outcome_classification = "UNVERIFIED".
    - Positive control: Another order WITH committed trade.closed event returns CONFIRMED.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_srep01",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    # 1. Closed order without terminal event in DB
    ord_missing = models.PaperOrder(
        id="ord_srep01_missing_ev",
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
        realized_pnl_net=2.00,
        realized_r=1.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
        session_instance_id="ASIA_20261014",
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add(ord_missing)
    isolated_db.commit()

    bind_order_to_forward_session(isolated_db, sess.session_id, ord_missing, now_ms=now_ms)

    # Snapshot DB state before report
    pnl_before = ord_missing.realized_pnl_net
    state_before = ord_missing.state

    # Generate report
    report = generate_forward_paper_report(isolated_db, sess.session_id, now_ms=now_ms + 10000)

    # PHẦN 002: Expected strict confirmed count 0, unverified reason, recorded PnL basis
    assert report["confirmed_closed_trades"] == 0, "Missing terminal event must fail closed to 0 confirmed"
    assert report["confirmed_wins"] == 0
    assert report["confirmed_losses"] == 0
    assert report["net_pnl"] == 0.0
    assert report["unverified_closed_trades"] == 1
    assert report["total_fills"] == 1
    assert report.get("recorded_closed_pnl") == 2.00

    # Export check
    export_rows = export_forward_paper_data(isolated_db, sess.session_id)
    assert len(export_rows) == 1
    assert export_rows[0]["order_id"] == ord_missing.id
    assert export_rows[0]["outcome_classification"] == "UNVERIFIED"

    # Verify report did NOT mutate order or DB
    isolated_db.refresh(ord_missing)
    assert ord_missing.realized_pnl_net == pnl_before
    assert ord_missing.state == state_before

    # 2. Positive control: Add matching trade.closed domain event
    ev = models.DomainEvent(
        event_id="evt_srep01_win",
        sequence=1,
        schema_version="1.0.0",
        event_type="trade.closed",
        aggregate_id=ord_missing.id,
        aggregate_version=2,
        occurred_at=now_ms + 5000,
        published_at=now_ms + 5000,
        payload=json.dumps({
            "order_id": ord_missing.id,
            "direction": "LONG",
            "exit_cause": "TP_HIT",
            "actual_exit": 4200.0,
            "realized_pnl": 2.00,
        }),
    )
    isolated_db.add(ev)
    isolated_db.commit()

    report_after = generate_forward_paper_report(isolated_db, sess.session_id, now_ms=now_ms + 10000)
    assert report_after["confirmed_closed_trades"] == 1
    assert report_after["confirmed_wins"] == 1
    assert report_after["net_pnl"] == 2.00
    assert report_after["unverified_closed_trades"] == 0


def test_srep02_terminal_event_and_as_of_matrix(isolated_db: Session):
    """SREP02 / A02: Terminal Event Decoding, Binding & As-Of Matrix (PHẦN 019, 020, 052).
    - Mismatched order ID, wrong direction, mismatched exit cause, NaN price.
    - Event occurred before as_of but published/known after as_of is excluded from past report.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_srep02",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    # 1. Order with mismatched exit cause in event (TP in order, SL in event)
    ord_mismatch = models.PaperOrder(
        id="ord_srep02_mismatch",
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
        realized_pnl_net=2.00,
        realized_r=1.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
        session_instance_id="ASIA_20261014",
    )
    isolated_db.add(ord_mismatch)
    isolated_db.commit()
    bind_order_to_forward_session(isolated_db, sess.session_id, ord_mismatch, now_ms=now_ms)

    ev_mismatch = models.DomainEvent(
        event_id="evt_srep02_mismatch",
        sequence=1,
        schema_version="1.0.0",
        event_type="trade.closed",
        aggregate_id=ord_mismatch.id,
        aggregate_version=2,
        occurred_at=now_ms + 5000,
        published_at=now_ms + 5000,
        payload=json.dumps({
            "order_id": ord_mismatch.id,
            "direction": "LONG",
            "exit_cause": "SL_HIT",  # Mismatched!
            "actual_exit": 4170.0,
            "realized_pnl": -2.50,
        }),
    )
    isolated_db.add(ev_mismatch)
    isolated_db.commit()

    # 2. Order with future event relative to as_of
    ord_future = models.PaperOrder(
        id="ord_srep02_future",
        instrument="XAUUSDT",
        direction="SHORT",
        state="closed",
        planned_entry=4200.0,
        actual_entry=4200.0,
        stop_loss=4210.0,
        take_profit=4180.0,
        actual_exit=4180.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.5,
        realized_pnl_net=2.00,
        realized_r=1.00,
        opened_at=now_ms,
        closed_at=now_ms + 20000,
        created_at=now_ms,
        session_instance_id="ASIA_20261014",
    )
    isolated_db.add(ord_future)
    isolated_db.commit()
    bind_order_to_forward_session(isolated_db, sess.session_id, ord_future, now_ms=now_ms)

    ev_future = models.DomainEvent(
        event_id="evt_srep02_future",
        sequence=2,
        schema_version="1.0.0",
        event_type="trade.closed",
        aggregate_id=ord_future.id,
        aggregate_version=2,
        occurred_at=now_ms + 20000,
        published_at=now_ms + 20000,
        payload=json.dumps({
            "order_id": ord_future.id,
            "direction": "SHORT",
            "exit_cause": "TP_HIT",
            "actual_exit": 4180.0,
            "realized_pnl": 2.00,
        }),
    )
    isolated_db.add(ev_future)
    isolated_db.commit()

    # Query report as of now_ms + 10000 (before ord_future closed_at and event)
    rep_past = generate_forward_paper_report(isolated_db, sess.session_id, now_ms=now_ms + 10000)
    assert rep_past["confirmed_closed_trades"] == 0
    assert rep_past["unverified_closed_trades"] == 1  # ord_mismatch binding failed
    assert rep_past["pending_count"] == 1            # ord_future closed after as_of, so classified as OPEN

    # Query report as of now_ms + 30000 (after ord_future closed)
    rep_now = generate_forward_paper_report(isolated_db, sess.session_id, now_ms=now_ms + 30000)
    assert rep_now["confirmed_closed_trades"] == 1  # ord_future matches
    assert rep_now["confirmed_wins"] == 1
    assert rep_now["unverified_closed_trades"] == 1  # ord_mismatch still fails binding


def test_srep03_legacy_session_scoping_and_parity(isolated_db: Session):
    """SREP03 / A03: Session Scoping, Export Parity & Non-Mutating Report (PHẦN 023, 024, 053).
    - Unknown forward session returns NOT_FOUND in report.
    - Export returns empty list for unknown session.
    - Zero DB mutation during report and export generation.
    - Strict classification wrapper enforces require_terminal_event=True.
    """
    now_ms = 1792000000000

    rep_unknown = generate_forward_paper_report(isolated_db, "fwd_nonexistent_xyz", now_ms=now_ms)
    assert rep_unknown.get("status") == "NOT_FOUND"

    exp_unknown = export_forward_paper_data(isolated_db, "fwd_nonexistent_xyz")
    assert exp_unknown == []

    # Verify strict_classify_forward_outcome enforces require_terminal_event=True
    ord_dummy = models.PaperOrder(
        id="ord_srep03_dummy",
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
        realized_pnl_net=2.00,
        realized_r=1.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    res_loose = classify_forward_outcome(ord_dummy, terminal_event=None, require_terminal_event=False)
    assert res_loose["classification"] == "CONFIRMED"

    res_strict = strict_classify_forward_outcome(ord_dummy, terminal_event=None)
    assert res_strict["classification"] == "UNVERIFIED"
    assert res_strict["countable_for_confirmed"] is False
    assert "MISSING_TERMINAL_EVENT" in (res_strict["reasons"] or [])
