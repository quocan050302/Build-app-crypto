"""
V12_4 Acceptance Manifest: 86 Prior Requirements + 12 Defect Repair Requirements (V124-01 to V124-12).
Authoritative, dynamically verified via EvidenceCollector from real pytest JUnit XML executions.
"""
from typing import Dict, Any, List, Optional
import os
import json

from lab.v12_3_manifest import ALL_86_REQUIREMENTS

DEFECT_REPAIR_REQUIREMENTS_V12_4: List[Dict[str, Any]] = [
    {
        "id": "V124-01",
        "group": "LEDGER_SYNC",
        "objective": "Canonical ledger schema synchronized between producer and exporters; no blank columns in CSV/Excel",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_01_canonical_ledger_schema_and_export"
    },
    {
        "id": "V124-02",
        "group": "CONTEXT_SAFETY",
        "objective": "ReplayContext keyword-safe parameter order, strict epoch timestamp validation, initial equity honored",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_02_replay_context_safe_params_and_initial_cash"
    },
    {
        "id": "V124-03",
        "group": "CAUSAL_RULES",
        "objective": "Real lesson rule blocking via can_proceed/lesson_blockers with real candidate metrics and simulated clock across A/B/C",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_03_rule_service_actual_blocking_and_metrics"
    },
    {
        "id": "V124-04",
        "group": "CAUSAL_NEWS",
        "objective": "Historical news & rule snapshots with causal timestamp filtering; unseeded history disclosed honestly",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_04_causal_news_blackout_and_snapshots"
    },
    {
        "id": "V124-05",
        "group": "EXACT_ACCOUNTING",
        "objective": "Single cash source of truth, exact reconciliation invariants without 0.02 rounding drift between trades and ledger",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_05_single_cash_source_and_exact_reconciliation"
    },
    {
        "id": "V124-06",
        "group": "EVIDENCE_INTEGRITY",
        "objective": "Exact pytest node mapping in EvidenceCollector without fuzzy false positives; errors and non-zero exits propagate",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_06_exact_evidence_mapping_no_fuzzy_fallbacks"
    },
    {
        "id": "V124-07",
        "group": "UI_JOB_FLOW",
        "objective": "TestingLabComponent persists active jobs past poll timeouts, unmount cleanup, reconnection without duplicate rerun",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_07_ui_job_persistence_and_reconnection_contracts"
    },
    {
        "id": "V124-08",
        "group": "JOB_QUEUE_API",
        "objective": "JobManager atomic capacity, HTTP 429 queue full mapping, explicit defaults without 0-falsy bug",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_08_queue_atomic_capacity_and_http_429"
    },
    {
        "id": "V124-09",
        "group": "STRESS_FIDELITY",
        "objective": "StressTester genuine single multiplier ownership and zero-delta repricing on real closed trades",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_09_stress_tester_real_single_ownership_and_repricing"
    },
    {
        "id": "V124-10",
        "group": "ARTIFACT_CONTAINMENT",
        "objective": "Dataset geometry validation, artifact download security containment against directory traversal, and read-back verification",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_10_dataset_containment_and_readback_export"
    },
    {
        "id": "V124-11",
        "group": "REPLAY_INTEGRITY",
        "objective": "Three-month historical replay executes with honest coverage, provenance artifacts, and methodology disclosures",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_11_three_month_historical_replay_integrity"
    },
    {
        "id": "V124-12",
        "group": "ISOLATION_SENTINEL",
        "objective": "Full regression suite passes, sentinel proves zero production DB mutation and no live order/telegram leakage",
        "mapped_test": "test_v12_4_acceptance.py::test_v124_12_full_regression_and_isolation_sentinel"
    }
]

ALL_98_REQUIREMENTS: List[Dict[str, Any]] = ALL_86_REQUIREMENTS + DEFECT_REPAIR_REQUIREMENTS_V12_4


def build_v12_4_requirement_manifest(test_evidence_map: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """
    Builds the authoritative 98-requirement manifest for V12_4.
    """
    manifest = []
    for req in ALL_98_REQUIREMENTS:
        req_copy = dict(req)
        test_node = req_copy.get("mapped_test", "")
        test_fn = test_node.split("::")[-1] if "::" in test_node else test_node

        if test_evidence_map:
            status = test_evidence_map.get(test_node) or test_evidence_map.get(test_fn) or test_evidence_map.get(req_copy["id"])
            req_copy["status"] = status if status else "NOT_RUN"
        else:
            req_copy["status"] = "NOT_RUN"

        manifest.append(req_copy)
    return manifest
