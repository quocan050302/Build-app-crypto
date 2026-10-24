import pytest
import time
import json
from database import engine, SessionLocal
from models import Base, PaperOrder, SystemConfig
from services.stop_placement_types import (
    StopPlacementMode,
    StopPlacementPolicy,
    StopAnchorKind,
    StopAnchor,
    StopMetrics,
    StopPlacementDecision,
    StopStatus,
    StopBufferResult,
    StopAnchorResult,
)
from services.stop_placement_service import plan_structural_stop
from domain_calculator import calculate_risk_reward, CostAssumptions
from services.trade_lifecycle_service import TradeLifecycleService


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.query(PaperOrder).filter(PaperOrder.instrument.in_(["XAUUSDT_FLOW_SHORT", "XAUUSDT_FLOW_LONG"])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(PaperOrder).filter(PaperOrder.instrument.in_(["XAUUSDT_FLOW_SHORT", "XAUUSDT_FLOW_LONG"])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_production_flow_short_paper_structural():
    """
    End-to-End Production Flow for SHORT under PAPER_STRUCTURAL mode:
    1. Plan structural stop (Variant C).
    2. Sizing calculation with frozen baseline TP.
    3. Invariant check: cand.stop_loss == calc.stop_loss.
    4. PaperOrder creation with arm cost snapshot preserving stop_placement and stop_mode_at_arm.
    5. Fill execution.
    6. Exit execution with exit_evidence.
    7. Verify order snapshot contains complete provenance.
    """
    db = SessionLocal()
    now_ms = int(time.time() * 1000)
    try:
        policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)
        entry = 4000.0
        legacy_sl = 4005.0
        baseline_tp = 3970.0

        anchor_res = StopAnchorResult(
            status="AVAILABLE",
            anchor=StopAnchor(
                id="anc_flow_sh_1",
                kind=StopAnchorKind.PROTECTED_LEG_HIGH.value,
                price=4008.0,
                timeframe="1M",
                occurred_at_ms=now_ms - 60000,
                known_at_ms=now_ms - 30000,
                source_leg_id="leg_1",
                source_break_id="break_1",
                price_basis="CANDLE_LAST",
            )
        )
        buffer_res = StopBufferResult(
            buffer_price=1.50,
            components={"tick_floor": 0.05, "atr_m1": 1.50},
            dominant_source="atr_m1",
            tick_size=0.01,
        )

        # 1. Plan structural stop
        stop_dec = plan_structural_stop(
            direction="SHORT",
            planned_entry=entry,
            take_profit=baseline_tp,
            legacy_sl=legacy_sl,
            anchor_result=anchor_res,
            buffer_result=buffer_res,
            policy=policy,
            as_of_ms=now_ms,
        )
        assert stop_dec.applied_variant == "C"
        assert stop_dec.selected_sl == 4009.50

        # 2. Canonical Sizing
        calc = calculate_risk_reward(
            direction="SHORT",
            entry=entry,
            stop_loss=stop_dec.selected_sl,
            take_profit=baseline_tp,
            capital=1000.0,
            risk_pct=0.25,
            min_net_rr=1.2,
            leverage=5,
            costs=CostAssumptions(),
        )
        assert calc.can_execute is True
        assert abs(calc.stop_loss - stop_dec.selected_sl) < 1e-4

        # 3. Arm PaperOrder
        cost_snapshot = calc.to_snapshot()
        cost_snapshot["stop_placement"] = stop_dec.to_dict()
        cost_snapshot["stop_mode_at_arm"] = policy.mode
        cost_snapshot["stop_policy_version"] = policy.version

        order = PaperOrder(
            id="order_flow_short_1",
            instrument="XAUUSDT_FLOW_SHORT",
            direction="SHORT",
            order_type="MARKET",
            quantity=calc.quantity,
            planned_entry=entry,
            actual_entry=entry,
            stop_loss=stop_dec.selected_sl,
            take_profit=baseline_tp,
            initial_risk_usdt=calc.net_risk_usdt,
            state="paper_open",
            created_at=now_ms,
            opened_at=now_ms,
            cost_snapshot=json.dumps(cost_snapshot),
        )
        db.add(order)
        db.commit()

        # 4. Exit execution with exit_evidence
        exit_ev = {
            "price_basis": "QUOTE_ASK",
            "trigger_level": stop_dec.selected_sl,
            "applied_exit_price": stop_dec.selected_sl + 0.10,
            "quote_timestamp_ms": now_ms + 10000,
            "evidence_reference": "TICK_QUOTE",
        }
        closed_order = TradeLifecycleService.execute_close(
            db=db,
            order_id="order_flow_short_1",
            exit_price=stop_dec.selected_sl + 0.10,
            exit_cause="SL_HIT",
            exit_evidence=exit_ev,
        )

        assert closed_order is not None
        assert closed_order.state == "closed"
        saved_snap = json.loads(closed_order.cost_snapshot)
        assert saved_snap["stop_mode_at_arm"] == "PAPER_STRUCTURAL"
        assert saved_snap["stop_placement"]["applied_variant"] == "C"
        assert saved_snap["exit_evidence"]["price_basis"] == "QUOTE_ASK"
    finally:
        db.close()


def test_production_flow_long_paper_structural():
    """
    End-to-End Production Flow for LONG under PAPER_STRUCTURAL mode.
    """
    db = SessionLocal()
    now_ms = int(time.time() * 1000)
    try:
        policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)
        entry = 4000.0
        legacy_sl = 3995.0
        baseline_tp = 4020.0

        anchor_res = StopAnchorResult(
            status="AVAILABLE",
            anchor=StopAnchor(
                id="anc_flow_lg_1",
                kind=StopAnchorKind.PROTECTED_LEG_LOW.value,
                price=3992.0,
                timeframe="1M",
                occurred_at_ms=now_ms - 60000,
                known_at_ms=now_ms - 30000,
                source_leg_id="leg_long_1",
                source_break_id="break_long_1",
                price_basis="CANDLE_LAST",
            )
        )
        buffer_res = StopBufferResult(
            buffer_price=1.20,
            components={"tick_floor": 0.05, "atr_m1": 1.20},
            dominant_source="atr_m1",
            tick_size=0.01,
        )

        stop_dec = plan_structural_stop(
            direction="LONG",
            planned_entry=entry,
            take_profit=baseline_tp,
            legacy_sl=legacy_sl,
            anchor_result=anchor_res,
            buffer_result=buffer_res,
            policy=policy,
            as_of_ms=now_ms,
        )
        assert stop_dec.applied_variant == "C"
        # anchor 3992.0 - buffer 1.20 = 3990.80
        assert stop_dec.selected_sl == 3990.80

        calc = calculate_risk_reward(
            direction="LONG",
            entry=entry,
            stop_loss=stop_dec.selected_sl,
            take_profit=baseline_tp,
            capital=1000.0,
            risk_pct=0.25,
            min_net_rr=1.2,
            leverage=5,
            costs=CostAssumptions(),
        )
        assert calc.can_execute is True

        cost_snapshot = calc.to_snapshot()
        cost_snapshot["stop_placement"] = stop_dec.to_dict()
        cost_snapshot["stop_mode_at_arm"] = policy.mode

        order = PaperOrder(
            id="order_flow_long_1",
            instrument="XAUUSDT_FLOW_LONG",
            direction="LONG",
            order_type="MARKET",
            quantity=calc.quantity,
            planned_entry=entry,
            actual_entry=entry,
            stop_loss=stop_dec.selected_sl,
            take_profit=baseline_tp,
            initial_risk_usdt=calc.net_risk_usdt,
            state="paper_open",
            created_at=now_ms,
            opened_at=now_ms,
            cost_snapshot=json.dumps(cost_snapshot),
        )
        db.add(order)
        db.commit()

        exit_ev = {
            "price_basis": "QUOTE_BID",
            "trigger_level": stop_dec.selected_sl,
            "applied_exit_price": stop_dec.selected_sl,
            "quote_timestamp_ms": now_ms + 10000,
            "evidence_reference": "TICK_QUOTE",
        }
        closed_order = TradeLifecycleService.execute_close(
            db=db,
            order_id="order_flow_long_1",
            exit_price=stop_dec.selected_sl,
            exit_cause="SL_HIT",
            exit_evidence=exit_ev,
        )

        assert closed_order is not None
        assert closed_order.state == "closed"
        saved_snap = json.loads(closed_order.cost_snapshot)
        assert saved_snap["stop_placement"]["applied_variant"] == "C"
        assert saved_snap["exit_evidence"]["price_basis"] == "QUOTE_BID"
    finally:
        db.close()
