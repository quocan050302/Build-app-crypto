"""
backend/services/smc_scenario_types.py
V14.4 — Core types, data structures, and identity helpers for Causal SMC Scenarios.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


class ScenarioState(str, Enum):
    OBSERVING = "OBSERVING"
    WAITING_LIQUIDITY = "WAITING_LIQUIDITY"
    WAITING_SWEEP = "WAITING_SWEEP"
    WAITING_BREAK = "WAITING_BREAK"
    WAITING_FVG = "WAITING_FVG"
    WAITING_RETEST = "WAITING_RETEST"
    STRUCTURE_CONFIRMED = "STRUCTURE_CONFIRMED"
    BLOCKED = "BLOCKED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class FactStatus(str, Enum):
    OBSERVED_FACT = "OBSERVED_FACT"          # Historical structural fact with confirmed known_at_ms
    SCENARIO_HYPOTHESIS = "SCENARIO_HYPOTHESIS"  # Conditional expectation (e.g. waiting for retest)
    CONFIRMED_EVIDENCE = "CONFIRMED_EVIDENCE"   # Fully verified evidence component in execution chain


@dataclass(frozen=True)
class EvidenceNode:
    id: str
    kind: str                                # SWING_HIGH, SWING_LOW, SWEEP_HIGH, SWEEP_LOW, BOS, CHOCH, MSS, FVG, RETEST
    symbol: str
    timeframe: str
    direction: Optional[str]                 # BULLISH, BEARISH, LONG, SHORT, or None
    occurred_at_ms: int                      # Candle open / event timestamp where fact occurred on chart
    known_at_ms: int                         # Exact time when fact was confirmed/known (for causal visibility)
    source_ids: Tuple[str, ...] = ()         # References to parent/anchor evidence IDs
    level: Optional[float] = None            # Price level associated with the node
    source_candle_open_ms: Optional[int] = None
    fact_status: FactStatus = FactStatus.OBSERVED_FACT
    extra: Dict[str, Any] = field(default_factory=dict)


    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "occurred_at_ms": self.occurred_at_ms,
            "known_at_ms": self.known_at_ms,
            "source_ids": list(self.source_ids),
            "level": self.level,
            "source_candle_open_ms": self.source_candle_open_ms,
            "fact_status": self.fact_status.value,
            "extra": self.extra,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "EvidenceNode":
        """Strict inverse of to_dict (missing required keys raise KeyError: no silent defaults)."""
        return EvidenceNode(
            id=d["id"], kind=d["kind"], symbol=d["symbol"], timeframe=d["timeframe"],
            direction=d.get("direction"),
            occurred_at_ms=d["occurred_at_ms"], known_at_ms=d["known_at_ms"],
            source_ids=tuple(d.get("source_ids") or ()),
            level=d.get("level"), source_candle_open_ms=d.get("source_candle_open_ms"),
            fact_status=FactStatus(d.get("fact_status", FactStatus.OBSERVED_FACT.value)),
            extra=dict(d.get("extra") or {}),
        )


@dataclass(frozen=True)
class ChainCheck:
    verified: bool
    reasons: Tuple[str, ...]
    evidence_ids: Tuple[str, ...]


@dataclass
class LiquidityLevel:
    id: str
    price: float
    side: str                                # BUY_SIDE / SELL_SIDE
    source_kind: str                         # SWING, EQH, EQL, SESSION_ASIA, SESSION_EUROPE, PDH, PDL
    occurred_at_ms: int
    known_at_ms: int
    source_ids: List[str] = field(default_factory=list)
    state: str = "UNTOUCHED"                 # UNTOUCHED, APPROACHED, SWEPT, BROKEN, CONSUMED
    label_vi: str = ""
    tolerance_price: Optional[float] = None
    is_partial: bool = False                 # session extreme still forming (as-of value, not final)
    coverage: Optional[Dict[str, Any]] = None  # source coverage for session/PD levels

    @property
    def level_id(self) -> str:
        return self.id

    @property
    def name(self) -> str:
        return self.label_vi or self.source_kind

    @property
    def kind(self) -> str:
        return self.source_kind

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "price": self.price,
            "side": self.side,
            "source_kind": self.source_kind,
            "occurred_at_ms": self.occurred_at_ms,
            "known_at_ms": self.known_at_ms,
            "source_ids": list(self.source_ids),
            "state": self.state,
            "label_vi": self.label_vi,
            "tolerance_price": self.tolerance_price,
            "is_partial": self.is_partial,
            "coverage": self.coverage,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LiquidityLevel":
        return LiquidityLevel(
            id=d["id"], price=float(d["price"]), side=d["side"], source_kind=d["source_kind"],
            occurred_at_ms=d["occurred_at_ms"], known_at_ms=d["known_at_ms"],
            source_ids=list(d.get("source_ids") or []), state=d.get("state", "UNTOUCHED"),
            label_vi=d.get("label_vi", ""), tolerance_price=d.get("tolerance_price"),
            is_partial=bool(d.get("is_partial", False)), coverage=d.get("coverage"),
        )


SNAPSHOT_SCHEMA_VERSION = "v14.4.1"

TERMINAL_STATES = (ScenarioState.INVALIDATED, ScenarioState.EXPIRED, ScenarioState.SUPERSEDED)


def default_probability() -> Dict[str, Any]:
    """No validated statistical model exists unless the registry says so: never fabricate a probability."""
    return {"status": "UNAVAILABLE", "p_tp_before_sl": None, "support_n": None}


@dataclass
class ScenarioSnapshot:
    """Canonical V14.4.1 scenario snapshot. Every reader (repository, API, chart adapter, eligibility)
    uses THIS shape: `evidence` is the full list of EvidenceNode dicts, `plan` is a dict that carries
    `plan_fingerprint`, `trigger_chain` is {verified, reasons, node_ids}."""
    scenario_id: str
    instance_id: str
    revision: int
    symbol: str
    setup_timeframe: str
    context_timeframes: List[str]
    family: str                              # STANDARD_SMC, NY_QUOTA_PAPER, etc.
    direction: Optional[str]                 # LONG, SHORT, or None (UNKNOWN)
    state: ScenarioState
    as_of_ms: int
    evidence_cutoff_ms: int
    evidence_ids: List[str]
    liquidity_map: Dict[str, Any]
    trigger_chain: Dict[str, Any]
    target: Optional[Dict[str, Any]]
    invalidation: Optional[Dict[str, Any]]
    summary_vi: str
    config_version: int
    detector_version: str
    digest: str = ""
    plan_fingerprint: Optional[str] = None
    plan: Optional[Dict[str, Any]] = None
    blockers: List[str] = field(default_factory=list)
    schema_version: str = SNAPSHOT_SCHEMA_VERSION
    session_name: str = "NY"
    session_id: str = ""
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    probability: Dict[str, Any] = field(default_factory=default_probability)
    context: Dict[str, Any] = field(default_factory=dict)
    requirement_profile: str = "DEFAULT"

    @property
    def snapshot_id(self) -> str:
        return f"snap-{self.instance_id}-{self.revision}"

    @property
    def strategy_family(self) -> str:
        return self.family

    @property
    def evidence_digest(self) -> str:
        return self.digest or self.compute_digest()

    @property
    def anchor_level_id(self) -> Optional[str]:
        anchor = (self.liquidity_map or {}).get("anchor")
        return anchor.get("id") if isinstance(anchor, dict) else None

    @property
    def target_level_id(self) -> Optional[str]:
        return self.target.get("id") if isinstance(self.target, dict) else None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def compute_digest(self, include_profile: bool = True) -> str:
        """Digest of MATERIAL content only: full evidence, liquidity/target/plan and config/schema versions.
        as_of/evidence_cutoff/revision are deliberately excluded so an unchanged picture does not mint a
        new revision every cycle (live distance telemetry never enters the frozen decision snapshot)."""
        canonical_content = {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "instance_id": self.instance_id,
            "symbol": self.symbol,
            "setup_timeframe": self.setup_timeframe,
            "family": self.family,
            "direction": self.direction,
            "session_id": self.session_id,
            "state": self.state.value if isinstance(self.state, ScenarioState) else str(self.state),
            "evidence": sorted(self.evidence, key=lambda e: str(e.get("id"))),
            "evidence_ids": sorted(self.evidence_ids),
            "liquidity_map": {"swept": (self.liquidity_map or {}).get("swept")},
            "trigger_chain": self.trigger_chain,
            "target": self.target if self.state in (ScenarioState.STRUCTURE_CONFIRMED, ScenarioState.BLOCKED) else None,
            "invalidation": self.invalidation,
            "plan": self.plan,
            "plan_fingerprint": self.plan_fingerprint,
            "config_version": self.config_version,
            "detector_version": self.detector_version,
            "blockers": sorted(self.blockers),
        }
        if include_profile:
            canonical_content["requirement_profile"] = getattr(self, "requirement_profile", "DEFAULT")
        raw_json = json.dumps(canonical_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()

    def compute_digest_legacy(self) -> str:
        return self.compute_digest(include_profile=False)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ScenarioSnapshot":
        """Inverse of to_dict for CURRENT-schema payloads. Payloads of another schema_version are NOT
        silently coerced: they raise ValueError('LEGACY_SNAPSHOT_SCHEMA') so callers treat them as
        uncertified legacy evidence instead of guessing field names."""
        if d.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("LEGACY_SNAPSHOT_SCHEMA")
        return ScenarioSnapshot(
            scenario_id=d["scenario_id"], instance_id=d["instance_id"], revision=int(d["revision"]),
            symbol=d["symbol"], setup_timeframe=d["setup_timeframe"],
            context_timeframes=list(d.get("context_timeframes") or []),
            family=d["family"], direction=d.get("direction"), state=ScenarioState(d["state"]),
            as_of_ms=d["as_of_ms"], evidence_cutoff_ms=d["evidence_cutoff_ms"],
            evidence_ids=list(d.get("evidence_ids") or []),
            liquidity_map=d.get("liquidity_map") or {}, trigger_chain=d.get("trigger_chain") or {},
            target=d.get("target"), invalidation=d.get("invalidation"),
            summary_vi=d.get("summary_vi", ""), config_version=d["config_version"],
            detector_version=d["detector_version"], digest=d.get("digest", ""),
            plan_fingerprint=d.get("plan_fingerprint"), plan=d.get("plan"),
            blockers=list(d.get("blockers") or []), schema_version=d["schema_version"],
            session_name=d.get("session_name", "NY"), session_id=d.get("session_id", ""),
            evidence=list(d.get("evidence") or []),
            probability=d.get("probability") or default_probability(),
            context=d.get("context") or {},
            requirement_profile=d.get("requirement_profile", "DEFAULT"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "instance_id": self.instance_id,
            "snapshot_id": self.snapshot_id,
            "revision": self.revision,
            "symbol": self.symbol,
            "setup_timeframe": self.setup_timeframe,
            "context_timeframes": list(self.context_timeframes),
            "family": self.family,
            "session_name": self.session_name,
            "session_id": self.session_id,
            "direction": self.direction,
            "state": self.state.value if isinstance(self.state, ScenarioState) else str(self.state),
            "as_of_ms": self.as_of_ms,
            "evidence_cutoff_ms": self.evidence_cutoff_ms,
            "evidence_ids": list(self.evidence_ids),
            "evidence": list(self.evidence),
            "liquidity_map": self.liquidity_map,
            "trigger_chain": self.trigger_chain,
            "target": self.target,
            "invalidation": self.invalidation,
            "plan": self.plan,
            "probability": self.probability,
            "summary_vi": self.summary_vi,
            "config_version": self.config_version,
            "detector_version": self.detector_version,
            "digest": self.digest or self.compute_digest(),
            "plan_fingerprint": self.plan_fingerprint,
            "blockers": list(self.blockers),
            "context": self.context,
            "requirement_profile": self.requirement_profile,
        }


def stable_hash(*parts: Any, length: int = 16) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:length]


def make_evidence_id(symbol: str, timeframe: str, kind: str, anchor: Any, detector_version: str = "v14.4") -> str:
    """Deterministic evidence ID based on symbol, timeframe, kind, candle/level anchor, and detector version."""
    anchor_str = str(anchor)
    return f"ev-{symbol}-{timeframe}-{kind.lower()}-{anchor_str}-{detector_version}"


def make_scenario_identity(symbol: str, session_id: str, family: str, direction: Optional[str], anchor_id: str) -> Tuple[str, str]:
    """Returns (scenario_id, instance_id).
    Changing direction or liquidity anchor creates a new instance.
    """
    dir_str = direction or "UNDECIDED"
    instance_id = f"scen-inst-{symbol}-{session_id}-{family}-{dir_str}-{stable_hash(anchor_id, length=10)}"
    scenario_id = f"scen-{symbol}-{session_id}-{family}-{dir_str}"
    return scenario_id, instance_id


def validate_reversal_order(
    *,
    symbol: str,
    direction: str,
    as_of_ms: int,
    sweep: EvidenceNode,
    brk: EvidenceNode,
    fvg: EvidenceNode,
    retest: EvidenceNode,
) -> ChainCheck:
    """Appendix A pure causality and relationship checker for reversal chain."""
    reasons: List[str] = []
    nodes = (sweep, brk, fvg, retest)
    ids = tuple(n.id for n in nodes)

    if direction not in ("LONG", "SHORT"):
        reasons.append("UNKNOWN_DIRECTION")

    valid_as_of = type(as_of_ms) is int and as_of_ms > 0
    if not valid_as_of:
        reasons.append("INVALID_AS_OF")

    if len(set(ids)) != len(ids):
        reasons.append("DUPLICATE_EVIDENCE")

    for n in nodes:
        if not n.id or not n.timeframe:
            reasons.append("EVIDENCE_IDENTITY_MISSING")
        if n.symbol != symbol:
            reasons.append("EVIDENCE_SYMBOL_MISMATCH")
        if (
            type(n.occurred_at_ms) is not int
            or type(n.known_at_ms) is not int
            or n.occurred_at_ms <= 0
            or n.known_at_ms < n.occurred_at_ms
            or (valid_as_of and n.known_at_ms > as_of_ms)
        ):
            reasons.append("EVIDENCE_TIME_INVALID")

    expected_sweep = "SWEEP_LOW" if direction == "LONG" else "SWEEP_HIGH"
    expected_dir = "BULLISH" if direction == "LONG" else "BEARISH"

    if sweep.kind != expected_sweep:
        reasons.append("SWEEP_SIDE_MISMATCH")
    if brk.kind not in ("BOS", "CHOCH", "MSS") or brk.direction != expected_dir:
        reasons.append("BREAK_DIRECTION_MISMATCH")

    if fvg.kind != "FVG" or fvg.direction != expected_dir:
        reasons.append("FVG_DIRECTION_MISMATCH")
    if retest.kind != "RETEST" or retest.direction != expected_dir:
        reasons.append("RETEST_DIRECTION_MISMATCH")

    # Sweep must already be known before the break candle begins.
    # Events on one OHLC candle do not reveal intra-candle order.
    if not (sweep.known_at_ms <= brk.occurred_at_ms and sweep.occurred_at_ms < brk.occurred_at_ms):
        reasons.append("SAME_BAR_OR_UNORDERED_SWEEP_BREAK")

    if brk.known_at_ms > fvg.known_at_ms:
        reasons.append("FVG_NOT_KNOWN_AFTER_BREAK")

    if not (fvg.known_at_ms <= retest.occurred_at_ms and fvg.known_at_ms < retest.known_at_ms):
        reasons.append("RETEST_BEFORE_FVG_CONFIRMATION")

    # Graph linkages
    if sweep.id not in brk.source_ids:
        reasons.append("BREAK_NOT_LINKED_TO_SWEEP")
    if brk.id not in fvg.source_ids:
        reasons.append("FVG_NOT_LINKED_TO_BREAK")
    if fvg.id not in retest.source_ids:
        reasons.append("RETEST_NOT_LINKED_TO_FVG")

    unique = tuple(dict.fromkeys(reasons))
    return ChainCheck(not unique, unique, ids)
