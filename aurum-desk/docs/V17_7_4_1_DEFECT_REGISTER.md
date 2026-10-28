# AURUM DESK V17.7.4.1 — DEFECT REGISTER & CLOSURE MATRIX

**Release Version:** V17.7.4.1  
**Target Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Closure Date:** 2026-10-27  
**Status:** ALL 10 DEFECTS RESOLVED & VERIFIED  

---

## Defect Closure Summary Table

| Defect ID | Severity | Area | Original File | Regression Test | Final Status |
|---|---|---|---|---|---|
| `FINAL-01` | P0 | ATR Authority | `backend/services/stop_placement_service.py` | `ATR-FINAL-01`, `ATR-FINAL-02` | **CLOSED / VERIFIED** |
| `FINAL-02` | P0 | Timestamps | `backend/services/stop_placement_service.py` | `ATR-FINAL-03`, `ATR-FINAL-04`, `ATR-FINAL-05` | **CLOSED / VERIFIED** |
| `FINAL-03` | P1 | Buffer Math | `backend/services/stop_placement_service.py` | `ATR-FINAL-08`, `ATR-FINAL-09` | **CLOSED / VERIFIED** |
| `FINAL-04` | P0 | ARM Boundary | `backend/services/stop_placement_service.py` | `ATR-FINAL-10`, `ATR-FINAL-11` | **CLOSED / VERIFIED** |
| `FINAL-05` | P0 | DB Aliasing | `backend/database.py` | `DB-FINAL-03`, `DB-FINAL-05` | **CLOSED / VERIFIED** |
| `FINAL-06` | P1 | Root Reuse | `backend/database.py` | `DB-FINAL-06`, `DB-FINAL-07`, `DB-FINAL-08` | **CLOSED / VERIFIED** |
| `FINAL-07` | P0 | Acceptance | `tools/run_v17_7_acceptance.py` | Section 07 Workflow & Gate G27 | **CLOSED / VERIFIED** |
| `FINAL-08` | P1 | Artifact Verifier | `tools/artifact_verifier.py` | `test_ce_20` | **CLOSED / VERIFIED** |
| `FINAL-09` | P0 | Regression | Entire Suite | Full Pytest, Vitest, Playwright | **CLOSED / VERIFIED** |
| `FINAL-10` | P1 | Pilot Readiness | Telemetry & Pilot Checklist | `V17_7_4_1_PAPER_PILOT_READINESS.md` | **CLOSED / VERIFIED** |

---

## Detailed Defect Records

### FINAL-01: Incomplete ATR Metadata Validation
- **Severity:** P0 (Safety Blocker)
- **Original File:** `backend/services/stop_placement_service.py`
- **Original Function:** `compute_stop_buffer()`
- **Reproduction:** Passing `StopATRResult(status="AVAILABLE", value=2.5, bars_count=0)` bypassed full authority validation under `PAPER_STRUCTURAL` mode.
- **Root Cause:** A conditional check `if getattr(atr_m1, "bars_count", 0) > 0:` skipped validation when candle count was zero or missing.
- **Actual Code Fix:** Mandate unconditional `validate_stop_atr_authority()` for M1 in `PAPER_STRUCTURAL` mode; require `source_candle_count > 0` and valid timestamps.
- **Regression Tests:** `ATR-FINAL-01`, `ATR-FINAL-02`
- **Final Status:** **CLOSED / VERIFIED**

---

### FINAL-02: Malformed Causal Timestamp Acceptance
- **Severity:** P0 (Safety Blocker)
- **Original File:** `backend/services/stop_placement_service.py`
- **Original Function:** `validate_stop_atr_authority()`
- **Reproduction:** Calling `validate_stop_atr_authority(decision_cutoff="invalid")` or `decision_cutoff=float("nan")` swallowed exceptions without returning explicit rejection.
- **Root Cause:** Overly permissive `except (ValueError, TypeError): pass` block permitted invalid cutoff values to pass without rejection.
- **Actual Code Fix:** Explicitly reject non-numeric, boolean, `NaN`, infinite, or negative `decision_cutoff` with `ATR_DECISION_CUTOFF_INVALID`. Reject missing or non-positive required timestamps with `ATR_TIMESTAMP_MISSING_OR_INVALID`.
- **Regression Tests:** `ATR-FINAL-03`, `ATR-FINAL-04`, `ATR-FINAL-05`
- **Final Status:** **CLOSED / VERIFIED**

---

### FINAL-03: Invalid Optional ATR Contamination
- **Severity:** P1 (Correctness)
- **Original File:** `backend/services/stop_placement_service.py`
- **Original Function:** `compute_stop_buffer()`
- **Reproduction:** Malformed optional M5 ATR was processed into buffer components or could influence weighted buffer calculations.
- **Root Cause:** Insufficient isolation between optional multi-timeframe ATR components and primary M1 buffer.
- **Actual Code Fix:** In `compute_stop_buffer()`, validate optional M5 strictly if provided. If M5 fails validation or is missing when optional, it is strictly excluded from `components` and has zero influence on the calculated buffer or stop loss distance.
- **Regression Tests:** `ATR-FINAL-08`, `ATR-FINAL-09`
- **Final Status:** **CLOSED / VERIFIED**

---

### FINAL-04: ATR Authority at ARM Boundary
- **Severity:** P0 (Execution Safety)
- **Original File:** `backend/services/stop_placement_service.py`
- **Original Function:** `validate_applied_stop_decision()`
- **Reproduction:** ARM boundary accepted scalar-only ATR evidence without validating underlying provenance object when candidate was passed as a dict.
- **Root Cause:** Attribute extraction from candidate used `getattr()` without handling candidate dictionaries uniformly.
- **Actual Code Fix:** Support both object and dictionary candidates. Strictly verify that `atr_m1_result` is present and passes authority validation at the ARM barrier.
- **Regression Tests:** `ATR-FINAL-10`, `ATR-FINAL-11`
- **Final Status:** **CLOSED / VERIFIED**

---

### FINAL-05: External Test Database Alias Protection
- **Severity:** P0 (Data Integrity Blocker)
- **Original File:** `backend/database.py`
- **Original Function:** `assert_test_database_isolated()`
- **Reproduction:** Hardlinking an external database file into a test root created a target with matching path containment but identical inode to external data.
- **Root Cause:** Path canonicalization check compared only against `aurum_desk.db`, failing to detect hardlinks to external files with other names.
- **Actual Code Fix:** Added check for `t_stat.st_nlink > 1`, rejecting hardlinked databases with `HARDLINK_ALIAS_DETECTED`.
- **Regression Tests:** `DB-FINAL-03`, `DB-FINAL-05`
- **Final Status:** **CLOSED / VERIFIED**

---

### FINAL-06: Unsafe Test-Root Reuse
- **Severity:** P1 (Isolation)
- **Original File:** `backend/database.py`
- **Original Function:** `_read_and_validate_marker()`, `assert_test_database_isolated()`
- **Reproduction:** Corrupted or foreign-run `.aurum_test_owned` markers, or pre-existing files modified before root creation, could be targeted.
- **Root Cause:** Marker parser did not validate schema and run ID strictly; file modification time was not compared against marker creation.
- **Actual Code Fix:** Enforce marker JSON schema, check `AURUM_TEST_RUN_ID` match, and verify target `mtime >= marker_created - 0.5`.
- **Regression Tests:** `DB-FINAL-06`, `DB-FINAL-07`, `DB-FINAL-08`
- **Final Status:** **CLOSED / VERIFIED**

---

### FINAL-07: Acceptance Source SHA Mismatch
- **Severity:** P0 (Release Audit Blocker)
- **Original File:** `tools/run_v17_7_acceptance.py`
- **Original Function:** Master Acceptance Workflow
- **Reproduction:** Release artifacts in V17.7.4 recorded a tested source SHA (`55538...`) that differed from the final committed code SHA (`1a9abc...`).
- **Root Cause:** Running acceptance on an uncommitted or intermediate working tree before committing final executable code.
- **Actual Code Fix:** Mandate Step C (commit all executable source) before Step D (run acceptance runner against `git rev-parse HEAD`), ensuring `tested_source_sha == final_code_sha`.
- **Regression Tests:** Workflow verification & Gate G27
- **Final Status:** **CLOSED / VERIFIED**

---

### FINAL-08: Incomplete Artifact Integrity Verification
- **Severity:** P1 (Auditability)
- **Original File:** `tools/artifact_verifier.py`
- **Original Function:** `verify_acceptance_bundle()`
- **Reproduction:** Report files could be tampered with or modified without detection if only top-level summary was checked.
- **Root Cause:** Verification lacked exhaustive leaf hash matching and raw JUnit XML error/failure parsing.
- **Actual Code Fix:** Implemented comprehensive leaf file SHA256 checking in `integrity_manifest.json`, containment checks for foreign files, and strict XML failure scanning.
- **Regression Tests:** `test_ce_20` (8 positive and negative control scenarios)
- **Final Status:** **CLOSED / VERIFIED**

---

### FINAL-09: Full Regression Provenance
- **Severity:** P0 (Regression Safety)
- **Original File:** Entire codebase
- **Original Function:** Test execution suites
- **Reproduction:** Running partial suites could miss latent regressions across older modules.
- **Root Cause:** Need for complete, unskipped backend, frontend, build, lint, and E2E regression runs.
- **Actual Code Fix:** Executed complete `backend/tests/` pytest suite, targeted v17.7 suite, frontend Vitest, build, lint, and Playwright suites.
- **Regression Tests:** Full backend pytest suite, frontend test suite, Playwright browser suite
- **Final Status:** **CLOSED / VERIFIED**

---

### FINAL-10: PAPER Pilot Readiness
- **Severity:** P1 (Operational Readiness)
- **Original File:** Governance & Telemetry
- **Original Function:** Pilot telemetry and mode gating
- **Reproduction:** System lacking explicit opt-in checklist and telemetry mapping for paper pilot.
- **Root Cause:** Need for explicit operational documentation separating software acceptance from runtime pilot activation.
- **Actual Code Fix:** Documented complete telemetry schema, invariant contracts, and step-by-step pilot activation checklist in `docs/V17_7_4_1_PAPER_PILOT_READINESS.md`.
- **Regression Tests:** Documented in readiness report
- **Final Status:** **CLOSED / VERIFIED**
