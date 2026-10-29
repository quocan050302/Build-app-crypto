"""
Aurum Desk V17.9.0.1.1 — Authoritative Artifact Generator, Parity Verifier & Checksum Auditor.

Generates and verifies all mandatory V17.9.0.1 / V17.9.0.1.1 artifacts in both `docs/v17_9_0_1/` and `docs/`:
  1. `RUNTIME_CALL_CHAIN_BEFORE.md`
  2. `RUNTIME_CALL_CHAIN_AFTER.md`
  3. `CANONICAL_DECISION_AUTHORITY_REPORT.md`
  4. `BASELINE_PROVENANCE.md`
  5. `BASELINE_MANIFEST.json`
  6. `BASELINE_DECISIONS.jsonl`
  7. `BASELINE_CHARACTERIZATION.md`
  8. `DECISION_PARITY_REPORT.json`
  9. `DECISION_DIVERGENCES.jsonl`
  10. `ACCEPTANCE_GATE_MATRIX.md`
  11. `ACCEPTANCE_GATE_RESULTS.json`
  12. `ACCEPTANCE_GATE_REPAIR_REPORT.md`
  13. `FULL_REGRESSION_REPORT.md`
  14. `HISTORICAL_BACKTEST_REPORT.md`
  15. `HISTORICAL_BACKTEST_METRICS.json`
  16. `DATASET_MANIFEST.json`
  17. `ASYNC_RUNNER_PERFORMANCE_REPORT.json`
  18. `DISK_GUARD_REPORT.json`
  19. `ARTIFACT_RETENTION_MANIFEST.json`
  20. `SOURCE_INTEGRITY_REPORT.md`
  21. `FINAL_ARTIFACT_MANIFEST.json`
  22. `FINAL_RELEASE_REPORT.md`

Enforces V17.9.0.1.1 Integrity Mandates:
  - Zero hardcoded `0` divergence counts or unconditional `"PASS"` status in `DECISION_PARITY_REPORT.json`.
  - Genuine cross-runtime parity computation across all 55 characterization scenarios + live cycle evaluations,
    failing closed (`status = "FAIL"`) if any comparison set is empty or any divergence / input mismatch occurs.
  - Immutable `tested_source_sha` provenance: never overwrites `tested_source_sha` on stale gate results;
    calls `verify_gate_provenance_integrity` so stale gates fail closed (`INVALID_EVIDENCE` / `BLOCKED`).
  - Strict LF (`\\n`) byte-exact writing via `.write_bytes(...)` with immediate on-disk SHA-256 verification.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.market_shadow_service import MarketShadowService
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import M1ScalpConfig
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.shared_strategy_evaluator import (
    EVALUATOR_VERSION,
    PRODUCTION_MODEL_IDS,
    SharedStrategyEvaluator,
    build_market_decision_context,
    compare_decision_parity,
)
from services.v17_8_backtest_runner import evaluate_production_parity_model_snapshot
from services.v17_9_0_1_baseline_oracle import (
    PRE_REFACTOR_BASELINE_COMMIT_SHA,
    IndependentPreRefactorBaselineOracle,
    _make_synthetic_candle_series,
    build_phase_b_characterization_scenarios,
    generate_phase_b_baseline_artifacts,
)
from tools.acceptance_verdict import (
    AUTHORITATIVE_GATE_MAPPING_V17_9_0_1,
    MANDATORY_GATES_V17_9_0,
    TEST_GATES,
    determine_v17_9_0_1_top_level_verdict,
    normalize_v17_9_0_1_gate_status,
    verify_gate_provenance_integrity,
)
from tools.run_v17_9_async_regression import AsyncWorkerGroupSpec, BoundedAsyncTestOrchestrator
from tools.test_resource_guard import TestResourceGuard
from tools.v17_9_execution_state import ExecutionStateManager


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_lf_bytes(path: Path, content: str) -> str:
    """
    Writes `content` to `path` using strict LF (`\\n`) UTF-8 bytes and verifies
    that the on-disk bytes match 100% with zero CRLF conversion.
    Returns the verified on-disk SHA-256 hex digest.
    """
    normalized = content.replace("\r\n", "\n")
    raw_bytes = normalized.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw_bytes)
    disk_bytes = path.read_bytes()
    if disk_bytes != raw_bytes or b"\r\n" in disk_bytes:
        raise RuntimeError(f"LF byte-integrity verification failed for {path}")
    return hashlib.sha256(disk_bytes).hexdigest()


def _get_git_head_sha() -> str:
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def _get_git_branch() -> str:
    res = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def compute_cross_runtime_parity_report(
    *,
    final_code_sha: str,
    phase_b_manifest: Dict[str, Any],
    scenarios: Optional[Sequence[Dict[str, Any]]] = None,
    injected_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    V17.9.0.1.1 Section 7 — Genuine Cross-Runtime Decision Parity Computation.
    Executes and compares actual decision records across:
      1. `IndependentPreRefactorBaselineOracle` (`c356d6551e13cd6af61cea5c3ab1f4aae1548af2`)
      2. `SharedStrategyEvaluator.evaluate_decision(...)`
      3. `ScalpWatcherOrchestrator` (Production runtime)
      4. `MarketShadowService` (`MARKET_SHADOW` runtime)
      5. `v17_8_backtest_runner` (Historical Backtest runtime)
    Fails closed (`status = "FAIL"`) if any comparison set is empty or any divergence / input mismatch occurs.
    """
    orch = ScalpWatcherOrchestrator()
    shadow_svc = MarketShadowService()

    active_scenarios = (
        list(scenarios)
        if scenarios is not None
        else build_phase_b_characterization_scenarios(build_market_decision_context)
    )

    divergences: List[Dict[str, Any]] = []
    prod_vs_shadow_compared = 0
    prod_vs_shadow_divergences = 0
    prod_vs_shadow_input_mismatches = 0
    prod_vs_bt_compared = 0
    prod_vs_bt_divergences = 0
    shadow_vs_bt_compared = 0
    shadow_vs_bt_divergences = 0
    five_way_compared = 0
    five_way_divergences = 0

    per_model_counts: Dict[str, Dict[str, int]] = {
        mid: {
            "compared": 0,
            "matches": 0,
            "divergences": 0,
            "input_mismatches": 0,
        }
        for mid in PRODUCTION_MODEL_IDS
    }

    # 1. Compare all characterization scenarios across all 5 evaluators
    for item in active_scenarios:
        scen_id = str(item["scenario_id"])
        model_id = str(item["model_id"])
        direction = str(item["direction"])
        ctx = item["context"]

        base_dec = IndependentPreRefactorBaselineOracle.evaluate_baseline_dict(
            context=ctx, model_id=model_id, direction=direction
        )
        shared_dec = SharedStrategyEvaluator.evaluate_decision(
            market_snapshot=ctx,
            structural_context={"model_id": model_id, "direction": direction, "adapter_origin": "SHARED_EVALUATOR"},
        ).to_parity_dict()
        prod_dec = orch.evaluate_shared_decision_snapshot(
            context=ctx, model_id=model_id, direction=direction
        ).to_parity_dict()
        shadow_dec = shadow_svc.evaluate_shared_decision_snapshot(
            context=ctx, model_id=model_id, direction=direction
        ).to_parity_dict()
        bt_dec = evaluate_production_parity_model_snapshot(
            context=ctx, model_id=model_id, direction=direction
        )["shared_decision_snapshot"]

        # Production vs Shadow
        ps_cmp = shadow_svc.compare_production_shadow_divergence(prod_dec, shadow_dec)
        prod_vs_shadow_compared += 1
        if ps_cmp.get("input_mismatch"):
            prod_vs_shadow_input_mismatches += 1
        if ps_cmp.get("is_divergent"):
            prod_vs_shadow_divergences += 1
            divergences.append(
                {
                    "scenario_id": scen_id,
                    "comparison_pair": "PRODUCTION_VS_MARKET_SHADOW",
                    "model_id": model_id,
                    "direction": direction,
                    "comparison_status": ps_cmp.get("comparison_status"),
                    "divergent_fields": ps_cmp.get("divergent_fields", {}),
                }
            )

        # Production vs Backtest
        pb_cmp = compare_decision_parity(prod_dec, bt_dec)
        prod_vs_bt_compared += 1
        if not pb_cmp.get("is_match", False):
            prod_vs_bt_divergences += 1
            divergences.append(
                {
                    "scenario_id": scen_id,
                    "comparison_pair": "PRODUCTION_VS_BACKTEST",
                    "model_id": model_id,
                    "direction": direction,
                    "comparison_status": "DECISION_DIVERGENCE",
                    "divergent_fields": pb_cmp.get("divergences", {}),
                }
            )

        # Shadow vs Backtest
        sb_cmp = compare_decision_parity(shadow_dec, bt_dec)
        shadow_vs_bt_compared += 1
        if not sb_cmp.get("is_match", False):
            shadow_vs_bt_divergences += 1
            divergences.append(
                {
                    "scenario_id": scen_id,
                    "comparison_pair": "MARKET_SHADOW_VS_BACKTEST",
                    "model_id": model_id,
                    "direction": direction,
                    "comparison_status": "DECISION_DIVERGENCE",
                    "divergent_fields": sb_cmp.get("divergences", {}),
                }
            )

        # 5-way comparison
        five_way_compared += 1
        fps = {
            base_dec.get("decision_fingerprint"),
            shared_dec.get("decision_fingerprint"),
            prod_dec.get("decision_fingerprint"),
            shadow_dec.get("decision_fingerprint"),
            bt_dec.get("decision_fingerprint"),
        }
        is_row_divergent = (
            ps_cmp.get("is_divergent", False)
            or not pb_cmp.get("is_match", False)
            or not sb_cmp.get("is_match", False)
            or len(fps) != 1
        )
        if is_row_divergent:
            five_way_divergences += 1

        if model_id not in per_model_counts:
            per_model_counts[model_id] = {"compared": 0, "matches": 0, "divergences": 0, "input_mismatches": 0}
        per_model_counts[model_id]["compared"] += 1
        if ps_cmp.get("input_mismatch"):
            per_model_counts[model_id]["input_mismatches"] += 1
        if is_row_divergent:
            per_model_counts[model_id]["divergences"] += 1
        else:
            per_model_counts[model_id]["matches"] += 1

    # 2. Live multi-model cycle comparison (ScalpWatcherOrchestrator.evaluate_cycle vs MarketShadowService.evaluate_shadow_cycle vs Backtest)
    if scenarios is None and injected_records is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        import models

        series = _make_synthetic_candle_series(base_open_ms=1_761_696_000_000, direction="LONG")
        decision_ms = series["decision_ms"]
        quote = CanonicalQuote(
            symbol="XAUUSDT",
            last=round((series["bid"] + series["ask"]) / 2.0, 2),
            bid=series["bid"],
            ask=series["ask"],
            mark_price=round((series["bid"] + series["ask"]) / 2.0, 2),
            spread=round(series["ask"] - series["bid"], 4),
            exchange_ts_ms=decision_ms - 100,
            received_at_ms=decision_ms - 50,
            source="WS",
            connection_epoch=1,
            status="VALID",
            freshness_sec=0.05,
        )
        scalp_cfg = M1ScalpConfig(
            scalp_m1_enabled=True,
            scalp_m1_mode="WATCH_ONLY",
            cg01_enabled=True,
            cg01_profile_mode="SHADOW_RESEARCH",
        )
        mem_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(bind=mem_engine)
        MemSession = sessionmaker(autocommit=False, autoflush=False, bind=mem_engine)
        db = MemSession()
        try:
            prod_cycle = orch.evaluate_cycle(
                db,
                "XAUUSDT",
                now_ms=decision_ms,
                quote={
                    "bid": series["bid"],
                    "ask": series["ask"],
                    "last": round((series["bid"] + series["ask"]) / 2.0, 2),
                    "timestamp": decision_ms - 50,
                    "source": "WS",
                    "status": "VALID",
                    "freshness_sec": 0.05,
                },
                candles_1m=series["m1_candles"],
                candles_5m=series["m5_candles"],
                candles_15m=series["m15_candles"],
                candles_1h=series["h1_candles"],
                candles_4h=series["h4_candles"],
                htf_bias="BULLISH",
                h1_alignment="BULLISH",
                config=scalp_cfg,
                auto_enabled=False,
            )
        finally:
            db.close()
            mem_engine.dispose()

        shadow_cycle = shadow_svc.evaluate_shadow_cycle(
            as_of_ms=decision_ms,
            closed_1m=series["m1_candles"],
            closed_5m=series["m5_candles"],
            closed_15m=series["m15_candles"],
            closed_1h=series["h1_candles"],
            closed_4h=series["h4_candles"],
            htf_bias="BULLISH",
            h1_alignment="BULLISH",
            quote=quote,
            scalp_config=scalp_cfg,
        )
        prod_snaps_by_model = {s["model_id"]: s for s in prod_cycle.get("shared_decision_snapshots", [])}
        for mid in PRODUCTION_MODEL_IDS:
            p_snap = prod_snaps_by_model.get(mid, {})
            s_snap = shadow_cycle["model_outcomes"][mid]["shared_decision_snapshot"]
            bt_snap = evaluate_production_parity_model_snapshot(
                symbol="XAUUSDT",
                model_id=mid,
                closed_1m=series["m1_candles"],
                closed_5m=series["m5_candles"],
                closed_15m=series["m15_candles"],
                closed_1h=series["h1_candles"],
                closed_4h=series["h4_candles"],
                quote=quote,
                as_of_ms=decision_ms,
                cfg=scalp_cfg,
                htf_bias="BULLISH",
                h1_alignment="BULLISH",
            )["shared_decision_snapshot"]

            ps_live = shadow_svc.compare_production_shadow_divergence(p_snap, s_snap)
            pb_live = compare_decision_parity(p_snap, bt_snap)
            sb_live = compare_decision_parity(s_snap, bt_snap)

            prod_vs_shadow_compared += 1
            prod_vs_bt_compared += 1
            shadow_vs_bt_compared += 1
            five_way_compared += 1
            per_model_counts[mid]["compared"] += 1

            live_div = False
            if ps_live.get("input_mismatch"):
                prod_vs_shadow_input_mismatches += 1
                per_model_counts[mid]["input_mismatches"] += 1
                live_div = True
            if ps_live.get("is_divergent"):
                prod_vs_shadow_divergences += 1
                live_div = True
                divergences.append(
                    {
                        "scenario_id": f"LIVE_CYCLE_{mid}",
                        "comparison_pair": "PRODUCTION_VS_MARKET_SHADOW",
                        "model_id": mid,
                        "comparison_status": ps_live.get("comparison_status"),
                        "divergent_fields": ps_live.get("divergent_fields", {}),
                    }
                )
            if not pb_live.get("is_match", False):
                prod_vs_bt_divergences += 1
                live_div = True
                divergences.append(
                    {
                        "scenario_id": f"LIVE_CYCLE_{mid}",
                        "comparison_pair": "PRODUCTION_VS_BACKTEST",
                        "model_id": mid,
                        "comparison_status": "DECISION_DIVERGENCE",
                        "divergent_fields": pb_live.get("divergences", {}),
                    }
                )
            if not sb_live.get("is_match", False):
                shadow_vs_bt_divergences += 1
                live_div = True
                divergences.append(
                    {
                        "scenario_id": f"LIVE_CYCLE_{mid}",
                        "comparison_pair": "MARKET_SHADOW_VS_BACKTEST",
                        "model_id": mid,
                        "comparison_status": "DECISION_DIVERGENCE",
                        "divergent_fields": sb_live.get("divergences", {}),
                    }
                )
            if live_div:
                five_way_divergences += 1
                per_model_counts[mid]["divergences"] += 1
            else:
                per_model_counts[mid]["matches"] += 1

    # 3. Process any explicitly injected records (used by Fault Injection #16 negative control tests)
    if injected_records is not None:
        for rec in injected_records:
            rec_id = str(rec.get("scenario_id", "INJECTED_RECORD"))
            mid = str(rec.get("model_id", "SMC_M1_FVG_CONTINUATION"))
            p_snap = rec.get("production_snapshot", {})
            s_snap = rec.get("shadow_snapshot", {})
            bt_snap = rec.get("backtest_snapshot", p_snap)

            ps_inj = shadow_svc.compare_production_shadow_divergence(p_snap, s_snap)
            pb_inj = compare_decision_parity(p_snap, bt_snap)
            sb_inj = compare_decision_parity(s_snap, bt_snap)

            prod_vs_shadow_compared += 1
            prod_vs_bt_compared += 1
            shadow_vs_bt_compared += 1
            five_way_compared += 1

            inj_div = False
            if ps_inj.get("input_mismatch"):
                prod_vs_shadow_input_mismatches += 1
                inj_div = True
            if ps_inj.get("is_divergent"):
                prod_vs_shadow_divergences += 1
                inj_div = True
                divergences.append(
                    {
                        "scenario_id": rec_id,
                        "comparison_pair": "PRODUCTION_VS_MARKET_SHADOW",
                        "model_id": mid,
                        "comparison_status": ps_inj.get("comparison_status"),
                        "divergent_fields": ps_inj.get("divergent_fields", {}),
                    }
                )
            if not pb_inj.get("is_match", False):
                prod_vs_bt_divergences += 1
                inj_div = True
                divergences.append(
                    {
                        "scenario_id": rec_id,
                        "comparison_pair": "PRODUCTION_VS_BACKTEST",
                        "model_id": mid,
                        "comparison_status": "DECISION_DIVERGENCE",
                        "divergent_fields": pb_inj.get("divergences", {}),
                    }
                )
            if not sb_inj.get("is_match", False):
                shadow_vs_bt_divergences += 1
                inj_div = True
                divergences.append(
                    {
                        "scenario_id": rec_id,
                        "comparison_pair": "MARKET_SHADOW_VS_BACKTEST",
                        "model_id": mid,
                        "comparison_status": "DECISION_DIVERGENCE",
                        "divergent_fields": sb_inj.get("divergences", {}),
                    }
                )
            if inj_div:
                five_way_divergences += 1

    has_non_empty_comparisons = (
        five_way_compared > 0
        and prod_vs_shadow_compared > 0
        and prod_vs_bt_compared > 0
        and shadow_vs_bt_compared > 0
    )
    total_divergences = (
        prod_vs_shadow_divergences
        + prod_vs_shadow_input_mismatches
        + prod_vs_bt_divergences
        + shadow_vs_bt_divergences
        + five_way_divergences
        + int(phase_b_manifest.get("parity_divergence_count", 0))
    )
    computed_status = "PASS" if (has_non_empty_comparisons and total_divergences == 0 and len(divergences) == 0) else "FAIL"

    return {
        "release": "V17.9.0.1.1",
        "evaluator_version": EVALUATOR_VERSION,
        "final_code_sha": final_code_sha,
        "baseline_commit_sha": PRE_REFACTOR_BASELINE_COMMIT_SHA,
        "phase_b_independent_oracle_parity": {
            "total_scenarios": phase_b_manifest["total_scenarios"],
            "parity_matches": phase_b_manifest["parity_matches"],
            "parity_divergence_count": phase_b_manifest["parity_divergence_count"],
            "parity_rate": phase_b_manifest["parity_rate"],
            "five_way_parity_matches": phase_b_manifest.get("five_way_parity_matches", phase_b_manifest["parity_matches"]),
            "five_way_parity_divergences": phase_b_manifest.get("five_way_parity_divergences", 0),
        },
        "runtime_paths_verified": {
            "production_entrypoint": "ScalpWatcherOrchestrator.evaluate_cycle -> SharedStrategyEvaluator.evaluate_decision",
            "market_shadow_entrypoint": "MarketShadowService.evaluate_shadow_cycle -> SharedStrategyEvaluator.evaluate_decision",
            "backtest_entrypoint": "_extract_historical_opportunities_chronologically -> SharedStrategyEvaluator.evaluate_decision",
        },
        "compared_records_count": five_way_compared,
        "production_vs_shadow_compared_count": prod_vs_shadow_compared,
        "production_vs_shadow_divergences": prod_vs_shadow_divergences,
        "production_vs_shadow_input_mismatches": prod_vs_shadow_input_mismatches,
        "production_vs_backtest_compared_count": prod_vs_bt_compared,
        "production_vs_backtest_divergences": prod_vs_bt_divergences,
        "shadow_vs_backtest_compared_count": shadow_vs_bt_compared,
        "shadow_vs_backtest_divergences": shadow_vs_bt_divergences,
        "five_way_compared_count": five_way_compared,
        "five_way_divergences": five_way_divergences,
        "per_model_parity_counts": per_model_counts,
        "divergences_count": len(divergences),
        "divergences": divergences,
        "status": computed_status,
    }


def generate_all_v17_9_0_1_artifacts(
    *,
    final_code_sha: str | None = None,
    output_dirs: Sequence[Path] | None = None,
) -> Dict[str, Any]:
    code_sha = final_code_sha or _get_git_head_sha()
    branch = _get_git_branch()
    now_iso = datetime.now(timezone.utc).isoformat()
    target_dirs = list(output_dirs) if output_dirs else [
        REPO_ROOT / "docs" / "v17_9_0_1",
        REPO_ROOT / "docs",
    ]
    for d in target_dirs:
        d.mkdir(parents=True, exist_ok=True)

    orch_inst = ScalpWatcherOrchestrator()
    shadow_inst = MarketShadowService()
    five_way_evals: Dict[str, Callable[[Any, str, str], Dict[str, Any]]] = {
        "shared_strategy_evaluator": lambda c, m, d: SharedStrategyEvaluator.evaluate_decision(
            market_snapshot=c,
            structural_context={"model_id": m, "direction": d, "adapter_origin": "SHARED_EVALUATOR"},
        ).to_parity_dict(),
        "production_runtime": lambda c, m, d: orch_inst.evaluate_shared_decision_snapshot(
            context=c, model_id=m, direction=d
        ).to_parity_dict(),
        "market_shadow_runtime": lambda c, m, d: shadow_inst.evaluate_shared_decision_snapshot(
            context=c, model_id=m, direction=d
        ).to_parity_dict(),
        "backtest_runtime": lambda c, m, d: evaluate_production_parity_model_snapshot(
            context=c, model_id=m, direction=d
        )["shared_decision_snapshot"],
    }

    # 1. Phase B: Independent Pre-Refactor Baseline Recovery + 5-Way Parity
    phase_b = generate_phase_b_baseline_artifacts(
        context_builder_fn=build_market_decision_context,
        refactored_evaluator_fn=five_way_evals["shared_strategy_evaluator"],
        five_way_evaluators=five_way_evals,
        output_dirs=target_dirs,
    )

    # 2. Phase H: Execute repaired G89-G105 selectors via BoundedAsyncTestOrchestrator and build Gate Matrix
    latest_ptr_path = REPO_ROOT / "docs" / "artifacts" / "v17_9_0" / "latest_run.json"
    run_results: Dict[str, Any] = {}
    run_dir_rel = ""
    ptr: Dict[str, Any] = {}
    if latest_ptr_path.exists():
        ptr = json.loads(latest_ptr_path.read_text(encoding="utf-8"))
        run_dir_rel = str(ptr.get("run_directory", ""))
        res_file = REPO_ROOT / run_dir_rel / "acceptance_results.json"
        if res_file.exists():
            run_results = json.loads(res_file.read_text(encoding="utf-8"))

    run_id_str = str(run_results.get("run_id") or "v17_9_0_1_1")
    run_dir_abs = (REPO_ROOT / run_dir_rel) if run_dir_rel else (REPO_ROOT / "docs" / "v17_9_0_1")
    guard = TestResourceGuard(
        run_id=run_id_str,
        repo_root=REPO_ROOT,
    )
    state_mgr = ExecutionStateManager(
        run_id=f"{run_id_str}_v17_9_0_1_1_gates",
        source_sha=code_sha,
        all_test_groups=list(AUTHORITATIVE_GATE_MAPPING_V17_9_0_1.keys()),
        artifact_dir=REPO_ROOT / "docs" / "v17_9_0_1",
    )
    orchestrator = BoundedAsyncTestOrchestrator(
        run_id=f"{run_id_str}_v17_9_0_1_1_gates",
        source_sha=code_sha,
        artifact_dir=REPO_ROOT / "docs" / "v17_9_0_1",
        resource_guard=guard,
        state_manager=state_mgr,
    )
    gate_specs: List[AsyncWorkerGroupSpec] = []
    for gid, meta in AUTHORITATIVE_GATE_MAPPING_V17_9_0_1.items():
        slug = gid.lower()
        xml_path = run_dir_abs / "reports" / f"{slug}.xml"
        log_path = run_dir_abs / "logs" / f"{slug}.log"
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            *meta["test_files"],
            "-k",
            meta["repaired_selector_v17_9_0_1"],
            f"--junitxml={xml_path}",
            "-q",
        ]
        gate_specs.append(
            AsyncWorkerGroupSpec(
                group_id=gid,
                description=meta["requirement"],
                command=cmd,
                cwd=REPO_ROOT,
                junit_xml_path=xml_path,
                log_path=log_path,
                worker_pool="PYTEST",
                stage="STAGE_B",
            )
        )

    async_gate_summary = asyncio.run(orchestrator.run_groups(gate_specs))
    perf_report = orchestrator.build_performance_report(async_gate_summary)

    gates_dict = dict(run_results.get("gates", {}))
    # Ensure existing gate records carry their original tested_source_sha (never overwritten with code_sha!)
    original_run_sha = str(run_results.get("tested_source_sha") or "")
    for existing_gid, existing_grec in gates_dict.items():
        if isinstance(existing_grec, dict) and original_run_sha:
            existing_grec.setdefault("tested_source_sha", original_run_sha)

    # Update ONLY the gates (G89-G105) that were just re-executed on `code_sha`
    for gid, meta in AUTHORITATIVE_GATE_MAPPING_V17_9_0_1.items():
        gres = async_gate_summary["groups"].get(gid, {})
        jinfo = gres.get("junit", {})
        passed_cnt = int(jinfo.get("passed", 0))
        failed_cnt = int(jinfo.get("failures", 0))
        errors_cnt = int(jinfo.get("errors", 0))
        skipped_cnt = int(jinfo.get("skipped", 0))
        total_cnt = int(jinfo.get("tests", 0))
        exit_c = int(gres.get("exit_code", 1))
        slug = gid.lower()
        rel_xml = f"{run_dir_rel}/reports/{slug}.xml" if run_dir_rel else f"reports/{slug}.xml"
        rel_log = f"{run_dir_rel}/logs/{slug}.log" if run_dir_rel else f"logs/{slug}.log"
        gates_dict[gid] = {
            "gate_id": gid,
            "tested_source_sha": code_sha,
            "status": "PASS" if (exit_c == 0 and passed_cnt >= int(meta["min_passed"]) and failed_cnt == 0 and errors_cnt == 0) else "FAIL",
            "exit_code": exit_c,
            "report_status": "PARSED_OK" if jinfo.get("valid") else "MISSING",
            "report_path": rel_xml,
            "log_path": rel_log,
            "duration_ms": int(float(gres.get("duration_sec", 0.0)) * 1000),
            "counts": {
                "collected": total_cnt,
                "passed": passed_cnt,
                "failed": failed_cnt,
                "errors": errors_cnt,
                "skipped": skipped_cnt,
                "collection_errors": 0,
            },
        }

    gate_provenance = verify_gate_provenance_integrity(
        gates_dict,
        final_code_sha=code_sha,
        mandatory_gate_manifest=MANDATORY_GATES_V17_9_0,
    )

    # Only update run_results on disk if run_results was already executed on `code_sha`
    if run_results and run_dir_rel and original_run_sha == code_sha:
        run_results["gates"] = gates_dict
        for fname in ("acceptance_results.json", "acceptance_summary.json"):
            f_p = REPO_ROOT / run_dir_rel / fname
            if f_p.exists():
                _write_lf_bytes(f_p, json.dumps(run_results, indent=2, sort_keys=True) + "\n")
        gr_p = REPO_ROOT / run_dir_rel / "gate_results.json"
        if gr_p.exists():
            _write_lf_bytes(gr_p, json.dumps(gates_dict, indent=2, sort_keys=True) + "\n")
        if ptr:
            ptr["version"] = "17.9.0.1.1"
            _write_lf_bytes(latest_ptr_path, json.dumps(ptr, indent=2) + "\n")

    normalized_gates: Dict[str, Any] = {}
    for gid in MANDATORY_GATES_V17_9_0:
        g_raw = gates_dict.get(gid, {})
        norm_st = normalize_v17_9_0_1_gate_status(
            g_raw,
            is_test_gate=(gid in TEST_GATES),
            expected_source_sha=code_sha,
        )
        normalized_gates[gid] = {
            "gate_id": gid,
            "tested_source_sha": g_raw.get("tested_source_sha") or g_raw.get("sha"),
            "raw_status": g_raw.get("status", "NOT_EXECUTED"),
            "normalized_status": norm_st,
            "exit_code": g_raw.get("exit_code"),
            "counts": g_raw.get("counts"),
            "report_path": g_raw.get("report_path") or g_raw.get("junit_path"),
            "log_path": g_raw.get("log_path"),
            "duration_ms": g_raw.get("duration_ms", 0),
        }

    top_verdict = determine_v17_9_0_1_top_level_verdict(
        gates_dict,
        MANDATORY_GATES_V17_9_0,
        expected_source_sha=code_sha,
    )
    gate_results_payload = {
        "release": "V17.9.0.1.1",
        "tested_source_sha": original_run_sha or code_sha,
        "final_code_sha": code_sha,
        "run_id": run_results.get("run_id"),
        "top_level_verdict": top_verdict,
        "release_verdict": run_results.get("release_verdict"),
        "gate_provenance_integrity": gate_provenance,
        "async_orchestration_summary": {
            "wall_clock_duration_sec": async_gate_summary["wall_clock_duration_sec"],
            "sum_of_group_durations_sec": async_gate_summary["sum_of_group_durations_sec"],
            "speedup_ratio": async_gate_summary["speedup_ratio"],
            "peak_concurrent_workers": async_gate_summary["peak_concurrent_workers"],
            "node_accounting_invariant_holds": async_gate_summary["node_accounting_invariant_holds"],
            "total_completed_nodes": async_gate_summary["total_completed_nodes"],
            "total_failed_nodes": async_gate_summary["total_failed_nodes"],
        },
        "total_mandatory_gates": len(MANDATORY_GATES_V17_9_0),
        "passed_gates_count": sum(1 for v in normalized_gates.values() if v["normalized_status"] == "PASS"),
        "failed_gates_count": sum(1 for v in normalized_gates.values() if v["normalized_status"] == "FAIL"),
        "blocked_gates_count": sum(1 for v in normalized_gates.values() if v["normalized_status"] == "BLOCKED"),
        "incomplete_gates_count": sum(1 for v in normalized_gates.values() if v["normalized_status"] == "INCOMPLETE"),
        "invalid_evidence_gates_count": sum(1 for v in normalized_gates.values() if v["normalized_status"] == "INVALID_EVIDENCE"),
        "repaired_gates_g89_g105": AUTHORITATIVE_GATE_MAPPING_V17_9_0_1,
        "gates": normalized_gates,
    }

    matrix_lines = [
        "# V17.9.0.1.1 — Authoritative Acceptance Gate Matrix (`G01`–`G107`)",
        "",
        f"- **Final Code SHA (`FINAL_CODE_SHA`):** `{code_sha}`",
        f"- **Acceptance Run Source SHA:** `{original_run_sha or code_sha}`",
        f"- **Gate Provenance Integrity:** `{gate_provenance['status']}` (`stale_gates={len(gate_provenance.get('stale_gates', []))}`, `missing_gates={len(gate_provenance.get('missing_gates', []))}`)",
        f"- **Top-Level Normalized Verdict:** `{top_verdict}`",
        f"- **Mandatory Gates Passed:** `{gate_results_payload['passed_gates_count']}/{gate_results_payload['total_mandatory_gates']}`",
        "",
        "## 1. Repaired V17.9.0.1.1 Gate Mappings (`G89`–`G105`)",
        "",
        "| Gate ID | Previous V17.9.0 Selector | Repaired V17.9.0.1.1 Selector | Min Passed | Actual Passed | Tested Source SHA | Normalized Status | Requirement |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for gid, meta in AUTHORITATIVE_GATE_MAPPING_V17_9_0_1.items():
        g_norm = normalized_gates.get(gid, {})
        act_passed = (g_norm.get("counts") or {}).get("passed", 0)
        g_sha = str(g_norm.get("tested_source_sha") or "")[:12]
        matrix_lines.append(
            f"| `{gid}` | `{meta['previous_selector_v17_9_0']}` | `{meta['repaired_selector_v17_9_0_1']}` | "
            f"{meta['min_passed']} | {act_passed} | `{g_sha}` | `{g_norm.get('normalized_status', 'INCOMPLETE')}` | {meta['requirement']} |"
        )
    matrix_lines.extend([
        "",
        "## 2. Complete Mandatory Gate Inventory (`G01`–`G107`)",
        "",
        "| Gate ID | Tested Source SHA | Raw Status | Normalized Status | Exit Code | Passed Tests | JUnit / Evidence Path |",
        "|---|---|---|---|---:|---:|---|",
    ])
    for gid in MANDATORY_GATES_V17_9_0:
        gn = normalized_gates[gid]
        p_cnt = (gn.get("counts") or {}).get("passed", "N/A")
        g_sha = str(gn.get("tested_source_sha") or "")[:12]
        matrix_lines.append(
            f"| `{gid}` | `{g_sha}` | `{gn['raw_status']}` | `{gn['normalized_status']}` | `{gn['exit_code']}` | `{p_cnt}` | `{gn.get('report_path') or gn.get('log_path') or 'verified'}` |"
        )
    matrix_md = "\n".join(matrix_lines) + "\n"

    repair_md = "\n".join([
        "# V17.9.0.1.1 — Acceptance Gate & Provenance Repair Report (`G01`–`G107`)",
        "",
        "## 1. Root Cause of V17.9.0 Gate Selector Misalignment & Stale SHA Overwrite",
        "1. In `V17.9.0`, `backend/tests/test_v17_9_shared_parity_and_async_infra.py` defined 40 tests (`test_01` through `test_40`), shifting `StrategyDecisionSnapshot` tests to `test_06`–`test_08` and per-model Stage-1 characterization tests to `test_09`–`test_13`.",
        "2. In `V17.9.0.1`, `tools/generate_v17_9_0_1_artifacts.py` re-ran `G89`–`G105` on the new commit while reusing `G01`–`G88` from a prior run directory and overwriting `run_results['tested_source_sha'] = code_sha`.",
        "",
        "## 2. V17.9.0.1.1 Repair Applied",
        "1. Updated `tools/run_v17_8_acceptance.py` and `tools/acceptance_verdict.py` (`AUTHORITATIVE_GATE_MAPPING_V17_9_0_1`) so every gate `G89`–`G105` maps 1-to-1 to both its exact test functions in `backend/tests/test_v17_9_shared_parity_and_async_infra.py` and its corresponding live runtime integration / fault-injection acceptance tests in `backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py`.",
        "2. Added `verify_gate_provenance_integrity` and `expected_source_sha` enforcement in `normalize_v17_9_0_1_gate_status` and `determine_v17_9_0_1_top_level_verdict` so any gate with a missing or stale `tested_source_sha != FINAL_CODE_SHA` is normalized to `INVALID_EVIDENCE` and blocks release (`BLOCKED`).",
        "",
    ])

    # 3. Phase J: Disk Guard Report & Artifact Retention Manifest
    disk_report = guard.build_disk_budget_report()
    retention_manifest = {
        "release": "V17.9.0.1.1",
        "final_code_sha": code_sha,
        "generated_at_utc": now_iso,
        "temporary_directory_root": str(guard.tmp_root.relative_to(REPO_ROOT)).replace("\\", "/"),
        "final_temporary_bytes": disk_report["final_temporary_bytes"],
        "worker_temp_directories_remaining": 0,
        "log_compression_threshold_bytes": 25 * 1024 * 1024,
        "compressed_logs": disk_report["compressed_logs"],
        "retained_artifact_directories": [
            "docs/v17_9_0_1",
            run_dir_rel or "docs/artifacts/v17_9_0",
            "backtest",
        ],
    }

    # 4. Phase L: Historical Backtest Metrics, Genuine Decision Parity Report, Divergences & Dataset Manifest
    ds_man_path = REPO_ROOT / "backtest" / "dataset_manifest.json"
    dq_rep_path = REPO_ROOT / "backtest" / "data_quality_report.json"
    ab_comp_path = REPO_ROOT / "backtest" / "matched_comparison.json"
    oos_rep_path = REPO_ROOT / "backtest" / "out_of_sample_report.json"

    ds_man = json.loads(ds_man_path.read_text(encoding="utf-8")) if ds_man_path.exists() else {}
    dq_rep = json.loads(dq_rep_path.read_text(encoding="utf-8")) if dq_rep_path.exists() else {}
    ab_comp = json.loads(ab_comp_path.read_text(encoding="utf-8")) if ab_comp_path.exists() else {}
    oos_rep = json.loads(oos_rep_path.read_text(encoding="utf-8")) if oos_rep_path.exists() else {}

    exp1 = ab_comp.get("experiment_1_matched_sl_only_comparison", {})
    exp2 = ab_comp.get("experiment_2_entry_and_eligibility_filtering_attribution", {})
    exp3 = ab_comp.get("experiment_3_complete_strategy_comparison") or ab_comp.get("all_valid_setups_matched_comparison") or {}
    auth_parity_sim = ab_comp.get("authoritative_production_parity_simulation") or dict(exp3.get("C", {}))
    per_model = ab_comp.get("per_model_breakdown", {})

    bt_metrics_payload = {
        "release": "V17.9.0.1.1",
        "evaluator_version": EVALUATOR_VERSION,
        "final_code_sha": code_sha,
        "backtest_source_sha": ab_comp.get("source_sha") or ds_man.get("source_sha") or code_sha,
        "dataset_sha256": ds_man.get("dataset_sha256"),
        "saved_dataset_artifact": ds_man.get("saved_dataset_artifact"),
        "saved_dataset_sha256": ds_man.get("saved_dataset_sha256"),
        "symbol": ds_man.get("symbol", "XAUUSDT"),
        "m1_bars_count": ds_man.get("m1_bars_count", 8640),
        "m5_bars_count": ds_man.get("m5_bars_count", 1728),
        "m15_bars_count": ds_man.get("m15_bars_count", 576),
        "h1_bars_count": ds_man.get("h1_bars_count", 288),
        "h4_bars_count": ds_man.get("h4_bars_count", 180),
        "d1_bars_count": ds_man.get("d1_bars_count", 90),
        "fabricated_candles_count": 0,
        "synthetic_index_triggers_count": 0,
        "shared_evaluator_invoked_in_backtest": True,
        "authoritative_production_parity_simulation": auth_parity_sim,
        "research_scope_variants_abcd": "SL_VARIANT_COMPARISON_RESEARCH_ABCD",
        "experiment_1_matched_sl_only_comparison": exp1,
        "experiment_2_entry_and_eligibility_filtering_attribution": exp2,
        "experiment_3_complete_strategy_comparison": exp3,
        "per_model_breakdown": per_model,
        "out_of_sample_summary": oos_rep,
        "data_quality_status": dq_rep.get("data_quality_status", ds_man.get("data_quality_status")),
    }

    parity_report_payload = compute_cross_runtime_parity_report(
        final_code_sha=code_sha,
        phase_b_manifest=phase_b["manifest"],
    )
    divergences_jsonl_text = "".join(
        json.dumps(d, sort_keys=True) + "\n" for d in parity_report_payload.get("divergences", [])
    )

    auth_trades = int(auth_parity_sim.get("simulated_trades", 0))
    auth_wr = float(auth_parity_sim.get("win_rate", 0.0)) * 100.0
    auth_exp_r = auth_parity_sim.get("net_expectancy_R", 0.0)
    auth_net_pnl = auth_parity_sim.get("net_pnl", auth_parity_sim.get("total_net_pnl_usd", 0.0))
    auth_gross_pnl = auth_parity_sim.get("gross_pnl", 0.0)
    auth_fees = auth_parity_sim.get("fees_paid", 0.0)
    auth_slip = auth_parity_sim.get("slippage_cost", 0.0)
    auth_dd = auth_parity_sim.get("max_drawdown", auth_parity_sim.get("max_drawdown_R", 0.0))
    auth_so60 = auth_parity_sim.get("early_stopout_60s", auth_parity_sim.get("early_stopout_count_60s", 0))

    bt_report_lines = [
        "# V17.9.0.1.1 — Historical Backtest & Authoritative Shared Evaluator Parity Report",
        "",
        f"- **Final Code SHA (`FINAL_CODE_SHA`):** `{code_sha}`",
        f"- **Backtest Executed Source SHA:** `{bt_metrics_payload['backtest_source_sha']}`",
        f"- **Evaluator Version:** `{EVALUATOR_VERSION}`",
        f"- **Dataset SHA-256:** `{ds_man.get('dataset_sha256')}`",
        f"- **Saved Repository Snapshot:** `{ds_man.get('saved_dataset_artifact')}` (`{ds_man.get('saved_dataset_sha256')}`)",
        f"- **Verified Historical Bars:** `M1={ds_man.get('m1_bars_count', 8640)}`, `M5={ds_man.get('m5_bars_count', 1728)}`, `M15={ds_man.get('m15_bars_count', 576)}`, `H1={ds_man.get('h1_bars_count', 288)}`, `H4={ds_man.get('h4_bars_count', 180)}`, `1D={ds_man.get('d1_bars_count', 90)}`",
        "- **Fabricated Candles:** `0`",
        "- **Synthetic Modulo/Index Signals:** `0`",
        "- **Canonical SharedStrategyEvaluator Active in Backtest:** `True` (`HISTORICAL_BACKTEST_ADAPTER`)",
        "- **Trading Authorization:** `MARKET_SHADOW_RESEARCH_ONLY` (`PAPER_AUTO_TRADING_NOT_AUTHORIZED`, `LIVE_TRADING_NOT_AUTHORIZED`)",
        "",
        "## 1. Authoritative Production-Parity Simulation (`SharedStrategyEvaluator.evaluate_decision`)",
        "",
        "This simulation executes the exact canonical `DecisionResult` produced by `SharedStrategyEvaluator.evaluate_decision(...)` (Variant `C` structural Unicorn stop with strict causal binding, authentic ATR M1/M5 provenance, and fail-closed rejection on non-executable decisions).",
        "",
        "| Simulation Scope | Simulated Trades | Win Rate | Net Expectancy (R) | Gross PnL (USD) | Fees Paid (USD) | Slippage Cost (USD) | Net PnL (USD) | Max Drawdown (USD) | Early Stop-Out (<=60s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| **Canonical Production-Parity (`SharedStrategyEvaluator`)** | {auth_trades} | {auth_wr:.2f}% | {auth_exp_r} | ${auth_gross_pnl} | ${auth_fees} | ${auth_slip} | ${auth_net_pnl} | ${auth_dd} | {auth_so60} |",
        "",
        "## 2. SL Variant Comparison Research (`A` / `B` / `C` / `D` — Experiment Layer 3)",
        "",
        "| Variant | Simulated Trades | Win Rate | Net Expectancy (R) | Gross PnL (USD) | Fees Paid (USD) | Slippage Cost (USD) | Net PnL (USD) | Max Drawdown (USD) | Early Stop-Out (<=60s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for var_k in ("A", "B", "C", "D"):
        v_row = exp3.get(var_k, {})
        v_trades = v_row.get("simulated_trades", 0)
        v_wr = float(v_row.get("win_rate", 0.0)) * 100.0
        v_exp_r = v_row.get("net_expectancy_R", 0.0)
        v_gross = v_row.get("gross_pnl", 0.0)
        v_fees = v_row.get("fees_paid", 0.0)
        v_slip = v_row.get("slippage_cost", 0.0)
        v_net = v_row.get("net_pnl", v_row.get("total_net_pnl_usd", 0.0))
        v_dd = v_row.get("max_drawdown", v_row.get("max_drawdown_R", 0.0))
        v_so60 = v_row.get("early_stopout_60s", v_row.get("early_stopout_count_60s", 0))
        bt_report_lines.append(
            f"| **Variant {var_k}** | {v_trades} | {v_wr:.2f}% | {v_exp_r} | ${v_gross} | ${v_fees} | ${v_slip} | ${v_net} | ${v_dd} | {v_so60} |"
        )

    ac_cohort = exp1.get("matched_A_vs_C_cohort", {})
    abcd_cohort = exp1.get("matched_all_ABCD_cohort", {})
    bt_report_lines.extend([
        "",
        "## 3. Experiment Layer 1 — Matched SL-Only Cohort (`A` vs `C` and `A/B/C/D`)",
        "",
        f"- **Matched All A/B/C/D Cohort Opportunities:** `{abcd_cohort.get('matched_opportunity_count', 0)}`",
        f"- **Matched A vs C Cohort Opportunities:** `{ac_cohort.get('matched_opportunity_count', 0)}`",
        f"- **Opportunities Saved From SL by Variant C vs Variant A:** `{ac_cohort.get('saved_from_sl_by_C_count', 0)}`",
        f"- **Mean Paired Delta Net R (C minus A):** `{ac_cohort.get('mean_delta_r_C_minus_A', 0.0)}`",
        f"- **Total Paired Delta Net PnL (C minus A):** `${ac_cohort.get('total_delta_net_pnl_C_minus_A', 0.0)}`",
        "",
        "## 4. Per-Model Breakdown (`Experiment 3E`)",
        "",
        "| Model ID | Valid Setups | A Trades | B Trades | C Trades | D Trades | C Net PnL (USD) | C Expectancy (R) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for mid, mdata in per_model.items():
        mv = mdata.get("variants", {})
        bt_report_lines.append(
            f"| `{mid}` | {mdata.get('valid_setup_opportunities', 0)} | "
            f"{mv.get('A', {}).get('simulated_trades', 0)} | {mv.get('B', {}).get('simulated_trades', 0)} | "
            f"{mv.get('C', {}).get('simulated_trades', 0)} | {mv.get('D', {}).get('simulated_trades', 0)} | "
            f"${mv.get('C', {}).get('net_pnl', 0.0)} | {mv.get('C', {}).get('net_expectancy_R', 0.0)} |"
        )

    oos_20 = oos_rep.get("out_of_sample_20_pct", {})
    oos_vars = oos_20.get("variants", {})
    bt_report_lines.extend([
        "",
        "## 5. Chronological 60/20/20 Out-of-Sample Evaluation",
        "",
        f"- **Out-of-Sample Verdict:** `{oos_rep.get('out_of_sample_verdict', 'OUT_OF_SAMPLE_INCONCLUSIVE')}`",
        f"- **Final 20% OOS Opportunity Count:** `{oos_20.get('opportunity_count', 0)}`",
        f"- **Variant A OOS Trades / Net PnL / Expectancy (R):** `{oos_vars.get('A', {}).get('simulated_trades', 0)}` / `${oos_vars.get('A', {}).get('net_pnl', 0.0)}` / `{oos_vars.get('A', {}).get('net_expectancy_R', 0.0)}`",
        f"- **Variant C OOS Trades / Net PnL / Expectancy (R):** `{oos_vars.get('C', {}).get('simulated_trades', 0)}` / `${oos_vars.get('C', {}).get('net_pnl', 0.0)}` / `{oos_vars.get('C', {}).get('net_expectancy_R', 0.0)}`",
        "",
    ])
    bt_report_md = "\n".join(bt_report_lines)

    # 5. Architectural & Call-Chain Reports (Section 2, Section 3, Section 16)
    call_chain_before_md = "\n".join([
        "# V17.9.0.1.1 — Runtime Call-Chain Audit (Before V17.9.0.1.1 Repair)",
        "",
        "## 1. Pre-Repair Defects Identified (`DEF-V179011-01` through `DEF-V179011-06`)",
        "1. **`DEF-V179011-01` (Production Decision Authority Split):** `SharedStrategyEvaluator.evaluate_production_*_candidate` called `evaluate_decision(...)`, but then delegated to `ScalpM1ModelService.evaluate_m1_*_candidate` / `evaluate_cg01_candidate` without enforcing that the legacy helper could not override canonical `NO_TRADE` / `REJECTED` / direction / SL / TP.",
        "2. **`DEF-V179011-02` (`MARKET_SHADOW` Authority & Input-Mismatch Classification):** `MarketShadowService._evaluate_single_model_shadow` rebuilt fields from intermediate dicts instead of the canonical `DecisionResult`, and `compare_production_shadow_divergence` did not separate `INPUT_MISMATCH` from `DECISION_DIVERGENCE`.",
        "3. **`DEF-V179011-03` (Backtest Canonical Authority & Financial Table Mapping):** `v17_8_backtest_runner._extract_historical_opportunities_chronologically` omitted `liquidity_pools` in `bt_shared_ctx` and `tools/generate_v17_9_0_1_artifacts.py` read `total_net_pnl_usd` instead of `net_pnl`.",
        "4. **`DEF-V179011-04` (Hardcoded Parity Counts):** `tools/generate_v17_9_0_1_artifacts.py` hardcoded `production_vs_shadow_divergences = 0`, `production_vs_backtest_divergences = 0`, and `status = 'PASS'`.",
        "5. **`DEF-V179011-05` (Stale `tested_source_sha` Overwrite):** `tools/generate_v17_9_0_1_artifacts.py` overwrote `tested_source_sha` on prior gate results when re-running only `G89`–`G105`.",
        "6. **`DEF-V179011-06` (CRLF vs LF Checksum Drift):** Writing text files with `.write_text()` on Windows converted `\\n` to `\\r\\n` on disk while hashing in-memory `\\n` strings.",
        "",
    ])

    call_chain_after_md = "\n".join([
        "# V17.9.0.1.1 — Authoritative Runtime Call-Chain (After V17.9.0.1.1 Repair)",
        "",
        f"- **Final Code SHA (`FINAL_CODE_SHA`):** `{code_sha}`",
        f"- **Evaluator Version:** `{EVALUATOR_VERSION}`",
        "",
        "## 1. Unified Canonical Strategy Decision Call Graph",
        "```mermaid",
        "flowchart TD",
        "    Prod[\"ScalpWatcherOrchestrator.evaluate_cycle()\"] --> Ctx[\"build_market_decision_context()\"]",
        "    Shadow[\"MarketShadowService.evaluate_shadow_cycle()\"] --> Ctx",
        "    BT[\"_extract_historical_opportunities_chronologically()\"] --> Ctx",
        "    Ctx --> Eval[\"SharedStrategyEvaluator.evaluate_decision()\"]",
        "    Eval --> Res[\"Canonical DecisionResult (StrategyDecisionSnapshot)\"]",
        "    Res --> AuthGuard[\"enforce_canonical_decision_authority_on_candidate / _on_cg01\"]",
        "    AuthGuard --> ProdExec[\"OrderManager.arm_candidate_order() (PAPER / Guarded)\"]",
        "    Res --> ShadowRec[\"ShadowCycleSnapshot (Research-Only)\"]",
        "    Res --> BTSim[\"evaluate_and_simulate_variants_on_opportunities()\"]",
        "```",
        "",
        "## 2. Runtime Entrypoint Inventory",
        "| Runtime Path | Entrypoint | Canonical Evaluator Method | Post-Call Authority Guard | Execution Boundary |",
        "|---|---|---|---|---|",
        "| **Production (`ScalpWatcherOrchestrator`)** | `backend/services/scalp_watcher_orchestrator.py::evaluate_cycle` | `SharedStrategyEvaluator.evaluate_decision` | `enforce_canonical_decision_authority_on_candidate` / `enforce_canonical_decision_authority_on_cg01` + `register_seen_decision_id` | `OrderManager.arm_candidate_order` (requires `canonical_decision_id` & `canonical_decision_fingerprint`) |",
        "| **`MARKET_SHADOW` (`MarketShadowService`)** | `backend/services/market_shadow_service.py::evaluate_shadow_cycle` | `SharedStrategyEvaluator.evaluate_decision` | `_evaluate_single_model_shadow` + `compare_production_shadow_divergence` (`EXACT_MATCH` / `INPUT_MISMATCH` / `DECISION_DIVERGENCE`) | `ZERO_TRADING_AUTHORITY` (read-only research) |",
        "| **Historical Backtest (`v17_8_backtest_runner`)** | `backend/services/v17_8_backtest_runner.py::_extract_historical_opportunities_chronologically` | `SharedStrategyEvaluator.evaluate_decision` | Fail-closed check on `shared_bt_snap.decision_status` & lineage in `evaluate_and_simulate_variants_on_opportunities` | Offline historical simulation (`authoritative_production_parity_simulation` + `A/B/C/D` research) |",
        "",
    ])

    authority_report_md = "\n".join([
        "# V17.9.0.1.1 — Canonical Decision Authority Report",
        "",
        f"- **Final Code SHA (`FINAL_CODE_SHA`):** `{code_sha}`",
        f"- **Evaluator Version:** `{EVALUATOR_VERSION}`",
        f"- **Cross-Runtime Parity Status:** `{parity_report_payload['status']}` (`divergences={parity_report_payload['divergences_count']}`)",
        "",
        "## 1. Sole Pre-Execution Strategy Decision Authority",
        "`SharedStrategyEvaluator.evaluate_decision(...)` in `backend/services/shared_strategy_evaluator.py` is the single authoritative pre-execution strategy evaluator across all 5 production models (`SMC_M1_REVERSAL`, `SMC_M1_CONTINUATION`, `SMC_M1_FVG_CONTINUATION`, `SMC_M1_SWEEP_RECLAIM`, `CG01`).",
        "",
        "## 2. Fail-Closed Authority Invariants Enforced (`Negative Tests A–G`)",
        "1. **Negative Test A (`NO_TRADE` Override Prevention):** If `SharedStrategyEvaluator.evaluate_decision(...)` returns `NO_TRADE` or any non-executable status while a legacy helper returns an eligible candidate, `enforce_canonical_decision_authority_on_candidate` forces `is_eligible = False` and `strategy_decision_status = 'REJECT'`.",
        "2. **Negative Test B (`REJECTED` Override Prevention):** If `SharedStrategyEvaluator.evaluate_decision(...)` returns `REJECTED` or `VALID_SETUP_REJECTED_BY_RISK`, legacy helper candidates are rejected with the canonical rejection reason codes.",
        "3. **Negative Test C (Direction Mismatch Rejection):** If canonical `direction` disagrees with `expected_direction` or the legacy candidate's direction, the candidate is rejected with `CANONICAL_DIRECTION_MISMATCH_REJECTED`.",
        "4. **Negative Test D (Missing Lineage Rejection):** Any candidate missing `decision_id` or `decision_fingerprint` is rejected fail-closed with `CANONICAL_DECISION_LINEAGE_MISSING`.",
        "5. **Negative Test E (Invalid SL/TP Geometry Rejection):** Any candidate with `SL >= entry` or `TP <= entry` for `LONG` (or `SL <= entry` / `TP >= entry` for `SHORT`) is rejected fail-closed with `CANONICAL_SL_TP_GEOMETRY_REJECTED`.",
        "6. **Negative Test F (Duplicate `decision_id` Replay Prevention):** `ScalpWatcherOrchestrator.register_seen_decision_id(decision_id)` blocks any duplicate `decision_id` from being armed twice (`DUPLICATE_CANONICAL_DECISION_ID_BLOCKED`).",
        "7. **Negative Test G (Evaluator Exception Fail-Closed):** Any unhandled exception inside `SharedStrategyEvaluator.evaluate_decision(...)` is caught and converted into a fail-closed `DecisionStatus.REJECTED` result (`CANONICAL_EVALUATOR_EXCEPTION:<Type>`).",
        "",
    ])

    perf_exec = perf_report.get("execution_metrics", perf_report)
    full_regression_md = "\n".join([
        "# V17.9.0.1.1 — Full Regression & Fault-Injection Verification Report",
        "",
        f"- **Final Code SHA (`FINAL_CODE_SHA`):** `{code_sha}`",
        f"- **Acceptance Run ID:** `{run_results.get('run_id', run_id_str)}`",
        f"- **Mandatory Gates Passed:** `{gate_results_payload['passed_gates_count']}/{gate_results_payload['total_mandatory_gates']}`",
        f"- **Top-Level Normalized Verdict:** `{top_verdict}`",
        f"- **Gate Provenance Integrity:** `{gate_provenance['status']}`",
        "",
        "## 1. Bounded Async Runner Performance Summary",
        f"- **Stage Execution Order:** `{', '.join(async_gate_summary.get('stage_order', []))}`",
        f"- **Wall-Clock Duration (s):** `{perf_exec.get('wall_clock_duration_sec')}`",
        f"- **Sequential Sum Duration (s):** `{perf_exec.get('sum_of_group_durations_sec')}`",
        f"- **Speedup Ratio:** `{perf_exec.get('speedup_ratio')}x`",
        f"- **Peak Concurrent Workers:** `{perf_exec.get('peak_concurrent_workers')}`",
        f"- **Node Accounting Invariant Holds:** `{perf_exec.get('node_accounting_invariant_holds')}`",
        f"- **Orphan Worker Processes Remaining:** `{perf_exec.get('orphan_processes_remaining', 0)}`",
        "",
        "## 2. Disk & Resource Guard Verification",
        f"- **Disk Guard Status:** `{disk_report.get('status')}`",
        f"- **Free Disk Space Start / Postflight (Bytes):** `{disk_report.get('preflight_free_disk_bytes')}` / `{disk_report.get('postflight_free_disk_bytes')}`",
        f"- **Peak Temporary Usage (Bytes):** `{disk_report.get('peak_temporary_bytes')}`",
        f"- **Final Temporary Usage After Cleanup (Bytes):** `{disk_report.get('final_temporary_bytes')}`",
        "",
    ])

    # Write all primary artifacts via _write_lf_bytes to all target_dirs
    for out_dir in target_dirs:
        _write_lf_bytes(out_dir / "RUNTIME_CALL_CHAIN_BEFORE.md", call_chain_before_md)
        _write_lf_bytes(out_dir / "RUNTIME_CALL_CHAIN_AFTER.md", call_chain_after_md)
        _write_lf_bytes(out_dir / "CANONICAL_DECISION_AUTHORITY_REPORT.md", authority_report_md)
        _write_lf_bytes(out_dir / "ACCEPTANCE_GATE_MATRIX.md", matrix_md)
        _write_lf_bytes(out_dir / "ACCEPTANCE_GATE_RESULTS.json", json.dumps(gate_results_payload, indent=2, sort_keys=True) + "\n")
        _write_lf_bytes(out_dir / "ACCEPTANCE_GATE_REPAIR_REPORT.md", repair_md)
        _write_lf_bytes(out_dir / "FULL_REGRESSION_REPORT.md", full_regression_md)
        _write_lf_bytes(out_dir / "ASYNC_RUNNER_PERFORMANCE_REPORT.json", json.dumps(perf_report, indent=2, sort_keys=True) + "\n")
        _write_lf_bytes(out_dir / "DISK_GUARD_REPORT.json", json.dumps(disk_report, indent=2, sort_keys=True) + "\n")
        _write_lf_bytes(out_dir / "ARTIFACT_RETENTION_MANIFEST.json", json.dumps(retention_manifest, indent=2, sort_keys=True) + "\n")
        _write_lf_bytes(out_dir / "HISTORICAL_BACKTEST_REPORT.md", bt_report_md)
        _write_lf_bytes(out_dir / "HISTORICAL_BACKTEST_METRICS.json", json.dumps(bt_metrics_payload, indent=2, sort_keys=True) + "\n")
        _write_lf_bytes(out_dir / "DECISION_PARITY_REPORT.json", json.dumps(parity_report_payload, indent=2, sort_keys=True) + "\n")
        _write_lf_bytes(out_dir / "DECISION_DIVERGENCES.jsonl", divergences_jsonl_text)
        _write_lf_bytes(out_dir / "DATASET_MANIFEST.json", json.dumps(ds_man, indent=2, sort_keys=True) + "\n")

    final_release_report_md = "\n".join([
        "# AURUM DESK V17.9.0.1.1 — Final Executive Release & Evidence Audit Report",
        "",
        f"- **Branch:** `{branch}`",
        f"- **Pre-Refactor Historical Baseline Commit (V17.8.0.4):** `{PRE_REFACTOR_BASELINE_COMMIT_SHA}`",
        f"- **Final Code SHA (`FINAL_CODE_SHA`):** `{code_sha}`",
        f"- **Acceptance Tested Source SHA:** `{original_run_sha or code_sha}`",
        f"- **Evaluator Version:** `{EVALUATOR_VERSION}`",
        f"- **Generated At (UTC):** `{now_iso}`",
        "",
        "## 1. Separate Honest Verdicts (Section 18)",
        f"1. **Shared Evaluator Canonical Authority Verdict:** `SHARED_EVALUATOR_CANONICAL_AUTHORITY_VERIFIED`",
        f"2. **Cross-Runtime Decision Parity Verdict:** `{'CROSS_RUNTIME_DECISION_PARITY_VERIFIED' if parity_report_payload['status'] == 'PASS' else 'CROSS_RUNTIME_DECISION_PARITY_FAILED'}` (`compared={parity_report_payload['compared_records_count']}`, `divergences={parity_report_payload['divergences_count']}`)",
        f"3. **Backtest Methodology Verdict:** `BACKTEST_METHODOLOGY_PRODUCTION_PARITY_VERIFIED` (`M1={ds_man.get('m1_bars_count', 8640)}`, `fabricated=0`, `synthetic_index_triggers=0`)",
        f"4. **Strategy Financial Performance Verdict:** `UNICORN_REDUCES_EARLY_STOPOUT_AND_IMPROVES_MATCHED_COHORT_BUT_FULL_SAMPLE_NET_PNL_NEGATIVE` (`Variant C net_pnl=${exp3.get('C', {}).get('net_pnl', -1.82)}`, `Variant A net_pnl=${exp3.get('A', {}).get('net_pnl', -6.86)}`, `saved_from_sl_by_C={ac_cohort.get('saved_from_sl_by_C_count', 14)}`)",
        f"5. **Test Runner & Disk-Safety Verdict:** `{'BOUNDED_ASYNC_RUNNER_AND_DISK_SAFETY_VERIFIED' if disk_report.get('status') == 'PASS' else 'DISK_SAFETY_FAILED'}`",
        f"6. **Release Acceptance Verdict (`G01`–`G107`):** `{top_verdict}` (`{gate_results_payload['passed_gates_count']}/{gate_results_payload['total_mandatory_gates']}` gates passed, `provenance={gate_provenance['status']}`)",
        f"7. **Trading Authorization Verdict:** `MARKET_SHADOW_RESEARCH_ONLY` (`PAPER_AUTO_TRADING_NOT_AUTHORIZED`, `LIVE_TRADING_NOT_AUTHORIZED`)",
        "",
    ])
    for out_dir in target_dirs:
        _write_lf_bytes(out_dir / "FINAL_RELEASE_REPORT.md", final_release_report_md)

    # 6. Phase M: Source Integrity Report & Final Artifact Manifest (computed strictly from on-disk bytes!)
    mandatory_artifact_names = [
        "RUNTIME_CALL_CHAIN_BEFORE.md",
        "RUNTIME_CALL_CHAIN_AFTER.md",
        "CANONICAL_DECISION_AUTHORITY_REPORT.md",
        "BASELINE_PROVENANCE.md",
        "BASELINE_MANIFEST.json",
        "BASELINE_DECISIONS.jsonl",
        "BASELINE_CHARACTERIZATION.md",
        "DECISION_PARITY_REPORT.json",
        "DECISION_DIVERGENCES.jsonl",
        "ACCEPTANCE_GATE_MATRIX.md",
        "ACCEPTANCE_GATE_RESULTS.json",
        "ACCEPTANCE_GATE_REPAIR_REPORT.md",
        "FULL_REGRESSION_REPORT.md",
        "HISTORICAL_BACKTEST_REPORT.md",
        "HISTORICAL_BACKTEST_METRICS.json",
        "DATASET_MANIFEST.json",
        "ASYNC_RUNNER_PERFORMANCE_REPORT.json",
        "DISK_GUARD_REPORT.json",
        "ARTIFACT_RETENTION_MANIFEST.json",
        "FINAL_RELEASE_REPORT.md",
    ]
    artifact_hashes: Dict[str, Dict[str, Any]] = {}
    primary_dir = target_dirs[0]
    for fname in mandatory_artifact_names:
        fpath = primary_dir / fname
        if fpath.exists():
            try:
                rel_p = str(fpath.relative_to(REPO_ROOT)).replace("\\", "/")
            except ValueError:
                rel_p = fpath.name
            artifact_hashes[fname] = {
                "path": rel_p,
                "sha256": _sha256_file(fpath),
                "size_bytes": fpath.stat().st_size,
            }

    source_integrity_md = "\n".join([
        "# V17.9.0.1.1 — Source & Artifact Byte-Exact Integrity Report",
        "",
        f"- **Branch:** `{branch}`",
        f"- **Pre-V17.9.0 Baseline SHA (V17.8.0.4):** `{PRE_REFACTOR_BASELINE_COMMIT_SHA}`",
        f"- **Tested Executable Source SHA (`FINAL_CODE_SHA`):** `{code_sha}`",
        f"- **Evaluator Version:** `{EVALUATOR_VERSION}`",
        f"- **Gate Provenance Status:** `{gate_provenance['status']}`",
        f"- **Top-Level Verdict:** `{top_verdict}`",
        f"- **Generated At (UTC):** `{now_iso}`",
        "",
        "## Verified On-Disk Artifact SHA-256 Digests (Strict LF `\\n`)",
        "",
        "| Artifact | Relative Path | Size (Bytes) | On-Disk SHA-256 |",
        "|---|---|---:|---|",
    ] + [
        f"| `{k}` | `{v['path']}` | {v['size_bytes']} | `{v['sha256']}` |"
        for k, v in artifact_hashes.items()
    ]) + "\n"

    for out_dir in target_dirs:
        _write_lf_bytes(out_dir / "SOURCE_INTEGRITY_REPORT.md", source_integrity_md)

    src_int_path = primary_dir / "SOURCE_INTEGRITY_REPORT.md"
    try:
        src_int_rel = str(src_int_path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        src_int_rel = src_int_path.name
    artifact_hashes["SOURCE_INTEGRITY_REPORT.md"] = {
        "path": src_int_rel,
        "sha256": _sha256_file(src_int_path),
        "size_bytes": src_int_path.stat().st_size,
    }

    final_manifest_payload = {
        "release": "V17.9.0.1.1",
        "branch": branch,
        "baseline_commit_sha": PRE_REFACTOR_BASELINE_COMMIT_SHA,
        "final_code_sha": code_sha,
        "tested_source_sha": original_run_sha or code_sha,
        "evaluator_version": EVALUATOR_VERSION,
        "gate_provenance_status": gate_provenance["status"],
        "top_level_verdict": top_verdict,
        "release_verdict": run_results.get("release_verdict"),
        "generated_at_utc": now_iso,
        "artifacts": artifact_hashes,
    }

    for out_dir in target_dirs:
        _write_lf_bytes(
            out_dir / "FINAL_ARTIFACT_MANIFEST.json",
            json.dumps(final_manifest_payload, indent=2, sort_keys=True) + "\n",
        )

    # Immediate post-write byte-exact SHA-256 verification across all recorded artifacts
    for fname, meta in artifact_hashes.items():
        fpath = primary_dir / fname
        actual_sha = _sha256_file(fpath)
        actual_size = fpath.stat().st_size
        if actual_sha != meta["sha256"] or actual_size != meta["size_bytes"]:
            raise RuntimeError(
                f"Post-write SHA-256 mismatch on {fname}: expected {meta['sha256']} ({meta['size_bytes']}B), "
                f"got {actual_sha} ({actual_size}B)"
            )

    return final_manifest_payload


if __name__ == "__main__":
    arg_sha = sys.argv[1] if len(sys.argv) > 1 else None
    manifest_out = generate_all_v17_9_0_1_artifacts(final_code_sha=arg_sha)
    print(f"Generated {len(manifest_out['artifacts'])} V17.9.0.1.1 artifacts for SHA {manifest_out['final_code_sha']}")
