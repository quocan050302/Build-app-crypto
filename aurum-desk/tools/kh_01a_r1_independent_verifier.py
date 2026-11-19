#!/usr/bin/env python3
"""
aurum-desk/tools/kh_01a_r1_independent_verifier.py

AURUM Desk — KH-01A.R1 Separate-OS-Process Independent Disk Verifier.
Executes in a fresh child OS process (`verifier_pid != orchestrator_pid`), parses sealed
files strictly from disk (never in-memory orchestrator state), validates all 170 forensic
rows + per-field provenance + closed JUnit XMLs + 30 exit-lineage partitions + 307 target
audit rows + dynamic gate receipts + prior KH-01A artifact immutability, and seals
`15_independent_verifier_receipts_sha256_manifest.json`.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.kh_01a_foundation_service import (
    CANONICAL_DATASET_SHA256_KH01A,
    MANDATORY_BRANCH_KH01A,
    SCHEMA_VERSION_KH01A_R1,
    SPEC_VERSION_KH01A_R1,
)
from services.kh_01a_r1_forensics_closure_service import (
    MANDATORY_KH01A_R1_GATES,
    PRIOR_KH01A_RUN_REL_DIR,
    REQUIRED_ARTIFACTS_KH01A_R1,
    evaluate_kh_01a_r1_14_gates,
    validate_kh_01a_r1_forensics_bundle_strict,
    verify_kh_01a_r1_cross_view_consistency,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    canonical_hash,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    verify_all_policy_fill_mode_lineage_independently_v17931211,
    verify_fresh_junit_bijection_v17931211,
    verify_source_freeze_v17931211,
)
from services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service import (
    verify_267_target_audit_v17931214,
)
from services.v17_9_3_1_2_19_truth_and_core_repair_service import (
    EvidenceReceiptV17931219,
)
from tools.fresh_junit_io import write_json_durable_atomic
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets

MANIFEST_15_R1_NAME = "15_independent_verifier_receipts_sha256_manifest.json"
V130_RUN_DIR = APP_ROOT / "docs/artifacts/v17_9_3_1_2_10/20261107T150245Z_b772a03bb7cc"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> Dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"JSON_NOT_OBJECT:{path}")
    return obj


def _iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for ln in fh:
            if ln.strip():
                row = json.loads(ln)
                if not isinstance(row, dict):
                    raise ValueError(f"JSONL_ROW_NOT_OBJECT:{path}")
                yield row


def verify_kh_01a_r1_run_dir_from_disk(
    run_dir: Path,
    *,
    expected_run_id: str = "",
    expected_tested_sha: str = "",
    check_git_source_freeze: bool = True,
) -> Dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    verifier_pid = os.getpid()
    violations: List[str] = []

    if not run_dir.is_dir():
        return {
            "spec_version": SPEC_VERSION_KH01A_R1,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": [f"KH01A_R1_RUN_DIR_NOT_FOUND:{run_dir}"],
        }

    # 1. Verify all required pre-manifest files exist and are non-empty
    for req_name in REQUIRED_ARTIFACTS_KH01A_R1:
        if req_name == MANIFEST_15_R1_NAME:
            continue
        p = run_dir / req_name
        if not p.is_file():
            violations.append(f"MISSING_REQUIRED_ARTIFACT:{req_name}")
        elif p.stat().st_size <= 0:
            violations.append(f"EMPTY_REQUIRED_ARTIFACT:{req_name}")

    if violations:
        return {
            "spec_version": SPEC_VERSION_KH01A_R1,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    try:
        f00 = _read_json(run_dir / "00_branch_sha_config_dataset_preflight.json")
        f01 = _read_json(run_dir / "01_function_callgraph_keep_as_is_changed_matrix.json")
        f02 = _read_json(run_dir / "02_macro_reversed_args_red_green_diff.json")
        f03 = _read_json(run_dir / "03_macro_dst_schedule_semantics_and_invalid_samples.json")
        f04 = _read_json(run_dir / "04_previous_vs_r1_170_casebook_distribution_diff.json")
        f06 = _read_json(run_dir / "06_feature_evidence_origin_missing_invalid_source_hash_census.json")
        f07 = _read_json(run_dir / "07_original_snapshot_vs_asof_reconstruction_fingerprint_truth.json")
        f08 = _read_json(run_dir / "08_adversarial_fault_injection_red_green_suite.json")
        f09 = _read_json(run_dir / "09_all_3_fill_modes_si0_to_si9_one_position_backtest_oos_cost_stress.json")
        f10 = _read_json(run_dir / "10_full_trade_action_and_3_adapter_parity.json")
        f11 = _read_json(run_dir / "11_pytest_collection_junit_closed_shard_exit_bijection.json")
        f12 = _read_json(run_dir / "12_worker_w1_w2_w4_repeat_determinism_and_speed.json")
        f13 = _read_json(run_dir / "13_disk_ram_cpu_child_process_temp_cleanup.json")
        f14 = _read_json(run_dir / "14_fresh_oos_registry_untouched_and_economic_verdict.json")
        f16 = _read_json(run_dir / "16_final_orthogonal_verdicts_open_blockers.json")
        readme_txt = (run_dir / "README_KH01A_R1.md").read_text(encoding="utf-8")
        handover_txt = (run_dir / "HANDOVER_KH01A_R1.txt").read_text(encoding="utf-8")
        casebook_rows = list(_iter_jsonl_gz(run_dir / "05_r1_preentry_170_rows.jsonl.gz"))
    except Exception as exc:
        violations.append(f"ARTIFACT_PARSE_OR_DECOMPRESS_ERROR:{type(exc).__name__}:{exc}")
        return {
            "spec_version": SPEC_VERSION_KH01A_R1,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    run_id = expected_run_id or str(f00.get("run_id") or run_dir.name)
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    orchestrator_pid = int(f00.get("orchestrator_pid") or -1)

    if verifier_pid == orchestrator_pid:
        violations.append(f"VERIFIER_NOT_SEPARATE_OS_PROCESS:pid={verifier_pid}")
    if f00.get("run_id") != run_id:
        violations.append(f"PREFLIGHT_RUN_ID_MISMATCH:{f00.get('run_id')}!={run_id}")
    if f00.get("tested_executable_sha") != tested_sha or len(tested_sha) != 40:
        violations.append(f"PREFLIGHT_TESTED_SHA_MISMATCH:{f00.get('tested_executable_sha')}!={tested_sha}")
    if f00.get("branch") != MANDATORY_BRANCH_KH01A:
        violations.append(f"WRONG_BRANCH:{f00.get('branch')}")
    if f00.get("canonical_dataset_sha256") != CANONICAL_DATASET_SHA256_KH01A:
        violations.append(f"DATASET_SHA_MISMATCH:{f00.get('canonical_dataset_sha256')}")

    if check_git_source_freeze:
        sf_chk = verify_source_freeze_v17931211(REPO_ROOT, tested_sha)
        if sf_chk["status"] != "PASS":
            violations.extend(f"SOURCE_FREEZE:{v}" for v in sf_chk["violations"])

    # Verify prior KH-01A run dir & latest_run.json exist and were not deleted/overwritten
    prior_kh01a_dir = REPO_ROOT / PRIOR_KH01A_RUN_REL_DIR
    prior_kh01a_ptr = APP_ROOT / "docs/artifacts/kh_01a/latest_run.json"
    if not prior_kh01a_dir.is_dir():
        violations.append(f"PRIOR_KH01A_RUN_DIR_MISSING:{PRIOR_KH01A_RUN_REL_DIR}")
    if prior_kh01a_ptr.is_file():
        ptr_doc = _read_json(prior_kh01a_ptr)
        if str(ptr_doc.get("run_id") or "") != "20261117T013053Z_a1fd9293e2d0":
            violations.append(f"PRIOR_KH01A_LATEST_RUN_OVERWRITTEN:{ptr_doc.get('run_id')}")

    # Load canonical dataset & persisted 07_verified_causal_features for the 170 cohort IDs
    datasets, dataset_prov = load_canonical_xauusdt_datasets()
    if dataset_prov.get("dataset_sha256") != CANONICAL_DATASET_SHA256_KH01A:
        violations.append(f"DISK_CANONICAL_DATASET_SHA_MISMATCH:{dataset_prov.get('dataset_sha256')}")

    bar_close_by_tf_open: Dict[Tuple[str, int], int] = {}
    for tf_u, tf_k, def_span in (
        ("1M", "1m", 60_000),
        ("5M", "5m", 300_000),
        ("15M", "15m", 900_000),
        ("1H", "1H", 3_600_000),
        ("4H", "4H", 14_400_000),
        ("1D", "1D", 86_400_000),
    ):
        for b in datasets[tf_k]:
            o_ms = int(b.get("open_time") or b.get("timestamp") or 0)
            c_ms = int(b.get("close_time") or (o_ms + def_span))
            if o_ms > 0:
                bar_close_by_tf_open[(tf_u, o_ms)] = c_ms

    cohort_oids = {str(r.get("opportunity_id") or "") for r in casebook_rows}
    persisted_f07: Dict[str, Dict[str, Any]] = {}
    f07_gz_path = V130_RUN_DIR / "07_verified_causal_features.jsonl.gz"
    if f07_gz_path.is_file():
        for f_row in _iter_jsonl_gz(f07_gz_path):
            oid = str(f_row.get("opportunity_id") or "")
            if oid in cohort_oids:
                if not verify_feature_snapshot_v1793127(f_row):
                    violations.append(f"PERSISTED_F07_PROOF_INVALID:{oid}")
                persisted_f07[oid] = f_row

    # Row-by-row independent validation of 05_r1_preentry_170_rows.jsonl.gz & 06 summary
    forensics_chk = validate_kh_01a_r1_forensics_bundle_strict(
        casebook_rows,
        f06,
        canonical_bar_close_by_tf_open=bar_close_by_tf_open,
        persisted_f07_by_oid=persisted_f07 if persisted_f07 else None,
        expected_dataset_sha=CANONICAL_DATASET_SHA256_KH01A,
    )
    if forensics_chk["status"] != "PASS":
        violations.extend(f"R1_FORENSICS_DISK_VERIFY:{v}" for v in forensics_chk["violations"])

    # Verify AST callgraph matrix in 01
    ast_audit = f01.get("macro_call_site_ast_audit") or {}
    if int(ast_audit.get("invalid_production_call_sites_count", 1)) != 0:
        violations.append(f"AST_INVALID_PRODUCTION_MACRO_CALLS:{ast_audit.get('invalid_production_call_sites')}")
    for m_item in f01.get("responsibility_matrix") or []:
        if not (m_item.get("ast_verification") or {}).get("exists"):
            violations.append(f"CALLGRAPH_FUNCTION_MISSING_IN_AST:{m_item.get('item_id')}:{m_item.get('function')}")

    # Verify 307-row target audit ledger on disk
    target_ledger_p = run_dir / "ledgers" / "07_target_307_rows_source_tf_atr_audit.jsonl.gz"
    if not target_ledger_p.is_file():
        violations.append("MISSING_TARGET_307_LEDGER_ON_DISK")
    else:
        t_rows = list(_iter_jsonl_gz(target_ledger_p))
        t_summary = ((f09.get("target_267_plus_40_audit") or {}).get("summary") or {})
        t_chk = verify_267_target_audit_v17931214(t_rows, t_summary)
        if t_chk["status"] != "PASS":
            violations.extend(f"TARGET_267_DISK_VERIFY:{v}" for v in t_chk["violations"])

    # Verify 30-partition exit-event lineage ledgers on disk
    lin_dir = run_dir / "ledgers"
    if not (lin_dir / "09_exit_event_lineage_ledger.jsonl.gz").is_file():
        violations.append("MISSING_EXIT_LINEAGE_LEDGER_ON_DISK")
    else:
        bars_1m_by_open = {int(b.get("open_time") or b.get("timestamp") or 0): b for b in datasets["1m"]}
        lin_chk = verify_all_policy_fill_mode_lineage_independently_v17931211(
            lin_dir,
            bars_1m_by_open_ts=bars_1m_by_open,
            expected_run_id=run_id,
            expected_tested_sha=tested_sha,
            expected_dataset_sha=CANONICAL_DATASET_SHA256_KH01A,
            require_source_reconciliation=True,
        )
        if lin_chk["status"] != "PASS":
            violations.extend(f"EXIT_LINEAGE_30_PARTITIONS:{v}" for v in lin_chk["violations"])

    # Re-verify closed JUnit XML shards directly from junit/*.xml on disk
    junit_chk = verify_fresh_junit_bijection_v17931211(
        run_dir,
        f11.get("collection_manifest") or {},
        f11.get("shard_manifest") or {},
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        require_shard_schedule=True,
    )
    if junit_chk["status"] != "PASS":
        violations.extend(f"JUNIT_DISK_BIJECTION:{v}" for v in junit_chk["violations"])

    # Read and verify dynamic gate receipts from receipts/G-KH01A-R1-*.json on disk
    receipts_map: Dict[str, EvidenceReceiptV17931219] = {}
    receipts_dir = run_dir / "receipts"
    for gid, _ in MANDATORY_KH01A_R1_GATES:
        rcpt_p = receipts_dir / f"{gid}.json"
        if not rcpt_p.is_file():
            violations.append(f"MISSING_GATE_RECEIPT_FILE:receipts/{gid}.json")
            continue
        r_outer = _read_json(rcpt_p)
        r_dict = r_outer.get("receipt") or {}
        rcpt = EvidenceReceiptV17931219(
            gate_id=str(r_dict.get("gate_id") or ""),
            status=str(r_dict.get("status") or ""),
            receipt_path=str(r_dict.get("receipt_path") or ""),
            content_sha256=str(r_dict.get("content_sha256") or ""),
            tested_sha=str(r_dict.get("tested_sha") or ""),
            run_id=str(r_dict.get("run_id") or ""),
            validator_name=str(r_dict.get("validator_name") or ""),
            validator_code_sha=str(r_dict.get("validator_code_sha") or ""),
            input_sha256=str(r_dict.get("input_sha256") or ""),
            observed_status=str(r_dict.get("observed_status") or ""),
            violations=tuple(r_dict.get("violations") or ()),
            metadata=dict(r_dict.get("metadata") or {}),
        )
        receipts_map[gid] = rcpt
        t_file = (run_dir / rcpt.receipt_path).resolve()
        if not t_file.is_file():
            violations.append(f"RECEIPT_TARGET_MISSING_ON_DISK:{gid}:{rcpt.receipt_path}")
        elif _file_sha256(t_file) != rcpt.content_sha256:
            violations.append(f"RECEIPT_TARGET_SHA256_MISMATCH_ON_DISK:{gid}:{rcpt.receipt_path}")

    # Inject our fresh disk-verified strict_bundle_validation into f06 copy so forged f06 cannot pass!
    f06_independently_audited = {**f06, "strict_bundle_validation": forensics_chk}
    recomputed_16 = evaluate_kh_01a_r1_14_gates(
        run_dir=run_dir,
        run_id=run_id,
        branch=str(f00.get("branch") or ""),
        tested_sha=tested_sha,
        dataset_sha=str(f00.get("canonical_dataset_sha256") or ""),
        preflight_00=f00,
        callgraph_01=f01,
        macro_diff_02=f02,
        macro_dst_03=f03,
        casebook_diff_04=f04,
        forensics_06=f06_independently_audited,
        lineage_07=f07,
        fault_suite_08=f08,
        replay_09=f09,
        parity_10=f10,
        junit_11=f11,
        worker_12=f12,
        resource_13=f13,
        fresh_oos_14=f14,
        receipts_by_gate=receipts_map,
        publication_status="PENDING_REMOTE_PUSH",
    )
    if recomputed_16["orthogonal_verdicts"] != f16.get("orthogonal_verdicts"):
        violations.append(
            f"VERDICTS_16_MISMATCH:recomputed={recomputed_16['orthogonal_verdicts']}!=stored={f16.get('orthogonal_verdicts')}"
        )
    if recomputed_16["orthogonal_verdicts"].get("ENGINEERING_VERDICT") != "PASS":
        violations.append(f"ENGINEERING_VERDICT_NOT_PASS:{recomputed_16['orthogonal_verdicts']}")
    if recomputed_16["orthogonal_verdicts"].get("FORENSICS_SEMANTIC_VERDICT") != "PASS":
        violations.append(f"FORENSICS_SEMANTIC_VERDICT_NOT_PASS:{recomputed_16['orthogonal_verdicts']}")
    if recomputed_16["orthogonal_verdicts"].get("ACTIVATION_READY") != "NO":
        violations.append("ACTIVATION_READY_MUST_BE_NO")

    # Cross-view consistency across JSON ledgers, README_KH01A_R1.md, and HANDOVER_KH01A_R1.txt
    cv_chk = verify_kh_01a_r1_cross_view_consistency(
        readme_text=readme_txt,
        handover_text=handover_txt,
        verdicts_16=f16,
        junit_11=f11,
        forensics_06=f06_independently_audited,
        parity_10=f10,
    )
    if cv_chk["status"] != "PASS":
        violations.extend(f"CROSS_VIEW:{v}" for v in cv_chk["violations"])

    # Seal 15_independent_verifier_receipts_sha256_manifest.json over every delivered file in run_dir
    delivered_files: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*"), key=lambda x: str(x.relative_to(run_dir)).replace("\\", "/")):
        if not p.is_file() or p.name == MANIFEST_15_R1_NAME:
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        delivered_files[rel] = {
            "byte_size": int(p.stat().st_size),
            "sha256": _file_sha256(p),
        }

    manifest_15_doc = {
        "spec_version": SPEC_VERSION_KH01A_R1,
        "schema_version": SCHEMA_VERSION_KH01A_R1,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "prior_audited_head_sha": f00.get("prior_audited_head_sha"),
        "prior_kh01a_tested_executable_sha": f00.get("prior_kh01a_tested_executable_sha"),
        "tested_executable_sha": tested_sha,
        "backend_git_tree_sha": f00.get("backend_git_tree_sha"),
        "tools_git_tree_sha": f00.get("tools_git_tree_sha"),
        "source_manifest_sha256": (f00.get("source_manifest") or {}).get("sha256"),
        "source_files_count": (f00.get("source_manifest") or {}).get("files_count"),
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01A,
        "config_versions_and_hashes": f00.get("config_versions_and_hashes"),
        "independent_verifier": {
            "verifier_script": "aurum-desk/tools/kh_01a_r1_independent_verifier.py",
            "verifier_pid": verifier_pid,
            "orchestrator_pid": orchestrator_pid,
            "separate_os_process_verified": verifier_pid != orchestrator_pid,
            "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "forensics_170_row_level_status": forensics_chk["status"],
            "status": "PASS" if not violations else "FAIL",
            "violations_count": len(violations),
            "violations": violations,
        },
        "gate_receipts_readback": {k: asdict(v) for k, v in sorted(receipts_map.items())},
        "circular_hash_exclusion_policy": (
            f"{MANIFEST_15_R1_NAME} hashes every other delivered file in {run_dir.name} "
            "(including receipts/*.json, junit/*.xml, logs/*.jsonl.gz, ledgers/*.jsonl.gz) "
            "and excludes only itself to avoid circular SHA-256 self-reference."
        ),
        "delivered_files_count": len(delivered_files),
        "delivered_files_aggregate_sha256": canonical_hash(delivered_files),
        "delivered_files": delivered_files,
    }
    manifest_sha = write_json_durable_atomic(run_dir / MANIFEST_15_R1_NAME, manifest_15_doc)

    return {
        "spec_version": SPEC_VERSION_KH01A_R1,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "verifier_pid": verifier_pid,
        "orchestrator_pid": orchestrator_pid,
        "separate_os_process_verified": verifier_pid != orchestrator_pid,
        "independent_verifier_status": "PASS" if not violations else "FAIL",
        "delivered_files_count": len(delivered_files),
        "manifest_15_sha256": manifest_sha,
        "violations": violations,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="KH-01A.R1 Separate-OS-Process Independent Disk Verifier")
    ap.add_argument("--run-dir", required=True, help="Path to aurum-desk/docs/artifacts/kh_01a_r1/<RUN_ID>")
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-git-freeze-check", action="store_true")
    args = ap.parse_args()

    res = verify_kh_01a_r1_run_dir_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_git_source_freeze=not args.skip_git_freeze_check,
    )
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res["independent_verifier_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
