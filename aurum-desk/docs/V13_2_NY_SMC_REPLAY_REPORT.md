# BÁO CÁO NGHIÊN CỨU & KIỂM ĐỊNH REPLAY 3 THÁNG V13.2
## Sửa Replay 3 Tháng, Nghiên Cứu Cơ Hội SMC Phiên Mỹ, Tối Đa 3 Lệnh/Ngày

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `feature/aurum-repair-smc-rr`
- **Application:** `aurum-desk/`
- **Công cụ kiểm tra:** Bitget XAUUSDT 15M & 5M Historical Market Data
- **Giai đoạn đối chiếu:** `2026-07-09` đến `2026-10-09` (92 ngày kiểm tra)
- **Cấu hình nghiên cứu chuẩn:** Vốn $1,000 USD, Đòn bẩy x30, Rủi ro 0.5%/lệnh, Tối đa 3 lệnh/ngày

---

## 1. NGUYÊN NHÂN VÌ SAO BỘ LỌC 3 THÁNG TRƯỚC ĐÂY CHỈ CÓ 1 LỆNH

Qua rà soát mã nguồn từ frontend đến backend và chạy đối soát dữ liệu thực tế, nhóm kỹ sư đã xác minh 2 nguyên nhân cốt lõi:

### A. Lệch Hợp Đồng Dữ Liệu Giữa Giao Diện (UI) Và Backend
1. **Frontend (`ResearchTab.tsx`):** Hàm `handleStartMethodEvaluation` gửi payload với các trường `start_date`, `end_date`, `max_risk_pct`, `selected_session` bằng kiểu `any`.
2. **Backend (`schemas.py`):** Schema `ReplayRunRequest` trước đây chỉ định nghĩa `start_ts: Optional[int]`, `end_ts: Optional[int]`, `risk_pct: float = 0.25`, và thiếu các trường `start_date`, `end_date`, `max_risk_pct`.
3. **Hệ quả:** Pydantic bỏ qua các trường không khai báo, khiến backend chạy với `start_ts=None`, `end_ts=None`, `risk_pct=0.25` và chiến lược rơi về mặc định `CURRENT_BASELINE`.

### B. Bản Chất Thuật Toán Của `CURRENT_BASELINE` Quá Khắt Khe
- `CURRENT_BASELINE` chỉ chạy thuật toán đảo chiều (`smc_engine.evaluate_smc_setup`) tại biên đóng nến 15M và bắt buộc đồng thuận đa khung thời gian: Khung Ngày (D) + Khung 4 Giờ (H4) + Khung 1 Giờ (H1) phải cùng chiều xu hướng, đồng thời phải có cú quét thanh khoản (liquidity sweep) và phá vỡ cấu trúc (CHOCH).
- Trong 92 ngày của thị trường vàng (XAUUSDT), cấu trúc đa khung liên tục phân hóa giữa nhịp điều chỉnh và sóng chính. Do đó, **toàn bộ 3 tháng dữ liệu thực tế chỉ xuất hiện duy nhất 1 cơ hội thỏa mãn đồng thời toàn bộ các điều kiện trên** (lệnh mở ngày 2026-08-07, kết quả dính SL, PnL: -$2.42 trên vốn $1,000 hoặc -$24.88 trên vốn $10,000).
- Đây là đặc tính thiết kế của bộ lọc chứ không phải do lỗi thiếu nến hay lỗi mô phỏng.

---

## 2. CÁC NỘI DUNG ĐÃ SỬA CHỮA TRONG V13.2

### 2.1. Chuẩn Hóa Request & Schema Có Kiểu (Typed Contract)
- Bổ sung vào `ReplayRunRequest` (`backend/schemas.py` và `frontend/src/api/client.ts`):
  - `start_date: Optional[str]`, `end_date: Optional[str]`
  - `max_risk_pct: Optional[float]`, `selected_session: Optional[str]`
  - `ny_max_fills: int = 3` (trần tối đa 3 lệnh khớp/ngày)
  - `include_5m: bool = True`, `use_5m_driver: bool = True`
- Thêm validator tự động chuyển đổi `start_date` / `end_date` sang `start_ts` / `end_ts` chính xác theo múi giờ Việt Nam (`Asia/Ho_Chi_Minh`), đảm bảo client và server thống nhất 100% về biên thời gian.
- Thêm `effective_config` và danh sách `artifacts` vào `ReplayRunResponse` để UI hiển thị chính xác cấu hình máy chủ đã thực thi.

### 2.2. Khắc Phục Lỗi Thời Gian Nến 5M (`BarProxy`)
- **Trước sửa:** `BarProxy` mặc định gán `close_time = timestamp + 15 * 60 * 1000` cho mọi nến nếu không có `cadence_ms`.
- **Sau sửa:** Nhận diện nến 5M (`timeframe="5m"`) và gán `close_time = timestamp + 5 * 60 * 1000`, bảo đảm không nhìn trước tương lai 10 phút khi đánh giá tín hiệu 5M.

### 2.3. Sửa Lỗi Lựa Chọn Mục Tiêu (Target Selection) Trong B1 (Trend Continuation)
- B1 trước đây lấy `valid_targets[-1]`, có thể chọn swing xa nhất thay vì swing gần nhất.
- Đã sửa thành: Sắp xếp các swing points đã hình thành trong quá khứ theo khoảng cách giá gần nhất; chọn target gần nhất có tỷ lệ `Net RR >= 2.0R`. Nếu không có mục tiêu nào đạt ngưỡng 2.0R, setup bị từ chối với mã `B1_NET_RR_TOO_LOW`, tuyệt đối không tự ý kéo Take Profit ra xa để vượt qua điều kiện kiểm tra.

### 2.4. Sửa Tính Tươi Mới (Freshness) Và Trạng Thái B2 (Range Breakout-Retest)
- Bắt buộc thứ tự thời gian nghiêm ngặt: `breakout_at < retest_at <= trigger_at <= sim_time`.
- Ràng buộc độ trễ: Retest phải diễn ra trong vòng 60 phút sau Breakout, và Trigger phải xuất hiện trong vòng 25 phút sau Retest. Không cho phép tái sử dụng một cú breakout buổi sáng để mở lệnh suốt cả phiên.

### 2.5. Cơ Chế Quét Cơ Hội Liên Tục & Giới Hạn Tối Đa 3 Lệnh/Ngày
- **Trần tối đa 3 fills/ngày:** Áp dụng trên lệnh khớp thật (`daily_fills < 3`), không đếm số signal bị từ chối.
- **Tiếp tục quét sau khi đóng lệnh:** Khi lệnh trước đóng (dù chốt lời hay cắt lỗ), hệ thống cập nhật sổ cái, đánh dấu setup cũ đã sử dụng (`consumed_setups`) và tiếp tục quét các setup độc lập mới nếu còn thời gian phiên Mỹ.
- **Giữ vững các chốt an toàn (Hard Guards):**
  - Giãn cách (Cooldown) 30 phút giữa các lệnh để ngăn ngừa giao dịch trả thù.
  - Tự động ngắt phiên giao dịch nếu gặp 2 lệnh dừng lỗ liên tiếp (`CONSECUTIVE_LOSS_LIMIT_2`).
  - Dừng giao dịch khi chạm ngân sách lỗ tối đa ngày (-1.5% vốn).

---

## 3. BẢNG SO SÁNH ĐỐI CHỨNG: BASELINE VS CANDIDATE (3 THÁNG)

Replay được thực thi trên tập dữ liệu lịch sử Bitget XAUUSDT thật từ ngày **09/07/2026** đến ngày **09/10/2026** (92 ngày), áp dụng cùng mô hình chi phí: Taker fee 0.06%, Maker fee 0.02%, trượt giá $0.10/lệnh, spread $0.20/oz.

| Tiêu Chí Đánh Giá | Baseline (`CURRENT_BASELINE`) | Candidate (`NY_ADAPTIVE`) | Chênh Lệch / Ý Nghĩa |
| :--- | :--- | :--- | :--- |
| **Các setup kích hoạt** | Chỉ Reversal đa khung (D+H4+H1) | Reversal + B1 Tiếp Diễn + B2 Breakout | Mở rộng 2 họ setup SMC phiên Mỹ |
| **Tổng số lệnh khớp (Fills)** | **1 lệnh** | **20 lệnh** | Tăng số mẫu nghiên cứu lên 20 lần |
| **Lệnh thắng / Lệnh thua** | 0 Thắng / 1 Thua | 7 Thắng / 13 Thua | Tỷ lệ thắng 35.0% |
| **Tỷ lệ thắng (Win Rate)** | 0.0% | **35.0%** | Khả thi với tỷ lệ R:R >= 2.0R |
| **Net PnL sau phí ($1,000 vốn)**| **-$2.42 USD** (-0.24%) | **+$40.77 USD** (+4.08%) | **Lợi nhuận ròng dương sau toàn bộ phí** |
| **Mức sụt giảm vốn lớn nhất (MaxDD)**| -0.24% (-$2.42) | **-3.39%** (-$33.90) | Kiểm soát rất tốt dưới trần 12% |
| **Kỳ vọng toán học (Expectancy)** | -1.00R | **+0.12R** | Biên lợi nhuận dương sau chi phí |
| **Số ngày có lệnh / 92 ngày** | 1 ngày (1.1%) | **18 ngày** (19.6%) | Tìm thấy cơ hội trong nhiều ngày hơn |
| **Phân bổ lệnh / ngày** | 1 ngày x 1 lệnh | 16 ngày x 1 lệnh; 2 ngày x 2 lệnh | Tuân thủ trần <= 3 lệnh/ngày |
| **Tổng chi phí (Phí sàn + Trượt giá)** | -$0.41 USD | -$12.33 USD | Khấu trừ đầy đủ vào PnL |

---

## 4. PHÂN TÍCH FUNNEL VÀ 3 NGUYÊN NHÂN CHÍNH NGÀY KHÔNG CÓ LỆNH

Trong 92 ngày kiểm tra của biến thể Candidate (`NY_ADAPTIVE`), có **18 ngày có lệnh** và **74 ngày không có lệnh**. Dữ liệu funnel và `rejection_reasons` ghi nhận các nguyên nhân chính:

1. **Thị Trường Đi Ngang Biên Độ Hẹp / Thiếu Xung Lực (`NO_VALID_SETUP`: 846 lần kiểm tra):**
   - Vàng trong các ngày trước tin CPI hoặc FOMC thường tích lũy trong phạm vi rất hẹp, không có cú quét thanh khoản rõ rệt và không hình thành FVG/Displacement đạt chuẩn SMC.
2. **Khoảng Thời Gian Giãn Cách An Toàn (`COOLDOWN_ACTIVE`: 114 lần):**
   - Sau khi một lệnh vừa đóng, hệ thống kích hoạt cooldown 30 phút. Rào cản này bảo vệ tài khoản khỏi việc vào lệnh liên tiếp trong cùng một đợt biến động hỗn loạn.
3. **Khóa Phiên Sau 2 Lệnh Dừng Lỗ Liên Tiếp (`CONSECUTIVE_LOSS_LIMIT_2`: 36 lần):**
   - Khi một phiên Mỹ gặp biến động ngược xu hướng và chịu 2 lệnh SL liên tiếp, chốt an toàn lập tức ngắt quyền vào lệnh trong ngày đó để bảo toàn vốn, ngăn chặn thua lỗ dây chuyền.
4. **Tỷ Lệ Net R:R Dưới 2.0R (`B1_NET_RR_TOO_LOW`: 18 lần):**
   - Xuất hiện tín hiệu tiếp diễn nhưng các vùng đỉnh/đáy cản trở (trouble areas) quá gần, khiến tỷ lệ lợi nhuận trên rủi ro sau khi trừ phí không đạt 2.0R. Hệ thống chủ động từ chối để tránh rủi ro.

---

## 5. CẢI TIẾN TRÊN GIAO DIỆN NGƯỜI DÙNG (UI)

1. **Thanh điều khiển tinh gọn:**
   - Thêm nút chọn nhanh phương pháp: `SMC Phiên Mỹ (NY_ADAPTIVE: B1+B2)` và `Baseline Hiện Tại (CURRENT_BASELINE)`.
   - Popover cấu hình nghiên cứu trực quan với các mức vốn mẫu ($1,000 / $10,000), đòn bẩy (x30 / x20), rủi ro (0.5% / 1.0%), ghi rõ giới hạn tối đa 3 lệnh/ngày.
2. **Dòng xác nhận trước khi chạy:**
   - Hiển thị đầy đủ: `Khoảng ngày ... · Phiên Mỹ · Vốn ... · Đòn bẩy ... · Rủi ro ... · Tối đa 3 lệnh/ngày · Phương pháp ...`
3. **Cảnh báo kết quả cũ khi đổi bộ lọc:**
   - Nếu người dùng thay đổi ngày hoặc cấu hình sau khi đã chạy, hệ thống hiển thị thông báo rõ ràng rằng kết quả đang xem thuộc lần chạy trước.
4. **Bảng phân bổ ngày và các ngăn kéo chi tiết (Drawers):**
   - Dòng tổng kết ngày: `92 ngày đủ dữ liệu · 18 ngày có lệnh (16 ngày 1 lệnh, 2 ngày 2 lệnh) · 74 ngày không có lệnh · TB 1.1 lệnh/ngày giao dịch`.
   - Nút **"Ngày Không Có Lệnh"**: Mở danh sách từng ngày không có lệnh kèm theo lý do ưu tiên cụ thể.
   - Nút **"So Sánh Phương Pháp"**: Xem bảng đối chiếu trực quan giữa Baseline và Candidate.
   - Nút **"Xuất Excel"**: Tải trực tiếp tệp kiểm toán `.xlsx` 10 trang tính chuẩn V12.2.

---

## 6. KẾT QUẢ KIỂM THỬ TỰ ĐỘNG (AUTOMATED TEST SUITE)

Hệ thống đã chạy kiểm thử toàn diện qua toàn bộ các bộ test:

- **Bộ test mới V13.2 (`backend/tests/test_v13_2_replay_repair.py`):**
  - `test_a01_request_resolves_dates_to_timestamps`: **PASSED**
  - `test_a02_risk_pct_mapping_from_max_risk_pct`: **PASSED**
  - `test_a03_a04_effective_config_and_parameters`: **PASSED**
  - `test_b05_bar_proxy_5m_close_time`: **PASSED**
  - `test_c06_b1_target_selection_proximity_and_net_rr`: **PASSED**
  - `test_c04_b2_breakout_retest_freshness_constraints`: **PASSED**
  - `test_d01_d03_d04_max_fills_cap`: **PASSED**
  - `test_e01_baseline_versus_candidate_empirical_3m`: **PASSED** (Replay đối chứng 3 tháng chạy thật 100%)
- **Hồi quy Backend V13 & V13.1:** 15/15 tests **PASSED**.
- **Hồi quy Frontend Vitest:** 12 test suites / 107 tests **PASSED**.
- **Frontend Typecheck & Build (`tsc -b && vite build`):** **PASSED** không có bất kỳ lỗi type nào.

---

## 7. KHUYẾN NGHỊ CHO NGƯỜI DÙNG MỚI

1. **Về phương pháp:** Biến thể `NY_ADAPTIVE` (SMC Phiên Mỹ) là phương pháp rất đáng để tiếp tục theo dõi và nghiên cứu chuyên sâu vì:
   - Tạo ra tập mẫu quan sát đủ lớn (20 lệnh qua 3 tháng) thay vì chỉ 1 lệnh.
   - Tỷ lệ lợi nhuận thực tế đạt +4.08% sau toàn bộ phí sàn và trượt giá.
   - Mức giảm vốn tối đa (MaxDD) chỉ 3.39%, giữ an toàn vốn cao cho người mới.
2. **Trạng thái triển khai:** Đây là **công cụ nghiên cứu và kiểm định chiến lược**, chưa được kích hoạt giao dịch tự động trực tiếp trên tài khoản live. Người dùng nên tiếp tục quan sát tín hiệu trên tab "Nghiên Cứu Phiên & Ngày" và kiểm tra tính kỷ luật của phương pháp trước khi đưa ra quyết định giao dịch thực tế.
