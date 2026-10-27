# AURUM DESK V17.7.4 — ACCEPTANCE ARTIFACT INTEGRITY
**Machine-Verifiable Evidence, Run-Scoped Bundles & Non-Forgeable Release Truth**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Reference HEAD:** `40f52d96f02b08ceaae6a2cfc9ff2673960dd949`

---

## 1. Overview & Threat Model

Acceptance testing for production release candidates must be backed by authentic, verifiable machine evidence. In previous releases, vulnerabilities included:
- Overwriting prior release artifacts (`v17_7_2_acceptance_results.json`), destroying audit history.
- Relying on fragile stdout regex matching rather than machine-structured reports.
- Tautological self-verification in unit tests that generated mock dictionaries matching expected values.
- Claiming release verification without binding to the actual git source code tested.

In AURUM DESK V17.7.4, acceptance artifacts are run-scoped, cryptographically tied to source code via SHA-256 digests, and backed by machine-parsed JUnit XML reports.

---

## 2. Directory Architecture & Run Scoping

Acceptance runs publish exclusively to a run-scoped directory:

```
docs/artifacts/v17_7_4/<run_id>/
├── acceptance_results.json          # Top-level release verdict and gate breakdown
├── source_manifest.json             # SHA-256 hashes of all source, config, and test files
├── gate_manifest.json               # Typed definitions of all 28 mandatory release gates
├── test_collection_manifest.json    # Exact list of collected test node IDs
├── command_results.json             # Process execution records (command, cwd, exit code, duration)
├── integrity_manifest.json          # File sizes and SHA-256 checksums of all generated artifacts
├── logs/                            # Full raw stdout/stderr logs for every gate (g01 - g28)
└── reports/                         # Machine-generated JUnit XML test reports (g02 - g27)
```

### 2.1 Preservation of Historical Evidence
Historical files in `docs/artifacts/`:
- `v17_7_1_acceptance_results.json`
- `v17_7_2_acceptance_results.json`
- `v17_7_3_acceptance_results.json`

remain **strictly read-only and immutable**. The runner refuses to overwrite or modify them.

---

## 3. Pure Fail-Closed Verdict Contract (`determine_release_verdict`)

The verdict logic in `tools/acceptance_verdict.py` is a pure function that evaluates gate evidence against `MANDATORY_GATES_V17_7_4` (G01 through G28):

### 3.1 Verdict Invariants
The release verdict evaluates strictly to:
- `ACCEPTED_FOR_PAPER_OPT_IN` if and only if **all 28 mandatory gates** have `status == "PASS"`, exit code 0, positive test counts, zero errors/failures, and verified current-run reports.
- `BLOCKED` if any gate is missing, malformed, skipped, failed, or has unverified evidence.

### 3.2 Gate Classification
1. **Command Gates (G01–G20, G21, G22, G23, G24, G25, G26, G27):** Require executed subprocess metadata (`exit_code == 0`, monotonic `duration_ms`, captured log path).
2. **Test Command Gates (G02–G20, G25–G27):** Require a valid JUnit XML report with `passed > 0`, `failed == 0`, and `errors == 0`.
3. **Verification & Audit Gates (G16, G27, G28):** Require domain-specific verification checks (exercised live submission spy, artifact checksum verification, and documentation completeness).

---

## 4. The Artifact Verifier (`tools/artifact_verifier.py`)

The artifact verifier implements three decoupled, testable procedures:
1. `build_source_manifest(root_dir)`: Recursively scans `backend/`, `frontend/src/`, `tools/`, and configuration files, computing SHA-256 digests while filtering transient caches (`__pycache__`, `.pytest_cache`, `node_modules`).
2. `write_acceptance_artifacts(...)`: Writes the full run-scoped bundle using atomic write operations, avoiding partially written corrupted states if a run is cancelled.
3. `verify_acceptance_bundle(bundle_dir, tested_source_sha)`: Validates that:
   - All required JSON manifests exist and adhere to schema.
   - All referenced log and report files exist within the run directory (no path traversal).
   - Checksums in `integrity_manifest.json` match disk file bytes.
   - The tested commit SHA matches the tested repository state.

---

## 5. Negative Control Verification Matrix (C01–C18)

Every safety and integrity invariant is verified by actual automated regression tests in `backend/tests/test_v17_7_adaptive_stop_loss.py` (`test_ce_19` and `test_ce_20`):

| Test Code | Invariant Verified | Negative Injection Applied | Expected Result | Verified Proof |
| :--- | :--- | :--- | :--- | :--- |
| **C01** | Missing Gate Blocking | Gate G05 omitted from results dictionary | Verdict returns `BLOCKED` | `test_ce_19` |
| **C02** | Empty Manifest | Manifest contains zero gates | Verdict returns `BLOCKED` | `test_ce_19` |
| **C03** | Malformed Payload | Gate value is non-dict string | Verdict returns `BLOCKED` | `test_ce_19` |
| **C04** | Missing Exit Code | Command gate omits `exit_code` | Verdict returns `BLOCKED` | `test_ce_19` |
| **C05** | Nonzero Exit with PASS | Status claims `PASS` but `exit_code == 1` | Verdict returns `BLOCKED` | `test_ce_19` |
| **C06** | Missing Test Report | Command gate references non-existent XML | Verdict returns `BLOCKED` | `test_ce_19` |
| **C07** | Report Parse Failure | XML report contains invalid syntax | Verdict returns `BLOCKED` | `test_ce_19` |
| **C08** | Pytest Collection Error | XML contains `<error message="...">` | Verdict returns `BLOCKED` | `test_ce_19` |
| **C09** | All-Skipped Suite | Pytest passed count is 0, skipped > 0 | Verdict returns `BLOCKED` | `test_ce_19` |
| **C10** | Missing Selected Tests | Mandatory test node ID not collected | Verdict returns `BLOCKED` | `test_ce_19` |
| **C11** | Cross-Run Leakage | Report references file from previous run | Verifier rejects bundle | `test_ce_20` |
| **C12** | Artifact Tampering | Report file modified after manifest generation | Hash mismatch triggers failure | `test_ce_20` |
| **C13** | Source Tampering | Source code modified during acceptance run | Source SHA mismatch triggers failure | `test_ce_20` |
| **C14** | Wrong Version / Run ID | Bundle manifest specifies mismatched run ID | Verifier rejects bundle | `test_ce_20` |
| **C15** | Valid Bundle Acceptance | Authentic bundle with full evidence | Verifier returns `(True, "VALID")` | `test_ce_20` |
| **C16** | Interrupted Run Safety | Run terminated midway | Partial artifacts never marked ACCEPTED | Runner lifecycle |
| **C17** | Historical Immutability | Prior JSON files protected | `v17_7_2` / `v17_7_3` unmodified | Runner teardown |
| **C18** | Docs/Evidence Match | Documented metrics match verified bundle | G28 integrity audit | Acceptance G28 |

---
*Signed and Approved for Production Paper Opt-In Evaluation.*
