from types import SimpleNamespace

import pytest

from services.v17_9_3_1_2_2_signal_intelligence import _resolve_unswept_liquidity_pool_target_v1793123
from services.v17_9_3_1_2_7_causal_signal_truth import (
    derive_pre_entry_features_from_causal_context_v1793127,
    rebuild_materialized_intent_proof_v1793127,
    verify_feature_snapshot_v1793127,
)


@pytest.mark.parametrize("swap_sweep,swap_break", [(False, False), (True, False), (False, True)])
def test_selected_sweep_and_break_use_exact_bar_and_unique_pivot(swap_sweep, swap_break):
    base = 1_790_000_100_000
    # Unique low at 2 and high at 5, both confirmed before the sweep at 8.
    prices = [(101, 99, 100), (101, 99, 100), (101, 95, 100),
              (101, 99, 100), (102, 99, 100), (105, 99, 100),
              (102, 99, 100), (102, 99, 100), (102, 94, 100),
              (107, 99, 106), (108, 102, 106)]
    bars = [dict(open_time=base + i * 60_000, timestamp=base + i * 60_000,
                 close_time=base + (i + 1) * 60_000, open=100 if i < 10 else 106,
                 high=high, low=low, close=close, is_closed=True)
            for i, (high, low, close) in enumerate(prices)]
    model = "SMC_M1_REVERSAL"
    cutoff = bars[-1]["close_time"]
    decision = dict(opportunity_id="fixture", setup_episode_id="setup", model_id=model, direction="LONG",
                    selected_sweep_id=f"sweep_{model}_{bars[7 if swap_sweep else 8]['open_time']}",
                    selected_break_id=f"bos_LONG_{bars[10 if swap_break else 9]['open_time']}")
    feature = derive_pre_entry_features_from_causal_context_v1793127(
        decision, SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars),
        dataset_sha256="a" * 64,
    )
    assert feature["sweep_confirmed"] is (None if swap_sweep else True)
    assert feature["mss_confirmed"] is (None if swap_break else True)
    assert feature["sweep_mss_ordered"] is (None if swap_sweep or swap_break else True)


@pytest.mark.parametrize("quantity,expected", [(0.1, None), (0.11, "REJECTED_UNPROVEN:FINAL_RISK_OR_QUANTITY_MISMATCH")])
def test_final_intent_rechecks_size_after_repricing(monkeypatch, quantity, expected):
    # Isolate risk handoff from proof eligibility. The real sizing engine still runs.
    from services import v17_9_3_1_2_7_causal_signal_truth as truth
    from services.v17_9_3_1_2_executable_intervention_replay_service import recalculate_trade_risk_quantity_v179312

    monkeypatch.setattr(truth, "required_proof_rejection_v1793127", lambda *args, **kwargs: None)
    cutoff = 1_790_000_160_000
    sized = recalculate_trade_risk_quantity_v179312(2650, 2645, 2680, direction="LONG", decision_ts=cutoff)
    assert sized["can_execute"]
    final_qty = sized["quantity"] if quantity == 0.1 else sized["quantity"] + 0.01
    bar = dict(open_time=cutoff - 60_000, close_time=cutoff, open=2650, high=2651, low=2649, close=2650, is_closed=True)
    opp = dict(opportunity_id="fixture", setup_episode_id="setup", model_id="SMC_M1_REVERSAL", direction="LONG",
               decision_timestamp_ms=cutoff, entry_reference=2649, selected_sl=2645, take_profit=2680)
    plan = dict(new_decision_timestamp_ms=cutoff, new_checkpoint_index_1m=0, new_entry_reference=2650,
                new_selected_sl=2645, new_take_profit=2680, new_quantity=final_qty)
    _, proof, rejection = rebuild_materialized_intent_proof_v1793127(opp, plan, {"1m": [bar]}, dataset_sha256="a" * 64)
    assert proof["decision_geometry"]["entry_reference"] == 2650
    assert rejection == expected


@pytest.mark.parametrize("pool", [
    {"pool_id": "p1", "price": 103, "timestamp_ms": 120_000},
    {"pool_id": "p1", "price": 103, "known_at_ms": True},
    {"pool_id": "p1", "price": 103, "known_at_ms": 360_001},
    {"price": 103, "known_at_ms": 180_000},
])
def test_pool_without_causal_identity_cannot_be_selected(pool):
    result = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[pool], closed_1m=[], direction="LONG", entry_price=100, selected_sl=98,
        selected_target_id="p1", take_profit=103, now_ms=360_000, eff_atr_m1=1, eff_atr_m5=2,
    )
    assert result == (None, 103, 0, False, False)


def test_pool_id_and_explicit_known_at_are_retained():
    pool = {"pool_id": "p1", "price": 103, "occurred_at_ms": 120_000, "known_at_ms": 180_000}
    bars = [
        {
            "open_time": 120_000 + i * 60_000,
            "close_time": 180_000 + i * 60_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "is_closed": True,
        }
        for i in range(4)
    ]
    result = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[pool], closed_1m=bars, direction="LONG", entry_price=100, selected_sl=98,
        selected_target_id="p1", take_profit=103, now_ms=360_000, eff_atr_m1=1, eff_atr_m5=2,
    )
    assert result == ("p1", 103, 180_000, True, False)

    empty_path_result = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[pool], closed_1m=[], direction="LONG", entry_price=100, selected_sl=98,
        selected_target_id="p1", take_profit=103, now_ms=360_000, eff_atr_m1=1, eff_atr_m5=2,
    )
    assert empty_path_result == (None, 103, 0, False, False)


@pytest.mark.parametrize("duplicate, stop", [(True, 98), (False, 101)])
def test_ambiguous_pool_or_unprotective_stop_cannot_certify_target(duplicate, stop):
    pool = {"pool_id": "p1", "price": 103, "known_at_ms": 180_000}
    pools = [pool, {**pool, "price": 104}] if duplicate else [pool]
    result = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=pools, closed_1m=[], direction="LONG", entry_price=100, selected_sl=stop,
        selected_target_id="p1", take_profit=103, now_ms=360_000, eff_atr_m1=1, eff_atr_m5=2,
    )
    assert result == (None, 103, 0, False, False)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("mutation", [None, "future_confirmation", "wrong_price", "unrelated_id", "source_gap"])
def test_selected_m5_anchor_matches_exact_closed_detector_source(direction, mutation):
    base = 1_790_000_100_000
    duration = 300_000
    high = direction == "SHORT"
    bars = []
    for i in range(3):
        opened = base + i * duration
        bars.append(dict(timestamp=opened, open_time=opened, close_time=opened + duration,
                         open=100, close=100, high=105 if high and i == 1 else 102,
                         low=95 if not high and i == 1 else 98, is_closed=True))
    pivot = "sh" if high else "sl"
    cutoff = bars[-1]["close_time"] - (1 if mutation == "future_confirmation" else 0)
    decision = dict(opportunity_id="fixture", setup_episode_id="setup", direction=direction,
                    model_id="SMC_M1_REVERSAL", entry_reference=100, selected_sl=106 if high else 94,
                    selected_anchor_id=f"v1792_m5_5m_{pivot}_{base + duration}",
                    structural_invalidation_price=105 if high else 95)
    if mutation == "wrong_price":
        decision["structural_invalidation_price"] += 0.01
    elif mutation == "unrelated_id":
        decision["selected_anchor_id"] += "_different"
    elif mutation == "source_gap":
        bars[0]["open_time"] -= duration
        bars[0]["timestamp"] -= duration
        bars[0]["close_time"] -= duration
    context = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m5_candles=bars)
    result = derive_pre_entry_features_from_causal_context_v1793127(decision, context, dataset_sha256="a" * 64)
    assert verify_feature_snapshot_v1793127(result)
    assert result["protected_structure_independent_of_sl"] is (True if mutation is None else None)
    if mutation is None:
        proof = next(ref for ref in result["causal_snapshot"]["features"]
                     if ref["evidence_type"] == "protected_structure_independent_of_sl")
        assert proof["timeframe"] == "5M"
        assert proof["source_bar_count"] == 3
        assert proof["occurred_at_ms"] == base + duration
        assert proof["known_at_ms"] == cutoff


@pytest.mark.parametrize("mutation", [None, "swept", "missing_path", "wrong_price", "unclosed_confirmation"])
def test_selected_m5_target_requires_exact_pool_and_complete_unswept_path(mutation):
    base = 1_790_000_100_000
    bars = [dict(open_time=base + i * 60_000, close_time=base + (i + 1) * 60_000,
                 open=100, close=100, high=105 if 5 <= i < 10 else 102,
                 low=98, is_closed=True) for i in range(20)]
    if mutation == "swept":
        bars[16]["high"] = 105
    m5 = [dict(open_time=base + i * 300_000, close_time=base + (i + 1) * 300_000,
               open=100, close=100, high=max(b["high"] for b in bars[i * 5:(i + 1) * 5]),
               low=98, is_closed=True) for i in range(4)]
    if mutation == "missing_path":
        bars.pop(17)
        bars.pop()  # The last M5 candle is still open at this earlier cutoff.
    if mutation == "unclosed_confirmation":
        del m5[2]["is_closed"]
    target_id = f"pool_5M_5m_sh_{base + 300_000}"
    decision = dict(opportunity_id="fixture", setup_episode_id="setup", direction="LONG",
                    model_id="SMC_M1_REVERSAL", entry_reference=100, selected_sl=97,
                    take_profit=105.02 if mutation == "wrong_price" else 105, selected_target_id=target_id)
    context = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=bars[-1]["close_time"],
                              closed_m1_candles=bars, closed_m5_candles=m5)
    result = derive_pre_entry_features_from_causal_context_v1793127(decision, context, dataset_sha256="a" * 64)
    assert verify_feature_snapshot_v1793127(result)
    assert result["target_unswept"] is {None: True, "swept": False}.get(mutation)
    assert result["target_reachable"] is None
    if mutation in (None, "swept", "missing_path"):
        assert result["target_pool_id"] == target_id
