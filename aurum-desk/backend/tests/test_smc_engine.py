import pytest
import schemas
from smc_engine import (
    identify_pivots,
    determine_trend,
    detect_fvgs,
    detect_sweeps_and_breaks,
    calculate_position_sizing,
    compute_atr
)

class DummyCandle:
    def __init__(self, timestamp, o, h, l, c, is_closed=True):
        self.timestamp = timestamp
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = 10.0
        self.is_closed = is_closed

def test_pivot_confirmation_no_lookahead():
    """
    Pivot at index i must require 2 bars to the left and 2 closed bars to the right.
    Crucial: pivot at index i is ONLY confirmed at index i+2.
    """
    # Create 5 candles where candle 2 is highest
    candles = [
        DummyCandle(1000, 4000, 4005, 3995, 4002), # i = 0
        DummyCandle(2000, 4002, 4010, 4000, 4008), # i = 1
        DummyCandle(3000, 4008, 4025, 4005, 4020), # i = 2 (PIVOT HIGH)
        DummyCandle(4000, 4020, 4015, 4002, 4006), # i = 3
        DummyCandle(5000, 4006, 4010, 3998, 4001), # i = 4 (CONFIRMATION BAR)
    ]
    swing_highs, swing_lows = identify_pivots(candles)
    assert len(swing_highs) == 1
    assert swing_highs[0]["index"] == 2
    assert swing_highs[0]["price"] == 4025.0
    assert swing_highs[0]["timestamp"] == 3000
    # Confirmed only at bar 4
    assert swing_highs[0]["confirmed_at"] == 5000


def test_known_cases_numeric_rr():
    """
    Prompt requirement Section L:
    Known numeric test cases:
    - Long: Entry 4000 / SL 3990 / TP 4020 -> Gross 2R
    - Short: Entry 4000 / SL 4010 / TP 3980 -> Gross 2R
    """
    # Long test case
    long_sizing = calculate_position_sizing(
        capital=1000.0,
        risk_pct=0.5,
        entry=4000.0,
        sl=3990.0,
        tp=4020.0,
        fees_pct=0.04,
        slippage_usd=0.10
    )
    assert long_sizing["gross_rr"] == 2.0
    assert long_sizing["estimated_net_rr"] > 1.2
    assert long_sizing["quantity"] > 0

    # Short test case
    short_sizing = calculate_position_sizing(
        capital=1000.0,
        risk_pct=0.5,
        entry=4000.0,
        sl=4010.0,
        tp=3980.0,
        fees_pct=0.04,
        slippage_usd=0.10
    )
    assert short_sizing["gross_rr"] == 2.0
    assert short_sizing["estimated_net_rr"] > 1.2
    assert short_sizing["quantity"] > 0



def test_fvg_detection_and_mitigation():
    """
    Test 3-candle Fair Value Gap detection and subsequent mitigation tracking.
    """
    # Bullish FVG: Low of candle 2 > High of candle 0
    candles = [
        DummyCandle(1000, 4000, 4005, 3995, 4002), # i = 0 (High = 4005)
        DummyCandle(2000, 4002, 4020, 4002, 4018), # i = 1 (Big green push)
        DummyCandle(3000, 4018, 4025, 4010, 4022), # i = 2 (Low = 4010 > 4005, gap: 4005 to 4010)
    ]
    fvgs = detect_fvgs(candles)
    assert len(fvgs) == 1
    assert fvgs[0]["type"] == "BULLISH_FVG"
    assert fvgs[0]["bottom"] == 4005.0
    assert fvgs[0]["top"] == 4010.0
    assert fvgs[0]["state"] == "confirmed"

    # Candle 3 retraces into the gap (low = 4008, between 4005 and 4010)
    candles.append(DummyCandle(4000, 4022, 4023, 4008, 4012))
    fvgs_part = detect_fvgs(candles)
    assert fvgs_part[0]["state"] == "partially_mitigated"

    # Candle 4 completely fills the gap (low = 4000 <= 4005)
    candles.append(DummyCandle(5000, 4012, 4015, 4000, 4004))
    fvgs_full = detect_fvgs(candles)
    assert fvgs_full[0]["state"] == "fully_mitigated"
    assert fvgs_full[0]["mitigated"] is True


def test_liquidity_sweep_detection():
    """
    Sweep: Wick pierces beyond swing level, but candle closes back inside.
    """
    swing_highs = [{"index": 2, "price": 4050.0, "timestamp": 2000, "type": "HIGH"}]
    swing_lows = [{"index": 3, "price": 3950.0, "timestamp": 3000, "type": "LOW"}]

    # Candle spikes to 4055 (above 4050) but closes at 4045 (below 4050)
    candles = [
        DummyCandle(1000, 4020, 4030, 4010, 4025),
        DummyCandle(2000, 4025, 4050, 4020, 4040),
        DummyCandle(3000, 4040, 4045, 3950, 3960),
        DummyCandle(4000, 3960, 4040, 3960, 4035),
        DummyCandle(5000, 4035, 4055, 4030, 4045), # Sweep candle!
    ]

    events, last_event, sweep_info = detect_sweeps_and_breaks(candles, swing_highs, swing_lows, "BULLISH")
    assert sweep_info is not None
    assert sweep_info["type"] == "SWEEP_HIGH"
    assert sweep_info["level"] == 4050.0
    assert "Sweep đỉnh" in last_event
