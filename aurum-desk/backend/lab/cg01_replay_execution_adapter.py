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
from services.trade_lifecycle_service import TradeLifecycleService

logger = logging.getLogger("cg01_replay_execution_adapter")


def build_assumed_bar_events(
    bar: Dict[str, Any],
    path_policy: str = "COLOR_BASED",
    spread: Optional[float] = None,
    execution_model: str = "ASSUMED_OHLC_PATH",
    assumption_version: int = 1,
) -> List[Dict[str, Any]]:
    """
    Pure event expansion (V16.9 NHÓM 03, PHẦN 027 - 029).
    Expands an OHLC candle into ordered synthetic quote events.
    Green candle (close >= open): OPEN -> LOW -> HIGH -> CLOSE
    Red candle (close < open):    OPEN -> HIGH -> LOW -> CLOSE
    """
    open_px = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    close_px = float(bar["close"])
    now_ms = int(bar["timestamp"])
    bar_dur = int(bar.get("bar_duration_ms", 60_000))
    bar_open = int(bar.get("bar_open_time", now_ms - bar_dur))

    if high < low or open_px < (low - 1e-4) or open_px > (high + 1e-4) or close_px < (low - 1e-4) or close_px > (high + 1e-4):
        raise ValueError(f"INVALID_OHLC_GEOMETRY: open={open_px}, high={high}, low={low}, close={close_px}")

    if spread is None:
        raw_spread = max(0.0, float(bar.get("ask", close_px)) - float(bar.get("bid", close_px)))
        spread = raw_spread if raw_spread > 0 else 0.10
    half_spread = spread / 2.0

    if close_px >= open_px:
        waypoints = [
            ("OPEN", open_px),
            ("LOW", low),
            ("HIGH", high),
            ("CLOSE", close_px),
        ]
    else:
        waypoints = [
            ("OPEN", open_px),
            ("HIGH", high),
            ("LOW", low),
            ("CLOSE", close_px),
        ]

    wp_offsets = [0, int(bar_dur * 0.25), int(bar_dur * 0.75), bar_dur]

    events: List[Dict[str, Any]] = []
    for seq, (phase, px) in enumerate(waypoints):
        sub_ts = min(now_ms, bar_open + wp_offsets[seq])
        wp_bid = round(px - half_spread, 2)
        wp_ask = round(px + half_spread, 2)
        events.append({
            "timestamp_ms": sub_ts,
            "sequence": seq,
            "phase": phase,
            "price": px,
            "bid": wp_bid,
            "ask": wp_ask,
            "source": "SYNTHETIC_WAYPOINT",
            "execution_model": execution_model,
            "path_policy": path_policy,
            "assumption_version": assumption_version,
            "bar_open_time": bar_open,
            "bar_duration_ms": bar_dur,
        })
    return events


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
        execution_model: str = "ASSUMED_OHLC_PATH",
        capital_mode: Any = "CURRENT_ACCOUNT_BALANCE",
    ):
        self.db = db
        self.clock = clock
        self.fee_bps = float(fee_bps) if fee_bps is not None else 4.0
        self._bootstrapped = False

        # V16.7 & V16.8: Centralized Immutable Replay Config, Execution Model & Capital Mode Resolution
        from services.plan_financial_context_service import (
            resolve_replay_financial_config,
            ReplayFinancialContext,
            ReplayCapitalMode,
        )
        self.effective_config = resolve_replay_financial_config(
            initial_capital=initial_capital,
            risk_pct=risk_pct,
            slippage=slippage,
            fee_bps=fee_bps,
            maker_fee_bps=maker_fee_bps,
            taker_fee_bps=taker_fee_bps,
            costs=costs,
            config=config,
            capital_mode=capital_mode,
            execution_model=execution_model,
        )

        self.initial_capital = self.effective_config.initial_capital
        self.risk_pct = self.effective_config.risk_pct
        self.slippage = self.effective_config.slippage_usd
        self.costs = self.effective_config.costs
        self.capital_mode = self.effective_config.capital_mode
        self.execution_model = self.effective_config.execution_model

        # Clone config without mutating caller's object (PHẦN 041)
        import copy
        self.config = copy.deepcopy(config) if config is not None else CG01Config(enabled=True, profile_mode="PAPER_EXECUTION")
        self.config.slippage_usd = self.slippage
        self.config.costs = self.costs
        self.config.fallback_risk_pct = self.risk_pct
        self._processed_candles: set = set()

        self.run_id = run_id or f"run_{self.clock.now_ms()}"
        self.orchestrator = ScalpWatcherOrchestrator()
        self.coordinator = ExecutionCoordinator()

        # Seed & Sync DB Settings (Bootstrap once at init)
        self.bootstrap_replay_settings()

    def bootstrap_replay_settings(self) -> None:
        """
        V16.7 & V16.8: One-time bootstrap of authoritative replay configuration.
        Idempotent (PHẦN 042, 148): calling again on same run does not reset current equity.
        """
        if getattr(self, "_bootstrapped", False):
            return

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
        audit.initial_equity = self.initial_capital
        audit.current_equity = self.initial_capital
        audit.updated_at_ms = now_ms
        self.db.commit()
        self._bootstrapped = True

    def sync_runtime_settings(self) -> None:
        """
        V16.7 Group F: Refresh current equity projection without resetting initial_equity of subsequent days.
        """
        now_ms = self.clock.now_ms() if hasattr(self.clock, "now_ms") else int(time.time() * 1000)
        audit = crud.get_or_create_today_audit(self.db, clock=self.clock)
        closed_orders = self.db.query(models.PaperOrder).filter(
            models.PaperOrder.state.in_(("closed", "paper_closed"))
        ).all()
        realized_pnl = sum(float(o.realized_pnl_net or 0.0) for o in closed_orders)

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

        from services.plan_financial_context_service import ReplayFinancialContext
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

        # Resolve capital to pass based on capital_mode (PHẦN 031, 032, 033)
        if getattr(self, "capital_mode", "CURRENT_ACCOUNT_BALANCE") == "FIXED_INITIAL_CAPITAL":
            sizing_capital_override = self.initial_capital
        else:
            sizing_capital_override = None  # Defer to account audit in orchestrator

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
            execution_financial_context=ReplayFinancialContext(
                risk_pct=self.risk_pct,
                capital_usdt=sizing_capital_override,
                slippage_usd=self.slippage,
                costs=self.costs,
                capital_mode=self.capital_mode,
            ),
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
        V16.8 Group 06, 07, 08: Canonical execution model dispatch and strictly nondecreasing time.
        1. Validates execution model (CLOSE_QUOTE vs ASSUMED_OHLC_PATH).
        2. Dispatches BEFORE any execution or ambiguity handling.
        3. Never mutates clock to candle close prior to processing earlier subevents.
        4. In CLOSE_QUOTE mode, candle wicks are completely ignored (high/low are not passed to PaperBroker/coordinator).
        5. In ASSUMED_OHLC_PATH, waypoints advance clock monotonically: t0 <= t1 <= t2 <= t3 == now_ms.
        """
        now_ms = int(event["timestamp"])
        bid = float(event["bid"])
        ask = float(event["ask"])
        high = float(event.get("high", max(bid, ask)))
        low = float(event.get("low", min(bid, ask)))
        bar_open = int(event.get("bar_open_time", now_ms - 60_000))
        bar_dur = int(event.get("bar_duration_ms", 60_000))
        symbol = event.get("symbol", "XAUUSDT")

        is_candle = bool(event.get("is_candle")) or ("open" in event and "close" in event)

        if not is_candle:
            # Single quote tick processing
            self.clock.set_time(now_ms)
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

        # Duplicate candle protection (PHẦN 075, 105)
        candle_key = (symbol, bar_open, bar_dur)
        if candle_key in self._processed_candles:
            return
        self._processed_candles.add(candle_key)

        open_px = float(event["open"])
        close_px = float(event["close"])

        # EXECUTION MODEL DISPATCH FIRST (PHẦN 051)
        model = getattr(self, "execution_model", "ASSUMED_OHLC_PATH")

        if model == "CLOSE_QUOTE":
            # Advance clock monotonically directly to candle close
            self.clock.set_time(now_ms)
            # In CLOSE_QUOTE: candle_high and candle_low are set to close_px so broker only checks close quote (PHẦN 055, 056)
            PaperBroker.process_price_tick(
                db=self.db,
                current_bid=bid,
                current_ask=ask,
                candle_high=close_px,
                candle_low=close_px,
                candle_timestamp=bar_open,
                bar_duration_ms=bar_dur,
                now_ms=now_ms,
                clock=self.clock,
                session_tag="REPLAY",
                tick_timestamp=now_ms,
            )
            self.coordinator.evaluate_orders_sync(
                db=self.db,
                ticker_override={"bid": bid, "ask": ask, "last": close_px, "timestamp": now_ms},
                clock=self.clock,
                now_ms=now_ms,
                slippage=self.slippage,
            )
            return

        elif model == "ASSUMED_OHLC_PATH":
            # ASSUMED_OHLC_PATH: Deterministic intrabar waypoints with synthetic subevent timestamps (V16.9 PHẦN 021 - 040)
            events = build_assumed_bar_events(event)

            # Check if any currently open position is intrabar ambiguous across the full candle range
            open_pos = crud.get_active_position(self.db)
            if open_pos and open_pos.state == "paper_open":
                is_ambig = False
                if open_pos.direction == "LONG":
                    if open_pos.stop_loss is not None and open_pos.take_profit is not None:
                        if low <= open_pos.stop_loss and high >= open_pos.take_profit:
                            is_ambig = True
                elif open_pos.direction == "SHORT":
                    if open_pos.stop_loss is not None and open_pos.take_profit is not None:
                        if high >= open_pos.stop_loss and low <= open_pos.take_profit:
                            is_ambig = True

                if is_ambig:
                    sl_ts = events[1]["timestamp_ms"]
                    self.clock.set_time(sl_ts)
                    open_pos.intrabar_ambiguous = True
                    TradeLifecycleService.execute_close(
                        db=self.db,
                        order_id=open_pos.id,
                        exit_price=open_pos.stop_loss,
                        exit_cause="AMBIGUOUS_BAR_SL_FIRST",
                        occurred_at=sl_ts,
                        now_ms=sl_ts,
                        clock=self.clock,
                        session_tag="REPLAY",
                        recovery_metadata={"intrabar_ambiguous": True},
                    )
                    open_pos.intrabar_ambiguous = True
                    self.db.commit()

            # Process waypoints in strictly nondecreasing order without premature future close at bar_open
            for ev in events:
                sub_ts = ev["timestamp_ms"]
                px = ev["price"]
                wp_bid = ev["bid"]
                wp_ask = ev["ask"]

                self.clock.set_time(sub_ts)

                # Step 1: Re-fetch position that was ALREADY open before this waypoint
                curr_pos = crud.get_active_position(self.db)
                if curr_pos and curr_pos.state == "paper_open":
                    PaperBroker.process_price_tick(
                        db=self.db,
                        current_bid=wp_bid,
                        current_ask=wp_ask,
                        candle_high=px,
                        candle_low=px,
                        candle_timestamp=bar_open,
                        bar_duration_ms=bar_dur,
                        now_ms=sub_ts,
                        clock=self.clock,
                        session_tag="REPLAY",
                        tick_timestamp=sub_ts,
                    )

                # Step 2: Evaluate pending limit orders against this waypoint through canonical coordinator
                quote_dict = {
                    "bid": wp_bid,
                    "ask": wp_ask,
                    "last": px,
                    "timestamp": sub_ts,
                }
                self.coordinator.evaluate_orders_sync(
                    db=self.db,
                    ticker_override=quote_dict,
                    clock=self.clock,
                    now_ms=sub_ts,
                    slippage=self.slippage,
                )

                # Step 3: If an order was filled at this waypoint, evaluate exit against this same waypoint
                curr_pos = crud.get_active_position(self.db)
                if curr_pos and curr_pos.state == "paper_open" and curr_pos.opened_at == sub_ts:
                    exit_res = PaperBroker.process_price_tick(
                        db=self.db,
                        current_bid=wp_bid,
                        current_ask=wp_ask,
                        candle_high=px,
                        candle_low=px,
                        candle_timestamp=bar_open,
                        bar_duration_ms=bar_dur,
                        now_ms=sub_ts,
                        clock=self.clock,
                        session_tag="REPLAY",
                        tick_timestamp=sub_ts,
                    )
                    if exit_res:
                        # Intrabar ambiguity flag if candle range reached both entry and SL/TP
                        hit_both = False
                        if curr_pos.direction == "LONG" and low <= curr_pos.planned_entry and low <= curr_pos.stop_loss:
                            hit_both = True
                        elif curr_pos.direction == "SHORT" and high >= curr_pos.planned_entry and high >= curr_pos.stop_loss:
                            hit_both = True
                        if hit_both:
                            curr_pos.intrabar_ambiguous = True
                            self.db.commit()

            # Restore clock to candle close (PHẦN 061)
            self.clock.set_time(now_ms)
        else:
            raise ValueError(f"CONFIG_ERROR: Unknown execution model '{model}'")

    def collect_state(self) -> Dict[str, Any]:
        """
        Rule 087, 121 - 135: Read authoritative orders, audit, events from isolated lab DB.
        Pure projection, idempotent, read-only.
        """
        all_orders = self.db.query(models.PaperOrder).all()

        closed_orders = [o for o in all_orders if o.state in ("closed", "paper_closed")]
        # PHẦN 115: Sort closed trades chronologically by actual closed timestamp then stable ID
        closed_orders.sort(key=lambda o: (int(o.closed_at or 0), str(o.id or "")))

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

            # PHẦN 113: Prioritize persisted entry_fee_usdt from snapshot
            if "entry_fee_usdt" in op_snap and op_snap["entry_fee_usdt"] is not None:
                op_fee = float(op_snap["entry_fee_usdt"])
            elif getattr(op, "fees_assumption", None) is not None:
                op_fee = float(op.fees_assumption)
            else:
                op_fee = float(entry_p * op.quantity * taker_rate)

            # PHẦN 015, 111: Extract contract_multiplier from snapshot
            contract_mult = float(op_snap.get("contract_multiplier", op_snap.get("multiplier", 1.0)) or 1.0)

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
                "contract_multiplier": contract_mult,
                "multiplier": contract_mult,
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
