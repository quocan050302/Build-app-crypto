# AURUM DESK V17.7.3 — DATABASE SAFETY ISOLATION
**Guaranteed Test Database Isolation, Sentinel Preservation & Migration Guard**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Status:** `VERIFIED_FROM_DATABASE_ISOLATION_TEST`

---

## 1. Architectural Overview & Threat Model

In automated trading systems, running destructive test operations (such as table truncation, schema wiping, or dropping data) against an application database can destroy historical trade records, corrupt stateful journal entries, or trigger erroneous automated recovery routines.

Prior to V17.7.3, test isolation relied on checking whether the target filename was `aurum_desk.db`. This was fundamentally insufficient:
- An environment variable like `AURUM_DB_PATH=/path/to/trading_copy.db` or `AURUM_TEST_DB_PATH=/data/backup.db` bypassed the check.
- Symbolic links, relative path traversals (`../../aurum_desk.db`), and `sqlite:////absolute/path` URI schemes could disguise target databases.
- The presence of `TESTING=1` could not prevent destructive fixtures from targeting an arbitrary database file.

### V17.7.3 Isolation Invariant:
A test database is considered isolated **if and only if** its fully resolved canonical filesystem path is strictly contained within a designated, verified test-owned root directory that contains a valid `.aurum_test_owned` metadata marker. Any path failing this invariant causes immediate fail-closed termination with `RuntimeError`.

---

## 2. Test-Owned Root Bootstrap Architecture

```
Operating System Temp Directory (/var/folders/.../T or /tmp)
 │
 ├── aurum_test_root_unique/  [Created by get_or_create_test_root()]
 │    ├── .aurum_test_owned   [Metadata marker with created_at, pid]
 │    ├── isolated_test.db    <-- ALLOWED: Contained within root
 │    └── subdirs/*.db        <-- ALLOWED: Contained within root
 │
 ├── pytest-basetemp/         [Registered via register_trusted_test_root()]
 │    ├── .aurum_test_owned   [Metadata marker registered by conftest.py]
 │    └── test_run_*.db       <-- ALLOWED: Contained within registered basetemp
 │
 └── arbitrary_path.db        <-- REJECTED: Outside test root (FAIL-CLOSED)
```

### Key Components in `backend/database.py`:
1. **`TEST_ROOT_MARKER = ".aurum_test_owned"`**:
   Guarantees that a directory cannot be mistaken for a test root unless it contains an explicit marker written by the test process.
2. **`get_or_create_test_root() -> Path`**:
   Creates a canonical unique directory under OS temporary storage (e.g., `aurum_test_root_XXXXXX/`) and initializes the `.aurum_test_owned` marker file containing the creation timestamp and process ID.
3. **`register_trusted_test_root(path: Path)`**:
   Allows pytest session-level test roots (e.g., pytest's `basetemp`) to be safely registered with the isolation system after verifying or writing the `.aurum_test_owned` marker.
4. **`assert_test_database_isolated(db_path: str = None) -> bool`**:
   - Resolves target path, peeling off `sqlite:///` and `file://` prefixes.
   - Rejects exact match with `RUNTIME_DEFAULT_DB_PATH`.
   - Rejects any file whose name contains `aurum_desk.db`.
   - Verifies that `t_path.is_relative_to(root)` holds for at least one active, trusted test root that contains `.aurum_test_owned`.
   - Rejects symlinks pointing outside the root.
   - Rejects relative path traversals that resolve outside the root.
   - Raises `RuntimeError("CRITICAL SAFETY HAZARD: ...")` on any violation.
5. **`run_schema_migrations(target_engine=None)`**:
   Extracts the underlying database file from `target_engine` or default engine and calls `assert_test_database_isolated()` before executing any DDL or migration scripts.

---

## 3. Negative & Positive Verification Test Suite (DBS-01 to DBS-15)

The entire isolation subsystem is verified by 15 dedicated tests in `backend/tests/test_v17_7_database_safety.py`:

| Test ID | Test Function | Description | Result |
|---|---|---|---|
| `DBS-01` | `test_dbs_01_default_isolated_test_path_succeeds` | Confirms default database in test-owned root passes isolation check. | **PASS** |
| `DBS-02` | `test_dbs_02_explicit_runtime_path_rejected` | Confirms pointing to `RUNTIME_DEFAULT_DB_PATH` is unconditionally rejected. | **PASS** |
| `DBS-03` | `test_dbs_03_arbitrary_existing_db_path_rejected` | Confirms arbitrary external `.db` outside test root is rejected. | **PASS** |
| `DBS-04` | `test_dbs_04_existing_file_named_my_test_db_rejected` | Confirms filename `my_test.db` outside test root is rejected (anti-spoofing). | **PASS** |
| `DBS-05` | `test_dbs_05_symbolic_link_to_unsafe_db_rejected` | Confirms symlink inside root pointing to external file is resolved and rejected. | **PASS** |
| `DBS-06` | `test_dbs_06_relative_traversal_outside_test_root_rejected` | Confirms `root/../external.db` traversal is resolved and rejected. | **PASS** |
| `DBS-07` | `test_dbs_07_file_uri_alias_to_unsafe_db_rejected` | Confirms `sqlite:///external.db` and `file://external.db` are rejected. | **PASS** |
| `DBS-08` | `test_dbs_08_unsafe_inherited_env_var_rejected` | Confirms inherited `AURUM_DB_PATH` pointing to external file is rejected. | **PASS** |
| `DBS-09` | `test_dbs_09_test_bootstrap_before_engine_binding` | Confirms test root creation occurs before SQLAlchemy engine binds. | **PASS** |
| `DBS-10` | `test_dbs_10_sentinel_database_remains_unchanged` | Byte-for-byte SHA256 proof that external sentinel database is untouched. | **PASS** |
| `DBS-11` | `test_dbs_11_concurrent_test_workers_use_isolated_databases` | Confirms multiple workers obtain independent database files inside root. | **PASS** |
| `DBS-12` | `test_dbs_12_independent_sessions_share_intentionally_owned_test_db`| Confirms concurrent sessions can intentionally share one test-owned DB. | **PASS** |
| `DBS-13` | `test_dbs_13_migration_refuses_unsafe_target` | Confirms `run_schema_migrations` refuses unsafe target with `RuntimeError`. | **PASS** |
| `DBS-14` | `test_dbs_14_destructive_fixture_refuses_unsafe_target` | Confirms destructive SQL fixture fails closed if target is unsafe. | **PASS** |
| `DBS-15` | `test_dbs_15_normal_production_database_config_unimpaired` | Confirms non-test runtime environment path resolution is unimpaired. | **PASS** |

### Execution Evidence:
```bash
./backend/venv/bin/python -m pytest backend/tests/test_v17_7_database_safety.py -v
============================= test session starts ==============================
collected 15 items

backend/tests/test_v17_7_database_safety.py::test_dbs_01_default_isolated_test_path_succeeds PASSED [  6%]
backend/tests/test_v17_7_database_safety.py::test_dbs_02_explicit_runtime_path_rejected PASSED [ 13%]
backend/tests/test_v17_7_database_safety.py::test_dbs_03_arbitrary_existing_db_path_rejected PASSED [ 20%]
backend/tests/test_v17_7_database_safety.py::test_dbs_04_existing_file_named_my_test_db_rejected PASSED [ 26%]
backend/tests/test_v17_7_database_safety.py::test_dbs_05_symbolic_link_to_unsafe_db_rejected PASSED [ 33%]
backend/tests/test_v17_7_database_safety.py::test_dbs_06_relative_traversal_outside_test_root_rejected PASSED [ 40%]
backend/tests/test_v17_7_database_safety.py::test_dbs_07_file_uri_alias_to_unsafe_db_rejected PASSED [ 46%]
backend/tests/test_v17_7_database_safety.py::test_dbs_08_unsafe_inherited_env_var_rejected PASSED [ 53%]
backend/tests/test_v17_7_database_safety.py::test_dbs_09_test_bootstrap_before_engine_binding PASSED [ 60%]
backend/tests/test_v17_7_database_safety.py::test_dbs_10_sentinel_database_remains_unchanged PASSED [ 66%]
backend/tests/test_v17_7_database_safety.py::test_dbs_11_concurrent_test_workers_use_isolated_databases PASSED [ 73%]
backend/tests/test_v17_7_database_safety.py::test_dbs_12_independent_sessions_share_intentionally_owned_test_db PASSED [ 80%]
backend/tests/test_v17_7_database_safety.py::test_dbs_13_migration_refuses_unsafe_target PASSED [ 86%]
backend/tests/test_v17_7_database_safety.py::test_dbs_14_destructive_fixture_refuses_unsafe_target PASSED [ 93%]
backend/tests/test_v17_7_database_safety.py::test_dbs_15_normal_production_database_config_unimpaired PASSED [100%]

============================== 15 passed in 0.63s ==============================
```

---

## 4. Sentinel Database Preservation Proof

In `DBS-10`:
1. A realistic SQLite database is created in an external temp directory outside the test root.
2. An initial table `sentinel_records` with critical records is inserted and committed.
3. The initial SHA256 checksum of the file is recorded:
   `initial_hash = hashlib.sha256(sentinel_file.read_bytes()).hexdigest()`
4. An attempt is made to call `assert_test_database_isolated(str(sentinel_file))` and `run_schema_migrations(target_engine=engine)`.
5. Both calls raise `RuntimeError("CRITICAL SAFETY HAZARD: ...")`.
6. The post-execution SHA256 checksum is computed:
   `post_hash = hashlib.sha256(sentinel_file.read_bytes()).hexdigest()`
7. An assertion confirms `initial_hash == post_hash` and the rows remain byte-for-byte identical.
