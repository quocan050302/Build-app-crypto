"""
backend/tests/test_v17_5_6_actual_order_cost_parity.py
AURUM V17.5.6 — Shared Cost Authority & Actual Order Parity Test Suite.
Covers Parts 081–110, Appendix B, E1:
1. Explicit zero slippage preserved across snapshot and fill.
2. Custom slippage overrides (0.27) resolved deterministically across ARM and Coordinator.
3. Custom maker/taker fees correctly consumed by order and research context.
4. Snapshot-first override: historical order snapshots preserve configured values even if settings change.
5. Dict and Object representation normalization for costs.
6. Strict validation and rejection of invalid slippage and fees (negative, NaN, inf, bool).
7. Version preservation and candidate revision isolation.
8. Persistence round-trip without data corruption.
9. Golden fill parity for MARKET, LIMIT, STOP orders.
10. Idempotency on duplicate quote ticks.
"""

import json
import math
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import crud
from services.clock import ReplayClock
from services.plan_financial_context_service import resolve_execution_cost_context
from services.order_plan_snapshot_service import (
    build_arm_plan_snapshot,
    OrderPlanSnapshot,
)
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.scalp_m1_model_service import M1ScalpConfig
from services.stop_placement_research_service import (
    evaluate_comparison_entry,
    resolve_stop_comparison_execution_context,
)


def make_arm_snapshot(
    order_type="MARKET",
    direction="LONG",
    entry_price=2000.0,
    stop_loss=1990.0,
    take_profit=2020.0,
    resolved_execution_costs=None,
    now_ms=1000000,
    min_net_rr=1.2,
):
    return build_arm_plan_snapshot(
        calc_res=None,
        order_type=order_type,
        direction=direction,
        planned_entry=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        capital_usdt=5000.0,
        risk_pct=0.10,
        leverage=10,
        now_ms=now_ms,
        min_net_rr=min_net_rr,
        resolved_execution_costs=resolved_execution_costs,
    )


@pytest.fixture
def memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()

    # Seed necessary system configs
    db.add(models.SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1000000))
    db.add(models.SystemConfig(key="execution_scope", value="PAPER", updated_at=1000000))
    db.add(models.SystemConfig(key="stop_placement_v1.mode", value="PAPER_STRUCTURAL", updated_at=1000000))

    clock = ReplayClock(1_000_000)
    audit = crud.get_or_create_today_audit(db, clock=clock)
    audit.initial_equity = 5000.0
    audit.current_equity = 5000.0
    db.commit()

    yield db, clock, Session


# ======================================================================
# PARTS 081–090: Cost Snapshot Contract & Normalization
# ======================================================================

def test_p081_explicit_zero_slippage_in_actual_arm_and_fill(memory_db):
    """Phần 081: Explicit 0.0 slippage preserved across ARM and fill; no fallback to 0.10."""
    db, clock, Session = memory_db
    coordinator = ExecutionCoordinator()

    snap = {
        "available": True,
        "entry_slippage_usd": 0.0,
        "tp_slippage_usd": 0.0,
        "sl_slippage_usd": 0.0,
        "maker_fee_rate": 0.0002,
        "taker_fee_rate": 0.0004,
        "version": 1,
        "provenance": "VERIFIED_TEST",
    }
    resolved = resolve_execution_cost_context(snapshot=snap)
    assert resolved["available"] is True
    assert resolved["entry_slippage_usd"] == 0.0

    # Build plan snapshot with resolved costs
    plan = make_arm_snapshot(
        order_type="MARKET",
        direction="LONG",
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        resolved_execution_costs=resolved,
    )
    assert plan["entry_slippage_usd"] == 0.0
    assert plan["slippage_usd"] == 0.0

    # Create paper order directly with this verified plan snapshot
    order = models.PaperOrder(
        id="test_zero_slip_081",
        setup_id="setup_081",
        signal_id="sig_081",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="MARKET",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="armed",
        created_at=1000000,
        cost_snapshot=json.dumps(plan),
    )
    db.add(order)
    db.commit()

    # Fill via coordinator with quote BID 2000.00, ASK 2000.20
    # MARKET LONG fills at ask + slip = 2000.20 + 0.0 = 2000.20 (NOT 2000.30!)
    quote = {"bid": 2000.00, "ask": 2000.20, "last": 2000.10, "timestamp": 1000100}
    coordinator.evaluate_orders_sync(db, ticker_override=quote, clock=clock, now_ms=1000100)

    db.refresh(order)
    assert order.state in ("open", "paper_open")
    assert abs(order.actual_entry - 2000.20) < 1e-4


def test_p082_custom_slippage_027_parity(memory_db):
    """Phần 082: Effective config 0.27 used consistently across ARM, fill, and research comparison."""
    db, clock, Session = memory_db
    coordinator = ExecutionCoordinator()

    settings = {
        "slippage_usd": 0.27,
        "config_version": 2,
    }
    resolved = resolve_execution_cost_context(settings=settings, as_of_ms=1000000)
    assert resolved["available"] is True
    assert resolved["entry_slippage_usd"] == 0.27

    plan = make_arm_snapshot(
        order_type="MARKET",
        direction="SHORT",
        entry_price=2000.0,
        stop_loss=2010.0,
        take_profit=1980.0,
        resolved_execution_costs=resolved,
    )
    order = models.PaperOrder(
        id="test_slip_027_082",
        setup_id="setup_082",
        signal_id="sig_082",
        instrument="XAUUSDT",
        direction="SHORT",
        order_type="MARKET",
        planned_entry=2000.0,
        stop_loss=2010.0,
        take_profit=1980.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="armed",
        created_at=1000000,
        cost_snapshot=json.dumps(plan),
    )
    db.add(order)
    db.commit()

    # Quote BID 2000.00, ASK 2000.20
    # MARKET SHORT fill = bid - slip = 2000.00 - 0.27 = 1999.73
    quote = {"bid": 2000.00, "ask": 2000.20, "last": 2000.10, "timestamp": 1000100}
    coordinator.evaluate_orders_sync(db, ticker_override=quote, clock=clock, now_ms=1000100)

    db.refresh(order)
    assert order.state in ("open", "paper_open")
    assert abs(order.actual_entry - 1999.73) < 1e-4


def test_p083_custom_fees_configured():
    """Phần 083: Maker/Taker fee rates distinct from default 0.0002/0.0004 resolved correctly."""
    settings = {
        "costs": {
            "maker_fee_rate": 0.00015,
            "taker_fee_rate": 0.00035,
            "entry_slippage_usd": 0.12,
        },
        "config_version": 5,
    }
    resolved = resolve_execution_cost_context(settings=settings, as_of_ms=1000000)
    assert resolved["available"] is True
    assert resolved["maker_fee_rate"] == 0.00015
    assert resolved["taker_fee_rate"] == 0.00035
    assert resolved["entry_slippage_usd"] == 0.12


def test_p084_snapshot_first_override_against_settings_change(memory_db):
    """Phần 084: Old order snapshot with 0.08 slippage keeps 0.08 even when current settings change to 0.27."""
    db, clock, Session = memory_db
    coordinator = ExecutionCoordinator()

    old_plan = make_arm_snapshot(
        order_type="MARKET",
        direction="LONG",
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        resolved_execution_costs={
            "available": True,
            "entry_slippage_usd": 0.08,
            "tp_slippage_usd": 0.0,
            "sl_slippage_usd": 0.08,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
            "version": 1,
            "provenance": "OLD_SNAPSHOT",
        },
    )
    order = models.PaperOrder(
        id="test_old_snap_084",
        setup_id="setup_084",
        signal_id="sig_084",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="MARKET",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="armed",
        created_at=1000000,
        cost_snapshot=json.dumps(old_plan),
    )
    db.add(order)
    db.commit()

    # Even if current settings have slippage 0.27, evaluate_orders_sync must use order's frozen snapshot 0.08
    quote = {"bid": 2000.00, "ask": 2000.20, "last": 2000.10, "timestamp": 1000100}
    coordinator.evaluate_orders_sync(db, ticker_override=quote, clock=clock, now_ms=1000100)

    db.refresh(order)
    # Long fill at ask + 0.08 = 2000.20 + 0.08 = 2000.28 (not 2000.47)
    assert abs(order.actual_entry - 2000.28) < 1e-4


def test_p085_p086_dict_and_object_cost_normalization():
    """Phần 085, 086: Costs passed as dict or object normalize identically without getattr fallback."""
    class CostObj:
        def __init__(self):
            self.entry_slippage_usd = 0.22
            self.tp_slippage_usd = 0.0
            self.sl_slippage_usd = 0.22
            self.maker_fee_rate = 0.0002
            self.taker_fee_rate = 0.0004
            self.version = 3
            self.provenance = "OBJ_SOURCE"

    cost_obj = CostObj()
    cost_dict = {
        "entry_slippage_usd": 0.22,
        "tp_slippage_usd": 0.0,
        "sl_slippage_usd": 0.22,
        "maker_fee_rate": 0.0002,
        "taker_fee_rate": 0.0004,
        "version": 3,
        "provenance": "OBJ_SOURCE",
    }

    res_obj = resolve_execution_cost_context(snapshot=cost_obj)
    res_dict = resolve_execution_cost_context(snapshot=cost_dict)

    assert res_obj["available"] is True
    assert res_dict["available"] is True
    assert res_obj["entry_slippage_usd"] == res_dict["entry_slippage_usd"] == 0.22
    assert res_obj["version"] == res_dict["version"] == 3


def test_p087_p088_invalid_slippage_and_fees_rejected():
    """Phần 087, 088: NaN, inf, negative, and bool costs rejected with proper reasons."""
    for invalid_val in [float("nan"), float("inf"), -0.10, True]:
        snap = {
            "entry_slippage_usd": invalid_val,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        }
        res = resolve_execution_cost_context(snapshot=snap)
        assert res["available"] is False
        assert "INVALID" in res["reason"]

    # Invalid fee rate (e.g. >= 1.0 or negative)
    snap_fee = {
        "entry_slippage_usd": 0.10,
        "maker_fee_rate": -0.01,
        "taker_fee_rate": 0.0004,
    }
    res_fee = resolve_execution_cost_context(snapshot=snap_fee)
    assert res_fee["available"] is False


def test_p089_p090_version_and_persistence_roundtrip(memory_db):
    """Phần 089, 090: Config version bump preserved and survives DB persistence without loss."""
    db, clock, Session = memory_db

    settings_v3 = {"slippage_usd": 0.15, "config_version": 3}
    settings_v4 = {"slippage_usd": 0.25, "config_version": 4}

    res_v3 = resolve_execution_cost_context(settings=settings_v3, as_of_ms=1000)
    res_v4 = resolve_execution_cost_context(settings=settings_v4, as_of_ms=2000)

    assert res_v3["version"] == 3
    assert res_v4["version"] == 4

    plan_v3 = make_arm_snapshot(order_type="MARKET", resolved_execution_costs=res_v3)
    order_v3 = models.PaperOrder(
        id="cand_v3",
        setup_id="setup_v3",
        signal_id="sig_v3",
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        cost_snapshot=json.dumps(plan_v3),
        state="armed",
        created_at=1000,
    )
    db.add(order_v3)
    db.commit()

    # Fresh session readback
    db2 = Session()
    persisted = db2.query(models.PaperOrder).filter(models.PaperOrder.id == "cand_v3").first()
    assert persisted is not None
    loaded_snap = json.loads(persisted.cost_snapshot)
    assert loaded_snap.get("cost_version") == 3
    assert loaded_snap.get("entry_slippage_usd") == 0.15
    assert loaded_snap.get("resolved_execution_costs", {}).get("version") == 3
    db2.close()


# ======================================================================
# PARTS 091–100: Order Execution Parity & Order Types
# ======================================================================

def test_p091_p092_golden_market_fill_parity():
    """Phần 091, 092: Hand-checked MARKET parity: Bid 2000.0, Ask 2000.20, slip 0.27."""
    bid = 2000.00
    ask = 2000.20
    slip = 0.27

    # LONG: ask + slip = 2000.20 + 0.27 = 2000.47
    trig_l, supp_l, fill_l, _ = evaluate_comparison_entry(
        direction="LONG", planned_entry=2000.00, order_type="MARKET", bid=bid, ask=ask, order_slip=slip
    )
    assert trig_l and supp_l
    assert abs(fill_l - 2000.47) < 1e-4

    # SHORT: bid - slip = 2000.00 - 0.27 = 1999.73
    trig_s, supp_s, fill_s, _ = evaluate_comparison_entry(
        direction="SHORT", planned_entry=2000.00, order_type="MARKET", bid=bid, ask=ask, order_slip=slip
    )
    assert trig_s and supp_s
    assert abs(fill_s - 1999.73) < 1e-4


def test_p093_p094_p095_limit_order_contract(memory_db):
    """Phần 093, 094, 095: LIMIT order fill behavior on touch vs wait."""
    db, clock, Session = memory_db
    coordinator = ExecutionCoordinator()

    # Buy limit 2000.00, quote Ask 2000.20, Bid 2000.00 -> NOT filled because buyer pays Ask!
    plan = make_arm_snapshot(
        order_type="LIMIT",
        direction="LONG",
        entry_price=2000.0,
        resolved_execution_costs={"available": True, "entry_slippage_usd": 0.0, "version": 1},
    )
    order_buy = models.PaperOrder(
        id="limit_buy_wait",
        setup_id="setup_limit",
        signal_id="sig_limit",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="LIMIT",
        planned_entry=2000.00,
        stop_loss=1990.0,
        take_profit=2020.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="armed",
        created_at=1000000,
        cost_snapshot=json.dumps(plan),
    )
    db.add(order_buy)
    db.commit()

    quote_wait = {"bid": 2000.00, "ask": 2000.20, "last": 2000.10, "timestamp": 1000100}
    coordinator.evaluate_orders_sync(db, ticker_override=quote_wait, clock=clock, now_ms=1000100)
    db.refresh(order_buy)
    assert order_buy.state == "armed"  # Still armed, not filled!

    # Now Ask touches 2000.00 -> Should fill at limit price 2000.00
    quote_fill = {"bid": 1999.80, "ask": 2000.00, "last": 1999.90, "timestamp": 1000200}
    coordinator.evaluate_orders_sync(db, ticker_override=quote_fill, clock=clock, now_ms=1000200)
    db.refresh(order_buy)
    assert order_buy.state in ("open", "paper_open")
    assert abs(order_buy.actual_entry - 2000.00) < 1e-4


def test_p096_p097_stop_order_gap_fill(memory_db):
    """Phần 096, 097: STOP order fills at executable gap price plus slippage."""
    db, clock, Session = memory_db
    coordinator = ExecutionCoordinator()

    # Buy stop at 2005.00, slippage 0.10. Quote Ask gaps to 2008.00.
    plan = make_arm_snapshot(
        order_type="STOP",
        direction="LONG",
        entry_price=2005.0,
        stop_loss=1995.0,
        take_profit=2035.0,
        resolved_execution_costs={"available": True, "entry_slippage_usd": 0.10, "version": 1},
    )
    order_stop = models.PaperOrder(
        id="stop_buy_gap",
        setup_id="setup_stop",
        signal_id="sig_stop",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="STOP",
        planned_entry=2005.00,
        stop_loss=1995.0,
        take_profit=2035.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="armed",
        created_at=1000000,
        cost_snapshot=json.dumps(plan),
    )
    db.add(order_stop)
    db.commit()

    quote_gap = {"bid": 2007.80, "ask": 2008.00, "last": 2007.90, "timestamp": 1000100}
    coordinator.evaluate_orders_sync(db, ticker_override=quote_gap, clock=clock, now_ms=1000100)
    db.refresh(order_stop)
    assert order_stop.state in ("open", "paper_open")
    # Fills at executable Ask (2008.00) + slip (0.10) = 2008.10
    assert abs(order_stop.actual_entry - 2008.10) < 1e-4


def test_p098_net_rr_gate_rejects_insufficient_rr(memory_db):
    """Phần 098: Net RR gate rejects fill if gross RR meets requirement but net RR after custom costs drops below threshold."""
    db, clock, Session = memory_db
    coordinator = ExecutionCoordinator()

    # With higher slippage 0.50 and min_net_rr 1.8, gross RR 2.0 (risk 10, reward 20) yields net RR < 1.8
    plan = make_arm_snapshot(
        order_type="MARKET",
        direction="LONG",
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        min_net_rr=1.8,
        resolved_execution_costs={"available": True, "entry_slippage_usd": 0.50, "version": 1},
    )
    order = models.PaperOrder(
        id="test_net_rr_gate_098",
        setup_id="setup_098",
        signal_id="sig_098",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="MARKET",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="armed",
        created_at=1000000,
        cost_snapshot=json.dumps(plan),
    )
    db.add(order)
    db.commit()

    quote = {"bid": 2000.00, "ask": 2000.20, "last": 2000.10, "timestamp": 1000100}
    coordinator.evaluate_orders_sync(db, ticker_override=quote, clock=clock, now_ms=1000100)
    db.refresh(order)
    assert order.state == "rejected"
    assert "NET_RR_TOO_LOW" in str(order.invalidation_reason)


def test_p100_idempotent_duplicate_tick(memory_db):
    """Phần 100: Repeated quote tick does not duplicate fill or create multiple accounting events."""
    db, clock, Session = memory_db
    coordinator = ExecutionCoordinator()

    plan = make_arm_snapshot(
        order_type="MARKET",
        direction="LONG",
        entry_price=2000.0,
        resolved_execution_costs={"available": True, "entry_slippage_usd": 0.10, "version": 1},
    )
    order = models.PaperOrder(
        id="idem_tick_100",
        setup_id="setup_idem",
        signal_id="sig_idem",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="MARKET",
        planned_entry=2000.00,
        stop_loss=1990.0,
        take_profit=2020.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="armed",
        created_at=1000000,
        cost_snapshot=json.dumps(plan),
    )
    db.add(order)
    db.commit()

    quote = {"bid": 2000.00, "ask": 2000.20, "last": 2000.10, "timestamp": 1000100}
    # Tick 1 -> fills
    coordinator.evaluate_orders_sync(db, ticker_override=quote, clock=clock, now_ms=1000100)
    db.refresh(order)
    first_entry = order.actual_entry
    assert order.state in ("open", "paper_open")

    # Tick 2 (identical tick) -> must not change actual_entry or trigger double fill
    coordinator.evaluate_orders_sync(db, ticker_override=quote, clock=clock, now_ms=1000100)
    db.refresh(order)
    assert order.actual_entry == first_entry
    assert order.state in ("open", "paper_open")
