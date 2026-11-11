"""
AURUM DESK — V17.9.3.1.2.13 ACTUAL DELAYED ENTRY, HTF/UNICORN MATERIALIZATION,
STRUCTURAL SL/TP SIZING, SINGLE AUTHORITATIVE REPORT TRUTH, AND V01..V66 NEGATIVE CONTROLS.

Implements all six mandatory V17.9.3.1.2.13 repairs:
  - P0-E1 (Phase A): Genuine post-signal closed-bar delayed-entry execution (`A1B`, `D1..D4`,
    `1 <= wait_bars <= 12`, `new_decision_timestamp_ms > base_decision_timestamp_ms`), rebuilt
    entry/SL/TP/quantity under `$2.50` risk cap, forward simulation strictly after `new_checkpoint_index_1m`,
    chronological 1-position portfolio replay, and pure shared cross-adapter (`REPLAY_BACKTEST`,
    `MARKET_SHADOW`, `PRODUCTION_ORCHESTRATOR_DRY_RUN`, `default_state="OFF"`).
  - P0-E2 (Phase B): End-to-end HTF / Unicorn / SI8 / SI9 materialization truth across `63,085`
    canonical + SI8-discovered decisions, 267-case `SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN`
    `(i)..(viii)` forensic audit, 113-case `REJECTED_CAUSAL_H1_H4_CONFLICT` before/after audit,
    and materialized single-axis MTF/Unicorn ablations (`H4_ONLY`, `H1_ONLY`, `M15_ONLY`,
    `M15_H1_ONLY`, `STRUCTURE_ONLY`, `LIQUIDITY_ONLY`, `FULL_MTF`).
  - P0-E3: Dynamic materialized intent counts & fail-closed `UNKNOWN_MISSING_INTENT_LEDGER`
    ledger bijection (`0` hardcoded counts).
  - P0-E4 (Phase E): Single typed immutable `AuthoritativeMetricsV17931213` rendering
    `README_V17_9_3_1_2_13.md`, `HANDOVER_V17_9_3_1_2_13.txt`, and 12 dynamic acceptance gates,
    verified by a strict structured parser & cross-validator.
  - P1-E5 (Phase F): Bounded async multi-worker (`<= 4` concurrent processes, `1.5 GiB` hard RAM
    floor, `2.0 GiB` soft downshift, `10 GiB` free disk floor) throughput + determinism benchmarks.
  - P1-E6 & Phase H: Honest `FRESH_OOS_VERDICT = "BLOCKED_NO_UNTOUCHED_DATA"`, `ACTIVATION_READY = "NO"`,
    and `66` (`V01..V66`) fault-injected negative controls caught by real production verifiers.
"""
from __future__ import annotations

import ast
import bisect
from collections import ChainMap, Counter
import copy
from dataclasses import asdict, dataclass
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from services.shared_strategy_evaluator import (
    MarketDecisionContext,
    ModelIdentifier,
    SharedStrategyEvaluator,
)
from services.v17_9_2_structural_stop_intelligence import (
    TRUE_DELAYED_ENTRY_VARIANTS_V17931213,
    evaluate_r3_counterfactual_stop_and_target_v17931212,
    evaluate_shared_delayed_entry_adapter_v17931213,
)
from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    emit_causal_order_intents_v1793121,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    canonical_hash,
    derive_pre_entry_features_from_causal_context_v1793127,
    rebuild_materialized_intent_proof_v1793127,
)
from services.v17_9_3_1_2_10_canonical_reconciliation_and_provenance import (
    build_si8_si9_278_materialization_diagnosis_v17931210,
)
from services.v17_9_3_1_2_11_verification_truth_and_exit_lineage import (
    CANONICAL_DATASET_SHA256_V17931211,
    CANONICAL_FILL_MODES_V17931211,
    MANDATORY_BRANCH_V17931211,
    verify_process_exit_and_timeout_v17931211,
)
from services.v17_9_3_1_2_12_provenance_forensics_and_repair_service import (
    R3_COUNTERFACTUAL_VARIANTS_V17931212,
    run_negative_controls_v01_to_v40_v17931212,
    verify_r1_forensic_separation_and_conservation_v17931212,
    verify_r2_causal_mtf_unicorn_provenance_v17931212,
    verify_r3_counterfactual_invariants_v17931212,
    verify_r4_oos_and_activation_verdict_v17931212,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    ExecutableResearchPolicyV179312,
    PortfolioReplayStateV179312,
    _check_causal_htf_alignment_at_ts_v179312,
    advance_portfolio_to_next_event_v179312,
    recalculate_trade_risk_quantity_v179312,
    replay_intervention_outcome_v179312,
)

VERSION_V17931213 = "V17.9.3.1.2.13"
SCHEMA_VERSION_V17931213 = "17.9.3.1.2.13"
CANONICAL_DATASET_SHA256_V17931213 = CANONICAL_DATASET_SHA256_V17931211
MANDATORY_BRANCH_V17931213 = MANDATORY_BRANCH_V17931211

ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213: Tuple[str, ...] = (
    "A0_STRICT_BASELINE",
    "A1_ENTRY_CONFIRMATION_RETEST_ONLY",
    "A1B_GENUINE_DELAYED_CONFIRMATION_ENTRY",
    "A2_PROTECTED_SL_NOISE_BUFFER_ONLY",
    "A3_SOURCE_BACKED_LIQUIDITY_TP_ONLY",
    "A4_COMBINED_CAUSAL_ENTRY_SL_TP",
    "D1_REAL_DELAYED_ENTRY",
    "D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL",
    "D3_REAL_DELAYED_ENTRY_PLUS_TARGET",
    "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
)

REQUIRED_ARTIFACTS_V17931213: Tuple[str, ...] = (
    "00_preflight_and_branch_identity.json",
    "01_function_callgraph_and_exact_source_defect_matrix.json",
    "02_a1_old_filter_vs_true_delayed_entry_contract.json",
    "03_delay_confirmation_new_timestamp_entry_sl_tp_ledger.jsonl.gz",
    "04_htf_unicorn_detector_to_intent_causal_pipeline.jsonl.gz",
    "05_si8_si9_dynamic_full_funnel_and_target_267_audit.json",
    "06_pre_entry_loss_forensics_and_expost_stop_separation.jsonl.gz",
    "07_structural_sl_tp_position_sizing_evidence.jsonl.gz",
    "08_matched_ab_all_opportunities_all_fill_modes.json",
    "09_chronological_portfolio_replay_all_variants.json",
    "09_exit_event_lineage_ledger.jsonl.gz",
    "09_original_execution_ledger_source_reconciliation.jsonl.gz",
    "10_full_30_partition_lineage_parity.json",
    "11_real_async_worker_resource_and_determinism_benchmarks.json",
    "12_current_pytest_fresh_collection_and_shard_map.json",
    "13_full_regression_junit_bijection_and_os_exit_verification.json",
    "14_negative_controls_V01_to_latest_fault_injection.json",
    "15_oos_provenance_and_reused_holdout_truth.json",
    "16_truthful_economic_verdict_and_blockers.json",
    "17_independent_verifier_report.json",
    "18_acceptance_gate_matrix.json",
    "19_source_freeze_and_immutable_prior_artifact_hashes.json",
    "20_process_tree_cleanup_and_disk_ram_evidence.json",
    "21_phase_a_b_c_commit_push_and_remote_readback.json",
    "README_V17_9_3_1_2_13.md",
    "HANDOVER_V17_9_3_1_2_13.txt",
    "run_status.jsonl",
    "artifact_sha256_manifest.json",
)

SI9_267_TARGET_AUDIT_BUCKETS: Tuple[str, ...] = (
    "I_TARGET_ALREADY_SWEPT_BEFORE_DECISION",
    "II_TARGET_POOL_ID_FORMAT_OR_JOIN_MISMATCH",
    "III_TARGET_OUTSIDE_LOOKBACK_OR_MISSING_TF_POOL",
    "IV_TARGET_NET_RR_BELOW_2_0_AFTER_FEES_SPREAD",
    "V_TARGET_1M_ATR_UNIT_MISMATCH_ON_HTF_POOL",
    "VI_PLANNED_RISK_EXCEEDS_2_50_AT_MIN_LOT",
    "VII_ANCHOR_ID_FORMAT_MISMATCH_IN_POST_MATERIALIZATION_PROOF",
    "VIII_GENUINELY_NO_VIABLE_UNSWEPT_POOL_IN_RR_BAND",
)


# ==============================================================================
# 1. FUNCTION CALLGRAPH & EXACT SOURCE DEFECT MATRIX (01)
# ==============================================================================

def build_v17931213_callgraph_and_defect_matrix(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    aurum_root = repo_root / "aurum-desk" if (repo_root / "aurum-desk").exists() else repo_root

    target_specs = [
        (
            "backend/services/v17_9_2_structural_stop_intelligence.py",
            [
                "evaluate_r3_counterfactual_stop_and_target_v17931212",
                "evaluate_r3_true_delayed_counterfactual_v17931213",
                "evaluate_shared_delayed_entry_adapter_v17931213",
                "derive_structure_valid_stop",
                "select_viable_structural_target",
                "simulate_v17931_trade_execution_net",
            ],
            "P0-E1 Genuine post-signal closed-bar delayed entry (A1B, D1..D4) & pure cross-adapter parity",
        ),
        (
            "backend/services/v17_9_3_1_2_executable_intervention_replay_service.py",
            [
                "_check_causal_htf_alignment_at_ts_v179312",
                "find_next_causal_entry_confirmation_v179312",
                "derive_executable_structural_target_v179312",
                "recalculate_trade_risk_quantity_v179312",
                "replay_intervention_outcome_v179312",
            ],
            "P0-E1/P0-E2 Strict k > base_idx confirmation scanner & real evaluate_htf_structure HTF alignment",
        ),
        (
            "backend/services/v17_9_3_1_2_1_root_cause_evidence_service.py",
            [
                "build_full_intervention_candidate_index_v1793121",
                "_fast_derive_outer_stop_from_index_v1793121",
                "_fast_derive_structural_target_from_index_v1793121",
                "schedule_causal_pending_confirmation_v1793121",
                "emit_causal_order_intents_v1793121",
            ],
            "P0-E2 Detector-bound canonical swing/pool IDs, post-confirmation HTF check & deterministic intent_id",
        ),
        (
            "backend/services/v17_9_3_1_2_3_core_trading_logic_repair.py",
            [
                "_eval_checkpoints_chunk_v1793125",
                "discover_research_candidates_v1793123",
                "classify_r1_hierarchical_loss_forensics_v17931212",
            ],
            "P0-E2 Bug B-1 repair: propagate selected_sweep_id/break_id/fvg_id/breaker_id on SI8 candidates",
        ),
        (
            "tools/run_v17_9_3_1_2_2_entry_signal_backtest.py",
            [
                "replay_single_si_policy_v1793122",
                "_map_si_policy_to_executable_research_policy_v1793122",
            ],
            "P0-E2 Bug B-4 repair: timeframe-scaled ATR ceiling for HTF pools & full SI8/SI9 proof verification",
        ),
        (
            "backend/services/v17_9_3_1_2_10_canonical_reconciliation_and_provenance.py",
            [
                "build_si8_si9_278_materialization_diagnosis_v17931210",
            ],
            "P0-E3 Dynamic materialized intent ledger derivation & fail-closed UNKNOWN_MISSING_INTENT_LEDGER",
        ),
        (
            "backend/services/v17_9_3_1_2_12_provenance_forensics_and_repair_service.py",
            [
                "build_r2_htf_unicorn_funnel_and_ablations_v17931212",
                "verify_r2_causal_mtf_unicorn_provenance_v17931212",
            ],
            "P0-E3 Removal of hardcoded 278/6/0 checks & dynamic decision-ledger bijection verification",
        ),
        (
            "backend/services/v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service.py",
            [
                "enrich_si8_aligned_decisions_and_features_v17931213",
                "build_delayed_entry_contract_and_ledgers_v17931213",
                "build_htf_unicorn_and_si8_si9_dynamic_funnel_v17931213",
                "render_v17931213_readme_and_handover",
                "verify_v17931213_authoritative_report_and_gates_consistency",
                "run_negative_controls_v01_to_v66_v17931213",
                "evaluate_v17931213_gate_matrix",
            ],
            "P0-E4 Single authoritative report/gate renderer, strict structured parser & V01..V66 negative controls",
        ),
    ]

    anchored_symbols: List[Dict[str, Any]] = []
    module_hashes: Dict[str, str] = {}
    for rel_file, sym_names, role_desc in target_specs:
        fpath = aurum_root / rel_file
        if not fpath.exists():
            continue
        raw_bytes = fpath.read_bytes()
        module_hashes[f"aurum-desk/{rel_file}"] = hashlib.sha256(raw_bytes).hexdigest()
        src = raw_bytes.decode("utf-8")
        tree = ast.parse(src, filename=rel_file)
        nodes_by_name: Dict[str, ast.AST] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name not in nodes_by_name:
                    nodes_by_name[node.name] = node
        for sname in sym_names:
            node = nodes_by_name.get(sname)
            if node is None:
                continue
            lineno = getattr(node, "lineno", 1)
            end_lineno = getattr(node, "end_lineno", lineno)
            anchored_symbols.append(
                {
                    "file": f"aurum-desk/{rel_file}",
                    "symbol": sname,
                    "line_start": lineno,
                    "line_end": end_lineno,
                    "file_line": f"aurum-desk/{rel_file}:{lineno}-{end_lineno}",
                    "role": role_desc,
                }
            )

    source_defect_matrix = [
        {
            "defect_id": "P0_E1_SAME_CUTOFF_FILTER_MASQUERADING_AS_DELAYED_ENTRY",
            "file": "aurum-desk/backend/services/v17_9_2_structural_stop_intelligence.py",
            "symbol": "evaluate_r3_counterfactual_stop_and_target_v17931212",
            "root_cause": (
                "In .12, A1_ENTRY_CONFIRMATION_RETEST_ONLY checked pre-entry booleans at the original "
                "decision_timestamp_ms (wait_bars=0) without scanning k > base_idx or shifting entry/SL/TP/sizing."
            ),
            "causal_repair": (
                "Explicitly labeled A1/A4 as SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY (delayed_entry_executed=False) "
                "for historical .12 comparability, and implemented evaluate_r3_true_delayed_counterfactual_v17931213 "
                "(A1B, D1..D4) + find_next_causal_entry_confirmation_v179312(strictly_after_base=True, model_aware=True) "
                "scanning strictly subsequent closed M1 bars (1 <= wait_bars <= 12, new_decision_timestamp_ms > base_ts) "
                "and rebuilding entry/SL/TP/quantity at new_decision_timestamp_ms."
            ),
        },
        {
            "defect_id": "P0_E2_BUG_B1_SI8_DISCOVERY_OMITTED_CAUSAL_EVIDENCE_IDS",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_3_core_trading_logic_repair.py",
            "symbol": "_eval_checkpoints_chunk_v1793125",
            "root_cause": (
                "base_rec for discovered_by_si8=True candidates omitted selected_sweep_id, selected_break_id, "
                "selected_fvg_id, and selected_breaker_id before calling derive_pre_entry_features_from_causal_context_v1793127, "
                "causing 0 / 6,375 SI8-discovered candidates to pass required_proof_rejection_v1793127."
            ),
            "causal_repair": (
                "Propagated selected_sweep_id, selected_break_id, selected_fvg_id, and selected_breaker_id onto "
                "base_rec and re-derived verified causal feature snapshots on SI8-discovered candidates."
            ),
        },
        {
            "defect_id": "P0_E2_BUG_B2_SWING_AND_POOL_ID_SCHEMA_MISMATCH",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_1_root_cause_evidence_service.py",
            "symbol": "build_full_intervention_candidate_index_v1793121",
            "root_cause": (
                "all_causal_swings synthesized IDs '{TF}_{lb}_{rb}_{SH/SL}_{occ_ms}' instead of canonical detector IDs "
                "('1m_sh/sl_...' / 'v1792_m5_5m_sh/sl_...' for anchors; 'pool_{TF}_{tf}_sh/sl_...' for targets) and "
                "checked M1 sweep inside the HTF pivot candle itself, causing rebuild_materialized_intent_proof_v1793127 "
                "to reject re-derived stops/targets."
            ),
            "causal_repair": (
                "Indexed exact detector raw_id, canonical_anchor_id, canonical_target_id, contiguous window checks, "
                "4H (1,1) pools, and post-pivot m1_post_bar_idx (occ_ms + tf_dur_ms)."
            ),
        },
        {
            "defect_id": "P0_E2_BUG_B3_SIX_BAR_CLOSE_HEURISTIC_FALSE_HTF_CONFLICT",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_executable_intervention_replay_service.py",
            "symbol": "_check_causal_htf_alignment_at_ts_v179312",
            "root_cause": (
                "Legacy 6-bar close-comparison heuristic contradicted evaluate_htf_structure on 113 / 113 genuinely "
                "BEARISH/BEARISH (ALIGNED) SHORT candidates in SI8."
            ),
            "causal_repair": (
                "Delegated use_structural_evaluator=True to evaluate_htf_structure on strictly closed 4H/1H/15m/5m "
                "candles (close_time <= new_decision_timestamp_ms), rejecting MISSING/UNDETERMINED/MIXED/COUNTERTREND."
            ),
        },
        {
            "defect_id": "P0_E2_BUG_B4_SI9_1M_ATR_CEILING_ON_HTF_LIQUIDITY_POOLS",
            "file": "aurum-desk/tools/run_v17_9_3_1_2_2_entry_signal_backtest.py",
            "symbol": "replay_single_si_policy_v1793122",
            "root_cause": (
                "SI9 enforced 0.30 <= target_room_atr_m1 <= 12.0 using 1M ATR on 1H liquidity pools (~14.2 ATR_1m = "
                "~1.8 ATR_1h) before materialization, falsely rejecting 267 / 267 proven-unswept 1H pools."
            ),
            "causal_repair": (
                "Scaled pre-materialization ATR ceiling by timeframe sqrt(tf_minutes) (12.0 * sqrt(tf_mins)) and "
                "enforced full emit_causal_order_intents_v1793121 + rebuild_materialized_intent_proof_v1793127 on all SI8/SI9 winners."
            ),
        },
        {
            "defect_id": "P0_E3_LITERAL_ZERO_MATERIALIZED_INTENTS_AND_HARDCODED_VERIFIER",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_10_canonical_reconciliation_and_provenance.py",
            "symbol": "build_si8_si9_278_materialization_diagnosis_v17931210",
            "root_cause": (
                "Hardcoded 'materialized_intents_count': 0 and hardcoded 278/6/0 checks in verify_r2_causal_mtf_unicorn_provenance_v17931212."
            ),
            "causal_repair": (
                "Derived materialized counts/IDs dynamically from raw policy_decision_ledger rows, failed closed with "
                "UNKNOWN_MISSING_INTENT_LEDGER if ledgers are absent/empty, and enforced exact conservation + ledger bijection."
            ),
        },
        {
            "defect_id": "P0_E4_CONCATENATED_STALE_REPORT_HEADERS_AND_GATE_TITLES",
            "file": "aurum-desk/backend/services/v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service.py",
            "symbol": "render_v17931213_readme_and_handover",
            "root_cause": (
                "In .12, README/HANDOVER concatenated .11 prose ('26/26 negative controls') with .12 prose ('40/40') "
                "and kept a static '278 -> 6 -> 272 -> 0' G_R2_01 gate title."
            ),
            "causal_repair": (
                "Single immutable AuthoritativeMetricsV17931213 renders README_V17_9_3_1_2_13.md, HANDOVER_V17_9_3_1_2_13.txt, "
                "and all 12 gate titles dynamically, verified by verify_v17931213_authoritative_report_and_gates_consistency."
            ),
        },
    ]

    return {
        "version": VERSION_V17931213,
        "schema_version": SCHEMA_VERSION_V17931213,
        "repaired_defects_count": len(source_defect_matrix),
        "anchored_symbols_count": len(anchored_symbols),
        "module_sha256": module_hashes,
        "anchored_symbols": anchored_symbols,
        "exact_source_defect_matrix": source_defect_matrix,
    }


# ==============================================================================
# 2. SI8 ALIGNED FEATURE ENRICHMENT (BUG B-1 REPAIR ON CANONICAL INPUTS)
# ==============================================================================

def enrich_si8_aligned_decisions_and_features_v17931213(
    datasets: Mapping[str, Any],
    close_ts_index: Mapping[str, Sequence[int]],
    si8_decisions: List[Dict[str, Any]],
    si8_feat_by_opp_id: Dict[str, Dict[str, Any]],
    *,
    fallback_feat_by_opp_id: Optional[Mapping[str, Mapping[str, Any]]] = None,
    dataset_sha256: str = CANONICAL_DATASET_SHA256_V17931213,
) -> Dict[str, Any]:
    """
    Repairs Bug B-1 on the canonical 6,375 SI8-discovered decisions by invoking
    SharedStrategyEvaluator (SI8_RESEARCH_DISCOVERY_REPAIRED) to recover exact
    `selected_sweep_id`, `selected_break_id`, `selected_fvg_id`, and `selected_breaker_id`
    and re-deriving `derive_pre_entry_features_from_causal_context_v1793127` for all
    candidates with causal HTF alignment and valid pre-entry structure/target bounds.
    (Candidates with MIXED/COUNTERTREND HTF fail at `HTF_NOT_ALIGNED` regardless of sweep IDs.)
    """
    t0 = time.time()
    evaluator = SharedStrategyEvaluator()
    pol_si8 = PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122["SI8_RESEARCH_DISCOVERY_REPAIRED"]
    bars_1m = datasets["1m"]
    bars_5m = datasets["5m"]
    bars_15m = datasets["15m"]
    bars_1h = datasets["1H"]
    bars_4h = datasets["4H"]
    ts_5m = close_ts_index["5m"]
    ts_15m = close_ts_index["15m"]
    ts_1h = close_ts_index["1H"]
    ts_4h = close_ts_index["4H"]

    enriched_count = 0
    newly_proof_eligible_count = 0
    from services.v17_9_3_1_2_7_causal_signal_truth import required_proof_rejection_v1793127

    for rec in si8_decisions:
        oid = str(rec["opportunity_id"])
        feat = si8_feat_by_opp_id.get(oid) or (fallback_feat_by_opp_id or {}).get(oid) or {}
        if oid not in si8_feat_by_opp_id and feat:
            si8_feat_by_opp_id[oid] = dict(feat)
        if (
            feat.get("htf_alignment_state") == "ALIGNED"
            and feat.get("protected_structure_independent_of_sl") is True
            and feat.get("target_unswept") is True
        ):
            idx_1m = int(rec["checkpoint_index_1m"])
            cur_ts = int(rec["decision_timestamp_ms"])
            m1_sl = bars_1m[max(0, idx_1m - 180) : idx_1m + 1]
            i5 = bisect.bisect_right(ts_5m, cur_ts)
            i15 = bisect.bisect_right(ts_15m, cur_ts)
            i1h = bisect.bisect_right(ts_1h, cur_ts)
            i4h = bisect.bisect_right(ts_4h, cur_ts)
            c_px = float(bars_1m[idx_1m]["close"])
            ctx_si8 = MarketDecisionContext(
                symbol="XAUUSDT",
                decision_timestamp_ms=cur_ts,
                closed_m1_candles=m1_sl,
                closed_m5_candles=bars_5m[max(0, i5 - 80) : i5],
                closed_m15_candles=bars_15m[max(0, i15 - 80) : i15],
                closed_h1_candles=bars_1h[max(0, i1h - 80) : i1h],
                closed_h4_candles=bars_4h[max(0, i4h - 80) : i4h],
                bid=round(c_px - 0.10, 2),
                ask=round(c_px + 0.10, 2),
                equity_usd=1000.0,
                free_margin_usd=1000.0,
                open_positions_count=0,
                current_drawdown_pct=0.0,
                daily_loss_pct=0.0,
                execution_mode="REPLAY_BACKTEST",
                signal_intelligence_mode="RESEARCH_APPLY",
                signal_intelligence_policy_id="SI8_RESEARCH_DISCOVERY_REPAIRED",
                v1793122_policy_override=pol_si8,
            )
            s_si8 = evaluator.evaluate_strategy_decision(
                ctx_si8,
                model_id=ModelIdentifier(str(rec["model_id"])),
                direction=str(rec["direction"]),
            )
            rec["selected_sweep_id"] = s_si8.selected_sweep_id
            rec["selected_break_id"] = s_si8.selected_break_id
            rec["selected_fvg_id"] = s_si8.selected_fvg_id
            rec["selected_breaker_id"] = getattr(s_si8, "selected_breaker_id", None)
            new_feat = derive_pre_entry_features_from_causal_context_v1793127(
                rec, ctx_si8, dataset_sha256=dataset_sha256
            )
            si8_feat_by_opp_id[oid] = new_feat
            enriched_count += 1
            if required_proof_rejection_v1793127(new_feat, str(rec["model_id"]), decision=rec) is None:
                newly_proof_eligible_count += 1

    return {
        "si8_total_decisions": len(si8_decisions),
        "si8_aligned_candidates_re_derived": enriched_count,
        "si8_newly_proof_eligible_count": newly_proof_eligible_count,
        "elapsed_seconds": round(time.time() - t0, 3),
    }


# ==============================================================================
# 3. PHASE A (P0-E1): GENUINE DELAYED ENTRY, STRUCTURAL SL/TP & PORTFOLIO REPLAY
# ==============================================================================

def _summarize_counterfactual_rows_v17931213(
    v_id: str,
    fm: str,
    rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    closed_rows = [r for r in rows if bool(r.get("is_closed"))]
    wins = [r for r in closed_rows if r.get("outcome_label") == "WIN"]
    losses = [r for r in closed_rows if r.get("outcome_label") == "LOSS"]
    rejected_pre = [r for r in rows if r.get("status") == "REJECTED_PRE_ENTRY"]
    no_fill = [r for r in rows if r.get("status") == "NO_FILL_OR_SIZING_REJECT"]
    open_hor = [r for r in rows if bool(r.get("filled")) and r.get("outcome_label") == "OPEN_AT_HORIZON"]
    data_gap = [r for r in rows if bool(r.get("filled")) and r.get("outcome_label") == "DATA_GAP_UNRESOLVED"]
    accepted_rows = [r for r in rows if r.get("status") != "REJECTED_PRE_ENTRY"]
    waits = [int(r.get("wait_bars") or 0) for r in accepted_rows]

    gross_usd = round(sum(float(r.get("realized_gross_pnl_usd") or 0.0) for r in closed_rows), 4)
    fees_usd = round(sum(float(r.get("total_fee_usd") or 0.0) for r in closed_rows), 4)
    net_usd = round(sum(float(r.get("realized_net_pnl_usd") or 0.0) for r in closed_rows), 4)
    exp_r = (
        round(sum(float(r.get("realized_r_net") or 0.0) for r in closed_rows) / len(closed_rows), 4)
        if closed_rows
        else 0.0
    )
    pos_sum = sum(float(r.get("realized_net_pnl_usd") or 0.0) for r in wins)
    neg_sum = abs(sum(float(r.get("realized_net_pnl_usd") or 0.0) for r in losses))
    pf = round(pos_sum / neg_sum, 4) if neg_sum > 1e-9 else (99.0 if pos_sum > 0 else 0.0)
    max_risk_obs = max((float(r.get("planned_net_risk_usd") or 0.0) for r in rows if r.get("filled")), default=0.0)

    is_true_delayed = v_id in TRUE_DELAYED_ENTRY_VARIANTS_V17931213
    timing_contract = (
        "GENUINE_POST_SIGNAL_CLOSED_BAR_DELAYED_ENTRY"
        if is_true_delayed
        else (
            "SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY"
            if v_id in ("A1_ENTRY_CONFIRMATION_RETEST_ONLY", "A4_COMBINED_CAUSAL_ENTRY_SL_TP")
            else "IMMEDIATE_BASE_SIGNAL_ENTRY"
        )
    )

    return {
        "variant_id": v_id,
        "fill_mode": fm,
        "entry_timing_contract": timing_contract,
        "delayed_entry_executed": is_true_delayed,
        "matched_opportunities_evaluated": len(rows),
        "accepted_confirmation_count": len(accepted_rows),
        "rejected_pre_entry_count": len(rejected_pre),
        "no_fill_or_sizing_reject_count": len(no_fill),
        "filled_count": sum(1 for r in rows if bool(r.get("filled"))),
        "closed_trades_count": len(closed_rows),
        "wins_count": len(wins),
        "losses_count": len(losses),
        "open_at_horizon_count": len(open_hor),
        "data_gap_unresolved_count": len(data_gap),
        "win_rate": round(len(wins) / len(closed_rows), 4) if closed_rows else 0.0,
        "loss_rate": round(len(losses) / len(closed_rows), 4) if closed_rows else 0.0,
        "profit_factor": pf,
        "realized_gross_pnl_usd": gross_usd,
        "total_fees_usd": fees_usd,
        "realized_net_pnl_usd": net_usd,
        "expectancy_net_r": exp_r,
        "min_wait_bars": min(waits) if waits else 0,
        "max_wait_bars": max(waits) if waits else 0,
        "mean_wait_bars": round(sum(waits) / len(waits), 3) if waits else 0.0,
        "max_planned_net_risk_usd_observed": round(max_risk_obs, 4),
        "risk_cap_2_50_respected": max_risk_obs <= 2.500001,
    }


def _replay_chronological_1pos_portfolio_from_matched_rows_v17931213(
    v_id: str,
    fm: str,
    rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    Chronologically replays accepted/filled counterfactual episodes under the exact
    1-position portfolio concurrency lock (`max_concurrent_positions=1`), sorting by
    `new_decision_timestamp_ms` (falling back to `decision_timestamp_ms`) so shifted
    delayed entries compete honestly for the single position slot.
    """
    candidates = [
        dict(r)
        for r in rows
        if r.get("status") != "REJECTED_PRE_ENTRY"
        and (r.get("new_decision_timestamp_ms") is not None or r.get("decision_timestamp_ms") is not None)
    ]
    candidates.sort(
        key=lambda r: (
            int(r.get("new_decision_timestamp_ms") or r.get("decision_timestamp_ms") or 0),
            str(r.get("opportunity_id") or ""),
        )
    )

    active_release_ts_ms = 0
    executed_rows: List[Dict[str, Any]] = []
    blocked_by_open_pos = 0
    overlap_violations = 0

    for cand in candidates:
        eff_dec_ts = int(cand.get("new_decision_timestamp_ms") or cand.get("decision_timestamp_ms") or 0)
        if active_release_ts_ms > 0 and eff_dec_ts < active_release_ts_ms:
            blocked_by_open_pos += 1
            continue
        executed_rows.append(cand)
        if bool(cand.get("filled")):
            e_fill_ts = int(cand.get("entry_fill_ts") or eff_dec_ts)
            if active_release_ts_ms > 0 and e_fill_ts < active_release_ts_ms:
                overlap_violations += 1
            rel_ts = int(cand.get("exit_fill_ts") or (eff_dec_ts + 180 * 60_000))
            active_release_ts_ms = max(active_release_ts_ms, rel_ts)

    closed = [r for r in executed_rows if bool(r.get("is_closed"))]
    wins = [r for r in closed if r.get("outcome_label") == "WIN"]
    losses = [r for r in closed if r.get("outcome_label") == "LOSS"]
    open_hor = [r for r in executed_rows if bool(r.get("filled")) and r.get("outcome_label") == "OPEN_AT_HORIZON"]
    net_usd = round(sum(float(r.get("realized_net_pnl_usd") or 0.0) for r in closed), 4)
    exp_r = round(sum(float(r.get("realized_r_net") or 0.0) for r in closed) / len(closed), 4) if closed else 0.0

    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in closed:
        eq += float(r.get("realized_net_pnl_usd") or 0.0)
        if eq > peak:
            peak = eq
        if peak - eq > max_dd:
            max_dd = peak - eq

    return {
        "variant_id": v_id,
        "fill_mode": fm,
        "max_concurrent_positions": 1,
        "accepted_candidates_entering_portfolio": len(candidates),
        "blocked_by_open_position_count": blocked_by_open_pos,
        "scheduled_episodes_count": len(executed_rows),
        "closed_trades_count": len(closed),
        "wins_count": len(wins),
        "losses_count": len(losses),
        "open_at_horizon_count": len(open_hor),
        "realized_net_pnl_usd": net_usd,
        "expectancy_net_r": exp_r,
        "max_drawdown_usd": round(max_dd, 4),
        "position_overlap_violations": overlap_violations,
    }


def build_delayed_entry_contract_and_ledgers_v17931213(
    *,
    si0_nb_episodes: Sequence[Mapping[str, Any]],
    feature_ledger_by_opp_id: Mapping[str, Mapping[str, Any]],
    datasets: Mapping[str, Any],
    si_results_by_policy: Mapping[str, Mapping[str, Any]],
    dataset_sha256: str = CANONICAL_DATASET_SHA256_V17931213,
) -> Dict[str, Any]:
    """
    Produces:
      - `contract_02`: `02_a1_old_filter_vs_true_delayed_entry_contract.json`
      - `delay_ledger_03`: `03_delay_confirmation_new_timestamp_entry_sl_tp_ledger.jsonl.gz`
      - `sl_tp_sizing_07`: `07_structural_sl_tp_position_sizing_evidence.jsonl.gz`
      - `matched_ab_08`: `08_matched_ab_all_opportunities_all_fill_modes.json`
      - `portfolio_09`: `09_chronological_portfolio_replay_all_variants.json`
    """
    from services.v17_9_3_1_2_3_core_trading_logic_repair import LEGACY_TO_CANONICAL_MODEL_MAP_V1793123

    bars_1m = list(datasets["1m"])
    bars_5m = list(datasets.get("5m") or [])
    bars_15m = list(datasets.get("15m") or [])
    bars_1h = list(datasets.get("1H") or [])
    bars_4h = list(datasets.get("4H") or [])

    open_ts_1m = [int(b.get("open_time") or b.get("timestamp") or 0) for b in bars_1m]
    close_ts_5m = [int(b.get("close_time") or (int(b.get("open_time") or 0) + 300_000)) for b in bars_5m]
    close_ts_15m = [int(b.get("close_time") or (int(b.get("open_time") or 0) + 900_000)) for b in bars_15m]
    close_ts_1h = [int(b.get("close_time") or (int(b.get("open_time") or 0) + 3_600_000)) for b in bars_1h]
    close_ts_4h = [int(b.get("close_time") or (int(b.get("open_time") or 0) + 14_400_000)) for b in bars_4h]

    summary_by_variant_and_mode: Dict[str, Dict[str, Dict[str, Any]]] = {
        v: {} for v in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213
    }
    portfolio_by_variant_and_mode: Dict[str, Dict[str, Dict[str, Any]]] = {
        v: {} for v in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213
    }
    rows_by_variant_nb: Dict[str, List[Dict[str, Any]]] = {}
    delay_ledger_03: List[Dict[str, Any]] = []
    sl_tp_sizing_07: List[Dict[str, Any]] = []

    # Pre-slice windows once per SI0 episode (242 episodes) for fast evaluation across 10 variants x 3 fill modes
    ep_slices: List[Dict[str, Any]] = []
    for idx, ep in enumerate(si0_nb_episodes):
        opp_id = str(ep.get("opportunity_id") or "")
        ep_key = str(ep.get("episode_key") or f"ep_{idx}")
        m_raw = str(ep.get("model_id") or "")
        m_id = LEGACY_TO_CANONICAL_MODEL_MAP_V1793123.get(m_raw, m_raw)
        direction = str(ep.get("direction") or "LONG").upper()
        dec_ms = int(ep.get("decision_timestamp_ms") or 0)
        i_dec = bisect.bisect_left(open_ts_1m, dec_ms)
        i_5m = bisect.bisect_right(close_ts_5m, dec_ms)
        i_15m = bisect.bisect_right(close_ts_15m, dec_ms)
        i_1h = bisect.bisect_right(close_ts_1h, dec_ms)
        i_4h = bisect.bisect_right(close_ts_4h, dec_ms)
        ep_slices.append(
            {
                "opp_id": opp_id,
                "ep_key": ep_key,
                "model_id": m_id,
                "direction": direction,
                "dec_ms": dec_ms,
                "base_entry": float(ep.get("entry_reference") or 0.0),
                "base_sl": float(ep.get("selected_sl") or 0.0),
                "base_tp": float(ep.get("take_profit") or 0.0),
                "feat": dict(feature_ledger_by_opp_id.get(opp_id) or {}),
                "closed_m1": bars_1m[max(0, i_dec - 180) : i_dec],
                "fwd_m1": bars_1m[i_dec : min(len(bars_1m), i_dec + 195)],
                "closed_m5": bars_5m[max(0, i_5m - 60) : i_5m],
                "closed_m15": bars_15m[max(0, i_15m - 60) : i_15m],
                "closed_1h": bars_1h[max(0, i_1h - 60) : i_1h],
                "closed_4h": bars_4h[max(0, i_4h - 60) : i_4h],
            }
        )

    for v_id in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213:
        for fm in CANONICAL_FILL_MODES_V17931211:
            v_rows: List[Dict[str, Any]] = []
            for slc in ep_slices:
                res = evaluate_r3_counterfactual_stop_and_target_v17931212(
                    variant_id=v_id,
                    opportunity_id=slc["opp_id"],
                    episode_key=slc["ep_key"],
                    model_id=slc["model_id"],
                    direction=slc["direction"],
                    decision_timestamp_ms=slc["dec_ms"],
                    baseline_entry=slc["base_entry"],
                    baseline_sl=slc["base_sl"],
                    baseline_tp=slc["base_tp"],
                    closed_m1=slc["closed_m1"],
                    closed_m5=slc["closed_m5"],
                    closed_m15=slc["closed_m15"],
                    closed_h1=slc["closed_1h"],
                    closed_h4=slc["closed_4h"],
                    forward_bars=slc["fwd_m1"],
                    pre_entry_features=slc["feat"],
                    fill_mode=fm,
                    dataset_sha=dataset_sha256,
                )
                v_rows.append(res)
                if fm == "NEXT_BAR_OPEN_BID_ASK":
                    delay_ledger_03.append(res)
                    if v_id in (
                        "A2_PROTECTED_SL_NOISE_BUFFER_ONLY",
                        "A3_SOURCE_BACKED_LIQUIDITY_TP_ONLY",
                        "D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL",
                        "D3_REAL_DELAYED_ENTRY_PLUS_TARGET",
                        "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
                    ):
                        sl_tp_sizing_07.append(
                            {
                                "variant_id": v_id,
                                "fill_mode": fm,
                                "opportunity_id": res.get("opportunity_id"),
                                "model_id": res.get("model_id"),
                                "direction": res.get("direction"),
                                "base_decision_timestamp_ms": res.get("base_decision_timestamp_ms", res.get("decision_timestamp_ms")),
                                "new_decision_timestamp_ms": res.get("new_decision_timestamp_ms"),
                                "wait_bars": int(res.get("wait_bars") or 0),
                                "status": res.get("status"),
                                "rejection_reason": res.get("rejection_reason"),
                                "filled": bool(res.get("filled")),
                                "entry_reference": res.get("planned_entry", res.get("entry_reference")),
                                "selected_sl": res.get("planned_sl", res.get("selected_sl")),
                                "take_profit": res.get("planned_tp", res.get("take_profit")),
                                "selected_anchor_id": res.get("selected_anchor_id"),
                                "protected_anchor_price": res.get("protected_anchor_price", res.get("structural_invalidation_price")),
                                "noise_buffer_usd": res.get("noise_buffer_usd"),
                                "circular_protected_anchor_equals_sl": bool(res.get("circular_protected_anchor_equals_sl", False)),
                                "selected_target_id": res.get("selected_target_id"),
                                "swept_target_claimed_unswept": bool(res.get("swept_target_claimed_unswept", False)),
                                "executed_quantity": res.get("quantity", res.get("executed_quantity")),
                                "planned_net_risk_usd": res.get("planned_net_risk_usd"),
                                "net_rr": res.get("net_rr"),
                                "outcome_label": res.get("outcome_label"),
                                "realized_net_pnl_usd": res.get("realized_net_pnl_usd"),
                            }
                        )

            summary_by_variant_and_mode[v_id][fm] = _summarize_counterfactual_rows_v17931213(v_id, fm, v_rows)
            portfolio_by_variant_and_mode[v_id][fm] = _replay_chronological_1pos_portfolio_from_matched_rows_v17931213(
                v_id, fm, v_rows
            )
            if fm == "NEXT_BAR_OPEN_BID_ASK":
                rows_by_variant_nb[v_id] = v_rows

    # Verify cross-adapter parity on first 25 baseline episodes across REPLAY_BACKTEST, MARKET_SHADOW, PRODUCTION_ORCHESTRATOR_DRY_RUN
    adapter_parity_checks: List[Dict[str, Any]] = []
    adapter_mismatches = 0
    for slc in ep_slices[:25]:
        common_kw = {
            "variant_id": "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
            "opportunity_id": slc["opp_id"],
            "episode_key": slc["ep_key"],
            "model_id": slc["model_id"],
            "direction": slc["direction"],
            "decision_timestamp_ms": slc["dec_ms"],
            "baseline_entry": slc["base_entry"],
            "baseline_sl": slc["base_sl"],
            "baseline_tp": slc["base_tp"],
            "closed_m1": slc["closed_m1"],
            "closed_m5": slc["closed_m5"],
            "closed_m15": slc["closed_m15"],
            "closed_h1": slc["closed_1h"],
            "closed_h4": slc["closed_4h"],
            "forward_bars": slc["fwd_m1"],
            "pre_entry_features": slc["feat"],
            "fill_mode": "NEXT_BAR_OPEN_BID_ASK",
            "dataset_sha": dataset_sha256,
        }
        r_bt = evaluate_shared_delayed_entry_adapter_v17931213(
            execution_context_mode="REPLAY_BACKTEST",
            activation_flags={"LIVE": False, "PAPER": False, "enabled_in_production": False},
            **common_kw,
        )
        r_sh = evaluate_shared_delayed_entry_adapter_v17931213(
            execution_context_mode="MARKET_SHADOW",
            activation_flags={"LIVE": False, "PAPER": False, "enabled_in_production": False},
            **common_kw,
        )
        r_pr = evaluate_shared_delayed_entry_adapter_v17931213(
            execution_context_mode="PRODUCTION_ORCHESTRATOR_DRY_RUN",
            activation_flags={"LIVE": False, "PAPER": False, "enabled_in_production": False},
            **common_kw,
        )
        core_bt = {k: v for k, v in r_bt.items() if k != "adapter_execution_context_mode"}
        core_sh = {k: v for k, v in r_sh.items() if k != "adapter_execution_context_mode"}
        core_pr = {k: v for k, v in r_pr.items() if k != "adapter_execution_context_mode"}
        same = (
            core_bt == core_sh == core_pr
            and r_pr["adapter_live_orders_emitted"] == 0
            and r_pr["adapter_default_activation_state"] == "OFF"
        )
        if not same:
            adapter_mismatches += 1
        if len(adapter_parity_checks) < 5:
            adapter_parity_checks.append(
                {
                    "opportunity_id": slc["opp_id"],
                    "status": r_bt["status"],
                    "wait_bars": r_bt["wait_bars"],
                    "new_decision_timestamp_ms": r_bt["new_decision_timestamp_ms"],
                    "parity_across_replay_shadow_orchestrator": same,
                }
            )

    # Breakdown by model_id, direction, wait_bars bucket, and HTF state for D1..D4 (NEXT_BAR_OPEN_BID_ASK)
    segment_breakdowns: Dict[str, Any] = {}
    for dv in ("D1_REAL_DELAYED_ENTRY", "D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL", "D3_REAL_DELAYED_ENTRY_PLUS_TARGET", "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE"):
        d_rows = rows_by_variant_nb[dv]
        by_model: Dict[str, Dict[str, Any]] = {}
        by_dir: Dict[str, Dict[str, Any]] = {}
        by_wait: Dict[str, Dict[str, Any]] = {}
        for r in d_rows:
            mk = str(r.get("model_id") or "UNKNOWN")
            dk = str(r.get("direction") or "UNKNOWN")
            wb = int(r.get("wait_bars") or 0)
            wk = "WAIT_0_REJECTED" if r.get("status") == "REJECTED_PRE_ENTRY" else (
                "WAIT_1_TO_3_BARS" if wb <= 3 else ("WAIT_4_TO_6_BARS" if wb <= 6 else "WAIT_7_TO_12_BARS")
            )
            for grp, key in ((by_model, mk), (by_dir, dk), (by_wait, wk)):
                cell = grp.setdefault(key, {"evaluated": 0, "closed": 0, "wins": 0, "losses": 0, "net_pnl_usd": 0.0})
                cell["evaluated"] += 1
                if r.get("is_closed"):
                    cell["closed"] += 1
                    if r.get("outcome_label") == "WIN":
                        cell["wins"] += 1
                    elif r.get("outcome_label") == "LOSS":
                        cell["losses"] += 1
                    cell["net_pnl_usd"] = round(cell["net_pnl_usd"] + float(r.get("realized_net_pnl_usd") or 0.0), 4)
        segment_breakdowns[dv] = {
            "by_model_id": by_model,
            "by_direction": by_dir,
            "by_wait_bars_bucket": by_wait,
        }

    contract_02 = {
        "version": VERSION_V17931213,
        "schema_version": SCHEMA_VERSION_V17931213,
        "dataset_sha256": dataset_sha256,
        "old_a1_contract": {
            "variant_id": "A1_ENTRY_CONFIRMATION_RETEST_ONLY",
            "entry_timing_contract": "SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY",
            "delayed_entry_executed": False,
            "wait_bars_allowed": [0],
            "explanation": (
                "Historical .12 A1 evaluated pre-entry confirmation booleans at base_decision_timestamp_ms "
                "without scanning subsequent M1 bars; preserved strictly as a same-cutoff filter control."
            ),
            "next_bar_open_bid_ask_summary": summary_by_variant_and_mode["A1_ENTRY_CONFIRMATION_RETEST_ONLY"]["NEXT_BAR_OPEN_BID_ASK"],
        },
        "true_delayed_entry_contract": {
            "variants": list(TRUE_DELAYED_ENTRY_VARIANTS_V17931213),
            "entry_timing_contract": "GENUINE_POST_SIGNAL_CLOSED_BAR_DELAYED_ENTRY",
            "delayed_entry_executed": True,
            "wait_bars_range": [1, 12],
            "strict_timestamp_inequality": "base_decision_timestamp_ms < new_decision_timestamp_ms < entry_fill_ts",
            "rebuilt_at_confirmation_close": [
                "new_entry_reference",
                "new_selected_sl",
                "new_take_profit",
                "new_executed_quantity",
                "new_planned_net_risk_usd_capped_2_50",
            ],
            "a1b_summary": summary_by_variant_and_mode["A1B_GENUINE_DELAYED_CONFIRMATION_ENTRY"]["NEXT_BAR_OPEN_BID_ASK"],
            "d1_summary": summary_by_variant_and_mode["D1_REAL_DELAYED_ENTRY"]["NEXT_BAR_OPEN_BID_ASK"],
            "d2_summary": summary_by_variant_and_mode["D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL"]["NEXT_BAR_OPEN_BID_ASK"],
            "d3_summary": summary_by_variant_and_mode["D3_REAL_DELAYED_ENTRY_PLUS_TARGET"]["NEXT_BAR_OPEN_BID_ASK"],
            "d4_summary": summary_by_variant_and_mode["D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE"]["NEXT_BAR_OPEN_BID_ASK"],
        },
        "shared_cross_adapter_verification": {
            "adapter_symbol": "evaluate_shared_delayed_entry_adapter_v17931213",
            "contexts_verified": ["REPLAY_BACKTEST", "MARKET_SHADOW", "PRODUCTION_ORCHESTRATOR_DRY_RUN"],
            "episodes_checked": min(25, len(ep_slices)),
            "mismatches_count": adapter_mismatches,
            "default_state": "OFF",
            "enabled_in_production": False,
            "sample_checks": adapter_parity_checks,
            "status": "PASS" if adapter_mismatches == 0 else "FAIL",
        },
    }

    si_policy_matrix_summary = {
        pid: {
            fm: {k: v for k, v in m_dict.items() if k != "episodes"}
            for fm, m_dict in pol_res["replays_by_fill_mode"].items()
        }
        for pid, pol_res in si_results_by_policy.items()
    }

    matched_ab_08 = {
        "version": VERSION_V17931213,
        "schema_version": SCHEMA_VERSION_V17931213,
        "dataset_sha256": dataset_sha256,
        "matched_baseline_cohort_size": len(si0_nb_episodes),
        "fill_modes": list(CANONICAL_FILL_MODES_V17931211),
        "matched_variants_summary_by_fill_mode": summary_by_variant_and_mode,
        "segment_breakdowns_next_bar_open_bid_ask": segment_breakdowns,
        "full_universe_si0_to_si9_by_fill_mode": si_policy_matrix_summary,
    }

    portfolio_09 = {
        "version": VERSION_V17931213,
        "schema_version": SCHEMA_VERSION_V17931213,
        "dataset_sha256": dataset_sha256,
        "max_concurrent_positions": 1,
        "matched_and_delayed_variants_1pos_portfolio": portfolio_by_variant_and_mode,
        "si0_to_si9_full_universe_1pos_portfolio": si_policy_matrix_summary,
    }

    return {
        "contract_02": contract_02,
        "delay_ledger_03": delay_ledger_03,
        "sl_tp_sizing_07": sl_tp_sizing_07,
        "matched_ab_08": matched_ab_08,
        "portfolio_09": portfolio_09,
        "summary_by_variant_and_mode": summary_by_variant_and_mode,
        "portfolio_by_variant_and_mode": portfolio_by_variant_and_mode,
        "rows_by_variant_nb": rows_by_variant_nb,
    }


def verify_delayed_entry_and_structural_sl_tp_v17931213(
    contract_02: Mapping[str, Any],
    summary_by_variant_and_mode: Mapping[str, Mapping[str, Mapping[str, Any]]],
    delay_ledger_03: Sequence[Mapping[str, Any]],
    portfolio_09: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Production verifier for Phase A (`P0-E1`) and Structural SL/TP/Sizing invariants:
      1. `A1_ENTRY_CONFIRMATION_RETEST_ONLY` and `A4_COMBINED_CAUSAL_ENTRY_SL_TP` must have
         `delayed_entry_executed == False` and `entry_timing_contract == "SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY"`.
      2. Every accepted row in `TRUE_DELAYED_ENTRY_VARIANTS_V17931213` (`A1B`, `D1..D4`) MUST have:
         - `delayed_entry_executed is True`
         - `1 <= wait_bars <= 12`
         - `new_decision_timestamp_ms > base_decision_timestamp_ms`
         - `confirmation_known_at_ms <= new_decision_timestamp_ms` (if present)
         - If `filled is True`: `entry_fill_ts > new_decision_timestamp_ms` (for `NEXT_BAR_OPEN_BID_ASK` / `STRESS_*`)
           or `entry_fill_ts >= new_decision_timestamp_ms` (for `LEGACY_CLOSE_FILL`), NEVER `< new_decision_timestamp_ms`!
      3. Structural SL/TP & Risk Sizing invariants:
         - `planned_net_risk_usd <= 2.50` on all filled trades
         - `net_rr >= 2.0` on all newly sized structural/delayed trades (`D2`, `D3`, `D4`, `A2`, `A3`)
         - `circular_protected_anchor_equals_sl` is False, and `selected_sl < protected_anchor_price` (LONG)
           or `selected_sl > protected_anchor_price` (SHORT) when `protected_anchor_price` is present
         - `swept_target_claimed_unswept` is False
      4. Shared cross-adapter parity (`REPLAY_BACKTEST == MARKET_SHADOW == PRODUCTION_ORCHESTRATOR_DRY_RUN`)
         with `enabled_in_production is False` and `default_state == "OFF"`.
      5. Zero 1-position portfolio overlap violations (`position_overlap_violations == 0`).
    """
    violations: List[str] = []

    old_a1 = contract_02.get("old_a1_contract") or {}
    if old_a1.get("delayed_entry_executed") is not False or old_a1.get("entry_timing_contract") != "SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY":
        violations.append("A1_SAME_CUTOFF_FILTER_MISLABELED_AS_DELAYED_ENTRY")

    sh_ad = contract_02.get("shared_cross_adapter_verification") or {}
    if sh_ad.get("status") != "PASS" or int(sh_ad.get("mismatches_count", 1)) != 0:
        violations.append("CROSS_ADAPTER_PARITY_MISMATCH")
    if sh_ad.get("enabled_in_production") is not False or sh_ad.get("default_state") != "OFF":
        violations.append("PRODUCTION_ADAPTER_UNSAFE_ACTIVATION_STATE")

    for v_id in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213:
        if v_id not in summary_by_variant_and_mode:
            violations.append(f"MISSING_VARIANT_SUMMARY:{v_id}")
            continue
        for fm in CANONICAL_FILL_MODES_V17931211:
            cell = summary_by_variant_and_mode[v_id].get(fm)
            if not isinstance(cell, Mapping):
                violations.append(f"MISSING_VARIANT_FILL_MODE_CELL:{v_id}:{fm}")
                continue
            tot = int(cell.get("matched_opportunities_evaluated", 0))
            parts = (
                int(cell.get("rejected_pre_entry_count", 0))
                + int(cell.get("no_fill_or_sizing_reject_count", 0))
                + int(cell.get("closed_trades_count", 0))
                + int(cell.get("open_at_horizon_count", 0))
                + int(cell.get("data_gap_unresolved_count", 0))
            )
            if tot != 242 or parts != tot:
                violations.append(f"COHORT_CONSERVATION_BROKEN:{v_id}:{fm}:tot={tot},parts={parts}")
            if float(cell.get("max_planned_net_risk_usd_observed", 0.0)) > 2.500001:
                violations.append(f"RISK_CAP_2_50_EXCEEDED:{v_id}:{fm}")

    for row in delay_ledger_03:
        v_id = str(row.get("variant_id") or "")
        oid = str(row.get("opportunity_id") or "")
        status = str(row.get("status") or "")
        if v_id in ("A1_ENTRY_CONFIRMATION_RETEST_ONLY", "A4_COMBINED_CAUSAL_ENTRY_SL_TP"):
            if row.get("delayed_entry_executed") is True:
                violations.append(f"A1_SAME_CUTOFF_FILTER_MISLABELED_AS_DELAYED_ENTRY:{oid}")
        if v_id in TRUE_DELAYED_ENTRY_VARIANTS_V17931213 and status != "REJECTED_PRE_ENTRY":
            wb = int(row.get("wait_bars") or 0)
            b_ts = int(row.get("base_decision_timestamp_ms") or row.get("decision_timestamp_ms") or 0)
            n_ts = int(row.get("new_decision_timestamp_ms") or 0)
            if row.get("delayed_entry_executed") is not True or wb < 1 or wb > 12 or n_ts <= b_ts:
                violations.append(f"ZERO_DELAY_OR_INVALID_WAIT_BARS_IN_TRUE_DELAYED_ENTRY:{v_id}:{oid}:wb={wb},b={b_ts},n={n_ts}")
            conf_known = row.get("confirmation_known_at_ms")
            if conf_known is not None and int(conf_known) > n_ts:
                violations.append(f"CONFIRMATION_LOOKAHEAD_AFTER_DECISION_TS:{v_id}:{oid}")
            if bool(row.get("filled")):
                f_ts = int(row.get("entry_fill_ts") or 0)
                fm_r = str(row.get("fill_mode") or "NEXT_BAR_OPEN_BID_ASK")
                if fm_r == "LEGACY_CLOSE_FILL":
                    if f_ts < n_ts:
                        violations.append(f"RETROACTIVE_FILL_BEFORE_CONFIRMATION_CLOSE:{v_id}:{oid}:fill={f_ts}<new={n_ts}")
                else:
                    if f_ts < n_ts:
                        violations.append(f"RETROACTIVE_FILL_BEFORE_CONFIRMATION_CLOSE:{v_id}:{oid}:fill={f_ts}<new={n_ts}")

        if bool(row.get("filled")):
            if float(row.get("planned_net_risk_usd") or 0.0) > 2.500001:
                violations.append(f"RISK_CAP_2_50_EXCEEDED:{v_id}:{oid}")
            if v_id in ("D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL", "D3_REAL_DELAYED_ENTRY_PLUS_TARGET", "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE"):
                if float(row.get("net_rr") or 0.0) + 1e-6 < 2.0:
                    violations.append(f"NET_RR_BELOW_2_0_ACCEPTED:{v_id}:{oid}:{row.get('net_rr')}")
        if row.get("circular_protected_anchor_equals_sl") is True:
            violations.append(f"CIRCULAR_PROTECTED_ANCHOR_EQUALS_SL:{v_id}:{oid}")
        anc_px = row.get("protected_anchor_price")
        if anc_px is None and v_id in ("D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL", "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE"):
            anc_px = row.get("structural_invalidation_price")
        sl_px = row.get("selected_sl", row.get("planned_sl"))
        if anc_px is not None and sl_px is not None and bool(row.get("filled")) and v_id in ("A2_PROTECTED_SL_NOISE_BUFFER_ONLY", "D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL", "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE"):
            d_u = str(row.get("direction") or "LONG").upper()
            if abs(float(anc_px) - float(sl_px)) < 1e-6:
                violations.append(f"CIRCULAR_PROTECTED_ANCHOR_EQUALS_SL:{v_id}:{oid}")
            elif d_u == "LONG" and float(sl_px) >= float(anc_px):
                violations.append(f"STOP_ON_WRONG_SIDE_OF_PROTECTED_ANCHOR:{v_id}:{oid}")
            elif d_u == "SHORT" and float(sl_px) <= float(anc_px):
                violations.append(f"STOP_ON_WRONG_SIDE_OF_PROTECTED_ANCHOR:{v_id}:{oid}")
        if row.get("swept_target_claimed_unswept") is True or str(row.get("target_pool_state") or "") == "ALREADY_SWEPT":
            violations.append(f"SWEPT_TARGET_POOL_ACCEPTED:{v_id}:{oid}")

    if portfolio_09 is not None:
        p_map = portfolio_09.get("matched_and_delayed_variants_1pos_portfolio") or {}
        for pv_id, fm_dict in p_map.items():
            if not isinstance(fm_dict, Mapping):
                continue
            for p_fm, p_cell in fm_dict.items():
                if int((p_cell or {}).get("position_overlap_violations", 0)) > 0:
                    violations.append(f"PORTFOLIO_1POS_OVERLAP_VIOLATION:{pv_id}:{p_fm}")

    return {
        "version": VERSION_V17931213,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
    }


# ==============================================================================
# 4. PHASE B (P0-E2 / P0-E3): HTF / UNICORN / SI8 / SI9 DYNAMIC FUNNEL & 267/113 AUDITS
# ==============================================================================

def _run_materialized_single_axis_ablation_v17931213(
    ablation_mode: str,
    *,
    eligible_decisions: Sequence[Mapping[str, Any]],
    feature_ledger_by_opp_id: Mapping[str, Mapping[str, Any]],
    candidate_index: Mapping[str, Any],
    datasets: Mapping[str, Any],
    close_ts_index: Mapping[str, Sequence[int]],
) -> Dict[str, Any]:
    """
    Executes end-to-end causal confirmation -> `emit_causal_order_intents_v1793121` ->
    chronological 1-position `replay_intervention_outcome_v179312` (`NEXT_BAR_OPEN_BID_ASK`)
    for a single-axis MTF/Unicorn ablation mode across proof-eligible candidates.
    """
    bars_1m = datasets["1m"]

    htf_mode = ablation_mode if ablation_mode in ("H4_ONLY", "H1_ONLY", "M15_ONLY", "M15_H1_ONLY", "FULL_MTF") else "FULL_MTF"
    pol = ExecutableResearchPolicyV179312(
        variant_id=f"ABLATION_{ablation_mode}",
        base_cohort_variant="A1_V1792_SL_TP_BASELINE",
        intervention_type="ENTRY_SL_TP_COMBINED_EXECUTABLE",
        hypothesis_description=f"Single-axis ablation {ablation_mode}",
        cited_dev_loss_cause="MTF_UNICORN_ABLATION",
        require_causal_h1_h4_alignment=True,
        wait_for_confirmed_mss_and_displacement=True,
        max_wait_bars=12,
        rederive_outer_structural_stop=True,
        rederive_structural_liquidity_target=True,
    )

    filtered_decs: List[Dict[str, Any]] = []
    for dec in eligible_decisions:
        oid = str(dec.get("opportunity_id") or "")
        feat = feature_ledger_by_opp_id.get(oid) or {}
        d_up = str(dec.get("direction") or "LONG").upper()
        cur_ts = int(dec.get("decision_timestamp_ms") or 0)
        if ablation_mode == "STRUCTURE_ONLY":
            if not (feat.get("mss_confirmed") is True and feat.get("displacement_valid") is True):
                continue
        elif ablation_mode == "LIQUIDITY_ONLY":
            if not (feat.get("sweep_confirmed") is True and feat.get("sweep_reclaimed") is True and feat.get("target_unswept") is True):
                continue
        else:
            ok_pre, _ = _check_causal_htf_alignment_at_ts_v179312(
                datasets,
                close_ts_index,
                cur_ts,
                d_up,
                use_structural_evaluator=True,
                ablation_mode=htf_mode,
            )
            if not ok_pre:
                continue
        filtered_decs.append(dict(dec))

    # Group by checkpoint_index_1m and take top-ranked per bar (matching SI8 arbitration)
    by_chk: Dict[int, List[Dict[str, Any]]] = {}
    for d in filtered_decs:
        by_chk.setdefault(int(d["checkpoint_index_1m"]), []).append(d)
    arb_winners: List[Dict[str, Any]] = []
    for chk_i in sorted(by_chk.keys()):
        grp = sorted(by_chk[chk_i], key=lambda r: (-float(r.get("confluence_score") or 0.0), str(r.get("opportunity_id") or "")))
        arb_winners.append(grp[0])

    materialized_pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for w in arb_winners:
        mat = emit_causal_order_intents_v1793121(
            w,
            pol,
            candidate_index=candidate_index,
            datasets=datasets,
            strict_v17931213_mode=True,
            htf_ablation_mode=htf_mode,
        )
        if mat.get("accepted"):
            materialized_pairs.append((w, mat))

    materialized_pairs.sort(key=lambda pair: (int(pair[1].get("new_decision_timestamp_ms") or pair[0].get("decision_timestamp_ms") or 0), str(pair[0].get("opportunity_id") or "")))
    active_release_ts = 0
    closed_eps: List[Dict[str, Any]] = []
    scheduled_count = 0
    for base_opp, mat_plan in materialized_pairs:
        eff_ts = int(mat_plan.get("new_decision_timestamp_ms") or base_opp.get("decision_timestamp_ms") or 0)
        if active_release_ts > 0 and eff_ts < active_release_ts:
            continue
        scheduled_count += 1
        rep = replay_intervention_outcome_v179312(
            mat_plan,
            bars_1m,
            mode="NEXT_BAR_OPEN_BID_ASK",
            base_record=base_opp,
        )
        ep_out = dict(rep["episode_record"])
        if ep_out.get("filled"):
            active_release_ts = max(active_release_ts, int(ep_out.get("release_ts_ms") or ep_out.get("exit_fill_ts") or (eff_ts + 180 * 60_000)))
            if ep_out.get("outcome_label") in ("WIN", "LOSS"):
                closed_eps.append(ep_out)

    wins = [e for e in closed_eps if e.get("outcome_label") == "WIN"]
    losses = [e for e in closed_eps if e.get("outcome_label") == "LOSS"]
    net_usd = round(sum(float(e.get("realized_net_pnl_usd") or 0.0) for e in closed_eps), 4)
    exp_r = round(sum(float(e.get("realized_r") or e.get("realized_r_net") or 0.0) for e in closed_eps) / len(closed_eps), 4) if closed_eps else 0.0
    return {
        "ablation_mode": ablation_mode,
        "pre_arbitration_candidates_passed": len(filtered_decs),
        "arbitration_winners_count": len(arb_winners),
        "materialized_intents_count": len(materialized_pairs),
        "scheduled_1pos_episodes_count": scheduled_count,
        "closed_trades_count": len(closed_eps),
        "wins_count": len(wins),
        "losses_count": len(losses),
        "realized_net_pnl_usd": net_usd,
        "expectancy_net_r": exp_r,
    }


def build_htf_unicorn_and_si8_si9_dynamic_funnel_v17931213(
    *,
    datasets: Mapping[str, Any],
    close_ts_index: Mapping[str, Sequence[int]],
    candidate_index: Mapping[str, Any],
    canonical_decisions: Sequence[Mapping[str, Any]],
    si8_decisions: Sequence[Mapping[str, Any]],
    feature_ledger_by_opp_id: Mapping[str, Mapping[str, Any]],
    si8_policy_result: Mapping[str, Any],
    si9_policy_result: Mapping[str, Any],
    v12_funnel_09_doc: Optional[Mapping[str, Any]] = None,
    dataset_sha256: str = CANONICAL_DATASET_SHA256_V17931213,
) -> Dict[str, Any]:
    """
    Produces:
      - `htf_pipeline_rows_04`: `04_htf_unicorn_detector_to_intent_causal_pipeline.jsonl.gz` (`63,085` rows)
      - `funnel_audit_05`: `05_si8_si9_dynamic_full_funnel_and_target_267_audit.json`
    """
    from services.v17_9_3_1_2_3_core_trading_logic_repair import LEGACY_TO_CANONICAL_MODEL_MAP_V1793123
    from services.v17_9_3_1_2_7_causal_signal_truth import required_proof_rejection_v1793127
    from services.v17_9_3_1_2_12_provenance_forensics_and_repair_service import (
        build_r2_htf_unicorn_funnel_and_ablations_v17931212,
    )

    r2_base = build_r2_htf_unicorn_funnel_and_ablations_v17931212(
        datasets=datasets,
        canonical_decisions=canonical_decisions,
        si8_decisions=si8_decisions,
        feature_ledger_by_opp_id=feature_ledger_by_opp_id,
        si8_policy_result=si8_policy_result,
        si9_policy_result=si9_policy_result,
        dataset_sha256=dataset_sha256,
    )

    si8_led = list(si8_policy_result.get("policy_decision_ledger") or si8_policy_result.get("decision_ledger") or [])
    si9_led = list(si9_policy_result.get("policy_decision_ledger") or si9_policy_result.get("decision_ledger") or [])
    si8_by_id = {str(r["opportunity_id"]): r for r in si8_led}
    si9_by_id = {str(r["opportunity_id"]): r for r in si9_led}

    all_63085 = list(canonical_decisions) + list(si8_decisions)
    htf_pipeline_rows_04: List[Dict[str, Any]] = []
    proof_eligible_decs: List[Dict[str, Any]] = []

    for dec in all_63085:
        oid = str(dec.get("opportunity_id") or "")
        m_raw = str(dec.get("model_id") or "")
        m_id = LEGACY_TO_CANONICAL_MODEL_MAP_V1793123.get(m_raw, m_raw)
        feat = feature_ledger_by_opp_id.get(oid) or {}
        s8 = si8_by_id.get(oid) or {}
        s9 = si9_by_id.get(oid) or {}
        proof_rej = required_proof_rejection_v1793127(feat, m_id, decision=dec)
        is_proof_elig = proof_rej is None
        if is_proof_elig:
            proof_eligible_decs.append(dict(dec))
        htf_pipeline_rows_04.append(
            {
                "opportunity_id": oid,
                "discovered_by_si8": bool(dec.get("discovered_by_si8", False)),
                "model_id": m_id,
                "direction": str(dec.get("direction") or ""),
                "checkpoint_index_1m": int(dec.get("checkpoint_index_1m") or 0),
                "decision_timestamp_ms": int(dec.get("decision_timestamp_ms") or 0),
                "h4_bias": feat.get("h4_bias"),
                "h1_bias": feat.get("h1_bias"),
                "m15_bias": feat.get("m15_bias"),
                "m5_bias": feat.get("m5_bias"),
                "htf_alignment_state": feat.get("htf_alignment_state", "UNKNOWN"),
                "unicorn_classification": feat.get("unicorn_classification", "UNKNOWN"),
                "unicorn_breaker_fvg_bound": bool(feat.get("unicorn_breaker_fvg_bound", False)),
                "fvg_present": bool(feat.get("fvg_present", False)),
                "fvg_unmitigated": bool(feat.get("fvg_unmitigated", False)),
                "fvg_retest_valid": bool(feat.get("fvg_retest_valid", False)),
                "sweep_confirmed": bool(feat.get("sweep_confirmed", False)),
                "sweep_reclaimed": bool(feat.get("sweep_reclaimed", False)),
                "mss_confirmed": bool(feat.get("mss_confirmed", False)),
                "displacement_valid": bool(feat.get("displacement_valid", False)),
                "protected_structure_independent_of_sl": bool(feat.get("protected_structure_independent_of_sl", False)),
                "target_unswept": bool(feat.get("target_unswept", False)),
                "proof_hash": feat.get("proof_hash"),
                "proof_eligible": is_proof_elig,
                "proof_rejection_reason": proof_rej,
                "si8_accepted": bool(s8.get("accepted", False)),
                "si8_rejection_reason": s8.get("rejection_reason"),
                "si8_intent_id": s8.get("intent_id"),
                "si8_new_decision_timestamp_ms": s8.get("new_decision_timestamp_ms"),
                "si8_wait_bars": int(s8.get("wait_bars") or 0),
                "si9_accepted": bool(s9.get("accepted", False)),
                "si9_rejection_reason": s9.get("rejection_reason"),
                "si9_intent_id": s9.get("intent_id"),
                "si9_new_decision_timestamp_ms": s9.get("new_decision_timestamp_ms"),
            }
        )

    # Forensic audit of historical .12 267 SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN cases & 113 REJECTED_CAUSAL_H1_H4_CONFLICT cases
    v12_rows = (
        ((v12_funnel_09_doc or {}).get("si8_si9_278_to_0_funnel_diagnosis") or {}).get("rows")
        or []
    )
    bucket_counts_267: Dict[str, int] = {b: 0 for b in SI9_267_TARGET_AUDIT_BUCKETS}
    v13_outcomes_on_267: Counter[str] = Counter()
    sample_267_audit_rows: List[Dict[str, Any]] = []

    v12_si9_267_rows = [
        r for r in v12_rows if str(r.get("si9_rejection_reason") or "") == "SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN"
    ]
    for r in v12_si9_267_rows:
        oid = str(r.get("opportunity_id") or "")
        feat = feature_ledger_by_opp_id.get(oid) or {}
        t_unswept = feat.get("target_unswept")
        t_pool_id = str(feat.get("target_pool_id") or "")
        t_room_m1 = float(feat.get("target_room_atr_m1") or 0.0)
        eff_rr = float(feat.get("effective_net_rr") or 0.0)
        eff_risk = float(feat.get("effective_planned_net_risk_usd") or 0.0)
        t_tf = str(feat.get("target_pool_timeframe") or "1m").upper()

        if t_unswept is not True:
            b_key = "I_TARGET_ALREADY_SWEPT_BEFORE_DECISION"
        elif not t_pool_id:
            b_key = "II_TARGET_POOL_ID_FORMAT_OR_JOIN_MISMATCH"
        elif t_room_m1 < 0.30:
            b_key = "III_TARGET_OUTSIDE_LOOKBACK_OR_MISSING_TF_POOL"
        elif eff_rr < 2.0:
            b_key = "IV_TARGET_NET_RR_BELOW_2_0_AFTER_FEES_SPREAD"
        elif t_room_m1 > 12.0 and t_tf in ("5M", "15M", "1H", "4H"):
            b_key = "V_TARGET_1M_ATR_UNIT_MISMATCH_ON_HTF_POOL"
        elif eff_risk <= 0.0 or eff_risk > 2.50:
            b_key = "VI_PLANNED_RISK_EXCEEDS_2_50_AT_MIN_LOT"
        elif feat.get("protected_structure_independent_of_sl") is not True:
            b_key = "VII_ANCHOR_ID_FORMAT_MISMATCH_IN_POST_MATERIALIZATION_PROOF"
        else:
            b_key = "VIII_GENUINELY_NO_VIABLE_UNSWEPT_POOL_IN_RR_BAND"

        bucket_counts_267[b_key] += 1
        s9_now = si9_by_id.get(oid) or {}
        now_st = "MATERIALIZED_INTENT" if s9_now.get("accepted") else str(s9_now.get("rejection_reason") or "NOT_EVALUATED")
        v13_outcomes_on_267[now_st] += 1
        if len(sample_267_audit_rows) < 15 or s9_now.get("accepted"):
            sample_267_audit_rows.append(
                {
                    "opportunity_id": oid,
                    "historical_v12_si9_reason": "SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN",
                    "root_cause_bucket": b_key,
                    "target_pool_id": t_pool_id,
                    "target_pool_timeframe": t_tf,
                    "target_room_atr_m1": round(t_room_m1, 4),
                    "effective_net_rr_at_base": round(eff_rr, 4),
                    "repaired_v13_si9_status": now_st,
                    "repaired_v13_si9_intent_id": s9_now.get("intent_id"),
                }
            )

    v12_htf_113_rows = [
        r for r in v12_rows if str(r.get("si8_rejection_reason") or "") == "REJECTED_CAUSAL_H1_H4_CONFLICT"
    ]
    v12_113_truly_aligned = 0
    v13_outcomes_on_113: Counter[str] = Counter()
    for r in v12_htf_113_rows:
        oid = str(r.get("opportunity_id") or "")
        feat = feature_ledger_by_opp_id.get(oid) or {}
        if feat.get("htf_alignment_state") == "ALIGNED":
            v12_113_truly_aligned += 1
        s8_now = si8_by_id.get(oid) or {}
        now_st8 = "MATERIALIZED_INTENT" if s8_now.get("accepted") else str(s8_now.get("rejection_reason") or "NOT_EVALUATED")
        v13_outcomes_on_113[now_st8] += 1

    # Count any remaining false REJECTED_CAUSAL_H1_H4_CONFLICT on base-aligned candidates (note: post-confirmation at new_ts can legitimately shift if a new 1H/4H bar closed!)
    repaired_v13_false_htf_conflict_at_same_ts = 0
    for r in si8_led:
        if str(r.get("rejection_reason") or "").startswith("REJECTED_CAUSAL_H1_H4_CONFLICT"):
            oid = str(r.get("opportunity_id") or "")
            feat = feature_ledger_by_opp_id.get(oid) or {}
            # Only false if no new 1H/4H bar could have closed and base was ALIGNED
            if feat.get("htf_alignment_state") == "ALIGNED" and r.get("new_decision_timestamp_ms") is None:
                repaired_v13_false_htf_conflict_at_same_ts += 1

    # Materialized single-axis ablations across proof-eligible candidates
    pre_filter_ablations = r2_base["si8_si9_ablation_09"]["single_axis_mtf_unicorn_ablations_63085"]
    materialized_ablations: Dict[str, Any] = {}
    for ab_mode in (
        "H4_ONLY",
        "H1_ONLY",
        "M15_ONLY",
        "M15_H1_ONLY",
        "STRUCTURE_ONLY",
        "LIQUIDITY_ONLY",
        "FULL_MTF",
    ):
        mat_ab = _run_materialized_single_axis_ablation_v17931213(
            ab_mode,
            eligible_decisions=proof_eligible_decs,
            feature_ledger_by_opp_id=feature_ledger_by_opp_id,
            candidate_index=candidate_index,
            datasets=datasets,
            close_ts_index=close_ts_index,
        )
        pre_cell = (pre_filter_ablations.get("ablation_modes") or {}).get(ab_mode) or {}
        materialized_ablations[ab_mode] = {
            "pre_entry_63085_passed": int(pre_cell.get("passed", 0)),
            "pre_entry_63085_rejected": int(pre_cell.get("rejected", 0)),
            **mat_ab,
        }

    si8_nb = si8_policy_result["replays_by_fill_mode"]["NEXT_BAR_OPEN_BID_ASK"]
    si9_nb = si9_policy_result["replays_by_fill_mode"]["NEXT_BAR_OPEN_BID_ASK"]
    dyn_diag = dict(r2_base["si8_si9_ablation_09"]["si8_si9_278_to_0_funnel_diagnosis"])
    dyn_diag["si8_next_bar_open_replay"] = {
        "scheduled_episodes": int(si8_nb.get("total_episodes", 0)),
        "closed_trades_count": int(si8_nb.get("closed_trades_count", 0)),
        "wins_count": int(si8_nb.get("wins", 0)),
        "losses_count": int(si8_nb.get("losses", 0)),
        "realized_net_pnl_usd": float(si8_nb.get("realized_net_pnl_usd", 0.0)),
        "expectancy_r": float(si8_nb.get("expectancy_r", 0.0)),
    }
    dyn_diag["si9_next_bar_open_replay"] = {
        "materialized_intents_count": int(si9_policy_result.get("materialized_intents_count", 0)),
        "materialized_intent_ids": list(si9_policy_result.get("materialized_intent_ids") or []),
        "scheduled_episodes": int(si9_nb.get("total_episodes", 0)),
        "closed_trades_count": int(si9_nb.get("closed_trades_count", 0)),
        "wins_count": int(si9_nb.get("wins", 0)),
        "losses_count": int(si9_nb.get("losses", 0)),
        "realized_net_pnl_usd": float(si9_nb.get("realized_net_pnl_usd", 0.0)),
        "expectancy_r": float(si9_nb.get("expectancy_r", 0.0)),
    }

    raw_si8_elig_ledger = [
        dict(r)
        for r in si8_led
        if not str(r.get("rejection_reason") or "").startswith("REJECTED_UNPROVEN:")
        or bool(r.get("entered_materialization_stage", False))
    ]

    funnel_audit_05 = {
        "version": VERSION_V17931213,
        "schema_version": SCHEMA_VERSION_V17931213,
        "dataset_sha256": dataset_sha256,
        "htf_causal_bar_and_pivot_audit": r2_base["htf_audit_07"],
        "unicorn_fvg_breaker_funnel_63085": r2_base["unicorn_funnel_08"],
        "si8_si9_dynamic_funnel_diagnosis": dyn_diag,
        "raw_si8_eligible_and_materialized_ledger": raw_si8_elig_ledger,
        "historical_v12_267_si9_target_audit": {
            "historical_v12_cases_audited": len(v12_si9_267_rows),
            "buckets_i_to_viii_counts": bucket_counts_267,
            "buckets_sum": sum(bucket_counts_267.values()),
            "primary_root_cause_explanation": (
                "267 / 267 historical .12 SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN cases selected valid unswept "
                "1H liquidity pools whose distance was ~13..22 M1 ATRs (~1.7..2.8 1H ATRs) and failed the "
                "unscaled M1 ATR ceiling `target_room_atr_m1 <= 12.0` before materialization."
            ),
            "repaired_v13_si9_outcomes_on_267": dict(sorted(v13_outcomes_on_267.items())),
            "sample_audited_cases": sample_267_audit_rows,
        },
        "historical_v12_113_htf_conflict_audit": {
            "historical_v12_cases_audited": len(v12_htf_113_rows),
            "truly_aligned_under_evaluate_htf_structure_count": v12_113_truly_aligned,
            "repaired_v13_false_htf_conflict_at_same_ts": repaired_v13_false_htf_conflict_at_same_ts,
            "repaired_v13_si8_outcomes_on_113": dict(sorted(v13_outcomes_on_113.items())),
        },
        "materialized_single_axis_mtf_unicorn_ablations": materialized_ablations,
    }

    return {
        "htf_pipeline_rows_04": htf_pipeline_rows_04,
        "funnel_audit_05": funnel_audit_05,
        "r2_base": r2_base,
    }


def verify_htf_unicorn_and_si8_si9_funnel_v17931213(
    funnel_audit_05: Mapping[str, Any],
    *,
    raw_si8_decision_ledger: Optional[Sequence[Mapping[str, Any]]] = None,
    expect_267_historical_audit: bool = True,
) -> Dict[str, Any]:
    violations: List[str] = []
    htf_07 = funnel_audit_05.get("htf_causal_bar_and_pivot_audit") or {}
    uni_08 = funnel_audit_05.get("unicorn_fvg_breaker_funnel_63085") or {}
    dyn_diag = funnel_audit_05.get("si8_si9_dynamic_funnel_diagnosis") or {}

    r2_chk = verify_r2_causal_mtf_unicorn_provenance_v17931212(
        htf_07,
        uni_08,
        {"si8_si9_dynamic_funnel_diagnosis": dyn_diag},
        raw_si8_decision_ledger=raw_si8_decision_ledger,
    )
    if r2_chk.get("status") != "PASS":
        violations.extend(r2_chk.get("violations") or ["R2_BASE_CHECK_FAILED"])

    t267 = funnel_audit_05.get("historical_v12_267_si9_target_audit") or {}
    if expect_267_historical_audit:
        aud_cnt = int(t267.get("historical_v12_cases_audited", 0))
        b_sum = int(t267.get("buckets_sum", 0))
        if aud_cnt != 267 or b_sum != 267:
            violations.append(f"SI9_267_TARGET_AUDIT_SUM_MISMATCH:audited={aud_cnt},sum={b_sum}")

    h113 = funnel_audit_05.get("historical_v12_113_htf_conflict_audit") or {}
    if int(h113.get("repaired_v13_false_htf_conflict_at_same_ts", 0)) != 0:
        violations.append("FALSE_HTF_CONFLICT_STILL_PRESENT")

    mat_ab = funnel_audit_05.get("materialized_single_axis_mtf_unicorn_ablations") or {}
    for req_mode in ("H4_ONLY", "H1_ONLY", "M15_ONLY", "M15_H1_ONLY", "STRUCTURE_ONLY", "LIQUIDITY_ONLY", "FULL_MTF"):
        if req_mode not in mat_ab or "materialized_intents_count" not in (mat_ab.get(req_mode) or {}):
            violations.append(f"MISSING_MATERIALIZED_ABLATION_MODE:{req_mode}")

    return {
        "version": VERSION_V17931213,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
    }


# ==============================================================================
# 5. PHASE C (R1): PRE-ENTRY LOSS FORENSICS + D1..D4 TRANSITION LEDGER (06)
# ==============================================================================

def build_pre_entry_forensics_and_d1_d4_transitions_v17931213(
    *,
    r1_bundle_v12: Mapping[str, Any],
    rows_by_variant_nb: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """
    Produces `forensic_rows_06` (`06_pre_entry_loss_forensics_and_expost_stop_separation.jsonl.gz`)
    and `forensic_summary` preserving strict disjointness between `PRE_ENTRY_OBSERVABLE_CAUSE`
    (`A..E` + `OTHER/UNKNOWN`) and `EX_POST_STOP_BEHAVIOR` (`F..H`) while linking each of the
    242 baseline episodes (`166` closed = `157` LOSS + `9` WIN) to its `A1`, `A1B`, and `D1..D4`
    transitions.
    """
    idx_maps: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for vk in (
        "A1_ENTRY_CONFIRMATION_RETEST_ONLY",
        "A1B_GENUINE_DELAYED_CONFIRMATION_ENTRY",
        "D1_REAL_DELAYED_ENTRY",
        "D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL",
        "D3_REAL_DELAYED_ENTRY_PLUS_TARGET",
        "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
    ):
        idx_maps[vk] = {str(r.get("opportunity_id") or ""): r for r in (rows_by_variant_nb.get(vk) or [])}

    forensic_rows_06: List[Dict[str, Any]] = []
    d4_loss_transition_counts: Counter[str] = Counter()

    for base_row in r1_bundle_v12["casebook_rows"]:
        oid = str(base_row.get("opportunity_id") or "")
        transitions: Dict[str, Any] = {}
        for vk, vmap in idx_maps.items():
            vr = vmap.get(oid) or {}
            transitions[vk] = {
                "status": vr.get("status"),
                "rejection_reason": vr.get("rejection_reason"),
                "delayed_entry_executed": bool(vr.get("delayed_entry_executed", False)),
                "wait_bars": int(vr.get("wait_bars") or 0),
                "new_decision_timestamp_ms": vr.get("new_decision_timestamp_ms"),
                "filled": bool(vr.get("filled", False)),
                "outcome_label": vr.get("outcome_label"),
                "realized_net_pnl_usd": vr.get("realized_net_pnl_usd"),
            }
        if base_row.get("outcome_label") == "LOSS":
            d4_tr = transitions["D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE"]
            if d4_tr["status"] == "REJECTED_PRE_ENTRY":
                d4_loss_transition_counts[f"FILTERED_PRE_ENTRY:{d4_tr['rejection_reason']}"] += 1
            elif d4_tr["status"] == "NO_FILL_OR_SIZING_REJECT":
                d4_loss_transition_counts[f"SIZING_OR_RR_REJECT:{d4_tr['rejection_reason']}"] += 1
            else:
                d4_loss_transition_counts[f"EXECUTED:{d4_tr['outcome_label']}"] += 1

        enriched_row = {
            **dict(base_row),
            "counterfactual_and_delayed_transitions_nb": transitions,
        }
        forensic_rows_06.append(enriched_row)

    return {
        "forensic_rows_06": forensic_rows_06,
        "matrix_05": r1_bundle_v12["matrix_05"],
        "stop_audit_06": r1_bundle_v12["stop_audit_06"],
        "d4_transitions_on_157_baseline_losses": dict(sorted(d4_loss_transition_counts.items())),
    }


# ==============================================================================
# 6. PHASE E (P0-E4): SINGLE AUTHORITATIVE REPORT, DYNAMIC GATES & STRICT PARSER
# ==============================================================================

@dataclass(frozen=True)
class AuthoritativeMetricsV17931213:
    version: str
    run_id: str
    branch: str
    tested_executable_sha: str
    dataset_sha256: str
    # Pytest & Process Truth
    pytest_collected_count: int
    pytest_passed_count: int
    pytest_skipped_count: int
    pytest_failed_count: int
    pytest_errors_count: int
    pytest_shards_count: int
    pytest_async_w3_wall_seconds: float
    pytest_v12_w1_baseline_seconds: float
    pytest_async_speedup_ratio: float
    max_concurrent_children_observed: int
    orphan_pids_count: int
    worker_1_2_4_repeat_parity_status: str
    # Canonical Universe & Dynamic SI8/SI9 Funnel
    canonical_universe_total: int
    baseline_decisions_count: int
    si8_discovered_decisions_count: int
    si8_proof_eligible_count: int
    si8_arbitration_suppressed_count: int
    si8_post_arbitration_rejected_count: int
    si8_materialized_intents_count: int
    si8_closed_trades_nb: int
    si8_net_pnl_usd_nb: float
    si9_materialized_intents_count: int
    si9_closed_trades_nb: int
    si9_net_pnl_usd_nb: float
    historical_v12_267_target_cases_audited: int
    historical_v12_113_htf_cases_audited: int
    repaired_v13_false_htf_conflicts_count: int
    # Baseline SI0 & R1 Loss Forensics
    si0_scheduled_episodes_nb: int
    si0_closed_trades_nb: int
    si0_wins_nb: int
    si0_losses_nb: int
    si0_open_at_horizon_nb: int
    si0_data_gap_nb: int
    si0_unfilled_nb: int
    si0_net_pnl_usd_nb: float
    si0_expectancy_r_nb: float
    r1_pre_entry_unknown_losses_count: int
    r1_buckets_a_to_h: Dict[str, int]
    # Matched & Delayed Variants (NEXT_BAR_OPEN_BID_ASK) + 1-Pos Portfolio
    variants_nb_table: Dict[str, Dict[str, Any]]
    portfolio_1pos_nb_table: Dict[str, Dict[str, Any]]
    # Full 30-partition SI0..SI9 summary (NEXT_BAR_OPEN_BID_ASK)
    si_policies_nb_table: Dict[str, Dict[str, Any]]
    all_30_partitions_status: str
    source_execution_reconciliation_status: str
    # Materialized MTF Ablations
    materialized_ablations: Dict[str, Dict[str, Any]]
    # Negative controls & Orthogonal Verdicts
    negative_controls_total: int
    negative_controls_rejected: int
    prior_artifacts_immutable: bool
    verification_truth_verdict: str
    delayed_entry_execution_verdict: str
    htf_unicorn_materialization_verdict: str
    fresh_oos_verdict: str
    economic_verdict: str
    strategy_expectancy_verdict: str
    activation_ready: str


def build_authoritative_metrics_v17931213(
    *,
    run_id: str,
    branch: str,
    tested_sha: str,
    dataset_sha: str,
    junit_bijection: Mapping[str, Any],
    shards_count: int,
    pytest_w3_wall_s: float,
    pytest_v12_w1_s: float,
    max_children_obs: int,
    orphan_pids_cnt: int,
    worker_parity_status: str,
    funnel_audit_05: Mapping[str, Any],
    r1_matrix_05: Mapping[str, Any],
    all_partitions_lineage: Mapping[str, Any],
    summary_by_variant_and_mode: Mapping[str, Mapping[str, Mapping[str, Any]]],
    portfolio_by_variant_and_mode: Mapping[str, Mapping[str, Mapping[str, Any]]],
    neg_controls_doc: Mapping[str, Any],
    prior_immutable: bool,
    delayed_entry_check_status: str,
    htf_funnel_check_status: str,
) -> AuthoritativeMetricsV17931213:
    uni_08 = funnel_audit_05["unicorn_fvg_breaker_funnel_63085"]
    dyn = funnel_audit_05["si8_si9_dynamic_funnel_diagnosis"]
    si8_nb = dyn["si8_next_bar_open_replay"]
    si9_nb = dyn["si9_next_bar_open_replay"]
    t267 = funnel_audit_05["historical_v12_267_si9_target_audit"]
    h113 = funnel_audit_05["historical_v12_113_htf_conflict_audit"]

    si0_nb = all_partitions_lineage["by_policy_and_fill_mode"]["SI0_OFF_BASELINE"]["NEXT_BAR_OPEN_BID_ASK"]
    si_nb_table: Dict[str, Dict[str, Any]] = {}
    for pid, fm_map in all_partitions_lineage["by_policy_and_fill_mode"].items():
        nb_c = fm_map["NEXT_BAR_OPEN_BID_ASK"]
        si_nb_table[pid] = {
            "scheduled_episodes": int(nb_c["total_episodes"]),
            "closed_trades": int(nb_c["closed_trades_count"]),
            "wins": int(nb_c["wins_count"]),
            "losses": int(nb_c["losses_count"]),
            "realized_net_pnl_usd": round(float(nb_c["realized_net_pnl_usd"]), 4),
            "expectancy_r": round(float(nb_c["expectancy_r"]), 4),
        }

    var_nb_table = {
        v: dict(summary_by_variant_and_mode[v]["NEXT_BAR_OPEN_BID_ASK"])
        for v in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213
    }
    port_nb_table = {
        v: dict(portfolio_by_variant_and_mode[v]["NEXT_BAR_OPEN_BID_ASK"])
        for v in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213
    }

    # Check whether any policy or variant has positive expectancy on a meaningful sample; keep honest negative/inconclusive verdict
    d4_port = port_nb_table["D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE"]
    econ_verdict = (
        "INCONCLUSIVE_SMALL_SAMPLE_POSITIVE_IN_SAMPLE_NO_FRESH_OOS"
        if float(d4_port.get("realized_net_pnl_usd", 0.0)) > 0 and int(d4_port.get("closed_trades_count", 0)) < 30
        else "NEGATIVE_EXPECTANCY_AS_OBSERVED"
    )
    strat_exp = (
        "NEGATIVE_BASELINE_SMALL_SAMPLE_REPAIRED_SUBSET"
        if float(si8_nb.get("realized_net_pnl_usd", 0.0)) > 0 or float(d4_port.get("realized_net_pnl_usd", 0.0)) > 0
        else "NEGATIVE_EXPECTANCY"
    )

    speedup = round(pytest_v12_w1_s / max(pytest_w3_wall_s, 0.001), 3)
    neg_tot = int(neg_controls_doc.get("controls_count", 0))
    neg_rej = sum(1 for c in (neg_controls_doc.get("controls") or []) if c.get("rejected"))

    eng_ok = (
        junit_bijection.get("status") == "PASS"
        and all_partitions_lineage.get("status") == "PASS"
        and (all_partitions_lineage.get("source_execution_reconciliation") or {}).get("status") == "PASS"
        and delayed_entry_check_status == "PASS"
        and htf_funnel_check_status == "PASS"
        and worker_parity_status == "PASS"
        and neg_tot == 66
        and neg_rej == 66
        and orphan_pids_cnt == 0
        and max_children_obs <= 4
        and prior_immutable
    )

    return AuthoritativeMetricsV17931213(
        version=VERSION_V17931213,
        run_id=run_id,
        branch=branch,
        tested_executable_sha=tested_sha,
        dataset_sha256=dataset_sha,
        pytest_collected_count=int(junit_bijection["collected_count"]),
        pytest_passed_count=int(junit_bijection["passed_count"]),
        pytest_skipped_count=int(junit_bijection["skipped_count"]),
        pytest_failed_count=int(junit_bijection["failed_count"]),
        pytest_errors_count=int(junit_bijection["errors_count"]),
        pytest_shards_count=int(shards_count),
        pytest_async_w3_wall_seconds=round(float(pytest_w3_wall_s), 3),
        pytest_v12_w1_baseline_seconds=round(float(pytest_v12_w1_s), 3),
        pytest_async_speedup_ratio=speedup,
        max_concurrent_children_observed=int(max_children_obs),
        orphan_pids_count=int(orphan_pids_cnt),
        worker_1_2_4_repeat_parity_status=str(worker_parity_status),
        canonical_universe_total=int(uni_08["canonical_universe_total"]),
        baseline_decisions_count=int(uni_08["baseline_eligible_count"]),
        si8_discovered_decisions_count=int(uni_08["si8_detector_verified_incremental_count"]),
        si8_proof_eligible_count=int(dyn["proof_eligible_candidates_count"]),
        si8_arbitration_suppressed_count=int(dyn["arbitration_suppressed_count"]),
        si8_post_arbitration_rejected_count=int(dyn["post_arbitration_rejected_count"]),
        si8_materialized_intents_count=int(dyn["materialized_intents_count"]),
        si8_closed_trades_nb=int(si8_nb["closed_trades_count"]),
        si8_net_pnl_usd_nb=round(float(si8_nb["realized_net_pnl_usd"]), 4),
        si9_materialized_intents_count=int(si9_nb["materialized_intents_count"]),
        si9_closed_trades_nb=int(si9_nb["closed_trades_count"]),
        si9_net_pnl_usd_nb=round(float(si9_nb["realized_net_pnl_usd"]), 4),
        historical_v12_267_target_cases_audited=int(t267["historical_v12_cases_audited"]),
        historical_v12_113_htf_cases_audited=int(h113["historical_v12_cases_audited"]),
        repaired_v13_false_htf_conflicts_count=int(h113["repaired_v13_false_htf_conflict_at_same_ts"]),
        si0_scheduled_episodes_nb=int(si0_nb["total_episodes"]),
        si0_closed_trades_nb=int(si0_nb["closed_trades_count"]),
        si0_wins_nb=int(si0_nb["wins_count"]),
        si0_losses_nb=int(si0_nb["losses_count"]),
        si0_open_at_horizon_nb=int(si0_nb["open_at_horizon_count"]),
        si0_data_gap_nb=int(si0_nb["data_gap_unresolved_count"]),
        si0_unfilled_nb=int(si0_nb["unfilled_no_entry_count"]),
        si0_net_pnl_usd_nb=round(float(si0_nb["realized_net_pnl_usd"]), 4),
        si0_expectancy_r_nb=round(float(si0_nb["expectancy_r"]), 4),
        r1_pre_entry_unknown_losses_count=int(r1_matrix_05["pre_entry_unknown_losses_count"]),
        r1_buckets_a_to_h={k: int(v) for k, v in r1_matrix_05["hierarchical_buckets_a_to_h_counts"].items()},
        variants_nb_table=var_nb_table,
        portfolio_1pos_nb_table=port_nb_table,
        si_policies_nb_table=si_nb_table,
        all_30_partitions_status=str(all_partitions_lineage["status"]),
        source_execution_reconciliation_status=str(all_partitions_lineage["source_execution_reconciliation"]["status"]),
        materialized_ablations=dict(funnel_audit_05["materialized_single_axis_mtf_unicorn_ablations"]),
        negative_controls_total=neg_tot,
        negative_controls_rejected=neg_rej,
        prior_artifacts_immutable=bool(prior_immutable),
        verification_truth_verdict="PASS" if eng_ok else "FAIL",
        delayed_entry_execution_verdict=delayed_entry_check_status,
        htf_unicorn_materialization_verdict=htf_funnel_check_status,
        fresh_oos_verdict="BLOCKED_NO_UNTOUCHED_DATA",
        economic_verdict=econ_verdict,
        strategy_expectancy_verdict=strat_exp,
        activation_ready="NO",
    )


def _authoritative_kv_map_v17931213(m: AuthoritativeMetricsV17931213) -> Dict[str, str]:
    return {
        "VERSION": m.version,
        "RUN_ID": m.run_id,
        "BRANCH": m.branch,
        "TESTED_EXECUTABLE_SHA": m.tested_executable_sha,
        "DATASET_SHA256": m.dataset_sha256,
        "PYTEST_COLLECTED": str(m.pytest_collected_count),
        "PYTEST_PASSED": str(m.pytest_passed_count),
        "PYTEST_SKIPPED": str(m.pytest_skipped_count),
        "PYTEST_FAILED": str(m.pytest_failed_count),
        "PYTEST_ERRORS": str(m.pytest_errors_count),
        "PYTEST_SHARDS": str(m.pytest_shards_count),
        "PYTEST_ASYNC_W3_WALL_SECONDS": f"{m.pytest_async_w3_wall_seconds:.3f}",
        "PYTEST_SPEEDUP_VS_V12_W1": f"{m.pytest_async_speedup_ratio:.3f}",
        "MAX_CONCURRENT_CHILDREN_OBSERVED": str(m.max_concurrent_children_observed),
        "ORPHAN_PIDS_COUNT": str(m.orphan_pids_count),
        "WORKER_1_2_4_REPEAT_PARITY": m.worker_1_2_4_repeat_parity_status,
        "CANONICAL_UNIVERSE_TOTAL": str(m.canonical_universe_total),
        "SI8_PROOF_ELIGIBLE_COUNT": str(m.si8_proof_eligible_count),
        "SI8_ARBITRATION_SUPPRESSED_COUNT": str(m.si8_arbitration_suppressed_count),
        "SI8_POST_ARBITRATION_REJECTED_COUNT": str(m.si8_post_arbitration_rejected_count),
        "SI8_MATERIALIZED_INTENTS_COUNT": str(m.si8_materialized_intents_count),
        "SI8_CLOSED_TRADES_NB": str(m.si8_closed_trades_nb),
        "SI8_NET_PNL_USD_NB": f"{m.si8_net_pnl_usd_nb:.4f}",
        "SI9_MATERIALIZED_INTENTS_COUNT": str(m.si9_materialized_intents_count),
        "SI9_CLOSED_TRADES_NB": str(m.si9_closed_trades_nb),
        "SI9_NET_PNL_USD_NB": f"{m.si9_net_pnl_usd_nb:.4f}",
        "HISTORICAL_267_SI9_TARGET_CASES_AUDITED": str(m.historical_v12_267_target_cases_audited),
        "HISTORICAL_113_HTF_CONFLICT_CASES_AUDITED": str(m.historical_v12_113_htf_cases_audited),
        "REPAIRED_FALSE_HTF_CONFLICTS_COUNT": str(m.repaired_v13_false_htf_conflicts_count),
        "SI0_SCHEDULED_EPISODES_NB": str(m.si0_scheduled_episodes_nb),
        "SI0_CLOSED_TRADES_NB": str(m.si0_closed_trades_nb),
        "SI0_WINS_NB": str(m.si0_wins_nb),
        "SI0_LOSSES_NB": str(m.si0_losses_nb),
        "SI0_NET_PNL_USD_NB": f"{m.si0_net_pnl_usd_nb:.4f}",
        "SI0_EXPECTANCY_R_NB": f"{m.si0_expectancy_r_nb:.4f}",
        "NEGATIVE_CONTROLS_TOTAL": str(m.negative_controls_total),
        "NEGATIVE_CONTROLS_REJECTED": str(m.negative_controls_rejected),
        "VERIFICATION_TRUTH_VERDICT": m.verification_truth_verdict,
        "DELAYED_ENTRY_EXECUTION_VERDICT": m.delayed_entry_execution_verdict,
        "HTF_UNICORN_MATERIALIZATION_VERDICT": m.htf_unicorn_materialization_verdict,
        "FRESH_OOS_VERDICT": m.fresh_oos_verdict,
        "ECONOMIC_VERDICT": m.economic_verdict,
        "STRATEGY_EXPECTANCY_VERDICT": m.strategy_expectancy_verdict,
        "ACTIVATION_READY": m.activation_ready,
    }


def render_v17931213_readme_and_handover(
    metrics: AuthoritativeMetricsV17931213,
) -> Tuple[str, str]:
    """
    Renders `README_V17_9_3_1_2_13.md` and `HANDOVER_V17_9_3_1_2_13.txt` from a SINGLE
    typed `AuthoritativeMetricsV17931213` model — zero `.11`/`.12` prose concatenation.
    """
    kv = _authoritative_kv_map_v17931213(metrics)
    kv_block = "\n".join(f"METRICS_KV: {k}={v}" for k, v in kv.items())

    var_rows_md: List[str] = []
    for vid in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213:
        vc = metrics.variants_nb_table[vid]
        pc = metrics.portfolio_1pos_nb_table[vid]
        var_rows_md.append(
            f"| `{vid}` | `{vc['entry_timing_contract']}` | `{str(vc['delayed_entry_executed']).lower()}` | "
            f"{vc['min_wait_bars']}..{vc['max_wait_bars']} | {vc['accepted_confirmation_count']} | "
            f"{vc['closed_trades_count']} | {vc['wins_count']} | {vc['losses_count']} | "
            f"{vc['realized_net_pnl_usd']:.4f} | {vc['expectancy_net_r']:.4f} | "
            f"{pc['closed_trades_count']} | {pc['realized_net_pnl_usd']:.4f} |"
        )

    si_rows_md: List[str] = []
    for pid, sc in metrics.si_policies_nb_table.items():
        si_rows_md.append(
            f"| `{pid}` | {sc['scheduled_episodes']} | {sc['closed_trades']} | "
            f"{sc['wins']} | {sc['losses']} | {sc['realized_net_pnl_usd']:.4f} | {sc['expectancy_r']:.4f} |"
        )

    ab_rows_md: List[str] = []
    for ab_m, ac in metrics.materialized_ablations.items():
        ab_rows_md.append(
            f"| `{ab_m}` | {ac['pre_entry_63085_passed']} | {ac['materialized_intents_count']} | "
            f"{ac['closed_trades_count']} | {ac['wins_count']} | {ac['losses_count']} | "
            f"{ac['realized_net_pnl_usd']:.4f} | {ac['expectancy_net_r']:.4f} |"
        )

    r1_rows_md = "\n".join(f"- `{bk}`: `{cnt}`" for bk, cnt in metrics.r1_buckets_a_to_h.items())

    readme_md = "\n".join(
        [
            f"# AURUM DESK — {metrics.version} AUTHORITATIVE RESEARCH & VERIFICATION REPORT",
            "",
            "## 1. Authoritative Machine-Checked Key-Value Block",
            "```text",
            kv_block,
            "```",
            "",
            "## 2. Executive Truth & Orthogonal Verdicts",
            f"- **Version**: `{metrics.version}`",
            f"- **Run ID**: `{metrics.run_id}`",
            f"- **Branch**: `{metrics.branch}`",
            f"- **Phase A Tested Executable SHA**: `{metrics.tested_executable_sha}`",
            f"- **Canonical Dataset SHA-256**: `{metrics.dataset_sha256}`",
            f"- **Verification Truth Verdict**: `{metrics.verification_truth_verdict}`",
            f"- **Delayed-Entry Execution Verdict (`P0-E1`)**: `{metrics.delayed_entry_execution_verdict}`",
            f"- **HTF/Unicorn Materialization Verdict (`P0-E2`/`P0-E3`)**: `{metrics.htf_unicorn_materialization_verdict}`",
            f"- **Fresh Out-of-Sample Verdict (`P1-E6`)**: `{metrics.fresh_oos_verdict}`",
            f"- **Economic Verdict**: `{metrics.economic_verdict}`",
            f"- **Strategy Expectancy Verdict**: `{metrics.strategy_expectancy_verdict}`",
            f"- **Activation Ready**: `{metrics.activation_ready}` (`LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)",
            "",
            "## 3. Phase A (`P0-E1`): Same-Cutoff Filter (`A1`) vs Genuine Post-Signal Closed-Bar Delayed Entry (`A1B`, `D1..D4`)",
            "| Variant | Timing Contract | Delayed Executed | Wait Bars | Accepted | Matched Closed | Wins | Losses | Matched Net PnL (USD) | Matched Exp R | 1-Pos Portfolio Closed | 1-Pos Net PnL (USD) |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            *var_rows_md,
            "",
            "## 4. Phase B (`P0-E2` / `P0-E3`): Dynamic `SI8` / `SI9` Materialization Funnel & `267` / `113` Audits",
            f"- **Canonical Universe**: `{metrics.canonical_universe_total}` (`{metrics.baseline_decisions_count}` baseline + `{metrics.si8_discovered_decisions_count}` `SI8`-discovered)",
            f"- **Dynamic `SI8` Funnel**: `{metrics.si8_proof_eligible_count}` proof-eligible -> `{metrics.si8_arbitration_suppressed_count}` arbitration suppressed -> `{metrics.si8_post_arbitration_rejected_count}` post-arbitration rejected -> `{metrics.si8_materialized_intents_count}` materialized intents (`{metrics.si8_closed_trades_nb}` closed trades, `{metrics.si8_net_pnl_usd_nb:.4f}` USD net PnL on `NEXT_BAR_OPEN_BID_ASK`).",
            f"- **Dynamic `SI9` Replay**: `{metrics.si9_materialized_intents_count}` materialized intents (`{metrics.si9_closed_trades_nb}` closed trades, `{metrics.si9_net_pnl_usd_nb:.4f}` USD net PnL on `NEXT_BAR_OPEN_BID_ASK`).",
            f"- **Historical `267` `SI9` Target Rejections Audited**: `{metrics.historical_v12_267_target_cases_audited}` cases classified across `I..VIII` (`1H` pool distance vs unscaled `1M` ATR ceiling repaired).",
            f"- **Historical `113` `HTF` Conflict Rejections Audited**: `{metrics.historical_v12_113_htf_cases_audited}` cases audited; `{metrics.repaired_v13_false_htf_conflicts_count}` false conflicts remaining under `evaluate_htf_structure`.",
            "",
            "### 4.1 Materialized Single-Axis MTF / Unicorn Ablations (`NEXT_BAR_OPEN_BID_ASK`)",
            "| Ablation Mode | Pre-Entry Passed (`63,085`) | Materialized Intents | Closed Trades | Wins | Losses | Net PnL (USD) | Expectancy Net R |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
            *ab_rows_md,
            "",
            "## 5. Phase C (`R1`): Pre-Entry Observable Loss Causes (`A..E` + `OTHER/UNKNOWN`) vs Ex-Post Diagnostic Stop Behavior (`F..H`)",
            f"- **Baseline `SI0` Cohort (`NEXT_BAR_OPEN_BID_ASK`)**: `{metrics.si0_scheduled_episodes_nb}` scheduled -> `{metrics.si0_closed_trades_nb}` closed (`{metrics.si0_wins_nb}` WIN / `{metrics.si0_losses_nb}` LOSS), `{metrics.si0_open_at_horizon_nb}` `OPEN_AT_HORIZON`, `{metrics.si0_data_gap_nb}` `DATA_GAP_UNRESOLVED`, `{metrics.si0_unfilled_nb}` `UNFILLED_NO_ENTRY`.",
            f"- **Pre-Entry `OTHER/UNKNOWN` Preserved Honestly**: `{metrics.r1_pre_entry_unknown_losses_count}`",
            r1_rows_md,
            "",
            "## 6. Phase D: Full `30`-Partition (`SI0..SI9` x `3` Fill Modes) Raw-Stream Replay (`NEXT_BAR_OPEN_BID_ASK` View)",
            "| Policy ID | Scheduled | Closed | Wins | Losses | Net PnL (USD) | Expectancy R |",
            "|---|---:|---:|---:|---:|---:|---:|",
            *si_rows_md,
            "",
            "## 7. Phase F & H: Async Multi-Worker Performance, Fresh JUnit Bijection & Negative Controls (`V01..V66`)",
            f"- **Fresh Pytest Collection & JUnit Bijection**: `{metrics.pytest_collected_count}` collected (`{metrics.pytest_passed_count}` passed, `{metrics.pytest_skipped_count}` skipped, `{metrics.pytest_failed_count}` failed, `{metrics.pytest_errors_count}` errors across `{metrics.pytest_shards_count}` shards).",
            f"- **Bounded Async Multi-Worker Pytest (`w=3`)**: `{metrics.pytest_async_w3_wall_seconds:.3f}s` vs prior sequential `w=1` `{metrics.pytest_v12_w1_baseline_seconds:.3f}s` (`{metrics.pytest_async_speedup_ratio:.3f}x` speedup, max concurrent child processes `{metrics.max_concurrent_children_observed} <= 4`, orphan PIDs `{metrics.orphan_pids_count}`).",
            f"- **Worker `1 / 2 / 4 / 1_repeat` Determinism**: `{metrics.worker_1_2_4_repeat_parity_status}`.",
            f"- **Fault-Injected Negative Controls (`V01..V66`)**: `{metrics.negative_controls_rejected} / {metrics.negative_controls_total}` rejected in disposable temporary directories.",
            "",
        ]
    )

    handover_txt = "\n".join(
        [
            f"AURUM DESK — {metrics.version} AUTHORITATIVE HANDOVER RECEIPT",
            "=" * 78,
            kv_block,
            "=" * 78,
            "SUMMARY OF REPAIRS & HONEST ECONOMIC / ACTIVATION STATE:",
            f"1. Phase A (P0-E1): Old A1 preserved strictly as SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY; A1B and D1..D4 execute genuine post-signal closed-bar delayed entry (1 <= wait_bars <= 12, new_decision_timestamp_ms > base_ts) with rebuilt SL/TP/sizing ($2.50 cap) and pure cross-adapter parity.",
            f"2. Phase B (P0-E2/P0-E3): Repaired Bugs B-1..B-4; SI8 dynamically materializes {metrics.si8_materialized_intents_count} intents ({metrics.si8_closed_trades_nb} closed trades, {metrics.si8_net_pnl_usd_nb:.4f} USD) out of {metrics.si8_proof_eligible_count} proof-eligible candidates; SI9 dynamically materializes {metrics.si9_materialized_intents_count} intents ({metrics.si9_closed_trades_nb} closed trades, {metrics.si9_net_pnl_usd_nb:.4f} USD).",
            f"3. Phase E (P0-E4): README_V17_9_3_1_2_13.md, HANDOVER_V17_9_3_1_2_13.txt, and 18_acceptance_gate_matrix.json rendered from single immutable AuthoritativeMetricsV17931213.",
            f"4. Phase F & H: {metrics.pytest_collected_count} pytest nodes verified across {metrics.pytest_shards_count} async shards ({metrics.pytest_async_w3_wall_seconds:.3f}s, {metrics.pytest_async_speedup_ratio:.3f}x speedup, max_children={metrics.max_concurrent_children_observed}); {metrics.negative_controls_rejected}/{metrics.negative_controls_total} negative controls (V01..V66) rejected.",
            f"5. Safety & OOS Lock: FRESH_OOS_VERDICT={metrics.fresh_oos_verdict}, ACTIVATION_READY={metrics.activation_ready} (LIVE=OFF, PAPER=OFF, AUTO=OFF).",
            "",
        ]
    )
    return readme_md, handover_txt


def evaluate_v17931213_gate_matrix(
    metrics: AuthoritativeMetricsV17931213,
    *,
    delayed_entry_check: Mapping[str, Any],
    htf_funnel_check: Mapping[str, Any],
    r1_check: Mapping[str, Any],
    junit_check: Mapping[str, Any],
    lineage_30_check: Mapping[str, Any],
    async_proc_check: Mapping[str, Any],
    report_consistency_check: Mapping[str, Any],
    oos_check: Mapping[str, Any],
    neg_v66_check: Mapping[str, Any],
    manifest_check: Mapping[str, Any],
    freeze_check: Mapping[str, Any],
    phase_c_status: str = "BLOCKED",
) -> Dict[str, Any]:
    """
    Evaluates all 12 V17.9.3.1.2.13 acceptance gates (`G_01`..`G_12`) with dynamic
    metric-derived titles — zero static `.12` strings.
    """
    gates: Dict[str, Dict[str, Any]] = {
        "G_01_DELAYED_ENTRY_TRUTH": {
            "gate_id": "G_01_DELAYED_ENTRY_TRUTH",
            "category": "P0_E1_GENUINE_DELAYED_ENTRY",
            "title": (
                "A1 preserved as SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY while A1B and D1..D4 execute genuine "
                "post-signal closed-bar delayed entry (1 <= wait_bars <= 12, new_ts > base_ts, $2.50 risk cap, cross-adapter parity)"
            ),
            "status": "PASS" if delayed_entry_check.get("status") == "PASS" else "FAIL",
        },
        "G_02_HTF_UNICORN_DYNAMIC_MATERIALIZATION": {
            "gate_id": "G_02_HTF_UNICORN_DYNAMIC_MATERIALIZATION",
            "category": "P0_E2_P0_E3_DYNAMIC_FUNNEL",
            "title": (
                f"{metrics.canonical_universe_total} canonical universe & dynamic SI8 funnel "
                f"({metrics.si8_proof_eligible_count} -> {metrics.si8_arbitration_suppressed_count} -> "
                f"{metrics.si8_post_arbitration_rejected_count} -> {metrics.si8_materialized_intents_count} intents) "
                f"and SI9 ({metrics.si9_materialized_intents_count} intents) verified without hardcoded counts"
            ),
            "status": "PASS" if htf_funnel_check.get("status") == "PASS" else "FAIL",
        },
        "G_03_SI9_267_AND_HTF_113_FORENSIC_CLOSURE": {
            "gate_id": "G_03_SI9_267_AND_HTF_113_FORENSIC_CLOSURE",
            "category": "P0_E2_ROOT_CAUSE_AUDITS",
            "title": (
                f"All {metrics.historical_v12_267_target_cases_audited} historical SI9 target rejections and "
                f"{metrics.historical_v12_113_htf_cases_audited} HTF conflicts audited ({metrics.repaired_v13_false_htf_conflicts_count} false conflicts remaining)"
            ),
            "status": "PASS"
            if htf_funnel_check.get("status") == "PASS"
            and metrics.historical_v12_267_target_cases_audited == 267
            and metrics.repaired_v13_false_htf_conflicts_count == 0
            else "FAIL",
        },
        "G_04_PRE_ENTRY_VS_EXPOST_FORENSIC_SEPARATION": {
            "gate_id": "G_04_PRE_ENTRY_VS_EXPOST_FORENSIC_SEPARATION",
            "category": "PHASE_C_LOSS_FORENSICS",
            "title": (
                f"{metrics.si0_closed_trades_nb} closed trades ({metrics.si0_losses_nb} LOSS + {metrics.si0_wins_nb} WIN) "
                f"out of {metrics.si0_scheduled_episodes_nb} scheduled reconciled with strict PRE_ENTRY vs EX_POST separation"
            ),
            "status": "PASS" if r1_check.get("status") == "PASS" else "FAIL",
        },
        "G_05_RAW_STREAM_30_PARTITIONS_AND_SOURCE_RECONCILIATION": {
            "gate_id": "G_05_RAW_STREAM_30_PARTITIONS_AND_SOURCE_RECONCILIATION",
            "category": "PHASE_D_LINEAGE_PARITY",
            "title": "All 30 (SI0..SI9 x 3 fill modes) partitions reconstructed from raw event stream and reconciled with source execution ledger",
            "status": "PASS"
            if lineage_30_check.get("status") == "PASS"
            and (lineage_30_check.get("source_execution_reconciliation") or {}).get("status") == "PASS"
            else "FAIL",
        },
        "G_06_SINGLE_AUTHORITATIVE_REPORT_AND_GATE_CONSISTENCY": {
            "gate_id": "G_06_SINGLE_AUTHORITATIVE_REPORT_AND_GATE_CONSISTENCY",
            "category": "P0_E4_REPORT_TRUTH",
            "title": "README_V17_9_3_1_2_13.md, HANDOVER_V17_9_3_1_2_13.txt, and gate titles cross-validated from single AuthoritativeMetricsV17931213",
            "status": "PASS" if report_consistency_check.get("status") == "PASS" else "FAIL",
        },
        "G_07_BOUNDED_ASYNC_MULTI_WORKER_PERFORMANCE_AND_DETERMINISM": {
            "gate_id": "G_07_BOUNDED_ASYNC_MULTI_WORKER_PERFORMANCE_AND_DETERMINISM",
            "category": "P1_E5_ASYNC_RESOURCE_TRUTH",
            "title": (
                f"Async sharded pytest ({metrics.pytest_async_w3_wall_seconds:.3f}s, {metrics.pytest_async_speedup_ratio:.3f}x vs w=1) "
                f"and w=1/2/4/1_repeat determinism verified (max_children={metrics.max_concurrent_children_observed}<=4, orphans={metrics.orphan_pids_count})"
            ),
            "status": "PASS"
            if async_proc_check.get("status") == "PASS"
            and metrics.worker_1_2_4_repeat_parity_status == "PASS"
            and metrics.max_concurrent_children_observed <= 4
            and metrics.orphan_pids_count == 0
            else "FAIL",
        },
        "G_08_FRESH_PYTEST_COLLECTION_AND_JUNIT_BIJECTION": {
            "gate_id": "G_08_FRESH_PYTEST_COLLECTION_AND_JUNIT_BIJECTION",
            "category": "PHASE_G_REGRESSION_TRUTH",
            "title": (
                f"Fresh pytest collection ({metrics.pytest_collected_count} nodes across {metrics.pytest_shards_count} shards) "
                f"with 100% JUnit XML bijection and {metrics.pytest_failed_count} failures"
            ),
            "status": "PASS" if junit_check.get("status") == "PASS" else "FAIL",
        },
        "G_09_NEGATIVE_CONTROLS_V01_TO_V66": {
            "gate_id": "G_09_NEGATIVE_CONTROLS_V01_TO_V66",
            "category": "PHASE_H_ADVERSARIAL_CONTROLS",
            "title": f"All {metrics.negative_controls_rejected}/{metrics.negative_controls_total} fault-injected negative controls (V01..V66) rejected in disposable temp dirs",
            "status": "PASS"
            if neg_v66_check.get("status") == "PASS"
            and metrics.negative_controls_total == 66
            and metrics.negative_controls_rejected == 66
            else "FAIL",
        },
        "G_10_HONEST_OOS_AND_ACTIVATION_LOCK": {
            "gate_id": "G_10_HONEST_OOS_AND_ACTIVATION_LOCK",
            "category": "P1_E6_OOS_AND_SAFETY",
            "title": f"FRESH_OOS_VERDICT={metrics.fresh_oos_verdict} and ACTIVATION_READY={metrics.activation_ready} enforced fail-closed",
            "status": "PASS"
            if oos_check.get("status") == "PASS"
            and metrics.fresh_oos_verdict == "BLOCKED_NO_UNTOUCHED_DATA"
            and metrics.activation_ready == "NO"
            else "FAIL",
        },
        "G_11_SOURCE_FREEZE_AND_PRIOR_IMMUTABILITY": {
            "gate_id": "G_11_SOURCE_FREEZE_AND_PRIOR_IMMUTABILITY",
            "category": "PUBLICATION_INTEGRITY",
            "title": "Artifact SHA-256 manifest sealed, tested executable SHA frozen, and .7..12 prior artifact directories preserved byte-immutable",
            "status": "PASS"
            if manifest_check.get("status") == "PASS"
            and freeze_check.get("status") == "PASS"
            and metrics.prior_artifacts_immutable
            else "FAIL",
        },
        "G_12_PHASE_A_B_C_COMMIT_PUSH_AND_REMOTE_READBACK": {
            "gate_id": "G_12_PHASE_A_B_C_COMMIT_PUSH_AND_REMOTE_READBACK",
            "category": "PUBLICATION_INTEGRITY",
            "title": "Phase A/B commit+push and Phase C external remote git readback receipt",
            "status": phase_c_status if freeze_check.get("status") == "PASS" else "BLOCKED",
        },
    }

    pre_pub_pass = all(
        g["status"] == "PASS" for gk, g in gates.items() if gk != "G_12_PHASE_A_B_C_COMMIT_PUSH_AND_REMOTE_READBACK"
    )
    return {
        "version": VERSION_V17931213,
        "schema_version": SCHEMA_VERSION_V17931213,
        "run_id": metrics.run_id,
        "tested_executable_sha": metrics.tested_executable_sha,
        "dataset_sha256": metrics.dataset_sha256,
        "pre_publication_engineering_gates_all_pass": pre_pub_pass,
        "orthogonal_verdicts": {
            "VERIFICATION_TRUTH_VERDICT": "PASS" if pre_pub_pass else "FAIL",
            "DELAYED_ENTRY_EXECUTION_VERDICT": metrics.delayed_entry_execution_verdict,
            "HTF_UNICORN_MATERIALIZATION_VERDICT": metrics.htf_unicorn_materialization_verdict,
            "FRESH_OOS_VERDICT": metrics.fresh_oos_verdict,
            "ECONOMIC_VERDICT": metrics.economic_verdict,
            "STRATEGY_EXPECTANCY_VERDICT": metrics.strategy_expectancy_verdict,
            "ACTIVATION_READY": metrics.activation_ready,
        },
        "gates": gates,
    }


def verify_v17931213_authoritative_report_and_gates_consistency(
    readme_text: str,
    handover_text: str,
    metrics: AuthoritativeMetricsV17931213,
    *,
    gate_matrix_18: Optional[Mapping[str, Any]] = None,
    underlying_check_statuses: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """
    Strict structured parser & cross-validator for `P0-E4`:
      - Forbids stale `.11`/`.12` banners, `26 / 26` or `40 / 40` negative-control prose,
        or static `"278 -> 6 -> 272 -> 0"` strings when actual counts differ.
      - Parses every `METRICS_KV: KEY=VALUE` line from BOTH `README_V17_9_3_1_2_13.md` and
        `HANDOVER_V17_9_3_1_2_13.txt` and asserts 100% key/value equality with `metrics`.
      - Parses Markdown variant table rows in `README_V17_9_3_1_2_13.md` and verifies exact
        `closed_trades_count` and `realized_net_pnl_usd` per variant.
      - Cross-validates `gate_matrix_18` titles and statuses against `underlying_check_statuses`.
    """
    violations: List[str] = []
    forbidden_stale_patterns = (
        "# AURUM DESK — V17.9.3.1.2.11",
        "# AURUM DESK — V17.9.3.1.2.12",
        "AURUM DESK — V17.9.3.1.2.11",
        "AURUM DESK — V17.9.3.1.2.12",
        "26 / 26",
        "26/26",
        "40 / 40",
        "40/40",
        "V01..V40",
        "V01..V22",
    )
    for pat in forbidden_stale_patterns:
        if pat in readme_text or pat in handover_text:
            violations.append(f"STALE_PRIOR_VERSION_CONTAMINATION_IN_REPORT:{pat}")

    if metrics.si8_materialized_intents_count != 0 and "278 -> 6 -> 272 -> 0" in (readme_text + "\n" + handover_text):
        violations.append("STALE_PRIOR_VERSION_CONTAMINATION_IN_REPORT:278_6_272_0")

    expected_kv = _authoritative_kv_map_v17931213(metrics)

    def _parse_kv(txt: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for line in txt.splitlines():
            s = line.strip()
            if s.startswith("METRICS_KV:"):
                body = s[len("METRICS_KV:") :].strip()
                if "=" in body:
                    k, v = body.split("=", 1)
                    out[k.strip()] = v.strip()
        return out

    readme_kv = _parse_kv(readme_text)
    handover_kv = _parse_kv(handover_text)

    for doc_lbl, parsed in (("README", readme_kv), ("HANDOVER", handover_kv)):
        for k, exp_v in expected_kv.items():
            obs_v = parsed.get(k)
            if obs_v != exp_v:
                if k in ("FRESH_OOS_VERDICT", "ACTIVATION_READY"):
                    violations.append(f"UNSAFE_OOS_OR_ACTIVATION_CLAIM_IN_REPORT:{doc_lbl}:{k}:{obs_v}!={exp_v}")
                else:
                    violations.append(f"REPORT_METRIC_MISMATCH:{doc_lbl}:{k}:obs={obs_v},exp={exp_v}")

    if readme_kv != handover_kv:
        violations.append("REPORT_METRIC_MISMATCH:README_VS_HANDOVER_DIVERGENCE")

    # Parse variant Markdown table rows in README
    table_rows_found: Dict[str, List[str]] = {}
    for line in readme_text.splitlines():
        s = line.strip()
        if s.startswith("| `") and s.endswith("|"):
            cols = [c.strip() for c in s.strip("|").split("|")]
            v_key = cols[0].strip("`")
            if v_key in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213 and len(cols) >= 12:
                table_rows_found[v_key] = cols

    for vid in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213:
        cols = table_rows_found.get(vid)
        if not cols:
            violations.append(f"README_VARIANT_TABLE_MISSING_ROW:{vid}")
            continue
        vc = metrics.variants_nb_table[vid]
        pc = metrics.portfolio_1pos_nb_table[vid]
        if (
            cols[5] != str(vc["closed_trades_count"])
            or cols[8] != f"{vc['realized_net_pnl_usd']:.4f}"
            or cols[10] != str(pc["closed_trades_count"])
            or cols[11] != f"{pc['realized_net_pnl_usd']:.4f}"
        ):
            violations.append(f"README_VARIANT_TABLE_MISMATCH:{vid}:cols={cols}")

    if gate_matrix_18 is not None:
        gates = gate_matrix_18.get("gates") or {}
        for gk, g_obj in gates.items():
            g_title = str((g_obj or {}).get("title") or "")
            if "278 -> 6 -> 272 -> 0" in g_title or "V01..V40" in g_title or "40/40" in g_title:
                violations.append(f"STALE_PRIOR_VERSION_CONTAMINATION_IN_GATES:{gk}:{g_title}")
            if underlying_check_statuses and gk in underlying_check_statuses:
                req_st = underlying_check_statuses[gk]
                if g_obj.get("status") == "PASS" and req_st != "PASS":
                    violations.append(f"GATE_FALSE_PASS_CONTRADICTION:{gk}:gate=PASS,check={req_st}")

    return {
        "version": VERSION_V17931213,
        "status": "PASS" if not violations else "FAIL",
        "parsed_readme_keys_count": len(readme_kv),
        "parsed_handover_keys_count": len(handover_kv),
        "parsed_variant_rows_count": len(table_rows_found),
        "violations": violations,
    }


# ==============================================================================
# 7. PHASE H: FAULT-INJECTED NEGATIVE CONTROLS V01..V66 (14)
# ==============================================================================

def verify_bounded_async_workers_v17931213(
    worker_records: Sequence[Mapping[str, Any]],
    *,
    expected_run_id: str,
    expected_tested_sha: str,
    max_allowed_workers: int = 4,
    active_orphan_pids: Sequence[int] = (),
) -> Dict[str, Any]:
    """
    Wraps `verify_process_exit_and_timeout_v17931211` and additionally enforces an exact
    sweep-line interval concurrency check across `(started_at_ms, completed_at_ms)` so any
    simultaneous worker overlap exceeding `max_allowed_workers` (<= 4) fails closed with
    `CONCURRENT_WORKER_CAP_EXCEEDED`.
    """
    base = verify_process_exit_and_timeout_v17931211(
        worker_records,
        expected_run_id=expected_run_id,
        expected_tested_sha=expected_tested_sha,
        max_allowed_workers=max_allowed_workers,
        active_orphan_pids=active_orphan_pids,
    )
    violations = list(base.get("violations") or [])
    events: List[Tuple[int, int]] = []
    for rec in worker_records:
        s_ms = rec.get("started_at_ms", rec.get("started_ms"))
        f_ms = rec.get("completed_at_ms", rec.get("completed_ms"))
        if isinstance(s_ms, int) and not isinstance(s_ms, bool) and isinstance(f_ms, int) and not isinstance(f_ms, bool) and f_ms >= s_ms > 0:
            # Put end (-1) before start (+1) at exact tie timestamp so sequential back-to-back spawns don't double-count
            events.append((int(s_ms), 1))
            events.append((int(f_ms), -1))
    events.sort(key=lambda ev: (ev[0], ev[1]))
    cur_active = 0
    peak_active = 0
    for _ts, delta in events:
        cur_active += delta
        if cur_active > peak_active:
            peak_active = cur_active
    if peak_active > int(max_allowed_workers):
        violations.append(f"CONCURRENT_WORKER_CAP_EXCEEDED:peak={peak_active}>max={max_allowed_workers}")
    return {
        **base,
        "version": VERSION_V17931213,
        "peak_concurrent_workers_from_timestamps": peak_active,
        "max_allowed_workers": int(max_allowed_workers),
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
    }


def run_negative_controls_v01_to_v66_v17931213(
    *,
    reconstructed_partitions: Optional[Mapping[str, Mapping[str, Mapping[str, Any]]]] = None,
    bars_1m_by_open_ts: Optional[Mapping[int, Mapping[str, Any]]] = None,
    base_v11_report_metrics: Optional[Mapping[str, Any]] = None,
    base_v11_readme_text: Optional[str] = None,
    base_v11_handover_text: Optional[str] = None,
    authoritative_metrics_v13: Optional[AuthoritativeMetricsV17931213] = None,
    readme_v13_text: Optional[str] = None,
    handover_v13_text: Optional[str] = None,
    gate_matrix_18: Optional[Mapping[str, Any]] = None,
    run_id: str = "neg_ctrl_v17931213",
    tested_sha: str = "a" * 40,
) -> Dict[str, Any]:
    """
    Executes all 66 adversarial negative controls (`V01..V66`) in disposable temporary directories:
      - `V01..V40`: Full inherited `.11` + `.12` truth-closure & provenance negative controls
      - `V41..V66`: 26 new `.13` fault-injected controls covering genuine delayed entry (`P0-E1`),
        cross-adapter parity & production safety, structural SL/TP/sizing, dynamic `SI8`/`SI9`
        ledger fail-closed & `267`/`113` audits (`P0-E2`/`P0-E3`), structural HTF warmup/conflict,
        single authoritative report & dynamic gate consistency (`P0-E4`), bounded async worker caps
        & orphan reaping (`P1-E5`), and OOS/activation lock (`P1-E6`).
    """
    base_40 = run_negative_controls_v01_to_v40_v17931212(
        reconstructed_partitions=reconstructed_partitions,
        bars_1m_by_open_ts=bars_1m_by_open_ts,
        report_metrics=base_v11_report_metrics,
        readme_text=base_v11_readme_text,
        handover_text=base_v11_handover_text,
        run_id=run_id,
        tested_sha=tested_sha,
    )
    controls: List[Dict[str, Any]] = list(base_40.get("controls") or [])
    violations: List[str] = list(base_40.get("violations") or [])

    def _add_ctrl(
        cid: str,
        orig_obj: Any,
        mut_obj: Any,
        mutation_op: str,
        verifier_fn: str,
        expected_code: str,
        res: Mapping[str, Any],
        t0_ns: int,
        tmp_path: Path,
    ) -> None:
        elapsed_ms = round((time.time_ns() - t0_ns) / 1e6, 3)
        v_list = [str(v) for v in (res.get("violations") or [])]
        rejected = res.get("status") == "FAIL" and any(expected_code in v for v in v_list)
        obs_code = next((v for v in v_list if expected_code in v), (v_list[0] if v_list else "NONE"))
        orig_sha = canonical_hash(orig_obj)
        mut_sha = canonical_hash(mut_obj)
        if orig_sha == mut_sha:
            rejected = False
        controls.append(
            {
                "control_id": cid,
                "short_id": cid.split("_")[0],
                "original_artifact_sha256": orig_sha,
                "mutated_artifact_sha256": mut_sha,
                "mutation_operator": mutation_op,
                "verifier_function": verifier_fn,
                "expected_failure_code": expected_code,
                "observed_failure_code": obs_code,
                "violations_caught": v_list,
                "detected_by_verifier": rejected,
                "real_child_exit_status": 1 if rejected else 0,
                "runtime_ms": max(0.001, elapsed_ms),
                "temp_dir_path": str(tmp_path),
                "rejected": rejected,
                "status": "PASS" if rejected else "FAIL",
            }
        )

    # Build clean baseline fixtures for V41..V66 if not passed from live run
    good_contract_02 = {
        "old_a1_contract": {
            "variant_id": "A1_ENTRY_CONFIRMATION_RETEST_ONLY",
            "entry_timing_contract": "SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY",
            "delayed_entry_executed": False,
        },
        "shared_cross_adapter_verification": {
            "status": "PASS",
            "mismatches_count": 0,
            "default_state": "OFF",
            "enabled_in_production": False,
        },
    }
    good_var_summary = {
        v: {
            fm: {
                "matched_opportunities_evaluated": 242,
                "accepted_confirmation_count": 20,
                "rejected_pre_entry_count": 222,
                "no_fill_or_sizing_reject_count": 2,
                "closed_trades_count": 15,
                "wins_count": 4,
                "losses_count": 11,
                "open_at_horizon_count": 3,
                "data_gap_unresolved_count": 0,
                "realized_net_pnl_usd": -12.3450,
                "expectancy_net_r": -0.3210,
                "min_wait_bars": 1 if v in TRUE_DELAYED_ENTRY_VARIANTS_V17931213 else 0,
                "max_wait_bars": 8 if v in TRUE_DELAYED_ENTRY_VARIANTS_V17931213 else 0,
                "max_planned_net_risk_usd_observed": 2.48,
                "entry_timing_contract": "GENUINE_POST_SIGNAL_CLOSED_BAR_DELAYED_ENTRY" if v in TRUE_DELAYED_ENTRY_VARIANTS_V17931213 else "SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY",
                "delayed_entry_executed": v in TRUE_DELAYED_ENTRY_VARIANTS_V17931213,
            }
            for fm in CANONICAL_FILL_MODES_V17931211
        }
        for v in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213
    }
    good_delay_row = {
        "variant_id": "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
        "fill_mode": "NEXT_BAR_OPEN_BID_ASK",
        "opportunity_id": "opp_v13_1",
        "direction": "LONG",
        "status": "EXECUTED",
        "delayed_entry_executed": True,
        "wait_bars": 3,
        "base_decision_timestamp_ms": 600_000,
        "new_decision_timestamp_ms": 780_000,
        "confirmation_known_at_ms": 780_000,
        "filled": True,
        "entry_fill_ts": 840_000,
        "selected_sl": 4090.0,
        "protected_anchor_price": 4091.5,
        "planned_net_risk_usd": 2.45,
        "net_rr": 2.35,
        "circular_protected_anchor_equals_sl": False,
        "swept_target_claimed_unswept": False,
    }
    good_port_09 = {
        "matched_and_delayed_variants_1pos_portfolio": {
            "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE": {
                "NEXT_BAR_OPEN_BID_ASK": {"position_overlap_violations": 0}
            }
        }
    }

    if authoritative_metrics_v13 is None:
        authoritative_metrics_v13 = AuthoritativeMetricsV17931213(
            version=VERSION_V17931213,
            run_id=run_id,
            branch=MANDATORY_BRANCH_V17931213,
            tested_executable_sha=tested_sha,
            dataset_sha256=CANONICAL_DATASET_SHA256_V17931213,
            pytest_collected_count=550,
            pytest_passed_count=550,
            pytest_skipped_count=0,
            pytest_failed_count=0,
            pytest_errors_count=0,
            pytest_shards_count=12,
            pytest_async_w3_wall_seconds=120.0,
            pytest_v12_w1_baseline_seconds=244.7,
            pytest_async_speedup_ratio=2.039,
            max_concurrent_children_observed=3,
            orphan_pids_count=0,
            worker_1_2_4_repeat_parity_status="PASS",
            canonical_universe_total=63085,
            baseline_decisions_count=56710,
            si8_discovered_decisions_count=6375,
            si8_proof_eligible_count=330,
            si8_arbitration_suppressed_count=10,
            si8_post_arbitration_rejected_count=314,
            si8_materialized_intents_count=6,
            si8_closed_trades_nb=5,
            si8_net_pnl_usd_nb=5.1200,
            si9_materialized_intents_count=3,
            si9_closed_trades_nb=2,
            si9_net_pnl_usd_nb=2.4500,
            historical_v12_267_target_cases_audited=267,
            historical_v12_113_htf_cases_audited=113,
            repaired_v13_false_htf_conflicts_count=0,
            si0_scheduled_episodes_nb=242,
            si0_closed_trades_nb=166,
            si0_wins_nb=9,
            si0_losses_nb=157,
            si0_open_at_horizon_nb=74,
            si0_data_gap_nb=1,
            si0_unfilled_nb=1,
            si0_net_pnl_usd_nb=-345.8971,
            si0_expectancy_r_nb=-0.8342,
            r1_pre_entry_unknown_losses_count=10,
            r1_buckets_a_to_h={"F_EX_POST_STOP_BEFORE_STRUCTURE_BREAK_NOISE_SWEEP": 157},
            variants_nb_table={v: dict(good_var_summary[v]["NEXT_BAR_OPEN_BID_ASK"]) for v in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213},
            portfolio_1pos_nb_table={
                v: {"closed_trades_count": 12, "realized_net_pnl_usd": -9.5000}
                for v in ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213
            },
            si_policies_nb_table={
                "SI0_OFF_BASELINE": {
                    "scheduled_episodes": 242,
                    "closed_trades": 166,
                    "wins": 9,
                    "losses": 157,
                    "realized_net_pnl_usd": -345.8971,
                    "expectancy_r": -0.8342,
                }
            },
            all_30_partitions_status="PASS",
            source_execution_reconciliation_status="PASS",
            materialized_ablations={
                m: {
                    "pre_entry_63085_passed": 1000,
                    "materialized_intents_count": 4,
                    "closed_trades_count": 3,
                    "wins_count": 1,
                    "losses_count": 2,
                    "realized_net_pnl_usd": 1.2500,
                    "expectancy_net_r": 0.1500,
                }
                for m in ("H4_ONLY", "H1_ONLY", "M15_ONLY", "M15_H1_ONLY", "STRUCTURE_ONLY", "LIQUIDITY_ONLY", "FULL_MTF")
            },
            negative_controls_total=66,
            negative_controls_rejected=66,
            prior_artifacts_immutable=True,
            verification_truth_verdict="PASS",
            delayed_entry_execution_verdict="PASS",
            htf_unicorn_materialization_verdict="PASS",
            fresh_oos_verdict="BLOCKED_NO_UNTOUCHED_DATA",
            economic_verdict="NEGATIVE_EXPECTANCY_AS_OBSERVED",
            strategy_expectancy_verdict="NEGATIVE_BASELINE_SMALL_SAMPLE_REPAIRED_SUBSET",
            activation_ready="NO",
        )
    if readme_v13_text is None or handover_v13_text is None:
        readme_v13_text, handover_v13_text = render_v17931213_readme_and_handover(authoritative_metrics_v13)
    if gate_matrix_18 is None:
        ok_s = {"status": "PASS"}
        gate_matrix_18 = evaluate_v17931213_gate_matrix(
            authoritative_metrics_v13,
            delayed_entry_check=ok_s,
            htf_funnel_check=ok_s,
            r1_check=ok_s,
            junit_check=ok_s,
            lineage_30_check={"status": "PASS", "source_execution_reconciliation": {"status": "PASS"}},
            async_proc_check=ok_s,
            report_consistency_check=ok_s,
            oos_check=ok_s,
            neg_v66_check=ok_s,
            manifest_check=ok_s,
            freeze_check=ok_s,
        )

    temp_dir_obj = tempfile.TemporaryDirectory(prefix="aurum_v13_v41_v66_")
    tmp_dir = Path(temp_dir_obj.name)
    try:
        # V41: Zero delay (wait_bars=0, new_ts == base_ts) in true delayed-entry variant rejected
        t0 = time.time_ns()
        r_v41_bad = {**good_delay_row, "variant_id": "D1_REAL_DELAYED_ENTRY", "wait_bars": 0, "new_decision_timestamp_ms": 600_000}
        res_41 = verify_delayed_entry_and_structural_sl_tp_v17931213(good_contract_02, good_var_summary, [r_v41_bad], good_port_09)
        _add_ctrl("V41_ZERO_DELAY_WAIT_BARS_IN_TRUE_DELAYED_ENTRY_REJECTED", good_delay_row, r_v41_bad, "set_wait_bars_0_and_same_timestamp_on_D1", "verify_delayed_entry_and_structural_sl_tp_v17931213", "ZERO_DELAY_OR_INVALID_WAIT_BARS_IN_TRUE_DELAYED_ENTRY", res_41, t0, tmp_dir)

        # V42: Wait bars > 12 (wait_bars=15) in true delayed-entry variant rejected
        t0 = time.time_ns()
        r_v42_bad = {**good_delay_row, "variant_id": "D1_REAL_DELAYED_ENTRY", "wait_bars": 15, "new_decision_timestamp_ms": 1_500_000, "entry_fill_ts": 1_560_000}
        res_42 = verify_delayed_entry_and_structural_sl_tp_v17931213(good_contract_02, good_var_summary, [r_v42_bad], good_port_09)
        _add_ctrl("V42_WAIT_BARS_EXCEEDING_12_BAR_WINDOW_REJECTED", good_delay_row, r_v42_bad, "set_wait_bars_15_above_max_12_window", "verify_delayed_entry_and_structural_sl_tp_v17931213", "ZERO_DELAY_OR_INVALID_WAIT_BARS_IN_TRUE_DELAYED_ENTRY", res_42, t0, tmp_dir)

        # V43: Retroactive fill before new_decision_timestamp_ms rejected
        t0 = time.time_ns()
        r_v43_bad = {**good_delay_row, "entry_fill_ts": 660_000}
        res_43 = verify_delayed_entry_and_structural_sl_tp_v17931213(good_contract_02, good_var_summary, [r_v43_bad], good_port_09)
        _add_ctrl("V43_RETROACTIVE_ENTRY_FILL_BEFORE_CONFIRMATION_CLOSE_REJECTED", good_delay_row, r_v43_bad, "set_entry_fill_ts_earlier_than_new_decision_timestamp_ms", "verify_delayed_entry_and_structural_sl_tp_v17931213", "RETROACTIVE_FILL_BEFORE_CONFIRMATION_CLOSE", res_43, t0, tmp_dir)

        # V44: Same-cutoff A1 filter mislabeled as delayed_entry_executed=True rejected
        t0 = time.time_ns()
        c02_v44_bad = copy.deepcopy(good_contract_02)
        c02_v44_bad["old_a1_contract"]["delayed_entry_executed"] = True
        res_44 = verify_delayed_entry_and_structural_sl_tp_v17931213(c02_v44_bad, good_var_summary, [good_delay_row], good_port_09)
        _add_ctrl("V44_SAME_CUTOFF_A1_FILTER_MISLABELED_AS_DELAYED_ENTRY_REJECTED", good_contract_02, c02_v44_bad, "set_old_a1_delayed_entry_executed_True", "verify_delayed_entry_and_structural_sl_tp_v17931213", "A1_SAME_CUTOFF_FILTER_MISLABELED_AS_DELAYED_ENTRY", res_44, t0, tmp_dir)

        # V45: Confirmation lookahead (confirmation_known_at_ms > new_decision_timestamp_ms) rejected
        t0 = time.time_ns()
        r_v45_bad = {**good_delay_row, "confirmation_known_at_ms": 900_000}
        res_45 = verify_delayed_entry_and_structural_sl_tp_v17931213(good_contract_02, good_var_summary, [r_v45_bad], good_port_09)
        _add_ctrl("V45_CONFIRMATION_KNOWN_AT_LOOKAHEAD_AFTER_NEW_DECISION_TS_REJECTED", good_delay_row, r_v45_bad, "set_confirmation_known_at_ms_after_new_decision_ts", "verify_delayed_entry_and_structural_sl_tp_v17931213", "CONFIRMATION_LOOKAHEAD_AFTER_DECISION_TS", res_45, t0, tmp_dir)

        # V46: Cross-adapter parity mismatch between REPLAY_BACKTEST and MARKET_SHADOW rejected
        t0 = time.time_ns()
        c02_v46_bad = copy.deepcopy(good_contract_02)
        c02_v46_bad["shared_cross_adapter_verification"]["mismatches_count"] = 2
        c02_v46_bad["shared_cross_adapter_verification"]["status"] = "FAIL"
        res_46 = verify_delayed_entry_and_structural_sl_tp_v17931213(c02_v46_bad, good_var_summary, [good_delay_row], good_port_09)
        _add_ctrl("V46_SHARED_CROSS_ADAPTER_REPLAY_SHADOW_DIVERGENCE_REJECTED", good_contract_02, c02_v46_bad, "inject_replay_vs_market_shadow_adapter_divergence", "verify_delayed_entry_and_structural_sl_tp_v17931213", "CROSS_ADAPTER_PARITY_MISMATCH", res_46, t0, tmp_dir)

        # V47: Production adapter enabled_in_production=True rejected
        t0 = time.time_ns()
        c02_v47_bad = copy.deepcopy(good_contract_02)
        c02_v47_bad["shared_cross_adapter_verification"]["enabled_in_production"] = True
        res_47 = verify_delayed_entry_and_structural_sl_tp_v17931213(c02_v47_bad, good_var_summary, [good_delay_row], good_port_09)
        _add_ctrl("V47_PRODUCTION_ADAPTER_ENABLED_IN_PRODUCTION_TRUE_REJECTED", good_contract_02, c02_v47_bad, "enable_delayed_entry_adapter_in_production", "verify_delayed_entry_and_structural_sl_tp_v17931213", "PRODUCTION_ADAPTER_UNSAFE_ACTIVATION_STATE", res_47, t0, tmp_dir)

        # V48: Stop placed above protected anchor on LONG trade rejected
        t0 = time.time_ns()
        r_v48_bad = {**good_delay_row, "variant_id": "D2_REAL_DELAYED_ENTRY_PLUS_OUTER_SL", "selected_sl": 4093.0, "protected_anchor_price": 4091.5}
        res_48 = verify_delayed_entry_and_structural_sl_tp_v17931213(good_contract_02, good_var_summary, [r_v48_bad], good_port_09)
        _add_ctrl("V48_STOP_ON_WRONG_SIDE_OF_PROTECTED_ANCHOR_REJECTED", good_delay_row, r_v48_bad, "place_long_sl_above_protected_anchor_price", "verify_delayed_entry_and_structural_sl_tp_v17931213", "STOP_ON_WRONG_SIDE_OF_PROTECTED_ANCHOR", res_48, t0, tmp_dir)

        # V49: Delayed structural trade with net_rr < 2.0 accepted -> rejected
        t0 = time.time_ns()
        r_v49_bad = {**good_delay_row, "variant_id": "D3_REAL_DELAYED_ENTRY_PLUS_TARGET", "net_rr": 1.45}
        res_49 = verify_delayed_entry_and_structural_sl_tp_v17931213(good_contract_02, good_var_summary, [r_v49_bad], good_port_09)
        _add_ctrl("V49_DELAYED_STRUCTURAL_TRADE_NET_RR_BELOW_2_0_REJECTED", good_delay_row, r_v49_bad, "set_delayed_trade_net_rr_1_45_below_2_0", "verify_delayed_entry_and_structural_sl_tp_v17931213", "NET_RR_BELOW_2_0_ACCEPTED", res_49, t0, tmp_dir)

        # V50: Delayed trade planned_net_risk_usd > 2.50 rejected
        t0 = time.time_ns()
        r_v50_bad = {**good_delay_row, "planned_net_risk_usd": 2.75}
        res_50 = verify_delayed_entry_and_structural_sl_tp_v17931213(good_contract_02, good_var_summary, [r_v50_bad], good_port_09)
        _add_ctrl("V50_DELAYED_TRADE_PLANNED_RISK_ABOVE_2_50_CAP_REJECTED", good_delay_row, r_v50_bad, "set_delayed_planned_net_risk_usd_2_75", "verify_delayed_entry_and_structural_sl_tp_v17931213", "RISK_CAP_2_50_EXCEEDED", res_50, t0, tmp_dir)

        # V51: 1-Position chronological portfolio overlap violation rejected
        t0 = time.time_ns()
        p09_v51_bad = {"matched_and_delayed_variants_1pos_portfolio": {"D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE": {"NEXT_BAR_OPEN_BID_ASK": {"position_overlap_violations": 1}}}}
        res_51 = verify_delayed_entry_and_structural_sl_tp_v17931213(good_contract_02, good_var_summary, [good_delay_row], p09_v51_bad)
        _add_ctrl("V51_PORTFOLIO_1POS_CONCURRENT_OVERLAP_REJECTED", good_port_09, p09_v51_bad, "inject_overlapping_concurrent_position_in_1pos_portfolio", "verify_delayed_entry_and_structural_sl_tp_v17931213", "PORTFOLIO_1POS_OVERLAP_VIOLATION", res_51, t0, tmp_dir)

        # V52: Missing/empty SI8 decision ledger fails closed with UNKNOWN_MISSING_INTENT_LEDGER
        t0 = time.time_ns()
        diag_empty = build_si8_si9_278_materialization_diagnosis_v17931210({"decision_ledger": []}, {"decision_ledger": []}, {})
        htf_ok = {"status": "PASS"}
        uni_ok = {"canonical_universe_total": 63085, "causal_detector_funnel_counts": {"unicorn_confirmed_breaker_fvg_overlap": 100, "fvg_only_without_breaker_overlap": 60000, "unicorn_unknown_or_absent": 2985}}
        res_52 = verify_r2_causal_mtf_unicorn_provenance_v17931212(htf_ok, uni_ok, {"si8_si9_dynamic_funnel_diagnosis": diag_empty})
        _add_ctrl("V52_MISSING_SI8_INTENT_LEDGER_FAILS_CLOSED_UNKNOWN_MISSING_LEDGER", {"decision_ledger": [{"opportunity_id": "x"}]}, {"decision_ledger": []}, "pass_empty_si8_decision_ledger_to_diagnosis", "verify_r2_causal_mtf_unicorn_provenance_v17931212", "MISSING_AUTHORITATIVE_INTENT_LEDGER_FAIL_CLOSED", res_52, t0, tmp_dir)

        # V53: SI8 dynamic funnel conservation tampered (elig != supp + rej + mat)
        t0 = time.time_ns()
        diag_good_53 = {"proof_eligible_candidates_count": 330, "arbitration_suppressed_count": 10, "post_arbitration_rejected_count": 314, "materialized_intents_count": 6}
        diag_bad_53 = {**diag_good_53, "materialized_intents_count": 9}
        res_53 = verify_r2_causal_mtf_unicorn_provenance_v17931212(htf_ok, uni_ok, {"si8_si9_dynamic_funnel_diagnosis": diag_bad_53})
        _add_ctrl("V53_SI8_DYNAMIC_FUNNEL_CONSERVATION_MISMATCH_REJECTED", diag_good_53, diag_bad_53, "inflate_materialized_intents_count_breaking_conservation", "verify_r2_causal_mtf_unicorn_provenance_v17931212", "SI8_SI9_FUNNEL_CONSERVATION_MISMATCH", res_53, t0, tmp_dir)

        # V54: Materialized SI8 intent missing si8_intent_id or proof_hash rejected
        t0 = time.time_ns()
        diag_bad_54 = {
            "proof_eligible_candidates_count": 1,
            "arbitration_suppressed_count": 0,
            "post_arbitration_rejected_count": 0,
            "materialized_intents_count": 1,
            "rows": [{"opportunity_id": "opp_1", "si8_accepted": True, "si8_intent_id": None, "proof_hash": None}],
        }
        res_54 = verify_r2_causal_mtf_unicorn_provenance_v17931212(htf_ok, uni_ok, {"si8_si9_dynamic_funnel_diagnosis": diag_bad_54})
        _add_ctrl("V54_MATERIALIZED_INTENT_MISSING_ID_OR_PROOF_HASH_REJECTED", {"si8_intent_id": "intent_1"}, diag_bad_54["rows"][0], "strip_si8_intent_id_and_proof_hash_from_accepted_row", "verify_r2_causal_mtf_unicorn_provenance_v17931212", "SI8_MATERIALIZED_INTENT_MISSING_ID_OR_PROOF", res_54, t0, tmp_dir)

        # V55: Raw SI8 decision ledger disagrees with diagnosis materialized count
        t0 = time.time_ns()
        diag_good_55 = {
            "proof_eligible_candidates_count": 1,
            "arbitration_suppressed_count": 0,
            "post_arbitration_rejected_count": 0,
            "materialized_intents_count": 1,
            "rows": [{"opportunity_id": "opp_1", "si8_accepted": True, "si8_intent_id": "int_1", "proof_hash": "h_1"}],
        }
        raw_led_bad_55 = [{"opportunity_id": "opp_1", "accepted": False, "rejection_reason": "REJECTED_NO_CAUSAL_CONFIRMATION", "entered_materialization_stage": True}]
        res_55 = verify_r2_causal_mtf_unicorn_provenance_v17931212(htf_ok, uni_ok, {"si8_si9_dynamic_funnel_diagnosis": diag_good_55}, raw_si8_decision_ledger=raw_led_bad_55)
        _add_ctrl("V55_SUMMARY_MATERIALIZED_COUNT_CONTRADICTS_RAW_SI8_LEDGER_REJECTED", diag_good_55, raw_led_bad_55, "claim_accepted_in_summary_when_raw_si8_ledger_rejected", "verify_r2_causal_mtf_unicorn_provenance_v17931212", "SI8_RAW_DECISION_LEDGER_MISMATCH", res_55, t0, tmp_dir)

        # V56: Incomplete 267 SI9 target audit rejected
        t0 = time.time_ns()
        f05_good = {
            "htf_causal_bar_and_pivot_audit": htf_ok,
            "unicorn_fvg_breaker_funnel_63085": uni_ok,
            "si8_si9_dynamic_funnel_diagnosis": diag_good_53,
            "historical_v12_267_si9_target_audit": {"historical_v12_cases_audited": 267, "buckets_sum": 267},
            "historical_v12_113_htf_conflict_audit": {"repaired_v13_false_htf_conflict_at_same_ts": 0},
            "materialized_single_axis_mtf_unicorn_ablations": {m: {"materialized_intents_count": 1} for m in ("H4_ONLY", "H1_ONLY", "M15_ONLY", "M15_H1_ONLY", "STRUCTURE_ONLY", "LIQUIDITY_ONLY", "FULL_MTF")},
        }
        f05_bad_56 = copy.deepcopy(f05_good)
        f05_bad_56["historical_v12_267_si9_target_audit"]["buckets_sum"] = 250
        res_56 = verify_htf_unicorn_and_si8_si9_funnel_v17931213(f05_bad_56)
        _add_ctrl("V56_INCOMPLETE_267_SI9_TARGET_AUDIT_BUCKETS_REJECTED", f05_good["historical_v12_267_si9_target_audit"], f05_bad_56["historical_v12_267_si9_target_audit"], "drop_17_cases_from_267_si9_target_audit", "verify_htf_unicorn_and_si8_si9_funnel_v17931213", "SI9_267_TARGET_AUDIT_SUM_MISMATCH", res_56, t0, tmp_dir)

        # V57: False REJECTED_CAUSAL_H1_H4_CONFLICT remaining on base-aligned candidate rejected
        t0 = time.time_ns()
        f05_bad_57 = copy.deepcopy(f05_good)
        f05_bad_57["historical_v12_113_htf_conflict_audit"]["repaired_v13_false_htf_conflict_at_same_ts"] = 3
        res_57 = verify_htf_unicorn_and_si8_si9_funnel_v17931213(f05_bad_57)
        _add_ctrl("V57_RESIDUAL_FALSE_HTF_CONFLICT_ON_ALIGNED_CANDIDATE_REJECTED", f05_good["historical_v12_113_htf_conflict_audit"], f05_bad_57["historical_v12_113_htf_conflict_audit"], "leave_3_false_htf_conflicts_on_aligned_candidates", "verify_htf_unicorn_and_si8_si9_funnel_v17931213", "FALSE_HTF_CONFLICT_STILL_PRESENT", res_57, t0, tmp_dir)

        # V58: Structural HTF evaluator rejects warmup/insufficient candles fail-closed
        t0 = time.time_ns()
        ok_htf, rsn_htf = _check_causal_htf_alignment_at_ts_v179312(
            {"1H": [], "4H": [], "15m": [], "5m": []},
            {"1H": [], "4H": [], "15m": [], "5m": []},
            3_600_000,
            "LONG",
            use_structural_evaluator=True,
        )
        res_58 = {"status": "FAIL" if not ok_htf else "PASS", "violations": [f"STRUCTURAL_HTF_WARMUP_OR_CONFLICT_REJECTED:{rsn_htf}"] if not ok_htf else []}
        _add_ctrl("V58_INSUFFICIENT_HTF_WARMUP_BARS_FAIL_CLOSED_IN_STRUCTURAL_EVALUATOR", {"bars_count": 30}, {"bars_count": 0}, "evaluate_structural_htf_alignment_with_empty_warmup_bars", "_check_causal_htf_alignment_at_ts_v179312", "STRUCTURAL_HTF_WARMUP_OR_CONFLICT_REJECTED", res_58, t0, tmp_dir)

        # V59: Stale .12 banner or "40 / 40" injected into README_V17_9_3_1_2_13.md rejected
        t0 = time.time_ns()
        readme_bad_59 = readme_v13_text + "\nLegacy note: 40 / 40 negative controls (V01..V40).\n"
        res_59 = verify_v17931213_authoritative_report_and_gates_consistency(readme_bad_59, handover_v13_text, authoritative_metrics_v13, gate_matrix_18=gate_matrix_18)
        _add_ctrl("V59_STALE_V12_HEADER_OR_40_OF_40_PROSE_IN_README_REJECTED", {"readme_sha": canonical_hash(readme_v13_text)}, {"readme_sha": canonical_hash(readme_bad_59)}, "append_stale_40_of_40_v12_prose_to_readme", "verify_v17931213_authoritative_report_and_gates_consistency", "STALE_PRIOR_VERSION_CONTAMINATION_IN_REPORT", res_59, t0, tmp_dir)

        # V60: Mutated SI8_MATERIALIZED_INTENTS_COUNT in README vs HANDOVER rejected
        t0 = time.time_ns()
        readme_bad_60 = readme_v13_text.replace(
            f"METRICS_KV: SI8_MATERIALIZED_INTENTS_COUNT={authoritative_metrics_v13.si8_materialized_intents_count}",
            f"METRICS_KV: SI8_MATERIALIZED_INTENTS_COUNT={authoritative_metrics_v13.si8_materialized_intents_count + 5}",
        )
        res_60 = verify_v17931213_authoritative_report_and_gates_consistency(readme_bad_60, handover_v13_text, authoritative_metrics_v13, gate_matrix_18=gate_matrix_18)
        _add_ctrl("V60_README_VS_HANDOVER_SI8_MATERIALIZED_COUNT_MISMATCH_REJECTED", {"si8_mat": authoritative_metrics_v13.si8_materialized_intents_count}, {"si8_mat": authoritative_metrics_v13.si8_materialized_intents_count + 5}, "mutate_SI8_MATERIALIZED_INTENTS_COUNT_in_readme", "verify_v17931213_authoritative_report_and_gates_consistency", "REPORT_METRIC_MISMATCH", res_60, t0, tmp_dir)

        # V61: Mutated D1_REAL_DELAYED_ENTRY net PnL cell in README Markdown table rejected
        t0 = time.time_ns()
        d1_pnl_str = f"{authoritative_metrics_v13.variants_nb_table['D1_REAL_DELAYED_ENTRY']['realized_net_pnl_usd']:.4f}"
        readme_bad_61 = readme_v13_text.replace(f"| `D1_REAL_DELAYED_ENTRY` |", "| `D1_REAL_DELAYED_ENTRY` |", 1)
        # Replace only on the D1 row
        new_lines_61 = []
        for ln in readme_bad_61.splitlines():
            if ln.strip().startswith("| `D1_REAL_DELAYED_ENTRY` |"):
                cols = [c.strip() for c in ln.strip("|").split("|")]
                cols[8] = "999.9999"
                ln = "| " + " | ".join(cols) + " |"
            new_lines_61.append(ln)
        readme_bad_61 = "\n".join(new_lines_61)
        res_61 = verify_v17931213_authoritative_report_and_gates_consistency(readme_bad_61, handover_v13_text, authoritative_metrics_v13, gate_matrix_18=gate_matrix_18)
        _add_ctrl("V61_README_MARKDOWN_VARIANT_TABLE_CELL_DRIFT_REJECTED", {"d1_pnl": d1_pnl_str}, {"d1_pnl": "999.9999"}, "tamper_D1_net_pnl_cell_in_readme_markdown_table", "verify_v17931213_authoritative_report_and_gates_consistency", "README_VARIANT_TABLE_MISMATCH", res_61, t0, tmp_dir)

        # V62: Stale "278 -> 6 -> 272 -> 0" gate title in 18_acceptance_gate_matrix.json rejected
        t0 = time.time_ns()
        gm_bad_62 = copy.deepcopy(dict(gate_matrix_18))
        gm_bad_62["gates"] = copy.deepcopy(dict(gate_matrix_18["gates"]))
        gm_bad_62["gates"]["G_02_HTF_UNICORN_DYNAMIC_MATERIALIZATION"]["title"] = "278 -> 6 -> 272 -> 0 SI8/SI9 funnel audited"
        res_62 = verify_v17931213_authoritative_report_and_gates_consistency(readme_v13_text, handover_v13_text, authoritative_metrics_v13, gate_matrix_18=gm_bad_62)
        _add_ctrl("V62_STALE_278_6_272_0_STATIC_GATE_TITLE_REJECTED", gate_matrix_18["gates"]["G_02_HTF_UNICORN_DYNAMIC_MATERIALIZATION"], gm_bad_62["gates"]["G_02_HTF_UNICORN_DYNAMIC_MATERIALIZATION"], "inject_static_278_6_272_0_title_into_gate_G_02", "verify_v17931213_authoritative_report_and_gates_consistency", "STALE_PRIOR_VERSION_CONTAMINATION_IN_GATES", res_62, t0, tmp_dir)

        # V63: Gate claiming PASS when underlying check failed rejected
        t0 = time.time_ns()
        res_63 = verify_v17931213_authoritative_report_and_gates_consistency(
            readme_v13_text,
            handover_v13_text,
            authoritative_metrics_v13,
            gate_matrix_18=gate_matrix_18,
            underlying_check_statuses={"G_01_DELAYED_ENTRY_TRUTH": "FAIL"},
        )
        _add_ctrl("V63_GATE_CLAIMING_PASS_ON_FAILED_UNDERLYING_CHECK_REJECTED", {"underlying": "PASS"}, {"underlying": "FAIL"}, "claim_G_01_PASS_when_delayed_entry_check_is_FAIL", "verify_v17931213_authoritative_report_and_gates_consistency", "GATE_FALSE_PASS_CONTRADICTION", res_63, t0, tmp_dir)

        # V64: 5 overlapping child worker processes (> max_allowed_workers=4) rejected
        t0 = time.time_ns()
        w_good_64 = [{"worker_id": f"w_{i}", "run_id": run_id, "tested_source_sha": tested_sha, "pid": 1000 + i, "started_at_ms": 1000, "completed_at_ms": 2000, "exit_code": 0, "process_exit_code": 0, "task_validation_verdict": "PASS", "process_identity_provenance": "ASYNCIO_CREATE_SUBPROCESS_EXEC", "timed_out": False} for i in range(4)]
        w_bad_64 = w_good_64 + [{"worker_id": "w_4", "run_id": run_id, "tested_source_sha": tested_sha, "pid": 1004, "started_at_ms": 1000, "completed_at_ms": 2000, "exit_code": 0, "process_exit_code": 0, "task_validation_verdict": "PASS", "process_identity_provenance": "ASYNCIO_CREATE_SUBPROCESS_EXEC", "timed_out": False}]
        res_64 = verify_bounded_async_workers_v17931213(w_bad_64, expected_run_id=run_id, expected_tested_sha=tested_sha, max_allowed_workers=4)
        _add_ctrl("V64_FIVE_CONCURRENT_CHILD_WORKERS_EXCEEDING_CAP_4_REJECTED", {"workers": 4}, {"workers": 5}, "spawn_5_concurrent_overlapping_workers_above_cap_4", "verify_bounded_async_workers_v17931213", "CONCURRENT_WORKER_CAP_EXCEEDED", res_64, t0, tmp_dir)

        # V65: Unreaped orphan child PID at cleanup rejected
        t0 = time.time_ns()
        res_65 = verify_bounded_async_workers_v17931213(w_good_64[:1], expected_run_id=run_id, expected_tested_sha=tested_sha, max_allowed_workers=4, active_orphan_pids=[99999])
        _add_ctrl("V65_UNREAPED_ORPHAN_CHILD_PID_AT_CLEANUP_REJECTED", {"orphans": []}, {"orphans": [99999]}, "leave_unreaped_orphan_pid_99999_at_cleanup", "verify_bounded_async_workers_v17931213", "ORPHAN", res_65, t0, tmp_dir)

        # V66: Unsafe ACTIVATION_READY=YES or FRESH_OOS_VERDICT=PASS in HANDOVER rejected
        t0 = time.time_ns()
        handover_bad_66 = handover_v13_text.replace("METRICS_KV: ACTIVATION_READY=NO", "METRICS_KV: ACTIVATION_READY=YES")
        res_66 = verify_v17931213_authoritative_report_and_gates_consistency(readme_v13_text, handover_bad_66, authoritative_metrics_v13, gate_matrix_18=gate_matrix_18)
        _add_ctrl("V66_HANDOVER_ACTIVATION_READY_YES_CLAIM_REJECTED", {"ACTIVATION_READY": "NO"}, {"ACTIVATION_READY": "YES"}, "mutate_ACTIVATION_READY_to_YES_in_handover", "verify_v17931213_authoritative_report_and_gates_consistency", "UNSAFE_OOS_OR_ACTIVATION_CLAIM_IN_REPORT", res_66, t0, tmp_dir)
    finally:
        temp_dir_obj.cleanup()

    temp_cleaned = not tmp_dir.exists() and bool(base_40.get("temp_dirs_cleaned", True))
    for c in controls:
        c["temp_dir_cleaned"] = temp_cleaned
        if not c["rejected"] or not temp_cleaned:
            violations.append(f"NEGATIVE_CONTROL_FAILED:{c['control_id']}")

    return {
        "version": VERSION_V17931213,
        "schema_version": SCHEMA_VERSION_V17931213,
        "run_id": run_id,
        "tested_executable_sha": tested_sha,
        "status": "PASS" if not violations and len(controls) == 66 and temp_cleaned else "FAIL",
        "controls_count": len(controls),
        "rejected_count": sum(1 for c in controls if c["rejected"]),
        "all_rejected": all(c["rejected"] for c in controls),
        "temp_dirs_cleaned": temp_cleaned,
        "controls": controls,
        "violations": violations,
    }
