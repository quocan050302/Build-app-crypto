"""
backend/services/smc_scenario_service.py
V14.4.1 — Causal SMC scenario engine (closed bars -> immutable scenario snapshots).

Pure orchestration in `evaluate_scenarios` (no DB, no network, injected clock). `evaluate_scenario_cycle`
adds persistence (flush only: the caller owns commit + event publication). Every relation between
evidence nodes is MEASURED from candles before links are written:
  sweep -> break (after sweep, prefix-ATR displacement, close beyond a pre-sweep swing)
        -> FVG (created by the break's leg; real bounds) -> retest (closed bar after FVG confirmation).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import smc_engine
from sqlalchemy.orm import Session

from services.ny_quota_helpers import closed_candles_at, validate_candle_ohlc
from services.smc_chain_service import (
    bind_fvg_to_leg, confirm_closed_retest, prefix_atr, qualify_displacement, validate_reversal_chain,
)
from services.smc_liquidity_service import (
    build_liquidity_map, select_observation_anchor, select_unconsumed_target, update_liquidity_states,
)
from services.smc_regime_service import atr_relative, build_htf_context, classify_regime, h1_alignment_from
from services.smc_scenario_config import ScenarioConfig, active_scenario_config
from services.smc_scenario_plan import FinancialSettings, build_scenario_plan
from services.smc_scenario_repository import get_previous_scenarios, upsert_scenario_revision
from services.smc_scenario_summary import build_scenario_summary_vi
from services.smc_scenario_types import (
    EvidenceNode, FactStatus, LiquidityLevel, ScenarioSnapshot, ScenarioState, TERMINAL_STATES,
    make_evidence_id, make_scenario_identity,
)
from services.smc_session_context import (
    NY_TZ, PreviousDayLevels, SESSION_CONFIGS, SessionRange, build_previous_day_levels,
    build_previous_ny_day_levels_from_m5, build_session_range, get_session_window_bounds, ny_date_str,
)

logger = logging.getLogger("smc_scenario_service")

TF_DURATIONS_MS = {
    "1M": 60_000, "5M": 5 * 60_000, "15M": 15 * 60_000,
    "1H": 60 * 60_000, "4H": 4 * 60 * 60_000, "D": 24 * 60 * 60_000,
}
SETUP_TF = "5M"
CONTEXT_TFS = ("D", "4H", "1H", "15M")
REQUIRED_TFS = ("5M",)
RECENT_COVERAGE_WINDOW = 60          # closed M5 bars inspected for holes
RECENT_COVERAGE_MIN_RATIO = 0.90
M5_MS = TF_DURATIONS_MS["5M"]


# ===================================================================================================
# Context (fail closed, typed issues)
# ===================================================================================================
@dataclass(frozen=True)
class ContextIssue:
    timeframe: str
    code: str                      # NO_CANDLES | CANDLE_DATA_CORRUPT | INVALID_OHLC | INSUFFICIENT_BARS | STALE | DATA_GAPS
    detail: str
    required: bool

    def to_dict(self) -> Dict[str, Any]:
        return {"timeframe": self.timeframe, "code": self.code, "detail": self.detail, "required": self.required}


@dataclass
class ClosedContext:
    """Closed, sorted, deduped candles per timeframe at `now_ms` plus every data issue found."""
    now_ms: int
    candles: Dict[str, List[Any]] = field(default_factory=dict)
    issues: List[ContextIssue] = field(default_factory=list)
    coverage: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def get(self, tf: str) -> List[Any]:
        return self.candles.get(tf, [])

    @property
    def blocking_issues(self) -> List[ContextIssue]:
        return [i for i in self.issues if i.required]

    def usable(self, tf: str) -> bool:
        return bool(self.candles.get(tf)) and not any(i.timeframe == tf and i.code in
                                                      ("CANDLE_DATA_CORRUPT", "INVALID_OHLC") for i in self.issues)


def validate_context_coverage(
    candles: Sequence[Any], timeframe: str, now_ms: int, *, min_bars: int = 20,
    max_stale_bars: int = 3, required: bool = True,
) -> Dict[str, Any]:
    """Coverage of an ALREADY closed/sorted/deduped series: expected vs observed bars in the window, holes
    in the most recent window, and staleness of the last bar. Returns {is_valid, issues:[...], ...}."""
    dur = TF_DURATIONS_MS[timeframe]
    out: Dict[str, Any] = {"is_valid": True, "issues": [], "bar_count": len(candles), "last_timestamp": None,
                           "expected_bars": 0, "gap_bars": 0, "coverage_ratio": 0.0}
    if not candles:
        out["is_valid"] = False
        out["issues"].append(ContextIssue(timeframe, "NO_CANDLES", "no closed candles", required))
        return out
    first, last = candles[0].timestamp, candles[-1].timestamp
    out["last_timestamp"] = last
    expected = (last - first) // dur + 1
    out["expected_bars"], out["gap_bars"] = expected, max(0, expected - len(candles))
    out["coverage_ratio"] = round(len(candles) / expected, 4) if expected else 0.0
    if len(candles) < min_bars:
        out["is_valid"] = False
        out["issues"].append(ContextIssue(timeframe, "INSUFFICIENT_BARS", f"{len(candles)} < {min_bars}", required))
    stale_by = now_ms - (last + dur)
    if stale_by > max_stale_bars * dur:
        out["is_valid"] = False
        out["issues"].append(ContextIssue(timeframe, "STALE", f"last closed bar is {stale_by // 60000} min old", required))
    recent = candles[-RECENT_COVERAGE_WINDOW:]
    r_expected = (recent[-1].timestamp - recent[0].timestamp) // dur + 1
    r_ratio = len(recent) / r_expected if r_expected else 0.0
    if len(recent) >= 2 and r_ratio < RECENT_COVERAGE_MIN_RATIO:
        out["is_valid"] = False
        out["issues"].append(ContextIssue(timeframe, "DATA_GAPS",
                                          f"recent coverage {r_ratio:.2f} < {RECENT_COVERAGE_MIN_RATIO}", required))
    return out


def load_closed_context(candles_by_tf: Dict[str, List[Any]], now_ms: int, config: ScenarioConfig) -> ClosedContext:
    """Closed bars per timeframe. A malformed/duplicate-conflicting series is a typed DATA issue for that
    timeframe (the series is dropped) - it is never lightly filtered and silently used as valid data."""
    ctx = ClosedContext(now_ms=now_ms)
    for tf in ("D", "4H", "1H", "15M", "5M"):
        required = tf in REQUIRED_TFS
        raw = candles_by_tf.get(tf) or []
        try:
            closed = closed_candles_at(raw, now_ms=now_ms, duration_ms=TF_DURATIONS_MS[tf])
        except ValueError as e:
            ctx.candles[tf] = []
            ctx.issues.append(ContextIssue(tf, "CANDLE_DATA_CORRUPT", str(e), required))
            continue
        bad = validate_candle_ohlc(closed)
        if bad:
            ctx.candles[tf] = []
            ctx.issues.append(ContextIssue(tf, "INVALID_OHLC", bad, required))
            continue
        ctx.candles[tf] = closed
        if not raw and not required:
            ctx.issues.append(ContextIssue(tf, "NO_CANDLES", "optional timeframe missing", False))
            continue
        min_bars = config.min_atr_bars if tf == "5M" else 3
        cov = validate_context_coverage(closed, tf, now_ms, min_bars=min_bars,
                                        max_stale_bars=config.max_context_staleness_bars, required=required)
        ctx.coverage[tf] = {k: v for k, v in cov.items() if k != "issues"}
        ctx.issues.extend(cov["issues"])
    return ctx


# ===================================================================================================
# Sessions
# ===================================================================================================
def session_for(now_ms: int) -> Tuple[str, str]:
    """(current session name | 'OFF', NY trading date) from the injected clock. From 19:00 NY the next
    NY date's Asia session has started."""
    from datetime import datetime, timedelta
    dt = datetime.fromtimestamp(now_ms / 1000.0, tz=NY_TZ)
    trading_date = dt + timedelta(days=1) if dt.hour >= 19 else dt
    date_str = trading_date.strftime("%Y-%m-%d")
    for name in ("NY", "LONDON", "ASIA"):
        start, end = get_session_window_bounds(name, date_str)
        if start <= now_ms < end:
            return name, date_str
    return "OFF", date_str


def build_session_context(ctx: ClosedContext, now_ms: int, config: ScenarioConfig) -> Dict[str, Any]:
    name, date_str = session_for(now_ms)
    m5 = ctx.get("5M")
    ranges = {s: build_session_range(s, date_str, m5, now_ms, M5_MS, config.min_session_coverage_ratio)
              for s in ("ASIA", "LONDON", "NY")}
    pd = build_previous_day_levels(date_str, ctx.get("D"), now_ms)
    if not pd.is_valid:
        alt = build_previous_ny_day_levels_from_m5(date_str, m5, now_ms)
        pd = alt if alt.is_valid else pd
    return {"session_name": name, "date": date_str, "ranges": ranges, "previous_day": pd}


# ===================================================================================================
# Adapters
# ===================================================================================================
def adapt_detected_fvg(raw: Dict[str, Any], *, symbol: str, timeframe: str, as_of_ms: int,
                       candle_duration_ms: int = M5_MS) -> EvidenceNode:
    """smc_engine.detect_fvgs output (type BULLISH_FVG/BEARISH_FVG) -> typed EvidenceNode with EXACT
    top/bottom and the 3 creator candle refs. Raises ValueError on malformed geometry/times."""
    direction = {"BULLISH_FVG": "BULLISH", "BEARISH_FVG": "BEARISH"}.get(raw.get("type"))
    if direction is None:
        raise ValueError("UNKNOWN_FVG_TYPE")
    top, bottom = raw.get("top"), raw.get("bottom")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (top, bottom)):
        raise ValueError("MISSING_FVG_GEOMETRY")
    if bottom >= top:
        raise ValueError("INVALID_FVG_GEOMETRY")
    occurred, known = raw.get("timestamp"), raw.get("confirmed_at")
    if (type(occurred) is not int or type(known) is not int or occurred <= 0 or known < occurred or known > as_of_ms):
        raise ValueError("FVG_TIME_INVALID")
    if not isinstance(raw.get("id"), str) or not raw["id"]:
        raise ValueError("FVG_ID_MISSING")
    return EvidenceNode(
        id=raw["id"], kind="FVG", symbol=symbol, timeframe=timeframe, direction=direction,
        occurred_at_ms=occurred, known_at_ms=known, level=None, source_candle_open_ms=occurred,
        extra={"top": float(top), "bottom": float(bottom), "state": raw.get("state", "UNKNOWN"),
               "mitigated": bool(raw.get("mitigated", False)),
               "first_candle_open_ms": occurred - candle_duration_ms, "middle_candle_open_ms": occurred,
               "third_candle_open_ms": occurred + candle_duration_ms, "confirmed_at_ms": known},
    )


def detect_level_sweeps(levels: Sequence[LiquidityLevel], candles: Sequence[Any], *, symbol: str,
                        timeframe: str = SETUP_TF, dur: int = M5_MS) -> List[Dict[str, Any]]:
    """Sweeps of SESSION/PD levels (the engine itself only sweeps swings): a CLOSED bar after the level was
    known that wicked beyond it and closed back inside. Same dict shape as engine sweep events."""
    out: List[Dict[str, Any]] = []
    for lv in levels:
        if not (lv.source_kind.startswith("SESSION_") or lv.source_kind in ("PDH", "PDL")):
            continue
        for c in candles:
            if c.timestamp < lv.known_at_ms or getattr(c, "is_closed", None) is not True:
                continue
            if lv.side == "BUY_SIDE" and c.close > lv.price:
                break                                  # level broken, no later sweep of it
            if lv.side == "SELL_SIDE" and c.close < lv.price:
                break
            if lv.side == "BUY_SIDE" and c.high > lv.price and c.close < lv.price:
                out.append({"id": f"sweep-high-{timeframe}-{lv.id}-{c.timestamp}", "event_type": "SWEEP",
                            "type": "SWEEP_HIGH", "level": lv.price, "source_swing_id": lv.id,
                            "wick_extreme": c.high, "close_back": c.close, "timestamp": c.timestamp,
                            "confirmed_at": c.timestamp + dur, "level_label": lv.label_vi, "level_kind": lv.source_kind})
                break
            if lv.side == "SELL_SIDE" and c.low < lv.price and c.close > lv.price:
                out.append({"id": f"sweep-low-{timeframe}-{lv.id}-{c.timestamp}", "event_type": "SWEEP",
                            "type": "SWEEP_LOW", "level": lv.price, "source_swing_id": lv.id,
                            "wick_extreme": c.low, "close_back": c.close, "timestamp": c.timestamp,
                            "confirmed_at": c.timestamp + dur, "level_label": lv.label_vi, "level_kind": lv.source_kind})
                break
    return out


def build_mtf_chain(*, direction: str, m15_candles: Sequence[Any], sweep: EvidenceNode, fvg: EvidenceNode,
                    symbol: str, as_of_ms: int) -> Tuple[Optional[EvidenceNode], List[str]]:
    """M15 point of interest: a same-direction M15 FVG KNOWN BEFORE the M5 sweep candle whose zone overlaps
    the M5 FVG zone. Returns (POI node | None, advisories). The M5 trigger is validated against it."""
    if len(m15_candles) < 3:
        return None, ["M15_DATA_UNAVAILABLE"]
    want = "BULLISH_FVG" if direction == "LONG" else "BEARISH_FVG"
    zt, zb = fvg.extra["top"], fvg.extra["bottom"]
    best: Optional[Dict[str, Any]] = None
    for raw in smc_engine.detect_fvgs(list(m15_candles), timeframe="15M"):
        if raw.get("type") != want or raw["confirmed_at"] > sweep.occurred_at_ms or raw["confirmed_at"] > as_of_ms:
            continue
        if raw["bottom"] <= zt and raw["top"] >= zb and not raw.get("mitigated"):
            best = raw
    if best is None:
        return None, ["M15_POI_ABSENT"]
    node = adapt_detected_fvg(best, symbol=symbol, timeframe="15M", as_of_ms=as_of_ms, candle_duration_ms=TF_DURATIONS_MS["15M"])
    node = replace(node, kind="POI", extra={**node.extra, "poi_kind": "M15_FVG",
                                           "contains_trigger_zone": True})
    return node, []


# ===================================================================================================
# Expiry / invalidation / transitions / selection
# ===================================================================================================
def compute_scenario_expiry(as_of_ms: int, config: ScenarioConfig, ny_session_end_ms: Optional[int] = None,
                            setup_timeframe: str = SETUP_TF) -> int:
    """Expiry from the MODEL timeframe (not a hardcoded 5M) and TTL; capped by the session end if given."""
    ttl = config.scenario_expiry_bars * TF_DURATIONS_MS[setup_timeframe]
    candidate = as_of_ms + ttl
    if ny_session_end_ms is not None and ny_session_end_ms > as_of_ms:
        return min(candidate, ny_session_end_ms)
    return candidate


def evaluate_scenario_invalidation(scenario: ScenarioSnapshot, last_closed_price: Optional[float]
                                   ) -> Tuple[Optional[bool], Optional[str]]:
    """Invalidation by a validated CLOSED price versus the stored invalidation (wick extreme). Returns
    (None, 'UNKNOWN_PRICE') when there is no valid price: unknown is not 'still valid'."""
    if scenario.state in TERMINAL_STATES:
        return True, "ALREADY_TERMINAL"
    inv = (scenario.invalidation or {}).get("price")
    if not isinstance(last_closed_price, (int, float)) or not math.isfinite(last_closed_price) or last_closed_price <= 0:
        return None, "UNKNOWN_PRICE"
    if not isinstance(inv, (int, float)) or inv <= 0:
        return False, None
    if scenario.direction == "LONG" and last_closed_price < inv:
        return True, f"PRICE_VIOLATED_INVALIDATION_LOW: {last_closed_price:.2f} < {inv:.2f}"
    if scenario.direction == "SHORT" and last_closed_price > inv:
        return True, f"PRICE_VIOLATED_INVALIDATION_HIGH: {last_closed_price:.2f} > {inv:.2f}"
    return False, None


_ALLOWED: Dict[ScenarioState, Set[ScenarioState]] = {
    ScenarioState.OBSERVING: {ScenarioState.WAITING_LIQUIDITY, ScenarioState.WAITING_SWEEP, ScenarioState.BLOCKED},
    ScenarioState.WAITING_LIQUIDITY: {ScenarioState.WAITING_SWEEP, ScenarioState.BLOCKED},
    ScenarioState.WAITING_SWEEP: {ScenarioState.WAITING_BREAK, ScenarioState.BLOCKED},
    ScenarioState.WAITING_BREAK: {ScenarioState.WAITING_FVG, ScenarioState.BLOCKED},
    ScenarioState.WAITING_FVG: {ScenarioState.WAITING_RETEST, ScenarioState.BLOCKED},
    ScenarioState.WAITING_RETEST: {ScenarioState.STRUCTURE_CONFIRMED, ScenarioState.BLOCKED},
    ScenarioState.STRUCTURE_CONFIRMED: {ScenarioState.BLOCKED},
    ScenarioState.BLOCKED: {ScenarioState.STRUCTURE_CONFIRMED, ScenarioState.WAITING_RETEST},
}
_ANYTIME_TERMINAL = {ScenarioState.INVALIDATED, ScenarioState.EXPIRED, ScenarioState.SUPERSEDED}


def transition_scenario(current: ScenarioSnapshot, next_state: ScenarioState, as_of_ms: int,
                        reason: Optional[str] = None) -> ScenarioSnapshot:
    """Next revision of `current` in `next_state`. Terminal states never transition (no resurrection).
    A non-forward move is NOT silently turned into INVALIDATED: it is accepted only when it is a legal
    forward/terminal move, otherwise the current snapshot is returned unchanged with a diagnostic blocker."""
    if current.state in TERMINAL_STATES:
        return current
    if next_state != current.state and next_state not in _ANYTIME_TERMINAL \
            and next_state not in _ALLOWED.get(current.state, set()):
        blockers = list(current.blockers)
        diag = f"ILLEGAL_TRANSITION_REJECTED:{current.state.value}->{next_state.value}"
        if diag not in blockers:
            blockers.append(diag)
        return replace(current, blockers=blockers, digest="")
    blockers = list(current.blockers)
    if reason and reason not in blockers:
        blockers.append(reason)
    nxt = replace(current, revision=current.revision + 1, state=next_state, as_of_ms=as_of_ms,
                  evidence_cutoff_ms=as_of_ms, blockers=blockers, digest="")
    nxt.digest = nxt.compute_digest()
    return nxt


_STATE_PRIORITY = {
    ScenarioState.STRUCTURE_CONFIRMED: 10, ScenarioState.WAITING_RETEST: 9, ScenarioState.WAITING_FVG: 8,
    ScenarioState.WAITING_BREAK: 7, ScenarioState.WAITING_SWEEP: 6, ScenarioState.WAITING_LIQUIDITY: 5,
    ScenarioState.OBSERVING: 4, ScenarioState.BLOCKED: 3,
    ScenarioState.INVALIDATED: 1, ScenarioState.EXPIRED: 0, ScenarioState.SUPERSEDED: 0,
}


def _plan_ok(s: ScenarioSnapshot) -> int:
    return 1 if isinstance(s.plan, dict) and s.plan.get("is_valid") else 0


def select_primary_scenario(scenarios: Sequence[ScenarioSnapshot]) -> Optional[ScenarioSnapshot]:
    """Strongest EVIDENCE wins: state progression, then amount of evidence, then executable plan. Newest
    revision is never a tiebreaker for strength. Two undecided opposite scenarios with no evidence return
    None (UNKNOWN) rather than inventing a direction."""
    if not scenarios:
        return None
    active = [s for s in scenarios if s.state not in TERMINAL_STATES]
    pool = active or list(scenarios)
    ranked = sorted(pool, key=lambda s: (_STATE_PRIORITY.get(s.state, 0), len(s.evidence), _plan_ok(s)), reverse=True)
    top = ranked[0]
    if len(ranked) > 1 and top.direction != ranked[1].direction:
        k0 = (_STATE_PRIORITY.get(top.state, 0), len(top.evidence), _plan_ok(top))
        k1 = (_STATE_PRIORITY.get(ranked[1].state, 0), len(ranked[1].evidence), _plan_ok(ranked[1]))
        if k0 == k1 and top.state in (ScenarioState.OBSERVING, ScenarioState.WAITING_SWEEP, ScenarioState.WAITING_LIQUIDITY):
            return None
    return top


def compute_scenario_proximity(scenario: ScenarioSnapshot, current_price: Optional[float], atr: Optional[float] = None
                               ) -> Dict[str, Any]:
    """LIVE telemetry (never part of the frozen decision snapshot)."""
    if current_price is None or current_price <= 0:
        return {"distance_price": None, "distance_atr": None}
    ref = None
    if scenario.state == ScenarioState.STRUCTURE_CONFIRMED and isinstance(scenario.plan, dict):
        ref = scenario.plan.get("planned_entry")
    elif isinstance((scenario.liquidity_map or {}).get("anchor"), dict):
        ref = scenario.liquidity_map["anchor"].get("price")
    if not ref:
        return {"distance_price": None, "distance_atr": None}
    dist = round(abs(current_price - ref), 2)
    return {"distance_price": dist, "distance_atr": round(dist / atr, 2) if atr and atr > 0 else None,
            "current_price": current_price, "target_level": ref}


# ===================================================================================================
# Evaluation
# ===================================================================================================
def _htf_regime_text(bias: str) -> str:
    return {"BULLISH": "TREND_UP", "BEARISH": "TREND_DOWN", "RANGE": "RANGE"}.get(bias, "UNKNOWN")


def _data_unavailable_snapshot(symbol: str, ctx: ClosedContext, config: ScenarioConfig, now_ms: int,
                               reasons: List[str]) -> ScenarioSnapshot:
    name, date_str = session_for(now_ms)
    session_id = f"{symbol}:{name}:{date_str}"
    scenario_id, instance_id = make_scenario_identity(symbol, session_id, config.family, None, "data-unavailable")
    snap = ScenarioSnapshot(
        scenario_id=scenario_id, instance_id=instance_id, revision=1, symbol=symbol, setup_timeframe=SETUP_TF,
        context_timeframes=list(CONTEXT_TFS), family=config.family, direction=None, state=ScenarioState.OBSERVING,
        as_of_ms=now_ms, evidence_cutoff_ms=now_ms, evidence_ids=[], liquidity_map={}, trigger_chain={
            "verified": False, "reasons": reasons, "node_ids": {}},
        target=None, invalidation=None, summary_vi="Dữ liệu nến chưa đủ để đọc thị trường: " + ", ".join(reasons),
        config_version=config.config_version, detector_version=config.detector_version, blockers=list(reasons),
        session_name=name, session_id=session_id, context={"data_issues": [i.to_dict() for i in ctx.issues]},
    )
    snap.digest = snap.compute_digest()
    return snap


def _sweep_candidates(direction: str, events: Sequence[Dict[str, Any]], level_sweeps: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    want = "SWEEP_LOW" if direction == "LONG" else "SWEEP_HIGH"
    cands = [e for e in list(events) + list(level_sweeps) if e.get("event_type") == "SWEEP" and e.get("type") == want]
    return sorted(cands, key=lambda e: e["timestamp"])


def _evaluate_direction(
    *, direction: str, symbol: str, m5: List[Any], idx_by_ts: Dict[int, int], sh: List[Dict], sl: List[Dict],
    events: List[Dict], level_sweeps: List[Dict], fvg_nodes: List[EvidenceNode], levels: List[LiquidityLevel],
    m15: List[Any], regime: str, h4_bias: str, h1_align: str, config: ScenarioConfig, now_ms: int,
    quote: Any, settings: Optional[FinancialSettings], session_ctx: Dict[str, Any], entry_window: Optional[Tuple[int, int]],
) -> ScenarioSnapshot:
    bull = direction == "LONG"
    dir_ev = "BULLISH" if bull else "BEARISH"
    last = m5[-1]
    last_idx = len(m5) - 1
    price = float(last.close)
    tracked = [l for l in levels if l.state in ("UNTOUCHED", "APPROACHED")]
    anchor = select_observation_anchor(tracked, direction, price)

    best: Optional[Dict[str, Any]] = None
    best_rank = -1
    for sw in reversed(_sweep_candidates(direction, events, level_sweeps)):
        res = _progress_for_sweep(
            direction=direction, symbol=symbol, sw=sw, m5=m5, idx_by_ts=idx_by_ts, sh=sh, sl=sl, fvg_nodes=fvg_nodes,
            m15=m15, regime=regime, config=config, now_ms=now_ms,
        )
        if res["rank"] > best_rank:
            best, best_rank = res, res["rank"]
        if res["rank"] >= 6:
            break
    # ---------------------------------------------------------------------- state & evidence
    state = ScenarioState.WAITING_SWEEP if anchor is not None else ScenarioState.WAITING_LIQUIDITY
    nodes: List[EvidenceNode] = []
    blockers: List[str] = []
    advisories: List[str] = []
    sweep = brk = fvg = retest = poi = None
    anchor_id = "observing"
    swept_level: Optional[Dict[str, Any]] = None
    chain_reasons: List[str] = []
    plan_dict: Optional[Dict[str, Any]] = None
    target_lvl: Optional[LiquidityLevel] = None
    invalidation: Optional[Dict[str, Any]] = None
    entry_basis = None

    if best is not None:
        sweep, brk, fvg, retest, poi = best["sweep"], best["brk"], best["fvg"], best["retest"], best["poi"]
        nodes = [n for n in best["nodes"] if n is not None]
        picks = [p for p in ((brk.extra.get("broken_swing_id") if brk else None),
                             (sweep.extra.get("source_level_id") if sweep else None)) if p]
        nodes += _swing_nodes(symbol, sh, sl, now_ms, picks)
        state = best["state"]
        blockers = list(best["blockers"])
        advisories = list(best["advisories"])
        anchor_id = best["anchor_id"]
        swept_level = best["swept_level"]
        invalidation = best["invalidation"]

    if best is not None and state == ScenarioState.STRUCTURE_CONFIRMED:
        eval_price = float(last.close)
        entry_basis = "LAST_CLOSE"
        update_liquidity_states(levels, m5, now_ms, M5_MS)
        target_lvl = select_unconsumed_target(levels, direction, eval_price, as_of_ms=now_ms)
        chain = validate_reversal_chain(
            symbol=symbol, direction=direction, as_of_ms=now_ms, sweep=sweep, brk=brk, fvg=fvg, retest=retest,
            regime=regime,
        )
        chain_reasons = list(chain.reasons)
        if not chain.verified:
            state = ScenarioState.BLOCKED
            blockers.extend(chain_reasons)
        else:
            if config.require_m15_poi and poi is None:
                state = ScenarioState.BLOCKED
                blockers.append("M15_POI_REQUIRED")
            if settings is None:
                blockers.append("FINANCIAL_SETTINGS_UNAVAILABLE")
            else:
                plan = build_scenario_plan(
                    symbol=symbol, direction=direction, entry_price=eval_price,
                    sweep_extreme=sweep.extra.get("wick_extreme"),
                    target_level=target_lvl.price if target_lvl else None,
                    config=config, settings=settings, now_ms=now_ms,
                )
                plan_dict = plan.to_dict()
                plan_dict["entry_basis"] = entry_basis
                if not plan.is_valid:
                    blockers.append(str(plan.invalid_reason))
            if entry_window is not None and not (entry_window[0] <= now_ms < entry_window[1]):
                blockers.append("OUTSIDE_ENTRY_WINDOW")
    # ---------------------------------------------------------------------- identity
    name, date_str = session_for(now_ms)
    session_id = f"{symbol}:{name}:{date_str}"
    scenario_id, instance_id = make_scenario_identity(
        symbol, session_id, config.family, direction, anchor_id or "unanchored")
    # ---------------------------------------------------------------------- target for display
    if target_lvl is None and best is None:
        target_lvl = select_unconsumed_target(levels, direction, price, as_of_ms=now_ms)
    target_dict = ({"id": target_lvl.id, "price": target_lvl.price, "label_vi": target_lvl.label_vi,
                    "source_kind": target_lvl.source_kind, "state": target_lvl.state,
                    "is_partial": target_lvl.is_partial} if target_lvl else None)
    if plan_dict and plan_dict.get("is_valid") and target_dict:
        target_dict["price"] = plan_dict["take_profit"]
    inv_price = invalidation["price"] if invalidation else None
    h4_text, h1_text = _htf_regime_text(h4_bias), h1_align
    nodes_dicts = [n.to_dict() for n in nodes]
    summary = build_scenario_summary_vi(
        symbol=symbol, direction=direction, state=state.value, h4_regime=h4_text, h1_regime=h1_text,
        anchor_desc=(swept_level or {}).get("label_vi") or (anchor.label_vi if anchor else None),
        anchor_price=(swept_level or {}).get("price") or (anchor.price if anchor else None),
        sweep_desc=_node_desc(sweep, "Quét thanh khoản"), break_desc=_node_desc(brk, "Break cấu trúc"),
        fvg_desc="FVG M5" if fvg else None, retest_desc="Retest FVG" if retest else None,
        target_desc=target_lvl.label_vi if target_lvl else None, target_price=target_dict["price"] if target_dict else None,
        invalidation_price=inv_price, blockers=blockers or None, timeframe=SETUP_TF,
    )
    liq_levels = sorted(levels, key=lambda l: abs(l.price - price))[:16]
    snap = ScenarioSnapshot(
        scenario_id=scenario_id, instance_id=instance_id, revision=1, symbol=symbol, setup_timeframe=SETUP_TF,
        context_timeframes=list(CONTEXT_TFS), family=config.family, direction=direction, state=state,
        as_of_ms=now_ms, evidence_cutoff_ms=now_ms, evidence_ids=[n.id for n in nodes],
        liquidity_map={
            "anchor": anchor.to_dict() if anchor else None, "swept": swept_level,
            "target": target_dict, "levels": [l.to_dict() for l in liq_levels],
            "coverage": {k: v.to_dict() for k, v in session_ctx["ranges"].items()},
            "previous_day": session_ctx["previous_day"].to_dict(),
        },
        trigger_chain={"verified": bool(state == ScenarioState.STRUCTURE_CONFIRMED and not chain_reasons and brk is not None),
                       "reasons": chain_reasons or [b for b in blockers], "advisories": advisories,
                       "node_ids": {k: (v.id if v else None) for k, v in
                                    (("sweep", sweep), ("break", brk), ("fvg", fvg), ("retest", retest), ("poi", poi))}},
        target=target_dict, invalidation=invalidation, summary_vi=summary,
        config_version=config.config_version, detector_version=config.detector_version,
        plan_fingerprint=plan_dict["plan_fingerprint"] if plan_dict else None, plan=plan_dict,
        blockers=list(dict.fromkeys(blockers)), session_name=name, session_id=session_id, evidence=nodes_dicts,
        context={"regime": regime, "h4_bias": h4_bias, "h1_alignment": h1_align,
                 "session_ranges": {k: v.to_dict() for k, v in session_ctx["ranges"].items()},
                 "previous_day": session_ctx["previous_day"].to_dict(), "entry_basis": entry_basis,
                 "executable_quote": ({"bid": getattr(quote, "bid", None), "ask": getattr(quote, "ask", None),
                                       "observed_at": getattr(quote, "observed_at", None)}
                                      if quote is not None and getattr(quote, "is_valid", False) else None)},
    )
    snap.digest = snap.compute_digest()
    return snap


def _node_desc(node: Optional[EvidenceNode], label: str) -> Optional[str]:
    return f"{label} ({node.kind})" if node is not None else None


def _progress_for_sweep(*, direction: str, symbol: str, sw: Dict[str, Any], m5: List[Any], idx_by_ts: Dict[int, int],
                        sh: List[Dict], sl: List[Dict], fvg_nodes: List[EvidenceNode], m15: List[Any], regime: str,
                        config: ScenarioConfig, now_ms: int) -> Dict[str, Any]:
    """Measures how far the chain following ONE sweep has progressed. rank: 1 sweep, 2 break pending (non
    qualified), 3 break, 4 fvg, 5 retest wait, 6 confirmed/blocked, -1/EXPIRED/INVALIDATED terminal."""
    bull = direction == "LONG"
    dir_ev = "BULLISH" if bull else "BEARISH"
    blockers: List[str] = []
    s_ts, s_known = sw["timestamp"], sw["confirmed_at"]
    wick = sw.get("wick_extreme")
    out: Dict[str, Any] = {"rank": 1, "state": ScenarioState.WAITING_BREAK, "sweep": None, "brk": None, "fvg": None,
                           "retest": None, "poi": None, "nodes": [], "blockers": blockers, "advisories": [],
                           "anchor_id": sw.get("source_swing_id"), "swept_level": None, "invalidation": None}
    close_back = sw.get("close_back")
    if close_back is None and s_ts in idx_by_ts:
        close_back = m5[idx_by_ts[s_ts]].close
    sweep = EvidenceNode(
        id=sw["id"], kind=sw["type"], symbol=symbol, timeframe=SETUP_TF, direction=direction,
        occurred_at_ms=s_ts, known_at_ms=s_known, level=sw.get("level"), source_candle_open_ms=s_ts,
        extra={"source_level_id": sw.get("source_swing_id"), "wick_extreme": wick, "close_back": close_back,
               "closed_back": (close_back is not None and ((close_back > sw["level"]) if bull else (close_back < sw["level"]))),
               "level_label": sw.get("level_label"), "level_kind": sw.get("level_kind", "SWING")},
    )
    out["sweep"] = sweep
    out["nodes"].append(sweep)
    out["swept_level"] = {"id": sw.get("source_swing_id"), "price": sw.get("level"),
                          "label_vi": sw.get("level_label") or ("Đáy swing" if bull else "Đỉnh swing") + f" {sw.get('level'):.2f}",
                          "wick_extreme": wick}
    out["invalidation"] = {"price": wick, "basis": "SWEEP_WICK_EXTREME", "kind": "close_beyond"}
    s_idx = idx_by_ts[s_ts]
    last_idx = len(m5) - 1

    # invalidated: a closed bar after the sweep closes beyond the wick extreme
    for c in m5[s_idx + 1:]:
        if (bull and c.close < wick) or ((not bull) and c.close > wick):
            out.update(rank=0, state=ScenarioState.INVALIDATED)
            blockers.append("PRICE_CLOSED_BEYOND_SWEEP_EXTREME")
            return out
    # --- reference swing (pre-sweep structure) and trend before sweep
    sweep_pool = sh if bull else sl
    refs = [s for s in sweep_pool if s["timestamp"] < s_ts and s["confirmed_at"] <= s_known]
    ref = max(refs, key=lambda s: s["timestamp"]) if refs else None
    if ref is None:
        blockers.append("NO_REFERENCE_SWING")
        return out
    sh_known = [s for s in sh if s["confirmed_at"] <= s_known]
    sl_known = [s for s in sl if s["confirmed_at"] <= s_known]
    trend_before = smc_engine.determine_causal_trend_at(m5, s_idx, sh_known, sl_known, s_known)
    # --- break: first closed bar after the sweep bar that closes beyond the reference swing
    brk_candle, brk_idx = None, None
    for i in range(s_idx + 1, len(m5)):
        c = m5[i]
        if (bull and c.close > ref["price"]) or ((not bull) and c.close < ref["price"]):
            brk_candle, brk_idx = c, i
            break
    if brk_candle is None:
        return out
    atr_pre = prefix_atr(m5, brk_idx, config.displacement_atr_period)
    disp = qualify_displacement(brk_candle, atr_pre, config.displacement_body_atr_min,
                                direction=dir_ev, broken_level=ref["price"])
    opposite = "BEARISH" if bull else "BULLISH"
    if trend_before == opposite:
        kind = "MSS" if disp["is_qualified"] else "CHOCH"
    elif trend_before == dir_ev:
        kind = "BOS"
    else:
        kind = "BREAK_UNCLASSIFIED"
    brk = EvidenceNode(
        id=make_evidence_id(symbol, SETUP_TF, kind, brk_candle.timestamp, config.detector_version),
        kind=kind, symbol=symbol, timeframe=SETUP_TF, direction=dir_ev, occurred_at_ms=brk_candle.timestamp,
        known_at_ms=brk_candle.timestamp + M5_MS, level=ref["price"], source_candle_open_ms=brk_candle.timestamp,
        extra={"displacement": disp, "broken_swing_id": ref["id"], "broken_level": ref["price"],
               "trend_before": trend_before, "prefix_atr": atr_pre, "atr_period": config.displacement_atr_period},
    )
    out["nodes"].append(brk)
    out["brk"] = brk
    out["rank"] = 2
    if kind == "BREAK_UNCLASSIFIED":
        blockers.append("BREAK_UNCLASSIFIED")
        return out
    if not disp["is_qualified"]:
        blockers.append("DISPLACEMENT_NOT_QUALIFIED:" + str(disp.get("reason")))
        return out
    out["rank"] = 3
    out["state"] = ScenarioState.WAITING_FVG
    # --- FVG created by the break's leg (real bounds, creator candles checked against candles)
    want = dir_ev
    fvg = None
    for node in fvg_nodes:
        if node.direction != want or node.occurred_at_ms < s_ts - M5_MS:
            continue
        ok, _why = bind_fvg_to_leg(node, brk, m5, M5_MS)
        if ok:
            fvg = replace(node, extra={**node.extra, "leg_bound": True, "leg_break_id": brk.id})
            break
    if fvg is None:
        return out
    out["fvg"] = fvg
    out["nodes"].append(fvg)
    out["rank"] = 4
    out["state"] = ScenarioState.WAITING_RETEST
    # --- retest: first CLOSED bar at/after FVG confirmation touching the actual zone
    zone = {"top": fvg.extra["top"], "bottom": fvg.extra["bottom"]}
    retest_candle, touch = None, None
    for c in m5:
        if c.timestamp < fvg.known_at_ms:
            continue
        hit, price = confirm_closed_retest(zone, c, direction, known_at_ms=fvg.known_at_ms, as_of_ms=now_ms)
        if hit:
            retest_candle, touch = c, price
            break
    out["rank"] = 5
    # source links are written only now that every predicate above passed
    brk = replace(brk, source_ids=(sweep.id,))
    fvg = replace(fvg, source_ids=(brk.id,))
    out["brk"], out["fvg"] = brk, fvg
    out["nodes"] = [sweep, brk, fvg]
    if retest_candle is None:
        poi, adv = build_mtf_chain(direction=direction, m15_candles=m15, sweep=sweep, fvg=fvg, symbol=symbol, as_of_ms=now_ms)
        out["poi"], out["advisories"] = poi, adv
        if poi is not None:
            out["nodes"].append(poi)
        return out
    r_idx = idx_by_ts[retest_candle.timestamp]
    # post-retest failures
    for c in m5[r_idx + 1:]:
        if (bull and c.close < zone["bottom"]) or ((not bull) and c.close > zone["top"]):
            out.update(rank=0, state=ScenarioState.INVALIDATED)
            blockers.append("FVG_ZONE_FAILED_AFTER_RETEST")
            return out
    if last_idx - r_idx > config.retest_validity_bars:
        out.update(rank=0, state=ScenarioState.EXPIRED)
        blockers.append("RETEST_STALE")
        return out
    retest = EvidenceNode(
        id=make_evidence_id(symbol, SETUP_TF, "RETEST", retest_candle.timestamp, config.detector_version),
        kind="RETEST", symbol=symbol, timeframe=SETUP_TF, direction=dir_ev, occurred_at_ms=retest_candle.timestamp,
        known_at_ms=retest_candle.timestamp + M5_MS, level=touch, source_ids=(fvg.id,),
        source_candle_open_ms=retest_candle.timestamp,
        extra={"zone_top": zone["top"], "zone_bottom": zone["bottom"], "touch_price": touch, "source": "CLOSED_CANDLE",
               "close": float(retest_candle.close)},
    )
    poi, adv = build_mtf_chain(direction=direction, m15_candles=m15, sweep=sweep, fvg=fvg, symbol=symbol, as_of_ms=now_ms)
    out["poi"], out["advisories"] = poi, adv
    out["retest"] = retest
    out["nodes"] = [sweep, brk, fvg, retest] + ([poi] if poi is not None else [])
    out["rank"] = 6
    out["state"] = ScenarioState.STRUCTURE_CONFIRMED
    return out


def _swing_nodes(symbol: str, sh: List[Dict], sl: List[Dict], now_ms: int, picks: Sequence[str]) -> List[EvidenceNode]:
    out = []
    for s in list(sh) + list(sl):
        if s["id"] in picks:
            out.append(EvidenceNode(
                id=s["id"], kind="SWING_HIGH" if s["kind"] == "HIGH" else "SWING_LOW", symbol=symbol, timeframe=SETUP_TF,
                direction=None, occurred_at_ms=s["timestamp"], known_at_ms=s["confirmed_at"], level=s["price"],
                source_candle_open_ms=s["timestamp"], extra={"label": s.get("label")}))
    return out


def evaluate_scenarios(
    symbol: str,
    context: ClosedContext,
    previous_scenarios: Sequence[ScenarioSnapshot],
    config: ScenarioConfig,
    now_ms: int,
    quote: Any = None,
    settings: Optional[FinancialSettings] = None,
    entry_window: Optional[Tuple[int, int]] = None,
) -> List[ScenarioSnapshot]:
    """Pure: closed context (+ optional validated quote and financial settings) -> scenario snapshots.
    Missing/invalid required data returns ONE observation snapshot carrying DATA_UNAVAILABLE blockers
    (never an empty list that a caller could read as 'no setup')."""
    blocking = context.blocking_issues
    m5 = context.get("5M")
    if blocking or len(m5) < config.min_atr_bars:
        reasons = [f"DATA_UNAVAILABLE:{i.code}" for i in blocking] or [f"DATA_UNAVAILABLE:INSUFFICIENT_BARS:{len(m5)}<{config.min_atr_bars}"]
        return [_data_unavailable_snapshot(symbol, context, config, now_ms, reasons)]

    h4 = build_htf_context(context.get("4H"), "4H")
    h1 = build_htf_context(context.get("1H"), "1H")
    h1_align = h1_alignment_from(h4, h1)
    regime = classify_regime(h4.bias, h1_align, atr_relative(m5))

    sh, sl = smc_engine.identify_pivots(m5, timeframe=SETUP_TF)
    atr_val = smc_engine.compute_atr(m5, period=config.displacement_atr_period)
    events, _summary, _latest, _trend = smc_engine.detect_sweeps_and_breaks_causal(
        m5, sh, sl, timeframe=SETUP_TF, atr=atr_val)

    fvg_nodes: List[EvidenceNode] = []
    for raw in smc_engine.detect_fvgs(m5, timeframe=SETUP_TF):
        try:
            fvg_nodes.append(adapt_detected_fvg(raw, symbol=symbol, timeframe=SETUP_TF, as_of_ms=now_ms))
        except ValueError:
            continue

    session_ctx = build_session_context(context, now_ms, config)
    current_session = session_ctx["session_name"]
    liq_ranges = {k: v for k, v in session_ctx["ranges"].items() if k in ("ASIA", "LONDON") and k != current_session}
    levels = build_liquidity_map(
        symbol=symbol, timeframe=SETUP_TF, swing_highs=sh, swing_lows=sl, session_ranges=liq_ranges,
        previous_day=session_ctx["previous_day"], tolerance_atr=config.eqh_eql_tolerance_atr, atr=atr_val, now_ms=now_ms)
    level_sweeps = detect_level_sweeps(levels, m5, symbol=symbol)
    update_liquidity_states(levels, m5, now_ms, M5_MS)

    idx_by_ts = {c.timestamp: i for i, c in enumerate(m5)}
    results: List[ScenarioSnapshot] = []
    for direction in ("LONG", "SHORT"):
        snap = _evaluate_direction(
            direction=direction, symbol=symbol, m5=m5, idx_by_ts=idx_by_ts, sh=sh, sl=sl, events=events,
            level_sweeps=level_sweeps, fvg_nodes=fvg_nodes, levels=levels, m15=context.get("15M"),
            regime=regime, h4_bias=h4.bias, h1_align=h1_align, config=config, now_ms=now_ms, quote=quote,
            settings=settings, session_ctx=session_ctx, entry_window=entry_window,
        )
        results.append(_reconcile_with_previous(snap, previous_scenarios, now_ms))
    return results


def _reconcile_with_previous(snap: ScenarioSnapshot, previous: Sequence[ScenarioSnapshot], now_ms: int) -> ScenarioSnapshot:
    """Same identity: a terminal scenario is never resurrected; an unchanged digest keeps its revision; a
    material change is revision+1 (regressions are kept as a diagnostic, facts win)."""
    same = [p for p in previous if p.instance_id == snap.instance_id]
    if not same:
        return snap
    prev = max(same, key=lambda p: p.revision)
    if prev.state in TERMINAL_STATES:
        return prev
    if prev.digest == snap.digest:
        return prev
    if prev.state != snap.state and snap.state not in _ANYTIME_TERMINAL \
            and snap.state not in _ALLOWED.get(prev.state, set()) and snap.state != prev.state:
        snap.blockers = list(dict.fromkeys(list(snap.blockers) + [f"STATE_REGRESSION:{prev.state.value}->{snap.state.value}"]))
    snap.revision = prev.revision + 1
    snap.digest = snap.compute_digest()
    return snap


# ===================================================================================================
# Cycle (persist + report). The CALLER owns commit and event publication.
# ===================================================================================================
@dataclass
class ScenarioCycleResult:
    snapshots: List[ScenarioSnapshot]
    changed: List[ScenarioSnapshot]             # material revisions that were persisted this cycle
    superseded: List[ScenarioSnapshot]
    primary: Optional[ScenarioSnapshot]
    issues: List[ContextIssue]


def evaluate_scenario_cycle(
    db: Session,
    symbol: str,
    candles_by_tf: Dict[str, List[Any]],
    quote: Any,
    now_ms: int,
    config: Optional[ScenarioConfig] = None,
    settings: Optional[FinancialSettings] = None,
    entry_window: Optional[Tuple[int, int]] = None,
) -> ScenarioCycleResult:
    """Context -> evaluate -> persist (flush only). Reads previously persisted scenarios so terminal ones
    stay terminal and revisions only advance on material change."""
    cfg = config or active_scenario_config
    ctx = load_closed_context(candles_by_tf, now_ms, cfg)
    previous = get_previous_scenarios(db, symbol)
    snapshots = evaluate_scenarios(symbol, ctx, previous, cfg, now_ms, quote=quote, settings=settings, entry_window=entry_window)

    changed: List[ScenarioSnapshot] = []
    superseded: List[ScenarioSnapshot] = []
    new_instance_ids = {s.instance_id for s in snapshots}
    new_scenario_ids = {s.scenario_id: s.instance_id for s in snapshots}
    for old in previous:
        if old.state in TERMINAL_STATES or old.instance_id in new_instance_ids:
            continue
        same_slot = new_scenario_ids.get(old.scenario_id)
        reason = "SUPERSEDED_BY_NEW_ANCHOR" if same_slot else None
        nxt_state = ScenarioState.SUPERSEDED if same_slot else ScenarioState.EXPIRED
        old_next = transition_scenario(old, nxt_state, now_ms, reason or "SESSION_ENDED")
        if old_next.state in TERMINAL_STATES and old_next is not old:
            upsert_scenario_revision(db=db, snapshot=old_next, is_material_change=True)
            superseded.append(old_next)

    for s in snapshots:
        prev_rows = [p for p in previous if p.instance_id == s.instance_id]
        prev_digest = max(prev_rows, key=lambda p: p.revision).digest if prev_rows else None
        material = prev_digest != s.digest
        if material:
            upsert_scenario_revision(db=db, snapshot=s, is_material_change=True)
            changed.append(s)
    return ScenarioCycleResult(snapshots=snapshots, changed=changed, superseded=superseded,
                               primary=select_primary_scenario(snapshots), issues=ctx.issues)
