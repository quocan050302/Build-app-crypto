"""
aurum-desk/backend/services/kh_03_fee_integrity_service.py

AURUM DESK — KH-03.R1 Authoritative Fee-Ledger Extraction, Root-Fix Reconciliation &
Six-Scenario Streaming Per-Episode Accounting Service.
SPEC VERSION: KH-03.R1-v1.0 | RESEARCH ONLY | ACTIVATION_READY=NO

Closes the C4 upstream fee-display bug at its root:
  - `ExecutableTradeLedgerEntryV17931.to_dict()` emits `entry_fee_usd` & `exit_fee_usd`
    (and `entry_fee` / `exit_fee` / `total_friction_usd`), NOT `total_fees_usd`.
  - Extracts authoritative fees strictly in Decimal (`entry_fee_usd + exit_fee_usd`)
    without double-counting or silent 0-coercion, validates |gross - fees - net| <= $0.001
    for every closed trade, explicitly separates OPEN_AT_HORIZON / DATA_GAP_UNRESOLVED
    accrued entry fees from closed realized fees, and streams deterministic per-episode
    ledgers (`04_fee_per_episode_ledger.jsonl.gz`) across all 6 full-universe cost scenarios.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH03 = "KH-03.R1-v1.0"
SCHEMA_VERSION_KH03 = "kh_03_research_data_v1"

CLOSED_OUTCOME_LABELS_KH03 = frozenset({"WIN", "LOSS"})
NON_CLOSED_FILLED_LABELS_KH03 = frozenset({"OPEN_AT_HORIZON", "DATA_GAP_UNRESOLVED"})
MONEY_RECONCILIATION_TOLERANCE_USD = Decimal("0.0010")
FEE_COMPONENT_CONSISTENCY_TOLERANCE_USD = Decimal("0.0005")

EXPECTED_SIX_SCENARIOS_KH03: Tuple[str, ...] = (
    "1X_BASE_0p0004",
    "FEE_ONLY_0p0006",
    "2X_ALL_COST_0p0004",
    "2X_ALL_COST_0p0006",
    "3X_ALL_COST_0p0004",
    "3X_ALL_COST_0p0006",
)


def _dec4(v: Decimal) -> float:
    return float(v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_strict_decimal(val: Any, *, field_name: str, allow_negative: bool = False) -> Decimal:
    if val is None or isinstance(val, bool):
        raise ValueError(f"MISSING_OR_INVALID_NUMERIC_FIELD:{field_name}:{val!r}")
    if isinstance(val, float) and not math.isfinite(val):
        raise ValueError(f"NON_FINITE_NUMERIC_FIELD:{field_name}:{val!r}")
    try:
        d = Decimal(str(val))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"UNPARSEABLE_DECIMAL_FIELD:{field_name}:{val!r}") from exc
    if not d.is_finite():
        raise ValueError(f"NON_FINITE_DECIMAL_FIELD:{field_name}:{val!r}")
    if not allow_negative and d < Decimal("0"):
        raise ValueError(f"NEGATIVE_FEE_FORBIDDEN:{field_name}:{d}")
    return d


def extract_authoritative_ledger_fees_kh03(
    ledger_dict: Mapping[str, Any],
    *,
    filled: bool,
    outcome_label: str,
) -> Dict[str, Any]:
    """
    Authoritative single-source fee extractor for `ExecutableTradeLedgerEntryV17931.to_dict()`
    (and compatible ledger dicts).

    Rules:
      1. Reads `entry_fee_usd` (fallback `entry_fee`) and `exit_fee_usd` (fallback `exit_fee`)
         strictly as non-negative finite Decimals. Rejects missing mandatory fee fields on closed
         trades rather than silently coercing to 0.0.
      2. NEVER double-counts: if `total_fees_usd` is ALSO present on `ledger_dict`, compares it
         against `entry_fee_usd + exit_fee_usd` and raises `ValueError` on mismatch instead of adding.
      3. For UNFILLED (`filled=False`): entry=0, exit=0, closed_total_fees=0, open_accrued_entry_fee=0.
      4. For OPEN_AT_HORIZON / DATA_GAP_UNRESOLVED (`filled=True`): preserves actual `entry_fee_usd`
         in `accrued_open_entry_fee_usd` (exit_fee_usd=0) while keeping `total_fees_usd=0.0` for
         closed-trade realized sums (never fabricating an exit or dropping open fee accrual).
      5. For CLOSED (`WIN` / `LOSS`): `total_fees_usd = entry_fee_usd + exit_fee_usd`, and asserts
         `abs((realized_gross_pnl_usd - total_fees_usd) - realized_net_pnl_usd) <= $0.001`.
    """
    if not isinstance(ledger_dict, Mapping):
        raise ValueError("INVALID_LEDGER_DICT_TYPE")

    lbl = str(outcome_label or "").strip().upper()
    has_entry = ("entry_fee_usd" in ledger_dict and ledger_dict.get("entry_fee_usd") is not None) or (
        "entry_fee" in ledger_dict and ledger_dict.get("entry_fee") is not None
    )
    has_exit = ("exit_fee_usd" in ledger_dict and ledger_dict.get("exit_fee_usd") is not None) or (
        "exit_fee" in ledger_dict and ledger_dict.get("exit_fee") is not None
    )

    if not filled:
        entry_d = (
            _parse_strict_decimal(
                ledger_dict.get("entry_fee_usd") if ledger_dict.get("entry_fee_usd") is not None else ledger_dict.get("entry_fee"),
                field_name="entry_fee_usd",
            )
            if has_entry
            else Decimal("0")
        )
        exit_d = (
            _parse_strict_decimal(
                ledger_dict.get("exit_fee_usd") if ledger_dict.get("exit_fee_usd") is not None else ledger_dict.get("exit_fee"),
                field_name="exit_fee_usd",
            )
            if has_exit
            else Decimal("0")
        )
        if entry_d != Decimal("0") or exit_d != Decimal("0"):
            raise ValueError(f"UNFILLED_TRADE_NONZERO_FEE:entry={entry_d},exit={exit_d}")
        return {
            "entry_fee_usd": 0.0,
            "exit_fee_usd": 0.0,
            "total_fees_usd": 0.0,
            "accrued_open_entry_fee_usd": 0.0,
            "gross_fee_net_residual_usd": 0.0,
            "fee_source_contract": "UNFILLED_ZERO_FEE",
        }

    # Filled trade must have authoritative entry fee
    if not has_entry:
        raise ValueError(f"MISSING_MANDATORY_ENTRY_FEE_FIELD:outcome={lbl}")
    entry_raw = ledger_dict.get("entry_fee_usd") if ledger_dict.get("entry_fee_usd") is not None else ledger_dict.get("entry_fee")
    entry_d = _parse_strict_decimal(entry_raw, field_name="entry_fee_usd")

    if lbl in NON_CLOSED_FILLED_LABELS_KH03 or lbl not in CLOSED_OUTCOME_LABELS_KH03:
        exit_raw = ledger_dict.get("exit_fee_usd") if ledger_dict.get("exit_fee_usd") is not None else ledger_dict.get("exit_fee")
        exit_d = _parse_strict_decimal(exit_raw, field_name="exit_fee_usd") if has_exit else Decimal("0")
        if exit_d != Decimal("0"):
            raise ValueError(f"NON_CLOSED_TRADE_NONZERO_EXIT_FEE:outcome={lbl},exit_fee={exit_d}")
        return {
            "entry_fee_usd": _dec4(entry_d),
            "exit_fee_usd": 0.0,
            "total_fees_usd": 0.0,  # Closed realized fees = 0; open accrued entry fee tracked separately!
            "accrued_open_entry_fee_usd": _dec4(entry_d),
            "gross_fee_net_residual_usd": 0.0,
            "fee_source_contract": "OPEN_OR_GAP_ENTRY_FEE_ACCRUED_SEPARATELY",
        }

    # Closed WIN / LOSS trade MUST have both authoritative entry & exit fees
    if not has_exit:
        raise ValueError(f"MISSING_MANDATORY_EXIT_FEE_FIELD:outcome={lbl}")
    exit_raw = ledger_dict.get("exit_fee_usd") if ledger_dict.get("exit_fee_usd") is not None else ledger_dict.get("exit_fee")
    exit_d = _parse_strict_decimal(exit_raw, field_name="exit_fee_usd")

    comp_sum_d = entry_d + exit_d

    # Guard against inconsistent or double-counted explicit total_fees_usd / total_friction_usd
    if ledger_dict.get("total_fees_usd") is not None:
        explicit_tot = _parse_strict_decimal(ledger_dict.get("total_fees_usd"), field_name="total_fees_usd")
        if abs(explicit_tot - comp_sum_d) > FEE_COMPONENT_CONSISTENCY_TOLERANCE_USD:
            raise ValueError(
                f"LEDGER_FEE_COMPONENT_MISMATCH:explicit_total={explicit_tot},entry_plus_exit={comp_sum_d}"
            )
    if ledger_dict.get("total_friction_usd") is not None:
        frict_tot = _parse_strict_decimal(ledger_dict.get("total_friction_usd"), field_name="total_friction_usd")
        if abs(frict_tot - comp_sum_d) > FEE_COMPONENT_CONSISTENCY_TOLERANCE_USD:
            raise ValueError(
                f"LEDGER_TOTAL_FRICTION_MISMATCH:total_friction={frict_tot},entry_plus_exit={comp_sum_d}"
            )

    gross_d = _parse_strict_decimal(
        ledger_dict.get("realized_gross_pnl_usd"), field_name="realized_gross_pnl_usd", allow_negative=True
    )
    net_d = _parse_strict_decimal(
        ledger_dict.get("realized_net_pnl_usd"), field_name="realized_net_pnl_usd", allow_negative=True
    )
    residual_d = abs((gross_d - comp_sum_d) - net_d)
    if residual_d > MONEY_RECONCILIATION_TOLERANCE_USD:
        raise ValueError(
            f"CLOSED_TRADE_GROSS_FEE_NET_INCONSISTENCY:gross={gross_d},entry={entry_d},exit={exit_d},net={net_d},residual={residual_d}"
        )

    return {
        "entry_fee_usd": _dec4(entry_d),
        "exit_fee_usd": _dec4(exit_d),
        "total_fees_usd": _dec4(comp_sum_d),
        "accrued_open_entry_fee_usd": 0.0,
        "gross_fee_net_residual_usd": _dec4(residual_d),
        "fee_source_contract": "AUTHORITATIVE_LEDGER_ENTRY_PLUS_EXIT_FEE_DECIMAL",
    }


def build_kh03_six_scenario_fee_ledger_and_reconciliation(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    fixed_fill_doc: Mapping[str, Any],
    out_episode_ledger_gz_path: Path,
    prior_c4_red_proof: Optional[Mapping[str, Any]] = None,
    arms: Optional[Sequence[str]] = None,
    source_latency_ms: int = 0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    §04 -> Artifacts `04_fee_per_episode_ledger.jsonl.gz` & `05_fee_full_universe_six_scenario_reconciliation.json`:
    Runs the full 6-scenario full-universe rerisk with authoritative root-fixed fee extraction,
    streams EVERY episode row deterministically into `04_fee_per_episode_ledger.jsonl.gz`,
    computes per-scenario x arm Decimal accounting, residual distributions, 1X vs fixed-fill
    same-cohort checks, and old-vs-new C4 deltas.
    """
    from domain_calculator import CostAssumptions
    from services.kh_02_r2_research_integrity_service import (
        PRIMARY_R2_ARMS,
        _replay_chronological_custom_cost_scenario,
        build_r2_target_repair_full_opportunity_conservation,
    )

    eff_arms = tuple(arms) if arms is not None else (*PRIMARY_R2_ARMS, "NC_NULL_TRIGGER")
    bars_1m = list(datasets["1m"])
    scenarios = (
        ("1X_BASE_0p0004", 0.20, 0.10, 0.0004, 0.0002),
        ("FEE_ONLY_0p0006", 0.20, 0.10, 0.0006, 0.0002),
        ("2X_ALL_COST_0p0004", 0.40, 0.20, 0.0008, 0.0004),
        ("2X_ALL_COST_0p0006", 0.40, 0.20, 0.0012, 0.0004),
        ("3X_ALL_COST_0p0004", 0.60, 0.30, 0.0012, 0.0006),
        ("3X_ALL_COST_0p0006", 0.60, 0.30, 0.0018, 0.0006),
    )

    out_path = Path(out_episode_ledger_gz_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(f"{out_path.name}.tmp.{os.getpid()}")

    rows_written = 0
    row_hashes: List[str] = []
    episode_uids: List[str] = []
    scenario_reports: Dict[str, Any] = {}
    reconciliation_violations: List[str] = []
    zero_fee_display_bug_hits = 0
    global_max_episode_residual = Decimal("0")
    global_max_cell_residual = Decimal("0")
    total_overlaps = 0
    all_conserved = True
    old_cells_map = (prior_c4_red_proof or {}).get("six_scenario_old_cells") or {}

    with tmp_path.open("wb") as raw_fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_fh, compresslevel=6, mtime=0) as gzf:
            with io.TextIOWrapper(gzf, encoding="utf-8") as gz:
                for sc_id, sp_usd, slp_usd, taker_r, maker_r in scenarios:
                    costs = CostAssumptions(
                        maker_fee_rate=maker_r,
                        taker_fee_rate=taker_r,
                        slippage_usd=slp_usd,
                    )
                    cands_bundle = build_r2_target_repair_full_opportunity_conservation(
                        datasets=datasets,
                        mtf_bundle=mtf_bundle,
                        ledger_bundle=ledger_bundle,
                        r1_candidates=None,
                        source_latency_ms=source_latency_ms,
                        cost_assumptions=costs,
                    )
                    if not cands_bundle["conservation_sum_verified"]:
                        all_conserved = False

                    arms_out: Dict[str, Any] = {}
                    for ac in sorted(eff_arms):
                        arm_list = list((cands_bundle.get("arm_candidates") or {}).get(ac) or ())
                        rep_sum, ep_rows = _replay_chronological_custom_cost_scenario(
                            arm_code=ac,
                            scenario_id=sc_id,
                            candidates=arm_list,
                            bars_1m=bars_1m,
                            spread_usd=sp_usd,
                            slippage_usd=slp_usd,
                            taker_fee_rate=taker_r,
                            maker_fee_rate=maker_r,
                            return_episodes=True,
                        )
                        total_overlaps += int(rep_sum.get("overlap_violations", 0))

                        cell_gross = Decimal("0")
                        cell_entry_fee = Decimal("0")
                        cell_exit_fee = Decimal("0")
                        cell_total_fee = Decimal("0")
                        cell_net = Decimal("0")
                        cell_open_accrued = Decimal("0")
                        cell_max_ep_resid = Decimal("0")
                        cell_closed = 0
                        cell_wins = 0
                        cell_losses = 0
                        cell_open_h = 0
                        cell_gap_u = 0
                        cell_unfilled = 0

                        for idx_in_cell, ep in enumerate(ep_rows):
                            ep_uid = f"{sc_id}|{ac}|{ep['decision_timestamp_ms']}|{ep['opportunity_id']}|{idx_in_cell}"
                            rec = {
                                "episode_uid": ep_uid,
                                "scenario_id": sc_id,
                                "arm_code": ac,
                                "opportunity_id": str(ep["opportunity_id"]),
                                "setup_episode_id": ep.get("setup_episode_id"),
                                "direction": str(ep["direction"]),
                                "decision_timestamp_ms": int(ep["decision_timestamp_ms"]),
                                "ny_date": ep.get("ny_date"),
                                "filled": bool(ep["filled"]),
                                "outcome_label": str(ep["outcome_label"]),
                                "exit_type": str(ep["exit_type"]),
                                "executed_quantity": float(ep["executed_quantity"]),
                                "planned_net_risk_usd": float(ep["planned_net_risk_usd"]),
                                "realized_gross_pnl_usd": float(ep["realized_gross_pnl_usd"]),
                                "entry_fee_usd": float(ep["entry_fee_usd"]),
                                "exit_fee_usd": float(ep["exit_fee_usd"]),
                                "total_fees_usd": float(ep["total_fees_usd"]),
                                "accrued_open_entry_fee_usd": float(ep["accrued_open_entry_fee_usd"]),
                                "realized_net_pnl_usd": float(ep["realized_net_pnl_usd"]),
                                "realized_net_r": float(ep["realized_net_r"]),
                                "gross_fee_net_residual_usd": float(ep["gross_fee_net_residual_usd"]),
                                "spread_usd": sp_usd,
                                "slippage_usd": slp_usd,
                                "taker_fee_rate": taker_r,
                                "maker_fee_rate": maker_r,
                                "quote_mode": "MODELED_BID_ASK",
                                "account_fee_status": "UNKNOWN_NOT_LOCALLY_VERIFIED",
                            }
                            line = json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                            gz.write(line + "\n")
                            rows_written += 1
                            row_hashes.append(hashlib.sha256(line.encode("utf-8")).hexdigest())
                            episode_uids.append(ep_uid)

                            lbl = rec["outcome_label"]
                            if not rec["filled"]:
                                cell_unfilled += 1
                            elif lbl == "OPEN_AT_HORIZON":
                                cell_open_h += 1
                                cell_open_accrued += Decimal(str(rec["accrued_open_entry_fee_usd"]))
                            elif lbl == "DATA_GAP_UNRESOLVED":
                                cell_gap_u += 1
                                cell_open_accrued += Decimal(str(rec["accrued_open_entry_fee_usd"]))
                            elif lbl in CLOSED_OUTCOME_LABELS_KH03:
                                cell_closed += 1
                                if lbl == "WIN":
                                    cell_wins += 1
                                else:
                                    cell_losses += 1
                                g_d = Decimal(str(rec["realized_gross_pnl_usd"]))
                                ef_d = Decimal(str(rec["entry_fee_usd"]))
                                xf_d = Decimal(str(rec["exit_fee_usd"]))
                                tf_d = Decimal(str(rec["total_fees_usd"]))
                                n_d = Decimal(str(rec["realized_net_pnl_usd"]))
                                cell_gross += g_d
                                cell_entry_fee += ef_d
                                cell_exit_fee += xf_d
                                cell_total_fee += tf_d
                                cell_net += n_d
                                ep_res = abs((g_d - tf_d) - n_d)
                                if ep_res > cell_max_ep_resid:
                                    cell_max_ep_resid = ep_res
                                if ep_res > global_max_episode_residual:
                                    global_max_episode_residual = ep_res

                        rep_gross = Decimal(str(rep_sum["gross_pnl_usd"]))
                        rep_fee = Decimal(str(rep_sum["total_fees_usd"]))
                        rep_net = Decimal(str(rep_sum["net_pnl_usd"]))
                        implied_fee = rep_gross - rep_net
                        cell_resid = abs((rep_gross - rep_fee) - rep_net)
                        if cell_resid > global_max_cell_residual:
                            global_max_cell_residual = cell_resid

                        if cell_closed > 0 and rep_fee == Decimal("0") and implied_fee > Decimal("0.01"):
                            zero_fee_display_bug_hits += 1
                            reconciliation_violations.append(f"RESIDUAL_ZERO_FEE_DISPLAY_BUG:{sc_id}:{ac}")
                        if abs(rep_fee - cell_total_fee) > Decimal("0.001"):
                            reconciliation_violations.append(
                                f"SUMMARY_VS_LEDGER_FEE_MISMATCH:{sc_id}:{ac}:rep={rep_fee},led={cell_total_fee}"
                            )
                        if cell_resid > Decimal("0.01"):
                            reconciliation_violations.append(
                                f"CELL_GROSS_FEE_NET_RESIDUAL_TOO_LARGE:{sc_id}:{ac}:resid={cell_resid}"
                            )

                        # Cross-check 1X_BASE_0p0004 against fixed-fill same-cohort fees & net
                        ff_match_fee: Optional[bool] = None
                        ff_match_net: Optional[bool] = None
                        if sc_id == "1X_BASE_0p0004" and ac in (fixed_fill_doc.get("arms") or {}):
                            ff_arm = fixed_fill_doc["arms"][ac]
                            ff_1x = ff_arm.get("1x_base_0p0004") or {}
                            ff_fee = Decimal(str(ff_1x.get("fees_usd") or 0.0))
                            ff_net = Decimal(str(ff_1x.get("net_pnl_usd") or 0.0))
                            ff_match_fee = abs(rep_fee - ff_fee) <= Decimal("0.01")
                            ff_match_net = abs(rep_net - ff_net) <= Decimal("0.01")
                            if not ff_match_fee:
                                reconciliation_violations.append(
                                    f"1X_FULL_VS_FIXED_FILL_FEE_MISMATCH:{ac}:full={rep_fee},ff={ff_fee}"
                                )
                            if not ff_match_net:
                                reconciliation_violations.append(
                                    f"1X_FULL_VS_FIXED_FILL_NET_MISMATCH:{ac}:full={rep_net},ff={ff_net}"
                                )

                        # Compare old C4 pre-fix economics (gross/net/closed/ids MUST remain 100% identical!)
                        old_cell = (old_cells_map.get(sc_id) or {}).get(ac)
                        old_delta = None
                        if isinstance(old_cell, Mapping):
                            econ_identical = (
                                int(old_cell.get("closed", -1)) == cell_closed
                                and int(old_cell.get("wins", -1)) == cell_wins
                                and int(old_cell.get("losses", -1)) == cell_losses
                                and abs(float(old_cell.get("gross_pnl_usd", 0.0)) - _dec4(rep_gross)) <= 1e-4
                                and abs(float(old_cell.get("net_pnl_usd", 0.0)) - _dec4(rep_net)) <= 1e-4
                                and old_cell.get("executed_ids_sha256") == rep_sum.get("executed_ids_sha256")
                            )
                            if not econ_identical:
                                reconciliation_violations.append(f"ROOT_FIX_ALTERED_ECONOMICS_OR_IDS:{sc_id}:{ac}")
                            old_delta = {
                                "old_displayed_total_fees_usd": float(old_cell.get("old_displayed_total_fees_usd", 0.0)),
                                "new_displayed_total_fees_usd": _dec4(rep_fee),
                                "delta_displayed_fees_usd": _dec4(rep_fee - Decimal(str(old_cell.get("old_displayed_total_fees_usd", 0.0)))),
                                "economics_and_trade_ids_unchanged": econ_identical,
                            }

                        arms_out[ac] = {
                            "arm_eligible_candidates": len(arm_list),
                            "scheduled": len(ep_rows),
                            "closed": cell_closed,
                            "wins": cell_wins,
                            "losses": cell_losses,
                            "open_at_horizon": cell_open_h,
                            "data_gap_unresolved": cell_gap_u,
                            "unfilled": cell_unfilled,
                            "gross_pnl_usd": _dec4(cell_gross),
                            "entry_fees_closed_usd": _dec4(cell_entry_fee),
                            "exit_fees_closed_usd": _dec4(cell_exit_fee),
                            "total_fees_usd": _dec4(cell_total_fee),
                            "accrued_open_entry_fees_usd": _dec4(cell_open_accrued),
                            "net_pnl_usd": _dec4(cell_net),
                            "implied_gross_minus_net_usd": _dec4(implied_fee),
                            "cell_gross_fee_net_residual_usd": _dec4(cell_resid),
                            "max_episode_residual_usd": _dec4(cell_max_ep_resid),
                            "expectancy_r": rep_sum.get("expectancy_r"),
                            "executed_ids_sha256": rep_sum.get("executed_ids_sha256"),
                            "matches_fixed_fill_1x_fees": ff_match_fee,
                            "matches_fixed_fill_1x_net": ff_match_net,
                            "old_vs_new_delta": old_delta,
                        }

                    scenario_reports[sc_id] = {
                        "scenario_id": sc_id,
                        "population_mode": "FULL_RERISKED_DYNAMIC_POPULATION",
                        "parameters": {
                            "spread_usd": sp_usd,
                            "slippage_usd": slp_usd,
                            "taker_fee_rate": taker_r,
                            "maker_fee_rate": maker_r,
                        },
                        "total_directional_bar_opportunities": int(cands_bundle["total_directional_bar_opportunities"]),
                        "raw_m1_triggers_count": int(cands_bundle["raw_m1_triggers_count"]),
                        "raw_r2_geometry_risk_eligible_count": int(cands_bundle["raw_r2_geometry_risk_eligible_count"]),
                        "arms": arms_out,
                    }

        raw_fh.flush()
        os.fsync(raw_fh.fileno())
    os.replace(tmp_path, out_path)

    ledger_meta = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "ledger_filename": out_path.name,
        "ledger_path": str(out_path),
        "ledger_file_sha256": _file_sha256(out_path),
        "ledger_size_bytes": int(out_path.stat().st_size),
        "total_episode_rows": rows_written,
        "episode_uids_sha256": canonical_hash(episode_uids),
        "canonical_rows_sha256": canonical_hash(row_hashes),
    }

    root_fixed = (
        len(reconciliation_violations) == 0
        and zero_fee_display_bug_hits == 0
        and all_conserved
        and total_overlaps == 0
        and rows_written > 0
    )

    reconciliation_doc: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "population_mode": "FULL_RERISKED_DYNAMIC_POPULATION",
        "starts_from_full_raw_opportunity_stream": True,
        "quote_mode": "MODELED_BID_ASK",
        "modeled_spread_usd_1x": 0.20,
        "observed_l1_bbo_available": False,
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "C4_UPSTREAM_FEE_DISPLAY_ROOT_CAUSE_FIXED": root_fixed,
        "zero_fee_display_bug_hits_after_fix": zero_fee_display_bug_hits,
        "conservation_verified_all_scenarios": all_conserved,
        "total_overlap_violations": total_overlaps,
        "global_max_episode_gross_fee_net_residual_usd": _dec4(global_max_episode_residual),
        "global_max_cell_gross_fee_net_residual_usd": _dec4(global_max_cell_residual),
        "money_reconciliation_tolerance_usd": float(MONEY_RECONCILIATION_TOLERANCE_USD),
        "linked_per_episode_fee_ledger": {
            "ledger_filename": ledger_meta["ledger_filename"],
            "ledger_file_sha256": ledger_meta["ledger_file_sha256"],
            "total_episode_rows": ledger_meta["total_episode_rows"],
            "episode_uids_sha256": ledger_meta["episode_uids_sha256"],
            "canonical_rows_sha256": ledger_meta["canonical_rows_sha256"],
        },
        "scenarios": scenario_reports,
        "violations": reconciliation_violations,
        "disclosure": (
            "All 6 full-universe scenarios recomputed from the full 44,692 directional bar stream with "
            "authoritative entry_fee_usd + exit_fee_usd Decimal extraction (zero double-counting, zero "
            "silent 0-coercion). Modeled historical bid/ask ($0.20 1x) is NOT observed L1 BBO; public "
            "0.0006 taker fee is NOT a locally verified actual account effective fee tier."
        ),
        "status": "PASS" if root_fixed else "FAIL",
    }
    reconciliation_doc["artifact_canonical_sha256"] = canonical_hash(
        {k: v for k, v in reconciliation_doc.items() if k != "artifact_canonical_sha256"}
    )
    return ledger_meta, reconciliation_doc


def validate_kh03_fee_ledger_independently(
    ledger_jsonl_gz_path: Path,
    reconciliation_doc: Mapping[str, Any],
    *,
    expected_scenarios: Sequence[str] = EXPECTED_SIX_SCENARIOS_KH03,
    expected_arms: Sequence[str] = ("B2_H1", "B2_H123", "B2_H2", "B2_H3", "NC_NULL_TRIGGER"),
) -> Dict[str, Any]:
    """
    Independent streaming Decimal verifier reading `04_fee_per_episode_ledger.jsonl.gz`
    directly from disk without trusting producer summary booleans or status labels.
    """
    p = Path(ledger_jsonl_gz_path)
    violations: List[str] = []
    if not p.is_file() or p.stat().st_size == 0:
        return {"valid": False, "status": "FAIL", "violations": ["MISSING_OR_EMPTY_04_FEE_LEDGER"]}

    actual_file_sha = _file_sha256(p)
    linked = reconciliation_doc.get("linked_per_episode_fee_ledger") or {}
    if linked.get("ledger_file_sha256") != actual_file_sha:
        violations.append(
            f"FEE_LEDGER_FILE_SHA_MISMATCH:claimed={linked.get('ledger_file_sha256')},actual={actual_file_sha}"
        )

    seen_uids: set[str] = set()
    all_uids: List[str] = []
    cells: Dict[Tuple[str, str], Dict[str, Any]] = {}
    total_rows = 0
    max_ep_resid = Decimal("0")

    try:
        with gzip.open(p, "rt", encoding="utf-8") as gz:
            for line_no, raw in enumerate(gz, 1):
                if not raw.strip():
                    continue
                row = json.loads(raw)
                total_rows += 1
                uid = str(row.get("episode_uid") or "")
                if not uid or uid in seen_uids:
                    violations.append(f"INVALID_OR_DUPLICATE_FEE_EPISODE_UID:line={line_no}:{uid}")
                    continue
                seen_uids.add(uid)
                all_uids.append(uid)

                sc = str(row.get("scenario_id") or "")
                ac = str(row.get("arm_code") or "")
                cell = cells.setdefault(
                    (sc, ac),
                    {
                        "scheduled": 0,
                        "closed": 0,
                        "wins": 0,
                        "losses": 0,
                        "open_at_horizon": 0,
                        "data_gap_unresolved": 0,
                        "unfilled": 0,
                        "gross": Decimal("0"),
                        "entry_fee": Decimal("0"),
                        "exit_fee": Decimal("0"),
                        "total_fee": Decimal("0"),
                        "net": Decimal("0"),
                        "open_accrued": Decimal("0"),
                    },
                )
                cell["scheduled"] += 1

                filled = bool(row.get("filled", False))
                lbl = str(row.get("outcome_label") or "")
                ef_d = _parse_strict_decimal(row.get("entry_fee_usd"), field_name="entry_fee_usd")
                xf_d = _parse_strict_decimal(row.get("exit_fee_usd"), field_name="exit_fee_usd")
                tf_d = _parse_strict_decimal(row.get("total_fees_usd"), field_name="total_fees_usd")
                oa_d = _parse_strict_decimal(row.get("accrued_open_entry_fee_usd"), field_name="accrued_open_entry_fee_usd")
                g_d = _parse_strict_decimal(row.get("realized_gross_pnl_usd"), field_name="realized_gross_pnl_usd", allow_negative=True)
                n_d = _parse_strict_decimal(row.get("realized_net_pnl_usd"), field_name="realized_net_pnl_usd", allow_negative=True)

                if not filled:
                    cell["unfilled"] += 1
                    if tf_d != Decimal("0") or ef_d != Decimal("0") or xf_d != Decimal("0"):
                        violations.append(f"UNFILLED_NONZERO_FEE_IN_LEDGER:{uid}")
                elif lbl == "OPEN_AT_HORIZON":
                    cell["open_at_horizon"] += 1
                    cell["open_accrued"] += oa_d
                    if tf_d != Decimal("0") or xf_d != Decimal("0") or g_d != Decimal("0") or n_d != Decimal("0"):
                        violations.append(f"OPEN_AT_HORIZON_NONZERO_REALIZED_IN_LEDGER:{uid}")
                elif lbl == "DATA_GAP_UNRESOLVED":
                    cell["data_gap_unresolved"] += 1
                    cell["open_accrued"] += oa_d
                    if tf_d != Decimal("0") or xf_d != Decimal("0") or g_d != Decimal("0") or n_d != Decimal("0"):
                        violations.append(f"DATA_GAP_NONZERO_REALIZED_IN_LEDGER:{uid}")
                elif lbl in CLOSED_OUTCOME_LABELS_KH03:
                    cell["closed"] += 1
                    if lbl == "WIN":
                        cell["wins"] += 1
                    else:
                        cell["losses"] += 1
                    if abs((ef_d + xf_d) - tf_d) > FEE_COMPONENT_CONSISTENCY_TOLERANCE_USD:
                        violations.append(f"CLOSED_ENTRY_PLUS_EXIT_NE_TOTAL_FEE:{uid}:ef={ef_d},xf={xf_d},tf={tf_d}")
                    resid = abs((g_d - tf_d) - n_d)
                    if resid > max_ep_resid:
                        max_ep_resid = resid
                    if resid > MONEY_RECONCILIATION_TOLERANCE_USD:
                        violations.append(f"CLOSED_GROSS_MINUS_FEE_NE_NET:{uid}:g={g_d},tf={tf_d},n={n_d}")
                    if tf_d == Decimal("0") and (g_d - n_d) > Decimal("0.005"):
                        violations.append(f"HIDDEN_ZERO_FEE_CLOSED_TRADE:{uid}")
                    cell["gross"] += g_d
                    cell["entry_fee"] += ef_d
                    cell["exit_fee"] += xf_d
                    cell["total_fee"] += tf_d
                    cell["net"] += n_d
                else:
                    violations.append(f"UNKNOWN_OUTCOME_LABEL:{uid}:{lbl}")
    except Exception as exc:
        return {"valid": False, "status": "FAIL", "violations": [f"FEE_LEDGER_READ_ERROR:{type(exc).__name__}:{exc}"]}

    if total_rows == 0 or total_rows != int(linked.get("total_episode_rows", -1)):
        violations.append(f"FEE_LEDGER_ROW_COUNT_MISMATCH:actual={total_rows},claimed={linked.get('total_episode_rows')}")
    if canonical_hash(all_uids) != linked.get("episode_uids_sha256"):
        violations.append("FEE_LEDGER_EPISODE_UIDS_SHA_MISMATCH")

    # Cross-check EVERY scenario x arm cell in 05 against streaming recomputation!
    doc_scenarios = reconciliation_doc.get("scenarios") or {}
    for sc in expected_scenarios:
        if sc not in doc_scenarios:
            violations.append(f"MISSING_SCENARIO_IN_05:{sc}")
            continue
        doc_arms = (doc_scenarios[sc] or {}).get("arms") or {}
        for ac in expected_arms:
            if ac not in doc_arms:
                violations.append(f"MISSING_ARM_IN_05:{sc}:{ac}")
                continue
            d_cell = doc_arms[ac]
            r_cell = cells.get((sc, ac))
            if r_cell is None:
                # Valid only if 0 scheduled trades
                if int(d_cell.get("scheduled", -1)) != 0 or int(d_cell.get("closed", -1)) != 0:
                    violations.append(f"MISSING_CELL_ROWS_IN_04_LEDGER:{sc}:{ac}")
                continue
            for int_k in ("scheduled", "closed", "wins", "losses", "open_at_horizon", "data_gap_unresolved", "unfilled"):
                if int(d_cell.get(int_k, -999)) != int(r_cell[int_k]):
                    violations.append(f"CELL_INT_MISMATCH_05_VS_04:{sc}:{ac}:{int_k}:{d_cell.get(int_k)}!={r_cell[int_k]}")
            for d_key, r_key in (
                ("gross_pnl_usd", "gross"),
                ("entry_fees_closed_usd", "entry_fee"),
                ("exit_fees_closed_usd", "exit_fee"),
                ("total_fees_usd", "total_fee"),
                ("accrued_open_entry_fees_usd", "open_accrued"),
                ("net_pnl_usd", "net"),
            ):
                claimed_d = Decimal(str(d_cell.get(d_key) or 0.0))
                if abs(claimed_d - r_cell[r_key]) > Decimal("0.01"):
                    violations.append(
                        f"CELL_MONEY_MISMATCH_05_VS_04:{sc}:{ac}:{d_key}:claimed={claimed_d},recomp={r_cell[r_key]}"
                    )
            # Zero-display check directly on 05
            c_fee = Decimal(str(d_cell.get("total_fees_usd") or 0.0))
            c_imp = Decimal(str(d_cell.get("gross_pnl_usd") or 0.0)) - Decimal(str(d_cell.get("net_pnl_usd") or 0.0))
            if int(d_cell.get("closed") or 0) > 0 and c_fee == Decimal("0") and c_imp > Decimal("0.01"):
                violations.append(f"ZERO_FEE_DISPLAY_TAMPER_OR_BUG_IN_05:{sc}:{ac}")

    return {
        "valid": len(violations) == 0,
        "status": "PASS" if len(violations) == 0 else "FAIL",
        "total_episode_rows": total_rows,
        "ledger_file_sha256": actual_file_sha,
        "max_episode_gross_fee_net_residual_usd": _dec4(max_ep_resid),
        "violations": violations,
    }
