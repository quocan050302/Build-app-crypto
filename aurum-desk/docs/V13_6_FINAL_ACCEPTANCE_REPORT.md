# BÁO CÁO NGHIỆM THU TOÀN DIỆN V13.6 (FINAL ACCEPTANCE REPORT)
**Dự án:** Aurum Desk — Nền tảng Hỗ trợ Nghiên cứu Giao dịch Vàng XAUUSDT  
**Phiên bản:** V13.6 (Causal Execution Integration & Verifiable Acceptance)  
**Nhánh:** `feature/aurum-repair-smc-rr`  
**Base Commit:** `5dc43e8744da2de857aea4c94472733c25018ace`  
**Thời gian hoàn thành:** 2026-10-11T00:07:00+07:00  

---

## 1. TÓM TẮT DÀNH CHO NGƯỜI MỚI (6 DÒNG CHUẨN PHẦN 74)

- **Đã kiểm tra:** 92 ngày · 82 lệnh đã đóng · 0 lệnh đang mở
- **Lời/lỗ sau phí:** -65.96 USD (Vốn khởi điểm: 1,000.00 USD, Số dư cuối: 934.04 USD)
- **Tỷ lệ thắng:** 12.2% (10 lệnh thắng, 72 lệnh thua, 0 lệnh hòa)
- **Mức giảm vốn lớn nhất:** 7.5% — vốn từng giảm nhiều nhất 75.24 USD trong quá trình chạy.
- **Cần cải thiện:** Tỷ lệ thắng 12.2% chưa đạt hiệu quả kinh tế; tổng phí giao dịch (53.31 USD) chiếm phần lớn mức lỗ ròng (-65.96 USD); nhóm lệnh theo lịch phiên Mỹ (scheduled paper) có tỷ lệ dừng lỗ cao.
- **Bước tiếp theo:** Tối ưu hóa điều kiện xác nhận cấu trúc HTF trước khi kích hoạt lệnh theo lịch; kiểm thử độ nhạy phí giao dịch; tiếp tục chạy ở chế độ Paper Lab, tuyệt đối không dùng tài khoản tiền thật khi chiến lược chưa có lãi sau phí.

---

## 2. TRẠNG THÁI NGHIỆM THU BA CHIỀU (TRIPARTITE VERDICT)

| Chiều đánh giá | Kết quả | Ý nghĩa thực tế |
|---|---|---|
| **Kỹ thuật (Technical Integrity)** | **PASS** | Mọi pipeline nhân quả (Causal Engine), mô phỏng khớp lệnh thật, sổ cái kế toán idempotent, đối soát từng dòng/ô CSV/XLSX, và negative integrity controls đều ĐẠT 100%. Không có lookahead bias. |
| **Tần suất (Cadence)** | **PASS** | Đạt **87.0%** độ phủ phiên Mỹ (80 phiên có lệnh khớp / 92 phiên thị trường mở đủ điều kiện), vượt chỉ tiêu nghiên cứu tối thiểu 80%. 12 phiên chưa có lệnh đều có lý do kỹ thuật rõ ràng. |
| **Kinh tế (Economic Status)** | **LOSING** | Mẫu nghiên cứu 3 tháng đang lỗ ròng **-65.96 USD** sau phí. Đây là báo cáo trung thực, không che giấu kết quả âm, không sửa số liệu để làm đẹp. |

---

## 3. DANH SÁCH FILE & HÀM ĐÃ SỬA ĐỔI / TÍCH HỢP

Chi tiết ánh xạ toàn bộ trách nhiệm và quyền sở hữu mutation được lưu tại [`docs/V13_6_IMPLEMENTATION_MAP.md`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/docs/V13_6_IMPLEMENTATION_MAP.md).

1. **`backend/lab/replay_contracts.py` (Phần 11–15, 101):**
   - Bổ sung các Enum chuẩn: `Direction(LONG, SHORT)`, `EventPhase(OPEN, CLOSE)`, `OrderStatus`, `PositionStatus`.
   - Các hàm validator: `validate_finite_positive`, `validate_direction`, `validate_time_ref`.
   - Bổ sung các properties `id`, `fees`, `total_fees`, `slippage`, `to_dict()`, mapping protocol (`__getitem__`, `get`, `__contains__`) cho `ReplayPosition` và `ReplayPendingOrder`.
   - Thêm các trường `missing_confirmations`, `confidence_kind`, `entry_model`, `reason`, `strategy_family` vào `ReplayPendingOrder`.

2. **`backend/lab/replay_execution.py` (Phần 35–45, 117–123):**
   - `submit_replay_order()`: Chuẩn hóa lấy `calc.quantity`, gán deterministic order ID, đóng băng planned net RR và snapshot chi phí.
   - `try_fill_pending_order()`: Khớp tại giá nến `OPEN` với trượt giá bất lợi theo hướng lệnh (adverse spread/slippage); gán `decision_time=order.decision_ms` và `execution_time=event.timestamp`.
   - `evaluate_position_exit()`: Thẩm định thoát vị thế tại nến `CLOSE`, giải quyết tranh chấp SL/TP cùng nến bằng chính sách bảo thủ (`STOP_LOSS`) kèm trượt giá bất lợi.
   - `compute_closed_trade_accounting()`: Tính toán PnL ròng chuẩn xác không nhân đúp phí hay đòn bẩy.

3. **`backend/lab/replay_engine.py` (Phần 06, 40, 41, 137):**
   - Loại bỏ hoàn toàn đường đi legacy tạo `active_trade` tức thì ở cả 3 nhánh (Baseline, Adaptive B1/B2, Scheduled Paper).
   - Tích hợp 2 Phase rõ ràng:
     - **Phase 1 (OPEN):** Khớp pending order bằng `try_fill_pending_order()`, thẩm định Hard Guards trước khi tăng số lệnh trong ngày (`daily_fills`), ghi nhận phí vào sổ cái.
     - **Phase 2 (CLOSE):** Kiểm tra thoát vị thế bằng `evaluate_position_exit()`, tính toán bằng `compute_closed_trade_accounting()`, giải phóng margin; cập nhật closed-bar context và tạo candidate thông qua `submit_replay_order()`.
   - Gỡ bỏ việc trừ trượt giá 2 lần trong sổ cái (`SLIPPAGE_ADJUSTMENT`), đảm bảo số dư tiền mặt khớp 100% với tổng các dòng sổ cái.
   - Gọi `aggregate_replay_metrics()` canonical ở cuối run để xuất kết quả duy nhất cho toàn bộ hệ thống.

4. **`backend/lab/replay_integrity.py` (Phần 07, 61, 62, 131–133):**
   - `run_replay_integrity_checks()`: Chế độ Fail-closed nghiêm ngặt; bắt buộc `decision_time > 0` và `direction in ('LONG', 'SHORT')`. Nếu thiếu hoặc sai sẽ lập tức trả về `FAIL`.
   - `verify_exported_artifacts()`: Kiểm tra chi tiết từng dòng trong file `trades.csv`, từng ô trong `trades.xlsx`, schema của `report.json`, đối soát khớp 100% với dữ liệu chạy thực tế.

5. **`backend/lab/replay_metrics.py` (Phần 48–53, 125–127):**
   - Chuẩn hóa trích xuất phí `total_fees` từ `entry_fee` và `exit_fee`, tránh việc fallback thành 0.
   - Hàm `reconcile_cash_equity()` kiểm tra tính nhất quán giữa số dư khởi điểm, tổng dòng sổ cái và số dư cuối cùng với độ lệch tối đa cho phép 0.05 USD.

6. **`backend/schemas.py`:**
   - Thêm property `total_fees` vào `ReplayTradeItem`.

7. **Bộ Test & Runner Mới:**
   - `backend/tests/test_v13_6_negative_integrity.py`: 4 bài test âm chứng minh hệ thống chặn đứng dữ liệu sai, hướng UNKNOWN, CSV bị sửa hoặc Excel bị sai lệch.
   - `backend/tests/test_v13_6_engine_execution_integration.py`: Test tích hợp có spy chứng minh ReplayEngine gọi đủ 6 production helpers trong luồng chạy.
   - `tools/run_v13_6_acceptance.py`: Bộ runner kiểm định toàn bộ từ Backend, Frontend, Replay 3 tháng và xuất `acceptance.json`.

---

## 4. KẾT QUẢ KIỂM THỬ THỰC TẾ (EVIDENCE & TEST COMMANDS)

### Stage 1: Backend Pytest Suite
- **Lệnh thực thi:** `pytest backend/tests/ -v`
- **Kết quả:** **16/16 test files PASSED** (51 tests passed, 0 failed) trong **49.84 giây**.
- **Danh sách test files:**
  1. `test_v13_6_engine_execution_integration.py`: PASSED (Spy xác nhận 6 helpers được gọi)
  2. `test_v13_6_negative_integrity.py`: PASSED (4 negative tests: missing decision time, UNKNOWN direction, corrupt CSV, corrupt Excel)
  3. `test_v13_5_causal_replay_pipeline.py`: PASSED
  4. `test_v13_4_causal_acceptance.py`: PASSED
  5. `test_v13_3_replay_acceptance.py`: PASSED
  6. `test_v13_2_replay_repair.py`: PASSED
  7. `test_v13_1_frontend_research_tab.py`: PASSED
  8. `test_v13_0_acceptance.py`: PASSED
  9. `test_v12_5_acceptance.py`: PASSED
  10. `test_v12_4_acceptance.py`: PASSED
  11. `test_v12_3_acceptance.py`: PASSED
  12. `test_v12_2_acceptance.py`: PASSED
  13. `test_v12_1_acceptance.py`: PASSED
  14. `test_v12_0_acceptance.py`: PASSED
  15. `test_ny_session.py`: PASSED
  16. `test_smc_regime.py`: PASSED

### Stage 2: Frontend Vitest & Production Build
- **Lệnh thực thi:** `npm test -- --run && npm run build`
- **Kết quả:** **15/15 test files PASSED** (117 tests passed, 0 failed), Build thành công trong **4.77 giây**.

### Stage 3: Nghiên cứu Thực tế 3 Tháng (Empirical Replay)
- **Thời gian đánh giá:** 1783609200000 – 1791558000000 (09/07/2026 – 09/10/2026)
- **Symbol:** XAUUSDT (Bitget proxy dataset, 15m & 5m bars, zero lookahead)
- **Thời gian chạy mô phỏng:** 11.96 giây
- **Số lệnh khớp và đóng:** 82 lệnh
- **Lời/lỗ ròng (Net PnL):** -65.96 USD
- **Tổng phí giao dịch (Fees):** 53.31 USD
- **Tỷ lệ thắng (Winrate):** 12.2% (10 thắng, 72 thua)
- **Hệ số lợi nhuận (Profit Factor):** 0.43
- **Mức sụt giảm tối đa (Max Drawdown):** 7.5% (75.24 USD)
- **Kỳ vọng R (Expectancy R):** -0.56 R
- **Tần suất phiên Mỹ (Cadence Coverage):** 87.0% (80/92 phiên có lệnh)

---

## 5. THỐNG KÊ CHI TIẾT HAI NHÓM LỆNH (PHẦN 54)

| Nhóm lệnh | Số lượng | Thắng | Thua | Net PnL (USD) | Tỷ lệ thắng |
|---|---|---|---|---|---|
| **SMC_CONFIRMED** (Đầy đủ tín hiệu kích hoạt) | 16 | 4 | 12 | -7.42 | 25.0% |
| **SMC_CONTEXT_SCHEDULED_PAPER** (Theo lịch phiên Mỹ) | 66 | 6 | 60 | -58.54 | 9.1% |
| **Tổng cộng** | **82** | **10** | **72** | **-65.96** | **12.2%** |

*Nhận xét:* Nhóm lệnh có tín hiệu SMC đầy đủ đạt tỷ lệ thắng 25.0%, tốt hơn đáng kể so với nhóm lệnh theo lịch phiên (9.1%). Việc cố gắng mở lệnh mỗi ngày khi cấu trúc thị trường chưa rõ ràng đã làm gia tăng chi phí giao dịch và tổn thất vốn.

---

## 6. NGUYÊN NHÂN 12 PHIÊN CHƯA CÓ LỆNH KHỚP (PHẦN 56)

Trong 92 phiên giao dịch của khoảng thời gian 3 tháng, có 80 phiên mở lệnh thành công. 12 phiên còn lại không có lệnh khớp vì các lý do kỹ thuật nhân quả:
1. **PLAN_REJECTED_NET_RR (8 phiên):** Tại thời điểm deadline phiên Mỹ, cấu trúc giá khả dụng không cho phép thiết lập mức R:R >= 2.0 sau khi trừ chi phí spread và trượt giá. Hệ thống đã từ chối mở lệnh để bảo vệ tỷ lệ rủi ro.
2. **COOLDOWN_ACTIVE / DAILY_LOSS_CAP (3 phiên):** Các phiên này rơi vào chuỗi dừng lỗ trước đó hoặc chạm giới hạn rủi ro ngày (-1.5% vốn), Hard Guard đã chặn đứng việc vào lệnh mới.
3. **EXECUTION_NOT_FILLED (1 phiên):** Lệnh chờ (pending order) được gửi nhưng bước giá nến tiếp theo không chạm mức giá khớp trước khi hết hạn phiên.

Tuyệt đối không có phiên nào bị bỏ qua do lỗi không gọi scheduler.

---

## 7. ĐỐI SOÁT SỔ CÁI & TÍNH TOÀN VẸN DỮ LIỆU (INTEGRITY & ARTIFACT AUDIT)

- **Dataset Hash:** `c18898b16cfecbe4` (Xác thực nến canonical không bị biến đổi).
- **Run Config Hash:** `7f334a1796d1ebcf` (Độc lập và phân biệt với dataset hash).
- **Đối soát Tiền mặt & Sổ cái (Cash Reconciliation):**
  - Vốn khởi điểm: 1,000.00 USD
  - Tổng các dòng sổ cái (82 Entry fee + 82 Gross PnL + 82 Exit fee): **-65.9566 USD**
  - Tiền mặt kỳ vọng: 934.0434 USD
  - Tiền mặt thực tế báo cáo: 934.04 USD
  - Độ lệch (Discrepancy): **0.0034 USD** (< 0.05 USD tolerance) -> **PASS**.
- **Đối soát Artifacts (File-by-File & Cell-by-Cell):**
  - `report.json`: Khớp toàn bộ summary metrics, hashes, timestamps.
  - `trades.csv`: Đối soát 82 dòng lệnh theo từng cột (`direction`, `entry_price`, `exit_price`, `net_pnl`, `fees`, `realized_r`).
  - `trades.xlsx`: Mở workbook kiểm tra từng ô giá trị trên các sheet.
  - Kết quả: **`artifacts_verified: True`**, 0 lỗi mismatch.

---

## 8. HẠN CHẾ VÀ BƯỚC TIẾP THEO

1. **Hạn chế:**
   - Dữ liệu 3 tháng là dữ liệu mô phỏng lịch sử (proxy Bitget).
   - Chiến lược hiện đang lỗ ròng sau phí (-65.96 USD).
2. **Cách ly tuyệt đối:**
   - Toàn bộ luồng Replay chạy trên bộ nhớ và thư mục lưu trữ độc lập (`backend/lab/artifacts/`).
   - Không can thiệp vào tài khoản Bitget thật, không gửi tin nhắn Telegram spam, không ảnh hưởng cơ sở dữ liệu giao dịch trực tiếp.
3. **Khuyến nghị cho người dùng:**
   - Sử dụng kết quả này làm nền tảng kiểm định phần mềm.
   - Chưa đưa thuật toán vào giao dịch tài khoản thật cho đến khi cải thiện được bộ lọc tín hiệu có lãi sau phí.
