"""
aurum-desk/backend/tests/test_kh03_r3_legacy_guard.py

KH-03.R3 P0-A Legacy Method Firewall Tests:
Verifies direct import, alias import, wildcard import, re-export (`__all__`),
`getattr`/`eval`/`exec` wrappers, R1 protocol certification denial vs descriptive-only mode,
forged metadata/CI/H3/OOS/LIVE provenance denial, and live AST callgraph pass across `aurum-desk/`.
"""
from __future__ import annotations

from pathlib import Path
import sys
import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_03_r3_legacy_research_guard import (
    LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY,
    SPEC_VERSION_KH03_R3,
    audit_kh03_r3_legacy_research_callgraph,
    build_r3_legacy_guard_artifacts_03_04,
    require_kh03_r2_or_newer_research_protocol,
    scan_python_source_for_forbidden_r1_usage,
    validate_r3_research_output_provenance,
)


@pytest.mark.parametrize(
    "code",
    [
        "from services.kh_03_research_service import run_kh03_full_research_study_10_to_16",
        "from services.kh_03_research_service import preregister_kh03_research_plan as my_plan\nmy_plan()",
        "from services.kh_03_research_service import *",
        "import services.kh_03_research_service as m\nm.run_kh03_full_research_study_10_to_16()",
        "__all__ = ['build_kh03_partition_manifest_and_noncontamination']",
        "import services.kh_03_research_service as m\ngetattr(m, 'run_kh03_full_research_study_10_to_16')()",
        "eval('run_kh03_full_research_study_10_to_16()')",
        "exec('preregister_kh03_research_plan()')",
    ],
)
def test_ast_scanner_catches_all_forbidden_r1_import_and_wrapper_vectors(code):
    hits = scan_python_source_for_forbidden_r1_usage(code, filename="test_bad_caller.py")
    assert len(hits) >= 1


def test_ast_scanner_allows_clean_r2_r3_module():
    code = "from services.kh_03_r2_methodology_service import parse_kh03_cluster_ci_strict\nparse_kh03_cluster_ci_strict([0.1, 0.2])"
    assert scan_python_source_for_forbidden_r1_usage(code, filename="clean_r3.py") == []


def test_live_repo_callgraph_audit_passes_with_zero_unauthorized_r1_callers():
    res = audit_kh03_r3_legacy_research_callgraph(REPO_ROOT)
    assert res["LEGACY_METHOD_GUARD"] == "PASS"
    assert res["unauthorized_r1_callers_count"] == 0
    assert res["LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY"] is True
    assert LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY is True


def test_protocol_gate_denies_r1_certification_and_forged_r3_invariants():
    good_ev = {
        "ACTIVATION_READY": "NO", "OOS_EVALUATIONS": 0, "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "h3_promoted_as_executable": False, "temporal_validation_kind": "FORWARD_ONLY_DEV_VALIDATION",
        "embargo_minutes": 240, "future_train_bars_count": 0,
        "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        "raw_ci_samples": {"E1": [-0.5, -0.2]},
    }
    assert require_kh03_r2_or_newer_research_protocol("KH-03.R3-v1.0", good_ev)["admitted_for_certification"] is True
    assert require_kh03_r2_or_newer_research_protocol("KH-03.R2-v1.0", good_ev)["admitted_for_certification"] is True
    assert require_kh03_r2_or_newer_research_protocol("KH-03.R1-v1.0", good_ev)["admitted_for_certification"] is False

    desc = require_kh03_r2_or_newer_research_protocol("KH-03.R1-v1.0", good_ev, allow_legacy_descriptive_only=True)
    assert desc["admitted_for_certification"] is False
    assert desc["admitted_for_descriptive_audit_only"] is True
    assert desc["classification"] == "LEGACY_DESCRIPTIVE_ONLY"

    for bad_patch in (
        {"h3_promoted_as_executable": True},
        {"temporal_validation_kind": "POSTHOC_FOUR_SLICES"},
        {"embargo_minutes": 239},
        {"future_train_bars_count": 10},
        {"OOS_EVALUATIONS": 1},
        {"ACTIVATION_READY": "YES"},
        {"RESEARCH_ECONOMIC_VERDICT": "PROVEN_EDGE"},
        {"raw_ci_samples": {"E1": [0.5, 0.1]}},
    ):
        ev = dict(good_ev, **bad_patch)
        assert require_kh03_r2_or_newer_research_protocol("KH-03.R3-v1.0", ev)["admitted_for_certification"] is False


def test_provenance_validator_rejects_old_ci_bug_h3_oos_and_live_flags():
    assert validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "ACTIVATION_READY": "NO", "OOS_EVALUATIONS": 0, "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE"})["valid"] is True
    assert not validate_r3_research_output_provenance({"spec_version": "KH-03.R1-v1.0"})["valid"]
    assert not validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "is_fresh_oos": True})["valid"]
    assert not validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "H3_RESIZE_EXECUTION_STATUS": "PASS"})["valid"]
    assert not validate_r3_research_output_provenance({"spec_version": SPEC_VERSION_KH03_R3, "observed_l1_bbo_available": True})["valid"]


def test_r3_legacy_guard_artifacts_03_04_pass_on_real_r1_dir():
    r1_dir = REPO_ROOT / "aurum-desk/docs/artifacts/kh_03_research_data/20261123T055123Z_f0b668dc0dd1"
    f03, f04 = build_r3_legacy_guard_artifacts_03_04(repo_root=REPO_ROOT, prior_r1_dir=r1_dir)
    assert f03["status"] == "PASS"
    assert f04["status"] == "PASS"
    assert f04["all_synthetic_bypass_vectors_detected"] is True
