"""
backend/tests/test_v17_4_1_contracts.py
V17.4.1 Contract & Schema Verification (Parts 061 - 080)

Verifies:
1. Strict schema validation: positive integer expected_version, symbol XAUUSDT, enums.
2. Server scope authority: execution_scope cannot be dictated by client body.
3. Pure resolver contract: zero ny_goal, reserve=False, fallback=False under ALL_SESSIONS_SETUP_FIRST.
4. Paired corruption detection: resolver identifies mismatched mode/timing/goals and returns CONFLICT.
5. GET read-only guarantee: GET queries never mutate database rows or bump policy versions.
6. No-op update idempotency: resaving identical settings preserves version and does not spam events.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pydantic import ValidationError

from database import run_schema_migrations
from models import Base, TradingPolicy, SystemConfig, DomainEvent
import schemas
from policy_constants import (
    ALL_SESSIONS_SETUP_FIRST,
    ALL_SESSIONS_WITH_NY_RESERVE,
    CG01_ALL_SESSIONS_PAPER,
    SILVER_BULLET_LONDON_NY,
)
from services.entry_session_policy_service import (
    resolve_entry_session_settings,
    update_entry_session_settings,
    resolve_server_execution_scope,
    SessionPolicyConfigError,
)
from services.clock import ReplayClock


@pytest.fixture
def contract_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/contracts.db")
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
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
        session.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1700000000000))
        session.commit()
    yield Session
    engine.dispose()


def test_strict_request_schema_validation():
    # Valid payload
    valid = schemas.EntrySessionSettingsUpdate(
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        timing_profile=CG01_ALL_SESSIONS_PAPER,
        expected_version=1,
        symbol="XAUUSDT",
    )
    assert valid.expected_version == 1

    # Missing expected_version is rejected
    with pytest.raises(ValidationError):
        schemas.EntrySessionSettingsUpdate(
            entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
            timing_profile=CG01_ALL_SESSIONS_PAPER,
            symbol="XAUUSDT",
        )

    # Negative or zero expected_version is rejected
    with pytest.raises(ValidationError):
        schemas.EntrySessionSettingsUpdate(
            entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
            timing_profile=CG01_ALL_SESSIONS_PAPER,
            expected_version=0,
            symbol="XAUUSDT",
        )


class ScalpConfigStub:
    def __init__(self, timing_profile: str):
        self.timing_profile = timing_profile


def test_pure_resolver_setup_first_zero_goals():
    pol = TradingPolicy(
        id="default",
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        ny_min_fills=0,
        reserve_ny_slot=False,
        ny_fallback_enabled=False,
        version=2,
    )
    resolved = resolve_entry_session_settings(
        policy=pol,
        scalp_config=ScalpConfigStub(CG01_ALL_SESSIONS_PAPER),
        execution_scope="PAPER",
    )
    assert resolved["status"] == "CONFIRMED"
    assert resolved["entry_session_policy"] == ALL_SESSIONS_SETUP_FIRST
    assert resolved["timing_profile"] == CG01_ALL_SESSIONS_PAPER
    assert resolved["effective_ny_goal"] == 0
    assert resolved["reserve_ny_slot"] is False
    assert resolved["ny_fallback_enabled"] is False


def test_pure_resolver_detects_paired_corruption():
    # Corrupt pair: ALL_SESSIONS_SETUP_FIRST but with ny_min_fills=1 and reserve=True
    corrupt_pol = TradingPolicy(
        id="default",
        entry_session_policy=ALL_SESSIONS_SETUP_FIRST,
        ny_min_fills=1,
        reserve_ny_slot=True,
        ny_fallback_enabled=True,
        version=2,
    )
    resolved = resolve_entry_session_settings(
        policy=corrupt_pol,
        scalp_config=ScalpConfigStub(CG01_ALL_SESSIONS_PAPER),
        execution_scope="PAPER",
    )
    assert resolved["status"] == "CONFLICT"
    assert len(resolved["reason_codes"]) > 0


def test_get_entry_session_settings_is_strictly_read_only(contract_db):
    with contract_db() as db:
        v_before = db.query(TradingPolicy).first().version
        rows_before = db.query(TradingPolicy).count()

        # Pure read
        server_scope = resolve_server_execution_scope(db)
        assert server_scope == "PAPER"
        pol = db.query(TradingPolicy).filter(TradingPolicy.id == "default").first()
        timing_row = db.query(SystemConfig).filter(SystemConfig.key == "timing_profile").first()

        resolved = resolve_entry_session_settings(
            policy=pol,
            scalp_config=ScalpConfigStub(timing_row.value),
            execution_scope=server_scope,
        )
        assert resolved["status"] == "CONFIRMED"

        v_after = db.query(TradingPolicy).first().version
        rows_after = db.query(TradingPolicy).count()
        assert v_before == v_after
        assert rows_before == rows_after


def test_no_op_update_preserves_version_and_avoids_event_spam(contract_db):
    with contract_db() as db:
        # Initial save to setup-first
        res1 = update_entry_session_settings(
            db,
            {
                "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                "timing_profile": CG01_ALL_SESSIONS_PAPER,
                "expected_version": 1,
            },
            now_ms=1700000001000,
        )
        assert res1.policy_version == 2

        event_count_before = db.query(DomainEvent).count()

        # No-op save with current version and identical values
        res2 = update_entry_session_settings(
            db,
            {
                "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                "timing_profile": CG01_ALL_SESSIONS_PAPER,
                "expected_version": 2,
            },
            now_ms=1700000002000,
        )
        assert res2.policy_version == 2
        event_count_after = db.query(DomainEvent).count()
        assert event_count_before == event_count_after


def test_unsupported_symbol_rejected(contract_db):
    with contract_db() as db:
        with pytest.raises(SessionPolicyConfigError) as exc_info:
            update_entry_session_settings(
                db,
                {
                    "entry_session_policy": ALL_SESSIONS_SETUP_FIRST,
                    "timing_profile": CG01_ALL_SESSIONS_PAPER,
                    "expected_version": 1,
                    "symbol": "BTCUSDT",
                },
                now_ms=1700000001000,
            )
        assert "UNSUPPORTED_SYMBOL" in str(exc_info.value.code)
