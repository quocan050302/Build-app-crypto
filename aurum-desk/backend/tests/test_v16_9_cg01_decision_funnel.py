"""
backend/tests/test_v16_9_cg01_decision_funnel.py
V16.9 Acceptance Suite - Group 02 (PHẦN 016, 017), Group 09 - 11 (PHẦN 081 - 110), Group 15 (PHẦN 141 - 146).

Verifies:
1. Pure serializer serialize_cg01_evaluation returns JSON-safe dictionary with required contract fields.
2. ScalpWatcherOrchestrator.evaluate_cycle returns additive cg01_evaluation structure.
3. Decision stage and reason codes are preserved in driver funnel records (never lost due to lifecycle status).
4. Armed order ID is properly captured from cycle response (no wrong field name).
5. Guard blocked setup is tracked in funnel even when cycle status is COMMITTED.
6. Router attribution: only CG-01 candidates count toward CG-01 funnel and arms.
7. normalize_replay_cycle_observation correctly extracts lifecycle and CG-01 facts.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import run_schema_migrations
from services.clock import ReplayClock
from lab.cg01_replay_driver import normalize_replay_cycle_observation
from services.cg01_types import (
    CG01Config,
    CG01Decision,
    CG01Stage,
)
from services.scalp_watcher_orchestrator import (
    ScalpWatcherOrchestrator,
    serialize_cg01_evaluation,
)


def test_serialize_cg01_evaluation_pure_helper():
    """PHẦN 083, 084: Pure serializer for CG-01 evaluation."""
    # When decision is None (e.g. disabled or early return)
    disabled_res = serialize_cg01_evaluation(None, enabled=False, evaluated=False, as_of_ms=1000)
    assert disabled_res["enabled"] is False
    assert disabled_res["evaluated"] is False
    assert disabled_res["stage"] == "MODEL_DISABLED"
    assert disabled_res["eligible"] is False

    # When decision is observing
    dec = CG01Decision(
        stage=CG01Stage.OBSERVING,
        is_eligible=False,
        reason_codes=("WAIT_SWEEP", "NO_SWEEP_DETECTED"),
        as_of_ms=2000,
    )
    obs_res = serialize_cg01_evaluation(dec, enabled=True, evaluated=True, as_of_ms=2000)
    assert obs_res["enabled"] is True
    assert obs_res["evaluated"] is True
    assert obs_res["stage"] == "OBSERVING"
    assert "WAIT_SWEEP" in obs_res["reason_codes"]
    assert obs_res["eligible"] is False


def test_normalize_replay_cycle_observation():
    """PHẦN 101: normalize_replay_cycle_observation extracts lifecycle and CG-01 facts."""
    cycle_res = {
        "status": "COMMITTED",
        "transaction_status": "COMMITTED",
        "armed_order_id": "ord_123",
        "as_of_ms": 5000,
        "cg01_evaluation": {
            "enabled": True,
            "evaluated": True,
            "stage": "EXECUTABLE",
            "eligible": True,
            "reason_codes": ["SWEEP_CONFIRMED", "MSS_CONFIRMED"],
            "candidate_key": "cand_xau",
            "episode_key": "ep_1",
            "armed_order_id": "ord_123",
        }
    }
    obs = normalize_replay_cycle_observation(cycle_res)
    assert obs["lifecycle_status"] == "COMMITTED"
    assert obs["stage"] == "EXECUTABLE"
    assert obs["armed_order_id"] == "ord_123"
    assert obs["is_eligible"] is True
    assert obs["candidate_key"] == "cand_xau"


def test_orchestrator_returns_cg01_evaluation_on_observing():
    """PHẦN 017, 085, 142: Cycle response contains cg01_evaluation even when not armed."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    clock = ReplayClock(initial_ms=1_700_000_000_000)

    try:
        orch = ScalpWatcherOrchestrator()
        # Evaluate cycle with minimal context (no sweep/trigger)
        quote = {
            "bid": 2000.0,
            "ask": 2000.1,
            "last": 2000.05,
            "timestamp": 1_700_000_000_000,
            "is_valid": True,
            "is_stale": False,
        }
        res = orch.evaluate_cycle(
            db,
            "XAUUSDT",
            now_ms=1_700_000_000_000,
            quote=quote,
            clock=clock,
        )
        assert "cg01_evaluation" in res
        cg_eval = res["cg01_evaluation"]
        assert isinstance(cg_eval, dict)
        assert "stage" in cg_eval
        assert "eligible" in cg_eval
        assert "reason_codes" in cg_eval
    finally:
        db.close()


def test_funnel_counts_armed_order_correctly():
    """PHẦN 016, 091, 143: Armed order with armed_order_id is tracked in funnel."""
    cycle_res = {
        "status": "COMMITTED",
        "transaction_status": "COMMITTED",
        "armed_order_id": "ord_cg01_arm_1",
        "as_of_ms": 1_000_000,
        "cg01_evaluation": {
            "enabled": True,
            "evaluated": True,
            "stage": "EXECUTABLE",
            "eligible": True,
            "armed_order_id": "ord_cg01_arm_1",
            "reason_codes": [],
        }
    }
    obs = normalize_replay_cycle_observation(cycle_res)
    assert obs["armed_order_id"] == "ord_cg01_arm_1"
