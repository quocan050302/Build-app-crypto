"""
backend/lab/cg01_replay_execution_adapter.py
V16.6 — Authoritative Shared Execution Adapter for CG-01 Replay and Lab.

Rules 081 - 100, 121 - 135:
Orchestrates data, clock, and execution services for replay without inventing a second engine.
Never executes manual position mutations or ad-hoc PnL formulas in lab drivers.
Authority resides in ExecutionCoordinator, PaperBroker, TradeLifecycleService, and persisted DB models.
Synchronizes runtime settings down to isolated lab DB and coordinates deterministic intrabar execution.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
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
        slippage: Optional[float] = None,
        fee_bps: Optional[float] = None,
        maker_fee_bps: Optional[float] = None,
        taker_fee_bps: Optional[float] = None,
        run_id: Optional[str] = None,
    ):
        self.db = db
        self.clock = clock
        self.initial_capital = float(initial_capital)
        self.risk_pct = float(risk_pct)
        self.fee_bps = float(fee_bps) if fee_bps is not None else 4.0

        # Slippage resolution: 0.0 is valid and must be strictly preserved!
        if slippage is not None:
            self.slippage = float(slippage)
        elif costs is not None and getattr(costs, "slippage_usd", None) is not None:
            self.slippage = float(costs.slippage_usd)
        elif config is not None and getattr(config, "slippage_usd", None) is not None:
            self.slippage = float(config.slippage_usd)
        else:
            self.slippage = 0.10

        maker_rate = (maker_fee_bps / 10000.0) if maker_fee_bps is not None else 0.0002
        taker_rate = (taker_fee_bps / 10000.0) if taker_fee_bps is not None else (self.fee_bps / 10000.0 if self.fee_bps >= 1.0 else 0.0004)

        self.costs = costs or CostAssumptions(
            slippage_usd=self.slippage,
            maker_fee_rate=maker_rate,
            taker_fee_rate=taker_rate,
        )
        if slippage is not None:
            self.costs.slippage_usd = self.slippage

        self.config = config or CG01Config(enabled=True, profile_mode="PAPER_EXECUTION")
        self.config.fallback_risk_pct = self.risk_pct
        self.config.costs = self.costs
        self.config.slippage_usd = self.slippage

        self.run_id = run_id or f"run_{self.clock.now_ms()}"
        self.orchestrator = ScalpWatcherOrchestrator()
        self.coordinator = ExecutionCoordinator()

        # Seed & Sync DB Settings
        self.sync_runtime_settings()

    def sync_runtime_settings(self) -> None:
        """
        V16.6: Synchronizes authoritative replay configuration down to SQLite DB:
        - models.SystemConfig (default_risk_pct, fallback_risk_pct, default_slippage, slippage, slippage_usd, fee_bps)
        - DayAudit (initial_equity, current_equity = initial_capital + realized_pnl)
        """
        now_ms = self.clock.now_ms() if hasattr(self.clock, "now_ms") else int(time.time() * 1000)

        def _upsert(key: str, val: str):
            cfg = self.db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
            if cfg:
                cfg.value = val
                cfg.updated_at = now_ms
            else:
                self.db.add(models.SystemConfig(key=key, value=val, updated_at=now_ms))

        _upsert("default_risk_pct", str(self.risk_pct))
        _upsert("risk_per_trade_pct", str(self.risk_pct))
        _upsert("fallback_risk_pct", str(self.risk_pct))
        _upsert("default_slippage", str(self.slippage))
        _upsert("slippage", str(self.slippage))
        _upsert("slippage_usd", str(self.slippage))
        _upsert("fee_bps", str(self.fee_bps))
        _upsert("account_balance", str(self.initial_capital))
        _upsert("initial_capital", str(self.initial_capital))
        _upsert("equity", str(self.initial_capital))
        _upsert("default_leverage", str(getattr(self.config, "leverage", 5) or 5))

        audit = crud.get_or_create_today_audit(self.db, clock=self.clock)
        closed_orders = self.db.query(models.PaperOrder).filter(
            models.PaperOrder.state.in_(("closed", "paper_closed"))
        ).all()
        realized_pnl = sum(float(o.realized_pnl_net or 0.0) for o in closed_orders)

        audit.initial_equity = self.initial_capital
        audit.current_equity = round(self.initial_capital + realized_pnl, 4)
        audit.updated_at_ms = now_ms
        self.db.commit()

    def _assert_order_config_parity(self, order: Any, snapshot: Any) -> None:
        """
        V16.6 Parity Assertion:
        Ensures order and snapshot adhere strictly to adapter runtime authority.
        """
        snap_slip = snapshot.get("slippage") if isinstance(snapshot, dict) else getattr(snapshot, "slippage", None)
        if snap_slip is None:
            snap_slip = snapshot.get("slippage_usd") if isinstance(snapshot, dict) else getattr(snapshot, "slippage_usd", None)

        snap_risk_pct = snapshot.get("risk_pct") if isinstance(snapshot, dict) else getattr(snapshot, "risk_pct", None)
        snap_risk_usd = snapshot.get("initial_risk_usd") if isinstance(snapshot, dict) else getattr(snapshot, "initial_risk_usd", None)
        if snap_risk_usd is None:
            snap_risk_usd = snapshot.get("initial_risk_usdt") if isinstance(snapshot, dict) else getattr(snapshot, "initial_risk_usdt", None)

        if snap_slip is not None:
            assert abs(float(snap_slip) - self.slippage) < 1e-4, f"Slippage parity violation: expected {self.slippage}, got {snap_slip}"
        if snap_risk_pct is not None:
            assert abs(float(snap_risk_pct) - self.risk_pct) < 1e-4, f"Risk % parity violation: expected {self.risk_pct}, got {snap_risk_pct}"
        target_risk = self.initial_capital * (self.risk_pct / 100.0)
        if snap_risk_usd is not None:
            assert abs(float(snap_risk_usd) - target_risk) < 0.05, f"Initial risk USD parity violation: expected ~{target_risk}, got {snap_risk_usd}"

    def on_bar_close(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Rule 085: Advance clock and trigger orchestrator.evaluate_cycle.
        Does not mutate PaperOrder directly.
        """
        now_ms = int(event["as_of_ms"])
        self.clock.set_time(now_ms)
        self.sync_runtime_settings()

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

    @staticmethod
    def _generate_intrabar_waypoints(open_px: float, high: float, low: float, close_px: float) -> List[Tuple[str, float]]:
        """
        V16.6: Deterministic intrabar path.
        Green candle: OPEN -> LOW -> HIGH -> CLOSE
        Red candle:   OPEN -> HIGH -> LOW -> CLOSE
        """
        if close_px >= open_px:
            return [
                ("OPEN", open_px),
                ("LOW", low),
                ("HIGH", high),
                ("CLOSE", close_px),
            ]
        else:
            return [
                ("OPEN", open_px),
                ("HIGH", high),
                ("LOW", low),
                ("CLOSE", close_px),
            ]

    def on_candle(self, candle: Dict[str, Any]) -> None:
        """Helper to process a full candle with intrabar simulation."""
        event = dict(candle)
        event["is_candle"] = True
        self.on_quote(event)

    def on_quote(self, event: Dict[str, Any]) -> None:
        """
        Rule 086, 091, 092 & V16.6: Intrabar simulation parity.
        1. Checks if event is a full candle or a single quote tick.
        2. For candle: evaluates ambiguity guard (SL-first) and deterministic waypoints.
        3. For single quote tick: standard broker/coordinator evaluation.
        """
        now_ms = int(event["timestamp"])
        self.clock.set_time(now_ms)

        bid = float(event["bid"])
        ask = float(event["ask"])
        high = float(event.get("high", max(bid, ask)))
        low = float(event.get("low", min(bid, ask)))
        bar_open = event.get("bar_open_time")
        bar_dur = int(event.get("bar_duration_ms", 60_000))

        is_candle = bool(event.get("is_candle")) or ("open" in event and "close" in event)

        if not is_candle:
            # Single quote tick processing
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
            self.coordinator.evaluate_orders_sync(
                db=self.db,
                ticker_override={"bid": bid, "ask": ask, "last": ask, "timestamp": now_ms},
                clock=self.clock,
                now_ms=now_ms,
                slippage=self.slippage,
            )
            return

        # Candle processing: deterministic intrabar waypoints & ambiguity check
        open_px = float(event["open"])
        close_px = float(event["close"])

        from services.trade_lifecycle_service import TradeLifecycleService

        # 0. Ambiguity Check & Worst-case handling for active open positions and pending orders
        # Case A: Active Open Position
        open_pos = crud.get_active_position(self.db)
        if open_pos and open_pos.state == "paper_open":
            is_ambiguous = False
            if open_pos.direction == "LONG":
                if open_pos.stop_loss is not None and open_pos.take_profit is not None:
                    if low <= open_pos.stop_loss and high >= open_pos.take_profit:
                        is_ambiguous = True
            elif open_pos.direction == "SHORT":
                if open_pos.stop_loss is not None and open_pos.take_profit is not None:
                    if high >= open_pos.stop_loss and low <= open_pos.take_profit:
                        is_ambiguous = True

            if is_ambiguous:
                open_pos.intrabar_ambiguous = True
                TradeLifecycleService.execute_close(
                    db=self.db,
                    order_id=open_pos.id,
                    exit_price=open_pos.stop_loss,
                    exit_cause="STOP_LOSS",
                    now_ms=now_ms,
                    clock=self.clock,
                    session_tag="REPLAY",
                    recovery_metadata={"intrabar_ambiguous": True},
                )
                open_pos.intrabar_ambiguous = True
                self.db.commit()

        # Case B: Pending limit orders
        armed_orders = self.db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").all()
        for po in armed_orders:
            # Causality guard: do not fill orders created at or after now_ms
            if po.created_at is not None and po.created_at >= now_ms:
                continue

            is_ambiguous = False
            if po.direction == "LONG":
                if low <= po.planned_entry and po.stop_loss is not None and low <= po.stop_loss:
                    is_ambiguous = True
            elif po.direction == "SHORT":
                if high >= po.planned_entry and po.stop_loss is not None and high >= po.stop_loss:
                    is_ambiguous = True

            if is_ambiguous:
                po.intrabar_ambiguous = True
                calc = calculate_risk_reward(
                    direction=po.direction,
                    entry=po.planned_entry,
                    sl=po.stop_loss,
                    tp=po.take_profit,
                    capital=self.initial_capital,
                    risk_pct=po.risk_pct or self.risk_pct,
                    entry_has_slippage=False,
                )
                TradeLifecycleService.execute_fill(
                    db=self.db,
                    order=po,
                    fill_price=po.planned_entry,
                    calc_result=calc,
                    now_ms=now_ms,
                    clock=self.clock,
                    recovery_metadata={"intrabar_ambiguous": True},
                )
                self.db.refresh(po)
                if po.state == "paper_open":
                    TradeLifecycleService.execute_close(
                        db=self.db,
                        order_id=po.id,
                        exit_price=po.stop_loss,
                        exit_cause="STOP_LOSS",
                        now_ms=now_ms,
                        clock=self.clock,
                        session_tag="REPLAY",
                        recovery_metadata={"intrabar_ambiguous": True},
                    )
                    po.intrabar_ambiguous = True
                    self.db.commit()

        # 1. Deterministic Intrabar Waypoints for normal execution
        waypoints = self._generate_intrabar_waypoints(open_px, high, low, close_px)

        for phase, px in waypoints:
            # Re-fetch active open position to see if it is open
            curr_pos = crud.get_active_position(self.db)
            if curr_pos and curr_pos.state == "paper_open":
                PaperBroker.process_price_tick(
                    db=self.db,
                    current_bid=round(px - 0.05, 2),
                    current_ask=round(px + 0.05, 2),
                    candle_high=px,
                    candle_low=px,
                    candle_timestamp=bar_open,
                    bar_duration_ms=bar_dur,
                    now_ms=now_ms,
                    clock=self.clock,
                    session_tag="REPLAY",
                    tick_timestamp=now_ms,
                )

            # Evaluate pending limit orders against this waypoint
            # For LONG limit, ask dips to low when phase == LOW.
            # For SHORT limit, bid rises to high when phase == HIGH.
            wp_ask = px if phase == "LOW" else round(px + 0.05, 2)
            wp_bid = px if phase == "HIGH" else round(px - 0.05, 2)

            quote_dict = {
                "bid": wp_bid,
                "ask": wp_ask,
                "last": px,
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
                "intrabar_ambiguous": getattr(o, "intrabar_ambiguous", False),
            })

        open_position: Optional[Dict[str, Any]] = None
        if open_orders:
            op = open_orders[0]
            op_snap = {}
            if op.cost_snapshot:
                try:
                    op_snap = json.loads(op.cost_snapshot) if isinstance(op.cost_snapshot, str) else dict(op.cost_snapshot)
                except Exception:
                    op_snap = {}
            entry_p = op.actual_entry or op.planned_entry
            taker_rate = float(op_snap.get("taker_fee_rate", 0.0004))
            op_fee = float(op_snap.get("entry_fee_usdt", entry_p * op.quantity * taker_rate))

            open_position = {
                "trade_id": op.id,
                "direction": op.direction,
                "entry_time": op.opened_at,
                "entry_price": entry_p,
                "stop_loss": op.stop_loss,
                "take_profit": op.take_profit,
                "quantity": op.quantity,
                "initial_risk_usdt": op.initial_risk_usdt,
                "entry_fee_usdt": op_fee,
                "intrabar_ambiguous": getattr(op, "intrabar_ambiguous", False),
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
