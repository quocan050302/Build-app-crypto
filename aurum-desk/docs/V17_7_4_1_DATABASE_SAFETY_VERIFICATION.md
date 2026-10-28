# AURUM DESK V17.7.4.1 — DATABASE SAFETY & ALIAS PROTECTION REPORT

**Release Version:** V17.7.4.1  
**Target Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Primary Module:** `backend/database.py`, `backend/tests/conftest.py`  
**Test Suite:** `backend/tests/test_v17_7_database_safety.py`  
**Status:** IMPLEMENTED & VERIFIED (30/30 PASS, 12/12 DB-FINAL PASS)  

---

## 1. Safety Objectives & Remaining Alias Risks

The testing harness executes destructive operations (table drop, recreation, vacuum, truncate) to ensure clean test state. The database isolation architecture ensures:
1. Production and runtime databases (`aurum_desk.db`) are never targeted by tests.
2. Every test database resides in a designated, test-owned directory with an active `.aurum_test_owned` marker.
3. Inode-level aliases (symlinks and hardlinks) cannot bypass path containment.
4. Pre-existing untrusted files inside test-allocated directories are rejected.

### Residual Risks Addressed in V17.7.4.1
- **Hardlink Aliases from External Databases:** Previously, canonical path checks prevented targeting the canonical path of `aurum_desk.db`. However, an external database with an arbitrary name hardlinked into the test root shared the same inode (`st_nlink > 1`), exposing external data to test mutations.
- **Pre-existing File Reuse:** Files created before the test root marker was written could be targeted if leftover from prior operations.
- **Marker Validation:** Marker file corruption, missing fields, or run ID mismatches required explicit, typed fail-closed errors.

---

## 2. Hardened Safeguards in `backend/database.py`

### 2.1 Marker Schema & Run ID Enforcement
In `_read_and_validate_marker()`:
```python
def _read_and_validate_marker(marker_path: Path) -> dict:
    if not marker_path.is_file():
        raise RuntimeError("TEST_DATABASE_ROOT_MARKER_MISSING: Missing .aurum_test_owned marker")
    try:
        content = marker_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception:
        raise RuntimeError("MALFORMED_OWNERSHIP_MARKER: Corrupted marker content")
    
    if not isinstance(data, dict) or not data.get("test_run_id") or not data.get("test_root"):
        raise RuntimeError("MALFORMED_OWNERSHIP_MARKER: Missing required fields")
    
    expected_run_id = os.environ.get("AURUM_TEST_RUN_ID")
    if expected_run_id and data.get("test_run_id") != expected_run_id:
        raise RuntimeError("FOREIGN_RUN_OWNERSHIP_MARKER: Run ID mismatch")
    return data
```

### 2.2 Hardlink Alias & Pre-existing File Rejection
In `assert_test_database_isolated()`:
```python
# Hardlink alias detection: Link count > 1 means the inode is shared with another path
if t_stat.st_nlink > 1:
    raise RuntimeError(f"HARDLINK_ALIAS_DETECTED: Target has link count {t_stat.st_nlink}")

# Pre-existing file detection: Target cannot be older than the test root ownership marker
marker_stat = marker_file.stat()
if t_stat.st_mtime < marker_stat.st_ctime - 0.5:
    raise RuntimeError("PRE_EXISTING_EXTERNAL_FILE_REJECTED: Target modified before marker allocation")
```

---

## 3. Verification & Test Evidence

The following 12 regression tests (`DB-FINAL-01` through `DB-FINAL-12`) were added to `backend/tests/test_v17_7_database_safety.py` and all pass:

| Test ID | Test Function | Verified Behavior | Status |
|---|---|---|---|
| `DB-FINAL-01` | `test_db_final_01_fresh_test_owned_database_accepted` | Newly allocated test-owned database passes isolation assertion | PASS |
| `DB-FINAL-02` | `test_db_final_02_runtime_db_path_rejected` | Runtime database path `aurum_desk.db` rejected immediately | PASS |
| `DB-FINAL-03` | `test_db_final_03_arbitrary_external_db_path_rejected` | External database outside test root rejected with `UNTRUSTED_TEST_DB_PATH` | PASS |
| `DB-FINAL-04` | `test_db_final_04_symlink_outside_test_root_rejected` | Symlink pointing outside test root rejected | PASS |
| `DB-FINAL-05` | `test_db_final_05_hardlink_alias_to_external_sentinel_rejected` | Hardlink alias to external sentinel rejected with `HARDLINK_ALIAS_DETECTED` | PASS |
| `DB-FINAL-06` | `test_db_final_06_pre_existing_external_db_rejected` | File older than root marker rejected with `PRE_EXISTING_EXTERNAL_FILE_REJECTED` | PASS |
| `DB-FINAL-07` | `test_db_final_07_malformed_ownership_marker_rejected` | Corrupted JSON marker rejected with `MALFORMED_OWNERSHIP_MARKER` | PASS |
| `DB-FINAL-08` | `test_db_final_08_foreign_run_ownership_marker_rejected` | Marker from foreign run rejected with `FOREIGN_RUN_OWNERSHIP_MARKER` | PASS |
| `DB-FINAL-09` | `test_db_final_09_destructive_fixture_cannot_target_external_db` | Fixture attempting to drop tables on external sentinel fails closed | PASS |
| `DB-FINAL-10` | `test_db_final_10_runner_subprocess_uses_test_owned_db` | Subprocess executes safely under test-owned root | PASS |
| `DB-FINAL-11` | `test_db_final_11_production_configuration_remains_unchanged` | When `TESTING` is unset, default production DB path is unchanged | PASS |
| `DB-FINAL-12` | `test_db_final_12_sentinel_records_remain_unchanged` | External sentinel database records remain bitwise intact after test run | PASS |

Total Database Safety suite result: **30 passed** (18 original safety tests + 12 DB-FINAL tests).
