# AURUM DESK — KH-03.R3 | ENTRY/EXIT FAILURE DIAGNOSIS, LEGACY GUARD & BOUNDED DEV RESEARCH

- **Phân loại kỹ thuật**: **`PASS_KH03_R3_ENGINEERING_DIAGNOSIS_NO_PROVEN_EDGE_OOS_BLOCKED`**
- **Spec Version**: `KH-03.R3-v1.0` | **Run ID**: `20261123T085134Z_2301d9c64898`
- **Nhánh bắt buộc**: `feature/v17-9-2-structural-sl-tp-from-qa` | **Tested SHA**: `2301d9c648984f6b06ccb4e22f47174c3990d7af`
- **Khóa an toàn**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`

## 1. Kết luận trực giao

| Chiều kiểm định | Kết luận |
|---|---|
| `KH03_R3_ENGINEERING` | **PASS** |
| `RESEARCH_METHOD_VALIDITY` | **PASS** |
| `ENTRY_EXIT_DIAGNOSTIC_VALIDITY` | **PASS** |
| `DEV_RESEARCH_CANDIDATE` | **NO_ECONOMIC_ADVANCEMENT_CANDIDATE** |
| `RESEARCH_ECONOMIC_VERDICT` | **NO_PROVEN_EDGE** |
| `FRESH_OOS` | **BLOCKED_NO_UNTOUCHED_DATA** |
| `KH04_ENTRY` | **BLOCKED_NO_DEV_CANDIDATE** |
| `ACCOUNT_EFFECTIVE_FEE_VERDICT` | **UNKNOWN_NOT_LOCALLY_VERIFIED** |
| `LIVE_NETWORK_VALIDATION` | **NOT_RUN_BY_DESIGN** |
| `PUBLICATION` | **PRE_PUSH_PENDING** |
| `ACTIVATION_READY` | **NO** |
| `OOS_EVALUATIONS` | **0** |

## 2. Ma trận 30 cổng (`G01`..`G30`)

| Cổng | Trạng thái |
|---|---|
| `G01_BRANCH_CLEAN_EXECUTABLE_SHA` | **PASS** |
| `G02_DATASET_AND_PRIOR_ARTIFACT_IMMUTABILITY` | **PASS** |
| `G03_PRODUCTION_KEEP_AS_IS_AST_PARITY` | **PASS** |
| `G04_LEGACY_R1_BAD_METHOD_RED_REPRO` | **PASS** |
| `G05_R2_OR_NEWER_METHOD_GUARD` | **PASS** |
| `G06_FULL_OPPORTUNITY_LINEAGE` | **PASS** |
| `G07_WIN_LOSS_DENOMINATOR_TRUTH` | **PASS** |
| `G08_ENTRY_EXIT_ASOF_CAUSALITY` | **PASS** |
| `G09_FEE_AND_RISK_ACCOUNTING` | **PASS** |
| `G10_FAILURE_ATTRIBUTION_DISCLOSURE` | **PASS** |
| `G11_NEGATIVE_CONTROLS_AND_MATCHING` | **PASS** |
| `G12_BOUNDED_PREREG_OR_DIAGNOSIS_ONLY` | **PASS** |
| `G13_CANDIDATE_FILTER_CONSERVATION` | **PASS** |
| `G14_FORWARD_ONLY_TRAIN_VAL_EMBARGO` | **PASS** |
| `G15_FOLD_LOCAL_SELECTION_FROZEN` | **PASS** |
| `G16_FRESH_FOLD_REPLAY_AND_LEAKAGE_TESTS` | **PASS** |
| `G17_FILL_REALISM_H3_NOT_PROMOTED` | **PASS** |
| `G18_SIX_SCENARIO_NET_COST_STRESS` | **PASS** |
| `G19_STATISTICS_CI_HOLM_POWER` | **PASS** |
| `G20_BASELINE_A1_SI8_ADAPTER_D4_PARITY` | **PASS** |
| `G23_DISK_RAM_CHILD_PROCESS_SAFETY` | **PASS** |
| `G21_FULL_PYTEST_JUNIT_1_TO_1` | **PASS** |
| `G22_W1_W2_W4_W1REPEAT_SEMANTIC_DETERMINISM` | **PASS** |
| `G24_UNIQUE_RED_GREEN_FAULTS` | **PASS** |
| `G25_INDEPENDENT_VERIFIER_MUTATION_DEFENSE` | **PASS** |
| `G26_OS_VERIFIER_MANIFEST_OWN_RECOMPUTATION` | **PENDING_SEPARATE_OS_VERIFIER** |
| `G27_RESEARCH_ECONOMIC_VERDICT_HONESTY` | **NO_ECONOMIC_ADVANCEMENT_CANDIDATE** |
| `G28_FRESH_OOS_ZERO_AND_EXCHANGE_OFF` | **BLOCKED_NO_UNTOUCHED_DATA** |
| `G29_ACTIVATION_ALWAYS_OFF_AND_KH04_GATED` | **BLOCKED_NO_DEV_CANDIDATE** |
| `G30_CODE_AND_EVIDENCE_REMOTE_READBACK` | **PRE_PUSH_PENDING** |

## 3. Tóm tắt số đo thực tế
- **P0-A Legacy Guard (`03`, `04`)**: `LEGACY_METHOD_GUARD=PASS`, unauthorized callers=`0`, `LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY=True`.
- **P0-B Forensic Diagnosis (`05`..`10`)**: NULL 1X closed=`203` (`7W/196L`, win_rate=`0.0345`, net=`-441.6118 USD`, fees=`276.6577 USD`, expR=`-0.8806`); engine bug=`False`; attribution=`ASSOCIATION_ONLY_NO_CAUSAL_ATTRIBUTION_WITHOUT_PAIRED_CONTROL`.
- **P1-C Bounded DEV Research (`11`..`16`)**: `DEV_RESEARCH_CANDIDATE=NO_ECONOMIC_ADVANCEMENT_CANDIDATE`, `RESEARCH_ECONOMIC_VERDICT=NO_PROVEN_EDGE`, `KH04_ENTRY=BLOCKED_NO_DEV_CANDIDATE`, `OOS_EVALUATIONS=0`.
- **Baseline Parity (`17`)**: `PASS` (`A1=56710/0`, `SI8=6375/0`, `3-adapter=246/0`, `D4=0`).
- **Full Pytest (`18`)**: `PASS` — collected=`5683`, executed=`5683`, passed=`5675`, skipped=`8`, failed=`0`.
- **Faults (`21`)**: `194/194` (`174` inherited + `20` new R3). **Mutations (`22`)**: `16/16`.
- **Tài nguyên (`00`, `20`)**: Preflight `44.4784 GiB`, runtime min `44.4647 GiB`, RAM min `8173.76 MiB`, max children `4`, orphans `0`.
- **Optimum Worker Policy (`19`)**: pytest=`w=1`, science=`w=1`.

## 4. Bản đồ trách nhiệm hàm (Function Responsibility Map)

| Hàm | Module | Bất biến cốt lõi | Tests | Bằng chứng |
|---|---|---|---|---|
| `audit_kh03_r3_legacy_research_callgraph + require_kh03_r2_or_newer_research_protocol` | `services/kh_03_r3_legacy_research_guard.py` | Blocks direct/alias/re-export/eval R1 promotion in new modules; discloses LEGACY_DIRECT_CALL_REMAINS_AVAILABLE_FOR_HISTORICAL_ONLY | `test_kh03_r3_legacy_guard.py` | `01, 03, 04` |
| `build_r3_full_opportunity_lineage + audit_r3_entry_exit_geometry_and_accounting` | `services/kh_03_r3_diagnostic_service.py` | Exact eligible=scheduled+busy_skipped conservation; gross-fees=net <= 0.001 USD; 0 overlaps | `test_kh03_r3_entry_exit_diagnosis.py` | `05, 06, 07, 08` |
| `build_r3_win_loss_failure_attribution + build_r3_cost_and_break_even_sensitivity + run_r3_negative_control_and_label_integrity_tests` | `services/kh_03_r3_diagnostic_service.py` | Separates closed/filled/scheduled/censored; labels association vs causality; confirms accounting is NOT engine bug | `test_kh03_r3_entry_exit_diagnosis.py, test_kh03_r3_research_falsification.py` | `07, 08, 09, 10` |
| `freeze_r3_bounded_research_plan + build_r3_train_only_candidates_asof + run_r3_forward_only_dev_evaluation + run_r3_paired_and_strict_dev_evaluation_15_16` | `services/kh_03_r3_experiment_service.py` | Max 3 distinct arms (E1/E2/E3), H3 excluded, no OPEN_TO_OPEN, embargo>=240m, future_train=0, strict R2 CI gate, NO_PROVEN_EDGE on DEV | `test_kh03_r3_research_falsification.py, test_kh03_r3_forward_only_economics.py` | `11, 12, 13, 14, 15, 16` |
| `verify_kh03_r3_artifacts_independently_from_disk` | `tools/kh_03_r3_independent_verifier.py` | Distinct OS PID; zero import from orchestrator; independently recomputes 06 ledger, 11 SHA, 13/14 WF, 16 CI/DEV, 18 JUnit, 19 W1-W4, 20 RAM/disk, G01..G30 | `test_kh03_r3_resource_async_and_verifier_mutations.py (16 new mutations)` | `22, 23, 24` |
