import pytest
import json
import time
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base, PaperOrder, Lesson, PostTradeReviewRecord, LessonRepairTask
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState, SNAPSHOT_SCHEMA_VERSION
from services.smc_scenario_repository import save_immutable_scenario_snapshot
from services.post_trade_review_service import (
    build_entry_review_context,
    assess_entry_compliance_for_review,
    classify_post_trade_lesson,
    resolve_model_review_requirements,
    LossClassification,
)
from services.trade_lifecycle_service import TradeLifecycleService
from services.lesson_repair_worker import LessonRepairWorker, lesson_repair_worker
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator, M1ScalpConfig
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_m1_types import WatchedZone
from services.trading_policy_service import TradingPolicyService, resolve_entry_session_context
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    build_short_fixture_series,
    make_valid_quote,
)


@pytest.fixture
def isolated_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        # Create default policy
        policy = TradingPolicyService.get_or_create_policy(session)
        policy.max_daily_fills = 3
        policy.daily_min_goal = 2
        policy.ny_min_fills = 1
        policy.is_active = False
        session.commit()
        yield session
    finally:
        session.close()


# ==============================================================================
# 011. TÁI HIỆN SNAPSHOT DECODING (AttributeError: 'SMCScenarioSnapshot' object has no attribute 'evidence')
# ==============================================================================
def test_reproduce_011_bound_snapshot_decoding_orm_attribute_error(isolated_db):
    now_ms = 1700000000000
    snap = ScenarioSnapshot(
        scenario_id="scen_cg01_01",
        instance_id="inst_cg01_01",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="1M",
        context_timeframes=["15M", "1H"],
        family="CG01",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=now_ms,
        evidence_cutoff_ms=now_ms,
        evidence_ids=["poi_1", "mss_1"],
        liquidity_map={},
        trigger_chain={"verified": True, "model_id": "CG01", "node_ids": ["mss_1"]},
        target={"price": 2660.0},
        invalidation={"price": 2645.0},
        summary_vi="CG01 setup",
        config_version=1,
        detector_version="1.0",
        evidence=[
            {"id": "poi_1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "occurred_at_ms": now_ms - 20000, "known_at_ms": now_ms - 20000},
            {"id": "mss_1", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "occurred_at_ms": now_ms - 10000, "known_at_ms": now_ms - 10000},
        ],
    )
    saved_row = save_immutable_scenario_snapshot(isolated_db, snap)
    assert saved_row is not None
    assert saved_row.snapshot_id == snap.snapshot_id

    order = PaperOrder(
        id="ord_test_011",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        scenario_snapshot_id=saved_row.snapshot_id,
        strategy_family="CG01",
        armed_at=now_ms,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    ctx = build_entry_review_context(isolated_db, order)
    assert ctx["source_status"] == "BOUND_SCENARIO"
    assert "evidence" in ctx["scenario_evidence"] and len(ctx["scenario_evidence"]["evidence"]) == 2
    assert ctx["trigger_chain"] is not None
    assert ctx["trigger_chain"]["node_ids"] == ["mss_1"]


# ==============================================================================
# 012. TÁI HIỆN COMPLIANCE FALSE POSITIVE (Future, Wrong Symbol, Ghost Nodes)
# ==============================================================================
def test_reproduce_012_compliance_false_positive():
    now_ms = 1700000000000
    entry_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": "CONTINUATION",
        "evidence": [
            {
                "id": "bos_fake",
                "kind": "BOS",
                "symbol": "NASDAQ",
                "direction": "SHORT",
                "occurred_at_ms": now_ms - 10000,
                "known_at_ms": now_ms + 100000,
            }
        ],
        "trigger_chain": {
            "verified": True,
            "model_id": "CONTINUATION",
            "node_ids": ["ghost_node"],
        },
    }
    res = assess_entry_compliance_for_review(entry_snapshot=entry_snap, decision_as_of_ms=now_ms)
    assert res.status == "UNVERIFIABLE"
    assert any("TRIGGER_REF" in f or "TRIGGER_CHAIN" in f for f in res.checks_failed)


# ==============================================================================
# 013. TÁI HIỆN SESSION MẤT: evaluate_cycle() -> _arm_paper_candidate()
# ==============================================================================
def test_reproduce_013_session_propagation_evaluate_cycle_to_arm(isolated_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 20, 10, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)
    poi = WatchedZone(
        zone_id="poi_session_test",
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

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_NY_PAPER_EXPANDED",
        daily_max_cap=3,
        min_net_rr=1.5,
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])
    
    with patch.object(orchestrator, "_arm_paper_candidate", wraps=orchestrator._arm_paper_candidate) as spy_arm:
        orchestrator.evaluate_cycle(
            isolated_db,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_5m=[],
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
            auto_enabled=True,
        )

        assert spy_arm.called
        kwargs = spy_arm.call_args.kwargs
        session_ctx = kwargs.get("session_context")
        assert session_ctx is not None, "evaluate_cycle must pass resolved session_context to _arm_paper_candidate"
        assert getattr(session_ctx, "analysis_session", None) in ("NY", "NEW_YORK", "NY_AM", "NY_PM", "EXPANDED_NY")

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.direction == "LONG").first()
    assert order is not None
    assert order.cost_snapshot is not None
    cost_snap = json.loads(order.cost_snapshot)
    assert cost_snap.get("session") in ("NY", "NEW_YORK", "NY_AM", "NY_PM", "EXPANDED_NY")
    assert cost_snap.get("analysis_session") in ("NY", "NEW_YORK", "NY_AM", "NY_PM", "EXPANDED_NY")


# ==============================================================================
# 014. TÁI HIỆN REPAIR CHƯA CHẠY: lesson_repair_worker.process_once()
# ==============================================================================
def test_reproduce_014_runtime_worker_processes_pending_lesson(isolated_db):
    now_ms = 1700000000000
    order = PaperOrder(
        id="ord_pending_repair_014",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        actual_entry=2650.1,
        stop_loss=2645.0,
        take_profit=2665.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=now_ms - 120000,
        strategy_family="CG01",
        opened_at=now_ms - 60000,
        closed_at=now_ms,
        cost_snapshot=json.dumps({"planned_entry": 2650.0, "stop_loss": 2645.0, "take_profit": 2665.0, "session": "NEW_YORK"}),
    )
    isolated_db.add(order)
    task = LessonRepairTask(
        id="task_014",
        order_id="ord_pending_repair_014",
        status="PENDING",
        created_at=now_ms - 30000,
        updated_at=now_ms - 30000,
    )
    isolated_db.add(task)
    isolated_db.commit()

    worker = LessonRepairWorker()
    res = worker.process_once(db=isolated_db, now_ms=now_ms)
    assert res["repaired_count"] == 1
    assert res["failed_count"] == 0

    isolated_db.refresh(task)
    assert task.status == "COMPLETED"

    lesson = isolated_db.query(models.Lesson).filter(models.Lesson.related_trade_id == "ord_pending_repair_014").first()
    assert lesson is not None
    assert lesson.session == "NEW_YORK"
    assert lesson.is_approved is False

    # Check worker health status
    status = worker.get_status(db=isolated_db)
    assert status["total_repaired_count"] == 1
    assert status["pending_count"] == 0


# ==============================================================================
# 020. CORRUPT SCENARIO PAYLOAD HANDLING
# ==============================================================================
def test_corrupt_scenario_payload_handling(isolated_db):
    now_ms = 1700000000000
    scen_row = models.SMCScenarioSnapshot(
        snapshot_id="snap_corrupt_01",
        scenario_id="scen_corrupt_01",
        instance_id="inst_01",
        revision=1,
        symbol="XAUUSDT",
        timeframe="1M",
        direction="LONG",
        state="STRUCTURE_CONFIRMED",
        as_of_ms=now_ms,
        evidence_cutoff_ms=now_ms,
        evidence_digest="dummy",
        created_at=now_ms,
        payload="NOT_VALID_JSON{{{",
    )
    isolated_db.add(scen_row)

    order = PaperOrder(
        id="ord_corrupt_test",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        scenario_snapshot_id="snap_corrupt_01",
        strategy_family="CG01",
        armed_at=now_ms,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    ctx = build_entry_review_context(isolated_db, order)
    assert ctx["source_status"] == "CORRUPT_BOUND_SCENARIO"
    assert any("CORRUPT_BOUND_SCENARIO" in issue for issue in ctx["provenance_issues"])


# ==============================================================================
# 022 - 026. BINDING MISMATCHES (Symbol, Direction, Model)
# ==============================================================================
def test_binding_mismatches_rejected(isolated_db):
    now_ms = 1700000000000
    snap = ScenarioSnapshot(
        scenario_id="scen_mismatch",
        instance_id="inst_mismatch",
        revision=1,
        symbol="BTCUSDT",  # Mismatch with XAUUSDT
        setup_timeframe="1M",
        context_timeframes=["15M"],
        family="SMC_SWEEP_RECLAIM",  # Mismatch with CG01
        direction="SHORT",  # Mismatch with LONG
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=now_ms,
        evidence_cutoff_ms=now_ms,
        evidence_ids=["poi_1"],
        liquidity_map={},
        trigger_chain={"verified": True, "model_id": "SMC_SWEEP_RECLAIM", "node_ids": ["poi_1"]},
        target={"price": 2600.0},
        invalidation={"price": 2660.0},
        summary_vi="Mismatch setup",
        config_version=1,
        detector_version="1.0",
        evidence=[{"id": "poi_1", "kind": "POI", "symbol": "BTCUSDT", "direction": "SHORT", "known_at_ms": now_ms}],
    )
    saved_row = save_immutable_scenario_snapshot(isolated_db, snap)

    order = PaperOrder(
        id="ord_mismatch_test",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        scenario_snapshot_id=saved_row.snapshot_id,
        strategy_family="CG01",
        armed_at=now_ms,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    ctx = build_entry_review_context(isolated_db, order)
    issues = ctx["provenance_issues"]
    assert any("SYMBOL" in i for i in issues)
    assert any("DIRECTION" in i for i in issues)
    assert any("MODEL" in i for i in issues)


# ==============================================================================
# 029 - 030. FALLBACK STRATEGY EVIDENCE WHEN SCENARIO MISSING
# ==============================================================================
def test_fallback_strategy_evidence_when_scenario_missing(isolated_db):
    now_ms = 1700000000000
    strat_ev = models.StrategyEvidence(
        id="strat_ev_01",
        strategy_family="CG01",
        direction="LONG",
        sweep_evidence=json.dumps({"id": "node_poi", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 5000}),
        displacement_evidence=json.dumps({"id": "node_mss", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000}),
        created_at=now_ms,
    )
    isolated_db.add(strat_ev)

    order = PaperOrder(
        id="ord_strat_ev_test",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        scenario_snapshot_id=None,
        evidence_snapshot_id="strat_ev_01",
        strategy_family="CG01",
        armed_at=now_ms,
        created_at=now_ms,
    )
    isolated_db.add(order)
    isolated_db.commit()

    ctx = build_entry_review_context(isolated_db, order)
    assert ctx["source_status"] == "FALLBACK_STRATEGY_EVIDENCE"
    assert len(ctx["scenario_evidence"]["evidence"]) == 2
    assert ctx["trigger_chain"]["node_ids"] == ["node_mss"]


# ==============================================================================
# 043 - 060. CAUSAL COMPLIANCE TEMPORAL AND NODE FILTERS
# ==============================================================================
def test_causal_compliance_temporal_and_filters():
    now_ms = 1700000000000
    
    # Test 1: Future evidence rejected
    snap_future = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": "CONTINUATION",
        "evidence": [
            {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000},
            {"id": "t1", "kind": "BOS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms + 5000},  # Future!
        ],
        "trigger_chain": {"verified": True, "model_id": "CONTINUATION", "node_ids": ["t1"]},
    }
    res_future = assess_entry_compliance_for_review(entry_snapshot=snap_future, decision_as_of_ms=now_ms)
    assert res_future.status == "UNVERIFIABLE"
    assert any("FUTURE" in p for p in res_future.provenance_issues)

    # Test 2: Direction contradiction
    snap_dir = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": "CONTINUATION",
        "evidence": [
            {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000},
            {"id": "t1", "kind": "BOS", "symbol": "XAUUSDT", "direction": "SHORT", "known_at_ms": now_ms - 500},  # SHORT on LONG!
        ],
        "trigger_chain": {"verified": True, "model_id": "CONTINUATION", "node_ids": ["t1"]},
    }
    res_dir = assess_entry_compliance_for_review(entry_snapshot=snap_dir, decision_as_of_ms=now_ms)
    assert res_dir.status == "UNVERIFIABLE"
    assert any("DIRECTION" in p for p in res_dir.provenance_issues)

    # Test 3: Temporal sequence violation (SWEEP after RECLAIM)
    snap_seq = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": "SWEEP_RECLAIM",
        "evidence": [
            {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 5000},
            {"id": "rec1", "kind": "RECLAIM", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 3000, "occurred_at_ms": now_ms - 3000},
            {"id": "swp1", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000, "occurred_at_ms": now_ms - 1000},  # Sweep occurred AFTER reclaim!
        ],
        "trigger_chain": {"verified": True, "model_id": "SWEEP_RECLAIM", "node_ids": ["rec1"]},
    }
    res_seq = assess_entry_compliance_for_review(entry_snapshot=snap_seq, decision_as_of_ms=now_ms)
    assert res_seq.status == "UNVERIFIABLE"
    assert any("TEMPORAL_SEQUENCE" in f for f in res_seq.checks_failed)

    # Test 4: Empty trigger refs rejected
    snap_empty_refs = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": "CONTINUATION",
        "evidence": [
            {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000},
            {"id": "t1", "kind": "BOS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 500},
        ],
        "trigger_chain": {"verified": True, "model_id": "CONTINUATION", "node_ids": []},  # Empty refs!
    }
    res_empty = assess_entry_compliance_for_review(entry_snapshot=snap_empty_refs, decision_as_of_ms=now_ms)
    assert res_empty.status == "UNVERIFIABLE"
    assert any("EMPTY" in f for f in res_empty.checks_failed)


# ==============================================================================
# 053 - 065. MODEL REQUIREMENTS MATRIX (Parametrized across 5 models)
# ==============================================================================
@pytest.mark.parametrize(
    "model_id, required_kinds, valid_nodes, missing_kind",
    [
        (
            "CONTINUATION",
            {"BOS"},
            [
                {"id": "t1", "kind": "BOS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 2000},
            ],
            "BOS",
        ),
        (
            "FVG_CONTINUATION",
            {"FVG", "RETEST", "BOS"},
            [
                {"id": "f1", "kind": "FVG", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 1000},
                {"id": "r1", "kind": "RETEST", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 2000, "occurred_at_ms": 2000},
                {"id": "t1", "kind": "BOS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 3000},
            ],
            "FVG",
        ),
        (
            "REVERSAL",
            {"POI", "SWEEP", "CHOCH"},
            [
                {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 1000},
                {"id": "s1", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 2000},
                {"id": "b1", "kind": "CHOCH", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 3000},
            ],
            "SWEEP",
        ),
        (
            "SWEEP_RECLAIM",
            {"SWEEP", "RECLAIM"},
            [
                {"id": "s1", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 1000, "occurred_at_ms": 1000},
                {"id": "r1", "kind": "RECLAIM", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 2000, "occurred_at_ms": 2000},
            ],
            "RECLAIM",
        ),
        (
            "CG01",
            {"POI", "MSS", "RETEST"},
            [
                {"id": "p1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 1000},
                {"id": "m1", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 2000},
                {"id": "r1", "kind": "RETEST", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": 3000},
            ],
            "MSS",
        ),
    ],
)
def test_model_requirements_matrix_all_models(model_id, required_kinds, valid_nodes, missing_kind):
    now_ms = 10000
    last_node_id = valid_nodes[-1]["id"]

    # 1. Full compliant case
    snap_full = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": model_id,
        "evidence": valid_nodes,
        "trigger_chain": {"verified": True, "model_id": model_id, "node_ids": [last_node_id]},
    }
    res_full = assess_entry_compliance_for_review(entry_snapshot=snap_full, decision_as_of_ms=now_ms)
    assert res_full.status == "VERIFIED_COMPLIANT"

    # 2. Missing required kind case
    pruned_nodes = [n for n in valid_nodes if n["kind"] != missing_kind]
    snap_missing = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": model_id,
        "evidence": pruned_nodes,
        "trigger_chain": {"verified": True, "model_id": model_id, "node_ids": [pruned_nodes[-1]["id"]] if pruned_nodes else ["missing_ref"]},
    }
    res_missing = assess_entry_compliance_for_review(entry_snapshot=snap_missing, decision_as_of_ms=now_ms)
    assert res_missing.status == "UNVERIFIABLE"
    assert any(missing_kind in m for m in res_missing.missing_evidence)


# ==============================================================================
# 067. REMOVAL OF LEGACY SINGLE-NODE SHORTCUT
# ==============================================================================
def test_legacy_shortcut_removed():
    now_ms = 1700000000000
    # Setup claiming compliance_verified=True with only 1 BOS node for CG01
    snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": "CG01",
        "compliance_verified": True,  # Legacy flag
        "evidence": [
            {"id": "bos_1", "kind": "BOS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000},
        ],
        "trigger_chain": {"verified": True, "model_id": "CG01", "node_ids": ["bos_1"]},
    }
    res = assess_entry_compliance_for_review(entry_snapshot=snap, decision_as_of_ms=now_ms)
    # Must NOT bypass required kinds for CG01 (POI, SWEEP, MSS, RETEST)
    assert res.status == "UNVERIFIABLE"
    assert len(res.missing_evidence) > 0


# ==============================================================================
# 076. COLOR CLASSIFICATION MATRIX
# ==============================================================================
def test_color_classification_matrix():
    from services.post_trade_review_service import PostTradeReview
    now_ms = 1700000000000

    # 1. Valid compliant SL -> GREEN
    compliant_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": "CONTINUATION",
        "evidence": [
            {"id": "t1", "kind": "BOS", "symbol": "XAUUSDT", "direction": "LONG", "known_at_ms": now_ms - 1000},
        ],
        "trigger_chain": {"verified": True, "model_id": "CONTINUATION", "node_ids": ["t1"]},
    }
    c_res = assess_entry_compliance_for_review(entry_snapshot=compliant_snap, decision_as_of_ms=now_ms)
    assert c_res.status == "VERIFIED_COMPLIANT"

    rev_green = PostTradeReview(
        order_id="ord_color_01",
        symbol="XAUUSDT",
        model_id="CONTINUATION",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.1,
        planned_sl=2645.0,
        planned_tp=2660.0,
        exit_price=2645.0,
        exit_cause="SL_HIT",
        gross_pnl=-5.0,
        net_pnl=-5.0,
        realized_rr=-1.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.1,
        loss_classification=LossClassification.STRATEGY_VALID_LOSS.value,
    )
    l_res_green = classify_post_trade_lesson(
        rev_green,
        entry_snapshot=compliant_snap,
        compliance_result=c_res,
    )
    assert l_res_green.suggested_color == "GREEN"

    # 2. Win with missing evidence -> YELLOW
    unverifiable_snap = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "planned_entry": 2650.0,
        "planned_sl": 2645.0,
        "planned_tp": 2660.0,
        "model_id": "CONTINUATION",
        "evidence": [],
    }
    c_res_unverif = assess_entry_compliance_for_review(entry_snapshot=unverifiable_snap, decision_as_of_ms=now_ms)
    rev_yellow = PostTradeReview(
        order_id="ord_color_02",
        symbol="XAUUSDT",
        model_id="CONTINUATION",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.0,
        planned_sl=2645.0,
        planned_tp=2660.0,
        exit_price=2660.0,
        exit_cause="TP_HIT",
        gross_pnl=10.0,
        net_pnl=10.0,
        realized_rr=2.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification=LossClassification.NOT_A_LOSS.value,
    )
    l_res_yellow = classify_post_trade_lesson(
        rev_yellow,
        entry_snapshot=unverifiable_snap,
        compliance_result=c_res_unverif,
    )
    assert l_res_yellow.suggested_color == "YELLOW"

    # 3. Verified execution violation -> RED
    rev_red = PostTradeReview(
        order_id="ord_color_03",
        symbol="XAUUSDT",
        model_id="CONTINUATION",
        direction="LONG",
        planned_entry=2650.0,
        actual_entry=2650.0,
        planned_sl=2645.0,
        planned_tp=2660.0,
        exit_price=2645.0,
        exit_cause="SL_HIT",
        gross_pnl=-5.0,
        net_pnl=-5.0,
        realized_rr=-1.0,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification=LossClassification.POLICY_VIOLATION.value,
    )
    l_res_red = classify_post_trade_lesson(
        rev_red,
        entry_snapshot=compliant_snap,
        compliance_result=c_res,
    )
    assert l_res_red.suggested_color == "RED"


# ==============================================================================
# 088 / 097. SESSION SCOPED LESSON RULE MATCHING
# ==============================================================================
def test_session_scoped_rule_matching(isolated_db):
    from services.lesson_rule_service import LessonRuleService
    now_ms = 1700000000000

    # Create approved rule scoped specifically to NEW_YORK
    rule = models.Lesson(
        title="Penalize poor NY CG01 setup",
        reflection="Test reflection",
        action_rule="Test action rule",
        category="PROCESS",
        status="APPROVED",
        is_approved=True,
        enabled=True,
        validation_status="VALID",
        effect="DEDUCT_SCORE",
        predicate=json.dumps({"score_delta": -15.0}),
        scope=json.dumps({
            "symbol": "XAUUSDT",
            "strategy_family": "CG01",
            "session": "NEW_YORK",
            "stage": "BEFORE_ARM",
            "execution_mode": "PAPER",
        }),
        session="NEW_YORK",
        created_at=now_ms - 10000,
    )
    isolated_db.add(rule)
    isolated_db.commit()
    LessonRuleService.invalidate_cache()

    # Context in NEW_YORK session
    ny_ctx = {
        "symbol": "XAUUSDT",
        "strategy_family": "CG01",
        "session": "NEW_YORK",
        "execution_mode": "PAPER",
        "stage": "BEFORE_ARM",
    }
    res_ny = LessonRuleService.evaluate_lesson_score_adjustments(ny_ctx, db=isolated_db, now_ms=now_ms)
    assert res_ny["applied_delta"] == -15.0

    # Context in LONDON session
    lon_ctx = {
        "symbol": "XAUUSDT",
        "strategy_family": "CG01",
        "session": "LONDON",
        "execution_mode": "PAPER",
        "stage": "BEFORE_ARM",
    }
    res_lon = LessonRuleService.evaluate_lesson_score_adjustments(lon_ctx, db=isolated_db, now_ms=now_ms)
    assert res_lon["applied_delta"] == 0.0


# ==============================================================================
# 085 - 087 / 116. REPAIR SESSION RECOVERY & ACCOUNTING INTEGRITY
# ==============================================================================
def test_repair_session_recovery_and_accounting_integrity(isolated_db):
    now_ms = 1700000000000
    order = PaperOrder(
        id="ord_session_rec_085",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        actual_entry=2650.1,
        stop_loss=2645.0,
        take_profit=2665.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        realized_pnl_net=14.8,
        actual_exit=2665.0,
        created_at=now_ms - 120000,
        opened_at=now_ms - 60000,
        closed_at=now_ms,
        strategy_family="CG01",
        cost_snapshot=json.dumps({"planned_entry": 2650.0, "stop_loss": 2645.0, "take_profit": 2665.0, "session": "LONDON"}),
    )
    isolated_db.add(order)
    task = LessonRepairTask(
        id="task_085",
        order_id="ord_session_rec_085",
        status="PENDING",
        created_at=now_ms,
        updated_at=now_ms,
    )
    isolated_db.add(task)
    isolated_db.commit()

    # Capture accounting facts before repair
    before_pnl = order.realized_pnl_net
    before_entry = order.actual_entry
    before_exit = order.actual_exit

    # Run repair worker batch
    worker = LessonRepairWorker()
    worker.process_once(db=isolated_db, now_ms=now_ms + 1000)

    isolated_db.refresh(order)
    assert order.realized_pnl_net == before_pnl
    assert order.actual_entry == before_entry
    assert order.actual_exit == before_exit

    lesson = isolated_db.query(models.Lesson).filter(models.Lesson.related_trade_id == "ord_session_rec_085").first()
    assert lesson is not None
    # Session must be restored to LONDON, NOT 'REPAIR'
    assert lesson.session == "LONDON"


# ==============================================================================
# 105 / 173. WORKER LIFESPAN AND SHUTDOWN
# ==============================================================================
@pytest.mark.anyio
async def test_worker_lifespan_and_shutdown():
    worker = LessonRepairWorker()
    task = asyncio.create_task(worker.run_repair_loop(poll_interval_s=0.05))
    await asyncio.sleep(0.1)
    assert worker.is_running is True

    # Shutdown gracefully
    worker.stop()
    res = await worker.shutdown(timeout_s=2.0)
    assert res.status in ("COMPLETE", "NO_ACTIVE_WORKER")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert worker.is_running is False


# ==============================================================================
# 142 / 182. FULL LONG CANDLE PIPELINE TO BOUND LESSON
# ==============================================================================
def test_full_long_candle_pipeline_to_lesson(isolated_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 20, 9, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    quote = make_valid_quote(2625.5, 2625.7, now_ms)
    poi = WatchedZone(
        zone_id="poi_pipe_long",
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

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_NY_PAPER_EXPANDED",
        daily_max_cap=3,
        min_net_rr=1.5,
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])
    orchestrator.evaluate_cycle(
        isolated_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.direction == "LONG").first()
    assert order is not None
    assert order.state == "armed"
    assert order.scenario_snapshot_id is not None

    # Fill order
    coordinator = ExecutionCoordinator()
    fill_tick = {"bid": order.planned_entry - 0.10, "ask": order.planned_entry, "last": order.planned_entry, "timestamp": now_ms + 1000}
    coordinator.evaluate_orders_sync(isolated_db, ticker_override=fill_tick, now_ms=now_ms + 1000)
    isolated_db.refresh(order)
    assert order.state == "paper_open"

    # Close order via TP_HIT
    closed_order = TradeLifecycleService.execute_close(
        isolated_db,
        order_id=order.id,
        exit_price=order.take_profit,
        exit_cause="TP_HIT",
        now_ms=now_ms + 10000,
    )
    isolated_db.commit()

    # Verify review and lesson
    review = isolated_db.query(models.PostTradeReviewRecord).filter(models.PostTradeReviewRecord.order_id == order.id).first()
    assert review is not None
    lesson = isolated_db.query(models.Lesson).filter(models.Lesson.related_trade_id == order.id).first()
    assert lesson is not None
    assert lesson.is_approved is False

    # Check decoded review context from real bound snapshot
    ctx = build_entry_review_context(isolated_db, closed_order)
    assert ctx["source_status"] == "BOUND_SCENARIO"
    assert "evidence" in ctx["scenario_evidence"]
    assert len(ctx["scenario_evidence"]["evidence"]) > 0


# ==============================================================================
# 143 / 183. FULL SHORT CANDLE PIPELINE TO BOUND LESSON
# ==============================================================================
def test_full_short_candle_pipeline_to_lesson(isolated_db):
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 20, 9, 0, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 600_000

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    quote = make_valid_quote(2629.3, 2629.5, now_ms)
    poi = WatchedZone(
        zone_id="poi_pipe_short",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="FVG",
        direction="BEARISH",
        top=2635.0,
        bottom=2630.0,
        created_at=t_start - 3600_000,
        known_at=t_start - 1800_000,
        touch_count=0,
    )

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
        timing_profile="CG01_NY_PAPER_EXPANDED",
        daily_max_cap=3,
        min_net_rr=1.5,
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.set_active_zones("XAUUSDT", [poi])
    orchestrator.evaluate_cycle(
        isolated_db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        quote=quote,
        candles_1m=c_1m,
        candles_5m=[],
        candles_15m=c_15m,
        candles_1h=c_1h,
        candles_4h=c_4h,
        config=cfg,
        auto_enabled=True,
    )

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.direction == "SHORT").first()
    assert order is not None
    assert order.state == "armed"
    # Geometry: TP < planned_entry < SL
    assert order.take_profit < order.planned_entry < order.stop_loss

    coordinator = ExecutionCoordinator()
    fill_tick = {"bid": order.planned_entry, "ask": order.planned_entry + 0.10, "last": order.planned_entry, "timestamp": now_ms + 1000}
    coordinator.evaluate_orders_sync(isolated_db, ticker_override=fill_tick, now_ms=now_ms + 1000)
    isolated_db.refresh(order)
    assert order.state == "paper_open"

    closed_order = TradeLifecycleService.execute_close(
        isolated_db,
        order_id=order.id,
        exit_price=order.take_profit,
        exit_cause="TP_HIT",
        now_ms=now_ms + 10000,
    )
    isolated_db.commit()

    review = isolated_db.query(models.PostTradeReviewRecord).filter(models.PostTradeReviewRecord.order_id == order.id).first()
    assert review is not None
    lesson = isolated_db.query(models.Lesson).filter(models.Lesson.related_trade_id == order.id).first()
    assert lesson is not None

    ctx = build_entry_review_context(isolated_db, closed_order)
    assert ctx["source_status"] == "BOUND_SCENARIO"
    assert ctx["direction"] == "SHORT"
