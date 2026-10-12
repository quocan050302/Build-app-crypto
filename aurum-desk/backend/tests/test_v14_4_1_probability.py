"""
V14.4.1 statistics: right labels, no invented probabilities, chronological purged validation.
(Synthetic fixtures prove the code is correct - they say nothing about the strategy being profitable.)
"""
import json
import os

import pytest

from lab.smc_outcome_labels import label_session_event, label_trade_outcome, net_winrate_from_closed
from lab.smc_probability_validation import (
    evaluate_probability_model, fit_probability_model, predict, split_chronological_purged,
)
from lab.smc_session_dataset import audit_dataset_coverage, verdict_for_real_data
from services.smc_opportunity_ranker import rank_confirmed_opportunities
from services.smc_probability_registry import (
    estimate_for_context, load_validated_model, schedule_offline_revalidation,
)
from services.smc_probability_service import (
    estimate_expected_net_r, expected_net_r, get_historical_support, probability_filter_decision, wilson_interval,
)
from services.smc_probability_types import ProbabilityEstimate

import smc_fixtures as F


class B:
    def __init__(self, ts, o, h, l, c):
        self.timestamp, self.open, self.high, self.low, self.close = ts, o, h, l, c


def test_small_sample_is_never_a_certified_hundred_percent():
    s = get_historical_support(3, 3)
    assert s.status == "INSUFFICIENT_SUPPORT" and s.observed_winrate == 1.0 and s.ci_low < 0.5
    assert get_historical_support(None, None).status == "UNAVAILABLE"
    big = get_historical_support(70, 100)
    assert big.status == "SUPPORTED" and 0.6 < big.ci_low < 0.7 < big.ci_high < 0.8


def test_wilson_validation():
    with pytest.raises(ValueError):
        wilson_interval(5, 3)
    with pytest.raises(ValueError):
        wilson_interval(1.0, 3)  # type: ignore[arg-type]
    w = wilson_interval(50, 100)
    assert w["low"] < 0.5 < w["high"] and "NOT_LIVE_WIN_PROBABILITY" in w["interpretation"]


def test_expected_net_r_does_not_subtract_costs_twice():
    assert expected_net_r(probability=0.5, win_net_r=2.0, loss_net_r=1.0) == pytest.approx(0.5)
    r = estimate_expected_net_r(probability=0.5, win_net_r=2.0, loss_net_r=1.0, p_low=0.4)
    assert r["ev_net_r"] == pytest.approx(0.5) and r["ev_conservative_net_r"] == pytest.approx(0.2)
    assert estimate_expected_net_r(probability=None, win_net_r=2.0, loss_net_r=1.0)["ev_net_r"] is None
    multi = estimate_expected_net_r(probability=0.5, win_net_r=2.0, loss_net_r=1.0, other_outcomes={"TIMEOUT": 0.2})
    assert multi["status"] == "UNAVAILABLE" and multi["ev_net_r"] is None
    with pytest.raises(ValueError):
        expected_net_r(probability=1.2, win_net_r=1, loss_net_r=1)


def test_event_vs_trade_labels_differ():
    # NY session ends UP (up level reached first) - yet a LONG entered at 2650 with SL 2645 hit its stop first.
    bars = [B(1, 2650, 2651, 2644, 2646), B(2, 2646, 2662, 2645, 2660)]
    assert label_session_event(up_level=2660.0, down_level=2630.0, bars=bars)["label"] == "TARGET_FIRST"
    t = label_trade_outcome(direction="LONG", entry=2650.0, stop_loss=2645.0, take_profit=2660.0,
                            bars_after_fill=bars, horizon_bars=2)
    assert t["label"] == "SL_FIRST" and t["closed_trade"] is True


def test_same_bar_touch_is_ambiguous_and_timeout_is_not_a_win():
    amb = label_trade_outcome(direction="SHORT", entry=2650.0, stop_loss=2655.0, take_profit=2640.0,
                              bars_after_fill=[B(1, 2650, 2656, 2639, 2650)], horizon_bars=3)
    assert amb["label"] == "AMBIGUOUS" and amb["closed_trade"] is False
    quiet = [B(i, 2650, 2651, 2649, 2650) for i in range(1, 4)]
    assert label_trade_outcome(direction="LONG", entry=2650.0, stop_loss=2640.0, take_profit=2670.0,
                               bars_after_fill=quiet, horizon_bars=3)["label"] == "TIMEOUT"
    assert label_trade_outcome(direction="LONG", entry=2650.0, stop_loss=2640.0, take_profit=2670.0,
                               bars_after_fill=quiet[:2], horizon_bars=3)["label"] == "CENSORED"
    assert label_trade_outcome(direction="LONG", entry=2650.0, stop_loss=2640.0, take_profit=2670.0,
                               bars_after_fill=quiet, horizon_bars=3, filled=False)["label"] == "NOT_FILLED"
    with pytest.raises(ValueError):
        label_trade_outcome(direction="LONG", entry=2650.0, stop_loss=2660.0, take_profit=2670.0,
                            bars_after_fill=quiet, horizon_bars=3)


def test_net_winrate_counts_only_closed_net_positive():
    trades = [{"closed_trade": True, "net_pnl": 1.0}, {"closed_trade": True, "net_pnl": -2.0},
              {"closed_trade": False, "net_pnl": None}, {"closed_trade": True, "net_pnl": 0.0}]
    r = net_winrate_from_closed(trades)
    assert (r["n"], r["wins"]) == (3, 1) and r["net_winrate"] == pytest.approx(1 / 3)
    assert net_winrate_from_closed([])["net_winrate"] is None


def _examples(n=100, span=1_000):
    return [{"id": i, "group": f"day{i // 5}", "decision_ms": i * span, "label_end_ms": i * span + 3 * span,
             "family": "STANDARD_SMC", "session": "NY", "regime": "TREND",
             "label": "TP_FIRST" if i % 3 == 0 else "SL_FIRST"} for i in range(n)]


def test_time_split_is_chronological_purged_and_grouped():
    ex = _examples()
    sp = split_chronological_purged(ex, embargo_ms=1_000)
    tr, ca, ho = sp["train"], sp["calibration"], sp["holdout"]
    assert tr and ca and ho and sp["purged"]
    assert max(e["decision_ms"] for e in tr) < min(e["decision_ms"] for e in ca) <= max(e["decision_ms"] for e in ca) < min(e["decision_ms"] for e in ho)
    assert max(e["label_end_ms"] for e in tr) < min(e["decision_ms"] for e in ca)        # no label window crosses the boundary
    assert max(e["label_end_ms"] for e in ca) < min(e["decision_ms"] for e in ho)
    groups = lambda part: {e["group"] for e in part}                                     # noqa: E731
    assert not (groups(tr) & groups(ca)) and not (groups(ca) & groups(ho)) and not (groups(tr) & groups(ho))
    assert split_chronological_purged([])["status"] == "UNAVAILABLE"


def test_model_needs_both_classes_and_support_and_evaluates_on_holdout_only():
    one_class = [dict(e, label="TP_FIRST") for e in _examples(60)]
    assert fit_probability_model(one_class)["status"] == "UNAVAILABLE"
    assert fit_probability_model(_examples(10))["status"] == "UNAVAILABLE"
    sp = split_chronological_purged(_examples(300), embargo_ms=1_000)
    model = fit_probability_model(sp["train"], min_cohort_n=10)
    assert model["status"] == "FITTED"
    assert predict(model, {"family": "OTHER", "session": "NY", "regime": "TREND"}) is None   # out of distribution -> null
    m = evaluate_probability_model(model, sp["holdout"])
    assert m["status"] == "EVALUATED" and m["brier"] is not None and m["reliability_bins"] and m["coverage"] == 1.0
    empty = evaluate_probability_model({"status": "UNAVAILABLE"}, sp["holdout"])
    assert empty["status"] == "NOT_VERIFIED" and empty["brier"] is None            # null, never 0


def test_missing_model_never_blocks_default_but_blocks_user_chosen_filter():
    assert probability_filter_decision("RULE_BASED_PAPER", None)["allowed"] is True
    assert probability_filter_decision(None, None)["allowed"] is True               # unknown config -> default mode
    d = probability_filter_decision("VALIDATED_PROBABILITY_FILTER", None)
    assert d["allowed"] is False and d["reason"] == "NO_VALIDATED_MODEL"            # explicit reason, no silent downgrade
    assert probability_filter_decision("VALIDATED_PROBABILITY_FILTER", "VALIDATED")["allowed"] is True


def test_default_estimate_is_unavailable_and_serialises_without_a_winrate():
    e = estimate_for_context({"cohort_key": "x"}, now_ms=F.NOW_MS)
    assert e.status == "UNAVAILABLE" and e.p is None
    d = e.to_dict()
    assert d["p_tp_before_sl"] is None and d["support_n"] is None and "Chưa" in d["note_vi"]


def test_registry_rejects_untrusted_expired_and_tampered(tmp_path):
    assert load_validated_model("../evil", now_ms=F.NOW_MS, registry_dir=str(tmp_path))["status"] == "UNAVAILABLE"
    assert load_validated_model("http://x/y", now_ms=F.NOW_MS, registry_dir=str(tmp_path))["status"] == "UNAVAILABLE"
    art = tmp_path / "m1.bin"; art.write_bytes(b"weights")
    import hashlib
    manifest = {"model_id": "m1", "schema_version": "1", "feature_schema": "fs1", "event": "TP_BEFORE_SL", "horizon": "72xM5",
                "training_cutoff_ms": F.NOW_MS - 10_000, "calibration_metrics": {"brier": 0.2}, "cost_version": "c1",
                "holdout_metrics": {"n": 120, "brier": 0.22, "reliability_bins": [{"n": 1}], "from_ms": 1, "to_ms": 2},
                "support_n": 500, "expires_at_ms": F.NOW_MS + 86_400_000, "artifact_file": "m1.bin",
                "artifact_sha256": hashlib.sha256(b"weights").hexdigest(),
                "cohorts": {"STANDARD_SMC|NY|TREND": {"p": 0.55, "n": 80, "ci_low": 0.44, "ci_high": 0.66}}}
    (tmp_path / "m1.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    ok = load_validated_model("m1", now_ms=F.NOW_MS, registry_dir=str(tmp_path))
    assert ok["status"] == "VALIDATED"
    est = estimate_for_context({"cohort_key": "STANDARD_SMC|NY|TREND"}, now_ms=F.NOW_MS, model_id="m1", registry_dir=str(tmp_path))
    assert est.status == "VALIDATED" and est.p == 0.55 and est.support_n == 80 and est.to_dict()["p_tp_before_sl"] == 0.55
    ood = estimate_for_context({"cohort_key": "UNSEEN"}, now_ms=F.NOW_MS, model_id="m1", registry_dir=str(tmp_path))
    assert ood.p is None and ood.status == "UNAVAILABLE"
    assert load_validated_model("m1", now_ms=F.NOW_MS + 2 * 86_400_000, registry_dir=str(tmp_path))["reason"] == "MODEL_EXPIRED"
    assert load_validated_model("m1", now_ms=F.NOW_MS - 20_000, registry_dir=str(tmp_path))["reason"] == "TRAINED_ON_THE_FUTURE"
    art.write_bytes(b"tampered")
    assert load_validated_model("m1", now_ms=F.NOW_MS, registry_dir=str(tmp_path))["reason"] == "ARTIFACT_HASH_MISMATCH"


def test_one_closed_trade_does_not_change_live_parameters():
    r = schedule_offline_revalidation({"order_id": "o1"})
    assert r["live_parameters_changed"] is False and r["queued_for_offline_batch"] is True


def test_ranker_gates_before_ranking_and_labels_structural_only():
    base = dict(chain_verified=True, binding_verified=True, has_target=True, plan_valid=True, net_rr=2.5, guard_blockers=[])
    out = rank_confirmed_opportunities([
        dict(base, id="A", structural_score=3, ev_conservative_net_r=None),
        dict(base, id="B", structural_score=5, ev_conservative_net_r=None),
        dict(base, id="C", structural_score=9, ev_conservative_net_r=0.9, guard_blockers=["RED_NEWS"]),   # high P never beats a block
        dict(base, id="D", structural_score=9, chain_verified=None),
    ])
    assert [r["id"] for r in out][:2] == ["B", "A"] and all(not r["eligible"] for r in out[2:])
    assert out[0]["rank_basis"] == "STRUCTURAL_ONLY_NO_STATISTICS" and "chưa có thống kê" in out[0]["label_vi"]
    with_ev = rank_confirmed_opportunities([dict(base, id="X", structural_score=1, ev_conservative_net_r=0.4),
                                            dict(base, id="Y", structural_score=9, ev_conservative_net_r=0.1)])
    assert with_ev[0]["id"] == "X" and with_ev[0]["rank_basis"] == "VALIDATED_EXPECTED_NET_UTILITY"


def test_dataset_audit_refuses_to_splice_or_claim_missing_real_data():
    m5 = F.build_m5("LONG")
    a = audit_dataset_coverage(m5, instrument="XAUUSDT", timeframe="5M", source="fixture")
    assert a["status"] == "COVERED" and a["gap_count"] == 0 and a["sha256"]
    spliced = audit_dataset_coverage(m5, instrument="XAUUSD", timeframe="5M", source="spot", expected_instrument="XAUUSDT")
    assert spliced["status"] == "NOT_VERIFIED" and spliced["reason"] == "INSTRUMENT_MISMATCH_NOT_SPLICED"
    holes = audit_dataset_coverage(m5[:50] + m5[80:], instrument="XAUUSDT", timeframe="5M", source="fixture")
    assert holes["status"] == "GAPS" and holes["gap_count"] == 1
    v = verdict_for_real_data([a])
    assert v["status"] == "NOT_VERIFIED" and v["winrate"] is None and v["expectancy"] is None   # one day is not 90
