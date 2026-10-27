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
    conn1.execute("CREATE TABLE shared_test (id INT PRIMARY KEY, val TEXT)")
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
