# AURUM DESK — KH-02 Entry Foundation Research (`KH-02.R1`)

- **Run ID:** `20261121T033338Z_b99ad30313a6`
- **Branch:** `feature/v17-9-2-structural-sl-tp-from-qa`
- **HEAD at start:** `98a6d25da94b07572decb531cd6207b91a78fdb0`
- **Tested executable SHA:** `b99ad30313a6ed5929ba4cbaeab4383bd3db6be5`
- **Canonical Dataset SHA256 (`REUSED_RESEARCH` / `MODELED_BID_ASK`):** `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **Frozen Hypothesis Manifest SHA256:** `ba0282d139b1cab88d5b62c142cb8dcd1fea4f9f9f8fc343edd2c8540c5306fc`

## 1. Orthogonal Verdicts (Non-Collapsed)

- `ENGINEERING_VERDICT`: **PASS**
- `REGRESSION_VERDICT`: **PASS**
- `RESOURCE_SAFETY_VERDICT`: **PASS**
- `BASELINE_PARITY_VERDICT`: **PASS**
- `DATA_VERDICT`: **PASS**
- `RESEARCH_ECONOMIC_VERDICT`: **NO_PROVEN_EDGE**
- `FRESH_OOS_VERDICT`: **BLOCKED_NO_UNTOUCHED_DATA** (`OOS_EVALUATIONS = 0`)
- `PUBLICATION_VERDICT` (pre-push): **PENDING_REMOTE_PUSH**
- `ACTIVATION_READY`: **NO** (`PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`)

## 2. Entry Foundation Arms, Ablations & Negative Controls (1x/2x Cost & Cluster CI)

| Arm | Role | Closed NB | Wins/Losses | WinRate | Net 1x USD | Exp 1x R | 2x All-Cost USD | 95% Cluster CI (R) | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| `ABL_H1_ONLY` | `SECONDARY/NC` | 107 | 4W/103L | 3.74% | -236.1101 | -0.8939R | -417.3748 | `[-1.017, -0.7214]` | `NO_PROVEN_EDGE` |
| `ABL_M15_ONLY` | `SECONDARY/NC` | 99 | 3W/96L | 3.03% | -222.3156 | -0.9098R | -368.4486 | `[-1.0155, -0.8235]` | `NO_PROVEN_EDGE` |
| `ABL_NO_REGIME` | `SECONDARY/NC` | 110 | 7W/103L | 6.36% | -216.7543 | -0.7992R | -403.6548 | `[-0.9342, -0.6452]` | `NO_PROVEN_EDGE` |
| `ABL_NO_SESSION` | `SECONDARY/NC` | 44 | 1W/43L | 2.27% | -102.1886 | -0.9427R | -162.9796 | `[-1.016, -0.7627]` | `NO_PROVEN_EDGE` |
| `ABL_STRUCT_ONLY` | `SECONDARY/NC` | 170 | 8W/162L | 4.71% | -357.2638 | -0.8522R | -649.2293 | `[-0.9488, -0.731]` | `NO_PROVEN_EDGE` |
| `B2_H1` | `PRIMARY` | 58 | 1W/57L | 1.72% | -137.6058 | -0.9619R | -225.5363 | `[-1.0181, -0.8214]` | `NO_PROVEN_EDGE` |
| `B2_H123` | `SECONDARY/NC` | 22 | 0W/22L | 0.00% | -55.0756 | -1.0160R | -88.3334 | `[-1.017, -1.0148]` | `NO_PROVEN_EDGE` |
| `B2_H2` | `PRIMARY` | 170 | 8W/162L | 4.71% | -357.2638 | -0.8522R | -649.2293 | `[-0.9488, -0.731]` | `NO_PROVEN_EDGE` |
| `B2_H3` | `PRIMARY` | 108 | 7W/101L | 6.48% | -211.7256 | -0.7951R | -390.6126 | `[-0.9334, -0.6277]` | `NO_PROVEN_EDGE` |
| `CONTROL_B0` | `CONTROL` | 166 | 9W/157L | 5.42% | -345.8971 | -0.8474R | -594.6226 | `[-0.9697, -0.712]` | `NO_PROVEN_EDGE` |
| `NC_NULL_TRIGGER` | `SECONDARY/NC` | 170 | 8W/162L | 4.71% | -357.2638 | -0.8522R | -649.2293 | `[-0.9488, -0.731]` | `NO_PROVEN_EDGE` |
| `NC_RANDOM_DIR` | `SECONDARY/NC` | 125 | 7W/118L | 5.60% | -252.8719 | -0.8194R | -458.5091 | `[-0.9503, -0.664]` | `NO_PROVEN_EDGE` |
| `NC_SHIFTED_REGIME` | `SECONDARY/NC` | 23 | 0W/23L | 0.00% | -57.9935 | -1.0187R | -104.0463 | `[-1.0221, -1.0163]` | `NO_PROVEN_EDGE` |

## 3. Baseline Parity, Full Regression, Determinism & Resources

- **Baseline Parity (`16`):** `A1 = 56710` (`0` mismatches), `SI8 = 6375`, `SI0 = 242/166` (`9W/157L`, Net `-345.8971 USD`, Exp `-0.8474R`), `3-adapter = 246/246` (`0` mismatches), `D4 strict = 0`.
- **Full Regression (`12`):** `collected=5524`, `executed=5524`, `passed=5516`, `skipped=8`, `failed=0`, `errors=0`.
- **Async Determinism (`13`):** pytest `w=4` speedup `1.537x` (4/4 identical hashes); KH-02 scientific slice 4/4 identical hashes.
- **RAM & Disk Safety (`14`):** min available RAM `4060.8 MiB` (floor `512.0 MiB`), peak parent RSS `2570.293 MiB`, peak child-tree RSS `987.512 MiB`, `max_children=4 (<=4)`, `orphans=0`, min free disk `44.8881 GiB` (`>=20.0 GiB`).
- **Fault Injection (`15`):** `33/33` REAL RED/GREEN PASS.

## 4. Gate Matrix (`G-KH02-01` .. `G-KH02-18`)

| Gate ID | Gate Name | Status | Artifact | Observed |
|---|---|---|---|---|
| `G-KH02-01` | `BRANCH_WORKTREE_SOURCE_FREEZE_AND_TESTED_SHA` | **PASS** | `00_preflight_git_branch_head_resource_runtime.json` | `{"branch": "feature/v17-9-2-structural-sl-tp-from-qa", "tested_sha": "b99ad30313a6ed5929ba4cbaeab4383bd3db6be5"}` |
| `G-KH02-02` | `BASELINE_IMMUTABLE_AND_AST_FINGERPRINT_UNCHANGED` | **PASS** | `01_function_callgraph_keep_as_is_diff_immutability.json` | `{"locked_modified": 0, "modified_existing_files": 0}` |
| `G-KH02-03` | `RESEARCH_ONLY_ISOLATION_AND_MODE_FLAGS_OFF` | **PASS** | `00_preflight_git_branch_head_resource_runtime.json` | `{"ACTIVATION_READY": "NO", "runtime_modes": {"AUTO": "OFF", "LIVE": "OFF", "PAPER": "OFF", "PRODUCTION": "OFF"}}` |
| `G-KH02-04` | `DATASET_ORIGIN_QUALITY_AND_MTF_COVERAGE` | **PASS** | `02_dataset_registry_lineage_m1_m15_h1_quality.json` | `{"h1_bars": 3863, "m15_bars": 15090, "m1_bars": 22376}` |
| `G-KH02-05` | `M15_H1_ASOF_CAUSALITY_AND_LEAKAGE_TESTS` | **PASS** | `04_asof_mtf_feature_provenance_and_leakage_controls.json` | `{"future_mut_mismatches": 0, "post_warmup_bars": 22346}` |
| `G-KH02-06` | `PREREGISTERED_HYPOTHESIS_FREEZE_BEFORE_OUTCOME` | **PASS** | `03_frozen_hypotheses_budget_splits_power_falsification.json` | `{"manifest_sha256": "ba0282d139b1cab8", "primary_count": 3}` |
| `G-KH02-07` | `UNIVERSAL_DENOMINATOR_MATCHED_COHORTS_ABLATIONS` | **PASS** | `07_matched_cohort_confounder_balance_ablations.json` | `{"arms_evaluated": 12, "directional_opps": 44692}` |
| `G-KH02-08` | `CHRONOLOGICAL_ONE_POSITION_REPLAY_AND_ADAPTER_PARITY` | **PASS** | `08_chronological_one_position_three_fill_replay.json` | `{"adapter_mismatches": 0, "arms_replayed": 12, "overlaps": 0}` |
| `G-KH02-09` | `RISK_CAP_NET_RR_AND_THREE_FILL_COSTS` | **PASS** | `09_fee_spread_slippage_1x_2x_3x_counterfactuals.json` | `{"risk_violations": 0, "rr_violations": 0}` |
| `G-KH02-10` | `PURGED_WALKFORWARD_UNCERTAINTY_AND_HONESTY` | **PASS** | `10_purged_walkforward_and_cluster_uncertainty.json` | `{"economic_verdict": "NO_PROVEN_EDGE", "fold_leakage": false}` |
| `G-KH02-11` | `FULL_REGRESSION_JUNIT_COLLECTION_BIJECTION` | **PASS** | `12_full_pytest_collection_junit_bijection_exit.json` | `{"collected": 5524, "errors": 0, "executed": 5524, "failed": 0, "mandatory_skipped": 0, "passed": 5516, "skipped": 8}` |
| `G-KH02-12` | `BOUNDED_ASYNC_W1_W2_W4_REPEAT_DETERMINISM` | **PASS** | `13_async_w1_w2_w4_w1repeat_hashes_speedups.json` | `{"pytest_w4_speedup": 1.537, "science_slice_identical": true}` |
| `G-KH02-13` | `STRICT_RAM_512MIB_SAFE_SCHEDULER_NO_ORPHANS` | **PASS** | `14_resource_telemetry_ram_disk_pid_cleanup.json` | `{"max_children": 4, "min_avail_ram_mib": 4060.8, "orphans": 0, "peak_child_rss_mib": 987.512, "peak_parent_rss_mib": 2570.293}` |
| `G-KH02-14` | `STRICT_DISK_RESERVE_ALL_MOUNTS_AND_SENTINEL_CLEANUP` | **PASS** | `14_resource_telemetry_ram_disk_pid_cleanup.json` | `{"min_free_disk_gib": 44.8881, "post_cleanup_gib": 44.8881, "sentinel_clean": true}` |
| `G-KH02-15` | `REAL_33_RED_GREEN_FAULTS_AND_INDEPENDENT_VERIFIER` | **PASS** | `15_red_green_fault_injection_independent_controls.json` | `{"passed_faults": "33/33"}` |
| `G-KH02-16` | `SHA256_MANIFESTS_AND_PRIOR_ARTIFACTS_INTACT` | **PASS** | `00_preflight_git_branch_head_resource_runtime.json` | `{"prior_unchanged": true}` |
| `G-KH02-17` | `OOS_UNTOUCHED_REGISTRY_AND_NO_PEEK_TRUTH` | **PASS** | `11_oos_escrow_no_overlap_no_peek_status.json` | `{"FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA", "OOS_EVALUATIONS": 0}` |
| `G-KH02-18` | `SAME_BRANCH_PUBLICATION_AND_REMOTE_VERIFY` | **PENDING_REMOTE_PUSH** | `19_git_tested_sha_evidence_publication_receipt.json` | `{"publication_status": "PENDING_REMOTE_PUSH", "tested_executable_sha": "b99ad30313a6ed5929ba4cbaeab4383bd3db6be5"}` |

## 5. Reproducible Commands

```powershell
python aurum-desk/tools/run_kh_02_entry_foundation_research.py --max-workers 4
python aurum-desk/tools/kh_02_entry_foundation_independent_verifier.py --run-dir aurum-desk/docs/artifacts/kh_02_entry_foundation/20261121T033338Z_b99ad30313a6 --expected-run-id 20261121T033338Z_b99ad30313a6 --expected-tested-sha b99ad30313a6ed5929ba4cbaeab4383bd3db6be5
```
