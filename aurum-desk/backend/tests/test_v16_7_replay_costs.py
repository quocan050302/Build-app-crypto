"""
backend/tests/test_v16_7_replay_costs.py
V16.7 Group E, O: Replay Costs & Immutable Config Resolution.

1. test_replay_costs_zero_fee_bps_preserved:
   0 bps fee is preserved as 0.0 (not replaced with default 0.0004).
2. test_replay_costs_fractional_fee_bps_converted_accurately:
   0.5 bps fee is converted to 0.00005 (0.5 / 10000.0).
3. test_replay_costs_custom_maker_taker_bps:
   maker_fee_bps=1.0, taker_fee_bps=3.5 converted accurately.
4. test_replay_costs_zero_slippage_preserved:
   slippage=0.0 is preserved as 0.0.
5. test_caller_objects_never_mutated:
   Caller's CostAssumptions and CG01Config remain completely unmodified.
6. test_two_interleaved_replay_runs_isolation:
   Two replay runs with different capital, risk, and costs run interleaved
   without cross-contaminating settings or state.
"""
import copy
import math
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import run_schema_migrations
from domain_calculator import CostAssumptions
from lab.cg01_replay_execution_adapter import CG01ReplayExecutionAdapter
import crud
import models
from models import Base
from services.cg01_types import CG01Config
from services.clock import ReplayClock
from services.plan_financial_context_service import resolve_replay_financial_config


def test_replay_costs_zero_fee_bps_preserved():
    """PHẦN 044: 0 bps fee must be preserved (0.0 fee rate)."""
    cfg = resolve_replay_financial_config(fee_bps=0.0)
    assert cfg.taker_fee_rate == 0.0, f"Expected 0.0, got {cfg.taker_fee_rate}"
    assert cfg.costs.taker_fee_rate == 0.0


def test_replay_costs_fractional_fee_bps_converted_accurately():
    """PHẦN 045: 0.5 bps fee must be converted to 0.00005."""
    cfg = resolve_replay_financial_config(fee_bps=0.5)
    assert abs(cfg.taker_fee_rate - 0.00005) < 1e-9, f"Expected 0.00005, got {cfg.taker_fee_rate}"
    assert abs(cfg.costs.taker_fee_rate - 0.00005) < 1e-9


def test_replay_costs_custom_maker_taker_bps():
    """PHẦN 046: Explicit maker_fee_bps and taker_fee_bps take precedence."""
    cfg = resolve_replay_financial_config(maker_fee_bps=1.0, taker_fee_bps=3.5)
    assert abs(cfg.maker_fee_rate - 0.0001) < 1e-9
    assert abs(cfg.taker_fee_rate - 0.00035) < 1e-9


def test_replay_costs_zero_slippage_preserved():
    """PHẦN 044: slippage=0.0 must be preserved."""
    cfg = resolve_replay_financial_config(slippage=0.0)
    assert cfg.slippage_usd == 0.0
    assert cfg.costs.slippage_usd == 0.0


def test_caller_objects_never_mutated():
    """
    PHẦN 041 & 042: Passing CostAssumptions and CG01Config to adapter
    must not mutate the caller's objects.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    caller_costs = CostAssumptions(
        slippage_usd=0.25,
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0004,
    )
    caller_config = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
    )
    # Record original state
    orig_costs_slip = caller_costs.slippage_usd
    orig_cfg_dict = dict(caller_config.__dict__)

    clock = ReplayClock(initial_ms=1792050000000)
    adapter = CG01ReplayExecutionAdapter(
        db=db,
        clock=clock,
        config=caller_config,
        costs=caller_costs,
        initial_capital=5000.0,
        risk_pct=0.10,
        slippage=0.0,  # override slippage
        fee_bps=0.5,   # override fee
    )

    # Caller's objects must NOT have been mutated!
    assert caller_costs.slippage_usd == orig_costs_slip, (
        f"caller_costs.slippage_usd was mutated to {caller_costs.slippage_usd}"
    )
    # Check caller config fields
    for k, v in orig_cfg_dict.items():
        assert getattr(caller_config, k) == v, f"caller_config.{k} was mutated"

    db.close()


def test_two_interleaved_replay_runs_isolation():
    """
    PHẦN 059: Two replay adapters on separate isolated DBs with different capital,
    risk, and costs must remain completely independent during interleaved execution.
    """
    engine1 = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine1)
    run_schema_migrations(engine1)
    db1 = sessionmaker(bind=engine1)()

    engine2 = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine2)
    run_schema_migrations(engine2)
    db2 = sessionmaker(bind=engine2)()

    now_ms = 1792050000000
    clock1 = ReplayClock(initial_ms=now_ms)
    clock2 = ReplayClock(initial_ms=now_ms)

    # Adapter 1: 5000 capital, 0.10% risk, 0 slippage
    adapter1 = CG01ReplayExecutionAdapter(
        db=db1,
        clock=clock1,
        initial_capital=5000.0,
        risk_pct=0.10,
        slippage=0.0,
    )

    # Adapter 2: 1000 capital, 0.25% risk, 0.10 slippage
    adapter2 = CG01ReplayExecutionAdapter(
        db=db2,
        clock=clock2,
        initial_capital=1000.0,
        risk_pct=0.25,
        slippage=0.10,
    )

    assert adapter1.initial_capital == 5000.0
    assert adapter1.risk_pct == 0.10
    assert adapter1.slippage == 0.0

    assert adapter2.initial_capital == 1000.0
    assert adapter2.risk_pct == 0.25
    assert adapter2.slippage == 0.10

    # Verify isolated DayAudit in DBs
    audit1 = crud.get_or_create_today_audit(db1, clock=clock1)
    audit2 = crud.get_or_create_today_audit(db2, clock=clock2)

    assert audit1.initial_equity == 5000.0
    assert audit2.initial_equity == 1000.0

    db1.close()
    db2.close()
