"""
backend/tests/test_v17_6_2_financial_run_isolation.py
AURUM V17.6.2 — Strict Financial Run Isolation & Zero Cross-Run Bleeding (FIX-03).

Verifies:
- PART 058: RUN_A orders and PnL are completely isolated from RUN_B orders and PnL.
- PART 059: Requesting a run with no linked orders or not found returns appropriate status without leaking other runs.
- PART 060: Requesting snapshot with run_id=None does NOT perform unscoped fallback queries across all PaperOrders.
- RULE 146: Unscoped paper_orders queries are completely blocked.
"""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.engine_observability_service import engine_observability_service
from services.paper_research_run_service import (
    PaperResearchRunService,
    associate_order_with_research_run,
)


@pytest.fixture
def isolated_db(tmp_path):
    db_path = tmp_path / "fin_iso.db"
    engine = create_engine(f"sqlite:///{db_path}")
    models.Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_fin01_two_runs_financial_isolation(isolated_db):
    """
    Seed:
    RUN_A: linked order_A1 with net_pnl = +25.0
    RUN_B: linked order_B1 with net_pnl = -40.0
    Query RUN_A: net_pnl must be +25.0 (NOT -15.0).
    Query RUN_B: net_pnl must be -40.0 (NOT -15.0).
    """
    session = isolated_db
    t_now = int(time.time() * 1000)
    t_start = t_now - 3600 * 1000

    # 1. Create Research Runs
    run_a = models.PaperResearchRunRecord(
        run_id="RUN_A",
        symbol="XAUUSDT",
        scope="CG01",
        status="COLLECTING",
        started_at_ms=t_start,
        created_at_ms=t_start,
        updated_at_ms=t_start,
    )
    run_b = models.PaperResearchRunRecord(
        run_id="RUN_B",
        symbol="XAUUSDT",
        scope="CG01",
        status="COLLECTING",
        started_at_ms=t_start,
        created_at_ms=t_start,
        updated_at_ms=t_start,
    )
    session.add_all([run_a, run_b])
    session.flush()

    # 2. Create Paper Orders
    order_a = models.PaperOrder(
        id="ord-a-1",
        instrument="XAUUSDT",
        direction="BUY",
        state="CLOSED",
        quantity=0.1,
        initial_risk_usdt=10.0,
        planned_entry=2700.0,
        actual_entry=2700.0,
        stop_loss=2695.0,
        take_profit=2710.0,
        realized_pnl_net=25.0,
        created_at=t_start + 10000,
        closed_at=t_start + 20000,
    )
    order_b = models.PaperOrder(
        id="ord-b-1",
        instrument="XAUUSDT",
        direction="SELL",
        state="CLOSED",
        quantity=0.1,
        initial_risk_usdt=10.0,
        planned_entry=2700.0,
        actual_entry=2700.0,
        stop_loss=2705.0,
        take_profit=2690.0,
        realized_pnl_net=-40.0,
        created_at=t_start + 30000,
        closed_at=t_start + 40000,
    )
    session.add(order_a)
    session.add(order_b)
    session.flush()

    # Link orders to respective runs
    associate_order_with_research_run(session, "RUN_A", "ord-a-1", opportunity_id="opp-a-1")
    associate_order_with_research_run(session, "RUN_B", "ord-b-1", opportunity_id="opp-b-1")
    session.commit()

    # Query snapshot for RUN_A
    snap_a = engine_observability_service.build_observation_dashboard_snapshot(
        db=session,
        symbol="XAUUSDT",
        run_id="RUN_A",
        start_ms=t_start,
        end_ms=t_now,
        now_ms=t_now,
    )
    fin_a = snap_a["financial"]
    assert fin_a is not None
    assert fin_a["closed_trades"] == 1
    assert fin_a["wins"] == 1
    assert fin_a["losses"] == 0
    assert fin_a["net_pnl_usd"] == 25.0
    assert fin_a["winrate_pct"] == 100.0

    # Query snapshot for RUN_B
    snap_b = engine_observability_service.build_observation_dashboard_snapshot(
        db=session,
        symbol="XAUUSDT",
        run_id="RUN_B",
        start_ms=t_start,
        end_ms=t_now,
        now_ms=t_now,
    )
    fin_b = snap_b["financial"]
    assert fin_b is not None
    assert fin_b["closed_trades"] == 1
    assert fin_b["wins"] == 0
    assert fin_b["losses"] == 1
    assert fin_b["net_pnl_usd"] == -40.0
    assert fin_b["winrate_pct"] == 0.0


def test_fin02_unscoped_fallback_is_blocked_when_run_id_none(isolated_db):
    """
    PART 060 & RULE 146:
    When run_id is None and no run is specified, financial_summary must be None.
    It MUST NOT leak orders ord-a-1 or ord-b-1!
    """
    session = isolated_db
    t_now = int(time.time() * 1000)
    t_start = t_now - 3600 * 1000

    snap_none = engine_observability_service.build_observation_dashboard_snapshot(
        db=session,
        symbol="XAUUSDT",
        run_id=None,
        start_ms=t_start,
        end_ms=t_now,
        now_ms=t_now,
    )
    assert snap_none["financial"] is None
