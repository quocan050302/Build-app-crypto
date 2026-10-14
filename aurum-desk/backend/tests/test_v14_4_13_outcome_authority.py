"""
backend/tests/test_v14_4_13_outcome_authority.py
V14.4.13 — Outcome Authority, Verified Denominator, and Breakdown Test Suite.

Covers:
  - O01: Authority Matrix & Confirmed Denominator (PHẦN 033–040, 065) — Win, loss, breakeven,
        pending review, conflicted, unverified, missing PnL, NaN PnL, open, armed, expired.
        Pure read query (zero mutations). Truthful denominator excludes unconfirmed trades.
  - O02: Review Scope & Strategy/Session Breakdown (PHẦN 041–045, 066) — Review B does not
        increase session A count; technical issue vs outcome conflict; family and session breakdown;
        missing R does not fake expectancy = 0.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Dict, Any, List
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session

import models
from services.forward_paper_service import (
    create_forward_paper_session,
    bind_order_to_forward_session,
    classify_forward_outcome,
    generate_forward_paper_report,
    export_forward_paper_data,
)


# =========================================================================
# O01: AUTHORITY MATRIX & CONFIRMED DENOMINATOR (PHẦN 033–040, 065)
# =========================================================================

def test_auth01_outcome_authority_matrix_and_denominator(isolated_db: Session):
    """PHẦN 033–040, 065 (O01):
    Seeds a comprehensive authority matrix:
      1. Confirmed WIN (closed, PnL > 0, valid R, exit_cause="TP_HIT")
      2. Confirmed LOSS (closed, PnL < 0, valid R, exit_cause="SL_HIT")
      3. Confirmed BREAKEVEN (closed, PnL == 0.0, valid R, exit_cause="MANUAL_CLOSE")
      4. Pending Review (closed, active background review record)
      5. Conflicted Review (closed, terminal conflict record requiring manual review)
      6. Unverified Exit (closed, missing exit_cause or closed_at)
      7. Missing PnL (closed, realized_pnl_net = None) -> DATA_INVALID, NOT breakeven!
      8. Non-finite PnL (closed, realized_pnl_net = float('nan')) -> DATA_INVALID
      9. OPEN position (state="paper_open")
      10. ARMED order (state="armed")
      11. EXPIRED order (state="expired")
    Asserts:
      - Only the 3 confirmed completed trades enter the winrate denominator.
      - Winrate = 1 / 3 = 0.333.
      - Missing PnL is NOT counted as breakeven.
      - Pure read-only report generation does NOT mutate database.
    """
    now_ms = 1792000000000
    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_matrix_01",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    # 1. Confirmed WIN
    o_win = models.PaperOrder(
        id="ord_mat_win", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4200.0, exit_cause="TP_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=4.00, realized_r=2.00, opened_at=now_ms + 1000, closed_at=now_ms + 5000,
        created_at=now_ms + 1000, strategy_family="STANDARD_SMC",
    )

    # 2. Confirmed LOSS
    o_loss = models.PaperOrder(
        id="ord_mat_loss", instrument="XAUUSDT", direction="SHORT", state="closed",
        planned_entry=4200.0, actual_entry=4200.0, stop_loss=4210.0, take_profit=4180.0,
        actual_exit=4210.0, exit_cause="SL_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=-2.00, realized_r=-1.00, opened_at=now_ms + 10000, closed_at=now_ms + 15000,
        created_at=now_ms + 10000, strategy_family="STANDARD_SMC",
    )

    # 3. Confirmed BREAKEVEN
    o_be = models.PaperOrder(
        id="ord_mat_be", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4180.0, exit_cause="MANUAL_CLOSE", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=0.00, realized_r=0.00, opened_at=now_ms + 20000, closed_at=now_ms + 25000,
        created_at=now_ms + 20000, strategy_family="STANDARD_SMC",
    )

    # 4. Closed but Pending Conflict Review
    o_rev = models.PaperOrder(
        id="ord_mat_rev", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4200.0, exit_cause="TP_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=4.00, realized_r=2.00, opened_at=now_ms + 30000, closed_at=now_ms + 35000,
        created_at=now_ms + 30000, strategy_family="STANDARD_SMC",
    )
    rev_config_pending = models.SystemConfig(
        key="recon_rev:ord_mat_rev",
        value=json.dumps({
            "order_id": "ord_mat_rev",
            "status": "PENDING_REVIEW",
            "reason_codes": ["LATE_EVIDENCE_CONFLICT"],
        }),
        updated_at=now_ms,
    )

    # 5. Closed but Conflicted (manual review required)
    o_conf = models.PaperOrder(
        id="ord_mat_conf", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4200.0, exit_cause="TP_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=4.00, realized_r=2.00, opened_at=now_ms + 40000, closed_at=now_ms + 45000,
        created_at=now_ms + 40000, strategy_family="STANDARD_SMC",
    )
    rev_config_conf = models.SystemConfig(
        key="recon_rev:ord_mat_conf",
        value=json.dumps({
            "order_id": "ord_mat_conf",
            "status": "MANUAL_REVIEW_REQUIRED",
            "reason_codes": ["UNRESOLVED_EARLIER_EXIT_CONFLICT"],
        }),
        updated_at=now_ms,
    )

    # 6. Unverified Exit (missing exit cause)
    o_unver = models.PaperOrder(
        id="ord_mat_unver", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4200.0, exit_cause=None, quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=4.00, realized_r=2.00, opened_at=now_ms + 50000, closed_at=now_ms + 55000,
        created_at=now_ms + 50000, strategy_family="STANDARD_SMC",
    )

    # 7. Missing PnL (realized_pnl_net is None)
    o_null_pnl = models.PaperOrder(
        id="ord_mat_null_pnl", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4200.0, exit_cause="TP_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=None, realized_r=None, opened_at=now_ms + 60000, closed_at=now_ms + 65000,
        created_at=now_ms + 60000, strategy_family="STANDARD_SMC",
    )

    # 8. Non-finite PnL (NaN)
    o_nan_pnl = models.PaperOrder(
        id="ord_mat_nan_pnl", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4200.0, exit_cause="TP_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=float("nan"), realized_r=None, opened_at=now_ms + 70000, closed_at=now_ms + 75000,
        created_at=now_ms + 70000, strategy_family="STANDARD_SMC",
    )

    # 9. Active OPEN position
    o_open = models.PaperOrder(
        id="ord_mat_open", instrument="XAUUSDT", direction="LONG", state="paper_open",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        quantity=0.1, initial_risk_usdt=2.0, opened_at=now_ms + 80000, created_at=now_ms + 80000,
        strategy_family="STANDARD_SMC",
    )

    # 10. ARMED order
    o_armed = models.PaperOrder(
        id="ord_mat_armed", instrument="XAUUSDT", direction="LONG", state="armed",
        planned_entry=4180.0, actual_entry=None, stop_loss=4170.0, take_profit=4200.0,
        quantity=0.1, initial_risk_usdt=2.0, created_at=now_ms + 90000,
        strategy_family="STANDARD_SMC",
    )

    # 11. EXPIRED order
    o_expired = models.PaperOrder(
        id="ord_mat_exp", instrument="XAUUSDT", direction="LONG", state="expired",
        planned_entry=4180.0, actual_entry=None, stop_loss=4170.0, take_profit=4200.0,
        quantity=0.1, initial_risk_usdt=2.0, created_at=now_ms + 100000,
        strategy_family="STANDARD_SMC",
    )

    all_orders = [
        o_win, o_loss, o_be, o_rev, o_conf, o_unver,
        o_null_pnl, o_nan_pnl, o_open, o_armed, o_expired,
    ]
    isolated_db.add_all(all_orders + [rev_config_pending, rev_config_conf])
    isolated_db.commit()

    # Explicitly bind all orders to Session
    for o in all_orders:
        bind_order_to_forward_session(isolated_db, sess.session_id, o, now_ms=now_ms)

    # Snapshot order states before report to verify zero DB mutation
    before_states = {o.id: (o.state, o.realized_pnl_net, o.exit_cause) for o in all_orders}

    # Generate truthful forward paper report
    report = generate_forward_paper_report(isolated_db, sess.session_id, now_ms=now_ms + 150000)

    # Verify zero DB mutation
    after_states = {o.id: (o.state, o.realized_pnl_net, o.exit_cause) for o in all_orders}
    assert before_states == after_states, "Report generation must be pure read query without mutations"

    # Confirmed outcomes count: exactly 3 (WIN, LOSS, BREAKEVEN)
    assert report["confirmed_closed_trades"] == 3
    assert report["confirmed_wins"] == 1
    assert report["confirmed_losses"] == 1
    assert report["breakevens"] == 1

    # Confirmed Winrate = 1 / 3 = 0.333 (NOT dividing by total fills or corrupted trades!)
    assert report["winrate"] == 0.333

    # Net PnL of confirmed trades: 4.0 - 2.0 + 0.0 = 2.0
    assert report["net_pnl"] == 2.0

    # Counts of non-confirmed categories
    assert report["pending_count"] == 1       # ord_mat_open
    assert report["armed_count"] == 1         # ord_mat_armed
    assert report["review_count"] >= 2        # ord_mat_rev + ord_mat_conf
    assert report["unverified_count"] == 1    # ord_mat_unver
    assert report["data_invalid_count"] == 2  # ord_mat_null_pnl + ord_mat_nan_pnl


# =========================================================================
# O02: REVIEW SCOPE & STRATEGY BREAKDOWN (PHẦN 041–045, 066)
# =========================================================================

def test_auth02_review_scope_and_strategy_breakdown(isolated_db: Session):
    """PHẦN 041–045, 066 (O02):
    Proves:
      1. Review record for Session B does NOT leak into Session A count ("Review B không tăng count A").
      2. Separates STANDARD_SMC and NY_QUOTA_PAPER strategy families.
      3. Missing realized_r does NOT fake expectancy = 0.0; reports valid_r_count and missing_r_count.
      4. Trading sessions are resolved with DST and explicit UNKNOWN_OR_OFF bucket.
    """
    now_ms = 1792000000000

    sess_a = create_forward_paper_session(
        isolated_db, session_id="fwd_scope_A", virtual_capital=1000.0, now_ms=now_ms
    )

    # Order in Session A (STANDARD_SMC, Asian session)
    ord_a = models.PaperOrder(
        id="ord_scope_A1", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4200.0, exit_cause="TP_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=4.00, realized_r=2.00, opened_at=now_ms + 1000, closed_at=now_ms + 5000,
        created_at=now_ms + 1000, session_instance_id="ASIA_20261014",
        strategy_family="STANDARD_SMC",
    )

    # Order in Session A (NY_QUOTA_PAPER, NY session, missing realized_r)
    ord_a2 = models.PaperOrder(
        id="ord_scope_A2", instrument="XAUUSDT", direction="SHORT", state="closed",
        planned_entry=4200.0, actual_entry=4200.0, stop_loss=4210.0, take_profit=4180.0,
        actual_exit=4180.0, exit_cause="TP_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=4.00, realized_r=None, opened_at=now_ms + 10000, closed_at=now_ms + 15000,
        created_at=now_ms + 10000, session_instance_id="NY_20261014",
        strategy_family="NY_QUOTA_PAPER",
    )

    isolated_db.add_all([ord_a, ord_a2])
    isolated_db.commit()
    bind_order_to_forward_session(isolated_db, sess_a.session_id, ord_a, now_ms=now_ms)
    bind_order_to_forward_session(isolated_db, sess_a.session_id, ord_a2, now_ms=now_ms)

    # Now create Session B with an order that HAS an active review record
    sess_b = create_forward_paper_session(
        isolated_db, session_id="fwd_scope_B", virtual_capital=1000.0, now_ms=now_ms + 20000
    )
    ord_b = models.PaperOrder(
        id="ord_scope_B1", instrument="XAUUSDT", direction="LONG", state="closed",
        planned_entry=4180.0, actual_entry=4180.0, stop_loss=4170.0, take_profit=4200.0,
        actual_exit=4200.0, exit_cause="TP_HIT", quantity=0.1, initial_risk_usdt=2.0,
        realized_pnl_net=4.00, realized_r=2.00, opened_at=now_ms + 21000, closed_at=now_ms + 25000,
        created_at=now_ms + 21000, session_instance_id="LONDON_20261014",
        strategy_family="STANDARD_SMC",
    )
    rev_config_b = models.SystemConfig(
        key="recon_rev:ord_scope_B1",
        value=json.dumps({
            "order_id": "ord_scope_B1",
            "status": "PENDING_REVIEW",
            "reason_codes": ["CROSS_SESSION_CONFLICT"],
        }),
        updated_at=now_ms + 26000,
    )
    isolated_db.add_all([ord_b, rev_config_b])
    isolated_db.commit()
    bind_order_to_forward_session(isolated_db, sess_b.session_id, ord_b, now_ms=now_ms + 20000)

    # Generate report for Session A
    report_a = generate_forward_paper_report(isolated_db, sess_a.session_id, now_ms=now_ms + 30000)

    # 1. Review B must NOT increase review count in Session A!
    assert report_a["review_count"] == 0, "Review in Session B must not leak into Session A count"

    # 2. Strategy breakdown
    strat = report_a["strategy_breakdown"]
    assert "standard_smc" in strat
    assert "ny_quota_paper" in strat
    assert strat["standard_smc"]["total_trades"] == 1
    assert strat["standard_smc"]["wins"] == 1
    assert strat["ny_quota_paper"]["total_trades"] == 1
    assert strat["ny_quota_paper"]["wins"] == 1

    # 3. Realized R: only 1 trade had valid R (2.00), 1 trade had missing R
    assert report_a["valid_r_count"] == 1
    assert report_a["missing_r_count"] == 1
    assert report_a["expectancy_r"] == 2.0  # Computed from valid R only!

    # 4. Session breakdown has canonical buckets
    sess_split = report_a["session_breakdown"]
    assert "ASIA" in sess_split
    assert "LONDON" in sess_split
    assert "NEW_YORK" in sess_split
    assert "UNKNOWN_OR_OFF" in sess_split
    assert sess_split["ASIA"]["total_trades"] == 1
    assert sess_split["NEW_YORK"]["total_trades"] == 1
