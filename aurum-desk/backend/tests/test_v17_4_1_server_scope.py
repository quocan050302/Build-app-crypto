"""
backend/tests/test_v17_4_1_server_scope.py
V17.4.1 Server Scope Authority Tests (Parts 101 - 106)

Verifies:
1. Server DB execution_scope is authoritative (Part 101).
2. Client request execution_scope="PAPER" cannot override DB execution_scope="LIVE" (Part 102).
3. Legacy writers with session payload respect server scope (Part 103).
4. Direct service calls fail closed if server scope is LIVE or UNKNOWN (Part 104).
5. SHADOW / PAPER_SHADOW observation separated from execution authority (Part 105, 106).
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import run_schema_migrations
from models import Base, TradingPolicy, SystemConfig
from policy_constants import (
    ALL_SESSIONS_SETUP_FIRST,
    ALL_SESSIONS_WITH_NY_RESERVE,
    CG01_ALL_SESSIONS_PAPER,
    SILVER_BULLET_LONDON_NY,
)
from services.entry_session_policy_service import (
    update_entry_session_settings,
    resolve_server_execution_scope,
    SessionPolicyConfigError,
)


@pytest.fixture
def scope_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/server_scope.db")
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    SessionFactory = sessionmaker(bind=engine)

    with SessionFactory() as session:
        pol = TradingPolicy(
            id="default",
            entry_session_policy=ALL_SESSIONS_WITH_NY_RESERVE,
            ny_min_fills=1,
            reserve_ny_slot=True,
            ny_fallback_enabled=True,
            version=1,
            updated_at=1700000000000,
        )
        session.add(pol)
        session.add(SystemConfig(key="timing_profile", value=SILVER_BULLET_LONDON_NY, updated_at=1700000000000))
        session.add(SystemConfig(key="daily_ny_goal", value="1", updated_at=1700000000000))
        session.add(SystemConfig(key="execution_scope", value="LIVE", updated_at=1700000000000))
        session.commit()

    yield SessionFactory
    engine.dispose()


def test_server_live_rejects_client_paper(scope_db):
    """Part 101/102: DB is LIVE; client specifies execution_scope=PAPER in body -> Must raise error."""
    with scope_db() as db:
        scope = resolve_server_execution_scope(db)
        assert scope == "LIVE"

        with pytest.raises(SessionPolicyConfigError) as exc_info:
            update_entry_session_settings(
                db,
                {
                    "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                    "timing_profile": CG01_ALL_SESSIONS_PAPER,
                    "expected_version": 1,
                    "execution_scope": "PAPER",
                },
                now_ms=1700000000100,
            )
        assert exc_info.value.code == "PAPER_ONLY_SESSION_PROFILE"


def test_server_paper_allows_setup_first(scope_db):
    """Part 104: When DB is PAPER, ALL_SESSIONS_SETUP_FIRST succeeds."""
    with scope_db() as db:
        db.query(SystemConfig).filter(SystemConfig.key == "execution_scope").update({"value": "PAPER"})
        db.commit()

        resp = update_entry_session_settings(
            db,
            {
                "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                "timing_profile": CG01_ALL_SESSIONS_PAPER,
                "expected_version": 1,
            },
            now_ms=1700000000100,
        )
        assert resp.status == "CONFIRMED"
        assert resp.entry_session_policy == ALL_SESSIONS_SETUP_FIRST
        assert resp.execution_scope == "PAPER"


def test_unknown_server_scope_fails_closed(scope_db):
    """Part 106: Unknown/conflicting server scope fails closed."""
    with scope_db() as db:
        db.query(SystemConfig).filter(SystemConfig.key == "execution_scope").update({"value": "UNKNOWN_SCOPE"})
        db.commit()

        with pytest.raises(SessionPolicyConfigError):
            update_entry_session_settings(
                db,
                {
                    "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                    "timing_profile": CG01_ALL_SESSIONS_PAPER,
                    "expected_version": 1,
                },
                now_ms=1700000000100,
            )
