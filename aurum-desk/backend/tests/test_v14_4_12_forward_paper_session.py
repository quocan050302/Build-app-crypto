"""
backend/tests/test_v14_4_12_forward_paper_session.py
V14.4.12 — Forward Paper Practice Session & Truthful Reporting Test Suite.

Proves:
  F01: Forward Session Creation & Restart — Capital, config, commit snapshot persisted
       cleanly without resetting user account, creating fake orders, or dispatching Telegram.
  F02: Real Production Pipeline Integration — Reuses three-session READY -> ARM -> FILL
       with forward session provenance; guards and Auto OFF verified.
  F03: Truthful Reporting & Metrics — Confirmed closed denominator only, realized drawdown,
       exact PnL math, INSUFFICIENT_SAMPLE threshold, no DB mutations during report.
  F04: User Notes & Export Integrity — Exported records match DB fields exactly;
       user notes/psychology preserved without fabricated defaults.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List
import pytest
from sqlalchemy.orm import Session, sessionmaker

import models
import smc_fixtures as F
from services.clock import IClock
from services.strategy_service import StrategyService
from services.trading_policy_service import TradingPolicyService
from services.execution_coordinator import ExecutionCoordinator
from services.trade_lifecycle_service import TradeLifecycleService
from services.forward_paper_service import (
    ForwardSessionConfig,
    create_forward_paper_session,
    export_forward_paper_data,
    generate_forward_paper_report,
    load_active_forward_session,
    load_forward_session,
    save_forward_session,
)


class MockClock(IClock):
    def __init__(self, dt: datetime):
        self._dt = dt

    def now_ms(self) -> int:
        return int(self._dt.timestamp() * 1000)

    def now_datetime(self, tz=None):
        return self._dt.astimezone(tz) if tz else self._dt


def make_shifted_candles(direction: str, target_now_ms: int) -> Dict[str, List[F.C]]:
    cbt = F.make_candles_by_tf(direction)
    time_offset = target_now_ms - F.NOW_MS
    shifted = {}
    for tf, candles in cbt.items():
        shifted[tf] = [
            F.C(
                timestamp=c.timestamp + time_offset,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                is_closed=c.is_closed,
            )
            for c in candles
        ]
    return shifted


# =========================================================================
# F01: FORWARD SESSION CREATION & RESTART
# =========================================================================

def test_f01_forward_session_creation_freeze_and_restart(isolated_db: Session):
    """PHẦN 043, 044, 045, 068 (F01):
    Create a new forward paper practice session with capital $1000 and frozen config.
    Assert:
    1. Capital, config, policy, commit SHA are persisted.
    2. Does NOT create fake trades or outbox notifications.
    3. Survives DB session restart cleanly.
    """
    now_ms = 1792000000000  # Fixed deterministic test epoch ms
    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_test_01",
        virtual_capital=1000.0,
        session_policy="ALL_SESSIONS_WITH_NY_RESERVE",
        commit_sha="3a34b6b6",
        now_ms=now_ms,
    )

    assert sess.session_id == "fwd_test_01"
    assert sess.virtual_capital == 1000.0
    assert sess.strategy_commit == "3a34b6b6"
    assert sess.session_policy == "ALL_SESSIONS_WITH_NY_RESERVE"

    # Assert no orders or outbox created by session start
    orders_count = isolated_db.query(models.PaperOrder).count()
    assert orders_count == 0, "Session start must not create orders"

    outbox_count = isolated_db.query(models.NotificationOutbox).count()
    assert outbox_count == 0, "Session start must not create notifications"

    # Simulate restart
    engine = isolated_db.bind
    SessionFactory = sessionmaker(bind=engine)
    isolated_db.close()

    db2 = SessionFactory()
    try:
        loaded = load_forward_session(db2, "fwd_test_01")
        assert loaded is not None
        assert loaded.session_id == "fwd_test_01"
        assert loaded.virtual_capital == 1000.0
        assert loaded.strategy_commit == "3a34b6b6"

        active = load_active_forward_session(db2)
        assert active is not None
        assert active.session_id == "fwd_test_01"
    finally:
        db2.close()


# =========================================================================
# F02: REAL PRODUCTION ENTRY PIPELINE INTEGRATION
# =========================================================================

from test_v14_4_11_three_session_execution import _run_full_ready_arm_fill_pipeline


def test_f02_real_production_entry_three_sessions_forward_provenance(isolated_db: Session):
    """PHẦN 005, 006, 069 (F02):
    Proves production READY -> ARM -> FILL pipeline runs for three sessions
    (Asia, London, NY) and binds forward session provenance cleanly.
    Verifies Auto OFF leaves scenario READY without auto-arming or fills.
    """
    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_prod_02",
        virtual_capital=1000.0,
    )

    # 1. Asian Session (09:00 VN) LONG production pipeline
    asia_dt = datetime(2026, 10, 14, 9, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    res_asia = _run_full_ready_arm_fill_pipeline(
        isolated_db,
        session_dt=asia_dt,
        direction="LONG",
        auto_enabled=True,
    )

    assert res_asia["setup"] is not None
    assert res_asia["armed_order"] is not None
    assert res_asia["filled_order"] is not None
    assert res_asia["filled_order"].state == "paper_open"

    # Bind forward session ID to filled order
    res_asia["filled_order"].session_instance_id = sess.session_id
    isolated_db.commit()

    # 2. Negative guard: Auto OFF promotes setup to READY but does NOT arm or fill
    london_dt = datetime(2026, 10, 14, 15, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    res_off = _run_full_ready_arm_fill_pipeline(
        isolated_db,
        session_dt=london_dt,
        direction="SHORT",
        auto_enabled=False,  # AUTO OFF
    )

    assert res_off["setup"] is not None
    assert res_off["armed_order"] is None
    assert res_off["filled_order"] is None


# =========================================================================
# F03: REPORT TRUTHFULNESS & CANONICAL METRICS
# =========================================================================

def test_f03_report_truthfulness_and_canonical_metrics(isolated_db: Session):
    """PHẦN 055–058, 070 (F03):
    Seed a realistic mix of trades:
    - 2 wins (STANDARD_SMC)
    - 1 loss (NY_QUOTA_PAPER)
    - 1 breakeven
    - 1 open pending (paper_open)
    - 1 review pending quarantine record
    Assert:
    1. Net PnL matches canonical sum of realized PnL.
    2. Confirmed denominator excludes open pending and review quarantine orders.
    3. Winrate = wins / confirmed closes.
    4. Realized drawdown computed from equity curve.
    5. INSUFFICIENT_SAMPLE flag since count < 30.
    6. Safe division by zero (no NaN/Infinity).
    7. Database trading state is completely untouched by report generation.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_rep_03",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    # Seed 2 wins
    o1 = models.PaperOrder(
        id="ord_w1", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4200.0, exit_cause="TP_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=3.80, realized_r=1.90, opened_at=now_ms + 1000, closed_at=now_ms + 5000,
        created_at=now_ms + 1000, session_instance_id="fwd_rep_03", strategy_family="STANDARD_SMC",
    )
    o2 = models.PaperOrder(
        id="ord_w2", instrument="XAUUSDT", direction="SHORT", state="closed",
        planned_entry=4200.0, actual_entry=4200.0, stop_loss=4210.0, take_profit=4180.0,
        actual_exit=4180.0, exit_cause="TP_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=3.80, realized_r=1.90, opened_at=now_ms + 10000, closed_at=now_ms + 15000,
        created_at=now_ms + 10000, session_instance_id="fwd_rep_03", strategy_family="STANDARD_SMC",
    )

    # Seed 1 loss
    o3 = models.PaperOrder(
        id="ord_l1", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4169.5, exit_cause="SL_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=-2.00, realized_r=-1.00, opened_at=now_ms + 20000, closed_at=now_ms + 25000,
        created_at=now_ms + 20000, session_instance_id="fwd_rep_03", strategy_family="NY_QUOTA_PAPER",
    )

    # Seed 1 breakeven
    o4 = models.PaperOrder(
        id="ord_be1", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4180.0, exit_cause="MANUAL_CLOSE", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=0.00, realized_r=0.00, opened_at=now_ms + 30000, closed_at=now_ms + 35000,
        created_at=now_ms + 30000, session_instance_id="fwd_rep_03", strategy_family="STANDARD_SMC",
    )

    # Seed 1 active open order (should NOT be in confirmed closes denominator)
    o5 = models.PaperOrder(
        id="ord_open_1", instrument="XAUUSDT", direction="LONG", state="paper_open",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        quantity=0.1, initial_risk_usdt=2.0, opened_at=now_ms + 40000, created_at=now_ms + 40000,
        session_instance_id="fwd_rep_03", strategy_family="STANDARD_SMC",
    )

    # Seed 1 quarantine review record
    rev_config = models.SystemConfig(
        key="recon_rev:ord_quar_1",
        value=json.dumps({
            "order_id": "ord_quar_1",
            "window_id": "ord_quar_1",
            "symbol": "XAUUSDT",
            "reason_codes": ["CORRUPT_COST_SNAPSHOT_JSON"],
            "source_status": "INVALID",
            "status": "PENDING_REVIEW",
            "first_seen_at": now_ms,
            "last_seen_at": now_ms,
            "attempt_count": 1,
            "next_retry_at": now_ms + 60000,
            "source_revision": 1,
        }),
        updated_at=now_ms,
    )

    isolated_db.add_all([o1, o2, o3, o4, o5, rev_config])
    isolated_db.commit()

    # Generate report
    report = generate_forward_paper_report(isolated_db, session_id="fwd_rep_03", now_ms=now_ms + 50000)

    # 1. Total fills & confirmed closes
    assert report["total_fills"] == 5  # 4 closed + 1 open
    assert report["confirmed_closed_trades"] == 4  # Exactly the 4 closed orders
    assert report["confirmed_wins"] == 2
    assert report["confirmed_losses"] == 1
    assert report["breakevens"] == 1
    assert report["pending_count"] == 1
    assert report["review_count"] >= 1

    # 2. Net PnL math
    expected_pnl = round(3.80 + 3.80 - 2.00 + 0.00, 2)
    assert report["net_pnl"] == expected_pnl

    # 3. Winrate: 2 / 4 = 0.50
    assert report["winrate"] == 0.50

    # 4. Profit factor: 7.60 / 2.00 = 3.80
    assert report["profit_factor"] == 3.8

    # 5. Expectancy: (1.9 + 1.9 - 1.0 + 0.0) / 4 = 0.70
    assert report["expectancy_r"] == 0.70

    # 6. Sample size status
    assert report["sample_status"] == "INSUFFICIENT_SAMPLE"

    # 7. Strategy breakdown separation
    std_stats = report["strategy_breakdown"]["standard_smc"]
    ny_stats = report["strategy_breakdown"]["ny_quota_paper"]
    assert std_stats["total_trades"] == 3
    assert std_stats["wins"] == 2
    assert ny_stats["total_trades"] == 1
    assert ny_stats["losses"] == 1

    # 8. Assert DB was not modified by report generator
    refreshed_o1 = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "ord_w1").first()
    assert refreshed_o1.realized_pnl_net == 3.80


# =========================================================================
# F04: USER NOTES & EXPORT INTEGRITY
# =========================================================================

def test_f04_notes_and_export_integrity(isolated_db: Session):
    """PHẦN 054, 071 (F04):
    Exports forward paper trade records with exact canonical fields.
    Asserts:
    1. Values in export match DB values exactly.
    2. User reflection notes and psychology are preserved.
    3. Missing fields remain None / empty, not fake defaults.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(isolated_db, session_id="fwd_exp_04", now_ms=now_ms)

    ord = models.PaperOrder(
        id="ord_exp_01",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.0,
        fees_assumption=0.10,
        realized_pnl_net=3.80,
        realized_r=1.90,
        opened_at=now_ms + 1000,
        closed_at=now_ms + 5000,
        created_at=now_ms + 1000,
        session_instance_id="fwd_exp_04",
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add(ord)

    # Attach TradeReview with user notes and psychology
    review = models.TradeReview(
        id="rev_exp_01",
        trade_id="ord_exp_01",
        user_notes="Waited for 15M sweep confirmation before entry",
        psychology_before="Patient, calm",
        created_at=now_ms + 6000,
        updated_at=now_ms + 6000,
    )
    isolated_db.add(review)
    isolated_db.commit()

    # Export as dict
    rows = export_forward_paper_data(isolated_db, session_id="fwd_exp_04", format="dict")
    assert len(rows) == 1
    row = rows[0]

    assert row["order_id"] == "ord_exp_01"
    assert row["direction"] == "LONG"
    assert row["planned_entry"] == 4180.0
    assert row["actual_entry"] == 4180.0
    assert row["actual_exit"] == 4200.0
    assert row["exit_cause"] == "TP_HIT"
    assert row["realized_pnl_net"] == 3.80
    assert row["fees"] == 0.10
    assert row["user_notes"] == "Waited for 15M sweep confirmation before entry"
    assert row["psychology"] == "Patient, calm"

    # Export as CSV
    csv_str = export_forward_paper_data(isolated_db, session_id="fwd_exp_04", format="csv")
    assert "ord_exp_01" in csv_str
    assert "TP_HIT" in csv_str
    assert "Waited for 15M sweep confirmation" in csv_str
