"""
backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py
V16.39.8 — CLOSE PRODUCTION LESSON-TO-ARM ACCEPTANCE
TEST-FIRST, MINIMAL PATCH, PRESERVE CURRENT TRADING FLOW

Verifies the complete production call graph:
HTTP mutation -> DB persistence -> cache invalidation ->
evaluate_cycle -> _arm_paper_candidate ->
rule service -> scorer -> ARM/funnel decision.

Core Acceptance Gates:
1. High-Score Candidate (Rules 004, 071 - 090):
   Pending delta = 0.0 -> score 90.0 -> ARM
   Active delta = -5.0 -> score 85.0 >= 70.0 -> ARM (penalty does not block trade)
   Disabled delta = 0.0 -> score 90.0 -> ARM
2. Threshold Crossing Gate (Rules 005, 101 - 135):
   Baseline score 82.0 >= 70.0 -> ARM
   Active penalty -15.0 -> score 67.0 < 70.0 -> BELOW_THRESHOLD -> GUARD_BLOCKED in Funnel -> NO ARM
   Disabled delta = 0.0 -> score 82.0 >= 70.0 -> ARM restored
3. Bearish SHORT Candidate (Rules 091 - 093):
   Bearish candles fixture -> SHORT geometry TP < planned_entry < SL -> ARM
4. Full Production Lifecycle (Rules 094 - 100, 155 - 161):
   ARM -> fill -> exit -> post-trade review -> pending lesson (not auto-activated)
5. Timeline & Scope Isolation Negatives (Rules 124, 125, 136 - 140):
   Decision before effective_at has delta 0.0
   Out of scope session/symbol/model has delta 0.0
6. Threshold Boundary Invariants (Rules 126, 127):
   Score == 70.0 is ELIGIBLE_BY_SCORE
   Score == 69.9 is BELOW_THRESHOLD
"""
import json
import time
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Sequence
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

import models
from models import Base, PaperOrder, Lesson, OpportunityFunnelRecord, PostTradeReviewRecord
from main import app, get_db
from services.cg01_types import CG01Config
from services.execution_coordinator import ExecutionCoordinator
from services.lesson_rule_service import LessonRuleService
from services.opportunity_funnel_service import OpportunityFunnelService
from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier, WatchedZone
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.setup_quality_service import (
    assess_candidate_quality,
    build_candidate_scoring_context,
    normalize_candidate_evidence,
    CandidateQualityAssessment,
)
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    build_short_fixture_series,
    make_valid_quote,
)


# ==============================================================================
# TEST UTILITIES & ISOLATED ENVIRONMENT (Rules 044, 045, 046, 047)
# ==============================================================================

@dataclass
class PaperAcceptanceResult:
    """Structured result holding production evaluation observations without replacing production logic."""
    assessment: Optional[CandidateQualityAssessment] = None
    base_score: Optional[float] = None
    lesson_delta: Optional[float] = None
    final_score: Optional[float] = None
    decision: Optional[str] = None
    order_id: Optional[str] = None
    order_state: Optional[str] = None
    armed_order: Optional[models.PaperOrder] = None
    funnel_stage: Optional[str] = None
    funnel_reasons: Tuple[str, ...] = ()
    funnel_reason_codes: Tuple[str, ...] = ()
    funnel_details: Dict[str, Any] = field(default_factory=dict)
    matched_rule_ids: Tuple[int, ...] = ()
    applied_delta: float = 0.0
    source_fixture_metadata: Dict[str, Any] = field(default_factory=dict)


class IsolatedPaperAcceptanceEnv:
    """
    Isolated test environment managing in-memory SQLite engine with StaticPool,
    session factory, dependency override for FastAPI TestClient, and teardown.
    """
    def __init__(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        
        # Initialize default policy in DB
        init_db = self.SessionFactory()
        try:
            policy = TradingPolicyService.get_or_create_policy(init_db)
            policy.max_daily_fills = 3
            policy.daily_min_goal = 2
            policy.ny_min_fills = 1
            policy.is_active = False  # Allows standard test fills without live calendar lockout
            init_db.commit()
        finally:
            init_db.close()

        # Cache invalidation for isolation
        LessonRuleService.invalidate_cache()

    def make_session(self) -> Session:
        return self.SessionFactory()

    def get_client(self) -> TestClient:
        def override_get_db():
            db = self.SessionFactory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app)

    def close(self):
        app.dependency_overrides.pop(get_db, None)
        LessonRuleService.invalidate_cache()
        self.engine.dispose()


def make_isolated_paper_acceptance_env() -> IsolatedPaperAcceptanceEnv:
    """Rule 045: Helper to create isolated test environment with clean DB, policy, and cleanup."""
    return IsolatedPaperAcceptanceEnv()


def make_test_scalp_config(
    quality_arm_threshold: float = 70.0,
    quality_scoring_enabled: bool = True,
    lesson_score_adjustment_enabled: bool = True,
    min_net_rr: float = 1.5,
) -> M1ScalpConfig:
    """Constructs effective production M1ScalpConfig with explicit quality and lesson adjustment flags."""
    return M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_NY_PAPER_EXPANDED",
        daily_max_cap=3,
        min_net_rr=min_net_rr,
        quality_scoring_enabled=quality_scoring_enabled,
        lesson_score_adjustment_enabled=lesson_score_adjustment_enabled,
        quality_arm_threshold=quality_arm_threshold,
    )


def execute_cycle_with_spy(
    db: Session,
    orchestrator: ScalpWatcherOrchestrator,
    *,
    symbol: str,
    now_ms: int,
    quote: Any,
    candles_1m: List[Any],
    candles_15m: List[Any],
    candles_1h: List[Any],
    candles_4h: List[Any],
    config: M1ScalpConfig,
    auto_enabled: bool = True,
) -> PaperAcceptanceResult:
    """
    Executes production orchestrator.evaluate_cycle and observes candidate quality assessment,
    resulting PaperOrder in DB, and OpportunityFunnelRecord transitions.
    Spy original wrapper calls original without mutating parameters or outputs (Rule 063).
    """
    orig_assess = assess_candidate_quality
    captured_assessments: List[CandidateQualityAssessment] = []

    def spy_assess(*args, **kwargs):
        res = orig_assess(*args, **kwargs)
        captured_assessments.append(res)
        return res

    with patch("services.setup_quality_service.assess_candidate_quality", side_effect=spy_assess):
        orchestrator.evaluate_cycle(
            db,
            symbol=symbol,
            now_ms=now_ms,
            quote=quote,
            candles_1m=candles_1m,
            candles_5m=[],
            candles_15m=candles_15m,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            config=config,
            auto_enabled=auto_enabled,
        )

    # Observe created order
    order = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()

    # Observe assessment
    assessment = captured_assessments[0] if captured_assessments else None

    # Observe funnel
    funnel_rec = (
        db.query(models.OpportunityFunnelRecord)
        .order_by(models.OpportunityFunnelRecord.id.desc())
        .first()
    )
    funnel_stage = funnel_rec.stage if funnel_rec else None
    funnel_reasons = tuple(json.loads(funnel_rec.reason_codes_json)) if (funnel_rec and funnel_rec.reason_codes_json) else ()
    funnel_payload = json.loads(funnel_rec.payload_json) if (funnel_rec and funnel_rec.payload_json) else {}

    return PaperAcceptanceResult(
        assessment=assessment,
        base_score=assessment.base_score if assessment else None,
        lesson_delta=assessment.lesson_delta if assessment else None,
        final_score=assessment.final_score if assessment else None,
        decision=assessment.decision if assessment else None,
        order_id=order.id if order else None,
        order_state=order.state if order else None,
        armed_order=order,
        funnel_stage=funnel_stage,
        funnel_reasons=funnel_reasons,
        funnel_reason_codes=funnel_reasons,
        funnel_details=funnel_payload.get("details", {}),
    )


# ==============================================================================
# GROUP 1: HIGH-SCORE CANDIDATE LESSON TO ARM ACCEPTANCE (Rules 004, 071 - 090)
# ==============================================================================

def test_010_orchestrator_high_score_long_pending_active_disabled_flow():
    """
    Rules 004, 071 - 090:
    Verifies that for a high-score candidate (base_score = 90.0):
    1. Phase 1 (PENDING): Pending lesson has delta 0.0 -> score 90.0 -> PaperOrder ARMED.
    2. Phase 2 (ACTIVE via API): HTTP approve-and-activate applies -5.0 penalty ->
       final_score = 90.0 - 5.0 = 85.0 >= 70.0 -> ELIGIBLE_BY_SCORE -> PaperOrder ARMED.
       (Crucial invariant: penalty reduces score but does NOT block trade if still above threshold).
    3. Phase 3 (DISABLED via API): HTTP set-enable(False) restores delta 0.0 -> score 90.0 -> ARMED.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 20, 10, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000
    approval_ms = t_start + 500_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)  # spread = 0.20
    poi = WatchedZone(
        zone_id="poi_high_score_test",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    cfg = make_test_scalp_config(quality_arm_threshold=70.0)

    # --------------------------------------------------------------------------
    # Phase 1: Seed PENDING Lesson and evaluate through Orchestrator
    # --------------------------------------------------------------------------
    env_p1 = make_isolated_paper_acceptance_env()
    try:
        session_p1 = env_p1.make_session()
        lesson_pending = Lesson(
            id=910,
            created_at=t_start + 400_000,
            related_trade_id="po_source_910",
            strategy_family="CG01_SWEEP_MSS_FVG",
            title="Execution spread adjustment lesson",
            reflection="Spread was within normal range.",
            action_rule="Deduct small penalty when spread is present.",
            status="PENDING_REVIEW",
            is_approved=False,
            enabled=False,
            severity="WARNING",
            effect="DEDUCT_SCORE",
            revision=1,
        )
        session_p1.add(lesson_pending)
        session_p1.commit()

        orch_p1 = ScalpWatcherOrchestrator()
        orch_p1.set_active_zones("XAUUSDT", [poi])
        res_p1 = execute_cycle_with_spy(
            session_p1,
            orch_p1,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
        )
        session_p1.close()

        assert res_p1.base_score == 90.0, "Baseline LONG candidate base score must be 90.0"
        assert res_p1.lesson_delta == 0.0, "Pending lesson must have lesson_delta == 0.0"
        assert res_p1.final_score == 90.0
        assert res_p1.decision == "ELIGIBLE_BY_SCORE"
        assert res_p1.order_id is not None, "Order must be armed when pending lesson has delta 0.0"
        assert res_p1.order_state == "armed"
    finally:
        env_p1.close()

    # --------------------------------------------------------------------------
    # Phase 2: HTTP API Approve and Activate (Rule 074 - 082)
    # --------------------------------------------------------------------------
    env_p2 = make_isolated_paper_acceptance_env()
    try:
        client_p2 = env_p2.get_client()
        session_p2_seed = env_p2.make_session()
        lesson_p2 = Lesson(
            id=910,
            created_at=t_start + 400_000,
            related_trade_id="po_source_910",
            strategy_family="CG01_SWEEP_MSS_FVG",
            title="Execution spread adjustment lesson",
            reflection="Spread was within normal range.",
            action_rule="Deduct small penalty when spread is present.",
            status="PENDING_REVIEW",
            is_approved=False,
            enabled=False,
            severity="WARNING",
            effect="DEDUCT_SCORE",
            revision=1,
        )
        session_p2_seed.add(lesson_p2)
        session_p2_seed.commit()
        session_p2_seed.close()

        approval_payload = {
            "expected_revision": 1,
            "selected_severity": "WARNING",
            "selected_effect": "DEDUCT_SCORE",
            "score_delta": -5.0,
            "predicate": {"metric": "spread", "operator": "<=", "threshold": 0.50, "score_delta": -5.0},
            "scope": {
                "symbol": "XAUUSDT",
                "strategy_family": "CG01_SWEEP_MSS_FVG",
                "stage": "BEFORE_ARM",
                "execution_mode": "PAPER",
                "session": "ALL",
            },
            "activate": True,
            "review_note": "Approved by risk lead for live paper adjustment",
        }
        with patch("time.time", return_value=approval_ms / 1000.0):
            resp_approve = client_p2.post("/api/v1/lessons/910/approve-and-activate", json=approval_payload)
        assert resp_approve.status_code == 200, f"Approval API must return 200: {resp_approve.text}"
        appr_data = resp_approve.json()
        assert appr_data["is_approved"] is True
        assert appr_data["enabled"] is True
        assert appr_data["status"] == "APPROVED"
        assert appr_data["revision"] == 2
        effective_at = appr_data["effective_at"]
        assert effective_at == approval_ms

        # Direct DB persistence verification (Rule 078)
        check_db = env_p2.make_session()
        db_l = check_db.query(Lesson).filter_by(id=910).first()
        assert db_l.is_approved is True
        assert db_l.enabled is True
        assert db_l.revision == 2
        assert db_l.validation_status == "VALID"
        check_db.close()

        # Evaluate through orchestrator at now_ms > effective_at (Rule 080)
        session_p2_eval = env_p2.make_session()
        orch_p2 = ScalpWatcherOrchestrator()
        orch_p2.set_active_zones("XAUUSDT", [poi])
        res_p2 = execute_cycle_with_spy(
            session_p2_eval,
            orch_p2,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
        )
        session_p2_eval.close()

        assert res_p2.base_score == 90.0
        assert res_p2.lesson_delta == -5.0, "Active approved lesson must deduct 5.0 points through orchestrator"
        assert res_p2.final_score == 85.0, "90.0 - 5.0 must equal 85.0"
        assert res_p2.decision == "ELIGIBLE_BY_SCORE"
        # Rule 082: Order must STILL be armed because 85.0 >= 70.0
        assert res_p2.order_id is not None, "High-score candidate must still ARM after small penalty"
        assert res_p2.order_state == "armed"
    finally:
        env_p2.close()

    # --------------------------------------------------------------------------
    # Phase 3: HTTP API Disable Rule (Rules 083 - 085)
    # --------------------------------------------------------------------------
    env_p3 = make_isolated_paper_acceptance_env()
    try:
        client_p3 = env_p3.get_client()
        session_p3_seed = env_p3.make_session()
        lesson_p3 = Lesson(
            id=910,
            created_at=t_start + 400_000,
            related_trade_id="po_source_910",
            strategy_family="CG01_SWEEP_MSS_FVG",
            title="Execution spread adjustment lesson",
            reflection="Spread was within normal range.",
            action_rule="Deduct small penalty when spread is present.",
            status="PENDING_REVIEW",
            is_approved=False,
            enabled=False,
            severity="WARNING",
            effect="DEDUCT_SCORE",
            revision=1,
        )
        session_p3_seed.add(lesson_p3)
        session_p3_seed.commit()
        session_p3_seed.close()

        # Approve first
        with patch("time.time", return_value=approval_ms / 1000.0):
            client_p3.post("/api/v1/lessons/910/approve-and-activate", json=approval_payload)
        
        # Then disable via API (Rule 083)
        with patch("time.time", return_value=(approval_ms + 10_000) / 1000.0):
            resp_disable = client_p3.post("/api/v1/lessons/910/set-enable", json={"enabled": False, "expected_revision": 2})
        assert resp_disable.status_code == 200
        assert resp_disable.json()["enabled"] is False

        # Evaluate through orchestrator
        session_p3_eval = env_p3.make_session()
        orch_p3 = ScalpWatcherOrchestrator()
        orch_p3.set_active_zones("XAUUSDT", [poi])
        res_p3 = execute_cycle_with_spy(
            session_p3_eval,
            orch_p3,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
        )
        session_p3_eval.close()

        assert res_p3.base_score == 90.0
        assert res_p3.lesson_delta == 0.0, "Disabled rule must result in lesson_delta == 0.0"
        assert res_p3.final_score == 90.0, "Score must restore to baseline 90.0"
        assert res_p3.decision == "ELIGIBLE_BY_SCORE"
        assert res_p3.order_id is not None
        assert res_p3.order_state == "armed"
    finally:
        env_p3.close()


# ==============================================================================
# GROUP 2: THRESHOLD CROSSING LESSON TO ARM REJECTION (Rules 005, 101 - 135)
# ==============================================================================

def test_020_orchestrator_threshold_crossing_candidate_rejected_by_active_lesson():
    """
    Rules 005, 101 - 135:
    Proves that a real candidate from market detector series:
    1. Baseline: Base score is 82.0 (>= 70.0 threshold) -> ELIGIBLE_BY_SCORE -> PaperOrder ARMED.
    2. Active Lesson: HTTP approved penalty rule (-15.0 within cap) is evaluated through
       orchestrator evaluate_cycle -> final_score = 82.0 - 15.0 = 67.0 < 70.0 ->
       decision crosses to BELOW_THRESHOLD -> _arm_paper_candidate blocks entry ->
       OpportunityFunnelRecord records GUARD_BLOCKED with reason ["BELOW_THRESHOLD"] ->
       NO PaperOrder created!
    3. Disabled Lesson: HTTP set-enable(False) restores final_score to 82.0 -> ARM eligibility restored!
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 20, 10, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000
    approval_ms = t_start + 500_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    # Market quote with spread = 0.38 and current price 2628.88 (dist = 5.13 USD ~ 0.85 ATR)
    # Produces deterministic base score = 82.0:
    # EXEC_SPREAD_BUDGET: 1.0/5.0 (-4.0 pts)
    # EXEC_ENTRY_DISTANCE: 1.0/5.0 (-4.0 pts)
    # Total base score = 90.0 - 8.0 = 82.0 >= 70.0
    quote = make_valid_quote(2628.69, 2629.07, now_ms)

    poi = WatchedZone(
        zone_id="poi_crossing_test",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    cfg = make_test_scalp_config(quality_arm_threshold=70.0)

    # --------------------------------------------------------------------------
    # Phase 1: Baseline Candidate (Before Lesson Effect)
    # --------------------------------------------------------------------------
    env_base = make_isolated_paper_acceptance_env()
    try:
        session_base = env_base.make_session()
        orch_base = ScalpWatcherOrchestrator()
        orch_base.set_active_zones("XAUUSDT", [poi])
        res_base = execute_cycle_with_spy(
            session_base,
            orch_base,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
        )
        session_base.close()

        assert res_base.base_score == 82.0, f"Expected base score 82.0, got {res_base.base_score}"
        assert res_base.final_score == 82.0
        assert res_base.decision == "ELIGIBLE_BY_SCORE"
        assert res_base.order_id is not None, "Baseline candidate must ARM an order"
        assert res_base.order_state == "armed"
    finally:
        env_base.close()

    # --------------------------------------------------------------------------
    # Phase 2: Seed Lesson and HTTP Approve & Activate (-15.0 Penalty)
    # --------------------------------------------------------------------------
    env_active = make_isolated_paper_acceptance_env()
    try:
        client_act = env_active.get_client()
        session_seed = env_active.make_session()
        lesson_crossing = Lesson(
            id=920,
            created_at=t_start + 400_000,
            related_trade_id="po_source_crossing",
            strategy_family="CG01_SWEEP_MSS_FVG",
            title="Wide spread and distance execution penalty rule",
            reflection="Wide spread and chase distance severely degrade net R:R edge.",
            action_rule="Deduct score when spread is wide.",
            status="PENDING_REVIEW",
            is_approved=False,
            enabled=False,
            severity="WARNING",
            effect="DEDUCT_SCORE",
            revision=1,
        )
        session_seed.add(lesson_crossing)
        session_seed.commit()
        session_seed.close()

        # HTTP Approve and Activate with -15.0 penalty for spread >= 0.35
        appr_payload = {
            "expected_revision": 1,
            "selected_severity": "WARNING",
            "selected_effect": "DEDUCT_SCORE",
            "score_delta": -15.0,
            "predicate": {"metric": "spread", "operator": ">=", "threshold": 0.35, "score_delta": -15.0},
            "scope": {
                "symbol": "XAUUSDT",
                "strategy_family": "CG01_SWEEP_MSS_FVG",
                "stage": "BEFORE_ARM",
                "execution_mode": "PAPER",
                "session": "ALL",
            },
            "activate": True,
            "review_note": "Penalize high execution cost setups",
        }
        with patch("time.time", return_value=approval_ms / 1000.0):
            resp = client_act.post("/api/v1/lessons/920/approve-and-activate", json=appr_payload)
        assert resp.status_code == 200
        appr_data = resp.json()
        assert appr_data["enabled"] is True
        effective_at = appr_data["effective_at"]
        assert effective_at == approval_ms

        # Evaluate through orchestrator at now_ms > effective_at
        session_active = env_active.make_session()
        orch_active = ScalpWatcherOrchestrator()
        orch_active.set_active_zones("XAUUSDT", [poi])
        res_active = execute_cycle_with_spy(
            session_active,
            orch_active,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
        )

        # Assert Threshold Crossing Gate (Rules 119 - 121)
        assert res_active.base_score == 82.0
        assert res_active.lesson_delta == -15.0
        assert res_active.final_score == 67.0, "82.0 - 15.0 must equal 67.0"
        assert res_active.final_score < 70.0, "Final score must cross below 70.0 threshold"
        assert res_active.decision == "BELOW_THRESHOLD"

        # Assert that NO PaperOrder was armed for this candidate!
        assert res_active.order_id is None, "Candidate below threshold must NOT create armed PaperOrder"
        armed_in_db = session_active.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
        assert armed_in_db is None, "Database must contain ZERO armed orders when blocked by quality score"

        # Assert OpportunityFunnelRecord accurately captured the rejection reason (Rule 120, 121)
        funnel_rec = (
            session_active.query(models.OpportunityFunnelRecord)
            .filter(models.OpportunityFunnelRecord.stage == "GUARD_BLOCKED")
            .order_by(models.OpportunityFunnelRecord.id.desc())
            .first()
        )
        assert funnel_rec is not None, "OpportunityFunnelRecord must record transition to GUARD_BLOCKED"
        reasons = json.loads(funnel_rec.reason_codes_json)
        assert "BELOW_THRESHOLD" in reasons, f"Reason codes must include BELOW_THRESHOLD: {reasons}"
        payload = json.loads(funnel_rec.payload_json)
        q_details = payload.get("details", {}).get("quality_assessment", {})
        assert q_details.get("final_score") == 67.0
        assert q_details.get("decision") == "BELOW_THRESHOLD"
        session_active.close()
    finally:
        env_active.close()

    # --------------------------------------------------------------------------
    # Phase 3: HTTP API Disable Restores ARM Eligibility (Rule 123)
    # --------------------------------------------------------------------------
    env_dis = make_isolated_paper_acceptance_env()
    try:
        client_dis = env_dis.get_client()
        session_dis_seed = env_dis.make_session()
        lesson_dis = Lesson(
            id=920,
            created_at=t_start + 400_000,
            related_trade_id="po_source_crossing",
            strategy_family="CG01_SWEEP_MSS_FVG",
            title="Wide spread and distance execution penalty rule",
            reflection="Wide spread and chase distance severely degrade net R:R edge.",
            action_rule="Deduct score when spread is wide.",
            status="PENDING_REVIEW",
            is_approved=False,
            enabled=False,
            severity="WARNING",
            effect="DEDUCT_SCORE",
            revision=1,
        )
        session_dis_seed.add(lesson_dis)
        session_dis_seed.commit()
        session_dis_seed.close()

        # Approve first
        with patch("time.time", return_value=approval_ms / 1000.0):
            client_dis.post("/api/v1/lessons/920/approve-and-activate", json=appr_payload)
        
        # Disable via API
        with patch("time.time", return_value=(approval_ms + 10_000) / 1000.0):
            resp_dis = client_dis.post("/api/v1/lessons/920/set-enable", json={"enabled": False, "expected_revision": 2})
        assert resp_dis.status_code == 200

        # Evaluate through orchestrator
        session_restored = env_dis.make_session()
        orch_restored = ScalpWatcherOrchestrator()
        orch_restored.set_active_zones("XAUUSDT", [poi])
        res_restored = execute_cycle_with_spy(
            session_restored,
            orch_restored,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
        )

        assert res_restored.base_score == 82.0
        assert res_restored.lesson_delta == 0.0, "Disabled rule must not apply penalty"
        assert res_restored.final_score == 82.0
        assert res_restored.decision == "ELIGIBLE_BY_SCORE"
        assert res_restored.order_id is not None, "Order must ARM after rule is disabled"
        assert res_restored.order_state == "armed"
        session_restored.close()
    finally:
        env_dis.close()


# ==============================================================================
# GROUP 3: SHORT CANDIDATE INTEGRATION (Rules 091 - 093)
# ==============================================================================

def test_030_orchestrator_short_candidate_geometry_and_lesson_flow():
    """
    Rules 091 - 093:
    Verifies that bearish candlestick fixture produces a valid SHORT candidate:
    1. Evaluated through production orchestrator evaluate_cycle.
    2. Verifies direction == 'SHORT' and geometry TP < planned_entry < SL.
    3. Verifies quality scoring and lesson rule service operate correctly on SHORT direction.
    """
    env = make_isolated_paper_acceptance_env()
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 10, 15, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    quote = make_valid_quote(2651.0, 2651.2, now_ms)
    poi_short = WatchedZone(
        zone_id="poi_short_15m",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BEARISH",
        top=2655.0,
        bottom=2650.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    cfg = make_test_scalp_config(quality_arm_threshold=70.0)

    try:
        session = env.make_session()
        orch = ScalpWatcherOrchestrator()
        orch.set_active_zones("XAUUSDT", [poi_short])
        res = execute_cycle_with_spy(
            session,
            orch,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
        )

        assert res.armed_order is not None, "Bearish candle series must produce armed PaperOrder"
        order = res.armed_order
        assert order.direction == "SHORT"
        # Rule 092: Assert TP < planned_entry < SL
        assert order.take_profit < order.planned_entry < order.stop_loss, (
            f"SHORT geometry violation: TP ({order.take_profit}) < entry ({order.planned_entry}) < SL ({order.stop_loss})"
        )
        assert res.decision == "ELIGIBLE_BY_SCORE"
        session.close()
    finally:
        env.close()


# ==============================================================================
# GROUP 4: FULL PRODUCTION LIFECYCLE ARM -> FILL -> EXIT -> REVIEW (Rules 094 - 100)
# ==============================================================================

def test_040_full_production_lifecycle_arm_fill_exit_review_lesson():
    """
    Rules 094 - 100:
    Verifies full production execution lifecycle starting from orchestrator ARM:
    1. evaluate_cycle creates armed PaperOrder.
    2. TradeLifecycleService.execute_fill fills order at planned entry -> paper_open.
    3. TradeLifecycleService.process_exit_tick closes order at TP -> closed.
    4. Post-trade review and Lesson are created in DB.
    5. Newly generated lesson defaults to PENDING_REVIEW, is_approved=False, enabled=False.
    6. Verifies PnL and risk settings invariants.
    """
    env = make_isolated_paper_acceptance_env()

    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 20, 10, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)
    poi = WatchedZone(
        zone_id="poi_lifecycle_test",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BULLISH",
        top=2625.0,
        bottom=2620.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )
    cfg = make_test_scalp_config(quality_arm_threshold=70.0)

    try:
        session = env.make_session()
        orch = ScalpWatcherOrchestrator()
        orch.set_active_zones("XAUUSDT", [poi])
        res = execute_cycle_with_spy(
            session,
            orch,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
        )

        order = res.armed_order
        assert order is not None
        assert order.state == "armed"
        planned_entry = order.planned_entry
        planned_sl = order.stop_loss
        planned_tp = order.take_profit

        # 2. Fill order using TradeLifecycleService (Rule 094)
        from domain_calculator import calculate_risk_reward
        fill_time = now_ms + 10_000
        calc = calculate_risk_reward(
            direction="LONG",
            entry=planned_entry,
            sl=planned_sl,
            tp=planned_tp,
            capital=1000.0,
            risk_pct=0.01,
            min_net_rr=1.5,
            now_ms=fill_time,
        )
        assert calc.is_valid
        filled = TradeLifecycleService.execute_fill(
            session,
            order,
            fill_price=planned_entry,
            calc_result=calc,
            source="AUTO",
            now_ms=fill_time,
        )
        assert filled.state == "paper_open"
        assert filled.actual_entry == planned_entry

        # 3. Process exit tick (TP hit) (Rule 095)
        exit_time = fill_time + 30_000
        closed = TradeLifecycleService.process_exit_tick(
            session,
            current_bid=planned_tp + 0.5,
            current_ask=planned_tp + 0.7,
            now_ms=exit_time,
        )
        assert closed is not None
        assert closed.state == "closed"
        assert closed.exit_cause == "TP_HIT"
        # Rule 098: Realized net PnL is purely financial
        assert closed.realized_pnl_net > 0.0

        # 4. Assert Review and Lesson in DB (Rules 096, 097)
        review = session.query(PostTradeReviewRecord).filter_by(order_id=order.id).first()
        assert review is not None
        assert review.outcome_status == "WIN"

        new_lesson = session.query(Lesson).filter_by(related_trade_id=order.id).first()
        assert new_lesson is not None
        # Rule 097: Newly created lesson MUST be pending review and not active
        assert new_lesson.status == "PENDING_REVIEW"
        assert new_lesson.is_approved is False
        assert new_lesson.enabled is False
        facts = json.loads(new_lesson.facts_snapshot)
        assert facts.get("verification_status") == "VERIFIED"
        assert facts.get("entry_mode_source") == "SCENARIO_SNAPSHOT"

        session.close()
    finally:
        env.close()


# ==============================================================================
# GROUP 5: TIMELINE & SCOPE ISOLATION NEGATIVES (Rules 124, 136 - 140)
# ==============================================================================

def test_050_timeline_decision_before_effective_at_not_penalized():
    """
    Rules 124, 125:
    A candidate evaluated at decision_time < effective_at is NOT subject to penalty,
    even if the rule is approved with future effective_at.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 20, 10, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000
    future_effective_at = now_ms + 10_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)
    poi = WatchedZone(zone_id="poi_tl_test", symbol="XAUUSDT", timeframe="15M", zone_type="FVG", direction="BULLISH", top=2625.0, bottom=2620.0, created_at=t_start - 3600_000, known_at=t_start - 1800_000, touch_count=0)
    cfg = make_test_scalp_config(quality_arm_threshold=70.0)

    # 1. Decision before effective_at
    env_before = make_isolated_paper_acceptance_env()
    try:
        session_b = env_before.make_session()
        lesson_b = Lesson(
            id=950,
            created_at=t_start + 400_000,
            related_trade_id="po_tl_source",
            strategy_family="CG01_SWEEP_MSS_FVG",
            title="Future effective rule",
            reflection="Test future effective time.",
            action_rule="Deduct score.",
            status="APPROVED",
            is_approved=True,
            enabled=True,
            validation_status="VALID",
            effective_at=future_effective_at,
            severity="WARNING",
            effect="DEDUCT_SCORE",
            scope=json.dumps({"symbol": "XAUUSDT", "strategy_family": "CG01_SWEEP_MSS_FVG"}),
            predicate=json.dumps({"metric": "spread", "operator": "<=", "threshold": 0.50, "score_delta": -15.0}),
            revision=2,
        )
        session_b.add(lesson_b)
        session_b.commit()
        LessonRuleService.invalidate_cache()

        orch_b = ScalpWatcherOrchestrator()
        orch_b.set_active_zones("XAUUSDT", [poi])
        res_before = execute_cycle_with_spy(
            session_b,
            orch_b,
            symbol="XAUUSDT",
            now_ms=now_ms,  # now_ms < future_effective_at
            quote=quote,
            candles_1m=c_1m,
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
        )
        assert res_before.lesson_delta == 0.0, "Decision before effective_at must not receive penalty"
        assert res_before.final_score == res_before.base_score
        assert res_before.order_id is not None
        session_b.close()
    finally:
        env_before.close()

    # 2. Decision after effective_at
    env_after = make_isolated_paper_acceptance_env()
    try:
        session_a = env_after.make_session()
        lesson_a = Lesson(
            id=950,
            created_at=t_start + 400_000,
            related_trade_id="po_tl_source",
            strategy_family="CG01_SWEEP_MSS_FVG",
            title="Future effective rule",
            reflection="Test future effective time.",
            action_rule="Deduct score.",
            status="APPROVED",
            is_approved=True,
            enabled=True,
            validation_status="VALID",
            effective_at=future_effective_at,
            severity="WARNING",
            effect="DEDUCT_SCORE",
            scope=json.dumps({"symbol": "XAUUSDT", "strategy_family": "CG01_SWEEP_MSS_FVG"}),
            predicate=json.dumps({"metric": "spread", "operator": "<=", "threshold": 0.50, "score_delta": -15.0}),
            revision=2,
        )
        session_a.add(lesson_a)
        session_a.commit()
        LessonRuleService.invalidate_cache()

        orch_a = ScalpWatcherOrchestrator()
        orch_a.set_active_zones("XAUUSDT", [poi])
        res_after = execute_cycle_with_spy(
            session_a,
            orch_a,
            symbol="XAUUSDT",
            now_ms=future_effective_at + 1000,  # now_ms > future_effective_at
            quote=quote,
            candles_1m=c_1m,
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
        )
        assert res_after.lesson_delta == -15.0, "Decision after effective_at must receive penalty"
        assert res_after.final_score == res_after.base_score - 15.0
        session_a.close()
    finally:
        env_after.close()


def test_060_scope_negative_isolations():
    """
    Rules 136 - 140:
    Verifies that rules out of scope (symbol, session, model) do NOT apply adjustments.
    """
    env = make_isolated_paper_acceptance_env()
    now_ms = 1700000000000

    try:
        session = env.make_session()
        # Rule scoped to LONDON session and symbol BTCUSDT
        lesson = Lesson(
            id=960,
            created_at=now_ms - 5000,
            related_trade_id="po_scope_source",
            strategy_family="CONTINUATION",
            title="London BTC rule",
            reflection="Test scope matching.",
            action_rule="Deduct score.",
            status="APPROVED",
            is_approved=True,
            enabled=True,
            validation_status="VALID",
            effective_at=now_ms - 2000,
            severity="WARNING",
            effect="DEDUCT_SCORE",
            scope=json.dumps({"symbol": "BTCUSDT", "strategy_family": "CONTINUATION", "session": "LONDON"}),
            predicate=json.dumps({"metric": "spread", "operator": "<=", "threshold": 0.50, "score_delta": -10.0}),
            revision=2,
        )
        session.add(lesson)
        session.commit()
        LessonRuleService.invalidate_cache()

        # Context for XAUUSDT NY CG01
        ctx = {
            "symbol": "XAUUSDT",
            "strategy_family": "CG01_SWEEP_MSS_FVG",
            "stage": "BEFORE_ARM",
            "execution_mode": "PAPER",
            "session": "NY",
            "spread": 0.20,
        }
        res = LessonRuleService.evaluate_lesson_score_adjustments(ctx, db=session, now_ms=now_ms)
        assert res["applied_delta"] == 0.0, "Out of scope rule must not apply delta"
        assert len(res["matched_rules"]) == 0
        session.close()
    finally:
        env.close()


# ==============================================================================
# GROUP 6: THRESHOLD BOUNDARY UNIT GATES (Rules 126, 127)
# ==============================================================================

def test_070_threshold_boundary_unit_gates():
    """
    Rules 126, 127:
    Verifies the exact inclusive threshold boundary in assess_candidate_quality:
    - final_score == 70.0 is ELIGIBLE_BY_SCORE
    - final_score == 69.9 is BELOW_THRESHOLD
    """
    from services.scalp_m1_model_service import ScalpCandidate
    from services.scalp_m1_types import build_adaptive_smc_paper_profile

    now_ms = 1700000000000
    ev_nodes = [
        {"id": "c1", "kind": "CONTEXT", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 20000, "extra": {"htf_bias": "BULLISH"}},
        {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 10000},
        {"id": "sw1", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 8000, "occurred_at_ms": now_ms - 8000},
        {"id": "ms1", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 5000, "occurred_at_ms": now_ms - 5000},
        {"id": "f1", "kind": "FVG", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 3000, "occurred_at_ms": now_ms - 3000},
        {"id": "tr1", "kind": "ENTRY_TRIGGER", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000, "occurred_at_ms": now_ms - 1000},
    ]

    cand = ScalpCandidate(
        is_eligible=True,
        candidate_key="cand_boundary_test",
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        direction="LONG",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        gross_rr=2.0,
        net_rr=1.8,
        as_of_ms=now_ms,
        evidence=ev_nodes,
    )

    quote = make_valid_quote(2649.85, 2650.15, now_ms)
    norm_ev = normalize_candidate_evidence(cand, as_of_ms=now_ms)
    ctx = build_candidate_scoring_context(
        cand,
        normalized_evidence=norm_ev,
        quote=quote,
        as_of_ms=now_ms,
        market_context={"context_source": "BOUND_SNAPSHOT", "htf_bias": "BULLISH", "session": "NY"},
    )
    profile = build_adaptive_smc_paper_profile()

    # Base score is evaluated
    as_eval = assess_candidate_quality(cand, context=ctx, profile=profile, as_of_ms=now_ms, threshold=70.0)
    base = as_eval.base_score

    # Delta chosen so final_score is exactly 70.0
    delta_70 = round(70.0 - base, 2)
    as_70 = assess_candidate_quality(cand, context=ctx, profile=profile, as_of_ms=now_ms, threshold=70.0, lesson_delta=delta_70)
    assert as_70.final_score == 70.0
    assert as_70.decision == "ELIGIBLE_BY_SCORE", "Score exactly equal to threshold must be ELIGIBLE_BY_SCORE"

    # Delta chosen so final_score is 69.9
    delta_69_9 = round(69.9 - base, 2)
    as_69_9 = assess_candidate_quality(cand, context=ctx, profile=profile, as_of_ms=now_ms, threshold=70.0, lesson_delta=delta_69_9)
    assert as_69_9.final_score == 69.9
    assert as_69_9.decision == "BELOW_THRESHOLD", "Score 69.9 must be BELOW_THRESHOLD"


# ==============================================================================
# GROUP 7: INVARIANTS - NO DOUBLE DEDUCTION & PENALTY CAP (Rules 089, 103)
# ==============================================================================

def test_080_no_double_deduction_and_penalty_cap_invariants():
    """
    Rules 089, 103:
    1. A single rule must NEVER be deducted twice in the same assessment (deduplication).
    2. Total penalty must be strictly clamped to cap_penalty (15.0).
    """
    env = make_isolated_paper_acceptance_env()
    now_ms = 1700000000000

    try:
        session = env.make_session()
        # Rule 1: -10.0 penalty
        l1 = Lesson(
            id=981,
            created_at=now_ms - 5000,
            related_trade_id="po_inv_1",
            strategy_family="CG01_SWEEP_MSS_FVG",
            title="Penalty 1",
            reflection="Ref 1",
            action_rule="Rule 1",
            status="APPROVED",
            is_approved=True,
            enabled=True,
            validation_status="VALID",
            effective_at=now_ms - 2000,
            severity="WARNING",
            effect="DEDUCT_SCORE",
            scope=json.dumps({"symbol": "XAUUSDT"}),
            predicate=json.dumps({"metric": "spread", "operator": "<=", "threshold": 0.50, "score_delta": -10.0}),
            revision=2,
        )
        # Rule 2: -10.0 penalty (Total would be -20.0, exceeding cap 15.0)
        l2 = Lesson(
            id=982,
            created_at=now_ms - 5000,
            related_trade_id="po_inv_2",
            strategy_family="CG01_SWEEP_MSS_FVG",
            title="Penalty 2",
            reflection="Ref 2",
            action_rule="Rule 2",
            status="APPROVED",
            is_approved=True,
            enabled=True,
            validation_status="VALID",
            effective_at=now_ms - 2000,
            severity="WARNING",
            effect="DEDUCT_SCORE",
            scope=json.dumps({"symbol": "XAUUSDT"}),
            predicate=json.dumps({"metric": "spread", "operator": "<=", "threshold": 0.50, "score_delta": -10.0}),
            revision=2,
        )
        session.add(l1)
        session.add(l2)
        session.commit()
        LessonRuleService.invalidate_cache()

        ctx = {"symbol": "XAUUSDT", "strategy_family": "CG01_SWEEP_MSS_FVG", "stage": "BEFORE_ARM", "execution_mode": "PAPER", "session": "ALL", "spread": 0.20}
        
        # Test cap_penalty = 15.0: raw is -20.0, applied must clamp to -15.0
        res = LessonRuleService.evaluate_lesson_score_adjustments(ctx, db=session, now_ms=now_ms, cap_penalty=15.0)
        assert res["raw_delta"] == -20.0
        assert res["applied_delta"] == -15.0, "Total penalty must be clamped to cap_penalty 15.0"
        assert res["is_capped"] is True

        # Test deduplication: pass duplicate rule objects
        rules_dup = [l1, l1, l1]
        res_dup = LessonRuleService.evaluate_lesson_score_adjustments(ctx, approved_rules=rules_dup, now_ms=now_ms)
        assert res_dup["raw_delta"] == -10.0, "Duplicate rule application must be prevented"
        assert res_dup["applied_delta"] == -10.0
        assert len(res_dup["matched_rules"]) == 1

        session.close()
    finally:
        env.close()
