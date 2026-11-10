#!/usr/bin/env python3
"""
tools/v17_9_3_1_2_12_independent_verifier.py

AURUM Desk — V17.9.3.1.2.12 Independent Separate-Process Verifier
(P0-A Provenance Fail-Closed, P0-B Async Worker Truth, R1 Loss Forensics,
R2 Causal MTF/Unicorn Pipeline, R3 Matched Counterfactuals, R4 Regression & OOS).

Runs in a separate OS process (distinct PID from the orchestrator) and
independently reconstructs and verifies all V17.9.3.1.2.12 claims from on-disk
raw artifacts before sealing `artifact_sha256_manifest.json`.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
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
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_artifact_manifest_v17931211,
    verify_execution_not_synthetic_v17931211,
    verify_fresh_junit_bijection_v17931211,
    verify_process_exit_and_timeout_v17931211,
    verify_source_freeze_v17931211,
    verify_strict_report_and_handover_consistency_v17931211,
)
from services.v17_9_3_1_2_12_provenance_forensics_and_repair_service import (
    CANONICAL_DATASET_SHA256_V17931212,
    REQUIRED_ARTIFACTS_V17931212,
    SCHEMA_VERSION_V17931212,
    VERSION_V17931212,
    evaluate_v17931212_gate_matrix,
    run_negative_controls_v01_to_v40_v17931212,
    verify_r1_forensic_separation_and_conservation_v17931212,
    verify_r2_causal_mtf_unicorn_provenance_v17931212,
    verify_r3_counterfactual_invariants_v17931212,
    verify_r4_oos_and_activation_verdict_v17931212,
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


def append_stage_status(run_dir: Path, stage: str, status: str, **extra: Any) -> None:
    status_path = run_dir / "run_status.jsonl"
    row = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "epoch_ns": time.time_ns(),
        "pid": os.getpid(),
        "version": VERSION_V17931212,
        "stage": stage,
        "status": status,
        **extra,
    }
    with status_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


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
        "version": VERSION_V17931212,
        "schema_version": SCHEMA_VERSION_V17931212,
        "generated_at_epoch_ms": int(time.time() * 1000),
        "files_count": len(files_map),
        "files": files_map,
    }
    write_json(run_dir / "artifact_sha256_manifest.json", manifest_doc)
    return manifest_doc


def run_independent_verification_v17931212(run_dir: Path) -> Dict[str, Any]:
    t0 = time.time()
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()

    preflight_00 = read_json(run_dir / "00_preflight_branch_data_resources.json")
    run_id = str(preflight_00["run_id"])
    tested_sha = str(preflight_00["tested_executable_sha"])
    dataset_sha = str(preflight_00["dataset_sha256"])
    orchestrator_pid = int(preflight_00.get("orchestrator_pid") or 0)

    # 1. Separate OS process check
    separate_process_ok = verifier_pid != orchestrator_pid and orchestrator_pid > 0

    # 2. Canonical dataset SHA256 and bar closure check from disk
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
        and (
            b.get("is_closed") is False
            or int(b.get("close_time") or 0) <= int(b.get("open_time") or b.get("timestamp") or 0)
        )
    )
    dataset_ok = (
        actual_dataset_sha == CANONICAL_DATASET_SHA256_V17931212
        and actual_dataset_sha == dataset_sha
        and len(bars_1m) == 22376
        and bad_bars == 0
    )

    # 3. Source freeze and prior artifact immutability check
    freeze_check = verify_source_freeze_v17931211(
        expected_tested_sha=tested_sha,
        observed_head_sha=str(preflight_00.get("observed_head_sha") or ""),
        observed_branch=str(preflight_00.get("branch") or ""),
        working_tree_dirty_files=list(preflight_00.get("working_tree_dirty_files") or []),
        source_manifest_before=preflight_00.get("source_manifest_before") or {},
        source_manifest_after=preflight_00.get("source_manifest_after") or {},
        prior_artifact_hashes_before=preflight_00.get("prior_artifact_hashes_before") or {},
        prior_artifact_hashes_after=preflight_00.get("prior_artifact_hashes_after") or {},
    )
    prior_immutability_ok = (
        preflight_00.get("prior_artifact_sha256_before") == preflight_00.get("prior_artifact_sha256_after")
        and bool(preflight_00.get("prior_artifact_sha256_before"))
    )

    # 4. Fresh pytest collection and JUnit XML node bijection check (14)
    junit_14 = read_json(run_dir / "14_r4_fresh_pytest_collection_and_junit_bijection.json")
    col_manifest = junit_14["collection_manifest"]
    shard_manifest = junit_14["shard_manifest"]
    junit_bijection_check = verify_fresh_junit_bijection_v17931211(
        run_dir,
        col_manifest,
        shard_manifest,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
    )

    # 5. Subprocess worker telemetry & 1/2/4/repeat determinism check (03, 20)
    bench_03 = read_json(run_dir / "03_p0b_async_worker_resource_benchmark.json")
    parity_20 = read_json(run_dir / "20_worker_1_2_4_repeat_determinism_and_timing.json")
    proc_check = verify_process_exit_and_timeout_v17931211(
        bench_03.get("worker_records") or [],
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        max_allowed_workers=4,
        active_orphan_pids=bench_03.get("orphan_pids_at_cleanup") or [],
    )
    non_synth_check = verify_execution_not_synthetic_v17931211(
        collection_manifest=col_manifest,
        shard_manifest=shard_manifest,
        parity_manifest=parity_20,
        resource_telemetry=bench_03.get("resource_telemetry"),
    )
    p0b_ok = (
        proc_check["status"] == "PASS"
        and non_synth_check["status"] == "PASS"
        and parity_20.get("status") == "PASS"
    )
    p0b_check = {
        "status": "PASS" if p0b_ok else "FAIL",
        "proc_check": proc_check,
        "non_synth_check": non_synth_check,
        "parity_status": parity_20.get("status"),
    }

    # 6. P0-A & R4: Independently reconstruct and verify all 30 partitions from raw stream
    # and mandatory 09_original_execution_ledger_source_reconciliation.jsonl.gz (02, 16)
    all_partitions_check = verify_all_policy_fill_mode_lineage_independently_v17931211(
        run_dir,
        bars_1m_by_open_ts=bars_1m_by_open_ts,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        expected_dataset_sha=dataset_sha,
        require_source_reconciliation=True,
    )
    lineage_16_doc = {
        k: v
        for k, v in all_partitions_check.items()
        if k not in ("reconstructed_partitions", "reconstructed_si0_primary_bundle")
    }
    write_json(run_dir / "16_r4_exit_event_lineage_raw_stream_verification.json", lineage_16_doc)

    # 7. R1 verification (04, 05, 06)
    r1_rows = list(iter_jsonl_gz(run_dir / "04_r1_166_closed_trades_forensic_casebook.jsonl.gz"))
    matrix_05 = read_json(run_dir / "05_r1_loss_root_cause_matrix_by_model_and_regime.json")
    r1_check = verify_r1_forensic_separation_and_conservation_v17931212(r1_rows, matrix_05)

    # 8. R2 verification (07, 08, 09)
    htf_07 = read_json(run_dir / "07_r2_htf_mtf_causality_and_pivot_audit.json")
    uni_08 = read_json(run_dir / "08_r2_unicorn_fvg_breaker_sweep_funnel_63085.json")
    abl_09 = read_json(run_dir / "09_r2_si8_si9_278_to_0_funnel_and_ablation.json")
    r2_check = verify_r2_causal_mtf_unicorn_provenance_v17931212(htf_07, uni_08, abl_09)

    # 9. R3 verification (10, 11, 12, 13)
    tp_12 = read_json(run_dir / "12_r3_liquidity_tp_reachability_counterfactuals.json")
    r3_ledger = list(iter_jsonl_gz(run_dir / "13_r3_combined_matched_counterfactual_ledger.jsonl.gz"))
    r3_check = verify_r3_counterfactual_invariants_v17931212(
        tp_12.get("all_variants_summary") or {},
        r3_ledger,
    )

    # 10. R4 OOS provenance check (18)
    oos_18 = read_json(run_dir / "18_r4_purged_walkforward_and_oos_provenance_report.json")
    r4_oos_check = verify_r4_oos_and_activation_verdict_v17931212(oos_18)

    # 11. Run all 40 adversarial negative controls V01..V40 in isolated temp directories (19)
    readme_text = (run_dir / "README.md").read_text(encoding="utf-8")
    handover_text = (run_dir / "HANDOVER.md").read_text(encoding="utf-8")
    p0a_02 = read_json(run_dir / "02_p0a_provenance_fail_closed_verification.json")
    report_metrics = p0a_02.get("canonical_report_metrics")
    narrative_check = verify_strict_report_and_handover_consistency_v17931211(
        readme_text, handover_text, report_metrics
    )
    neg_v40_check = run_negative_controls_v01_to_v40_v17931212(
        reconstructed_partitions=all_partitions_check["reconstructed_partitions"],
        bars_1m_by_open_ts=bars_1m_by_open_ts,
        report_metrics=report_metrics,
        readme_text=readme_text,
        handover_text=handover_text,
        run_id=run_id,
        tested_sha=tested_sha,
    )
    write_json(run_dir / "19_negative_controls_v01_to_v40_report.json", neg_v40_check)

    p0a_ok = (
        all_partitions_check["status"] == "PASS"
        and all_partitions_check["source_execution_reconciliation"]["status"] == "PASS"
        and all_partitions_check["source_execution_reconciliation"]["checked"] is True
        and narrative_check["status"] == "PASS"
        and neg_v40_check["status"] == "PASS"
    )
    p0a_check = {
        "status": "PASS" if p0a_ok else "FAIL",
        "all_30_partitions_status": all_partitions_check["status"],
        "source_execution_reconciliation": all_partitions_check["source_execution_reconciliation"],
        "strict_narrative_validation": narrative_check,
        "negative_controls_v01_to_v40_status": neg_v40_check["status"],
    }

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
        ("p0a_check", p0a_check),
        ("p0b_check", p0b_check),
        ("r1_check", r1_check),
        ("r2_check", r2_check),
        ("r3_check", r3_check),
        ("r4_oos_check", r4_oos_check),
        ("neg_v40_check", neg_v40_check),
    ):
        if sub_res.get("status") != "PASS":
            verifier_violations.append(f"{sub_name}_FAILED:{sub_res.get('violations')}")

    report_21 = {
        "version": VERSION_V17931212,
        "schema_version": SCHEMA_VERSION_V17931212,
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
            "p0a_provenance_fail_closed": p0a_check["status"],
            "p0b_async_worker_resource_truth": p0b_check["status"],
            "r1_loss_forensics_conservation_and_separation": r1_check["status"],
            "r2_causal_mtf_unicorn_provenance": r2_check["status"],
            "r3_counterfactual_invariants": r3_check["status"],
            "r4_fresh_junit_bijection": junit_bijection_check["status"],
            "r4_all_30_partitions_raw_stream_reconstructed": all_partitions_check["status"],
            "r4_source_execution_ledger_reconciliation": all_partitions_check["source_execution_reconciliation"]["status"],
            "r4_oos_and_activation_verdict": r4_oos_check["status"],
            "negative_controls_v01_to_v40": neg_v40_check["status"],
            "strict_readme_and_handover_consistency": narrative_check["status"],
        },
    }
    write_json(run_dir / "21_independent_verifier_report.json", report_21)

    required_files = [
        f for f in REQUIRED_ARTIFACTS_V17931212 if f != "artifact_sha256_manifest.json"
    ]

    # Write preliminary 22_acceptance_gate_matrix.json and 25_FINAL_VERDICT.json
    write_json(run_dir / "22_acceptance_gate_matrix.json", {"status": "PRELIM", "run_id": run_id})
    write_json(run_dir / "25_FINAL_VERDICT.json", {"status": "PRELIM", "run_id": run_id})

    build_and_write_artifact_manifest(run_dir)
    manifest_check = verify_artifact_manifest_v17931211(run_dir, required_files)

    gate_matrix_22 = evaluate_v17931212_gate_matrix(
        p0a_check=p0a_check,
        p0b_check=p0b_check,
        r1_check=r1_check,
        r2_check=r2_check,
        r3_check=r3_check,
        r4_junit_check=junit_bijection_check,
        r4_lineage_check=all_partitions_check,
        r4_oos_check=r4_oos_check,
        neg_v40_check=neg_v40_check,
        manifest_check=manifest_check,
        prior_immutability_ok=prior_immutability_ok,
        phase_b_status="PASS" if freeze_check["status"] == "PASS" else "BLOCKED",
        phase_c_status="BLOCKED",
    )
    gate_matrix_22["run_id"] = run_id
    gate_matrix_22["tested_executable_sha"] = tested_sha
    gate_matrix_22["dataset_sha256"] = dataset_sha
    write_json(run_dir / "22_acceptance_gate_matrix.json", gate_matrix_22)

    final_verdict_25 = {
        "version": VERSION_V17931212,
        "schema_version": SCHEMA_VERSION_V17931212,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "dataset_sha256": dataset_sha,
        "pre_publication_engineering_gates_all_pass": gate_matrix_22["pre_publication_engineering_gates_all_pass"],
        **gate_matrix_22["orthogonal_verdicts"],
        "independent_verifier_status": report_21["status"],
        "independent_verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "phase_c_publication_note": (
            "Inside the immutable run directory, Phase C remote readback is recorded pre-push as BLOCKED "
            "and finalized externally after git push in aurum-desk/docs/artifacts/v17_9_3_1_2_12/publication_<run_id>.json "
            "and aurum-desk/docs/artifacts/v17_9_3_1_2_12/latest_run.json."
        ),
    }
    write_json(run_dir / "25_FINAL_VERDICT.json", final_verdict_25)

    # Update 00_preflight_branch_data_resources.json with final status
    preflight_00["status"] = "COMPLETED" if report_21["status"] == "PASS" else "FAILED"
    preflight_00["independent_verifier_status"] = report_21["status"]
    preflight_00["independent_verifier_pid"] = verifier_pid
    preflight_00["orthogonal_verdicts"] = gate_matrix_22["orthogonal_verdicts"]
    write_json(run_dir / "00_preflight_branch_data_resources.json", preflight_00)

    # Append final stage status BEFORE sealing artifact_sha256_manifest.json
    append_stage_status(
        run_dir,
        "STAGE_D_INDEPENDENT_SEPARATE_PROCESS_VERIFIER",
        "COMPLETED",
        verifier_pid=verifier_pid,
        status_verdict=report_21["status"],
    )

    # Seal final artifact_sha256_manifest.json and verify 100% file hash integrity
    build_and_write_artifact_manifest(run_dir)
    final_manifest_check = verify_artifact_manifest_v17931211(run_dir, required_files)
    if final_manifest_check["status"] != "PASS" or report_21["status"] != "PASS":
        raise RuntimeError(
            f"V17_9_3_1_2_12_INDEPENDENT_VERIFIER_FAILED:report={report_21['violations']},manifest={final_manifest_check['violations']}"
        )

    return {
        "status": "PASS",
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "verified_files_count": final_manifest_check["verified_files_count"],
        "manifest_sha256": file_sha256(run_dir / "artifact_sha256_manifest.json"),
        "orthogonal_verdicts": gate_matrix_22["orthogonal_verdicts"],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: v17_9_3_1_2_12_independent_verifier.py <run_dir>")
    result = run_independent_verification_v17931212(Path(sys.argv[1]))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result.get("status") == "PASS" else 1)

