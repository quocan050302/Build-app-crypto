"""
backend/tests/test_v17_9_3_1_2_2_verification_integrity.py

Adversarial & Functional Unit Tests T53..T64 for V17.9.3.1.2.2
Verification Truth Closures: Full Pytest Discovery, JUnit Bijection,
Executable RED/GREEN Mutations, 1/2/4-Worker Determinism, Disk/RAM Guards & Git Provenance.
"""
from __future__ import annotations

from pathlib import Path
import sys
import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
for p in (str(BACKEND_ROOT), str(APP_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.scalp_m1_types import M1ScalpConfig
from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    cleanup_orphan_subprocesses_and_temp_v1793121,
    preflight_runtime_resource_limits_v1793121,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_DATASET_SHA256_V1793122,
    evaluate_all_52_acceptance_gates_v1793122,
    is_research_apply_forbidden_in_adapter_v1793122,
)
from tools.run_v17_9_3_1_2_2_verification import (
    canonicalize_collect_node_id_v1793122,
    canonicalize_junit_testcase_node_id_v1793122,
    verify_collect_vs_junit_node_id_bijection_v1793122,
)


def test_t53_full_pytest_discovery_includes_entire_backend_test_tree():
    """T53: Repository test discovery enumerates all test_*.py files under backend/tests (>150 test files)."""
    test_files = sorted((BACKEND_ROOT / "tests").glob("test_*.py"))
    assert len(test_files) >= 150
    names = {p.name for p in test_files}
    assert "test_v17_9_3_1_2_2_signal_intelligence.py" in names
    assert "test_v17_9_3_1_2_2_verification_integrity.py" in names


def test_t54_exact_collected_junit_node_ids_match_executed_one_to_one():
    """T54: Canonicalized collect-only node IDs match JUnit XML testcase node IDs 1-to-1 and detect any missing/extra test."""
    c_id = canonicalize_collect_node_id_v1793122(
        "backend/tests/test_v17_9_3_1_2_2_signal_intelligence.py::test_t01_signal_intelligence_mode_off_exact_decision_and_action_parity"
    )
    j_id = canonicalize_junit_testcase_node_id_v1793122(
        "backend.tests.test_v17_9_3_1_2_2_signal_intelligence",
        "test_t01_signal_intelligence_mode_off_exact_decision_and_action_parity",
    )
    assert c_id == j_id

    bij_ok = verify_collect_vs_junit_node_id_bijection_v1793122([c_id], [j_id])
    assert bij_ok["exact_node_id_bijection_verified"] is True

    bij_bad = verify_collect_vs_junit_node_id_bijection_v1793122([c_id, "backend/tests/test_foo.py::test_missing"], [j_id])
    assert bij_bad["exact_node_id_bijection_verified"] is False
    assert bij_bad["missing_from_junit_count"] == 1


def test_t55_failing_shard_or_missing_junit_makes_full_regression_gates_fail():
    """T55: Any nonzero shard exit code, failed test, or missing JUnit node ID causes G41/G42 to FAIL."""
    ev_fail = {
        "24": {"collected_node_ids_count": 4890},
        "25": {
            "tested_executable_sha": "a" * 40,
            "shard_count": 4,
            "executed_junit_node_ids_count": 4889,
            "exact_node_id_bijection_verified": False,
            "missing_from_junit_count": 1,
            "unexpected_in_junit_count": 0,
            "failed_count": 1,
            "error_count": 0,
            "nonzero_exit_shards_count": 1,
        },
    }
    res = evaluate_all_52_acceptance_gates_v1793122(
        evidence_by_key=ev_fail,
        tested_executable_sha="a" * 40,
        dataset_sha256=CANONICAL_DATASET_SHA256_V1793122,
    )
    failed_set = set(res["failed_gate_ids"])
    assert "G40" in failed_set
    assert "G41" in failed_set
    assert "G42" in failed_set


def test_t56_genuine_executable_red_green_requires_separate_source_mutations():
    """T56: G43 requires >=12 executable source mutations with red_exit_code != 0 and green_exit_code == 0, separate from G44."""
    ev = {
        "26": {"executable_mutations_count": 16, "executable_mutations_caught_count": 16},
        "27": {"separated_from_executable_red_green": True, "evidence_gate_mutations_count": 12},
    }
    res = evaluate_all_52_acceptance_gates_v1793122(
        evidence_by_key=ev,
        tested_executable_sha="a" * 40,
        dataset_sha256=CANONICAL_DATASET_SHA256_V1793122,
    )
    g43 = [g for g in res["gates"] if g["gate_id"] == "G43"][0]
    g44 = [g for g in res["gates"] if g["gate_id"] == "G44"][0]
    assert g43["passed"] is True
    assert g44["passed"] is True


def test_t57_worker_1_vs_2_vs_4_independent_3_fill_mode_hashes_identical():
    """T57: G45 passes when 1, 2, and 4 independent worker executions produce identical SHA-256 across all 3 fill modes."""
    ev = {
        "31": {
            "all_1_2_4_worker_hashes_identical": True,
            "independent_execution_no_shared_digest_reuse": True,
        }
    }
    res = evaluate_all_52_acceptance_gates_v1793122(
        evidence_by_key=ev,
        tested_executable_sha="a" * 40,
        dataset_sha256=CANONICAL_DATASET_SHA256_V1793122,
    )
    g45 = [g for g in res["gates"] if g["gate_id"] == "G45"][0]
    assert g45["passed"] is True


def test_t58_mutating_only_4_worker_portfolio_or_reusing_shared_digest_fails_gate_g45():
    """T58: Mutating the 4-worker hash or reusing a shared digest causes Gate G45 to FAIL."""
    ev_mismatch = {
        "31": {
            "all_1_2_4_worker_hashes_identical": False,
            "independent_execution_no_shared_digest_reuse": True,
        }
    }
    res1 = evaluate_all_52_acceptance_gates_v1793122(
        evidence_by_key=ev_mismatch,
        tested_executable_sha="a" * 40,
        dataset_sha256=CANONICAL_DATASET_SHA256_V1793122,
    )
    assert "G45" in res1["failed_gate_ids"]

    ev_shared = {
        "31": {
            "all_1_2_4_worker_hashes_identical": True,
            "independent_execution_no_shared_digest_reuse": False,
        }
    }
    res2 = evaluate_all_52_acceptance_gates_v1793122(
        evidence_by_key=ev_shared,
        tested_executable_sha="a" * 40,
        dataset_sha256=CANONICAL_DATASET_SHA256_V1793122,
    )
    assert "G45" in res2["failed_gate_ids"]


def test_t59_disk_low_watermark_aborts_before_filesystem_fills():
    """T59: preflight_runtime_resource_limits_v1793121 raises RuntimeError if free disk space is below hard minimum."""
    with pytest.raises(RuntimeError, match="DISK_FLOOR_BREACHED"):
        preflight_runtime_resource_limits_v1793121(
            APP_ROOT.parent, hard_min_free_disk_gib=999_999.0, max_workers_cap=4
        )


def test_t60_worker_cap_le_4_and_orphan_cleanup():
    """T60: Worker cap is bounded <= 4 and staging cleanup leaves 0 orphan subprocesses."""
    info = preflight_runtime_resource_limits_v1793121(
        APP_ROOT.parent, hard_min_free_disk_gib=1.0, max_workers_cap=4
    )
    assert 1 <= int(info["configured_max_workers"]) <= 4
    clean = cleanup_orphan_subprocesses_and_temp_v1793121([APP_ROOT / "docs" / "artifacts" / "_nonexistent_tmp"])
    assert int(clean.get("orphan_subprocesses_remaining", 0)) == 0


def test_t61_corrupt_or_missing_artifact_sha_manifest_fails_g47():
    """T61: Corrupt or missing files in artifact_sha256_manifest.json cause Gate G47 to FAIL."""
    ev_bad = {
        "manifest_check": {
            "verified_files_count": 36,
            "corrupt_or_missing_files_count": 2,
        }
    }
    res = evaluate_all_52_acceptance_gates_v1793122(
        evidence_by_key=ev_bad,
        tested_executable_sha="a" * 40,
        dataset_sha256=CANONICAL_DATASET_SHA256_V1793122,
    )
    assert "G47" in res["failed_gate_ids"]


def test_t62_different_executable_git_sha_from_tested_sha_fails_g40_and_g48():
    """T62: Executable code drift since tested SHA or mismatched suite SHA causes G40 and G48 to FAIL."""
    ev_drift = {
        "24": {"collected_node_ids_count": 4890},
        "25": {
            "tested_executable_sha": "b" * 40,
            "executed_junit_node_ids_count": 4890,
            "exact_node_id_bijection_verified": True,
            "missing_from_junit_count": 0,
            "unexpected_in_junit_count": 0,
            "failed_count": 0,
            "error_count": 0,
            "nonzero_exit_shards_count": 0,
        },
        "35": {"executable_diff_files_since_tested_sha_count": 1},
        "36": {"branch_matches_mandatory": True},
    }
    res = evaluate_all_52_acceptance_gates_v1793122(
        evidence_by_key=ev_drift,
        tested_executable_sha="a" * 40,
        dataset_sha256=CANONICAL_DATASET_SHA256_V1793122,
    )
    assert "G40" in res["failed_gate_ids"]
    assert "G48" in res["failed_gate_ids"]


def test_t63_wrong_branch_or_unverified_remote_fails_g01_and_g48():
    """T63: Switching off mandatory branch feature/v17-9-2-structural-sl-tp-from-qa fails G01 and G48."""
    ev_branch = {
        "01": {"current_branch": "master", "ancestry_verified": False},
        "35": {"executable_diff_files_since_tested_sha_count": 0},
        "36": {"branch_matches_mandatory": False},
    }
    res = evaluate_all_52_acceptance_gates_v1793122(
        evidence_by_key=ev_branch,
        tested_executable_sha="a" * 40,
        dataset_sha256=CANONICAL_DATASET_SHA256_V1793122,
    )
    assert "G01" in res["failed_gate_ids"]
    assert "G48" in res["failed_gate_ids"]


def test_t64_research_policy_cannot_enable_live_or_auto_paper_action():
    """T64: Research policies cannot be activated in PAPER, AUTO_PAPER, or LIVE, and G51 requires activation_ready == NO."""
    for exec_mode in ("PAPER", "AUTO_PAPER", "LIVE"):
        with pytest.raises(ValueError, match="SI_FORBIDDEN_IN_PRODUCTION_EXECUTION_MODE"):
            M1ScalpConfig(
                execution_mode=exec_mode,
                v17_9_3_1_2_2_signal_intelligence_mode="RESEARCH_APPLY",
                v17_9_3_1_2_2_policy_id="SI6_COMBINED_PREDECLARED",
            )

    forbid, code = is_research_apply_forbidden_in_adapter_v1793122(
        "BACKTEST", policy_dict={"auto_paper_enabled": True}
    )
    assert forbid is True
    assert code == "SI_FORBIDDEN_WHEN_LIVE_OR_AUTO_PAPER_ENABLED"
