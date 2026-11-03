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
    rebuilt=build_verified_baseline_pre_entry_feature_ledger_v1793127({"decisions_by_checkpoint_index":groups},datasets,dataset_sha256=sha)
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
