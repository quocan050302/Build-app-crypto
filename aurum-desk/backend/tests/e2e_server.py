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
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests dir: shared NY fixtures

_tmp = tempfile.mkdtemp(prefix="aurum_e2e_")
os.environ["AURUM_DB_PATH"] = os.path.join(_tmp, "e2e_aurum.db")
os.environ["TESTING"] = "1"

import uvicorn  # noqa: E402
from fastapi import Body  # noqa: E402

import crud  # noqa: E402
import models  # noqa: E402
from database import SessionLocal  # noqa: E402
from main import app  # noqa: E402
from services.clock import FakeClock, VN_TZ, live_clock  # noqa: E402
from services.collector_service import collector_service  # noqa: E402
from services.execution_coordinator import execution_coordinator  # noqa: E402

# ---- V14.3: injectable launcher clock (TEST LAUNCHER ONLY - production main.py has no such backdoor) ----
# `None` = real time (V14.2 scenarios). The NY scenarios pin the process clock to a NY wall time so the
# window / quota / audit logic is deterministic; every service already reads time through `live_clock`.
_CLOCK = {"ms": None}
_real_now_ms = live_clock.now_ms
live_clock.now_ms = lambda: _CLOCK["ms"] if _CLOCK["ms"] is not None else _real_now_ms()
live_clock.now_datetime = lambda: datetime.fromtimestamp(live_clock.now_ms() / 1000.0, tz=VN_TZ)

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
    _CLOCK["ms"] = None
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


# =====================================================================================================
# V14.3 NY quota scenarios: real NY cycle -> generator -> upsert -> guarded auto-arm -> coordinator fill.
# Only the clock, the candles and the quote are injected here; calculator / guard / lifecycle are real.
# =====================================================================================================
NY_DAY = (2026, 10, 9)          # a Friday inside EDT; window 08:00-11:00 NY, fallback 10:30


def _ny_now(hhmm: str) -> int:
    from ny_quota_fixtures import ny_ms
    hh, mm = (int(x) for x in hhmm.split(":"))
    return ny_ms(*NY_DAY, hh, mm)


def _ny_state(db):
    from services.trading_policy_service import TradingPolicyService
    now = live_clock.now_ms()
    q = TradingPolicyService.update_quota_lifecycle_state(db, now_ms=now, clock=FakeClock(now))
    st = _state(db)
    st.update(
        quota_status=q.quota_status, total_fills=q.total_fills, fallback_fills=q.fallback_fills,
        armed=db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").count(),
        open_positions=db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").count(),
        now_ms=now,
    )
    return st


@app.post("/__e2e/ny/reset")
def e2e_ny_reset(body: dict = Body(default={})):
    from ny_quota_fixtures import set_policy, set_saved_risk
    from services.lesson_policy_service import LessonPolicyService
    from services.lesson_rule_service import LessonRuleService
    db = SessionLocal()
    try:
        _CLOCK["ms"] = _ny_now(body.get("time", "09:00"))
        for model in (models.NotificationOutbox, models.DomainEvent, models.PaperOrder, models.EconomicNews,
                      models.WatchSetup, models.SessionQuota, models.NYOpportunityAudit, models.NYSessionAudit,
                      models.DayAudit, models.PaperObservation):
            db.query(model).delete()
        db.query(models.Candle).filter(models.Candle.timeframe == "5M").delete()
        db.commit()
        LessonPolicyService.reset_to_defaults(db)
        LessonRuleService.invalidate_cache()
        set_saved_risk(db)
        set_policy(db, ny_fallback_enabled=bool(body.get("fallback_enabled", True)), entry_session_policy="NY_ONLY")
        crud.get_or_create_today_audit(db)
        return _ny_state(db)
    finally:
        db.close()


@app.post("/__e2e/ny/clock")
def e2e_ny_clock(body: dict = Body(default={})):
    db = SessionLocal()
    try:
        _CLOCK["ms"] = _ny_now(body["time"])
        return _ny_state(db)
    finally:
        db.close()


@app.post("/__e2e/ny/policy")
def e2e_ny_policy(body: dict = Body(default={})):
    from ny_quota_fixtures import set_policy
    db = SessionLocal()
    try:
        set_policy(db, ny_fallback_enabled=bool(body["fallback_enabled"]))
        return _ny_state(db)
    finally:
        db.close()


@app.post("/__e2e/ny/news")
def e2e_ny_news(body: dict = Body(default={})):
    from ny_quota_fixtures import make_news
    db = SessionLocal()
    try:
        make_news(db, live_clock.now_ms(), title=body.get("title", "Core CPI m/m"), offset_min=int(body.get("offset_min", 0)))
        return _ny_state(db)
    finally:
        db.close()


@app.post("/__e2e/ny/cycle")
def e2e_ny_cycle(body: dict = Body(default={})):
    """One real strategy cycle at the pinned clock. `story` seeds the M5 FVG-retest story (LONG|SHORT|FLAT)."""
    from ny_quota_fixtures import BIAS, FIVE, seed_candles, ticker as ny_ticker
    from services.strategy_service import strategy_service
    direction = body.get("direction", "LONG")
    story = body.get("story", direction)
    db = SessionLocal()
    try:
        now = live_clock.now_ms()
        db.query(models.Candle).filter(models.Candle.timeframe == "5M").delete()
        db.commit()
        if story == "FLAT":
            flat = [(2672.0, 2672.5, 2671.5, 2672.0)] * 30
            seed_candles(db, "LONG", now, bars=flat)
        else:
            seed_candles(db, story, now)
        strategy_service.evaluate_upcoming_setups(
            "XAUUSDT", "15M", db=db, clock=FakeClock(now),
            ticker=ny_ticker(direction, now, int(body.get("quote_age_ms", 500))),
            htf_bias=BIAS[direction], h1_alignment="ALIGNED",
            feed_health={"has_execution_gap": False, "is_lagging": False},
            auto_enabled=bool(body.get("auto", True)))
        return _ny_state(db)
    finally:
        db.close()


@app.post("/__e2e/ny/fill")
def e2e_ny_fill(body: dict = Body(default={})):
    from ny_quota_fixtures import ticker as ny_ticker
    direction = body.get("direction", "LONG")
    db = SessionLocal()
    try:
        now = live_clock.now_ms() + 1000
        _CLOCK["ms"] = now
        execution_coordinator.evaluate_orders_sync(
            db, ticker_override=ny_ticker(direction, now, 200), clock=FakeClock(now), now_ms=now)
        return _ny_state(db)
    finally:
        db.close()


@app.get("/__e2e/ny/state")
def e2e_ny_state():
    db = SessionLocal()
    try:
        return _ny_state(db)
    finally:
        db.close()

# =====================================================================================================
# V14.4.1 SMC chart scenarios: REAL market-reading cycle (detector -> scenario -> persist -> watch -> guard ->
# arm -> coordinator fill). Only the persisted candles (smc_fixtures chain), the clock and the quote are injected.
# =====================================================================================================
SMC_OK_FEED = {"has_execution_gap": False, "is_lagging": False}
SMC_BIAS = {"LONG": "BULLISH", "SHORT": "BEARISH"}


def _smc_tick(direction, now_ms, age_ms=500):
    last = 2648.6 if direction == "LONG" else 2651.4
    ts = now_ms - age_ms
    return {"symbol": "XAUUSDT", "bid": last - 0.1, "ask": last + 0.1, "last": last - 0.1,
            "timestamp": ts, "observed_at": ts}


def _smc_state(db):
    import json as _json
    scen = [{"scenario_id": s.scenario_id, "snapshot_id": s.snapshot_id, "state": s.state,
             "direction": s.direction, "revision": s.revision, "instance_id": s.instance_id}
            for s in db.query(models.SMCScenarioSnapshot).order_by(models.SMCScenarioSnapshot.as_of_ms.asc(), models.SMCScenarioSnapshot.revision.asc()).all()]
    orders = [{"id": o.id, "state": o.state, "direction": o.direction, "planned_entry": o.planned_entry,
               "stop_loss": o.stop_loss, "take_profit": o.take_profit, "opened_at": o.opened_at,
               "closed_at": o.closed_at, "scenario_snapshot_id": o.scenario_snapshot_id,
               "quality_snapshot_id": o.quality_snapshot_id}
              for o in db.query(models.PaperOrder).order_by(models.PaperOrder.created_at.asc()).all()]
    watches = [{"id": w.id, "state": w.state, "direction": w.direction, "revision": w.version,
                "instance_id": w.setup_instance_id, "scenario_snapshot_id": w.scenario_snapshot_id}
               for w in db.query(models.WatchSetup).all()]
    return {"now_ms": live_clock.now_ms(), "scenarios": scen, "orders": orders, "watches": watches,
            "mutations_log": _json.dumps([])}


@app.post("/__e2e/smc/reset")
def e2e_smc_reset(body: dict = Body(default={})):
    """Seed one real confirmed SMC story and run the production cycle.

    body: direction LONG|SHORT, auto (arm), fill (coordinator fill), close (mark closed for history view).
    """
    import smc_fixtures as F
    from ny_quota_fixtures import set_policy, set_saved_risk
    from services.lesson_policy_service import LessonPolicyService
    from services.lesson_rule_service import LessonRuleService
    from services.strategy_service import strategy_service
    direction = body.get("direction", "LONG")
    db = SessionLocal()
    try:
        now = int(body.get("now_ms") or F.NOW_MS)
        _CLOCK["ms"] = now
        for model in (models.NotificationOutbox, models.DomainEvent, models.PaperOrder, models.EconomicNews,
                      models.WatchSetup, models.SessionQuota, models.NYOpportunityAudit, models.NYSessionAudit,
                      models.SMCScenarioSnapshot, models.SMCScenario, models.SetupQualitySnapshot,
                      models.PaperObservation):
            db.query(model).delete()
        db.query(models.Candle).delete()
        db.commit()
        LessonPolicyService.reset_to_defaults(db)
        LessonRuleService.invalidate_cache()
        set_saved_risk(db)
        set_policy(db, ny_fallback_enabled=False, entry_session_policy="NY_ONLY")
        for tf, candles in F.make_candles_by_tf(direction, now).items():
            for c in candles:
                db.add(models.Candle(symbol="XAUUSDT", timeframe=tf, timestamp=c.timestamp, open=c.open,
                                     high=c.high, low=c.low, close=c.close, volume=c.volume, is_closed=True))
        db.commit()
        # the live candle cache lazily snapshots the DB on first read and never refreshes: drop it so the
        # chart under test reads the candles this reset just seeded (test launcher only).
        from services.candle_cache_service import candle_cache_service
        candle_cache_service._cache.clear()
        candle_cache_service._watermarks.clear()
        # never DELETE the audit row: the open page polls /account/status concurrently and would race to
        # re-insert it (UNIQUE date_str). Reset the counters in place instead.
        audit = crud.get_or_create_today_audit(db)
        audit.fills_count = 0
        audit.consecutive_losses = 0
        audit.is_blocked = False
        audit.cooldown_until = None
        db.commit()
        strategy_service.evaluate_upcoming_setups(
            "XAUUSDT", "15M", db=db, clock=FakeClock(now), ticker=_smc_tick(direction, now),
            htf_bias=SMC_BIAS[direction], h1_alignment="ALIGNED", feed_health=SMC_OK_FEED,
            auto_enabled=bool(body.get("auto", True)))
        if body.get("fill"):
            order = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
            if order:
                fill_tk = {
                    "symbol": "XAUUSDT",
                    "bid": round(order.planned_entry - 0.2, 2) if direction == "LONG" else order.planned_entry,
                    "ask": order.planned_entry if direction == "LONG" else round(order.planned_entry + 0.2, 2),
                    "last": order.planned_entry,
                    "timestamp": now - 200,
                    "observed_at": now - 200
                }
                execution_coordinator.evaluate_orders_sync(
                    db, ticker_override=fill_tk, clock=FakeClock(now), now_ms=now)
            else:
                execution_coordinator.evaluate_orders_sync(
                    db, ticker_override=_smc_tick(direction, now, 200), clock=FakeClock(now), now_ms=now)
        if body.get("close"):
            for o in db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").all():
                sign = 1.0 if o.direction == "LONG" else -1.0
                entry = o.actual_entry or o.planned_entry
                o.state = "closed"
                o.actual_exit = o.take_profit
                o.exit_cause = "TP_HIT"
                o.closed_at = (o.opened_at or now) + 20 * 60_000
                o.realized_pnl_net = round(sign * (o.take_profit - entry) * o.quantity, 4)
                o.realized_r = round(o.realized_pnl_net / o.initial_risk_usdt, 4) if o.initial_risk_usdt else None
            db.commit()
        if body.get("invalidate"):
            for o in db.query(models.PaperOrder).all():
                o.state = "invalidated"
                o.invalidation_reason = "STRUCTURE_VIOLATED"
            db.commit()
        pad_days = int(body.get("after_days", 0))
        if pad_days > 0:
            # Flat, structure-free bars AFTER the story so that the story is older than the chart's
            # default window (the history-focus test must fetch a bounded historical window).
            for tf, step in (("5M", 300_000), ("15M", 900_000), ("1H", 3_600_000), ("4H", 14_400_000)):
                last = (db.query(models.Candle).filter_by(symbol="XAUUSDT", timeframe=tf)
                        .order_by(models.Candle.timestamp.desc()).first())
                price = float(last.close)
                for i in range(1, pad_days * 86_400_000 // step + 1):
                    db.add(models.Candle(symbol="XAUUSDT", timeframe=tf, timestamp=last.timestamp + i * step,
                                         open=price, high=price + 0.2, low=price - 0.2, close=price,
                                         volume=1.0, is_closed=True))
            db.commit()
            candle_cache_service._cache.clear()
            candle_cache_service._watermarks.clear()
        return _smc_state(db)
    finally:
        db.close()


@app.get("/__e2e/smc/state")
def e2e_smc_state():
    db = SessionLocal()
    try:
        return _smc_state(db)
    finally:
        db.close()


@app.post("/__e2e/ws/broadcast_frames")
async def e2e_broadcast_frames(body: dict = Body(...)):
    """Broadcasts actual schema-valid envelopes over the active WebSocket transport to the browser (PHẦN 055, 056, 057)."""
    from services.market_broadcaster import market_broadcaster
    frames = body.get("frames", [])
    sent = 0
    for frame in frames:
        await market_broadcaster._send_to_all(frame)
        sent += 1
    return {"sent_count": sent, "timestamp": int(time.time() * 1000)}

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
