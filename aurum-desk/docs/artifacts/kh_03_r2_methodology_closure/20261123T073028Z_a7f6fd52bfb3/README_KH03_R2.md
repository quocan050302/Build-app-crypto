# AURUM DESK — KH-03.R2 | RESEARCH METHODOLOGY CLOSURE

- **Phân loại kỹ thuật**: **`PASS_KH03_R2_METHODOLOGY_ENGINEERING_RESEARCH_NO_PROVEN_EDGE_OOS_BLOCKED`**
- **Spec Version**: `KH-03.R2-v1.0` | **Run ID**: `20261123T073028Z_a7f6fd52bfb3`
- **Nhánh bắt buộc**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA**: `a7f6fd52bfb39a3c771a1d3dc3853f1dbdc75a4f` (`git_clean_at_start=True`)
- **Khóa an toàn**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`
- **Chế độ H3**: `PATH_B_FILTER_ONLY_RESEARCH_ADVISORY` (`H3_RESIZE_EXECUTION_STATUS=NOT_VERIFIABLE_WITH_M1`, `H3_FILTER_ONLY_RESEARCH=PASS`, `size_applied_to_execution=False`)

## 1. Kết luận trực giao

| Chiều kiểm định | Kết luận |
|---|---|
| `ENGINEERING` | **PASS** |
| `RESEARCH_METHOD_VALIDITY` | **PASS** |
| `CI_GATE_CORRECTNESS` | **PASS** |
| `H3_POLICY_TRUTH` | **PASS_PATH_B_FILTER_ONLY_ADVISORY** |
| `H3_RESIZE_EXECUTION_STATUS` | **NOT_VERIFIABLE_WITH_M1** |
| `WALKFORWARD_VALIDITY` | **PASS** |
| `PARITY` | **PASS** |
| `REGRESSION` | **PASS** |
| `DETERMINISM` | **PASS** |
| `RESOURCE` | **PASS** |
| `PROVENANCE` | **PASS** |
| `ECONOMIC_DEV` | **NO_ECONOMIC_ADVANCEMENT_CANDIDATE** |
| `RESEARCH_ECONOMIC_VERDICT` | **NO_PROVEN_EDGE** |
| `FRESH_OOS` | **BLOCKED_NO_UNTOUCHED_DATA** |
| `KH04_ENTRY` | **BLOCKED_NO_DEV_CANDIDATE** |
| `ACCOUNT_EFFECTIVE_FEE_VERDICT` | **UNKNOWN_NOT_LOCALLY_VERIFIED** |
| `LIVE_NETWORK_VALIDATION` | **NOT_RUN_BY_DESIGN** |
| `PUBLICATION` | **PRE_PUSH_PENDING** |
| `ACTIVATION` | **NO** |

## 2. Ma trận 28 cổng (`G01`..`G28`)

| Cổng | Trạng thái |
|---|---|
| `G01_BRANCH_ORIGIN_CLEAN_TESTED_SHA` | **PASS** |
| `G02_IMMUTABLE_PRODUCTION_DATASET_PRIOR_EVIDENCE` | **PASS** |
| `G03_CI_RED_REPRODUCTION_REAL` | **PASS** |
| `G04_CI_STRICT_SCHEMA_AND_SEMANTIC_RED_GREEN` | **PASS** |
| `G05_CI_POSITIVE_NEGATIVE_AND_BOUNDARY_FALSIFICATION` | **PASS** |
| `G06_R1_H3_RESIZE_DISCARD_RED_REPRODUCTION` | **PASS** |
| `G07_H3_POLICY_CHOICE_FREEZE_AND_TRUTHFUL_CLASSIFICATION` | **PASS** |
| `G08_H3_EXECUTION_CAUSALITY_OR_FILTER_ONLY_EXCLUSION` | **PASS** |
| `G09_H3_QUANTITY_FEE_RISK_AND_TIME_RECONCILIATION` | **PASS** |
| `G10_WF_R1_POSTHOC_SLICE_RED_REPRODUCTION` | **PASS** |
| `G11_TRUE_FORWARD_ONLY_FOLDS_NO_FUTURE_TRAIN` | **PASS** |
| `G12_PURGE_EMBARGO_HORIZON_NO_EPISODE_LEAK` | **PASS** |
| `G13_FOLD_LOCAL_POLICY_FREEZE_AND_VALIDATION_REPLAY` | **PASS** |
| `G14_PREREG_BUDGET_AND_HOLM_P_VALUE_NOT_RELAXED` | **PASS** |
| `G15_NEGATIVE_CONTROLS_MATCHING_INTEGRITY` | **PASS** |
| `G16_SIX_COST_RERISK_FEES_RISK_TAIL_PARITY` | **PASS** |
| `G17_FULL_BASELINE_A1_SI8_ADAPTER_D4_PARITY` | **PASS** |
| `G18_PROVENANCE_C4_AND_OOS_NO_PEEK` | **PASS** |
| `G21_STRICT_DISK_RAM_PID_WINDOWS_CLEANUP` | **PASS** |
| `G19_FULL_REPO_PYTEST_AND_JUNIT_BIJECTION` | **PASS** |
| `G20_ASYNC_W1_W2_W4_W1REPEAT_SEMANTIC_HASH_EQUAL` | **PASS** |
| `G22_NEW_R2_FAULTS_PLUS_INHERITED_RED_GREEN` | **PASS** |
| `G24_MUTATION_DETECTION_AT_LEAST_16_NEW` | **PASS** |
| `G23_INDEPENDENT_VERIFIER_SECOND_OS` | **PENDING_SEPARATE_OS_VERIFIER** |
| `G25_RESEARCH_ECONOMIC_AND_WIN_LOSS_TRUTH` | **NO_ECONOMIC_ADVANCEMENT_CANDIDATE** |
| `G27_PHASE1_CODE_PUSH_THEN_PHASE2_EVIDENCE_AND_READBACK` | **PRE_PUSH_PENDING** |
| `G28_ALWAYS_OFF_ACTIVATION_AND_NO_NETWORK_CAPTURE` | **PASS** |
| `G26_KH04_ENTRY_DEPENDENCY_NOT_FORCED` | **BLOCKED_NO_DEV_CANDIDATE** |

## 3. Tóm tắt 3 đóng lỗi phương pháp & kiểm định thực tế
- **P0-SCI-01 (CI Gate)**: R1 bug tái lập 3/3 arms (`PASS`), strict boundary matrix `14/14`, strictly negative CI phân loại `CI_VALID_NEGATIVE` (không còn `CROSSES_ZERO:ci_low=None`).
- **P0-SCI-02 (H3 Policy Truth)**: R1 bỏ `final_qty` trên `156` lệnh replayed (`881` đề xuất resize / 1,323 cơ hội). Chọn trung thực **`PATH_B_FILTER_ONLY_RESEARCH_ADVISORY`**: `H3_RESIZE_EXECUTION_STATUS=NOT_VERIFIABLE_WITH_M1`, `size_applied_to_execution_any_row=False`, H3 bị loại khỏi `EXECUTABLE_EDGE_CANDIDATE`.
- **P0-SCI-03 (True Forward-Only DEV WF)**: `3` expanding folds (`train_end < val_start`, embargo `>= 240 min`, `future_train_bars = 0`, `is_fresh_oos = False`), future +100R injection invariance = `True`.
- **Baseline Parity (`17`)**: `PASS` (`A1=56710/0`, `SI8=6375/0`, `3-adapter=246/0`, `D4=0`).
- **Full Pytest (`18`)**: `PASS` — collected=`5646`, executed=`5646`, passed=`5638`, skipped=`8`, mandatory_skipped=`0`, failed=`0`.
- **Faults (`21`)**: `174/174` (`150` inherited + `24` new R2). **Mutations (`22`)**: `16/16`.
- **Tài nguyên (`00`, `20`)**: Preflight `44.5703 GiB`, runtime min `44.5567 GiB`, RAM min `3581.23 MiB`, max children `4`, orphans `0`.
- **Kinh tế & KH-04 (`23`)**: `KH03_R2_DEV_CANDIDATE_VERDICT=NO_ECONOMIC_ADVANCEMENT_CANDIDATE`, `RESEARCH_ECONOMIC_VERDICT=NO_PROVEN_EDGE`, `KH04_ENTRY=BLOCKED_NO_DEV_CANDIDATE`, `ACTIVATION_READY=NO`.

## 4. Bản đồ trách nhiệm hàm (Function Responsibility Map)

| Hàm | Module | Bất biến cốt lõi | Tests | Bằng chứng |
|---|---|---|---|---|
| `parse_kh03_cluster_ci_strict` | `services/kh_03_r2_methodology_service.py` | Rejects swapped/NaN/Inf/bool/str/arbitrary dict; strictly negative -> CI_VALID_NEGATIVE (never CROSSES_ZERO); exact zero -> CI_TOUCHES_ZERO | `test_kh03_r2_ci_schema_and_gate.py` | `03, 04, 05, 14` |
| `evaluate_kh03_dev_ci_and_advancement` | `services/kh_03_r2_methodology_service.py` | Never trusts self-reported boolean; disqualifies non-executable PATH B H3; requires all independent gates for DEV_CANDIDATE_UNPROVEN_OOS | `test_kh03_r2_ci_schema_and_gate.py, test_kh03_r2_verifier_mutations.py` | `05, 14, 23, 25` |
| `build_kh03_r2_h3_advisory_ledger_and_disclosure` | `services/kh_03_r2_methodology_service.py` | PATH B enforces size_applied_to_execution=False, assumed_tradeable_at_ms=None, Decimal fee reconciliation residual <= 0.001, H3 disqualified from executable candidate | `test_kh03_r2_h3_policy_truth.py, test_kh03_r2_verifier_mutations.py` | `06, 07, 08, 09` |
| `build_kh03_r2_forward_only_dev_folds + validate_kh03_r2_fold_causality` | `services/kh_03_r2_methodology_service.py` | Zero future train bars; gap >= 240m; label exit <= embargo_start; is_fresh_oos=False; returns WALKFORWARD_INSUFFICIENT_HISTORY_OR_POWER when short | `test_kh03_r2_forward_only_temporal_folds.py` | `10, 11, 12` |
| `run_kh03_r2_forward_only_walkforward` | `services/kh_03_r2_methodology_service.py` | Fresh bounded replay per fold truncated at val_end; future +100R injection invariant; zero duplicate cross-fold episodes | `test_kh03_r2_forward_only_temporal_folds.py, test_kh03_r2_verifier_mutations.py` | `11, 12, 13, 14` |
| `verify_kh03_r2_artifacts_independently_from_disk` | `tools/kh_03_r2_independent_verifier.py` | Distinct OS PID; recomputes CI, H3 ledger, WF causality, 6-cost fees, JUnit bijection, W1-W4, RAM/disk, G01..G28 directly from raw disk files | `test_kh03_r2_verifier_mutations.py (16 new mutations)` | `22, 24, 25` |

## 5. Lệnh tái chạy kiểm chứng độc lập
```bash
python aurum-desk/tools/run_kh_03_r2_acceptance.py
python aurum-desk/tools/kh_03_r2_independent_verifier.py --run-dir aurum-desk/docs/artifacts/kh_03_r2_methodology_closure/20261123T073028Z_a7f6fd52bfb3 --expected-run-id 20261123T073028Z_a7f6fd52bfb3 --expected-tested-sha a7f6fd52bfb39a3c771a1d3dc3853f1dbdc75a4f --no-write-manifest
```
