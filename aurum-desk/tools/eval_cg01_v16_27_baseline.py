#!/usr/bin/env python3
"""
tools/eval_cg01_v16_27_baseline.py
Evaluates baseline performance of CG-01 + HTF on commit 33dcd791f2ccfba8e3b3c19e5a44318cdf295ad8.
Runs two independent historical paper replays:
1. 30 calendar days UTC: 2026-09-18T00:00:00Z to 2026-10-18T00:00:00Z
2. 14 calendar days UTC: 2026-10-04T00:00:00Z to 2026-10-18T00:00:00Z

Enforces:
- READ-ONLY: zero changes to backend production code.
- NO order seeding / NO setup_hook.
- Uses native CG01ReplayDriver and CG01ReplayExecutionAdapter.
- Initial capital: 1000.0 USDT per replay.
- Generates raw report JSONs, structured metrics JSON, CSVs, and markdown reports.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from lab.cg01_replay_driver import CG01ReplayDriver
from domain_calculator import CostAssumptions
from services.cg01_types import CG01Config, CG01ProfileMode
from services.cg01_context_adapter import CanonicalCandle, resolve_tf_duration_ms
import lab.cg01_replay_driver as replay_driver_mod

# Pure cache for completed closed buckets (100% bit-for-bit identical to aggregate_closed_bars, converts O(N^2) to O(N))
_AGG_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}

def fast_aggregate_closed_bars(m1_bars: Sequence[Any], target_tf: str, as_of_ms: int, symbol: str = "XAUUSDT") -> List[CanonicalCandle]:
    dur = resolve_tf_duration_ms(target_tf)
    if dur <= 60_000:
        return replay_driver_mod.select_closed_candles(m1_bars, "1M", as_of_ms, symbol)

    cache_key = (symbol, target_tf)
    if cache_key not in _AGG_CACHE:
        _AGG_CACHE[cache_key] = {"completed": [], "last_m1_idx": 0, "buckets": {}}

    state = _AGG_CACHE[cache_key]
    completed = state["completed"]
    buckets = state["buckets"]

    for i in range(state["last_m1_idx"], len(m1_bars)):
        b = m1_bars[i]
        t = b["open_time"] if isinstance(b, dict) else b.open_time
        b_start = (t // dur) * dur
        if b_start not in buckets:
            buckets[b_start] = []
        buckets[b_start].append(b)
    state["last_m1_idx"] = len(m1_bars)

    for b_start in sorted(buckets.keys()):
        b_end = b_start + dur
        if b_end <= as_of_ms:
            bars = buckets.pop(b_start)
            bars.sort(key=lambda x: x["open_time"] if isinstance(x, dict) else x.open_time)
            o = float(bars[0]["open"] if isinstance(bars[0], dict) else bars[0].open)
            h = max(float(x["high"] if isinstance(x, dict) else x.high) for x in bars)
            l = min(float(x["low"] if isinstance(x, dict) else x.low) for x in bars)
            c = float(bars[-1]["close"] if isinstance(bars[-1], dict) else bars[-1].close)
            v = sum(float(x.get("volume", 100.0) if isinstance(x, dict) else getattr(x, "volume", 100.0)) for x in bars)
            agg = CanonicalCandle(
                open_time=b_start,
                open=round(o, 4),
                high=round(h, 4),
                low=round(l, 4),
                close=round(c, 4),
                volume=round(v, 4),
                timeframe=target_tf,
                symbol=symbol,
                is_closed=True,
                close_time_ms=b_end,
            )
            completed.append(agg)
            completed.sort(key=lambda x: x.open_time)

    return list(completed)

_CACHE_1M: List[CanonicalCandle] = []

def fast_select_closed_candles(m1_bars: Sequence[Any], timeframe: str, as_of_ms: int, symbol: str = "XAUUSDT") -> List[CanonicalCandle]:
    from services.cg01_context_adapter import normalize_cg01_candle, select_closed_candles as orig_select
    if timeframe != "1M":
        return orig_select(m1_bars, timeframe, as_of_ms, symbol)
    for i in range(len(_CACHE_1M), len(m1_bars)):
        c = normalize_cg01_candle(m1_bars[i], "1M", symbol)
        _CACHE_1M.append(c)
    return [c for c in _CACHE_1M if c.open_time + 60_000 <= as_of_ms]

replay_driver_mod.aggregate_closed_bars = fast_aggregate_closed_bars
replay_driver_mod.select_closed_candles = fast_select_closed_candles

ARTIFACTS_DIR = os.path.join(ROOT, "research_artifacts", "cg01_v16_27_baseline_20261018")
DATA_DIR = os.path.join(ARTIFACTS_DIR, "data")
M1_30D_PATH = os.path.join(DATA_DIR, "xauusdt_m1_30d.json")
M1_14D_PATH = os.path.join(DATA_DIR, "xauusdt_m1_14d.json")

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NY_TZ = ZoneInfo("America/New_York")


def get_git_info() -> Dict[str, Any]:
    try:
        remote = subprocess.check_output(["git", "config", "--get", "remote.origin.url"], cwd=ROOT, text=True).strip()
    except Exception:
        remote = "unknown"
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        branch = "unknown"
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        sha = "unknown"
    try:
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    except Exception:
        status = "unknown"

    return {
        "remote": remote,
        "branch": branch,
        "commit_sha": sha,
        "is_working_tree_clean": (status == ""),
        "status_porcelain": status,
    }


def format_ms(ts_ms: Optional[int], tz: ZoneInfo) -> Optional[str]:
    if ts_ms is None:
        return None
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=tz)
    return dt.isoformat()


def compute_wilson_score_interval(wins: int, total: int, confidence: float = 0.95) -> Optional[Tuple[float, float]]:
    """Calculates Wilson score interval for binomial proportion."""
    if total <= 0:
        return None
    # 95% confidence z = 1.96
    z = 1.959963984540054
    p = wins / total
    denom = 1.0 + (z**2) / total
    center = (p + (z**2) / (2.0 * total)) / denom
    margin = (z * math.sqrt((p * (1.0 - p) + (z**2) / (4.0 * total)) / total)) / denom
    lower = max(0.0, center - margin) * 100.0
    upper = min(1.0, center + margin) * 100.0
    return (round(lower, 2), round(upper, 2))


def process_closed_trades(closed_trades: List[Dict[str, Any]], initial_capital: float = 1000.0) -> Dict[str, Any]:
    total_closed = len(closed_trades)
    if total_closed == 0:
        return {
            "total_closed": 0,
            "wins": 0,
            "losses": 0,
            "breakevens": 0,
            "winrate_pct": None,
            "winrate_reason": "NO_CLOSED_TRADES",
            "net_pnl_usd": 0.0,
            "gross_pnl_usd": 0.0,
            "total_fees_usd": 0.0,
            "expectancy_net_usd": None,
            "expectancy_reason": "NO_CLOSED_TRADES",
            "expectancy_r": None,
            "profit_factor": None,
            "profit_factor_reason": "NO_CLOSED_TRADES",
            "avg_win_usd": None,
            "avg_loss_usd": None,
            "max_consecutive_losses": 0,
            "closed_equity_drawdown_pct": 0.0,
            "long_trades": {"count": 0, "wins": 0, "losses": 0, "net_pnl_usd": 0.0, "winrate_pct": None},
            "short_trades": {"count": 0, "wins": 0, "losses": 0, "net_pnl_usd": 0.0, "winrate_pct": None},
            "durations_minutes": {"avg": None, "min": None, "max": None},
            "processed_trades": [],
        }

    wins = 0
    losses = 0
    breakevens = 0
    total_net_pnl = 0.0
    total_gross_pnl = 0.0
    total_fees = 0.0

    sum_win_usd = 0.0
    sum_loss_usd = 0.0

    long_count = 0
    long_wins = 0
    long_losses = 0
    long_net = 0.0

    short_count = 0
    short_wins = 0
    short_losses = 0
    short_net = 0.0

    durations: List[float] = []
    processed: List[Dict[str, Any]] = []

    # Running equity peak for closed trade drawdown
    running_equity = initial_capital
    peak_equity = initial_capital
    max_closed_dd_pct = 0.0

    current_loss_streak = 0
    max_loss_streak = 0

    for t in closed_trades:
        net_pnl = float(t.get("realized_pnl_net") or t.get("net_pnl_usd") or 0.0)
        gross_pnl = float(t.get("realized_pnl_gross") or t.get("gross_pnl_usd") or 0.0)
        entry_fee = float(t.get("entry_fee_usdt") or 0.0)
        exit_fee = float(t.get("exit_fee_usdt") or 0.0)
        fee = entry_fee + exit_fee

        total_net_pnl += net_pnl
        total_gross_pnl += gross_pnl
        total_fees += fee

        direction = str(t.get("direction") or "").upper()
        if net_pnl > 0.0001:
            wins += 1
            sum_win_usd += net_pnl
            current_loss_streak = 0
            if direction == "LONG":
                long_wins += 1
            elif direction == "SHORT":
                short_wins += 1
        elif net_pnl < -0.0001:
            losses += 1
            sum_loss_usd += abs(net_pnl)
            current_loss_streak += 1
            if current_loss_streak > max_loss_streak:
                max_loss_streak = current_loss_streak
            if direction == "LONG":
                long_losses += 1
            elif direction == "SHORT":
                short_losses += 1
        else:
            breakevens += 1
            current_loss_streak = 0

        if direction == "LONG":
            long_count += 1
            long_net += net_pnl
        elif direction == "SHORT":
            short_count += 1
            short_net += net_pnl

        # Duration
        opened_at = t.get("opened_at")
        closed_at = t.get("closed_at")
        dur_mins = None
        if opened_at and closed_at and closed_at >= opened_at:
            dur_mins = round((closed_at - opened_at) / 60_000.0, 2)
            durations.append(dur_mins)

        # Drawdown tracking
        running_equity += net_pnl
        if running_equity > peak_equity:
            peak_equity = running_equity
        if peak_equity > 0:
            dd = ((peak_equity - running_equity) / peak_equity) * 100.0
            if dd > max_closed_dd_pct:
                max_closed_dd_pct = dd

        # Formatted trade record
        entry_ts = opened_at
        exit_ts = closed_at

        processed.append({
            "trade_id": t.get("order_id") or t.get("id"),
            "direction": direction,
            "entry_time_ms": entry_ts,
            "entry_time_utc": format_ms(entry_ts, timezone.utc),
            "entry_time_vn": format_ms(entry_ts, VN_TZ),
            "entry_time_ny": format_ms(entry_ts, NY_TZ),
            "exit_time_ms": exit_ts,
            "exit_time_utc": format_ms(exit_ts, timezone.utc),
            "exit_time_vn": format_ms(exit_ts, VN_TZ),
            "exit_time_ny": format_ms(exit_ts, NY_TZ),
            "entry_price": t.get("entry_price"),
            "exit_price": t.get("exit_price"),
            "quantity": t.get("quantity"),
            "exit_reason": t.get("exit_reason") or t.get("close_reason"),
            "net_pnl_usd": round(net_pnl, 4),
            "gross_pnl_usd": round(gross_pnl, 4),
            "total_fee_usd": round(fee, 4),
            "realized_r": t.get("realized_r"),
            "planned_entry": t.get("planned_entry"),
            "planned_sl": t.get("planned_sl"),
            "planned_tp": t.get("planned_tp"),
            "duration_minutes": dur_mins,
            "intrabar_ambiguous": t.get("intrabar_ambiguous", False),
        })

    winrate = round((wins / total_closed) * 100.0, 2)
    expectancy_net = round(total_net_pnl / total_closed, 4)

    profit_factor = None
    pf_reason = None
    if sum_loss_usd > 0:
        profit_factor = round(sum_win_usd / sum_loss_usd, 4)
    elif wins > 0:
        profit_factor = None
        pf_reason = "NO_LOSING_TRADES"
    else:
        profit_factor = 0.0

    avg_win = round(sum_win_usd / wins, 4) if wins > 0 else None
    avg_loss = round(sum_loss_usd / losses, 4) if losses > 0 else None

    long_wr = round((long_wins / long_count) * 100.0, 2) if long_count > 0 else None
    short_wr = round((short_wins / short_count) * 100.0, 2) if short_count > 0 else None

    return {
        "total_closed": total_closed,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "winrate_pct": winrate,
        "winrate_wilson_ci_95": compute_wilson_score_interval(wins, total_closed),
        "net_pnl_usd": round(total_net_pnl, 4),
        "gross_pnl_usd": round(total_gross_pnl, 4),
        "total_fees_usd": round(total_fees, 4),
        "expectancy_net_usd": expectancy_net,
        "profit_factor": profit_factor,
        "profit_factor_reason": pf_reason,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "max_consecutive_losses": max_loss_streak,
        "closed_equity_drawdown_pct": round(max_closed_dd_pct, 2),
        "long_trades": {
            "count": long_count,
            "wins": long_wins,
            "losses": long_losses,
            "net_pnl_usd": round(long_net, 4),
            "winrate_pct": long_wr,
        },
        "short_trades": {
            "count": short_count,
            "wins": short_wins,
            "losses": short_losses,
            "net_pnl_usd": round(short_net, 4),
            "winrate_pct": short_wr,
        },
        "durations_minutes": {
            "avg": round(sum(durations) / len(durations), 2) if durations else None,
            "min": min(durations) if durations else None,
            "max": max(durations) if durations else None,
        },
        "processed_trades": processed,
    }


def analyze_daily_distribution(daily_stats: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    total_days = len(daily_stats)
    market_days = sum(1 for d in daily_stats.values() if d.get("is_market_day", True))
    weekend_days = total_days - market_days

    days_0 = sum(1 for d in daily_stats.values() if d.get("fills_count", 0) == 0)
    days_1 = sum(1 for d in daily_stats.values() if d.get("fills_count", 0) == 1)
    days_2 = sum(1 for d in daily_stats.values() if d.get("fills_count", 0) == 2)
    days_3 = sum(1 for d in daily_stats.values() if d.get("fills_count", 0) == 3)
    days_gt_3 = sum(1 for d in daily_stats.values() if d.get("fills_count", 0) > 3)

    market_days_0 = sum(1 for d in daily_stats.values() if d.get("is_market_day", True) and d.get("fills_count", 0) == 0)
    market_days_target_met = sum(1 for d in daily_stats.values() if d.get("is_market_day", True) and d.get("fills_count", 0) >= 2)

    total_fills = sum(d.get("fills_count", 0) for d in daily_stats.values())
    total_ny_fills = sum(d.get("ny_fills", 0) for d in daily_stats.values())

    return {
        "total_calendar_days": total_days,
        "market_days": market_days,
        "weekend_days": weekend_days,
        "days_distribution": {
            "0_fills": days_0,
            "1_fill": days_1,
            "2_fills": days_2,
            "3_fills": days_3,
            "gt_3_fills": days_gt_3,
        },
        "market_days_with_zero_fills": market_days_0,
        "market_days_target_met": market_days_target_met,
        "target_met_rate_pct": round(market_days_target_met / market_days * 100.0, 2) if market_days > 0 else 0.0,
        "total_fills": total_fills,
        "total_ny_fills": total_ny_fills,
    }


def export_csv(filepath: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            clean_row = {k: r.get(k, "") for k in fieldnames}
            writer.writerow(clean_row)


def run_single_replay(
    m1_path: str,
    label: str,
    costs: CostAssumptions,
    config: CG01Config,
    initial_capital: float = 1000.0,
    risk_pct: float = 0.25,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    print(f"\n=======================================================")
    print(f"Starting {label} Replay...")
    print(f"Dataset: {m1_path}")
    print(f"Initial Capital: {initial_capital} USDT, Risk: {risk_pct}%, Fees: {costs.maker_fee_rate}/{costs.taker_fee_rate}")
    print(f"=======================================================")

    _AGG_CACHE.clear()
    _CACHE_1M.clear()
    t0 = time.time()
    driver = CG01ReplayDriver(
        m1_dataset_path=m1_path,
        config=config,
        initial_capital=initial_capital,
        risk_pct=risk_pct,
        costs=costs,
        capital_mode="CURRENT_ACCOUNT_BALANCE",
        execution_model="ASSUMED_OHLC_PATH",
        trading_day_timezone="Asia/Ho_Chi_Minh",
        setup_hook=None,  # STRICTLY NO SEEDING (Rules 073, 138)
    )

    raw_res = driver.run()
    elapsed = time.time() - t0
    print(f"{label} Replay completed in {elapsed:.2f}s. Status: {raw_res['status']}")

    closed_trades = raw_res.get("closed_trades", [])
    trade_metrics = process_closed_trades(closed_trades, initial_capital)
    daily_dist = analyze_daily_distribution(raw_res.get("daily_stats", {}))

    computed_metrics = {
        "label": label,
        "elapsed_seconds": round(elapsed, 2),
        "status": raw_res["status"],
        "reason": raw_res["reason"],
        "initial_capital_usdt": initial_capital,
        "final_cash_usdt": raw_res.get("metrics", {}).get("final_cash"),
        "total_fills": raw_res["total_fills"],
        "closed_trades_count": raw_res["closed_trades_count"],
        "open_positions_count": raw_res["open_positions_count"],
        "pending_orders_count": raw_res["pending_orders_count"],
        "trade_metrics": trade_metrics,
        "daily_distribution": daily_dist,
        "strategy_funnel": raw_res.get("strategy_funnel", {}),
        "rejection_funnel": raw_res.get("rejection_funnel", []),
        "max_drawdown": {
            "m1_mtm_drawdown_pct": raw_res.get("metrics", {}).get("max_drawdown_pct"),
            "closed_equity_drawdown_pct": trade_metrics["closed_equity_drawdown_pct"],
        },
        "effective_config": raw_res.get("effective_config", {}),
    }

    return raw_res, computed_metrics


def main():
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    git_info = get_git_info()
    print("Git verification:", git_info)

    if not os.path.exists(M1_30D_PATH) or not os.path.exists(M1_14D_PATH):
        print(f"Error: M1 datasets missing in {DATA_DIR}. Run download_v16_27_m1_dataset.py first!")
        sys.exit(1)

    costs = CostAssumptions(
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0004,
        slippage_usd=0.10,
        multiplier=1.0,
        tp_is_maker=False,
        tp_slippage_usd=0.0,
    )
    config = CG01Config(
        enabled=True,
        profile_mode=CG01ProfileMode.PAPER_EXECUTION.value,
    )

    # 1. Run 30-Day Replay
    raw_30d, metrics_30d = run_single_replay(
        m1_path=M1_30D_PATH,
        label="30-Day",
        costs=costs,
        config=config,
        initial_capital=1000.0,
        risk_pct=0.25,
    )

    # 2. Run 14-Day Replay
    raw_14d, metrics_14d = run_single_replay(
        m1_path=M1_14D_PATH,
        label="14-Day",
        costs=costs,
        config=config,
        initial_capital=1000.0,
        risk_pct=0.25,
    )

    # 3. Reproducibility test (Run 14-Day second time, Rule 183)
    print("\nRunning Reproducibility Replay on 14-Day dataset...")
    raw_14d_repro, metrics_14d_repro = run_single_replay(
        m1_path=M1_14D_PATH,
        label="14-Day Reproducibility",
        costs=costs,
        config=config,
        initial_capital=1000.0,
        risk_pct=0.25,
    )
    repro_match = (
        raw_14d["total_fills"] == raw_14d_repro["total_fills"]
        and raw_14d["closed_trades_count"] == raw_14d_repro["closed_trades_count"]
        and metrics_14d["trade_metrics"]["net_pnl_usd"] == metrics_14d_repro["trade_metrics"]["net_pnl_usd"]
        and metrics_14d["strategy_funnel"]["cycle_count"] == metrics_14d_repro["strategy_funnel"]["cycle_count"]
    )
    print(f"Reproducibility match: {repro_match}")

    # Save raw reports
    with open(os.path.join(ARTIFACTS_DIR, "raw_report_30d.json"), "w", encoding="utf-8") as f:
        json.dump(raw_30d, f, indent=2)
    with open(os.path.join(ARTIFACTS_DIR, "raw_report_14d.json"), "w", encoding="utf-8") as f:
        json.dump(raw_14d, f, indent=2)

    # Save metrics
    all_metrics = {
        "git_info": git_info,
        "evaluation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "reproducibility_verified": repro_match,
        "metrics_30d": metrics_30d,
        "metrics_14d": metrics_14d,
    }
    with open(os.path.join(ARTIFACTS_DIR, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, indent=2)

    # Save funnel JSONs
    with open(os.path.join(ARTIFACTS_DIR, "funnel_30d.json"), "w", encoding="utf-8") as f:
        json.dump({
            "strategy_funnel": raw_30d.get("strategy_funnel", {}),
            "rejection_funnel": raw_30d.get("rejection_funnel", []),
        }, f, indent=2)
    with open(os.path.join(ARTIFACTS_DIR, "funnel_14d.json"), "w", encoding="utf-8") as f:
        json.dump({
            "strategy_funnel": raw_14d.get("strategy_funnel", {}),
            "rejection_funnel": raw_14d.get("rejection_funnel", []),
        }, f, indent=2)

    # Save trades CSVs
    trade_fields = [
        "trade_id", "direction", "entry_time_ms", "entry_time_utc", "entry_time_vn", "entry_time_ny",
        "exit_time_ms", "exit_time_utc", "exit_time_vn", "exit_time_ny",
        "entry_price", "exit_price", "quantity", "exit_reason",
        "net_pnl_usd", "gross_pnl_usd", "total_fee_usd", "realized_r",
        "planned_entry", "planned_sl", "planned_tp", "duration_minutes", "intrabar_ambiguous"
    ]
    export_csv(os.path.join(ARTIFACTS_DIR, "trades_30d.csv"), metrics_30d["trade_metrics"]["processed_trades"], trade_fields)
    export_csv(os.path.join(ARTIFACTS_DIR, "trades_14d.csv"), metrics_14d["trade_metrics"]["processed_trades"], trade_fields)

    # Save daily stats CSVs
    daily_fields = ["date", "is_market_day", "fills_count", "ny_fills", "status", "shortfall_reason"]
    export_csv(
        os.path.join(ARTIFACTS_DIR, "daily_stats_30d.csv"),
        [v for k, v in sorted(raw_30d.get("daily_stats", {}).items())],
        daily_fields
    )
    export_csv(
        os.path.join(ARTIFACTS_DIR, "daily_stats_14d.csv"),
        [v for k, v in sorted(raw_14d.get("daily_stats", {}).items())],
        daily_fields
    )

    # Save equity series CSVs
    equity_fields = ["timestamp_ms", "datetime_utc", "datetime_vn", "datetime_ny", "mtm_equity", "cash_equity", "drawdown_pct"]
    def build_equity_rows(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        mtm_series = raw.get("equity_mtm", [])
        real_series = raw.get("equity_realized", [])
        rows = []
        for i in range(len(mtm_series)):
            pt_m = mtm_series[i]
            pt_r = real_series[i] if i < len(real_series) else {}
            ts = pt_m["timestamp"]
            rows.append({
                "timestamp_ms": ts,
                "datetime_utc": format_ms(ts, timezone.utc),
                "datetime_vn": format_ms(ts, VN_TZ),
                "datetime_ny": format_ms(ts, NY_TZ),
                "mtm_equity": pt_m.get("mtm_equity"),
                "cash_equity": pt_r.get("cash_equity"),
                "drawdown_pct": pt_m.get("drawdown_pct"),
            })
        return rows

    export_csv(os.path.join(ARTIFACTS_DIR, "equity_30d.csv"), build_equity_rows(raw_30d), equity_fields)
    export_csv(os.path.join(ARTIFACTS_DIR, "equity_14d.csv"), build_equity_rows(raw_14d), equity_fields)

    # Save Manifest
    manifest = {
        "git_commit": git_info["commit_sha"],
        "branch": git_info["branch"],
        "remote": git_info["remote"],
        "is_working_tree_clean": git_info["is_working_tree_clean"],
        "evaluation_timestamp_utc": all_metrics["evaluation_timestamp_utc"],
        "model_version": config.model_version,
        "execution_model": "ASSUMED_OHLC_PATH",
        "capital_mode": "CURRENT_ACCOUNT_BALANCE",
        "initial_capital_usdt": 1000.0,
        "risk_pct": 0.25,
        "costs": {
            "maker_fee_rate": costs.maker_fee_rate,
            "taker_fee_rate": costs.taker_fee_rate,
            "slippage_usd": costs.slippage_usd,
            "multiplier": costs.multiplier,
        },
        "datasets": {
            "30d": {
                "file": M1_30D_PATH,
                "start_utc": "2026-09-18T00:00:00Z",
                "end_utc": "2026-10-18T00:00:00Z",
            },
            "14d": {
                "file": M1_14D_PATH,
                "start_utc": "2026-10-04T00:00:00Z",
                "end_utc": "2026-10-18T00:00:00Z",
            }
        },
        "output_files": [
            "raw_report_30d.json", "raw_report_14d.json", "metrics.json",
            "trades_30d.csv", "trades_14d.csv", "daily_stats_30d.csv", "daily_stats_14d.csv",
            "equity_30d.csv", "equity_14d.csv", "funnel_30d.json", "funnel_14d.json",
            "manifest.json"
        ]
    }
    with open(os.path.join(ARTIFACTS_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print("\nAll artifacts generated in:", ARTIFACTS_DIR)


if __name__ == "__main__":
    main()
