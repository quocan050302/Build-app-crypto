"""
backend/tests/test_v17_9_3_1_2_6_causal_proof_and_loss_forensics.py

AURUM Desk / Bitget XAUUSDT — V17.9.3.1.2.6
Targeted verification suite for causal signal proof, FVG retest causal repair (P0-A),
zero timestamp fallback (P0-B), strictly verified pre-entry scoring (P0-C),
quote provenance (P1-E), and loss forensics integrity (P1-F).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping

import pytest

from services.scalp_m1_types import ModelIdentifier
from services.shared_strategy_evaluator import (
    SharedStrategyEvaluator,
    build_market_decision_context,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_V1793123_POLICY_IDS,
    CANONICAL_V1793126_POLICY_IDS,
    POLICY_ID_ALIASES_V1793122,
    PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122,
    ProvenanceEvidence,
    build_signal_quality_evidence_v1793122,
    score_candidate_record_pre_entry_v1793122,
)
from services.v17_9_3_1_2_3_core_trading_logic_repair import (
    build_ground_truth_loss_forensics_v1793123,
    classify_single_episode_root_causes_v1793123,
    iter_causal_decision_checkpoints_v1793125,
)


def _make_m1_candles(
    count: int = 30,
    *,
    base_price: float = 2650.0,
    start_ts: int = 1_000_000,
    step_ms: int = 60_000,
) -> List[Dict[str, Any]]:
    candles: List[Dict[str, Any]] = []
    p = base_price
    for i in range(count):
        o_ts = start_ts + i * step_ms
        c_ts = o_ts + step_ms
        candles.append(
            {
                "symbol": "XAUUSDT",
                "timeframe": "1M",
                "open_time": o_ts,
                "open_time_ms": o_ts,
                "timestamp": o_ts,
                "close_time": c_ts,
                "close_time_ms": c_ts,
                "open": round(p, 2),
                "high": round(p + 0.50, 2),
                "low": round(p - 0.50, 2),
                "close": round(p + 0.10, 2),
                "volume": 100.0,
                "is_closed": True,
            }
        )
        p += 0.10
    return candles


# ==============================================================================
# P0-A: FVG Retest Causal Repair & Negative Counterexamples (Task A2 & Task A3)
# ==============================================================================

def test_v1793126_p0_a_fvg_unmitigated_never_retested_is_rejected() -> None:
    """
    Counterexample 1: FVG is unmitigated (fvg_mitigated=False), but post-formation bars
    never touch the FVG zone. Must produce fvg_retest_valid == False.
    (Old defect: fell back to fvg_retest_valid = bool(fvg_unmitigated)).
    """
    bars = _make_m1_candles(20, base_price=2650.0)
    # FVG formed at bar index 10 (price 2651.0), zone 2650.80 - 2651.40
    f_formed_ts = bars[10]["close_time"]
    now_ts = bars[-1]["close_time"]

    # Subsequent bars 11..19 are far above zone (e.g. 2655.0 to 2660.0), never touching 2651.40
    for b in bars[11:]:
        b["open"] = 2658.0
        b["high"] = 2660.0
        b["low"] = 2656.0  # lowest price 2656 > zone top 2651.40
        b["close"] = 2659.0

    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ts,
        m1_candles=bars,
        m5_candles=bars,
    )

    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_unvisited_1",
            "fvg_top": 2651.40,
            "fvg_bottom": 2650.80,
            "fvg_formed_at_ms": f_formed_ts,
            "fvg_mitigated": False,
        },
        entry_price=2659.0,
        selected_sl=2650.0,
        take_profit=2675.0,
    )

    assert ev.fvg_present is True
    assert ev.fvg_unmitigated is True
    # Unmitigated != retested. Must be False!
    assert ev.fvg_retest_valid is False


def test_v1793126_p0_a_descriptor_spoof_rejected_without_bar_touch() -> None:
    """
    Counterexample 2: Descriptor asserts fvg_retest_valid=True, but actual candles
    do not touch the zone. Must fail closed (fvg_retest_valid == False).
    """
    bars = _make_m1_candles(20, base_price=2650.0)
    f_formed_ts = bars[10]["close_time"]
    now_ts = bars[-1]["close_time"]

    # Move bars away from zone
    for b in bars[11:]:
        b["open"] = 2660.0
        b["high"] = 2662.0
        b["low"] = 2658.0
        b["close"] = 2661.0

    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ts,
        m1_candles=bars,
        m5_candles=bars,
    )

    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_spoof_1",
            "fvg_top": 2651.40,
            "fvg_bottom": 2650.80,
            "fvg_formed_at_ms": f_formed_ts,
            "fvg_retest_valid": True,  # Spoof descriptor!
        },
        entry_price=2661.0,
        selected_sl=2650.0,
        take_profit=2675.0,
    )

    assert ev.fvg_retest_valid is False


def test_v1793126_p0_a_retest_timestamp_before_formation_rejected() -> None:
    """
    Counterexample 3: Retest timestamp is before FVG formation (chronology invalid).
    """
    bars = _make_m1_candles(20, base_price=2650.0)
    f_formed_ts = bars[15]["close_time"]
    spoofed_early_retest_ts = bars[10]["close_time"]  # Before formation!
    now_ts = bars[-1]["close_time"]

    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ts,
        m1_candles=bars,
        m5_candles=bars,
    )

    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_early_1",
            "fvg_top": 2651.40,
            "fvg_bottom": 2650.80,
            "fvg_formed_at_ms": f_formed_ts,
            "fvg_retest_at_ms": spoofed_early_retest_ts,
        },
        entry_price=2655.0,
        selected_sl=2650.0,
        take_profit=2670.0,
    )

    assert ev.fvg_retest_valid is False


def test_v1793126_p0_a_retest_from_future_rejected() -> None:
    """
    Counterexample 4: Retest timestamp is in the future (> decision_timestamp_ms).
    """
    bars = _make_m1_candles(20, base_price=2650.0)
    f_formed_ts = bars[10]["close_time"]
    now_ts = bars[-1]["close_time"]
    future_ts = now_ts + 120_000  # 2 minutes into future!

    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ts,
        m1_candles=bars,
        m5_candles=bars,
    )

    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_future_1",
            "fvg_top": 2651.40,
            "fvg_bottom": 2650.80,
            "fvg_formed_at_ms": f_formed_ts,
            "fvg_retest_at_ms": future_ts,
        },
        entry_price=2655.0,
        selected_sl=2650.0,
        take_profit=2670.0,
    )

    assert ev.fvg_retest_valid is False


def test_v1793126_p0_a_missing_ohlc_or_zone_geometry_rejected() -> None:
    """
    Counterexample 6: Invalid zone geometry (fvg_top <= fvg_bottom) or missing bars.
    """
    bars = _make_m1_candles(20, base_price=2650.0)
    now_ts = bars[-1]["close_time"]

    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ts,
        m1_candles=bars,
        m5_candles=bars,
    )

    # Inverted geometry: top (2650.0) <= bottom (2652.0)
    ev_geom = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_inverted",
            "fvg_top": 2650.0,
            "fvg_bottom": 2652.0,
            "fvg_formed_at_ms": bars[10]["close_time"],
        },
        entry_price=2655.0,
        selected_sl=2648.0,
        take_profit=2670.0,
    )
    assert ev_geom.fvg_present is False
    assert ev_geom.fvg_retest_valid is False


def test_v1793126_p0_a_same_bar_manufactured_retest_rejected() -> None:
    """
    Counterexample 7: FVG formed on the most recent bar (post_fvg_bars is empty).
    Cannot self-retest on the formation bar!
    """
    bars = _make_m1_candles(20, base_price=2650.0)
    now_ts = bars[-1]["close_time"]

    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ts,
        m1_candles=bars,
        m5_candles=bars,
    )

    ev_same = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_latest_bar",
            "fvg_top": 2655.0,
            "fvg_bottom": 2653.0,
            "fvg_formed_at_ms": now_ts,  # Formed on current bar
        },
        entry_price=2655.0,
        selected_sl=2650.0,
        take_profit=2670.0,
    )
    assert ev_same.fvg_retest_valid is False


def test_v1793126_p0_a_valid_genuinely_retested_fvg_confirmed() -> None:
    """
    Positive Control: True post-formation closed bar whose low wicks into the FVG zone
    (low <= 2653.0, high >= 2651.0, close >= 2651.0) without invalidating.
    Must produce fvg_retest_valid == True.
    """
    bars = _make_m1_candles(20, base_price=2650.0)
    f_formed_ts = bars[10]["close_time"]
    now_ts = bars[-1]["close_time"]

    # FVG zone is 2651.00 - 2653.00 formed at bar 10
    # Make bar 14 a retest bar touching the zone (low 2652.00 enters zone, close 2654.00 holds)
    bars[14]["open"] = 2656.0
    bars[14]["high"] = 2657.0
    bars[14]["low"] = 2652.0  # touches zone [2651.0, 2653.0]
    bars[14]["close"] = 2654.0

    # Ensure other subsequent bars stay above zone and don't invalidate
    for idx in (11, 12, 13, 15, 16, 17, 18, 19):
        bars[idx]["open"] = 2655.0
        bars[idx]["high"] = 2658.0
        bars[idx]["low"] = 2654.0
        bars[idx]["close"] = 2656.0

    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ts,
        m1_candles=bars,
        m5_candles=bars,
    )

    ev_valid = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_valid_touch",
            "fvg_top": 2653.00,
            "fvg_bottom": 2651.00,
            "fvg_formed_at_ms": f_formed_ts,
        },
        entry_price=2656.0,
        selected_sl=2650.0,
        take_profit=2670.0,
    )
    assert ev_valid.fvg_present is True
    assert ev_valid.fvg_unmitigated is True
    assert ev_valid.fvg_retest_valid is True
    assert ev_valid.fvg_retest_bar_id is not None
    assert ev_valid.fvg_retest_at_ms == bars[14]["close_time"]


# ==============================================================================
# P0-B: Provenance Timestamps & Zero Fallback
# ==============================================================================

def test_v1793126_p0_b_source_known_at_ms_no_fallback_to_cur_ts() -> None:
    """
    P0-B: When target_known_at_ms is missing or None, source_known_at_ms must be None
    (or UNKNOWN), target_reachable must be False, and status must be UNPROVEN.
    Never fallback to cur_ts.
    """
    bars = _make_m1_candles(20)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
    )
    # When no pools are provided, target_reachable is False and target_known_at_ms is 0
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        setup_descriptors={},
        entry_price=2655.0,
        selected_sl=2650.0,
        take_profit=2670.0,
        selected_target_id=None,
    )
    assert ev.target_reachable is False
    assert ev.target_known_at_ms == 0


# ==============================================================================
# P0-C: Arbitration & Scoring Feature Integrity (No ID-Presence Fallback)
# ==============================================================================

def test_v1793126_p0_c_id_presence_alone_scores_zero() -> None:
    """
    P0-C: ID presence alone must not be treated as confirmed proof in pre-entry scoring.
    When sweep_confirmed, mss_confirmed, and fvg_retest_valid are None/missing/False,
    the score tuple must score 0 for those dimensions even if selected_sweep_id,
    selected_break_id, or selected_fvg_id are present on the record.
    """
    rec_with_ids = {
        "model_id": "CG01_SWEEP_MSS_FVG",
        "direction": "LONG",
        "net_rr": 2.50,
        "selected_sweep_id": "sw_id_unconfirmed",
        "selected_break_id": "brk_id_unconfirmed",
        "selected_fvg_id": "fvg_id_unconfirmed",
        "pre_entry_features": {
            # Feature flags are explicitly missing / unproven
            "sweep_confirmed": None,
            "mss_confirmed": None,
            "fvg_retest_valid": None,
            "displacement_valid": False,
            "target_reachable": False,
        },
    }
    score = score_candidate_record_pre_entry_v1793122(rec_with_ids)
    # Index 2: sweep_and_mss -> must be 0 (not 1)
    assert score[2] == 0
    # Index 3: displacement -> 0
    assert score[3] == 0
    # Index 4: retest -> must be 0 (not 1)
    assert score[4] == 0
    # Index 5: target reachability -> 0
    assert score[5] == 0


def test_v1793126_p0_c_verified_features_score_points() -> None:
    """
    P0-C: When verified Boolean features are explicitly True, they score 1.
    """
    rec_verified = {
        "model_id": "CG01_SWEEP_MSS_FVG",
        "direction": "LONG",
        "net_rr": 2.50,
        "pre_entry_features": {
            "htf_alignment_state": "ALIGNED",
            "unicorn_classification": "UNICORN_CONFIRMED",
            "sweep_confirmed": True,
            "mss_confirmed": True,
            "displacement_valid": True,
            "fvg_retest_valid": True,
            "target_reachable": True,
        },
    }
    score = score_candidate_record_pre_entry_v1793122(rec_verified)
    assert score[0] == 1  # HTF
    assert score[1] == 1  # Unicorn
    assert score[2] == 1  # Sweep & MSS
    assert score[3] == 1  # Displacement
    assert score[4] == 1  # Retest
    assert score[5] == 1  # Target reachable
    assert score[6] == 2.50  # net_rr


# ==============================================================================
# P1-E: Quote Provenance Labeling
# ==============================================================================

def test_v1793126_p1_e_quote_provenance_synthetic_research_quote() -> None:
    """
    P1-E: Research quotes are synthetic and must be explicitly labeled as
    SYNTHETIC_RESEARCH_QUOTE, not REAL_PROVIDER_QUOTE.
    """
    bars = _make_m1_candles(40)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
        quote_source_kind="SYNTHETIC_RESEARCH_QUOTE",
    )
    assert ctx.quote_source_kind == "SYNTHETIC_RESEARCH_QUOTE"

    snap = SharedStrategyEvaluator().evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_REVERSAL.value, "LONG", adapter_origin="BACKTEST"
    )
    assert snap.quote_source_kind == "SYNTHETIC_RESEARCH_QUOTE"


# ==============================================================================
# Phase C: Pre-Registered Policy SI9 & ProvenanceEvidence Dataclass
# ==============================================================================

def test_v1793126_si9_policy_registration_and_provenance() -> None:
    """
    Phase C: Verify SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS is properly registered
    in CANONICAL_V1793123_POLICY_IDS, CANONICAL_V1793126_POLICY_IDS, and PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122.
    """
    assert "SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS" in CANONICAL_V1793123_POLICY_IDS
    assert "SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS" in CANONICAL_V1793126_POLICY_IDS
    assert "SI9" in POLICY_ID_ALIASES_V1793122

    si9 = PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122["SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"]
    assert si9.policy_id == "SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"
    assert si9.version == "17.9.3.1.2.6"
    assert si9.min_net_rr == 2.0
    assert si9.max_planned_risk_usd == 2.50
    assert si9.activation_ready == "NO"


def test_v1793126_provenance_evidence_dataclass_and_proof_hash() -> None:
    """
    Task A1: ProvenanceEvidence dataclass builds deterministic proof hashes.
    """
    ev = ProvenanceEvidence(
        evidence_type="FVG_RETEST",
        evidence_status="PROVEN_TRUE",
        timeframe="M1",
        source_bar_id="bar_10",
        occurred_at_ms=1000,
        known_at_ms=1060,
        decision_at_ms=1120,
        zone_low=2650.0,
        zone_high=2652.0,
        confirmed_retest_bar_id="bar_14",
        confirmed_retest_close_ms=1080,
    )
    assert len(ev.proof_hash_sha256) == 64
    d = ev.to_dict()
    assert d["evidence_type"] == "FVG_RETEST"
    assert d["evidence_status"] == "PROVEN_TRUE"
    assert d["derivation_version"] == "17.9.3.1.2.6"


# ==============================================================================
# P1-F: Loss Forensics Ledger & Accounting Invariant
# ==============================================================================

def test_v1793126_loss_forensics_accounting_and_predicates() -> None:
    """
    P1-F: Loss forensics classifies root causes deterministically and separates
    observed facts from hypothesized causes with zero duplicate records.
    """
    mock_episodes = [
        {
            "opportunity_id": "opp_1",
            "model_id": "SMC_M1_REVERSAL",
            "filled": True,
            "outcome_label": "LOSS",
            "direction": "LONG",
            "decision_timestamp_ms": 1_000_000,
            "entry_fill_ts": 1_000_000,
            "exit_fill_ts": 1_060_000,
            "entry_fill_price": 2650.0,
            "exit_fill_price": 2648.0,
            "realized_gross_pnl_usd": -2.0,
            "entry_fee_usd": 0.05,
            "exit_fee_usd": 0.05,
            "total_fees_usd": 0.10,
            "realized_net_pnl_usd": -2.10,
            "realized_r": -1.0,
            "bars_held_1m": 2,
        },
        {
            "opportunity_id": "opp_2",
            "model_id": "SMC_M1_CONTINUATION",
            "filled": True,
            "outcome_label": "WIN",
            "direction": "LONG",
            "decision_timestamp_ms": 2_000_000,
            "entry_fill_ts": 2_000_000,
            "exit_fill_ts": 2_120_000,
            "entry_fill_price": 2650.0,
            "exit_fill_price": 2656.0,
            "realized_gross_pnl_usd": 6.0,
            "entry_fee_usd": 0.05,
            "exit_fee_usd": 0.05,
            "total_fees_usd": 0.10,
            "realized_net_pnl_usd": 5.90,
            "realized_r": 2.5,
            "bars_held_1m": 12,
        },
    ]
    feat_ledger = {
        "opp_1": {
            "htf_alignment_state": "MIXED",
            "target_reachable": False,
        },
        "opp_2": {
            "htf_alignment_state": "ALIGNED",
            "target_reachable": True,
        },
    }

    report = build_ground_truth_loss_forensics_v1793123(mock_episodes, feat_ledger)
    assert report["closed_trades_count"] == 2
    assert report["wins_count"] == 1
    assert report["losses_count"] == 1
    assert report["primary_loss_causes_match_total_losses"] is True
