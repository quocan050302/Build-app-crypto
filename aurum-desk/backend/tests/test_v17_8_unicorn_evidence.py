"""
backend/tests/test_v17_8_unicorn_evidence.py
AURUM V17.8 — Test Suite for ICT Unicorn Evidence Engine.
Verifies positive Bullish & Bearish Unicorn detection, negative controls,
geometric overlap enforcement, causal time-ordering, and invalidation rules.
"""

import pytest
from dataclasses import dataclass
from typing import List

from services.unicorn_evidence_types import (
    BreakerBlock,
    BreakerType,
    UnicornEvidence,
    UnicornSetupStatus,
)
from services.unicorn_evidence_service import (
    detect_breaker_blocks,
    detect_unicorn_evidence,
)


@dataclass
class CandleBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 10.0
    open_time: int = 0

    def __post_init__(self):
        if not self.open_time:
            self.open_time = self.timestamp


def test_positive_bullish_unicorn_detection():
    """
    Constructs a textbook Bullish Unicorn setup:
    1. Swing High at candle 2 (H=2010.0, confirmed at candle 4).
    2. Bearish down-close candle (OB) at candle 5 (O=2002.0, H=2003.0, L=1998.0, C=1999.0).
    3. Impulsive bullish displacement at candle 6 (O=2000.0, H=2015.0, L=2000.0, C=2014.0) breaking 2010.0.
    4. Bullish FVG created with candle 7 (O=2014.0, H=2020.0, L=2001.0, C=2018.0) where candle 5 high=2003.0 vs candle 7 low=2001.0?
       Let's ensure standard FVG geometry:
       Candle 5: H=2001.0
       Candle 6: Large green candle
       Candle 7: L=2004.0 -> FVG is [2001.0, 2004.0]
       And Breaker is candle 5: [1998.0, 2003.0] -> Overlap in [2001.0, 2003.0].
    5. Candle 8 dips to L=2002.0 (retesting overlap [2001.0, 2003.0]).
    """
    bars = [
        CandleBar(timestamp=60_000, open=2005.0, high=2007.0, low=2004.0, close=2006.0),
        CandleBar(timestamp=120_000, open=2006.0, high=2010.0, low=2005.0, close=2009.0),  # SH @ 2010.0
        CandleBar(timestamp=180_000, open=2009.0, high=2009.5, low=2006.0, close=2007.0),
        CandleBar(timestamp=240_000, open=2007.0, high=2008.0, low=2003.0, close=2004.0),  # Confirms SH @ 2010
        # Bearish OB candle (down-close):
        CandleBar(timestamp=300_000, open=2004.0, high=2004.0, low=1998.0, close=1999.0),  # OB: low=1998, high=2004
        # Displacement break candle closing above 2010.0:
        CandleBar(timestamp=360_000, open=2000.0, high=2015.0, low=2000.0, close=2014.0),  # Break bar!
        # Candle 7 completing FVG [2002.0, 2006.0]:
        CandleBar(timestamp=420_000, open=2014.0, high=2020.0, low=2002.0, close=2018.0),
        # Retest candle dipping into overlap:
        CandleBar(timestamp=480_000, open=2018.0, high=2019.0, low=2003.0, close=2016.0),
    ]
    as_of_ms = 540_000  # Candle 8 closed at 540_000

    # Explicit confirmed swings
    swings = ([{"id": "sh_120", "price": 2010.0, "timestamp": 120_000, "confirmed_at": 240_000, "side": "BUY_SIDE"}], [])

    # Explicit FVG overlapping with Breaker [1998.0, 2004.0]
    candidate_fvgs = [{
        "id": "fvg_bull_360",
        "type": "BULLISH_FVG",
        "top": 2006.0,
        "bottom": 2002.0,
        "timestamp": 360_000,
        "confirmed_at": 480_000,
        "state": "confirmed",
    }]

    ev = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=as_of_ms,
        confirmed_swings=swings,
        candidate_fvgs=candidate_fvgs,
        min_overlap_ticks=1.0,
        tick_size=0.01,
    )

    assert ev.validity_status == UnicornSetupStatus.VALID.value
    assert ev.is_valid is True
    assert ev.direction == "LONG"
    assert ev.breaker_type == BreakerType.BULLISH_BREAKER.value
    assert ev.breaker_low == 1998.0
    assert ev.breaker_high == 2004.0
    assert ev.breaker_distal_price == 1998.0
    assert ev.invalidation_price == 1998.0
    assert ev.overlap_low == 2002.0
    assert ev.overlap_high == 2004.0
    assert ev.has_overlap is True
    assert ev.retest_timestamp_ms == 480_000


def test_positive_bearish_unicorn_detection():
    """
    Constructs a textbook Bearish Unicorn setup:
    1. Swing Low at candle 2 (L=1990.0, confirmed at candle 4).
    2. Bullish up-close candle (OB) at candle 5 (O=1996.0, H=2002.0, L=1996.0, C=2001.0).
    3. Impulsive bearish displacement at candle 6 (O=2000.0, H=2000.0, L=1985.0, C=1986.0) breaking 1990.0.
    4. Bearish FVG [1994.0, 1998.0] created.
    5. Breaker [1996.0, 2002.0] overlaps with FVG [1994.0, 1998.0] in [1996.0, 1998.0].
    6. Candle 8 retests up to H=1997.0.
    """
    bars = [
        CandleBar(timestamp=60_000, open=1995.0, high=1996.0, low=1993.0, close=1994.0),
        CandleBar(timestamp=120_000, open=1994.0, high=1995.0, low=1990.0, close=1991.0),  # SL @ 1990.0
        CandleBar(timestamp=180_000, open=1991.0, high=1994.0, low=1990.5, close=1993.0),
        CandleBar(timestamp=240_000, open=1993.0, high=1997.0, low=1992.0, close=1996.0),  # Confirms SL @ 1990
        # Bullish OB candle:
        CandleBar(timestamp=300_000, open=1996.0, high=2002.0, low=1996.0, close=2001.0),  # OB: low=1996, high=2002
        # Displacement break candle closing below 1990.0:
        CandleBar(timestamp=360_000, open=2000.0, high=2000.0, low=1985.0, close=1986.0),  # Break bar!
        # Candle 7 completing FVG:
        CandleBar(timestamp=420_000, open=1986.0, high=1994.0, low=1980.0, close=1982.0),
        # Retest candle rising into overlap:
        CandleBar(timestamp=480_000, open=1982.0, high=1997.0, low=1981.0, close=1985.0),
    ]
    as_of_ms = 540_000

    swings = ([], [{"id": "sl_120", "price": 1990.0, "timestamp": 120_000, "confirmed_at": 240_000, "side": "SELL_SIDE"}])

    candidate_fvgs = [{
        "id": "fvg_bear_360",
        "type": "BEARISH_FVG",
        "top": 1998.0,
        "bottom": 1994.0,
        "timestamp": 360_000,
        "confirmed_at": 480_000,
        "state": "confirmed",
    }]

    ev = detect_unicorn_evidence(
        closed_bars=bars,
        direction="SHORT",
        as_of_ms=as_of_ms,
        confirmed_swings=swings,
        candidate_fvgs=candidate_fvgs,
        min_overlap_ticks=1.0,
        tick_size=0.01,
    )

    assert ev.validity_status == UnicornSetupStatus.VALID.value
    assert ev.is_valid is True
    assert ev.direction == "SHORT"
    assert ev.breaker_type == BreakerType.BEARISH_BREAKER.value
    assert ev.breaker_low == 1996.0
    assert ev.breaker_high == 2002.0
    assert ev.breaker_distal_price == 2002.0
    assert ev.invalidation_price == 2002.0
    assert ev.overlap_low == 1996.0
    assert ev.overlap_high == 1998.0
    assert ev.has_overlap is True
    assert ev.retest_timestamp_ms == 480_000


def test_negative_breaker_without_fvg():
    """FVG list is empty -> MISSING_FVG status."""
    bars = [
        CandleBar(timestamp=60_000, open=2005.0, high=2007.0, low=2004.0, close=2006.0),
        CandleBar(timestamp=120_000, open=2006.0, high=2010.0, low=2005.0, close=2009.0),
        CandleBar(timestamp=180_000, open=2009.0, high=2009.5, low=2006.0, close=2007.0),
        CandleBar(timestamp=240_000, open=2007.0, high=2008.0, low=2003.0, close=2004.0),
        CandleBar(timestamp=300_000, open=2004.0, high=2004.0, low=1998.0, close=1999.0),
        CandleBar(timestamp=360_000, open=2000.0, high=2015.0, low=2000.0, close=2014.0),
    ]
    swings = ([{"id": "sh_120", "price": 2010.0, "timestamp": 120_000, "confirmed_at": 240_000, "side": "BUY_SIDE"}], [])

    ev = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=420_000,
        confirmed_swings=swings,
        candidate_fvgs=[],  # No FVG
    )
    assert ev.validity_status == UnicornSetupStatus.MISSING_FVG.value
    assert ev.is_valid is False
    assert "FVG_NOT_FOUND" in ev.reason_codes


def test_negative_no_geometric_overlap():
    """Breaker is [1998.0, 2004.0], but FVG is far above [2008.0, 2012.0] -> NO_GEOMETRIC_OVERLAP."""
    bars = [
        CandleBar(timestamp=60_000, open=2005.0, high=2007.0, low=2004.0, close=2006.0),
        CandleBar(timestamp=120_000, open=2006.0, high=2010.0, low=2005.0, close=2009.0),
        CandleBar(timestamp=180_000, open=2009.0, high=2009.5, low=2006.0, close=2007.0),
        CandleBar(timestamp=240_000, open=2007.0, high=2008.0, low=2003.0, close=2004.0),
        CandleBar(timestamp=300_000, open=2004.0, high=2004.0, low=1998.0, close=1999.0),  # Breaker: [1998, 2004]
        CandleBar(timestamp=360_000, open=2000.0, high=2015.0, low=2000.0, close=2014.0),
    ]
    swings = ([{"id": "sh_120", "price": 2010.0, "timestamp": 120_000, "confirmed_at": 240_000, "side": "BUY_SIDE"}], [])

    disjoint_fvgs = [{
        "id": "fvg_far",
        "type": "BULLISH_FVG",
        "top": 2012.0,
        "bottom": 2008.0,  # Completely above 2004.0!
        "timestamp": 360_000,
        "confirmed_at": 420_000,
    }]

    ev = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=420_000,
        confirmed_swings=swings,
        candidate_fvgs=disjoint_fvgs,
    )
    assert ev.validity_status == UnicornSetupStatus.NO_GEOMETRIC_OVERLAP.value
    assert ev.is_valid is False
    assert "BREAKER_FVG_NO_OVERLAP" in ev.reason_codes


def test_negative_unicorn_invalidated_by_distal_breach():
    """Price closes beyond breaker distal price before decision cutoff -> UNICORN_INVALIDATED."""
    bars = [
        CandleBar(timestamp=60_000, open=2005.0, high=2007.0, low=2004.0, close=2006.0),
        CandleBar(timestamp=120_000, open=2006.0, high=2010.0, low=2005.0, close=2009.0),
        CandleBar(timestamp=180_000, open=2009.0, high=2009.5, low=2006.0, close=2007.0),
        CandleBar(timestamp=240_000, open=2007.0, high=2008.0, low=2003.0, close=2004.0),
        CandleBar(timestamp=300_000, open=2004.0, high=2004.0, low=1998.0, close=1999.0),  # Distal: 1998.0
        CandleBar(timestamp=360_000, open=2000.0, high=2015.0, low=2000.0, close=2014.0),
        CandleBar(timestamp=420_000, open=2014.0, high=2020.0, low=2002.0, close=2018.0),
        # Severe crash breaching 1998.0 distal low:
        CandleBar(timestamp=480_000, open=2018.0, high=2018.0, low=1995.0, close=1996.0),  # Close 1996 < 1998
    ]
    swings = ([{"id": "sh_120", "price": 2010.0, "timestamp": 120_000, "confirmed_at": 240_000, "side": "BUY_SIDE"}], [])
    candidate_fvgs = [{
        "id": "fvg_bull_360",
        "type": "BULLISH_FVG",
        "top": 2006.0,
        "bottom": 2002.0,
        "timestamp": 360_000,
        "confirmed_at": 480_000,
    }]

    ev = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=540_000,
        confirmed_swings=swings,
        candidate_fvgs=candidate_fvgs,
    )
    assert ev.validity_status == UnicornSetupStatus.UNICORN_INVALIDATED.value
    assert ev.is_valid is False
    assert "DISTAL_LEVEL_BREACHED" in ev.reason_codes


def test_negative_waiting_retest():
    """Breaker and FVG overlap, but no retest has touched the zone yet -> WAITING_RETEST."""
    bars = [
        CandleBar(timestamp=60_000, open=2005.0, high=2007.0, low=2004.0, close=2006.0),
        CandleBar(timestamp=120_000, open=2006.0, high=2010.0, low=2005.0, close=2009.0),
        CandleBar(timestamp=180_000, open=2009.0, high=2009.5, low=2006.0, close=2007.0),
        CandleBar(timestamp=240_000, open=2007.0, high=2008.0, low=2003.0, close=2004.0),
        CandleBar(timestamp=300_000, open=2004.0, high=2004.0, low=1998.0, close=1999.0),
        CandleBar(timestamp=360_000, open=2000.0, high=2015.0, low=2000.0, close=2014.0),
        CandleBar(timestamp=420_000, open=2014.0, high=2020.0, low=2010.0, close=2018.0),
        # Stays high up at 2016-2022, never touches overlap [2002, 2004]:
        CandleBar(timestamp=480_000, open=2018.0, high=2022.0, low=2016.0, close=2021.0),
    ]
    swings = ([{"id": "sh_120", "price": 2010.0, "timestamp": 120_000, "confirmed_at": 240_000, "side": "BUY_SIDE"}], [])
    candidate_fvgs = [{
        "id": "fvg_bull_360",
        "type": "BULLISH_FVG",
        "top": 2006.0,
        "bottom": 2002.0,
        "timestamp": 360_000,
        "confirmed_at": 480_000,
    }]

    ev = detect_unicorn_evidence(
        closed_bars=bars,
        direction="LONG",
        as_of_ms=540_000,
        confirmed_swings=swings,
        candidate_fvgs=candidate_fvgs,
    )
    assert ev.validity_status == UnicornSetupStatus.WAITING_RETEST.value
    assert ev.is_valid is False
    assert "WAITING_CONFLUENCE_RETEST" in ev.reason_codes
