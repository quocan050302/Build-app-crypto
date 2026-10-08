import time
import asyncio
from typing import Dict, Any, Optional, Tuple, List
import httpx
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud, bitget_data, smc_engine
from services.event_bus import event_bus

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
        self.feed_connected = False
        self.last_success_time = 0
        self.last_error: Optional[str] = None
        self.d_4h_bias = "UNKNOWN"
        self.h1_alignment = "UNKNOWN"
        self.latest_ticker: Optional[Dict[str, Any]] = None
        self._was_degraded = False

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
                db: Session = SessionLocal()
                try:
                    crud.bulk_upsert_candles(db, candles)
                    return True
                finally:
                    db.close()
            return False
        except Exception as e:
            self.last_error = f"{timeframe} sync error: {str(e)}"
            return False

    def update_htf_context(self, symbol: str = "XAUUSDT"):
        """Calculate authentic D/4H bias and 1H alignment from stored candles."""
        db: Session = SessionLocal()
        try:
            # 1. 4H / D bias
            d_candles = crud.get_candles(db, symbol, "D", limit=50, ascending=True)
            h4_candles = crud.get_candles(db, symbol, "4H", limit=80, ascending=True)

            ref_candles = h4_candles if len(h4_candles) >= 20 else d_candles
            if len(ref_candles) >= 15:
                sh, sl = smc_engine.identify_pivots(ref_candles, "4H" if len(h4_candles) >= 20 else "D")
                trend = smc_engine.determine_trend(ref_candles, sh, sl)
                self.d_4h_bias = trend
            else:
                self.d_4h_bias = "UNKNOWN"

            # 2. 1H alignment
            h1_candles = crud.get_candles(db, symbol, "1H", limit=80, ascending=True)
            if len(h1_candles) >= 15:
                sh1, sl1 = smc_engine.identify_pivots(h1_candles, "1H")
                h1_trend = smc_engine.determine_trend(h1_candles, sh1, sl1)
                self.h1_alignment = "ALIGNED" if h1_trend == self.d_4h_bias else ("OPPOSING" if h1_trend in ("BULLISH", "BEARISH") else "NEUTRAL")
            else:
                self.h1_alignment = "UNKNOWN"
        finally:
            db.close()

    async def run_collector_loop(self):
        """Main non-blocking background collector loop."""
        self.collector_alive = True
        symbol = "XAUUSDT"

        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    # 1. Fetch live ticker
                    try:
                        self.latest_ticker = await bitget_data.async_fetch_ticker(client, symbol)
                        self.feed_connected = True
                        self.last_success_time = int(time.time() * 1000)

                        if self._was_degraded:
                            self._was_degraded = False
                            event_bus.publish_event(
                                event_type="feed.recovered",
                                aggregate_id=symbol,
                                payload={"symbol": symbol, "status": "connected"}
                            )
                            try:
                                db_rec = SessionLocal()
                                from services.position_recovery_service import position_recovery_service
                                position_recovery_service.check_and_recover_offline_positions(db_rec)
                                db_rec.close()
                            except Exception:
                                pass
                    except Exception as e:
                        self.feed_connected = False
                        self.last_error = f"Ticker fetch failed: {str(e)}"
                        if not self._was_degraded:
                            self._was_degraded = True
                            event_bus.publish_event(
                                event_type="feed.degraded",
                                aggregate_id=symbol,
                                payload={"symbol": symbol, "reason": self.last_error}
                            )

                    # 2. Cycle timeframe syncs
                    # 15M and 5M every 10 seconds
                    await self.sync_timeframe(client, symbol, "15M", limit=120)
                    await self.sync_timeframe(client, symbol, "5M", limit=100)

                    # 3. Update HTF bias every 30 seconds
                    self.update_htf_context(symbol)

                    # 4. Sync 1H and 4H periodically
                    await self.sync_timeframe(client, symbol, "1H", limit=60)
                    await self.sync_timeframe(client, symbol, "4H", limit=60)

                    await asyncio.sleep(5.0)

                except asyncio.CancelledError:
                    self.collector_alive = False
                    break
                except Exception as e:
                    self.last_error = str(e)
                    await asyncio.sleep(4.0)

collector_service = CollectorService()
