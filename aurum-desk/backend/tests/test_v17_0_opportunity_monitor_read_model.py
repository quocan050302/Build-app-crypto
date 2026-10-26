"""
backend/tests/test_v17_0_opportunity_monitor_read_model.py
Aurum V17.0 — Unit test suite for read-only paper opportunity monitor read model.

Verifies:
- Context validation (half-open window, 31 days max, limit bounding).
- Stage row normalization (safe JSON parsing, malformed handling, no coercion of missing direction to LONG).
- Grouping logic (canonical identity deduplication, terminal stage resolution, no fake rank overriding invalidation).
- Order link evidence association (instrument validation, dangling links, chart target).
"""
import json
import pytest
from types import SimpleNamespace

from services.paper_opportunity_monitor_service import (
    MonitorChartTarget,
    encode_cursor,
    decode_cursor,
    get_reason_description_vi,
    get_stage_title_vi,
    group_monitor_opportunities,
    make_monitor_query_context,
    normalize_monitor_stage_row,
)


class TestOpportunityMonitorContext:
    def test_make_monitor_query_context_defaults(self):
        ctx = make_monitor_query_context(now_ms=1700000000000)
        assert ctx.symbol == "XAUUSDT"
        assert ctx.scope == "CG01"
        assert ctx.limit == 20
        assert ctx.now_ms == 1700000000000
        assert ctx.end_ms == 1700000000000

    def test_make_monitor_query_context_valid_window(self):
        ctx = make_monitor_query_context(
            start_ms=1000,
            end_ms=5000,
            limit=35,
            now_ms=6000,
        )
        assert ctx.start_ms == 1000
        assert ctx.end_ms == 5000
        assert ctx.limit == 35

    def test_make_monitor_query_context_invalid_window_raises(self):
        with pytest.raises(ValueError, match="end_ms .* must be strictly greater than start_ms"):
            make_monitor_query_context(start_ms=5000, end_ms=4000)

        with pytest.raises(ValueError, match="end_ms .* must be strictly greater than start_ms"):
            make_monitor_query_context(start_ms=5000, end_ms=5000)

    def test_make_monitor_query_context_exceeds_31_days_raises(self):
        ms_in_32_days = 32 * 24 * 60 * 60 * 1000
        with pytest.raises(ValueError, match="Query window cannot exceed 31 days"):
            make_monitor_query_context(start_ms=0, end_ms=ms_in_32_days)

    def test_make_monitor_query_context_clamps_limit(self):
        ctx_low = make_monitor_query_context(limit=0)
        assert ctx_low.limit == 20

        ctx_high = make_monitor_query_context(limit=100)
        assert ctx_high.limit == 50

    def test_cursor_encode_decode(self):
        cur = encode_cursor(1700000500, "opp-xyz-123")
        assert isinstance(cur, str)
        decoded = decode_cursor(cur)
        assert decoded == (1700000500, "opp-xyz-123")

        assert decode_cursor(None) is None
        assert decode_cursor("invalid-base64-!!!") is None


class TestOpportunityMonitorNormalization:
    def test_normalize_valid_stage_row(self):
        row = SimpleNamespace(
            opportunity_id="opp-001",
            stage="TRIGGER_CONFIRMED",
            symbol="XAUUSDT",
            model_id="CG01_SWEEP_MSS_FVG",
            direction="SHORT",
            session_id="run-session-a",
            run_id="run-session-a",
            zone_id="zone-fvg-01",
            as_of_ms=1700000100,
            created_at=1700000050,
            updated_at=1700000100,
            reason_codes_json=json.dumps(["WAIT_RETEST", "WAIT_FVG"]),
            evidence_ids_json=json.dumps(["ev-1", "ev-2"]),
            metrics_json=json.dumps({"evaluation_started_at": 100, "evaluation_completed_at": 120}),
            payload_json=json.dumps({
                "planned_entry": 2650.0,
                "stop_loss": 2660.0,
                "take_profit": 2626.0,
                "score": 85.0,
            }),
        )

        norm = normalize_monitor_stage_row(row)
        assert norm.opportunity_id == "opp-001"
        assert norm.stage == "TRIGGER_CONFIRMED"
        assert norm.stage_label_vi == "Trigger đã xác nhận"
        assert norm.direction == "SHORT"
        assert norm.reason_codes == ["WAIT_RETEST", "WAIT_FVG"]
        assert norm.primary_reason_code == "WAIT_RETEST"
        assert norm.evidence_ids == ["ev-1", "ev-2"]
        assert norm.planned_entry == 2650.0
        assert norm.score == 85.0
        assert norm.data_quality == "VERIFIED"
        assert len(norm.limitations) == 0

    def test_normalize_malformed_json_fallback(self):
        row = SimpleNamespace(
            opportunity_id="opp-bad-json",
            stage="ZONE_DETECTED",
            symbol="XAUUSDT",
            model_id="CG01",
            direction="",  # missing direction
            session_id="run-1",
            run_id="run-1",
            zone_id="zone-1",
            as_of_ms=1700000100,
            created_at=1700000100,
            updated_at=1700000100,
            reason_codes_json="invalid json syntax {[[",
            evidence_ids_json=None,
            metrics_json="bad-metrics",
            payload_json="corrupted json string",
        )

        norm = normalize_monitor_stage_row(row)
        assert norm.opportunity_id == "opp-bad-json"
        assert norm.direction == "UNKNOWN"  # Rule: never coerce missing direction to LONG
        assert norm.data_quality == "PARTIAL"
        assert any("MALFORMED_STAGE_PAYLOAD" in lim for lim in norm.limitations)
        assert norm.planned_entry is None


class TestOpportunityMonitorGrouping:
    def test_group_single_opportunity_multiple_stages_no_duplication(self):
        s1 = normalize_monitor_stage_row(SimpleNamespace(
            opportunity_id="opp-canon-1",
            stage="ZONE_DETECTED",
            symbol="XAUUSDT",
            model_id="CG01",
            direction="LONG",
            session_id="run-a",
            run_id="run-a",
            zone_id="poi-1",
            as_of_ms=1000,
            created_at=1000,
            updated_at=1000,
            reason_codes_json=json.dumps(["WAIT_SWEEP"]),
            evidence_ids_json=json.dumps([]),
            metrics_json=None,
            payload_json=json.dumps({"planned_entry": 2650.0, "score": 75.0}),
        ))

        s2 = normalize_monitor_stage_row(SimpleNamespace(
            opportunity_id="opp-canon-1",
            stage="TRIGGER_CONFIRMED",
            symbol="XAUUSDT",
            model_id="CG01",
            direction="LONG",
            session_id="run-a",
            run_id="run-a",
            zone_id="poi-1",
            as_of_ms=2000,
            created_at=2000,
            updated_at=2000,
            reason_codes_json=json.dumps(["WAIT_RETEST"]),
            evidence_ids_json=json.dumps([]),
            metrics_json=None,
            payload_json=json.dumps({"planned_entry": 2650.0, "score": 82.0}),
        ))

        items = group_monitor_opportunities([s1, s2])
        assert len(items) == 1  # Exactly 1 unique opportunity, not 2
        item = items[0]
        assert item.opportunity_id == "opp-canon-1"
        assert item.observed_milestone_count == 2
        assert item.first_recorded_at_ms == 1000
        assert item.last_observed_at_ms == 2000
        assert item.current_stage == "TRIGGER_CONFIRMED"
        assert item.primary_reason_code == "WAIT_RETEST"
        assert item.score == 82.0

    def test_group_stage_invalidation_after_armed_is_preserved(self):
        """Rule: Never let an older ARMED override a newer INVALIDATED / EXPIRED."""
        s_armed = normalize_monitor_stage_row(SimpleNamespace(
            opportunity_id="opp-inval",
            stage="ARMED",
            symbol="XAUUSDT",
            model_id="CG01",
            direction="SHORT",
            session_id="run-a",
            run_id="run-a",
            zone_id="poi-2",
            as_of_ms=3000,
            created_at=3000,
            updated_at=3000,
            reason_codes_json=json.dumps(["ARMED"]),
            evidence_ids_json=json.dumps([]),
            metrics_json=None,
            payload_json=json.dumps({"planned_entry": 2650.0}),
        ))

        s_inval = normalize_monitor_stage_row(SimpleNamespace(
            opportunity_id="opp-inval",
            stage="INVALIDATED",
            symbol="XAUUSDT",
            model_id="CG01",
            direction="SHORT",
            session_id="run-a",
            run_id="run-a",
            zone_id="poi-2",
            as_of_ms=4500,
            created_at=4500,
            updated_at=4500,
            reason_codes_json=json.dumps(["TRIGGER_EXPIRED"]),
            evidence_ids_json=json.dumps([]),
            metrics_json=None,
            payload_json=json.dumps({"planned_entry": 2650.0}),
        ))

        items = group_monitor_opportunities([s_armed, s_inval])
        assert len(items) == 1
        assert items[0].current_stage == "INVALIDATED"
        assert items[0].stage_label_vi == "Kế hoạch bị hủy"
        assert items[0].primary_reason_code == "TRIGGER_EXPIRED"

    def test_group_upsert_overwrite_limitation_recorded(self):
        """Rule: When updated_at != created_at, mark limitation about UPSERT in-place overwrite."""
        s_upserted = normalize_monitor_stage_row(SimpleNamespace(
            opportunity_id="opp-upsert",
            stage="ZONE_WATCHED",
            symbol="XAUUSDT",
            model_id="CG01",
            direction="LONG",
            session_id="run-a",
            run_id="run-a",
            zone_id="poi-3",
            as_of_ms=2500,
            created_at=1000,
            updated_at=2500,
            reason_codes_json=json.dumps(["WAIT_SWEEP"]),
            evidence_ids_json=json.dumps([]),
            metrics_json=None,
            payload_json=json.dumps({}),
        ))

        items = group_monitor_opportunities([s_upserted])
        assert len(items) == 1
        assert any("UPSERT_SNAPSHOT_OVERWRITE" in lim for lim in items[0].limitations)
        assert items[0].data_quality == "PARTIAL"
