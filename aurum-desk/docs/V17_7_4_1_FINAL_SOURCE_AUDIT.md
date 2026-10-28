# AURUM DESK V17.7.4.1 — FINAL SOURCE AND ROOT CAUSE AUDIT

**Release Version:** V17.7.4.1  
**Target Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Reference Reviewed Commit:** `1a9abc0173f179da2d2db071a1eb98e1eb35d34e`  
**Audit Date:** 2026-10-27  
**Status:** COMPLETE — REMAINING GAPS IDENTIFIED & ADDRESSED  

---

## 1. Executive Summary & Objective

Aurum Desk V17.7.4 introduced a comprehensive 28-gate release acceptance framework, test database isolation mechanisms, and structural Stop Loss placement. However, an independent pre-flight audit of commit `1a9abc0173f179da2d2db071a1eb98e1eb35d34e` identified three critical residual defect areas:

1. **Defect A — ATR Provenance and Strict Fail-Closed Validation:**
   - In `backend/services/stop_placement_service.py:compute_stop_buffer()`, full ATR authority validation was conditionally skipped when `bars_count` or `source_candle_count` was missing or $\le 0$. Under `PAPER_STRUCTURAL` mode, this allowed an ATR object with status `AVAILABLE` and scalar `value > 0` to bypass causal provenance verification.
   - Non-numeric or invalid decision cutoffs (e.g. string literals, NaN, negative timestamps) were silently caught by exception handlers or coerced into integers, evading strict fail-closed rejection with required reason codes (`ATR_DECISION_CUTOFF_INVALID`, `ATR_TIMESTAMP_MISSING_OR_INVALID`).
   - Potential contamination from optional M5 ATR was not strictly prevented if malformed metadata slipped past validation.

2. **Defect B — Database Test Safety & External Aliasing:**
   - In `backend/database.py:assert_test_database_isolated()`, database isolation verified path containment and canonical equivalence against the default runtime database (`aurum_desk.db`), but did not protect against external database files with arbitrary names hardlinked into a test-owned directory (`st_nlink > 1`).
   - Pre-existing files in test-allocated roots modified prior to marker allocation were not explicitly rejected, creating a hazard of targeting persistent or shared database files.
   - Corrupted or foreign-run ownership markers did not explicitly raise typed errors (`MALFORMED_OWNERSHIP_MARKER`, `FOREIGN_RUN_OWNERSHIP_MARKER`).

3. **Defect C — Exact-Commit Acceptance Verification:**
   - In V17.7.4, the acceptance runner executed against an uncommitted working tree or prior commit (`55538218552179710cbc2a8b6b84d9953088397a`), while the final commit was recorded as `1a9abc0173f179da2d2db071a1eb98e1eb35d34e`.
   - V17.7.4.1 enforces a strict release workflow: implement fixes -> verify targeted tests -> commit final executable code -> run 28-gate acceptance runner against the exact committed code SHA -> publish and verify immutable bundle artifacts -> push to remote.

---

## 2. Source Files Audited

The following authoritative files were audited on `feat/v17-7-adaptive-structural-stop-loss`:

| File Path | Role in Architecture | Audit Findings |
|---|---|---|
| `backend/database.py` | Database connection, test root isolation, marker validation | Inode hardlink detection and marker parsing needed hardening. |
| `backend/services/stop_placement_service.py` | ATR authority validation, buffer calculation, ARM validation | Conditional authority check bypassed on missing counts; cutoff exceptions swallowed. |
| `backend/services/stop_placement_types.py` | Stop placement dataclasses, Enums, buffer results | `StopBufferResult` had unneeded sentinel fallback logic. |
| `backend/services/stop_placement_policy_service.py` | Policy resolution (LEGACY, SHADOW, PAPER_STRUCTURAL) | Invariants intact. PAPER_STRUCTURAL strictly opt-in. |
| `backend/services/cg01_engine.py` | CG01 model orchestration & stop placement | Invariants intact. Preserves causal anchor evidence. |
| `backend/services/scalp_m1_model_service.py` | Scalp M1 model family evaluation & stop placement | Invariants intact. Preserves swing and sweep anchors. |
| `backend/domain_calculator.py` | Authoritative risk, quantity, and liquidation calculator | Distant SL > 4x ATR correctly handled by sizing reduction. No changes needed. |
| `backend/paper_broker.py` | Virtual execution, fill simulation, live order guard | Invariants intact. Zero live orders dispatched. |
| `backend/tests/conftest.py` | Test database bootstrapping, root registration | Trusted test roots registered cleanly. |
| `tools/run_v17_7_acceptance.py` | 28-gate release acceptance runner | G28 documentation verification and exact-commit tracking audited. |
| `tools/artifact_verifier.py` | Run-scoped bundle verification and SHA validation | Verification strictly verifies leaf hashes, counts, and commit SHA. |
| `tools/acceptance_verdict.py` | Pure gate verdict evaluation | All 28 gates required for `ACCEPTED_FOR_PAPER_OPT_IN`. |

---

## 3. Detailed Root Cause Analysis

### 3.1 Defect A: ATR Metadata Incompleteness & Timestamp Handling
In `backend/services/stop_placement_service.py:compute_stop_buffer`:
```python
# PREVIOUS PATTERN:
auth_ok = True
if (
    getattr(atr_m1, "bars_count", 0) > 0
    or getattr(atr_m1, "source_candle_count", 0) > 0
):
    auth_ok, _ = validate_stop_atr_authority(...)
```
When `atr_m1` was provided with `bars_count=None` or `0`, `auth_ok` remained `True`, bypassing full causal provenance checks for `PAPER_STRUCTURAL`.
Furthermore, in `validate_stop_atr_authority`:
```python
# PREVIOUS PATTERN:
try:
    int_cutoff = int(decision_cutoff)
    ...
except (ValueError, TypeError):
    pass
```
Non-numeric decision cutoffs (e.g. `"not_a_time"`, `float('nan')`, or negative values) were either swallowed or passed without triggering an explicit fail-closed reason.

**Fix Applied in V17.7.4.1:**
1. In `validate_stop_atr_authority`:
   - Non-numeric, boolean, `NaN`, infinite, or negative `decision_cutoff` values are immediately rejected with reason `ATR_DECISION_CUTOFF_INVALID`.
   - `decision_cutoff_ms` matching is validated with `ATR_DECISION_CUTOFF_INVALID`.
   - Non-numeric, missing, or negative `max_age_ms` values are rejected with `ATR_TIMESTAMP_MISSING_OR_INVALID`.
   - Missing required timestamps (`last_closed_at_ms`, `calculated_as_of`) fail with `ATR_TIMESTAMP_MISSING_OR_INVALID`.
   - `source_candle_count <= 0` fails with `ATR_SOURCE_COUNT_INSUFFICIENT`.
2. In `compute_stop_buffer`:
   - In `PAPER_STRUCTURAL` mode, full authority validation is unconditionally mandated for M1. If M1 lacks full authority or provenance, buffer calculation fails closed.
   - If M5 is optional and valid, it is incorporated; if invalid, it is rejected and strictly excluded from contaminating the buffer or SL distance.

### 3.2 Defect B: Test Database Isolation & Inode Aliasing
In `backend/database.py:assert_test_database_isolated`:
```python
# PREVIOUS PATTERN:
# Only compared resolved canonical path against aurum_desk.db canonical path
```
If an external database file (e.g. `/path/to/external_prod.db`) was hardlinked into the test root as `/test_root/test.db`, their canonical paths differed, but their underlying inode was identical. A destructive fixture modifying the test database would corrupt the external database.

**Fix Applied in V17.7.4.1:**
1. In `assert_test_database_isolated`:
   - Check `t_stat.st_nlink > 1`: If the database file has multiple hardlinks, it is rejected immediately with `HARDLINK_ALIAS_DETECTED`.
   - Check `t_stat.st_mtime < marker_created - 0.5`: If the file was modified before the test root was allocated and marked, it is rejected as a pre-existing untrusted database.
2. In `_read_and_validate_marker`:
   - Enforce JSON dictionary structure, schema validation, and matching `AURUM_TEST_RUN_ID`.
   - Corrupted or invalid markers raise `MALFORMED_OWNERSHIP_MARKER`.
   - Mismatched run IDs raise `FOREIGN_RUN_OWNERSHIP_MARKER`.

### 3.3 Defect C: Exact-Commit Verification
V17.7.4 acceptance artifacts reported results for a commit different from the final reviewed commit.
**Fix Applied in V17.7.4.1:**
- All code and test repairs are staged and committed first.
- The 28-gate acceptance runner is executed against that exact committed commit SHA.
- The resulting immutable artifacts and reports record `tested_commit_sha == final_code_sha`.

---

## 4. Preservation of Critical Trading Invariants

1. **Distant SL > 4x ATR:**
   - Confirmed that Stop Loss distance > 4x ATR remains fully valid and `ELIGIBLE_FOR_PAPER` when structural anchor causality and financial budget (max risk, margin, liquidation) are satisfied.
   - Position sizing automatically reduces quantity to keep total risk at or below the risk budget.
2. **Zero Live Orders:**
   - Virtual/paper broker paths remain isolated with zero exchange API dispatch attempts (`LIVE_ORDER_DISPATCH_ATTEMPTS == 0`).
3. **No Strategy or Sizing Alterations:**
   - SMC/ICT setups (sweep, FVG, MSS), entry guards, and risk calculations in `backend/domain_calculator.py` remain untouched.
