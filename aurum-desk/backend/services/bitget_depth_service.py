"""
backend/services/bitget_depth_service.py
V16 Public Orderbook Depth Adapter & Orderflow Shadow Layer (Rules 113 - 125).
Connects to Bitget public REST v3 orderbook endpoint for secondary market depth observation.
Features:
- Strict quality validation: finite prices, ascending asks, descending bids, uncrossed book, freshness.
- Isolated shadow observation layer: records telemetry/evidence, never bypasses SMC guards.
- Transparent unavailable handling: returns UNAVAILABLE when offline; never mocks fake depth.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger(__name__)

BITGET_REST_V3_ORDERBOOK_URL = "https://api.bitget.com/api/v3/market/orderbook"


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    amount: float


@dataclass
class OrderBookSnapshot:
    symbol: str
    source: str
    exchange_ts_ms: int
    received_ts_ms: int
    quality_state: str  # "VALID", "CROSSED", "STALE", "INVALID", "UNAVAILABLE"
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    spread: float = 0.0
    depth_imbalance: float = 0.0  # -1.0 (all ask) to +1.0 (all bid)
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        return self.quality_state == "VALID" and len(self.bids) > 0 and len(self.asks) > 0


def validate_orderbook_levels(
    raw_bids: List[Any],
    raw_asks: List[Any],
    exchange_ts_ms: int,
    now_ms: int,
    max_age_ms: int = 5000,
) -> Tuple[str, List[OrderBookLevel], List[OrderBookLevel], float, float, Dict[str, Any]]:
    """
    Validates orderbook levels strictly (Rule 120):
    - Finite positive numbers.
    - Bids strictly descending, asks strictly ascending.
    - Uncrossed book: highest bid < lowest ask.
    - Freshness check against now_ms.
    """
    if exchange_ts_ms <= 0:
        return "INVALID", [], [], 0.0, 0.0, {"error": "Missing exchange timestamp"}

    if now_ms - exchange_ts_ms > max_age_ms:
        return "STALE", [], [], 0.0, 0.0, {"error": f"Orderbook stale: age {now_ms - exchange_ts_ms}ms > {max_age_ms}ms"}

    parsed_bids: List[OrderBookLevel] = []
    parsed_asks: List[OrderBookLevel] = []

    try:
        for item in raw_bids:
            p, a = float(item[0]), float(item[1])
            if not math.isfinite(p) or not math.isfinite(a) or p <= 0 or a < 0:
                return "INVALID", [], [], 0.0, 0.0, {"error": f"Invalid bid values: {item}"}
            parsed_bids.append(OrderBookLevel(price=p, amount=a))

        for item in raw_asks:
            p, a = float(item[0]), float(item[1])
            if not math.isfinite(p) or not math.isfinite(a) or p <= 0 or a < 0:
                return "INVALID", [], [], 0.0, 0.0, {"error": f"Invalid ask values: {item}"}
            parsed_asks.append(OrderBookLevel(price=p, amount=a))
    except (ValueError, TypeError, IndexError) as err:
        return "INVALID", [], [], 0.0, 0.0, {"error": f"Parsing error: {err}"}

    if not parsed_bids or not parsed_asks:
        return "INVALID", [], [], 0.0, 0.0, {"error": "Empty bids or asks"}

    # Validate ordering
    for i in range(len(parsed_bids) - 1):
        if parsed_bids[i].price <= parsed_bids[i + 1].price:
            return "INVALID", [], [], 0.0, 0.0, {"error": "Bids not strictly descending"}

    for i in range(len(parsed_asks) - 1):
        if parsed_asks[i].price >= parsed_asks[i + 1].price:
            return "INVALID", [], [], 0.0, 0.0, {"error": "Asks not strictly ascending"}

    best_bid = parsed_bids[0].price
    best_ask = parsed_asks[0].price

    if best_bid >= best_ask:
        return "CROSSED", parsed_bids, parsed_asks, 0.0, 0.0, {
            "error": f"Crossed book: best_bid {best_bid} >= best_ask {best_ask}"
        }

    spread = round(best_ask - best_bid, 4)

    total_bid_vol = sum(b.amount for b in parsed_bids)
    total_ask_vol = sum(a.amount for a in parsed_asks)
    imbalance = 0.0
    if total_bid_vol + total_ask_vol > 0:
        imbalance = round((total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol), 4)

    return "VALID", parsed_bids, parsed_asks, spread, imbalance, {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "total_bid_vol": total_bid_vol,
        "total_ask_vol": total_ask_vol,
    }


class BitgetPublicDepthAdapter:
    """
    HTTP REST Client for Bitget public market depth v3 (USDT-FUTURES).
    Operates strictly in shadow mode: provides telemetry and context without bypassing trading guards.
    """

    def __init__(self, timeout_sec: float = 2.5):
        self.timeout_sec = timeout_sec
        self._last_snapshot: Optional[OrderBookSnapshot] = None

    def fetch_orderbook_sync(
        self,
        symbol: str = "XAUUSDT",
        limit: int = 5,
        category: str = "USDT-FUTURES",
        now_ms: Optional[int] = None,
    ) -> OrderBookSnapshot:
        """
        Synchronously fetches public depth from Bitget v3 market orderbook endpoint.
        Returns validated OrderBookSnapshot. If offline or failed, returns UNAVAILABLE without faking data.
        """
        curr_time = now_ms if now_ms is not None else int(time.time() * 1000)
        params = {
            "category": category,
            "symbol": symbol,
            "limit": str(limit),
        }
        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                resp = client.get(BITGET_REST_V3_ORDERBOOK_URL, params=params)
                if resp.status_code != 200:
                    return OrderBookSnapshot(
                        symbol=symbol,
                        source="BITGET_PUBLIC_V3_REST",
                        exchange_ts_ms=0,
                        received_ts_ms=curr_time,
                        quality_state="UNAVAILABLE",
                        details={"http_status": resp.status_code, "body": resp.text[:200]},
                    )
                data = resp.json()

            if not isinstance(data, dict) or data.get("code") not in (0, "0", "00000"):
                return OrderBookSnapshot(
                    symbol=symbol,
                    source="BITGET_PUBLIC_V3_REST",
                    exchange_ts_ms=0,
                    received_ts_ms=curr_time,
                    quality_state="UNAVAILABLE",
                    details={"api_code": data.get("code") if isinstance(data, dict) else "NON_DICT", "msg": data.get("msg") if isinstance(data, dict) else ""},
                )

            d = data.get("data", {})
            raw_bids = d.get("b", [])
            raw_asks = d.get("a", [])
            ts = int(d.get("ts", 0))

            q_state, bids, asks, spread, imbalance, details = validate_orderbook_levels(
                raw_bids, raw_asks, exchange_ts_ms=ts, now_ms=curr_time
            )

            snap = OrderBookSnapshot(
                symbol=symbol,
                source="BITGET_PUBLIC_V3_REST",
                exchange_ts_ms=ts,
                received_ts_ms=curr_time,
                quality_state=q_state,
                bids=bids,
                asks=asks,
                spread=spread,
                depth_imbalance=imbalance,
                details=details,
            )
            self._last_snapshot = snap
            return snap

        except Exception as err:
            logger.warning(f"Bitget depth fetch unavailable: {err}")
            return OrderBookSnapshot(
                symbol=symbol,
                source="BITGET_PUBLIC_V3_REST",
                exchange_ts_ms=0,
                received_ts_ms=curr_time,
                quality_state="UNAVAILABLE",
                details={"exception": str(err)},
            )


bitget_depth_adapter = BitgetPublicDepthAdapter()
