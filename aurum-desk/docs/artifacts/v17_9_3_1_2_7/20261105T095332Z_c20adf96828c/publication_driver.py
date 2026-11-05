"""Finish the explicitly authorized same-branch publication after a PASS only.

No source edits, force pushes, branch changes or execution activation.
The final self-referential Phase C SHA is observed in an external receipt.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

APP=Path(r"D:\build app-crypto\aurum-desk")
ROOT=APP.parent
WORK=Path(__file__).parent
SHA="c20adf96828ccc09eb007cc5bfab6dd3edc671ac"
BRANCH="feature/v17-9-2-structural-sl-tp-from-qa"
GIT=r"C:\Program Files\Git\mingw64\bin\git.exe"
DRIVER_BYTES=Path(__file__).read_bytes().replace(b"\r\n",b"\n")
sys.path[:0]=[str(APP/"backend"),str(APP)]
os.environ.update(GIT_TERMINAL_PROMPT="0",GCM_INTERACTIVE="never")


def read(path):return json.loads(path.read_text(encoding="utf-8"))


def write(path,value):
    path.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")


def file_hash(path):
    h=hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda:source.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()


def git(*args):
    return subprocess.check_output([GIT,*args],cwd=ROOT,text=True,stderr=subprocess.STDOUT,timeout=300).strip()


def require(value,message):
    if not value:raise RuntimeError(message)


def remote_sha():
    value=git("ls-remote","--heads","origin",BRANCH)
    require(bool(value),"MANDATORY_REMOTE_BRANCH_MISSING")
    return value.split()[0]


def rehash(run):
    manifest=read(run/"artifact_sha256_manifest.json")
    manifest["files"]={str(path.relative_to(run)).replace("\\","/"):file_hash(path)
                       for path in run.rglob("*") if path.is_file()
                       and path.name!="artifact_sha256_manifest.json" and not path.name.endswith(".partial")}
    write(run/"artifact_sha256_manifest.json",manifest)


def assert_staged_bundle_bytes(run,attested_manifest_sha):
    """Git must publish the exact attested bytes, including original JUnit/log EOLs."""
    relative=str(run.relative_to(ROOT)).replace("\\","/")
    changed=subprocess.check_output([GIT,"diff","--cached","--name-only","-z"],cwd=ROOT).split(b"\0")
    require(all(not name or name.decode("utf-8").startswith(relative+"/") for name in changed),"UNEXPECTED_STAGED_USER_FILES")
    manifest=read(run/"artifact_sha256_manifest.json")
    manifest_digest=file_hash(run/"artifact_sha256_manifest.json")
    require(manifest_digest==attested_manifest_sha,"MANIFEST_CHANGED_AFTER_INDEPENDENT_VERIFICATION")
    staged=subprocess.check_output([GIT,"ls-files","--stage","-z","--",relative],cwd=ROOT)
    expected_blobs={}
    for entry in staged.split(b"\0"):
        if not entry:continue
        metadata,encoded_path=entry.split(b"\t",1)
        path=ROOT/encoded_path.decode("utf-8")
        rel=str(path.relative_to(run)).replace("\\","/")
        expected=manifest_digest if rel=="artifact_sha256_manifest.json" else manifest["files"][rel]
        sha256=hashlib.sha256()
        blob=hashlib.sha1()
        blob.update(("blob "+str(path.stat().st_size)+"\0").encode("ascii"))
        with path.open("rb") as source:
            for chunk in iter(lambda:source.read(1024*1024),b""):
                sha256.update(chunk);blob.update(chunk)
        require(sha256.hexdigest()==expected,"ARTIFACT_CHANGED_BEFORE_COMMIT:"+rel)
        require(blob.hexdigest()==metadata.split()[1].decode("ascii"),"GIT_FILTER_CHANGED_ATTESTED_BYTES:"+rel)
        expected_blobs[encoded_path.decode("utf-8")]=blob.hexdigest()
    expected_paths={relative+"/"+name for name in manifest["files"]}|{relative+"/artifact_sha256_manifest.json"}
    require(set(expected_blobs)==expected_paths,"MANIFEST_REFERENCES_FILES_NOT_STAGED_FOR_PUBLICATION")
    return expected_blobs


def stage_manifest_files(run):
    manifest=read(run/"artifact_sha256_manifest.json")
    relative=str(run.relative_to(ROOT)).replace("\\","/")
    names=[relative+"/"+name for name in manifest["files"]]+[relative+"/artifact_sha256_manifest.json"]
    # Captured *.log evidence can be globally ignored; include exactly manifest
    # paths. This is force-add of owned evidence, never a force-push.
    subprocess.run([GIT,"--literal-pathspecs","add","-f","--pathspec-from-file=-","--pathspec-file-nul"],
                   cwd=ROOT,input=("\0".join(names)+"\0").encode("utf-8"),
                   stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=True,timeout=300)


def assert_committed_bundle(run,expected_blobs):
    tree=subprocess.check_output([GIT,"ls-tree","-r","-z","HEAD","--",str(run.relative_to(ROOT))],cwd=ROOT)
    actual={}
    for entry in tree.split(b"\0"):
        if not entry:continue
        metadata,name=entry.split(b"\t",1)
        actual[name.decode("utf-8")]=metadata.split()[2].decode("ascii")
    require(actual==expected_blobs,"COMMITTED_ARTIFACT_BYTES_DIFFER_FROM_VERIFIED_BUNDLE")


def wait_for_job(queue_path,output):
    deadline=time.monotonic()+8*3600
    while time.monotonic()<deadline:
        queue=read(queue_path)
        state=queue["status"]
        if state=="VERIFICATION_FINISHED":
            require(queue.get("exit_code")==0,"VERIFICATION_DID_NOT_PASS; see "+queue["log"])
            return queue
        require(state in ("WAITING_FOR_RAM","RUNNING_VERIFICATION"),"QUEUE_STOPPED:"+state)
        import psutil
        try:
            process=psutil.Process(queue["queue_pid"])
            require(any("queue_v127_full_verification.py" in word for word in process.cmdline()),"QUEUE_PID_OWNERSHIP_CHANGED")
        except psutil.NoSuchProcess:
            raise RuntimeError("QUEUE_OWNER_EXITED_WITHOUT_FINAL_RESULT")
        write(output,{"status":"WAITING_FOR_VERIFICATION","queue":str(queue_path),"queue_state":state,"tested_executable_sha":SHA})
        time.sleep(10)
    raise RuntimeError("WAITING_FOR_VERIFICATION_TIMEOUT")


def publish(queue_path,output):
    queue=wait_for_job(queue_path,output)
    candidates=sorted((APP/"docs/artifacts/v17_9_3_1_2_7").glob("*_"+SHA[:12]))
    require(bool(candidates),"NO_FROZEN_RUN_DIRECTORY")
    run=candidates[-1]
    manifest=read(run/"00_run_manifest.json")
    require(manifest.get("tested_executable_sha")==SHA,"RUN_SOURCE_SHA_MISMATCH")
    require(manifest.get("prepublication_engineering")=="PASS" and not manifest.get("failure"),"RUN_PREPUBLICATION_NOT_PASS")
    require(git("branch","--show-current")==BRANCH and git("rev-parse","HEAD")==SHA,"BRANCH_OR_HEAD_CHANGED")
    require(not git("diff","--name-only","HEAD","--","aurum-desk/backend","aurum-desk/tools"),"FROZEN_SOURCE_DIRTY")
    git("diff","--cached","--quiet")
    require(git("remote","get-url","origin")=="https://github.com/quocan050302/Build-app-crypto.git","ORIGIN_CHANGED")

    from tools.run_v17_9_3_1_2_7_verification import ResourceMonitor,verify_v1793127_artifact_truth
    from tools.v17_9_3_1_2_7_proof_checks import verify_fresh_full_junit,verify_git_remote_phase_receipt_v1793127
    monitor=ResourceMonitor()
    monitor.abort_path=output.with_suffix(".resource_abort.json")
    os.environ["AURUM_RESOURCE_GUARD_ABORT_FILE"]=str(monitor.abort_path)
    monitor.start()
    try:
        write(output,{"status":"VERIFYING_ARTIFACTS","run":str(run),"tested_executable_sha":SHA})
        initial=verify_v1793127_artifact_truth(run)
        require(initial["status"]=="PASS","INITIAL_DISK_PROOF_FAILED:"+str(initial))
        monitor.check()
        module_path=WORK/"supplement_v127_evidence.py"
        spec=importlib.util.spec_from_file_location("v127_publication_supplement",module_path)
        supplement=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(supplement)
        supplement.supplement(run)
        (run/"publication_driver.py").write_bytes(DRIVER_BYTES)
        copied=run/"supplement_v127_evidence.py"
        copied.write_bytes(copied.read_bytes().replace(b"\r\n",b"\n"))
        # EOL conversion would invalidate SHA256 attestations of logs and JUnit.
        (run/".gitattributes").write_bytes(b"* -text\n")
        junit=verify_fresh_full_junit(run,read(run/"17_fresh_full_pytest_collection_node_ids_and_hash.json"),read(run/"18_full_fresh_junit_shard_bijection_and_exitcodes.json"))
        require(junit["status"]=="PASS","POST_SUPPLEMENT_JUNIT_FAILED")
        monitor.check()

        stats=read(run/"24_engineering_strategy_activation_dual_verdict.json")
        metrics=read(run/"15_all_policies_all_three_fill_modes_metrics.json")
        report=run/"EXECUTIVE_CODE_FIX_AND_TRADING_EDGE_REPORT.md"
        lines=["", "## Actual completed execution", "",
               "Command: backend/venv/Scripts/python.exe tools/run_v17_9_3_1_2_7_verification.py --tested-code-sha "+SHA,
               f"Fresh JUnit: executed {junit['executed']}, passed {junit['passed']}, failures {junit['failures']}, errors {junit['errors']}, skipped {len(junit['skipped'])}.",
               "Mutation proof: 16 previous and 12 new executable RED/GREEN controls; see the two 19_* evidence files.",
               "", "| Policy | Fill mode | Closed | Wins | Losses | Net USD | Expectancy R | Open | Gap |",
               "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
        for policy,modes in sorted(metrics.items()):
            for mode,row in sorted(modes.items()):
                values=[policy,mode,row.get("closed_trades_count"),row.get("wins_count"),row.get("losses_count"),row.get("total_realized_net_usd"),row.get("expectancy_net_r"),row.get("open_at_horizon_count"),row.get("data_gap_unresolved_count")]
                lines.append("| "+" | ".join(str(value) for value in values)+" |")
        lines.extend(["", "Strategy verdict: "+str(stats["strategy_statistical_verdict"])+". Activation: "+str(stats["activation_ready"])+".",
                      "Quote assumption: synthetic close +/- USD 0.10. Previously exposed October history is not fresh untouched OOS.",
                      "Original failed attempts remain preserved locally; only this completed frozen execution is certified here.",
                      "Final Phase C SHA is recorded externally after its push; immutable artifacts here retain their prepublication verdict."])
        report.write_text(report.read_text(encoding="utf-8")+"\n".join(lines)+"\n",encoding="utf-8",newline="\n")
        rehash(run)
        proof=verify_v1793127_artifact_truth(run)
        require(proof["status"]=="PASS","SUPPLEMENTED_DISK_PROOF_FAILED:"+str(proof))
        write(run/"26_post_supplement_independent_verification.json",{"disk":proof,"junit":{k:v for k,v in junit.items() if k!="passed_node_ids"},"tested_executable_sha":SHA,"note":"This check precedes inclusion of its own attestation; the final manifest is rehashed and checked again."})
        rehash(run)
        final_proof=verify_v1793127_artifact_truth(run)
        require(final_proof["status"]=="PASS","FINAL_DISK_PROOF_FAILED:"+str(final_proof))
        attested_manifest_sha=file_hash(run/"artifact_sha256_manifest.json")
        monitor.check()
        files=[path for path in run.rglob("*") if path.is_file()]
        require(not any(path.stat().st_size>=95*1024**2 for path in files),"LARGE_ARTIFACT_REQUIRES_REVIEW")
        require(sum(path.stat().st_size for path in files)<300*1024**2,"PUBLICATION_BUNDLE_REQUIRES_SIZE_REVIEW")
        observed_before=remote_sha()
        git("merge-base","--is-ancestor",observed_before,SHA)
        require(git("rev-parse","HEAD")==SHA,"HEAD_CHANGED_BEFORE_EVIDENCE_COMMIT")
        git("diff","--cached","--quiet")
        stage_manifest_files(run)
        expected_blobs=assert_staged_bundle_bytes(run,attested_manifest_sha)
        git("commit","-m","docs(v17.9.3.1.2.7): publish source-grounded AB and regression evidence for "+SHA[:12])
        phase_b=git("rev-parse","HEAD")
        assert_committed_bundle(run,expected_blobs)
        require(not git("diff","--name-only",SHA,"HEAD","--","aurum-desk/backend","aurum-desk/tools"),"COMMIT_HOOK_CHANGED_FROZEN_SOURCE")
        require(not git("diff","--name-only","HEAD","--","aurum-desk/backend","aurum-desk/tools"),"WORKING_SOURCE_CHANGED")
        monitor.check()
        write(output,{"status":"PUSHING_PHASE_B","phase_a":SHA,"phase_b":phase_b,"run":str(run)})
        git("push","origin","HEAD:refs/heads/"+BRANCH)
        observed_b=remote_sha()
        require(observed_b==phase_b,"PHASE_B_REMOTE_SHA_MISMATCH")
        write(output,{"status":"PHASE_B_PUSH_VERIFIED","phase_a":SHA,"phase_b":phase_b,"observed_remote":observed_b,"run":str(run)})
        require(not git("diff","--name-only",SHA,"HEAD","--","aurum-desk/backend","aurum-desk/tools"),"SOURCE_CHANGED_AFTER_TESTING")
        receipt=run.parent/("publication_"+run.name+".json")
        write(receipt,{"phase_a_tested_executable_sha":SHA,"phase_b_evidence_sha":phase_b,"observed_remote_after_phase_b":observed_b,
                       "branch":BRANCH,"run":run.name,"phase_b_push_verdict":"PASS","manifest_sha256":file_hash(run/"artifact_sha256_manifest.json"),
                       "phase_c_note":"This receipt is committed in Phase C. The final Phase C local/remote equality is observed externally after that push to avoid a self-referential commit hash."})
        latest=run.parent/"latest_run.json"
        write(latest,{"run_id":run.name,"tested_executable_sha":SHA,"branch":BRANCH,
                      "prepublication_engineering":manifest["prepublication_engineering"],"strategy_statistical_verdict":stats["strategy_statistical_verdict"],
                      "activation_ready":stats["activation_ready"],"publication_receipt":receipt.name,
                      "final_remote_check":"OBSERVED_EXTERNALLY_AFTER_PHASE_C_PUSH"})
        git("add","-f","--",str(receipt.relative_to(ROOT)),str(latest.relative_to(ROOT)))
        git("commit","-m","docs(v17.9.3.1.2.7): record verified phase B push and frozen-source provenance")
        phase_c=git("rev-parse","HEAD")
        monitor.check()
        assert_committed_bundle(run,expected_blobs)
        require(not git("diff","--name-only",SHA,"HEAD","--","aurum-desk/backend","aurum-desk/tools"),"PHASE_C_CHANGED_FROZEN_SOURCE")
        require(not git("diff","--name-only","HEAD","--","aurum-desk/backend","aurum-desk/tools"),"WORKING_SOURCE_CHANGED_BEFORE_FINAL_PUSH")
        write(output,{"status":"PUSHING_PHASE_C","phase_a":SHA,"phase_b":phase_b,"phase_c":phase_c,"phase_b_remote_verified":observed_b})
        git("push","origin","HEAD:refs/heads/"+BRANCH)
        final_receipt=verify_git_remote_phase_receipt_v1793127(ROOT,phase_a=SHA,phase_b=phase_b,phase_c=phase_c)
        require(final_receipt["status"]=="PASS","FINAL_REMOTE_PHASE_RECEIPT_FAILED")
        gates=read(run/"23_acceptance_gates_computed_from_proof.json")
        final_gates=[]
        for original in gates["gates"]:
            gate=dict(original)
            if gate["gate_id"]=="G25":
                gate.update(status=final_receipt["status"],measured=final_receipt,source="Externally observed Git remote receipt; no self-referential file hash",source_sha256=None)
            final_gates.append(gate)
        require(all(gate["status"]=="PASS" for gate in final_gates),"FINAL_ACCEPTANCE_GATE_BLOCKED")
        monitor.check()
        result={"status":"COMPLETE","phase_a":SHA,"phase_b":phase_b,"phase_c":phase_c,"branch":BRANCH,"run":str(run),
                "git_receipt":final_receipt,"engineering":"PASS","strategy":stats["strategy_statistical_verdict"],"activation":stats["activation_ready"],
                "fresh_tests":{k:v for k,v in junit.items() if k!="passed_node_ids"},"final_gates":final_gates,
                "evidence_manifest_sha256":file_hash(run/"artifact_sha256_manifest.json"),"independent_disk_verification":final_proof,
                "note":"This external final receipt was written after the Phase C push and is not embedded into its own Git commit."}
        return result
    finally:
        resource=monitor.stop()
        write(output.with_suffix(".resources.json"),resource)
        if resource["error"]:raise RuntimeError("PUBLICATION_RESOURCE_GUARD:"+resource["error"])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--queue-status",type=Path,required=True)
    args=parser.parse_args()
    output=args.queue_status.with_name(args.queue_status.stem+"_final_receipt.json")
    try:
        result=publish(args.queue_status,output)
    except Exception as exc:
        last_state=read(output) if output.is_file() else None
        detail=exc.output[-2000:] if isinstance(exc,subprocess.CalledProcessError) and isinstance(exc.output,str) else None
        result={"status":"BLOCKED","error_type":type(exc).__name__,"error":str(exc),"detail":detail,"tested_executable_sha":SHA,"queue":str(args.queue_status),"last_state":last_state}
    write(output,result)
    print(json.dumps({key:value for key,value in result.items() if key not in ("final_gates","fresh_tests")}),flush=True)
    return 0 if result["status"]=="COMPLETE" else 1


if __name__=="__main__":raise SystemExit(main())
