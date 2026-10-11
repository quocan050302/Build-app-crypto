# AURUM DESK — KẾ HOẠCH & KIẾN TRÚC KIỂM THỬ TOÀN DIỆN V9
## Full System Test & Verification Plan: Paper Trading & Telegram Notifications

---

## 1. Giới Thiệu & Mục Tiêu

Hệ thống Aurum Desk V9 đòi hỏi độ tin cậy tuyệt đối trong việc:
1. **Khớp lệnh PAPER**: Đúng cơ chế 2 chiều Bid/Ask (Executable Quotes), bảo vệ bất biến tối đa 1 vị thế mở (Max 1 Active Position) và tối đa 3 fills/ngày.
2. **Thông báo Telegram**: Phản ánh chính xác vòng đời lệnh (`NEAR_ENTRY`, `READY`, `ARMED`, `FILLED`, `TP_HIT`, `SL_HIT`, `MANUAL_CLOSED`, `REJECTED`, `CANCELLED`, `EXPIRED`, `INVALIDATED`, `LIQUIDATED`, `FEED_DOWN`, `RECOVERED`).
3. **Độ an toàn phân tán (Concurrency & Transport Safety)**:
   - Worker claim outbox bằng Conditional CAS Update (`status IN ('PENDING', 'RETRYING')`) chống duplicate dispatch giữa các worker sessions.
   - Finalize lease có kiểm tra `worker_id` / generation để tránh worker cũ ghi đè claim của worker mới.
   - Xác thực nghiêm ngặt phản hồi Telegram: `ok is True` (loại chuỗi `"false"` và số truthy) và `message_id` phải là số nguyên dương hợp lệ (loại boolean, chuỗi rác).
   - Retry outbox tuân thủ whitelist trạng thái (`FAILED`, `AMBIGUOUS`, `RETRYING`).
4. **Bảo toàn môi trường Runtime**: Không xâm phạm, không xóa, không seed vào database thật `aurum_desk.db`.

---

## 2. Nguyên Tắc & Kiến Trúc Cô Lập (Test Isolation Harness)

### 2.1. Bảo Vệ Dữ Liệu Runtime (Runtime Protection)
- Harness kiểm tra đường dẫn SQLite: Nếu đường dẫn trỏ tới `aurum_desk.db`, hệ thống kích hoạt ngoại lệ `RuntimeProtectionException` và lập tức dừng tiến trình (Fail-Closed).
- Mỗi suite kiểm thử chạy trên một file SQLite tạm thời độc lập (Temp DB) với cờ `WAL mode` và `busy_timeout=5000ms`, đồng bộ schema đầy đủ thông qua `Base.metadata.create_all`.
- Sau khi hoàn thành scenario, file database tạm được dọn dẹp an toàn (`teardown()`).

### 2.2. V9 Deterministic Clock (`V9Clock`)
- Cố định mốc thời gian giả lập: **`2026-10-09 08:30:00 America/New_York`** (sử dụng chuẩn `ZoneInfo("America/New_York")` và `ZoneInfo("Asia/Ho_Chi_Minh")`).
- Loại bỏ hoàn toàn sự phụ thuộc vào đồng hồ hệ thống (`time.time()`), loại trừ rủi ro expired ngẫu nhiên hoặc lệch múi giờ trong kiểm thử.

### 2.3. Mock Boundaries tại Biên Giao Tiếp
- **`MockBitgetFeedDriver`**: Tạo báo giá chuẩn (`CanonicalQuote`), hỗ trợ giả lập giá Ask/Bid chuẩn, inverted spread, NaN/Inf, stale quotes, và reconnect epoch gaps.
- **`MockTelegramTransport`**: Giả lập HTTP client gọi Telegram Bot API, kiểm soát hàng đợi response (200 OK, 429 Rate Limit với `parameters.retry_after`, 400 Bad Request, 5xx Gateway, Network Timeout, Malformed JSON).
- Giữ nguyên 100% logic nghiệp vụ sản xuất (`TradeLifecycleService`, `ExecutionCoordinator`, `QuoteValidator`, `domain_calculator`, `event_bus`, `crud`).

---

## 3. Danh Mục Ma Trận Kiểm Thử V9

### 3.1. Ma trận E: Khớp lệnh & Vòng đời Giao dịch (E01 – E32)
| Kịch Bản | Mô tả | Tiêu chí đạt |
|---|---|---|
| **E01** | LONG LIMIT to TP | Ask <= Entry fill lệnh; Bid >= TP chốt lời; 1 fill audit, 1 lesson draft. |
| **E02** | SHORT LIMIT to TP | Bid >= Entry fill lệnh; Ask <= TP chốt lời; PnL dương. |
| **E03** | LONG LIMIT to SL | Bid <= SL kích hoạt dừng lỗ `SL_HIT`; lesson ghi nhận tổn thất. |
| **E04** | SHORT LIMIT to SL | Ask >= SL kích hoạt dừng lỗ `SL_HIT`. |
| **E05** | LONG MARKET manual | Entry = Ask + directional slippage; tính toán chi phí chính xác. |
| **E06** | SHORT MARKET manual | Entry = Bid - directional slippage. |
| **E07** | LONG STOP trigger | Ask >= Stop mới kích hoạt fill; quote dưới stop không fill. |
| **E08** | SHORT STOP trigger | Bid <= Stop mới kích hoạt fill. |
| **E09 & E10** | Last vs Executable Side | Last chạm nhưng Ask chưa chạm -> KHÔNG fill. Last không chạm nhưng Ask chạm -> FILLED. |
| **E11** | Vị thế mở chặn lệnh mới | Có vị thế đang mở -> lệnh thứ hai bị từ chối với lý do rõ ràng `MAX_POSITIONS_REACHED`. |
| **E13** | Xung đột chiều lệnh 409 | Setup nền lật chiều (LONG -> SHORT) -> phát hiện xung đột, không đổi hướng ngầm. |
| **E14 & E15** | Cạnh tranh 2 sessions đồng thời | Hai luồng đua cùng microsecond qua Threading Barrier -> đúng 1 lệnh khớp, 1 lệnh bị reject. |
| **E19 & E20** | Đóng tay có lãi / lỗ | Đóng lệnh thủ công ánh xạ chính xác `MANUAL_CLOSED`, không nhầm với `TP_HIT` hay `SL_HIT`. |
| **E21 & E22** | Huỷ và hết hạn lệnh | Huỷ setup/lệnh phát domain event `CANCELLED`/`EXPIRED`, ngăn chặn fill hồi tố. |
| **E24** | Sai lệch cấu trúc hình học | Entry/SL/TP sai thứ tự (ví dụ LONG có SL > Entry) bị từ chối ngay lập tức. |
| **E25** | Trượt giá bước nhảy (Gap) | Thị trường gap qua SL -> thoát tại giá bid thực tế của gap, không dùng giá kế hoạch giả định. |
| **E26** | Chạm thanh lý trước SL | Giá chạm ngưỡng thanh lý trước SL -> thoát trạng thái `LIQUIDATED`. |
| **E29** | Bất biến cài đặt rủi ro | Vị thế đang mở giữ nguyên leverage/risk snapshot khi cấu hình hệ thống thay đổi. |
| **E30** | Hạn mức 3 fills/ngày | Chạm mốc 3 fills trong ngày -> lệnh thứ 4 bị chặn bởi Daily Guard. |
| **E32** | Độc lập nguyên nhân và PnL | `TP_HIT` được giữ nguyên làm nguyên nhân đóng lệnh kể cả khi chi phí phí/trượt giá làm Net PnL âm hoặc break-even. |

### 3.2. Ma trận F: Nguồn cấp giá, Tin tức, Phiên NY & Khôi phục (F01 – F16)
| Kịch Bản | Mô tả | Tiêu chí đạt |
|---|---|---|
| **F01 & F02** | Báo giá lỗi & Độ trễ | Từ chối thực thi khi quote có NaN/Inf, âm, đảo spread hoặc độ trễ > 15 giây. |
| **F03 & F04** | REST Fallback & Epoch | WS ngắt kết nối chuyển sang REST; lọc bỏ quotes cũ/out-of-order, không hồi quy trạng thái. |
| **F08** | Tin tức kinh tế High-Impact | Chặn vào lệnh trong khung thời gian tin tức USD nhạy cảm (Blackout Window). |
| **F09 & F10** | Phiên NY & Múi giờ DST | Đếm hạn mức theo múi giờ New York và Việt Nam; chuyển đổi DST chính xác bằng ZoneInfo. |
| **F11, F12 & F15** | Khôi phục Offline & Tái lặp | Đối soát nến lịch sử khi hệ thống khởi động lại, phục hồi đúng 1 lần (Idempotent). |

### 3.3. Ma trận T: Vận chuyển Telegram & Đồng thời Outbox (T01 – T32)
| Kịch Bản | Mô tả | Tiêu chí đạt |
|---|---|---|
| **T01 & T02** | Che giấu Token & Bản nháp | Token bot được che giấu (`mask_token`), cập nhật không bị ghi đè rỗng. |
| **T05 – T08** | Định dạng thông báo đầy đủ | Định dạng tin nhắn chuẩn cho tất cả 14 loại sự kiện vòng đời. |
| **T12** | Khung giờ yên lặng (Quiet Hours) | Chặn tin không khẩn cấp, cho phép tin khẩn cấp vượt qua nếu có cấu hình bypass. |
| **T15 & T16** | Xác thực nghiêm ngặt phản hồi | Từ chối `ok="false"`, `message_id="not-a-number"` hoặc `message_id=false`. |
| **T17** | Xử lý Rate Limit 429 | Đọc `parameters.retry_after` ưu tiên trước header; hoãn gửi đúng thời gian. |
| **T23 & T24** | Atomic CAS Claim & Finalize Lease | Worker claim bằng CAS `status IN ('PENDING', 'RETRYING')`; finalize chặn worker hết hạn ghi đè. |
| **T26 & T27** | Whitelist thử lại Outbox | Chỉ cho phép retry khi outbox ở trạng thái `FAILED`, `AMBIGUOUS`, `RETRYING`. |
| **T32** | Smoke Test Bot Thật | Opt-in qua cờ `--telegram-live`; nếu thiếu credentials, đánh dấu `BLOCKED` minh bạch. |

### 3.4. Ma trận J: Nhật ký Giao dịch, Bài học & Bất biến Giao diện (J01 – J12)
| Kịch Bản | Mô tả | Tiêu chí đạt |
|---|---|---|
| **J03 & J04** | Tổng kết bộ lọc & Phân trang | Tính toán thống kê trên toàn bộ tập dữ liệu đã lọc; phân trang không mất dòng. |
| **J05** | Khóa lạc quan (Optimistic Locking) | Cập nhật ghi chú/tâm lý giao dịch phát hiện xung đột phiên bản qua `updated_at`. |
| **J06** | Quản trị Bài học (Lesson Governance) | Tạo bản nháp `PENDING_REVIEW`, chỉ áp dụng vào chiến lược khi được phê duyệt. |
| **J07** | Đóng lặp lại không sinh bài học thừa | Đối soát lặp lại không tạo bài học trùng lặp cho cùng một trade ID. |

### 3.5. Ma trận B: Luồng Toàn Diện Đầu Cuối (B01 – B10)
| Kịch Bản | Mô tả | Tiêu chí đạt |
|---|---|---|
| **B01** | LONG Manual LIMIT E2E | Setup READY -> Arm -> Fill -> TP -> Outbox -> Journal -> Lesson draft. |
| **B02** | SHORT Auto E2E | Kích hoạt Auto -> Setup READY -> Auto Arm -> Fill -> SL -> Lesson. |
| **B05** | Bất biến Snapshot khi đổi Config | Thay đổi đòn bẩy/rủi ro toàn hệ thống không tác động đến vị thế đang mở. |
| **B08** | Cạnh tranh đa tiến trình E2E | Barrier concurrency kiểm chứng số vị thế mở luôn <= 1. |
| **B09** | Quản lý hạn mức phiên New York | Phân bổ quota phiên NY, bảo toàn kỷ luật dừng lỗ và hạn mức ngày. |

---

## 4. Hướng Dẫn Thực Thi Kiểm Thử

### 4.1. Lệnh Chạy Bộ Kiểm Thử Độc Lập (CLI Runner)
```bash
# Chạy toàn bộ 42 kịch bản V9 với báo cáo tự động
PYTHONPATH=backend python -m lab.v9_full_runner --suite all --seed 42 --report-dir docs

# Chạy riêng từng phân hệ
PYTHONPATH=backend python -m lab.v9_full_runner --suite lifecycle
PYTHONPATH=backend python -m lab.v9_full_runner --suite feed
PYTHONPATH=backend python -m lab.v9_full_runner --suite telegram
PYTHONPATH=backend python -m lab.v9_full_runner --suite journal
PYTHONPATH=backend python -m lab.v9_full_runner --suite e2e
```

### 4.2. Lệnh Chạy Pytest Backend
```bash
# Chạy toàn bộ suite pytest (174 tests bao gồm cả V9)
PYTHONPATH=backend pytest backend/tests/ -q

# Chạy riêng bộ kiểm thử ma trận V9
PYTHONPATH=backend pytest backend/tests/test_v9_full_matrices.py -q
```

### 4.3. Lệnh Kiểm Thử Frontend
```bash
cd frontend
# Chạy unit tests Vitest (24/24 passed)
npm test

# Kiểm tra TypeScript typecheck và Vite production build
npm run build

# Kiểm tra cú pháp và chất lượng mã nguồn
npm run lint
```
