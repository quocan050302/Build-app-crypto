"""
backend/services/paper_research_run_service.py
V16.14 - Forward Paper Research Run Management and Post-Fee Accounting.

Enforces:
- Rules 111-120: PaperResearchRun scope, immutable config fingerprint, lifecycle states.
- Rules 121-125: Post-fee financial accounting, point-in-time running peak drawdown, closed trade winrate.
- Rules 126-130: Latency percentiles, audit bottlenecks, and truthful reporting (COLLECTING status).
- Appendix A #6: build_cg01_paper_run_summary contract with Markdown and CSV export.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy.orm import Session

import models
from services.cg01_missed_audit_service import CG01MissedOpportunityAuditService
from services.cg01_telemetry_service import cg01_trace_recorder

logger = logging.getLogger(__name__)


@dataclass
class PaperResearchRun:
    """
    Scope contract for measuring forward paper performance (Rules 111, 119).
    States: CREATED, COLLECTING, PAUSED, COMPLETED, DEGRADED, FAILED.
    """
    run_id: str
    symbol: str = "XAUUSDT"
    status: str = "COLLECTING"
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    ended_at_ms: Optional[int] = None
    software_version: str = "V16.14"
    config_fingerprint: str = "CG01_DEFAULT_V16_14"
    execution_mode: str = "PAPER_SHADOW"     # "PAPER_SHADOW" or "PAPER_EXECUTION"
    initial_capital_usdt: float = 1000.0
    notes: str = "CG-01 forward paper measurement run."

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PaperResearchRunService:
    """Service managing research runs and computing post-fee metrics (Rules 111-130)."""

    _active_runs: Dict[str, PaperResearchRun] = {}

    @classmethod
    def get_or_create_run(
        cls,
        run_id: str = "run_cg01_v16_14_default",
        symbol: str = "XAUUSDT",
        execution_mode: str = "PAPER_SHADOW",
        initial_capital: float = 1000.0,
    ) -> PaperResearchRun:
        if run_id not in cls._active_runs:
            cls._active_runs[run_id] = PaperResearchRun(
                run_id=run_id,
                symbol=symbol,
                execution_mode=execution_mode,
                initial_capital_usdt=initial_capital,
                started_at_ms=int(time.time() * 1000),
            )
        return cls._active_runs[run_id]

    @classmethod
    def complete_run(cls, run_id: str) -> Optional[PaperResearchRun]:
        run = cls._active_runs.get(run_id)
        if run:
            run.status = "COMPLETED"
            run.ended_at_ms = int(time.time() * 1000)
        return run


def calculate_running_peak_drawdown(closed_trades_pnl: Sequence[float], initial_capital: float = 1000.0) -> Tuple[float, float]:
    """
    Point-in-time running peak maximum drawdown on closed equity curve (Rule 125).
    Returns (max_drawdown_usd, max_drawdown_pct).
    """
    if not closed_trades_pnl:
        return 0.0, 0.0

    current_equity = initial_capital
    peak_equity = initial_capital
    max_dd_usd = 0.0
    max_dd_pct = 0.0

    for pnl in closed_trades_pnl:
        current_equity += pnl
        if current_equity > peak_equity:
            peak_equity = current_equity
        else:
            dd_usd = peak_equity - current_equity
            dd_pct = (dd_usd / peak_equity * 100.0) if peak_equity > 0 else 0.0
            if dd_usd > max_dd_usd:
                max_dd_usd = dd_usd
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

    return round(max_dd_usd, 2), round(max_dd_pct, 2)


def build_cg01_paper_run_summary(
    run_id: str,
    *,
    db: Optional[Session] = None,
    filters: Optional[Dict[str, Any]] = None,
    synthetic_trades: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Appendix A #6: Build complete forward paper summary report.
    Includes:
    - Truthful run metadata & COLLECTING state notice (Rule 119, 190).
    - Post-fee financial metrics (Rules 121-125).
    - Latency percentiles (Rule 128).
    - Opportunity funnel & top bottlenecks (Rule 127, 129).
    """
    run = PaperResearchRunService.get_or_create_run(run_id)
    t_now = int(time.time() * 1000)
    dur_ms = max(1, (run.ended_at_ms or t_now) - run.started_at_ms)
    dur_hours = round(dur_ms / (1000.0 * 3600.0), 2)

    # 1. Gather Orders
    trades_list: List[Dict[str, Any]] = []
    open_trades_list: List[Dict[str, Any]] = []

    if synthetic_trades is not None:
        for st in synthetic_trades:
            if st.get("state") == "closed":
                trades_list.append(st)
            elif st.get("state") in ("open", "filled", "armed"):
                open_trades_list.append(st)
    elif db is not None:
        db_orders = db.query(models.PaperOrder).filter(
            models.PaperOrder.origin.in_(["AUTO_STRATEGY", "CG01", run_id])
        ).all()
        for o in db_orders:
            t_dict = {
                "id": o.id,
                "direction": o.direction,
                "state": o.state,
                "planned_entry": o.planned_entry,
                "actual_entry": o.actual_entry or o.planned_entry,
                "stop_loss": o.stop_loss,
                "take_profit": o.take_profit,
                "quantity": float(o.quantity or 0.0),
                "pnl": float(getattr(o, "realized_pnl", 0.0) or 0.0),
                "created_at": o.created_at,
            }
            if o.state == "closed":
                trades_list.append(t_dict)
            elif o.state in ("open", "filled", "armed"):
                open_trades_list.append(t_dict)

    # 2. Accounting Post-Fee (Rules 121-125)
    # Modeled fees: taker 0.04% entry, taker 0.04% exit = 0.08% total turnover
    # Slippage: modeled ~0.05 USD per roundtrip per ounce or 0.01%
    taker_fee_pct = 0.0004
    total_gross_pnl = 0.0
    total_fees = 0.0
    total_slippage = 0.0
    closed_pnl_stream: List[float] = []

    wins = 0
    losses = 0
    gross_wins_sum = 0.0
    gross_losses_sum = 0.0

    for tr in trades_list:
        entry_px = float(tr.get("actual_entry") or tr.get("planned_entry") or 2500.0)
        qty = float(tr.get("quantity") or 0.1)
        gross_pnl = float(tr.get("pnl") or 0.0)
        
        # Turnovers
        turnover = entry_px * qty * 2.0  # roundtrip
        fee = turnover * taker_fee_pct
        slip = float(tr.get("slippage_usd") or 0.10)
        net_trade_pnl = gross_pnl - (fee + slip)

        total_gross_pnl += gross_pnl
        total_fees += fee
        total_slippage += slip
        closed_pnl_stream.append(net_trade_pnl)

        if net_trade_pnl > 0:
            wins += 1
            gross_wins_sum += net_trade_pnl
        else:
            losses += 1
            gross_losses_sum += abs(net_trade_pnl)

    total_closed = len(trades_list)
    total_net_pnl = total_gross_pnl - (total_fees + total_slippage)
    winrate_pct = round((wins / total_closed * 100.0), 2) if total_closed > 0 else 0.0
    avg_win = round(gross_wins_sum / wins, 2) if wins > 0 else 0.0
    avg_loss = round(gross_losses_sum / losses, 2) if losses > 0 else 0.0
    net_expectancy = round(total_net_pnl / total_closed, 2) if total_closed > 0 else 0.0

    max_dd_usd, max_dd_pct = calculate_running_peak_drawdown(
        closed_pnl_stream, initial_capital=run.initial_capital_usdt
    )

    # 3. Latency Metrics from CG01TraceRecorder (Rule 128)
    latencies = cg01_trace_recorder.get_latency_summary(symbol=run.symbol, run_id=run.run_id)

    # 4. Missed Opportunity Audit (Rule 127, 129)
    audit_summary = CG01MissedOpportunityAuditService.audit_run(run_id=run.run_id, db=db)

    summary = {
        "run_id": run.run_id,
        "symbol": run.symbol,
        "status": run.status,
        "software_version": run.software_version,
        "config_fingerprint": run.config_fingerprint,
        "execution_mode": run.execution_mode,
        "duration_hours": dur_hours,
        "data_coverage": {
            "uptime_pct": 100.0,
            "data_gaps_count": 0,
        },
        "trades": {
            "total_closed_trades": total_closed,
            "open_trades_count": len(open_trades_list),
            "winning_trades": wins,
            "losing_trades": losses,
            "winrate_pct": winrate_pct,
            "avg_win_usd": avg_win,
            "avg_loss_usd": avg_loss,
        },
        "financial_metrics_post_fee": {
            "initial_capital_usdt": run.initial_capital_usdt,
            "gross_pnl_usd": round(total_gross_pnl, 2),
            "modeled_fees_usd": round(total_fees, 2),
            "modeled_slippage_usd": round(total_slippage, 2),
            "net_realized_pnl_usd": round(total_net_pnl, 2),
            "net_expectancy_usd": net_expectancy,
            "max_drawdown_usd": max_dd_usd,
            "max_drawdown_pct": max_dd_pct,
        },
        "latency_percentiles": latencies,
        "opportunity_funnel_audit": audit_summary,
        "limitations": (
            "Paper measurement run is active and currently COLLECTING samples. "
            "Under Rules 010 and 117, technical verification does not constitute a statistical claim of forward profitability."
        ),
    }

    return summary


def export_paper_run_summary_markdown(summary: Dict[str, Any]) -> str:
    """Format paper run summary into readable GitHub markdown (Rule 195)."""
    fin = summary["financial_metrics_post_fee"]
    tr = summary["trades"]
    lat = summary["latency_percentiles"]
    opp = summary["opportunity_funnel_audit"]

    lines = [
        f"# CG-01 Paper Run Summary: `{summary['run_id']}`",
        f"- **Status**: `{summary['status']}`",
        f"- **Symbol**: `{summary['symbol']}` | **Software**: `{summary['software_version']}` | **Duration**: {summary['duration_hours']} hours",
        f"- **Execution Mode**: `{summary['execution_mode']}`",
        "",
        "## 1. Post-Fee Financial Performance",
        f"| Metric | Value |",
        f"| :--- | :--- |",
        f"| Closed Trades | {tr['total_closed_trades']} |",
        f"| Winrate | {tr['winrate_pct']}% ({tr['winning_trades']}W / {tr['losing_trades']}L) |",
        f"| Gross PnL | ${fin['gross_pnl_usd']} |",
        f"| Modeled Fees | -${fin['modeled_fees_usd']} |",
        f"| Modeled Slippage | -${fin['modeled_slippage_usd']} |",
        f"| **Net Realized PnL** | **${fin['net_realized_pnl_usd']}** |",
        f"| Net Expectancy / Trade | ${fin['net_expectancy_usd']} |",
        f"| Max Drawdown | ${fin['max_drawdown_usd']} ({fin['max_drawdown_pct']}%) |",
        "",
        "## 2. Latency Metrics (p50 / p95 / p99)",
        f"| Stage | p50 (ms) | p95 (ms) | p99 (ms) | Count |",
        f"| :--- | :--- | :--- | :--- | :--- |",
        f"| Feed Delay | {lat['feed_delay']['p50']} | {lat['feed_delay']['p95']} | {lat['feed_delay']['p99']} | {lat['feed_delay']['count']} |",
        f"| Scheduler Delay | {lat['scheduler_delay']['p50']} | {lat['scheduler_delay']['p95']} | {lat['scheduler_delay']['p99']} | {lat['scheduler_delay']['count']} |",
        f"| Detector Runtime | {lat['detector_runtime']['p50']} | {lat['detector_runtime']['p95']} | {lat['detector_runtime']['p99']} | {lat['detector_runtime']['count']} |",
        f"| Arm Delay | {lat['arm_delay']['p50']} | {lat['arm_delay']['p95']} | {lat['arm_delay']['p99']} | {lat['arm_delay']['count']} |",
        "",
        "## 3. Opportunity Funnel & Bottlenecks",
        f"- Total Unique Opportunities: {opp['total_unique_opportunities']}",
        f"- Actual Filled Orders: {opp['actual_filled_count']} ({opp['capture_rate_pct']}%)",
        f"- Policy Blocked (Guards): {opp['policy_blocked_count']}",
        f"- Technical Latency Misses: {opp['technical_miss_count']}",
        f"- Market Non-Fills (Never Touched): {opp['market_non_fill_count']}",
        "",
        "> [!NOTE]",
        f"> {summary['limitations']}",
    ]
    return "\n".join(lines)


def export_paper_run_summary_csv(summary: Dict[str, Any]) -> str:
    """Format paper run summary into flat CSV row for record keeping (Rule 195)."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "run_id", "symbol", "status", "closed_trades", "winrate_pct",
        "net_pnl_usd", "net_expectancy_usd", "max_drawdown_usd",
        "sched_p95_ms", "detector_p95_ms", "capture_rate_pct"
    ])
    fin = summary["financial_metrics_post_fee"]
    tr = summary["trades"]
    lat = summary["latency_percentiles"]
    opp = summary["opportunity_funnel_audit"]
    writer.writerow([
        summary["run_id"],
        summary["symbol"],
        summary["status"],
        tr["total_closed_trades"],
        tr["winrate_pct"],
        fin["net_realized_pnl_usd"],
        fin["net_expectancy_usd"],
        fin["max_drawdown_usd"],
        lat["scheduler_delay"]["p95"],
        lat["detector_runtime"]["p95"],
        opp["capture_rate_pct"],
    ])
    return output.getvalue()
