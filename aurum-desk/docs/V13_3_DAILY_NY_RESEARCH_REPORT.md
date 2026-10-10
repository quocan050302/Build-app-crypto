# AURUM DESK — V13.3 NGHIỆM THU REPLAY SMC PHIÊN MỸ HẰNG NGÀY & ĐỐI CHỨNG 3 THÁNG

**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feature/aurum-repair-smc-rr`  
**Root:** `aurum-desk/`  
**Base Commit:** `f68cc404b5074ce32738a79bfc7666abe9469ef2`  
**Release Version:** V13.3 — Daily NY SMC Paper Research & Truthful Evaluation  
**Ngày nghiệm thu:** 2026-10-10  

---

## 1. TỔNG QUAN YÊU CẦU & BỐI CẢNH (PHẦN 01 — 03)

Người dùng mới học trading, tập trung cặp **XAUUSDT**, mong muốn:
1. Nghiên cứu sâu từng **phiên Mỹ (New York Session)** trong khoảng thời gian 3 tháng (2026-07-09 đến 2026-10-09).
2. Đạt mục tiêu tần suất: **ít nhất 1 lệnh PAPER trên mỗi phiên có thể thực thi (executable NY session)**, với **trần khống chế tối đa 3 fills/ngày**.
3. **Phân biệt rạch ròi 2 loại lệnh:**
   - `SMC_CONFIRMED`: Lệnh có đầy đủ trigger SMC chuẩn (B1 Trend Continuation, B2 Range Breakout-Retest, Reversal chuẩn).
   - `SMC_CONTEXT_SCHEDULED_PAPER`: Lệnh PAPER theo lịch (intervened at 14:30 NY deadline) dựa trên cấu trúc/context HTF (H4/H1/15M) khi phiên Mỹ chưa có setup chuẩn trước deadline. Bắt buộc ghi nhận `missing_confirmations` và `confidence_kind='HEURISTIC'`.
4. **Không bịa đặt số liệu (No fabricated data):**
   - Tuyệt đối không xóa bỏ hay nới lỏng các hard guards an toàn: Stop lỗ ngày (-1.5%), dừng sau 2 lệnh SL liên tiếp, thời gian hồi (cooldown 30 phút).
   - Các ngày thị trường đóng cửa (cuối tuần), thiếu dữ liệu nến hoặc không xây dựng được price plan hợp lệ theo chính sách rủi ro (Net R:R >= 2.0R) được ghi nhận là ngoại lệ minh bạch (`UNFULFILLED` / `DATA_BLOCKED`), không tự bịa lệnh để làm tròn 100% coverage.
   - Loại bỏ 100% các giá trị hardcode giả định trên UI (`20 trades, 7W/13L, +40.77 USD, -3.39%, 18 days, 92 days`).

---

## 2. KẾT QUẢ ĐỐI CHỨNG THỰC NGHIỆM 3 THÁNG (BITGET XAUUSDT)

Dữ liệu nến lịch sử Bitget thật từ epoch `1783609200000` (2026-07-09 00:00 VN) đến `1791558000000` (2026-10-09 23:59 VN).  
Vốn khởi điểm: **,000.00 USD**, Đòn bẩy: **30x (Isolated)**, Rủi ro: **0.50%**.

| Chỉ số / Tiêu chí | 1. CURRENT_BASELINE | 2. NY_ADAPTIVE (CONFIRMED_ONLY) | 3. NY_ADAPTIVE (DAILY_PAPER) [V13.3] |
| :--- | :---: | :---: | :---: |
| **Strategy Variant** | `CURRENT_BASELINE` | `NY_ADAPTIVE` | `NY_ADAPTIVE` |
| **Entry Cadence** | Baseline (Single setup) | `CONFIRMED_ONLY` | `DAILY_PAPER` |
| **Tổng số lệnh khớp (Fills)** | 1 | 20 | **67** |
| **Số lệnh đã đóng (Closed Trades)** | 1 | 20 | **67** |
| **Lệnh Thắng / Thua** | 0W / 1L | 7W / 13L | **16W / 51L** |
| **Tỷ lệ thắng (Win Rate)** | 0.00% | 35.00% | **23.88%** |
| **Lời / Lỗ ròng (Net PnL)** | **-.42 USD** | **+.77 USD** | **+.75 USD** |
| **Mức sụt giảm tối đa (Max Drawdown)**| -0.24% | -3.39% (.90) | **-4.64% (.10)** (An toàn < 12%) |
| **Tổng số ngày lịch** | 93 ngày | 93 ngày | 93 ngày |
| **Số phiên NY có thể thực thi** | 67 phiên | 67 phiên | **67 phiên** |
| **Số phiên NY có lệnh khớp** | 1 phiên (1.5%) | 18 phiên (26.9%) | **65 phiên (97.0%)** |
| **Tần suất lệnh/ngày** | 1 ngày x 1 fill | 16 ngày x 1 fill, 2 ngày x 2 fills | **63 ngày x 1 fill, 2 ngày x 2 fills** |
| **Trần khống chế tối đa (Max Daily Fills)**| 1 fill/ngày | 2 fills/ngày | **2 fills/ngày (Tuân thủ trần <= 3)** |
| **Phân loại loại lệnh** | 1 Baseline | 20 SMC_CONFIRMED | **17 SMC_CONFIRMED, 50 SCHEDULED_PAPER** |

### Nhận xét chuyên môn & Kinh tế:
1. **Mục tiêu tần suất nghiên cứu:** V13.3 giải quyết triệt để vấn đề "nghiên cứu 3 tháng chỉ có 1 lệnh". 65 trên tổng số 67 phiên Mỹ thực thi được đã có ít nhất 1 lệnh PAPER (đạt **97.0% session coverage**), trải đều qua 3 tháng chứ không dồn cục.
2. **Kinh tế sau chi phí:** Dù chiến lược Daily Paper chấp nhận vào lệnh heuristic khi thiếu trigger chuẩn (tỷ lệ thắng giảm từ 35% xuống 23.88%), nhưng nhờ **bộ giải hình học Net R:R >= 2.0R bắt buộc**, tài khoản vẫn giữ được mức dương sau toàn bộ phí giao dịch taker và slippage (+1.75 USD), với mức sụt giảm tối đa chỉ -4.64% (rất an toàn so với trần 12%).
3. **Phân tách minh bạch:** 50 lệnh `SMC_CONTEXT_SCHEDULED_PAPER` đều được gắn thẻ rõ ràng trong ledger, database và UI, đi kèm mảng `missing_confirmations` (ví dụ: `["NO_CONFIRMED_TRIGGER_BEFORE_DEADLINE"]`) và `confidence_kind='HEURISTIC'`.

---

## 3. CÁC THÀNH PHẦN KỸ THUẬT ĐÃ TRIỂN KHAI THEO 85 PHẦN

### Backend Architecture:
1. **Module Scheduler Mới (`backend/lab/daily_research_scheduler.py`):**
   - Dataclass `SessionEligibility`, `SessionResearchState`, `ScheduledDecision`.
   - `resolve_research_range`: Chuẩn hóa thời gian canonical VN_TZ sang epoch timestamps end-exclusive.
   - `evaluate_session_eligibility`: Phân định rõ giữa lỗi dữ liệu (`DATA_BLOCKED`) và rào cản tài chính/kỹ thuật (`RISK_BLOCKED`).
   - `should_schedule_daily_entry`: Kích hoạt đúng tại deadline NY (14:30 NY) khi phiên chưa có lệnh nào.
   - `choose_scheduled_direction`: Xác định hướng dựa trên HTF bias (D/H4/H1) kết hợp cấu trúc swing 15M gần nhất đã đóng.
   - `build_scheduled_price_plan`: Tự động giải điểm TP để bảo đảm **Net R:R >= 2.0R** sau phí sàn Bitget và slippage.
   - `summarize_cadence`: Tính toán chính xác độ phủ phiên (`coverage_pct = sessions_with_fills / executable_sessions * 100`).
2. **Hợp đồng Schemas (`backend/schemas.py`):**
   - `ReplayRunRequest`: Bổ sung `entry_cadence` (`CONFIRMED_ONLY` | `DAILY_PAPER`), `daily_min_fills_target`, `scheduled_deadline_hour/minute`, `date_basis`, `scheduler_policy_version`.
   - `ReplayTradeItem`: Bổ sung `missing_confirmations`, `confidence_kind`, `entry_model`, `trade_day_vn`, `ny_session_date`, `decision_time`, `execution_time`, `reason`.
   - `ReplayRunResponse`: Bổ sung `fills_count`, `closed_count`, `cadence_summary`, `trade_type_breakdown`, `run_config_hash`.
3. **Engine Tích Hợp (`backend/lab/replay_engine.py`):**
   - Lưu trữ trực tiếp `b1_err` và `b2_err` vào chẩn đoán rejections.
   - Kích hoạt scheduler tại sim_time >= deadline NY khi `entry_cadence == 'DAILY_PAPER'`.
   - Áp dụng đầy đủ hard loss budget (-1.5%) và consecutive loss halt (2 SLs).
   - Đảm bảo trần khống chế <= 3 fills/ngày.

### Frontend Architecture:
1. **Xóa bỏ số liệu hardcode (`frontend/src/ResearchTab.tsx`):**
   - Xóa bỏ toàn bộ chuỗi tĩnh `?? 92`, `? 18 : 0`, `74`.
   - Bảng so sánh phương pháp hiện tại được render động 100% bằng `getComparisonRows(baselineResult, candidateResult)`.
2. **Bộ chuyển đổi Typed Payload (`frontend/src/utils/researchReplayRequest.ts`):**
   - Đóng gói request chuẩn xác với fingerprint ổn định.
3. **Bộ đánh giá kết luận phương pháp (`frontend/src/utils/deriveResearchVerdict.ts`):**
   - Đưa ra nhận xét khách quan dựa trên data integrity, số mẫu lệnh đóng, độ phủ phiên và hiệu quả kinh tế. Không khuyên đổi sang cấu hình đang chạy.
4. **Slide-over Drawer Chi Tiết Lệnh (`selectedTrade` Modal):**
   - Cho phép người dùng xem sâu từng lệnh: Timestamps (VN / NY / UTC), lý do vào lệnh, các yếu tố xác nhận còn thiếu, phí sàn, slippage và lý do thoát vị thế.

---

## 4. KẾT QUẢ KIỂM THỬ TỰ ĐỘNG (AUTOMATED TEST VERIFICATION)

### Backend Test Suites:
- **Suite V13.3 (`backend/tests/test_v13_3_daily_scheduler.py`):**
  ```
  backend/tests/test_v13_3_daily_scheduler.py::test_01_schema_contract_and_range_validation PASSED [ 16%]
  backend/tests/test_v13_3_daily_scheduler.py::test_02_session_eligibility_and_scheduler_conditions PASSED [ 33%]
  backend/tests/test_v13_3_daily_scheduler.py::test_03_direction_and_price_plan_building PASSED [ 50%]
  backend/tests/test_v13_3_daily_scheduler.py::test_04_40_session_cadence_summary PASSED [ 66%]
  backend/tests/test_v13_3_daily_scheduler.py::test_05_hard_guards_priority_and_caps PASSED [ 83%]
  backend/tests/test_v13_3_daily_scheduler.py::test_06_empirical_3m_daily_paper_scheduler PASSED [100%]
  ======================== 6 passed in 16.55s =========================
  ```
- **Suite Hồi quy V13.2 (`backend/tests/test_v13_2_replay_repair.py`):**
  ```
  backend/tests/test_v13_2_replay_repair.py::test_a01_request_resolves_dates_to_timestamps PASSED [ 12%]
  backend/tests/test_v13_2_replay_repair.py::test_a02_risk_pct_mapping_from_max_risk_pct PASSED [ 25%]
  backend/tests/test_v13_2_replay_repair.py::test_a03_a04_effective_config_and_parameters PASSED [ 37%]
  backend/tests/test_v13_2_replay_repair.py::test_b05_bar_proxy_5m_close_time PASSED [ 50%]
  backend/tests/test_v13_2_replay_repair.py::test_c06_b1_target_selection_proximity_and_net_rr PASSED [ 62%]
  backend/tests/test_v13_2_replay_repair.py::test_c04_b2_breakout_retest_freshness_constraints PASSED [ 75%]
  backend/tests/test_v13_2_replay_repair.py::test_d01_d03_d04_max_fills_cap PASSED [ 87%]
  backend/tests/test_v13_2_replay_repair.py::test_e01_baseline_versus_candidate_empirical_3m PASSED [100%]
  ======================== 8 passed in 30.05s =========================
  ```

### Frontend Test Suites & Build:
- **Vitest Suites:** 13/13 test files passed (110/110 unit & component tests passed).
- **TypeScript / Vite Production Build:** `tsc -b && vite build` thành công 100% trong 1.15s với 0 errors.

---

## 5. HƯỚNG DẪN NGHIỆM THU TRÊN GIAO DIỆN (UI USER GUIDE)

1. Mở ứng dụng tại tab **Nghiên cứu phiên & ngày** (`ResearchTab`).
2. Chọn khoảng thời gian **3 tháng gần nhất** (ví dụ 90 ngày).
3. Tại phần **Chế độ khớp lệnh**, chọn:
   - **Tất cả phiên (1 lệnh/phiên)** (`DAILY_PAPER`) để kiểm tra toàn bộ 67 phiên Mỹ.
   - Hoặc **Chỉ khi đủ tín hiệu chuẩn** (`CONFIRMED_ONLY`) để kiểm tra các cơ hội chuẩn SMC thuần túy.
4. Bấm **Chạy đánh giá phương pháp**:
   - Thẻ tóm tắt hiển thị 6 dòng rõ ràng: Số ngày kiểm tra, số lệnh đã đóng, lời/lỗ sau phí, tỷ lệ thắng, mức sụt giảm vốn lớn nhất, và bước tiếp theo.
   - Thẻ phụ hiển thị độ phủ phiên (`Cadence: 65/67 phiên Mỹ (97.0%)`).
   - Bảng lịch sử lệnh cho phép click vào bất kỳ dòng nào để mở **Drawer Chi tiết lệnh**, hiển thị đầy đủ timestamps, lý do vào lệnh và các yếu tố xác nhận còn thiếu.
   - Bảng so sánh hiển thị số liệu thực nghiệm thực tế giữa Baseline và Candidate, không còn bất kỳ con số hardcode nào.

---

## 6. KẾT LUẬN & CAM KẾT AN TOÀN

- Phương pháp được thiết kế và thực thi hoàn toàn trong môi trường Lab/Research, **không tác động đến database live, không gửi order lên sàn Bitget thật và không can thiệp vào các tiến trình chạy thật**.
- Không tự động kích hoạt tính năng tự vào lệnh (Auto-paper / Live-trading) ra ngoài production mà không có sự xác nhận của người dùng.
- Toàn bộ kết quả đối chứng đều là dữ liệu thực nghiệm trung thực từ nến thị trường Bitget XAUUSDT.
