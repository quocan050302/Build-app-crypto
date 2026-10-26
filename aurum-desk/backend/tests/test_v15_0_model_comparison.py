"""
test_v15_0_model_comparison.py
V15.0 - Unit & Integration tests for Model Comparison & Statistical Reporting (PHẦN 080).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.model_comparison_service import ModelComparisonService


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@dataclass
class OrderStub:
    strategy_family: str
    state: str
    realized_pnl: float = 0.0
    fee_actual: float = 0.5
    fee_estimated: float = 0.5


def test_comparison_separates_models_without_cross_contamination():
    """PHẦN 080: Compares M5, M1 Reversal, M1 Continuation, and NY Daily Research separately without merging."""
    orders = [
        # STANDARD_SMC: 1 win (+20), 1 loss (-10) -> 50% winrate
        OrderStub("STANDARD_SMC", "closed", realized_pnl=20.0, fee_actual=1.0),
        OrderStub("STANDARD_SMC", "closed", realized_pnl=-10.0, fee_actual=1.0),

        # SMC_M1_REVERSAL: 2 wins (+15, +25) -> 100% winrate
        OrderStub("SMC_M1_REVERSAL", "closed", realized_pnl=15.0, fee_actual=0.5),
        OrderStub("SMC_M1_REVERSAL", "closed", realized_pnl=25.0, fee_actual=0.5),

        # NY_DAILY_RESEARCH: 1 loss (-5) -> 0% winrate (MUST NOT contaminate STANDARD_SMC winrate)
        OrderStub("NY_DAILY_RESEARCH", "closed", realized_pnl=-5.0, fee_actual=0.8),
    ]

    report = ModelComparisonService.generate_comparison_report(None, orders=orders)
    comp = report["comparison"]

    std = comp["STANDARD_SMC"]
    assert std["closed_trades"] == 2
    assert std["wins"] == 1
    assert std["losses"] == 1
    assert std["winrate_pct"] == 50.0
    assert std["net_pnl_usdt"] == 10.0

    m1_rev = comp["SMC_M1_REVERSAL"]
    assert m1_rev["closed_trades"] == 2
    assert m1_rev["wins"] == 2
    assert m1_rev["winrate_pct"] == 100.0
    assert m1_rev["net_pnl_usdt"] == 40.0

    ny_res = comp["NY_DAILY_RESEARCH"]
    assert ny_res["closed_trades"] == 1
    assert ny_res["wins"] == 0
    assert ny_res["losses"] == 1
    assert ny_res["winrate_pct"] == 0.0
    assert ny_res["net_pnl_usdt"] == -5.0

    # Verify disclaimer is present
    assert "NY_DAILY_RESEARCH không được gộp vào tỷ lệ thắng của STANDARD_SMC" in report["disclaimer_vi"]


def test_comparison_db_backed_query(db_session):
    """PHẦN 080: Database-backed model comparison report query."""
    ord1 = models.PaperOrder(
        id="ord_test_01", instrument="XAUUSDT", direction="LONG", state="closed",
        strategy_family="SMC_M1_REVERSAL", realized_pnl_net=12.5, fees_assumption=0.6,
        planned_entry=2620.0, stop_loss=2615.0, take_profit=2635.0, quantity=1.0, initial_risk_usdt=5.0,
        created_at=1_000_000,
    )
    ord2 = models.PaperOrder(
        id="ord_test_02", instrument="XAUUSDT", direction="SHORT", state="closed",
        strategy_family="NY_DAILY_RESEARCH", realized_pnl_net=-4.0, fees_assumption=0.5,
        planned_entry=2630.0, stop_loss=2636.0, take_profit=2615.0, quantity=1.0, initial_risk_usdt=6.0,
        created_at=1_000_000,
    )
    db_session.add_all([ord1, ord2])
    db_session.commit()

    report = ModelComparisonService.generate_comparison_report(db_session)
    comp = report["comparison"]

    assert comp["SMC_M1_REVERSAL"]["closed_trades"] == 1
    assert comp["SMC_M1_REVERSAL"]["wins"] == 1
    assert comp["NY_DAILY_RESEARCH"]["closed_trades"] == 1
    assert comp["NY_DAILY_RESEARCH"]["losses"] == 1
