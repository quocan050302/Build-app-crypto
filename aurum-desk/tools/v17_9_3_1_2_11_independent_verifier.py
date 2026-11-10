#!/usr/bin/env python3
"""
tools/v17_9_3_1_2_11_independent_verifier.py

AURUM Desk — V17.9.3.1.2.11 Independent Separate-Process Verifier
(P0-A Verification Truth & P0-B Exit-Event Lineage).

Runs in a separate OS process (distinct PID from the orchestrator) and
reconstructs/verifies all V17.9.3.1.2.11 claims directly from on-disk artifacts:
  1. Source freeze, branch constraint, and prior .10/.9/.8/.7 artifact immutability (T01, T02, T13).
  2. Canonical dataset SHA256 and M1 bar closure/continuity (T03).
  3. Fresh pytest collection and raw JUnit XML node bijection across all shards (T04, T05, T06).
  4. Subprocess exit codes, PIDs, wall-clock durations, resource floors, and zero orphans (T07, T08).
  5. Verification-truth negative controls a..j (T09) and 1/2/4-worker + repeat determinism (T10).
  6. Full exit-event lineage parentage, state machine, fill basis, deduplication, PnL/fee
     recomputation, and cohort conservation directly from 09_exit_event_lineage_ledger.jsonl.gz
     and raw M1 dataset candles (E01..E08).
  7. Late reconciliation idempotency (E09), cross-adapter exit parity (E10), exit-lineage
     corruption negative controls (E11), and narrative/table consistency against
     README_V17_9_3_1_2_11.md (E12).
  8. Seals and verifies artifact_sha256_manifest.json over all required files (T11, T12).
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Mapping

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "backend"))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    CANONICAL_DATASET_SHA256_V17931211,
    MANDATORY_BRANCH_V17931211,
    REQUIRED_ARTIFACTS_V17931211,
    SCHEMA_VERSION_V17931211,
    TRUTH_CLOSURE_REQUIRED_ARTIFACTS_V17931211,
    VERSION_V17931211,
    canonical_hash,
    evaluate_truth_closure_gates_v17931211,
    evaluate_v17931211_acceptance_gates,
    evaluate_verification_truth_gates_v17931211,
    recompute_realized_pnl_from_exit_events_v17931211,
    run_truth_closure_negative_controls_v01_to_v22_v17931211,
    run_verification_truth_negative_controls_v17931211,
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_artifact_manifest_v17931211,
    verify_execution_not_synthetic_v17931211,
    verify_exit_event_deduplication_v17931211,
    verify_exit_event_parentage_v17931211,
    verify_exit_fill_basis_v17931211,
    verify_exit_lineage_cross_adapter_parity_v17931211,
    verify_exit_portfolio_conservation_v17931211,
    verify_exit_state_machine_v17931211,
    verify_fresh_junit_bijection_v17931211,
    verify_process_exit_and_timeout_v17931211,
    verify_source_freeze_v17931211,
    verify_strict_report_and_handover_consistency_v17931211,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

DATASET_PATH = APP_ROOT / "backtest/historical_dataset_XAUUSDT.json.gz"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_and_write_artifact_manifest(run_dir: Path) -> Dict[str, Any]:
    files_map: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name == "artifact_sha256_manifest.json" or p.name.endswith(".partial"):
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        files_map[rel] = {
            "sha256": file_sha256(p),
            "bytes": int(p.stat().st_size),
        }
    manifest_doc = {
        "version": VERSION_V17931211,
        "schema_version": SCHEMA_VERSION_V17931211,
        "generated_at_epoch_ms": int(time.time() * 1000),
        "files_count": len(files_map),
        "files": files_map,
    }
    write_json(run_dir / "artifact_sha256_manifest.json", manifest_doc)
    return manifest_doc


def verify_readme_consistency_v17931211(
    readme_text: str,
    *,
    tested_sha: str,
    dataset_sha: str,
    cohort_doc: Mapping[str, Any],
    casebook_15_doc: Mapping[str, Any],
    junit_doc: Mapping[str, Any],
    report_metrics_doc: Mapping[str, Any] | None = None,
    handover_text: str | None = None,
) -> Dict[str, Any]:
    """
    E12: Strictly verifies README_TRUTH_CLOSURE.md / README_V17_9_3_1_2_11.md and
    HANDOVER_TRUTH_CLOSURE.txt against JSON source artifacts without drift.
    Rejects prior Venn (3+2+1+4+2+3=15), fee-split (101.6095/101.5040), and
    negative-control count (10 vs 22) narrative drift.
    """
    violations: List[str] = []
    if tested_sha not in readme_text:
        violations.append(f"README_MISSING_TESTED_SHA:{tested_sha}")
    if dataset_sha not in readme_text:
        violations.append(f"README_MISSING_DATASET_SHA:{dataset_sha}")

    main_sections = readme_text.split("## 6. Errata")[0]
    if "3 + 2 + 1 + 4 + 2 + 3 = 15" in main_sections or "3+2+1+4+2+3=15" in main_sections:
        violations.append("README_PROSE_HAS_WRONG_VENN_3_2_1_4_2_3")
    if "101.6095" in main_sections or "101.5040" in main_sections:
        violations.append("README_PROSE_HAS_WRONG_FEE_SPLIT_101_6095")

    venn = dict(casebook_15_doc.get("venn_overlap_partition") or {})
    if venn:
        exp_si1_si3 = int(venn.get("SI1_H1_H2+SI3_H4_only", 0))
        exp_si2 = int(venn.get("SI2_H3_only", 0))
        if f"SI1_H1_H2+SI3_H4_only`: `{exp_si1_si3}`" not in readme_text and f"METRICS_KV|VENN_SI1_SI3_ONLY|{exp_si1_si3}" not in readme_text:
            violations.append(f"README_MISSING_EXACT_VENN_SI1_SI3_ONLY:{exp_si1_si3}")
        if f"SI2_H3_only`: `{exp_si2}`" not in readme_text and f"METRICS_KV|VENN_SI2_ONLY|{exp_si2}" not in readme_text:
            violations.append(f"README_MISSING_EXACT_VENN_SI2_ONLY:{exp_si2}")

    if report_metrics_doc is not None:
        strict_res = verify_strict_report_and_handover_consistency_v17931211(
            readme_text,
            handover_text if handover_text is not None else readme_text,
            report_metrics_doc,
        )
        if strict_res["status"] != "PASS":
            violations.extend(strict_res["violations"])

    col_cnt = str(junit_doc.get("collection_manifest", {}).get("collected_node_ids_count") or "")
    if col_cnt and col_cnt not in readme_text:
        violations.append(f"README_MISSING_COLLECTED_TEST_COUNT:{col_cnt}")

    return {
        "version": VERSION_V17931211,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
    }


def run_independent_verification_v17931211(run_dir: Path) -> Dict[str, Any]:
    """
    Executes full independent verification of a V17.9.3.1.2.11 Truth Closure run directory
    from on-disk files in a separate OS process (verifier_pid != orchestrator_pid).
    """
    t0 = time.time()
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()

    manifest_00 = read_json(run_dir / "00_run_manifest.json")
    run_id = str(manifest_00["run_id"])
    tested_sha = str(manifest_00["tested_executable_sha"])
    dataset_sha = str(manifest_00["dataset_sha256"])
    orchestrator_pid = int(manifest_00.get("orchestrator_pid") or 0)

    # 1. Verify separate process execution (verifier PID != orchestrator PID)
    separate_process_ok = verifier_pid != orchestrator_pid and orchestrator_pid > 0

    # 2. Verify dataset SHA256 and bar closure directly from disk
    actual_dataset_sha = file_sha256(DATASET_PATH)
    datasets, _ = load_canonical_xauusdt_datasets()
    bars_1m = list(datasets.get("1m", []))
    bars_1m_by_open_ts = {int(b.get("open_time") or b.get("timestamp") or 0): b for b in bars_1m}
    bad_bars = sum(
        1
        for tf, b_list in datasets.items()
        if isinstance(b_list, list)
        for b in b_list
        if isinstance(b, dict)
        and (b.get("is_closed") is False or int(b.get("close_time") or 0) <= int(b.get("open_time") or b.get("timestamp") or 0))
    )
    dataset_ok = (
        actual_dataset_sha == CANONICAL_DATASET_SHA256_V17931211
        and actual_dataset_sha == dataset_sha
        and len(bars_1m) == 22376
        and bad_bars == 0
    )

    # 3. Verify git branch, source freeze, and prior artifact immutability (01)
    freeze_01 = read_json(run_dir / "01_git_branch_and_source_freeze.json")
    freeze_check = verify_source_freeze_v17931211(
        expected_tested_sha=tested_sha,
        observed_head_sha=str(freeze_01.get("observed_head_sha") or ""),
        observed_branch=str(freeze_01.get("branch") or ""),
        working_tree_dirty_files=list(freeze_01.get("working_tree_dirty_files") or []),
        source_manifest_before=freeze_01.get("source_manifest_before") or {},
        source_manifest_after=freeze_01.get("source_manifest_after") or {},
        prior_artifact_hashes_before=freeze_01.get("prior_artifact_hashes_before") or {},
        prior_artifact_hashes_after=freeze_01.get("prior_artifact_hashes_after") or {},
    )
    prior_immutability_ok = (
        freeze_01.get("prior_artifact_sha256_before") == freeze_01.get("prior_artifact_sha256_after")
        and bool(freeze_01.get("prior_artifact_sha256_before"))
    )

    # 4. Independently verify JUnit bijection from raw XML files on disk (06)
    junit_06 = read_json(run_dir / "06_fresh_pytest_collection_and_junit_bijection.json")
    col_manifest = junit_06["collection_manifest"]
    shard_manifest = junit_06["shard_manifest"]
    junit_bijection_check = verify_fresh_junit_bijection_v17931211(
        run_dir,
        col_manifest,
        shard_manifest,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
    )

    # 5. Independently verify subprocess exit codes, telemetry, and 1/2/4-worker process & order parity (07, 19)
    telemetry_07 = read_json(run_dir / "07_subprocess_worker_and_resource_telemetry.json")
    parity_19 = read_json(run_dir / "19_worker_1_2_4_and_repeat_determinism.json")
    proc_check = verify_process_exit_and_timeout_v17931211(
        telemetry_07.get("worker_records") or [],
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        max_allowed_workers=4,
        active_orphan_pids=telemetry_07.get("orphan_pids_at_cleanup") or [],
    )
    non_synth_check = verify_execution_not_synthetic_v17931211(
        collection_manifest=col_manifest,
        shard_manifest=shard_manifest,
        parity_manifest=parity_19,
        resource_telemetry=telemetry_07.get("resource_telemetry"),
    )

    # 6. Reconstruct and independently verify ALL 30 (10 policies x 3 fill modes) partitions
    # directly from 09_exit_event_lineage_ledger.jsonl.gz and cross-check against
    # 09_original_execution_ledger_source_reconciliation.jsonl.gz and raw M1 bars!
    all_partitions_check = verify_all_policy_fill_mode_lineage_independently_v17931211(
        run_dir,
        bars_1m_by_open_ts=bars_1m_by_open_ts,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        expected_dataset_sha=dataset_sha,
    )
    write_json(
        run_dir / "10_all_policies_3_fill_modes_independent_lineage_verification.json",
        {
            k: v
            for k, v in all_partitions_check.items()
            if k not in ("reconstructed_partitions", "reconstructed_si0_primary_bundle")
        },
    )

    dedupe_13 = read_json(run_dir / "13_exit_deduplication_and_idempotency_verification.json")
    rec15_15 = read_json(run_dir / "15_recovered_15_trades_exit_forensics_and_venn.json")
    cohort_04 = read_json(run_dir / "04_canonical_universe_and_cohort_reconciliation.json")
    report_metrics_13 = read_json(run_dir / "13_report_metrics_and_strict_narrative_validation.json")

    cases_15 = list(rec15_15.get("cases") or [])
    rec15_ok = (
        rec15_15.get("status") == "PASS"
        and int(rec15_15.get("unique_closed_trades_count") or 0) == 15
        and len(cases_15) == 15
        and all(int(c.get("exit_timestamp_ms") or 0) > 0 for c in cases_15)
        and all(float(c.get("exit_price") or 0.0) > 0.0 for c in cases_15)
        and all(float(c.get("fill_price") or 0.0) > 0.0 for c in cases_15)
    )

    # 7. Verify cross-adapter parity in this independent process
    live_cross_adapter = verify_exit_lineage_cross_adapter_parity_v17931211()

    # 8. Verify strict README_TRUTH_CLOSURE.md & HANDOVER_TRUTH_CLOSURE.txt consistency (E12)
    readme_tc_path = run_dir / "README_TRUTH_CLOSURE.md"
    readme_text = (
        readme_tc_path.read_text(encoding="utf-8")
        if readme_tc_path.exists()
        else (run_dir / "README_V17_9_3_1_2_11.md").read_text(encoding="utf-8")
    )
    handover_tc_path = run_dir / "HANDOVER_TRUTH_CLOSURE.txt"
    handover_text = handover_tc_path.read_text(encoding="utf-8") if handover_tc_path.exists() else readme_text

    readme_check = verify_readme_consistency_v17931211(
        readme_text,
        tested_sha=tested_sha,
        dataset_sha=dataset_sha,
        cohort_doc=cohort_04,
        casebook_15_doc=rec15_15,
        junit_doc=junit_06,
        report_metrics_doc=report_metrics_13,
        handover_text=handover_text,
    )

    # 9. Run all 22 adversarial negative controls V01..V22 in isolated temp directories in this verifier process
    truth_neg_controls = run_verification_truth_negative_controls_v17931211()
    neg_v22_check = run_truth_closure_negative_controls_v01_to_v22_v17931211(
        reconstructed_partitions=all_partitions_check["reconstructed_partitions"],
        bars_1m_by_open_ts=bars_1m_by_open_ts,
        report_metrics=report_metrics_13,
        readme_text=readme_text,
        handover_text=handover_text,
        run_id=run_id,
        tested_sha=tested_sha,
    )
    write_json(run_dir / "18_negative_controls_v01_to_v22_execution_report.json", neg_v22_check)

    # 10. Build 20_independent_verifier_report.json
    verifier_violations: List[str] = []
    if not separate_process_ok:
        verifier_violations.append(f"VERIFIER_NOT_SEPARATE_PROCESS:pid={verifier_pid},orch={orchestrator_pid}")
    if not dataset_ok:
        verifier_violations.append("DATASET_SHA_OR_BAR_CLOSURE_MISMATCH")
    if not prior_immutability_ok:
        verifier_violations.append("PRIOR_ARTIFACTS_MUTATED")

    for sub_name, sub_res in (
        ("freeze_check", freeze_check),
        ("junit_bijection_check", junit_bijection_check),
        ("proc_check", proc_check),
        ("non_synth_check", non_synth_check),
        ("truth_neg_controls", truth_neg_controls),
        ("all_30_partitions_lineage_check", all_partitions_check),
        ("cross_adapter_parity_check", live_cross_adapter),
        ("readme_and_handover_consistency_check", readme_check),
        ("negative_controls_v01_to_v22_check", neg_v22_check),
    ):
        if sub_res.get("status") != "PASS":
            verifier_violations.append(f"{sub_name}_FAILED:{sub_res.get('violations')}")
    if not rec15_ok:
        verifier_violations.append("RECOVERED_15_TRADES_VERIFICATION_FAILED")

    report_20 = {
        "version": VERSION_V17931211,
        "schema_version": SCHEMA_VERSION_V17931211,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "dataset_sha256": dataset_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "separate_process_verified": separate_process_ok,
        "elapsed_seconds": round(time.time() - t0, 3),
        "status": "PASS" if not verifier_violations else "FAIL",
        "violations": verifier_violations,
        "checks": {
            "dataset_and_bar_closure_ok": dataset_ok,
            "source_freeze_and_immutability": freeze_check["status"],
            "prior_artifacts_byte_immutable": prior_immutability_ok,
            "fresh_junit_bijection": junit_bijection_check["status"],
            "subprocess_exit_and_timeout": proc_check["status"],
            "non_synthetic_execution_and_worker_order_parity": non_synth_check["status"],
            "verification_truth_negative_controls": truth_neg_controls["status"],
            "all_30_partitions_raw_stream_reconstructed": all_partitions_check["status"],
            "exit_event_stream_rows_verified": all_partitions_check["total_events_verified"],
            "total_episodes_verified_all_30_partitions": all_partitions_check["total_episodes_verified_all_partitions"],
            "total_closed_trades_verified_all_30_partitions": all_partitions_check["total_closed_trades_verified_all_partitions"],
            "source_execution_ledger_reconciliation": all_partitions_check["source_execution_reconciliation"]["status"],
            "recovered_15_unique_trades_valid": rec15_ok,
            "cross_adapter_exit_parity": live_cross_adapter["status"],
            "negative_controls_v01_to_v22": neg_v22_check["status"],
            "strict_readme_and_handover_consistency": readme_check["status"],
        },
    }
    write_json(run_dir / "20_independent_verifier_report.json", report_20)

    required_files = [
        f
        for f in (
            TRUTH_CLOSURE_REQUIRED_ARTIFACTS_V17931211
            if (run_dir / "README_TRUTH_CLOSURE.md").exists()
            else REQUIRED_ARTIFACTS_V17931211
        )
        if f != "artifact_sha256_manifest.json"
    ]

    # Write preliminary 17_acceptance_gates_from_raw_proof.json and 21_acceptance_gate_matrix.json
    # before sealing artifact_sha256_manifest.json so all required files exist on first pass
    prelim_gate_doc = {"status": "PRELIM", "run_id": run_id}
    write_json(run_dir / "17_acceptance_gates_from_raw_proof.json", prelim_gate_doc)
    write_json(run_dir / "21_acceptance_gate_matrix.json", prelim_gate_doc)

    build_and_write_artifact_manifest(run_dir)
    manifest_check = verify_artifact_manifest_v17931211(run_dir, required_files)

    truth_gates_eval = evaluate_verification_truth_gates_v17931211(
        branch_freeze_check=freeze_check,
        dataset_manifest_check={"status": "PASS" if dataset_ok else "FAIL", "dataset_sha256": actual_dataset_sha, "bad_bars": bad_bars, "violations": [] if dataset_ok else ["DATASET_AUDIT_FAILED"]},
        junit_bijection_check=junit_bijection_check,
        process_telemetry_check=proc_check,
        non_synthetic_check=non_synth_check,
        artifact_manifest_check=manifest_check,
        negative_controls_check=neg_v22_check,
    )

    gate_matrix_21 = evaluate_truth_closure_gates_v17931211(
        verification_truth_gates=truth_gates_eval,
        all_partitions_lineage_check=all_partitions_check,
        late_reconciliation_check=dedupe_13.get("late_reconciliation_idempotency", {"status": "FAIL"}),
        cross_adapter_parity_check=live_cross_adapter,
        negative_controls_v22_check=neg_v22_check,
        strict_narrative_check=readme_check,
        full_regression_check=junit_bijection_check,
        cohort_reconciliation_check={"status": "PASS" if cohort_04.get("status") == "PASS" and rec15_ok else "FAIL", "violations": []},
        worker_parity_check={"status": "PASS" if parity_19.get("status") == "PASS" and non_synth_check["status"] == "PASS" else "FAIL", "violations": list(non_synth_check.get("violations") or [])},
        oos_verdict_check={"status": "PASS" if manifest_00.get("fresh_oos_status") == "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS" else "FAIL", "fresh_oos_verdict": "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS", "violations": []},
        artifact_manifest_check=manifest_check,
        phase_a_b_check={"status": "PASS" if freeze_check["status"] == "PASS" else "BLOCKED", "phase_a_tested_executable_sha": tested_sha},
        phase_c_check={"status": "BLOCKED", "note": "Pre-publication inside immutable run dir; finalized externally in Phase C publication_<run_id>.json and latest_truth_closure_run.json"},
        prior_immutability_check={"status": "PASS" if prior_immutability_ok else "FAIL", "prior_artifact_sha256": freeze_01.get("prior_artifact_sha256_after")},
    )
    gate_matrix_21["run_id"] = run_id
    gate_matrix_21["tested_executable_sha"] = tested_sha
    gate_matrix_21["dataset_sha256"] = dataset_sha
    write_json(run_dir / "17_acceptance_gates_from_raw_proof.json", gate_matrix_21)
    write_json(run_dir / "21_acceptance_gate_matrix.json", gate_matrix_21)

    # Update 00_run_manifest.json with final prepublication verdicts
    manifest_00["status"] = "COMPLETED" if report_20["status"] == "PASS" else "FAILED"
    manifest_00["verification_truth_verdict"] = gate_matrix_21["verification_truth_verdict"]
    manifest_00["exit_event_lineage_verdict"] = gate_matrix_21["exit_event_lineage_verdict"]
    manifest_00["raw_exit_event_lineage_verdict"] = gate_matrix_21["raw_exit_event_lineage_verdict"]
    manifest_00["prepublication_engineering_verdict"] = gate_matrix_21["prepublication_engineering_verdict"]
    manifest_00["independent_verifier_status"] = report_20["status"]
    manifest_00["independent_verifier_pid"] = verifier_pid
    write_json(run_dir / "00_run_manifest.json", manifest_00)

    # Re-seal artifact_sha256_manifest.json and run final verification
    build_and_write_artifact_manifest(run_dir)
    final_manifest_check = verify_artifact_manifest_v17931211(run_dir, required_files)
    if final_manifest_check["status"] != "PASS" or report_20["status"] != "PASS":
        raise RuntimeError(
            f"V17_9_3_1_2_11_INDEPENDENT_VERIFIER_FAILED:report={report_20['violations']},manifest={final_manifest_check['violations']}"
        )

    return {
        "status": "PASS",
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "verified_files_count": final_manifest_check["verified_files_count"],
        "manifest_sha256": file_sha256(run_dir / "artifact_sha256_manifest.json"),
        "verification_truth_verdict": gate_matrix_21["verification_truth_verdict"],
        "exit_event_lineage_verdict": gate_matrix_21["exit_event_lineage_verdict"],
        "raw_exit_event_lineage_verdict": gate_matrix_21["raw_exit_event_lineage_verdict"],
        "prepublication_engineering_verdict": gate_matrix_21["prepublication_engineering_verdict"],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: v17_9_3_1_2_11_independent_verifier.py <run_dir>")
    result = run_independent_verification_v17931211(Path(sys.argv[1]))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result.get("status") == "PASS" else 1)
