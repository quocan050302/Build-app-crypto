"""
backend/tests/test_v16_15_auto_permission_and_owner.py
V16.15 Automated Tests for Global Auto Authority, Single Execution Owner Arbitration, and Flag Safety.

Covers:
- Rules 006, 014, 022, 041-050: Global auto cannot be forced on by scheduler is_owner=True.
- Rules 043-046: Single execution owner resolution (resolve_entry_execution_owner).
- Rules 044: Flag validation prevents invalid/contradictory states (e.g., owner without enabled).
- Rules 071-073: resolve_effective_cg01_config content fingerprint and source provenance.
"""
import pytest
from unittest.mock import MagicMock, patch

from database import SessionLocal
import models
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    resolve_entry_execution_owner,
    resolve_effective_cg01_config,
)
from services.cg01_types import CG01Config, CG01ProfileMode


def test_global_auto_off_blocks_execution_even_if_scheduler_is_owner():
    """
    Rules 006, 014, 022, 041: When global auto is OFF, scheduler having is_owner=True
    must NOT force auto_enabled=True down to evaluate_cycle and arm orders.
    """
    db = SessionLocal()
    try:
        sched = CG01EvaluationScheduler()
        sched.enabled = True
        sched.is_owner = True
        sched.shadow_mode = False

        # Mock strategy_service.get_auto_state to return False (Global auto is OFF)
        with patch("services.strategy_service.strategy_service.get_auto_state", return_value=False):
            with patch("services.scalp_watcher_orchestrator.scalp_watcher_orchestrator.evaluate_cycle") as mock_eval:
                mock_eval.return_value = {
                    "status": "COMPLETED",
                    "armed_order_id": None,
                    "cg01_evaluation": {"candidate_key": "c1", "skip_reason": None},
                }

                res = sched.evaluate_symbol_now("XAUUSDT", db=db)

                # Verify evaluate_cycle received auto_enabled=None (which defers to strategy_service get_auto_state),
                # NOT auto_enabled=True!
                assert mock_eval.called
                _, kwargs = mock_eval.call_args
                assert kwargs.get("auto_enabled") is None, "Scheduler must not force auto_enabled=True when is_owner=True"
                assert res["status"] == "COMPLETED"
                assert res["armed_order_id"] is None
    finally:
        db.close()


def test_shadow_mode_forces_auto_enabled_false():
    """
    Rule 043: In shadow mode, auto_enabled must be explicitly False to prevent order creation.
    """
    db = SessionLocal()
    try:
        sched = CG01EvaluationScheduler()
        sched.enabled = True
        sched.is_owner = True
        sched.shadow_mode = True  # Shadow mode active

        with patch("services.scalp_watcher_orchestrator.scalp_watcher_orchestrator.evaluate_cycle") as mock_eval:
            mock_eval.return_value = {"status": "COMPLETED", "cg01_evaluation": {}}
            sched.evaluate_symbol_now("XAUUSDT", db=db)
            assert mock_eval.called
            _, kwargs = mock_eval.call_args
            assert kwargs.get("auto_enabled") is False
    finally:
        db.close()


def test_set_flags_validates_conflicting_configurations():
    """
    Rule 044: set_flags rejects invalid flag combinations, such as owner=True while disabled.
    """
    sched = CG01EvaluationScheduler()

    # Setting owner=True while enabled=False must fail
    with pytest.raises(ValueError, match="Cannot set is_owner=True when scheduler is disabled"):
        sched.set_flags(enabled=False, is_owner=True)

    # Valid settings work smoothly
    sched.set_flags(enabled=True, is_owner=True, shadow_mode=False)
    assert sched.enabled is True
    assert sched.is_owner is True
    assert sched.shadow_mode is False


def test_resolve_entry_execution_owner_arbitration_matrix():
    """
    Rule 046: resolve_entry_execution_owner arbitrates single execution owner based on health and config.
    """
    sched = CG01EvaluationScheduler()

    # Case 1: Scheduler running, enabled, owner, not shadow, auto ON
    sched.enabled = True
    sched.is_owner = True
    sched.shadow_mode = False
    sched._running = True

    with patch("services.strategy_service.strategy_service.get_auto_state", return_value=True):
        res1 = resolve_entry_execution_owner(sched)
        assert res1["execution_owner"] == "SCHEDULER"
        assert res1["watchdog"] == "WATCHER"
        assert res1["can_execute"] is True
        assert res1["reason"] == "SCHEDULER_ACTIVE_OWNER"

    # Case 2: Scheduler is in shadow mode -> owner falls back to WATCHER
    sched.shadow_mode = True
    with patch("services.strategy_service.strategy_service.get_auto_state", return_value=True):
        res2 = resolve_entry_execution_owner(sched)
        assert res2["execution_owner"] == "WATCHER"
        assert res2["reason"] == "SCHEDULER_IN_SHADOW_MODE"

    # Case 3: Scheduler not running -> owner falls back to WATCHER
    sched.shadow_mode = False
    sched._running = False
    with patch("services.strategy_service.strategy_service.get_auto_state", return_value=True):
        res3 = resolve_entry_execution_owner(sched)
        assert res3["execution_owner"] == "WATCHER"
        assert res3["reason"] == "SCHEDULER_NOT_RUNNING"


def test_resolve_effective_cg01_config_fingerprint():
    """
    Rules 071-073: Effective config resolver produces deterministic 16-char content fingerprint.
    """
    cfg1 = CG01Config(model_version="v16.2", min_net_rr=2.0)
    cfg, fp1, src = resolve_effective_cg01_config(base_config=cfg1)
    assert len(fp1) == 16
    assert src == "EXPLICIT_PARAMETER"

    # Changing a parameter produces a different fingerprint
    cfg2 = CG01Config(model_version="v16.2", min_net_rr=2.5)
    _, fp2, _ = resolve_effective_cg01_config(base_config=cfg2)
    assert fp1 != fp2
