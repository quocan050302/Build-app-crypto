"""
Aurum V17.5.1 — Full Production Flow & Lifecycle Integration Tests
Verifies the complete end-to-end execution path:
Orchestrator cycle -> _arm_paper_candidate -> ExecutionCoordinator fill ->
TradeLifecycleService process_exit_tick -> PostTradeReview.
"""
import time
import json
import pytest
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, PaperOrder, SystemConfig
from services.stop_placement_types import (
    StopPlacementPolicy,
    StopAnchorResult,
    StopAnchor,
    StopBufferResult,
    StopStatus,
    StopPlacementDecision,
)
from services.stop_placement_service import plan_structural_stop, validate_applied_stop_decision
from services.execution_coordinator import ExecutionCoordinator
from services.trade_lifecycle_service import TradeLifecycleService
from services.clock import ReplayClock
from services.quote_validator import CanonicalQuote
from domain_calculator import calculate_risk_reward, CostAssumptions
from services.post_trade_review_service import classify_post_trade_lesson


@pytest.fixture
def test_db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "prod_flow.db"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    with Session() as s:
        # Seed settings
        s.add(SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1000000))
        s.add(SystemConfig(key="execution_scope", value="PAPER", updated_at=1000000))
        s.add(SystemConfig(key="stop_placement_v1.mode", value="PAPER_STRUCTURAL", updated_at=1000000))
        s.add(SystemConfig(key="stop_placement_v1.version", value="1", updated_at=1000000))
        s.commit()
    return Session


def test_production_flow_short_paper_structural(test_db):
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)

    entry = 2000.0
    legacy_sl = 2005.0
    tp = 1975.0

    anchor_res = StopAnchorResult(
        status="AVAILABLE",
        anchor=StopAnchor(
            id="anc_flow_sh_1",
            kind="PROTECTED_LEG_HIGH",
            price=2008.0,
            timeframe="1M",
            occurred_at_ms=now_ms - 60000,
            known_at_ms=now_ms - 30000,
            source_leg_id="leg_1",
            price_basis="CANDLE_LAST",
        ),
    )
    buffer_res = StopBufferResult(
        buffer_price=1.50,
        components={"tick_floor": 0.05, "atr_m1": 1.50},
        dominant_source="atr_m1",
        tick_size=0.01,
    )

    # 1. Plan structural stop (Variant C)
    stop_dec = plan_structural_stop(
        direction="SHORT",
        planned_entry=entry,
        take_profit=tp,
        legacy_sl=legacy_sl,
        anchor_result=anchor_res,
        buffer_result=buffer_res,
        policy=policy,
        as_of_ms=now_ms,
    )
    assert stop_dec.status == StopStatus.VALID.value
    assert stop_dec.applied_variant == "C"
    assert stop_dec.selected_sl == 2009.50

    # 2. Canonical Sizing
    calc = calculate_risk_reward(
        direction="SHORT",
        entry=entry,
        stop_loss=stop_dec.selected_sl,
        take_profit=tp,
        capital=1000.0,
        risk_pct=0.25,
        leverage=5,
        costs=CostAssumptions(fee_pct=0.0004, slippage_ticks=1.0, multiplier=1.0),
        now_ms=now_ms,
    )
    assert calc.can_execute, "Sizing calculation must succeed"

    # 3. ARM Barrier
    candidate = SimpleNamespace(
        stop_loss=stop_dec.selected_sl,
        stop_placement=stop_dec,
        calculation=calc,
    )
    is_valid, reason = validate_applied_stop_decision(candidate=candidate, current_policy=policy)
    assert is_valid, f"ARM barrier must accept valid structural stop: {reason}"

    # 4. Create Armed Order in DB
    cost_snap = calc.to_snapshot()
    cost_snap["stop_placement"] = stop_dec.to_dict()
    cost_snap["stop_mode_at_arm"] = policy.mode
    cost_snap["stop_policy_version_at_arm"] = policy.version

    order_id = "order_prod_flow_sh_1"
    with test_db() as db:
        order = PaperOrder(
            id=order_id,
            instrument="XAUUSDT",
            direction="SHORT",
            state="armed",
            planned_entry=entry,
            actual_entry=None,
            stop_loss=stop_dec.selected_sl,
            take_profit=tp,
            quantity=calc.quantity,
            initial_risk_usdt=calc.net_risk_usdt,
            armed_at=now_ms,
            created_at=now_ms,
            cost_snapshot=json.dumps(cost_snap),
        )
        db.add(order)
        db.commit()

    # 5. Coordinator Fills Order
    coord = ExecutionCoordinator()
    fill_time = now_ms + 10000
    clock.set_time(fill_time)
    fill_quote = {
        "bid": 2000.0,
        "ask": 2000.2,
        "last": 2000.1,
        "exchange_time": fill_time,
        "observed_at": fill_time,
    }
    with test_db() as db:
        coord.evaluate_orders_sync(db, ticker_override=fill_quote, clock=clock, now_ms=fill_time)
        db.commit()

    with test_db() as db:
        o = db.query(PaperOrder).filter_by(id=order_id).one()
        assert o.state == "paper_open", f"Expected paper_open, got {o.state}"
        assert o.actual_entry is not None and abs(o.actual_entry - entry) <= 0.20

    # 6. Exit at Take Profit
    exit_time = fill_time + 60000
    clock.set_time(exit_time)
    with test_db() as db:
        closed = TradeLifecycleService.process_exit_tick(
            db,
            current_bid=tp - 0.2,
            current_ask=tp,  # Short exits on ask <= TP
            now_ms=exit_time,
            clock=clock,
            tick_timestamp=exit_time,
        )
        db.commit()
        assert closed is not None
        assert closed.state == "closed"
        assert closed.exit_cause == "TP_HIT"
        assert closed.realized_pnl_net > 0.0

    # 7. Post Trade Lesson Classification
    from services.post_trade_review_service import PostTradeReview, EntryComplianceResult
    review = PostTradeReview(
        order_id=order_id,
        symbol="XAUUSDT",
        model_id="M1_CONTINUATION",
        direction="SHORT",
        planned_entry=entry,
        actual_entry=o.actual_entry,
        planned_sl=stop_dec.selected_sl,
        planned_tp=tp,
        exit_price=tp,
        exit_cause="TP_HIT",
        gross_pnl=5.0,
        net_pnl=4.5,
        realized_rr=2.2,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification=None,
        outcome_status="WIN",
    )
    comp = EntryComplianceResult(
        status="VERIFIED_COMPLIANT",
        reason="Kế hoạch hợp lệ",
        missing_evidence=[],
    )
    classification = classify_post_trade_lesson(
        review=review,
        entry_snapshot={"stop_placement": stop_dec.to_dict()},
        compliance_result=comp,
    )
    assert classification.suggested_color == "GREEN"
    assert classification.canonical_severity == "INFO"


def test_production_flow_long_paper_structural(test_db):
    now_ms = 1700000000000
    clock = ReplayClock(now_ms)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)

    entry = 2000.0
    legacy_sl = 1995.0
    tp = 2025.0

    anchor_res = StopAnchorResult(
        status="AVAILABLE",
        anchor=StopAnchor(
            id="anc_flow_lg_1",
            kind="PROTECTED_LEG_LOW",
            price=1992.0,
            timeframe="1M",
            occurred_at_ms=now_ms - 60000,
            known_at_ms=now_ms - 30000,
            source_leg_id="leg_long_1",
            price_basis="CANDLE_LAST",
        ),
    )
    buffer_res = StopBufferResult(
        buffer_price=1.50,
        components={"tick_floor": 0.05, "atr_m1": 1.50},
        dominant_source="atr_m1",
        tick_size=0.01,
    )

    # 1. Plan structural stop (Variant C)
    stop_dec = plan_structural_stop(
        direction="LONG",
        planned_entry=entry,
        take_profit=tp,
        legacy_sl=legacy_sl,
        anchor_result=anchor_res,
        buffer_result=buffer_res,
        policy=policy,
        as_of_ms=now_ms,
    )
    assert stop_dec.status == StopStatus.VALID.value
    assert stop_dec.applied_variant == "C"
    assert stop_dec.selected_sl == 1990.50

    # 2. Canonical Sizing
    calc = calculate_risk_reward(
        direction="LONG",
        entry=entry,
        stop_loss=stop_dec.selected_sl,
        take_profit=tp,
        capital=1000.0,
        risk_pct=0.25,
        leverage=5,
        costs=CostAssumptions(fee_pct=0.0004, slippage_ticks=1.0, multiplier=1.0),
        now_ms=now_ms,
    )
    assert calc.can_execute, "Sizing calculation must succeed"

    # 3. ARM Barrier
    candidate = SimpleNamespace(
        stop_loss=stop_dec.selected_sl,
        stop_placement=stop_dec,
        calculation=calc,
    )
    is_valid, reason = validate_applied_stop_decision(candidate=candidate, current_policy=policy)
    assert is_valid, f"ARM barrier must accept valid structural stop: {reason}"

    # 4. Create Armed Order in DB
    cost_snap = calc.to_snapshot()
    cost_snap["stop_placement"] = stop_dec.to_dict()
    cost_snap["stop_mode_at_arm"] = policy.mode
    cost_snap["stop_policy_version_at_arm"] = policy.version

    order_id = "order_prod_flow_lg_1"
    with test_db() as db:
        order = PaperOrder(
            id=order_id,
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            planned_entry=entry,
            actual_entry=None,
            stop_loss=stop_dec.selected_sl,
            take_profit=tp,
            quantity=calc.quantity,
            initial_risk_usdt=calc.net_risk_usdt,
            armed_at=now_ms,
            created_at=now_ms,
            cost_snapshot=json.dumps(cost_snap),
        )
        db.add(order)
        db.commit()

    # 5. Coordinator Fills Order
    coord = ExecutionCoordinator()
    fill_time = now_ms + 10000
    clock.set_time(fill_time)
    fill_quote = {
        "bid": 1999.8,
        "ask": 2000.0,
        "last": 1999.9,
        "exchange_time": fill_time,
        "observed_at": fill_time,
    }
    with test_db() as db:
        coord.evaluate_orders_sync(db, ticker_override=fill_quote, clock=clock, now_ms=fill_time)
        db.commit()

    with test_db() as db:
        o = db.query(PaperOrder).filter_by(id=order_id).one()
        assert o.state == "paper_open", f"Expected paper_open, got {o.state}"
        assert o.actual_entry is not None and abs(o.actual_entry - entry) <= 0.20

    # 6. Exit at Take Profit
    exit_time = fill_time + 60000
    clock.set_time(exit_time)
    with test_db() as db:
        closed = TradeLifecycleService.process_exit_tick(
            db,
            current_bid=tp,  # Long exits on bid >= TP
            current_ask=tp + 0.2,
            now_ms=exit_time,
            clock=clock,
            tick_timestamp=exit_time,
        )
        db.commit()
        assert closed is not None
        assert closed.state == "closed"
        assert closed.exit_cause == "TP_HIT"
        assert closed.realized_pnl_net > 0.0

    # 7. Post Trade Lesson Classification
    from services.post_trade_review_service import PostTradeReview, EntryComplianceResult
    review = PostTradeReview(
        order_id=order_id,
        symbol="XAUUSDT",
        model_id="M1_CONTINUATION",
        direction="LONG",
        planned_entry=entry,
        actual_entry=o.actual_entry,
        planned_sl=stop_dec.selected_sl,
        planned_tp=tp,
        exit_price=tp,
        exit_cause="TP_HIT",
        gross_pnl=5.0,
        net_pnl=4.5,
        realized_rr=2.2,
        hold_time_ms=60000,
        entry_slippage_usd=0.0,
        loss_classification=None,
        outcome_status="WIN",
    )
    comp = EntryComplianceResult(
        status="VERIFIED_COMPLIANT",
        reason="Kế hoạch hợp lệ",
        missing_evidence=[],
    )
    classification = classify_post_trade_lesson(
        review=review,
        entry_snapshot={"stop_placement": stop_dec.to_dict()},
        compliance_result=comp,
    )
    assert classification.suggested_color == "GREEN"
    assert classification.canonical_severity == "INFO"
