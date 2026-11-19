#!/usr/bin/env python3
"""
aurum-desk/tools/kh_01b_independent_verifier.py

AURUM DESK — KH-01B Separate-OS-Process Independent Disk Verifier (§14).
Runs in a fresh OS process (`verifier_pid != orchestrator_pid`), parses closed disk
artifacts and `.jsonl.gz` episode ledgers directly from disk, recomputes variant PnL,
chronological 1-position non-overlap, ontology isolation, as-of timestamps, AST F01-F17
callables, JUnit XML bijection, dynamic gate receipts, and seals
`18_independent_verifier_manifest_receipts_hashes.json`.
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
from typing import Any, Dict, Iterable, List, Mapping, Set

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.kh_01b_entry_unicorn_research_service import (
    CANONICAL_DATASET_SHA256_KH01B,
    FILL_MODES_KH01B,
    MANDATORY_BRANCH_KH01B,
    MANDATORY_KH01B_TECHNICAL_GATES,
    REQUIRED_ARTIFACTS_KH01B,
    SCHEMA_VERSION_KH01B,
    SPEC_VERSION_KH01B,
    evaluate_kh01b_orthogonal_gates_from_disk,
    verify_kh01b_cross_view_consistency,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_fresh_junit_bijection_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import EvidenceReceiptV17931219
from tools.fresh_junit_io import write_json_durable_atomic

MANIFEST_18_NAME = "18_independent_verifier_manifest_receipts_hashes.json"


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


def _verify_f01_to_f17_ast_callables() -> List[str]:
    viols: List[str] = []
    svc_path = BACKEND_ROOT / "services/kh_01b_entry_unicorn_research_service.py"
    run_path = APP_ROOT / "tools/run_kh_01b_verification.py"
    if not svc_path.is_file() or not run_path.is_file():
        return ["MISSING_KH01B_SERVICE_OR_RUNNER_SOURCE"]

    svc_tree = ast.parse(svc_path.read_text(encoding="utf-8-sig"))
    run_tree = ast.parse(run_path.read_text(encoding="utf-8-sig"))
    svc_fns = {n.name for n in ast.walk(svc_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    run_fns = {n.name for n in ast.walk(run_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    req_svc = {
        "load_and_verify_kh01b_reference_baseline",
        "pre_register_kh01b_hypotheses",
        "extract_kh01b_causal_feature_frame",
        "evaluate_htf_alignment_hypothesis",
        "evaluate_macro_window_hypothesis",
        "evaluate_sweep_mss_displacement_hypothesis",
        "evaluate_source_tf_target_reachability_hypothesis",
        "evaluate_strict_true_unicorn_hypothesis",
        "evaluate_unicorn_adjacent_research_hypothesis",
        "evaluate_kh01b_variant_on_matched_candidate_universe",
        "run_kh01b_orthogonal_ablation_matrix",
        "compute_kh01b_research_economics_and_uncertainty",
        "audit_kh01b_true_unicorn_positive_and_negative_fixtures",
        "verify_kh01b_full_baseline_and_adapter_parity",
        "evaluate_kh01b_orthogonal_gates_from_disk",
        "render_kh01b_readme_handover_vi",
        "verify_kh01b_cross_view_consistency",
    }
    missing_svc = sorted(req_svc - svc_fns)
    if missing_svc:
        viols.append(f"MISSING_F01_F16_CALLABLES:{missing_svc}")

    req_run = {
        "run_kh01b_bounded_async_test_and_replay_orchestrator",
        "verify_resource_cleanup",
    }
    missing_run = sorted(req_run - run_fns)
    if missing_run:
        viols.append(f"MISSING_F17_ORCHESTRATOR_CALLABLES:{missing_run}")
    return viols


def verify_kh_01b_run_dir_from_disk(
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

    # 1. Verify all required artifacts exist & are non-empty
    for rel_req in REQUIRED_ARTIFACTS_KH01B:
        if rel_req == MANIFEST_18_NAME:
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
        f00 = json.loads((run_dir / "00_preflight_source_config_dataset_freeze.json").read_text(encoding="utf-8"))
        f02 = json.loads((run_dir / "02_pre_registered_hypothesis_manifest_and_search_budget.json").read_text(encoding="utf-8"))
        f03 = json.loads((run_dir / "03_causal_feature_sources_unknown_invalid_coverage.json").read_text(encoding="utf-8"))
        f09 = json.loads((run_dir / "09_strict_D4_and_adjacent_B09_B10_detector_funnel.json").read_text(encoding="utf-8"))
        f10 = json.loads((run_dir / "10_all_variant_comparable_replays_3fill_1pos_ledgers.json").read_text(encoding="utf-8"))
        f11 = json.loads((run_dir / "11_net_economics_cost_stress_cluster_uncertainty_reused_folds.json").read_text(encoding="utf-8"))
        f12 = json.loads((run_dir / "12_full_baseline_and_three_adapter_parity.json").read_text(encoding="utf-8"))
        f14 = json.loads((run_dir / "14_full_pytest_junit_shards_process_exit_and_bijection.json").read_text(encoding="utf-8"))
        f16 = json.loads((run_dir / "16_resource_disk_ram_cpu_pid_cleanup.json").read_text(encoding="utf-8"))
        f17 = json.loads((run_dir / "17_fresh_oos_registry_and_economic_verdict_separation.json").read_text(encoding="utf-8"))
        f19 = json.loads((run_dir / "19_final_orthogonal_gates_blockers_and_kh01c_candidates.json").read_text(encoding="utf-8"))
        readme_txt = (run_dir / "README_KH01B.md").read_text(encoding="utf-8")
        handover_txt = (run_dir / "HANDOVER_KH01B_VI.txt").read_text(encoding="utf-8")
        episode_rows = list(_iter_jsonl_gz(run_dir / "ledgers/kh01b_all_variants_episode_ledgers.jsonl.gz"))
        filter_rows = list(_iter_jsonl_gz(run_dir / "ledgers/kh01b_candidate_filter_decisions_summary.jsonl.gz"))
    except Exception as exc:
        return {
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"ARTIFACT_PARSE_OR_DECOMPRESS_ERROR:{type(exc).__name__}:{exc}"],
        }

    run_id = expected_run_id or str(f00.get("run_id") or "")
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    orchestrator_pid = int(f00.get("orchestrator_pid") or -1)
    if verifier_pid == orchestrator_pid:
        violations.append(f"VERIFIER_NOT_SEPARATE_OS_PROCESS:{verifier_pid}")

    # 2. Source & dataset freeze checks
    if f00.get("branch") != MANDATORY_BRANCH_KH01B:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if f00.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH01B:
        violations.append(f"DATASET_SHA_MISMATCH:{f00.get('canonical_dataset_sha256')}")
    ds_disk = APP_ROOT / "backtest/historical_dataset_XAUUSDT.json.gz"
    if ds_disk.is_file() and _file_sha256(ds_disk) != CANONICAL_DATASET_SHA256_KH01B:
        violations.append("CANONICAL_DATASET_BYTES_MODIFIED_ON_DISK")

    if check_git_source_freeze:
        sf = verify_source_freeze_v17931211(REPO_ROOT, tested_sha)
        if sf.get("status") != "PASS":
            violations.extend(f"SOURCE_FREEZE:{v}" for v in (sf.get("violations") or []))
        violations.extend(_verify_f01_to_f17_ast_callables())

    # 3. Pre-registration hash integrity
    f02_body = {k: v for k, v in f02.items() if k not in ("manifest_sha256", "run_id", "tested_executable_sha")}
    if canonical_hash(f02_body) != f02.get("manifest_sha256"):
        violations.append("PREREGISTERED_MANIFEST_HASH_TAMPERED")

    # 4. Feature clock & row-hash integrity on candidate filter summary ledger
    if len(filter_rows) != 13:
        violations.append(f"FILTER_SUMMARY_LEDGER_COUNT_NOT_13:{len(filter_rows)}")
    for fr_row in filter_rows:
        r_copy = {k: v for k, v in fr_row.items() if k != "row_sha256"}
        if canonical_hash(r_copy) != fr_row.get("row_sha256"):
            violations.append(f"FILTER_SUMMARY_ROW_HASH_TAMPERED:{fr_row.get('short_code')}")
        if int(fr_row.get("max_known_at_minus_decision_ms") or 0) > 0:
            violations.append(f"FUTURE_KNOWN_AT_DETECTED_IN_LEDGER:{fr_row.get('short_code')}")
        if fr_row.get("short_code") == "B10":
            if fr_row.get("ontology") != "ADJACENT_FVG_SWEEP_MSS" or fr_row.get("counts_toward_d4") is not False:
                violations.append("ONTOLOGY_TAMPER_ADJACENT_RELABELED_AS_TRUE_UNICORN")

    if int(f03.get("future_bar_or_known_at_violations_count") or 0) != 0:
        violations.append("F03_FUTURE_KNOWN_AT_VIOLATION")

    # 5. Recompute economics & 1-position non-overlap directly from episode ledger rows on disk!
    by_var_mode: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for ep in episode_rows:
        ep_no_sha = {k: v for k, v in ep.items() if k != "row_sha256"}
        if canonical_hash(ep_no_sha) != ep.get("row_sha256"):
            violations.append(f"EPISODE_LEDGER_ROW_HASH_TAMPERED:{ep.get('short_code')}:{ep.get('opportunity_id')}")
            break
        if int(ep.get("known_at_ms") or 0) > int(ep.get("decision_timestamp_ms") or 0):
            violations.append(f"EPISODE_FUTURE_KNOWN_AT:{ep.get('opportunity_id')}")
            break
        if float(ep.get("planned_net_risk_usd") or 0.0) > 2.5001:
            violations.append(f"EPISODE_RISK_CAP_EXCEEDED:{ep.get('opportunity_id')}:{ep.get('planned_net_risk_usd')}")
            break
        sc_e = str(ep.get("short_code") or "")
        fm_e = str(ep.get("fill_mode") or "")
        by_var_mode.setdefault(sc_e, {}).setdefault(fm_e, []).append(ep)

    vs_10 = f10.get("variants_summary") or {}
    ec_11 = f11.get("variants_economics") or {}
    for sc_i in [f"B{i:02d}" for i in range(13)]:
        if sc_i not in vs_10 or sc_i not in ec_11:
            violations.append(f"MISSING_VARIANT_IN_10_OR_11:{sc_i}")
            continue
        if sc_i == "B10" and (vs_10[sc_i].get("ontology") != "ADJACENT_FVG_SWEEP_MSS" or ec_11[sc_i].get("ontology") != "ADJACENT_FVG_SWEEP_MSS"):
            violations.append("ONTOLOGY_TAMPER_B10_IN_SUMMARY")
        for fm_k in FILL_MODES_KH01B:
            eps_vm = sorted(
                by_var_mode.get(sc_i, {}).get(fm_k, []),
                key=lambda r: (int(r.get("decision_timestamp_ms") or 0), str(r.get("opportunity_id") or "")),
            )
            # Check strict 1-position chronological non-overlap
            active_rel = 0
            for e_r in eps_vm:
                d_ts = int(e_r.get("decision_timestamp_ms") or 0)
                if active_rel > 0 and d_ts < active_rel:
                    violations.append(f"ONE_POSITION_OVERLAP_DETECTED:{sc_i}:{fm_k}:{e_r.get('opportunity_id')}")
                    break
                if e_r.get("filled"):
                    active_rel = max(active_rel, int(e_r.get("release_ts_ms") or 0))

            closed_vm = [e_r for e_r in eps_vm if e_r.get("filled") and e_r.get("outcome_label") in ("WIN", "LOSS")]
            recomp_net = round(sum(float(e_r.get("realized_net_pnl_usd") or 0.0) for e_r in closed_vm), 4)
            rep_sum = (vs_10[sc_i].get("fill_mode_summaries") or {}).get(fm_k) or {}
            if len(closed_vm) != int(rep_sum.get("closed_trades_count", -1)):
                violations.append(f"CLOSED_COUNT_MISMATCH_VS_LEDGER:{sc_i}:{fm_k}:{len(closed_vm)}!={rep_sum.get('closed_trades_count')}")
            if abs(recomp_net - float(rep_sum.get("total_realized_net_usd", 999999.0))) > 1e-3:
                violations.append(f"NET_PNL_TAMPERED_VS_LEDGER:{sc_i}:{fm_k}:ledger={recomp_net}!=reported={rep_sum.get('total_realized_net_usd')}")
            if fm_k == "NEXT_BAR_OPEN_BID_ASK":
                if abs(recomp_net - float(ec_11[sc_i].get("net_pnl_1x_nb_usd", 999999.0))) > 1e-3:
                    violations.append(f"ECONOMICS_11_PNL_TAMPERED_VS_LEDGER:{sc_i}:ledger={recomp_net}!=ec11={ec_11[sc_i].get('net_pnl_1x_nb_usd')}")

    # 6. Fresh OOS counterfeit & safety modes check
    if f17.get("FRESH_OOS_VERDICT") != "BLOCKED_NO_UNTOUCHED_DATA":
        violations.append(f"COUNTERFEIT_OR_INVALID_FRESH_OOS_VERDICT:{f17.get('FRESH_OOS_VERDICT')}")
    ov_19 = f19.get("orthogonal_verdicts") or {}
    if ov_19.get("FRESH_OOS_VERDICT") != "BLOCKED_NO_UNTOUCHED_DATA":
        violations.append(f"VERDICT_19_FRESH_OOS_NOT_BLOCKED:{ov_19.get('FRESH_OOS_VERDICT')}")
    if ov_19.get("ACTIVATION_READY") != "NO":
        violations.append(f"ACTIVATION_READY_NOT_NO:{ov_19.get('ACTIVATION_READY')}")
    modes_19 = ov_19.get("RUNTIME_SAFETY_MODES") or {}
    if any(modes_19.get(mk) != "OFF" for mk in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
        violations.append(f"UNSAFE_RUNTIME_MODES:{modes_19}")
    if f09.get("b10_counted_toward_d4") is not False or f09.get("ontology_isolation_verified") is not True:
        violations.append("D4_ONTOLOGY_ISOLATION_BROKEN")
    if f12.get("status") != "PASS":
        violations.append("PARITY_12_NOT_PASS")

    # 7. Closed JUnit XML bijection on disk
    jb = verify_fresh_junit_bijection_v17931211(
        run_dir,
        f14.get("collection_manifest") or {},
        f14.get("shard_manifest") or {},
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        mandatory_node_prefixes=(
            "backend/tests/test_kh_01b",
            "backend/tests/test_kh_01a",
            "backend/tests/test_v17_9_3_1_2",
            "backend/tests/test_v16_5_replay_shared_execution.py",
            "backend/tests/test_v17_5_exit_evidence.py",
        ),
        require_shard_schedule=True,
    )
    if jb.get("status") != "PASS":
        violations.extend(f"JUNIT_BIJECTION:{v}" for v in (jb.get("violations") or []))

    # 8. Dynamic gate receipts on disk + independent gate re-evaluation + cross-view check
    rcpt_dir = run_dir / "receipts"
    receipts_read: Dict[str, EvidenceReceiptV17931219] = {}
    for gid in MANDATORY_KH01B_TECHNICAL_GATES:
        rp = rcpt_dir / f"{gid}.json"
        if not rp.is_file():
            violations.append(f"MISSING_MANDATORY_GATE_RECEIPT:{gid}")
            continue
        r_doc = json.loads(rp.read_text(encoding="utf-8")).get("receipt") or {}
        r_obj = EvidenceReceiptV17931219(**r_doc)
        receipts_read[gid] = r_obj
        if not r_obj.is_valid_pass(
            expected_run_id=run_id,
            expected_tested_sha=tested_sha,
            run_dir=run_dir,
            reject_uniform_placeholder_hash=True,
        ):
            violations.append(f"INVALID_GATE_RECEIPT:{gid}:{r_obj.violations}")

    re_eval_19 = evaluate_kh01b_orthogonal_gates_from_disk(run_dir, run_id, tested_sha)
    if re_eval_19["orthogonal_verdicts"]["ENGINEERING_VERDICT"] != "PASS":
        violations.append("INDEPENDENT_GATE_REEVALUATION_ENGINEERING_NOT_PASS")
    if ov_19.get("ENGINEERING_VERDICT") != re_eval_19["orthogonal_verdicts"]["ENGINEERING_VERDICT"]:
        violations.append("FORGED_ENGINEERING_VERDICT_IN_19")

    cv = verify_kh01b_cross_view_consistency(
        readme_text=readme_txt,
        handover_text=handover_txt,
        verdicts_19=f19,
        economics_11=f11,
        junit_14=f14,
    )
    if cv["status"] != "PASS":
        violations.extend(f"CROSS_VIEW:{v}" for v in cv["violations"])

    # 9. Resource & 20 GiB disk safety check
    if not (1 <= int(f16.get("max_children_observed") or 0) <= 4):
        violations.append(f"WORKER_CAP_VIOLATION:{f16.get('max_children_observed')}")
    if int(f16.get("orphan_pids_at_cleanup_count", 1)) != 0:
        violations.append(f"ORPHAN_PIDS_REMAINING:{f16.get('orphan_pids_at_cleanup_count')}")
    if float(f16.get("min_free_disk_gb_observed") or 0.0) < 20.0:
        violations.append(f"MIN_FREE_DISK_BELOW_20GB:{f16.get('min_free_disk_gb_observed')}")

    # 10. Seal 18_independent_verifier_manifest_receipts_hashes.json
    delivered_files: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.name == MANIFEST_18_NAME:
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        delivered_files[rel] = {
            "byte_size": int(p.stat().st_size),
            "sha256": _file_sha256(p),
        }

    manifest_18_doc = {
        "spec_version": SPEC_VERSION_KH01B,
        "schema_version": SCHEMA_VERSION_KH01B,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "tested_executable_sha": tested_sha,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B,
        "independent_verifier": {
            "verifier_script": "aurum-desk/tools/kh_01b_independent_verifier.py",
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
            f"{MANIFEST_18_NAME} hashes every other file in {run_dir.name} and excludes only itself."
        ),
        "delivered_files_count": len(delivered_files),
        "delivered_files_aggregate_sha256": canonical_hash(delivered_files),
        "delivered_files": delivered_files,
    }
    manifest_sha = write_json_durable_atomic(run_dir / MANIFEST_18_NAME, manifest_18_doc)

    return {
        "spec_version": SPEC_VERSION_KH01B,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "separate_os_process_verified": verifier_pid != orchestrator_pid,
        "independent_verifier_status": "PASS" if not violations else "FAIL",
        "delivered_files_count": len(delivered_files),
        "manifest_18_sha256": manifest_sha,
        "violations": violations,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="KH-01B Separate-OS-Process Independent Disk Verifier")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-git-freeze-check", action="store_true")
    args = ap.parse_args()

    res = verify_kh_01b_run_dir_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_git_source_freeze=not args.skip_git_freeze_check,
    )
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res["independent_verifier_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
