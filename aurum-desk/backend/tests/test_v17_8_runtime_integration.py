"""
backend/tests/test_v17_8_runtime_integration.py
AURUM DESK V17.8.0.1 — Comprehensive End-to-End Runtime Integration Test Suite.
Covers:
- DEF-178-07: Canonical Unicorn detector interface contract & M1 FVG Continuation evaluator integration.
- DEF-178-08: V17.8 feature flag persistence, strict boolean validation, CAS, no-op, restart reload, and mode separation.
- DEF-178-09: ARM-to-FILL protective distance persistence, breached SL quote guard, and close-quote guard.
- DEF-178-10: Causal episode binding, provenance IDs, anti-lookahead, unclosed HTF rejection, consumed FVG rejection, and invalidation_price safety.
- DEF-178-11: All 12 mandatory runtime scenarios (CANDIDATE -> ARM -> FILL -> CLOSE -> EVIDENCE API) + negative control.
- DEF-178-13: Honest replay comparison and empirical P90 calibration vs EXPERIMENTAL_FIXED_MULTIPLIER.
"""

import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

import crud
import models
from database import SessionLocal, assert_test_database_isolated, engine, run_schema_migrations
from domain_calculator import CostAssumptions, calculate_risk_reward
from main import app
from models import (
    Base,
    OpportunityDecisionEvent,
    OpportunityFunnelRecord,
    PaperObservation,
    PaperOrder,
    SMCScenario,
    SMCScenarioSnapshot,
    SystemConfig,
    WatchSetup,
)
from services.clock import live_clock
from services.entry_guard_service import EntryGuardRequest, evaluate_entry_guard
from services.execution_coordinator import ExecutionCoordinator
from services.quote_validator import ValidatedQuote
from services.scalp_m1_model_service import M1ScalpConfig, ModelIdentifier, ScalpM1ModelService
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.smc_scenario_types import LiquidityLevel
from services.stop_placement_policy_service import (
    StopPolicyValidationError,
    resolve_stop_placement_policy,
    update_stop_placement_policy,
)
from services.stop_placement_service import (
    resolve_stop_anchor,
)
from services.stop_placement_types import (
    StopAnchorKind,
    StopPlacementPolicy,
)
from services.trade_lifecycle_service import TradeLifecycleService
from services.unicorn_evidence_service import (
    detect_breaker_blocks,
    detect_unicorn_evidence,
)
from services.unicorn_evidence_types import (
    UnicornSetupStatus,
)
from services.unicorn_replay_service import (
    NoiseCalibrationSample,
    calibrate_empirical_p90_noise_buffer,
)


@pytest.fixture(autouse=True)
def clean_v17_8_runtime_db():
    assert_test_database_isolated(target_engine=engine)
    run_schema_migrations(engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    assert_test_database_isolated(session=db)
    try:
        db.query(SystemConfig).delete(synchronize_session=False)
        db.query(models.TradingPolicy).delete(synchronize_session=False)
        db.query(PaperOrder).delete(synchronize_session=False)
        db.query(PaperObservation).delete(synchronize_session=False)
        db.query(WatchSetup).delete(synchronize_session=False)
        db.query(SMCScenarioSnapshot).delete(synchronize_session=False)
        db.query(SMCScenario).delete(synchronize_session=False)
        db.query(OpportunityFunnelRecord).delete(synchronize_session=False)
        db.query(OpportunityDecisionEvent).delete(synchronize_session=False)
        db.query(models.DayAudit).delete(synchronize_session=False)
        audit = crud.get_or_create_today_audit(db)
        audit.fills_count = 0
        audit.cooldown_until = None
        audit.consecutive_losses = 0
        audit.is_blocked = False
        audit.block_reason = None
        db.commit()
    finally:
        db.close()
    yield


@dataclass
class RuntimeCandleStub:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 100.0
    open_time_ms: int = 0
    is_closed: bool = True

    def __post_init__(self):
        if not self.open_time_ms:
            self.open_time_ms = self.timestamp

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "open_time_ms": self.open_time_ms,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_closed": self.is_closed,
        }

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


def build_bullish_m1_fvg_continuation_series(
    with_unicorn: bool = True,
    t_offset_ms: int = 0,
) -> Tuple[List[RuntimeCandleStub], List[LiquidityLevel], Dict[str, Any], int]:
    """
    Constructs a causal 18-bar M1 series that satisfies ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate
    for a LONG setup:
    - Confirmed Swing Low & Swing High before displacement break
    - If with_unicorn=True: Bearish down-close OB candle in the pullback leg after the Swing High whose
      range overlaps the Bullish FVG created by the displacement break
    - Retest into the FVG / overlap zone with bullish rejection close.
    """
    t0 = 60_000
    bars = [
        RuntimeCandleStub(t_offset_ms + t0 * 1, 2600.0, 2602.0, 2599.0, 2601.0),
        RuntimeCandleStub(t_offset_ms + t0 * 2, 2601.0, 2603.0, 2597.0, 2600.0),   # Swing Low @ 2597.0
        RuntimeCandleStub(t_offset_ms + t0 * 3, 2600.0, 2604.0, 2599.0, 2603.0),
        RuntimeCandleStub(t_offset_ms + t0 * 4, 2603.0, 2606.0, 2601.0, 2605.0),
        RuntimeCandleStub(t_offset_ms + t0 * 5, 2605.0, 2608.0, 2603.0, 2607.0),
        RuntimeCandleStub(t_offset_ms + t0 * 6, 2607.0, 2610.0, 2605.0, 2609.0),
        RuntimeCandleStub(t_offset_ms + t0 * 7, 2609.0, 2612.0, 2607.0, 2611.0),
        RuntimeCandleStub(t_offset_ms + t0 * 8, 2611.0, 2618.5, 2610.5, 2616.0),   # Swing High @ 2618.5 (idx 7)
        RuntimeCandleStub(t_offset_ms + t0 * 9, 2616.0, 2616.0, 2612.0, 2614.0),
        RuntimeCandleStub(t_offset_ms + t0 * 10, 2614.0, 2615.0, 2611.5, 2613.5),  # Confirms SH @ 2618.5
        RuntimeCandleStub(t_offset_ms + t0 * 11, 2613.5, 2615.5, 2612.0, 2615.0),
        RuntimeCandleStub(t_offset_ms + t0 * 12, 2615.0, 2616.5, 2613.0, 2616.0),
    ]
    if with_unicorn:
        # Candle 13: Bearish down-close OB [2609.5, 2618.0] (not a SH peak because c15 has 2626.0)
        bars.append(RuntimeCandleStub(t_offset_ms + t0 * 13, 2617.0, 2618.0, 2609.5, 2610.5))
    else:
        # Candle 13: Down-close OB [2609.5, 2612.5] which does NOT overlap FVG [2614.0, 2617.0]
        bars.append(RuntimeCandleStub(t_offset_ms + t0 * 13, 2612.0, 2612.5, 2609.5, 2610.0))

    # Candle 14: Pre-displacement bar with high=2614.0, low=2610.0
    bars.append(RuntimeCandleStub(t_offset_ms + t0 * 14, 2610.5, 2614.0, 2610.0, 2613.5))
    # Candle 15: Displacement break bar closing at 2625.0 (> 2615.0 SH)
    bars.append(RuntimeCandleStub(t_offset_ms + t0 * 15, 2613.5, 2626.0, 2613.5, 2625.0))
    # Candle 16: Completes Bullish FVG [2614.0, 2617.0] (c14.high=2614.0, c16.low=2617.0)
    bars.append(RuntimeCandleStub(t_offset_ms + t0 * 16, 2625.0, 2627.0, 2617.0, 2626.0))
    # Candle 17: Retest bar dipping into FVG [2614.0, 2617.0] (low=2615.5) and rejecting up to 2618.5
    bars.append(RuntimeCandleStub(t_offset_ms + t0 * 17, 2626.0, 2626.5, 2615.5, 2618.5))
    # Candle 18: Confirmation hold bar above FVG midpoint
    bars.append(RuntimeCandleStub(t_offset_ms + t0 * 18, 2618.5, 2620.0, 2616.0, 2619.0))

    target_lvl = LiquidityLevel(
        id="target_buy_side_2655",
        price=2655.0,
        side="BUY_SIDE",
        source_kind="SWING",
        occurred_at_ms=t_offset_ms + t0 * 1,
        known_at_ms=t_offset_ms + t0 * 2,
        state="UNTOUCHED",
    )
    now_ms = t_offset_ms + t0 * 19 + 5_000
    quote = {"bid": 2618.5, "ask": 2618.8, "is_valid": True, "timestamp": now_ms}
    return bars, [target_lvl], quote, now_ms


def build_bearish_m1_fvg_continuation_series(
    with_unicorn: bool = True,
) -> Tuple[List[RuntimeCandleStub], List[LiquidityLevel], Dict[str, Any], int]:
    """
    Constructs a causal 18-bar M1 series that satisfies ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate
    for a SHORT setup.
    """
    t0 = 60_000
    bars = [
        RuntimeCandleStub(t0 * 1, 2700.0, 2701.0, 2698.0, 2699.0),
        RuntimeCandleStub(t0 * 2, 2699.0, 2703.0, 2697.0, 2700.0),   # Swing High @ 2703.0
        RuntimeCandleStub(t0 * 3, 2700.0, 2701.0, 2696.0, 2697.0),
        RuntimeCandleStub(t0 * 4, 2697.0, 2699.0, 2694.0, 2695.0),
        RuntimeCandleStub(t0 * 5, 2695.0, 2697.0, 2692.0, 2693.0),
        RuntimeCandleStub(t0 * 6, 2693.0, 2695.0, 2690.0, 2691.0),
        RuntimeCandleStub(t0 * 7, 2691.0, 2693.0, 2688.0, 2689.0),
        RuntimeCandleStub(t0 * 8, 2689.0, 2689.5, 2681.5, 2684.0),   # Swing Low @ 2681.5 (idx 7)
        RuntimeCandleStub(t0 * 9, 2684.0, 2688.0, 2684.0, 2686.0),
        RuntimeCandleStub(t0 * 10, 2686.0, 2688.5, 2685.0, 2686.5),  # Confirms SL @ 2681.5
        RuntimeCandleStub(t0 * 11, 2686.5, 2688.0, 2684.5, 2685.0),
        RuntimeCandleStub(t0 * 12, 2685.0, 2687.0, 2683.5, 2684.0),
    ]
    if with_unicorn:
        # Candle 13 (t=780_000): Bullish up-close OB [2682.0, 2690.5] (not a SL peak because c15 has 2674.0)
        bars.append(RuntimeCandleStub(t0 * 13, 2683.0, 2690.5, 2682.0, 2689.5))
    else:
        # Candle 13 (t=780_000): Up-close OB [2687.5, 2690.5] which does NOT overlap FVG [2683.0, 2686.0]
        bars.append(RuntimeCandleStub(t0 * 13, 2688.0, 2690.5, 2687.5, 2690.0))

    # Candle 14 (t=840_000): Pre-displacement bar with low=2686.0, high=2690.0
    bars.append(RuntimeCandleStub(t0 * 14, 2689.5, 2690.0, 2686.0, 2686.5))
    # Candle 15 (t=900_000): Bearish displacement break bar closing at 2675.0 (< 2685.0 SL)
    bars.append(RuntimeCandleStub(t0 * 15, 2686.5, 2686.5, 2674.0, 2675.0))
    # Candle 16 (t=960_000): Completes Bearish FVG [2683.0, 2686.0] (c16.high=2683.0, c14.low=2686.0)
    bars.append(RuntimeCandleStub(t0 * 16, 2675.0, 2683.0, 2673.0, 2674.0))
    # Candle 17 (t=1020_000): Retest bar rising into FVG [2683.0, 2686.0] (high=2684.5) and rejecting down to 2681.5
    bars.append(RuntimeCandleStub(t0 * 17, 2674.0, 2684.5, 2673.5, 2681.5))
    # Candle 18 (t=1080_000): Confirmation hold bar below FVG midpoint
    bars.append(RuntimeCandleStub(t0 * 18, 2681.5, 2684.0, 2680.0, 2681.0))

    target_lvl = LiquidityLevel(
        id="target_sell_side_2645",
        price=2645.0,
        side="SELL_SIDE",
        source_kind="SWING",
        occurred_at_ms=t0 * 1,
        known_at_ms=t0 * 2,
        state="UNTOUCHED",
    )
    now_ms = t0 * 19 + 5_000
    quote = {"bid": 2681.2, "ask": 2681.5, "is_valid": True, "timestamp": now_ms}
    return bars, [target_lvl], quote, now_ms


# ==============================================================================
# DEF-178-07: UNICORN DETECTOR INTERFACE CONTRACT & EVALUATOR INTEGRATION
# ==============================================================================

def test_def_178_07_unicorn_detector_canonical_selected_fvg_contract():
    """
    DEF-178-07: Verifies detect_unicorn_evidence accepts selected_fvg directly,
    evaluates ONLY the active candidate FVG (never substituting an unrelated FVG),
    and ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate succeeds without TypeError.
    """
    bars, levels, quote, now_ms = build_bullish_m1_fvg_continuation_series(with_unicorn=True)
    cfg = M1ScalpConfig(scalp_m1_enabled=True, scalp_m1_mode="PAPER")
    policy = StopPlacementPolicy(
        mode="PAPER_STRUCTURAL",
        v18_structural_intelligence_enabled=True,
        v18_unicorn_detection_enabled=True,
    )

    cand = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        bars,
        bars,
        quote,
        "BULLISH",
        "BULLISH",
        {"capital_usdt": 1000.0, "risk_pct": 0.50, "leverage": 50},
        now_ms=now_ms,
        config=cfg,
        context_levels=levels,
        stop_policy=policy,
    )
    assert cand.is_eligible is True, f"Expected eligible candidate, got {cand.reason_code}: {cand.missing_conditions}"
    assert cand.model_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value
    assert cand.unicorn_evidence is not None
    assert cand.unicorn_evidence.is_valid is True
    assert cand.unicorn_evidence.validity_status in ("CONFIRMED", "VALID")
    assert cand.stop_placement is not None
    assert cand.stop_placement.anchor is not None
    assert cand.stop_placement.anchor.kind == StopAnchorKind.BREAKER_DISTAL_BOUNDARY.value

    # Verify detector cannot substitute an unrelated FVG when selected_fvg is passed
    unrelated_fvg = {
        "id": "fvg_unrelated_high",
        "type": "BULLISH_FVG",
        "top": 2690.0,
        "bottom": 2685.0,
        "timestamp": 60_000 * 15,
        "confirmed_at": 60_000 * 16,
    }
    valid_fvg = {
        "id": "fvg_valid_overlap",
        "type": "BULLISH_FVG",
        "top": 2617.0,
        "bottom": 2614.0,
        "timestamp": 60_000 * 15,
        "confirmed_at": 60_000 * 16,
    }
    ev_unrelated = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=now_ms,
        selected_fvg=unrelated_fvg,
        candidate_fvgs=[valid_fvg],
    )
    assert ev_unrelated.is_valid is False
    assert ev_unrelated.selected_fvg_id == "fvg_unrelated_high"


def test_def_178_07_negative_control_interface_breakage_caught():
    """
    DEF-178-11 / DEF-178-15 Negative Control:
    Simulates the pre-V17.8.0.1 interface mismatch where detect_unicorn_evidence rejects selected_fvg,
    proving that this test suite immediately catches the defect if reintroduced.
    """
    bars, levels, quote, now_ms = build_bullish_m1_fvg_continuation_series(with_unicorn=True)
    cfg = M1ScalpConfig(scalp_m1_enabled=True, scalp_m1_mode="PAPER")
    policy = StopPlacementPolicy(
        mode="PAPER_STRUCTURAL",
        v18_structural_intelligence_enabled=True,
        v18_unicorn_detection_enabled=True,
    )

    def _broken_detector(*args, **kwargs):
        if "selected_fvg" in kwargs:
            raise TypeError("detect_unicorn_evidence() got an unexpected keyword argument 'selected_fvg'")
        raise RuntimeError("Should have passed selected_fvg")

    with patch("services.unicorn_evidence_service.detect_unicorn_evidence", side_effect=_broken_detector):
        with pytest.raises(TypeError, match="unexpected keyword argument 'selected_fvg'"):
            ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                bars,
                bars,
                quote,
                "BULLISH",
                "BULLISH",
                {"capital_usdt": 1000.0, "risk_pct": 0.50, "leverage": 50},
                now_ms=now_ms,
                config=cfg,
                context_levels=levels,
                stop_policy=policy,
            )


# ==============================================================================
# DEF-178-08: FEATURE FLAG PERSISTENCE, CAS, STRICT BOOL, MODE DISTINCTION
# ==============================================================================

def test_def_178_08_feature_flags_full_lifecycle_and_api():
    """
    DEF-178-08: Verifies default-off, intentional enable, DB persistence, API read/write,
    restart-equivalent reload, strict boolean validation, CAS conflict, no-op,
    LIVE denial, and PAPER_STRUCTURAL vs v18_structural_intelligence_enabled separation.
    """
    client = TestClient(app)

    # 1. Default-off behavior
    r0 = client.get("/api/v1/policy/stop-placement")
    assert r0.status_code == 200
    d0 = r0.json()
    assert d0["mode"] == "SHADOW_COMPARE"
    assert d0["v18_structural_intelligence_enabled"] is False
    assert d0["v18_unicorn_detection_enabled"] is False
    assert d0["v18_noise_research_enabled"] is False
    assert d0["v18_execution_policy_version"] == 1
    v0 = d0["version"]

    # 2. Enable V17.8 flags intentionally
    r1 = client.put(
        "/api/v1/policy/stop-placement",
        json={
            "mode": "PAPER_STRUCTURAL",
            "v18_structural_intelligence_enabled": True,
            "v18_unicorn_detection_enabled": True,
            "v18_noise_research_enabled": True,
            "v18_execution_policy_version": 2,
            "expected_version": v0,
        },
    )
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["version"] == v0 + 1
    assert d1["v18_structural_intelligence_enabled"] is True
    assert d1["v18_unicorn_detection_enabled"] is True
    assert d1["v18_noise_research_enabled"] is True
    assert d1["v18_execution_policy_version"] == 2

    # 3. Restart-equivalent reload from a fresh DB session
    db_fresh = SessionLocal()
    try:
        reloaded = resolve_stop_placement_policy(db_fresh)
        assert reloaded.mode == "PAPER_STRUCTURAL"
        assert reloaded.v18_structural_intelligence_enabled is True
        assert reloaded.v18_unicorn_detection_enabled is True
        assert reloaded.v18_noise_research_enabled is True
        assert reloaded.v18_execution_policy_version == 2
        assert reloaded.version == v0 + 1
    finally:
        db_fresh.close()

    # 4. True no-op does not increment version
    r_noop = client.put(
        "/api/v1/policy/stop-placement",
        json={
            "v18_structural_intelligence_enabled": True,
            "v18_unicorn_detection_enabled": True,
            "expected_version": v0 + 1,
        },
    )
    assert r_noop.status_code == 200
    assert r_noop.json()["version"] == v0 + 1

    # 5. CAS conflict rejected with 409
    r_cas = client.put(
        "/api/v1/policy/stop-placement",
        json={
            "v18_unicorn_detection_enabled": False,
            "expected_version": v0,  # Stale version!
        },
    )
    assert r_cas.status_code == 409

    # 6. Strict boolean validation at service boundary (strings/ints/nulls rejected)
    db_val = SessionLocal()
    try:
        for bad_val in ["true", "1", 1, 0, None, "yes"]:
            with pytest.raises(StopPolicyValidationError):
                update_stop_placement_policy(
                    db_val,
                    payload={"v18_unicorn_detection_enabled": bad_val},
                    expected_version=v0 + 1,
                )
        with pytest.raises(StopPolicyValidationError):
            update_stop_placement_policy(
                db_val,
                payload={"v18_execution_policy_version": True},
                expected_version=v0 + 1,
            )
        with pytest.raises(StopPolicyValidationError):
            update_stop_placement_policy(
                db_val,
                payload={"scope": "LIVE"},
                expected_version=v0 + 1,
            )
    finally:
        db_val.close()

    # 7. Disable enabled flags while preserving unrelated policy values
    r_dis = client.put(
        "/api/v1/policy/stop-placement",
        json={
            "v18_structural_intelligence_enabled": False,
            "v18_unicorn_detection_enabled": False,
            "expected_version": v0 + 1,
        },
    )
    assert r_dis.status_code == 200
    d_dis = r_dis.json()
    assert d_dis["v18_structural_intelligence_enabled"] is False
    assert d_dis["v18_unicorn_detection_enabled"] is False
    assert d_dis["mode"] == "PAPER_STRUCTURAL"  # Unrelated field preserved!


def test_def_178_08_paper_structural_without_v18_flag_uses_v17_7_anchor():
    """
    DEF-178-08: An operator selecting V17.7-style PAPER_STRUCTURAL with
    v18_structural_intelligence_enabled=False must NOT accidentally enable V17.8 Unicorn stops.
    """
    bars, levels, quote, now_ms = build_bullish_m1_fvg_continuation_series(with_unicorn=True)
    cfg = M1ScalpConfig(scalp_m1_enabled=True, scalp_m1_mode="PAPER")
    policy_v17_7 = StopPlacementPolicy(
        mode="PAPER_STRUCTURAL",
        v18_structural_intelligence_enabled=False,
        v18_unicorn_detection_enabled=False,
    )

    cand = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        bars,
        bars,
        quote,
        "BULLISH",
        "BULLISH",
        {"capital_usdt": 1000.0, "risk_pct": 0.50, "leverage": 50},
        now_ms=now_ms,
        config=cfg,
        context_levels=levels,
        stop_policy=policy_v17_7,
    )
    assert cand.is_eligible is True
    assert cand.unicorn_evidence is None
    assert cand.stop_placement.anchor.kind == StopAnchorKind.PROTECTED_LEG_LOW.value


# ==============================================================================
# DEF-178-09: FILL-TIME PROTECTIVE DISTANCE & QUOTE BREACH GUARD
# ==============================================================================

def test_def_178_09_fill_guard_protective_distance_and_breached_quote():
    """
    DEF-178-09: Verifies EntryGuardService and ExecutionCoordinator enforce:
    1. Quote already breaching SL -> INVALID_GEOMETRY_STOP_BREACHED (prioritized before generic calculator).
    2. Quote closer than min_protective_distance -> STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE.
    3. Missing/malformed min_protective_distance on V17.8 structural order -> MISSING/INVALID_PROTECTIVE_DISTANCE.
    """
    now_ms = 1_000_000
    db = SessionLocal()
    try:
        def _vq(bid: float, ask: float) -> ValidatedQuote:
            return ValidatedQuote(
                is_valid=True,
                bid=bid,
                ask=ask,
                last=bid,
                spread=round(ask - bid, 4),
                exchange_time=now_ms,
                observed_at=now_ms,
                freshness_sec=0.1,
                is_stale=False,
                rejection_code=None,
                rejection_detail=None,
            )

        # 1. LONG quote breaching SL (bid=2694.8 <= sl=2695.0)
        req_breach_long = EntryGuardRequest(
            stage="FILL",
            now_ms=now_ms,
            symbol="XAUUSDT",
            direction="LONG",
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            planned_entry=2694.8,
            stop_loss=2695.0,
            take_profit=2715.0,
            validated_quote=_vq(2694.8, 2695.0),
            min_protective_distance=1.50,
            evidence={"v18_structural_intelligence_enabled": True},
        )
        res_breach_long = evaluate_entry_guard(db, req_breach_long)
        assert res_breach_long.allowed is False
        assert "INVALID_GEOMETRY_STOP_BREACHED" in res_breach_long.reason_codes

        # 2. SHORT quote breaching SL (ask=2705.2 >= sl=2705.0)
        req_breach_short = EntryGuardRequest(
            stage="FILL",
            now_ms=now_ms,
            symbol="XAUUSDT",
            direction="SHORT",
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            planned_entry=2705.2,
            stop_loss=2705.0,
            take_profit=2685.0,
            validated_quote=_vq(2705.0, 2705.2),
            min_protective_distance=1.50,
            evidence={"v18_structural_intelligence_enabled": True},
        )
        res_breach_short = evaluate_entry_guard(db, req_breach_short)
        assert res_breach_short.allowed is False
        assert "INVALID_GEOMETRY_STOP_BREACHED" in res_breach_short.reason_codes

        # 3. LONG quote too close to SL (bid=2696.0 - sl=2695.0 = 1.00 < min_protective_distance=1.50)
        req_close_long = EntryGuardRequest(
            stage="FILL",
            now_ms=now_ms,
            symbol="XAUUSDT",
            direction="LONG",
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            planned_entry=2696.2,
            stop_loss=2695.0,
            take_profit=2715.0,
            validated_quote=_vq(2696.0, 2696.2),
            min_protective_distance=1.50,
            evidence={"v18_structural_intelligence_enabled": True},
        )
        res_close_long = evaluate_entry_guard(db, req_close_long)
        assert res_close_long.allowed is False
        assert "STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE" in res_close_long.reason_codes

        # 4. SHORT quote too close to SL (sl=2705.0 - ask=2704.0 = 1.00 < min_protective_distance=1.50)
        req_close_short = EntryGuardRequest(
            stage="FILL",
            now_ms=now_ms,
            symbol="XAUUSDT",
            direction="SHORT",
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            planned_entry=2703.8,
            stop_loss=2705.0,
            take_profit=2685.0,
            validated_quote=_vq(2703.8, 2704.0),
            min_protective_distance=1.50,
            evidence={"v18_structural_intelligence_enabled": True},
        )
        res_close_short = evaluate_entry_guard(db, req_close_short)
        assert res_close_short.allowed is False
        assert "STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE" in res_close_short.reason_codes

        # 5. Missing min_protective_distance when v18_structural_intelligence_enabled=True fails closed
        req_missing_dist = EntryGuardRequest(
            stage="FILL",
            now_ms=now_ms,
            symbol="XAUUSDT",
            direction="LONG",
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            planned_entry=2700.0,
            stop_loss=2690.0,
            take_profit=2725.0,
            validated_quote=_vq(2699.8, 2700.0),
            min_protective_distance=None,
            evidence={"v18_structural_intelligence_enabled": True},
        )
        res_missing_dist = evaluate_entry_guard(db, req_missing_dist)
        assert res_missing_dist.allowed is False
        assert "MISSING_PROTECTIVE_DISTANCE" in res_missing_dist.reason_codes

        # 6. Malformed min_protective_distance (-1.0) fails closed
        req_bad_dist = EntryGuardRequest(
            stage="FILL",
            now_ms=now_ms,
            symbol="XAUUSDT",
            direction="LONG",
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            planned_entry=2700.0,
            stop_loss=2690.0,
            take_profit=2725.0,
            validated_quote=_vq(2699.8, 2700.0),
            min_protective_distance=-1.0,
            evidence={"v18_structural_intelligence_enabled": True},
        )
        res_bad_dist = evaluate_entry_guard(db, req_bad_dist)
        assert res_bad_dist.allowed is False
        assert "INVALID_PROTECTIVE_DISTANCE" in res_bad_dist.reason_codes
    finally:
        db.close()


# ==============================================================================
# DEF-178-10: CAUSAL UNICORN PROVENANCE & ANTI-LOOKAHEAD
# ==============================================================================

def test_def_178_10_causal_unicorn_provenance_and_negative_controls():
    """
    DEF-178-10: Verifies causal episode binding, provenance IDs, future swing rejection,
    unclosed HTF bar rejection, consumed FVG rejection, wrong-direction overlap rejection,
    retest-before-confirmation rejection, duplicate breaker deduplication, and invalidation_price=None when invalid.
    """
    bars, _, _, now_ms = build_bullish_m1_fvg_continuation_series(with_unicorn=True)
    t0 = 60_000
    swings = (
        [{"id": "sh_8", "price": 2618.5, "timestamp": t0 * 8, "confirmed_at": t0 * 10, "side": "BUY_SIDE"}],
        [{"id": "sl_13", "price": 2609.5, "timestamp": t0 * 13, "confirmed_at": t0 * 14, "side": "SELL_SIDE"}],
    )
    fvg_valid = {
        "id": "fvg_bull_15",
        "type": "BULLISH_FVG",
        "top": 2617.0,
        "bottom": 2614.0,
        "timestamp": t0 * 15,
        "confirmed_at": t0 * 16,
        "state": "confirmed",
    }

    # 1. Valid same-episode Bullish Unicorn with full causal provenance
    ev_ok = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=now_ms,
        selected_fvg=fvg_valid,
        confirmed_swings=swings,
    )
    assert ev_ok.is_valid is True
    assert ev_ok.selected_fvg_id == "fvg_bull_15"
    assert ev_ok.breaker_id is not None
    assert ev_ok.breaker_source_ob_id is not None
    assert ev_ok.broken_swing_id == "sh_8"
    assert ev_ok.mss_or_bos_id is not None
    assert ev_ok.displacement_id is not None
    assert ev_ok.setup_episode_id is not None
    assert ev_ok.confirmed_at_ms is not None and ev_ok.confirmed_at_ms <= now_ms
    assert ev_ok.known_at_ms is not None and ev_ok.known_at_ms <= now_ms
    assert ev_ok.invalidation_price == 2609.5

    # 2. Unrelated historical OB + much later FVG rejected as EPISODE_MISMATCH
    fvg_late = {
        "id": "fvg_late_episode",
        "type": "BULLISH_FVG",
        "top": 2617.0,
        "bottom": 2614.0,
        "timestamp": t0 * 35,  # 20 bars after break at t0*15 (> max_fvg_lag_bars=3)
        "confirmed_at": t0 * 36,
        "state": "confirmed",
    }
    ev_mismatch = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=t0 * 40,
        selected_fvg=fvg_late,
        confirmed_swings=swings,
    )
    assert ev_mismatch.is_valid is False
    assert ev_mismatch.validity_status == UnicornSetupStatus.EPISODE_MISMATCH.value
    assert ev_mismatch.invalidation_price is None

    # 3. Future swing (confirmed_at > as_of_ms) rejected as FUTURE_DATA_REJECTED
    swings_future = (
        [{"id": "sh_future", "price": 2618.5, "timestamp": t0 * 8, "confirmed_at": now_ms + 60_000, "side": "BUY_SIDE"}],
        [],
    )
    ev_future = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=now_ms,
        selected_fvg=fvg_valid,
        confirmed_swings=swings_future,
    )
    assert ev_future.is_valid is False
    assert ev_future.validity_status == UnicornSetupStatus.FUTURE_DATA_REJECTED.value
    assert ev_future.invalidation_price is None

    # 4. Unclosed HTF bar or unclosed FVG rejected as UNCLOSED_EVIDENCE_REJECTED
    ev_unclosed = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=now_ms,
        selected_fvg=fvg_valid,
        confirmed_swings=swings,
        htf_evidence=[
            {
                "id": "htf_15m_unclosed",
                "timeframe": "15M",
                "is_closed": False,
                "open_time": t0 * 15,
                "close_time": now_ms + 300_000,
            }
        ],
    )
    assert ev_unclosed.is_valid is False
    assert ev_unclosed.validity_status == UnicornSetupStatus.UNCLOSED_EVIDENCE_REJECTED.value
    assert ev_unclosed.invalidation_price is None

    ev_unclosed_fvg = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=now_ms,
        selected_fvg=dict(fvg_valid, is_closed=False),
        confirmed_swings=swings,
    )
    assert ev_unclosed_fvg.is_valid is False
    assert ev_unclosed_fvg.validity_status == UnicornSetupStatus.UNCLOSED_EVIDENCE_REJECTED.value
    assert ev_unclosed_fvg.invalidation_price is None

    # 5. Consumed/mitigated FVG rejected as CONSUMED_OR_INVALIDATED_FVG
    fvg_consumed = dict(fvg_valid, state="MITIGATED", is_consumed=True)
    ev_consumed = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=now_ms,
        selected_fvg=fvg_consumed,
        confirmed_swings=swings,
    )
    assert ev_consumed.is_valid is False
    assert ev_consumed.validity_status == UnicornSetupStatus.CONSUMED_OR_INVALIDATED_FVG.value
    assert ev_consumed.invalidation_price is None

    # 6. Wrong-direction FVG overlap rejected as WRONG_DIRECTION_OVERLAP
    fvg_wrong_dir = dict(fvg_valid, type="BEARISH_FVG", direction="SHORT")
    ev_wrong_dir = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=now_ms,
        selected_fvg=fvg_wrong_dir,
        confirmed_swings=swings,
    )
    assert ev_wrong_dir.is_valid is False
    assert ev_wrong_dir.validity_status == UnicornSetupStatus.WRONG_DIRECTION_OVERLAP.value
    assert ev_wrong_dir.invalidation_price is None

    # 7. Retest before FVG confirmation rejected
    fvg_confirmed_after_retest = dict(fvg_valid, confirmed_at=t0 * 18)
    bars_early_retest = list(bars[:-1]) + [
        RuntimeCandleStub(t0 * 18, 2618.5, 2622.0, 2618.0, 2621.0)
    ]
    ev_early_retest = detect_unicorn_evidence(
        closed_bars=bars_early_retest,
        direction="LONG",
        as_of_ms=now_ms,
        selected_fvg=fvg_confirmed_after_retest,
        confirmed_swings=swings,
    )
    assert ev_early_retest.is_valid is False
    assert ev_early_retest.validity_status == UnicornSetupStatus.RETEST_BEFORE_CONFIRMATION.value
    assert ev_early_retest.invalidation_price is None

    # 8. Duplicate Breaker from same OB candle when multiple swings are broken is deduplicated
    swings_multi = (
        [
            {"id": "sh_a", "price": 2614.5, "timestamp": t0 * 6, "confirmed_at": t0 * 8, "side": "BUY_SIDE"},
            {"id": "sh_b", "price": 2618.5, "timestamp": t0 * 8, "confirmed_at": t0 * 10, "side": "BUY_SIDE"},
        ],
        [],
    )
    breakers = detect_breaker_blocks(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=now_ms,
        confirmed_swings=swings_multi,
    )
    ob_ids = [b.source_ob_id for b in breakers]
    assert len(ob_ids) == len(set(ob_ids)), "Duplicate Breaker blocks from the same OB candle must be deduplicated"


# ==============================================================================
# DEF-178-11: MANDATORY 12 END-TO-END RUNTIME SCENARIOS
# ==============================================================================

def _arm_and_fill_candidate(
    db,
    cand,
    now_ms: int,
    arm_quote: ValidatedQuote,
    fill_quote: ValidatedQuote,
    policy_settings: Dict[str, Any],
    order_id: str,
):
    orch = ScalpWatcherOrchestrator()
    cand.order_id = order_id
    armed_order = orch._arm_paper_candidate(
        db,
        cand,
        now_ms=now_ms,
        clock=live_clock,
        quote=arm_quote,
        policy_settings=policy_settings,
        auto_enabled=True,
    )
    assert armed_order is not None, "Expected candidate to ARM into PaperOrder"
    armed_order.id = order_id
    db.commit()

    # Process fill through real ExecutionCoordinator
    coord = ExecutionCoordinator()
    fills = coord.evaluate_orders_sync(
        db=db,
        ticker_override={
            "bid": fill_quote.bid,
            "ask": fill_quote.ask,
            "last": fill_quote.last,
            "timestamp": now_ms + 1_000,
        },
        now_ms=now_ms + 1_000,
    )
    db.commit()
    reloaded = db.query(PaperOrder).filter(PaperOrder.id == order_id).first()
    return armed_order, fills, reloaded


def test_def_178_11_scenarios_01_to_12_end_to_end_runtime():
    """
    DEF-178-11: Executes all 12 mandatory runtime scenarios end-to-end across
    Policy -> Evaluator -> Stop Engine -> Calculator -> Orchestrator ARM -> Coordinator FILL -> Lifecycle CLOSE -> Evidence API.
    """
    spy = Mock()

    def _forbidden_live_order(*args, **kwargs):
        spy(*args, **kwargs)
        raise RuntimeError("FORBIDDEN_LIVE_ORDER_SUBMISSION")

    with patch("httpx.Client.post", side_effect=_forbidden_live_order), \
         patch("httpx.AsyncClient.post", side_effect=_forbidden_live_order), \
         patch(
             "services.trading_policy_service.TradingPolicyService.evaluate_entry_policy",
             return_value={"allowed": True, "reason_code": "OK", "reason_message": "OK"},
         ):

        db = SessionLocal()
        try:
            import crud
            from services.risk_settings_service import risk_settings_service

            db.query(PaperOrder).delete()
            audit = crud.get_or_create_today_audit(db)
            audit.fills_count = 0
            audit.realized_pnl_today = 0.0
            audit.current_equity = 1000.0
            audit.initial_equity = 1000.0
            audit.is_blocked = False
            audit.block_reason = None
            audit.cooldown_until = None
            db.commit()

            global_settings = risk_settings_service.get_settings(db)
            cfg_ver = getattr(global_settings, "config_version", 1) or 1

            # ------------------------------------------------------------------
            # Scenario 10: Policy update & reload persistence
            # ------------------------------------------------------------------
            pol_init = resolve_stop_placement_policy(db)
            pol_v18, is_mut = update_stop_placement_policy(
                db,
                payload={
                    "mode": "PAPER_STRUCTURAL",
                    "v18_structural_intelligence_enabled": True,
                    "v18_unicorn_detection_enabled": True,
                    "v18_noise_research_enabled": True,
                    "v18_execution_policy_version": 2,
                },
                expected_version=pol_init.version,
            )
            assert is_mut is True
            pol_reloaded = resolve_stop_placement_policy(db)
            assert pol_reloaded.v18_structural_intelligence_enabled is True
            assert pol_reloaded.v18_unicorn_detection_enabled is True

            cfg = M1ScalpConfig(scalp_m1_enabled=True, scalp_m1_mode="PAPER")
            policy_settings = {"capital_usdt": 1000.0, "risk_pct": 0.50, "leverage": 50, "config_version": cfg_ver}

            # ------------------------------------------------------------------
            # Scenario 1: Valid Bearish M1 FVG Continuation with Unicorn
            # ------------------------------------------------------------------
            s_bars, s_levels, s_quote_dict, s_now = build_bearish_m1_fvg_continuation_series(with_unicorn=True)
            cand_s1 = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                s_bars,
                s_bars,
                s_quote_dict,
                "BEARISH",
                "BEARISH",
                policy_settings,
                now_ms=s_now,
                config=cfg,
                context_levels=s_levels,
                stop_policy=pol_reloaded,
            )
            assert cand_s1.is_eligible is True
            assert cand_s1.direction == "SHORT"
            assert cand_s1.unicorn_evidence is not None and cand_s1.unicorn_evidence.is_valid is True
            assert cand_s1.stop_placement.anchor.kind == StopAnchorKind.BREAKER_DISTAL_BOUNDARY.value
            assert cand_s1.stop_loss > cand_s1.unicorn_evidence.invalidation_price  # Outward buffer applied above 2690.5

            v_quote_s1 = ValidatedQuote(
                is_valid=True,
                bid=cand_s1.planned_entry,
                ask=cand_s1.planned_entry + 0.20,
                last=cand_s1.planned_entry,
                spread=0.20,
                exchange_time=s_now,
                observed_at=s_now,
                freshness_sec=0.1,
                is_stale=False,
                rejection_code=None,
                rejection_detail=None,
            )
            _, _, order_s1 = _arm_and_fill_candidate(
                db, cand_s1, s_now, v_quote_s1, v_quote_s1, policy_settings, "ord_scen_01_bear_unicorn"
            )
            assert order_s1.state == "paper_open", f"Expected paper_open, got {order_s1.state} ({order_s1.exit_cause})"
            assert math.isclose(order_s1.stop_loss, cand_s1.stop_loss, abs_tol=1e-4)
            closed_s1 = TradeLifecycleService.execute_close(
                db, order_id=order_s1.id, exit_price=order_s1.take_profit, exit_cause="TP_HIT", now_ms=s_now + 5_000
            )
            assert closed_s1.state == "closed"

            # ------------------------------------------------------------------
            # Scenario 2: Valid Bullish M1 FVG Continuation with Unicorn
            # ------------------------------------------------------------------
            l_bars, l_levels, l_quote_dict, l_now = build_bullish_m1_fvg_continuation_series(
                with_unicorn=True, t_offset_ms=3_600_000
            )
            cand_s2 = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                l_bars,
                l_bars,
                l_quote_dict,
                "BULLISH",
                "BULLISH",
                policy_settings,
                now_ms=l_now,
                config=cfg,
                context_levels=l_levels,
                stop_policy=pol_reloaded,
            )
            assert cand_s2.is_eligible is True
            assert cand_s2.direction == "LONG"
            assert cand_s2.unicorn_evidence is not None and cand_s2.unicorn_evidence.is_valid is True
            assert cand_s2.stop_placement.anchor.kind == StopAnchorKind.BREAKER_DISTAL_BOUNDARY.value
            assert cand_s2.stop_loss < cand_s2.unicorn_evidence.invalidation_price  # Outward buffer applied below 2609.5

            v_quote_s2 = ValidatedQuote(
                is_valid=True,
                bid=cand_s2.planned_entry - 0.20,
                ask=cand_s2.planned_entry,
                last=cand_s2.planned_entry,
                spread=0.20,
                exchange_time=l_now,
                observed_at=l_now,
                freshness_sec=0.1,
                is_stale=False,
                rejection_code=None,
                rejection_detail=None,
            )
            _, _, order_s2 = _arm_and_fill_candidate(
                db, cand_s2, l_now, v_quote_s2, v_quote_s2, policy_settings, "ord_scen_02_bull_unicorn"
            )
            assert order_s2.state == "paper_open", f"Expected paper_open, got {order_s2.state} ({order_s2.exit_cause})"
            closed_s2 = TradeLifecycleService.execute_close(
                db, order_id=order_s2.id, exit_price=order_s2.take_profit, exit_cause="TP_HIT", now_ms=l_now + 5_000
            )
            assert closed_s2.state == "closed"

            # ------------------------------------------------------------------
            # Scenario 3: Valid M1 FVG Continuation without Unicorn
            # ------------------------------------------------------------------
            no_u_bars, no_u_levels, no_u_quote, no_u_now = build_bullish_m1_fvg_continuation_series(with_unicorn=False)
            cand_s3 = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                no_u_bars,
                no_u_bars,
                no_u_quote,
                "BULLISH",
                "BULLISH",
                policy_settings,
                now_ms=no_u_now,
                config=cfg,
                context_levels=no_u_levels,
                stop_policy=pol_reloaded,
            )
            assert cand_s3.is_eligible is True
            assert cand_s3.unicorn_evidence is not None and cand_s3.unicorn_evidence.is_valid is False
            assert cand_s3.stop_placement.anchor.kind in (
                StopAnchorKind.PROTECTED_LEG_LOW.value,
                StopAnchorKind.DISPLACEMENT_ORIGIN.value,
            )

            # ------------------------------------------------------------------
            # Scenario 4: Invalid Unicorn (distal boundary breached) in PAPER_STRUCTURAL fails closed
            # ------------------------------------------------------------------
            inv_bars = list(l_bars)
            inv_ev = detect_unicorn_evidence(
                closed_bars=inv_bars[:-1] + [RuntimeCandleStub(3_600_000 + 60_000 * 18, 2618.0, 2618.5, 2605.0, 2606.0)],
                direction="LONG",
                as_of_ms=l_now,
                selected_fvg={
                    "id": "fvg_inv",
                    "type": "BULLISH_FVG",
                    "top": 2617.0,
                    "bottom": 2614.0,
                    "timestamp": 3_600_000 + 60_000 * 15,
                    "confirmed_at": 3_600_000 + 60_000 * 16,
                },
            )
            assert inv_ev.validity_status == UnicornSetupStatus.UNICORN_INVALIDATED.value
            res_s4_anchor = resolve_stop_anchor(
                model_id="M1_FVG_CONTINUATION",
                direction="LONG",
                closed_bars=inv_bars,
                as_of_ms=l_now,
                unicorn_evidence=inv_ev,
                policy=pol_reloaded,
            )
            assert res_s4_anchor.status in ("INVALID", "INVALID_DATA")
            assert "UNICORN_INVALIDATED" in res_s4_anchor.reasons

            # ------------------------------------------------------------------
            # Scenario 5: Distant structural SL reduces size or rejects by R:R
            # ------------------------------------------------------------------
            calc_near = calculate_risk_reward(
                direction="LONG",
                planned_entry=2620.0,
                stop_loss=2615.0,
                take_profit=2660.0,
                capital=1000.0,
                risk_pct=0.25,
            )
            calc_distant = calculate_risk_reward(
                direction="LONG",
                planned_entry=2620.0,
                stop_loss=2608.0,
                take_profit=2660.0,
                capital=1000.0,
                risk_pct=0.25,
            )
            assert calc_distant.can_execute is True
            assert calc_distant.quantity < calc_near.quantity  # Automatically downsized to preserve 2.50 USDT risk budget!
            assert calc_distant.net_risk_usdt <= 2.55

            calc_poor_rr = calculate_risk_reward(
                direction="LONG",
                planned_entry=2620.0,
                stop_loss=2600.0,
                take_profit=2632.0,
                capital=1000.0,
                risk_pct=0.25,
                min_net_rr=1.5,
            )
            assert calc_poor_rr.can_execute is False  # Rejected when Net R:R < min_net_rr

            # ------------------------------------------------------------------
            # Scenario 6: Fill-time quote too close to SL rejected by Coordinator
            # ------------------------------------------------------------------
            orch = ScalpWatcherOrchestrator()
            s6_bars, s6_levels, s6_quote_dict, s6_now = build_bullish_m1_fvg_continuation_series(
                with_unicorn=True, t_offset_ms=7_200_000
            )
            cand_s6 = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                s6_bars, s6_bars, s6_quote_dict, "BULLISH", "BULLISH", policy_settings,
                now_ms=s6_now, config=cfg, context_levels=s6_levels, stop_policy=pol_reloaded,
            )
            v_quote_s6 = ValidatedQuote(
                is_valid=True, bid=cand_s6.planned_entry - 0.20, ask=cand_s6.planned_entry, last=cand_s6.planned_entry,
                spread=0.20, exchange_time=s6_now, observed_at=s6_now, freshness_sec=0.1, is_stale=False,
                rejection_code=None, rejection_detail=None,
            )
            cand_s6.order_id = "ord_scen_06_close_quote"
            armed_s6 = orch._arm_paper_candidate(
                db, cand_s6, now_ms=s6_now, clock=live_clock, quote=v_quote_s6, policy_settings=policy_settings, auto_enabled=True
            )
            assert armed_s6 is not None
            armed_s6.id = "ord_scen_06_close_quote"
            min_p_dist = json.loads(armed_s6.cost_snapshot)["min_protective_distance"]
            armed_s6.planned_entry = round(armed_s6.stop_loss + min_p_dist * 0.5, 2)
            db.commit()

            close_fill_quote = ValidatedQuote(
                is_valid=True,
                bid=round(armed_s6.stop_loss + min_p_dist * 0.4, 2),
                ask=armed_s6.planned_entry,
                last=armed_s6.planned_entry,
                spread=0.10,
                exchange_time=s6_now + 1_000,
                observed_at=s6_now + 1_000,
                freshness_sec=0.1,
                is_stale=False,
                rejection_code=None,
                rejection_detail=None,
            )
            coord = ExecutionCoordinator()
            fills_s6 = coord.evaluate_orders_sync(
                db=db,
                ticker_override={
                    "bid": close_fill_quote.bid,
                    "ask": close_fill_quote.ask,
                    "last": close_fill_quote.last,
                    "timestamp": s6_now + 1_000,
                },
                now_ms=s6_now + 1_000,
            )
            reloaded_s6 = db.query(PaperOrder).filter(PaperOrder.id == "ord_scen_06_close_quote").first()
            assert reloaded_s6.state == "rejected"
            s6_text = f"{reloaded_s6.exit_cause or ''} {getattr(reloaded_s6, 'fill_decision_snapshot', '') or ''}"
            assert "STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE" in s6_text

            # ------------------------------------------------------------------
            # Scenario 7: Fill-time quote already breaching SL rejected by Coordinator
            # ------------------------------------------------------------------
            s7_bars, s7_levels, s7_quote_dict, s7_now = build_bullish_m1_fvg_continuation_series(
                with_unicorn=True, t_offset_ms=10_800_000
            )
            cand_s7 = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                s7_bars, s7_bars, s7_quote_dict, "BULLISH", "BULLISH", policy_settings,
                now_ms=s7_now, config=cfg, context_levels=s7_levels, stop_policy=pol_reloaded,
            )
            v_quote_s7 = ValidatedQuote(
                is_valid=True, bid=cand_s7.planned_entry - 0.20, ask=cand_s7.planned_entry, last=cand_s7.planned_entry,
                spread=0.20, exchange_time=s7_now, observed_at=s7_now, freshness_sec=0.1, is_stale=False,
                rejection_code=None, rejection_detail=None,
            )
            cand_s7.order_id = "ord_scen_07_breached_quote"
            armed_s7 = orch._arm_paper_candidate(
                db, cand_s7, now_ms=s7_now, clock=live_clock, quote=v_quote_s7, policy_settings=policy_settings, auto_enabled=True
            )
            assert armed_s7 is not None
            armed_s7.id = "ord_scen_07_breached_quote"
            db.commit()

            breached_fill_quote = ValidatedQuote(
                is_valid=True,
                bid=round(armed_s7.stop_loss - 0.50, 2),
                ask=round(armed_s7.stop_loss - 0.30, 2),
                last=round(armed_s7.stop_loss - 0.40, 2),
                spread=0.20,
                exchange_time=s7_now + 2_000,
                observed_at=s7_now + 2_000,
                freshness_sec=0.1,
                is_stale=False,
                rejection_code=None,
                rejection_detail=None,
            )
            fills_s7 = coord.evaluate_orders_sync(
                db=db,
                ticker_override={
                    "bid": breached_fill_quote.bid,
                    "ask": breached_fill_quote.ask,
                    "last": breached_fill_quote.last,
                    "timestamp": s7_now + 2_000,
                },
                now_ms=s7_now + 2_000,
            )
            reloaded_s7 = db.query(PaperOrder).filter(PaperOrder.id == "ord_scen_07_breached_quote").first()
            assert reloaded_s7.state == "rejected"
            s7_text = f"{reloaded_s7.exit_cause or ''} {getattr(reloaded_s7, 'fill_decision_snapshot', '') or ''}"
            assert "INVALID_GEOMETRY_STOP_BREACHED" in s7_text

            # ------------------------------------------------------------------
            # Scenario 8: SHADOW_COMPARE mode executes Variant A while recording B & C
            # ------------------------------------------------------------------
            pol_shadow = StopPlacementPolicy(
                mode="SHADOW_COMPARE",
                v18_structural_intelligence_enabled=True,
                v18_unicorn_detection_enabled=True,
            )
            cand_s8 = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                l_bars,
                l_bars,
                l_quote_dict,
                "BULLISH",
                "BULLISH",
                policy_settings,
                now_ms=l_now,
                config=cfg,
                context_levels=l_levels,
                stop_policy=pol_shadow,
            )
            assert cand_s8.is_eligible is True
            assert cand_s8.stop_placement.applied_variant == "A"
            assert math.isclose(cand_s8.stop_loss, cand_s8.stop_placement.legacy_sl, abs_tol=1e-4)

            # ------------------------------------------------------------------
            # Scenario 9: LEGACY mode executes Legacy FVG stop and keeps V17.8 disabled
            # ------------------------------------------------------------------
            pol_legacy = StopPlacementPolicy(
                mode="LEGACY",
                v18_structural_intelligence_enabled=False,
                v18_unicorn_detection_enabled=False,
            )
            cand_s9 = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                l_bars,
                l_bars,
                l_quote_dict,
                "BULLISH",
                "BULLISH",
                policy_settings,
                now_ms=l_now,
                config=cfg,
                context_levels=l_levels,
                stop_policy=pol_legacy,
            )
            assert cand_s9.is_eligible is True
            assert cand_s9.unicorn_evidence is None
            assert cand_s9.stop_placement.applied_variant == "A"

            # ------------------------------------------------------------------
            # Scenario 11: Evidence API completeness for V17.8.0.1 Unicorn order
            # ------------------------------------------------------------------
            client = TestClient(app)
            ev_res = client.get(f"/api/v1/orders/{order_s1.id}/stop-evidence")
            assert ev_res.status_code == 200
            ev_json = ev_res.json()
            assert ev_json["status"] == "AVAILABLE"
            assert ev_json["order_id"] == order_s1.id
            assert ev_json["direction"] == "SHORT"
            assert ev_json["stop_mode_at_arm"] == "PAPER_STRUCTURAL"
            assert ev_json["applied_variant"] == "C"
            assert ev_json["min_protective_distance"] is not None and ev_json["min_protective_distance"] > 0
            assert ev_json["unicorn_evidence"] is not None
            assert ev_json["unicorn_evidence"]["is_valid"] is True
            assert ev_json["unicorn_evidence"]["selected_fvg_id"] is not None
            assert ev_json["unicorn_evidence"]["breaker_id"] is not None

            # ------------------------------------------------------------------
            # Scenario 12: Zero live orders invariant
            # ------------------------------------------------------------------
            assert spy.call_count == 0
        finally:
            db.close()


# ==============================================================================
# DEF-178-13: HONEST REPLAY CALIBRATION & API TRANSPARENCY
# ==============================================================================

def test_def_178_13_honest_replay_calibration_and_endpoint():
    """
    DEF-178-13: Verifies calibrate_empirical_p90_noise_buffer computes true empirical P90
    when strictly prior observations are present, labels EXPERIMENTAL_FIXED_MULTIPLIER when absent,
    rejects lookahead samples, and /api/v1/research/stop-placement/replay-comparison exposes transparent calibration metadata.
    """
    decision_ms = 2_000_000
    prior_samples = [
        NoiseCalibrationSample(timestamp_ms=1_000_000 + i * 60_000, adverse_noise_usd=0.20 + i * 0.10, regime="NORMAL")
        for i in range(12)
    ]
    cal = calibrate_empirical_p90_noise_buffer(prior_samples, decision_ms=decision_ms, min_sample_size=10)
    assert cal.status == "CALIBRATED_EMPIRICAL_P90"
    assert cal.calibration_mode == "EMPIRICAL_P90_QUANTILE"
    assert cal.sample_size == 12
    assert cal.p90_noise_usd is not None and cal.p90_noise_usd > 1.0

    # Without sufficient samples -> EXPERIMENTAL_FIXED_MULTIPLIER
    uncal = calibrate_empirical_p90_noise_buffer([], decision_ms=decision_ms, min_sample_size=10)
    assert uncal.status == "INSUFFICIENT_CALIBRATION_SAMPLE"
    assert uncal.calibration_mode == "EXPERIMENTAL_FIXED_MULTIPLIER"
    assert uncal.p90_noise_usd is None

    # Lookahead sample -> LOOKAHEAD_REJECTED
    future_samples = list(prior_samples) + [
        NoiseCalibrationSample(timestamp_ms=decision_ms + 1_000, adverse_noise_usd=0.90, regime="NORMAL")
    ]
    lookahead_cal = calibrate_empirical_p90_noise_buffer(future_samples, decision_ms=decision_ms, min_sample_size=10)
    assert lookahead_cal.status == "LOOKAHEAD_REJECTED"
    assert lookahead_cal.calibration_mode == "EXPERIMENTAL_FIXED_MULTIPLIER"

    # Replay comparison API transparency
    client = TestClient(app)
    res = client.get("/api/v1/research/stop-placement/replay-comparison")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in ("NO_REPLAY_RESULTS", "INSUFFICIENT_REPLAY_DATA", "AVAILABLE")
    assert "variants" in body
    assert body["variants"]["D"]["calibration_mode"] == "EXPERIMENTAL_FIXED_MULTIPLIER"


# ==============================================================================
# DEF-178-14: FORENSIC AUDIT DATABASE & CALCULATOR TRUTH RECONCILIATION
# ==============================================================================

def test_def_178_14_forensic_audit_truth_reconciliation():
    """
    DEF-178-14: Verifies the authoritative calculator math and forensic truth reconciliation
    for trade ord_SMC_M1_FVG_CONTINUATION_fvg_cont_SHORT_fvg-bear-1M-1793102220000_1793102400000:
    - Filled at 1793102463329 (entry=4021.08, sl=4021.67, tp=4002.34, qty=0.64), closed at 1793102574926
      (exit=4021.74, hold_time_ms=111597 = 111.597s, realized_pnl_net=-2.48 USDT).
    - Recomputed Variant C structural SL=4024.80 with authoritative calculate_risk_reward:
      * Auto-sized for 2.50 USDT risk budget (capital=1000.0, risk_pct=0.25):
        quantity=0.35 oz, net_risk_usdt=2.50 USDT, net_reward_usdt=5.68 USDT (maker TP, 2.2738R).
      * Fixed 0.15 oz quantity:
        net_risk_usdt=1.07 USDT (not 2.48 USDT), net_reward_usdt=2.43 USDT (maker TP, 2.2738R).
    """
    # 1. Exact hold time from persisted milestones: 1793102574926 - 1793102463329 = 111597 ms (111.597s, not 9s)
    filled_at_ms = 1793102463329
    closed_at_ms = 1793102574926
    hold_time_ms = closed_at_ms - filled_at_ms
    assert hold_time_ms == 111597
    assert math.isclose(hold_time_ms / 1000.0, 111.597, abs_tol=1e-4)

    # 2. Original Legacy execution (Entry=4021.08, SL=4021.67, TP=4002.34, quantity=0.64, realized_pnl_net=-2.48)
    calc_legacy = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4021.08,
        stop_loss=4021.67,
        take_profit=4002.34,
        capital=986.80,
        risk_pct=0.25,
        quantity_override=0.64,
    )
    assert calc_legacy.is_valid is True
    assert calc_legacy.quantity == 0.64

    # 3. Structural SL=4024.80 auto-sized for 2.50 USDT risk budget (capital=1000.0, risk_pct=0.25)
    calc_struct_autosized = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4021.08,
        stop_loss=4024.80,
        take_profit=4002.34,
        capital=1000.0,
        risk_pct=0.25,
    )
    assert calc_struct_autosized.can_execute is True
    assert calc_struct_autosized.quantity == 0.35
    assert math.isclose(calc_struct_autosized.net_risk_usdt, 2.50, abs_tol=0.05)
    assert math.isclose(calc_struct_autosized.net_reward_usdt, 5.40, abs_tol=0.05)
    assert math.isclose(calc_struct_autosized.net_rr, 2.1617, abs_tol=0.02)

    calc_struct_autosized_maker_tp = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4021.08,
        stop_loss=4024.80,
        take_profit=4002.34,
        capital=1000.0,
        risk_pct=0.25,
        costs=CostAssumptions(tp_is_maker=True),
    )
    assert calc_struct_autosized_maker_tp.quantity == 0.35
    assert math.isclose(calc_struct_autosized_maker_tp.net_risk_usdt, 2.50, abs_tol=0.05)
    assert math.isclose(calc_struct_autosized_maker_tp.net_reward_usdt, 5.68, abs_tol=0.05)
    assert math.isclose(calc_struct_autosized_maker_tp.net_rr, 2.2738, abs_tol=0.02)

    # 4. Structural SL=4024.80 at fixed 0.15 oz quantity (quantity_override=0.15)
    calc_struct_015 = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4021.08,
        stop_loss=4024.80,
        take_profit=4002.34,
        capital=1000.0,
        risk_pct=0.25,
        quantity_override=0.15,
    )
    assert calc_struct_015.quantity == 0.15
    assert math.isclose(calc_struct_015.net_risk_usdt, 1.07, abs_tol=0.05)
    assert math.isclose(calc_struct_015.net_reward_usdt, 2.31, abs_tol=0.05)
    assert math.isclose(calc_struct_015.net_rr, 2.1617, abs_tol=0.02)

    calc_struct_015_maker_tp = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4021.08,
        stop_loss=4024.80,
        take_profit=4002.34,
        capital=1000.0,
        risk_pct=0.25,
        quantity_override=0.15,
        costs=CostAssumptions(tp_is_maker=True),
    )
    assert calc_struct_015_maker_tp.quantity == 0.15
    assert math.isclose(calc_struct_015_maker_tp.net_risk_usdt, 1.07, abs_tol=0.05)
    assert math.isclose(calc_struct_015_maker_tp.net_reward_usdt, 2.43, abs_tol=0.05)
    assert math.isclose(calc_struct_015_maker_tp.net_rr, 2.2738, abs_tol=0.02)


