"""Run real source mutants in fresh disposable packages, never edit checkout."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

APP=Path(__file__).resolve().parents[1]
SPECS=(
 ("causal",'return "REJECTED_UNPROVEN:INVALID_OR_MISSING_PROOF_HASH"','return None'),
 ("causal",'if any(row.get(k) != v for k,v in snap.get("derived_values", ())):','if False:'),
 ("causal",'_get(b, "is_closed") is not True','False'),
 ("causal",'if cutoff <= 0:','if False:'),
 ("causal",'"UNKNOWN" if any(x is None for x in htf_known[:2])','"ALIGNED" if any(x is None for x in htf_known[:2])'),
 ("causal",'quote_kind = str(_get(context, "quote_source_kind", "UNKNOWN"))','quote_kind = "SYNTHETIC_RESEARCH_QUOTE"'),
 ("causal",'return (labels[0],labels) if labels else ("OTHER/UNKNOWN",["OTHER/UNKNOWN"])','return ("TARGET_ALREADY_SWEPT", ["TARGET_ALREADY_SWEPT"])'),
 ("ab",'if key in result or str(oid) in seen:','if False:'),
 ("ab",'or not source:', 'or False:'),
 ("ab",'if min(ent, ext)<0 or abs(gross-ent-ext-net)>.001:', 'if False:'),
 ("ab",'if actual!=claim:', 'if False:'),
 ("ab",'"IDENTICAL_ON_TESTED_DATA" if diff["changed_count"]==0 and all(r["net_usd_delta"]==0 for r in pairs)', '"DIFFERENT_ON_TESTED_DATA" if diff["changed_count"]==0 and all(r["net_usd_delta"]==0 for r in pairs)'),
)
SELECTORS=("test_injected_optimistic_feature_without_proof_rejected",
 "test_edit_flat_flag_invalidates_proof", "test_point_in_time_and_source_hash and partial",
 "test_missing_decision_timestamp_fails_closed", "test_missing_baseline_proof_remains_unknown_and_synthetic",
 "test_provider_label_without_source_receipt_not_promoted_to_real",
 "test_rr_and_quick_loss_do_not_invent_target_or_premature_cause",
 "test_matched_identity_and_accounting_negative_controls and duplicate",
 "test_raw_index_rejects_missing_source_before_matching",
 "test_matched_identity_and_accounting_negative_controls and changed_fee",
 "test_disk_verifier_rejects_deleted_pair_and_tampered_raw_pnl",
 "test_si8_si9_identity_reported_honestly")


def run_new_source_mutations(output_dir):
    output_dir.mkdir(parents=True,exist_ok=False)
    originals={"causal":APP/"backend/services/v17_9_3_1_2_7_causal_signal_truth.py",
               "ab":APP/"backend/services/v17_9_3_1_2_7_matched_ab.py"}
    digests={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in originals.items()}
    rows=[]
    with tempfile.TemporaryDirectory(prefix="aurum_v127_mutants_") as temp:
        temp=Path(temp)
        package=temp/"services"
        package.mkdir()
        (package/"__init__.py").write_text(f"__path__.append({str(APP/'backend/services')!r})\n",encoding="utf-8")
        test=temp/"test_causal_truth.py"
        shutil.copy2(APP/"backend/tests/test_v17_9_3_1_2_7_causal_truth.py",test)
        for n,(kind,old,new) in enumerate(SPECS,1):
            for p in originals.values(): shutil.copyfile(p,package/p.name)
            target=package/originals[kind].name
            source=target.read_text(encoding="utf-8")
            if old not in source: raise ValueError("MUTATION_TARGET_NOT_FOUND:"+old)
            target.write_text(source.replace(old,new,1),encoding="utf-8")
            red_source_sha=hashlib.sha256(target.read_bytes()).hexdigest()
            env=os.environ.copy()
            env["PYTHONPATH"]=os.pathsep.join([str(temp),str(APP/"backend"),str(APP),env.get("PYTHONPATH","")])
            env["PYTHONDONTWRITEBYTECODE"]="1"
            cmd=[sys.executable,"-B","-m","pytest",str(test),"--noconftest","-q","-k",SELECTORS[n-1]]
            log=output_dir/f"M{n:02d}_red.log"
            with log.open("w",encoding="utf-8") as stream:
                red_start=time.time_ns()
                red=subprocess.Popen(cmd+["--junitxml="+str(output_dir/f"M{n:02d}_red.xml")],cwd=temp,env=env,stdout=stream,stderr=subprocess.STDOUT)
                try:red.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    red.terminate();red.wait(timeout=15);raise
                red_end=time.time_ns()
            shutil.copyfile(originals[kind],target)
            with (output_dir/f"M{n:02d}_green.log").open("w",encoding="utf-8") as stream:
                green_start=time.time_ns()
                green=subprocess.Popen(cmd+["--junitxml="+str(output_dir/f"M{n:02d}_green.xml")],cwd=temp,env=env,stdout=stream,stderr=subprocess.STDOUT)
                try:green.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    green.terminate();green.wait(timeout=15);raise
                green_end=time.time_ns()
            red_cases=list(ET.parse(output_dir/f"M{n:02d}_red.xml").getroot().iter("testcase"))
            green_cases=list(ET.parse(output_dir/f"M{n:02d}_green.xml").getroot().iter("testcase"))
            red_failed=[c.get("name") for c in red_cases if c.find("failure") is not None]
            executed_same=bool(green_cases) and sorted(c.get("name") for c in red_cases)==sorted(c.get("name") for c in green_cases)
            rows.append({"id":f"NEW_M{n:02d}","source":str(originals[kind].relative_to(APP)),
                         "target":old,"red_exit_code":red.returncode,"green_exit_code":green.returncode,
                         "red_failed_test_names":red_failed,"same_selected_tests_executed":executed_same,
                         "red_pid":red.pid,"green_pid":green.pid,"red_start_ns":red_start,"red_end_ns":red_end,
                         "green_start_ns":green_start,"green_end_ns":green_end,
                         "red_source_sha256":red_source_sha,"restored_source_sha256":hashlib.sha256(target.read_bytes()).hexdigest(),
                         "red_log_sha256":hashlib.sha256(log.read_bytes()).hexdigest(),
                         "green_log_sha256":hashlib.sha256((output_dir/f"M{n:02d}_green.log").read_bytes()).hexdigest()})
    unchanged=all(hashlib.sha256(p.read_bytes()).hexdigest()==digests[k] for k,p in originals.items())
    return {"status":"PASS" if unchanged and all(r["red_exit_code"]==1 and r["green_exit_code"]==0 and r["red_failed_test_names"] and r["same_selected_tests_executed"] for r in rows) else "FAIL",
            "count":len(rows),"source_restored":unchanged,"mutations":rows}
