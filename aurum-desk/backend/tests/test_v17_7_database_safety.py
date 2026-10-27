"""
backend/tests/test_v17_7_database_safety.py
AURUM DESK V17.7.3 — Database Safety Isolation Test Suite
Verifies DBS-01 through DBS-15:
Strict test-owned root containment, fail-closed guards, rejection of arbitrary paths,
symlink/traversal defenses, sentinel database preservation, and production runtime non-regression.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import (
    assert_test_database_isolated,
    get_or_create_test_root,
    run_schema_migrations,
    TEST_ROOT_MARKER,
    RUNTIME_DEFAULT_DB_PATH,
    DB_PATH,
)


def test_dbs_01_default_isolated_test_path_succeeds():
    """DBS-01: Default isolated test path within verified test-owned root succeeds."""
    test_root = get_or_create_test_root()
    test_db = str(test_root / "dbs01_test.db")
    assert assert_test_database_isolated(test_db) is True


def test_dbs_02_explicit_runtime_database_path_rejected():
    """DBS-02: Explicit runtime database path (aurum_desk.db) is rejected with RuntimeError."""
    with pytest.raises(RuntimeError) as exc:
        assert_test_database_isolated(RUNTIME_DEFAULT_DB_PATH)
    assert "CRITICAL SAFETY HAZARD" in str(exc.value)
    assert "targets runtime database" in str(exc.value)


def test_dbs_03_arbitrary_existing_db_path_rejected():
    """DBS-03: Arbitrary existing external .db path outside test root is rejected."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        external_db = tmp.name
    try:
        with pytest.raises(RuntimeError) as exc:
            assert_test_database_isolated(external_db)
        assert "CRITICAL SAFETY HAZARD" in str(exc.value)
        assert "not within verified test-owned root" in str(exc.value)
    finally:
        if os.path.exists(external_db):
            os.remove(external_db)


def test_dbs_04_existing_file_named_my_test_db_rejected():
    """DBS-04: Existing file named my_test.db outside test root is rejected regardless of filename (V17.7.4 Section 12.8)."""
    with tempfile.NamedTemporaryFile(prefix="my_test_", suffix=".db", delete=False) as f:
        my_test_db = f.name
    try:
        # A file with 'test' in its name must NOT be trusted if it is outside test-owned root
        test_root = get_or_create_test_root()
        if Path(my_test_db).resolve() != test_root.resolve():
            with pytest.raises(RuntimeError) as exc:
                assert_test_database_isolated(my_test_db)
            assert "CRITICAL SAFETY HAZARD" in str(exc.value)
    finally:
        if os.path.exists(my_test_db):
            os.remove(my_test_db)


def test_dbs_05_symbolic_link_to_unsafe_database_rejected():
    """DBS-05: Symbolic link inside test root pointing to unsafe database is rejected (V17.7.4 Section 12.5)."""
    test_root = get_or_create_test_root()
    with tempfile.NamedTemporaryFile(suffix="_external_target.db", delete=False) as ext_f:
        external_target = ext_f.name

    symlink_path = test_root / "symlink_to_external.db"
    try:
        if symlink_path.exists():
            symlink_path.unlink()
        try:
            symlink_path.symlink_to(external_target)
        except OSError as e:
            if getattr(e, "winerror", None) == 1314 or "privilege" in str(e).lower():
                pytest.skip("Symlink creation requires elevated privilege on Windows (WinError 1314)")
            raise

        # Resolving the symlink points outside test root, so it must be rejected!
        with pytest.raises(RuntimeError) as exc:
            assert_test_database_isolated(str(symlink_path))
        assert "CRITICAL SAFETY HAZARD" in str(exc.value)
        assert "not within verified test-owned root" in str(exc.value)
    finally:
        if symlink_path.exists():
            symlink_path.unlink()
        if os.path.exists(external_target):
            os.remove(external_target)


def test_dbs_05b_hardlink_to_runtime_database_rejected():
    """DBS-05B: Hardlink inside test root pointing to runtime database is rejected (V17.7.4 Section 08.3 / B06)."""
    r_path = Path(RUNTIME_DEFAULT_DB_PATH).resolve()
    if not r_path.exists():
        pytest.skip("Runtime default database does not exist to create hardlink from.")
    test_root = get_or_create_test_root()
    hardlink_path = test_root / "hardlink_to_runtime.db"
    if hardlink_path.exists():
        hardlink_path.unlink()
    try:
        try:
            os.link(str(r_path), str(hardlink_path))
        except OSError as e:
            pytest.skip(f"Hardlink creation not supported across filesystems/volumes: {e}")
        with pytest.raises(RuntimeError) as exc:
            assert_test_database_isolated(str(hardlink_path))
        assert "CRITICAL SAFETY HAZARD" in str(exc.value)
        assert "hardlink" in str(exc.value).lower()
    finally:
        if hardlink_path.exists():
            hardlink_path.unlink()


def test_dbs_06_relative_traversal_outside_test_root_rejected():
    """DBS-06: Relative path traversal (../) escaping test root is rejected."""
    test_root = get_or_create_test_root()
    traversal_path = str(test_root / ".." / "escaped_external.db")
    with pytest.raises(RuntimeError) as exc:
        assert_test_database_isolated(traversal_path)
    assert "CRITICAL SAFETY HAZARD" in str(exc.value)
    assert "not within verified test-owned root" in str(exc.value)


def test_dbs_07_file_uri_alias_to_unsafe_database_rejected():
    """DBS-07: File URI aliases (file:// and sqlite:///) to unsafe databases are stripped and rejected."""
    uri_1 = "file:///tmp/unsafe_external_target.db"
    uri_2 = "sqlite:////tmp/unsafe_external_target.db"

    for uri in (uri_1, uri_2):
        with pytest.raises(RuntimeError) as exc:
            assert_test_database_isolated(uri)
        assert "CRITICAL SAFETY HAZARD" in str(exc.value)


def test_dbs_08_unsafe_inherited_aurum_db_path_rejected():
    """DBS-08: Subprocess with unsafe inherited AURUM_DB_PATH fails closed at startup (V17.7.4 Section 12.6)."""
    backend_dir = str(Path(__file__).resolve().parent.parent)
    env = os.environ.copy()
    env["AURUM_DB_PATH"] = "/tmp/deliberately_unsafe_external_override.db"
    env["PYTHONPATH"] = os.pathsep.join([backend_dir, f"{backend_dir}/tests"])
    env["TESTING"] = "1"

    cmd = [sys.executable, "-c", "import database; print('BIND_SUCCESS')"]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode != 0
    assert "CRITICAL SAFETY HAZARD" in res.stderr


def test_dbs_09_test_bootstrap_occurs_before_engine_binding():
    """DBS-09: Test bootstrap binds DB_PATH within verified test root before any operations occur."""
    test_root = get_or_create_test_root()
    current_db = Path(DB_PATH).resolve()
    assert current_db.is_relative_to(test_root), f"DB_PATH {current_db} not inside {test_root}"
    assert (test_root / TEST_ROOT_MARKER).is_file()


def test_dbs_10_sentinel_database_remains_unchanged():
    """DBS-10: Sentinel database in unsafe location is untouched byte-for-byte when isolation is tested."""
    with tempfile.NamedTemporaryFile(suffix="_sentinel.db", delete=False) as sent_file:
        sentinel_path = sent_file.name

    try:
        # Populate sentinel with known table and data
        conn = sqlite3.connect(sentinel_path)
        conn.execute("CREATE TABLE sentinel_vault (id INT PRIMARY KEY, secret TEXT)")
        conn.execute("INSERT INTO sentinel_vault VALUES (1, 'SUPER_SECRET_PRODUCTION_VALUE')")
        conn.commit()
        conn.close()

        # Compute pre-test SHA256
        with open(sentinel_path, "rb") as f:
            pre_hash = hashlib.sha256(f.read()).hexdigest()

        # Attempt to assert isolation on sentinel -> must fail
        with pytest.raises(RuntimeError):
            assert_test_database_isolated(sentinel_path)

        # Attempt to run schema migrations against sentinel -> must fail
        unsafe_engine = create_engine(f"sqlite:///{sentinel_path}")
        with pytest.raises(RuntimeError):
            run_schema_migrations(unsafe_engine)

        # Verify byte-for-byte SHA256 integrity remains identical
        with open(sentinel_path, "rb") as f:
            post_hash = hashlib.sha256(f.read()).hexdigest()

        assert pre_hash == post_hash, "CRITICAL HAZARD: Sentinel database was modified!"

        # Verify record content is untouched
        conn = sqlite3.connect(sentinel_path)
        cursor = conn.execute("SELECT secret FROM sentinel_vault WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        assert row[0] == "SUPER_SECRET_PRODUCTION_VALUE"
    finally:
        if os.path.exists(sentinel_path):
            os.remove(sentinel_path)


def test_dbs_11_concurrent_test_workers_use_isolated_databases():
    """DBS-11: Independent test workers generate isolated database files inside test root (V17.7.4 Section 12.7)."""
    test_root = get_or_create_test_root()
    current_run_id = os.getenv("AURUM_TEST_RUN_ID", "test_run_11")
    backend_dir = str(Path(__file__).resolve().parent.parent)

    worker_script = (
        "import os, sys\n"
        "from database import get_or_create_test_root, DB_PATH, assert_test_database_isolated\n"
        "assert_test_database_isolated(DB_PATH)\n"
        "print(DB_PATH)\n"
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([backend_dir, f"{backend_dir}/tests"])
    env["TESTING"] = "1"
    env["AURUM_TEST_RUN_ID"] = current_run_id
    env["AURUM_TEST_ROOT"] = str(test_root)
    env.pop("AURUM_TEST_DB_PATH", None)
    env.pop("AURUM_DB_PATH", None)

    res1 = subprocess.run([sys.executable, "-c", worker_script], env=env, capture_output=True, text=True)
    res2 = subprocess.run([sys.executable, "-c", worker_script], env=env, capture_output=True, text=True)

    assert res1.returncode == 0, res1.stderr
    assert res2.returncode == 0, res2.stderr

    db1 = res1.stdout.strip()
    db2 = res2.stdout.strip()

    assert db1 != db2, f"Worker collision: both workers used {db1}"
    assert Path(db1).parent == test_root
    assert Path(db2).parent == test_root
    assert assert_test_database_isolated(db1) is True
    assert assert_test_database_isolated(db2) is True


def test_dbs_12_independent_sessions_share_intentionally_owned_test_db():
    """DBS-12: Independent sessions can safely share an intentionally owned test DB."""
    test_root = get_or_create_test_root()
    shared_db_path = str(test_root / "dbs12_shared.db")
    assert_test_database_isolated(shared_db_path)

    test_engine = create_engine(f"sqlite:///{shared_db_path}")
    with test_engine.connect() as conn:
        conn.execute(sqlite3.connect.__name__ and create_engine and None or None) if False else None
        conn.execute(sqlite3.connect.__name__ and None or None) if False else None
    
    # Direct SQLite test across two connections
    conn1 = sqlite3.connect(shared_db_path)
    conn1.execute("CREATE TABLE IF NOT EXISTS shared_test (id INT PRIMARY KEY, val TEXT)")
    conn1.execute("DELETE FROM shared_test")
    conn1.execute("INSERT INTO shared_test VALUES (1, 'SESSION_1_COMMITTED')")
    conn1.commit()
    conn1.close()

    conn2 = sqlite3.connect(shared_db_path)
    cursor = conn2.execute("SELECT val FROM shared_test WHERE id = 1")
    res = cursor.fetchone()
    conn2.close()
    assert res[0] == "SESSION_1_COMMITTED"


def test_dbs_13_migration_refuses_unsafe_target():
    """DBS-13: run_schema_migrations refuses to execute against an unsafe target database."""
    with tempfile.NamedTemporaryFile(suffix="_unsafe_migration.db", delete=False) as f:
        unsafe_path = f.name
    try:
        unsafe_engine = create_engine(f"sqlite:///{unsafe_path}")
        with pytest.raises(RuntimeError) as exc:
            run_schema_migrations(unsafe_engine)
        assert "CRITICAL SAFETY HAZARD" in str(exc.value)
    finally:
        if os.path.exists(unsafe_path):
            os.remove(unsafe_path)


def test_dbs_14_destructive_fixture_refuses_unsafe_target():
    """DBS-14: Destructive cleanup fixture routine refuses to run on unsafe target database (V17.7.4 Section 09.4/12.6)."""
    with tempfile.NamedTemporaryFile(suffix="_unsafe_destructive.db", delete=False) as f:
        unsafe_path = f.name
    try:
        unsafe_engine = create_engine(f"sqlite:///{unsafe_path}")
        UnsafeSession = sessionmaker(bind=unsafe_engine)
        unsafe_session = UnsafeSession()
        try:
            # Exercise assertion with actual session bind
            with pytest.raises(RuntimeError) as exc:
                assert_test_database_isolated(session=unsafe_session)
            assert "CRITICAL SAFETY HAZARD" in str(exc.value)

            # Exercise assertion with actual engine bind
            with pytest.raises(RuntimeError) as exc2:
                assert_test_database_isolated(target_engine=unsafe_engine)
            assert "CRITICAL SAFETY HAZARD" in str(exc2.value)
        finally:
            unsafe_session.close()
    finally:
        if os.path.exists(unsafe_path):
            os.remove(unsafe_path)


def test_dbs_15_normal_production_database_configuration_remains_unchanged():
    """DBS-15: Normal production database path resolution remains intact when testing flag is off."""
    backend_dir = str(Path(__file__).resolve().parent.parent)
    expected_prod_path = str(Path(backend_dir) / "aurum_desk.db")

    # Run in subprocess with no TESTING or pytest markers, skip startup migrations to preserve DB sentinel
    script = (
        "import os, sys\n"
        "os.environ.pop('TESTING', None)\n"
        "os.environ.pop('PYTEST_CURRENT_TEST', None)\n"
        "os.environ.pop('AURUM_DB_PATH', None)\n"
        "os.environ['AURUM_SKIP_STARTUP_MIGRATIONS'] = '1'\n"
        "import database\n"
        "print(database.DB_PATH)\n"
    )
    res = subprocess.run([sys.executable, "-c", script], cwd=backend_dir, capture_output=True, text=True)
    assert res.returncode == 0
    actual_prod_path = res.stdout.strip()
    assert actual_prod_path == expected_prod_path, f"Expected {expected_prod_path}, got {actual_prod_path}"


def test_review_probe_1_runner_g16_cannot_wipe_external_sentinel(tmp_path):
    """Probe 1: G16 cannot wipe external database when run in normal environment with external AURUM_DB_PATH."""
    sentinel_dir = tmp_path / "external_sentinel"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel_db = sentinel_dir / "customer_runtime.db"

    # Pre-create sentinel DB with 1 record in a dummy table
    conn = sqlite3.connect(str(sentinel_db))
    conn.execute("CREATE TABLE sentinel_records (id INTEGER PRIMARY KEY, note TEXT);")
    conn.execute("INSERT INTO sentinel_records (note) VALUES ('PROTECTED_DATA');")
    conn.commit()
    conn.close()

    # Run helper in subprocess with TESTING unset and AURUM_DB_PATH pointing to external sentinel DB
    backend_dir = str(Path(__file__).resolve().parent.parent)
    test_script = (
        "import os, sys\n"
        "os.environ.pop('TESTING', None)\n"
        "os.environ.pop('PYTEST_CURRENT_TEST', None)\n"
        f"os.environ['AURUM_DB_PATH'] = r'{str(sentinel_db)}'\n"
        "from database import engine, assert_test_database_isolated\n"
        "try:\n"
        "    assert_test_database_isolated(target_engine=engine)\n"
        "    print('HAZARD: Assertion passed!')\n"
        "    sys.exit(1)\n"
        "except RuntimeError as exc:\n"
        "    if 'CRITICAL SAFETY HAZARD' in str(exc):\n"
        "        print('SUCCESS_ISOLATED')\n"
        "        sys.exit(0)\n"
        "    sys.exit(2)\n"
    )
    res = subprocess.run([sys.executable, "-c", test_script], cwd=backend_dir, capture_output=True, text=True)
    assert res.returncode == 0
    assert "SUCCESS_ISOLATED" in res.stdout

    # Verify sentinel record count remains exactly 1
    conn2 = sqlite3.connect(str(sentinel_db))
    cur = conn2.execute("SELECT count(*) FROM sentinel_records;")
    count = cur.fetchone()[0]
    conn2.close()
    assert count == 1, f"Expected 1 record remaining, got {count}"


def test_review_probe_6_test_root_canonical_mismatch_and_preexisting_data_rejected(tmp_path):
    """Probe 6: Marker with mismatched canonical_root or pre-existing *.db data cannot register as trusted root."""
    from database import register_trusted_test_root, TEST_ROOT_MARKER
    import time

    cur_run = os.getenv("AURUM_TEST_RUN_ID", "test_run_probe6")
    os.environ["AURUM_TEST_RUN_ID"] = cur_run

    # Case A: Mismatched canonical_root
    dir_a = tmp_path / "probe6_mismatched"
    dir_a.mkdir(parents=True, exist_ok=True)
    marker_a = dir_a / TEST_ROOT_MARKER
    marker_a.write_text(json.dumps({
        "schema_version": 1,
        "test_owner": "aurum_test_framework",
        "run_id": cur_run,
        "canonical_root": str((tmp_path / "WRONG_ROOT_PATH").resolve()),
    }), encoding="utf-8")

    with pytest.raises(RuntimeError) as exc_a:
        register_trusted_test_root(dir_a)
    assert "MISMATCHED_CANONICAL_ROOT" in str(exc_a.value)

    # Case B: Pre-existing *.db file before marker creation
    dir_b = tmp_path / "probe6_preexisting"
    dir_b.mkdir(parents=True, exist_ok=True)
    db_file = dir_b / "existing_customer.db"
    db_file.write_bytes(b"SQLite format 3\x00")
    # Backdate db file modification time by 5 seconds
    old_time = time.time() - 5.0
    os.utime(str(db_file), (old_time, old_time))

    time.sleep(0.1)
    marker_b = dir_b / TEST_ROOT_MARKER
    marker_b.write_text(json.dumps({
        "schema_version": 1,
        "test_owner": "aurum_test_framework",
        "run_id": cur_run,
        "canonical_root": str(dir_b.resolve()),
        "created_at": time.time(),
    }), encoding="utf-8")

    with pytest.raises(RuntimeError) as exc_b:
        register_trusted_test_root(dir_b)
    assert "PRE_EXISTING_EXTERNAL_DATA" in str(exc_b.value)


# ==============================================================================
# AURUM DESK V17.7.4.1 — FINAL DATABASE SAFETY VERIFICATION (DB-FINAL-01..12)
# ==============================================================================

def test_db_final_01_fresh_test_owned_db_accepted():
    """DB-FINAL-01: Fresh test-owned database within verified test root is accepted."""
    test_root = get_or_create_test_root()
    test_db = str(test_root / f"final_test_db_{os.getpid()}.db")
    assert assert_test_database_isolated(test_db) is True


def test_db_final_02_runtime_db_path_rejected():
    """DB-FINAL-02: Runtime DB path (aurum_desk.db) is unconditionally rejected with RuntimeError."""
    with pytest.raises(RuntimeError) as exc:
        assert_test_database_isolated(RUNTIME_DEFAULT_DB_PATH)
    assert "CRITICAL SAFETY HAZARD" in str(exc.value)
    assert "targets runtime database" in str(exc.value)


def test_db_final_03_arbitrary_external_db_path_rejected():
    """DB-FINAL-03: Arbitrary external DB path outside test root is rejected."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        ext_db = tmp.name
    try:
        with pytest.raises(RuntimeError) as exc:
            assert_test_database_isolated(ext_db)
        assert "CRITICAL SAFETY HAZARD" in str(exc.value)
        assert "not within verified test-owned root" in str(exc.value)
    finally:
        if os.path.exists(ext_db):
            os.remove(ext_db)


def test_db_final_04_symlink_outside_test_root_rejected():
    """DB-FINAL-04: Symlink inside test root pointing to external file is resolved and rejected."""
    test_root = get_or_create_test_root()
    with tempfile.NamedTemporaryFile(suffix="_real_ext.db", delete=False) as ext_f:
        ext_target = ext_f.name

    symlink_path = test_root / f"symlink_final_{os.getpid()}.db"
    try:
        if symlink_path.exists():
            symlink_path.unlink()
        try:
            symlink_path.symlink_to(ext_target)
        except OSError as e:
            if getattr(e, "winerror", None) == 1314 or "privilege" in str(e).lower():
                pytest.skip("Symlink creation requires elevated privilege on Windows")
            raise

        with pytest.raises(RuntimeError) as exc:
            assert_test_database_isolated(str(symlink_path))
        assert "CRITICAL SAFETY HAZARD" in str(exc.value)
        assert "not within verified test-owned root" in str(exc.value)
    finally:
        if symlink_path.exists():
            symlink_path.unlink()
        if os.path.exists(ext_target):
            os.remove(ext_target)


def test_db_final_05_hardlink_alias_to_external_sentinel_rejected():
    """DB-FINAL-05: Hardlink alias inside test root pointing to external sentinel DB is rejected."""
    test_root = get_or_create_test_root()
    with tempfile.NamedTemporaryFile(suffix="_sentinel_vault.db", delete=False) as sent_f:
        ext_sentinel = sent_f.name

    hardlink_path = test_root / f"hardlink_sentinel_{os.getpid()}.db"
    if hardlink_path.exists():
        hardlink_path.unlink()
    try:
        try:
            os.link(str(ext_sentinel), str(hardlink_path))
        except OSError as e:
            pytest.skip(f"Hardlink creation not supported across volumes: {e}")

        # Target file has st_nlink == 2, so it MUST be rejected by assert_test_database_isolated!
        with pytest.raises(RuntimeError) as exc:
            assert_test_database_isolated(str(hardlink_path))
        assert "CRITICAL SAFETY HAZARD" in str(exc.value)
        assert "hardlink" in str(exc.value).lower()
    finally:
        if hardlink_path.exists():
            hardlink_path.unlink()
        if os.path.exists(ext_sentinel):
            os.remove(ext_sentinel)


def test_db_final_06_preexisting_external_db_rejected(tmp_path):
    """DB-FINAL-06: Pre-existing database file in candidate test root is rejected."""
    from database import register_trusted_test_root, TEST_ROOT_MARKER
    import time

    old_env = os.environ.get("AURUM_TEST_RUN_ID")
    cur_run = f"run_db_final_06_{int(time.time()*1000)}"
    os.environ["AURUM_TEST_RUN_ID"] = cur_run

    test_dir = tmp_path / "preexisting_root"
    test_dir.mkdir(parents=True, exist_ok=True)
    db_file = test_dir / "old_customer_data.db"
    db_file.write_bytes(b"SQLite format 3\x00")
    # Backdate mtime
    old_ts = time.time() - 10.0
    os.utime(str(db_file), (old_ts, old_ts))

    time.sleep(0.05)
    marker = test_dir / TEST_ROOT_MARKER
    marker.write_text(json.dumps({
        "schema_version": 1,
        "test_owner": "aurum_test_framework",
        "run_id": cur_run,
        "canonical_root": str(test_dir.resolve()),
        "created_at": time.time(),
    }), encoding="utf-8")

    try:
        with pytest.raises(RuntimeError) as exc:
            register_trusted_test_root(test_dir)
        assert "PRE_EXISTING_EXTERNAL_DATA" in str(exc.value)
    finally:
        if old_env is not None:
            os.environ["AURUM_TEST_RUN_ID"] = old_env
        else:
            os.environ.pop("AURUM_TEST_RUN_ID", None)


def test_db_final_07_malformed_ownership_marker_rejected(tmp_path):
    """DB-FINAL-07: Malformed ownership marker is rejected fail-closed."""
    from database import register_trusted_test_root, TEST_ROOT_MARKER

    old_env = os.environ.get("AURUM_TEST_RUN_ID")
    cur_run = "run_db_final_07"
    os.environ["AURUM_TEST_RUN_ID"] = cur_run

    # Corrupted JSON marker
    dir_corrupt = tmp_path / "dir_corrupt_marker"
    dir_corrupt.mkdir(parents=True, exist_ok=True)
    marker = dir_corrupt / TEST_ROOT_MARKER
    marker.write_text("NOT_VALID_JSON{:::}", encoding="utf-8")

    try:
        with pytest.raises(RuntimeError) as exc:
            register_trusted_test_root(dir_corrupt)
        assert "MALFORMED_OWNERSHIP_MARKER" in str(exc.value)
    finally:
        if old_env is not None:
            os.environ["AURUM_TEST_RUN_ID"] = old_env
        else:
            os.environ.pop("AURUM_TEST_RUN_ID", None)


def test_db_final_08_foreign_run_ownership_marker_rejected(tmp_path):
    """DB-FINAL-08: Foreign-run ownership marker from another test run is rejected."""
    from database import register_trusted_test_root, TEST_ROOT_MARKER

    old_env = os.environ.get("AURUM_TEST_RUN_ID")
    os.environ["AURUM_TEST_RUN_ID"] = "current_run_alpha"

    dir_foreign = tmp_path / "dir_foreign_marker"
    dir_foreign.mkdir(parents=True, exist_ok=True)
    marker = dir_foreign / TEST_ROOT_MARKER
    marker.write_text(json.dumps({
        "schema_version": 1,
        "test_owner": "aurum_test_framework",
        "run_id": "foreign_run_bravo_different",
        "canonical_root": str(dir_foreign.resolve()),
    }), encoding="utf-8")

    try:
        with pytest.raises(RuntimeError) as exc:
            register_trusted_test_root(dir_foreign)
        assert "FOREIGN_RUN_OWNERSHIP_MARKER" in str(exc.value)
    finally:
        if old_env is not None:
            os.environ["AURUM_TEST_RUN_ID"] = old_env
        else:
            os.environ.pop("AURUM_TEST_RUN_ID", None)


def test_db_final_09_destructive_fixture_cannot_target_external_db():
    """DB-FINAL-09: Destructive operations refuse to execute when bound to external database."""
    with tempfile.NamedTemporaryFile(suffix="_external_destructive.db", delete=False) as f:
        ext_db = f.name

    try:
        ext_engine = create_engine(f"sqlite:///{ext_db}")
        ExtSession = sessionmaker(bind=ext_engine)
        session = ExtSession()
        try:
            with pytest.raises(RuntimeError) as exc1:
                assert_test_database_isolated(session=session)
            assert "CRITICAL SAFETY HAZARD" in str(exc1.value)

            with pytest.raises(RuntimeError) as exc2:
                assert_test_database_isolated(target_engine=ext_engine)
            assert "CRITICAL SAFETY HAZARD" in str(exc2.value)

            with pytest.raises(RuntimeError) as exc3:
                run_schema_migrations(ext_engine)
            assert "CRITICAL SAFETY HAZARD" in str(exc3.value)
        finally:
            session.close()
    finally:
        if os.path.exists(ext_db):
            os.remove(ext_db)


def test_db_final_10_runner_subprocess_uses_test_owned_db():
    """DB-FINAL-10: Subprocess running under test configuration binds strictly inside test-owned root."""
    test_root = get_or_create_test_root()
    backend_dir = str(Path(__file__).resolve().parent.parent)
    cur_run_id = os.getenv("AURUM_TEST_RUN_ID", "test_run_final_10")

    script = (
        "import os, sys\n"
        "from database import DB_PATH, assert_test_database_isolated, get_or_create_test_root\n"
        "assert_test_database_isolated(DB_PATH)\n"
        "print(DB_PATH)\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([backend_dir, f"{backend_dir}/tests"])
    env["TESTING"] = "1"
    env["AURUM_TEST_RUN_ID"] = cur_run_id
    env["AURUM_TEST_ROOT"] = str(test_root)

    res = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    db_out = res.stdout.strip()
    assert Path(db_out).parent == test_root


def test_db_final_11_production_configuration_remains_unchanged():
    """DB-FINAL-11: Normal production DB path resolution remains unchanged when TESTING is not set."""
    backend_dir = str(Path(__file__).resolve().parent.parent)
    expected_prod_path = str(Path(backend_dir) / "aurum_desk.db")

    script = (
        "import os, sys\n"
        "os.environ.pop('TESTING', None)\n"
        "os.environ.pop('PYTEST_CURRENT_TEST', None)\n"
        "os.environ.pop('AURUM_DB_PATH', None)\n"
        "os.environ['AURUM_SKIP_STARTUP_MIGRATIONS'] = '1'\n"
        "import database\n"
        "print(database.DB_PATH)\n"
    )
    res = subprocess.run([sys.executable, "-c", script], cwd=backend_dir, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == expected_prod_path


def test_db_final_12_sentinel_records_remain_unchanged():
    """DB-FINAL-12: External sentinel records and byte-for-byte SHA256 checksum remain completely untouched."""
    with tempfile.NamedTemporaryFile(suffix="_vault_sentinel.db", delete=False) as f:
        sentinel_path = f.name

    try:
        conn = sqlite3.connect(sentinel_path)
        conn.execute("CREATE TABLE sentinel_vault (id INT PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO sentinel_vault VALUES (101, 'CRITICAL_PROD_PAYLOAD')")
        conn.commit()
        conn.close()

        with open(sentinel_path, "rb") as sf:
            pre_sha = hashlib.sha256(sf.read()).hexdigest()

        # Attempt isolation assertion and migrations
        with pytest.raises(RuntimeError):
            assert_test_database_isolated(sentinel_path)

        unsafe_engine = create_engine(f"sqlite:///{sentinel_path}")
        with pytest.raises(RuntimeError):
            run_schema_migrations(unsafe_engine)

        with open(sentinel_path, "rb") as sf:
            post_sha = hashlib.sha256(sf.read()).hexdigest()
        assert pre_sha == post_sha, "Sentinel database was modified!"

        conn2 = sqlite3.connect(sentinel_path)
        cur = conn2.execute("SELECT payload FROM sentinel_vault WHERE id = 101")
        val = cur.fetchone()[0]
        conn2.close()
        assert val == "CRITICAL_PROD_PAYLOAD"
    finally:
        if os.path.exists(sentinel_path):
            os.remove(sentinel_path)
