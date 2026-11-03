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


def test_future_provider_quote_is_not_real_point_in_time_evidence():
    context=fixture_context()
    context.quote_source_kind="REAL_PROVIDER_QUOTE"
    context.quote_provider_source_id="provider-receipt"
    context.quote_timestamp_ms=context.decision_timestamp_ms+1
    assert derive(decision(),context,dataset_sha256="a"*64)["quote_source_kind"]=="UNVERIFIED_PROVIDER_QUOTE"


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


@pytest.mark.parametrize("field,value",[("entry_reference",2654.),("selected_sl",2644.),("take_profit",2671.),("hypothetical_quantity",.03)])
def test_geometry_changes_invalidate_proof_even_when_signal_flags_are_unknown(field,value):
    before=derive(decision(),fixture_context(),dataset_sha256="a"*64)
    after=derive({**decision(),field:value},fixture_context(),dataset_sha256="a"*64)
    assert before["proof_hash"]!=after["proof_hash"]


def test_forensic_legacy_identity_resolves_to_exact_canonical_source_proof():
    from services.v17_9_3_1_2_1_root_cause_evidence_service import build_canonical_opportunity_id_v1793121
    from services.v17_9_3_1_2_7_causal_signal_truth import build_forensic_causal_evidence_join_v1793127
    ctx=fixture_context()
    ep={**decision(),"opportunity_id":"opp::legacy","checkpoint_index_1m":19,"decision_timestamp_ms":ctx.decision_timestamp_ms}
    oid=build_canonical_opportunity_id_v1793121(ep,dataset_sha256="a"*64)
    feature=derive({**ep,"opportunity_id":oid},ctx,dataset_sha256="a"*64)
    rows=build_forensic_causal_evidence_join_v1793127([ep],{oid:feature})
    assert rows[0]["source_proof_valid"] is True
    assert rows[0]["canonical_opportunity_id"]==oid
    assert rows[0]["observed_execution"]["opportunity_id"]=="opp::legacy"
    with pytest.raises(ValueError,match="DUPLICATE_CANONICAL_FORENSIC_OPPORTUNITY"):
        build_forensic_causal_evidence_join_v1793127([ep,{**ep,"opportunity_id":oid}],{oid:feature})


def test_final_intent_rebuild_uses_final_geometry_and_rejects_nonclosed_cutoff():
    from services.v17_9_3_1_2_7_causal_signal_truth import rebuild_materialized_intent_proof_v1793127
    ctx=fixture_context()
    opp={**decision(),"checkpoint_index_1m":19,"decision_timestamp_ms":ctx.decision_timestamp_ms}
    plan={"new_decision_timestamp_ms":ctx.decision_timestamp_ms,"new_checkpoint_index_1m":19,
          "new_entry_reference":2654.,"new_selected_sl":2644.,"new_take_profit":2672.,"new_quantity":.02}
    record,proof,rejection=rebuild_materialized_intent_proof_v1793127(opp,plan,{"1m":ctx.closed_m1_candles},dataset_sha256="a"*64)
    assert proof["decision_geometry"]["selected_sl"]==2644.
    assert record["hypothetical_quantity"]==.02
    assert rejection.startswith("REJECTED_UNPROVEN")
    with pytest.raises(ValueError,match="FINAL_INTENT_NOT_AT_CLOSED_SOURCE_BAR"):
        rebuild_materialized_intent_proof_v1793127(opp,{**plan,"new_decision_timestamp_ms":ctx.decision_timestamp_ms+1},{"1m":ctx.closed_m1_candles},dataset_sha256="a"*64)


def test_gap_is_observed_without_invented_trading_cause():
    label,tags=classify_observed_loss_v1793127({"outcome_label":"DATA_GAP_UNRESOLVED"},{})
    assert label=="FILL_GAP/AMBIGUOUS_INTRABAR"


def test_legacy_casebook_missing_features_never_invents_displacement_or_counterfactual():
    from services.v17_9_3_1_2_2_signal_intelligence import build_smc_m1_continuation_24_losses_casebook_v1793122
    result=build_smc_m1_continuation_24_losses_casebook_v1793122(
        baseline_trades=[{**decision(),"model_id":"SMC_M1_CONTINUATION","exit_reason":"SL",
                          "realized_net_usd":-2.2,"realized_gross_usd":-2.0}],feature_ledger_by_opp_id={})
    row=result["cases"][0]
    assert row["primary_failure_category"]=="OTHER/UNKNOWN"
    assert row["counterfactual_assessment_status"]=="NOT_ESTABLISHED"
    assert all(v=="UNKNOWN_NO_COUNTERFACTUAL_REPLAY" for v in row["policy_counterfactual_outcomes"].values())


def test_quote_timestamp_changes_market_input_identity():
    from services.shared_strategy_evaluator import build_market_decision_context
    ctx=fixture_context()
    kwargs=dict(symbol=ctx.symbol,decision_timestamp_ms=ctx.decision_timestamp_ms,
                m1_candles=ctx.closed_m1_candles,bid=ctx.bid,ask=ctx.ask)
    a=build_market_decision_context(**kwargs,quote_timestamp_ms=ctx.decision_timestamp_ms-100)
    b=build_market_decision_context(**kwargs,quote_timestamp_ms=ctx.decision_timestamp_ms-50)
    assert a.compute_input_snapshot_hash()!=b.compute_input_snapshot_hash()


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
       "exit_fee_usd":.1,"realized_net_pnl_usd":-2.2,"realized_r":-.88,"planned_net_risk_usd":2.5,"filled":True}
    return [d],[copy.deepcopy(d)],[t],[copy.deepcopy(t)]


def test_si8_si9_identity_reported_honestly():
    report=compare(*raw_rows(),comparison_id="SI8_SI9",fill_mode="NEXT_BAR_OPEN_BID_ASK")
    assert report["relation"]=="IDENTICAL_ON_TESTED_DATA"
    assert report["incremental_edge_status"]=="INCREMENTAL_EDGE_NOT_DEMONSTRATED"
    assert report["paired_mean_net_r_delta"]==0


def test_equal_pnl_does_not_hide_different_actual_fill_paths():
    da,db,ta,tb=raw_rows()
    ta[0]["entry_fill_ts"]=2000
    tb[0]["entry_fill_ts"]=3000
    report=compare(da,db,ta,tb,comparison_id="different_fills",fill_mode="x")
    assert report["portfolio_net_usd_delta"]==0
    assert report["relation"]=="DIFFERENT_ON_TESTED_DATA"


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


@pytest.mark.parametrize("source",[None,""])
def test_raw_index_rejects_missing_source_before_matching(source):
    from services.v17_9_3_1_2_7_matched_ab import _index
    with pytest.raises(ValueError,match="MISSING_MATCH_IDENTITY"):
        _index([{"opportunity_id":"o","decision_timestamp_ms":1000,"source_snapshot_hash":source}])


@pytest.mark.parametrize("partial",[False,True])
def test_real_fvg_retest_requires_closed_post_formation_touch(partial):
    ctx=fixture_context()
    bars=ctx.closed_m1_candles
    for b in bars[:17]:b.update(open=2650.,high=2652.,low=2649.,close=2651.)
    bars[17].update(open=2652.,high=2664.,low=2652.,close=2663.)
    bars[18].update(open=2664.,high=2666.,low=2663.,close=2665.)
    bars[19].update(open=2665.,high=2666.,low=2660.,close=2665.,is_closed=not partial)
    row=derive(decision(),ctx,dataset_sha256="a"*64)
    assert row["fvg_present"] is True
    assert row["fvg_retest_valid"] is (not partial)
    assert verify_feature_snapshot_v1793127(row)


@pytest.mark.parametrize("model",["SMC_M1_CONTINUATION","SMC_M1_REVERSAL","SMC_M1_FVG_CONTINUATION","SMC_M1_SWEEP_RECLAIM","CG01_SWEEP_MSS_FVG"])
@pytest.mark.parametrize("direction",["LONG","SHORT"])
def test_unknown_source_rejects_all_five_models_both_directions(model,direction):
    row=derive({**decision(),"model_id":model,"direction":direction},fixture_context(),dataset_sha256="a"*64)
    assert required_proof_rejection_v1793127(row,model).startswith("REJECTED_UNPROVEN")


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


@pytest.mark.parametrize("tamper",["delete_pair","raw_pnl","duplicate_episode","trade_source"])
def test_compressed_pair_publication_is_bound_to_separate_raw_ledgers(tmp_path,tamper):
    rows=raw_rows()
    spec={}
    def emit(name,records):
        with gzip.open(tmp_path/name,"wt",encoding="utf-8") as f:
            for record in records:f.write(json.dumps(record)+"\n")
    for key,records in zip(("decisions_a","decisions_b","trades_a","trades_b"),rows):
        spec[key]=key+".jsonl.gz"
        emit(spec[key],records)
    claim=compare(*rows,comparison_id="compressed",fill_mode="NEXT_BAR_OPEN_BID_ASK")
    spec.update(claim="claim.json",pair_rows="pairs.jsonl.gz",decision_diff_rows="diff.jsonl.gz")
    emit(spec["pair_rows"],claim.pop("pair_ledger"))
    emit(spec["decision_diff_rows"],claim["decision_diff"].pop("rows"))
    (tmp_path/spec["claim"]).write_text(json.dumps(claim),encoding="utf-8")
    assert verify_ab(tmp_path,spec)["status"]=="PASS"
    if tamper=="delete_pair":emit(spec["pair_rows"],[])
    else:
        if tamper=="raw_pnl":
            rows[3][0]["realized_gross_pnl_usd"]+=1
            rows[3][0]["realized_net_pnl_usd"]+=1
        elif tamper=="duplicate_episode":rows[3].append(copy.deepcopy(rows[3][0]))
        else:rows[3][0]["source_snapshot_hash"]="wrong"*13
        emit(spec["trades_b"],rows[3])
    assert verify_ab(tmp_path,spec)["status"]=="FAIL"


@pytest.mark.parametrize("tamper",["reorder","duplicate","missing","future","wrong_cutoff","observe_changed","unknown_model"])
def test_checkpoint_coverage_and_off_observe_negative_controls(tmp_path,tamper):
    from tools.v17_9_3_1_2_7_proof_checks import verify_checkpoint_coverage_from_disk
    from services.v17_9_3_1_2_3_core_trading_logic_repair import CANONICAL_5_MODELS_V1793123
    datasets={"1m":[{"close_time":(i+1)*60000} for i in range(31)]}
    rows=[{"checkpoint_index_1m":30,"model_id":m,"direction":d,"decision_timestamp_ms":1860000,
           "max_closed_bar_ms":1860000,"source_snapshot_hash":"a"*64,
           "off_action_hash":"b"*64,"observe_action_hash":"b"*64}
          for m in CANONICAL_5_MODELS_V1793123 for d in ("LONG","SHORT")]
    path=tmp_path/"checkpoint_audit.jsonl.gz"
    def emit():
        with gzip.open(path,"wt",encoding="utf-8") as f:
            for row in rows:f.write(json.dumps(row)+"\n")
    emit()
    before=verify_checkpoint_coverage_from_disk(path,datasets)
    assert before["status"]=="PASS"
    if tamper=="reorder":rows.reverse()
    elif tamper=="duplicate":rows.append(copy.deepcopy(rows[0]))
    elif tamper=="missing":rows.pop()
    elif tamper=="future":rows[0]["max_closed_bar_ms"]+=60000
    elif tamper=="wrong_cutoff":rows[0]["decision_timestamp_ms"]-=60000
    elif tamper=="observe_changed":rows[0]["observe_action_hash"]="c"*64
    else:rows[0]["model_id"]="FABRICATED_MODEL"
    emit()
    after=verify_checkpoint_coverage_from_disk(path,datasets)
    if tamper=="reorder":
        assert after==before
    else:assert after["status"]=="FAIL" or after["off_observe_action_mismatches"]>0


def test_actual_sampled_discovery_records_all_models_and_off_observe_actions():
    from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets
    from services.v17_9_3_1_2_3_core_trading_logic_repair import discover_research_candidates_v1793123
    datasets,_=load_canonical_xauusdt_datasets()
    report=discover_research_candidates_v1793123(datasets,test_only_checkpoint_override=[30,200,500],max_workers=1)
    rows=report["checkpoint_audit_rows"]
    assert len(rows)==30
    assert len({(r["checkpoint_index_1m"],r["model_id"],r["direction"]) for r in rows})==30
    assert all(r["off_action_hash"]==r["observe_action_hash"] for r in rows)
    assert all(r["max_closed_bar_ms"]<=r["decision_timestamp_ms"] for r in rows)


def test_compact_source_cache_roundtrip_preserves_proofs_and_cannot_be_poisoned():
    import pickle
    from services.v17_9_3_1_2_7_causal_signal_truth import build_verified_baseline_pre_entry_feature_ledger_v1793127
    ctx=fixture_context()
    rec={**decision(),"checkpoint_index_1m":19,"decision_timestamp_ms":ctx.decision_timestamp_ms}
    index={"decisions_by_checkpoint_index":{19:[rec]}}
    data={"1m":ctx.closed_m1_candles}
    plain=build_verified_baseline_pre_entry_feature_ledger_v1793127(index,data,dataset_sha256="a"*64)
    compact=build_verified_baseline_pre_entry_feature_ledger_v1793127(index,data,dataset_sha256="a"*64,compact=True)
    assert canonical_hash(compact["o1"])==canonical_hash(plain["o1"])
    read=compact["o1"]
    read["sweep_confirmed"]=True
    assert compact["o1"]["sweep_confirmed"]==plain["o1"]["sweep_confirmed"]
    restored=pickle.loads(pickle.dumps(compact))
    assert verify_feature_snapshot_v1793127(restored["o1"])
    assert restored.dataset_hashes=={"a"*64}
    assert restored.encoded_bytes>0


def test_memory_bounded_shards_preserve_every_file_and_isolate_db_interpreter_group(monkeypatch):
    from collections import Counter
    from pathlib import Path
    from tools.run_v17_9_3_1_2_2_verification import _build_resource_bounded_shards_v1793122
    app=Path(__file__).resolve().parents[2]
    files=sorted(str(p.relative_to(app)).replace("\\","/") for p in (app/"backend/tests").glob("test_*.py"))
    monkeypatch.setenv("AURUM_RESEARCH_MAX_FILES_PER_SHARD","0")
    original=_build_resource_bounded_shards_v1793122(files)
    monkeypatch.setenv("AURUM_RESEARCH_MAX_FILES_PER_SHARD","4")
    bounded=_build_resource_bounded_shards_v1793122(files)
    assert Counter(f for _,group in original for f in group)==Counter(f for _,group in bounded for f in group)
    assert all(len(group)<=4 for _,group in bounded)
    assert all(len(group)==1 for _,group in bounded if any(Path(f).name=="test_v17_7_database_safety.py" for f in group))
