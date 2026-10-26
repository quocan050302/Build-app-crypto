import pytest
from services.stop_placement_service import resolve_stop_anchor
from services.stop_placement_types import StopAnchorKind

def test_short_broken_low_not_used_as_protective_anchor():
    """
    In a continuation SHORT, price breaks below a previous swing low.
    The protective anchor MUST be the swing high that originated the break leg,
    NEVER the broken low (which is in the opposite direction).
    """
    sh_list = [
        {"id": "sh_1", "type": "high", "price": 4050.0, "index": 5, "occurred_at_ms": 1000, "known_at_ms": 1200},
        {"id": "sh_origin", "type": "high", "price": 4045.0, "index": 12, "occurred_at_ms": 1800, "known_at_ms": 2000},
    ]
    sl_list = [
        {"id": "sl_1", "type": "low", "price": 4020.0, "index": 8, "occurred_at_ms": 1300, "known_at_ms": 1500},
    ]
    selected_break = {
        "id": "bar_15",
        "broken_swing_id": "sl_1",
        "broken_price": 4020.0,
        "break_time_ms": 2200,
        "known_at_ms": 2200,
    }
    
    anchor_res = resolve_stop_anchor(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        closed_bars=(),
        as_of_ms=2300,
        selected_break=selected_break,
        confirmed_swings=(sh_list, sl_list),
    )
    
    assert anchor_res.status == "AVAILABLE"
    assert anchor_res.anchor is not None
    assert anchor_res.anchor.kind == StopAnchorKind.PROTECTED_LEG_HIGH.value
    # Anchor MUST be 4045.0 (the high), NOT 4020.0 (the broken low)
    assert anchor_res.anchor.price == 4045.0
    assert anchor_res.anchor.price > selected_break["broken_price"]
    assert anchor_res.anchor.source_break_id == "bar_15"


def test_long_continuation_protective_anchor():
    """
    Symmetrical test for LONG: price breaks above swing high 4040.0.
    Protective anchor must be the swing low 4010.0 that originated the break leg.
    """
    sh_list = [
        {"id": "sh_broken", "type": "high", "price": 4040.0, "index": 12, "occurred_at_ms": 1800, "known_at_ms": 2000},
    ]
    sl_list = [
        {"id": "sl_origin", "type": "low", "price": 4010.0, "index": 10, "occurred_at_ms": 1500, "known_at_ms": 1700},
    ]
    selected_break = {
        "id": "bar_14",
        "broken_swing_id": "sh_broken",
        "broken_price": 4040.0,
        "break_time_ms": 2100,
        "known_at_ms": 2100,
    }
    
    anchor_res = resolve_stop_anchor(
        model_id="M1_CONTINUATION",
        direction="LONG",
        closed_bars=(),
        as_of_ms=2200,
        selected_break=selected_break,
        confirmed_swings=(sh_list, sl_list),
    )
    
    assert anchor_res.status == "AVAILABLE"
    assert anchor_res.anchor is not None
    assert anchor_res.anchor.kind == StopAnchorKind.PROTECTED_LEG_LOW.value
    assert anchor_res.anchor.price == 4010.0
    assert anchor_res.anchor.price < selected_break["broken_price"]


def test_future_anchor_rejected():
    """
    If a pivot swing is known only AFTER decision cutoff (known_at_ms > as_of_ms),
    it must be rejected as hindsight.
    """
    sh_list = [
        {"id": "sh_future", "type": "high", "price": 4055.0, "index": 12, "occurred_at_ms": 1800, "known_at_ms": 2500},
    ]
    
    anchor_res = resolve_stop_anchor(
        model_id="M1_CONTINUATION",
        direction="SHORT",
        closed_bars=(),
        as_of_ms=2200,  # 2200 < 2500 -> future!
        confirmed_swings=(sh_list, []),
    )
    
    # Since the only high is in the future, it cannot be used
    assert anchor_res.status == "WAITING_EVIDENCE"
    assert "STRUCTURAL_ANCHOR_NOT_FOUND" in anchor_res.reasons


def test_fvg_origin_distinct_from_fvg_edge():
    """
    In FVG continuation, the legacy SL (A) uses FVG top/bottom edge + fixed buffer.
    The structural SL (B/C) MUST bind to the leg origin / protected extreme,
    proving that B/C is distinctly wider and structural rather than just hugging the FVG edge.
    """
    selected_fvg = {
        "id": "fvg_1",
        "top": 4030.0,
        "bottom": 4025.0,
        "created_bar_id": "bar_10",
        "time_ms": 1500,
    }
    sh_list = [
        {"id": "sh_fvg_origin", "type": "high", "price": 4048.0, "index": 7, "occurred_at_ms": 1100, "known_at_ms": 1300},
    ]
    
    anchor_res = resolve_stop_anchor(
        model_id="M1_FVG_CONTINUATION",
        direction="SHORT",
        closed_bars=(),
        as_of_ms=2000,
        selected_fvg=selected_fvg,
        confirmed_swings=(sh_list, []),
    )
    
    assert anchor_res.status == "AVAILABLE"
    assert anchor_res.anchor is not None
    # Origin swing high is 4048.0, well above FVG top of 4030.0
    assert anchor_res.anchor.price == 4048.0
    assert anchor_res.anchor.price > selected_fvg["top"]


def test_sweep_extreme_binding_multiple_episodes():
    """
    When sweep models execute, resolve_stop_anchor must use
    the exact swept_extreme of the selected episode.
    """
    selected_sweep = {
        "id": "sweep_ep_2",
        "episode_id": "sweep_ep_2",
        "cluster_id": "clust_a",
        "sweep_extreme": 4062.50,
        "time_ms": 1800,
        "known_at_ms": 1900,
    }
    
    anchor_res = resolve_stop_anchor(
        model_id="M1_SWEEP_RECLAIM",
        direction="SHORT",
        closed_bars=(),
        as_of_ms=2000,
        selected_sweep=selected_sweep,
    )
    
    assert anchor_res.status == "AVAILABLE"
    assert anchor_res.anchor is not None
    assert anchor_res.anchor.kind == StopAnchorKind.SWEEP_EXTREME.value
    assert anchor_res.anchor.price == 4062.50
    assert anchor_res.anchor.source_leg_id == "sweep_ep_2"
