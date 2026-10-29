# V17.9.0.1.2 — Initial Code & Architecture Audit (`V179012_INITIAL_CODE_AUDIT.md`)

- **Repository:** `quocan050302/Build-app-crypto` (`aurum-desk`)
- **Git Top-Level Root:** `/Users/macbook/Documents/Project_Github/Build-app-crypto`
- **Workspace Root:** `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk`
- **Branch:** `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Original HEAD SHA (Pre-V17.9.0.1.2):** `f2a98c49c2eab28df2181af31f4270ba7d9f4378`
- **Previously Tested Implementation SHA (V17.9.0.1.1):** `397cf251584470a84f20d222702c5f6e038cf271`
- **Pre-Refactor Historical Baseline Commit (V17.8.0.4):** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Working Tree State Before Changes:** `CLEAN` (`git status -s` returned 0 modified/untracked files)

---

## 1. Authoritative Decision Entry Points & Relevant Files Mapped

| Subsystem | File | Entry Point / Function | Current Role |
|---|---|---|---|
| **Canonical Shared Evaluator** | `backend/services/shared_strategy_evaluator.py` | `SharedStrategyEvaluator.evaluate_decision()`, `evaluate_model_direction()`, `_execute_production_domain_spec()`, `enforce_canonical_decision_authority_on_candidate()`, `enforce_canonical_decision_authority_on_cg01()` | Pre-execution strategy evaluation, structural SL/TP selection, risk sizing, and legacy candidate reconciliation |
| **Production Runtime Orchestrator** | `backend/services/scalp_watcher_orchestrator.py` | `ScalpWatcherOrchestrator.evaluate_cycle()`, `_arm_paper_candidate()` | Evaluates 5 production models (`SMC_M1_REVERSAL`, `SMC_M1_CONTINUATION`, `SMC_M1_FVG_CONTINUATION`, `SMC_M1_SWEEP_RECLAIM`, `CG01`), routes candidates, enforces duplicate `decision_id` guard, and arms paper orders |
| **Market Shadow Runtime** | `backend/services/market_shadow_service.py` | `MarketShadowService.evaluate_shadow_cycle()`, `_evaluate_single_model_shadow()`, `compare_production_shadow_divergence()` | Read-only shadow evaluation of all 5 production models and divergence comparison against Production |
| **Historical Backtest Runtime** | `backend/services/v17_8_backtest_runner.py` | `_extract_historical_opportunities_chronologically()`, `evaluate_and_simulate_variants_on_opportunities()`, `evaluate_production_parity_model_snapshot()` | Point-in-time historical replay, opportunity extraction, authoritative production-parity trade simulation, and A/B/C/D SL variant research |
| **Historical Baseline Oracle** | `backend/services/v17_9_0_1_baseline_oracle.py` | `verify_historical_baseline_commit_provenance()`, `IndependentPreRefactorBaselineOracle`, `generate_phase_b_baseline_artifacts()` | Characterization and provenance verification against pre-refactor commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` |
| **Parity & Artifact Generator** | `tools/generate_v17_9_0_1_artifacts.py` | `compute_cross_runtime_parity_report()`, `generate_all_v17_9_0_1_artifacts()` | Cross-runtime parity verification and byte-exact LF (`\n`) artifact bundle generation |
| **Async Runner, Disk Guard & State** | `tools/run_v17_9_async_regression.py`, `tools/test_resource_guard.py`, `tools/v17_9_execution_state.py`, `tools/run_v17_8_acceptance.py`, `tools/acceptance_verdict.py` | `BoundedAsyncTestOrchestrator`, `TestResourceGuard`, `ExecutionStateManager`, `MANDATORY_GATES_V17_9_0` (`G01`–`G107`) | Bounded multi-worker test execution, disk reserve protection, checkpoint/resume, and exact-commit gate verification |

---

## 2. Reproduced Unresolved Defects (`DEF-179012-01` through `DEF-179012-05`)

### 2.1 `DEF-179012-01` (P0) — Canonical Eligibility Can Be Promoted by Legacy Domain Evaluators
- **Location:** `backend/services/shared_strategy_evaluator.py`, `_execute_production_domain_spec()` (lines 2287–2510).
- **Exact Reproduction:**
  1. In the `CG01` branch (lines 2309–2377), line 2317 only checks `if not context.causal_valid or base_snap.decision_status in (DecisionStatus.NO_TRADE.value, DecisionStatus.REJECTED.value):`. If `base_snap.decision_status` is `VALID_SETUP_REJECTED_BY_RISK`, `NO_VALID_SETUP`, `WAITING_CONFIRMATION`, `MODEL_DISABLED`, or `MISSING_MARKET_DATA`, and the legacy `ScalpM1ModelService.evaluate_cg01_candidate()` returns `cg_cand.is_eligible = True`, lines 2323–2376 overwrite `d_stat = DecisionStatus.ELIGIBLE.value`, clear `rejection_reason_codes = ()`, and return a promoted `ELIGIBLE` canonical snapshot.
  2. In the four M1 branches (`REVERSAL`, `CONTINUATION`, `FVG_CONTINUATION`, `SWEEP_RECLAIM`, lines 2433–2510), line 2433 similarly only blocks `NO_TRADE` and `REJECTED`. At line 2446, `if not is_el and base_snap.decision_status != DecisionStatus.ELIGIBLE.value:` only returns `base_snap` when `cand.is_eligible` is `False`. When `cand.is_eligible` is `True`, line 2462 sets `d_stat = DecisionStatus.ELIGIBLE.value` and promotes `VALID_SETUP_REJECTED_BY_RISK`, `NO_VALID_SETUP`, `WAITING_CONFIRMATION`, `MODEL_DISABLED`, or `MISSING_MARKET_DATA` into `DecisionStatus.ELIGIBLE.value`, while overwriting canonical `entry_reference`, `selected_sl`, `take_profit`, `net_rr`, and `hypothetical_quantity` with legacy candidate values.
- **Minimal Repair Plan:**
  - Introduce the explicit global positive eligibility contract `canonical_is_executable(decision, context=None) -> bool` requiring `decision.decision_status == DecisionStatus.ELIGIBLE.value`, `decision.setup_validity == "VALID"`, valid lineage (`decision_id`, `decision_fingerprint`, `input_snapshot_hash`, `configuration_hash`, `evaluator_version`), valid finite directional entry/SL/TP geometry, valid risk intent (`hypothetical_quantity > 0`, `net_rr >= min_net_rr`, empty `rejection_reason_codes`), and valid point-in-time causal inputs.
  - In `_execute_production_domain_spec()`, enforce a strict monotonic safety invariant: if `not canonical_is_executable(base_snap, context)` (i.e., `base_snap.decision_status != DecisionStatus.ELIGIBLE.value`), a legacy candidate (`cand` or `cg01_dec`) can **never** promote the decision to `ELIGIBLE`. Force `cand.is_eligible = False`, preserve `base_snap`'s non-eligible status and rejection reasons, and preserve canonical fields. Conversely, if `base_snap` is `ELIGIBLE` and the domain evaluator fails a guard (`not cand.is_eligible`), allow monotonic downgrade to a non-eligible status.
  - Enforce `canonical_is_executable` inside `enforce_canonical_decision_authority_on_candidate()`, `enforce_canonical_decision_authority_on_cg01()`, and `ScalpWatcherOrchestrator` (`evaluate_cycle()` candidate selection and `_arm_paper_candidate()`).

### 2.2 `DEF-179012-02` (P0) — Historical Backtest Eligibility Gate Is Too Permissive
- **Location:** `backend/services/v17_8_backtest_runner.py`, `_extract_historical_opportunities_chronologically()` (lines 1399–1435) and `evaluate_and_simulate_variants_on_opportunities()` (lines 1681–1707, 1935–1968).
- **Exact Reproduction:**
  1. `_extract_historical_opportunities_chronologically()` uses a negative status blacklist `("NO_TRADE", "REJECTED", "NO_VALID_SETUP", "WAITING_CONFIRMATION", "MODEL_DISABLED", "MISSING_MARKET_DATA")` rather than requiring positive `shared_bt_snap.decision_status == DecisionStatus.ELIGIBLE.value`.
  2. In `evaluate_and_simulate_variants_on_opportunities()`, lines 1684–1691 also use the 6-item blacklist (omitting `VALID_SETUP_REJECTED_BY_RISK`, `UNKNOWN`, and unrecognized statuses). Worse, at line 1936, `canon_eligible = bool(opp.get("canonical_is_eligible", True))` defaults to `True`! When an opportunity dict is injected with `canonical_decision_status = "VALID_SETUP_REJECTED_BY_RISK"` (without `canonical_is_eligible=False`), `canon_eligible` defaults to `True` and `authoritative_parity_trades` executes a simulated trade!
- **Minimal Repair Plan:**
  - In `_extract_historical_opportunities_chronologically()`, require `canonical_is_executable(shared_bt_snap, bt_shared_ctx)` (`shared_bt_snap.decision_status == DecisionStatus.ELIGIBLE.value`) for authoritative eligibility, and provide `include_research_sl_comparison_candidates: bool = False` (or separate `research_opportunities`) so authoritative opportunity extraction strictly requires `DecisionStatus.ELIGIBLE.value` while explicitly labeled A/B/C/D SL research cohorts remain clearly separated and never mislabeled as authoritative production-parity results.
  - In `evaluate_and_simulate_variants_on_opportunities()`, remove the `True` default on `canonical_is_eligible` and enforce strict positive `canonical_is_executable` authorization at the authoritative simulation boundary: require `canon_status == DecisionStatus.ELIGIBLE.value`, valid `shared_decision_snapshot` passing `canonical_is_executable`, valid lineage, valid geometry, and valid risk intent. Every non-`ELIGIBLE` status (`VALID_SETUP_REJECTED_BY_RISK`, `NO_VALID_SETUP`, `WAITING_CONFIRMATION`, `MODEL_DISABLED`, `MISSING_MARKET_DATA`, `NO_TRADE`, `REJECTED`, `UNKNOWN`, `None`, malformed) fails closed with zero authoritative trades and an explicit rejection record in `authoritative_production_parity_rejections`.

### 2.3 `DEF-179012-03` (P1) — Historical Baseline Is Not Independently Executed
- **Location:** `backend/services/v17_9_0_1_baseline_oracle.py`, `verify_historical_baseline_commit_provenance()` (lines 1583–1620) and `IndependentPreRefactorBaselineOracle`.
- **Exact Reproduction:**
  1. The Git top-level directory is `/Users/macbook/Documents/Project_Github/Build-app-crypto`, so repository-relative paths in commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` are `aurum-desk/backend/services/...`. Because `verify_historical_baseline_commit_provenance()` passed `backend/services/...` to `git show <commit>:<path>`, `git show` failed on all 8 files and `historical_file_sha256s` in `docs/v17_9_0_1/BASELINE_MANIFEST.json` was empty (`{}`).
  2. `IndependentPreRefactorBaselineOracle` imported `smc_engine`, `domain_calculator`, `services.scalp_m1_model_service`, etc. from the current working tree rather than executing the historical code from commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` in an isolated process with historical dependencies.
- **Minimal Repair Plan:**
  - Discover the Git top-level root dynamically via `git rev-parse --show-toplevel` and resolve actual repository-relative paths (`aurum-desk/backend/...`) via `git ls-tree` at `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`. Fail closed if `historical_file_sha256s` is empty or any required historical file is missing.
  - Materialize an isolated temporary snapshot/worktree of `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` (cleaned up deterministically after execution to respect the disk budget) and execute the historical baseline in a separate Python subprocess whose `PYTHONPATH` points exclusively to the historical snapshot's `aurum-desk/backend` directory, consuming versioned serialized JSON inputs and returning deterministic JSON decisions across all 55 characterization scenarios plus real historical XAUUSDT dataset snapshots.

### 2.4 `DEF-179012-04` (P1) — Cross-Runtime Parity Does Not Fully Validate Consumed Decisions on Real Historical Data
- **Location:** `tools/generate_v17_9_0_1_artifacts.py`, `compute_cross_runtime_parity_report()` (lines 129–535).
- **Exact Reproduction:**
  - `compute_cross_runtime_parity_report()` evaluated 55 synthetic characterization scenarios via `.evaluate_shared_decision_snapshot()` plus 1 synthetic candle series (5 models = 60 comparisons). It did not replay real historical XAUUSDT candles chronologically through the full downstream decision consumption paths of `ScalpWatcherOrchestrator.evaluate_cycle()`, `MarketShadowService.evaluate_shadow_cycle()`, and `v17_8_backtest_runner`.
- **Minimal Repair Plan:**
  - Replay verified historical XAUUSDT market states chronologically across:
    1. Canonical `SharedStrategyEvaluator.evaluate_decision()`
    2. Actual Production decision path (`ScalpWatcherOrchestrator.evaluate_cycle()` in dry-run/shadow mode, inspecting actual consumed candidate and decision records)
    3. Actual `MARKET_SHADOW` cycle (`MarketShadowService.evaluate_shadow_cycle()`, inspecting actual consumed `ShadowModelEvaluation` and opportunity states)
    4. Actual Backtest decision path (`_extract_historical_opportunities_chronologically()` / `evaluate_and_simulate_variants_on_opportunities()` / `evaluate_production_parity_model_snapshot()`)
  - Enforce exact input comparability (`input_snapshot_hash`, `configuration_hash`, symbol, timestamp, quote, closed M1/M5/M15/H1/H4 candles) and classify each comparison into `EXACT_MATCH`, `DECISION_DIVERGENCE`, `INPUT_MISMATCH`, `NOT_COMPARABLE`, `NOT_EVALUATED`, or `INVALID_EVIDENCE`.
  - Ensure full coverage across all 5 models, `LONG`/`SHORT`, `ELIGIBLE`, `NO_TRADE`, `VALID_SETUP_REJECTED_BY_RISK`, `NO_VALID_SETUP`, `MISSING_MARKET_DATA`, boundary conditions, and all historical eligible opportunities from the verified XAUUSDT dataset.

### 2.5 `DEF-179012-05` (P2) — Resource Telemetry Is Incomplete
- **Location:** `tools/test_resource_guard.py` and `tools/run_v17_9_async_regression.py`.
- **Exact Reproduction:**
  - `DISK_GUARD_REPORT.json` did not expose `free_disk_bytes_preflight`, `minimum_free_disk_bytes_observed`, `free_disk_bytes_postflight`, `peak_artifact_bytes`, `peak_memory_bytes`, `samples_recorded`, and `orphan_worker_count` under the required telemetry keys, causing `ASYNC_RUNNER_PERFORMANCE_REPORT.json` to report `free_disk_bytes_postflight: null` and `samples_recorded: 0`.
  - `sample_cpu_memory()` lacked a macOS/POSIX `resource` / `ps` fallback when `psutil` is not installed.
  - Serial-vs-parallel speedup was reported only as `sum_of_group_durations_sec / wall_clock_duration_sec` without an independently measured serial benchmark or explicit qualification.
- **Minimal Repair Plan:**
  - Add periodic runtime sampling and POSIX `resource` / `ps` / `vm_stat` fallback in `TestResourceGuard.sample_cpu_memory()`.
  - Record and report all required telemetry keys (`free_disk_bytes_preflight`, `minimum_free_disk_bytes_observed`, `free_disk_bytes_postflight`, `peak_temporary_bytes`, `peak_artifact_bytes`, `samples_recorded`, `peak_memory_bytes`, `final_temporary_bytes`, `orphan_worker_count`).
  - Measure an actual serial vs parallel benchmark workload and report both `measured_serial_duration_sec`, `measured_parallel_duration_sec`, `measured_speedup_ratio`, and the qualified `aggregate_duration_to_wall_ratio`.

---

## 3. Already-Resolved Issues Preserved (Do Not Break)
1. `SharedStrategyEvaluator` 20-field `StrategyDecisionSnapshot` / `DecisionResult` contract and deterministic SHA-256 `decision_fingerprint`.
2. Duplicate `decision_id` replay guard (`ScalpWatcherOrchestrator.register_seen_decision_id`).
3. Fail-closed exception handling inside `SharedStrategyEvaluator.evaluate_decision()`.
4. Strict LF (`\n`) byte-exact file writing and immediate post-write SHA-256 verification.
5. `verify_gate_provenance_integrity()` preventing stale `tested_source_sha` reuse across acceptance gates `G01`–`G107`.

