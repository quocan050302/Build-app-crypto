"""
AURUM DESK — REPLAY METRICS & RECONCILIATION (V13.5)
Single source of truth for:
- Trade partitioning (strictly closed vs open)
- Core metrics (Winrate, Profit Factor, Expectancy R, Max Drawdown)
- Cash ledger & equity curve reconciliation
- VN day vs NY session statistical aggregation
- Run difference diagnostics (diagnose_run_difference)
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Tuple, Optional

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NY_TZ = ZoneInfo("America/New_York")


def partition_trade_records(all_trades: List[Any]) -> Tuple[List[Any], List[Any], List[Any]]:
    """
    PHẦN 46, 55: Strictly partitions trades into closed, open, and invalid.
    Prevents open positions from inflating winrate denominator.
    """
    closed_trades = []
    open_trades = []
    invalid_trades = []

    for t in all_trades:
        status = getattr(t, "status", None)
        if status is None and isinstance(t, dict):
            status = t.get("status")

        if status == "CLOSED":
            closed_trades.append(t)
        elif status == "OPEN":
            open_trades.append(t)
        else:
            invalid_trades.append(t)

    return closed_trades, open_trades, invalid_trades


def compute_equity_metrics(equity_curve: List[Any], initial_equity: float = 1000.0) -> Dict[str, Any]:
    """
    PHẦN 51, 59: Computes peak equity, max drawdown USDT, and max drawdown pct.
    MaxDD% is computed strictly relative to peak equity.
    """
    if not equity_curve:
        return {
            "max_drawdown_usdt": 0.0,
            "max_drawdown_pct": 0.0,
            "peak_equity": initial_equity,
            "trough_equity": initial_equity,
            "peak_timestamp": 0,
            "trough_timestamp": 0
        }

    peak = initial_equity
    peak_ts = 0
    max_dd_usdt = 0.0
    max_dd_pct = 0.0
    trough_ts = 0

    for pt in equity_curve:
        eq = getattr(pt, "equity", None)
        ts = getattr(pt, "timestamp", 0)
        if eq is None and isinstance(pt, dict):
            eq = pt.get("equity", initial_equity)
            ts = pt.get("timestamp", 0)

        if eq > peak:
            peak = eq
            peak_ts = ts

        dd_usd = peak - eq
        dd_pct = (dd_usd / peak * 100.0) if peak > 0.0 else 0.0

        if dd_usd > max_dd_usdt:
            max_dd_usdt = dd_usd
            trough_ts = ts
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    return {
        "max_drawdown_usdt": round(max_dd_usdt, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "peak_equity": round(peak, 2),
        "peak_timestamp": peak_ts,
        "trough_timestamp": trough_ts
    }


def reconcile_cash_equity(
    initial_cash: float,
    ledger_postings: List[Dict[str, Any]],
    reported_cash: float,
    tolerance: float = 0.10
) -> Tuple[bool, float, float]:
    """
    PHẦN 45, 50: Reconciles cash ledger against reported cash balance.
    Returns (is_reconciled, total_postings, discrepancy).
    """
    total_postings = sum(p.get("amount", 0.0) or p.get("amount_usdt", 0.0) for p in ledger_postings)
    expected_cash = initial_cash + total_postings
    discrepancy = abs(reported_cash - expected_cash)
    is_reconciled = discrepancy <= tolerance
    return is_reconciled, round(total_postings, 4), round(discrepancy, 4)


def aggregate_replay_metrics(
    all_trades: List[Any],
    ledger_postings: List[Dict[str, Any]],
    equity_curve: List[Any],
    initial_equity: float = 1000.0,
    reported_cash: Optional[float] = None
) -> Dict[str, Any]:
    """
    PHẦN 47, 48, 49, 52: Aggregates comprehensive replay metrics from partitioned trades.
    """
    closed_trades, open_trades, _ = partition_trade_records(all_trades)
    closed_count = len(closed_trades)
    open_count = len(open_trades)
    fills_count = closed_count + open_count

    wins = 0
    losses = 0
    breakevens = 0
    gross_profit = 0.0
    gross_loss = 0.0
    total_net_pnl = 0.0
    total_fees = 0.0
    valid_r_list = []

    # Breakdown by trade type
    trade_type_breakdown = {}
    type_pnl_map = {}

    for t in closed_trades:
        pnl = getattr(t, "net_pnl", None)
        if pnl is None and isinstance(t, dict):
            pnl = t.get("net_pnl", 0.0)
        pnl = float(pnl or 0.0)
        total_net_pnl += pnl

        fee = getattr(t, "total_fees", None)
        if fee is None and isinstance(t, dict):
            fee = t.get("total_fees", 0.0)
        if fee is None:
            f_en = getattr(t, "fee_entry", 0.0) or 0.0
            f_ex = getattr(t, "fee_exit", 0.0) or 0.0
            fee = f_en + f_ex
        total_fees += float(fee or 0.0)

        if pnl > 0.001:
            wins += 1
            gross_profit += pnl
        elif pnl < -0.001:
            losses += 1
            gross_loss += abs(pnl)
        else:
            breakevens += 1

        r_val = getattr(t, "realized_r", None)
        if r_val is None and isinstance(t, dict):
            r_val = t.get("realized_r")
        if r_val is not None and isinstance(r_val, (int, float)):
            valid_r_list.append(float(r_val))

        etype = getattr(t, "entry_type", None) or (t.get("entry_type") if isinstance(t, dict) else "UNKNOWN") or "UNKNOWN"
        trade_type_breakdown[etype] = trade_type_breakdown.get(etype, 0) + 1
        type_pnl_map[etype] = type_pnl_map.get(etype, 0.0) + pnl

    win_rate_pct = round((wins / closed_count * 100.0), 2) if closed_count > 0 else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0.0 else (None if gross_profit == 0.0 else 999.0)
    expectancy_r = round(sum(valid_r_list) / len(valid_r_list), 2) if valid_r_list else 0.0
    expectancy_usd = round(total_net_pnl / closed_count, 2) if closed_count > 0 else 0.0

    eq_metrics = compute_equity_metrics(equity_curve, initial_equity)

    reconciled = True
    cash_diff = 0.0
    if reported_cash is not None:
        reconciled, _, cash_diff = reconcile_cash_equity(initial_equity, ledger_postings, reported_cash)

    return {
        "closed_count": closed_count,
        "open_positions_count": open_count,
        "fills_count": fills_count,
        "total_trades": closed_count,  # Legacy alias strictly matches closed_count
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "total_net_pnl": round(total_net_pnl, 2),
        "total_fees": round(total_fees, 2),
        "expectancy_r": expectancy_r,
        "expectancy_usd": expectancy_usd,
        "max_drawdown_usdt": eq_metrics["max_drawdown_usdt"],
        "max_drawdown_pct": eq_metrics["max_drawdown_pct"],
        "trade_type_breakdown": trade_type_breakdown,
        "type_pnl_breakdown": {k: round(v, 2) for k, v in type_pnl_map.items()},
        "cash_ledger_reconciled": reconciled,
        "cash_discrepancy": cash_diff
    }


def diagnose_run_difference(
    before_result: Dict[str, Any],
    after_result: Dict[str, Any],
    before_manifest: Optional[Dict[str, Any]] = None,
    after_manifest: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    PHẦN 10: Diagnoses why confirmed setups differed (e.g. 17 vs 1).
    Explains routing difference: CURRENT_BASELINE runs smc_engine on 15M close,
    while NY_ADAPTIVE runs B1 (trend continuation) and B2 (range break retest).
    """
    v_before = before_result.get("strategy_variant") or (before_manifest.get("strategy_variant") if before_manifest else "UNKNOWN")
    v_after = after_result.get("strategy_variant") or (after_manifest.get("strategy_variant") if after_manifest else "UNKNOWN")

    confirmed_before = before_result.get("trade_type_breakdown", {}).get("SMC_CONFIRMED", 0)
    confirmed_after = after_result.get("trade_type_breakdown", {}).get("SMC_CONFIRMED", 0)

    differences = []
    if v_before != v_after:
        differences.append(f"STRATEGY_VARIANT_MISMATCH: before={v_before}, after={v_after}")
        if "CURRENT_BASELINE" in (v_before, v_after) and "NY_ADAPTIVE" in (v_before, v_after):
            differences.append("ROUTING_EXPLANATION: CURRENT_BASELINE uses single-candle 15M close SMC evaluator (historically yielding ~1 confirmed trade in 3M), whereas NY_ADAPTIVE uses multi-timeframe B1/B2 evaluators with Pre-NY range.")

    fills_before = before_result.get("fills_count", 0)
    fills_after = after_result.get("fills_count", 0)
    if fills_before != fills_after:
        differences.append(f"FILLS_COUNT_DIFFERENCE: before={fills_before}, after={fills_after}")

    return {
        "variant_before": v_before,
        "variant_after": v_after,
        "confirmed_before": confirmed_before,
        "confirmed_after": confirmed_after,
        "differences": differences,
        "has_variant_mismatch": v_before != v_after
    }
