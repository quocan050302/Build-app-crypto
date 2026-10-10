"""
V12_3 Acceptance Manifest: 74 Core Requirements (A01-E12) + 12 Defect Repair Requirements (P01-P12).
Total: 86 Requirements dynamically verified via EvidenceCollector from real pytest executions.
"""
from typing import Dict, Any, List, Optional
import os
import json

from lab.v12_2_manifest import ALL_74_REQUIREMENTS

DEFECT_REPAIR_REQUIREMENTS: List[Dict[str, Any]] = [
    {"id": "P01", "group": "CORRECTNESS_REPAIR", "objective": "Evidence collector computes test outcomes dynamically from pytest JUnit XML; no hardcoded 74/74 PASS", "mapped_test": "test_v12_3_acceptance.py::test_p01_evidence_collector_dynamic_no_hardcoded_pass"},
    {"id": "P02", "group": "CORRECTNESS_REPAIR", "objective": "Vacuous assertions replaced with genuine prefix invariance and production-replay parity checks", "mapped_test": "test_v12_3_acceptance.py::test_p02_genuine_assertions_on_prefix_and_parity"},
    {"id": "P03", "group": "CORRECTNESS_REPAIR", "objective": "Replay evaluates causal news blackout and temporal lesson rules during simulation", "mapped_test": "test_v12_3_acceptance.py::test_p03_causal_news_and_lesson_evaluation"},
    {"id": "P04", "group": "CORRECTNESS_REPAIR", "objective": "Cancellation token checkpoints interrupt ReplayEngine candle loop; bounded job queue (max 5)", "mapped_test": "test_v12_3_acceptance.py::test_p04_cancellation_checkpoint_and_bounded_queue"},
    {"id": "P05", "group": "CORRECTNESS_REPAIR", "objective": "Frontend fallback limited to 404 before job creation; FAILED/CANCELLED jobs never restart replay automatically", "mapped_test": "test_v12_3_acceptance.py::test_p05_frontend_job_flow_and_no_rerun"},
    {"id": "P06", "group": "CORRECTNESS_REPAIR", "objective": "Ledger postings and execution events emitted live during simulation, capturing all fees, slippages, and PnL", "mapped_test": "test_v12_3_acceptance.py::test_p06_live_ledger_postings_and_events"},
    {"id": "P07", "group": "CORRECTNESS_REPAIR", "objective": "Eliminate 0.02 rounding drift by accumulating unrounded cash balances; exact cash reconciliation", "mapped_test": "test_v12_3_acceptance.py::test_p07_exact_accounting_no_drift"},
    {"id": "P08", "group": "CORRECTNESS_REPAIR", "objective": "Single multiplier ownership in StressTester prevents 4x compounding on 2x multiplier", "mapped_test": "test_v12_3_acceptance.py::test_p08_stress_multiplier_single_ownership"},
    {"id": "P09", "group": "CORRECTNESS_REPAIR", "objective": "Zero-delta repricing produces exact baseline net PnL and records role provenance", "mapped_test": "test_v12_3_acceptance.py::test_p09_zero_delta_repricing_exact_baseline"},
    {"id": "P10", "group": "CORRECTNESS_REPAIR", "objective": "Latency labeled honestly as cost approximation rather than temporal execution fidelity", "mapped_test": "test_v12_3_acceptance.py::test_p10_latency_labeled_as_approximation"},
    {"id": "P11", "group": "CORRECTNESS_REPAIR", "objective": "Strict artifact containment rejects symlink escapes via Path.resolve(strict=True)", "mapped_test": "test_v12_3_acceptance.py::test_p11_artifact_containment_rejects_symlink_escape"},
    {"id": "P12", "group": "CORRECTNESS_REPAIR", "objective": "Economic conclusions reflect empirical coverage honestly (14/67 labeled COVERAGE_UNMET / PARTIAL_EXPANSION)", "mapped_test": "test_v12_3_acceptance.py::test_p12_honest_coverage_classification"}
]

ALL_86_REQUIREMENTS: List[Dict[str, Any]] = ALL_74_REQUIREMENTS + DEFECT_REPAIR_REQUIREMENTS


def build_v12_3_requirement_manifest(test_evidence_map: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """
    Builds the authoritative 86-requirement manifest for V12_3.
    """
    manifest = []
    for req in ALL_86_REQUIREMENTS:
        req_copy = dict(req)
        test_node = req_copy.get("mapped_test", "")
        test_fn = test_node.split("::")[-1] if "::" in test_node else test_node

        if test_evidence_map:
            # Check exact node, then test function name, then req ID
            status = test_evidence_map.get(test_node) or test_evidence_map.get(test_fn) or test_evidence_map.get(req_copy["id"])
            req_copy["status"] = status if status else "NOT_RUN"
        else:
            req_copy["status"] = "NOT_RUN"

        manifest.append(req_copy)
    return manifest
