import pytest
from services.stop_placement_types import (
    StopPlacementMode,
    StopPlacementPolicy,
    StopStatus,
    StopAnchorResult,
    StopBufferResult,
    StopATRResult,
)
from services.stop_placement_service import plan_structural_stop
from services.scalp_m1_model_service import ScalpM1ModelService
from domain_calculator import CostAssumptions


def test_routing_modes_legacy_shadow_paper_structural():
    """
    Tests routing across the 3 modes:
    1. LEGACY: applies Variant A (legacy SL).
    2. SHADOW_COMPARE: applies Variant A for execution, but attaches comparison plan for research.
    3. PAPER_STRUCTURAL: applies Variant C (structural SL + dynamic buffer).
    """
    entry = 4000.0
    legacy_sl = 4005.0  # dist 5.0
    tp = 3980.0

    anchor_res = StopAnchorResult(
        status="AVAILABLE",
        anchor=type("Anchor", (), {"price": 4008.0, "kind": "PROTECTED_LEG_HIGH"})(),
    )
    buffer_res = StopBufferResult(
        buffer_price=1.50,
        components={"tick_floor": 0.05, "atr_m1": 1.50},
        dominant_source="atr_m1",
        tick_size=0.01,
    )

    # 1. LEGACY
    policy_legacy = StopPlacementPolicy(mode="LEGACY", version=1)
    dec_legacy = plan_structural_stop(
        direction="SHORT",
        planned_entry=entry,
        take_profit=tp,
        legacy_sl=legacy_sl,
        anchor_result=anchor_res,
        buffer_result=buffer_res,
        policy=policy_legacy,
        as_of_ms=1000,
    )
    assert dec_legacy.applied_variant == "A"
    assert dec_legacy.selected_sl == legacy_sl

    # 2. SHADOW_COMPARE
    policy_shadow = StopPlacementPolicy(mode="SHADOW_COMPARE", version=1)
    dec_shadow = plan_structural_stop(
        direction="SHORT",
        planned_entry=entry,
        take_profit=tp,
        legacy_sl=legacy_sl,
        anchor_result=anchor_res,
        buffer_result=buffer_res,
        policy=policy_shadow,
        as_of_ms=1000,
    )
    assert dec_shadow.applied_variant == "A"
    assert dec_shadow.selected_sl == legacy_sl  # Production geometry uses A

    # 3. PAPER_STRUCTURAL
    policy_paper = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)
    atr_m1 = StopATRResult(
        value=2.0,
        status="AVAILABLE",
        bars_count=15,
        source_candle_count=15,
        timeframe="1M",
        symbol="XAUUSDT",
        last_closed_at_ms=900,
        last_closed_candle_timestamp=900,
        calculated_as_of=1000,
        decision_cutoff=1000,
        method="SMA_TR_V1",
        period=14,
    )
    dec_paper = plan_structural_stop(
        direction="SHORT",
        planned_entry=entry,
        take_profit=tp,
        legacy_sl=legacy_sl,
        anchor_result=anchor_res,
        buffer_result=buffer_res,
        policy=policy_paper,
        as_of_ms=1000,
        atr_m1_obj=atr_m1,
        symbol="XAUUSDT",
    )
    assert dec_paper.applied_variant == "C"
    # anchor 4008.0 + buffer 1.50 = 4009.50
    assert dec_paper.selected_sl == 4009.50
    assert dec_paper.selected_sl > legacy_sl


def test_missing_evidence_does_not_block_shadow_but_waits_in_paper_structural():
    """
    When structural anchor is unavailable:
    - SHADOW_COMPARE falls back safely to legacy A (does not block trade).
    - PAPER_STRUCTURAL returns WAITING_EVIDENCE / does not execute structural C.
    """
    entry = 4000.0
    legacy_sl = 4005.0
    tp = 3980.0

    missing_anchor_res = StopAnchorResult(
        status="UNAVAILABLE",
        anchor=None,
        reasons=("MISSING_STRUCTURAL_ANCHOR",),
    )
    buffer_res = StopBufferResult(
        buffer_price=1.0,
        components={"tick_floor": 0.05},
        dominant_source="tick_floor",
        tick_size=0.01,
    )

    # In SHADOW_COMPARE, returns WAITING_EVIDENCE status for research, but selected_sl remains legacy_sl
    policy_shadow = StopPlacementPolicy(mode="SHADOW_COMPARE", version=1)
    dec_shadow = plan_structural_stop(
        direction="SHORT",
        planned_entry=entry,
        take_profit=tp,
        legacy_sl=legacy_sl,
        anchor_result=missing_anchor_res,
        buffer_result=buffer_res,
        policy=policy_shadow,
        as_of_ms=1000,
    )
    assert dec_shadow.status == StopStatus.WAITING_EVIDENCE.value
    assert dec_shadow.selected_sl == legacy_sl

    # In PAPER_STRUCTURAL, status is WAITING_EVIDENCE and applied_variant cannot be C
    policy_paper = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)
    dec_paper = plan_structural_stop(
        direction="SHORT",
        planned_entry=entry,
        take_profit=tp,
        legacy_sl=legacy_sl,
        anchor_result=missing_anchor_res,
        buffer_result=buffer_res,
        policy=policy_paper,
        as_of_ms=1000,
    )
    assert dec_paper.status == StopStatus.WAITING_EVIDENCE.value
    assert dec_paper.applied_variant != "C"
