# AURUM DESK V17.7.4 — SOURCE AND ROOT CAUSE AUDIT
**Final ATR Authority, Test DB Ownership & Acceptance Artifact Integrity**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Reference HEAD:** `40f52d96f02b08ceaae6a2cfc9ff2673960dd949`

---

## 1. Executive Summary & Objective

AURUM DESK V17.7.4 is an engineering implementation release addressing foundational soundness issues across three mission-critical architectural boundaries:
1. **Contract A: Final ATR Authority:** Eliminating semantic violations where weighted buffer components or unverified loose scalars acted as authoritative ATR in structural stop-loss calculations.
2. **Contract B: Test Database Ownership & Isolation:** Enforcing run-scoped database ownership with active bind validation across SQLAlchemy engines and sessions to prevent test mutation of live/runtime data.
3. **Contract C: Acceptance Artifact Integrity & Truthfulness:** Establishing verifiable, non-forgeable release artifacts tied strictly to executed subprocesses, JUnit XML structured reports, and deterministic SHA-256 source manifests.

---

## 2. Source Audit & Root Cause Analysis

### 2.1 Contract A: Final ATR Authority Gaps

#### Root Cause 1: Weighted Component Promoted to Raw ATR in `StopBufferResult`
- **Location:** `backend/services/stop_placement_types.py::StopBufferResult.__post_init__()`
- **Mechanism:** In prior versions, `StopBufferResult.__post_init__()` checked if `raw_atr_m1` was unset and populated it using `self.components.get("atr_m1")`. However, `components` holds weighted values (e.g., $k_{\text{m1}} \times \text{ATR}_{\text{raw}} = 0.25 \times 3.0 = 0.75$).
- **Hazard:** Downstream calculations in `plan_structural_stop` (distance/ATR ratios, volatility gates) received $0.75$ instead of $3.00$, distorting ratio classifications and enabling unverified buffer contributions to masquerade as authoritative raw market volatility.
- **Fix:** Removed all fallback from `components` to `raw_atr_m1` and `raw_atr_m5`. When raw evidence is omitted, fields remain strictly `None`. Reverse-calculation by dividing by multipliers is expressly prohibited.

#### Root Cause 2: Fragmented ATR Validation & Scalar Overrides
- **Location:** `backend/services/stop_placement_service.py::plan_structural_stop`
- **Mechanism:** The planner previously fell back from object to loose scalar `atr_m1_val` and then to `buffer_result.raw_atr_m1`. In `PAPER_STRUCTURAL` mode, a synthetic or UNAVAILABLE object could be bypassed by passing a loose scalar.
- **Hazard:** Broken provenance: trades could execute with arbitrary scalar ATR values lacking timeframe, symbol, causal timestamp, or candle count verification.
- **Fix:** Introduced authoritative validator `validate_stop_atr_authority(...)`. In `PAPER_STRUCTURAL`, authority resolution occurs once at Step 9b. Missing or invalid M1 authority fails closed with `status = "WAITING_VOLATILITY_EVIDENCE"`, `selected_sl = None`, and detailed reason codes.

#### Root Cause 3: Truthiness Bug in Candle Closure Detection
- **Location:** `backend/services/stop_placement_service.py::compute_stop_atr`
- **Mechanism:** Code evaluated `getattr(bar, "is_closed", None) or ...`. When `is_closed` was explicitly `False`, the `or` operator treated `False` as falsy and evaluated fallback expressions, inadvertently including open candles.
- **Fix:** Implemented explicit boolean check: if `is_closed` is explicitly `False`, the candle is skipped immediately.

---

### 2.2 Contract B: Test Database Ownership & Safety Isolation

#### Root Cause 1: Unverified Marker Trust (`.aurum_test_owned`)
- **Location:** `backend/database.py::get_or_create_test_root`
- **Mechanism:** Any directory containing a file named `.aurum_test_owned` was unconditionally trusted, even if created by a previous, stale run or an external actor.
- **Hazard:** Accidental directory sharing between tests, stale test runs pointing to host directories, or misconfigured paths could lead to destructive operations (`Base.metadata.drop_all`) on unintended locations.
- **Fix:** Upgraded marker contract to Schema Version 1. Markers now encapsulate JSON metadata: `schema_version`, `run_id`, `created_at_utc`, `canonical_root`, and `framework_owner`. The database bootstrap strictly validates that the marker belongs to the active `CURRENT_RUN_ID`.

#### Root Cause 2: Inspection of Global `DB_PATH` Rather Than Actual Session Bind
- **Location:** `backend/database.py::assert_test_database_isolated`
- **Mechanism:** Isolation guards inspected the global variable `DB_PATH`. However, SQLAlchemy sessions and engines can be bound to arbitrary URLs independently of `DB_PATH`.
- **Hazard:** A test creating a local engine pointing to `aurum_desk.db` would pass the guard because global `DB_PATH` pointed to a test temp dir.
- **Fix:** Added `assert_test_database_isolated(bind=...)` accepting `Engine`, `Session`, or path. Implemented `normalize_sqlite_database_target` to parse SQLite URLs (`sqlite:///`), file URIs (`file://`), and native paths into canonical filesystem locations before verifying containment within trusted test roots.

#### Root Cause 3: Premature Module Import Execution
- **Location:** `backend/database.py` (top-level migration execution)
- **Mechanism:** `run_schema_migrations(engine)` was called at module import time, before `conftest.py` fixtures had configured isolated environments.
- **Fix:** Guarded migration execution with `AURUM_SKIP_STARTUP_MIGRATIONS` and reinforced bootstrap in `conftest.py` prior to `models` and `database` imports.

---

### 2.3 Contract C: Acceptance Artifact Integrity & Truthfulness

#### Root Cause 1: Overwriting Historical Artifacts
- **Location:** `tools/run_v17_7_acceptance.py`
- **Mechanism:** Previous runners overwrote `docs/artifacts/v17_7_2_acceptance_results.json` and `v17_7_3_acceptance_results.json`.
- **Hazard:** Loss of historical provenance, inability to independently audit prior releases.
- **Fix:** All V17.7.4 acceptance runs write exclusively to isolated run directories: `docs/artifacts/v17_7_4/<run_id>/`. Historical artifacts are preserved intact.

#### Root Cause 2: Fragile Regex Stdout Parsing
- **Location:** `tools/run_v17_7_acceptance.py::parse_pytest_counts`
- **Mechanism:** Parsing `N passed in X.XXs` via regex from terminal stdout. If stdout formatting changed or errors were truncated, counts were misreported.
- **Fix:** Enforced structured JUnit XML reporting (`--junitxml=<report.xml>`) parsed via `xml.etree.ElementTree`. Extracted exact test counts (`passed`, `failed`, `errors`, `skipped`) from XML testsuite headers.

#### Root Cause 3: Tautological Self-Verification Tests
- **Location:** `backend/tests/test_v17_7_adaptive_stop_loss.py::test_ce_20`
- **Mechanism:** In previous versions, `test_ce_20` built a mock dictionary with `tested_source_sha = expected_sha` and asserted equality on its own dict.
- **Fix:** Replaced with an end-to-end verifier (`tools/artifact_verifier.py`) that hashes disk files using SHA-256, verifies source manifests against git worktree, and validates negative controls (tampered SHA, missing gate, malformed JSON).

---

## 3. Findings Classification Summary

| Category | Finding ID | Location | Status | Verified Proof |
| :--- | :--- | :--- | :--- | :--- |
| **ATR Authority** | DEF-1774-01 | `stop_placement_types.py` | Fixed | `test_atr_01_omitted_raw_fields_remain_none` |
| **ATR Authority** | DEF-1774-02 | `stop_placement_service.py` | Fixed | `validate_stop_atr_authority(...)` suite (A01–A24) |
| **ATR Authority** | DEF-1774-03 | `scalp_m1_model_service.py` | Fixed | `test_atr_18_m1_strategy_uses_verified_atr` |
| **ATR Authority** | DEF-1774-04 | `scalp_watcher_orchestrator.py` | Fixed | `test_atr_20_paper_structural_cannot_arm_with_invalid_provenance` |
| **DB Safety** | DEF-1774-05 | `database.py` | Fixed | `test_dbs_02_valid_metadata_marker_trusted` |
| **DB Safety** | DEF-1774-06 | `database.py` | Fixed | `test_dbs_14_destructive_fixture_refuses_unsafe_target` |
| **DB Safety** | DEF-1774-07 | `conftest.py` | Fixed | Deterministic sentinel verification (`guard_runtime_database`) |
| **Acceptance** | DEF-1774-08 | `acceptance_verdict.py` | Fixed | `test_ce_19_acceptance_runner_refuses_pass_on_missing_gates` |
| **Acceptance** | DEF-1774-09 | `artifact_verifier.py` | Fixed | `test_ce_20_acceptance_artifact_source_sha_matches_tested_source` |
| **Acceptance** | DEF-1774-10 | `run_v17_7_acceptance.py` | Fixed | Run-scoped isolation under `docs/artifacts/v17_7_4/<run_id>/` |

---
*Signed and Approved for Production Paper Opt-In Evaluation.*
