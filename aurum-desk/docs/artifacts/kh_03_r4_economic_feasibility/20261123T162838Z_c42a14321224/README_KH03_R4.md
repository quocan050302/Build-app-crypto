# AURUM DESK — KH-03.R4 | Economic Feasibility, Entry/Exit Root-Cause Evidence & Data Readiness

- **Spec Version**: `KH-03.R4-v1.0` | **Run ID**: `20261123T162838Z_c42a14321224`
- **Nhánh Git bắt buộc**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA**: `c42a14321224d3a143cb56ac15df5f5f228dbac6`
- **Prior KH-03.R3.C2 Pub HEAD**: `2eb271f624504b5cb8617eb2039d2dccd9ea46de` (C2 Run `20261123T133101Z_06c02e626b99` bất biến 100%)
- **Kết luận trực giao**: `ENGINEERING=PASS; DIAGNOSIS=PASS; ECONOMIC=NO_PROVEN_EDGE; DATA_READINESS=INCOMPLETE; FRESH_OOS=BLOCKED_NO_UNTOUCHED_DATA; KH04_ENTRY=BLOCKED_NO_DEV_CANDIDATE; ACTIVATION_READY=NO; CI_STATUS=CI_UNKNOWN_NO_ACTIONS_RUN; PUBLICATION=PRE_PUSH_PENDING`
- **Khóa an toàn cố định**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`

## 1. Cầu nối hạch toán Decimal & Bảo toàn dân số (`NC_NULL_TRIGGER`, 1x, REUSED DEV)
- **Bảo toàn tầng**: `22376` M1 bars -> `44692` cơ hội có hướng -> `3788` raw triggers -> `1323` eligible (`233` scheduled + `1090` skipped busy 1-pos).
- **Trạng thái cuối**: `233` filled / `0` unfilled -> `203` closed (`7W/196L`), `28` open-at-horizon (censored 180m), `2` data-gap unresolved.
- **Tách mẫu số Win-Rate**: chính thức trên lệnh đã đóng `7/203 = 3.45%` (khác hoàn toàn `7/233 = 3.00%` trên lệnh đã khớp/lên lịch).
- **Kinh tế học Decimal chính xác**: Gross `-164.9541 USD`, Closed Fees `276.6577 USD` (Entry `138.3223` + Exit `138.3354`), Net `-441.6118 USD`, Residual `0.0 USD`, Expectancy `-0.8806R`, NY-day 95% CI `[-0.9761, -0.7712]` (`17` NY days).
- **Tách phí lệnh chưa đóng**: `14.7151 USD` (28 lệnh open) + `1.608 USD` (2 lệnh data-gap) = `16.3231 USD` — tách hoàn toàn khỏi closed fees `276.6577 USD`.
- **Counterfactual giảm phí về 0 (NON-EXECUTABLE)**: Ngay cả khi ép phí về `0` trên đúng 203 lệnh đã khớp (`COUNTERFACTUAL_ZERO_FEE_DESCRIPTIVE`), Net vẫn âm **`-164.9541 USD`** vì Gross đã âm sẵn `-164.9541 USD`. Giảm phí đơn thuần KHÔNG thể biến baseline hiện tại thành có lãi.
- **So sánh các nhánh (INCOMPARABLE_UNMATCHED_POPULATIONS)**: `E1` (`58` lệnh, `-118.0121 USD`, `-0.8286R`), `E2` (`131` lệnh, `-274.3771 USD`, `-0.8498R`), `E3` (`206` lệnh, `-439.0423 USD`, `-0.8628R`) — tất cả CI 95% âm nghiêm ngặt, cấm xếp hạng theo tổng lỗ USD.
- **Break-even 1x bảo thủ**: Win-rate quan sát `0.0345` thấp hơn ~`7.46x` so với break-even thực nghiệm `0.2575`.

## 2. Chẩn đoán 3 trục (KHÔNG phát biểu nhân quả giả)
- **ENGINE / ACCOUNTING AXIS**: `NOT_FOUND_IN_TESTED_SCOPE` (Decimal residual `0.0000 USD`, không chứng minh "không có bug ở mọi nơi").
- **STRATEGY & COST AXIS**: `ASSOCIATION_ONLY_NO_CAUSAL_CLAIM` (`NO_CONFIRMED_CAUSAL_ROOT_FIX`, không tạo E4/E5, không tối ưu hóa SL/TP). Lưu ý: `$2.50` là ước tính planned risk, KHÔNG phải cam kết max-loss vật lý (`139/196` lệnh vượt `$2.50`, max `2.5574 USD`; `86/196` lệnh từng đạt MFE `>=1x SL` trong 20 nến — chỉ là association).
- **DATA / EXECUTION AXIS**: `INCOMPLETE_OBSERVED_EXECUTION_DATA` (`observed_l1_bbo_available=False`, `ACCOUNT_EFFECTIVE_FEE_VERDICT=UNKNOWN_NOT_LOCALLY_VERIFIED`, `funding=EXCLUDED_UNMODELED`, `FRESH_OOS=BLOCKED_NO_UNTOUCHED_DATA`).

## 3. Kiểm thử & Tài nguyên đo tươi KH-03.R4
- **Full Repo Pytest JUnit 1:1**: `5848` collected / `5848` executed / `5846` passed / `2` skipped (`mandatory_skipped=0`) / `0` failed / `0` error (`max_workers=3`).
- **Baseline Parity**: A1 `56710/0`, SI8 `6375/0`, Adapter `246/0`, D4 `0`.
- **Benchmark W1/W2/W4/W1_repeat**:
  - Pytest subset (`155`): `means={'w=1': 29.556, 'w=2': 18.631, 'w=4': 14.305}`, `w=1_repeat=28.693s` (var `0.0301`), `fastest=w=4`, `applied=3` (`safe_cap=3`), `full_suite_speedup_extrapolated=False`.
  - Science slice (`8,000` bars): `means={'w=1': 1.296, 'w=2': 1.011, 'w=4': 1.173}`, `w=1_repeat=1.078s` (var `0.2022`), `fastest=w=2`, `applied=2`.
  - Forensic chronological 1-position replay: `NOT_PARALLELIZABLE_SAFELY_SEQUENTIAL_W1_ONLY`.
- **Faults & Mutations**: `240/240` (`224` kế thừa + `16` mới R4) | Separate-OS Verifier Mutations: `18/18` mới R4 + `51/51` kế thừa.
- **Tài nguyên**: min disk `23.8267 GiB`, min RAM `2304.23 MiB`, peak RSS `2225.5 MiB`, max child `4`, orphans `0`, waiver `False`.

## 4. Ma trận 30 cổng (`G01..G30`)
- `G01_BRANCH_HEAD_CLEAN_SHA`: **PASS**
- `G02_IMMUTABLE_DATASET_AND_PRIOR_SEALED_SHA`: **PASS**
- `G03_PRODUCTION_AST_KEEP_AS_IS`: **PASS**
- `G04_OPPORTUNITY_ID_CONSERVATION`: **PASS**
- `G05_CLOSED_FILLED_SCHEDULED_DENOMINATORS`: **PASS**
- `G06_CENSOR_GAP_STATUS`: **PASS**
- `G07_DECIMAL_GROSS_FEE_NET_1X`: **PASS**
- `G08_OPEN_FEE_SEPARATION`: **PASS**
- `G09_SIX_COST_SCENARIO_ACCOUNTING`: **PASS**
- `G10_FIXED_FILL_VS_DYNAMIC_SEPARATED`: **PASS**
- `G11_E1_E2_E3_NO_POPULATION_CONFLATION`: **PASS**
- `G12_REALIZED_GT_PLANNED_RISK_DISCLOSED`: **PASS**
- `G13_ASSOCIATION_VS_CAUSAL_LABELS`: **PASS**
- `G14_NEGATIVE_CONTROL_AND_PLACEBO`: **PASS**
- `G15_R2_FORWARD_ONLY_DEV_EMBARGO`: **PASS**
- `G16_NO_LOOKAHEAD_FUTURE_MUTATION`: **PASS**
- `G17_DATA_READINESS_REGISTER_TRUE_UNKNOWN`: **PASS**
- `G18_NO_FABRICATED_ACCOUNT_FEES_L1_FUNDING`: **PASS**
- `G19_ZERO_FRESH_OOS_AND_NO_NEW_ALPHA`: **PASS**
- `G20_NO_MARKET_ORDER_ALL_ACTIVATION_OFF`: **PASS**
- `G21_FULL_REPO_JUNIT_EXACT_1_TO_1`: **PASS**
- `G22_A1_SI8_ADAPTER_D4_PARITY`: **PASS**
- `G23_ASYNC_SHARD_W1_W2_W4_SEMANTIC_SHA`: **PASS**
- `G24_MEASURED_SPEEDUP_AND_SAFE_WORKER_CAP`: **PASS**
- `G25_DISK_RAM_TEMP_ORPHAN_LOCK`: **PASS**
- `G26_INHERITED_AND_NEW_FAULT_CONTROLS`: **PASS**
- `G27_INDEPENDENT_VERIFIER_MUTATIONS`: **PASS**
- `G28_DISTINCT_PID_INDEPENDENT_VERIFIER`: **PENDING_SEPARATE_OS_VERIFIER**
- `G29_ECONOMIC_VERDICT_NO_GO_EVIDENCE_CONSISTENT`: **NO_PROVEN_EDGE_NO_GO_DIAGNOSIS_CONSISTENT**
- `G30_TWO_PHASE_GIT_REMOTE_VERIFIED_PUBLICATION`: **PRE_PUSH_PENDING**
