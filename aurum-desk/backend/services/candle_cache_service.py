import time
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

class CandleCacheService:
    """
    In-memory Sliding Window Candle Cache with Bounded SQLite Persistence (V7.1):
    - Eliminates blocking disk writes on every price tick.
    - Manages canonical closed and open bars across 1M, 5M, 15M, 1H, 4H, and D.
    - Updates open bars incrementally with incoming quotes.
    - Automatically finalizes bars at UTC timeframe boundaries.
    - Flushes closed bars to SQLite via a bounded background worker.
    """
    def __init__(self, max_bars: int = 200):
        self.max_bars = max_bars
        # (symbol, timeframe) -> List[schemas.CandleCreate]
        self._cache: Dict[Tuple[str, str], List[schemas.CandleCreate]] = {}
        self._watermarks: Dict[Tuple[str, str], int] = {}
        self._persist_queue: asyncio.Queue[schemas.CandleCreate] = asyncio.Queue(maxsize=1000)
        self._running = False
        self._bootstrapped = False

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

    def process_quote(self, quote: CanonicalQuote) -> List[schemas.CandleCreate]:
        """
        Updates in-memory open candle across all timeframes with the latest price quote.
        Returns list of newly closed candles (if any boundary was crossed).
        """
        if not quote.is_valid:
            return []

        symbol = quote.symbol
        price = quote.last
        now_ms = quote.exchange_ts_ms or quote.received_at_ms or int(time.time() * 1000)
        closed_bars: List[schemas.CandleCreate] = []

        for tf, dur_ms in TF_MS.items():
            key = (symbol, tf)
            bar_start = (now_ms // dur_ms) * dur_ms
            bars = self._cache.setdefault(key, [])

            if not bars:
                new_bar = schemas.CandleCreate(
                    symbol=symbol,
                    timeframe=tf,
                    timestamp=bar_start,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=0.1,
                    is_closed=False
                )
                bars.append(new_bar)
                continue

            last_bar = bars[-1]

            if last_bar.timestamp == bar_start:
                # Same interval, update open bar
                last_bar.high = max(last_bar.high, price)
                last_bar.low = min(last_bar.low, price)
                last_bar.close = price
            elif bar_start > last_bar.timestamp:
                # Boundary crossed! Finalize previous bar
                last_bar.is_closed = True
                closed_bars.append(last_bar)

                # Enqueue for DB persistence
                try:
                    self._persist_queue.put_nowait(last_bar)
                except asyncio.QueueFull:
                    logger.warning("Candle persistence queue full, dropping bar from async queue")

                # Create new open bar
                new_bar = schemas.CandleCreate(
                    symbol=symbol,
                    timeframe=tf,
                    timestamp=bar_start,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=0.1,
                    is_closed=False
                )
                bars.append(new_bar)

                # Keep bounded memory
                if len(bars) > self.max_bars:
                    self._cache[key] = bars[-self.max_bars:]

        return closed_bars

    def update_from_rest_sync(self, symbol: str, timeframe: str, candles: List[schemas.CandleCreate]):
        """Integrates historical candles fetched via REST without overwriting fresh deltas."""
        if not candles:
            return
        key = (symbol, timeframe)
        existing = self._cache.setdefault(key, [])
        if not existing:
            self._cache[key] = candles[-self.max_bars:]
            return

        latest_existing_ts = existing[-1].timestamp
        for c in candles:
            if c.timestamp > latest_existing_ts:
                existing.append(c)
            else:
                # update if closed
                for i, ex in enumerate(existing):
                    if ex.timestamp == c.timestamp and not ex.is_closed and c.is_closed:
                        existing[i] = c

        existing.sort(key=lambda x: x.timestamp)
        self._cache[key] = existing[-self.max_bars:]

    async def run_persistence_worker(self):
        """Background worker that flushes closed candles in batches to SQLite."""
        self._running = True
        logger.info("Candle persistence worker started.")

        while self._running:
            try:
                bar = await self._persist_queue.get()
                batch = [bar]

                # Collect any other pending closed bars
                while not self._persist_queue.empty() and len(batch) < 50:
                    try:
                        batch.append(self._persist_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                db = SessionLocal()
                try:
                    crud.bulk_upsert_candles(db, batch)
                except Exception as e:
                    logger.error(f"Error persisting candle batch to DB: {e}")
                finally:
                    db.close()
                    for _ in batch:
                        self._persist_queue.task_done()

            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error(f"Candle persistence worker error: {e}", exc_info=True)
                await asyncio.sleep(1.0)

candle_cache_service = CandleCacheService()
