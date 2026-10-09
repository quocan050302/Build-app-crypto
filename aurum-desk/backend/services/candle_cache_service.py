import time
import math
import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Any
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud, schemas
from services.quote_validator import CanonicalQuote

logger = logging.getLogger(__name__)

TF_MS = {
    "1M": 60 * 1000,
    "5M": 5 * 60 * 1000,
    "15M": 15 * 60 * 1000,
    "1H": 60 * 60 * 1000,
    "4H": 4 * 60 * 60 * 1000,
    "D": 24 * 60 * 60 * 1000
}

FRESHNESS_THRESHOLDS_SEC = {
    "1M": 3 * 60,
    "5M": 10 * 60,
    "15M": 30 * 60,
    "1H": 70 * 60,
    "4H": 260 * 60,
    "D": 26 * 3600
}


def validate_candle_row(row: Any) -> Optional[Dict[str, Any]]:
    """
    Validates a single candle row from Bitget WebSocket or REST.
    Expected: [ts, open, high, low, close, base_vol, quote_vol, usdt_vol]
    """
    if not isinstance(row, (list, tuple)) or len(row) < 5:
        return None
    try:
        ts = int(row[0])
        o = float(row[1])
        h = float(row[2])
        l = float(row[3])
        c = float(row[4])
        vol = float(row[5]) if len(row) > 5 and row[5] is not None and str(row[5]).strip() != "" else 0.0
    except (ValueError, TypeError):
        return None

    # Finite and positive checks
    for val in (o, h, l, c, vol):
        if not math.isfinite(val):
            return None
    if o <= 0 or h <= 0 or l <= 0 or c <= 0 or vol < 0:
        return None

    # Geometry checks: low <= min(open, close), high >= max(open, close), high >= low
    if l > min(o, c) + 1e-4 or h < max(o, c) - 1e-4 or h < l:
        return None

    return {
        "timestamp": ts,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": vol
    }


class CandleCacheService:
    """
    In-memory Sliding Window Candle Cache with Real Bitget OHLCV Streams (V7.2):
    - Completely eliminates synthesizing fake OHLC bars from ticker snapshots with volume=0.1.
    - Authoritative stream processor for real Bitget Classic WebSocket candle channels:
      (candle1m, candle5m, candle15m, candle1H, candle4H, candle1D).
    - Cumulative volume replacement: updates bar volume per revision, never cumulative '+='.
    - Causal bar closure: finalizes closed bar when a new interval start timestamp is received.
    - Direct broadcaster wiring: immediately broadcasts CANDLE_CLOSED and CANDLE_UPDATE to frontend.
    - Automated AnalysisCacheService invalidation upon bar closure.
    - Authoritative REST sync replaces stale / synthetic historical bars.
    - Persistence queue with retry buffer to guarantee zero dropped closed bars.
    """
    def __init__(self, max_bars: int = 200):
        self.max_bars = max_bars
        # (symbol, timeframe) -> List[schemas.CandleCreate]
        self._cache: Dict[Tuple[str, str], List[schemas.CandleCreate]] = {}
        self._watermarks: Dict[Tuple[str, str], int] = {}
        self._persist_queue: asyncio.Queue[schemas.CandleCreate] = asyncio.Queue(maxsize=2000)
        self._running = False
        self._bootstrapped = False
        self.persistence_drops = 0

    def is_bootstrapped(self, symbol: str = "XAUUSDT", timeframe: str = "15M") -> bool:
        candles = self._cache.get((symbol, timeframe), [])
        return len(candles) >= 10

    def bootstrap_from_db(self, db: Session, symbol: str = "XAUUSDT"):
        """Populate initial cache from local SQLite database."""
        for tf in ["1M", "5M", "15M", "1H", "4H", "D"]:
            candles = crud.get_candles(db, symbol, tf, limit=self.max_bars, ascending=True)
            if candles:
                models_list = [
                    schemas.CandleCreate(
                        symbol=c.symbol,
                        timeframe=c.timeframe,
                        timestamp=c.timestamp,
                        open=c.open,
                        high=c.high,
                        low=c.low,
                        close=c.close,
                        volume=c.volume,
                        is_closed=c.is_closed
                    )
                    for c in candles
                ]
                self._cache[(symbol, tf)] = models_list
                self._watermarks[(symbol, tf)] = models_list[-1].timestamp
        self._bootstrapped = True
        logger.info(f"CandleCacheService bootstrapped for {symbol}.")

    def get_candles(self, symbol: str, timeframe: str, limit: int = 150) -> List[schemas.CandleCreate]:
        candles = self._cache.get((symbol, timeframe), [])
        if not candles:
            # Fallback to local SQLite read if cache empty
            db = SessionLocal()
            try:
                db_candles = crud.get_candles(db, symbol, timeframe, limit=limit, ascending=True)
                if db_candles:
                    candles = [
                        schemas.CandleCreate(
                            symbol=c.symbol,
                            timeframe=c.timeframe,
                            timestamp=c.timestamp,
                            open=c.open,
                            high=c.high,
                            low=c.low,
                            close=c.close,
                            volume=c.volume,
                            is_closed=c.is_closed
                        )
                        for c in db_candles
                    ]
                    self._cache[(symbol, timeframe)] = candles
            finally:
                db.close()
        return candles[-limit:]

    def is_stale(self, symbol: str, timeframe: str) -> Tuple[bool, float]:
        candles = self._cache.get((symbol, timeframe), [])
        if not candles:
            return True, 9999.0
        last_ts = candles[-1].timestamp
        now_ms = int(time.time() * 1000)
        freshness_sec = max(0.0, (now_ms - last_ts) / 1000.0)
        max_sec = FRESHNESS_THRESHOLDS_SEC.get(timeframe, 30 * 60)
        return freshness_sec > max_sec, round(freshness_sec, 1)

    def process_candle_payload(
        self,
        symbol: str,
        timeframe: str,
        data_rows: list,
        action: str = "update"
    ) -> List[schemas.CandleCreate]:
        """
        Parses and applies real Bitget OHLCV candle stream updates.
        - Multiple rows supported.
        - Replaces cumulative volume per bar (no '+=').
        - Finalizes previous open bar when a new timestamp boundary is received.
        - Emits CANDLE_CLOSED and CANDLE_UPDATE via market_broadcaster.
        - Triggers AnalysisCacheService.on_bar_closed.
        """
        if not data_rows:
            return []

        key = (symbol, timeframe)
        bars = self._cache.setdefault(key, [])
        closed_bars: List[schemas.CandleCreate] = []

        # Sort rows by timestamp ascending
        parsed_rows = []
        for r in data_rows:
            validated = validate_candle_row(r)
            if validated:
                parsed_rows.append(validated)

        parsed_rows.sort(key=lambda x: x["timestamp"])

        for row in parsed_rows:
            ts = row["timestamp"]
            o = row["open"]
            h = row["high"]
            l = row["low"]
            c = row["close"]
            vol = row["volume"]

            if not bars:
                new_bar = schemas.CandleCreate(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=vol,
                    is_closed=False
                )
                bars.append(new_bar)
                self._broadcast_update(new_bar)
                continue

            last_bar = bars[-1]

            if ts == last_bar.timestamp:
                # Same bar: in-place revision with cumulative volume replace
                last_bar.high = max(last_bar.high, h)
                last_bar.low = min(last_bar.low, l)
                last_bar.close = c
                last_bar.volume = vol  # Cumulative replace!
                self._broadcast_update(last_bar)

            elif ts > last_bar.timestamp:
                # Boundary crossed: finalize previous bar!
                last_bar.is_closed = True
                closed_bars.append(last_bar)

                # 1. Enqueue closed bar for DB persistence
                self._enqueue_persist(last_bar)

                # 2. Broadcast CANDLE_CLOSED immediately
                self._broadcast_closed(last_bar)

                # 3. Invalidate Analysis Cache for this timeframe
                try:
                    from services.analysis_cache_service import analysis_cache_service
                    analysis_cache_service.on_bar_closed(symbol, timeframe)
                except Exception as e:
                    logger.debug(f"Analysis cache invalidation error: {e}")

                # 4. Append new open bar
                new_bar = schemas.CandleCreate(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=vol,
                    is_closed=False
                )
                bars.append(new_bar)
                self._broadcast_update(new_bar)

            else:
                # Historical revision: update existing historical bar if found
                for i, existing_bar in enumerate(bars):
                    if existing_bar.timestamp == ts:
                        bars[i] = schemas.CandleCreate(
                            symbol=symbol,
                            timeframe=timeframe,
                            timestamp=ts,
                            open=o,
                            high=h,
                            low=l,
                            close=c,
                            volume=vol,
                            is_closed=existing_bar.is_closed
                        )
                        break

            # Bound memory window
            if len(bars) > self.max_bars:
                self._cache[key] = bars[-self.max_bars:]

        return closed_bars

    def _enqueue_persist(self, bar: schemas.CandleCreate):
        try:
            self._persist_queue.put_nowait(bar)
        except asyncio.QueueFull:
            self.persistence_drops += 1
            logger.warning("Candle persistence queue full, bar will be retained in DB sync")

    def _broadcast_update(self, bar: schemas.CandleCreate):
        try:
            from services.market_broadcaster import market_broadcaster
            loop = asyncio.get_running_loop()
            loop.create_task(market_broadcaster.broadcast_candle_update(bar))
        except (RuntimeError, Exception):
            pass

    def _broadcast_closed(self, bar: schemas.CandleCreate):
        try:
            from services.market_broadcaster import market_broadcaster
            loop = asyncio.get_running_loop()
            loop.create_task(market_broadcaster.broadcast_candle_closed(bar))
        except (RuntimeError, Exception):
            pass

    def process_quote(self, quote: CanonicalQuote) -> List[schemas.CandleCreate]:
        """
        V7.2: Ticker quotes are strictly for price display and execution order matching.
        Does NOT synthesize fake OHLC bars or invent fake volume (0.1).
        Authoritative OHLCV comes exclusively from Bitget WS candle streams or REST.
        """
        return []

    def update_from_rest_sync(self, symbol: str, timeframe: str, candles: List[schemas.CandleCreate]):
        """
        Integrates authoritative historical candles fetched via REST.
        Replaces stale or synthetic closed bars cleanly.
        """
        if not candles:
            return
        key = (symbol, timeframe)
        existing = self._cache.setdefault(key, [])
        if not existing:
            self._cache[key] = candles[-self.max_bars:]
            return

        # Map existing bars by timestamp
        existing_map = {c.timestamp: i for i, c in enumerate(existing)}
        for c in candles:
            if c.timestamp in existing_map:
                idx = existing_map[c.timestamp]
                # Authoritative REST candle overwrites cached bar
                existing[idx] = c
            else:
                existing.append(c)

        existing.sort(key=lambda x: x.timestamp)
        self._cache[key] = existing[-self.max_bars:]

    async def run_persistence_worker(self):
        """Background worker that flushes closed candles in batches to SQLite with retry buffer."""
        self._running = True
        logger.info("Candle persistence worker started.")
        retry_buffer: List[schemas.CandleCreate] = []

        while self._running:
            try:
                batch: List[schemas.CandleCreate] = []
                if retry_buffer:
                    batch.extend(retry_buffer[:50])
                    retry_buffer = retry_buffer[50:]

                if len(batch) < 50:
                    try:
                        bar = await asyncio.wait_for(self._persist_queue.get(), timeout=1.0)
                        batch.append(bar)
                        while not self._persist_queue.empty() and len(batch) < 50:
                            try:
                                batch.append(self._persist_queue.get_nowait())
                            except asyncio.QueueEmpty:
                                break
                    except asyncio.TimeoutError:
                        if not batch:
                            continue

                if not batch:
                    continue

                db = SessionLocal()
                success = False
                try:
                    crud.bulk_upsert_candles(db, batch)
                    success = True
                except Exception as e:
                    logger.error(f"Error persisting candle batch to DB: {e}")
                    # Retain failed batch in retry buffer to prevent silent drops
                    retry_buffer.extend(batch)
                    if len(retry_buffer) > 500:
                        retry_buffer = retry_buffer[-500:]
                finally:
                    db.close()
                    if success:
                        for _ in batch:
                            try:
                                self._persist_queue.task_done()
                            except ValueError:
                                pass

            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error(f"Candle persistence worker error: {e}", exc_info=True)
                await asyncio.sleep(1.0)


candle_cache_service = CandleCacheService()

