"""
aurum-desk/backend/tests/test_kh03_r6_custody_and_readiness.py

KH-03.R6 Paired Controls D12..D14 (Partition/Symlink-Junction/OOS Deny-Before-Open, Custody
Tamper/Duplicate/Crash-Orphan, Anchor Rollback & Unrelated File Cleanup Preservation) +
E01..E06 (Release Truth: Stale SHA, Forged Paired Receipt, JUnit Bijection, Benchmark Policy,
Fake Readiness PASS & Fake Remote/OOS/Activation) + Capture CLI Dry-Run & C2 Live Hard-Block Check.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Tuple

from services.kh_02_r2_c2_public_capture import RawHttpResponseC2, StdlibUrllibPublicTransportC2
from services.kh_03_r5_capture_and_custody_service import capture_r5_public_batch, make_r5_fixture_resource_monitor
from services.kh_03_r5_market_data_foundation_service import (
    R5AttestationVerificationResult,
    R5OperatorGrant,
    build_r5_source_capability_inventory,
    plan_r5_capture_batches,
    validate_r5_operator_capture_scope,
)
from services.kh_03_r6_acceptance_service import (
    R6FaultCaseSpec,
    derive_r6_fault_verdict,
    execute_r6_fault_pair,
    validate_r6_fault_pair,
)
from services.kh_03_r6_custody_readiness_service import (
    CLASSIFICATION_PASS_OFFLINE_R6,
    R6CustodyJournal,
    assign_r6_dev_partition_before_io,
    derive_r6_readiness_verdicts,
    enforce_r6_destination_guard,
    mint_r6_raw_provenance_receipt,
    verify_r6_custody_readonly,
)
from services.kh_03_r6_source_contract_service import API_FAMILY_CLASSIC_V2, MANDATORY_BRANCH_KH03_R6
from services.kh_02_r2_prospective_data_service import CANONICAL_DATASET_SHA256_KH02

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "aurum-desk"


def _minimal_passing_stage_a_artifacts(run_id: str, sha: str) -> Dict[str, Dict[str, Any]]:
    return {
        "00": {"branch": MANDATORY_BRANCH_KH03_R6, "tested_executable_sha": sha, "run_id": run_id, "worktree_clean_at_start": True, "origin_url": "https://github.com/quocan050302/Build-app-crypto.git", "disk_waiver_used": False, "preflight_disk_free_gib": 40.0, "preflight_ram_available_mib": 4000.0},
        "01": {"locked_files_modified_count": 0, "unauthorized_changed_files_count": 0, "forbidden_order_imports_count": 0, "legacy_r1_unauthorized_callers_count": 0, "production_cost_defaults_intact": True, "c2_live_hard_block_intact": True, "secrets_detected_count": 0},
        "02": {"canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02, "all_prior_sealed_artifacts_intact": True, "prior_sealed_artifacts_checked_count": 25, "prior_c2_manifest_sha256_verified": True, "oos_reads_executed": 0, "oos_evaluations": 0},
        "03": {"selected_api_family": API_FAMILY_CLASSIC_V2, "registry_status": "DOC_VERIFIED_READY_FOR_STAGE_B_REVIEW", "doc_verified_endpoints_count": 7, "official_docs_retrieved_count": 8, "rest_quote_snapshot_can_be_continuous_bbo": False, "mixed_v2_v3_rejected": True, "substitute_symbol_rejected": True, "private_or_redirect_rejected": True},
        "04": {"default_embedded_owner_key_present": False, "unprovisioned_owner_trust_blocked": True, "unsigned_template_rejected": True, "qa_trust_cannot_authorize_external": True, "cli_env_prompt_bypass_rejected": True, "qa_isolated_ed25519_verified": True, "payload_tamper_rejected": True, "duplicate_key_rejected": True, "expired_or_clock_rollback_rejected": True, "revoked_trust_root_rejected": True, "plan_exceeding_grant_rejected_no_clamp": True, "sha_branch_store_drift_rejected": True},
        "05": {"sequential_replay_denied": True, "cross_process_race_single_winner": True, "cross_process_contenders_count": 3, "cross_process_winners_count": 1, "replan_reissue_denied": True, "same_identity_different_payload_rejected": True, "post_kill_restart_replay_denied": True},
        "06": {"missing_db_no_auto_init_blocked": True, "corrupt_db_quarantined": True, "anchor_rollback_quarantined": True, "post_commit_pre_anchor_forward_recovered": True, "kill_after_start_retains_unknown_bytes": True, "alternate_store_path_rejected": True, "duplicate_attempt_id_rejected": True, "double_settle_rejected": True, "negative_counter_rejected": True, "budget_overflow_rejected": True, "unknown_bytes_conservatively_retained": True, "capacity_full_blocks_without_eviction": True},
        "08": {"loopback_worker_launched_and_completed": True, "loopback_worker_pid": 12345, "loopback_worker_os_exit_verified": True, "actual_start_expiry_zero_dispatch": True, "external_socket_without_grant_blocked": True, "noncooperative_tree_terminated_and_reaped": True, "surviving_descendants_after_reap_count": 0, "unverified_os_proof_quarantined": True, "late_result_after_cancel_discarded": True, "slow_drip_total_deadline_enforced": True, "oversized_and_chunked_rejected": True, "gzip_bomb_rejected_bounded": True, "retry_after_honored_no_shortening": True, "nonretryable_401_403_redirect_stopped": True, "ws_valid_subscribe_ack_and_snapshot": True, "ws_invalid_ack_or_private_op_rejected": True, "ws_oversized_frame_rejected": True, "ws_disconnect_epoch_gap_and_bounded_reconnect": True, "ws_queue_overflow_no_silent_drop": True, "resource_missing_stale_nan_wrong_vol_rejected": True, "qa_fixture_telemetry_blocked_in_live": True},
        "09": {"external_market_requests_attempted": 0, "accepted_real_observed_records": 0, "production_mode": "OFF", "live_trading_mode": "OFF", "paper_trading_mode": "OFF", "auto_trading_mode": "OFF", "activation_ready": "NO", "economic_verdict": "NO_PROVEN_EDGE", "fresh_oos_verdict": "BLOCKED_NO_UNTOUCHED_DATA", "kh04_entry_verdict": "BLOCKED_NO_DEV_CANDIDATE", "oos_evaluations": 0},
        "10": {"qa_valid_metadata_accepted": True, "missing_tick_lot_blocked_no_default": True, "identity_mismatch_halts_grant": True},
        "11": {"valid_closed_bars_accepted": True, "invalid_geometry_rejected": True, "cursor_replay_stopped": True, "contradictory_duplicate_quarantined": True, "unclosed_future_excluded_from_canonical": True, "unknown_calendar_marked_incomplete": True},
        "12": {"crossed_tick_ooo_rejected": True, "rest_snapshot_rejected_as_streaming_bbo": True, "no_carry_across_disconnect_or_missing_snapshot": True, "duplicates_do_not_inflate_coverage": True, "stale_quote_not_observed_despite_heartbeat": True, "observed_vs_state_known_reported_separately": True},
        "13": {"documented_settlements_validated": True, "missing_settlements_not_zero_filled": True, "unknown_interval_blocked_no_8h_default": True, "conflicting_settlement_quarantined": True},
        "14": {"owner_fee_status": "UNKNOWN_NOT_LOCALLY_VERIFIED", "public_fee_rejected_as_owner_effective": True, "slippage_status": "MISSING_NO_OWNER_FILL_EVIDENCE", "public_bbo_proves_execution_slippage": False, "orders_placed_count": 0},
        "15": {"qa_custody_chain_verified": True, "symlink_junction_oos_denied_before_open": True, "qa_promotion_to_dev_denied": True, "tamper_and_duplicate_receipt_rejected": True, "anchor_rollback_detected": True, "unrelated_files_preserved_on_cleanup": True},
        "16": {"r4_decimal_bridge_exact_match": True, "n_closed": 203, "n_wins": 7, "n_losses": 196, "n_open_censored": 28, "n_gap_events": 2, "realized_losses_gt_2p50_count": 139},
        "17": {"all_six_scenarios_exact_match": True, "all_three_arms_exact_match": True},
        "18": {"a1_checked": 56710, "a1_mismatches": 0, "si8_checked": 6375, "si8_mismatches": 0, "adapters_checked": 246, "adapters_mismatches": 0, "d4_mismatches": 0},
        "19": {"junit_bijection_exact": True, "collected_count": 5965, "executed_count": 5965, "failed_count": 0, "error_count": 0, "mandatory_skipped_count": 0, "nonzero_exit_shards_count": 0},
        "20": {"science_w1_w2_w4_all_identical": True, "r6_ingest_w1_w2_w4_all_identical": True, "r6_ingest_canonical_semantic_sha256": "e" * 64, "pytest_policy_valid": True, "science_policy_valid": True, "r6_ingest_benchmark_valid": True, "applied_safe_workers_cap": 2},
        "21": {"disk_waiver_exit2_proven": True, "min_observed_disk_free_gib": 40.0, "min_observed_ram_available_mib": 4000.0, "samples_count": 10, "max_observed_descendants": 3, "owned_temp_cleaned_verified": True, "orphan_workers_count": 0},
        "23": {"new_r6_paired_verified_count": 56, "new_r6_paired_failed_count": 0, "r6_fault_ast_literal_flags_count": 0, "unique_r6_execution_ids_ok": True, "inherited_c1_c2_paired_passed_count": 91, "inherited_c1_c2_paired_failed_count": 0, "inherited_regression_passed_count": 288, "inherited_regression_failed_count": 0, "inherited_not_relabelled_as_new_r6": True},
        "24": {"clean_clone_verifier_green": True, "new_r6_mutations_tested_count": 32, "new_r6_mutations_detected_count": 32, "inherited_mutations_tested_count": 135, "inherited_mutations_detected_count": 135},
        "26": {"distinct_os_process_verified": True, "verifier_status": "PASS", "violations_count": 0, "verifier_pid": 9999, "orchestrator_pid": 8888},
        "27": {"phase1_executable_committed": True, "tested_executable_sha": sha},
    }


def run_kh03_r6_controls_d12_to_e06(*, executable_sha: str, run_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    receipts: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="r6_d12e06_") as td:
        tdir = Path(td)
        approved = tdir / "approved_store"
        approved.mkdir()
        oos_root = tdir / "sealed_oos"
        oos_root.mkdir()
        outside_target = tdir / "outside_target"
        outside_target.mkdir()

        # D12: symlink/NTFS junction / OOS deny-before-open / QA->DEV promotion denied vs clean QA partition
        junc_path = approved / "evil_junction"
        junc_created = False
        try:
            if os.name == "nt":
                rc_mk = subprocess.run(["cmd", "/c", "mklink", "/J", str(junc_path), str(outside_target)], capture_output=True, check=False).returncode
                junc_created = (rc_mk == 0 and junc_path.exists())
            else:
                os.symlink(outside_target, junc_path)
                junc_created = True
            g_junc = enforce_r6_destination_guard(junc_path / "x.bin", approved_root=approved, partition="QA_FIXTURE", repo_root=REPO_ROOT, oos_roots=(oos_root,))
        finally:
            if junc_created and junc_path.exists():
                if os.name == "nt":
                    os.rmdir(junc_path)
                else:
                    junc_path.unlink()

        g_oos = enforce_r6_destination_guard(oos_root / "leak.bin", approved_root=approved, partition="DEV_ONLY", repo_root=REPO_ROOT, oos_roots=(oos_root,))
        p_qa_dev = assign_r6_dev_partition_before_io(stage="C_DEV", provenance_class="QA_LOOPBACK", approved_root=approved, run_id=run_id, repo_root=REPO_ROOT, oos_roots=(oos_root,))
        p_ok = assign_r6_dev_partition_before_io(stage="QA_LOOPBACK", provenance_class="QA_LOOPBACK", approved_root=approved, run_id=run_id, repo_root=REPO_ROOT, oos_roots=(oos_root,))
        summary["symlink_junction_oos_denied_before_open"] = (not g_junc["allowed"] and not g_oos["allowed"])
        summary["qa_promotion_to_dev_denied"] = (not p_qa_dev["assigned"])
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-D12", "D_DATA_CUSTODY", "INTEGRATION_FAULT", "services.kh_03_r6_custody_readiness_service.enforce_r6_destination_guard", "custody_denied_or_caught", "custody_verified_clean", "NTFS junction/symlink, OOS path & QA->DEV promotion denied before open vs clean QA partition"),
            negative_fn=lambda: ({"junc_created": junc_created}, {"denied_or_caught": summary["symlink_junction_oos_denied_before_open"] and summary["qa_promotion_to_dev_denied"], "violations_count": len(g_junc["violations"]) + len(g_oos["violations"]) + len(p_qa_dev["violations"])}),
            positive_fn=lambda: ({"partition": p_ok["partition"]}, {"verified_clean": p_ok["assigned"] and p_ok["partition"] == "QA_FIXTURE", "violations_count": len(p_ok["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # D13: staged SHA/proof tamper, duplicate receipt & crash partial commit orphan detected vs clean custody
        # Updated minimally for KH-03.R6.A1 F23: provenance now strictly requires genuine R6TrustedSupervisorResult
        # + R6DurableSettlementReceipt from real loopback supervise_r6_session (raw Mapping dicts are rejected!).
        from http.server import BaseHTTPRequestHandler as _BHRH, ThreadingHTTPServer as _THS
        import threading as _th
        import time as _t
        from services.kh_03_r6_a1_trust_service import complete_caps_vector_a1 as _ccv
        from services.kh_03_r6_bounded_transport_service import (
            build_r6_bound_capture_plan as _bplan,
            collect_real_r6_resource_sample as _csamp,
            supervise_r6_session as _sup,
        )
        from services.kh_03_r6_durable_grant_service import (
            QA_ISOLATED_TRUST_NAMESPACE_R6 as _QNS,
            load_r6_owner_trust_root as _ltr,
            open_r6_durable_ledger as _oled,
            provision_qa_isolated_trust_root as _pqa,
            provision_r6_durable_ledger as _pled,
            sign_r6_grant_payload_with_ed25519 as _sgn,
            verify_r6_owner_grant as _vgr,
        )
        from services.kh_03_r6_source_contract_service import build_r6_source_contract_registry as _breg
        from tests.test_kh03_r6_source_contracts import make_qa_grant_payload as _mqg

        payload1 = b'{"symbol":"XAUUSDT","bars":1}'

        class _D13H(_BHRH):
            def log_message(self, fmt: str, *args: Any) -> None:
                return
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload1)))
                self.end_headers()
                self.wfile.write(payload1)

        srv_d13 = _THS(("127.0.0.1", 0), _D13H)
        port_d13 = int(srv_d13.server_address[1])
        th_d13 = _th.Thread(target=srv_d13.serve_forever, daemon=True)
        th_d13.start()
        try:
            now_d13 = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)
            d13_sid = "qa_d13_store"
            d13_led_dir = tdir / "d13_ledger"
            d13_tr_p, d13_pk, _ = _pqa(tdir / "d13_trust", store_id=d13_sid)
            d13_tr = _ltr(d13_tr_p, expected_namespace=_QNS)["trust_root"]
            _pled(d13_led_dir, store_id=d13_sid, trust_namespace=_QNS, now_utc=now_d13)
            d13_led = _oled({"ledger_store_root": str(d13_led_dir), "store_id": d13_sid}, d13_sid, expected_trust_namespace=_QNS, actual_now=now_d13)["ledger"]
            d13_reg = _breg()
            d13_gp = _mqg(grant_id="G_D13", dest_root=approved, store_id=d13_sid, now_utc=now_d13, executable_sha=executable_sha)
            d13_vg = _vgr(_sgn(d13_gp, d13_pk), d13_tr, now_d13, expected_executable_sha=executable_sha, expected_release_manifest_sha256="b" * 64, expected_store_id=d13_sid, expected_stage="QA_LOOPBACK")["verified_grant"]
            d13_pl = _bplan(d13_vg, d13_reg, {}, {"ok": True}, plan_id="pl_d13")["plan"]
            d13_lease = d13_led.claim_r6_grant_session_atomic(d13_vg, d13_pl, "s1", actual_now=now_d13)["lease"]
            d13_vols = {"dest": approved, "ledger": d13_led_dir}
            d13_caps = _ccv({"requests": 1, "payload_bytes": 4096, "decoded_bytes": 8192})
            sup1 = _sup(
                ledger=d13_led, lease=d13_lease, plan=d13_pl, verified_grant=d13_vg,
                validated_request={"kind": "rest_get", "url": f"http://127.0.0.1:{port_d13}/ok", "params": {}},
                attempt_id="a1", request_type="rest_get", reserved_caps=d13_caps,
                resource_sample_fn=lambda: _csamp(d13_vols, active_workers=1), expected_volumes=d13_vols,
                session_deadline_monotonic=_t.monotonic() + 10.0, utc_now_fn=lambda: now_d13, qa_loopback_mode=True,
                run_id=run_id, partition="QA_FIXTURE", endpoint_id="classic_v2_candles", contract_digest="c" * 64,
            )
            sup2 = _sup(
                ledger=d13_led, lease=d13_lease, plan=d13_pl, verified_grant=d13_vg,
                validated_request={"kind": "rest_get", "url": f"http://127.0.0.1:{port_d13}/ok", "params": {}},
                attempt_id="a2", request_type="rest_get", reserved_caps=d13_caps,
                resource_sample_fn=lambda: _csamp(d13_vols, active_workers=1), expected_volumes=d13_vols,
                session_deadline_monotonic=_t.monotonic() + 10.0, utc_now_fn=lambda: now_d13, qa_loopback_mode=True,
                run_id=run_id, partition="QA_FIXTURE", endpoint_id="classic_v2_candles", contract_digest="c" * 64,
            )
            d13_led.finalize_r6_session(d13_lease, quarantine=False, reason="D13_DONE", actual_now=now_d13)
        finally:
            srv_d13.shutdown()
            srv_d13.server_close()
            th_d13.join(timeout=2.0)

        jnl = R6CustodyJournal(approved_root=approved, partition="QA_FIXTURE", run_id=f"{run_id}_d13", repo_root=REPO_ROOT, oos_roots=(oos_root,))
        prov1 = mint_r6_raw_provenance_receipt(tested_executable_sha=executable_sha, run_id=run_id, session_id="s1", lease_id=d13_lease.lease_id, attempt_id="a1", endpoint_id="classic_v2_candles", contract_digest="c" * 64, partition="QA_FIXTURE", qa_loopback_mode=True, transport_kind="REST_GET", supervisor_result=sup1["trusted_result"], raw_payload_bytes_data=payload1, settlement_receipt=sup1["settlement_receipt"])
        p_ok1 = jnl.persist_r6_verified_chunk(chunk_id="chk_001", raw_payload=payload1, normalized_rows=[{"ts": 1}], provenance_receipt=prov1)
        p_dup = jnl.persist_r6_verified_chunk(chunk_id="chk_002", raw_payload=payload1, normalized_rows=[{"ts": 1}], provenance_receipt=prov1)
        p_forged = jnl.persist_r6_verified_chunk(chunk_id="chk_003", raw_payload=payload1, normalized_rows=[{"ts": 1}], provenance_receipt=prov1.to_dict())
        p_tamper = jnl.persist_r6_verified_chunk(chunk_id="chk_004", raw_payload=b"TAMPERED", normalized_rows=[{"ts": 1}], provenance_receipt=prov1)
        ro_clean = verify_r6_custody_readonly(jnl.store_dir, expected_partition="QA_FIXTURE", expected_run_id=f"{run_id}_d13")
        # Now simulate crash before commit -> orphan .tmp detected by readonly verifier!
        prov2 = mint_r6_raw_provenance_receipt(tested_executable_sha=executable_sha, run_id=run_id, session_id="s1", lease_id=d13_lease.lease_id, attempt_id="a2", endpoint_id="classic_v2_candles", contract_digest="c" * 64, partition="QA_FIXTURE", qa_loopback_mode=True, transport_kind="REST_GET", supervisor_result=sup2["trusted_result"], raw_payload_bytes_data=payload1, settlement_receipt=sup2["settlement_receipt"])
        jnl.persist_r6_verified_chunk(chunk_id="chk_orphan", raw_payload=payload1, normalized_rows=[{"ts": 2}], provenance_receipt=prov2, simulate_crash_before_commit=True)
        ro_orphan = verify_r6_custody_readonly(jnl.store_dir, expected_partition="QA_FIXTURE", expected_run_id=f"{run_id}_d13")
        summary["qa_custody_chain_verified"] = (p_ok1["persisted"] is True and ro_clean["valid"] is True)
        summary["tamper_and_duplicate_receipt_rejected"] = (not p_dup["persisted"] and not p_forged["persisted"] and not p_tamper["persisted"] and not ro_orphan["valid"])
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-D13", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.R6CustodyJournal.persist_r6_verified_chunk", "custody_denied_or_caught", "custody_verified_clean", "Duplicate receipt, forged dict, SHA tamper & crash orphan caught vs verified chunk"),
            negative_fn=lambda: ({"orphans": len(ro_orphan["orphan_files_detected"])}, {"denied_or_caught": summary["tamper_and_duplicate_receipt_rejected"], "violations_count": len(p_dup["violations"]) + len(p_forged["violations"]) + len(p_tamper["violations"]) + len(ro_orphan["violations"])}),
            positive_fn=lambda: ({"committed_chunks": ro_clean["committed_chunks"]}, {"verified_clean": ro_clean["valid"], "violations_count": len(ro_clean["violations"])}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # D14: custody anchor rollback detected & cleanup preserves unrelated files + quarantines failed unlink
        unrelated = jnl.chunks_dir / "user_unrelated_keep.txt"
        unrelated.write_text("KEEP_ME", encoding="utf-8")
        cln = jnl.cleanup_owned_temp_files_safe()
        unrelated_kept = unrelated.exists() and unrelated.read_text("utf-8") == "KEEP_ME"
        unrelated.unlink()
        # Truncate custody_chain.jsonl behind custody.anchor.json (rollback!)
        jnl.chain_path.write_text("", encoding="utf-8")
        ro_rb = verify_r6_custody_readonly(jnl.store_dir, expected_partition="QA_FIXTURE", expected_run_id=f"{run_id}_d13")
        summary["anchor_rollback_detected"] = (not ro_rb["valid"] and any("ROLLBACK" in v for v in ro_rb["violations"]))
        summary["unrelated_files_preserved_on_cleanup"] = unrelated_kept and cln["all_owned_temps_deleted"]
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-D14", "D_DATA_CUSTODY", "INTEGRATION_FAULT", "services.kh_03_r6_custody_readiness_service.verify_r6_custody_readonly", "custody_denied_or_caught", "custody_verified_clean", "Custody chain rollback vs anchor detected & cleanup preserves unrelated files"),
            negative_fn=lambda: ({"rollback_viols": ro_rb["violations"]}, {"denied_or_caught": summary["anchor_rollback_detected"], "violations_count": len(ro_rb["violations"])}),
            positive_fn=lambda: ({"unrelated_kept": unrelated_kept}, {"verified_clean": summary["unrelated_files_preserved_on_cleanup"], "violations_count": 0}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # E01..E06: Release truth controls via derive_r6_readiness_verdicts & validate_r6_fault_pair
        base_arts = _minimal_passing_stage_a_artifacts(run_id, executable_sha)
        clean_v = derive_r6_readiness_verdicts(base_arts, stage="STAGE_A_OFFLINE", expected_run_id=run_id, expected_tested_sha=executable_sha)

        # E01: stale/mismatched SHA rejected vs exact fresh freeze
        bad_e01 = json.loads(json.dumps(base_arts))
        bad_e01["00"]["tested_executable_sha"] = "0" * 40
        v_e01 = derive_r6_readiness_verdicts(bad_e01, stage="STAGE_A_OFFLINE", expected_run_id=run_id, expected_tested_sha=executable_sha)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-E01", "E_RELEASE_TRUTH", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.derive_r6_readiness_verdicts", "release_gate_rejected", "release_gate_passed", "Stale/mismatched executable SHA fails G01 vs clean fresh freeze"),
            negative_fn=lambda: ({"g01": v_e01["gates"]["G01_BRANCH_SHA_CLEAN"]}, {"gate_passed": v_e01["gates"]["G01_BRANCH_SHA_CLEAN"] == "PASS", "expected_rejection_matched": v_e01["gates"]["G01_BRANCH_SHA_CLEAN"] == "FAIL"}),
            positive_fn=lambda: ({"g01": clean_v["gates"]["G01_BRANCH_SHA_CLEAN"]}, {"gate_passed": clean_v["gates"]["G01_BRANCH_SHA_CLEAN"] == "PASS", "violations_count": 0}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # E02: forged paired receipt / shared neg+pos execution_id rejected vs genuine receipt
        forged_r = json.loads(json.dumps(receipts[0]))
        forged_r["negative"]["execution_id"] = forged_r["positive"]["execution_id"]
        ok_f, v_f = validate_r6_fault_pair(forged_r, expected_executable_sha=executable_sha, expected_run_id=run_id)
        ok_g, v_g = validate_r6_fault_pair(receipts[0], expected_executable_sha=executable_sha, expected_run_id=run_id)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-E02", "E_RELEASE_TRUTH", "NEGATIVE_CONTRACT", "services.kh_03_r6_acceptance_service.validate_r6_fault_pair", "release_gate_rejected", "release_gate_passed", "Forged/shared execution_id paired receipt rejected vs genuine receipt"),
            negative_fn=lambda: ({"forged_viols": v_f}, {"gate_passed": ok_f, "expected_rejection_matched": "SHARED_NEG_POS_EXECUTION_ID" in v_f}),
            positive_fn=lambda: ({"genuine_ok": ok_g}, {"gate_passed": ok_g, "violations_count": len(v_g)}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # E03: missing JUnit / mandatory skip fails G21 vs exact 1:1 bijection
        bad_e03 = json.loads(json.dumps(base_arts))
        bad_e03["19"]["mandatory_skipped_count"] = 1
        v_e03 = derive_r6_readiness_verdicts(bad_e03, stage="STAGE_A_OFFLINE", expected_run_id=run_id, expected_tested_sha=executable_sha)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-E03", "E_RELEASE_TRUTH", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.derive_r6_readiness_verdicts", "release_gate_rejected", "release_gate_passed", "Mandatory skip fails G21 vs exact full pytest bijection"),
            negative_fn=lambda: ({"g21": v_e03["gates"]["G21_FULL_PYTEST"]}, {"gate_passed": v_e03["gates"]["G21_FULL_PYTEST"] == "PASS", "expected_rejection_matched": v_e03["gates"]["G21_FULL_PYTEST"] == "FAIL"}),
            positive_fn=lambda: ({"g21": clean_v["gates"]["G21_FULL_PYTEST"]}, {"gate_passed": clean_v["gates"]["G21_FULL_PYTEST"] == "PASS", "violations_count": 0}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # E04: semantic hash mismatch in worker benchmark fails G24/G25 vs deterministic match
        bad_e04 = json.loads(json.dumps(base_arts))
        bad_e04["20"]["r6_ingest_w1_w2_w4_all_identical"] = False
        v_e04 = derive_r6_readiness_verdicts(bad_e04, stage="STAGE_A_OFFLINE", expected_run_id=run_id, expected_tested_sha=executable_sha)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-E04", "E_RELEASE_TRUTH", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.derive_r6_readiness_verdicts", "release_gate_rejected", "release_gate_passed", "Benchmark semantic mismatch fails G24 vs deterministic W1/W2/W4 match"),
            negative_fn=lambda: ({"g24": v_e04["gates"]["G24_DETERMINISM"]}, {"gate_passed": v_e04["gates"]["G24_DETERMINISM"] == "PASS", "expected_rejection_matched": v_e04["gates"]["G24_DETERMINISM"] == "FAIL"}),
            positive_fn=lambda: ({"g24": clean_v["gates"]["G24_DETERMINISM"]}, {"gate_passed": clean_v["gates"]["G24_DETERMINISM"] == "PASS", "violations_count": 0}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # E05: injected readiness PASS despite unauthorized external traffic in Stage A fails vs clean Stage A blocked B/C
        bad_e05 = json.loads(json.dumps(base_arts))
        bad_e05["09"]["external_market_requests_attempted"] = 1
        v_e05 = derive_r6_readiness_verdicts(bad_e05, stage="STAGE_A_OFFLINE", expected_run_id=run_id, expected_tested_sha=executable_sha)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-E05", "E_RELEASE_TRUTH", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.derive_r6_readiness_verdicts", "release_gate_rejected", "release_gate_passed", "Unauthorized external traffic in Stage A fails overall readiness vs clean blocked Stage B/C"),
            negative_fn=lambda: ({"g31": v_e05["gates"]["G31_EXTERNAL_AUTHORIZATION"]}, {"gate_passed": v_e05["stage_a_ready_for_publication"], "expected_rejection_matched": not v_e05["stage_a_ready_for_publication"]}),
            positive_fn=lambda: ({"cls": clean_v["overall_classification"]}, {"gate_passed": clean_v["stage_a_ready_for_publication"] and clean_v["overall_classification"] == CLASSIFICATION_PASS_OFFLINE_R6 and clean_v["stage_denominators"]["stage_bc_executed_count"] == 0, "violations_count": 0}),
            executable_sha=executable_sha, run_id=run_id,
        ))

        # E06: fake activation / OOS > 0 / fake remote PASS rejected vs honest OFF/0/PENDING
        bad_e06 = json.loads(json.dumps(base_arts))
        bad_e06["09"]["activation_ready"] = "YES"
        bad_e06["09"]["oos_evaluations"] = 1
        v_e06 = derive_r6_readiness_verdicts(bad_e06, stage="STAGE_A_OFFLINE", expected_run_id=run_id, expected_tested_sha=executable_sha)
        receipts.append(execute_r6_fault_pair(
            R6FaultCaseSpec("R6-E06", "E_RELEASE_TRUTH", "NEGATIVE_CONTRACT", "services.kh_03_r6_custody_readiness_service.derive_r6_readiness_verdicts", "release_gate_rejected", "release_gate_passed", "Fake activation_ready=YES or OOS>0 fails G35 vs honest OFF/0"),
            negative_fn=lambda: ({"g35": v_e06["gates"]["G35_ECONOMIC_ACTIVATION"]}, {"gate_passed": v_e06["gates"]["G35_ECONOMIC_ACTIVATION"] == "PASS", "expected_rejection_matched": v_e06["gates"]["G35_ECONOMIC_ACTIVATION"] == "FAIL"}),
            positive_fn=lambda: ({"g35": clean_v["gates"]["G35_ECONOMIC_ACTIVATION"]}, {"gate_passed": clean_v["gates"]["G35_ECONOMIC_ACTIVATION"] == "PASS", "violations_count": 0}),
            executable_sha=executable_sha, run_id=run_id,
        ))

    return receipts, summary


def test_kh03_r6_paired_controls_d12_to_e06() -> None:
    recs, summary = run_kh03_r6_controls_d12_to_e06(executable_sha="5" * 40, run_id="test_r6_d12e06")
    assert len(recs) == 9
    verd = derive_r6_fault_verdict(recs, expected_executable_sha="5" * 40, expected_run_id="test_r6_d12e06")
    assert verd["valid"] is True, verd["violations"]
    assert verd["passed_controls"] == 9
    assert summary["symlink_junction_oos_denied_before_open"] is True
    assert summary["qa_promotion_to_dev_denied"] is True
    assert summary["anchor_rollback_detected"] is True


def test_kh03_r6_capture_cli_dry_run_and_c2_hard_block_intact() -> None:
    # 1. Capture CLI --dry-run runs with 0 sockets and prints unsigned template
    cli = APP_ROOT / "tools" / "run_kh_03_r6_capture.py"
    p_dry = subprocess.run([sys.executable, str(cli), "--stage", "smoke", "--dry-run"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)
    assert p_dry.returncode == 0, p_dry.stderr
    doc = json.loads(p_dry.stdout)
    assert doc["mode"] == "DRY_RUN_NO_SOCKET"
    assert doc["external_market_requests_attempted"] == 0
    assert doc["unsigned_owner_review_template"]["template_status"] == "UNSIGNED_OWNER_REVIEW_TEMPLATE"

    # 2. Capture CLI rejects --allow-live with exit code 2
    p_byp = subprocess.run([sys.executable, str(cli), "--stage", "smoke", "--allow-live"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)
    assert p_byp.returncode == 2

    # 3. Verify legacy C2 live hard block in capture_r5_public_batch is STILL 100% intact!
    inv = build_r5_source_capability_inventory(REPO_ROOT)
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)

    class _StubV:
        verifier_id = "R6_C2_HARDBLOCK_AUDIT_STUB"

        def verify_owner_attestation(self, g: Any, eff: Any, nu: Any, tc: Any) -> R5AttestationVerificationResult:
            return R5AttestationVerificationResult(True, "VERIFIED_SEPARATE_OWNER_ATTESTATION", "R6_AUDIT", self.verifier_id, g.grant_id, now_ms)

    with tempfile.TemporaryDirectory(prefix="r6_c2hb_") as td:
        dest = Path(td)
        g = R5OperatorGrant("G_C2HB", "OWNER", "DEVELOPMENT_CAPTURE", "XAUUSDT", "USDT-FUTURES", ("public_ticker",), 1794000000000, 1794000600000, 2, 8 * 1024 * 1024, 20, now_ms + 120_000, "r6_c2hb", False, "SEPARATE_OWNER_ATTESTATION", max_response_bytes=4 * 1024 * 1024)
        d = validate_r5_operator_capture_scope(g, {"symbol": "XAUUSDT", "product_type": "USDT-FUTURES", "endpoints": ["public_ticker"], "run_id": "r6_c2hb", "purpose": "DEVELOPMENT_CAPTURE", "window_start_ms": 1794000000000, "window_end_ms": 1794000600000, "max_requests": 2, "max_total_bytes": 8 * 1024 * 1024, "max_response_bytes": 4 * 1024 * 1024, "max_duration_sec": 20}, now, {}, attestation_verifier=_StubV())
        p = plan_r5_capture_batches(d, inv, {"destination_disk_free_gib": 40.0, "max_concurrency": 1, "max_pages": 2, "max_requests": 2, "max_total_bytes": 8 * 1024 * 1024, "max_response_bytes": 4 * 1024 * 1024, "max_duration_sec": 20, "endpoints": ["public_ticker"]}, now_utc=now)
        mon = make_r5_fixture_resource_monitor(disk_free_gib=40.0, destination=dest)
        res = asyncio.run(capture_r5_public_batch(p, StdlibUrllibPublicTransportC2(), mon, run_id="r6_c2hb", trusted_scope=d, destination_root=dest, utc_now_fn=lambda: now))
        assert res["capture_execution_status"] == "BLOCKED_LIVE_DURABILITY_AND_TERMINATION_UNPROVEN"
        assert res["external_requests_attempted"] == 0
