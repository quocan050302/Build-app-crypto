"""Disk-side checks and measured statistics, separate from producer gate flags."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import subprocess
import xml.etree.ElementTree as ET
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash, verify_feature_snapshot_v1793127
from services.v17_9_3_1_2_7_matched_ab import read_jsonl


def verify_fresh_full_junit(run,collection,junit):
    from tools.run_v17_9_3_1_2_2_verification import canonicalize_junit_testcase_node_id_v1793122, verify_fresh_junit_shards_attestation_v1793124
    attestation=verify_fresh_junit_shards_attestation_v1793124(junit,expected_tested_executable_sha=junit.get("tested_executable_sha",""))
    if not attestation["passed"]:
        raise ValueError("INVALID_FRESH_JUNIT_ATTESTATION:"+str(attestation["violations"]))
    expected=collection.get("collected_node_ids",[])
    ids=[]
    failures=errors=skips=0
    skip_rows=[]
    for shard in junit.get("shards",[]):
        path=Path(shard["junit_xml_path"])
        if not path.is_absolute():
            path=Path(__file__).resolve().parents[1]/path
        if not path.resolve().is_relative_to(run.resolve()) or shard.get("reused") is not False:
            raise ValueError("FOREIGN_OR_REUSED_JUNIT")
        if hashlib.sha256(path.read_bytes()).hexdigest()!=shard.get("xml_sha256"):
            raise ValueError("STALE_OR_TAMPERED_JUNIT")
        if path.stat().st_mtime_ns != shard.get("xml_mtime_ns"):
            raise ValueError("JUNIT_DISK_MTIME_MISMATCH")
        for tc in ET.parse(path).getroot().iter("testcase"):
            oid=canonicalize_junit_testcase_node_id_v1793122(tc.get("classname"),tc.get("name"))
            if oid is None: raise ValueError("UNEXPECTED_JUNIT_NODE")
            ids.append(oid)
            failures+=tc.find("failure") is not None
            errors+=tc.find("error") is not None
            skips+=tc.find("skipped") is not None
            if tc.find("skipped") is not None:
                skip_rows.append({"node_id":oid,"reason":tc.find("skipped").get("message")})
    valid=bool(expected) and Counter(expected)==Counter(ids) and len(set(ids))==len(ids) and failures==errors==0
    return {"status":"PASS" if valid else "FAIL","executed":len(ids),"passed":len(ids)-failures-errors-skips,
            "failures":failures,"errors":errors,"skipped":skip_rows,"node_sha256":canonical_hash(sorted(ids)),
            "passed_node_ids":sorted(set(ids)-{r["node_id"] for r in skip_rows}) if not failures and not errors else []}


def empirical_policy_statistics(results):
    policies={}
    for pid,result in sorted(results.items()):
        rows=[r for r in result["primary_episodes"] if r.get("outcome_label") in ("WIN","LOSS")]
        values=[float(r.get("realized_r",0)) for r in rows]
        n=len(values)
        rng=random.Random(1793127)
        mean=sum(values)/n if n else None
        draws=sorted(sum(rng.choices(values,k=n))/n for _ in range(2000)) if n else []
        policies[pid]={"closed_trades_count":n,"total_realized_net_usd":round(sum(float(r["realized_net_pnl_usd"]) for r in rows),4),
                       "expectancy_net_r":mean,"bootstrap_ci_95_low_r":draws[49] if draws else None,
                       "bootstrap_ci_95_high_r":draws[1949] if draws else None,
                       "bootstrap_positive_tail_p":sum(x-mean>=mean for x in draws)/len(draws) if draws else None,
                       "bootstrap_draws":len(draws),"sample_powered_ge_30":n>=30}
    hypotheses=sorted((r["bootstrap_positive_tail_p"],pid) for pid,r in policies.items() if pid!="SI0_OFF_BASELINE" and r["bootstrap_positive_tail_p"] is not None)
    reject_more=True
    for i,(p,pid) in enumerate(hypotheses):
        threshold=.05/(len(hypotheses)-i)
        rejected=reject_more and p<=threshold
        reject_more=rejected
        policies[pid]["holm_threshold"]=threshold
        policies[pid]["holm_positive_null_rejected"]=rejected
    powered=[r for pid,r in policies.items() if pid!="SI0_OFF_BASELINE" and r["closed_trades_count"]>=30]
    verdict=("NEGATIVE_EXPECTANCY" if powered and all(r["expectancy_net_r"]<=0 for r in powered) else
             "POSITIVE_IN_REUSED_HISTORY_NOT_VERIFIED" if any(r["expectancy_net_r"] and r["expectancy_net_r"]>0 for r in powered) else "INCONCLUSIVE_UNDERPOWERED")
    return {"policies":policies,"strategy_statistical_verdict":verdict,"activation_ready":"NO",
            "fresh_holdout":"REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS","method":"SEEDED_EMPIRICAL_BOOTSTRAP_HOLM_STEP_DOWN"}


def verify_git_remote_phase_receipt_v1793127(root, *, phase_a, phase_b=None, phase_c=None):
    def git(*args):
        return subprocess.check_output(["git",*args],cwd=root,text=True).strip()
    branch=git("branch","--show-current")
    remote_line=git("ls-remote","--heads","origin",branch)
    remote=remote_line.split()[0] if remote_line else None
    local=git("rev-parse","HEAD")
    for ancestor in (phase_a,phase_b):
        if ancestor:
            subprocess.run(["git","merge-base","--is-ancestor",ancestor,local],cwd=root,check=True)
    code_diff=git("diff","--name-only",phase_a,local,"--","aurum-desk/backend","aurum-desk/tools")
    status="PASS" if branch=="feature/v17-9-2-structural-sl-tp-from-qa" and local==remote and not code_diff and phase_b and phase_c==local else "BLOCKED"
    return {"status":status,"phase_a":phase_a,"phase_b":phase_b,"phase_c":phase_c,
            "observed_local_sha":local,"observed_remote_sha":remote,"code_diff_since_tested":code_diff,
            "note":"Final receipt observed externally; not embedded into its own commit hash."}


def verify_features_from_disk(path):
    total=valid=0
    import gzip
    with gzip.open(path,"rt",encoding="utf-8") as stream:
        for line in stream:
            row=json.loads(line)
            total+=1
            valid+=verify_feature_snapshot_v1793127(row)
    return {"status":"PASS" if total and total==valid else "FAIL","total":total,"valid":valid}


def verify_checkpoint_coverage_from_disk(path,datasets):
    from services.v17_9_3_1_2_3_core_trading_logic_repair import CANONICAL_5_MODELS_V1793123
    import gzip
    seen=set()
    violations=[]
    parity_mismatches=0
    for row in read_jsonl(path):
        key=(row["checkpoint_index_1m"],row["model_id"],row["direction"])
        idx=key[0]
        if key in seen:violations.append("DUPLICATE_CHECKPOINT_MODEL_DIRECTION")
        seen.add(key)
        if not 30<=idx<len(datasets["1m"]) or key[1] not in CANONICAL_5_MODELS_V1793123 or key[2] not in ("LONG","SHORT"):
            violations.append("UNEXPECTED_CHECKPOINT_MODEL_DIRECTION")
        elif row["decision_timestamp_ms"]!=int(datasets["1m"][idx]["close_time"]):
            violations.append("CHECKPOINT_SOURCE_TIMESTAMP_MISMATCH")
        if row["max_closed_bar_ms"]>row["decision_timestamp_ms"] or len(row.get("source_snapshot_hash",""))!=64:
            violations.append("INVALID_CHECKPOINT_CAUSAL_SOURCE")
        if not row.get("off_action_hash") or row.get("off_action_hash")!=row.get("observe_action_hash"):
            parity_mismatches+=1
    expected=(len(datasets["1m"])-30)*len(CANONICAL_5_MODELS_V1793123)*2
    if len(seen)!=expected:violations.append("MISSING_CHECKPOINT_MODEL_DIRECTION")
    return {"status":"FAIL" if violations else "PASS","expected_rows":expected,"actual_unique_rows":len(seen),
            "violations":dict(Counter(violations)),"off_observe_action_mismatches":parity_mismatches,
            "canonical_keys_sha256":canonical_hash(sorted(seen))}


def verify_raw_accounting_and_worker_proofs(run):
    """Recompute published economics and worker digests from separate raw files."""
    from services.v17_9_3_1_2_7_matched_ab import _money, _index
    metrics=json.loads((run/"15_all_policies_all_three_fill_modes_metrics.json").read_text())
    stats=json.loads((run/"24_engineering_strategy_activation_dual_verdict.json").read_text())
    workers=json.loads((run/"21_1v2v4_independent_determinism_sha_manifest.json").read_text())
    errors=[]
    from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets
    datasets,_=load_canonical_xauusdt_datasets()
    coverage=verify_checkpoint_coverage_from_disk(run/"05_full_checkpoint_model_direction_execution_audit.jsonl.gz",datasets)
    if coverage["status"]!="PASS" or coverage["off_observe_action_mismatches"]:
        errors.append("RAW_CHECKPOINT_COVERAGE_OR_OFF_OBSERVE_MISMATCH")
    hashes=[]
    for key,count in (("28",1),("29",2),("30",4)):
        report=workers[key]
        payload={pid:{k:v for k,v in r.items() if k!="worker_pid"} for pid,r in sorted(report["results_by_policy"].items())}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        hashes.append(digest)
        if digest!=report["canonical_results_sha256"] or report["worker_count"]!=count or len(report["worker_pids_observed"])!=count:
            errors.append("WORKER_EXECUTION_OR_DIGEST_MISMATCH:"+key)
        if set(payload)!=set(metrics): errors.append("WORKER_POLICY_COVERAGE_MISMATCH:"+key)
    if len(set(hashes))!=1: errors.append("INDEPENDENT_WORKER_DIGEST_DIVERGENCE")
    result_inputs={}
    oos=json.loads((run/"16_walkforward_purge_embargo_oos_freshness_and_uncertainty.json").read_text())
    if oos.get("split_boundaries_ms")!=[1791676800000,1792195200000] or oos.get("embargo_bars_m1")!=180 or oos.get("fresh_oos_verification_status")!="REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS":
        errors.append("RAW_OOS_SPLIT_OR_FRESHNESS_MISMATCH")
    for pid,modes in metrics.items():
        import psutil
        if psutil.virtual_memory().available<512*1024**2:
            raise RuntimeError("BLOCKED_LOW_RAM_DISK_ACCOUNTING")
        decisions=read_jsonl(run/(pid+"_decisions.jsonl.gz"))
        _index(decisions)
        from services.v17_9_3_1_2_7_causal_signal_truth import rebuild_materialized_intent_proof_v1793127
        dataset_sha=json.loads((run/"03_canonical_dataset_and_quote_source_manifest.json").read_text())["dataset_sha256"]
        for decision in decisions:
            final=decision.get("v1793127_final_feature_proof")
            if pid!="SI0_OFF_BASELINE" and decision.get("accepted") and not final:
                errors.append("ACCEPTED_INTENT_WITHOUT_FINAL_CAUSAL_PROOF:"+pid)
            if final:
                record,rebuilt,rejection=rebuild_materialized_intent_proof_v1793127(
                    decision["v1793127_final_record"],decision,datasets,dataset_sha256=dataset_sha)
                if canonical_hash(rebuilt)!=canonical_hash(final) or canonical_hash(record)!=canonical_hash(decision["v1793127_final_record"]) or record["opportunity_id"]!=decision["opportunity_id"] or (decision.get("accepted") and rejection):
                    errors.append("FINAL_INTENT_RAW_SOURCE_RECONSTRUCTION_MISMATCH:"+pid)
        decision_hash=hashlib.sha256(json.dumps(decisions,sort_keys=True).encode()).hexdigest()
        all_trades={}
        for mode,claimed in modes.items():
            trades=read_jsonl(run/(pid+"_"+mode+"_trades.jsonl.gz"))
            all_trades[mode]=trades
            _index(trades)
            for row in trades: _money(row)
            closed=[r for r in trades if r.get("filled") and r.get("outcome_label") in ("WIN","LOSS")]
            actual={"closed_trades_count":len(closed),"wins_count":sum(r["outcome_label"]=="WIN" for r in closed),
                    "losses_count":sum(r["outcome_label"]=="LOSS" for r in closed),
                    "total_realized_net_usd":round(sum(float(r["realized_net_pnl_usd"]) for r in closed),4),
                    "total_fees_usd":round(sum(float(r["entry_fee_usd"])+float(r["exit_fee_usd"]) for r in closed),4)}
            if any(claimed.get(k)!=v for k,v in actual.items()):
                errors.append("RAW_METRICS_MISMATCH:"+pid+":"+mode)
            for r in trades:
                if r.get("filled") and (r.get("planned_net_risk_usd") is None or not 0<float(r["planned_net_risk_usd"])<=2.5001 or r.get("net_rr") is None or float(r["net_rr"])<1.9999):
                    errors.append("RAW_RISK_COST_ENVELOPE_VIOLATION:"+pid+":"+mode)
        trade_hash=hashlib.sha256(json.dumps(all_trades,sort_keys=True).encode()).hexdigest()
        for key in ("28","29","30"):
            row=workers[key]["results_by_policy"].get(pid,{})
            if row.get("decision_rows_sha256")!=decision_hash or row.get("raw_all_mode_trades_sha256")!=trade_hash:
                errors.append("WORKER_HASH_NOT_BOUND_TO_RAW_LEDGER:"+pid+":"+key)
        result_inputs[pid]={"primary_episodes":all_trades["NEXT_BAR_OPEN_BID_ASK"]}
        closed=[r for r in all_trades["NEXT_BAR_OPEN_BID_ASK"] if r.get("filled") and r.get("outcome_label") in ("WIN","LOSS")]
        b1,b2,embargo=1791676800000,1792195200000,180*60000
        groups=([r for r in closed if int(r["exit_ts_ms"])<=b1-embargo],
                [r for r in closed if int(r["entry_ts_ms"])>=b1+embargo and int(r["exit_ts_ms"])<=b2-embargo],
                [r for r in closed if int(r["entry_ts_ms"])>=b2+embargo])
        for name,group in zip(("D_DEV_2026_10_01_TO_10_10","V_VAL_2026_10_11_TO_10_16","H_HOLDOUT_2026_10_17_TO_10_23"),groups):
            n=len(group)
            expected={"closed_trades":n,"wins":sum(r["outcome_label"]=="WIN" for r in group),"losses":sum(r["outcome_label"]=="LOSS" for r in group),
                      "net_usd":round(sum(float(r["realized_net_pnl_usd"]) for r in group),4),
                      "expectancy_net_r":round(sum(float(r["realized_r"]) for r in group)/n,4) if n else 0.0}
            if oos.get("policies",{}).get(pid,{}).get(name)!=expected:
                errors.append("RAW_OOS_PARTITION_ACCOUNTING_MISMATCH:"+pid+":"+name)
    if empirical_policy_statistics(result_inputs)!=stats:
        errors.append("RAW_STATISTICS_OR_POLICY_COVERAGE_MISMATCH")
    from services.v17_9_3_1_2_7_causal_signal_truth import build_forensic_causal_evidence_join_v1793127
    episodes=read_jsonl(run/"13_baseline_raw_execution_episodes.jsonl.gz")
    wanted={r["opportunity_id"] for r in episodes}
    from services.v17_9_3_1_2_1_root_cause_evidence_service import build_canonical_opportunity_id_v1793121
    dataset_sha=json.loads((run/"03_canonical_dataset_and_quote_source_manifest.json").read_text())["dataset_sha256"]
    for episode in episodes:
        wanted.add(build_canonical_opportunity_id_v1793121(episode,dataset_sha256=dataset_sha))
    features={}
    import gzip
    with gzip.open(run/"07_verified_causal_features.jsonl.gz","rt",encoding="utf-8") as stream:
        for line in stream:
            row=json.loads(line)
            if row["opportunity_id"] in wanted:features[row["opportunity_id"]]=row
    rebuilt=build_forensic_causal_evidence_join_v1793127(episodes,features)
    if rebuilt!=read_jsonl(run/"13_per_trade_forensics_observed_vs_hypothesized.jsonl.gz"):
        errors.append("RAW_FORENSIC_SOURCE_JOIN_OR_LABEL_MISMATCH")
    return {"status":"FAIL" if errors else "PASS","errors":errors,"policies":len(metrics),"worker_hashes":hashes}


def reconstruct_feature_sources_from_disk(run):
    """Rebuild from immutable canonical OHLC + raw decisions, ignoring produced flags."""
    from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets
    from tools.run_v17_9_3_1_2_1_full_opportunity_replay import DATASET_PATH
    from services.v17_9_3_1_2_7_causal_signal_truth import build_verified_baseline_pre_entry_feature_ledger_v1793127
    source_manifest=json.loads((run/"03_canonical_dataset_and_quote_source_manifest.json").read_text())
    sha=hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    if sha!=source_manifest["dataset_sha256"]:
        raise ValueError("DATASET_SOURCE_HASH_MISMATCH")
    datasets,_=load_canonical_xauusdt_datasets()
    groups={}
    for row in read_jsonl(run/"10_canonical_decision_inputs.jsonl.gz"):
        groups.setdefault(int(row["checkpoint_index_1m"]),[]).append(row)
    rebuilt=build_verified_baseline_pre_entry_feature_ledger_v1793127({"decisions_by_checkpoint_index":groups},datasets,dataset_sha256=sha,compact=True)
    mismatch=[]
    total=0
    import gzip
    with gzip.open(run/"07_verified_causal_features.jsonl.gz","rt",encoding="utf-8") as stream:
        for line in stream:
            row=json.loads(line)
            total+=1
            actual=rebuilt.pop(row["opportunity_id"],None)
            if not actual or row["proof_hash"]!=actual["proof_hash"] or not verify_feature_snapshot_v1793127(row):
                mismatch.append(row["opportunity_id"])
    return {"status":"PASS" if total and not mismatch and not rebuilt else "FAIL",
            "reconstructed_count":total,"mismatch_ids":mismatch[:100],"missing_count":len(rebuilt),
            "dataset_sha256":sha}
