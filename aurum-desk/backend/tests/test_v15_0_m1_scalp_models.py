"""
test_v15_0_m1_scalp_models.py
V15.0 - Unit & Behavioral tests for SMC M1 Scalp Reversal & Continuation Models (PHẦN 042 - 053, 086 - 088).
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass
from services.scalp_m1_model_service import ScalpM1ModelService
from services.scalp_m1_types import M1ScalpConfig, WatchedZone, ZoneState


@dataclass
class CandleStub:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 10.0
    is_closed: bool = True

    @property
    def open_time(self) -> int:
        return self.timestamp


def test_m1_model_requires_sufficient_closed_candles():
    """PHẦN 042, 043: Rejects candidate if closed M1 candles < min_closed_m1_bars."""
    cfg = M1ScalpConfig(min_closed_m1_bars=15)
    # Only 5 closed bars
    candles = [CandleStub((i + 1) * 60_000, 2600.0, 2605.0, 2595.0, 2602.0) for i in range(5)]
    zone = WatchedZone(
        zone_id="z_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2605.0, bottom=2595.0, created_at=60_000, known_at=60_000,
    )
    quote = {"bid": 2600.0, "ask": 2600.5, "is_valid": True}
    res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles, quote, {"capital_usdt": 1000.0, "risk_pct": 0.25}, now_ms=400_000, config=cfg
    )
    assert res.is_eligible is False
    assert res.reason_code == "DATA_INCOMPLETE"
    assert "Thiếu nến M1" in res.missing_conditions[0]


def test_m1_reversal_positive_control_long():
    """PHẦN 045, 046, 086: Full causal M1 Reversal chain (LONG) produces eligible candidate with valid Net R:R."""
    from tests.test_v15_1_execution_lifecycle import _make_causal_long_m1_candles

    cfg = M1ScalpConfig(min_closed_m1_bars=15, displacement_body_atr_min=0.7)

    # 1. Watched Bullish FVG zone at [2600, 2620]
    zone = WatchedZone(
        zone_id="z_long", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.CONFIRMED.value,
    )

    candles = _make_causal_long_m1_candles()
    now_ms = 1300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles, quote, {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50}, now_ms=now_ms, config=cfg
    )

    assert res.is_eligible is True
    assert res.model_id == "SMC_M1_REVERSAL"
    assert res.direction == "LONG"
    assert res.stop_loss <= 2602.0  # SL protected below sweep low
    assert res.planned_entry >= 2620.0
    assert res.take_profit > res.planned_entry
    assert res.net_rr >= 2.0
    assert res.snapshot is not None
    assert res.snapshot.state.value == "STRUCTURE_CONFIRMED"


def test_m1_reversal_positive_control_short():
    """PHẦN 088: Symmetry check - Full causal M1 Reversal chain (SHORT) produces eligible candidate."""
    from tests.test_v15_1_execution_lifecycle import _make_causal_short_m1_candles

    cfg = M1ScalpConfig(min_closed_m1_bars=15, displacement_body_atr_min=0.7)

    # Watched Bearish FVG zone at [2650, 2670]
    zone = WatchedZone(
        zone_id="z_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.CONFIRMED.value,
    )

    candles = _make_causal_short_m1_candles()
    now_ms = 1300_000
    quote = {"bid": 2647.5, "ask": 2648.0, "is_valid": True}
    res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles, quote, {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50}, now_ms=now_ms, config=cfg
    )

    assert res.is_eligible is True
    assert res.model_id == "SMC_M1_REVERSAL"
    assert res.direction == "SHORT"
    assert res.stop_loss >= 2668.0  # SL above sweep high
    assert res.planned_entry <= 2650.0
    assert res.take_profit < res.planned_entry
    assert res.net_rr >= 2.0


def test_m1_continuation_positive_and_negative_control():
    """PHẦN 048, 087: M1 Continuation model respects trend alignment and M1 BOS."""
    cfg = M1ScalpConfig(min_closed_m1_bars=15)

    # 1. Negative control: HTF conflicting or unknown trend -> rejected
    candles_1m = [CandleStub((i + 1) * 60_000, 2600.0 + i, 2605.0 + i, 2598.0 + i, 2604.0 + i) for i in range(20)]
    quote = {"bid": 2620.0, "ask": 2620.5, "is_valid": True}

    res_conflict = ScalpM1ModelService.evaluate_m1_continuation_candidate(
        candles_1m, [], quote, htf_bias="UNKNOWN", h1_alignment="NEUTRAL",
        policy_settings={"capital_usdt": 1000.0}, now_ms=1300_000, config=cfg
    )
    assert res_conflict.is_eligible is False
    assert res_conflict.reason_code == "HTF_BIAS_NOT_ALIGNED"

    # 2. Positive control: HTF BULLISH + ALIGNED, Pullback -> Swing High & Low -> M1 BOS break
    continuation_candles = [
        CandleStub(60_000, 2600.0, 2603.0, 2599.0, 2602.0),
        CandleStub(120_000, 2602.0, 2606.0, 2601.0, 2605.0),
        CandleStub(180_000, 2605.0, 2610.0, 2604.0, 2609.0),
        CandleStub(240_000, 2609.0, 2615.0, 2608.0, 2614.0),
        CandleStub(300_000, 2614.0, 2618.0, 2612.0, 2616.0), # Swing High @ 2618
        CandleStub(360_000, 2616.0, 2617.0, 2613.0, 2614.0),
        CandleStub(420_000, 2614.0, 2615.0, 2611.0, 2612.0),
        CandleStub(480_000, 2612.0, 2613.0, 2608.0, 2609.0), # Swing Low @ 2608
        CandleStub(540_000, 2609.0, 2612.0, 2608.5, 2611.0),
        CandleStub(600_000, 2611.0, 2614.0, 2610.0, 2613.0),
        CandleStub(660_000, 2613.0, 2617.0, 2612.0, 2616.0),
        CandleStub(720_000, 2616.0, 2622.0, 2615.0, 2621.0), # BOS break above 2618!
        CandleStub(780_000, 2621.0, 2623.0, 2619.0, 2622.0),
        CandleStub(840_000, 2622.0, 2624.0, 2620.0, 2623.0),
        CandleStub(900_000, 2623.0, 2625.0, 2621.0, 2624.0),
        CandleStub(960_000, 2624.0, 2625.0, 2622.0, 2624.0),
    ]
    quote_pos = {"bid": 2623.0, "ask": 2623.5, "is_valid": True}
    res_pos = ScalpM1ModelService.evaluate_m1_continuation_candidate(
        continuation_candles, [], quote_pos, htf_bias="BULLISH", h1_alignment="ALIGNED",
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50}, now_ms=1050_000, config=cfg
    )
    assert res_pos.is_eligible is True
    assert res_pos.model_id == "SMC_M1_CONTINUATION"
    assert res_pos.direction == "LONG"
    assert res_pos.net_rr >= 2.0
