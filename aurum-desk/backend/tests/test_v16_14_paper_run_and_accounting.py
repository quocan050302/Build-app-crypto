"""
backend/tests/test_v16_14_paper_run_and_accounting.py
V16.14 Automated Tests for Paper Research Run and Post-Fee Accounting.

Covers:
- Rules 111-120: PaperResearchRun lifecycle, COLLECTING status, immutable fingerprints.
- Rules 121-125: Closed trades only in winrate, post-fee net PnL, running peak drawdown.
- Rule 176: Long TP, Long SL, Short TP, Short SL financial calculations.
- Rule 195: Markdown and CSV export integrity.
"""
import pytest
from services.paper_research_run_service import (
    PaperResearchRun,
    PaperResearchRunService,
    build_cg01_paper_run_summary,
    calculate_running_peak_drawdown,
    export_paper_run_summary_csv,
    export_paper_run_summary_markdown,
)


# =========================================================================
# 1. PaperResearchRun Lifecycle (Rules 111, 119, 120)
# =========================================================================
def test_paper_research_run_lifecycle_and_collecting_status():
    """
    Rules 111, 119: Runs start in COLLECTING state and transition to COMPLETED upon termination.
    Never report PASS for incomplete runs (Rule 119).
    """
    run = PaperResearchRunService.get_or_create_run("run_v16_14_test_01", symbol="XAUUSDT")
    assert run.run_id == "run_v16_14_test_01"
    assert run.status == "COLLECTING"
    assert run.software_version == "V16.14"

    completed = PaperResearchRunService.complete_run("run_v16_14_test_01")
    assert completed is not None
    assert completed.status == "COMPLETED"
    assert completed.ended_at_ms is not None


# =========================================================================
# 2. Financial Accounting Post-Fee & Drawdown (Rules 121-125, 176)
# =========================================================================
def test_point_in_time_running_peak_drawdown():
    """
    Rule 125: Calculate correct running peak maximum drawdown in USD and percentage.
    """
    # Start: 1000
    # Trade 1: +100 -> peak = 1100
    # Trade 2: -50  -> equity = 1050 (dd = 50, 4.55%)
    # Trade 3: -100 -> equity = 950  (dd = 150, 13.64%)
    # Trade 4: +200 -> equity = 1150 (new peak = 1150)
    # Trade 5: -50  -> equity = 1100 (dd = 50, 4.35%)
    pnl_series = [100.0, -50.0, -100.0, 200.0, -50.0]
    dd_usd, dd_pct = calculate_running_peak_drawdown(pnl_series, initial_capital=1000.0)

    assert dd_usd == 150.0
    assert dd_pct == round(150.0 / 1100.0 * 100.0, 2)


def test_rule_176_long_and_short_tp_sl_post_fee_accounting():
    """
    Rule 176: Test Long TP, Long SL, Short TP, Short SL accounting with modeled fees and slippage.
    Rule 121: Confirmed closed trades only in winrate; open positions excluded.
    """
    synthetic_trades = [
        # 1. Long TP: gross +20.0
        {
            "id": "ord_long_tp",
            "direction": "LONG",
            "state": "closed",
            "planned_entry": 2500.0,
            "actual_entry": 2500.0,
            "quantity": 0.1,
            "pnl": 20.0,
            "slippage_usd": 0.10,
        },
        # 2. Long SL: gross -10.0
        {
            "id": "ord_long_sl",
            "direction": "LONG",
            "state": "closed",
            "planned_entry": 2500.0,
            "actual_entry": 2500.0,
            "quantity": 0.1,
            "pnl": -10.0,
            "slippage_usd": 0.10,
        },
        # 3. Short TP: gross +30.0
        {
            "id": "ord_short_tp",
            "direction": "SHORT",
            "state": "closed",
            "planned_entry": 2550.0,
            "actual_entry": 2550.0,
            "quantity": 0.1,
            "pnl": 30.0,
            "slippage_usd": 0.10,
        },
        # 4. Short SL: gross -12.0
        {
            "id": "ord_short_sl",
            "direction": "SHORT",
            "state": "closed",
            "planned_entry": 2550.0,
            "actual_entry": 2550.0,
            "quantity": 0.1,
            "pnl": -12.0,
            "slippage_usd": 0.10,
        },
        # 5. Open Position: must be excluded from closed trade winrate (Rule 121)
        {
            "id": "ord_open_active",
            "direction": "LONG",
            "state": "open",
            "planned_entry": 2510.0,
            "quantity": 0.1,
            "pnl": 15.0,  # Unrealized
        },
    ]

    summary = build_cg01_paper_run_summary(
        run_id="run_acct_test",
        synthetic_trades=synthetic_trades,
    )

    tr = summary["trades"]
    fin = summary["financial_metrics_post_fee"]

    # Exactly 4 closed trades, 1 open trade
    assert tr["total_closed_trades"] == 4
    assert tr["open_trades_count"] == 1
    assert tr["winning_trades"] == 2
    assert tr["losing_trades"] == 2
    assert tr["winrate_pct"] == 50.0

    # Gross PnL = 20 - 10 + 30 - 12 = 28.0
    assert fin["gross_pnl_usd"] == 28.0

    # Fees: 4 trades * ~2500 * 0.1 * 2 * 0.0004 = ~0.80 total fees
    assert fin["modeled_fees_usd"] > 0.0
    assert fin["modeled_slippage_usd"] == 0.40  # 4 * 0.10
    # Net PnL must be strictly less than gross PnL
    assert fin["net_realized_pnl_usd"] < fin["gross_pnl_usd"]
    assert fin["net_expectancy_usd"] == round(fin["net_realized_pnl_usd"] / 4.0, 2)


# =========================================================================
# 3. Markdown and CSV Export Integrity (Rule 195)
# =========================================================================
def test_export_paper_run_summary_markdown_and_csv():
    """
    Rule 195: Summary report formats into clean Markdown table and CSV export.
    """
    summary = build_cg01_paper_run_summary(
        run_id="run_export_test",
        synthetic_trades=[],
    )

    md = export_paper_run_summary_markdown(summary)
    assert "# CG-01 Paper Run Summary: `run_export_test`" in md
    assert "## 1. Post-Fee Financial Performance" in md
    assert "## 2. Latency Metrics" in md
    assert "## 3. Opportunity Funnel" in md
    assert "COLLECTING" in md

    csv_data = export_paper_run_summary_csv(summary)
    rows = csv_data.strip().split("\n")
    assert len(rows) == 2
    assert "run_id,symbol,status,closed_trades" in rows[0]
    assert "run_export_test,XAUUSDT" in rows[1]
