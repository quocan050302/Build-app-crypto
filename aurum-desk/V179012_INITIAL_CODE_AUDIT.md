# AURUM DESK V17.9.0.1.2 — Initial Code & Defect Audit (`Phase A`)

## 1. Repository & Git State Verification

| Property | Verified Value |
|---|---|
| **Repository** | `quocan050302/Build-app-crypto` |
| **Git Top-Level Directory** | `/Users/macbook/Documents/Project_Github/Build-app-crypto` |
| **Application Directory** | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk` |
| **Active Branch** | `feat/v17-8-unicorn-structural-sl-tp-intelligence` |
| **Initial HEAD SHA (`Evidence HEAD`)** | `f2a98c49c2eab28df2181af31f4270ba7d9f4378` |
| **Previous Executable Source SHA** | `397cf251584470a84f20d222702c5f6e038cf271` |
| **Remote Tracking Branch SHA** | `f2a98c49c2eab28df2181af31f4270ba7d9f4378` (`origin/feat/v17-8-unicorn-structural-sl-tp-intelligence`) |
| **Historical Baseline Commit SHA** | `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` (`V17.8.0.4: complete production-parity historical backtest, causal Unicorn validation, 88/88 gates PASS`) |
| **Initial Working Tree Status** | Clean (`0` modified or untracked files) |
| **Trading Authorization** | `ZERO` (`AUTO_PAPER_NOT_AUTHORIZED_BY_V17_9_0`, `LIVE_TRADING_NOT_AUTHORIZED`) |

---

## 2. Exact Line-Level Findings for `DEF-179012-01` through `DEF-179012-05`

### `DEF-179012-01` (P0) — Canonical eligibility can be promoted by legacy domain evaluators
- **File:** `backend/services/shared_strategy_evaluator.py`
- **Function:** `SharedStrategyEvaluator._execute_production_domain_spec()` (lines `2287–2510`)
- **Observed Defect:**
  1. In the `CG01` branch (lines `2309–2377`), line `2309` only checks:
     `if not context.causal_valid or base_snap.decision_status in (DecisionStatus.NO_TRADE.value, DecisionStatus.REJECTED.value):`
     When `base_snap.decision_status` is `VALID_SETUP_REJECTED_BY_RISK`, `NO_VALID_SETUP`, `WAITING_CONFIRMATION`, `MODEL_DISABLED`, `MISSING_MARKET_DATA`, or an unknown status (e.g. `"UNKNOWN"`), and `cg_cand.is_eligible` is `True`, lines `2323–2376` construct a new `StrategyDecisionSnapshot` with `decision_status=DecisionStatus.ELIGIBLE.value` and overwrite `direction`, `entry_reference`, `selected_sl`, `take_profit`, `net_rr`, `gross_rr`, and `hypothetical_quantity` from `cg_cand`.
  2. In the 4 M1 model branches (`SMC_M1_REVERSAL`, `SMC_M1_CONTINUATION`, `SMC_M1_FVG_CONTINUATION`, `SMC_M1_SWEEP_RECLAIM`, lines `2433–2510`), line `2433` only blocks `DecisionStatus.NO_TRADE.value` and `DecisionStatus.REJECTED.value`. At line `2445`, `is_el = bool(cand.is_eligible)`. If `is_el` is `True` while `base_snap.decision_status` is `VALID_SETUP_REJECTED_BY_RISK`, `NO_VALID_SETUP`, `WAITING_CONFIRMATION`, `MODEL_DISABLED`, `MISSING_MARKET_DATA`, or `"UNKNOWN"`, line `2446` (`if not is_el and base_snap.decision_status != DecisionStatus.ELIGIBLE.value:`) is skipped and lines `2462–2510` promote `base_snap` to `DecisionStatus.ELIGIBLE.value`.
- **Required Repair (`Phases B & C`):**
  - Implement `canonical_is_executable(decision, context)` enforcing strict positive eligibility (`decision.decision_status == DecisionStatus.ELIGIBLE.value` plus valid setup, direction, entry/SL/TP geometry, risk intent, decision lineage, and point-in-time inputs).
  - Enforce a strict monotonic safety invariant in `_execute_production_domain_spec()`, `enforce_canonical_decision_authority_on_candidate()`, `enforce_canonical_decision_authority_on_cg01()`, and `ScalpWatcherOrchestrator`: a legacy domain candidate can reject/downgrade an eligible canonical decision on additional safety grounds, but can NEVER promote a non-eligible canonical decision (`decision_status != DecisionStatus.ELIGIBLE.value`) to `ELIGIBLE` or override canonical direction, entry, SL, TP, quantity, R:R, or lineage.

### `DEF-179012-02` (P0) — Historical Backtest eligibility gate is too permissive
- **File:** `backend/services/v17_8_backtest_runner.py`
- **Functions:** `_extract_historical_opportunities_chronologically()` (lines `1386–1435`) and `evaluate_and_simulate_variants_on_opportunities()` (lines `1681–1707` and `1935–1968`)
- **Observed Defect:**
  1. In `_extract_historical_opportunities_chronologically()`, line `1400` only rejects a 6-status deny-list (`"NO_TRADE"`, `"REJECTED"`, `"NO_VALID_SETUP"`, `"WAITING_CONFIRMATION"`, `"MODEL_DISABLED"`, `"MISSING_MARKET_DATA"`), allowing `VALID_SETUP_REJECTED_BY_RISK` or unknown statuses into `opportunities`. Furthermore, `build_market_decision_context` at line `1386` omitted `min_net_rr=float(getattr(cfg, "min_net_rr", 1.2))`, causing `bt_shared_ctx` to default to `min_net_rr=1.8` while `evaluate_production_parity_model_snapshot`, `ScalpWatcherOrchestrator`, and `MarketShadowService` used `cfg.min_net_rr` (`1.2`).
  2. In `evaluate_and_simulate_variants_on_opportunities()`, line `1684` uses the same incomplete 6-status deny-list, and line `1936` uses a permissive default `canon_eligible = bool(opp.get("canonical_is_eligible", True))`.
- **Required Repair (`Phase E`):**
  - Pass `min_net_rr=float(getattr(cfg, "min_net_rr", 1.2))` into `bt_shared_ctx` in `_extract_historical_opportunities_chronologically()`.
  - Enforce `canonical_is_executable(shared_bt_snap, bt_shared_ctx)` (`shared_bt_snap.decision_status == DecisionStatus.ELIGIBLE.value` plus valid lineage, setup, direction, entry/SL/TP geometry, risk intent, and point-in-time market data) at opportunity extraction (`E1`).
  - Enforce strict positive canonical authorization at `evaluate_and_simulate_variants_on_opportunities()` (`E2`) without permissive defaults (`canonical_is_eligible is True`, `canonical_decision_status == DecisionStatus.ELIGIBLE.value`, valid `shared_decision_snapshot`, non-empty `decision_id` and `decision_fingerprint`, matching `direction`/`entry_price`/`selected_sl`/`take_profit` geometry, and positive risk intent).

### `DEF-179012-03` (P1, mandatory) — Historical baseline is not independently executed and reports empty file hashes
- **File:** `backend/services/v17_9_0_1_baseline_oracle.py`
- **Functions:** `verify_historical_baseline_commit_provenance()` (lines `1575–1630`) and `IndependentPreRefactorBaselineOracle`
- **Observed Defect:**
  1. `repo_root = Path(__file__).resolve().parents[2]` resolves to `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk`, whereas the Git repository top-level (`git rev-parse --show-toplevel`) is `/Users/macbook/Documents/Project_Github/Build-app-crypto`. Consequently, `git show c356d655...:backend/services/...` failed because the Git tree stores files under `aurum-desk/backend/services/...`, producing `historical_file_sha256s: {}` (`verified_files_count: 0`).
  2. `IndependentPreRefactorBaselineOracle` imported `smc_engine`, `domain_calculator`, `services.scalp_m1_model_service`, etc. from the current working tree rather than executing the historical baseline code from commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` with its original historical dependencies.
- **Required Repair (`Phase F`):**
  - Resolve the Git top-level directory via `git rev-parse --show-toplevel` and inspect paths via `git ls-tree -r --name-only <commit_sha>` (`aurum-desk/backend/...`), computing non-empty SHA-256 digests for all required historical baseline files and failing closed (`BLOCKED`) if any required historical file is missing or `historical_file_sha256s` is empty.
  - Extract the historical `aurum-desk/backend` tree from commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` into an isolated temporary runtime directory (cleaned up deterministically within the disk guard budget) and execute the historical baseline evaluations in a dedicated subprocess whose `sys.path` points exclusively to the extracted historical `backend` tree (`0` imports from the current working tree), covering both the 55 characterization scenarios and representative real historical `XAUUSDT` market states.

### `DEF-179012-04` (P1) — Cross-runtime parity does not fully validate consumed decisions on real historical market states
- **File:** `tools/generate_v17_9_0_1_artifacts.py`
- **Function:** `compute_cross_runtime_parity_report()` (lines `129–536`)
- **Observed Defect:**
  - Previously compared 55 synthetic characterization scenarios and 5 synthetic live cycle evaluations, relying heavily on adapter helper methods (`evaluate_shared_decision_snapshot`, `evaluate_production_parity_model_snapshot`) rather than replaying real historical `XAUUSDT` market states chronologically and comparing the actual downstream decisions consumed by Production (`ScalpWatcherOrchestrator`), `MARKET_SHADOW` (`MarketShadowService.evaluate_shadow_cycle`), and Historical Backtest (`v17_8_backtest_runner`).
- **Required Repair (`Phases G & H`):**
  - Replay real historical `XAUUSDT` market states chronologically from the verified historical dataset across Canonical (`SharedStrategyEvaluator.evaluate_decision`), Production (`ScalpWatcherOrchestrator` dry-run cycle), `MARKET_SHADOW` (`MarketShadowService.evaluate_shadow_cycle`), and Historical Backtest (`_extract_historical_opportunities_chronologically` / `evaluate_production_parity_model_snapshot`), verifying exact input comparability and comparing all consumed downstream decision fields across all 5 models and status categories.

### `DEF-179012-05` (P2) — Resource telemetry in async test runner is incomplete
- **Files:** `tools/run_v17_9_async_regression.py` and `tools/test_resource_guard.py`
- **Observed Defect:**
  - `ASYNC_RUNNER_PERFORMANCE_REPORT.json` previously recorded `samples_recorded: 0` and `free_disk_bytes_postflight: null` because `disk_rep` used key `latest_free_disk_bytes` instead of `postflight_free_disk_bytes` and did not include `cpu_memory_samples` in `build_disk_budget_report()`, and no periodic background sampling task ran during `run_groups()`.
- **Required Repair (`Phases J & K`):**
  - Add periodic resource sampling in `BoundedAsyncTestOrchestrator.run_groups()` and populate all mandatory `K5` telemetry fields (`free_disk_bytes_preflight`, `minimum_free_disk_bytes_observed`, `free_disk_bytes_postflight`, `peak_temporary_bytes`, `peak_artifact_bytes`, `samples_recorded > 0`, `peak_memory_bytes`, `final_temporary_bytes`, `orphan_worker_count`), plus honest serial-vs-parallel benchmark timing.

---

## 3. Files Planned for Modification / Creation

1. `backend/services/shared_strategy_evaluator.py` — Implement `canonical_is_executable(...)`, strict monotonic domain reconciliation in `_execute_production_domain_spec()`, and strict authority guards.
2. `backend/services/scalp_watcher_orchestrator.py` — Enforce `canonical_is_executable(...)` before candidate selection and paper arming.
3. `backend/services/market_shadow_service.py` — Enforce `canonical_is_executable(...)` in `_evaluate_single_model_shadow()` and `evaluate_shadow_cycle()`.
4. `backend/services/v17_8_backtest_runner.py` — Enforce `canonical_is_executable(...)` in `_extract_historical_opportunities_chronologically()` and strict positive eligibility in `evaluate_and_simulate_variants_on_opportunities()`.
5. `backend/services/v17_9_0_1_baseline_oracle.py` — Fix Git top-level path resolution (`aurum-desk/backend/...`), fail-closed provenance verification, and isolated subprocess execution of historical commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`.
6. `tools/test_resource_guard.py` & `tools/run_v17_9_async_regression.py` — Periodic resource sampling, complete `K5` disk/memory telemetry fields, and serial-vs-parallel benchmark measurement.
7. `tools/acceptance_verdict.py` & `tools/run_v17_8_acceptance.py` — Wire V17.9.0.1.2 strict eligibility and parity tests into `G89`–`G107`.
8. `tools/generate_v17_9_0_1_artifacts.py` — Implement real historical chronological cross-runtime parity replay (`ACTUAL_RUNTIME_PARITY_REPORT.json`) and generate all 21 mandatory Section 18 artifacts with byte-exact LF SHA-256 verification.
9. `backend/tests/test_v17_9_0_1_2_strict_eligibility_and_parity.py` — Implement all 30 mandatory negative tests (`N01`–`N30`) and positive invariants.
