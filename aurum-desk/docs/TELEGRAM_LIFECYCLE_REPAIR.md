# BÁO CÁO KỸ THUẬT SỬA ĐỔI VÀ HOÀN THIỆN TELEGRAM TRADE LIFECYCLE & ENTRY PROXIMITY
**Dự án:** AURUM DESK (Local Paper Trading & SMC/ICT Research for XAUUSDT)  
**Nhánh:** `feature/aurum-repair-smc-rr`  
**Tài liệu tham chiếu:** Master Prompt V4.1 — Telegram Trade Lifecycle Repair  
**Thời điểm thực hiện:** 08/10/2026

---

## 1. HEAD TRƯỚC KHI SỬA, LỖI ĐÃ XÁC MINH VÀ SOURCE LIÊN QUAN

### Baseline Reference
- **Baseline Snapshot:** `887562e98660e9c80d33f083fc1b0dbf15aad1f6`
- **HEAD trước khi sửa:** `887562e98660e9c80d33f083fc1b0dbf15aad1f6`

### Các lỗi cốt lõi đã xác minh trên mã nguồn gốc:
1. **ARMED bị gán nhãn sai là gần Entry (ARMED_NEAR_ENTRY):**
   - *Source:* `backend/services/event_bus.py`, `telegram_service.py`
   - *Vấn đề:* Mã nguồn cũ map sự kiện `order.armed` trực tiếp thành thông báo `ARMED_NEAR_ENTRY`. Không có bộ đánh giá khoảng cách (Proximity Evaluator) độc lập. Lệnh manual arm không có `distance` nhưng formatter lại mặc định `distance=0`, gây hiểu sai là giá đã sát điểm vào lệnh.
2. **Sai lệch ngữ nghĩa lệnh MARKET vs LIMIT/STOP:**
   - *Source:* `backend/services/strategy_service.py`, `execution_coordinator.py`
   - *Vấn đề:* Cả auto-arm và manual-arm đều tạo lệnh với `order_type="MARKET"`, nhưng tin nhắn Telegram lại ghi "chờ nến chạm planned entry". Vòng lặp coordinator kế tiếp lập tức kích hoạt lệnh MARKET theo giá khớp thị trường thay vì chờ limit.
3. **Mất thông báo đóng lệnh (TP/SL) qua endpoint `/candles/sync`:**
   - *Source:* `backend/main.py`, `paper_broker.py`, `exit_monitor.py`
   - *Vấn đề:* Endpoint nến lịch sử gọi `PaperBroker.process_price_tick()` đóng vị thế trong DB nhưng không hề emit domain event hay outbox notification. Khi `exit_monitor` kiểm tra lại thì vị thế đã chuyển sang `closed`, dẫn đến việc hoàn toàn không có thông báo TP/SL tới Telegram.
4. **Deduplication của READY bị kẹt bởi ID khung thời gian cố định:**
   - *Source:* `backend/services/strategy_service.py`, `event_bus.py`
   - *Vấn đề:* Setup dùng aggregate_id dạng `watch-XAUUSDT-15M`. Dedupe key `watch-XAUUSDT-15M:READY` khiến setup mới sau này bị chặn gửi vì trùng ID với setup cũ đã hoàn thành.
5. **Giao dịch và Event/Outbox bị phân mảnh (Non-atomic commit):**
   - *Source:* `backend/crud.py`, `paper_broker.py`, `event_bus.py`
   - *Vấn đề:* Lệnh giao dịch được commit trong một DB session, sau đó `event_bus` lại mở một session khác để ghi domain event và outbox. Nếu ứng dụng crash giữa 2 bước, vị thế đã khớp/đóng nhưng mất vĩnh viễn thông báo.
6. **Worker Telegram nghẽn toàn bộ vì Quiet Hours và thiếu lịch Retry khoa học:**
   - *Source:* `backend/services/telegram_service.py`
   - *Vấn đề:* Quiet Hours chặn đứng toàn bộ hàng đợi outbox (bao gồm cả sự kiện sống còn như FILLED, TP_HIT, SL_HIT, LIQUIDATED). Cơ chế retry cũ dùng `time.sleep` chặn luồng, không có exponential backoff + jitter, không xử lý header 429 `Retry-After`.

---

## 2. KIẾN TRÚC SAU KHI SỬA ĐỔI

Hệ thống được tái cấu trúc thành một **Luồng chuyển trạng thái nguyên tử (Atomic Unit of Work)** kết hợp hàng đợi bền vững **Durable Notification Outbox**.

```
[ Market Ticker / Candles ]
            │
            ▼
┌───────────────────────────────┐
│   Proximity Evaluator Service │  <── Độc lập, chạy chu kỳ 3s
│   (Distance vs Ask/Bid, ATR)  │  <── Hysteresis 1.5x, chống spam
└──────────────┬────────────────┘
               │ (setup.near_entry)
               ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Unified Trade Lifecycle Service                      │
│                                                                        │
│   Trong CÙNG 1 Transaction SQLite / SQLAlchemy (Single Unit of Work):   │
│   1. DB State Conditional Guard (tránh race condition giữa các luồng)   │
│   2. Lưu Fill / Exit & Actual Slippage/Fees                            │
│   3. Cập nhật Daily Audit & Khởi tạo Journal Lesson tự động           │
│   4. Tạo Domain Event (setup_instance_id động)                        │
│   5. Ghi nhận NotificationOutbox (kèm Priority & next_attempt_at)     │
│   6. COMMIT DUY NHẤT một lần ở biên transaction                        │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Durable Telegram Outbox Worker                       │
│                                                                        │
│   - Ưu tiên CRITICAL (FILLED, TP, SL, LIQUIDATED) xử lý trước          │
│   - Quiet Hours Bypass: Cho phép sự kiện vị thế vượt khung giờ yên lặng│
│   - Tự động Suppress các cảnh báo phân tích (NEAR_ENTRY, READY) quá hạn│
│   - Exponential Backoff + Jitter & Tôn trọng Telegram 429 Retry-After  │
│   - Đánh dấu FAILED vĩnh viễn nếu gặp lỗi cấu hình (400, 401, 403)     │
│   - Không log Bot Token hoặc Chat ID nhạy cảm                          │
└────────────────────────────────────────────────────────────────────────┘
```

### Bảng Phân Định Sự Kiện & Danh Mục Thông Báo (Event & Notification Mapping)

| Domain Event | Notification Type | Priority | Ý Nghĩa Nghiệp Vụ | Dedupe Key Policy |
| :--- | :--- | :--- | :--- | :--- |
| `setup.ready` | `READY` | STANDARD | Đủ điều kiện xác nhận SMC (Sweep + MSS + FVG) | `{setup_instance_id}:READY` |
| `setup.near_entry` | `NEAR_ENTRY` | STANDARD | Giá chạm ngưỡng khoảng cách cảnh báo | `{setup_instance_id}:NEAR_ENTRY` |
| `order.armed` | `ARMED` | STANDARD | Lệnh chờ đã tạo trong DB, nêu rõ điều kiện trigger | `{order_id}:ARMED` |
| `trade.opened` | `FILLED` | **CRITICAL** | Vị thế Paper đã khớp thành công | `{trade_id}:FILLED` |
| `trade.closed` (cause=TP_HIT) | `TP_HIT` | **CRITICAL** | Chốt lời Take Profit thành công | `{trade_id}:TP_HIT` |
| `trade.closed` (cause=SL_HIT) | `SL_HIT` | **CRITICAL** | Cắt lỗ Stop Loss | `{trade_id}:SL_HIT` |
| `trade.closed` (cause=AMBIGUOUS_BAR_SL_FIRST) | `SL_HIT` | **CRITICAL** | Nến chạm cả TP và SL trong cùng bar; chọn SL thận trọng | `{trade_id}:SL_HIT` |
| `trade.closed` (cause=MANUAL_CLOSE) | `MANUAL_CLOSED` | **CRITICAL** | Đóng vị thế chủ động từ Dashboard | `{trade_id}:MANUAL_CLOSED` |
| `trade.liquidated` | `LIQUIDATED` | **CRITICAL** | Chạm ngưỡng thanh lý mô phỏng Isolated Margin | `{trade_id}:LIQUIDATED` |
| `order.rejected` | `REJECTED` | STANDARD | Bị Execution Guards / Loss Limits từ chối | `{order_id}:REJECTED` |
| `setup.invalidated` | `INVALIDATED` | STANDARD | Cấu trúc nến vi phạm mức Invalidation | `{setup_instance_id}:INVALIDATED` |
| `setup.expired` | `EXPIRED` | STANDARD | Hết thời gian chờ hiệu lực setup | `{setup_instance_id}:EXPIRED` |
| `feed.stale` | `FEED_DOWN` | STANDARD | Mất kết nối hoặc nến trễ quá 30 phút | `XAUUSDT:FEED_DOWN` |
| `feed.recovered` | `RECOVERED` | STANDARD | Kết nối thị trường phục hồi bình thường | `XAUUSDT:RECOVERED` |

---

## 3. QUY TẮC SETUP IDENTITY, DEDUPLICATION & PROXIMITY EVALUATOR

### Setup Instance Identity:
- Mỗi khi cấu trúc nến phát hiện một vùng POI/Entry mới, hệ thống tạo `setup_instance_id` duy nhất dạng `setup-XAUUSDT-15M-{direction}-{epoch_ms}`.
- Không dùng ID khung thời gian cố định `watch-XAUUSDT-15M` làm khóa deduplication của sự kiện domain, loại bỏ hoàn toàn tình trạng nuốt tin setup mới.

### Proximity Evaluator (Đánh giá tiếp cận Entry):
1. **Không báo giả khi chưa có POI:**
   - Nếu setup chưa có POI thực tế hoặc `provisional_entry` gán bằng `current_price` ở trạng thái quan sát sơ bộ, Proximity Evaluator sẽ bỏ qua, không tính khoảng cách = 0.
2. **Đúng chiều thực thi (Executable Side):**
   - Vị thế **LONG**: Đo khoảng cách giữa giá **Ask** và biên trên của Entry Zone (`entry_zone_high`).
   - Vị thế **SHORT**: Đo khoảng cách giữa giá **Bid** và biên dưới của Entry Zone (`entry_zone_low`).
3. **Cơ chế Hysteresis & Chống Spam:**
   - Ngưỡng vào cảnh báo (`enter_threshold`): Mặc định `0.5 × ATR` hoặc `2.0 USDT`.
   - Ngưỡng thoát cảnh báo (`exit_threshold`): Bằng `1.5 × enter_threshold`. Giá phải thoát xa khỏi ngưỡng này trước khi một cảnh báo mới có thể được kích hoạt.
   - **One-per-instance:** Mỗi setup instance mặc định chỉ gửi đúng 1 thông báo `NEAR_ENTRY`. Trạng thái được lưu bền vững vào cột `near_entry_alerted_at` trong SQLite, đảm bảo khi khởi động lại ứng dụng không bị spam lặp lại.
4. **Phân biệt rủi ro tài khoản và khoảng cách giá:**
   - Template ghi rõ `Khoảng cách tới vùng: $X.XX USDT (X.XX ATR)` và phân biệt rạch ròi với `Rủi ro tài khoản: $Y.YY USDT`.

---

## 4. NGỮ NGHĨA LỆNH (ORDER EXECUTION SEMANTICS) & RISK GUARDS

- **Lệnh MARKET:** Kích hoạt ngay khi guards cho phép. Khớp theo giá thị trường (`Ask` đối với Long, `Bid` đối với Short) cộng mô hình trượt giá (slippage) và phí sàn. Thông báo ghi rõ "Chờ guards để khớp ngay theo thị trường", không nói "chờ chạm planned entry".
- **Lệnh LIMIT:**
  - `BUY LIMIT`: Chỉ khớp khi giá `Ask <= planned_entry`.
  - `SELL LIMIT`: Chỉ khớp khi giá `Bid >= planned_entry`.
- **Lệnh STOP:**
  - `BUY STOP`: Chỉ kích hoạt khi `Ask >= stop_price`.
  - `SELL STOP`: Chỉ kích hoạt khi `Bid <= stop_price`.
  - Nếu engine chưa cấu hình stop order hợp lệ, hệ thống từ chối rõ ràng (`REJECTED: STOP order not supported in current mode`) thay vì âm thầm ép thành LIMIT.
- **Risk Guards không nới lỏng:**
  - Tối đa 1 vị thế paper hoạt động đồng thời.
  - Tối đa 3 lần khớp lệnh (`fills_count`) mỗi ngày.
  - Ngưỡng lỗ tối đa ngày: 1.5% NAV. Cooldown sau 2 lệnh lỗ liên tiếp.
  - Blackout tin tức kinh tế quan trọng (Non-Farm Payrolls, CPI, FOMC).

---

## 5. DỮ LIỆU BỀN VỮNG, MIGRATION & TƯƠNG THÍCH CẤU HÌNH CŨ

### Cơ chế Migration Idempotent (`backend/migrate.py`):
- Tự động bổ sung các cột mới vào SQLite schema mà không làm mất dữ liệu giao dịch, journal, lessons hay outbox cũ:
  - `watch_setups`: `setup_instance_id`, `near_entry_alerted_at`, `near_entry_distance_price`, `near_entry_distance_atr`, `entry_zone_low`, `entry_zone_high`.
  - `notification_outbox`: `priority` (CRITICAL/STANDARD), `next_attempt_at`, `occurred_at`.
  - `telegram_configs`: `bypass_critical_quiet_hours`, `near_entry_mode`, `near_entry_atr_mult`, `near_entry_price_dist`, `near_entry_cooldown_min`.
- **Mở rộng tương thích đăng ký cũ (Subscription Expansion):**
  - Cấu hình cũ có `CLOSED` được tự động chuyển đổi thành `["TP_HIT", "SL_HIT", "MANUAL_CLOSED"]`.
  - Cấu hình cũ có `ARMED_NEAR_ENTRY` được tách bạch thành `["ARMED", "NEAR_ENTRY"]`.
  - Không bao giờ gửi 2 tin nhắn Telegram trùng lặp cho cùng một lượt đóng lệnh.
- **Quy trình sao lưu (Backup & Rollback):**
  - Script migration tự động tạo bản sao lưu `aurum_desk.db.bak.{timestamp}` trước khi chạy.
  - Đã kiểm tra chạy lại lần 2 (idempotent test) đạt 100% an toàn. Các file backup được loại khỏi git tracking trong `.gitignore`.

---

## 6. HƯỚNG DẪN VẬN HÀNH LOCAL & CẤU HÌNH TELEGRAM

### Yêu cầu tiên quyết:
- Backend phải luôn chạy trên máy local để theo dõi thị trường và xử lý hàng đợi outbox.
- Ứng dụng gửi tin qua **Telegram Bot API (HTTPS)** trực tiếp từ Backend tới Telegram servers.

### Khởi chạy Backend và Frontend:
1. **Khởi chạy Backend (Port 8000):**
   ```powershell
   cd aurum-desk
   $env:PYTHONPATH="backend"
   .\backend\venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000
   ```
2. **Khởi chạy Frontend (Port 5173):**
   ```powershell
   cd aurum-desk/frontend
   npm run dev
   ```

### Cấu hình Telegram trên Giao diện (Tab Cài đặt):
- Nhập **Bot Token** và **Chat ID** (Lưu ý: Người dùng phải bấm `/start` bot trên Telegram trước để Telegram mở phiên chat với bot).
- Tùy chọn 11 danh mục thông báo:
  - `Tín hiệu sẵn sàng (READY)`
  - `Sắp tiếp cận Entry (NEAR_ENTRY)`
  - `Đã Arm lệnh (ARMED)`
  - `Đã khớp lệnh (FILLED)`
  - `Chốt lời (TP)`
  - `Cắt lỗ (SL)`
  - `Đóng thủ công (MANUAL_CLOSE)`
  - `Thanh lý (LIQUIDATION)`
  - `Lệnh bị từ chối (REJECTED)`
  - `Hủy/Hết hạn (INVALIDATED/EXPIRED)`
  - `Sự cố dữ liệu (FEED_DOWN)`
- Bật/Tắt **"Vượt khung giờ yên lặng cho sự kiện quan trọng (FILLED, TP, SL, Thanh lý)"**.
- Điều chỉnh ngưỡng cảnh báo gần Entry: Chế độ `ATR` (mặc định 0.5 ATR) hoặc `Giá tuyệt đối (USDT)` kèm thời gian hồi chiêu (Cooldown).
- Xem bảng **"Hàng Đợi & Lịch Sử Gửi Thông Báo"**: Theo dõi trạng thái `SENT`, `PENDING`, `RETRYING`, `FAILED` kèm thời gian xảy ra sự kiện và nút "Gửi lại" thủ công cho các tin bị lỗi.

---

## 7. KẾT QUẢ KIỂM TRA THỰC TẾ (TEST SUITE & REGRESSION RESULTS)

Tất cả các kiểm tra tự động đã được thực hiện bằng `pytest` trên môi trường Python 3.11.9:

### Lệnh chạy test:
```powershell
$env:PYTHONPATH="backend"; .\backend\venv\Scripts\python -m pytest backend/tests
```

### Kết quả kiểm tra:
```
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\build app-crypto\aurum-desk
plugins: anyio-4.15.1
collected 46 items

backend\tests\test_news_service.py ...                                   [  6%]
backend\tests\test_risk_and_broker.py ....                               [ 15%]
backend\tests\test_smc_engine.py ....                                    [ 23%]
backend\tests\test_v3_regression.py .........                            [ 43%]
backend\tests\test_v41_telegram_lifecycle.py .............               [ 71%]
backend\tests\test_v4_invariants.py .............                        [100%]

============================= 46 passed in 0.86s ==============================
```

### Các kịch bản kiểm tra hồi quy trọng điểm trong `test_v41_telegram_lifecycle.py`:
1. `test_proximity_evaluator_ignores_setup_without_real_poi`: Không phát cảnh báo NEAR_ENTRY nếu setup chưa có POI/signal thực sự, dù provisional_entry trùng với giá hiện tại.
2. `test_proximity_evaluator_uses_ask_for_long_and_bid_for_short`: Kiểm chứng chính xác tính khoảng cách bằng Ask cho lệnh LONG và Bid cho lệnh SHORT.
3. `test_proximity_anti_spam_and_persisted_state_on_restart`: Cảnh báo đúng 1 lần khi giá đi vào vùng; duy trì trong vùng nhiều tick không bị spam lặp; khởi động lại ứng dụng vẫn nhớ trạng thái đã cảnh báo.
4. `test_stale_or_invalid_ticker_does_not_alert_or_fill`: Ticker cũ quá 30 phút hoặc báo giá bất thường (`Ask < Bid`) bị chặn hoàn toàn, không tạo cảnh báo và không mở lệnh.
5. `test_market_vs_limit_vs_stop_order_trigger_semantics`: Phân tách rõ ràng điều kiện kích hoạt giữa MARKET, LIMIT và STOP.
6. `test_rejected_order_creates_outbox_and_does_not_increment_fills`: Lệnh bị guards từ chối tạo thông báo REJECTED trong cùng transaction và không tính vào 3 lệnh tối đa trong ngày.
7. `test_atomic_fill_creates_position_audit_event_and_outbox`: Khớp lệnh lưu position, cập nhật audit, phát sinh domain event và outbox row trong cùng một unit of work nguyên tử.
8. `test_atomic_rollback_on_outbox_failure`: Giả lập lỗi chèn outbox; toàn bộ transaction rollback sạch sẽ, không tạo vị thế nửa vời.
9. `test_candles_sync_unified_close_path_emits_notification`: Đóng vị thế qua `/api/v1/candles/sync` phát sinh đúng một sự kiện `trade.closed` và outbox row như exit monitor.
10. `test_concurrent_exit_monitor_and_sync_idempotency`: Hai luồng cùng đóng một vị thế chỉ tạo duy nhất 1 sự kiện đóng, 1 bài học journal và 1 thông báo outbox.
11. `test_quiet_hours_bypasses_critical_events_and_blocks_standard`: Khung giờ yên lặng cho phép sự kiện quan trọng (FILLED, TP, SL) gửi đi nhưng chặn giữ lại các sự kiện phân tích (NEAR_ENTRY, READY).
12. `test_stale_opportunity_suppression_before_dispatch`: Setup đã bị hủy (INVALIDATED) trước khi worker kịp gửi sẽ bị tự động đánh dấu `SUPPRESSED`, không gửi cơ hội đã chết cho người dùng.
13. `test_template_formatting_sanitization_and_no_fake_zeros`: Đảm bảo toàn bộ ký tự đặc biệt được xử lý chuẩn Markdown Telegram, hiển thị nhãn PAPER rõ ràng và không tự bịa số 0 cho trường rỗng.

### Frontend Compilation Check:
- Lệnh: `npm run build` tại `aurum-desk/frontend`.
- Kết quả: `tsc -b && vite build` hoàn thành với **0 lỗi** TypeScript / Vite.

---

## 8. MỤC "LIVE TELEGRAM": ĐÁNH GIÁ CHUYỂN PHÁT THỰC TẾ

- **Trạng thái Live Delivery:** Đã thực hiện kiểm tra endpoint Telegram API Bot sendMessage trên thông tin bot `MyAurumAlearts_bot` và Chat ID `6919390280` được cấu hình sẵn trong DB cục bộ.
- **Khắc phục lỗi định dạng thực tế:**
  - Lỗi `Bad Request: can't parse entities` của Telegram API do xung đột giữa cú pháp in đậm `*(...)*` chứa dấu ngoặc đơn liền kề đã được loại bỏ hoàn toàn trên tất cả các template.
  - Các thông báo test gửi trực tiếp phản hồi HTTP 200 `{"ok": true}` từ Telegram servers.
- **Lưu ý bảo mật:**
  - Tuyệt đối không log bot token, token URL hoặc Chat ID người dùng trong terminal hay file log.
  - Trên API và UI chỉ hiển thị token đã che mờ (`****...****`).

---

## 9. CÁC GIỚI HẠN KỸ THUẬT ĐÃ BIẾT (KNOWN LIMITATIONS)

1. **Giới hạn chu kỳ Tick Polling:**
   - Collector thị trường Bitget cập nhật theo chu kỳ định kỳ (~2-4 giây). Biến động giá cực nhanh xuyên qua vùng Entry trong mili-giây có thể kích hoạt cảnh báo trễ một vài nhịp tick.
2. **Nến mơ hồ (Ambiguous Bar / Gap):**
   - Khi tải nến lịch sử có biên độ dao động cực lớn (High > TP và Low < SL trong cùng 1 nến 15M), hệ thống áp dụng nguyên tắc thận trọng chọn SL trước (`AMBIGUOUS_BAR_SL_FIRST`) vì không có dữ liệu sub-tick theo từng giây trong quá khứ để xác định TP hay SL chạm trước.
3. **Giới hạn tầng vận chuyển Telegram (Transport Idempotency):**
   - Telegram Bot API không hỗ trợ thuộc tính Idempotency Key cho phương thức `sendMessage`. Trường hợp Telegram đã nhận tin nhưng kết nối mạng bị rớt khi trả HTTP response về backend, worker có thể thử gửi lại dẫn đến tin nhắn bị lặp một lần ở phía ứng dụng Telegram. Cơ chế outbox nội bộ đảm bảo chỉ một bản ghi outbox duy nhất được tạo ra.
