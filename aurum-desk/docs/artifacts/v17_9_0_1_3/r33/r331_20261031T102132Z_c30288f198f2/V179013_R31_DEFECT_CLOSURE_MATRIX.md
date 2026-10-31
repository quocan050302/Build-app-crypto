# AURUM DESK V17.9.0.1.3 R3.1 — DEFECT CLOSURE MATRIX
**Authoritative QA Auditor & Principal Quantitative Systems Engineer Matrix**
**Release Target:** V17.9.0.1.3 R3.1

---

| Defect ID | Severity | Defect Name | Root Cause | Exact File & Lines Modified | Positive Verification | Negative Control / Fault Injection | Final Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **DEF-179013-R31-01 (P0-A)** | P0 Blocker | Retrospective Runtime Input Captures | Appended post-facto with identical UTC timestamps and reconstructed input maps. | `tools/generate_v17_9_0_1_artifacts.py` (runtime boundary instrumentation) | Real start/end times measured around each callable; 750 captures recorded under `R31_RUNTIME_CAPTURE_V1`. | N138 (Mislabeled capture point rejected), N139 (Missing runtime boundary rejected). | **CLOSED** |
| **DEF-179013-R31-02 (P0-B)** | P0 Blocker | Tautological Negative Controls | N138–N142 asserted local constants (`210 != 21`) without exercising real verifiers. | `backend/tests/test_v17_9_0_1_2_strict_eligibility_and_parity.py` (N138–N142, N146–N148) | Tests pass when invoked against real validation functions; assert specific rejection codes. | Verified fault injections produce expected rejection codes from `acceptance_verdict.py`. | **CLOSED** |
| **DEF-179013-R31-03 (P0-C)** | P0 Blocker | Hardcoded JUnit Reports Directory | Static path to old run `run_v17_9_0_1793340451146_6444`. | `tools/generate_v17_9_0_1_artifacts.py` (`aggregate_junit_xml_reports` & main resolution) | Dynamically queries `latest_run.json` to resolve active run's 92+ JUnit XML files. | Stale or missing run reports directory fails closed with `MISSING_JUNIT_REPORTS_DIR`. | **CLOSED** |
| **DEF-179013-R31-04 (P0-D)** | P0 Blocker | Hardcoded Async & Resource Telemetry | Concurrency and wall-clock seconds were hardcoded literals (`10`, `500.7s`). | `tools/generate_v17_9_0_1_artifacts.py` (telemetry loader from run artifacts) | Dynamically extracts actual wall clock (472.878s) and memory/disk metrics from run reports. | Missing telemetry or non-zero orphan worker count fails closed. | **CLOSED** |
| **DEF-179013-R31-05 (P0-E)** | P0 Blocker | Fail-Open Mandatory Artifact Verification | Verifier skipped missing files using `if fpath.exists():`. | `tools/acceptance_verdict.py` (`verify_persisted_acceptance_artifacts`) | All 19 mandatory R3.1 deliverables must exist, be non-empty, and pass schema validation. | N140 (Empty file rejected), N143 / N146 (Missing mandatory artifact rejected). | **CLOSED** |

