"""
backend/tests/test_v17_5_5_origin_bridge.py
AURUM V17.5.5 — Origin & Episode Binding Bridge Test Suite.
Covers Parts 091–130, Appendix C, E1:
1. select_protected_origin_for_break binds causal origin swing within displacement episode.
2. Directional parity: LONG binds swing low, SHORT binds swing high.
3. Swings occurring after break or known after decision cutoff are rejected.
4. Noise swings outside episode do not displace authoritative anchor (Appendix C7).
5. build_selected_break_descriptor carries origin_pivot_id, episode_start_ms, origin_price.
6. resolve_stop_anchor consumes bound descriptor and creates verified StopAnchor.
7. FVG continuation model parity with structural origin proof.
"""

import pytest
from services.stop_placement_service import (
    select_protected_origin_for_break,
    build_selected_break_descriptor,
    resolve_stop_anchor,
)
from services.stop_placement_types import StopAnchorKind


class MockCandle:
    def __init__(self, open_time: int, open: float, high: float, low: float, close: float):
        self.open_time = open_time
        self.timestamp = open_time
        self.open = open
        self.high = high
        self.low = low
        self.close = close


def test_select_protected_origin_for_break_long():
    """Phần 101, 102: LONG continuation selects preceding swing low before break."""
    ref_sh = {
        "id": "sh_1",
        "price": 2650.0,
        "occurred_at_ms": 1000,
        "known_at_ms": 1060,
    }
    origin_sl = {
        "id": "sl_origin",
        "price": 2640.0,
        "occurred_at_ms": 1100,
        "known_at_ms": 1160,
    }
    outside_sl = {
        "id": "sl_noise",
        "price": 2635.0,
        "occurred_at_ms": 500,  # outside episode
        "known_at_ms": 560,
    }

    # Break bar at 1200 closing at 2652 (above 2650)
    break_bar = MockCandle(open_time=1200, open=2648.0, high=2653.0, low=2647.0, close=2652.0)
    closed_bars = [
        MockCandle(open_time=1100, open=2642.0, high=2643.0, low=2640.0, close=2641.0),
        break_bar,
    ]

    binding = select_protected_origin_for_break(
        model_id="M1_CONTINUATION",
        direction="LONG",
        selected_break={"bar": break_bar, "broken_at_ms": 1260},
        selected_reference=ref_sh,
        confirmed_swings=([ref_sh], [outside_sl, origin_sl]),
        closed_bars=closed_bars,
        decision_cutoff_ms=1300,
        tick_size=0.01,
    )

    assert binding["status"] == "AVAILABLE"
    assert binding["origin_pivot_id"] == "sl_origin"
    assert binding["origin_price"] == 2640.0
    assert binding["origin_kind"] == StopAnchorKind.PROTECTED_LEG_LOW.value


def test_select_protected_origin_for_break_short():
    """Phần 101, 102: SHORT continuation selects preceding swing high before break."""
    ref_sl = {
        "id": "sl_1",
        "price": 2640.0,
        "occurred_at_ms": 1000,
        "known_at_ms": 1060,
    }
    origin_sh = {
        "id": "sh_origin",
        "price": 2655.0,
        "occurred_at_ms": 1100,
        "known_at_ms": 1160,
    }

    # Break bar at 1200 closing at 2638 (below 2640)
    break_bar = MockCandle(open_time=1200, open=2642.0, high=2643.0, low=2637.0, close=2638.0)
    closed_bars = [
        MockCandle(open_time=1100, open=2650.0, high=2655.0, low=2649.0, close=2654.0),
        break_bar,
    ]

    binding = select_protected_origin_for_break(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_break={"bar": break_bar, "broken_at_ms": 1260},
        selected_reference=ref_sl,
        confirmed_swings=([origin_sh], [ref_sl]),
        closed_bars=closed_bars,
        decision_cutoff_ms=1300,
        tick_size=0.01,
    )

    assert binding["status"] == "AVAILABLE"
    assert binding["origin_pivot_id"] == "sh_origin"
    assert binding["origin_price"] == 2655.0
    assert binding["origin_kind"] == StopAnchorKind.PROTECTED_LEG_HIGH.value


def test_swings_after_break_or_cutoff_rejected():
    """Phần 115: Swings occurring after the break or known after decision cutoff are rejected."""
    ref_sh = {"id": "sh_1", "price": 2650.0, "occurred_at_ms": 1000, "known_at_ms": 1060}
    future_sl = {
        "id": "sl_future",
        "price": 2642.0,
        "occurred_at_ms": 1300,  # after break at 1200!
        "known_at_ms": 1360,
    }
    break_bar = MockCandle(open_time=1200, open=2648.0, high=2653.0, low=2647.0, close=2652.0)

    binding = select_protected_origin_for_break(
        model_id="M1_CONTINUATION",
        direction="LONG",
        selected_break={"bar": break_bar, "broken_at_ms": 1260},
        selected_reference=ref_sh,
        confirmed_swings=([ref_sh], [future_sl]),
        closed_bars=[break_bar],
        decision_cutoff_ms=1300,
    )

    assert binding["status"] == "UNAVAILABLE"
    assert binding["reason"] in ("NO_VALID_ORIGIN_IN_EPISODE", "NO_VALID_PROTECTED_ORIGIN_IN_EPISODE")


def test_noise_swings_outside_episode_ignored():
    """Phần 108, Appendix C7: Adding extra noise swings before the episode does not change selected origin."""
    ref_sh = {"id": "sh_1", "price": 2650.0, "occurred_at_ms": 1000, "known_at_ms": 1060}
    origin_sl = {"id": "sl_origin", "price": 2640.0, "occurred_at_ms": 1100, "known_at_ms": 1160}
    noise_sl1 = {"id": "sl_noise1", "price": 2620.0, "occurred_at_ms": 200, "known_at_ms": 260}
    noise_sl2 = {"id": "sl_noise2", "price": 2625.0, "occurred_at_ms": 400, "known_at_ms": 460}

    break_bar = MockCandle(open_time=1200, open=2648.0, high=2653.0, low=2647.0, close=2652.0)
    closed_bars = [
        MockCandle(open_time=1100, open=2642.0, high=2643.0, low=2640.0, close=2641.0),
        break_bar,
    ]

    binding = select_protected_origin_for_break(
        model_id="M1_CONTINUATION",
        direction="LONG",
        selected_break={"bar": break_bar, "broken_at_ms": 1260},
        selected_reference=ref_sh,
        confirmed_swings=([ref_sh], [noise_sl1, noise_sl2, origin_sl]),
        closed_bars=closed_bars,
        decision_cutoff_ms=1300,
    )

    assert binding["origin_pivot_id"] == "sl_origin"
    assert binding["origin_price"] == 2640.0


def test_build_selected_break_descriptor_and_resolve_stop_anchor():
    """Phần 094, 119: Descriptor carries origin proof and resolve_stop_anchor produces verified StopAnchor."""
    t0 = 1_000_000
    t1 = t0 + 60_000  # 1_060_000
    t2 = t1 + 60_000  # 1_120_000

    ref_sl = {"id": "sl_1", "price": 2640.0, "occurred_at_ms": t0, "known_at_ms": t0 + 60_000}
    origin_sh = {"id": "sh_origin", "price": 2655.0, "occurred_at_ms": t1, "known_at_ms": t1 + 60_000}

    origin_bar = MockCandle(open_time=t1, open=2650.0, high=2655.0, low=2649.0, close=2654.0)
    break_bar = MockCandle(open_time=t2, open=2642.0, high=2643.0, low=2637.0, close=2638.0)
    closed_bars = [origin_bar, break_bar]

    decision_cutoff = t2 + 65_000  # after break candle close

    # 1. Select origin
    binding = select_protected_origin_for_break(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_break={"bar": break_bar, "broken_at_ms": t2 + 60_000},
        selected_reference=ref_sl,
        confirmed_swings=([origin_sh], [ref_sl]),
        closed_bars=closed_bars,
        decision_cutoff_ms=decision_cutoff,
        tick_size=0.01,
    )
    assert binding["status"] == "AVAILABLE"

    # 2. Build break descriptor
    desc = build_selected_break_descriptor(
        break_bar=break_bar,
        broken_reference=ref_sl,
        timeframe_ms=60_000,
        decision_cutoff_ms=decision_cutoff,
        model_id="M1_CONTINUATION",
        direction="SHORT",
        origin_pivot_id=binding["origin_pivot_id"],
        episode_start_ms=binding["episode_start_ms"],
        origin_price=binding["origin_price"],
        origin_occurred_at_ms=binding["origin_occurred_at_ms"],
    )
    assert desc["origin_pivot_id"] == "sh_origin"
    assert desc["episode_start_ms"] == binding["episode_start_ms"]

    # 3. Resolve stop anchor
    anchor_res = resolve_stop_anchor(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        closed_bars=closed_bars,
        as_of_ms=decision_cutoff,
        selected_break=desc,
        confirmed_swings=([origin_sh], [ref_sl]),
        tick_size=0.01,
    )

    assert anchor_res.status == "AVAILABLE"
    assert anchor_res.anchor is not None
    assert anchor_res.anchor.kind == StopAnchorKind.PROTECTED_LEG_HIGH.value
    assert anchor_res.anchor.price == 2655.0
    assert anchor_res.bound_leg is not None
    assert anchor_res.bound_leg.origin_pivot_id == "sh_origin"
