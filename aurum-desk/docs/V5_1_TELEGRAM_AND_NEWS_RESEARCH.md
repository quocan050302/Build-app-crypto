# Báo Cáo Triển Khai V5.1 — Sửa Geometry SHORT/LONG, Telegram Contract và Nghiên Cứu URL Tin Tức XAUUSDT

**Dự án:** Aurum Desk — Paper Trading XAUUSDT & Phân Tích SMC  
**Phiên bản:** V5.1 (Tiếp nối V5 Repair & Risk Lab)  
**Branch:** `feature/aurum-repair-smc-rr`  
**HEAD Tham chiếu:** `e8925fbb389c8f20f7a8af0baddeeb5b0f47ce43`  
**Môi trường:** Local Paper XAUUSDT (Không lệnh thật, không API key thật, không VPS/tunnel)

---

## 1. Tóm Tắt & Nguyên Nhân Gốc Rễ (Root Cause Analysis)

### 1.1. Lỗi Telegram `ValueError: too many values to unpack (expected 3)`
- **Hiện tượng trên ảnh người dùng:** Khi nhấn nút "Gửi tin thử nghiệm", hệ thống báo lỗi 500: `ValueError: too many values to unpack (expected 3)`.
- **Nguyên nhân gốc rễ:** `send_telegram_direct()` trong `backend/services/telegram_service.py` trả về một 4-tuple: `(success, provider_message_id, error_message, retry_after_sec)`. Trong khi đó, endpoint `POST /api/v1/telegram/test` trong `backend/main.py` lại giải nén thành 3 giá trị: `success, msg_id, err = await send_telegram_direct(...)`.
- **Giải pháp dứt điểm:**
  - Chuẩn hóa thành dataclass typed `TelegramSendResult` với các trường tường minh: `success`, `provider_message_id`, `error_code`, `error_message`, `retry_after_sec`, `http_status`.
  - Hỗ trợ giao thức `__iter__` và `__getitem__` cho 4-tuple để tương thích ngược 100% với các callers hiện có mà không gây runtime drift.
  - Cập nhật endpoint `POST /api/v1/telegram/test` xử lý lỗi có phân loại (401, 403, 429 với retry_after), tránh nuốt mã lỗi thành 500 hoặc báo nhầm lỗi token.
  - Phân biệt rõ trên UI: "Gửi tin thử nghiệm thành công" với trạng thái "Thông báo tự động đang TẮT" (cần bật & lưu để nhận tin realtime).

### 1.2. Geometry SHORT Bị Ngược Hướng & Preview R:R Giả
- **Hiện tượng trên ảnh người dùng:**
  - Setup SHORT hiển thị: Entry `4123.32`, SL `4109.02`, TP `4151.92`. Các mức này có `SL < Entry < TP`, vốn là cấu trúc của lệnh LONG, hoàn toàn sai với SHORT!
  - Khoảng SL = `14.30` và TP = `28.60`, khớp chính xác với công thức fallback `-2 × ATR` và `+4 × ATR` với `ATR = 7.15`.
  - Trạng thái `WAITING_MSS` hoặc `ARMED` vẫn hiện nút Arm SHORT, card hiển thị Margin bằng `risk × 2` thay vì lấy từ domain calculator.
- **Nguyên nhân gốc rễ:** Trong `backend/services/strategy_service.py`, đoạn code tính toán SL/TP dự phòng (fallback) đã áp dụng công thức một chiều: `current_price - 2*atr` cho SL và `current_price + 4*atr` cho TP bất kể hướng của setup là LONG hay SHORT!
- **Giải pháp dứt điểm:**
  - **Hướng Math Authoritative:**
    - `LONG`: `SL < Entry < TP` (Fallback: `SL = current_price - 2*ATR`, `TP = current_price + 4*ATR`).
    - `SHORT`: `TP < Entry < SL` (Fallback: `SL = current_price + 2*ATR`, `TP = current_price - 4*ATR`).
  - **Authoritative Execution Guards:**
    - `backend/main.py` (`manual_arm_setup`): Kiểm tra `validate_price_geometry` và `calculate_risk_reward` bắt buộc trước khi tạo `PaperOrder`. Nếu geometry đảo hoặc Net R:R < 2.0, trả về HTTP 400 `INVALID_PRICE_GEOMETRY` hoặc `INSUFFICIENT_RR`, tuyệt đối không ghi nhận đơn pending hay phát tán event `order.armed`.
    - `backend/services/execution_coordinator.py`: Thêm chốt chặn authoritative geometry guard. Bất kỳ lệnh armed nào có mức giá bị đảo (do dữ liệu cũ) sẽ tự động bị từ chối chuyển sang `rejected` với audit reason rõ ràng, không bao giờ được trigger/fill.
    - `frontend/src/App.tsx`: Tính toán Margin đúng chuẩn `(Quantity × Entry) / Leverage` (không nhân đôi risk), loại bỏ hoàn toàn default `grossRR || 2.0`, ẩn nút Arm và hiển thị nút "Hủy Lệnh Chờ" khi trạng thái là `ARMED`.

### 1.3. Chuẩn Hóa Giờ Yên Lặng (Quiet Hours) `24:00`
- **Nguyên nhân:** Cấu hình lưu trữ giá trị `quiet_hours_start = "24:00"`. Định dạng giờ hợp lệ trong ISO/Python ZoneInfo là `00:00 - 23:59`. Việc phân tích `24:00` gây crash parsing hoặc vô hiệu hóa nhầm quiet hours.
- **Giải pháp:** Bổ sung hàm `normalize_hh_mm()` và migration tự động chuyển đổi `24:00` về `00:00` kèm log cảnh báo, hỗ trợ đầy đủ crossing midnight (ví dụ: `23:00 - 06:00`).

---

## 2. Kiến Trúc Import Lịch Kinh Tế Forex Factory & URL Enrichment

### 2.1. Parser CSV 83 Dòng Theo Định Dạng Nguồn
- **Cấu trúc CSV Forex Factory:** Cột gồm `Title, Country, Date, Time, Impact, Forecast, Previous, URL`.
- **Xử lý Thời Gian & Múi Giờ:**
  - Ghép trường `Date` (định dạng `MM-DD-YYYY`) và `Time` (định dạng 12h `8:30am` hoặc 24h).
  - Tự động chuyển đổi từ `source_timezone` (mặc định `America/New_York` của Forex Factory) sang UTC timestamp (milliseconds) và múi giờ Việt Nam `Asia/Ho_Chi_Minh` (UTC+7).
  - Hỗ trợ các loại lịch đặc biệt: `ALL_DAY`, `TENTATIVE`, `TBA` gán `schedule_kind` riêng, tuyệt đối **không** fallback về `datetime.now()`.
  - Hỗ trợ file có BOM (`\ufeff`), trim whitespace ở headers và cells.
  - Quản lý cách ly (quarantine) dòng lỗi, báo cáo cụ thể vị trí dòng và nguyên nhân thay vì làm gián đoạn toàn bộ batch.
  - Giá trị Actual vắng mặt trong CSV được lưu là `None` (chưa công bố), không gán bằng 0 hay sao chép từ Forecast.

### 2.2. Bảo Vệ An Toàn & Đọc URL Nguồn (News Research Service)
- **Kiến trúc Tầng Dịch Vụ:** `backend/services/news_research_service.py`:
  - **SSRF Protection:** Kiểm tra URL nghiêm ngặt, chặn mọi địa chỉ IP nội bộ, `localhost`, `127.0.0.1`, `10.x`, `192.168.x`, `169.254.x` (AWS/Cloud metadata) và chỉ cho phép domain nằm trong whitelist uy tín (`forexfactory.com`, `investing.com`, `bloomberg.com`, `reuters.com`, `tradingeconomics.com`, `marketwatch.com`).
  - **Bounded Concurrency & Timeout:** Sử dụng `asyncio.Semaphore(3)` và `httpx.AsyncClient` với timeout 10 giây và User-Agent mô phỏng trình duyệt để tránh bị chặn IP.
  - **Cache Tầng Ứng Dụng:** In-memory hash cache 1 giờ giúp tránh gửi yêu cầu mạng lặp lại cho cùng một URL chỉ số.
  - **Structured HTML Extraction:** Trích xuất bảng đặc tính chỉ số (`Specs`, `Source`, `Measures`, `Usual Effect`) và bảng lịch sử công bố (`Historical Releases`) qua BeautifulSoup4.

### 2.3. Báo Cáo Đánh Giá Vĩ Mô Dành Riêng Cho XAUUSDT
Nghiên cứu được cấu trúc thành các mục rõ ràng bằng Tiếng Việt:
1. **Ý Nghĩa Chỉ Số (XAU/USD Context):** Giải thích bản chất kinh tế (CPI, NFP, Fed Funds Rate, Retail Sales, PPI, ISM PMI).
2. **Kênh Tác Động Tới Giá Vàng:**
   - *Kênh DXY (Chỉ số Dollar):* Tác động nghịch chiều lên định giá vàng vật chất tính bằng USD.
   - *Kênh Lợi Suất Thực (Real Yields):* Chi phí cơ hội nắm giữ tài sản không sinh lãi như Vàng khi Fed thay đổi lãi suất kỳ vọng.
   - *Kênh Trú Ẩn & Phòng Hộ Lạm Phát:* Cầu tích lũy vàng khi lạm phát vượt tầm kiểm soát hoặc căng thẳng địa chính trị.
3. **Kịch Bản Trước Giờ Ra Tin (Pre-Release Scenarios):**
   - Kịch bản Hawkish (Tin tốt cho USD): USD vọt tăng, đè nặng giá Vàng.
   - Kịch bản Dovish (Tin xấu cho USD): USD suy yếu, thúc đẩy Vàng tăng giá.
   - Kịch bản In-line: Bám sát cấu trúc kỹ thuật SMC hiện hành.
4. **Đánh Giá Thực Tế Sau Công Bố (Post-Release Assessment):** Phân tích độ lệch (Surprise) giữa Actual và Forecast.
5. **Cảnh Báo & Giới Hạn Nghiên Cứu:** Nhấn mạnh nghiên cứu vĩ mô chỉ đóng vai trò lọc rủi ro (Blackout Window), mọi quyết định giao dịch bắt buộc phải tôn trọng tín hiệu kỹ thuật SMC (Sweep, MSS, FVG) và Net R:R tối thiểu 1:2.0.

---

## 3. Schema & Migration Database

File migration: `backend/migrate.py` (chạy tự động, bảo toàn dữ liệu lịch sử và idempotent):
- **Bổ sung cột cho bảng `economic_news`:**
  - `source_url`: URL bài viết/chỉ số gốc trên Forex Factory.
  - `event_type_id`: Mã định danh chuỗi sự kiện (ví dụ `11-us-cpi-mm`).
  - `source_timezone`: Múi giờ gốc của nguồn tin (ví dụ `America/New_York`).
  - `source_time_raw`: Chuỗi ngày giờ nguyên bản từ CSV.
  - `schedule_kind`: Loại lịch công bố (`EXACT`, `ALL_DAY`, `TENTATIVE`, `TBA`).
  - `gold_relevance`: Mức độ liên quan tới Vàng (`HIGH`, `MEDIUM`, `LOW`).
  - `research_status`: Trạng thái phân tích (`NOT_FETCHED`, `QUEUED`, `FETCHING`, `SUCCEEDED`, `PARTIAL`, `BLOCKED`, `FAILED`).
  - `research_assessment`: JSON lưu trữ toàn bộ nội dung nghiên cứu vĩ mô tiếng Việt.
  - `research_fetched_at`: Thời điểm lấy tin (epoch ms).
- **Chuẩn hóa `telegram_configs`:** Cập nhật các bản ghi có `quiet_hours_start = '24:00'` thành `'00:00'`.
- **Xử lý đơn pending sai cấu trúc:** Các lệnh đang ở trạng thái `armed` có geometry bị ngược (`SL < Entry < TP` cho SHORT hoặc ngược lại) được chuyển sang `state = 'rejected'` kèm ghi chú audit: `REMEDIATION_V5_1: INVALID_PRICE_GEOMETRY`.

---

## 4. Giao Diện Người Dùng (Frontend Enhancements)

1. **Card Setup & Intent:**
   - Hiển thị badge kiểm tra geometry tức thời. Nếu setup bị đảo mức giá: hiển thị cảnh báo vi phạm màu đỏ, vô hiệu hóa nút Arm.
   - Tính toán Margin dự kiến chính xác theo đòn bẩy: `(Quantity × Entry) / Leverage` USDT.
   - Khi setup chuyển sang trạng thái `ARMED`: Nút Arm tự động ẩn đi, thay thế bằng badge "Đang chờ khớp lệnh" và nút **"Hủy Lệnh Chờ"** an toàn.
   - Nút Arm bị vô hiệu hóa khi setup ở trạng thái `WAITING_MSS` (chưa đủ chuỗi xác nhận SMC).
2. **Tab "Tin Tức & Blackout":**
   - Modal xem trước file CSV (`CSV Import Preview Modal`): Cho phép chọn múi giờ nguồn tin (`America/New_York`, `UTC`, `Asia/Ho_Chi_Minh`), hiển thị bảng phân tích giờ VN, mức độ ảnh hưởng và mức độ liên quan tới Vàng (`Gold Relevance`).
   - Modal Nghiên Cứu Vĩ Mô (`News Research Modal`): Hiển thị chi tiết ý nghĩa chỉ số, các kênh truyền dẫn giá vàng, các kịch bản hành động, nguồn trích dẫn và các khuyến cáo rủi ro.
   - Nút liên kết an toàn mở trực tiếp trang nguồn Forex Factory (`target="_blank" rel="noopener noreferrer"`).

---

## 5. Kết Quả Kiểm Thử Toàn Diện (Verification & Smoke Tests)

### 5.1. Backend Test Suite (Pytest)
```bash
PYTHONPATH=backend ./backend/venv/bin/pytest backend/tests/ -v
```
**Kết quả:** `72 passed, 1 warning in 1.25s` (Toàn bộ 72/72 tests đạt 100%).
- `test_news_service.py`: 3/3 passed.
- `test_risk_and_broker.py`: 4/4 passed.
- `test_smc_engine.py`: 4/4 passed.
- `test_v3_regression.py`: 9/9 passed.
- `test_v41_telegram_lifecycle.py`: 13/13 passed.
- `test_v4_invariants.py`: 13/13 passed.
- `test_v5_authoritative_lab.py`: 11/11 passed.
- `test_v5_1_geometry_telegram_news.py`: 15/15 passed:
  - `test_telegram_send_result_contract`: Dataclass typed + 4-tuple unpack.
  - `test_telegram_endpoint_success_no_value_error`: Không còn lỗi unpack ValueError.
  - `test_telegram_endpoint_errors_handled_sanitized`: 401, 403, 429 handled.
  - `test_quiet_hours_24_00_normalization`: Chuẩn hóa 24:00 thành 00:00.
  - `test_screenshot_regression_short_inverted_geometry`: Chặn đứng fixture SHORT ngược.
  - `test_directional_fallback_levels`: Fallback ATR đúng hướng SHORT/LONG.
  - `test_arm_endpoint_rejects_screenshot_fixture`: Từ chối Arm với HTTP 400.
  - `test_arm_endpoint_accepts_valid_short`: Arm thành công setup SHORT hợp lệ.
  - `test_coordinator_rejects_malformed_legacy_armed_order`: Coordinator từ chối fill lệnh sai.
  - `test_parse_csv_calendar_83_row_shape`: Phân tích chuẩn 83 dòng CSV Forex Factory.
  - `test_csv_parser_no_fallback_to_now_on_error`: Cách ly lỗi định dạng, không fallback now().
  - `test_csv_commit_idempotent`: Lưu trữ không trùng lặp khi import lại.
  - `test_url_safety_validator`: Chặn SSRF, private IP và localhost.
  - `test_forex_factory_html_parser`: Trích xuất cấu trúc Specs và History.
  - `test_xauusdt_assessment_generation`: Đánh giá bối cảnh Vàng tiếng Việt.

### 5.2. Frontend Test Suite & Build
- `npm test`: `7/7 passed` (3 tests selection_logic + 4 tests v5.1 geometry & margin invariants).
- `npm run build`: `tsc -b && vite build` hoàn tất không lỗi, mã nguồn được đóng gói sạch vào `dist/`.
- `npm run lint`: `oxlint` chạy trên 13 files, phát hiện 0 lỗi.

### 5.3. Live API Smoke Test
- Backend server port 8000: Endpoint `/health` trả về HTTP 200.
- Endpoint `POST /api/v1/news/import/preview`: Tiếp nhận CSV, chuyển đổi múi giờ New York -> VN và gán nhãn Gold Relevance `HIGH` chính xác.

---

## 6. Hướng Dẫn Sử Dụng Tính Năng Mới

1. **Kiểm Tra Kết Nối Telegram:**
   - Mở modal Cài đặt Telegram.
   - Nhập Bot Token và Chat ID của bạn.
   - Nhấn **"Gửi thử nghiệm"**: Hệ thống sẽ gửi tin nhắn test kiểm tra kết nối qua Bot Telegram mà không gặp lỗi unpack.
   - Bật công tắc **"Kích hoạt thông báo tự động"** và nhấn **"Lưu cấu hình"** để bắt đầu nhận cảnh báo vòng đời lệnh (READY, ARMED, FILLED, TP_HIT, SL_HIT).
2. **Import Lịch Tin Tức Forex Factory:**
   - Tải file CSV lịch tuần từ Forex Factory (hoặc copy nội dung có cột: `Title,Country,Date,Time,Impact,Forecast,Previous,URL`).
   - Vào tab **"Tin Tức & Blackout"**, nhấn **"Import Lịch Tin (CSV)"**.
   - Chọn múi giờ nguồn (`America/New_York (Mặc định Forex Factory)`).
   - Nhấn **"Kiểm Tra Trước (Preview)"**: Kiểm tra các sự kiện kinh tế đã được chuyển sang giờ Việt Nam.
   - Nhấn **"Xác Nhận Nhập Dữ Liệu"** để lưu vào cơ sở dữ liệu.
3. **Xem Báo Cáo Nghiên Cứu Vĩ Mô Cho XAU/USD:**
   - Trong danh sách tin tức, các tin có URL nguồn sẽ hiển thị nút **"Nghiên Cứu"**.
   - Nhấn vào nút để mở bảng phân tích: xem giải thích chỉ số bằng tiếng Việt, các kênh truyền dẫn giá vàng (USD, lợi suất, trú ẩn), và kịch bản giá dự kiến.

---

## 7. Giới Hạn Hệ Thống & Bảo Lưu

- **Local Paper Trading Only:** Toàn bộ hệ thống giao dịch là mô phỏng Paper Trading trên tài sản XAUUSDT, tuyệt đối không kết nối API đặt lệnh tiền thật.
- **Không Nhìn Thấy Tương Lai (Point-in-Time Isolation):** Trong chế độ Risk Lab Replay, dữ liệu nghiên cứu tin tức chỉ có hiệu lực tại thời điểm sau khi tin đã được công bố (`scheduled_at`). Trước thời điểm này, kết quả nghiên cứu sau công bố không được xuất hiện trong quyết định giao dịch.
- **Bảo Mật:** Không lưu trữ khóa bí mật hoặc token Telegram trong git. Tất cả API keys và tokens đều do người dùng cấu hình cục bộ trên máy của họ.
