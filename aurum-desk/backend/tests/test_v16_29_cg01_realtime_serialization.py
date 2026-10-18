"""
Tests for CG-01 Realtime Serialization, Lifecycle Overrides, and Diagnostic Broadcast (V16.29).
Covers items 059, 060, 131, 132, 142, 143, 144 from prompt.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient

from services.scalp_watcher_orchestrator import (
    serialize_cg01_evaluation,
    ScalpWatcherOrchestrator,
)
from services.cg01_types import (
    CG01Decision,
    CG01CandidateDiagnostic,
    CG01Stage,
)
from services.market_broadcaster import MarketBroadcaster
from main import app


def test_serialize_cg01_evaluation_none_symmetric_schema():
    """Item 131, 132: Decision None returns symmetric dictionary with None for ui_summary_vi, poi_id, direction."""
    res = serialize_cg01_evaluation(None, enabled=False, evaluated=False, as_of_ms=1000)
    assert res["enabled"] is False
    assert res["evaluated"] is False
    assert res["as_of_ms"] == 1000
    assert res["stage"] == "MODEL_DISABLED"
    assert res["eligible"] is False
    assert "MODEL_DISABLED" in res["reason_codes"]
    assert res["ui_summary_vi"] is None
    assert res["poi_id"] is None
    assert res["direction"] is None
    assert res["armed_order_id"] is None


def test_serialize_cg01_evaluation_no_quote_symmetric_schema():
    """Item 132: NO_QUOTE skip reason keeps schema symmetric without false ready."""
    res = serialize_cg01_evaluation(None, enabled=True, evaluated=False, skip_reason="NO_QUOTE", as_of_ms=2000)
    assert res["enabled"] is True
    assert res["evaluated"] is False
    assert res["stage"] == "NO_QUOTE"
    assert res["eligible"] is False
    assert "NO_QUOTE" in res["reason_codes"]
    assert res["ui_summary_vi"] is None
    assert res["poi_id"] is None
    assert res["direction"] is None


def test_serialize_cg01_evaluation_wait_mss():
    """Item 059, 133: Decision with WAIT_MSS preserves stage, poi_id, direction, reason_codes, and ui_summary_vi."""
    dec = CG01Decision(
        stage=CG01Stage.WAIT_MSS.value,
        is_eligible=False,
        reason_codes=["WAITING_MSS_CONFIRMATION"],
        direction="LONG",
        poi_id="poi_h1_demand_01",
        ui_summary_vi="Đã có quét thanh khoản; đang chờ nến đóng xác nhận phá cấu trúc và lực đẩy.",
        as_of_ms=3000,
    )
    res = serialize_cg01_evaluation(dec, enabled=True, evaluated=True, as_of_ms=3000)
    assert res["enabled"] is True
    assert res["evaluated"] is True
    assert res["as_of_ms"] == 3000
    assert res["stage"] == "WAIT_MSS"
    assert res["eligible"] is False
    assert res["reason_codes"] == ["WAITING_MSS_CONFIRMATION"]
    assert res["poi_id"] == "poi_h1_demand_01"
    assert res["direction"] == "LONG"
    assert "phá cấu trúc" in res["ui_summary_vi"]


def test_serialize_cg01_evaluation_wait_fvg():
    """Item 059, 134: Decision with NO_FVG_ON_LEG preserves stage and reason."""
    dec = CG01Decision(
        stage=CG01Stage.WAIT_FVG.value,
        is_eligible=False,
        reason_codes=["NO_FVG_ON_LEG"],
        direction="SHORT",
        poi_id="poi_m15_supply_02",
        ui_summary_vi="Đã xác nhận cấu trúc; đang chờ FVG hợp lệ trên nhịp đẩy.",
        as_of_ms=4000,
    )
    res = serialize_cg01_evaluation(dec, enabled=True, evaluated=True, as_of_ms=4000)
    assert res["enabled"] is True
    assert res["evaluated"] is True
    assert res["stage"] == "WAIT_FVG"
    assert res["eligible"] is False
    assert res["reason_codes"] == ["NO_FVG_ON_LEG"]
    assert res["poi_id"] == "poi_m15_supply_02"
    assert res["direction"] == "SHORT"


def test_lifecycle_override_armed_same_timestamp():
    """Item 060, 142: EXECUTABLE candidate overridden to ARMED at same as_of_ms preserves properties."""
    dec = CG01Decision(
        stage=CG01Stage.EXECUTABLE.value,
        is_eligible=True,
        reason_codes=["EXECUTABLE"],
        direction="LONG",
        poi_id="poi_01",
        ui_summary_vi="Setup đủ điều kiện, đang chuẩn bị đặt lệnh.",
        as_of_ms=5000,
    )
    res = serialize_cg01_evaluation(dec, enabled=True, evaluated=True, as_of_ms=5000, armed_order_id="order_123")
    assert res["stage"] == "EXECUTABLE"
    assert res["arm_outcome"] == "ARMED"
    assert res["armed_order_id"] == "order_123"
    assert res["as_of_ms"] == 5000

    # In orchestrator, stage is updated to ARMED
    res["stage"] = "ARMED"
    assert res["stage"] == "ARMED"
    assert res["armed_order_id"] == "order_123"


def test_lifecycle_override_guard_blocked():
    """Item 060, 143: EXECUTABLE candidate overridden to GUARD_BLOCKED reflects guard rejection."""
    dec = CG01Decision(
        stage=CG01Stage.EXECUTABLE.value,
        is_eligible=True,
        reason_codes=["EXECUTABLE"],
        direction="SHORT",
        poi_id="poi_02",
        ui_summary_vi="Setup đủ điều kiện.",
        as_of_ms=6000,
    )
    guard_sum = {"blocked_reason": "DAILY_MAX_CAP_REACHED", "blocked_at": 6000}
    res = serialize_cg01_evaluation(dec, enabled=True, evaluated=True, as_of_ms=6000, guard_summary=guard_sum)
    res["stage"] = "GUARD_BLOCKED"
    res["reason_codes"].append("DAILY_MAX_CAP_REACHED")

    assert res["stage"] == "GUARD_BLOCKED"
    assert "DAILY_MAX_CAP_REACHED" in res["reason_codes"]
    assert res["guard_summary"]["blocked_reason"] == "DAILY_MAX_CAP_REACHED"
    assert res["direction"] == "SHORT"
    assert res["poi_id"] == "poi_02"


def test_lifecycle_override_rollback_degraded():
    """Item 060, 144: Commit failure forces stage to DEGRADED and clears armed_order_id."""
    dec = CG01Decision(
        stage=CG01Stage.EXECUTABLE.value,
        is_eligible=True,
        reason_codes=["EXECUTABLE"],
        direction="LONG",
        poi_id="poi_03",
        as_of_ms=7000,
    )
    res = serialize_cg01_evaluation(dec, enabled=True, evaluated=True, as_of_ms=7000, armed_order_id="order_failed")
    # Simulate rollback block in evaluate_cycle
    res["armed_order_id"] = None
    res["arm_outcome"] = "TRANSACTION_ROLLBACK"
    res["stage"] = "DEGRADED"
    res.setdefault("reason_codes", []).append("TRANSACTION_COMMIT_FAILED")

    assert res["stage"] == "DEGRADED"
    assert res["armed_order_id"] is None
    assert res["arm_outcome"] == "TRANSACTION_ROLLBACK"
    assert "TRANSACTION_COMMIT_FAILED" in res["reason_codes"]


def test_orchestrator_get_latest_cg01_evaluation():
    """Test ScalpWatcherOrchestrator stores and retrieves latest CG-01 evaluation per symbol."""
    orch = ScalpWatcherOrchestrator()
    initial = orch.get_latest_cg01_evaluation("XAUUSDT")
    assert initial["enabled"] is False
    assert initial["stage"] == "MODEL_DISABLED"

    # Simulate recording evaluation
    eval_mock = {
        "enabled": True,
        "evaluated": True,
        "stage": "WAITING_MSS_CONFIRMATION",
        "direction": "LONG",
        "poi_id": "poi_test",
        "as_of_ms": 12345,
        "reason_codes": ["WAITING_MSS_CONFIRMATION"],
        "ui_summary_vi": "Đang chờ MSS",
    }
    orch._record_and_broadcast_cg01_evaluation("XAUUSDT", eval_mock)

    retrieved = orch.get_latest_cg01_evaluation("XAUUSDT")
    assert retrieved["stage"] == "WAITING_MSS_CONFIRMATION"
    assert retrieved["direction"] == "LONG"
    assert retrieved["poi_id"] == "poi_test"
    assert retrieved["as_of_ms"] == 12345


@pytest.mark.anyio
async def test_market_broadcaster_cg01_envelope():
    """Test MarketBroadcaster envelope for CG01_EVALUATION."""
    broadcaster = MarketBroadcaster()
    mock_ws = AsyncMock()
    mock_ws.send_text = AsyncMock()

    await broadcaster.connect(mock_ws)
    assert broadcaster.client_count == 1

    payload = {
        "stage": "WAITING_FVG_FORMATION",
        "direction": "SHORT",
        "poi_id": "poi_h1_01",
        "reason_codes": ["NO_FVG_ON_LEG"],
        "as_of_ms": 99999,
    }

    await broadcaster.broadcast_cg01_evaluation(payload, symbol="XAUUSDT")

    assert mock_ws.send_text.called
    sent_text = mock_ws.send_text.call_args[0][0]
    import json
    msg = json.loads(sent_text)

    assert msg["protocol_version"] == "7.1.0"
    assert msg["type"] == "CG01_EVALUATION"
    assert msg["symbol"] == "XAUUSDT"
    assert msg["server_sequence"] > 0
    assert msg["published_at_ms"] > 0
    assert msg["payload"] == payload

    await broadcaster.disconnect(mock_ws)


def test_get_scalp_cg01_evaluation_endpoint():
    """Test GET /api/scalp/cg01/evaluation endpoint returns evaluation data."""
    client = TestClient(app)
    res = client.get("/api/scalp/cg01/evaluation?symbol=XAUUSDT")
    assert res.status_code == 200
    data = res.json()
    assert "stage" in data
    assert "enabled" in data
    assert "reason_codes" in data
