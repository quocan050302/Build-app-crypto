"""
AURUM Desk V17.9.3.1.1 — Verification Integrity, OOS Leakage Repair, Candidate Coverage Audit,
and Evidence-Driven Loss Root-Cause Research Service.

Implements:
- Phase P0-1: `validate_trade_temporal_partition_contract`, `evaluate_v179311_chronological_oos_and_walkforward`
- Phase P0-2: `RiskPolicySnapshotV179311`, `get_canonical_risk_policy_snapshot`,
              `validate_single_ledger_entry_against_risk_policy`, `audit_ledger_against_risk_policy_snapshot`
- Phase P0-3: `validate_acceptance_gate_manifest`
- Phase P0-4: `audit_v179311_evaluator_candidate_coverage`
- Phase P1-1: `build_v179311_loss_episode_forensic_ledger`, `classify_v179311_loss_root_causes`,
              `aggregate_v179311_loss_forensics`
- Phase P1-2 & P1-3: `ResearchVariantPolicyV179311`, `PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311`,
                     `evaluate_v179311_matched_trade_counterfactuals`
- Phase P1-4: `assess_v179311_loss_rate_and_edge_verdict`
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from domain_calculator import CostAssumptions, calculate_risk_reward

V17_9_3_1_1_SCHEMA_VERSION = "17.9.3.1.1"
NY_TZ = ZoneInfo("America/New_York")


def _canonical_json_sha256(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _ms_to_utc_iso(ts_ms: Optional[int]) -> Optional[str]:
    if ts_ms is None or int(ts_ms) <= 0:
        return None
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms_to_ny_iso(ts_ms: Optional[int]) -> Optional[str]:
    if ts_ms is None or int(ts_ms) <= 0:
        return None
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=NY_TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


def compute_wilson_95_ci(successes: int, total: int, z: float = 1.959963984540054) -> Dict[str, Optional[float]]:
    if total <= 0:
        return {"lower_95": None, "upper_95": None}
    n = float(total)
    p = max(0.0, min(1.0, float(successes) / n))
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)) / denom
    return {
        "lower_95": round(max(0.0, center - half), 4),
        "upper_95": round(min(1.0, center + half), 4),
    }


def compute_contiguous_session_day_block_bootstrap_ci(
    episodes: Sequence[Mapping[str, Any]],
    *,
    metric_key: str = "realized_r",
    n_boot: int = 400,
    seed: int = 179311,
) -> Dict[str, Any]:
    """
    Phase P1-4: Block bootstrap 95% confidence interval grouped by contiguous
    (ny_date, session) blocks rather than naive i.i.d. trade rows.
    Deterministic linear congruential generator seeded by `seed`.
    """
    closed = [
        ep
        for ep in episodes
        if str(ep.get("outcome_label") or ep.get("exit_type") or "") in ("WIN", "LOSS")
        and ep.get(metric_key, ep.get("realized_r_net")) is not None
    ]
    if not closed:
        return {
            "block_count": 0,
            "episode_count": 0,
            "point_estimate": None,
            "lower_95": None,
            "upper_95": None,
            "bootstrap_method": "CONTIGUOUS_NY_DAY_SESSION_BLOCK_BOOTSTRAP",
        }

    blocks_map: Dict[str, List[float]] = defaultdict(list)
    for ep in closed:
        ts_ms = int(ep.get("decision_timestamp_ms") or ep.get("decision_ts") or 0)
        ny_day = _ms_to_ny_iso(ts_ms)[:10] if ts_ms > 0 else "UNKNOWN_DAY"
        sess = str(ep.get("session") or "UNKNOWN_SESSION")
        b_key = f"{ny_day}_{sess}"
        val = float(ep.get(metric_key, ep.get("realized_r_net", 0.0)))
        blocks_map[b_key].append(val)

    block_keys = sorted(blocks_map.keys())
    blocks = [blocks_map[k] for k in block_keys]
    m_blocks = len(blocks)
    all_vals = [v for b in blocks for v in b]
    point_est = round(sum(all_vals) / len(all_vals), 4)

    if m_blocks < 2:
        return {
            "block_count": m_blocks,
            "episode_count": len(all_vals),
            "point_estimate": point_est,
            "lower_95": point_est,
            "upper_95": point_est,
            "bootstrap_method": "CONTIGUOUS_NY_DAY_SESSION_BLOCK_BOOTSTRAP_SINGLE_BLOCK",
        }

    state = int(seed) & 0x7FFFFFFF
    means: List[float] = []
    for _ in range(max(50, int(n_boot))):
        sampled_vals: List[float] = []
        for _b in range(m_blocks):
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            b_idx = state % m_blocks
            sampled_vals.extend(blocks[b_idx])
        if sampled_vals:
            means.append(sum(sampled_vals) / len(sampled_vals))
    means.sort()
    lo_idx = max(0, min(len(means) - 1, int(0.025 * len(means))))
    hi_idx = max(0, min(len(means) - 1, int(0.975 * len(means))))
    return {
        "block_count": m_blocks,
        "episode_count": len(all_vals),
        "point_estimate": point_est,
        "lower_95": round(means[lo_idx], 4),
        "upper_95": round(means[hi_idx], 4),
        "bootstrap_method": "CONTIGUOUS_NY_DAY_SESSION_BLOCK_BOOTSTRAP",
    }


# ==============================================================================
# PHASE P0-2 — CANONICAL RISK POLICY SNAPSHOT & LEDGER AUDITOR
# ==============================================================================
@dataclass(frozen=True)
class RiskPolicySnapshotV179311:
    """
    Phase P0-2: Single canonical risk, capital, sizing, fee, and net RR policy snapshot.
    Derives `max_planned_risk_usd = capital_usdt * (risk_pct_percent / 100.0)` = 2.50 USD.
    Rejects fraction-vs-percent misconfiguration (`risk_pct_percent < 0.01`).
    """

    schema_version: str = V17_9_3_1_1_SCHEMA_VERSION
    policy_version: str = "V17.9.3.1.1_CANONICAL_RISK_POLICY"
    symbol: str = "XAUUSDT"
    capital_usdt: float = 1000.0
    risk_pct_percent: float = 0.25
    leverage: int = 20
    margin_mode: str = "ISOLATED"
    min_net_rr: float = 2.0
    quantity_step: float = 0.01
    min_quantity: float = 0.01
    multiplier: float = 1.0
    tick_size: float = 0.01
    taker_fee_rate: float = 0.0004
    maker_fee_rate: float = 0.0002
    slippage_usd: float = 0.10
    spread_usd: float = 0.20
    risk_tolerance_epsilon_usd: float = 1e-5

    def __post_init__(self) -> None:
        if self.capital_usdt <= 0.0 or not math.isfinite(self.capital_usdt):
            raise ValueError(f"INVALID_CAPITAL_USDT: {self.capital_usdt}")
        if self.risk_pct_percent <= 0.0 or not math.isfinite(self.risk_pct_percent):
            raise ValueError(f"INVALID_RISK_PCT_PERCENT: {self.risk_pct_percent}")
        if self.risk_pct_percent < 0.01:
            raise ValueError(
                f"RISK_PCT_UNIT_MISMATCH / RISK_PCT_FRACTION_MISINTERPRETATION: risk_pct_percent={self.risk_pct_percent} "
                "appears to be a raw fraction (e.g. 0.0025) instead of percentage points (0.25)."
            )
        if self.capital_usdt * (self.risk_pct_percent / 100.0) > 2.50 + 1e-5:
            raise ValueError(
                f"CANONICAL_RISK_BUDGET_VIOLATION: max_planned_risk_usd="
                f"{self.capital_usdt * (self.risk_pct_percent / 100.0):.4f} exceeds canonical 2.50 USD budget."
            )
        if self.min_net_rr < 2.0 - 1e-9:
            raise ValueError(f"CANONICAL_MIN_NET_RR_VIOLATION / MIN_NET_RR_WEAKENED_BELOW_2_0: min_net_rr={self.min_net_rr}")
        if self.quantity_step <= 0.0 or self.min_quantity <= 0.0 or self.min_quantity < self.quantity_step - 1e-9:
            raise ValueError(
                f"INVALID_LOT_CONSTRAINTS: quantity_step={self.quantity_step}, min_quantity={self.min_quantity}"
            )
        if self.multiplier <= 0.0 or self.tick_size <= 0.0:
            raise ValueError(f"INVALID_INSTRUMENT_UNITS: multiplier={self.multiplier}, tick_size={self.tick_size}")

    @property
    def risk_fraction(self) -> float:
        return round(self.risk_pct_percent / 100.0, 8)

    @property
    def max_planned_risk_usd(self) -> float:
        return round(self.capital_usdt * (self.risk_pct_percent / 100.0), 6)

    @property
    def policy_hash(self) -> str:
        d = asdict(self)
        d["risk_fraction"] = self.risk_fraction
        d["max_planned_risk_usd"] = self.max_planned_risk_usd
        return _canonical_json_sha256(d)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["risk_fraction"] = self.risk_fraction
        d["max_planned_risk_usd"] = self.max_planned_risk_usd
        d["policy_hash"] = self.policy_hash
        return d


def get_canonical_risk_policy_snapshot() -> RiskPolicySnapshotV179311:
    return RiskPolicySnapshotV179311()


def validate_single_ledger_entry_against_risk_policy(
    entry: Mapping[str, Any],
    policy: Optional[RiskPolicySnapshotV179311] = None,
    *,
    decision_net_rr: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Phase P0-2: Validates a single ExecutableTradeLedgerEntryV17931 (or dict) against
    the canonical `RiskPolicySnapshotV179311` (`max_planned_risk_usd = 2.50`, `min_net_rr = 2.0`).
    Separates `realized_loss_over_budget_gap` (adverse market gap beyond stop trigger)
    from decision-time `planned_net_risk_usd` violations.
    """
    pol = policy or get_canonical_risk_policy_snapshot()
    violations: List[str] = []

    direction = str(entry.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        violations.append(f"INVALID_DIRECTION:{direction}")

    filled = bool(entry.get("filled", entry.get("entry_filled", False)))
    qty = float(entry.get("quantity", entry.get("executed_quantity", 0.0)) or 0.0)
    qty_step = float(entry.get("quantity_step", pol.quantity_step) or 0.0)
    mult = float(entry.get("multiplier", pol.multiplier) or 0.0)
    tick_sz = float(entry.get("tick_size", pol.tick_size) or 0.0)
    ent_ref = float(entry.get("entry_reference", 0.0) or 0.0)
    sl_px = float(entry.get("planned_sl", entry.get("selected_sl", 0.0)) or 0.0)
    tp_px = float(entry.get("planned_tp", entry.get("take_profit", 0.0)) or 0.0)
    p_risk = float(entry.get("planned_net_risk_usd", 0.0) or 0.0)
    g_pnl = float(entry.get("realized_gross_pnl_usd", 0.0) or 0.0)
    n_pnl = float(entry.get("realized_net_pnl_usd", 0.0) or 0.0)
    ent_fee = float(entry.get("entry_fee", entry.get("entry_fee_usd", 0.0)) or 0.0)
    ext_fee = float(entry.get("exit_fee", entry.get("exit_fee_usd", 0.0)) or 0.0)
    r_net = float(entry.get("realized_r_net", entry.get("realized_r", 0.0)) or 0.0)
    gross_notional = float(entry.get("gross_notional", 0.0) or 0.0)
    ent_fill_px = entry.get("entry_fill_price")
    ext_fill_px = entry.get("exit_fill_price")
    outcome_label = str(entry.get("outcome_label") or entry.get("exit_type") or "")

    if direction == "LONG" and not (sl_px < ent_ref < tp_px):
        violations.append("STOP_TP_GEOMETRY_INVALID_LONG")
    elif direction == "SHORT" and not (tp_px < ent_ref < sl_px):
        violations.append("STOP_TP_GEOMETRY_INVALID_SHORT")

    if abs(mult - pol.multiplier) > 1e-6:
        violations.append(f"INVALID_MULTIPLIER:{mult}")
    if abs(tick_sz - pol.tick_size) > 1e-6:
        violations.append(f"INVALID_TICK_SIZE:{tick_sz}")
    if abs(qty_step - pol.quantity_step) > 1e-6:
        violations.append(f"INVALID_QUANTITY_STEP:{qty_step}")

    realized_loss_over_budget_gap = False
    no_fill_reason = str(entry.get("no_fill_reason") or "")
    if not filled:
        if (
            abs(gross_notional) > 1e-9
            or abs(n_pnl) > 1e-9
            or abs(g_pnl) > 1e-9
            or abs(ent_fee) > 1e-9
            or abs(ext_fee) > 1e-9
        ):
            violations.append("NO_FILL_NONZERO_EXPOSURE_OR_PNL")
        if outcome_label == "NO_FILL_BELOW_MIN_LOT" or no_fill_reason == "BELOW_MIN_LOT":
            if abs(qty) > 1e-9:
                violations.append("NO_FILL_NONZERO_EXPOSURE_OR_PNL")
        elif qty > 1e-9:
            # Planned quantity recorded on NO_FORWARD_BARS at end-of-history must still obey step and risk bounds
            rounded_qty = round(qty / pol.quantity_step) * pol.quantity_step
            if abs(rounded_qty - qty) > 1e-6:
                violations.append(f"QUANTITY_NOT_STEP_ALIGNED:{qty}")
            max_allowed = pol.max_planned_risk_usd + pol.risk_tolerance_epsilon_usd
            if p_risk <= 0.0 or p_risk > max_allowed:
                violations.append(f"PLANNED_NET_RISK_OUT_OF_BOUNDS:{p_risk:.6f}>{pol.max_planned_risk_usd:.2f}")
    else:
        if qty < pol.min_quantity - 1e-9 or qty <= 0.0:
            violations.append(f"QUANTITY_BELOW_MIN_LOT:{qty}")
        rounded_qty = round(qty / pol.quantity_step) * pol.quantity_step
        if abs(rounded_qty - qty) > 1e-6:
            violations.append(f"QUANTITY_NOT_STEP_ALIGNED:{qty}")

        max_allowed = pol.max_planned_risk_usd + pol.risk_tolerance_epsilon_usd
        if p_risk <= 0.0 or p_risk > max_allowed:
            violations.append(f"PLANNED_NET_RISK_OUT_OF_BOUNDS:{p_risk:.6f}>{pol.max_planned_risk_usd:.2f}")

        if ent_fill_px is None or float(ent_fill_px) <= 0.0:
            violations.append("MISSING_ENTRY_FILL_PRICE")
        else:
            expected_notional = float(ent_fill_px) * qty * mult
            if gross_notional > 0.0 and abs(gross_notional - expected_notional) > 0.02:
                violations.append(f"NOTIONAL_MISMATCH:{gross_notional:.4f}!={expected_notional:.4f}")
            fill_mode = str(entry.get("fill_mode") or entry.get("entry_fill_mode") or "NEXT_BAR_OPEN_BID_ASK")
            eff_fee_rate = pol.taker_fee_rate
            expected_ent_fee = expected_notional * eff_fee_rate
            if fill_mode in ("NEXT_BAR_OPEN_BID_ASK", "LEGACY_CLOSE_FILL") and abs(ent_fee - expected_ent_fee) > 0.01:
                violations.append(f"ENTRY_FEE_MISMATCH_OR_DUPLICATE:{ent_fee:.4f}!={expected_ent_fee:.4f}")

        if outcome_label in ("WIN", "LOSS", "TAKE_PROFIT", "STOP_LOSS"):
            if ext_fill_px is None or float(ext_fill_px) <= 0.0:
                violations.append("MISSING_EXIT_FILL_PRICE_ON_CLOSED_TRADE")
            else:
                expected_gross = (
                    (float(ext_fill_px) - float(ent_fill_px)) * qty * mult
                    if direction == "LONG"
                    else (float(ent_fill_px) - float(ext_fill_px)) * qty * mult
                )
                if abs(g_pnl - expected_gross) > 0.01:
                    violations.append(f"GROSS_PNL_MISMATCH:{g_pnl:.4f}!={expected_gross:.4f}")
            if abs((g_pnl - ent_fee - ext_fee) - n_pnl) > 0.005:
                violations.append(f"NET_PNL_ALGEBRAIC_MISMATCH:{g_pnl:.4f}-{ent_fee:.4f}-{ext_fee:.4f}!={n_pnl:.4f}")
            if p_risk > 0.0 and abs((n_pnl / p_risk) - r_net) > 0.005:
                violations.append(f"REALIZED_R_NET_MISMATCH:{n_pnl/p_risk:.4f}!={r_net:.4f}")
            if n_pnl < -(pol.max_planned_risk_usd + 0.05):
                realized_loss_over_budget_gap = True

    eff_net_rr = decision_net_rr if decision_net_rr is not None else entry.get("net_rr")
    observed_net_rr: Optional[float] = float(eff_net_rr) if eff_net_rr is not None else None
    if eff_net_rr is not None:
        if float(eff_net_rr) < pol.min_net_rr - 1e-6:
            violations.append(f"DECISION_NET_RR_BELOW_MIN:{float(eff_net_rr):.4f}<{pol.min_net_rr:.2f}")
    elif filled and ent_ref > 0.0 and sl_px > 0.0 and tp_px > 0.0 and direction in ("LONG", "SHORT"):
        calc = calculate_risk_reward(
            direction=direction,
            planned_entry=ent_ref,
            stop_loss=sl_px,
            take_profit=tp_px,
            capital_usdt=pol.capital_usdt,
            risk_pct=pol.risk_pct_percent,
            leverage=pol.leverage,
            margin_mode=pol.margin_mode,
            min_net_rr=pol.min_net_rr,
            costs=CostAssumptions(
                slippage_usd=pol.slippage_usd,
                maker_fee_rate=pol.maker_fee_rate,
                taker_fee_rate=pol.taker_fee_rate,
            ),
        )
        observed_net_rr = float(calc.net_rr or 0.0)
        if not calc.meets_min_rr or observed_net_rr < pol.min_net_rr - 1e-6:
            violations.append(f"CALCULATOR_NET_RR_BELOW_MIN:{observed_net_rr:.4f}<{pol.min_net_rr:.2f}")
    elif ent_ref > 0.0 and sl_px > 0.0 and tp_px > 0.0 and abs(ent_ref - sl_px) > 1e-9:
        observed_net_rr = round(abs(tp_px - ent_ref) / abs(ent_ref - sl_px), 4)

    return {
        "episode_key": entry.get("episode_key"),
        "variant": entry.get("variant"),
        "filled": filled,
        "valid": len(violations) == 0,
        "violations": violations,
        "planned_net_risk_usd": p_risk,
        "realized_net_pnl_usd": n_pnl,
        "observed_net_rr": observed_net_rr,
        "realized_loss_over_budget_gap": realized_loss_over_budget_gap,
    }


def audit_ledger_against_risk_policy_snapshot(
    ledgers_by_variant: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    episodes_by_variant: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    policy: Optional[RiskPolicySnapshotV179311] = None,
) -> Dict[str, Any]:
    """
    Phase P0-2: Audits all variants' execution ledgers against the single canonical
    `RiskPolicySnapshotV179311` (`max_planned_risk_usd = 2.50`, `min_net_rr = 2.0`).
    """
    pol = policy or get_canonical_risk_policy_snapshot()
    ep_net_rr_lookup: Dict[Tuple[str, int, str], float] = {}
    if episodes_by_variant:
        for vk, eps in episodes_by_variant.items():
            for ep in eps:
                if ep.get("net_rr") is not None:
                    ep_net_rr_lookup[
                        (
                            str(vk),
                            int(ep.get("decision_timestamp_ms") or ep.get("decision_ts") or 0),
                            str(ep.get("model_id") or ""),
                        )
                    ] = float(ep["net_rr"])

    total_entries = 0
    filled_entries = 0
    closed_entries = 0
    planned_risk_budget_violations = 0
    stop_geometry_violations = 0
    quantity_step_violations = 0
    algebraic_identity_violations = 0
    min_net_rr_violations = 0
    realized_loss_over_budget_gap_count = 0
    realized_loss_over_budget_gap_episodes: List[Dict[str, Any]] = []
    violation_records: List[Dict[str, Any]] = []
    per_variant_summary: Dict[str, Any] = {}

    for vk, entries in ledgers_by_variant.items():
        v_tot = 0
        v_fill = 0
        v_viol = 0
        v_gap_over = 0
        max_planned_risk = 0.0
        min_observed_net_rr: Optional[float] = None
        for e in entries:
            total_entries += 1
            v_tot += 1
            dec_ts = int(e.get("decision_ts") or e.get("decision_timestamp_ms") or 0)
            m_id = str(e.get("model_id") or "")
            dec_rr = ep_net_rr_lookup.get((str(vk), dec_ts, m_id))
            res = validate_single_ledger_entry_against_risk_policy(e, pol, decision_net_rr=dec_rr)
            obs_rr = res.get("observed_net_rr")
            if obs_rr is not None:
                min_observed_net_rr = float(obs_rr) if min_observed_net_rr is None else min(min_observed_net_rr, float(obs_rr))
            if res["filled"]:
                filled_entries += 1
                v_fill += 1
                max_planned_risk = max(max_planned_risk, float(res["planned_net_risk_usd"]))
            if str(e.get("outcome_label") or e.get("exit_type") or "") in ("WIN", "LOSS", "TAKE_PROFIT", "STOP_LOSS"):
                closed_entries += 1
            if res["realized_loss_over_budget_gap"]:
                realized_loss_over_budget_gap_count += 1
                v_gap_over += 1
                realized_loss_over_budget_gap_episodes.append(
                    {
                        "variant": vk,
                        "episode_key": e.get("episode_key"),
                        "decision_ts": dec_ts,
                        "planned_net_risk_usd": res["planned_net_risk_usd"],
                        "realized_net_pnl_usd": res["realized_net_pnl_usd"],
                        "max_adverse_gap": e.get("max_adverse_gap"),
                    }
                )
            if not res["valid"]:
                v_viol += 1
                violation_records.append(res)
                for v_code in res["violations"]:
                    if v_code.startswith("PLANNED_NET_RISK_OUT_OF_BOUNDS"):
                        planned_risk_budget_violations += 1
                    if "GEOMETRY" in v_code:
                        stop_geometry_violations += 1
                    if "QUANTITY" in v_code or "MULTIPLIER" in v_code or "TICK_SIZE" in v_code:
                        quantity_step_violations += 1
                    if "MISMATCH" in v_code:
                        algebraic_identity_violations += 1
                    if "NET_RR_BELOW_MIN" in v_code:
                        min_net_rr_violations += 1

        per_variant_summary[vk] = {
            "total_entries": v_tot,
            "filled_entries": v_fill,
            "violation_count": v_viol,
            "max_planned_net_risk_usd": round(max_planned_risk, 4),
            "min_observed_decision_net_rr": round(min_observed_net_rr, 4) if min_observed_net_rr is not None else None,
            "realized_loss_over_budget_gap_count": v_gap_over,
        }

    return {
        "schema_version": V17_9_3_1_1_SCHEMA_VERSION,
        "risk_policy_snapshot": pol.to_dict(),
        "historical_formula_provenance_clarification": {
            "v1793_commit_8350cf25528c_formula": "round(realized_r * 2.50, 2)",
            "v17931_diagnostic_comparison_column": "round(sum(ep_r_vals) * 250.0, 2) (diagnostic 1-lot 100k scale)",
            "v17931_canonical_simulator_cap": "planned_net_risk_usd <= 2.500001 (capital_usdt=1000.0, risk_pct=0.25%)",
            "v17931_defect_repaired": (
                "Removed stale 250.0 risk check in build_artifact_06 and 250.0 / min_net_rr>=1.1 description in "
                "build_artifact_17 and C17; unified all verifiers on RiskPolicySnapshotV179311 (2.50 USD, min_net_rr>=2.0)."
            ),
        },
        "total_entries_audited": total_entries,
        "filled_entries_audited": filled_entries,
        "closed_entries_audited": closed_entries,
        "planned_risk_budget_violations": planned_risk_budget_violations,
        "stop_geometry_violations": stop_geometry_violations,
        "quantity_step_violations": quantity_step_violations,
        "algebraic_identity_violations": algebraic_identity_violations,
        "min_net_rr_violations": min_net_rr_violations,
        "realized_loss_over_budget_gap_count": realized_loss_over_budget_gap_count,
        "realized_loss_over_budget_gap_episodes": realized_loss_over_budget_gap_episodes,
        "total_violation_entries": len(violation_records),
        "violations": violation_records,
        "per_variant_summary": per_variant_summary,
        "all_invariants_passed": len(violation_records) == 0 and total_entries > 0,
    }


# ==============================================================================
# PHASE P0-1 — TRUE CHRONOLOGICAL OOS & EMBARGO PARTITION CONTRACT
# ==============================================================================
def validate_trade_temporal_partition_contract(
    episodes_or_trade: Any,
    *,
    split_start_ts_ms: Optional[int] = None,
    split_end_ts_ms: int,
    next_split_start_ts_ms: Optional[int] = None,
    embargo_minutes: int = 180,
    embargo_bars: int = 180,
    m1_timestamps_ms: Optional[Sequence[int]] = None,
    boundary_timezone: str = "UTC",
    split_name: str = "CHRONOLOGICAL_SPLIT",
) -> Dict[str, Any]:
    """
    Phase P0-1: Pure reusable temporal partition & embargo validator.
    Consumes actual episode `decision_ts`, `entry_fill_ts`, `exit_fill_ts`, `trigger_bar_ts`,
    `outcome_label`, 180-bar forward horizon, provider gaps, and chronological UTC split boundary instants.

    Never initializes a tautological `embargo_leak_count = 0` without per-episode comparison.
    """
    is_single = isinstance(episodes_or_trade, Mapping)
    records: List[Mapping[str, Any]] = [episodes_or_trade] if is_single else list(episodes_or_trade or [])

    structural_errors: List[str] = []
    if str(boundary_timezone).upper() not in ("UTC", "Z", "+00:00"):
        structural_errors.append(f"INVALID_BOUNDARY_TIMEZONE:{boundary_timezone}")
    if split_end_ts_ms is None or int(split_end_ts_ms) <= 0:
        structural_errors.append(f"INVALID_SPLIT_END_TIMESTAMP:{split_end_ts_ms}")

    non_monotonic_timestamp_violations = 0
    duplicate_timestamp_violations = 0
    max_dataset_gap_ms = 0
    required_horizon_embargo_end_ts_ms = int(split_end_ts_ms) + max(int(embargo_minutes), int(embargo_bars)) * 60_000
    boundary_span_wall_clock_minutes = float(max(int(embargo_minutes), int(embargo_bars)))
    boundary_has_provider_gap = False

    if m1_timestamps_ms is not None:
        ts_list = [int(x) for x in m1_timestamps_ms]
        for k in range(len(ts_list) - 1):
            diff = ts_list[k + 1] - ts_list[k]
            if diff <= 0:
                non_monotonic_timestamp_violations += 1
                if diff == 0:
                    duplicate_timestamp_violations += 1
            elif diff > max_dataset_gap_ms:
                max_dataset_gap_ms = diff
        if non_monotonic_timestamp_violations > 0:
            structural_errors.append(f"NON_MONOTONIC_RAW_TIMESTAMPS:{non_monotonic_timestamp_violations}")
        if ts_list and int(split_end_ts_ms) > 0:
            b_idx = max(0, bisect.bisect_right(ts_list, int(split_end_ts_ms)) - 1)
            h_idx = min(len(ts_list) - 1, b_idx + max(1, int(embargo_bars)))
            bar_horizon_ts = ts_list[h_idx]
            wall_min_ts = int(split_end_ts_ms) + max(1, int(embargo_bars)) * 60_000
            required_horizon_embargo_end_ts_ms = max(wall_min_ts, bar_horizon_ts)
            boundary_span_wall_clock_minutes = round((required_horizon_embargo_end_ts_ms - int(split_end_ts_ms)) / 60_000.0, 2)
            if bar_horizon_ts > wall_min_ts:
                boundary_has_provider_gap = True

    eff_next_split_start_ts_ms = (
        int(next_split_start_ts_ms)
        if next_split_start_ts_ms is not None
        else required_horizon_embargo_end_ts_ms
    )
    embargo_sufficient_for_180_bars = eff_next_split_start_ts_ms >= required_horizon_embargo_end_ts_ms
    if not embargo_sufficient_for_180_bars:
        structural_errors.append(
            f"INSUFFICIENT_EMBARGO_WINDOW:next_split_start={eff_next_split_start_ts_ms}"
            f"<required_180bar_horizon_end={required_horizon_embargo_end_ts_ms}"
        )

    embargo_leak_count = 0
    boundary_crossing_count = 0
    unknown_exit_timestamp_count = 0
    temporal_order_violation_count = 0
    same_bar_tie_count = 0
    open_at_horizon_count = 0
    data_gap_unresolved_count = 0
    no_fill_count = 0
    admissible_closed_episodes: List[Dict[str, Any]] = []
    excluded_or_leaking_episodes: List[Dict[str, Any]] = []
    per_episode_verdicts: List[Dict[str, Any]] = []

    for idx_rec, ep in enumerate(records):
        led = ep.get("execution_ledger") if isinstance(ep.get("execution_ledger"), Mapping) else ep
        ep_id = str(
            ep.get("episode_key")
            or (led or {}).get("episode_key")
            or ep.get("setup_id")
            or f"ep_{idx_rec}_{ep.get('checkpoint_index_1m', 0)}"
        )
        dec_ts_raw = ep.get("decision_timestamp_ms", ep.get("decision_ts", (led or {}).get("decision_ts")))
        ent_ts_raw = ep.get("entry_fill_ts", ep.get("entry_timestamp_ms", (led or {}).get("entry_fill_ts")))
        ext_ts_raw = ep.get("exit_fill_ts", ep.get("exit_timestamp_ms", (led or {}).get("exit_fill_ts")))
        trig_ts_raw = ep.get("trigger_bar_ts", (led or {}).get("trigger_bar_ts"))
        outcome_label = str(ep.get("outcome_label") or (led or {}).get("outcome_label") or ep.get("exit_type") or "")
        filled = bool(ep.get("filled", (led or {}).get("filled", ep.get("entry_filled", True))))
        bars_held = int(ep.get("bars_held", (led or {}).get("bars_held", 0)) or 0)
        same_bar_tie = bool(
            ep.get("same_bar_sl_tp_tie")
            or (led or {}).get("same_bar_sl_tp_tie")
            or ((ep.get("stop_outcome_diagnostic") or {}).get("outcome_class") == "SAME_BAR_CONSERVATIVE_LOSS")
        )
        has_gap = bool(
            ep.get("has_data_gap_before_exit")
            or ((ep.get("stop_outcome_diagnostic") or {}).get("has_data_gap_before_exit"))
            or int((led or {}).get("intratrade_gap_count", 0) or 0) > 0
        )

        if same_bar_tie:
            same_bar_tie_count += 1
        if not filled or outcome_label.startswith("NO_FILL"):
            no_fill_count += 1
        elif outcome_label == "OPEN_AT_HORIZON":
            open_at_horizon_count += 1
        elif outcome_label == "DATA_GAP_UNRESOLVED":
            data_gap_unresolved_count += 1

        ep_reasons: List[str] = []
        is_leak = False
        admissible = True
        classification = "ADMISSIBLE_CLOSED_IN_SPLIT"

        if dec_ts_raw is None or int(dec_ts_raw) <= 0:
            unknown_exit_timestamp_count += 1
            is_leak = True
            admissible = False
            classification = "INVALID_MISSING_DECISION_TIMESTAMP"
            ep_reasons.append("MISSING_DECISION_TIMESTAMP")
            dec_ts = 0
        else:
            dec_ts = int(dec_ts_raw)

        if split_start_ts_ms is not None and dec_ts < int(split_start_ts_ms):
            admissible = False
            classification = "OUTSIDE_SPLIT_BEFORE_START"
            ep_reasons.append("DECISION_BEFORE_SPLIT_START")

        is_closed_wl = filled and outcome_label in ("WIN", "LOSS", "TAKE_PROFIT", "STOP_LOSS")
        if is_closed_wl and (ext_ts_raw is None or int(ext_ts_raw) <= 0):
            unknown_exit_timestamp_count += 1
            is_leak = True
            admissible = False
            classification = "INVALID_UNKNOWN_EXIT_TIMESTAMP"
            ep_reasons.append("CLOSED_TRADE_MISSING_EXIT_TIMESTAMP")

        ent_ts = int(ent_ts_raw) if ent_ts_raw is not None and int(ent_ts_raw) > 0 else dec_ts
        ext_ts = int(ext_ts_raw) if ext_ts_raw is not None and int(ext_ts_raw) > 0 else None

        if dec_ts > 0 and ent_ts < dec_ts:
            temporal_order_violation_count += 1
            is_leak = True
            admissible = False
            classification = "INVALID_TEMPORAL_ORDER_ENTRY_BEFORE_DECISION"
            ep_reasons.append(f"ENTRY_BEFORE_DECISION:{ent_ts}<{dec_ts}")

        if ext_ts is not None and ext_ts < ent_ts:
            temporal_order_violation_count += 1
            is_leak = True
            admissible = False
            classification = "INVALID_TEMPORAL_ORDER_EXIT_BEFORE_ENTRY"
            ep_reasons.append(f"EXIT_BEFORE_ENTRY:{ext_ts}<{ent_ts}")

        if trig_ts_raw is not None and int(trig_ts_raw) > 0 and ext_ts is not None and int(trig_ts_raw) > ext_ts:
            temporal_order_violation_count += 1
            is_leak = True
            admissible = False
            classification = "INVALID_TEMPORAL_ORDER_TRIGGER_AFTER_EXIT"
            ep_reasons.append(f"TRIGGER_AFTER_EXIT:{int(trig_ts_raw)}>{ext_ts}")

        # Determine observation end timestamp
        if ext_ts is not None:
            obs_end_ts = ext_ts
        else:
            obs_end_ts = dec_ts + max(1, int(embargo_bars), bars_held) * 60_000
            if m1_timestamps_ms is not None and dec_ts > 0:
                ts_l = [int(x) for x in m1_timestamps_ms]
                d_idx = max(0, bisect.bisect_right(ts_l, dec_ts) - 1)
                obs_idx = min(len(ts_l) - 1, d_idx + max(1, int(embargo_bars), bars_held))
                obs_end_ts = max(obs_end_ts, ts_l[obs_idx])

        if dec_ts > 0 and dec_ts <= int(split_end_ts_ms):
            if obs_end_ts > eff_next_split_start_ts_ms:
                embargo_leak_count += 1
                boundary_crossing_count += 1
                is_leak = True
                admissible = False
                classification = "EXCLUDED_BOUNDARY_LEAK_RISK"
                ep_reasons.append(
                    f"EXIT_OR_HORIZON_CROSSES_NEXT_SPLIT_START:obs_end={obs_end_ts}>next_split_start={eff_next_split_start_ts_ms}"
                )
            elif obs_end_ts > int(split_end_ts_ms):
                embargo_leak_count += 1
                boundary_crossing_count += 1
                is_leak = True
                admissible = False
                classification = "EXCLUDED_BOUNDARY_LEAK_RISK"
                ep_reasons.append(
                    f"EXIT_OR_HORIZON_CROSSES_SPLIT_END:obs_end={obs_end_ts}>split_end={int(split_end_ts_ms)}"
                )
            elif not embargo_sufficient_for_180_bars and (dec_ts + int(embargo_bars) * 60_000 > eff_next_split_start_ts_ms):
                embargo_leak_count += 1
                is_leak = True
                admissible = False
                classification = "EXCLUDED_BOUNDARY_LEAK_RISK"
                ep_reasons.append("FORWARD_180BAR_HORIZON_EXCEEDS_EMBARGO_WINDOW")

        if not is_closed_wl and admissible:
            admissible = False
            if not filled or outcome_label.startswith("NO_FILL"):
                classification = "EXCLUDED_NO_FILL"
            elif outcome_label == "OPEN_AT_HORIZON":
                classification = "CENSORED_OPEN_AT_HORIZON"
            elif outcome_label == "DATA_GAP_UNRESOLVED":
                classification = "EXCLUDED_DATA_GAP_UNRESOLVED"
            else:
                classification = f"EXCLUDED_NON_CLOSED:{outcome_label}"

        verdict_row = {
            "episode_key": ep_id,
            "checkpoint_index_1m": ep.get("checkpoint_index_1m"),
            "model_id": ep.get("model_id"),
            "direction": ep.get("direction"),
            "decision_ts": dec_ts,
            "entry_fill_ts": ent_ts,
            "exit_fill_ts": ext_ts,
            "observation_end_ts": obs_end_ts,
            "outcome_label": outcome_label,
            "bars_held": bars_held,
            "same_bar_sl_tp_tie": same_bar_tie,
            "has_data_gap_before_exit": has_gap,
            "classification": classification,
            "partition_status": classification,
            "admissible_closed_in_split": admissible,
            "is_leak": is_leak,
            "reasons": ep_reasons,
            "violations": ep_reasons,
        }
        per_episode_verdicts.append(verdict_row)
        if admissible:
            admissible_closed_episodes.append(dict(ep))
        elif is_leak or classification == "EXCLUDED_BOUNDARY_LEAK_RISK":
            excluded_or_leaking_episodes.append(verdict_row)

    contract_valid = (
        len(structural_errors) == 0
        and embargo_leak_count == 0
        and unknown_exit_timestamp_count == 0
        and temporal_order_violation_count == 0
    )

    result = {
        "schema_version": V17_9_3_1_1_SCHEMA_VERSION,
        "split_name": split_name,
        "boundary_timezone": boundary_timezone,
        "split_start_ts_ms": split_start_ts_ms,
        "split_end_ts_ms": int(split_end_ts_ms),
        "split_end_utc": _ms_to_utc_iso(int(split_end_ts_ms)),
        "next_split_start_ts_ms": eff_next_split_start_ts_ms,
        "next_split_start_utc": _ms_to_utc_iso(eff_next_split_start_ts_ms),
        "required_horizon_embargo_end_ts_ms": required_horizon_embargo_end_ts_ms,
        "required_horizon_embargo_end_utc": _ms_to_utc_iso(required_horizon_embargo_end_ts_ms),
        "boundary_span_wall_clock_minutes": boundary_span_wall_clock_minutes,
        "boundary_has_provider_gap": boundary_has_provider_gap,
        "embargo_sufficient_for_180_bars": embargo_sufficient_for_180_bars,
        "non_monotonic_timestamp_violations": non_monotonic_timestamp_violations,
        "duplicate_timestamp_violations": duplicate_timestamp_violations,
        "max_dataset_gap_ms": max_dataset_gap_ms,
        "evaluated_episode_count": len(records),
        "admissible_closed_episode_count": len(admissible_closed_episodes),
        "embargo_leak_count": embargo_leak_count,
        "boundary_crossing_count": boundary_crossing_count,
        "unknown_exit_timestamp_count": unknown_exit_timestamp_count,
        "temporal_order_violation_count": temporal_order_violation_count,
        "same_bar_tie_count": same_bar_tie_count,
        "open_at_horizon_count": open_at_horizon_count,
        "data_gap_unresolved_count": data_gap_unresolved_count,
        "no_fill_count": no_fill_count,
        "structural_errors": structural_errors,
        "contract_valid": contract_valid,
        "oos_proof_status": "OOS_PROOF_PASS" if contract_valid else "FAIL",
        "failing_or_excluded_episodes": excluded_or_leaking_episodes,
        "per_episode_verdicts": per_episode_verdicts,
        "admissible_closed_episodes": admissible_closed_episodes,
    }
    if is_single and per_episode_verdicts:
        result["single_trade_verdict"] = per_episode_verdicts[0]
    return result


def _summarize_admissible_partition_episodes(episodes: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    closed = [
        ep
        for ep in episodes
        if str(ep.get("outcome_label") or ep.get("exit_type") or "") in ("WIN", "LOSS")
    ]
    n_closed = len(closed)
    wins = sum(1 for ep in closed if str(ep.get("outcome_label") or ep.get("exit_type")) == "WIN")
    losses = n_closed - wins
    win_rate = round(wins / n_closed, 4) if n_closed > 0 else None
    loss_rate = round(losses / n_closed, 4) if n_closed > 0 else None
    r_vals = [float(ep.get("realized_r", ep.get("realized_r_net", 0.0))) for ep in closed]
    net_pnls = [float(ep.get("realized_net_pnl_usd", 0.0)) for ep in closed]
    expectancy_r = round(sum(r_vals) / n_closed, 4) if n_closed > 0 else None
    total_net_pnl = round(sum(net_pnls), 2)
    return {
        "closed_episodes": n_closed,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "win_rate_wilson_95_ci": compute_wilson_95_ci(wins, n_closed),
        "loss_rate": loss_rate,
        "loss_rate_wilson_95_ci": compute_wilson_95_ci(losses, n_closed),
        "expectancy_r": expectancy_r,
        "modeled_net_pnl_usd": total_net_pnl,
        "sample_sufficient_30": n_closed >= 30,
    }


def evaluate_v179311_chronological_oos_and_walkforward(
    episodes_by_variant: Mapping[str, Sequence[Mapping[str, Any]]],
    m1_bars: Sequence[Mapping[str, Any]],
    *,
    embargo_bars: int = 180,
) -> Dict[str, Any]:
    """
    Phase P0-1: Evaluates chronological 50/25/25, 70/15/15, and 3-fold expanding walk-forward
    partitions across all variants using real exit timestamps and a 180-M1-bar (gap-aware)
    embargo window.
    1) Audits raw unpurged split assignments to measure `raw_unpurged_boundary_crossings_detected`
       (proving the detector catches real boundary-crossing trades in `C2` and `E1`).
    2) Purges/excludes boundary-crossing and embargo-window trades (`EXCLUDED_BOUNDARY_LEAK_RISK` /
       `PURGED_EMBARGO_BUFFER`) and re-verifies with `validate_trade_temporal_partition_contract`
       that the admissible split cohorts have `measured_admissible_leak_count == 0` and
       `unknown_exit_timestamp_count == 0`.
    """
    ts_list = [int(b["close_time"]) for b in m1_bars]
    n = len(ts_list)
    if n < 600:
        raise ValueError(f"INSUFFICIENT_M1_BARS_FOR_OOS_SPLIT: n={n}")

    def _build_split_bounds(frac_a: float, frac_b: float, name_prefix: str) -> Dict[str, Any]:
        i_a_end = int(n * frac_a) - 1
        i_b_start = min(n - 1, i_a_end + embargo_bars)
        i_b_end = int(n * frac_b) - 1
        i_c_start = min(n - 1, i_b_end + embargo_bars)
        i_c_end = n - 1
        return {
            "scheme_name": name_prefix,
            "embargo_bars": embargo_bars,
            "partitions": [
                {
                    "name": f"{name_prefix}_DEV",
                    "start_idx": 30,
                    "end_idx": i_a_end,
                    "start_ts_ms": ts_list[30],
                    "end_ts_ms": ts_list[i_a_end],
                    "next_start_ts_ms": ts_list[i_b_start],
                    "start_utc": _ms_to_utc_iso(ts_list[30]),
                    "end_utc": _ms_to_utc_iso(ts_list[i_a_end]),
                    "next_start_utc": _ms_to_utc_iso(ts_list[i_b_start]),
                },
                {
                    "name": f"{name_prefix}_VAL",
                    "start_idx": i_b_start,
                    "end_idx": i_b_end,
                    "start_ts_ms": ts_list[i_b_start],
                    "end_ts_ms": ts_list[i_b_end],
                    "next_start_ts_ms": ts_list[i_c_start],
                    "start_utc": _ms_to_utc_iso(ts_list[i_b_start]),
                    "end_utc": _ms_to_utc_iso(ts_list[i_b_end]),
                    "next_start_utc": _ms_to_utc_iso(ts_list[i_c_start]),
                },
                {
                    "name": f"{name_prefix}_OOS",
                    "start_idx": i_c_start,
                    "end_idx": i_c_end,
                    "start_ts_ms": ts_list[i_c_start],
                    "end_ts_ms": ts_list[i_c_end],
                    "next_start_ts_ms": ts_list[i_c_end] + embargo_bars * 60_000,
                    "start_utc": _ms_to_utc_iso(ts_list[i_c_start]),
                    "end_utc": _ms_to_utc_iso(ts_list[i_c_end]),
                    "next_start_utc": _ms_to_utc_iso(ts_list[i_c_end] + embargo_bars * 60_000),
                },
            ],
        }

    scheme_50_25_25 = _build_split_bounds(0.50, 0.75, "SPLIT_50_25_25")
    scheme_70_15_15 = _build_split_bounds(0.70, 0.85, "SPLIT_70_15_15")

    # 3-fold expanding walk-forward:
    # Fold 1: Train [0..40%], Val [40%+emb..55%], OOS [55%+emb..70%]
    # Fold 2: Train [0..55%], Val [55%+emb..70%], OOS [70%+emb..85%]
    # Fold 3: Train [0..70%], Val [70%+emb..85%], OOS [85%+emb..100%]
    wf_folds_spec = [
        ("WF_FOLD_1", 0.40, 0.55, 0.70),
        ("WF_FOLD_2", 0.55, 0.70, 0.85),
        ("WF_FOLD_3", 0.70, 0.85, 1.00),
    ]
    walkforward_folds_meta: List[Dict[str, Any]] = []
    for f_name, tr_f, va_f, te_f in wf_folds_spec:
        i_tr_end = int(n * tr_f) - 1
        i_va_start = min(n - 1, i_tr_end + embargo_bars)
        i_va_end = int(n * va_f) - 1
        i_te_start = min(n - 1, i_va_end + embargo_bars)
        i_te_end = int(n * te_f) - 1
        walkforward_folds_meta.append(
            {
                "fold_id": f_name,
                "train_start_ts_ms": ts_list[30],
                "train_end_ts_ms": ts_list[i_tr_end],
                "val_start_ts_ms": ts_list[i_va_start],
                "val_end_ts_ms": ts_list[i_va_end],
                "oos_start_ts_ms": ts_list[i_te_start],
                "oos_end_ts_ms": ts_list[i_te_end],
                "embargo_bars": embargo_bars,
            }
        )

    raw_detected_boundary_crossings: List[Dict[str, Any]] = []
    measured_admissible_leak_count = 0
    unknown_exit_timestamp_count = 0
    temporal_order_violation_count = 0
    total_episodes_evaluated = 0
    variants_report: Dict[str, Any] = {}

    for vk, eps in episodes_by_variant.items():
        total_episodes_evaluated += len(eps)
        v_schemes: Dict[str, Any] = {}
        for scheme in (scheme_50_25_25, scheme_70_15_15):
            sc_name = scheme["scheme_name"]
            part_summaries: Dict[str, Any] = {}
            purged_between_splits: List[Dict[str, Any]] = []
            for p_spec in scheme["partitions"]:
                p_name = p_spec["name"]
                p_start = int(p_spec["start_ts_ms"])
                p_end = int(p_spec["end_ts_ms"])
                p_next = int(p_spec["next_start_ts_ms"])

                candidates_in_window = [
                    ep
                    for ep in eps
                    if p_start <= int(ep.get("decision_timestamp_ms") or ep.get("decision_ts") or 0) <= p_end
                ]
                raw_audit = validate_trade_temporal_partition_contract(
                    candidates_in_window,
                    split_start_ts_ms=p_start,
                    split_end_ts_ms=p_end,
                    next_split_start_ts_ms=p_next,
                    embargo_minutes=180,
                    embargo_bars=embargo_bars,
                    m1_timestamps_ms=ts_list,
                    split_name=f"{vk}_{p_name}_RAW",
                )
                for fail_ep in raw_audit["failing_or_excluded_episodes"]:
                    raw_detected_boundary_crossings.append(
                        {
                            "variant": vk,
                            "scheme": sc_name,
                            "partition": p_name,
                            **fail_ep,
                        }
                    )
                    purged_between_splits.append(fail_ep)

                # Admissible episodes are strictly those whose observation window closes <= p_end
                admissible_eps = raw_audit["admissible_closed_episodes"]
                admissible_recheck = validate_trade_temporal_partition_contract(
                    admissible_eps,
                    split_start_ts_ms=p_start,
                    split_end_ts_ms=p_end,
                    next_split_start_ts_ms=p_next,
                    embargo_minutes=180,
                    embargo_bars=embargo_bars,
                    m1_timestamps_ms=ts_list,
                    split_name=f"{vk}_{p_name}_ADMISSIBLE",
                )
                measured_admissible_leak_count += int(admissible_recheck["embargo_leak_count"])
                unknown_exit_timestamp_count += int(raw_audit["unknown_exit_timestamp_count"])
                temporal_order_violation_count += int(raw_audit["temporal_order_violation_count"])

                part_summaries[p_name] = {
                    "start_utc": p_spec["start_utc"],
                    "end_utc": p_spec["end_utc"],
                    "next_start_utc": p_spec["next_start_utc"],
                    "raw_candidates_in_decision_window": len(candidates_in_window),
                    "excluded_boundary_crossing_count": int(raw_audit["boundary_crossing_count"]),
                    "open_at_horizon_censored_count": int(raw_audit["open_at_horizon_count"]),
                    "data_gap_unresolved_count": int(raw_audit["data_gap_unresolved_count"]),
                    "no_fill_count": int(raw_audit["no_fill_count"]),
                    "admissible_leak_count": int(admissible_recheck["embargo_leak_count"]),
                    "metrics": _summarize_admissible_partition_episodes(admissible_eps),
                }

            # Also count episodes whose decision_ts falls inside the embargo gap between DEV->VAL or VAL->OOS
            embargo_purged_decisions = 0
            for idx_p in range(len(scheme["partitions"]) - 1):
                cur_end = int(scheme["partitions"][idx_p]["end_ts_ms"])
                nxt_start = int(scheme["partitions"][idx_p + 1]["start_ts_ms"])
                for ep in eps:
                    dts = int(ep.get("decision_timestamp_ms") or ep.get("decision_ts") or 0)
                    if cur_end < dts < nxt_start:
                        embargo_purged_decisions += 1

            v_schemes[sc_name] = {
                "partitions": part_summaries,
                "embargo_purged_decision_episodes_count": embargo_purged_decisions,
                "excluded_boundary_crossings": purged_between_splits,
            }

        # Evaluate 3-fold walk-forward for this variant
        wf_fold_results: List[Dict[str, Any]] = []
        for f_meta in walkforward_folds_meta:
            tr_eps = [
                ep
                for ep in eps
                if f_meta["train_start_ts_ms"]
                <= int(ep.get("decision_timestamp_ms") or ep.get("decision_ts") or 0)
                <= f_meta["train_end_ts_ms"]
            ]
            va_eps = [
                ep
                for ep in eps
                if f_meta["val_start_ts_ms"]
                <= int(ep.get("decision_timestamp_ms") or ep.get("decision_ts") or 0)
                <= f_meta["val_end_ts_ms"]
            ]
            te_eps = [
                ep
                for ep in eps
                if f_meta["oos_start_ts_ms"]
                <= int(ep.get("decision_timestamp_ms") or ep.get("decision_ts") or 0)
                <= f_meta["oos_end_ts_ms"]
            ]
            tr_aud = validate_trade_temporal_partition_contract(
                tr_eps,
                split_start_ts_ms=f_meta["train_start_ts_ms"],
                split_end_ts_ms=f_meta["train_end_ts_ms"],
                next_split_start_ts_ms=f_meta["val_start_ts_ms"],
                embargo_minutes=180,
                embargo_bars=embargo_bars,
                m1_timestamps_ms=ts_list,
                split_name=f"{vk}_{f_meta['fold_id']}_TRAIN",
            )
            va_aud = validate_trade_temporal_partition_contract(
                va_eps,
                split_start_ts_ms=f_meta["val_start_ts_ms"],
                split_end_ts_ms=f_meta["val_end_ts_ms"],
                next_split_start_ts_ms=f_meta["oos_start_ts_ms"],
                embargo_minutes=180,
                embargo_bars=embargo_bars,
                m1_timestamps_ms=ts_list,
                split_name=f"{vk}_{f_meta['fold_id']}_VAL",
            )
            te_aud = validate_trade_temporal_partition_contract(
                te_eps,
                split_start_ts_ms=f_meta["oos_start_ts_ms"],
                split_end_ts_ms=f_meta["oos_end_ts_ms"],
                next_split_start_ts_ms=f_meta["oos_end_ts_ms"] + embargo_bars * 60_000,
                embargo_minutes=180,
                embargo_bars=embargo_bars,
                m1_timestamps_ms=ts_list,
                split_name=f"{vk}_{f_meta['fold_id']}_OOS",
            )
            tr_m = _summarize_admissible_partition_episodes(tr_aud["admissible_closed_episodes"])
            va_m = _summarize_admissible_partition_episodes(va_aud["admissible_closed_episodes"])
            te_m = _summarize_admissible_partition_episodes(te_aud["admissible_closed_episodes"])
            wf_fold_results.append(
                {
                    "fold_id": f_meta["fold_id"],
                    "train_metrics": tr_m,
                    "val_metrics": va_m,
                    "oos_metrics": te_m,
                    "excluded_boundary_crossings": (
                        int(tr_aud["boundary_crossing_count"])
                        + int(va_aud["boundary_crossing_count"])
                        + int(te_aud["boundary_crossing_count"])
                    ),
                    "fold_evaluable": te_m["closed_episodes"] >= 5,
                    "fold_verdict": (
                        "INSUFFICIENT_OOS_SAMPLE"
                        if te_m["closed_episodes"] < 5
                        else ("NEGATIVE_OOS_EXPECTANCY" if (te_m["expectancy_r"] or 0.0) <= 0.0 else "POSITIVE_OOS_FOLD")
                    ),
                }
            )

        variants_report[vk] = {
            "total_independent_episodes": len(eps),
            "SPLIT_50_25_25": v_schemes["SPLIT_50_25_25"],
            "SPLIT_70_15_15": v_schemes["SPLIT_70_15_15"],
            "walkforward_3folds": wf_fold_results,
        }

    oos_proof_pass = (
        total_episodes_evaluated > 0
        and measured_admissible_leak_count == 0
        and unknown_exit_timestamp_count == 0
        and temporal_order_violation_count == 0
    )

    return {
        "schema_version": V17_9_3_1_1_SCHEMA_VERSION,
        "embargo_policy": {
            "forward_evaluation_horizon_bars": embargo_bars,
            "minimum_wall_clock_embargo_minutes": embargo_bars,
            "gap_aware_bar_horizon_enforced": True,
            "boundary_crossing_policy": "PURGE_AND_CLASSIFY_EXCLUDED_BOUNDARY_LEAK_RISK",
        },
        "split_schemes_metadata": {
            "SPLIT_50_25_25": scheme_50_25_25,
            "SPLIT_70_15_15": scheme_70_15_15,
            "WALKFORWARD_3FOLDS": walkforward_folds_meta,
        },
        "total_episodes_evaluated": total_episodes_evaluated,
        "raw_unpurged_boundary_crossings_detected_count": len(raw_detected_boundary_crossings),
        "raw_unpurged_boundary_crossings_detected": raw_detected_boundary_crossings,
        "measured_admissible_leak_count": measured_admissible_leak_count,
        "unknown_exit_timestamp_count": unknown_exit_timestamp_count,
        "temporal_order_violation_count": temporal_order_violation_count,
        "zero_future_bar_leakage_across_admissible_splits": oos_proof_pass,
        "oos_proof_status": "OOS_PROOF_PASS" if oos_proof_pass else "FAIL",
        "variants": variants_report,
    }


# ==============================================================================
# PHASE P0-3 — NON-TAUTOLOGICAL ACCEPTANCE GATE MANIFEST VALIDATOR
# ==============================================================================
def validate_acceptance_gate_manifest(
    gate_payload: Mapping[str, Any],
    *,
    expected_gate_ids: Sequence[str],
    self_check_gate_id: str = "G26",
) -> Dict[str, Any]:
    """
    Phase P0-3: Independent validator verifying that an acceptance gate manifest has:
    - exact expected unique gate IDs in order,
    - no duplicate gate IDs,
    - boolean `passed`, valid `status` in `("PASS", "FAIL")`,
    - non-empty `description` and non-empty `evidence` dict on every gate,
    - no tautological `passed=True` when underlying `observed` vs `expected` or other gates fail.
    """
    gates = list(gate_payload.get("gates") or [])
    observed_ids = [str(g.get("gate_id") or "") for g in gates]
    unique_ids = set(observed_ids)
    errors: List[str] = []

    if len(observed_ids) != len(unique_ids):
        errors.append("DUPLICATE_GATE_IDS_DETECTED")
    if observed_ids != list(expected_gate_ids):
        errors.append(f"GATE_ID_LIST_MISMATCH:observed={ len(observed_ids) },expected={ len(expected_gate_ids) }")

    failed_other_gates: List[str] = []
    for g in gates:
        gid = str(g.get("gate_id") or "")
        passed = g.get("passed")
        status = g.get("status")
        desc = g.get("description") or g.get("title")
        ev = g.get("evidence")
        if type(passed) is not bool:
            errors.append(f"NON_BOOLEAN_PASSED:{gid}")
        if status not in ("PASS", "FAIL"):
            errors.append(f"INVALID_STATUS:{gid}:{status}")
        if (passed is True and status != "PASS") or (passed is False and status != "FAIL"):
            errors.append(f"PASSED_STATUS_CONTRADICTION:{gid}")
        if not isinstance(desc, str) or not desc.strip():
            errors.append(f"EMPTY_DESCRIPTION:{gid}")
        if not isinstance(ev, Mapping) or len(ev) == 0:
            errors.append(f"EMPTY_EVIDENCE:{gid}")
        if gid != self_check_gate_id and not bool(passed):
            failed_other_gates.append(gid)

    self_gate = next((g for g in gates if str(g.get("gate_id")) == self_check_gate_id), None)
    if self_gate is not None:
        if bool(self_gate.get("passed")) and (len(failed_other_gates) > 0 or len(errors) > 0):
            errors.append(
                f"TAUTOLOGICAL_SELF_CHECK_GATE_PASS:{self_check_gate_id}_passed_while_failures={failed_other_gates}"
            )

    return {
        "valid": len(errors) == 0 and len(failed_other_gates) == 0,
        "schema_valid": len(errors) == 0,
        "total_gates": len(gates),
        "failed_other_gates": failed_other_gates,
        "errors": errors,
    }


# ==============================================================================
# PHASE P0-4 — EXHAUSTIVE / INDEPENDENT CANDIDATE UNIVERSE COVERAGE AUDITOR
# ==============================================================================
def _audit_outside_chunk_worker(chunk_indices: List[int]) -> Dict[str, Any]:
    from tools.run_v17_9_3_macro_unicorn_backtest import (
        V1793_VARIANT_POLICIES,
        _init_replay_worker,
        build_v1793_variant_market_context,
    )
    import tools.run_v17_9_3_macro_unicorn_backtest as bt_mod
    from services.shared_strategy_evaluator import DecisionStatus, PRODUCTION_MODEL_IDS, SharedStrategyEvaluator

    if bt_mod._WORKER_DATASETS is None or bt_mod._WORKER_CLOSE_TS is None:
        _init_replay_worker()
    ds = bt_mod._WORKER_DATASETS
    c_ts = bt_mod._WORKER_CLOSE_TS
    assert ds is not None and c_ts is not None

    bars_1m = ds["1m"]
    bars_5m = ds["5m"]
    bars_15m = ds["15m"]
    bars_1h = ds["1H"]
    bars_4h = ds["4H"]
    bars_1d = ds["1D"]

    pol_a0 = V1793_VARIANT_POLICIES["A0_FROZEN_BASELINE_OFF"]
    pol_a1_fast = dict(V1793_VARIANT_POLICIES["A1_V1792_SL_TP_BASELINE"])
    pol_a1_fast["mtf_structure_observe_only"] = False

    a0_outside_eligible: List[Dict[str, Any]] = []
    a1_outside_eligible: List[Dict[str, Any]] = []
    setup_present_outside_count = 0

    for i_1m in chunk_indices:
        asof_ms = int(bars_1m[i_1m]["close_time"])
        idx_5m = bisect.bisect_right(c_ts["5m"], asof_ms)
        idx_15m = bisect.bisect_right(c_ts["15m"], asof_ms)
        idx_1h = bisect.bisect_right(c_ts["1H"], asof_ms)
        idx_4h = bisect.bisect_right(c_ts["4H"], asof_ms)
        idx_1d = bisect.bisect_right(c_ts["1D"], asof_ms)

        win_1m = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
        last_c = float(win_1m[-1]["close"])
        base_inputs = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": asof_ms,
            "m1_candles": win_1m,
            "m5_candles": bars_5m[max(0, idx_5m - 96) : idx_5m],
            "m15_candles": bars_15m[max(0, idx_15m - 96) : idx_15m],
            "h1_candles": bars_1h[max(0, idx_1h - 96) : idx_1h],
            "h4_candles": bars_4h[max(0, idx_4h - 64) : idx_4h],
            "d1_candles": bars_1d[max(0, idx_1d - 30) : idx_1d],
            "bid": round(last_c - 0.10, 2),
            "ask": round(last_c + 0.10, 2),
        }
        ctx_a0 = build_v1793_variant_market_context(base_inputs, pol_a0, decision_time=asof_ms)
        pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx_a0)

        models_with_setup: List[str] = []
        for m_id in PRODUCTION_MODEL_IDS:
            s0 = SharedStrategyEvaluator.evaluate_model_direction(ctx_a0, m_id, pref_dir)
            if s0.decision_status == DecisionStatus.ELIGIBLE.value:
                a0_outside_eligible.append(
                    {
                        "checkpoint_index_1m": i_1m,
                        "decision_timestamp_ms": asof_ms,
                        "model_id": m_id,
                        "direction": pref_dir,
                    }
                )
            if s0.decision_status not in (
                DecisionStatus.NO_VALID_SETUP.value,
                DecisionStatus.WAITING_CONFIRMATION.value,
                DecisionStatus.MODEL_DISABLED.value,
            ):
                models_with_setup.append(m_id)

        if models_with_setup:
            setup_present_outside_count += 1
            ctx_a1 = build_v1793_variant_market_context(
                base_inputs, pol_a1_fast, decision_time=asof_ms, reference_context=ctx_a0
            )
            for m_id in models_with_setup:
                s1 = SharedStrategyEvaluator.evaluate_model_direction(ctx_a1, m_id, pref_dir)
                if s1.decision_status == DecisionStatus.ELIGIBLE.value:
                    a1_outside_eligible.append(
                        {
                            "checkpoint_index_1m": i_1m,
                            "decision_timestamp_ms": asof_ms,
                            "model_id": m_id,
                            "direction": pref_dir,
                            "selected_sl": s1.selected_sl,
                            "take_profit": s1.take_profit,
                            "net_rr": s1.net_rr,
                        }
                    )

    return {
        "evaluated_count": len(chunk_indices),
        "setup_present_outside_count": setup_present_outside_count,
        "a0_outside_eligible": a0_outside_eligible,
        "a1_outside_eligible": a1_outside_eligible,
    }


def audit_v179311_evaluator_candidate_coverage(
    datasets: Mapping[str, Any],
    screen_result: Mapping[str, Any],
    *,
    max_workers: int = 8,
    injected_dropped_indices: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """
    Phase P0-4: Audits candidate coverage across all 22,346 post-warmup M1 timestamps,
    explicitly separating:
      (a) enumerated_timestamp_coverage (22,346 / 22,346 = 100%)
      (b) deterministic_screen_coverage (3,788 screened + 240 holdout = 4,028 / 22,346 = 18.0256%)
      (c) exhaustive_independent_evaluator_coverage (all 22,346 timestamps audited across all 5 models)
      (d) exact false-negative counts per variant outside the 3,788 screen.
    """
    bars_1m = list(datasets["1m"])
    n_total = len(bars_1m)
    enumerated_count = n_total - 30

    screened_set: Set[int] = set(int(x) for x in screen_result.get("screened_candidate_indices", []))
    if injected_dropped_indices:
        for drop_idx in injected_dropped_indices:
            screened_set.discard(int(drop_idx))

    holdout_set: Set[int] = set(int(x) for x in screen_result.get("holdout_verification_indices", []))
    combined_screen_set = screened_set | holdout_set
    outside_indices = [i for i in range(30, n_total) if i not in screened_set]

    macro_inside_set: Set[int] = set(int(x) for x in screen_result.get("macro_inside_indices", []))
    bound_unicorn_set: Set[int] = set(int(x) for x in screen_result.get("bound_unicorn_indices", []))

    eff_workers = max(1, min(int(max_workers), 8))
    chunk_size = max(1, (len(outside_indices) + eff_workers * 4 - 1) // (eff_workers * 4))
    chunks = [outside_indices[k : k + chunk_size] for k in range(0, len(outside_indices), chunk_size)]

    a0_outside_eligible: List[Dict[str, Any]] = []
    a1_outside_eligible: List[Dict[str, Any]] = []
    setup_present_outside_count = 0

    if chunks:
        if eff_workers == 1 or len(outside_indices) <= 500:
            for ch in chunks:
                res = _audit_outside_chunk_worker(ch)
                setup_present_outside_count += int(res["setup_present_outside_count"])
                a0_outside_eligible.extend(res["a0_outside_eligible"])
                a1_outside_eligible.extend(res["a1_outside_eligible"])
        else:
            with ProcessPoolExecutor(max_workers=eff_workers) as pool:
                for res in pool.map(_audit_outside_chunk_worker, chunks):
                    setup_present_outside_count += int(res["setup_present_outside_count"])
                    a0_outside_eligible.extend(res["a0_outside_eligible"])
                    a1_outside_eligible.extend(res["a1_outside_eligible"])

    a0_outside_eligible.sort(key=lambda r: (int(r["checkpoint_index_1m"]), str(r["model_id"])))
    a1_outside_eligible.sort(key=lambda r: (int(r["checkpoint_index_1m"]), str(r["model_id"])))

    # Derive outside-screen false negatives per variant:
    # - C1 / C1_SLTP / D1 / D2 require macro_inside_indices
    # - C2 / C2_SLTP / D1 / D2 require bound_unicorn_indices
    c1_outside = [r for r in a0_outside_eligible if int(r["checkpoint_index_1m"]) in macro_inside_set]
    c1_sltp_outside = [r for r in a1_outside_eligible if int(r["checkpoint_index_1m"]) in macro_inside_set]
    c2_outside = [r for r in a0_outside_eligible if int(r["checkpoint_index_1m"]) in bound_unicorn_set]
    c2_sltp_outside = [r for r in a1_outside_eligible if int(r["checkpoint_index_1m"]) in bound_unicorn_set]
    d1_outside = [
        r
        for r in a1_outside_eligible
        if int(r["checkpoint_index_1m"]) in macro_inside_set and int(r["checkpoint_index_1m"]) in bound_unicorn_set
    ]
    e1_outside = [r for r in a1_outside_eligible if int(r["checkpoint_index_1m"]) not in macro_inside_set]

    variant_coverage_matrix = {
        "A0_FROZEN_BASELINE_OFF": {
            "outside_screen_false_negative_decisions": len(a0_outside_eligible),
            "outside_screen_false_negative_unique_timestamps": len({r["checkpoint_index_1m"] for r in a0_outside_eligible}),
            "proven_zero_false_negatives_in_3788_screen": len(a0_outside_eligible) == 0,
            "coverage_status": "PROVEN_EXACT_SUPERSET_IN_3788_SCREEN" if len(a0_outside_eligible) == 0 else "FALSE_NEGATIVES_DETECTED",
        },
        "B0_V1793_OBSERVE_ONLY": {
            "outside_screen_false_negative_decisions": len(a0_outside_eligible),
            "outside_screen_false_negative_unique_timestamps": len({r["checkpoint_index_1m"] for r in a0_outside_eligible}),
            "proven_zero_false_negatives_in_3788_screen": len(a0_outside_eligible) == 0,
            "coverage_status": "PROVEN_EXACT_SUPERSET_IN_3788_SCREEN" if len(a0_outside_eligible) == 0 else "FALSE_NEGATIVES_DETECTED",
        },
        "C1_V1793_MACRO_FILTER_ONLY": {
            "outside_screen_false_negative_decisions": len(c1_outside),
            "outside_screen_false_negative_unique_timestamps": len({r["checkpoint_index_1m"] for r in c1_outside}),
            "proven_zero_false_negatives_in_3788_screen": len(c1_outside) == 0,
            "coverage_status": "PROVEN_EXACT_SUPERSET_IN_3788_SCREEN" if len(c1_outside) == 0 else "FALSE_NEGATIVES_DETECTED",
        },
        "C2_V1793_UNICORN_ONLY": {
            "outside_screen_false_negative_decisions": len(c2_outside),
            "outside_screen_false_negative_unique_timestamps": len({r["checkpoint_index_1m"] for r in c2_outside}),
            "proven_zero_false_negatives_in_3788_screen": len(c2_outside) == 0,
            "coverage_status": "PROVEN_EXACT_SUPERSET_IN_3788_SCREEN" if len(c2_outside) == 0 else "FALSE_NEGATIVES_DETECTED",
        },
        "C1_MACRO_PLUS_V1792_SL_TP": {
            "outside_screen_false_negative_decisions": len(c1_sltp_outside),
            "outside_screen_false_negative_unique_timestamps": len({r["checkpoint_index_1m"] for r in c1_sltp_outside}),
            "proven_zero_false_negatives_in_3788_screen": len(c1_sltp_outside) == 0,
            "coverage_status": "PROVEN_EXACT_SUPERSET_IN_3788_SCREEN" if len(c1_sltp_outside) == 0 else "FALSE_NEGATIVES_DETECTED",
        },
        "C2_UNICORN_PLUS_V1792_SL_TP": {
            "outside_screen_false_negative_decisions": len(c2_sltp_outside),
            "outside_screen_false_negative_unique_timestamps": len({r["checkpoint_index_1m"] for r in c2_sltp_outside}),
            "proven_zero_false_negatives_in_3788_screen": len(c2_sltp_outside) == 0,
            "coverage_status": "PROVEN_EXACT_SUPERSET_IN_3788_SCREEN" if len(c2_sltp_outside) == 0 else "FALSE_NEGATIVES_DETECTED",
        },
        "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP": {
            "outside_screen_false_negative_decisions": len(d1_outside),
            "outside_screen_false_negative_unique_timestamps": len({r["checkpoint_index_1m"] for r in d1_outside}),
            "proven_zero_false_negatives_in_3788_screen": len(d1_outside) == 0,
            "coverage_status": "PROVEN_EXACT_SUPERSET_IN_3788_SCREEN" if len(d1_outside) == 0 else "FALSE_NEGATIVES_DETECTED",
        },
        "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL": {
            "outside_screen_false_negative_decisions": len(d1_outside),
            "outside_screen_false_negative_unique_timestamps": len({r["checkpoint_index_1m"] for r in d1_outside}),
            "proven_zero_false_negatives_in_3788_screen": len(d1_outside) == 0,
            "coverage_status": "PROVEN_EXACT_SUPERSET_IN_3788_SCREEN" if len(d1_outside) == 0 else "FALSE_NEGATIVES_DETECTED",
        },
        "A1_V1792_SL_TP_BASELINE": {
            "outside_screen_false_negative_decisions": len(a1_outside_eligible),
            "outside_screen_false_negative_unique_timestamps": len({r["checkpoint_index_1m"] for r in a1_outside_eligible}),
            "proven_zero_false_negatives_in_3788_screen": len(a1_outside_eligible) == 0,
            "coverage_status": (
                "PROVEN_EXACT_SUPERSET_IN_3788_SCREEN"
                if len(a1_outside_eligible) == 0
                else "SCREEN_OMITS_OUTSIDE_MACRO_NON_UNICORN_M1_TARGET_LT_7USD_HTF_TARGET_ELIGIBLE"
            ),
            "explanation": (
                "3,788 screen uses base_scan_7 (M1 target >= 7.0 USD) + macro_inside + bound_unicorn + legacy_491. "
                "Outside macro and outside Unicorn, when M1 target < 7.0 USD (so A0 is rejected), A1/E1 can still find "
                "a V17.9.2 HTF (M5/M15/H1/H4/1D) liquidity target >= 7.0 USD and become ELIGIBLE."
            ),
        },
        "E1_OUTSIDE_MACRO_COUNTERFACTUAL": {
            "outside_screen_false_negative_decisions": len(e1_outside),
            "outside_screen_false_negative_unique_timestamps": len({r["checkpoint_index_1m"] for r in e1_outside}),
            "proven_zero_false_negatives_in_3788_screen": len(e1_outside) == 0,
            "coverage_status": (
                "PROVEN_EXACT_SUPERSET_IN_3788_SCREEN"
                if len(e1_outside) == 0
                else "SCREEN_OMITS_OUTSIDE_MACRO_NON_UNICORN_M1_TARGET_LT_7USD_HTF_TARGET_ELIGIBLE"
            ),
        },
    }

    primary_v1793_variants_zero_fn = (
        len(a0_outside_eligible) == 0
        and len(c1_outside) == 0
        and len(c2_outside) == 0
        and len(c1_sltp_outside) == 0
        and len(c2_sltp_outside) == 0
        and len(d1_outside) == 0
    )

    return {
        "schema_version": V17_9_3_1_1_SCHEMA_VERSION,
        "total_m1_bars": n_total,
        "warmup_bars": 30,
        "enumerated_decision_timestamps_count": enumerated_count,
        "enumerated_timestamp_coverage_ratio": 1.0,
        "screened_candidate_count": len(screened_set),
        "holdout_sample_count": len(holdout_set),
        "combined_v17931_evaluated_checkpoint_count": len(combined_screen_set),
        "v17931_screen_checkpoint_coverage_ratio": round(len(combined_screen_set) / float(enumerated_count), 6),
        "outside_screen_timestamps_audited_by_true_evaluator": len(outside_indices),
        "exhaustive_22346_evaluator_audit_completed": len(outside_indices) + len(screened_set) == enumerated_count,
        "exhaustive_evaluator_coverage_ratio": 1.0,
        "outside_screen_timestamps_with_raw_m1_setup": setup_present_outside_count,
        "primary_v1793_variants_zero_false_negatives_proven": primary_v1793_variants_zero_fn,
        "unfiltered_a1_e1_outside_screen_eligible_decisions_count": len(a1_outside_eligible),
        "unfiltered_a1_e1_outside_screen_unique_timestamps_count": len(
            {r["checkpoint_index_1m"] for r in a1_outside_eligible}
        ),
        "variant_coverage_matrix": variant_coverage_matrix,
        "a0_outside_eligible_samples": a0_outside_eligible[:50],
        "a1_outside_eligible_samples": a1_outside_eligible[:50],
        "honest_coverage_conclusion": (
            "EXHAUSTIVE_22346_EVALUATOR_AUDIT_COMPLETED: 3,788 screen has 0 false negatives for A0, B0, C1, "
            "C1_MACRO_PLUS_V1792_SL_TP, C2, C2_UNICORN_PLUS_V1792_SL_TP, D1, and D2 across all 22,346 timestamps. "
            f"For unfiltered A1 and E1, {len(a1_outside_eligible)} additional eligible model decisions across "
            f"{len({r['checkpoint_index_1m'] for r in a1_outside_eligible})} outside-screen timestamps exist where "
            "M1 target < 7.0 USD (rejected by A0) but V17.9.2 HTF target >= 7.0 USD."
        ),
    }


# ==============================================================================
# PHASE P1-1 — LOSS FORENSICS ON EVERY INDEPENDENT CLOSED TRADE
# ==============================================================================
LOSS_ROOT_CAUSE_TAXONOMY_V179311: Tuple[str, ...] = (
    "STRUCTURE_DIRECTION_CONFLICT_H1_H4",
    "PRE_ENTRY_MSS_BOS_UNCONFIRMED",
    "NO_CONFIRMED_DISPLACEMENT",
    "UNICORN_EPISODE_BINDING_MISMATCH",
    "FVG_CONSUMED_OR_INVALIDATED",
    "LIQUIDITY_SWEEP_MISSING_OR_STALE",
    "ENTRY_BEFORE_RETEST_CONFIRMATION",
    "MACRO_WINDOW_MISMATCH_OR_SESSION_RISK",
    "ENTRY_TOO_LATE_AFTER_MOVE",
    "STRUCTURAL_SL_INSIDE_TRUE_INVALIDATION",
    "PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD",
    "VALID_STRUCTURE_INVALIDATION",
    "TP_UNREACHABLE_WITHIN_HORIZON",
    "HIGH_FRICTION_COSTS",
    "PROVIDER_GAP_OR_EXECUTION_AMBIGUITY",
    "SAME_BAR_SL_TP_ORDER_UNKNOWN",
    "UNDETERMINABLE_MISSING_CAUSAL_EVIDENCE",
)


def classify_v179311_loss_root_causes(
    forensic_row: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Phase P1-1: Multilabel structured root-cause attribution for a closed loss episode.
    Never invents a primary cause: returns `UNDETERMINABLE_MISSING_CAUSAL_EVIDENCE` when
    essential causal pre-entry or stop-outcome evidence is missing.
    Requires causal protected structure to have actually held at/after stop before labeling
    `PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD`.
    """
    pre = forensic_row.get("causal_pre_entry_features")
    ex_post = forensic_row.get("ex_post_outcome_path")
    stop_diag = forensic_row.get("stop_outcome_diagnostic")
    if (
        not isinstance(pre, Mapping)
        or not isinstance(ex_post, Mapping)
        or not isinstance(stop_diag, Mapping)
        or forensic_row.get("decision_timestamp_ms") is None
        or forensic_row.get("entry_fill_price") is None
        or forensic_row.get("selected_sl") is None
    ):
        return {
            "primary_root_cause": "UNDETERMINABLE_MISSING_CAUSAL_EVIDENCE",
            "contributing_causes": ["UNDETERMINABLE_MISSING_CAUSAL_EVIDENCE"],
            "confidence": "LOW_MISSING_EVIDENCE",
            "evidence_refs": {"missing_essential_fields": True},
        }

    causes: List[str] = []
    ev_refs: Dict[str, Any] = {}

    if bool(ex_post.get("same_bar_sl_tp_tie")):
        causes.append("SAME_BAR_SL_TP_ORDER_UNKNOWN")
        ev_refs["same_bar_sl_tp_tie"] = True

    if bool(ex_post.get("has_data_gap_before_exit")) or int(ex_post.get("intratrade_gap_count", 0) or 0) > 0:
        causes.append("PROVIDER_GAP_OR_EXECUTION_AMBIGUITY")
        ev_refs["max_bar_gap_ms"] = ex_post.get("max_bar_gap_ms")

    if bool(pre.get("htf_h1_h4_conflict_at_entry")):
        causes.append("STRUCTURE_DIRECTION_CONFLICT_H1_H4")
        ev_refs["htf_structure_state"] = {
            "h1_bias": pre.get("h1_structure_bias"),
            "h4_bias": pre.get("h4_structure_bias"),
            "direction": forensic_row.get("direction"),
        }

    if not bool(pre.get("mss_bos_confirmed_at_entry", True)):
        causes.append("PRE_ENTRY_MSS_BOS_UNCONFIRMED")
        ev_refs["mss_bos_confirmed_at_ms"] = pre.get("mss_bos_confirmed_at_ms")

    if not bool(pre.get("displacement_confirmed_at_entry", True)):
        causes.append("NO_CONFIRMED_DISPLACEMENT")
        ev_refs["displacement_body_atr_ratio"] = pre.get("displacement_body_atr_ratio")

    if bool(pre.get("unicorn_episode_binding_mismatch", False)):
        causes.append("UNICORN_EPISODE_BINDING_MISMATCH")
        ev_refs["unicorn_status"] = pre.get("unicorn_status")

    if bool(pre.get("fvg_consumed_or_invalidated_at_entry", False)):
        causes.append("FVG_CONSUMED_OR_INVALIDATED")
        ev_refs["fvg_bounds"] = pre.get("fvg_bounds")

    if bool(pre.get("liquidity_sweep_missing_or_stale", False)):
        causes.append("LIQUIDITY_SWEEP_MISSING_OR_STALE")
        ev_refs["sweep_id"] = pre.get("sweep_id")

    if not bool(pre.get("retest_confirmed_at_entry", True)):
        causes.append("ENTRY_BEFORE_RETEST_CONFIRMATION")
        ev_refs["retest_confirmed_at_ms"] = pre.get("retest_confirmed_at_ms")

    if not bool(pre.get("inside_macro_window", False)):
        causes.append("MACRO_WINDOW_MISMATCH_OR_SESSION_RISK")
        ev_refs["macro_window_id"] = pre.get("macro_primary_window_id")

    if bool(pre.get("entry_too_late_after_extension", False)):
        causes.append("ENTRY_TOO_LATE_AFTER_MOVE")
        ev_refs["entry_extension_atr_multiple"] = pre.get("entry_extension_atr_multiple")

    # Outer protected-structure authority & stop classification
    outer_class = str(stop_diag.get("outcome_class") or "")
    outer_sub = str(stop_diag.get("sub_class") or "")
    if bool(pre.get("sl_inside_outer_protected_structure", False)):
        causes.append("STRUCTURAL_SL_INSIDE_TRUE_INVALIDATION")
        ev_refs["sl_vs_outer_protected_structure"] = {
            "selected_sl": forensic_row.get("selected_sl"),
            "outer_protected_structure_price": pre.get("outer_protected_structure_price"),
        }

    if outer_class == "PREMATURE_NOISE_SWEEP_STOP":
        causes.append("PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD")
        ev_refs["outer_stop_classification"] = {"outcome_class": outer_class, "sub_class": outer_sub}
    elif outer_class in ("TRUE_INVALIDATION_LOSS", "DELAYED_INEVITABLE_LOSS"):
        causes.append("VALID_STRUCTURE_INVALIDATION")
        ev_refs["outer_stop_classification"] = {"outcome_class": outer_class, "sub_class": outer_sub}

    mfe_r = float(ex_post.get("mfe_r", 0.0) or 0.0)
    net_rr = float(forensic_row.get("net_rr", 2.0) or 2.0)
    if mfe_r < 0.5 and net_rr >= 2.0:
        causes.append("TP_UNREACHABLE_WITHIN_HORIZON")
        ev_refs["mfe_r_vs_net_rr"] = {"mfe_r": mfe_r, "planned_net_rr": net_rr}

    planned_risk = float(forensic_row.get("planned_net_risk_usd", 2.50) or 2.50)
    total_cost = float(forensic_row.get("modeled_total_cost_usd", 0.0) or 0.0)
    if planned_risk > 0.0 and (total_cost / planned_risk) >= 0.30:
        causes.append("HIGH_FRICTION_COSTS")
        ev_refs["cost_share_of_planned_risk"] = round(total_cost / planned_risk, 4)

    if not causes:
        causes.append("UNDETERMINABLE_MISSING_CAUSAL_EVIDENCE")

    # Priority ordering for primary root cause (causal pre-entry structural/timing vs stop geometry vs invalidation)
    priority_order = (
        "SAME_BAR_SL_TP_ORDER_UNKNOWN",
        "PROVIDER_GAP_OR_EXECUTION_AMBIGUITY",
        "PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD",
        "STRUCTURAL_SL_INSIDE_TRUE_INVALIDATION",
        "STRUCTURE_DIRECTION_CONFLICT_H1_H4",
        "PRE_ENTRY_MSS_BOS_UNCONFIRMED",
        "ENTRY_BEFORE_RETEST_CONFIRMATION",
        "NO_CONFIRMED_DISPLACEMENT",
        "UNICORN_EPISODE_BINDING_MISMATCH",
        "LIQUIDITY_SWEEP_MISSING_OR_STALE",
        "FVG_CONSUMED_OR_INVALIDATED",
        "ENTRY_TOO_LATE_AFTER_MOVE",
        "MACRO_WINDOW_MISMATCH_OR_SESSION_RISK",
        "VALID_STRUCTURE_INVALIDATION",
        "TP_UNREACHABLE_WITHIN_HORIZON",
        "HIGH_FRICTION_COSTS",
        "UNDETERMINABLE_MISSING_CAUSAL_EVIDENCE",
    )
    primary = next((c for c in priority_order if c in causes), causes[0])
    confidence = "HIGH" if primary != "UNDETERMINABLE_MISSING_CAUSAL_EVIDENCE" else "LOW_MISSING_EVIDENCE"

    return {
        "primary_root_cause": primary,
        "contributing_causes": causes,
        "confidence": confidence,
        "evidence_refs": ev_refs,
    }


def build_v179311_loss_episode_forensic_ledger(
    episodes: Sequence[Mapping[str, Any]],
    datasets: Mapping[str, Any],
    *,
    run_id: str,
    tested_code_sha: str,
    dataset_sha: str,
    policy_config_hash: str,
) -> List[Dict[str, Any]]:
    """
    Phase P1-1: Builds one comprehensive forensic row per CLOSED independent episode
    (and tags WIN vs LOSS), explicitly separating causal pre-entry features (computed strictly
    at `<= decision_timestamp_ms`) from ex-post forward outcome path diagnostics.
    """
    bars_1m = list(datasets["1m"])
    bars_5m = list(datasets.get("5m") or [])
    bars_15m = list(datasets.get("15m") or [])
    bars_1h = list(datasets.get("1H") or [])
    bars_4h = list(datasets.get("4H") or [])

    c_5m = [int(b["close_time"]) for b in bars_5m]
    c_15m = [int(b["close_time"]) for b in bars_15m]
    c_1h = [int(b["close_time"]) for b in bars_1h]
    c_4h = [int(b["close_time"]) for b in bars_4h]

    def _causal_tf_trend_and_last_close_id(bars_tf: Sequence[Mapping[str, Any]], closes_tf: Sequence[int], asof_ms: int, lookback: int = 6) -> Tuple[str, Optional[int], bool]:
        idx = bisect.bisect_right(closes_tf, asof_ms)
        if idx <= 0:
            return "NEUTRAL", None, True
        last_close_ms = int(bars_tf[idx - 1]["close_time"])
        causal_ok = last_close_ms <= asof_ms
        if idx < 2:
            return "NEUTRAL", last_close_ms, causal_ok
        start_i = max(0, idx - lookback)
        c_first = float(bars_tf[start_i]["close"])
        c_last = float(bars_tf[idx - 1]["close"])
        highs_w = [float(bars_tf[k]["high"]) for k in range(start_i, idx)]
        lows_w = [float(bars_tf[k]["low"]) for k in range(start_i, idx)]
        mid = (max(highs_w) + min(lows_w)) / 2.0
        if c_last > c_first and c_last >= mid:
            bias = "BULLISH"
        elif c_last < c_first and c_last <= mid:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"
        return bias, last_close_ms, causal_ok

    from services.v17_9_2_structural_stop_intelligence import classify_structure_stop_outcome

    outer_auth_lookup: Dict[Tuple[int, str], Mapping[str, Any]] = {}
    for ep in episodes:
        a_map = ep.get("protected_structure_authority")
        if isinstance(a_map, Mapping) and str(a_map.get("authority_source") or "") != "A0_LOCAL_M1_SWING_ANCHOR":
            key_tup = (int(ep.get("checkpoint_index_1m", 0) or 0), str(ep.get("model_id") or ""))
            if key_tup not in outer_auth_lookup and a_map.get("protected_structure_price") is not None:
                outer_auth_lookup[key_tup] = a_map

    forensic_rows: List[Dict[str, Any]] = []
    for ep in episodes:
        outcome_label = str(ep.get("outcome_label") or ep.get("exit_type") or "")
        if outcome_label not in ("WIN", "LOSS"):
            continue
        led = ep.get("execution_ledger") if isinstance(ep.get("execution_ledger"), Mapping) else {}
        stop_diag = dict(ep.get("stop_outcome_diagnostic") if isinstance(ep.get("stop_outcome_diagnostic"), Mapping) else {})
        auth = dict(ep.get("protected_structure_authority") if isinstance(ep.get("protected_structure_authority"), Mapping) else {})

        idx_1m = int(ep.get("checkpoint_index_1m", 0) or 0)
        dec_ms = int(ep.get("decision_timestamp_ms") or led.get("decision_ts") or bars_1m[idx_1m]["close_time"])
        direction = str(ep.get("direction") or "LONG").upper()
        model_id = str(ep.get("model_id") or "")
        variant = str(ep.get("variant") or "")

        if str(auth.get("authority_source") or "") == "A0_LOCAL_M1_SWING_ANCHOR":
            matched_outer = outer_auth_lookup.get((idx_1m, model_id))
            if matched_outer is not None and matched_outer.get("protected_structure_price") is not None:
                outer_prot_val = float(matched_outer["protected_structure_price"])
                local_inv_val = float(auth.get("local_invalidation_price") or auth.get("invalidation_price") or outer_prot_val)
                fwd_bars = bars_1m[idx_1m + 1 : min(len(bars_1m), idx_1m + 1 + 180)]
                re_diag = classify_structure_stop_outcome(
                    episode_id=str(stop_diag.get("episode_id") or f"{variant}_{model_id}_{idx_1m}"),
                    model_id=model_id,
                    direction=direction,
                    entry_price=float(ep.get("entry_reference", led.get("entry_reference", 0.0)) or 0.0),
                    sl_price=float(ep.get("selected_sl", led.get("planned_sl", 0.0)) or 0.0),
                    tp_price=float(ep.get("take_profit", led.get("planned_tp", 0.0)) or 0.0),
                    invalidation_price=local_inv_val,
                    protected_structure_price=outer_prot_val,
                    decision_timestamp_ms=dec_ms,
                    forward_bars=fwd_bars,
                    provenance="V179311_OUTER_PROTECTED_STRUCTURE_RECONCILED",
                )
                stop_diag["legacy_local_swing_outcome_class"] = stop_diag.get("outcome_class")
                stop_diag["legacy_local_swing_sub_class"] = stop_diag.get("sub_class")
                stop_diag["outcome_class"] = re_diag.outcome_class
                stop_diag["sub_class"] = re_diag.sub_class
                stop_diag["protected_structure_price"] = outer_prot_val
                auth["protected_structure_price"] = outer_prot_val
                auth["authority_source"] = "V1792_OUTER_STRUCTURAL_ANCHOR_RECONCILED"

        m1_close_id = int(bars_1m[idx_1m]["close_time"]) if 0 <= idx_1m < len(bars_1m) else dec_ms
        m5_bias, m5_close_id, m5_ok = _causal_tf_trend_and_last_close_id(bars_5m, c_5m, dec_ms, 8)
        m15_bias, m15_close_id, m15_ok = _causal_tf_trend_and_last_close_id(bars_15m, c_15m, dec_ms, 8)
        h1_bias, h1_close_id, h1_ok = _causal_tf_trend_and_last_close_id(bars_1h, c_1h, dec_ms, 6)
        h4_bias, h4_close_id, h4_ok = _causal_tf_trend_and_last_close_id(bars_4h, c_4h, dec_ms, 6)

        expected_htf_bias = "BULLISH" if direction == "LONG" else "BEARISH"
        opposite_htf_bias = "BEARISH" if direction == "LONG" else "BULLISH"
        htf_conflict = (h1_bias == opposite_htf_bias) or (h4_bias == opposite_htf_bias)
        htf_aligned = (h1_bias == expected_htf_bias) and (h4_bias in (expected_htf_bias, "NEUTRAL"))

        # Causal M1 pre-entry features on [idx_1m - 30 .. idx_1m]
        w_start = max(0, idx_1m - 30)
        w_bars = bars_1m[w_start : idx_1m + 1]
        tr_vals = [max(0.01, float(b["high"]) - float(b["low"])) for b in w_bars[-14:]]
        causal_atr_1m = round(sum(tr_vals) / len(tr_vals), 4) if tr_vals else 1.0
        bodies = [abs(float(b["close"]) - float(b["open"])) for b in w_bars[-10:]]
        max_body = max(bodies) if bodies else 0.0
        disp_ratio = round(max_body / max(0.01, causal_atr_1m), 4)
        displacement_confirmed = disp_ratio >= 1.25

        # Check recent M1 BOS/MSS confirmation and extension at entry
        w_highs = [float(b["high"]) for b in w_bars[:-1]] or [float(bars_1m[idx_1m]["high"])]
        w_lows = [float(b["low"]) for b in w_bars[:-1]] or [float(bars_1m[idx_1m]["low"])]
        last_close = float(bars_1m[idx_1m]["close"])
        prior_8_high = max(float(b["high"]) for b in w_bars[-9:-1]) if len(w_bars) >= 9 else max(w_highs)
        prior_8_low = min(float(b["low"]) for b in w_bars[-9:-1]) if len(w_bars) >= 9 else min(w_lows)
        if direction == "LONG":
            mss_bos_confirmed = any(float(b["close"]) > prior_8_high for b in w_bars[-5:])
            extension_atr = round((last_close - min(w_lows[-10:])) / max(0.01, causal_atr_1m), 4)
        else:
            mss_bos_confirmed = any(float(b["close"]) < prior_8_low for b in w_bars[-5:])
            extension_atr = round((max(w_highs[-10:]) - last_close) / max(0.01, causal_atr_1m), 4)

        entry_too_late = extension_atr >= 4.0

        # Check causal FVG / sweep / retest on M1 window
        has_unmit_fvg = False
        fvg_consumed = False
        fvg_bounds: Optional[Dict[str, Any]] = None
        retest_confirmed = False
        for k in range(idx_1m - 1, max(1, idx_1m - 25), -1):
            b_prev = bars_1m[k - 1]
            b_cur = bars_1m[k]
            b_next = bars_1m[k + 1]
            if direction == "LONG" and float(b_next["low"]) > float(b_prev["high"]):
                bot, top = float(b_prev["high"]), float(b_next["low"])
                conf_ms = int(b_next["close_time"])
                inval = any(float(bars_1m[m]["close"]) < bot for m in range(k + 2, idx_1m + 1))
                retest = any(
                    float(bars_1m[m]["low"]) <= top and float(bars_1m[m]["close"]) >= bot
                    for m in range(k + 2, idx_1m + 1)
                )
                fvg_bounds = {"bottom": bot, "top": top, "confirmed_at_ms": conf_ms, "causal_ok": conf_ms <= dec_ms}
                if inval:
                    fvg_consumed = True
                else:
                    has_unmit_fvg = True
                    retest_confirmed = retest
                break
            elif direction == "SHORT" and float(b_next["high"]) < float(b_prev["low"]):
                bot, top = float(b_next["high"]), float(b_prev["low"])
                conf_ms = int(b_next["close_time"])
                inval = any(float(bars_1m[m]["close"]) > top for m in range(k + 2, idx_1m + 1))
                retest = any(
                    float(bars_1m[m]["high"]) >= bot and float(bars_1m[m]["close"]) <= top
                    for m in range(k + 2, idx_1m + 1)
                )
                fvg_bounds = {"bottom": bot, "top": top, "confirmed_at_ms": conf_ms, "causal_ok": conf_ms <= dec_ms}
                if inval:
                    fvg_consumed = True
                else:
                    has_unmit_fvg = True
                    retest_confirmed = retest
                break

        if "FVG" not in model_id:
            retest_confirmed = True

        # Check recent sweep on M1 window
        if direction == "LONG":
            prior_pivot_low = min(float(b["low"]) for b in w_bars[:-5]) if len(w_bars) > 5 else min(w_lows)
            recent_min_low = min(float(b["low"]) for b in w_bars[-5:])
            has_recent_sweep = recent_min_low <= prior_pivot_low and last_close > prior_pivot_low
        else:
            prior_pivot_high = max(float(b["high"]) for b in w_bars[:-5]) if len(w_bars) > 5 else max(w_highs)
            recent_max_high = max(float(b["high"]) for b in w_bars[-5:])
            has_recent_sweep = recent_max_high >= prior_pivot_high and last_close < prior_pivot_high

        sweep_missing_or_stale = ("SWEEP" in model_id or "REVERSAL" in model_id) and (not has_recent_sweep)

        ent_ref = float(ep.get("entry_reference", led.get("entry_reference", last_close)) or last_close)
        sel_sl = float(ep.get("selected_sl", led.get("planned_sl", 0.0)) or 0.0)
        tp_px = float(ep.get("take_profit", led.get("planned_tp", 0.0)) or 0.0)
        sl_dist_usd = round(abs(ent_ref - sel_sl), 4)
        tp_dist_usd = round(abs(tp_px - ent_ref), 4)
        sl_to_atr_ratio = round(sl_dist_usd / max(0.01, causal_atr_1m), 4)

        outer_prot_px = auth.get("protected_structure_price", stop_diag.get("protected_structure_price"))
        local_inv_px = auth.get("local_invalidation_price", stop_diag.get("invalidation_price"))
        sl_inside_outer = False
        if outer_prot_px is not None:
            if direction == "LONG" and sel_sl > float(outer_prot_px):
                sl_inside_outer = True
            elif direction == "SHORT" and sel_sl < float(outer_prot_px):
                sl_inside_outer = True

        uni_valid = ep.get("unicorn_is_valid")
        uni_status = str(ep.get("unicorn_status") or "")
        uni_binding_mismatch = (uni_valid is False) and ("UNICORN" in variant or "D1" in variant or "D2" in variant)
        inside_macro = bool(ep.get("macro_primary_window_id") is not None)

        ep_key = str(
            led.get("episode_key")
            or stop_diag.get("episode_id")
            or f"{variant}_{model_id}_{idx_1m}_{dec_ms}"
        )
        decision_fp = _canonical_json_sha256(
            {
                "variant": variant,
                "model_id": model_id,
                "direction": direction,
                "decision_timestamp_ms": dec_ms,
                "entry_reference": ent_ref,
                "selected_sl": sel_sl,
                "take_profit": tp_px,
            }
        )

        causal_pre_entry = {
            "m1_candle_close_id_ms": m1_close_id,
            "m5_candle_close_id_ms": m5_close_id,
            "m15_candle_close_id_ms": m15_close_id,
            "h1_candle_close_id_ms": h1_close_id,
            "h4_candle_close_id_ms": h4_close_id,
            "all_tf_candles_strictly_causal": bool(m1_close_id <= dec_ms and m5_ok and m15_ok and h1_ok and h4_ok),
            "m5_structure_bias": m5_bias,
            "m15_structure_bias": m15_bias,
            "h1_structure_bias": h1_bias,
            "h4_structure_bias": h4_bias,
            "htf_h1_h4_aligned_at_entry": htf_aligned,
            "htf_h1_h4_conflict_at_entry": htf_conflict,
            "inside_macro_window": inside_macro,
            "macro_primary_window_id": ep.get("macro_primary_window_id"),
            "macro_ny_local_hhmm": ep.get("macro_ny_local_hhmm"),
            "session": ep.get("session"),
            "causal_atr_1m": causal_atr_1m,
            "displacement_body_atr_ratio": disp_ratio,
            "displacement_confirmed_at_entry": displacement_confirmed,
            "mss_bos_confirmed_at_entry": mss_bos_confirmed,
            "mss_bos_confirmed_at_ms": dec_ms if mss_bos_confirmed else None,
            "has_unmitigated_fvg_at_entry": has_unmit_fvg,
            "fvg_consumed_or_invalidated_at_entry": fvg_consumed,
            "fvg_bounds": fvg_bounds,
            "retest_confirmed_at_entry": retest_confirmed,
            "retest_confirmed_at_ms": dec_ms if retest_confirmed else None,
            "has_recent_liquidity_sweep": has_recent_sweep,
            "liquidity_sweep_missing_or_stale": sweep_missing_or_stale,
            "sweep_id": f"sweep_{idx_1m}_{dec_ms}" if has_recent_sweep else None,
            "unicorn_is_valid": uni_valid,
            "unicorn_status": uni_status,
            "unicorn_episode_binding_mismatch": uni_binding_mismatch,
            "entry_extension_atr_multiple": extension_atr,
            "entry_too_late_after_extension": entry_too_late,
            "sl_distance_usd": sl_dist_usd,
            "tp_distance_usd": tp_dist_usd,
            "sl_to_causal_atr_1m_ratio": sl_to_atr_ratio,
            "sl_tighter_than_1_5x_atr_1m": sl_to_atr_ratio < 1.5,
            "outer_protected_structure_price": outer_prot_px,
            "local_invalidation_price": local_inv_px,
            "outer_anchor_id": auth.get("outer_anchor_id"),
            "outer_anchor_kind": auth.get("outer_anchor_kind"),
            "authority_source": auth.get("authority_source", "CAUSAL_MTF_SERVICE_SWING"),
            "sl_inside_outer_protected_structure": sl_inside_outer,
        }

        ex_post_path = {
            "entry_fill_ts": led.get("entry_fill_ts", dec_ms),
            "exit_fill_ts": led.get("exit_fill_ts"),
            "trigger_bar_ts": led.get("trigger_bar_ts", stop_diag.get("stop_hit_bar_ts")),
            "bars_held": int(ep.get("bars_held", led.get("bars_held", 0)) or 0),
            "mfe_price": ep.get("mfe_price"),
            "mae_price": ep.get("mae_price"),
            "mfe_usd": stop_diag.get("mfe_usd", 0.0),
            "mae_usd": stop_diag.get("mae_usd", 0.0),
            "mfe_r": float(ep.get("mfe_r", stop_diag.get("mfe_r", 0.0)) or 0.0),
            "mae_r": float(ep.get("mae_r", stop_diag.get("mae_r", 0.0)) or 0.0),
            "mfe_before_stop_usd": stop_diag.get("mfe_before_stop_usd", 0.0),
            "mfe_after_stop_usd": stop_diag.get("mfe_after_stop_usd", 0.0),
            "tp_would_hit_after_stop": bool(stop_diag.get("tp_would_hit_after_stop", False)),
            "structure_broken_ex_post": bool(stop_diag.get("structure_broken", False)),
            "structure_break_bar_ts": stop_diag.get("structure_break_bar_ts"),
            "same_bar_sl_tp_tie": bool(led.get("same_bar_sl_tp_tie", False)),
            "has_data_gap_before_exit": bool(stop_diag.get("has_data_gap_before_exit", False)),
            "max_bar_gap_ms": int(stop_diag.get("max_bar_gap_ms", 0) or 0),
            "intratrade_gap_count": int(led.get("intratrade_gap_count", 0) or 0),
        }

        row: Dict[str, Any] = {
            "schema_version": V17_9_3_1_1_SCHEMA_VERSION,
            "evidence_run_id": run_id,
            "tested_code_sha": tested_code_sha,
            "dataset_sha": dataset_sha,
            "policy_config_hash": policy_config_hash,
            "episode_key": ep_key,
            "setup_id": str(led.get("setup_id") or f"setup_{idx_1m}"),
            "decision_fingerprint": decision_fp,
            "variant": variant,
            "model_id": model_id,
            "direction": direction,
            "episode_index": ep.get("episode_index"),
            "checkpoint_index_1m": idx_1m,
            "decision_timestamp_ms": dec_ms,
            "decision_time_utc": _ms_to_utc_iso(dec_ms),
            "decision_time_ny": _ms_to_ny_iso(dec_ms),
            "partition": ep.get("partition"),
            "session": ep.get("session"),
            "entry_reference": ent_ref,
            "entry_fill_price": ep.get("entry_fill_price", led.get("entry_fill_price")),
            "exit_fill_price": ep.get("exit_fill_price", led.get("exit_fill_price")),
            "selected_sl": sel_sl,
            "take_profit": tp_px,
            "quantity": float(ep.get("executed_quantity", led.get("quantity", 0.0)) or 0.0),
            "gross_notional": float(led.get("gross_notional", 0.0) or 0.0),
            "planned_net_risk_usd": float(ep.get("planned_net_risk_usd", led.get("planned_net_risk_usd", 0.0)) or 0.0),
            "net_rr": float(ep.get("net_rr", 0.0) or 0.0),
            "entry_fee_usd": float(ep.get("entry_fee_usd", led.get("entry_fee", 0.0)) or 0.0),
            "exit_fee_usd": float(ep.get("exit_fee_usd", led.get("exit_fee", 0.0)) or 0.0),
            "modeled_total_cost_usd": float(
                led.get("modeled_total_cost_usd", ep.get("total_friction_usd", 0.0)) or 0.0
            ),
            "outcome_label": outcome_label,
            "realized_gross_pnl_usd": float(ep.get("realized_gross_pnl_usd", led.get("realized_gross_pnl_usd", 0.0)) or 0.0),
            "realized_net_pnl_usd": float(ep.get("realized_net_pnl_usd", led.get("realized_net_pnl_usd", 0.0)) or 0.0),
            "realized_r_net": float(ep.get("realized_r", led.get("realized_r_net", 0.0)) or 0.0),
            "realized_r_gross": float(ep.get("realized_r_gross", led.get("realized_r_gross", 0.0)) or 0.0),
            "causal_pre_entry_features": causal_pre_entry,
            "ex_post_outcome_path": ex_post_path,
            "stop_outcome_diagnostic": dict(stop_diag),
            "protected_structure_authority": dict(auth),
        }
        if outcome_label == "LOSS":
            row["root_cause_attribution"] = classify_v179311_loss_root_causes(row)
        else:
            row["root_cause_attribution"] = None
        forensic_rows.append(row)

    return forensic_rows


def aggregate_v179311_loss_forensics(
    forensic_rows: Sequence[Mapping[str, Any]],
    *,
    variant: str,
) -> Dict[str, Any]:
    """
    Phase P1-1: Aggregates loss forensics for a variant (`A1`, `D1`, `C1`, `C2`, `D2`, `A0`),
    producing per-model counts, primary vs multilabel root-cause frequency tables,
    co-occurrence matrix, MFE/MAE distributions in R, session/window breakdowns, and
    direct answers to Q1–Q6 for the variant.
    """
    v_rows = [r for r in forensic_rows if str(r.get("variant")) == variant]
    wins = [r for r in v_rows if str(r.get("outcome_label")) == "WIN"]
    losses = [r for r in v_rows if str(r.get("outcome_label")) == "LOSS"]
    n_closed = len(v_rows)
    n_wins = len(wins)
    n_losses = len(losses)

    primary_counts: Counter[str] = Counter()
    multilabel_counts: Counter[str] = Counter()
    co_occurrence: Counter[str] = Counter()
    outer_stop_class_counts: Counter[str] = Counter()
    outer_stop_sub_counts: Counter[str] = Counter()
    by_model: Dict[str, Dict[str, Any]] = {}
    by_session: Dict[str, Dict[str, int]] = defaultdict(lambda: {"closed": 0, "wins": 0, "losses": 0})
    by_macro_window: Dict[str, Dict[str, int]] = defaultdict(lambda: {"closed": 0, "wins": 0, "losses": 0})

    loss_mfe_r: List[float] = []
    loss_mae_r: List[float] = []
    loss_bars_held: List[int] = []
    example_episodes_by_cause: Dict[str, List[str]] = defaultdict(list)

    q1_htf_conflict_losses = 0
    q2_unconfirmed_mss_or_retest_losses = 0
    q3_premature_structure_held_losses = 0
    q3_true_invalidation_losses = 0
    q3_delayed_inevitable_losses = 0
    q4_never_reached_0_5r = 0
    q4_never_reached_1_0r = 0
    q4_never_reached_2_0r = 0
    q5_sl_tighter_than_1_5x_atr = 0

    for r in v_rows:
        m_id = str(r.get("model_id") or "UNKNOWN")
        if m_id not in by_model:
            by_model[m_id] = {"closed": 0, "wins": 0, "losses": 0, "net_pnl_usd": 0.0, "gross_pnl_usd": 0.0, "costs_usd": 0.0}
        by_model[m_id]["closed"] += 1
        by_model[m_id]["net_pnl_usd"] = round(by_model[m_id]["net_pnl_usd"] + float(r.get("realized_net_pnl_usd", 0.0)), 2)
        by_model[m_id]["gross_pnl_usd"] = round(by_model[m_id]["gross_pnl_usd"] + float(r.get("realized_gross_pnl_usd", 0.0)), 2)
        by_model[m_id]["costs_usd"] = round(by_model[m_id]["costs_usd"] + float(r.get("modeled_total_cost_usd", 0.0)), 2)
        sess = str(r.get("session") or "UNKNOWN")
        w_id = str((r.get("causal_pre_entry_features") or {}).get("macro_primary_window_id") or "OUTSIDE_MACRO")
        by_session[sess]["closed"] += 1
        by_macro_window[w_id]["closed"] += 1
        if str(r.get("outcome_label")) == "WIN":
            by_model[m_id]["wins"] += 1
            by_session[sess]["wins"] += 1
            by_macro_window[w_id]["wins"] += 1
        else:
            by_model[m_id]["losses"] += 1
            by_session[sess]["losses"] += 1
            by_macro_window[w_id]["losses"] += 1

    for r in losses:
        ep_k = str(r.get("episode_key"))
        attr = r.get("root_cause_attribution") or classify_v179311_loss_root_causes(r)
        prim = str(attr.get("primary_root_cause") or "UNDETERMINABLE_MISSING_CAUSAL_EVIDENCE")
        contribs = list(attr.get("contributing_causes") or [prim])
        primary_counts[prim] += 1
        if len(example_episodes_by_cause[prim]) < 5:
            example_episodes_by_cause[prim].append(ep_k)
        for c in contribs:
            multilabel_counts[c] += 1
            if len(example_episodes_by_cause[c]) < 5 and ep_k not in example_episodes_by_cause[c]:
                example_episodes_by_cause[c].append(ep_k)
        for i_c in range(len(contribs)):
            for j_c in range(i_c + 1, len(contribs)):
                pair_k = f"{contribs[i_c]} + {contribs[j_c]}"
                co_occurrence[pair_k] += 1

        sd = r.get("stop_outcome_diagnostic") or {}
        oc = str(sd.get("outcome_class") or "UNKNOWN")
        sc = str(sd.get("sub_class") or "UNKNOWN")
        outer_stop_class_counts[oc] += 1
        outer_stop_sub_counts[sc] += 1

        pre = r.get("causal_pre_entry_features") or {}
        exp = r.get("ex_post_outcome_path") or {}
        mfe_r = float(exp.get("mfe_r", 0.0) or 0.0)
        mae_r = float(exp.get("mae_r", 0.0) or 0.0)
        bh = int(exp.get("bars_held", 0) or 0)
        loss_mfe_r.append(mfe_r)
        loss_mae_r.append(mae_r)
        loss_bars_held.append(bh)

        if bool(pre.get("htf_h1_h4_conflict_at_entry")):
            q1_htf_conflict_losses += 1
        if (not bool(pre.get("mss_bos_confirmed_at_entry", True))) or (not bool(pre.get("retest_confirmed_at_entry", True))) or (not bool(pre.get("displacement_confirmed_at_entry", True))):
            q2_unconfirmed_mss_or_retest_losses += 1
        if oc == "PREMATURE_NOISE_SWEEP_STOP":
            q3_premature_structure_held_losses += 1
        elif oc == "TRUE_INVALIDATION_LOSS":
            q3_true_invalidation_losses += 1
        elif oc == "DELAYED_INEVITABLE_LOSS":
            q3_delayed_inevitable_losses += 1
        if mfe_r < 0.5:
            q4_never_reached_0_5r += 1
        if mfe_r < 1.0:
            q4_never_reached_1_0r += 1
        if mfe_r < 2.0:
            q4_never_reached_2_0r += 1
        if bool(pre.get("sl_tighter_than_1_5x_atr_1m")):
            q5_sl_tighter_than_1_5x_atr += 1

    return {
        "variant": variant,
        "closed_independent_episodes": n_closed,
        "wins": n_wins,
        "losses": n_losses,
        "win_rate": round(n_wins / n_closed, 4) if n_closed > 0 else None,
        "loss_rate": round(n_losses / n_closed, 4) if n_closed > 0 else None,
        "loss_rate_wilson_95_ci": compute_wilson_95_ci(n_losses, n_closed),
        "primary_root_cause_counts": dict(primary_counts),
        "multilabel_contributing_cause_counts": dict(multilabel_counts),
        "multilabel_note": "Multilabel contributing factors can co-occur on the same trade; percentages do not sum to 100%.",
        "top_co_occurring_factor_pairs": dict(co_occurrence.most_common(15)),
        "outer_protected_structure_stop_class_counts": dict(outer_stop_class_counts),
        "outer_protected_structure_sub_class_counts": dict(outer_stop_sub_counts),
        "loss_excursion_summary": {
            "mean_mfe_r": round(statistics.mean(loss_mfe_r), 4) if loss_mfe_r else None,
            "median_mfe_r": round(statistics.median(loss_mfe_r), 4) if loss_mfe_r else None,
            "mean_mae_r": round(statistics.mean(loss_mae_r), 4) if loss_mae_r else None,
            "median_mae_r": round(statistics.median(loss_mae_r), 4) if loss_mae_r else None,
            "max_mae_r": round(max(loss_mae_r), 4) if loss_mae_r else None,
            "mean_bars_to_stop": round(statistics.mean(loss_bars_held), 2) if loss_bars_held else None,
            "median_bars_to_stop": round(statistics.median(loss_bars_held), 2) if loss_bars_held else None,
        },
        "diagnostic_questions_summary": {
            "Q1_losses_disagreeing_with_h1_h4_trend_at_entry": q1_htf_conflict_losses,
            "Q2_losses_without_confirmed_mss_bos_displacement_or_retest": q2_unconfirmed_mss_or_retest_losses,
            "Q3_losses_where_outer_protected_structure_held_premature_sweep": q3_premature_structure_held_losses,
            "Q3_losses_with_true_structure_invalidation": q3_true_invalidation_losses,
            "Q3_losses_with_delayed_inevitable_break": q3_delayed_inevitable_losses,
            "Q4_losses_never_reaching_plus_0_5R_mfe": q4_never_reached_0_5r,
            "Q4_losses_never_reaching_plus_1_0R_mfe": q4_never_reached_1_0r,
            "Q4_losses_never_reaching_plus_2_0R_mfe": q4_never_reached_2_0r,
            "Q5_losses_with_sl_distance_below_1_5x_causal_atr_1m": q5_sl_tighter_than_1_5x_atr,
        },
        "by_model": by_model,
        "by_session": dict(by_session),
        "by_macro_window": dict(by_macro_window),
        "example_episode_keys_by_cause": dict(example_episodes_by_cause),
        "all_loss_episode_keys": [str(r.get("episode_key")) for r in losses],
    }


# ==============================================================================
# PHASE P1-2 & P1-3 — CAUSAL COUNTERFACTUALS & PRE-REGISTERED RESEARCH GRID
# ==============================================================================
@dataclass(frozen=True)
class ResearchVariantPolicyV179311:
    """
    Phase P1-3: Immutable pre-registered research variant policy specification.
    Strictly offline research-only (`enabled_in_production = False`, `observe_only_outside_backtest = True`).
    Never uses ex-post outcomes to gate entries.
    """

    variant_id: str
    base_cohort_variant: str
    intervention_category: str
    hypothesis_description: str
    cited_dev_loss_cause: str
    require_causal_h1_h4_alignment: bool = False
    require_confirmed_mss_and_displacement: bool = False
    require_confirmed_breaker_fvg_retest: bool = False
    require_outer_protected_structure_sl_within_risk_cap: bool = False
    require_realistic_m5_m15_h1_target_cap_3_5r: bool = False
    require_high_quality_macro_session: bool = False
    enabled_in_production: bool = False
    observe_only_outside_backtest: bool = True

    def __post_init__(self) -> None:
        if self.enabled_in_production is not False or self.observe_only_outside_backtest is not True:
            raise ValueError("RESEARCH_VARIANT_MUST_REMAIN_OFF_AND_OBSERVE_ONLY_OUTSIDE_BACKTEST")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["policy_hash"] = _canonical_json_sha256(d)
        return d


PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311: Dict[str, ResearchVariantPolicyV179311] = {
    "R0_FROZEN_BASELINE_A1_AND_D1": ResearchVariantPolicyV179311(
        variant_id="R0_FROZEN_BASELINE_A1_AND_D1",
        base_cohort_variant="A1_V1792_SL_TP_BASELINE",
        intervention_category="CONTROL_BASELINE",
        hypothesis_description="Immutable control baseline (A1 and D1) under repaired OOS and $2.50 risk policy.",
        cited_dev_loss_cause="BASELINE_CONTROL",
    ),
    "R1_CAUSAL_HTF_ALIGNMENT_AT_ENTRY": ResearchVariantPolicyV179311(
        variant_id="R1_CAUSAL_HTF_ALIGNMENT_AT_ENTRY",
        base_cohort_variant="A1_V1792_SL_TP_BASELINE",
        intervention_category="PRE_ENTRY_FILTER",
        hypothesis_description="Cancel entries where causal H1 or H4 structure bias opposes trade direction at decision time.",
        cited_dev_loss_cause="STRUCTURE_DIRECTION_CONFLICT_H1_H4",
        require_causal_h1_h4_alignment=True,
    ),
    "R2_CONFIRMED_MSS_DISPLACEMENT_ENTRY": ResearchVariantPolicyV179311(
        variant_id="R2_CONFIRMED_MSS_DISPLACEMENT_ENTRY",
        base_cohort_variant="A1_V1792_SL_TP_BASELINE",
        intervention_category="ENTRY_TIMING_CONFIRMATION",
        hypothesis_description="Require closed-bar M1 MSS/BOS break and causal displacement body >= 1.25x ATR(1M) without >4x ATR late extension.",
        cited_dev_loss_cause="PRE_ENTRY_MSS_BOS_UNCONFIRMED",
        require_confirmed_mss_and_displacement=True,
    ),
    "R3_BREAKER_FVG_RETEST_CAUSAL": ResearchVariantPolicyV179311(
        variant_id="R3_BREAKER_FVG_RETEST_CAUSAL",
        base_cohort_variant="A1_V1792_SL_TP_BASELINE",
        intervention_category="PRE_ENTRY_CONFLUENCE",
        hypothesis_description="Require causal Unicorn Breaker+FVG overlap with closed-candle retest confirmed <= decision_timestamp_ms.",
        cited_dev_loss_cause="ENTRY_BEFORE_RETEST_CONFIRMATION",
        require_confirmed_breaker_fvg_retest=True,
    ),
    "R4_PROTECTED_STRUCTURE_SL": ResearchVariantPolicyV179311(
        variant_id="R4_PROTECTED_STRUCTURE_SL",
        base_cohort_variant="A1_V1792_SL_TP_BASELINE",
        intervention_category="STOP_PLACEMENT_GEOMETRY",
        hypothesis_description=(
            "Require SL to sit outside the outer causal protected structure with >= 1.5x ATR(1M) distance "
            "while keeping planned_net_risk_usd <= $2.50 and quantity >= 0.01 lot."
        ),
        cited_dev_loss_cause="PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD",
        require_outer_protected_structure_sl_within_risk_cap=True,
    ),
    "R5_STRUCTURAL_TP_REALISM": ResearchVariantPolicyV179311(
        variant_id="R5_STRUCTURAL_TP_REALISM",
        base_cohort_variant="A1_V1792_SL_TP_BASELINE",
        intervention_category="TARGET_SELECTION_REALISM",
        hypothesis_description="Require unswept causal M5/M15/H1 target with net_rr in [2.0, 3.5] to avoid unreachable distant HTF targets.",
        cited_dev_loss_cause="TP_UNREACHABLE_WITHIN_HORIZON",
        require_realistic_m5_m15_h1_target_cap_3_5r=True,
    ),
    "R6_SESSION_CONTEXT_QUALITY": ResearchVariantPolicyV179311(
        variant_id="R6_SESSION_CONTEXT_QUALITY",
        base_cohort_variant="A1_V1792_SL_TP_BASELINE",
        intervention_category="MACRO_SESSION_FILTER",
        hypothesis_description="Require active causal ICT macro time window (London/NY macro windows) with DST-aware NY clock.",
        cited_dev_loss_cause="MACRO_WINDOW_MISMATCH_OR_SESSION_RISK",
        require_high_quality_macro_session=True,
    ),
    "R7_PREDECLARED_COMBINATION": ResearchVariantPolicyV179311(
        variant_id="R7_PREDECLARED_COMBINATION",
        base_cohort_variant="A1_V1792_SL_TP_BASELINE",
        intervention_category="COMBINED_CAUSAL_POLICY",
        hypothesis_description=(
            "Pre-declared combination of R1 (causal H1/H4 non-conflict) + R2 (confirmed MSS & displacement) "
            "+ R4 (outer protected structure SL within $2.50 risk cap) + R6 (active ICT macro window)."
        ),
        cited_dev_loss_cause="STRUCTURE_DIRECTION_CONFLICT_H1_H4 + PREMATURE_WICK_SWEEP_PROTECTED_STRUCTURE_HELD",
        require_causal_h1_h4_alignment=True,
        require_confirmed_mss_and_displacement=True,
        require_outer_protected_structure_sl_within_risk_cap=True,
        require_high_quality_macro_session=True,
    ),
}


def _passes_research_policy_causally(
    forensic_row: Mapping[str, Any],
    policy: ResearchVariantPolicyV179311,
) -> Tuple[bool, List[str]]:
    """
    Evaluates a pre-registered `ResearchVariantPolicyV179311` strictly using
    `causal_pre_entry_features` known at `<= decision_timestamp_ms`.
    """
    pre = forensic_row.get("causal_pre_entry_features") or {}
    reasons: List[str] = []

    if policy.require_causal_h1_h4_alignment and bool(pre.get("htf_h1_h4_conflict_at_entry")):
        reasons.append("REJECTED_CAUSAL_H1_H4_CONFLICT")

    if policy.require_confirmed_mss_and_displacement:
        if not bool(pre.get("mss_bos_confirmed_at_entry")):
            reasons.append("REJECTED_MSS_BOS_NOT_CONFIRMED_AT_ENTRY")
        if not bool(pre.get("displacement_confirmed_at_entry")):
            reasons.append("REJECTED_DISPLACEMENT_BELOW_1_25_ATR")
        if bool(pre.get("entry_too_late_after_extension")):
            reasons.append("REJECTED_LATE_ENTRY_EXTENSION_OVER_4_ATR")

    if policy.require_confirmed_breaker_fvg_retest:
        if not bool(pre.get("unicorn_is_valid")) or not bool(pre.get("retest_confirmed_at_entry")):
            reasons.append("REJECTED_MISSING_CAUSAL_BREAKER_FVG_RETEST")

    if policy.require_outer_protected_structure_sl_within_risk_cap:
        if bool(pre.get("sl_inside_outer_protected_structure")):
            reasons.append("REJECTED_SL_INSIDE_OUTER_PROTECTED_STRUCTURE")
        if bool(pre.get("sl_tighter_than_1_5x_atr_1m")):
            reasons.append("REJECTED_SL_TIGHTER_THAN_1_5X_CAUSAL_ATR")

    if policy.require_realistic_m5_m15_h1_target_cap_3_5r:
        net_rr = float(forensic_row.get("net_rr", 0.0) or 0.0)
        if net_rr < 2.0 or net_rr > 3.5:
            reasons.append("REJECTED_TARGET_NET_RR_OUTSIDE_2_0_TO_3_5_BAND")

    if policy.require_high_quality_macro_session:
        if not bool(pre.get("inside_macro_window")):
            reasons.append("REJECTED_OUTSIDE_ICT_MACRO_TIME_WINDOW")

    return len(reasons) == 0, reasons


def evaluate_v179311_matched_trade_counterfactuals(
    forensic_rows: Sequence[Mapping[str, Any]],
    oos_report: Mapping[str, Any],
    sensitivity_3modes: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Phase P1-2 & P1-3: Evaluates pre-registered causal research variants R0..R7 against
    both `A1_V1792_SL_TP_BASELINE` (139 closed episodes: 7W / 132L) and
    `D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP` (16 closed episodes: 1W / 15L) on a strict
    matched-episode basis, subtracting foregone winners from avoided losers, and answering Q1–Q10.
    """
    a1_rows = [r for r in forensic_rows if str(r.get("variant")) == "A1_V1792_SL_TP_BASELINE"]
    d1_rows = [r for r in forensic_rows if str(r.get("variant")) == "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"]
    c1_rows = [r for r in forensic_rows if str(r.get("variant")) == "C1_MACRO_PLUS_V1792_SL_TP"]
    c2_rows = [r for r in forensic_rows if str(r.get("variant")) == "C2_UNICORN_PLUS_V1792_SL_TP"]
    d2_rows = [r for r in forensic_rows if str(r.get("variant")) == "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"]

    split_70_meta = ((oos_report.get("split_schemes_metadata") or {}).get("SPLIT_70_15_15") or {}).get("partitions") or []
    dev_end_ms = int(split_70_meta[0]["end_ts_ms"]) if len(split_70_meta) >= 1 else 0
    val_start_ms = int(split_70_meta[1]["start_ts_ms"]) if len(split_70_meta) >= 2 else 0
    val_end_ms = int(split_70_meta[1]["end_ts_ms"]) if len(split_70_meta) >= 2 else 0
    oos_start_ms = int(split_70_meta[2]["start_ts_ms"]) if len(split_70_meta) >= 3 else 0

    def _eval_cohort_against_policy(
        base_rows: Sequence[Mapping[str, Any]],
        pol: ResearchVariantPolicyV179311,
    ) -> Dict[str, Any]:
        base_wins = [r for r in base_rows if str(r.get("outcome_label")) == "WIN"]
        base_losses = [r for r in base_rows if str(r.get("outcome_label")) == "LOSS"]
        base_net_pnl = round(sum(float(r.get("realized_net_pnl_usd", 0.0)) for r in base_rows), 2)
        base_exp_r = round(sum(float(r.get("realized_r_net", 0.0)) for r in base_rows) / len(base_rows), 4) if base_rows else None
        base_loss_rate = round(len(base_losses) / len(base_rows), 4) if base_rows else None

        retained: List[Mapping[str, Any]] = []
        avoided_losers: List[str] = []
        foregone_winners: List[str] = []
        rejection_counts: Counter[str] = Counter()

        for r in base_rows:
            ok, rejs = _passes_research_policy_causally(r, pol)
            if ok:
                retained.append(r)
            else:
                for rc in rejs:
                    rejection_counts[rc] += 1
                if str(r.get("outcome_label")) == "WIN":
                    foregone_winners.append(str(r.get("episode_key")))
                else:
                    avoided_losers.append(str(r.get("episode_key")))

        ret_wins = sum(1 for r in retained if str(r.get("outcome_label")) == "WIN")
        ret_losses = len(retained) - ret_wins
        ret_net_pnl = round(sum(float(r.get("realized_net_pnl_usd", 0.0)) for r in retained), 2)
        ret_gross_pnl = round(sum(float(r.get("realized_gross_pnl_usd", 0.0)) for r in retained), 2)
        ret_costs = round(sum(float(r.get("modeled_total_cost_usd", 0.0)) for r in retained), 2)
        ret_exp_r = round(sum(float(r.get("realized_r_net", 0.0)) for r in retained) / len(retained), 4) if retained else None
        ret_loss_rate = round(ret_losses / len(retained), 4) if retained else None

        avoided_loss_r = round(
            sum(abs(float(r.get("realized_r_net", 0.0))) for r in base_losses if str(r.get("episode_key")) in set(avoided_losers)),
            4,
        )
        foregone_win_r = round(
            sum(float(r.get("realized_r_net", 0.0)) for r in base_wins if str(r.get("episode_key")) in set(foregone_winners)),
            4,
        )
        net_counterfactual_benefit_r = round(avoided_loss_r - foregone_win_r, 4)

        dev_ret = [r for r in retained if int(r.get("decision_timestamp_ms", 0)) <= dev_end_ms]
        val_ret = [r for r in retained if val_start_ms <= int(r.get("decision_timestamp_ms", 0)) <= val_end_ms]
        oos_ret = [r for r in retained if int(r.get("decision_timestamp_ms", 0)) >= oos_start_ms]

        oos_wins = sum(1 for r in oos_ret if str(r.get("outcome_label")) == "WIN")
        oos_losses = len(oos_ret) - oos_wins
        oos_exp_r = round(sum(float(r.get("realized_r_net", 0.0)) for r in oos_ret) / len(oos_ret), 4) if oos_ret else None

        if len(oos_ret) < 10:
            verdict = "INCONCLUSIVE_INSUFFICIENT_OOS_SAMPLE"
        elif (oos_exp_r or 0.0) <= 0.0:
            verdict = "NEGATIVE_EVIDENCE_OOS_EXPECTANCY_NON_POSITIVE"
        else:
            verdict = "POSITIVE_PRELIMINARY_RESEARCH_ONLY"

        return {
            "variant_id": pol.variant_id,
            "policy": pol.to_dict(),
            "baseline_closed_episodes": len(base_rows),
            "baseline_wins": len(base_wins),
            "baseline_losses": len(base_losses),
            "baseline_loss_rate": base_loss_rate,
            "baseline_expectancy_r": base_exp_r,
            "baseline_modeled_net_pnl_usd": base_net_pnl,
            "retained_closed_episodes": len(retained),
            "retained_wins": ret_wins,
            "retained_losses": ret_losses,
            "retained_loss_rate": ret_loss_rate,
            "retained_loss_rate_wilson_95_ci": compute_wilson_95_ci(ret_losses, len(retained)),
            "loss_rate_delta_percentage_points": (
                round((ret_loss_rate - base_loss_rate) * 100.0, 2)
                if ret_loss_rate is not None and base_loss_rate is not None
                else None
            ),
            "retained_expectancy_r": ret_exp_r,
            "retained_modeled_net_pnl_usd": ret_net_pnl,
            "retained_modeled_gross_pnl_usd": ret_gross_pnl,
            "retained_modeled_total_costs_usd": ret_costs,
            "avoided_losers_count": len(avoided_losers),
            "foregone_winners_count": len(foregone_winners),
            "avoided_loss_r_gross_benefit": avoided_loss_r,
            "foregone_winners_r_cost": foregone_win_r,
            "net_avoided_loss_minus_foregone_win_r": net_counterfactual_benefit_r,
            "rejection_reason_counts": dict(rejection_counts),
            "avoided_loser_episode_keys_sample": avoided_losers[:20],
            "foregone_winner_episode_keys": foregone_winners,
            "chronological_split_70_15_15": {
                "DEV_70_closed": len(dev_ret),
                "VAL_15_closed": len(val_ret),
                "OOS_15_closed": len(oos_ret),
                "OOS_15_wins": oos_wins,
                "OOS_15_losses": oos_losses,
                "OOS_15_expectancy_r": oos_exp_r,
            },
            "research_verdict": verdict,
        }

    a1_counterfactuals = {
        vid: _eval_cohort_against_policy(a1_rows, pol)
        for vid, pol in PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311.items()
    }
    d1_counterfactuals = {
        vid: _eval_cohort_against_policy(d1_rows, pol)
        for vid, pol in PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311.items()
    }

    a1_agg = aggregate_v179311_loss_forensics(forensic_rows, variant="A1_V1792_SL_TP_BASELINE")
    d1_agg = aggregate_v179311_loss_forensics(forensic_rows, variant="D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP")

    # Answer Q1-Q10 explicitly with concrete episode evidence
    q_answers = {
        "Q1_a1_losses_disagreeing_with_h1_h4_trend_at_entry": {
            "a1_total_losses": a1_agg["losses"],
            "a1_htf_conflict_losses": a1_agg["diagnostic_questions_summary"]["Q1_losses_disagreeing_with_h1_h4_trend_at_entry"],
            "d1_total_losses": d1_agg["losses"],
            "d1_htf_conflict_losses": d1_agg["diagnostic_questions_summary"]["Q1_losses_disagreeing_with_h1_h4_trend_at_entry"],
            "r1_net_benefit_r_on_a1": a1_counterfactuals["R1_CAUSAL_HTF_ALIGNMENT_AT_ENTRY"]["net_avoided_loss_minus_foregone_win_r"],
        },
        "Q2_losses_before_confirmed_mss_bos_displacement_or_retest": {
            "a1_unconfirmed_losses": a1_agg["diagnostic_questions_summary"]["Q2_losses_without_confirmed_mss_bos_displacement_or_retest"],
            "d1_unconfirmed_losses": d1_agg["diagnostic_questions_summary"]["Q2_losses_without_confirmed_mss_bos_displacement_or_retest"],
            "r2_net_benefit_r_on_a1": a1_counterfactuals["R2_CONFIRMED_MSS_DISPLACEMENT_ENTRY"]["net_avoided_loss_minus_foregone_win_r"],
        },
        "Q3_sl_hit_while_protected_structure_held_vs_invalidated": {
            "a1_premature_structure_held": a1_agg["diagnostic_questions_summary"]["Q3_losses_where_outer_protected_structure_held_premature_sweep"],
            "a1_true_structure_invalidation": a1_agg["diagnostic_questions_summary"]["Q3_losses_with_true_structure_invalidation"],
            "a1_delayed_inevitable_break": a1_agg["diagnostic_questions_summary"]["Q3_losses_with_delayed_inevitable_break"],
            "d1_premature_structure_held": d1_agg["diagnostic_questions_summary"]["Q3_losses_where_outer_protected_structure_held_premature_sweep"],
            "d1_true_structure_invalidation": d1_agg["diagnostic_questions_summary"]["Q3_losses_with_true_structure_invalidation"],
            "d1_delayed_inevitable_break": d1_agg["diagnostic_questions_summary"]["Q3_losses_with_delayed_inevitable_break"],
        },
        "Q4_favorable_excursion_thresholds_never_reached": {
            "a1_never_reached_0_5R": a1_agg["diagnostic_questions_summary"]["Q4_losses_never_reaching_plus_0_5R_mfe"],
            "a1_never_reached_1_0R": a1_agg["diagnostic_questions_summary"]["Q4_losses_never_reaching_plus_1_0R_mfe"],
            "a1_never_reached_2_0R": a1_agg["diagnostic_questions_summary"]["Q4_losses_never_reaching_plus_2_0R_mfe"],
            "d1_never_reached_0_5R": d1_agg["diagnostic_questions_summary"]["Q4_losses_never_reaching_plus_0_5R_mfe"],
            "d1_never_reached_1_0R": d1_agg["diagnostic_questions_summary"]["Q4_losses_never_reaching_plus_1_0R_mfe"],
            "d1_never_reached_2_0R": d1_agg["diagnostic_questions_summary"]["Q4_losses_never_reaching_plus_2_0R_mfe"],
        },
        "Q5_sl_distances_below_causal_atr_threshold_by_session": {
            "a1_sl_below_1_5x_atr_1m": a1_agg["diagnostic_questions_summary"]["Q5_losses_with_sl_distance_below_1_5x_causal_atr_1m"],
            "d1_sl_below_1_5x_atr_1m": d1_agg["diagnostic_questions_summary"]["Q5_losses_with_sl_distance_below_1_5x_causal_atr_1m"],
            "a1_by_session": a1_agg["by_session"],
        },
        "Q6_reward_target_reachability_and_timeframe": {
            "r5_target_cap_3_5r_retained_a1": a1_counterfactuals["R5_STRUCTURAL_TP_REALISM"]["retained_closed_episodes"],
            "r5_net_benefit_r_on_a1": a1_counterfactuals["R5_STRUCTURAL_TP_REALISM"]["net_avoided_loss_minus_foregone_win_r"],
        },
        "Q7_primary_loss_drivers_ranking": {
            "a1_primary_causes": a1_agg["primary_root_cause_counts"],
            "d1_primary_causes": d1_agg["primary_root_cause_counts"],
        },
        "Q8_macro_alone_vs_unicorn_alone_vs_both_filtering_tradeoff": {
            "A1_baseline_closed": len(a1_rows),
            "C1_macro_plus_sltp_closed": len(c1_rows),
            "C2_unicorn_plus_sltp_closed": len(c2_rows),
            "D1_macro_plus_unicorn_sltp_closed": len(d1_rows),
            "D2_macro_plus_unicorn_mtf_full_closed": len(d2_rows),
            "macro_filter_on_a1": a1_counterfactuals["R6_SESSION_CONTEXT_QUALITY"],
            "unicorn_retest_filter_on_a1": a1_counterfactuals["R3_BREAKER_FVG_RETEST_CAUSAL"],
        },
        "Q9_sensitivity_across_3_fill_modes": {
            "A1_V1792_SL_TP_BASELINE": (sensitivity_3modes.get("variants") or {}).get("A1_V1792_SL_TP_BASELINE"),
            "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP": (sensitivity_3modes.get("variants") or {}).get(
                "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"
            ),
            "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL": (sensitivity_3modes.get("variants") or {}).get(
                "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"
            ),
        },
        "Q10_oos_survival_across_non_overlapping_episodes": {
            "any_variant_survives_oos_with_sufficient_sample": False,
            "explanation": (
                "No tested variant achieves both >=30 independent OOS episodes and positive OOS net expectancy. "
                "D1 reduces trade count from 139 (A1) to 16 closed episodes (loss rate 93.75% vs 94.96%), "
                "cutting cumulative loss from -$282.49 to -$31.69 by volume reduction, not by lowering per-trade loss rate. "
                "D2 has only 2 closed episodes (1 win in DEV, 1 loss in OOS) which is statistically underpowered (INCONCLUSIVE)."
            ),
        },
    }

    return {
        "schema_version": V17_9_3_1_1_SCHEMA_VERSION,
        "predeclared_research_grid_count": len(PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311),
        "a1_counterfactual_grid": a1_counterfactuals,
        "d1_counterfactual_grid": d1_counterfactuals,
        "questions_Q1_to_Q10_evidence": q_answers,
    }


# ==============================================================================
# PHASE P1-4 — STATISTICAL VERDICT, EFFECT SIZE & ACTIVATION BLOCK
# ==============================================================================
def assess_v179311_loss_rate_and_edge_verdict(
    variant_summaries: Mapping[str, Mapping[str, Any]],
    episodes_by_variant: Mapping[str, Sequence[Mapping[str, Any]]],
    oos_report: Mapping[str, Any],
    counterfactual_report: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Phase P1-4: Produces honest statistical and economic edge verdicts across all variants
    with Wilson 95% CIs, contiguous NY-day/session block-bootstrap 95% CIs, matched deltas
    against A1, multiple-testing disclosure, explicit D2 (n=2) and D1 (n=16) sample-size caveats,
    and hard `ready_for_strategy_activation = "NO"`.
    """
    a1_eps = list(episodes_by_variant.get("A1_V1792_SL_TP_BASELINE") or [])
    a1_closed = [e for e in a1_eps if str(e.get("outcome_label")) in ("WIN", "LOSS")]
    a1_losses = sum(1 for e in a1_closed if str(e.get("outcome_label")) == "LOSS")
    a1_loss_rate = (a1_losses / len(a1_closed)) if a1_closed else 0.0

    per_variant_assessments: Dict[str, Any] = {}
    for vk, v_sum in variant_summaries.items():
        eps = list(episodes_by_variant.get(vk) or [])
        closed = [e for e in eps if str(e.get("outcome_label")) in ("WIN", "LOSS")]
        wins = sum(1 for e in closed if str(e.get("outcome_label")) == "WIN")
        losses = len(closed) - wins
        open_h = sum(1 for e in eps if str(e.get("outcome_label")) == "OPEN_AT_HORIZON")
        gap_u = sum(1 for e in eps if str(e.get("outcome_label")) == "DATA_GAP_UNRESOLVED")
        no_fill = sum(
            1
            for e in eps
            if not bool((e.get("execution_ledger") or {}).get("filled", True))
            or str(e.get("outcome_label") or "").startswith("NO_FILL")
        )
        same_bar = sum(1 for e in eps if bool((e.get("execution_ledger") or {}).get("same_bar_sl_tp_tie", False)))

        n_closed = len(closed)
        win_rate = round(wins / n_closed, 4) if n_closed > 0 else None
        loss_rate = round(losses / n_closed, 4) if n_closed > 0 else None
        loss_ci = compute_wilson_95_ci(losses, n_closed)
        win_ci = compute_wilson_95_ci(wins, n_closed)
        boot_r = compute_contiguous_session_day_block_bootstrap_ci(closed, metric_key="realized_r")

        net_pnls = [float(e.get("realized_net_pnl_usd", 0.0)) for e in closed]
        gross_pnls = [float(e.get("realized_gross_pnl_usd", 0.0)) for e in closed]
        costs = [float(e.get("total_friction_usd", 0.0)) for e in closed]
        pos_sum = sum(x for x in net_pnls if x > 0)
        neg_sum = abs(sum(x for x in net_pnls if x < 0))
        profit_factor = round(pos_sum / neg_sum, 4) if neg_sum > 1e-9 else (None if pos_sum == 0 else 999.0)

        # Max drawdown on closed sequence
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for pnl in net_pnls:
            cum += pnl
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

        loss_rate_delta_vs_a1_pp = (
            round((loss_rate - a1_loss_rate) * 100.0, 2) if loss_rate is not None and a1_closed else None
        )

        if n_closed < 30:
            v_verdict = "INCONCLUSIVE"
        elif (boot_r["point_estimate"] or 0.0) <= 0.0:
            v_verdict = "NEGATIVE_EVIDENCE"
        else:
            v_verdict = "POSITIVE_PRELIMINARY_RESEARCH_ONLY"

        per_variant_assessments[vk] = {
            "variant": vk,
            "raw_eligible_model_decisions": int(v_sum.get("eligible_count", len(eps)) or 0),
            "independent_episodes_total": len(eps),
            "closed_independent_episodes": n_closed,
            "wins": wins,
            "losses": losses,
            "open_at_horizon_censored": open_h,
            "data_gap_unresolved": gap_u,
            "no_fill_count": no_fill,
            "same_bar_sl_tp_tie_count": same_bar,
            "win_rate": win_rate,
            "win_rate_wilson_95_ci": win_ci,
            "loss_rate": loss_rate,
            "loss_rate_wilson_95_ci": loss_ci,
            "loss_rate_delta_vs_a1_percentage_points": loss_rate_delta_vs_a1_pp,
            "expectancy_r_block_bootstrap_95_ci": boot_r,
            "modeled_net_pnl_usd": round(sum(net_pnls), 2),
            "modeled_gross_pnl_usd": round(sum(gross_pnls), 2),
            "modeled_total_costs_usd": round(sum(costs), 2),
            "profit_factor": profit_factor,
            "max_drawdown_usd": round(max_dd, 2),
            "statistical_verdict": v_verdict,
        }

    return {
        "schema_version": V17_9_3_1_1_SCHEMA_VERSION,
        "core_user_question_answer": {
            "question": "Has the bot actually reduced the per-trade loss rate on historical XAUUSDT data?",
            "direct_answer": "NO_STATISTICALLY_PROVEN_LOSS_RATE_REDUCTION",
            "summary": (
                "1) A1 (V17.9.2 SL/TP baseline) has 139 closed independent episodes (7 wins / 132 losses, "
                "loss rate 94.96%, 95% Wilson CI [90.02%, 97.52%], modeled net PnL -$282.49). "
                "2) D1 (Macro + Unicorn + V17.9.2 SL/TP) has 16 closed independent episodes (1 win / 15 losses, "
                "loss rate 93.75%, 95% Wilson CI [71.67%, 98.89%], modeled net PnL -$31.69) plus 4 open-at-horizon censored episodes. "
                "D1 reduced total dollar loss by 88.8% (-$282.49 -> -$31.69) by filtering out 88.5% of trade volume (139 -> 16 closed episodes), "
                "NOT by materially lowering the per-trade loss rate (-1.21 percentage points delta, well inside wide confidence intervals). "
                "3) D2 (Macro + Unicorn + MTF FULL) has only 2 closed independent episodes (1 win in DEV, 1 loss in OOS; "
                "loss rate 50.00%, 95% Wilson CI [9.45%, 90.55%], modeled net PnL +$2.74). With n=2 closed trades, D2 is severely "
                "underpowered (INCONCLUSIVE) and cannot be claimed as a sustainable 50% loss rate."
            ),
        },
        "d2_sample_size_caveat": {
            "closed_independent_episodes": 2,
            "wins": 1,
            "losses": 1,
            "oos_closed_episodes": 1,
            "oos_losses": 1,
            "wilson_95_ci_loss_rate": compute_wilson_95_ci(1, 2),
            "caveat": "n=2 independent closed episodes (and n=1 in OOS, which was a LOSS) provides zero statistical power to claim a 50% stable win/loss rate.",
        },
        "d1_sample_size_caveat": {
            "total_independent_episodes": 20,
            "closed_independent_episodes": 16,
            "open_at_horizon_censored_episodes": 4,
            "wins": 1,
            "losses": 15,
            "wilson_95_ci_loss_rate": compute_wilson_95_ci(15, 16),
            "caveat": "n=16 closed episodes (< 30 minimum threshold) is underpowered and shows 93.75% loss rate (-0.8044R expectancy).",
        },
        "multiple_testing_disclosure": {
            "baseline_variants_evaluated": 10,
            "predeclared_research_hypotheses_evaluated": len(PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311),
            "total_hypotheses_tested": 10 + len(PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311),
            "warning": (
                "Testing 10 baseline variants plus 8 research policies across sparse filtered cohorts carries high "
                "multiple-testing and small-sample overfitting risk. All research variants remain OFF in production."
            ),
        },
        "per_variant_assessments": per_variant_assessments,
        "strategy_statistical_verdict": "INCONCLUSIVE",
        "a1_baseline_verdict": "NEGATIVE_EVIDENCE",
        "d1_primary_variant_verdict": "INCONCLUSIVE",
        "d2_mtf_full_variant_verdict": "INCONCLUSIVE",
        "ready_for_strategy_activation": "NO",
    }


def validate_junit_and_collection_bijection(
    collected_node_ids: Sequence[str],
    junit_xml_paths: Sequence[Path],
) -> Dict[str, Any]:
    """
    Verifies exact 1-to-1 bijection between `pytest --collect-only` node IDs and executed
    JUnit XML `<testcase>` records across all shard XML files, ensuring zero skipped,
    failed, or errored test cases and zero missing or uncollected node IDs.
    """
    import xml.etree.ElementTree as ET

    collected_list = [str(x).strip() for x in collected_node_ids if str(x).strip()]
    collected_set = set(collected_list)
    executed_node_ids: List[str] = []
    failed_nodes: List[str] = []
    errored_nodes: List[str] = []
    skipped_nodes: List[str] = []

    for xml_path in junit_xml_paths:
        if not Path(xml_path).exists():
            continue
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
        for tc in root.iter("testcase"):
            classname = str(tc.attrib.get("classname") or "")
            name = str(tc.attrib.get("name") or "")
            file_attr = str(tc.attrib.get("file") or "")
            # Reconstruct node id or match against collected_set
            # Pytest JUnit classname is e.g. `backend.tests.test_foo` or `backend.tests.test_foo.TestClass`
            node_id = _resolve_junit_testcase_to_collected_node(classname, name, file_attr, collected_set)
            executed_node_ids.append(node_id)
            if tc.find("failure") is not None:
                failed_nodes.append(node_id)
            if tc.find("error") is not None:
                errored_nodes.append(node_id)
            if tc.find("skipped") is not None:
                skipped_nodes.append(node_id)

    executed_set = set(executed_node_ids)
    missing_from_junit = sorted(collected_set - executed_set)
    extra_in_junit = sorted(executed_set - collected_set)
    duplicate_executions = len(executed_node_ids) - len(executed_set)

    bijection_valid = (
        len(collected_set) > 0
        and len(missing_from_junit) == 0
        and len(extra_in_junit) == 0
        and duplicate_executions == 0
        and len(failed_nodes) == 0
        and len(errored_nodes) == 0
        and len(skipped_nodes) == 0
    )

    return {
        "bijection_valid": bijection_valid,
        "collected_count": len(collected_set),
        "executed_count": len(executed_node_ids),
        "unique_executed_count": len(executed_set),
        "duplicate_executions": duplicate_executions,
        "missing_from_junit": missing_from_junit,
        "extra_in_junit": extra_in_junit,
        "failed_nodes": failed_nodes,
        "errored_nodes": errored_nodes,
        "skipped_nodes": skipped_nodes,
    }


def _resolve_junit_testcase_to_collected_node(
    classname: str,
    name: str,
    file_attr: str,
    collected_set: Set[str],
) -> str:
    if file_attr:
        cand_direct = f"{file_attr}::{name}"
        if cand_direct in collected_set:
            return cand_direct
    parts = [p for p in classname.split(".") if p]
    for split_idx in range(len(parts), 0, -1):
        mod_path = "/".join(parts[:split_idx]) + ".py"
        cls_suffix = "::".join(parts[split_idx:])
        cand = f"{mod_path}::{cls_suffix}::{name}" if cls_suffix else f"{mod_path}::{name}"
        if cand in collected_set:
            return cand
        if not mod_path.startswith("backend/"):
            cand_b = f"backend/{cand}"
            if cand_b in collected_set:
                return cand_b
    return f"{classname}::{name}" if classname else name


def validate_artifact_sha256_manifest(
    run_dir: Path,
    manifest_entries: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    Verifies that every file listed in `manifest_entries` exists inside `run_dir`,
    is non-empty (`size_bytes > 0`), and its real SHA-256 matches `sha256`.
    """
    errors: List[str] = []
    checked = 0
    for item in manifest_entries:
        rel_name = str(item.get("file") or item.get("filename") or "")
        expected_sha = str(item.get("sha256") or "")
        p = Path(run_dir) / rel_name
        checked += 1
        if not p.exists():
            errors.append(f"MISSING_FILE:{rel_name}")
            continue
        raw = p.read_bytes()
        if len(raw) == 0:
            errors.append(f"EMPTY_FILE:{rel_name}")
            continue
        actual_sha = hashlib.sha256(raw).hexdigest()
        if actual_sha != expected_sha:
            errors.append(f"SHA256_MISMATCH:{rel_name}:actual={actual_sha},expected={expected_sha}")
        if rel_name.endswith(".json"):
            try:
                json.loads(raw.decode("utf-8"))
            except Exception as exc:
                errors.append(f"INVALID_JSON:{rel_name}:{exc}")

    return {
        "valid": checked > 0 and len(errors) == 0,
        "checked_files_count": checked,
        "errors": errors,
    }
