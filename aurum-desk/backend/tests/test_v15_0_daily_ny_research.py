"""
test_v15_0_daily_ny_research.py
V15.0 - Unit & Behavioral tests for Daily NY Paper Research Model (PHẦN 004, 005, 062 - 067, 090).
"""
from __future__ import annotations

from dataclasses import dataclass
import pytest

from services.daily_ny_research_service import DailyNYResearchService
from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier, OpportunityReasonCode


@dataclass
class Candle5mStub:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 100.0
    is_closed: bool = True

    @property
    def open_time(self) -> int:
        return self.timestamp


def make_5m_candles(count: int = 25, base_price: float = 2620.0) -> list[Candle5mStub]:
    candles = []
    base_ts = 1_000_000_000
    for i in range(count):
        ts = base_ts + (i * 300_000)
        p = base_price + (i * 0.5)
        candles.append(Candle5mStub(
            timestamp=ts,
            open=p,
            high=p + 2.0,
            low=p - 1.5,
            close=p + 0.5,
        ))
    return candles


def test_daily_research_skipped_if_trade_already_filled_today():
    """PHẦN 065: If standard trade or previous research trade already filled today (>=1), do NOT trade."""
    candles = make_5m_candles(25)
    quote = {"bid": 2635.0, "ask": 2635.5, "is_valid": True}
    now_ms = 1_000_000_000 + (25 * 300_000) + 10_000

    res = DailyNYResearchService.evaluate_daily_research_candidate(
        candles, quote, htf_bias="BULLISH", h1_alignment="ALIGNED",
        policy_settings={"capital_usdt": 1000.0, "max_daily_trades": 3},
        today_fills_count=1,  # 1 fill already today
        now_ms=now_ms,
        is_ny_session=True,
    )

    assert res["is_eligible"] is False
    assert res["reason_code"] == "DAILY_TARGET_ALREADY_MET"
    assert "Mục tiêu tối thiểu 1 lệnh/ngày đã đạt" in res["missing_conditions"][0]


def test_daily_research_skipped_if_shared_max_daily_limit_reached():
    """PHẦN 060, 065: Shared max 3 fills/day limit blocks research execution."""
    candles = make_5m_candles(25)
    quote = {"bid": 2635.0, "ask": 2635.5, "is_valid": True}
    now_ms = 1_000_000_000 + (25 * 300_000) + 10_000

    res = DailyNYResearchService.evaluate_daily_research_candidate(
        candles, quote, htf_bias="BULLISH", h1_alignment="ALIGNED",
        policy_settings={"capital_usdt": 1000.0, "max_daily_trades": 3},
        today_fills_count=3,  # Reached shared cap
        now_ms=now_ms,
        is_ny_session=True,
    )

    assert res["is_eligible"] is False
    assert res["reason_code"] in ("DAILY_TARGET_ALREADY_MET", OpportunityReasonCode.DAILY_LIMIT_REACHED.value)


def test_daily_research_session_window_check():
    """PHẦN 061, 062: Ineligible outside NY session trading window."""
    candles = make_5m_candles(25)
    quote = {"bid": 2635.0, "ask": 2635.5, "is_valid": True}
    now_ms = 1_000_000_000 + (25 * 300_000) + 10_000

    res = DailyNYResearchService.evaluate_daily_research_candidate(
        candles, quote, htf_bias="BULLISH", h1_alignment="ALIGNED",
        policy_settings={"capital_usdt": 1000.0},
        today_fills_count=0,
        now_ms=now_ms,
        is_ny_session=False,  # Not in NY session
    )

    assert res["is_eligible"] is False
    assert res["reason_code"] == OpportunityReasonCode.SESSION_CLOSED.value


def test_daily_research_quote_invalidation_prevents_fake_fills():
    """PHẦN 005, 064: Zero fake fills on invalid or disconnected market quotes."""
    candles = make_5m_candles(25)
    quote_invalid = {"bid": 0.0, "ask": 0.0, "is_valid": False}
    now_ms = 1_000_000_000 + (25 * 300_000) + 10_000

    res = DailyNYResearchService.evaluate_daily_research_candidate(
        candles, quote_invalid, htf_bias="BULLISH", h1_alignment="ALIGNED",
        policy_settings={"capital_usdt": 1000.0},
        today_fills_count=0,
        now_ms=now_ms,
        is_ny_session=True,
    )

    assert res["is_eligible"] is False
    assert res["reason_code"] == OpportunityReasonCode.QUOTE_STALE.value


def test_daily_research_positive_control_eligible():
    """PHẦN 062, 063: When eligible (0 fills today, active NY session, valid data & quote), creates research candidate."""
    candles = make_5m_candles(25)
    quote = {"bid": 2635.0, "ask": 2635.5, "is_valid": True}
    now_ms = 1_000_000_000 + (25 * 300_000) + 10_000

    res = DailyNYResearchService.evaluate_daily_research_candidate(
        candles, quote, htf_bias="BULLISH", h1_alignment="ALIGNED",
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        today_fills_count=0,
        now_ms=now_ms,
        is_ny_session=True,
    )

    assert res["is_eligible"] is True
    assert res["model_id"] == "NY_DAILY_RESEARCH"
    assert res["model_id"] != "STANDARD_SMC"  # PHẦN 004, 023
    assert res["direction"] == "LONG"
    assert res["planned_entry"] == 2635.5
    assert res["stop_loss"] < res["planned_entry"]
    assert res["take_profit"] > res["planned_entry"]
    assert res["net_rr"] >= 2.0
    assert res["snapshot"] is not None
