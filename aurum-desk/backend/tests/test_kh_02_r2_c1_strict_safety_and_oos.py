"""
KH-02.R2.C1 Dedicated Unit, Negative-Control, OOS-Contract & Separate-OS Verifier Tamper Tests:
  - Stage A (§4.5): All strict multi-mount disk safety, waiver rejection, mid-run drop abort,
    sentinel cleanup, and read-only disk inventory controls.
  - Stage B (§5.2-§5.6): Authoritative R2 freeze/embargo audit, read-only disabled-by-default
    collector, statistical power analysis refuting N=30, and all 20 synthetic OOS ingestion controls.
  - Stage E (§8.1-§8.3): Separate-OS independent verifier GREEN + 18 adversarial tamper RED controls.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest

from services.kh_02_r2_prospective_data_service import (
    R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
    R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
    R2_AUTHORITATIVE_FROZEN_CODE_SHA,
    R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
    ReadOnlyBitgetPublicProspectiveCollectorR2C1,
    audit_authoritative_r2_freeze_manifest_r2_c1,
    compute_prospective_oos_power_requirements_r2_c1,
    run_kh02_r2_c1_20_synthetic_oos_ingestion_faults,
)
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    CANONICAL_DATASET_SHA256_KH02_R2,
    MANDATORY_BRANCH_KH02_R2,
    PRIOR_KH02_R2_TESTED_SHA,
    RUNTIME_SAFETY_MODES_KH02_R2,
    SPEC_VERSION_KH02_R2_C1,
    build_r2_c1_readonly_disk_inventory,
    create_sentinel_owned_temp_dir_r2_c1,
    probe_r2_c1_write_mounts_strict,
    safe_cleanup_sentinel_owned_temp_dir_r2_c1,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.kh_02_r2_c1_independent_verifier import (
    recompute_c1_15_orthogonal_verdicts_from_disk,
)
from tools.run_kh_02_r2_c1_strict_closure import run_kh02_r2_c1_strict_closure
from tools.run_kh_02_r2_verification import _BoundedResourceSampler, run_kh02_r2_orchestrator

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
C1_VERIFIER_SCRIPT = APP_ROOT / "tools" / "kh_02_r2_c1_independent_verifier.py"
R2_RUNNER_SCRIPT = APP_ROOT / "tools" / "run_kh_02_r2_verification.py"
C1_RUNNER_SCRIPT = APP_ROOT / "tools" / "run_kh_02_r2_c1_strict_closure.py"


def test_stage_a_strict_disk_boundaries_dedup_mounts_and_waiver_rejection(tmp_path: Path) -> None:
    def _probe_val(free_by_role: dict[str, tuple[int, float, str]], *, reserved: float = 0.05) -> dict:
        paths = {}
        devs, gibs, mps = {}, {}, {}
        for role, (dev, gib, mp) in free_by_role.items():
            d = tmp_path / f"role_{role}"
            d.mkdir(exist_ok=True)
            rp = os.path.realpath(str(d))
            paths[role] = d
            devs[rp] = dev
            gibs[rp] = gib
            mps[rp] = mp
        return probe_r2_c1_write_mounts_strict(
            paths,
            reserved_evidence_gib=reserved,
            stat_fn=lambda s: SimpleNamespace(st_dev=devs[s]),
            disk_usage_fn=lambda s: SimpleNamespace(
                total=int(500 * 1024**3),
                used=int((500.0 - gibs[s]) * 1024**3),
                free=int(round(gibs[s] * 1024**3)),
            ),
            mountpoint_fn=lambda p: mps[os.path.realpath(str(p))],
        )

    # 1. Exact §4.5 below-floor thresholds: 20.499, 19.999, 5.6567, 1.999 -> NEVER run heavy
    for low_gib in (20.499, 19.999, 5.6567, 1.999):
        res = _probe_val({"repo": (1, low_gib, "/System/Volumes/Data")})
        assert res["status"] == "BLOCKED_LOW_DISK"
        assert res["can_run_heavy_stages"] is False
        assert res["owner_waiver_permitted"] is False

    # 2. Exact 20.500 GiB boundary passes when reserved headroom keeps post-reserve >= 20.0 GiB
    exact_ok = _probe_val({"repo": (1, 20.500, "/System/Volumes/Data")}, reserved=0.05)
    assert exact_ok["status"] == "PASS_STRICT_BELOW_ADVISORY_25GIB"
    assert exact_ok["can_run_heavy_stages"] is True

    # Oversubscription at 20.500 GiB (20.500 - 0.600 = 19.900 < 20.000) MUST block
    exact_oversub = _probe_val({"repo": (1, 20.500, "/System/Volumes/Data")}, reserved=0.60)
    assert exact_oversub["status"] == "BLOCKED_LOW_DISK"
    assert exact_oversub["can_run_heavy_stages"] is False

    # 3. Alias/symlink/NTFS-junction mount duplicates deduplicated by st_dev (no double counting)
    real_d = tmp_path / "real_vol"
    real_d.mkdir()
    sym_d = tmp_path / "sym_vol"
    try:
        os.symlink(real_d, sym_d)
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            subprocess.run(["cmd", "/c", "mklink", "/J", str(sym_d), str(real_d)], check=True, capture_output=True)
        else:
            raise
    r_sym = probe_r2_c1_write_mounts_strict(
        {"repo": real_d, "temp": sym_d},
        stat_fn=lambda _s: SimpleNamespace(st_dev=99),
        disk_usage_fn=lambda _s: SimpleNamespace(total=100 * 1024**3, used=88 * 1024**3, free=int(12.0 * 1024**3)),
        mountpoint_fn=lambda _p: "/System/Volumes/Data",
    )
    assert r_sym["deduplicated_distinct_mounts_count"] == 1
    assert r_sym["can_run_heavy_stages"] is False
    assert r_sym["min_free_gib"] == 12.0

    # 4. Distinct disk mounts enforced independently
    r_multi = _probe_val({"repo": (101, 45.0, "/Volumes/DataA"), "temp": (202, 19.5, "/Volumes/TempB")})
    assert r_multi["deduplicated_distinct_mounts_count"] == 2
    assert r_multi["status"] == "BLOCKED_LOW_DISK"
    assert r_multi["blocked_mountpoints"] == ["/Volumes/TempB"]

    # 5. Unreadable / missing mount fails closed
    r_missing = probe_r2_c1_write_mounts_strict({"repo": tmp_path / "missing_mount"}, allow_nonexistent_leaf_roles=())
    assert r_missing["status"] == "FAIL_MOUNT_PROBE_ERROR"
    assert r_missing["can_run_heavy_stages"] is False

    # 6. Lowering numeric disk thresholds raises ValueError
    with pytest.raises(ValueError, match="ILLEGAL_LOWERED_PREFLIGHT_DISK_FLOOR"):
        probe_r2_c1_write_mounts_strict({"repo": tmp_path}, strict_preflight_min_gib=15.0)
    with pytest.raises(ValueError, match="ILLEGAL_LOWERED_RUNTIME_DISK_FLOOR"):
        probe_r2_c1_write_mounts_strict({"repo": tmp_path}, strict_runtime_min_gib=10.0)

    # 7. CLI & API --owner-waive-disk-preflight REJECTED everywhere
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    for script in (R2_RUNNER_SCRIPT, C1_RUNNER_SCRIPT):
        p = subprocess.run(
            [sys.executable, str(script), "--owner-waive-disk-preflight"],
            cwd=str(APP_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert p.returncode == 2
        assert "UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN" in (p.stderr + p.stdout)

    with pytest.raises(RuntimeError, match="UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN"):
        run_kh02_r2_orchestrator(owner_waive_disk_preflight=True)
    with pytest.raises(RuntimeError, match="UNAPPROVED_AUTONOMOUS_DISK_WAIVER_FORBIDDEN"):
        run_kh02_r2_c1_strict_closure(owner_waive_disk_preflight=True)


def test_stage_a_midrun_disk_drop_abort_sentinel_guard_and_readonly_inventory(tmp_path: Path) -> None:
    # 1. Mid-run disk drop below 20.0 GiB triggers monitored hard abort & reaps spawned child
    seq = iter([25.0, 19.8, 19.8])
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    log_gz = tmp_path / "midrun.jsonl.gz"
    sampler = _BoundedResourceSampler(
        out_gz=log_gz,
        watch_path=tmp_path,
        interval_sec=0.02,
        enforce_runtime_disk_abort=True,
        disk_free_fn=lambda _p: next(seq, 19.8),
    )
    sampler.start()
    deadline = time.monotonic() + 3.0
    while not sampler.abort_event.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    t_res = sampler.stop_and_flush()
    try:
        child.wait(timeout=2.0)
    except Exception:
        pass
    assert t_res["emergency_abort_triggered"] is True
    assert "RUNTIME_STRICT_DISK_FLOOR_BREACH" in str(t_res["emergency_abort_reason"])
    assert child.poll() is not None
    assert t_res["orphan_children_after_cleanup"] == 0

    # 2. Unowned sentinel-less temp directory CANNOT be deleted; owned sentinel dir cleans cleanly
    unowned = tmp_path / "unowned_user_dir"
    unowned.mkdir()
    (unowned / "precious.txt").write_text("do_not_delete", encoding="utf-8")
    with pytest.raises(PermissionError, match="UNOWNED_TEMP_DIR_MISSING_SENTINEL"):
        safe_cleanup_sentinel_owned_temp_dir_r2_c1(unowned, expected_run_id="run_test")
    assert (unowned / "precious.txt").is_file()

    owned = create_sentinel_owned_temp_dir_r2_c1("owned_test_", run_id="run_test", base_dir=tmp_path)
    with pytest.raises(PermissionError, match="TEMP_SENTINEL_RUN_ID_MISMATCH"):
        safe_cleanup_sentinel_owned_temp_dir_r2_c1(owned, expected_run_id="wrong_run_id")
    assert owned.is_dir()
    cleaned = safe_cleanup_sentinel_owned_temp_dir_r2_c1(owned, expected_run_id="run_test")
    assert cleaned["cleaned"] is True
    assert not owned.exists()

    # 3. Read-only disk inventory executes ZERO deletions
    inv = build_r2_c1_readonly_disk_inventory(REPO_ROOT)
    assert inv["inventory_mode"] == "READ_ONLY_ZERO_MUTATIONS"
    assert inv["autonomous_deletion_executed"] is False
    assert inv["git_gc_executed"] is False
    assert all(cmd["executed"] is False for cmd in inv["manual_inspection_commands_NOT_AUTOMATICALLY_EXECUTED"])


def test_stage_b_authoritative_freeze_collector_power_and_20_synthetic_oos_controls(tmp_path: Path) -> None:
    # 1. Authoritative R2 freeze manifest matches on disk verbatim
    frz = audit_authoritative_r2_freeze_manifest_r2_c1(REPO_ROOT)
    assert frz["status"] == "PASS"
    assert frz["sha256_match"] is True
    assert frz["observed_freeze_coordinates"]["freeze_timestamp_ms"] == R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS
    assert frz["observed_freeze_coordinates"]["embargo_cutoff_ms"] == R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS

    # Missing/mismatched freeze manifest fails closed
    frz_bad = audit_authoritative_r2_freeze_manifest_r2_c1(tmp_path)
    assert frz_bad["status"] == "FAIL_FREEZE_MANIFEST_MISMATCH"

    # 2. Read-only Bitget public collector disabled by default & blocked on low disk
    col = ReadOnlyBitgetPublicProspectiveCollectorR2C1()
    desc = col.describe_readiness()
    assert desc["read_only"] is True
    assert desc["network_capture_enabled"] is False
    assert desc["order_or_private_endpoints_present"] is False
    assert desc["api_key_or_secret_reads_present"] is False
    with pytest.raises(PermissionError, match="PROSPECTIVE_NETWORK_CAPTURE_DISABLED_BY_DEFAULT"):
        col.fetch_public_snapshot("public_ticker", write_mount_probe={"can_run_heavy_stages": True})

    col_en = ReadOnlyBitgetPublicProspectiveCollectorR2C1(
        network_capture_enabled=True, explicit_operator_approval_id="TEST_ONLY"
    )
    with pytest.raises(RuntimeError, match="BLOCKED_LOW_DISK_CAPTURE_FORBIDDEN"):
        col_en.fetch_public_snapshot("public_ticker", write_mount_probe={"can_run_heavy_stages": False, "status": "BLOCKED_LOW_DISK", "min_free_gib": 9.1})

    # 3. Statistical power analysis proves fixed N = 30 trades does NOT prove edge
    pwr = compute_prospective_oos_power_requirements_r2_c1()
    assert pwr["fixed_30_trades_proves_edge"] is False
    assert all(row["fixed_n_30_sufficient"] is False for row in pwr["sample_size_grid"])

    # 4. All 20 §5.6 synthetic OOS validator regression controls pass and are explicitly labeled synthetic
    v20 = run_kh02_r2_c1_20_synthetic_oos_ingestion_faults(tmp_path / "oos_v20")
    assert v20["total_cases"] == 20
    assert v20["passed_cases"] == 20
    assert v20["failed_cases"] == 0
    assert v20["validates_real_market_data"] is False
    assert v20["status"] == "PASS"


def _build_minimal_valid_synthetic_c1_run_dir(
    run_dir: Path,
    *,
    disk_free_gib: float = 9.1058,
    include_junit_pass: bool = False,
) -> tuple[str, str]:
    sha = "f" * 40
    run_id = f"20261122T010000Z_{sha[:12]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)

    def _wj(name: str, obj: dict) -> None:
        (run_dir / name).write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")

    def _wgz(name: str, rows: list) -> None:
        with gzip.open(run_dir / name, "wt", encoding="utf-8") as gz:
            for r in rows:
                gz.write(json.dumps(r, sort_keys=True) + "\n")

    strict_ok = disk_free_gib >= 20.5
    f00 = {
        "spec_version": SPEC_VERSION_KH02_R2_C1,
        "run_id": run_id,
        "branch": MANDATORY_BRANCH_KH02_R2,
        "start_head_sha": sha,
        "end_head_sha": sha,
        "tested_executable_sha": sha,
        "orchestrator_pid": os.getpid(),
        "git_clean_at_start": strict_ok,
        "local_uncommitted_c1_changes": not strict_ok,
        "owner_disk_preflight_waiver": False,
        "owner_disk_waiver_permitted": False,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
    }
    f01 = {
        "status": "PASS",
        "locked_files_modified_count": 0,
        "forbidden_order_import_hits": [],
        "cost_assumptions_defaults_intact": True,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
    }
    f02 = {
        "owner_disk_preflight_waiver": False,
        "owner_waiver_permitted": False,
        "strict_multi_mount_probe": {
            "owner_waiver_permitted": False,
            "status": "PASS_STRICT_AND_ADVISORY" if disk_free_gib >= 25.0 else ("PASS_STRICT_BELOW_ADVISORY_25GIB" if strict_ok else "BLOCKED_LOW_DISK"),
            "can_run_heavy_stages": strict_ok,
            "min_free_gib": disk_free_gib,
            "probe_errors": [],
            "distinct_mounts": [
                {
                    "st_dev": 16777229,
                    "mountpoint": "/System/Volumes/Data",
                    "free_gib": disk_free_gib,
                    "projected_post_reserve_gib": round(disk_free_gib - 0.05, 4),
                    "passes_all_strict_floors": strict_ok,
                }
            ],
        },
    }
    f03 = {"status": "PASS", "total_controls": 12, "passed_controls": 12, "failed_controls": 0}
    f04 = {
        "authoritative_r2_freeze_audit": {
            "status": "PASS",
            "observed_freeze_coordinates": {
                "frozen_candidate_code_sha": R2_AUTHORITATIVE_FROZEN_CODE_SHA,
                "frozen_config_sha256": R2_AUTHORITATIVE_FROZEN_CONFIG_SHA256,
                "freeze_timestamp_ms": R2_AUTHORITATIVE_FREEZE_TIMESTAMP_MS,
                "embargo_cutoff_ms": R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS,
            },
        },
        "reused_dataset_limitations": {
            "quote_mode": "MODELED_BID_ASK",
            "observed_l1_bbo_available": False,
            "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        },
        "proposed_snapshots_count": 0,
        "accepted_prospective_snapshots_count": 0,
        "accepted_prospective_snapshots": [],
        "OOS_EVALUATIONS": 0,
        "FUTURE_OOS_READINESS_VERDICT": "BLOCKED_NO_VERIFIED_NEW_DATA",
        "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
        "escrow_contract_integrity_status": "PASS",
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
    }
    f05 = {"status": "PASS", "total_cases": 20, "passed_cases": 20, "failed_cases": 0, "validates_real_market_data": False}
    f06 = {
        "status": "PASS",
        "swept_tp_accepted_count": 0,
        "t01_to_t10_all_passed": True,
        "future_mutation_invariance_passed": True,
        "m1_between_htf_sweep_detected": True,
        "outcome_ledger_sample": [
            {"candidate_id": "cand_01", "decision_timestamp_ms": 1000},
            {"candidate_id": "cand_02", "decision_timestamp_ms": 2000},
        ],
    }
    f07 = {
        "status": "PASS",
        "b0_vs_b2_identifiability_status": "COMPARISON_NOT_IDENTIFIABLE",
        "matching_inadequate_guard_verified": True,
        "PAIRED_RESEARCH_VERDICT": "IDENTIFIABLE_WITH_PARTIAL_COMMON_SUPPORT",
    }
    f08 = {
        "status": "PASS",
        "actual_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "fixed_fill_zero_dropped_ids_verified": True,
        "full_rerisk_starts_from_full_universe": True,
        "fill_time_cap_violations": 0,
        "realized_tail_risk_disclosed": True,
        "fixed_fill_decimal_checks": {
            "B2_H1": {"gross_pnl_usd": -10.0, "fees_usd": 1.0, "net_pnl_usd": -11.0}
        },
    }
    f09 = {
        "status": "PASS",
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
    }

    if include_junit_pass and strict_ok:
        jdir = run_dir / "junit"
        jdir.mkdir(exist_ok=True)
        xp = jdir / "s1.xml"
        xp.write_text(
            '<testsuite tests="1" failures="0" errors="0" skipped="0">'
            '<testcase classname="backend.tests.test_kh_02_r2_sample" name="test_ok"/>'
            "</testsuite>",
            encoding="utf-8",
        )
        xsha = canonical_hash(xp.read_text("utf-8"))
        import hashlib
        fsha = hashlib.sha256(xp.read_bytes()).hexdigest()
        nids = ["backend/tests/test_kh_02_r2_sample.py::test_ok"]
        cm = {
            "run_id": run_id,
            "tested_executable_sha": sha,
            "collect_exit_code": 0,
            "collect_pid": 1234,
            "collected_node_ids": nids,
            "collected_node_ids_count": 1,
            "collected_node_ids_sha256": canonical_hash(nids),
        }
        sm = {
            "run_id": run_id,
            "tested_executable_sha": sha,
            "shards": [
                {
                    "shard_name": "s1",
                    "junit_xml_path": "junit/s1.xml",
                    "xml_sha256": fsha,
                    "junit_sha256": fsha,
                    "xml_mtime_ns": xp.stat().st_mtime_ns,
                    "exit_code": 0,
                    "pid": 1235,
                    "reused": False,
                    "run_id": run_id,
                    "tested_executable_sha": sha,
                    "scheduled_node_ids": nids,
                }
            ],
        }
        f10 = {
            "status": "PASS",
            "collected_count": 1,
            "executed_count": 1,
            "failed_count": 0,
            "error_count": 0,
            "mandatory_skipped_count": 0,
            "junit_bijection_exact": True,
            "collection_manifest": cm,
            "shard_manifest": sm,
        }
        f11 = {"status": "PASS"}
        f14 = {
            "BASELINE_PARITY_VERDICT": "PASS",
            "prior_evidence_immutability_verified": True,
            "canonical_dataset_sha256_match": True,
            "a1_mismatches_count": 0,
            "si8_mismatches_count": 0,
            "three_adapter_mismatches_count": 0,
            "d4_historical_closed_trades": 0,
            "prior_sealed_artifacts_immutability": {"KH02_R2_LATEST_PTR": {"match": True}},
        }
        f17 = {
            "run_id": run_id,
            "tested_executable_sha": sha,
            "owner_disk_preflight_waiver": False,
            "publication_status": "PRE_PUSH_PENDING",
        }
    else:
        f10 = {"status": "NOT_RUN_BLOCKED_LOW_DISK", "collected_count": 0, "executed_count": 0}
        f11 = {"status": "NOT_RUN_BLOCKED_LOW_DISK"}
        f14 = {
            "BASELINE_PARITY_VERDICT": "NOT_RUN_BLOCKED_LOW_DISK",
            "prior_evidence_immutability_verified": True,
            "canonical_dataset_sha256_match": True,
            "prior_sealed_artifacts_immutability": {"KH02_R2_LATEST_PTR": {"match": True}},
        }
        f17 = {
            "run_id": run_id,
            "tested_executable_sha": sha,
            "owner_disk_preflight_waiver": False,
            "publication_status": "NOT_PUBLISHED_BLOCKED_LOW_DISK",
        }

    f12 = {
        "min_available_ram_mib": 3200.0,
        "min_disk_free_gib": disk_free_gib,
        "max_descendant_children": 1,
        "orphan_children_after_cleanup": 0,
        "sentinel_temp_cleaned_up": True,
        "owner_disk_preflight_waiver": False,
    }
    _wgz(
        "logs/runtime_memory_samples_0p5s.jsonl.gz",
        [
            {"ts_ms": 1, "available_ram_bytes": 3200 * 1024**2, "rss_bytes": 50 * 1024**2, "child_rss_bytes": 0, "worker_pids": [101]},
            {"ts_ms": 2, "available_ram_bytes": 3100 * 1024**2, "rss_bytes": 52 * 1024**2, "child_rss_bytes": 0, "worker_pids": [101]},
        ],
    )
    f13 = {"status": "PASS", "total_controls": 56, "passed_controls": 56, "failed_controls": 0}

    docs = {
        "00": f00, "01": f01, "02": f02, "03": f03, "04": f04, "05": f05,
        "06": f06, "07": f07, "08": f08, "09": f09, "10": f10, "11": f11,
        "12": f12, "13": f13, "14": f14, "17": f17,
    }
    recomp = recompute_c1_15_orthogonal_verdicts_from_disk(
        run_dir,
        docs=docs,
        mem_audit={"status": "PASS", "below_floor_samples_count": 0, "max_workers_observed_in_samples": 1},
        junit_bijection_ok=bool(include_junit_pass and strict_ok),
        for_final_verifier_manifest=False,
    )
    v15 = recomp["orthogonal_15_verdicts"]
    f15 = {
        "run_id": run_id,
        "tested_executable_sha": sha,
        "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "orthogonal_15_verdicts": v15,
    }
    for num, name in (
        ("00", "00_git_source_branch_preflight_sha.json"),
        ("01", "01_function_ownership_callgraph_and_locked_diff.json"),
        ("02", "02_disk_mount_preflight_and_policy.json"),
        ("03", "03_disk_waiver_bypass_negative_tests.json"),
        ("04", "04_prospective_oos_registry_and_source_provenance.json"),
        ("05", "05_oos_ingestion_validation_fault_controls.json"),
        ("06", "06_unswept_tp_and_future_mutation_regression.json"),
        ("07", "07_entry_geometry_pairing_and_balance.json"),
        ("08", "08_fixed_ids_vs_full_rerisk_cost_and_fill_time_risk.json"),
        ("09", "09_walkforward_uncertainty_and_economic_verdict.json"),
        ("10", "10_full_pytest_collection_junit_bijection_exit.json"),
        ("11", "11_async_w1_w2_w4_repeat_measured_determinism.json"),
        ("12", "12_ram_disk_pid_runtime_telemetry.json"),
        ("13", "13_red_green_fault_injection_receipts.json"),
        ("14", "14_production_baseline_immutable_parity_fingerprints.json"),
        ("17", "17_publication_tested_sha_remote_readback_receipt.json"),
    ):
        _wj(name, docs[num])
    _wj("15_orthogonal_gate_matrix.json", f15)

    disk_v = v15["STRICT_DISK_SAFETY_VERDICT"]
    (run_dir / "README_KH02_R2_C1.md").write_text(
        f"# KH-02.R2.C1\nrun_id={run_id}\nACTIVATION_READY=NO\n{disk_v}\nNO_PROVEN_EDGE\n", encoding="utf-8"
    )
    (run_dir / "HANDOVER_KH02_R2_C1_VI.txt").write_text(
        f"run_id={run_id}\nACTIVATION_READY=NO\n{disk_v}\nNO_PROVEN_EDGE\n", encoding="utf-8"
    )
    return run_id, sha


def _run_c1_verifier_proc(run_dir: Path, run_id: str, sha: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)])
    return subprocess.run(
        [
            sys.executable,
            str(C1_VERIFIER_SCRIPT),
            "--run-dir",
            str(run_dir),
            "--expected-run-id",
            run_id,
            "--expected-tested-sha",
            sha,
            "--skip-live-repo-pointers",
            "--no-write-manifest",
        ],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_stage_e_separate_os_c1_verifier_green_and_18_adversarial_tampers(tmp_path: Path) -> None:
    clean_blocked = tmp_path / "clean_blocked"
    run_id_b, sha_b = _build_minimal_valid_synthetic_c1_run_dir(clean_blocked, disk_free_gib=9.1058, include_junit_pass=False)
    ok_b = _run_c1_verifier_proc(clean_blocked, run_id_b, sha_b)
    assert ok_b.returncode == 0, ok_b.stdout + ok_b.stderr
    out_b = json.loads(ok_b.stdout)
    assert out_b["independent_verifier_status"] == "PASS"
    assert out_b["distinct_os_process_verified"] is True
    assert out_b["recomputed_15_orthogonal_verdicts"]["STRICT_DISK_SAFETY_VERDICT"] == "BLOCKED_LOW_DISK"

    clean_full = tmp_path / "clean_full"
    run_id_f, sha_f = _build_minimal_valid_synthetic_c1_run_dir(clean_full, disk_free_gib=30.0, include_junit_pass=True)
    ok_f = _run_c1_verifier_proc(clean_full, run_id_f, sha_f)
    assert ok_f.returncode == 0, ok_f.stdout + ok_f.stderr

    def _clone(src: Path, name: str) -> Path:
        dst = tmp_path / name
        shutil.copytree(src, dst)
        return dst

    def _edit_json(d: Path, fname: str, fn) -> None:
        p = d / fname
        obj = json.loads(p.read_text("utf-8"))
        fn(obj)
        p.write_text(json.dumps(obj), encoding="utf-8")

    # Prepare ALL 18 adversarial tamper directories upfront, then verify concurrently with
    # bounded max_workers=3 separate-OS verifier processes (1 pytest + 3 verifiers <= 4 cap!).
    tamper_jobs: list[tuple[str, Path, str, str]] = []

    # T01: Previous-run JUnit reused (wrong run_id in shard manifest)
    d = _clone(clean_full, "t01_reused_junit")
    _edit_json(d, "10_full_pytest_collection_junit_bijection_exit.json", lambda o: o["shard_manifest"]["shards"][0].__setitem__("run_id", "old_run_id"))
    tamper_jobs.append(("T01_reused_junit", d, run_id_f, sha_f))

    # T02: Collected/executed mismatch
    d = _clone(clean_full, "t02_collect_mismatch")
    _edit_json(d, "10_full_pytest_collection_junit_bijection_exit.json", lambda o: o.__setitem__("collected_count", 99))
    tamper_jobs.append(("T02_collect_mismatch", d, run_id_f, sha_f))

    # T03: Skipped mandatory test in JUnit XML
    d = _clone(clean_full, "t03_mandatory_skip")
    (d / "junit" / "s1.xml").write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="1">'
        '<testcase classname="backend.tests.test_kh_02_r2_sample" name="test_ok"><skipped/></testcase>'
        "</testsuite>",
        encoding="utf-8",
    )
    tamper_jobs.append(("T03_mandatory_skip", d, run_id_f, sha_f))

    # T04: Wrong branch
    d = _clone(clean_blocked, "t04_wrong_branch")
    _edit_json(d, "00_git_source_branch_preflight_sha.json", lambda o: o.__setitem__("branch", "main"))
    tamper_jobs.append(("T04_wrong_branch", d, run_id_b, sha_b))

    # T05: Stale R2 tested SHA reused
    d = _clone(clean_blocked, "t05_stale_sha")
    for fn in ("00_git_source_branch_preflight_sha.json", "15_orthogonal_gate_matrix.json", "17_publication_tested_sha_remote_readback_receipt.json"):
        _edit_json(d, fn, lambda o: o.__setitem__("tested_executable_sha", PRIOR_KH02_R2_TESTED_SHA))
    tamper_jobs.append(("T05_stale_sha", d, run_id_b, PRIOR_KH02_R2_TESTED_SHA))

    # T06: Code changed during tests (start_head_sha != end_head_sha)
    d = _clone(clean_blocked, "t06_midrun_code_change")
    _edit_json(d, "00_git_source_branch_preflight_sha.json", lambda o: o.__setitem__("end_head_sha", "1" * 40))
    tamper_jobs.append(("T06_midrun_code_change", d, run_id_b, sha_b))

    # T07: Reordered outcome ledger
    d = _clone(clean_blocked, "t07_reordered_ledger")
    _edit_json(d, "06_unswept_tp_and_future_mutation_regression.json", lambda o: o.__setitem__("outcome_ledger_sample", [{"candidate_id": "c2", "decision_timestamp_ms": 2000}, {"candidate_id": "c1", "decision_timestamp_ms": 1000}]))
    tamper_jobs.append(("T07_reordered_ledger", d, run_id_b, sha_b))

    # T08: Duplicate candidate IDs
    d = _clone(clean_blocked, "t08_dup_cand_id")
    _edit_json(d, "06_unswept_tp_and_future_mutation_regression.json", lambda o: o.__setitem__("outcome_ledger_sample", [{"candidate_id": "c1", "decision_timestamp_ms": 1000}, {"candidate_id": "c1", "decision_timestamp_ms": 2000}]))
    tamper_jobs.append(("T08_dup_cand_id", d, run_id_b, sha_b))

    # T09: Swept TP labeled as unswept / accepted
    d = _clone(clean_blocked, "t09_swept_tp")
    _edit_json(d, "06_unswept_tp_and_future_mutation_regression.json", lambda o: o.__setitem__("swept_tp_accepted_count", 1))
    tamper_jobs.append(("T09_swept_tp", d, run_id_b, sha_b))

    # T10: Synthetic/modeled quote labeled observed
    d = _clone(clean_blocked, "t10_fake_observed")
    _edit_json(d, "04_prospective_oos_registry_and_source_provenance.json", lambda o: o["reused_dataset_limitations"].__setitem__("quote_mode", "OBSERVED_L1_BBO"))
    tamper_jobs.append(("T10_fake_observed", d, run_id_b, sha_b))

    # T11: Old DEV dataset SHA relabeled fresh OOS
    d = _clone(clean_blocked, "t11_old_dev_as_oos")
    _edit_json(d, "04_prospective_oos_registry_and_source_provenance.json", lambda o: o.__setitem__("accepted_prospective_snapshots", [{"dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2, "start_utc_ms": R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS}]))
    tamper_jobs.append(("T11_old_dev_as_oos", d, run_id_b, sha_b))

    # T12: Pre-freeze / within-embargo OOS accepted
    d = _clone(clean_blocked, "t12_embargo_oos")
    _edit_json(d, "04_prospective_oos_registry_and_source_provenance.json", lambda o: o.__setitem__("accepted_prospective_snapshots", [{"dataset_sha256": "9" * 64, "start_utc_ms": R2_AUTHORITATIVE_EMBARGO_CUTOFF_MS - 1}]))
    tamper_jobs.append(("T12_embargo_oos", d, run_id_b, sha_b))

    # T13: OOS evaluation counter > 0
    d = _clone(clean_blocked, "t13_oos_peek")
    _edit_json(d, "04_prospective_oos_registry_and_source_provenance.json", lambda o: o.__setitem__("OOS_EVALUATIONS", 1))
    tamper_jobs.append(("T13_oos_peek", d, run_id_b, sha_b))

    # T14: Unapproved owner disk waiver injected
    d = _clone(clean_blocked, "t14_disk_waiver")
    _edit_json(d, "00_git_source_branch_preflight_sha.json", lambda o: o.__setitem__("owner_disk_preflight_waiver", True))
    tamper_jobs.append(("T14_disk_waiver", d, run_id_b, sha_b))

    # T15: Insufficient disk mount forged as STRICT_DISK_SAFETY_VERDICT = PASS
    d = _clone(clean_blocked, "t15_forged_disk_pass")
    _edit_json(d, "15_orthogonal_gate_matrix.json", lambda o: o["orthogonal_15_verdicts"].__setitem__("STRICT_DISK_SAFETY_VERDICT", "PASS"))
    tamper_jobs.append(("T15_forged_disk_pass", d, run_id_b, sha_b))

    # T16: RAM floor breach / surviving orphan
    d = _clone(clean_blocked, "t16_orphan_ram")
    _edit_json(d, "12_ram_disk_pid_runtime_telemetry.json", lambda o: o.__setitem__("orphan_children_after_cleanup", 1))
    tamper_jobs.append(("T16_orphan_ram", d, run_id_b, sha_b))

    # T17: Fee 0.0006 silently substituted as verified account tier + dropped fixed-fill IDs
    d = _clone(clean_blocked, "t17_fee_and_dropped_ids")
    _edit_json(d, "08_fixed_ids_vs_full_rerisk_cost_and_fill_time_risk.json", lambda o: o.update({"actual_account_effective_fee_tier": "VERIFIED_0p0006", "fixed_fill_zero_dropped_ids_verified": False}))
    tamper_jobs.append(("T17_fee_and_dropped_ids", d, run_id_b, sha_b))

    # T18: Overwritten prior KH-02.R2 artifact & NO_PROVEN_EDGE rewritten PROVEN_EDGE
    d = _clone(clean_blocked, "t18_overwrite_and_proven_edge")
    _edit_json(d, "14_production_baseline_immutable_parity_fingerprints.json", lambda o: o["prior_sealed_artifacts_immutability"]["KH02_R2_LATEST_PTR"].__setitem__("match", False))
    _edit_json(d, "09_walkforward_uncertainty_and_economic_verdict.json", lambda o: o.__setitem__("RESEARCH_ECONOMIC_VERDICT", "PROVEN_EDGE"))
    tamper_jobs.append(("T18_overwrite_and_proven_edge", d, run_id_b, sha_b))

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(lambda job: (job[0], _run_c1_verifier_proc(job[1], job[2], job[3])), tamper_jobs)
        )
    for label, proc in results:
        assert proc.returncode != 0, f"Tamper {label} unexpectedly passed: {proc.stdout}"
