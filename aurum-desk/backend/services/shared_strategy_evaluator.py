"""
Aurum Desk V17.9.0 — Shared Strategy Evaluator & True Production Decision Parity Layer.

Provides the single authoritative, side-effect-free strategy decision contract shared across:
  1. Production evaluation (`ScalpWatcherOrchestrator` adapter)
  2. Live research observation (`MarketShadowService` adapter)
  3. Historical production-parity backtesting (`V17_8_BacktestRunner` adapter)

Trading authorization: NONE. Pure evaluation only — zero database writes, zero order mutations,
zero arming mutations, zero position creation.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import smc_engine
from domain_calculator import CostAssumptions, calculate_risk_reward
from services.cg01_context_adapter import build_cg01_closed_context
from services.cg01_engine import CG01Config, evaluate_cg01_candidate
from services.liquidity_scenario_service import identify_confirmed_swings
from services.quote_validator import CanonicalQuote
from services.scalp_m1_model_service import (
    ScalpCandidate,
    ScalpM1ModelService,
    candle_open_ms,
    detect_confirmed_continuation_break,
    evaluate_displacement,
    resolve_confirmed_structure_reference,
    resolve_executable_target,
)
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    WatchedZone,
    get_scalp_config,
)
from services.smc_liquidity_service import build_liquidity_clusters, build_liquidity_map
from services.stop_placement_research_service import build_stop_comparison_plan
from services.stop_placement_service import (
    compute_stop_atr,
    compute_stop_buffer,
    plan_structural_stop,
    resolve_stop_anchor,
)
from services.stop_placement_types import (
    StopPlacementMode,
    StopPlacementPolicy,
    StopStatus,
)
from services.unicorn_evidence_service import detect_breaker_blocks, detect_unicorn_evidence


PRODUCTION_MODEL_IDS: Tuple[str, ...] = (
    ModelIdentifier.SMC_M1_REVERSAL.value,
    ModelIdentifier.SMC_M1_CONTINUATION.value,
    ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
    ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
    ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
)

ALL_EVALUATED_MODEL_IDS: Tuple[str, ...] = (
    ModelIdentifier.SMC_M1_REVERSAL.value,
    ModelIdentifier.SMC_M1_CONTINUATION.value,
    ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
    ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
    ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
    "UNICORN_BREAKER_FVG",
)


class DecisionStatus(str, Enum):
    MODEL_DISABLED = "MODEL_DISABLED"
    MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
    NO_VALID_SETUP = "NO_VALID_SETUP"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    VALID_SETUP = "VALID_SETUP"
    VALID_SETUP_REJECTED_BY_RISK = "VALID_SETUP_REJECTED_BY_RISK"
    ELIGIBLE = "ELIGIBLE"
    NO_TRADE = "NO_TRADE"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


VALID_DECISION_STATUSES: Tuple[str, ...] = (
    DecisionStatus.MODEL_DISABLED.value,
    DecisionStatus.MISSING_MARKET_DATA.value,
    DecisionStatus.NO_VALID_SETUP.value,
    DecisionStatus.WAITING_CONFIRMATION.value,
    DecisionStatus.VALID_SETUP.value,
    DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value,
    DecisionStatus.ELIGIBLE.value,
)

NON_EXECUTABLE_DECISION_STATUSES: Tuple[str, ...] = (
    DecisionStatus.NO_TRADE.value,
    DecisionStatus.REJECTED.value,
    DecisionStatus.MODEL_DISABLED.value,
    DecisionStatus.MISSING_MARKET_DATA.value,
    DecisionStatus.NO_VALID_SETUP.value,
    DecisionStatus.WAITING_CONFIRMATION.value,
    DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value,
    DecisionStatus.UNKNOWN.value,
)

REQUIRED_PARITY_FIELDS: Tuple[str, ...] = (
    "model_id",
    "direction",
    "setup_validity",
    "entry_reference",
    "selected_fvg_id",
    "selected_break_id",
    "selected_sweep_id",
    "setup_episode_id",
    "unicorn_validity",
    "selected_anchor_id",
    "structural_invalidation_price",
    "selected_sl",
    "take_profit",
    "atr_m1",
    "atr_m5",
    "spread",
    "hypothetical_quantity",
    "net_rr",
    "decision_status",
    "rejection_reason_codes",
)


def _canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _DualAccessCandle(dict):
    """Immutable-style candle supporting both attribute (b.high, b.open_time) and dict (b['high'], b.get('high')) access."""

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def _normalize_candle_obj(bar: Any, symbol: str, timeframe: str, tf_ms: int) -> Any:
    open_ms = _candle_open_ms(bar)
    close_ms = _candle_close_ms(bar, tf_ms)
    if isinstance(bar, dict):
        d = dict(bar)
    elif dataclasses.is_dataclass(bar) and not isinstance(bar, type):
        d = dataclasses.asdict(bar)
    else:
        d = {
            "symbol": getattr(bar, "symbol", symbol),
            "timeframe": getattr(bar, "timeframe", timeframe),
            "open": getattr(bar, "open", 0.0),
            "high": getattr(bar, "high", 0.0),
            "low": getattr(bar, "low", 0.0),
            "close": getattr(bar, "close", 0.0),
            "volume": getattr(bar, "volume", 100.0),
            "is_closed": getattr(bar, "is_closed", True),
        }
    d["symbol"] = d.get("symbol", symbol)
    d["timeframe"] = d.get("timeframe", timeframe)
    d["open_time"] = open_ms
    d["open_time_ms"] = open_ms
    d["timestamp"] = open_ms
    d["close_time"] = close_ms
    d["close_time_ms"] = close_ms
    d["open"] = float(d.get("open", 0.0))
    d["high"] = float(d.get("high", 0.0))
    d["low"] = float(d.get("low", 0.0))
    d["close"] = float(d.get("close", 0.0))
    d["volume"] = float(d.get("volume", 100.0))
    d["is_closed"] = bool(d.get("is_closed", True))
    return _DualAccessCandle(d)


def _candle_open_ms(bar: Any) -> int:
    if isinstance(bar, dict):
        val = bar.get("open_time_ms") or bar.get("open_time") or bar.get("timestamp") or 0
        return int(val)
    val = getattr(bar, "open_time_ms", None)
    if val is None:
        val = getattr(bar, "open_time", None)
    if val is None:
        val = getattr(bar, "timestamp", 0)
    return int(val or 0)


def _candle_close_ms(bar: Any, tf_ms: int) -> int:
    if isinstance(bar, dict):
        c = bar.get("close_time_ms") or bar.get("close_time")
        if c is not None:
            return int(c)
    else:
        c = getattr(bar, "close_time_ms", None)
        if c is None:
            c = getattr(bar, "close_time", None)
        if c is not None:
            return int(c)
    open_ms = _candle_open_ms(bar)
    return open_ms + tf_ms if open_ms > 0 else 0


def _candle_is_closed(bar: Any) -> bool:
    if isinstance(bar, dict):
        return bool(bar.get("is_closed", True))
    return bool(getattr(bar, "is_closed", True))


def _canonical_candle_series(bars: Sequence[Any], tf_ms: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for b in bars or ():
        if isinstance(b, dict):
            o_px = round(float(b.get("open", 0.0) or 0.0), 4)
            h_px = round(float(b.get("high", 0.0) or 0.0), 4)
            l_px = round(float(b.get("low", 0.0) or 0.0), 4)
            c_px = round(float(b.get("close", 0.0) or 0.0), 4)
        else:
            o_px = round(float(getattr(b, "open", 0.0) or 0.0), 4)
            h_px = round(float(getattr(b, "high", 0.0) or 0.0), 4)
            l_px = round(float(getattr(b, "low", 0.0) or 0.0), 4)
            c_px = round(float(getattr(b, "close", 0.0) or 0.0), 4)
        out.append({
            "open_ms": _candle_open_ms(b),
            "close_ms": _candle_close_ms(b, tf_ms),
            "open": o_px,
            "high": h_px,
            "low": l_px,
            "close": c_px,
            "is_closed": _candle_is_closed(b),
        })
    return out


def _candle_summary(bars: Sequence[Any], tf_ms: int) -> Dict[str, Any]:
    if not bars:
        return {
            "count": 0,
            "first_open_ms": None,
            "last_open_ms": None,
            "last_close": None,
            "series_hash": _canonical_hash([]),
        }
    first_b = bars[0]
    last_b = bars[-1]
    last_close = last_b.get("close") if isinstance(last_b, dict) else getattr(last_b, "close", None)
    return {
        "count": len(bars),
        "first_open_ms": _candle_open_ms(first_b),
        "last_open_ms": _candle_open_ms(last_b),
        "last_close_ms": _candle_close_ms(last_b, tf_ms),
        "last_close": round(float(last_close), 4) if last_close is not None else None,
        "series_hash": _canonical_hash(_canonical_candle_series(bars, tf_ms)),
    }


EVALUATOR_VERSION = "17.9.0.1.1"


def validate_htf_candle_series(
    raw_bars: Optional[Sequence[Any]],
    *,
    symbol: str,
    timeframe: str,
    tf_ms: int,
    decision_timestamp_ms: int,
    strict_validation: bool = False,
) -> Tuple[List[Any], str, List[str]]:
    """
    Phase G: Validates higher-timeframe (H1/H4/D1) closed candle series across runtimes.
    Rejects unclosed bars, lookahead bars (`close_time > decision_timestamp_ms`), duplicate
    timestamps, non-monotonic timestamps, malformed OHLC values, and misaligned UTC boundaries
    (when `strict_validation=True`).
    """
    bars = list(raw_bars or [])
    if not bars:
        return [], "OPTIONAL_EMPTY", []

    closed_out: List[Any] = []
    reasons: List[str] = []
    seen_open_times: set[int] = set()
    last_open_ms = -1
    unclosed_count = 0
    lookahead_count = 0

    for b in bars:
        if not _candle_is_closed(b):
            unclosed_count += 1
            if strict_validation:
                reasons.append(f"UNCLOSED_{timeframe}_CANDLE_REJECTED")
                reasons.append("UNCLOSED_CANDLE")
            continue

        open_ms = _candle_open_ms(b)
        close_ms = _candle_close_ms(b, tf_ms)
        if open_ms <= 0 or close_ms <= open_ms:
            if strict_validation:
                reasons.append(f"MALFORMED_{timeframe}_TIMESTAMP")
            continue

        if open_ms > decision_timestamp_ms or close_ms > decision_timestamp_ms:
            lookahead_count += 1
            if strict_validation:
                reasons.append(f"LOOKAHEAD_{timeframe}_CANDLE_REJECTED")
                reasons.append("LOOKAHEAD_REJECTED")
            continue

        if strict_validation and tf_ms in (3_600_000, 14_400_000) and (open_ms % tf_ms != 0):
            reasons.append(f"MISALIGNED_{timeframe}_UTC_BOUNDARY")
            continue

        if open_ms in seen_open_times:
            if strict_validation:
                reasons.append(f"DUPLICATE_{timeframe}_CANDLE_TIMESTAMP")
            continue
        if open_ms < last_open_ms:
            if strict_validation:
                reasons.append(f"OUT_OF_ORDER_{timeframe}_CANDLE_TIMESTAMP")
            continue

        norm_b = _normalize_candle_obj(b, symbol, timeframe, tf_ms)
        if isinstance(norm_b, dict):
            o_px = float(norm_b.get("open", 0.0) or 0.0)
            h_px = float(norm_b.get("high", 0.0) or 0.0)
            l_px = float(norm_b.get("low", 0.0) or 0.0)
            c_px = float(norm_b.get("close", 0.0) or 0.0)
        else:
            o_px = float(getattr(norm_b, "open", 0.0) or 0.0)
            h_px = float(getattr(norm_b, "high", 0.0) or 0.0)
            l_px = float(getattr(norm_b, "low", 0.0) or 0.0)
            c_px = float(getattr(norm_b, "close", 0.0) or 0.0)
        if l_px <= 0 or h_px < l_px or h_px < max(o_px, c_px) or l_px > min(o_px, c_px):
            if strict_validation:
                reasons.append(f"MALFORMED_{timeframe}_CANDLE_OHLC")
            continue

        seen_open_times.add(open_ms)
        last_open_ms = open_ms
        closed_out.append(norm_b)

    if not closed_out:
        if unclosed_count > 0:
            status = "UNCLOSED_REJECTED"
            reasons.append(f"UNCLOSED_{timeframe}_CANDLE_REJECTED")
        elif lookahead_count > 0:
            status = "LOOKAHEAD_REJECTED"
            reasons.append(f"LOOKAHEAD_{timeframe}_CANDLE_REJECTED")
        elif reasons:
            status = "INVALID_REJECTED"
        else:
            status = "MISSING"
    else:
        status = "AVAILABLE" if not reasons else "PARTIAL_REJECTED"

    return closed_out, status, reasons


def aggregate_closed_htf_candles(
    source_bars: Sequence[Any],
    *,
    target_timeframe: str,
    decision_timestamp_ms: int,
    symbol: str = "XAUUSDT",
    source_tf_ms: int = 60_000,
) -> List[Any]:
    """
    Phase G: Deterministic UTC-aligned closed-candle aggregation into 1H (3,600,000 ms)
    or 4H (14,400,000 ms) bars.
    """
    tf_upper = str(target_timeframe).upper()
    target_tf_ms = 14_400_000 if tf_upper in ("4H", "H4") else 3_600_000
    norm_tf = "4H" if target_tf_ms == 14_400_000 else "1H"

    buckets: Dict[int, List[Any]] = {}
    for b in source_bars or ():
        if not _candle_is_closed(b):
            continue
        open_ms = _candle_open_ms(b)
        close_ms = _candle_close_ms(b, source_tf_ms)
        if open_ms <= 0 or close_ms > decision_timestamp_ms:
            continue
        bucket_open = (open_ms // target_tf_ms) * target_tf_ms
        bucket_close = bucket_open + target_tf_ms
        if bucket_close > decision_timestamp_ms:
            continue
        norm_b = _normalize_candle_obj(b, symbol, norm_tf, source_tf_ms)
        buckets.setdefault(bucket_open, []).append(norm_b)

    aggregated: List[Any] = []
    for bucket_open in sorted(buckets.keys()):
        group = sorted(buckets[bucket_open], key=lambda x: int(x["open_time"]))
        if not group:
            continue
        bucket_close = bucket_open + target_tf_ms
        agg_bar = _DualAccessCandle({
            "symbol": symbol,
            "timeframe": norm_tf,
            "open_time": bucket_open,
            "open_time_ms": bucket_open,
            "timestamp": bucket_open,
            "close_time": bucket_close,
            "close_time_ms": bucket_close,
            "open": float(group[0]["open"]),
            "high": max(float(x["high"]) for x in group),
            "low": min(float(x["low"]) for x in group),
            "close": float(group[-1]["close"]),
            "volume": round(sum(float(x.get("volume", 0.0)) for x in group), 4),
            "is_closed": True,
            "source_bar_count": len(group),
        })
        aggregated.append(agg_bar)
    return aggregated


@dataclass(frozen=True)
class MarketDecisionContext:
    """
    Section 5: Immutable, deterministic, side-effect-free MarketDecisionContext.
    """
    symbol: str
    decision_timestamp_ms: int
    closed_m1_candles: Tuple[Any, ...]
    closed_m5_candles: Tuple[Any, ...]
    closed_m15_candles: Tuple[Any, ...] = ()
    closed_h1_candles: Tuple[Any, ...] = ()
    closed_h4_candles: Tuple[Any, ...] = ()
    closed_d1_candles: Tuple[Any, ...] = ()
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread: Optional[float] = None
    quote_timestamp_ms: Optional[int] = None
    atr_m1: Optional[float] = None
    atr_m5: Optional[float] = None
    atr_m1_obj: Any = None
    atr_m5_obj: Any = None
    htf_zones: Tuple[Any, ...] = ()
    liquidity_pools: Tuple[Any, ...] = ()
    model_enablement_flags: Tuple[Tuple[str, bool], ...] = ()
    policy_config_snapshot: Tuple[Tuple[str, Any], ...] = ()
    risk_config_snapshot: Tuple[Tuple[str, Any], ...] = ()
    cost_config_snapshot: Tuple[Tuple[str, Any], ...] = ()
    source_fingerprint: str = ""
    causal_valid: bool = True
    causal_rejection_reasons: Tuple[str, ...] = ()
    m1_status: str = "AVAILABLE"
    m5_status: str = "AVAILABLE"
    h1_status: str = "OPTIONAL_EMPTY"
    h4_status: str = "OPTIONAL_EMPTY"
    htf_bias: str = "UNKNOWN"
    h1_alignment: str = "UNKNOWN"
    htf_provenance: Tuple[Tuple[str, Any], ...] = ()
    session_restricted: bool = False
    session_restriction_reason: Optional[str] = None

    def is_model_enabled(self, model_id: str) -> bool:
        flags = dict(self.model_enablement_flags)
        return bool(flags.get(model_id, True))

    def get_policy_dict(self) -> Dict[str, Any]:
        return dict(self.policy_config_snapshot)

    def get_risk_dict(self) -> Dict[str, Any]:
        return dict(self.risk_config_snapshot)

    def get_cost_dict(self) -> Dict[str, Any]:
        return dict(self.cost_config_snapshot)

    def get_htf_provenance_dict(self) -> Dict[str, Any]:
        return dict(self.htf_provenance)

    def compute_market_input_hash(self) -> str:
        return _canonical_hash({
            "symbol": self.symbol,
            "decision_timestamp_ms": self.decision_timestamp_ms,
            "m1": _canonical_candle_series(self.closed_m1_candles, 60_000),
            "m5": _canonical_candle_series(self.closed_m5_candles, 300_000),
            "m15": _canonical_candle_series(self.closed_m15_candles, 900_000),
            "h1": _canonical_candle_series(self.closed_h1_candles, 3_600_000),
            "h4": _canonical_candle_series(self.closed_h4_candles, 14_400_000),
            "d1": _canonical_candle_series(self.closed_d1_candles, 86_400_000),
            "bid": self.bid,
            "ask": self.ask,
            "spread": self.spread,
            "quote_timestamp_ms": self.quote_timestamp_ms,
            "atr_m1": self.atr_m1,
            "atr_m5": self.atr_m5,
            "m1_status": self.m1_status,
            "m5_status": self.m5_status,
            "h1_status": self.h1_status,
            "h4_status": self.h4_status,
            "htf_bias": self.htf_bias,
            "h1_alignment": self.h1_alignment,
            "session_restricted": self.session_restricted,
            "session_restriction_reason": self.session_restriction_reason,
        })

    def compute_input_snapshot_hash(self) -> str:
        if self.source_fingerprint and len(str(self.source_fingerprint)) == 64:
            return str(self.source_fingerprint)
        return self.compute_market_input_hash()

    def compute_configuration_hash(self) -> str:
        return _canonical_hash({
            "policy": self.policy_config_snapshot,
            "risk": self.risk_config_snapshot,
            "cost": self.cost_config_snapshot,
        })


def build_market_decision_context(
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
    model_enablement: Optional[Mapping[str, bool]] = None,
    policy_config: Optional[Mapping[str, Any]] = None,
    risk_config: Optional[Mapping[str, Any]] = None,
    cost_config: Optional[Mapping[str, Any]] = None,
    strict_causal_validation: bool = False,
    strict_htf_validation: bool = False,
    htf_bias: Optional[str] = None,
    h1_alignment: Optional[str] = None,
    session_restricted: bool = False,
    session_restriction_reason: Optional[str] = None,
) -> MarketDecisionContext:
    """
    Constructs an immutable MarketDecisionContext, enforcing causal candle filtering
    and computing deterministic ATR and fingerprints.
    """
    raw_m1 = list(m1_candles or [])
    raw_m5 = list(m5_candles or [])
    raw_m15 = list(m15_candles or [])
    raw_h1 = list(h1_candles or [])
    raw_h4 = list(h4_candles or [])
    raw_d1 = list(d1_candles or [])

    rejection_reasons: List[str] = []
    m1_status = "AVAILABLE"
    m5_status = "AVAILABLE"

    closed_m1: List[Any] = []
    unclosed_m1_count = 0
    lookahead_m1_count = 0
    for b in raw_m1:
        if not _candle_is_closed(b):
            unclosed_m1_count += 1
            if strict_causal_validation:
                rejection_reasons.append("UNCLOSED_CANDLE")
            continue
        open_ms = _candle_open_ms(b)
        close_ms = _candle_close_ms(b, 60_000)
        if open_ms > decision_timestamp_ms or close_ms > decision_timestamp_ms:
            lookahead_m1_count += 1
            if strict_causal_validation:
                rejection_reasons.append("LOOKAHEAD_REJECTED")
            continue
        closed_m1.append(_normalize_candle_obj(b, symbol, '1M', 60_000))

    if not closed_m1:
        if unclosed_m1_count > 0:
            m1_status = "UNCLOSED_REJECTED"
            rejection_reasons.append("UNCLOSED_M1_CANDLE_REJECTED")
            rejection_reasons.append("UNCLOSED_CANDLE")
        elif lookahead_m1_count > 0:
            m1_status = "LOOKAHEAD_REJECTED"
            rejection_reasons.append("LOOKAHEAD_REJECTED")
        else:
            m1_status = "MISSING"
            rejection_reasons.append("MISSING_EVIDENCE")
            rejection_reasons.append("M1_BARS_EMPTY")

    closed_m5: List[Any] = []
    unclosed_m5_count = 0
    lookahead_m5_count = 0
    for b in raw_m5:
        if not _candle_is_closed(b):
            unclosed_m5_count += 1
            if strict_causal_validation:
                rejection_reasons.append("UNCLOSED_CANDLE")
            continue
        open_ms = _candle_open_ms(b)
        close_ms = _candle_close_ms(b, 300_000)
        if open_ms > decision_timestamp_ms or close_ms > decision_timestamp_ms:
            lookahead_m5_count += 1
            if strict_causal_validation:
                rejection_reasons.append("LOOKAHEAD_REJECTED")
            continue
        closed_m5.append(_normalize_candle_obj(b, symbol, '5M', 300_000))

    if not closed_m5:
        if unclosed_m5_count > 0:
            m5_status = "UNCLOSED_REJECTED"
            rejection_reasons.append("UNCLOSED_M5_CANDLE_REJECTED")
            rejection_reasons.append("UNCLOSED_CANDLE")
        elif lookahead_m5_count > 0:
            m5_status = "LOOKAHEAD_REJECTED"
            rejection_reasons.append("LOOKAHEAD_REJECTED")
        else:
            m5_status = "MISSING"

    closed_m15 = [
        _normalize_candle_obj(b, symbol, '15M', 900_000) for b in raw_m15
        if _candle_is_closed(b) and _candle_close_ms(b, 900_000) <= decision_timestamp_ms
    ]
    closed_h1, h1_status, h1_reasons = validate_htf_candle_series(
        raw_h1,
        symbol=symbol,
        timeframe="1H",
        tf_ms=3_600_000,
        decision_timestamp_ms=decision_timestamp_ms,
        strict_validation=strict_causal_validation or strict_htf_validation,
    )
    closed_h4, h4_status, h4_reasons = validate_htf_candle_series(
        raw_h4,
        symbol=symbol,
        timeframe="4H",
        tf_ms=14_400_000,
        decision_timestamp_ms=decision_timestamp_ms,
        strict_validation=strict_causal_validation or strict_htf_validation,
    )
    closed_d1, _, _ = validate_htf_candle_series(
        raw_d1,
        symbol=symbol,
        timeframe="1D",
        tf_ms=86_400_000,
        decision_timestamp_ms=decision_timestamp_ms,
        strict_validation=False,
    )
    if strict_causal_validation or strict_htf_validation:
        rejection_reasons.extend(h1_reasons)
        rejection_reasons.extend(h4_reasons)

    h4_source = "EXPLICIT_4H" if raw_h4 else "NONE"
    if not raw_h4 and closed_h1:
        agg_h4 = aggregate_closed_htf_candles(
            closed_h1,
            target_timeframe="4H",
            decision_timestamp_ms=decision_timestamp_ms,
            symbol=symbol,
            source_tf_ms=3_600_000,
        )
        if agg_h4:
            closed_h4 = agg_h4
            h4_status = "AGGREGATED_FROM_1H"
            h4_source = "AGGREGATED_FROM_1H"

    h1_source = "EXPLICIT_1H" if raw_h1 else "NONE"
    if not raw_h1 and closed_m15:
        agg_h1 = aggregate_closed_htf_candles(
            closed_m15,
            target_timeframe="1H",
            decision_timestamp_ms=decision_timestamp_ms,
            symbol=symbol,
            source_tf_ms=900_000,
        )
        if agg_h1:
            closed_h1 = agg_h1
            h1_status = "AGGREGATED_FROM_15M"
            h1_source = "AGGREGATED_FROM_15M"

    eff_bid = float(bid) if bid is not None else None
    eff_ask = float(ask) if ask is not None else None
    if (eff_bid is None or eff_ask is None) and closed_m1:
        last_b = closed_m1[-1]
        last_close = float(last_b.get("close", 0.0) if isinstance(last_b, dict) else getattr(last_b, "close", 0.0))
        if last_close > 0:
            eff_bid = round(last_close - 0.10, 2)
            eff_ask = round(last_close + 0.10, 2)

    spread: Optional[float] = None
    if eff_bid is not None and eff_ask is not None and eff_ask >= eff_bid > 0:
        spread = round(eff_ask - eff_bid, 4)
    else:
        rejection_reasons.append("MISSING_EVIDENCE")
        rejection_reasons.append("INVALID_QUOTE")

    atr_m1_obj = compute_stop_atr(
        closed_m1,
        period=14,
        timeframe="1M",
        as_of_ms=decision_timestamp_ms,
        symbol=symbol,
    )
    atr_m5_obj = compute_stop_atr(
        closed_m5,
        period=14,
        timeframe="5M",
        as_of_ms=decision_timestamp_ms,
        symbol=symbol,
    )
    atr_m1_val = (
        round(float(atr_m1_obj.value), 6)
        if atr_m1_obj is not None and getattr(atr_m1_obj, "status", "") == "AVAILABLE" and atr_m1_obj.value is not None
        else None
    )
    atr_m5_val = (
        round(float(atr_m5_obj.value), 6)
        if atr_m5_obj is not None and getattr(atr_m5_obj, "status", "") == "AVAILABLE" and atr_m5_obj.value is not None
        else None
    )

    resolved_zones: List[Any] = list(htf_zones) if htf_zones is not None else []
    resolved_pools: List[Any] = list(liquidity_pools) if liquidity_pools is not None else []
    if not resolved_pools and closed_m1:
        try:
            sh_1m, sl_1m = identify_confirmed_swings(
                closed_m1, timeframe="1M", left_bars=2, right_bars=2, as_of_ms=decision_timestamp_ms
            )
            sh_5m, sl_5m = (
                identify_confirmed_swings(
                    closed_m5, timeframe="5M", left_bars=1, right_bars=1, as_of_ms=decision_timestamp_ms
                )
                if len(closed_m5) >= 3
                else ([], [])
            )
            resolved_pools = list(
                build_liquidity_map(
                    symbol=symbol,
                    timeframe="1M",
                    swing_highs=sh_1m + sh_5m,
                    swing_lows=sl_1m + sl_5m,
                    now_ms=decision_timestamp_ms,
                )
            )
        except Exception:
            resolved_pools = []

    default_enablement = {m: True for m in ALL_EVALUATED_MODEL_IDS}
    if model_enablement:
        for k, v in model_enablement.items():
            default_enablement[str(k)] = bool(v)
    enablement_tuple = tuple(sorted(default_enablement.items()))

    default_policy = {
        "policy_version": "V17.9.0",
        "stop_policy_mode": StopPlacementMode.PAPER_STRUCTURAL.value,
        "min_net_rr": float(get_scalp_config().min_net_rr),
        "tick_size": 0.01,
        "strict_causal_binding": True,
    }
    if policy_config:
        default_policy.update(dict(policy_config))
    policy_tuple = tuple(sorted(default_policy.items()))

    default_risk = {
        "capital_usdt": 1000.0,
        "risk_pct": 0.25,
        "leverage": 20,
        "margin_mode": "ISOLATED",
        "min_net_rr": float(default_policy.get("min_net_rr", 1.2)),
    }
    if risk_config:
        default_risk.update(dict(risk_config))
    risk_tuple = tuple(sorted(default_risk.items()))

    default_cost = {
        "slippage_usd": 0.10,
        "maker_fee_rate": 0.0002,
        "taker_fee_rate": 0.0004,
    }
    if cost_config:
        default_cost.update(dict(cost_config))
    cost_tuple = tuple(sorted(default_cost.items()))

    dedup_reasons: List[str] = []
    for r in rejection_reasons:
        if r not in dedup_reasons:
            dedup_reasons.append(r)

    fp_payload = {
        "symbol": symbol,
        "decision_timestamp_ms": decision_timestamp_ms,
        "m1": _candle_summary(closed_m1, 60_000),
        "m5": _candle_summary(closed_m5, 300_000),
        "m15": _candle_summary(closed_m15, 900_000),
        "h1": _candle_summary(closed_h1, 3_600_000),
        "bid": eff_bid,
        "ask": eff_ask,
        "spread": spread,
        "atr_m1": atr_m1_val,
        "atr_m5": atr_m5_val,
        "enablement": enablement_tuple,
        "policy": policy_tuple,
        "risk": risk_tuple,
        "cost": cost_tuple,
    }
    source_fp = _canonical_hash(fp_payload)

    causal_valid = (
        m1_status == "AVAILABLE"
        and "UNCLOSED_CANDLE" not in dedup_reasons
        and "LOOKAHEAD_REJECTED" not in dedup_reasons
        and not any(
            "UNCLOSED_" in r or "LOOKAHEAD_" in r or "DUPLICATE_" in r or "OUT_OF_ORDER_" in r or "MALFORMED_" in r or "MISALIGNED_" in r
            for r in dedup_reasons
        )
        and spread is not None
    )

    resolved_htf_bias = str(htf_bias or default_policy.get("htf_bias") or "UNKNOWN")
    resolved_h1_align = str(h1_alignment or default_policy.get("h1_alignment") or "UNKNOWN")
    htf_prov_tuple = tuple(sorted({
        "h1_source": h1_source,
        "h4_source": h4_source,
        "h1_count": len(closed_h1),
        "h4_count": len(closed_h4),
        "d1_count": len(closed_d1),
        "h1_last_close_ms": _candle_close_ms(closed_h1[-1], 3_600_000) if closed_h1 else None,
        "h4_last_close_ms": _candle_close_ms(closed_h4[-1], 14_400_000) if closed_h4 else None,
        "h1_status": h1_status,
        "h4_status": h4_status,
    }.items()))

    return MarketDecisionContext(
        symbol=symbol,
        decision_timestamp_ms=int(decision_timestamp_ms),
        closed_m1_candles=tuple(closed_m1),
        closed_m5_candles=tuple(closed_m5),
        closed_m15_candles=tuple(closed_m15),
        closed_h1_candles=tuple(closed_h1),
        closed_h4_candles=tuple(closed_h4),
        closed_d1_candles=tuple(closed_d1),
        bid=eff_bid,
        ask=eff_ask,
        spread=spread,
        quote_timestamp_ms=int(quote_timestamp_ms or decision_timestamp_ms),
        atr_m1=atr_m1_val,
        atr_m5=atr_m5_val,
        atr_m1_obj=atr_m1_obj,
        atr_m5_obj=atr_m5_obj,
        htf_zones=tuple(resolved_zones),
        liquidity_pools=tuple(resolved_pools),
        model_enablement_flags=enablement_tuple,
        policy_config_snapshot=policy_tuple,
        risk_config_snapshot=risk_tuple,
        cost_config_snapshot=cost_tuple,
        source_fingerprint=source_fp,
        causal_valid=causal_valid,
        causal_rejection_reasons=tuple(dedup_reasons),
        m1_status=m1_status,
        m5_status=m5_status,
        h1_status=h1_status,
        h4_status=h4_status,
        htf_bias=resolved_htf_bias,
        h1_alignment=resolved_h1_align,
        htf_provenance=htf_prov_tuple,
        session_restricted=bool(session_restricted or default_policy.get("session_restricted", False)),
        session_restriction_reason=session_restriction_reason or default_policy.get("session_restriction_reason"),
    )


@dataclass(frozen=True)
class StrategyDecisionSnapshot:
    """
    Section 6 / Phase C: Immutable, side-effect-free StrategyDecisionSnapshot (DecisionResult).
    """
    model_id: str
    symbol: str
    decision_timestamp_ms: int
    direction: str
    setup_validity: str
    entry_reference: Optional[float]
    entry_zone_low: Optional[float]
    entry_zone_high: Optional[float]
    selected_fvg_id: Optional[str]
    selected_break_id: Optional[str]
    selected_sweep_id: Optional[str]
    setup_episode_id: Optional[str]
    unicorn_validity: str
    unicorn_failure_stage: Optional[str]
    selected_anchor_id: Optional[str]
    anchor_kind: Optional[str]
    structural_invalidation_price: Optional[float]
    buffer_value: Optional[float]
    selected_sl: Optional[float]
    legacy_sl: Optional[float]
    take_profit: Optional[float]
    selected_target_id: Optional[str]
    atr_m1: Optional[float]
    atr_m5: Optional[float]
    spread: Optional[float]
    hypothetical_quantity: Optional[float]
    stop_distance_price: Optional[float]
    target_distance_price: Optional[float]
    gross_rr: Optional[float]
    net_rr: Optional[float]
    decision_status: str
    rejection_reason_codes: Tuple[str, ...]
    evidence_fingerprint: str
    policy_fingerprint: str
    decision_fingerprint: str
    adapter_origin: str = "SHARED_EVALUATOR"
    raw_candidate_reason_code: Optional[str] = None
    candidate_key: Optional[str] = None
    unicorn_evidence_dict: Optional[Dict[str, Any]] = None
    stop_comparison_plan_dict: Optional[Dict[str, Any]] = None
    unicorn_sl_candidate: Optional[float] = None
    protective_distance: Optional[float] = None
    selected_breaker_id: Optional[str] = None
    evaluator_version: str = EVALUATOR_VERSION
    custom_decision_id: Optional[str] = None
    production_candidate_payload: Optional[Any] = None

    @property
    def decision_id(self) -> str:
        if self.custom_decision_id is not None:
            return str(self.custom_decision_id)
        if not self.decision_fingerprint or not self.model_id or not self.symbol:
            return ""
        return f"dec_{self.model_id}_{self.symbol}_{self.direction}_{self.decision_timestamp_ms}_{self.decision_fingerprint[:12]}"

    @property
    def strategy_decision_status(self) -> str:
        return "APPROVE" if self.decision_status == DecisionStatus.ELIGIBLE.value else "REJECT"

    @property
    def evaluation_timestamp(self) -> int:
        return self.decision_timestamp_ms

    @property
    def entry(self) -> Optional[float]:
        return self.entry_reference

    @property
    def stop_loss(self) -> Optional[float]:
        return self.selected_sl

    @property
    def effective_direction(self) -> str:
        return self.direction if self.decision_status == DecisionStatus.ELIGIBLE.value else "NO_TRADE"

    @property
    def risk_or_sizing_intent(self) -> Dict[str, Any]:
        return {
            "hypothetical_quantity": self.hypothetical_quantity,
            "gross_rr": self.gross_rr,
            "net_rr": self.net_rr,
            "stop_distance_price": self.stop_distance_price,
            "target_distance_price": self.target_distance_price,
        }

    @property
    def reason_codes(self) -> Tuple[str, ...]:
        return self.rejection_reason_codes if self.rejection_reason_codes else ("ELIGIBLE",)

    @property
    def input_snapshot_hash(self) -> str:
        return self.evidence_fingerprint

    @property
    def market_input_hash(self) -> str:
        return self.evidence_fingerprint

    @property
    def configuration_hash(self) -> str:
        return self.policy_fingerprint

    def to_parity_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "symbol": self.symbol,
            "decision_timestamp_ms": self.decision_timestamp_ms,
            "direction": self.direction,
            "setup_validity": self.setup_validity,
            "entry_reference": self.entry_reference,
            "entry_zone_low": self.entry_zone_low,
            "entry_zone_high": self.entry_zone_high,
            "selected_fvg_id": self.selected_fvg_id,
            "selected_break_id": self.selected_break_id,
            "selected_sweep_id": self.selected_sweep_id,
            "setup_episode_id": self.setup_episode_id,
            "unicorn_validity": self.unicorn_validity,
            "unicorn_failure_stage": self.unicorn_failure_stage,
            "selected_anchor_id": self.selected_anchor_id,
            "anchor_kind": self.anchor_kind,
            "structural_invalidation_price": self.structural_invalidation_price,
            "buffer_value": self.buffer_value,
            "selected_sl": self.selected_sl,
            "legacy_sl": self.legacy_sl,
            "take_profit": self.take_profit,
            "selected_target_id": self.selected_target_id,
            "atr_m1": self.atr_m1,
            "atr_m5": self.atr_m5,
            "spread": self.spread,
            "hypothetical_quantity": self.hypothetical_quantity,
            "stop_distance_price": self.stop_distance_price,
            "target_distance_price": self.target_distance_price,
            "gross_rr": self.gross_rr,
            "net_rr": self.net_rr,
            "decision_status": self.decision_status,
            "rejection_reason_codes": list(self.rejection_reason_codes),
            "evidence_fingerprint": self.evidence_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "input_snapshot_hash": self.input_snapshot_hash,
            "market_input_hash": self.market_input_hash,
            "configuration_hash": self.configuration_hash,
            "decision_fingerprint": self.decision_fingerprint,
            "decision_id": self.decision_id,
            "evaluator_version": self.evaluator_version,
            "adapter_origin": self.adapter_origin,
        }


DecisionResult = StrategyDecisionSnapshot


def _compute_snapshot_fingerprints(
    *,
    context: MarketDecisionContext,
    model_id: str,
    direction: str,
    setup_validity: str,
    entry_reference: Optional[float],
    selected_fvg_id: Optional[str],
    selected_break_id: Optional[str],
    selected_sweep_id: Optional[str],
    setup_episode_id: Optional[str],
    unicorn_validity: str,
    selected_anchor_id: Optional[str],
    structural_invalidation_price: Optional[float],
    selected_sl: Optional[float],
    take_profit: Optional[float],
    hypothetical_quantity: Optional[float],
    net_rr: Optional[float],
    decision_status: str,
    rejection_reason_codes: Sequence[str],
) -> Tuple[str, str, str]:
    evidence_fp = _canonical_hash({
        "source_fingerprint": context.source_fingerprint,
        "model_id": model_id,
        "direction": direction,
        "selected_fvg_id": selected_fvg_id,
        "selected_break_id": selected_break_id,
        "selected_sweep_id": selected_sweep_id,
        "setup_episode_id": setup_episode_id,
        "unicorn_validity": unicorn_validity,
    })
    policy_fp = _canonical_hash({
        "policy": context.policy_config_snapshot,
        "risk": context.risk_config_snapshot,
        "cost": context.cost_config_snapshot,
    })
    decision_fp = _canonical_hash({
        "model_id": model_id,
        "direction": direction,
        "setup_validity": setup_validity,
        "entry_reference": entry_reference,
        "selected_fvg_id": selected_fvg_id,
        "selected_break_id": selected_break_id,
        "selected_sweep_id": selected_sweep_id,
        "setup_episode_id": setup_episode_id,
        "unicorn_validity": unicorn_validity,
        "selected_anchor_id": selected_anchor_id,
        "structural_invalidation_price": structural_invalidation_price,
        "selected_sl": selected_sl,
        "take_profit": take_profit,
        "atr_m1": context.atr_m1,
        "atr_m5": context.atr_m5,
        "spread": context.spread,
        "hypothetical_quantity": hypothetical_quantity,
        "net_rr": net_rr,
        "decision_status": decision_status,
        "rejection_reason_codes": list(rejection_reason_codes),
    })
    return evidence_fp, policy_fp, decision_fp


def validate_canonical_executability(
    decision: Any,
    context: Optional[MarketDecisionContext] = None,
    *,
    expected_direction: Optional[str] = None,
    strict_direction_match: bool = False,
) -> Tuple[bool, Optional[str]]:
    """
    V17.9.0.1.2 Phase B (Section 3): Strict positive eligibility invariant.
    A decision is executable if and only if ALL positive conditions hold:
      1. decision is a StrategyDecisionSnapshot
      2. Complete non-empty canonical lineage (decision_id, decision_fingerprint,
         input_snapshot_hash, configuration_hash, evaluator_version)
      3. Point-in-time market data context is valid (when context is provided)
      4. decision.decision_status == DecisionStatus.ELIGIBLE.value (exact positive equality)
      5. decision.setup_validity == "VALID"
      6. len(decision.rejection_reason_codes) == 0
      7. decision.direction in ("LONG", "SHORT") and matches expected_direction when strict
      8. entry_reference, selected_sl, take_profit are finite positive numbers with valid
         directional geometry (SL < entry < TP for LONG; TP < entry < SL for SHORT)
      9. hypothetical_quantity > 0 and net_rr > 0
    Any other status (including NO_TRADE, REJECTED, VALID_SETUP_REJECTED_BY_RISK,
    NO_VALID_SETUP, WAITING_CONFIRMATION, MODEL_DISABLED, MISSING_MARKET_DATA,
    unknown strings, None, or empty string) is strictly non-executable.
    """
    import math

    if not isinstance(decision, StrategyDecisionSnapshot):
        return False, "CANONICAL_DECISION_INVALID_TYPE_FAIL_CLOSED"

    has_valid_lineage = bool(
        decision.decision_id
        and decision.decision_fingerprint
        and decision.input_snapshot_hash
        and decision.configuration_hash
        and decision.evaluator_version
    )
    if not has_valid_lineage:
        return False, "CANONICAL_DECISION_LINEAGE_MISSING"

    if context is not None:
        if not bool(getattr(context, "causal_valid", False)) or len(getattr(context, "causal_rejection_reasons", ()) or ()) > 0:
            c_reasons = getattr(context, "causal_rejection_reasons", ()) or ()
            return False, str(c_reasons[0]) if c_reasons else "CANONICAL_CONTEXT_CAUSAL_INVALID"
        c_spread = getattr(context, "spread", None)
        if c_spread is None or isinstance(c_spread, bool) or not math.isfinite(float(c_spread)) or float(c_spread) < 0.0:
            return False, "CANONICAL_CONTEXT_MISSING_SPREAD"

    if decision.decision_status != DecisionStatus.ELIGIBLE.value:
        rej = (
            str(decision.rejection_reason_codes[0])
            if decision.rejection_reason_codes
            else str(decision.raw_candidate_reason_code or decision.decision_status or "CANONICAL_DECISION_NOT_ELIGIBLE")
        )
        return False, rej

    if decision.setup_validity != "VALID":
        return False, "CANONICAL_SETUP_NOT_VALID"

    if len(decision.rejection_reason_codes or ()) > 0:
        return False, str(decision.rejection_reason_codes[0]) or "CANONICAL_REJECTION_REASONS_PRESENT"

    if decision.direction not in ("LONG", "SHORT") or (
        strict_direction_match
        and expected_direction in ("LONG", "SHORT")
        and decision.direction != expected_direction
    ):
        return False, "CANONICAL_DIRECTION_MISMATCH_REJECTED"

    ent_val = decision.entry_reference
    sl_val = decision.selected_sl
    tp_val = decision.take_profit
    coords_finite = (
        ent_val is not None
        and sl_val is not None
        and tp_val is not None
        and not isinstance(ent_val, bool)
        and not isinstance(sl_val, bool)
        and not isinstance(tp_val, bool)
        and math.isfinite(float(ent_val))
        and math.isfinite(float(sl_val))
        and math.isfinite(float(tp_val))
        and float(ent_val) > 0.0
        and float(sl_val) > 0.0
        and float(tp_val) > 0.0
    )
    if not coords_finite:
        return False, "CANONICAL_SL_TP_MISSING_REJECTED"

    ent_f = float(ent_val)
    sl_f = float(sl_val)
    tp_f = float(tp_val)
    geom_ok = (
        (decision.direction == "LONG" and sl_f < ent_f < tp_f)
        or (decision.direction == "SHORT" and tp_f < ent_f < sl_f)
    )
    if not geom_ok:
        return False, "CANONICAL_SL_TP_GEOMETRY_REJECTED"

    qty_val = decision.hypothetical_quantity
    if (
        qty_val is None
        or isinstance(qty_val, bool)
        or not math.isfinite(float(qty_val))
        or float(qty_val) <= 0.0
    ):
        return False, "CANONICAL_INVALID_HYPOTHETICAL_QUANTITY"

    nrr_val = decision.net_rr
    if (
        nrr_val is None
        or isinstance(nrr_val, bool)
        or not math.isfinite(float(nrr_val))
        or float(nrr_val) <= 0.0
    ):
        return False, "CANONICAL_INVALID_NET_RR"

    return True, None


def valid_decision_lineage(decision: Any, context: Optional[MarketDecisionContext] = None) -> bool:
    if not isinstance(decision, StrategyDecisionSnapshot):
        return False
    return bool(
        decision.decision_id
        and decision.decision_fingerprint
        and decision.input_snapshot_hash
        and decision.configuration_hash
        and decision.evaluator_version
    )


def valid_direction_entry_sl_tp(
    decision: Any,
    *,
    expected_direction: Optional[str] = None,
    strict_direction_match: bool = False,
) -> bool:
    import math

    if not isinstance(decision, StrategyDecisionSnapshot):
        return False
    if decision.direction not in ("LONG", "SHORT"):
        return False
    if strict_direction_match and expected_direction in ("LONG", "SHORT") and decision.direction != expected_direction:
        return False
    ent_val = decision.entry_reference
    sl_val = decision.selected_sl
    tp_val = decision.take_profit
    if (
        ent_val is None
        or sl_val is None
        or tp_val is None
        or isinstance(ent_val, bool)
        or isinstance(sl_val, bool)
        or isinstance(tp_val, bool)
        or not math.isfinite(float(ent_val))
        or not math.isfinite(float(sl_val))
        or not math.isfinite(float(tp_val))
        or float(ent_val) <= 0.0
        or float(sl_val) <= 0.0
        or float(tp_val) <= 0.0
    ):
        return False
    ent_f = float(ent_val)
    sl_f = float(sl_val)
    tp_f = float(tp_val)
    return bool(
        (decision.direction == "LONG" and sl_f < ent_f < tp_f)
        or (decision.direction == "SHORT" and tp_f < ent_f < sl_f)
    )


def valid_required_risk_intent(decision: Any) -> bool:
    import math

    if not isinstance(decision, StrategyDecisionSnapshot):
        return False
    qty_val = decision.hypothetical_quantity
    nrr_val = decision.net_rr
    if (
        qty_val is None
        or isinstance(qty_val, bool)
        or not math.isfinite(float(qty_val))
        or float(qty_val) <= 0.0
    ):
        return False
    if (
        nrr_val is None
        or isinstance(nrr_val, bool)
        or not math.isfinite(float(nrr_val))
        or float(nrr_val) <= 0.0
    ):
        return False
    return True


def valid_point_in_time_inputs(context: Optional[MarketDecisionContext]) -> bool:
    import math

    if context is None:
        return True
    if not bool(getattr(context, "causal_valid", False)) or len(getattr(context, "causal_rejection_reasons", ()) or ()) > 0:
        return False
    c_spread = getattr(context, "spread", None)
    if c_spread is None or isinstance(c_spread, bool) or not math.isfinite(float(c_spread)) or float(c_spread) < 0.0:
        return False
    return True


def canonical_is_executable(
    decision: Any,
    context: Optional[MarketDecisionContext] = None,
    *,
    expected_direction: Optional[str] = None,
    strict_direction_match: bool = False,
) -> bool:
    """
    Boolean wrapper around `validate_canonical_executability`.
    """
    ok, _ = validate_canonical_executability(
        decision,
        context=context,
        expected_direction=expected_direction,
        strict_direction_match=strict_direction_match,
    )
    return ok


class SharedStrategyEvaluator:
    """
    Section 7: Shared side-effect-free strategy evaluator used across Production,
    MARKET_SHADOW, and Historical Backtest.
    """

    _invocation_counts: Dict[str, int] = {}

    @classmethod
    def record_runtime_invocation(cls, adapter_origin: str) -> None:
        key = str(adapter_origin or "SHARED_EVALUATOR")
        cls._invocation_counts[key] = cls._invocation_counts.get(key, 0) + 1
        cls._invocation_counts["TOTAL"] = cls._invocation_counts.get("TOTAL", 0) + 1

    @classmethod
    def get_runtime_invocation_counts(cls) -> Dict[str, int]:
        return dict(cls._invocation_counts)

    @classmethod
    def reset_runtime_invocation_counts(cls) -> None:
        cls._invocation_counts.clear()

    @staticmethod
    def _build_default_watched_zone(
        symbol: str,
        direction: str,
        closed_1m: Sequence[Any],
        now_ms: int,
    ) -> WatchedZone:
        if closed_1m:
            lows = [float(b.get("low") if isinstance(b, dict) else getattr(b, "low", 0.0)) for b in closed_1m[-15:]]
            highs = [float(b.get("high") if isinstance(b, dict) else getattr(b, "high", 0.0)) for b in closed_1m[-15:]]
            if direction == "LONG":
                bot = min(lows)
                top = round(bot + max(0.5, (max(highs) - bot) * 0.25), 2)
            else:
                top = max(highs)
                bot = round(top - max(0.5, (top - min(lows)) * 0.25), 2)
        else:
            bot, top = 2640.0, 2642.0
        return WatchedZone(
            zone_id=f"shared_zone_{direction}_{now_ms}",
            symbol=symbol,
            timeframe="15M",
            zone_type="OB",
            direction="BULLISH" if direction == "LONG" else "BEARISH",
            top=round(top, 2),
            bottom=round(bot, 2),
            created_at=max(0, now_ms - 900_000),
            known_at=max(0, now_ms - 300_000),
        )

    @classmethod
    def _evaluate_raw_production_candidate(
        cls,
        context: MarketDecisionContext,
        model_id: str,
        direction: str,
        cfg: Optional[M1ScalpConfig] = None,
        cg01_cfg: Optional[CG01Config] = None,
    ) -> Tuple[Optional[ScalpCandidate], Optional[Any], Dict[str, Any]]:
        """
        Invokes the true authoritative production model evaluators from
        ScalpM1ModelService or cg01_engine.evaluate_cg01_candidate.
        """
        m1_cfg = cfg or get_scalp_config()
        closed_1m = list(context.closed_m1_candles)
        closed_5m = list(context.closed_m5_candles)
        closed_15m = list(context.closed_m15_candles)
        pools = list(context.liquidity_pools)
        zones = list(context.htf_zones)
        now_ms = context.decision_timestamp_ms
        bid = float(context.bid or 0.0)
        ask = float(context.ask or 0.0)

        mid_px = round((bid + ask) / 2.0, 2) if (bid > 0 and ask >= bid) else 0.0
        quote_obj = CanonicalQuote(
            symbol=context.symbol,
            last=mid_px,
            bid=bid,
            ask=ask,
            mark_price=mid_px,
            spread=round(ask - bid, 4) if ask >= bid else 0.0,
            exchange_ts_ms=context.quote_timestamp_ms or now_ms,
            received_at_ms=now_ms,
            source="SHARED_EVALUATOR",
            connection_epoch=1,
            status="VALID" if (bid > 0 and ask >= bid) else "MALFORMED",
            freshness_sec=0.1,
        )

        risk_dict = context.get_risk_dict()
        cost_dict = context.get_cost_dict()
        policy_settings = {
            "capital_usdt": float(risk_dict.get("capital_usdt", 1000.0)),
            "risk_pct": float(risk_dict.get("risk_pct", 0.25)),
            "leverage": int(risk_dict.get("leverage", 20)),
            "margin_mode": str(risk_dict.get("margin_mode", "ISOLATED")),
            "min_net_rr": float(risk_dict.get("min_net_rr", m1_cfg.min_net_rr)),
            "costs": CostAssumptions(
                slippage_usd=float(cost_dict.get("slippage_usd", 0.10)),
                maker_fee_rate=float(cost_dict.get("maker_fee_rate", 0.0002)),
                taker_fee_rate=float(cost_dict.get("taker_fee_rate", 0.0004)),
            ),
        }
        stop_policy = StopPlacementPolicy(
            mode=StopPlacementMode.PAPER_STRUCTURAL.value,
            v18_structural_intelligence_enabled=True,
            v18_unicorn_detection_enabled=True,
            v18_noise_research_enabled=True,
        )
        stop_context = {
            "closed_5m": closed_5m,
            "atr_m1_obj": context.atr_m1_obj,
            "atr_m5_obj": context.atr_m5_obj,
        }
        htf_bias = "BULLISH" if direction == "LONG" else "BEARISH"
        h1_alignment = htf_bias

        if model_id == ModelIdentifier.SMC_M1_REVERSAL.value:
            matching_zones = [
                z for z in zones
                if getattr(z, "direction", "") in (("BULLISH", "LONG") if direction == "LONG" else ("BEARISH", "SHORT"))
            ]
            zone = matching_zones[0] if matching_zones else cls._build_default_watched_zone(
                context.symbol, direction, closed_1m, now_ms
            )
            cand = ScalpM1ModelService.evaluate_m1_reversal_candidate(
                zone=zone,
                candles_1m=closed_1m,
                current_quote=quote_obj,
                policy_settings=policy_settings,
                now_ms=now_ms,
                config=m1_cfg,
                context_levels=pools,
                stop_policy=stop_policy,
                stop_context=stop_context,
            )
            return cand, None, {}

        if model_id == ModelIdentifier.SMC_M1_CONTINUATION.value:
            cand = ScalpM1ModelService.evaluate_m1_continuation_candidate(
                candles_1m=closed_1m,
                candles_5m=closed_5m,
                current_quote=quote_obj,
                htf_bias=htf_bias,
                h1_alignment=h1_alignment,
                policy_settings=policy_settings,
                now_ms=now_ms,
                config=m1_cfg,
                context_levels=pools,
                stop_policy=stop_policy,
                stop_context=stop_context,
            )
            return cand, None, {}

        if model_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value:
            cand = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                closed_1m=closed_1m,
                closed_5m=closed_5m,
                quote=quote_obj,
                htf_bias=htf_bias,
                h1_alignment=h1_alignment,
                policy_settings=policy_settings,
                now_ms=now_ms,
                config=m1_cfg,
                context_levels=pools,
                stop_policy=stop_policy,
                stop_context=stop_context,
            )
            return cand, None, {}

        if model_id == ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value:
            clusters = build_liquidity_clusters(
                levels=pools,
                tolerance_price=max(0.25, 0.25 * (context.atr_m1 or 1.0)),
                now_ms=now_ms,
                timeframe="1M",
                symbol=context.symbol,
            )
            target_side = "SELL_SIDE" if direction == "LONG" else "BUY_SIDE"
            matching_clusters = [c for c in clusters if getattr(c, "side", "") == target_side]
            cluster = matching_clusters[-1] if matching_clusters else (clusters[-1] if clusters else None)
            cand = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
                closed_1m=closed_1m,
                cluster=cluster,
                quote=quote_obj,
                policy_settings=policy_settings,
                now_ms=now_ms,
                config=m1_cfg,
                context_levels=pools,
                stop_policy=stop_policy,
                stop_context=stop_context,
            )
            return cand, None, {}

        if model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value:
            c_cfg = cg01_cfg or CG01Config()
            m15_for_cg01 = closed_15m if len(closed_15m) >= 5 else (closed_5m if len(closed_5m) >= 5 else closed_1m)
            m5_for_cg01 = closed_5m if len(closed_5m) >= 5 else closed_1m
            h1_for_cg01 = list(context.closed_h1_candles) or m15_for_cg01
            h4_for_cg01 = list(context.closed_h4_candles) or h1_for_cg01
            try:
                cg_ctx = {
                    "1M": closed_1m,
                    "5M": m5_for_cg01,
                    "15M": m15_for_cg01,
                    "1H": h1_for_cg01,
                    "4H": h4_for_cg01,
                    "1D": list(context.closed_d1_candles),
                }
                cg_res = evaluate_cg01_candidate(
                    context=cg_ctx,
                    current_quote={"bid": bid, "ask": ask, "timestamp": context.quote_timestamp_ms or now_ms},
                    config=c_cfg,
                    now_ms=now_ms,
                    capital=float(risk_dict.get("capital_usdt", 1000.0)),
                    risk_pct=float(risk_dict.get("risk_pct", 0.25)),
                )
                return None, cg_res, {"cg01_context": cg_ctx}
            except Exception as exc:
                return None, None, {"cg01_error": str(exc)}

        return None, None, {}

    @staticmethod
    def _extract_causal_setup_descriptors(
        *,
        symbol: str,
        model_id: str,
        direction: str,
        closed_1m: Sequence[Any],
        sh_1m: List[Dict[str, Any]],
        sl_1m: List[Dict[str, Any]],
        cfg: M1ScalpConfig,
        cutoff_ms: int,
    ) -> Dict[str, Any]:
        """
        Extracts causal FVG/Break/Sweep descriptors from confirmed M1 bars so that
        detect_unicorn_evidence and resolve_stop_anchor receive exact causal bindings.
        """
        if len(closed_1m) < 10 or not sh_1m or not sl_1m:
            return {"has_setup": False, "reason_code": "WAITING_M1_STRUCTURE"}

        fvgs = smc_engine.detect_fvgs(list(closed_1m), timeframe="1M")
        valid_fvgs = [
            f for f in fvgs
            if isinstance(f, dict)
            and int(f.get("confirmed_at") or f.get("timestamp") or 0) <= cutoff_ms
            and float(f.get("bottom", 0.0)) < float(f.get("top", 0.0))
            and str(f.get("state", "confirmed")).lower() in ("confirmed", "partially_mitigated")
        ]

        if model_id in (
            ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
            ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
            "UNICORN_BREAKER_FVG",
        ):
            target_fvg_type = "BULLISH_FVG" if direction == "LONG" else "BEARISH_FVG"
            dir_fvgs = [f for f in valid_fvgs if str(f.get("type", "")).upper() == target_fvg_type]
            if not dir_fvgs:
                return {"has_setup": False, "reason_code": "NO_VALID_FVG_SETUP"}
            sel_fvg = dir_fvgs[-1]
            f_bot = float(sel_fvg["bottom"])
            f_top = float(sel_fvg["top"])
            last_2_open = _candle_open_ms(closed_1m[-2])
            f_ts = int(sel_fvg.get("timestamp") or sel_fvg.get("middle_bar_ms") or last_2_open)
            f_conf = int(sel_fvg.get("confirmed_at") or (f_ts + 60_000))
            f_id = str(sel_fvg.get("id") or f"fvg_{direction}_{f_ts}")

            ref_swings = sh_1m if direction == "LONG" else sl_1m
            orig_swings = sl_1m if direction == "LONG" else sh_1m
            swing_prefix = "sh" if direction == "LONG" else "sl"

            causal_breakers = detect_breaker_blocks(
                list(closed_1m),
                symbol=symbol,
                direction=direction,
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
                return {"has_setup": False, "reason_code": "WAITING_M1_STRUCTURE"}

            broken_swing_id_str = (
                str(matched_breaker.broken_swing_id)
                if (matched_breaker is not None and matched_breaker.broken_swing_id)
                else str(ref_swing.get("id") or f"{swing_prefix}_{ref_swing.get('occurred_at_ms')}")
            )
            eff_break_open_ms = int(matched_breaker.break_bar_open_ms) if matched_breaker is not None else f_ts
            eff_break_known_ms = int(matched_breaker.confirmed_at_ms) if matched_breaker is not None else f_conf

            legacy_sl = (
                round(f_bot - cfg.sl_buffer_ticks * cfg.tick_size, 2)
                if direction == "LONG"
                else round(f_top + cfg.sl_buffer_ticks * cfg.tick_size, 2)
            )
            sel_fvg_desc = {
                "id": f_id,
                "type": target_fvg_type,
                "direction": direction,
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
                "break_id": f"brk_{direction}_{eff_break_open_ms}",
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
                "direction": direction,
                "legacy_sl": legacy_sl,
                "episode_id": f"ep_{model_id}_{direction}_{f_ts}",
                "selected_fvg": sel_fvg_desc,
                "selected_break": sel_break_desc,
                "selected_sweep": sel_sweep,
                "selected_reference": ref_swing,
                "entry_zone_low": round(f_bot, 2),
                "entry_zone_high": round(f_top, 2),
            }

        if model_id == ModelIdentifier.SMC_M1_CONTINUATION.value:
            last_sh = sh_1m[-1]
            last_sl = sl_1m[-1]
            last_bar = closed_1m[-1]
            last_close = float(last_bar.get("close") if isinstance(last_bar, dict) else getattr(last_bar, "close", 0.0))
            last_open_ms = _candle_open_ms(last_bar)
            last_close_ms = _candle_close_ms(last_bar, 60_000)
            if direction == "LONG" and last_close > float(last_sh["price"]):
                legacy_sl = round(float(last_sl["price"]) - cfg.sl_buffer_ticks * cfg.tick_size, 2)
                orig, ref = last_sl, last_sh
            elif direction == "SHORT" and last_close < float(last_sl["price"]):
                legacy_sl = round(float(last_sh["price"]) + cfg.sl_buffer_ticks * cfg.tick_size, 2)
                orig, ref = last_sh, last_sl
            else:
                return {"has_setup": False, "reason_code": "WAITING_M1_BOS"}
            return {
                "has_setup": True,
                "direction": direction,
                "legacy_sl": legacy_sl,
                "episode_id": f"ep_cont_{direction}_{last_open_ms}",
                "selected_fvg": None,
                "selected_break": {
                    "break_id": f"bos_{direction}_{last_open_ms}",
                    "break_open_ms": last_open_ms,
                    "broken_at_ms": last_open_ms,
                    "known_at_ms": last_close_ms,
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
                "entry_zone_low": None,
                "entry_zone_high": None,
            }

        # SMC_M1_REVERSAL or SMC_M1_SWEEP_RECLAIM
        last_sh = sh_1m[-1]
        last_sl = sl_1m[-1]
        recent_bars = closed_1m[-5:]
        lows = [float(b.get("low") if isinstance(b, dict) else getattr(b, "low", 0.0)) for b in recent_bars]
        highs = [float(b.get("high") if isinstance(b, dict) else getattr(b, "high", 0.0)) for b in recent_bars]
        last_close = float(closed_1m[-1].get("close") if isinstance(closed_1m[-1], dict) else getattr(closed_1m[-1], "close", 0.0))
        swept_low = min(lows)
        swept_high = max(highs)
        if direction == "LONG" and swept_low <= float(last_sl["price"]) and last_close > float(last_sl["price"]):
            legacy_sl = round(swept_low - cfg.sl_buffer_ticks * cfg.tick_size, 2)
            sweep_px = swept_low
            sweep_ts = min(_candle_open_ms(b) for b, l_val in zip(recent_bars, lows) if l_val == swept_low)
        elif direction == "SHORT" and swept_high >= float(last_sh["price"]) and last_close < float(last_sh["price"]):
            legacy_sl = round(swept_high + cfg.sl_buffer_ticks * cfg.tick_size, 2)
            sweep_px = swept_high
            sweep_ts = min(_candle_open_ms(b) for b, h_val in zip(recent_bars, highs) if h_val == swept_high)
        else:
            target_fvg_type = "BULLISH_FVG" if direction == "LONG" else "BEARISH_FVG"
            dir_fvgs = [f for f in valid_fvgs if str(f.get("type", "")).upper() == target_fvg_type]
            if model_id == ModelIdentifier.SMC_M1_REVERSAL.value and dir_fvgs and len(closed_1m) >= 15:
                fvg = dir_fvgs[-1]
                all_lows = [float(b.get("low") if isinstance(b, dict) else getattr(b, "low", 0.0)) for b in closed_1m[-18:]]
                all_highs = [float(b.get("high") if isinstance(b, dict) else getattr(b, "high", 0.0)) for b in closed_1m[-18:]]
                min_sw_low = min(float(s["price"]) for s in sl_1m)
                max_sw_high = max(float(s["price"]) for s in sh_1m)
                if direction == "LONG" and min(all_lows) <= min_sw_low and last_close > min_sw_low:
                    swept_low = min(all_lows)
                    legacy_sl = round(swept_low - cfg.sl_buffer_ticks * cfg.tick_size, 2)
                    sweep_px = swept_low
                    sweep_ts = min(_candle_open_ms(b) for b, l_val in zip(closed_1m[-18:], all_lows) if l_val == swept_low)
                    return {
                        "has_setup": True,
                        "direction": direction,
                        "legacy_sl": legacy_sl,
                        "episode_id": f"ep_{model_id}_{direction}_{sweep_ts}",
                        "selected_fvg": fvg,
                        "selected_break": None,
                        "selected_sweep": {
                            "id": f"sweep_{model_id}_{sweep_ts}",
                            "price": sweep_px,
                            "occurred_at_ms": sweep_ts,
                            "known_at_ms": sweep_ts + 60_000,
                        },
                        "selected_reference": min(sl_1m, key=lambda s: float(s["price"])),
                        "entry_zone_low": round(float(fvg["bottom"]), 2),
                        "entry_zone_high": round(float(fvg["top"]), 2),
                    }
                if direction == "SHORT" and max(all_highs) >= max_sw_high and last_close < max_sw_high:
                    swept_high = max(all_highs)
                    legacy_sl = round(swept_high + cfg.sl_buffer_ticks * cfg.tick_size, 2)
                    sweep_px = swept_high
                    sweep_ts = min(_candle_open_ms(b) for b, h_val in zip(closed_1m[-18:], all_highs) if h_val == swept_high)
                    return {
                        "has_setup": True,
                        "direction": direction,
                        "legacy_sl": legacy_sl,
                        "episode_id": f"ep_{model_id}_{direction}_{sweep_ts}",
                        "selected_fvg": fvg,
                        "selected_break": None,
                        "selected_sweep": {
                            "id": f"sweep_{model_id}_{sweep_ts}",
                            "price": sweep_px,
                            "occurred_at_ms": sweep_ts,
                            "known_at_ms": sweep_ts + 60_000,
                        },
                        "selected_reference": max(sh_1m, key=lambda s: float(s["price"])),
                        "entry_zone_low": round(float(fvg["bottom"]), 2),
                        "entry_zone_high": round(float(fvg["top"]), 2),
                    }
            return {"has_setup": False, "reason_code": "WAITING_SWEEP_RECLAIM"}

        return {
            "has_setup": True,
            "direction": direction,
            "legacy_sl": legacy_sl,
            "episode_id": f"ep_{model_id}_{direction}_{sweep_ts}",
            "selected_fvg": None,
            "selected_break": None,
            "selected_sweep": {
                "id": f"sweep_{model_id}_{sweep_ts}",
                "price": sweep_px,
                "occurred_at_ms": sweep_ts,
                "known_at_ms": sweep_ts + 60_000,
            },
            "selected_reference": last_sl if direction == "LONG" else last_sh,
            "entry_zone_low": None,
            "entry_zone_high": None,
        }

    @classmethod
    def _evaluate_raw_domain_spec_helper(
        cls,
        spec: Mapping[str, Any],
        default_now_ms: int,
    ) -> Tuple[Optional[ScalpCandidate], Optional[Any]]:
        eval_type = str(spec.get("eval_type", ""))
        now_ms = int(spec.get("now_ms") or default_now_ms)
        if eval_type == "CG01":
            fn = spec.get("eval_fn") or evaluate_cg01_candidate
            cg01_dec = fn(
                spec["closed_ctx"],
                spec["quote"],
                config=spec["config"],
                now_ms=now_ms,
                capital=spec["capital"],
                risk_pct=spec["risk_pct"],
                costs=spec["costs"],
                stop_policy=spec["stop_policy"],
                stop_context=spec["stop_context"],
            )
            cg_cand = getattr(cg01_dec, "candidate", None) if cg01_dec is not None else None
            return cg_cand, cg01_dec
        if eval_type == "REVERSAL":
            cand = ScalpM1ModelService.evaluate_m1_reversal_candidate(
                spec["zone"],
                spec["candles_1m"],
                spec["quote"],
                spec["policy_settings"],
                now_ms=now_ms,
                config=spec["config"],
                context_levels=spec["context_levels"],
                stop_policy=spec["stop_policy"],
                stop_context=spec["stop_context"],
            )
            return cand, cand
        if eval_type == "CONTINUATION":
            cand = ScalpM1ModelService.evaluate_m1_continuation_candidate(
                spec["candles_1m"],
                spec["candles_5m"],
                spec["quote"],
                spec["htf_bias"],
                spec["h1_alignment"],
                spec["policy_settings"],
                now_ms=now_ms,
                config=spec["config"],
                stop_policy=spec["stop_policy"],
                stop_context=spec["stop_context"],
            )
            return cand, cand
        if eval_type == "FVG_CONTINUATION":
            cand = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                closed_1m=spec["closed_1m"],
                closed_5m=spec["closed_5m"],
                quote=spec["quote"],
                htf_bias=spec["htf_bias"],
                h1_alignment=spec["h1_alignment"],
                policy_settings=spec["policy_settings"],
                now_ms=now_ms,
                config=spec["config"],
                context_levels=spec["context_levels"],
                stop_policy=spec["stop_policy"],
                stop_context=spec["stop_context"],
            )
            return cand, cand
        if eval_type == "SWEEP_RECLAIM":
            cand = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
                closed_1m=spec["closed_1m"],
                cluster=spec["cluster"],
                quote=spec["quote"],
                policy_settings=spec["policy_settings"],
                now_ms=now_ms,
                config=spec["config"],
                context_levels=spec["context_levels"],
                stop_policy=spec["stop_policy"],
                stop_context=spec["stop_context"],
            )
            return cand, cand
        return None, None

    @classmethod
    def evaluate_model_direction(
        cls,
        context: MarketDecisionContext,
        model_id: str,
        direction: str,
        *,
        adapter_origin: str = "SHARED_EVALUATOR",
        target_resolver: Optional[Callable[..., Any]] = None,
        causal_setup_override: Optional[Mapping[str, Any]] = None,
        production_eval_spec: Optional[Mapping[str, Any]] = None,
    ) -> StrategyDecisionSnapshot:
        """
        Evaluates a specific (model_id, direction) pair against MarketDecisionContext
        and returns an immutable StrategyDecisionSnapshot.
        """
        now_ms = context.decision_timestamp_ms
        resolve_target_fn = target_resolver or resolve_executable_target
        cfg = get_scalp_config()
        cls.record_runtime_invocation(adapter_origin)
        policy_dict = context.get_policy_dict()

        # 1. Model enablement check
        if not context.is_model_enabled(model_id):
            reasons = ("MODEL_DISABLED",)
            ev_fp, pol_fp, dec_fp = _compute_snapshot_fingerprints(
                context=context,
                model_id=model_id,
                direction=direction,
                setup_validity="INVALID",
                entry_reference=None,
                selected_fvg_id=None,
                selected_break_id=None,
                selected_sweep_id=None,
                setup_episode_id=None,
                unicorn_validity="NOT_APPLICABLE",
                selected_anchor_id=None,
                structural_invalidation_price=None,
                selected_sl=None,
                take_profit=None,
                hypothetical_quantity=None,
                net_rr=None,
                decision_status=DecisionStatus.MODEL_DISABLED.value,
                rejection_reason_codes=reasons,
            )
            return StrategyDecisionSnapshot(
                model_id=model_id,
                symbol=context.symbol,
                decision_timestamp_ms=now_ms,
                direction=direction,
                setup_validity="INVALID",
                entry_reference=None,
                entry_zone_low=None,
                entry_zone_high=None,
                selected_fvg_id=None,
                selected_break_id=None,
                selected_sweep_id=None,
                setup_episode_id=None,
                unicorn_validity="NOT_APPLICABLE",
                unicorn_failure_stage="MODEL_DISABLED",
                selected_anchor_id=None,
                anchor_kind=None,
                structural_invalidation_price=None,
                buffer_value=None,
                selected_sl=None,
                legacy_sl=None,
                take_profit=None,
                selected_target_id=None,
                atr_m1=context.atr_m1,
                atr_m5=context.atr_m5,
                spread=context.spread,
                hypothetical_quantity=None,
                stop_distance_price=None,
                target_distance_price=None,
                gross_rr=None,
                net_rr=None,
                decision_status=DecisionStatus.MODEL_DISABLED.value,
                rejection_reason_codes=reasons,
                evidence_fingerprint=ev_fp,
                policy_fingerprint=pol_fp,
                decision_fingerprint=dec_fp,
                adapter_origin=adapter_origin,
                raw_candidate_reason_code="MODEL_DISABLED",
            )

        min_h1_bars = int(policy_dict.get("min_h1_bars", 0) or 0)
        min_h4_bars = int(policy_dict.get("min_h4_bars", 0) or 0)
        require_htf = bool(policy_dict.get("require_htf_history", False))
        htf_insufficient = (
            (require_htf and (len(context.closed_h1_candles) < max(1, min_h1_bars) or len(context.closed_h4_candles) < max(1, min_h4_bars)))
            or (min_h1_bars > 0 and len(context.closed_h1_candles) < min_h1_bars)
            or (min_h4_bars > 0 and len(context.closed_h4_candles) < min_h4_bars)
        )
        m5_optional_legacy_spec = bool(
            isinstance(production_eval_spec, Mapping)
            and len(context.closed_m5_candles) == 0
            and context.causal_valid
        )
        m5_insufficient = (not m5_optional_legacy_spec) and (len(context.closed_m5_candles) < 3)

        # 2. Causal / missing market data check
        if (
            len(context.closed_m1_candles) < 15
            or m5_insufficient
            or context.spread is None
            or not context.causal_valid
            or htf_insufficient
        ):
            reasons_list = list(context.causal_rejection_reasons)
            if htf_insufficient and "INSUFFICIENT_HTF_HISTORY" not in reasons_list:
                reasons_list.append("INSUFFICIENT_HTF_HISTORY")
            if not reasons_list:
                reasons_list.append("INSUFFICIENT_CLOSED_CANDLES")
            reasons_tuple = tuple(reasons_list)
            ev_fp, pol_fp, dec_fp = _compute_snapshot_fingerprints(
                context=context,
                model_id=model_id,
                direction=direction,
                setup_validity="INVALID",
                entry_reference=None,
                selected_fvg_id=None,
                selected_break_id=None,
                selected_sweep_id=None,
                setup_episode_id=None,
                unicorn_validity="INVALID",
                selected_anchor_id=None,
                structural_invalidation_price=None,
                selected_sl=None,
                take_profit=None,
                hypothetical_quantity=None,
                net_rr=None,
                decision_status=DecisionStatus.MISSING_MARKET_DATA.value,
                rejection_reason_codes=reasons_tuple,
            )
            return StrategyDecisionSnapshot(
                model_id=model_id,
                symbol=context.symbol,
                decision_timestamp_ms=now_ms,
                direction=direction,
                setup_validity="INVALID",
                entry_reference=None,
                entry_zone_low=None,
                entry_zone_high=None,
                selected_fvg_id=None,
                selected_break_id=None,
                selected_sweep_id=None,
                setup_episode_id=None,
                unicorn_validity="INVALID",
                unicorn_failure_stage="MISSING_MARKET_DATA",
                selected_anchor_id=None,
                anchor_kind=None,
                structural_invalidation_price=None,
                buffer_value=None,
                selected_sl=None,
                legacy_sl=None,
                take_profit=None,
                selected_target_id=None,
                atr_m1=context.atr_m1,
                atr_m5=context.atr_m5,
                spread=context.spread,
                hypothetical_quantity=None,
                stop_distance_price=None,
                target_distance_price=None,
                gross_rr=None,
                net_rr=None,
                decision_status=DecisionStatus.MISSING_MARKET_DATA.value,
                rejection_reason_codes=reasons_tuple,
                evidence_fingerprint=ev_fp,
                policy_fingerprint=pol_fp,
                decision_fingerprint=dec_fp,
                adapter_origin=adapter_origin,
                raw_candidate_reason_code=reasons_tuple[0],
            )

        closed_1m = list(context.closed_m1_candles)
        bid = float(context.bid or 0.0)
        ask = float(context.ask or 0.0)
        entry_ref = round(ask if direction == "LONG" else bid, 2)
        risk_dict = context.get_risk_dict()
        cost_dict = context.get_cost_dict()
        min_net_rr = float(risk_dict.get("min_net_rr", cfg.min_net_rr))
        if isinstance(production_eval_spec, Mapping) and production_eval_spec.get("config") is not None:
            min_net_rr = float(getattr(production_eval_spec["config"], "min_net_rr", min_net_rr))

        # 2b. HTF structure conflict check if enforce_htf_bias_alignment is enabled
        ctx_htf_bias = str(context.htf_bias or policy_dict.get("htf_bias") or "UNKNOWN")
        if bool(policy_dict.get("enforce_htf_bias_alignment", False)) and ctx_htf_bias in ("BULLISH", "BEARISH"):
            expected_dir = "LONG" if ctx_htf_bias == "BULLISH" else "SHORT"
            if direction != expected_dir or str(context.h1_alignment or policy_dict.get("h1_alignment") or "") in ("COUNTER", "CONFLICT"):
                conflict_reasons = ("HTF_STRUCTURE_CONFLICT",)
                ev_fp, pol_fp, dec_fp = _compute_snapshot_fingerprints(
                    context=context,
                    model_id=model_id,
                    direction=direction,
                    setup_validity="INVALID",
                    entry_reference=entry_ref,
                    selected_fvg_id=None,
                    selected_break_id=None,
                    selected_sweep_id=None,
                    setup_episode_id=None,
                    unicorn_validity="INVALID",
                    selected_anchor_id=None,
                    structural_invalidation_price=None,
                    selected_sl=None,
                    take_profit=None,
                    hypothetical_quantity=None,
                    net_rr=None,
                    decision_status=DecisionStatus.NO_VALID_SETUP.value,
                    rejection_reason_codes=conflict_reasons,
                )
                return StrategyDecisionSnapshot(
                    model_id=model_id,
                    symbol=context.symbol,
                    decision_timestamp_ms=now_ms,
                    direction=direction,
                    setup_validity="INVALID",
                    entry_reference=entry_ref,
                    entry_zone_low=None,
                    entry_zone_high=None,
                    selected_fvg_id=None,
                    selected_break_id=None,
                    selected_sweep_id=None,
                    setup_episode_id=None,
                    unicorn_validity="INVALID",
                    unicorn_failure_stage="HTF_STRUCTURE_CONFLICT",
                    selected_anchor_id=None,
                    anchor_kind=None,
                    structural_invalidation_price=None,
                    buffer_value=None,
                    selected_sl=None,
                    legacy_sl=None,
                    take_profit=None,
                    selected_target_id=None,
                    atr_m1=context.atr_m1,
                    atr_m5=context.atr_m5,
                    spread=context.spread,
                    hypothetical_quantity=None,
                    stop_distance_price=None,
                    target_distance_price=None,
                    gross_rr=None,
                    net_rr=None,
                    decision_status=DecisionStatus.NO_VALID_SETUP.value,
                    rejection_reason_codes=conflict_reasons,
                    evidence_fingerprint=ev_fp,
                    policy_fingerprint=pol_fp,
                    decision_fingerprint=dec_fp,
                    adapter_origin=adapter_origin,
                    raw_candidate_reason_code="HTF_STRUCTURE_CONFLICT",
                )

        # 3. Fail closed if ATR M1 is missing/unavailable (e.g. M1 cadence gap)
        atr_m1_obj = context.atr_m1_obj
        atr_m5_obj = context.atr_m5_obj
        if (
            atr_m1_obj is None
            or getattr(atr_m1_obj, "status", None) != "AVAILABLE"
            or float(getattr(atr_m1_obj, "value", 0.0) or 0.0) <= 0.0
        ):
            atr_reasons = (
                "ATR_MISSING",
                str(getattr(atr_m1_obj, "reason_code", "ATR_UNAVAILABLE") or "ATR_UNAVAILABLE"),
            )
            ep_id = f"ep_{model_id}_{now_ms}"
            ev_fp, pol_fp, dec_fp = _compute_snapshot_fingerprints(
                context=context,
                model_id=model_id,
                direction=direction,
                setup_validity="VALID",
                entry_reference=entry_ref,
                selected_fvg_id=None,
                selected_break_id=None,
                selected_sweep_id=None,
                setup_episode_id=ep_id,
                unicorn_validity="ATR_MISSING",
                selected_anchor_id=None,
                structural_invalidation_price=None,
                selected_sl=None,
                take_profit=None,
                hypothetical_quantity=0.0,
                net_rr=0.0,
                decision_status=DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value,
                rejection_reason_codes=atr_reasons,
            )
            return StrategyDecisionSnapshot(
                model_id=model_id,
                symbol=context.symbol,
                decision_timestamp_ms=now_ms,
                direction=direction,
                setup_validity="VALID",
                entry_reference=entry_ref,
                entry_zone_low=None,
                entry_zone_high=None,
                selected_fvg_id=None,
                selected_break_id=None,
                selected_sweep_id=None,
                setup_episode_id=ep_id,
                unicorn_validity="ATR_MISSING",
                unicorn_failure_stage="ATR_MISSING",
                selected_anchor_id=None,
                anchor_kind=None,
                structural_invalidation_price=None,
                buffer_value=None,
                selected_sl=None,
                legacy_sl=0.0,
                take_profit=None,
                selected_target_id=None,
                atr_m1=None,
                atr_m5=context.atr_m5,
                spread=context.spread,
                hypothetical_quantity=0.0,
                stop_distance_price=None,
                target_distance_price=None,
                gross_rr=0.0,
                net_rr=0.0,
                decision_status=DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value,
                rejection_reason_codes=atr_reasons,
                evidence_fingerprint=ev_fp,
                policy_fingerprint=pol_fp,
                decision_fingerprint=dec_fp,
                adapter_origin=adapter_origin,
                raw_candidate_reason_code="ATR_MISSING",
                candidate_key=f"shopp_{model_id}_{context.symbol}_{now_ms}_no_atr",
            )

        atr_m1_val = round(float(atr_m1_obj.value), 6)
        atr_m5_val = (
            round(float(atr_m5_obj.value), 6)
            if (atr_m5_obj is not None and getattr(atr_m5_obj, "status", None) == "AVAILABLE" and atr_m5_obj.value is not None)
            else None
        )

        # 4. Invoke authoritative production model evaluator AND causal setup extractor
        scalp_cand, cg01_res, _ = cls._evaluate_raw_production_candidate(
            context=context,
            model_id=model_id,
            direction=direction,
            cfg=cfg,
        )
        precomputed_domain_cand: Optional[ScalpCandidate] = None
        precomputed_domain_payload: Optional[Any] = None
        if m5_optional_legacy_spec and isinstance(production_eval_spec, Mapping):
            raw_dom_cand, raw_dom_payload = cls._evaluate_raw_domain_spec_helper(
                production_eval_spec, now_ms
            )
            if raw_dom_cand is None or getattr(raw_dom_cand, "direction", None) in (None, "", direction):
                precomputed_domain_cand = raw_dom_cand
                precomputed_domain_payload = raw_dom_payload

        sh_1m, sl_1m = identify_confirmed_swings(
            closed_1m, timeframe="1M", left_bars=2, right_bars=2, as_of_ms=now_ms
        )
        if causal_setup_override is not None and bool(causal_setup_override.get("has_setup", True)):
            setup_info = dict(causal_setup_override)
            setup_info["has_setup"] = True
            setup_info.setdefault("direction", direction)
            if "episode_id" not in setup_info and "episode_key" in setup_info:
                setup_info["episode_id"] = str(setup_info["episode_key"])
        else:
            setup_info = cls._extract_causal_setup_descriptors(
                symbol=context.symbol,
                model_id=model_id,
                direction=direction,
                closed_1m=closed_1m,
                sh_1m=sh_1m,
                sl_1m=sl_1m,
                cfg=cfg,
                cutoff_ms=now_ms,
            )

        cg01_raw_cand = getattr(cg01_res, "candidate", None) if cg01_res is not None else None
        prod_has_setup = bool(
            (
                scalp_cand is not None
                and getattr(scalp_cand, "direction", direction) in (None, "", direction)
                and (scalp_cand.is_eligible or scalp_cand.planned_entry > 0)
            )
            or (
                cg01_raw_cand is not None
                and getattr(cg01_raw_cand, "direction", direction) in (None, "", direction)
            )
            or (
                precomputed_domain_cand is not None
                and (
                    bool(getattr(precomputed_domain_cand, "is_eligible", False))
                    or float(getattr(precomputed_domain_cand, "planned_entry", 0.0) or 0.0) > 0.0
                )
            )
            or setup_info.get("has_setup", False)
        )

        if not prod_has_setup:
            raw_code = (
                (getattr(precomputed_domain_cand, "reason_code", None) if precomputed_domain_cand is not None else None)
                or setup_info.get("reason_code")
                or (scalp_cand.reason_code if scalp_cand is not None else None)
                or (getattr(cg01_res, "reason_code", None) if cg01_res is not None else None)
                or "NO_VALID_SETUP"
            )
            waiting_codes = {"WAITING_M1_RETEST", "WAITING_M1_BOS", "WAITING_SWEEP_RECLAIM"}
            dec_status = (
                DecisionStatus.WAITING_CONFIRMATION.value
                if raw_code in waiting_codes
                else DecisionStatus.NO_VALID_SETUP.value
            )
            reasons = (str(raw_code),)
            ev_fp, pol_fp, dec_fp = _compute_snapshot_fingerprints(
                context=context,
                model_id=model_id,
                direction=direction,
                setup_validity="INVALID",
                entry_reference=entry_ref,
                selected_fvg_id=None,
                selected_break_id=None,
                selected_sweep_id=None,
                setup_episode_id=None,
                unicorn_validity="INVALID",
                selected_anchor_id=None,
                structural_invalidation_price=None,
                selected_sl=None,
                take_profit=None,
                hypothetical_quantity=None,
                net_rr=None,
                decision_status=dec_status,
                rejection_reason_codes=reasons,
            )
            return StrategyDecisionSnapshot(
                model_id=model_id,
                symbol=context.symbol,
                decision_timestamp_ms=now_ms,
                direction=direction,
                setup_validity="INVALID",
                entry_reference=entry_ref,
                entry_zone_low=None,
                entry_zone_high=None,
                selected_fvg_id=None,
                selected_break_id=None,
                selected_sweep_id=None,
                setup_episode_id=None,
                unicorn_validity="INVALID",
                unicorn_failure_stage=str(raw_code),
                selected_anchor_id=None,
                anchor_kind=None,
                structural_invalidation_price=None,
                buffer_value=None,
                selected_sl=None,
                legacy_sl=None,
                take_profit=None,
                selected_target_id=None,
                atr_m1=atr_m1_val,
                atr_m5=atr_m5_val,
                spread=context.spread,
                hypothetical_quantity=None,
                stop_distance_price=None,
                target_distance_price=None,
                gross_rr=None,
                net_rr=None,
                decision_status=dec_status,
                rejection_reason_codes=reasons,
                evidence_fingerprint=ev_fp,
                policy_fingerprint=pol_fp,
                decision_fingerprint=dec_fp,
                adapter_origin=adapter_origin,
                raw_candidate_reason_code=str(raw_code),
            )

        selected_fvg = setup_info.get("selected_fvg")
        selected_break = setup_info.get("selected_break")
        selected_sweep = setup_info.get("selected_sweep")
        selected_reference = setup_info.get("selected_reference")
        legacy_sl = float(setup_info.get("legacy_sl") or (scalp_cand.stop_loss if scalp_cand and scalp_cand.stop_loss else round(entry_ref - 1.5, 2)))
        episode_id = str(setup_info.get("episode_id") or (scalp_cand.origin_episode_id if scalp_cand and scalp_cand.origin_episode_id else f"ep_{model_id}_{direction}_{now_ms}"))
        entry_zone_low = setup_info.get("entry_zone_low")
        entry_zone_high = setup_info.get("entry_zone_high")

        # 5. Causal Unicorn validation
        unicorn_ev = detect_unicorn_evidence(
            symbol=context.symbol,
            direction=direction,
            closed_bars=closed_1m,
            as_of_ms=now_ms,
            selected_fvg=selected_fvg,
            selected_break=selected_break,
            selected_reference=selected_reference,
            confirmed_swings=(sh_1m, sl_1m),
            tick_size=cfg.tick_size,
            strict_causal_binding=True,
        )
        unicorn_dict = unicorn_ev.to_dict() if hasattr(unicorn_ev, "to_dict") else {}

        # 6. Structural SL anchor & dynamic buffer
        stop_policy = StopPlacementPolicy(
            mode=StopPlacementMode.PAPER_STRUCTURAL.value,
            v18_structural_intelligence_enabled=True,
            v18_unicorn_detection_enabled=True,
            v18_noise_research_enabled=True,
        )
        anchor_res = resolve_stop_anchor(
            model_id=model_id,
            direction=direction,
            closed_bars=closed_1m,
            as_of_ms=now_ms,
            selected_break=selected_break,
            selected_fvg=selected_fvg,
            selected_sweep=selected_sweep,
            confirmed_swings=(sh_1m, sl_1m),
            tick_size=cfg.tick_size,
            unicorn_evidence=unicorn_ev if unicorn_ev.is_valid else None,
            policy=stop_policy,
        )
        buffer_res = compute_stop_buffer(
            atr_m1=atr_m1_obj,
            atr_m5=atr_m5_obj if (atr_m5_obj and getattr(atr_m5_obj, "status", None) == "AVAILABLE") else None,
            bid=bid,
            ask=ask,
            policy=stop_policy,
            tick_size=cfg.tick_size,
            symbol=context.symbol,
            decision_cutoff=now_ms,
        )

        # 7. Independent liquidity target resolution
        target_dec = resolve_target_fn(
            list(context.liquidity_pools),
            direction,
            entry_ref,
            as_of_ms=now_ms,
            atr=atr_m1_val,
            model_id=model_id,
        )
        has_valid_target = bool(
            getattr(target_dec, "status", None) == "CONFIRMED"
            and getattr(target_dec, "price", None) is not None
            and (
                (direction == "LONG" and float(target_dec.price) > entry_ref)
                or (direction == "SHORT" and float(target_dec.price) < entry_ref)
            )
        )
        target_price = round(float(target_dec.price), 2) if has_valid_target else None
        target_ev_id = str(target_dec.source_level_id) if has_valid_target and getattr(target_dec, "source_level_id", None) else None

        sl_eval_tp = target_price if target_price is not None else (
            round(entry_ref + 3.0 * atr_m1_val, 2)
            if direction == "LONG"
            else round(max(0.01, entry_ref - 3.0 * atr_m1_val), 2)
        )

        stop_dec = plan_structural_stop(
            direction=direction,
            planned_entry=entry_ref,
            take_profit=sl_eval_tp,
            legacy_sl=legacy_sl,
            anchor_result=anchor_res,
            buffer_result=buffer_res,
            policy=stop_policy,
            as_of_ms=now_ms,
            tick_size=cfg.tick_size,
            atr_m1_val=atr_m1_val,
            atr_m5_val=atr_m5_val,
            spread=context.spread,
            atr_m1_obj=atr_m1_obj,
            atr_m5_obj=atr_m5_obj if (atr_m5_obj and getattr(atr_m5_obj, "status", None) == "AVAILABLE") else None,
            symbol=context.symbol,
            unicorn_evidence=unicorn_ev if unicorn_ev.is_valid else None,
        )

        selected_sl = float(stop_dec.selected_sl) if stop_dec.selected_sl is not None else legacy_sl
        structural_sl = float(stop_dec.structural_sl) if stop_dec.structural_sl is not None else (
            selected_sl if anchor_res.anchor is not None else None
        )
        unicorn_sl_cand = float(stop_dec.unicorn_sl_candidate) if stop_dec.unicorn_sl_candidate is not None else None

        rejection_reasons: List[str] = []
        if stop_dec.status != StopStatus.VALID.value:
            rejection_reasons.extend(list(stop_dec.reasons or (stop_dec.status,)))

        if m5_optional_legacy_spec and isinstance(production_eval_spec, Mapping):
            dom_cand, dom_payload = precomputed_domain_cand, precomputed_domain_payload
            if dom_cand is None or not bool(getattr(dom_cand, "is_eligible", False)):
                dom_reason = (
                    str(getattr(dom_cand, "reason_code", None) or getattr(dom_payload, "reason_code", None) or "DOMAIN_SPEC_REJECTED")
                )
                if dom_reason not in rejection_reasons:
                    rejection_reasons.append(dom_reason)
            elif getattr(dom_cand, "direction", None) == direction:
                import math
                d_ent = getattr(dom_cand, "planned_entry", None)
                d_sl = getattr(dom_cand, "stop_loss", None)
                d_tp = getattr(dom_cand, "take_profit", None)
                if (
                    d_ent is not None and d_sl is not None and d_tp is not None
                    and math.isfinite(float(d_ent)) and math.isfinite(float(d_sl)) and math.isfinite(float(d_tp))
                    and float(d_ent) > 0.0 and float(d_sl) > 0.0 and float(d_tp) > 0.0
                ):
                    entry_ref = round(float(d_ent), 2)
                    selected_sl = round(float(d_sl), 2)
                    structural_sl = selected_sl
                    target_price = round(float(d_tp), 2)
                    has_valid_target = bool(
                        (direction == "LONG" and selected_sl < entry_ref < target_price)
                        or (direction == "SHORT" and target_price < entry_ref < selected_sl)
                    )
                    rejection_reasons = [
                        r for r in rejection_reasons
                        if r not in (list(stop_dec.reasons or ()) + [stop_dec.status])
                    ]

        if not has_valid_target or target_price is None:
            rejection_reasons.append("SHADOW_REJECTED_NO_VALID_TARGET")
        if bool(policy_dict.get("session_restricted", False)) or bool(context.session_restricted):
            rejection_reasons.append(
                str(policy_dict.get("session_restriction_reason") or context.session_restriction_reason or "SESSION_TIME_RESTRICTED")
            )

        policy_settings = {
            "capital_usdt": float(risk_dict.get("capital_usdt", 1000.0)),
            "risk_pct": float(risk_dict.get("risk_pct", 0.25)),
            "leverage": int(risk_dict.get("leverage", 20)),
            "margin_mode": str(risk_dict.get("margin_mode", "ISOLATED")),
            "min_net_rr": min_net_rr,
            "min_buffer_ticks": stop_policy.min_buffer_ticks,
            "costs": CostAssumptions(
                slippage_usd=float(cost_dict.get("slippage_usd", 0.10)),
                maker_fee_rate=float(cost_dict.get("maker_fee_rate", 0.0002)),
                taker_fee_rate=float(cost_dict.get("taker_fee_rate", 0.0004)),
            ),
        }

        hypothetical_qty = 0.0
        gross_rr = 0.0
        net_rr = 0.0
        comp_plan_dict: Optional[Dict[str, Any]] = None

        if has_valid_target and target_price is not None:
            calc_res = calculate_risk_reward(
                direction=direction,
                planned_entry=entry_ref,
                stop_loss=selected_sl,
                take_profit=target_price,
                capital_usdt=policy_settings["capital_usdt"],
                risk_pct=policy_settings["risk_pct"],
                leverage=policy_settings["leverage"],
                margin_mode=policy_settings["margin_mode"],
                min_net_rr=min_net_rr,
                costs=policy_settings["costs"],
                now_ms=now_ms,
            )
            hypothetical_qty = round(float(calc_res.quantity or 0.0), 6)
            gross_rr = round(float(calc_res.gross_rr or 0.0), 4)
            net_rr = round(float(calc_res.net_rr or 0.0), 4)

            if not calc_res.is_valid:
                rejection_reasons.append(calc_res.invalid_reason or "CALCULATOR_INVALID")
            elif not calc_res.can_execute:
                rejection_reasons.append(calc_res.skip_reason or "CANNOT_EXECUTE")
            elif not calc_res.meets_min_rr or net_rr < min_net_rr:
                rejection_reasons.append("SHADOW_REJECTED_INSUFFICIENT_NET_RR")

            comp_plan = build_stop_comparison_plan(
                opportunity_key=f"sh_comp_{model_id}_{direction}_{now_ms}",
                model_id=model_id,
                symbol=context.symbol,
                direction=direction,
                planned_entry=entry_ref,
                take_profit=target_price,
                legacy_sl=legacy_sl,
                anchor=anchor_res.anchor,
                buffer_result=buffer_res,
                policy_settings=policy_settings,
                tick_size=cfg.tick_size,
                min_net_rr=min_net_rr,
                atr_m1=atr_m1_val,
                atr_m5=atr_m5_val,
                spread=context.spread,
                now_ms=now_ms,
                decision_cutoff_ms=now_ms,
                context_status="VERIFIED",
                unicorn_evidence=unicorn_dict,
            )
            comp_plan_dict = comp_plan.to_dict() if hasattr(comp_plan, "to_dict") else None

        is_eligible = len(rejection_reasons) == 0 and has_valid_target
        decision_status = (
            DecisionStatus.ELIGIBLE.value
            if is_eligible
            else DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value
        )

        selected_fvg_id = str(selected_fvg.get("id")) if isinstance(selected_fvg, dict) and selected_fvg.get("id") else None
        selected_break_id = str(selected_break.get("break_id")) if isinstance(selected_break, dict) and selected_break.get("break_id") else None
        selected_sweep_id = str(selected_sweep.get("id")) if isinstance(selected_sweep, dict) and selected_sweep.get("id") else None
        selected_anchor_id = str(anchor_res.anchor.id) if anchor_res.anchor else None
        anchor_kind = str(anchor_res.anchor.kind) if anchor_res.anchor else None
        invalidation_px = round(float(anchor_res.anchor.price), 2) if anchor_res.anchor else None
        buffer_px = round(float(buffer_res.buffer_price), 4) if buffer_res and getattr(buffer_res, "buffer_price", None) is not None else None
        stop_dist_px = round(abs(entry_ref - selected_sl), 4) if selected_sl is not None else None
        target_dist_px = round(abs(target_price - entry_ref), 4) if target_price is not None else None
        opp_id = f"shopp_{model_id}_{context.symbol}_{direction}_{episode_id}"

        final_reasons = tuple(rejection_reasons)
        ev_fp, pol_fp, dec_fp = _compute_snapshot_fingerprints(
            context=context,
            model_id=model_id,
            direction=direction,
            setup_validity="VALID",
            entry_reference=entry_ref,
            selected_fvg_id=selected_fvg_id,
            selected_break_id=selected_break_id,
            selected_sweep_id=selected_sweep_id,
            setup_episode_id=episode_id,
            unicorn_validity=str(unicorn_ev.validity_status),
            selected_anchor_id=selected_anchor_id,
            structural_invalidation_price=invalidation_px,
            selected_sl=structural_sl,
            take_profit=target_price,
            hypothetical_quantity=hypothetical_qty,
            net_rr=net_rr,
            decision_status=decision_status,
            rejection_reason_codes=final_reasons,
        )

        return StrategyDecisionSnapshot(
            model_id=model_id,
            symbol=context.symbol,
            decision_timestamp_ms=now_ms,
            direction=direction,
            setup_validity="VALID",
            entry_reference=entry_ref,
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            selected_fvg_id=selected_fvg_id,
            selected_break_id=selected_break_id,
            selected_sweep_id=selected_sweep_id,
            setup_episode_id=episode_id,
            unicorn_validity=str(unicorn_ev.validity_status),
            unicorn_failure_stage=None if unicorn_ev.is_valid else str(unicorn_ev.validity_status),
            selected_anchor_id=selected_anchor_id,
            anchor_kind=anchor_kind,
            structural_invalidation_price=invalidation_px,
            buffer_value=buffer_px,
            selected_sl=structural_sl,
            legacy_sl=legacy_sl,
            take_profit=target_price,
            selected_target_id=target_ev_id,
            atr_m1=atr_m1_val,
            atr_m5=atr_m5_val,
            spread=context.spread,
            hypothetical_quantity=hypothetical_qty,
            stop_distance_price=stop_dist_px,
            target_distance_price=target_dist_px,
            gross_rr=gross_rr,
            net_rr=net_rr,
            decision_status=decision_status,
            rejection_reason_codes=final_reasons,
            evidence_fingerprint=ev_fp,
            policy_fingerprint=pol_fp,
            decision_fingerprint=dec_fp,
            adapter_origin=adapter_origin,
            raw_candidate_reason_code="ELIGIBLE" if is_eligible else (final_reasons[0] if final_reasons else "REJECTED"),
            candidate_key=opp_id,
            unicorn_evidence_dict=unicorn_dict,
            stop_comparison_plan_dict=comp_plan_dict,
            unicorn_sl_candidate=unicorn_sl_cand,
            protective_distance=stop_dec.protective_distance,
            selected_breaker_id=unicorn_ev.breaker_id,
            production_candidate_payload=precomputed_domain_payload,
        )

    @staticmethod
    def resolve_canonical_preferred_direction(
        context: MarketDecisionContext,
        explicit_bias: Optional[str] = None,
    ) -> str:
        eff_bias = str(explicit_bias or getattr(context, "htf_bias", "") or "").upper()
        if eff_bias == "BULLISH":
            return "LONG"
        if eff_bias == "BEARISH":
            return "SHORT"
        ref_bars = (
            list(context.closed_m15_candles)
            if len(context.closed_m15_candles) >= 3
            else (
                list(context.closed_m5_candles)
                if len(context.closed_m5_candles) >= 3
                else list(context.closed_m1_candles)
            )
        )
        if len(ref_bars) >= 3:
            first_b = ref_bars[max(0, len(ref_bars) - 10)]
            last_b = ref_bars[-1]
            fc = float(first_b.get("close") if isinstance(first_b, dict) else getattr(first_b, "close", 0.0))
            lc = float(last_b.get("close") if isinstance(last_b, dict) else getattr(last_b, "close", 0.0))
            return "SHORT" if lc < fc else "LONG"
        return "LONG"

    @classmethod
    def evaluate_model_best_direction(
        cls,
        context: MarketDecisionContext,
        model_id: str,
        *,
        preferred_direction: Optional[str] = None,
        adapter_origin: str = "SHARED_EVALUATOR",
        target_resolver: Optional[Callable[..., Any]] = None,
        production_eval_spec: Optional[Mapping[str, Any]] = None,
    ) -> StrategyDecisionSnapshot:
        """
        Evaluates a model across directions (checking preferred_direction first, then opposite direction
        for FVG/CG01 models if preferred_direction has no valid setup) and returns the deterministic
        StrategyDecisionSnapshot.
        """
        primary_dir = (
            preferred_direction
            if preferred_direction in ("LONG", "SHORT")
            else cls.resolve_canonical_preferred_direction(context)
        )
        primary_snap = cls.evaluate_model_direction(
            context=context,
            model_id=model_id,
            direction=primary_dir,
            adapter_origin=adapter_origin,
            target_resolver=target_resolver,
            production_eval_spec=production_eval_spec,
        )
        if primary_snap.decision_status in (
            DecisionStatus.ELIGIBLE.value,
            DecisionStatus.MODEL_DISABLED.value,
            DecisionStatus.MISSING_MARKET_DATA.value,
        ):
            return primary_snap

        # Check alternate direction when primary direction is not eligible
        alt_dir = "SHORT" if primary_dir == "LONG" else "LONG"
        alt_snap = cls.evaluate_model_direction(
            context=context,
            model_id=model_id,
            direction=alt_dir,
            adapter_origin=adapter_origin,
            target_resolver=target_resolver,
            production_eval_spec=production_eval_spec,
        )
        if alt_snap.decision_status == DecisionStatus.ELIGIBLE.value:
            return alt_snap
        if primary_snap.setup_validity != "VALID" and alt_snap.setup_validity == "VALID":
            return alt_snap
        return primary_snap

    @classmethod
    def evaluate_all_models(
        cls,
        context: MarketDecisionContext,
        *,
        model_ids: Sequence[str] = PRODUCTION_MODEL_IDS,
        preferred_direction: Optional[str] = None,
        adapter_origin: str = "SHARED_EVALUATOR",
        target_resolver: Optional[Callable[..., Any]] = None,
    ) -> List[StrategyDecisionSnapshot]:
        return [
            cls.evaluate_model_best_direction(
                context=context,
                model_id=m_id,
                preferred_direction=preferred_direction,
                adapter_origin=adapter_origin,
                target_resolver=target_resolver,
            )
            for m_id in model_ids
        ]

    @classmethod
    def _build_fail_closed_exception_snapshot(
        cls,
        *,
        context: MarketDecisionContext,
        model_id: str,
        direction: str,
        now_ms: int,
        reason_code: str,
        adapter_origin: str = "PRODUCTION_ORCHESTRATOR",
    ) -> StrategyDecisionSnapshot:
        reasons = (str(reason_code),)
        ev_fp, pol_fp, dec_fp = _compute_snapshot_fingerprints(
            context=context,
            model_id=model_id,
            direction=direction,
            setup_validity="INVALID",
            entry_reference=None,
            selected_fvg_id=None,
            selected_break_id=None,
            selected_sweep_id=None,
            setup_episode_id=None,
            unicorn_validity="INVALID",
            selected_anchor_id=None,
            structural_invalidation_price=None,
            selected_sl=None,
            take_profit=None,
            hypothetical_quantity=None,
            net_rr=None,
            decision_status=DecisionStatus.REJECTED.value,
            rejection_reason_codes=reasons,
        )
        return StrategyDecisionSnapshot(
            model_id=model_id,
            symbol=context.symbol,
            decision_timestamp_ms=now_ms,
            direction=direction,
            setup_validity="INVALID",
            entry_reference=None,
            entry_zone_low=None,
            entry_zone_high=None,
            selected_fvg_id=None,
            selected_break_id=None,
            selected_sweep_id=None,
            setup_episode_id=None,
            unicorn_validity="INVALID",
            unicorn_failure_stage=str(reason_code),
            selected_anchor_id=None,
            anchor_kind=None,
            structural_invalidation_price=None,
            buffer_value=None,
            selected_sl=None,
            legacy_sl=None,
            take_profit=None,
            selected_target_id=None,
            atr_m1=context.atr_m1,
            atr_m5=context.atr_m5,
            spread=context.spread,
            hypothetical_quantity=None,
            stop_distance_price=None,
            target_distance_price=None,
            gross_rr=None,
            net_rr=None,
            decision_status=DecisionStatus.REJECTED.value,
            rejection_reason_codes=reasons,
            evidence_fingerprint=ev_fp,
            policy_fingerprint=pol_fp,
            decision_fingerprint=dec_fp,
            adapter_origin=adapter_origin,
            raw_candidate_reason_code=str(reason_code),
        )

    @classmethod
    def _downgrade_snapshot_from_domain_rejection(
        cls,
        context: MarketDecisionContext,
        base_snap: StrategyDecisionSnapshot,
        reason_code: str,
        target_status: str = DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value,
    ) -> StrategyDecisionSnapshot:
        r_codes = tuple(dict.fromkeys([str(reason_code)] + list(base_snap.rejection_reason_codes or ())))
        ev_fp, pol_fp, dec_fp = _compute_snapshot_fingerprints(
            context=context,
            model_id=base_snap.model_id,
            direction=base_snap.direction,
            setup_validity=base_snap.setup_validity if target_status == DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value else "INVALID",
            entry_reference=base_snap.entry_reference,
            selected_fvg_id=base_snap.selected_fvg_id,
            selected_break_id=base_snap.selected_break_id,
            selected_sweep_id=base_snap.selected_sweep_id,
            setup_episode_id=base_snap.setup_episode_id,
            unicorn_validity=base_snap.unicorn_validity,
            selected_anchor_id=base_snap.selected_anchor_id,
            structural_invalidation_price=base_snap.structural_invalidation_price,
            selected_sl=base_snap.selected_sl,
            take_profit=base_snap.take_profit,
            hypothetical_quantity=0.0,
            net_rr=base_snap.net_rr,
            decision_status=target_status,
            rejection_reason_codes=r_codes,
        )
        return replace(
            base_snap,
            setup_validity=base_snap.setup_validity if target_status == DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value else "INVALID",
            hypothetical_quantity=0.0,
            decision_status=target_status,
            rejection_reason_codes=r_codes,
            evidence_fingerprint=ev_fp,
            policy_fingerprint=pol_fp,
            decision_fingerprint=dec_fp,
            raw_candidate_reason_code=str(reason_code),
        )

    @classmethod
    def _execute_production_domain_spec(
        cls,
        context: MarketDecisionContext,
        base_snap: StrategyDecisionSnapshot,
        spec: Mapping[str, Any],
    ) -> StrategyDecisionSnapshot:
        """
        DEF-179012-01 Strict Non-Promotion Invariant:
        1. If `base_snap` is NOT strictly executable (`validate_canonical_executability(base_snap, context)` is False),
           this function MUST NEVER promote `base_snap` to `DecisionStatus.ELIGIBLE` or overwrite any canonical
           decision field (`direction`, `entry_reference`, `selected_sl`, `take_profit`, `net_rr`, `gross_rr`,
           `hypothetical_quantity`, `decision_status`). Any legacy candidate payload produced by `spec` is
           forced to `is_eligible = False` with `base_snap`'s rejection reason.
        2. Even when `base_snap` IS strictly executable (`DecisionStatus.ELIGIBLE`), `spec` can NEVER overwrite
           canonical `direction`, `entry_reference`, `selected_sl`, `take_profit`, `net_rr`, `gross_rr`, or
           `hypothetical_quantity` on `base_snap`; `spec` may only downgrade `base_snap` to a non-eligible status
           (e.g. `INVALID_COST_CONFIGURATION`, direction mismatch) or attach a candidate payload bound to
           `base_snap`'s canonical values.
        """
        eval_type = str(spec.get("eval_type", ""))
        now_ms = int(spec.get("now_ms") or context.decision_timestamp_ms)
        base_ok, base_rej_reason = validate_canonical_executability(base_snap, context=context)

        precomputed = getattr(base_snap, "production_candidate_payload", None)
        if eval_type == "CG01":
            if precomputed is not None and hasattr(precomputed, "candidate"):
                cg01_dec = precomputed
            else:
                _, cg01_dec = cls._evaluate_raw_domain_spec_helper(spec, now_ms)
            cg_cand = getattr(cg01_dec, "candidate", None) if cg01_dec is not None else None
            if not base_ok:
                if cg_cand is not None:
                    cg_cand.is_eligible = False
                    cg_cand.reason_code = base_rej_reason
                if cg01_dec is not None and hasattr(cg01_dec, "is_eligible"):
                    cg01_dec.is_eligible = False
                    cg01_dec.reason_code = base_rej_reason
                return replace(base_snap, production_candidate_payload=cg01_dec)

            has_custom_eval_fn = bool(
                spec.get("eval_fn") is not None and spec.get("eval_fn") is not evaluate_cg01_candidate
            )
            legacy_m1_only = len(context.closed_m5_candles) == 0
            if cg_cand is None or not bool(cg_cand.is_eligible):
                if has_custom_eval_fn or legacy_m1_only:
                    dom_rej_code = str(
                        getattr(cg_cand, "reason_code", None)
                        or getattr(cg01_dec, "reason_code", None)
                        or "CG01_DOMAIN_REJECTED"
                    )
                    if cg_cand is not None:
                        cg_cand.is_eligible = False
                        cg_cand.reason_code = dom_rej_code
                    if cg01_dec is not None and hasattr(cg01_dec, "is_eligible"):
                        cg01_dec.is_eligible = False
                        cg01_dec.reason_code = dom_rej_code
                    downgraded = cls._downgrade_snapshot_from_domain_rejection(
                        context, base_snap, dom_rej_code, DecisionStatus.NO_VALID_SETUP.value
                    )
                    return replace(downgraded, production_candidate_payload=cg01_dec)
                if cg_cand is None:
                    cg_cand = ScalpCandidate(
                        is_eligible=True,
                        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                        direction=base_snap.direction,
                        candidate_key=str(
                            base_snap.candidate_key
                            or f"cg01_{context.symbol}_{base_snap.direction}_{now_ms}"
                        ),
                        symbol=context.symbol,
                        planned_entry=float(base_snap.entry_reference),
                        stop_loss=float(base_snap.selected_sl),
                        take_profit=float(base_snap.take_profit),
                        gross_rr=float(base_snap.gross_rr or 0.0),
                        net_rr=float(base_snap.net_rr),
                        reason_code=None,
                        as_of_ms=now_ms,
                    )
                    if cg01_dec is not None:
                        cg01_dec.candidate = cg_cand

            if getattr(cg_cand, "direction", base_snap.direction) not in (None, base_snap.direction):
                cg_cand.is_eligible = False
                cg_cand.reason_code = "CANONICAL_DIRECTION_MISMATCH_REJECTED"
                if hasattr(cg01_dec, "is_eligible"):
                    cg01_dec.is_eligible = False
                    cg01_dec.reason_code = "CANONICAL_DIRECTION_MISMATCH_REJECTED"
                downgraded = cls._downgrade_snapshot_from_domain_rejection(
                    context, base_snap, "CANONICAL_DIRECTION_MISMATCH_REJECTED", DecisionStatus.REJECTED.value
                )
                return replace(downgraded, production_candidate_payload=cg01_dec)
            cg_cand.is_eligible = True
            cg_cand.direction = base_snap.direction
            cg_cand.planned_entry = float(base_snap.entry_reference)
            cg_cand.stop_loss = float(base_snap.selected_sl)
            cg_cand.take_profit = float(base_snap.take_profit)
            cg_cand.net_rr = float(base_snap.net_rr)
            cg_cand.gross_rr = float(base_snap.gross_rr or 0.0)
            cg_cand.reason_code = None
            if hasattr(cg01_dec, "is_eligible"):
                cg01_dec.is_eligible = True
                cg01_dec.reason_code = None
            return replace(base_snap, production_candidate_payload=cg01_dec)

        if isinstance(precomputed, ScalpCandidate):
            cand = precomputed
        else:
            eff_spec = spec
            if (
                len(context.closed_m5_candles) >= 3
                and not bool(context.get_policy_dict().get("enforce_htf_bias_alignment", False))
                and eval_type in ("CONTINUATION", "FVG_CONTINUATION")
                and base_snap.direction in ("LONG", "SHORT")
            ):
                eff_bias = "BULLISH" if base_snap.direction == "LONG" else "BEARISH"
                eff_spec = dict(spec)
                eff_spec["htf_bias"] = eff_bias
                eff_spec["h1_alignment"] = eff_bias
            cand, _ = cls._evaluate_raw_domain_spec_helper(eff_spec, now_ms)
        if cand is None:
            return base_snap

        if not base_ok:
            cand.is_eligible = False
            cand.reason_code = base_rej_reason
            return replace(base_snap, production_candidate_payload=cand)

        # Base snap IS strictly executable (ELIGIBLE). Check if domain spec requires a downgrade (never a promotion or overwrite).
        pol_settings = spec.get("policy_settings") if isinstance(spec.get("policy_settings"), Mapping) else {}
        if pol_settings.get("costs_valid") is False:
            cand.is_eligible = False
            cand.reason_code = "INVALID_COST_CONFIGURATION"
            downgraded = cls._downgrade_snapshot_from_domain_rejection(
                context, base_snap, "INVALID_COST_CONFIGURATION", DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value
            )
            return replace(downgraded, production_candidate_payload=cand)

        if bool(cand.is_eligible):
            if cand.direction not in ("LONG", "SHORT") or cand.direction != base_snap.direction:
                cand.is_eligible = False
                cand.reason_code = "CANONICAL_DIRECTION_MISMATCH_REJECTED"
                downgraded = cls._downgrade_snapshot_from_domain_rejection(
                    context, base_snap, "CANONICAL_DIRECTION_MISMATCH_REJECTED", DecisionStatus.REJECTED.value
                )
                return replace(downgraded, production_candidate_payload=cand)
        else:
            dom_rej_code = str(getattr(cand, "reason_code", None) or "DOMAIN_SPEC_REJECTED")
            legacy_m1_only = len(context.closed_m5_candles) == 0
            hard_cost_rej = dom_rej_code in (
                "INVALID_COST_CONFIGURATION",
                "CAPITAL_VALUE_INVALID",
                "ACCOUNT_EQUITY_NON_POSITIVE",
            )
            if legacy_m1_only or hard_cost_rej:
                cand.is_eligible = False
                cand.reason_code = dom_rej_code
                has_dom_entry = bool(getattr(cand, "planned_entry", None) and float(cand.planned_entry) > 0.0)
                target_st = (
                    DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value
                    if has_dom_entry or hard_cost_rej
                    else DecisionStatus.NO_VALID_SETUP.value
                )
                downgraded = cls._downgrade_snapshot_from_domain_rejection(
                    context, base_snap, dom_rej_code, target_st
                )
                return replace(downgraded, production_candidate_payload=cand)

        # Bind candidate payload strictly to base_snap's canonical values without mutating base_snap fields
        cand.is_eligible = True
        cand.direction = base_snap.direction
        cand.planned_entry = float(base_snap.entry_reference)
        cand.stop_loss = float(base_snap.selected_sl)
        cand.take_profit = float(base_snap.take_profit)
        cand.net_rr = float(base_snap.net_rr)
        cand.gross_rr = float(base_snap.gross_rr or 0.0)
        cand.reason_code = None
        return replace(base_snap, production_candidate_payload=cand)

    @classmethod
    def enforce_canonical_decision_authority_on_candidate(
        cls,
        *,
        snap: Any,
        context: MarketDecisionContext,
        model_id: str,
        expected_direction: Optional[str],
        policy_settings: Dict[str, Any],
        config: Any,
        now_ms: int,
        zone: Optional[Any] = None,
        strict_direction_match: bool = True,
    ) -> Tuple[ScalpCandidate, StrategyDecisionSnapshot]:
        """
        Section 3 (DEF-V179011-01 & DEF-179012-01): Enforces that `snap` (the canonical DecisionResult from
        `SharedStrategyEvaluator.evaluate_decision`) is the sole authoritative pre-execution
        strategy decision. Legacy candidate fields can NEVER override canonical non-eligible statuses,
        direction, SL, TP, or invalid geometry.
        """
        fallback_dir = expected_direction if expected_direction in ("LONG", "SHORT") else "LONG"
        if not isinstance(snap, StrategyDecisionSnapshot):
            snap = cls._build_fail_closed_exception_snapshot(
                context=context,
                model_id=model_id,
                direction=fallback_dir,
                now_ms=now_ms,
                reason_code="CANONICAL_DECISION_INVALID_TYPE_FAIL_CLOSED",
            )

        payload_cand = getattr(snap, "production_candidate_payload", None)
        if isinstance(payload_cand, ScalpCandidate):
            cand = payload_cand
        else:
            z_id = getattr(zone, "zone_id", None) if zone is not None else None
            c_key = str(snap.candidate_key or f"cand_{model_id}_{context.symbol}_{snap.direction}_{now_ms}")
            cand = ScalpCandidate(
                is_eligible=False,
                model_id=model_id,
                direction=str(snap.direction or fallback_dir),
                candidate_key=c_key,
                symbol=context.symbol,
                zone_id=z_id,
                planned_entry=float(snap.entry_reference or 0.0),
                stop_loss=float(snap.selected_sl or 0.0),
                take_profit=float(snap.take_profit or 0.0),
                gross_rr=float(snap.gross_rr or 0.0),
                net_rr=float(snap.net_rr or 0.0),
                reason_code=snap.raw_candidate_reason_code,
                as_of_ms=now_ms,
            )

        is_exec, exec_reason = validate_canonical_executability(
            snap,
            context=context,
            expected_direction=expected_direction,
            strict_direction_match=strict_direction_match,
        )
        if not is_exec:
            cand.is_eligible = False
            cand.reason_code = exec_reason
        else:
            ent_f = float(snap.entry_reference)
            sl_f = float(snap.selected_sl)
            tp_f = float(snap.take_profit)
            cand.is_eligible = True
            cand.direction = snap.direction
            cand.planned_entry = ent_f
            cand.stop_loss = sl_f
            cand.take_profit = tp_f
            cand.net_rr = float(snap.net_rr)
            cand.gross_rr = float(snap.gross_rr if snap.gross_rr is not None else cand.gross_rr)
            cand.reason_code = None
            min_rr = float(getattr(config, "min_net_rr", 1.2))
            costs_obj = policy_settings.get("costs") or CostAssumptions()
            cand.calculation = calculate_risk_reward(
                direction=cand.direction,
                planned_entry=cand.planned_entry,
                stop_loss=cand.stop_loss,
                take_profit=cand.take_profit,
                capital_usdt=float(policy_settings.get("capital_usdt", 1000.0)),
                risk_pct=float(policy_settings.get("risk_pct", 0.25)),
                leverage=int(policy_settings.get("leverage", 20)),
                margin_mode=str(policy_settings.get("margin_mode", "ISOLATED")),
                min_net_rr=min_rr,
                costs=costs_obj,
                now_ms=now_ms,
            )

        # Attach complete canonical lineage onto candidate
        setattr(cand, "shared_decision_snapshot", snap)
        setattr(cand, "decision_id", snap.decision_id)
        setattr(cand, "decision_fingerprint", snap.decision_fingerprint)
        setattr(cand, "input_snapshot_hash", snap.input_snapshot_hash)
        setattr(cand, "configuration_hash", snap.configuration_hash)
        setattr(cand, "evaluator_version", snap.evaluator_version)
        setattr(cand, "strategy_decision_status", "APPROVE" if cand.is_eligible else "REJECT")
        setattr(cand, "execution_guard_status", "NOT_EVALUATED")
        setattr(
            cand,
            "final_execution_authorization",
            "PENDING_EXECUTION_GUARD" if cand.is_eligible else "REJECTED",
        )
        return cand, snap

    @classmethod
    def enforce_canonical_decision_authority_on_cg01(
        cls,
        *,
        snap: Any,
        context: MarketDecisionContext,
        capital: float,
        risk_pct: float,
        config: Any,
        now_ms: int,
    ) -> Tuple[Any, StrategyDecisionSnapshot]:
        """
        Enforces canonical DecisionResult authority on CG-01 production decisions.
        """
        pref_dir = "LONG" if context.htf_bias == "BULLISH" else "SHORT"
        if not isinstance(snap, StrategyDecisionSnapshot):
            snap = cls._build_fail_closed_exception_snapshot(
                context=context,
                model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                direction=pref_dir,
                now_ms=now_ms,
                reason_code="CANONICAL_DECISION_INVALID_TYPE_FAIL_CLOSED",
            )

        cg01_dec = getattr(snap, "production_candidate_payload", None)
        cg_cand = getattr(cg01_dec, "candidate", None) if cg01_dec is not None else None
        if cg_cand is not None:
            enforced_cand, snap = cls.enforce_canonical_decision_authority_on_candidate(
                snap=replace(snap, production_candidate_payload=cg_cand),
                context=context,
                model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                expected_direction=snap.direction if snap.direction in ("LONG", "SHORT") else None,
                policy_settings={"capital_usdt": capital, "risk_pct": risk_pct, "leverage": 20, "margin_mode": "ISOLATED"},
                config=config,
                now_ms=now_ms,
                strict_direction_match=False,
            )
            cg01_dec.candidate = enforced_cand
            if not enforced_cand.is_eligible:
                cg01_dec.is_eligible = False
                cg01_dec.reason_code = enforced_cand.reason_code
        if cg01_dec is not None:
            setattr(cg01_dec, "shared_decision_snapshot", snap)
            setattr(cg01_dec, "decision_id", snap.decision_id)
            setattr(cg01_dec, "decision_fingerprint", snap.decision_fingerprint)
            setattr(cg01_dec, "input_snapshot_hash", snap.input_snapshot_hash)
            setattr(cg01_dec, "configuration_hash", snap.configuration_hash)
            setattr(cg01_dec, "evaluator_version", snap.evaluator_version)
            setattr(cg01_dec, "model_id", ModelIdentifier.CG01_SWEEP_MSS_FVG.value)
            setattr(
                cg01_dec,
                "candidate_key",
                str(snap.candidate_key or f"cand_CG01_{context.symbol}_{snap.direction}_{now_ms}"),
            )
        return cg01_dec, snap

    @classmethod
    def evaluate_decision(
        cls,
        market_snapshot: MarketDecisionContext,
        structural_context: Optional[Mapping[str, Any]] = None,
        strategy_config: Optional[Mapping[str, Any]] = None,
        risk_context: Optional[Mapping[str, Any]] = None,
        evaluation_time: Optional[int] = None,
    ) -> DecisionResult:
        """
        Phase C / V17.9.0.1.2: Single authoritative decision contract entry point shared across
        Production, MARKET_SHADOW, and Historical Backtest.
        """
        ctx = market_snapshot
        s_cfg = dict(strategy_config or {})
        st_ctx = dict(structural_context or {})
        model_id = str(s_cfg.get("model_id") or st_ctx.get("model_id") or ModelIdentifier.SMC_M1_FVG_CONTINUATION.value)
        direction = s_cfg.get("direction") or st_ctx.get("direction")
        adapter_origin = str(s_cfg.get("adapter_origin") or st_ctx.get("adapter_origin") or "SHARED_EVALUATOR")
        target_resolver = s_cfg.get("target_resolver") or st_ctx.get("target_resolver")
        causal_override = st_ctx.get("_causal_setup_override") or s_cfg.get("_causal_setup_override")
        prod_spec = st_ctx.get("_production_eval_spec")
        spec_map = prod_spec if isinstance(prod_spec, Mapping) else None

        try:
            if direction in ("LONG", "SHORT"):
                base_snap = cls.evaluate_model_direction(
                    context=ctx,
                    model_id=model_id,
                    direction=str(direction),
                    adapter_origin=adapter_origin,
                    target_resolver=target_resolver,
                    causal_setup_override=causal_override,
                    production_eval_spec=spec_map,
                )
            else:
                base_snap = cls.evaluate_model_best_direction(
                    context=ctx,
                    model_id=model_id,
                    preferred_direction=s_cfg.get("preferred_direction"),
                    adapter_origin=adapter_origin,
                    target_resolver=target_resolver,
                    production_eval_spec=spec_map,
                )

            if spec_map is not None:
                return cls._execute_production_domain_spec(ctx, base_snap, spec_map)
            return base_snap
        except Exception as exc:
            now_val = int(evaluation_time or getattr(ctx, "decision_timestamp_ms", 0) or 0)
            eff_dir = str(direction) if direction in ("LONG", "SHORT") else "LONG"
            return cls._build_fail_closed_exception_snapshot(
                context=ctx,
                model_id=model_id,
                direction=eff_dir,
                now_ms=now_val,
                reason_code=f"CANONICAL_EVALUATOR_EXCEPTION:{type(exc).__name__}",
            )

    @classmethod
    def evaluate_production_reversal_candidate(
        cls,
        *,
        context: MarketDecisionContext,
        zone: WatchedZone,
        candles_1m: Sequence[Any],
        quote: Any,
        policy_settings: Dict[str, Any],
        now_ms: int,
        config: Any,
        context_levels: Sequence[Any],
        stop_policy: Any,
        stop_context: Dict[str, Any],
    ) -> Tuple[ScalpCandidate, StrategyDecisionSnapshot]:
        """
        Phase D / Section 3: Authoritative production reversal evaluation invoked inside
        ScalpWatcherOrchestrator.evaluate_cycle(). `cls.evaluate_decision(...)` is the sole
        decision producer; `enforce_canonical_decision_authority_on_candidate` enforces
        canonical DecisionResult invariants with fail-closed semantics.
        """
        z_dir = "LONG" if getattr(zone, "direction", "") in ("BULLISH", "LONG") else "SHORT"
        try:
            snap = cls.evaluate_decision(
                market_snapshot=context,
                structural_context={
                    "model_id": ModelIdentifier.SMC_M1_REVERSAL.value,
                    "direction": z_dir,
                    "_production_eval_spec": {
                        "eval_type": "REVERSAL",
                        "zone": zone,
                        "candles_1m": candles_1m,
                        "quote": quote,
                        "policy_settings": policy_settings,
                        "now_ms": now_ms,
                        "config": config,
                        "context_levels": context_levels,
                        "stop_policy": stop_policy,
                        "stop_context": stop_context,
                    },
                },
                strategy_config={
                    "model_id": ModelIdentifier.SMC_M1_REVERSAL.value,
                    "direction": z_dir,
                    "adapter_origin": "PRODUCTION_ORCHESTRATOR",
                },
                risk_context=policy_settings,
                evaluation_time=now_ms,
            )
        except Exception as exc:
            snap = cls._build_fail_closed_exception_snapshot(
                context=context,
                model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
                direction=z_dir,
                now_ms=now_ms,
                reason_code=f"CANONICAL_EVALUATOR_EXCEPTION:{type(exc).__name__}",
            )
        return cls.enforce_canonical_decision_authority_on_candidate(
            snap=snap,
            context=context,
            model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
            expected_direction=z_dir,
            policy_settings=policy_settings,
            config=config,
            now_ms=now_ms,
            zone=zone,
            strict_direction_match=True,
        )

    @classmethod
    def evaluate_production_continuation_candidate(
        cls,
        *,
        context: MarketDecisionContext,
        candles_1m: Sequence[Any],
        candles_5m: Sequence[Any],
        quote: Any,
        htf_bias: str,
        h1_alignment: str,
        policy_settings: Dict[str, Any],
        now_ms: int,
        config: Any,
        stop_policy: Any,
        stop_context: Dict[str, Any],
    ) -> Tuple[ScalpCandidate, StrategyDecisionSnapshot]:
        """
        Phase D / Section 3: Authoritative production continuation evaluation invoked inside
        ScalpWatcherOrchestrator.evaluate_cycle().
        """
        pref_dir = cls.resolve_canonical_preferred_direction(context, htf_bias)
        legacy_m1_only = len(context.closed_m5_candles) < 3
        enforce_bias = bool(context.get_policy_dict().get("enforce_htf_bias_alignment", False))
        strict_dir = bool(htf_bias in ("BULLISH", "BEARISH") and (legacy_m1_only or enforce_bias))
        try:
            snap = cls.evaluate_decision(
                market_snapshot=context,
                structural_context={
                    "model_id": ModelIdentifier.SMC_M1_CONTINUATION.value,
                    "direction": pref_dir if strict_dir else None,
                    "_production_eval_spec": {
                        "eval_type": "CONTINUATION",
                        "candles_1m": candles_1m,
                        "candles_5m": candles_5m,
                        "quote": quote,
                        "htf_bias": htf_bias,
                        "h1_alignment": h1_alignment,
                        "policy_settings": policy_settings,
                        "now_ms": now_ms,
                        "config": config,
                        "stop_policy": stop_policy,
                        "stop_context": stop_context,
                    },
                },
                strategy_config={
                    "model_id": ModelIdentifier.SMC_M1_CONTINUATION.value,
                    "direction": pref_dir if strict_dir else None,
                    "preferred_direction": pref_dir,
                    "adapter_origin": "PRODUCTION_ORCHESTRATOR",
                },
                risk_context=policy_settings,
                evaluation_time=now_ms,
            )
        except Exception as exc:
            snap = cls._build_fail_closed_exception_snapshot(
                context=context,
                model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
                direction=pref_dir,
                now_ms=now_ms,
                reason_code=f"CANONICAL_EVALUATOR_EXCEPTION:{type(exc).__name__}",
            )
        return cls.enforce_canonical_decision_authority_on_candidate(
            snap=snap,
            context=context,
            model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
            expected_direction=pref_dir if strict_dir else None,
            policy_settings=policy_settings,
            config=config,
            now_ms=now_ms,
            strict_direction_match=strict_dir,
        )

    @classmethod
    def evaluate_production_fvg_continuation_candidate(
        cls,
        *,
        context: MarketDecisionContext,
        closed_1m: Sequence[Any],
        closed_5m: Sequence[Any],
        quote: Any,
        htf_bias: str,
        h1_alignment: str,
        policy_settings: Dict[str, Any],
        now_ms: int,
        config: Any,
        context_levels: Sequence[Any],
        stop_policy: Any,
        stop_context: Dict[str, Any],
    ) -> Tuple[ScalpCandidate, StrategyDecisionSnapshot]:
        """
        Phase D / Section 3: Authoritative production FVG continuation evaluation invoked inside
        ScalpWatcherOrchestrator.evaluate_cycle().
        """
        pref_dir = cls.resolve_canonical_preferred_direction(context, htf_bias)
        legacy_m1_only = len(context.closed_m5_candles) < 3
        enforce_bias = bool(context.get_policy_dict().get("enforce_htf_bias_alignment", False))
        strict_dir = bool(htf_bias in ("BULLISH", "BEARISH") and (legacy_m1_only or enforce_bias))
        try:
            snap = cls.evaluate_decision(
                market_snapshot=context,
                structural_context={
                    "model_id": ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
                    "direction": pref_dir if strict_dir else None,
                    "_production_eval_spec": {
                        "eval_type": "FVG_CONTINUATION",
                        "closed_1m": closed_1m,
                        "closed_5m": closed_5m,
                        "quote": quote,
                        "htf_bias": htf_bias,
                        "h1_alignment": h1_alignment,
                        "policy_settings": policy_settings,
                        "now_ms": now_ms,
                        "config": config,
                        "context_levels": context_levels,
                        "stop_policy": stop_policy,
                        "stop_context": stop_context,
                    },
                },
                strategy_config={
                    "model_id": ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
                    "direction": pref_dir if strict_dir else None,
                    "preferred_direction": pref_dir,
                    "adapter_origin": "PRODUCTION_ORCHESTRATOR",
                },
                risk_context=policy_settings,
                evaluation_time=now_ms,
            )
        except Exception as exc:
            snap = cls._build_fail_closed_exception_snapshot(
                context=context,
                model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
                direction=pref_dir,
                now_ms=now_ms,
                reason_code=f"CANONICAL_EVALUATOR_EXCEPTION:{type(exc).__name__}",
            )
        return cls.enforce_canonical_decision_authority_on_candidate(
            snap=snap,
            context=context,
            model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
            expected_direction=pref_dir if strict_dir else None,
            policy_settings=policy_settings,
            config=config,
            now_ms=now_ms,
            strict_direction_match=strict_dir,
        )

    @classmethod
    def evaluate_production_sweep_reclaim_candidate(
        cls,
        *,
        context: MarketDecisionContext,
        closed_1m: Sequence[Any],
        cluster: Any,
        quote: Any,
        policy_settings: Dict[str, Any],
        now_ms: int,
        config: Any,
        context_levels: Sequence[Any],
        stop_policy: Any,
        stop_context: Dict[str, Any],
    ) -> Tuple[ScalpCandidate, StrategyDecisionSnapshot]:
        """
        Phase D / Section 3: Authoritative production sweep-reclaim evaluation invoked inside
        ScalpWatcherOrchestrator.evaluate_cycle().
        """
        c_dir = "LONG" if getattr(cluster, "side", "") == "SELL_SIDE" else "SHORT"
        try:
            snap = cls.evaluate_decision(
                market_snapshot=context,
                structural_context={
                    "model_id": ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
                    "direction": c_dir,
                    "_production_eval_spec": {
                        "eval_type": "SWEEP_RECLAIM",
                        "closed_1m": closed_1m,
                        "cluster": cluster,
                        "quote": quote,
                        "policy_settings": policy_settings,
                        "now_ms": now_ms,
                        "config": config,
                        "context_levels": context_levels,
                        "stop_policy": stop_policy,
                        "stop_context": stop_context,
                    },
                },
                strategy_config={
                    "model_id": ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
                    "direction": c_dir,
                    "adapter_origin": "PRODUCTION_ORCHESTRATOR",
                },
                risk_context=policy_settings,
                evaluation_time=now_ms,
            )
        except Exception as exc:
            snap = cls._build_fail_closed_exception_snapshot(
                context=context,
                model_id=ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
                direction=c_dir,
                now_ms=now_ms,
                reason_code=f"CANONICAL_EVALUATOR_EXCEPTION:{type(exc).__name__}",
            )
        return cls.enforce_canonical_decision_authority_on_candidate(
            snap=snap,
            context=context,
            model_id=ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
            expected_direction=c_dir,
            policy_settings=policy_settings,
            config=config,
            now_ms=now_ms,
            strict_direction_match=True,
        )

    @classmethod
    def evaluate_production_cg01_candidate(
        cls,
        *,
        context: MarketDecisionContext,
        closed_ctx: Dict[str, Any],
        quote: Any,
        config: Any,
        now_ms: int,
        capital: float,
        risk_pct: float,
        costs: Any,
        stop_policy: Any,
        stop_context: Dict[str, Any],
        eval_fn: Optional[Callable[..., Any]] = None,
    ) -> Tuple[Any, StrategyDecisionSnapshot]:
        """
        Phase D / Section 3: Authoritative production CG-01 evaluation invoked inside
        ScalpWatcherOrchestrator.evaluate_cycle().
        """
        pref_dir = "LONG" if context.htf_bias == "BULLISH" else ("SHORT" if context.htf_bias == "BEARISH" else None)
        try:
            snap = cls.evaluate_decision(
                market_snapshot=context,
                structural_context={
                    "model_id": ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                    "_production_eval_spec": {
                        "eval_type": "CG01",
                        "closed_ctx": closed_ctx,
                        "quote": quote,
                        "config": config,
                        "now_ms": now_ms,
                        "capital": capital,
                        "risk_pct": risk_pct,
                        "costs": costs,
                        "stop_policy": stop_policy,
                        "stop_context": stop_context,
                        "eval_fn": eval_fn,
                    },
                },
                strategy_config={
                    "model_id": ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                    "preferred_direction": pref_dir,
                    "adapter_origin": "PRODUCTION_ORCHESTRATOR",
                },
                risk_context={"capital_usdt": capital, "risk_pct": risk_pct},
                evaluation_time=now_ms,
            )
        except Exception as exc:
            snap = cls._build_fail_closed_exception_snapshot(
                context=context,
                model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
                direction=pref_dir or "LONG",
                now_ms=now_ms,
                reason_code=f"CANONICAL_EVALUATOR_EXCEPTION:{type(exc).__name__}",
            )
        return cls.enforce_canonical_decision_authority_on_cg01(
            snap=snap,
            context=context,
            capital=capital,
            risk_pct=risk_pct,
            config=config,
            now_ms=now_ms,
        )


def evaluate_decision(
    market_snapshot: MarketDecisionContext,
    structural_context: Optional[Mapping[str, Any]] = None,
    strategy_config: Optional[Mapping[str, Any]] = None,
    risk_context: Optional[Mapping[str, Any]] = None,
    evaluation_time: Optional[int] = None,
) -> DecisionResult:
    """
    Phase C: Top-level canonical decision evaluator function.
    """
    return SharedStrategyEvaluator.evaluate_decision(
        market_snapshot=market_snapshot,
        structural_context=structural_context,
        strategy_config=strategy_config,
        risk_context=risk_context,
        evaluation_time=evaluation_time,
    )


def characterize_production_baseline_decision(
    context: MarketDecisionContext,
    model_id: str,
    direction: str,
) -> StrategyDecisionSnapshot:
    """
    Phase B / Section 8.1 Stage 1: Independent pre-refactor baseline characterization.
    Executes `IndependentPreRefactorBaselineOracle` (recovered from pre-V17.9.0 commit
    `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`) without calling `SharedStrategyEvaluator`.
    """
    from services.v17_9_0_1_baseline_oracle import IndependentPreRefactorBaselineOracle

    raw_dict = IndependentPreRefactorBaselineOracle.evaluate_baseline_dict(
        context=context,
        model_id=model_id,
        direction=direction,
    )
    return StrategyDecisionSnapshot(
        model_id=raw_dict["model_id"],
        symbol=raw_dict["symbol"],
        decision_timestamp_ms=raw_dict["decision_timestamp_ms"],
        direction=raw_dict["direction"],
        setup_validity=raw_dict["setup_validity"],
        entry_reference=raw_dict["entry_reference"],
        entry_zone_low=raw_dict["entry_zone_low"],
        entry_zone_high=raw_dict["entry_zone_high"],
        selected_fvg_id=raw_dict["selected_fvg_id"],
        selected_break_id=raw_dict["selected_break_id"],
        selected_sweep_id=raw_dict["selected_sweep_id"],
        setup_episode_id=raw_dict["setup_episode_id"],
        unicorn_validity=raw_dict["unicorn_validity"],
        unicorn_failure_stage=raw_dict["unicorn_failure_stage"],
        selected_anchor_id=raw_dict["selected_anchor_id"],
        anchor_kind=raw_dict["anchor_kind"],
        structural_invalidation_price=raw_dict["structural_invalidation_price"],
        buffer_value=raw_dict["buffer_value"],
        selected_sl=raw_dict["selected_sl"],
        legacy_sl=raw_dict["legacy_sl"],
        take_profit=raw_dict["take_profit"],
        selected_target_id=raw_dict["selected_target_id"],
        atr_m1=raw_dict["atr_m1"],
        atr_m5=raw_dict["atr_m5"],
        spread=raw_dict["spread"],
        hypothetical_quantity=raw_dict["hypothetical_quantity"],
        stop_distance_price=raw_dict["stop_distance_price"],
        target_distance_price=raw_dict["target_distance_price"],
        gross_rr=raw_dict["gross_rr"],
        net_rr=raw_dict["net_rr"],
        decision_status=raw_dict["decision_status"],
        rejection_reason_codes=tuple(raw_dict["rejection_reason_codes"]),
        evidence_fingerprint=raw_dict["evidence_fingerprint"],
        policy_fingerprint=raw_dict["policy_fingerprint"],
        decision_fingerprint=raw_dict["decision_fingerprint"],
        adapter_origin="BASELINE_DIRECT_PRODUCTION",
        raw_candidate_reason_code=raw_dict.get("raw_candidate_reason_code"),
        candidate_key=raw_dict.get("candidate_key"),
        unicorn_evidence_dict=raw_dict.get("unicorn_evidence_dict"),
        stop_comparison_plan_dict=raw_dict.get("stop_comparison_plan_dict"),
        unicorn_sl_candidate=raw_dict.get("unicorn_sl_candidate"),
        protective_distance=raw_dict.get("protective_distance"),
        selected_breaker_id=raw_dict.get("selected_breaker_id"),
    )


def _snap_get(obj: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(field_name, default)
    return getattr(obj, field_name, default)


def _resolve_parity_hash(obj: Any, primary_key: str, fallback_key: str) -> Optional[str]:
    if isinstance(obj, Mapping):
        if primary_key in obj:
            val = obj.get(primary_key)
            return str(val).strip() if val is not None and str(val).strip() else None
        val = obj.get(fallback_key)
        return str(val).strip() if val is not None and str(val).strip() else None
    val = getattr(obj, primary_key, None)
    if val is not None and str(val).strip():
        return str(val).strip()
    val = getattr(obj, fallback_key, None)
    return str(val).strip() if val is not None and str(val).strip() else None


def compare_decision_parity(
    left: Any,
    right: Any,
    *,
    float_tolerance: float = 1e-6,
    allow_same_adapter_origin: bool = False,
    check_input_config_hashes: bool = True,
) -> Dict[str, Any]:
    """
    Section 8.2 / 8.3 & V17.9.0.1.2 Phase E: Compares two StrategyDecisionSnapshots (or parity dicts)
    across all 20 required parity fields, input/config hashes, and anti-fake parity controls.
    Never treats two null/empty input or configuration hashes as equal.
    """
    mismatches: List[Dict[str, Any]] = []
    left_origin = _snap_get(left, "adapter_origin")
    right_origin = _snap_get(right, "adapter_origin")

    if not allow_same_adapter_origin and left_origin == right_origin:
        mismatches.append({
            "field": "adapter_origin",
            "left": left_origin,
            "right": right_origin,
            "reason": "SELF_COMPARISON_TRAP_DETECTED",
        })

    left_in_hash = _resolve_parity_hash(left, "input_snapshot_hash", "evidence_fingerprint")
    right_in_hash = _resolve_parity_hash(right, "input_snapshot_hash", "evidence_fingerprint")
    left_cfg_hash = _resolve_parity_hash(left, "configuration_hash", "policy_fingerprint")
    right_cfg_hash = _resolve_parity_hash(right, "configuration_hash", "policy_fingerprint")

    input_mismatch = False
    if check_input_config_hashes:
        if not left_in_hash or not right_in_hash:
            input_mismatch = True
            mismatches.append({
                "field": "input_snapshot_hash",
                "left": left_in_hash,
                "right": right_in_hash,
                "reason": "NULL_OR_MISSING_INPUT_HASH",
            })
        elif left_in_hash != right_in_hash:
            input_mismatch = True
            mismatches.append({
                "field": "input_snapshot_hash",
                "left": left_in_hash,
                "right": right_in_hash,
                "reason": "INPUT_MISMATCH",
            })

        if not left_cfg_hash or not right_cfg_hash:
            input_mismatch = True
            mismatches.append({
                "field": "configuration_hash",
                "left": left_cfg_hash,
                "right": right_cfg_hash,
                "reason": "NULL_OR_MISSING_CONFIG_HASH",
            })
        elif left_cfg_hash != right_cfg_hash:
            input_mismatch = True
            mismatches.append({
                "field": "configuration_hash",
                "left": left_cfg_hash,
                "right": right_cfg_hash,
                "reason": "CONFIG_MISMATCH",
            })

    for field_name in REQUIRED_PARITY_FIELDS:
        l_val = _snap_get(left, field_name)
        r_val = _snap_get(right, field_name)

        if isinstance(l_val, float) and isinstance(r_val, float):
            if abs(l_val - r_val) > float_tolerance:
                mismatches.append({
                    "field": field_name,
                    "left": l_val,
                    "right": r_val,
                    "reason": "FLOAT_MISMATCH",
                })
        elif field_name == "rejection_reason_codes":
            if tuple(l_val or ()) != tuple(r_val or ()):
                mismatches.append({
                    "field": field_name,
                    "left": list(l_val or ()),
                    "right": list(r_val or ()),
                    "reason": "REJECTION_REASONS_MISMATCH",
                })
        else:
            if l_val != r_val:
                mismatches.append({
                    "field": field_name,
                    "left": l_val,
                    "right": r_val,
                    "reason": "VALUE_MISMATCH",
                })

    left_fp = _snap_get(left, "decision_fingerprint")
    right_fp = _snap_get(right, "decision_fingerprint")
    if left_fp != right_fp:
        mismatches.append({
            "field": "decision_fingerprint",
            "left": left_fp,
            "right": right_fp,
            "reason": "FINGERPRINT_MISMATCH",
        })

    is_match = len(mismatches) == 0
    return {
        "parity_status": "PARITY_MATCH" if is_match else ("INPUT_MISMATCH" if input_mismatch else "PRODUCTION_PARITY_MISMATCH"),
        "is_match": is_match,
        "input_mismatch": input_mismatch,
        "model_id": _snap_get(left, "model_id"),
        "direction": _snap_get(left, "direction"),
        "decision_timestamp_ms": _snap_get(left, "decision_timestamp_ms"),
        "left_adapter_origin": left_origin,
        "right_adapter_origin": right_origin,
        "left_fingerprint": left_fp,
        "right_fingerprint": right_fp,
        "left_input_snapshot_hash": left_in_hash,
        "right_input_snapshot_hash": right_in_hash,
        "left_configuration_hash": left_cfg_hash,
        "right_configuration_hash": right_cfg_hash,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "divergences": {m["field"]: m for m in mismatches},
    }


def verify_multi_adapter_parity(
    *,
    baseline_snapshot: StrategyDecisionSnapshot,
    shared_snapshot: StrategyDecisionSnapshot,
    production_snapshot: StrategyDecisionSnapshot,
    shadow_snapshot: StrategyDecisionSnapshot,
    backtest_snapshot: StrategyDecisionSnapshot,
) -> Dict[str, Any]:
    """
    Section 8.2 Stage 2: Cross-adapter parity verification across
    Baseline Production, Shared Evaluator, Production Adapter, Shadow Adapter,
    and Backtest Adapter.
    """
    comparisons = {
        "baseline_vs_shared": compare_decision_parity(baseline_snapshot, shared_snapshot),
        "shared_vs_production": compare_decision_parity(shared_snapshot, production_snapshot),
        "shared_vs_shadow": compare_decision_parity(shared_snapshot, shadow_snapshot),
        "shared_vs_backtest": compare_decision_parity(shared_snapshot, backtest_snapshot),
        "production_vs_shadow": compare_decision_parity(production_snapshot, shadow_snapshot),
        "production_vs_backtest": compare_decision_parity(production_snapshot, backtest_snapshot),
        "shadow_vs_backtest": compare_decision_parity(shadow_snapshot, backtest_snapshot),
    }
    all_match = all(c["is_match"] for c in comparisons.values())
    total_mismatches = sum(c["mismatch_count"] for c in comparisons.values())
    return {
        "parity_status": "PARITY_MATCH" if all_match else "PRODUCTION_PARITY_MISMATCH",
        "is_match": all_match,
        "total_mismatch_count": total_mismatches,
        "comparisons": comparisons,
    }


def verify_no_synthetic_index_signals_in_source(
    file_paths: Optional[Sequence[Path]] = None,
) -> Dict[str, Any]:
    """
    Section 8.6 #3 & #4 / Section 9.1: Inspects AST of backtest and evaluator modules
    to verify zero synthetic modulo index-based setup triggers (e.g. `i % 15 == 0`
    or `(i // 15) % 2 == 0`).
    """
    repo_root = Path(__file__).resolve().parents[2]
    targets = list(file_paths) if file_paths else [
        repo_root / "backend" / "services" / "v17_8_backtest_runner.py",
        repo_root / "backend" / "services" / "shared_strategy_evaluator.py",
    ]
    violations: List[Dict[str, Any]] = []
    forbidden_patterns = (
        "i % 15 == 0",
        "(i // 15) % 2 == 0",
        "idx % 15 == 0",
        "(idx // 15) % 2 == 0",
    )

    for path in targets:
        if not path.exists():
            violations.append({"file": str(path), "reason": "FILE_NOT_FOUND"})
            continue
        source = path.read_text(encoding="-utf-8" if False else "utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                segment = ast.get_source_segment(source, node) or ""
                normalized = " ".join(segment.split())
                for pat in forbidden_patterns:
                    if pat in normalized:
                        violations.append({
                            "file": str(path),
                            "line": getattr(node, "lineno", 0),
                            "segment": normalized,
                            "reason": "SYNTHETIC_INDEX_SIGNAL_DETECTED",
                        })

    return {
        "is_clean": len(violations) == 0,
        "status": "CLEAN" if len(violations) == 0 else "PRODUCTION_PARITY_MISMATCH",
        "violation_count": len(violations),
        "violations": violations,
    }
