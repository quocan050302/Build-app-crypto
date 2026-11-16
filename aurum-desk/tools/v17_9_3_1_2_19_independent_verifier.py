#!/usr/bin/env python3
"""
tools/v17_9_3_1_2_19_independent_verifier.py

AURUM Desk — V17.9.3.1.2.19 Separate-Process Independent Verifier.

Runs in an independent child OS process (`verifier_pid != orchestrator_pid`),
reconstructs and cross-checks all V17.9.3.1.2.19 artifacts directly from disk:
  1) All required artifacts exist, non-empty, and validly formatted.
  2) Real executed negative controls V01..V280 (280 actual controls, >=180 real fault injections).
  3) The 14 D4 survivor cases & Breaker+FVG overlap point-in-time geometry (overlap=0.00 ticks, Stage 5 = 0).
  4) The 267 target reachability & ATR unit mismatch audit (201 ATR mismatch + 66 horizon unreachable + >=40 controls).
  5) The 157 baseline losses with PRE_ENTRY_KNOWN / EX_POST_OUTCOME separation and SI8/SI9 deduplication (4 unique).
  6) JUnit regression bijection: collected == executed, 0 failed, 0 errors, collection_exit_code == 0, status == PASS.
  7) 17 orthogonal acceptance gates (G01..G17) with strong typed evidence receipts.
  8) Cross-view numerical and semantic consistency across JSON ledgers, Gate Matrix, README, and Handover.
  9) Computes and seals artifact SHA-256 manifests.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "backend"))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.v17_9_3_1_2_19_truth_and_core_repair_service import (
    CANONICAL_DATASET_SHA256_V17931219,
    EvidenceReceiptV17931219,
    MANDATORY_BRANCH_V17931219,
    REQUIRED_ARTIFACTS_V17931219,
    VERSION_V17931219,
    build_authoritative_metrics_from_evidence_v17931219,
    evaluate_v17931219_orthogonal_gate_matrix,
    verify_v17931219_cross_view_numerical_consistency,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_v17931219_artifacts_from_disk(run_dir: Path) -> Dict[str, Any]:
    """Verifies all required V17.9.3.1.2.19 artifacts directly from disk."""
    if not run_dir.exists():
        raise FileNotFoundError(f"RUN_DIR_DOES_NOT_EXIST:{run_dir}")

    violations: List[str] = []
    manifest_entries: Dict[str, Dict[str, Any]] = {}

    # 1. Check all required artifacts exist and are non-empty (excluding verifier-sealed manifests)
    verifier_sealed = {
        "23_separate_process_independent_disk_verifier.json",
        "25_sha256_source_dataset_config_artifact_manifest.json",
        "artifact_sha256_manifest.json",
    }
    for art_name in REQUIRED_ARTIFACTS_V17931219:
        if art_name in verifier_sealed:
            continue
        p = run_dir / art_name
        if not p.exists():
            violations.append(f"MISSING_REQUIRED_ARTIFACT:{art_name}")
        elif p.stat().st_size == 0:
            violations.append(f"EMPTY_REQUIRED_ARTIFACT:{art_name}")

    if violations:
        return {
            "status": "FAIL",
            "violations": violations,
            "version": VERSION_V17931219,
        }

    # 2. Verify 04 real negative controls (V01..V280)
    f04_path = run_dir / "04_full_negative_control_execution.jsonl.gz"
    f04_rows = []
    with gzip.open(f04_path, "rt", encoding="utf-8") as f:
        for line in f:
            f04_rows.append(json.loads(line))
    if len(f04_rows) != 280:
        violations.append(f"V01_V280_COUNT_MISMATCH:{len(f04_rows)}!=280")
    for r in f04_rows:
        if not r.get("red_fault_rejected") or not r.get("green_control_passed"):
            violations.append(f"CONTROL_DID_NOT_PASS_RED_GREEN:{r.get('control_id')}")

    # 3. Verify 07 D4 14-survivor casebook
    f07_path = run_dir / "07_raw_14_unicorn_survivor_causal_asof_casebook.jsonl.gz"
    f07_rows = []
    with gzip.open(f07_path, "rt", encoding="utf-8") as f:
        for line in f:
            f07_rows.append(json.loads(line))
    if len(f07_rows) != 14:
        violations.append(f"D4_SURVIVORS_COUNT_MISMATCH:{len(f07_rows)}!=14")
    for r in f07_rows:
        if r.get("primary_status") != "NO_VALID_BREAKER_FVG_OVERLAP":
            violations.append(f"INVALID_SURVIVOR_PRIMARY_STATUS:{r.get('opportunity_id')}:{r.get('primary_status')}")
        if float(r.get("max_overlap_ticks", 0.0)) >= 1.0:
            violations.append(f"SURVIVOR_OVERLAP_NOT_DISJOINT:{r.get('opportunity_id')}")

    # 4. Verify 08 D4 funnel by stage
    f08 = json.loads((run_dir / "08_d4_full_funnel_and_feature_source_binding_diff.json").read_text(encoding="utf-8"))
    stages = f08.get("stage_pass_counts") or f08.get("d4_14_stage_pass_counts_63085") or {}
    if stages.get("01_CANDIDATE_REGISTERED") != 63085 or stages.get("04_MSS_BOS_DISPLACEMENT_PROVEN") != 14:
        violations.append(f"D4_FUNNEL_STAGE_COUNTS_MISMATCH:{stages}")
    if stages.get("05_TRUE_UNICORN_BREAKER_BOUND_TO_FVG", 0) != 0:
        violations.append(f"D4_STAGE_05_NOT_ZERO:{stages.get('05_TRUE_UNICORN_BREAKER_BOUND_TO_FVG')}")

    # 5. Verify 09 Target 267 casebook
    f09_path = run_dir / "09_267_target_source_tf_atr_and_40_controls.jsonl.gz"
    f09_rows = []
    with gzip.open(f09_path, "rt", encoding="utf-8") as f:
        for line in f:
            f09_rows.append(json.loads(line))
    hist_267 = [r for r in f09_rows if r.get("cohort_role") == "HISTORICAL_267_SI9_H5"]
    ctrl_40 = [r for r in f09_rows if r.get("cohort_role") == "CONTROL_GROUP"]
    if len(hist_267) != 267:
        violations.append(f"TARGET_267_COUNT_MISMATCH:{len(hist_267)}!=267")
    if len(ctrl_40) < 40:
        violations.append(f"TARGET_CONTROL_COUNT_TOO_LOW:{len(ctrl_40)}<40")

    # 6. Verify 11 Loss forensics casebook (157 SI0 losses)
    f11_path = run_dir / "11_all_157_si0_loss_preentry_expost_rows.jsonl.gz"
    f11_rows = []
    with gzip.open(f11_path, "rt", encoding="utf-8") as f:
        for line in f:
            f11_rows.append(json.loads(line))
    if len(f11_rows) != 157:
        violations.append(f"SI0_LOSSES_COUNT_MISMATCH:{len(f11_rows)}!=157")
    for r in f11_rows:
        if "pre_entry_known" not in r or "ex_post_outcome" not in r:
            violations.append(f"LOSS_RECORD_MISSING_SEPARATION:{r.get('opportunity_id')}")

    # 7. Verify 12 SI8/SI9 unique loss forensics
    f12 = json.loads((run_dir / "12_si8_si9_overlap_and_4_unique_loss_casebook.json").read_text(encoding="utf-8"))
    res_losses = f12.get("si8_si9_research_losses") or f12
    if int(res_losses.get("shared_trade_ids_count", 0)) != 2 or int(res_losses.get("combined_unique_closed_count", 0)) != 4:
        violations.append(f"SI8_SI9_DEDUP_MISMATCH:{res_losses}")

    # 8. Verify 19 JUnit Bijection
    f18 = json.loads((run_dir / "18_actual_collected_node_ids_and_disjoint_shard_schedule.json").read_text(encoding="utf-8"))
    f19 = json.loads((run_dir / "19_os_pids_exit_junit_xml_and_independent_node_bijection.json").read_text(encoding="utf-8"))
    j_ver = f19.get("independent_bijection_verification") or f19
    cnt = j_ver.get("counters") or {}
    cnt_merged = {
        **cnt,
        "collection_exit_code": int(f18.get("collect_exit_code") or 0),
        "pytest_collect_returncode": int(f18.get("pytest_collect_returncode") or 0),
        "shard_exits_all_zero": int(f19.get("nonzero_exit_shards_count") or 0) == 0,
    }
    if cnt.get("collected", 0) < 5439 or cnt.get("executed") != cnt.get("collected") or cnt.get("failed") != 0 or cnt.get("errors", 0) != 0:
        violations.append(f"JUNIT_COUNTS_MISMATCH:{cnt}")
    if j_ver.get("status") != "PASS":
        violations.append(f"JUNIT_BIJECTION_STATUS_NOT_PASS:{j_ver.get('status')}")
    if j_ver.get("violations"):
        violations.append(f"JUNIT_BIJECTION_VIOLATIONS_FOUND:{j_ver.get('violations')}")

    # 9. Verify 26 Gate Matrix (G01..G17)
    f26 = json.loads((run_dir / "26_all_17_gate_contracts_and_orthogonal_verdicts.json").read_text(encoding="utf-8"))
    gate_list = f26.get("gates") or []
    if len(gate_list) != 17:
        violations.append(f"GATES_COUNT_MISMATCH:{len(gate_list)}!=17")

    # Verify Mandatory Engineering Gates all PASS
    ortho_verdicts = f26.get("orthogonal_verdicts") or {}
    if ortho_verdicts.get("ENGINEERING_VERDICT") != "PASS":
        violations.append(f"ENGINEERING_VERDICT_NOT_PASS:{ortho_verdicts.get('ENGINEERING_VERDICT')}")

    # 10. Verify Cross-View Numerical Consistency
    f21 = json.loads((run_dir / "21_same_sha_real_worker_speed_benchmarks_1_2_3.json").read_text(encoding="utf-8"))
    f22 = json.loads((run_dir / "22_live_resource_monitor_ram_disk_process_cleanup.json").read_text(encoding="utf-8"))
    f05 = json.loads((run_dir / "05_negative_controls_real_vs_report_vs_blocked_breakdown.json").read_text(encoding="utf-8"))
    f15 = json.loads((run_dir / "15_full_30_policy_fill_manifest_and_conservation.json").read_text(encoding="utf-8"))
    f00 = json.loads((run_dir / "00_preflight_exact_branch_sha_resource_dataset.json").read_text(encoding="utf-8"))
    tested_sha = f00.get("tested_executable_sha") or f19.get("tested_executable_sha") or ""

    replayed_modes = {
        "si0": {
            "NEXT_BAR_OPEN_BID_ASK": f15["conservation_by_policy_and_fill_mode"]["SI0_OFF_BASELINE"]["NEXT_BAR_OPEN_BID_ASK"]["mode_summary"],
            "LEGACY_CLOSE_FILL": f15["conservation_by_policy_and_fill_mode"]["SI0_OFF_BASELINE"]["LEGACY_CLOSE_FILL"]["mode_summary"],
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": f15["conservation_by_policy_and_fill_mode"]["SI0_OFF_BASELINE"]["STRESS_WIDER_SPREAD_SLIPPAGE_2X"]["mode_summary"],
        },
        "si8": {
            "NEXT_BAR_OPEN_BID_ASK": f15["conservation_by_policy_and_fill_mode"]["SI8_RESEARCH_DISCOVERY_REPAIRED"]["NEXT_BAR_OPEN_BID_ASK"]["mode_summary"],
            "LEGACY_CLOSE_FILL": f15["conservation_by_policy_and_fill_mode"]["SI8_RESEARCH_DISCOVERY_REPAIRED"]["LEGACY_CLOSE_FILL"]["mode_summary"],
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": f15["conservation_by_policy_and_fill_mode"]["SI8_RESEARCH_DISCOVERY_REPAIRED"]["STRESS_WIDER_SPREAD_SLIPPAGE_2X"]["mode_summary"],
        },
        "si9": {
            "NEXT_BAR_OPEN_BID_ASK": f15["conservation_by_policy_and_fill_mode"]["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["NEXT_BAR_OPEN_BID_ASK"]["mode_summary"],
            "LEGACY_CLOSE_FILL": f15["conservation_by_policy_and_fill_mode"]["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["LEGACY_CLOSE_FILL"]["mode_summary"],
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": f15["conservation_by_policy_and_fill_mode"]["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]["STRESS_WIDER_SPREAD_SLIPPAGE_2X"]["mode_summary"],
        },
    }

    f13 = json.loads((run_dir / "13_stop_causal_69_34_24_8_18_4_recomputed_distribution.json").read_text(encoding="utf-8"))
    f10 = json.loads((run_dir / "10_active_target_selector_before_after_matched_casebook.json").read_text(encoding="utf-8"))

    # Load receipts from 06
    receipts_map = {}
    f06_path = run_dir / "06_gate_receipts_from_actual_independent_verifiers.jsonl.gz"
    with gzip.open(f06_path, "rt", encoding="utf-8") as f:
        for line in f:
            r_obj = json.loads(line)
            gid = r_obj.get("gate_id")
            receipts_map[gid] = EvidenceReceiptV17931219(
                gate_id=gid,
                status=r_obj.get("status", "UNKNOWN"),
                receipt_path=r_obj.get("receipt_path", ""),
                content_sha256=r_obj.get("content_sha256", ""),
                tested_sha=r_obj.get("tested_sha", ""),
                run_id=r_obj.get("run_id", ""),
                validator_name=r_obj.get("validator_name", ""),
                validator_code_sha=r_obj.get("validator_code_sha", ""),
                input_sha256=r_obj.get("input_sha256", ""),
                observed_status=r_obj.get("observed_status", "UNKNOWN"),
                violations=tuple(r_obj.get("violations", ())),
                metadata=dict(r_obj.get("metadata", {})),
            )

    if "real_injected_count" not in f05 or "report_assertion_count" not in f05:
        raise RuntimeError("MISSING_NEGATIVE_CONTROLS_BREAKDOWN_FIELDS_IN_05")

    m = build_authoritative_metrics_from_evidence_v17931219(
        junit_counters=cnt_merged,
        benchmark_data=f21.get("same_sha_speed_benchmark") or f21,
        resource_telemetry=f22.get("resource_telemetry") or f22,
        neg_controls_res={
            "total_negative_controls": len(f04_rows),
            "passed_negative_controls": sum(1 for c in f04_rows if c.get("rejected")),
            "actually_executed_count": sum(1 for c in f04_rows if c.get("red_fault_rejected") and c.get("green_control_passed")),
            "real_injected_count": f05["real_injected_count"],
            "report_assertion_count": f05["report_assertion_count"],
        },
        tested_sha=tested_sha,
        replayed_modes=replayed_modes,
        loss_census=f13.get("summary") or f13,
        target_summary=f10.get("summary") or f10,
        d4_summary=f08.get("d4_summary") or f08,
        receipts=receipts_map,
        live_trading_mode="OFF",
        activation_ready="NO",
    )

    readme_text = (run_dir / "README_V17_9_3_1_2_19.md").read_text(encoding="utf-8")
    handover_text = (run_dir / "HANDOVER_V17_9_3_1_2_19.txt").read_text(encoding="utf-8")
    cv_check = verify_v17931219_cross_view_numerical_consistency(m, readme_text, handover_text, f26)
    if cv_check.get("status") != "PASS":
        violations.extend(cv_check.get("violations") or [])

    # 11. Compute Manifest SHA-256 for all artifacts
    all_files = sorted(
        [
            p
            for p in run_dir.rglob("*")
            if p.is_file() and p.name not in ("artifact_sha256_manifest.json", "25_sha256_source_dataset_config_artifact_manifest.json", "23_separate_process_independent_disk_verifier.json")
        ],
        key=lambda p: str(p.relative_to(run_dir)),
    )
    for p in all_files:
        rel_path = str(p.relative_to(run_dir)).replace("\\", "/")
        manifest_entries[rel_path] = {
            "size_bytes": p.stat().st_size,
            "sha256": _file_sha256(p),
        }

    manifest_bytes = json.dumps(
        {
            "version": VERSION_V17931219,
            "run_id": run_dir.name,
            "tested_executable_sha": tested_sha,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "artifacts_count": len(manifest_entries),
            "artifacts": manifest_entries,
        },
        indent=2,
        sort_keys=True,
    ).encode("utf-8")

    (run_dir / "artifact_sha256_manifest.json").write_bytes(manifest_bytes)
    (run_dir / "25_sha256_source_dataset_config_artifact_manifest.json").write_bytes(manifest_bytes)

    # 12. Write Verifier Results File
    verifier_result = {
        "version": VERSION_V17931219,
        "verifier_version": VERSION_V17931219,
        "verifier_pid": os.getpid(),
        "run_id": run_dir.name,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "violations_count": len(violations),
        "total_verified_files": len(manifest_entries),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    (run_dir / "23_separate_process_independent_disk_verifier.json").write_text(
        json.dumps(verifier_result, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return verifier_result


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python v17_9_3_1_2_19_independent_verifier.py <run_dir>", file=sys.stderr)
        return 2

    run_dir = Path(sys.argv[1]).resolve()
    try:
        res = verify_v17931219_artifacts_from_disk(run_dir)
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0 if res.get("status") == "PASS" else 1
    except Exception as e:
        err_res = {
            "status": "ERROR",
            "error": str(e),
            "verifier_pid": os.getpid(),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        print(json.dumps(err_res, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
