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


@dataclass
class CanonicalQuote:
    symbol: str
    last: float
    bid: float
    ask: float
    mark_price: Optional[float] = None
    exchange_ts_ms: Optional[int] = None
    received_at_ms: int = 0
    source: str = "WS"  # "WS" or "REST_FALLBACK"
    connection_epoch: int = 1
    status: str = "VALID"  # "VALID", "STALE", "MALFORMED", "INVERTED", "DISCONNECTED"
    spread: float = 0.0
    freshness_sec: float = 0.0
    rejection_code: Optional[str] = None
    rejection_detail: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.status == "VALID"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "last": self.last,
            "bid": self.bid,
            "ask": self.ask,
            "mark_price": self.mark_price or self.last,
            "exchange_ts_ms": self.exchange_ts_ms,
            "received_at_ms": self.received_at_ms,
            "source": self.source,
            "connection_epoch": self.connection_epoch,
            "status": self.status,
            "spread": self.spread,
            "freshness_sec": self.freshness_sec,
            "rejection_code": self.rejection_code,
            # Backward compatibility with ticker dict
            "server_time": self.exchange_ts_ms or self.received_at_ms,
            "observed_at": self.received_at_ms
        }

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

        if math.isnan(bid) or math.isinf(bid) or math.isnan(ask) or math.isinf(ask) or math.isnan(last) or math.isinf(last):
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
                rejection_detail="Giá bid/ask/last chứa NaN hoặc Infinity"
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
        raw_ex = (
            ticker.get("server_time")
            or ticker.get("server_time_ms")
            or ticker.get("exchange_time")
            or ticker.get("exchange_ts_ms")
            or ticker.get("timestamp")
        )
        raw_obs = (
            ticker.get("observed_at")
            or ticker.get("received_at_ms")
        )

        def _parse_ts(val: Any) -> Tuple[Optional[int], bool]:
            if val is None:
                return None, False
            if isinstance(val, (bool, dict, list)):
                return None, True
            try:
                f_val = float(val)
                if not math.isfinite(f_val):
                    return None, True
                i_val = int(f_val)
                if i_val <= 0:
                    return None, True
                return i_val, False
            except (ValueError, TypeError, OverflowError):
                return None, True

        exchange_time, ex_malformed = _parse_ts(raw_ex)
        actual_observed, obs_malformed = _parse_ts(raw_obs)

        if (raw_ex is not None and ex_malformed) or (raw_obs is not None and obs_malformed):
            return ValidatedQuote(
                is_valid=False,
                bid=bid,
                ask=ask,
                last=last,
                spread=round(ask - bid, 4),
                exchange_time=None,
                observed_at=now,
                freshness_sec=9999.0,
                is_stale=True,
                rejection_code="MALFORMED_TIMESTAMP",
                rejection_detail="Timestamp quote không hợp lệ (NaN, Infinity, boolean hoặc không thể chuyển đổi)",
            )

        # If neither exchange_time nor actual receipt provenance is provided, do NOT fabricate now as fresh quote
        if not exchange_time and not actual_observed:
            return ValidatedQuote(
                is_valid=False,
                bid=bid,
                ask=ask,
                last=last,
                spread=round(ask - bid, 4),
                exchange_time=None,
                observed_at=now,
                freshness_sec=9999.0,
                is_stale=True,
                rejection_code="TIME_UNAVAILABLE",
                rejection_detail="Thiếu timestamp nguồn và thời điểm nhận thực tế"
            )

        ref_time = exchange_time if (exchange_time and exchange_time > 0) else actual_observed
        observed_at = actual_observed or now

        # Future timestamp check (tolerance 5.0 seconds = 5000 ms)
        if ref_time > (now + 5000):
            return ValidatedQuote(
                is_valid=False,
                bid=bid,
                ask=ask,
                last=last,
                spread=round(ask - bid, 4),
                exchange_time=exchange_time,
                observed_at=observed_at,
                freshness_sec=9999.0,
                is_stale=True,
                rejection_code="FUTURE_TIMESTAMP",
                rejection_detail=f"Timestamp quote ở tương lai vượt ngưỡng dung sai ({ref_time} > {now})"
            )

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

    @classmethod
    def validate_canonical(
        cls,
        raw_quote: Dict[str, Any],
        source: str = "WS",
        connection_epoch: int = 1,
        now_ms: Optional[int] = None,
        max_age_sec: Optional[float] = None
    ) -> CanonicalQuote:
        """
        Validates raw ticker or candle quote and builds CanonicalQuote according to V7.1 specification.
        """
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        threshold_sec = max_age_sec if max_age_sec is not None else cls.DEFAULT_TICKER_MAX_AGE_SEC
        symbol = raw_quote.get("symbol", "XAUUSDT") if raw_quote else "XAUUSDT"

        if not raw_quote:
            return CanonicalQuote(
                symbol=symbol,
                last=0.0,
                bid=0.0,
                ask=0.0,
                received_at_ms=now,
                source=source,
                connection_epoch=connection_epoch,
                status="DISCONNECTED",
                freshness_sec=9999.0,
                rejection_code="FEED_DISCONNECTED",
                rejection_detail="Không có dữ liệu quote"
            )

        raw_bid = raw_quote.get("bid", raw_quote.get("bidPr"))
        raw_ask = raw_quote.get("ask", raw_quote.get("askPr"))
        raw_last = raw_quote.get("last", raw_quote.get("lastPr", raw_bid))
        raw_mark = raw_quote.get("mark_price", raw_quote.get("markPrice"))

        try:
            bid = float(raw_bid) if raw_bid is not None else 0.0
            ask = float(raw_ask) if raw_ask is not None else 0.0
            last = float(raw_last) if raw_last is not None else bid
            mark = float(raw_mark) if raw_mark is not None else last
        except (ValueError, TypeError):
            return CanonicalQuote(
                symbol=symbol,
                last=0.0,
                bid=0.0,
                ask=0.0,
                received_at_ms=now,
                source=source,
                connection_epoch=connection_epoch,
                status="MALFORMED",
                freshness_sec=9999.0,
                rejection_code="MALFORMED_QUOTE",
                rejection_detail="Giá bid/ask không phải số hợp lệ"
            )

        if math.isnan(bid) or math.isinf(bid) or math.isnan(ask) or math.isinf(ask) or math.isnan(last) or math.isinf(last):
            return CanonicalQuote(
                symbol=symbol,
                last=0.0,
                bid=0.0,
                ask=0.0,
                received_at_ms=now,
                source=source,
                connection_epoch=connection_epoch,
                status="MALFORMED",
                freshness_sec=9999.0,
                rejection_code="NON_FINITE_QUOTE",
                rejection_detail="Giá bid/ask chứa NaN hoặc Infinity"
            )

        if bid <= 0 or ask <= 0:
            return CanonicalQuote(
                symbol=symbol,
                last=last,
                bid=bid,
                ask=ask,
                received_at_ms=now,
                source=source,
                connection_epoch=connection_epoch,
                status="MALFORMED",
                freshness_sec=9999.0,
                rejection_code="ZERO_OR_NEGATIVE_PRICE",
                rejection_detail=f"Giá bid={bid} hoặc ask={ask} không dương"
            )

        if ask < bid:
            return CanonicalQuote(
                symbol=symbol,
                last=last,
                bid=bid,
                ask=ask,
                spread=round(ask - bid, 4),
                received_at_ms=now,
                source=source,
                connection_epoch=connection_epoch,
                status="INVERTED",
                freshness_sec=0.0,
                rejection_code="INVERTED_SPREAD",
                rejection_detail=f"Spread bị đảo ngược: ask={ask} < bid={bid}"
            )

        exchange_time_raw = raw_quote.get("exchange_ts_ms") or raw_quote.get("server_time") or raw_quote.get("ts") or raw_quote.get("timestamp")
        exchange_time = int(exchange_time_raw) if exchange_time_raw else None
        actual_received = raw_quote.get("received_at_ms") or raw_quote.get("observed_at")
        received_at = int(actual_received) if actual_received else None

        if not exchange_time and not received_at:
            return CanonicalQuote(
                symbol=symbol,
                last=round(last, 2),
                bid=round(bid, 2),
                ask=round(ask, 2),
                mark_price=round(mark, 2),
                exchange_ts_ms=None,
                received_at_ms=now,
                source=source,
                connection_epoch=connection_epoch,
                status="TIME_UNAVAILABLE",
                spread=round(ask - bid, 4),
                freshness_sec=9999.0,
                rejection_code="TIME_UNAVAILABLE",
                rejection_detail="Thiếu timestamp nguồn và thời điểm nhận thực tế"
            )

        ref_time = exchange_time if (exchange_time and exchange_time > 0) else received_at
        received_at = received_at or now

        if ref_time > (now + 5000):
            return CanonicalQuote(
                symbol=symbol,
                last=round(last, 2),
                bid=round(bid, 2),
                ask=round(ask, 2),
                mark_price=round(mark, 2),
                exchange_ts_ms=exchange_time,
                received_at_ms=received_at,
                source=source,
                connection_epoch=connection_epoch,
                status="FUTURE_TIMESTAMP",
                spread=round(ask - bid, 4),
                freshness_sec=9999.0,
                rejection_code="FUTURE_TIMESTAMP",
                rejection_detail=f"Timestamp quote ở tương lai vượt ngưỡng ({ref_time} > {now})"
            )

        freshness_sec = max(0.0, (now - ref_time) / 1000.0)
        is_stale = freshness_sec > threshold_sec

        if is_stale:
            return CanonicalQuote(
                symbol=symbol,
                last=round(last, 2),
                bid=round(bid, 2),
                ask=round(ask, 2),
                mark_price=round(mark, 2),
                exchange_ts_ms=exchange_time,
                received_at_ms=received_at,
                source=source,
                connection_epoch=connection_epoch,
                status="STALE",
                spread=round(ask - bid, 4),
                freshness_sec=round(freshness_sec, 2),
                rejection_code="TICKER_STALE",
                rejection_detail=f"Giá quote bị cũ ({freshness_sec:.1f}s > {threshold_sec:.1f}s)"
            )

        return CanonicalQuote(
            symbol=symbol,
            last=round(last, 2),
            bid=round(bid, 2),
            ask=round(ask, 2),
            mark_price=round(mark, 2),
            exchange_ts_ms=exchange_time,
            received_at_ms=received_at,
            source=source,
            connection_epoch=connection_epoch,
            status="VALID",
            spread=round(ask - bid, 4),
            freshness_sec=round(freshness_sec, 2),
            rejection_code=None,
            rejection_detail=None
        )


def normalize_execution_quote(
    raw_quote: Any,
    *,
    now_ms: int,
    max_age_sec: Optional[float] = None,
) -> ValidatedQuote:
    """
    Authoritative quote normalizer and freshness validator for execution entry paths (V15.8).
    Accepts:
    - None -> ValidatedQuote(is_valid=False, rejection_code='FEED_DISCONNECTED')
    - ValidatedQuote -> Re-validates against now_ms, preserves original timestamps and source rejections.
    - CanonicalQuote -> Re-validates against now_ms, maps exchange_ts_ms / received_at_ms, preserves source rejections.
    - dict -> Re-validates against now_ms, maps server_time / timestamp / exchange_time / observed_at / received_at_ms.
      Does NOT mutate input dict.
      Does NOT fabricate now_ms as quote timestamp when provenance is missing.
    - Unsupported types -> ValidatedQuote(is_valid=False, rejection_code='MALFORMED_QUOTE')
    """
    if raw_quote is None:
        return ValidatedQuote(
            is_valid=False,
            bid=0.0,
            ask=0.0,
            last=0.0,
            spread=0.0,
            exchange_time=None,
            observed_at=now_ms,
            freshness_sec=9999.0,
            is_stale=True,
            rejection_code="FEED_DISCONNECTED",
            rejection_detail="Không có dữ liệu báo giá",
        )

    if isinstance(raw_quote, ValidatedQuote):
        t_dict = {
            "bid": raw_quote.bid,
            "ask": raw_quote.ask,
            "last": raw_quote.last,
            "server_time": raw_quote.exchange_time,
            "observed_at": raw_quote.observed_at,
        }
        res = QuoteValidator.validate_ticker(t_dict, now_ms=now_ms, max_age_sec=max_age_sec)
        if not raw_quote.is_valid:
            return ValidatedQuote(
                is_valid=False,
                bid=res.bid,
                ask=res.ask,
                last=res.last,
                spread=res.spread,
                exchange_time=res.exchange_time,
                observed_at=res.observed_at,
                freshness_sec=res.freshness_sec,
                is_stale=res.is_stale or raw_quote.is_stale,
                rejection_code=raw_quote.rejection_code or res.rejection_code or "QUOTE_INVALID",
                rejection_detail=raw_quote.rejection_detail or res.rejection_detail or "Báo giá không hợp lệ",
            )
        return res

    if isinstance(raw_quote, CanonicalQuote):
        t_dict = {
            "bid": raw_quote.bid,
            "ask": raw_quote.ask,
            "last": raw_quote.last,
            "server_time": raw_quote.exchange_ts_ms,
            "observed_at": raw_quote.received_at_ms,
        }
        res = QuoteValidator.validate_ticker(t_dict, now_ms=now_ms, max_age_sec=max_age_sec)
        if not raw_quote.is_valid:
            return ValidatedQuote(
                is_valid=False,
                bid=res.bid,
                ask=res.ask,
                last=res.last,
                spread=res.spread,
                exchange_time=res.exchange_time,
                observed_at=res.observed_at,
                freshness_sec=res.freshness_sec,
                is_stale=res.is_stale,
                rejection_code=raw_quote.rejection_code or raw_quote.status or "QUOTE_INVALID",
                rejection_detail=raw_quote.rejection_detail or f"CanonicalQuote trạng thái {raw_quote.status}",
            )
        return res

    if isinstance(raw_quote, dict):
        raw_ex = (
            raw_quote.get("server_time")
            or raw_quote.get("server_time_ms")
            or raw_quote.get("exchange_time")
            or raw_quote.get("exchange_ts_ms")
            or raw_quote.get("timestamp")
        )
        raw_obs = (
            raw_quote.get("observed_at")
            or raw_quote.get("received_at_ms")
        )
        t_dict = {
            "bid": raw_quote.get("bid"),
            "ask": raw_quote.get("ask"),
            "last": raw_quote.get("last"),
            "server_time": raw_ex,
            "observed_at": raw_obs,
        }
        res = QuoteValidator.validate_ticker(t_dict, now_ms=now_ms, max_age_sec=max_age_sec)
        if raw_quote.get("is_valid") is False:
            return ValidatedQuote(
                is_valid=False,
                bid=res.bid,
                ask=res.ask,
                last=res.last,
                spread=res.spread,
                exchange_time=res.exchange_time,
                observed_at=res.observed_at,
                freshness_sec=res.freshness_sec,
                is_stale=res.is_stale or bool(raw_quote.get("is_stale", False)),
                rejection_code=raw_quote.get("rejection_code") or res.rejection_code or "QUOTE_INVALID",
                rejection_detail=raw_quote.get("rejection_detail") or res.rejection_detail or "Báo giá không hợp lệ",
            )
        return res

    return ValidatedQuote(
        is_valid=False,
        bid=0.0,
        ask=0.0,
        last=0.0,
        spread=0.0,
        exchange_time=None,
        observed_at=now_ms,
        freshness_sec=9999.0,
        is_stale=True,
        rejection_code="MALFORMED_QUOTE",
        rejection_detail=f"Kiểu dữ liệu báo giá không được hỗ trợ: {type(raw_quote).__name__}",
    )
