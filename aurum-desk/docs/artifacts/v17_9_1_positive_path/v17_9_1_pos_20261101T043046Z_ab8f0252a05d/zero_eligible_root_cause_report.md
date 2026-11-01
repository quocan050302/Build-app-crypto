# BÁO CÁO ĐIỀU TRA CĂN NGUYÊN GỐC (ROOT-CAUSE ANALYSIS): `349 VALID SETUPS -> 0 ELIGIBLE TRADES` (V17.9.1.1)

## 1. Kết luận Điều tra Căn nguyên (`ROOT_CAUSE_CLASSIFICATION`)
- **Mã kết luận**: `ZERO_ELIGIBLE_IN_SPARSE_SAMPLE_EXPLAINED_AND_POSITIVE_PATH_ESTABLISHED_IN_FULL_SCAN`
- **Phân loại Bug vs Ràng buộc Chiến thuật**: `NO_BUG_FOUND_STRATEGY_CONSTRAINT`
- **Chính sách Gate Rủi ro (`min_net_rr = 2.0`, phí, spread, SL/TP)**: **GIỮ NGUYÊN 100% (KHÔNG NỚI GATE)**

### Tóm tắt 2 Tầng Nguyên nhân đã Chứng minh Toán học & Thực nghiệm
1. **Nguyên nhân Cấu trúc Thượng nguồn (`99.836%` valid setups bị chặn bởi `NET_RR_TOO_LOW` trên toàn bộ `22,346` điểm M1)**:
   - Ở mức giá `XAUUSDT ~ $4,140`, công thức `calculate_risk_reward` (`backend/domain_calculator.py:380-462`) tính phí Taker hai chiều (`0.04%` entry + `0.04%` exit $\approx \$3.32/\text{oz}$ vì `tp_is_maker = False`) cộng trượt giá (`$0.10` entry + `$0.10` SL) và `spread = $0.20`.
   - Ma sát phần thưởng trên mỗi ounce là **`~$3.41/oz`** và ma sát rủi ro là **`~$3.51/oz`**.
   - Do đó, để đạt `min_net_rr = 2.0`, khoảng cách chốt lời tối thiểu phải thỏa mãn:
     $$\text{target\_distance} \ge 2.0 \times \text{stop\_distance} + \$10.44$$
   - Với `stop_distance` cấu trúc điển hình `$2.50 - $6.00`, `target_distance` bắt buộc phải đạt **`$15.50 - $22.50`**.
   - Trong khi đó, `resolve_executable_target` (`backend/services/scalp_m1_model_service.py:281-289`) gọi `select_unconsumed_target` (`backend/services/smc_liquidity_service.py:313-320`) luôn chọn **đỉnh/đáy M1/M5 gần nhất** (`min(price)` cho LONG, `max(price)` cho SHORT) vượt qua `max(1.0 * atr_m1, 0.50)` (`~$1.50 - $3.50`).
   - Hệ quả: khi mục tiêu thanh khoản gần nhất nằm trong khoảng `$1.50 - $3.42`, phí + trượt giá (`~$3.42/oz`) lớn hơn cả biên độ lợi nhuận gộp (`target_distance`), làm **`net_reward < 0` (`net_rr < 0`)**!
2. **Nguyên nhân Lấy mẫu Thưa trong V17.9.1 (`121` checkpoints đều nhau trên `22,346` nến M1)**:
   - Khi quét **toàn bộ `22,346` điểm M1 lịch sử** (`111,730` quyết định canonical trên 5 model), hệ thống thực tế có **`106` quyết định `ELIGIBLE` hợp lệ** (tại `32` thời điểm M1 độc lập, với `net_rr` từ `2.01` đến `4.33`), xảy ra khi giá vừa phá vỡ qua các swing M1 gần và hướng tới pool thanh khoản M5 ở khoảng cách `$26 - $33`!
   - Vì `32` thời điểm M1 này chỉ chiếm `0.143%` tổng số nến M1, lưới lấy mẫu thưa `121` điểm của V17.9.1 (`stride = 184` phút) đã bước nhảy qua toàn bộ `32` thời điểm dương tính này, dẫn đến `0` eligible trades trong mẫu 121 điểm.

---

## 2. Đối khớp Quyết định Duy nhất (Unique Decisions) vs Lý do Từ chối Chồng lấp (Overlapping Occurrences)

### 2.1. Mẫu 121 Checkpoints của V17.9.1 (`605` Unique Decisions)
- **Tổng số quyết định duy nhất (`unique_decision_count`)**: `605`
- **Số setup hợp lệ (`valid_setup_count`)**: `349`
- **Số quyết định `ELIGIBLE` (`canonical_executable_eligible_count`)**: `0`
- **Đối khớp tổng (`sum_first_blocking + eligible == unique_decisions`)**: `605 + 0 == 605` (`True`)

| Mã lý do từ chối | Funnel Stage | Tổng xuất hiện (Overlapping) | Số Decision Unique | Chặn đầu tiên (`FIRST_BLOCKING`) | Duy nhất (`EXCLUSIVE`) | Chồng lấp (`OVERLAP_ONLY`) |
|---|---|---:|---:|---:|---:|---:|
| `NET_RR_TOO_LOW` | `STAGE_05_RISK_REWARD_AND_COST_GATE` | 302 | 302 | 296 | 296 | 6 |
| `WAITING_SWEEP_RECLAIM` | `STAGE_02_LTF_SETUP_EVIDENCE` | 138 | 138 | 138 | 138 | 0 |
| `WAITING_M1_BOS` | `STAGE_02_LTF_SETUP_EVIDENCE` | 94 | 94 | 94 | 94 | 0 |
| `SHADOW_REJECTED_NO_VALID_TARGET` | `STAGE_03_LIQUIDITY_TARGET_SELECTION` | 42 | 42 | 41 | 41 | 1 |
| `NO_VALID_FVG_SETUP` | `STAGE_02_LTF_SETUP_EVIDENCE` | 24 | 24 | 24 | 24 | 0 |
| `STRUCTURAL_ANCHOR_NOT_FOUND` | `STAGE_04_STRUCTURAL_STOP_ANCHOR` | 6 | 6 | 6 | 0 | 6 |
| `ATR_MISSING` | `STAGE_01_DATA_AND_ATR_AUTHORITY` | 5 | 5 | 5 | 0 | 5 |
| `NON_CONTIGUOUS_BARS` | `STAGE_01_DATA_AND_ATR_AUTHORITY` | 5 | 5 | 0 | 0 | 5 |
| `ANCHOR_ALREADY_INVALIDATED` | `STAGE_04_STRUCTURAL_STOP_ANCHOR` | 1 | 1 | 1 | 0 | 1 |

### 2.2. Tập Chronological Mở rộng Stage-2 (`365` Checkpoints = `1,825` Unique Decisions, bao gồm 100% Candidate Dương tính + 121 Grid + 120 Control)
- **Tổng số nến M1 đã quét Stage-1 (`stage1_scanned_checkpoints`)**: `22346` (`coverage_ratio = 1.0`)
- **Tổng số quyết định duy nhất đánh giá đầy đủ Stage-2**: `2455`
- **Số setup hợp lệ (`valid_setup_count`)**: `1382`
- **Số quyết định `ELIGIBLE` thực tế (`canonical_executable_eligible_count`)**: **`106`** (trên `32` thời điểm M1)
- **Số lệnh đóng đã phân giải (`forward_resolved_trade_count`)**: **`106`**

---

## 3. Phân tích Counterfactual (`NON_EXECUTABLE_RESEARCH_ONLY`)
- *Lưu ý: Tất cả số liệu dưới đây chỉ phục vụ chẩn đoán nghiên cứu cho V17.9.2, tuyệt đối không tính vào lệnh thực thi hay win-rate.*
- **Số quyết định `ELIGIBLE` nếu xét hướng ngược lại (`opposite_direction_eligible_count`)**: `7`
- **Số valid setups trong Stage-2 vượt ngưỡng nếu chọn Pool thanh khoản xa nhất (`cf_farthest_pool_passes_2_0`)**: `390`
- **Số valid setups trong Stage-2 vượt ngưỡng nếu `cost = 0` (`cf_zero_cost_passes_2_0`)**: `296`
- **Số valid setups trong Stage-2 vượt ngưỡng nếu `tp_is_maker = True` (`cf_maker_tp_passes_2_0`)**: `124`
- **Số valid setups trong Stage-2 vượt ngưỡng nếu `min_net_rr = 1.5` (`cf_min_net_rr_1_5_passes`)**: `183`
- **Số valid setups trong Stage-2 vượt ngưỡng nếu `min_net_rr = 1.0` (`cf_min_net_rr_1_0_passes`)**: `259`

---

## 4. Mẫu Quyết định Lịch sử Thực tế Có thể Tái lập (Reproducible Examples)

| # | `i_1m` | UTC Time | Model | Dir | Status | Entry | SL | TP | Stop Dist | Target Dist | Req Target (`RR>=2`) | Gross RR | Net RR | Lý do / Kết quả |
|---:|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | `6652` | `2026-10-15T02:07:00Z` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | **`ELIGIBLE`** | 4143.28 | 4150.99 | 4110.68 | 7.71 | 32.6 | 24.2224 | 4.2283 | **2.6006** | `ELIGIBLE` |
| 2 | `6652` | `2026-10-15T02:07:00Z` | `CG01_SWEEP_MSS_FVG` | `SHORT` | **`ELIGIBLE`** | 4143.28 | 4150.99 | 4110.68 | 7.71 | 32.6 | 24.2224 | 4.2283 | **2.6006** | `ELIGIBLE` |
| 3 | `6676` | `2026-10-15T02:31:00Z` | `SMC_M1_REVERSAL` | `SHORT` | **`ELIGIBLE`** | 4143.64 | 4150.27 | 4110.68 | 6.63 | 32.96 | 22.0614 | 4.9713 | **2.9128** | `ELIGIBLE` |
| 4 | `6676` | `2026-10-15T02:31:00Z` | `SMC_M1_SWEEP_RECLAIM` | `SHORT` | **`ELIGIBLE`** | 4143.64 | 4150.27 | 4110.68 | 6.63 | 32.96 | 22.0614 | 4.9713 | **2.9128** | `ELIGIBLE` |
| 5 | `6886` | `2026-10-15T06:01:00Z` | `SMC_M1_REVERSAL` | `SHORT` | **`ELIGIBLE`** | 4142.76 | 4147.33 | 4109.96 | 4.57 | 32.8 | 17.9364 | 7.1772 | **3.6358** | `ELIGIBLE` |
| 6 | `6886` | `2026-10-15T06:01:00Z` | `SMC_M1_CONTINUATION` | `SHORT` | **`ELIGIBLE`** | 4142.76 | 4147.33 | 4109.96 | 4.57 | 32.8 | 17.9364 | 7.1772 | **3.6358** | `ELIGIBLE` |
| 7 | `6886` | `2026-10-15T06:01:00Z` | `SMC_M1_SWEEP_RECLAIM` | `SHORT` | **`ELIGIBLE`** | 4142.76 | 4147.33 | 4109.96 | 4.57 | 32.8 | 17.9364 | 7.1772 | **3.6358** | `ELIGIBLE` |
| 8 | `6887` | `2026-10-15T06:02:00Z` | `SMC_M1_REVERSAL` | `SHORT` | **`ELIGIBLE`** | 4142.39 | 4146.92 | 4109.96 | 4.53 | 32.43 | 17.8555 | 7.1589 | **3.608** | `ELIGIBLE` |
| 9 | `6887` | `2026-10-15T06:02:00Z` | `SMC_M1_CONTINUATION` | `SHORT` | **`ELIGIBLE`** | 4142.39 | 4146.92 | 4109.96 | 4.53 | 32.43 | 17.8555 | 7.1589 | **3.608** | `ELIGIBLE` |
| 10 | `6887` | `2026-10-15T06:02:00Z` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | **`ELIGIBLE`** | 4142.39 | 4146.92 | 4109.96 | 4.53 | 32.43 | 17.8555 | 7.1589 | **3.608** | `ELIGIBLE` |
| 11 | `6887` | `2026-10-15T06:02:00Z` | `SMC_M1_SWEEP_RECLAIM` | `SHORT` | **`ELIGIBLE`** | 4142.39 | 4146.92 | 4109.96 | 4.53 | 32.43 | 17.8555 | 7.1589 | **3.608** | `ELIGIBLE` |
| 12 | `6887` | `2026-10-15T06:02:00Z` | `CG01_SWEEP_MSS_FVG` | `SHORT` | **`ELIGIBLE`** | 4142.39 | 4146.92 | 4109.96 | 4.53 | 32.43 | 17.8555 | 7.1589 | **3.608** | `ELIGIBLE` |
| 13 | `6888` | `2026-10-15T06:03:00Z` | `SMC_M1_REVERSAL` | `SHORT` | **`ELIGIBLE`** | 4142.43 | 4146.93 | 4109.96 | 4.5 | 32.47 | 17.7956 | 7.2156 | **3.6265** | `ELIGIBLE` |
| 14 | `6888` | `2026-10-15T06:03:00Z` | `SMC_M1_CONTINUATION` | `SHORT` | **`ELIGIBLE`** | 4142.43 | 4146.93 | 4109.96 | 4.5 | 32.47 | 17.7956 | 7.2156 | **3.6265** | `ELIGIBLE` |
| 15 | `6888` | `2026-10-15T06:03:00Z` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | **`ELIGIBLE`** | 4142.43 | 4146.93 | 4109.96 | 4.5 | 32.47 | 17.7956 | 7.2156 | **3.6265** | `ELIGIBLE` |
| 16 | `30` | `2026-10-08T06:09:00Z` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | **`VALID_SETUP_REJECTED_BY_RISK`** | 4124.8 | 4131.15 | 4122.91 | 6.35 | 1.89 | 21.4633 | 0.2976 | **-0.1532** | `NET_RR_TOO_LOW: Net R:R 1:-0.1532 chưa đạt ngưỡng tối thiểu 1:2.0 (NET_REWARD_NON_POSITIVE: Lợi nhuận ròng dự kiến $-0.38 <= 0 sau chi phí)` |
| 17 | `30` | `2026-10-08T06:09:00Z` | `CG01_SWEEP_MSS_FVG` | `SHORT` | **`VALID_SETUP_REJECTED_BY_RISK`** | 4124.8 | 4131.15 | 4122.91 | 6.35 | 1.89 | 21.4633 | 0.2976 | **-0.1532** | `NET_RR_TOO_LOW: Net R:R 1:-0.1532 chưa đạt ngưỡng tối thiểu 1:2.0 (NET_REWARD_NON_POSITIVE: Lợi nhuận ròng dự kiến $-0.38 <= 0 sau chi phí)` |
| 18 | `120` | `2026-10-08T07:39:00Z` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | **`VALID_SETUP_REJECTED_BY_RISK`** | 4126.64 | 4134.85 | 4123.83 | 8.21 | 2.81 | 25.1899 | 0.3423 | **-0.0504** | `NET_RR_TOO_LOW: Net R:R 1:-0.0504 chưa đạt ngưỡng tối thiểu 1:2.0 (NET_REWARD_NON_POSITIVE: Lợi nhuận ròng dự kiến $-0.12 <= 0 sau chi phí)` |
| 19 | `120` | `2026-10-08T07:39:00Z` | `CG01_SWEEP_MSS_FVG` | `SHORT` | **`VALID_SETUP_REJECTED_BY_RISK`** | 4126.64 | 4134.85 | 4123.83 | 8.21 | 2.81 | 25.1899 | 0.3423 | **-0.0504** | `NET_RR_TOO_LOW: Net R:R 1:-0.0504 chưa đạt ngưỡng tối thiểu 1:2.0 (NET_REWARD_NON_POSITIVE: Lợi nhuận ròng dự kiến $-0.12 <= 0 sau chi phí)` |
| 20 | `213` | `2026-10-09T03:29:00Z` | `SMC_M1_REVERSAL` | `LONG` | **`VALID_SETUP_REJECTED_BY_RISK`** | 4181.69 | None | None | None | None | None | 0.0 | **0.0** | `ATR_MISSING, NON_CONTIGUOUS_BARS` |
| 21 | `213` | `2026-10-09T03:29:00Z` | `SMC_M1_CONTINUATION` | `LONG` | **`VALID_SETUP_REJECTED_BY_RISK`** | 4181.69 | None | None | None | None | None | 0.0 | **0.0** | `ATR_MISSING, NON_CONTIGUOUS_BARS` |
| 22 | `213` | `2026-10-09T03:29:00Z` | `SMC_M1_FVG_CONTINUATION` | `LONG` | **`VALID_SETUP_REJECTED_BY_RISK`** | 4181.69 | None | None | None | None | None | 0.0 | **0.0** | `ATR_MISSING, NON_CONTIGUOUS_BARS` |
| 23 | `213` | `2026-10-09T03:29:00Z` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | **`VALID_SETUP_REJECTED_BY_RISK`** | 4181.69 | None | None | None | None | None | 0.0 | **0.0** | `ATR_MISSING, NON_CONTIGUOUS_BARS` |
| 24 | `213` | `2026-10-09T03:29:00Z` | `CG01_SWEEP_MSS_FVG` | `LONG` | **`VALID_SETUP_REJECTED_BY_RISK`** | 4181.69 | None | None | None | None | None | 0.0 | **0.0** | `ATR_MISSING, NON_CONTIGUOUS_BARS` |
| 25 | `216` | `2026-10-09T03:32:00Z` | `SMC_M1_FVG_CONTINUATION` | `LONG` | **`VALID_SETUP_REJECTED_BY_RISK`** | 4181.26 | 4177.83 | 4182.94 | 3.43 | 1.68 | 15.7261 | 0.4898 | **-0.2532** | `NET_RR_TOO_LOW: Net R:R 1:-0.2532 chưa đạt ngưỡng tối thiểu 1:2.0 (NET_REWARD_NON_POSITIVE: Lợi nhuận ròng dự kiến $-0.62 <= 0 sau chi phí)` |
