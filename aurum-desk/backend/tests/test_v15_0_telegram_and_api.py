"""
test_v15_0_telegram_and_api.py
V15.0 - Unit & Integration tests for Telegram notifications and Scalp REST APIs (PHẦN 068 - 072, 093).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from services.telegram_service import format_telegram_message


@pytest.fixture
def client():
    return TestClient(app)


def test_telegram_near_entry_formatting():
    """PHẦN 068, 069: NEAR_ENTRY telegram format informs proximity and does not state 'order placed'."""
    data = {
        "setup_id": "z_fvg_15m_01",
        "direction": "LONG",
        "reference_price": 2620.5,
        "entry_zone_low": 2618.0,
        "entry_zone_high": 2622.0,
        "entry_target": 2620.0,
        "distance_price": 0.5,
        "distance_atr": 0.25,
        "atr": 2.0,
        "auto_paper_enabled": True,
    }
    msg = format_telegram_message("NEAR_ENTRY", data, occurred_at=1_000_000)
    assert "SẮP TIẾP CẬN VÙNG ENTRY" in msg
    assert "tự động vào lệnh nếu chưa đủ xác nhận" in msg
    assert "Auto Paper" in msg
    assert "Đang BẬT" in msg


def test_telegram_trigger_confirmed_formatting():
    """PHẦN 068, 069: TRIGGER_CONFIRMED shows mode, model ID, levels and net R:R."""
    data = {
        "model_id": "SMC_M1_REVERSAL",
        "mode": "PAPER",
        "direction": "LONG",
        "planned_entry": 2622.5,
        "stop_loss": 2615.0,
        "take_profit": 2640.0,
        "net_rr": 2.25,
        "zone_id": "z_long_01",
    }
    msg = format_telegram_message("TRIGGER_CONFIRMED", data, occurred_at=1_000_000)
    assert "XÁC NHẬN TRIGGER (PAPER)" in msg
    assert "SMC" in msg and "M1" in msg and "REVERSAL" in msg
    assert "2622.50" in msg
    assert "2.25" in msg


def test_api_scalp_config_endpoints(client):
    """PHẦN 021, 022: Scalp configuration API allows querying and safe mode updates."""
    # GET config
    res_get = client.get("/api/scalp/config")
    assert res_get.status_code == 200
    cfg = res_get.json()
    assert "scalp_m1_mode" in cfg
    assert "daily_ny_research_enabled" in cfg

    # POST config update to SHADOW
    res_post = client.post("/api/scalp/config", json={"scalp_m1_mode": "SHADOW"})
    assert res_post.status_code == 200
    assert res_post.json()["status"] == "SUCCESS"

    # Verify updated via GET
    res_after = client.get("/api/scalp/config")
    assert res_after.json()["scalp_m1_mode"] == "SHADOW"
    assert res_after.json()["scalp_m1_enabled"] is True

    # Revert to OFF
    client.post("/api/scalp/config", json={"scalp_m1_mode": "OFF"})


def test_api_scalp_watchlist_and_funnel_endpoints(client):
    """PHẦN 032, 072: Watchlist and Opportunity funnel telemetry query endpoints."""
    # Watchlist
    res_wl = client.get("/api/scalp/watchlist?symbol=XAUUSDT")
    assert res_wl.status_code == 200
    wl_data = res_wl.json()
    assert "symbol" in wl_data
    assert "zones" in wl_data

    # Funnel
    res_fn = client.get("/api/scalp/funnel?symbol=XAUUSDT")
    assert res_fn.status_code == 200
    fn_data = res_fn.json()
    assert "total_unique_opportunities" in fn_data
    assert "top_rejection_reasons" in fn_data


def test_api_scalp_comparison_endpoint(client):
    """PHẦN 080: Multi-model statistical comparison report API."""
    res = client.get("/api/scalp/comparison")
    assert res.status_code == 200
    comp_data = res.json()
    assert "comparison" in comp_data
    assert "disclaimer_vi" in comp_data
    assert "STANDARD_SMC" in comp_data["comparison"]
    assert "NY_DAILY_RESEARCH" in comp_data["comparison"]
