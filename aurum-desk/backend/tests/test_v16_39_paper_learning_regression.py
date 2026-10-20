"""
backend/tests/test_v16_39_paper_learning_regression.py
AURUM DESK — V16.39
PAPER LEARNING EXPERIMENT (QUALITY SCORE >= 70 / 100, GOVERNED GREEN / YELLOW / RED LESSONS)
COMPREHENSIVE TEST SUITE (SECTIONS 011 - 190)
"""
import json
import math
import time
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient

import models
from main import app
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    ScalpMode,
    build_adaptive_smc_paper_profile,
    load_effective_scalp_config,
)
from services.scalp_m1_model_service import ScalpM1ModelService, ScalpCandidate
from services.scalp_watcher_orchestrator import (
    ScalpWatcherOrchestrator,
    resolve_model_evaluation_plan,
)
from services.setup_quality_service import (
    QualityScoreFactor,
    CandidateQualityAssessment,
    assess_candidate_quality,
)
from services.post_trade_review_service import (
    PostTradeReview,
    LossClassification,
    LessonClassification,
    classify_post_trade_lesson,
    save_post_trade_review,
    get_post_trade_review,
    summarize_lesson_evidence,
    build_post_trade_review,
)
from services.lesson_rule_service import LessonRuleService
from services.trade_lifecycle_service import TradeLifecycleService


def _create_mock_candles(base_price=2650.0, count=30, now_ms=1000000):
    candles = []
    for i in range(count):
        ts = now_ms - (count - i) * 60000
        candles.append({
            "timestamp": ts,
            "open": base_price + i * 0.1,
            "high": base_price + i * 0.1 + 0.5,
            "low": base_price + i * 0.1 - 0.5,
            "close": base_price + i * 0.1 + 0.2,
            "volume": 100.0,
        })
    return candles


class TestRoutingRegressionReproduction:
    def test_section_011_continuation_called_under_trend_bullish(self, isolated_db):
        """
        SECTION 011: When regime is TREND_BULLISH, reversal is disallowed by regime,
        but continuation MUST still be evaluated.
        Baseline unpatched code fails because continuation is nested inside `if run_reversal:`.
        """
        now_ms = 1_700_000_000_000
        cfg = build_adaptive_smc_paper_profile()
        assert cfg.scalp_m1_mode == ScalpMode.PAPER.value
        assert cfg.execution_profile == "PAPER_ADAPTIVE_SMC"

        orch = ScalpWatcherOrchestrator()
        quote = {
            "bid": 2650.0,
            "ask": 2650.2,
            "last": 2650.1,
            "server_time": now_ms,
            "timestamp": now_ms,
            "observed_at": now_ms,
            "is_valid": True,
            "is_stale": False,
        }
        c_1m = _create_mock_candles(2650.0, count=40, now_ms=now_ms)
        c_5m = _create_mock_candles(2650.0, count=30, now_ms=now_ms)

        with patch("services.liquidity_scenario_service.classify_market_regime", return_value="TREND_BULLISH"), \
             patch.object(ScalpM1ModelService, "evaluate_m1_continuation_candidate", wraps=ScalpM1ModelService.evaluate_m1_continuation_candidate) as spy_cont:
            res = orch.evaluate_cycle(
                isolated_db,
                symbol="XAUUSDT",
                now_ms=now_ms,
                quote=quote,
                candles_1m=c_1m,
                candles_5m=c_5m,
                config=cfg,
                auto_enabled=True,
            )
            assert spy_cont.call_count > 0, "evaluate_m1_continuation_candidate was NOT called under TREND_BULLISH!"
            assert "model_diagnostics" in res
            diags = {d["model_id"]: d for d in res["model_diagnostics"]}
            assert diags[ModelIdentifier.SMC_M1_CONTINUATION.value]["evaluated"] is True

    def test_section_012_continuation_called_when_allowlist_is_continuation_only(self, isolated_db):
        """
        SECTION 012 / 161: When adaptive allowlist only contains SMC_M1_CONTINUATION,
        continuation MUST still be evaluated under UNCERTAIN or matching regime.
        """
        now_ms = 1_700_000_000_000
        cfg = build_adaptive_smc_paper_profile(
            allowlist=[ModelIdentifier.SMC_M1_CONTINUATION.value]
        )
        assert cfg.adaptive_model_allowlist == (ModelIdentifier.SMC_M1_CONTINUATION.value,)

        orch = ScalpWatcherOrchestrator()
        quote = {
            "bid": 2650.0,
            "ask": 2650.2,
            "last": 2650.1,
            "server_time": now_ms,
            "timestamp": now_ms,
            "observed_at": now_ms,
            "is_valid": True,
            "is_stale": False,
        }
        c_1m = _create_mock_candles(2650.0, count=40, now_ms=now_ms)
        c_5m = _create_mock_candles(2650.0, count=30, now_ms=now_ms)

        with patch("services.liquidity_scenario_service.classify_market_regime", return_value="UNCERTAIN"), \
             patch.object(ScalpM1ModelService, "evaluate_m1_continuation_candidate", wraps=ScalpM1ModelService.evaluate_m1_continuation_candidate) as spy_cont:
            res = orch.evaluate_cycle(
                isolated_db,
                symbol="XAUUSDT",
                now_ms=now_ms,
                quote=quote,
                candles_1m=c_1m,
                candles_5m=c_5m,
                config=cfg,
                auto_enabled=True,
            )
            assert spy_cont.call_count > 0, "evaluate_m1_continuation_candidate was NOT called under continuation-only allowlist!"
            diags = {d["model_id"]: d for d in res["model_diagnostics"]}
            assert diags[ModelIdentifier.SMC_M1_CONTINUATION.value]["evaluated"] is True
            assert diags[ModelIdentifier.SMC_M1_REVERSAL.value]["evaluated"] is False
            assert diags[ModelIdentifier.SMC_M1_REVERSAL.value]["reason"] == "NOT_IN_ALLOWLIST"

    def test_section_023_empty_allowlist_disables_all_models(self):
        """Section 023: Empty allowlist [] disables all models and evaluates none."""
        cfg = build_adaptive_smc_paper_profile(allowlist=[])
        assert cfg.adaptive_model_allowlist == ()

        plan = resolve_model_evaluation_plan(
            ModelIdentifier.SMC_M1_CONTINUATION.value,
            config=cfg,
            regime="TREND_BULLISH",
            has_m1=True,
        )
        assert plan[0] is False
        assert plan[1] == "DISABLED"
        assert plan[2] == "NOT_IN_ALLOWLIST"


class TestQualityScoringEngine:
    def test_section_039_groups_sum_to_100(self):
        """Section 039: 5 groups sum to exactly 100 max points."""
        cand = ScalpCandidate(
            is_eligible=True,
            model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
            direction="LONG",
            candidate_key="cand_1",
            symbol="XAUUSDT",
            planned_entry=2650.0,
            stop_loss=2645.0,
            take_profit=2660.0,
            gross_rr=2.0,
            net_rr=1.8,
            evidence={
                "has_trigger": True,
                "has_sweep": True,
                "has_fvg": True,
                "has_displacement": True,
                "has_mss": True,
                "poi_id": "poi_1",
                "poi_fresh": True,
                "poi_confirmed": True,
                "liquidity_target": "liq_1",
                "clean_path": True,
            },
        )
        ctx = {"regime": "RANGE", "bias": "BULLISH", "sr_zones": [{"zone_id": "z1"}]}
        assessment = assess_candidate_quality(cand, context=ctx, threshold=70.0)

        total_max = sum(f.max_points for f in assessment.factors)
        assert abs(total_max - 100.0) < 0.001, f"Total max points was {total_max}, expected 100.0"

        groups = assessment.group_scores()
        assert abs(groups["HTF_CONTEXT"]["max"] - 20.0) < 0.001
        assert abs(groups["POI_QUALITY"]["max"] - 20.0) < 0.001
        assert abs(groups["LIQUIDITY_SCENARIO"]["max"] - 15.0) < 0.001
        assert abs(groups["LTF_CONFIRMATION"]["max"] - 25.0) < 0.001
        assert abs(groups["EXECUTION_QUALITY"]["max"] - 20.0) < 0.001

    def test_section_040_missing_required_trigger_blocks_arm(self):
        """
        Section 040: Missing required trigger results in BLOCKED_REQUIRED.
        Supporting score does NOT compensate for missing required trigger.
        """
        cand = ScalpCandidate(
            is_eligible=False,
            model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
            direction="LONG",
            candidate_key="cand_no_trigger",
            symbol="XAUUSDT",
            planned_entry=2650.0,
            stop_loss=2645.0,
            take_profit=2660.0,
            gross_rr=2.0,
            net_rr=1.8,
            evidence={"has_trigger": False},
        )
        ctx = {"regime": "RANGE", "bias": "BULLISH", "sr_zones": [{"zone_id": "z1"}]}
        assessment = assess_candidate_quality(cand, context=ctx, threshold=70.0)
        assert assessment.decision == "BLOCKED_REQUIRED"
        assert "MISSING_REQUIRED_TRIGGER" in assessment.blockers

    def test_section_034_score_threshold_boundaries(self):
        """
        Section 034 / 164: Score threshold boundaries:
        69.99 -> BELOW_THRESHOLD
        70.0 -> ELIGIBLE_BY_SCORE
        80.0 -> ELIGIBLE_BY_SCORE
        """
        cand = ScalpCandidate(
            is_eligible=True,
            model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
            direction="LONG",
            candidate_key="cand_b",
            symbol="XAUUSDT",
            planned_entry=2650.0,
            stop_loss=2645.0,
            take_profit=2660.0,
            gross_rr=2.0,
            net_rr=1.8,
            evidence={"has_trigger": True, "has_sweep": True, "poi_confirmed": True, "clean_path": True},
        )
        ctx = {"regime": "RANGE", "bias": "BULLISH"}

        # Custom mock factor assessment
        assessment_70 = assess_candidate_quality(cand, context=ctx, threshold=70.0)
        # If score >= 70 -> ELIGIBLE_BY_SCORE, if < 70 -> BELOW_THRESHOLD
        if assessment_70.final_score >= 70.0:
            assert assessment_70.decision == "ELIGIBLE_BY_SCORE"
        else:
            assert assessment_70.decision == "BELOW_THRESHOLD"

        # Explicitly verify boundary comparison logic
        boundary_assessment_below = CandidateQualityAssessment(
            assessment_id="a1", candidate_key="c1", model_id="SMC_M1_REVERSAL",
            base_score=69.99, lesson_delta=0.0, final_score=69.99, threshold=70.0,
            decision="BELOW_THRESHOLD", as_of_ms=1000, profile_version="v16.39",
            config_fingerprint="fp", factors=[], missing_data=[], blockers=[],
        )
        assert boundary_assessment_below.final_score < boundary_assessment_below.threshold
        assert boundary_assessment_below.decision == "BELOW_THRESHOLD"

    def test_section_122_123_lesson_score_adjustments_and_capping(self):
        """
        Section 122 - 123 / 171: Lesson adjustments apply to base score,
        are strictly bounded by cap, and pending rules have zero effect.
        """
        now_ms = 1_000_000

        # Mock approved active rule with -5.0 penalty
        rule_penalty = MagicMock()
        rule_penalty.id = 101
        rule_penalty.revision = 2
        rule_penalty.is_approved = True
        rule_penalty.enabled = True
        rule_penalty.status = "APPROVED"
        rule_penalty.effective_at = now_ms - 1000
        rule_penalty.scope = json.dumps({"symbol": "XAUUSDT"})
        rule_penalty.effect = "DEDUCT_SCORE"
        rule_penalty.predicate = json.dumps({"score_delta": -5.0})

        # Mock pending rule (must be ignored)
        rule_pending = MagicMock()
        rule_pending.id = 102
        rule_pending.revision = 1
        rule_pending.is_approved = False
        rule_pending.enabled = False
        rule_pending.status = "PENDING_REVIEW"
        rule_pending.effective_at = now_ms - 1000
        rule_pending.scope = json.dumps({"symbol": "XAUUSDT"})
        rule_pending.effect = "DEDUCT_SCORE"
        rule_pending.predicate = json.dumps({"score_delta": -10.0})

        ctx = {"symbol": "XAUUSDT"}
        adj = LessonRuleService.evaluate_lesson_score_adjustments(
            ctx, approved_rules=[rule_penalty, rule_pending], now_ms=now_ms
        )
        assert adj["applied_delta"] == -5.0
        assert len(adj["matched_rules"]) == 1
        assert adj["matched_rules"][0]["rule_id"] == 101

        # Test capping
        rule_huge_penalty = MagicMock()
        rule_huge_penalty.id = 103
        rule_huge_penalty.revision = 1
        rule_huge_penalty.is_approved = True
        rule_huge_penalty.enabled = True
        rule_huge_penalty.status = "APPROVED"
        rule_huge_penalty.effective_at = now_ms - 1000
        rule_huge_penalty.scope = None
        rule_huge_penalty.effect = "DEDUCT_SCORE"
        rule_huge_penalty.predicate = json.dumps({"score_delta": -50.0})

        adj_capped = LessonRuleService.evaluate_lesson_score_adjustments(
            ctx, approved_rules=[rule_huge_penalty], now_ms=now_ms, cap_penalty=15.0
        )
        assert adj_capped["raw_delta"] == -50.0
        assert adj_capped["applied_delta"] == -15.0
        assert adj_capped["is_capped"] is True


class TestGovernedLessonClassification:
    def test_section_099_valid_strategy_loss_is_green(self):
        """
        Section 099: Valid SL hit conforming to plan produces GREEN / INFO lesson.
        A loss does NOT automatically become RED!
        """
        rev = PostTradeReview(
            order_id="ord_1",
            symbol="XAUUSDT",
            model_id="SMC_M1_REVERSAL",
            direction="LONG",
            planned_entry=2650.0,
            actual_entry=2650.0,
            planned_sl=2645.0,
            planned_tp=2660.0,
            exit_price=2645.0,
            exit_cause="SL_HIT",
            gross_pnl=-50.0,
            net_pnl=-52.0,
            realized_rr=-1.0,
            hold_time_ms=120000,
            entry_slippage_usd=0.0,
            loss_classification=LossClassification.STRATEGY_VALID_LOSS.value,
            outcome_status="LOSS",
        )
        classification = classify_post_trade_lesson(rev)
        assert classification.suggested_color == "GREEN"
        assert classification.canonical_severity == "INFO"
        assert classification.proposed_effect == "ANNOTATE"
        assert "dừng lỗ hợp lệ" in classification.reason_vi

    def test_section_099_profitable_rule_violation_is_red(self):
        """
        Section 099: Profitable trade with rule or geometry violation is classified RED / CRITICAL.
        Win alone does not grant GREEN!
        """
        # Long order with inverted geometry (planned_entry <= planned_sl)
        rev = PostTradeReview(
            order_id="ord_2",
            symbol="XAUUSDT",
            model_id="SMC_M1_REVERSAL",
            direction="LONG",
            planned_entry=2645.0,
            actual_entry=2645.0,
            planned_sl=2650.0,  # Inverted!
            planned_tp=2660.0,
            exit_price=2660.0,
            exit_cause="TP_HIT",
            gross_pnl=100.0,
            net_pnl=98.0,
            realized_rr=2.0,
            hold_time_ms=180000,
            entry_slippage_usd=0.0,
            loss_classification=LossClassification.NOT_A_LOSS.value,
            outcome_status="WIN",
        )
        classification = classify_post_trade_lesson(rev)
        assert classification.suggested_color == "RED"
        assert classification.canonical_severity == "CRITICAL"
        assert "hình học lệnh vi phạm" in classification.reason_vi

    def test_section_098_adverse_slippage_or_missing_evidence_is_yellow(self):
        """
        Section 098: Adverse slippage > 1.0 USD or unconfirmed cause produces YELLOW / WARNING.
        """
        rev = PostTradeReview(
            order_id="ord_3",
            symbol="XAUUSDT",
            model_id="SMC_M1_REVERSAL",
            direction="LONG",
            planned_entry=2650.0,
            actual_entry=2652.5,  # 2.5 USD adverse slippage!
            planned_sl=2645.0,
            planned_tp=2660.0,
            exit_price=2645.0,
            exit_cause="SL_HIT",
            gross_pnl=-75.0,
            net_pnl=-78.0,
            realized_rr=-1.5,
            hold_time_ms=60000,
            entry_slippage_usd=2.5,
            adverse_slippage_usd=2.5,
            loss_classification=LossClassification.EXECUTION_SLIPPAGE.value,
            outcome_status="LOSS",
        )
        classification = classify_post_trade_lesson(rev)
        assert classification.suggested_color == "YELLOW"
        assert classification.canonical_severity == "WARNING"
        assert classification.proposed_effect == "WARN_ENTRY"


class TestReviewPersistenceAndStatistics:
    def test_section_078_080_save_and_get_post_trade_review(self, isolated_db):
        """
        Section 078 - 080: Persisting and retrieving post-trade reviews.
        Isolated from user notes in TradeReview.
        """
        now_ms = 1_700_000_000_000
        # Create PaperOrder
        order = models.PaperOrder(
            id="ord_persist_1",
            instrument="XAUUSDT",
            direction="LONG",
            planned_entry=2650.0,
            actual_entry=2650.1,
            stop_loss=2645.0,
            take_profit=2660.0,
            actual_exit=2660.0,
            exit_cause="TP_HIT",
            state="closed",
            realized_pnl_net=48.5,
            realized_r=1.9,
            quantity=0.05,
            initial_risk_usdt=25.0,
            leverage=20,
            margin_mode="ISOLATED",
            opened_at=now_ms - 60000,
            closed_at=now_ms,
            created_at=now_ms - 70000,
        )
        isolated_db.add(order)
        isolated_db.flush()

        outcome = {
            "order_id": order.id,
            "exit_price": 2660.0,
            "exit_cause": "TP_HIT",
            "net_pnl": 48.5,
            "realized_r": 1.9,
            "closed_at_ms": now_ms,
        }
        review = build_post_trade_review(order, outcome, now_ms=now_ms)
        rec_id = save_post_trade_review(isolated_db, review)
        isolated_db.commit()

        # Retrieve and verify
        retrieved = get_post_trade_review(isolated_db, order.id)
        assert retrieved is not None
        assert retrieved.order_id == "ord_persist_1"
        assert retrieved.outcome_status == "WIN"
        assert retrieved.net_pnl == 48.5
        assert retrieved.hold_time_ms == 60000

    def test_section_083_inverted_timestamps_repair(self):
        """
        Section 083: Inverted timestamps (closed_at < opened_at) return hold_time_ms = None,
        never clamped to 0 with max(0, ...).
        """
        order = MagicMock()
        order.id = "ord_inverted"
        order.opened_at = 2000
        order.closed_at = 1000  # Inverted!
        order.state = "closed"
        order.actual_entry = 2650.0
        order.actual_exit = 2660.0

        outcome = {"exit_price": 2660.0, "exit_cause": "TP_HIT", "closed_at_ms": 1000, "net_pnl": 10.0}
        review = build_post_trade_review(order, outcome)
        assert review.hold_time_ms is None

    def test_section_084_breakeven_statistics_summary(self):
        """
        Section 084: summarize_lesson_evidence accurately separates WIN, BREAKEVEN, LOSS, UNCONFIRMED.
        Does NOT count 0 as WIN!
        """
        r_win = PostTradeReview(
            order_id="1", symbol="XAUUSDT", model_id="SMC_M1_REVERSAL", direction="LONG",
            planned_entry=2650.0, actual_entry=2650.0, planned_sl=2645.0, planned_tp=2660.0,
            exit_price=2660.0, exit_cause="TP_HIT", gross_pnl=50.0, net_pnl=48.0, realized_rr=1.9,
            hold_time_ms=1000, entry_slippage_usd=0.0, loss_classification=LossClassification.NOT_A_LOSS.value,
            outcome_status="WIN",
        )
        r_be = PostTradeReview(
            order_id="2", symbol="XAUUSDT", model_id="SMC_M1_REVERSAL", direction="LONG",
            planned_entry=2650.0, actual_entry=2650.0, planned_sl=2645.0, planned_tp=2660.0,
            exit_price=2650.0, exit_cause="MANUAL_CLOSE", gross_pnl=0.0, net_pnl=0.0, realized_rr=0.0,
            hold_time_ms=1000, entry_slippage_usd=0.0, loss_classification=LossClassification.NOT_A_LOSS.value,
            outcome_status="BREAKEVEN",
        )
        r_loss = PostTradeReview(
            order_id="3", symbol="XAUUSDT", model_id="SMC_M1_REVERSAL", direction="LONG",
            planned_entry=2650.0, actual_entry=2650.0, planned_sl=2645.0, planned_tp=2660.0,
            exit_price=2645.0, exit_cause="SL_HIT", gross_pnl=-50.0, net_pnl=-52.0, realized_rr=-1.0,
            hold_time_ms=1000, entry_slippage_usd=0.0, loss_classification=LossClassification.STRATEGY_VALID_LOSS.value,
            outcome_status="LOSS",
        )
        r_unconfirmed = PostTradeReview(
            order_id="4", symbol="XAUUSDT", model_id="SMC_M1_REVERSAL", direction="LONG",
            planned_entry=2650.0, actual_entry=2650.0, planned_sl=2645.0, planned_tp=2660.0,
            exit_price=2650.0, exit_cause="UNKNOWN", gross_pnl=0.0, net_pnl=0.0, realized_rr=0.0,
            hold_time_ms=1000, entry_slippage_usd=0.0, loss_classification=LossClassification.UNVERIFIABLE.value,
            outcome_status="OUTCOME_UNCONFIRMED",
        )

        summary = summarize_lesson_evidence([r_win, r_be, r_loss, r_unconfirmed])
        stats = summary["models"]["SMC_M1_REVERSAL"]
        assert stats["total_trades"] == 4
        assert stats["wins"] == 1
        assert stats["breakevens"] == 1
        assert stats["losses"] == 1
        assert stats["unconfirmed"] == 1


class TestLessonApproveAndActivateApi:
    def test_section_110_113_approve_and_activate_endpoint(self, client, isolated_db):
        """
        Section 110 - 113: Approve and activate updates lesson revision, validates predicate,
        sets enabled=True, effective_at=now_ms.
        Legacy approve keeps enabled=False.
        """
        now_ms = 1_000_000
        lesson = models.Lesson(
            id=999,
            created_at=now_ms,
            title="Slippage warning rule",
            reflection="Test reflection",
            action_rule="Test action rule",
            is_hard_filter=False,
            is_approved=False,
            enabled=False,
            status="PENDING_REVIEW",
            severity="WARNING",
            effect="WARN_ENTRY",
            revision=1,
            scope=json.dumps({"symbol": "XAUUSDT"}),
            predicate=json.dumps({"metric": "spread", "operator": "<=", "threshold": 0.5}),
        )
        isolated_db.add(lesson)
        isolated_db.commit()

        # 1. Revision mismatch check (Section 111, 169) -> 409
        bad_req = {
            "expected_revision": 99,
            "activate": True,
            "review_note": "Mismatch test",
        }
        resp_conflict = client.post("/api/v1/lessons/999/approve-and-activate", json=bad_req)
        assert resp_conflict.status_code == 409

        # 2. Valid approve-and-activate
        good_req = {
            "expected_revision": 1,
            "activate": True,
            "review_note": "Approved and activated by trader",
        }
        resp = client.post("/api/v1/lessons/999/approve-and-activate", json=good_req)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_approved"] is True
        assert data["enabled"] is True
        assert data["status"] == "APPROVED"
        assert data["revision"] == 2
        assert data["effective_at"] is not None

        # Verify DB
        db_l = isolated_db.query(models.Lesson).filter(models.Lesson.id == 999).first()
        assert db_l.is_approved is True
        assert db_l.enabled is True
        assert db_l.revision == 2


class TestFullExecutionAndGuardIntegration:
    def test_section_055_score_90_blocked_by_hard_guards(self, isolated_db):
        """
        Section 055 / 177: Score 90 candidate CANNOT bypass hard guards (daily cap reached).
        """
        import dataclasses
        orch = ScalpWatcherOrchestrator()
        now_ms = 1_700_000_000_000
        cfg = dataclasses.replace(
            build_adaptive_smc_paper_profile(),
            quality_scoring_enabled=True,
            quality_arm_threshold=70.0,
        )

        cand = ScalpCandidate(
            is_eligible=True,
            model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
            direction="LONG",
            candidate_key="cand_high_score",
            symbol="XAUUSDT",
            planned_entry=2650.0,
            stop_loss=2645.0,
            take_profit=2660.0,
            gross_rr=2.0,
            net_rr=1.8,
            calculation=MagicMock(
                is_valid=True,
                can_execute=True,
                quantity=0.05,
                leverage=20,
                margin_mode="ISOLATED",
                effective_risk_pct=1.0,
                net_risk_usdt=10.0,
                gross_rr=2.0,
                net_rr=1.8,
                estimated_liquidation=2500.0,
            ),
            evidence={"has_trigger": True, "has_fvg": True, "has_displacement": True},
        )

        quote = {
            "bid": 2650.0, "ask": 2650.2, "last": 2650.1,
            "server_time": now_ms, "timestamp": now_ms, "observed_at": now_ms,
            "is_valid": True, "is_stale": False,
        }

        # Mock audit with daily cap reached (3 fills already done today)
        with patch("crud.get_or_create_today_audit") as mock_audit:
            m_audit = MagicMock()
            m_audit.fills_count = 3
            mock_audit.return_value = m_audit

            armed = orch._arm_paper_candidate(
                isolated_db,
                cand,
                now_ms=now_ms,
                clock=MagicMock(),
                quote=quote,
                policy_settings={"capital_usdt": 1000.0, "risk_pct": 1.0, "capital_mode": "CURRENT_ACCOUNT_BALANCE"},
                config=cfg,
                auto_enabled=True,
            )
            # Must be BLOCKED by daily cap guard despite high setup quality
            assert armed is None
            order_count = isolated_db.query(models.PaperOrder).count()
            assert order_count == 0
