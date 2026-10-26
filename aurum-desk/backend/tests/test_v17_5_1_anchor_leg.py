"""
Aurum V17.5.1 — Anchor Leg & Binding Integration Tests
Verifies SelectedStopLeg descriptor, resolve_bound_stop_leg, pre-break pivot sorting,
sweep descriptors, and breach invalidation.
"""
from types import SimpleNamespace
import pytest
from services.stop_placement_service import (
    resolve_stop_anchor,
    resolve_bound_stop_leg,
    build_sweep_stop_descriptor,
    validate_anchor_not_invalidated,
)
from services.stop_placement_types import StopAnchor


def test_resolve_bound_stop_leg_validates_pivot_occurred_before_break():
    # 2 pivots: one occurred before break, one after break
    pivots = [
        {"id": "p_valid", "price": 2050.0, "occurred_at_ms": 100000, "known_at_ms": 120000},
        {"id": "p_post_break", "price": 2045.0, "occurred_at_ms": 250000, "known_at_ms": 280000},
    ]
    break_bar = SimpleNamespace(id="b_break", open_time=200000, open=2040.0, high=2042.0, low=2030.0, close=2032.0)
    selected_break = {
        "id": "brk_1",
        "broken_at_ms": 200000,
        "known_at_ms": 260000,
        "bar_id": "b_break",
        "broken_price": 2038.0,
    }

    leg = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        candidate_pivots=pivots,
        break_bar=break_bar,
        selected_break=selected_break,
        decision_cutoff_ms=300000,
    )

    assert leg is not None
    assert leg.origin_pivot_id == "p_valid"
    assert leg.origin_price == 2050.0
    assert leg.origin_occurred_at_ms == 100000
    assert leg.origin_occurred_at_ms < selected_break["broken_at_ms"]


def test_resolve_bound_stop_leg_short_vs_long():
    # SHORT: Protective level is high above broken low
    short_pivots = [
        {"id": "p_high", "price": 2100.0, "occurred_at_ms": 50000, "known_at_ms": 70000},
    ]
    short_break_bar = SimpleNamespace(id="sb", open_time=100000, open=2080.0, high=2085.0, low=2060.0, close=2065.0)
    short_break = {"id": "sbrk", "broken_at_ms": 100000, "known_at_ms": 160000, "broken_price": 2075.0}

    short_leg = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        candidate_pivots=short_pivots,
        break_bar=short_break_bar,
        selected_break=short_break,
        decision_cutoff_ms=200000,
    )
    assert short_leg is not None
    assert short_leg.origin_price == 2100.0
    assert short_leg.broken_reference_price == 2075.0
    assert short_leg.origin_price > short_leg.broken_reference_price

    # LONG: Protective level is low below broken high
    long_pivots = [
        {"id": "p_low", "price": 2000.0, "occurred_at_ms": 50000, "known_at_ms": 70000},
    ]
    long_break_bar = SimpleNamespace(id="lb", open_time=100000, open=2020.0, high=2040.0, low=2015.0, close=2035.0)
    long_break = {"id": "lbrk", "broken_at_ms": 100000, "known_at_ms": 160000, "broken_price": 2025.0}

    long_leg = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        candidate_pivots=long_pivots,
        break_bar=long_break_bar,
        selected_break=long_break,
        decision_cutoff_ms=200000,
    )
    assert long_leg is not None
    assert long_leg.origin_price == 2000.0
    assert long_leg.broken_reference_price == 2025.0
    assert long_leg.origin_price < long_leg.broken_reference_price


def test_build_sweep_stop_descriptor_extracts_real_fields():
    closed_bars = [
        SimpleNamespace(id="bar_0", open_time=100000, open=2000, high=2010, low=1995, close=2005),
        SimpleNamespace(id="bar_1", open_time=160000, open=2005, high=2015, low=2000, close=2012),
        SimpleNamespace(id="bar_2", open_time=220000, open=2012, high=2020, low=2008, close=2018),
    ]
    raw_sweep = {
        "sweep_time_ms": 100000,
        "sweep_bar_idx": 0,
        "reclaim_bar_idx": 1,
        "sweep_id": "sw_123",
        "episode_id": "ep_456",
        "reference_id": "ref_cluster_1",
        "sweep_extreme": 1995.0,
        "cluster_level": 2002.0,
    }

    desc = build_sweep_stop_descriptor(raw_sweep, closed_bars)
    assert desc is not None
    assert desc["sweep_extreme"] == 1995.0
    assert desc["occurred_at_ms"] == 100000
    # Reclaim bar index 1 open_time is 160000, so close is 160000 + 60000 = 220000
    assert desc["known_at_ms"] == 220000
    assert desc["sweep_id"] == "sw_123"
    assert desc["episode_id"] == "ep_456"


def test_validate_anchor_not_invalidated_rejects_breached_origin():
    anchor = StopAnchor(
        id="anchor_1",
        kind="PROTECTED_SWING_HIGH",
        price=2050.0,
        timeframe="1M",
        occurred_at_ms=100000,
        known_at_ms=120000,
        source_leg_id="leg_1",
        price_basis="EXACT_PIVOT",
    )
    # Bar that breached the SHORT protective swing high (2050.0) with high=2055.0
    breaching_bar = SimpleNamespace(open_time=130000, open=2045.0, high=2055.0, low=2044.0, close=2052.0)
    is_valid_breached = validate_anchor_not_invalidated(anchor, [breaching_bar], direction="SHORT", as_of_ms=200000)
    assert not is_valid_breached, "Breached anchor must be rejected"

    # Bar that stayed below the SHORT protective swing high (high=2048.0 < 2050.0)
    intact_bar = SimpleNamespace(open_time=130000, open=2042.0, high=2048.0, low=2040.0, close=2045.0)
    is_valid_intact = validate_anchor_not_invalidated(anchor, [intact_bar], direction="SHORT", as_of_ms=200000)
    assert is_valid_intact, "Intact anchor must remain valid"
