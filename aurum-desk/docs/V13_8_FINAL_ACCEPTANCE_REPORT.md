# V13.8 FINAL ACCEPTANCE REPORT
## AURUM DESK — REPLAY CORRECTNESS, STRICT VERIFICATION & DETERMINISTIC EVIDENCE

---

### 1. TỔNG QUAN NGHIỆM THU 6 DÒNG CHO NGƯỜI MỚI (BEGINNER SUMMARY — PHẦN 84, 117, 149)

1. **Đã kiểm tra**: 93 ngày lịch (09/07/2026 – 09/10/2026), 92 phiên NY thực thi được, **80 lệnh đã đóng**, 0 lệnh đang mở (12 phiên không có lệnh do không có cấu trúc đạt Net R:R >= 2.0R sau phí và trượt giá).
2. **Lợi nhuận thực tế sau phí**: **-45.33 USDT** (Tổng phí giao dịch: **44.00 USDT**, Gross PnL trước phí: **-1.33 USDT** trên vốn ban đầu 1.000,00 USDT).
3. **Tỷ lệ thắng (Winrate)**: **12.5%** (10 lệnh thắng / 70 lệnh thua trên tổng 80 lệnh đã đóng). Profit Factor: **0.55**.
4. **Mức sụt giảm vốn lớn nhất (Max Drawdown)**: **5.84%** (vốn từng giảm nhiều nhất 58.62 USDT trong toàn bộ quá trình chạy 3 tháng).
5. **Điểm cần cải thiện**: Tỷ lệ vào lệnh ở phiên NY đạt 87% nhưng chi phí giao dịch (phí 44.00 USDT) chiếm phần lớn khoản lỗ; cần cải thiện chất lượng điểm vào SMC thay vì dựa vào lệnh scheduled để đạt chỉ tiêu.
6. **Bước tiếp theo**: Lõi replay, sổ cái kế toán và bộ kiểm chứng artifact đã được chốt tính đúng đắn 100%; có thể yên tâm chuyển sang phát triển các tính năng tiếp theo mà không làm xáo trộn luồng replay.

---

### 2. BA TRẠNG THÁI NGHIỆM THU ĐỘC LẬP (THREE INDEPENDENT STATUSES — PHẦN 03, 04, 91, 155)

| Trạng thái (Status) | Kết quả | Ý nghĩa & Bằng chứng xác minh |
| :--- | :---: | :--- |
| **Technical Status** | **PASS** | Tất cả 10 suite backend pytest (42 tests) PASS, frontend Vitest (117 tests) PASS, build production PASS, đối soát cell-by-cell artifacts JSON/CSV/XLSX PASS, kiểm thử lặp tất định 100% PASS. Đã sửa triệt để bug sổ cái và lỗ hổng bypass verifier. |
| **Cadence Status** | **UNMET** | 80/92 phiên thực thi được có lệnh khớp (87.0% coverage). Tuân thủ nguyên tắc Phần 04, 55 & 153: **Không hạ tiêu chí xuống 80% để tự nhận PASS**. 12 phiên không có setup đạt Net R:R >= 2.0R được báo cáo trung thực là UNMET. |
| **Economic Status** | **LOSING** | Net PnL đạt -45.33 USDT sau phí. Báo cáo trung thực kết quả mô phỏng thị trường thực tế với chi phí bất lợi, không uốn cong số liệu hay nới lỏng guard để làm giả lợi nhuận. |

---

### 3. CÁC NÂNG CẤP VÀ SỬA ĐỔI CỐT LÕI V13.8 (CORE REPAIRS — PHẦN 06-08, 32, 51, 123-146)

1. **Khắc phục triệt để lỗi trừ tiền trước khi deduplicate tại ReplayContext (Phần 32, 123–127)**:
   - Trước V13.8, `self.current_cash += amt` diễn ra trước khi tìm kiếm `pid` trong `posting_index`. Khi gọi lại cùng `posting_id`, tiền bị trừ thêm dù index chỉ có 1 dòng; khi payload bị xung đột, exception ném ra sau khi tiền đã bị trừ.
   - Trong V13.8: kiểm tra định dạng và tính hữu hạn (`math.isfinite()`), tìm kiếm `posting_index` và đối chiếu signature payload trước khi thực hiện bất kỳ phép gán tiền nào.
   - Lặp lại cùng payload 5 lần: số dư tiền giữ nguyên, số lượng posting không đổi. Conflicting payload: ném lỗi `POSTING_ID_PAYLOAD_CONFLICT` và bảo toàn 100% số dư tiền ban đầu.
   - Đồng bộ hóa `cash_balance` của engine với `current_cash` của context sổ cái sau mỗi sự kiện fill và close, đảm bảo chỉ có duy nhất một chủ sở hữu tiền mặt.

2. **Đóng băng duy nhất một Multiplier trong lập kế hoạch giá (Phần 07, 51, 134)**:
   - Trong `backend/lab/daily_research_scheduler.py`: xóa bỏ hoàn toàn vòng lặp tìm kiếm multiplier `for mm_mult in [2.0, 2.5, 3.0, 3.5]`.
   - Sử dụng một `measured_move_multiplier` đóng băng duy nhất từ policy (mặc định 3.5).
   - Nếu kế hoạch giá với multiplier này không đạt Net R:R >= 2.0R sau chi phí, engine ghi nhận từ chối và lý do rõ ràng; không mở rộng mục tiêu giá để gượng ép đạt điều kiện.

3. **Chặn triệt để lỗ hổng bypass của Verifier (Phần 08, 139–142)**:
   - Trước V13.8: trong Python `abs(float("nan") - val) > tol` luôn trả về `False`, khiến ô chứa `NaN` trong CSV và Excel không bị phát hiện; Excel cũng bỏ qua các dòng có trade ID lạ nếu không tìm thấy trong danh sách mong đợi.
   - Trong V13.8:
     - `backend/lab/replay_evidence_utils.py`: bổ sung `assert_exact_id_set` (yêu cầu tập hợp ID giữa kết quả và artifact phải trùng khớp 100%) và `assert_finite_equal` (xác thực tính hữu hạn trước khi so sánh khoảng cách).
     - `backend/lab/replay_integrity.py`: xác thực `math.isfinite()` trên từng ô số của `trades.csv` và `trades.xlsx`. Bắt lỗi ngay khi phát hiện `NaN` hoặc `XLSX_UNKNOWN_TRADE_ID`.
     - Xác thực mã băm SHA256 bằng regex hex nghiêm ngặt, từ chối hash giả mạo bắt đầu bằng `FAKE`.

4. **Runner nghiệm thu V13.8 với giai đoạn kiểm thử tính tất định 2 lượt (Phần 88, 90, 143–146)**:
   - Tạo mới `tools/run_v13_8_acceptance.py`.
   - Stage 1: Chạy 10 suite pytest, xuất JUnit XML và trích xuất số lượng test thực tế (42 tests passed).
   - Stage 2: Chạy Vitest (117 tests passed) và build frontend.
   - Stage 3: Chạy mô phỏng 3 tháng với dữ liệu cố định.
   - Stage 4: Đối soát cell-by-cell toàn bộ artifacts.
   - Stage 5: Chạy lại lượt 2 trên cùng cấu hình và seed, đối chiếu 100% khớp các chỉ số trades, net pnl, fees, cadence, dataset hash và config hash.
   - Tùy chọn `--skip-tests` đánh dấu `SKIPPED`, trạng thái kỹ thuật `NOT_VERIFIED` (không bao giờ tự nhận PASS khi bỏ qua kiểm thử).

---

### 4. THÔNG SỐ VÀ DẤU BĂM DỮ LIỆU ĐỐI SOÁT (CANONICAL AUDIT EVIDENCE)

- **Run ID**: `v12-replay-beffbc19`
- **Khoảng thời gian mô phỏng**: 1783609200000 – 1791558000000 (09/07/2026 – 09/10/2026 UTC, 93 ngày)
- **Cấu hình Replay Hash (run_config_hash)**: `c1aaa05326ae2fc1`
- **Tập dữ liệu nến chuẩn hóa Hash (dataset_hash)**: `1dd754e8f038db496dee577d11c1d834517a5c8c483386efb4454297f011e63f`
- **Vốn ban đầu**: 1.000,00 USDT
- **Đòn bẩy & Ký quỹ**: x30, ISOLATED
- **Chi phí mô phỏng**: Taker fee rate = 0.06%, Maker fee rate = 0.02%, Trượt giá vào/ra lệnh = 0.10 USD, Spread = 0.20 – 0.35 USD.

---

### 5. KẾT QUẢ KIỂM THỬ TỔNG HỢP (TEST EXECUTION SUITE)

- **Stage 1 (Backend Pytest)**: 10 test suites, **42 test cases**, **100% PASSED** (thời gian: 14.6s).
  - `backend/tests/test_v13_8_order_lifecycle.py` (4 passed)
  - `backend/tests/test_v13_8_ledger_idempotency.py` (4 passed)
  - `backend/tests/test_v13_8_execution_costs.py` (4 passed)
  - `backend/tests/test_v13_8_target_provenance.py` (4 passed)
  - `backend/tests/test_v13_8_causal_driver.py` (2 passed)
  - `backend/tests/test_v13_8_data_audit.py` (3 passed)
  - `backend/tests/test_v13_8_artifact_contract.py` (4 passed)
  - `backend/tests/test_v13_8_cadence_contract.py` (5 passed)
  - `backend/tests/test_v13_8_engine_e2e.py` (2 passed)
  - `backend/tests/test_v13_7_evidence_utils.py` (10 passed)
- **Stage 2 (Frontend Vitest & Build)**: 15 test files, **117 test cases**, **100% PASSED**; `npm run build` thành công xuất bundle production trong 5.39s.
- **Stage 3 (Empirical Replay & Integrity)**: Replay 3 tháng hoàn tất trong 12.49s, 7/7 tiêu chí toàn vẹn đạt chuẩn PASS.
- **Stage 4 (Artifact Verification)**: Toàn bộ artifacts xuất bản tại `backend/lab/artifacts/v12_2/v12-replay-beffbc19` được đối soát cell-by-cell thành công (`artifacts_verified: True`).
- **Stage 5 (Determinism Repeat Stage)**: Lượt chạy lặp lại (`run_b_id: v12-replay-e140c817`) đạt độ trùng khớp 100% so với lượt chạy A, không có bất kỳ sai lệch nào (`mismatches: []`, `verified_deterministic: True`).

---

### 6. CÁC TẬP TIN ARTIFACTS XUẤT BẢN

Các file artifacts chính thức được lưu tại thư mục run:
- Thư mục bundle: `backend/lab/artifacts/v12_2/v12-replay-beffbc19/`
- Báo cáo tổng hợp: `backend/lab/artifacts/v12_2/v12-replay-beffbc19/report.json`
- Chi tiết danh sách lệnh: `backend/lab/artifacts/v12_2/v12-replay-beffbc19/trades.csv`
- Sổ làm việc Excel đa trang: `backend/lab/artifacts/v12_2/v12-replay-beffbc19/Aurum_XAUUSDT_3Months_NY_ADAPTIVE_20260709_20261009_v12-replay-beffbc19.xlsx`
- Bằng chứng nghiệm thu tự động: `acceptance.json` (tại thư mục gốc dự án)
- Báo cáo kiểm thử JUnit XML: `backend/lab/artifacts/v13_8_tests/junit.xml`
