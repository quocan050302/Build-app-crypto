"""
Aurum Desk V12.3 Real Evidence Collector.
Parses genuine pytest JUnit XML test execution reports and maps real test node outcomes
to requirement IDs (A01-E12, P01-P12).
No hardcoded PASS statuses: requirement status is PASS only if all mapped tests executed and passed.
"""

import os
import xml.etree.ElementTree as ET
import json
import time
import subprocess
from typing import Dict, Any, List, Optional
from pathlib import Path

from lab.v12_2_manifest import ALL_74_REQUIREMENTS
from lab.v12_4_manifest import ALL_98_REQUIREMENTS

# V12.3 Defect & Correctness Verification Requirements
DEFECT_REQUIREMENTS_V12_3: List[Dict[str, Any]] = [
    {"id": "P01", "group": "CORRECTNESS_EVIDENCE", "objective": "Pytest JUnit XML collector dynamically computes pass/fail; no hardcoded 74/74 PASS", "mapped_test": "test_v12_3_acceptance.py::test_p01_evidence_collector_dynamic_no_hardcoded_pass"},
    {"id": "P02", "group": "CORRECTNESS_EVIDENCE", "objective": "Real boundary assertions on prefix invariance, target model, and parity replace vacuous tests", "mapped_test": "test_v12_3_acceptance.py::test_p02_genuine_assertions_on_prefix_and_parity"},
    {"id": "P03", "group": "CORRECTNESS_EVIDENCE", "objective": "Replay evaluates causal news blackout and temporal lesson rules", "mapped_test": "test_v12_3_acceptance.py::test_p03_causal_news_and_lesson_evaluation"},
    {"id": "P04", "group": "CORRECTNESS_EVIDENCE", "objective": "Replay cancellation checkpoint stops worker immediately; bounded queue enforced", "mapped_test": "test_v12_3_acceptance.py::test_p04_cancellation_checkpoint_and_bounded_queue"},
    {"id": "P05", "group": "CORRECTNESS_EVIDENCE", "objective": "Frontend does not auto-rerun on FAILED/CANCELLED; supports clean job cancellation", "mapped_test": "test_v12_3_acceptance.py::test_p05_frontend_job_flow_and_no_rerun"},
    {"id": "P06", "group": "CORRECTNESS_EVIDENCE", "objective": "Ledger postings and execution events emitted live during simulation", "mapped_test": "test_v12_3_acceptance.py::test_p06_live_ledger_postings_and_events"},
    {"id": "P07", "group": "CORRECTNESS_EVIDENCE", "objective": "Cash balance and net PnL reconciled to exact cent without 0.02 rounding drift", "mapped_test": "test_v12_3_acceptance.py::test_p07_exact_accounting_no_drift"},
    {"id": "P08", "group": "CORRECTNESS_EVIDENCE", "objective": "Stress multiplier single ownership: effective spread = base * sm, no 4x compounding", "mapped_test": "test_v12_3_acceptance.py::test_p08_stress_multiplier_single_ownership"},
    {"id": "P09", "group": "CORRECTNESS_EVIDENCE", "objective": "Zero-delta repricing matches baseline net PnL to the exact cent", "mapped_test": "test_v12_3_acceptance.py::test_p09_zero_delta_repricing_exact_baseline"},
    {"id": "P10", "group": "CORRECTNESS_EVIDENCE", "objective": "Latency labeled honestly as ESTIMATED_EXECUTION_WITH_LATENCY_APPROXIMATION", "mapped_test": "test_v12_3_acceptance.py::test_p10_latency_labeled_as_approximation"},
    {"id": "P11", "group": "CORRECTNESS_EVIDENCE", "objective": "Strict artifact containment rejects symlinks pointing outside job directory", "mapped_test": "test_v12_3_acceptance.py::test_p11_artifact_containment_rejects_symlink_escape"},
    {"id": "P12", "group": "CORRECTNESS_EVIDENCE", "objective": "Honest economic conclusions: 14/67 sessions labeled COVERAGE_UNMET / PARTIAL_EXPANSION", "mapped_test": "test_v12_3_acceptance.py::test_p12_honest_coverage_classification"}
]

ALL_V12_3_REQUIREMENTS = ALL_74_REQUIREMENTS + DEFECT_REQUIREMENTS_V12_3
ALL_V12_4_REQUIREMENTS = ALL_98_REQUIREMENTS


class EvidenceCollector:
    """
    Parses JUnit XML files produced by pytest and generates an audited,
    evidence-backed test_results.json artifact.
    """

    @classmethod
    def parse_junit_xml(cls, xml_path: str) -> Dict[str, Any]:
        """
        Parses a JUnit XML file and extracts all test case outcomes.
        """
        if not os.path.exists(xml_path):
            raise FileNotFoundError(f"JUnit XML evidence file not found: {xml_path}")

        tree = ET.parse(xml_path)
        root = tree.getroot()

        test_cases: List[Dict[str, Any]] = []
        outcomes_by_name: Dict[str, str] = {}
        outcomes_by_node: Dict[str, str] = {}

        for testcase in root.iter("testcase"):
            classname = testcase.get("classname", "")
            name = testcase.get("name", "")
            file_path = testcase.get("file", "")
            time_sec = float(testcase.get("time", "0.0"))

            status = "PASS"
            failure_msg = None
            traceback_text = None

            # Check for failure
            failure = testcase.find("failure")
            if failure is not None:
                status = "FAIL"
                failure_msg = failure.get("message", "")
                traceback_text = failure.text

            # Check for error
            error = testcase.find("error")
            if error is not None:
                status = "ERROR"
                failure_msg = error.get("message", "")
                traceback_text = error.text

            # Check for skipped / xfailed
            skipped = testcase.find("skipped")
            if skipped is not None:
                status = "SKIP"
                failure_msg = skipped.get("message", "")

            # Construct node id
            node_id = f"{file_path}::{name}" if file_path else f"{classname}::{name}"

            tc_info = {
                "name": name,
                "classname": classname,
                "file": file_path,
                "node_id": node_id,
                "time_sec": time_sec,
                "status": status,
                "message": failure_msg,
                "traceback": traceback_text
            }
            test_cases.append(tc_info)
            outcomes_by_name[name] = status
            outcomes_by_node[node_id] = status

        return {
            "test_cases": test_cases,
            "outcomes_by_name": outcomes_by_name,
            "outcomes_by_node": outcomes_by_node
        }

    @classmethod
    def evaluate_requirements(cls, xml_path: str) -> Dict[str, Any]:
        """
        Evaluates ALL_V12_3_REQUIREMENTS against parsed JUnit XML evidence.
        Returns full report with zero hardcoding.
        """
        parsed = cls.parse_junit_xml(xml_path)
        outcomes_by_name = parsed["outcomes_by_name"]
        outcomes_by_node = parsed["outcomes_by_node"]

        req_results: List[Dict[str, Any]] = []
        passed_reqs = 0
        failed_reqs = 0
        blocked_reqs = 0
        not_run_reqs = 0

        requirements_pool = ALL_V12_4_REQUIREMENTS if "ALL_V12_4_REQUIREMENTS" in globals() else ALL_V12_3_REQUIREMENTS
        for req in requirements_pool:
            req_id = req["id"]
            mapped_test = req["mapped_test"]
            test_func = mapped_test.split("::")[-1] if "::" in mapped_test else mapped_test

            # Exact node_id matching without ambiguous fuzzy false positives (V124-06)
            status = "NOT_RUN"
            if mapped_test in outcomes_by_node:
                status = outcomes_by_node[mapped_test]
            elif f"tests/{mapped_test}" in outcomes_by_node:
                status = outcomes_by_node[f"tests/{mapped_test}"]
            elif mapped_test.startswith("tests/") and mapped_test[6:] in outcomes_by_node:
                status = outcomes_by_node[mapped_test[6:]]
            else:
                # Match exact test function node boundary (::test_name or ::test_name[param])
                exact_matches = [
                    (node, st) for node, st in outcomes_by_node.items()
                    if node.endswith(f"::{test_func}") or f"::{test_func}[" in node
                ]
                if exact_matches:
                    # If any parameterized run failed, status is FAIL
                    if any(st in ("FAIL", "ERROR") for _, st in exact_matches):
                        status = "FAIL"
                    elif all(st == "PASS" for _, st in exact_matches):
                        status = "PASS"
                    elif any(st == "SKIP" for _, st in exact_matches):
                        status = "SKIP"
                    else:
                        status = exact_matches[0][1]

            if status == "PASS":
                passed_reqs += 1
            elif status in ("FAIL", "ERROR"):
                failed_reqs += 1
            elif status == "SKIP":
                blocked_reqs += 1
            else:
                not_run_reqs += 1

            req_results.append({
                "id": req_id,
                "group": req["group"],
                "objective": req["objective"],
                "mapped_test": mapped_test,
                "status": status
            })

        total_reqs = len(requirements_pool)
        any_failed = (failed_reqs > 0 or any(tc["status"] in ("FAIL", "ERROR") for tc in parsed["test_cases"]))
        overall_status = "PASS_WITH_EVIDENCE" if (passed_reqs == total_reqs and not any_failed) else (
            "FAIL_WITH_EVIDENCE" if any_failed else "PARTIAL_OR_BLOCKED"
        )

        # Get git SHA
        try:
            git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=os.path.dirname(__file__)).decode("utf-8").strip()
        except Exception:
            git_commit = "UNKNOWN"

        report = {
            "source_git_commit": git_commit,
            "evaluated_at": int(time.time() * 1000),
            "evidence_xml": xml_path,
            "total_requirements": total_reqs,
            "passed": passed_reqs,
            "failed": failed_reqs,
            "blocked": blocked_reqs,
            "not_run": not_run_reqs,
            "overall_status": overall_status,
            "is_hardcoded": False,
            "total_test_cases": len(parsed["test_cases"]),
            "requirements": req_results,
            "raw_test_cases": parsed["test_cases"]
        }
        return report

    @classmethod
    def export_test_results(cls, xml_path: str, output_json_path: str) -> Dict[str, Any]:
        """
        Evaluates requirements from JUnit XML and saves test_results.json.
        """
        report = cls.evaluate_requirements(xml_path)
        os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report
