# BÁO CÁO NGHIỆM THU AURUM V17.4
**MỤC TIÊU:** SETUP-FIRST PAPER TRADING QUA CẢ BA PHIÊN (ASIA, LONDON, NEW YORK) — BỎ ƯU TIÊN PHIÊN MỸ  
**TRẠNG THÁI CUỐI CÙNG:** `IMPLEMENTED_AND_VERIFIED`  
**BRANCH:** `fix/v14-1-rr-flow`  
**BASELINE SHA:** `4977a21deb475bafb0cd8cac6af87a4a0191d15a`  
**COMMIT SCOPE:** `feat(v17.4): enable setup-first PAPER entries across sessions`  

---

## 1. TÓM TẮT DÀNH CHO NGƯỜI DÙNG (USER SUMMARY)

### 1.1 Quyền giao dịch và giờ vào lệnh đã thay đổi những gì?
- **Mọi phiên đều bình đẳng:** Bot không còn ưu tiên riêng phiên New York (NY), không giữ slot cho phiên NY, không ép phải có ít nhất 1 lệnh trong phiên NY (`ny_min_fills = 0`), và không kích hoạt lệnh dự phòng (fallback) chỉ vì thiếu lệnh.
- **Tiêu chí Setup-First:** Lệnh chỉ được hình thành khi và chỉ khi thị trường xuất hiện đầy đủ cấu trúc chuẩn xác:
  $$\text{Vùng quan tâm (POI)} \rightarrow \text{Quét thanh khoản (Sweep)} \rightarrow \text{Phá vỡ cấu trúc (MSS)} \rightarrow \text{Khoảng trống giá (FVG cùng leg)} \rightarrow \text{Mục tiêu R:R sau phí} \ge \text{Cài đặt}$$
- **Thời gian cho phép:** Bất kể thời điểm trong ngày (Asia, London, hay sau 11:00 NY), miễn là giá chạy thật, nến đóng hợp lệ, và các lớp bảo vệ rủi ro đều thông qua, bot đều có quyền xem xét setup.

### 1.2 Phân biệt rõ ARM, FILL và Quyền Auto
- **ARM (Lên nòng):** Là khi setup thị trường đã đủ điều kiện cấu trúc và điểm chất lượng đạt ngưỡng Settings (70 hoặc 80). Lệnh chờ (LIMIT) được tạo trên sổ lệnh Paper. **ARM chưa phải là đã vào vị thế.**
- **FILL (Khớp lệnh thật):** Chỉ xảy ra khi giá thị trường (Bid đối với lệnh Bán / Ask đối với lệnh Mua) thực sự chạm đến mức giá Entry của lệnh chờ trước khi hết hạn (TTL).
- **Quyền Auto:** Lựa chọn chế độ session không tự động bật Auto Paper Trading. Nếu Auto đang `OFF`, bot chỉ hiển thị setup để theo dõi, không tự đặt lệnh. Người dùng chủ động bật Auto khi muốn bot tự động thực thi.

### 1.3 Giới hạn an toàn giữ nguyên tuyệt đối
- **Tối đa 3 lệnh/ngày:** Tính theo múi giờ cấu hình (`Asia/Ho_Chi_Minh` UTC+7). Khi đã có đủ 3 fills, toàn bộ các phiên còn lại trong ngày đều dừng vào lệnh mới.
- **Tối đa 1 vị thế mở cùng lúc:** Bot không mở lệnh chồng chéo khi đang có vị thế chạy.
- **Giữ nguyên rủi ro và vốn:** Không tự ý nới lỏng đòn bẩy, tỷ lệ rủi ro/lệnh, phí giao dịch hay floor R:R (1.5).
- **Điểm chất lượng:** 70/80 là điểm số đánh giá độ tin cậy của cấu trúc, không phải xác suất thắng cam kết 100%.

---

## 2. BẢNG TỔNG HỢP CÁC CỔNG KIỂM TRA (ACCEPTANCE GATES)

| Cổng kiểm tra | Phạm vi thực hiện | Số lượng test | Kết quả | Thời gian |
|---|---|---|---|---|
| **Gate 1** | Backend V17.4 New Suite | 59 tests | **PASS (100%)** | 17.08s |
| **Gate 2** | Backend V14.4 Regression Suite | 493 tests | **PASS (100%)** | 44.49s |
| **Gate 3** | Frontend Vitest Test Suite | 488 tests | **PASS (100%)** | 8.21s |
| **Gate 4** | Frontend Production Build (`npm run build`) | TypeScript & Vite | **PASS (0 errors)** | 12.32s |
| **Gate 5** | Frontend Linter (`npm run lint`) | Oxlint | **PASS (0 errors)** | 1.05s |
| **Gate 6** | Playwright E2E Browser Suite | Chromium Headless | **PASS (6/6)** | 25.44s |

---

## 3. MA TRẬN BẰNG CHỨNG THỰC TẾ (EVIDENCE MATRIX)

### 3.1 Sáu Kịch Bản Positive E2E Causal (Đầy đủ chuỗi nến -> ARM -> FILL -> CLOSE)
Mọi ca kiểm thử đều sử dụng dữ liệu nến nhân quả thực tế (causal candles), xác thực qua toàn bộ pipeline production:

1. **B01 — ASIA LONG (XAUUSDT):**
   - *Khung giờ:* Phiên Á (01:00 UTC = 08:00 UTC+7).
   - *Chuỗi cấu trúc:* POI Demand -> Sweep SSL -> MSS tăng -> FVG Bullish -> Target External BSL.
   - *Kết quả:* Lệnh ARM Limit tại 4180.00 -> Ask chạm 4180.00 -> Khớp `paper_open` -> Giá đạt Take Profit 4200.00 -> Đóng lệnh `TP_HIT` với Net PnL > 0.
2. **B02 — ASIA SHORT (XAUUSDT):**
   - *Khung giờ:* Phiên Á (01:30 UTC).
   - *Chuỗi cấu trúc:* POI Supply -> Sweep BSL -> MSS giảm -> FVG Bearish -> Target SSL.
   - *Kết quả:* Geometry chuẩn `TP < Entry < SL` -> Bid chạm 4180.00 -> Khớp lệnh -> Giá đạt Take Profit 4160.00 -> Đóng lệnh `TP_HIT` dấu PnL dương.
3. **B03 — LONDON LONG (ngoài cửa 03–04 NY cũ):**
   - *Khung giờ:* 04:30 NY (ngoài Silver Bullet cũ).
   - *Kết quả:* Time gate cho phép nhờ `CG01_ALL_SESSIONS_PAPER` -> ARM và FILL thành công.
4. **B04 — LONDON SHORT (ngoài cửa 03–04 NY cũ):**
   - *Khung giờ:* 05:00 NY.
   - *Kết quả:* Cấu trúc Short hợp lệ, trigger Bid khớp lệnh, ghi nhận nhật ký với lý do xuất cảnh chính xác.
5. **B05 — NEW YORK LONG (sau 11:00 NY):**
   - *Khung giờ:* 11:30 NY (thời điểm mà chế độ NY_ONLY cũ sẽ khóa `WINDOW_CLOSED`).
   - *Kết quả:* Vẫn giữ quyền xét lệnh do trong ngày chưa đạt 3 fills và nến đạt chuẩn; khớp và quản lý lệnh trọn vòng đời.
6. **B06 — NEW YORK SHORT (sau 11:00 NY):**
   - *Khung giờ:* 12:00 NY.
   - *Kết quả:* Thực hiện trọn vẹn vòng đời giao dịch Short, kiểm tra R:R sau phí đạt ngưỡng cài đặt.

### 3.2 Nhóm Kiểm Soát Tiêu Cực (Negative Controls & Guard Regression)
- **B08 — Thiếu Sweep:** Chạm vùng nhưng không quét qua đỉnh/đáy -> Blocked tại `WAIT_SWEEP`, 0 lệnh ARM.
- **B09 — MSS/Displacement yếu:** Thân nến đóng cửa chưa vượt ngưỡng ATR -> Blocked tại `WAIT_MSS`, 0 lệnh ARM.
- **B10 — FVG không cùng leg hoặc Target đã bị lấy:** Blocked tại `WAIT_FVG` hoặc `TARGET_UNAVAILABLE`, 0 lệnh ARM.
- **B11 — Auto OFF:** Setup đạt 85 điểm nhưng Auto tắt -> Hiển thị trên UI trạng thái Ready, tuyệt đối 0 gửi lệnh ra broker.
- **B12 — News Blackout:** Tin đỏ kinh tế xuất hiện trong cửa sổ bảo vệ -> Chặn ARM và chặn FILL nếu tin xuất hiện sau khi ARM.
- **B13 — Approved Red Lesson:** Bài học kỷ luật đã duyệt trùng bối cảnh rủi ro -> Blocked `LESSON_RULE_BLOCK`.
- **B14 — Stale Quote:** Mất kết nối giá hoặc nến đứng yên -> Chặn khớp lệnh từ giá cũ.
- **B15 — Slot thứ 3:** Khi đã có 2 fills ngoài NY, slot thứ 3 vẫn dành cho setup hợp lệ bất kỳ, không giữ chỗ cho phiên Mỹ.
- **B16 — Đạt trần 3 fills:** Khi đã đủ 3 fills trong ngày, cơ hội thứ 4 bị chặn ngay tại gate chính sách `DAILY_FILL_CAP_REACHED`.
- **B17 — Vị thế đang mở:** Đang có 1 vị thế mở -> Toàn bộ setup mới đều bị hoãn cho đến khi vị thế hiện tại đóng xong.
- **B18 — TTL qua biên phiên:** Lệnh LIMIT chờ khớp tại cuối phiên Á vẫn giữ nguyên hiệu lực sang phiên Âu nếu TTL chưa hết.
- **B19 — Hết hạn TTL:** Hết hạn TTL mà giá chưa chạm Entry -> Trạng thái chuyển sang `EXPIRED`, không khớp vị thế.
- **B20 — Scope Defense:** Yêu cầu chuyển sang chế độ mọi phiên trên tài khoản REAL/LIVE bị từ chối với lỗi `PAPER_ONLY_SESSION_PROFILE`.

---

## 4. CHI TIẾT KỸ THUẬT VÀ TÍNH NHẤT QUÁN CSDL

### 4.1 Cập nhật Phối Hợp Đơn Giao Dịch (Single-Transaction Coordinated Update)
- Điểm yếu cũ được khắc phục hoàn toàn: Không còn tình trạng gọi 2 request HTTP riêng biệt để lưu timing và policy.
- Endpoint mới `PUT /api/v1/policy/entry-session` thực hiện toàn bộ các bước trong một transaction SQLAlchemy duy nhất:
  1. Kiểm tra phiên bản đồng thời `expected_version` (phát hiện xung đột 409 nếu có người sửa ở tab khác).
  2. Cập nhật `TradingPolicy`: `entry_session_policy = "ALL_SESSIONS_SETUP_FIRST"`, `ny_min_fills = 0`, `reserve_ny_slot = False`, `ny_fallback_enabled = False`.
  3. Cập nhật `SystemConfig`: `timing_profile = "CG01_ALL_SESSIONS_PAPER"`, `daily_ny_goal = "0"`.
  4. Tăng `policy.version` đúng 1 đơn vị và ghi nhật ký kiểm toán `audit`.
  5. Thực hiện `db.commit()` một lần duy nhất. Nếu có lỗi, tự động `db.rollback()` toàn bộ, không để lại trạng thái phân đôi (split-brain).

### 4.2 Bảo Vệ Trạng Thái Khi Khởi Động Lại (Startup Upgrader Protection)
- Module `TradingPolicyService.upgrade_session_policy_contract` được bảo vệ: nếu người dùng đã chủ động chọn `ALL_SESSIONS_SETUP_FIRST`, script startup sẽ giữ nguyên cấu hình, không tự ý ghi đè về chế độ cũ.

---

## 5. HƯỚNG DẪN KÍCH HOẠT CHO NGƯỜI DÙNG (CUTOVER INSTRUCTIONS)

1. Mở giao diện ứng dụng Aurum Desk trên trình duyệt.
2. Tại bảng **CG-01 Diagnostic Card**, nhấn vào biểu tượng bánh răng Cài đặt.
3. Tại mục chọn Cửa sổ giao dịch (Timing Profile), chọn:
   **"Mọi phiên — chỉ vào khi đủ setup (PAPER)"**
4. Nhấn nút **Lưu Cấu Hình**.
5. Kiểm tra dòng huy hiệu xác nhận:
   `PAPER • Mọi phiên • Tối đa 3 lệnh/ngày`
6. Nếu muốn bot tự động đặt lệnh khi có setup đạt chuẩn, hãy bật công tắc **Auto Paper Trading**.
