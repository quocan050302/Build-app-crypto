# BÁO CÁO NGHIỆM THU KỸ THUẬT V16.8 — CG-01 REPLAY FINANCIAL AUTHORITY, CAUSAL EVENTS AND EVIDENCE REPAIR

- **Commit Base**: `9663b3db6cf1c76240c1ccb41c6709a1040d19c4`
- **Commit Được Kiểm Thử**: `9663b3db6cf1c76240c1ccb41c6709a1040d19c4`
- **Diff Hash Working Tree**: `3fd77ebf84c9a08c4e97e168668a7ef183b7cc696b7e262a8543500e88084b85`
- **Run ID**: `run_v16_8_1792156950`
- **Thời điểm tạo**: 2026-10-16 13:22:44 UTC
- **Technical Status**: `PASS`
- **Historical Status**: `BLOCKED_DATA`
- **Performance Status**: `NOT_EVALUATED`

---

## 1. TÓM TẮT THỰC THI (EXECUTIVE SUMMARY)

> Đã kiểm tra: 1 ngày mẫu mô phỏng kỹ thuật · 1 lệnh khớp có guard. Đã đóng: 0 · Đang mở: 1. Lời/lỗ đã chốt sau phí: 0.0 USD. Drawdown tối đa MTM: 0.05%. Historical status: BLOCKED_DATA.

- **Phạm vi sửa chữa**: Hoàn thiện 5 trụ cột kỹ thuật của replay engine:
  1. **Quyền hạn vốn (Capital Basis Authority)**: Hỗ trợ `ReplayCapitalMode` (`CURRENT_ACCOUNT_BALANCE` vs `FIXED_INITIAL_CAPITAL`). Vốn thay đổi tự động điều chỉnh risk budget; equity non-positive bị chặn fail-closed.
  2. **Điều phối mô hình thực thi (Execution Model Dispatch)**: Phân tách `CLOSE_QUOTE` (wick không kích hoạt exit/fill) và `ASSUMED_OHLC_PATH` (waypoints giả định intrabar). Reject `CONFIG_ERROR` với mô hình không hợp lệ.
  3. **Thời gian sự kiện tăng dần (Causal Event Time)**: Loại bỏ triệt để bước nhảy ngược thời gian giữa nến close và waypoint đầu nến.
  4. **Thống kê ngày & Phiên chuẩn mực (Daily & Session Authority)**: Sử dụng trực tiếp `resolve_trade_day_bounds` và timezone policy (`Asia/Ho_Chi_Minh`), hỗ trợ ngày 23h/25h DST.
  5. **Quy chiếu tài chính & Phễu quyết định (Projection & Funnel)**: Chuyển tiếp `contract_multiplier` và phí thực tế vào MTM series; thu thập toàn bộ stage/reason từ `on_bar_close`.

---

## 2. BẰNG CHỨNG NGHIỆM THU TỪNG CỔNG KỸ THUẬT (EVIDENCE-BASED GATES)

| Gate | Trạng Thái | Test Suite | Số Case Yêu Cầu | Số Case Đã Chạy | Ghi Chú |
|---|---|---|---|---|---|
| `capital_basis_authority` | **PASS** | `test_v16_8_capital_basis` | 6 | 6 | OK |
| `execution_model_and_time_monotonicity` | **PASS** | `test_v16_8_execution_model_and_time` | 6 | 6 | OK |
| `statistics_and_financial_accounting` | **PASS** | `test_v16_8_statistics_and_accounting` | 7 | 7 | OK |
| `full_regression_v16` | **PASS** | `v16_full_suite` | 1 | 1 | OK |
| `git_cleanliness` | **PASS** | `git_diff_check` | 1 | 1 | OK |

---

## 3. BẢNG TỔNG HỢP CÁC BƯỚC TEST TỰ ĐỘNG (MACHINE TEST RESULTS)

| Tên Test Step | Lệnh Thực Thi | Thời Gian (s) | Passed | Failed | Deselected | Status |
|---|---|---|---|---|---|---|
| `git_diff_check` | `git diff --check` | 0.017 | - | - | - | **PASS** |
| `test_v16_8_capital_basis` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.275 | 6 | 0 | 0 | **PASS** |
| `test_v16_8_execution_model_and_time` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.094 | 6 | 0 | 0 | **PASS** |
| `test_v16_8_statistics_and_accounting` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.03 | 7 | 0 | 0 | **PASS** |
| `test_v16_7_execution_guard_parity` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.307 | 10 | 0 | 0 | **PASS** |
| `test_v16_7_risk_authority` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.042 | 5 | 0 | 0 | **PASS** |
| `test_v16_7_replay_costs` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 0.908 | 6 | 0 | 0 | **PASS** |
| `test_v16_6_measurement_integrity` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.234 | 6 | 0 | 0 | **PASS** |
| `test_v16_5_replay_shared_execution` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 1.476 | 6 | 0 | 0 | **PASS** |
| `v16_full_suite` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/...` | 4.933 | 144 | 0 | 1785 | **PASS** |

---

## 4. KẾT QUẢ MÔ PHỎNG MẪU (SYNTHETIC EXECUTION & ACCOUNTING EVIDENCE)

Dữ liệu mẫu kỹ thuật 18 nến M1 nhằm chứng minh toàn bộ chu trình:
`Candidate -> Arm -> Coordinator Before-Fill Guards -> Fill -> Close -> MTM Equity Series`.

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
| `OBSERVING_WAITING_TRIGGER` | 0 | Active observation awaiting valid sweep / displacement |
| `GUARD_BLOCKED` | 0 | Candidate setup blocked by risk/news/account/cooldown guards |
| `ARMED_CANDIDATES` | 0 | Setups successfully validated and armed for execution |
| `EXPIRED_BEFORE_FILL` | 1 | Armed orders expired prior to reaching limit price |
| `CANCELLED_ORDERS` | 0 | Armed orders cancelled prior to execution |

---

## 5. GIỚI HẠN HIỆN TẠI VÀ BƯỚC TIẾP THEO (LIMITATIONS & ROADMAP)

1. **Tình trạng Dữ liệu Lịch sử (Historical Status)**: Hiện tại kho lưu trữ cục bộ chưa nạp tệp M1 nến đóng chuẩn (yêu cầu M1 OHLC causal feed). Báo cáo đánh dấu trung thực `BLOCKED_DATA` theo đúng quy định PHẦN 110 & 170. Khi có tệp M1, chỉ cần chạy acceptance runner kèm cờ `--m1-dataset <path>`.
2. **Đảm bảo không suy diễn lợi nhuận**: Nghiệm thu kỹ thuật đạt PASS chứng minh tính toàn vẹn của động cơ thực thi và đối soát kế toán; không cấu thành lời hứa sinh lời cho chiến lược.
3. **Zero Strategy Drift**: Không nới lỏng bất kỳ ngưỡng quét thanh khoản (sweep), MSS/displacement, FVG hay khung giờ giao dịch của CG-01.

---
*Báo cáo được tạo tự động bởi tools/run_v16_8_acceptance.py*