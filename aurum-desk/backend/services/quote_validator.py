import math
import time
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

@dataclass
class ValidatedQuote:
    is_valid: bool
    bid: float
    ask: float
    last: float
    spread: float
    exchange_time: Optional[int]
    observed_at: int
    freshness_sec: float
    is_stale: bool
    rejection_code: Optional[str]
    rejection_detail: Optional[str]

    @property
    def reason(self) -> str:
        return self.rejection_code or self.rejection_detail or ""

class QuoteValidator:
    """
    Authoritative Quote Validator for Aurum Desk:
    - Finite numeric checks: bid > 0, ask >= bid, no NaN/inf.
    - Explicit separation of exchange timestamp and observed_at.
    - Configurable freshness thresholds for tickers (default 15s for execution).
    - Detects stale quotes, inverted spreads, and feed disconnects.
    """
    DEFAULT_TICKER_MAX_AGE_SEC = 15.0

    @classmethod
    def validate_ticker(
        cls,
        ticker: Optional[Dict[str, Any]],
        now_ms: Optional[int] = None,
        max_age_sec: Optional[float] = None
    ) -> ValidatedQuote:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        threshold_sec = max_age_sec if max_age_sec is not None else cls.DEFAULT_TICKER_MAX_AGE_SEC

        if not ticker:
            return ValidatedQuote(
                is_valid=False,
                bid=0.0,
                ask=0.0,
                last=0.0,
                spread=0.0,
                exchange_time=None,
                observed_at=now,
                freshness_sec=9999.0,
                is_stale=True,
                rejection_code="FEED_DISCONNECTED",
                rejection_detail="Không có dữ liệu ticker từ collector feed"
            )

        raw_bid = ticker.get("bid")
        raw_ask = ticker.get("ask")
        raw_last = ticker.get("last", raw_bid)

        # Check numeric finite
        try:
            bid = float(raw_bid) if raw_bid is not None else 0.0
            ask = float(raw_ask) if raw_ask is not None else 0.0
            last = float(raw_last) if raw_last is not None else bid
        except (ValueError, TypeError):
            return ValidatedQuote(
                is_valid=False,
                bid=0.0,
                ask=0.0,
                last=0.0,
                spread=0.0,
                exchange_time=None,
                observed_at=now,
                freshness_sec=9999.0,
                is_stale=True,
                rejection_code="MALFORMED_QUOTE",
                rejection_detail="Giá bid/ask không phải số hợp lệ"
            )

        if math.isnan(bid) or math.isinf(bid) or math.isnan(ask) or math.isinf(ask):
            return ValidatedQuote(
                is_valid=False,
                bid=0.0,
                ask=0.0,
                last=0.0,
                spread=0.0,
                exchange_time=None,
                observed_at=now,
                freshness_sec=9999.0,
                is_stale=True,
                rejection_code="NON_FINITE_QUOTE",
                rejection_detail="Giá bid/ask chứa NaN hoặc Infinity"
            )

        if bid <= 0 or ask <= 0:
            return ValidatedQuote(
                is_valid=False,
                bid=bid,
                ask=ask,
                last=last,
                spread=0.0,
                exchange_time=None,
                observed_at=now,
                freshness_sec=9999.0,
                is_stale=True,
                rejection_code="ZERO_OR_NEGATIVE_PRICE",
                rejection_detail=f"Giá bid={bid} hoặc ask={ask} không dương"
            )

        if ask < bid:
            return ValidatedQuote(
                is_valid=False,
                bid=bid,
                ask=ask,
                last=last,
                spread=round(ask - bid, 4),
                exchange_time=None,
                observed_at=now,
                freshness_sec=0.0,
                is_stale=False,
                rejection_code="INVERTED_SPREAD",
                rejection_detail=f"Spread bị đảo ngược: ask={ask} < bid={bid}"
            )

        # Timestamps
        exchange_time = ticker.get("server_time") or ticker.get("timestamp")
        observed_at = ticker.get("observed_at") or now

        # Use exchange_time if valid and reasonable, otherwise fallback to observed_at
        ref_time = exchange_time if (exchange_time and exchange_time > 0) else observed_at
        freshness_sec = max(0.0, (now - ref_time) / 1000.0)
        is_stale = freshness_sec > threshold_sec

        if is_stale:
            return ValidatedQuote(
                is_valid=False,
                bid=bid,
                ask=ask,
                last=last,
                spread=round(ask - bid, 4),
                exchange_time=exchange_time,
                observed_at=observed_at,
                freshness_sec=round(freshness_sec, 2),
                is_stale=True,
                rejection_code="TICKER_STALE",
                rejection_detail=f"Giá ticker bị cũ ({freshness_sec:.1f}s > ngưỡng {threshold_sec:.1f}s)"
            )

        return ValidatedQuote(
            is_valid=True,
            bid=round(bid, 2),
            ask=round(ask, 2),
            last=round(last, 2),
            spread=round(ask - bid, 4),
            exchange_time=exchange_time,
            observed_at=observed_at,
            freshness_sec=round(freshness_sec, 2),
            is_stale=False,
            rejection_code=None,
            rejection_detail=None
        )
