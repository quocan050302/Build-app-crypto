"""
aurum-desk/backend/services/kh_03_r3_c1_worker_policy_service.py

AURUM DESK — KH-03.R3.C1 | WORKER BENCHMARK TRUTH, STRICT SCHEMA PARSER & SAFE CAP POLICY
SPEC VERSION: KH-03.R3.C1-v1.0 | ENGINEERING & SCIENTIFIC HONESTY CLOSURE | STDLIB ONLY

Pure, deterministic, fail-closed benchmark timing parser, semantic-equivalence validator,
measured-vs-applied worker policy selector, and independent raw-receipt recomputer.

Closes the verified KH-03.R3 P1 defect where `run_kh_03_r3_acceptance._compute_optimum_worker_policy`
only inspected (`elapsed_sec`, `wall_sec`, `duration_sec`) and ignored the actual producer keys:
  - Controlled pytest subset (`pytest_controlled_subset`): `wall_clock_seconds`
  - 8,000-bar R2 science slice (`r2_science_slice`):       `wall_seconds`
which caused sealed R3 artifact `19` to publish empty `mean_wall_sec_by_mode={}` and default `w=1`.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH03_R3_C1 = "KH-03.R3.C1-v1.0"
SCHEMA_VERSION_KH03_R3_C1 = "kh_03_r3_c1_worker_policy_v1"

BENCHMARK_KIND_PYTEST = "pytest_controlled_subset"
BENCHMARK_KIND_SCIENCE = "r2_science_slice"
VALID_BENCHMARK_KINDS: Tuple[str, ...] = (BENCHMARK_KIND_PYTEST, BENCHMARK_KIND_SCIENCE)

CANONICAL_DURATION_KEY_BY_KIND: Dict[str, str] = {
    BENCHMARK_KIND_PYTEST: "wall_clock_seconds",
    BENCHMARK_KIND_SCIENCE: "wall_seconds",
}
LEGACY_DURATION_KEYS: Tuple[str, ...] = ("elapsed_sec", "wall_sec", "duration_sec")

PYTEST_EXCLUSIVE_KEYS: Tuple[str, ...] = (
    "wall_clock_seconds",
    "canonical_sorted_outcome_sha256",
    "executed_tests_count",
)
SCIENCE_EXCLUSIVE_KEYS: Tuple[str, ...] = (
    "wall_seconds",
    "canonical_sorted_sha256",
    "rows_count",
    "evaluated_bars_count",
    "chunks_count",
)

REQUIRED_MODES_WORKERS: Dict[str, int] = {
    "w=1": 1,
    "w=2": 2,
    "w=4": 4,
    "w=1_repeat": 1,
}
CANDIDATE_MODES_ORDER: Tuple[str, ...] = ("w=1", "w=2", "w=4")

MAX_SANE_DURATION_SECONDS: float = 86_400.0
GLOBAL_MAX_CHILD_PROCESSES_CAP: int = 4
DEFAULT_FULL_SUITE_PYTEST_SAFE_CAP: int = 3
DEFAULT_SCIENCE_SAFE_CAP: int = 4
STRICT_PREFLIGHT_DISK_FLOOR_GIB: float = 20.5
STRICT_RUNTIME_DISK_FLOOR_GIB: float = 20.0
STRICT_RAM_FLOOR_MIB: float = 512.0
DEFAULT_MIN_MEANINGFUL_GAIN_PCT: float = 0.05
DEFAULT_NOISY_REPEAT_VARIANCE_PCT: float = 0.05


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _is_strict_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_sha256_hex(val: Any) -> bool:
    if not isinstance(val, str) or len(val) != 64:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in val)


def parse_json_strict_no_nonfinite(raw_text: str) -> Any:
    """Parse JSON while rejecting nonstandard NaN, Infinity, and -Infinity constants."""

    def _reject_constant(token: str) -> Any:
        raise ValueError(f"NONSTANDARD_JSON_CONSTANT_FORBIDDEN:{token}")

    return json.loads(raw_text, parse_constant=_reject_constant)


def legacy_buggy_compute_optimum_worker_policy_r3_red_repro(
    bench_doc: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    EXACT verbatim reproduction of KH-03.R3 `run_kh_03_r3_acceptance.py` lines 581-591.
    FOR RED-FIXTURE DEFECT REPRODUCTION ONLY — NEVER USE FOR PRODUCTION OR POLICY DECISIONS.
    """
    runs = list(bench_doc.get("runs") or ()) if isinstance(bench_doc, Mapping) else []
    by_mode: Dict[str, List[float]] = {}
    for r in runs:
        if not isinstance(r, Mapping):
            continue
        ml = str(r.get("mode_label") or "")
        t = (
            r.get("elapsed_sec")
            if r.get("elapsed_sec") is not None
            else (r.get("wall_sec") if r.get("wall_sec") is not None else r.get("duration_sec"))
        )
        if t is not None and ml in ("w=1", "w=2", "w=4"):
            by_mode.setdefault(ml, []).append(float(t))
    means = {m: round(sum(v) / len(v), 4) for m, v in by_mode.items() if v}
    opt = min(means.items(), key=lambda kv: kv[1])[0] if means else "w=1"
    return {
        "mean_wall_sec_by_mode": means,
        "optimum_worker_policy": opt,
        "repro_role": "RED_REPRO_OLD_R3_BUG_ONLY",
    }


def infer_benchmark_kind_strict(
    bench_doc: Mapping[str, Any],
) -> Tuple[Optional[str], List[str]]:
    """
    Strictly infer benchmark kind for single-arg backward-compat callers.
    Fails closed when pytest and science keys are mixed or schema is unidentifiable.
    """
    if not isinstance(bench_doc, Mapping):
        return None, ["BENCH_DOC_NOT_MAPPING"]
    explicit = bench_doc.get("benchmark_kind")
    if explicit is not None:
        if explicit in VALID_BENCHMARK_KINDS:
            return str(explicit), []
        return None, [f"UNKNOWN_EXPLICIT_BENCHMARK_KIND:{explicit}"]

    runs = bench_doc.get("runs")
    if not isinstance(runs, list) or len(runs) == 0:
        return None, ["BENCH_DOC_RUNS_MISSING_OR_EMPTY"]

    has_pytest_sig = False
    has_science_sig = False
    for row in runs:
        if not isinstance(row, Mapping):
            continue
        if any(k in row for k in PYTEST_EXCLUSIVE_KEYS):
            has_pytest_sig = True
        if any(k in row for k in SCIENCE_EXCLUSIVE_KEYS):
            has_science_sig = True

    if has_pytest_sig and has_science_sig:
        return None, ["AMBIGUOUS_MIXED_PYTEST_AND_SCIENCE_KEYS"]
    if has_pytest_sig:
        return BENCHMARK_KIND_PYTEST, []
    if has_science_sig:
        return BENCHMARK_KIND_SCIENCE, []
    return None, ["UNIDENTIFIED_BENCHMARK_KIND_NO_CANONICAL_SIGNATURE"]


def parse_benchmark_run_duration_strict(
    run: Mapping[str, Any],
    *,
    benchmark_kind: str,
    allow_legacy_duration_keys: bool = True,
) -> Dict[str, Any]:
    """
    Strict parser for a single RAW benchmark run row.
    Accepts canonical kind-specific key (`wall_clock_seconds` for pytest, `wall_seconds` for science),
    allows documented legacy keys (`elapsed_sec`, `wall_sec`, `duration_sec`) ONLY when unambiguous,
    rejects conflicting simultaneous non-equal keys, cross-kind keys, bool/str/NaN/Inf/<=0/huge values,
    bad `mode_label`/`workers` pairings, and incompatible `worker_pids`.
    """
    violations: List[str] = []

    if benchmark_kind not in VALID_BENCHMARK_KINDS:
        return {
            "valid": False,
            "benchmark_kind": benchmark_kind,
            "mode_label": None,
            "workers": None,
            "duration_sec": None,
            "duration_source_key": None,
            "input_row_sha256": "",
            "provenance_status": "INVALID_BENCHMARK_KIND",
            "worker_pids_validated": False,
            "violations": [f"UNKNOWN_BENCHMARK_KIND:{benchmark_kind}"],
        }

    if not isinstance(run, Mapping):
        return {
            "valid": False,
            "benchmark_kind": benchmark_kind,
            "mode_label": None,
            "workers": None,
            "duration_sec": None,
            "duration_source_key": None,
            "input_row_sha256": "",
            "provenance_status": "INVALID_ROW_TYPE",
            "worker_pids_validated": False,
            "violations": ["RUN_ROW_NOT_MAPPING"],
        }

    # Safe canonical hash even when row contains NaN/Inf (fallback to repr if non-finite)
    try:
        row_sha = canonical_hash(dict(run))
    except Exception:
        row_sha = canonical_hash({"nonfinite_row_repr": repr(sorted((str(k), repr(v)) for k, v in run.items()))})

    # 1. Cross-kind key contamination check (P11)
    forbidden_cross_keys = SCIENCE_EXCLUSIVE_KEYS if benchmark_kind == BENCHMARK_KIND_PYTEST else PYTEST_EXCLUSIVE_KEYS
    present_cross = [k for k in forbidden_cross_keys if k in run]
    if present_cross:
        violations.append(f"CROSS_KIND_SCHEMA_CONTAMINATION:{benchmark_kind}:{','.join(sorted(present_cross))}")

    # 2. mode_label & workers pairing check (P12)
    raw_ml = run.get("mode_label")
    mode_label = raw_ml if isinstance(raw_ml, str) else None
    if mode_label not in REQUIRED_MODES_WORKERS:
        violations.append(f"INVALID_MODE_LABEL:{raw_ml!r}")

    raw_workers = run.get("workers")
    workers_val: Optional[int] = None
    if not _is_strict_int(raw_workers):
        violations.append(f"INVALID_WORKERS_TYPE:{type(raw_workers).__name__}:{raw_workers!r}")
    else:
        workers_val = int(raw_workers)
        if mode_label in REQUIRED_MODES_WORKERS and workers_val != REQUIRED_MODES_WORKERS[mode_label]:
            violations.append(
                f"MODE_LABEL_WORKERS_MISMATCH:mode={mode_label}:workers={workers_val}:expected={REQUIRED_MODES_WORKERS[mode_label]}"
            )

    # 3. Worker PIDs / chunk identity validation (P13; never invent OS PIDs)
    worker_pids_validated = False
    if "worker_pids" in run:
        pids_raw = run.get("worker_pids")
        if not isinstance(pids_raw, (list, tuple)):
            violations.append(f"INVALID_WORKER_PIDS_TYPE:{type(pids_raw).__name__}")
        else:
            bad_entries = [p for p in pids_raw if not _is_strict_int(p) or int(p) <= 0]
            if bad_entries:
                violations.append(f"INVALID_WORKER_PIDS_ENTRIES:{bad_entries!r}")
            elif workers_val is not None:
                if len(pids_raw) != workers_val:
                    violations.append(
                        f"INCOMPATIBLE_WORKER_PIDS_COUNT:pids_len={len(pids_raw)}!=workers={workers_val}"
                    )
                elif len(set(int(p) for p in pids_raw)) != workers_val:
                    violations.append(
                        f"DUPLICATE_WORKER_PIDS_FOR_MULTIWORKER:unique={len(set(int(p) for p in pids_raw))}!=workers={workers_val}"
                    )
                else:
                    worker_pids_validated = True
    elif benchmark_kind == BENCHMARK_KIND_SCIENCE:
        # Science producer records chunks_count rather than worker_pids (never invent PIDs!)
        if "chunks_count" in run:
            cc = run.get("chunks_count")
            if not _is_strict_int(cc) or int(cc) <= 0:
                violations.append(f"INVALID_CHUNKS_COUNT:{cc!r}")
            elif workers_val is not None and int(cc) != workers_val:
                violations.append(f"CHUNKS_COUNT_WORKERS_MISMATCH:chunks={cc}!=workers={workers_val}")
            else:
                worker_pids_validated = True

    # 4. Strict duration field parsing & conflict rejection (P07, P08, P09, P10)
    canonical_key = CANONICAL_DURATION_KEY_BY_KIND[benchmark_kind]
    candidate_keys: List[str] = [canonical_key] + (list(LEGACY_DURATION_KEYS) if allow_legacy_duration_keys else [])
    present_keys = [k for k in candidate_keys if k in run]

    parsed_durations: Dict[str, float] = {}
    for k in present_keys:
        v = run.get(k)
        if v is None:
            violations.append(f"DURATION_KEY_NULL:{k}")
            continue
        if isinstance(v, bool):
            violations.append(f"DURATION_BOOL_FORBIDDEN:{k}:{v!r}")
            continue
        if not _is_strict_number(v):
            violations.append(f"DURATION_NON_NUMERIC_TYPE:{k}:{type(v).__name__}:{v!r}")
            continue
        fv = float(v)
        if not math.isfinite(fv):
            violations.append(f"DURATION_NON_FINITE:{k}:{v!r}")
            continue
        if fv <= 0.0:
            violations.append(f"DURATION_NON_POSITIVE:{k}:{fv}")
            continue
        if fv > MAX_SANE_DURATION_SECONDS:
            violations.append(f"DURATION_EXCEEDS_UPPER_BOUND:{k}:{fv}>{MAX_SANE_DURATION_SECONDS}")
            continue
        parsed_durations[k] = fv

    duration_sec: Optional[float] = None
    duration_source_key: Optional[str] = None
    provenance_status = "INVALID_MISSING_DURATION"

    if not present_keys:
        violations.append(f"MISSING_DURATION_FIELD:expected={canonical_key}")
    elif parsed_durations:
        # Reject conflicting simultaneous non-equal duration keys (P10)
        distinct_keys = list(parsed_durations.keys())
        if len(distinct_keys) > 1:
            ref_k = distinct_keys[0]
            ref_v = parsed_durations[ref_k]
            conflicts = [
                f"{ref_k}={ref_v}!={ok}={parsed_durations[ok]}"
                for ok in distinct_keys[1:]
                if abs(parsed_durations[ok] - ref_v) > 1e-9
            ]
            if conflicts:
                violations.append(f"CONFLICTING_DURATION_KEYS:{';'.join(conflicts)}")

        if canonical_key in parsed_durations:
            duration_source_key = canonical_key
            duration_sec = round(parsed_durations[canonical_key], 6)
            provenance_status = "CANONICAL_SCHEMA_DURATION_KEY"
        else:
            for lk in LEGACY_DURATION_KEYS:
                if lk in parsed_durations:
                    duration_source_key = lk
                    duration_sec = round(parsed_durations[lk], 6)
                    provenance_status = "LEGACY_SCHEMA_DURATION_KEY"
                    break

    is_valid = len(violations) == 0 and duration_sec is not None and mode_label is not None and workers_val is not None
    if not is_valid and provenance_status.endswith("DURATION_KEY"):
        provenance_status = "INVALID_ROW_SCHEMA"

    return {
        "valid": is_valid,
        "benchmark_kind": benchmark_kind,
        "mode_label": mode_label,
        "workers": workers_val,
        "duration_sec": duration_sec,
        "duration_source_key": duration_source_key,
        "input_row_sha256": row_sha,
        "provenance_status": provenance_status,
        "worker_pids_validated": worker_pids_validated,
        "violations": violations,
    }


def validate_benchmark_semantic_equivalence_strict(
    bench_doc: Mapping[str, Any],
    *,
    benchmark_kind: str,
    expected_run_id: Optional[str] = None,
    expected_tested_sha: Optional[str] = None,
    expected_dataset_sha: Optional[str] = None,
    is_historical_reference: bool = False,
    allow_legacy_duration_keys: bool = True,
) -> Dict[str, Any]:
    """
    Validate a full 4-run (w=1, w=2, w=4, w=1_repeat) benchmark receipt:
      - Exact presence of all 4 modes (no silent drop of w=4 or w=1_repeat).
      - No duplicate or incompatible mode rows.
      - No mixed pytest/science key contamination across runs.
      - Positive consistent item count (`executed_tests_count` or `rows_count`) across all 4 runs.
      - Identical 64-hex canonical semantic SHA across all 4 runs (including `w=1_repeat`).
      - Provenance binding (`run_id`, `tested_executable_sha`, `canonical_dataset_sha256`) when attached or expected.
      - Rejects stale/cached/reused markers.
    """
    violations: List[str] = []

    if benchmark_kind not in VALID_BENCHMARK_KINDS:
        return {
            "status": "BLOCKED_INVALID_BENCHMARK",
            "valid": False,
            "benchmark_kind": benchmark_kind,
            "provenance_role": "HISTORICAL_REFERENCE_ONLY" if is_historical_reference else "FRESH_EXECUTION",
            "parsed_runs_by_mode": {},
            "canonical_semantic_sha256": None,
            "semantic_item_count": 0,
            "input_doc_sha256": "",
            "violations": [f"UNKNOWN_BENCHMARK_KIND:{benchmark_kind}"],
        }

    if not isinstance(bench_doc, Mapping):
        return {
            "status": "BLOCKED_INVALID_BENCHMARK",
            "valid": False,
            "benchmark_kind": benchmark_kind,
            "provenance_role": "HISTORICAL_REFERENCE_ONLY" if is_historical_reference else "FRESH_EXECUTION",
            "parsed_runs_by_mode": {},
            "canonical_semantic_sha256": None,
            "semantic_item_count": 0,
            "input_doc_sha256": "",
            "violations": ["BENCH_DOC_NOT_MAPPING"],
        }

    try:
        doc_sha = canonical_hash(dict(bench_doc))
    except Exception:
        doc_sha = canonical_hash({"nonfinite_doc_repr": repr(sorted((str(k), repr(v)) for k, v in bench_doc.items()))})

    # Stale / cached / reused flags check
    for flag_k in ("reused", "cached", "stale", "phony_timestamps"):
        if bool(bench_doc.get(flag_k, False)):
            violations.append(f"BENCHMARK_STALE_OR_CACHED_FLAG:{flag_k}")

    # Wrapper provenance checks (when expected or attached)
    if expected_run_id is not None:
        attached_rid = bench_doc.get("run_id")
        if attached_rid is not None and str(attached_rid) != str(expected_run_id):
            violations.append(f"BENCHMARK_RUN_ID_MISMATCH:got={attached_rid}!=expected={expected_run_id}")
        elif attached_rid is None and not is_historical_reference:
            violations.append("BENCHMARK_RUN_ID_MISSING_ON_FRESH_EXECUTION")

    if expected_tested_sha is not None:
        attached_sha = bench_doc.get("tested_executable_sha")
        if attached_sha is not None and str(attached_sha) != str(expected_tested_sha):
            violations.append(f"BENCHMARK_TESTED_SHA_MISMATCH:got={attached_sha}!=expected={expected_tested_sha}")
        elif attached_sha is None and not is_historical_reference:
            violations.append("BENCHMARK_TESTED_SHA_MISSING_ON_FRESH_EXECUTION")

    if expected_dataset_sha is not None:
        attached_ds = bench_doc.get("canonical_dataset_sha256")
        if attached_ds is not None and str(attached_ds) != str(expected_dataset_sha):
            violations.append(f"BENCHMARK_DATASET_SHA_MISMATCH:got={attached_ds}!=expected={expected_dataset_sha}")

    raw_runs = bench_doc.get("runs")
    if not isinstance(raw_runs, list) or len(raw_runs) == 0:
        violations.append("BENCHMARK_RUNS_MISSING_OR_EMPTY")
        return {
            "status": "BLOCKED_INVALID_BENCHMARK",
            "valid": False,
            "benchmark_kind": benchmark_kind,
            "provenance_role": "HISTORICAL_REFERENCE_ONLY" if is_historical_reference else "FRESH_EXECUTION",
            "parsed_runs_by_mode": {},
            "canonical_semantic_sha256": None,
            "semantic_item_count": 0,
            "input_doc_sha256": doc_sha,
            "violations": violations,
        }

    # Global mixed-schema check across all rows (P11)
    seen_pytest_keys = any(isinstance(r, Mapping) and any(k in r for k in PYTEST_EXCLUSIVE_KEYS) for r in raw_runs)
    seen_science_keys = any(isinstance(r, Mapping) and any(k in r for k in SCIENCE_EXCLUSIVE_KEYS) for r in raw_runs)
    if seen_pytest_keys and seen_science_keys:
        violations.append("MIXED_PYTEST_AND_SCIENCE_KEYS_IN_SAME_BENCHMARK_DOC")

    count_key = "executed_tests_count" if benchmark_kind == BENCHMARK_KIND_PYTEST else "rows_count"
    sha_key = "canonical_sorted_outcome_sha256" if benchmark_kind == BENCHMARK_KIND_PYTEST else "canonical_sorted_sha256"

    parsed_by_mode: Dict[str, Dict[str, Any]] = {}
    counts_by_mode: Dict[str, int] = {}
    shas_by_mode: Dict[str, str] = {}
    source_keys_used: List[str] = []

    for idx, row in enumerate(raw_runs):
        pr = parse_benchmark_run_duration_strict(
            row, benchmark_kind=benchmark_kind, allow_legacy_duration_keys=allow_legacy_duration_keys
        )
        if not pr["valid"]:
            violations.extend([f"ROW[{idx}]:{v}" for v in pr["violations"]])
            continue

        ml = str(pr["mode_label"])
        # Extract semantic item count and canonical SHA
        raw_cnt = row.get(count_key)
        if not _is_strict_int(raw_cnt) or int(raw_cnt) <= 0:
            violations.append(f"ROW[{idx}]:{ml}:INVALID_{count_key.upper()}:{raw_cnt!r}")
            cnt_val = -1
        else:
            cnt_val = int(raw_cnt)

        raw_sha = row.get(sha_key)
        if not _is_sha256_hex(raw_sha):
            violations.append(f"ROW[{idx}]:{ml}:INVALID_{sha_key.upper()}:{raw_sha!r}")
            sha_val = ""
        else:
            sha_val = str(raw_sha).lower()

        # Check duplicate rows for the same mode_label (P06)
        if ml in parsed_by_mode:
            prev = parsed_by_mode[ml]
            disagrees = (
                abs(float(prev["duration_sec"]) - float(pr["duration_sec"])) > 1e-9
                or prev.get("workers") != pr.get("workers")
                or counts_by_mode.get(ml) != cnt_val
                or shas_by_mode.get(ml) != sha_val
            )
            if disagrees:
                violations.append(f"DUPLICATE_INCOMPATIBLE_MODE_ROW:{ml}")
            else:
                violations.append(f"DUPLICATE_MODE_ROW_FORBIDDEN:{ml}")
            continue

        pr_enriched = dict(pr)
        pr_enriched["semantic_item_count"] = cnt_val
        pr_enriched["canonical_semantic_sha256"] = sha_val
        if benchmark_kind == BENCHMARK_KIND_PYTEST and isinstance(row.get("worker_pids"), list):
            pr_enriched["worker_pids"] = [int(p) for p in row["worker_pids"]]
        if benchmark_kind == BENCHMARK_KIND_SCIENCE and _is_strict_int(row.get("evaluated_bars_count")):
            pr_enriched["evaluated_bars_count"] = int(row["evaluated_bars_count"])
        parsed_by_mode[ml] = pr_enriched
        if cnt_val > 0:
            counts_by_mode[ml] = cnt_val
        if sha_val:
            shas_by_mode[ml] = sha_val
        if pr["duration_source_key"]:
            source_keys_used.append(str(pr["duration_source_key"]))

    # Require ALL 4 modes present (P04 missing w=1_repeat, P05 missing w=4)
    missing_modes = [m for m in ("w=1", "w=2", "w=4", "w=1_repeat") if m not in parsed_by_mode]
    if missing_modes:
        violations.append(f"MISSING_REQUIRED_BENCHMARK_MODES:{','.join(missing_modes)}")

    # Require consistent duration source key across the 4 rows
    if len(set(source_keys_used)) > 1:
        violations.append(f"INCONSISTENT_DURATION_SOURCE_KEYS:{sorted(set(source_keys_used))}")

    # Require identical positive semantic counts across all 4 modes
    unique_counts = set(counts_by_mode.values())
    if len(counts_by_mode) == 4 and len(unique_counts) != 1:
        violations.append(f"SEMANTIC_ITEM_COUNT_MISMATCH:{counts_by_mode}")

    # Require identical canonical semantic SHA across all 4 modes (P03, P18)
    unique_shas = set(shas_by_mode.values())
    if "w=1" in shas_by_mode and "w=1_repeat" in shas_by_mode and shas_by_mode["w=1"] != shas_by_mode["w=1_repeat"]:
        violations.append("W1_REPEAT_SEMANTIC_HASH_MISMATCH")
    if len(shas_by_mode) == 4 and len(unique_shas) != 1:
        violations.append(f"SEMANTIC_HASH_MISMATCH_ACROSS_MODES:{shas_by_mode}")

    # Check declared all_4_runs_identical flag if present
    if "all_4_runs_identical" in bench_doc and bench_doc.get("all_4_runs_identical") is not True:
        violations.append(f"WRAPPER_ALL_4_RUNS_IDENTICAL_NOT_TRUE:{bench_doc.get('all_4_runs_identical')!r}")

    # For pytest w=4: verify multi-worker PID diversity when worker_pids is provided
    if benchmark_kind == BENCHMARK_KIND_PYTEST and "w=4" in parsed_by_mode:
        w4_pids = parsed_by_mode["w=4"].get("worker_pids")
        if w4_pids is not None and len(set(w4_pids)) != 4:
            violations.append(f"W4_WORKER_PIDS_NOT_4_DISTINCT:{w4_pids}")

    is_valid = len(violations) == 0
    return {
        "status": "PASS" if is_valid else "BLOCKED_INVALID_BENCHMARK",
        "valid": is_valid,
        "benchmark_kind": benchmark_kind,
        "provenance_role": "HISTORICAL_REFERENCE_ONLY" if is_historical_reference else "FRESH_EXECUTION",
        "required_modes_present": len(missing_modes) == 0,
        "missing_modes": missing_modes,
        "parsed_runs_by_mode": parsed_by_mode,
        "duration_source_key": sorted(set(source_keys_used))[0] if len(set(source_keys_used)) == 1 else None,
        "canonical_semantic_sha256": next(iter(unique_shas)) if (is_valid and len(unique_shas) == 1) else None,
        "semantic_item_count": next(iter(unique_counts)) if (is_valid and len(unique_counts) == 1) else 0,
        "all_4_runs_semantically_identical": is_valid and len(unique_shas) == 1 and len(shas_by_mode) == 4,
        "input_doc_sha256": doc_sha,
        "violations": violations,
    }


def _evaluate_system_limits_strict(
    benchmark_kind: str,
    system_limits: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Validate resource ceilings fail-closed without reading untrusted environment overrides."""
    lim = dict(system_limits or {})
    violations: List[str] = []

    if bool(lim.get("owner_disk_preflight_waiver", False)):
        violations.append("UNAPPROVED_OWNER_DISK_WAIVER_FORBIDDEN")

    disk_floor = float(lim.get("disk_floor_gib", STRICT_RUNTIME_DISK_FLOOR_GIB))
    if disk_floor < STRICT_RUNTIME_DISK_FLOOR_GIB:
        violations.append(f"DISK_FLOOR_BELOW_STRICT_MINIMUM:{disk_floor}<{STRICT_RUNTIME_DISK_FLOOR_GIB}")
    disk_preflight_floor = float(lim.get("disk_preflight_floor_gib", STRICT_PREFLIGHT_DISK_FLOOR_GIB))
    if disk_preflight_floor < STRICT_PREFLIGHT_DISK_FLOOR_GIB:
        violations.append(f"DISK_PREFLIGHT_FLOOR_BELOW_STRICT_MINIMUM:{disk_preflight_floor}<{STRICT_PREFLIGHT_DISK_FLOOR_GIB}")

    ram_floor = float(lim.get("ram_floor_mib", STRICT_RAM_FLOOR_MIB))
    if ram_floor < STRICT_RAM_FLOOR_MIB:
        violations.append(f"RAM_FLOOR_BELOW_STRICT_MINIMUM:{ram_floor}<{STRICT_RAM_FLOOR_MIB}")

    if "preflight_disk_free_gib" in lim and lim["preflight_disk_free_gib"] is not None:
        pd = lim["preflight_disk_free_gib"]
        if not _is_strict_number(pd) or not math.isfinite(float(pd)) or float(pd) < disk_preflight_floor:
            violations.append(f"PREFLIGHT_DISK_FLOOR_BREACHED:{pd}<{disk_preflight_floor}")

    if "disk_free_gib" in lim and lim["disk_free_gib"] is not None:
        df = lim["disk_free_gib"]
        if not _is_strict_number(df) or not math.isfinite(float(df)) or float(df) < disk_floor:
            violations.append(f"RUNTIME_DISK_FLOOR_BREACHED:{df}<{disk_floor}")

    if "available_ram_mib" in lim and lim["available_ram_mib"] is not None:
        ar = lim["available_ram_mib"]
        if not _is_strict_number(ar) or not math.isfinite(float(ar)) or float(ar) < ram_floor:
            violations.append(f"RAM_FLOOR_BREACHED:{ar}<{ram_floor}")

    default_suite_cap = (
        DEFAULT_FULL_SUITE_PYTEST_SAFE_CAP if benchmark_kind == BENCHMARK_KIND_PYTEST else DEFAULT_SCIENCE_SAFE_CAP
    )

    cap_fields = {
        "configured_child_process_cap": lim.get("configured_child_process_cap", GLOBAL_MAX_CHILD_PROCESSES_CAP),
        "currently_allowed_worker_cap": lim.get("currently_allowed_worker_cap", GLOBAL_MAX_CHILD_PROCESSES_CAP),
        "ram_derived_worker_cap": lim.get("ram_derived_worker_cap", GLOBAL_MAX_CHILD_PROCESSES_CAP),
        "shard_exclusive_cap": lim.get("shard_exclusive_cap", default_suite_cap),
        "full_suite_safe_worker_cap": lim.get("full_suite_safe_worker_cap", default_suite_cap),
    }

    parsed_caps: Dict[str, int] = {}
    for ck, cv in cap_fields.items():
        if not _is_strict_int(cv) or int(cv) < 1:
            violations.append(f"INVALID_WORKER_CAP_{ck.upper()}:{cv!r}")
        elif int(cv) > GLOBAL_MAX_CHILD_PROCESSES_CAP:
            violations.append(f"WORKER_CAP_EXCEEDS_GLOBAL_MAX_4:{ck}={cv}>{GLOBAL_MAX_CHILD_PROCESSES_CAP}")
        else:
            parsed_caps[ck] = int(cv)

    safe_worker_cap = min(parsed_caps.values()) if (len(parsed_caps) == len(cap_fields) and not violations) else 0
    return {
        "resource_safety_ok": len(violations) == 0,
        "safe_worker_cap": safe_worker_cap,
        "cap_components": parsed_caps,
        "disk_free_gib": lim.get("disk_free_gib"),
        "preflight_disk_free_gib": lim.get("preflight_disk_free_gib"),
        "available_ram_mib": lim.get("available_ram_mib"),
        "violations": violations,
    }


def select_measured_worker_policy_strict(
    bench_doc: Mapping[str, Any],
    *,
    benchmark_kind: str,
    system_limits: Optional[Mapping[str, Any]] = None,
    tie_rule: str = "prefer_fewer_workers",
    min_meaningful_gain_pct: float = DEFAULT_MIN_MEANINGFUL_GAIN_PCT,
    expected_run_id: Optional[str] = None,
    expected_tested_sha: Optional[str] = None,
    expected_dataset_sha: Optional[str] = None,
    is_historical_reference: bool = False,
    allow_legacy_duration_keys: bool = True,
) -> Dict[str, Any]:
    """
    Select measured benchmark fastest mode AND separately compute resource-safe applied worker count.
    Never conflates subset fastest (`w=4`) with full-suite safe cap (`<= 3`), never extrapolates
    subset speedup to the full suite, and never returns PASS on invalid/missing/resource-blocked inputs.
    """
    if tie_rule != "prefer_fewer_workers":
        raise ValueError(f"UNSUPPORTED_TIE_RULE:{tie_rule}")

    limits_eval = _evaluate_system_limits_strict(benchmark_kind, system_limits)
    equiv = validate_benchmark_semantic_equivalence_strict(
        bench_doc,
        benchmark_kind=benchmark_kind,
        expected_run_id=expected_run_id,
        expected_tested_sha=expected_tested_sha,
        expected_dataset_sha=expected_dataset_sha,
        is_historical_reference=is_historical_reference,
        allow_legacy_duration_keys=allow_legacy_duration_keys,
    )

    benchmark_scope = (
        "controlled_pytest_subset_only_NOT_full_repo_suite"
        if benchmark_kind == BENCHMARK_KIND_PYTEST
        else "r2_8000_bar_science_slice_only_NOT_full_dataset"
    )

    # Compute measured stats whenever benchmark rows are valid (even if resource limits later block application)
    if equiv["valid"]:
        by_mode = equiv["parsed_runs_by_mode"]
        d1 = float(by_mode["w=1"]["duration_sec"])
        d2 = float(by_mode["w=2"]["duration_sec"])
        d4 = float(by_mode["w=4"]["duration_sec"])
        d1_rep = float(by_mode["w=1_repeat"]["duration_sec"])

        mean_wall = {"w=1": round(d1, 4), "w=2": round(d2, 4), "w=4": round(d4, 4)}
        median_wall = dict(mean_wall)
        all_four_wall = {
            "w=1": round(d1, 4),
            "w=2": round(d2, 4),
            "w=4": round(d4, 4),
            "w=1_repeat": round(d1_rep, 4),
        }

        sp_w2_vs_w1 = round(d1 / max(1e-9, d2), 3)
        sp_w4_vs_w1 = round(d1 / max(1e-9, d4), 3)
        w1_pair_mean = (d1 + d1_rep) / 2.0
        sp_w4_vs_w1_mean = round(w1_pair_mean / max(1e-9, d4), 3)

        w1_repeat_delta_sec = round(d1_rep - d1, 4)
        w1_repeat_variance_pct = round(abs(d1_rep - d1) / max(1e-9, min(d1, d1_rep)), 4)
        w1_repeat_drift_vs_w1_pct = round(abs(d1_rep - d1) / max(1e-9, d1), 4)
        if w1_repeat_variance_pct <= DEFAULT_NOISY_REPEAT_VARIANCE_PCT:
            stability_status = "STABLE_LOW_TIMING_VARIANCE"
        elif w1_repeat_variance_pct <= 0.15:
            stability_status = "MODERATE_TIMING_NOISE_WARNING"
        else:
            stability_status = "HIGH_TIMING_NOISE_WARNING"

        # Deterministic selection among measured W1, W2, W4; exact tie chooses FEWER workers (P16)
        ranked = sorted(
            CANDIDATE_MODES_ORDER,
            key=lambda m: (round(mean_wall[m], 6), REQUIRED_MODES_WORKERS[m]),
        )
        measured_fastest_mode = ranked[0]
        measured_fastest_workers = REQUIRED_MODES_WORKERS[measured_fastest_mode]
        measured_fastest_sec = mean_wall[measured_fastest_mode]

        tie_detected = len(
            [m for m in CANDIDATE_MODES_ORDER if abs(mean_wall[m] - measured_fastest_sec) <= 1e-6]
        ) > 1
        gain_vs_w1_pct = round((d1 - measured_fastest_sec) / max(1e-9, d1), 4)
        gain_exceeds_min_threshold = (
            True if measured_fastest_mode == "w=1" else (gain_vs_w1_pct >= float(min_meaningful_gain_pct))
        )
        gain_exceeds_w1_repeat_noise = (
            True if measured_fastest_mode == "w=1" else (gain_vs_w1_pct > w1_repeat_variance_pct)
        )

        borderline_or_noisy_gain_warning = False
        if measured_fastest_mode == "w=1":
            policy_rec_mode = "w=1"
            policy_confidence = "CLEAR_W1_FASTEST_OR_TIED_PREFER_FEWER_WORKERS"
            stability_note = (
                f"w=1 fastest (or tied, resolved to fewer workers); w=1_repeat drift={w1_repeat_variance_pct:.2%} ({stability_status})."
            )
        elif not gain_exceeds_min_threshold:
            policy_rec_mode = "w=1"
            borderline_or_noisy_gain_warning = True
            policy_confidence = "INCONCLUSIVE_GAIN_BELOW_MIN_THRESHOLD_RETAIN_W1"
            stability_note = (
                f"{measured_fastest_mode} gain vs w=1 is {gain_vs_w1_pct:.2%} (< {min_meaningful_gain_pct:.2%} min threshold); "
                f"w=1_repeat variance={w1_repeat_variance_pct:.2%}; retaining conservative w=1 policy."
            )
        elif not gain_exceeds_w1_repeat_noise:
            policy_rec_mode = "w=1"
            borderline_or_noisy_gain_warning = True
            policy_confidence = "INCONCLUSIVE_GAIN_WITHIN_W1_REPEAT_NOISE_RETAIN_CONSERVATIVE"
            stability_note = (
                f"{measured_fastest_mode} gain vs w=1 ({gain_vs_w1_pct:.2%}) does not exceed w=1_repeat timing noise ({w1_repeat_variance_pct:.2%}); "
                f"retaining conservative policy without cherry-picking."
            )
        else:
            policy_rec_mode = measured_fastest_mode
            policy_confidence = "CLEAR_PARALLEL_GAIN_WITHIN_MEASURED_SCOPE"
            stability_note = (
                f"{measured_fastest_mode} fastest in measured scope ({gain_vs_w1_pct:.2%} faster than w=1, "
                f"exceeding {min_meaningful_gain_pct:.2%} threshold and {w1_repeat_variance_pct:.2%} w=1_repeat variance). "
                f"Subset timing NOT extrapolated to full repo suite."
            )
    else:
        mean_wall = {}
        median_wall = {}
        all_four_wall = {}
        sp_w2_vs_w1 = None
        sp_w4_vs_w1 = None
        sp_w4_vs_w1_mean = None
        d1_rep = None
        w1_repeat_delta_sec = None
        w1_repeat_variance_pct = None
        w1_repeat_drift_vs_w1_pct = None
        stability_status = "INVALID_BENCHMARK_NO_STABILITY_DATA"
        measured_fastest_mode = None
        measured_fastest_workers = None
        tie_detected = False
        gain_vs_w1_pct = None
        gain_exceeds_min_threshold = False
        gain_exceeds_w1_repeat_noise = False
        borderline_or_noisy_gain_warning = True
        policy_rec_mode = "w=1"
        policy_confidence = "POLICY_BLOCKED_WITH_SAFE_FALLBACK"
        stability_note = "Benchmark validation failed; conservative w=1 fallback is BLOCKED (NOT PASS)."

    # Fail-closed precedence: Resource floor breach -> BLOCKED_RESOURCE; Invalid benchmark -> BLOCKED_INVALID_BENCHMARK
    if not limits_eval["resource_safety_ok"]:
        all_viols = list(limits_eval["violations"]) + list(equiv["violations"])
        result: Dict[str, Any] = {
            "spec_version": SPEC_VERSION_KH03_R3_C1,
            "schema_version": SCHEMA_VERSION_KH03_R3_C1,
            "benchmark_kind": benchmark_kind,
            "benchmark_scope": benchmark_scope,
            "provenance_role": equiv["provenance_role"],
            "status": "BLOCKED_RESOURCE",
            "policy_status": "BLOCKED_RESOURCE_SAFETY",
            "can_certify": False,
            "mean_wall_sec_by_mode": mean_wall,
            "median_wall_sec_by_mode": median_wall,
            "all_four_measured_wall_sec": all_four_wall,
            "w1_repeat_wall_sec": round(d1_rep, 4) if d1_rep is not None else None,
            "w1_repeat_delta_sec": w1_repeat_delta_sec,
            "w1_repeat_variance_pct": w1_repeat_variance_pct,
            "w1_repeat_drift_vs_w1_pct": w1_repeat_drift_vs_w1_pct,
            "timing_stability_status": stability_status,
            "timing_determinism_claimed": False,
            "measured_fastest_mode": measured_fastest_mode,
            "measured_fastest_workers": measured_fastest_workers,
            "optimum_worker_policy": measured_fastest_mode or "w=1",
            "policy_recommended_mode_after_gain_rule": "BLOCKED",
            "safe_worker_cap": 0,
            "applied_worker_count": 0,
            "applied_worker_mode": "BLOCKED_RESOURCE",
            "applied_worker_count_is_benchmark_measured": False,
            "cap_components": limits_eval["cap_components"],
            "measured_speedup_w2_vs_w1": sp_w2_vs_w1,
            "measured_speedup_w4_vs_w1": sp_w4_vs_w1,
            "measured_speedup_w4_vs_w1_mean": sp_w4_vs_w1_mean,
            "gain_vs_w1_pct": gain_vs_w1_pct,
            "min_meaningful_gain_pct": float(min_meaningful_gain_pct),
            "gain_exceeds_min_threshold": gain_exceeds_min_threshold,
            "gain_exceeds_w1_repeat_noise": gain_exceeds_w1_repeat_noise,
            "tie_detected": tie_detected,
            "tie_rule": tie_rule,
            "borderline_or_noisy_gain_warning": True,
            "policy_recommendation_confidence": "BLOCKED_RESOURCE_SAFETY",
            "stability_disclosure": "Resource ceiling breached (disk/RAM/waiver/cap); heavy execution BLOCKED with no override.",
            "full_suite_speedup_extrapolated": False,
            "sample_size": equiv["semantic_item_count"],
            "duration_source_key": equiv.get("duration_source_key"),
            "canonical_semantic_sha256": equiv.get("canonical_semantic_sha256"),
            "input_doc_sha256": equiv["input_doc_sha256"],
            "safe_fallback_reason": "; ".join(all_viols),
            "violations": all_viols,
        }
        result["policy_decision_sha256"] = canonical_hash(result)
        return result

    if not equiv["valid"]:
        result = {
            "spec_version": SPEC_VERSION_KH03_R3_C1,
            "schema_version": SCHEMA_VERSION_KH03_R3_C1,
            "benchmark_kind": benchmark_kind,
            "benchmark_scope": benchmark_scope,
            "provenance_role": equiv["provenance_role"],
            "status": "BLOCKED_INVALID_BENCHMARK",
            "policy_status": "POLICY_BLOCKED_WITH_SAFE_FALLBACK",
            "can_certify": False,
            "mean_wall_sec_by_mode": {},
            "median_wall_sec_by_mode": {},
            "all_four_measured_wall_sec": {},
            "w1_repeat_wall_sec": None,
            "w1_repeat_delta_sec": None,
            "w1_repeat_variance_pct": None,
            "w1_repeat_drift_vs_w1_pct": None,
            "timing_stability_status": stability_status,
            "timing_determinism_claimed": False,
            "measured_fastest_mode": None,
            "measured_fastest_workers": None,
            "optimum_worker_policy": "w=1",
            "policy_recommended_mode_after_gain_rule": "w=1",
            "safe_worker_cap": limits_eval["safe_worker_cap"],
            "applied_worker_count": 1,
            "applied_worker_mode": "w=1_SAFE_FALLBACK_BLOCKED",
            "applied_worker_count_is_benchmark_measured": False,
            "cap_components": limits_eval["cap_components"],
            "measured_speedup_w2_vs_w1": None,
            "measured_speedup_w4_vs_w1": None,
            "measured_speedup_w4_vs_w1_mean": None,
            "gain_vs_w1_pct": None,
            "min_meaningful_gain_pct": float(min_meaningful_gain_pct),
            "gain_exceeds_min_threshold": False,
            "gain_exceeds_w1_repeat_noise": False,
            "tie_detected": False,
            "tie_rule": tie_rule,
            "borderline_or_noisy_gain_warning": True,
            "policy_recommendation_confidence": "POLICY_BLOCKED_WITH_SAFE_FALLBACK",
            "stability_disclosure": stability_note,
            "full_suite_speedup_extrapolated": False,
            "sample_size": 0,
            "duration_source_key": equiv.get("duration_source_key"),
            "canonical_semantic_sha256": None,
            "input_doc_sha256": equiv["input_doc_sha256"],
            "safe_fallback_reason": "; ".join(equiv["violations"]),
            "violations": list(equiv["violations"]),
        }
        result["policy_decision_sha256"] = canonical_hash(result)
        return result

    safe_cap = int(limits_eval["safe_worker_cap"])
    rec_workers = REQUIRED_MODES_WORKERS[policy_rec_mode]
    applied_workers = min(rec_workers, safe_cap)
    is_capped = applied_workers < int(measured_fastest_workers or 1)
    applied_is_measured = applied_workers in (1, 2, 4) and (not is_capped or applied_workers in (1, 2))
    if applied_workers == 3:
        applied_mode_lbl = "w=3_SAFE_CAP_NOT_BENCHMARK_MEASURED"
        applied_is_measured = False
    elif is_capped:
        applied_mode_lbl = f"w={applied_workers}_CAPPED_BELOW_MEASURED_{measured_fastest_mode}"
    else:
        applied_mode_lbl = f"w={applied_workers}"

    result = {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "benchmark_kind": benchmark_kind,
        "benchmark_scope": benchmark_scope,
        "provenance_role": equiv["provenance_role"],
        "status": "PASS",
        "policy_status": "PASS_MEASURED_AND_SAFE_CAP_APPLIED",
        "can_certify": True,
        "mean_wall_sec_by_mode": mean_wall,
        "median_wall_sec_by_mode": median_wall,
        "all_four_measured_wall_sec": all_four_wall,
        "w1_repeat_wall_sec": round(d1_rep, 4),
        "w1_repeat_delta_sec": w1_repeat_delta_sec,
        "w1_repeat_variance_pct": w1_repeat_variance_pct,
        "w1_repeat_drift_vs_w1_pct": w1_repeat_drift_vs_w1_pct,
        "timing_stability_status": stability_status,
        "timing_determinism_claimed": False,
        "measured_fastest_mode": measured_fastest_mode,
        "measured_fastest_workers": measured_fastest_workers,
        "optimum_worker_policy": measured_fastest_mode,
        "policy_recommended_mode_after_gain_rule": policy_rec_mode,
        "safe_worker_cap": safe_cap,
        "applied_worker_count": applied_workers,
        "applied_worker_mode": applied_mode_lbl,
        "applied_worker_count_is_benchmark_measured": applied_is_measured,
        "cap_components": limits_eval["cap_components"],
        "measured_speedup_w2_vs_w1": sp_w2_vs_w1,
        "measured_speedup_w4_vs_w1": sp_w4_vs_w1,
        "measured_speedup_w4_vs_w1_mean": sp_w4_vs_w1_mean,
        "gain_vs_w1_pct": gain_vs_w1_pct,
        "min_meaningful_gain_pct": float(min_meaningful_gain_pct),
        "gain_exceeds_min_threshold": gain_exceeds_min_threshold,
        "gain_exceeds_w1_repeat_noise": gain_exceeds_w1_repeat_noise,
        "tie_detected": tie_detected,
        "tie_rule": tie_rule,
        "borderline_or_noisy_gain_warning": borderline_or_noisy_gain_warning,
        "policy_recommendation_confidence": policy_confidence,
        "stability_disclosure": stability_note,
        "full_suite_speedup_extrapolated": False,
        "sample_size": equiv["semantic_item_count"],
        "duration_source_key": equiv["duration_source_key"],
        "canonical_semantic_sha256": equiv["canonical_semantic_sha256"],
        "input_doc_sha256": equiv["input_doc_sha256"],
        "safe_fallback_reason": None,
        "violations": [],
    }
    result["policy_decision_sha256"] = canonical_hash(result)
    return result


def recompute_worker_policy_from_raw_receipt(
    raw_bench_doc: Mapping[str, Any],
    reported_policy: Mapping[str, Any],
    *,
    benchmark_kind: str,
    reported_speedup_w2_vs_w1: Optional[float] = None,
    reported_speedup_w4_vs_w1: Optional[float] = None,
    system_limits: Optional[Mapping[str, Any]] = None,
    expected_run_id: Optional[str] = None,
    expected_tested_sha: Optional[str] = None,
    is_historical_reference: bool = False,
) -> Dict[str, Any]:
    """
    Pure verification path reading RAW benchmark rows and comparing against a reported policy.
    Detects empty `mean_wall_sec_by_mode`, mismatched fastest mode, forged speedups, over-cap
    applied workers, extrapolated full-suite speedup claims, and stale/invalid inputs.
    """
    discrepancies: List[str] = []
    recomp = select_measured_worker_policy_strict(
        raw_bench_doc,
        benchmark_kind=benchmark_kind,
        system_limits=system_limits,
        expected_run_id=expected_run_id,
        expected_tested_sha=expected_tested_sha,
        is_historical_reference=is_historical_reference,
    )

    if not isinstance(reported_policy, Mapping):
        discrepancies.append("REPORTED_POLICY_NOT_MAPPING")
        return {
            "status": "FAIL",
            "valid": False,
            "benchmark_kind": benchmark_kind,
            "recomputed": recomp,
            "discrepancies": discrepancies,
        }

    if recomp["status"] != "PASS":
        discrepancies.append(f"RAW_BENCHMARK_INVALID_OR_BLOCKED:{recomp['status']}:{';'.join(recomp['violations'])}")
        if reported_policy.get("status") == "PASS":
            discrepancies.append("REPORTED_PASS_DESPITE_INVALID_OR_BLOCKED_RAW_BENCHMARK")

    rep_means = reported_policy.get("mean_wall_sec_by_mode")
    if not isinstance(rep_means, Mapping) or len(rep_means) == 0:
        if recomp["mean_wall_sec_by_mode"]:
            discrepancies.append("EMPTY_REPORTED_MEAN_WALL_SEC_BY_MODE_WITH_VALID_RAW_TIMINGS")
    else:
        for mode_k in CANDIDATE_MODES_ORDER:
            exp_v = recomp["mean_wall_sec_by_mode"].get(mode_k)
            got_v = rep_means.get(mode_k)
            if exp_v is None:
                continue
            if not _is_strict_number(got_v) or abs(float(got_v) - float(exp_v)) > 1e-3:
                discrepancies.append(f"MEAN_WALL_MISMATCH:{mode_k}:reported={got_v}!=recomputed={exp_v}")

    if recomp["measured_fastest_mode"] is not None:
        rep_opt = reported_policy.get("optimum_worker_policy")
        rep_fast = reported_policy.get("measured_fastest_mode", rep_opt)
        if rep_opt != recomp["measured_fastest_mode"]:
            discrepancies.append(
                f"OPTIMUM_WORKER_POLICY_MISMATCH:reported={rep_opt}!=recomputed={recomp['measured_fastest_mode']}"
            )
        if rep_fast != recomp["measured_fastest_mode"]:
            discrepancies.append(
                f"MEASURED_FASTEST_MODE_MISMATCH:reported={rep_fast}!=recomputed={recomp['measured_fastest_mode']}"
            )

    if "applied_worker_count" in reported_policy:
        app_w = reported_policy.get("applied_worker_count")
        if not _is_strict_int(app_w):
            discrepancies.append(f"INVALID_REPORTED_APPLIED_WORKER_COUNT:{app_w!r}")
        else:
            if int(app_w) > GLOBAL_MAX_CHILD_PROCESSES_CAP:
                discrepancies.append(f"APPLIED_WORKER_COUNT_EXCEEDS_GLOBAL_CAP_4:{app_w}")
            if recomp["safe_worker_cap"] > 0 and int(app_w) > int(recomp["safe_worker_cap"]):
                discrepancies.append(
                    f"APPLIED_WORKER_COUNT_EXCEEDS_SAFE_CAP:{app_w}>{recomp['safe_worker_cap']}"
                )
            if recomp["status"] == "PASS" and int(app_w) != int(recomp["applied_worker_count"]):
                discrepancies.append(
                    f"APPLIED_WORKER_COUNT_DRIFT:reported={app_w}!=recomputed={recomp['applied_worker_count']}"
                )

    if bool(reported_policy.get("full_suite_speedup_extrapolated", False)):
        discrepancies.append("FORBIDDEN_FULL_SUITE_SPEEDUP_EXTRAPOLATION_CLAIMED")

    if "w1_repeat_wall_sec" in reported_policy and recomp["w1_repeat_wall_sec"] is not None:
        got_rep = reported_policy.get("w1_repeat_wall_sec")
        if not _is_strict_number(got_rep) or abs(float(got_rep) - float(recomp["w1_repeat_wall_sec"])) > 1e-3:
            discrepancies.append(
                f"W1_REPEAT_WALL_SEC_MISMATCH:reported={got_rep}!=recomputed={recomp['w1_repeat_wall_sec']}"
            )

    sp2_chk = reported_speedup_w2_vs_w1 if reported_speedup_w2_vs_w1 is not None else reported_policy.get("measured_speedup_w2_vs_w1")
    sp4_chk = reported_speedup_w4_vs_w1 if reported_speedup_w4_vs_w1 is not None else reported_policy.get("measured_speedup_w4_vs_w1")
    if sp2_chk is not None and recomp["measured_speedup_w2_vs_w1"] is not None:
        if not _is_strict_number(sp2_chk) or abs(float(sp2_chk) - float(recomp["measured_speedup_w2_vs_w1"])) > 0.01:
            discrepancies.append(
                f"FORGED_OR_DRIFTED_SPEEDUP_W2:reported={sp2_chk}!=recomputed={recomp['measured_speedup_w2_vs_w1']}"
            )
    if sp4_chk is not None and recomp["measured_speedup_w4_vs_w1"] is not None:
        if not _is_strict_number(sp4_chk) or abs(float(sp4_chk) - float(recomp["measured_speedup_w4_vs_w1"])) > 0.01:
            discrepancies.append(
                f"FORGED_OR_DRIFTED_SPEEDUP_W4:reported={sp4_chk}!=recomputed={recomp['measured_speedup_w4_vs_w1']}"
            )

    ok = len(discrepancies) == 0 and recomp["status"] == "PASS"
    return {
        "spec_version": SPEC_VERSION_KH03_R3_C1,
        "schema_version": SCHEMA_VERSION_KH03_R3_C1,
        "status": "PASS" if ok else "FAIL",
        "valid": ok,
        "benchmark_kind": benchmark_kind,
        "recomputed_policy": recomp,
        "discrepancies_count": len(discrepancies),
        "discrepancies": discrepancies,
    }
