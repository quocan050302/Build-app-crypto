"""
V14.2 - isolated backend for the Playwright browser tests (frontend/tests/e2e).

* temp SQLite DB (never the runtime DB), TESTING=1
* NO background loops (no Bitget WS, no collector, no Telegram, no strategy loop): the app lifespan is
  replaced by one that only pushes a fresh QUOTE_UPDATE over /ws each second, so nothing here can reach
  the network or send a notification
* a tiny feeder thread keeps `collector_service.latest_ticker` fresh (bid 2650.0 / ask 2650.2)
* test-only controls live under /__e2e/* and exist ONLY in this launcher (not in production main.py)

Run:  python backend/tests/e2e_server.py --port 8765
"""
import argparse
import asyncio
import os
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

_tmp = tempfile.mkdtemp(prefix="aurum_e2e_")
os.environ["AURUM_DB_PATH"] = os.path.join(_tmp, "e2e_aurum.db")
os.environ["TESTING"] = "1"

import uvicorn  # noqa: E402
from fastapi import Body  # noqa: E402

import crud  # noqa: E402
import models  # noqa: E402
from database import SessionLocal  # noqa: E402
from main import app  # noqa: E402
from services.clock import FakeClock  # noqa: E402
from services.collector_service import collector_service  # noqa: E402
from services.execution_coordinator import execution_coordinator  # noqa: E402

BID, ASK = 2650.0, 2650.2
PLANS = {
    # good plan: net R:R well above 2.0
    "LONG": dict(entry=2650.0, sl=2640.0, tp=2685.0),
    "SHORT": dict(entry=2650.0, sl=2660.0, tp=2615.0),
    # the "photo" shape (risk 5, reward 0.29) scaled to the seeded candle range
    "TIGHT_SHORT": dict(entry=2650.29, sl=2655.29, tp=2650.00),
}


async def _ws_quote_feeder():
    """Push a fresh QUOTE_UPDATE to the browser over the real /ws endpoint every second."""
    from services.market_broadcaster import market_broadcaster
    seq = 0
    while True:
        await asyncio.sleep(1.0)
        seq += 1
        now = int(time.time() * 1000)
        await market_broadcaster._send_to_all({
            "protocol_version": "7.1.0", "type": "QUOTE_UPDATE", "symbol": "XAUUSDT",
            "connection_epoch": 1, "server_sequence": seq, "exchange_ts_ms": now,
            "server_received_at_ms": now, "published_at_ms": now,
            "payload": {"last": BID, "bid": BID, "ask": ASK, "spread": round(ASK - BID, 2),
                        "source": "e2e", "freshness_sec": 0},
        })


@asynccontextmanager
async def _e2e_lifespan(_app):
    # Only the quote feeder runs: no Bitget, no collector, no strategy loop, no Telegram outbox worker.
    task = asyncio.create_task(_ws_quote_feeder())
    try:
        yield
    finally:
        task.cancel()


app.router.lifespan_context = _e2e_lifespan


def _feed():
    while True:
        ts = int(time.time() * 1000)
        collector_service.latest_ticker = {
            "symbol": "XAUUSDT", "bid": BID, "ask": ASK, "last": BID, "timestamp": ts, "observed_at": ts,
        }
        time.sleep(1.0)


def _seed_candles(db):
    now = int(time.time() * 1000)
    step = 15 * 60_000
    base = now - (now % step) - 150 * step
    price = 2640.0
    for i in range(150):
        o = price
        c = o + (3.0 if i % 3 else -2.0)
        db.add(models.Candle(symbol="XAUUSDT", timeframe="15M", timestamp=base + i * step, open=o,
                             high=max(o, c) + 1.5, low=min(o, c) - 1.5, close=c, volume=100.0, is_closed=True))
        price = c if 2630 < c < 2670 else 2650.0
    db.commit()


def _upsert_setup(db, sid, direction, state, entry, sl, tp, plan_kind="CONFIRMED"):
    now = int(time.time() * 1000)
    row = db.query(models.WatchSetup).filter(models.WatchSetup.id == sid).first()
    if row is None:
        row = models.WatchSetup(id=sid, created_at=now)
        db.add(row)
    row.timeframe = "15M"
    row.direction = direction
    row.state = state
    row.provisional_entry = row.confirmed_entry = entry
    row.provisional_sl = row.confirmed_sl = sl
    row.provisional_tp = row.confirmed_tp = tp
    row.invalidation_price = sl
    row.leverage = 5
    row.margin_mode = "ISOLATED"
    row.risk_pct = 0.25
    row.version = (row.version or 0) + 1
    row.plan_kind = plan_kind
    row.direction_source = "SIGNAL" if plan_kind == "CONFIRMED" else "UNCONFIRMED"
    row.updated_at = now
    db.commit()


def _reset(db):
    for model in (models.NotificationOutbox, models.DomainEvent, models.PaperOrder, models.EconomicNews,
                  models.WatchSetup):
        db.query(model).delete()
    db.commit()
    audit = crud.get_or_create_today_audit(db)
    audit.fills_count = 0
    audit.consecutive_losses = 0
    audit.is_blocked = False
    audit.cooldown_until = None
    db.commit()
    _upsert_setup(db, "e2e-long", "LONG", "READY", **PLANS["LONG"])
    _upsert_setup(db, "e2e-short", "SHORT", "READY", **PLANS["SHORT"])
    _upsert_setup(db, "e2e-tight-short", "SHORT", "READY", **PLANS["TIGHT_SHORT"], plan_kind="PROVISIONAL")


def _state(db):
    audit = crud.get_or_create_today_audit(db)
    by_state = {}
    for o in db.query(models.PaperOrder).all():
        by_state[o.state] = by_state.get(o.state, 0) + 1
    return {
        "orders_by_state": by_state,
        "orders": db.query(models.PaperOrder).count(),
        "fills": audit.fills_count or 0,
        "events": db.query(models.DomainEvent).count(),
        "opened_events": db.query(models.DomainEvent).filter(models.DomainEvent.event_type == "trade.opened").count(),
        "rejected_events": db.query(models.DomainEvent).filter(models.DomainEvent.event_type == "order.rejected").count(),
        "outbox": db.query(models.NotificationOutbox).count(),
        "rejection_reasons": [o.invalidation_reason for o in
                              db.query(models.PaperOrder).filter(models.PaperOrder.state == "rejected").all()],
    }


@app.post("/__e2e/reset")
def e2e_reset():
    db = SessionLocal()
    try:
        _reset(db)
        return _state(db)
    finally:
        db.close()


@app.get("/__e2e/state")
def e2e_state():
    db = SessionLocal()
    try:
        return _state(db)
    finally:
        db.close()


@app.post("/__e2e/news")
def e2e_news(body: dict = Body(default={})):
    db = SessionLocal()
    try:
        now = int(time.time() * 1000)
        offset = int(body.get("offset_min", 0))
        db.add(models.EconomicNews(
            source_id=f"e2e-{now}-{offset}", title=body.get("title", "Core CPI m/m"),
            country=body.get("currency", "USD"), currency=body.get("currency", "USD"),
            impact=body.get("impact", "High"), scheduled_at=now + offset * 60_000,
            received_at=now - 86_400_000,
        ))
        db.commit()
        return _state(db)
    finally:
        db.close()


@app.post("/__e2e/tick")
def e2e_tick(body: dict = Body(default={})):
    """Feed one touching quote to the REAL execution coordinator (the fill path under test)."""
    direction = body.get("direction", "LONG")
    now = int(time.time() * 1000)
    bid, ask = (2649.8, 2650.0) if direction == "LONG" else (2650.0, 2650.2)
    ticker = {"symbol": "XAUUSDT", "bid": bid, "ask": ask, "timestamp": now, "observed_at": now}
    db = SessionLocal()
    try:
        execution_coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=FakeClock(now), now_ms=now)
        return _state(db)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        _seed_candles(db)
        _reset(db)
    finally:
        db.close()
    threading.Thread(target=_feed, daemon=True).start()
    print(f"[e2e_server] isolated DB: {os.environ['AURUM_DB_PATH']}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
