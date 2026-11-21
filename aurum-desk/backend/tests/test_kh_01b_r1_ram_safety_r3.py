"""
backend/tests/test_kh_01b_r1_ram_safety_r3.py

Comprehensive unit, async scheduler, real-subprocess OOM/cancellation, static source,
and separate-OS-process adversarial tamper tests for KH-01B.R1.DSC.R3: RAM Safety &
Adaptive Test Scheduling Closure (`KH-01B.R1.DSC.R3-v1.0`, §3.F, §4, §5 Phase 2, §7 S01..S14).

Mocks memory providers instead of intentionally exhausting host RAM, executes real
lightweight Python child processes for OOM/JUnit/verifier verification, and mutates zero
tracked repository datasets or prior R2/KH artifacts.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

from domain_calculator import CostAssumptions
from services.kh_01a_r1_forensics_closure_service import build_dynamic_validated_gate_receipt_r1
from services.kh_01b_r1_data_safety_closure_service import (
    CANONICAL_DATASET_SHA256_KH01B_R1,
    MANDATORY_BRANCH_KH01B_R1,
    run_kh01b_r1_dsc_red_green_fault_suite,
)
from services.kh_01b_r1_ram_safety_r3_service import (
    HARD_ABORT_RAM_FLOOR_BYTES_R3,
    PRIOR_R2_LATEST_PTR_REL,
    PRIOR_R2_LATEST_PTR_SHA256,
    PRIOR_R2_MANIFEST_15_SHA256,
    PRIOR_R2_PUBLICATION_HEAD_SHA,
    PRIOR_R2_RUN_REL_DIR,
    PRIOR_R2_TESTED_SHA,
    RAM_POLICY_VERSION_R3,
    SCHEMA_VERSION_KH01B_R1_DSC_R3,
    SEALED_R2_A1_56710_IDS_SHA256,
    SEALED_R2_E00_BUNDLE_SHA256,
    SEALED_R2_SI0_242_TUPLES_SHA256,
    SEALED_R2_SI8_6375_IDS_SHA256,
    SPEC_VERSION_KH01B_R1_DSC_R3,
    audit_r3_memory_samples_jsonl_gz,
    classify_shard_exit_and_oom_r3,
    compute_adaptive_slot_limit_r3,
    compute_r3_ram_preflight_and_policy,
    detect_active_64mib_ram_floor_in_source,
    evaluate_kh01b_r1_ram_safety_r3_gates_from_disk,
    evaluate_r3_shard_admission,
    render_kh01b_r1_ram_safety_r3_readme_and_handover_vi,
    run_kh01b_r1_r3_ram_fault_suite,
    validate_r3_latest_run_pointer,
)
from tools.fresh_junit_io import (
    write_json_durable_atomic,
    write_jsonl_gz_durable_atomic,
    write_text_durable_atomic,
)
from tools.kh_01b_r1_collect_market_data import (
    DEFAULT_DEV_EXTENDED_START_MS,
    build_and_validate_future_oos_escrow_registry,
)
from tools.run_kh_01a_verification import ResourceMonitorKH01A, file_sha256
from tools.run_kh_01b_r1_verification import (
    compute_kh01b_r1_disk_reserve_policy,
    run_async_lpt_pytest_shards_kh01b_r1,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFIER_DSC = REPO_ROOT / "aurum-desk/tools/kh_01b_r1_data_safety_independent_verifier.py"


def _pass_v() -> dict:
    return {"status": "PASS", "violations": []}


def test_r3_512mib_boundary_low_ram_injections_and_irreversible_latch(tmp_path: Path) -> None:
    exact_512 = HARD_ABORT_RAM_FLOOR_BYTES_R3
    assert exact_512 == 512 * 1024**2 == 536_870_912

    # 1. Exact 512.00 MiB passes `<` boundary (with near-floor warning flagged)
    mon_512 = ResourceMonitorKH01A(
        abort_path=tmp_path / "abort_512.json",
        minimum_ram_bytes=exact_512,
        available_ram_fn=lambda: exact_512,
        free_disk_fn=lambda: 35 * 1024**3,
    )
    row = mon_512.check()
    assert row["available_ram_bytes"] == exact_512
    assert mon_512.hard_ram_breached is False
    assert mon_512.near_hard_floor_warning is True
    assert not (tmp_path / "abort_512.json").exists()

    # 2. 511.99 MiB, 450 MiB, 300 MiB, and 69 MiB (R2 ~68.9 MiB) all abort fail-closed
    for bad_bytes in (int(511.99 * 1024**2), 450 * 1024**2, 300 * 1024**2, 69 * 1024**2):
        ab_p = tmp_path / f"abort_{bad_bytes}.json"
        m_low = ResourceMonitorKH01A(
            abort_path=ab_p,
            minimum_ram_bytes=exact_512,
            available_ram_fn=lambda b=bad_bytes: b,
            free_disk_fn=lambda: 35 * 1024**3,
            active_context_fn=lambda: {"active_shards": ["SHARD_INPROC_13_BALANCED"]},
        )
        with pytest.raises(RuntimeError, match="BLOCKED_LOW_RAM"):
            m_low.check()
        assert m_low.hard_ram_breached is True
        assert ab_p.is_file()
        ab_doc = json.loads(ab_p.read_text("utf-8"))
        assert ab_doc["active_shard_ids"] == ["SHARD_INPROC_13_BALANCED"]

    # 3. Post-breach RAM recovery to 8 GiB NEVER clears latched BLOCKED_LOW_RAM error
    seq = iter([300 * 1024**2, 8 * 1024**3, 8 * 1024**3])
    m_latch = ResourceMonitorKH01A(
        abort_path=tmp_path / "abort_latch.json",
        minimum_ram_bytes=exact_512,
        available_ram_fn=lambda: next(seq, 8 * 1024**3),
        free_disk_fn=lambda: 35 * 1024**3,
    )
    with pytest.raises(RuntimeError, match="BLOCKED_LOW_RAM"):
        m_latch.check()
    with pytest.raises(RuntimeError, match="BLOCKED_LOW_RAM"):
        m_latch.check()
    rep = m_latch.stop()
    assert rep["hard_ram_breached"] is True
    assert "BLOCKED_LOW_RAM" in str(rep["latched_error"])


def test_r3_admission_tiers_hysteresis_singleton_bypass_and_static_64mib_guard() -> None:
    # Static check: ZERO active 64MiB RAM floor overrides in repository runners/services
    st_clean = detect_active_64mib_ram_floor_in_source(REPO_ROOT)
    assert st_clean["status"] == "PASS", f"Unexpected 64MiB floor override: {st_clean}"
    st_dirty = detect_active_64mib_ram_floor_in_source(
        REPO_ROOT,
        extra_text_by_name={"fake_runner.py": 'os.environ["AURUM_MINIMUM_RAM_BYTES"] = str(64 * 1024**2)'},
    )
    assert st_dirty["status"] == "FAIL"

    # Prior R2 artifacts & accepted latest_run.json remain byte-identical
    assert file_sha256(REPO_ROOT / PRIOR_R2_LATEST_PTR_REL) == PRIOR_R2_LATEST_PTR_SHA256
    assert file_sha256(REPO_ROOT / PRIOR_R2_RUN_REL_DIR / "15_independent_verifier_manifest_and_content_sha256.json") == PRIOR_R2_MANIFEST_15_SHA256
    assert CostAssumptions().taker_fee_rate == 0.0004

    # Tiered slot limits & hysteresis across 1.5 / 2.4 / 3.3 / 5.6 GiB
    assert compute_adaptive_slot_limit_r3(int(1.50 * 1024**3), prev_slot_limit=2)[0] == 0
    assert compute_adaptive_slot_limit_r3(int(1.75 * 1024**3), prev_slot_limit=0)[0] == 0  # Hysteresis holds pause until 1856 MiB
    assert compute_adaptive_slot_limit_r3(int(2.40 * 1024**3), prev_slot_limit=0)[0] == 1
    assert compute_adaptive_slot_limit_r3(int(2.70 * 1024**3), prev_slot_limit=1)[0] == 1  # Hysteresis holds 1 slot until 2.85 GiB
    assert compute_adaptive_slot_limit_r3(int(3.30 * 1024**3), prev_slot_limit=1)[0] == 2
    assert compute_adaptive_slot_limit_r3(int(5.60 * 1024**3), prev_slot_limit=2)[0] == 3  # Reserves 4th descendant slot for nested child

    # active_slots == 0 heavy shard (`slot_cost=2`) MUST NOT bypass when RAM is 1.5 GiB!
    bypass_blocked = evaluate_r3_shard_admission(
        shard_name="SHARD_SINGLE_CHILD_02_BUDGET2",
        exclusive=False,
        slot_cost=2,
        estimated_shard_bytes=820 * 1024**2,
        available_ram_bytes=int(1.50 * 1024**3),
        free_disk_bytes=35 * 1024**3,
        effective_disk_reserve_bytes=20 * 1024**3,
        active_slots=0,
        active_shards_count=0,
        exclusive_running=False,
        prev_slot_limit=1,
    )
    assert bypass_blocked["decision"] == "WAIT_BACKPRESSURE"
    assert bypass_blocked["shortfall_bytes"] > 0

    # At 2.4 GiB with 0 siblings, `slot_cost=2` fits singleton budget -> ADMIT_VALIDATED_SINGLETON
    singleton_ok = evaluate_r3_shard_admission(
        shard_name="SHARD_SINGLE_CHILD_02_BUDGET2",
        exclusive=False,
        slot_cost=2,
        estimated_shard_bytes=820 * 1024**2,
        available_ram_bytes=int(2.40 * 1024**3),
        free_disk_bytes=35 * 1024**3,
        effective_disk_reserve_bytes=20 * 1024**3,
        active_slots=0,
        active_shards_count=0,
        exclusive_running=False,
        prev_slot_limit=1,
    )
    assert singleton_ok["decision"] == "ADMIT_VALIDATED_SINGLETON"
    assert singleton_ok["acquired_cost"] == 2


def test_r3_async_scheduler_pause_timeout_oom_exit_missing_junit_and_cancellation(tmp_path: Path) -> None:
    run_d = tmp_path / "sched_run"
    tmp_r = tmp_path / "sched_tmp"
    run_d.mkdir(parents=True, exist_ok=True)
    tmp_r.mkdir(parents=True, exist_ok=True)
    sha = "1" * 40

    specs = [
        {"shard_name": "SHARD_MOCK_HEAVY", "files": ["backend/tests/test_mock.py"], "exclusive": False, "slot_cost": 2, "priority_weight": 100, "needs_venv": False}
    ]
    col_map = {"backend/tests/test_mock.py": ["backend/tests/test_mock.py::test_one"]}

    # 1. Admission pause timeout when active_slots==0 and RAM stays at 1.4 GiB -> BLOCKED_LOW_RAM_ADMISSION (no hang!)
    with pytest.raises(RuntimeError, match="BLOCKED_LOW_RAM_ADMISSION"):
        asyncio.run(
            run_async_lpt_pytest_shards_kh01b_r1(
                run_d,
                tmp_r,
                run_id="R3_TIMEOUT_TEST",
                tested_executable_sha=sha,
                effective_disk_reserve_bytes=1024**3,
                max_parallel_workers=4,
                ram_policy_mode="R3_STRICT_512MIB",
                available_ram_fn=lambda: int(1.40 * 1024**3),
                free_disk_fn=lambda: 35 * 1024**3,
                admission_pause_timeout_seconds=0.25,
                shard_specs_override=specs,
                collected_by_file_override=col_map,
            )
        )

    # 2. Simulated OS OOM exit 137 subprocess without XML -> fails closed, never ghost PASS
    def _oom_cmd(_sp: dict, _xml: Path, log_p: Path) -> tuple[list[str], list[str]]:
        code = f"open({str(log_p)!r}, 'w').write('Simulated OOM kill\\n'); import os; os._exit(137)"
        return [sys.executable, "-c", code], [sys.executable, "-c", "oom_sim"]

    run_oom = tmp_path / "oom_run"
    run_oom.mkdir(parents=True, exist_ok=True)
    with pytest.raises(RuntimeError, match="JUNIT_BIJECTION_FAILED"):
        asyncio.run(
            run_async_lpt_pytest_shards_kh01b_r1(
                run_oom,
                tmp_r,
                run_id="R3_OOM_TEST",
                tested_executable_sha=sha,
                effective_disk_reserve_bytes=1024**3,
                max_parallel_workers=2,
                ram_policy_mode="R3_STRICT_512MIB",
                available_ram_fn=lambda: int(6.0 * 1024**3),
                free_disk_fn=lambda: 35 * 1024**3,
                shard_specs_override=specs,
                collected_by_file_override=col_map,
                shard_command_override=_oom_cmd,
            )
        )

    # 3. Clean mock subprocess writing valid closed JUnit XML & skipping 1 test enumerates skip reason & records RSS > 0
    col_map_2 = {
        "backend/tests/test_mock.py": [
            "backend/tests/test_mock.py::test_one",
            "backend/tests/test_mock.py::test_two_skip",
        ]
    }

    def _clean_cmd(_sp: dict, xml_p: Path, log_p: Path) -> tuple[list[str], list[str]]:
        xml_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuite name="pytest" tests="2" errors="0" failures="0" skipped="1">'
            '<testcase classname="backend.tests.test_mock" name="test_one" time="0.02"/>'
            '<testcase classname="backend.tests.test_mock" name="test_two_skip" time="0.01">'
            '<skipped message="optional platform fixture unavailable"/>'
            "</testcase></testsuite>"
        )
        code = (
            f"import time; open({str(log_p)!r}, 'w').write('1 passed, 1 skipped\\n'); "
            f"time.sleep(0.08); open({str(xml_p)!r}, 'w', encoding='utf-8').write({xml_body!r})"
        )
        return [sys.executable, "-c", code], [sys.executable, "-m", "pytest"]

    run_ok = tmp_path / "ok_run"
    run_ok.mkdir(parents=True, exist_ok=True)
    res_ok = asyncio.run(
        run_async_lpt_pytest_shards_kh01b_r1(
            run_ok,
            tmp_r,
            run_id="R3_OK_TEST",
            tested_executable_sha=sha,
            effective_disk_reserve_bytes=1024**3,
            max_parallel_workers=4,
            ram_policy_mode="R3_STRICT_512MIB",
            available_ram_fn=lambda: int(2.40 * 1024**3),
            free_disk_fn=lambda: 35 * 1024**3,
            shard_specs_override=specs,
            collected_by_file_override=col_map_2,
            shard_command_override=_clean_cmd,
        )
    )
    assert res_ok["independent_bijection_verification"]["status"] == "PASS"
    assert res_ok["admission_telemetry"]["singleton_validated_admissions_count"] == 1
    assert res_ok["skip_reasons_summary"] == {"optional platform fixture unavailable": 1}
    assert res_ok["shard_manifest"]["shards"][0]["peak_rss_bytes"] > 0
    assert res_ok["shard_manifest"]["shards"][0]["rss_sample_count"] >= 1


def test_r3_full_43_fault_suite_and_dsc_extension() -> None:
    # Default R2 call keeps exact 31/31; include_r3_controls=True runs all 43/43
    s_r2 = run_kh01b_r1_dsc_red_green_fault_suite(REPO_ROOT)
    assert s_r2["status"] == "PASS" and s_r2["total_mutations"] == 31
    s_r3 = run_kh01b_r1_dsc_red_green_fault_suite(REPO_ROOT, include_r3_controls=True)
    assert s_r3["status"] == "PASS"
    assert s_r3["total_mutations"] == 43
    assert s_r3["failed_mutations"] == 0


def _build_valid_r3_bundle(base_dir: Path, *, blocked_low_ram: bool = False) -> Path:
    sha = "9988776655443322110099887766554433221100"
    run_id = f"{'20261120T100000Z' if blocked_low_ram else '20261120T110000Z'}_{sha[:12]}"
    r = base_dir / ("r3_blocked_ram" if blocked_low_ram else "r3_pass")
    (r / "receipts").mkdir(parents=True, exist_ok=True)

    fb = int(45.0 * 1024**3)
    d_pol = compute_kh01b_r1_disk_reserve_policy(fb, volume_free_bytes_by_label={"repo": fb, "output": fb, "temp": fb})
    avail_ram = int(1.30 * 1024**3) if blocked_low_ram else int(6.50 * 1024**3)
    f02 = compute_r3_ram_preflight_and_policy(avail_ram, 32 * 1024**3, max_workers=4)
    f02["configured_env_aurum_minimum_ram_bytes"] = HARD_ABORT_RAM_FLOOR_BYTES_R3
    f11 = run_kh01b_r1_r3_ram_fault_suite(REPO_ROOT)
    ram_faults = [m for m in f11["mutations"] if str(m["mutation_id"]).startswith("R3-M")]

    f00 = {
        "spec_version": SPEC_VERSION_KH01B_R1_DSC_R3,
        "schema_version": SCHEMA_VERSION_KH01B_R1_DSC_R3,
        "ram_policy_version": RAM_POLICY_VERSION_R3,
        "run_id": run_id,
        "branch": MANDATORY_BRANCH_KH01B_R1,
        "head_at_start_sha": PRIOR_R2_PUBLICATION_HEAD_SHA,
        "tested_executable_sha": sha,
        "orchestrator_pid": -999,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH01B_R1,
        "disk_reserve_policy": d_pol,
        "source_tree_unchanged_during_run": True,
        "prior_artifacts_unchanged_during_run": True,
    }
    f01 = {
        "locked_files_modified_count": 0,
        "static_no_active_64mib_floor_check": {"status": "PASS", "violations_count": 0},
        "r3_modified_and_added_functions": [],
        "keep_as_is_functions": [],
    }
    f04 = {
        "status": "PASS",
        "passed_ram_controls_count": len(ram_faults),
        "failed_ram_controls_count": 0,
        "controls": ram_faults,
    }

    samples_rows = [
        {
            "epoch_ns": 1000,
            "free_disk_bytes": fb,
            "available_ram_bytes": 300 * 1024**2 if blocked_low_ram else int(5.20 * 1024**3),
            "rss_bytes": 150 * 1024**2,
            "child_rss_bytes": 0 if blocked_low_ram else 480 * 1024**2,
            "worker_pids": [] if blocked_low_ram else [2222],
        }
    ]
    write_jsonl_gz_durable_atomic(r / "08_runtime_memory_samples_and_peak_rss.jsonl.gz", samples_rows)

    if blocked_low_ram:
        f03 = {
            "status": "NOT_RUN_PREFLIGHT_BLOCKED",
            "scheduler_terminal_status": "BLOCKED_LOW_RAM_PREFLIGHT",
            "scheduler_abort_reason": f02["abort_reason"],
            "active_slots_zero_unvalidated_bypass_allowed": False,
            "slot_downshift_events_count": 0,
            "backpressure_pause_events_count": 0,
            "singleton_validated_admissions_count": 0,
        }
        f05 = {"execution_status": "NOT_RUN_BLOCKED_LOW_RAM", "completed": False}
        f06 = {"execution_status": "NOT_RUN_BLOCKED_LOW_RAM", "completed": False}
        f07 = {"execution_status": "NOT_RUN_BLOCKED_LOW_RAM", "completed": False}
    else:
        (r / "junit").mkdir(parents=True, exist_ok=True)
        nodes = [
            f"backend/tests/test_kh_01b_r1_ram_safety_r3.py::test_case_{i:04d}"
            for i in range(5510)
        ] + [
            "backend/tests/test_kh_01b_entry_unicorn_research.py::test_b",
            "backend/tests/test_kh_01a_data_and_verification_foundation.py::test_c",
            "backend/tests/test_v17_9_3_1_2_7_causal_truth.py::test_d",
        ]
        tcs_xml = "".join(
            f'<testcase classname="{n.split("::")[0].replace("/", ".").removesuffix(".py")}" name="{n.split("::")[1]}" time="0.001"/>'
            for n in nodes
        )
        xml_p = r / "junit/SHARD_01.xml"
        xml_p.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuite name="pytest" tests="{len(nodes)}" errors="0" failures="0" skipped="0">{tcs_xml}</testsuite>', encoding="utf-8")
        xml_sha = hashlib.sha256(xml_p.read_bytes()).hexdigest()
        xml_mt = int(xml_p.stat().st_mtime_ns)

        f03 = {
            "status": "PASS",
            "scheduler_terminal_status": "COMPLETED_CLEANLY",
            "active_slots_zero_unvalidated_bypass_allowed": False,
            "slot_downshift_events_count": 0,
            "backpressure_pause_events_count": 0,
            "singleton_validated_admissions_count": 2,
        }
        f05 = {
            "execution_status": "EXECUTED",
            "completed": True,
            "skip_reasons_summary": {},
            "collection_manifest": {"run_id": run_id, "tested_executable_sha": sha, "collect_exit_code": 0, "collected_node_ids": nodes},
            "shard_manifest": {
                "run_id": run_id, "tested_executable_sha": sha, "run_start_epoch_ns": xml_mt - 1000, "nonzero_exit_shards_count": 0,
                "shards": [{
                    "shard_id": "SHARD_01", "shard_name": "SHARD_01", "run_id": run_id, "tested_executable_sha": sha,
                    "pid": 2222, "reused": False, "reused_from_prior_run": False, "exit_code": 0, "shard_status": "PASS",
                    "oom_or_killed_detected": False, "timed_out": False, "peak_rss_bytes": 480 * 1024**2, "rss_sample_count": 5,
                    "scheduled_node_ids": nodes, "junit_xml_path": "junit/SHARD_01.xml", "xml_sha256": xml_sha,
                    "xml_mtime_ns": xml_mt, "junit_sha256": xml_sha, "junit_mtime_ns": xml_mt,
                }],
            },
            "independent_bijection_verification": {
                "status": "PASS",
                "counters": {"collected": len(nodes), "executed": len(nodes), "passed": len(nodes), "skipped": 0, "failed": 0, "errors": 0, "deselected": 0, "mandatory_skipped": 0},
            },
        }
        w_py = [{"mode_label": m, "executed_tests_count": 155, "canonical_sorted_outcome_sha256": "a" * 64} for m in ("w=1", "w=2", "w=4", "w=1_repeat")]
        w_sl = [{"mode_label": m, "evaluated_pairs_count": 2800, "canonical_sorted_sha256": "b" * 64} for m in ("w=1", "w=2", "w=4", "w=1_repeat")]
        f06 = {
            "execution_status": "EXECUTED",
            "completed": True,
            "measured_speedup_w4_vs_w1": 1.4,
            "pytest_controlled_subset_w1_w2_w4_repeat": {"w4_status": "EXECUTED", "runs": w_py},
            "r1_variant_slice_w1_w2_w4_repeat": {"w4_status": "EXECUTED", "runs": w_sl, "measured_optimal_worker_count_for_slice": 1},
        }
        f07 = {
            "execution_status": "EXECUTED",
            "completed": True,
            "status": "PASS",
            "a1_evaluated_count": 56710,
            "a1_mismatches_count": 0,
            "si8_separate_population_count": 6375,
            "three_adapter_episodes_denominator": 246,
            "three_adapter_mismatches_count": 0,
            "si0_e00_primary_nb_summary": {
                "total_scheduled_episodes": 242, "closed_trades_count": 166, "wins_count": 9, "losses_count": 157,
                "total_realized_net_usd": -345.8971, "expectancy_net_r": -0.8474,
            },
            "per_decision_and_episode_fingerprints": {
                "a1_56710_ids_sha256": SEALED_R2_A1_56710_IDS_SHA256,
                "si8_6375_ids_sha256": SEALED_R2_SI8_6375_IDS_SHA256,
                "si0_242_episode_matched_tuples_sha256": SEALED_R2_SI0_242_TUPLES_SHA256,
                "e00_bundle_sha256": SEALED_R2_E00_BUNDLE_SHA256,
            },
            "fee_sensitivity_summary": {"fixed_fill_0p0006_net_usd": -447.4532},
        }

    f09 = {
        "stage_memory_and_backpressure_ledger": [{"stage": "preflight", "available_ram_mib": round(avail_ram / (1024**2), 2)}],
        "runtime_telemetry": {
            "max_children_observed": 1 if blocked_low_ram else 3,
            "orphan_pids_at_cleanup_count": 0,
            "hard_ram_breached": blocked_low_ram,
            "latched_error": "BLOCKED_LOW_RAM" if blocked_low_ram else None,
            "min_free_disk_gib": 45.0,
            "post_cleanup_free_gib": 45.0,
            "peak_child_tree_rss_mib": 0.0 if blocked_low_ram else 480.0,
            "temp_dirs_cleaned_with_sentinel_check": True,
            "unowned_paths_deleted_count": 0,
        },
    }
    f10 = {
        "status": "PASS",
        "source_tree_unchanged_during_run": True,
        "prior_artifacts_unchanged_during_run": True,
        "prior_r2_accepted_bundle_intact": True,
        "prior_r2_latest_pointer_intact": True,
        "modified_prior_files_count": 0,
    }
    esc = build_and_validate_future_oos_escrow_registry(freeze_timestamp_ms=DEFAULT_DEV_EXTENDED_START_MS, frozen_candidate_code_sha=sha, frozen_config_sha256="cfg")
    f12 = {
        "DATA_ACQUISITION_VERDICT": "REUSED_VERIFIED_R2_EXTENDED_DEV_SNAPSHOT",
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE_BASELINE_UNCHANGED_FEE_SENSITIVITY_NEGATIVE",
        "KH01C_ADVANCEMENT_VERDICT": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE",
        "future_oos_escrow": esc,
        "ACTIVATION_READY": "NO",
        "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
    }

    for fname, doc in (
        ("00_preflight_branch_sha_mount_ram_disk_immutable.json", f00),
        ("01_function_callgraph_keep_as_is_and_source_diff.json", f01),
        ("02_ram_policy_version_thresholds_preflight_and_projections.json", f02),
        ("03_admission_hysteresis_exclusive_nested_child_reservations.json", f03),
        ("04_ram_pressure_red_green_512mib_boundary_faults.json", f04),
        ("05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json", f05),
        ("06_async_w1_w2_w4_repeat_timings_hashes_and_capacity.json", f06),
        ("07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json", f07),
        ("09_disk_all_mounts_and_safe_temp_cleanup.json", f09),
        ("10_integrity_previous_artifacts_and_source_sha_diffs.json", f10),
        ("11_independent_verifier_red_controls_and_receipts.json", f11),
        ("12_oos_zero_evaluations_and_activation_off.json", f12),
    ):
        write_json_durable_atomic(r / fname, doc)

    target_map = {
        "S01": "10_integrity_previous_artifacts_and_source_sha_diffs.json",
        "S02": "02_ram_policy_version_thresholds_preflight_and_projections.json",
        "S03": "03_admission_hysteresis_exclusive_nested_child_reservations.json",
        "S04": "04_ram_pressure_red_green_512mib_boundary_faults.json",
        "S05": "08_runtime_memory_samples_and_peak_rss.jsonl.gz",
        "S06": "09_disk_all_mounts_and_safe_temp_cleanup.json",
        "S07": "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json",
        "S08": "06_async_w1_w2_w4_repeat_timings_hashes_and_capacity.json",
        "S09": "07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json",
        "S10": "11_independent_verifier_red_controls_and_receipts.json",
        "S11": "11_independent_verifier_red_controls_and_receipts.json",
        "S12": "12_oos_zero_evaluations_and_activation_off.json",
        "S14": "12_oos_zero_evaluations_and_activation_off.json",
    }
    skip_s = {"S02", "S07", "S08", "S09"} if blocked_low_ram else set()
    for gid, rel_p in target_map.items():
        if gid not in skip_s:
            build_dynamic_validated_gate_receipt_r1(
                gate_id=gid, run_dir=r, run_id=run_id, tested_sha=sha,
                target_rel_path=rel_p, validator_fn=_pass_v, write_json_fn=write_json_durable_atomic,
            )

    f13 = evaluate_kh01b_r1_ram_safety_r3_gates_from_disk(r, run_id, sha)
    write_json_durable_atomic(r / "13_final_orthogonal_ram_engineering_economic_verdicts.json", f13)
    rm, ho = render_kh01b_r1_ram_safety_r3_readme_and_handover_vi(
        run_id=run_id, branch=MANDATORY_BRANCH_KH01B_R1, head_at_start_sha=PRIOR_R2_PUBLICATION_HEAD_SHA,
        tested_sha=sha, dataset_sha=CANONICAL_DATASET_SHA256_KH01B_R1,
        f00=f00, f01=f01, f02=f02, f03=f03, f05=f05, f06=f06, f07=f07, f09=f09, f11=f11, f12=f12, f13=f13,
    )
    write_text_durable_atomic(r / "README_KH01B_R1_RAM_SAFETY_R3.md", rm)
    write_text_durable_atomic(r / "HANDOVER_KH01B_R1_RAM_SAFETY_R3_VI.txt", ho)
    return r


def test_r3_subproc_independent_verifier_green_and_12_adversarial_tampers(tmp_path: Path) -> None:
    r3_blocked = _build_valid_r3_bundle(tmp_path, blocked_low_ram=True)
    r3_pass = _build_valid_r3_bundle(tmp_path, blocked_low_ram=False)

    def _run_v(d: Path) -> tuple[int, dict]:
        p = subprocess.run(
            [sys.executable, str(VERIFIER_DSC), "--r3", "--run-dir", str(d), "--skip-git-freeze-check"],
            capture_output=True, text=True,
        )
        return p.returncode, json.loads(p.stdout)

    # GREEN 1: Honest BLOCKED_LOW_RAM bundle passes integrity verification with recomputed BLOCKED_LOW_RAM
    rc_b, out_b = _run_v(r3_blocked)
    assert rc_b == 0 and out_b["independent_verifier_status"] == "PASS", f"R3 Blocked GREEN failed: {out_b}"
    assert out_b["recomputed_engineering_closure_verdict"] == "BLOCKED_LOW_RAM"
    assert out_b["recomputed_ram_safety_verdict"] == "BLOCKED_LOW_RAM"

    # GREEN 2: Complete >=512 MiB RAM-safe R3 bundle passes verification with recomputed PASS
    rc_p, out_p = _run_v(r3_pass)
    assert rc_p == 0 and out_p["independent_verifier_status"] == "PASS", f"R3 Pass GREEN failed: {out_p}"
    assert out_p["recomputed_engineering_closure_verdict"] == "PASS"
    assert out_p["recomputed_ram_safety_verdict"] == "PASS_STRICT_512MIB_FLOOR"
    assert (r3_pass / "14_sha256_manifest_independent_os_readback.json").is_file()

    # Tamper 1: Forging PASS in 13 while 08_runtime_memory_samples_and_peak_rss.jsonl.gz has a 300 MiB sample -> FAIL
    t1 = tmp_path / "t1"
    shutil.copytree(r3_pass, t1)
    with gzip.open(t1 / "08_runtime_memory_samples_and_peak_rss.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"epoch_ns": 1, "free_disk_bytes": 45 * 1024**3, "available_ram_bytes": 300 * 1024**2, "rss_bytes": 150 * 1024**2, "child_rss_bytes": 400 * 1024**2, "worker_pids": [1]}) + "\n")
    rc, out = _run_v(t1)
    assert rc != 0 and any("FORGED_PASS_UNDER_LOW_RAM" in v or "FORGED_VERDICT" in v for v in out["violations"])

    # Tamper 2: Lowering configured hard_abort_floor_bytes to 64 MiB in 02 -> FAIL
    t2 = tmp_path / "t2"
    shutil.copytree(r3_pass, t2)
    d02 = json.loads((t2 / "02_ram_policy_version_thresholds_preflight_and_projections.json").read_text("utf-8"))
    d02["hard_abort_floor_bytes"] = 64 * 1024**2
    write_json_durable_atomic(t2 / "02_ram_policy_version_thresholds_preflight_and_projections.json", d02)
    rc, out = _run_v(t2)
    assert rc != 0 and any("RAM_HARD_FLOOR_BELOW_512MIB" in v for v in out["violations"])

    # Tamper 3: Forging 0 errors in 13 when shard in 05 exited with OOM code 137 -> FAIL
    t3 = tmp_path / "t3"
    shutil.copytree(r3_pass, t3)
    d05 = json.loads((t3 / "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json").read_text("utf-8"))
    d05["shard_manifest"]["shards"][0]["exit_code"] = 137
    d05["shard_manifest"]["shards"][0]["oom_or_killed_detected"] = True
    write_json_durable_atomic(t3 / "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json", d05)
    rc, out = _run_v(t3)
    assert rc != 0 and any("SHARD_OOM_OR_NONZERO_EXIT" in v for v in out["violations"])

    # Tamper 4: Presence of resource_abort.json while 13 claims ENGINEERING_CLOSURE_VERDICT="PASS" -> FAIL
    t4 = tmp_path / "t4"
    shutil.copytree(r3_pass, t4)
    write_json_durable_atomic(t4 / "resource_abort.json", {"status": "BLOCKED", "reason": "BLOCKED_LOW_RAM"})
    rc, out = _run_v(t4)
    assert rc != 0 and any("FORGED_PASS_UNDER_LOW_RAM" in v for v in out["violations"])

    # Tamper 5: Descendant count > 4 in 08 memory samples -> FAIL
    t5 = tmp_path / "t5"
    shutil.copytree(r3_pass, t5)
    with gzip.open(t5 / "08_runtime_memory_samples_and_peak_rss.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"epoch_ns": 1, "free_disk_bytes": 45 * 1024**3, "available_ram_bytes": 6 * 1024**3, "rss_bytes": 150 * 1024**2, "child_rss_bytes": 900 * 1024**2, "worker_pids": [1, 2, 3, 4, 5]}) + "\n")
    rc, out = _run_v(t5)
    assert rc != 0 and any("GLOBAL_DESCENDANT_CAP_EXCEEDED" in v for v in out["violations"])

    # Tamper 6: Zero shard peak_rss_bytes (unmeasured child memory) in 05 -> FAIL
    t6 = tmp_path / "t6"
    shutil.copytree(r3_pass, t6)
    d05_6 = json.loads((t6 / "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json").read_text("utf-8"))
    d05_6["shard_manifest"]["shards"][0]["peak_rss_bytes"] = 0
    write_json_durable_atomic(t6 / "05_full_pytest_collected_scheduled_executed_junit_exit_bijection.json", d05_6)
    rc, out = _run_v(t6)
    assert rc != 0 and any("SHARD_ZERO_RSS_SAMPLE" in v for v in out["violations"])

    # Tamper 7: w=4 outcome hash mismatch in 06 -> FAIL
    t7 = tmp_path / "t7"
    shutil.copytree(r3_pass, t7)
    d06 = json.loads((t7 / "06_async_w1_w2_w4_repeat_timings_hashes_and_capacity.json").read_text("utf-8"))
    d06["pytest_controlled_subset_w1_w2_w4_repeat"]["runs"][2]["canonical_sorted_outcome_sha256"] = "f" * 64
    write_json_durable_atomic(t7 / "06_async_w1_w2_w4_repeat_timings_hashes_and_capacity.json", d06)
    rc, out = _run_v(t7)
    assert rc != 0 and any("WORKER_06" in v for v in out["violations"])

    # Tamper 8: Tampered backtest fingerprint vs sealed R2 in 07 -> FAIL
    t8 = tmp_path / "t8"
    shutil.copytree(r3_pass, t8)
    d07 = json.loads((t8 / "07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json").read_text("utf-8"))
    d07["per_decision_and_episode_fingerprints"]["e00_bundle_sha256"] = "0" * 64
    write_json_durable_atomic(t8 / "07_full_a1_si8_si0_three_fill_adapter_backtest_parity.json", d07)
    rc, out = _run_v(t8)
    assert rc != 0 and any("BACKTEST_07_PARITY" in v for v in out["violations"])

    # Tamper 9: Reusing stale R2 tested_executable_sha (`c20b0b1b...`) in 00 -> FAIL
    t9 = tmp_path / "t9"
    shutil.copytree(r3_pass, t9)
    d00 = json.loads((t9 / "00_preflight_branch_sha_mount_ram_disk_immutable.json").read_text("utf-8"))
    d00["tested_executable_sha"] = PRIOR_R2_TESTED_SHA
    write_json_durable_atomic(t9 / "00_preflight_branch_sha_mount_ram_disk_immutable.json", d00)
    rc, out = _run_v(t9)
    assert rc != 0 and any("SHA_MISMATCH" in v or "STALE_TESTED_SHA" in v for v in out["violations"])

    # Tamper 10: Promoting BLOCKED_LOW_RAM run into parent latest_run.json -> FAIL
    t10_ns = tmp_path / "t10_ns"
    t10 = t10_ns / r3_blocked.name
    shutil.copytree(r3_blocked, t10)
    d00_b = json.loads((t10 / "00_preflight_branch_sha_mount_ram_disk_immutable.json").read_text("utf-8"))
    write_json_durable_atomic(t10_ns / "latest_run.json", {"run_id": d00_b["run_id"], "ENGINEERING_CLOSURE_VERDICT": "BLOCKED_LOW_RAM"})
    rc, out = _run_v(t10)
    assert rc != 0 and any("BLOCKED_OR_INVALID_R3_RUN_PROMOTED" in v for v in out["violations"])

    # Tamper 11: Missing dynamic gate receipt S02.json -> FAIL
    t11 = tmp_path / "t11"
    shutil.copytree(r3_pass, t11)
    (t11 / "receipts/S02.json").unlink()
    rc, out = _run_v(t11)
    assert rc != 0 and any("INDEPENDENT_R3_GATE_REEVALUATION_FAILED" in v for v in out["violations"])

    # Tamper 12: Activating LIVE="ON" or promoting KH01C in 13 -> FAIL
    t12 = tmp_path / "t12"
    shutil.copytree(r3_pass, t12)
    d13 = json.loads((t12 / "13_final_orthogonal_ram_engineering_economic_verdicts.json").read_text("utf-8"))
    d13["orthogonal_verdicts"]["RUNTIME_SAFETY_MODES"]["LIVE"] = "ON"
    d13["orthogonal_verdicts"]["KH01C_ADVANCEMENT_VERDICT"] = "PROMOTED"
    write_json_durable_atomic(t12 / "13_final_orthogonal_ram_engineering_economic_verdicts.json", d13)
    rc, out = _run_v(t12)
    assert rc != 0 and any("UNSAFE_RUNTIME_MODES" in v or "ILLEGAL_KH01C" in v for v in out["violations"])
