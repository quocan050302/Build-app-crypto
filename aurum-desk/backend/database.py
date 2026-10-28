import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Tuple
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session as OrmSession, sessionmaker
from sqlalchemy.pool import NullPool
from services.runtime_profile_service import (
    increment_safety_counter,
    is_market_shadow_runtime,
    is_non_trading_runtime,
    is_observe_only_runtime,
    resolve_be_runtime_profile,
)

# Ensure absolute path to backend/aurum_desk.db
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
RUNTIME_DEFAULT_DB_PATH = str(BASE_DIR / "aurum_desk.db")

TEST_ROOT_MARKER = ".aurum_test_owned"
_ACTIVE_TEST_ROOT: Path | None = None
_TRUSTED_TEST_ROOTS: set = set()

FORBIDDEN_ROOTS = {
    PROJECT_ROOT.resolve(),
    BASE_DIR.resolve(),
    Path.home().resolve(),
    Path(tempfile.gettempdir()).resolve(),
}
for d_drive in ("C:\\", "D:\\", "E:\\", "/"):
    try:
        FORBIDDEN_ROOTS.add(Path(d_drive).resolve())
    except Exception:
        pass


def _read_and_validate_marker(marker_path: Path, expected_run_id: str | None = None) -> dict:
    if not marker_path.is_file():
        raise RuntimeError(f"MARKER_NOT_FOUND: {marker_path}")
    if marker_path.is_symlink():
        raise RuntimeError(f"MARKER_IS_SYMLINK: {marker_path}")
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"MALFORMED_OWNERSHIP_MARKER (MALFORMED_MARKER): {e}")
    if not isinstance(data, dict):
        raise RuntimeError("MALFORMED_OWNERSHIP_MARKER (MALFORMED_MARKER): Expected JSON object")
    if data.get("schema_version") != 1:
        raise RuntimeError(f"MALFORMED_OWNERSHIP_MARKER (UNSUPPORTED_MARKER_SCHEMA): {data.get('schema_version')}")
    if data.get("test_owner") != "aurum_test_framework":
        raise RuntimeError(f"MALFORMED_OWNERSHIP_MARKER (INVALID_TEST_OWNER): {data.get('test_owner')}")
    run_id = data.get("run_id")
    if not run_id or not isinstance(run_id, str):
        raise RuntimeError("MALFORMED_OWNERSHIP_MARKER (MISSING_RUN_ID_IN_MARKER)")
    if expected_run_id and run_id != expected_run_id:
        raise RuntimeError(f"FOREIGN_RUN_OWNERSHIP_MARKER (STALE_RUN_MARKER): Expected run_id {expected_run_id}, got {run_id}")

    # Canonical root validation (V17.7.4 Section 08):
    # Marker must specify canonical_root matching its actual directory.
    canonical_str = data.get("canonical_root")
    if not canonical_str or not isinstance(canonical_str, str):
        raise RuntimeError("MALFORMED_OWNERSHIP_MARKER (MISSING_CANONICAL_ROOT_IN_MARKER)")
    marker_root = marker_path.parent.resolve()
    marker_canonical = Path(canonical_str).resolve()
    if marker_canonical != marker_root:
        raise RuntimeError(
            f"MISMATCHED_CANONICAL_ROOT: Marker specifies {marker_canonical} but is located in {marker_root}"
        )

    return data


def get_or_create_test_root() -> Path:
    """Returns canonical test-owned root directory with verified current-run ownership marker (V17.7.4 Section 08)."""
    global _ACTIVE_TEST_ROOT
    current_run_id = os.getenv("AURUM_TEST_RUN_ID")
    env_root = os.getenv("AURUM_TEST_ROOT")

    if env_root:
        candidate = Path(env_root).resolve()
        if candidate in FORBIDDEN_ROOTS:
            raise RuntimeError(f"FORBIDDEN_TEST_ROOT: Environment AURUM_TEST_ROOT cannot be system or project root ({candidate})")
        marker = candidate / TEST_ROOT_MARKER
        if candidate.is_dir() and marker.is_file():
            _read_and_validate_marker(marker, expected_run_id=current_run_id)
            _ACTIVE_TEST_ROOT = candidate
            return candidate

    if _ACTIVE_TEST_ROOT and _ACTIVE_TEST_ROOT.is_dir():
        marker = _ACTIVE_TEST_ROOT / TEST_ROOT_MARKER
        if marker.is_file():
            _read_and_validate_marker(marker, expected_run_id=current_run_id)
            return _ACTIVE_TEST_ROOT

    # Allocate fresh root for current run
    if not current_run_id:
        current_run_id = f"run_{int(time.time()*1000)}_{os.getpid()}"
        os.environ["AURUM_TEST_RUN_ID"] = current_run_id

    tmp_dir = tempfile.mkdtemp(prefix="aurum_test_root_")
    root_path = Path(tmp_dir).resolve()
    marker_path = root_path / TEST_ROOT_MARKER
    now_ts = time.time()
    metadata = {
        "schema_version": 1,
        "run_id": current_run_id,
        "canonical_root": str(root_path),
        "test_owner": "aurum_test_framework",
        "created_at": now_ts,
        "pid": os.getpid(),
    }
    marker_path.write_text(json.dumps(metadata), encoding="utf-8")
    os.environ["AURUM_TEST_ROOT"] = str(root_path)
    _ACTIVE_TEST_ROOT = root_path
    return root_path


def register_trusted_test_root(root_path: Path | str) -> None:
    """Registers an additional verified test-owned root directory (V17.7.4 Section 08.2). Raises on invalid."""
    r = Path(root_path).resolve()
    if r in FORBIDDEN_ROOTS:
        raise RuntimeError(f"FORBIDDEN_TEST_ROOT: Cannot register system or project root ({r})")
    if not r.is_dir():
        raise RuntimeError(f"INVALID_TEST_ROOT: Directory does not exist ({r})")
    if r.is_symlink():
        raise RuntimeError(f"INVALID_TEST_ROOT: Test root cannot be a symlink ({r})")

    marker = r / TEST_ROOT_MARKER
    current_run_id = os.getenv("AURUM_TEST_RUN_ID")
    data = _read_and_validate_marker(marker, expected_run_id=current_run_id)

    # V17.7.4: Rejects pre-existing external database files
    marker_created = float(data.get("created_at") or marker.stat().st_mtime)
    for db_candidate in r.glob("*.db"):
        if db_candidate.stat().st_mtime < marker_created - 0.5:
            raise RuntimeError(
                f"PRE_EXISTING_EXTERNAL_DATA: Trusted test root {r} contains pre-existing database file {db_candidate.name}"
            )

    _TRUSTED_TEST_ROOTS.add(r)


def normalize_sqlite_database_target(target: Any) -> Tuple[str, Path | None]:
    """
    Normalizes database target into (target_type, canonical_path) per V17.7.4 Section 08.3.
    target_type is 'memory' or 'file'.
    Raises RuntimeError on unsupported schemes or invalid targets.
    """
    if target is None:
        raise RuntimeError("CRITICAL SAFETY HAZARD: Database target is None or unspecified!")

    # Check if target is SQLAlchemy Engine
    if hasattr(target, "url"):
        target = target.url

    # Check if target is SQLAlchemy URL
    if hasattr(target, "database") and hasattr(target, "drivername"):
        if not target.drivername.startswith("sqlite"):
            raise RuntimeError(f"UNSUPPORTED_SCHEME: Only SQLite databases are supported, got {target.drivername}")
        db_name = target.database
        if db_name in (None, "", ":memory:"):
            return "memory", None
        target = db_name

    # Check string representation
    clean = str(target).strip()
    if clean in (":memory:", "sqlite:///:memory:"):
        return "memory", None

    # Handle SQLite file URI (file:///path or file:path)
    if clean.startswith("file:"):
        parsed = urllib.parse.urlparse(clean)
        if parsed.query:
            params = dict(qp.split("=", 1) for qp in parsed.query.split("&") if "=" in qp)
            if params.get("mode") == "memory":
                return "memory", None
        clean = urllib.request.url2pathname(parsed.path)

    # URL parsing via make_url if scheme exists
    elif "://" in clean:
        try:
            url_obj = make_url(clean)
            if not url_obj.drivername.startswith("sqlite"):
                raise RuntimeError(f"UNSUPPORTED_SCHEME: Only SQLite databases are supported, got {url_obj.drivername}")
            db_name = url_obj.database
            if db_name in (None, "", ":memory:"):
                return "memory", None
            clean = db_name
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"INVALID_DATABASE_URL: {e}")

    t_path = Path(clean).resolve()

    # Reject target directories or marker files
    if t_path.is_dir():
        raise RuntimeError(f"CRITICAL SAFETY HAZARD: Database target cannot be a directory ({t_path})!")
    if t_path.name == TEST_ROOT_MARKER:
        raise RuntimeError(f"CRITICAL SAFETY HAZARD: Test marker cannot be targeted as database ({t_path})!")

    return "file", t_path


def assert_test_database_isolated(
    db_path: Any = None,
    target_engine: Any = None,
    session: Any = None,
) -> bool:
    """Fail-closed guard (V17.7.4.1 Section 05):
    Asserts that target DB is strictly contained within a verified test-owned root directory,
    and is never the runtime database or an arbitrary external database.
    Can verify target from path, engine, or session bind.
    Enforces file ownership, rejects external hardlinks and pre-existing files.
    """
    target = db_path
    if session is not None:
        target = getattr(session, "bind", target)
    if target_engine is not None:
        target = target_engine
    if target is None:
        target = DB_PATH

    target_type, t_path = normalize_sqlite_database_target(target)
    if target_type == "memory":
        return True

    r_path = Path(RUNTIME_DEFAULT_DB_PATH).resolve()

    # 1. Direct collision with runtime DB
    if t_path == r_path:
        raise RuntimeError(f"CRITICAL SAFETY HAZARD: Test database targets runtime database at {r_path}!")

    # 2. Configured runtime DB collision
    configured_runtime = os.getenv("AURUM_RUNTIME_DB_PATH")
    if configured_runtime and t_path == Path(configured_runtime).resolve():
        raise RuntimeError(f"CRITICAL SAFETY HAZARD: Test database targets configured runtime database at {t_path}!")

    # 3. Basename protection against aurum_desk.db
    if "aurum_desk.db" in t_path.name:
        raise RuntimeError(f"CRITICAL SAFETY HAZARD: Test database cannot be named aurum_desk.db ({t_path})!")

    # 4. Hardlink alias detection against runtime DB (Section 08.3 / B06)
    if t_path.exists() and r_path.exists():
        try:
            t_stat = t_path.stat()
            r_stat = r_path.stat()
            if t_stat.st_ino != 0 and t_stat.st_ino == r_stat.st_ino:
                raise RuntimeError(
                    f"CRITICAL SAFETY HAZARD: Database target {t_path} is a hardlink to runtime database at {r_path}!"
                )
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise

    # 5. Strict containment in verified current-run test-owned root
    test_root = get_or_create_test_root()
    valid_roots = [test_root] + list(_TRUSTED_TEST_ROOTS)

    is_contained = False
    for root in valid_roots:
        marker = root / TEST_ROOT_MARKER
        if root.is_dir() and marker.is_file():
            try:
                marker_data = _read_and_validate_marker(marker, expected_run_id=os.getenv("AURUM_TEST_RUN_ID"))
                if t_path.is_relative_to(root):
                    # V17.7.4.1: Verify actual file ownership, hardlink count and age
                    if t_path.exists():
                        t_stat = t_path.stat()
                        # Reject hardlinks to any external files (link count > 1)
                        if t_stat.st_nlink > 1:
                            raise RuntimeError(
                                f"CRITICAL SAFETY HAZARD: Database target {t_path} is a hardlink alias (nlink={t_stat.st_nlink}) to an external file!"
                            )
                        # Reject pre-existing external database files
                        marker_created = float(marker_data.get("created_at") or marker.stat().st_mtime)
                        if t_stat.st_mtime < marker_created - 0.5:
                            raise RuntimeError(
                                f"CRITICAL SAFETY HAZARD: Pre-existing database file {t_path.name} in test root was modified before test marker!"
                            )
                    is_contained = True
                    break
            except Exception as e:
                if isinstance(e, RuntimeError) and "CRITICAL SAFETY HAZARD" in str(e):
                    raise

    if not is_contained:
        raise RuntimeError(
            f"CRITICAL SAFETY HAZARD: Database path {t_path} is not within verified test-owned root {test_root}!"
        )

    return True


# Strict Test Isolation Guard (V17.7.4 Section 08/09):
# If running under pytest or testing environment, tests must NEVER touch the runtime database!
is_pytest = "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST") is not None or os.getenv("TESTING") == "1"

if is_pytest:
    test_root = get_or_create_test_root()
    test_db_override = os.getenv("AURUM_TEST_DB_PATH")
    db_path_override = os.getenv("AURUM_DB_PATH")
    if test_db_override and db_path_override and test_db_override != db_path_override:
        raise RuntimeError(f"CONFLICTING_TEST_DB_TARGETS: AURUM_TEST_DB_PATH={test_db_override} vs AURUM_DB_PATH={db_path_override}")
    custom_test_db = test_db_override or db_path_override
    if custom_test_db:
        # Preflight validation: fail closed before binding engine
        assert_test_database_isolated(custom_test_db)
        _, resolved_target = normalize_sqlite_database_target(custom_test_db)
        DB_PATH = str(resolved_target) if resolved_target else ":memory:"
    else:
        DB_PATH = str(test_root / f"aurum_isolated_test_{os.getpid()}.db")
else:
    if is_non_trading_runtime():
        obs_db_override = os.getenv("AURUM_DB_PATH")
        if obs_db_override:
            _, resolved_obs_target = normalize_sqlite_database_target(obs_db_override)
            if resolved_obs_target and (
                resolved_obs_target == Path(RUNTIME_DEFAULT_DB_PATH).resolve()
                or "aurum_desk.db" in resolved_obs_target.name
            ):
                raise RuntimeError(
                    f"CRITICAL SAFETY HAZARD: {resolve_be_runtime_profile()} runtime cannot target operational database ({resolved_obs_target})!"
                )
            DB_PATH = str(resolved_obs_target) if resolved_obs_target else ":memory:"
        else:
            obs_tmp_dir = Path(tempfile.mkdtemp(prefix="aurum_observe_only_")).resolve()
            DB_PATH = str(obs_tmp_dir / f"aurum_observe_only_{os.getpid()}.db")
    else:
        DB_PATH = os.getenv("AURUM_DB_PATH", RUNTIME_DEFAULT_DB_PATH)

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    poolclass=NullPool
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    if is_non_trading_runtime():
        cursor.execute("PRAGMA query_only=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
    else:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


_FORBIDDEN_OBSERVE_ONLY_SQL_PREFIXES = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "CREATE",
    "ALTER",
    "DROP",
    "VACUUM",
    "REINDEX",
    "ATTACH",
    "DETACH",
)


@event.listens_for(Engine, "before_cursor_execute")
def _enforce_observe_only_sql_guard(conn, cursor, statement, parameters, context, executemany):
    if not is_non_trading_runtime():
        return
    is_shadow_engine = bool(getattr(conn.engine, "_aurum_shadow_research_engine", False))
    if is_market_shadow_runtime() and is_shadow_engine:
        return
    sql_clean = (statement or "").lstrip()
    while sql_clean.startswith("--") or sql_clean.startswith("/*"):
        if sql_clean.startswith("--"):
            nl = sql_clean.find("\n")
            sql_clean = sql_clean[nl + 1 :].lstrip() if nl != -1 else ""
        elif sql_clean.startswith("/*"):
            end_c = sql_clean.find("*/")
            sql_clean = sql_clean[end_c + 2 :].lstrip() if end_c != -1 else ""
    if not sql_clean:
        return
    first_token = sql_clean.split(None, 1)[0].upper()
    err_prefix = (
        "MARKET_SHADOW_EXECUTION_FORBIDDEN"
        if is_market_shadow_runtime()
        else "OBSERVE_ONLY_DB_WRITE_BLOCKED"
    )
    profile_name = resolve_be_runtime_profile()
    if first_token in _FORBIDDEN_OBSERVE_ONLY_SQL_PREFIXES:
        increment_safety_counter("BLOCKED_DB_WRITE_ATTEMPTS", 1)
        raise RuntimeError(
            f"{err_prefix}: Operational DB SQL write statement '{first_token}' is prohibited in {profile_name} runtime profile."
        )
    if first_token == "PRAGMA" and "=" in sql_clean:
        pragma_upper = sql_clean.upper().replace(" ", "")
        if not (
            pragma_upper.startswith("PRAGMAQUERY_ONLY=")
            or pragma_upper.startswith("PRAGMABUSY_TIMEOUT=")
        ):
            increment_safety_counter("BLOCKED_DB_WRITE_ATTEMPTS", 1)
            raise RuntimeError(
                f"{err_prefix}: Mutating PRAGMA '{sql_clean.strip()}' is prohibited in {profile_name} runtime profile."
            )


@event.listens_for(OrmSession, "before_flush")
def _enforce_observe_only_orm_flush_guard(session, flush_context, instances):
    if not is_non_trading_runtime():
        return
    bind = getattr(session, "bind", None)
    if is_market_shadow_runtime() and bool(getattr(bind, "_aurum_shadow_research_engine", False)):
        return
    increment_safety_counter("BLOCKED_DB_WRITE_ATTEMPTS", 1)
    err_prefix = (
        "MARKET_SHADOW_EXECUTION_FORBIDDEN"
        if is_market_shadow_runtime()
        else "OBSERVE_ONLY_DB_WRITE_BLOCKED"
    )
    raise RuntimeError(
        f"{err_prefix}: Operational DB ORM Session flush is prohibited in {resolve_be_runtime_profile()} runtime profile."
    )


def run_schema_migrations(target_engine=None):
    """Safely adds missing V6.1 and V7 columns and tables to SQLite without dropping data (V17.7.4 Section 08.4)."""
    if is_non_trading_runtime():
        increment_safety_counter("BLOCKED_STARTUP_MUTATION_ATTEMPTS", 1)
        return False
    eng = target_engine or engine
    if is_pytest:
        # Preflight: fail-closed before connection or DDL execution
        assert_test_database_isolated(target_engine=eng)
    try:
        with eng.connect() as conn:
            # 1. paper_orders
            result = conn.execute(text("PRAGMA table_info(paper_orders)"))
            cols = {row[1] for row in result.fetchall()}
            if cols:  # table exists
                v6_cols = [
                    ("last_processed_market_timestamp", "BIGINT"),
                    ("recovery_status", "VARCHAR(30) DEFAULT 'NONE'"),
                    ("recovery_run_id", "VARCHAR(36)"),
                    ("last_recovery_attempt", "BIGINT"),
                    ("resolved_through", "BIGINT"),
                    ("recovery_confidence", "VARCHAR(30)"),
                    ("discovered_at", "BIGINT"),
                    ("occurred_at", "BIGINT"),
                ]
                v7_cols = [
                    ("strategy_family", "VARCHAR(30) DEFAULT 'STANDARD_SMC'"),
                    ("session_instance_id", "VARCHAR(60)"),
                    ("policy_config_version", "INTEGER DEFAULT 1"),
                    ("evidence_snapshot_id", "VARCHAR(60)"),
                    ("requested_risk_pct", "FLOAT DEFAULT 0.25"),
                    ("effective_risk_pct", "FLOAT DEFAULT 0.25"),
                    ("risk_profile", "VARCHAR(30) DEFAULT 'STANDARD'"),
                    ("cost_snapshot", "TEXT"),
                ]
                v10_1_cols = [
                    ("origin", "VARCHAR(30) DEFAULT 'UNKNOWN'"),
                    ("execution_mode", "VARCHAR(20) DEFAULT 'AUTO'"),
                    ("arm_decision_snapshot", "TEXT"),
                    ("fill_decision_snapshot", "TEXT"),
                ]
                v14_4_po_cols = [
                    ("scenario_snapshot_id", "VARCHAR(80)"),
                    ("quality_snapshot_id", "VARCHAR(80)"),
                ]
                v16_6_po_cols = [
                    ("intrabar_ambiguous", "BOOLEAN DEFAULT 0"),
                ]
                for col_name, col_type in (v6_cols + v7_cols + v10_1_cols + v14_4_po_cols + v16_6_po_cols):
                    if col_name not in cols:
                        conn.execute(text(f"ALTER TABLE paper_orders ADD COLUMN {col_name} {col_type}"))
                conn.commit()

            # 2. watch_setups
            ws_res = conn.execute(text("PRAGMA table_info(watch_setups)"))
            ws_cols = {row[1] for row in ws_res.fetchall()}
            if ws_cols:
                v7_ws_cols = [
                    ("strategy_family", "VARCHAR(30) DEFAULT 'STANDARD_SMC'"),
                    ("policy_config_version", "INTEGER DEFAULT 1"),
                    ("evidence_snapshot_id", "VARCHAR(60)"),
                    ("requested_risk_pct", "FLOAT DEFAULT 0.25"),
                    ("effective_risk_pct", "FLOAT DEFAULT 0.25"),
                    ("risk_profile", "VARCHAR(30) DEFAULT 'STANDARD'"),
                    ("cost_snapshot", "TEXT"),
                    # V14.2 additive plan provenance
                    ("plan_kind", "VARCHAR(20)"),
                    ("direction_source", "VARCHAR(20)"),
                    # V14.3 additive: NY session id (NULL on legacy rows)
                    ("session_instance_id", "VARCHAR(60)"),
                    ("scenario_snapshot_id", "VARCHAR(80)"),
                    ("quality_snapshot_id", "VARCHAR(80)"),
                ]
                for col_name, col_type in v7_ws_cols:
                    if col_name not in ws_cols:
                        conn.execute(text(f"ALTER TABLE watch_setups ADD COLUMN {col_name} {col_type}"))
                conn.commit()

            # 3. lessons
            ls_res = conn.execute(text("PRAGMA table_info(lessons)"))
            ls_cols = {row[1] for row in ls_res.fetchall()}
            if ls_cols:
                v7_ls_cols = [
                    ("strategy_family", "VARCHAR(30) DEFAULT 'STANDARD_SMC'"),
                    ("facts_snapshot", "TEXT"),
                    ("compliance_snapshot", "TEXT"),
                    ("mfe_mae_snapshot", "TEXT"),
                    ("action_candidate", "TEXT"),
                    ("status", "VARCHAR(30) DEFAULT 'PENDING_REVIEW'"),
                    ("reviewed_at", "BIGINT"),
                    ("hypothesis", "TEXT"),
                    ("author", "VARCHAR(50) DEFAULT 'SYSTEM'"),
                    ("audit_trail", "TEXT"),
                    # V10 columns
                    ("severity", "VARCHAR(20) DEFAULT 'INFO'"),
                    ("effect", "VARCHAR(30) DEFAULT 'ANNOTATE'"),
                    ("enabled", "BOOLEAN DEFAULT 1"),
                    ("validation_status", "VARCHAR(20) DEFAULT 'UNVALIDATED'"),
                    ("validated_at", "BIGINT"),
                    ("validation_report", "TEXT"),
                    ("scope", "TEXT"),
                    ("predicate", "TEXT"),
                    ("stage", "VARCHAR(30) DEFAULT 'BEFORE_ARM'"),
                    ("version", "INTEGER DEFAULT 1"),
                    ("revision", "INTEGER DEFAULT 1"),
                    ("effective_at", "BIGINT"),
                    ("expiry_at", "BIGINT"),
                ]
                for col_name, col_type in v7_ls_cols:
                    if col_name not in ls_cols:
                        conn.execute(text(f"ALTER TABLE lessons ADD COLUMN {col_name} {col_type}"))
                conn.commit()

            # 4. notification_outbox V8 lease fields
            no_res = conn.execute(text("PRAGMA table_info(notification_outbox)"))
            no_cols = {row[1] for row in no_res.fetchall()}
            if no_cols:
                v8_no_cols = [
                    ("lease_expires_at", "BIGINT"),
                    ("worker_id", "VARCHAR(50)"),
                ]
                for col_name, col_type in v8_no_cols:
                    if col_name not in no_cols:
                        conn.execute(text(f"ALTER TABLE notification_outbox ADD COLUMN {col_name} {col_type}"))
                conn.commit()

            # 6. research_reports V13 additive columns
            rr_res = conn.execute(text("PRAGMA table_info(research_reports)"))
            rr_cols = {row[1] for row in rr_res.fetchall()}
            if rr_cols:
                v13_rr_cols = [
                    ("research_date", "VARCHAR(10)"),
                    ("date_basis", "VARCHAR(20) DEFAULT 'VN_DATE'"),
                    ("mode", "VARCHAR(30) DEFAULT 'CURRENT_ASOF'"),
                    ("as_of_ms", "BIGINT"),
                    ("market_regime", "VARCHAR(50)"),
                    ("timeframe_matrix", "TEXT"),
                    ("structured_scenarios", "TEXT"),
                    ("data_coverage_status", "VARCHAR(50) DEFAULT 'PROVIDED_VALIDATED'"),
                    ("quality_score", "FLOAT DEFAULT 0.8"),
                    ("provenance_metadata", "TEXT"),
                    ("review_reference", "TEXT"),
                ]
                for col_name, col_type in v13_rr_cols:
                    if col_name not in rr_cols:
                        conn.execute(text(f"ALTER TABLE research_reports ADD COLUMN {col_name} {col_type}"))
                conn.commit()

            # 5. Create V7 & V8 tables if not exist
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS trade_reviews (
                    id VARCHAR(36) PRIMARY KEY,
                    trade_id VARCHAR(36) UNIQUE NOT NULL,
                    execution_mode VARCHAR(20) DEFAULT 'AUTO',
                    user_notes TEXT,
                    self_reported_entry_reason TEXT,
                    psychology_before VARCHAR(100),
                    psychology_during VARCHAR(100),
                    psychology_after VARCHAR(100),
                    emotions TEXT,
                    confidence_score INTEGER,
                    discipline_score INTEGER,
                    user_loss_reason TEXT,
                    mistakes TEXT,
                    what_went_well TEXT,
                    improvement_plan TEXT,
                    revision INTEGER DEFAULT 1,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    reviewed_at BIGINT
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS trading_policies (
                    id VARCHAR(50) PRIMARY KEY,
                    version INTEGER DEFAULT 1,
                    mode VARCHAR(20) DEFAULT 'PAPER',
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    max_daily_fills INTEGER DEFAULT 3,
                    daily_min_goal INTEGER DEFAULT 2,
                    daily_timezone VARCHAR(50) DEFAULT 'Asia/Ho_Chi_Minh',
                    entry_session_policy VARCHAR(40) DEFAULT 'ALL_SESSIONS_WITH_NY_RESERVE',
                    ny_timezone VARCHAR(50) DEFAULT 'America/New_York',
                    ny_entry_start VARCHAR(10) DEFAULT '08:00',
                    ny_entry_end VARCHAR(10) DEFAULT '11:00',
                    ny_min_fills INTEGER DEFAULT 1,
                    reserve_ny_slot BOOLEAN DEFAULT 1,
                    ny_fallback_enabled BOOLEAN DEFAULT 1,
                    ny_fallback_start VARCHAR(10) DEFAULT '10:30',
                    ny_fallback_risk_pct_cap FLOAT DEFAULT 0.10,
                    min_net_rr FLOAT DEFAULT 2.0,
                    max_open_positions INTEGER DEFAULT 1,
                    max_armed_orders INTEGER DEFAULT 1,
                    updated_at BIGINT NOT NULL
                );
            """))
            tp_res = conn.execute(text("PRAGMA table_info(trading_policies)"))
            tp_cols = {row[1] for row in tp_res.fetchall()}
            if tp_cols and "daily_min_goal" not in tp_cols:
                conn.execute(text("ALTER TABLE trading_policies ADD COLUMN daily_min_goal INTEGER DEFAULT 2"))
                conn.commit()
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS session_quotas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_namespace VARCHAR(50) DEFAULT 'default',
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    session_instance_id VARCHAR(60) UNIQUE NOT NULL,
                    session_date_utc VARCHAR(10) NOT NULL,
                    session_date_vn VARCHAR(10) NOT NULL,
                    session_date_ny VARCHAR(10) NOT NULL,
                    window_start_ms BIGINT NOT NULL,
                    window_end_ms BIGINT NOT NULL,
                    fallback_start_ms BIGINT NOT NULL,
                    target_fills INTEGER DEFAULT 1,
                    standard_fills INTEGER DEFAULT 0,
                    fallback_fills INTEGER DEFAULT 0,
                    total_fills INTEGER DEFAULT 0,
                    quota_status VARCHAR(30) DEFAULT 'NOT_STARTED',
                    last_status_reason VARCHAR(255),
                    last_evaluated_at BIGINT,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS strategy_evidences (
                    id VARCHAR(60) PRIMARY KEY,
                    strategy_family VARCHAR(30) DEFAULT 'STANDARD_SMC',
                    strategy_version VARCHAR(20) DEFAULT '7.0.0',
                    setup_id VARCHAR(60),
                    setup_instance_id VARCHAR(100),
                    direction VARCHAR(10) NOT NULL,
                    htf_context TEXT,
                    h1_alignment VARCHAR(20) DEFAULT 'UNKNOWN',
                    sweep_evidence TEXT,
                    displacement_evidence TEXT,
                    fvg_evidence TEXT,
                    retest_evidence TEXT,
                    missing_evidence TEXT,
                    cost_snapshot TEXT,
                    risk_snapshot TEXT,
                    created_at BIGINT NOT NULL
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS smc_scenarios (
                    scenario_id VARCHAR(80) PRIMARY KEY,
                    instance_id VARCHAR(100) NOT NULL,
                    revision INTEGER DEFAULT 1,
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    timeframe VARCHAR(10) DEFAULT '5M',
                    strategy_family VARCHAR(30) DEFAULT 'STANDARD_SMC',
                    direction VARCHAR(10),
                    state VARCHAR(30) DEFAULT 'OBSERVING',
                    as_of_ms BIGINT NOT NULL,
                    anchor_level_id VARCHAR(80),
                    target_level_id VARCHAR(80),
                    summary_vi TEXT,
                    current_snapshot_id VARCHAR(80),
                    payload TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS smc_scenario_snapshots (
                    snapshot_id VARCHAR(80) PRIMARY KEY,
                    scenario_id VARCHAR(80) NOT NULL,
                    instance_id VARCHAR(100) NOT NULL,
                    revision INTEGER DEFAULT 1,
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    timeframe VARCHAR(10) DEFAULT '5M',
                    direction VARCHAR(10),
                    state VARCHAR(30) NOT NULL,
                    as_of_ms BIGINT NOT NULL,
                    evidence_cutoff_ms BIGINT NOT NULL,
                    evidence_digest VARCHAR(64) NOT NULL,
                    plan_fingerprint VARCHAR(32),
                    payload TEXT NOT NULL,
                    created_at BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_smc_scenarios_instance_id ON smc_scenarios (instance_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_smc_scenarios_state ON smc_scenarios (state);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_smc_scenario_snapshots_scenario_id ON smc_scenario_snapshots (scenario_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_smc_scenario_snapshots_instance_id ON smc_scenario_snapshots (instance_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_smc_scenario_snapshots_as_of_ms ON smc_scenario_snapshots (as_of_ms);"))
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sqs_setup_created ON setup_quality_snapshots (setup_id, created_at DESC);"))
            except Exception:
                pass
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS paper_observations (
                    id VARCHAR(80) PRIMARY KEY,
                    namespace VARCHAR(40) NOT NULL,
                    event_kind VARCHAR(30) NOT NULL,
                    snapshot_id VARCHAR(160),
                    scenario_id VARCHAR(160),
                    order_id VARCHAR(36),
                    as_of_ms BIGINT NOT NULL,
                    payload_json TEXT NOT NULL
                );
            """))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_observation_key ON paper_observations (namespace, event_kind, snapshot_id, order_id);"))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS forecast_observations (
                    id VARCHAR(80) PRIMARY KEY,
                    forecast_id VARCHAR(80) NOT NULL,
                    revision INTEGER DEFAULT 1,
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    session VARCHAR(20) NOT NULL,
                    session_id VARCHAR(80) NOT NULL,
                    as_of_ms BIGINT NOT NULL,
                    context_cutoff_ms BIGINT NOT NULL,
                    content_digest VARCHAR(64) NOT NULL,
                    hypothesis_type VARCHAR(50) NOT NULL,
                    direction VARCHAR(10),
                    target_price FLOAT,
                    invalidation_price FLOAT,
                    outcome_status VARCHAR(50) DEFAULT 'PENDING',
                    outcome_at_ms BIGINT,
                    outcome_details TEXT,
                    verification_status VARCHAR(30) DEFAULT 'UNVERIFIED',
                    invalidation_mode VARCHAR(30) DEFAULT 'CLOSED_PRICE',
                    payload_json TEXT NOT NULL,
                    created_at BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_forecast_session_digest ON forecast_observations (session_id, content_digest);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_forecast_observations_forecast_id ON forecast_observations (forecast_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_forecast_observations_session_id ON forecast_observations (session_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_forecast_observations_as_of_ms ON forecast_observations (as_of_ms);"))

            # Column migration for forecast_observations
            res_fc = conn.execute(text("PRAGMA table_info(forecast_observations)"))
            fc_cols = {row[1] for row in res_fc.fetchall()}
            if fc_cols:
                if "verification_status" not in fc_cols:
                    conn.execute(text("ALTER TABLE forecast_observations ADD COLUMN verification_status VARCHAR(30) DEFAULT 'UNVERIFIED';"))
                if "invalidation_mode" not in fc_cols:
                    conn.execute(text("ALTER TABLE forecast_observations ADD COLUMN invalidation_mode VARCHAR(30) DEFAULT 'CLOSED_PRICE';"))

            # V14.4.4: Upgrade legacy default policy NY_ONLY to ALL_SESSIONS_WITH_NY_RESERVE
            conn.execute(text("""
                UPDATE trading_policies
                SET entry_session_policy = 'ALL_SESSIONS_WITH_NY_RESERVE',
                    version = version + 1
                WHERE id = 'default' AND entry_session_policy = 'NY_ONLY';
            """))

            # V16.2: Ensure daily_min_goal in trading_policies
            res_tp = conn.execute(text("PRAGMA table_info(trading_policies)"))
            tp_cols = {row[1] for row in res_tp.fetchall()}
            if tp_cols and "daily_min_goal" not in tp_cols:
                conn.execute(text("ALTER TABLE trading_policies ADD COLUMN daily_min_goal INTEGER DEFAULT 2;"))

            # V16.15: Ensure paper_research_run_records and paper_research_order_links exist
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS paper_research_run_records (
                    run_id VARCHAR(80) PRIMARY KEY,
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    status VARCHAR(20) DEFAULT 'COLLECTING',
                    started_at_ms BIGINT NOT NULL,
                    ended_at_ms BIGINT,
                    software_version VARCHAR(20) DEFAULT 'V16.15',
                    config_fingerprint VARCHAR(80) DEFAULT 'DEFAULT',
                    execution_mode VARCHAR(30) DEFAULT 'PAPER_SHADOW',
                    initial_capital_usdt FLOAT DEFAULT 1000.0,
                    current_capital_usdt FLOAT DEFAULT 1000.0,
                    metadata_json TEXT,
                    created_at_ms BIGINT NOT NULL,
                    updated_at_ms BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_research_run_status ON paper_research_run_records (status);"))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS paper_research_order_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id VARCHAR(80) NOT NULL,
                    order_id VARCHAR(36) NOT NULL,
                    opportunity_id VARCHAR(80),
                    trace_id VARCHAR(80),
                    linked_at_ms BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_run_order ON paper_research_order_links (run_id, order_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_research_order_run ON paper_research_order_links (run_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_research_order_order ON paper_research_order_links (order_id);"))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS paper_research_order_milestones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id VARCHAR(80) NOT NULL,
                    order_id VARCHAR(36) NOT NULL,
                    milestone VARCHAR(30) NOT NULL,
                    timestamp_ms BIGINT NOT NULL,
                    payload_json TEXT,
                    created_at_ms BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_run_order_milestone ON paper_research_order_milestones (run_id, order_id, milestone);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_research_milestone_run ON paper_research_order_milestones (run_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_research_milestone_order ON paper_research_order_milestones (order_id);"))

            # V16.17 Opportunity Funnel run_id column
            opp_res = conn.execute(text("PRAGMA table_info(opportunity_funnel_records)"))
            opp_cols = {row[1] for row in opp_res.fetchall()}
            if opp_cols and "run_id" not in opp_cols:
                conn.execute(text("ALTER TABLE opportunity_funnel_records ADD COLUMN run_id VARCHAR(80)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_opportunity_funnel_run_id ON opportunity_funnel_records (run_id);"))

            # V16.17 Durable CG-01 Trace Records Table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS cg01_trace_records (
                    trace_id VARCHAR(80) PRIMARY KEY,
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    run_id VARCHAR(80),
                    source_event_id VARCHAR(120),
                    input_version VARCHAR(80),
                    evaluation_status VARCHAR(30) DEFAULT 'COMPLETED',
                    candidate_key VARCHAR(80),
                    armed_order_id VARCHAR(36),
                    total_runtime_ms FLOAT,
                    feed_delay_ms FLOAT,
                    scheduler_delay_ms FLOAT,
                    arm_delay_ms FLOAT,
                    fill_delay_ms FLOAT,
                    payload_json TEXT,
                    created_at_ms BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cg01_trace_run_id ON cg01_trace_records (run_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cg01_trace_created ON cg01_trace_records (created_at_ms);"))

            # V16.17 Paper Research Projection Checkpoints Table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS paper_research_checkpoints (
                    scope VARCHAR(50) PRIMARY KEY,
                    last_event_id VARCHAR(80),
                    last_event_time_ms BIGINT DEFAULT 0,
                    last_sequence INTEGER DEFAULT 0,
                    updated_at_ms BIGINT NOT NULL
                );
            """))
            # V16.18: paper_research_run_records scope and observation_mode columns
            run_res = conn.execute(text("PRAGMA table_info(paper_research_run_records)"))
            run_cols = {row[1] for row in run_res.fetchall()}
            if run_cols:
                if "scope" not in run_cols:
                    conn.execute(text("ALTER TABLE paper_research_run_records ADD COLUMN scope VARCHAR(50) DEFAULT 'CG01';"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_research_run_scope ON paper_research_run_records (scope);"))
                if "observation_mode" not in run_cols:
                    conn.execute(text("ALTER TABLE paper_research_run_records ADD COLUMN observation_mode VARCHAR(30) DEFAULT 'SHADOW';"))

            # V16.18: cg01_trace_records observation_mode column
            trace_res = conn.execute(text("PRAGMA table_info(cg01_trace_records)"))
            trace_cols = {row[1] for row in trace_res.fetchall()}
            if trace_cols and "observation_mode" not in trace_cols:
                conn.execute(text("ALTER TABLE cg01_trace_records ADD COLUMN observation_mode VARCHAR(30) DEFAULT 'SHADOW';"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cg01_trace_mode ON cg01_trace_records (observation_mode);"))

            # V16.18: Durable Pending Projections Table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS paper_research_pending_projections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id VARCHAR(80) NOT NULL,
                    scope VARCHAR(50) DEFAULT 'CG01',
                    event_type VARCHAR(50) NOT NULL,
                    aggregate_id VARCHAR(80),
                    order_id VARCHAR(36),
                    run_id VARCHAR(80),
                    reason VARCHAR(100) NOT NULL,
                    attempt_count INTEGER DEFAULT 0,
                    next_retry_at_ms BIGINT DEFAULT 0,
                    first_seen_at_ms BIGINT NOT NULL,
                    last_error TEXT,
                    status VARCHAR(30) DEFAULT 'PENDING',
                    payload_json TEXT,
                    created_at_ms BIGINT NOT NULL,
                    updated_at_ms BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pending_proj_status ON paper_research_pending_projections (status);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pending_proj_event ON paper_research_pending_projections (event_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pending_proj_order ON paper_research_pending_projections (order_id);"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_proj_event_scope ON paper_research_pending_projections (event_id, scope);"))

            # V16.18 & V17.6.4.3: Observation Intervals Table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS paper_observation_intervals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope VARCHAR(50) DEFAULT 'CG01',
                    run_id VARCHAR(80),
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    worker_role VARCHAR(40) DEFAULT 'SCHEDULER',
                    mode VARCHAR(30) DEFAULT 'SHADOW',
                    start_ms BIGINT NOT NULL,
                    end_ms BIGINT NOT NULL,
                    data_quality VARCHAR(30) DEFAULT 'GOOD',
                    heartbeats_count INTEGER DEFAULT 1,
                    created_at_ms BIGINT NOT NULL,
                    engine_instance_id VARCHAR(80),
                    evidence_origin VARCHAR(40) DEFAULT 'LIVE_FORWARD',
                    interval_identity VARCHAR(128) UNIQUE
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_obs_interval_run ON paper_observation_intervals (run_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_obs_interval_scope ON paper_observation_intervals (scope);"))

            # V17.6.4.3: Additive column migration and unique index for paper_observation_intervals (DBEA-05, DBEA-06)
            poi_res = conn.execute(text("PRAGMA table_info(paper_observation_intervals)"))
            poi_cols = {row[1] for row in poi_res.fetchall()}
            if poi_cols:
                if "engine_instance_id" not in poi_cols:
                    conn.execute(text("ALTER TABLE paper_observation_intervals ADD COLUMN engine_instance_id VARCHAR(80);"))
                if "evidence_origin" not in poi_cols:
                    conn.execute(text("ALTER TABLE paper_observation_intervals ADD COLUMN evidence_origin VARCHAR(40) DEFAULT 'LEGACY_UNKNOWN';"))
                if "interval_identity" not in poi_cols:
                    conn.execute(text("ALTER TABLE paper_observation_intervals ADD COLUMN interval_identity VARCHAR(128);"))
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_obs_interval_identity ON paper_observation_intervals (interval_identity);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_obs_engine_instance ON paper_observation_intervals (engine_instance_id);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_obs_evidence_origin ON paper_observation_intervals (evidence_origin);"))

            # V16.39: Post Trade Reviews Table (Section 078 - 080)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS post_trade_reviews (
                    id VARCHAR(36) PRIMARY KEY,
                    order_id VARCHAR(36) NOT NULL UNIQUE,
                    symbol VARCHAR(20) NOT NULL,
                    model_id VARCHAR(50) NOT NULL,
                    direction VARCHAR(10) NOT NULL,
                    planned_entry FLOAT NOT NULL,
                    actual_entry FLOAT NOT NULL,
                    planned_sl FLOAT NOT NULL,
                    planned_tp FLOAT NOT NULL,
                    exit_price FLOAT NOT NULL,
                    exit_cause VARCHAR(50) NOT NULL,
                    gross_pnl FLOAT NOT NULL,
                    net_pnl FLOAT NOT NULL,
                    realized_rr FLOAT NOT NULL,
                    outcome_status VARCHAR(20) NOT NULL,
                    hold_time_ms BIGINT,
                    entry_slippage_usd FLOAT NOT NULL,
                    loss_classification VARCHAR(50) NOT NULL,
                    review_json TEXT NOT NULL,
                    created_at BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_post_trade_reviews_order ON post_trade_reviews (order_id);"))

            # V16.39.2: Durable Lesson Repair Tasks Table (Section 039 - 042)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS lesson_repair_tasks (
                    id VARCHAR(36) PRIMARY KEY,
                    order_id VARCHAR(36) NOT NULL UNIQUE,
                    artifact_type VARCHAR(30) DEFAULT 'LESSON',
                    status VARCHAR(20) DEFAULT 'PENDING',
                    retry_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lesson_repair_tasks_order ON lesson_repair_tasks (order_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lesson_repair_tasks_status ON lesson_repair_tasks (status);"))

            # V17.6: Durable Engine Observability & Decision Intelligence Tables
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS engine_heartbeats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    engine_instance_id VARCHAR(80) NOT NULL UNIQUE,
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    run_id VARCHAR(80),
                    state VARCHAR(30) DEFAULT 'IDLE',
                    last_process_heartbeat_at_ms BIGINT NOT NULL,
                    last_evaluation_started_at_ms BIGINT,
                    last_evaluation_completed_at_ms BIGINT,
                    last_successful_evaluation_at_ms BIGINT,
                    last_cycle_id VARCHAR(80),
                    cycles_count INTEGER DEFAULT 0,
                    failed_cycles_count INTEGER DEFAULT 0,
                    cadence_sec INTEGER DEFAULT 15,
                    metadata_json TEXT,
                    updated_at_ms BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_engine_hb_instance ON engine_heartbeats (engine_instance_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_engine_hb_symbol ON engine_heartbeats (symbol);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_engine_hb_run_id ON engine_heartbeats (run_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_engine_hb_state ON engine_heartbeats (state);"))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS opportunity_decision_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id VARCHAR(80) NOT NULL UNIQUE,
                    event_version INTEGER DEFAULT 1,
                    run_id VARCHAR(80),
                    cycle_id VARCHAR(80),
                    opportunity_id VARCHAR(80) NOT NULL,
                    symbol VARCHAR(20) DEFAULT 'XAUUSDT',
                    model_id VARCHAR(40) NOT NULL,
                    direction VARCHAR(10) NOT NULL,
                    stage VARCHAR(30) NOT NULL,
                    primary_reason_code VARCHAR(80),
                    reason_category VARCHAR(40),
                    reason_codes_json TEXT,
                    evidence_ids_json TEXT,
                    waiting_conditions_json TEXT,
                    payload_json TEXT,
                    occurred_at_ms BIGINT NOT NULL,
                    created_at_ms BIGINT NOT NULL
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_opp_dec_event_id ON opportunity_decision_events (event_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_opp_dec_opp_id ON opportunity_decision_events (opportunity_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_opp_dec_symbol ON opportunity_decision_events (symbol);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_opp_dec_model_id ON opportunity_decision_events (model_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_opp_dec_stage ON opportunity_decision_events (stage);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_opp_dec_run_id ON opportunity_decision_events (run_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_opp_dec_occurred ON opportunity_decision_events (occurred_at_ms);"))

            # Additive migration check for existing opportunity_decision_events table
            ode_res = conn.execute(text("PRAGMA table_info(opportunity_decision_events)"))
            ode_cols = {row[1] for row in ode_res.fetchall()}
            if ode_cols and "symbol" not in ode_cols:
                conn.execute(text("ALTER TABLE opportunity_decision_events ADD COLUMN symbol VARCHAR(20) DEFAULT 'XAUUSDT';"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_opp_dec_symbol ON opportunity_decision_events (symbol);"))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS engine_incidents (
                    id VARCHAR(80) PRIMARY KEY,
                    severity VARCHAR(20) NOT NULL,
                    component VARCHAR(40) NOT NULL,
                    engine_instance_id VARCHAR(80),
                    run_id VARCHAR(80),
                    cycle_id VARCHAR(80),
                    opportunity_id VARCHAR(80),
                    order_id VARCHAR(36),
                    reason_code VARCHAR(80),
                    message TEXT NOT NULL,
                    status VARCHAR(20) DEFAULT 'ACTIVE',
                    created_at_ms BIGINT NOT NULL,
                    recovered_at_ms BIGINT,
                    payload_json TEXT
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_engine_incidents_status ON engine_incidents (status);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_engine_incidents_created ON engine_incidents (created_at_ms);"))

            conn.commit()
    except Exception:
        pass

if os.environ.get("AURUM_SKIP_STARTUP_MIGRATIONS") != "1" and not is_non_trading_runtime():
    run_schema_migrations(engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    if is_non_trading_runtime():
        db.autoflush = False
    try:
        yield db
    finally:
        db.close()

