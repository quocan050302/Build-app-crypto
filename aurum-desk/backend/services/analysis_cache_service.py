import time
import json
import asyncio
import logging
from typing import Dict, Tuple, Optional, Any
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud, smc_engine
from services.candle_cache_service import candle_cache_service
from services.quote_validator import CanonicalQuote

logger = logging.getLogger(__name__)

class AnalysisCacheService:
    """
    Cached SMC Analysis Snapshots (V7.1):
    - Completely eliminates running full SMC recalculations on every GET request or price tick.
    - Manages snapshot cache keyed by (symbol, timeframe).
    - Single-flight evaluation prevents CPU and DB lock contention.
    - Automatically invalidates on closed bars, policy updates, risk changes, or news blackouts.
    """
    def __init__(self, ttl_sec: float = 12.0):
        self.ttl_sec = ttl_sec
        # (symbol, timeframe) -> dict
        self._cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._last_as_of: Dict[Tuple[str, str], int] = {}
        self._inflight_locks: Dict[Tuple[str, str], asyncio.Lock] = {}
        self._running = False

    def _get_lock(self, key: Tuple[str, str]) -> asyncio.Lock:
        if key not in self._inflight_locks:
            self._inflight_locks[key] = asyncio.Lock()
        return self._inflight_locks[key]

    def invalidate(self, symbol: str = "XAUUSDT", timeframe: Optional[str] = None):
        """Invalidate cache on dependency changes (news, policy, risk config)."""
        if timeframe:
            self._last_as_of.pop((symbol, timeframe), None)
            self._cache.pop((symbol, timeframe), None)
        else:
            keys = [k for k in self._cache.keys() if k[0] == symbol]
            for k in keys:
                self._last_as_of.pop(k, None)
                self._cache.pop(k, None)

    def on_bar_closed(self, symbol: str, timeframe: str):
        """Called by candle_cache_service when a bar is finalized."""
        self.invalidate(symbol, timeframe)

    def compute_analysis_sync(
        self,
        db: Session,
        symbol: str = "XAUUSDT",
        timeframe: str = "15M",
        latest_quote: Optional[CanonicalQuote] = None
    ) -> Dict[str, Any]:
        """Synchronously compute SMC analysis without network blocking."""
        candles = candle_cache_service.get_candles(symbol, timeframe, limit=150)
        if len(candles) < 15:
            # Fallback to local DB query
            db_candles = crud.get_candles(db, symbol, timeframe, limit=150, ascending=True)
            if len(db_candles) >= 15:
                candle_cache_service.update_from_rest_sync(symbol, timeframe, [
                    crud.to_candle_create(c) for c in db_candles
                ] if hasattr(crud, 'to_candle_create') else [])
                candles = candle_cache_service.get_candles(symbol, timeframe, limit=150)

        if len(candles) < 15:
            return {
                "status": "BOOTSTRAPPING",
                "symbol": symbol,
                "timeframe": timeframe,
                "message": "Đang đồng bộ dữ liệu nến lịch sử...",
                "active_signal": None,
                "conditions_met": [],
                "missing_conditions": ["Chưa đủ nến lịch sử"],
                "candles_count": len(candles)
            }

        now_ms = int(time.time() * 1000)
        is_blackout, blackout_reason, _ = crud.check_news_blackout(db, now_ms)
        day_audit = crud.get_or_create_today_audit(db)

        # Settings
        from services.risk_settings_service import risk_settings_service
        global_settings = risk_settings_service.get_settings(db)
        leverage = global_settings.requested_leverage
        margin_mode = global_settings.margin_mode
        risk_pct = global_settings.risk_pct

        # Ticker representation
        if latest_quote and latest_quote.is_valid:
            ticker_data = latest_quote.to_dict()
        else:
            c_last = candles[-1].close
            ticker_data = {"bid": c_last, "ask": c_last, "last": c_last, "server_time": now_ms}

        from services.collector_service import collector_service
        analysis = smc_engine.evaluate_smc_setup(
            candles=candles,
            symbol=symbol,
            timeframe=timeframe,
            ticker_data=ticker_data,
            day_audit=day_audit,
            is_news_blackout=is_blackout,
            news_blackout_reason=blackout_reason,
            htf_bias=collector_service.d_4h_bias,
            h1_alignment=collector_service.h1_alignment,
            leverage=leverage,
            margin_mode=margin_mode,
            risk_pct=risk_pct
        )

        analysis["as_of_ms"] = now_ms
        self._cache[(symbol, timeframe)] = analysis
        self._last_as_of[(symbol, timeframe)] = now_ms
        return analysis

    async def get_or_compute_analysis(
        self,
        symbol: str = "XAUUSDT",
        timeframe: str = "15M",
        latest_quote: Optional[CanonicalQuote] = None
    ) -> Dict[str, Any]:
        """Asynchronously returns cached analysis or runs single-flight compute."""
        key = (symbol, timeframe)
        now_ms = int(time.time() * 1000)

        # Cache hit check
        if key in self._cache and (now_ms - self._last_as_of.get(key, 0) < self.ttl_sec * 1000):
            return self._cache[key]

        lock = self._get_lock(key)
        async with lock:
            # Double-check inside lock
            if key in self._cache and (now_ms - self._last_as_of.get(key, 0) < self.ttl_sec * 1000):
                return self._cache[key]

            db = SessionLocal()
            try:
                res = self.compute_analysis_sync(db, symbol, timeframe, latest_quote)
                return res
            finally:
                db.close()

analysis_cache_service = AnalysisCacheService()
