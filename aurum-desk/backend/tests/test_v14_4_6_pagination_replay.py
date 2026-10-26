"""
backend/tests/test_v14_4_6_pagination_replay.py
V14.4.6 Cases PAGE01–PAGE03 (PHẦN 036–045, 091–098, 111–113):
Keyset pagination for reconciliation evidence (reaching event 201+), tie-breaker handling,
window pagination (reaching window 51+), and pre-LIMIT SQL symbol filtering.
"""

import json
import pytest
from sqlalchemy.orm import Session

import models
from services.market_reconciliation_service import (
    load_pending_reconciliation_windows,
    load_persisted_window_evidence,
    market_reconciliation_service,
    OrderReconciliationWindow,
)


def _seed_late_evidence(db: Session, ev_id: str, symbol: str, ts: int, bid: float, ask: float):
    payload = {
        "evidence_id": ev_id,
        "symbol": symbol,
        "source_timestamp": ts,
        "bid": bid,
        "ask": ask,
        "last": bid,
        "epoch": 1,
        "source": "RECON_TEST",
    }
    ev = models.DomainEvent(
        event_id=f"late_ev_{ev_id}",
        sequence=1,
        schema_version="1.0.0",
        event_type="LATE_RECONCILIATION_EVIDENCE",
        aggregate_id=ev_id,
        aggregate_version=1,
        occurred_at=ts,
        published_at=ts,
        payload=json.dumps(payload),
    )
    db.add(ev)


def test_page01_event_201_reachable_via_pagination(isolated_db: Session):
    """Case PAGE01 (PHẦN 036, 039, 111):
    First 200 events are non-terminal quotes; 201st event is a terminal quote (SL hit).
    Reconciliation evidence loader with keyset cursor and coordinator must traverse beyond 200
    so that event 201 is reached.
    """
    order = models.PaperOrder(
        id="ord_page01_01",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_entry=4180.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        created_at=1000,
        opened_at=1000,
    )
    isolated_db.add(order)

    # Ingest 200 non-terminal events (price 4180.0 - 4185.0)
    for i in range(1, 201):
        _seed_late_evidence(isolated_db, f"ev_nonterm_{i:03d}", "XAUUSDT", 1000 + i * 10, 4181.0, 4181.5)

    # 201st event is terminal SL hit (bid drops to 4169.0)
    _seed_late_evidence(isolated_db, "ev_term_201", "XAUUSDT", 1000 + 201 * 10, 4169.0, 4169.5)
    isolated_db.commit()

    window = OrderReconciliationWindow(
        order_id=order.id,
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=10000,
        order=order,
    )

    # Page 1: first 200 items
    p1 = load_persisted_window_evidence(isolated_db, window, as_of_ms=10000, limit=200)
    assert len(p1.items) == 200
    assert p1.next_cursor is not None

    # Page 2: must reach event 201!
    p2 = load_persisted_window_evidence(isolated_db, window, as_of_ms=10000, limit=200, cursor=p1.next_cursor)
    assert len(p2.items) >= 1
    assert any(e.get("evidence_id") == "ev_term_201" for e in p2.items)


def test_page02_tie_timestamps_traversed_without_duplicates(isolated_db: Session):
    """Case PAGE02 (PHẦN 040, 042, 112):
    Multiple events with exact same timestamp but different IDs must all be traversed
    without skipping or infinite loops using keyset (timestamp, event_id).
    """
    same_ts = 5000
    # Seed 30 events with same timestamp
    for i in range(30):
        _seed_late_evidence(isolated_db, f"ev_tie_{i:02d}", "XAUUSDT", same_ts, 4180.0, 4180.5)
    isolated_db.commit()

    window = OrderReconciliationWindow(
        order_id="ord_tie",
        symbol="XAUUSDT",
        opened_at=1000,
        end_as_of=10000,
    )

    # Read in pages of 10
    collected_ids = []
    cursor = None
    for _ in range(5):
        page = load_persisted_window_evidence(isolated_db, window, as_of_ms=10000, limit=10, cursor=cursor)
        for it in page.items:
            collected_ids.append(it["evidence_id"])
        if not page.next_cursor or not page.items:
            break
        cursor = page.next_cursor

    assert len(collected_ids) == 30
    assert len(set(collected_ids)) == 30, "No duplicate evidence IDs across pages with identical timestamps"


def test_page03_window_pagination_and_symbol_filter(isolated_db: Session):
    """Case PAGE03 (PHẦN 037, 038, 113):
    1. 60 open orders: load_pending_reconciliation_windows must reach window 51+ using cursor.
    2. Interleaved BTCUSDT events must not starve XAUUSDT events prior to LIMIT.
    """
    # 1. Seed 60 open orders for XAUUSDT
    for i in range(1, 61):
        ord_i = models.PaperOrder(
            id=f"ord_xau_{i:02d}",
            instrument="XAUUSDT",
            direction="LONG",
            state="paper_open",
            planned_entry=4180.0,
            stop_loss=4170.0,
            take_profit=4200.0,
            actual_entry=4180.0,
            quantity=0.1,
            initial_risk_usdt=1.0,
            created_at=1000 + i * 100,
            opened_at=1000 + i * 100,
        )
        isolated_db.add(ord_i)
    isolated_db.commit()

    wp1 = load_pending_reconciliation_windows(isolated_db, symbol="XAUUSDT", limit=50)
    assert len(wp1.items) == 50
    assert wp1.next_cursor is not None

    wp2 = load_pending_reconciliation_windows(isolated_db, symbol="XAUUSDT", limit=50, cursor=wp1.next_cursor)
    assert len(wp2.items) == 10
    assert wp2.items[0].order_id == "ord_xau_51"

    # 2. Seed 200 events for BTCUSDT and 10 events for XAUUSDT
    for b in range(200):
        _seed_late_evidence(isolated_db, f"ev_btc_{b:03d}", "BTCUSDT", 2000 + b, 65000.0, 65001.0)
    for x in range(10):
        _seed_late_evidence(isolated_db, f"ev_xau_filter_{x:02d}", "XAUUSDT", 2050 + x, 4180.0, 4180.5)
    isolated_db.commit()

    xau_win = OrderReconciliationWindow(
        order_id="ord_filter_test",
        symbol="XAUUSDT",
        opened_at=2000,
        end_as_of=5000,
    )
    ev_page = load_persisted_window_evidence(isolated_db, xau_win, as_of_ms=5000, limit=50)
    # BTCUSDT must NOT starve XAUUSDT slots: all 10 XAUUSDT items must be present in the first page!
    xau_items = [it for it in ev_page.items if it.get("symbol") == "XAUUSDT"]
    assert len(xau_items) == 10
