#!/usr/bin/env python3
"""Sequential, resource-monitored verification. Missing evidence never becomes PASS.

The operator freezes a code commit first. This runner never commits/pushes code
or enables execution. Phase receipts are verified separately after a clean run.
"""
from __future__ import annotations
import argparse
from collections import ChainMap, Counter
from datetime import datetime, timezone
import gzip
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

APP_ROOT=Path(__file__).resolve().parents[1]
ROOT=APP_ROOT.parent
sys.path[:0]=[str(APP_ROOT),str(APP_ROOT/"backend")]
from services.v17_9_3_1_2_7_causal_signal_truth import (
    canonical_hash, verify_feature_snapshot_v1793127, evidence_cache_metrics,
    build_forensic_causal_evidence_join_v1793127,
)
from services.v17_9_3_1_2_7_matched_ab import compute_matched_policy_ab_v1793127, verify_matched_ab_against_raw_ledgers_v1793127
from services.research_resource_guard import raise_if_research_aborted

VERSION="17.9.3.1.2.7"
REQUIREMENTS=("same branch/ancestry", "frozen source", "dataset chronology", "full-universe coverage",
 "source-grounded features", "causal timestamps", "FVG adversarial tests", "Unicorn evidence tests",
 "unknown fail closed", "OFF/OBSERVE parity", "policy decision diff", "independent matched AB",
 "forensic observations", "quote provenance", "risk/cost envelope", "three fill modes",
 "fresh full JUnit bijection", "executable source mutations", "1/2/4 independent determinism",
 "resource monitor", "OOS freshness/purge", "dynamic statistical verdict", "legacy regression",
 "disk artifact integrity", "phase A/B/C remote receipt")


def git(*args):
    return subprocess.check_output(["git",*args],cwd=ROOT,text=True).strip()


def write(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+".partial")
    with temp.open("w",encoding="utf-8",newline="\n") as f:
        json.dump(payload,f,sort_keys=True,indent=2,allow_nan=False,default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp,path)


def write_rows(path,rows):
    tmp=path.with_suffix(path.suffix+".partial")
    with gzip.open(tmp,"wt",encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r,sort_keys=True,allow_nan=False,default=str)+"\n")
    os.replace(tmp,path)


def source_manifest():
    files={}
    for rel in git("ls-files","--","aurum-desk/backend/*.py","aurum-desk/tools/*.py","aurum-desk/backend/requirements.txt").splitlines():
        files[rel]=hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()
    return {"files":files,"sha256":canonical_hash(files),"python":sys.version}


def prior_artifact_manifest():
    files={}
    for rel in git("ls-files","--","aurum-desk/docs/artifacts","aurum-desk/backtest").splitlines():
        path=ROOT/rel
        if path.is_file():
            with path.open("rb") as stream:files[rel]=hashlib.file_digest(stream,"sha256").hexdigest()
    return {"files":files,"sha256":canonical_hash(files)}


def _active_heavy_worker_children(proc):
    import psutil
    raw_children = proc.children(recursive=True)
    by_ppid = {}
    for ch in raw_children:
        try:
            by_ppid.setdefault(ch.ppid(), []).append(ch)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    out=[]
    for ch in raw_children:
        try:
            if ch.status()==psutil.STATUS_ZOMBIE:
                continue
            name=(ch.name() or "").lower()
            if name.startswith("git"):
                continue
            cmdline = list(ch.cmdline() or ())
            cmd=" ".join(cmdline)
            if "multiprocessing.resource_tracker" in cmd or " git " in f" {cmd} " or cmd.startswith("git "):
                continue
            # Exclude Windows PEP-405 venv launcher wrapper when its direct Python child is already counted
            direct_py = [
                g for g in by_ppid.get(ch.pid, ())
                if (g.name() or "").lower().startswith("python")
            ]
            if len(direct_py) == 1 and "venv" in (ch.exe() or "").lower():
                try:
                    if cmdline[1:] == list(direct_py[0].cmdline() or ())[1:]:
                        continue
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            out.append(ch)
        except (psutil.NoSuchProcess,psutil.AccessDenied,psutil.ZombieProcess):
            continue
    return out


class ResourceMonitor:
    def __init__(self, *, minimum_ram_bytes=512 * 1024**2):
        self.stop_event=threading.Event()
        self.samples=[]
        self.error=None
        self.abort_path=None
        self.minimum_ram_bytes=minimum_ram_bytes
    def check(self):
        import psutil
        proc=psutil.Process()
        children=_active_heavy_worker_children(proc)
        ram=psutil.virtual_memory().available
        free=shutil.disk_usage(ROOT).free
        def child_rss(child):
            try: return child.memory_info().rss
            except (psutil.NoSuchProcess,psutil.AccessDenied,psutil.ZombieProcess): return 0
        row={"epoch_ns":time.time_ns(),"free_disk_bytes":free,"available_ram_bytes":ram,
             "system_cpu_percent":psutil.cpu_percent(),
             "worker_pids":[p.pid for p in children],"rss_bytes":proc.memory_info().rss,
             "child_rss_bytes":sum(child_rss(p) for p in children)}
        self.samples.append(row)
        if len(self.samples)>10000: self.samples=self.samples[::2]
        if free<5*1024**3: self.error="BLOCKED_LOW_DISK"
        if ram<self.minimum_ram_bytes: self.error="BLOCKED_LOW_RAM"
        if len(children)>4: self.error="BLOCKED_GLOBAL_WORKER_CAP"
        if self.error: raise RuntimeError(self.error)
    def loop(self):
        while not self.stop_event.wait(2):
            try: self.check()
            except Exception as exc:
                self.error=str(exc)
                if self.abort_path:
                    write(self.abort_path,{"status":"BLOCKED","reason":self.error,"last_sample":self.samples[-1] if self.samples else None})
                # Guarded checkpoints propagate; do not kill unrelated applications.
                import psutil
                for child in reversed(_active_heavy_worker_children(psutil.Process())):
                    try: child.terminate()
                    except psutil.Error: pass
                break
    def start(self):
        self.check()
        self.thread=threading.Thread(target=self.loop,daemon=True)
        self.thread.start()
    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
        import psutil
        remaining=_active_heavy_worker_children(psutil.Process())
        remaining_pids=[p.pid for p in remaining]
        if remaining:
            self.error=self.error or "BLOCKED_ORPHAN_WORKERS_REMAINING"
            for child in reversed(remaining):
                try:child.terminate()
                except psutil.Error:pass
        return {"samples":self.samples,"error":self.error,"global_worker_cap":4,
                "orphan_pids_observed_at_cleanup":remaining_pids,
                "max_children_observed":max((len(s["worker_pids"]) for s in self.samples),default=0),
                "minimum_available_ram_bytes":min((s["available_ram_bytes"] for s in self.samples),default=None),
                "minimum_free_disk_bytes":min((s["free_disk_bytes"] for s in self.samples),default=None),
                "peak_total_rss_bytes":max((s["rss_bytes"]+s["child_rss_bytes"] for s in self.samples),default=None)}


def evaluate_v1793127_acceptance_gates(proofs, *, tested_sha, dataset_sha):
    gates=[]
    for i,requirement in enumerate(REQUIREMENTS,1):
        key=f"G{i:02d}"
        p=proofs.get(key)
        valid=isinstance(p,dict) and p.get("status") in ("PASS","FAIL","BLOCKED") and bool(p.get("source")) and "measured" in p
        status=p["status"] if valid else "BLOCKED"
        gates.append({"gate_id":key,"requirement":requirement,"status":status,
                      "measured":p.get("measured") if valid else None,
                      "source":p.get("source") if valid else None,
                      "reason":p.get("reason") if valid else "MISSING_INDEPENDENT_PROOF",
                      "tested_sha":tested_sha,"dataset_sha256":dataset_sha})
    return {"gates":gates,"engineering_verdict":"PASS" if all(g["status"]=="PASS" for g in gates) else "BLOCKED",
            "prepublication_engineering":"PASS" if all(g["status"]=="PASS" for g in gates if g["gate_id"]!="G25") else "BLOCKED",
            "activation_ready":"NO"}


def verify_v1793127_artifact_truth(run_dir):
    errors=[]
    manifest=json.loads((run_dir/"artifact_sha256_manifest.json").read_text(encoding="utf-8"))
    required=("07_verified_causal_features.jsonl.gz","07_independent_source_reconstruction.json",
              "10_canonical_decision_inputs.jsonl.gz","18_independent_junit_verification.json",
              "19_new_executable_red_green_mutation_evidence.json")
    for rel in required:
        if rel not in manifest["files"]:
            errors.append("MISSING_REQUIRED_ARTIFACT:"+rel)
    if len(manifest.get("ab_specs",[])) != 9:
        errors.append("MISSING_REQUIRED_AB_COMPARISONS")
    for rel,expected in manifest["files"].items():
        path=(run_dir/rel).resolve()
        if not path.is_relative_to(run_dir.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            errors.append("FILE_HASH_MISMATCH:"+rel)
    for spec in manifest.get("ab_specs",[]):
        proof=verify_matched_ab_against_raw_ledgers_v1793127(run_dir,spec)
        if proof["status"]!="PASS": errors.append(proof["reason"])
    from tools.v17_9_3_1_2_7_proof_checks import verify_features_from_disk
    if (run_dir/required[0]).is_file() and verify_features_from_disk(run_dir/required[0])["status"]!="PASS":
        errors.append("INVALID_SERIALIZED_FEATURE_PROOF")
    for rel in (required[1],required[3],required[4]):
        if (run_dir/rel).is_file() and json.loads((run_dir/rel).read_text(encoding="utf-8")).get("status")!="PASS":
            errors.append("FAILED_INDEPENDENT_PROOF:"+rel)
    try:
        from tools.v17_9_3_1_2_7_proof_checks import verify_raw_accounting_and_worker_proofs
        economics=verify_raw_accounting_and_worker_proofs(run_dir)
        errors.extend(economics["errors"])
    except (OSError,KeyError,TypeError,ValueError,RuntimeError) as exc:
        errors.append("MISSING_OR_INVALID_RAW_ACCOUNTING_PROOF:"+str(exc))
    return {"status":"FAIL" if errors else "PASS","errors":errors,"verified_files":len(manifest["files"])}


def run_independent_source_reconstruction(run):
    """Preserve the actual child failure instead of masking it as a missing file."""
    raise_if_research_aborted()
    target=run/"07_independent_source_reconstruction.json"
    code=("import sys,json; from pathlib import Path; sys.path[:0]=['backend','.']\n"
          "from tools.v17_9_3_1_2_7_proof_checks import reconstruct_feature_sources_from_disk\n"
          "try:\n"
          f" r=reconstruct_feature_sources_from_disk(Path({str(run)!r}))\n"
          "except Exception as exc:\n"
          " r={'status':'BLOCKED','reason':str(exc),'exception_type':type(exc).__name__}\n"
          f"Path({str(target)!r}).write_text(json.dumps(r),encoding='utf-8')\n"
          "raise SystemExit(0 if r['status']=='PASS' else 1)\n")
    try:
        with (run/"07_source_reconstruction.log").open("w",encoding="utf-8") as log:
            child=subprocess.run([sys.executable,"-c",code],cwd=APP_ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=3600)
        exit_code=child.returncode
    except subprocess.TimeoutExpired:
        exit_code="TIMEOUT"
    if not target.is_file():
        reason="RECONSTRUCTION_CHILD_DID_NOT_WRITE_RESULT"
        marker=os.getenv("AURUM_RESOURCE_GUARD_ABORT_FILE")
        if marker and Path(marker).is_file():
            reason="RESOURCE_GUARD:"+str(json.loads(Path(marker).read_text(encoding="utf-8")).get("reason","ABORTED"))
        write(target,{"status":"BLOCKED","reason":reason,"exit_code":exit_code})
    result=json.loads(target.read_text(encoding="utf-8"))
    if exit_code!=0 or result.get("status")!="PASS":
        raise RuntimeError("INDEPENDENT_SOURCE_RECONSTRUCTION_FAILED:"+str(result.get("reason",result.get("status")))+":"+str(exit_code))
    return result


def main(*, artifact_namespace="v17_9_3_1_2_7", minimum_ram_bytes=512 * 1024**2):
    ap=argparse.ArgumentParser()
    ap.add_argument("--tested-code-sha",required=True)
    ap.add_argument("--run-id",default="")
    args=ap.parse_args()
    # Windows venv python.exe and Git/cmd are process redirectors. Launch the
    # underlying binaries, retaining this venv's packages in child PYTHONPATH,
    # so four workers mean four OS processes rather than eight redirectors.
    if os.name=="nt":
        os.environ["AURUM_VERIFY_VENV_PYTHON"]=sys.executable
        packages=str(Path(sys.prefix)/"Lib/site-packages")
        os.environ["PYTHONPATH"]=os.pathsep.join([packages,os.environ.get("PYTHONPATH","")])
        sys.executable=getattr(sys,"_base_executable",sys.executable)
        found=shutil.which("git")
        direct=Path(found).parents[1]/"mingw64/bin" if found else None
        if direct and (direct/"git.exe").is_file():
            os.environ["PATH"]=str(direct)+os.pathsep+os.environ.get("PATH","")
    sha=git("rev-parse","HEAD")
    if sha!=args.tested_code_sha or git("diff","--name-only","HEAD","--","aurum-desk/backend","aurum-desk/tools"):
        raise SystemExit("FROZEN_EXECUTABLE_SHA_MISMATCH_OR_DIRTY_SOURCE")
    if git("branch","--show-current")!="feature/v17-9-2-structural-sl-tp-from-qa":
        raise SystemExit("WRONG_BRANCH")
    subprocess.run(["git","merge-base","--is-ancestor","3ed8f8af337a1a065da265861f95ddd2d7f625a4",sha],cwd=ROOT,check=True)
    runid=args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+sha[:12]
    run=APP_ROOT/"docs/artifacts"/artifact_namespace/runid
    run.mkdir(parents=True,exist_ok=False)
    os.environ["AURUM_VERIFICATION_ARTIFACT_ROOT"]=str(run/"auxiliary")
    before=source_manifest()
    prior_before=prior_artifact_manifest()
    write(run/"01_prior_artifact_sha256_before.json",prior_before)
    write(run/"01_git_preflight_and_frozen_executable_sha.json",{"sha":sha,"branch":git("branch","--show-current"),"source":before})
    import ast
    inventory={}
    for rel in before["files"]:
        if rel.endswith(".py") and ("v17_9_3_1_2_7" in rel or Path(rel).name in ("shared_strategy_evaluator.py","scalp_watcher_orchestrator.py","v17_9_3_1_2_2_signal_intelligence.py","v17_9_3_1_2_3_core_trading_logic_repair.py")):
            tree=ast.parse((ROOT/rel).read_text(encoding="utf-8"))
            inventory[rel]=[{"function":n.name,"line":n.lineno,"contract":ast.get_docstring(n)} for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
    write(run/"02_source_function_and_consumer_inventory.json",inventory)
    write(run/"10_predeclared_hypotheses.json",{"frozen_sha":sha,"dataset_status":"PREVIOUSLY_EXPOSED_HISTORY",
          "hypotheses":["Source truth may reduce eligibility; no minimum trade count is forced.",
                        "SI8 and SI9 may be identical; identity does not establish incremental edge.",
                        "Profitability is assessed after costs; activation remains NO."],
          "baseline_corpus":"Immutable prior canonical decisions are input data; feature proofs and policy replays are rebuilt on this executable SHA."})
    env=subprocess.run([sys.executable,"-m","pip","freeze"],capture_output=True,text=True,check=True)
    write(run/"02_environment_package_manifest.json",{"python":sys.executable,"version":sys.version,"packages":env.stdout.splitlines(),"source_manifest":before})
    stage_workers=max(1,min(4,int(os.environ.get("AURUM_RESEARCH_STAGE_WORKERS","3"))))
    test_workers=1
    os.environ["AURUM_RESEARCH_STAGE_WORKERS"]="1"
    os.environ["AURUM_TESTED_EXECUTABLE_SHA"]=sha
    os.environ["AURUM_RESEARCH_TEST_WORKERS"]="1"
    os.environ["AURUM_RESEARCH_MAX_FILES_PER_SHARD"]="4"
    os.environ["AURUM_RESEARCH_PROCESS_WORKERS"]="1"
    proofs={"G01":{"status":"PASS","source":"01_git_preflight_and_frozen_executable_sha.json","measured":sha}}
    dataset_sha="UNAVAILABLE"
    specs=[]
    monitor=ResourceMonitor(minimum_ram_bytes=minimum_ram_bytes)
    monitor.abort_path=run/"resource_abort.json"
    os.environ["AURUM_RESOURCE_GUARD_ABORT_FILE"]=str(monitor.abort_path)
    os.environ["AURUM_MINIMUM_RAM_BYTES"]=str(minimum_ram_bytes)
    def abort_on_signal(signum,frame):
        monitor.error=f"BLOCKED_SIGNAL_{signum}"
        write(monitor.abort_path,{"status":"BLOCKED","reason":monitor.error})
        import psutil
        for child in reversed(psutil.Process().children(recursive=True)):
            try:child.terminate()
            except psutil.Error:pass
        raise RuntimeError(monitor.error)
    for signal_name in ("SIGINT","SIGTERM","SIGBREAK"):
        if hasattr(signal,signal_name):signal.signal(getattr(signal,signal_name),abort_on_signal)
    failure=None
    try:
        monitor.start()
        print("[A] Fresh full backend collection and sequential shards",flush=True)
        from tools.run_v17_9_3_1_2_2_verification import run_full_backend_pytest_collection_and_sharded_junit_v1793122, run_16_executable_red_green_source_mutations_v1793122
        collection,junit=run_full_backend_pytest_collection_and_sharded_junit_v1793122(run/"junit",tested_executable_sha=sha,max_workers=1,force_fresh_execution=True)
        write(run/"17_fresh_full_pytest_collection_node_ids_and_hash.json",collection)
        write(run/"18_full_fresh_junit_shard_bijection_and_exitcodes.json",junit)
        from tools.v17_9_3_1_2_7_proof_checks import verify_fresh_full_junit, empirical_policy_statistics
        junit_check=verify_fresh_full_junit(run,collection,junit)
        write(run/"18_independent_junit_verification.json",junit_check)
        write(run/"20_executed_negative_control_node_index.json",{"source":"18_independent_junit_verification.json",
              "passed_control_node_ids":[n for n in junit_check["passed_node_ids"] if any(s in n for s in ("v17_9_3_1_2_7","stale","missing_junit","source_mutation"))],
              "full_junit_verdict":junit_check["status"]})
        proofs["G17"]={"status":junit_check["status"],"source":"18_independent_junit_verification.json","measured":{k:junit.get(k) for k in ("passed_count","failed_count","error_count","skipped_count","reused_shards_count")}}
        monitor.check()
        print("[B] Isolated executable source mutations",flush=True)
        mutations=run_16_executable_red_green_source_mutations_v1793122(tested_executable_sha=sha)
        write(run/"19_old_executable_red_green_mutation_evidence.json",mutations)
        from tools.v17_9_3_1_2_7_source_mutations import run_new_source_mutations
        new_mutations=run_new_source_mutations(run/"mutations")
        write(run/"19_new_executable_red_green_mutation_evidence.json",new_mutations)
        proofs["G18"]={"status":"PASS" if new_mutations["status"]=="PASS" and mutations.get("all_mutations_caught_red_and_restored_green") is True else "BLOCKED","source":"19_new_executable_red_green_mutation_evidence.json","measured":new_mutations["count"]}
        monitor.check()
        print(f"[C] Full-universe causal discovery and all-policy three-fill replay (workers={stage_workers})",flush=True)
        os.environ["AURUM_DISCOVERY_CHECKPOINT_DIR"]=str(run/"discovery_checkpoints")
        os.environ["AURUM_TESTED_EXECUTABLE_SHA"]=sha
        os.environ["AURUM_RESEARCH_STAGE_WORKERS"]=str(stage_workers)
        from tools.run_v17_9_3_1_2_2_entry_signal_backtest import run_v17_9_3_1_2_2_entry_signal_backtest_pipeline
        evidence=run_v17_9_3_1_2_2_entry_signal_backtest_pipeline(run/"raw_backtest",tested_executable_sha=sha,full_universe_si8=True)
        os.environ["AURUM_RESEARCH_STAGE_WORKERS"]="1"
        monitor.check()
        internal=evidence.pop("_internal_context")
        dataset_sha=evidence["01"]["dataset_sha256"]
        features=ChainMap(internal["cand_index"].get("si8_discovered_feature_ledger_by_opp_id",{}),internal["feat_by_opp_id"])
        write_rows(run/"07_verified_causal_features.jsonl.gz",features.values())
        states=Counter((ref["evidence_type"],ref["proof_state"]) for r in features.values() for ref in r["causal_snapshot"]["features"])
        write(run/"06_causal_evidence_contract_and_proof_state_counts.json",{"rows":len(features),"proof_states":{name:dict((state,n) for (feature,state),n in states.items() if feature==name) for name in sorted({key[0] for key in states})},"schema":"VerifiedPreEntryFeatureSnapshot","unknown_eligibility":"REJECTED_UNPROVEN"})
        write(run/"03_canonical_dataset_and_quote_source_manifest.json",{"dataset_sha256":dataset_sha,"quote_source_kind":"SYNTHETIC_RESEARCH_QUOTE","assumption":"CLOSE_PLUS_MINUS_0.10_USD"})
        discovery=internal["si8_discovery_report"]
        write_rows(run/"05_full_checkpoint_model_direction_execution_audit.jsonl.gz",discovery.pop("checkpoint_audit_rows"))
        from tools.v17_9_3_1_2_7_proof_checks import verify_checkpoint_coverage_from_disk
        checkpoint_proof=verify_checkpoint_coverage_from_disk(run/"05_full_checkpoint_model_direction_execution_audit.jsonl.gz",internal["datasets"])
        write(run/"05_independent_checkpoint_and_off_observe_verification.json",checkpoint_proof)
        expected_checkpoints=max(0,len(internal["datasets"]["1m"])-30)
        observed=sum(v["observed"] for v in discovery["funnel_by_model"].values())
        write(run/"05_full_universe_checkpoint_counts_and_model_funnel.json",{
            "expected_checkpoints":expected_checkpoints,"observed_model_directions":observed,
            "funnel":discovery["funnel_by_model"],"reported_checkpoint_count":discovery["causal_checkpoint_count"]})
        proofs["G04"]={"status":checkpoint_proof["status"],"source":"05_independent_checkpoint_and_off_observe_verification.json","measured":checkpoint_proof}
        from tools.run_v17_9_3_1_2_1_full_opportunity_replay import DATASET_PATH
        actual_dataset_sha=hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
        bad_bars=sum(1 for tf,bars in internal["datasets"].items() if isinstance(bars,list) for b in bars if isinstance(b,dict) and (b.get("is_closed") is False or b.get("close_time",0)<=b.get("open_time",b.get("timestamp",0))))
        proofs["G03"]={"status":"PASS" if actual_dataset_sha==dataset_sha and bad_bars==0 else "FAIL","source":"03_canonical_dataset_and_quote_source_manifest.json","measured":{"actual_hash":actual_dataset_sha,"bad_bars":bad_bars}}
        valid_count=sum(verify_feature_snapshot_v1793127(r) for r in features.values())
        proofs["G05"]={"status":"BLOCKED" if features and valid_count==len(features) else "FAIL","source":"07_independent_source_reconstruction.json","measured":{"rows":len(features),"valid":valid_count},"reason":"INDEPENDENT_SOURCE_RECONSTRUCTION_PENDING"}
        results=internal["si_results_by_policy"]
        merged={int(k):list(v) for k,v in internal["cand_index"]["decisions_by_checkpoint_index"].items()}
        for k,v in internal["cand_index"].get("si8_discovered_decisions_by_checkpoint_index",{}).items():
            merged.setdefault(int(k),[]).extend(v)
        write_rows(run/"10_canonical_decision_inputs.jsonl.gz",(r for _,rows in sorted(merged.items()) for r in rows))
        write(run/"15_all_policies_all_three_fill_modes_metrics.json",{p:r["replays_by_fill_mode"] for p,r in results.items()})
        for policy,result in results.items():
            write_rows(run/(policy+"_decisions.jsonl.gz"),result["policy_decision_ledger"])
            for mode,trades in result["all_mode_episodes"].items():
                write_rows(run/(policy+"_"+mode+"_trades.jsonl.gz"),trades)
        pairs=[("SI0_OFF_BASELINE","SI8_RESEARCH_DISCOVERY_REPAIRED"),("SI8_RESEARCH_DISCOVERY_REPAIRED","SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS"),("SI7_CORE_TRADING_LOGIC_REPAIRED","SI8_RESEARCH_DISCOVERY_REPAIRED")]
        for a,b in pairs:
            for mode in results[a]["all_mode_episodes"]:
                cid=a.split("_")[0]+"_"+b.split("_")[0]+"_"+mode
                report=compute_matched_policy_ab_v1793127(results[a]["policy_decision_ledger"],results[b]["policy_decision_ledger"],results[a]["all_mode_episodes"][mode],results[b]["all_mode_episodes"][mode],comparison_id=cid,fill_mode=mode)
                pair_rows=report.pop("pair_ledger")
                diff_rows=report["decision_diff"].pop("rows")
                write_rows(run/(cid+"_pairs.jsonl.gz"),pair_rows)
                write_rows(run/(cid+"_decision_diff.jsonl.gz"),diff_rows)
                write(run/(cid+".json"),report)
                spec={"decisions_a":a+"_decisions.jsonl.gz","decisions_b":b+"_decisions.jsonl.gz","trades_a":a+"_"+mode+"_trades.jsonl.gz","trades_b":b+"_"+mode+"_trades.jsonl.gz","claim":cid+".json"}
                spec.update(pair_rows=cid+"_pairs.jsonl.gz",decision_diff_rows=cid+"_decision_diff.jsonl.gz")
                specs.append(spec)
        abchecks=[verify_matched_ab_against_raw_ledgers_v1793127(run,s) for s in specs]
        write(run/"11_true_matched_ab_summary_and_independent_verification.json",abchecks)
        proofs["G12"]={"status":"PASS" if abchecks and all(r["status"]=="PASS" for r in abchecks) else "FAIL","source":"11_true_matched_ab_summary_and_independent_verification.json","measured":abchecks}
        forensic=build_forensic_causal_evidence_join_v1793127(internal["a1_episodes"],features)
        write_rows(run/"13_baseline_raw_execution_episodes.jsonl.gz",internal["a1_episodes"])
        write_rows(run/"13_per_trade_forensics_observed_vs_hypothesized.jsonl.gz",forensic)
        write(run/"14_forensic_root_cause_evidence_and_unknown_breakdown.json",dict(Counter(r["primary_label"] for r in forensic)))
        write(run/"07_cache_metrics.json",{**evidence_cache_metrics(),
              "baseline_encoded_payload_bytes":getattr(internal["feat_by_opp_id"],"encoded_bytes",None),
              "discovery_encoded_payload_bytes":getattr(internal["cand_index"].get("si8_discovered_feature_ledger_by_opp_id"),"encoded_bytes",None)})
        stats=empirical_policy_statistics(results)
        write(run/"24_engineering_strategy_activation_dual_verdict.json",stats)
        write(run/"16_walkforward_purge_embargo_oos_freshness_and_uncertainty.json",evidence["21"])
        # Gates reference actual disk evidence; unavailable proof remains BLOCKED.
        passed_nodes=junit_check["passed_node_ids"]
        def test_gate(gid,parts):
            selected=[n for n in passed_nodes if any(p in n for p in parts)]
            proofs[gid]={"status":"PASS" if selected and junit_check["status"]=="PASS" else "FAIL","source":"18_independent_junit_verification.json","measured":selected}
        test_gate("G07",["v1793126_p0_a","test_real_fvg_retest_requires_closed_post_formation_touch"])
        if len(proofs["G07"]["measured"])<9:proofs["G07"]["status"]="FAIL"
        test_gate("G08",["unicorn_evidence","unicorn_overlap"])
        test_gate("G09",["test_unknown_source_rejects_all_five_models_both_directions"])
        if len(proofs["G09"]["measured"])!=10:proofs["G09"]["status"]="FAIL"
        proofs["G10"]={"status":"PASS" if checkpoint_proof["status"]=="PASS" and checkpoint_proof["off_observe_action_mismatches"]==0 else "FAIL","source":"05_independent_checkpoint_and_off_observe_verification.json","measured":checkpoint_proof["off_observe_action_mismatches"]}
        test_gate("G23",["test_v17_9_2","test_v17_7"])
        proofs["G06"]={"status":"PASS" if valid_count==len(features) and features else "FAIL","source":"07_verified_causal_features.jsonl.gz","measured":valid_count}
        proofs["G11"]={"status":"PASS" if len(specs)==9 and all(r["status"]=="PASS" for r in abchecks) else "FAIL","source":"11_true_matched_ab_summary_and_independent_verification.json","measured":len(specs)}
        proofs["G13"]={"status":"PASS" if len(forensic)==len(internal["a1_episodes"]) and all(r["confidence"] in ("UNKNOWN","OBSERVED_NOT_CAUSAL") and (r["primary_label"] in ("OTHER/UNKNOWN","FILL_GAP/AMBIGUOUS_INTRABAR","FEES_DOMINATE") or r["source_proof_valid"]) for r in forensic) else "FAIL","source":"13_per_trade_forensics_observed_vs_hypothesized.jsonl.gz","measured":len(forensic)}
        proofs["G14"]={"status":"PASS" if features and all(r["quote_source_kind"]=="SYNTHETIC_RESEARCH_QUOTE" for r in features.values()) else "FAIL","source":"03_canonical_dataset_and_quote_source_manifest.json","measured":dict(Counter(r["quote_source_kind"] for r in features.values()))}
        risk_bad=[]
        for p,r in results.items():
            for mode,rows in r["all_mode_episodes"].items():
                for row in rows:
                    risk=row.get("planned_net_risk_usd")
                    rr=row.get("planned_net_rr",row.get("net_rr"))
                    if row.get("filled") and (risk is None or float(risk)>2.5001 or rr is None or float(rr)<1.9999):
                        risk_bad.append({"policy":p,"mode":mode,"opportunity_id":row["opportunity_id"],"risk":risk,"rr":rr})
        write(run/"15_risk_cost_violations.json",risk_bad)
        proofs["G15"]={"status":"FAIL" if risk_bad else "PASS","source":"15_risk_cost_violations.json","measured":len(risk_bad)}
        proofs["G16"]={"status":"PASS" if all(set(r["all_mode_episodes"])=={"NEXT_BAR_OPEN_BID_ASK","LEGACY_CLOSE_FILL","STRESS_WIDER_SPREAD_SLIPPAGE_2X"} for r in results.values()) else "FAIL","source":"15_all_policies_all_three_fill_modes_metrics.json","measured":{p:list(r["all_mode_episodes"]) for p,r in results.items()}}
        parity=evidence["31"]
        write(run/"21_1v2v4_independent_determinism_sha_manifest.json",{k:evidence[k] for k in ("28","29","30","31")})
        proofs["G19"]={"status":"PASS" if parity["worker_1_sha256"]==parity["worker_2_sha256"]==parity["worker_4_sha256"] else "FAIL","source":"21_1v2v4_independent_determinism_sha_manifest.json","measured":[parity[k] for k in ("worker_1_sha256","worker_2_sha256","worker_4_sha256")]}
        oos=evidence["21"]
        proofs["G21"]={"status":"PASS" if oos.get("fresh_oos_verification_status")=="REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS" and oos.get("embargo_bars_m1",0)>=180 else "BLOCKED","source":"16_walkforward_purge_embargo_oos_freshness_and_uncertainty.json","measured":{"fresh_oos":"NOT_AVAILABLE","embargo":oos.get("embargo_bars_m1")}}
        proofs["G22"]={"status":"PASS" if set(stats["policies"])==set(results) and stats["activation_ready"]=="NO" else "FAIL","source":"24_engineering_strategy_activation_dual_verdict.json","measured":stats["strategy_statistical_verdict"]}
        # Raw outputs are durable before D; the parent releases the large corpus
        # instead of retaining it alongside an independent reconstruction copy.
        del internal, results, features, merged, discovery, evidence, forensic, result, r, pair_rows, diff_rows, report
        gc.collect()
        monitor.check()
        print("[D] Independent process reconstructs features from on-disk decisions and raw OHLC",flush=True)
        reconstruction_result=run_independent_source_reconstruction(run)
        proofs["G05"]={"status":reconstruction_result["status"],"source":"07_independent_source_reconstruction.json","measured":reconstruction_result}
    except Exception as exc:
        failure={"type":type(exc).__name__,"message":str(exc)}
        import traceback
        failure["traceback"]=traceback.format_exc()
        write(run/"failure.json",failure)
    finally:
        resource=monitor.stop() if hasattr(monitor,"thread") else {"error":monitor.error,"samples":monitor.samples}
        write(run/"22_resource_cpu_ram_disk_worker_cleanup_metrics.json",resource)
        after=source_manifest()
        prior_after=prior_artifact_manifest()
        changed=[rel for rel in prior_before["files"].keys()|prior_after["files"].keys() if prior_before["files"].get(rel)!=prior_after["files"].get(rel)]
        write(run/"01_prior_artifact_immutability_verification.json",{"status":"FAIL" if changed else "PASS","before_sha256":prior_before["sha256"],"after_sha256":prior_after["sha256"],"changed_files":changed,"files":len(prior_before["files"])})
        current_branch=git("branch","--show-current")
        proofs["G01"]={"status":"FAIL" if changed or current_branch!="feature/v17-9-2-structural-sl-tp-from-qa" else "PASS","source":"01_prior_artifact_immutability_verification.json","measured":{"branch":current_branch,"changed_files":changed}}
        proofs["G02"]={"status":"PASS" if before==after and git("rev-parse","HEAD")==sha else "FAIL","source":"01_git_preflight_and_frozen_executable_sha.json","measured":after["sha256"]}
        proofs["G20"]={"status":"PASS" if resource.get("samples") and not resource.get("error") else "FAIL","source":"22_resource_cpu_ram_disk_worker_cleanup_metrics.json","measured":resource.get("error")}
        proofs["G24"]={"status":"BLOCKED","source":"artifact_sha256_manifest.json","measured":None,"reason":"FINAL_DISK_VERIFICATION_PENDING"}
        gates=evaluate_v1793127_acceptance_gates(proofs,tested_sha=sha,dataset_sha=dataset_sha)
        write(run/"23_acceptance_gates_computed_from_proof.json",gates)
        report_lines=["# V17.9.3.1.2.7 verification",f"Tested executable: `{sha}`.",
                      f"Engineering: {gates['engineering_verdict']}; prepublication: {gates['prepublication_engineering']}.",
                      "Activation: NO. Fresh untouched OOS: NOT_AVAILABLE; October history was previously exposed.",
                      "Final remote publication is a separate phase; no unperformed push is certified here.",
                      f"Failure: {failure['message'] if failure else 'No stage exception.'}",
                      "", "| Function | Responsibility |", "|---|---|",
                      "| derive_pre_entry_features_from_causal_context_v1793127 | Closed OHLC to source-bound tri-state evidence. |",
                      "| build_verified_baseline_pre_entry_feature_ledger_v1793127 | Reconstruct every canonical opportunity without pseudo-features. |",
                      "| replay_single_si_policy_v1793122 | Reject unproven research candidates before arbitration; preserve raw intents. |",
                      "| compute_matched_policy_ab_v1793127 | Injective decision/trade pairing, separate matched and portfolio deltas. |",
                      "| verify_matched_ab_against_raw_ledgers_v1793127 | Reload separate disk ledgers and rebuild the complete comparison. |",
                      "| classify_observed_loss_v1793127 | Report measured observations; keep unsupported causes UNKNOWN. |",
                      "| verify_fresh_full_junit | Exact fresh collection/JUnit bijection and on-disk mtime/hash checks. |",
                      "| verify_raw_accounting_and_worker_proofs | Recompute metrics, costs, statistics and bind worker hashes to raw rows. |",
                      "", "See source inventory for exact file paths and line numbers; raw large ledgers use JSONL.gz."]
        (run/"EXECUTIVE_CODE_FIX_AND_TRADING_EDGE_REPORT.md").write_bytes(("\n\n".join(report_lines)+"\n").encode("utf-8"))
        write(run/"00_run_manifest.json",{"version":VERSION,"run_id":runid,"tested_executable_sha":sha,"dataset_sha256":dataset_sha,"engineering":gates["engineering_verdict"],"prepublication_engineering":gates["prepublication_engineering"],"activation_ready":"NO","fresh_oos":"NOT_AVAILABLE","failure":failure})
        files={str(p.relative_to(run)).replace("\\","/"):hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file() and not p.name.endswith(".partial")}
        write(run/"artifact_sha256_manifest.json",{"files":files,"ab_specs":specs})
        independent=verify_v1793127_artifact_truth(run)
        write(run/"24_disk_integrity_check.json",independent)
        proofs["G24"]={"status":independent["status"],"source":"24_disk_integrity_check.json","measured":independent}
        gates=evaluate_v1793127_acceptance_gates(proofs,tested_sha=sha,dataset_sha=dataset_sha)
        report_lines[2]=f"Engineering: {gates['engineering_verdict']}; prepublication: {gates['prepublication_engineering']}."
        (run/"EXECUTIVE_CODE_FIX_AND_TRADING_EDGE_REPORT.md").write_bytes(("\n".join(report_lines)+"\n").encode("utf-8"))
        write(run/"00_run_manifest.json",{"version":VERSION,"run_id":runid,"tested_executable_sha":sha,"dataset_sha256":dataset_sha,"engineering":gates["engineering_verdict"],"prepublication_engineering":gates["prepublication_engineering"],"activation_ready":"NO","fresh_oos":"NOT_AVAILABLE","failure":failure})
        for gate in gates["gates"]:
            path=run/gate["source"] if gate["source"] else None
            gate["source_sha256"]=hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None
        write(run/"23_acceptance_gates_computed_from_proof.json",gates)
        files={str(p.relative_to(run)).replace("\\","/"):hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file() and p.name!="artifact_sha256_manifest.json" and not p.name.endswith(".partial")}
        write(run/"artifact_sha256_manifest.json",{"files":files,"ab_specs":specs})
        independent=verify_v1793127_artifact_truth(run)
        print(json.dumps({"run":str(run),"engineering":gates["engineering_verdict"],"artifact_verification":independent,"failure":failure}),flush=True)
    return 0 if gates["prepublication_engineering"]=="PASS" and independent["status"]=="PASS" else 1


if __name__=="__main__":
    raise SystemExit(main())
