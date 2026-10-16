"""
backend/lab/cg01_replay_execution_adapter.py
V16.5 — Authoritative Shared Execution Adapter for CG-01 Replay and Lab.

Rules 081 - 100, 121 - 135:
Orchestrates data, clock, and execution services for replay without inventing a second engine.
Never executes manual position mutations or ad-hoc PnL formulas in lab drivers.
Authority resides in ExecutionCoordinator, PaperBroker, TradeLifecycleService, and persisted DB models.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy.orm import Session

import crud
import models
from domain_calculator import CostAssumptions, calculate_risk_reward
from paper_broker import PaperBroker
from services.clock import ReplayClock
from services.cg01_types import CG01Config, resolve_slippage_usd
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier, ScalpMode
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator

logger = logging.getLogger("cg01_replay_execution_adapter")


class CG01ReplayExecutionAdapter:
    """
    Adapter orchestrating real ScalpWatcherOrchestrator, ExecutionCoordinator,
    and TradeLifecycleService against an isolated lab SQLite database.
    """
    def __init__(
        self,
        db: Session,
        clock: ReplayClock,
        config: Optional[CG01Config] = None,
        costs: Optional[CostAssumptions] = None,
        initial_capital: float = 1000.0,
        risk_pct: float = 0.25,
        run_id: Optional[str] = None,
    ):
        self.db = db
        self.clock = clock
        self.config = config or CG01Config(enabled=True, profile_mode="PAPER_EXECUTION")
        self.costs = costs or CostAssumptions()
        self.initial_capital = initial_capital
        self.risk_pct = risk_pct
        self.run_id = run_id or f"run_{self.clock.now_ms()}"
        self.orchestrator = ScalpWatcherOrchestrator()
        self.coordinator = ExecutionCoordinator()
        self.slippage = resolve_slippage_usd(getattr(self.costs, "slippage_usd", None), default=0.10)

        # Seed initial DayAudit if none exists
        crud.get_or_create_today_audit(self.db, clock=self.clock)

    def on_bar_close(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Rule 085: Advance clock and trigger orchestrator.evaluate_cycle.
        Does not mutate PaperOrder directly.
        """
        now_ms = int(event["as_of_ms"])
        self.clock.set_time(now_ms)

        m1_cfg = M1ScalpConfig(
            scalp_m1_mode="PAPER",
            execution_profile="CG01",
            cg01_enabled=bool(self.config.enabled),
            cg01_profile_mode=self.config.profile_mode.value if hasattr(self.config.profile_mode, "value") else str(self.config.profile_mode),
            daily_max_cap=3,
            daily_min_goal=2,
            min_net_rr=getattr(self.config, "min_net_rr", 2.0),
            fallback_risk_pct=self.risk_pct,
            cg01_config=self.config,
        )

        res = self.orchestrator.evaluate_cycle(
            db=self.db,
            symbol=event.get("symbol", "XAUUSDT"),
            now_ms=now_ms,
            clock=self.clock,
            quote=event.get("quote"),
            candles_1m=event.get("candles_1m"),
            candles_15m=event.get("candles_15m"),
            candles_1h=event.get("candles_1h"),
            candles_4h=event.get("candles_4h"),
            config=m1_cfg,
            auto_enabled=True,
        )
        return res

    def on_quote(self, event: Dict[str, Any]) -> None:
        """
        Rule 086, 091, 092: Process price tick via PaperBroker exit tick and ExecutionCoordinator.
        Ensures exact event ordering: exit processor for open positions, coordinator for pending entries.
        """
        now_ms = int(event["timestamp"])
        self.clock.set_time(now_ms)

        bid = float(event["bid"])
        ask = float(event["ask"])
        high = float(event.get("high", max(bid, ask)))
        low = float(event.get("low", min(bid, ask)))
        bar_open = event.get("bar_open_time")
        bar_dur = int(event.get("bar_duration_ms", 60_000))

        # 1. Exit processor for open positions (Rule 092)
        PaperBroker.process_price_tick(
            db=self.db,
            current_bid=bid,
            current_ask=ask,
            candle_high=high,
            candle_low=low,
            candle_timestamp=bar_open,
            bar_duration_ms=bar_dur,
            now_ms=now_ms,
            clock=self.clock,
            session_tag="REPLAY",
            tick_timestamp=now_ms,
        )

        # 2. Coordinator for pending entry execution and expiry housekeeping (Rule 091)
        quote_dict = {
            "bid": bid,
            "ask": ask,
            "last": (bid + ask) / 2.0,
            "timestamp": now_ms,
        }
        self.coordinator.evaluate_orders_sync(
            db=self.db,
            ticker_override=quote_dict,
            clock=self.clock,
            now_ms=now_ms,
            slippage=self.slippage,
        )

    def collect_state(self) -> Dict[str, Any]:
        """
        Rule 087, 121 - 135: Read authoritative orders, audit, events from isolated lab DB.
        Pure projection, idempotent, read-only.
        """
        all_orders = self.db.query(models.PaperOrder).all()

        closed_orders = [o for o in all_orders if o.state in ("closed", "paper_closed")]
        open_orders = [o for o in all_orders if o.state == "paper_open"]
        pending_orders = [o for o in all_orders if o.state == "armed"]
        expired_orders = [o for o in all_orders if o.state == "expired"]
        rejected_orders = [o for o in all_orders if o.state == "rejected"]

        closed_trades_count = len(closed_orders)
        open_positions_count = len(open_orders)
        pending_orders_count = len(pending_orders)
        total_fills = closed_trades_count + open_positions_count

        closed_trades: List[Dict[str, Any]] = []
        for o in closed_orders:
            actual_entry = o.actual_entry or o.planned_entry
            net_pnl = float(o.realized_pnl_net if o.realized_pnl_net is not None else 0.0)
            exit_cause = str(o.exit_cause or "CLOSED")
            if "TP" in exit_cause:
                outcome = "TP"
            elif "SL" in exit_cause:
                outcome = "SL"
            else:
                outcome = exit_cause

            closed_trades.append({
                "trade_id": o.id,
                "direction": o.direction,
                "entry_time": o.opened_at,
                "entry_price": actual_entry,
                "exit_time": o.closed_at,
                "exit_price": o.actual_exit or actual_entry,
                "outcome": outcome,
                "exit_reason": exit_cause,
                "quantity": o.quantity,
                "net_pnl_usd": net_pnl,
                "realized_r": o.realized_r,
                "initial_risk_usdt": o.initial_risk_usdt,
            })

        open_position: Optional[Dict[str, Any]] = None
        if open_orders:
            op = open_orders[0]
            open_position = {
                "trade_id": op.id,
                "direction": op.direction,
                "entry_time": op.opened_at,
                "entry_price": op.actual_entry or op.planned_entry,
                "stop_loss": op.stop_loss,
                "take_profit": op.take_profit,
                "quantity": op.quantity,
                "initial_risk_usdt": op.initial_risk_usdt,
            }

        pending_order: Optional[Dict[str, Any]] = None
        if pending_orders:
            po = pending_orders[0]
            pending_order = {
                "trade_id": po.id,
                "direction": po.direction,
                "planned_entry": po.planned_entry,
                "stop_loss": po.stop_loss,
                "take_profit": po.take_profit,
                "valid_until_ms": po.expires_at,
            }

        # Accounting: Total PnL, Winrate (Rule 131), and Drawdown (Rule 129)
        total_pnl = round(sum(t["net_pnl_usd"] for t in closed_trades), 2) if closed_trades else None
        wins = sum(1 for t in closed_trades if t.get("net_pnl_usd", 0.0) > 0.0)
        winrate_pct = round((wins / closed_trades_count) * 100.0, 2) if closed_trades_count > 0 else None
        final_cash = round(self.initial_capital + (sum(t["net_pnl_usd"] for t in closed_trades) if closed_trades else 0.0), 2)

        # Drawdown calculation over closed trade series
        max_dd = 0.0
        if closed_trades:
            running_equity = self.initial_capital
            peak_equity = self.initial_capital
            for t in closed_trades:
                running_equity += t["net_pnl_usd"]
                if running_equity > peak_equity:
                    peak_equity = running_equity
                if peak_equity > 0:
                    dd = ((peak_equity - running_equity) / peak_equity) * 100.0
                    if dd > max_dd:
                        max_dd = dd
            max_drawdown_pct = round(max_dd, 2)
        else:
            max_drawdown_pct = None

        return {
            "status": "COMPLETED",
            "run_id": self.run_id,
            "total_fills": total_fills,
            "closed_trades_count": closed_trades_count,
            "open_positions_count": open_positions_count,
            "pending_orders_count": pending_orders_count,
            "expired_orders_count": len(expired_orders),
            "rejected_orders_count": len(rejected_orders),
            "closed_trades": closed_trades,
            "open_position": open_position,
            "pending_order": pending_order,
            "metrics": {
                "initial_capital": self.initial_capital,
                "final_cash": final_cash,
                "total_pnl_usd": total_pnl,
                "winrate_pct": winrate_pct,
                "max_drawdown_pct": max_drawdown_pct,
                "total_trades": closed_trades_count,
            },
        }

    def finalize_run(self) -> None:
        """Rule 088: Cleanup lab resources."""
        try:
            self.db.close()
        except Exception:
            pass
