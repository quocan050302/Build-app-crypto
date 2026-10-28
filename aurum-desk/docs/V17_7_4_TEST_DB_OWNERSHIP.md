# AURUM DESK V17.7.4 — TEST DATABASE OWNERSHIP & ISOLATION
**Run-Scoped Provenance, Active Bind Inspection & Fail-Closed Safety Guards**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Reference HEAD:** `40f52d96f02b08ceaae6a2cfc9ff2673960dd949`

---

## 1. Threat Model & Architectural Principles

Automated test suites frequently execute destructive operations—such as `Base.metadata.drop_all()`, table truncations, and schema migrations. In an environment where a live trading or paper engine may be running concurrently on the same host, relying on loose directory naming conventions or unverified markers introduces catastrophic risks of data corruption.

### 1.1 Defined Threat Model
- **Misconfiguration Risk:** Environment variables (e.g., `AURUM_DB_PATH`) inherited from host shells or previous manual runs pointing to live production databases (`aurum_desk.db`).
- **Stale Context Hazard:** Reusing temporary directories from crashed or past test runs containing outdated markers.
- **Escape & Aliasing Attacks:** Path traversal (`../`), symlink/junction escapes, hardlink aliases, and URL/URI variations (`sqlite:///`, `file://`) designed to bypass string-based containment checks.
- **Bind Disconnect:** A test creating a local SQLAlchemy `Engine` or `Session` targeting a live database while global configuration points to a test directory.

*Note on Security Boundary:* The ownership marker is designed as a safety guard against accidental misconfiguration and test interference; it does not claim to defend against hostile, arbitrary code running with identical OS user privileges.

---

## 2. Run-Scoped Ownership Contract (Schema Version 1)

In V17.7.4, the presence of a file named `.aurum_test_owned` alone is **insufficient** to prove ownership. Test roots must encapsulate verified metadata matching the active execution run.

### 2.1 Marker Metadata Specification
When a test root is allocated, a JSON marker `.aurum_test_owned` is atomically created:

```json
{
  "schema_version": 1,
  "run_id": "test_run_1793066492525_31760",
  "created_at_utc": "2026-10-27T02:01:32.525000+00:00",
  "canonical_root": "C:\\Users\\ADMIN\\AppData\\Local\\Temp\\aurum_test_root_1793066492525_31760",
  "framework_owner": "pytest"
}
```

### 2.2 Ownership Validation Rules
1. **Schema Check:** Marker must contain `"schema_version": 1`. Unversioned or malformed markers are rejected immediately with `RuntimeError("UNSUPPORTED_MARKER_SCHEMA")`.
2. **Run Identity Match:** The `run_id` in the marker must match `CURRENT_RUN_ID` of the active test session. Stale markers from prior runs cause fail-closed rejection.
3. **Canonical Containment:** The directory must not be a filesystem root (`C:\`, `/`), user home directory, backend source directory, or repository root.
4. **Symlink Escape Prevention:** Canonicalization via `Path.resolve()` verifies that symlinks or Windows junctions do not point outside the designated test root.

---

## 3. Path Normalization (`normalize_sqlite_database_target`)

SQLAlchemy engines and SQLite drivers accept diverse URL and URI syntaxes. V17.7.4 introduces `normalize_sqlite_database_target` to canonicalize all targets before ownership evaluation.

| Input Syntax | Classification | Normalization & Security Handling |
| :--- | :--- | :--- |
| `sqlite:///:memory:` | In-Memory | Recognized as safe isolated memory instance; bypassed from filesystem checks. |
| `sqlite:///C:/path/to/test.db` | SQLAlchemy URL | URL-parsed via `make_url()`; path extracted, percent-decoded, and resolved. |
| `file:///C:/path/to/test.db` | SQLite URI | Parsed via `urllib.parse.urlparse` and `url2pathname`; query params stripped. |
| `C:\path\to\test.db` | Native Path | Resolved to canonical Windows absolute path with case normalization. |
| `http://...` / `postgresql://...` | Unsupported | Rejected with `CRITICAL SAFETY HAZARD: UNSUPPORTED_SCHEME`. |

### 3.1 Hardlink Alias Detection
On operating systems and filesystems supporting hardlinks (e.g. NTFS), multiple directory entries can share the same inode. Standard `Path.resolve()` does not detect hardlinks. `normalize_sqlite_database_target` compares `(st_dev, st_ino)` against known protected databases (`aurum_desk.db`), preventing stealth mutations via hardlinked paths.

---

## 4. Active Bind Isolation Guard (`assert_test_database_isolated`)

Global variable checking is replaced with **active bind inspection**:
```python
def assert_test_database_isolated(target: Any = None) -> None:
    """
    Validates that the given target (Engine, Session, connection URL, or Path)
    resolves to an isolated, owned test database. Fails closed with RuntimeError.
    """
```
- When an `Engine` is passed, the engine's URL is extracted and validated.
- When a `Session` is passed, the session's active bind engine is extracted and validated.
- Preflight guards in `run_schema_migrations`, `clean_v17_7_db`, and destructive test fixtures verify the active bind before executing DDL or `DELETE` statements.

---

## 5. Runtime Sentinel & Concurrency Protection

### 5.1 Conftest Bootstrap Ordering
`backend/tests/conftest.py` executes environment bootstrap at the top level before importing `models`, `database`, or `main`:
1. If `AURUM_TEST_ROOT` is set, its ownership is verified.
2. If unset, a unique run-scoped root is allocated under OS temp.
3. Unsafe inherited configurations (e.g. `AURUM_DB_PATH` pointing to live DB) raise an immediate fatal exception before pytest collects tests.
4. `AURUM_SKIP_STARTUP_MIGRATIONS = "1"` prevents module-level migrations from executing during import.

### 5.2 Deterministic Sentinel Verification
In `guard_runtime_database`, a dedicated sentinel database is instantiated to prove non-mutation. For live environments where an external application process may legitimately log to `backend/aurum_desk.db`:
- The guard inspects file existence, inode identity (`st_ino`), non-zero size, and recent activity timestamps.
- If concurrent legitimate writes occur, the guard avoids fragile byte-equality claims on the live database, relying on the dedicated sentinel to provide deterministic proof of zero test leakage.

---
*Signed and Approved for Production Paper Opt-In Evaluation.*
