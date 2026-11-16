#!/usr/bin/env python3
"""
aurum-desk/tools/kh_01a_independent_verifier.py

AURUM Desk — KH-01A Separate-OS-Process Independent Disk Verifier (SPEC: KH-01A-v1.0).

Executes in an independent child OS process (`verifier_pid != orchestrator_pid`) and
reconstructs/verifies every KH-01A claim strictly from closed files on disk — NEVER
from in-memory summary objects passed by the orchestrator:
  1. Verifies all required artifacts (`00`..`14`, `16`, `README_KH01A.md`, `HANDOVER_KH01A.txt`)
     exist on disk and are non-empty.
  2. Verifies branch, frozen executable SHA, source freeze, and dataset SHA256 immutability.
  3. Verifies data-gap RED->GREEN proof (`02`) and dataset quality/calendar disclosures (`03`).
  4. Verifies real fault injections (`04`: 0 failed, 0 report-assertion padding, nonzero-D4 fixture PASS).
  5. Streams `05_preentry_casebook_157_losses_and_win_controls.jsonl.gz` row-by-row, verifying
     row SHA256s, `known_at_ms <= decision_timestamp_ms` (`0` hindsight violations), `166 = 9 + 157`
     conservation, SI8/SI9 `4 + 2 - 2 = 4` deduplication, and exact match with `06`.
  6. Streams `ledgers/07_target_307_rows_source_tf_atr_audit.jsonl.gz` and runs
     `verify_267_target_audit_v17931214` against `07`.
  7. Streams `ledgers/09_exit_event_lineage_ledger.jsonl.gz` & source reconciliation ledger
     via `verify_all_policy_fill_mode_lineage_independently_v17931211` across all 30 partitions.
  8. Re-verifies closed JUnit XML shards in `junit/*.xml` via `verify_fresh_junit_bijection_v17931211`
     (`require_shard_schedule=True`).
  9. Re-verifies `EvidenceReceiptV17931219` disk SHA256 readback (`14`), re-evaluates all 14 gates
     (`G-KH01A-01..14`), and checks cross-view consistency (`README_KH01A.md` & `HANDOVER_KH01A.txt`)
     against `16_final_orthogonal_verdicts_and_open_blockers.json`.
  10. Seals `15_sha256_manifest_and_source_config_versions.json` hashing every delivered file in
      `run_dir` (with documented self-exclusion of `15_...` to avoid circular hashing).
"""
from __future__ import annotations

import argparse
from collections import Counter
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

from services.kh_01a_foundation_service import (
    CANONICAL_DATASET_SHA256_KH01A,
    MANDATORY_BRANCH_KH01A,
    REQUIRED_ARTIFACTS_KH01A,
    SCHEMA_VERSION_KH01A,
    SPEC_VERSION_KH01A,
    evaluate_kh_01a_14_gates,
    verify_kh_01a_cross_view_consistency,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
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

MANIFEST_15_NAME = "15_sha256_manifest_and_source_config_versions.json"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON_NOT_OBJECT:{path}")
    return data


def _iter_jsonl_gz(path: Path) -> Iterable[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for ln in fh:
            if ln.strip():
                obj = json.loads(ln)
                if not isinstance(obj, dict):
                    raise ValueError(f"JSONL_ROW_NOT_OBJECT:{path}")
                yield obj


def verify_kh_01a_run_dir_from_disk(
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
        raise FileNotFoundError(f"KH01A_RUN_DIR_NOT_FOUND:{run_dir}")

    # 1. Verify required pre-manifest artifacts exist and are non-empty
    for req_name in REQUIRED_ARTIFACTS_KH01A:
        if req_name == MANIFEST_15_NAME:
            continue
        p = run_dir / req_name
        if not p.is_file():
            violations.append(f"MISSING_REQUIRED_ARTIFACT:{req_name}")
        elif p.stat().st_size <= 0:
            violations.append(f"EMPTY_REQUIRED_ARTIFACT:{req_name}")

    if violations:
        return {
            "spec_version": SPEC_VERSION_KH01A,
            "independent_verifier_status": "FAIL",
            "verifier_pid": verifier_pid,
            "violations": violations,
        }

    f00 = _read_json(run_dir / "00_preflight_branch_source_dataset_resources.json")
    f01 = _read_json(run_dir / "01_active_function_callgraph_and_keep_as_is_matrix.json")
    f02 = _read_json(run_dir / "02_data_gap_red_green_and_cross_tf_audit.json")
    f03 = _read_json(run_dir / "03_frozen_dataset_sha_session_calendar_and_quality.json")
    f04 = _read_json(run_dir / "04_fail_closed_red_green_real_fault_injections.json")
    f06 = _read_json(run_dir / "06_causal_loss_attribution_and_unknown_evidence_census.json")
    f07 = _read_json(run_dir / "07_target_267_plus_40_control_provenance_audit.json")
    f08 = _read_json(run_dir / "08_replay_3fill_1position_oos_and_cost_stress.json")
    f09 = _read_json(run_dir / "09_before_after_trade_action_and_adapter_parity.json")
    f10 = _read_json(run_dir / "10_collected_node_ids_shards_junit_exit_manifest.json")
    f11 = _read_json(run_dir / "11_worker_1_2_4_repeat_determinism_speed_report.json")
    f12 = _read_json(run_dir / "12_disk_ram_cpu_pid_cleanup_report.json")
    f13 = _read_json(run_dir / "13_fresh_oos_untouched_registry_and_verdict.json")
    f14 = _read_json(run_dir / "14_gate_receipts_independent_readback.json")
    f16 = _read_json(run_dir / "16_final_orthogonal_verdicts_and_open_blockers.json")
    readme_txt = (run_dir / "README_KH01A.md").read_text(encoding="utf-8")
    handover_txt = (run_dir / "HANDOVER_KH01A.txt").read_text(encoding="utf-8")

    run_id = expected_run_id or str(f00.get("run_id") or run_dir.name)
    tested_sha = expected_tested_sha or str(f00.get("tested_executable_sha") or "")
    orchestrator_pid = int(f00.get("orchestrator_pid") or -1)

    if verifier_pid == orchestrator_pid:
        violations.append(f"VERIFIER_NOT_SEPARATE_PROCESS:pid={verifier_pid}")
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

    # Verify 01 callgraph AST checks
    for item in f01.get("matrix") or []:
        if not (item.get("ast_verification") or {}).get("exists"):
            violations.append(f"CALLGRAPH_FUNCTION_NOT_FOUND_IN_AST:{item.get('contract_item')}:{item.get('function')}")

    # Verify 05 row-level casebook vs 06 summary
    casebook_rows = list(_iter_jsonl_gz(run_dir / "05_preentry_casebook_157_losses_and_win_controls.jsonl.gz"))
    role_counts = Counter(str(r.get("population_role") or "") for r in casebook_rows)
    if role_counts.get("SI0_CLOSED_LOSS", 0) != 157:
        violations.append(f"CASEBOOK_SI0_LOSS_COUNT:{role_counts.get('SI0_CLOSED_LOSS', 0)}!=157")
    if role_counts.get("SI0_CLOSED_WIN_CONTROL", 0) != 9:
        violations.append(f"CASEBOOK_SI0_WIN_COUNT:{role_counts.get('SI0_CLOSED_WIN_CONTROL', 0)}!=9")
    if role_counts.get("SI8_SI9_UNIQUE_RESEARCH_LOSS", 0) != 4:
        violations.append(f"CASEBOOK_SI89_UNIQUE_COUNT:{role_counts.get('SI8_SI9_UNIQUE_RESEARCH_LOSS', 0)}!=4")

    rebuilt_primary_reasons: Counter[str] = Counter()
    for r in casebook_rows:
        r_no_hash = {k: v for k, v in r.items() if k != "row_sha256"}
        if canonical_hash(r_no_hash) != r.get("row_sha256"):
            violations.append(f"CASEBOOK_ROW_SHA256_MISMATCH:{r.get('opportunity_id')}")
        dec_ts = int(r.get("decision_timestamp_ms") or 0)
        feats = r.get("pre_entry_features") or {}
        for fk, fv in feats.items():
            kn = fv.get("known_at_ms")
            if kn is not None and int(kn) > dec_ts:
                violations.append(f"CASEBOOK_LOOKAHEAD_VIOLATION:{r.get('opportunity_id')}:{fk}:{kn}>{dec_ts}")
        if r.get("population_role") == "SI0_CLOSED_LOSS":
            rebuilt_primary_reasons[str((r.get("pre_entry_known") or {}).get("reason_code") or "")] += 1

    obs_reasons_06 = ((f06.get("si0_157_losses_aggregates") or {}).get("primary_pre_entry_reason_counts") or {})
    if dict(sorted(rebuilt_primary_reasons.items())) != dict(sorted(obs_reasons_06.items())):
        violations.append("FORENSICS_06_SUMMARY_DOES_NOT_MATCH_05_ROW_STREAM")

    # Verify 07 target audit from row-level ledger on disk
    target_rows_path = run_dir / "ledgers" / "07_target_307_rows_source_tf_atr_audit.jsonl.gz"
    if not target_rows_path.is_file():
        violations.append("MISSING_ROW_LEVEL_TARGET_LEDGER:ledgers/07_target_307_rows_source_tf_atr_audit.jsonl.gz")
    else:
        t_rows = list(_iter_jsonl_gz(target_rows_path))
        t_chk = verify_267_target_audit_v17931214(t_rows, f07.get("summary_v14") or f07.get("summary") or {})
        if t_chk["status"] != "PASS":
            violations.extend(f"TARGET_267_DISK_VERIFY:{v}" for v in t_chk["violations"])

    # Verify 08 replay 30-partition exit lineage from disk ledgers
    lin_dir = run_dir / "ledgers"
    if not (lin_dir / "09_exit_event_lineage_ledger.jsonl.gz").is_file():
        violations.append("MISSING_EXIT_LINEAGE_LEDGER:ledgers/09_exit_event_lineage_ledger.jsonl.gz")
    else:
        datasets, _ = load_canonical_xauusdt_datasets()
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

    # Re-verify 10 JUnit bijection directly from junit/*.xml on disk
    junit_chk = verify_fresh_junit_bijection_v17931211(
        run_dir,
        f10.get("collection_manifest") or {},
        f10.get("shard_manifest") or {},
        expected_run_id=run_id,
        expected_tested_sha=tested_sha,
        require_shard_schedule=True,
    )
    if junit_chk["status"] != "PASS":
        violations.extend(f"JUNIT_DISK_BIJECTION:{v}" for v in junit_chk["violations"])

    # Reconstruct EvidenceReceiptV17931219 objects from 14 and verify disk SHA256 readback
    receipts_map: Dict[str, EvidenceReceiptV17931219] = {}
    for r_dict in f14.get("receipts") or []:
        gid = str(r_dict.get("gate_id") or "")
        rcpt = EvidenceReceiptV17931219(
            gate_id=gid,
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
        # Check physical target file SHA256 matches content_sha256 regardless of PASS/BLOCKED status
        t_file = (run_dir / rcpt.receipt_path).resolve()
        if not t_file.is_file():
            violations.append(f"RECEIPT_FILE_MISSING_ON_DISK:{gid}:{rcpt.receipt_path}")
        elif _file_sha256(t_file) != rcpt.content_sha256:
            violations.append(f"RECEIPT_CONTENT_SHA256_MISMATCH_ON_DISK:{gid}:{rcpt.receipt_path}")

    # Re-evaluate all 14 gates independently and compare with 16
    recomputed_16 = evaluate_kh_01a_14_gates(
        run_dir=run_dir,
        run_id=run_id,
        branch=str(f00.get("branch") or ""),
        tested_sha=tested_sha,
        dataset_sha=str(f00.get("canonical_dataset_sha256") or ""),
        preflight_00=f00,
        gap_audit_02=f02,
        dataset_quality_03=f03,
        fault_suite_04=f04,
        forensics_06=f06,
        target_audit_07=f07,
        replay_08=f08,
        parity_09=f09,
        junit_manifest_10=f10,
        worker_report_11=f11,
        resource_report_12=f12,
        fresh_oos_13=f13,
        receipts_by_gate=receipts_map,
        publication_status="PENDING_REMOTE_PUSH",
    )
    if recomputed_16["orthogonal_verdicts"] != f16.get("orthogonal_verdicts"):
        violations.append(
            f"VERDICTS_16_MISMATCH:recomputed={recomputed_16['orthogonal_verdicts']}!=stored={f16.get('orthogonal_verdicts')}"
        )
    if recomputed_16["orthogonal_verdicts"].get("ENGINEERING_VERDICT") != "PASS":
        violations.append(f"ENGINEERING_VERDICT_NOT_PASS:{recomputed_16['orthogonal_verdicts']}")
    if recomputed_16["orthogonal_verdicts"].get("ACTIVATION_READY") != "NO":
        violations.append("ACTIVATION_READY_MUST_BE_NO")

    # Cross-view consistency across JSON ledgers, README_KH01A.md, and HANDOVER_KH01A.txt
    cv_res = verify_kh_01a_cross_view_consistency(
        readme_text=readme_txt,
        handover_text=handover_txt,
        verdicts_16=f16,
        junit_manifest_10=f10,
        forensics_06=f06,
        target_audit_07=f07,
    )
    if cv_res["status"] != "PASS":
        violations.extend(f"CROSS_VIEW:{v}" for v in cv_res["violations"])

    # Compute SHA256 manifest over EVERY delivered file in run_dir (self-excluding 15_...)
    delivered_files: Dict[str, Dict[str, Any]] = {}
    for p in sorted(run_dir.rglob("*"), key=lambda x: str(x.relative_to(run_dir)).replace("\\", "/")):
        if not p.is_file():
            continue
        if p.name == MANIFEST_15_NAME:
            continue
        rel = str(p.relative_to(run_dir)).replace("\\", "/")
        delivered_files[rel] = {
            "byte_size": int(p.stat().st_size),
            "sha256": _file_sha256(p),
        }

    manifest_15_doc = {
        "spec_version": SPEC_VERSION_KH01A,
        "schema_version": SCHEMA_VERSION_KH01A,
        "run_id": run_id,
        "branch": f00.get("branch"),
        "starting_reference_head_sha": f00.get("starting_reference_head_sha"),
        "prior_v19_tested_executable_sha": f00.get("prior_v19_tested_executable_sha"),
        "tested_executable_sha": tested_sha,
        "backend_git_tree_sha": f00.get("backend_git_tree_sha"),
        "tools_git_tree_sha": f00.get("tools_git_tree_sha"),
        "source_manifest_sha256": (f00.get("source_manifest") or {}).get("sha256"),
        "source_files_count": (f00.get("source_manifest") or {}).get("files_count"),
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01A,
        "config_versions_and_hashes": f00.get("config_versions_and_hashes"),
        "prepatch_baseline_reference_hashes": f00.get("prepatch_baseline_reference_hashes"),
        "independent_verifier": {
            "verifier_script": "aurum-desk/tools/kh_01a_independent_verifier.py",
            "verifier_pid": verifier_pid,
            "orchestrator_pid": orchestrator_pid,
            "separate_os_process_verified": verifier_pid != orchestrator_pid,
            "verified_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "PASS" if not violations else "FAIL",
            "violations_count": len(violations),
            "violations": violations,
        },
        "circular_hash_exclusion_policy": (
            f"{MANIFEST_15_NAME} hashes every other delivered file in {run_dir.name} and excludes "
            "only itself to avoid circular SHA-256 self-reference."
        ),
        "delivered_files_count": len(delivered_files),
        "delivered_files_aggregate_sha256": canonical_hash(delivered_files),
        "delivered_files": delivered_files,
    }
    manifest_sha = write_json_durable_atomic(run_dir / MANIFEST_15_NAME, manifest_15_doc)

    return {
        "spec_version": SPEC_VERSION_KH01A,
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
    ap = argparse.ArgumentParser(description="KH-01A Separate-OS-Process Independent Disk Verifier")
    ap.add_argument("--run-dir", required=True, help="Path to aurum-desk/docs/artifacts/kh_01a/<RUN_ID>")
    ap.add_argument("--expected-run-id", default="")
    ap.add_argument("--expected-tested-sha", default="")
    ap.add_argument("--skip-git-freeze-check", action="store_true")
    args = ap.parse_args()

    res = verify_kh_01a_run_dir_from_disk(
        Path(args.run_dir),
        expected_run_id=args.expected_run_id,
        expected_tested_sha=args.expected_tested_sha,
        check_git_source_freeze=not args.skip_git_freeze_check,
    )
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res["independent_verifier_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
