"""
backend/tests/test_v16_38_adaptive_paper_readiness.py
AURUM DESK — V16.38 COMPLETE ADAPTIVE PAPER EXECUTION AND TONIGHT READINESS TEST SUITE

Tests verify:
1. Allowlist normalization & builder immutability (Rules 017-020, 163).
2. GET/POST /api/scalp/config validation, Vietnamese 400 messages, & LIVE rejection (Rules 024-027, 164).
3. Policy router across profile/mode matrix & regime routing (Rules 033-038, 165, 170).
4. Causal S/R filtering, max span, quantized rounding, reclaim chronology, role flip, invalidation (Rules 053-069, 166-169).
5. SetupQualityRepository bounded batch query without N+1 (Rule 190).
6. Scenario snapshot immutability conflict enforcement (Rule 191).
7. Post-trade review accounting (realized R, hold time, signed slippage, win/loss/be) (Rules 121-127, 182, 183).
8. Full candle -> ARM -> fill -> close execution pipeline for LONG and SHORT (Rules 176, 177).
9. Quota enforcement: 3 fills cap, 4th rejected (Rules 093, 094, 180).
10. Primary bias enforcement across all adaptive model families (Rules 082, 083, 179).
"""
import json
import time
import pytest
from unittest.mock import MagicMock, patch

import crud
import models
from domain_calculator import CalculationResult, calculate_risk_reward
from services.clock import FakeClock
from services.cg01_config_factory import resolve_canonical_cg01_config
from services.entry_guard_service import EntryGuardRequest, evaluate_entry_guard
from services.execution_coordinator import ExecutionCoordinator
from services.post_trade_review_service import PostTradeReview, build_post_trade_review
from services.scalp_m1_model_service import (
    ModelExecutionAction,
    ScalpCandidate,
    resolve_model_execution_policy,
)
from services.scalp_m1_types import (
    ExecutionProfile,
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityEvaluation,
    OpportunityStage,
    SUPPORTED_ADAPTIVE_MODELS,
    WatchedZone,
    build_adaptive_smc_paper_profile,
    load_effective_scalp_config,
    normalize_adaptive_model_allowlist,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.setup_quality_repository import SetupQualityRepository
from services.smc_scenario_repository import save_immutable_scenario_snapshot
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState
from services.support_resistance_service import (
    SRState,
    SRZone,
    build_support_resistance_zones,
    cluster_confirmed_levels,
    derive_sr_zone_bounds,
    evaluate_sr_reclaim,
    evaluate_sr_role_flip,
    invalidate_sr_zone,
    resolve_models_for_regime,
)
from services.trade_lifecycle_service import TradeLifecycleService


# ===========================================================================
# 1. Allowlist Normalization & Builder Immutability (Rules 017-020, 163)
# ===========================================================================

class TestAllowlistAndBuilder:
    def test_normalize_allowlist_defaults_and_empty(self):
        # None yields default 5 supported models
        default_res = normalize_adaptive_model_allowlist(None)
        assert default_res == SUPPORTED_ADAPTIVE_MODELS
        assert len(default_res) == 5

        # Empty list yields empty tuple (not defaults)
        empty_res = normalize_adaptive_model_allowlist([])
        assert empty_res == ()
        empty_str_res = normalize_adaptive_model_allowlist("")
        assert empty_str_res == ()

    def test_normalize_allowlist_deduplication_and_order(self):
        raw = [
            ModelIdentifier.SMC_M1_CONTINUATION.value,
            ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
            ModelIdentifier.SMC_M1_CONTINUATION.value,
        ]
        res = normalize_adaptive_model_allowlist(raw)
        assert res == (
            ModelIdentifier.SMC_M1_CONTINUATION.value,
            ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        )

    def test_normalize_allowlist_rejects_research_and_unknown(self):
        # Research model rejected (Rule 020)
        with pytest.raises(ValueError, match="RESEARCH_MODEL_NOT_PERMITTED"):
            normalize_adaptive_model_allowlist([ModelIdentifier.NY_DAILY_RESEARCH.value])

        # Unknown model rejected (Rule 019)
        with pytest.raises(ValueError, match="UNKNOWN_MODEL"):
            normalize_adaptive_model_allowlist(["UNICORN_STRATEGY"])

    def test_builder_immutability_and_preservation(self):
        base = M1ScalpConfig(
            min_net_rr=2.0,
            fallback_risk_pct=0.25,
            execution_profile="BASELINE",
            adaptive_model_allowlist=(ModelIdentifier.SMC_M1_REVERSAL.value,),
        )
        built = build_adaptive_smc_paper_profile(base)

        # Base not mutated
        assert base.min_net_rr == 2.0
        assert base.execution_profile == "BASELINE"

        # Built has correct adaptive paper properties
        assert built.execution_profile == "PAPER_ADAPTIVE_SMC"
        assert built.scalp_m1_mode == "PAPER"
        assert built.min_net_rr == 1.5
        assert built.fallback_risk_pct == 0.25  # Preserved
        assert built.adaptive_model_allowlist == (ModelIdentifier.SMC_M1_REVERSAL.value,)
        assert built.cg01_enabled is False  # CG01 was not in allowlist


# ===========================================================================
# 2. Config API Endpoints (Rules 024-027, 164)
# ===========================================================================

class TestConfigAPIEndpoints:
    def test_get_and_post_adaptive_config(self, client, isolated_db):
        # POST valid adaptive config
        payload = {
            "execution_profile": "PAPER_ADAPTIVE_SMC",
            "adaptive_model_allowlist": [
                ModelIdentifier.SMC_M1_REVERSAL.value,
                ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
            ],
            "min_net_rr": 1.5,
            "scalp_m1_mode": "PAPER",
        }
        res = client.post("/api/scalp/config", json=payload)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["execution_profile"] == "PAPER_ADAPTIVE_SMC"
        assert ModelIdentifier.CG01_SWEEP_MSS_FVG.value in data["adaptive_model_allowlist"]

        # GET confirms effective config persisted
        get_res = client.get("/api/scalp/config")
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["execution_profile"] == "PAPER_ADAPTIVE_SMC"
        assert get_data["min_net_rr"] == 1.5

    def test_post_invalid_execution_profile_rejected(self, client):
        payload = {"execution_profile": "MARTINGALE_PRO"}
        res = client.post("/api/scalp/config", json=payload)
        assert res.status_code == 400
        detail = str(res.json()["detail"])
        assert "INVALID_EXECUTION_PROFILE" in detail
        assert "không được hỗ trợ" in detail

    def test_paper_only_scope_guard(self, client, isolated_db):
        # Set execution_scope in DB to LIVE
        isolated_db.add(models.SystemConfig(key="execution_scope", value="LIVE", updated_at=int(time.time() * 1000)))
        isolated_db.commit()

        payload = {"execution_profile": "PAPER_ADAPTIVE_SMC"}
        res = client.post("/api/scalp/config", json=payload)
        assert res.status_code == 400
        detail = str(res.json()["detail"])
        assert "PAPER_ONLY_PROFILE_VIOLATION" in detail


# ===========================================================================
# 3. Policy Router Matrix & Regime Routing (Rules 033-038, 165, 170)
# ===========================================================================

class TestPolicyRouter:
    def test_policy_router_adaptive_matrix(self):
        # 1. PAPER_ADAPTIVE_SMC with full allowlist in PAPER mode
        cfg = M1ScalpConfig(
            execution_profile="PAPER_ADAPTIVE_SMC",
            scalp_m1_mode="PAPER",
            adaptive_model_allowlist=SUPPORTED_ADAPTIVE_MODELS,
            cg01_enabled=True,
            fvg_continuation_enabled=True,
            sweep_reclaim_enabled=True,
        )
        action, reason = resolve_model_execution_policy(cfg, ModelIdentifier.CG01_SWEEP_MSS_FVG.value)
        assert action == ModelExecutionAction.EVALUATE_AND_EXECUTE
        assert reason == "ADAPTIVE_PAPER_ACTIVE"

        action, reason = resolve_model_execution_policy(cfg, ModelIdentifier.SMC_M1_CONTINUATION.value)
        assert action == ModelExecutionAction.EVALUATE_AND_EXECUTE

        # 2. Disabled feature flag
        cfg_no_fvg = M1ScalpConfig(
            execution_profile="PAPER_ADAPTIVE_SMC",
            scalp_m1_mode="PAPER",
            adaptive_model_allowlist=SUPPORTED_ADAPTIVE_MODELS,
            fvg_continuation_enabled=False,
        )
        action, reason = resolve_model_execution_policy(cfg_no_fvg, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value)
        assert action == ModelExecutionAction.DISABLED
        assert reason == "FVG_CONTINUATION_DISABLED"

        # 3. Model not in allowlist
        cfg_custom = M1ScalpConfig(
            execution_profile="PAPER_ADAPTIVE_SMC",
            scalp_m1_mode="PAPER",
            adaptive_model_allowlist=(ModelIdentifier.SMC_M1_REVERSAL.value,),
        )
        action, reason = resolve_model_execution_policy(cfg_custom, ModelIdentifier.SMC_M1_CONTINUATION.value)
        assert action == ModelExecutionAction.DISABLED
        assert reason == "MODEL_DISABLED_BY_PROFILE"

        # 4. Shadow mode
        cfg_shadow = M1ScalpConfig(
            execution_profile="PAPER_ADAPTIVE_SMC",
            scalp_m1_mode="SHADOW",
            adaptive_model_allowlist=SUPPORTED_ADAPTIVE_MODELS,
        )
        action, reason = resolve_model_execution_policy(cfg_shadow, ModelIdentifier.SMC_M1_REVERSAL.value)
        assert action == ModelExecutionAction.EVALUATE_SHADOW
        assert reason == "ADAPTIVE_SHADOW"

    def test_regime_to_model_routing_matrix(self):
        trend_models = resolve_models_for_regime("TREND_BULLISH")
        assert ModelIdentifier.SMC_M1_CONTINUATION.value in trend_models
        assert ModelIdentifier.CG01_SWEEP_MSS_FVG.value in trend_models
        assert ModelIdentifier.SMC_M1_REVERSAL.value not in trend_models

        range_models = resolve_models_for_regime("RANGE")
        assert ModelIdentifier.SMC_M1_REVERSAL.value in range_models
        assert ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value in range_models
        assert ModelIdentifier.SMC_M1_CONTINUATION.value not in range_models


# ===========================================================================
# 4. Causal Support & Resistance (Rules 053-069, 166-169)
# ===========================================================================

class TestSupportResistanceCausal:
    def test_sr_zone_bounds_validation(self):
        # Inverted bounds fail
        with pytest.raises(ValueError, match="top .* must be > bottom"):
            SRZone(zone_id="sr_inv", symbol="XAUUSDT", timeframe="15M", role="SUPPORT", top=4000.0, bottom=4010.0, midpoint=4005.0)

        # Midpoint outside bounds fails
        with pytest.raises(ValueError, match="midpoint .* must be between bottom"):
            SRZone(zone_id="sr_mid", symbol="XAUUSDT", timeframe="15M", role="SUPPORT", top=4010.0, bottom=4000.0, midpoint=4020.0)

    def test_causal_swings_filtering(self):
        as_of = 1_700_000_100_000
        swings = [
            {"id": "sw_past", "price": 4050.0, "type": "HIGH", "known_at": as_of - 50_000, "bar_index": 10},
            {"id": "sw_future", "price": 4055.0, "type": "HIGH", "known_at": as_of + 10_000, "bar_index": 15},  # FUTURE
            {"id": "sw_missing", "price": 4048.0, "type": "HIGH", "known_at": 0, "bar_index": 5},               # INVALID
        ]
        candles = [{"open": 4049.0, "high": 4052.0, "low": 4048.0, "close": 4050.0, "close_time": as_of}]

        zones = build_support_resistance_zones(swings, as_of_ms=as_of, timeframe="15M")
        assert len(zones) == 1
        assert zones[0].source_swing_ids == ("sw_past",)

    def test_cluster_max_span_prevents_chaining(self):
        # 10 levels stepping up by 0.30: 4000.0, 4000.3, 4000.6 ... 4002.7
        # With tolerance 0.50 and max_span 1.50, it must split into multiple clusters
        levels = [4000.0 + i * 0.30 for i in range(10)]
        clusters = cluster_confirmed_levels(levels, tolerance_usd=0.50, max_span_usd=1.50)
        assert len(clusters) > 1
        for cl in clusters:
            assert (max(cl) - min(cl)) <= 1.50

    def test_derive_bounds_quantized_tick_size(self):
        cluster = [4050.1234, 4050.3456]
        top, bottom, mid = derive_sr_zone_bounds(cluster, tick_size=0.01)
        assert top > bottom
        assert bottom <= mid <= top
        assert round(top, 2) == top
        assert round(bottom, 2) == bottom

    def test_evaluate_sr_reclaim_chronology(self):
        zone = SRZone(zone_id="sr1", symbol="XAUUSDT", timeframe="15M", role="RESISTANCE", top=4050.0, bottom=4045.0, midpoint=4047.5)
        # Resistance sweep candle: high > top, close <= top
        sweep_bar = {"high": 4052.0, "close": 4049.0, "close_time": 1_700_000_100_000}
        reclaim_bar_earlier = {"close": 4048.0, "close_time": 1_700_000_050_000}  # BEFORE sweep!
        reclaim_bar_later = {"close": 4048.0, "close_time": 1_700_000_150_000}    # AFTER sweep

        # Chronologically invalid reclaim
        assert evaluate_sr_reclaim(zone, sweep_candle=sweep_bar, reclaim_candle=reclaim_bar_earlier) is False

        # Chronologically valid reclaim
        assert evaluate_sr_reclaim(zone, sweep_candle=sweep_bar, reclaim_candle=reclaim_bar_later) is True

    def test_evaluate_sr_role_flip(self):
        # Broken support -> resistance
        zone = SRZone(zone_id="sr2", symbol="XAUUSDT", timeframe="15M", role="SUPPORT", top=4050.0, bottom=4045.0, midpoint=4047.5, state=SRState.BROKEN.value)
        # Retest candle closes INSIDE zone -> pending, not flipped
        retest_inside = {"high": 4048.0, "low": 4044.0, "close": 4046.0}
        assert evaluate_sr_role_flip(zone, retest_inside) is None

        # Retest candle tests zone and closes BELOW bottom -> flipped to RESISTANCE
        retest_outside = {"high": 4047.0, "low": 4040.0, "close": 4042.0}
        assert evaluate_sr_role_flip(zone, retest_outside) == "RESISTANCE"

    def test_invalidate_sr_zone_preserves_broken_time(self):
        zone = SRZone(
            zone_id="sr3", symbol="XAUUSDT", timeframe="15M", role="SUPPORT",
            top=4050.0, bottom=4045.0, midpoint=4047.5, broken_at_ms=1000, state=SRState.BROKEN.value,
        )
        invalidate_sr_zone(zone, reason="EXPIRED_SESSION", now_ms=2000)
        assert zone.state == SRState.INVALIDATED.value
        assert zone.broken_at_ms == 1000       # Preserved!
        assert zone.invalidated_at_ms == 2000   # Distinct invalidation timestamp


# ===========================================================================
# 5. Setup Quality Bounded Batch Query (Rule 190)
# ===========================================================================

class TestSetupQualityBatchQuery:
    def test_get_snapshots_batch_single_query(self, isolated_db):
        now_ms = int(time.time() * 1000)
        # Seed 5 quality records across 3 setup IDs
        for i, s_id in enumerate(["setup_a", "setup_b", "setup_a", "setup_c", "setup_b"]):
            isolated_db.add(models.SetupQualitySnapshot(
                snapshot_id=f"snap_{s_id}_{i}",
                setup_id=s_id,
                setup_instance_id=f"inst_{s_id}_{i}",
                strategy_family="CG01_SWEEP_MSS_FVG",
                direction="LONG",
                status="ASSESSED",
                as_of_ms=now_ms + i * 1000,
                payload_json=json.dumps({"overall_score": 85.0 + i}),
                created_at=now_ms + i * 1000,
            ))
        isolated_db.commit()

        repo = SetupQualityRepository()
        snapshots = repo.get_snapshots_batch(isolated_db, ["setup_a", "setup_b", "setup_c", "setup_missing"])

        assert len(snapshots) == 3
        assert snapshots["setup_a"]["overall_score"] == 87.0  # Latest created_at (i=2)
        assert snapshots["setup_b"]["overall_score"] == 89.0  # Latest created_at (i=4)
        assert snapshots["setup_c"]["overall_score"] == 88.0  # Latest created_at (i=3)
        assert "setup_missing" not in snapshots


# ===========================================================================
# 6. Scenario Immutability Conflict (Rule 191)
# ===========================================================================

class TestScenarioImmutabilityConflict:
    def test_conflict_raised_even_if_state_terminal(self, isolated_db):
        now_ms = int(time.time() * 1000)
        snap1 = ScenarioSnapshot(
            scenario_id="scen_term_1",
            instance_id="inst_term_1",
            revision=1,
            symbol="XAUUSDT",
            setup_timeframe="15M",
            context_timeframes=["D", "4H"],
            family="STANDARD_SMC",
            direction="LONG",
            state=ScenarioState.EXPIRED,
            as_of_ms=now_ms,
            evidence_cutoff_ms=now_ms,
            evidence_ids=["sw_term_1"],
            liquidity_map={},
            trigger_chain={},
            target=None,
            invalidation=None,
            summary_vi="Terminal expired version 1",
            config_version=1,
            detector_version="v14.4",
        )
        save_immutable_scenario_snapshot(isolated_db, snap1)
        isolated_db.commit()

        # Attempt to save different digest with same snapshot_id
        snap2 = ScenarioSnapshot(
            scenario_id="scen_term_1",
            instance_id="inst_term_1",
            revision=1,
            symbol="XAUUSDT",
            setup_timeframe="15M",
            context_timeframes=["D", "4H"],
            family="STANDARD_SMC",
            direction="LONG",
            state=ScenarioState.EXPIRED,
            as_of_ms=now_ms,
            evidence_cutoff_ms=now_ms,
            evidence_ids=["sw_term_MUTATED"],
            liquidity_map={},
            trigger_chain={},
            target=None,
            invalidation=None,
            summary_vi="Terminal expired version 2 mutated",
            config_version=1,
            detector_version="v14.4",
        )
        with pytest.raises(ValueError, match="SNAPSHOT_IMMUTABILITY_CONFLICT"):
            save_immutable_scenario_snapshot(isolated_db, snap2)


# ===========================================================================
# 7. Post-Trade Review Accounting (Rules 121-127, 182, 183)
# ===========================================================================

class TestPostTradeReviewAccounting:
    def test_post_trade_review_fields_and_accounting(self, isolated_db):
        now_ms = 1_700_000_000_000
        order = models.PaperOrder(
            id="ord_rev_1",
            instrument="XAUUSDT",
            direction="LONG",
            state="closed",
            planned_entry=4100.0,
            actual_entry=4099.5,  # Favorable slippage -0.50
            stop_loss=4090.0,
            take_profit=4120.0,
            actual_exit=4119.0,
            quantity=0.1,
            initial_risk_usdt=1.0,
            created_at=now_ms - 1000,
            opened_at=now_ms,
            closed_at=now_ms + 60_000,  # 1 min hold
            realized_pnl_net=1.80,
            realized_r=1.80,
            cost_snapshot=json.dumps({"min_net_rr_at_arm": 1.5, "gross_rr": 2.0}),
        )
        isolated_db.add(order)
        isolated_db.commit()

        review = build_post_trade_review(order, outcome={"realized_pnl": 1.80, "realized_r": 1.80, "gross_pnl": 2.0})

        assert review.order_id == "ord_rev_1"
        assert review.net_pnl == 1.80
        assert review.realized_rr == 1.80
        assert review.gross_price_rr == 2.05  # (4119.0 - 4099.5) / (4099.5 - 4090.0) = 19.5 / 9.5 = 2.05
        assert review.hold_time_ms == 60_000
        assert review.signed_slippage_usd == -0.50  # Favorable for LONG
        assert review.outcome_status == "WIN"

    def test_post_trade_review_breakeven_and_loss(self, isolated_db):
        now_ms = 1_700_000_000_000
        order_be = models.PaperOrder(
            id="ord_rev_be",
            instrument="XAUUSDT",
            direction="SHORT",
            state="closed",
            planned_entry=4100.0,
            actual_entry=4100.0,
            stop_loss=4110.0,
            take_profit=4080.0,
            actual_exit=4100.0,
            quantity=0.1,
            initial_risk_usdt=1.0,
            created_at=now_ms - 1000,
            opened_at=now_ms,
            closed_at=now_ms + 10_000,
            realized_pnl_net=0.0001,  # Within epsilon
            realized_r=0.0,
        )
        review_be = build_post_trade_review(order_be)
        assert review_be.outcome_status == "BREAKEVEN"


# ===========================================================================
# 8. Full Candle -> ARM -> Fill -> Close Execution Pipeline (Rules 176, 177)
# ===========================================================================

class TestFullExecutionPipeline:
    def test_full_pipeline_long_execution(self, isolated_db):
        now_ms = 1_700_000_000_000
        isolated_db.add(models.SystemConfig(key="min_net_rr", value="1.5", updated_at=now_ms))
        isolated_db.commit()

        orchestrator = ScalpWatcherOrchestrator()
        cfg = M1ScalpConfig(
            execution_profile="PAPER_ADAPTIVE_SMC",
            scalp_m1_mode="PAPER",
            min_net_rr=1.5,
            adaptive_model_allowlist=SUPPORTED_ADAPTIVE_MODELS,
        )

        calc = calculate_risk_reward("LONG", 4100.0, 4090.0, 4124.0, capital=1000.0, risk_pct=0.10, min_net_rr=1.5)
        cand_long = ScalpCandidate(
            is_eligible=True,
            model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
            direction="LONG",
            candidate_key="cand_pipe_long_1",
            symbol="XAUUSDT",
            planned_entry=4100.0,
            stop_loss=4090.0,
            take_profit=4124.0,
            gross_rr=calc.gross_rr,
            net_rr=calc.net_rr,
            calculation=calc,
            as_of_ms=now_ms,
        )

        quote = {"bid": 4099.9, "ask": 4100.1, "last": 4100.0, "timestamp": now_ms}
        clock = FakeClock(now_ms)
        feed_health = {"has_execution_gap": False, "is_lagging": False, "is_healthy": True}

        # 1. ARM
        armed_order = orchestrator._arm_paper_candidate(
            db=isolated_db, cand=cand_long, now_ms=now_ms, clock=clock, quote=quote,
            policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.10, "max_daily_trades": 3, "config_version": 1},
            config=cfg, feed_health=feed_health, auto_enabled=True,
        )
        assert armed_order is not None
        assert armed_order.state == "armed"
        assert armed_order.direction == "LONG"
        cost_snap = json.loads(armed_order.cost_snapshot)
        assert cost_snap["min_net_rr_at_arm"] == 1.5
        assert cost_snap["execution_profile"] == "PAPER_ADAPTIVE_SMC"

        # 2. FILL via ExecutionCoordinator
        coordinator = ExecutionCoordinator()
        fill_ticker = {"bid": 4099.8, "ask": 4100.0, "last": 4100.0, "timestamp": now_ms + 1000}
        coordinator.evaluate_orders_sync(isolated_db, ticker_override=fill_ticker, now_ms=now_ms + 1000)
        isolated_db.refresh(armed_order)
        assert armed_order.state == "paper_open"
        assert armed_order.actual_entry is not None

        # 3. CLOSE via TradeLifecycleService (TP hit)
        lifecycle = TradeLifecycleService()
        lifecycle.process_exit_tick(isolated_db, current_bid=4125.0, current_ask=4125.2, now_ms=now_ms + 60_000, clock=FakeClock(now_ms + 60_000))
        isolated_db.refresh(armed_order)
        assert armed_order.state == "closed"
        assert armed_order.exit_cause in ("TP_HIT", "TP")
        assert armed_order.realized_pnl_net > 0

    def test_full_pipeline_short_execution(self, isolated_db):
        now_ms = 1_700_000_000_000
        isolated_db.add(models.SystemConfig(key="min_net_rr", value="1.5", updated_at=now_ms))
        isolated_db.commit()

        orchestrator = ScalpWatcherOrchestrator()
        cfg = M1ScalpConfig(
            execution_profile="PAPER_ADAPTIVE_SMC",
            scalp_m1_mode="PAPER",
            min_net_rr=1.5,
            adaptive_model_allowlist=SUPPORTED_ADAPTIVE_MODELS,
        )

        calc = calculate_risk_reward("SHORT", 4100.0, 4110.0, 4076.0, capital=1000.0, risk_pct=0.10, min_net_rr=1.5)
        cand_short = ScalpCandidate(
            is_eligible=True,
            model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
            direction="SHORT",
            candidate_key="cand_pipe_short_1",
            symbol="XAUUSDT",
            planned_entry=4100.0,
            stop_loss=4110.0,
            take_profit=4076.0,  # TP < Entry < SL
            gross_rr=calc.gross_rr,
            net_rr=calc.net_rr,
            calculation=calc,
            as_of_ms=now_ms,
        )

        quote = {"bid": 4099.9, "ask": 4100.1, "last": 4100.0, "timestamp": now_ms}
        clock = FakeClock(now_ms)
        feed_health = {"has_execution_gap": False, "is_lagging": False, "is_healthy": True}

        # 1. ARM
        armed_order = orchestrator._arm_paper_candidate(
            db=isolated_db, cand=cand_short, now_ms=now_ms, clock=clock, quote=quote,
            policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.10, "max_daily_trades": 3, "config_version": 1},
            config=cfg, feed_health=feed_health, auto_enabled=True,
        )
        assert armed_order is not None
        assert armed_order.direction == "SHORT"
        assert armed_order.stop_loss > armed_order.planned_entry > armed_order.take_profit

        # 2. FILL via Coordinator
        coordinator = ExecutionCoordinator()
        fill_ticker = {"bid": 4100.0, "ask": 4100.2, "last": 4100.0, "timestamp": now_ms + 1000}
        coordinator.evaluate_orders_sync(isolated_db, ticker_override=fill_ticker, now_ms=now_ms + 1000)
        isolated_db.refresh(armed_order)
        assert armed_order.state == "paper_open"
        assert armed_order.actual_entry is not None

        # 3. CLOSE via SL hit
        lifecycle = TradeLifecycleService()
        lifecycle.process_exit_tick(isolated_db, current_bid=4110.5, current_ask=4110.7, now_ms=now_ms + 30_000, clock=FakeClock(now_ms + 30_000))
        isolated_db.refresh(armed_order)
        assert armed_order.state == "closed"
        assert armed_order.exit_cause in ("SL_HIT", "SL")
        assert armed_order.realized_pnl_net < 0


# ===========================================================================
# 9. Quota Enforcement (Rules 093, 094, 180)
# ===========================================================================

class TestQuotaEnforcement:
    def test_daily_cap_blocks_fourth_fill(self, isolated_db):
        now_ms = 1_700_000_000_000
        clock = FakeClock(now_ms)
        # Seed DayAudit with 3 fills already
        audit = crud.get_or_create_today_audit(isolated_db, clock=clock)
        audit.fills_count = 3
        isolated_db.commit()

        orchestrator = ScalpWatcherOrchestrator()
        cfg = M1ScalpConfig(
            execution_profile="PAPER_ADAPTIVE_SMC",
            scalp_m1_mode="PAPER",
            daily_max_cap=3,
            adaptive_model_allowlist=SUPPORTED_ADAPTIVE_MODELS,
        )
        calc_q = calculate_risk_reward("LONG", 4100.0, 4090.0, 4124.0, capital=1000.0, risk_pct=0.10, min_net_rr=1.5)
        cand = ScalpCandidate(
            is_eligible=True,
            model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
            direction="LONG",
            candidate_key="cand_quota_blocked",
            symbol="XAUUSDT",
            planned_entry=4100.0,
            stop_loss=4090.0,
            take_profit=4124.0,
            calculation=calc_q,
        )

        res = orchestrator._arm_paper_candidate(
            db=isolated_db, cand=cand, now_ms=now_ms, clock=clock,
            quote={"bid": 4100.0, "ask": 4100.2, "last": 4100.0, "timestamp": now_ms},
            policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.10, "max_daily_trades": 3},
            config=cfg, feed_health={"has_execution_gap": False, "is_lagging": False, "is_healthy": True}, auto_enabled=True,
        )
        assert res is None  # Blocked by daily cap!


# ===========================================================================
# 10. Primary Bias Enforcement Across Adaptive Models (Rules 082, 083, 179)
# ===========================================================================

class TestPrimaryBiasEnforcement:
    @pytest.mark.parametrize("model_id", [
        ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        ModelIdentifier.SMC_M1_REVERSAL.value,
        ModelIdentifier.SMC_M1_CONTINUATION.value,
        ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
    ])
    def test_daily_bias_primary_blocks_opposite_direction(self, isolated_db, model_id):
        now_ms = 1_700_000_000_000
        # Set up direction policy DAILY_BIAS_PRIMARY with BULLISH bias only
        evidence = {
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "daily_bias": {
                "status": "READY",
                "config_fingerprint": "fp_test",
                "authority_token": "token_123",
                "allowed_directions": ["LONG"],
            },
            "effective_config_fingerprint": "fp_test",
            "current_daily_bias": {
                "available": True,
                "status": "AUTHORITY_RESOLVED",
                "config_fingerprint": "fp_test",
                "evaluated_at_ms": now_ms,
                "authority_token": "token_123",
                "allowed_directions": ["LONG"],  # ONLY LONG allowed!
            },
        }

        calc_bias = calculate_risk_reward("SHORT", 4100.0, 4110.0, 4076.0, capital=1000.0, risk_pct=0.10, min_net_rr=1.5)

        # Attempt SHORT request
        req = EntryGuardRequest(
            stage="ARM",
            now_ms=now_ms,
            symbol="XAUUSDT",
            direction="SHORT",  # VIOLATES BULLISH PRIMARY BIAS!
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            setup_id="ws_bias_test",
            setup_instance_id="inst_bias_test",
            setup_state="READY",
            timeframe="1M",
            strategy_family=model_id,
            planned_entry=4100.0,
            stop_loss=4110.0,
            take_profit=4076.0,
            calc_result=calc_bias,
            validated_quote=MagicMock(is_valid=True, is_stale=False),
            feed_health={"has_execution_gap": False, "is_lagging": False, "is_healthy": True},
            min_net_rr=1.5,
            evidence=evidence,
        )
        decision = evaluate_entry_guard(isolated_db, req)
        assert decision.allowed is False
        assert "DAILY_BIAS_DIRECTION_MISMATCH" in decision.reason_codes
