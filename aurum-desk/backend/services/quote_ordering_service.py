"""
backend/services/quote_ordering_service.py
V14.4.2 — Realtime quote ordering gate per Appendix B contract.

Enforces:
- Monotonic source watermark tracking per symbol.
- Dispositions: ACCEPT_CURRENT, DUPLICATE, LATE_EVIDENCE, REJECT_INVALID.
- Equal-timestamp with changed payload preserved within equal-time bucket.
- Reconnect and REST fallback never reset source watermark to 0.
- Late evidence audit trail: quotes older than order.opened_at cannot retroactively fill/close new orders.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from services.quote_validator import CanonicalQuote

logger = logging.getLogger("quote_ordering_service")


class QuoteDisposition(str, Enum):
    ACCEPT_CURRENT = "ACCEPT_CURRENT"
    DUPLICATE = "DUPLICATE"
    LATE_EVIDENCE = "LATE_EVIDENCE"
    REJECT_INVALID = "REJECT_INVALID"


@dataclass
class SymbolOrderState:
    watermark_ms: Optional[int] = None
    last_digest: Optional[str] = None
    epoch: int = 1
    total_accepted: int = 0
    total_duplicates: int = 0
    total_late: int = 0
    total_invalid: int = 0
    late_evidence_queue: list = field(default_factory=list)
    overflow_count: int = 0
    overflow_gap_start_ms: Optional[int] = None
    overflow_gap_end_ms: Optional[int] = None


class QuoteOrderingService:
    """
    Authoritative quote ordering service protecting backend execution chronology.
    Guarantees monotonic exchange watermark progression without silent regressions.
    """
    def __init__(self):
        self._states: Dict[str, SymbolOrderState] = {}

    def get_state(self, symbol: str) -> SymbolOrderState:
        if symbol not in self._states:
            self._states[symbol] = SymbolOrderState()
        return self._states[symbol]

    def reset_watermark_for_testing(self, symbol: str = "XAUUSDT"):
        """Testing hook only."""
        if symbol in self._states:
            del self._states[symbol]

    def get_late_evidence(self, symbol: str) -> list:
        """Returns recent bounded late evidence items for audit / reconciliation."""
        return list(self.get_state(symbol).late_evidence_queue)

    def claim_late_evidence_batch(self, symbol: str, limit: int = 50) -> list:
        """Claims a batch of late evidence for reconciliation."""
        st = self.get_state(symbol)
        return list(st.late_evidence_queue[:limit])

    def acknowledge_late_evidence(self, symbol: str, processed_count: int) -> None:
        """Acknowledges processed late evidence records from the queue."""
        st = self.get_state(symbol)
        st.late_evidence_queue = st.late_evidence_queue[processed_count:]

    def get_overflow_gap(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Returns overflow gap interval if queue overflowed."""
        st = self.get_state(symbol)
        if st.overflow_count > 0:
            return {
                "overflow_count": st.overflow_count,
                "gap_start_ms": st.overflow_gap_start_ms,
                "gap_end_ms": st.overflow_gap_end_ms,
            }
        return None

    @staticmethod
    def compute_quote_digest(quote: CanonicalQuote) -> str:
        """Computes deterministic digest of price-bearing payload fields."""
        payload = {
            "symbol": quote.symbol,
            "bid": round(float(quote.bid), 4) if quote.bid is not None else None,
            "ask": round(float(quote.ask), 4) if quote.ask is not None else None,
            "last": round(float(quote.last), 4) if quote.last is not None else None,
            "mark_price": round(float(quote.mark_price), 4) if quote.mark_price is not None else None,
            "exchange_ts_ms": quote.exchange_ts_ms,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def classify_quote(self, quote: CanonicalQuote) -> Tuple[QuoteDisposition, str]:
        """
        Classifies incoming validated quote relative to monotonic exchange watermark.
        Returns (QuoteDisposition, reason_code).
        """
        if not quote.is_valid or quote.exchange_ts_ms is None or quote.exchange_ts_ms <= 0:
            st = self.get_state(quote.symbol)
            st.total_invalid += 1
            return QuoteDisposition.REJECT_INVALID, "QUOTE_INVALID_OR_MISSING_EXCHANGE_TS"

        state = self.get_state(quote.symbol)
        digest = self.compute_quote_digest(quote)
        src_ts = quote.exchange_ts_ms

        # First quote seen
        if state.watermark_ms is None:
            state.watermark_ms = src_ts
            state.last_digest = digest
            state.total_accepted += 1
            return QuoteDisposition.ACCEPT_CURRENT, "FIRST_QUOTE_WATERMARK_INITIALIZED"

        # Strictly newer exchange timestamp
        if src_ts > state.watermark_ms:
            state.watermark_ms = src_ts
            state.last_digest = digest
            state.total_accepted += 1
            return QuoteDisposition.ACCEPT_CURRENT, "NEWER_EXCHANGE_TS"

        # Equal timestamp: check digest
        if src_ts == state.watermark_ms:
            if digest == state.last_digest:
                state.total_duplicates += 1
                return QuoteDisposition.DUPLICATE, "IDENTICAL_TIMESTAMP_AND_DIGEST"
            else:
                # Legitimate order book tick at the same millisecond with changed prices
                state.last_digest = digest
                state.total_accepted += 1
                return QuoteDisposition.ACCEPT_CURRENT, "EQUAL_TS_PRICE_UPDATE"

        # Older exchange timestamp: source arrived out-of-order
        state.total_late += 1
        record = {
            "symbol": quote.symbol,
            "source_timestamp": src_ts,
            "watermark_at_arrival": state.watermark_ms,
            "bid": quote.bid,
            "ask": quote.ask,
            "last": quote.last,
            "digest": digest,
            "received_at_ms": quote.received_at_ms,
            "reason": f"LATE_TICK_{src_ts}_LESS_THAN_WATERMARK_{state.watermark_ms}",
        }
        state.late_evidence_queue.append(record)
        if len(state.late_evidence_queue) > 50:
            dropped = state.late_evidence_queue.pop(0)
            state.overflow_count += 1
            d_ts = dropped.get("source_timestamp")
            if d_ts is not None:
                if state.overflow_gap_start_ms is None or d_ts < state.overflow_gap_start_ms:
                    state.overflow_gap_start_ms = d_ts
                if state.overflow_gap_end_ms is None or d_ts > state.overflow_gap_end_ms:
                    state.overflow_gap_end_ms = d_ts

        return QuoteDisposition.LATE_EVIDENCE, f"LATE_TICK_{src_ts}_LESS_THAN_WATERMARK_{state.watermark_ms}"

    def on_reconnect_or_source_switch(self, symbol: str, new_epoch: int, source: str):
        """
        Maintains watermark monotonicity across transport reconnect or REST fallback.
        Does NOT reset watermark_ms to 0!
        """
        state = self.get_state(symbol)
        state.epoch = new_epoch
        logger.info(
            f"QuoteOrderingService source switched for {symbol} (epoch={new_epoch}, source={source}), "
            f"preserved monotonic watermark={state.watermark_ms}"
        )


quote_ordering_service = QuoteOrderingService()
