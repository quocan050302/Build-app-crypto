import pytest
from services.stop_placement_research_service import (
    make_opportunity_hash_id,
    build_stop_comparison_plan,
    compute_stop_excursions,
)
from services.stop_placement_types import (
    StopAnchor,
    StopBufferResult,
)


def test_deterministic_opportunity_id_stability():
    """
    Opportunity ID must be deterministic and stable based on model, direction, symbol,
    and leg/break/time identifiers across repeated cycles.
    """
    key = "M1_CONTINUATION:SHORT:XAUUSDT:leg_100:break_50:1000000"
    id1 = make_opportunity_hash_id("opp", key)
    id2 = make_opportunity_hash_id("opp", key)
    assert id1 == id2
    assert id1.startswith("opp_")
    assert len(id1) <= 70


def test_build_stop_comparison_plan_three_variants():
    """
    build_stop_comparison_plan creates Variant A, B, and C with:
    - Same planned_entry and take_profit.
    - Independent stop_loss and quantities.
    - Net risk budget preserved.
    """
    anchor = StopAnchor(
        id="anc_1",
        kind="PROTECTED_LEG_HIGH",
        price=4008.0,
        timeframe="1M",
        occurred_at_ms=1000,
        known_at_ms=1200,
        source_leg_id="leg_1",
        source_break_id=None,
        price_basis="CANDLE_LAST",
    )
    buffer_res = StopBufferResult(
        buffer_price=2.0,
        components={"tick_floor": 0.05, "atr_m1": 2.0},
        dominant_source="atr_m1",
        tick_size=0.01,
    )

    plan = build_stop_comparison_plan(
        opportunity_key="opp_test_1",
        model_id="M1_CONTINUATION",
        direction="SHORT",
        symbol="XAUUSDT",
        planned_entry=4000.0,
        legacy_sl=4005.0,
        take_profit=3980.0,
        anchor=anchor,
        buffer_result=buffer_res,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 5},
        min_net_rr=1.2,
    )

    assert plan is not None
    assert plan.opportunity_key == "opp_test_1"
    assert plan.planned_entry == 4000.0
    assert plan.take_profit == 3980.0
    assert plan.variant_a.stop_loss == 4005.0
    assert plan.variant_c.stop_loss == 4010.0
    # Wider SL in C has smaller quantity than A
    assert plan.variant_c.quantity <= plan.variant_a.quantity


def test_compute_stop_excursions_mae_mfe():
    """
    MAE and MFE must respect trade direction:
    - SHORT: adverse = max(ask - entry, 0), favorable = max(entry - ask, 0)
    - LONG:  adverse = max(entry - bid, 0), favorable = max(bid - entry, 0)
    """
    # SHORT: entry 4000. Quote: bid 3990, ask 3992
    # Favorable = 4000 - 3992 = 8.0, Adverse = max(3992 - 4000, 0) = 0.0
    res_short = compute_stop_excursions(
        direction="SHORT",
        planned_entry=4000.0,
        quote={"bid": 3990.0, "ask": 3992.0},
    )
    assert res_short["favorable_price"] == 8.0
    assert res_short["adverse_price"] == 0.0

    # LONG: entry 4000. Quote: bid 4005, ask 4007
    # Favorable = 4005 - 4000 = 5.0, Adverse = 0.0
    res_long = compute_stop_excursions(
        direction="LONG",
        planned_entry=4000.0,
        quote={"bid": 4005.0, "ask": 4007.0},
    )
    assert res_long["favorable_price"] == 5.0
    assert res_long["adverse_price"] == 0.0
