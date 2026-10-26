"""
backend/tests/test_v16_2_repro_and_micro_break.py
V16.2 Checkpoint 1: Reproductions & Regression Tests for Micro-Break and Daily Config (Rules 011, 012, 020, 088, 089, 094, 095).
"""
import pytest
from typing import Sequence, Any, Dict
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from services.scalp_m1_model_service import detect_post_reclaim_micro_break
from services.trading_policy_service import TradingPolicyService, resolve_effective_daily_policy
import models


class CandleBarStub:
    def __init__(self, t: int, o: float, h: float, l: float, c: float, is_closed: bool = True):
        self.timestamp = t
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.is_closed = is_closed

    @property
    def open_time(self) -> int:
        return self.timestamp


# ==============================================================================
# 1. MICRO-BREAK LONG REGRESSION (Rule 011, 088, 094)
# ==============================================================================

def test_v16_2_micro_break_regression_long_no_mss():
    """
    Rule 011, 094: Micro-break regression LONG:
    Pre-sweep structural high = 110.
    Sweep candle: open=105, high=109, low=95, close=98.
    Reclaim candle: high=103, close=101.
    Expected: NO MSS / Micro-break confirmed (None).
    Must NOT return micro_swing_price = 98.
    """
    bars = [
        CandleBarStub(60_000, 108.0, 110.0, 107.0, 109.0),    # Pre-sweep high = 110.0
        CandleBarStub(120_000, 105.0, 109.0, 95.0, 98.0),     # Sweep bar
        CandleBarStub(180_000, 98.0, 103.0, 97.0, 101.0),     # Reclaim bar closing at 101
    ]
    sweep_event = {"bar": bars[1], "swept_at_ms": 120_000, "cluster_bot": 100.0}
    reclaim_event = {"bar": bars[2], "reclaimed_at_ms": 180_000}

    # Strict structural micro-break evaluation (Rule 088, 089)
    res = detect_post_reclaim_micro_break(
        bars, sweep_event, reclaim_event, "LONG", as_of_ms=240_000, tick_size=0.01
    )
    assert res is None, f"Expected no micro-break past 110.0, got: {res}"


def test_v16_2_micro_break_positive_long():
    """
    Rule 096: Positive micro-break confirmation LONG when closed candle breaks pre-sweep high.
    Pre-sweep structural high = 110.
    Subsequent candle closes at 110.50 (> 110.0 + tick_size).
    Expected: Micro-break confirmed at 110.50 with micro_swing_price = 110.0.
    """
    bars = [
        CandleBarStub(60_000, 108.0, 110.0, 107.0, 109.0),    # Pre-sweep high = 110.0
        CandleBarStub(120_000, 105.0, 109.0, 95.0, 98.0),     # Sweep bar
        CandleBarStub(180_000, 98.0, 103.0, 97.0, 101.0),     # Reclaim bar
        CandleBarStub(240_000, 101.0, 111.0, 100.5, 110.5),   # MSS break candle closing above 110.0
    ]
    sweep_event = {"bar": bars[1], "swept_at_ms": 120_000, "cluster_bot": 100.0}
    reclaim_event = {"bar": bars[2], "reclaimed_at_ms": 180_000}

    res = detect_post_reclaim_micro_break(
        bars, sweep_event, reclaim_event, "LONG", as_of_ms=300_000, tick_size=0.01
    )
    assert res is not None, "Expected confirmed micro-break above 110.0"
    assert res["micro_swing_price"] == 110.0
    assert res["break_price"] == 110.5
    assert res["broken_at_ms"] == 240_000


# ==============================================================================
# 2. MICRO-BREAK SHORT REGRESSION (Rule 095)
# ==============================================================================

def test_v16_2_micro_break_regression_short_no_mss():
    """
    Rule 095: Micro-break regression SHORT:
    Pre-sweep structural low = 90.
    Sweep candle: open=95, low=91, high=105, close=102.
    Reclaim candle: low=97, close=99.
    Expected: NO MSS / Micro-break confirmed (None).
    Must NOT return micro_swing_price = 102.
    """
    bars = [
        CandleBarStub(60_000, 92.0, 93.0, 90.0, 91.0),        # Pre-sweep low = 90.0
        CandleBarStub(120_000, 95.0, 105.0, 91.0, 102.0),     # Sweep bar
        CandleBarStub(180_000, 102.0, 103.0, 97.0, 99.0),     # Reclaim bar closing at 99
    ]
    sweep_event = {"bar": bars[1], "swept_at_ms": 120_000, "cluster_top": 100.0}
    reclaim_event = {"bar": bars[2], "reclaimed_at_ms": 180_000}

    res = detect_post_reclaim_micro_break(
        bars, sweep_event, reclaim_event, "SHORT", as_of_ms=240_000, tick_size=0.01
    )
    assert res is None, f"Expected no micro-break below 90.0, got: {res}"


def test_v16_2_micro_break_positive_short():
    """
    Rule 095, 096: Positive micro-break confirmation SHORT when closed candle breaks pre-sweep low.
    Pre-sweep structural low = 90.
    Subsequent candle closes at 89.50 (< 90.0 - tick_size).
    Expected: Micro-break confirmed at 89.50 with micro_swing_price = 90.0.
    """
    bars = [
        CandleBarStub(60_000, 92.0, 93.0, 90.0, 91.0),        # Pre-sweep low = 90.0
        CandleBarStub(120_000, 95.0, 105.0, 91.0, 102.0),     # Sweep bar
        CandleBarStub(180_000, 102.0, 103.0, 97.0, 99.0),     # Reclaim bar
        CandleBarStub(240_000, 99.0, 99.5, 89.0, 89.5),       # MSS break candle closing below 90.0
    ]
    sweep_event = {"bar": bars[1], "swept_at_ms": 120_000, "cluster_top": 100.0}
    reclaim_event = {"bar": bars[2], "reclaimed_at_ms": 180_000}

    res = detect_post_reclaim_micro_break(
        bars, sweep_event, reclaim_event, "SHORT", as_of_ms=300_000, tick_size=0.01
    )
    assert res is not None, "Expected confirmed micro-break below 90.0"
    assert res["micro_swing_price"] == 90.0
    assert res["break_price"] == 89.5
    assert res["broken_at_ms"] == 240_000


# ==============================================================================
# 3. DAILY CONFIG API PARITY REGRESSION (Rule 012, 021-028)
# ==============================================================================

def test_v16_2_daily_config_api_parity(client: TestClient, isolated_db: Session):
    """
    Rule 012, 021-028:
    POST /api/scalp/config saves daily_min_goal=1, daily_max_cap=2, daily_ny_goal=0.
    GET /api/scalp/config AND GET /api/scalp/daily-status must return the identical effective values:
    min_goal=1, cap=2, ny_goal=0.
    """
    # 1. Update config with daily_ny_goal=0 and cap=2
    res_post = client.post("/api/scalp/config", json={
        "daily_min_goal": 1,
        "daily_max_cap": 2,
        "daily_ny_goal": 0,
    })
    assert res_post.status_code == 200, res_post.text
    post_data = res_post.json()["effective_config"]
    assert post_data["daily_min_goal"] == 1
    assert post_data["daily_max_cap"] == 2
    assert post_data["daily_ny_goal"] == 0

    # 2. GET /api/scalp/config must match
    res_get_cfg = client.get("/api/scalp/config")
    assert res_get_cfg.status_code == 200
    get_cfg = res_get_cfg.json()["effective_config"]
    assert get_cfg["daily_min_goal"] == 1
    assert get_cfg["daily_max_cap"] == 2
    assert get_cfg["daily_ny_goal"] == 0

    # 3. GET /api/scalp/daily-status must match canonical policy (NOT cap=3, ny_goal=1!)
    res_daily = client.get("/api/scalp/daily-status")
    assert res_daily.status_code == 200
    daily_data = res_daily.json()
    assert daily_data["min_goal"] == 1
    assert daily_data["cap"] == 2
    assert daily_data["ny_goal"] == 0

    # 4. Strict validation checks (Rule 025, 026)
    # Reject boolean as int
    res_bool = client.post("/api/scalp/config", json={"daily_min_goal": True})
    assert res_bool.status_code == 400

    # Reject float
    res_float = client.post("/api/scalp/config", json={"daily_min_goal": 2.9})
    assert res_float.status_code == 400

    # Reject min_goal > max_cap
    res_invalid_order = client.post("/api/scalp/config", json={"daily_min_goal": 4, "daily_max_cap": 2})
    assert res_invalid_order.status_code == 400
