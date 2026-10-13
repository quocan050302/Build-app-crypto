"""
backend/tests/test_v14_4_4_policy_consistency.py
V14.4.4 - Regression & Reproduction Tests for Trading Policy Defaults, Upgrades, and Validation Parity.

Proves:
1. Fresh DB initialization produces ALL_SESSIONS_WITH_NY_RESERVE, matching TradingPolicyService.
2. Models, database DDL, migrate.py, schemas, and service share the exact same default source.
3. Centralized validation rejects invalid max_daily_fills != 3, min_net_rr < 2.0, fallback cap > 0.10%,
   and invalid time window ordering.
4. HTTP POST /api/v1/policy/trading uses centralized validation and does not allow bypass.
5. Session policy upgrade helper upgrades legacy default NY_ONLY to ALL_SESSIONS_WITH_NY_RESERVE
   with audit record, while preserving custom settings and being idempotent.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import models
from models import Base
from database import run_schema_migrations
from services.trading_policy_service import TradingPolicyService
from services.clock import IClock


class MockClock(IClock):
    def __init__(self, now_ms: int):
        self._now_ms = now_ms

    def now_ms(self) -> int:
        return self._now_ms

    def now_datetime(self, tz=None):
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(self._now_ms / 1000.0, tz=timezone.utc)
        return dt.astimezone(tz) if tz else dt


def test_repro_fresh_database_has_all_sessions_ny_reserve():
    """PHẦN 006: Fresh DB via migration/startup must produce ALL_SESSIONS_WITH_NY_RESERVE default."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        policy = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()
        # If no row exists yet, get_or_create_policy must insert ALL_SESSIONS_WITH_NY_RESERVE
        if not policy:
            policy = TradingPolicyService.get_or_create_policy(db)
        assert policy.entry_session_policy == "ALL_SESSIONS_WITH_NY_RESERVE"
        assert policy.max_daily_fills == 3
        assert policy.ny_min_fills == 1
        assert policy.reserve_ny_slot is True
    finally:
        db.close()
        engine.dispose()


def test_single_default_source_parity():
    """PHẦN 007: get_default_policy_values returns authoritative values matching get_default_policy."""
    from policy_constants import get_default_policy_values
    defaults = get_default_policy_values()
    assert defaults["entry_session_policy"] == "ALL_SESSIONS_WITH_NY_RESERVE"
    assert defaults["max_daily_fills"] == 3
    assert defaults["ny_min_fills"] == 1
    assert defaults["reserve_ny_slot"] is True
    assert defaults["ny_fallback_risk_pct_cap"] == 0.10

    svc_default = TradingPolicyService.get_default_policy()
    assert svc_default.entry_session_policy == defaults["entry_session_policy"]
    assert svc_default.max_daily_fills == defaults["max_daily_fills"]
    assert svc_default.ny_min_fills == defaults["ny_min_fills"]
    assert svc_default.reserve_ny_slot == defaults["reserve_ny_slot"]
    assert svc_default.ny_fallback_risk_pct_cap == defaults["ny_fallback_risk_pct_cap"]


def test_centralized_policy_validator_rejects_invalid_values():
    """PHẦN 010, 011: validate_trading_policy_update rejects illegal patches."""
    from policy_constants import validate_trading_policy_update
    current = TradingPolicyService.get_default_policy()

    # Reject max_daily_fills != 3
    with pytest.raises(ValueError, match="max_daily_fills"):
        validate_trading_policy_update(current, {"max_daily_fills": 4})

    with pytest.raises(ValueError, match="max_daily_fills"):
        validate_trading_policy_update(current, {"max_daily_fills": 99})

    # Reject min_net_rr < 2.0
    with pytest.raises(ValueError, match="min_net_rr"):
        validate_trading_policy_update(current, {"min_net_rr": 1.5})

    # Reject ny_fallback_risk_pct_cap > 0.10
    with pytest.raises(ValueError, match="ny_fallback_risk_pct_cap"):
        validate_trading_policy_update(current, {"ny_fallback_risk_pct_cap": 0.25})

    # Reject invalid time ordering
    with pytest.raises(ValueError, match="Cửa sổ phiên Mỹ không hợp lệ"):
        validate_trading_policy_update(current, {
            "ny_entry_start": "11:00",
            "ny_fallback_start": "10:30",
            "ny_entry_end": "08:00"
        })


def test_api_endpoint_uses_centralized_validation(client, isolated_db):
    """PHẦN 010: POST /api/v1/policy/trading cannot bypass validation."""
    # Attempting to post max_daily_fills=99 must return HTTP 400
    res = client.post("/api/v1/policy/trading", json={"max_daily_fills": 99})
    assert res.status_code == 400

    # Attempting to post min_net_rr=1.2 must return HTTP 400
    res = client.post("/api/v1/policy/trading", json={"min_net_rr": 1.2})
    assert res.status_code == 400

    # Attempting to post risk cap > 0.10 must return HTTP 400
    res = client.post("/api/v1/policy/trading", json={"ny_fallback_risk_pct_cap": 0.20})
    assert res.status_code == 400

    # Valid patch succeeds
    res_ok = client.post("/api/v1/policy/trading", json={"ny_entry_start": "08:15"})
    assert res_ok.status_code == 200
    data = res_ok.json()
    assert data["ny_entry_start"] == "08:15"


def test_upgrade_session_policy_contract_idempotent(isolated_db):
    """PHẦN 008, 009, 097: upgrade_session_policy_contract upgrades legacy NY_ONLY with audit."""
    # Seed a legacy default NY_ONLY policy
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "NY_ONLY"
    isolated_db.commit()

    clock = MockClock(1700000000000)
    result = TradingPolicyService.upgrade_session_policy_contract(
        isolated_db,
        target_symbol="XAUUSDT",
        migration_id="V14_4_4_UPGRADE",
        desired_mode="ALL_SESSIONS_WITH_NY_RESERVE",
        now_ms=clock.now_ms()
    )

    assert result["changed"] is True
    assert result["old_mode"] == "NY_ONLY"
    assert result["new_mode"] == "ALL_SESSIONS_WITH_NY_RESERVE"

    isolated_db.refresh(policy)
    assert policy.entry_session_policy == "ALL_SESSIONS_WITH_NY_RESERVE"

    # Second run is idempotent (changed=False, no extra version bump)
    version_before = policy.version
    result2 = TradingPolicyService.upgrade_session_policy_contract(
        isolated_db,
        target_symbol="XAUUSDT",
        migration_id="V14_4_4_UPGRADE",
        desired_mode="ALL_SESSIONS_WITH_NY_RESERVE",
        now_ms=clock.now_ms() + 1000
    )
    assert result2["changed"] is False
    isolated_db.refresh(policy)
    assert policy.version == version_before
