"""
backend/tests/test_v16_39_17_rr_sync.py
Aurum V16.39.17 — Backend Scenario Cycle & Forward Session RR Synchronization Tests

Verifies:
1. build_current_scenario_config creates an immutable cycle copy without mutating global active_scenario_config.
2. StrategyService.evaluate_market_reading_cycle propagates effective RR from rr_policy_service to scenario cycle.
3. forward_paper_service.create_forward_paper_session inherits current RR when omitted,
   merges partial risk_config without dropping min_net_rr, and respects explicit overrides.
"""

from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SystemConfig
from services.smc_scenario_config import (
    active_scenario_config,
    build_current_scenario_config,
    ScenarioConfig,
)
from services.rr_policy_service import (
    resolve_effective_rr_policy,
    stage_min_net_rr_update,
)
from services.forward_paper_service import create_forward_paper_session, load_forward_session


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_build_current_scenario_config_immutable():
    """Verify build_current_scenario_config replaces min_net_rr without mutating active_scenario_config."""
    base = active_scenario_config
    initial_base_rr = base.min_net_rr

    cycle_cfg = build_current_scenario_config(base, min_net_rr=1.5)
    assert cycle_cfg.min_net_rr == 1.5
    # Base configuration must remain unchanged
    assert base.min_net_rr == initial_base_rr
    assert active_scenario_config.min_net_rr == initial_base_rr

    cycle_custom = build_current_scenario_config(base, min_net_rr=2.5)
    assert cycle_custom.min_net_rr == 2.5
    assert base.min_net_rr == initial_base_rr


def test_scenario_cycle_inherits_effective_rr(test_db):
    """Verify evaluate_market_reading_cycle resolves effective RR and passes cycle config copy."""
    from services.strategy_service import strategy_service

    # Configure canonical SystemConfig to min_net_rr = 1.50
    stage_min_net_rr_update(test_db, 1.50)
    test_db.commit()

    rr_ctx = resolve_effective_rr_policy(test_db)
    assert rr_ctx.min_net_rr == 1.50

    # Spy on evaluate_scenario_cycle to inspect the passed config
    with patch("services.smc_scenario_service.evaluate_scenario_cycle") as mock_eval:
        mock_result = MagicMock()
        mock_result.primary = None
        mock_result.issues = []
        mock_result.changed = []
        mock_eval.return_value = mock_result

        with patch.object(strategy_service, "_load_scenario_candles", return_value={}):
            strategy_service.evaluate_market_reading_cycle(
                test_db,
                "XAUUSDT",
                now_ms=1700000000000,
                now_dt=None,
            )

        assert mock_eval.called
        call_kwargs = mock_eval.call_args.kwargs
        passed_cfg: ScenarioConfig = call_kwargs["config"]
        # The cycle configuration must have received the 1.50 setting!
        assert passed_cfg.min_net_rr == 1.50
        # The global active_scenario_config must NOT have been mutated
        assert active_scenario_config.min_net_rr == 2.0


def test_forward_paper_session_inherits_current_rr(test_db):
    """Verify new forward paper session inherits current RR policy when risk_config is omitted."""
    # Set current settings to 1.75
    stage_min_net_rr_update(test_db, 1.75)
    test_db.commit()

    sess = create_forward_paper_session(test_db, "fwd_test_inherit", virtual_capital=5000.0)
    assert sess.frozen_risk_config["min_net_rr"] == 1.75
    assert sess.frozen_risk_config["risk_pct"] == 0.25


def test_forward_paper_session_partial_risk_config_merges_without_dropping_rr(test_db):
    """Verify partial risk_config merges without dropping current min_net_rr."""
    stage_min_net_rr_update(test_db, 1.50)
    test_db.commit()

    # User only specified risk_pct
    sess = create_forward_paper_session(
        test_db,
        "fwd_test_partial",
        virtual_capital=5000.0,
        risk_config={"risk_pct": 0.50},
    )
    assert sess.frozen_risk_config["risk_pct"] == 0.50
    # Must preserve current min_net_rr 1.50, not drop or fallback to 2.0
    assert sess.frozen_risk_config["min_net_rr"] == 1.50


def test_forward_paper_session_explicit_override_preserved(test_db):
    """Verify explicit research override is preserved even when current setting is different."""
    stage_min_net_rr_update(test_db, 1.50)
    test_db.commit()

    sess = create_forward_paper_session(
        test_db,
        "fwd_test_override",
        virtual_capital=5000.0,
        risk_config={"min_net_rr": 2.50},
    )
    # Explicit override to 2.50 must be preserved
    assert sess.frozen_risk_config["min_net_rr"] == 2.50


def test_forward_paper_session_historical_run_stays_frozen(test_db):
    """Verify existing forward paper session config does not mutate when global settings change."""
    stage_min_net_rr_update(test_db, 1.50)
    test_db.commit()

    sess = create_forward_paper_session(test_db, "fwd_test_frozen", virtual_capital=5000.0)
    assert sess.frozen_risk_config["min_net_rr"] == 1.50

    # User later changes global settings to 3.00
    stage_min_net_rr_update(test_db, 3.00)
    test_db.commit()

    # Previously saved session must remain frozen at 1.50
    saved_sess = load_forward_session(test_db, "fwd_test_frozen")
    assert saved_sess is not None
    assert saved_sess.frozen_risk_config["min_net_rr"] == 1.50
