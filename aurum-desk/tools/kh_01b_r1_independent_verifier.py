#!/usr/bin/env python3
"""
aurum-desk/tools/kh_01b_r1_independent_verifier.py

AURUM DESK — KH-01B.R1 Separate-OS-Process Independent Disk Verifier (§D3.C & §G14).
Runs in a fresh OS process (`verifier_pid != orchestrator_pid`), reopens closed JSON
artifacts, `.jsonl.gz` episode/casebook/filter ledgers, JUnit XMLs, and dynamic receipts
directly from disk, recomputes episode PnL/fees/1-position non-overlap/hash integrity
without trusting orchestrator claims, and seals `17_independent_verifier_sha256_receipts_manifest.json`.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Mapping

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.kh_01b_entry_unicorn_research_service import FILL_MODES_KH01B
from services.kh_01b_r1_entry_edge_discovery_service import (
    CANONICAL_DATASET_SHA256_KH01B_R1,
    MANDATORY_BRANCH_KH01B_R1,
    MANDATORY_KH01B_R1_ENGINEERING_GATES,
    REQUIRED_ARTIFACTS_KH01B_R1,
    SCHEMA_VERSION_KH01B_R1,
    SPEC_VERSION_KH01B_R1,
    evaluate_kh01b_r1_engineering_and_economic_gates_from_disk,
    verify_kh01b_r1_cross_view_consistency,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import EvidenceReceiptV17931219
from tools.fresh_junit_io import write_json_durable_atomic

MANIFEST_17_NAME = "17_independent_verifier_sha256_receipts_manifest.json"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for ln in fh:
            s = ln.strip()
            if s:
                yield json.loads(s)


def _verify_r1_ast_callables() -> List[str]:
    viols: List[str] = []
    svc_p = BACKEND_ROOT / "services/kh_01b_r1_entry_edge_discovery_service.py"
    run_p = APP_ROOT / "tools/run_kh_01b_r1_verification.py"
    col_p = APP_ROOT / "tools/kh_01b_r1_collect_market_data.py"
    for p in (svc_p, run_p, col_p):
        if not p.is_file():
            viols.append(f"MISSING_R1_MODULE:{p.name}")
    if viols:
        return viols

    svc_tree = ast.parse(svc_p.read_text(encoding="utf-8-sig"))
    run_tree = ast.parse(run_p.read_text(encoding="utf-8-sig"))
    svc_fns = {n.name for n in ast.walk(svc_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    run_fns = {n.name for n in ast.walk(run_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    req_svc = {
        "load_and_verify_kh01b_r1_sources",
        "build_kh01b_r1_preentry_outcome_attribution",
        "audit_kh01b_r1_strategy_mechanism_by_model_direction_regime",
        "pre_register_kh01b_r1_theses",
        "extract_kh01b_r1_causal_regime_features",
        "evaluate_kh01b_r1_entry_variant",
        "replay_kh01b_r1_frozen_variants",
        "compute_kh01b_r1_edge_uncertainty_and_research_verdict",
        "validate_kh01b_r1_data_provenance_and_escrow",
        "evaluate_kh01b_r1_engineering_and_economic_gates_from_disk",
        "run_kh01b_r1_real_fault_injections",
    }
    miss_svc = sorted(req_svc - svc_fns)
    if miss_svc:
        viols.append(f"MISSING_R1_SERVICE_CALLABLES:{miss_svc}")

    req_run = {
        "run_kh01b_r1_bounded_async_orchestrator",
        "verify_kh01b_r1_resource_cleanup",
    }
    miss_run = sorted(req_run - run_fns)
    if miss_run:
        viols.append(f"MISSING_R1_RUNNER_CALLABLES:{miss_run}")
    return viols


def verify_kh_01b_r1_run_dir_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_git_source_freeze: bool = True,
) -> Dict[str, Any]:
    run_dir = run_dir.resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"RUN_DIR_NOT_FOUND:{run_dir}"],
        }

    # 1. Verify every required artifact exists & is non-empty
    for rel_req in REQUIRED_ARTIFACTS_KH01B_R1:
        if rel_req == MANIFEST_17_NAME:
            continue
        fp = run_dir / rel_req
        if not fp.is_file() or fp.stat().st_size == 0:
            violations.append(f"MISSING_REQUIRED_ARTIFACT:{rel_req}")

    if violations:
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    try:
        f00 = json.loads((run_dir / "00_preflight_branch_source_dataset_invariants.json").read_text("utf-8"))
        f01 = json.loads((run_dir / "01_function_callgraph_keep_as_is_and_no_production_change.json").read_text("utf-8"))
        f02 = json.loads((run_dir / "02_frozen_hypothesis_budget_and_expected_falsification.json").read_text("utf-8"))
        f03 = json.loads((run_dir / "03_si0_242_episode_preentry_vs_expost_loss_attribution.json").read_text("utf-8"))
        f06 = json.loads((run_dir / "06_extended_dev_dataset_provenance_gap_and_quote_capability.json").read_text("utf-8"))
        f07 = json.loads((run_dir / "07_future_oos_sealed_escrow_registry_no_evaluation.json").read_text("utf-8"))
        f08 = json.loads((run_dir / "08_E00_E06_variant_conservation_and_rejection_reason_ledger.json").read_text("utf-8"))
        f09 = json.loads((run_dir / "09_matched_baseline_and_chronological_1pos_3fill_economics.json").read_text("utf-8"))
        f10 = json.loads((run_dir / "10_cost_stress_cluster_bootstrap_reused_purged_folds.json").read_text("utf-8"))
        f11 = json.loads((run_dir / "11_D4_strict_unchanged_and_unicorn_adjacent_isolation.json").read_text("utf-8"))
        f12 = json.loads((run_dir / "12_full_regression_junit_shard_exits_bijection.json").read_text("utf-8"))
        f14 = json.loads((run_dir / "14_disk_ram_cpu_pid_atomic_write_cleanup.json").read_text("utf-8"))
        f16 = json.loads((run_dir / "16_full_baseline_decision_trade_action_and_adapter_parity.json").read_text("utf-8"))
        f18 = json.loads((run_dir / "18_final_orthogonal_engineering_economics_data_oos_verdicts.json").read_text("utf-8"))
        readme_txt = (run_dir / "README_KH01B_R1.md").read_text("utf-8")
        handover_txt = (run_dir / "HANDOVER_KH01B_R1_VI.txt").read_text("utf-8")

        casebook_rows = list(_iter_jsonl_gz(run_dir / "ledgers/kh01b_r1_si0_242_casebook.jsonl.gz"))
        episode_rows = list(_iter_jsonl_gz(run_dir / "ledgers/kh01b_r1_all_variants_episode_ledgers.jsonl.gz"))
        filter_rows = list(_iter_jsonl_gz(run_dir / "ledgers/kh01b_r1_candidate_filter_decisions_summary.jsonl.gz"))
    except Exception as exc:
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"ARTIFACT_PARSE_OR_GZIP_CORRUPTION:{type(exc).__name__}:{exc}"],
        }

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    orchestrator_pid = int(f00.get("orchestrator_pid") or -1)
    if verifier_pid == orchestrator_pid:
        violations.append(f"VERIFIER_NOT_SEPARATE_OS_PROCESS:{verifier_pid}")

    # 2. Branch, canonical dataset & source freeze checks
    if f00.get("branch") != MANDATORY_BRANCH_KH01B_R1:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if f00.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH01B_R1:
        violations.append(f"DATASET_SHA_MISMATCH:{f00.get('canonical_dataset_sha256')}")
    ds_disk = APP_ROOT / "backtest/historical_dataset_XAUUSDT.json.gz"
    if ds_disk.is_file() and _file_sha256(ds_disk) != CANONICAL_DATASET_SHA256_KH01B_R1:
        violations.append("CANONICAL_DATASET_MODIFIED_ON_DISK")
    if int(f01.get("existing_production_or_prior_kh_files_modified_count", 1)) != 0:
        violations.append("EXISTING_PRODUCTION_OR_PRIOR_KH_FILES_MODIFIED")

    if check_git_source_freeze:
        sf = verify_source_freeze_v17931211(REPO_ROOT, tested_sha)
        if sf.get("status") != "PASS":
            violations.extend(f"SOURCE_FREEZE:{v}" for v in (sf.get("violations") or []))
        violations.extend(_verify_r1_ast_callables())

    # 3. Pre-registration manifest hash & <= 6 new hypotheses check
    f02_body = {k: v for k, v in f02.items() if k not in ("manifest_sha256", "run_id", "tested_executable_sha")}
    if canonical_hash(f02_body) != f02.get("manifest_sha256"):
        violations.append("PREREGISTERED_R1_MANIFEST_HASH_TAMPERED")
    if int(f02.get("registered_new_hypotheses_count") or 99) > 6:
        violations.append("HYPOTHESIS_BUDGET_EXCEEDED_6")

    # 4. Verify 242 casebook rows & strict pre-entry vs ex-post separation
    if len(casebook_rows) != int(f03.get("scheduled_total") or -1):
        violations.append(f"CASEBOOK_COUNT_MISMATCH:{len(casebook_rows)}!={f03.get('scheduled_total')}")
    for cb in casebook_rows:
        cb_copy = {k: v for k, v in cb.items() if k != "row_sha256"}
        if canonical_hash(cb_copy) != cb.get("row_sha256"):
            violations.append(f"CASEBOOK_ROW_HASH_TAMPERED:{cb.get('opportunity_id')}")
            break
        if cb.get("evidence_completeness") != "PARTIAL":
            violations.append(f"CASEBOOK_PARTIAL_UPGRADED_ILLEGALLY:{cb.get('opportunity_id')}")
            break
        pred = cb.get("PREDICTIVE_PRE_ENTRY") or {}
        if int(pred.get("max_known_at_ms") or 0) > int(cb.get("decision_timestamp_ms") or 0):
            violations.append(f"CASEBOOK_FUTURE_KNOWN_AT:{cb.get('opportunity_id')}")
            break

    # 5. Verify filter summary ledger & candidate conservation
    arms_08 = f08.get("arms_conservation") or {}
    for fr_row in filter_rows:
        r_copy = {k: v for k, v in fr_row.items() if k != "row_sha256"}
        if canonical_hash(r_copy) != fr_row.get("row_sha256"):
            violations.append(f"FILTER_ROW_HASH_TAMPERED:{fr_row.get('short_code')}")
        if int(fr_row.get("max_known_at_minus_decision_ms") or 0) > 0:
            violations.append(f"FILTER_LEDGER_FUTURE_KNOWN_AT:{fr_row.get('short_code')}")
        sc = str(fr_row.get("short_code") or "")
        if sc in arms_08:
            if int(fr_row.get("eligible_candidates_count", -1)) + int(fr_row.get("rejected_candidates_count", -1)) != int(fr_row.get("population_total", -2)):
                violations.append(f"FILTER_LEDGER_CONSERVATION_MISMATCH:{sc}")

    # 6. Recompute episode accounting, fees & 1-pos non-overlap directly from ledger on disk
    by_arm_mode: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for ep in episode_rows:
        ep_no_sha = {k: v for k, v in ep.items() if k != "row_sha256"}
        if canonical_hash(ep_no_sha) != ep.get("row_sha256"):
            violations.append(f"EPISODE_LEDGER_HASH_TAMPERED:{ep.get('short_code')}:{ep.get('opportunity_id')}")
            break
        if int(ep.get("known_at_ms") or 0) > int(ep.get("decision_timestamp_ms") or 0):
            violations.append(f"EPISODE_FUTURE_KNOWN_AT:{ep.get('short_code')}:{ep.get('opportunity_id')}")
            break
        if float(ep.get("planned_net_risk_usd") or 0.0) > 2.5001:
            violations.append(f"EPISODE_RISK_CAP_EXCEEDED:{ep.get('opportunity_id')}:{ep.get('planned_net_risk_usd')}")
            break
        if ep.get("is_synthetic_fixture") is True:
            violations.append(f"SYNTHETIC_TRADE_INJECTED_INTO_HISTORICAL_PNL:{ep.get('opportunity_id')}")
            break
        by_arm_mode.setdefault(str(ep.get("short_code") or ""), {}).setdefault(str(ep.get("fill_mode") or ""), []).append(ep)

    arms_09 = f09.get("arms_summary") or {}
    arms_10 = f10.get("arms_economics") or {}
    for sc_k, sum_09 in arms_09.items():
        if sc_k not in arms_10:
            violations.append(f"MISSING_ARM_IN_10:{sc_k}")
            continue
        ec_k = arms_10[sc_k]
        if ec_k.get("research_verdict") == "NOT_RUN_JUSTIFIED":
            continue
        for fm in FILL_MODES_KH01B:
            eps_vm = sorted(
                by_arm_mode.get(sc_k, {}).get(fm, []),
                key=lambda r: (int(r.get("decision_timestamp_ms") or 0), str(r.get("opportunity_id") or "")),
            )
            active_rel = 0
            for e_r in eps_vm:
                d_ts = int(e_r.get("decision_timestamp_ms") or 0)
                if active_rel > 0 and d_ts < active_rel:
                    violations.append(f"ONE_POSITION_OVERLAP:{sc_k}:{fm}:{e_r.get('opportunity_id')}")
                    break
                if e_r.get("filled"):
                    active_rel = max(active_rel, int(e_r.get("release_ts_ms") or 0))

            closed_vm = [e_r for e_r in eps_vm if e_r.get("filled") and e_r.get("outcome_label") in ("WIN", "LOSS")]
            recomp_net = round(sum(float(e_r.get("realized_net_pnl_usd") or 0.0) for e_r in closed_vm), 4)
            rep_fm = (sum_09.get("fill_mode_summaries") or {}).get(fm) or {}
            if len(closed_vm) != int(rep_fm.get("closed_trades_count", -1)):
                violations.append(f"CLOSED_COUNT_TAMPERED:{sc_k}:{fm}:{len(closed_vm)}!={rep_fm.get('closed_trades_count')}")
            if abs(recomp_net - float(rep_fm.get("total_realized_net_usd", 999999.0))) > 1e-3:
                violations.append(f"NET_PNL_TAMPERED_IN_09:{sc_k}:{fm}:{recomp_net}!={rep_fm.get('total_realized_net_usd')}")
            if fm == "NEXT_BAR_OPEN_BID_ASK":
                if abs(recomp_net - float(ec_k.get("net_pnl_1x_nb_usd", 999999.0))) > 1e-3:
                    violations.append(f"NET_PNL_TAMPERED_IN_10:{sc_k}:{recomp_net}!={ec_k.get('net_pnl_1x_nb_usd')}")
                if len(closed_vm) == 0 and ec_k.get("expectancy_1x_nb_r") is not None:
                    violations.append(f"ZERO_TRADES_NON_NULL_EXPECTANCY:{sc_k}:{ec_k.get('expectancy_1x_nb_r')}")

    # 7. Check OOS escrow, D4 isolation, parity & safety locks
    if f07.get("FRESH_OOS_VERDICT") not in ("BLOCKED_NO_UNTOUCHED_DATA", "SEALED_UNTOUCHED_NOT_EVALUATED"):
        violations.append(f"ILLEGAL_FRESH_OOS_VERDICT_IN_07:{f07.get('FRESH_OOS_VERDICT')}")
    if int(f07.get("strategy_evaluation_count_total", 1)) != 0:
        violations.append("NONZERO_FUTURE_OOS_EVALUATION_COUNT")
    if f11.get("ontology_isolation_verified") is not True or int(f11.get("adjacent_counted_toward_d4_count", 1)) != 0:
        violations.append("D4_ONTOLOGY_ISOLATION_BROKEN")
    if f11.get("d4_historical_expectancy_r") is not None:
        violations.append("D4_ZERO_TRADE_EXPECTANCY_NOT_NULL")
    if f16.get("status") != "PASS":
        violations.append("BASELINE_PARITY_16_NOT_PASS")
    ov_18 = f18.get("orthogonal_verdicts") or {}
    if ov_18.get("ACTIVATION_READY") != "NO":
        violations.append("ACTIVATION_READY_NOT_NO")
    modes_18 = ov_18.get("RUNTIME_SAFETY_MODES") or {}
    if any(modes_18.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
        violations.append(f"UNSAFE_RUNTIME_MODES:{modes_18}")

    # Verify optional acquired dev snapshot hash if online acquisition claimed PASS
    if f06.get("DATA_ACQUISITION_VERDICT") == "PASS_VERIFIED_WITH_ORIGIN_RECEIPTS":
        ext_info = f06.get("bitget_source_capability_and_extended_dev") or {}
        dev_rel = str(ext_info.get("dev_extended_snapshot_rel_path") or "")
        rcp_rel = str(ext_info.get("origin_http_receipts_rel_path") or "")
        if not dev_rel or not (run_dir / dev_rel).is_file() or _file_sha256(run_dir / dev_rel) != ext_info.get("dev_extended_snapshot_sha256"):
            violations.append("ACQUIRED_DEV_SNAPSHOT_MISSING_OR_HASH_MISMATCH")
        if not rcp_rel or not (run_dir / rcp_rel).is_file() or _file_sha256(run_dir / rcp_rel) != ext_info.get("origin_http_receipts_sha256"):
            violations.append("ORIGIN_HTTP_RECEIPTS_MISSING_OR_HASH_MISMATCH")

    # 8. Closed JUnit XML bijection on disk
    jb = verify_fresh_junit_bijection_v17931211(
        run_dir,
        f12.get("collection_manifest") or {},
        f12.get("shard_manifest") or {},
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        mandatory_node_prefixes=(
            "backend/tests/test_kh_01b_r1",
            "backend/tests/test_kh_01b",
            "backend/tests/test_kh_01a",
            "backend/tests/test_v17_9_3_1_2",
        ),
        require_shard_schedule=True,
    )
    if jb.get("status") != "PASS":
        violations.extend(f"JUNIT_BIJECTION:{v}" for v in (jb.get("violations") or []))

    # 9. Dynamic gate receipts on disk + independent gate re-evaluation + cross-view check
    rcpt_dir = run_dir / "receipts"
    receipts_read: Dict[str, EvidenceReceiptV17931219] = {}
    for gid in MANDATORY_KH01B_R1_ENGINEERING_GATES:
        rp = rcpt_dir / f"{gid}.json"
        if not rp.is_file():
            violations.append(f"MISSING_MANDATORY_GATE_RECEIPT:{gid}")
            continue
        r_doc = json.loads(rp.read_text("utf-8")).get("receipt") or {}
        r_obj = EvidenceReceiptV17931219(**r_doc)
        receipts_read[gid] = r_obj
        if not r_obj.is_valid_pass(
            expected_run_id=run_id,
            expected_tested_sha=tested_sha,
            run_dir=run_dir,
            reject_uniform_placeholder_hash=True,
        ):
            violations.append(f"INVALID_GATE_RECEIPT:{gid}:{r_obj.violations}")

    re_eval_18 = evaluate_kh01b_r1_engineering_and_economic_gates_from_disk(run_dir, run_id, tested_sha)
    if re_eval_18["orthogonal_verdicts"]["ENGINEERING_VERDICT"] != "PASS":
        violations.append("INDEPENDENT_GATE_REEVALUATION_ENGINEERING_NOT_PASS")
    if ov_18.get("ENGINEERING_VERDICT") != re_eval_18["orthogonal_verdicts"]["ENGINEERING_VERDICT"]:
        violations.append("FORGED_ENGINEERING_VERDICT_IN_18")
    if ov_18.get("KH01C_ADVANCEMENT_VERDICT") != re_eval_18["orthogonal_verdicts"]["KH01C_ADVANCEMENT_VERDICT"]:
        violations.append("FORGED_KH01C_VERDICT_IN_18")

    cv = verify_kh01b_r1_cross_view_consistency(
        readme_text=readme_txt,
        handover_text=handover_txt,
        verdicts_18=f18,
        economics_10=f10,
        junit_12=f12,
    )
    if cv["status"] != "PASS":
        violations.extend(f"CROSS_VIEW:{v}" for v in cv["violations"])

    # 10. Resource & disk guard check
    if not (1 <= int(f14.get("max_children_observed") or 0) <= 4):
        violations.append(f"WORKER_CAP_EXCEEDED:{f14.get('max_children_observed')}")
    if int(f14.get("orphan_pids_at_cleanup_count", 1)) != 0:
        violations.append(f"ORPHAN_PIDS_DETECTED:{f14.get('orphan_pids_at_cleanup_count')}")
    if f14.get("effective_disk_reserve_breached") is not False:
        violations.append("EFFECTIVE_DISK_RESERVE_BREACHED")
    if float(f14.get("min_free_disk_gb_observed") or 0.0) < float(f14.get("hard_abort_floor_gib") or 10.0):
        violations.append(f"DISK_BELOW_HARD_FLOOR:{f14.get('min_free_disk_gb_observed')}")

    # 11. Seal 17_independent_verifier_sha256_receipts_manifest.json
    delivered_files: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.name == MANIFEST_17_NAME:
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        delivered_files[rel] = {
            "byte_size": int(p.stat().st_size),
            "sha256": _file_sha256(p),
        }

    manifest_17_doc = {
        "spec_version": SPEC_VERSION_KH01B_R1,
        "schema_version": SCHEMA_VERSION_KH01B_R1,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1,
        "independent_verifier": {
            "verifier_script": "aurum-desk/tools/kh_01b_r1_independent_verifier.py",
            "verifier_pid": verifier_pid,
            "orchestrator_pid": orchestrator_pid,
            "separate_os_process_verified": verifier_pid != orchestrator_pid,
            "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "PASS" if not violations else "FAIL",
            "violations_count": len(violations),
            "violations": violations,
        },
        "gate_receipts_readback": {k: asdict(v) for k, v in sorted(receipts_read.items())},
        "circular_hash_exclusion_policy": (
            f"{MANIFEST_17_NAME} hashes every other file in {run_dir.name} and excludes only itself."
        ),
        "delivered_files_count": len(delivered_files),
        "delivered_files_aggregate_sha256": canonical_hash(delivered_files),
        "delivered_files": delivered_files,
    }
    manifest_sha = write_json_durable_atomic(run_dir / MANIFEST_17_NAME, manifest_17_doc)

    return {
        "spec_version": SPEC_VERSION_KH01B_R1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "separate_os_process_verified": verifier_pid != orchestrator_pid,
        "independent_verifier_status": "PASS" if not violations else "FAIL",
        "delivered_files_count": len(delivered_files),
        "manifest_17_sha256": manifest_sha,
        "violations": violations,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="KH-01B.R1 Separate-OS-Process Independent Disk Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-git-freeze-check", action="store_true")
    args = ap.parse_args()

    res = verify_kh_01b_r1_run_dir_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_git_source_freeze=not args.skip_git_freeze_check,
    )
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res["independent_verifier_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
