# BÁO CÁO NGHIỆM THU CUỐI CÙNG — AURUM DESK V13.7
## CHUẨN HÓA VÒNG ĐỜI LỆNH REPLAY, TÍNH IDEMPOTENT CỦA SỔ CÁI VÀ ĐỐI SOÁT TỪNG CELL ARTIFACTS

---

### 1. TỔNG QUAN NGHIỆM THU 6 DÒNG CHO NGƯỜI MỚI (BEGINNER SUMMARY — PHẦN 84, 117)

1. **Đã kiểm tra**: 93 ngày lịch (09/07/2026 – 09/10/2026), 92 phiên NY thực thi được, **80 lệnh đã khớp** (12 phiên không có lệnh do không có setup đạt Net R:R >= 2.0R sau trượt giá).
2. **Lợi nhuận thực tế sau phí**: **-45.33 USDT** (Tổng phí giao dịch: **44.00 USDT**, Gross PnL: **-1.33 USDT** trên vốn ban đầu 1.000 USDT).
3. **Tỷ lệ thắng (Winrate)**: **12.5%** (10 lệnh thắng / 70 lệnh thua). Profit Factor: **0.55**.
4. **Mức sụt giảm tối đa (Max Drawdown)**: **5.84%** (-58.42 USDT, rủi ro vốn được kiểm soát chặt chẽ theo hạn mức).
5. **Điểm cần cải thiện**: Các lệnh PAPER lên lịch phiên chiều NY chưa đủ độ sắc bén cấu trúc để bù đắp chi phí spread và taker fee (chi phí phí 44.00 USDT chiếm tỷ trọng lớn trong khoản lỗ).
6. **Bước tiếp theo**: Duy trì hệ sinh thái khớp lệnh causal trung thực và đối soát nghiêm ngặt; chuyển sang tối ưu hóa chất lượng setup SMC xác nhận thay vì ép tần suất vào lệnh cố định.

---

### 2. BA TRẠNG THÁI NGHIỆM THU ĐỘC LẬP (THREE INDEPENDENT STATUSES — PHẦN 03, 04, 91)

| Trạng thái (Status) | Kết quả | Ý nghĩa & Bằng chứng xác minh |
| :--- | :---: | :--- |
| **Technical Status** | **PASS** | Tất cả 26 suite backend pytest (86 tests) PASS, frontend Vitest (117 tests) PASS, build production PASS, đối soát cell-by-cell artifacts JSON/CSV/XLSX PASS. Không còn bug lifecycle, không double-charge phí. |
| **Cadence Status** | **UNMET** | 80/92 phiên thực thi được có lệnh khớp (87.0% coverage). Tuân thủ nguyên tắc Phần 04 & 55: **Không hạ tiêu chí xuống 80% để tự nhận PASS**. 12 phiên không có setup đạt Net R:R >= 2.0R được ghi nhận trung thực là UNMET. |
| **Economic Status** | **LOSING** | Net PnL đạt -45.33 USDT. Báo cáo trung thực kết quả mô phỏng thị trường thực tế với chi phí bất lợi, không uốn cong số liệu hay sửa SL/TP để làm đẹp kết quả kinh tế. |

---

### 3. CÁC LỖI ĐÃ KHẮC PHỤC TRỰC TIẾP TRÊN MÃ NGUỒN (CODE REPAIRS — PHẦN 06, 07, 08, 11-45)

1. **Sửa lỗi vòng đời lệnh REJECTED (Phần 06, 23, 24, 25)**:
   - Trong `backend/lab/replay_execution.py`: bổ sung `TERMINAL_ORDER_STATUSES` guard ngay đầu `try_fill_pending_order()`. Bất kỳ lệnh nào có trạng thái `FILLED`, `REJECTED`, `EXPIRED`, `CANCELLED` đều bị chặn ngay lập tức, không gọi calculator hay phát sinh phí/vị thế mới.
   - Thêm helper chuẩn hóa `reject_order(order, reason_code)` gán dứt khoát `order.status = "REJECTED"` và ghi nhận lý do.
   - Trong `backend/lab/replay_engine.py`: Phase 1 kiểm tra `normalize_order_status(p_order.status)`. Lệnh REJECTED lập tức được chuyển vào `all_orders` lịch sử và dọn sạch khỏi danh sách `pending_orders` active, ngăn chặn hoàn toàn việc lệnh bị từ chối ở nến trước lại tiếp tục khớp ở nến sau.

2. **Xóa bỏ vòng lặp nhân SL ép Take Profit (Phần 07, 51, 52, 53)**:
   - Trong `backend/lab/daily_research_scheduler.py`: xóa bỏ hoàn toàn đoạn mã `for mult_f in [3.0, 3.5, 4.0]: ext_target = ...` dựa trên `sl_dist`.
   - Mục tiêu giá được tính toán từ cấu trúc thực tế: đỉnh/đáy swing hoặc measured move từ độ rộng range đã biết (`width = high - low`), không kéo giãn TP theo khoảng cách SL để làm giả tỷ lệ R:R.
   - Nguồn gốc mục tiêu (`target_source`, `target_model`, `stop_model`) được lưu truyền nguyên vẹn từ kế hoạch candidate -> pending order -> position -> xuất artifacts.

3. **Bổ sung thư viện thẩm định bằng chứng & Sổ cái Idempotent (Phần 11-20, 31-34)**:
   - Tạo mới `backend/lab/replay_evidence_utils.py` cung cấp các hàm nền tảng: `finite_number` (loại bỏ bool, NaN, Inf), `decimal_amount`, `read_required`, `stable_id` (deterministic sha256), `index_unique`, `compare_trade_numbers`, và `apply_posting_once`.
   - `ReplayContext.record_posting()` kiểm tra posting idempotency: lặp lại cùng `posting_id` với cùng payload là thao tác no-op hợp lệ; xung đột số tiền hoặc payload sẽ ném lỗi lập tức.

4. **Nâng cấp bộ đối soát Artifacts Cell-by-Cell & Kiểm thử Negative (Phần 08, 73-80, 106)**:
   - Trong `backend/lab/replay_integrity.py`: refactor `verify_exported_artifacts()`.
   - Kiểm tra mã băm SHA256 thực tế (từ chối chuỗi giả mạo FAKE/ngắn).
   - Kiểm tra từng dòng trong `trades.csv` theo `trade_id`, đối chiếu từng giá trị `direction`, `entry_price`, `stop_loss`, `take_profit`, `exit_price`, `net_pnl`.
   - Mở file Excel `.xlsx` kiểm tra từng ô trong trang Tổng quan (`Mã kiểm thử`, `Tổng lợi nhuận thực hiện`) và từng ô trong trang Chi tiết lệnh (cột Direction, Entry, SL, TP, Net PnL).
   - Đã xác thực bằng bộ kiểm thử negative `test_v13_7_artifact_contract.py`: bất kỳ sửa đổi trái phép nào vào TP, Net PnL, Run ID hay fake hash đều bị phát hiện và FAIL chính xác mã lỗi.

---

### 4. THÔNG SỐ VÀ DẤU BĂM DỮ LIỆU ĐỐI SOÁT (CANONICAL AUDIT EVIDENCE)

- **Run ID**: `v12-replay-83e3f901`
- **Khoảng thời gian mô phỏng**: 1783609200000 – 1791558000000 (09/07/2026 – 09/10/2026 UTC, 93 ngày)
- **Cấu hình Replay Hash (run_config_hash)**: `c1aaa05326ae2fc1`
- **Tập dữ liệu nến chuẩn hóa Hash (dataset_hash)**: `1dd754e8f038db496dee577d11c1d834517a5c8c483386efb4454297f011e63f`
- **Vốn ban đầu**: 1.000,00 USDT
- **Đòn bẩy & Ký quỹ**: x30, ISOLATED
- **Chi phí mô phỏng**: Taker fee rate = 0.06%, Maker fee rate = 0.02%, Trượt giá vào/ra lệnh = 0.10 USD, Spread = 0.20 – 0.35 USD.

---

### 5. KẾT QUẢ KIỂM THỬ TỔNG HỢP (TEST EXECUTION SUITE)

- **Stage 1 (Backend Pytest)**: 26 files kiểm thử, **86 test cases**, **100% PASSED** (thời gian: 80.4s).
  - Bao gồm toàn bộ các test mới V13.7:
    - `backend/tests/test_v13_7_evidence_utils.py` (10 passed)
    - `backend/tests/test_v13_7_order_lifecycle.py` (3 passed)
    - `backend/tests/test_v13_7_ledger_idempotency.py` (3 passed)
    - `backend/tests/test_v13_7_execution_costs.py` (3 passed)
    - `backend/tests/test_v13_7_target_provenance.py` (3 passed)
    - `backend/tests/test_v13_7_causal_driver.py` (2 passed)
    - `backend/tests/test_v13_7_data_audit.py` (2 passed)
    - `backend/tests/test_v13_7_artifact_contract.py` (3 passed)
    - `backend/tests/test_v13_7_cadence_contract.py` (5 passed)
    - `backend/tests/test_v13_7_engine_e2e.py` (2 passed)
- **Stage 2 (Frontend Vitest & Build)**: 15 test files, **117 test cases**, **100% PASSED**; `npm run build` thành công xuất bundle production trong 7.16s.
- **Stage 3 (Empirical Replay & Integrity)**: Replay 3 tháng hoàn tất trong 14.8s, 7/7 tiêu chí toàn vẹn đạt chuẩn PASS.
- **Stage 4 (Artifact Verification)**: Toàn bộ artifacts xuất bản tại `backend/lab/artifacts/` được đối soát cell-by-cell thành công (`artifacts_verified: True`).

---

### 6. CÁC TẬP TIN ARTIFACTS XUẤT BẢN

Các file artifacts chính thức được lưu tại thư mục run:
- Báo cáo tổng hợp: `backend/lab/artifacts/v12-replay-83e3f901/report.json`
- Chi tiết danh sách lệnh: `backend/lab/artifacts/v12-replay-83e3f901/trades.csv`
- Sổ làm việc Excel đa trang: `backend/lab/artifacts/v12-replay-83e3f901/trades.xlsx`
- Bằng chứng nghiệm thu tự động: `acceptance.json` (tại thư mục gốc dự án)
- Báo cáo kiểm thử JUnit XML: `backend/lab/artifacts/v13_7_tests/junit.xml`
