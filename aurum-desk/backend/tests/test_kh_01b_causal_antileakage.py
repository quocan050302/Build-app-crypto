"""
backend/tests/test_kh_01b_causal_antileakage.py

Causal anti-lookahead, future-bar perturbation invariance, NY DST/holiday shifts,
missing-vs-false monotonicity, and §09 25-case negative-control verification for KH-01B.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from services.kh_01b_entry_unicorn_research_service import (
    _build_synthetic_strict_unicorn_fixture,
    _evaluate_single_candidate_for_variant,
    audit_kh01b_true_unicorn_positive_and_negative_fixtures,
    evaluate_htf_alignment_hypothesis,
    evaluate_macro_window_hypothesis,
    evaluate_sweep_mss_displacement_hypothesis,
    extract_kh01b_causal_feature_frame,
)
from services.v17_9_3_macro_unicorn_intelligence import resolve_causal_macro_time_window


def test_kh01b_future_bar_perturbation_leaves_all_pre_entry_decisions_invariant() -> None:
    ts_ms, m1_bars, htf_map = _build_synthetic_strict_unicorn_fixture("LONG")
    datasets_base = {"1m": list(m1_bars), **htf_map}
    close_base = {k: [b["close_time"] for b in v] for k, v in datasets_base.items()}

    # Append extreme future crash and moon bars strictly AFTER `ts_ms`
    future_bars = [
        {
            "timestamp": ts_ms + 60_000 * k,
            "open_time": ts_ms + 60_000 * k,
            "close_time": ts_ms + 60_000 * (k + 1),
            "open": 3007.5,
            "high": 3500.0 if k % 2 == 0 else 3008.0,
            "low": 2500.0 if k % 2 == 1 else 3006.0,
            "close": 2550.0,
            "volume": 9999.0,
            "is_closed": True,
        }
        for k in range(15)
    ]
    datasets_perturbed = {"1m": list(m1_bars) + future_bars, **htf_map}
    close_perturbed = {k: [b["close_time"] for b in v] for k, v in datasets_perturbed.items()}

    cand = {
        "opportunity_id": "opp_pert_1",
        "setup_episode_id": "ep_pert_1",
        "model_id": "SMC_M1_FVG_CONTINUATION",
        "direction": "LONG",
        "decision_timestamp_ms": ts_ms,
        "checkpoint_index_1m": 30,
        "entry_reference": 3007.50,
        "selected_sl": 3003.50,
        "take_profit": 3017.50,
        "selected_target_id": "pool_15m_1",
        "hypothetical_quantity": 0.50,
        "planned_net_risk_usd": 2.20,
        "net_rr": 2.25,
        "trade_action_fingerprint": "fp_fixed_1",
        "decision_fingerprint": "dfp_fixed_1",
    }
    feat = {
        "opp_pert_1": {
            "opportunity_id": "opp_pert_1",
            "decision_timestamp_ms": ts_ms,
            "h4_bias": "BULLISH",
            "h1_bias": "BEARISH",
            "sweep_confirmed": None,
            "mss_confirmed": True,
            "displacement_valid": True,
            "target_pool_id": "pool_15m_1",
            "target_unswept": True,
        }
    }
    pools = {"pool_15m_1": {"pool_id": "pool_15m_1", "timeframe": "15M", "price": 3017.50, "known_at_ms": ts_ms - 900_000}}

    fr_1 = extract_kh01b_causal_feature_frame(cand, feat, {}, ts_ms, pools_by_id=pools, datasets=datasets_base, close_ts_index=close_base)
    fr_2 = extract_kh01b_causal_feature_frame(cand, feat, {}, ts_ms, pools_by_id=pools, datasets=datasets_perturbed, close_ts_index=close_perturbed)

    assert fr_1.frame_sha256 == fr_2.frame_sha256
    for sc in [f"B{i:02d}" for i in range(13)]:
        d1 = _evaluate_single_candidate_for_variant(sc, f"KH01B-{sc}", fr_1)
        d2 = _evaluate_single_candidate_for_variant(sc, f"KH01B-{sc}", fr_2)
        assert d1[:5] == d2[:5], f"Future bar perturbation changed pre-entry decision for {sc}!"


def test_kh01b_missing_vs_false_monotonicity_and_unsafe_string_bool_rejection() -> None:
    ny_tz = ZoneInfo("America/New_York")
    ts_ms = int(datetime(2026, 10, 14, 9, 55, 0, tzinfo=ny_tz).astimezone(timezone.utc).timestamp() * 1000)
    base_cand = {
        "opportunity_id": "opp_mono",
        "direction": "LONG",
        "model_id": "SMC_M1_CONTINUATION",
        "decision_timestamp_ms": ts_ms,
        "entry_reference": 3000.0,
        "selected_sl": 2996.0,
        "take_profit": 3010.0,
        "selected_sweep_id": "sw_present_without_proof",
        "selected_break_id": "brk_present_without_proof",
    }

    # String "false" or "true" in boolean feature MUST become INVALID, never Python bool("false") == True!
    fr_str_bool = extract_kh01b_causal_feature_frame(
        base_cand,
        {"opp_mono": {"opportunity_id": "opp_mono", "decision_timestamp_ms": ts_ms, "sweep_confirmed": "false"}},
        {},
        ts_ms,
    )
    assert fr_str_bool.has_any_invalid_evidence is True
    assert fr_str_bool.feature_cells["sweep_confirmed"]["tri_state"] == "INVALID"

    # Monotonicity: dropping proven evidence to None changes PROVEN_TRUE -> UNKNOWN_MISSING (never stays eligible and never becomes PROVEN_FALSE)
    fr_proven = extract_kh01b_causal_feature_frame(
        base_cand,
        {"opp_mono": {"opportunity_id": "opp_mono", "decision_timestamp_ms": ts_ms, "sweep_confirmed": True, "sweep_reclaimed": True}},
        {},
        ts_ms,
    )
    fr_dropped = extract_kh01b_causal_feature_frame(
        base_cand,
        {"opp_mono": {"opportunity_id": "opp_mono", "decision_timestamp_ms": ts_ms, "sweep_confirmed": None, "sweep_reclaimed": True}},
        {},
        ts_ms,
    )
    ev_p = evaluate_sweep_mss_displacement_hypothesis(fr_proven, mode="SWEEP_RECLAIM_ONLY")
    ev_d = evaluate_sweep_mss_displacement_hypothesis(fr_dropped, mode="SWEEP_RECLAIM_ONLY")
    assert ev_p.eligible is True and ev_p.tri_state == "PROVEN_TRUE"
    assert ev_d.eligible is False and ev_d.tri_state == "UNKNOWN_MISSING"
    assert ev_d.missing_evidence_rejected is True and ev_d.proven_false_rejected is False

    # HTF neutral ("NEUTRAL") vs unknown ("UNKNOWN")
    fr_neut = extract_kh01b_causal_feature_frame(
        base_cand,
        {"opp_mono": {"opportunity_id": "opp_mono", "decision_timestamp_ms": ts_ms, "h4_bias": "NEUTRAL"}},
        {},
        ts_ms,
    )
    h_neut = evaluate_htf_alignment_hypothesis(fr_neut, "LONG", "H4_ONLY")
    assert h_neut.eligible is False and h_neut.tri_state == "PROVEN_FALSE"
    assert "NEUTRAL" in str(h_neut.rejection_code)


def test_kh01b_dst_holiday_and_macro_keyword_contracts() -> None:
    # Same UTC hour 13:55 UTC in January (EST = 08:55 NY -> INSIDE 08:50-09:10) vs July (EDT = 09:55 NY -> INSIDE 09:50-10:10)
    jan_utc = int(datetime(2026, 1, 14, 13, 55, 0, tzinfo=timezone.utc).timestamp() * 1000)
    jul_utc = int(datetime(2026, 7, 15, 13, 55, 0, tzinfo=timezone.utc).timestamp() * 1000)
    st_jan = resolve_causal_macro_time_window(decision_timestamp_ms=jan_utc, symbol="XAUUSDT")
    st_jul = resolve_causal_macro_time_window(decision_timestamp_ms=jul_utc, symbol="XAUUSDT")
    assert st_jan.primary_window_id == "NY_AM_MACRO_0850_0910" and st_jan.utc_offset_minutes == -300
    assert st_jul.primary_window_id == "NY_AM_MACRO_0950_1010" and st_jul.utc_offset_minutes == -240

    # Holiday configuration shift: verified holiday closes window; unverified required calendar -> INVALID
    cand = {"opportunity_id": "opp_hol", "direction": "LONG", "model_id": "SMC_M1_CONTINUATION", "decision_timestamp_ms": jan_utc, "entry_reference": 3000.0, "selected_sl": 2996.0, "take_profit": 3010.0}
    fr = extract_kh01b_causal_feature_frame(cand, {}, {}, jan_utc)

    hol_cfg = {
        "schedule_version": "HOL_TEST_V1",
        "timezone": "America/New_York",
        "weekdays": [0, 1, 2, 3, 4],
        "holiday_calendar_verified": True,
        "verified_holiday_dates": ["2026-01-14"],
    }
    d_hol = evaluate_macro_window_hypothesis(fr, schedule_config=hol_cfg)
    assert d_hol.eligible is False and d_hol.tri_state == "PROVEN_FALSE"

    unver_req_cfg = {
        "schedule_version": "UNVER_REQ_V1",
        "timezone": "America/New_York",
        "weekdays": [0, 1, 2, 3, 4],
        "require_verified_holiday_calendar": True,
        "holiday_calendar_verified": False,
    }
    d_unver = evaluate_macro_window_hypothesis(fr, schedule_config=unver_req_cfg)
    assert d_unver.eligible is False and d_unver.tri_state == "INVALID"


def test_kh01b_twenty_five_mandatory_cases_all_pass() -> None:
    res = audit_kh01b_true_unicorn_positive_and_negative_fixtures()
    assert res.status == "PASS"
    assert [c["case_id"] for c in res.cases] == [f"CASE-{i:02d}" for i in range(1, 26)]
    for c in res.cases:
        assert c["status"] == "PASS", f"Mandatory negative/positive case failed: {c}"
