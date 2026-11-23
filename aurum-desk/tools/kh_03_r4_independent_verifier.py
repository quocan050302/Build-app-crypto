#!/usr/bin/env python3
"""
aurum-desk/tools/kh_03_r4_independent_verifier.py

AURUM DESK — KH-03.R4 | SEPARATE-OS INDEPENDENT DISK VERIFIER (G01..G30)
SPEC VERSION: KH-03.R4-v1.0 | ZERO ORCHESTRATOR IMPORT | INDEPENDENT RAW RECOMPUTATION

Runs in a separate OS process (`verifier_pid != orchestrator_pid`), reads ALL raw R4 artifacts
(`00`..`20`, `README_KH03_R4.md`, `HANDOVER_KH03_R4_VI.txt`, `logs/forensic_closed_open_gap_ledger_1x.jsonl.gz`,
`logs/runtime_memory_samples_0p5s.jsonl.gz`, `junit/*.xml`) directly from disk with strict
non-finite-rejecting JSON parsing, independently recomputes exact Decimal economics, lineage,
censoring/gap accounting, 6-scenario dynamic vs fixed-fill separation, E1/E2/E3/NULL comparability,
claim-inflation checks, DATA_READINESS_REGISTER completeness, JUnit 1:1 XML bijection, A1/SI8/adapter/D4
parity, W1/W2/W4/W1_repeat raw timings/SHAs/speedups, 0.5s resource telemetry, fault/mutation counts,
and ALL 30 orthogonal gates (`G01..G30`), then seals `21_independent_OS_verifier_manifest_sha256.json`.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple
import xml.etree.ElementTree as ET

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic
from tools.kh_03_r3_c1_independent_verifier import (
    PRIOR_KH03_R3_EVIDENCE_SHA,
    PRIOR_KH03_R3_PUB_HEAD_SHA,
    PRIOR_KH03_R3_RUN_ID,
    PRIOR_KH03_R3_TESTED_SHA,
    SEALED_BASELINE_PARITY_FINGERPRINTS,
)
from tools.kh_03_r3_c2_independent_verifier import (
    PRIOR_KH03_R3_C1_EVIDENCE_SHA,
    PRIOR_KH03_R3_C1_PUB_HEAD_SHA,
    PRIOR_KH03_R3_C1_RUN_ID,
    PRIOR_KH03_R3_C1_TESTED_SHA,
    PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C2,
)

SPEC_VERSION_KH03_R4 = "KH-03.R4-v1.0"
SCHEMA_VERSION_KH03_R4 = "kh_03_r4_economic_feasibility_v1"
MANDATORY_BRANCH_KH03_R4 = "feature/v17-9-2-structural-sl-tp-from-qa"
CANONICAL_DATASET_SHA256_KH03_R4 = CANONICAL_DATASET_SHA256_KH02
STRICT_PREFLIGHT_DISK_MIN_GIB_R4 = 20.5
STRICT_RUNTIME_DISK_MIN_GIB_R4 = 20.0
HARD_RAM_FLOOR_MIB_R4 = 512.0
GLOBAL_MAX_DESCENDANTS_R4 = 4
MIN_EXPECTED_PYTEST_COLLECTED_R4 = 5805
MANIFEST_21_NAME_KH03_R4 = "21_independent_OS_verifier_manifest_sha256.json"
Q4 = Decimal("0.0001")

# Prior KH-03.R3.C2 sealed references (verified from live HEAD 2eb271f624504b5cb8617eb2039d2dccd9ea46de)
PRIOR_KH03_R3_C2_TESTED_SHA = "06c02e626b9993de6d21d906e2340b8a22ed37b3"
PRIOR_KH03_R3_C2_EVIDENCE_SHA = "fdd82caa6ae73b95e0082cd9b06778e30e5095e5"
PRIOR_KH03_R3_C2_PUB_HEAD_SHA = "2eb271f624504b5cb8617eb2039d2dccd9ea46de"
PRIOR_KH03_R3_C2_RUN_ID = "20261123T133101Z_06c02e626b99"

PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R4: Tuple[Tuple[str, str, str], ...] = (
    *PRIOR_SEALED_POINTERS_AND_ARTIFACTS_KH03_R3_C2,
    (
        "aurum-desk/docs/artifacts/kh_03_r3_c2_legacy_fail_closed/latest_run.json",
        "d61a1ad520cc6fccfd04673604d59e6a5c60a8b9940dedcd72fdc35d64f7d431",
        "KH03_R3_C2_LATEST_RUN_POINTER",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c2_legacy_fail_closed/{PRIOR_KH03_R3_C2_RUN_ID}/16_independent_OS_verifier_manifest_sha256.json",
        "37a40f5d2c9df87734c40866ed04dffc0bcdea06fe8522f090a0a33323a2eb60",
        "KH03_R3_C2_SEALED_MANIFEST_16",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c2_legacy_fail_closed/19_phase2_evidence_publish_remote_https_readback_{PRIOR_KH03_R3_C2_RUN_ID}.json",
        "fd89dc9eddb6b841e5c0e560db3afbfe8ed364c54d5a2d59d5209b8f2d43c47f",
        "KH03_R3_C2_SEALED_POSTPUSH_READBACK_19",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c2_legacy_fail_closed/{PRIOR_KH03_R3_C2_RUN_ID}/09_full_repo_pytest_junit_1to1.json",
        "d5a9131f87e6cb920e57adea27370ba9479fc8ded9e5d3159faf2ccbb12d5f7a",
        "KH03_R3_C2_SEALED_JUNIT_09",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c2_legacy_fail_closed/{PRIOR_KH03_R3_C2_RUN_ID}/10_full_A1_SI8_adapter_D4_parity.json",
        "6fd145bb61742a1ffc48772b269f37bf4563b86b47ef35cd18a951d7bd7c8c86",
        "KH03_R3_C2_SEALED_PARITY_10",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c2_legacy_fail_closed/{PRIOR_KH03_R3_C2_RUN_ID}/11_six_cost_e1_e2_e3_null_regression.json",
        "2664f12a8a7e4b49e50fc58c21bc2ffabb7e3efbbe5d68768b9a63af934f34c0",
        "KH03_R3_C2_SEALED_ECONOMIC_11",
    ),
    (
        f"aurum-desk/docs/artifacts/kh_03_r3_c2_legacy_fail_closed/{PRIOR_KH03_R3_C2_RUN_ID}/17_orthogonal_gate_matrix_and_verdicts.json",
        "0b1a13b9a95c8d81a1c7385c7c5028e51b03490e168687059611d7fbf8758ee4",
        "KH03_R3_C2_SEALED_GATES_17",
    ),
)

KH03_R4_SOURCE_FILES_ALLOWLIST: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_03_r4_economic_diagnostic_service.py",
    "aurum-desk/tools/kh_03_r4_independent_verifier.py",
    "aurum-desk/tools/run_kh_03_r4_acceptance.py",
    "aurum-desk/backend/tests/test_kh03_r4_economic_accounting.py",
    "aurum-desk/backend/tests/test_kh03_r4_population_and_censoring.py",
    "aurum-desk/backend/tests/test_kh03_r4_no_alpha_no_causal_claims.py",
    "aurum-desk/backend/tests/test_kh03_r4_resource_and_async.py",
    "aurum-desk/backend/tests/test_kh03_r4_independent_verifier_mutations.py",
)

REQUIRED_ARTIFACTS_KH03_R4: Tuple[str, ...] = (
    "00_branch_head_clean_sha_preflight_resource_lock.json",
    "01_ownership_keep_as_is_locked_sha_manifest.json",
    "02_prior_r1_r2_r3_c1_c2_dataset_hash_integrity.json",
    "03_raw_population_lineage_and_denominator_conservation.json",
    "04_closed_trade_decimal_gross_fee_net_bridge.json",
    "05_entry_exit_stop_target_risk_and_tail_diagnostic.json",
    "06_censoring_open_gap_unfilled_and_effective_n.json",
    "07_fee_only_fixed_fills_non_executable_counterfactual.json",
    "08_full_dynamic_rerisk_6_cost_scenario_replay.json",
    "09_E1_E2_E3_null_negative_controls_comparability.json",
    "10_assoc_not_causal_and_no_alpha_claim_proof.json",
    "11_data_readiness_register_and_missing_evidence.json",
    "12_go_nogo_decision_rules_and_owner_next_actions.json",
    "13_full_pytest_junit_fresh_collection_bijection.json",
    "14_A1_SI8_adapter_D4_production_baseline_parity.json",
    "15_W1_W2_W4_W1repeat_determinism_real_timings.json",
    "16_disk_ram_PID_0p5s_log_and_cleanup.json",
    "17_inherited_plus_new_fault_red_green_receipts.json",
    "18_separate_OS_verifier_mutation_attack_results.json",
    "19_orthogonal_30_gates_and_verdict_matrix.json",
    "20_phase1_code_commit_push_receipt.json",
    "README_KH03_R4.md",
    "HANDOVER_KH03_R4_VI.txt",
)

GATE_30_KEYS_KH03_R4: Tuple[str, ...] = (
    "G01_BRANCH_HEAD_CLEAN_SHA",
    "G02_IMMUTABLE_DATASET_AND_PRIOR_SEALED_SHA",
    "G03_PRODUCTION_AST_KEEP_AS_IS",
    "G04_OPPORTUNITY_ID_CONSERVATION",
    "G05_CLOSED_FILLED_SCHEDULED_DENOMINATORS",
    "G06_CENSOR_GAP_STATUS",
    "G07_DECIMAL_GROSS_FEE_NET_1X",
    "G08_OPEN_FEE_SEPARATION",
    "G09_SIX_COST_SCENARIO_ACCOUNTING",
    "G10_FIXED_FILL_VS_DYNAMIC_SEPARATED",
    "G11_E1_E2_E3_NO_POPULATION_CONFLATION",
    "G12_REALIZED_GT_PLANNED_RISK_DISCLOSED",
    "G13_ASSOCIATION_VS_CAUSAL_LABELS",
    "G14_NEGATIVE_CONTROL_AND_PLACEBO",
    "G15_R2_FORWARD_ONLY_DEV_EMBARGO",
    "G16_NO_LOOKAHEAD_FUTURE_MUTATION",
    "G17_DATA_READINESS_REGISTER_TRUE_UNKNOWN",
    "G18_NO_FABRICATED_ACCOUNT_FEES_L1_FUNDING",
    "G19_ZERO_FRESH_OOS_AND_NO_NEW_ALPHA",
    "G20_NO_MARKET_ORDER_ALL_ACTIVATION_OFF",
    "G21_FULL_REPO_JUNIT_EXACT_1_TO_1",
    "G22_A1_SI8_ADAPTER_D4_PARITY",
    "G23_ASYNC_SHARD_W1_W2_W4_SEMANTIC_SHA",
    "G24_MEASURED_SPEEDUP_AND_SAFE_WORKER_CAP",
    "G25_DISK_RAM_TEMP_ORPHAN_LOCK",
    "G26_INHERITED_AND_NEW_FAULT_CONTROLS",
    "G27_INDEPENDENT_VERIFIER_MUTATIONS",
    "G28_DISTINCT_PID_INDEPENDENT_VERIFIER",
    "G29_ECONOMIC_VERDICT_NO_GO_EVIDENCE_CONSISTENT",
    "G30_TWO_PHASE_GIT_REMOTE_VERIFIED_PUBLICATION",
)

ANCHOR_1X_DEC_R4: Dict[str, Dict[str, Any]] = {
    "NC_NULL_TRIGGER": {"closed": 203, "wins": 7, "losses": 196, "open": 28, "gap": 2, "gross": Decimal("-164.9541"), "fees": Decimal("276.6577"), "net": Decimal("-441.6118"), "exp": Decimal("-0.8806"), "open28_fee": Decimal("14.7151")},
    "KH03_R3_E1_LOW_FRICTION_STOP_FLOOR": {"closed": 58, "wins": 3, "losses": 55, "net": Decimal("-118.0121"), "exp": Decimal("-0.8286")},
    "KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION": {"closed": 131, "wins": 5, "losses": 126, "net": Decimal("-274.3771"), "exp": Decimal("-0.8498")},
    "KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT": {"closed": 206, "wins": 9, "losses": 197, "net": Decimal("-439.0423"), "exp": Decimal("-0.8628")},
}

DATA_REGISTER_REQUIRED_15_FIELDS: Tuple[str, ...] = (
    "name", "why_required", "currently_observed", "source_existing_path", "evidence_sha256",
    "freshness", "granularity", "observed_or_modeled_or_missing", "cost_privacy_risk",
    "possible_owner_action", "blocked_if_absent", "confirmation_method",
    "minimum_coverage_spec", "cannot_assume", "promotion_gates_dependent_on_field",
)


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strict_loads(text: str) -> Any:
    def _rej(tok: str) -> Any:
        raise ValueError(f"NONSTANDARD_JSON_CONSTANT:{tok}")

    return json.loads(text, parse_constant=_rej)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_hex64(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v)


def _dec(v: Any) -> Decimal:
    if isinstance(v, bool) or v is None:
        raise ValueError(f"BAD_DEC:{v!r}")
    if isinstance(v, float) and not math.isfinite(v):
        raise ValueError(f"NONFINITE_DEC:{v!r}")
    d = Decimal(str(v))
    if not d.is_finite():
        raise ValueError(f"NONFINITE_DEC:{v!r}")
    return d


def _q4(d: Decimal) -> float:
    return float(d.quantize(Q4, rounding=ROUND_HALF_UP))


def _audit_forensic_ledger_gz_decimal(
    run_dir: Path,
    f03: Mapping[str, Any],
    f04: Mapping[str, Any],
    f05: Mapping[str, Any],
    f06: Mapping[str, Any],
    f07: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Independently streams `logs/forensic_closed_open_gap_ledger_1x.jsonl.gz` directly from disk
    using exact Decimal arithmetic and reconciles EVERY count, subtotal, residual, open/gap fee,
    tail loss, and zero-fee counterfactual against `03`, `04`, `05`, `06`, `07`.
    """
    viols: List[str] = []
    linked = f04.get("linked_forensic_ledger") or {}
    rel_gz = str(linked.get("ledger_rel_path") or "logs/forensic_closed_open_gap_ledger_1x.jsonl.gz")
    gz_path = run_dir / rel_gz
    if not gz_path.is_file() or gz_path.stat().st_size == 0:
        return {"valid": False, "status": "FAIL_MISSING_LEDGER", "violations": ["R4V_MISSING_FORENSIC_LEDGER_GZ"]}

    actual_sha = _sha256_file(gz_path)
    if linked.get("ledger_file_sha256") != actual_sha:
        viols.append("R4V_FORENSIC_LEDGER_SHA_MISMATCH")

    seen_oids: set[str] = set()
    uids: List[str] = []
    rhashes: List[str] = []
    n_sched = 0
    n_filled = 0
    n_unfilled = 0
    n_closed = 0
    n_wins = 0
    n_losses = 0
    n_open = 0
    n_gap = 0
    over_250 = 0
    mfe_ge_1x = 0
    gross_sum = Decimal("0")
    fee_sum = Decimal("0")
    net_sum = Decimal("0")
    open_fee_sum = Decimal("0")
    gap_fee_sum = Decimal("0")
    max_res = Decimal("0")

    try:
        with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
            for ln_no, raw in enumerate(fh, 1):
                s = raw.rstrip("\r\n")
                if not s:
                    continue
                row = _strict_loads(s)
                n_sched += 1
                oid = str(row.get("opportunity_id") or "")
                if not oid or oid in seen_oids:
                    viols.append(f"R4V_LEDGER_DUPLICATE_OPPORTUNITY_ID:line={ln_no}:{oid}")
                seen_oids.add(oid)
                uids.append(str(row.get("episode_uid") or ""))
                rhashes.append(hashlib.sha256(s.encode("utf-8")).hexdigest())

                filled = bool(row.get("filled"))
                lbl = str(row.get("outcome_label") or "")
                if filled:
                    n_filled += 1
                else:
                    n_unfilled += 1
                    if lbl == "DATA_GAP_UNRESOLVED":
                        n_gap += 1
                    continue

                if lbl in ("WIN", "LOSS"):
                    n_closed += 1
                    g = _dec(row["realized_gross_pnl_usd"])
                    ef = _dec(row["entry_fee_usd"])
                    xf = _dec(row["exit_fee_usd"])
                    tf = _dec(row["total_fees_usd"])
                    ne = _dec(row["realized_net_pnl_usd"])
                    op_f = _dec(row.get("accrued_open_entry_fee_usd", 0))
                    if op_f != Decimal("0"):
                        viols.append(f"R4V_OPEN_FEE_MIXED_INTO_CLOSED:line={ln_no}")
                    if tf <= Decimal("0") and (g - ne) > Decimal("0.01"):
                        viols.append(f"R4V_LEDGER_ZERO_FEE_BUG:line={ln_no}")
                    if abs((ef + xf) - tf) > Decimal("0.0005"):
                        viols.append(f"R4V_LEDGER_FEE_SPLIT_DRIFT:line={ln_no}")
                    res = abs((g - tf) - ne)
                    if res > max_res:
                        max_res = res
                    if res > Decimal("0.0010"):
                        viols.append(f"R4V_LEDGER_GROSS_FEE_NET_RESIDUAL_BREACH:line={ln_no}:res={res}")
                    gross_sum += g
                    fee_sum += tf
                    net_sum += ne
                    if lbl == "WIN":
                        n_wins += 1
                    else:
                        n_losses += 1
                        if abs(ne) > Decimal("2.50"):
                            over_250 += 1
                        if float(row.get("post_hoc_mfe_20bar_in_sl_units") or 0.0) >= 1.0:
                            mfe_ge_1x += 1
                elif lbl == "OPEN_AT_HORIZON":
                    n_open += 1
                    if _dec(row.get("total_fees_usd", 0)) != Decimal("0"):
                        viols.append(f"R4V_OPEN_TRADE_CLOSED_FEE_NONZERO:line={ln_no}")
                    open_fee_sum += _dec(row.get("accrued_open_entry_fee_usd", 0))
                elif lbl == "DATA_GAP_UNRESOLVED":
                    n_gap += 1
                    if _dec(row.get("total_fees_usd", 0)) != Decimal("0"):
                        viols.append(f"R4V_GAP_TRADE_CLOSED_FEE_NONZERO:line={ln_no}")
                    op_f = _dec(row.get("accrued_open_entry_fee_usd", 0))
                    ef = _dec(row.get("entry_fee_usd", 0))
                    gap_fee_sum += (op_f if op_f > Decimal("0") else ef)
                else:
                    viols.append(f"R4V_LEDGER_UNKNOWN_LABEL:line={ln_no}:{lbl}")
    except Exception as exc:
        return {"valid": False, "status": "FAIL_PARSE_LEDGER", "violations": [f"R4V_LEDGER_PARSE_ERR:{exc}"]}

    if n_sched != int(linked.get("total_episode_rows", -1)):
        viols.append("R4V_LEDGER_ROW_COUNT_MISMATCH")
    if canonical_hash(uids) != linked.get("episode_uids_sha256"):
        viols.append("R4V_LEDGER_UIDS_HASH_MISMATCH")
    if canonical_hash(rhashes) != linked.get("canonical_rows_sha256"):
        viols.append("R4V_LEDGER_ROWS_HASH_MISMATCH")

    # Cross-check vs 03 & 04
    p03 = f03.get("primary_baseline_1x_null_conservation") or {}
    c04 = f04.get("counts") or {}
    b04 = f04.get("closed_trades_decimal_bridge_usd") or {}
    o04 = f04.get("open_and_gap_fee_separation_usd") or {}
    anc = ANCHOR_1X_DEC_R4["NC_NULL_TRIGGER"]

    if (
        n_closed != int(p03.get("n_closed", -1))
        or n_wins != int(p03.get("n_wins", -1))
        or n_losses != int(p03.get("n_losses", -1))
        or n_closed != int(c04.get("n_closed", -1))
        or n_losses != int(c04.get("n_losses", -1))
        or n_closed != anc["closed"]
        or n_wins != anc["wins"]
        or n_losses != anc["losses"]
    ):
        viols.append("R4V_LEDGER_CLOSED_OR_LOSS_COUNT_MISMATCH")

    # Denominator check: 7/203 vs 7/233 must NOT be swapped!
    dsep = f03.get("denominator_separation_disclosure") or {}
    exp_wr_closed = round(n_wins / max(1, n_closed), 4)
    exp_wr_filled = round(n_wins / max(1, n_filled), 4)
    if (
        not _is_num(dsep.get("primary_closed_win_rate"))
        or abs(float(dsep["primary_closed_win_rate"]) - exp_wr_closed) > 1e-4
        or not _is_num(dsep.get("filled_win_rate_secondary_only"))
        or abs(float(dsep["filled_win_rate_secondary_only"]) - exp_wr_filled) > 1e-4
    ):
        viols.append("R4V_DENOMINATOR_WIN_RATE_SWAPPED_OR_MISMATCH")

    # Censor & gap reconciliation vs 06
    c06 = f06.get("baseline_1x_censoring_and_gaps") or {}
    if (
        n_open != int(c06.get("n_open_censored", -1))
        or n_gap != int(c06.get("n_gap_unresolved", -1))
        or n_open != anc["open"]
        or n_gap != anc["gap"]
        or c06.get("open_trades_silently_dropped") is not False
        or c06.get("open_trades_recoded_as_win_or_loss") is not False
        or c06.get("gap_trades_recoded_as_win_or_loss") is not False
    ):
        viols.append("R4V_CENSOR_OR_GAP_COUNT_MISMATCH")

    # Decimal P&L reconciliation vs 04 & anchor
    agg_res = abs((gross_sum - fee_sum) - net_sum)
    if agg_res > Decimal("0.0010"):
        viols.append(f"R4V_LEDGER_GROSS_FEE_NET_RESIDUAL_BREACH:agg={agg_res}")
    if (
        not _is_num(b04.get("gross_pnl_closed_usd"))
        or abs(_dec(b04["gross_pnl_closed_usd"]) - gross_sum) > Decimal("0.002")
        or abs(gross_sum - anc["gross"]) > Decimal("0.01")
    ):
        viols.append("R4V_LEDGER_GROSS_PNL_MISMATCH")
    if (
        not _is_num(b04.get("total_closed_fees_usd"))
        or abs(_dec(b04["total_closed_fees_usd"]) - fee_sum) > Decimal("0.002")
        or abs(fee_sum - anc["fees"]) > Decimal("0.01")
    ):
        viols.append("R4V_LEDGER_CLOSED_FEES_MISMATCH")
    if (
        not _is_num(b04.get("net_pnl_closed_usd"))
        or abs(_dec(b04["net_pnl_closed_usd"]) - net_sum) > Decimal("0.002")
        or abs(net_sum - anc["net"]) > Decimal("0.01")
    ):
        viols.append("R4V_LEDGER_NET_PNL_MISMATCH")

    # Open accrued fee separation vs 04
    if (
        not _is_num(o04.get("open_at_horizon_28_accrued_entry_fees_usd"))
        or abs(_dec(o04["open_at_horizon_28_accrued_entry_fees_usd"]) - open_fee_sum) > Decimal("0.002")
        or abs(open_fee_sum - anc["open28_fee"]) > Decimal("0.01")
        or o04.get("open_fees_excluded_from_closed_net_pnl") is not True
        or o04.get("entry_fee_double_counted") is not False
    ):
        viols.append("R4V_OPEN_ACCRUED_FEE_SEPARATION_MISMATCH")

    # Tail & planned risk cap vs 05
    if f05.get("planned_cap_is_physical_guarantee") is not False:
        viols.append("R4V_PLANNED_CAP_MISREPRESENTED_AS_GUARANTEE")
    if (f05.get("non_exclusive_overlap_counts") or {}).get("realized_losses_gt_2p50_cap") != f"{over_250}/{n_losses}":
        viols.append("R4V_TAIL_LOSS_GT_2P50_COUNT_MISMATCH")
    if (f05.get("non_exclusive_overlap_counts") or {}).get("losses_20bar_mfe_ge_1p0x_sl_before_stopout") != f"{mfe_ge_1x}/{n_losses}":
        viols.append("R4V_MFE_20BAR_COUNT_MISMATCH")

    # Fee-only counterfactual check vs 07 (gross MUST remain negative and NOT zeroed / NOT executable alpha)
    if (
        f07.get("is_executable_strategy") is not False
        or f07.get("is_proven_edge") is not False
        or f07.get("population_mode") != "FIXED_FILLS_DESCRIPTIVE_REPRICING"
        or f07.get("counterfactual_zero_fee_label") != "COUNTERFACTUAL_ZERO_FEE_DESCRIPTIVE"
        or f07.get("dynamic_vs_fixed_fill_populations_separated") is not True
        or f07.get("fee_reduction_alone_cannot_cure_baseline_losses") is not True
        or not _is_num(f07.get("baseline_gross_pnl_usd"))
        or abs(_dec(f07["baseline_gross_pnl_usd"]) - gross_sum) > Decimal("0.002")
        or _dec(f07["baseline_gross_pnl_usd"]) >= Decimal("0")
        or not _is_num(f07.get("zero_fee_counterfactual_net_pnl_usd"))
        or abs(_dec(f07["zero_fee_counterfactual_net_pnl_usd"]) - gross_sum) > Decimal("0.002")
    ):
        viols.append("R4V_FEE_ONLY_MISLABELED_OR_GROSS_TAMPERED")

    return {
        "valid": len(viols) == 0,
        "status": "PASS" if len(viols) == 0 else "FAIL",
        "recomputed": {
            "n_scheduled": n_sched, "n_filled": n_filled, "n_unfilled": n_unfilled,
            "n_closed": n_closed, "n_wins": n_wins, "n_losses": n_losses,
            "n_open_censored": n_open, "n_gap_unresolved": n_gap,
            "gross_pnl_closed_usd": _q4(gross_sum),
            "total_closed_fees_usd": _q4(fee_sum),
            "net_pnl_closed_usd": _q4(net_sum),
            "open_at_horizon_accrued_entry_fees_usd": _q4(open_fee_sum),
            "gap_accrued_entry_fees_usd": _q4(gap_fee_sum),
            "max_per_trade_residual_usd": _q4(max_res),
            "aggregate_residual_usd": _q4(agg_res),
            "realized_losses_gt_2p50_count": over_250,
            "losses_20bar_mfe_ge_1p0x_sl_count": mfe_ge_1x,
        },
        "violations": viols,
    }


def _audit_six_cost_08_and_arms_09(f08: Mapping[str, Any], f09: Mapping[str, Any]) -> Dict[str, Any]:
    viols: List[str] = []
    dyn = f08.get("dynamic_full_rerisk_six_scenarios") or {}
    if len(dyn) != 6 or f08.get("population_mode") != "FULL_DYNAMIC_RERISK":
        viols.append("R4V_SIX_COST_SCENARIOS_MISSING_OR_WRONG_MODE")
    for sc_id, sc_obj in dyn.items():
        for ac, rs in ((sc_obj or {}).get("arms") or {}).items():
            cl = int((rs or {}).get("closed") or 0)
            if cl > 0:
                g = _dec(rs.get("gross_pnl_usd", 0))
                fe = _dec(rs.get("total_fees_usd", 0))
                ne = _dec(rs.get("net_pnl_usd", 0))
                if fe <= Decimal("0") and (g - ne) > Decimal("0.01"):
                    viols.append(f"R4V_SIX_COST_ZERO_FEE_BUG:{sc_id}:{ac}")
                if abs((g - fe) - ne) > Decimal("0.01"):
                    viols.append(f"R4V_SIX_COST_RESIDUAL_DRIFT:{sc_id}:{ac}")

    # Verify E1/E2/E3/NULL anchors and strict negative CI + non-conflation in 09
    profiles = f09.get("arm_profiles_1x") or {}
    for ac, anc in ANCHOR_1X_DEC_R4.items():
        pr = profiles.get(ac) or {}
        if int(pr.get("n_closed", -1)) != anc["closed"] or int(pr.get("n_wins", -1)) != anc["wins"] or int(pr.get("n_losses", -1)) != anc["losses"]:
            viols.append(f"R4V_FORGED_POSITIVE_CI_OR_ANCHOR_DRIFT:counts:{ac}")
        if not _is_num(pr.get("net_pnl_usd")) or abs(_dec(pr["net_pnl_usd"]) - anc["net"]) > Decimal("0.01"):
            viols.append(f"R4V_FORGED_POSITIVE_CI_OR_ANCHOR_DRIFT:net:{ac}")
        if not _is_num(pr.get("expectancy_r")) or abs(_dec(pr["expectancy_r"]) - anc["exp"]) > Decimal("0.001"):
            viols.append(f"R4V_FORGED_POSITIVE_CI_OR_ANCHOR_DRIFT:exp:{ac}")
        ci_raw = pr.get("ny_day_cluster_95ci_r_raw")
        if not isinstance(ci_raw, list) or len(ci_raw) != 2 or not (_is_num(ci_raw[0]) and _is_num(ci_raw[1])):
            viols.append(f"R4V_FORGED_POSITIVE_CI_OR_ANCHOR_DRIFT:ci_shape:{ac}")
        else:
            lo, hi = float(ci_raw[0]), float(ci_raw[1])
            if not (math.isfinite(lo) and math.isfinite(hi)) or lo > hi or hi >= 0.0:
                viols.append(f"R4V_FORGED_POSITIVE_CI_OR_ANCHOR_DRIFT:ci_val:{ac}:[{lo},{hi}]")
        if pr.get("passes_preregistered_dev_advancement") is not False:
            viols.append(f"R4V_FORGED_DEV_PASS:{ac}")

    if f09.get("comparability_classification") != "INCOMPARABLE_UNMATCHED_POPULATIONS" or f09.get("raw_pnl_best_arm_selector_prohibited") is not True:
        viols.append("R4V_ARM_POPULATION_CONFLATION")

    nc = f09.get("negative_controls_and_label_integrity") or {}
    if nc.get("status") != "PASS" or int((nc.get("future_bar_mutation_invariance") or {}).get("mismatches_count", 1)) != 0:
        viols.append("R4V_NEGATIVE_CONTROLS_OR_FUTURE_MUTATION_FAILED")

    r2_wf = f09.get("r2_forward_only_dev_embargo_regression") or {}
    if int(r2_wf.get("embargo_minutes_required", 0)) < 240 or r2_wf.get("is_fresh_oos") is not False or r2_wf.get("zero_future_train_bars") is not True:
        viols.append("R4V_R2_FORWARD_ONLY_EMBARGO_FAILED")

    return {"valid": len(viols) == 0, "status": "PASS" if len(viols) == 0 else "FAIL", "violations": viols}


def _audit_claims_10_data_11_decision_12(
    f10: Mapping[str, Any], f11: Mapping[str, Any], f12: Mapping[str, Any]
) -> Dict[str, Any]:
    viols: List[str] = []

    # 10: association vs causal, no alpha inflation
    t3 = f10.get("three_axis_diagnosis") or {}
    eng_ax = t3.get("ENGINE_ACCOUNTING_AXIS") or {}
    str_ax = t3.get("STRATEGY_AND_COST_AXIS") or {}
    dat_ax = t3.get("DATA_EXECUTION_AXIS") or {}
    att = f10.get("anti_claim_inflation_attestation") or {}
    if (
        eng_ax.get("verdict") != "NOT_FOUND_IN_TESTED_SCOPE"
        or str_ax.get("verdict") != "ASSOCIATION_ONLY_NO_CAUSAL_CLAIM"
        or str_ax.get("causal_root_fix_confirmed") is not False
        or str_ax.get("new_strategy_arms_e4_e5_created") is not False
        or str_ax.get("optimal_sl_tp_invented") is not False
        or dat_ax.get("verdict") != "INCOMPLETE_OBSERVED_EXECUTION_DATA"
        or att.get("causal_improvement_claimed") is not False
        or att.get("fee_only_treated_as_executable_alpha") is not False
        or att.get("reused_dev_treated_as_oos") is not False
        or att.get("economic_verdict") != "NO_PROVEN_EDGE"
    ):
        viols.append("R4V_CAUSAL_OR_ALPHA_CLAIM_INFLATION")
    txt10 = json.dumps(f10, sort_keys=True).replace("NO_PROVEN_EDGE", "")
    for bad in ("PROVEN_EDGE", "CAUSAL_FIX_PROVED", "FEE_ONLY_ALPHA_EXECUTABLE"):
        if bad in txt10:
            viols.append(f"R4V_CAUSAL_OR_ALPHA_CLAIM_INFLATION:{bad}")

    # 11: DATA_READINESS_REGISTER schema & true unknowns
    reg = list(f11.get("DATA_READINESS_REGISTER") or ())
    if len(reg) < 8 or not all(all(k in row for k in DATA_REGISTER_REQUIRED_15_FIELDS) for row in reg):
        viols.append("R4V_DATA_READINESS_REGISTER_INCOMPLETE_SCHEMA")
    if (
        f11.get("DATA_READINESS_VERDICT") != "INCOMPLETE"
        or f11.get("FRESH_OOS") != "BLOCKED_NO_UNTOUCHED_DATA"
        or f11.get("observed_l1_bbo_available") is not False
        or f11.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") != "UNKNOWN_NOT_LOCALLY_VERIFIED"
        or f11.get("funding_cost_status") not in ("EXCLUDED_UNMODELED", "EXCLUDED_UNMODELED_DISCLOSED")
        or f11.get("private_api_called") is not False
        or f11.get("credentials_or_api_keys_requested") is not False
    ):
        viols.append("R4V_FABRICATED_L1_OR_ACCOUNT_FEE_OR_FUNDING")

    # 12: NO_GO / NO_PROVEN_EDGE / OOS=0 / ACTIVATION=NO / ALL MODES OFF
    modes = f12.get("runtime_safety_modes") or {}
    if (
        not str(f12.get("DECISION") or "").startswith("NO_GO")
        or f12.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE"
        or f12.get("DATA_READINESS") != "INCOMPLETE"
        or f12.get("FRESH_OOS") != "BLOCKED_NO_UNTOUCHED_DATA"
        or f12.get("KH04_ENTRY") != "BLOCKED_NO_DEV_CANDIDATE"
        or f12.get("ACTIVATION_READY") != "NO"
        or int(f12.get("OOS_EVALUATIONS", -1)) != 0
        or any(modes.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO"))
    ):
        viols.append("R4V_FORGED_OOS_OR_ALPHA_OR_ACTIVATION")

    return {"valid": len(viols) == 0, "status": "PASS" if len(viols) == 0 else "FAIL", "violations": viols}


def _recompute_raw_bench_r4(
    raw_doc: Mapping[str, Any],
    rep_pol: Mapping[str, Any],
    *,
    kind: str,
    expected_run_id: str,
    expected_tested_sha: str,
    max_safe_cap: int,
) -> Dict[str, Any]:
    viols: List[str] = []
    if kind == "pytest_controlled_subset":
        dk, cross, cnt_k, sha_k = "wall_clock_seconds", ("wall_seconds", "canonical_sorted_sha256", "rows_count"), "executed_tests_count", "canonical_sorted_outcome_sha256"
    elif kind == "r2_science_slice":
        dk, cross, cnt_k, sha_k = "wall_seconds", ("wall_clock_seconds", "canonical_sorted_outcome_sha256", "executed_tests_count"), "rows_count", "canonical_sorted_sha256"
    else:
        return {"valid": False, "violations": [f"R4V_UNKNOWN_BENCH_KIND:{kind}"]}

    if raw_doc.get("run_id") != expected_run_id or raw_doc.get("tested_executable_sha") != expected_tested_sha:
        viols.append(f"R4V_BENCH_PROVENANCE_MISMATCH:{kind}")
    runs = raw_doc.get("runs")
    if not isinstance(runs, list) or len(runs) != 4:
        viols.append(f"R4V_BENCH_BAD_RUNS_LEN:{kind}")
        return {"valid": False, "violations": viols}

    req_w = {"w=1": 1, "w=2": 2, "w=4": 4, "w=1_repeat": 1}
    durs: Dict[str, float] = {}
    cnts: Dict[str, int] = {}
    shas: Dict[str, str] = {}
    for r in runs:
        if not isinstance(r, Mapping):
            viols.append(f"R4V_BENCH_ROW_NOT_OBJ:{kind}")
            continue
        for ck in cross:
            if ck in r:
                viols.append(f"R4V_BENCH_MIXED_KEY:{kind}:{ck}")
        ml = r.get("mode_label")
        if ml not in req_w or ml in durs:
            viols.append(f"R4V_BENCH_BAD_OR_DUP_MODE:{kind}:{ml}")
            continue
        if not _is_int(r.get("workers")) or int(r["workers"]) != req_w[ml]:
            viols.append(f"R4V_BENCH_BAD_WORKERS:{kind}:{ml}")
        dv = r.get(dk)
        if not _is_num(dv) or not math.isfinite(float(dv)) or float(dv) <= 0.0 or float(dv) > 86_400.0:
            viols.append(f"R4V_BENCH_BAD_DURATION:{kind}:{ml}")
        else:
            durs[ml] = float(dv)
        cv = r.get(cnt_k)
        if not _is_int(cv) or int(cv) <= 0:
            viols.append(f"R4V_BENCH_BAD_COUNT:{kind}:{ml}")
        else:
            cnts[ml] = int(cv)
        sv = r.get(sha_k)
        if not _is_hex64(sv):
            viols.append(f"R4V_BENCH_BAD_SHA:{kind}:{ml}")
        else:
            shas[ml] = str(sv).lower()

    if len(durs) != 4:
        return {"valid": False, "violations": viols}
    if len(cnts) != 4 or len(set(cnts.values())) != 1:
        viols.append(f"R4V_BENCH_COUNT_DRIFT:{kind}")
    if len(shas) != 4 or len(set(shas.values())) != 1:
        viols.append(f"R4V_BENCH_SEMANTIC_SHA_DRIFT:{kind}")

    means = {m: round(durs[m], 4) for m in ("w=1", "w=2", "w=4")}
    fastest = min(("w=1", "w=2", "w=4"), key=lambda m: (round(means[m], 6), req_w[m]))
    sp2 = round(durs["w=1"] / max(1e-9, durs["w=2"]), 3)
    sp4 = round(durs["w=1"] / max(1e-9, durs["w=4"]), 3)
    w1v = round(abs(durs["w=1_repeat"] - durs["w=1"]) / max(1e-9, min(durs["w=1"], durs["w=1_repeat"])), 4)

    if not isinstance(rep_pol, Mapping) or rep_pol.get("status") != "PASS" or rep_pol.get("can_certify") is not True:
        viols.append(f"R4V_BENCH_POLICY_NOT_PASS:{kind}")
    else:
        if rep_pol.get("measured_fastest_mode") != fastest:
            viols.append(f"R4V_BENCH_FASTEST_MISMATCH:{kind}")
        if not _is_num(rep_pol.get("measured_speedup_w2_vs_w1")) or abs(float(rep_pol["measured_speedup_w2_vs_w1"]) - sp2) > 0.01:
            viols.append(f"R4V_BENCH_SPEEDUP_OR_EXTRAPOLATION_TAMPERED:w2:{kind}")
        if not _is_num(rep_pol.get("measured_speedup_w4_vs_w1")) or abs(float(rep_pol["measured_speedup_w4_vs_w1"]) - sp4) > 0.01:
            viols.append(f"R4V_BENCH_SPEEDUP_OR_EXTRAPOLATION_TAMPERED:w4:{kind}")
        if not _is_num(rep_pol.get("w1_repeat_variance_pct")) or abs(float(rep_pol["w1_repeat_variance_pct"]) - w1v) > 1e-3:
            viols.append(f"R4V_BENCH_W1_VAR_MISMATCH:{kind}")
        aw = rep_pol.get("applied_worker_count")
        sc = rep_pol.get("safe_worker_cap")
        if not _is_int(aw) or not _is_int(sc) or int(aw) < 1 or int(aw) > max_safe_cap or int(sc) > max_safe_cap or int(aw) > int(sc):
            viols.append(f"R4V_BENCH_SAFE_CAP_EXCEEDED:{kind}")
        if rep_pol.get("full_suite_speedup_extrapolated") is not False or rep_pol.get("timing_determinism_claimed") is not False:
            viols.append(f"R4V_BENCH_SPEEDUP_OR_EXTRAPOLATION_TAMPERED:extrap:{kind}")

    return {"valid": len(viols) == 0, "means": means, "fastest": fastest, "sp2": sp2, "sp4": sp4, "w1_var": w1v, "violations": viols}


def _verify_junit_raw_xml_r4(
    run_dir: Path, f13: Mapping[str, Any], *, expected_run_id: str, expected_tested_sha: str
) -> Tuple[bool, List[str]]:
    viols: List[str] = []
    cm = f13.get("collection_manifest") or {}
    sm = f13.get("shard_manifest") or {}
    if not cm or not sm:
        return False, ["R4V_JUNIT_MISSING_MANIFESTS"]
    if cm.get("run_id") != expected_run_id or sm.get("run_id") != expected_run_id:
        viols.append("R4V_JUNIT_RUN_ID_MISMATCH")
    if cm.get("tested_executable_sha") != expected_tested_sha or sm.get("tested_executable_sha") != expected_tested_sha:
        viols.append("R4V_JUNIT_TESTED_SHA_MISMATCH")
    if int(sm.get("nonzero_exit_shards_count", 1)) != 0:
        viols.append("R4V_JUNIT_NONZERO_SHARDS")

    c_ids = list(cm.get("collected_node_ids") or ())
    if len(c_ids) < MIN_EXPECTED_PYTEST_COLLECTED_R4:
        viols.append(f"R4V_JUNIT_COLLECTED_BELOW_C2:{len(c_ids)}<{MIN_EXPECTED_PYTEST_COLLECTED_R4}")
    if len(c_ids) != int(cm.get("collected_node_ids_count", -1)) or len(set(c_ids)) != len(c_ids):
        viols.append("R4V_JUNIT_COLLECTED_DUP_OR_COUNT_ERR")

    shards = list(sm.get("shards") or ())
    if not shards:
        return False, ["R4V_JUNIT_ZERO_SHARDS"]

    sched_all: List[str] = []
    tot_t, tot_f, tot_e, tot_s, mand_s = 0, 0, 0, 0, 0
    for sh in shards:
        if int(sh.get("exit_code", 1)) != 0 or bool(sh.get("timed_out", True)) or bool(sh.get("reused", True)):
            viols.append(f"R4V_JUNIT_BAD_SHARD:{sh.get('shard_id')}")
        rel_x = str(sh.get("junit_xml_path") or "")
        xp = run_dir / rel_x
        if not xp.is_file() or xp.stat().st_size == 0:
            viols.append(f"R4V_JUNIT_XML_MISSING:{rel_x}")
            continue
        if _sha256_file(xp) != str(sh.get("xml_sha256") or sh.get("junit_sha256") or ""):
            viols.append(f"R4V_JUNIT_XML_SHA_MISMATCH:{rel_x}")
        sched = list(sh.get("scheduled_node_ids") or ())
        sched_all.extend(sched)
        try:
            root = ET.fromstring(xp.read_text("utf-8"))
        except Exception as exc:
            viols.append(f"R4V_JUNIT_XML_PARSE_ERR:{rel_x}:{exc}")
            continue
        tcs = list(root.iter("testcase"))
        tot_t += len(tcs)
        if len(tcs) != len(sched):
            viols.append(f"R4V_JUNIT_SHARD_LEN_MISMATCH:{sh.get('shard_id')}")
        for tc in tcs:
            cn, nm = str(tc.attrib.get("classname") or ""), str(tc.attrib.get("name") or "")
            if tc.find("failure") is not None:
                tot_f += 1
            if tc.find("error") is not None:
                tot_e += 1
            if tc.find("skipped") is not None:
                tot_s += 1
                if any(tok in cn or tok in nm for tok in ("test_kh_02_r2", "test_kh03_r3", "test_kh03_r3_c1", "test_kh03_r3_c2", "test_kh03_r4")):
                    mand_s += 1

    if sorted(sched_all) != sorted(c_ids):
        viols.append("R4V_JUNIT_BIJECTION_MISMATCH")
    tot_p = tot_t - tot_f - tot_e - tot_s
    if (
        tot_t != int(f13.get("executed_count", -1))
        or tot_p != int(f13.get("passed_count", -1))
        or tot_s != int(f13.get("skipped_count", -1))
        or tot_f != int(f13.get("failed_count", -1))
        or tot_e != int(f13.get("error_count", -1))
    ):
        viols.append("R4V_JUNIT_COUNTS_TAMPERED_VS_RAW_XML")
    if tot_f != 0 or tot_e != 0 or mand_s != 0:
        viols.append(f"R4V_JUNIT_FAIL_ERR_OR_MANDATORY_SKIP:f={tot_f},e={tot_e},ms={mand_s}")
    return len(viols) == 0, viols


def _audit_telemetry_gz_vs_16_r4(mem_gz: Path, f16: Mapping[str, Any]) -> Dict[str, Any]:
    viols: List[str] = []
    if not mem_gz.is_file() or mem_gz.stat().st_size == 0:
        return {"status": "FAIL_MISSING_LOG", "samples_count": 0, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99, "violations": ["R4V_MISSING_TELEMETRY_LOG"]}
    n, min_ram, min_dk, max_ch, below_r, below_d = 0, float("inf"), float("inf"), 0, 0, 0
    try:
        with gzip.open(mem_gz, "rt", encoding="utf-8") as fh:
            for ln in fh:
                s = ln.strip()
                if not s:
                    continue
                row = _strict_loads(s)
                n += 1
                rm = row.get("available_mib")
                if rm is None and row.get("available_ram_bytes") is not None:
                    rm = float(row["available_ram_bytes"]) / (1024.0 * 1024.0)
                if _is_num(rm):
                    rf = float(rm)
                    min_ram = min(min_ram, rf)
                    if rf < HARD_RAM_FLOOR_MIB_R4:
                        below_r += 1
                dk = row.get("disk_free_gib")
                if _is_num(dk):
                    df = float(dk)
                    min_dk = min(min_dk, df)
                    if df < STRICT_RUNTIME_DISK_MIN_GIB_R4:
                        below_d += 1
                ch = row.get("child_count")
                if _is_int(ch):
                    max_ch = max(max_ch, int(ch))
    except Exception as exc:
        return {"status": "FAIL_PARSE", "samples_count": n, "below_floor_samples_count": 1, "max_workers_observed_in_samples": 99, "violations": [f"R4V_TELEMETRY_PARSE_ERR:{exc}"]}

    if n == 0 or min_ram == float("inf") or min_dk == float("inf"):
        viols.append("R4V_TELEMETRY_EMPTY_OR_INCOMPLETE")
    if below_r > 0 or min_ram < HARD_RAM_FLOOR_MIB_R4:
        viols.append(f"R4V_TELEMETRY_RAM_BELOW_512MIB:{min_ram:.2f}")
    if below_d > 0 or min_dk < STRICT_RUNTIME_DISK_MIN_GIB_R4:
        viols.append(f"R4V_TELEMETRY_DISK_BELOW_20GIB:{min_dk:.4f}")
    if max_ch > GLOBAL_MAX_DESCENDANTS_R4:
        viols.append(f"R4V_TELEMETRY_CHILD_EXCEEDED_4:{max_ch}")
    rep_ram, rep_dk, rep_ch = f16.get("min_available_ram_mib"), f16.get("min_disk_free_gib"), f16.get("max_descendant_children")
    if not _is_num(rep_ram) or abs(float(rep_ram) - min_ram) > 1.0:
        viols.append("R4V_TELEMETRY_16_RAM_MUTATED_VS_GZ")
    if not _is_num(rep_dk) or abs(float(rep_dk) - min_dk) > 0.05:
        viols.append("R4V_TELEMETRY_16_DISK_MUTATED_VS_GZ")
    if not _is_int(rep_ch) or int(rep_ch) < max_ch or int(rep_ch) > GLOBAL_MAX_DESCENDANTS_R4:
        viols.append("R4V_TELEMETRY_16_CHILDREN_MUTATED_VS_GZ")
    return {
        "status": "PASS" if len(viols) == 0 else "FAIL",
        "samples_count": n,
        "min_available_ram_mib": round(min_ram if min_ram != float("inf") else 0.0, 2),
        "min_disk_free_gib": round(min_dk if min_dk != float("inf") else 0.0, 4),
        "max_workers_observed_in_samples": max_ch,
        "below_floor_samples_count": below_r + below_d,
        "violations": viols,
    }


def recompute_r4_gates_from_raw(
    *,
    docs: Mapping[str, Mapping[str, Any]],
    ledger_audit: Mapping[str, Any],
    econ_audit: Mapping[str, Any],
    claims_audit: Mapping[str, Any],
    pytest_recomp: Mapping[str, Any],
    science_recomp: Mapping[str, Any],
    mem_audit: Mapping[str, Any],
    junit_ok: bool,
    verifier_violations_count: int = 0,
    for_final_verifier_manifest: bool = False,
) -> Dict[str, Any]:
    """Independently recomputes ALL 30 orthogonal KH-03.R4 gates (`G01..G30`) and verdicts."""
    f00, f01, f02, f03, f04, f05 = docs["00"], docs["01"], docs["02"], docs["03"], docs["04"], docs["05"]
    f06, f07, f08, f09, f10, f11 = docs["06"], docs["07"], docs["08"], docs["09"], docs["10"], docs["11"]
    f12, f13, f14, f15, f16, f17, f18, f20 = docs["12"], docs["13"], docs["14"], docs["15"], docs["16"], docs["17"], docs["18"], docs["20"]

    g: Dict[str, str] = {}

    # G01: Branch / HEAD / clean SHA / not stale prior run
    tsha = str(f00.get("tested_executable_sha") or "")
    stale_shas = {
        PRIOR_KH03_R3_TESTED_SHA, PRIOR_KH03_R3_EVIDENCE_SHA, PRIOR_KH03_R3_PUB_HEAD_SHA,
        PRIOR_KH03_R3_C1_TESTED_SHA, PRIOR_KH03_R3_C1_EVIDENCE_SHA, PRIOR_KH03_R3_C1_PUB_HEAD_SHA,
        PRIOR_KH03_R3_C2_TESTED_SHA, PRIOR_KH03_R3_C2_EVIDENCE_SHA, PRIOR_KH03_R3_C2_PUB_HEAD_SHA,
    }
    stale_rids = {PRIOR_KH03_R3_RUN_ID, PRIOR_KH03_R3_C1_RUN_ID, PRIOR_KH03_R3_C2_RUN_ID}
    sha_ok = (
        f00.get("branch") == MANDATORY_BRANCH_KH03_R4
        and len(tsha) == 40
        and tsha == f00.get("start_head_sha") == f00.get("end_head_sha")
        and tsha not in stale_shas
        and str(f00.get("run_id") or "") not in stale_rids
    )
    clean_ok = bool(f00.get("git_clean_at_start", False)) and not bool(f00.get("local_uncommitted_changes", False))
    g["G01_BRANCH_HEAD_CLEAN_SHA"] = "PASS" if (sha_ok and clean_ok) else ("BLOCKED_UNCOMMITTED_LOCAL_RUN" if sha_ok else "FAIL")

    # G02: Immutable dataset & prior sealed SHAs
    g["G02_IMMUTABLE_DATASET_AND_PRIOR_SEALED_SHA"] = (
        "PASS"
        if (
            f02.get("status") == "PASS"
            and f02.get("canonical_dataset_sha256") == CANONICAL_DATASET_SHA256_KH03_R4
            and bool(f02.get("canonical_dataset_sha256_match"))
            and bool(f02.get("all_prior_sealed_artifacts_intact"))
        )
        else "FAIL"
    )

    # G03: Production AST keep-as-is & zero locked modifications
    g["G03_PRODUCTION_AST_KEEP_AS_IS"] = (
        "PASS"
        if (
            f01.get("status") == "PASS"
            and int(f01.get("locked_files_modified_count", 1)) == 0
            and int(f01.get("unauthorized_changed_files_count", 1)) == 0
            and len(f01.get("forbidden_order_import_hits") or []) == 0
            and bool(f01.get("cost_assumptions_defaults_intact"))
            and f01.get("legacy_guard_status") == "PASS"
        )
        else "FAIL"
    )

    # G04..G08: Population conservation, denominators, censor/gap, Decimal bridge, open fee separation
    led_ok = bool(ledger_audit.get("valid"))
    led_viols = set(ledger_audit.get("violations") or ())
    cc03 = f03.get("conservation_checks") or {}
    g["G04_OPPORTUNITY_ID_CONSERVATION"] = (
        "PASS"
        if (
            f03.get("status") == "PASS"
            and all(bool(cc03.get(k)) for k in ("eligible_eq_scheduled_plus_skipped_busy", "scheduled_eq_closed_plus_open_plus_gap_plus_unfilled", "closed_eq_wins_plus_losses_plus_breakeven", "zero_duplicate_opportunity_ids", "zero_ambiguous_outcomes", "zero_1pos_overlap_violations", "all_6_scenarios_and_arms_conserved", "baseline_1x_matches_sealed_r3_c2_anchors"))
            and not any("DUPLICATE" in v for v in led_viols)
        )
        else "FAIL"
    )
    g["G05_CLOSED_FILLED_SCHEDULED_DENOMINATORS"] = (
        "PASS"
        if (f03.get("status") == "PASS" and not any("DENOMINATOR" in v or "CLOSED_OR_LOSS" in v for v in led_viols))
        else "FAIL"
    )
    g["G06_CENSOR_GAP_STATUS"] = (
        "PASS"
        if (f06.get("status") == "PASS" and not any("CENSOR_OR_GAP" in v for v in led_viols))
        else "FAIL"
    )
    g["G07_DECIMAL_GROSS_FEE_NET_1X"] = (
        "PASS"
        if (f04.get("status") == "PASS" and led_ok and bool(f04.get("historical_r3_c2_anchor_exact_match")))
        else "FAIL"
    )
    g["G08_OPEN_FEE_SEPARATION"] = (
        "PASS"
        if (f04.get("status") == "PASS" and not any("OPEN_ACCRUED" in v or "OPEN_FEE" in v or "OPEN_TRADE" in v for v in led_viols))
        else "FAIL"
    )

    # G09..G16: 6-cost, fixed-vs-dynamic, E1/E2/E3 non-conflation, realized tail, assoc vs causal, negative controls, R2 embargo, future mutation
    econ_ok = bool(econ_audit.get("valid"))
    ec_viols = set(econ_audit.get("violations") or ())
    cl_ok = bool(claims_audit.get("valid"))
    cl_viols = set(claims_audit.get("violations") or ())

    g["G09_SIX_COST_SCENARIO_ACCOUNTING"] = (
        "PASS"
        if (f08.get("status") == "PASS" and not any("SIX_COST" in v for v in ec_viols) and int(f08.get("zero_fee_display_bugs_count", 1)) == 0 and int(f08.get("decimal_residual_breaches_count", 1)) == 0)
        else "FAIL"
    )
    g["G10_FIXED_FILL_VS_DYNAMIC_SEPARATED"] = (
        "PASS"
        if (f07.get("status") == "PASS" and not any("FEE_ONLY" in v for v in led_viols) and f07.get("dynamic_vs_fixed_fill_populations_separated") is True and f07.get("is_executable_strategy") is False)
        else "FAIL"
    )
    g["G11_E1_E2_E3_NO_POPULATION_CONFLATION"] = (
        "PASS"
        if (f09.get("status") == "PASS" and econ_ok and f09.get("comparability_classification") == "INCOMPARABLE_UNMATCHED_POPULATIONS" and f09.get("raw_pnl_best_arm_selector_prohibited") is True)
        else "FAIL"
    )
    g["G12_REALIZED_GT_PLANNED_RISK_DISCLOSED"] = (
        "PASS"
        if (f05.get("status") == "PASS" and f05.get("planned_cap_is_physical_guarantee") is False and not any("PLANNED_CAP" in v or "TAIL_LOSS" in v for v in led_viols))
        else "FAIL"
    )
    g["G13_ASSOCIATION_VS_CAUSAL_LABELS"] = (
        "PASS"
        if (f10.get("status") == "PASS" and not any("CAUSAL_OR_ALPHA" in v for v in cl_viols))
        else "FAIL"
    )
    nc09 = f09.get("negative_controls_and_label_integrity") or {}
    g["G14_NEGATIVE_CONTROL_AND_PLACEBO"] = (
        "PASS" if (nc09.get("status") == "PASS" and bool((nc09.get("synthetic_label_and_conservative_tie_controls") or {}).get("passed"))) else "FAIL"
    )
    r2_emb = f09.get("r2_forward_only_dev_embargo_regression") or {}
    g["G15_R2_FORWARD_ONLY_DEV_EMBARGO"] = (
        "PASS" if (int(r2_emb.get("embargo_minutes_required", 0)) >= 240 and r2_emb.get("is_fresh_oos") is False and r2_emb.get("zero_future_train_bars") is True) else "FAIL"
    )
    fm09 = nc09.get("future_bar_mutation_invariance") or {}
    g["G16_NO_LOOKAHEAD_FUTURE_MUTATION"] = (
        "PASS" if (bool(fm09.get("passed")) and int(fm09.get("mismatches_count", 1)) == 0) else "FAIL"
    )

    # G17..G20: Data readiness, no fabricated L1/fee/funding, zero fresh OOS, all activation OFF
    g["G17_DATA_READINESS_REGISTER_TRUE_UNKNOWN"] = (
        "PASS" if (f11.get("status") == "PASS" and bool(f11.get("register_schema_complete")) and not any("REGISTER" in v for v in cl_viols)) else "FAIL"
    )
    g["G18_NO_FABRICATED_ACCOUNT_FEES_L1_FUNDING"] = (
        "PASS" if (f11.get("status") == "PASS" and not any("FABRICATED_L1" in v for v in cl_viols)) else "FAIL"
    )
    g["G19_ZERO_FRESH_OOS_AND_NO_NEW_ALPHA"] = (
        "PASS" if (f12.get("status") == "PASS" and int(f12.get("OOS_EVALUATIONS", -1)) == 0 and f12.get("FRESH_OOS") == "BLOCKED_NO_UNTOUCHED_DATA" and not any("FORGED_OOS" in v for v in cl_viols)) else "FAIL"
    )
    modes12 = f12.get("runtime_safety_modes") or {}
    g["G20_NO_MARKET_ORDER_ALL_ACTIVATION_OFF"] = (
        "PASS"
        if (
            f12.get("ACTIVATION_READY") == "NO"
            and all(modes12.get(k) == "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO"))
            and len(f01.get("forbidden_order_import_hits") or []) == 0
        )
        else "FAIL"
    )

    # G25: Resource safety (evaluated before heavy G21..G24)
    mp00 = f00.get("strict_multi_mount_probe") or {}
    waiver = bool(f00.get("owner_disk_preflight_waiver", False)) or bool(mp00.get("owner_waiver_permitted", False)) or bool(f16.get("owner_disk_preflight_waiver", False)) or bool(f20.get("owner_disk_preflight_waiver", False))
    mounts = list(mp00.get("distinct_mounts") or ())
    probe_errs = list(mp00.get("probe_errors") or ())
    pre_disk_ok = len(mounts) > 0 and len(probe_errs) == 0 and all(float(m.get("free_gib", 0.0)) >= STRICT_PREFLIGHT_DISK_MIN_GIB_R4 and float(m.get("projected_post_reserve_gib", 0.0)) >= STRICT_RUNTIME_DISK_MIN_GIB_R4 for m in mounts) and bool(mp00.get("can_run_heavy_stages", False))
    rt_disk_ok = float(f16.get("min_disk_free_gib") or 0.0) >= STRICT_RUNTIME_DISK_MIN_GIB_R4
    ram_pid_ok = (
        float(f16.get("min_available_ram_mib") or 0.0) >= HARD_RAM_FLOOR_MIB_R4
        and int(f16.get("max_descendant_children", 99)) <= GLOBAL_MAX_DESCENDANTS_R4
        and int(f16.get("orphan_children_after_cleanup", 99)) == 0
        and bool(f16.get("sentinel_temp_cleaned_up", False))
        and mem_audit.get("status") == "PASS"
        and int(mem_audit.get("below_floor_samples_count", 1)) == 0
        and int(mem_audit.get("max_workers_observed_in_samples", 99)) <= GLOBAL_MAX_DESCENDANTS_R4
    )
    if waiver or len(probe_errs) > 0 or not ram_pid_ok:
        g["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] = "FAIL"
    elif pre_disk_ok and rt_disk_ok:
        g["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] = "PASS"
    elif pre_disk_ok and not rt_disk_ok:
        g["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] = "FAIL"
    else:
        g["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] = "BLOCKED_LOW_DISK"

    res_pass = g["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] == "PASS"
    low_disk = g["G25_DISK_RAM_TEMP_ORPHAN_LOCK"] == "BLOCKED_LOW_DISK"

    # G21: Full repo pytest JUnit 1:1
    if f13.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        g["G21_FULL_REPO_JUNIT_EXACT_1_TO_1"] = "NOT_RUN_BLOCKED_LOW_DISK" if low_disk else "FAIL"
    elif (
        f13.get("status") == "PASS" and res_pass and junit_ok
        and int(f13.get("collected_count", 0)) >= MIN_EXPECTED_PYTEST_COLLECTED_R4
        and int(f13.get("collected_count", -1)) == int(f13.get("executed_count", -2))
        and int(f13.get("failed_count", 1)) == 0 and int(f13.get("error_count", 1)) == 0
        and int(f13.get("mandatory_skipped_count", 1)) == 0
    ):
        g["G21_FULL_REPO_JUNIT_EXACT_1_TO_1"] = "PASS"
    else:
        g["G21_FULL_REPO_JUNIT_EXACT_1_TO_1"] = "FAIL"

    # G22: Full A1/SI8/Adapter/D4 parity
    fp14 = f14.get("per_decision_and_episode_fingerprints") or {}
    fp_ok = all(fp14.get(k) == v for k, v in SEALED_BASELINE_PARITY_FINGERPRINTS.items())
    if f14.get("BASELINE_PARITY_VERDICT") == "NOT_RUN_BLOCKED_LOW_DISK":
        g["G22_A1_SI8_ADAPTER_D4_PARITY"] = "NOT_RUN_BLOCKED_LOW_DISK" if low_disk else "FAIL"
    elif (
        f14.get("BASELINE_PARITY_VERDICT") == "PASS" and res_pass and fp_ok
        and int(f14.get("a1_population_count", 0)) == 56710 and int(f14.get("a1_mismatches_count", 1)) == 0
        and int(f14.get("si8_population_count", 0)) == 6375 and int(f14.get("si8_mismatches_count", 1)) == 0
        and int(f14.get("three_adapter_population_count", 0)) == 246 and int(f14.get("three_adapter_mismatches_count", 1)) == 0
        and int(f14.get("d4_historical_closed_trades", 1)) == 0
    ):
        g["G22_A1_SI8_ADAPTER_D4_PARITY"] = "PASS"
    else:
        g["G22_A1_SI8_ADAPTER_D4_PARITY"] = "FAIL"

    # G23 & G24: W1/W2/W4/W1_repeat determinism, real timings, safe worker caps
    if f15.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        st_b = "NOT_RUN_BLOCKED_LOW_DISK" if low_disk else "FAIL"
        g["G23_ASYNC_SHARD_W1_W2_W4_SEMANTIC_SHA"] = st_b
        g["G24_MEASURED_SPEEDUP_AND_SAFE_WORKER_CAP"] = st_b
    else:
        p_pol = f15.get("pytest_worker_policy") or {}
        s_pol = f15.get("science_worker_policy") or {}
        g["G23_ASYNC_SHARD_W1_W2_W4_SEMANTIC_SHA"] = (
            "PASS" if (res_pass and bool(pytest_recomp.get("valid")) and bool(science_recomp.get("valid"))) else "FAIL"
        )
        g["G24_MEASURED_SPEEDUP_AND_SAFE_WORKER_CAP"] = (
            "PASS"
            if (
                res_pass and bool(pytest_recomp.get("valid")) and bool(science_recomp.get("valid"))
                and f15.get("status") == "PASS"
                and int(p_pol.get("applied_worker_count", 99)) <= 3
                and int(s_pol.get("applied_worker_count", 99)) <= GLOBAL_MAX_DESCENDANTS_R4
                and f15.get("full_suite_speedup_extrapolated") is False
            )
            else "FAIL"
        )

    # G26: Inherited (>=224) + NEW R4 (>=16) = >=240 unique RED/GREEN fault controls
    g["G26_INHERITED_AND_NEW_FAULT_CONTROLS"] = (
        "PASS"
        if (
            f17.get("status") == "PASS"
            and int(f17.get("inherited_c2_c1_r3_controls_count", 0)) >= 224
            and int(f17.get("new_r4_controls_count", 0)) >= 16
            and int(f17.get("failed_controls", 1)) == 0
            and int(f17.get("unique_fault_ids", 0)) == int(f17.get("total_controls", -1))
        )
        else "FAIL"
    )

    # G27: Separate-OS verifier mutations (>=51 inherited + >=16 NEW R4)
    g["G27_INDEPENDENT_VERIFIER_MUTATIONS"] = (
        "PASS"
        if (
            f18.get("status") == "PASS"
            and bool(f18.get("clean_clone_verifier_green"))
            and int(f18.get("inherited_c2_c1_r3_mutations_tested_count", 0)) >= 51
            and int(f18.get("inherited_c2_c1_r3_mutations_failed_detection_count", 1)) == 0
            and int(f18.get("new_r4_mutations_tested_count", 0)) >= 16
            and int(f18.get("new_r4_mutations_failed_detection_count", 1)) == 0
        )
        else "FAIL"
    )

    # G28: Distinct OS PID independent verifier
    g["G28_DISTINCT_PID_INDEPENDENT_VERIFIER"] = (
        ("PASS" if verifier_violations_count == 0 else "FAIL") if for_final_verifier_manifest else "PENDING_SEPARATE_OS_VERIFIER"
    )

    # G29: Economic verdict / NO_GO evidence consistent (NEVER fake alpha PASS!)
    g["G29_ECONOMIC_VERDICT_NO_GO_EVIDENCE_CONSISTENT"] = (
        "NO_PROVEN_EDGE_NO_GO_DIAGNOSIS_CONSISTENT"
        if (cl_ok and led_ok and econ_ok and f12.get("RESEARCH_ECONOMIC_VERDICT") == "NO_PROVEN_EDGE" and str(f12.get("DECISION") or "").startswith("NO_GO"))
        else "FAIL"
    )

    # G30: Two-phase Git remote verified publication (fails closed on false readback / false CI PASS)
    false_pub = (
        (bool(f20.get("remote_readback_sha256_verified")) and not f20.get("publication_head_sha"))
        or (f20.get("ci_status") == "CI_PASS" and not bool(f20.get("actual_github_actions_run_verified")))
    )
    if false_pub:
        g["G30_TWO_PHASE_GIT_REMOTE_VERIFIED_PUBLICATION"] = "FAIL"
    else:
        g["G30_TWO_PHASE_GIT_REMOTE_VERIFIED_PUBLICATION"] = str(f20.get("publication_status") or "PRE_PUSH_PENDING")

    ordered = {k: g[k] for k in GATE_30_KEYS_KH03_R4}
    eng_gates = [k for k in GATE_30_KEYS_KH03_R4 if k not in ("G28_DISTINCT_PID_INDEPENDENT_VERIFIER", "G29_ECONOMIC_VERDICT_NO_GO_EVIDENCE_CONSISTENT", "G30_TWO_PHASE_GIT_REMOTE_VERIFIED_PUBLICATION")]
    if for_final_verifier_manifest:
        eng_gates.append("G28_DISTINCT_PID_INDEPENDENT_VERIFIER")

    all_eng_pass = all(ordered[k] == "PASS" for k in eng_gates) and ordered["G29_ECONOMIC_VERDICT_NO_GO_EVIDENCE_CONSISTENT"] == "NO_PROVEN_EDGE_NO_GO_DIAGNOSIS_CONSISTENT" and not false_pub
    any_fail = any(ordered[k] == "FAIL" for k in GATE_30_KEYS_KH03_R4)

    eng_verdict = "PASS" if all_eng_pass else ("BLOCKED_LOW_DISK" if (low_disk and not any_fail) else "FAIL")
    diag_verdict = "PASS" if (ordered["G29_ECONOMIC_VERDICT_NO_GO_EVIDENCE_CONSISTENT"] == "NO_PROVEN_EDGE_NO_GO_DIAGNOSIS_CONSISTENT" and led_ok and econ_ok and cl_ok) else "FAIL"
    overall_cls = (
        "ENGINEERING_PASS_DIAGNOSIS_PASS_ECONOMIC_NO_PROVEN_EDGE_DATA_INCOMPLETE"
        if all_eng_pass
        else ("BLOCKED_LOW_DISK" if (low_disk and not any_fail) else "FAIL_CLOSED")
    )

    return {
        "gates_30": ordered,
        "verdicts": {
            "ENGINEERING": eng_verdict,
            "DIAGNOSIS": diag_verdict,
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
            "DATA_READINESS": str(f11.get("DATA_READINESS_VERDICT") or "INCOMPLETE"),
            "FRESH_OOS": str(f12.get("FRESH_OOS") or "BLOCKED_NO_UNTOUCHED_DATA"),
            "KH04_ENTRY": str(f12.get("KH04_ENTRY") or "BLOCKED_NO_DEV_CANDIDATE"),
            "ACTIVATION_READY": str(f12.get("ACTIVATION_READY") or "NO"),
            "CI_STATUS": str(f20.get("ci_status") or "CI_UNKNOWN_NO_ACTIONS_RUN"),
            "PUBLICATION": ordered["G30_TWO_PHASE_GIT_REMOTE_VERIFIED_PUBLICATION"],
        },
        "overall_classification": overall_cls,
    }


def verify_kh03_r4_artifacts_independently_from_disk(
    run_dir: Path,
    *,
    expected_run_id: Optional[str] = None,
    expected_tested_sha: Optional[str] = None,
    write_manifest: bool = True,
) -> Tuple[int, Dict[str, Any]]:
    run_dir = Path(run_dir).resolve()
    verif_pid = os.getpid()
    viols: List[str] = []
    docs: Dict[str, Dict[str, Any]] = {}
    file_hashes: Dict[str, str] = {}

    for rel in REQUIRED_ARTIFACTS_KH03_R4:
        p = run_dir / rel
        if not p.is_file() or p.stat().st_size == 0:
            viols.append(f"R4V_MISSING_OR_EMPTY_ARTIFACT:{rel}")
            continue
        file_hashes[rel] = _sha256_file(p)
        if rel.endswith(".json"):
            try:
                docs[rel[:2]] = _strict_loads(p.read_text("utf-8"))
            except Exception as exc:
                viols.append(f"R4V_INVALID_JSON:{rel}:{exc}")

    # Require full 00..20 JSON docs present
    for dk in ("00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20"):
        if dk not in docs:
            viols.append(f"R4V_MISSING_JSON_DOC:{dk}")

    if viols:
        out = {
            "spec_version": SPEC_VERSION_KH03_R4,
            "schema_version": SCHEMA_VERSION_KH03_R4,
            "verifier_pid": verif_pid,
            "status": "FAIL_MISSING_OR_INVALID_ARTIFACTS",
            "violations": viols,
        }
        return 1, out

    f00, f02, f15, f16, f19, f20 = docs["00"], docs["02"], docs["15"], docs["16"], docs["19"], docs["20"]
    rid = expected_run_id or str(f00.get("run_id") or "")
    tsha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")

    # Check stale prior run_id or SHA reused anywhere
    stale_shas = {
        PRIOR_KH03_R3_TESTED_SHA, PRIOR_KH03_R3_EVIDENCE_SHA, PRIOR_KH03_R3_PUB_HEAD_SHA,
        PRIOR_KH03_R3_C1_TESTED_SHA, PRIOR_KH03_R3_C1_EVIDENCE_SHA, PRIOR_KH03_R3_C1_PUB_HEAD_SHA,
        PRIOR_KH03_R3_C2_TESTED_SHA, PRIOR_KH03_R3_C2_EVIDENCE_SHA, PRIOR_KH03_R3_C2_PUB_HEAD_SHA,
    }
    stale_rids = {PRIOR_KH03_R3_RUN_ID, PRIOR_KH03_R3_C1_RUN_ID, PRIOR_KH03_R3_C2_RUN_ID}
    if rid in stale_rids or tsha in stale_shas:
        viols.append("R4V_STALE_PRIOR_RUN_OR_SHA_REUSED")

    orch_pid = f00.get("orchestrator_pid")
    if _is_int(orch_pid) and int(orch_pid) == verif_pid:
        viols.append("R4V_SAME_PID_AS_ORCHESTRATOR")

    # Provenance consistency across all JSON docs
    for dk, doc in sorted(docs.items()):
        if doc.get("run_id") != rid:
            viols.append(f"R4V_RUN_ID_MISMATCH:{dk}")
        if doc.get("tested_executable_sha") != tsha:
            viols.append(f"R4V_TESTED_SHA_MISMATCH:{dk}")
        if doc.get("run_id") in stale_rids or doc.get("tested_executable_sha") in stale_shas:
            viols.append(f"R4V_STALE_PRIOR_RUN_OR_SHA_REUSED:{dk}")

    # Check canonical dataset SHA in 02
    if f02.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH03_R4 or f02.get("canonical_dataset_sha256_match") is not True:
        viols.append("R4V_DATASET_SHA_MISMATCH")

    # False publication / CI claim check in 20
    if (bool(f20.get("remote_readback_sha256_verified")) and not f20.get("publication_head_sha")) or (
        f20.get("ci_status") == "CI_PASS" and not bool(f20.get("actual_github_actions_run_verified"))
    ):
        viols.append("R4V_FALSE_PUBLICATION_OR_CI_CLAIM")

    # Independent forensic ledger Decimal audit (03..07 + raw ledger gz)
    ledger_audit = _audit_forensic_ledger_gz_decimal(run_dir, docs["03"], docs["04"], docs["05"], docs["06"], docs["07"])
    viols.extend(ledger_audit.get("violations") or ())

    # Independent 6-cost & E1/E2/E3/NULL comparability + negative controls + R2 embargo audit (08, 09)
    econ_audit = _audit_six_cost_08_and_arms_09(docs["08"], docs["09"])
    viols.extend(econ_audit.get("violations") or ())

    # Independent claim inflation & DATA_READINESS_REGISTER & NO_GO decision audit (10, 11, 12)
    claims_audit = _audit_claims_10_data_11_decision_12(docs["10"], docs["11"], docs["12"])
    viols.extend(claims_audit.get("violations") or ())

    # Independent raw telemetry audit (logs/runtime_memory_samples_0p5s.jsonl.gz vs 16)
    mem_gz = run_dir / str(f16.get("memory_samples_log_rel") or "logs/runtime_memory_samples_0p5s.jsonl.gz")
    mem_audit = _audit_telemetry_gz_vs_16_r4(mem_gz, f16)
    viols.extend(mem_audit.get("violations") or ())
    if mem_gz.is_file():
        file_hashes["logs/runtime_memory_samples_0p5s.jsonl.gz"] = _sha256_file(mem_gz)

    led_gz = run_dir / str((docs["04"].get("linked_forensic_ledger") or {}).get("ledger_rel_path") or "logs/forensic_closed_open_gap_ledger_1x.jsonl.gz")
    if led_gz.is_file():
        file_hashes["logs/forensic_closed_open_gap_ledger_1x.jsonl.gz"] = _sha256_file(led_gz)

    # Independent raw W1/W2/W4/W1_repeat benchmark recomputation (15)
    if f15.get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        p_rec = {"valid": False, "violations": ["NOT_RUN_BLOCKED_LOW_DISK"]}
        s_rec = {"valid": False, "violations": ["NOT_RUN_BLOCKED_LOW_DISK"]}
    else:
        p_rec = _recompute_raw_bench_r4(
            f15.get("pytest_controlled_subset_raw") or {},
            f15.get("pytest_worker_policy") or {},
            kind="pytest_controlled_subset", expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=3,
        )
        s_rec = _recompute_raw_bench_r4(
            f15.get("science_slice_8000_raw") or {},
            f15.get("science_worker_policy") or {},
            kind="r2_science_slice", expected_run_id=rid, expected_tested_sha=tsha, max_safe_cap=GLOBAL_MAX_DESCENDANTS_R4,
        )
        viols.extend(p_rec.get("violations") or ())
        viols.extend(s_rec.get("violations") or ())

    # Independent JUnit raw XML bijection (13)
    if docs["13"].get("status") == "NOT_RUN_BLOCKED_LOW_DISK":
        junit_ok, j_viols = False, []
    else:
        junit_ok, j_viols = _verify_junit_raw_xml_r4(run_dir, docs["13"], expected_run_id=rid, expected_tested_sha=tsha)
        viols.extend(j_viols)

    # Recompute ALL 30 orthogonal gates independently
    recomp = recompute_r4_gates_from_raw(
        docs=docs,
        ledger_audit=ledger_audit,
        econ_audit=econ_audit,
        claims_audit=claims_audit,
        pytest_recomp=p_rec,
        science_recomp=s_rec,
        mem_audit=mem_audit,
        junit_ok=junit_ok,
        verifier_violations_count=len(viols),
        for_final_verifier_manifest=True,
    )

    # Detect any forged gate inflation in orchestrator artifact 19
    rep_g = f19.get("gates_30") or {}
    for gk, gv in recomp["gates_30"].items():
        if gk == "G28_DISTINCT_PID_INDEPENDENT_VERIFIER":
            continue
        if rep_g.get(gk) == "PASS" and gv != "PASS":
            viols.append(f"R4V_FORGED_GATE_INFLATION:{gk}:rep=PASS!=recomp={gv}")
    if (f19.get("verdicts") or {}).get("ENGINEERING") == "PASS" and recomp["verdicts"]["ENGINEERING"] != "PASS":
        viols.append("R4V_FORGED_ENGINEERING_PASS")

    # Re-evaluate G28 if new violations were appended
    if viols and recomp["gates_30"]["G28_DISTINCT_PID_INDEPENDENT_VERIFIER"] == "PASS":
        recomp["gates_30"]["G28_DISTINCT_PID_INDEPENDENT_VERIFIER"] = "FAIL"
        recomp["verdicts"]["ENGINEERING"] = "FAIL"
        recomp["overall_classification"] = "FAIL_CLOSED"

    all_ok = (len(viols) == 0) and (recomp["verdicts"]["ENGINEERING"] == "PASS")
    manifest: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R4,
        "schema_version": SCHEMA_VERSION_KH03_R4,
        "run_id": rid,
        "tested_executable_sha": tsha,
        "verified_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "orchestrator_pid": orch_pid,
        "verifier_pid": verif_pid,
        "distinct_os_process_verified": (_is_int(orch_pid) and int(orch_pid) != verif_pid),
        "independent_forensic_ledger_audit": ledger_audit,
        "independent_six_cost_and_arms_audit": econ_audit,
        "independent_claims_data_decision_audit": claims_audit,
        "independent_pytest_benchmark_recompute": p_rec,
        "independent_science_benchmark_recompute": s_rec,
        "independent_telemetry_audit": mem_audit,
        "independent_junit_bijection_ok": junit_ok,
        "independent_recomputed_30_gates": recomp["gates_30"],
        "independent_recomputed_verdicts": recomp["verdicts"],
        "overall_classification": recomp["overall_classification"],
        "verified_artifact_sha256_manifest": dict(sorted(file_hashes.items())),
        "violations_count": len(viols),
        "violations": viols,
        "status": "PASS" if all_ok else "FAIL",
    }
    manifest["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in manifest.items() if k != "artifact_canonical_sha256"})

    if write_manifest:
        write_json_durable_atomic(run_dir / MANIFEST_21_NAME_KH03_R4, manifest)

    return (0 if all_ok else 1), manifest


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="KH-03.R4 Separate-OS Independent Verifier (G01..G30)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--expected-run-id", default=None)
    ap.add_argument("--expected-tested-sha", default=None)
    ap.add_argument("--no-write-manifest", action="store_true")
    args = ap.parse_args(argv)
    rc, res = verify_kh03_r4_artifacts_independently_from_disk(
        args.run_dir,
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        write_manifest=not args.no_write_manifest,
    )
    print(json.dumps({"status": res["status"], "verifier_pid": res["verifier_pid"], "violations_count": len(res.get("violations") or ()), "violations": res.get("violations") or ()}, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
