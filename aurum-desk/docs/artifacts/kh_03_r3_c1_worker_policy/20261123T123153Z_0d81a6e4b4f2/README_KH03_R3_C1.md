# AURUM DESK — KH-03.R3.C1 | Worker Benchmark Truth, Deterministic Async QA & Scientific Closure

- **Spec Version**: `KH-03.R3.C1-v1.0` | **Run ID**: `20261123T123153Z_0d81a6e4b4f2`
- **Nhánh Git bắt buộc**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA**: `0d81a6e4b4f2de6c0770506308f4a280ff7f186d`
- **Prior R3 Pub HEAD**: `b03db8642cdd9a1dede38ca5c9adad47cd1ced71` (R3 Run `20261123T085134Z_2301d9c64898` giữ bất biến 100%)
- **Kết luận tổng quan**: `ENGINEERING: PASS; WORKER_POLICY: PASS; DETERMINISM: PASS; ECONOMIC: NO_PROVEN_EDGE; OOS: BLOCKED_NO_UNTOUCHED_DATA; KH04: BLOCKED_NO_DEV_CANDIDATE; ACTIVATION: NO; PUBLICATION: PRE_PUSH_PENDING`
- **Phân loại toàn cục**: `PASS_KH03_R3_C1_ENGINEERING_POLICY_CORRECT_NO_PROVEN_EDGE_OOS_BLOCKED`
- **An toàn bắt buộc**: `ACTIVATION_READY=NO` | `PRODUCTION=OFF` | `LIVE=OFF` | `PAPER=OFF` | `AUTO=OFF` | `OOS_EVALUATIONS=0` | `NO_PROVEN_EDGE` | `CI_STATUS=CI_UNKNOWN_NO_ACTIONS_RUN`

## 1. Lỗi P1 đã đóng (Before -> After)

- **Nguyên nhân gốc (RED trên R3 `19_w1_w2_w4_w1repeat_hashes_timings_speedups.json`)**:
  Hàm cũ `run_kh_03_r3_acceptance._compute_optimum_worker_policy` chỉ đọc `elapsed_sec`/`wall_sec`/`duration_sec`, bỏ qua hoàn toàn hai khóa thực tế của producer (`wall_clock_seconds` cho pytest subset và `wall_seconds` cho science slice 8.000 bar) -> xuất `mean_wall_sec_by_mode={}` và mặc định sai `optimum_worker_policy="w=1"` cho cả hai benchmark.
- **Fixture R3 bất biến sau khi sửa (GREEN — `HISTORICAL_REFERENCE_ONLY`)**:
  - Pytest subset 155 test: `mean_wall_sec_by_mode={'w=1': 51.698, 'w=2': 41.123, 'w=4': 32.947}` -> `measured_fastest_mode=w=4` (`1.569x` so với W1 đầu tiên), `applied_worker_count=3` (giữ trần an toàn full-suite `<= 3`, KHÔNG ngoại suy tốc độ full-suite).
  - Science slice 8.000 bar: `mean_wall_sec_by_mode={'w=1': 2.069, 'w=2': 2.181, 'w=4': 2.277}` -> `measured_fastest_mode=w=1`, `applied_worker_count=1`.
- **Chạy thực tươi KH-03.R3.C1 trên SHA `0d81a6e4b4f2`**:
  - Pytest subset (155 test): `mean_wall_sec_by_mode={'w=1': 26.887, 'w=2': 18.346, 'w=4': 14.119}`, `w=1_repeat=25.064s` (variance `0.0727`), `measured_fastest_mode=w=4`, `safe_worker_cap=3`, `applied_worker_count=3`, `speedup_w4_vs_w1=1.904x`, `full_suite_speedup_extrapolated=False`.
  - Science slice (8.000 bar): `mean_wall_sec_by_mode={'w=1': 1.332, 'w=2': 1.084, 'w=4': 1.143}`, `w=1_repeat=1.185s` (variance `0.1241`), `measured_fastest_mode=w=2`, `safe_worker_cap=4`, `applied_worker_count=2`, `speedup_w4_vs_w1=1.165x`.

## 2. Hồi quy kiểm thử, Parity & Kinh tế học trung thực (Read-only DEV)

- **Full Repo Pytest (JUnit 1:1 tươi)**: `5743` collected / `5743` executed / `5741` passed / `2` skipped (`mandatory_skipped=0`) / `0` failed / `0` error (`max_workers=3`).
- **Baseline Parity**: A1 `56710/0`, SI8 `6375/0`, 3-Adapter `246/0`, D4 closed `0`.
- **Kinh tế học lịch sử DEV (1x — KHỚP 100% KH-03.R3, KHÔNG có alpha mới)**:
  - `NULL`: `203` closed (7W/196L), net `-441.6118` USD, expectancy `-0.8806R`.
  - `E1`: `58` closed (3W/55L), net `-118.0121` USD, expectancy `-0.8286R`.
  - `E2`: `131` closed (5W/126L), net `-274.3771` USD, expectancy `-0.8498R`.
  - `E3`: `206` closed (9W/197L), net `-439.0423` USD, expectancy `-0.8628R`.
  - `ACCOUNT_EFFECTIVE_FEE_VERDICT=UNKNOWN_NOT_LOCALLY_VERIFIED` | `observed_l1_bbo_available=False` | `H3_RESIZE_EXECUTION_STATUS=BLOCKED_ADVISORY_FILTER_ONLY`.
- **Fault Controls & Mutation Defense**: `208/208` (194 kế thừa R3 + 14 mới C1) | Verifier Mutations: `17/17` mới C1 + `16/16` kế thừa R3.
- **Tài nguyên & An toàn tiến trình**: min disk `33.2401 GiB` (`>=20.0`), min RAM `2467.56 MiB` (`>=512.0`), max child `4` (`<=4`), orphans `0`, waiver `False`.

## 3. Ma trận 28 cổng (G01..G28)

- `G01_BRANCH_CLEAN_EXECUTABLE_SHA`: **PASS**
- `G02_PRIOR_R1_R2_R3_AND_DATASET_IMMUTABLE`: **PASS**
- `G03_LOCKED_PROD_ENTRY_SL_TP_AST_PARITY`: **PASS**
- `G04_R3_EMPTY_MEAN_MAP_RED_REPRODUCED`: **PASS**
- `G05_STRICT_SCHEMA_PARSER_BOUNDARY_GREEN`: **PASS**
- `G06_R3_PYTEST_FIXTURE_SELECTS_W4`: **PASS**
- `G07_R3_SCIENCE_FIXTURE_SELECTS_W1`: **PASS**
- `G13_RESOURCE_CEILINGS_DISK_RAM_WORKERS_FAIL_CLOSED`: **PASS**
- `G08_EXPLICIT_BENCHMARK_IDENTITY_FOUR_RAW_RUNS`: **PASS**
- `G09_FRESH_W1_W2_W4_W1REPEAT_SEMANTIC_IDENTICAL`: **PASS**
- `G10_W1_REPEAT_TIMING_VARIANCE_DISCLOSED`: **PASS**
- `G11_MEASURED_FASTEST_VS_APPLIED_SAFE_CAP_SEPARATED`: **PASS**
- `G12_POLICY_INDEPENDENTLY_RECOMPUTED_NONEMPTY_MEANS`: **PASS**
- `G14_FULL_PYTEST_FRESH_JUNIT_1_TO_1_ZERO_MANDATORY_SKIP`: **PASS**
- `G15_FULL_A1_SI8_ADAPTER_D4_PARITY_ZERO_MISMATCH`: **PASS**
- `G16_BASELINE_1X_AND_E1_E2_E3_ECONOMIC_REGRESSION`: **PASS**
- `G17_SIX_COST_DYNAMIC_VS_FIXED_FILL_SCOPED`: **PASS**
- `G18_LEGACY_R1_GUARD_R2_FWD_ONLY_H3_PATH_B_INTACT`: **PASS**
- `G19_FRESH_OOS_ZERO_NO_L1_FORGERY_ACTIVATION_OFF`: **PASS**
- `G20_INHERITED_R3_FAULTS_RED_GREEN_VALID`: **PASS**
- `G21_NEW_C1_WORKER_POLICY_FAULTS_12_PLUS_RED_GREEN`: **PASS**
- `G22_NEW_C1_INDEPENDENT_VERIFIER_MUTATIONS_12_PLUS`: **PASS**
- `G23_INDEPENDENT_VERIFIER_DISTINCT_OS_PID_NO_TRUST`: **PENDING_SEPARATE_OS_VERIFIER**
- `G24_ZERO_ORPHANS_CLEAN_TEMP_ATOMIC_CHECKSUMS`: **PASS**
- `G25_PERFORMANCE_CLAIMS_MEASURED_SCOPE_NO_EXTRAPOLATION`: **PASS**
- `G26_RESEARCH_ECONOMIC_NO_PROVEN_EDGE_HONESTY`: **NO_PROVEN_EDGE_NO_DEV_CANDIDATE**
- `G27_KH04_BLOCKED_AND_ACTIVATION_READY_NO`: **BLOCKED_NO_DEV_CANDIDATE**
- `G28_TWO_PHASE_COMMIT_PUSH_REMOTE_READBACK`: **PRE_PUSH_PENDING**
