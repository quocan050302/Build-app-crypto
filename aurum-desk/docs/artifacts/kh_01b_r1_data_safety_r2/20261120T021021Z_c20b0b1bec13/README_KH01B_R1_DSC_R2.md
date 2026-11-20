# AURUM DESK — KH-01B.R1.DSC.R2: STRICT DISK RECOVERY, FULL RE-VERIFICATION & EVIDENCE CLOSURE (`KH-01B.R1.DSC.R2-v1.0`)

- **Run ID**: `20261120T021021Z_c20b0b1bec13`
- **Mandatory Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **HEAD at Start (Prior DSC Blocked Publication Commit)**: `647206f3c446db202ecf152a4f8e028de56d101c`
- **Prior Blocked DSC Run Preserved Untouched**: `20261119T150709Z_0c247bdc466a` (`tested_sha = 0c247bdc466a76da2d17b2c9ccfd9a0431dcc73e`)
- **Tested Executable Source SHA (`CODE_COMMIT_SHA`)**: `c20b0b1bec1344e2da258a5d18832874f5f5668a`
- **Canonical Dataset SHA256 (`REUSED_RESEARCH`, `MODELED_BID_ASK`)**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`

## 1. Orthogonal Verdicts
- `ENGINEERING_CLOSURE_VERDICT`: **PASS**
- `DISK_SAFETY_VERDICT`: **PASS_STRICT_20GIB** (Preflight floor: `>= 20.5 GiB`; Constraining preflight free: `45.0002 GiB`; Min runtime free: `44.8338 GiB`; Post-cleanup free: `44.968 GiB`)
- `FULL_REGRESSION_VERDICT`: **PASS** (`collected = 5513`, `executed = 5513`, `passed = 5505`, `skipped = 8`, `failed = 0`, `errors = 0`)
- `BACKTEST_VERDICT`: **PASS** (`56710` A1 + `6375` SI8 = `63085` cohorts; `SI0` `242` scheduled / `166` closed = `9W/157L`, `net = -345.8971 USD`, `expectancy = -0.8474R`; `246` 3-adapter episodes `0` mismatches)
- `WORKER_DETERMINISM_VERDICT`: **PASS** (`pytest w4 vs w1 speedup = 1.386x`, `all_4_identical = True / True`)
- `DATA_ACQUISITION_VERDICT`: **VERIFIED_COMPLETE** (Old M1 snapshot preserved as `1600/2880` `VERIFIED_PARTIAL`; New R2 online capture: `VERIFIED_COMPLETE`)
- `RESEARCH_ECONOMIC_VERDICT`: **NO_PROVEN_EDGE_BASELINE_UNCHANGED_FEE_SENSITIVITY_NEGATIVE**
- `KH01C_ADVANCEMENT_VERDICT`: **NO_ECONOMIC_ADVANCEMENT_CANDIDATE**
- `FRESH_OOS_VERDICT`: **BLOCKED_NO_UNTOUCHED_DATA** (`strategy_evaluation_count = 0`)
- `ACTIVATION_READY`: **NO** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)
- `PUBLICATION_VERDICT` (pre-push bundle): **PENDING_REMOTE_PUSH**

## 2. Multi-Mount Disk Audit & Safe Sentinel Reclamation (`00`, `06`)
| Label | Mountpoint / Probe | st_dev | FS Type | Free Bytes | Free GiB | Free GB (dec) | >=20.5 GiB |
|---|---|---:|---|---:|---:|---:|---|
| `repo` | `D:\` | `134018765441740000` | `NTFS` | `48318582784` | `45.0002` | `48.3186` | `True` |
| `output` | `D:\` | `134018765441740000` | `NTFS` | `48318582784` | `45.0002` | `48.3186` | `True` |
| `temp` | `C:\` | `5779928256407799542` | `NTFS` | `194744438784` | `181.3699` | `194.7444` | `True` |
| `venv_or_python` | `C:\` | `5779928256407799542` | `NTFS` | `194744438784` | `181.3699` | `194.7444` | `True` |

- **Stale Sentinel-Owned Temp Reclamation**: `0` dead-PID dirs reclaimed (`0` bytes / `0.0` MiB); `unowned_paths_deleted_count = 0`.
- **Process & Memory Telemetry**: `max_children_observed = 4` (`<= 4`), `orphan_pids_at_cleanup_count = 0`, `parent_rss_mib = 2522.898`, `peak_child_tree_rss_mib = 0.0`.

## 3. Window Coverage & Fee Sensitivity Summary (`04`, `05`, `10`)
| Timeframe | Old Snapshot Expected | Old Snapshot Observed | Old Missing | Old Verdict | New R2 Online Observed/Expected | New R2 Online Verdict |
|---|---:|---:|---:|---|---|---|
| `1m` | `2880` | `1600` | `1280` | `PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION` | `2880/2880` | `VERIFIED_COMPLETE` |
| `5m` | `576` | `576` | `0` | `VERIFIED_COMPLETE` | `576/576` | `VERIFIED_COMPLETE` |
| `15m` | `192` | `192` | `0` | `VERIFIED_COMPLETE` | `192/192` | `VERIFIED_COMPLETE` |
| `1H` | `48` | `48` | `0` | `VERIFIED_COMPLETE` | `48/48` | `VERIFIED_COMPLETE` |
| `4H` | `12` | `12` | `0` | `VERIFIED_COMPLETE` | `12/12` | `VERIFIED_COMPLETE` |
| `1D` | `1` | `1` | `0` | `VERIFIED_COMPLETE` | `1/1` | `VERIFIED_COMPLETE` |

- **Fixed-Fill Counterfactual (`166` closed)**: `0.0004` (`gross = -142.7836`, `fees = 203.1135`, `net = -345.8971 USD`) vs `0.0006` (`fees = 304.6696`, `net = -447.4532 USD`).
- **Separate `SI0-242` Cohort Rerisked Study (NOT a full `56,710` opportunity rerun)**: `0.0004` (`166` closed, `net = -345.8971 USD`) vs `0.0006` (`87` closed, `net = -210.6112 USD`). `CostAssumptions` defaults unchanged (`0.0002 / 0.0004 / 0.10`).
- **Three Fill Modes (`SI0/E00`)**: `NEXT_BAR_OPEN_BID_ASK` net = `-345.8971`, `LEGACY_CLOSE_FILL` net = `-313.6541`, `STRESS_WIDER_SPREAD_SLIPPAGE_2X` net = `-381.7661`.
- **Adversarial RED/GREEN Fault Suite (`11`)**: `31/31` PASS.

## 4. Gate Matrix (`G-DS-01..G-DS-15` & `R2-A..R2-G`)
| Gate ID | Gate Name | Status | Observed Summary |
|---|---|---|---|
| `G-DS-01` | `BRANCH_SOURCE_SHA_IMMUTABLE_BASELINE_AND_OFF_FLAGS` | **PASS** | `{"branch": "feature/v17-9-2-structural-sl-tp-from-qa", "locked_modified": 0, "tested_sha": "c20b0b1bec1344e2da258a5d18832874f5f5668a"}` |
| `G-DS-02` | `SIX_TF_GAP_TRUTH_MAP_NON_NULL_AND_OLD_SCHEMA_RED_CONTROL` | **PASS** | `{"canonical_status": "PASS", "extended_dev_status": "PASS"}` |
| `G-DS-03` | `PAGE_BUDGET_17_CURSOR_INTEGRITY_AND_DECIMAL_CROSS_PAGE_DEDUP` | **PASS** | `{"m1_48h_page_budget": 17, "passed_controls": 9}` |
| `G-DS-04` | `REQUESTED_WINDOW_CONSERVATION_AND_HONEST_M1_PARTIAL_OR_VERIFIED_CAPTURE` | **PASS** | `{"old_m1_slots": "1600/2880", "old_verdict": "VERIFIED_PARTIAL", "online_capture_executed": true, "online_capture_verdict": "VERIFIED_COMPLETE"}` |
| `G-DS-05` | `CONTRACT_METADATA_ACCOUNT_VIP_UNKNOWN_AND_POINT_BBO_PROVENANCE` | **PASS** | `{"account_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED", "public_taker_fee": "0.0006"}` |
| `G-DS-06` | `DECIMAL_FEE_SENSITIVITY_FIXED_FILL_VS_SI0_242_RERISKED_DEFAULTS_INTACT` | **PASS** | `{"fixed_0p04_net": -345.8971, "fixed_0p06_net": -447.4532, "rerisked_0p06_closed_net": "87/-210.6112"}` |
| `G-DS-07` | `STRICT_20P5_GIB_PREFLIGHT_AND_20GIB_RESERVE_ALL_WRITING_VOLUMES` | **PASS** | `{"disk_safety_verdict": "PASS_STRICT_20GIB", "min_runtime_free_gib": 44.8338, "preflight_min_free_gib": 45.0002}` |
| `G-DS-08` | `RESOURCE_MONITOR_MAX4_DESCENDANTS_ZERO_ORPHANS_AND_SAFE_TEMP_CLEANUP` | **PASS** | `{"max_children": 4, "orphans": 0, "sentinel_cleaned": true}` |
| `G-DS-09` | `ASYNC_DETERMINISTIC_W1_W2_W4_W1REPEAT_HASHES_AND_TIMINGS` | **PASS** | `{"pytest_w4_speedup": 1.386, "speedup_label": "MEANINGFUL_PARALLEL_SPEEDUP"}` |
| `G-DS-10` | `FULL_PYTEST_COLLECTED_EXECUTED_CLOSED_JUNIT_BIJECTION` | **PASS** | `{"collected": 5513, "deselected": 0, "errors": 0, "executed": 5513, "failed": 0, "mandatory_skipped": 0, "passed": 5505, "scheduled": 5513, "skipped": 8, "unknown": 0, "xfailed": 0, "xpassed": 0}` |
| `G-DS-11` | `FULL_BACKTEST_56710_6375_SI0_242_166_THREE_FILL_AND_ADAPTER_PARITY` | **PASS** | `{"a1_evaluated": 56710, "adapters_matched": 246, "si0_closed": "166 (9W/157L)", "si8_count": 6375}` |
| `G-DS-12` | `ORIGINAL_DATASET_AND_OOS_ESCROW_UNTOUCHED_ZERO_EVALUATIONS` | **PASS** | `{"FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA", "evaluation_count": 0}` |
| `G-DS-13` | `INDEPENDENT_VERIFIER_AND_22PLUS_ADVERSARIAL_FAULT_INJECTIONS` | **PASS** | `{"passed_mutations": "31/31"}` |
| `G-DS-14` | `SHA256_MANIFESTS_CROSS_VIEW_AND_IMMUTABLE_PRIOR_ARTIFACTS` | **PASS** | `{"gds_receipts_count": 14, "modified_prior_files_count": 0}` |
| `G-DS-15` | `SAME_BRANCH_PUBLICATION_AND_REMOTE_SHA_GRAPH_READBACK` | **PENDING_REMOTE_PUSH** | `{"publication_status": "PENDING_REMOTE_PUSH", "tested_executable_sha": "c20b0b1bec1344e2da258a5d18832874f5f5668a"}` |
| `R2-A` | `TESTED_SHA_SEPARATED_FROM_PUBLICATION_HEAD_AND_UNIQUE_RUN_ID` | **PASS** | `{"head_at_start_sha": "647206f3c446db202ecf152a4f8e028de56d101c", "tested_executable_sha": "c20b0b1bec1344e2da258a5d18832874f5f5668a"}` |
| `R2-B` | `MULTI_MOUNT_STRICT_20P5_GIB_DISK_GUARD_NO_AUTO_DOWNGRADE` | **PASS** | `{"disk_verdict": "PASS_STRICT_20GIB", "distinct_filesystems": 2}` |
| `R2-C` | `ACTUAL_FULL_SUITE_EXECUTION_NOT_SUBSTITUTED_BY_FOCUSED_TESTS` | **PASS** | `{"collected": 5513, "deselected": 0, "errors": 0, "executed": 5513, "failed": 0, "mandatory_skipped": 0, "passed": 5505, "scheduled": 5513, "skipped": 8, "unknown": 0, "xfailed": 0, "xpassed": 0}` |
| `R2-D` | `ACTUAL_FULL_BACKTEST_FINGERPRINTS_AND_NO_FALSE_56710_CLAIM_FROM_242_RERISK` | **PASS** | `{"backtest_status": "PASS", "fee_status": "PASS"}` |
| `R2-E` | `PARTIAL_BITGET_COVERAGE_NEVER_PROMOTED_TO_COMPLETE_AND_ZERO_OOS_CONTAMINATION` | **PASS** | `{"g04": "PASS", "g12": "PASS"}` |
| `R2-F` | `INDEPENDENT_VERIFIER_DETECTS_FORGED_PASS_FAKE_JUNIT_2880_M1_AND_STALE_MANIFEST` | **PASS** | `{"passed_mutations": 31}` |
| `R2-G` | `SAME_MANDATORY_BRANCH_PRIOR_BLOCKED_RUN_AND_LATEST_POINTERS_INTACT` | **PASS** | `{"prior_dsc_v1_intact": true, "prior_latest_ptrs_intact": true}` |

## 5. Platform-Safe Replay Instructions (Current Committed Code SHA)
```bash
# Always derive current committed executable SHA (never pass a stale historical --tested-sha after evidence commits):
python aurum-desk/tools/run_kh_01b_r1_data_safety_closure.py --r2 --max-workers 4
```
