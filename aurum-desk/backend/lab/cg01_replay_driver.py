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

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.cg01_context_adapter import aggregate_closed_bars, select_closed_candles
from services.cg01_engine import evaluate_cg01_candidate
from services.cg01_types import CG01Config, CG01EntryMode, CG01Stage
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
    """Rule 176: Computes MTM Equity = cash + unrealized PnL."""
    if not open_position:
        return cash

    direction = open_position["direction"]
    entry_price = open_position["entry_price"]
    qty = open_position["quantity"]

    if direction == "LONG":
        unrealized = qty * (current_price - entry_price)
    else:
        unrealized = qty * (entry_price - current_price)

    return round(cash + unrealized, 4)


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
    ):
        self.m1_dataset_path = m1_dataset_path
        self.config = config or CG01Config(enabled=True, profile_mode="PAPER_EXECUTION")
        self.initial_capital = initial_capital
        self.risk_pct = risk_pct
        self.costs = costs or CostAssumptions()

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

        # Run causal forward simulation
        ny_tz = ZoneInfo("America/New_York")
        cash = self.initial_capital
        peak_mtm = cash
        max_dd_pct = 0.0

        daily_stats: Dict[str, Dict[str, Any]] = {}
        closed_trades: List[Dict[str, Any]] = []
        equity_mtm_series: List[Dict[str, Any]] = []
        equity_realized_series: List[Dict[str, Any]] = []
        rejection_funnel: List[Dict[str, Any]] = []

        open_position: Optional[Dict[str, Any]] = None
        pending_order: Optional[Dict[str, Any]] = None
        executed_episodes: set = set()

        for idx in range(15, len(candles)):
            current_bar = candles[idx]
            now_ms = current_bar.open_time + 60_000 # candle close time
            dt_ny = datetime.fromtimestamp(now_ms / 1000, tz=ny_tz)
            date_str = dt_ny.strftime("%Y-%m-%d")

            if date_str not in daily_stats:
                is_weekday = (dt_ny.weekday() < 5)
                daily_stats[date_str] = {
                    "date": date_str,
                    "is_market_day": is_weekday,
                    "fills_count": 0,
                    "ny_fills": 0,
                    "status": "SEARCHING" if is_weekday else "WEEKEND",
                    "shortfall_reason": "SEARCHING" if is_weekday else "WEEKEND_MARKET_CLOSED",
                }

            d_stat = daily_stats[date_str]

            # 0. Check pending limit orders against current bar price (Rules 081, 084, 149, 169)
            if pending_order is not None and open_position is None:
                if now_ms > pending_order["valid_until_ms"]:
                    # Order expired before fill
                    pending_order = None
                else:
                    po_dir = pending_order["direction"]
                    po_entry = pending_order["planned_entry"]
                    filled = False
                    fill_price = po_entry

                    if po_dir == "LONG" and current_bar.low <= po_entry:
                        filled = True
                        fill_price = po_entry
                    elif po_dir == "SHORT" and current_bar.high >= po_entry:
                        filled = True
                        fill_price = po_entry

                    if filled:
                        open_position = {
                            "trade_id": pending_order["trade_id"],
                            "entry_time": now_ms,
                            "direction": po_dir,
                            "model_id": pending_order["model_id"],
                            "entry_price": fill_price,
                            "stop_loss": pending_order["stop_loss"],
                            "take_profit": pending_order["take_profit"],
                            "quantity": pending_order["quantity"],
                        }
                        pending_order = None
                        d_stat["fills_count"] += 1
                        if 10 <= dt_ny.hour < 11:
                            d_stat["ny_fills"] += 1
                        if d_stat["fills_count"] >= self.config.daily_max_cap:
                            d_stat["status"] = "CAP_REACHED"
                        elif d_stat["fills_count"] >= self.config.daily_min_goal:
                            d_stat["status"] = "MINIMUM_MET"

            # 1. Update open position exits causally (Rules 168, 172, 173)
            if open_position is not None:
                p_dir = open_position["direction"]
                p_sl = open_position["stop_loss"]
                p_tp = open_position["take_profit"]
                p_entry = open_position["entry_price"]
                p_qty = open_position["quantity"]

                exit_occurred = False
                exit_price = p_entry
                outcome = "OPEN"

                is_ambiguous = False
                ambiguity_reason = None

                # Check SL / TP on current bar
                if p_dir == "LONG":
                    hit_sl = (current_bar.low <= p_sl)
                    hit_tp = (current_bar.high >= p_tp)
                    if hit_sl and hit_tp:
                        # Conservative rule 172: ambiguous bar touches both SL and TP -> treat as SL assumption
                        outcome = "SL"
                        is_ambiguous = True
                        ambiguity_reason = "INTRABAR_SL_TP_COLLISION"
                        exit_price = p_sl
                        exit_occurred = True
                    elif hit_sl:
                        outcome = "SL"
                        exit_price = min(p_sl, current_bar.open) if current_bar.open < p_sl else p_sl
                        exit_occurred = True
                    elif hit_tp:
                        outcome = "TP"
                        exit_price = p_tp
                        exit_occurred = True
                else:
                    hit_sl = (current_bar.high >= p_sl)
                    hit_tp = (current_bar.low <= p_tp)
                    if hit_sl and hit_tp:
                        outcome = "SL"
                        is_ambiguous = True
                        ambiguity_reason = "INTRABAR_SL_TP_COLLISION"
                        exit_price = p_sl
                        exit_occurred = True
                    elif hit_sl:
                        outcome = "SL"
                        exit_price = max(p_sl, current_bar.open) if current_bar.open > p_sl else p_sl
                        exit_occurred = True
                    elif hit_tp:
                        outcome = "TP"
                        exit_price = p_tp
                        exit_occurred = True

                if exit_occurred:
                    fee_rate = self.costs.taker_fee if self.costs else 0.0005
                    gross_pnl = p_qty * (exit_price - p_entry if p_dir == "LONG" else p_entry - exit_price)
                    fees = (p_qty * p_entry * fee_rate) + (p_qty * exit_price * fee_rate)
                    net_pnl = round(gross_pnl - fees, 2)
                    cash = round(cash + net_pnl, 2)

                    trade_record = {
                        "trade_id": open_position["trade_id"],
                        "entry_time": open_position["entry_time"],
                        "exit_time": now_ms,
                        "direction": p_dir,
                        "model_id": open_position["model_id"],
                        "entry_price": p_entry,
                        "exit_price": exit_price,
                        "stop_loss": p_sl,
                        "take_profit": p_tp,
                        "quantity": p_qty,
                        "outcome": outcome,
                        "is_ambiguous": is_ambiguous,
                        "ambiguity_reason": ambiguity_reason,
                        "net_pnl_usd": net_pnl,
                        "realized_cash": cash,
                    }
                    closed_trades.append(trade_record)
                    equity_realized_series.append({
                        "timestamp": now_ms,
                        "cash": cash,
                        "realized_pnl": net_pnl,
                    })
                    open_position = None

            # Mark to Market Equity calculation (Rule 176, 177)
            mtm_eq = compute_equity_mark(cash, open_position, current_bar.close)
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

            # 2. Check if we can look for new entries (Rule 169: single position or pending order at a time)
            if open_position is not None or pending_order is not None:
                continue

            # Check daily cap
            if d_stat["fills_count"] >= self.config.daily_max_cap:
                d_stat["status"] = "CAP_REACHED"
                continue

            # Evaluate CG-01 candidate causally with aggregated closed HTF bars (Rules 146 - 148)
            c_slice = candles[:idx+1]
            agg_4h = aggregate_closed_bars(c_slice, "4H", as_of_ms=now_ms)
            agg_1h = aggregate_closed_bars(c_slice, "1H", as_of_ms=now_ms)
            agg_15m = aggregate_closed_bars(c_slice, "15M", as_of_ms=now_ms)
            agg_1m = select_closed_candles(c_slice, "1M", as_of_ms=now_ms)

            ctx = {
                "4H": agg_4h,
                "1H": agg_1h,
                "15M": agg_15m,
                "1M": agg_1m,
            }
            quote = {
                "bid": current_bar.close,
                "ask": current_bar.close + 0.10,
                "is_valid": True,
                "is_stale": False,
                "timestamp": now_ms,
            }

            decision = evaluate_cg01_candidate(
                context=ctx,
                current_quote=quote,
                config=self.config,
                now_ms=now_ms,
                capital=cash,
                risk_pct=self.risk_pct,
                costs=self.costs,
            )

            if decision.is_eligible and decision.candidate and decision.stage == CG01Stage.EXECUTABLE:
                cand = decision.candidate
                ep_id = cand.origin_episode_id or decision.episode_id
                if ep_id in executed_episodes:
                    continue
                executed_episodes.add(ep_id)

                calc = cand.calculation
                qty = calc.quantity if calc else 0.01

                if self.config.entry_mode == CG01EntryMode.CONFIRMED_RETEST_MARKET.value:
                    open_position = {
                        "trade_id": f"trade_{len(closed_trades) + 1}",
                        "entry_time": now_ms,
                        "direction": cand.direction,
                        "model_id": cand.model_id,
                        "entry_price": cand.planned_entry,
                        "stop_loss": cand.stop_loss,
                        "take_profit": cand.take_profit,
                        "quantity": qty,
                    }
                    d_stat["fills_count"] += 1
                    if 10 <= dt_ny.hour < 11:
                        d_stat["ny_fills"] += 1
                    if d_stat["fills_count"] >= self.config.daily_max_cap:
                        d_stat["status"] = "CAP_REACHED"
                    elif d_stat["fills_count"] >= self.config.daily_min_goal:
                        d_stat["status"] = "MINIMUM_MET"
                else:
                    # Limit at CE: armed pending order awaiting future bar touch
                    pending_order = {
                        "trade_id": f"trade_{len(closed_trades) + 1}",
                        "armed_at": now_ms,
                        "valid_until_ms": now_ms + self.config.candidate_ttl_ms,
                        "direction": cand.direction,
                        "model_id": cand.model_id,
                        "planned_entry": cand.planned_entry,
                        "stop_loss": cand.stop_loss,
                        "take_profit": cand.take_profit,
                        "quantity": qty,
                    }
            elif decision.reason_codes:
                rejection_funnel.append({
                    "date": date_str,
                    "stage": decision.stage.value if hasattr(decision.stage, "value") else str(decision.stage),
                    "reason": decision.reason_codes[0],
                })

        # Summary Metrics (Rules 111 - 119)
        closed_trades_count = len(closed_trades)
        open_positions_count = 1 if open_position is not None else 0
        pending_orders_count = 1 if pending_order is not None else 0
        total_fills = closed_trades_count + open_positions_count

        # Net winrate (Rules 118, 170): Gross TP but net negative is NOT counted as win
        wins = sum(1 for t in closed_trades if t.get("outcome") == "TP" and t.get("net_pnl_usd", 0.0) > 0.0)
        winrate = (wins / closed_trades_count * 100.0) if closed_trades_count > 0 else None
        total_pnl = round(cash - self.initial_capital, 2)

        return {
            "status": "COMPLETED",
            "reason": "OK",
            "total_fills": total_fills,
            "closed_trades_count": closed_trades_count,
            "open_positions_count": open_positions_count,
            "pending_orders_count": pending_orders_count,
            "closed_trades": closed_trades,
            "open_position": open_position,
            "pending_order": pending_order,
            "daily_stats": daily_stats,
            "equity_mtm": equity_mtm_series,
            "equity_realized": equity_realized_series,
            "rejection_funnel": rejection_funnel,
            "metrics": {
                "initial_capital": self.initial_capital,
                "final_cash": cash,
                "total_pnl_usd": total_pnl,
                "winrate_pct": round(winrate, 2) if winrate is not None else None,
                "max_drawdown_pct": round(max_dd_pct, 2),
                "total_trades": closed_trades_count,
                "open_trades": open_positions_count,
            },
        }
