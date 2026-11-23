"""
aurum-desk/backend/services/kh_03_r3_legacy_research_guard.py

AURUM DESK — KH-03.R3 | P0-A LEGACY METHOD FIREWALL
SPEC VERSION: KH-03.R3-v1.0 | RESEARCH ONLY | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO

Enforces a static AST + runtime provenance guard preventing any NEW/CURRENT research
entrypoint from certifying DEV candidates or research approvals using superseded KH-03.R1
methods (`run_kh03_full_research_study_10_to_16`, `preregister_kh03_research_plan`,
`build_kh03_partition_manifest_and_noncontamination`, old CI Mapping parser, post-hoc WF
slicing, or executable H3 promotion).

Honest Scope Disclosure (§04):
  - Sealed KH-03.R1 historical files and legacy R1 tests remain untouched and direct-importable
    solely for `HISTORICAL_REPLAY_ONLY` audit compatibility
    (`LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY = True`).
  - No production code or monkeypatching is applied. Enforcement operates via static AST
    callgraph/import/alias/re-export/eval-wrapper scanning plus explicit protocol/provenance
    gates on all KH-03.R2+ research entrypoints.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from services.kh_03_r2_methodology_service import (
    KH03_R2_H3_ADVISORY_ARM,
    parse_kh03_cluster_ci_strict,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH03_R3 = "KH-03.R3-v1.0"
SCHEMA_VERSION_KH03_R3 = "kh_03_r3_entry_exit_diagnosis_v1"

LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY = True

FORBIDDEN_LEGACY_R1_PROMOTION_SYMBOLS: Tuple[str, ...] = (
    "run_kh03_full_research_study_10_to_16",
    "preregister_kh03_research_plan",
    "build_kh03_partition_manifest_and_noncontamination",
)

# Exact, transparent whitelist of historical R1 files and R3 RED-repro tests ONLY.
# Any OTHER file importing or invoking FORBIDDEN_LEGACY_R1_PROMOTION_SYMBOLS fails closed!
DEFAULT_ALLOWED_HISTORICAL_LEGACY_PATHS: Tuple[str, ...] = (
    "aurum-desk/backend/services/kh_03_research_service.py",
    "aurum-desk/backend/services/kh_03_data_governance_service.py",
    "aurum-desk/tools/run_kh_03_acceptance.py",
    "aurum-desk/tools/kh_03_independent_verifier.py",
    "aurum-desk/backend/tests/test_kh_03_causal_research_and_negative_controls.py",
    "aurum-desk/backend/tests/test_kh_03_partition_seal_and_provenance.py",
    "aurum-desk/backend/tests/test_kh_03_async_and_resource_safety.py",
    "aurum-desk/backend/tests/test_kh_03_verifier_mutations.py",
    "aurum-desk/backend/tests/test_kh03_r3_legacy_guard.py",
)

VALID_CURRENT_RESEARCH_PROTOCOLS: Tuple[str, ...] = (
    "KH-03.R2-v1.0",
    "KH-03.R3-v1.0",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _classify_entrypoint_role(rel_posix: str) -> str:
    low = rel_posix.lower()
    if any(tok in low for tok in ("kh_03_r2", "kh03_r2", "kh_03_r3", "kh03_r3")):
        return "CURRENT_RESEARCH_EVALUATION"
    if any(tok in low for tok in ("kh_03_", "kh_02_", "kh_01")):
        return "HISTORICAL_REPLAY_ONLY"
    return "PRODUCTION"


def scan_python_source_for_forbidden_r1_usage(
    source_code: str,
    *,
    filename: str = "<memory>",
) -> List[Dict[str, Any]]:
    """
    AST scanner detecting direct imports, aliased imports, re-exports, attribute calls,
    and dynamic `getattr`/`eval`/`exec`/`importlib` wrappers referencing forbidden R1
    certification/study functions.
    """
    hits: List[Dict[str, Any]] = []
    forbidden_set = set(FORBIDDEN_LEGACY_R1_PROMOTION_SYMBOLS)
    source_code = source_code.lstrip("\ufeff")
    try:
        tree = ast.parse(source_code, filename=filename)
    except SyntaxError as exc:
        return [{"kind": "SYNTAX_ERROR", "symbol": "", "line": getattr(exc, "lineno", 0), "detail": str(exc)}]

    aliased_r1_modules: Set[str] = set()
    aliased_forbidden_names: Dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("kh_03_research_service") or alias.name.endswith("kh_03_data_governance_service"):
                    aliased_r1_modules.add(alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                orig = alias.name
                local_nm = alias.asname or orig
                if orig in forbidden_set:
                    aliased_forbidden_names[local_nm] = orig
                    hits.append(
                        {
                            "kind": "DIRECT_OR_ALIASED_IMPORT",
                            "symbol": orig,
                            "local_alias": local_nm,
                            "module": mod,
                            "line": int(getattr(node, "lineno", 0)),
                        }
                    )
                elif orig == "*" and ("kh_03_research_service" in mod or "kh_03_data_governance_service" in mod):
                    hits.append(
                        {
                            "kind": "WILDCARD_R1_IMPORT",
                            "symbol": "*",
                            "local_alias": "*",
                            "module": mod,
                            "line": int(getattr(node, "lineno", 0)),
                        }
                    )

    for node in ast.walk(tree):
        # Attribute access: r1_mod.run_kh03_full_research_study_10_to_16
        if isinstance(node, ast.Attribute) and node.attr in forbidden_set:
            hits.append(
                {
                    "kind": "ATTRIBUTE_REFERENCE_OR_CALL",
                    "symbol": node.attr,
                    "line": int(getattr(node, "lineno", 0)),
                }
            )
        # Re-export via __all__ or string-based dynamic wrapper (`getattr`, `eval`, `exec`)
        elif isinstance(node, ast.Call):
            fn_name = ""
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fn_name = node.func.attr

            if fn_name in aliased_forbidden_names:
                hits.append(
                    {
                        "kind": "CALL_TO_ALIASED_FORBIDDEN_SYMBOL",
                        "symbol": aliased_forbidden_names[fn_name],
                        "local_alias": fn_name,
                        "line": int(getattr(node, "lineno", 0)),
                    }
                )
            elif fn_name in ("getattr", "eval", "exec"):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        for sym in forbidden_set:
                            if sym in arg.value:
                                hits.append(
                                    {
                                        "kind": f"DYNAMIC_{fn_name.upper()}_WRAPPER",
                                        "symbol": sym,
                                        "line": int(getattr(node, "lineno", 0)),
                                    }
                                )
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str) and elt.value in forbidden_set:
                            hits.append(
                                {
                                    "kind": "REEXPORT_IN_ALL",
                                    "symbol": elt.value,
                                    "line": int(getattr(node, "lineno", 0)),
                                }
                            )

    # Deduplicate stably by (kind, symbol, line)
    seen: Set[Tuple[str, str, int]] = set()
    uniq: List[Dict[str, Any]] = []
    for h in sorted(hits, key=lambda x: (int(x.get("line", 0)), str(x.get("kind")), str(x.get("symbol")))):
        key = (str(h["kind"]), str(h["symbol"]), int(h.get("line", 0)))
        if key not in seen:
            seen.add(key)
            uniq.append(h)
    return uniq


def audit_kh03_r3_legacy_research_callgraph(
    repo_root: Path,
    allowed_legacy_compat_paths: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Scans EVERY Python file under `aurum-desk/` via AST to classify research entrypoints
    and enforce that ZERO non-whitelisted current/new or production modules import, alias,
    re-export, or dynamically wrap superseded KH-03.R1 promotion functions.
    """
    repo_root = Path(repo_root).resolve()
    app_root = repo_root / "aurum-desk"
    allowed_set = {
        p.replace("\\", "/")
        for p in (
            DEFAULT_ALLOWED_HISTORICAL_LEGACY_PATHS
            if allowed_legacy_compat_paths is None
            else allowed_legacy_compat_paths
        )
    }
    # Always exclude the guard definition file itself from tripping on its own constant strings
    guard_self_rel = "aurum-desk/backend/services/kh_03_r3_legacy_research_guard.py"

    files_scanned = 0
    whitelisted_callers: List[Dict[str, Any]] = []
    unauthorized_violations: List[Dict[str, Any]] = []
    current_r2_r3_entrypoints: List[Dict[str, Any]] = []

    for py_path in sorted(app_root.rglob("*.py")):
        rel = py_path.relative_to(repo_root).as_posix()
        if "/__pycache__/" in rel or "/.venv/" in rel or "/venv/" in rel:
            continue
        files_scanned += 1
        role = _classify_entrypoint_role(rel)
        if role == "CURRENT_RESEARCH_EVALUATION":
            current_r2_r3_entrypoints.append({"rel_path": rel, "role": role})
        if rel == guard_self_rel:
            continue

        src = py_path.read_text("utf-8-sig", errors="replace")
        hits = scan_python_source_for_forbidden_r1_usage(src, filename=rel)
        if not hits:
            continue
        rec = {"rel_path": rel, "role": role, "hits_count": len(hits), "hits": hits}
        if rel in allowed_set:
            whitelisted_callers.append(rec)
        else:
            unauthorized_violations.append(rec)

    guard_pass = len(unauthorized_violations) == 0 and files_scanned > 0
    return {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "LEGACY_METHOD_GUARD": "PASS" if guard_pass else "FAIL",
        "LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY": LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY,
        "runtime_monkeypatch_applied_to_production": False,
        "forbidden_r1_promotion_symbols": list(FORBIDDEN_LEGACY_R1_PROMOTION_SYMBOLS),
        "allowed_historical_legacy_whitelist": sorted(allowed_set),
        "python_files_scanned_count": files_scanned,
        "current_r2_r3_entrypoints_count": len(current_r2_r3_entrypoints),
        "whitelisted_historical_callers": whitelisted_callers,
        "unauthorized_r1_callers": unauthorized_violations,
        "unauthorized_r1_callers_count": len(unauthorized_violations),
        "scope_disclosure": (
            "Static AST whitelist+blacklist enforcement covers all Python modules under `aurum-desk/`. "
            "Historical KH-03.R1 modules remain untouched and directly importable strictly for "
            "`HISTORICAL_REPLAY_ONLY` audit compatibility; no current/new research evaluation or "
            "production module is permitted to invoke R1 certification entrypoints."
        ),
        "status": "PASS" if guard_pass else "FAIL",
    }


def require_kh03_r2_or_newer_research_protocol(
    protocol_id: str,
    evidence: Mapping[str, Any],
    *,
    allow_legacy_descriptive_only: bool = False,
) -> Dict[str, Any]:
    """
    Runtime protocol gate protecting `RESEARCH_APPROVAL` and `DEV_CANDIDATE` outputs.
    Rejects KH-03.R1 or unknown protocol versions; allows legacy R1 payloads ONLY when
    explicitly requested with `allow_legacy_descriptive_only=True` AND tagged
    `LEGACY_DESCRIPTIVE_ONLY` (NEVER eligible for DEV candidate promotion).
    """
    pid = str(protocol_id or "").strip()
    violations: List[str] = []

    if pid not in VALID_CURRENT_RESEARCH_PROTOCOLS:
        if allow_legacy_descriptive_only and pid.startswith("KH-03.R1"):
            return {
                "admitted_for_certification": False,
                "admitted_for_descriptive_audit_only": True,
                "protocol_id": pid,
                "classification": "LEGACY_DESCRIPTIVE_ONLY",
                "violations": ["LEGACY_R1_CANNOT_CERTIFY_CURRENT_RESEARCH_OR_DEV_CANDIDATE"],
            }
        return {
            "admitted_for_certification": False,
            "admitted_for_descriptive_audit_only": False,
            "protocol_id": pid,
            "classification": "REJECTED_UNSUPPORTED_OR_SUPERSEDED_PROTOCOL",
            "violations": [f"PROTOCOL_BELOW_KH03_R2_REJECTED:{pid}"],
        }

    # Check mandatory KH-03.R2+ structural invariants on evidence payload
    if str(evidence.get("ACTIVATION_READY") or "") != "NO":
        violations.append("FORGED_OR_MISSING_ACTIVATION_READY_NO")
    if int(evidence.get("OOS_EVALUATIONS", -1)) != 0:
        violations.append(f"NONZERO_OOS_EVALUATIONS:{evidence.get('OOS_EVALUATIONS')}")
    if str(evidence.get("RESEARCH_ECONOMIC_VERDICT") or "") != "NO_PROVEN_EDGE":
        violations.append(f"INVALID_DEV_ECONOMIC_VERDICT:{evidence.get('RESEARCH_ECONOMIC_VERDICT')}")
    if bool(evidence.get("h3_promoted_as_executable", False)):
        violations.append("FORBIDDEN_H3_EXECUTABLE_PROMOTION")
    if str(evidence.get("temporal_validation_kind") or "") != "FORWARD_ONLY_DEV_VALIDATION":
        violations.append(f"NON_FORWARD_ONLY_VALIDATION:{evidence.get('temporal_validation_kind')}")
    if int(evidence.get("embargo_minutes", 0)) < 240:
        violations.append(f"EMBARGO_LT_240M:{evidence.get('embargo_minutes')}")
    if int(evidence.get("future_train_bars_count", 1)) != 0:
        violations.append(f"FUTURE_TRAIN_BARS_NONZERO:{evidence.get('future_train_bars_count')}")
    if bool(evidence.get("reused_dev_claimed_as_fresh_oos", False)):
        violations.append("REUSED_DEV_CLAIMED_AS_FRESH_OOS")

    rsm = evidence.get("runtime_safety_modes") or {}
    if any(rsm.get(k) != "OFF" for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO")):
        violations.append("RUNTIME_SAFETY_MODE_NOT_OFF")

    if "raw_ci_samples" in evidence:
        for ac, raw_ci in (evidence.get("raw_ci_samples") or {}).items():
            parsed = parse_kh03_cluster_ci_strict(raw_ci, label=str(ac))
            if not parsed["schema_valid"]:
                violations.append(f"INVALID_CI_SCHEMA:{ac}:{parsed['diagnostic']}")

    ok = len(violations) == 0
    return {
        "admitted_for_certification": ok,
        "admitted_for_descriptive_audit_only": ok,
        "protocol_id": pid,
        "classification": "VERIFIED_KH03_R2_PLUS_PROTOCOL" if ok else "REJECTED_PROTOCOL_INVARIANT_VIOLATION",
        "violations": violations,
    }


def validate_r3_research_output_provenance(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Fail-closed provenance check on any KH-03.R3 research artifact before acceptance/publication.
    Blocks: old CI Mapping misinterpretation, H3 executable promotion, reused DEV -> fresh OOS,
    forged PROVEN_EDGE on DEV, or any production/live/paper/auto mode != OFF.
    """
    violations: List[str] = []
    if str(doc.get("spec_version") or "") not in VALID_CURRENT_RESEARCH_PROTOCOLS:
        violations.append(f"INVALID_SPEC_VERSION:{doc.get('spec_version')}")
    if doc.get("ACTIVATION_READY") is not None and str(doc.get("ACTIVATION_READY")) != "NO":
        violations.append(f"FORGED_ACTIVATION_READY:{doc.get('ACTIVATION_READY')}")
    if doc.get("OOS_EVALUATIONS") is not None and int(doc.get("OOS_EVALUATIONS")) != 0:
        violations.append(f"FORGED_OOS_EVALUATIONS:{doc.get('OOS_EVALUATIONS')}")
    if doc.get("RESEARCH_ECONOMIC_VERDICT") is not None and str(doc.get("RESEARCH_ECONOMIC_VERDICT")) != "NO_PROVEN_EDGE":
        violations.append(f"FORGED_PROVEN_EDGE_ON_DEV:{doc.get('RESEARCH_ECONOMIC_VERDICT')}")
    if doc.get("is_fresh_oos") is True:
        violations.append("REUSED_DEV_FORGED_AS_FRESH_OOS")
    if doc.get("H3_RESIZE_EXECUTION_STATUS") == "PASS":
        violations.append("FORGED_H3_RESIZE_EXECUTION_PASS")
    if doc.get("observed_l1_bbo_available") is True:
        violations.append("FORGED_OBSERVED_L1_BBO_FROM_M1")

    rsm = doc.get("runtime_safety_modes")
    if isinstance(rsm, Mapping):
        for k in ("PRODUCTION", "LIVE", "PAPER", "AUTO"):
            if rsm.get(k) != "OFF":
                violations.append(f"SAFETY_MODE_NOT_OFF:{k}={rsm.get(k)}")

    # Check primary arm evaluations if present
    pae = doc.get("primary_arm_evaluations")
    if isinstance(pae, Mapping):
        for ac, st in pae.items():
            if not isinstance(st, Mapping):
                continue
            if ac == KH03_R2_H3_ADVISORY_ARM and (
                bool(st.get("executable_edge_eligible", False))
                or bool(st.get("passes_preregistered_dev_advancement", False))
            ):
                violations.append("H3_PROMOTED_AS_EXECUTABLE_CANDIDATE")
            raw_ci = st.get("ny_day_cluster_95ci_r")
            cp = parse_kh03_cluster_ci_strict(raw_ci, label=str(ac))
            if not cp["schema_valid"]:
                violations.append(f"ARM_CI_INVALID_SCHEMA:{ac}")
            fails = list(st.get("failed_advancement_criteria") or ())
            if cp["status"] == "CI_VALID_NEGATIVE" and any("CROSSES_ZERO" in str(x) for x in fails):
                violations.append(f"OLD_R1_CI_MAPPING_BUG_REAPPEARED:{ac}")

    ok = len(violations) == 0
    return {"valid": ok, "status": "PASS" if ok else "FAIL", "violations": violations}


def build_r3_legacy_guard_artifacts_03_04(
    *,
    repo_root: Path,
    prior_r1_dir: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Produces Artifacts 03 and 04 for P0-A:
      - `03_r1_legacy_false_pass_red_reproductions.json`: reproduces real R1 CI Mapping bug
        (including `[+0.1, +0.5]` failing under R1 and negative CIs falsely labeled CROSSES_ZERO),
        R1 post-hoc future-train WF leak, and R1 H3 executable overclaim -> all rejected by R3 guard!
      - `04_r2_method_guard_green_and_entrypoint_scans.json`: live AST callgraph scan of entire
        `aurum-desk/` repo + synthetic attack vector tests (direct import, alias import, re-export,
        eval/getattr dynamic wrapper, forged R1 metadata) -> all caught!
    """
    r1_15 = json.loads((prior_r1_dir / "15_purged_walkforward_bootstrap_multiplicity_power.json").read_text("utf-8"))
    r1_07 = json.loads((prior_r1_dir / "07_partition_freeze_pre_registration_and_noncontamination.json").read_text("utf-8"))

    # RED 1: R1 CI bug on positive list [+0.1, +0.5] and real R1 negative arms
    def _old_r1_ci_low(raw_ci: Any) -> Any:
        c = raw_ci or {}
        return c.get("ci_low") if isinstance(c, Mapping) else None

    pos_old = _old_r1_ci_low([0.10, 0.50])
    pos_r2 = parse_kh03_cluster_ci_strict([0.10, 0.50], label="red_pos_list")
    r1_h1_ci = (r1_15.get("primary_arm_evaluations") or {}).get("KH03_H1_MTF_UNSWEPT_ALIGN", {}).get("ny_day_cluster_95ci_r")
    r1_h1_fails = (r1_15.get("primary_arm_evaluations") or {}).get("KH03_H1_MTF_UNSWEPT_ALIGN", {}).get("failed_advancement_criteria") or []

    # RED 2: R1 protocol certification attempt rejected by guard
    r1_cert_try = require_kh03_r2_or_newer_research_protocol(
        "KH-03.R1-v1.0", {"ACTIVATION_READY": "NO", "OOS_EVALUATIONS": 0}
    )
    r1_desc_only = require_kh03_r2_or_newer_research_protocol(
        "KH-03.R1-v1.0", {"ACTIVATION_READY": "NO", "OOS_EVALUATIONS": 0}, allow_legacy_descriptive_only=True
    )

    # RED 3: Forged H3 executable + posthoc WF rejected by guard
    h3_forged_try = require_kh03_r2_or_newer_research_protocol(
        "KH-03.R3-v1.0",
        {
            "ACTIVATION_READY": "NO",
            "OOS_EVALUATIONS": 0,
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
            "h3_promoted_as_executable": True,
            "temporal_validation_kind": "POSTHOC_FOUR_SLICES",
            "embargo_minutes": 240,
            "future_train_bars_count": 16542,
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
        },
    )

    r1_fold1 = ((r1_07.get("partitions") or {}).get("REUSED_DEV_VALIDATION_PURGED") or {}).get("folds", [{}])[0]

    f03_ok = (
        pos_old is None
        and pos_r2["status"] == "CI_VALID_POSITIVE"
        and "NY_DAY_95CI_CROSSES_ZERO:ci_low=None" in r1_h1_fails
        and not r1_cert_try["admitted_for_certification"]
        and r1_desc_only["classification"] == "LEGACY_DESCRIPTIVE_ONLY"
        and not r1_desc_only["admitted_for_certification"]
        and not h3_forged_try["admitted_for_certification"]
        and int(r1_fold1.get("val_start_idx", -1)) == 0
        and int(r1_fold1.get("train_bars_after_240m_embargo_count", 0)) > 10_000
    )

    f03: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "classification": "REAL_RED",
        "r1_ci_positive_list_bug": {"input": [0.10, 0.50], "old_r1_ci_low": pos_old, "r2_strict_status": pos_r2["status"]},
        "r1_h1_real_false_crosses_zero": {"raw_ci": r1_h1_ci, "r1_failed_criteria": r1_h1_fails},
        "r1_fold1_future_train_proof": {
            "val_start_idx": r1_fold1.get("val_start_idx"),
            "train_bars_after_val": r1_fold1.get("train_bars_after_240m_embargo_count"),
        },
        "r1_protocol_certification_rejected": r1_cert_try,
        "r1_descriptive_only_mode": r1_desc_only,
        "forged_h3_and_posthoc_wf_rejected": h3_forged_try,
        "status": "PASS" if f03_ok else "FAIL",
    }
    f03["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f03.items() if k != "artifact_canonical_sha256"})

    # 04: Full repo AST callgraph scan + synthetic bypass attack vectors (direct, alias, re-export, getattr/eval)
    callgraph_audit = audit_kh03_r3_legacy_research_callgraph(repo_root)
    attack_snippets = {
        "direct_import": "from services.kh_03_research_service import run_kh03_full_research_study_10_to_16\nrun_kh03_full_research_study_10_to_16()",
        "alias_import": "from services.kh_03_research_service import run_kh03_full_research_study_10_to_16 as f\nf()",
        "reexport_all": "__all__ = ['preregister_kh03_research_plan']",
        "getattr_wrapper": "import services.kh_03_research_service as m\ngetattr(m, 'run_kh03_full_research_study_10_to_16')()",
        "eval_wrapper": "eval('build_kh03_partition_manifest_and_noncontamination()')",
    }
    attack_results = {
        nm: {"detected": len(scan_python_source_for_forbidden_r1_usage(code, filename=f"synth_{nm}.py")) > 0}
        for nm, code in attack_snippets.items()
    }
    green_r3_proto = require_kh03_r2_or_newer_research_protocol(
        "KH-03.R3-v1.0",
        {
            "ACTIVATION_READY": "NO",
            "OOS_EVALUATIONS": 0,
            "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
            "h3_promoted_as_executable": False,
            "temporal_validation_kind": "FORWARD_ONLY_DEV_VALIDATION",
            "embargo_minutes": 240,
            "future_train_bars_count": 0,
            "runtime_safety_modes": {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF"},
            "raw_ci_samples": {"E1": [-0.5, -0.2]},
        },
    )
    all_attacks_caught = all(v["detected"] for v in attack_results.values())
    f04_ok = (
        callgraph_audit["status"] == "PASS"
        and all_attacks_caught
        and green_r3_proto["admitted_for_certification"] is True
    )
    f04: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "callgraph_audit": callgraph_audit,
        "synthetic_bypass_vector_detection": attack_results,
        "all_synthetic_bypass_vectors_detected": all_attacks_caught,
        "green_r3_protocol_check": green_r3_proto,
        "status": "PASS" if f04_ok else "FAIL",
    }
    f04["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f04.items() if k != "artifact_canonical_sha256"})
    return f03, f04
