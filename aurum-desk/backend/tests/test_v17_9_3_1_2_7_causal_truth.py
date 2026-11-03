"""Executable adversarial controls for source truth and independent matched ledgers."""
import copy
import gzip
import json
from types import SimpleNamespace

import pytest

from services.v17_9_3_1_2_7_causal_signal_truth import (
    CausalEvidenceRef, TRUE, UNKNOWN, canonical_hash,
    derive_pre_entry_features_from_causal_context_v1793127 as derive,
    required_proof_rejection_v1793127, verify_feature_snapshot_v1793127,
    classify_observed_loss_v1793127,
)
from services.v17_9_3_1_2_7_matched_ab import (
    compute_matched_policy_ab_v1793127 as compare,
    verify_matched_ab_against_raw_ledgers_v1793127 as verify_ab,
)


def fixture_context():
    bars=[dict(timestamp=1_700_000_000_000+i*60_000,open_time=1_700_000_000_000+i*60_000,
               close_time=1_700_000_000_000+(i+1)*60_000,open=2650+i*.1,high=2652+i*.1,
               low=2649+i*.1,close=2651+i*.1,is_closed=True) for i in range(20)]
    return SimpleNamespace(symbol="XAUUSDT",decision_timestamp_ms=bars[-1]["close_time"],
                           closed_m1_candles=bars,closed_m5_candles=(),closed_m15_candles=(),
                           closed_h1_candles=(),closed_h4_candles=(),bid=2652.9,ask=2653.1,
                           quote_source_kind="SYNTHETIC_RESEARCH_QUOTE")


def decision():
    return dict(opportunity_id="o1",setup_episode_id="e1",direction="LONG",model_id="SMC_M1_FVG_CONTINUATION",
                entry_reference=2653.,selected_sl=2645.,take_profit=2670.)


def test_missing_baseline_proof_remains_unknown_and_synthetic():
    row=derive(decision(),fixture_context(),dataset_sha256="a"*64)
    assert row["htf_alignment_state"]=="UNKNOWN"
    assert row["protected_structure_independent_of_sl"] is None
    assert row["target_pool_state"]=="UNKNOWN"
    assert row["quote_source_kind"]=="SYNTHETIC_RESEARCH_QUOTE"
    assert verify_feature_snapshot_v1793127(row)
    assert required_proof_rejection_v1793127(row,decision()["model_id"]).startswith("REJECTED_UNPROVEN")


@pytest.mark.parametrize("field",["fvg_retest_valid","sweep_confirmed","mss_confirmed","target_unswept",
                                  "protected_structure_independent_of_sl","unicorn_breaker_fvg_bound"])
def test_injected_optimistic_feature_without_proof_rejected(field):
    assert required_proof_rejection_v1793127({field:True},decision()["model_id"])


@pytest.mark.parametrize("change",["future","partial","source_edit","quote_kind"])
def test_point_in_time_and_source_hash(change):
    ctx=fixture_context()
    first=derive(decision(),ctx,dataset_sha256="a"*64)
    ctx=copy.deepcopy(ctx)
    if change=="future":
        ctx.closed_m1_candles[-1]["close_time"]+=60_000
    elif change=="partial":
        ctx.closed_m1_candles[-1]["is_closed"]=False
    elif change=="source_edit":
        ctx.closed_m1_candles[5]["high"]+=3
    else:
        ctx.quote_source_kind="REAL_PROVIDER_QUOTE"
    changed=derive(decision(),ctx,dataset_sha256="a"*64)
    if change!="quote_kind":
        assert first["source_snapshot_hash"]!=changed["source_snapshot_hash"]
    assert first["proof_hash"]!=changed["proof_hash"]


@pytest.mark.parametrize("field",["sweep_reclaimed","fvg_retest_valid","mss_confirmed","target_unswept"])
def test_edit_flat_flag_invalidates_proof(field):
    row=derive(decision(),fixture_context(),dataset_sha256="a"*64)
    row[field]=not row.get(field)
    assert not verify_feature_snapshot_v1793127(row)


def test_missing_known_at_cannot_be_proven():
    with pytest.raises(ValueError,match="MISSING_CAUSAL_TIMESTAMP"):
        CausalEvidenceRef("e","FVG","XAUUSDT","1M",("b",),1,2,1,2,None,"d","h","UNKNOWN",True,TRUE,"test")


def test_missing_decision_timestamp_fails_closed():
    context=fixture_context()
    context.decision_timestamp_ms=0
    with pytest.raises(ValueError,match="MISSING_DECISION_TIMESTAMP"):
        derive(decision(),context,dataset_sha256="a"*64)


def test_provider_label_without_source_receipt_not_promoted_to_real():
    context=fixture_context()
    context.quote_source_kind="REAL_PROVIDER_QUOTE"
    row=derive(decision(),context,dataset_sha256="a"*64)
    assert row["quote_source_kind"]=="UNVERIFIED_PROVIDER_QUOTE"


def test_canonical_uppercase_hourly_frames_are_not_silently_lost():
    from services.v17_9_3_1_2_7_causal_signal_truth import build_verified_baseline_pre_entry_feature_ledger_v1793127
    ctx=fixture_context()
    cutoff=ctx.decision_timestamp_ms
    hourly=[]
    for i in range(20):
        bar=dict(ctx.closed_m1_candles[i])
        bar.update(timestamp=cutoff-(20-i)*3600000,open_time=cutoff-(20-i)*3600000,
                   close_time=cutoff-(19-i)*3600000)
        hourly.append(bar)
    rec={**decision(),"decision_timestamp_ms":cutoff}
    rows=build_verified_baseline_pre_entry_feature_ledger_v1793127(
        {"decisions_by_checkpoint_index":{19:[rec]}},
        {"1m":ctx.closed_m1_candles,"1H":hourly},dataset_sha256="a"*64)
    refs={r["evidence_type"]:r for r in rows["o1"]["causal_snapshot"]["features"]}
    assert refs["h1_bias"]["source_bar_count"]==20
    assert refs["h4_bias"]["source_bar_count"]==0


@pytest.mark.parametrize("field,value",[("selected_target_id","fabricated"),("selected_sl",2700.),("selected_anchor_id","invented")])
def test_unbound_target_and_wrong_side_stop_stay_unproven(field,value):
    row=derive({**decision(),field:value},fixture_context(),dataset_sha256="a"*64)
    assert row["protected_structure_independent_of_sl"] is None
    assert row["target_unswept"] is None
    assert required_proof_rejection_v1793127(row,decision()["model_id"])


def test_outcomes_cannot_change_pre_entry_proof():
    before=derive(decision(),fixture_context(),dataset_sha256="a"*64)
    after=derive({**decision(),"outcome_label":"WIN","realized_net_pnl_usd":999,
                  "future_mfe":50,"exit_fill_ts":9999999999999},fixture_context(),dataset_sha256="a"*64)
    assert before["proof_hash"]==after["proof_hash"]


def test_gap_is_observed_without_invented_trading_cause():
    label,tags=classify_observed_loss_v1793127({"outcome_label":"DATA_GAP_UNRESOLVED"},{})
    assert label=="FILL_GAP/AMBIGUOUS_INTRABAR"


def test_missing_gate_never_passes():
    from tools.run_v17_9_3_1_2_7_verification import evaluate_v1793127_acceptance_gates
    report=evaluate_v1793127_acceptance_gates({},tested_sha="a"*40,dataset_sha="b"*64)
    assert report["engineering_verdict"]=="BLOCKED"
    assert all(g["status"]=="BLOCKED" for g in report["gates"])


@pytest.mark.parametrize("ep",[{"planned_net_rr":99,"hold_bars":1}, {"net_rr":8,"bars_held_1m":1}])
def test_rr_and_quick_loss_do_not_invent_target_or_premature_cause(ep):
    label,tags=classify_observed_loss_v1793127({**ep,"outcome_label":"LOSS"},{})
    assert label=="OTHER/UNKNOWN"


def raw_rows():
    d=dict(opportunity_id="o",decision_timestamp_ms=1000,source_snapshot_hash="s"*64,accepted=True,status="ELIGIBLE")
    t={**d,"outcome_label":"LOSS","realized_gross_pnl_usd":-2.,"entry_fee_usd":.1,
       "exit_fee_usd":.1,"realized_net_pnl_usd":-2.2,"realized_r":-.88}
    return [d],[copy.deepcopy(d)],[t],[copy.deepcopy(t)]


def test_si8_si9_identity_reported_honestly():
    report=compare(*raw_rows(),comparison_id="SI8_SI9",fill_mode="NEXT_BAR_OPEN_BID_ASK")
    assert report["relation"]=="IDENTICAL_ON_TESTED_DATA"
    assert report["incremental_edge_status"]=="INCREMENTAL_EDGE_NOT_DEMONSTRATED"
    assert report["paired_mean_net_r_delta"]==0


@pytest.mark.parametrize("bad",["duplicate","missing_source","changed_fee","wrong_timestamp"])
def test_matched_identity_and_accounting_negative_controls(bad):
    rows=list(raw_rows())
    if bad=="duplicate": rows[0].append(copy.deepcopy(rows[0][0]))
    if bad=="missing_source": rows[0][0].pop("source_snapshot_hash")
    if bad=="changed_fee": rows[2][0]["entry_fee_usd"]=9.
    if bad=="wrong_timestamp": rows[0][0]["decision_timestamp_ms"]=None
    with pytest.raises(ValueError):
        compare(*rows,comparison_id="x",fill_mode="x")


def test_open_and_incremental_candidates_are_not_paired_closed():
    da,db,ta,tb=raw_rows()
    tb[0]["outcome_label"]="OPEN_AT_HORIZON"
    db.append({**db[0],"opportunity_id":"only_b"})
    report=compare(da,db,ta,tb,comparison_id="x",fill_mode="x")
    assert report["matched_closed_count"]==0
    assert report["paired_mean_net_r_delta"] is None
    assert report["decision_diff"]["changed_count"]==1


def test_same_opportunity_wrong_timestamp_must_not_be_unmatched_discovery():
    da,db,ta,tb=raw_rows()
    db[0]["decision_timestamp_ms"]+=1
    with pytest.raises(ValueError,match="PAIR_SOURCE_OR_TIMESTAMP_MISMATCH"):
        compare(da,db,ta,tb,comparison_id="x",fill_mode="x")


def test_disk_verifier_rejects_deleted_pair_and_tampered_raw_pnl(tmp_path):
    rows=raw_rows()
    spec={}
    for key,records in zip(("decisions_a","decisions_b","trades_a","trades_b"),rows):
        spec[key]=key+".jsonl.gz"
        with gzip.open(tmp_path/spec[key],"wt",encoding="utf-8") as f:
            for r in records: f.write(json.dumps(r)+"\n")
    spec["claim"]="claim.json"
    claim=compare(*rows,comparison_id="x",fill_mode="x")
    (tmp_path/spec["claim"]).write_text(json.dumps(claim),encoding="utf-8")
    assert verify_ab(tmp_path,spec)["status"]=="PASS"
    claim["pair_ledger"].clear()
    (tmp_path/spec["claim"]).write_text(json.dumps(claim),encoding="utf-8")
    assert verify_ab(tmp_path,spec)["status"]=="FAIL"
