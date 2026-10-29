"""
backend/services/market_shadow_service.py
AURUM DESK V17.8.0.3 — Safe Market Data Shadow Consumer, Multi-Timeframe Candle Cache,
Continuous SMC/ICT Unicorn Evaluator, and Hypothetical Opportunity Monitor.

ARCHITECTURAL SAFETY GUARANTEES:
- Strictly isolated from ExecutionConsumer, ExecutionCoordinator, PaperBroker, TradeLifecycleService,
  and operational SQLite database (`aurum_desk.db`).
- Never calls `execution_consumer.enqueue_quote(...)`.
- Never starts or triggers `cg01_evaluation_scheduler`.
- Never creates, arms, fills, or closes PAPER or LIVE orders.
- Persists research snapshots exclusively to `ShadowResearchStorage` (`AURUM_SHADOW_DB_PATH`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx

import smc_engine
from domain_calculator import CostAssumptions, calculate_risk_reward
from services.liquidity_scenario_service import identify_confirmed_swings
from services.quote_ordering_service import QuoteDisposition, QuoteOrderingService
from services.quote_validator import CanonicalQuote, QuoteValidator
from services.runtime_profile_service import (
    assert_runtime_capability,
    get_runtime_profile_status,
    increment_safety_counter,
)
from services.scalp_m1_model_service import (
    ModelExecutionAction,
    ScalpM1ModelService,
    resolve_executable_target,
    resolve_model_execution_policy,
)
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    WatchedZone,
    get_scalp_config,
)
from services.shadow_research_storage import (
    ALGORITHM_VERSION,
    DATA_VERSION,
    RESEARCH_ONLY_DISCLOSURE,
    ShadowResearchStorage,
    shadow_research_storage,
)
from services.smc_liquidity_service import build_liquidity_map, build_liquidity_clusters
from services.stop_placement_research_service import build_stop_comparison_plan
from services.stop_placement_service import (
    build_selected_break_descriptor,
    compute_stop_atr,
    compute_stop_buffer,
    plan_structural_stop,
    quantize_stop_outward,
    resolve_stop_anchor,
    select_protected_origin_for_break,
)
from services.stop_placement_types import (
    StopAnchor,
    StopAnchorKind,
    StopAnchorResult,
    StopPlacementMode,
    StopPlacementPolicy,
    StopStatus,
)
from services.unicorn_evidence_service import detect_breaker_blocks, detect_unicorn_evidence
from services.unicorn_evidence_types import UnicornSetupStatus

logger = logging.getLogger("aurum.market_shadow")

BITGET_PUBLIC_TICKER_URL = "https://api.bitget.com/api/v2/mix/market/ticker"

SUPPORTED_SHADOW_MODELS: Tuple[str, ...] = (
    ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
    ModelIdentifier.SMC_M1_CONTINUATION.value,
    ModelIdentifier.SMC_M1_REVERSAL.value,
    ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
    ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
)

TIMEFRAME_CANONICAL_MAP: Dict[str, str] = {
    "1M": "1M",
    "M1": "1M",
    "1m": "1M",
    "5M": "5M",
    "M5": "5M",
    "5m": "5M",
    "15M": "15M",
    "M15": "15M",
    "15m": "15M",
    "1H": "1H",
    "H1": "1H",
    "4H": "4H",
    "H4": "4H",
    "D": "D",
    "1D": "D",
}

TIMEFRAME_DURATION_MS: Dict[str, int] = {
    "1M": 60_000,
    "5M": 300_000,
    "15M": 900_000,
    "1H": 3_600_000,
    "4H": 14_400_000,
    "D": 86_400_000,
}

TIMEFRAME_STALE_SEC: Dict[str, float] = {
    "1M": 180.0,
    "5M": 600.0,
    "15M": 1800.0,
    "1H": 4200.0,
    "4H": 15600.0,
    "D": 93600.0,
}


class ShadowFeedState(str, Enum):
    CONNECTED_FRESH = "CONNECTED_FRESH"
    CONNECTED_STALE = "CONNECTED_STALE"
    REST_FALLBACK = "REST_FALLBACK"
    RECONNECTING = "RECONNECTING"
    DISCONNECTED = "DISCONNECTED"
    DATA_GAP = "DATA_GAP"


class ShadowOpportunityState(str, Enum):
    DETECTED = "DETECTED"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    OBSERVATION_COMPLETED = "OBSERVATION_COMPLETED"


class ShadowModelClassification(str, Enum):
    MODEL_NOT_EVALUATED = "MODEL_NOT_EVALUATED"
    MODEL_DISABLED = "MODEL_DISABLED"
    MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
    NO_VALID_SETUP = "NO_VALID_SETUP"
    VALID_SETUP_REJECTED_BY_RISK = "VALID_SETUP_REJECTED_BY_RISK"
    VALID_UNICORN = "VALID_UNICORN"
    ORDINARY_FVG_WITHOUT_UNICORN = "ORDINARY_FVG_WITHOUT_UNICORN"


SHADOW_STATE_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    ShadowOpportunityState.DETECTED.value: (
        ShadowOpportunityState.WAITING_CONFIRMATION.value,
        ShadowOpportunityState.CONFIRMED.value,
        ShadowOpportunityState.ELIGIBLE.value,
        ShadowOpportunityState.REJECTED.value,
        ShadowOpportunityState.INVALIDATED.value,
        ShadowOpportunityState.EXPIRED.value,
    ),
    ShadowOpportunityState.WAITING_CONFIRMATION.value: (
        ShadowOpportunityState.CONFIRMED.value,
        ShadowOpportunityState.ELIGIBLE.value,
        ShadowOpportunityState.REJECTED.value,
        ShadowOpportunityState.INVALIDATED.value,
        ShadowOpportunityState.EXPIRED.value,
    ),
    ShadowOpportunityState.CONFIRMED.value: (
        ShadowOpportunityState.ELIGIBLE.value,
        ShadowOpportunityState.REJECTED.value,
        ShadowOpportunityState.INVALIDATED.value,
        ShadowOpportunityState.EXPIRED.value,
        ShadowOpportunityState.OBSERVATION_COMPLETED.value,
    ),
    ShadowOpportunityState.ELIGIBLE.value: (
        ShadowOpportunityState.INVALIDATED.value,
        ShadowOpportunityState.EXPIRED.value,
        ShadowOpportunityState.OBSERVATION_COMPLETED.value,
    ),
    ShadowOpportunityState.REJECTED.value: (
        ShadowOpportunityState.OBSERVATION_COMPLETED.value,
    ),
    ShadowOpportunityState.EXPIRED.value: (
        ShadowOpportunityState.OBSERVATION_COMPLETED.value,
    ),
    ShadowOpportunityState.INVALIDATED.value: (
        ShadowOpportunityState.OBSERVATION_COMPLETED.value,
    ),
    ShadowOpportunityState.OBSERVATION_COMPLETED.value: (),
}


@dataclass
class ShadowCandleRecord:
    symbol: str
    timeframe: str
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool
    source: str = "WS"
    received_at: int = 0
    revision: int = 1
    connection_epoch: int = 1

    @property
    def timestamp(self) -> int:
        return self.open_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_time": self.open_time,
            "close_time": self.close_time,
            "timestamp": self.open_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_closed": self.is_closed,
            "source": self.source,
            "received_at": self.received_at,
            "revision": self.revision,
            "connection_epoch": self.connection_epoch,
        }


class MarketShadowService:
    """
    Isolated Market Shadow Consumer, Multi-Timeframe Candle Store, and Continuous
    SMC/ICT Unicorn Research Evaluator.
    """

    def __init__(
        self,
        *,
        symbol: str = "XAUUSDT",
        max_queue_size: int = 1000,
        max_candles_per_tf: int = 500,
        storage: Optional[ShadowResearchStorage] = None,
    ) -> None:
        self.symbol = symbol
        self.max_queue_size = max_queue_size
        self.max_candles_per_tf = max_candles_per_tf
        self._storage = storage or shadow_research_storage

        # Dedicated quote ordering state isolated from trading execution
        self.quote_ordering = QuoteOrderingService()

        # Bounded event queue for async stream processing
        self._event_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

        # Transport & epoch state
        self.transport_state: str = "DISCONNECTED"
        self.feed_source: str = "WS"
        self.active_connection_epoch: int = 1
        self.degraded_reason: Optional[str] = None
        self.has_data_gap: bool = False

        # Required Phase C.5 telemetry counters
        self.quotes_received: int = 0
        self.quotes_accepted: int = 0
        self.quotes_rejected: int = 0
        self.quotes_late: int = 0
        self.quotes_duplicate: int = 0

        self.candles_received: int = 0
        self.candles_closed: int = 0
        self.candles_revised: int = 0
        self.candles_missing: int = 0

        self.ws_reconnects: int = 0
        self.rest_fallback_activations: int = 0
        self.queue_drops: int = 0

        self.last_quote_timestamp: Optional[int] = None
        self.last_closed_m1_timestamp: Optional[int] = None
        self.last_closed_m5_timestamp: Optional[int] = None
        self.last_closed_m15_timestamp: Optional[int] = None

        # In-memory market state
        self.latest_quote: Optional[CanonicalQuote] = None
        self._recent_quotes: List[Dict[str, Any]] = []
        self._closed_candles: Dict[Tuple[str, str], List[ShadowCandleRecord]] = {}
        self._provisional_candles: Dict[Tuple[str, str], ShadowCandleRecord] = {}
        self._candle_digests: Dict[Tuple[str, str, int], str] = {}
        self._missing_intervals_by_tf: Dict[str, int] = {tf: 0 for tf in TIMEFRAME_DURATION_MS}

        # Shadow analysis & opportunity state
        self._opportunities: Dict[str, Dict[str, Any]] = {}
        self._unicorn_records: List[Dict[str, Any]] = []
        self._analysis_cycles: List[Dict[str, Any]] = []
        self._last_cycle_summary: Optional[Dict[str, Any]] = None
        self._disabled_models: set[str] = set()
        self._broadcaster_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._last_rest_poll_ms: int = 0

    @property
    def storage(self) -> ShadowResearchStorage:
        return self._storage

    def set_storage(self, storage: Optional[ShadowResearchStorage]) -> None:
        self._storage = storage

    def bind_storage(self, storage: Optional[ShadowResearchStorage]) -> None:
        self._storage = storage

    def reset_for_test(self, symbol: str = "XAUUSDT") -> None:
        self.reset_for_testing(symbol=symbol)

    @property
    def queue_depth(self) -> int:
        return self._event_queue.qsize()

    def reset_for_testing(self, symbol: str = "XAUUSDT") -> None:
        """Resets in-memory state and counters for deterministic unit/integration tests."""
        self.symbol = symbol
        self.quote_ordering = QuoteOrderingService()
        self._event_queue = asyncio.Queue(maxsize=self.max_queue_size)
        self.transport_state = "DISCONNECTED"
        self.feed_source = "WS"
        self.active_connection_epoch = 1
        self.degraded_reason = None
        self.has_data_gap = False

        self.quotes_received = 0
        self.quotes_accepted = 0
        self.quotes_rejected = 0
        self.quotes_late = 0
        self.quotes_duplicate = 0

        self.candles_received = 0
        self.candles_closed = 0
        self.candles_revised = 0
        self.candles_missing = 0

        self.ws_reconnects = 0
        self.rest_fallback_activations = 0
        self.queue_drops = 0

        self.last_quote_timestamp = None
        self.last_closed_m1_timestamp = None
        self.last_closed_m5_timestamp = None
        self.last_closed_m15_timestamp = None

        self.latest_quote = None
        self._recent_quotes.clear()
        self._closed_candles.clear()
        self._provisional_candles.clear()
        self._candle_digests.clear()
        self._missing_intervals_by_tf = {tf: 0 for tf in TIMEFRAME_DURATION_MS}
        self._opportunities.clear()
        self._unicorn_records.clear()
        self._analysis_cycles.clear()
        self._last_cycle_summary = None
        self._disabled_models.clear()
        self._last_rest_poll_ms = 0

    def set_model_enabled(self, model_id: str, enabled: bool) -> None:
        if enabled:
            self._disabled_models.discard(model_id)
        else:
            self._disabled_models.add(model_id)

    def on_transport_state_change(
        self,
        state: str,
        connection_epoch: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Updates transport state, connection epoch, and optional degraded reason."""
        self.transport_state = str(state or "DISCONNECTED").upper()
        if connection_epoch is not None and int(connection_epoch) > 0:
            self.active_connection_epoch = max(self.active_connection_epoch, int(connection_epoch))
        self.degraded_reason = reason

    def on_ws_reconnect(self, new_epoch: int, source: str = "WS") -> None:
        """Records WebSocket reconnect event and advances connection epoch monotonically."""
        if new_epoch > self.active_connection_epoch:
            if self.active_connection_epoch >= 1 and self.quotes_received > 0:
                self.ws_reconnects += 1
            self.active_connection_epoch = new_epoch
        else:
            self.ws_reconnects += 1
            self.active_connection_epoch = max(self.active_connection_epoch, new_epoch)
        self.transport_state = "RECONNECTING"
        self.quote_ordering.on_reconnect_or_source_switch(self.symbol, self.active_connection_epoch, source)

    def enqueue_event(self, event: Dict[str, Any]) -> bool:
        """
        Enqueues a market data event into the bounded shadow queue.
        On overflow, increments `queue_drops` and marks status `DEGRADED`.
        """
        try:
            self._event_queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self.queue_drops += 1
            self.degraded_reason = "SHADOW_EVENT_QUEUE_OVERFLOW"
            logger.warning("MarketShadowService event queue overflow (drops=%d)", self.queue_drops)
            return False

    # =========================================================================
    # PHASE C.2 — CANONICAL QUOTE VALIDATION & ORDERING
    # =========================================================================

    def ingest_raw_quote(
        self,
        raw_quote: Dict[str, Any],
        *,
        source: str = "WS",
        connection_epoch: Optional[int] = None,
        now_ms: Optional[int] = None,
        max_age_sec: float = 15.0,
    ) -> Tuple[bool, str, Optional[CanonicalQuote]]:
        """
        Validates a raw quote dictionary and processes it through the isolated shadow quote pipeline.
        Strictly enforces:
        - Explicit Bid and Ask required (never substitutes Last for Bid or Ask).
        - Symbol == self.symbol and valid contract type.
        - Connection epoch >= active_connection_epoch.
        - Finite positive prices, bid <= ask, non-negative spread.
        - Freshness and future timestamp bounds.
        - Monotonic exchange timestamp ordering (rejects duplicates and late quotes).
        """
        self.quotes_received += 1
        eff_now = now_ms if now_ms is not None else int(time.time() * 1000)
        eff_epoch = connection_epoch if connection_epoch is not None else self.active_connection_epoch

        if not isinstance(raw_quote, dict) or not raw_quote:
            self.quotes_rejected += 1
            return False, "EMPTY_OR_MALFORMED_QUOTE", None

        # 1. Symbol and contract type validation
        sym = str(raw_quote.get("symbol") or raw_quote.get("instId") or "").strip().upper()
        if sym != self.symbol:
            self.quotes_rejected += 1
            return False, f"WRONG_SYMBOL:{sym or 'MISSING'}", None

        inst_type = raw_quote.get("instType") or raw_quote.get("productType")
        if inst_type is not None and str(inst_type).strip().upper() != "USDT-FUTURES":
            self.quotes_rejected += 1
            return False, f"WRONG_CONTRACT_TYPE:{inst_type}", None

        # 2. Epoch validation
        if eff_epoch is None or isinstance(eff_epoch, bool) or int(eff_epoch) <= 0:
            self.quotes_rejected += 1
            return False, "INVALID_CONNECTION_EPOCH", None
        if int(eff_epoch) < self.active_connection_epoch:
            self.quotes_rejected += 1
            return False, f"STALE_CONNECTION_EPOCH:{eff_epoch}<{self.active_connection_epoch}", None

        # 3. Never silently substitute Last as executable Bid or Ask
        raw_bid = raw_quote.get("bid") if "bid" in raw_quote else raw_quote.get("bidPr")
        raw_ask = raw_quote.get("ask") if "ask" in raw_quote else raw_quote.get("askPr")
        if raw_bid is None or raw_ask is None:
            self.quotes_rejected += 1
            return False, "MISSING_EXECUTABLE_BID_OR_ASK", None
        if isinstance(raw_bid, bool) or isinstance(raw_ask, bool):
            self.quotes_rejected += 1
            return False, "BOOLEAN_PRICE_REJECTED", None

        canonical = QuoteValidator.validate_canonical(
            raw_quote={
                "symbol": sym,
                "bid": raw_bid,
                "ask": raw_ask,
                "last": raw_quote.get("last", raw_quote.get("lastPr", raw_bid)),
                "mark_price": raw_quote.get("mark_price", raw_quote.get("markPrice")),
                "exchange_ts_ms": (
                    raw_quote.get("exchange_ts_ms")
                    or raw_quote.get("ts")
                    or raw_quote.get("server_time")
                    or raw_quote.get("timestamp")
                ),
                "received_at_ms": raw_quote.get("received_at_ms") or raw_quote.get("observed_at") or eff_now,
            },
            source=source,
            connection_epoch=int(eff_epoch),
            now_ms=eff_now,
            max_age_sec=max_age_sec,
        )
        return self._process_validated_quote(canonical, count_received=False)

    def ingest_quote(
        self,
        quote: CanonicalQuote,
        *,
        now_ms: Optional[int] = None,
        max_age_sec: float = 15.0,
    ) -> Tuple[bool, str, Optional[CanonicalQuote]]:
        """Ingests a CanonicalQuote object into the shadow pipeline."""
        self.quotes_received += 1
        if not isinstance(quote, CanonicalQuote):
            self.quotes_rejected += 1
            return False, "INVALID_QUOTE_TYPE", None

        if str(quote.symbol).upper() != self.symbol:
            self.quotes_rejected += 1
            return False, f"WRONG_SYMBOL:{quote.symbol}", quote

        if quote.connection_epoch <= 0 or quote.connection_epoch < self.active_connection_epoch:
            self.quotes_rejected += 1
            return False, f"STALE_CONNECTION_EPOCH:{quote.connection_epoch}<{self.active_connection_epoch}", quote

        if now_ms is not None and quote.exchange_ts_ms is not None:
            revalidated = QuoteValidator.validate_canonical(
                raw_quote={
                    "symbol": quote.symbol,
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "last": quote.last,
                    "mark_price": quote.mark_price,
                    "exchange_ts_ms": quote.exchange_ts_ms,
                    "received_at_ms": quote.received_at_ms or now_ms,
                },
                source=quote.source,
                connection_epoch=quote.connection_epoch,
                now_ms=now_ms,
                max_age_sec=max_age_sec,
            )
            return self._process_validated_quote(revalidated, count_received=False)

        return self._process_validated_quote(quote, count_received=False)

    def _process_validated_quote(
        self,
        quote: CanonicalQuote,
        *,
        count_received: bool = False,
    ) -> Tuple[bool, str, Optional[CanonicalQuote]]:
        if count_received:
            self.quotes_received += 1

        if not quote.is_valid:
            self.quotes_rejected += 1
            reason = quote.rejection_code or quote.status or "QUOTE_INVALID"
            self._persist_quote(quote, disposition="REJECT_INVALID", is_accepted=False, reason=reason)
            return False, reason, quote

        disposition, reason = self.quote_ordering.classify_quote(quote)
        if disposition == QuoteDisposition.REJECT_INVALID:
            self.quotes_rejected += 1
            self._persist_quote(quote, disposition=disposition.value, is_accepted=False, reason=reason)
            return False, reason, quote

        if disposition == QuoteDisposition.DUPLICATE:
            self.quotes_duplicate += 1
            self.quotes_rejected += 1
            self._persist_quote(quote, disposition=disposition.value, is_accepted=False, reason=reason)
            return False, "DUPLICATE_QUOTE", quote

        if disposition == QuoteDisposition.LATE_EVIDENCE:
            self.quotes_late += 1
            self.quotes_rejected += 1
            self._persist_quote(quote, disposition=disposition.value, is_accepted=False, reason=reason)
            return False, "LATE_OUT_OF_ORDER_QUOTE", quote

        # ACCEPT_CURRENT
        self.quotes_accepted += 1
        self.latest_quote = quote
        self.last_quote_timestamp = quote.exchange_ts_ms or quote.received_at_ms
        self.active_connection_epoch = max(self.active_connection_epoch, quote.connection_epoch)
        if quote.source == "REST_FALLBACK":
            self.feed_source = "REST_FALLBACK"
        else:
            self.feed_source = "WS"
            self.transport_state = "CONNECTED"

        q_dict = quote.to_dict()
        q_dict["disposition"] = disposition.value
        self._recent_quotes.append(q_dict)
        if len(self._recent_quotes) > 200:
            self._recent_quotes = self._recent_quotes[-200:]

        self._persist_quote(quote, disposition=disposition.value, is_accepted=True, reason=reason)
        self.monitor_open_opportunities_on_quote(quote)
        return True, "ACCEPTED", quote

    def _persist_quote(
        self,
        quote: CanonicalQuote,
        *,
        disposition: str,
        is_accepted: bool,
        reason: Optional[str],
    ) -> None:
        if self._storage and self._storage.is_initialized:
            digest = self.quote_ordering.compute_quote_digest(quote)
            ev_id = f"sq_{quote.symbol}_{quote.exchange_ts_ms or quote.received_at_ms}_{digest[:10]}_{self.quotes_received}"
            self._storage.record_quote(
                {
                    "source_event_id": ev_id,
                    "symbol": quote.symbol,
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "last": quote.last,
                    "mark_price": quote.mark_price,
                    "spread": quote.spread,
                    "exchange_ts_ms": quote.exchange_ts_ms or 0,
                    "received_at_ms": quote.received_at_ms,
                    "freshness_sec": quote.freshness_sec,
                    "source": quote.source,
                    "connection_epoch": quote.connection_epoch,
                    "disposition": disposition,
                    "status": quote.status,
                    "is_valid": is_accepted and quote.is_valid,
                    "rejection_code": quote.rejection_code or (None if is_accepted else reason),
                }
            )

    # =========================================================================
    # PHASE C.3 — SAFE PUBLIC REST TICKER FALLBACK (NO CANDLE SYNTHESIS)
    # =========================================================================

    def synthesize_candle_from_ticker(self, *args: Any, **kwargs: Any) -> None:
        """
        Phase C.3 negative control: REST ticker snapshots must NEVER be converted
        into synthetic OHLCV candles.
        """
        raise RuntimeError(
            "REST_FALLBACK_CANDLE_SYNTHESIS_FORBIDDEN: "
            "Converting REST ticker snapshots into synthetic OHLCV candles is strictly prohibited."
        )

    async def poll_public_rest_ticker_fallback(
        self,
        *,
        min_interval_ms: int = 2500,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> Tuple[bool, str, Optional[CanonicalQuote]]:
        """
        Controlled public-only REST ticker fallback.
        - Uses public Bitget ticker endpoint only (no credentials, no order endpoints).
        - Explicit source='REST_FALLBACK'.
        - Rate-limited by min_interval_ms.
        - Uses exchange timestamp `ts` from payload; never fabricates timestamps.
        - Never synthesizes candles from ticker.
        """
        now_ms = int(time.time() * 1000)
        if self._last_rest_poll_ms > 0 and (now_ms - self._last_rest_poll_ms) < min_interval_ms:
            return False, "REST_FALLBACK_RATE_LIMITED", None

        self._last_rest_poll_ms = now_ms
        self.rest_fallback_activations += 1

        owns_client = http_client is None
        client = http_client or httpx.AsyncClient(timeout=5.0, trust_env=False)
        try:
            resp = await client.get(
                BITGET_PUBLIC_TICKER_URL,
                params={"symbol": self.symbol, "productType": "USDT-FUTURES"},
            )
            if resp.status_code != 200:
                return False, f"REST_HTTP_{resp.status_code}", None
            payload = resp.json()
            if payload.get("code") != "00000" or not payload.get("data"):
                return False, f"REST_API_ERROR:{payload.get('code')}", None

            item = payload["data"][0]
            ex_ts = item.get("ts")
            if ex_ts is None:
                self.quotes_received += 1
                self.quotes_rejected += 1
                return False, "REST_MISSING_EXCHANGE_TIMESTAMP", None

            raw_quote = {
                "symbol": item.get("symbol", self.symbol),
                "instType": "USDT-FUTURES",
                "bid": item.get("bidPr"),
                "ask": item.get("askPr"),
                "last": item.get("lastPr"),
                "mark_price": item.get("markPrice"),
                "exchange_ts_ms": int(ex_ts),
                "received_at_ms": int(time.time() * 1000),
            }
            return self.ingest_raw_quote(
                raw_quote,
                source="REST_FALLBACK",
                connection_epoch=self.active_connection_epoch,
            )
        except Exception as exc:
            logger.debug("Public REST ticker fallback error: %s", exc)
            return False, f"REST_EXCEPTION:{exc}", None
        finally:
            if owns_client:
                await client.aclose()

    # =========================================================================
    # PHASE C.4 — MULTI-TIMEFRAME CANDLE INTEGRITY (M1 / M5 / M15 + HTF)
    # =========================================================================

    @staticmethod
    def normalize_timeframe(timeframe: str) -> Optional[str]:
        return TIMEFRAME_CANONICAL_MAP.get(str(timeframe or "").strip())

    def ingest_candle_payload(
        self,
        *,
        symbol: str,
        timeframe: str,
        data_rows: Sequence[Any],
        action: str = "update",
        source: str = "WS",
        connection_epoch: Optional[int] = None,
        received_at_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Ingests one or more OHLCV candle rows from Bitget WS or verified historical source.
        - Rejects REST_FALLBACK ticker-derived synthetic candles.
        - Validates symbol, timeframe, positive finite OHLC, non-negative volume, and high/low geometry.
        - Separates provisional (in-progress) bars from confirmed closed bars (`is_closed=True`).
        - Finalizes previous provisional bar as `is_closed=True` when a newer interval timestamp arrives.
        - Tracks revisions (cumulative volume replacement, never `+=`), duplicates, and missing intervals.
        - Never fabricates missing historical bars.
        """
        if source == "REST_TICKER_SYNTHETIC":
            self.synthesize_candle_from_ticker()

        norm_sym = str(symbol or "").strip().upper()
        norm_tf = self.normalize_timeframe(timeframe)
        eff_now = received_at_ms if received_at_ms is not None else int(time.time() * 1000)
        eff_epoch = connection_epoch if connection_epoch is not None else self.active_connection_epoch

        if norm_sym != self.symbol or norm_tf is None:
            return {
                "accepted": 0,
                "closed": 0,
                "revised": 0,
                "rejected": len(data_rows or ()),
                "reason": "INVALID_SYMBOL_OR_TIMEFRAME",
            }

        tf_ms = TIMEFRAME_DURATION_MS[norm_tf]
        key = (norm_sym, norm_tf)
        closed_list = self._closed_candles.setdefault(key, [])

        parsed_rows: List[Dict[str, Any]] = []
        rejected_count = 0

        for raw in data_rows or ():
            self.candles_received += 1
            validated = self._validate_raw_candle(raw, tf_ms=tf_ms)
            if validated is None:
                rejected_count += 1
                continue
            parsed_rows.append(validated)

        parsed_rows.sort(key=lambda r: r["open_time"])

        closed_in_batch: List[ShadowCandleRecord] = []
        revised_count = 0
        accepted_count = 0

        for idx, row in enumerate(parsed_rows):
            open_time = row["open_time"]
            close_time = open_time + tf_ms
            has_newer_row_in_batch = idx < (len(parsed_rows) - 1) and parsed_rows[idx + 1]["open_time"] > open_time
            explicit_closed = row.get("is_closed")
            should_persist_row = len(parsed_rows) <= 30 or idx >= (len(parsed_rows) - 5)

            # Check if a provisional bar exists with an older open_time -> advancing timestamp closes it!
            prov = self._provisional_candles.get(key)
            if prov is not None and open_time > prov.open_time:
                prov.is_closed = True
                self._promote_closed_candle(prov, persist=should_persist_row)
                closed_in_batch.append(prov)
                del self._provisional_candles[key]
                prov = None

            # Determine if this row itself is closed
            if explicit_closed is True or has_newer_row_in_batch:
                is_closed = True
            elif explicit_closed is False:
                is_closed = False
            else:
                # In Bitget WS snapshot/update without explicit is_closed:
                # A bar whose close_time <= eff_now and is followed by newer bars is closed;
                # the newest bar in a live WS update is provisional unless close_time <= eff_now and action == "snapshot_closed"
                is_closed = bool(action == "closed" or (action == "snapshot" and close_time <= eff_now))

            digest = self._compute_candle_digest(norm_sym, norm_tf, row)
            digest_key = (norm_sym, norm_tf, open_time)

            # Check if bar is already in closed_list
            existing_closed = next((b for b in reversed(closed_list) if b.open_time == open_time), None)
            if existing_closed is not None:
                if self._candle_digests.get(digest_key) == digest:
                    # Identical duplicate candle
                    continue
                # Legitimate revision of closed bar (replaces cumulative volume)
                existing_closed.open = row["open"]
                existing_closed.high = row["high"]
                existing_closed.low = row["low"]
                existing_closed.close = row["close"]
                existing_closed.volume = row["volume"]
                existing_closed.revision += 1
                existing_closed.received_at = eff_now
                self._candle_digests[digest_key] = digest
                self.candles_revised += 1
                revised_count += 1
                accepted_count += 1
                self._persist_closed_candle(existing_closed)
                continue

            if is_closed:
                rec = ShadowCandleRecord(
                    symbol=norm_sym,
                    timeframe=norm_tf,
                    open_time=open_time,
                    close_time=close_time,
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"],
                    is_closed=True,
                    source=source,
                    received_at=eff_now,
                    revision=1,
                    connection_epoch=int(eff_epoch),
                )
                self._candle_digests[digest_key] = digest
                self._promote_closed_candle(rec, persist=should_persist_row)
                closed_in_batch.append(rec)
                accepted_count += 1
            else:
                # Provisional in-progress candle
                if prov is not None and prov.open_time == open_time:
                    if self._candle_digests.get(digest_key) != digest:
                        prov.open = row["open"]
                        prov.high = row["high"]
                        prov.low = row["low"]
                        prov.close = row["close"]
                        prov.volume = row["volume"]  # Replace cumulative volume, never +=
                        prov.revision += 1
                        prov.received_at = eff_now
                        self._candle_digests[digest_key] = digest
                        self.candles_revised += 1
                        revised_count += 1
                        accepted_count += 1
                else:
                    self._provisional_candles[key] = ShadowCandleRecord(
                        symbol=norm_sym,
                        timeframe=norm_tf,
                        open_time=open_time,
                        close_time=close_time,
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row["volume"],
                        is_closed=False,
                        source=source,
                        received_at=eff_now,
                        revision=1,
                        connection_epoch=int(eff_epoch),
                    )
                    self._candle_digests[digest_key] = digest
                    accepted_count += 1

        return {
            "accepted": accepted_count,
            "closed": len(closed_in_batch),
            "revised": revised_count,
            "rejected": rejected_count,
            "closed_bars": [b.to_dict() for b in closed_in_batch],
        }

    def _validate_raw_candle(self, raw: Any, *, tf_ms: int) -> Optional[Dict[str, Any]]:
        is_closed_flag: Optional[bool] = None
        if isinstance(raw, dict):
            try:
                open_time = int(raw.get("open_time") or raw.get("timestamp") or raw.get("ts") or 0)
                o = float(raw.get("open") if "open" in raw else raw.get("o"))
                h = float(raw.get("high") if "high" in raw else raw.get("h"))
                l = float(raw.get("low") if "low" in raw else raw.get("l"))
                c = float(raw.get("close") if "close" in raw else raw.get("c"))
                vol = float(raw.get("volume") if "volume" in raw else (raw.get("vol") or raw.get("v") or 0.0))
                if "is_closed" in raw and isinstance(raw["is_closed"], bool):
                    is_closed_flag = raw["is_closed"]
            except (ValueError, TypeError):
                return None
        elif isinstance(raw, (list, tuple)) and len(raw) >= 5:
            try:
                open_time = int(raw[0])
                o = float(raw[1])
                h = float(raw[2])
                l = float(raw[3])
                c = float(raw[4])
                vol = float(raw[5]) if len(raw) > 5 and raw[5] is not None and str(raw[5]).strip() != "" else 0.0
                if len(raw) > 6 and isinstance(raw[6], bool):
                    is_closed_flag = raw[6]
            except (ValueError, TypeError):
                return None
        elif hasattr(raw, "timestamp") and hasattr(raw, "open") and hasattr(raw, "close"):
            try:
                open_time = int(getattr(raw, "open_time", None) or getattr(raw, "timestamp", 0))
                o = float(raw.open)
                h = float(raw.high)
                l = float(raw.low)
                c = float(raw.close)
                vol = float(getattr(raw, "volume", 0.0) or 0.0)
                if hasattr(raw, "is_closed") and isinstance(raw.is_closed, bool):
                    is_closed_flag = raw.is_closed
            except (ValueError, TypeError):
                return None
        else:
            return None

        if open_time <= 0:
            return None
        for v in (o, h, l, c, vol):
            if isinstance(v, bool) or not math.isfinite(v):
                return None
        if o <= 0.0 or h <= 0.0 or l <= 0.0 or c <= 0.0 or vol < 0.0:
            return None
        if h < max(o, c, l) - 1e-6 or l > min(o, c, h) + 1e-6 or h < l:
            return None

        return {
            "open_time": open_time,
            "close_time": open_time + tf_ms,
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": round(vol, 4),
            "is_closed": is_closed_flag,
        }

    @staticmethod
    def _compute_candle_digest(symbol: str, timeframe: str, row: Dict[str, Any]) -> str:
        payload = {
            "s": symbol,
            "tf": timeframe,
            "t": row["open_time"],
            "o": row["open"],
            "h": row["high"],
            "l": row["low"],
            "c": row["close"],
            "v": row["volume"],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def _promote_closed_candle(self, rec: ShadowCandleRecord, *, persist: bool = True) -> None:
        key = (rec.symbol, rec.timeframe)
        closed_list = self._closed_candles.setdefault(key, [])
        tf_ms = TIMEFRAME_DURATION_MS[rec.timeframe]

        if closed_list:
            last_closed = closed_list[-1]
            if rec.open_time > last_closed.open_time:
                delta = rec.open_time - last_closed.open_time
                if delta > tf_ms:
                    missing_bars = int(delta // tf_ms) - 1
                    if missing_bars > 0:
                        self.candles_missing += missing_bars
                        self._missing_intervals_by_tf[rec.timeframe] = (
                            self._missing_intervals_by_tf.get(rec.timeframe, 0) + missing_bars
                        )
                        if rec.timeframe in ("1M", "5M", "15M"):
                            self.has_data_gap = True
                closed_list.append(rec)
            elif rec.open_time < last_closed.open_time:
                # Out-of-order historical bar insertion
                closed_list.append(rec)
                closed_list.sort(key=lambda b: b.open_time)
        else:
            closed_list.append(rec)

        if len(closed_list) > self.max_candles_per_tf:
            self._closed_candles[key] = closed_list[-self.max_candles_per_tf:]

        self.candles_closed += 1
        if rec.timeframe == "1M":
            self.last_closed_m1_timestamp = max(self.last_closed_m1_timestamp or 0, rec.close_time)
        elif rec.timeframe == "5M":
            self.last_closed_m5_timestamp = max(self.last_closed_m5_timestamp or 0, rec.close_time)
        elif rec.timeframe == "15M":
            self.last_closed_m15_timestamp = max(self.last_closed_m15_timestamp or 0, rec.close_time)

        if persist:
            self._persist_closed_candle(rec)
        self.monitor_open_opportunities_on_closed_candle(rec)

    def _persist_closed_candle(self, rec: ShadowCandleRecord) -> None:
        if self._storage and self._storage.is_initialized:
            self._storage.record_closed_candle(rec.to_dict())

    def get_closed_candles(
        self,
        timeframe: str,
        *,
        symbol: Optional[str] = None,
        as_of_ms: Optional[int] = None,
        limit: int = 250,
    ) -> List[ShadowCandleRecord]:
        """
        Returns strictly confirmed closed candles (`is_closed=True`) with `close_time <= as_of_ms`.
        Provisional in-progress bars are NEVER included.
        """
        norm_sym = (symbol or self.symbol).upper()
        norm_tf = self.normalize_timeframe(timeframe)
        if norm_tf is None:
            return []
        bars = self._closed_candles.get((norm_sym, norm_tf), [])
        filtered = [
            b for b in bars
            if b.is_closed is True and (as_of_ms is None or b.close_time <= as_of_ms)
        ]
        return filtered[-limit:]

    def get_provisional_candle(self, timeframe: str, *, symbol: Optional[str] = None) -> Optional[ShadowCandleRecord]:
        norm_sym = (symbol or self.symbol).upper()
        norm_tf = self.normalize_timeframe(timeframe)
        if norm_tf is None:
            return None
        return self._provisional_candles.get((norm_sym, norm_tf))

    # =========================================================================
    # PHASE E — CONTINUOUS SMC / ICT UNICORN SHADOW ANALYSIS
    # =========================================================================

    def evaluate_shadow_cycle(
        self,
        *,
        as_of_ms: Optional[int] = None,
        closed_1m: Optional[Sequence[Any]] = None,
        closed_5m: Optional[Sequence[Any]] = None,
        closed_15m: Optional[Sequence[Any]] = None,
        closed_1h: Optional[Sequence[Any]] = None,
        closed_4h: Optional[Sequence[Any]] = None,
        htf_bias: Optional[str] = None,
        h1_alignment: Optional[str] = None,
        quote: Optional[CanonicalQuote] = None,
        policy: Optional[StopPlacementPolicy] = None,
        stop_policy: Optional[StopPlacementPolicy] = None,
        scalp_config: Optional[M1ScalpConfig] = None,
    ) -> Dict[str, Any]:
        """
        Executes a complete read-only SMC / ICT Unicorn shadow analysis cycle at `as_of_ms`.
        - Uses only confirmed closed candles (`is_closed=True` and `close_time <= decision_cutoff_ms`).
        - Rejects unclosed M1/M5/M15 candles from establishing structure or setups.
        - Rejects stale or missing quotes from creating confirmed Unicorn evidence or eligible setups.
        - Evaluates all 5 supported models (`SMC_M1_FVG_CONTINUATION`, `SMC_M1_CONTINUATION`,
          `SMC_M1_REVERSAL`, `SMC_M1_SWEEP_RECLAIM`, `CG01`).
        - Runs V17.8 Unicorn detector, structural SL planner, independent liquidity TP selector,
          authoritative risk calculator, and matched A/B/C/D comparison.
        - Never creates or mutates PAPER or LIVE orders.
        """
        cutoff_ms = int(as_of_ms if as_of_ms is not None else time.time() * 1000)
        analysis_time_ms = int(time.time() * 1000)
        cfg = scalp_config or get_scalp_config()
        stop_pol = policy or stop_policy or StopPlacementPolicy(
            mode=StopPlacementMode.SHADOW_COMPARE.value,
            v18_unicorn_detection_enabled=True,
            v18_structural_intelligence_enabled=True,
        )

        # 1. Filter and validate strictly closed candles <= cutoff_ms
        raw_1m = closed_1m if closed_1m is not None else self.get_closed_candles("1M", as_of_ms=cutoff_ms, limit=250)
        raw_5m = closed_5m if closed_5m is not None else self.get_closed_candles("5M", as_of_ms=cutoff_ms, limit=150)
        raw_15m = closed_15m if closed_15m is not None else self.get_closed_candles("15M", as_of_ms=cutoff_ms, limit=100)
        raw_1h = closed_1h if closed_1h is not None else self.get_closed_candles("1H", as_of_ms=cutoff_ms, limit=60)
        raw_4h = closed_4h if closed_4h is not None else self.get_closed_candles("4H", as_of_ms=cutoff_ms, limit=40)

        bars_1m, unclosed_1m_rejected = self._filter_strict_closed_bars(raw_1m, "1M", cutoff_ms)
        bars_5m, unclosed_5m_rejected = self._filter_strict_closed_bars(raw_5m, "5M", cutoff_ms)
        bars_15m, unclosed_15m_rejected = self._filter_strict_closed_bars(raw_15m, "15M", cutoff_ms)
        bars_1h, _ = self._filter_strict_closed_bars(raw_1h, "1H", cutoff_ms)
        bars_4h, _ = self._filter_strict_closed_bars(raw_4h, "4H", cutoff_ms)

        # 2. Quote freshness & validity check
        eff_quote = quote if quote is not None else self.latest_quote
        quote_valid = False
        quote_stale = True
        quote_reason = "QUOTE_MISSING"
        if eff_quote is not None:
            q_ts = eff_quote.exchange_ts_ms or eff_quote.received_at_ms or 0
            age_sec = max(0.0, (cutoff_ms - q_ts) / 1000.0) if q_ts > 0 else 9999.0
            quote_stale = bool(not eff_quote.is_valid or eff_quote.status == "STALE" or age_sec > 15.0)
            quote_valid = bool(
                eff_quote.is_valid
                and not quote_stale
                and eff_quote.bid > 0
                and eff_quote.ask >= eff_quote.bid
                and math.isfinite(eff_quote.bid)
                and math.isfinite(eff_quote.ask)
            )
            quote_reason = "VALID" if quote_valid else ("TICKER_STALE" if quote_stale else (eff_quote.rejection_code or "QUOTE_INVALID"))

        cycle_id = f"sh_cycle_{self.symbol}_{cutoff_ms}_{len(self._analysis_cycles) + 1}"
        model_outcomes: Dict[str, Dict[str, Any]] = {}
        cycle_opportunities: List[Dict[str, Any]] = []
        cycle_unicorns: List[Dict[str, Any]] = []

        # 3. Check if unclosed M5 or M1 candle attempted to authorize setup or if data is missing/stale
        if not quote_valid or len(bars_1m) < 15 or len(bars_5m) < 3:
            missing_reason = (
                "STALE_OR_INVALID_QUOTE"
                if not quote_valid
                else ("UNCLOSED_M5_CANDLE_REJECTED" if unclosed_5m_rejected and len(bars_5m) < 3 else "INSUFFICIENT_CLOSED_CANDLES")
            )
            from services.shared_strategy_evaluator import EVALUATOR_VERSION
            for mid in SUPPORTED_SHADOW_MODELS:
                is_dis = mid in self._disabled_models or not self._is_model_enabled_by_config(mid, cfg)
                sh_ctx = self.build_shared_decision_context(
                    decision_timestamp_ms=cutoff_ms,
                    m1_candles=bars_1m,
                    m5_candles=bars_5m,
                    m15_candles=bars_15m,
                    h1_candles=bars_1h,
                    h4_candles=bars_4h,
                    bid=eff_quote.bid if eff_quote is not None and quote_valid else None,
                    ask=eff_quote.ask if eff_quote is not None and quote_valid else None,
                    quote_timestamp_ms=(eff_quote.exchange_ts_ms or eff_quote.received_at_ms) if eff_quote is not None else None,
                    model_enablement={mid: False} if is_dis else None,
                )
                sh_snap = self.evaluate_shared_decision_snapshot(
                    context=sh_ctx,
                    model_id=mid,
                    direction="LONG",
                )
                sh_snap_dict = sh_snap.to_parity_dict() if hasattr(sh_snap, "to_parity_dict") else None
                if is_dis:
                    model_outcomes[mid] = {
                        "model_id": mid,
                        "classification": ShadowModelClassification.MODEL_DISABLED.value,
                        "reason_code": "MODEL_DISABLED",
                        "shared_decision_fingerprint": getattr(sh_snap, "decision_fingerprint", ""),
                        "shared_decision_snapshot": sh_snap_dict,
                        "evaluator_version": getattr(sh_snap, "evaluator_version", EVALUATOR_VERSION),
                    }
                else:
                    model_outcomes[mid] = {
                        "model_id": mid,
                        "classification": ShadowModelClassification.MISSING_MARKET_DATA.value,
                        "reason_code": missing_reason,
                        "shared_decision_fingerprint": getattr(sh_snap, "decision_fingerprint", ""),
                        "shared_decision_snapshot": sh_snap_dict,
                        "evaluator_version": getattr(sh_snap, "evaluator_version", EVALUATOR_VERSION),
                    }
            summary = self._finalize_cycle_summary(
                cycle_id=cycle_id,
                analysis_time_ms=analysis_time_ms,
                cutoff_ms=cutoff_ms,
                feed_status=self.get_feed_status(now_ms=cutoff_ms),
                quote=eff_quote,
                bars_1m=bars_1m,
                bars_5m=bars_5m,
                bars_15m=bars_15m,
                model_outcomes=model_outcomes,
                opportunities=cycle_opportunities,
                unicorns=cycle_unicorns,
                unclosed_m5_rejected=unclosed_5m_rejected,
            )
            return summary

        # 4. Compute authoritative M1 & M5 ATR (SMA_TR_V1)
        atr_m1_obj = compute_stop_atr(
            bars_1m,
            period=stop_pol.atr_period,
            timeframe="1M",
            as_of_ms=cutoff_ms,
            symbol=self.symbol,
        )
        atr_m5_obj = compute_stop_atr(
            bars_5m,
            period=min(stop_pol.atr_period, max(2, len(bars_5m) - 1)) if len(bars_5m) < stop_pol.atr_period + 1 else stop_pol.atr_period,
            timeframe="5M",
            as_of_ms=cutoff_ms,
            symbol=self.symbol,
        )
        # Strict 14-period check for M5 when full history is present; if M5 has < 15 bars, also compute standard 14-period for strict authority check
        strict_atr_m5_obj = compute_stop_atr(
            bars_5m,
            period=stop_pol.atr_period,
            timeframe="5M",
            as_of_ms=cutoff_ms,
            symbol=self.symbol,
        )

        # 5. Derive HTF/M5/M15 directional bias and liquidity context from confirmed closed candles
        derived_bias, derived_align = self._derive_multi_tf_bias(bars_1m, bars_5m, bars_15m)
        htf_bias = htf_bias if htf_bias is not None else derived_bias
        h1_alignment = h1_alignment if h1_alignment is not None else derived_align
        sh_1m, sl_1m = identify_confirmed_swings(bars_1m, timeframe="1M", left_bars=2, right_bars=2, as_of_ms=cutoff_ms)
        sh_5m, sl_5m = identify_confirmed_swings(bars_5m, timeframe="5M", left_bars=1, right_bars=1, as_of_ms=cutoff_ms)
        liquidity_levels = build_liquidity_map(
            symbol=self.symbol,
            timeframe="1M",
            swing_highs=sh_1m + sh_5m,
            swing_lows=sl_1m + sl_5m,
            now_ms=cutoff_ms,
        )

        # 6. Evaluate Unicorn evidence for LONG and SHORT on confirmed M1 bars (strict causal binding)
        atr_m1_for_scan = float(atr_m1_obj.value) if (atr_m1_obj and getattr(atr_m1_obj, "status", None) == "AVAILABLE") else 1.0
        for u_dir in ("LONG", "SHORT"):
            scan_setup = self._detect_model_setup_evidence(
                model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
                direction=u_dir,
                bars_1m=bars_1m,
                sh_1m=sh_1m,
                sl_1m=sl_1m,
                atr_m1=atr_m1_for_scan,
                cfg=cfg,
                cutoff_ms=cutoff_ms,
                strict_direction=True,
            )
            u_ev = detect_unicorn_evidence(
                symbol=self.symbol,
                direction=u_dir,
                closed_bars=bars_1m,
                as_of_ms=cutoff_ms,
                selected_fvg=scan_setup.get("selected_fvg"),
                selected_break=scan_setup.get("selected_break"),
                selected_reference=scan_setup.get("selected_reference"),
                confirmed_swings=(sh_1m, sl_1m),
                tick_size=cfg.tick_size,
                strict_causal_binding=True,
            )
            u_dict = u_ev.to_dict()
            if u_ev.is_valid or u_ev.validity_status not in (
                UnicornSetupStatus.MISSING_BREAKER.value,
                UnicornSetupStatus.MISSING_FVG.value,
            ):
                cycle_unicorns.append(u_dict)
                self._unicorn_records.append(u_dict)
                if len(self._unicorn_records) > 200:
                    self._unicorn_records = self._unicorn_records[-200:]
                if self._storage and self._storage.is_initialized:
                    self._storage.record_unicorn_evidence(u_dict)

        # 7. Evaluate each supported model
        policy_settings = {
            "capital_usdt": 1000.0,
            "risk_pct": 0.25,
            "leverage": 20,
            "margin_mode": "ISOLATED",
            "min_net_rr": cfg.min_net_rr,
            "min_buffer_ticks": stop_pol.min_buffer_ticks,
            "costs": CostAssumptions(slippage_usd=0.10, maker_fee_rate=0.0002, taker_fee_rate=0.0004),
        }

        from services.shared_strategy_evaluator import EVALUATOR_VERSION
        for model_id in SUPPORTED_SHADOW_MODELS:
            if model_id in self._disabled_models or not self._is_model_enabled_by_config(model_id, cfg):
                dis_ctx = self.build_shared_decision_context(
                    decision_timestamp_ms=cutoff_ms,
                    m1_candles=bars_1m,
                    m5_candles=bars_5m,
                    m15_candles=bars_15m,
                    h1_candles=bars_1h,
                    h4_candles=bars_4h,
                    bid=eff_quote.bid,
                    ask=eff_quote.ask,
                    quote_timestamp_ms=eff_quote.exchange_ts_ms or eff_quote.received_at_ms,
                    liquidity_pools=liquidity_levels,
                    htf_bias=htf_bias,
                    h1_alignment=h1_alignment,
                    model_enablement={model_id: False},
                )
                dis_snap = self.evaluate_shared_decision_snapshot(
                    context=dis_ctx,
                    model_id=model_id,
                    direction="LONG" if htf_bias == "BULLISH" else "SHORT",
                )
                dis_snap_dict = dis_snap.to_parity_dict() if hasattr(dis_snap, "to_parity_dict") else None
                model_outcomes[model_id] = {
                    "model_id": model_id,
                    "classification": ShadowModelClassification.MODEL_DISABLED.value,
                    "reason_code": "MODEL_DISABLED",
                    "shared_decision_fingerprint": getattr(dis_snap, "decision_fingerprint", ""),
                    "shared_decision_snapshot": dis_snap_dict,
                    "evaluator_version": getattr(dis_snap, "evaluator_version", EVALUATOR_VERSION),
                }
                continue

            opp = self._evaluate_single_model_shadow(
                model_id=model_id,
                bars_1m=bars_1m,
                bars_5m=bars_5m,
                bars_15m=bars_15m,
                bars_1h=bars_1h,
                bars_4h=bars_4h,
                quote=eff_quote,
                htf_bias=htf_bias,
                h1_alignment=h1_alignment,
                sh_1m=sh_1m,
                sl_1m=sl_1m,
                liquidity_levels=liquidity_levels,
                atr_m1_obj=atr_m1_obj,
                atr_m5_obj=strict_atr_m5_obj,
                stop_policy=stop_pol,
                policy_settings=policy_settings,
                cfg=cfg,
                cutoff_ms=cutoff_ms,
                analysis_time_ms=analysis_time_ms,
            )
            model_outcomes[model_id] = {
                "model_id": model_id,
                "classification": opp["model_classification"],
                "reason_code": opp["primary_reason_code"],
                "opportunity_id": opp.get("opportunity_id"),
                "shared_decision_fingerprint": opp.get("decision_fingerprint"),
                "shared_decision_snapshot": opp.get("shared_decision_snapshot"),
                "evaluator_version": (opp.get("shared_decision_snapshot") or {}).get("evaluator_version", "17.9.0.1.1"),
            }
            if opp.get("opportunity_record") is not None:
                opp_rec = opp["opportunity_record"]
                cycle_opportunities.append(opp_rec)
                self._register_or_update_opportunity(opp_rec)

        return self._finalize_cycle_summary(
            cycle_id=cycle_id,
            analysis_time_ms=analysis_time_ms,
            cutoff_ms=cutoff_ms,
            feed_status=self.get_feed_status(now_ms=cutoff_ms),
            quote=eff_quote,
            bars_1m=bars_1m,
            bars_5m=bars_5m,
            bars_15m=bars_15m,
            model_outcomes=model_outcomes,
            opportunities=cycle_opportunities,
            unicorns=cycle_unicorns,
            unclosed_m5_rejected=unclosed_5m_rejected,
        )

    def _filter_strict_closed_bars(
        self,
        bars: Sequence[Any],
        timeframe: str,
        cutoff_ms: int,
    ) -> Tuple[List[ShadowCandleRecord], bool]:
        """
        Filters input bars to strictly confirmed closed candles (`is_closed=True` and `close_time <= cutoff_ms`).
        Returns (valid_closed_bars, had_unclosed_or_future_bars_rejected).
        """
        tf_ms = TIMEFRAME_DURATION_MS[timeframe]
        valid: List[ShadowCandleRecord] = []
        rejected_unclosed = False

        for b in bars or ():
            if isinstance(b, ShadowCandleRecord):
                if not b.is_closed or b.close_time > cutoff_ms:
                    rejected_unclosed = True
                    continue
                valid.append(b)
                continue

            is_closed = getattr(b, "is_closed", None) if not isinstance(b, dict) else b.get("is_closed")
            if is_closed is False:
                rejected_unclosed = True
                continue

            v = self._validate_raw_candle(b, tf_ms=tf_ms)
            if v is None:
                continue
            if v["close_time"] > cutoff_ms:
                rejected_unclosed = True
                continue
            valid.append(
                ShadowCandleRecord(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    open_time=v["open_time"],
                    close_time=v["close_time"],
                    open=v["open"],
                    high=v["high"],
                    low=v["low"],
                    close=v["close"],
                    volume=v["volume"],
                    is_closed=True,
                    source="VERIFIED_CLOSED",
                    received_at=cutoff_ms,
                )
            )

        valid.sort(key=lambda r: r.open_time)
        return valid, rejected_unclosed

    @staticmethod
    def _is_model_enabled_by_config(model_id: str, cfg: M1ScalpConfig) -> bool:
        if model_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value:
            return bool(getattr(cfg, "fvg_continuation_enabled", True))
        if model_id == ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value:
            return bool(getattr(cfg, "sweep_reclaim_enabled", True))
        if model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value:
            return bool(getattr(cfg, "cg01_enabled", True))
        return True

    @staticmethod
    def _derive_multi_tf_bias(
        bars_1m: Sequence[ShadowCandleRecord],
        bars_5m: Sequence[ShadowCandleRecord],
        bars_15m: Sequence[ShadowCandleRecord],
    ) -> Tuple[str, str]:
        ref_bars = bars_15m if len(bars_15m) >= 3 else (bars_5m if len(bars_5m) >= 3 else bars_1m)
        if len(ref_bars) < 3:
            return "UNKNOWN", "UNKNOWN"
        first_c = ref_bars[max(0, len(ref_bars) - 10)].close
        last_c = ref_bars[-1].close
        if last_c > first_c:
            return "BULLISH", "BULLISH"
        if last_c < first_c:
            return "BEARISH", "BEARISH"
        return "BULLISH", "ALIGNED"

    def _evaluate_single_model_shadow(
        self,
        *,
        model_id: str,
        bars_1m: List[ShadowCandleRecord],
        bars_5m: List[ShadowCandleRecord],
        bars_15m: List[ShadowCandleRecord],
        bars_1h: Optional[List[ShadowCandleRecord]] = None,
        bars_4h: Optional[List[ShadowCandleRecord]] = None,
        quote: CanonicalQuote,
        htf_bias: str,
        h1_alignment: str,
        sh_1m: List[Dict[str, Any]],
        sl_1m: List[Dict[str, Any]],
        liquidity_levels: List[Any],
        atr_m1_obj: Any,
        atr_m5_obj: Any,
        stop_policy: StopPlacementPolicy,
        policy_settings: Dict[str, Any],
        cfg: M1ScalpConfig,
        cutoff_ms: int,
        analysis_time_ms: int,
    ) -> Dict[str, Any]:
        """
        Evaluates a single model in shadow mode using SharedStrategyEvaluator.evaluate_decision(...)
        as the single authoritative decision source (Section 4, V17.9.0.1.1).
        """
        from services.shared_strategy_evaluator import (
            DecisionStatus,
            NON_EXECUTABLE_DECISION_STATUSES,
            SharedStrategyEvaluator,
            StrategyDecisionSnapshot,
            validate_canonical_executability,
        )

        shared_ctx = self.build_shared_decision_context(
            decision_timestamp_ms=cutoff_ms,
            m1_candles=bars_1m,
            m5_candles=bars_5m,
            m15_candles=bars_15m,
            h1_candles=bars_1h,
            h4_candles=bars_4h,
            bid=quote.bid,
            ask=quote.ask,
            quote_timestamp_ms=quote.exchange_ts_ms or quote.received_at_ms,
            liquidity_pools=liquidity_levels,
            htf_bias=htf_bias,
            h1_alignment=h1_alignment,
            policy_config={
                "min_net_rr": float(getattr(cfg, "min_net_rr", 1.8)),
                "tick_size": float(getattr(cfg, "tick_size", 0.01)),
            },
            risk_config={
                "capital_usdt": float(policy_settings.get("capital_usdt", 1000.0)),
                "risk_pct": float(policy_settings.get("risk_pct", 0.25)),
                "leverage": int(policy_settings.get("leverage", 20)),
                "margin_mode": str(policy_settings.get("margin_mode", "ISOLATED")),
                "min_net_rr": float(getattr(cfg, "min_net_rr", 1.8)),
            },
        )
        initial_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(shared_ctx, htf_bias)

        # Invoke canonical SharedStrategyEvaluator.evaluate_decision with fail-closed exception handling
        try:
            shared_snap = self.evaluate_shared_decision_snapshot(
                context=shared_ctx,
                model_id=model_id,
                direction=initial_dir,
            )
            if (
                isinstance(shared_snap, StrategyDecisionSnapshot)
                and shared_snap.decision_status not in (
                    DecisionStatus.ELIGIBLE.value,
                    DecisionStatus.MODEL_DISABLED.value,
                    DecisionStatus.MISSING_MARKET_DATA.value,
                )
            ):
                alt_dir = "SHORT" if initial_dir == "LONG" else "LONG"
                alt_snap = self.evaluate_shared_decision_snapshot(
                    context=shared_ctx,
                    model_id=model_id,
                    direction=alt_dir,
                )
                if isinstance(alt_snap, StrategyDecisionSnapshot) and (
                    alt_snap.decision_status == DecisionStatus.ELIGIBLE.value
                    or (shared_snap.setup_validity != "VALID" and alt_snap.setup_validity == "VALID")
                ):
                    shared_snap = alt_snap
        except Exception as exc:
            return {
                "model_classification": ShadowModelClassification.NO_VALID_SETUP.value,
                "primary_reason_code": f"CANONICAL_EVALUATOR_EXCEPTION:{type(exc).__name__}",
                "opportunity_id": None,
                "opportunity_record": None,
                "shared_decision_snapshot": None,
                "decision_fingerprint": "",
            }

        if (
            not isinstance(shared_snap, StrategyDecisionSnapshot)
            or not shared_snap.decision_id
            or not shared_snap.decision_fingerprint
        ):
            return {
                "model_classification": ShadowModelClassification.NO_VALID_SETUP.value,
                "primary_reason_code": "CANONICAL_DECISION_LINEAGE_MISSING",
                "opportunity_id": None,
                "opportunity_record": None,
                "shared_decision_snapshot": shared_snap.to_parity_dict() if hasattr(shared_snap, "to_parity_dict") else None,
                "decision_fingerprint": getattr(shared_snap, "decision_fingerprint", ""),
            }

        # Fail closed if ATR M1 is missing or unavailable
        if (
            atr_m1_obj is None
            or getattr(atr_m1_obj, "status", None) != "AVAILABLE"
            or float(getattr(atr_m1_obj, "value", 0.0) or 0.0) <= 0.0
            or "ATR_MISSING" in shared_snap.rejection_reason_codes
        ):
            opp_id = f"shopp_{model_id}_{self.symbol}_{cutoff_ms}_no_atr"
            rej_rec = self._build_opportunity_dict(
                opportunity_id=opp_id,
                model_id=model_id,
                direction=shared_snap.direction or initial_dir,
                setup_episode_id=shared_snap.setup_episode_id or f"ep_{model_id}_{cutoff_ms}",
                analysis_time_ms=analysis_time_ms,
                decision_cutoff_ms=cutoff_ms,
                quote=quote,
                entry_reference=shared_snap.entry_reference or (quote.ask if htf_bias == "BULLISH" else quote.bid),
                legacy_sl=0.0,
                structural_sl=None,
                unicorn_sl_candidate=None,
                selected_invalidation_price=None,
                selected_anchor_kind=None,
                selected_anchor_id=None,
                atr_m1=None,
                atr_m5=float(atr_m5_obj.value) if (atr_m5_obj and atr_m5_obj.status == "AVAILABLE") else None,
                protective_distance=None,
                target_price=None,
                target_evidence_id=None,
                risk_budget=float(policy_settings["capital_usdt"]) * float(policy_settings["risk_pct"]) / 100.0,
                hypothetical_quantity=0.0,
                net_risk=0.0,
                net_reward=0.0,
                net_rr=0.0,
                unicorn_validity="ATR_MISSING",
                unicorn_evidence_id=None,
                shadow_eligibility_status="REJECTED",
                lifecycle_state=ShadowOpportunityState.REJECTED.value,
                rejection_reason_codes=["ATR_MISSING", getattr(atr_m1_obj, "reason_code", "ATR_UNAVAILABLE") or "ATR_UNAVAILABLE"],
                comparison_plan=None,
            )
            rej_rec["shared_decision_snapshot"] = shared_snap.to_parity_dict()
            rej_rec["decision_id"] = shared_snap.decision_id
            rej_rec["decision_fingerprint"] = shared_snap.decision_fingerprint
            rej_rec["evidence_fingerprint"] = shared_snap.evidence_fingerprint
            rej_rec["policy_fingerprint"] = shared_snap.policy_fingerprint
            rej_rec["input_snapshot_hash"] = shared_snap.input_snapshot_hash
            rej_rec["configuration_hash"] = shared_snap.configuration_hash
            rej_rec["adapter_origin"] = shared_snap.adapter_origin
            rej_rec["evaluator_version"] = shared_snap.evaluator_version
            return {
                "model_classification": ShadowModelClassification.VALID_SETUP_REJECTED_BY_RISK.value,
                "primary_reason_code": "ATR_MISSING",
                "opportunity_id": opp_id,
                "opportunity_record": rej_rec,
                "shared_decision_snapshot": shared_snap.to_parity_dict(),
                "decision_id": shared_snap.decision_id,
                "decision_fingerprint": shared_snap.decision_fingerprint,
            }

        # Authoritative check: if canonical DecisionResult says no valid setup or NO_TRADE / REJECTED without setup
        if (
            shared_snap.setup_validity != "VALID"
            or shared_snap.decision_status in (
                DecisionStatus.NO_TRADE.value,
                DecisionStatus.REJECTED.value,
                DecisionStatus.NO_VALID_SETUP.value,
                DecisionStatus.WAITING_CONFIRMATION.value,
                DecisionStatus.MODEL_DISABLED.value,
                DecisionStatus.MISSING_MARKET_DATA.value,
            )
        ):
            primary_reason = (
                shared_snap.rejection_reason_codes[0]
                if shared_snap.rejection_reason_codes
                else (shared_snap.raw_candidate_reason_code or shared_snap.decision_status)
            )
            return {
                "model_classification": ShadowModelClassification.NO_VALID_SETUP.value,
                "primary_reason_code": primary_reason,
                "opportunity_id": None,
                "opportunity_record": None,
                "shared_decision_snapshot": shared_snap.to_parity_dict(),
                "decision_id": shared_snap.decision_id,
                "decision_fingerprint": shared_snap.decision_fingerprint,
            }

        # Populate all shadow opportunity fields strictly from canonical DecisionResult (shared_snap)
        direction = shared_snap.direction
        entry_ref = float(shared_snap.entry_reference or round(float(quote.ask if direction == "LONG" else quote.bid), 2))
        legacy_sl = float(shared_snap.legacy_sl or 0.0)
        structural_sl = float(shared_snap.selected_sl) if shared_snap.selected_sl is not None else None
        selected_sl = structural_sl if structural_sl is not None else legacy_sl
        unicorn_sl_cand = float(shared_snap.unicorn_sl_candidate) if shared_snap.unicorn_sl_candidate is not None else None
        episode_id = str(shared_snap.setup_episode_id or f"ep_{model_id}_{direction}_{cutoff_ms}")
        atr_m1_val = float(shared_snap.atr_m1 if shared_snap.atr_m1 is not None else atr_m1_obj.value)
        atr_m5_val = (
            float(shared_snap.atr_m5)
            if shared_snap.atr_m5 is not None
            else (float(atr_m5_obj.value) if (atr_m5_obj and getattr(atr_m5_obj, "status", None) == "AVAILABLE") else None)
        )
        target_price = float(shared_snap.take_profit) if shared_snap.take_profit is not None else None
        target_ev_id = shared_snap.selected_target_id
        has_valid_target = bool(
            target_price is not None
            and ((direction == "LONG" and target_price > entry_ref) or (direction == "SHORT" and target_price < entry_ref))
        )
        rejection_reasons = list(shared_snap.rejection_reason_codes)

        # Enforce directional SL/TP geometry on canonical result
        geom_valid = bool(
            selected_sl is not None
            and target_price is not None
            and ((direction == "LONG" and selected_sl < entry_ref < target_price) or (direction == "SHORT" and target_price < entry_ref < selected_sl))
        )
        if not geom_valid and shared_snap.decision_status == DecisionStatus.ELIGIBLE.value:
            rejection_reasons.append("CANONICAL_SL_TP_GEOMETRY_REJECTED")

        exec_ok, exec_rej = validate_canonical_executability(shared_snap, context=shared_ctx)
        if not exec_ok and exec_rej and exec_rej not in rejection_reasons:
            rejection_reasons.append(exec_rej)

        is_eligible = bool(
            exec_ok
            and shared_snap.decision_status == DecisionStatus.ELIGIBLE.value
            and shared_snap.decision_status not in NON_EXECUTABLE_DECISION_STATUSES
            and len(rejection_reasons) == 0
            and has_valid_target
            and geom_valid
        )

        risk_budget = round(float(policy_settings["capital_usdt"]) * float(policy_settings["risk_pct"]) / 100.0, 4)
        hypothetical_qty = float(shared_snap.hypothetical_quantity or 0.0) if has_valid_target else 0.0
        net_rr = float(shared_snap.net_rr or 0.0) if has_valid_target else 0.0
        net_risk = risk_budget if hypothetical_qty > 0 else 0.0
        net_reward = round(net_risk * net_rr, 4) if hypothetical_qty > 0 else 0.0
        comp_plan_dict = shared_snap.stop_comparison_plan_dict
        unicorn_dict = shared_snap.unicorn_evidence_dict or {}
        if self._storage and self._storage.is_initialized and unicorn_dict:
            self._storage.record_unicorn_evidence(unicorn_dict)

        unicorn_is_valid = bool(shared_snap.unicorn_validity == "VALID_UNICORN")
        opp_id = str(shared_snap.candidate_key or f"shopp_{model_id}_{self.symbol}_{direction}_{episode_id}")

        if unicorn_is_valid:
            classification = (
                ShadowModelClassification.VALID_UNICORN.value
                if is_eligible
                else ShadowModelClassification.VALID_SETUP_REJECTED_BY_RISK.value
            )
        else:
            classification = (
                ShadowModelClassification.ORDINARY_FVG_WITHOUT_UNICORN.value
                if is_eligible
                else ShadowModelClassification.VALID_SETUP_REJECTED_BY_RISK.value
            )

        opp_rec = self._build_opportunity_dict(
            opportunity_id=opp_id,
            model_id=model_id,
            direction=direction,
            setup_episode_id=episode_id,
            analysis_time_ms=analysis_time_ms,
            decision_cutoff_ms=cutoff_ms,
            quote=quote,
            entry_reference=entry_ref,
            legacy_sl=legacy_sl,
            structural_sl=structural_sl,
            unicorn_sl_candidate=unicorn_sl_cand,
            selected_invalidation_price=shared_snap.structural_invalidation_price,
            selected_anchor_kind=shared_snap.anchor_kind,
            selected_anchor_id=shared_snap.selected_anchor_id,
            atr_m1=atr_m1_val,
            atr_m5=atr_m5_val,
            protective_distance=shared_snap.protective_distance,
            target_price=target_price,
            target_evidence_id=target_ev_id,
            risk_budget=risk_budget,
            hypothetical_quantity=hypothetical_qty,
            net_risk=round(net_risk, 4),
            net_reward=round(net_reward, 4),
            net_rr=round(net_rr, 4),
            unicorn_validity=shared_snap.unicorn_validity,
            unicorn_evidence_id=unicorn_dict.get("unicorn_id"),
            shadow_eligibility_status="SHADOW_ELIGIBLE" if is_eligible else "SHADOW_REJECTED",
            lifecycle_state=ShadowOpportunityState.ELIGIBLE.value if is_eligible else ShadowOpportunityState.REJECTED.value,
            rejection_reason_codes=rejection_reasons,
            comparison_plan=comp_plan_dict,
            source_candle_ids=[f"{b.timeframe}_{b.open_time}" for b in bars_1m[-15:]],
            source_break_id=shared_snap.selected_break_id,
            source_fvg_id=shared_snap.selected_fvg_id,
            source_breaker_id=shared_snap.selected_breaker_id,
        )

        # Persist stop and target decisions to isolated shadow storage
        if self._storage and self._storage.is_initialized:
            self._storage.record_stop_decision(
                {
                    "decision_id": f"sh_sl_{opp_id}_{cutoff_ms}",
                    "opportunity_id": opp_id,
                    "symbol": self.symbol,
                    "model_id": model_id,
                    "direction": direction,
                    "decision_cutoff_ms": cutoff_ms,
                    "status": StopStatus.VALID.value if is_eligible else "REJECTED",
                    "legacy_sl": legacy_sl,
                    "structural_sl": structural_sl,
                    "unicorn_sl_candidate": unicorn_sl_cand,
                    "selected_sl": selected_sl,
                    "selected_invalidation_price": shared_snap.structural_invalidation_price,
                    "selected_anchor_kind": shared_snap.anchor_kind,
                    "selected_anchor_id": shared_snap.selected_anchor_id,
                    "atr_m1": atr_m1_val,
                    "atr_m5": atr_m5_val,
                    "spread": quote.spread,
                    "protective_distance": shared_snap.protective_distance,
                    "buffer_components": {"buffer_value": shared_snap.buffer_value} if shared_snap.buffer_value is not None else {},
                    "variants": comp_plan_dict.get("variants", {}) if comp_plan_dict else {},
                    "reason_codes": rejection_reasons,
                }
            )
            self._storage.record_target_decision(
                {
                    "target_decision_id": f"sh_tp_{opp_id}_{cutoff_ms}",
                    "opportunity_id": opp_id,
                    "symbol": self.symbol,
                    "model_id": model_id,
                    "direction": direction,
                    "decision_cutoff_ms": cutoff_ms,
                    "status": "CONFIRMED" if has_valid_target else "SHADOW_REJECTED_NO_VALID_TARGET",
                    "target_price": target_price,
                    "target_evidence_id": target_ev_id,
                    "target_kind": "LIQUIDITY_POOL" if has_valid_target else None,
                    "reason_code": "VALID_TARGET" if has_valid_target else "SHADOW_REJECTED_NO_VALID_TARGET",
                }
            )

        opp_rec["shared_decision_snapshot"] = shared_snap.to_parity_dict()
        opp_rec["decision_id"] = shared_snap.decision_id
        opp_rec["decision_fingerprint"] = shared_snap.decision_fingerprint
        opp_rec["evidence_fingerprint"] = shared_snap.evidence_fingerprint
        opp_rec["policy_fingerprint"] = shared_snap.policy_fingerprint
        opp_rec["input_snapshot_hash"] = shared_snap.input_snapshot_hash
        opp_rec["configuration_hash"] = shared_snap.configuration_hash
        opp_rec["adapter_origin"] = shared_snap.adapter_origin
        opp_rec["evaluator_version"] = shared_snap.evaluator_version

        return {
            "model_classification": classification,
            "primary_reason_code": "ELIGIBLE" if is_eligible else (rejection_reasons[0] if rejection_reasons else "REJECTED"),
            "opportunity_id": opp_id,
            "opportunity_record": opp_rec,
            "shared_decision_snapshot": shared_snap.to_parity_dict(),
            "decision_id": shared_snap.decision_id,
            "decision_fingerprint": shared_snap.decision_fingerprint,
        }

    def _detect_model_setup_evidence(
        self,
        *,
        model_id: str,
        direction: str,
        bars_1m: List[ShadowCandleRecord],
        sh_1m: List[Dict[str, Any]],
        sl_1m: List[Dict[str, Any]],
        atr_m1: float,
        cfg: M1ScalpConfig,
        cutoff_ms: int,
        strict_direction: bool = False,
    ) -> Dict[str, Any]:
        """
        Extracts causal setup evidence from confirmed M1 bars without mutating any state.
        """
        if len(bars_1m) < 10 or not sh_1m or not sl_1m:
            return {"has_setup": False, "reason_code": "WAITING_M1_STRUCTURE"}

        fvgs = smc_engine.detect_fvgs(bars_1m, timeframe="1M")
        valid_fvgs = [
            f for f in fvgs
            if isinstance(f, dict)
            and int(f.get("confirmed_at") or f.get("timestamp") or 0) <= cutoff_ms
            and float(f.get("bottom", 0.0)) < float(f.get("top", 0.0))
            and str(f.get("state", "confirmed")).lower() in ("confirmed", "partially_mitigated")
        ]

        # Check FVG continuation / Unicorn confluence setup first
        if model_id in (ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, ModelIdentifier.CG01_SWEEP_MSS_FVG.value):
            cand_dirs = (direction,) if strict_direction else (direction, "SHORT" if direction == "LONG" else "LONG")
            for cand_dir in cand_dirs:
                target_fvg_type = "BULLISH_FVG" if cand_dir == "LONG" else "BEARISH_FVG"
                dir_fvgs = [f for f in valid_fvgs if str(f.get("type", "")).upper() == target_fvg_type]
                if not dir_fvgs:
                    continue
                sel_fvg = dir_fvgs[-1]
                f_bot = float(sel_fvg["bottom"])
                f_top = float(sel_fvg["top"])
                f_ts = int(sel_fvg.get("timestamp") or sel_fvg.get("middle_bar_ms") or bars_1m[-2].open_time)
                f_conf = int(sel_fvg.get("confirmed_at") or (f_ts + 60_000))
                f_id = str(sel_fvg.get("id") or f"fvg_{cand_dir}_{f_ts}")

                ref_swings = sh_1m if cand_dir == "LONG" else sl_1m
                orig_swings = sl_1m if cand_dir == "LONG" else sh_1m
                swing_prefix = "sh" if cand_dir == "LONG" else "sl"

                causal_breakers = detect_breaker_blocks(
                    bars_1m,
                    symbol=self.symbol,
                    direction=cand_dir,
                    timeframe="1M",
                    as_of_ms=cutoff_ms,
                    confirmed_swings=(sh_1m, sl_1m),
                )
                overlapping_breakers = [
                    b for b in causal_breakers
                    if b.confirmed_at_ms <= cutoff_ms
                    and abs(int(b.break_bar_open_ms) - f_ts) <= 5 * 60_000
                    and min(float(b.high), f_top) > max(float(b.low), f_bot)
                ]
                matched_breaker = overlapping_breakers[-1] if overlapping_breakers else None

                prior_refs = [s for s in ref_swings if int(s.get("occurred_at_ms", 0)) <= f_ts]
                prior_origs = [s for s in orig_swings if int(s.get("occurred_at_ms", 0)) <= f_ts]
                ref_swing = prior_refs[-1] if prior_refs else (ref_swings[-1] if ref_swings else None)
                orig_swing = prior_origs[-1] if prior_origs else (orig_swings[-1] if orig_swings else None)
                if matched_breaker is not None and matched_breaker.broken_swing_id:
                    for s in ref_swings:
                        s_id = str(s.get("id") or f"{swing_prefix}_{s.get('occurred_at_ms')}")
                        if s_id == matched_breaker.broken_swing_id:
                            ref_swing = s
                            break
                if ref_swing is None or orig_swing is None:
                    continue

                broken_swing_id_str = (
                    str(matched_breaker.broken_swing_id)
                    if (matched_breaker is not None and matched_breaker.broken_swing_id)
                    else str(ref_swing.get("id") or f"{swing_prefix}_{ref_swing.get('occurred_at_ms')}")
                )
                eff_break_open_ms = int(matched_breaker.break_bar_open_ms) if matched_breaker is not None else f_ts
                eff_break_known_ms = int(matched_breaker.confirmed_at_ms) if matched_breaker is not None else f_conf

                legacy_sl = (
                    round(f_bot - cfg.sl_buffer_ticks * cfg.tick_size, 2)
                    if cand_dir == "LONG"
                    else round(f_top + cfg.sl_buffer_ticks * cfg.tick_size, 2)
                )
                sel_fvg_desc = {
                    "id": f_id,
                    "type": target_fvg_type,
                    "direction": cand_dir,
                    "timeframe": "1M",
                    "state": "confirmed",
                    "top": f_top,
                    "bottom": f_bot,
                    "middle_bar_ms": f_ts,
                    "timestamp": f_ts,
                    "confirmed_at": f_conf,
                    "break_open_ms": eff_break_open_ms,
                    "broken_swing_id": broken_swing_id_str,
                }
                sel_break_desc = {
                    "break_id": f"brk_{cand_dir}_{eff_break_open_ms}",
                    "break_open_ms": eff_break_open_ms,
                    "broken_at_ms": eff_break_open_ms,
                    "known_at_ms": eff_break_known_ms,
                    "broken_swing_id": broken_swing_id_str,
                    "origin_pivot_id": str(orig_swing.get("id", f"orig_{orig_swing.get('occurred_at_ms')}")),
                    "origin_price": float(orig_swing["price"]),
                    "origin_occurred_at_ms": int(orig_swing["occurred_at_ms"]),
                    "episode_start_ms": min(int(orig_swing["occurred_at_ms"]), int(ref_swing.get("occurred_at_ms", f_ts))),
                }
                sel_sweep = None
                if model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value:
                    sel_sweep = {
                        "id": f"sweep_cg01_{orig_swing.get('occurred_at_ms')}",
                        "price": float(orig_swing["price"]),
                        "occurred_at_ms": int(orig_swing["occurred_at_ms"]),
                        "known_at_ms": int(orig_swing.get("known_at_ms") or orig_swing["occurred_at_ms"]),
                    }
                return {
                    "has_setup": True,
                    "direction": cand_dir,
                    "legacy_sl": legacy_sl,
                    "episode_id": f"ep_{model_id}_{cand_dir}_{f_ts}",
                    "selected_fvg": sel_fvg_desc,
                    "selected_break": sel_break_desc,
                    "selected_sweep": sel_sweep,
                    "selected_reference": ref_swing,
                }
            return {"has_setup": False, "reason_code": "NO_VALID_FVG_SETUP"}

        if model_id == ModelIdentifier.SMC_M1_CONTINUATION.value:
            last_sh = sh_1m[-1]
            last_sl = sl_1m[-1]
            last_bar = bars_1m[-1]
            if last_bar.close > float(last_sh["price"]):
                cand_dir = "LONG"
                legacy_sl = round(float(last_sl["price"]) - cfg.sl_buffer_ticks * cfg.tick_size, 2)
                orig = last_sl
                ref = last_sh
            elif last_bar.close < float(last_sl["price"]):
                cand_dir = "SHORT"
                legacy_sl = round(float(last_sh["price"]) + cfg.sl_buffer_ticks * cfg.tick_size, 2)
                orig = last_sh
                ref = last_sl
            else:
                return {"has_setup": False, "reason_code": "WAITING_M1_BOS"}

            return {
                "has_setup": True,
                "direction": cand_dir,
                "legacy_sl": legacy_sl,
                "episode_id": f"ep_cont_{cand_dir}_{last_bar.open_time}",
                "selected_fvg": None,
                "selected_break": {
                    "break_id": f"bos_{cand_dir}_{last_bar.open_time}",
                    "break_open_ms": last_bar.open_time,
                    "broken_at_ms": last_bar.open_time,
                    "known_at_ms": last_bar.close_time,
                    "broken_swing_id": str(ref.get("id")),
                    "origin_pivot_id": str(orig.get("id")),
                    "origin_price": float(orig["price"]),
                    "origin_occurred_at_ms": int(orig["occurred_at_ms"]),
                    "episode_start_ms": int(orig["occurred_at_ms"]),
                },
                "selected_sweep": {
                    "id": f"anchor_cont_{orig.get('occurred_at_ms')}",
                    "price": float(orig["price"]),
                    "occurred_at_ms": int(orig["occurred_at_ms"]),
                    "known_at_ms": int(orig.get("known_at_ms") or orig["occurred_at_ms"]),
                },
                "selected_reference": ref,
            }

        # SMC_M1_REVERSAL or SMC_M1_SWEEP_RECLAIM
        last_sh = sh_1m[-1]
        last_sl = sl_1m[-1]
        recent_bars = bars_1m[-5:]
        swept_low = min(b.low for b in recent_bars)
        swept_high = max(b.high for b in recent_bars)
        if swept_low <= float(last_sl["price"]) and bars_1m[-1].close > float(last_sl["price"]):
            cand_dir = "LONG"
            legacy_sl = round(swept_low - cfg.sl_buffer_ticks * cfg.tick_size, 2)
            sweep_px = swept_low
            sweep_ts = min(b.open_time for b in recent_bars if b.low == swept_low)
        elif swept_high >= float(last_sh["price"]) and bars_1m[-1].close < float(last_sh["price"]):
            cand_dir = "SHORT"
            legacy_sl = round(swept_high + cfg.sl_buffer_ticks * cfg.tick_size, 2)
            sweep_px = swept_high
            sweep_ts = min(b.open_time for b in recent_bars if b.high == swept_high)
        else:
            return {"has_setup": False, "reason_code": "WAITING_SWEEP_RECLAIM"}

        return {
            "has_setup": True,
            "direction": cand_dir,
            "legacy_sl": legacy_sl,
            "episode_id": f"ep_{model_id}_{cand_dir}_{sweep_ts}",
            "selected_fvg": None,
            "selected_break": None,
            "selected_sweep": {
                "id": f"sweep_{model_id}_{sweep_ts}",
                "price": sweep_px,
                "occurred_at_ms": sweep_ts,
                "known_at_ms": sweep_ts + 60_000,
            },
            "selected_reference": last_sl if cand_dir == "LONG" else last_sh,
        }

    def _build_opportunity_dict(
        self,
        *,
        opportunity_id: str,
        model_id: str,
        direction: str,
        setup_episode_id: str,
        analysis_time_ms: int,
        decision_cutoff_ms: int,
        quote: CanonicalQuote,
        entry_reference: float,
        legacy_sl: float,
        structural_sl: Optional[float],
        unicorn_sl_candidate: Optional[float],
        selected_invalidation_price: Optional[float],
        selected_anchor_kind: Optional[str],
        selected_anchor_id: Optional[str],
        atr_m1: Optional[float],
        atr_m5: Optional[float],
        protective_distance: Optional[float],
        target_price: Optional[float],
        target_evidence_id: Optional[str],
        risk_budget: float,
        hypothetical_quantity: float,
        net_risk: float,
        net_reward: float,
        net_rr: float,
        unicorn_validity: str,
        unicorn_evidence_id: Optional[str],
        shadow_eligibility_status: str,
        lifecycle_state: str,
        rejection_reason_codes: List[str],
        comparison_plan: Optional[Dict[str, Any]],
        source_candle_ids: Sequence[str] = (),
        source_break_id: Optional[str] = None,
        source_fvg_id: Optional[str] = None,
        source_breaker_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        selected_sl = unicorn_sl_candidate or structural_sl or legacy_sl
        return {
            "opportunity_id": opportunity_id,
            "symbol": self.symbol,
            "model_id": model_id,
            "direction": direction,
            "setup_episode_id": setup_episode_id,
            "analysis_time_ms": analysis_time_ms,
            "decision_cutoff_ms": decision_cutoff_ms,
            "entry_reference": entry_reference,
            "entry_quote_bid": quote.bid,
            "entry_quote_ask": quote.ask,
            "legacy_sl": legacy_sl,
            "structural_sl": structural_sl,
            "unicorn_sl_candidate": unicorn_sl_candidate,
            "selected_invalidation_price": selected_invalidation_price,
            "selected_anchor_kind": selected_anchor_kind,
            "selected_anchor_id": selected_anchor_id,
            "atr_m1": atr_m1,
            "atr_m5": atr_m5,
            "atr_m1_value": atr_m1,
            "atr_m1_source": "1M",
            "atr_m1_confirmed_at": decision_cutoff_ms if atr_m1 is not None else None,
            "atr_m5_value": atr_m5,
            "atr_m5_source": "5M" if atr_m5 is not None else None,
            "atr_m5_confirmed_at": decision_cutoff_ms if atr_m5 is not None else None,
            "atr_m5_available": bool(atr_m5 is not None),
            "atr_fallback_policy": "OPTIONAL_M5_OMITTED_USE_ACTUAL_M1_AND_SPREAD",
            "atr_decision_status": (
                "REJECTED_MISSING_ATR_M1"
                if atr_m1 is None
                else ("ACTUAL_ATR_M5" if atr_m5 is not None else "OPTIONAL_M5_OMITTED")
            ),
            "spread": quote.spread,
            "protective_distance": protective_distance,
            "target_price": target_price,
            "target_evidence_id": target_evidence_id,
            "risk_budget": risk_budget,
            "hypothetical_quantity": hypothetical_quantity,
            "net_risk": net_risk,
            "net_reward": net_reward,
            "net_rr": net_rr,
            "unicorn_validity": unicorn_validity,
            "unicorn_evidence_id": unicorn_evidence_id,
            "shadow_eligibility_status": shadow_eligibility_status,
            "lifecycle_state": lifecycle_state,
            "rejection_reason_codes": list(rejection_reason_codes),
            "would_enter_at": entry_reference,
            "would_use_sl": selected_sl,
            "would_use_tp": target_price,
            "would_use_quantity": hypothetical_quantity,
            "hypothetical_entry_status": "SHADOW_ENTRY_NOT_TRIGGERED",
            "hypothetical_exit_status": "EXIT_SEQUENCE_UNVERIFIABLE",
            "execution_status": "NEVER_EXECUTED",
            "research_disclosure": RESEARCH_ONLY_DISCLOSURE,
            "comparison_plan": comparison_plan,
            "source_candle_ids": list(source_candle_ids),
            "source_quote_id": f"q_{quote.exchange_ts_ms or quote.received_at_ms}",
            "source_break_id": source_break_id,
            "source_fvg_id": source_fvg_id,
            "source_breaker_id": source_breaker_id,
            "connection_epoch": quote.connection_epoch,
            "data_version": DATA_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "state_history": [
                {
                    "state": lifecycle_state,
                    "timestamp_ms": decision_cutoff_ms,
                    "reason": "INITIAL_SHADOW_EVALUATION",
                }
            ],
        }

    # =========================================================================
    # PHASE F — SHADOW OPPORTUNITY LIFECYCLE & HYPOTHETICAL MONITORING
    # =========================================================================

    def _register_or_update_opportunity(self, opp: Dict[str, Any]) -> None:
        opp_id = opp["opportunity_id"]
        existing = self._opportunities.get(opp_id)
        if existing is not None:
            # Preserve state history across re-evaluations
            opp["state_history"] = list(existing.get("state_history", [])) + opp.get("state_history", [])
        self._opportunities[opp_id] = opp
        if self._storage and self._storage.is_initialized:
            self._storage.record_opportunity(opp)

    def transition_opportunity_state(
        self,
        opportunity_id: str,
        new_state: str,
        *,
        event_time_ms: int,
        reason: str,
    ) -> bool:
        """
        Transitions a shadow opportunity along the documented state machine.
        Every state transition must be caused by an event available at `event_time_ms`.
        """
        opp = self._opportunities.get(opportunity_id)
        if opp is None:
            return False
        curr_state = opp.get("lifecycle_state", ShadowOpportunityState.DETECTED.value)
        if curr_state == new_state:
            return True
        allowed = SHADOW_STATE_TRANSITIONS.get(curr_state, ())
        if new_state not in allowed:
            return False

        opp["lifecycle_state"] = new_state
        opp.setdefault("state_history", []).append(
            {
                "from_state": curr_state,
                "state": new_state,
                "timestamp_ms": event_time_ms,
                "reason": reason,
            }
        )
        if self._storage and self._storage.is_initialized:
            self._storage.record_opportunity(opp)
        return True

    def monitor_open_opportunities_on_quote(self, quote: CanonicalQuote) -> None:
        """
        Updates hypothetical entry/exit monitoring for active shadow opportunities
        using real Bid/Ask quotes without ever marking any trade as FILLED.
        - LONG entry requires Ask <= would_enter_at; LONG SL uses Bid <= would_use_sl; LONG TP uses Bid >= would_use_tp.
        - SHORT entry requires Bid >= would_enter_at; SHORT SL uses Ask >= would_use_sl; SHORT TP uses Ask <= would_use_tp.
        - Always preserves `execution_status = "NEVER_EXECUTED"`.
        """
        if not quote.is_valid:
            return
        q_ts = quote.exchange_ts_ms or quote.received_at_ms

        for opp_id, opp in list(self._opportunities.items()):
            state = opp.get("lifecycle_state")
            if state not in (
                ShadowOpportunityState.DETECTED.value,
                ShadowOpportunityState.WAITING_CONFIRMATION.value,
                ShadowOpportunityState.CONFIRMED.value,
                ShadowOpportunityState.ELIGIBLE.value,
            ):
                continue

            if q_ts < int(opp.get("decision_cutoff_ms") or 0):
                continue

            # Expiry check (15 minutes after decision cutoff if entry never triggered)
            if (
                opp.get("hypothetical_entry_status") == "SHADOW_ENTRY_NOT_TRIGGERED"
                and (q_ts - int(opp.get("decision_cutoff_ms") or 0)) > 15 * 60_000
            ):
                self.transition_opportunity_state(
                    opp_id,
                    ShadowOpportunityState.EXPIRED.value,
                    event_time_ms=q_ts,
                    reason="SHADOW_ENTRY_NOT_TRIGGERED_BEFORE_EXPIRY",
                )
                continue

            if state != ShadowOpportunityState.ELIGIBLE.value:
                continue

            direction = opp.get("direction", "LONG")
            entry_px = opp.get("would_enter_at")
            sl_px = opp.get("would_use_sl")
            tp_px = opp.get("would_use_tp")
            if entry_px is None or sl_px is None or tp_px is None:
                continue

            if opp.get("hypothetical_entry_status") == "SHADOW_ENTRY_NOT_TRIGGERED":
                entry_touched = (quote.ask <= float(entry_px)) if direction == "LONG" else (quote.bid >= float(entry_px))
                if entry_touched:
                    opp["hypothetical_entry_status"] = "SHADOW_SIMULATED_ENTRY"
                    opp["hypothetical_exit_status"] = "MONITORING_ACTIVE_TICKS"
                    if self._storage and self._storage.is_initialized:
                        self._storage.record_opportunity(opp)
            elif opp.get("hypothetical_entry_status") == "SHADOW_SIMULATED_ENTRY":
                # Evaluate hypothetical exit on executable Bid (for LONG) or Ask (for SHORT)
                if direction == "LONG":
                    sl_hit = quote.bid <= float(sl_px)
                    tp_hit = quote.bid >= float(tp_px)
                else:
                    sl_hit = quote.ask >= float(sl_px)
                    tp_hit = quote.ask <= float(tp_px)

                if sl_hit and tp_hit:
                    opp["hypothetical_exit_status"] = "EXIT_SEQUENCE_UNVERIFIABLE"
                    self.transition_opportunity_state(
                        opp_id,
                        ShadowOpportunityState.OBSERVATION_COMPLETED.value,
                        event_time_ms=q_ts,
                        reason="EXIT_SEQUENCE_UNVERIFIABLE",
                    )
                elif sl_hit:
                    opp["hypothetical_exit_status"] = "SHADOW_SIMULATED_EXIT_SL"
                    self.transition_opportunity_state(
                        opp_id,
                        ShadowOpportunityState.OBSERVATION_COMPLETED.value,
                        event_time_ms=q_ts,
                        reason="SHADOW_SIMULATED_EXIT_SL",
                    )
                elif tp_hit:
                    opp["hypothetical_exit_status"] = "SHADOW_SIMULATED_EXIT_TP"
                    self.transition_opportunity_state(
                        opp_id,
                        ShadowOpportunityState.OBSERVATION_COMPLETED.value,
                        event_time_ms=q_ts,
                        reason="SHADOW_SIMULATED_EXIT_TP",
                    )

    def monitor_open_opportunities_on_closed_candle(self, candle: ShadowCandleRecord) -> None:
        """
        Evaluates open opportunities against newly closed M1 candles when tick ordering was not observed.
        If a candle spans both SL and TP without tick sequence evidence, records `EXIT_SEQUENCE_UNVERIFIABLE`.
        """
        if candle.timeframe != "1M" or not candle.is_closed:
            return

        for opp_id, opp in list(self._opportunities.items()):
            if opp.get("lifecycle_state") != ShadowOpportunityState.ELIGIBLE.value:
                continue
            if candle.close_time <= int(opp.get("decision_cutoff_ms") or 0):
                continue

            direction = opp.get("direction", "LONG")
            sl_px = opp.get("would_use_sl")
            tp_px = opp.get("would_use_tp")
            if sl_px is None or tp_px is None:
                continue

            sl_hit = (candle.low <= float(sl_px)) if direction == "LONG" else (candle.high >= float(sl_px))
            tp_hit = (candle.high >= float(tp_px)) if direction == "LONG" else (candle.low <= float(tp_px))

            if sl_hit and tp_hit:
                opp["hypothetical_exit_status"] = "EXIT_SEQUENCE_UNVERIFIABLE"
                self.transition_opportunity_state(
                    opp_id,
                    ShadowOpportunityState.OBSERVATION_COMPLETED.value,
                    event_time_ms=candle.close_time,
                    reason="INTRABAR_SL_TP_COLLISION_EXIT_SEQUENCE_UNVERIFIABLE",
                )

    # =========================================================================
    # PHASE H — STATUS, TELEMETRY & SNAPSHOT QUERIES
    # =========================================================================

    def get_feed_status(self, *, now_ms: Optional[int] = None) -> str:
        eff_now = now_ms if now_ms is not None else int(time.time() * 1000)

        if self.transport_state == "DISCONNECTED" and self.feed_source != "REST_FALLBACK":
            return ShadowFeedState.DISCONNECTED.value
        if self.transport_state in ("CONNECTING", "RECONNECTING", "DEGRADED") and self.feed_source != "REST_FALLBACK":
            return ShadowFeedState.RECONNECTING.value
        if self.has_data_gap:
            return ShadowFeedState.DATA_GAP.value

        if self.latest_quote is None or not self.latest_quote.is_valid:
            return ShadowFeedState.CONNECTED_STALE.value

        q_ts = self.latest_quote.exchange_ts_ms or self.latest_quote.received_at_ms or 0
        age_sec = max(0.0, (eff_now - q_ts) / 1000.0) if q_ts > 0 else 9999.0
        if age_sec > 15.0:
            return ShadowFeedState.CONNECTED_STALE.value

        if self.feed_source == "REST_FALLBACK":
            return ShadowFeedState.REST_FALLBACK.value

        return ShadowFeedState.CONNECTED_FRESH.value

    def get_timeframe_status(self, timeframe: str, *, now_ms: Optional[int] = None) -> Dict[str, Any]:
        eff_now = now_ms if now_ms is not None else int(time.time() * 1000)
        norm_tf = self.normalize_timeframe(timeframe) or "1M"
        closed = self.get_closed_candles(norm_tf, limit=500)
        prov = self.get_provisional_candle(norm_tf)
        missing = self._missing_intervals_by_tf.get(norm_tf, 0)

        if not closed:
            return {
                "timeframe": norm_tf,
                "status": "EMPTY",
                "closed_bars_count": 0,
                "last_closed_open_time": None,
                "last_closed_close_time": None,
                "freshness_sec": None,
                "has_provisional_bar": prov is not None,
                "missing_intervals": missing,
            }

        last_bar = closed[-1]
        freshness_sec = round(max(0.0, (eff_now - last_bar.close_time) / 1000.0), 2)
        max_stale = TIMEFRAME_STALE_SEC.get(norm_tf, 1800.0)
        status = "DATA_GAP" if missing > 0 else ("STALE" if freshness_sec > max_stale else "READY")

        return {
            "timeframe": norm_tf,
            "status": status,
            "closed_bars_count": len(closed),
            "last_closed_open_time": last_bar.open_time,
            "last_closed_close_time": last_bar.close_time,
            "freshness_sec": freshness_sec,
            "has_provisional_bar": prov is not None,
            "missing_intervals": missing,
        }

    def get_market_status_snapshot(self, *, now_ms: Optional[int] = None) -> Dict[str, Any]:
        eff_now = now_ms if now_ms is not None else int(time.time() * 1000)
        feed_status = self.get_feed_status(now_ms=eff_now)
        q = self.latest_quote
        q_ts = (q.exchange_ts_ms or q.received_at_ms) if q else None
        q_age = round(max(0.0, (eff_now - q_ts) / 1000.0), 2) if q_ts else None

        m1_status = self.get_timeframe_status("1M", now_ms=eff_now)
        m5_status = self.get_timeframe_status("5M", now_ms=eff_now)
        m15_status = self.get_timeframe_status("15M", now_ms=eff_now)

        opps = list(self._opportunities.values())
        eligible_opps = [o for o in opps if o.get("shadow_eligibility_status") == "SHADOW_ELIGIBLE"]
        rejected_opps = [o for o in opps if o.get("shadow_eligibility_status") == "SHADOW_REJECTED"]
        confirmed_unicorns = [u for u in self._unicorn_records if u.get("validity_status") == "VALID"]

        return {
            "symbol": self.symbol,
            "runtime_profile": get_runtime_profile_status(),
            "feed_status": feed_status,
            "transport_state": self.transport_state,
            "feed_source": self.feed_source,
            "connection_epoch": self.active_connection_epoch,
            "degraded_reason": self.degraded_reason,
            "last_quote_timestamp": self.last_quote_timestamp,
            "quote_freshness_sec": q_age,
            "spread": q.spread if q else None,
            "latest_quote": q.to_dict() if q else None,
            "m1_status": m1_status,
            "m5_status": m5_status,
            "m15_status": m15_status,
            "last_closed_m1_timestamp": self.last_closed_m1_timestamp,
            "last_closed_m5_timestamp": self.last_closed_m5_timestamp,
            "last_closed_m15_timestamp": self.last_closed_m15_timestamp,
            "metrics": {
                "quotes_received": self.quotes_received,
                "quotes_accepted": self.quotes_accepted,
                "quotes_rejected": self.quotes_rejected,
                "quotes_late": self.quotes_late,
                "quotes_duplicate": self.quotes_duplicate,
                "candles_received": self.candles_received,
                "candles_closed": self.candles_closed,
                "candles_revised": self.candles_revised,
                "candles_missing": self.candles_missing,
                "ws_reconnects": self.ws_reconnects,
                "rest_fallback_activations": self.rest_fallback_activations,
                "queue_depth": self.queue_depth,
                "queue_drops": self.queue_drops,
                "last_quote_timestamp": self.last_quote_timestamp,
                "last_closed_m1_timestamp": self.last_closed_m1_timestamp,
                "last_closed_m5_timestamp": self.last_closed_m5_timestamp,
                "last_closed_m15_timestamp": self.last_closed_m15_timestamp,
            },
            "last_completed_analysis_cycle": self._last_cycle_summary,
            "summary_counts": {
                "detected_setups": len(opps),
                "confirmed_unicorn_opportunities": len(confirmed_unicorns),
                "eligible_opportunities": len(eligible_opps),
                "rejected_opportunities": len(rejected_opps),
            },
            "storage_health": self._storage.get_health_summary() if (self._storage and self._storage.is_initialized) else None,
            "research_disclosure": RESEARCH_ONLY_DISCLOSURE,
        }

    def _finalize_cycle_summary(
        self,
        *,
        cycle_id: str,
        analysis_time_ms: int,
        cutoff_ms: int,
        feed_status: str,
        quote: Optional[CanonicalQuote],
        bars_1m: List[ShadowCandleRecord],
        bars_5m: List[ShadowCandleRecord],
        bars_15m: List[ShadowCandleRecord],
        model_outcomes: Dict[str, Dict[str, Any]],
        opportunities: List[Dict[str, Any]],
        unicorns: List[Dict[str, Any]],
        unclosed_m5_rejected: bool,
    ) -> Dict[str, Any]:
        valid_unicorns = [u for u in unicorns if u.get("validity_status") == "VALID"]
        eligible_opps = [o for o in opportunities if o.get("shadow_eligibility_status") == "SHADOW_ELIGIBLE"]
        rejected_opps = [o for o in opportunities if o.get("shadow_eligibility_status") == "SHADOW_REJECTED"]

        summary = {
            "cycle_id": cycle_id,
            "symbol": self.symbol,
            "analysis_time_ms": analysis_time_ms,
            "decision_cutoff_ms": cutoff_ms,
            "feed_status": feed_status,
            "quote_freshness_sec": quote.freshness_sec if quote else None,
            "spread": quote.spread if quote else None,
            "m1_status": "READY" if len(bars_1m) >= 15 else "INSUFFICIENT",
            "m5_status": "UNCLOSED_REJECTED" if unclosed_m5_rejected and len(bars_5m) < 3 else ("READY" if len(bars_5m) >= 3 else "INSUFFICIENT"),
            "m15_status": "READY" if len(bars_15m) >= 1 else "INSUFFICIENT",
            "models_evaluated": model_outcomes,
            "model_outcomes": model_outcomes,
            "evaluator_version": "17.9.0.1",
            "detected_setups_count": len(opportunities),
            "confirmed_unicorns_count": len(valid_unicorns),
            "eligible_count": len(eligible_opps),
            "rejected_count": len(rejected_opps),
            "opportunities": opportunities,
            "unicorn_evidence": unicorns,
            "research_disclosure": RESEARCH_ONLY_DISCLOSURE,
        }
        self._last_cycle_summary = summary
        self._analysis_cycles.append(summary)
        if len(self._analysis_cycles) > 100:
            self._analysis_cycles = self._analysis_cycles[-100:]

        if self._storage and self._storage.is_initialized:
            self._storage.record_analysis_cycle(summary)
            self._storage.record_runtime_health(
                {
                    "timestamp_ms": analysis_time_ms,
                    "runtime_profile": "MARKET_SHADOW",
                    "feed_status": feed_status,
                    "quotes_received": self.quotes_received,
                    "quotes_accepted": self.quotes_accepted,
                    "quotes_rejected": self.quotes_rejected,
                    "quotes_late": self.quotes_late,
                    "quotes_duplicate": self.quotes_duplicate,
                    "candles_received": self.candles_received,
                    "candles_closed": self.candles_closed,
                    "candles_revised": self.candles_revised,
                    "candles_missing": self.candles_missing,
                    "ws_reconnects": self.ws_reconnects,
                    "rest_fallback_activations": self.rest_fallback_activations,
                    "queue_depth": self.queue_depth,
                    "queue_drops": self.queue_drops,
                    "last_quote_timestamp": self.last_quote_timestamp,
                    "last_closed_m1_timestamp": self.last_closed_m1_timestamp,
                    "last_closed_m5_timestamp": self.last_closed_m5_timestamp,
                    "last_closed_m15_timestamp": self.last_closed_m15_timestamp,
                }
            )
        return summary

    def get_recent_quotes(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        if self._storage and self._storage.is_initialized:
            db_quotes = self._storage.get_quotes(limit=limit)
            if db_quotes:
                return db_quotes
        return list(reversed(self._recent_quotes[-limit:]))

    def get_candles_snapshot(
        self,
        timeframe: str = "1M",
        *,
        limit: int = 100,
        include_provisional: bool = True,
    ) -> Dict[str, Any]:
        norm_tf = self.normalize_timeframe(timeframe) or "1M"
        closed = [b.to_dict() for b in self.get_closed_candles(norm_tf, limit=limit)]
        if not closed and self._storage and self._storage.is_initialized:
            closed = self._storage.get_closed_candles(timeframe=norm_tf, limit=limit)
        prov = self.get_provisional_candle(norm_tf)
        return {
            "symbol": self.symbol,
            "timeframe": norm_tf,
            "closed_candles": closed,
            "provisional_candle": prov.to_dict() if (include_provisional and prov is not None) else None,
            "status": self.get_timeframe_status(norm_tf),
        }

    def get_opportunities(
        self,
        *,
        limit: int = 50,
        eligibility_status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self._storage and self._storage.is_initialized:
            db_opps = self._storage.get_opportunities(limit=limit, eligibility_status=eligibility_status)
            if db_opps:
                return db_opps
        items = list(self._opportunities.values())
        if eligibility_status:
            items = [o for o in items if o.get("shadow_eligibility_status") == eligibility_status]
        items.sort(key=lambda o: int(o.get("decision_cutoff_ms") or 0), reverse=True)
        return items[:limit]

    def get_opportunity_by_id(self, opportunity_id: str) -> Optional[Dict[str, Any]]:
        if opportunity_id in self._opportunities:
            return self._opportunities[opportunity_id]
        if self._storage and self._storage.is_initialized:
            return self._storage.get_opportunity_by_id(opportunity_id)
        return None

    def get_unicorn_evidence_list(
        self,
        *,
        limit: int = 50,
        validity_status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self._storage and self._storage.is_initialized:
            db_items = self._storage.get_unicorn_evidence_list(limit=limit, validity_status=validity_status)
            if db_items:
                return db_items
        items = list(self._unicorn_records)
        if validity_status:
            items = [u for u in items if u.get("validity_status") == validity_status]
        return list(reversed(items[-limit:]))

    def get_backtests_list(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        if self._storage and self._storage.is_initialized:
            return self._storage.get_replay_results(limit=limit)
        return []

    def get_backtest_by_run_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        if self._storage and self._storage.is_initialized:
            return self._storage.get_replay_result_by_id(run_id)
        return None

    # =========================================================================
    # BACKGROUND SHADOW WORKER LOOP & CLEAN SHUTDOWN
    # =========================================================================

    async def run_shadow_loop(self, evaluation_interval_sec: float = 5.0) -> None:
        """
        Background worker loop for MARKET_SHADOW:
        - Drains queued market events.
        - Periodically runs continuous shadow analysis cycles on closed candles + fresh quote.
        - Never touches operational DB or trading execution services.
        """
        assert_runtime_capability("can_evaluate_shadow_strategies", "MarketShadowService.run_shadow_loop")
        self._running = True
        last_eval_ms = 0
        logger.info("MarketShadowService worker loop started.")
        try:
            while self._running:
                try:
                    ev = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                    self._handle_queued_event(ev)
                    self._event_queue.task_done()
                except asyncio.TimeoutError:
                    pass

                now_ms = int(time.time() * 1000)
                if (now_ms - last_eval_ms) >= int(evaluation_interval_sec * 1000):
                    last_eval_ms = now_ms
                    try:
                        self.evaluate_shadow_cycle(as_of_ms=now_ms)
                    except Exception as eval_exc:
                        logger.warning("Shadow evaluation cycle error: %s", eval_exc)
        except asyncio.CancelledError:
            pass
        finally:
            self._drain_remaining_events()
            logger.info("MarketShadowService worker loop exited cleanly.")

    def _handle_queued_event(self, ev: Dict[str, Any]) -> None:
        kind = ev.get("kind")
        if kind == "quote":
            q = ev.get("quote")
            if isinstance(q, CanonicalQuote):
                self.ingest_quote(q)
            elif isinstance(q, dict):
                self.ingest_raw_quote(q, source=ev.get("source", "WS"), connection_epoch=ev.get("epoch"))
        elif kind == "candle":
            self.ingest_candle_payload(
                symbol=ev.get("symbol", self.symbol),
                timeframe=ev.get("timeframe", "1M"),
                data_rows=ev.get("data_rows", []),
                action=ev.get("action", "update"),
                source=ev.get("source", "WS"),
                connection_epoch=ev.get("epoch"),
            )

    def _drain_remaining_events(self) -> None:
        while not self._event_queue.empty():
            try:
                ev = self._event_queue.get_nowait()
                self._handle_queued_event(ev)
                self._event_queue.task_done()
            except Exception:
                break

    def stop(self) -> None:
        self._running = False
        self._drain_remaining_events()

    def build_shared_decision_context(
        self,
        *,
        decision_timestamp_ms: int,
        m1_candles: Optional[Sequence[Any]] = None,
        m5_candles: Optional[Sequence[Any]] = None,
        m15_candles: Optional[Sequence[Any]] = None,
        h1_candles: Optional[Sequence[Any]] = None,
        h4_candles: Optional[Sequence[Any]] = None,
        d1_candles: Optional[Sequence[Any]] = None,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        quote_timestamp_ms: Optional[int] = None,
        htf_zones: Optional[Sequence[Any]] = None,
        liquidity_pools: Optional[Sequence[Any]] = None,
        model_enablement: Optional[Dict[str, bool]] = None,
        policy_config: Optional[Dict[str, Any]] = None,
        risk_config: Optional[Dict[str, Any]] = None,
        cost_config: Optional[Dict[str, Any]] = None,
        strict_causal_validation: bool = False,
        strict_htf_validation: bool = False,
        htf_bias: Optional[str] = None,
        h1_alignment: Optional[str] = None,
    ):
        """
        V17.9.0.1 Phase E: MARKET_SHADOW adapter method constructing immutable
        MarketDecisionContext for SharedStrategyEvaluator.
        """
        from services.shared_strategy_evaluator import ALL_EVALUATED_MODEL_IDS, build_market_decision_context
        enablement = {
            m: (m not in self._disabled_models)
            for m in ALL_EVALUATED_MODEL_IDS
        }
        if model_enablement:
            enablement.update(model_enablement)
        return build_market_decision_context(
            symbol=self.symbol,
            decision_timestamp_ms=decision_timestamp_ms,
            m1_candles=m1_candles,
            m5_candles=m5_candles,
            m15_candles=m15_candles,
            h1_candles=h1_candles,
            h4_candles=h4_candles,
            d1_candles=d1_candles,
            bid=bid,
            ask=ask,
            quote_timestamp_ms=quote_timestamp_ms,
            htf_zones=htf_zones,
            liquidity_pools=liquidity_pools,
            model_enablement=enablement,
            policy_config=policy_config,
            risk_config=risk_config,
            cost_config=cost_config,
            strict_causal_validation=strict_causal_validation,
            strict_htf_validation=strict_htf_validation,
            htf_bias=htf_bias,
            h1_alignment=h1_alignment,
        )

    def compare_production_shadow_divergence(
        self,
        production_snapshot: Any,
        shadow_snapshot: Any,
    ) -> Dict[str, Any]:
        """
        V17.9.0.1.1 Section 4: Compares Production and MARKET_SHADOW decision snapshots
        on the same normalized input snapshot, distinguishing INPUT_MISMATCH from
        EXACT_MATCH and DECISION_DIVERGENCE.
        """
        from services.shared_strategy_evaluator import compare_decision_parity

        def _get_field(obj: Any, name: str) -> Any:
            return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

        prod_in_hash = _get_field(production_snapshot, "input_snapshot_hash") or _get_field(production_snapshot, "evidence_fingerprint")
        shad_in_hash = _get_field(shadow_snapshot, "input_snapshot_hash") or _get_field(shadow_snapshot, "evidence_fingerprint")
        prod_cfg_hash = _get_field(production_snapshot, "configuration_hash") or _get_field(production_snapshot, "policy_fingerprint")
        shad_cfg_hash = _get_field(shadow_snapshot, "configuration_hash") or _get_field(shadow_snapshot, "policy_fingerprint")

        input_mismatch = bool(
            (prod_in_hash and shad_in_hash and prod_in_hash != shad_in_hash)
            or (prod_cfg_hash and shad_cfg_hash and prod_cfg_hash != shad_cfg_hash)
        )

        diff = compare_decision_parity(production_snapshot, shadow_snapshot)
        field_diffs = {
            m["field"]: {"production": m["left"], "shadow": m["right"]}
            for m in diff["mismatches"]
        }
        if input_mismatch:
            comparison_status = "INPUT_MISMATCH"
        elif diff["is_match"]:
            comparison_status = "EXACT_MATCH"
        else:
            comparison_status = "DECISION_DIVERGENCE"

        event = {
            "event_type": "SHADOW_PARITY_VERIFIED" if diff["is_match"] and not input_mismatch else "RUNTIME_DECISION_DIVERGENCE",
            "comparison_status": comparison_status,
            "symbol": self.symbol,
            "model_id": _get_field(shadow_snapshot, "model_id"),
            "direction": _get_field(shadow_snapshot, "direction"),
            "decision_timestamp_ms": _get_field(shadow_snapshot, "decision_timestamp_ms"),
            "parity_status": "INPUT_MISMATCH" if input_mismatch else diff["parity_status"],
            "is_match": bool(diff["is_match"] and not input_mismatch),
            "is_divergent": bool(not diff["is_match"] or input_mismatch),
            "input_mismatch": input_mismatch,
            "production_input_snapshot_hash": prod_in_hash,
            "shadow_input_snapshot_hash": shad_in_hash,
            "production_configuration_hash": prod_cfg_hash,
            "shadow_configuration_hash": shad_cfg_hash,
            "mismatch_count": diff["mismatch_count"],
            "mismatches": diff["mismatches"],
            "field_diffs": field_diffs,
            "production_fingerprint": diff["left_fingerprint"],
            "shadow_fingerprint": diff["right_fingerprint"],
        }
        return event

    def evaluate_shared_decision_snapshot(
        self,
        *,
        context: Any,
        model_id: str,
        direction: Optional[str] = None,
    ):
        """
        V17.9.0.1.1 Section 4: MARKET_SHADOW adapter method delegating pure strategy
        decision evaluation to SharedStrategyEvaluator.evaluate_decision(...).
        """
        from services.shared_strategy_evaluator import SharedStrategyEvaluator
        return SharedStrategyEvaluator.evaluate_decision(
            market_snapshot=context,
            structural_context={"model_id": model_id, "direction": direction},
            strategy_config={
                "model_id": model_id,
                "direction": direction if direction in ("LONG", "SHORT") else None,
                "adapter_origin": "MARKET_SHADOW_ADAPTER",
                "target_resolver": resolve_executable_target,
            },
        )

    def evaluate_shared_all_models(
        self,
        *,
        context: Any,
        model_ids: Optional[Sequence[str]] = None,
        preferred_direction: Optional[str] = None,
    ):
        """
        V17.9.0 Section 7.2: MARKET_SHADOW adapter method evaluating all supported
        models through SharedStrategyEvaluator.
        """
        from services.shared_strategy_evaluator import PRODUCTION_MODEL_IDS, SharedStrategyEvaluator
        return SharedStrategyEvaluator.evaluate_all_models(
            context=context,
            model_ids=model_ids or PRODUCTION_MODEL_IDS,
            preferred_direction=preferred_direction,
            adapter_origin="MARKET_SHADOW_ADAPTER",
            target_resolver=resolve_executable_target,
        )


market_shadow_service = MarketShadowService()
