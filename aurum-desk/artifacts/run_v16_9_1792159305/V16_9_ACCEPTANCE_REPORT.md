# BÁO CÁO NGHIỆM THU KỸ THUẬT V16.9 — CG-01 CAUSAL EXECUTION, REAL HISTORICAL REPLAY, DECISION FUNNEL AND SIZING CONTRACT REPAIR

- **Commit Base**: `13dfe2d53a343f9d66ea8e086c75c0d21e41782c`
- **Commit Được Kiểm Thử**: `13dfe2d53a343f9d66ea8e086c75c0d21e41782c`
- **Diff Hash Working Tree**: `9f96f677238eeb569a8d614b3e3b32f6da3f0681bfcfc0e4c1fa13c9b2e1f95d`
- **Run ID**: `run_v16_9_1792159305`
- **Thời điểm tạo**: 2026-10-16 14:02:06 UTC
- **Technical Status**: `PASS`
- **Historical Status**: `BLOCKED_DATA`
- **Performance Status**: `NOT_EVALUATED`
- **Simulation Evidence Type**: `DATA_SYNTHESIZED_DEMO`

---

## 1. TÓM TẮT THỰC THI (EXECUTIVE SUMMARY)

> Đã kiểm tra kỹ thuật V16.9: 1 lệnh khớp có guard. Đã đóng: 0 · Đang mở: 1. Lời/lỗ đã chốt sau phí: 0.0 USD. Drawdown tối đa MTM: 0.05%. Historical status: BLOCKED_DATA · Technical status: PASS.

- **Phạm vi hoàn thiện 4 lỗi cốt lõi (LỖI A, B, C, D)**:
  1. **LỖI A — Thực thi OHLC Nhân quả (Causal OHLC Execution)**: Loại bỏ lookahead pre-pass tại `bar_open`. Sử dụng `build_assumed_bar_events` mở rộng nến thành các điểm waypoint giả định, tịnh tiến đồng hồ tăng dần đơn điệu và chỉ đóng lệnh khi điểm waypoint vi phạm ngưỡng. Sửa `TradeLifecycleService.process_exit_tick` để ưu tiên `tick_timestamp`.
  2. **LỖI B — Tách biệt Replay Lịch sử thật và Demo (Historical Runner)**: Tách bạch rõ ràng giữa `run_synthetic_execution_demo` và `run_historical_cg01_replay`. Tuyệt đối không cấy lệnh mẫu vào nhánh lịch sử. Khi thiếu dataset M1 thật, trung thực báo cáo `historical_status = BLOCKED_DATA` và `performance_status = NOT_EVALUATED`.
  3. **LỖI C — Hợp đồng Phễu Quyết định (Decision Funnel Contract)**: Bổ sung từ điển `cg01_evaluation` và `armed_order_id` vào mọi chu trình đánh giá của `ScalpWatcherOrchestrator.evaluate_cycle` (kể cả các nhánh return sớm). Driver chuẩn hóa quan sát bằng `normalize_replay_cycle_observation` và tổng hợp phễu đầy đủ.
  4. **LỖI D — Hợp đồng Quy mô Khả thi (Executable Sizing Contract)**: Phân tách rõ ràng giữa `target_budget`, `account_safety_cap` và `executable_budget`. Lưu vết đầy đủ các trường sizing vào `cost_snapshot` tại thời điểm fill (`capital_mode`, `target_capital`, `target_risk_budget`, `account_capital_at_fill`, `executable_risk_budget`, `actual_risk_after_rounding`, `sizing_adjustment_reason`).

---

## 2. BẰNG CHỨNG NGHIỆM THU TỪNG CỔNG KỸ THUẬT (EVIDENCE-BASED GATES)

| Gate | Trạng Thái | Test Suite | Số Case Yêu Cầu | Số Case Đã Chạy | Ghi Chú |
|---|---|---|---|---|---|
| `causal_execution` | **PASS** | `test_v16_9_causal_ohlc_execution` | 5 | 5 | OK |
| `historical_routing` | **PASS** | `test_v16_9_historical_runner` | 4 | 4 | OK |
| `decision_contract` | **PASS** | `test_v16_9_cg01_decision_funnel` | 4 | 4 | OK |
| `executable_sizing` | **PASS** | `test_v16_9_executable_sizing_contract` | 6 | 6 | OK |
| `full_regression_v16` | **PASS** | `v16_full_suite` | 1 | 1 | OK |
| `git_cleanliness` | **PASS** | `git_diff_check` | 1 | 1 | OK |

---

## 3. BẢNG TỔNG HỢP CÁC BƯỚC TEST TỰ ĐỘNG (MACHINE TEST RESULTS)

| Tên Test Step | Lệnh Thực Thi | Thời Gian (s) | Passed | Failed | Deselected | Status |
|---|---|---|---|---|---|---|
| `git_diff_check` | `git diff --check` | 0.016 | - | - | - | **PASS** |
| `test_v16_9_causal_ohlc_execution` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.276 | 5 | 0 | 0 | **PASS** |
| `test_v16_9_historical_runner` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.176 | 4 | 0 | 0 | **PASS** |
| `test_v16_9_cg01_decision_funnel` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 0.985 | 4 | 0 | 0 | **PASS** |
| `test_v16_9_executable_sizing_contract` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.168 | 6 | 0 | 0 | **PASS** |
| `test_v16_8_capital_basis` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.569 | 6 | 0 | 0 | **PASS** |
| `test_v16_8_execution_model_and_time` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.153 | 6 | 0 | 0 | **PASS** |
| `test_v16_8_statistics_and_accounting` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.102 | 7 | 0 | 0 | **PASS** |
| `test_v16_7_execution_guard_parity` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.338 | 10 | 0 | 0 | **PASS** |
| `test_v16_7_risk_authority` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.466 | 5 | 0 | 0 | **PASS** |
| `test_v16_7_replay_costs` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.133 | 6 | 0 | 0 | **PASS** |
| `test_v16_6_measurement_integrity` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 2.023 | 6 | 0 | 0 | **PASS** |
| `v16_full_suite` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 5.945 | 163 | 0 | 1785 | **PASS** |

---

## 4. KẾT QUẢ MÔ PHỎNG MẪU (SYNTHETIC EXECUTION & SIZING EVIDENCE)

Dữ liệu kiểm tra kỹ thuật chứng minh chu trình nhân quả đầy đủ:
`Observation -> Arm (Target Budget) -> Coordinator Before-Fill Guards -> Sizing Safety Check -> Fill (Executable Budget) -> Waypoint Exit -> Realized Accounting`.

- **Vốn ban đầu**: `5000.0 USD`
- **Tiền mặt kết thúc**: `5000.0 USD`
- **Tổng PnL thực hiện sau phí**: `None USD`
- **Drawdown tối đa MTM**: `0.05%`
- **Số lệnh đã khớp**: `1`
- **Số vị thế mở**: `1`
- **Số lệnh hết hạn trước khi chạm entry**: `1`

### Phễu Quyết Định và Phân Loại Lý Do Chặn (Rejection Funnel)

| Danh Mục | Số Lượng | Ý Nghĩa / Nguyên Nhân |
|---|---|---|
| `CONTEXT_NOT_READY` | 0 | HTF context or warmup insufficient for evaluation |
| `OBSERVING_WAITING_TRIGGER` | 3 | Active observation awaiting valid sweep / displacement |
| `GUARD_BLOCKED` | 0 | Candidate setup blocked by risk/news/account/cooldown guards |
| `ARMED_CANDIDATES` | 2 | Setups successfully validated and armed for execution |
| `EXPIRED_BEFORE_FILL` | 1 | Armed orders expired prior to reaching limit price |
| `CANCELLED_ORDERS` | 0 | Armed orders cancelled prior to execution |

---

## 5. GIỚI HẠN VÀ TÍNH TRUNG THỰC (LIMITATIONS & INTEGRITY)

1. **Tình trạng Dữ liệu Lịch sử (Historical Status)**: Khi chưa cung cấp dataset M1 thật qua tham số `--m1-dataset`, hệ thống trung thực báo cáo `BLOCKED_DATA` và không đo lường chỉ số sinh lời (`performance_status = NOT_EVALUATED`). Tuyệt đối không giả mạo kết quả lịch sử bằng lệnh demo.
2. **Không Thay Đổi Logic Chiến Lược**: Giữ nguyên vẹn 100% điều kiện setup CG-01 (sweep thresholds, MSS/displacement, FVG selection, RR >= 2.0).
3. **Tính Đơn Điệu Của Đồng Hồ**: Đồng hồ ReplayClock được kiểm tra tiến tuần tự theo thời gian sub-event, không bao giờ nhảy ngược về quá khứ.

---
*Báo cáo được tạo tự động bởi tools/run_v16_9_acceptance.py*