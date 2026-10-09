import time
import asyncio
import logging
from typing import Dict, Any, Optional, Tuple, List
import httpx
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud, bitget_data, smc_engine
from services.event_bus import event_bus
from services.candle_cache_service import candle_cache_service

logger = logging.getLogger(__name__)

FRESHNESS_THRESHOLDS_SEC = {
    "1M": 3 * 60,
    "5M": 10 * 60,
    "15M": 30 * 60,
    "1H": 70 * 60,
    "4H": 260 * 60,
    "D": 26 * 3600
}

class CollectorService:
    def __init__(self):
        self.collector_alive = False
        self._feed_connected = False
        self._last_success_time = 0
        self.last_error: Optional[str] = None
        self.d_bias = "UNKNOWN"
        self.h4_bias = "UNKNOWN"
        self.d_4h_bias = "UNKNOWN"
        self.h1_alignment = "UNKNOWN"
        self._latest_ticker: Optional[Dict[str, Any]] = None
        self._was_degraded = False
        self._last_d_sync = 0
        self._last_1m_sync = 0
        self._last_htf_update = 0

    @property
    def latest_ticker(self) -> Optional[Dict[str, Any]]:
        from services.bitget_ws_service import bitget_ws_service
        if bitget_ws_service.latest_quote and bitget_ws_service.latest_quote.is_valid:
            return bitget_ws_service.latest_quote.to_dict()
        return self._latest_ticker

    @latest_ticker.setter
    def latest_ticker(self, val: Optional[Dict[str, Any]]):
        self._latest_ticker = val

    @property
    def feed_connected(self) -> bool:
        from services.bitget_ws_service import bitget_ws_service
        if bitget_ws_service.transport_state in ("CONNECTED", "DEGRADED"):
            return True
        return self._feed_connected

    @feed_connected.setter
    def feed_connected(self, val: bool):
        self._feed_connected = val

    @property
    def last_success_time(self) -> int:
        from services.bitget_ws_service import bitget_ws_service
        if bitget_ws_service.last_success_time > 0:
            return bitget_ws_service.last_success_time
        return self._last_success_time

    @last_success_time.setter
    def last_success_time(self, val: int):
        self._last_success_time = val

    def is_stale(self, timeframe: str, last_candle_ts: Optional[int]) -> Tuple[bool, float]:
        if not last_candle_ts:
            return True, 9999.0
        now_ms = int(time.time() * 1000)
        freshness_sec = max(0.0, (now_ms - last_candle_ts) / 1000.0)
        max_sec = FRESHNESS_THRESHOLDS_SEC.get(timeframe, 30 * 60)
        return freshness_sec > max_sec, round(freshness_sec, 1)

    async def sync_timeframe(
        self,
        client: httpx.AsyncClient,
        symbol: str,
        timeframe: str,
        limit: int = 100
    ) -> bool:
        try:
            candles, server_time = await bitget_data.async_fetch_candles(client, symbol, timeframe, limit)
            if candles:
                # Update in-memory candle cache
                candle_cache_service.update_from_rest_sync(symbol, timeframe, candles)

                # Persist to database in thread pool to prevent blocking event loop
                def _save_db():
                    db: Session = SessionLocal()
                    try:
                        crud.bulk_upsert_candles(db, candles)
                    finally:
                        db.close()
                await asyncio.to_thread(_save_db)
                return True
            return False
        except Exception as e:
            self.last_error = f"{timeframe} sync error: {str(e)}"
            return False

    def update_htf_context(self, symbol: str = "XAUUSDT"):
        """
        Calculate authentic, independent D & 4H bias and 1H alignment.
        No synthetic consensus or fake fallback.
        """
        db: Session = SessionLocal()
        try:
            # 1. Independent Daily Context (D)
            d_candles = crud.get_candles(db, symbol, "D", limit=50, ascending=True)
            if len(d_candles) >= 15:
                sh_d, sl_d = smc_engine.identify_pivots(d_candles, "D")
                self.d_bias = smc_engine.determine_trend(d_candles, sh_d, sl_d)
            else:
                self.d_bias = "UNKNOWN"

            # 2. Independent 4H Bias (4H)
            h4_candles = crud.get_candles(db, symbol, "4H", limit=80, ascending=True)
            if len(h4_candles) >= 15:
                sh_h4, sl_h4 = smc_engine.identify_pivots(h4_candles, "4H")
                self.h4_bias = smc_engine.determine_trend(h4_candles, sh_h4, sl_h4)
            else:
                self.h4_bias = "UNKNOWN"

            # 3. Synthesized HTF Context (Conflict aware)
            if self.d_bias in ("BULLISH", "BEARISH") and self.h4_bias in ("BULLISH", "BEARISH"):
                if self.d_bias == self.h4_bias:
                    self.d_4h_bias = self.d_bias
                else:
                    self.d_4h_bias = "CONFLICT"
            elif self.h4_bias in ("BULLISH", "BEARISH"):
                self.d_4h_bias = self.h4_bias
            elif self.d_bias in ("BULLISH", "BEARISH"):
                self.d_4h_bias = self.d_bias
            else:
                self.d_4h_bias = "UNKNOWN"

            # 4. Independent 1H Alignment
            h1_candles = crud.get_candles(db, symbol, "1H", limit=80, ascending=True)
            if len(h1_candles) >= 15:
                sh1, sl1 = smc_engine.identify_pivots(h1_candles, "1H")
                h1_trend = smc_engine.determine_trend(h1_candles, sh1, sl1)
                if self.d_4h_bias in ("BULLISH", "BEARISH"):
                    if h1_trend == self.d_4h_bias:
                        self.h1_alignment = "ALIGNED"
                    elif h1_trend in ("BULLISH", "BEARISH"):
                        self.h1_alignment = "OPPOSING"
                    else:
                        self.h1_alignment = "NEUTRAL"
                else:
                    self.h1_alignment = "NEUTRAL" if h1_trend != "UNKNOWN" else "UNKNOWN"
            else:
                self.h1_alignment = "UNKNOWN"
        finally:
            db.close()

    async def run_collector_loop(self):
        """
        Background collector loop with multi-timeframe synchronization (V7.1):
        - Offloads ticker streaming to BitgetWSService.
        - Synchronizes candle history periodically in background tasks.
        - Calculates HTF bias in separate thread to protect event loop.
        """
        self.collector_alive = True
        symbol = "XAUUSDT"

        # Bootstrap local cache from DB
        db = SessionLocal()
        try:
            candle_cache_service.bootstrap_from_db(db, symbol)
            await asyncio.to_thread(self.update_htf_context, symbol)
        finally:
            db.close()

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Initial fast sync of 15M, 5M, 1H
            try:
                await asyncio.gather(
                    self.sync_timeframe(client, symbol, "15M", limit=120),
                    self.sync_timeframe(client, symbol, "5M", limit=100),
                    self.sync_timeframe(client, symbol, "1H", limit=60),
                    return_exceptions=True
                )
            except Exception as e:
                logger.debug(f"Initial sync exception: {e}")

            while True:
                try:
                    now = time.time()

                    # 1. Background sync for active timeframes
                    await self.sync_timeframe(client, symbol, "15M", limit=60)
                    await self.sync_timeframe(client, symbol, "5M", limit=60)

                    # 2. 1M sync every 30s
                    if now - self._last_1m_sync > 30:
                        await self.sync_timeframe(client, symbol, "1M", limit=60)
                        self._last_1m_sync = now

                    # 3. 1H & 4H sync periodically
                    await self.sync_timeframe(client, symbol, "1H", limit=40)
                    await self.sync_timeframe(client, symbol, "4H", limit=40)

                    # 4. D sync every 5 minutes
                    if now - self._last_d_sync > 300:
                        await self.sync_timeframe(client, symbol, "D", limit=30)
                        self._last_d_sync = now

                    # 5. Update HTF bias in thread pool
                    if now - self._last_htf_update > 30:
                        await asyncio.to_thread(self.update_htf_context, symbol)
                        self._last_htf_update = now

                    await asyncio.sleep(10.0)

                except asyncio.CancelledError:
                    self.collector_alive = False
                    break
                except Exception as e:
                    self.last_error = str(e)
                    await asyncio.sleep(5.0)

collector_service = CollectorService()
