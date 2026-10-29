"""
backend/services/scalp_watcher_orchestrator.py
V15.0 - Autonomous SMC Scalp Watcher Orchestrator (PHẦN 054 - 071).

Coordinates:
- Zone extraction and bounded watchlist updates on 15M/5M closures.
- Continuous near-zone proximity evaluation with hysteresis and cooldown.
- Autonomous M1 Scalp evaluation (Reversal & Continuation) on 1M closures.
- Daily NY Research model evaluation (PAPER ONLY) on eligible NY trading days.
- Opportunity funnel transitions recording with latency tracking.
- Canonical entry guards validation under serialize_entry_mutation for PAPER mode.
- Telegram notification dispatch via outbox pipeline (ZONE_NEAR, TRIGGER_CONFIRMED, ORDER_ARMED).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional, Sequence
import uuid
from sqlalchemy.orm import Session

import crud
import models
from database import SessionLocal
from domain_calculator import CalculationResult, CostAssumptions
from services.collector_service import collector_service
from services.daily_ny_research_service import DailyNYResearchService
from services.entry_guard_service import (
    EntryGuardRequest,
    evaluate_entry_guard,
    serialize_entry_mutation,
)
from services.opportunity_funnel_service import OpportunityFunnelService
from services.quote_validator import QuoteValidator, ValidatedQuote, normalize_execution_quote
from services.risk_settings_service import risk_settings_service
from services.scalp_m1_model_service import ScalpCandidate, ScalpM1ModelService
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityEvaluation,
    OpportunityReasonCode,
    OpportunityStage,
    ScalpMode,
    WatchedZone,
    ZoneState,
    get_scalp_config,
    load_effective_scalp_config,
)
from services.smc_scenario_binding import promote_confirmed_scenario
from services.telegram_service import queue_telegram_notification
from services.trading_policy_service import TradingPolicyService, resolve_entry_session_context
from services.zone_watchlist_service import ZoneWatchlistService

logger = logging.getLogger(__name__)


@dataclass
class ObservationProvenance:
    symbol: str
    bid: float
    ask: float
    exchange_ts_ms: Optional[int]
    received_at_ms: Optional[int]
    evaluated_at_ms: int
    quote_source: str
    transport_state: str
    connection_epoch: Optional[int]
    quote_valid: bool
    quote_stale: bool
    reason_code: Optional[str] = None
    is_authoritative_for_epoch: bool = True

    def to_snapshot_dict(self) -> Dict[str, Any]:
        age_sec = None
        if self.exchange_ts_ms is not None:
            age_sec = round(max(0.0, (self.evaluated_at_ms - self.exchange_ts_ms) / 1000.0), 3)
        elif self.received_at_ms is not None:
            age_sec = round(max(0.0, (self.evaluated_at_ms - self.received_at_ms) / 1000.0), 3)

        status = "VALID"
        if not self.quote_valid or self.reason_code in ("QUOTE_TIMESTAMP_UNVERIFIED", "QUOTE_MISSING_BID_OR_ASK"):
            status = "UNVERIFIED"
        elif self.quote_stale:
            status = "STALE"
        elif not self.is_authoritative_for_epoch:
            status = "DEGRADED"

        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "quote_source": self.quote_source,
            "transport_state": self.transport_state,
            "last_quote_at_ms": self.exchange_ts_ms or self.received_at_ms,
            "exchange_ts_ms": self.exchange_ts_ms,
            "received_at_ms": self.received_at_ms,
            "quote_age_sec": age_sec,
            "connection_epoch": self.connection_epoch,
            "is_authoritative_for_epoch": self.is_authoritative_for_epoch,
            "status": status,
            "freshness_state": "CONNECTED_FRESH" if (status == "VALID" and not self.quote_stale) else status,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class TimestampAuthorityResult:
    is_valid: bool
    is_stale: bool
    exchange_ts_ms: Optional[int]
    received_at_ms: Optional[int]
    reason_code: Optional[str] = None


def validate_observation_timestamp_authority(
    *,
    exchange_ts_ms: Optional[Any],
    received_at_ms: Optional[Any],
    evaluated_at_ms: int,
    source: Optional[str] = None,
    connection_epoch: Optional[int] = None,
) -> TimestampAuthorityResult:
    """
    V17.6.4.3c CPA-03 (Section 04): Strict exchange vs receive timestamp authority policy.
    Primary Bitget WebSocket GOOD requires a valid exchange timestamp and a trusted receive timestamp.
    """
    if exchange_ts_ms is None and received_at_ms is None:
        return TimestampAuthorityResult(
            is_valid=False,
            is_stale=False,
            exchange_ts_ms=None,
            received_at_ms=None,
            reason_code="QUOTE_TIMESTAMP_UNVERIFIED",
        )
    if exchange_ts_ms is None:
        recv_val = None
        try:
            if received_at_ms is not None:
                rf = float(received_at_ms)
                if math.isfinite(rf):
                    recv_val = int(rf)
        except (TypeError, ValueError):
            pass
        return TimestampAuthorityResult(
            is_valid=False,
            is_stale=False,
            exchange_ts_ms=None,
            received_at_ms=recv_val,
            reason_code="EXCHANGE_TIMESTAMP_UNVERIFIED",
        )
    if received_at_ms is None:
        exch_val = None
        try:
            if exchange_ts_ms is not None:
                ef = float(exchange_ts_ms)
                if math.isfinite(ef):
                    exch_val = int(ef)
        except (TypeError, ValueError):
            pass
        return TimestampAuthorityResult(
            is_valid=False,
            is_stale=False,
            exchange_ts_ms=exch_val,
            received_at_ms=None,
            reason_code="RECEIVE_TIMESTAMP_UNVERIFIED",
        )

    # Validate numbers
    try:
        exch_f = float(exchange_ts_ms)
        if not math.isfinite(exch_f) or exch_f <= 0:
            return TimestampAuthorityResult(False, False, None, None, "INVALID_EXCHANGE_TIMESTAMP")
    except (TypeError, ValueError):
        return TimestampAuthorityResult(False, False, None, None, "INVALID_EXCHANGE_TIMESTAMP")

    try:
        recv_f = float(received_at_ms)
        if not math.isfinite(recv_f) or recv_f <= 0:
            return TimestampAuthorityResult(False, False, int(exch_f), None, "INVALID_RECEIVE_TIMESTAMP")
    except (TypeError, ValueError):
        return TimestampAuthorityResult(False, False, int(exch_f), None, "INVALID_RECEIVE_TIMESTAMP")

    # Units check: must be milliseconds (> 10_000_000_000)
    if exch_f < 10_000_000_000 or recv_f < 10_000_000_000:
        return TimestampAuthorityResult(False, False, int(exch_f), int(recv_f), "TIMESTAMP_UNIT_INVALID")

    exch_ts = int(exch_f)
    recv_ts = int(recv_f)

    # Future timestamps (> +5s)
    if exch_ts > evaluated_at_ms + 5_000 or recv_ts > evaluated_at_ms + 5_000:
        return TimestampAuthorityResult(False, False, exch_ts, recv_ts, "QUOTE_FUTURE_CLOCK_SKEW")

    # Contradictory ordering: received earlier than exchange by > 1s
    if recv_ts < exch_ts - 1_000:
        return TimestampAuthorityResult(False, False, exch_ts, recv_ts, "QUOTE_TIMESTAMP_CONTRADICTORY")

    # Staleness (> 15s)
    if (evaluated_at_ms - exch_ts) > 15_000 or (evaluated_at_ms - recv_ts) > 15_000:
        return TimestampAuthorityResult(True, True, exch_ts, recv_ts, "QUOTE_STALE")

    return TimestampAuthorityResult(True, False, exch_ts, recv_ts, None)


@dataclass(frozen=True)
class TransportAuthorityResult:
    transport_state: str
    is_verified: bool
    reason_code: Optional[str] = None


def resolve_authoritative_transport_state(
    *,
    feed_adapter_state: Optional[Any],
    quote_metadata: Optional[Any] = None,
) -> TransportAuthorityResult:
    """
    V17.6.4.3c CPA-01 (Section 02): Trusted feed adapter authority hierarchy.
    Raw quote metadata cannot override trusted adapter state.
    """
    if feed_adapter_state is None:
        return TransportAuthorityResult(
            transport_state="UNKNOWN",
            is_verified=False,
            reason_code="ADAPTER_STATE_UNAVAILABLE",
        )

    adapter_t = getattr(feed_adapter_state, "transport_state", None)
    raw_t = None
    if isinstance(quote_metadata, dict):
        raw_t = quote_metadata.get("transport_state")
    elif hasattr(quote_metadata, "transport_state"):
        raw_t = getattr(quote_metadata, "transport_state")

    if not adapter_t or adapter_t in ("UNKNOWN", "TRANSPORT_UNKNOWN"):
        return TransportAuthorityResult(
            transport_state="UNKNOWN",
            is_verified=False,
            reason_code="TRANSPORT_STATE_UNKNOWN",
        )

    if adapter_t in ("DISCONNECTED", "DOWN"):
        code = "TRANSPORT_AUTHORITY_CONFLICT" if raw_t == "CONNECTED" else f"ADAPTER_{adapter_t}"
        return TransportAuthorityResult(
            transport_state="DISCONNECTED",
            is_verified=False,
            reason_code=code,
        )

    if adapter_t in ("RECONNECTING", "CONNECTING", "DEGRADED"):
        code = "TRANSPORT_AUTHORITY_CONFLICT" if raw_t == "CONNECTED" else f"ADAPTER_{adapter_t}"
        return TransportAuthorityResult(
            transport_state=adapter_t,
            is_verified=False,
            reason_code=code,
        )

    if adapter_t == "CONNECTED":
        if raw_t in ("DISCONNECTED", "DOWN"):
            return TransportAuthorityResult(
                transport_state="DISCONNECTED",
                is_verified=False,
                reason_code="TRANSPORT_AUTHORITY_CONFLICT",
            )
        return TransportAuthorityResult(
            transport_state="CONNECTED",
            is_verified=True,
            reason_code=None,
        )

    return TransportAuthorityResult(
        transport_state="UNKNOWN",
        is_verified=False,
        reason_code=f"UNKNOWN_ADAPTER_STATE_{adapter_t}",
    )


def resolve_watcher_observation_provenance(
    *,
    raw_quote: Any,
    validated_quote: Optional[ValidatedQuote],
    feed_adapter_state: Optional[Any] = None,
    evaluated_at_ms: int,
) -> ObservationProvenance:
    """
    V17.6.4.3c CPA-01..CPA-04: Authoritative resolution of market data feed provenance.
    Guarantees:
    - Never replaces missing exchange timestamp with evaluation time (CFP-01 / CPA-03).
    - Preserves exchange_ts_ms, received_at_ms, and evaluated_at_ms as distinct values.
    - Never allows raw quote metadata to self-authorize WS or override adapter transport (CPA-01, CPA-02).
    - Requires valid exchange and receive timestamps for primary WS GOOD (CPA-03).
    - Validates connection_epoch: quotes from prior or missing epochs fail authority (CPA-04).
    """
    sym = "XAUUSDT"
    if raw_quote is not None:
        sym = getattr(raw_quote, "symbol", None) or (raw_quote.get("symbol") if isinstance(raw_quote, dict) else None) or "XAUUSDT"

    # Prices & validity
    v_valid = False
    v_stale = False
    v_reason = None
    bid = 0.0
    ask = 0.0

    if validated_quote is not None:
        bid = float(getattr(validated_quote, "bid", 0.0))
        ask = float(getattr(validated_quote, "ask", 0.0))
        v_valid = getattr(validated_quote, "is_valid", False)
        v_stale = getattr(validated_quote, "is_stale", False)
        v_reason = getattr(validated_quote, "rejection_code", None)
    elif raw_quote is not None:
        bid = float(getattr(raw_quote, "bid", 0.0) if hasattr(raw_quote, "bid") else (raw_quote.get("bid", 0.0) if isinstance(raw_quote, dict) else 0.0))
        ask = float(getattr(raw_quote, "ask", 0.0) if hasattr(raw_quote, "ask") else (raw_quote.get("ask", 0.0) if isinstance(raw_quote, dict) else 0.0))
        v_valid = (bid > 0 and ask >= bid)
        v_stale = False
        v_reason = None
    else:
        bid, ask, v_valid, v_stale, v_reason = 0.0, 0.0, False, True, "QUOTE_UNAVAILABLE"

    # Extract raw timestamps (CPA-03)
    raw_exch_ts = None
    raw_recv_ts = None
    if raw_quote is not None:
        raw_exch_ts = getattr(raw_quote, "exchange_ts_ms", None)
        if raw_exch_ts is None:
            raw_exch_ts = getattr(raw_quote, "exchange_time", None)
        if raw_exch_ts is None and isinstance(raw_quote, dict):
            raw_exch_ts = raw_quote.get("exchange_ts_ms") or raw_quote.get("exchange_time") or raw_quote.get("timestamp_ms") or raw_quote.get("timestamp")

        raw_recv_ts = getattr(raw_quote, "received_at_ms", None)
        if raw_recv_ts is None and isinstance(raw_quote, dict):
            raw_recv_ts = raw_quote.get("received_at_ms") or raw_quote.get("observed_at")

    ts_res = validate_observation_timestamp_authority(
        exchange_ts_ms=raw_exch_ts,
        received_at_ms=raw_recv_ts,
        evaluated_at_ms=evaluated_at_ms,
    )
    exch_ts = ts_res.exchange_ts_ms
    recv_ts = ts_res.received_at_ms
    if not ts_res.is_valid:
        v_valid = False
        if not v_reason:
            v_reason = ts_res.reason_code
    if ts_res.is_stale:
        v_stale = True
        if not v_reason:
            v_reason = ts_res.reason_code

    # Authoritative Transport State (CPA-01)
    trans_res = resolve_authoritative_transport_state(
        feed_adapter_state=feed_adapter_state,
        quote_metadata=raw_quote,
    )
    transport_state = trans_res.transport_state
    if not trans_res.is_verified:
        v_valid = False
        if not v_reason:
            v_reason = trans_res.reason_code

    # Authoritative Quote Source (CPA-02)
    raw_src = None
    if raw_quote is not None:
        raw_src = getattr(raw_quote, "source", None) or getattr(raw_quote, "quote_source", None)
        if raw_src is None and isinstance(raw_quote, dict):
            raw_src = raw_quote.get("quote_source") or raw_quote.get("source")

    if feed_adapter_state is not None:
        adapter_src = getattr(feed_adapter_state, "feed_source", None)
        if adapter_src in ("REST_FALLBACK", "REST"):
            quote_source = "REST_FALLBACK"
            if raw_src in ("WS", "WEBSOCKET"):
                v_valid = False
                v_reason = "SOURCE_AUTHORITY_CONFLICT"
            elif raw_src in ("REST_FALLBACK", "REST", None) and ts_res.is_valid and not ts_res.is_stale and bid > 0 and ask >= bid:
                # Genuine REST fallback quote with valid timestamps and prices is valid
                v_valid = True
                v_reason = None
        elif adapter_src in ("WS", "WEBSOCKET") and trans_res.transport_state == "CONNECTED":
            if raw_src in ("REST_FALLBACK", "REST"):
                quote_source = "REST_FALLBACK"
                v_valid = False
                if not v_reason:
                    v_reason = "SOURCE_AUTHORITY_CONFLICT"
            elif raw_src in ("WS", "WEBSOCKET") or raw_src is None:
                quote_source = "WS"
            else:
                quote_source = str(raw_src)
        elif adapter_src:
            quote_source = str(adapter_src)
        else:
            quote_source = "UNKNOWN"
            v_valid = False
            if not v_reason:
                v_reason = "QUOTE_SOURCE_UNKNOWN"
    else:
        quote_source = "UNKNOWN"
        v_valid = False
        if not v_reason:
            v_reason = "ADAPTER_STATE_UNAVAILABLE"

    # Connection Epoch Authority (CPA-04)
    adapter_epoch = getattr(feed_adapter_state, "connection_epoch", None) if feed_adapter_state is not None else None
    quote_epoch = None
    if raw_quote is not None:
        quote_epoch = getattr(raw_quote, "connection_epoch", None)
        if quote_epoch is None and isinstance(raw_quote, dict):
            quote_epoch = raw_quote.get("connection_epoch") or raw_quote.get("epoch")

    if quote_source == "REST_FALLBACK":
        is_authoritative_for_epoch = True
    elif adapter_epoch is not None and quote_epoch is not None and adapter_epoch == quote_epoch:
        is_authoritative_for_epoch = True
    else:
        is_authoritative_for_epoch = False
        v_valid = False
        if not v_reason:
            if quote_epoch is not None and adapter_epoch is not None:
                v_reason = "CONNECTION_EPOCH_MISMATCH"
            else:
                v_reason = "CONNECTION_EPOCH_UNVERIFIED"

    # Symbol match
    raw_sym = getattr(raw_quote, "symbol", None) if raw_quote is not None else None
    if raw_sym is None and isinstance(raw_quote, dict):
        raw_sym = raw_quote.get("symbol")
    adapter_sym = getattr(feed_adapter_state, "symbol", None) if feed_adapter_state is not None else None
    if raw_sym and adapter_sym and str(raw_sym).upper() != str(adapter_sym).upper():
        v_valid = False
        if not v_reason:
            v_reason = f"SYMBOL_MISMATCH_{raw_sym}_VS_{adapter_sym}"

    return ObservationProvenance(
        symbol=sym,
        bid=bid,
        ask=ask,
        exchange_ts_ms=exch_ts,
        received_at_ms=recv_ts,
        evaluated_at_ms=evaluated_at_ms,
        quote_source=quote_source,
        transport_state=transport_state,
        connection_epoch=quote_epoch if quote_epoch is not None else adapter_epoch,
        quote_valid=v_valid,
        quote_stale=v_stale,
        reason_code=v_reason,
        is_authoritative_for_epoch=is_authoritative_for_epoch,
    )


def resolve_regime_inputs(
    c_15m: Optional[Sequence[Any]],
    c_5m: Optional[Sequence[Any]],
    c_1m: Optional[Sequence[Any]],
) -> Tuple[List[Any], str, float, str]:
    """
    V16.39 Section 026: Resolves market regime input candles, timeframe,
    causal ATR corresponding to selected timeframe, and data status.
    Prevents using 15M ATR for 1M fallback or using ATR floor as strategy evidence.
    """
    import smc_engine
    if c_15m and len(c_15m) >= 14:
        atr = smc_engine.compute_atr(c_15m, period=14)
        return list(c_15m), "15M", float(atr) if atr > 0 else 2.0, "OK"
    elif c_5m and len(c_5m) >= 14:
        atr = smc_engine.compute_atr(c_5m, period=14)
        return list(c_5m), "5M", float(atr) if atr > 0 else 1.0, "FALLBACK_5M"
    elif c_1m and len(c_1m) >= 14:
        atr = smc_engine.compute_atr(c_1m, period=14)
        return list(c_1m), "1M", float(atr) if atr > 0 else 0.5, "FALLBACK_1M"
    return list(c_15m or c_5m or c_1m or []), "15M", 2.0, "DATA_INSUFFICIENT"


def resolve_model_evaluation_plan(
    model_id: str,
    config: Any,
    regime: str,
    has_m1: bool,
    cycle_authority: Optional[Any] = None,
    adaptive_regime_models: Optional[Set[str]] = None,
) -> Tuple[bool, str, str]:
    """
    V16.39 Section 017: Evaluates whether a model should be evaluated in current cycle.
    Returns (should_evaluate: bool, execution_action: str, reason_code: str).
    """
    exec_profile = getattr(config, "execution_profile", "BASELINE")
    scalp_mode = getattr(config, "scalp_m1_mode", "OFF")

    if scalp_mode == "OFF" and not getattr(config, "daily_ny_research_enabled", False) and not getattr(config, "cg01_enabled", False):
        return False, "DISABLED", "SCALP_MODE_OFF"

    if cycle_authority and getattr(cycle_authority, "has_write_failures", False):
        return False, "DISABLED", "CYCLE_WRITE_FAILED"

    if not has_m1:
        return False, "DISABLED", "M1_DATA_INSUFFICIENT"

    if exec_profile == "PAPER_ADAPTIVE_SMC":
        allowlist = getattr(config, "adaptive_model_allowlist", ())
        if allowlist is None:
            allowlist = SUPPORTED_ADAPTIVE_MODELS
        if model_id not in allowlist:
            return False, "DISABLED", "NOT_IN_ALLOWLIST"

        if adaptive_regime_models is not None and model_id not in adaptive_regime_models and regime != "UNCERTAIN":
            return False, "DISABLED", f"REGIME_DISALLOWED:{regime}"

    # Feature flags
    if model_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value:
        if not (getattr(config, "v16_profile_enabled", True) and getattr(config, "fvg_continuation_enabled", True)):
            return False, "DISABLED", "FVG_CONTINUATION_DISABLED"
    elif model_id == ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value:
        if not (getattr(config, "v16_profile_enabled", True) and getattr(config, "sweep_reclaim_enabled", True)):
            return False, "DISABLED", "SWEEP_RECLAIM_DISABLED"
    elif model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value:
        if not getattr(config, "cg01_enabled", False):
            return False, "DISABLED", "CG01_DISABLED"

    action = "EVALUATE_AND_EXECUTE" if scalp_mode == "PAPER" else "EVALUATE_SHADOW"
    return True, action, "ELIGIBLE_FOR_EVALUATION"


def serialize_cg01_evaluation(
    decision: Optional[Any],
    *,
    enabled: bool = False,
    evaluated: bool = False,
    as_of_ms: int = 0,
    skip_reason: Optional[str] = None,
    armed_order_id: Optional[str] = None,
    guard_summary: Optional[Dict[str, Any]] = None,
    daily_bias: Optional[Dict[str, Any]] = None,
    direction_policy: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pure serializer for CG-01 candidate decision evaluation (V16.9 NHÓM 09, PHẦN 083 - 090, V16.30 Daily Bias).
    Produces a JSON-safe additive structure for orchestrator cycle responses.
    """
    if decision is None:
        stage_str = skip_reason or ("MODEL_DISABLED" if not enabled else "NOT_EVALUATED")
        reasons = [skip_reason] if skip_reason else ([stage_str] if stage_str else [])
        return {
            "enabled": enabled,
            "evaluated": evaluated,
            "as_of_ms": as_of_ms,
            "stage": stage_str,
            "eligible": False,
            "reason_codes": reasons,
            "candidate_key": None,
            "episode_key": None,
            "router_outcome": None,
            "arm_outcome": None,
            "armed_order_id": None,
            "guard_summary": guard_summary,
            "ui_summary_vi": None,
            "poi_id": None,
            "direction": None,
            "daily_bias": daily_bias,
            "direction_policy": direction_policy,
        }

    dec_dict = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
    stage_val = dec_dict.get("stage") or "OBSERVING"
    reasons = dec_dict.get("reason_codes") or []
    cand_obj = getattr(decision, "candidate", None)
    cand_key = getattr(cand_obj, "candidate_key", None) if cand_obj else dec_dict.get("episode_id")
    episode_key = dec_dict.get("episode_id")

    daily_bias_obj = dec_dict.get("daily_bias") if daily_bias is None else daily_bias
    daily_bias_dict = daily_bias_obj.to_dict() if hasattr(daily_bias_obj, "to_dict") else daily_bias_obj
    direction_policy_val = dec_dict.get("direction_policy") if direction_policy is None else direction_policy

    return {
        "enabled": enabled,
        "evaluated": evaluated,
        "as_of_ms": as_of_ms or dec_dict.get("as_of_ms", 0),
        "stage": stage_val,
        "eligible": bool(dec_dict.get("is_eligible", False)),
        "reason_codes": list(reasons),
        "candidate_key": cand_key,
        "episode_key": episode_key,
        "router_outcome": None,
        "arm_outcome": "ARMED" if armed_order_id else None,
        "armed_order_id": armed_order_id,
        "guard_summary": guard_summary,
        "ui_summary_vi": dec_dict.get("ui_summary_vi"),
        "poi_id": dec_dict.get("poi_id"),
        "direction": dec_dict.get("direction"),
        "daily_bias": daily_bias_dict,
        "direction_policy": direction_policy_val,
    }


def validate_arm_invariants(
    *,
    candidate: Any,
    calculation: Any,
    quote: Any,
    now_ms: int,
    expires_at_ms: Optional[int] = None,
    daily_fills_count: int = 0,
    daily_max_cap: int = 3,
    active_armed_count: int = 0,
) -> Tuple[bool, Optional[str]]:
    """
    AURUM V17.5.9 (D03, Section 05 Parts 034-035):
    Pure validator checking ARM gate invariants before persistence.
    Rejects unsupported direction, non-finite or invalid numeric geometry,
    stale/invalid quotes, and capacity violations.
    """
    direction = str(getattr(candidate, "direction", "") or "").upper()
    if direction not in ("SHORT", "SELL", "LONG", "BUY"):
        return False, "INVALID_DIRECTION"

    if calculation is None or not getattr(calculation, "is_valid", False) or not getattr(calculation, "can_execute", False):
        return False, "CALCULATION_INVALID"

    try:
        raw_qty = getattr(calculation, "quantity", 0.0)
        if isinstance(raw_qty, bool) or raw_qty is None:
            return False, "INVALID_QUANTITY"
        qty = float(raw_qty)
        if qty <= 0 or not math.isfinite(qty):
            return False, "INVALID_QUANTITY"
    except (ValueError, TypeError, OverflowError):
        return False, "INVALID_QUANTITY"

    try:
        raw_cand_sl = getattr(candidate, "stop_loss", 0.0)
        raw_entry = getattr(candidate, "planned_entry", 0.0)
        raw_tp = getattr(candidate, "take_profit", 0.0)
        raw_calc_sl = getattr(calculation, "stop_loss", raw_cand_sl)

        if any(isinstance(x, bool) or x is None for x in (raw_cand_sl, raw_entry, raw_tp, raw_calc_sl)):
            return False, "INVALID_PRICE_GEOMETRY"

        cand_sl = float(raw_cand_sl)
        calc_sl = float(raw_calc_sl)
        entry = float(raw_entry)
        tp = float(raw_tp)

        if not (math.isfinite(cand_sl) and math.isfinite(calc_sl) and math.isfinite(entry) and math.isfinite(tp)):
            return False, "INVALID_PRICE_GEOMETRY"
        if cand_sl <= 0 or calc_sl <= 0 or entry <= 0 or tp <= 0:
            return False, "INVALID_PRICE_GEOMETRY"
    except (ValueError, TypeError, OverflowError):
        return False, "INVALID_PRICE_GEOMETRY"

    if abs(cand_sl - calc_sl) > 1e-4:
        return False, "STOP_PLAN_MISMATCH"

    if (daily_fills_count + active_armed_count) >= daily_max_cap:
        return False, "DAILY_CAP_REACHED"

    if quote is None or not getattr(quote, "is_valid", False) or getattr(quote, "is_stale", False):
        return False, "QUOTE_INVALID_OR_STALE"

    if expires_at_ms is not None:
        try:
            exp_ms = int(expires_at_ms)
            if now_ms >= exp_ms:
                return False, "TRIGGER_EXPIRED"
        except (ValueError, TypeError):
            return False, "TRIGGER_EXPIRED"

    if direction in ("SHORT", "SELL"):
        if not (tp < entry < cand_sl):
            return False, "INVALID_PRICE_GEOMETRY"
    elif direction in ("LONG", "BUY"):
        if not (cand_sl < entry < tp):
            return False, "INVALID_PRICE_GEOMETRY"

    return True, None


def resolve_ordered_cost_setting(
    db: Session,
    ordered_keys: Sequence[str],
    *,
    setting_name: str = "cost_setting",
) -> Dict[str, Any]:
    """
    AURUM V17.5.9 (D04, Section 06 Part 048):
    Differentiates missing vs invalid settings under deterministic key precedence.
    If an authoritative higher-priority key is present but invalid, fails closed
    with status='INVALID' rather than silently falling back to lower-priority keys.
    """
    rows = db.query(models.SystemConfig).filter(models.SystemConfig.key.in_(ordered_keys)).all()
    row_map = {r.key: r.value for r in rows if r and r.key is not None}
    for k in ordered_keys:
        if k in row_map:
            raw_val = row_map[k]
            if raw_val is None:
                continue
            if isinstance(raw_val, bool):
                return {
                    "status": "INVALID",
                    "key": k,
                    "value": None,
                    "reason": f"INVALID_{setting_name.upper()}_BOOLEAN",
                }
            try:
                f_val = float(raw_val)
                if not math.isfinite(f_val):
                    return {
                        "status": "INVALID",
                        "key": k,
                        "value": None,
                        "reason": f"INVALID_{setting_name.upper()}_NON_FINITE",
                    }
                if f_val < 0.0:
                    return {
                        "status": "INVALID",
                        "key": k,
                        "value": None,
                        "reason": f"INVALID_{setting_name.upper()}_NEGATIVE",
                    }
                return {
                    "status": "VALID",
                    "key": k,
                    "value": f_val,
                    "reason": None,
                }
            except (ValueError, TypeError, OverflowError):
                return {
                    "status": "INVALID",
                    "key": k,
                    "value": None,
                    "reason": f"INVALID_{setting_name.upper()}_FORMAT",
                }

    return {
        "status": "NOT_CONFIGURED",
        "key": None,
        "value": None,
        "reason": f"NOT_CONFIGURED_{setting_name.upper()}",
    }


def get_system_config_cost_value(db: Session, ordered_keys: Sequence[str]) -> Optional[float]:
    """
    AURUM V17.5.9 (D04, Section 06 Part 048):
    Compatibility adapter around resolve_ordered_cost_setting.
    Returns finite non-negative float if valid, or None if not configured or invalid.
    Does NOT silently fall back if an authoritative key has an invalid value.
    """
    res = resolve_ordered_cost_setting(db, ordered_keys)
    if res["status"] == "VALID":
        return res["value"]
    return None


class ScalpWatcherOrchestrator:
    def __init__(self):
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock() if hasattr(asyncio, "Lock") else None
        self._active_zones: Dict[str, List[WatchedZone]] = {}  # symbol -> list of zones
        self._last_alert_times: Dict[str, int] = {}            # zone_id:alert_type -> timestamp
        self._running = False
        self._last_m1_evaluated_ts: Dict[str, int] = {}
        self._active_hypotheses: Dict[str, List[Any]] = {}
        self._active_liquidity_levels: Dict[str, List[Any]] = {}
        self._active_dealing_range: Dict[str, Any] = {}
        self._active_regime: Dict[str, str] = {}
        self._active_zone_assessments: Dict[str, Dict[str, Any]] = {}
        self._active_market_context: Dict[str, Dict[str, Any]] = {}
        self._latest_hypothesis_batch_result: Dict[str, Any] = {}
        self._latest_hypothesis_authority: Dict[str, Any] = {}
        self._latest_cycle_outcome: Dict[str, Dict[str, Any]] = {}
        self._arm_rejections: Dict[str, Any] = {}
        self._latest_cg01_evaluation: Dict[str, Dict[str, Any]] = {}
        self._latest_candidate_quality_display: Dict[str, Dict[str, Any]] = {}

    def record_candidate_quality_display(
        self,
        *,
        symbol: str,
        setup_id: Optional[str],
        setup_instance_id: Optional[str],
        candidate_key: Optional[str],
        direction: str,
        assessment: Any,
    ) -> None:
        """
        Rules 021-030, 083-085, V16.39.12:
        Caches volatile candidate quality display for setups before ARM.
        Bounded cardinality (max 500), exact structured key (CandidateQualityDisplayKey).
        Never splits strings by ':' or bleeds identity across setup instances.
        """
        if not assessment or not hasattr(assessment, "to_dict"):
            return

        import copy
        from services.order_quality_display_service import (
            CandidateQualityDisplayKey,
            make_candidate_quality_display_key,
            CandidateScoreDisplay,
        )

        assessment_dict = copy.deepcopy(assessment.to_dict())
        assessed_cand_key = assessment_dict.get("candidate_key")
        effective_cand_key = candidate_key or assessed_cand_key or setup_instance_id

        key = make_candidate_quality_display_key(
            symbol=symbol,
            setup_id=setup_id,
            setup_instance_id=setup_instance_id or effective_cand_key,
            candidate_key=effective_cand_key,
            direction=direction,
        )

        display = CandidateScoreDisplay(
            status="AVAILABLE",
            score=assessment_dict["final_score"],
            max_score=100.0,
            base_score=assessment_dict.get("base_score"),
            lesson_delta=assessment_dict.get("lesson_delta"),
            threshold=assessment_dict.get("threshold"),
            decision=assessment_dict.get("decision"),
            as_of_ms=assessment_dict.get("as_of_ms"),
            source="VOLATILE_LATEST_EVALUATION",
            frozen=False,
            assessment_id=assessment_dict.get("assessment_id"),
            candidate_key=effective_cand_key,
            model_id=assessment_dict.get("model_id"),
            profile_version=assessment_dict.get("profile_version"),
            config_fingerprint=assessment_dict.get("config_fingerprint"),
            reason_code=None,
        )

        with self._lock:
            # Rule 029: Bounded cache (max 500). If key already exists, update without evicting.
            if key not in self._latest_candidate_quality_display and len(self._latest_candidate_quality_display) >= 500:
                oldest_k = next(iter(self._latest_candidate_quality_display))
                self._latest_candidate_quality_display.pop(oldest_k, None)
            self._latest_candidate_quality_display[key] = copy.deepcopy(display.to_dict())

    def get_candidate_quality_display(
        self,
        *,
        symbol: str = "XAUUSDT",
        setup_id: Optional[str] = None,
        setup_instance_id: Optional[str] = None,
        candidate_key: Optional[str] = None,
        direction: str = "LONG",
    ) -> Optional[Dict[str, Any]]:
        """
        Rules 023-024, 086, V16.39.12:
        Reads volatile pre-ARM candidate quality display matching exact binding.
        Exact-only: Never bleeds scores across distinct setup instances or setup IDs.
        """
        import copy
        from services.order_quality_display_service import make_candidate_quality_display_key

        query_key = make_candidate_quality_display_key(
            symbol=symbol,
            setup_id=setup_id,
            setup_instance_id=setup_instance_id,
            candidate_key=candidate_key,
            direction=direction,
        )

        with self._lock:
            # 1. Direct exact key lookup
            if query_key in self._latest_candidate_quality_display:
                return copy.deepcopy(self._latest_candidate_quality_display[query_key])

            # 2. Iterate backwards for exact matching across alias fields (candidate_key / setup_instance_id)
            for k, val in reversed(list(self._latest_candidate_quality_display.items())):
                # Must match symbol and direction
                if k.symbol != query_key.symbol or k.direction != query_key.direction:
                    continue

                # If query specified setup_instance_id: MUST match key's setup_instance_id or candidate_key
                if query_key.setup_instance_id is not None:
                    k_inst = k.setup_instance_id or k.candidate_key
                    if k_inst != query_key.setup_instance_id:
                        continue

                # If query specified candidate_key: MUST match key's candidate_key or setup_instance_id
                if query_key.candidate_key is not None:
                    k_ck = k.candidate_key or k.setup_instance_id
                    if k_ck != query_key.candidate_key:
                        continue

                # If query specified setup_id: if key has setup_id, it must match
                if query_key.setup_id is not None:
                    if k.setup_id is not None and k.setup_id != query_key.setup_id:
                        continue

                # Rule 023: If query had NEITHER setup_instance_id nor candidate_key,
                # do NOT return an entry that belongs to a specific instance!
                if query_key.setup_instance_id is None and query_key.candidate_key is None:
                    if k.setup_instance_id is not None or k.candidate_key is not None:
                        continue
                    if query_key.setup_id is not None and k.setup_id != query_key.setup_id:
                        continue

                return copy.deepcopy(val)

        return None


    def get_latest_cg01_evaluation(self, symbol: str = "XAUUSDT") -> Dict[str, Any]:
        with self._lock:
            eval_data = self._latest_cg01_evaluation.get(symbol)
            if eval_data:
                return dict(eval_data)
        return serialize_cg01_evaluation(None, enabled=False, evaluated=False, as_of_ms=int(time.time() * 1000))

    def _record_and_broadcast_cg01_evaluation(self, symbol: str, cg01_eval_data: Dict[str, Any]):
        with self._lock:
            self._latest_cg01_evaluation[symbol] = dict(cg01_eval_data)
        try:
            from services.market_broadcaster import market_broadcaster
            market_broadcaster.dispatch_cg01_evaluation(cg01_eval_data, symbol=symbol)
        except Exception as e:
            logger.debug(f"Failed to dispatch cg01 evaluation broadcast for {symbol}: {e}")

    def get_latest_hypothesis_batch_result(self, symbol: str = "XAUUSDT") -> Optional[Any]:
        with self._lock:
            return self._latest_hypothesis_batch_result.get(symbol)

    def get_latest_hypothesis_authority(self, symbol: str = "XAUUSDT") -> Optional[Any]:
        with self._lock:
            return self._latest_hypothesis_authority.get(symbol)

    def get_latest_cycle_outcome(self, symbol: str = "XAUUSDT") -> Optional[Dict[str, Any]]:
        with self._lock:
            outcome = self._latest_cycle_outcome.get(symbol)
            return dict(outcome) if outcome else None

    def get_active_zones(self, symbol: str = "XAUUSDT") -> List[WatchedZone]:
        with self._lock:
            return list(self._active_zones.get(symbol, []))

    def set_active_zones(self, symbol: str, zones: List[WatchedZone]):
        with self._lock:
            self._active_zones[symbol] = list(zones)

    def get_active_hypotheses(self, symbol: str = "XAUUSDT") -> List[Any]:
        with self._lock:
            return list(self._active_hypotheses.get(symbol, []))

    def get_active_liquidity_levels(self, symbol: str = "XAUUSDT") -> List[Any]:
        with self._lock:
            return list(self._active_liquidity_levels.get(symbol, []))

    def get_zone_assessment_explanation(self, symbol: str, zone_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns real multi-dimensional assessment and Vietnamese explanation for zone (SECTIONS 083 - 086, 164).
        Never uses hardcoded scores or fake constants. Returns None (404) if zone not found.
        """
        with self._lock:
            assessments = self._active_zone_assessments.get(symbol, {})
            if zone_id in assessments:
                dto = dict(assessments[zone_id])
                dto["win_probability"] = None
                return dto

            zones = self._active_zones.get(symbol, [])
            matching = [z for z in zones if z.zone_id == zone_id]
            if not matching:
                return None
            zone = matching[0]

            ctx = self._active_market_context.get(symbol, {})
            c_px = ctx.get("current_price", zone.midpoint)
            c_atr = ctx.get("atr", 1.0)
            c_as_of = ctx.get("as_of_ms", 0)
            dr = self._active_dealing_range.get(symbol)
            levels = self._active_liquidity_levels.get(symbol, [])
            hyps = self._active_hypotheses.get(symbol, [])
            matching_hyp = next((h for h in hyps if getattr(h, "watched_zone", None) and h.watched_zone.zone_id == zone_id), None)
            from services.liquidity_scenario_service import rank_zones_multi_dimensional, build_zone_assessment_explanation
            if dr is not None:
                pairs = rank_zones_multi_dimensional([zone], levels, dr, c_px, c_atr, now_ms=c_as_of)
                if pairs:
                    return build_zone_assessment_explanation(
                        zone, pairs[0][1], matching_hyp, current_price=c_px, atr=c_atr, as_of_ms=c_as_of, is_stale=True
                    )
            return None

    def evaluate_cycle(
        self,
        db: Session,
        symbol: str = "XAUUSDT",
        *,
        now_ms: Optional[int] = None,
        clock: Any = None,
        ticker: Optional[Dict[str, Any]] = None,
        quote: Any = None,
        candles_1m: Optional[Sequence[Any]] = None,
        candles_5m: Optional[Sequence[Any]] = None,
        candles_15m: Optional[Sequence[Any]] = None,
        candles_1h: Optional[Sequence[Any]] = None,
        candles_4h: Optional[Sequence[Any]] = None,
        candles_1d: Optional[Sequence[Any]] = None,
        htf_bias: Optional[str] = None,
        h1_alignment: Optional[str] = None,
        config: Optional[M1ScalpConfig] = None,
        auto_enabled: Optional[bool] = None,
        feed_health: Optional[Dict[str, Any]] = None,
        execution_financial_context: Optional[Any] = None,
        research_context: Optional[Any] = None,
        caller_identity: Optional[str] = None,
        evaluation_origin: Optional[str] = None,
        execution_scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Single-cycle autonomous evaluation (PHẦN 015 - 024, 054 - 056).
        Can be driven synchronously by tests / replay or asynchronously by background watcher.
        Uses load_effective_scalp_config(db) if config is not injected.
        """
        t_recv = int(now_ms if now_ms is not None else int(time.time() * 1000))
        t_start = int(time.time() * 1000)
        t_perf_start = time.perf_counter()
        cycle_id = f"cycle-{t_recv}-{uuid.uuid4().hex[:8]}"
        cfg = config if config is not None else load_effective_scalp_config(db)

        # Resolve effective observation run identity (Phase D, PART 043-050)
        effective_run_id = None
        if research_context is not None and getattr(research_context, "run_id", None):
            effective_run_id = research_context.run_id
        elif db is not None:
            try:
                from contextlib import nullcontext
                from services.paper_research_run_service import resolve_active_observation_run
                with getattr(db, "no_autoflush", nullcontext()):
                    active_run = resolve_active_observation_run(db, scope=execution_scope or "CG01", symbol=symbol)
                if active_run:
                    effective_run_id = active_run.run_id
            except Exception:
                pass

        eff_evidence_origin = evaluation_origin or "LIVE_FORWARD"

        # Validate quote & price for initial cycle heartbeat quality (EVID-01, EVID-32)
        q_init = None
        if quote is not None:
            q_init = normalize_execution_quote(quote, now_ms=t_recv)
        else:
            raw_t = ticker if ticker is not None else collector_service.latest_ticker
            q_init = normalize_execution_quote(raw_t, now_ms=t_recv) if raw_t else None

        bid_val = float(getattr(q_init, "bid", 0.0) if hasattr(q_init, "bid") else (q_init or {}).get("bid", 0.0))
        ask_val = float(getattr(q_init, "ask", 0.0) if hasattr(q_init, "ask") else (q_init or {}).get("ask", 0.0))
        is_fresh_quote = bool(
            q_init is not None
            and getattr(q_init, "is_valid", False)
            and not getattr(q_init, "is_stale", False)
            and bid_val > 0
            and ask_val >= bid_val
        )
        from services.bitget_ws_service import bitget_ws_service
        raw_source_quote = quote if quote is not None else (ticker if ticker is not None else collector_service.latest_ticker)
        prov = resolve_watcher_observation_provenance(
            raw_quote=raw_source_quote,
            validated_quote=q_init,
            feed_adapter_state=bitget_ws_service,
            evaluated_at_ms=t_recv,
        )
        canonical_quote_snap = prov.to_snapshot_dict()
        is_feed_authoritative = (
            prov.quote_valid
            and not prov.quote_stale
            and prov.transport_state == "CONNECTED"
            and prov.quote_source == "WS"
            and prov.is_authoritative_for_epoch
        )
        initial_coverage_quality = "GOOD" if is_feed_authoritative else (
            "DOWN" if prov.transport_state in ("DISCONNECTED", "DOWN") else "DEGRADED"
        )

        try:
            from services.engine_observability_service import engine_observability_service
            engine_observability_service.record_engine_heartbeat(
                db,
                symbol=symbol,
                run_id=effective_run_id,
                state="RUNNING",
                heartbeat_at_ms=t_recv,
                last_cycle_id=cycle_id,
                evaluation_started_at_ms=t_recv,
                metadata={
                    "worker_role": "EVALUATION_OWNER",
                    "caller_identity": caller_identity or "ORCHESTRATOR",
                    "evaluation_origin": eff_evidence_origin,
                    "evidence_origin": eff_evidence_origin,
                    "coverage_quality": initial_coverage_quality,
                    "is_feed_healthy": is_feed_authoritative,
                    "is_quote_fresh": is_fresh_quote,
                    "canonical_quote": canonical_quote_snap,
                    "scope": execution_scope or "CG01",
                    "mode": "PAPER",
                },
            )
        except Exception as hb_err:
            logger.warning(f"Error recording watcher heartbeat telemetry: {hb_err}")

        # Global auto stop policy check (PHẦN 024)
        if auto_enabled is None:
            try:
                from services.strategy_service import strategy_service
                global_auto = strategy_service.get_auto_state(db)
            except Exception:
                global_auto = False  # Fail closed for execution (PHẦN 024)
        else:
            global_auto = bool(auto_enabled)

        # Check feature flag
        scalp_mode = cfg.scalp_m1_mode
        if (scalp_mode == ScalpMode.OFF.value or not getattr(cfg, "scalp_m1_enabled", True)) and not cfg.daily_ny_research_enabled and not getattr(cfg, "cg01_enabled", False):
            cg01_eval = serialize_cg01_evaluation(None, enabled=False, evaluated=False, skip_reason="MODEL_DISABLED", as_of_ms=t_recv)
            self._record_and_broadcast_cg01_evaluation(symbol, cg01_eval)
            dur_ms = max(0.0, (time.perf_counter() - t_perf_start) * 1000)
            t_done = (t_recv + int(dur_ms)) if now_ms is not None else int(time.time() * 1000)
            try:
                from services.engine_observability_service import engine_observability_service
                engine_observability_service.record_evaluation_cycle(
                    db,
                    cycle_id=cycle_id,
                    run_id=effective_run_id,
                    started_at_ms=t_recv,
                    completed_at_ms=t_done,
                    status="SKIPPED",
                    symbol=symbol,
                    duration_ms=dur_ms,
                    skip_reason="SCALP_AUTO_PAPER_DISABLED" if not getattr(cfg, "scalp_m1_enabled", True) else "MODEL_DISABLED",
                    decision_status="SKIPPED",
                    evidence_origin=eff_evidence_origin,
                    canonical_quote=canonical_quote_snap,
                )
            except Exception:
                pass
            return {
                "cycle_id": cycle_id,
                "status": "DISABLED",
                "decision_status": "SKIPPED",
                "scalp_mode": scalp_mode,
                "as_of_ms": t_recv,
                "zones_count": 0,
                "candidates_count": 0,
                "candidates": [],
                "armed_order_id": None,
                "cg01_evaluation": cg01_eval,
            }

        # Validate quote & price using canonical execution quote normalizer (V15.8 PROMPT 086-096, 116-119)
        if quote is not None:
            q = normalize_execution_quote(quote, now_ms=t_recv)
        else:
            raw_t = ticker if ticker is not None else collector_service.latest_ticker
            q = normalize_execution_quote(raw_t, now_ms=t_recv) if raw_t else None

        bid = float(getattr(q, "bid", 0.0) if hasattr(q, "bid") else (q or {}).get("bid", 0.0))
        ask = float(getattr(q, "ask", 0.0) if hasattr(q, "ask") else (q or {}).get("ask", 0.0))
        import math
        is_q_valid = (
            q is not None
            and bool(getattr(q, "is_valid", False))
            and not bool(getattr(q, "is_stale", False))
            and bid > 0
            and ask >= bid
            and math.isfinite(bid)
            and math.isfinite(ask)
        )

        if not is_q_valid:
            cg01_eval = serialize_cg01_evaluation(None, enabled=bool(getattr(cfg, "cg01_enabled", True)), evaluated=False, skip_reason="NO_QUOTE", as_of_ms=t_recv)
            self._record_and_broadcast_cg01_evaluation(symbol, cg01_eval)
            dur_ms = max(0.0, (time.perf_counter() - t_perf_start) * 1000)
            t_done = (t_recv + int(dur_ms)) if now_ms is not None else int(time.time() * 1000)
            try:
                from services.engine_observability_service import engine_observability_service
                engine_observability_service.record_evaluation_cycle(
                    db,
                    cycle_id=cycle_id,
                    run_id=effective_run_id,
                    started_at_ms=t_recv,
                    completed_at_ms=t_done,
                    status="SKIPPED",
                    symbol=symbol,
                    duration_ms=dur_ms,
                    skip_reason="NO_VALID_QUOTE",
                    decision_status="SKIPPED",
                    evidence_origin=eff_evidence_origin,
                )
            except Exception:
                pass
            return {
                "cycle_id": cycle_id,
                "status": "NO_QUOTE",
                "decision_status": "SKIPPED",
                "as_of_ms": t_recv,
                "zones_count": 0,
                "candidates_count": 0,
                "candidates": [],
                "armed_order_id": None,
                "cg01_evaluation": cg01_eval,
            }

        current_px = (bid + ask) / 2.0

        # Check whether caller explicitly injected HTF candles or bias (Rules 105, 106)
        caller_injected_htf = (candles_4h is not None or candles_1h is not None or htf_bias is not None)

        # Pre-resolve cg01_cfg early for HTF settings synchronization (Rules 016, 087, 088, V16.21 Rules 046-065, V16.22 Rules 071-085)
        from services.cg01_config_factory import resolve_canonical_cg01_config
        is_explicit = (config is not None and getattr(config, "_is_explicit_override", True))
        cg01_cfg, _, _ = resolve_canonical_cg01_config(
            db=db,
            symbol=symbol,
            scalp_config_override=cfg,
            is_explicit_override=is_explicit,
        )
        cg01_mode = cg01_cfg.profile_mode

        # 1. Load or use injected candles
        c_4h = candles_4h if candles_4h is not None else (crud.get_candles(db, symbol, "4H", limit=40, ascending=True, cutoff_ms=t_recv) if db else [])
        c_1h = candles_1h if candles_1h is not None else (crud.get_candles(db, symbol, "1H", limit=60, ascending=True, cutoff_ms=t_recv) if db else [])
        c_15m = candles_15m if candles_15m is not None else (crud.get_candles(db, symbol, "15M", limit=120, ascending=True, cutoff_ms=t_recv) if db else [])
        c_5m = candles_5m if candles_5m is not None else (crud.get_candles(db, symbol, "5M", limit=150, ascending=True, cutoff_ms=t_recv) if db else [])
        c_1m = candles_1m if candles_1m is not None else (crud.get_candles(db, symbol, "1M", limit=100, ascending=True, cutoff_ms=t_recv) if db else [])
        c_1d = candles_1d if candles_1d is not None else ((crud.get_candles(db, symbol, "1D", limit=30, ascending=True, cutoff_ms=t_recv) or crud.get_candles(db, symbol, "D", limit=30, ascending=True, cutoff_ms=t_recv)) if db else [])

        from services.cg01_context_adapter import normalize_cg01_candle
        if c_4h and isinstance(c_4h[0], dict):
            c_4h = [normalize_cg01_candle(b, "4H", symbol) for b in c_4h]
        if c_1h and isinstance(c_1h[0], dict):
            c_1h = [normalize_cg01_candle(b, "1H", symbol) for b in c_1h]
        if c_15m and isinstance(c_15m[0], dict):
            c_15m = [normalize_cg01_candle(b, "15M", symbol) for b in c_15m]
        if c_5m and isinstance(c_5m[0], dict):
            c_5m = [normalize_cg01_candle(b, "5M", symbol) for b in c_5m]
        if c_1m and isinstance(c_1m[0], dict):
            c_1m = [normalize_cg01_candle(b, "1M", symbol) for b in c_1m]
        if c_1d and isinstance(c_1d[0], dict):
            c_1d = [normalize_cg01_candle(b, "1D", symbol) for b in c_1d]

        import smc_engine
        atr_15m = smc_engine.compute_atr(c_15m, period=14) if c_15m else 2.0
        if atr_15m <= 0:
            atr_15m = 2.0

        # V17.5: Stop Placement Policy & Volatility Context resolved once per cycle (PHẦN 021, 029, 081)
        from services.stop_placement_policy_service import resolve_stop_placement_policy
        from services.stop_placement_service import compute_stop_atr
        stop_policy = resolve_stop_placement_policy(db)

        from services.stop_placement_types import StopATRResult
        atr_m1_obj = None
        atr_m5_obj = None
        if c_1m:
            try:
                atr_m1_obj = compute_stop_atr(c_1m, period=stop_policy.atr_period, timeframe="1M", as_of_ms=t_recv, symbol=symbol)
            except Exception as e:
                atr_m1_obj = StopATRResult(
                    status="UNAVAILABLE",
                    value=0.0,
                    period=stop_policy.atr_period,
                    timeframe="1M",
                    method=stop_policy.atr_method,
                    reason_code=f"EXCEPTION_{type(e).__name__}",
                    symbol=symbol,
                    calculated_as_of=t_recv,
                    decision_cutoff=t_recv,
                )
        if atr_m1_obj is None:
            atr_m1_obj = StopATRResult(
                status="UNAVAILABLE",
                value=0.0,
                period=stop_policy.atr_period,
                timeframe="1M",
                method=stop_policy.atr_method,
                reason_code="MISSING_1M_CANDLE_FEED",
                symbol=symbol,
                calculated_as_of=t_recv,
                decision_cutoff=t_recv,
            )

        if c_5m:
            try:
                atr_m5_obj = compute_stop_atr(c_5m, period=stop_policy.atr_period, timeframe="5M", as_of_ms=t_recv, symbol=symbol)
            except Exception as e:
                atr_m5_obj = StopATRResult(
                    status="UNAVAILABLE",
                    value=0.0,
                    period=stop_policy.atr_period,
                    timeframe="5M",
                    method=stop_policy.atr_method,
                    reason_code=f"EXCEPTION_{type(e).__name__}",
                    symbol=symbol,
                    calculated_as_of=t_recv,
                    decision_cutoff=t_recv,
                )

        stop_context = {
            "atr_m1": atr_m1_obj,
            "atr_m5": atr_m5_obj,
            "bid": bid,
            "ask": ask,
        }

        # Rules 105 - 108: Causal HTF Resolution for evaluate_cycle
        from services.smc_regime_service import build_htf_context, h1_alignment_from

        if htf_bias is not None:
            bias = htf_bias
            align = h1_alignment if h1_alignment is not None else "UNKNOWN"
            context_source = "INJECTED_REPLAY"
        elif caller_injected_htf:
            # Explicit candle injection (even if empty list)
            h4_ctx = build_htf_context(c_4h, "4H", as_of_ms=t_recv, symbol=symbol, config=cg01_cfg)
            bias = getattr(h4_ctx, "bias", "UNKNOWN")
            if candles_1h is not None:
                h1_ctx = build_htf_context(c_1h, "1H", as_of_ms=t_recv, symbol=symbol, config=cg01_cfg)
                align = h1_alignment_from(h4_ctx, h1_ctx)
            else:
                align = "UNKNOWN"
            context_source = "INJECTED_REPLAY"
        elif c_4h or c_1h:
            # Database-loaded candles
            h4_ctx = build_htf_context(c_4h, "4H", as_of_ms=t_recv, symbol=symbol, config=cg01_cfg)
            bias = getattr(h4_ctx, "bias", "UNKNOWN")
            h1_ctx = build_htf_context(c_1h, "1H", as_of_ms=t_recv, symbol=symbol, config=cg01_cfg)
            align = h1_alignment_from(h4_ctx, h1_ctx)
            context_source = "DATABASE_LOADED"
        else:
            # Fallback to live collector singleton only when caller did not provide HTF candles and DB had none
            bias = collector_service.d_4h_bias
            align = collector_service.h1_alignment
            context_source = "LIVE_COLLECTOR"

        # 2. Watchlist update from 15M/5M closures
        existing_zones = self.get_active_zones(symbol)
        extracted = ZoneWatchlistService.extract_zones_from_candles(c_15m, "15M", symbol, as_of_ms=t_recv, atr=atr_15m)
        if c_5m:
            extracted += ZoneWatchlistService.extract_zones_from_candles(c_5m, "5M", symbol, as_of_ms=t_recv, atr=atr_15m)

        # Merge new zones by zone_id
        zone_map = {z.zone_id: z for z in existing_zones}
        for nz in extracted:
            if nz.zone_id not in zone_map:
                zone_map[nz.zone_id] = nz
                OpportunityFunnelService.record_opportunity_transition(
                    db,
                    OpportunityEvaluation(
                        opportunity_id=f"opp_{nz.zone_id}",
                        symbol=symbol,
                        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
                        direction=nz.direction,
                        zone_id=nz.zone_id,
                        as_of_ms=t_recv,
                        stage=OpportunityStage.ZONE_DETECTED.value,
                        market_event_received_at=t_recv,
                    ),
                    now_ms=t_recv,
                )

        # 3. Proximity evaluation & ranking with hysteresis
        current_zones = list(zone_map.values())
        for z in current_zones:
            prev_state = z.state
            new_state, dist_atr = ZoneWatchlistService.evaluate_zone_proximity(z, bid, ask, atr_15m, cfg)
            z.state = new_state
            z.proximity_distance_atr = dist_atr
            z.last_evaluated_at = t_recv

            is_touching = (new_state == ZoneState.TOUCHED.value)
            new_episode = ZoneWatchlistService.track_zone_touch_episode(z, bar_id_or_ts=t_recv, is_touching=is_touching)
            if new_episode and prev_state != ZoneState.TOUCHED.value:
                OpportunityFunnelService.record_opportunity_transition(
                    db,
                    OpportunityEvaluation(
                        opportunity_id=f"opp_{z.zone_id}",
                        symbol=symbol,
                        direction=z.direction,
                        zone_id=z.zone_id,
                        as_of_ms=t_recv,
                        stage=OpportunityStage.ZONE_TOUCHED.value,
                        market_event_received_at=t_recv,
                    ),
                    now_ms=t_recv,
                )

            # NEAR notification with hysteresis and cooldown
            if new_state == ZoneState.NEAR.value and prev_state != ZoneState.NEAR.value:
                alert_key = f"{z.zone_id}:NEAR"
                last_alert = self._last_alert_times.get(alert_key, 0)
                if (t_recv - last_alert) > 15 * 60 * 1000:
                    self._last_alert_times[alert_key] = t_recv
                    OpportunityFunnelService.record_opportunity_transition(
                        db,
                        OpportunityEvaluation(
                            opportunity_id=f"opp_{z.zone_id}",
                            symbol=symbol,
                            direction=z.direction,
                            zone_id=z.zone_id,
                            as_of_ms=t_recv,
                            stage=OpportunityStage.PRICE_NEAR.value,
                            market_event_received_at=t_recv,
                        ),
                        now_ms=t_recv,
                    )
                    # Queue Telegram notification (Rules 015, 051 - 057)
                    if scalp_mode != ScalpMode.OFF.value:
                        from services.proximity_service import compute_zone_proximity_metrics
                        near_metrics = compute_zone_proximity_metrics(
                            price=current_px,
                            zone_low=z.bottom,
                            zone_high=z.top,
                            atr=atr_15m,
                            price_basis="MID",
                            floor_atr=0.10,
                        )
                        queue_telegram_notification(
                            db=db,
                            item_type="NEAR_ENTRY",
                            data={
                                "setup_id": z.zone_id,
                                "direction": z.direction,
                                "reference_price": current_px,
                                "entry_zone_low": z.bottom,
                                "entry_zone_high": z.top,
                                "entry_target": z.midpoint,
                                "distance_price": near_metrics.distance_price,
                                "distance_atr": near_metrics.distance_atr,
                                "midpoint_distance": near_metrics.midpoint_distance,
                                "atr": near_metrics.effective_atr,
                                "measured_atr": near_metrics.measured_atr,
                                "effective_atr": near_metrics.effective_atr,
                                "atr_effective_floor_used": near_metrics.atr_effective_floor_used,
                                "price_basis": "MID",
                                "auto_paper_enabled": (scalp_mode == ScalpMode.PAPER.value and global_auto),
                            },
                            occurred_at=t_recv,
                        )

        # Build Liquidity Map, Dealing Range & Hypotheses (SECTIONS 028, 071 - 096)
        from services.liquidity_scenario_service import (
            identify_confirmed_swings,
            build_liquidity_hypotheses,
            compute_dealing_range,
            classify_market_regime,
            rank_zones_multi_dimensional,
            build_zone_assessment_explanation,
            load_durable_hypotheses_for_symbol,
            stage_hypothesis_lifecycle,
            stage_hypothesis_status_transition,
            get_hypothesis_storage_key,
            LifecycleStageResult,
            HypothesisLoadStatus,
            HypothesisBatchLoadResult,
            is_lifecycle_stage_success,
            HypothesisWriteOutcome,
            LifecycleCycleAuthority,
            derive_cycle_status,
            check_candidate_lifecycle_authority,
        )
        from services.smc_liquidity_service import build_liquidity_map, update_liquidity_states

        ref_candles, ref_tf, atr_ref, regime_data_status = resolve_regime_inputs(c_15m, c_5m, c_1m)
        atr_15m = atr_ref
        shs, sls = identify_confirmed_swings(ref_candles, timeframe=ref_tf, left_bars=2, right_bars=2, symbol=symbol, as_of_ms=t_recv) if ref_candles else ([], [])
        liq_levels = build_liquidity_map(
            symbol=symbol,
            timeframe=ref_tf,
            swing_highs=shs,
            swing_lows=sls,
            now_ms=t_recv,
        )
        if c_1m:
            update_liquidity_states(liq_levels, c_1m, t_recv, candle_duration_ms=60_000)

        dealing_range = compute_dealing_range(shs + sls, t_recv, symbol=symbol)
        regime = classify_market_regime(ref_candles if ref_candles else c_1m, atr_15m, t_recv)
        regime_snapshot = {
            "regime": regime,
            "timeframe": ref_tf,
            "atr_reference": float(atr_15m) if atr_15m else 1.0,
            "data_status": regime_data_status,
            "known_at_ms": t_recv,
            "classifier_version": "v16.39",
        }
        sr_zones = []
        if getattr(cfg, "execution_profile", "BASELINE") == "PAPER_ADAPTIVE_SMC":
            try:
                from services.support_resistance_service import build_support_resistance_zones
                sr_zones = build_support_resistance_zones(
                    symbol=symbol,
                    swings=(shs + sls),
                    closed_candles=ref_candles or c_1m or (),
                    as_of_ms=t_recv,
                    tick_size=float(getattr(cfg, "tick_size", 0.01) or 0.01),
                    atr=float(atr_15m) if atr_15m else 1.0,
                    timeframe=ref_tf,
                )
            except Exception as sr_err:
                logger.warning(f"[SRZone] Error building causal S/R zones: {sr_err}")

        policy = TradingPolicyService.get_or_create_policy(db)
        session_ctx = resolve_entry_session_context(policy, "STANDARD_SMC", now_ms=t_recv, clock=clock)

        # Multi-dimensional ranking of active zones (SECTIONS 081, 097, 098)
        scored_pairs = rank_zones_multi_dimensional(
            current_zones, liq_levels, dealing_range, current_px, atr_15m, htf_bias=bias, now_ms=t_recv
        )
        ranked_zones = [p[0] for p in scored_pairs]
        rank_scores_by_id = {p[0].zone_id: p[1] for p in scored_pairs}

        bounded_zones = ZoneWatchlistService.prune_and_bound_watchlist(ranked_zones, current_px, t_recv, cfg)
        self.set_active_zones(symbol, bounded_zones)

        # Load durable hypothesis lifecycle from DB (SECTIONS 042, 046, 061 - 087)
        batch_result = load_durable_hypotheses_for_symbol(db, symbol)
        durable_hyps_map = batch_result.records
        blocked_keys = set(batch_result.blocked_keys)
        blocked_ids = set(batch_result.blocked_identities)

        # Clean in-memory cache if any cached hypothesis is corrupted in DB (SECTION 096)
        existing_hyps = [
            h for h in self._active_hypotheses.get(symbol, [])
            if h.hypothesis_id not in blocked_ids
            and get_hypothesis_storage_key(symbol, h.hypothesis_id) not in blocked_keys
        ]
        existing_ids = {h.hypothesis_id for h in existing_hyps}
        for d_id, d_rec in durable_hyps_map.items():
            if d_id not in existing_ids:
                existing_hyps.append(d_rec)

        use_multi_zone = bool(
            getattr(cfg, "v16_profile_enabled", True)
            and getattr(cfg, "multi_zone_hypotheses_enabled", False)
        )
        if use_multi_zone:
            from services.liquidity_scenario_service import build_execution_hypotheses_for_zones
            unfiltered_hyps = build_execution_hypotheses_for_zones(
                bounded_zones, liq_levels, dealing_range, current_px, regime, t_recv,
                session_cap_ms=session_ctx.expiry_cap_ms if session_ctx else None,
                existing_hypotheses=existing_hyps,
            )
        else:
            unfiltered_hyps = build_liquidity_hypotheses(
                bounded_zones, liq_levels, dealing_range, current_px, regime, t_recv,
                session_cap_ms=session_ctx.expiry_cap_ms if session_ctx else None,
                existing_hypotheses=existing_hyps,
            )

        hypotheses: List[Any] = []
        blocked_zone_issues: Dict[str, Any] = {}
        for h in unfiltered_hyps:
            h_key = get_hypothesis_storage_key(symbol, h.hypothesis_id)
            if h.hypothesis_id in blocked_ids or h_key in blocked_keys:
                matching_issue = next(
                    (iss for iss in batch_result.issues if iss.storage_key == h_key or iss.hypothesis_id == h.hypothesis_id),
                    None
                )
                if getattr(h, "watched_zone", None):
                    blocked_zone_issues[h.watched_zone.zone_id] = matching_issue
                continue
            hypotheses.append(h)

        # Stage durable hypothesis records in same cycle transaction (SECTIONS 040, 043, 061 - 065)
        staged_ids = set()
        entry_authorized_hypotheses: List[Any] = []
        cycle_authority = LifecycleCycleAuthority()

        for h in hypotheses:
            h_key = get_hypothesis_storage_key(symbol, h.hypothesis_id)
            z_id = h.watched_zone.zone_id if getattr(h, "watched_zone", None) else None
            try:
                res = stage_hypothesis_lifecycle(db, symbol, h, now_ms=t_recv)
            except Exception as e:
                logger.warning(f"Exception during stage_hypothesis_lifecycle for {h.hypothesis_id}: {e}")
                res = LifecycleStageResult.FAILED

            success = is_lifecycle_stage_success(res)
            rcode = None
            if not success:
                if res == LifecycleStageResult.BLOCKED:
                    rcode = OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.value
                    cycle_authority.has_write_blocks = True
                else:
                    rcode = OpportunityReasonCode.HYPOTHESIS_WRITE_FAILED.value
                    cycle_authority.has_write_failures = True
                cycle_authority.write_issues_count += 1
            else:
                staged_ids.add(h.hypothesis_id)
                entry_authorized_hypotheses.append(h)

            outcome = HypothesisWriteOutcome(
                hypothesis_id=h.hypothesis_id,
                storage_key=h_key,
                zone_id=z_id,
                operation="UPSERT_HYPOTHESIS",
                result=res if isinstance(res, LifecycleStageResult) else LifecycleStageResult.FAILED,
                is_success=success,
                reason_code=rcode,
            )
            cycle_authority.write_outcomes[h.hypothesis_id] = outcome
            if z_id:
                cycle_authority.zone_outcomes[z_id] = outcome

            # SECTION 046: Fatal persistence failure or poisoned session: stop staging immediately
            if not getattr(db, "is_active", True) or res == LifecycleStageResult.FAILED:
                cycle_authority.has_write_failures = True
                cycle_authority.write_status = "FAILED"
                break

        if cycle_authority.has_write_failures or not getattr(db, "is_active", True):
            return self._finalize_failed_cycle(
                db, symbol,
                t_recv=t_recv,
                t_start=t_start,
                batch_result=batch_result,
                cycle_authority=cycle_authority,
                bounded_zones_count=len(bounded_zones),
                hypotheses_count=len(hypotheses),
                regime=regime,
                liq_levels_count=len(liq_levels),
                cycle_id=cycle_id,
                t_perf_start=t_perf_start,
                effective_run_id=effective_run_id,
            )

        # Ensure any durable hypothesis past its expiry deadline transitions to EXPIRED (SECTIONS 116 - 120)
        for d_id, d_rec in durable_hyps_map.items():
            if d_id not in staged_ids and t_recv >= d_rec.expiry_ms and d_rec.status == "ACTIVE":
                try:
                    exp_res = stage_hypothesis_status_transition(db, symbol, d_rec, desired_status="EXPIRED", now_ms=t_recv)
                except Exception as e:
                    logger.warning(f"Exception during stage_hypothesis_status_transition for {d_id}: {e}")
                    exp_res = LifecycleStageResult.FAILED

                exp_success = is_lifecycle_stage_success(exp_res)
                exp_key = get_hypothesis_storage_key(symbol, d_id)
                exp_rcode = None
                if not exp_success:
                    if exp_res == LifecycleStageResult.BLOCKED:
                        exp_rcode = OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.value
                        cycle_authority.has_write_blocks = True
                    else:
                        exp_rcode = OpportunityReasonCode.HYPOTHESIS_WRITE_FAILED.value
                        cycle_authority.has_write_failures = True
                    cycle_authority.write_issues_count += 1

                exp_outcome = HypothesisWriteOutcome(
                    hypothesis_id=d_id,
                    storage_key=exp_key,
                    zone_id=d_rec.source_zone_id,
                    operation="EXPIRE_DURABLE",
                    result=exp_res if isinstance(exp_res, LifecycleStageResult) else LifecycleStageResult.FAILED,
                    is_success=exp_success,
                    reason_code=exp_rcode,
                )
                cycle_authority.write_outcomes[f"{d_id}:EXPIRE"] = exp_outcome
                if not getattr(db, "is_active", True) or exp_res == LifecycleStageResult.FAILED:
                    cycle_authority.has_write_failures = True
                    cycle_authority.write_status = "FAILED"
                    break

        if cycle_authority.has_write_failures or not getattr(db, "is_active", True):
            return self._finalize_failed_cycle(
                db, symbol,
                t_recv=t_recv,
                t_start=t_start,
                batch_result=batch_result,
                cycle_authority=cycle_authority,
                bounded_zones_count=len(bounded_zones),
                hypotheses_count=len(hypotheses),
                regime=regime,
                liq_levels_count=len(liq_levels),
                cycle_id=cycle_id,
                t_perf_start=t_perf_start,
                effective_run_id=effective_run_id,
            )

        # Compute cycle write_status
        if cycle_authority.has_write_failures:
            cycle_authority.write_status = "FAILED"
        elif cycle_authority.has_write_blocks:
            cycle_authority.write_status = "BLOCKED"
        else:
            cycle_authority.write_status = "OK"

        cycle_authority.entry_authorized_hypotheses = entry_authorized_hypotheses

        display_hypotheses_by_zone = {h.watched_zone.zone_id: h for h in hypotheses if getattr(h, "watched_zone", None)}
        entry_hypotheses_by_zone = {h.watched_zone.zone_id: h for h in entry_authorized_hypotheses if getattr(h, "watched_zone", None)}
        assessments_map: Dict[str, Any] = {}
        for z in bounded_zones:
            r_score = rank_scores_by_id.get(z.zone_id)
            if r_score:
                h = display_hypotheses_by_zone.get(z.zone_id)
                dto = build_zone_assessment_explanation(
                    z, r_score, h, current_price=current_px, atr=atr_15m, as_of_ms=t_recv, is_stale=False
                )
                assessments_map[z.zone_id] = dto

        with self._lock:
            self._active_zone_assessments[symbol] = assessments_map
            self._active_market_context[symbol] = {
                "current_price": current_px,
                "atr": atr_15m,
                "as_of_ms": t_recv,
                "regime": regime,
            }
            self._active_liquidity_levels[symbol] = liq_levels
            self._active_dealing_range[symbol] = dealing_range
            self._active_regime[symbol] = regime

        # 4. Model evaluations
        candidates: List[ScalpCandidate] = []
        g_settings = risk_settings_service.get_settings(db)
        audit = crud.get_or_create_today_audit(db, clock=clock)

        # V16.7 Group D: Canonical Risk Authority
        # g_settings.risk_pct is the default authority for non-replay callers.
        # Fallback is only used if g_settings.risk_pct is missing.
        # Explicit execution_financial_context overrides for replay.
        from services.plan_financial_context_service import resolve_execution_risk
        explicit_replay_risk = getattr(execution_financial_context, "risk_pct", None) if execution_financial_context else None
        account_risk = float(g_settings.risk_pct) if (g_settings and g_settings.risk_pct is not None) else float(getattr(cfg, "fallback_risk_pct", 0.10))
        risk_resolution = resolve_execution_risk(
            account_risk_pct=account_risk,
            explicit_replay_risk_pct=explicit_replay_risk,
        )
        resolved_risk_pct = float(risk_resolution["effective_risk_pct"])

        # V16.8 Capital Resolution (NHÓM 03, 04, PHẦN 027, 028, 034)
        override_cap = getattr(execution_financial_context, "capital_usdt", None) if execution_financial_context else None
        cap_mode = getattr(execution_financial_context, "capital_mode", "CURRENT_ACCOUNT_BALANCE") if execution_financial_context else "CURRENT_ACCOUNT_BALANCE"

        if override_cap is not None:
            capital_val = float(override_cap)
            capital_source = getattr(execution_financial_context, "source", "REPLAY_EXPLICIT_CAPITAL")
        else:
            raw_eq = audit.current_equity if audit is not None else None
            capital_val = float(raw_eq) if raw_eq is not None else 1000.0
            capital_source = "ACCOUNT_AUDIT"

        # Validate capital (PHẦN 027): reject NaN, inf, or non-positive; entry fails closed without fallback
        if isinstance(capital_val, bool) or not math.isfinite(capital_val):
            logger.warning(f"Capital value invalid: {capital_val}. Disabling cycle entries.")
            return {"status": "BLOCKED", "reason": "CAPITAL_VALUE_INVALID", "candidates": [], "evaluations": []}

        if capital_val <= 0:
            logger.warning(f"Capital non-positive: {capital_val}. Entry blocked by account capacity guard.")
            return {"status": "BLOCKED", "reason": "ACCOUNT_EQUITY_NON_POSITIVE", "candidates": [], "evaluations": []}

        policy_dict = {
            "capital_usdt": capital_val,
            "risk_pct": resolved_risk_pct,
            "leverage": int(g_settings.requested_leverage),
            "margin_mode": str(g_settings.margin_mode),
            "max_daily_trades": getattr(policy, "max_daily_fills", 3) if policy else 3,
            "risk_source": risk_resolution["source"],
            "capital_source": capital_source,
            "capital_mode": cap_mode,
            "config_version": getattr(g_settings, "config_version", 1),
        }

        # V17.5.7: Load effective costs (slippage, maker/taker fees) into policy_dict for full cycle cost parity
        cost_overrides: Dict[str, Any] = {}
        if execution_financial_context is not None:
            if hasattr(execution_financial_context, "slippage_usd") and execution_financial_context.slippage_usd is not None:
                cost_overrides["slippage_usd"] = float(execution_financial_context.slippage_usd)
            if hasattr(execution_financial_context, "maker_fee_rate") and execution_financial_context.maker_fee_rate is not None:
                cost_overrides["maker_fee_rate"] = float(execution_financial_context.maker_fee_rate)
            if hasattr(execution_financial_context, "taker_fee_rate") and execution_financial_context.taker_fee_rate is not None:
                cost_overrides["taker_fee_rate"] = float(execution_financial_context.taker_fee_rate)
            if hasattr(execution_financial_context, "costs") and execution_financial_context.costs is not None:
                cost_overrides["costs"] = execution_financial_context.costs

        cost_config_errors = []
        if "slippage_usd" not in cost_overrides:
            res_slip = resolve_ordered_cost_setting(db, ["slippage_usd", "slippage", "default_slippage"], setting_name="slippage")
            if res_slip["status"] == "VALID":
                cost_overrides["slippage_usd"] = res_slip["value"]
            elif res_slip["status"] == "INVALID":
                cost_config_errors.append(f"Invalid slippage setting: {res_slip['reason']} on key {res_slip['key']}")

        if "maker_fee_rate" not in cost_overrides:
            res_maker = resolve_ordered_cost_setting(db, ["maker_fee_rate", "default_maker_fee_rate"], setting_name="maker_fee")
            if res_maker["status"] == "VALID":
                cost_overrides["maker_fee_rate"] = res_maker["value"]
            elif res_maker["status"] == "INVALID":
                cost_config_errors.append(f"Invalid maker fee setting: {res_maker['reason']} on key {res_maker['key']}")

        if "taker_fee_rate" not in cost_overrides:
            res_taker = resolve_ordered_cost_setting(db, ["taker_fee_rate", "default_taker_fee_rate"], setting_name="taker_fee")
            if res_taker["status"] == "VALID":
                cost_overrides["taker_fee_rate"] = res_taker["value"]
            elif res_taker["status"] == "INVALID":
                cost_config_errors.append(f"Invalid taker fee setting: {res_taker['reason']} on key {res_taker['key']}")

        if cost_config_errors:
            policy_dict["cost_config_error"] = "; ".join(cost_config_errors)
            policy_dict["costs_valid"] = False
        else:
            policy_dict["costs_valid"] = True

        policy_dict.update(cost_overrides)
        from domain_calculator import CostAssumptions
        slip_val = float(policy_dict.get("slippage_usd", 0.10)) if policy_dict.get("slippage_usd") is not None else 0.10
        maker_val = float(policy_dict.get("maker_fee_rate", 0.0002)) if policy_dict.get("maker_fee_rate") is not None else 0.0002
        taker_val = float(policy_dict.get("taker_fee_rate", 0.0004)) if policy_dict.get("taker_fee_rate") is not None else 0.0004
        policy_dict["costs"] = CostAssumptions(slippage_usd=slip_val, maker_fee_rate=maker_val, taker_fee_rate=taker_val)

        # 4. Independent Peer Model Evaluations (V16.39 Sections 016, 017, 030; V17.9.0.1 Phase D Canonical Evaluator Integration)
        exec_profile = getattr(cfg, "execution_profile", "BASELINE")
        adaptive_regime_models = None
        if exec_profile == "PAPER_ADAPTIVE_SMC":
            from services.support_resistance_service import resolve_models_for_regime
            adaptive_regime_models = set(resolve_models_for_regime(regime))

        from services.shared_strategy_evaluator import EVALUATOR_VERSION, SharedStrategyEvaluator
        shared_cycle_context = self.build_shared_decision_context(
            symbol=symbol,
            decision_timestamp_ms=t_recv,
            m1_candles=c_1m,
            m5_candles=c_5m,
            m15_candles=c_15m,
            h1_candles=c_1h,
            h4_candles=c_4h,
            d1_candles=c_1d,
            bid=bid,
            ask=ask,
            quote_timestamp_ms=getattr(q, "exchange_ts_ms", None) or getattr(q, "received_at_ms", None) or t_recv,
            htf_zones=bounded_zones,
            liquidity_pools=liq_levels,
            htf_bias=bias,
            h1_alignment=align,
            risk_config={
                "capital_usdt": policy_dict["capital_usdt"],
                "risk_pct": policy_dict["risk_pct"],
                "leverage": policy_dict["leverage"],
                "margin_mode": policy_dict["margin_mode"],
                "min_net_rr": float(getattr(cfg, "min_net_rr", 1.2)),
            },
            cost_config={
                "slippage_usd": slip_val,
                "maker_fee_rate": maker_val,
                "taker_fee_rate": taker_val,
            },
        )
        shared_decision_snapshots: List[Any] = []

        model_diagnostics: List[Dict[str, Any]] = []

        # A. SMC M1 Reversal Model on watched zones
        run_rev, act_rev, reason_rev = resolve_model_evaluation_plan(
            ModelIdentifier.SMC_M1_REVERSAL.value,
            config=cfg,
            regime=regime,
            has_m1=bool(c_1m),
            cycle_authority=cycle_authority,
            adaptive_regime_models=adaptive_regime_models,
        )
        rev_diag = {
            "model_id": ModelIdentifier.SMC_M1_REVERSAL.value,
            "evaluated": run_rev,
            "eligible": False,
            "execution_action": act_rev,
            "reason": reason_rev,
        }

        if run_rev:
            rev_evaluated_zone = False
            for z in bounded_zones:
                if z.state in (ZoneState.NEAR.value, ZoneState.TOUCHED.value, ZoneState.WAITING_TRIGGER.value):
                    # Check if zone's hypothesis read authority failed (SECTIONS 095 - 098)
                    if z.zone_id in blocked_zone_issues or batch_result.read_status == "READ_FAILED":
                        issue = blocked_zone_issues.get(z.zone_id)
                        if batch_result.read_status == "READ_FAILED":
                            rcode = OpportunityReasonCode.HYPOTHESIS_STORE_UNAVAILABLE.value
                        elif issue and issue.status == HypothesisLoadStatus.UNSUPPORTED_VERSION:
                            rcode = OpportunityReasonCode.HYPOTHESIS_SCHEMA_UNSUPPORTED.value
                        else:
                            rcode = OpportunityReasonCode.HYPOTHESIS_STATE_CORRUPT.value

                        OpportunityFunnelService.record_opportunity_transition(
                            db,
                            OpportunityEvaluation(
                                opportunity_id=f"opp_{z.zone_id}",
                                symbol=symbol,
                                model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
                                direction=z.direction,
                                zone_id=z.zone_id,
                                as_of_ms=t_recv,
                                stage=OpportunityStage.TRIGGER_PENDING.value,
                                reason_codes=(rcode,),
                                market_event_received_at=t_recv,
                                details={"message": "Dữ liệu theo dõi kịch bản bị lỗi, bot đang tạm chặn lệnh liên quan."},
                            ),
                            now_ms=t_recv,
                        )
                        continue

                    # Check write authority (V15.7 SECTIONS 061 - 090)
                    if cycle_authority.has_write_failures:
                        OpportunityFunnelService.record_opportunity_transition(
                            db,
                            OpportunityEvaluation(
                                opportunity_id=f"opp_{z.zone_id}",
                                symbol=symbol,
                                model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
                                direction=z.direction,
                                zone_id=z.zone_id,
                                as_of_ms=t_recv,
                                stage=OpportunityStage.TRIGGER_PENDING.value,
                                reason_codes=(OpportunityReasonCode.HYPOTHESIS_WRITE_FAILED.value,),
                                market_event_received_at=t_recv,
                                details={"message": "Chưa thể chuẩn bị lệnh vì bot chưa lưu được trạng thái kịch bản. Bot đang giữ nguyên các lệnh hiện có và sẽ đánh giá lại ở chu kỳ tiếp theo."},
                            ),
                            now_ms=t_recv,
                        )
                        continue

                    z_outcome = cycle_authority.zone_outcomes.get(z.zone_id)
                    if z_outcome is not None and not z_outcome.is_success:
                        rcode = z_outcome.reason_code or OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.value
                        msg = "Kịch bản này đang bị chặn vì trạng thái lưu không cho phép cập nhật." if rcode == OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.value else "Chưa thể chuẩn bị lệnh vì bot chưa lưu được trạng thái kịch bản. Bot đang giữ nguyên các lệnh hiện có và sẽ đánh giá lại ở chu kỳ tiếp theo."
                        OpportunityFunnelService.record_opportunity_transition(
                            db,
                            OpportunityEvaluation(
                                opportunity_id=f"opp_{z.zone_id}",
                                symbol=symbol,
                                model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
                                direction=z.direction,
                                zone_id=z.zone_id,
                                as_of_ms=t_recv,
                                stage=OpportunityStage.TRIGGER_PENDING.value,
                                reason_codes=(rcode,),
                                market_event_received_at=t_recv,
                                details={"message": msg},
                            ),
                            now_ms=t_recv,
                        )
                        continue

                    zone_hyp = entry_hypotheses_by_zone.get(z.zone_id)
                    if zone_hyp is None:
                        OpportunityFunnelService.record_opportunity_transition(
                            db,
                            OpportunityEvaluation(
                                opportunity_id=f"opp_{z.zone_id}",
                                symbol=symbol,
                                model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
                                direction=z.direction,
                                zone_id=z.zone_id,
                                as_of_ms=t_recv,
                                stage=OpportunityStage.TRIGGER_PENDING.value,
                                reason_codes=(OpportunityReasonCode.HYPOTHESIS_AUTHORITY_MISSING.value,),
                                market_event_received_at=t_recv,
                                details={"message": "Thiếu dữ liệu xác thực kịch bản, tạm chặn vào lệnh."},
                            ),
                            now_ms=t_recv,
                        )
                        continue

                    if zone_hyp.status == "EXPIRED" or t_recv >= getattr(zone_hyp, "expiry_ms", float("inf")):
                        OpportunityFunnelService.record_opportunity_transition(
                            db,
                            OpportunityEvaluation(
                                opportunity_id=f"opp_{z.zone_id}",
                                symbol=symbol,
                                model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
                                direction=z.direction,
                                zone_id=z.zone_id,
                                as_of_ms=t_recv,
                                stage=OpportunityStage.TRIGGER_PENDING.value,
                                reason_codes=(OpportunityReasonCode.HYPOTHESIS_EXPIRED.value,),
                                market_event_received_at=t_recv,
                            ),
                            now_ms=t_recv,
                        )
                        continue

                    rev_evaluated_zone = True
                    cand, rev_snap = SharedStrategyEvaluator.evaluate_production_reversal_candidate(
                        context=shared_cycle_context,
                        zone=z,
                        candles_1m=c_1m,
                        quote=q,
                        policy_settings=policy_dict,
                        now_ms=t_recv,
                        config=cfg,
                        context_levels=liq_levels,
                        stop_policy=stop_policy,
                        stop_context=stop_context,
                    )
                    shared_decision_snapshots.append(rev_snap)
                    rev_diag["shared_decision_fingerprint"] = rev_snap.decision_fingerprint
                    if cand.is_eligible:
                        cand.hypothesis_id = zone_hyp.hypothesis_id
                        cand.regime_snapshot = regime_snapshot
                        cand.execution_profile = exec_profile
                        if sr_zones:
                            cand.sr_evidence = [srz.to_dict() for srz in sr_zones[:5]]
                        candidates.append(cand)
                        rev_diag["eligible"] = True
                    else:
                        OpportunityFunnelService.record_opportunity_transition(
                            db,
                            OpportunityEvaluation(
                                opportunity_id=f"opp_{z.zone_id}",
                                symbol=symbol,
                                model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
                                direction=z.direction,
                                zone_id=z.zone_id,
                                as_of_ms=t_recv,
                                stage=OpportunityStage.TRIGGER_PENDING.value,
                                reason_codes=tuple([cand.reason_code] if cand.reason_code else ()),
                                market_event_received_at=t_recv,
                            ),
                            now_ms=t_recv,
                        )
            if not rev_evaluated_zone:
                rev_snap_default = SharedStrategyEvaluator.evaluate_decision(
                    market_snapshot=shared_cycle_context,
                    strategy_config={
                        "model_id": ModelIdentifier.SMC_M1_REVERSAL.value,
                        "preferred_direction": "LONG" if bias == "BULLISH" else "SHORT",
                        "adapter_origin": "PRODUCTION_ORCHESTRATOR",
                    },
                    evaluation_time=t_recv,
                )
                shared_decision_snapshots.append(rev_snap_default)
                rev_diag["shared_decision_fingerprint"] = rev_snap_default.decision_fingerprint
        model_diagnostics.append(rev_diag)

        # B. SMC M1 Continuation Model (SECTIONS 080 - 082) - PEER independent branch
        run_cont, act_cont, reason_cont = resolve_model_evaluation_plan(
            ModelIdentifier.SMC_M1_CONTINUATION.value,
            config=cfg,
            regime=regime,
            has_m1=bool(c_1m),
            cycle_authority=cycle_authority,
            adaptive_regime_models=adaptive_regime_models,
        )
        cont_diag = {
            "model_id": ModelIdentifier.SMC_M1_CONTINUATION.value,
            "evaluated": run_cont,
            "eligible": False,
            "execution_action": act_cont,
            "reason": reason_cont,
        }
        if run_cont:
            cand_cont, cont_snap = SharedStrategyEvaluator.evaluate_production_continuation_candidate(
                context=shared_cycle_context,
                candles_1m=c_1m,
                candles_5m=c_5m,
                quote=q,
                htf_bias=bias,
                h1_alignment=align,
                policy_settings=policy_dict,
                now_ms=t_recv,
                config=cfg,
                stop_policy=stop_policy,
                stop_context=stop_context,
            )
            shared_decision_snapshots.append(cont_snap)
            cont_diag["shared_decision_fingerprint"] = cont_snap.decision_fingerprint
            if cand_cont.is_eligible:
                cand_cont.regime_snapshot = regime_snapshot
                cand_cont.execution_profile = exec_profile
                if sr_zones:
                    cand_cont.sr_evidence = [srz.to_dict() for srz in sr_zones[:5]]
                candidates.append(cand_cont)
                cont_diag["eligible"] = True
        model_diagnostics.append(cont_diag)

        # B2. SMC M1 FVG Continuation Model (Rules 046 - 060) - PEER independent branch
        run_fvg, act_fvg, reason_fvg = resolve_model_evaluation_plan(
            ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
            config=cfg,
            regime=regime,
            has_m1=bool(c_1m),
            cycle_authority=cycle_authority,
            adaptive_regime_models=adaptive_regime_models,
        )
        fvg_diag = {
            "model_id": ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
            "evaluated": run_fvg,
            "eligible": False,
            "execution_action": act_fvg,
            "reason": reason_fvg,
        }
        if run_fvg:
            cand_fvg_cont, fvg_snap = SharedStrategyEvaluator.evaluate_production_fvg_continuation_candidate(
                context=shared_cycle_context,
                closed_1m=c_1m,
                closed_5m=c_5m,
                quote=q,
                htf_bias=bias,
                h1_alignment=align,
                policy_settings=policy_dict,
                now_ms=t_recv,
                config=cfg,
                context_levels=liq_levels,
                stop_policy=stop_policy,
                stop_context=stop_context,
            )
            shared_decision_snapshots.append(fvg_snap)
            fvg_diag["shared_decision_fingerprint"] = fvg_snap.decision_fingerprint
            if cand_fvg_cont.is_eligible:
                cand_fvg_cont.regime_snapshot = regime_snapshot
                cand_fvg_cont.execution_profile = exec_profile
                if sr_zones:
                    cand_fvg_cont.sr_evidence = [srz.to_dict() for srz in sr_zones[:5]]
                candidates.append(cand_fvg_cont)
                fvg_diag["eligible"] = True
        model_diagnostics.append(fvg_diag)

        # B3. SMC M1 Sweep Reclaim Model (Rules 061 - 074, 080) - PEER independent branch
        run_sr, act_sr, reason_sr = resolve_model_evaluation_plan(
            ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
            config=cfg,
            regime=regime,
            has_m1=bool(c_1m),
            cycle_authority=cycle_authority,
            adaptive_regime_models=adaptive_regime_models,
        )
        sr_diag = {
            "model_id": ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
            "evaluated": run_sr,
            "eligible": False,
            "execution_action": act_sr,
            "reason": reason_sr,
        }
        if run_sr:
            from services.smc_liquidity_service import build_liquidity_clusters, update_cluster_state
            liq_clusters = build_liquidity_clusters(liq_levels, timeframe="15M", symbol=symbol, now_ms=t_recv)
            sr_evaluated_cluster = False
            for cluster in liq_clusters:
                update_cluster_state(cluster, c_1m, now_ms=t_recv)
                cycle_authority.cluster_outcomes[cluster.id] = cluster
                if getattr(cluster, "state", "") in ("SWEPT", "RECLAIMED"):
                    sr_evaluated_cluster = True
                    cand_sr, sr_snap = SharedStrategyEvaluator.evaluate_production_sweep_reclaim_candidate(
                        context=shared_cycle_context,
                        closed_1m=c_1m,
                        cluster=cluster,
                        quote=q,
                        policy_settings=policy_dict,
                        now_ms=t_recv,
                        config=cfg,
                        context_levels=liq_levels,
                        stop_policy=stop_policy,
                        stop_context=stop_context,
                    )
                    shared_decision_snapshots.append(sr_snap)
                    sr_diag["shared_decision_fingerprint"] = sr_snap.decision_fingerprint
                    if cand_sr.is_eligible:
                        cand_sr.regime_snapshot = regime_snapshot
                        cand_sr.execution_profile = exec_profile
                        if sr_zones:
                            cand_sr.sr_evidence = [srz.to_dict() for srz in sr_zones[:5]]
                        candidates.append(cand_sr)
                        sr_diag["eligible"] = True
            if not sr_evaluated_cluster:
                sr_snap_default = SharedStrategyEvaluator.evaluate_decision(
                    market_snapshot=shared_cycle_context,
                    strategy_config={
                        "model_id": ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
                        "preferred_direction": "LONG" if bias == "BULLISH" else "SHORT",
                        "adapter_origin": "PRODUCTION_ORCHESTRATOR",
                    },
                    evaluation_time=t_recv,
                )
                shared_decision_snapshots.append(sr_snap_default)
                sr_diag["shared_decision_fingerprint"] = sr_snap_default.decision_fingerprint
        model_diagnostics.append(sr_diag)

        # C. Daily NY Research Model (SECTIONS 080 - 083)
        if cfg.daily_ny_research_enabled and not cycle_authority.has_write_failures:
            today_fills = audit.fills_count if audit else 0
            ny_session_ctx = resolve_entry_session_context(policy, "NY_DAILY_RESEARCH", now_ms=t_recv, clock=clock)
            is_ny = bool(ny_session_ctx.in_ny_entry_window)
            cand_res = DailyNYResearchService.evaluate_daily_research_candidate(
                c_5m, q, bias, align, policy_dict, today_fills, now_ms=t_recv, is_ny_session=is_ny, config=cfg
            )
            if cand_res.get("is_eligible"):
                calc = cand_res.get("calculation")
                candidates.append(ScalpCandidate(
                    is_eligible=True,
                    model_id=ModelIdentifier.NY_DAILY_RESEARCH.value,
                    direction=cand_res["direction"],
                    candidate_key=cand_res["candidate_key"],
                    symbol=symbol,
                    planned_entry=cand_res["planned_entry"],
                    stop_loss=cand_res["stop_loss"],
                    take_profit=cand_res["take_profit"],
                    gross_rr=cand_res["gross_rr"],
                    net_rr=cand_res["net_rr"],
                    calculation=calc,
                    evidence=cand_res["evidence"],
                    trigger_chain=cand_res["trigger_chain"],
                    snapshot=cand_res["snapshot"],
                    as_of_ms=t_recv,
                ))

        # D. CG-01 Paper Profile Model (V16.2 Rules 001 - 035, 121 - 125, V16.9 NHÓM 09 - 11)
        cg01_mode = getattr(cfg, "cg01_profile_mode", None)
        if cg01_mode is None:
            cg01_mode = "PAPER_EXECUTION" if getattr(cfg, "cg01_enabled", False) else "OFF"

        cg01_eval_data = None
        cg01_dec = None
        cg01_snap = None
        if cg01_cfg.enabled and c_1m:
            from services.cg01_daily_bias_service import (
                build_cg01_authority_publish_context,
                publish_current_cg01_bias_authority,
            )
            # Rules 019, 027, 028: Capture runtime ownership publish lease BEFORE compute
            pub_ctx = build_cg01_authority_publish_context(
                symbol=symbol,
                config=cg01_cfg,
                research_context=research_context,
                db=db,
                caller_identity=caller_identity,
                evaluation_origin=evaluation_origin,
                execution_scope=execution_scope,
                auto_enabled=auto_enabled,
                now_ms=t_recv,
            )
            from services.cg01_engine import evaluate_cg01_candidate
            closed_ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m, "1D": c_1d, "context_source": context_source}
            costs_to_pass = getattr(cg01_cfg, "costs", None) or getattr(cfg, "costs", None)
            cg01_dec, cg01_snap = SharedStrategyEvaluator.evaluate_production_cg01_candidate(
                context=shared_cycle_context,
                closed_ctx=closed_ctx,
                quote=q,
                config=cg01_cfg,
                now_ms=t_recv,
                capital=policy_dict["capital_usdt"],
                risk_pct=policy_dict["risk_pct"],
                costs=costs_to_pass,
                stop_policy=stop_policy,
                stop_context=stop_context,
                eval_fn=evaluate_cg01_candidate,
            )
            shared_decision_snapshots.append(cg01_snap)
            if cg01_dec and getattr(cg01_dec, "daily_bias", None):
                publish_current_cg01_bias_authority(
                    symbol=symbol,
                    bias=cg01_dec.daily_bias,
                    config=cg01_cfg,
                    evaluated_at_ms=t_recv,
                    source=pub_ctx["source"],
                    is_active_owner=pub_ctx["is_active_owner"],
                    owner=pub_ctx["owner"],
                    generation=pub_ctx.get("generation", 1),
                )
            cg01_eval_data = serialize_cg01_evaluation(cg01_dec, enabled=True, evaluated=True, as_of_ms=t_recv)
            if cg01_dec.candidate:
                cg01_dec.candidate.regime_snapshot = regime_snapshot
                cg01_dec.candidate.execution_profile = exec_profile
                if sr_zones:
                    cg01_dec.candidate.sr_evidence = [srz.to_dict() for srz in sr_zones[:5]]
                candidates.append(cg01_dec.candidate)
        else:
            cg01_eval_data = serialize_cg01_evaluation(
                None,
                enabled=cg01_cfg.enabled,
                evaluated=False,
                skip_reason="MODEL_DISABLED" if not cg01_cfg.enabled else "WARMUP_INSUFFICIENT",
                as_of_ms=t_recv,
            )

        cg01_diag = {
            "model_id": ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
            "evaluated": bool(cg01_cfg.enabled and c_1m),
            "eligible": bool(cg01_dec and cg01_dec.candidate and cg01_dec.candidate.is_eligible),
            "execution_action": "EVALUATE_AND_EXECUTE" if cg01_cfg.enabled else "DISABLED",
            "reason": "ELIGIBLE_FOR_EVALUATION" if (cg01_cfg.enabled and c_1m) else ("CG01_DISABLED" if not cg01_cfg.enabled else "WARMUP_INSUFFICIENT"),
        }
        if cg01_snap is not None:
            cg01_diag["shared_decision_fingerprint"] = cg01_snap.decision_fingerprint
        model_diagnostics.append(cg01_diag)

        # Candidate Router & Deduplication (Rules 093 - 096, 131 - 134)
        from services.scalp_m1_model_service import evaluate_smc_opportunity_candidates
        effective_router_bias = bias or "UNKNOWN"
        cg01_policy = getattr(cg01_cfg, "direction_policy", "LEGACY_H1")
        if cg01_policy == "DAILY_BIAS_PRIMARY" and cg01_dec and getattr(cg01_dec, "daily_bias", None):
            daily_bias_dir = getattr(cg01_dec.daily_bias, "direction", "NEUTRAL")
            if daily_bias_dir in ("BULLISH", "BEARISH"):
                effective_router_bias = daily_bias_dir

        routed_candidates = evaluate_smc_opportunity_candidates(
            candidates,
            htf_bias=effective_router_bias,
            cg01_policy=cg01_policy,
        )

        if cg01_eval_data and cg01_dec and cg01_dec.candidate:
            if cg01_dec.candidate not in routed_candidates:
                cg01_eval_data["router_outcome"] = "OTHER_MODEL_SELECTED"
            else:
                cg01_eval_data["router_outcome"] = "SELECTED"

        candidates = routed_candidates

        # 5. Execution or Shadow Handling
        t_eval_done = int(time.time() * 1000)
        armed_order = None

        effective_scalp_mode = scalp_mode
        # Global auto stop policy check: if stopped, downgrade PAPER to SHADOW (PHẦN 024)
        if not global_auto and effective_scalp_mode == ScalpMode.PAPER.value:
            effective_scalp_mode = ScalpMode.SHADOW.value

        for cand in candidates:
            # V17.5.4 Appendix A3: bind execution context to comparison plan before persistence
            if getattr(cand, "stop_comparison", None) is not None:
                try:
                    from services.stop_placement_research_service import (
                        persist_stop_comparison,
                        resolve_stop_comparison_execution_context,
                        bind_stop_comparison_execution_context,
                    )
                    ctx = resolve_stop_comparison_execution_context(
                        cand=cand,
                        policy_settings=policy_dict,
                        decision_as_of_ms=t_recv,
                        session_context=session_ctx,
                        cfg=cfg,
                    )
                    cand.stop_comparison = bind_stop_comparison_execution_context(cand.stop_comparison, ctx)
                    persist_stop_comparison(db, cand.stop_comparison, now_ms=t_recv)
                except Exception as e:
                    logger.warning(f"Error persisting stop comparison for {cand.candidate_key}: {e}")

            opp_eval = OpportunityEvaluation(
                opportunity_id=f"opp_{cand.candidate_key}",
                symbol=symbol,
                model_id=cand.model_id,
                direction=cand.direction,
                zone_id=cand.zone_id,
                as_of_ms=t_recv,
                stage=OpportunityStage.TRIGGER_CONFIRMED.value,
                market_event_received_at=t_recv,
                evaluation_started_at=t_start,
                evaluation_completed_at=t_eval_done,
                trigger_known_at=t_recv,
                planned_entry=cand.planned_entry,
                stop_loss=cand.stop_loss,
                take_profit=cand.take_profit,
                net_rr=cand.net_rr,
            )
            OpportunityFunnelService.record_opportunity_transition(db, opp_eval, now_ms=t_recv)

            from services.scalp_m1_model_service import resolve_model_execution_policy, ModelExecutionAction
            exec_profile = getattr(cfg, "execution_profile", "BASELINE")
            is_cg01_model = (cand.model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value)

            # Rule 019 & 033, 034, 035, 102, 104: Policy router-driven execution arbitration
            pol_action, pol_reason = resolve_model_execution_policy(cfg, cand.model_id, execution_scope="PAPER")
            if pol_action == ModelExecutionAction.DISABLED:
                logger.info(f"Candidate {cand.candidate_key} ({cand.model_id}) disabled by execution policy: {pol_reason}")
                OpportunityFunnelService.record_opportunity_transition(
                    db,
                    OpportunityEvaluation(
                        opportunity_id=f"opp_{cand.candidate_key}",
                        symbol=symbol,
                        model_id=cand.model_id,
                        direction=cand.direction,
                        zone_id=cand.zone_id,
                        as_of_ms=t_recv,
                        stage=OpportunityStage.GUARD_BLOCKED.value,
                        reason_codes=(pol_reason,),
                        planned_entry=cand.planned_entry,
                        stop_loss=cand.stop_loss,
                        take_profit=cand.take_profit,
                        net_rr=cand.net_rr,
                    ),
                    now_ms=t_recv,
                )
                continue

            should_shadow = (effective_scalp_mode == ScalpMode.SHADOW.value) or (pol_action == ModelExecutionAction.EVALUATE_SHADOW)

            if should_shadow:
                # In SHADOW mode: record hypothetical plan, do NOT arm or fill order (PHẦN 024, 079)
                logger.info(f"[SHADOW] Qualified {cand.model_id} candidate: {cand.direction} @ {cand.planned_entry}")
                opp_eval = OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=t_recv,
                    stage=OpportunityStage.PLAN_VALID.value,
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                )
                OpportunityFunnelService.record_opportunity_transition(db, opp_eval, now_ms=t_recv)

            elif effective_scalp_mode == ScalpMode.PAPER.value or (cand.model_id == ModelIdentifier.NY_DAILY_RESEARCH.value and cfg.daily_ny_research_enabled and global_auto):
                # In PAPER mode: validate through canonical entry guards (PHẦN 025 - 033, 059, V16.39.3 Sections 081 - 084)
                cand_family = getattr(cand, "model_id", "STANDARD_SMC")
                cand_session_ctx = resolve_entry_session_context(policy, cand_family, now_ms=t_recv, clock=clock)
                armed_order = self._arm_paper_candidate(
                    db, cand, now_ms=t_recv, clock=clock, quote=q, policy_settings=policy_dict,
                    config=cfg, feed_health=feed_health, auto_enabled=auto_enabled,
                    authority_context=cycle_authority, session_context=cand_session_ctx, research_context=research_context,
                )
                if is_cg01_model and cg01_eval_data:
                    if armed_order:
                        cg01_eval_data["armed_order_id"] = armed_order.id
                        cg01_eval_data["arm_outcome"] = "ARMED"
                        cg01_eval_data["stage"] = OpportunityStage.ARMED.value if hasattr(OpportunityStage, "ARMED") else "ARMED"
                    else:
                        rej = self._arm_rejections.get(cand.candidate_key)
                        if rej:
                            cg01_eval_data["stage"] = OpportunityStage.GUARD_BLOCKED.value
                            cg01_eval_data["guard_summary"] = {"blocked_reason": rej[1], "blocked_at": rej[0]}
                            if rej[1] not in cg01_eval_data["reason_codes"]:
                                cg01_eval_data["reason_codes"].append(rej[1])
                elif not is_cg01_model and armed_order and cg01_eval_data:
                    cg01_eval_data["router_outcome"] = "OTHER_MODEL_SELECTED"
                if armed_order:
                    break

        commit_ok = False
        rollback_ok = False
        try:
            db.commit()
            commit_ok = True
        except Exception as e:
            logger.warning(f"Error committing scalp watcher cycle: {e}")
            try:
                db.rollback()
                rollback_ok = True
            except Exception as rb_e:
                logger.error(f"Error rolling back after commit failure: {rb_e}")
            armed_order = None
            if cg01_eval_data:
                cg01_eval_data["armed_order_id"] = None
                cg01_eval_data["arm_outcome"] = "TRANSACTION_ROLLBACK"
                cg01_eval_data["stage"] = "DEGRADED"
                if "TRANSACTION_COMMIT_FAILED" not in cg01_eval_data.setdefault("reason_codes", []):
                    cg01_eval_data["reason_codes"].append("TRANSACTION_COMMIT_FAILED")

        t_cycle_done = int(time.time() * 1000)

        cycle_status = derive_cycle_status(
            read_status=batch_result.read_status,
            write_status=cycle_authority.write_status,
            commit_ok=commit_ok,
            has_failed_write=cycle_authority.has_write_failures,
            has_blocked_write=cycle_authority.has_write_blocks,
        )

        if commit_ok:
            with self._lock:
                self._active_hypotheses[symbol] = entry_authorized_hypotheses
                self._latest_hypothesis_batch_result[symbol] = batch_result
                self._latest_hypothesis_authority[symbol] = cycle_authority
                self._latest_cycle_outcome[symbol] = {
                    "status": cycle_status,
                    "transaction_status": "COMMITTED",
                    "rollback_status": "NONE",
                    "as_of_ms": t_recv,
                }
        else:
            with self._lock:
                self._active_hypotheses.pop(symbol, None)
                self._latest_hypothesis_batch_result.pop(symbol, None)
                self._latest_hypothesis_authority.pop(symbol, None)
                self._latest_cycle_outcome[symbol] = {
                    "status": "DEGRADED",
                    "transaction_status": "FAILED",
                    "rollback_status": "ROLLED_BACK" if rollback_ok else "ROLLBACK_FAILED",
                    "as_of_ms": t_recv,
                    "write_status": "FAILED",
                    "message": "Bot tạm dừng chuẩn bị lệnh mới vì chưa lưu được dữ liệu. Các lệnh đã có vẫn được giữ nguyên; hệ thống sẽ đánh giá lại khi kết nối dữ liệu hoạt động.",
                }
        try:
            daily_goal_eval = TradingPolicyService.evaluate_daily_frequency_goal(
                db, now_ms=t_recv, clock=clock, daily_min_goal=getattr(cfg, "daily_min_goal", 2)
            )
            daily_goal_dict = daily_goal_eval.to_dict()
        except Exception as dg_e:
            logger.debug(f"Failed to evaluate daily frequency goal: {dg_e}")
            daily_goal_dict = {}

        cg01_final = cg01_eval_data or serialize_cg01_evaluation(None, enabled=False, evaluated=False, as_of_ms=t_recv)
        self._record_and_broadcast_cg01_evaluation(symbol, cg01_final)

        # V17.6 Observability Telemetry Recording
        cycle_dur_ms = max(0.0, (time.perf_counter() - t_perf_start) * 1000)
        t_cycle_done = (t_recv + int(cycle_dur_ms)) if now_ms is not None else int(time.time() * 1000)
        if armed_order and commit_ok:
            cycle_decision_status = "ARMED"
        elif len(candidates) > 0 and commit_ok:
            cycle_decision_status = "BLOCKED"
        else:
            cycle_decision_status = "NO_SETUP"

        try:
            from services.engine_observability_service import engine_observability_service
            engine_observability_service.record_evaluation_cycle(
                db,
                cycle_id=cycle_id,
                run_id=effective_run_id,
                started_at_ms=t_recv,
                completed_at_ms=t_cycle_done,
                status="COMPLETED" if commit_ok else "DEGRADED",
                symbol=symbol,
                duration_ms=cycle_dur_ms,
                opportunities_detected=len(candidates) if commit_ok else 0,
                opportunities_qualified=len(entry_authorized_hypotheses) if commit_ok else 0,
                opportunities_blocked=(len(candidates) - (1 if armed_order else 0)) if commit_ok else 0,
                armed_order_id=armed_order.id if armed_order and commit_ok else None,
                decision_status=cycle_decision_status,
                market_regime=regime,
                evidence_origin=eff_evidence_origin,
                canonical_quote=canonical_quote_snap,
            )
        except Exception as tel_err:
            logger.debug(f"Error recording cycle observability telemetry: {tel_err}")

        return {
            "cycle_id": cycle_id,
            "decision_status": cycle_decision_status,
            "status": cycle_status if commit_ok else "DEGRADED",
            "lifecycle_read_status": batch_result.read_status,
            "lifecycle_write_status": cycle_authority.write_status,
            "lifecycle_write_issues_count": cycle_authority.write_issues_count,
            "transaction_status": "COMMITTED" if commit_ok else "FAILED",
            "rollback_status": "NONE" if commit_ok else ("ROLLED_BACK" if rollback_ok else "ROLLBACK_FAILED"),
            "as_of_ms": t_recv,
            "zones_count": len(bounded_zones),
            "candidates_count": len(candidates) if commit_ok else 0,
            "armed_order_id": armed_order.id if armed_order and commit_ok else None,
            "evaluation_duration_ms": max(0, t_cycle_done - t_start),
            "hypotheses_count": len(hypotheses),
            "entry_authorized_hypotheses_count": len(entry_authorized_hypotheses) if commit_ok else 0,
            "issues_count": len(batch_result.issues),
            "liquidity_levels_count": len(liq_levels),
            "market_regime": regime,
            "daily_goal": daily_goal_dict,
            "cg01_evaluation": cg01_final,
            "model_diagnostics": model_diagnostics,
            "shared_decision_snapshots": [s.to_parity_dict() for s in shared_decision_snapshots],
            "evaluator_version": EVALUATOR_VERSION,
        }

    def _finalize_failed_cycle(
        self,
        db: Session,
        symbol: str,
        *,
        t_recv: int,
        t_start: int,
        batch_result: Any,
        cycle_authority: Any,
        bounded_zones_count: int = 0,
        hypotheses_count: int = 0,
        regime: str = "UNKNOWN",
        liq_levels_count: int = 0,
        cycle_id: Optional[str] = None,
        t_perf_start: Optional[float] = None,
        effective_run_id: Optional[str] = None,
        evidence_origin: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Failure finalization helper for aborted/failed cycle transactions (V15.8 PROMPT 049-056).
        Rolls back the active transaction without making further queries on a poisoned session.
        Clears authoritative cache and publishes runtime diagnostic outcome.
        """
        rollback_ok = False
        try:
            db.rollback()
            rollback_ok = True
        except Exception as rb_err:
            logger.error(f"Error executing rollback during failed cycle finalization: {rb_err}")
            rollback_ok = False

        cycle_authority.write_status = "FAILED"
        cycle_authority.has_write_failures = True

        with self._lock:
            self._active_hypotheses.pop(symbol, None)
            self._latest_hypothesis_batch_result.pop(symbol, None)
            self._latest_hypothesis_authority.pop(symbol, None)
            self._latest_cycle_outcome[symbol] = {
                "status": "DEGRADED",
                "transaction_status": "FAILED",
                "rollback_status": "ROLLED_BACK" if rollback_ok else "ROLLBACK_FAILED",
                "as_of_ms": t_recv,
                "write_status": "FAILED",
                "message": "Bot tạm dừng chuẩn bị lệnh mới vì chưa lưu được dữ liệu. Các lệnh đã có vẫn được giữ nguyên; hệ thống sẽ đánh giá lại khi kết nối dữ liệu hoạt động.",
            }

        t_cycle_done = int(time.time() * 1000)
        eff_dur_ms = max(0.0, (time.perf_counter() - t_perf_start) * 1000) if t_perf_start else max(0, t_cycle_done - t_start)
        eff_cycle_id = cycle_id or f"failed-{t_recv}"
        eff_evidence_origin = evidence_origin or getattr(self, "evidence_origin", None) or "LIVE_FORWARD"

        try:
            from services.engine_observability_service import engine_observability_service
            engine_observability_service.record_evaluation_cycle(
                db,
                cycle_id=eff_cycle_id,
                run_id=effective_run_id,
                started_at_ms=t_recv,
                completed_at_ms=t_cycle_done,
                status="FAILED",
                symbol=symbol,
                duration_ms=eff_dur_ms,
                opportunities_detected=0,
                opportunities_qualified=0,
                opportunities_blocked=0,
                decision_status="FAILED",
                failure_reason="TRANSACTION_FAILED",
                market_regime=regime,
                evidence_origin=eff_evidence_origin,
            )
        except Exception as tel_err:
            logger.debug(f"Error recording failed cycle telemetry: {tel_err}")

        cg01_failed = serialize_cg01_evaluation(None, enabled=True, evaluated=False, skip_reason="CYCLE_FAILED", as_of_ms=t_recv)
        self._record_and_broadcast_cg01_evaluation(symbol, cg01_failed)

        return {
            "cycle_id": eff_cycle_id,
            "decision_status": "FAILED",
            "status": "DEGRADED",
            "lifecycle_read_status": getattr(batch_result, "read_status", "UNKNOWN"),
            "lifecycle_write_status": "FAILED",
            "lifecycle_write_issues_count": getattr(cycle_authority, "write_issues_count", 1),
            "transaction_status": "FAILED",
            "rollback_status": "ROLLED_BACK" if rollback_ok else "ROLLBACK_FAILED",
            "as_of_ms": t_recv,
            "zones_count": bounded_zones_count,
            "candidates_count": 0,
            "armed_order_id": None,
            "evaluation_duration_ms": max(0, t_cycle_done - t_start),
            "hypotheses_count": hypotheses_count,
            "entry_authorized_hypotheses_count": 0,
            "issues_count": len(getattr(batch_result, "issues", [])),
            "liquidity_levels_count": liq_levels_count,
            "market_regime": regime,
            "daily_goal": {},
            "cg01_evaluation": cg01_failed,
        }

    @serialize_entry_mutation
    def _arm_paper_candidate(
        self,
        db: Session,
        cand: ScalpCandidate,
        *,
        now_ms: int,
        clock: Any,
        quote: Any,
        policy_settings: Dict[str, Any],
        config: Optional[M1ScalpConfig] = None,
        feed_health: Optional[Dict[str, Any]] = None,
        auto_enabled: Optional[bool] = None,
        authority_context: Optional[Any] = None,
        session_context: Optional[Any] = None,
        research_context: Optional[Any] = None,
    ) -> Optional[models.PaperOrder]:
        """
        Guarded ARM path under entry_mutation_lock (PHẦN 025 - 033, 059).
        Never bypasses entry guards or daily quotas.
        """
        from services.runtime_profile_service import assert_not_observe_only
        assert_not_observe_only(
            "ScalpWatcherOrchestrator._arm_paper_candidate",
            counter_key="BLOCKED_ARM_ATTEMPTS",
        )
        cfg = config if config is not None else load_effective_scalp_config(db)
        snap = cand.snapshot
        calc = cand.calculation

        # Reason-aware repeated arm-rejection suppression (Rule 109)
        suppression_ms = getattr(cfg, "arm_rejection_suppression_ms", 30_000)
        with self._lock:
            last_rej = self._arm_rejections.get(cand.candidate_key)
            if last_rej:
                last_ts, last_reason = last_rej
                if (now_ms - last_ts) < suppression_ms:
                    logger.debug(f"Candidate {cand.candidate_key} suppressed due to repeated rejection ({last_reason})")
                    return None

        # Validate calculation output (PHẦN 026, 037)
        if calc is None or not getattr(calc, "is_valid", False) or not getattr(calc, "can_execute", False):
            logger.info(f"Candidate calculation is invalid or cannot execute: {getattr(calc, 'skip_reason', None) or getattr(calc, 'invalid_reason', None)}")
            return None

        # PHẦN 084: Invariant validation between cand.stop_loss and calc.stop_loss (STOP_PLAN_MISMATCH)
        cand_sl = float(getattr(cand, "stop_loss", 0.0))
        calc_sl = float(getattr(calc, "stop_loss", cand_sl))
        if abs(cand_sl - calc_sl) > 1e-4:
            logger.error(f"STOP_PLAN_MISMATCH: cand.stop_loss ({cand_sl}) != calc.stop_loss ({calc_sl})")
            return None

        # V17.5.1 Fail-closed stop placement barrier at ARM boundary (PHẦN 011, 020, 071, 073)
        from services.stop_placement_policy_service import resolve_stop_placement_policy
        from services.stop_placement_service import validate_applied_stop_decision
        current_sp_policy = resolve_stop_placement_policy(db)
        is_sp_valid, sp_err = validate_applied_stop_decision(
            candidate=cand,
            current_policy=current_sp_policy,
            tolerance_tick=float(getattr(cfg, "tick_size", 0.01)),
            now_ms=now_ms,
        )
        if not is_sp_valid:
            logger.error(f"ARM_BARRIER_REJECTED: {sp_err}")
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{getattr(cand, 'candidate_key', 'unknown')}",
                    symbol=getattr(cand, "symbol", "XAUUSDT"),
                    model_id=getattr(cand, "model_id", "UNKNOWN"),
                    direction=getattr(cand, "direction", "LONG"),
                    zone_id=getattr(cand, "zone_id", "UNKNOWN"),
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=(f"ARM_STOP_BARRIER_REJECTED: {sp_err}",),
                    planned_entry=getattr(cand, "planned_entry", 0.0),
                    stop_loss=getattr(cand, "stop_loss", 0.0),
                    take_profit=getattr(cand, "take_profit", 0.0),
                    net_rr=getattr(cand, "net_rr", 0.0),
                    details={"error": sp_err},
                ),
                now_ms=now_ms,
            )
            return None

        qty = float(getattr(calc, "quantity", 0.0))
        if qty <= 0 or not math.isfinite(qty):
            logger.info(f"Invalid calculator quantity: {qty}")
            return None

        # Cost configuration integrity check (D04, Section 06 Part 052)
        if policy_settings.get("costs_valid") is False or policy_settings.get("cost_config_error"):
            cost_err = policy_settings.get("cost_config_error") or "INVALID_COST_CONFIGURATION"
            logger.warning(f"Cost configuration error, refusing to arm candidate {cand.candidate_key}: {cost_err}")
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=("INVALID_COST_CONFIGURATION",),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                    details={"error": cost_err},
                ),
                now_ms=now_ms,
            )
            return None

        # 0. Critical section auto check (PHẦN 024, 025, 026)
        if auto_enabled is not None:
            is_auto = bool(auto_enabled)
        else:
            try:
                from services.strategy_service import strategy_service
                is_auto = strategy_service.get_auto_state(db)
            except Exception as e:
                logger.warning(f"Error checking auto state in arm critical section: {e}")
                is_auto = False

        if not is_auto:
            logger.info(f"Auto trading disabled or unreadable at arm time, refusing to arm candidate {cand.candidate_key}")
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=("GLOBAL_AUTO_DISABLED",),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                ),
                now_ms=now_ms,
            )
            return None

        # 0b. Atomic Daily Cap & Pending Opening Intent check (Rules 008, 131)
        audit = crud.get_or_create_today_audit(db, clock=clock)
        effective_max_cap = int(getattr(cfg, "daily_max_cap", 3) or 3)
        armed_open_count = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").count()
        if (audit.fills_count + armed_open_count) >= effective_max_cap:
            logger.info(
                f"Daily cap reached: fills={audit.fills_count}, armed={armed_open_count}, cap={effective_max_cap}. "
                f"Refusing to arm candidate {cand.candidate_key}"
            )
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=("DAILY_CAP_REACHED",),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                    details={"fills_count": audit.fills_count, "armed_count": armed_open_count, "daily_max_cap": effective_max_cap},
                ),
                now_ms=now_ms,
            )
            return None

        # 1. Idempotency Check: prevent duplicate orders for the same signal (PHẦN 058, Rule 128)
        from services.scalp_m1_model_service import candidate_execution_fingerprint
        cand_fp = candidate_execution_fingerprint(cand)
        idempotency_key = f"scalp_arm_{cand_fp}"
        existing_order = db.query(models.PaperOrder).filter(
            (models.PaperOrder.idempotency_key == idempotency_key) |
            (models.PaperOrder.idempotency_key == f"scalp_arm_{cand.candidate_key}")
        ).first()
        if existing_order:
            logger.info(f"Idempotent hit: PaperOrder {existing_order.id} already armed for {cand.candidate_key}")
            return existing_order

        # 1b. Check candidate lifecycle authority (SECTIONS 088 - 093)
        from services.liquidity_scenario_service import check_candidate_lifecycle_authority
        is_auth, auth_reason = check_candidate_lifecycle_authority(cand, authority_context)
        if not is_auth:
            logger.info(f"Refusing to arm candidate {cand.candidate_key}: lifecycle authority rejected ({auth_reason})")
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=(auth_reason or OpportunityReasonCode.GUARD_BLOCKED.value,),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                ),
                now_ms=now_ms,
            )
            return None

        # 1c. Validate and normalize execution quote before mutations (V15.8 PROMPT 120, 121, 126)
        v_quote = normalize_execution_quote(quote, now_ms=now_ms)
        if not v_quote.is_valid or v_quote.is_stale:
            reason = v_quote.rejection_code or ("TICKER_STALE" if v_quote.is_stale else "QUOTE_INVALID")
            msg = (
                "Giá nhận được đã cũ nên bot chưa chuẩn bị lệnh mới. Đang chờ báo giá mới từ nguồn dữ liệu."
                if v_quote.is_stale
                else (v_quote.rejection_detail or "Chưa xác định được thời điểm của báo giá nên bot tạm chưa vào lệnh.")
            )
            logger.info(f"Quote invalid or stale at arm boundary for {cand.candidate_key}: {reason} ({msg})")
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=(reason,),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                    details={"message": msg},
                ),
                now_ms=now_ms,
            )
            return None

        # 2. Immutable WatchSetup instance (PHẦN 031)
        ws_id = f"watch-{cand.symbol}-{cand.model_id}-{cand.candidate_key}"
        ws = db.query(models.WatchSetup).filter(models.WatchSetup.id == ws_id).first()
        if ws is None:
            ws = models.WatchSetup(
                id=ws_id,
                setup_instance_id=cand.candidate_key,
                direction=cand.direction,
                timeframe=snap.setup_timeframe if snap else "1M",
                state="READY",
                provisional_entry=cand.planned_entry,
                provisional_sl=cand.stop_loss,
                provisional_tp=cand.take_profit,
                invalidation_price=cand.stop_loss,
                strategy_family=cand.model_id,
                created_at=now_ms,
                updated_at=now_ms,
            )
            db.add(ws)
        else:
            ws.direction = cand.direction
            ws.state = "READY"
            ws.provisional_entry = cand.planned_entry
            ws.provisional_sl = cand.stop_loss
            ws.provisional_tp = cand.take_profit
            ws.invalidation_price = cand.stop_loss
            ws.strategy_family = cand.model_id
            ws.updated_at = now_ms
        db.flush()

        # 3. Save immutable scenario snapshot for chart evidence linking (PHẦN 022, 023, 069, 070)
        scenario_snap_id = None
        if snap is not None:
            from services.smc_scenario_repository import save_immutable_scenario_snapshot
            try:
                snap_row = save_immutable_scenario_snapshot(db, snap)
                scenario_snap_id = snap_row.snapshot_id
            except Exception as e:
                logger.warning(f"Error persisting scenario snapshot: {e}")
                err_code = "SNAPSHOT_IMMUTABILITY_CONFLICT" if "IMMUTABILITY_CONFLICT" in str(e).upper() else "SNAPSHOT_PERSISTENCE_FAILED"
                OpportunityFunnelService.record_opportunity_transition(
                    db,
                    OpportunityEvaluation(
                        opportunity_id=f"opp_{cand.candidate_key}",
                        symbol=cand.symbol,
                        model_id=cand.model_id,
                        direction=cand.direction,
                        zone_id=cand.zone_id,
                        as_of_ms=now_ms,
                        stage=OpportunityStage.GUARD_BLOCKED.value,
                        reason_codes=(err_code,),
                        planned_entry=cand.planned_entry,
                        stop_loss=cand.stop_loss,
                        take_profit=cand.take_profit,
                        net_rr=cand.net_rr,
                    ),
                    now_ms=now_ms,
                )
                return None

        # 4. Canonical Entry Guard evaluation (PHẦN 004, 034, 035)
        from services.entry_guard_service import read_execution_feed_health
        actual_feed_health = feed_health if feed_health is not None else read_execution_feed_health()

        # Extract evidence from cand or snap for daily bias binding verification (Rules 031, 050)
        raw_ev = getattr(cand, "evidence", None)
        if isinstance(raw_ev, dict):
            cand_evidence = dict(raw_ev)
        elif isinstance(raw_ev, (list, tuple)):
            cand_evidence = {"items": list(raw_ev)}
        else:
            cand_evidence = {}

        if snap and hasattr(snap, "context") and isinstance(snap.context, dict):
            if "direction_policy" in snap.context and "direction_policy" not in cand_evidence:
                cand_evidence["direction_policy"] = snap.context["direction_policy"]
            if "daily_bias" in snap.context and "daily_bias" not in cand_evidence:
                cand_evidence["daily_bias"] = snap.context["daily_bias"]

        # Resolve true current daily bias authority (Rules 022, 032-038, 061, 076)
        from services.cg01_daily_bias_service import resolve_current_cg01_bias_authority
        from services.cg01_config_factory import resolve_canonical_cg01_config, compute_cg01_config_fingerprint
        cg01_cfg = getattr(cfg, "cg01_config", None) if cfg else None
        if cg01_cfg is None:
            eff_cg01_cfg, eff_fp, _ = resolve_canonical_cg01_config(
                db=db,
                symbol=cand.symbol,
                scalp_config_override=cfg,
            )
            cg01_cfg = eff_cg01_cfg
        else:
            eff_fp = compute_cg01_config_fingerprint(cg01_cfg)

        cand_daily_b = cand_evidence.get("daily_bias") or {}
        cand_fp = cand_daily_b.get("config_fingerprint") if isinstance(cand_daily_b, dict) else getattr(cand_daily_b, "config_fingerprint", None)

        # Rule 021, 042: Query resolver using effective execution config (NOT cand_fp!)
        curr_auth = resolve_current_cg01_bias_authority(
            symbol=cand.symbol,
            now_ms=now_ms,
            config=cg01_cfg,
            config_fingerprint=eff_fp,
            clock=clock,
        )

        # Rule 046: Store complete authority envelope in cand_evidence for guard validation
        cand_evidence["effective_config_fingerprint"] = eff_fp
        cand_evidence["current_daily_bias"] = {
            "available": curr_auth.available,
            "status": curr_auth.status,
            "reason": curr_auth.reason,
            "bias": curr_auth.bias.to_dict() if curr_auth.bias else None,
            "symbol": curr_auth.symbol,
            "config_fingerprint": curr_auth.config_fingerprint,
            "evaluated_at_ms": curr_auth.evaluated_at_ms,
            "source": curr_auth.source,
            "authority_token": getattr(curr_auth.bias, "authority_token", None) if curr_auth.bias else None,
            "allowed_directions": list(getattr(curr_auth.bias, "allowed_directions", None) or []) if curr_auth.bias else [],
        }

        session_tag = getattr(session_context, "analysis_session", None) or getattr(session_context, "session", None) or "ALL"

        # V16.39.1 Evidence-based Quality Scoring (Sections 016 - 060)
        quality_assessment = None
        if getattr(cfg, "quality_scoring_enabled", False):
            from services.setup_quality_service import (
                assess_candidate_quality,
                normalize_candidate_evidence,
                build_candidate_scoring_context,
            )
            norm_ev = normalize_candidate_evidence(cand, as_of_ms=now_ms)
            active_m_ctx = self._active_market_context.get(cand.symbol, {})
            active_dr = self._active_dealing_range.get(cand.symbol)
            cand_atr = cand_evidence.get("atr") or cand_evidence.get("atr_15m") or active_m_ctx.get("atr")
            market_ctx = {
                "regime": (getattr(cand, "regime_snapshot", {}) or {}).get("regime", "UNKNOWN"),
                "htf_bias": cand_evidence.get("daily_bias", {}).get("direction", "UNKNOWN") if isinstance(cand_evidence.get("daily_bias"), dict) else "UNKNOWN",
                "h1_alignment": cand_evidence.get("h1_alignment") if isinstance(cand_evidence, dict) else None,
                "sr_zones": getattr(cand, "sr_evidence", None),
                "dealing_range": getattr(cand, "dealing_range", None) or active_dr,
                "atr": cand_atr,
                "session": session_tag,
            }
            score_ctx = build_candidate_scoring_context(
                candidate=cand,
                normalized_evidence=norm_ev,
                effective_config=cfg,
                quote=quote,
                calculation=calc,
                market_context=market_ctx,
                as_of_ms=now_ms,
            )
            lesson_delta = 0.0
            if getattr(cfg, "lesson_score_adjustment_enabled", False):
                lesson_eval_error = False
                lesson_err_msg = ""
                try:
                    from services.lesson_rule_service import LessonRuleService
                    # Pass complete context for lesson retrieval & scope check (Section 063)
                    lesson_scope_ctx = dict(score_ctx)
                    lesson_scope_ctx["symbol"] = cand.symbol
                    lesson_scope_ctx["direction"] = cand.direction
                    lesson_scope_ctx["strategy_family"] = cand.model_id
                    lesson_scope_ctx["execution_mode"] = "PAPER"
                    lesson_scope_ctx["stage"] = "BEFORE_ARM"
                    lesson_scope_ctx["session"] = session_tag

                    adj_res = LessonRuleService.evaluate_lesson_score_adjustments(lesson_scope_ctx, db=db, now_ms=now_ms)
                    if adj_res.get("status") == "ERROR":
                        lesson_eval_error = True
                        lesson_err_msg = adj_res.get("reason") or "Lesson evaluation returned ERROR status"
                    else:
                        lesson_delta = adj_res.get("applied_delta", 0.0)
                except Exception as l_err:
                    logger.warning(f"Lesson score adjustment evaluation error: {l_err}")
                    lesson_eval_error = True
                    lesson_err_msg = str(l_err)

                if lesson_eval_error:
                    # Section 107, 108, 175: Do NOT silently ignore lesson ERROR when enabled; block entry
                    logger.warning(f"Candidate {cand.candidate_key} blocked due to lesson evaluation error: {lesson_err_msg}")
                    rej_reason = "LESSON_EVALUATION_ERROR"
                    with self._lock:
                        self._arm_rejections[cand.candidate_key] = (now_ms, rej_reason)
                    OpportunityFunnelService.record_opportunity_transition(
                        db,
                        OpportunityEvaluation(
                            opportunity_id=f"opp_{cand.candidate_key}",
                            symbol=cand.symbol,
                            model_id=cand.model_id,
                            direction=cand.direction,
                            zone_id=cand.zone_id,
                            as_of_ms=now_ms,
                            stage=OpportunityStage.GUARD_BLOCKED.value,
                            reason_codes=(rej_reason,),
                            planned_entry=cand.planned_entry,
                            stop_loss=cand.stop_loss,
                            take_profit=cand.take_profit,
                            net_rr=cand.net_rr,
                            details={"error": lesson_err_msg},
                        ),
                        now_ms=now_ms,
                    )
                    return None

            assessment = assess_candidate_quality(
                candidate=cand,
                context=score_ctx,
                profile=cfg,
                as_of_ms=now_ms,
                threshold=float(getattr(cfg, "quality_arm_threshold", 70.0)),
                lesson_delta=lesson_delta,
            )
            quality_assessment = assessment
            self.record_candidate_quality_display(
                symbol=cand.symbol,
                setup_id=ws.id if ws else None,
                setup_instance_id=cand.candidate_key,
                candidate_key=cand.candidate_key,
                direction=cand.direction,
                assessment=assessment,
            )

            if assessment.decision != "ELIGIBLE_BY_SCORE":
                logger.info(
                    f"Candidate {cand.candidate_key} ({cand.model_id}) rejected by quality score: "
                    f"score={assessment.final_score:.2f}/{assessment.threshold:.2f}, decision={assessment.decision}, blockers={assessment.blockers}"
                )
                rej_reason = assessment.decision
                with self._lock:
                    self._arm_rejections[cand.candidate_key] = (now_ms, rej_reason)
                OpportunityFunnelService.record_opportunity_transition(
                    db,
                    OpportunityEvaluation(
                        opportunity_id=f"opp_{cand.candidate_key}",
                        symbol=cand.symbol,
                        model_id=cand.model_id,
                        direction=cand.direction,
                        zone_id=cand.zone_id,
                        as_of_ms=now_ms,
                        stage=OpportunityStage.GUARD_BLOCKED.value,
                        reason_codes=(rej_reason,),
                        planned_entry=cand.planned_entry,
                        stop_loss=cand.stop_loss,
                        take_profit=cand.take_profit,
                        net_rr=cand.net_rr,
                        details={"quality_assessment": assessment.to_dict()},
                    ),
                    now_ms=now_ms,
                )
                return None

        req = EntryGuardRequest(
            stage="ARM",
            now_ms=now_ms,
            symbol=cand.symbol,
            direction=cand.direction,
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            setup_id=ws.id,
            setup_instance_id=cand.candidate_key,
            setup_state=ws.state,
            timeframe=snap.setup_timeframe if snap else "1M",
            strategy_family=cand.model_id,
            planned_entry=cand.planned_entry,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            calc_result=calc,
            validated_quote=v_quote,
            feed_health=actual_feed_health,
            min_net_rr=cfg.min_net_rr,
            clock=clock,
            evidence=cand_evidence,
            min_protective_distance=getattr(cand.stop_placement, "protective_distance", None) if cand.stop_placement else None,
            unicorn_evidence=getattr(cand, "unicorn_evidence", None),
            stop_decision=cand.stop_placement,
        )

        decision = evaluate_entry_guard(db, req)
        if not decision.allowed:
            logger.info(f"Guard blocked {cand.model_id} candidate: {decision.primary_reason if hasattr(decision, 'primary_reason') else decision.reason_codes}")
            rej_reason = decision.reason_codes[0] if decision.reason_codes else "GUARD_BLOCKED"
            with self._lock:
                self._arm_rejections[cand.candidate_key] = (now_ms, rej_reason)
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=tuple(decision.reason_codes),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                ),
                now_ms=now_ms,
            )
            return None

        # 5. Guard passed: Create PaperOrder with canonical sizing and schema fields (PHẦN 025 - 030, Rule 031 - 034)
        with self._lock:
            self._arm_rejections.pop(cand.candidate_key, None)
        from services.cg01_types import candidate_execution_order_type, resolve_candidate_order_expiry
        resolved_order_type = candidate_execution_order_type(cand, legacy_default="MARKET")
        try:
            resolved_expires_at = resolve_candidate_order_expiry(cand, now_ms=now_ms, session_context=session_context)
        except Exception as e:
            logger.warning(f"[_arm_paper_candidate] Expiry resolution failed for candidate {cand.candidate_key}: {e}")
            return None

        # Rule 032: Check expiry before mutation - reject if already expired
        if now_ms >= resolved_expires_at:
            logger.info(f"[_arm_paper_candidate] Candidate {cand.candidate_key} expired before arm (now={now_ms} >= expires={resolved_expires_at})")
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=("TRIGGER_EXPIRED",),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                ),
                now_ms=now_ms,
            )
            return None

        from services.order_plan_snapshot_service import build_arm_plan_snapshot
        from services.plan_financial_context_service import resolve_execution_cost_context
        cost_ctx = resolve_execution_cost_context(
            candidate=cand,
            settings=policy_settings,
            config=cfg,
            as_of_ms=now_ms,
        )
        if not cost_ctx.get("available", False):
            logger.info(f"Cost context unavailable at arm boundary: {cost_ctx.get('reason')}")
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=("COST_CONTEXT_UNAVAILABLE",),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                    details={"cost_reason": cost_ctx.get("reason")},
                ),
                now_ms=now_ms,
            )
            return None
        cand_slip = cost_ctx["entry_slippage_usd"]
        effective_risk = policy_settings.get("risk_pct") if policy_settings.get("risk_pct") is not None else float(getattr(calc, "effective_risk_pct", cfg.fallback_risk_pct))
        target_capital = float(policy_settings.get("capital_usdt", 1000.0))
        configured_risk_pct = float(effective_risk)
        target_budget = round(target_capital * (configured_risk_pct / 100.0), 4)
        resolved_cap_mode = str(policy_settings.get("capital_mode", "CURRENT_ACCOUNT_BALANCE") or "CURRENT_ACCOUNT_BALANCE").upper()
        if resolved_cap_mode not in ("CURRENT_ACCOUNT_BALANCE", "FIXED_INITIAL_CAPITAL"):
            resolved_cap_mode = "CURRENT_ACCOUNT_BALANCE"

        arm_snap = build_arm_plan_snapshot(
            calc,
            order_type=resolved_order_type,
            direction=cand.direction,
            planned_entry=cand.planned_entry,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            capital_usdt=target_capital,
            risk_pct=configured_risk_pct,
            leverage=calc.leverage,
            margin_mode=calc.margin_mode,
            now_ms=now_ms,
            policy_config_version=cfg.config_version,
            risk_config_version=cfg.config_version,
            slippage_usd=cand_slip,
            capital_mode=resolved_cap_mode,
            target_capital=target_capital,
            target_risk_budget=target_budget,
            min_net_rr=cfg.min_net_rr,
            resolved_execution_costs=cost_ctx if cost_ctx.get("available") else None,
        )
        arm_snap["expires_at"] = resolved_expires_at
        arm_snap["execution_profile"] = getattr(cfg, "execution_profile", "BASELINE")
        arm_snap["session"] = session_tag
        arm_snap["analysis_session"] = session_tag
        arm_snap["session_policy_version"] = getattr(session_context, "policy_version", 1) if session_context else 1
        arm_snap["order_type"] = resolved_order_type
        # V16.39.6 Rule 026 - 028: Freeze entry_mode in arm snapshot
        resolved_entry_mode = (
            getattr(cand, "entry_mode", None)
            or (cand.entry_intent.get("entry_mode") if isinstance(getattr(cand, "entry_intent", None), dict) else None)
            or ((getattr(cand.snapshot, "plan", None) or {}).get("entry_mode") if cand.snapshot and hasattr(cand.snapshot, "plan") else None)
            or ("CONFIRMED_RETEST_MARKET" if resolved_order_type == "MARKET" else "LIMIT_AT_CE_AFTER_CONFIRMATION")
        )
        arm_snap["entry_mode"] = resolved_entry_mode
        if hasattr(cand, "regime_snapshot") and cand.regime_snapshot:
            arm_snap["regime_snapshot"] = cand.regime_snapshot
        if hasattr(cand, "sr_evidence") and cand.sr_evidence:
            arm_snap["sr_evidence"] = cand.sr_evidence
        if quality_assessment:
            arm_snap["quality_assessment"] = quality_assessment.to_dict()

        # V17.5/V17.8.0.1: Stop Placement & Unicorn additive metadata in arm_snap
        sp = getattr(cand, "stop_placement", None)
        if sp is not None:
            arm_snap["stop_placement"] = sp.to_dict() if hasattr(sp, "to_dict") else sp
        uni_ev = getattr(cand, "unicorn_evidence", None)
        if uni_ev is not None:
            arm_snap["unicorn_evidence"] = uni_ev.to_dict() if hasattr(uni_ev, "to_dict") else uni_ev
            arm_snap["unicorn_evidence_id"] = (
                getattr(uni_ev, "unicorn_id", None)
                if not isinstance(uni_ev, dict)
                else uni_ev.get("unicorn_id")
            )
        else:
            arm_snap["unicorn_evidence_id"] = getattr(sp, "unicorn_id", None) if sp is not None else None
        if getattr(cand, "stop_comparison", None) is not None:
            sc = cand.stop_comparison
            arm_snap["stop_comparison_plan_id"] = getattr(sc, "opportunity_key", None) or getattr(sc, "id", None)
            arm_snap["stop_comparison"] = sc.to_dict() if hasattr(sc, "to_dict") else sc
        from services.stop_placement_policy_service import resolve_stop_placement_policy
        current_sp_policy = resolve_stop_placement_policy(db)
        arm_snap["selected_stop_loss"] = cand.stop_loss
        arm_snap["selected_take_profit"] = cand.take_profit
        arm_snap["min_protective_distance"] = (
            getattr(sp, "protective_distance", None)
            if sp is not None and not isinstance(sp, dict)
            else (sp.get("protective_distance") if isinstance(sp, dict) else None)
        )
        arm_snap["stop_mode_at_arm"] = getattr(sp, "mode", current_sp_policy.mode) if sp is not None else current_sp_policy.mode
        arm_snap["stop_policy_version"] = getattr(sp, "policy_version", current_sp_policy.version) if sp is not None else current_sp_policy.version
        arm_snap["stop_algorithm_version"] = (
            getattr(sp, "algorithm_version", current_sp_policy.algorithm_version)
            if sp is not None
            else current_sp_policy.algorithm_version
        )
        arm_snap["applied_variant"] = getattr(sp, "applied_variant", "A") if sp is not None else "A"
        sp_anchor = (
            getattr(sp, "anchor", None)
            if sp is not None and not isinstance(sp, dict)
            else (sp.get("anchor") if isinstance(sp, dict) else None)
        )
        arm_snap["structural_anchor_id"] = (
            getattr(sp_anchor, "id", None)
            if sp_anchor is not None and not isinstance(sp_anchor, dict)
            else (sp_anchor.get("id") if isinstance(sp_anchor, dict) else None)
        )
        arm_snap["quote_timestamp_at_arm"] = (
            getattr(v_quote, "observed_at", None) or getattr(v_quote, "exchange_time", None) or now_ms
            if v_quote is not None
            else now_ms
        )
        arm_snap["v18_structural_intelligence_enabled"] = bool(current_sp_policy.v18_structural_intelligence_enabled)
        arm_snap["v18_unicorn_detection_enabled"] = bool(current_sp_policy.v18_unicorn_detection_enabled)
        arm_snap["v18_noise_research_enabled"] = bool(current_sp_policy.v18_noise_research_enabled)
        arm_snap["v18_execution_policy_version"] = int(current_sp_policy.v18_execution_policy_version)

        # V17.5.9 (D03, Section 05 Part 037): Final shared ARM invariant barrier before order persistence
        is_arm_valid, arm_inv_reason = validate_arm_invariants(
            candidate=cand,
            calculation=calc,
            quote=v_quote,
            now_ms=now_ms,
            expires_at_ms=resolved_expires_at,
            daily_fills_count=audit.fills_count,
            daily_max_cap=effective_max_cap,
            active_armed_count=armed_open_count,
        )
        if not is_arm_valid:
            logger.warning(
                f"ARM_INVARIANTS_REJECTED: candidate {cand.candidate_key} failed invariant check: {arm_inv_reason}"
            )
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=(arm_inv_reason or "ARM_INVARIANTS_FAILED",),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                    details={"arm_invariant_reason": arm_inv_reason},
                ),
                now_ms=now_ms,
            )
            return None

        order_id = f"ord_{cand.model_id}_{cand.candidate_key}"
        order = models.PaperOrder(
            id=order_id,
            setup_id=ws.id,
            setup_instance_id=cand.candidate_key,
            config_version=int(policy_settings.get("config_version") or getattr(cfg, "config_version", 1)),
            idempotency_key=idempotency_key,
            signal_id=f"sig-{cand.candidate_key}",
            instrument=cand.symbol,
            direction=cand.direction,
            state="armed",
            order_type=resolved_order_type,
            timeframe=snap.setup_timeframe if snap else "1M",
            planned_entry=cand.planned_entry,
            actual_entry=None,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            quantity=qty,
            initial_risk_usdt=float(calc.net_risk_usdt),
            risk_pct=effective_risk,
            gross_rr=float(calc.gross_rr),
            estimated_net_rr=float(calc.net_rr),
            leverage=calc.leverage,
            margin_mode=calc.margin_mode,
            estimated_liquidation=calc.estimated_liquidation,
            opened_at=None,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=resolved_expires_at,
            origin="AUTO_STRATEGY",
            execution_mode="AUTO",
            strategy_family=cand.model_id,
            strategy_version="15.0",
            scenario_snapshot_id=scenario_snap_id,
            arm_decision_snapshot=json.dumps(decision.to_dict() if hasattr(decision, 'to_dict') else {}, ensure_ascii=False),
            cost_snapshot=json.dumps(arm_snap, ensure_ascii=False),
        )
        db.add(order)
        ws.state = "ARMED"
        ws.confirmed_entry = cand.planned_entry
        ws.confirmed_sl = cand.stop_loss
        ws.confirmed_tp = cand.take_profit
        ws.net_rr = calc.net_rr
        ws.gross_rr = calc.gross_rr
        ws.quantity = qty
        ws.risk_usdt = calc.net_risk_usdt
        db.flush()

        # V16.17: Exact run association and ARMED milestone within the same transaction (Rules 021, 044, 045)
        target_run_id = getattr(research_context, "run_id", None) if research_context else None
        if not target_run_id:
            try:
                from services.paper_research_run_service import resolve_active_observation_run
                act_run = resolve_active_observation_run(db=db, symbol=cand.symbol)
                if act_run:
                    target_run_id = act_run.run_id
            except Exception:
                pass

        trace_id_val = getattr(research_context, "trace_id", None) if research_context else None

        if target_run_id:
            try:
                with db.begin_nested():
                    from services.paper_research_run_service import (
                        associate_order_with_research_run,
                        record_committed_order_milestone,
                    )
                    associate_order_with_research_run(
                        db=db,
                        run_id=target_run_id,
                        order_id=order.id,
                        opportunity_id=f"opp_{cand.candidate_key}",
                        trace_id=trace_id_val,
                        now_ms=now_ms,
                    )
                    record_committed_order_milestone(
                        db=db,
                        run_id=target_run_id,
                        order_id=order.id,
                        milestone="ARMED",
                        timestamp_ms=now_ms,
                        payload={"planned_entry": cand.planned_entry, "stop_loss": cand.stop_loss, "take_profit": cand.take_profit},
                    )
            except Exception as ms_err:
                logger.warning(f"Failed to record ARMED milestone for order {order.id}: {ms_err}")

        OpportunityFunnelService.record_opportunity_transition(
            db,
            OpportunityEvaluation(
                opportunity_id=f"opp_{cand.candidate_key}",
                symbol=cand.symbol,
                model_id=cand.model_id,
                direction=cand.direction,
                zone_id=cand.zone_id,
                as_of_ms=now_ms,
                stage=OpportunityStage.ARMED.value,
                armed_at=now_ms,
                order_id=order.id,
                run_id=target_run_id,
                watch_setup_id=ws.id,
                planned_entry=cand.planned_entry,
                stop_loss=cand.stop_loss,
                take_profit=cand.take_profit,
                net_rr=cand.net_rr,
            ),
            now_ms=now_ms,
        )

        # 6. Publish order.armed domain event atomically within caller's db transaction (PHẦN 019, 020, 021, 138, Rule 034)
        from services.event_bus import event_bus
        event_bus.publish_event(
            event_type="order.armed",
            aggregate_id=order.id,
            payload={
                "order_id": order.id,
                "setup_id": ws.id,
                "direction": cand.direction,
                "order_type": order.order_type,
                "planned_entry": cand.planned_entry,
                "stop_loss": cand.stop_loss,
                "take_profit": cand.take_profit,
                "net_rr": calc.net_rr,
                "expires_at": resolved_expires_at,
                "run_id": target_run_id,
                "trace_id": trace_id_val,
            },
            db=db,
            occurred_at=now_ms,
        )

        try:
            db.commit()
        except Exception as c_err:
            logger.error(f"[_arm_paper_candidate] Failed to commit armed order {order.id}: {c_err}")
            db.rollback()
            return None

        return order
    async def run_watcher_loop(self):
        """Periodic background evaluation loop for autonomous scalp watcher (Rules 011, 070, 165)."""
        from services.runtime_profile_service import assert_not_observe_only
        assert_not_observe_only(
            "ScalpWatcherOrchestrator.run_watcher_loop",
            counter_key="BLOCKED_WORKER_START_ATTEMPTS",
        )
        self._running = True
        while self._running:
            try:
                await asyncio.sleep(2.0)
                def _eval():
                    db = SessionLocal()
                    try:
                        from services.cg01_scheduler_service import (
                            cg01_evaluation_scheduler,
                            resolve_entry_execution_owner,
                        )
                        # Rules 014, 024, 025, 051-055: Use effective owner arbitration instead of raw is_owner
                        owner_info = resolve_entry_execution_owner(cg01_evaluation_scheduler, db=db)

                        # V17.6 Heartbeat observation (EVID-01, EVID-31)
                        try:
                            from services.engine_observability_service import (
                                engine_observability_service,
                                resolve_observation_evidence_quality,
                            )
                            feed_health_snap = engine_observability_service.build_market_feed_health_snapshot(symbol="XAUUSDT")
                            feed_quality = resolve_observation_evidence_quality(
                                process_heartbeat="RUNNING" if owner_info.get("can_execute") else "IDLE",
                                feed_health=feed_health_snap,
                                symbol="XAUUSDT",
                            )
                            engine_observability_service.record_engine_heartbeat(
                                db,
                                symbol="XAUUSDT",
                                state="RUNNING" if owner_info.get("can_execute") else "IDLE",
                                metadata={
                                    "worker_role": "WATCHER_PROCESS",
                                    "execution_owner": owner_info.get("execution_owner", "UNKNOWN"),
                                    "can_execute": owner_info.get("can_execute", False),
                                    "scope": "CG01",
                                    "mode": "PAPER",
                                    "evidence_origin": "LIVE_FORWARD",
                                    "evaluation_origin": "LIVE_FORWARD",
                                    "feed_health": feed_health_snap,
                                    "canonical_quote": feed_health_snap,
                                    "coverage_quality": feed_quality.quality,
                                    "is_feed_healthy": feed_quality.quote_valid,
                                    "is_quote_fresh": feed_quality.quality == "GOOD",
                                    "transport_state": feed_quality.transport_state,
                                    "quote_age_sec": round((feed_quality.quote_age_ms or 0) / 1000.0, 2) if feed_quality.quote_age_ms else None,
                                },
                            )
                        except Exception:
                            pass

                        if owner_info["execution_owner"] == "SCHEDULER":
                            # Rule 055: Scheduler is active execution owner; watcher acts strictly as watchdog
                            # and does not run competing mutable production evaluation cycles.
                            return
                        # Watcher is effective execution owner:
                        self.evaluate_cycle(
                            db,
                            "XAUUSDT",
                            auto_enabled=owner_info["can_execute"],
                            caller_identity="WATCHER",
                            evaluation_origin="LIVE_FORWARD",
                        )
                    finally:
                        db.close()
                await asyncio.to_thread(_eval)
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error(f"Scalp watcher loop error: {e}", exc_info=True)
                await asyncio.sleep(2.0)


    def stop(self):
        self._running = False

    def build_shared_decision_context(
        self,
        *,
        symbol: str = "XAUUSDT",
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
        V17.9.0.1 Phase D: Production adapter method constructing immutable
        MarketDecisionContext for SharedStrategyEvaluator.
        """
        from services.shared_strategy_evaluator import build_market_decision_context
        return build_market_decision_context(
            symbol=symbol,
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
            model_enablement=model_enablement,
            policy_config=policy_config,
            risk_config=risk_config,
            cost_config=cost_config,
            strict_causal_validation=strict_causal_validation,
            strict_htf_validation=strict_htf_validation,
            htf_bias=htf_bias,
            h1_alignment=h1_alignment,
        )

    def evaluate_shared_decision_snapshot(
        self,
        *,
        context: Any,
        model_id: str,
        direction: Optional[str] = None,
    ):
        """
        V17.9.0 Section 7.2: Production adapter method delegating pure strategy
        decision evaluation to SharedStrategyEvaluator.
        """
        from services.shared_strategy_evaluator import SharedStrategyEvaluator
        if direction in ("LONG", "SHORT"):
            return SharedStrategyEvaluator.evaluate_model_direction(
                context=context,
                model_id=model_id,
                direction=direction,
                adapter_origin="PRODUCTION_ORCHESTRATOR_ADAPTER",
            )
        return SharedStrategyEvaluator.evaluate_model_best_direction(
            context=context,
            model_id=model_id,
            adapter_origin="PRODUCTION_ORCHESTRATOR_ADAPTER",
        )

    def evaluate_shared_all_models(
        self,
        *,
        context: Any,
        model_ids: Optional[Sequence[str]] = None,
        preferred_direction: Optional[str] = None,
    ):
        """
        V17.9.0 Section 7.2: Production adapter method evaluating all configured
        production models through SharedStrategyEvaluator.
        """
        from services.shared_strategy_evaluator import PRODUCTION_MODEL_IDS, SharedStrategyEvaluator
        return SharedStrategyEvaluator.evaluate_all_models(
            context=context,
            model_ids=model_ids or PRODUCTION_MODEL_IDS,
            preferred_direction=preferred_direction,
            adapter_origin="PRODUCTION_ORCHESTRATOR_ADAPTER",
        )


scalp_watcher_orchestrator = ScalpWatcherOrchestrator()
