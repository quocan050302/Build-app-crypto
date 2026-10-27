# AURUM DESK V17.7.4.1 — EXACT-COMMIT ACCEPTANCE SPECIFICATION

**Release Version:** V17.7.4.1  
**Target Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Execution Mode:** Production-Grade Release Acceptance  
**Status:** ACTIVE  

---

## 1. Problem Statement: Acceptance vs. Commit SHA Disconnect

In release V17.7.4, the acceptance runner executed against working tree source SHA `55538218552179710cbc2a8b6b84d9953088397a`, but the final reviewed commit on the branch was recorded as `1a9abc0173f179da2d2db071a1eb98e1eb35d34e`. Because executable files differed between the two states, independent verification could not prove that the exact final code passed all gates without regression.

V17.7.4.1 resolves this defect (Defect C) by enforcing a strict **7-Step Exact-Commit Release Workflow**.

---

## 2. The 7-Step Exact-Commit Release Workflow

```
[Step A] Implement Code & Test Fixes
          ↓
[Step B] Run Targeted Regression Suites (ATR, DB Safety, Stop Loss)
          ↓
[Step C] Commit Final Executable Code (Final Code SHA established)
          ↓
[Step D] Run Master Acceptance Runner (G01–G28) against Final Code SHA
          ↓
[Step E] Verify Immutable Run-Scoped Acceptance Bundle (Hashes & Verdict)
          ↓
[Step F] Commit Acceptance Evidence & Reports (Evidence SHA established)
          ↓
[Step G] Push to Feature Branch (feat/v17-7-adaptive-structural-stop-loss)
```

### Invariants:
1. **No Executable Code Changes After Step C:** Any modification to backend, frontend, or tools code invalidates the acceptance run and requires restarting at Step C.
2. **Deterministic Source Manifest:** `build_source_manifest()` traverses all executable files across `backend/`, `tools/`, `frontend/`, and root configurations, computing individual file SHA256 hashes and a single composite SHA.
3. **Exact Commit Identity:** The acceptance runner verifies:
   $$\text{tested\_commit\_sha} == \text{git rev-parse HEAD}$$
   and checks that `git_worktree_clean` is true for executable paths.

---

## 3. Bundle Integrity & Anti-Fabrication Safeguards

The artifact verifier (`tools/artifact_verifier.py`) enforces:
1. **Presence of Core Artifacts:** `acceptance_results.json`, `source_manifest.json`, `gate_manifest.json`, `integrity_manifest.json`.
2. **Leaf File Hash Matching:** Every file in the run directory has its SHA256 recorded in `integrity_manifest.json`; tampering with any file immediately fails verification.
3. **Raw JUnit XML Failure Detection:** Even if `acceptance_results.json` records `status: PASS`, the verifier parses all XML reports in `reports/*.xml` to ensure zero failures and zero errors exist.
4. **Composite Source Verification:** Verifies that source code did not mutate between runner initialization and conclusion.
5. **Verdict Consistency:** Recalculates the release verdict independently using `tools.acceptance_verdict:determine_release_verdict`. Only when all 28 mandatory gates pass does the verdict evaluate to `ACCEPTED_FOR_PAPER_OPT_IN`.

---

## 4. Run-Scoped Directory Structure

Each acceptance execution produces an isolated, immutable directory under:
`docs/artifacts/v17_7_4/<RUN_ID>/`
containing:
- `acceptance_results.json`: Full gate status, test counts, execution times, and verdict.
- `source_manifest.json`: Every source file path and its SHA256 digest.
- `gate_manifest.json`: List of all 28 mandatory release gates.
- `integrity_manifest.json`: File-level hashes of all artifacts in the run directory.
- `reports/`: Machine-readable JUnit XML test reports (`g02.xml` through `g27.xml`).
- `logs/`: Standard output and error logs for all executed commands.
