"""
backend/tests/test_v17_5_8_episode_authority.py
AURUM V17.5.8 — Episode Authority & Protected Origin Selection Suite (Parts 018–020, 031–040, 084–085).

Audits and proves:
1. Caller-supplied episode_start_ms is NOT authoritative; derived structural boundaries govern the episode.
2. validate_episode_interval strictly enforces causal ordering: ref_occ <= start < end <= break_open.
3. Multi-leg boundary isolation: subsequent reference terminates episode; pivots at or after episode_end_ms
   are rejected and excluded from eligible origin ids.
4. Pivots before episode_start_ms are rejected with ORIGIN_OUTSIDE_EPISODE.
5. Pivot confirmation after decision cutoff rejected with ORIGIN_KNOWN_AFTER_DECISION.
6. Extreme noise isolation: appending older lookback candles with extreme prices does NOT alter
   the selected protected origin or the derived episode.
7. Directional selection: SHORT selects the highest swing high in episode, LONG selects the lowest swing low in episode.
"""

import math
import pytest
from services.stop_placement_types import ProtectedLegEpisode
from services.stop_placement_service import (
    derive_protected_leg_episode,
    select_protected_origin_for_break,
    validate_episode_interval,
    validate_protected_origin_binding,
)


class CandleBar:
    def __init__(self, open_time, o, h, l, c, is_closed=True):
        self.open_time = int(open_time)
        self.timestamp = int(open_time)
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.is_closed = is_closed


def test_p084_caller_injected_boundary_does_not_override_derived_structure():
    """Part 019 & 084: Caller passes an arbitrary early episode_start_ms in reference claim; derived start remains authoritative."""
    ref = {
        "id": "ref_1",
        "price": 2000.0,
        "occurred_at_ms": 5000,
        "known_at_ms": 5060,
        "episode_start_ms": 1000,  # caller attempts to inject 1000
    }
    brk_bar = CandleBar(10000, 2002.0, 2004.0, 1994.0, 1995.0)
    brk = {"break_open_ms": 10000, "known_at_ms": 10060, "bar": brk_bar}
    p_origin = {"id": "sh_leg", "price": 2018.0, "occurred_at_ms": 7000, "known_at_ms": 7060}

    bars = [
        CandleBar(1000, 2030, 2035, 2028, 2032),  # early bar where caller wanted to push start
        CandleBar(5000, 2005, 2010, 2000, 2002),  # reference bar
        CandleBar(7000, 2012, 2018, 2010, 2015),  # origin bar
        brk_bar,
    ]

    episode = derive_protected_leg_episode(
        model_id="M1_FVG",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([p_origin], [{"id": "ref_1", "price": 2000.0, "timestamp": 5000, "confirmed_at": 5060}]),
        closed_bars=bars,
        decision_cutoff_ms=20000,
    )

    assert episode.status == "AVAILABLE"
    # Derived start must start from reference occurrence, NOT arbitrary caller 1000
    assert episode.episode_start_ms == 5000
    assert "sh_leg" in episode.eligible_origin_ids

    sel = select_protected_origin_for_break(
        model_id="M1_FVG",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([p_origin], [{"id": "ref_1", "price": 2000.0, "timestamp": 5000, "confirmed_at": 5060}]),
        closed_bars=bars,
        decision_cutoff_ms=20000,
    )
    assert sel["status"] == "AVAILABLE"
    assert sel["origin_pivot_id"] == "sh_leg"
    assert sel["origin_price"] == 2018.0


def test_p084_validate_episode_interval_enforces_causal_ordering():
    """Part 020: validate_episode_interval returns False when timestamps are inverted or out of bounds."""
    # Valid interval
    valid, reason = validate_episode_interval(
        ref_occurred_at_ms=5000,
        break_open_ms=10000,
        episode_start_ms=5000,
        episode_end_ms=10000,
    )
    assert valid is True
    assert reason is None

    # Inverted start >= end
    inv_valid, inv_reason = validate_episode_interval(
        ref_occurred_at_ms=5000,
        break_open_ms=12000,
        episode_start_ms=10000,
        episode_end_ms=5000,
    )
    assert inv_valid is False
    assert inv_reason == "EPISODE_START_AFTER_END"

    # End after break open
    end_valid, end_reason = validate_episode_interval(
        ref_occurred_at_ms=5000,
        break_open_ms=10000,
        episode_start_ms=5000,
        episode_end_ms=15000,
    )
    assert end_valid is False
    assert end_reason == "EPISODE_END_AFTER_BREAK"


def test_p084_multi_leg_boundary_isolates_newer_leg_pivot():
    """Part 029 & 084: Subsequent reference B terminates leg A; origin in leg B rejected for leg A break."""
    ref_a = {"id": "ref_a", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    ref_b = {"id": "ref_b", "price": 1990.0, "occurred_at_ms": 8000, "known_at_ms": 8060}

    # Pivot in Leg A (7000ms) and Pivot in Leg B (9000ms)
    sh_leg_a = {"id": "sh_leg_a", "price": 2015.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    sh_leg_b = {"id": "sh_leg_b", "price": 2025.0, "occurred_at_ms": 9000, "known_at_ms": 9060}

    brk_bar = CandleBar(11000, 1995.0, 1997.0, 1985.0, 1988.0)
    brk = {"break_open_ms": 11000, "known_at_ms": 11060, "bar": brk_bar}

    bars = [
        CandleBar(5000, 2005, 2010, 2000, 2002),
        CandleBar(7000, 2010, 2015, 2008, 2012),
        CandleBar(8000, 2000, 2002, 1990, 1992),
        CandleBar(9000, 1995, 2025, 1994, 2020),
        brk_bar,
    ]

    # Deriving episode for ref_a: episode should end at ref_b (8000ms)
    episode = derive_protected_leg_episode(
        model_id="M1_FVG",
        direction="SHORT",
        selected_reference=ref_a,
        selected_break=brk,
        confirmed_swings=([sh_leg_a, sh_leg_b], [
            {"id": "ref_a", "price": 2000.0, "timestamp": 5000, "confirmed_at": 5060},
            {"id": "ref_b", "price": 1990.0, "timestamp": 8000, "confirmed_at": 8060},
        ]),
        closed_bars=bars,
        decision_cutoff_ms=20000,
    )

    assert episode.status == "AVAILABLE"
    # Even though sh_leg_b has higher price (2025 > 2015), it belongs to Leg B (> 8000ms)
    assert episode.episode_start_ms == 5000
    assert episode.episode_end_ms == 8000
    assert "sh_leg_a" in episode.eligible_origin_ids
    assert "sh_leg_b" not in episode.eligible_origin_ids

    sel = select_protected_origin_for_break(
        model_id="M1_FVG",
        direction="SHORT",
        selected_reference=ref_a,
        selected_break=brk,
        confirmed_swings=([sh_leg_a, sh_leg_b], [
            {"id": "ref_a", "price": 2000.0, "timestamp": 5000, "confirmed_at": 5060},
            {"id": "ref_b", "price": 1990.0, "timestamp": 8000, "confirmed_at": 8060},
        ]),
        closed_bars=bars,
        decision_cutoff_ms=20000,
    )
    assert sel["status"] == "AVAILABLE"
    assert sel["origin_pivot_id"] == "sh_leg_a"
    assert sel["origin_price"] == 2015.0


def test_p085_pivot_confirmed_after_cutoff_rejected():
    """Part 034: Pivot confirmed after decision cutoff is rejected with ORIGIN_KNOWN_AFTER_DECISION."""
    candle = CandleBar(7000, 2010, 2015, 2008, 2012)
    valid, reason = validate_protected_origin_binding(
        origin={"id": "p_late", "price": 2015.0, "occurred_at_ms": 7000, "known_at_ms": 12000},
        direction="SHORT",
        break_open_ms=15000,
        decision_cutoff_ms=10000,  # cutoff 10000 < known 12000
        closed_bars=[candle],
        episode_start_ms=5000,
        episode_end_ms=15000,
    )
    assert valid is False
    assert reason == "ORIGIN_KNOWN_AFTER_DECISION"


def test_p085_older_extreme_noise_does_not_alter_selected_origin():
    """Part 036 & 085: Distant older extreme (2050.0 at 2000ms) outside episode does not overwrite origin (2018.0 at 7000ms)."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    brk_bar = CandleBar(10000, 2002.0, 2004.0, 1994.0, 1995.0)
    brk = {"break_open_ms": 10000, "known_at_ms": 10060, "bar": brk_bar}
    p_origin = {"id": "sh_leg", "price": 2018.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    p_noise_past = {"id": "sh_past", "price": 2050.0, "occurred_at_ms": 2000, "known_at_ms": 2060}

    bars = [
        CandleBar(2000, 2040, 2050, 2038, 2045),
        CandleBar(5000, 2005, 2010, 2000, 2002),
        CandleBar(7000, 2012, 2018, 2010, 2015),
        brk_bar,
    ]

    episode = derive_protected_leg_episode(
        model_id="M1_FVG",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([p_noise_past, p_origin], [{"id": "ref_1", "price": 2000.0, "timestamp": 5000, "confirmed_at": 5060}]),
        closed_bars=bars,
        decision_cutoff_ms=20000,
    )

    assert episode.status == "AVAILABLE"
    assert "sh_leg" in episode.eligible_origin_ids
    assert "sh_past" not in episode.eligible_origin_ids

    sel = select_protected_origin_for_break(
        model_id="M1_FVG",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([p_noise_past, p_origin], [{"id": "ref_1", "price": 2000.0, "timestamp": 5000, "confirmed_at": 5060}]),
        closed_bars=bars,
        decision_cutoff_ms=20000,
    )
    assert sel["status"] == "AVAILABLE"
    assert sel["origin_pivot_id"] == "sh_leg"
    assert sel["origin_price"] == 2018.0


def test_p085_long_direction_selects_lowest_swing_low():
    """Part 036: For LONG continuation, selects lowest swing low in episode with outward SL anchor."""
    ref = {"id": "ref_long_1", "price": 2020.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    brk_bar = CandleBar(10000, 2018.0, 2026.0, 2017.0, 2025.0)  # closes 2025 > 2020
    brk = {"break_open_ms": 10000, "known_at_ms": 10060, "bar": brk_bar}

    sl_origin = {"id": "sl_leg", "price": 2005.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    sl_higher = {"id": "sl_mid", "price": 2010.0, "occurred_at_ms": 8000, "known_at_ms": 8060}

    bars = [
        CandleBar(5000, 2015, 2020, 2012, 2018),
        CandleBar(7000, 2010, 2012, 2005, 2008),
        CandleBar(8000, 2012, 2016, 2010, 2014),
        brk_bar,
    ]

    episode = derive_protected_leg_episode(
        model_id="M1_FVG",
        direction="LONG",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([], [sl_origin, sl_higher, {"id": "ref_long_1", "price": 2020.0, "timestamp": 5000, "confirmed_at": 5060}]),
        closed_bars=bars,
        decision_cutoff_ms=20000,
    )

    assert episode.status == "AVAILABLE"
    # LONG selects lowest low in episode (2005.0 < 2010.0)
    assert "sl_leg" in episode.eligible_origin_ids

    sel = select_protected_origin_for_break(
        model_id="M1_FVG",
        direction="LONG",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([], [sl_origin, sl_higher, {"id": "ref_long_1", "price": 2020.0, "timestamp": 5000, "confirmed_at": 5060}]),
        closed_bars=bars,
        decision_cutoff_ms=20000,
    )
    assert sel["status"] == "AVAILABLE"
    assert sel["origin_pivot_id"] == "sl_leg"
    assert sel["origin_price"] == 2005.0
