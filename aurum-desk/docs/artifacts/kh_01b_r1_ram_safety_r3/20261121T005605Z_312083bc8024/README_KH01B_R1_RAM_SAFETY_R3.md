# AURUM DESK — KH-01B.R1.DSC.R3: RAM SAFETY & ADAPTIVE TEST SCHEDULING CLOSURE (`KH-01B.R1.DSC.R3-v1.0`)

- **Run ID**: `20261121T005605Z_312083bc8024`
- **Mandatory Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **HEAD at Start (Accepted R2 Publication HEAD)**: `64fbc433e2bffb378c649624961b7c69ee532172`
- **Prior Accepted R2 Run Preserved Untouched**: `20261120T021021Z_c20b0b1bec13` (`tested_sha = c20b0b1bec1344e2da258a5d18832874f5f5668a`)
- **Tested Executable Source SHA (`CODE_COMMIT_SHA`)**: `312083bc80247c4931e3b7ab059a346d360d465f`
- **Canonical Dataset SHA256 (`REUSED_RESEARCH`, `MODELED_BID_ASK`)**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **RAM Policy Version**: `KH-01B.R1.DSC.R3.RAM.v1` (`policy_sha256 = ed4d56ccf8ff5c16b12b04db56c6c6d28ddef2bb50f0c753df768411686a39ef`)

## 1. Orthogonal Verdicts
- `ENGINEERING_CLOSURE_VERDICT`: **PASS**
- `RAM_SAFETY_VERDICT`: **PASS_STRICT_512MIB_FLOOR** (Hard floor: `>= 512.0 MiB`; R2 min RAM: `68.91 MiB`; R3 min sampled RAM: `5522.32 MiB`; Headroom above 512 MiB: `5010.32 MiB`)
- `DISK_SAFETY_VERDICT`: **PASS_STRICT_20GIB** (Preflight floor: `>= 20.5 GiB`; Constraining preflight free: `44.9604 GiB`; Min runtime free: `44.9166 GiB`)
- `FULL_REGRESSION_VERDICT`: **PASS** (`collected = 5518`, `executed = 5518`, `passed = 5510`, `skipped = 8`, `failed = 0`, `errors = 0`)
- `WORKER_DETERMINISM_VERDICT`: **PASS** (`pytest w4 vs w1 speedup = 1.445x`)
- `BACKTEST_VERDICT`: **PASS** (`56710` A1 + `6375` SI8 = `63085` cohorts; `SI0` `242` scheduled / `166` closed = `9W/157L`, `net = -345.8971 USD`, `246` 3-adapter episodes `0` mismatches)
- `RESEARCH_ECONOMIC_VERDICT`: **NO_PROVEN_EDGE_BASELINE_UNCHANGED_FEE_SENSITIVITY_NEGATIVE**
- `KH01C_ADVANCEMENT_VERDICT`: **NO_ECONOMIC_ADVANCEMENT_CANDIDATE**
- `FRESH_OOS_VERDICT`: **BLOCKED_NO_UNTOUCHED_DATA** (`strategy_evaluation_count = 0`)
- `ACTIVATION_READY`: **NO** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)
- `PUBLICATION_VERDICT` (pre-push bundle): **PENDING_REMOTE_PUSH**

## 2. R3 RAM Safety & Adaptive Scheduler Telemetry (`02`, `03`, `08`, `09`)
- **Hard Floor & Headroom**: `hard_abort_floor_mib = 512.0`, `min_sampled_available_ram_mib = 5522.32`, `headroom_above_512mib = 5010.32 MiB` (`samples_count = 3378`).
- **Process-Tree Peak RSS**: `peak_parent_rss_mib = 2443.062`, `peak_child_tree_rss_mib = 877.551`, `peak_total_rss_mib = 2443.062` (system available RAM also reflects external non-test OS processes).
- **Scheduler Backpressure & Descendants**: `max_children_observed = 4` (`<= 4`), `orphan_pids_at_cleanup_count = 0`, `slot_downshift_events = 0`, `backpressure_pauses = 0`, `singleton_validated_admissions = 3`.
- **Adversarial RED/GREEN Controls (`11`)**: `43/43` PASS.

## 3. Orthogonal Gate Matrix (`S01..S14`)
| Gate ID | Gate Name | Status | Observed Summary |
|---|---|---|---|
| `S01` | `SOURCE_BRANCH_TESTED_SHA_AND_R2_IMMUTABILITY` | **PASS** | `{"branch": "feature/v17-9-2-structural-sl-tp-from-qa", "modified_prior_files": 0, "tested_sha": "312083bc80247c4931e3b7ab059a346d360d465f"}` |
| `S02` | `RAM_HARD_FLOOR_512MIB_ENFORCED_NO_64MIB_OVERRIDE` | **PASS** | `{"hard_floor_mib": 512.0, "headroom_above_512mib_mib": 5010.32, "min_sampled_ram_mib": 5522.32, "ram_safety_verdict": "PASS_STRICT_512MIB_FLOOR"}` |
| `S03` | `PREDICTIVE_RAM_ADMISSION_HYSTERESIS_SINGLETON_AND_DESCENDANT_CAP` | **PASS** | `{"backpressure_pauses": 0, "max_children_observed": 4, "singleton_validated_admissions": 3}` |
| `S04` | `PRESSURE_FAIL_CLOSED_511MIB_BLOCKED_NO_POST_ABORT_LAUNDERING` | **PASS** | `{"passed_ram_controls": 12}` |
| `S05` | `AUTHENTIC_PROCESS_TREE_AND_SHARD_MEMORY_TELEMETRY` | **PASS** | `{"peak_child_tree_rss_mib": 877.551, "peak_parent_rss_mib": 2443.062, "samples_count": 3378}` |
| `S06` | `STRICT_20P5_GIB_ALL_MOUNTS_SENTINEL_CLEANUP_ZERO_ORPHANS` | **PASS** | `{"disk_safety_verdict": "PASS_STRICT_20GIB", "min_free_disk_gib": 44.9166, "orphans": 0}` |
| `S07` | `FULL_PYTEST_COLLECTED_SCHEDULED_EXECUTED_JUNIT_BIJECTION` | **PASS** | `{"collected": 5518, "deselected": 0, "errors": 0, "executed": 5518, "failed": 0, "mandatory_skipped": 0, "passed": 5510, "scheduled": 5518, "skipped": 8, "unknown": 0, "xfailed": 0, "xpassed": 0}` |
| `S08` | `CONTROLLED_W1_W2_W4_REPEAT_DETERMINISM_AND_TRUTHFUL_TIMINGS` | **PASS** | `{"pytest_w4_speedup": 1.445, "slice_optimal_workers": 1}` |
| `S09` | `FULL_BACKTEST_56710_A1_6375_SI8_SI0_242_166_PARITY` | **PASS** | `{"a1": 56710, "net_usd": -345.8971, "si0": "242/166 (9W/157L)", "si8": 6375}` |
| `S10` | `ANTI_FAKE_PASS_31_PRIOR_PLUS_R3_ADVERSARIAL_FAULTS` | **PASS** | `{"passed_mutations": "43/43"}` |
| `S11` | `SEPARATE_OS_PROCESS_INDEPENDENT_DISK_VERIFIER` | **PASS** | `{"s_receipts_count": 13}` |
| `S12` | `OOS_ESCROW_UNTOUCHED_AND_RUNTIME_ACTIVATION_OFF` | **PASS** | `{"FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA", "evaluation_count": 0}` |
| `S13` | `SAME_BRANCH_PUBLICATION_AND_REMOTE_SHA_READBACK` | **PENDING_REMOTE_PUSH** | `{"publication_status": "PENDING_REMOTE_PUSH", "tested_executable_sha": "312083bc80247c4931e3b7ab059a346d360d465f"}` |
| `S14` | `ORTHOGONAL_ECONOMIC_TRUTH_NO_PROVEN_EDGE` | **PASS** | `{"KH01C_ADVANCEMENT_VERDICT": "NO_ECONOMIC_ADVANCEMENT_CANDIDATE", "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE_BASELINE_UNCHANGED_FEE_SENSITIVITY_NEGATIVE"}` |

## 4. Exact Function Callgraph & Before/After SHA256 (`01`)
### Modified / Added R3 Infrastructure Functions
| File | Function | Lines | Purpose | Before SHA12 | After SHA12 |
|---|---|---:|---|---|---|
| `aurum-desk/tools/run_kh_01a_verification.py` | `ResourceMonitorKH01A` | `378..646` | Shared 0.5s process-tree/RAM/disk watchdog with >=512 MiB hard floor latch, active-shard context & full descendant RSS. | `3f2c101b0ca4` | `7a0ae94dd235` |
| `aurum-desk/tools/run_kh_01a_verification.py` | `ResourceMonitorKH01A.check` | `444..549` | Race-proof RAM/disk/descendant sampler latching first hard breach and writing durable resource_abort.json. | `e762219e0860` | `f2b300f7b68c` |
| `aurum-desk/tools/run_kh_01a_verification.py` | `ResourceMonitorKH01A.stop` | `591..646` | Reaps owned descendants and reports separate parent RSS, full child-tree RSS, min RAM headroom & latched error. | `4efcd32f83aa` | `a259329d2f8c` |
| `aurum-desk/tools/run_kh_01b_r1_verification.py` | `_build_lpt_shards_kh01b_r1` | `624..790` | Deterministic LPT shard builder preserving slot_cost=2 for child-spawning/runtime shards and slot_cost=4 for exclusives. | `e68335bc7d7a` | `e68335bc7d7a` |
| `aurum-desk/tools/run_kh_01b_r1_verification.py` | `run_async_lpt_pytest_shards_kh01b_r1` | `793..1492` | Bounded async pytest scheduler with R3 predictive RAM admission, validated singleton budget, 0.2s child-tree RSS & OOM detection. | `b5a91f1ecd18` | `b2394df0eb3f` |
| `aurum-desk/tools/run_kh_01b_r1_verification.py` | `_run_controlled_pytest_w1_w2_w4_benchmark_r1` | `1495..1614` | Controlled 155-test w=1/2/4/1_repeat determinism & speed benchmark with R3 RAM capacity check. | `8e4ab9691fa4` | `fde10c3f186e` |
| `aurum-desk/tools/run_kh_01b_r1_verification.py` | `_run_r1_variant_slice_w1_w2_w4_determinism` | `1651..1773` | 2,800-pair R1 slice w=1/2/4/1_repeat determinism benchmark selecting optimal worker count truthfully. | `c4e4ef208b92` | `5cdf3b0d53ce` |
| `aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py` | `run_kh01b_r1_dsc_r2_orchestrator` | `946..1718` | Replay-compatible R2 orchestrator upgraded to >=512 MiB monitor floor with zero 64 MiB override. | `9ca507f853de` | `b445ed836776` |
| `aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py` | `run_kh01b_r1_ram_safety_closure_orchestrator_r3` | `1766..2661` | New R3 orchestrator enforcing >=512 MiB hard floor, stage memory leak telemetry, bounded GC & R3 artifact generation. | `NEW_IN_R3` | `35030495aca7` |
| `aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py` | `evaluate_r3_shard_admission` | `248..417` | Pure R3 hysteresis & projected-footprint admission gate blocking active_slots==0 low-RAM bypass. | `NEW_IN_R3` | `ed5f5b7048dc` |
| `aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py` | `compute_r3_ram_preflight_and_policy` | `420..499` | Freezes KH-01B.R1.DSC.R3.RAM.v1 thresholds, empirical shard projections & preflight verdict. | `NEW_IN_R3` | `7adc2c790cde` |
| `aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py` | `run_kh01b_r1_r3_ram_fault_suite` | `828..1202` | 43-mutation RED/GREEN adversarial fault suite (31 prior DSC + 12 R3 RAM/scheduler/OOM faults). | `NEW_IN_R3` | `0f5f4c0d83e0` |
| `aurum-desk/backend/services/kh_01b_r1_ram_safety_r3_service.py` | `evaluate_kh01b_r1_ram_safety_r3_gates_from_disk` | `1387..1855` | Independent disk-based evaluator for orthogonal R3 gates S01..S14. | `NEW_IN_R3` | `7233877a6613` |
| `aurum-desk/tools/kh_01b_r1_data_safety_independent_verifier.py` | `verify_kh_01b_r1_ram_safety_r3_run_dir_from_disk` | `808..1153` | Separate-OS-process R3 verifier recomputing min RAM from 08 jsonl.gz, JUnit bijection & backtest fingerprints. | `NEW_IN_R3` | `deaef4143d2f` |

### Immutable Production & Strategy Functions (`KEEP_AS_IS`)
| File | Function | Lines | Purpose | Before SHA12 | After SHA12 |
|---|---|---:|---|---|---|
| `aurum-desk/backend/services/shared_strategy_evaluator.py` | `SharedStrategyEvaluator.evaluate_model_direction` | `2706..4336` | Production model evaluation; untouched. | `b7f05dcd49ce` | `b7f05dcd49ce` |
| `aurum-desk/backend/domain_calculator.py` | `CostAssumptions` | `20..41` | Authoritative default cost config (maker=0.0002, taker=0.0004, slip=0.10); read-only. | `db36e52440ce` | `db36e52440ce` |
| `aurum-desk/backend/domain_calculator.py` | `calculate_risk_reward` | `197..585` | Authoritative R:R, fee, sizing & liquidation domain calculator; read-only. | `359a22f60075` | `359a22f60075` |
| `aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py` | `advance_pending_confirmation_state_v1793122` | `2867..3080` | Causal forward confirmation state machine; untouched. | `ba22eecb5797` | `ba22eecb5797` |
| `aurum-desk/backend/services/v17_9_3_1_2_2_signal_intelligence.py` | `arbitrate_competing_model_intents_v1793122` | `3120..3196` | Deterministic static model priority arbitration; untouched. | `b718d6bddbd7` | `b718d6bddbd7` |
| `aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py` | `resolve_causal_macro_time_window` | `748..1063` | ICT Macro NY schedule resolver; untouched. | `124c7ecff8cd` | `124c7ecff8cd` |
| `aurum-desk/backend/services/v17_9_3_macro_unicorn_intelligence.py` | `evaluate_causal_unicorn_confluence` | `1475..1829` | Strict Breaker+FVG confluence authority; untouched. | `f7e2e40a5c06` | `f7e2e40a5c06` |
| `aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py` | `compute_causal_tf_atr_v17931214` | `909..941` | Causal closed-bar source-TF ATR calculator; untouched. | `532eedaaaa8f` | `532eedaaaa8f` |
| `aurum-desk/backend/services/v17_9_3_1_2_14_accounting_target_and_d4_truth_service.py` | `build_d4_full_63085_and_matched_funnel_v17931214` | `1508..1875` | Authoritative 63,085 -> 0 strict D4 funnel; untouched. | `9e86d99c2c4f` | `9e86d99c2c4f` |
| `aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py` | `evaluate_strict_true_unicorn_hypothesis` | `1847..2020` | Historical B09 strict D4 detector; untouched. | `9f9cb42e98e9` | `9f9cb42e98e9` |
| `aurum-desk/backend/services/kh_01b_entry_unicorn_research_service.py` | `evaluate_unicorn_adjacent_research_hypothesis` | `2023..2109` | Historical B10 adjacent detector isolated from D4; untouched. | `bcf74cdf5559` | `bcf74cdf5559` |
| `aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py` | `evaluate_kh01b_r1_entry_variant` | `1704..2024` | R1 E00..E06 candidate evaluator; strategy logic untouched. | `4ef71a8c830d` | `4ef71a8c830d` |
| `aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py` | `replay_kh01b_r1_frozen_variants` | `2532..2747` | R1 1-position 3-fill replay engine; untouched. | `4e3cb4695d70` | `4e3cb4695d70` |
| `aurum-desk/backend/services/kh_01b_r1_entry_edge_discovery_service.py` | `compute_kh01b_r1_edge_uncertainty_and_research_verdict` | `2303..2529` | R1 E00..E06 cost stress & uncertainty calculator; untouched. | `839afc34b48b` | `839afc34b48b` |

## 5. Platform-Safe Replay Command (Current Committed Code SHA)
```bash
python aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py --r3 --max-workers 4 --offline
```
