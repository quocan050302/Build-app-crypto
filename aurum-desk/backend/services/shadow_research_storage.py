"""
backend/services/shadow_research_storage.py
AURUM V17.8.0.3 — Isolated Shadow Research Storage Engine & Versioned Entities.

Guarantees:
1. Dedicated SQLite database configured via AURUM_SHADOW_DB_PATH (defaulting to
   backend/shadow_data/market_shadow.db outside pytest, or test-owned root in pytest).
2. Strict path validation rejecting:
   - Operational trading database (aurum_desk.db or active database.DB_PATH)
   - Relative-path traversal ('..')
   - Symlinks (on target file or any ancestor directory)
   - Hardlink aliasing (matching operational DB inode or st_nlink > 1)
   - Directory targets or forbidden roots
   - Existing SQLite databases containing operational trading tables (e.g. paper_orders)
3. Separate DeclarativeBase (ShadowBase) so shadow research tables never mix with
   operational trading tables and no entity is ever represented as a PaperOrder.
4. Bounded write queue, retry policy, transactional integrity, and explicit failure reporting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    inspect,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

import database
from services.runtime_profile_service import increment_safety_counter

logger = logging.getLogger(__name__)

ENV_SHADOW_DB_PATH = "AURUM_SHADOW_DB_PATH"
DEFAULT_SHADOW_SUBDIR = "shadow_data"
DEFAULT_SHADOW_DB_FILENAME = "market_shadow.db"
SHADOW_OWNERSHIP_MARKER = "MARKET_SHADOW_RESEARCH_ONLY_V17_8_0_3"

# Explicit shadow status labels (Section 7.2)
SHADOW_LABEL_SIGNAL = "SHADOW_SIGNAL"
SHADOW_LABEL_CANDIDATE = "SHADOW_CANDIDATE"
SHADOW_LABEL_REJECTED = "SHADOW_REJECTED"
SHADOW_LABEL_ELIGIBLE = "SHADOW_ELIGIBLE"
SHADOW_LABEL_SIMULATED_ENTRY = "SHADOW_SIMULATED_ENTRY"
SHADOW_LABEL_SIMULATED_EXIT = "SHADOW_SIMULATED_EXIT"

ALLOWED_SHADOW_LABELS = (
    SHADOW_LABEL_SIGNAL,
    SHADOW_LABEL_CANDIDATE,
    SHADOW_LABEL_REJECTED,
    SHADOW_LABEL_ELIGIBLE,
    SHADOW_LABEL_SIMULATED_ENTRY,
    SHADOW_LABEL_SIMULATED_EXIT,
)

# Explicit shadow opportunity states (Section 9.1)
SHADOW_STATE_DETECTED = "DETECTED"
SHADOW_STATE_WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
SHADOW_STATE_CONFIRMED = "CONFIRMED"
SHADOW_STATE_ELIGIBLE = "ELIGIBLE"
SHADOW_STATE_REJECTED = "REJECTED"
SHADOW_STATE_EXPIRED = "EXPIRED"
SHADOW_STATE_INVALIDATED = "INVALIDATED"
SHADOW_STATE_OBSERVATION_COMPLETED = "OBSERVATION_COMPLETED"

ALLOWED_SHADOW_STATES = (
    SHADOW_STATE_DETECTED,
    SHADOW_STATE_WAITING_CONFIRMATION,
    SHADOW_STATE_CONFIRMED,
    SHADOW_STATE_ELIGIBLE,
    SHADOW_STATE_REJECTED,
    SHADOW_STATE_EXPIRED,
    SHADOW_STATE_INVALIDATED,
    SHADOW_STATE_OBSERVATION_COMPLETED,
)

RESEARCH_ONLY_BADGE = "RESEARCH ONLY — NO ORDER EXECUTED"
RESEARCH_ONLY_DISCLOSURE = RESEARCH_ONLY_BADGE
DATA_VERSION = "v17.8.0.3"
ALGORITHM_VERSION = "V17_8_0_3"
EXECUTION_STATUS_NEVER = "NEVER_EXECUTED"

ShadowBase = declarative_base()


# ==============================================================================
# Versioned Shadow Research ORM Entities (Never PaperOrder)
# ==============================================================================

class ShadowStorageOwnership(ShadowBase):
    __tablename__ = "shadow_storage_ownership"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ownership_marker = Column(String(128), nullable=False, unique=True)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at_ms = Column(BigInteger, nullable=False)
    operational_trading_allowed = Column(Boolean, nullable=False, default=False)


class ShadowMarketQuote(ShadowBase):
    __tablename__ = "shadow_market_quotes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quote_id = Column(String(128), nullable=False, unique=True, index=True)
    source_event_id = Column(String(128), nullable=False)
    symbol = Column(String(32), nullable=False, index=True)
    bid = Column(Float, nullable=False)
    ask = Column(Float, nullable=False)
    last = Column(Float, nullable=True)
    mark_price = Column(Float, nullable=True)
    spread = Column(Float, nullable=False)
    exchange_time_ms = Column(BigInteger, nullable=False, index=True)
    receive_time_ms = Column(BigInteger, nullable=False)
    decision_cutoff_ms = Column(BigInteger, nullable=False)
    source = Column(String(32), nullable=False)
    connection_epoch = Column(Integer, nullable=False, default=0)
    sequence_order = Column(BigInteger, nullable=False, default=0)
    freshness_ms = Column(BigInteger, nullable=False, default=0)
    is_fresh = Column(Boolean, nullable=False, default=True)
    data_version = Column(String(32), nullable=False, default="v17.8.0.3")
    source_hash = Column(String(64), nullable=False)


class ShadowClosedCandle(ShadowBase):
    __tablename__ = "shadow_closed_candles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    candle_id = Column(String(128), nullable=False, unique=True, index=True)
    source_event_id = Column(String(128), nullable=False)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(16), nullable=False, index=True)
    open_time_ms = Column(BigInteger, nullable=False, index=True)
    close_time_ms = Column(BigInteger, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False, default=0.0)
    is_closed = Column(Boolean, nullable=False, default=True)
    source = Column(String(32), nullable=False)
    connection_epoch = Column(Integer, nullable=False, default=0)
    exchange_time_ms = Column(BigInteger, nullable=False)
    receive_time_ms = Column(BigInteger, nullable=False)
    decision_cutoff_ms = Column(BigInteger, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    data_version = Column(String(32), nullable=False, default="v17.8.0.3")
    source_hash = Column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_shadow_candles_sym_tf_open", "symbol", "timeframe", "open_time_ms"),
    )


class ShadowAnalysisCycle(ShadowBase):
    __tablename__ = "shadow_analysis_cycles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cycle_id = Column(String(128), nullable=False, unique=True, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    trigger_reason = Column(String(64), nullable=False)
    started_at_ms = Column(BigInteger, nullable=False, index=True)
    completed_at_ms = Column(BigInteger, nullable=False)
    decision_cutoff_ms = Column(BigInteger, nullable=False)
    connection_epoch = Column(Integer, nullable=False, default=0)
    feed_state = Column(String(32), nullable=False)
    m1_bars_count = Column(Integer, nullable=False, default=0)
    m5_bars_count = Column(Integer, nullable=False, default=0)
    m15_bars_count = Column(Integer, nullable=False, default=0)
    models_evaluated_json = Column(Text, nullable=False)
    detected_count = Column(Integer, nullable=False, default=0)
    unicorn_confirmed_count = Column(Integer, nullable=False, default=0)
    eligible_count = Column(Integer, nullable=False, default=0)
    rejected_count = Column(Integer, nullable=False, default=0)
    algorithm_version = Column(String(32), nullable=False, default="V17_8_0_3")
    policy_snapshot_version = Column(Integer, nullable=False, default=1)
    source_quote_id = Column(String(128), nullable=True)
    source_candle_ids_json = Column(Text, nullable=True)
    source_hash = Column(String(64), nullable=False)


class ShadowUnicornEvidence(ShadowBase):
    __tablename__ = "shadow_unicorn_evidences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    unicorn_evidence_id = Column(String(128), nullable=False, unique=True, index=True)
    opportunity_id = Column(String(128), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    direction = Column(String(16), nullable=False)
    timeframe = Column(String(16), nullable=False, default="1M")
    is_valid = Column(Boolean, nullable=False, default=False)
    validity_status = Column(String(64), nullable=False)
    setup_episode_id = Column(String(128), nullable=True)
    breaker_id = Column(String(128), nullable=True)
    selected_fvg_id = Column(String(128), nullable=True)
    source_break_id = Column(String(128), nullable=True)
    overlap_low = Column(Float, nullable=True)
    overlap_high = Column(Float, nullable=True)
    invalidation_price = Column(Float, nullable=True)
    decision_cutoff_ms = Column(BigInteger, nullable=False, index=True)
    receive_time_ms = Column(BigInteger, nullable=False)
    rejection_reasons_json = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=False)
    source_hash = Column(String(64), nullable=False)


class ShadowStopDecision(ShadowBase):
    __tablename__ = "shadow_stop_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stop_decision_id = Column(String(128), nullable=False, unique=True, index=True)
    opportunity_id = Column(String(128), nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    model_id = Column(String(64), nullable=False)
    direction = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False)
    legacy_sl = Column(Float, nullable=True)
    structural_sl = Column(Float, nullable=True)
    unicorn_sl_candidate = Column(Float, nullable=True)
    noise_research_sl = Column(Float, nullable=True)
    selected_invalidation_price = Column(Float, nullable=True)
    selected_anchor_kind = Column(String(64), nullable=True)
    selected_anchor_id = Column(String(128), nullable=True)
    atr_m1 = Column(Float, nullable=True)
    atr_m5 = Column(Float, nullable=True)
    spread = Column(Float, nullable=True)
    buffer_price = Column(Float, nullable=True)
    protective_distance = Column(Float, nullable=True)
    decision_cutoff_ms = Column(BigInteger, nullable=False)
    algorithm_version = Column(String(32), nullable=False, default="V17_8_0_3")
    policy_snapshot_version = Column(Integer, nullable=False, default=1)
    variants_comparison_json = Column(Text, nullable=False)
    source_hash = Column(String(64), nullable=False)


class ShadowTargetDecision(ShadowBase):
    __tablename__ = "shadow_target_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_decision_id = Column(String(128), nullable=False, unique=True, index=True)
    opportunity_id = Column(String(128), nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    direction = Column(String(16), nullable=False)
    status = Column(String(64), nullable=False)
    target_price = Column(Float, nullable=True)
    target_evidence_id = Column(String(128), nullable=True)
    target_type = Column(String(64), nullable=True)
    reason_code = Column(String(64), nullable=True)
    decision_cutoff_ms = Column(BigInteger, nullable=False)
    payload_json = Column(Text, nullable=False)
    source_hash = Column(String(64), nullable=False)


class ShadowOpportunity(ShadowBase):
    __tablename__ = "shadow_opportunities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    opportunity_id = Column(String(128), nullable=False, unique=True, index=True)
    cycle_id = Column(String(128), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    model_id = Column(String(64), nullable=False, index=True)
    direction = Column(String(16), nullable=False)
    setup_episode_id = Column(String(128), nullable=False, index=True)
    analysis_time_ms = Column(BigInteger, nullable=False, index=True)
    decision_cutoff_ms = Column(BigInteger, nullable=False, index=True)
    exchange_time_ms = Column(BigInteger, nullable=False)
    receive_time_ms = Column(BigInteger, nullable=False)
    connection_epoch = Column(Integer, nullable=False, default=0)
    source = Column(String(32), nullable=False, default="WS")

    entry_reference = Column(Float, nullable=True)
    entry_quote_bid = Column(Float, nullable=False)
    entry_quote_ask = Column(Float, nullable=False)

    legacy_sl = Column(Float, nullable=True)
    structural_sl = Column(Float, nullable=True)
    unicorn_sl_candidate = Column(Float, nullable=True)

    selected_invalidation_price = Column(Float, nullable=True)
    selected_anchor_kind = Column(String(64), nullable=True)
    selected_anchor_id = Column(String(128), nullable=True)

    atr_m1 = Column(Float, nullable=True)
    atr_m5 = Column(Float, nullable=True)
    spread = Column(Float, nullable=False)
    protective_distance = Column(Float, nullable=True)

    target_price = Column(Float, nullable=True)
    target_evidence_id = Column(String(128), nullable=True)

    risk_budget = Column(Float, nullable=False, default=2.50)
    hypothetical_quantity = Column(Float, nullable=True)
    net_risk = Column(Float, nullable=True)
    net_reward = Column(Float, nullable=True)
    net_rr = Column(Float, nullable=True)

    unicorn_validity = Column(String(64), nullable=False)
    unicorn_evidence_id = Column(String(128), nullable=True)

    shadow_label = Column(String(64), nullable=False)
    lifecycle_state = Column(String(64), nullable=False, index=True)
    shadow_eligibility_status = Column(String(64), nullable=False, index=True)
    rejection_reason_codes_json = Column(Text, nullable=False)
    execution_status = Column(String(32), nullable=False, default=EXECUTION_STATUS_NEVER)
    research_disclosure = Column(String(128), nullable=False, default=RESEARCH_ONLY_BADGE)

    # Hypothetical monitoring fields (Section 9.2)
    would_enter_at = Column(Float, nullable=True)
    would_use_sl = Column(Float, nullable=True)
    would_use_tp = Column(Float, nullable=True)
    would_use_quantity = Column(Float, nullable=True)
    simulated_entry_status = Column(String(64), nullable=False, default="SHADOW_ENTRY_NOT_TRIGGERED")
    simulated_exit_status = Column(String(64), nullable=False, default="EXIT_SEQUENCE_UNVERIFIABLE")
    state_history_json = Column(Text, nullable=False, default="[]")

    # Provenance & versions (Section 7.3)
    source_event_id = Column(String(128), nullable=False)
    source_quote_id = Column(String(128), nullable=True)
    source_candle_ids_json = Column(Text, nullable=False)
    source_break_id = Column(String(128), nullable=True)
    source_fvg_id = Column(String(128), nullable=True)
    source_breaker_id = Column(String(128), nullable=True)
    data_version = Column(String(32), nullable=False, default="v17.8.0.3")
    algorithm_version = Column(String(32), nullable=False, default="V17_8_0_3")
    policy_snapshot_version = Column(Integer, nullable=False, default=1)
    variants_json = Column(Text, nullable=False, default="{}")
    source_hash = Column(String(64), nullable=False)


class ShadowReplayResult(ShadowBase):
    __tablename__ = "shadow_replay_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(128), nullable=False, unique=True, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    dataset_sha256 = Column(String(64), nullable=False)
    source_sha = Column(String(64), nullable=False)
    backtest_verdict = Column(String(64), nullable=False)
    data_quality_status = Column(String(64), nullable=False)
    requested_start_ms = Column(BigInteger, nullable=False)
    requested_end_ms = Column(BigInteger, nullable=False)
    actual_earliest_ms = Column(BigInteger, nullable=True)
    actual_latest_ms = Column(BigInteger, nullable=True)
    m1_bars = Column(Integer, nullable=False, default=0)
    m5_bars = Column(Integer, nullable=False, default=0)
    m15_bars = Column(Integer, nullable=False, default=0)
    created_at_ms = Column(BigInteger, nullable=False, index=True)
    summary_json = Column(Text, nullable=False)
    source_hash = Column(String(64), nullable=False)


class ShadowRuntimeHealth(ShadowBase):
    __tablename__ = "shadow_runtime_health"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recorded_at_ms = Column(BigInteger, nullable=False, index=True)
    runtime_profile = Column(String(32), nullable=False)
    feed_status = Column(String(32), nullable=False)
    connection_epoch = Column(Integer, nullable=False, default=0)
    quotes_received = Column(Integer, nullable=False, default=0)
    quotes_accepted = Column(Integer, nullable=False, default=0)
    quotes_rejected = Column(Integer, nullable=False, default=0)
    candles_closed = Column(Integer, nullable=False, default=0)
    queue_depth = Column(Integer, nullable=False, default=0)
    queue_drops = Column(Integer, nullable=False, default=0)
    persistence_status = Column(String(32), nullable=False, default="HEALTHY")
    payload_json = Column(Text, nullable=False)


def compute_shadow_source_hash(payload: Dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash over canonical JSON representation."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ==============================================================================
# Strict Path & Ownership Validation (Section 7.1)
# ==============================================================================

def resolve_and_validate_shadow_db_path(explicit_path: Optional[str] = None) -> Path:
    """
    Resolve and validate AURUM_SHADOW_DB_PATH with fail-closed security guards:
    - Rejects empty / whitespace override if explicitly passed as blank
    - Rejects URI/query manipulation or ':memory:' when file path expected
    - Rejects relative-path traversal ('..')
    - Rejects symlinks on target or any existing parent directory
    - Rejects operational trading DB (aurum_desk.db or active database.DB_PATH)
    - Rejects hardlink aliasing (inode match with operational DB or st_nlink > 1)
    - Rejects directories or direct placement in FORBIDDEN_ROOTS
    - Rejects existing SQLite files that contain operational tables (paper_orders)
    """
    is_pytest = "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST") is not None
    raw_env = explicit_path if explicit_path is not None else os.environ.get(ENV_SHADOW_DB_PATH)

    if raw_env is not None and not str(raw_env).strip():
        raise RuntimeError("SHADOW_DB_PATH_INVALID: Empty AURUM_SHADOW_DB_PATH is not permitted.")

    if raw_env:
        raw_str = str(raw_env).strip()
        if "?" in raw_str or "#" in raw_str or raw_str.startswith("file:") or raw_str == ":memory:":
            raise RuntimeError(
                f"SHADOW_DB_PATH_INVALID: URI parameters or in-memory target not allowed for AURUM_SHADOW_DB_PATH ({raw_str!r})."
            )
        # Check raw path components for traversal ('..') before resolution
        normalized_parts = Path(raw_str.replace("\\", "/")).parts
        if ".." in normalized_parts:
            raise RuntimeError(
                f"SHADOW_DB_PATH_TRAVERSAL_REJECTED: Relative path traversal '..' is prohibited in AURUM_SHADOW_DB_PATH ({raw_str!r})."
            )
        candidate = Path(raw_str)
        if not candidate.is_absolute():
            candidate = database.PROJECT_ROOT / candidate
    else:
        if is_pytest:
            test_root = database.get_or_create_test_root()
            candidate = test_root / "shadow_data" / f"market_shadow_test_{os.getpid()}.db"
        else:
            candidate = database.BASE_DIR / DEFAULT_SHADOW_SUBDIR / DEFAULT_SHADOW_DB_FILENAME

    # 1. Symlink inspection on candidate and every existing parent component BEFORE .resolve()
    check_cursor = candidate
    while True:
        if check_cursor.exists() or check_cursor.is_symlink():
            if check_cursor.is_symlink():
                raise RuntimeError(
                    f"SHADOW_DB_SYMLINK_REJECTED: Symlink target or parent is prohibited for shadow database ({check_cursor})."
                )
        parent = check_cursor.parent
        if parent == check_cursor:
            break
        check_cursor = parent

    resolved = candidate.resolve(strict=False)

    # 2. Reject directory target
    if resolved.exists() and resolved.is_dir():
        raise RuntimeError(
            f"SHADOW_DB_PATH_INVALID: Shadow database path resolves to a directory ({resolved})."
        )

    # 3. Reject operational trading database by name or resolved path
    operational_default = Path(database.RUNTIME_DEFAULT_DB_PATH).resolve(strict=False)
    operational_active = Path(database.DB_PATH).resolve(strict=False) if database.DB_PATH != ":memory:" else None

    if (
        resolved == operational_default
        or (operational_active is not None and resolved == operational_active)
        or "aurum_desk.db" in resolved.name.lower()
    ):
        raise RuntimeError(
            f"SHADOW_DB_OPERATIONAL_CONFLICT: Shadow research DB cannot resolve to operational trading DB ({resolved})."
        )

    # 4. Reject direct placement in FORBIDDEN_ROOTS (must be inside a dedicated subdirectory like shadow_data/ or test_root)
    if resolved.parent in database.FORBIDDEN_ROOTS:
        raise RuntimeError(
            f"SHADOW_DB_FORBIDDEN_ROOT: Shadow database cannot be placed directly in forbidden root directory ({resolved.parent})."
        )

    # 5. Hardlink aliasing check if file already exists
    if resolved.exists():
        st = os.stat(resolved)
        if getattr(st, "st_nlink", 1) > 1:
            raise RuntimeError(
                f"SHADOW_DB_HARDLINK_REJECTED: Shadow database file has multiple hardlinks (st_nlink={st.st_nlink}): {resolved}"
            )
        for op_path in (operational_default, operational_active):
            if op_path is not None and op_path.exists():
                op_st = os.stat(op_path)
                if st.st_ino != 0 and st.st_ino == op_st.st_ino and st.st_dev == op_st.st_dev:
                    raise RuntimeError(
                        f"SHADOW_DB_HARDLINK_REJECTED: Shadow database shares inode with operational database ({resolved})."
                    )

        # 6. Ownership / schema contamination check on existing SQLite file
        if st.st_size > 0:
            try:
                conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=5.0)
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = {r[0] for r in cur.fetchall()}
                finally:
                    conn.close()
            except sqlite3.Error as sqle:
                raise RuntimeError(
                    f"SHADOW_DB_CORRUPT_OR_INVALID: Existing file is not a valid SQLite database ({resolved}): {sqle}"
                ) from sqle

            forbidden_operational_tables = {"paper_orders", "day_audits", "trading_policies", "watch_setups"}
            overlap = tables.intersection(forbidden_operational_tables)
            if overlap:
                raise RuntimeError(
                    f"SHADOW_DB_OWNERSHIP_VIOLATION: Target database contains operational trading tables {sorted(overlap)}: {resolved}"
                )

    return resolved


# ==============================================================================
# ShadowResearchStorage Service
# ==============================================================================

class ShadowResearchStorage:
    """
    Isolated persistence manager for MARKET_SHADOW research entities.
    Maintains a bounded write backlog, retry policy, and explicit health metrics.
    Never touches operational trading tables or creates PaperOrder rows.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        max_queue_size: int = 1000,
        max_retries: int = 3,
    ):
        self._explicit_db_path = db_path
        self._resolved_path: Optional[Path] = None
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._lock = threading.RLock()
        self._max_queue_size = max(10, int(max_queue_size))
        self._max_retries = max(1, int(max_retries))
        self._write_queue: Deque[Tuple[str, Dict[str, Any]]] = deque()

        # Telemetry & persistence quality counters
        self.writes_succeeded: int = 0
        self.writes_failed: int = 0
        self.writes_retried: int = 0
        self.queue_drops: int = 0
        self.has_persistence_gap: bool = False
        self.last_error: Optional[str] = None
        self.last_write_ms: Optional[int] = None
        self._simulate_failure: bool = False

    @property
    def db_path(self) -> Path:
        if self._resolved_path is None:
            self._resolved_path = resolve_and_validate_shadow_db_path(self._explicit_db_path)
        return self._resolved_path

    def initialize(self, db_path_override: Optional[str] = None) -> Path:
        """
        Validate path, create parent directory, initialize isolated SQLite engine,
        create ShadowBase tables, and verify ownership marker.
        """
        with self._lock:
            if db_path_override is not None:
                self.dispose()
                self._explicit_db_path = db_path_override
                self._resolved_path = None

            target_path = resolve_and_validate_shadow_db_path(self._explicit_db_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.parent.is_symlink():
                raise RuntimeError(
                    f"SHADOW_DB_SYMLINK_REJECTED: Parent directory is a symlink ({target_path.parent})."
                )

            eng = create_engine(
                f"sqlite:///{target_path}",
                connect_args={"check_same_thread": False, "timeout": 30},
                poolclass=NullPool,
            )
            # Mark engine explicitly as authorized shadow research storage so database.py guard permits writes
            setattr(eng, "_aurum_shadow_research_engine", True)

            @event.listens_for(eng, "connect")
            def _set_shadow_pragma(dbapi_conn, _conn_rec):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()

            ShadowBase.metadata.create_all(bind=eng)

            # Double-check no operational tables exist in this DB
            insp = inspect(eng)
            table_names = set(insp.get_table_names())
            if "paper_orders" in table_names:
                eng.dispose()
                raise RuntimeError(
                    f"SHADOW_DB_OWNERSHIP_VIOLATION: 'paper_orders' table detected in shadow DB ({target_path})."
                )

            sess_factory = sessionmaker(autocommit=False, autoflush=False, bind=eng)
            session = sess_factory()
            try:
                marker = (
                    session.query(ShadowStorageOwnership)
                    .filter(ShadowStorageOwnership.ownership_marker == SHADOW_OWNERSHIP_MARKER)
                    .first()
                )
                if marker is None:
                    marker = ShadowStorageOwnership(
                        ownership_marker=SHADOW_OWNERSHIP_MARKER,
                        schema_version=1,
                        created_at_ms=int(time.time() * 1000),
                        operational_trading_allowed=False,
                    )
                    session.add(marker)
                    session.commit()
                    increment_safety_counter("SHADOW_DB_WRITES", 1)
            finally:
                session.close()

            self._resolved_path = target_path
            self._engine = eng
            self._session_factory = sess_factory
            return target_path

    def dispose(self) -> None:
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.dispose()
                except Exception:
                    pass
            self._engine = None
            self._session_factory = None

    def _get_session(self) -> Session:
        if self._session_factory is None:
            self.initialize()
        assert self._session_factory is not None
        return self._session_factory()

    def set_simulate_failure(self, fail: bool) -> None:
        """Test hook for verifying negative control #19 (failed research persistence queue is reported)."""
        with self._lock:
            self._simulate_failure = bool(fail)

    @property
    def is_initialized(self) -> bool:
        return self._engine is not None

    def get_health_summary(self) -> Dict[str, Any]:
        return self.get_health_snapshot()

    def get_health_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            q_len = len(self._write_queue)
            degraded = self.has_persistence_gap or self.writes_failed > 0 or self.queue_drops > 0
            return {
                "db_path": str(self._resolved_path) if self._resolved_path else None,
                "initialized": self._engine is not None,
                "status": "DEGRADED" if degraded else "HEALTHY",
                "stream_complete": not degraded,
                "writes_succeeded": self.writes_succeeded,
                "writes_failed": self.writes_failed,
                "writes_retried": self.writes_retried,
                "queue_depth": q_len,
                "max_queue_size": self._max_queue_size,
                "queue_drops": self.queue_drops,
                "has_persistence_gap": self.has_persistence_gap,
                "last_error": self.last_error,
                "last_write_ms": self.last_write_ms,
            }

    def _execute_with_retry(self, fn) -> bool:
        for attempt in range(1, self._max_retries + 1):
            if self._simulate_failure:
                err_msg = "SIMULATED_SHADOW_STORAGE_WRITE_FAILURE"
                with self._lock:
                    if attempt < self._max_retries:
                        self.writes_retried += 1
                    else:
                        self.writes_failed += 1
                        self.has_persistence_gap = True
                        self.last_error = err_msg
                if attempt < self._max_retries:
                    continue
                return False

            session = self._get_session()
            try:
                fn(session)
                session.commit()
                now_ms = int(time.time() * 1000)
                with self._lock:
                    self.writes_succeeded += 1
                    self.last_write_ms = now_ms
                increment_safety_counter("SHADOW_DB_WRITES", 1)
                return True
            except Exception as exc:
                session.rollback()
                with self._lock:
                    if attempt < self._max_retries:
                        self.writes_retried += 1
                    else:
                        self.writes_failed += 1
                        self.has_persistence_gap = True
                        self.last_error = f"{type(exc).__name__}: {exc}"
                        logger.error(f"Shadow research write failed after {attempt} attempts: {exc}")
            finally:
                session.close()
        return False

    def enqueue_write(self, record_type: str, payload: Dict[str, Any]) -> bool:
        """
        Bounded queue staging for burst handling. Overflow sets has_persistence_gap=True
        and increments queue_drops immediately.
        """
        with self._lock:
            if len(self._write_queue) >= self._max_queue_size:
                self.queue_drops += 1
                self.has_persistence_gap = True
                self.last_error = f"SHADOW_STORAGE_QUEUE_OVERFLOW (max={self._max_queue_size})"
                return False
            self._write_queue.append((record_type, dict(payload)))
        return self.flush_queue(max_items=10)

    def flush_queue(self, max_items: int = 100) -> bool:
        processed = 0
        all_ok = True
        while processed < max_items:
            with self._lock:
                if not self._write_queue:
                    break
                rec_type, payload = self._write_queue.popleft()
            processed += 1
            if rec_type == "quote":
                ok = self.record_quote(payload)
            elif rec_type == "candle":
                ok = self.record_closed_candle(payload)
            elif rec_type == "cycle":
                ok = self.record_analysis_cycle(payload)
            elif rec_type == "opportunity":
                ok = self.record_opportunity(payload)
            elif rec_type == "health":
                ok = self.record_runtime_health(payload)
            else:
                ok = False
            if not ok:
                all_ok = False
        return all_ok

    # ------------------------------------------------------------------
    # Entity Writers
    # ------------------------------------------------------------------

    def record_quote(self, quote_data: Dict[str, Any]) -> bool:
        source_hash = quote_data.get("source_hash") or compute_shadow_source_hash(quote_data)
        ex_ts = int(
            quote_data.get("exchange_time_ms")
            if quote_data.get("exchange_time_ms") is not None
            else (quote_data.get("exchange_ts_ms") or 0)
        )
        rx_ts = int(
            quote_data.get("receive_time_ms")
            if quote_data.get("receive_time_ms") is not None
            else (quote_data.get("received_at_ms") or int(time.time() * 1000))
        )
        quote_id = str(
            quote_data.get("quote_id")
            or quote_data.get("source_event_id")
            or f"q_{quote_data.get('symbol', 'XAUUSDT')}_{ex_ts}_{rx_ts}_{source_hash[:8]}"
        )
        freshness_ms = int(
            quote_data["freshness_ms"]
            if "freshness_ms" in quote_data and quote_data["freshness_ms"] is not None
            else round(float(quote_data.get("freshness_sec") or 0.0) * 1000)
        )

        def _op(session: Session):
            existing = (
                session.query(ShadowMarketQuote)
                .filter(ShadowMarketQuote.quote_id == quote_id)
                .first()
            )
            if existing is not None:
                return
            row = ShadowMarketQuote(
                quote_id=quote_id,
                source_event_id=str(quote_data.get("source_event_id") or quote_id),
                symbol=str(quote_data.get("symbol", "XAUUSDT")),
                bid=float(quote_data["bid"]),
                ask=float(quote_data["ask"]),
                last=float(quote_data["last"]) if quote_data.get("last") is not None else None,
                mark_price=float(quote_data["mark_price"]) if quote_data.get("mark_price") is not None else None,
                spread=float(quote_data.get("spread", float(quote_data["ask"]) - float(quote_data["bid"]))),
                exchange_time_ms=ex_ts,
                receive_time_ms=rx_ts,
                decision_cutoff_ms=int(quote_data.get("decision_cutoff_ms") or rx_ts),
                source=str(quote_data.get("source", "WS")),
                connection_epoch=int(quote_data.get("connection_epoch", 1)),
                sequence_order=int(quote_data.get("sequence_order", 0)),
                freshness_ms=freshness_ms,
                is_fresh=bool(quote_data.get("is_fresh", quote_data.get("is_valid", True))),
                data_version=str(quote_data.get("data_version", DATA_VERSION)),
                source_hash=source_hash,
            )
            session.add(row)

        return self._execute_with_retry(_op)

    def record_closed_candle(self, candle_data: Dict[str, Any]) -> bool:
        if not bool(candle_data.get("is_closed", False)):
            raise ValueError("ShadowClosedCandle storage requires is_closed=True.")
        source_hash = candle_data.get("source_hash") or compute_shadow_source_hash(candle_data)
        open_ms = int(
            candle_data["open_time_ms"]
            if "open_time_ms" in candle_data and candle_data["open_time_ms"] is not None
            else candle_data["open_time"]
        )
        close_ms = int(
            candle_data["close_time_ms"]
            if "close_time_ms" in candle_data and candle_data["close_time_ms"] is not None
            else candle_data["close_time"]
        )
        rx_ms = int(
            candle_data.get("receive_time_ms")
            or candle_data.get("received_at")
            or candle_data.get("received_at_ms")
            or close_ms
        )
        candle_id = str(
            candle_data.get("candle_id")
            or f"c_{candle_data.get('symbol', 'XAUUSDT')}_{candle_data['timeframe']}_{open_ms}"
        )

        def _op(session: Session):
            existing = (
                session.query(ShadowClosedCandle)
                .filter(ShadowClosedCandle.candle_id == candle_id)
                .first()
            )
            if existing is not None:
                existing.open = float(candle_data["open"])
                existing.high = float(candle_data["high"])
                existing.low = float(candle_data["low"])
                existing.close = float(candle_data["close"])
                existing.volume = float(candle_data.get("volume", 0.0))
                existing.revision = int(candle_data.get("revision", existing.revision + 1))
                existing.receive_time_ms = rx_ms
                existing.source_hash = source_hash
                return

            row = ShadowClosedCandle(
                candle_id=candle_id,
                source_event_id=str(candle_data.get("source_event_id") or candle_id),
                symbol=str(candle_data.get("symbol", "XAUUSDT")),
                timeframe=str(candle_data["timeframe"]),
                open_time_ms=open_ms,
                close_time_ms=close_ms,
                open=float(candle_data["open"]),
                high=float(candle_data["high"]),
                low=float(candle_data["low"]),
                close=float(candle_data["close"]),
                volume=float(candle_data.get("volume", 0.0)),
                is_closed=True,
                source=str(candle_data.get("source", "WS")),
                connection_epoch=int(candle_data.get("connection_epoch", 1)),
                exchange_time_ms=int(candle_data.get("exchange_time_ms") or close_ms),
                receive_time_ms=rx_ms,
                decision_cutoff_ms=int(candle_data.get("decision_cutoff_ms") or close_ms),
                revision=int(candle_data.get("revision", 1)),
                data_version=str(candle_data.get("data_version", DATA_VERSION)),
                source_hash=source_hash,
            )
            session.add(row)

        return self._execute_with_retry(_op)

    def record_analysis_cycle(self, cycle_data: Dict[str, Any]) -> bool:
        source_hash = cycle_data.get("source_hash") or compute_shadow_source_hash(cycle_data)
        cycle_id = str(cycle_data["cycle_id"])
        started_ms = int(
            cycle_data.get("started_at_ms")
            or cycle_data.get("analysis_time_ms")
            or cycle_data.get("decision_cutoff_ms")
            or int(time.time() * 1000)
        )
        completed_ms = int(
            cycle_data.get("completed_at_ms")
            or cycle_data.get("analysis_time_ms")
            or started_ms
        )
        cutoff_ms = int(cycle_data.get("decision_cutoff_ms") or completed_ms)

        def _op(session: Session):
            existing = (
                session.query(ShadowAnalysisCycle)
                .filter(ShadowAnalysisCycle.cycle_id == cycle_id)
                .first()
            )
            if existing is not None:
                return
            row = ShadowAnalysisCycle(
                cycle_id=cycle_id,
                symbol=str(cycle_data.get("symbol", "XAUUSDT")),
                trigger_reason=str(cycle_data.get("trigger_reason", "CLOSED_CANDLE")),
                started_at_ms=started_ms,
                completed_at_ms=completed_ms,
                decision_cutoff_ms=cutoff_ms,
                connection_epoch=int(cycle_data.get("connection_epoch", 1)),
                feed_state=str(cycle_data.get("feed_state") or cycle_data.get("feed_status") or "CONNECTED_FRESH"),
                m1_bars_count=int(cycle_data.get("m1_bars_count", 0)),
                m5_bars_count=int(cycle_data.get("m5_bars_count", 0)),
                m15_bars_count=int(cycle_data.get("m15_bars_count", 0)),
                models_evaluated_json=json.dumps(cycle_data.get("models_evaluated", []), ensure_ascii=False, default=str),
                detected_count=int(cycle_data.get("detected_count", cycle_data.get("detected_setups_count", 0))),
                unicorn_confirmed_count=int(cycle_data.get("unicorn_confirmed_count", cycle_data.get("confirmed_unicorns_count", 0))),
                eligible_count=int(cycle_data.get("eligible_count", 0)),
                rejected_count=int(cycle_data.get("rejected_count", 0)),
                algorithm_version=str(cycle_data.get("algorithm_version", ALGORITHM_VERSION)),
                policy_snapshot_version=int(cycle_data.get("policy_snapshot_version", 1)),
                source_quote_id=cycle_data.get("source_quote_id"),
                source_candle_ids_json=json.dumps(cycle_data.get("source_candle_ids", [])),
                source_hash=source_hash,
            )
            session.add(row)

        return self._execute_with_retry(_op)

    def record_unicorn_evidence(self, unicorn_data: Dict[str, Any]) -> bool:
        uev_id = str(
            unicorn_data.get("unicorn_evidence_id")
            or unicorn_data.get("unicorn_id")
            or f"uev_{unicorn_data.get('symbol', 'XAUUSDT')}_{unicorn_data.get('as_of_ms') or int(time.time() * 1000)}"
        )
        opp_id = str(unicorn_data.get("opportunity_id") or unicorn_data.get("setup_episode_id") or uev_id)
        cutoff_ms = int(
            unicorn_data.get("decision_cutoff_ms")
            or unicorn_data.get("as_of_ms")
            or int(time.time() * 1000)
        )
        rx_ms = int(unicorn_data.get("receive_time_ms") or cutoff_ms)
        source_hash = unicorn_data.get("source_hash") or compute_shadow_source_hash(unicorn_data)

        def _op(session: Session):
            if session.query(ShadowUnicornEvidence).filter_by(unicorn_evidence_id=uev_id).first() is not None:
                return
            session.add(
                ShadowUnicornEvidence(
                    unicorn_evidence_id=uev_id,
                    opportunity_id=opp_id,
                    symbol=str(unicorn_data.get("symbol", "XAUUSDT")),
                    direction=str(unicorn_data.get("direction", "LONG")),
                    timeframe=str(unicorn_data.get("timeframe", "1M")),
                    is_valid=bool(unicorn_data.get("is_valid", False)),
                    validity_status=str(unicorn_data.get("validity_status", "INVALID")),
                    setup_episode_id=unicorn_data.get("setup_episode_id"),
                    breaker_id=unicorn_data.get("breaker_id"),
                    selected_fvg_id=unicorn_data.get("selected_fvg_id"),
                    source_break_id=unicorn_data.get("source_break_id"),
                    overlap_low=unicorn_data.get("overlap_low"),
                    overlap_high=unicorn_data.get("overlap_high"),
                    invalidation_price=unicorn_data.get("invalidation_price"),
                    decision_cutoff_ms=cutoff_ms,
                    receive_time_ms=rx_ms,
                    rejection_reasons_json=json.dumps(
                        unicorn_data.get("rejection_reasons") or unicorn_data.get("reasons") or []
                    ),
                    payload_json=json.dumps(unicorn_data, ensure_ascii=False, default=str),
                    source_hash=source_hash,
                )
            )

        return self._execute_with_retry(_op)

    def record_stop_decision(self, stop_data: Dict[str, Any]) -> bool:
        opp_id = str(stop_data.get("opportunity_id") or "opp_unknown")
        sdec_id = str(
            stop_data.get("stop_decision_id")
            or stop_data.get("decision_id")
            or f"sdec_{opp_id}"
        )
        cutoff_ms = int(stop_data.get("decision_cutoff_ms") or int(time.time() * 1000))
        source_hash = stop_data.get("source_hash") or compute_shadow_source_hash(stop_data)

        def _op(session: Session):
            if session.query(ShadowStopDecision).filter_by(stop_decision_id=sdec_id).first() is not None:
                return
            session.add(
                ShadowStopDecision(
                    stop_decision_id=sdec_id,
                    opportunity_id=opp_id,
                    symbol=str(stop_data.get("symbol", "XAUUSDT")),
                    model_id=str(stop_data.get("model_id", "SMC_M1_FVG_CONTINUATION")),
                    direction=str(stop_data.get("direction", "LONG")),
                    status=str(stop_data.get("status", "VALID")),
                    legacy_sl=stop_data.get("legacy_sl"),
                    structural_sl=stop_data.get("structural_sl"),
                    unicorn_sl_candidate=stop_data.get("unicorn_sl_candidate"),
                    noise_research_sl=stop_data.get("noise_research_sl"),
                    selected_invalidation_price=stop_data.get("selected_invalidation_price"),
                    selected_anchor_kind=stop_data.get("selected_anchor_kind"),
                    selected_anchor_id=stop_data.get("selected_anchor_id"),
                    atr_m1=stop_data.get("atr_m1"),
                    atr_m5=stop_data.get("atr_m5"),
                    spread=stop_data.get("spread"),
                    buffer_price=stop_data.get("buffer_price"),
                    protective_distance=stop_data.get("protective_distance"),
                    decision_cutoff_ms=cutoff_ms,
                    algorithm_version=str(stop_data.get("algorithm_version", ALGORITHM_VERSION)),
                    policy_snapshot_version=int(stop_data.get("policy_snapshot_version", 1)),
                    variants_comparison_json=json.dumps(
                        stop_data.get("variants_comparison") or stop_data.get("variants") or {},
                        ensure_ascii=False,
                        default=str,
                    ),
                    source_hash=source_hash,
                )
            )

        return self._execute_with_retry(_op)

    def record_target_decision(self, target_data: Dict[str, Any]) -> bool:
        opp_id = str(target_data.get("opportunity_id") or "opp_unknown")
        tdec_id = str(
            target_data.get("target_decision_id")
            or target_data.get("decision_id")
            or f"tdec_{opp_id}"
        )
        cutoff_ms = int(target_data.get("decision_cutoff_ms") or int(time.time() * 1000))
        source_hash = target_data.get("source_hash") or compute_shadow_source_hash(target_data)

        def _op(session: Session):
            if session.query(ShadowTargetDecision).filter_by(target_decision_id=tdec_id).first() is not None:
                return
            session.add(
                ShadowTargetDecision(
                    target_decision_id=tdec_id,
                    opportunity_id=opp_id,
                    symbol=str(target_data.get("symbol", "XAUUSDT")),
                    direction=str(target_data.get("direction", "LONG")),
                    status=str(target_data.get("status", "UNAVAILABLE")),
                    target_price=target_data.get("target_price"),
                    target_evidence_id=target_data.get("target_evidence_id"),
                    target_type=target_data.get("target_type") or target_data.get("target_kind"),
                    reason_code=target_data.get("reason_code"),
                    decision_cutoff_ms=cutoff_ms,
                    payload_json=json.dumps(target_data, ensure_ascii=False, default=str),
                    source_hash=source_hash,
                )
            )

        return self._execute_with_retry(_op)

    def record_opportunity(
        self,
        opp_data: Dict[str, Any],
        unicorn_data: Optional[Dict[str, Any]] = None,
        stop_data: Optional[Dict[str, Any]] = None,
        target_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Persist ShadowOpportunity and optional linked ShadowUnicornEvidence,
        ShadowStopDecision, and ShadowTargetDecision atomically in one transaction.
        Enforces execution_status == 'NEVER_EXECUTED' and valid shadow labels.
        """
        if opp_data.get("execution_status", EXECUTION_STATUS_NEVER) != EXECUTION_STATUS_NEVER:
            raise ValueError("ShadowOpportunity execution_status must be NEVER_EXECUTED.")

        elig_status = str(opp_data.get("shadow_eligibility_status", "SHADOW_REJECTED"))
        default_label = (
            SHADOW_LABEL_ELIGIBLE if elig_status == "SHADOW_ELIGIBLE" else SHADOW_LABEL_REJECTED
        )
        shadow_label = str(opp_data.get("shadow_label", default_label))
        if shadow_label not in ALLOWED_SHADOW_LABELS:
            raise ValueError(f"Invalid shadow_label: {shadow_label!r}")

        lifecycle_state = str(opp_data.get("lifecycle_state", SHADOW_STATE_DETECTED))
        if lifecycle_state not in ALLOWED_SHADOW_STATES:
            raise ValueError(f"Invalid shadow lifecycle_state: {lifecycle_state!r}")

        opp_id = str(opp_data["opportunity_id"])
        cycle_id = str(opp_data.get("cycle_id") or f"cyc_{opp_data.get('decision_cutoff_ms', 0)}")
        source_hash = opp_data.get("source_hash") or compute_shadow_source_hash(opp_data)
        sim_entry = str(
            opp_data.get("simulated_entry_status")
            or opp_data.get("hypothetical_entry_status")
            or "SHADOW_ENTRY_NOT_TRIGGERED"
        )
        sim_exit = str(
            opp_data.get("simulated_exit_status")
            or opp_data.get("hypothetical_exit_status")
            or "EXIT_SEQUENCE_UNVERIFIABLE"
        )

        def _op(session: Session):
            existing = (
                session.query(ShadowOpportunity)
                .filter(ShadowOpportunity.opportunity_id == opp_id)
                .first()
            )
            if existing is not None:
                existing.lifecycle_state = lifecycle_state
                existing.shadow_label = shadow_label
                existing.shadow_eligibility_status = str(opp_data.get("shadow_eligibility_status", existing.shadow_eligibility_status))
                existing.simulated_entry_status = sim_entry
                existing.simulated_exit_status = sim_exit
                if "state_history" in opp_data:
                    existing.state_history_json = json.dumps(opp_data["state_history"], ensure_ascii=False)
                existing.source_hash = source_hash
                return

            row = ShadowOpportunity(
                opportunity_id=opp_id,
                cycle_id=cycle_id,
                symbol=str(opp_data.get("symbol", "XAUUSDT")),
                model_id=str(opp_data["model_id"]),
                direction=str(opp_data["direction"]),
                setup_episode_id=str(opp_data["setup_episode_id"]),
                analysis_time_ms=int(opp_data["analysis_time_ms"]),
                decision_cutoff_ms=int(opp_data["decision_cutoff_ms"]),
                exchange_time_ms=int(opp_data.get("exchange_time_ms") or opp_data["decision_cutoff_ms"]),
                receive_time_ms=int(opp_data.get("receive_time_ms") or opp_data["analysis_time_ms"]),
                connection_epoch=int(opp_data.get("connection_epoch", 1)),
                source=str(opp_data.get("source", "WS")),
                entry_reference=opp_data.get("entry_reference"),
                entry_quote_bid=float(opp_data["entry_quote_bid"]),
                entry_quote_ask=float(opp_data["entry_quote_ask"]),
                legacy_sl=opp_data.get("legacy_sl"),
                structural_sl=opp_data.get("structural_sl"),
                unicorn_sl_candidate=opp_data.get("unicorn_sl_candidate"),
                selected_invalidation_price=opp_data.get("selected_invalidation_price"),
                selected_anchor_kind=opp_data.get("selected_anchor_kind"),
                selected_anchor_id=opp_data.get("selected_anchor_id"),
                atr_m1=opp_data.get("atr_m1"),
                atr_m5=opp_data.get("atr_m5"),
                spread=float(opp_data.get("spread", float(opp_data["entry_quote_ask"]) - float(opp_data["entry_quote_bid"]))),
                protective_distance=opp_data.get("protective_distance"),
                target_price=opp_data.get("target_price"),
                target_evidence_id=opp_data.get("target_evidence_id"),
                risk_budget=float(opp_data.get("risk_budget", 2.50)),
                hypothetical_quantity=opp_data.get("hypothetical_quantity"),
                net_risk=opp_data.get("net_risk"),
                net_reward=opp_data.get("net_reward"),
                net_rr=opp_data.get("net_rr"),
                unicorn_validity=str(opp_data.get("unicorn_validity", "NOT_EVALUATED")),
                unicorn_evidence_id=opp_data.get("unicorn_evidence_id"),
                shadow_label=shadow_label,
                lifecycle_state=lifecycle_state,
                shadow_eligibility_status=elig_status,
                rejection_reason_codes_json=json.dumps(opp_data.get("rejection_reason_codes", []), ensure_ascii=False),
                execution_status=EXECUTION_STATUS_NEVER,
                research_disclosure=RESEARCH_ONLY_BADGE,
                would_enter_at=opp_data.get("would_enter_at"),
                would_use_sl=opp_data.get("would_use_sl"),
                would_use_tp=opp_data.get("would_use_tp"),
                would_use_quantity=opp_data.get("would_use_quantity"),
                simulated_entry_status=sim_entry,
                simulated_exit_status=sim_exit,
                state_history_json=json.dumps(opp_data.get("state_history", []), ensure_ascii=False),
                source_event_id=str(opp_data.get("source_event_id") or opp_id),
                source_quote_id=opp_data.get("source_quote_id"),
                source_candle_ids_json=json.dumps(opp_data.get("source_candle_ids", [])),
                source_break_id=opp_data.get("source_break_id"),
                source_fvg_id=opp_data.get("source_fvg_id"),
                source_breaker_id=opp_data.get("source_breaker_id"),
                data_version=str(opp_data.get("data_version", DATA_VERSION)),
                algorithm_version=str(opp_data.get("algorithm_version", ALGORITHM_VERSION)),
                policy_snapshot_version=int(opp_data.get("policy_snapshot_version", 1)),
                variants_json=json.dumps(
                    opp_data.get("variants") or opp_data.get("comparison_plan") or {},
                    ensure_ascii=False,
                    default=str,
                ),
                source_hash=source_hash,
            )
            session.add(row)

            if unicorn_data is not None:
                uev_id = str(unicorn_data.get("unicorn_evidence_id") or unicorn_data.get("unicorn_id") or f"uev_{opp_id}")
                if session.query(ShadowUnicornEvidence).filter_by(unicorn_evidence_id=uev_id).first() is None:
                    session.add(
                        ShadowUnicornEvidence(
                            unicorn_evidence_id=uev_id,
                            opportunity_id=opp_id,
                            symbol=str(unicorn_data.get("symbol", "XAUUSDT")),
                            direction=str(unicorn_data.get("direction", opp_data["direction"])),
                            timeframe=str(unicorn_data.get("timeframe", "1M")),
                            is_valid=bool(unicorn_data.get("is_valid", False)),
                            validity_status=str(unicorn_data.get("validity_status", "INVALID")),
                            setup_episode_id=unicorn_data.get("setup_episode_id"),
                            breaker_id=unicorn_data.get("breaker_id"),
                            selected_fvg_id=unicorn_data.get("selected_fvg_id"),
                            source_break_id=unicorn_data.get("source_break_id"),
                            overlap_low=unicorn_data.get("overlap_low"),
                            overlap_high=unicorn_data.get("overlap_high"),
                            invalidation_price=unicorn_data.get("invalidation_price"),
                            decision_cutoff_ms=int(unicorn_data.get("decision_cutoff_ms") or opp_data["decision_cutoff_ms"]),
                            receive_time_ms=int(unicorn_data.get("receive_time_ms") or opp_data["analysis_time_ms"]),
                            rejection_reasons_json=json.dumps(unicorn_data.get("rejection_reasons", [])),
                            payload_json=json.dumps(unicorn_data, ensure_ascii=False, default=str),
                            source_hash=compute_shadow_source_hash(unicorn_data),
                        )
                    )

            if stop_data is not None:
                sdec_id = str(stop_data.get("stop_decision_id") or stop_data.get("decision_id") or f"sdec_{opp_id}")
                if session.query(ShadowStopDecision).filter_by(stop_decision_id=sdec_id).first() is None:
                    session.add(
                        ShadowStopDecision(
                            stop_decision_id=sdec_id,
                            opportunity_id=opp_id,
                            symbol=str(stop_data.get("symbol", "XAUUSDT")),
                            model_id=str(stop_data.get("model_id", opp_data["model_id"])),
                            direction=str(stop_data.get("direction", opp_data["direction"])),
                            status=str(stop_data.get("status", "VALID")),
                            legacy_sl=stop_data.get("legacy_sl"),
                            structural_sl=stop_data.get("structural_sl"),
                            unicorn_sl_candidate=stop_data.get("unicorn_sl_candidate"),
                            noise_research_sl=stop_data.get("noise_research_sl"),
                            selected_invalidation_price=stop_data.get("selected_invalidation_price"),
                            selected_anchor_kind=stop_data.get("selected_anchor_kind"),
                            selected_anchor_id=stop_data.get("selected_anchor_id"),
                            atr_m1=stop_data.get("atr_m1"),
                            atr_m5=stop_data.get("atr_m5"),
                            spread=stop_data.get("spread"),
                            buffer_price=stop_data.get("buffer_price"),
                            protective_distance=stop_data.get("protective_distance"),
                            decision_cutoff_ms=int(stop_data.get("decision_cutoff_ms") or opp_data["decision_cutoff_ms"]),
                            algorithm_version=str(stop_data.get("algorithm_version", ALGORITHM_VERSION)),
                            policy_snapshot_version=int(stop_data.get("policy_snapshot_version", 1)),
                            variants_comparison_json=json.dumps(stop_data.get("variants_comparison", {}), ensure_ascii=False, default=str),
                            source_hash=compute_shadow_source_hash(stop_data),
                        )
                    )

            if target_data is not None:
                tdec_id = str(target_data.get("target_decision_id") or target_data.get("decision_id") or f"tdec_{opp_id}")
                if session.query(ShadowTargetDecision).filter_by(target_decision_id=tdec_id).first() is None:
                    session.add(
                        ShadowTargetDecision(
                            target_decision_id=tdec_id,
                            opportunity_id=opp_id,
                            symbol=str(target_data.get("symbol", "XAUUSDT")),
                            direction=str(target_data.get("direction", opp_data["direction"])),
                            status=str(target_data.get("status", "UNAVAILABLE")),
                            target_price=target_data.get("target_price"),
                            target_evidence_id=target_data.get("target_evidence_id"),
                            target_type=target_data.get("target_type"),
                            reason_code=target_data.get("reason_code"),
                            decision_cutoff_ms=int(target_data.get("decision_cutoff_ms") or opp_data["decision_cutoff_ms"]),
                            payload_json=json.dumps(target_data, ensure_ascii=False, default=str),
                            source_hash=compute_shadow_source_hash(target_data),
                        )
                    )

        return self._execute_with_retry(_op)

    def record_replay_result(self, replay_data: Dict[str, Any]) -> bool:
        run_id = str(replay_data["run_id"])
        source_hash = replay_data.get("source_hash") or compute_shadow_source_hash(replay_data)
        d_sha = str(replay_data.get("dataset_sha256") or replay_data.get("dataset_sha") or "")

        def _op(session: Session):
            existing = (
                session.query(ShadowReplayResult)
                .filter(ShadowReplayResult.run_id == run_id)
                .first()
            )
            if existing is not None:
                existing.summary_json = json.dumps(replay_data, ensure_ascii=False, default=str)
                existing.backtest_verdict = str(replay_data["backtest_verdict"])
                existing.source_hash = source_hash
                return
            row = ShadowReplayResult(
                run_id=run_id,
                symbol=str(replay_data.get("symbol", "XAUUSDT")),
                dataset_sha256=d_sha,
                source_sha=str(replay_data["source_sha"]),
                backtest_verdict=str(replay_data["backtest_verdict"]),
                data_quality_status=str(replay_data["data_quality_status"]),
                requested_start_ms=int(replay_data["requested_start_ms"]),
                requested_end_ms=int(replay_data["requested_end_ms"]),
                actual_earliest_ms=replay_data.get("actual_earliest_ms") or replay_data.get("actual_start_ms"),
                actual_latest_ms=replay_data.get("actual_latest_ms") or replay_data.get("actual_end_ms"),
                m1_bars=int(replay_data.get("m1_bars", replay_data.get("m1_bars_count", 0))),
                m5_bars=int(replay_data.get("m5_bars", replay_data.get("m5_bars_count", 0))),
                m15_bars=int(replay_data.get("m15_bars", replay_data.get("m15_bars_count", 0))),
                created_at_ms=int(replay_data.get("created_at_ms") or int(time.time() * 1000)),
                summary_json=json.dumps(replay_data, ensure_ascii=False, default=str),
                source_hash=source_hash,
            )
            session.add(row)

        return self._execute_with_retry(_op)

    def record_runtime_health(self, health_data: Dict[str, Any]) -> bool:
        def _op(session: Session):
            row = ShadowRuntimeHealth(
                recorded_at_ms=int(
                    health_data.get("recorded_at_ms")
                    or health_data.get("timestamp_ms")
                    or int(time.time() * 1000)
                ),
                runtime_profile=str(health_data.get("runtime_profile", "MARKET_SHADOW")),
                feed_status=str(health_data.get("feed_status", "DISCONNECTED")),
                connection_epoch=int(health_data.get("connection_epoch", 0)),
                quotes_received=int(health_data.get("quotes_received", 0)),
                quotes_accepted=int(health_data.get("quotes_accepted", 0)),
                quotes_rejected=int(health_data.get("quotes_rejected", 0)),
                candles_closed=int(health_data.get("candles_closed", 0)),
                queue_depth=int(health_data.get("queue_depth", 0)),
                queue_drops=int(health_data.get("queue_drops", 0)),
                persistence_status=str(health_data.get("persistence_status", "HEALTHY")),
                payload_json=json.dumps(health_data, ensure_ascii=False, default=str),
            )
            session.add(row)

        return self._execute_with_retry(_op)

    # ------------------------------------------------------------------
    # Read-Only Query Methods for Shadow API
    # ------------------------------------------------------------------

    def list_quotes(self, symbol: str = "XAUUSDT", limit: int = 50) -> List[Dict[str, Any]]:
        session = self._get_session()
        try:
            rows = (
                session.query(ShadowMarketQuote)
                .filter(ShadowMarketQuote.symbol == symbol)
                .order_by(ShadowMarketQuote.exchange_time_ms.desc(), ShadowMarketQuote.id.desc())
                .limit(max(1, min(limit, 500)))
                .all()
            )
            return [
                {
                    "quote_id": r.quote_id,
                    "source_event_id": r.source_event_id,
                    "symbol": r.symbol,
                    "bid": r.bid,
                    "ask": r.ask,
                    "last": r.last,
                    "mark_price": r.mark_price,
                    "spread": r.spread,
                    "exchange_time_ms": r.exchange_time_ms,
                    "receive_time_ms": r.receive_time_ms,
                    "decision_cutoff_ms": r.decision_cutoff_ms,
                    "source": r.source,
                    "connection_epoch": r.connection_epoch,
                    "sequence_order": r.sequence_order,
                    "freshness_ms": r.freshness_ms,
                    "is_fresh": r.is_fresh,
                    "data_version": r.data_version,
                    "source_hash": r.source_hash,
                }
                for r in rows
            ]
        finally:
            session.close()

    def list_closed_candles(
        self,
        symbol: str = "XAUUSDT",
        timeframe: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        session = self._get_session()
        try:
            q = session.query(ShadowClosedCandle).filter(ShadowClosedCandle.symbol == symbol)
            if timeframe:
                q = q.filter(ShadowClosedCandle.timeframe == timeframe.upper())
            rows = (
                q.order_by(ShadowClosedCandle.open_time_ms.desc())
                .limit(max(1, min(limit, 1000)))
                .all()
            )
            return [
                {
                    "candle_id": r.candle_id,
                    "source_event_id": r.source_event_id,
                    "symbol": r.symbol,
                    "timeframe": r.timeframe,
                    "open_time": r.open_time_ms,
                    "close_time": r.close_time_ms,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume,
                    "is_closed": r.is_closed,
                    "source": r.source,
                    "connection_epoch": r.connection_epoch,
                    "received_at": r.receive_time_ms,
                    "revision": r.revision,
                    "source_hash": r.source_hash,
                }
                for r in rows
            ]
        finally:
            session.close()

    def _serialize_opportunity(self, r: ShadowOpportunity) -> Dict[str, Any]:
        return {
            "opportunity_id": r.opportunity_id,
            "cycle_id": r.cycle_id,
            "symbol": r.symbol,
            "model_id": r.model_id,
            "direction": r.direction,
            "setup_episode_id": r.setup_episode_id,
            "analysis_time_ms": r.analysis_time_ms,
            "decision_cutoff_ms": r.decision_cutoff_ms,
            "exchange_time_ms": r.exchange_time_ms,
            "receive_time_ms": r.receive_time_ms,
            "connection_epoch": r.connection_epoch,
            "source": r.source,
            "entry_reference": r.entry_reference,
            "entry_quote_bid": r.entry_quote_bid,
            "entry_quote_ask": r.entry_quote_ask,
            "legacy_sl": r.legacy_sl,
            "structural_sl": r.structural_sl,
            "unicorn_sl_candidate": r.unicorn_sl_candidate,
            "selected_invalidation_price": r.selected_invalidation_price,
            "selected_anchor_kind": r.selected_anchor_kind,
            "selected_anchor_id": r.selected_anchor_id,
            "atr_m1": r.atr_m1,
            "atr_m5": r.atr_m5,
            "spread": r.spread,
            "protective_distance": r.protective_distance,
            "target_price": r.target_price,
            "target_evidence_id": r.target_evidence_id,
            "risk_budget": r.risk_budget,
            "hypothetical_quantity": r.hypothetical_quantity,
            "net_risk": r.net_risk,
            "net_reward": r.net_reward,
            "net_rr": r.net_rr,
            "unicorn_validity": r.unicorn_validity,
            "unicorn_evidence_id": r.unicorn_evidence_id,
            "shadow_label": r.shadow_label,
            "lifecycle_state": r.lifecycle_state,
            "shadow_eligibility_status": r.shadow_eligibility_status,
            "rejection_reason_codes": json.loads(r.rejection_reason_codes_json or "[]"),
            "execution_status": r.execution_status,
            "research_disclosure": r.research_disclosure,
            "would_enter_at": r.would_enter_at,
            "would_use_sl": r.would_use_sl,
            "would_use_tp": r.would_use_tp,
            "would_use_quantity": r.would_use_quantity,
            "simulated_entry_status": r.simulated_entry_status,
            "simulated_exit_status": r.simulated_exit_status,
            "hypothetical_entry_status": r.simulated_entry_status,
            "hypothetical_exit_status": r.simulated_exit_status,
            "state_history": json.loads(r.state_history_json or "[]"),
            "source_event_id": r.source_event_id,
            "source_quote_id": r.source_quote_id,
            "source_candle_ids": json.loads(r.source_candle_ids_json or "[]"),
            "source_break_id": r.source_break_id,
            "source_fvg_id": r.source_fvg_id,
            "source_breaker_id": r.source_breaker_id,
            "data_version": r.data_version,
            "algorithm_version": r.algorithm_version,
            "policy_snapshot_version": r.policy_snapshot_version,
            "variants": json.loads(r.variants_json or "{}"),
            "comparison_plan": json.loads(r.variants_json or "{}"),
            "source_hash": r.source_hash,
        }

    def list_opportunities(
        self,
        symbol: str = "XAUUSDT",
        limit: int = 50,
        status: Optional[str] = None,
        eligibility_status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        session = self._get_session()
        eff_status = status or eligibility_status
        try:
            q = session.query(ShadowOpportunity).filter(ShadowOpportunity.symbol == symbol)
            if eff_status:
                q = q.filter(ShadowOpportunity.shadow_eligibility_status == eff_status)
            rows = (
                q.order_by(ShadowOpportunity.analysis_time_ms.desc(), ShadowOpportunity.id.desc())
                .limit(max(1, min(limit, 200)))
                .all()
            )
            return [self._serialize_opportunity(r) for r in rows]
        finally:
            session.close()

    def get_opportunity_by_id(self, opportunity_id: str) -> Optional[Dict[str, Any]]:
        session = self._get_session()
        try:
            r = (
                session.query(ShadowOpportunity)
                .filter(ShadowOpportunity.opportunity_id == opportunity_id)
                .first()
            )
            if r is None:
                return None
            out = self._serialize_opportunity(r)
            uev = (
                session.query(ShadowUnicornEvidence)
                .filter(ShadowUnicornEvidence.opportunity_id == opportunity_id)
                .first()
            )
            sdec = (
                session.query(ShadowStopDecision)
                .filter(ShadowStopDecision.opportunity_id == opportunity_id)
                .first()
            )
            tdec = (
                session.query(ShadowTargetDecision)
                .filter(ShadowTargetDecision.opportunity_id == opportunity_id)
                .first()
            )
            out["unicorn_evidence"] = json.loads(uev.payload_json) if uev else None
            out["stop_decision"] = (
                {
                    "stop_decision_id": sdec.stop_decision_id,
                    "status": sdec.status,
                    "legacy_sl": sdec.legacy_sl,
                    "structural_sl": sdec.structural_sl,
                    "unicorn_sl_candidate": sdec.unicorn_sl_candidate,
                    "noise_research_sl": sdec.noise_research_sl,
                    "selected_invalidation_price": sdec.selected_invalidation_price,
                    "selected_anchor_kind": sdec.selected_anchor_kind,
                    "selected_anchor_id": sdec.selected_anchor_id,
                    "atr_m1": sdec.atr_m1,
                    "atr_m5": sdec.atr_m5,
                    "spread": sdec.spread,
                    "buffer_price": sdec.buffer_price,
                    "protective_distance": sdec.protective_distance,
                    "variants_comparison": json.loads(sdec.variants_comparison_json or "{}"),
                    "source_hash": sdec.source_hash,
                }
                if sdec
                else None
            )
            out["target_decision"] = json.loads(tdec.payload_json) if tdec else None
            return out
        finally:
            session.close()

    def list_unicorn_evidences(
        self,
        symbol: str = "XAUUSDT",
        limit: int = 50,
        validity_status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        session = self._get_session()
        try:
            q = session.query(ShadowUnicornEvidence).filter(ShadowUnicornEvidence.symbol == symbol)
            if validity_status:
                q = q.filter(ShadowUnicornEvidence.validity_status == validity_status)
            rows = (
                q.order_by(ShadowUnicornEvidence.decision_cutoff_ms.desc(), ShadowUnicornEvidence.id.desc())
                .limit(max(1, min(limit, 200)))
                .all()
            )
            return [
                {
                    "unicorn_evidence_id": r.unicorn_evidence_id,
                    "unicorn_id": r.unicorn_evidence_id,
                    "opportunity_id": r.opportunity_id,
                    "symbol": r.symbol,
                    "direction": r.direction,
                    "timeframe": r.timeframe,
                    "is_valid": r.is_valid,
                    "validity_status": r.validity_status,
                    "setup_episode_id": r.setup_episode_id,
                    "breaker_id": r.breaker_id,
                    "selected_fvg_id": r.selected_fvg_id,
                    "source_break_id": r.source_break_id,
                    "overlap_low": r.overlap_low,
                    "overlap_high": r.overlap_high,
                    "invalidation_price": r.invalidation_price,
                    "decision_cutoff_ms": r.decision_cutoff_ms,
                    "receive_time_ms": r.receive_time_ms,
                    "rejection_reasons": json.loads(r.rejection_reasons_json or "[]"),
                    "source_hash": r.source_hash,
                }
                for r in rows
            ]
        finally:
            session.close()

    def list_replay_results(self, symbol: str = "XAUUSDT", limit: int = 20) -> List[Dict[str, Any]]:
        session = self._get_session()
        try:
            rows = (
                session.query(ShadowReplayResult)
                .filter(ShadowReplayResult.symbol == symbol)
                .order_by(ShadowReplayResult.created_at_ms.desc(), ShadowReplayResult.id.desc())
                .limit(max(1, min(limit, 100)))
                .all()
            )
            return [
                {
                    "run_id": r.run_id,
                    "symbol": r.symbol,
                    "dataset_sha256": r.dataset_sha256,
                    "source_sha": r.source_sha,
                    "backtest_verdict": r.backtest_verdict,
                    "data_quality_status": r.data_quality_status,
                    "requested_start_ms": r.requested_start_ms,
                    "requested_end_ms": r.requested_end_ms,
                    "actual_earliest_ms": r.actual_earliest_ms,
                    "actual_latest_ms": r.actual_latest_ms,
                    "m1_bars": r.m1_bars,
                    "m5_bars": r.m5_bars,
                    "m15_bars": r.m15_bars,
                    "created_at_ms": r.created_at_ms,
                    "source_hash": r.source_hash,
                }
                for r in rows
            ]
        finally:
            session.close()

    def get_replay_result_by_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        session = self._get_session()
        try:
            r = (
                session.query(ShadowReplayResult)
                .filter(ShadowReplayResult.run_id == run_id)
                .first()
            )
            if r is None:
                return None
            return json.loads(r.summary_json)
        finally:
            session.close()

    # Convenience aliases for MarketShadowService
    def get_quotes(self, *, symbol: str = "XAUUSDT", limit: int = 50) -> List[Dict[str, Any]]:
        return self.list_quotes(symbol=symbol, limit=limit)

    def get_closed_candles(
        self,
        *,
        symbol: str = "XAUUSDT",
        timeframe: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return self.list_closed_candles(symbol=symbol, timeframe=timeframe, limit=limit)

    def get_opportunities(
        self,
        *,
        symbol: str = "XAUUSDT",
        limit: int = 50,
        status: Optional[str] = None,
        eligibility_status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.list_opportunities(
            symbol=symbol,
            limit=limit,
            status=status,
            eligibility_status=eligibility_status,
        )

    def get_unicorn_evidence_list(
        self,
        *,
        symbol: str = "XAUUSDT",
        limit: int = 50,
        validity_status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.list_unicorn_evidences(symbol=symbol, limit=limit, validity_status=validity_status)

    def get_replay_results(self, *, symbol: str = "XAUUSDT", limit: int = 20) -> List[Dict[str, Any]]:
        return self.list_replay_results(symbol=symbol, limit=limit)


shadow_research_storage = ShadowResearchStorage()
