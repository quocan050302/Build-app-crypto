# BÁO CÁO NGHIỆM THU AURUM DESK V13
## Nghiên Cứu Phiên & Ngày Theo Thời Điểm, Bộ Lọc Ngày Hiện Tại/Quá Khứ, Replay Quyết Định và Kiểm Định Cải Tiến

**Mã phiên bản:** V13 — Daily Research & Causal Decision Replay  
**Thời gian hoàn thành:** 2026-10-10  
**Repository:** https://github.com/quocan050302/Build-app-crypto  
**Nhánh:** `feature/aurum-repair-smc-rr`  
**Trạng thái kiểm thử:** 100% Passed (Backend 509 tests + 9 V13 acceptance tests; Frontend 12 test files, 103 tests)

---

### 1. TỔNG QUAN VÀ MỤC TIÊU V13

Phiên bản V13 giải quyết trọn vẹn bài toán nghiên cứu thị trường có tính thời gian và nhân quả (Causal Research):
1. **Phân chia 3 không gian độc lập trên giao diện người dùng:**
   - **Nghiên Cứu Phiên & Ngày (`reports`):** Phân tích trạng thái thị trường tại thời điểm chọn (`as_of`), xác định cấu trúc Swing/POI/FVG, kịch bản giao dịch LONG/SHORT/NO_TRADE với Net R:R, và đánh giá sau phiên (MFE/MAE).
   - **Phòng Kiểm Thử Rủi Ro → Replay Lịch Sử (`lab`):** Thực hiện backtest 3 tháng, kiểm định walk-forward, so sánh các biến thể chiến lược.
   - **Nhật Ký & Bài Học (`journal`):** Đánh giá từng lệnh đã khớp, phê duyệt bài học có bằng chứng (`PENDING_REVIEW` -> `APPROVED`).
2. **Loại bỏ hoàn toàn rò rỉ tương lai (Future Leakage & Live Poisoning):**
   - Khi chọn một ngày/thời điểm trong quá khứ, hệ thống tuyệt đối không dùng singleton `collector_service` của ngày hôm nay.
   - Toàn bộ nến, pivot và cấu trúc đa khung thời gian D/4H/1H/15M chỉ được tính toán từ các nến có `close_time <= as_of_ms`.
3. **Thân thiện với người mới bắt đầu (Beginner-Friendly Vietnamese UI):**
   - Trực quan hóa thị trường bằng tiếng Việt tự nhiên, giải thích rõ trạng thái, điều kiện đang chờ, vùng giá hợp lệ và lý do từ chối vào lệnh.
   - Bám sát kỷ luật 1–3 lệnh/ngày, ưu tiên phiên Mỹ (New York), tôn trọng tuyệt đối các chốt an toàn rủi ro (Hard Guards).

---

### 2. KIẾN TRÚC THỜI GIAN NHÂN QUẢ (CAUSAL TIME CONTEXT)

Hệ thống bổ sung module `backend/research_context.py` với cấu trúc bất biến `ResearchContext`:
- `symbol`: Cặp giao dịch (`XAUUSDT` / Vàng).
- `mode`: 
  - `CURRENT_ASOF`: Chụp nhanh trạng thái thị trường hiện tại.
  - `HISTORICAL_ASOF`: Nghiên cứu tại một thời điểm quá khứ xác định.
  - `POST_SESSION_REVIEW`: Đánh giá diễn biến sau phiên mà không thay đổi kế hoạch ban đầu.
- `selected_date`: Ngày nghiên cứu (định dạng YYYY-MM-DD).
- `date_basis`: `VN_DATE` (theo múi giờ Asia/Ho_Chi_Minh) hoặc `NY_SESSION_DATE` (theo múi giờ America/New_York).
- `as_of_ms`: Mốc thời gian UTC (milliseconds) tối đa mà hệ thống được phép nhìn thấy dữ liệu.
- `selected_session`: `ALL`, `TOKYO`, `LONDON`, hoặc `NEW_YORK`.

**Nguyên tắc xử lý dữ liệu:**
- **Boundary nến đóng:** Nến chưa đóng tại thời điểm `as_of_ms` không được công nhận là cấu trúc hoàn tất.
- **Pivot xác nhận:** Pivot Swing High/Low chỉ được ghi nhận khi có nến đóng phía bên phải (right-side confirmation) trước hoặc tại `as_of_ms`.
- **Miễn dịch với Live Collector:** Khi `mode = HISTORICAL_ASOF`, engine tự động ngắt kết nối với live collector và tổng hợp cấu trúc trực tiếp từ dữ liệu lịch sử đã đóng.

---

### 3. PHÂN LOẠI TRẠNG THÁI THỊ TRƯỜNG (MARKET REGIME EVALUATOR)

Module `backend/market_regime.py` phân loại trạng thái thị trường nhân quả trước khi xét điều kiện vào lệnh:
- **TREND_UP (Xu Hướng Tăng):** Cấu trúc đỉnh/đáy cao dần (HH/HL) trên khung H1/15M, giá nằm trên EMA20/50. Phù hợp thiết lập B1 Tiếp Diễn Xu Hướng (Long).
- **TREND_DOWN (Xu Hướng Giảm):** Cấu trúc đỉnh/đáy thấp dần (LH/LL) trên khung H1/15M, giá nằm dưới EMA20/50. Phù hợp thiết lập B1 Tiếp Diễn Xu Hướng (Short).
- **RANGE (Đi Ngang Tích Lũy):** Biên độ dao động nén chặt trong Dealing Range. Phù hợp thiết lập B2 Phá Vỡ / Kiểm Tra Lại (Breakout/Retest).
- **TRANSITION (Chuyển Giao Xu Hướng):** Xuất hiện CHOCH hoặc phân kỳ đa khung thời gian. Khuyến nghị đứng ngoài hoặc giảm khối lượng.
- **EVENT_VOLATILITY (Biến Động Tin Tức):** Nằm trong cửa sổ tin tức đỏ (CPI, NFP, FOMC). Kích hoạt cấm giao dịch (News Blackout).
- **UNKNOWN (Chưa Rõ Ràng):** Dữ liệu không đủ nến để xác định hoặc tín hiệu mâu thuẫn.

---

### 4. XÂY DỰNG KỊCH BẢN GIAO DỊCH CHUẨN XÁC HÌNH HỌC (SCENARIO GEOMETRY & NET R:R)

Module `backend/scenario_builder.py` đảm bảo tính khả thi về mặt toán học và chi phí thực tế:
- **Hình học Long:** Bắt buộc `Stop Loss < Entry < Take Profit`.
- **Hình học Short:** Bắt buộc `Take Profit < Entry < Stop Loss`.
- **Mô hình chi phí thực tế (Bitget Classic Standard):**
  - Phí chênh lệch mua bán (Spread): zsh.20/oz.
  - Trượt giá khớp lệnh (Slippage): zsh.10/oz.
  - Phí giao dịch (Commission): 0.04% giá trị vị thế.
  - Phí cấp vốn (Funding cost): zsh.05/oz.
- **Kiểm định Net R:R:**
  77985	ext{Net RR} = rac{	ext{Gross Reward} - 	ext{Total Cost}}{	ext{Gross Risk} + 	ext{Total Cost}}77985
  Hệ thống yêu cầu $	ext{Net RR} \ge 1.80R$. Nếu $	ext{Net RR} < 1.80R$ hoặc điểm kích hoạt vượt quá mục tiêu, kịch bản bị đánh dấu `INVALID` kèm giải thích tiếng Việt rõ ràng, tuyệt đối không tạo tín hiệu giả.

---

### 5. ĐÁNH GIÁ KẾT QUẢ SAU PHIÊN & MFE/MAE (POST-SESSION REVIEW)

Module `backend/research_review.py` cung cấp công cụ phân tích sau khi phiên đã kết thúc (`outcome_cutoff`):
- **MFE (Maximum Favorable Excursion):** Biên độ giá di chuyển tối đa theo hướng có lợi tính bằng USD và R.
- **MAE (Maximum Adverse Excursion):** Độ sụt giảm giá tối đa bất lợi trước khi đạt mục tiêu hoặc chạm SL.
- **Phân định rạch ròi SỰ THẬT (FACTS) vs GIẢ THUYẾT (HYPOTHESES):**
  - **Facts:** Mức giá MFE đạt được, mức MAE phải chịu, thời điểm kết thúc phiên, trạng thái tin tức tại thời điểm đó.
  - **Hypotheses:** Giả định dời SL hòa vốn sớm, giả định mục tiêu 1R hay 2R tối ưu hơn.
- **Đề xuất bài học (Candidate Lessons):** Được lưu ở trạng thái `PENDING_REVIEW`, yêu cầu người dùng phê duyệt thủ công trong tab Nhật Ký trước khi có hiệu lực, ngăn chặn việc tự ý thay đổi chiến lược live bot sau mỗi lệnh thua.

---

### 6. GIAO DIỆN NGƯỜI DÙNG TAB NGHIÊN CỨU PHIÊN & NGÀY (FRONTEND)

Được triển khai trong `frontend/src/ResearchTab.tsx` và tích hợp vào `frontend/src/App.tsx` (tab ID: `reports`):
1. **Thanh điều khiển trực quan:**
   - Bộ chọn ngày với các nút chọn nhanh: **Hôm nay**, **Hôm qua**, **7 ngày**, **30 ngày**.
   - Bộ chọn giờ: "Tại thời điểm" (mặc định 08:30 phiên Mỹ).
   - Bộ chọn phiên: **Tất cả**, **Phiên Á (Tokyo)**, **Phiên Âu (London)**, **Phiên Mỹ (New York)**.
   - Nút chuyển đổi cơ sở ngày: **Ngày VN (UTC+7)** / **Ngày Phiên NY (UTC-4/5)**.
   - Nút hành động: "Phân Tích Tại Thời Điểm Đã Chọn", "Xem Báo Cáo Đã Lưu", "Đánh Giá Diễn Biến Sau Phiên".
2. **Sáu thẻ thông tin thân thiện cho người mới:**
   - **Thẻ 1: Trạng thái & Tóm tắt:** Nhận định thị trường bằng tiếng Việt tự nhiên kèm chỉ dẫn "Đang chờ điều kiện gì...".
   - **Thẻ 2: Ma trận đa khung thời gian:** Xu hướng và cấu trúc nến đóng trên D, H4, H1, M15.
   - **Thẻ 3: Bản đồ cấu trúc & Thanh khoản:** Swing High/Low, Vùng mất cân bằng giá (FVG), Khối lệnh (Order Block).
   - **Thẻ 4: Kịch bản giao dịch (LONG / SHORT / NO_TRADE):** Vùng vào lệnh, điều kiện kích hoạt, Stop Loss, Take Profit, Net R:R sau phí.
   - **Thẻ 5: Đánh giá sau phiên (MFE / MAE):** Biên độ biến động thực tế sau phiên, sự thật vs giả thuyết, đề xuất bài học rút ra.
   - **Thẻ 6: Lịch sử báo cáo:** Danh sách các báo cáo đã phân tích trong ngày để tiện tra cứu và so sánh.

---

### 7. KẾT QUẢ KIỂM THỬ NGHIỆM THU (TEST SUITE RESULTS)

#### Backend Test Suite
- File kiểm thử V13 chuyên biệt: `backend/tests/test_v13_acceptance.py`
  - `test_r01_legacy_api_compatibility`: PASSED
  - `test_r02_historical_request_immune_to_live_collector_poisoning`: PASSED
  - `test_r03_prefix_invariance_and_close_time_cutoff`: PASSED
  - `test_r05_date_basis_and_iana_timezone_handling`: PASSED
  - `test_r06_r07_future_date_handling_and_limitations`: PASSED
  - `test_r08_scenario_geometry_and_net_rr_validation`: PASSED
  - `test_r09_r12_market_regime_causal_evaluation`: PASSED
  - `test_r10_r11_r13_post_session_review_and_mfe_mae`: PASSED
  - `test_r14_r17_r18_api_filters_and_detail_routes`: PASSED
- Toàn bộ suite hồi quy backend: **509 passed, 4 skipped, 0 failed**.

#### Frontend Test Suite
- File kiểm thử V13 chuyên biệt: `frontend/src/v13_research_tab.test.ts` (4 tests PASSED)
- Toàn bộ suite kiểm thử frontend (Vitest): **12 test files passed, 103 passed, 0 failed (100%)**.
- TypeScript type check & production build: **0 errors, built successfully in 806ms**.
- Oxlint linter: **0 errors, 14 clean warnings (0 warnings in ResearchTab)**.

---

### 8. GIỚI HẠN KỸ THUẬT VÀ KHUYẾN CÁO AN TOÀN

1. **Nguồn cấp dữ liệu:** Dữ liệu giá vàng dựa trên hợp đồng XAUUSDT Bitget Classic; không đại diện cho toàn bộ khối lượng COMEX toàn cầu.
2. **Quyền hạn hệ thống:** Mọi phân tích và kịch bản trong Tab Nghiên Cứu đều mang tính chất cố vấn (View-Only), không tự ý phát sinh lệnh vào tài khoản live hoặc can thiệp vị thế thực.
3. **Kỷ luật giao dịch:** Mục tiêu nghiên cứu 1–3 lệnh chất lượng cao mỗi ngày (ưu tiên phiên Mỹ). Nếu thị trường biến động quá mạnh hoặc vi phạm quy tắc an toàn, hệ thống kiên quyết đề xuất đứng ngoài (NO_TRADE).
