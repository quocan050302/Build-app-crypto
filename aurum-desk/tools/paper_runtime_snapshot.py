"""
tools/paper_runtime_snapshot.py
V16.39.9 — Forward Paper Runtime Observation & Snapshot Tool

Strictly Read-Only (Rules 091-170):
- Never mutates trading configurations or user auto authority.
- Never creates spurious runs (create_if_missing=False).
- Never sends live Bitget trading orders or modifies balances.
- Does not start secondary bots or background schedulers.
- Supports both HTTP live backend query and offline SQLite read-only snapshot.
- Enforces honest post-fee accounting and N/A winrate on zero closed trades.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

# Add backend directory to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

logger = logging.getLogger("paper_runtime_snapshot")


def get_git_commit() -> str:
    """Retrieves current git commit hash."""
    try:
        res = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return res
    except Exception:
        return "UNKNOWN_COMMIT"


def load_runtime_sources(
    url: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Rule 154, 155: Load runtime information either from an HTTP backend or read-only SQLite database.
    Does NOT start background loops, workers, or migrations.
    """
    if url:
        import urllib.error
        import urllib.request

        base_url = url.rstrip("/")
        sources = {
            "mode": "HTTP",
            "url": base_url,
            "connected": False,
            "data": {},
        }
        endpoints = {
            "health": f"{base_url}/health",
            "watchlist": f"{base_url}/api/scalp/watchlist?symbol=XAUUSDT",
            "evaluation": f"{base_url}/api/scalp/cg01/evaluation?symbol=XAUUSDT",
            "funnel": f"{base_url}/api/scalp/funnel",
            "daily_status": f"{base_url}/api/scalp/daily-status",
        }
        for name, ep_url in endpoints.items():
            try:
                req = urllib.request.Request(ep_url, headers={"User-Agent": "AurumDesk-Snapshot/1.0"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status == 200:
                        sources["data"][name] = json.loads(resp.read().decode())
                        sources["connected"] = True
            except Exception as e:
                sources["data"][name] = {"error": str(e)}

        if not sources["connected"]:
            sources["status"] = "RUNTIME_NOT_AVAILABLE"
        else:
            sources["status"] = "CONNECTED"
        return sources

    # Offline / direct database mode
    target_db = db_path
    if not target_db:
        candidate_db = BACKEND_DIR / "data" / "trading.db"
        if candidate_db.exists():
            target_db = str(candidate_db)

    if not target_db or not Path(target_db).exists():
        return {
            "mode": "NONE",
            "status": "RUNTIME_NOT_AVAILABLE",
            "detail": f"Database file not found: {target_db or 'no path specified'}",
            "db": None,
        }

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_abs = Path(target_db).resolve()
    # Read-only URI to guarantee no writes (Rule 107, 165)
    db_uri = f"sqlite:///file:{db_abs.as_posix()}?mode=ro&uri=true"
    try:
        engine = create_engine(db_uri, connect_args={"check_same_thread": False})
        SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = SessionFactory()
        return {
            "mode": "OFFLINE_DB",
            "status": "CONNECTED",
            "db_path": str(db_abs),
            "engine": engine,
            "session": session,
        }
    except Exception as e:
        return {
            "mode": "OFFLINE_DB",
            "status": "RUNTIME_NOT_AVAILABLE",
            "detail": f"Failed to connect to SQLite in read-only mode: {e}",
            "db": None,
        }


def collect_existing_run_summary(
    sources: Dict[str, Any],
    run_id: Optional[str] = None,
    create_if_missing: bool = False,
) -> Dict[str, Any]:
    """
    Rules 094, 095, 109, 161: Read existing run summary without creating spurious runs.
    """
    mode = sources.get("mode")
    if mode == "OFFLINE_DB" and sources.get("session"):
        session = sources["session"]
        from services.paper_research_run_service import (
            PaperResearchRunService,
            build_cg01_paper_run_summary,
            resolve_active_observation_run,
        )

        run = None
        if run_id:
            run = PaperResearchRunService.get_or_create_run(
                run_id=run_id,
                db=session,
                create_if_missing=False,
            )
        else:
            run = resolve_active_observation_run(db=session, scope="CG01", symbol="XAUUSDT")

        if not run:
            return {
                "status": "NOT_FOUND",
                "run_id": run_id or "NONE_ACTIVE",
                "total_closed_trades": 0,
                "net_realized_pnl_usd": 0.0,
                "winrate_pct": None,
                "detail": "No active or specified PaperResearchRun record found in database.",
            }

        summary = build_cg01_paper_run_summary(run.run_id, db=session, synthetic_trades=None)
        summary["status"] = run.status
        summary["execution_mode"] = run.execution_mode
        summary["config_fingerprint"] = run.config_fingerprint
        return summary

    if mode == "HTTP":
        # In HTTP mode, evaluation endpoint may report active run metadata
        eval_data = sources.get("data", {}).get("evaluation", {})
        return {
            "status": "OBSERVED_VIA_HTTP",
            "run_id": eval_data.get("run_id") or run_id or "REMOTE_ACTIVE",
            "total_closed_trades": eval_data.get("total_closed_trades", 0),
            "net_realized_pnl_usd": eval_data.get("net_realized_pnl_usd", 0.0),
            "winrate_pct": eval_data.get("winrate_pct"),
            "raw": eval_data,
        }

    return {
        "status": "NOT_FOUND",
        "run_id": run_id or "NONE",
        "total_closed_trades": 0,
        "net_realized_pnl_usd": 0.0,
        "winrate_pct": None,
    }


def collect_funnel_summary(
    sources: Dict[str, Any],
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Rules 116-120: Summarize funnel stages, latencies, and classify rejection reasons.
    """
    from services.ny_quota_helpers import classify_reason

    mode = sources.get("mode")
    raw_funnel = {}

    if mode == "OFFLINE_DB" and sources.get("session"):
        session = sources["session"]
        from services.opportunity_funnel_service import OpportunityFunnelService
        raw_funnel = OpportunityFunnelService.summarize_opportunity_funnel(
            session, start_ms=start_ms, end_ms=end_ms
        )
    elif mode == "HTTP":
        raw_funnel = sources.get("data", {}).get("funnel", {})

    top_reasons_raw = raw_funnel.get("top_rejection_reasons", [])
    top_reasons_enriched = []
    for r in top_reasons_raw:
        code = r.get("code")
        count = r.get("count", 0)
        cat, msg_vi = classify_reason(code)
        top_reasons_enriched.append({
            "code": code,
            "count": count,
            "category": cat,
            "message_vi": msg_vi,
        })

    return {
        "total_unique_opportunities": raw_funnel.get("total_unique_opportunities", 0),
        "zones_detected": raw_funnel.get("zones_detected", 0),
        "zones_watched": raw_funnel.get("zones_watched", 0),
        "zones_touched": raw_funnel.get("zones_touched", 0),
        "trigger_confirmed": raw_funnel.get("trigger_confirmed", 0),
        "plans_valid": raw_funnel.get("plans_valid", 0),
        "guard_blocked": raw_funnel.get("guard_blocked", 0),
        "armed": raw_funnel.get("armed", 0),
        "filled": raw_funnel.get("filled", 0),
        "expired": raw_funnel.get("expired", 0),
        "invalidated": raw_funnel.get("invalidated", 0),
        "top_rejection_reasons": top_reasons_enriched,
        "latency_metrics": raw_funnel.get("latency_metrics", {
            "sample_count": 0,
            "median_ms": None,
            "p95_ms": None,
        }),
    }


def build_runtime_snapshot(
    sources: Dict[str, Any],
    run_id: Optional[str] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Rules 154, 156: Assembles the canonical JSON snapshot structure.
    Strictly adheres to:
    - Redaction of secrets (Rule 108).
    - Offline worker health is UNKNOWN (Rule 155).
    - Zero closed trades => winrate and expectancy are None (Rule 141-143, 163).
    """
    now_ms = int(time.time() * 1000)
    commit_sha = get_git_commit()
    source_mode = sources.get("mode", "UNKNOWN")
    source_status = sources.get("status", "UNKNOWN")

    limitations: List[str] = []

    # 1. Effective Config & Policy
    effective_config: Dict[str, Any] = {}
    auto_enabled = False
    worker_health: Dict[str, Any] = {"status": "UNKNOWN", "running": False}

    if source_mode == "OFFLINE_DB" and sources.get("session"):
        session = sources["session"]
        from services.scalp_m1_types import load_effective_scalp_config
        from services.trading_policy_service import resolve_effective_daily_policy
        from services.strategy_service import strategy_service

        try:
            cfg = load_effective_scalp_config(session)
            eff_pol = resolve_effective_daily_policy(session)
            auto_enabled = strategy_service.get_auto_state(session)

            effective_config = {
                "execution_profile": getattr(cfg, "execution_profile", "CG01"),
                "cg01_enabled": getattr(cfg, "cg01_enabled", True),
                "cg01_profile_mode": getattr(cfg, "cg01_profile_mode", "PAPER_EXECUTION"),
                "timing_profile": getattr(cfg, "timing_profile", "CG01_NY_PAPER_EXPANDED"),
                "min_net_rr": getattr(cfg, "min_net_rr", 1.5),
                "quality_scoring_enabled": getattr(cfg, "quality_scoring_enabled", True),
                "quality_arm_threshold": getattr(cfg, "quality_arm_threshold", 70.0),
                "lesson_score_adjustment_enabled": getattr(cfg, "lesson_score_adjustment_enabled", True),
                "daily_max_cap": eff_pol.max_fills,
                "daily_min_goal": eff_pol.min_goal,
                "ny_min_fills": eff_pol.ny_goal,
                "reserve_ny_slot": eff_pol.reserve_ny_slot,
                "auto_enabled": auto_enabled,
            }
        except Exception as e:
            effective_config = {"error": f"Failed to load config: {e}"}

        # Offline mode cannot observe running process health truthfully
        worker_health = {
            "status": "UNKNOWN",
            "running": False,
            "source": "OFFLINE_DB_READ_ONLY",
            "detail": "Worker process health cannot be determined from offline database alone.",
        }
        limitations.append("OFFLINE_MODE: Worker process health is UNKNOWN. Report reflects persisted DB state only.")

    elif source_mode == "HTTP" and source_status == "CONNECTED":
        h_data = sources.get("data", {}).get("health", {})
        worker_health = {
            "status": h_data.get("status") or ("RUNNING" if h_data.get("running") else "STOPPED"),
            "running": bool(h_data.get("running", False)),
            "source": "HTTP_HEALTH_ENDPOINT",
        }
        daily_st = sources.get("data", {}).get("daily_status", {})
        effective_config = {
            "daily_max_cap": daily_st.get("cap", 3),
            "daily_min_goal": daily_st.get("min_goal", 2),
            "ny_goal": daily_st.get("ny_goal", 1),
            "reservation_state": daily_st.get("reservation_state", "NONE"),
        }
    else:
        limitations.append("RUNTIME_NOT_AVAILABLE: No active backend connection or readable database file.")

    # 2. Collect Run Summary & Funnel Summary
    run_summary = collect_existing_run_summary(sources, run_id=run_id)
    funnel_summary = collect_funnel_summary(sources, start_ms=start_ms, end_ms=end_ms)

    # 3. Financial Metrics & Trade Outcomes
    trades_info = run_summary.get("trades", {})
    total_closed = int(trades_info.get("total_closed_trades", 0) or 0)
    net_pnl_usd = float(run_summary.get("financial_metrics_post_fee", {}).get("net_realized_pnl_usd", 0.0) or 0.0)

    winrate_pct = None
    expectancy_usd = None
    if total_closed > 0:
        winrate_pct = trades_info.get("winrate_pct")
        expectancy_usd = round(net_pnl_usd / total_closed, 2)
    else:
        limitations.append("INSUFFICIENT_SAMPLE: 0 closed trades verified. Winrate and expectancy are N/A.")

    if not auto_enabled and source_mode == "OFFLINE_DB":
        limitations.append("AUTO_OFF: Automated trading authority is currently OFF. Orders will not be auto-placed.")

    if worker_health.get("status") == "STOPPED":
        limitations.append("WORKER_STOPPED: Evaluation worker is currently STOPPED.")

    duration_minutes = 0.0
    dur_hours = run_summary.get("duration_hours")
    if dur_hours is not None:
        duration_minutes = round(dur_hours * 60.0, 1)

    overall_status = "RUNNING"
    if source_status == "RUNTIME_NOT_AVAILABLE":
        overall_status = "RUNTIME_NOT_AVAILABLE"
    elif worker_health.get("status") == "STOPPED":
        overall_status = "STOPPED"
    elif run_summary.get("status") == "NOT_FOUND":
        overall_status = "NOT_CONFIGURED"
    elif worker_health.get("status") == "UNKNOWN":
        overall_status = "SNAPSHOT_READY"

    return {
        "schema_version": "1.0",
        "generated_at": now_ms,
        "source_mode": source_mode,
        "status": overall_status,
        "runtime_commit": commit_sha,
        "time_window": {
            "start_ms": start_ms,
            "end_ms": end_ms or now_ms,
        },
        "effective_config": effective_config,
        "worker_health": worker_health,
        "run_summary": run_summary,
        "funnel_summary": funnel_summary,
        "metrics": {
            "duration_minutes": duration_minutes,
            "opportunities_count": funnel_summary.get("total_unique_opportunities", 0),
            "orders_count": trades_info.get("total_trades_count", 0),
            "open_positions_count": trades_info.get("open_trades_count", 0),
            "armed_orders_count": trades_info.get("armed_orders_count", 0),
            "closed_trades_count": total_closed,
            "net_realized_pnl_usd": net_pnl_usd,
            "winrate_pct": winrate_pct,
            "expectancy_usd_per_trade": expectancy_usd,
        },
        "limitations": limitations,
    }


def render_markdown(snapshot: Dict[str, Any]) -> str:
    """
    Rules 157, 158: Renders a concise, beginner-friendly Markdown report.
    """
    metrics = snapshot.get("metrics", {})
    worker = snapshot.get("worker_health", {})
    cfg = snapshot.get("effective_config", {})
    funnel = snapshot.get("funnel_summary", {})
    limitations = snapshot.get("limitations", [])
    run_sum = snapshot.get("run_summary", {})

    status = snapshot.get("status", "UNKNOWN")
    dur_min = metrics.get("duration_minutes", 0)
    opps = metrics.get("opportunities_count", 0)
    orders = metrics.get("orders_count", 0)
    pnl = metrics.get("net_realized_pnl_usd", 0.0)
    winrate = metrics.get("winrate_pct")
    winrate_str = f"{winrate}%" if winrate is not None else "N/A (chưa đủ mẫu lệnh đóng)"

    # Rule 158: Beginner summary lines
    beginner_summary = (
        f"**Đã quan sát:** {dur_min} phút · {opps} cơ hội · {orders} lệnh.\n"
        f"**Lời/lỗ sau phí:** {pnl:+.2f} USD.\n"
        f"**Tỷ lệ thắng:** {winrate_str}."
    )

    top_reasons = funnel.get("top_rejection_reasons", [])
    reasons_table = []
    if top_reasons:
        reasons_table.append("| Mã chặn | Số lần | Nhóm | Giải thích |")
        reasons_table.append("| :--- | :--- | :--- | :--- |")
        for r in top_reasons:
            reasons_table.append(
                f"| `{r['code']}` | {r['count']} | {r['category']} | {r['message_vi']} |"
            )
    else:
        reasons_table.append("*(Chưa ghi nhận sự kiện chặn trong khoảng thời gian này)*")

    limitations_list = "\n".join(f"- {item}" for item in limitations) if limitations else "- Không có cảnh báo hạn chế."

    md = f"""# Báo Cáo Quan Sát Vận Hành PAPER (V16.39.9)

> {beginner_summary}

---

## 1. Trạng Thái Vận Hành
- **Tình trạng tổng thể**: `{status}`
- **Nguồn dữ liệu**: `{snapshot.get('source_mode')}`
- **Commit mã nguồn**: `{snapshot.get('runtime_commit')[:12]}`
- **Worker thực thi**: `{worker.get('status')}` (Đang chạy: `{worker.get('running')}`)
- **Quyền tự động (Auto Trading)**: `{'BẬT (ON)' if cfg.get('auto_enabled') else 'TẮT (OFF)'}`

## 2. Phễu Cơ Hội & Thực Thi (Opportunity Funnel)
- Vùng quét (Zones Detected): **{funnel.get('zones_detected', 0)}**
- Chờ kích hoạt (Trigger Confirmed): **{funnel.get('trigger_confirmed', 0)}**
- Kế hoạch hợp lệ (Plans Valid): **{funnel.get('plans_valid', 0)}**
- Lệnh đã đặt chờ (ARMED): **{funnel.get('armed', 0)}**
- Lệnh đã khớp (FILLED): **{funnel.get('filled', 0)}**
- Vị thế đang mở (Open Positions): **{metrics.get('open_positions_count', 0)}**

### Lý do chặn/bỏ lỡ chính:
{chr(10).join(reasons_table)}

## 3. Kết Quả Tài Chính Sau Phí (Post-Fee Performance)
- **Tổng lệnh đã đóng (Closed Trades)**: `{metrics.get('closed_trades_count', 0)}`
- **Lợi nhuận ròng (Net Realized PnL)**: `{pnl:+.2f} USD`
- **Tỷ lệ thắng (Winrate)**: `{winrate_str}`
- **Kỳ vọng bình quân (Expectancy)**: `{metrics.get('expectancy_usd_per_trade') or 'N/A'} USD/lệnh`

## 4. Hạn Chế & Lưu Ý Nghiệm Thu (Limitations)
{limitations_list}

## 5. Bước Tiếp Theo
1. Nếu quyền Auto đang `OFF`, giữ nguyên quan sát hoặc bật thủ công khi người dùng yêu cầu.
2. Không cưỡng ép lệnh (No forced trades) để đạt chỉ tiêu ngày.
3. Khi phiên Mỹ (NY) mở cửa, chạy snapshot để quan sát các vùng FVG và phản ứng giá thực tế.
"""
    return md


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Aurum Desk V16.39.9 Forward Paper Runtime Snapshot Tool")
    parser.add_argument("--url", type=str, default=None, help="HTTP Backend URL (e.g. http://127.0.0.1:8000)")
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite database file for offline read-only snapshot")
    parser.add_argument("--run-id", type=str, default=None, help="Specific observation run ID")
    parser.add_argument("--start-ms", type=int, default=None, help="Start epoch timestamp in ms")
    parser.add_argument("--end-ms", type=int, default=None, help="End epoch timestamp in ms")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to save snapshot JSON and Markdown files")
    parser.add_argument("--format", type=str, choices=["json", "markdown", "both"], default="both", help="Output format")

    args = parser.parse_args()

    sources = load_runtime_sources(url=args.url, db_path=args.db)
    snapshot = build_runtime_snapshot(
        sources,
        run_id=args.run_id,
        start_ms=args.start_ms,
        end_ms=args.end_ms,
    )

    md_output = render_markdown(snapshot)
    json_output = json.dumps(snapshot, indent=2, ensure_ascii=False)

    if args.output_dir:
        out_path = Path(args.output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "paper_runtime_snapshot.json").write_text(json_output, encoding="utf-8")
        (out_path / "paper_runtime_snapshot.md").write_text(md_output, encoding="utf-8")
        print(f"Snapshot written to {out_path.resolve()}/paper_runtime_snapshot.[json|md]")
    else:
        if args.format == "json":
            print(json_output)
        elif args.format == "markdown":
            print(md_output)
        else:
            print("=== PAPER RUNTIME SNAPSHOT (JSON) ===")
            print(json_output)
            print("\n=== PAPER RUNTIME SNAPSHOT (MARKDOWN) ===")
            print(md_output)

    # Clean up DB session if opened
    if sources.get("session"):
        try:
            sources["session"].close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
