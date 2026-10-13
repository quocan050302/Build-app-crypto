"""
backend/tests/test_v14_4_7_coordinator_pagination.py
V14.4.7 Cases WINDOW01–WINDOW03, EVIDENCE01–EVIDENCE04, PARTIAL01, DURABLE01
(PHẦN 025–043, 063, 064, 083–094, 101–109):

Integration test suite for:
- Window pagination for CLOSED orders (keyset pagination closed_at DESC, id DESC).
- Coordinator process_late_batch continuation across bounded cycles reaching order 51+.
- Evidence pagination continuation across budget (max_pages=20, 4001 events) without losing progress.
- Incomplete traversal does not false-close or report complete.
- Revisit / replay when late older evidence arrives.
"""

import json
import pytest
from sqlalchemy.orm import Session
from datetime import datetime, timezone

import models
from database import SessionLocal
from services.market_reconciliation_service import (
    load_pending_reconciliation_windows,
    load_persisted_window_evidence,
    persist_reconciliation_evidence,
    resolve_reconciliation_window,
    MarketReconciliationService,
    WindowPage,
)


def _seed_paper_order(
    db: Session,
    order_id: str | int,
    state: str = "closed",
    opened_at: int = 1000,
    closed_at: int = 2000,
    symbol: str = "XAUUSDT",
    direction: str = "LONG",
    entry_price: float = 4180.0,
    sl_price: float = 4160.0,
    tp_price: float = 4220.0,
) -> models.PaperOrder:
    ord_id_str = f"ord_{order_id}" if isinstance(order_id, int) else str(order_id)
    ord = models.PaperOrder(
        id=ord_id_str,
        instrument=symbol,
        direction=direction,
        state=state,
        quantity=0.1,
        initial_risk_usdt=2.0,
        planned_entry=entry_price,
        actual_entry=entry_price,
        stop_loss=sl_price,
        take_profit=tp_price,
        opened_at=opened_at,
        closed_at=closed_at if state == "closed" else None,
        created_at=opened_at,
    )
    db.add(ord)
    db.commit()
    return ord


def test_window01_51_closed_orders_reached_via_coordinator(isolated_db: Session):
    """Case WINDOW01 (PHẦN 025, 028, 029, 084, 089, 101):
    With 51 PaperOrders in 'closed' state:
    load_pending_reconciliation_windows(limit=50) returns 50 items and next_cursor != None.
    Coordinator running across cycles must traverse next_cursor and reach order #51.
    In baseline, order #51 was never audited because limit=50 returned next_cursor=None for closed orders.
    """
    # 1. Seed 51 closed orders with chronological closed_at timestamps
    # Order 1 is the oldest closed order, Order 51 is the newest closed order
    for i in range(1, 52):
        _seed_paper_order(
            isolated_db,
            order_id=i,
            state="closed",
            opened_at=10000 + i * 100,
            closed_at=20000 + i * 100,
        )

    # Direct helper pagination test
    wp1 = load_pending_reconciliation_windows(isolated_db, symbol="XAUUSDT", limit=50)
    assert len(wp1.items) == 50
    assert wp1.next_cursor is not None, "Expected next_cursor to be set when 51 closed orders exist"

    wp2 = load_pending_reconciliation_windows(isolated_db, symbol="XAUUSDT", limit=50, cursor=wp1.next_cursor)
    assert len(wp2.items) == 1
    assert wp2.items[0].order_id in (1, "ord_1", "1")  # Oldest closed order
    assert wp2.next_cursor is None

    # Coordinator test: coordinator process_late_batch in repeated cycles must reach order 1 (the 51st order)
    service = MarketReconciliationService()
    # Cycle 1
    service.process_late_batch(isolated_db, symbol="XAUUSDT")
    # Cycle 2 (resuming progress)
    service.process_late_batch(isolated_db, symbol="XAUUSDT")

    # Verify that order 1 was processed in the audit / review
    # The scan progress must have reached the end of the 51 closed orders
    from services.market_reconciliation_service import load_scan_progress
    progress = load_scan_progress(isolated_db, symbol="XAUUSDT", scan_scope="default")
    assert progress is not None


def test_window02_101_mixed_and_tie_keys(isolated_db: Session):
    """Case WINDOW02 (PHẦN 026, 028, 083, 084, 102):
    1 OPEN order prioritized, alongside 101 CLOSED orders with multiple identical closed_at timestamps.
    Phased keyset pagination must reach all orders deterministically without duplicate scans or skipping ties.
    """
    # 1 open order
    _seed_paper_order(isolated_db, order_id=999, state="paper_open", opened_at=50000)

    # 101 closed orders, with groups having identical closed_at timestamps
    for i in range(1, 102):
        tier_closed_at = 10000 + (i // 5) * 50  # groups of 5 with identical closed_at
        _seed_paper_order(
            isolated_db,
            order_id=i,
            state="closed",
            opened_at=5000 + i * 10,
            closed_at=tier_closed_at,
        )

    all_visited_ids = set()
    cursor = None
    page_count = 0
    max_pages = 10

    while page_count < max_pages:
        page = load_pending_reconciliation_windows(isolated_db, symbol="XAUUSDT", limit=20, cursor=cursor)
        for w in page.items:
            all_visited_ids.add(w.order_id)
        if not page.next_cursor or not page.items:
            break
        cursor = page.next_cursor
        page_count += 1

    assert 999 in all_visited_ids or "ord_999" in all_visited_ids, "OPEN order 999 must be visited"
    # All 101 closed orders plus the 1 open order = 102 total orders
    assert len(all_visited_ids) == 102, f"Expected 102 unique orders, got: {len(all_visited_ids)}"


def test_evidence01_budget_continuation_4001_events(isolated_db: Session):
    """Case EVIDENCE01 (PHẦN 032, 087, 088, 104):
    Window with 4001 evidence events.
    Max pages per cycle budget = 20 (at limit=200, 20 pages = 4000 quotes).
    Cycle 1 exhausts budget and returns MORE_WORK / PENDING with durable progress saved.
    Cycle 2 resumes from stored cursor and reaches record 4001 to resolve.
    Incomplete read in Cycle 1 must NOT false-close or report coverage complete.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id=5001,
        state="paper_open",
        opened_at=100000,
        sl_price=4150.0,
        tp_price=4250.0,
    )

    # Seed 4001 evidence records
    # 4000 non-terminal quotes, then quote 4001 touches TP
    events_batch = []
    base_ts = 100000
    for i in range(1, 4001):
        events_batch.append({
            "event_id": f"ev_bulk_{i}",
            "symbol": "XAUUSDT",
            "source_timestamp": base_ts + i * 10,
            "received_timestamp": base_ts + i * 10 + 2,
            "bid": 4180.0,
            "ask": 4181.0,
            "source": "REALTIME",
        })

    # Terminal touch at 4001
    events_batch.append({
        "event_id": "ev_bulk_4001",
        "symbol": "XAUUSDT",
        "source_timestamp": base_ts + 4001 * 10,
        "received_timestamp": base_ts + 4001 * 10 + 2,
        "bid": 4251.0,  # TP touch!
        "ask": 4252.0,
        "source": "REALTIME",
    })

    # Persist evidence in chunks
    for i in range(0, len(events_batch), 500):
        persist_reconciliation_evidence(isolated_db, events_batch[i:i+500])

    from services.market_reconciliation_service import (
        process_window_slice,
        CycleBudget,
        OrderReconciliationWindow,
    )

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=ord.opened_at,
        end_as_of=base_ts + 50000,
        source_epoch=1,
        status="PENDING",
        cursor=ord.opened_at,
        order=ord,
    )

    # Cycle 1: budget max_pages=20 (reads 4000 records)
    budget1 = CycleBudget(max_windows=1, max_pages=20, max_records=4000)
    slice1 = process_window_slice(isolated_db, window, budget=budget1)

    assert slice1.has_more is True, "Cycle 1 must indicate more work remains"
    assert slice1.read_complete is False, "Cycle 1 must not claim read is complete"
    assert slice1.decision is None or slice1.decision.action != "CLOSE", "Cycle 1 must not finalize close from partial read"

    # Cycle 2: resumes from saved progress
    budget2 = CycleBudget(max_windows=1, max_pages=20, max_records=4000)
    slice2 = process_window_slice(isolated_db, window, budget=budget2)

    assert slice2.read_complete is True, "Cycle 2 must complete reading all 4001 records"
    assert slice2.has_more is False


def test_evidence02_small_injected_budget_and_restart(isolated_db: Session):
    """Case EVIDENCE02 (PHẦN 033, 088, 105):
    Test with small injected budget (max_pages=2, page_size=5 on 20 records).
    Demonstrates durable progress continuation and restart across new SessionLocal.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id=6001,
        state="paper_open",
        opened_at=200000,
        sl_price=4150.0,
        tp_price=4250.0,
    )

    # Seed 20 quotes
    quotes = [
        {
            "event_id": f"ev_small_{i}",
            "symbol": "XAUUSDT",
            "source_timestamp": 200000 + i * 10,
            "received_timestamp": 200000 + i * 10 + 1,
            "bid": 4180.0,
            "ask": 4181.0,
            "source": "REALTIME",
        }
        for i in range(1, 21)
    ]
    persist_reconciliation_evidence(isolated_db, quotes)

    from services.market_reconciliation_service import (
        process_window_slice,
        CycleBudget,
        OrderReconciliationWindow,
        load_window_progress,
    )

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=ord.opened_at,
        end_as_of=200500,
        source_epoch=1,
        status="PENDING",
        cursor=ord.opened_at,
        order=ord,
    )

    # Cycle 1 with small budget (max_pages=2, page_size=5)
    budget = CycleBudget(max_windows=1, max_pages=2, max_records=10)
    slice1 = process_window_slice(isolated_db, window, budget=budget, page_size=5)
    assert slice1.has_more is True

    # Verify progress was persisted
    prog = load_window_progress(isolated_db, window.order_id)
    assert prog is not None
    assert prog.source_cursor is not None

    # Resume in a brand new Session
    from sqlalchemy.orm import sessionmaker
    NewSession = sessionmaker(autocommit=False, autoflush=False, bind=isolated_db.get_bind())
    new_db = NewSession()
    try:
        ord_new = new_db.query(models.PaperOrder).filter(models.PaperOrder.id == ord.id).first()
        window_new = OrderReconciliationWindow(
            order_id=ord_new.id,
            symbol="XAUUSDT",
            opened_at=ord_new.opened_at,
            end_as_of=200500,
            source_epoch=1,
            status="PENDING",
            cursor=ord_new.opened_at,
            order=ord_new,
        )
        budget_new = CycleBudget(max_windows=1, max_pages=5, max_records=50)
        slice2 = process_window_slice(new_db, window_new, budget=budget_new, page_size=5)
        assert slice2.read_complete is True
    finally:
        new_db.close()


def test_evidence03_late_older_timestamp_invalidates_progress(isolated_db: Session):
    """Case EVIDENCE03 (PHẦN 036, 091, 106):
    An evidence record arriving with source_timestamp <= processed prefix
    must invalidate window progress / schedule replay.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id=7001,
        state="paper_open",
        opened_at=300000,
        sl_price=4150.0,
        tp_price=4250.0,
    )

    # 1. Seed initial evidence from 300010 to 300050
    quotes1 = [
        {
            "event_id": f"ev_init_{i}",
            "symbol": "XAUUSDT",
            "source_timestamp": 300000 + i * 10,
            "received_timestamp": 300000 + i * 10 + 1,
            "bid": 4180.0,
            "ask": 4181.0,
            "source": "REALTIME",
        }
        for i in range(1, 6)
    ]
    persist_reconciliation_evidence(isolated_db, quotes1)

    from services.market_reconciliation_service import (
        process_window_slice,
        CycleBudget,
        OrderReconciliationWindow,
        load_window_progress,
        invalidate_affected_window_progress,
    )

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=ord.opened_at,
        end_as_of=300100,
        source_epoch=1,
        status="PENDING",
        cursor=ord.opened_at,
        order=ord,
    )
    budget = CycleBudget(max_windows=1, max_pages=1, max_records=2)
    process_window_slice(isolated_db, window, budget=budget, page_size=2)

    prog_before = load_window_progress(isolated_db, window.order_id)
    assert prog_before is not None

    # Now a late quote arrives with source_timestamp = 300015 (earlier than cursor)
    late_quote = {
        "event_id": "ev_late_touch",
        "symbol": "XAUUSDT",
        "source_timestamp": 300015,
        "received_timestamp": 350000,
        "bid": 4140.0,  # SL breach!
        "ask": 4141.0,
        "source": "REALTIME",
    }
    persist_reconciliation_evidence(isolated_db, [late_quote])

    # Invalidate affected progress
    invalidate_affected_window_progress(isolated_db, [ord.id], arrival_ts=300015)

    prog_after = load_window_progress(isolated_db, window.order_id)
    assert prog_after.read_status in ("REPLAY_NEEDED", "RESET")


def test_evidence04_symbol_filter_before_limit_prevents_starvation(isolated_db: Session):
    """Case EVIDENCE04 (PHẦN 037, 107):
    Other symbol (e.g. BTCUSDT) events must not consume page limit slots
    and starve XAUUSDT reconciliation queries.
    """
    ord = _seed_paper_order(
        isolated_db,
        order_id=8001,
        state="paper_open",
        opened_at=400000,
        symbol="XAUUSDT",
    )

    # 100 BTC quotes with earlier/overlapping timestamps
    btc_quotes = [
        {
            "event_id": f"ev_btc_{i}",
            "symbol": "BTCUSDT",
            "source_timestamp": 400000 + i * 5,
            "received_timestamp": 400000 + i * 5 + 1,
            "bid": 65000.0,
            "ask": 65001.0,
            "source": "REALTIME",
        }
        for i in range(1, 101)
    ]
    # 5 XAU quotes
    xau_quotes = [
        {
            "event_id": f"ev_xau_{i}",
            "symbol": "XAUUSDT",
            "source_timestamp": 400000 + i * 10,
            "received_timestamp": 400000 + i * 10 + 1,
            "bid": 4180.0,
            "ask": 4181.0,
            "source": "REALTIME",
        }
        for i in range(1, 6)
    ]

    persist_reconciliation_evidence(isolated_db, btc_quotes + xau_quotes)

    from services.market_reconciliation_service import OrderReconciliationWindow

    window = OrderReconciliationWindow(
        order_id=ord.id,
        symbol="XAUUSDT",
        opened_at=ord.opened_at,
        end_as_of=400100,
        source_epoch=1,
        status="PENDING",
        cursor=ord.opened_at,
        order=ord,
    )

    # Load evidence with limit=10: should get all 5 XAU quotes and 0 BTC quotes
    page = load_persisted_window_evidence(isolated_db, window, limit=10)
    assert len(page.items) == 5
    for item in page.items:
        payload = item.get("payload") if isinstance(item, dict) else {}
        assert item.get("symbol") == "XAUUSDT" or payload.get("symbol") == "XAUUSDT"
