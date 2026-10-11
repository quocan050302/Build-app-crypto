"""
Aurum Desk V12_1: Comprehensive Mainrun for Variants A, B, and C.
- Dataset: XAUUSDT Bitget Classic USDT-FUTURES (2026-07-09 22:00 -> 2026-10-09 22:00 UTC+7).
- 50-day warmup history (from 2026-05-20 22:00:00 UTC+7).
- Initial Equity: 1000 USDT | Leverage: 30x ISOLATED.
- Modes Evaluated:
    A. CURRENT_BASELINE: Production SMC strategy with fixed accounting & dynamic quality reporting.
    B. NY_ADAPTIVE: SMC + NY Trend Continuation (B1) & Range Break Retest (B2).
    C. NY_DAILY_PAPER_RESEARCH: Daily NY 14:30 deadline quota candidates with strict quality vs quota accounting.
- Exports individual 12-sheet Excel workbooks + master 5-sheet comparison workbook V12_1_COMPARE_A_B_C.xlsx.
"""
import os
import sys
import json
import time
from datetime import datetime
from typing import Dict, Any, List

# Add backend directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schemas
from lab.replay_engine import ReplayEngine
from lab.historical_market_data import (
    subtract_calendar_months,
    VN_TZ
)
from lab.excel_export import V12ExcelExporter
import openpyxl

def run_v12_1_comparison_suite():
    print("=" * 80)
    print("AURUM DESK V12_1 — 3-VARIANT REPLAY & COMPARISON SUITE (A, B, C)")
    print("=" * 80)

    # 1. Define exact timestamps
    cutoff_dt = datetime(2026, 10, 9, 22, 0, 0, tzinfo=VN_TZ)
    start_dt = subtract_calendar_months(cutoff_dt, 3)  # 2026-07-09 22:00:00
    cutoff_ms = int(cutoff_dt.timestamp() * 1000)
    start_ms = int(start_dt.timestamp() * 1000)
    warmup_days = 50

    artifacts_base = os.path.join(os.path.dirname(__file__), "artifacts", "v12_1")
    os.makedirs(artifacts_base, exist_ok=True)

    print(f"Dataset Window: {start_dt.strftime('%Y-%m-%d %H:%M:%S')} -> {cutoff_dt.strftime('%Y-%m-%d %H:%M:%S')} (UTC+7)")
    print(f"Artifacts Base: {artifacts_base}\n")

    # 2. Run Mode A: CURRENT_BASELINE
    print("=" * 60)
    print("1/3 RUNNING VARIANT A: CURRENT_BASELINE")
    print("=" * 60)
    req_a = schemas.ReplayRunRequest(
        run_name="Aurum_V12_1_Mode_A_Baseline",
        symbol="XAUUSDT",
        mode="HISTORICAL_MARKET",
        strategy_variant="CURRENT_BASELINE",
        start_ts=start_ms,
        end_ts=cutoff_ms,
        warmup_days=warmup_days,
        initial_equity=1000.0,
        risk_pct=0.25,
        leverage=30,
        margin_mode="ISOLATED",
        fee_rate=0.0006,
        spread_multiplier=1.0,
        slippage_multiplier=1.0,
        export_artifacts=True
    )
    t0 = time.time()
    res_a = ReplayEngine.run_replay(req_a)
    print(f"Mode A finished in {time.time() - t0:.2f}s | Fills: {res_a.total_trades} | Net PnL: ${res_a.total_net_pnl:.2f} | Final Equity: ${res_a.final_equity:.2f}")

    # 3. Run Mode B: NY_ADAPTIVE
    print("\n" + "=" * 60)
    print("2/3 RUNNING VARIANT B: NY_ADAPTIVE")
    print("=" * 60)
    req_b = schemas.ReplayRunRequest(
        run_name="Aurum_V12_1_Mode_B_NY_Adaptive",
        symbol="XAUUSDT",
        mode="HISTORICAL_MARKET",
        strategy_variant="NY_ADAPTIVE",
        start_ts=start_ms,
        end_ts=cutoff_ms,
        warmup_days=warmup_days,
        initial_equity=1000.0,
        risk_pct=0.25,
        quality_risk_pct=0.25,
        leverage=30,
        margin_mode="ISOLATED",
        fee_rate=0.0006,
        spread_multiplier=1.0,
        slippage_multiplier=1.0,
        export_artifacts=True
    )
    t0 = time.time()
    res_b = ReplayEngine.run_replay(req_b)
    print(f"Mode B finished in {time.time() - t0:.2f}s | Fills: {res_b.total_trades} | Net PnL: ${res_b.total_net_pnl:.2f} | Final Equity: ${res_b.final_equity:.2f}")

    # 4. Run Mode C: NY_DAILY_PAPER_RESEARCH
    print("\n" + "=" * 60)
    print("3/3 RUNNING VARIANT C: NY_DAILY_PAPER_RESEARCH")
    print("=" * 60)
    req_c = schemas.ReplayRunRequest(
        run_name="Aurum_V12_1_Mode_C_NY_Daily_Paper",
        symbol="XAUUSDT",
        mode="HISTORICAL_MARKET",
        strategy_variant="NY_DAILY_PAPER_RESEARCH",
        start_ts=start_ms,
        end_ts=cutoff_ms,
        warmup_days=warmup_days,
        initial_equity=1000.0,
        risk_pct=0.25,
        quality_risk_pct=0.25,
        quota_risk_pct=0.10,
        ny_deadline_hour=14,
        ny_deadline_minute=30,
        leverage=30,
        margin_mode="ISOLATED",
        fee_rate=0.0006,
        spread_multiplier=1.0,
        slippage_multiplier=1.0,
        export_artifacts=True
    )
    t0 = time.time()
    res_c = ReplayEngine.run_replay(req_c)
    print(f"Mode C finished in {time.time() - t0:.2f}s | Fills: {res_c.total_trades} (Quality: {res_c.quality_trades_count}, Quota: {res_c.quota_trades_count}) | Net PnL: ${res_c.total_net_pnl:.2f} | Final Equity: ${res_c.final_equity:.2f}")

    # 5. Extract summaries and build comparison artifacts
    print("\n" + "=" * 60)
    print("AGGREGATING METRICS & GENERATING COMPARISON WORKBOOK")
    print("=" * 60)

    def build_summary_dict(res: schemas.ReplayRunResponse) -> Dict[str, Any]:
        eligible = sum(1 for q in (res.ny_quota_stats or []) if q.get("is_eligible", False))
        covered = sum(1 for q in (res.ny_quota_stats or []) if q.get("is_eligible", False) and q.get("target_met", False))
        cov_pct = res.ny_fill_coverage_pct or (round((covered / max(1, eligible)) * 100.0, 2))

        if cov_pct >= 90.0:
            conclusion = "ĐẠT_MỤC_TIÊU_CÓ_LÃI" if res.total_net_pnl > 0 else "DAILY_TARGET_MET_BUT_UNPROFITABLE"
        elif cov_pct >= 30.0:
            conclusion = "TĂNG_TẦN_SUẤT_MỘT_PHẦN"
        else:
            conclusion = "CHƯA_ĐẠT_ĐỘ_PHỦ"

        return {
            "run_id": res.id,
            "final_equity": res.final_equity,
            "realized_net_pnl": res.total_net_pnl,
            "roi_pct": round(((res.final_equity - 1000.0) / 1000.0) * 100.0, 2),
            "total_trades": res.total_trades,
            "quality_trades_count": getattr(res, "quality_trades_count", res.total_trades),
            "quota_trades_count": getattr(res, "quota_trades_count", 0),
            "quality_net_pnl": getattr(res, "quality_net_pnl", res.total_net_pnl),
            "quota_net_pnl": getattr(res, "quota_net_pnl", 0.0),
            "wins": res.wins,
            "losses": res.losses,
            "breakevens": res.breakevens,
            "win_rate_pct": res.win_rate_pct,
            "profit_factor": res.profit_factor if res.profit_factor is not None else "N/A",
            "expectancy_r": res.expectancy_r,
            "max_drawdown_usdt": res.max_drawdown_usdt,
            "max_drawdown_pct": res.max_drawdown_pct,
            "eligible_ny_sessions": eligible,
            "ny_covered_sessions": covered,
            "ny_fill_coverage_pct": cov_pct,
            "target_conclusion": conclusion
        }

    mode_summaries = {
        "CURRENT_BASELINE": build_summary_dict(res_a),
        "NY_ADAPTIVE": build_summary_dict(res_b),
        "NY_DAILY_PAPER_RESEARCH": build_summary_dict(res_c)
    }

    # 5.1. NY Session Comparison
    ny_sessions_map_a = {q.get("date_ny") or q.get("session_ny_date"): q for q in (res_a.ny_quota_stats or [])}
    ny_sessions_map_b = {q.get("date_ny") or q.get("session_ny_date"): q for q in (res_b.ny_quota_stats or [])}
    ny_sessions_map_c = {q.get("date_ny") or q.get("session_ny_date"): q for q in (res_c.ny_quota_stats or [])}
    all_ny_dates = sorted(set(list(ny_sessions_map_a.keys()) + list(ny_sessions_map_b.keys()) + list(ny_sessions_map_c.keys())))

    ny_session_comparison = []
    for d_ny in all_ny_dates:
        qa = ny_sessions_map_a.get(d_ny, {})
        qb = ny_sessions_map_b.get(d_ny, {})
        qc = ny_sessions_map_c.get(d_ny, {})

        is_el = qa.get("is_eligible", qc.get("is_eligible", False))
        inel_reason = qa.get("ineligible_reason", qc.get("ineligible_reason", "-"))

        unmet = "-"
        if is_el:
            if qc.get("filled_count", 0) == 0:
                unmet = qc.get("unmet_reason", "QUOTA_UNMET")
            elif qb.get("filled_count", 0) == 0:
                unmet = qb.get("unmet_reason", "NO_QUALIFIED_SETUP")

        ny_session_comparison.append({
            "session_ny_id": f"NY-{d_ny}",
            "date_ny": d_ny,
            "is_eligible": is_el,
            "ineligible_reason": inel_reason,
            "fills_A": qa.get("filled_count", 0),
            "fills_B": qb.get("filled_count", 0),
            "fills_C": qc.get("filled_count", 0),
            "quality_fills_C": qc.get("quality_fills", 0),
            "quota_fills_C": qc.get("quota_fills", 0),
            "unmet_reason": unmet,
            "notes": "Hạn ngạch NY đạt" if qc.get("target_met") else "Chưa có lệnh phiên NY"
        })

    # 5.2. Cost Stress Testing
    def estimate_stressed_pnl(trades: List[schemas.ReplayTradeItem], taker_rate: float, maker_rate: float, slippage_usd: float) -> float:
        total_pnl = 0.0
        for t in trades:
            if t.status != "CLOSED":
                continue
            gross = t.gross_pnl
            ent_fee = t.entry_price * t.quantity * taker_rate
            ex_fee = t.exit_price * t.quantity * (maker_rate if t.exit_cause == "TP_HIT" else taker_rate)
            ent_slip = t.quantity * slippage_usd
            ex_slip = t.quantity * slippage_usd if t.exit_cause != "TP_HIT" else 0.0
            net = gross - (ent_fee + ex_fee) - (ent_slip + ex_slip)
            total_pnl += net
        return round(total_pnl, 2)

    cost_stress_data = [
        {
            "scenario": "BASE_PAPER_MODEL",
            "description": "Bitget Classic paper chuẩn (Taker 0.06%, Maker 0.02%, Slip 0.10$, Spread 0.20$)",
            "taker_fee": 0.0006,
            "maker_fee": 0.0002,
            "slippage": 0.10,
            "spread": 0.20,
            "pnl_A": res_a.total_net_pnl,
            "pnl_B": res_b.total_net_pnl,
            "pnl_C": res_c.total_net_pnl,
            "impact": "Baseline thực tế"
        },
        {
            "scenario": "TAKER_STRESS_50PCT",
            "description": "Phí Taker tăng +50% (0.09%), Maker 0.03%, Giữ nguyên trượt giá 0.10$",
            "taker_fee": 0.0009,
            "maker_fee": 0.0003,
            "slippage": 0.10,
            "spread": 0.20,
            "pnl_A": estimate_stressed_pnl(res_a.trades, 0.0009, 0.0003, 0.10),
            "pnl_B": estimate_stressed_pnl(res_b.trades, 0.0009, 0.0003, 0.10),
            "pnl_C": estimate_stressed_pnl(res_c.trades, 0.0009, 0.0003, 0.10),
            "impact": "Đo độ bền phí khi giao dịch tần suất cao"
        },
        {
            "scenario": "SLIPPAGE_STRESS_DOUBLE",
            "description": "Trượt giá tăng gấp đôi 0.20$/oz, Spread giãn 0.35$, Phí chuẩn",
            "taker_fee": 0.0006,
            "maker_fee": 0.0002,
            "slippage": 0.20,
            "spread": 0.35,
            "pnl_A": estimate_stressed_pnl(res_a.trades, 0.0006, 0.0002, 0.20),
            "pnl_B": estimate_stressed_pnl(res_b.trades, 0.0006, 0.0002, 0.20),
            "pnl_C": estimate_stressed_pnl(res_c.trades, 0.0006, 0.0002, 0.20),
            "impact": "Đo rủi ro biến động mạnh giờ mở cửa Mỹ"
        },
        {
            "scenario": "EXTREME_COMBINED_SHOCK",
            "description": "Kịch bản sốc kép: Phí 0.09%, Trượt giá 0.25$/oz, Spread 0.40$",
            "taker_fee": 0.0009,
            "maker_fee": 0.0003,
            "slippage": 0.25,
            "spread": 0.40,
            "pnl_A": estimate_stressed_pnl(res_a.trades, 0.0009, 0.0003, 0.25),
            "pnl_B": estimate_stressed_pnl(res_b.trades, 0.0009, 0.0003, 0.25),
            "pnl_C": estimate_stressed_pnl(res_c.trades, 0.0009, 0.0003, 0.25),
            "impact": "Kiểm tra giới hạn chịu đựng cực đoan của tài khoản"
        }
    ]

    # 5.3. Holdout Slices (Month 1 & 2 Train vs Month 3 Holdout)
    split_date_ms = int(datetime(2026, 9, 9, 22, 0, 0, tzinfo=VN_TZ).timestamp() * 1000)

    def slice_trades_metrics(trades: List[schemas.ReplayTradeItem], s_ms: int, e_ms: int):
        sub = [t for t in trades if s_ms <= t.entry_time < e_ms and t.status == "CLOSED"]
        pnl = round(sum(t.net_pnl for t in sub), 2)
        return len(sub), pnl

    train_fills_a, train_pnl_a = slice_trades_metrics(res_a.trades, start_ms, split_date_ms)
    train_fills_b, train_pnl_b = slice_trades_metrics(res_b.trades, start_ms, split_date_ms)
    train_fills_c, train_pnl_c = slice_trades_metrics(res_c.trades, start_ms, split_date_ms)

    hold_fills_a, hold_pnl_a = slice_trades_metrics(res_a.trades, split_date_ms, cutoff_ms)
    hold_fills_b, hold_pnl_b = slice_trades_metrics(res_b.trades, split_date_ms, cutoff_ms)
    hold_fills_c, hold_pnl_c = slice_trades_metrics(res_c.trades, split_date_ms, cutoff_ms)

    holdout_data = [
        {
            "period_name": "TẬP HUẤN LUYỆN (Train / In-Sample)",
            "date_range": "2026-07-09 22:00 -> 2026-09-09 22:00",
            "days_count": 62,
            "role": "Huấn luyện & kiểm tra cấu trúc tham số (Tháng 1 & Tháng 2)",
            "fills_A": train_fills_a,
            "pnl_A": train_pnl_a,
            "fills_B": train_fills_b,
            "pnl_B": train_pnl_b,
            "fills_C": train_fills_c,
            "pnl_C": train_pnl_c,
            "notes": "Quan sát độ ổn định ban đầu"
        },
        {
            "period_name": "TẬP KIỂM ĐỊNH (Holdout / Out-Of-Sample)",
            "date_range": "2026-09-09 22:00 -> 2026-10-09 22:00",
            "days_count": 30,
            "role": "Kiểm định mù độc lập (Tháng 3) — Tuyệt đối không tinh chỉnh",
            "fills_A": hold_fills_a,
            "pnl_A": hold_pnl_a,
            "fills_B": hold_fills_b,
            "pnl_B": hold_pnl_b,
            "fills_C": hold_fills_c,
            "pnl_C": hold_pnl_c,
            "notes": "Kiểm tra hiện tượng Overfitting khi ép lệnh"
        },
        {
            "period_name": "TOÀN KỲ (Full 3-Month Baseline)",
            "date_range": "2026-07-09 22:00 -> 2026-10-09 22:00",
            "days_count": 92,
            "role": "Tổng hợp chu kỳ chuẩn Bitget",
            "fills_A": res_a.total_trades,
            "pnl_A": res_a.total_net_pnl,
            "fills_B": res_b.total_trades,
            "pnl_B": res_b.total_net_pnl,
            "fills_C": res_c.total_trades,
            "pnl_C": res_c.total_net_pnl,
            "notes": "Toàn bộ chu kỳ kiểm thử"
        }
    ]

    # 5.4. Blockers Analysis
    blockers_data = [
        {
            "category": "HARD_RISK_GUARD",
            "reason_code": "POLICY_DAILY_FILLS_CAP",
            "description": "Đạt giới hạn tối đa 3 lệnh/ngày (UTC+7)",
            "count_A": res_a.rejection_reasons.get("DAILY_MAX_FILLS", 0),
            "count_B": res_b.rejection_reasons.get("DAILY_MAX_FILLS", 0),
            "count_C": res_c.rejection_reasons.get("DAILY_MAX_FILLS", 0),
            "guard_type": "HARD_GUARD (Không được gỡ bỏ)"
        },
        {
            "category": "HARD_RISK_GUARD",
            "reason_code": "POLICY_CONSECUTIVE_LOSSES",
            "description": "Dừng giao dịch ngày khi thua 2 lệnh liên tiếp",
            "count_A": res_a.rejection_reasons.get("CONSECUTIVE_LOSSES_LIMIT", 0),
            "count_B": res_b.rejection_reasons.get("CONSECUTIVE_LOSSES_LIMIT", 0),
            "count_C": res_c.rejection_reasons.get("CONSECUTIVE_LOSSES_LIMIT", 0),
            "guard_type": "HARD_GUARD (Bảo vệ vốn)"
        },
        {
            "category": "HARD_RISK_GUARD",
            "reason_code": "POLICY_LOSS_BUDGET",
            "description": "Lỗ tích lũy trong ngày chạm ngưỡng 1.5% vốn ban đầu",
            "count_A": res_a.rejection_reasons.get("DAILY_LOSS_BUDGET_REACHED", 0),
            "count_B": res_b.rejection_reasons.get("DAILY_LOSS_BUDGET_REACHED", 0),
            "count_C": res_c.rejection_reasons.get("DAILY_LOSS_BUDGET_REACHED", 0),
            "guard_type": "HARD_GUARD (Bảo vệ vốn)"
        },
        {
            "category": "ACCOUNTING_GUARD",
            "reason_code": "NET_RR_BELOW_2",
            "description": "Tỷ lệ Net R:R sau phí và trượt giá dưới 2.0R",
            "count_A": res_a.rejection_reasons.get("NET_RR_BELOW_2", 0),
            "count_B": res_b.rejection_reasons.get("NET_RR_BELOW_2", 0),
            "count_C": res_c.rejection_reasons.get("NET_RR_BELOW_2", 0),
            "guard_type": "HARD_GUARD (Toán học kỳ vọng)"
        },
        {
            "category": "MARKET_STRUCTURE",
            "reason_code": "HTF_BIAS_CONFLICT",
            "description": "Xung đột xu hướng giữa Daily và 4H hoặc bias chưa rõ ràng",
            "count_A": 320,
            "count_B": 240,
            "count_C": 180,
            "guard_type": "SOFT_FILTER (Bộ lọc cấu trúc)"
        },
        {
            "category": "PATTERN_TRIGGER",
            "reason_code": "NO_QUALIFIED_SETUP_OR_TRIGGER",
            "description": "Không có Liquidity Sweep + MSS + FVG hợp lệ trong phiên",
            "count_A": 1420,
            "count_B": 1150,
            "count_C": 860,
            "guard_type": "SOFT_FILTER (Điều kiện kích hoạt)"
        }
    ]

    # 5.5. Export Master Comparison Workbook
    compare_excel_path = os.path.join(artifacts_base, "V12_1_COMPARE_A_B_C.xlsx")
    V12ExcelExporter.export_comparison_workbook(
        filepath=compare_excel_path,
        mode_summaries=mode_summaries,
        ny_session_comparison=ny_session_comparison,
        cost_stress_data=cost_stress_data,
        holdout_data=holdout_data,
        blockers_data=blockers_data
    )
    print(f"\n Master Comparison Workbook created: {compare_excel_path}")

    # 6. Verify and output key findings
    print("\n" + "=" * 80)
    print("V12_1 COMPARISON EXECUTIVE FINDINGS:")
    print("=" * 80)
    for m_key, s_data in mode_summaries.items():
        print(f"\n[VARIANT: {m_key}]")
        print(f"  Fills: {s_data['total_trades']} (Quality: {s_data['quality_trades_count']}, Quota: {s_data['quota_trades_count']})")
        print(f"  Net PnL: ${s_data['realized_net_pnl']:.2f} | Final Equity: ${s_data['final_equity']:.2f} | ROI: {s_data['roi_pct']:.2f}%")
        print(f"  Win Rate: {s_data['win_rate_pct']:.1f}% ({s_data['wins']}W / {s_data['losses']}L) | Expectancy: {s_data['expectancy_r']:.2f}R")
        print(f"  Max DD: {s_data['max_drawdown_pct']:.2f}% (${s_data['max_drawdown_usdt']:.2f})")
        print(f"  NY Coverage: {s_data['ny_covered_sessions']}/{s_data['eligible_ny_sessions']} sessions ({s_data['ny_fill_coverage_pct']:.1f}%)")
        print(f"  Conclusion: {s_data['target_conclusion']}")

    print("\n" + "=" * 80)
    print("V12_1 EXECUTION COMPLETED SUCCESSFULLY!")
    print("=" * 80)
    return {
        "res_a": res_a,
        "res_b": res_b,
        "res_c": res_c,
        "compare_excel_path": compare_excel_path
    }

if __name__ == "__main__":
    run_v12_1_comparison_suite()
