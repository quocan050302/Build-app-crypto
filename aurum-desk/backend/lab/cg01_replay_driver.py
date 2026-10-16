"""
backend/lab/cg01_replay_driver.py
V16.2 Rules 161 - 180: Production-parity Replay Driver for CG-01 Paper Profile.
Enforces:
- Strict M1 dataset validation (returns BLOCKED_DATA if dataset is missing or non-M1).
- Causal bar feed (zero lookahead).
- Sizing and accounting via domain_calculator.calculate_risk_reward.
- Mark-to-Market (MTM) equity series and running peak drawdown.
- Canonical daily goal tracking (target 2, cap 3).
"""
from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import run_schema_migrations
from domain_calculator import CostAssumptions, calculate_risk_reward
from lab.cg01_replay_execution_adapter import CG01ReplayExecutionAdapter
from services.cg01_context_adapter import aggregate_closed_bars, select_closed_candles
from services.cg01_engine import evaluate_cg01_candidate
from services.cg01_types import CG01Config, CG01EntryMode, CG01Stage
from services.clock import ReplayClock
from services.scalp_m1_types import ModelIdentifier


@dataclass
class ReplayBar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 100.0
    is_closed: bool = True

    @property
    def timestamp(self) -> int:
        return self.open_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "open_time": self.open_time,
            "timestamp": self.open_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_closed": self.is_closed,
        }


def compute_equity_mark(cash: float, open_position: Optional[Dict[str, Any]], current_price: float) -> float:
    """Rule 176 & V16.7/V16.8: Computes MTM Equity = cash - entry_fees_of_open_positions + unrealized PnL with multiplier."""
    if not open_position:
        return round(cash, 4)

    direction = open_position["direction"]
    entry_price = open_position["entry_price"]
    qty = open_position["quantity"]
    entry_fee = float(open_position.get("entry_fee_usdt", 0.0))
    mult = float(open_position.get("contract_multiplier", open_position.get("multiplier", 1.0)) or 1.0)

    if direction == "LONG":
        unrealized = qty * (current_price - entry_price) * mult
    else:
        unrealized = qty * (entry_price - current_price) * mult

    return round(cash - entry_fee + unrealized, 4)



def _is_in_ny_session(ts_val: Any, policy: Optional[models.TradingPolicy] = None) -> bool:
    """
    V16.8: Authoritative NY session validator based on canonical policy.
    Uses TradingPolicyService.get_ny_session_window or policy windows.
    """
    if ts_val is None:
        return False
    from services.trading_policy_service import TradingPolicyService
    if isinstance(ts_val, (int, float)):
        now_ms = int(ts_val if ts_val > 1e11 else ts_val * 1000)
    elif isinstance(ts_val, datetime):
        now_ms = int(ts_val.timestamp() * 1000)
    else:
        return False
    win = TradingPolicyService.get_ny_session_window(policy=policy, now_ms=now_ms)
    return bool(win.get("is_in_window", False))


class CG01ReplayDriver:
    """
    Authoritative replay driver for CG-01.
    Strictly forbids faking M1 from 5M/15M or inventing targets (Rules 013, 014, 163).
    """
    def __init__(
        self,
        m1_dataset_path: Optional[str] = None,
        config: Optional[CG01Config] = None,
        initial_capital: float = 1000.0,
        risk_pct: float = 0.25,
        costs: Optional[CostAssumptions] = None,
        setup_hook: Optional[Callable[[Any, Any], None]] = None,
        capital_mode: Any = "CURRENT_ACCOUNT_BALANCE",
        execution_model: str = "ASSUMED_OHLC_PATH",
        trading_day_timezone: Optional[str] = None,
    ):
        self.m1_dataset_path = m1_dataset_path
        self.config = config or CG01Config(enabled=True, profile_mode="PAPER_EXECUTION")
        self.initial_capital = initial_capital
        self.risk_pct = risk_pct
        self.costs = costs or CostAssumptions()
        self.setup_hook = setup_hook
        self.capital_mode = capital_mode
        self.execution_model = execution_model
        self.trading_day_timezone = trading_day_timezone

    def run(self) -> Dict[str, Any]:
        """
        Executes replay or returns BLOCKED_DATA if dataset is missing or non-M1 (Rules 013, 163, 195).
        """
        if not self.m1_dataset_path or not os.path.exists(self.m1_dataset_path):
            return {
                "status": "BLOCKED_DATA",
                "reason": "Thiếu tệp dữ liệu M1 nến đóng chuẩn (Rules 013, 163, 195). Không dùng nến 5M/15M giả lập M1.",
                "total_fills": 0,
                "closed_trades_count": 0,
                "open_positions_count": 0,
                "pending_orders_count": 0,
                "closed_trades": [],
                "open_position": None,
                "pending_order": None,
                "daily_stats": {},
                "equity_mtm": [],
                "equity_realized": [],
                "rejection_funnel": [],
                "metrics": {
                    "initial_capital": self.initial_capital,
                    "final_cash": None,
                    "total_pnl_usd": None,
                    "winrate_pct": None,
                    "max_drawdown_pct": None,
                    "total_trades": 0,
                },
            }

        # Load and validate M1 candles
        try:
            with open(self.m1_dataset_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as e:
            return {
                "status": "BLOCKED_DATA",
                "reason": f"Không thể đọc tệp dữ liệu M1: {e}",
                "total_fills": 0,
                "closed_trades_count": 0,
                "open_positions_count": 0,
                "pending_orders_count": 0,
                "closed_trades": [],
                "open_position": None,
                "pending_order": None,
                "daily_stats": {},
                "equity_mtm": [],
                "equity_realized": [],
                "rejection_funnel": [],
                "metrics": {
                    "initial_capital": self.initial_capital,
                    "final_cash": None,
                    "total_pnl_usd": None,
                    "winrate_pct": None,
                    "max_drawdown_pct": None,
                    "total_trades": 0,
                },
            }

        # Validate M1 interval and length (Rule 096, 142)
        if len(raw_data) < 2:
            return {
                "status": "BLOCKED_DATA",
                "reason": "Dữ liệu M1 quá ngắn (< 2 nến).",
                "total_fills": 0,
                "closed_trades_count": 0,
                "open_positions_count": 0,
                "pending_orders_count": 0,
                "closed_trades": [],
                "open_position": None,
                "pending_order": None,
                "daily_stats": {},
                "equity_mtm": [],
                "equity_realized": [],
                "rejection_funnel": [],
                "metrics": {
                    "initial_capital": self.initial_capital,
                    "final_cash": None,
                    "total_pnl_usd": None,
                    "winrate_pct": None,
                    "max_drawdown_pct": None,
                    "total_trades": 0,
                },
            }

        candles: List[ReplayBar] = []
        for idx, r in enumerate(raw_data):
            try:
                t_raw = int(r.get("open_time") or r.get("timestamp") or r.get("time") or 0)
                o = float(r["open"])
                h = float(r["high"])
                l = float(r["low"])
                c = float(r["close"])
                v = float(r.get("volume", 100.0))
            except (ValueError, TypeError, KeyError) as e:
                return {
                    "status": "BLOCKED_DATA",
                    "reason": f"Dữ liệu nến không hợp lệ ở vị trí {idx}: {e}",
                    "total_fills": 0,
                    "closed_trades_count": 0,
                    "open_positions_count": 0,
                    "pending_orders_count": 0,
                    "closed_trades": [],
                    "open_position": None,
                    "pending_order": None,
                    "daily_stats": {},
                    "equity_mtm": [],
                    "equity_realized": [],
                    "rejection_funnel": [],
                    "metrics": {
                        "initial_capital": self.initial_capital,
                        "final_cash": None,
                        "total_pnl_usd": None,
                        "winrate_pct": None,
                        "max_drawdown_pct": None,
                        "total_trades": 0,
                    },
                }

            if h < l or o < (l - 1e-4) or o > (h + 1e-4) or c < (l - 1e-4) or c > (h + 1e-4) or t_raw <= 0:
                return {
                    "status": "BLOCKED_DATA",
                    "reason": f"Lỗi hình học giá OHLC hoặc timestamp ở vị trí {idx} (t={t_raw}, O={o}, H={h}, L={l}, C={c}).",
                    "total_fills": 0,
                    "closed_trades_count": 0,
                    "open_positions_count": 0,
                    "pending_orders_count": 0,
                    "closed_trades": [],
                    "open_position": None,
                    "pending_order": None,
                    "daily_stats": {},
                    "equity_mtm": [],
                    "equity_realized": [],
                    "rejection_funnel": [],
                    "metrics": {
                        "initial_capital": self.initial_capital,
                        "final_cash": None,
                        "total_pnl_usd": None,
                        "winrate_pct": None,
                        "max_drawdown_pct": None,
                        "total_trades": 0,
                    },
                }

            candles.append(ReplayBar(
                open_time=t_raw,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=v,
                is_closed=True,
            ))

        # Check non-decreasing timestamps and conflicting duplicates (Rule 025, 096)
        for i in range(len(candles) - 1):
            curr_c = candles[i]
            next_c = candles[i+1]
            if next_c.open_time < curr_c.open_time:
                return {
                    "status": "BLOCKED_DATA",
                    "reason": f"Dữ liệu timestamps bị đảo lộn tại vị trí {i} ({curr_c.open_time} > {next_c.open_time}).",
                    "total_fills": 0,
                    "closed_trades_count": 0,
                    "open_positions_count": 0,
                    "pending_orders_count": 0,
                    "closed_trades": [],
                    "open_position": None,
                    "pending_order": None,
                    "daily_stats": {},
                    "equity_mtm": [],
                    "equity_realized": [],
                    "rejection_funnel": [],
                    "metrics": {
                        "initial_capital": self.initial_capital,
                        "final_cash": None,
                        "total_pnl_usd": None,
                        "winrate_pct": None,
                        "max_drawdown_pct": None,
                        "total_trades": 0,
                    },
                }
            elif next_c.open_time == curr_c.open_time:
                # Conflicting duplicate check (Rule 025)
                if (next_c.high != curr_c.high or next_c.low != curr_c.low or
                    next_c.open != curr_c.open or next_c.close != curr_c.close):
                    return {
                        "status": "BLOCKED_DATA",
                        "reason": f"Phát hiện nến trùng timestamp {curr_c.open_time} nhưng xung đột OHLC (vị trí {i}).",
                        "total_fills": 0,
                        "closed_trades_count": 0,
                        "open_positions_count": 0,
                        "pending_orders_count": 0,
                        "closed_trades": [],
                        "open_position": None,
                        "pending_order": None,
                        "daily_stats": {},
                        "equity_mtm": [],
                        "equity_realized": [],
                        "rejection_funnel": [],
                        "metrics": {
                            "initial_capital": self.initial_capital,
                            "final_cash": None,
                            "total_pnl_usd": None,
                            "winrate_pct": None,
                            "max_drawdown_pct": None,
                            "total_trades": 0,
                        },
                    }

        diffs = [candles[i+1].open_time - candles[i].open_time for i in range(min(10, len(candles)-1))]
        avg_diff = sum(diffs) / len(diffs) if diffs else 0
        if not (50_000 <= avg_diff <= 70_000): # ~60_000 ms = 1 minute
            return {
                "status": "BLOCKED_DATA",
                "reason": f"Interval dữ liệu không phải 1M (khoảng cách trung bình: {avg_diff/1000:.1f}s, yêu cầu 60s).",
                "total_fills": 0,
                "closed_trades_count": 0,
                "open_positions_count": 0,
                "pending_orders_count": 0,
                "closed_trades": [],
                "open_position": None,
                "pending_order": None,
                "daily_stats": {},
                "equity_mtm": [],
                "equity_realized": [],
                "rejection_funnel": [],
                "metrics": {
                    "initial_capital": self.initial_capital,
                    "final_cash": None,
                    "total_pnl_usd": None,
                    "winrate_pct": None,
                    "max_drawdown_pct": None,
                    "total_trades": 0,
                },
            }

        # Run causal forward simulation via authoritative CG01ReplayExecutionAdapter (Rules 081 - 100)
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.Base.metadata.create_all(bind=engine)
        run_schema_migrations(engine)
        LabSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        lab_db = LabSession()

        initial_ms = candles[0].open_time if candles else 0
        replay_clock = ReplayClock(initial_ms=initial_ms)

        adapter = CG01ReplayExecutionAdapter(
            db=lab_db,
            clock=replay_clock,
            config=self.config,
            costs=self.costs,
            initial_capital=self.initial_capital,
            risk_pct=self.risk_pct,
            capital_mode=self.capital_mode,
            execution_model=self.execution_model,
        )

        if self.setup_hook:
            self.setup_hook(adapter, lab_db)

        # PHẦN 091: Canonical trading_day_timezone from policy
        from services.trading_policy_service import (
            resolve_effective_daily_policy,
            resolve_trade_day_bounds,
            TradingPolicyService,
        )
        effective_daily_policy = resolve_effective_daily_policy(lab_db)
        effective_tz_name = self.trading_day_timezone or effective_daily_policy.trading_day_timezone or "Asia/Ho_Chi_Minh"
        trading_policy_model = TradingPolicyService.get_policy(lab_db)
        tz_obj = ZoneInfo(effective_tz_name)

        daily_stats: Dict[str, Dict[str, Any]] = {}
        daily_bounds_map: Dict[str, Tuple[int, int]] = {}
        equity_mtm_series: List[Dict[str, Any]] = []
        equity_realized_series: List[Dict[str, Any]] = []
        decision_funnel_records: List[Dict[str, Any]] = []
        peak_mtm = self.initial_capital
        max_dd_pct = 0.0

        warmup_bars = 15
        start_idx = min(warmup_bars, len(candles))

        for idx in range(start_idx, len(candles)):
            current_bar = candles[idx]
            now_ms = current_bar.open_time + 60_000

            # PHẦN 092, 093: Direct resolve_trade_day_bounds from timestamp and effective timezone
            date_str, d_start_ms, d_end_ms = resolve_trade_day_bounds(now_ms, effective_tz_name)
            daily_bounds_map[date_str] = (d_start_ms, d_end_ms)

            if date_str not in daily_stats:
                local_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=tz_obj)
                is_weekday = (local_dt.weekday() < 5)
                daily_stats[date_str] = {
                    "date": date_str,
                    "is_market_day": is_weekday,
                    "fills_count": 0,
                    "ny_fills": 0,
                    "status": "SEARCHING" if is_weekday else "WEEKEND",
                    "shortfall_reason": "SEARCHING" if is_weekday else "WEEKEND_MARKET_CLOSED",
                }

            # 1. on_quote: process ticks against open position & pending orders via intrabar simulation
            quote_tick_event = {
                "timestamp": now_ms,
                "open": current_bar.open,
                "high": current_bar.high,
                "low": current_bar.low,
                "close": current_bar.close,
                "bid": round(current_bar.close - 0.05, 2),
                "ask": round(current_bar.close + 0.05, 2),
                "bar_open_time": current_bar.open_time,
                "bar_duration_ms": 60_000,
            }
            adapter.on_quote(quote_tick_event)

            # 2. on_bar_close: evaluate setups via real orchestrator
            c_slice = candles[:idx+1]
            agg_4h = aggregate_closed_bars(c_slice, "4H", as_of_ms=now_ms)
            agg_1h = aggregate_closed_bars(c_slice, "1H", as_of_ms=now_ms)
            agg_15m = aggregate_closed_bars(c_slice, "15M", as_of_ms=now_ms)
            agg_1m = select_closed_candles(c_slice, "1M", as_of_ms=now_ms)

            eval_quote = {
                "bid": round(current_bar.close - 0.05, 2),
                "ask": round(current_bar.close + 0.05, 2),
                "last": current_bar.close,
                "is_valid": True,
                "is_stale": False,
                "timestamp": now_ms,
            }
            bar_event = {
                "symbol": "XAUUSDT",
                "as_of_ms": now_ms,
                "quote": eval_quote,
                "candles_1m": agg_1m,
                "candles_15m": agg_15m,
                "candles_1h": agg_1h,
                "candles_4h": agg_4h,
            }
            # PHẦN 131: Capture evaluation cycle decision
            cycle_res = adapter.on_bar_close(bar_event)
            if cycle_res:
                st = cycle_res.get("status", "UNKNOWN")
                rsn = cycle_res.get("reason")
                stg = cycle_res.get("stage") or st
                decision_funnel_records.append({
                    "timestamp": now_ms,
                    "status": st,
                    "stage": stg,
                    "reason": rsn,
                    "order_id": cycle_res.get("order_id"),
                })

            # 3. Track state snapshot for MTM series & Daily Stats Delta
            interim = adapter.collect_state()
            d_stat = daily_stats[date_str]

            all_orders_curr = lab_db.query(models.PaperOrder).all()
            day_filled_orders = [
                o for o in all_orders_curr
                if o.opened_at is not None and d_start_ms <= o.opened_at < d_end_ms
            ]
            d_stat["fills_count"] = len(day_filled_orders)
            d_stat["ny_fills"] = sum(1 for o in day_filled_orders if _is_in_ny_session(o.opened_at, policy=trading_policy_model))

            if d_stat["fills_count"] >= self.config.daily_max_cap:
                d_stat["status"] = "CAP_REACHED"
            elif d_stat["fills_count"] >= self.config.daily_min_goal:
                d_stat["status"] = "MINIMUM_MET"

            raw_cash = interim.get("metrics", {}).get("final_cash")
            curr_cash = float(raw_cash) if raw_cash is not None else float(self.initial_capital)
            mtm_eq = compute_equity_mark(curr_cash, interim["open_position"], current_bar.close)
            if mtm_eq > peak_mtm:
                peak_mtm = mtm_eq
            dd = ((peak_mtm - mtm_eq) / peak_mtm * 100.0) if peak_mtm > 0 else 0.0
            if dd > max_dd_pct:
                max_dd_pct = dd

            equity_mtm_series.append({
                "timestamp": now_ms,
                "mtm_equity": round(mtm_eq, 2),
                "drawdown_pct": round(dd, 2),
            })
            equity_realized_series.append({
                "timestamp": now_ms,
                "cash_equity": round(curr_cash, 2),
            })

        final_report = adapter.collect_state()

        # V16.8 Final Daily Stats Reconciliation across all days using canonical day bounds (PHẦN 092, 095)
        final_orders = lab_db.query(models.PaperOrder).all()
        for d_key, d_stat in daily_stats.items():
            bounds = daily_bounds_map.get(d_key)
            if bounds:
                d_start_ms, d_end_ms = bounds
            else:
                d_start_dt = datetime.strptime(d_key, "%Y-%m-%d").replace(tzinfo=tz_obj)
                base_ms = int(d_start_dt.timestamp() * 1000)
                _, d_start_ms, d_end_ms = resolve_trade_day_bounds(base_ms + 3600_000, effective_tz_name)

            day_filled = [
                o for o in final_orders
                if o.opened_at is not None and d_start_ms <= o.opened_at < d_end_ms
            ]
            d_stat["fills_count"] = len(day_filled)
            d_stat["ny_fills"] = sum(1 for o in day_filled if _is_in_ny_session(o.opened_at, policy=trading_policy_model))
            if d_stat["fills_count"] >= self.config.daily_max_cap:
                d_stat["status"] = "CAP_REACHED"
            elif d_stat["fills_count"] >= self.config.daily_min_goal:
                d_stat["status"] = "MINIMUM_MET"
            else:
                d_stat["status"] = "SEARCHING" if d_stat.get("is_market_day", True) else "WEEKEND"

        # V16.8 Decision funnel accounting from recorded evaluations + lifecycle (PHẦN 131 - 139)
        expired_count = lab_db.query(models.PaperOrder).filter(models.PaperOrder.state == "expired").count()
        cancelled_count = lab_db.query(models.PaperOrder).filter(models.PaperOrder.state == "cancelled").count()
        armed_count = sum(1 for d in decision_funnel_records if d.get("status") in ("ARMED", "ARM_SUCCESS"))
        guard_blocked_count = sum(1 for d in decision_funnel_records if d.get("status") in ("BLOCKED", "GUARD_BLOCKED"))
        observing_count = sum(1 for d in decision_funnel_records if d.get("status") in ("OBSERVING", "WAITING", "SEARCHING", "TRIGGER_WAITING"))
        context_not_ready_count = sum(1 for d in decision_funnel_records if d.get("status") in ("CONTEXT_NOT_READY", "WARMUP", "DISABLED"))

        rejection_funnel = [
            {"category": "CONTEXT_NOT_READY", "count": context_not_ready_count, "description": "HTF context or warmup insufficient for evaluation"},
            {"category": "OBSERVING_WAITING_TRIGGER", "count": observing_count, "description": "Active observation awaiting valid sweep / displacement"},
            {"category": "GUARD_BLOCKED", "count": guard_blocked_count, "description": "Candidate setup blocked by risk/news/account/cooldown guards"},
            {"category": "ARMED_CANDIDATES", "count": armed_count, "description": "Setups successfully validated and armed for execution"},
            {"category": "EXPIRED_BEFORE_FILL", "count": expired_count, "description": "Armed orders expired prior to reaching limit price"},
            {"category": "CANCELLED_ORDERS", "count": cancelled_count, "description": "Armed orders cancelled prior to execution"},
        ]

        adapter.finalize_run()

        final_report["reason"] = "OK"
        final_report["daily_stats"] = daily_stats
        final_report["equity_mtm"] = equity_mtm_series
        final_report["equity_realized"] = equity_realized_series
        final_report["rejection_funnel"] = rejection_funnel
        final_report["decision_observations"] = decision_funnel_records

        # V16.6: Max drawdown computed directly from MTM series (never None if series is present)
        if equity_mtm_series:
            peak_val = self.initial_capital
            calc_dd = 0.0
            for pt in equity_mtm_series:
                eq_val = pt["mtm_equity"]
                if eq_val > peak_val:
                    peak_val = eq_val
                if peak_val > 0:
                    pt_dd = ((peak_val - eq_val) / peak_val) * 100.0
                    if pt_dd > calc_dd:
                        calc_dd = pt_dd
            final_report["metrics"]["max_drawdown_pct"] = round(calc_dd, 2)
        elif final_report["closed_trades_count"] > 0:
            final_report["metrics"]["max_drawdown_pct"] = round(max_dd_pct, 2)
        else:
            final_report["metrics"]["max_drawdown_pct"] = 0.0

        return final_report
