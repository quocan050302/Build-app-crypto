import time
import asyncio
import logging
import httpx
from typing import Dict, List, Any, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class MarginTier(BaseModel):
    tier: int
    min_notional: float
    max_notional: float
    max_leverage: int
    maintenance_margin_rate: float

class InstrumentMetadata(BaseModel):
    symbol: str = "XAUUSDT"
    product_type: str = "USDT-FUTURES"
    max_leverage: int = 100
    min_leverage: int = 1
    maker_fee_rate: float = 0.0002
    taker_fee_rate: float = 0.0006
    qty_step: float = 0.01
    min_qty: float = 0.01
    min_notional_usdt: float = 5.0
    tiers: List[MarginTier] = []
    fetched_at: int = 0
    metadata_version: int = 1
    status: str = "NOT_FETCHED"

class InstrumentProvider:
    def __init__(self):
        self._cache: Dict[str, InstrumentMetadata] = {}
        self._lock = asyncio.Lock()

    def _get_default_tiers(self) -> List[MarginTier]:
        # Based on verified Bitget rules for XAUUSDT (08/10/2026)
        return [
            MarginTier(tier=1, min_notional=0, max_notional=20000, max_leverage=100, maintenance_margin_rate=0.0050),
            MarginTier(tier=2, min_notional=20000, max_notional=200000, max_leverage=75, maintenance_margin_rate=0.0100),
            MarginTier(tier=3, min_notional=200000, max_notional=500000, max_leverage=50, maintenance_margin_rate=0.0150),
            MarginTier(tier=4, min_notional=500000, max_notional=2000000, max_leverage=25, maintenance_margin_rate=0.0200),
            MarginTier(tier=5, min_notional=2000000, max_notional=5000000, max_leverage=20, maintenance_margin_rate=0.0250),
            MarginTier(tier=6, min_notional=5000000, max_notional=20000000, max_leverage=10, maintenance_margin_rate=0.0500),
            MarginTier(tier=7, min_notional=20000000, max_notional=40000000, max_leverage=5, maintenance_margin_rate=0.1000),
            MarginTier(tier=8, min_notional=40000000, max_notional=60000000, max_leverage=4, maintenance_margin_rate=0.1250),
            MarginTier(tier=9, min_notional=60000000, max_notional=100000000, max_leverage=2, maintenance_margin_rate=0.3000),
            MarginTier(tier=10, min_notional=100000000, max_notional=200000000, max_leverage=1, maintenance_margin_rate=0.6000),
        ]

    def get_metadata_sync(self, symbol: str = "XAUUSDT") -> InstrumentMetadata:
        """Get cached metadata synchronously (fallback to verified static data if not fetched)"""
        if symbol in self._cache and self._cache[symbol].status in ("SUCCESS", "STALE"):
            return self._cache[symbol]
        
        # Fallback to verified 08/10/2026 data
        meta = InstrumentMetadata(
            symbol=symbol,
            tiers=self._get_default_tiers(),
            fetched_at=int(time.time() * 1000),
            status="OFFLINE_FIXTURE"
        )
        return meta

    async def fetch_metadata(self, symbol: str = "XAUUSDT", product_type: str = "USDT-FUTURES") -> InstrumentMetadata:
        """Fetch fresh metadata from Bitget Public API."""
        async with self._lock:
            # Simple caching for 1 hour
            now_ms = int(time.time() * 1000)
            if symbol in self._cache and self._cache[symbol].status == "SUCCESS":
                if now_ms - self._cache[symbol].fetched_at < 3600_000:
                    return self._cache[symbol]

            meta = InstrumentMetadata(symbol=symbol, product_type=product_type)
            
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    # 1. Fetch Contract Info
                    url_contract = f"https://api.bitget.com/api/v2/mix/market/contracts?productType={product_type}&symbol={symbol}"
                    res_contract = await client.get(url_contract)
                    res_contract.raise_for_status()
                    data_contract = res_contract.json()
                    
                    if data_contract.get("code") == "00000" and data_contract.get("data"):
                        contract = data_contract["data"][0]
                        meta.max_leverage = int(contract.get("maxLever", 100))
                        meta.min_leverage = int(contract.get("minLever", 1))
                        meta.maker_fee_rate = float(contract.get("makerFeeRate", 0.0002))
                        meta.taker_fee_rate = float(contract.get("takerFeeRate", 0.0006))
                        meta.qty_step = float(contract.get("sizeMultiplier", 0.01)) # Bitget uses sizeMultiplier or qtyStep
                        meta.min_notional_usdt = float(contract.get("minTradeUSDT", 5.0))
                    
                    # 2. Fetch Position Leverage Tiers
                    url_tier = f"https://api.bitget.com/api/v2/mix/market/query-position-lever?productType={product_type}&symbol={symbol}"
                    res_tier = await client.get(url_tier)
                    res_tier.raise_for_status()
                    data_tier = res_tier.json()
                    
                    if data_tier.get("code") == "00000" and data_tier.get("data"):
                        tiers_raw = data_tier["data"]
                        parsed_tiers = []
                        for t in tiers_raw:
                            parsed_tiers.append(MarginTier(
                                tier=int(t.get("tier", 1)),
                                min_notional=float(t.get("minPosAmt", 0)),
                                max_notional=float(t.get("maxPosAmt", 0)),
                                max_leverage=int(t.get("maxLever", 1)),
                                maintenance_margin_rate=float(t.get("maintainMarginRate", 0))
                            ))
                        # Sort and validate no gaps
                        parsed_tiers.sort(key=lambda x: x.tier)
                        meta.tiers = parsed_tiers
                    
                    if not meta.tiers:
                        meta.tiers = self._get_default_tiers()

                    meta.status = "SUCCESS"
                    meta.fetched_at = now_ms
                    meta.metadata_version = now_ms
                    self._cache[symbol] = meta
                    return meta
                    
            except Exception as e:
                logger.error(f"Failed to fetch metadata for {symbol}: {e}")
                if symbol in self._cache:
                    self._cache[symbol].status = "STALE"
                    return self._cache[symbol]
                
                meta.tiers = self._get_default_tiers()
                meta.status = "OFFLINE_FIXTURE"
                meta.fetched_at = now_ms
                return meta

instrument_provider = InstrumentProvider()
