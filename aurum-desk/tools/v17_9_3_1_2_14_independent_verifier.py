#!/usr/bin/env python3
"""
tools/v17_9_3_1_2_14_independent_verifier.py

AURUM Desk — V17.9.3.1.2.14 Separate-Process Independent Verifier.

Runs in an independent child OS process (`verifier_pid != orchestrator_pid`),
reconstructs and cross-checks all V17.9.3.1.2.14 artifacts directly from disk,
independently recomputes canonical scope accounting (`PNL-01..PNL-12`),
verifies the 267 + control cohort target source/R:R audit, verifies the 63,085 +
242 D4 causal Unicorn+HTF funnel, re-runs all 100 fault-injected negative controls
(`V01..V100`) in disposable temporary directories, evaluates the 14 orthogonal
acceptance gates, verifies cross-view numerical consistency across JSON/README/Handover,
and seals `23_source_sha_dataset_sha_artifact_sha256_manifest.json` +
`artifact_sha256_manifest.json`.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, Iterable, List

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "backend"))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    render_truth_closure_readme_and_handover_v17931211,
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_artifact_manifest_v17931211,
    verify_execution_not_synthetic_v17931211,
    verify_fresh_junit_bijection_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_12_provenance_forensics_and_repair_service import (
    verify_r4_oos_and_activation_verdict_v17931212,
)
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    AuthoritativeMetricsV17931213,
    render_v17931213_readme_and_handover,
    verify_bounded_async_workers_v17931213,
    verify_delayed_entry_and_structural_sl_tp_v17931213,
)
from services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service import (
    AuthoritativeMetricsV17931214,
    CANONICAL_DATASET_SHA256_V17931214,
    REQUIRED_ARTIFACTS_V17931214,
    SCHEMA_VERSION_V17931214,
    VERSION_V17931214,
    evaluate_v17931214_orthogonal_gate_matrix,
    render_v17931214_readme_and_handover,
    run_negative_controls_v01_to_v100_v17931214,
    validate_canonical_scope_accounting_v17931214,
    verify_267_target_audit_v17931214,
    verify_d4_funnel_and_execution_truth_v17931214,
    verify_v17931214_cross_view_numerical_consistency,
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


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
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
        "version": VERSION_V17931214,
        "stage": stage,
        "status": status,
        **extra,
    }
    with status_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def build_and_write_artifact_manifests(
    run_dir: Path,
    freeze_base_doc: Dict[str, Any],
) -> Dict[str, Any]:
    files_for_23: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name in (
            "artifact_sha256_manifest.json",
            "23_source_sha_dataset_sha_artifact_sha256_manifest.json",
        ) or p.name.endswith(".partial"):
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        files_for_23[rel] = {
            "sha256": file_sha256(p),
            "bytes": int(p.stat().st_size),
        }

    doc_23 = {
        **freeze_base_doc,
        "generated_at_epoch_ms": int(time.time() * 1000),
        "artifact_files_count": len(files_for_23),
        "artifact_files": files_for_23,
    }
    write_json(run_dir / "23_source_sha_dataset_sha_artifact_sha256_manifest.json", doc_23)

    files_for_all = dict(files_for_23)
    p23 = run_dir / "23_source_sha_dataset_sha_artifact_sha256_manifest.json"
    files_for_all["23_source_sha_dataset_sha_artifact_sha256_manifest.json"] = {
        "sha256": file_sha256(p23),
        "bytes": int(p23.stat().st_size),
    }
    manifest_doc = {
        "version": VERSION_V17931214,
        "schema_version": SCHEMA_VERSION_V17931214,
        "generated_at_epoch_ms": int(time.time() * 1000),
        "files_count": len(files_for_all),
        "files": files_for_all,
    }
    write_json(run_dir / "artifact_sha256_manifest.json", manifest_doc)
    return manifest_doc


def _verify_p0a_accounting_from_disk(
    run_dir: Path,
    tested_sha: str,
    raw_stream_by_pol_mode: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    rows_03 = list(iter_jsonl_gz(run_dir / "03_policy_fill_scope_authoritative_trade_accounting.jsonl.gz"))
    recon_04 = read_json(run_dir / "04_si8_si9_all_modes_raw_events_episodes_and_1pos_reconciliation.json")
    violations: List[str] = []

    scope_summaries: Dict[tuple, Dict[str, Any]] = {}
    episodes_by_pol_mode: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in rows_03:
        kind = r.get("record_kind")
        if kind == "SCOPE_SUMMARY":
            key = (r.get("scope_entity_id"), r.get("fill_mode"), r.get("portfolio_scope"))
            scope_summaries[key] = r
        elif kind == "POLICY_EPISODE":
            key = (r.get("scope_entity_id"), r.get("fill_mode"), r.get("portfolio_scope"))
            episodes_by_pol_mode.setdefault(key, []).append(r)

    if len(scope_summaries) < 90:
        violations.append(f"SCOPE_SUMMARY_COUNT_TOO_LOW:{len(scope_summaries)}<90")

    for (ent_id, fm, p_scope), sum_row in scope_summaries.items():
        ep_rows = episodes_by_pol_mode.get((ent_id, fm, p_scope))
        chk = validate_canonical_scope_accounting_v17931214(
            sum_row,
            episode_rows=ep_rows,
            run_sha=tested_sha,
            scope_entity_id=str(ent_id),
            fill_mode=str(fm),
            portfolio_scope=str(p_scope),
            materialized_or_accepted_count=int(sum_row.get("materialized_or_accepted_count") or 0),
        )
        if chk["status"] != "PASS":
            violations.extend(chk["violations"])
        if p_scope == "POLICY_1POS_CHRONOLOGICAL_PORTFOLIO":
            raw_cell = (raw_stream_by_pol_mode.get(str(ent_id)) or {}).get(str(fm))
            if not raw_cell:
                violations.append(f"MISSING_RAW_STREAM_PARTITION:{ent_id}:{fm}")
            else:
                if (
                    int(sum_row["scheduled_episodes_count"]) != int(raw_cell["total_episodes"])
                    or int(sum_row["closed_trades_count"]) != int(raw_cell["closed_trades_count"])
                    or int(sum_row["wins_count"]) != int(raw_cell["wins_count"])
                    or int(sum_row["losses_count"]) != int(raw_cell["losses_count"])
                    or abs(float(sum_row["realized_net_pnl_usd"]) - float(raw_cell["recomputed_total_net_pnl_usd"])) > 1e-4
                ):
                    violations.append(f"DISK_SCOPE_VS_RAW_STREAM_MISMATCH:{ent_id}:{fm}")

    if recon_04.get("status") != "PASS" or recon_04.get("do_not_sum_overlapping_si8_si9_pnl") is not True:
        violations.append(f"RECON_04_INVALID:{recon_04.get('violations')}")
    if len(recon_04.get("P0A_ACCOUNTING_CAUSAL_DIFF") or []) != 9:
        violations.append("P0A_CAUSAL_DIFF_MISSING_3X3_ROWS")

    return {
        "status": "PASS" if not violations else "FAIL",
        "scope_summaries_verified": len(scope_summaries),
        "violations": violations,
    }


def run_independent_verification_v17931214(run_dir: Path) -> Dict[str, Any]:
    t0 = time.time()
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()

    preflight_00 = read_json(run_dir / "00_preflight_branch_sha_resource_contract.json")
    freeze_23_base = read_json(run_dir / "23_source_sha_dataset_sha_artifact_sha256_manifest.json")
    run_id = str(preflight_00["run_id"])
    tested_sha = str(preflight_00["tested_executable_sha"])
    dataset_sha = str(preflight_00["dataset_sha256"])
    orchestrator_pid = int(preflight_00.get("orchestrator_pid") or 0)

    # 1. Separate OS process check
    separate_process_ok = verifier_pid != orchestrator_pid and orchestrator_pid > 0

    # 2. Canonical dataset SHA-256 and bar closure check from disk
    actual_dataset_sha = file_sha256(DATASET_PATH)
    datasets, _ = load_canonical_xauusdt_datasets()
    bars_1m = list(datasets.get("1m", []))
    bars_1m_by_open_ts = {int(b.get("open_time") or b.get("timestamp") or 0): b for b in bars_1m}
    bad_bars = sum(
        1
        for _tf, b_list in datasets.items()
        if isinstance(b_list, list)
        for b in b_list
        if isinstance(b, dict)
        and (
            b.get("is_closed") is False
            or int(b.get("close_time") or 0) <= int(b.get("open_time") or b.get("timestamp") or 0)
        )
    )
    dataset_ok = (
        actual_dataset_sha == CANONICAL_DATASET_SHA256_V17931214
        and actual_dataset_sha == dataset_sha
        and len(bars_1m) == 22376
        and bad_bars == 0
    )

    # 3. Source freeze & .7..13 prior artifact immutability check
    freeze_check = verify_source_freeze_v17931211(
        expected_tested_sha=tested_sha,
        observed_head_sha=str(freeze_23_base.get("observed_head_sha") or ""),
        observed_branch=str(freeze_23_base.get("branch") or ""),
        working_tree_dirty_files=list(freeze_23_base.get("working_tree_dirty_files") or []),
        source_manifest_before=freeze_23_base.get("source_manifest_before") or {},
        source_manifest_after=freeze_23_base.get("source_manifest_after") or {},
        prior_artifact_hashes_before=freeze_23_base.get("prior_artifact_hashes_before") or {},
        prior_artifact_hashes_after=freeze_23_base.get("prior_artifact_hashes_after") or {},
    )
    prior_immutability_ok = (
        freeze_23_base.get("prior_artifact_sha256_before") == freeze_23_base.get("prior_artifact_sha256_after")
        and bool(freeze_23_base.get("prior_artifact_sha256_before"))
    )

    # 4. Fresh pytest collection & JUnit XML node bijection (14, 15)
    col_14 = read_json(run_dir / "14_full_current_pytest_collection_and_shard_map.json")
    jun_15 = read_json(run_dir / "15_actual_async_os_exit_junit_node_bijection.json")
    col_manifest = col_14["collection_manifest"]
    shard_manifest = jun_15["shard_manifest"]
    junit_bijection_check = verify_fresh_junit_bijection_v17931211(
        run_dir,
        col_manifest,
        shard_manifest,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
    )

    # 5. Async process tree, 4-PID worker parity & same-SHA speed benchmark (16, 17, 22)
    parity_16 = read_json(run_dir / "16_worker_1_2_4_repeat_semantic_parity.json")
    bench_17 = read_json(run_dir / "17_same_sha_worker_speed_and_memory_disk_benchmark.json")
    proc_22 = read_json(run_dir / "22_process_tree_cleanup_atomic_outputs_and_resource_guard.json")
    proc_check = verify_bounded_async_workers_v17931213(
        proc_22.get("worker_records") or [],
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        max_allowed_workers=4,
        active_orphan_pids=proc_22.get("orphan_pids_at_cleanup") or [],
    )
    non_synth_check = verify_execution_not_synthetic_v17931211(
        collection_manifest=col_manifest,
        shard_manifest=shard_manifest,
        parity_manifest=parity_16,
        resource_telemetry=proc_22.get("resource_telemetry"),
    )
    same_sha_speed_ok = (
        bench_17.get("status") == "PASS"
        and int(bench_17.get("repeats_per_worker_mode") or 0) >= 2
        and float(bench_17.get("w3_speedup_vs_w1") or 0.0) > 1.0
    )
    async_proc_ok = (
        proc_check["status"] == "PASS"
        and non_synth_check["status"] == "PASS"
        and parity_16.get("status") == "PASS"
        and same_sha_speed_ok
        and not (proc_22.get("resource_telemetry") or {}).get("error")
    )
    async_proc_check = {
        "status": "PASS" if async_proc_ok else "FAIL",
        "proc_check": proc_check,
        "non_synth_check": non_synth_check,
        "parity_status": parity_16.get("status"),
        "same_sha_speed_ok": same_sha_speed_ok,
    }

    # 6. Raw-stream reconstruction across all 30 partitions & source execution reconciliation (09)
    all_partitions_check = verify_all_policy_fill_mode_lineage_independently_v17931211(
        run_dir,
        bars_1m_by_open_ts=bars_1m_by_open_ts,
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        expected_dataset_sha=dataset_sha,
        require_source_reconciliation=True,
    )

    # 7. P0-A Canonical Scope Accounting from disk (03, 04)
    p0a_check = _verify_p0a_accounting_from_disk(
        run_dir,
        tested_sha=tested_sha,
        raw_stream_by_pol_mode=all_partitions_check["by_policy_and_fill_mode"],
    )

    # 8. P0-B 267 + Control Target Source/R:R Audit from disk (05, 06)
    rows_05 = list(iter_jsonl_gz(run_dir / "05_267_target_failure_point_in_time_source_rr_audit.jsonl.gz"))
    summary_06 = read_json(run_dir / "06_267_target_failure_root_cause_summary_and_unknown_denominators.json")
    p0b_check = verify_267_target_audit_v17931214(rows_05, summary_06)

    # 9. P0-C D4 63,085 + 242 Funnel & Delayed Entry / Structural SL/TP from disk (07, 08, 10, 11)
    funnel_07_cnt = sum(
        1 for _ in iter_jsonl_gz(run_dir / "07_d4_full_unicorn_htf_candidate_funnel_and_rejections.jsonl.gz")
    )
    prov_rows_08 = list(iter_jsonl_gz(run_dir / "08_d4_entry_sl_tp_confirmation_replay_provenance.jsonl.gz"))
    matched_10 = read_json(run_dir / "10_a0_a4_a1b_d1_d4_matched_counterfactuals_3_fill_modes.json")
    portfolio_11 = read_json(run_dir / "11_independent_chronological_1pos_portfolio_replay.json")
    d4_summary_doc = matched_10["d4_full_63085_and_matched_funnel_summary"]
    d4_check = verify_d4_funnel_and_execution_truth_v17931214(d4_summary_doc, prov_rows_08)
    if funnel_07_cnt != 63085:
        d4_check = {
            "status": "FAIL",
            "violations": list(d4_check.get("violations") or []) + [f"FUNNEL_07_ROW_COUNT_NEQ_63085:{funnel_07_cnt}"],
        }

    sl_tp_delayed_check = verify_delayed_entry_and_structural_sl_tp_v17931213(
        matched_10["delayed_entry_contract"],
        matched_10["matched_variants_summary_by_fill_mode"],
        matched_10.get("delay_confirmation_ledger_projection") or [],
        portfolio_11,
    )

    # 10. OOS & Activation lock check (13, 25)
    oos_13 = read_json(run_dir / "13_purged_walkforward_and_fresh_oos_truth.json")
    verdict_25 = read_json(run_dir / "25_final_verdict.json")
    oos_check = verify_r4_oos_and_activation_verdict_v17931212(oos_13, verdict_25)

    # 11. Populate real `independent_verifier_pid` onto AuthoritativeMetricsV17931214
    raw_auth_dict = dict(verdict_25["authoritative_metrics_v17931214"])
    auth_metrics = replace(
        AuthoritativeMetricsV17931214(**raw_auth_dict),
        independent_verifier_pid=verifier_pid,
    )

    prelim_gates = evaluate_v17931214_orthogonal_gate_matrix(
        auth_metrics,
        p0a_check=p0a_check,
        p0b_check=p0b_check,
        d4_check=d4_check,
        sl_tp_delayed_check=sl_tp_delayed_check,
        junit_check=junit_bijection_check,
        neg_v100_check={"status": "PASS"},
        async_proc_check=async_proc_check,
        lineage_30_check=all_partitions_check,
        report_cross_view_check={"status": "PASS"},
        oos_check=oos_check,
        freeze_check=freeze_check,
        manifest_check={"status": "PASS"},
    )
    readme_text, handover_text = render_v17931214_readme_and_handover(auth_metrics, prelim_gates)

    underlying_statuses = {
        "G_ACCOUNTING_SOURCE_TRUTH": "PASS" if p0a_check["status"] == "PASS" and all_partitions_check["status"] == "PASS" else "FAIL",
        "G_TARGET_SOURCE_AND_RR_TRUTH": p0b_check["status"],
        "G_D4_CAUSAL_EXECUTION_TRUTH": d4_check["status"],
        "G_SL_TP_GEOMETRY_AND_RISK": sl_tp_delayed_check["status"],
        "G_REGRESSION_FULL": junit_bijection_check["status"],
        "G_ASYNC_RESOURCE": async_proc_check["status"],
        "G_OOS_PROVENANCE": oos_13.get("OOS_PROVENANCE_GATE", "BLOCKED_NO_UNTOUCHED_DATA"),
    }
    report_cross_view_check = verify_v17931214_cross_view_numerical_consistency(
        readme_text,
        handover_text,
        auth_metrics,
        gate_matrix_21=prelim_gates,
        underlying_check_statuses=underlying_statuses,
    )
    write_json(run_dir / "20_report_numerical_semantic_consistency_matrix.json", report_cross_view_check)

    # 12. Execute all 100 negative controls (V01..V100) in disposable temp directories
    base_v11_metrics = verdict_25.get("base_v11_compat_report_metrics")
    base_v11_readme, base_v11_handover = (
        render_truth_closure_readme_and_handover_v17931211(base_v11_metrics)
        if isinstance(base_v11_metrics, dict)
        else (None, None)
    )
    base_v13_dict = verdict_25.get("base_v13_compat_authoritative_metrics")
    base_v13_metrics = AuthoritativeMetricsV17931213(**base_v13_dict) if isinstance(base_v13_dict, dict) else None
    base_v13_readme, base_v13_handover = (
        render_v17931213_readme_and_handover(base_v13_metrics) if base_v13_metrics is not None else (None, None)
    )

    neg_v100_check = run_negative_controls_v01_to_v100_v17931214(
        reconstructed_partitions=all_partitions_check["reconstructed_partitions"],
        bars_1m_by_open_ts=bars_1m_by_open_ts,
        base_v11_report_metrics=base_v11_metrics,
        base_v11_readme_text=base_v11_readme,
        base_v11_handover_text=base_v11_handover,
        authoritative_metrics_v13=base_v13_metrics,
        readme_v13_text=base_v13_readme,
        handover_v13_text=base_v13_handover,
        gate_matrix_18_v13=verdict_25.get("base_v13_compat_gate_matrix"),
        authoritative_metrics_v14=auth_metrics,
        readme_v14_text=readme_text,
        handover_v14_text=handover_text,
        gate_matrix_21=prelim_gates,
        run_id=run_id,
        tested_sha=tested_sha,
    )
    write_json(run_dir / "18_negative_controls_v01_v100_red_green_results.json", neg_v100_check)

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
        ("async_proc_check", async_proc_check),
        ("all_partitions_check", all_partitions_check),
        ("p0a_accounting_check", p0a_check),
        ("p0b_target_267_check", p0b_check),
        ("p0c_d4_funnel_check", d4_check),
        ("sl_tp_delayed_check", sl_tp_delayed_check),
        ("oos_check", oos_check),
        ("report_cross_view_check", report_cross_view_check),
        ("neg_v100_check", neg_v100_check),
    ):
        if sub_res.get("status") != "PASS":
            verifier_violations.append(f"{sub_name}_FAILED:{sub_res.get('violations')}")

    report_19 = {
        "version": VERSION_V17931214,
        "schema_version": SCHEMA_VERSION_V17931214,
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
            "source_freeze": freeze_check["status"],
            "prior_v7_to_v13_artifacts_byte_immutable": prior_immutability_ok,
            "p0a_canonical_scope_accounting": p0a_check["status"],
            "p0b_267_and_control_target_audit": p0b_check["status"],
            "p0c_d4_63085_and_matched_funnel": d4_check["status"],
            "delayed_entry_and_structural_sl_tp": sl_tp_delayed_check["status"],
            "all_30_partitions_raw_stream_reconstructed": all_partitions_check["status"],
            "source_execution_ledger_reconciled": all_partitions_check["source_execution_reconciliation"]["status"],
            "cross_view_report_consistency": report_cross_view_check["status"],
            "async_worker_resource_speed_and_determinism": async_proc_check["status"],
            "fresh_pytest_junit_bijection": junit_bijection_check["status"],
            "negative_controls_v01_to_v100": neg_v100_check["status"],
            "oos_and_activation_lock": oos_check["status"],
        },
    }
    write_json(run_dir / "19_independent_report_verifier_from_disk.json", report_19)

    required_files = list(REQUIRED_ARTIFACTS_V17931214)
    write_json(run_dir / "21_all_gate_matrix_orthogonal_economic_and_oos_verdicts.json", prelim_gates)
    write_text_atomic(run_dir / "README_v17_9_3_1_2_14.md", readme_text)
    write_text_atomic(run_dir / "24_handover_with_lineage_and_explicit_known_limitations.txt", handover_text)
    build_and_write_artifact_manifests(run_dir, freeze_23_base)
    manifest_check = verify_artifact_manifest_v17931211(run_dir, required_files)

    final_gates_21 = evaluate_v17931214_orthogonal_gate_matrix(
        auth_metrics,
        p0a_check=p0a_check,
        p0b_check=p0b_check,
        d4_check=d4_check,
        sl_tp_delayed_check=sl_tp_delayed_check,
        junit_check=junit_bijection_check,
        neg_v100_check=neg_v100_check,
        async_proc_check=async_proc_check,
        lineage_30_check=all_partitions_check,
        report_cross_view_check=report_cross_view_check,
        oos_check=oos_check,
        freeze_check=freeze_check,
        manifest_check=manifest_check,
    )
    write_json(run_dir / "21_all_gate_matrix_orthogonal_economic_and_oos_verdicts.json", final_gates_21)

    final_readme, final_handover = render_v17931214_readme_and_handover(auth_metrics, final_gates_21)
    write_text_atomic(run_dir / "README_v17_9_3_1_2_14.md", final_readme)
    write_text_atomic(run_dir / "24_handover_with_lineage_and_explicit_known_limitations.txt", final_handover)

    final_cross_view = verify_v17931214_cross_view_numerical_consistency(
        final_readme,
        final_handover,
        auth_metrics,
        gate_matrix_21=final_gates_21,
        underlying_check_statuses=underlying_statuses,
    )
    write_json(run_dir / "20_report_numerical_semantic_consistency_matrix.json", final_cross_view)
    if final_cross_view["status"] != "PASS":
        raise RuntimeError(f"FINAL_CROSS_VIEW_CONSISTENCY_FAILED:{final_cross_view['violations']}")

    verdict_25["authoritative_metrics_v17931214"] = asdict(auth_metrics)
    verdict_25["independent_verifier_pid"] = verifier_pid
    verdict_25["independent_verifier_status"] = report_19["status"]
    verdict_25["orthogonal_verdicts"] = final_gates_21["orthogonal_verdicts"]
    verdict_25.pop("base_v11_compat_report_metrics", None)
    verdict_25.pop("base_v13_compat_authoritative_metrics", None)
    verdict_25.pop("base_v13_compat_gate_matrix", None)
    write_json(run_dir / "25_final_verdict.json", verdict_25)

    preflight_00["status"] = "COMPLETED" if report_19["status"] == "PASS" else "FAILED"
    preflight_00["independent_verifier_status"] = report_19["status"]
    preflight_00["independent_verifier_pid"] = verifier_pid
    preflight_00["orthogonal_verdicts"] = final_gates_21["orthogonal_verdicts"]
    write_json(run_dir / "00_preflight_branch_sha_resource_contract.json", preflight_00)

    append_stage_status(
        run_dir,
        "STAGE_E_INDEPENDENT_SEPARATE_PROCESS_VERIFIER",
        "COMPLETED",
        verifier_pid=verifier_pid,
        status_verdict=report_19["status"],
    )

    build_and_write_artifact_manifests(run_dir, freeze_23_base)
    final_manifest_check = verify_artifact_manifest_v17931211(run_dir, required_files)
    if final_manifest_check["status"] != "PASS" or report_19["status"] != "PASS":
        raise RuntimeError(
            f"V17_9_3_1_2_14_INDEPENDENT_VERIFIER_FAILED:report={report_19['violations']},manifest={final_manifest_check['violations']}"
        )

    return {
        "status": "PASS",
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "verified_files_count": final_manifest_check["verified_files_count"],
        "manifest_23_sha256": file_sha256(run_dir / "23_source_sha_dataset_sha_artifact_sha256_manifest.json"),
        "manifest_sha256": file_sha256(run_dir / "artifact_sha256_manifest.json"),
        "orthogonal_verdicts": final_gates_21["orthogonal_verdicts"],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: v17_9_3_1_2_14_independent_verifier.py <run_dir>")
    result = run_independent_verification_v17931214(Path(sys.argv[1]))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result.get("status") == "PASS" else 1)

