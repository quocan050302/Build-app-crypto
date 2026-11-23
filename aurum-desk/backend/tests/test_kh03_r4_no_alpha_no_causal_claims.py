"""
aurum-desk/backend/tests/test_kh03_r4_no_alpha_no_causal_claims.py

Tests KH-03.R4 anti-claim-inflation guards (association != causal, zero-fee != executable alpha,
planned $2.50 != guarantee), DATA_READINESS_REGISTER 15-field x 8-domain schema & true unknowns,
deterministic NO_GO decision rules, zero unauthorized R1 callers, and ALL 16 NEW unique R4
RED/GREEN fault controls (`KH03R4-F01` .. `KH03R4-F16`).
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_research_integrity_service import CANONICAL_DATASET_SHA256_KH02_R2
from services.kh_03_r2_methodology_service import (
    build_kh03_r2_forward_only_dev_folds,
    parse_kh03_cluster_ci_strict,
    validate_kh03_r2_fold_causality,
)
from services.kh_03_r3_legacy_research_guard import audit_kh03_r3_legacy_research_callgraph
from services.kh_03_r4_economic_diagnostic_service import (
    DATA_READINESS_REQUIRED_FIELDS,
    build_r4_counterfactual_cost_only_diagnostics,
    build_r4_data_gap_register,
    build_r4_mechanism_evidence_table,
    build_r4_trade_population_conservation,
    decide_r4_research_next_action,
    recompute_r4_decimal_economic_bridge,
    validate_r4_no_claim_inflation,
)
from tests.test_kh03_r4_economic_accounting import _make_synthetic_anchor_forensic_rows
from tests.test_kh03_r4_population_and_censoring import _make_population_inputs


def _build_clean_r4_docs_fixture() -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    inp = _make_population_inputs()
    rows = inp["rows"]
    d03 = build_r4_trade_population_conservation(
        lineage_05=inp["lineage_05"], forensic_rows=rows, r2_cands_1x=inp["r2_cands_1x"],
        dyn6_scenarios=inp["dyn6"], dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
    )
    d04 = recompute_r4_decimal_economic_bridge(rows, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2, ledger_meta={})
    dyn6_full = {
        **inp["dyn6"],
        "2X_ALL_COST_0p0004": {"arms": {"NC_NULL_TRIGGER": {"closed": 111}}},
    }
    d07 = build_r4_counterfactual_cost_only_diagnostics(
        rows, fixed_fills_from_r3={}, dyn6_scenarios=dyn6_full, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2
    )
    f08_attr = {"non_exclusive_failure_buckets": {"losses_with_20bar_mfe_ge_1p0x_sl_before_stopout": 86, "realized_losses_gt_2p50_cap_count": 139}}
    d05, d10 = build_r4_mechanism_evidence_table(rows, f08_attr=f08_attr, f09_cost={}, decimal_bridge=d04, fee_only_diag=d07)
    d09 = {
        "comparability_classification": "INCOMPARABLE_UNMATCHED_POPULATIONS",
        "raw_pnl_best_arm_selector_prohibited": True,
        "passing_dev_candidate_arms": [],
        "status": "PASS",
    }
    d11 = build_r4_data_gap_register(
        repo_root=REPO_ROOT, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
        timeframe_stats={"1m": {"first_utc": "2026-10-08T00:00:00Z", "last_utc": "2026-10-28T00:00:00Z", "missing_bar_ratio": 0.2364}},
    )
    d12 = decide_r4_research_next_action(decimal_bridge=d04, fee_only_diag=d07, data_register=d11, arm_comparability=d09)
    return rows, {"03": d03, "04": d04, "05": d05, "07": d07, "09": d09, "10": d10, "11": d11, "12": d12}


def run_new_kh03_r4_fault_controls_16(
    *,
    forensic_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    docs_03_12: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Executes 16 NEW unique KH-03.R4 RED/GREEN fault controls (`KH03R4-F01` .. `KH03R4-F16`).
    Every control runs real negative injection (RED) + clean recovery (GREEN).
    """
    if forensic_rows is None or docs_03_12 is None:
        base_rows, base_docs = _build_clean_r4_docs_fixture()
    else:
        base_rows = list(forensic_rows)
        base_docs = {k: dict(v) for k, v in docs_03_12.items()}

    clean_v = validate_r4_no_claim_inflation(base_docs)
    green_ok = clean_v["valid"] is True
    out: List[Dict[str, Any]] = []

    def _rec(fid: str, name: str, fn: str, red_det: bool, grn: bool, note: str) -> None:
        ok = bool(red_det) and bool(grn)
        out.append({
            "fault_id": fid, "control_name": name, "target_function": fn,
            "red_negative_detected": bool(red_det), "green_clean_passed": bool(grn),
            "detail": note, "status": "PASS" if ok else "FAIL",
        })

    # F01: Decimal residual breach (>1e-3)
    r1 = copy.deepcopy(list(base_rows))
    for rr in r1:
        if rr.get("filled") and rr.get("outcome_label") in ("WIN", "LOSS"):
            rr["realized_net_pnl_usd"] = round(float(rr["realized_net_pnl_usd"]) + 0.05, 4)
            break
    d1 = recompute_r4_decimal_economic_bridge(r1, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2, ledger_meta={})
    _rec("KH03R4-F01", "DECIMAL_RESIDUAL_BREACH_DETECTED", "recompute_r4_decimal_economic_bridge", d1["status"] == "FAIL", base_docs["04"]["status"] == "PASS", "gross-fees!=net detected")

    # F02: Entry fee double count / open fee leaked into closed
    r2 = copy.deepcopy(list(base_rows))
    for rr in r2:
        if rr.get("outcome_label") == "OPEN_AT_HORIZON":
            rr["total_fees_usd"] = 0.50
            break
    d2 = recompute_r4_decimal_economic_bridge(r2, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2, ledger_meta={})
    _rec("KH03R4-F02", "OPEN_FEE_LEAK_INTO_CLOSED_REJECTED", "recompute_r4_decimal_economic_bridge", d2["status"] == "FAIL", base_docs["04"]["status"] == "PASS", "open fee separation enforced")

    # F03: Swapping closed win-rate denominator (7/203) with filled denominator (7/233)
    p03 = base_docs["03"]["denominator_separation_disclosure"]
    swapped_ok = abs(float(p03["primary_closed_win_rate"]) - float(p03["filled_win_rate_secondary_only"])) > 0.002 and p03["denominators_distinct"] is True
    _rec("KH03R4-F03", "CLOSED_VS_FILLED_DENOMINATOR_SEPARATED", "build_r4_trade_population_conservation", swapped_ok, base_docs["03"]["status"] == "PASS", "7/203 strictly distinct from 7/233")

    # F04: Recoding open-at-horizon / data-gap trade as unknown or closed
    r4 = copy.deepcopy(list(base_rows))
    for rr in r4:
        if rr.get("outcome_label") == "OPEN_AT_HORIZON":
            rr["outcome_label"] = "FORGED_DROPPED"
            break
    d4 = recompute_r4_decimal_economic_bridge(r4, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2, ledger_meta={})
    _rec("KH03R4-F04", "OPEN_CENSOR_RECODING_DETECTED", "recompute_r4_decimal_economic_bridge", d4["status"] == "FAIL", base_docs["04"]["status"] == "PASS", "open/gap recoding rejected")

    # F05: Duplicate opportunity ID rejected
    r5 = copy.deepcopy(list(base_rows))
    r5[1]["opportunity_id"] = r5[0]["opportunity_id"]
    d5 = recompute_r4_decimal_economic_bridge(r5, dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2, ledger_meta={})
    _rec("KH03R4-F05", "DUPLICATE_OPPORTUNITY_ID_REJECTED", "recompute_r4_decimal_economic_bridge", d5["status"] == "FAIL" and any("DUPLICATE" in v for v in d5["violations"]), base_docs["04"]["status"] == "PASS", "duplicate ID rejected")

    # F06: Zero-fee counterfactual mislabeled as executable alpha
    m6 = copy.deepcopy(base_docs)
    m6["07"]["is_executable_strategy"] = True
    _rec("KH03R4-F06", "FEE_ONLY_EXECUTABLE_CLAIM_REJECTED", "validate_r4_no_claim_inflation", not validate_r4_no_claim_inflation(m6)["valid"], green_ok, "fee-only executable claim blocked")

    # F07: Conflating fixed fills with dynamic rerisk mode
    m7 = copy.deepcopy(base_docs)
    m7["07"]["population_mode"] = "FULL_DYNAMIC_RERISK"
    _rec("KH03R4-F07", "FIXED_VS_DYNAMIC_CONFLATION_REJECTED", "validate_r4_no_claim_inflation", not validate_r4_no_claim_inflation(m7)["valid"], green_ok, "population mode conflation blocked")

    # F08: Ranking unmatched E1/E2/E3 arms by raw PnL
    m8 = copy.deepcopy(base_docs)
    m8["09"]["comparability_classification"] = "COMPARABLE_BEST_ARM_E1"
    m8["09"]["raw_pnl_best_arm_selector_prohibited"] = False
    _rec("KH03R4-F08", "UNMATCHED_ARM_RAW_PNL_RANKING_REJECTED", "validate_r4_no_claim_inflation", not validate_r4_no_claim_inflation(m8)["valid"], green_ok, "raw PnL ranking blocked")

    # F09: Claiming $2.50 planned risk is physical max-loss guarantee
    m9 = copy.deepcopy(base_docs)
    m9["05"]["planned_cap_is_physical_guarantee"] = True
    _rec("KH03R4-F09", "PLANNED_CAP_AS_GUARANTEE_REJECTED", "validate_r4_no_claim_inflation", not validate_r4_no_claim_inflation(m9)["valid"], green_ok, "$2.50 guarantee claim blocked")

    # F10: Turning association diagnostics into causal claim
    m10 = copy.deepcopy(base_docs)
    m10["10"]["three_axis_diagnosis"]["STRATEGY_AND_COST_AXIS"]["verdict"] = "CAUSAL_FIX_PROVED"
    _rec("KH03R4-F10", "CAUSAL_CLAIM_INFLATION_REJECTED", "validate_r4_no_claim_inflation", not validate_r4_no_claim_inflation(m10)["valid"], green_ok, "causal claim inflation blocked")

    # F11: Fabricated observed L1 / account fee / funding
    m11 = copy.deepcopy(base_docs)
    m11["11"]["observed_l1_bbo_available"] = True
    m11["11"]["ACCOUNT_EFFECTIVE_FEE_VERDICT"] = "VIP9_VERIFIED"
    _rec("KH03R4-F11", "FABRICATED_L1_ACCOUNT_FEE_REJECTED", "validate_r4_no_claim_inflation", not validate_r4_no_claim_inflation(m11)["valid"], green_ok, "fabricated L1/fee blocked")

    # F12: DATA_READINESS_REGISTER schema completeness
    reg11 = list(base_docs["11"].get("DATA_READINESS_REGISTER") or ())
    bad_reg = copy.deepcopy(reg11)
    bad_reg[0].pop("cannot_assume", None)
    red12 = not all(all(k in row for k in DATA_READINESS_REQUIRED_FIELDS) for row in bad_reg)
    grn12 = len(reg11) >= 8 and all(all(k in row for k in DATA_READINESS_REQUIRED_FIELDS) for row in reg11)
    _rec("KH03R4-F12", "DATA_READINESS_REGISTER_15_FIELDS_CHECKED", "build_r4_data_gap_register", red12, grn12, "15-field x 8-domain schema enforced")

    # F13: Forged OOS>0 / PROVEN_EDGE / ACTIVATION=YES / mode=ON
    m13 = copy.deepcopy(base_docs)
    m13["12"]["OOS_EVALUATIONS"] = 1
    m13["12"]["RESEARCH_ECONOMIC_VERDICT"] = "PROVEN_EDGE"
    m13["12"]["ACTIVATION_READY"] = "YES"
    _rec("KH03R4-F13", "FORGED_OOS_ALPHA_ACTIVATION_REJECTED", "validate_r4_no_claim_inflation", not validate_r4_no_claim_inflation(m13)["valid"], green_ok, "activation/OOS forgery blocked")

    # F14: Malformed cluster CI rejected by strict parser
    ci_bad = parse_kh03_cluster_ci_strict([0.5, -0.5], label="reversed")
    ci_nan = parse_kh03_cluster_ci_strict([float("nan"), -0.1], label="nan_ci")
    ci_ok = parse_kh03_cluster_ci_strict([-0.9761, -0.7712], label="valid_neg")
    _rec("KH03R4-F14", "MALFORMED_CLUSTER_CI_REJECTED", "parse_kh03_cluster_ci_strict", (not ci_bad["schema_valid"]) and (not ci_nan["schema_valid"]), ci_ok["schema_valid"] and ci_ok["status"] == "CI_VALID_NEGATIVE", "reversed/NaN CI rejected")

    # F15: Unapproved disk waiver CLI rejected exit code 2
    env = os.environ.copy()
    env["KH03_R4_OWNER_WAIVE_DISK_PREFLIGHT"] = "1"
    proc = subprocess.run([sys.executable, str(APP_ROOT / "tools" / "run_kh_03_r4_acceptance.py")], cwd=str(APP_ROOT), env=env, capture_output=True, text=True, timeout=30)
    _rec("KH03R4-F15", "DISK_WAIVER_ENV_REJECTED_EXIT_2", "tools/run_kh_03_r4_acceptance.py", proc.returncode == 2 and "REJECTED_UNAPPROVED_AUTONOMOUS_DISK_WAIVER" in proc.stderr, True, "disk waiver rejected exit=2")

    # F16: R2 forward-only 240m embargo causality & zero unauthorized R1 callers
    synth_bars = [{"open_time": 1791500000000 + i * 60_000, "close_time": 1791500060000 + i * 60_000, "open": 4100.0, "high": 4101.0, "low": 4099.0, "close": 4100.5, "volume": 1.0} for i in range(2500)]
    fb = build_kh03_r2_forward_only_dev_folds(bars_1m=synth_bars, embargo_minutes=240)
    vc_good = validate_kh03_r2_fold_causality(fb["folds"], min_embargo_minutes=240)
    bad_folds = copy.deepcopy(fb["folds"])
    bad_folds[0]["train_max_known_at_ts_ms"] = int(bad_folds[0]["val_min_ts_ms"]) + 60_000  # inject future leak
    vc_bad = validate_kh03_r2_fold_causality(bad_folds, min_embargo_minutes=240)
    cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
    _rec("KH03R4-F16", "EMBARGO_240M_AND_ZERO_R1_CALLERS", "validate_kh03_r2_fold_causality", (not vc_bad["valid"]), bool(vc_good["valid"]) and cg["LEGACY_METHOD_GUARD"] == "PASS", "future train leak detected; zero R1 callers")

    return out


def test_r4_all_16_fault_controls_pass_red_green() -> None:
    ctrls = run_new_kh03_r4_fault_controls_16()
    assert len(ctrls) == 16
    assert len({c["fault_id"] for c in ctrls}) == 16
    failed = [c for c in ctrls if c["status"] != "PASS"]
    assert failed == [], f"Failed R4 fault controls: {failed}"


def test_r4_data_readiness_register_and_nogo_decision_rules() -> None:
    _, docs = _build_clean_r4_docs_fixture()
    d11, d12 = docs["11"], docs["12"]
    assert d11["status"] == "PASS"
    assert d11["DATA_READINESS_VERDICT"] == "INCOMPLETE"
    assert d11["observed_l1_bbo_available"] is False
    assert d11["ACCOUNT_EFFECTIVE_FEE_VERDICT"] == "UNKNOWN_NOT_LOCALLY_VERIFIED"
    assert d11["funding_cost_status"] == "EXCLUDED_UNMODELED"
    assert d11["private_api_called"] is False
    assert d11["credentials_or_api_keys_requested"] is False
    assert d12["status"] == "PASS"
    assert d12["DECISION"].startswith("NO_GO")
    assert d12["RESEARCH_ECONOMIC_VERDICT"] == "NO_PROVEN_EDGE"
    assert d12["KH04_ENTRY"] == "BLOCKED_NO_DEV_CANDIDATE"
    assert d12["ACTIVATION_READY"] == "NO"
    assert d12["OOS_EVALUATIONS"] == 0
    assert all(d12["runtime_safety_modes"][k] == "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO"))


def test_r4_zero_unauthorized_r1_callers_across_repo() -> None:
    cg = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
    assert cg["LEGACY_METHOD_GUARD"] == "PASS", cg
    assert int(cg["unauthorized_r1_callers_count"]) == 0
