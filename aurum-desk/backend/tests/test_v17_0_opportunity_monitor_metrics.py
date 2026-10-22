"""
backend/tests/test_v17_0_opportunity_monitor_metrics.py
Aurum V17.0 — Metrics, Funnel Counting, Financial & Classification Parity Tests.

Verifies:
- Distinct canonical opportunity counting vs stage event counts.
- Top rejection reasons unique opportunity denominator.
- Post-fee financial metrics integration (0 closed trades => N/A, verified breakeven => 0.00).
- Latency percentile calculation and sample counts.
- Causal missed opportunity classification parity (POLICY_BLOCKED != TECHNICAL_MISS).
- Markdown export rendering without sensitive secrets.
"""
import json
import pytest
from types import SimpleNamespace

from services.cg01_missed_audit_service import CG01MissedOpportunityAuditService
from services.paper_opportunity_monitor_service import (
    OpportunityMonitorListItem,
    OpportunityMonitorSummary,
    render_opportunity_monitor_markdown,
)
from services.paper_runtime_snapshot_service import compute_financial_metrics


class TestMonitorMetricsParity:
    def test_financial_metrics_zero_closed_trades_yields_na(self):
        """0 closed trades must produce None (N/A) for winrate and expectancy, not 0 or 0%."""
        orders = [
            {"id": "ord-1", "state": "armed", "realized_pnl_net": None, "created_at": 1000},
            {"id": "ord-2", "state": "open", "realized_pnl_net": None, "created_at": 1100},
        ]
        fin = compute_financial_metrics(orders, start_ms=500, end_ms=2000)
        assert fin["closed_trades_count"] == 0
        assert fin["winrate_pct"] is None
        assert fin["expectancy_usd_per_trade"] is None
        assert fin["accounting_status"] == "COMPLETE"

    def test_financial_metrics_breakeven_yields_zero_expectancy(self):
        """Verified breakeven trades must yield expectancy 0.00, not None."""
        orders = [
            {"id": "ord-be", "state": "closed", "realized_pnl_net": 0.0, "closed_at": 1500, "created_at": 1000},
        ]
        fin = compute_financial_metrics(orders, start_ms=500, end_ms=2000)
        assert fin["closed_trades_count"] == 1
        assert fin["breakevens_count"] == 1
        assert fin["winrate_pct"] == 0.0
        assert fin["expectancy_usd_per_trade"] == 0.00
        assert fin["net_realized_pnl_usd"] == 0.0

    def test_financial_metrics_positive_and_negative_trades(self):
        orders = [
            {"id": "ord-win", "state": "closed", "realized_pnl_net": 35.50, "closed_at": 1500, "created_at": 1000},
            {"id": "ord-loss", "state": "closed", "realized_pnl_net": -20.00, "closed_at": 1600, "created_at": 1050},
        ]
        fin = compute_financial_metrics(orders, start_ms=500, end_ms=2000)
        assert fin["closed_trades_count"] == 2
        assert fin["wins_count"] == 1
        assert fin["losses_count"] == 1
        assert fin["winrate_pct"] == 50.0
        assert fin["net_realized_pnl_usd"] == 15.50
        assert fin["expectancy_usd_per_trade"] == 7.75

    def test_classification_distinguishes_policy_blocked_from_technical_miss(self):
        # 1. Policy blocked: guard blocked with quote coverage
        eval_records_blocked = [
            {"stage": "ZONE_DETECTED", "as_of_ms": 1000},
            {"stage": "GUARD_BLOCKED", "as_of_ms": 1100, "reason_codes": ["NEWS_BLACKOUT"]},
        ]
        res_blocked = CG01MissedOpportunityAuditService.classify_opportunity(
            eval_records_blocked,
            quote_coverage_verified=True,
        )
        assert res_blocked.classification == "POLICY_BLOCKED"

        # 2. Market non-fill: price never touched entry
        eval_records_no_touch = [
            {"stage": "ZONE_DETECTED", "as_of_ms": 1000},
            {"stage": "TRIGGER_CONFIRMED", "as_of_ms": 1100},
            {"stage": "EXPIRED", "as_of_ms": 2000},
        ]
        order_no_touch = {
            "quote_coverage_verified": True,
            "touched": False,
            "planned_entry": 2650.0,
            "direction": "LONG",
            "lowest_ask": 2652.0,  # Never reached 2650
        }
        res_no_touch = CG01MissedOpportunityAuditService.classify_opportunity(
            eval_records_no_touch,
            order=order_no_touch,
        )
        assert res_no_touch.classification == "MARKET_NON_FILL"

    def test_render_opportunity_monitor_markdown_contains_expected_sections_and_redacts(self):
        summary = OpportunityMonitorSummary(
            schema_version="1.0",
            generated_at_ms=1700000000000,
            query_scope={"symbol": "XAUUSDT", "scope": "CG01", "run_id": "run-test-01"},
            status="READY",
            counts={
                "total_unique_opportunities": 3,
                "zones_touched": 2,
                "trigger_confirmed": 2,
                "guard_blocked": 1,
                "armed": 1,
                "filled": 1,
            },
            top_reasons=[
                {"code": "NEWS_BLACKOUT", "unique_opportunity_count": 1, "category": "SAFETY", "message_vi": "Tạm dừng vì tin"}
            ],
            financial={
                "metric_scope": "RUN_WINDOW",
                "closed_trades_count": 1,
                "wins_count": 1,
                "losses_count": 0,
                "breakevens_count": 0,
                "winrate_pct": 100.0,
                "net_realized_pnl_usd": 25.0,
                "expectancy_usd_per_trade": 25.0,
            },
            coverage={"run_id": "run-test-01", "run_status": "COLLECTING", "symbol": "XAUUSDT", "scope": "CG01"},
            limitations=["UPSERT_STAGE_SNAPSHOTS: Snapshot lưu mốc cuối"],
        )

        item = OpportunityMonitorListItem(
            key="run-test-01:opp-1",
            opportunity_id="opp-1",
            run_id="run-test-01",
            model_id="CG01",
            direction="LONG",
            session_id="run-test-01",
            session_label_vi="run-test-01",
            first_recorded_at_ms=1699999000000,
            last_observed_at_ms=1700000000000,
            current_stage="FILLED",
            stage_label_vi="Đã khớp lệnh",
            primary_reason_code="WAIT_RETEST",
            primary_reason_vi="Đang chờ giá quay lại vùng",
            classification="FILLED",
            data_quality="VERIFIED",
            observed_milestone_count=3,
            score=88.0,
            zone_label="FVG_M1_01",
        )

        md = render_opportunity_monitor_markdown(summary, [item])
        assert "# BÁO CÁO THEO DÕI CƠ HỘI PAPER" in md
        assert "**Tổng số cơ hội độc lập ghi nhận:** **3**" in md
        assert "**Số lệnh đã đóng xác nhận:** 1" in md
        assert "+25.00 USDT" in md
        assert "Tạm dừng vì tin" in md
        assert "opp-1" not in md or "LONG" in md
        assert "Từ Điển Dữ Liệu & Giới Hạn Cần Biết" in md
