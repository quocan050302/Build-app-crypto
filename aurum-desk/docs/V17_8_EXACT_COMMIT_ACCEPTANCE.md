# AURUM DESK V17.8 — EXACT-COMMIT RELEASE ACCEPTANCE PROTOCOL

## 1. Zero-Discrepancy Invariant
To eliminate tested-source-versus-final-commit discrepancies, Aurum Desk V17.8 mandates the following execution sequence:

1. **Step A (Implementation):** Implement all features and targeted unit tests.
2. **Step B (Regression Validation):** Verify all cross-module regressions pass.
3. **Step C (Executable Source Freeze):** Commit all final executable code and test files to the feature branch. Record:
   `FINAL_CODE_SHA`
4. **Step D (Full Acceptance Run):** Execute `tools/run_v17_8_acceptance.py` on the clean committed source tree.
   - Computes start-of-run source manifest and composite SHA.
   - Executes all 42 gates (G01 to G42).
   - Computes end-of-run source manifest.
   - Asserts: `start_composite_sha == end_composite_sha` (no source mutation during test run).
5. **Step E (Commit Verification):** Asserts:
   `tested_commit_sha == FINAL_CODE_SHA`
6. **Step F (Evidence Commit):** Commit only generated acceptance artifacts, logs, and markdown documentation. No executable code changes are permitted in this commit. Record:
   `EVIDENCE_SHA`
7. **Step G (Ancestry & Difference Verification):** Verify that the difference between `EVIDENCE_SHA` and `FINAL_CODE_SHA` consists solely of documentation and artifacts.
8. **Step H (Push):** Push `feat/v17-8-unicorn-structural-sl-tp-intelligence` to remote repository.
9. **Step I (Remote HEAD Audit):** Recheck remote tracking and branch status.

---

## 2. Machine-Readable Artifact Structure
Run artifacts are written to `docs/artifacts/v17_8/<RUN_ID>/`:
- `gate_manifest.json`: The authoritative list of 42 mandatory gates.
- `source_manifest.json`: SHA-256 digests of all source files and composite digest.
- `acceptance_results.json`: Execution results, test counts, exit codes, durations.
- `integrity_manifest.json`: SHA-256 digests of all written leaf files.
- `reports/`: Raw JUnit XML test results.
- `logs/`: Subprocess command stdout and stderr logs.
