"""Execute 28 real V17.9.3.1.2.9 RED/GREEN source mutants (M01..M28) in isolated temporary packages."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

APP = Path(__file__).resolve().parents[1]

SPECS = (
    (
        "M01",
        "si",
        "pool_proven_in_si5 = bool(",
        "pool_proven_in_si5 = (prop_tp is not None and bool(prop_target_id)) or bool(",
        "test_m01_fabricated_pool_from_prop_target_id_and_tp_rejected",
    ),
    (
        "M02",
        "si",
        "if not p_id or p_px_raw is None or isinstance(p_px_raw, bool) or type(k_ts_raw) is not int or not 0 < k_ts_raw <= now_ms:",
        "k_ts_raw = now_ms if k_ts_raw is None else k_ts_raw\n        if not p_id or p_px_raw is None or isinstance(p_px_raw, bool) or type(k_ts_raw) is not int or not 0 < k_ts_raw <= now_ms:",
        "test_m02_falsify_known_at_now_ms_without_confirming_bars_rejected",
    ),
    (
        "M03",
        "si",
        "if type(occ_raw) is not int or type(k_raw) is not int:",
        "k_raw = occ_raw if k_raw is None else k_raw\n        if type(occ_raw) is not int or type(k_raw) is not int:",
        "test_m03_missing_known_at_fallback_to_occurred_at_rejected",
    ),
    (
        "M04",
        "si",
        'if not fast_bars:\n        return (False, [(s_ms, n_ms)], "EMPTY_PRICE_PATH")',
        'if not fast_bars:\n        return (True, [], "COMPLETE")',
        "test_m04_empty_m1_price_path_treated_as_unswept_rejected",
    ),
    (
        "M05",
        "si",
        "if curr_c - prev_c != 60_000:\n            gaps.append((prev_c, curr_c - 60_000))",
        "if False:\n            gaps.append((prev_c, curr_c - 60_000))",
        "test_m05_interior_price_path_gap_treated_as_complete_rejected",
    ),
    (
        "M06",
        "si",
        "if is_swept_flag or swept_by_ts or swept_by_bars:",
        "if False:",
        "test_m06_observed_actual_sweep_before_decision_rejected",
    ),
    (
        "M07",
        "si",
        "if is_closed_flag is False:",
        "if False:",
        "test_m07_future_or_partial_source_bar_rejected",
    ),
    (
        "M08",
        "si",
        "if p_id in seen_pool_ids:\n            ambiguous_pool_ids.add(p_id)",
        "if False:\n            ambiguous_pool_ids.add(p_id)",
        "test_m08_duplicate_pool_id_with_conflicting_prices_rejected",
    ),
    (
        "M09",
        "causal",
        'if target_id == swing.get("id") or (isinstance(target_id, str) and target_id == f\'pool_{tf.upper()}_{swing.get("id")}\'):',
        'if tf == "5m":',
        "test_m09_selected_target_id_rebound_to_unrelated_nearby_pool_rejected",
    ),
    (
        "M10",
        "si",
        'if direction == "LONG" and p_px <= entry_price:\n            continue',
        "if False:\n            continue",
        "test_m10_wrong_side_target_rejected",
    ),
    (
        "M11",
        "causal",
        'if float(target) == price and (price > float(entry) if direction == "LONG" else price < float(entry)):',
        'if (price > float(entry) if direction == "LONG" else price < float(entry)):',
        "test_m11_incorrect_tick_tp_price_or_altered_selected_id_rejected",
    ),
    (
        "M12",
        "causal",
        'if snap.get("opportunity_id") != row.get("opportunity_id") or snap.get("decision_timestamp_ms") != row.get("decision_timestamp_ms"):',
        "if False:",
        "test_m12_target_source_event_from_different_episode_rejected",
    ),
    (
        "M13",
        "causal",
        'if decision is not None and feature.get("decision_geometry")!={k:decision.get(k) for k in GEOMETRY_FIELDS}:',
        "if False:",
        "test_m13_source_proof_stale_after_repricing_rejected",
    ),
    (
        "M14",
        "causal",
        '(float(stop) < s["price"] < float(entry) if direction == "LONG" else float(stop) > s["price"] > float(entry))',
        "True",
        "test_m14_wrong_protective_sl_side_or_inside_pivot_rejected",
    ),
    (
        "M15",
        "causal",
        'or quantity != risk["quantity"]):',
        "or False):",
        "test_m15_mutation_of_final_quantity_without_risk_recalculation_rejected",
    ),
    (
        "M16",
        "si",
        "target_reachable = bool(target_geometrically_feasible and target_pool_proven)",
        "target_reachable = bool(target_geometrically_feasible)",
        "test_m16_target_reachable_requires_verified_source_pool",
    ),
    (
        "M17",
        "causal",
        'if quote_kind == "REAL_PROVIDER_QUOTE":',
        "if False:",
        "test_m17_real_provider_quote_without_receipt_rejected",
    ),
    (
        "M18",
        "causal",
        'if cl != op + duration or cl > cutoff or _get(b, "is_closed") is not True:',
        'if cl != op + duration or _get(b, "is_closed") is not True:',
        "test_m18_future_h4_bar_excluded_from_htf_bias",
    ),
    (
        "M19",
        "causal",
        'fvg = next((f for f in reversed(candidates) if not selected_id or f["id"] == selected_id), None)',
        "fvg = next((f for f in reversed(candidates)), None)",
        "test_m19_unrelated_fvg_or_unicorn_breaker_source_rejected",
    ),
    (
        "M20",
        "causal",
        "if any(isinstance(t,bool) or not isinstance(t,int) or t<=0 for t in times) or not times[0]<=times[1]<=times[2]<=cutoff:",
        "if any(isinstance(t,bool) or not isinstance(t,int) or t<=0 for t in times):",
        "test_m20_decisive_source_timestamp_one_second_beyond_cutoff_rejected",
    ),
    (
        "M21",
        "proof",
        'if hashlib.sha256(xml_bytes).hexdigest()!=shard.get("xml_sha256"):',
        "if False:",
        "test_m21_altered_raw_junit_xml_rejected_by_verifier",
    ),
    (
        "M22",
        "ab",
        "if min(ent, ext)<0 or abs(gross-ent-ext-net)>.001:",
        "if False:",
        "test_m22_deleted_matched_pair_or_altered_fee_rejected",
    ),
    (
        "M23",
        "proof",
        'if key in seen:violations.append("DUPLICATE_CHECKPOINT_MODEL_DIRECTION")',
        'if False:violations.append("DUPLICATE_CHECKPOINT_MODEL_DIRECTION")',
        "test_m23_injected_repeated_or_missing_checkpoint_fails_census",
    ),
    (
        "M24",
        "proof",
        'if digest!=report["canonical_results_sha256"] or report["worker_count"]!=count or len(report["worker_pids_observed"])!=count:',
        "if False:",
        "test_m24_worker_1v2v4_parity_requires_independent_pids_and_matching_digests",
    ),
    (
        "M25",
        "proof",
        'status="PASS" if branch=="feature/v17-9-2-structural-sl-tp-from-qa" and local==remote and not code_diff and phase_b and phase_c==local else "BLOCKED"',
        'status="PASS"',
        "test_m25_remote_phase_receipt_blocked_before_phase_c",
    ),
    (
        "M26",
        "causal",
        'quote_kind = str(_get(context, "quote_source_kind", "UNKNOWN"))',
        'quote_kind = "REAL_PROVIDER_QUOTE"',
        "test_m26_synthetic_quote_to_provider_label_without_receipt_fails",
    ),
    (
        "M27",
        "causal",
        'source_hash = canonical_hash({"bars": series, "dataset": dataset_sha256, "cutoff": cutoff,',
        'source_hash = canonical_hash({"bars": {}, "dataset": dataset_sha256, "cutoff": cutoff,',
        "test_m27_mutate_data_source_after_cache_warmup_invalidates_cache",
    ),
    (
        "M28",
        "causal",
        'return (labels[0],labels) if labels else ("OTHER/UNKNOWN",["OTHER/UNKNOWN"])',
        'return ("NOISE_STOP_QUICK_LOSS", ["NOISE_STOP_QUICK_LOSS"])',
        "test_m28_quick_stop_or_rr_does_not_fabricate_loss_cause",
    ),
)


def run_v1793129_source_mutations(output_dir: Path, *, max_workers: int = 2) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    originals = {
        "si": APP / "backend/services/v17_9_3_1_2_2_signal_intelligence.py",
        "causal": APP / "backend/services/v17_9_3_1_2_7_causal_signal_truth.py",
        "ab": APP / "backend/services/v17_9_3_1_2_7_matched_ab.py",
        "proof": APP / "tools/v17_9_3_1_2_7_proof_checks.py",
    }
    pkg_for_kind = {
        "si": "services",
        "causal": "services",
        "ab": "services",
        "proof": "tools",
    }
    digests = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in originals.items()}
    test_src = APP / "backend/tests/test_v17_9_3_1_2_9_liquidity_provenance.py"

    def _run_one(spec_tuple):
        mid, kind, old, new, selector = spec_tuple
        with tempfile.TemporaryDirectory(prefix=f"aurum_v129_{mid}_") as temp_str:
            temp = Path(temp_str)
            services_pkg = temp / "services"
            tools_pkg = temp / "tools"
            services_pkg.mkdir()
            tools_pkg.mkdir()
            (services_pkg / "__init__.py").write_text(
                f"__path__.append({str(APP / 'backend/services')!r})\n", encoding="utf-8"
            )
            (tools_pkg / "__init__.py").write_text(
                f"__path__.append({str(APP / 'tools')!r})\n", encoding="utf-8"
            )
            test_file = temp / "test_v129_liquidity_provenance.py"
            shutil.copy2(test_src, test_file)

            for k_name, orig_path in originals.items():
                dest_dir = services_pkg if pkg_for_kind[k_name] == "services" else tools_pkg
                shutil.copyfile(orig_path, dest_dir / orig_path.name)

            target_dir = services_pkg if pkg_for_kind[kind] == "services" else tools_pkg
            target = target_dir / originals[kind].name
            source = target.read_text(encoding="utf-8")
            if old not in source:
                raise ValueError(f"MUTATION_TARGET_NOT_FOUND:{mid}:{old}")
            target.write_text(source.replace(old, new, 1), encoding="utf-8")
            red_source_sha = hashlib.sha256(target.read_bytes()).hexdigest()

            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(
                [str(temp), str(APP / "backend"), str(APP), env.get("PYTHONPATH", "")]
            )
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            cmd = [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                str(test_file),
                "--noconftest",
                "-q",
                "-k",
                selector,
            ]
            red_log = output_dir / f"V129_{mid}_red.log"
            red_xml = output_dir / f"V129_{mid}_red.xml"
            with red_log.open("w", encoding="utf-8") as stream:
                red_start = time.time_ns()
                red = subprocess.Popen(
                    cmd + [f"--junitxml={red_xml}"],
                    cwd=temp,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
                try:
                    red.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    red.terminate()
                    red.wait(timeout=15)
                    raise
                red_end = time.time_ns()

            shutil.copyfile(originals[kind], target)
            green_log = output_dir / f"V129_{mid}_green.log"
            green_xml = output_dir / f"V129_{mid}_green.xml"
            with green_log.open("w", encoding="utf-8") as stream:
                green_start = time.time_ns()
                green = subprocess.Popen(
                    cmd + [f"--junitxml={green_xml}"],
                    cwd=temp,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
                try:
                    green.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    green.terminate()
                    green.wait(timeout=15)
                    raise
                green_end = time.time_ns()

            red_cases = list(ET.parse(red_xml).getroot().iter("testcase"))
            green_cases = list(ET.parse(green_xml).getroot().iter("testcase"))
            red_failed = [
                c.get("name")
                for c in red_cases
                if c.find("failure") is not None or c.find("error") is not None
            ]
            executed_same = bool(green_cases) and sorted(c.get("name") for c in red_cases) == sorted(
                c.get("name") for c in green_cases
            )
            restored_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            return {
                "id": f"V129_{mid}",
                "source": str(originals[kind].relative_to(APP)).replace("\\", "/"),
                "target": old,
                "replacement": new,
                "selector": selector,
                "red_exit_code": red.returncode,
                "green_exit_code": green.returncode,
                "red_failed_test_names": red_failed,
                "same_selected_tests_executed": executed_same,
                "red_pid": red.pid,
                "green_pid": green.pid,
                "red_start_ns": red_start,
                "red_end_ns": red_end,
                "green_start_ns": green_start,
                "green_end_ns": green_end,
                "original_source_sha256": digests[kind],
                "red_source_sha256": red_source_sha,
                "restored_source_sha256": restored_sha,
                "red_log_sha256": hashlib.sha256(red_log.read_bytes()).hexdigest(),
                "green_log_sha256": hashlib.sha256(green_log.read_bytes()).hexdigest(),
                "red_xml_sha256": hashlib.sha256(red_xml.read_bytes()).hexdigest(),
                "green_xml_sha256": hashlib.sha256(green_xml.read_bytes()).hexdigest(),
            }

    eff_workers = max(1, min(3, int(max_workers)))
    with ThreadPoolExecutor(max_workers=eff_workers) as pool:
        rows = list(pool.map(_run_one, SPECS))

    unchanged = all(hashlib.sha256(p.read_bytes()).hexdigest() == digests[k] for k, p in originals.items())
    all_ok = unchanged and all(
        r["red_exit_code"] != 0
        and r["green_exit_code"] == 0
        and bool(r["red_failed_test_names"])
        and r["same_selected_tests_executed"]
        and r["restored_source_sha256"] == r["original_source_sha256"]
        and r["red_source_sha256"] != r["original_source_sha256"]
        for r in rows
    )
    return {
        "status": "PASS" if all_ok else "FAIL",
        "count": len(rows),
        "source_restored": unchanged,
        "mutations": rows,
    }


if __name__ == "__main__":
    import json

    with tempfile.TemporaryDirectory(prefix="v129_mut_check_") as td:
        res = run_v1793129_source_mutations(Path(td), max_workers=3)
        print(json.dumps({"status": res["status"], "count": res["count"], "source_restored": res["source_restored"]}))
        if res["status"] != "PASS":
            for m in res["mutations"]:
                if m["red_exit_code"] == 0 or m["green_exit_code"] != 0 or not m["red_failed_test_names"]:
                    print("FAILED MUTANT:", m)
            raise SystemExit(1)
