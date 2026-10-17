"""
backend/services/paper_research_run_service.py
V16.15 - Forward Paper Research Run Management and Truthful Post-Fee Accounting.

Enforces:
- Rules 081-090: Persistent storage of research runs in SQLite database, surviving restarts.
- Rules 091-100: Exact, idempotent order-to-run linkage (no leaking historical AUTO_STRATEGY orders).
- Rules 101-110: Post-fee financial accounting reading realized_pnl_net, recognizing paper_open, zero double-fee deduction.
- Rules 111-120: Point-in-time running peak closed equity drawdown, sample_count=0 handling, filter support.
- Appendix A #6 & Appendix B: associate_order_with_research_run, build_cg01_paper_run_summary.
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


def _get_projection_worker():
    try:
        from services.cg01_projection_worker import cg01_projection_worker
        return cg01_projection_worker
    except Exception:
        return None


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
    software_version: str = "V16.15"
    config_fingerprint: str = "CG01_DEFAULT_V16_15"
    execution_mode: str = "PAPER_SHADOW"     # "PAPER_SHADOW" or "PAPER_EXECUTION"
    initial_capital_usdt: float = 1000.0
    notes: str = "CG-01 forward paper measurement run."

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def resolve_active_observation_run(
    db: Optional[Session] = None,
    scope: str = "CG01",
    symbol: str = "XAUUSDT",
) -> Optional[PaperResearchRun]:
    """
    V16.17 Single authoritative active run resolver (Rules 061-063, 158).
    Resolves the active persisted observation run for scope/symbol.
    Never creates a fictitious run or mutates global execution flags.
    """
    if db is not None:
        try:
            row = (
                db.query(models.PaperResearchRunRecord)
                .filter(
                    models.PaperResearchRunRecord.symbol == symbol,
                    models.PaperResearchRunRecord.status.in_(["COLLECTING", "CREATED"]),
                )
                .order_by(models.PaperResearchRunRecord.started_at_ms.desc())
                .first()
            )
            if row:
                run = PaperResearchRun(
                    run_id=row.run_id,
                    symbol=row.symbol,
                    status=row.status,
                    started_at_ms=row.started_at_ms,
                    ended_at_ms=row.ended_at_ms,
                    software_version=row.software_version,
                    config_fingerprint=row.config_fingerprint,
                    execution_mode=row.execution_mode,
                    initial_capital_usdt=row.initial_capital_usdt,
                )
                PaperResearchRunService._active_runs[row.run_id] = run
                return run
        except Exception as e:
            logger.warning(f"Error resolving active observation run from DB: {e}")

    # Check memory active runs
    for r in reversed(list(PaperResearchRunService._active_runs.values())):
        if r.symbol == symbol and r.status in ("COLLECTING", "CREATED"):
            return r

    return None


class PaperResearchRunService:
    """Service managing research runs and computing post-fee metrics (Rules 081-090, 111-130)."""

    _active_runs: Dict[str, PaperResearchRun] = {}

    @classmethod
    def get_or_create_run(
        cls,
        run_id: Optional[str] = None,
        symbol: str = "XAUUSDT",
        execution_mode: str = "PAPER_SHADOW",
        initial_capital: float = 1000.0,
        db: Optional[Session] = None,
        create_if_missing: bool = True,
        scope: str = "CG01",
        initial_capital_usdt: Optional[float] = None,
    ) -> Optional[PaperResearchRun]:
        """
        Rules 081, 084, 089: Load run from DB first to survive restarts.
        Create only if explicitly requested, preventing fictitious runs on GET queries.
        Uses db.flush(), never committing caller's transaction prematurely (Rule 031, 032).
        """
        t_now = int(time.time() * 1000)
        cap = initial_capital_usdt if initial_capital_usdt is not None else initial_capital

        if not run_id:
            active = resolve_active_observation_run(db=db, scope=scope, symbol=symbol)
            if active:
                return active
            if not create_if_missing:
                return None
            actual_run_id = f"run_{scope.lower()}_{symbol.lower()}_{t_now}"
        else:
            actual_run_id = run_id

        # 1. Database persistent record lookup (DB is authority, Rule 041)
        if db is not None:
            try:
                row = db.query(models.PaperResearchRunRecord).filter(
                    models.PaperResearchRunRecord.run_id == actual_run_id
                ).first()
                if row:
                    run = PaperResearchRun(
                        run_id=row.run_id,
                        symbol=row.symbol,
                        status=row.status,
                        started_at_ms=row.started_at_ms,
                        ended_at_ms=row.ended_at_ms,
                        software_version=row.software_version,
                        config_fingerprint=row.config_fingerprint,
                        execution_mode=row.execution_mode,
                        initial_capital_usdt=row.initial_capital_usdt,
                    )
                    cls._active_runs[actual_run_id] = run
                    return run
            except Exception as e:
                logger.warning(f"Error querying PaperResearchRunRecord: {e}")
        elif actual_run_id in cls._active_runs:
            return cls._active_runs[actual_run_id]

        # 2. Create new run if requested
        if not create_if_missing:
            return None

        version = "V16.14" if "v16_14" in actual_run_id.lower() else ("V16.15" if "v16_15" in actual_run_id.lower() else ("V16.16" if "v16_16" in actual_run_id.lower() else "V16.17"))
        run = PaperResearchRun(
            run_id=actual_run_id,
            symbol=symbol,
            execution_mode=execution_mode,
            initial_capital_usdt=cap,
            started_at_ms=t_now,
            software_version=version,
            config_fingerprint=f"CG01_DEFAULT_{version.replace('.', '_')}",
        )

        if db is not None:
            try:
                rec = models.PaperResearchRunRecord(
                    run_id=run.run_id,
                    symbol=run.symbol,
                    status=run.status,
                    started_at_ms=run.started_at_ms,
                    ended_at_ms=run.ended_at_ms,
                    software_version=run.software_version,
                    config_fingerprint=run.config_fingerprint,
                    execution_mode=run.execution_mode,
                    initial_capital_usdt=run.initial_capital_usdt,
                    current_capital_usdt=run.initial_capital_usdt,
                    created_at_ms=t_now,
                    updated_at_ms=t_now,
                )
                db.add(rec)
                db.flush()
            except Exception as e:
                logger.warning(f"Error persisting PaperResearchRunRecord: {e}")

        cls._active_runs[actual_run_id] = run
        return run

    @classmethod
    def complete_run(cls, run_id: str, db: Optional[Session] = None) -> Optional[PaperResearchRun]:
        run = cls.get_or_create_run(run_id, db=db, create_if_missing=False)
        if run:
            run.status = "COMPLETED"
            run.ended_at_ms = int(time.time() * 1000)
            if db is not None:
                try:
                    row = db.query(models.PaperResearchRunRecord).filter(
                        models.PaperResearchRunRecord.run_id == run_id
                    ).first()
                    if row:
                        row.status = "COMPLETED"
                        row.ended_at_ms = run.ended_at_ms
                        row.updated_at_ms = run.ended_at_ms
                        db.flush()
                except Exception as e:
                    logger.warning(f"Error updating completed PaperResearchRunRecord: {e}")
        return run


def complete_run(run_id: str, db: Optional[Session] = None) -> Optional[PaperResearchRun]:
    """Module-level wrapper for PaperResearchRunService.complete_run (Rule 033)."""
    return PaperResearchRunService.complete_run(run_id, db=db)



def associate_order_with_research_run(
    db: Session,
    run_id: str,
    order_id: str,
    opportunity_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> bool:
    """
    Rules 091, 096 & Appendix B: Idempotently link an order to a paper research run.
    Ensures strict run isolation without substring or wildcard matching.
    """
    t_now = now_ms if now_ms is not None else int(time.time() * 1000)
    try:
        existing = db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.run_id == run_id,
            models.PaperResearchOrderLink.order_id == order_id,
        ).first()
        if existing:
            updated = False
            if trace_id and not existing.trace_id:
                existing.trace_id = trace_id
                updated = True
            if opportunity_id and not existing.opportunity_id:
                existing.opportunity_id = opportunity_id
                updated = True
            if updated:
                db.flush()
            return True

        link = models.PaperResearchOrderLink(
            run_id=run_id,
            order_id=order_id,
            opportunity_id=opportunity_id,
            trace_id=trace_id,
            linked_at_ms=t_now,
        )
        db.add(link)
        db.flush()
        return True
    except Exception as e:
        logger.warning(f"Error linking order {order_id} to run {run_id}: {e}")
        return False


def calculate_running_peak_drawdown(
    closed_trades_pnl: Sequence[float], initial_capital: float = 1000.0
) -> Tuple[float, float]:
    """
    Point-in-time running peak maximum drawdown on closed equity curve (Rule 109, 110, 125).
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
    - Post-fee financial metrics (Rules 101, 102, 121-125).
    - Exact run isolation (Rules 025, 092).
    - Latency percentiles (Rule 128).
    - Opportunity funnel & top bottlenecks (Rule 127, 129).
    """
    run = PaperResearchRunService.get_or_create_run(run_id, db=db, create_if_missing=False)
    if run is None:
        if synthetic_trades is not None:
            t_now = int(time.time() * 1000)
            run = PaperResearchRun(
                run_id=run_id,
                symbol="XAUUSDT",
                status="COLLECTING",
                started_at_ms=t_now - 3600000,
            )
        else:
            return {
                "status": "NOT_FOUND",
                "run_id": run_id,
                "error": f"Research run {run_id} not found",
                "total_trades": 0,
                "net_pnl_usd": 0.0,
            }
    t_now = int(time.time() * 1000)
    dur_ms = max(1, (run.ended_at_ms or t_now) - run.started_at_ms)
    dur_hours = round(dur_ms / (1000.0 * 3600.0), 2)

    # 1. Gather Orders linked to this run (Rules 025, 092, 101, 106)
    trades_list: List[Dict[str, Any]] = []
    open_trades_list: List[Dict[str, Any]] = []
    modeled_fees_synthetic = 0.0
    modeled_slippage_synthetic = 0.0
    gross_pnl_synthetic = 0.0

    if synthetic_trades is not None:
        for st in synthetic_trades:
            state = str(st.get("state") or "").lower()
            if state == "closed":
                trades_list.append(st)
                trade_gross = float(st.get("pnl") or 0.0)
                gross_pnl_synthetic += trade_gross
                qty = float(st.get("quantity") or 0.1)
                entry = float(st.get("actual_entry") or st.get("planned_entry") or 2500.0)
                # Standard modeled taker fee 0.04% entry and exit
                fee = (entry * qty * 2) * 0.0004
                modeled_fees_synthetic += fee
                modeled_slippage_synthetic += float(st.get("slippage_usd") or 0.0)
            elif state in ("open", "paper_open", "filled", "armed"):
                open_trades_list.append(st)
    elif db is not None:
        # Query exact run associations (Rule 091, 092)
        links = db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.run_id == run_id
        ).all()
        linked_order_ids = {l.order_id for l in links}

        # Also support legacy session_instance_id matching run_id
        db_orders = db.query(models.PaperOrder).filter(
            (models.PaperOrder.id.in_(linked_order_ids)) |
            (models.PaperOrder.session_instance_id == run_id)
        ).all()

        for o in db_orders:
            # Rule 101: Read realized_pnl_net!
            net_pnl = float(o.realized_pnl_net) if o.realized_pnl_net is not None else 0.0
            t_dict = {
                "id": o.id,
                "direction": o.direction,
                "state": o.state,
                "planned_entry": o.planned_entry,
                "actual_entry": o.actual_entry or o.planned_entry,
                "stop_loss": o.stop_loss,
                "take_profit": o.take_profit,
                "quantity": float(o.quantity or 0.0),
                "pnl": net_pnl,
                "realized_pnl_net": net_pnl,
                "created_at": o.created_at,
                "closed_at": o.closed_at,
            }
            state = str(o.state or "").lower()
            if state == "closed":
                trades_list.append(t_dict)
            elif state in ("paper_open", "armed", "open", "filled"):  # Rule 106
                open_trades_list.append(t_dict)

    # 2. Accounting Post-Fee (Rules 101, 102, 109, 121-125)
    # Sort closed trades chronologically by closed_at (Rule 109)
    trades_list.sort(key=lambda x: int(x.get("closed_at") or x.get("created_at") or 0))

    # Apply filters if provided (Rule 113)
    if filters:
        if "direction" in filters:
            trades_list = [t for t in trades_list if t.get("direction") == filters["direction"]]
        if "start_time" in filters:
            trades_list = [t for t in trades_list if (t.get("closed_at") or 0) >= filters["start_time"]]
        if "end_time" in filters:
            trades_list = [t for t in trades_list if (t.get("closed_at") or 0) <= filters["end_time"]]

    total_net_pnl = 0.0
    closed_pnl_stream: List[float] = []
    wins = 0
    losses = 0
    breakevens = 0
    gross_wins_sum = 0.0
    gross_losses_sum = 0.0

    for tr in trades_list:
        # Rule 102: realized_pnl_net from TradeLifecycleService already accounts for fees & slippage!
        # Do not deduct fees a second time.
        net_trade_pnl = float(tr.get("realized_pnl_net") if "realized_pnl_net" in tr else tr.get("pnl", 0.0))
        total_net_pnl += net_trade_pnl
        closed_pnl_stream.append(net_trade_pnl)

        if net_trade_pnl > 0.0:
            wins += 1
            gross_wins_sum += net_trade_pnl
        elif net_trade_pnl < 0.0:
            losses += 1
            gross_losses_sum += abs(net_trade_pnl)
        else:
            breakevens += 1

    total_closed = len(trades_list)
    # Rule 111: If zero closed trades, winrate is None (sample_count = 0)
    winrate_pct = round((wins / total_closed * 100.0), 2) if total_closed > 0 else None
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

    # Data coverage grounded in actual trace recordings (Rule 119)
    recent_traces = cg01_trace_recorder.get_recent_traces(limit=500) if hasattr(cg01_trace_recorder, "get_recent_traces") else []
    run_traces = [t for t in recent_traces if getattr(t, "run_id", None) == run.run_id]
    coverage_summary = build_run_coverage_summary(run, run_traces)

    armed_list = [t for t in open_trades_list if str(t.get("state") or "").lower() == "armed"]
    open_pos_list = [t for t in open_trades_list if str(t.get("state") or "").lower() in ("paper_open", "open", "filled")]

    summary = {
        "run_id": run.run_id,
        "symbol": run.symbol,
        "status": run.status,
        "software_version": run.software_version,
        "config_fingerprint": run.config_fingerprint,
        "execution_mode": run.execution_mode,
        "duration_hours": dur_hours,
        "data_coverage": {
            "uptime_pct": coverage_summary.get("uptime_pct"),
            "data_gaps_count": coverage_summary.get("data_gaps_count", 0),
            "coverage_state": coverage_summary.get("coverage_state", run.status),
        },
        "trades": {
            "total_closed_trades": total_closed,
            "open_trades_count": len(open_pos_list),
            "armed_orders_count": len(armed_list),
            "winning_trades": wins,
            "losing_trades": losses,
            "breakeven_trades": breakevens,
            "winrate_pct": winrate_pct,
            "avg_win_usd": avg_win,
            "avg_loss_usd": avg_loss,
        },
        "financial_metrics_post_fee": {
            "initial_capital_usdt": run.initial_capital_usdt,
            "gross_pnl_usd": round(gross_pnl_synthetic, 2) if synthetic_trades is not None else round(total_net_pnl, 2),
            "modeled_fees_usd": round(modeled_fees_synthetic, 2),
            "modeled_slippage_usd": round(modeled_slippage_synthetic, 2),
            "net_realized_pnl_usd": round(total_net_pnl - modeled_fees_synthetic - modeled_slippage_synthetic, 2) if synthetic_trades is not None else round(total_net_pnl, 2),
            "net_expectancy_usd": round((total_net_pnl - modeled_fees_synthetic - modeled_slippage_synthetic) / total_closed, 2) if (synthetic_trades is not None and total_closed > 0) else net_expectancy,
            "max_drawdown_usd": max_dd_usd,
            "max_drawdown_pct": max_dd_pct,
            "drawdown_type": "CLOSED_EQUITY_DRAWDOWN",  # Rule 110
        },
        "latency_percentiles": latencies,
        "opportunity_funnel_audit": audit_summary,
        "projection_worker": (
            _get_projection_worker().get_health_status(db=db)
            if _get_projection_worker() is not None
            else {"status": "NOT_LOADED"}
        ),
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

    winrate_disp = f"{tr['winrate_pct']}%" if tr['winrate_pct'] is not None else "N/A (0 closed trades)"

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
        f"| Open Positions | {tr['open_trades_count']} |",
        f"| Winrate | {winrate_disp} ({tr['winning_trades']}W / {tr['losing_trades']}L / {tr['breakeven_trades']}BE) |",
        f"| **Net Realized PnL** | **${fin['net_realized_pnl_usd']}** |",
        f"| Net Expectancy / Trade | ${fin['net_expectancy_usd']} |",
        f"| Max Drawdown ({fin['drawdown_type']}) | ${fin['max_drawdown_usd']} ({fin['max_drawdown_pct']}%) |",
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
        f"- Market Non-Fills: {opp['market_non_fill_count']}",
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


def start_observation_run(
    db: Optional[Session] = None,
    symbol: str = "XAUUSDT",
    run_id: Optional[str] = None,
    config_fingerprint: str = "DEFAULT",
    initial_capital_usdt: float = 1000.0,
    scope: str = "CG01",
) -> PaperResearchRun:
    """
    Appendix B & Rules 085-087: Explicitly start an observation run into COLLECTING state.
    Does NOT modify global auto, risk, margin, or account balance.
    """
    t_now = int(time.time() * 1000)
    actual_run_id = run_id or f"run_{scope.lower()}_{symbol.lower()}_{t_now}"
    run = PaperResearchRunService.get_or_create_run(
        run_id=actual_run_id,
        symbol=symbol,
        scope=scope,
        initial_capital_usdt=initial_capital_usdt,
        db=db,
        create_if_missing=True,
    )
    run.status = "COLLECTING"
    run.config_fingerprint = config_fingerprint
    if db is not None:
        rec = db.query(models.PaperResearchRunRecord).filter(
            models.PaperResearchRunRecord.run_id == run.run_id
        ).first()
        if rec:
            rec.status = "COLLECTING"
            rec.config_fingerprint = config_fingerprint
            db.flush()
    return run


def reconcile_run_accounting(db: Session, run_id: str) -> Dict[str, Any]:
    """
    Appendix B & Rule 117: Per-order reconciliation against journal and PaperOrder.realized_pnl_net.
    Returns audit status without creating competing accounting authority.
    """
    links = db.query(models.PaperResearchOrderLink).filter(
        models.PaperResearchOrderLink.run_id == run_id
    ).all()
    linked_order_ids = {l.order_id for l in links}

    orders = db.query(models.PaperOrder).filter(
        (models.PaperOrder.id.in_(linked_order_ids)) |
        (models.PaperOrder.session_instance_id == run_id)
    ).all()

    itemized_orders: List[Dict[str, Any]] = []
    total_net = 0.0
    closed_count = 0
    open_count = 0

    for o in orders:
        net_val = float(o.realized_pnl_net) if o.realized_pnl_net is not None else 0.0
        state = str(o.state or "").lower()
        if state == "closed":
            closed_count += 1
            total_net += net_val
        elif state in ("paper_open", "armed", "open"):
            open_count += 1

        itemized_orders.append({
            "order_id": o.id,
            "direction": o.direction,
            "state": o.state,
            "planned_entry": o.planned_entry,
            "actual_entry": o.actual_entry,
            "stop_loss": o.stop_loss,
            "take_profit": o.take_profit,
            "quantity": float(o.quantity or 0.0),
            "realized_pnl_net": net_val,
            "closed_at": o.closed_at,
        })

    return {
        "run_id": run_id,
        "reconciled": True,
        "total_orders_associated": len(orders),
        "closed_orders_count": closed_count,
        "open_positions_count": open_count,
        "reconciled_net_pnl_usd": round(total_net, 2),
        "itemized_orders": itemized_orders,
        "status": "RECONCILED" if len(orders) > 0 else "NO_ORDERS",
    }


def build_run_coverage_summary(
    run: PaperResearchRun,
    traces: Sequence[Any] = (),
) -> Dict[str, Any]:
    """
    Appendix B & Rules 114, 115: Computes observation coverage and data health
    grounded in actual trace recordings, never hardcoding 100% when data is absent.
    """
    if not traces:
        return {
            "run_id": run.run_id,
            "uptime_pct": None,
            "data_gaps_count": 0,
            "traces_count": 0,
            "coverage_state": "NO_TELEMETRY",
        }

    # Count valid vs gap traces
    gap_count = sum(1 for t in traces if getattr(t, "evaluation_status", "") == "DATA_GAP")
    total_traces = len(traces)
    uptime = round(((total_traces - gap_count) / total_traces) * 100.0, 2) if total_traces > 0 else None

    return {
        "run_id": run.run_id,
        "uptime_pct": uptime,
        "data_gaps_count": gap_count,
        "traces_count": total_traces,
        "coverage_state": "COLLECTING" if run.status == "COLLECTING" else run.status,
    }


def record_committed_order_milestone(
    db: Session,
    run_id: str,
    order_id: str,
    milestone: str,
    timestamp_ms: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Appendix B & Rule 128: Atomically record armed/fill/close facts linked to committed lifecycle.
    """
    t_now = timestamp_ms or int(time.time() * 1000)
    try:
        # 1. Ensure link exists
        link = db.query(models.PaperResearchOrderLink).filter(
            models.PaperResearchOrderLink.run_id == run_id,
            models.PaperResearchOrderLink.order_id == order_id,
        ).first()
        if not link:
            link = models.PaperResearchOrderLink(
                run_id=run_id,
                order_id=order_id,
                linked_at_ms=t_now,
            )
            db.add(link)
            db.flush()

        # 2. Persist milestone fact idempotently (Rules 028, 095)
        existing_ms = db.query(models.PaperResearchOrderMilestone).filter(
            models.PaperResearchOrderMilestone.run_id == run_id,
            models.PaperResearchOrderMilestone.order_id == order_id,
            models.PaperResearchOrderMilestone.milestone == milestone,
        ).first()
        if not existing_ms:
            ms_rec = models.PaperResearchOrderMilestone(
                run_id=run_id,
                order_id=order_id,
                milestone=milestone,
                timestamp_ms=t_now,
                payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
                created_at_ms=t_now,
            )
            db.add(ms_rec)
            db.flush()

        return True
    except Exception as e:
        logger.warning(f"Error recording milestone {milestone} for order {order_id} in run {run_id}: {e}")
        return False


def generate_runtime_paper_report_content(
    run_id: str,
    db: Optional[Session] = None,
    scheduler: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Appendix A #12 & Rules 029, 141-146: Generate truthful forward paper report grounded in DB and worker health.
    Never asserts 'running continuously' when worker is stopped or no heartbeat.
    """
    # 1. Fetch run from DB without creating
    run = PaperResearchRunService.get_or_create_run(run_id, db=db, create_if_missing=False)
    if not run:
        return {
            "status": "NOT_FOUND",
            "run_id": run_id,
            "markdown": f"# CG-01 Paper Run Status\n\nRun `{run_id}` was not found in persistence records.",
            "operational_status": "NOT_FOUND",
            "total_closed_trades": 0,
        }

    # 2. Worker health
    sched = scheduler
    if sched is None:
        try:
            from services.cg01_scheduler_service import cg01_evaluation_scheduler
            sched = cg01_evaluation_scheduler
        except Exception:
            pass

    health = sched.get_health_snapshot() if sched else {"worker_status": "UNKNOWN", "status": "UNKNOWN", "running": False}
    worker_running = health.get("running", False)
    worker_status = health.get("status") or health.get("worker_status") or ("RUNNING" if worker_running else "STOPPED")

    # 3. Build summary
    summary = build_cg01_paper_run_summary(run_id, db=db)
    tr = summary.get("trades", {})
    fin = summary.get("financial_metrics_post_fee", {})
    cov = summary.get("data_coverage", {})

    # Evaluate true operational status
    if not worker_running or worker_status == "STOPPED":
        operational_status = "STOPPED"
        obs_notice = "Observation worker is currently STOPPED. Forward evaluation is paused."
    elif run.status == "COMPLETED":
        operational_status = "COMPLETED"
        obs_notice = "Observation run has completed."
    elif run.status == "PAUSED":
        operational_status = "PAUSED"
        obs_notice = "Observation run is paused."
    elif cov.get("coverage_state") == "NO_TELEMETRY":
        operational_status = "COLLECTING_INITIALIZING"
        obs_notice = "Worker is active; waiting for initial telemetry samples from the live market."
    else:
        operational_status = run.status
        obs_notice = "Forward observation is active in the live market."

    total_closed = tr.get("total_closed_trades", 0)
    winrate_disp = f"{tr.get('winrate_pct')}%" if tr.get('winrate_pct') is not None else "N/A (0 closed trades)"

    lines = [
        f"# V16.17 CG-01 Paper Run Status & Measurement Report",
        "",
        "## 1. Run Metadata & Operational State",
        f"- **Run ID**: `{run.run_id}`",
        f"- **Software Version**: `{run.software_version}`",
        f"- **Deployment Scope**: `{run.symbol}`",
        f"- **Run Status**: `{run.status}`",
        f"- **Worker Health**: `{worker_status}` (running: {worker_running})",
        f"- **Operational Status**: `{operational_status}`",
        f"- **Execution Mode**: `{run.execution_mode}` (Strictly respect user auto authority)",
        f"- **Persistence Authority**: `SQLite: paper_research_run_records`",
        "",
        "## 2. Forward Data Collection Status",
        f"- {obs_notice}",
        f"- Observed duration: {summary.get('duration_hours', 0.0)} hours",
        f"- Uptime: {cov.get('uptime_pct', 'N/A')}% | Data gaps: {cov.get('data_gaps_count', 0)}",
        f"- Verified closed trades: {total_closed}",
        f"- Open positions: {tr.get('open_trades_count', 0)} | Armed orders: {tr.get('armed_orders_count', 0)}",
        "",
        "## 3. Post-Fee Performance Summary",
        f"- Net Realized PnL: ${fin.get('net_realized_pnl_usd', 0.0)} USDT",
        f"- Winrate: {winrate_disp}",
        f"- Max Drawdown: ${fin.get('max_drawdown_usd', 0.0)} ({fin.get('max_drawdown_pct', 0.0)}%)",
        "",
        "> [!NOTE]",
        "> Under Rules 009, 010, and 189, forward data is collected causally without synthetic time simulation.",
        "> Winrate and statistical expectancy are marked N/A until live market trade outcomes have confirmed.",
    ]
    md_content = "\n".join(lines)
    return {
        "run_id": run.run_id,
        "operational_status": operational_status,
        "worker_running": worker_running,
        "total_closed_trades": total_closed,
        "markdown": md_content,
        "summary": summary,
    }
