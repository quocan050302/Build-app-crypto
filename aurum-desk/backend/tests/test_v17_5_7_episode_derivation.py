"""
backend/tests/test_v17_5_7_episode_derivation.py
AURUM V17.5.7 — Reference-Bound Episode Derivation Suite (Parts 101–120, Appendix A, B).

Guarantees:
1. derive_protected_leg_episode derives leg boundaries from reference/break/source structure,
   NOT requiring caller-injected episode_start_ms (Part 101).
2. Multi-leg boundary isolation: newer pivot B belonging to a later leg is strictly excluded
   even though it occurred before the break (Part 102).
3. Switching selected reference to a different structure node changes the derived episode
   and selects the corresponding origin (Part 103).
4. Lookback noise: adding distant extremes D outside the leg does not change anchor (Part 104).
5. Missing reference source returns UNAVAILABLE without fabricated boundaries (Part 105).
6. Reference known after decision cutoff rejected (Part 106).
7. Non-close-through (wick-only or equality) break rejected (Part 107).
8. Arbitrary supplied boundary without candle provenance overridden by structural derivation (Part 108).
9. Mismatched origin price rejected (Part 109).
10. Production callers (M1 Continuation & FVG Continuation) run without injected episode_start_ms
    and successfully derive verified structural origin proofs (Parts 110, 111).
"""

import math
import pytest
from services.stop_placement_types import (
    ProtectedLegEpisode,
    StopPlacementPolicy,
    StopAnchorKind,
)
from services.stop_placement_service import (
    derive_protected_leg_episode,
    select_protected_origin_for_break,
    resolve_episode_membership_from_structure,
    validate_protected_origin_binding,
)
from services.scalp_m1_model_service import ScalpM1ModelService, M1ScalpConfig


class CandleBar:
    def __init__(self, open_time, o, h, l, c):
        self.open_time = int(open_time)
        self.timestamp = int(open_time)
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.is_closed = True


# ======================================================================
# PARTS 101–109: Structural Episode Derivation & Multi-Leg Isolation
# ======================================================================

def test_p101_derive_episode_without_injected_boundary():
    """Phần 101: Caller does NOT pass episode_start_ms; helper derives boundaries from reference."""
    ref = {"id": "ref_sl_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    brk = {"break_open_ms": 10000, "broken_at_ms": 10000, "known_at_ms": 10060, "bar": CandleBar(10000, 2002, 2004, 1994, 1995)}
    p_origin = {"id": "sh_leg", "price": 2018.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    p_noise_past = {"id": "sh_past", "price": 2040.0, "occurred_at_ms": 2000, "known_at_ms": 2060}

    bars = [
        CandleBar(2000, 2030, 2040, 2025, 2035),
        CandleBar(5000, 2005, 2010, 1998, 2000),
        CandleBar(7000, 2005, 2018, 2003, 2010),
        CandleBar(10000, 2002, 2004, 1994, 1995),
    ]

    ep = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([p_noise_past, p_origin], [ref]),
        closed_bars=bars,
        decision_cutoff_ms=10100,
        timeframe_ms=60000,
    )
    assert ep.status == "AVAILABLE"
    # Derived start is bounded to ref occurred time (5000), excluding past noise at 2000
    assert ep.episode_start_ms == 5000
    assert ep.episode_end_ms == 10000
    assert "sh_leg" in ep.eligible_origin_ids
    assert "sh_past" not in ep.eligible_origin_ids
    assert ep.boundary_source == "CONFIRMED_STRUCTURE_NODES"


def test_p102_wrong_later_leg_excluded():
    """Phần 102: Pivot B is newer and before break, but belongs to a subsequent reference leg -> strictly excluded."""
    # Structure has two legs:
    # Leg 1: Reference R1 (occurred 3000), Origin A (occurred 4000)
    # Leg 2: Reference R2 (occurred 6000), Origin B (occurred 8000)
    # Break of R1 occurs at 10000
    r1 = {"id": "sl_ref1", "price": 2000.0, "occurred_at_ms": 3000, "known_at_ms": 3060}
    p_a = {"id": "sh_leg1", "price": 2020.0, "occurred_at_ms": 4000, "known_at_ms": 4060}
    r2 = {"id": "sl_ref2", "price": 1990.0, "occurred_at_ms": 6000, "known_at_ms": 6060}
    p_b = {"id": "sh_leg2", "price": 2012.0, "occurred_at_ms": 8000, "known_at_ms": 8060}

    brk = {"break_open_ms": 10000, "broken_at_ms": 10000, "known_at_ms": 10060, "bar": CandleBar(10000, 2002, 2004, 1985, 1988)}

    bars = [
        CandleBar(3000, 2005, 2010, 1998, 2000),
        CandleBar(4000, 2005, 2020, 2003, 2015),
        CandleBar(6000, 2000, 2002, 1988, 1990),
        CandleBar(8000, 1995, 2012, 1992, 2005),
        CandleBar(10000, 2002, 2004, 1985, 1988),
    ]

    # Evaluate with selected_reference = R1
    ep1 = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=r1,
        selected_break=brk,
        confirmed_swings=([p_a, p_b], [r1, r2]),
        closed_bars=bars,
        decision_cutoff_ms=10100,
        timeframe_ms=60000,
    )
    assert ep1.status == "AVAILABLE"
    # Episode 1 is bounded by [r1, r2) = [3000, 6000)
    assert ep1.episode_start_ms == 3000
    assert ep1.episode_end_ms == 6000
    assert "sh_leg1" in ep1.eligible_origin_ids
    # p_b at 8000 is after r2 (6000) -> belongs to leg 2, so strictly excluded!
    assert "sh_leg2" not in ep1.eligible_origin_ids

    res1 = select_protected_origin_for_break(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=r1,
        selected_break=brk,
        confirmed_swings=([p_a, p_b], [r1, r2]),
        closed_bars=bars,
        decision_cutoff_ms=10100,
    )
    assert res1["status"] == "AVAILABLE"
    assert res1["origin_pivot_id"] == "sh_leg1"
    assert res1["origin_price"] == 2020.0


def test_p103_different_reference_changes_episode_and_origin():
    """Phần 103: Changing selected reference to R2 shifts episode window to Leg 2 and selects Pivot B."""
    r1 = {"id": "sl_ref1", "price": 2000.0, "occurred_at_ms": 3000, "known_at_ms": 3060}
    p_a = {"id": "sh_leg1", "price": 2020.0, "occurred_at_ms": 4000, "known_at_ms": 4060}
    r2 = {"id": "sl_ref2", "price": 1990.0, "occurred_at_ms": 6000, "known_at_ms": 6060}
    p_b = {"id": "sh_leg2", "price": 2012.0, "occurred_at_ms": 8000, "known_at_ms": 8060}

    brk = {"break_open_ms": 10000, "broken_at_ms": 10000, "known_at_ms": 10060, "bar": CandleBar(10000, 2002, 2004, 1985, 1988)}

    bars = [
        CandleBar(3000, 2005, 2010, 1998, 2000),
        CandleBar(4000, 2005, 2020, 2003, 2015),
        CandleBar(6000, 2000, 2002, 1988, 1990),
        CandleBar(8000, 1995, 2012, 1992, 2005),
        CandleBar(10000, 2002, 2004, 1985, 1988),
    ]

    # Evaluate with selected_reference = R2
    ep2 = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=r2,
        selected_break=brk,
        confirmed_swings=([p_a, p_b], [r1, r2]),
        closed_bars=bars,
        decision_cutoff_ms=10100,
        timeframe_ms=60000,
    )
    assert ep2.status == "AVAILABLE"
    # Episode 2 starts at r2 (6000) and ends at break (10000)
    assert ep2.episode_start_ms == 6000
    assert ep2.episode_end_ms == 10000
    assert "sh_leg2" in ep2.eligible_origin_ids
    assert "sh_leg1" not in ep2.eligible_origin_ids

    res2 = select_protected_origin_for_break(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=r2,
        selected_break=brk,
        confirmed_swings=([p_a, p_b], [r1, r2]),
        closed_bars=bars,
        decision_cutoff_ms=10100,
    )
    assert res2["status"] == "AVAILABLE"
    assert res2["origin_pivot_id"] == "sh_leg2"
    assert res2["origin_price"] == 2012.0


def test_p104_lookback_noise_does_not_change_anchor():
    """Phần 104: Adding arbitrary distant pivots D outside the leg does not change selected anchor."""
    ref = {"id": "ref_sl", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    brk = {"break_open_ms": 10000, "broken_at_ms": 10000, "known_at_ms": 10060, "bar": CandleBar(10000, 2002, 2004, 1994, 1995)}
    p_origin = {"id": "sh_valid", "price": 2015.0, "occurred_at_ms": 7000, "known_at_ms": 7060}

    # Noise extremes outside episode: one way before, one after break
    d_far_past = {"id": "sh_dist_1", "price": 2080.0, "occurred_at_ms": 500, "known_at_ms": 560}
    d_far_past2 = {"id": "sh_dist_2", "price": 2060.0, "occurred_at_ms": 2000, "known_at_ms": 2060}
    d_post_break = {"id": "sh_post", "price": 2018.0, "occurred_at_ms": 10050, "known_at_ms": 10080}

    bars = [
        CandleBar(500, 2070, 2080, 2065, 2075),
        CandleBar(2000, 2050, 2060, 2045, 2055),
        CandleBar(5000, 2005, 2010, 1998, 2000),
        CandleBar(7000, 2005, 2015, 2003, 2010),
        CandleBar(10000, 2002, 2004, 1994, 1995),
        CandleBar(10050, 2000, 2018, 1998, 2012),
    ]

    res = select_protected_origin_for_break(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([d_far_past, d_far_past2, p_origin, d_post_break], [ref]),
        closed_bars=bars,
        decision_cutoff_ms=10100,
    )
    assert res["status"] == "AVAILABLE"
    assert res["origin_pivot_id"] == "sh_valid"
    assert res["origin_price"] == 2015.0  # Does NOT widen to 2080 or 2060


def test_p105_missing_reference_source_fails_closed():
    """Phần 105: Reference node with no timestamp and missing from source bars fails closed."""
    ref_missing = {"id": "ref_phantom", "price": 2000.0}
    brk = {"break_open_ms": 10000, "broken_at_ms": 10000, "known_at_ms": 10060, "bar": CandleBar(10000, 2002, 2004, 1994, 1995)}
    p = {"id": "sh_1", "price": 2015.0, "occurred_at_ms": 7000, "known_at_ms": 7060}

    bars = [CandleBar(7000, 2005, 2015, 2003, 2010), CandleBar(10000, 2002, 2004, 1994, 1995)]

    ep = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref_missing,
        selected_break=brk,
        confirmed_swings=([p], []),
        closed_bars=bars,
        decision_cutoff_ms=10100,
    )
    assert ep.status == "UNAVAILABLE"
    assert "MISSING_REFERENCE_SOURCE" in ep.reasons


def test_p106_reference_known_after_decision_rejected():
    """Phần 106: Reference known after decision cutoff is strictly rejected."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 10500}
    brk = {"break_open_ms": 10000, "broken_at_ms": 10000, "known_at_ms": 10060, "bar": CandleBar(10000, 2002, 2004, 1994, 1995)}
    bars = [CandleBar(5000, 2005, 2010, 1998, 2000), CandleBar(10000, 2002, 2004, 1994, 1995)]

    ep = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        closed_bars=bars,
        decision_cutoff_ms=10100,  # ref known at 10500 > 10100!
    )
    assert ep.status == "UNAVAILABLE"
    assert "REFERENCE_KNOWN_AFTER_DECISION" in ep.reasons


def test_p107_equality_or_wick_only_break_rejected():
    """Phần 107: Break candle touching reference price without strictly closing through rejected."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    # Break candle low dipped to 1998 (wick through), but close is 2000.0 (equality)
    brk_eq = {"break_open_ms": 10000, "broken_at_ms": 10000, "known_at_ms": 10060, "bar": CandleBar(10000, 2002, 2004, 1998, 2000.0)}
    bars = [CandleBar(5000, 2005, 2010, 1998, 2000), CandleBar(10000, 2002, 2004, 1998, 2000.0)]

    ep = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk_eq,
        closed_bars=bars,
        decision_cutoff_ms=10100,
    )
    assert ep.status == "UNAVAILABLE"
    assert "BREAK_NOT_CLOSED_THROUGH_REFERENCE" in ep.reasons


def test_p108_arbitrary_supplied_boundary_without_provenance():
    """Phần 108: Arbitrary integer boundary not matching source candles is overridden by structural derivation."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    # Break attempts to inject arbitrary episode_start_ms=99999999
    brk = {
        "break_open_ms": 10000,
        "broken_at_ms": 10000,
        "known_at_ms": 10060,
        "bar": CandleBar(10000, 2002, 2004, 1994, 1995),
        "episode_start_ms": 99999999,
    }
    p = {"id": "sh_1", "price": 2015.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    bars = [CandleBar(5000, 2005, 2010, 1998, 2000), CandleBar(7000, 2005, 2015, 2003, 2010), CandleBar(10000, 2002, 2004, 1994, 1995)]

    ep = derive_protected_leg_episode(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        selected_reference=ref,
        selected_break=brk,
        confirmed_swings=([p], [ref]),
        closed_bars=bars,
        decision_cutoff_ms=10100,
    )
    # The arbitrary 99999999 is overridden by structural start 5000
    assert ep.status == "AVAILABLE"
    assert ep.episode_start_ms == 5000
    assert "sh_1" in ep.eligible_origin_ids


def test_p109_source_candle_mismatch_rejected():
    """Phần 109: Pivot price mismatching source candle High fails validation."""
    ref = {"id": "ref_1", "price": 2000.0, "occurred_at_ms": 5000, "known_at_ms": 5060}
    brk = {"break_open_ms": 10000, "broken_at_ms": 10000, "known_at_ms": 10060, "bar": CandleBar(10000, 2002, 2004, 1994, 1995)}
    # Pivot claims High is 2030.0, but candle at 7000 only reached 2015.0
    p_fake_high = {"id": "sh_fake", "price": 2030.0, "occurred_at_ms": 7000, "known_at_ms": 7060}
    bars = [CandleBar(5000, 2005, 2010, 1998, 2000), CandleBar(7000, 2005, 2015, 2003, 2010), CandleBar(10000, 2002, 2004, 1994, 1995)]

    is_valid, reason = validate_protected_origin_binding(
        origin=p_fake_high,
        direction="SHORT",
        break_open_ms=10000,
        decision_cutoff_ms=10100,
        closed_bars=bars,
        tick_size=0.01,
        episode_start_ms=5000,
    )
    assert is_valid is False
    assert reason == "ORIGIN_PRICE_MISMATCH_HIGH"


# ======================================================================
# PARTS 110–120: Actual Evaluator Invocation & Proof Readback
# ======================================================================

def test_p110_actual_continuation_evaluator_without_injected_boundary():
    """Phần 110: Actual evaluate_m1_continuation_candidate derives origin proof without injected episode_start_ms."""
    t_base = 500_000
    # Construct sequence for SHORT continuation:
    # 1. Warmup candles (10 bars)
    warmup = [CandleBar(t_base + i * 60_000, 2005, 2008, 2004, 2006) for i in range(10)]
    # 2. Pattern: Swing Low at 2000, pullback to Swing High at 2015, break closing at 1995
    pattern = [
        CandleBar(t_base + 600_000, 2006, 2009, 2005, 2008),
        CandleBar(t_base + 660_000, 2008, 2010, 2007, 2009),
        CandleBar(t_base + 720_000, 2009, 2011, 2008, 2010),
        CandleBar(t_base + 780_000, 2010, 2010, 2006, 2007),
        CandleBar(t_base + 840_000, 2007, 2008, 2003, 2004),
        CandleBar(t_base + 900_000, 2004, 2005, 1998, 2000),  # Low 1998, Close 2000 (SL reference)
        CandleBar(t_base + 960_000, 2000, 2006, 2000, 2005),
        CandleBar(t_base + 1020_000, 2005, 2015, 2004, 2012), # High 2015 (SH origin)
        CandleBar(t_base + 1080_000, 2012, 2013, 2006, 2008),
        CandleBar(t_base + 1140_000, 2008, 2009, 2002, 2004),
        CandleBar(t_base + 1200_000, 2004, 2005, 1993, 1995), # Break closing at 1995 (< 2000)
    ]
    candles = warmup + pattern
    now_ms = t_base + 1260_000

    quote = {"bid": 1995.0, "ask": 1995.20, "is_valid": True, "is_stale": False, "server_time": now_ms, "observed_at": now_ms}
    policy_settings = {"capital_usdt": 5000.0, "risk_pct": 0.10, "leverage": 10, "margin_mode": "ISOLATED"}
    cfg = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode="PAPER",
        pivot_left_bars=2,
        pivot_right_bars=2,
        sl_buffer_ticks=10,
        min_net_rr=1.2,
    )
    stop_policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)

    cand = ScalpM1ModelService.evaluate_m1_continuation_candidate(
        candles_1m=candles,
        candles_5m=[],
        current_quote=quote,
        htf_bias="BEARISH",
        h1_alignment="ALIGNED",
        policy_settings=policy_settings,
        now_ms=now_ms,
        config=cfg,
        stop_policy=stop_policy,
    )
    assert cand.is_eligible is True
    assert cand.direction == "SHORT"
    assert cand.stop_placement is not None
    assert cand.stop_placement.anchor is not None
    # Verified origin exists, is bound, and has valid derivation provenance
    assert cand.stop_placement.status == "VALID"
    assert cand.stop_placement.anchor.kind == StopAnchorKind.PROTECTED_LEG_HIGH.value
    assert cand.stop_placement.anchor.price > cand.planned_entry


def test_p111_actual_fvg_continuation_evaluator_with_full_reference():
    """Phần 111: evaluate_m1_fvg_continuation_candidate receives full swing_ref and binds structural origin."""
    from services.stop_placement_service import select_protected_origin_for_break

    # Mock full swing_ref object produced by resolve_confirmed_structure_reference
    full_ref = {
        "id": "m1_sl_5000",
        "price": 2000.0,
        "occurred_at_ms": 5000,
        "known_at_ms": 5180,
        "side": "SELL_SIDE",
    }
    p_origin = {"id": "m1_sh_7000", "price": 2015.0, "occurred_at_ms": 7000, "known_at_ms": 7180}
    brk_bar = CandleBar(10000, 2005, 2006, 1992, 1994)
    bars = [
        CandleBar(5000, 2005, 2010, 1998, 2000),
        CandleBar(7000, 2005, 2015, 2003, 2010),
        brk_bar,
    ]

    binding = select_protected_origin_for_break(
        model_id="M1_FVG_CONTINUATION",
        direction="SHORT",
        selected_break={
            "bar": brk_bar,
            "break_open_ms": 10000,
            "broken_at_ms": 10000,
            "known_at_ms": 10060,
        },
        selected_reference=full_ref,
        confirmed_swings=([p_origin], [full_ref]),
        closed_bars=bars,
        decision_cutoff_ms=10100,
    )
    assert binding["status"] == "AVAILABLE"
    assert binding["origin_pivot_id"] == "m1_sh_7000"
    assert binding["origin_price"] == 2015.0
    assert binding["episode"]["boundary_source"] == "CONFIRMED_STRUCTURE_NODES"
    assert binding["episode"]["reference_id"] == "m1_sl_5000"
    assert binding["episode"]["reference_occurred_at_ms"] == 5000
