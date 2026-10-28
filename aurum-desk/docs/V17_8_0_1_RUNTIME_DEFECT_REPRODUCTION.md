# AURUM DESK V17.8.0.1 — RUNTIME DEFECT REPRODUCTION & AUDIT REGISTER

**Repository:** `quocan050302/Build-app-crypto`  
**Application:** `aurum-desk/`  
**Branch:** `feat/v17-8-unicorn-structural-sl-tp-intelligence`  
**Version:** V17.8.0.1  

---

## 1. Executive Summary

Following the V17.8.0 release (`11b19cffa15048dc0cc475fe8916fb18c64a20d0`), an independent source audit identified nine runtime integration, causal provenance, frontend mounting, and forensic documentation defects (`DEF-178-07` through `DEF-178-15`) that escaped the initial 42-gate acceptance suite.

Every finding was reproduced at the source level, repaired with fail-closed semantics, and covered by deterministic runtime integration tests (`backend/tests/test_v17_8_runtime_integration.py`), component tests (`frontend/src/__tests__/v17_8_0_1_sltp_intelligence.test.tsx`), and mounted Playwright browser E2E tests (`frontend/tests/e2e/v17_8_0_1_sltp_intelligence.e2e.ts`).

---

## 2. Reproduced Defect Matrix (`DEF-178-07` to `DEF-178-15`)

| Defect ID | Severity | Title | Root Cause Reproduced | Repair File(s) | Verification Test(s) | Status |
|---|---|---|---|---|---|---|
| `DEF-178-07` | P0 | Unicorn Detector Interface Mismatch | `scalp_m1_model_service.py` passed `selected_fvg=selected_fvg_desc` to `detect_unicorn_evidence`, which only accepted `candidate_fvgs`, raising `TypeError: unexpected keyword argument 'selected_fvg'` when `v18_unicorn_detection_enabled=True`. | `backend/services/unicorn_evidence_service.py`, `backend/services/scalp_m1_model_service.py` | `test_def_178_07_unicorn_detector_canonical_selected_fvg_contract`, `test_def_178_07_negative_control_interface_breakage_caught` | **RESOLVED & VERIFIED** |
| `DEF-178-08` | P0 | V17.8 Feature Flags Not Persisted | `StopPlacementPolicy` defined `v18_*` flags in memory, but `resolve_stop_placement_policy` and `update_stop_placement_policy` did not read, validate, or persist them in `system_config`, nor expose them in `schemas.py` / `main.py`. | `backend/services/stop_placement_policy_service.py`, `backend/schemas.py`, `backend/main.py` | `test_def_178_08_feature_flags_full_lifecycle_and_api`, `test_def_178_08_paper_structural_without_v18_flag_uses_v17_7_anchor` | **RESOLVED & VERIFIED** |
| `DEF-178-09` | P0 | Fill-Time Protective Distance Guard Not Wired | `ExecutionCoordinator.evaluate_orders_sync` built `EntryGuardRequest(stage="FILL")` without passing `min_protective_distance` or `unicorn_evidence` from `order.cost_snapshot`, and `entry_guard_service.py` lacked fail-closed validation for missing/invalid `min_protective_distance`. | `backend/services/scalp_watcher_orchestrator.py`, `backend/services/order_plan_snapshot_service.py`, `backend/services/entry_guard_service.py`, `backend/services/execution_coordinator.py` | `test_def_178_09_fill_guard_protective_distance_and_breached_quote` | **RESOLVED & VERIFIED** |
| `DEF-178-10` | P1 | Unicorn Causal Episode Binding Incomplete | `detect_breaker_blocks` searched for pullback OBs before `broken_swing.timestamp_ms`, `detect_unicorn_evidence` could match unrelated FVGs or leave `invalidation_price` non-null when `is_valid=False`, and `stop_placement_service.py` dropped `selected_break` / `selected_fvg` when delegating to `resolve_stop_anchor_v17_7`. | `backend/services/unicorn_evidence_types.py`, `backend/services/unicorn_evidence_service.py`, `backend/services/stop_placement_service.py` | `test_def_178_10_causal_unicorn_provenance_and_negative_controls` | **RESOLVED & VERIFIED** |
| `DEF-178-11` | P1 | Missing End-to-End Runtime Integration Tests | V17.8 tests called unit helpers in isolation (`detect_unicorn_evidence`, `resolve_stop_anchor`) without exercising `ScalpM1ModelService` -> `ScalpWatcherOrchestrator` -> `ExecutionCoordinator` -> `TradeLifecycleService` -> `/api/v1/orders/{id}/stop-evidence`. | `backend/tests/test_v17_8_runtime_integration.py` | `test_def_178_11_scenarios_01_to_12_end_to_end_runtime` | **RESOLVED & VERIFIED** |
| `DEF-178-12` | P1 | `SLTPIntelligenceView.tsx` Unmounted in Production UI | `SLTPIntelligenceView.tsx` existed as a standalone component file but was never imported or mounted in `JournalTab.tsx`, had no component tests, and had no Playwright E2E coverage. | `frontend/src/components/SLTPIntelligenceView.tsx`, `frontend/src/JournalTab.tsx`, `frontend/src/__tests__/v17_8_0_1_sltp_intelligence.test.tsx`, `frontend/tests/e2e/v17_8_0_1_sltp_intelligence.e2e.ts` | Vitest (`4 passed`) & Playwright E2E (`3 passed`) | **RESOLVED & VERIFIED** |
| `DEF-178-13` | P1 | Variant D Replay Calibration Hardcoded | Variant D multiplied Variant C's buffer by `1.25` without disclosing `EXPERIMENTAL_FIXED_MULTIPLIER` vs `EMPIRICAL_P90_NOISE_CALIBRATION`, and `/api/v1/research/stop-placement/replay-comparison` returned `status="AVAILABLE"` even when `plans` was empty. | `backend/services/unicorn_replay_service.py`, `backend/services/stop_placement_research_service.py`, `backend/main.py` | `test_def_178_13_honest_replay_calibration_and_endpoint` | **RESOLVED & VERIFIED** |
| `DEF-178-14` | P1 | Forensic Audit Internal Contradictions | `V17_8_FINAL_RELEASE_REPORT.md` cited "~9 seconds", "$5.60R", and `0.15 oz -> $2.48 risk` for `ord_...1793102220000_1793102400000`, whereas the persisted DB record had `hold_time = 111.597s` and the authoritative calculator produces `0.35 oz` (`$2.50` risk, `2.1617R` taker TP / `2.2738R` maker TP) or `0.15 oz` (`$1.07` risk). | `docs/V17_8_0_1_FORENSIC_TRUTH_RECONCILIATION.md`, `docs/V17_8_STOP_OUT_FORENSIC_AUDIT.md`, `docs/V17_8_FINAL_RELEASE_REPORT.md` | `test_def_178_14_forensic_audit_truth_reconciliation` | **RESOLVED & VERIFIED** |
| `DEF-178-15` | P0 | Acceptance Gates Did Not Catch Runtime Defects | `G38` only ran `test_v17_7_adaptive_stop_loss.py` and the acceptance runner lacked gates `G43`–`G52` for V17.8.0.1 runtime contracts, flag persistence, fill-time guards, causal Unicorn binding, mounted E2E, forensic reconciliation, and honest replay disclosure. | `tools/acceptance_verdict.py`, `tools/run_v17_8_acceptance.py`, `backend/tests/test_v17_8_acceptance_runner.py` | `G01`–`G52` in `tools/run_v17_8_acceptance.py` | **RESOLVED & VERIFIED** |

