"""
aurum-desk/backend/tests/test_kh03_r5_c2_fault_control_truth.py

KH-03.R5.C2 — Phase B Fault Control Truth Paired Negative/Positive Controls (`C2-D01`..`C2-D12`).
Exports `run_new_kh03_r5_c2_fault_controls_d12(executable_sha, run_id)` returning 12 verified
execution-derived paired fault receipts. Executes ZERO external market sockets.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Dict, List

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_03_r5_c1_readiness_and_red_service import derive_r5_c1_readiness_verdicts
from services.kh_03_r5_c2_fault_truth_and_readiness_service import (
    FaultCaseSpec,
    PRIOR_C1_TESTED_EXECUTABLE_SHA,
    PRIOR_R5_BASELINE_SHA,
    audit_fault_certification_ast_for_literal_flags,
    build_c1_all48_correction_ledger,
    derive_fault_control_verdict,
    execute_fault_case_pair,
    validate_fault_execution_receipt,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash


def _make_sample_valid_pair(*, fault_id: str, executable_sha: str, run_id: str) -> Dict[str, Any]:
    spec = FaultCaseSpec(
        fault_id=fault_id,
        control_name="SAMPLE_CLEAN_PAIR",
        target_qualified_name="validate_r5_operator_capture_scope",
        kind="NEGATIVE_CONTRACT",
        negative_perturbation="Sample negative scope rejection",
        positive_clean_description="Sample clean QA scope admission",
        negative_predicate_id="PRED_SCOPE_REJECTED",
        positive_predicate_id="PRED_SCOPE_ADMITTED_QA",
        negative_runner=lambda: {"admitted": False, "live_network_permitted": False, "violations_count": 1},
        positive_runner=lambda: {
            "admitted": True, "live_network_permitted": False, "mode": "QA_FIXTURE_OFFLINE_ONLY",
            "violations_count": 0, "scope_receipt_id": "scp_clean_01",
        },
    )
    return execute_fault_case_pair(spec, executable_sha=executable_sha, run_id=run_id)


def _recompute_branch_trace(br: Dict[str, Any]) -> None:
    body = {
        "fault_id": br["fault_id"],
        "branch_role": br["branch_role"],
        "execution_id": br["execution_id"],
        "invocation_seq": br["invocation_seq"],
        "target_qualified_name": br["target_qualified_name"],
        "executable_sha": br["executable_sha"],
        "run_id": br["run_id"],
        "observed_fields": dict(br["observed_fields"]),
        "predicate_id": br["predicate_id"],
        "exception_type": br["exception_type"],
        "pid": br["pid"],
    }
    br["trace_digest"] = canonical_hash(body)


def run_new_kh03_r5_c2_fault_controls_d12(
    *,
    executable_sha: str = "c" * 40,
    run_id: str = "c2_d12_unit",
) -> List[Dict[str, Any]]:
    """Executes all 12 P1-D Fault Control Truth paired controls (`C2-D01`..`C2-D12`)."""
    out: List[Dict[str, Any]] = []

    def _add(
        fid: str, name: str, target: str, kind: str, neg_desc: str, pos_desc: str, neg_fn: Any, pos_fn: Any,
        *, bw: Dict[str, Any] | None = None,
    ) -> None:
        spec = FaultCaseSpec(
            fault_id=fid,
            control_name=name,
            target_qualified_name=target,
            kind=kind,
            negative_perturbation=neg_desc,
            positive_clean_description=pos_desc,
            negative_predicate_id="PRED_FAULT_AGGREGATOR_REJECTS_UNPROVEN",
            positive_predicate_id="PRED_FAULT_AGGREGATOR_ACCEPTS_VALID_PAIR",
            negative_runner=neg_fn,
            positive_runner=pos_fn,
            baseline_witness_key=bw["witness_key"] if bw else None,
            baseline_sha=bw["baseline_sha"] if bw else None,
        )
        out.append(execute_fault_case_pair(spec, executable_sha=executable_sha, run_id=run_id, baseline_witness=bw))

    def _pos_valid_pair() -> Dict[str, Any]:
        p = _make_sample_valid_pair(fault_id="TMP_VALID_PAIR", executable_sha=executable_sha, run_id=run_id)
        ok, vs = validate_fault_execution_receipt(p, expected_executable_sha=executable_sha, expected_run_id=run_id)
        return {"validation_ok": ok, "violations_count": len(vs)}

    # D01: AST audit catches literal True in historical C1 source; passes on repaired C1 source!
    def _neg_d01() -> Dict[str, Any]:
        leg_src = "def f():\n    _rec('A', 'N', 'fn', 'K', True, True)\n"
        au = audit_fault_certification_ast_for_literal_flags(leg_src)
        return {"validation_ok": au["clean_no_literal_aggregator_flags"], "violations_count": au["suspicious_literal_findings_count"]}

    def _pos_d01() -> Dict[str, Any]:
        rep_p = BACKEND_ROOT / "tests" / "test_kh03_r5_c1_custody_abort_and_determinism.py"
        au = audit_fault_certification_ast_for_literal_flags(rep_p.read_text("utf-8"))
        return {"validation_ok": au["clean_no_literal_aggregator_flags"], "violations_count": au["suspicious_literal_findings_count"]}

    _add("C2-D01", "AST_LITERAL_FLAG_AUDIT_ALL48", "audit_fault_certification_ast_for_literal_flags", "NEGATIVE_CONTRACT",
         "Literal True passed to _rec detected by AST audit", "Repaired C1 48-control module passes AST audit cleanly", _neg_d01, _pos_d01)

    # D02: Legacy _rec negative real + positive literal True without positive receipt -> rejected!
    def _neg_d02() -> Dict[str, Any]:
        legacy_dict = {"fault_id": "C1-A13", "control_kind": "NEGATIVE_CONTRACT", "red_negative_detected": True, "green_clean_passed": True, "status": "PASS"}
        ok, vs = validate_fault_execution_receipt(legacy_dict, expected_executable_sha=executable_sha, expected_run_id=run_id)
        return {"validation_ok": ok, "violations_count": len(vs)}
    _add("C2-D02", "LEGACY_LITERAL_POSITIVE_REJECTED", "validate_fault_execution_receipt", "BASELINE_BUG_RED",
         "Legacy boolean-only _rec(..., True) dict rejected", "Typed paired receipt accepted", _neg_d02, _pos_valid_pair,
         bw={"baseline_sha": PRIOR_C1_TESTED_EXECUTABLE_SHA, "witness_key": "P1D_fault_control_literal_pass_weakness", "baseline_negative_reproduced": True})

    # D03: Missing negative, missing positive, or actual_invocation_witnessed=False -> FAIL
    def _neg_d03() -> Dict[str, Any]:
        p = _make_sample_valid_pair(fault_id="TMP_D03", executable_sha=executable_sha, run_id=run_id)
        p["positive_receipt"]["actual_invocation_witnessed"] = False
        ok1, vs1 = validate_fault_execution_receipt(p, expected_executable_sha=executable_sha, expected_run_id=run_id)
        p2 = dict(p, negative_receipt=None)
        ok2, vs2 = validate_fault_execution_receipt(p2, expected_executable_sha=executable_sha, expected_run_id=run_id)
        return {"validation_ok": ok1 or ok2, "violations_count": len(vs1) + len(vs2)}
    _add("C2-D03", "MISSING_BRANCH_OR_UNWITNESSED_REJECTED", "validate_fault_execution_receipt", "NEGATIVE_CONTRACT",
         "Missing negative receipt or unwitnessed positive rejected", "Both branches witnessed accepted", _neg_d03, _pos_valid_pair)

    # D04: Negative observed accepted or positive observed fail with forged status=PASS -> FAIL
    def _neg_d04() -> Dict[str, Any]:
        p = _make_sample_valid_pair(fault_id="TMP_D04", executable_sha=executable_sha, run_id=run_id)
        p["negative_receipt"]["observed_fields"]["admitted"] = True
        _recompute_branch_trace(p["negative_receipt"])
        p["negative_receipt"]["predicate_passed"] = True
        p["status"] = "PASS"
        ok, vs = validate_fault_execution_receipt(p, expected_executable_sha=executable_sha, expected_run_id=run_id)
        return {"validation_ok": ok, "violations_count": len(vs)}
    _add("C2-D04", "OBSERVED_FAILURE_DESPITE_STATUS_PASS_REJECTED", "validate_fault_execution_receipt", "NEGATIVE_CONTRACT",
         "Negative observation violated with recomputed trace_digest & status=PASS rejected", "Consistent observed fields accepted", _neg_d04, _pos_valid_pair)

    # D05: Reused positive execution ID across two cases / duplicate fault IDs -> FAIL
    def _neg_d05() -> Dict[str, Any]:
        p1 = _make_sample_valid_pair(fault_id="TMP_D05_A", executable_sha=executable_sha, run_id=run_id)
        p2 = _make_sample_valid_pair(fault_id="TMP_D05_B", executable_sha=executable_sha, run_id=run_id)
        p2["positive_receipt"] = copy.deepcopy(p1["positive_receipt"])
        p2["positive_receipt"]["fault_id"] = "TMP_D05_B"
        _recompute_branch_trace(p2["positive_receipt"])
        vd = derive_fault_control_verdict([p1, p2], expected_executable_sha=executable_sha, expected_run_id=run_id)
        return {"validation_ok": vd["valid"], "violations_count": vd["violations_count"]}
    _add("C2-D05", "SHARED_POSITIVE_AND_DUP_EXEC_ID_REJECTED", "derive_fault_control_verdict", "NEGATIVE_CONTRACT",
         "Reused positive execution_id across cases rejected", "Distinct positive execution_ids accepted", _neg_d05, _pos_valid_pair)

    # D06: Stale SHA/run or wrong kind -> FAIL
    def _neg_d06() -> Dict[str, Any]:
        p = _make_sample_valid_pair(fault_id="TMP_D06", executable_sha=PRIOR_C1_TESTED_EXECUTABLE_SHA, run_id=run_id)
        ok, vs = validate_fault_execution_receipt(p, expected_executable_sha=executable_sha, expected_run_id=run_id, stale_shas=[PRIOR_C1_TESTED_EXECUTABLE_SHA])
        return {"validation_ok": ok, "violations_count": len(vs)}
    _add("C2-D06", "STALE_SHA_OR_WRONG_KIND_REJECTED", "validate_fault_execution_receipt", "NEGATIVE_CONTRACT",
         "Stale prior C1 executable SHA rejected", "Current frozen executable SHA accepted", _neg_d06, _pos_valid_pair)

    # D07: Tampered trace with recomputed public trace_digest -> predicate recompute catches it!
    def _neg_d07() -> Dict[str, Any]:
        p = _make_sample_valid_pair(fault_id="TMP_D07", executable_sha=executable_sha, run_id=run_id)
        p["positive_receipt"]["observed_fields"]["live_network_permitted"] = True
        _recompute_branch_trace(p["positive_receipt"])
        ok, vs = validate_fault_execution_receipt(p, expected_executable_sha=executable_sha, expected_run_id=run_id)
        return {"validation_ok": ok, "violations_count": len(vs)}
    _add("C2-D07", "TAMPERED_OBSERVATION_RECOMPUTED_DIGEST_REJECTED", "validate_fault_execution_receipt", "NEGATIVE_CONTRACT",
         "Tampered positive live_network_permitted=True with recomputed trace_digest rejected", "Clean positive observation accepted", _neg_d07, _pos_valid_pair)

    # D08: BASELINE_BUG_RED without real baseline witness or using tested SHA as baseline SHA -> FAIL
    def _neg_d08() -> Dict[str, Any]:
        spec_b = FaultCaseSpec(
            fault_id="TMP_D08", control_name="FAKE_BASE", target_qualified_name="validate_r5_operator_capture_scope",
            kind="BASELINE_BUG_RED", negative_perturbation="n", positive_clean_description="p",
            negative_predicate_id="PRED_SCOPE_REJECTED", positive_predicate_id="PRED_SCOPE_ADMITTED_QA",
            negative_runner=lambda: {"admitted": False, "live_network_permitted": False, "violations_count": 1},
            positive_runner=lambda: {"admitted": True, "live_network_permitted": False, "mode": "QA_FIXTURE_OFFLINE_ONLY", "violations_count": 0, "scope_receipt_id": "s1"},
        )
        rec_no_bw = execute_fault_case_pair(spec_b, executable_sha=executable_sha, run_id=run_id, baseline_witness=None)
        return {"validation_ok": rec_no_bw["paired_receipt_verified"], "violations_count": len(rec_no_bw["validation_violations"])}

    def _pos_d08() -> Dict[str, Any]:
        spec_b = FaultCaseSpec(
            fault_id="TMP_D08_OK", control_name="REAL_BASE", target_qualified_name="validate_r5_operator_capture_scope",
            kind="BASELINE_BUG_RED", negative_perturbation="n", positive_clean_description="p",
            negative_predicate_id="PRED_SCOPE_REJECTED", positive_predicate_id="PRED_SCOPE_ADMITTED_QA",
            negative_runner=lambda: {"admitted": False, "live_network_permitted": False, "violations_count": 1},
            positive_runner=lambda: {"admitted": True, "live_network_permitted": False, "mode": "QA_FIXTURE_OFFLINE_ONLY", "violations_count": 0, "scope_receipt_id": "s1"},
        )
        rec_ok = execute_fault_case_pair(
            spec_b, executable_sha=executable_sha, run_id=run_id,
            baseline_witness={"baseline_sha": PRIOR_R5_BASELINE_SHA, "witness_key": "P1A_forged_trust_context_dict_live", "baseline_negative_reproduced": True},
        )
        return {"validation_ok": rec_ok["paired_receipt_verified"], "violations_count": len(rec_ok["validation_violations"])}
    _add("C2-D08", "BASELINE_BUG_RED_REQUIRES_REAL_WITNESS", "validate_fault_execution_receipt", "NEGATIVE_CONTRACT",
         "BASELINE_BUG_RED without baseline_witness rejected", "BASELINE_BUG_RED with valid 09493feb witness accepted", _neg_d08, _pos_d08)

    # D09: Positive branch missing target_qualified_name or mismatched target -> FAIL; same target -> PASS
    def _neg_d09() -> Dict[str, Any]:
        p = _make_sample_valid_pair(fault_id="TMP_D09", executable_sha=executable_sha, run_id=run_id)
        p["positive_receipt"]["target_qualified_name"] = "unrelated_other_fn"
        _recompute_branch_trace(p["positive_receipt"])
        ok, vs = validate_fault_execution_receipt(p, expected_executable_sha=executable_sha, expected_run_id=run_id)
        return {"validation_ok": ok, "violations_count": len(vs)}
    _add("C2-D09", "SAME_CONTRACT_TARGET_REQUIRED_ON_POSITIVE", "validate_fault_execution_receipt", "NEGATIVE_CONTRACT",
         "Positive targeting unrelated function rejected", "Positive targeting same contract accepted", _neg_d09, _pos_valid_pair)

    # D10: Unexpected exception fails; expected exception type only passes when actually raised!
    def _neg_d10() -> Dict[str, Any]:
        spec_e = FaultCaseSpec(
            fault_id="TMP_D10", control_name="UNEXP_EXC", target_qualified_name="validate_r5_operator_capture_scope",
            kind="NEGATIVE_CONTRACT", negative_perturbation="raises RuntimeError unexpectedly", positive_clean_description="clean",
            negative_predicate_id="PRED_SCOPE_REJECTED", positive_predicate_id="PRED_SCOPE_ADMITTED_QA",
            negative_runner=lambda: (_ for _ in ()).throw(RuntimeError("unexpected_boom")),
            positive_runner=lambda: {"admitted": True, "live_network_permitted": False, "mode": "QA_FIXTURE_OFFLINE_ONLY", "violations_count": 0, "scope_receipt_id": "s1"},
        )
        r = execute_fault_case_pair(spec_e, executable_sha=executable_sha, run_id=run_id)
        return {"validation_ok": r["paired_receipt_verified"], "violations_count": len(r["validation_violations"])}
    _add("C2-D10", "UNEXPECTED_EXCEPTION_REJECTED_NOT_PASS", "execute_fault_case_pair", "NEGATIVE_CONTRACT",
         "Unexpected exception in negative branch rejected", "Clean witnessed pair accepted", _neg_d10, _pos_valid_pair)

    # D11: Inherited negative-only receipt cannot be relabeled as paired!
    def _neg_d11() -> Dict[str, Any]:
        inh_neg_only = {"fault_id": "R5-F01", "control_kind": "LEGACY_NEGATIVE_ONLY", "red_negative_detected": True, "green_clean_passed": True}
        ok, vs = validate_fault_execution_receipt(inh_neg_only, expected_executable_sha=executable_sha, expected_run_id=run_id)
        return {"validation_ok": ok, "violations_count": len(vs)}
    _add("C2-D11", "INHERITED_NEGATIVE_ONLY_CANNOT_RELABEL_PAIRED", "validate_fault_execution_receipt", "NEGATIVE_CONTRACT",
         "Inherited negative-only legacy dict rejected by paired validator", "True C2 paired receipt accepted", _neg_d11, _pos_valid_pair)

    # D12: Active C1 certification gate fails closed with LEGACY_CONTROL_PROOF_INSUFFICIENT_USE_C2 on boolean-only 18!
    def _neg_d12() -> Dict[str, Any]:
        c1_res = derive_r5_c1_readiness_verdicts(
            raw_evidence={"18": {"status": "PASS", "inherited_r5_r4_c2_c1_r3_controls_count": 288, "new_c1_controls_count": 48, "total_controls": 336, "unique_fault_ids": 336, "failed_controls": 0}},
            mem_audit={"status": "PASS", "below_floor_samples_count": 0},
            pytest_recomp={"valid": True},
            science_recomp={"valid": True},
            junit_ok=True,
            r4_replay_ok=True,
        )
        g26 = c1_res["gates_30"]["G26_FAULT_CONTROLS"]
        return {"validation_ok": g26 == "PASS", "violations_count": 1 if g26 == "LEGACY_CONTROL_PROOF_INSUFFICIENT_USE_C2" else 0}

    def _pos_d12() -> Dict[str, Any]:
        c1_res = derive_r5_c1_readiness_verdicts(
            raw_evidence={"18": {"status": "PASS", "inherited_r5_r4_c2_c1_r3_controls_count": 288, "new_c1_controls_count": 48, "total_controls": 336, "unique_fault_ids": 336, "failed_controls": 0, "paired_fault_receipts_verified": True, "paired_c1_controls_verified_count": 48}},
            mem_audit={"status": "PASS", "below_floor_samples_count": 0},
            pytest_recomp={"valid": True},
            science_recomp={"valid": True},
            junit_ok=True,
            r4_replay_ok=True,
        )
        g26 = c1_res["gates_30"]["G26_FAULT_CONTROLS"]
        return {"validation_ok": g26 == "PASS", "violations_count": 0 if g26 == "PASS" else 1}
    _add("C2-D12", "LEGACY_C1_GATE_FAILS_CLOSED_WITHOUT_PAIRED_RECEIPTS", "derive_r5_c1_readiness_verdicts", "NEGATIVE_CONTRACT",
         "Legacy C1 G26 rejects boolean-only 18 with LEGACY_CONTROL_PROOF_INSUFFICIENT_USE_C2", "G26 accepts only verified paired_c1_controls_verified_count>=48", _neg_d12, _pos_d12)

    return out


def test_c2_d01_to_d12_all_fault_control_truth_pairs_pass() -> None:
    recs = run_new_kh03_r5_c2_fault_controls_d12(executable_sha="d" * 40, run_id="pytest_d12")
    assert len(recs) == 12
    vd = derive_fault_control_verdict(recs, expected_executable_sha="d" * 40, expected_run_id="pytest_d12")
    assert vd["valid"] is True, f"D01..D12 failed: {vd['violations']}"
    assert vd["passed_controls"] == 12 and vd["unique_positive_execution_ids_count"] == 12


def test_c2_c1_all48_correction_ledger_from_git_sha_exact() -> None:
    led = build_c1_all48_correction_ledger(repo_root=REPO_ROOT, c1_baseline_sha=PRIOR_C1_TESTED_EXECUTABLE_SHA)
    assert led["status"] == "PASS"
    assert led["total_c1_controls_audited"] == 48
    assert led["unique_c1_fault_ids"] == 48
    assert led["c1_defect_summary"]["LITERAL_TRUE_POSITIVE"] >= 20
    assert led["c1_defect_summary"]["LITERAL_TRUE_NEGATIVE"] >= 1
    assert led["c2_baseline_bug_red_count"] == 8
    assert led["c2_negative_contract_count"] == 40
    assert led["historical_c1_artifacts_mutated"] is False
