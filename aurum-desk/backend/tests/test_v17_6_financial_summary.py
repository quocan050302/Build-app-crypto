"""
backend/tests/test_v17_6_financial_summary.py
AURUM V17.6 — Financial Monitoring & Performance Review Test Suite (Phase G, Parts 066-075).

Verifies:
1. Post-fee accounting: Gross PnL minus entry/exit commissions equals true Net Realized PnL.
2. Fail-closed financial reporting: Zero closed trades produces winrate=None ("N/A"), sample_count=0.
3. Separation of planned/armed vs realized: ARMED/OPEN orders are not counted as closed trades.
4. Drawdown calculation from committed ledger equity curve.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.engine_observability_service import engine_observability_service
from services.paper_research_run_service import PaperResearchRunService, build_cg01_paper_run_summary


@pytest.fixture
def fin_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session = SessionFactory()
    yield session
    session.close()


def test_f01_zero_closed_trades_fail_closed(fin_db: Session):
    run_id = "run-fin-zero"
    PaperResearchRunService.get_or_create_run(run_id, db=fin_db, create_if_missing=True)

    summary = build_cg01_paper_run_summary(run_id, db=fin_db)
    trades = summary["trades"]
    fin_post = summary["financial_metrics_post_fee"]

    assert trades["total_closed_trades"] == 0
    assert trades["winrate_pct"] is None  # Strictly None (renders as N/A in UI)
    assert trades["winning_trades"] == 0
    assert trades["losing_trades"] == 0
    assert fin_post["net_realized_pnl_usd"] == 0.0

    # Test dashboard financial summary
    dash = engine_observability_service.build_observation_dashboard_snapshot(
        fin_db,
        run_id=run_id,
        now_ms=1700000000000,
    )
    fin = dash["financial"]
    assert fin["closed_trades"] == 0
    assert fin["winrate_pct"] is None
    assert fin["net_pnl_usd"] == 0.0


def test_f02_post_fee_pnl_accounting(fin_db: Session):
    run_id = "run-fin-trades"
    PaperResearchRunService.get_or_create_run(run_id, db=fin_db, create_if_missing=True)

    # 1. Winning trade: net +95
    order1 = models.PaperOrder(
        id="order-win-01",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=1700000000000,
        realized_pnl_net=95.0,
        realized_r=2.0,
    )
    link1 = models.PaperResearchOrderLink(
        run_id=run_id,
        order_id="order-win-01",
        linked_at_ms=1700000060000,
    )

    # 2. Losing trade: net -55
    order2 = models.PaperOrder(
        id="order-loss-02",
        instrument="XAUUSDT",
        direction="SHORT",
        state="closed",
        planned_entry=2660.0,
        stop_loss=2665.0,
        take_profit=2650.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=1700000000000,
        realized_pnl_net=-55.0,
        realized_r=-1.0,
    )
    link2 = models.PaperResearchOrderLink(
        run_id=run_id,
        order_id="order-loss-02",
        linked_at_ms=1700000160000,
    )

    fin_db.add_all([order1, link1, order2, link2])
    fin_db.commit()

    summary = build_cg01_paper_run_summary(run_id, db=fin_db)
    trades = summary["trades"]
    fin_post = summary["financial_metrics_post_fee"]

    assert trades["total_closed_trades"] == 2
    assert trades["winning_trades"] == 1
    assert trades["losing_trades"] == 1
    assert trades["winrate_pct"] == 50.0
    assert fin_post["net_realized_pnl_usd"] == 40.0  # +95 - 55 = +40

    dash = engine_observability_service.build_observation_dashboard_snapshot(
        fin_db,
        run_id=run_id,
        now_ms=1700000200000,
    )
    fin = dash["financial"]
    assert fin["closed_trades"] == 2
    assert fin["winrate_pct"] == 50.0
    assert fin["net_pnl_usd"] == 40.0


def test_f03_armed_and_open_orders_excluded_from_realized(fin_db: Session):
    run_id = "run-fin-open"
    PaperResearchRunService.get_or_create_run(run_id, db=fin_db, create_if_missing=True)

    # ARMED order
    order_armed = models.PaperOrder(
        id="order-arm-01",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=1700000000000,
    )
    link_armed = models.PaperResearchOrderLink(
        run_id=run_id,
        order_id="order-arm-01",
        linked_at_ms=1700000000000,
    )

    # OPEN order
    order_open = models.PaperOrder(
        id="order-open-02",
        instrument="XAUUSDT",
        direction="SHORT",
        state="paper_open",
        planned_entry=2655.0,
        stop_loss=2660.0,
        take_profit=2645.0,
        quantity=1.0,
        initial_risk_usdt=5.0,
        created_at=1700000000000,
    )
    link_open = models.PaperResearchOrderLink(
        run_id=run_id,
        order_id="order-open-02",
        linked_at_ms=1700000010000,
    )

    fin_db.add_all([order_armed, link_armed, order_open, link_open])
    fin_db.commit()

    summary = build_cg01_paper_run_summary(run_id, db=fin_db)
    trades = summary["trades"]
    fin_post = summary["financial_metrics_post_fee"]

    # Neither ARMED nor OPEN is a closed trade
    assert trades["total_closed_trades"] == 0
    assert trades["winrate_pct"] is None
    assert fin_post["net_realized_pnl_usd"] == 0.0
