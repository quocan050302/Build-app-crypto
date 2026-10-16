"""
backend/services/cg01_evidence_adapter.py
V16.3 CG-01 Evidence and ScenarioSnapshot Adapter.
Implements Rules 096, 099, 121 - 127.

Binds CG-01 pure evaluation results to:
- EvidenceNode lists (HTF context, POI, Sweep, MSS, Displacement, FVG, CE trigger, SL, TP).
- TriggerChain dictionary.
- Immutable ScenarioSnapshot compatible with SMC Scenario repository and chart evidence API.
- build_cg01_rejection_summary for actionable diagnostic reporting.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from domain_calculator import CalculationResult
from services.cg01_types import CG01Config, CG01Decision, CG01EvidenceRefs, CG01Stage
from services.scalp_m1_types import ModelIdentifier
from services.smc_scenario_types import (
    EvidenceNode,
    FactStatus,
    ScenarioSnapshot,
    ScenarioState,
)
from services.zone_watchlist_service import WatchedZone


def build_cg01_evidence_nodes(
    symbol: str,
    direction: str,
    htf_data: Dict[str, Any],
    poi: WatchedZone,
    sweep: Dict[str, Any],
    mss: Dict[str, Any],
    fvg: Dict[str, Any],
    planned_entry: float,
    sl: float,
    tp: float,
    target_dec: Optional[Any],
    now_ms: int,
) -> List[Dict[str, Any]]:
    """
    Rule 096, 121 - 126: Constructs list of EvidenceNode dictionaries for CG01 candidate.
    """
    ev_nodes: List[Dict[str, Any]] = [
        # 1. HTF Context
        EvidenceNode(
            id=str(htf_data.get("evidence_id") or f"ev_htf_{now_ms}"),
            kind="HTF_CONTEXT",
            symbol=symbol,
            timeframe="1H",
            direction=direction,
            occurred_at_ms=now_ms,
            known_at_ms=now_ms,
            level=planned_entry,
            extra={
                "h4_bias": htf_data.get("h4_bias", "NEUTRAL"),
                "h1_bias": htf_data.get("h1_bias", "NEUTRAL"),
                "range_position": htf_data.get("range_position", "EQUILIBRIUM"),
            },
        ).to_dict(),

        # 2. HTF POI
        EvidenceNode(
            id=f"ev_poi_{poi.zone_id}",
            kind="POI",
            symbol=symbol,
            timeframe=poi.timeframe,
            direction=direction,
            occurred_at_ms=poi.known_at,
            known_at_ms=poi.known_at,
            level=poi.midpoint,
            extra={
                "zone_id": poi.zone_id,
                "bounds": [poi.bottom, poi.top],
                "poi_direction": poi.direction,
                "touch_count": poi.touch_count,
            },
        ).to_dict(),

        # 3. Liquidity Sweep
        EvidenceNode(
            id=str(sweep.get("sweep_id") or f"ev_sweep_{now_ms}"),
            kind="LIQUIDITY_SWEEP",
            symbol=symbol,
            timeframe="1M",
            direction=direction,
            occurred_at_ms=int(sweep.get("sweep_time_ms") or now_ms),
            known_at_ms=now_ms,
            level=float(sweep.get("sweep_extreme", planned_entry)),
            extra={
                "reference_id": sweep.get("reference_id"),
                "reference_price": sweep.get("reference_price"),
                "sweep_extreme": sweep.get("sweep_extreme"),
                "sweep_bar_idx": sweep.get("sweep_bar_idx"),
            },
        ).to_dict(),

        # 4. MSS
        EvidenceNode(
            id=str(mss.get("mss_id") or f"ev_mss_{now_ms}"),
            kind="MSS",
            symbol=symbol,
            timeframe="1M",
            direction=direction,
            occurred_at_ms=now_ms,
            known_at_ms=now_ms,
            level=float(mss.get("structure_ref_price", planned_entry)),
            extra={
                "structure_ref_id": mss.get("structure_ref_id"),
                "structure_ref_price": mss.get("structure_ref_price"),
                "mss_bar_idx": mss.get("mss_bar_idx"),
            },
        ).to_dict(),

        # 5. Displacement
        EvidenceNode(
            id=f"ev_disp_{mss.get('mss_id', now_ms)}",
            kind="DISPLACEMENT",
            symbol=symbol,
            timeframe="1M",
            direction=direction,
            occurred_at_ms=now_ms,
            known_at_ms=now_ms,
            level=float(mss.get("structure_ref_price", planned_entry)),
            extra={
                "body_atr_ratio": mss.get("body_atr_ratio", 1.0),
                "displacement_atr": mss.get("displacement_atr", 2.0),
            },
        ).to_dict(),

        # 6. FVG
        EvidenceNode(
            id=str(fvg.get("fvg_id") or f"ev_fvg_{now_ms}"),
            kind="FVG",
            symbol=symbol,
            timeframe="1M",
            direction=direction,
            occurred_at_ms=int(fvg.get("formation_time_ms") or now_ms),
            known_at_ms=now_ms,
            level=float(fvg.get("ce_price", planned_entry)),
            extra={
                "lower": fvg.get("lower"),
                "upper": fvg.get("upper"),
                "ce_price": fvg.get("ce_price"),
                "formation_time_ms": fvg.get("formation_time_ms"),
            },
        ).to_dict(),

        # 7. Entry Trigger (Limit at CE)
        EvidenceNode(
            id=f"ev_trig_{fvg.get('fvg_id', now_ms)}",
            kind="ENTRY_TRIGGER",
            symbol=symbol,
            timeframe="1M",
            direction=direction,
            occurred_at_ms=now_ms,
            known_at_ms=now_ms,
            level=planned_entry,
            extra={
                "entry_mode": "LIMIT_AT_CE_AFTER_CONFIRMATION",
                "planned_entry": planned_entry,
            },
        ).to_dict(),

        # 8. Structural Invalidation
        EvidenceNode(
            id=f"ev_inval_{sl}",
            kind="INVALIDATION",
            symbol=symbol,
            timeframe="1M",
            direction=direction,
            occurred_at_ms=now_ms,
            known_at_ms=now_ms,
            level=sl,
            extra={
                "sweep_extreme": sweep.get("sweep_extreme"),
                "invalidation_price": sl,
            },
        ).to_dict(),

        # 9. Liquidity Target
        EvidenceNode(
            id=f"ev_target_{tp}",
            kind="TARGET",
            symbol=symbol,
            timeframe="1M",
            direction=direction,
            occurred_at_ms=now_ms,
            known_at_ms=now_ms,
            level=tp,
            extra={
                "source_level_id": getattr(target_dec, "source_level_id", None) if target_dec else None,
                "target_type": "LIQUIDITY_TARGET",
                "target_price": tp,
            },
        ).to_dict(),
    ]

    return ev_nodes


def build_cg01_trigger_chain(
    ev_nodes: List[Dict[str, Any]],
    now_ms: int,
    model_id: str = ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
) -> Dict[str, Any]:
    """
    Rule 096, 122: Builds verified trigger chain dictionary.
    """
    return {
        "verified": True,
        "reasons": (),
        "node_ids": [n["id"] for n in ev_nodes],
        "model_id": model_id,
        "trigger_known_at": now_ms,
    }


def build_cg01_snapshot(
    symbol: str,
    direction: str,
    candidate_key: str,
    planned_entry: float,
    sl: float,
    tp: float,
    calc: CalculationResult,
    poi: WatchedZone,
    sweep: Dict[str, Any],
    mss: Dict[str, Any],
    fvg: Dict[str, Any],
    target_dec: Optional[Any],
    ev_nodes: List[Dict[str, Any]],
    trigger_chain: Dict[str, Any],
    config: Optional[CG01Config],
    now_ms: int,
) -> ScenarioSnapshot:
    """
    Rule 121 - 127: Builds immutable ScenarioSnapshot object for CG-01 ScalpCandidate.
    """
    cfg_version = config.config_version if config else 1
    model_ver = config.model_version if config else "v16.2"

    target_id = getattr(target_dec, "source_level_id", None) if target_dec else f"lvl_tp_{tp}"

    liquidity_map = {
        "swept": {
            "id": sweep.get("reference_id"),
            "price": sweep.get("reference_price"),
            "sweep_extreme": sweep.get("sweep_extreme"),
        },
        "target": {
            "id": target_id,
            "price": tp,
        },
    }

    summary_vi = (
        f"CG-01 {direction}: Quét thanh khoản {sweep.get('reference_price', 0.0):.2f}, "
        f"MSS qua {mss.get('structure_ref_price', 0.0):.2f}, FVG CE tại {planned_entry:.2f}, "
        f"SL {sl:.2f}, TP {tp:.2f} (Net R:R {calc.net_rr:.2f})."
    )

    plan = {
        "is_valid": True,
        "entry": planned_entry,
        "stop_loss": sl,
        "take_profit": tp,
        "net_rr": calc.net_rr,
        "gross_rr": calc.gross_rr,
        "quantity": calc.quantity,
        "initial_risk_usdt": calc.cost_breakdown.net_risk_usdt if hasattr(calc, "cost_breakdown") else round(abs(planned_entry - sl) * calc.quantity, 2),
    }

    snap = ScenarioSnapshot(
        scenario_id=f"scen-{candidate_key}",
        instance_id=candidate_key,
        revision=1,
        symbol=symbol,
        setup_timeframe="1M",
        context_timeframes=["4H", "1H", "15M"],
        family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        direction=direction,
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=now_ms,
        evidence_cutoff_ms=now_ms,
        evidence_ids=[n["id"] for n in ev_nodes],
        liquidity_map=liquidity_map,
        trigger_chain=trigger_chain,
        target={"id": target_id, "price": tp},
        invalidation={"price": sl},
        summary_vi=summary_vi,
        config_version=cfg_version,
        detector_version=model_ver,
        plan=plan,
        evidence=ev_nodes,
        session_name="LONDON_NY",
    )

    return snap


def build_cg01_rejection_summary(decision: CG01Decision) -> Dict[str, Any]:
    """
    Rule 099: Diagnostic summary of evaluation outcome when a setup is not executable.
    """
    stage_str = decision.stage.value if hasattr(decision.stage, "value") else str(decision.stage)
    return {
        "stage": stage_str,
        "is_eligible": decision.is_eligible,
        "reason_codes": list(decision.reason_codes),
        "missing_conditions": list(decision.missing_conditions),
        "as_of_ms": decision.as_of_ms,
        "waiting_step": (
            "Chờ bối cảnh HTF đủ nến" if stage_str == "OBSERVING" and "CONTEXT_WARMUP" in decision.reason_codes
            else "Chờ cửa sổ giao dịch Silver Bullet" if stage_str == "WAIT_WINDOW"
            else "Chờ vùng POI 15M hợp lệ" if stage_str == "WAIT_POI"
            else "Chờ quét thanh khoản (Sweep)" if stage_str == "WAIT_SWEEP"
            else "Chờ phá vỡ cấu trúc (MSS)" if stage_str == "WAIT_MSS"
            else "Chờ khoảng trống FVG cùng nhịp" if stage_str == "WAIT_FVG"
            else "Chờ giá retest khớp lệnh" if stage_str == "WAIT_ENTRY"
            else "Đang chờ thực thi" if stage_str == "EXECUTABLE"
            else stage_str
        ),
    }
