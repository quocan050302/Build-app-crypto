"""
backend/services/smc_scenario_binding.py
V14.4.1 - exact binding between a WatchSetup/PaperOrder and an immutable ScenarioSnapshot, plus the single
promotion path STRUCTURE_CONFIRMED scenario -> WatchSetup (the same table the legacy flow uses).

* `verify_scenario_binding` is the ONE validator used by auto-arm, manual eligibility and the chart API. A JSON
  blob with a `verified: true` key is never enough: the stored evidence is re-run through the measured chain
  predicates and every identity field (id/instance/direction/symbol/plan hash/versions/digest/cutoff) is
  compared with what the caller claims.
* `promote_confirmed_scenario` writes a WatchSetup revision (atomic plan, exact snapshot ref). It does NOT
  create orders, commit, or publish: arm still goes through `_auto_arm_candidate` and the common guards.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

import models
from services.smc_chain_service import validate_entry_evidence_bundle
from services.smc_scenario_config import ScenarioConfig, active_scenario_config
from services.smc_scenario_plan import make_plan_fingerprint
from services.smc_scenario_repository import get_scenario_snapshot, snapshot_from_row
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState

logger = logging.getLogger("smc_scenario_binding")

SCENARIO_WATCH_TF = "5M"


@dataclass(frozen=True)
class BindingResult:
    verified: bool
    reasons: Tuple[str, ...] = ()
    snapshot_id: Optional[str] = None
    snapshot: Optional[ScenarioSnapshot] = field(default=None, compare=False, repr=False)
    legacy: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"verified": self.verified, "reasons": list(self.reasons), "snapshot_id": self.snapshot_id,
                "legacy": self.legacy}


def _fail(reason: str, snapshot_id: Optional[str] = None, snap: Optional[ScenarioSnapshot] = None,
          legacy: bool = False) -> BindingResult:
    return BindingResult(False, (reason,), snapshot_id, snap, legacy)


def _nodes_for_chain(snap: ScenarioSnapshot) -> Optional[Dict[str, Dict[str, Any]]]:
    ids = (snap.trigger_chain or {}).get("node_ids") or {}
    by_id = {e.get("id"): e for e in (snap.evidence or [])}
    out: Dict[str, Dict[str, Any]] = {}
    for key in ("sweep", "break", "fvg", "retest"):
        nid = ids.get(key)
        if not nid or nid not in by_id:
            return None
        out[key] = by_id[nid]
    return out


def verify_scenario_binding(
    db: Session, *, snapshot_id: Optional[str], direction: Optional[str], symbol: str,
    setup_instance_id: Optional[str], plan_levels: Optional[Dict[str, Any]], now_ms: int,
    setup_id: Optional[str] = None, config: Optional[ScenarioConfig] = None,
) -> BindingResult:
    """Fail closed. `plan_levels` = {entry, stop_loss, take_profit} as RESOLVED from the watch/order (what would
    actually be traded); they must reproduce the snapshot's plan fingerprint."""
    cfg = config or active_scenario_config
    if not snapshot_id:
        return _fail("NO_SCENARIO_SNAPSHOT_REF")
    row = get_scenario_snapshot(db, snapshot_id)
    if row is None:
        return _fail("SCENARIO_SNAPSHOT_NOT_FOUND", snapshot_id)
    try:
        snap = snapshot_from_row(row)
    except ValueError as exc:
        if str(exc) == "LEGACY_SNAPSHOT_SCHEMA":
            return _fail("LEGACY_SNAPSHOT_SCHEMA", snapshot_id, legacy=True)
        return _fail("SCENARIO_SNAPSHOT_MALFORMED", snapshot_id)
    except (KeyError, TypeError, json.JSONDecodeError):
        return _fail("SCENARIO_SNAPSHOT_MALFORMED", snapshot_id)

    reasons: List[str] = []
    if snap.snapshot_id != snapshot_id:
        reasons.append("SNAPSHOT_ID_MISMATCH")
    if snap.symbol != symbol:
        reasons.append("SNAPSHOT_SYMBOL_MISMATCH")
    if snap.direction not in ("LONG", "SHORT") or snap.direction != direction:
        reasons.append("SNAPSHOT_DIRECTION_MISMATCH")
    if setup_instance_id is not None and snap.instance_id != setup_instance_id:
        reasons.append("SNAPSHOT_INSTANCE_MISMATCH")
    if snap.family != "STANDARD_SMC":
        reasons.append("SNAPSHOT_FAMILY_MISMATCH")
    if snap.state != ScenarioState.STRUCTURE_CONFIRMED:
        reasons.append(f"SNAPSHOT_STATE_{snap.state.value}")
    valid_digest = (snap.digest == snap.compute_digest() or
                    (hasattr(snap, "compute_digest_legacy") and snap.digest == snap.compute_digest_legacy()))
    if not valid_digest or row.evidence_digest != snap.digest:
        reasons.append("SNAPSHOT_DIGEST_MISMATCH")
    if snap.detector_version != cfg.detector_version:
        reasons.append("DETECTOR_VERSION_MISMATCH")
    if snap.config_version != cfg.config_version:
        reasons.append("CONFIG_VERSION_MISMATCH")
    active_profile = getattr(cfg, "requirement_profile", "V14.4_SMC_BASELINE")
    snap_profile = getattr(snap, "requirement_profile", "DEFAULT")
    if snap_profile not in (active_profile, "DEFAULT", "V14.4_SMC_BASELINE"):
        reasons.append("REQUIREMENT_PROFILE_MISMATCH")
    if snap.evidence_cutoff_ms > now_ms:
        reasons.append("SNAPSHOT_FROM_FUTURE")
    if not (snap.trigger_chain or {}).get("verified"):
        reasons.append("SNAPSHOT_CHAIN_NOT_VERIFIED")
    plan = snap.plan or {}
    if not plan.get("is_valid"):
        reasons.append("SNAPSHOT_PLAN_INVALID")
    elif plan_levels:
        try:
            fp = make_plan_fingerprint(symbol, direction or "", round(float(plan_levels["entry"]), 2),
                                       round(float(plan_levels["stop_loss"]), 2), round(float(plan_levels["take_profit"]), 2))
        except (KeyError, TypeError, ValueError):
            fp = None
        if fp is None or fp != snap.plan_fingerprint or fp != plan.get("plan_fingerprint"):
            reasons.append("PLAN_FINGERPRINT_MISMATCH")
    else:
        reasons.append("PLAN_LEVELS_UNAVAILABLE")

    nodes = _nodes_for_chain(snap)
    if nodes is None:
        reasons.append("CHAIN_NODES_MISSING")
    elif not reasons:
        res = validate_entry_evidence_bundle({
            "strategy_family": snap.family, "direction": snap.direction, "as_of_ms": now_ms, "symbol": symbol,
            "nodes": nodes, "planned_entry": plan.get("planned_entry"),
            "invalidation_price": plan.get("invalidation_price"), "target_price": plan.get("take_profit"),
            "regime": (snap.context or {}).get("regime"),
        })
        if not res.get("verified"):
            reasons.extend(res.get("reasons") or ["CHAIN_REVALIDATION_FAILED"])
    reasons = list(dict.fromkeys(reasons))
    return BindingResult(not reasons, tuple(reasons), snapshot_id, snap)


# ===================================================================================================
# Promotion (scenario -> WatchSetup revision). No orders, no commit, no event publication.
# ===================================================================================================
def watch_setup_id_for(symbol: str) -> str:
    return f"watch-{symbol}-{SCENARIO_WATCH_TF}"


def promote_confirmed_scenario(
    db: Session, snap: ScenarioSnapshot, *, now_ms: int, risk_config_version: int,
    leverage: int, margin_mode: str, risk_pct: float, expires_at_ms: int,
    estimated_liquidation: Optional[float] = None,
) -> Optional[models.WatchSetup]:
    """Create/refresh the WatchSetup of a STRUCTURE_CONFIRMED scenario with a VALID atomic plan.
    Returns None when the scenario is not promotable (still observation). ARMED / PAPER_OPEN watches are
    never rewritten (their frozen plan belongs to the order)."""
    plan = snap.plan or {}
    if snap.state != ScenarioState.STRUCTURE_CONFIRMED or snap.direction not in ("LONG", "SHORT"):
        return None
    if not plan.get("is_valid") or not (snap.trigger_chain or {}).get("verified"):
        return None

    setup_id = watch_setup_id_for(snap.symbol)
    ws = db.query(models.WatchSetup).filter(models.WatchSetup.id == setup_id).first()
    ctx = snap.context or {}
    conditions_met = [n.get("kind") for n in snap.evidence if n.get("kind") in
                      ("SWEEP_LOW", "SWEEP_HIGH", "MSS", "CHOCH", "BOS", "BREAK_UNCLASSIFIED", "FVG", "RETEST")]
    zone = next(({"top": n["extra"].get("top"), "bottom": n["extra"].get("bottom")}
                 for n in snap.evidence if n.get("kind") == "FVG" and n.get("extra")), None)
    fields: Dict[str, Any] = dict(
        setup_instance_id=snap.instance_id, strategy="SMC_SCENARIO", strategy_family="STANDARD_SMC",
        plan_kind="CONFIRMED", direction_source="SIGNAL", direction=snap.direction,
        timeframe=SCENARIO_WATCH_TF, state="READY",
        htf_bias=ctx.get("h4_bias") or "UNKNOWN", h1_alignment=ctx.get("h1_alignment") or "UNKNOWN",
        poi_zone=json.dumps({"fvg": zone, "scenario_id": snap.scenario_id}),
        trigger_mode="CONFIRMED_CLOSE",
        provisional_entry=plan["planned_entry"], provisional_sl=plan["stop_loss"], provisional_tp=plan["take_profit"],
        confirmed_entry=plan["planned_entry"], confirmed_sl=plan["stop_loss"], confirmed_tp=plan["take_profit"],
        invalidation_price=plan["invalidation_price"],
        invalidation_reason="Giá đóng cửa vượt đỉnh/đáy quét thanh khoản (cực trị wick của lệnh quét).",
        gross_rr=plan["gross_rr"], net_rr=plan["net_rr"], risk_usdt=plan["net_risk_usdt"], quantity=plan["quantity"],
        leverage=leverage, margin_mode=margin_mode, risk_pct=risk_pct, requested_risk_pct=risk_pct,
        effective_risk_pct=risk_pct, risk_profile="STANDARD", config_version=risk_config_version,
        estimated_liquidation=estimated_liquidation,
        conditions_met=json.dumps(conditions_met), conditions_remaining=json.dumps([]),
        distance_to_entry_atr=0.0, distance_to_entry_usdt=0.0, expires_at=expires_at_ms,
        scenario_snapshot_id=snap.snapshot_id, evidence_snapshot_id=None,
    )
    if ws is None:
        ws = models.WatchSetup(id=setup_id, version=1, created_at=now_ms, updated_at=now_ms, **fields)
        db.add(ws)
        db.flush()
        return ws
    if ws.state in ("ARMED", "PAPER_OPEN"):
        return ws
    if ws.scenario_snapshot_id == snap.snapshot_id and ws.state == "READY":
        return ws
    ws.version = (ws.version or 1) + 1
    for k, v in fields.items():
        setattr(ws, k, v)
    ws.quality_snapshot_id = None
    ws.updated_at = now_ms
    db.flush()
    return ws


# ===================================================================================================
# Shared structure verification for a WatchSetup (auto-arm AND manual eligibility use exactly this)
# ===================================================================================================
def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return None
    return v


def verify_setup_structure(
    db: Session, setup: Any, *, symbol: str, plan_levels: Optional[Dict[str, Any]], now_ms: int,
) -> Tuple[Optional[bool], Dict[str, Any], Optional[BindingResult]]:
    """-> (structure_chain_verified, evidence_dict, binding).
    * scenario_snapshot_id set  -> exact snapshot binding (True/False).
    * only legacy StrategyEvidence -> re-run the MEASURED validator on its nodes; legacy rows lack measured
      fields so they come back False (uncertified) instead of passing on 'has JSON content'.
    * nothing at all -> None (unknown), which the certified guard blocks."""
    family = getattr(setup, "strategy_family", None) or "STANDARD_SMC"
    if family != "STANDARD_SMC":
        return None, {}, None
    snap_id = getattr(setup, "scenario_snapshot_id", None)
    if snap_id:
        b = verify_scenario_binding(
            db, snapshot_id=snap_id, direction=getattr(setup, "direction", None), symbol=symbol,
            setup_instance_id=getattr(setup, "setup_instance_id", None), plan_levels=plan_levels, now_ms=now_ms,
            setup_id=getattr(setup, "id", None))
        return b.verified, {"scenario_binding": b.to_dict(), "structure_confirmed": b.verified}, b
    ev_id = getattr(setup, "evidence_snapshot_id", None)
    if ev_id:
        row = db.query(models.StrategyEvidence).filter(models.StrategyEvidence.id == ev_id).first()
        if row is None:
            return None, {}, None
        nodes: Dict[str, Any] = {}
        for key, col in (("sweep", row.sweep_evidence), ("break", row.displacement_evidence),
                         ("fvg", row.fvg_evidence), ("retest", row.retest_evidence)):
            val = _loads(col)
            if val:
                nodes[key] = val
        from services.smc_scenario_config import active_scenario_config
        if not active_scenario_config.certified_smc_entry_enabled:
            # Flag OFF: legacy completeness only (all three nodes present). Explicitly NOT certified.
            complete = all(k in nodes for k in ("sweep", "break", "fvg"))
            return complete, {"legacy_evidence": True, "legacy_uncertified": True,
                              "structure_confirmed": False, "reasons": [] if complete else ["LEGACY_INCOMPLETE"]}, None
        res = validate_entry_evidence_bundle({
            "strategy_family": family, "direction": getattr(setup, "direction", None), "as_of_ms": now_ms,
            "symbol": symbol, "nodes": nodes})
        return bool(res.get("verified")), {"legacy_evidence": True, "structure_confirmed": bool(res.get("verified")),
                                           "reasons": res.get("reasons", [])}, None
    return None, {}, None
