# TÀI LIỆU KỸ THUẬT V5 — AURUM DESK
## Sửa Lỗi SHORT/LONG, Execution Guards và Phòng Kiểm Thử Rủi Ro (Risk Lab)

> **Phiên bản:** V5.0.0-PROD  
> **Repository:** `quocan050302/Build-app-crypto`  
> **Branch:** `feature/aurum-repair-smc-rr`  
> **HEAD Tham Chiếu:** `4d61e4c1f7f37ef98b9bb38523be1edde0bd2449`  
> **Môi trường:** Local Development & Paper Trading (Không VPS, Không API Key Giao Dịch Thật, Không Lệnh Thật)

---

## 1. Tổng Quan Baseline và Các Bug Đã Xác Minh & Sửa Đổi

Tại commit tham chiếu `4d61e4c1f7f37ef98b9bb38523be1edde0bd2449`, mã nguồn tồn tại 8 nhóm lỗi cốt lõi ảnh hưởng trực tiếp đến tính đúng đắn của lệnh, an toàn thực thi và độ tin cậy của kiểm thử:

### 1.1. Bug 4.1: Chart Ghi Đè Setup Được Người Dùng Chọn (Selection Overwrite)
- **Hiện tượng cũ:** Khi người dùng chọn một setup `SHORT` trong Watchboard (`handleFocusSetupOnChart`), hàm `refreshAnalysis()` định kỳ (mỗi 4s) gọi `setActiveOverlay()` bằng `analysisData.active_signal` (vốn đang là `LONG` từ luồng quét nền). Kết quả là chart overlay âm thầm chuyển từ SHORT sang LONG dù người dùng chưa hề thao tác đổi.
- **Khắc phục V5:** Xây dựng mô hình state `selectedIntent` (`SelectedTradeIntent`) độc lập hoàn toàn với `activeAnalysis`. Quá trình polling phân tích nền tiếp tục cập nhật market context nhưng **tuyệt đối không ghi đè** `selectedIntent`. Bổ sung nút **"Theo tín hiệu mới nhất"** để người dùng chủ động quay lại tín hiệu thời gian thực khi có nhu cầu.

### 1.2. Bug 4.2: Nút Mở Lệnh Dùng Sai Đối Tượng Lệnh
- **Hiện tượng cũ:** Nút "Mở Lệnh Paper" trong `App.tsx` lấy `const sig = analysis.active_signal` để tạo payload mở lệnh thay vì lấy setup đang hiển thị trên Card và Chart Overlay. Khi người dùng nhìn thấy Card SHORT, nút bấm lại gửi lệnh LONG của `active_signal`.
- **Khắc phục V5:** Nút hành động, Card tóm tắt bên phải và Chart Overlay được đồng bộ 100% vào cùng một `selectedIntent`. Tiêu đề nút hiển thị rõ ràng: *"Arm SHORT — PAPER"*, *"Mở LONG MARKET — PAPER"*, hoặc *"Chưa đủ điều kiện"* nếu thiếu TP/SL hợp lệ.

### 1.3. Bug 4.3: Manual Arm Chỉ Gửi Setup ID, DB Row Đổi Hướng Âm Thầm
- **Hiện tượng cũ:** Request `POST /api/v1/setups/arm/{id}` chỉ gửi setupId. Ở backend, `strategy_service` chạy loop 4s và cập nhật in-place row của setup đó (cùng symbol/timeframe) thành hướng mới (ví dụ từ SHORT sang LONG). Người dùng bấm Arm SHORT cũ thì DB đã là LONG, dẫn đến arm sai hướng hoàn toàn.
- **Khắc phục V5:**
  - Client gửi `ArmSetupRequest` gồm `expected_direction`, `setup_instance_id`, `expected_revision` và `idempotency_key`.
  - Backend đối chiếu nguyên tử (atomic check): Nếu DB row đã đổi hướng hoặc đổi instance, backend từ chối ngay lập tức với mã `HTTP 409 Conflict` (`SETUP_CHANGED`), trả về snapshot hiện tại để UI hiển thị thông báo rõ ràng cho người dùng.

### 1.4. Bug 4.4: Lệnh MARKET Được Tạo Bừa Bãi Cho Setup Đang Chờ Retrace
- **Hiện tượng cũ:** Bấm Arm thủ công luôn sinh ra lệnh `MARKET`, ngay cả khi setup đang ở trạng thái `WAITING_PRICE` hoặc `WAITING_RETRACE` (chưa chạm vùng POI).
- **Khắc phục V5:** Phân định rạch ròi loại lệnh (`MARKET`, `LIMIT`, `STOP`) và trạng thái trigger. Setup chỉ được phép khớp lệnh MARKET khi giá đã sweep qua POI hợp lệ và thỏa mãn điều kiện strategy. Nếu giá chưa chạm, lệnh được lưu dưới dạng pending limit/stop có điều kiện kích hoạt.

### 1.5. Bug 4.5: Thiếu Authoritative Freshness Validation Tại Fill
- **Hiện tượng cũ:** Coordinator chỉ kiểm tra `bid is not None and ask is not None`. Giá có thể stale từ 30 phút trước hoặc bị ngắt kết nối WebSocket mà hệ thống vẫn fill lệnh với giá cũ.
- **Khắc phục V5:** Tạo `QuoteValidator` tập trung (`backend/services/quote_validator.py`):
  - Kiểm tra tính hữu hạn: `bid > 0`, `ask >= bid`, lọc bỏ NaN/Infinity.
  - Phân tách rõ ràng giữa `exchange_timestamp` (thời điểm sàn phát hành) và `observed_at` (thời điểm backend nhận).
  - Ngưỡng độ tươi (`freshness_threshold_ms`, mặc định 15,000ms cho execution). Nếu quote bị stale hoặc feed ngắt kết nối, từ chối fill với lý do `STALE_QUOTE` hoặc `FEED_DISCONNECTED`.

### 1.6. Bug 4.6: Sai Lệch Executable Exit Sides và Lỗi Intrabar Extremes
- **Hiện tượng cũ:**
  - Exit tick dùng `min(bid, ask)` / `max(bid, ask)` sai chiều: khiến LONG TP theo Ask (thực tế phải bán ở Bid), SHORT TP theo Bid (thực tế phải mua ở Ask).
  - Exit candle dùng `candle_timestamp < opened_at - 60000`, không loại bỏ được biến động giá xảy ra *trước* thời điểm mở lệnh trong chính thanh nến vào lệnh (intrabar extreme trước entry).
- **Khắc phục V5:**
  - Executable sides chuẩn xác: **LONG thoát ở BID** (bán), **SHORT thoát ở ASK** (mua lại).
  - Intrabar guard: Khi đánh giá nến chứa thời điểm mở lệnh (`opened_at`), chỉ xét các biến động sau entry hoặc yêu cầu tick data. Nếu cùng bar chạm cả TP và SL mà không có tick finer, áp dụng chính sách bảo thủ **`AMBIGUOUS_BAR_SL_FIRST`**.

### 1.7. Bug 4.7: Test Cũ Có Tên Lớn Hơn Phạm Vi Kiểm Tra
- **Hiện tượng cũ:** Test MARKET/LIMIT/STOP trước đây chỉ so sánh một nhánh BUY LIMIT; test concurrency cũ chỉ gọi hàm tuần tự 2 lần trong cùng một Session SQLite.
- **Khắc phục V5:** Viết lại toàn bộ bộ kiểm thử V5 (`backend/tests/test_v5_authoritative_lab.py`):
  - Kiểm tra thực tế cả 6 tổ hợp: LONG/SHORT × MARKET/LIMIT/STOP qua `ExecutionCoordinator` thật.
  - Kiểm tra concurrency thực thụ bằng 2 database sessions riêng biệt trên SQLite tạm, có barrier đồng bộ, chứng minh Side-Effects (Trade, Audit, Lesson, Outbox) chỉ xảy ra **đúng một lần**.
  - Kiểm thử unit of work rollback: lỗi khi tạo Outbox/Lesson sẽ rollback toàn bộ giao dịch database.

### 1.8. Bug 4.8: Guard State Concurrency & Atomic Transitions
- **Khắc phục V5:** Đảm bảo nguyên tắc chuyển trạng thái có điều kiện trong DB. Bổ sung ràng buộc `idempotency_key` và `setup_instance_id` trong bảng `paper_orders` để chống double-submit và duplicate fills giữa các tiến trình cạnh tranh.

---

## 2. Selected Intent & Hợp Đồng Dữ Liệu Frontend/Backend

### 2.1. Typed Model Phía Frontend (`SelectedTradeIntent`)
```typescript
export interface SelectedTradeIntent {
  source: 'LIVE_CANDIDATE' | 'WATCH_SETUP' | 'DRAFT' | 'OPEN_POSITION' | 'REPLAY';
  setupId: string;
  setupInstanceId: string;
  revision: number;
  symbol: string;
  timeframe: string;
  direction: 'LONG' | 'SHORT';
  entryZone: [number, number];
  sl: number;
  tp: number;
  orderType: 'MARKET' | 'LIMIT' | 'STOP';
  triggerMode: 'IMMEDIATE' | 'TOUCH' | 'CONFIRMED_CLOSE';
  status: string;
  snapshotAt: number;
}
```

### 2.2. Hợp Đồng API Khi Arm / Mở Lệnh
**Request:** `POST /api/v1/setups/arm/{setup_id}`
```json
{
  "setup_instance_id": "inst-15m-short-01",
  "expected_revision": 1,
  "expected_direction": "SHORT",
  "idempotency_key": "idemp-arm-89a1c2"
}
```

**Response khi có xung đột (HTTP 409 Conflict):**
```json
{
  "error": "SETUP_CHANGED",
  "message": "Setup SHORT bạn chọn đã thay đổi hoặc hết hiệu lực trên hệ thống. Hướng hiện tại: LONG.",
  "current_snapshot": {
    "id": "setup-xau-15m",
    "direction": "LONG",
    "revision": 2,
    "state": "READY"
  }
}
```

---

## 3. Kiến Trúc Lõi Dùng Chung (Shared Core) & Cách Ly Lab

```
                   +----------------------------------+
                   |          IClock Provider         |
                   |   LiveClock  /   ReplayClock     |
                   |       (Asia/Ho_Chi_Minh)         |
                   +-----------------+----------------+
                                     |
                                     v
                   +----------------------------------+
                   |          QuoteValidator          |
                   | Finite Check, Bid/Ask, Freshness |
                   +-----------------+----------------+
                                     |
                                     v
                   +----------------------------------+
                   |        Domain Calculator         |
                   | Net R:R >= 2.0, Risk/Loss Budget |
                   +-----------------+----------------+
                                     |
                  +------------------+------------------+
                  |                                     |
                  v                                     v
      +-----------------------+             +-----------------------+
      |      LIVE_PAPER       |             |       LAB_REPLAY      |
      | Realtime Polling Loop |             | Zero Lookahead Engine |
      | Real DB Persistence   |             | Isolated Mock Session |
      | Real Telegram Outbox  |             | Record-only Mocks     |
      | Real User Cooldown    |             | Independent Metrics   |
      +-----------------------+             +-----------------------+
```

### 3.1. Đồng Hồ Thời Gian (`backend/services/clock.py`)
- Cung cấp giao diện `IClock` với hai cài đặt:
  - `LiveClock`: Sử dụng `time.time()` và wall-clock thực tế.
  - `ReplayClock`: Tua thời gian theo từng bar/tick của dữ liệu lịch sử, đảm bảo toàn bộ logic tính ngày UTC+7 (`get_today_str_vn`), expiry, và cooldown phụ thuộc 100% vào thời gian của sự kiện, không bị rò rỉ thời gian thực tế của máy chủ.

### 3.2. Bộ Xác Thực Giá Tập Trung (`backend/services/quote_validator.py`)
- Xác minh `bid > 0`, `ask >= bid`, lọc bỏ NaN/Infinity.
- Phân biệt `exchange_time` và `observed_at`.
- Ngưỡng độ tươi riêng cho Ticker (mặc định 15s cho khớp lệnh) và Candle.

### 3.3. Cách Ly Dữ Liệu Giữa Live Paper và Lab/Replay
- **Không ghi đè:** Replay và Scenarios chạy trên bộ nhớ tạm hoặc instance tách biệt, không sửa đổi bảng `daily_audits`, `trade_lessons` hay `paper_orders` của tài khoản Live Paper.
- **Không spam Telegram:** Lab sử dụng cơ chế `RecordOnlyNotificationMock`, ghi nhận nội dung thông báo vào timeline của run để người dùng kiểm tra trên UI, tuyệt đối không gửi tin nhắn ra Telegram bot thật.
- **Không chia sẻ trạng thái rủi ro:** Lệnh lỗ trong chế độ Lab không kích hoạt cooldown hoặc daily loss limit của tài khoản Live Paper.

---

## 4. Phòng Kiểm Thử Rủi Ro (Testing Lab)

Phòng kiểm thử được tích hợp trực tiếp trên Frontend tại tab **"Phòng Kiểm Thử"** và backend API tại `/api/v1/lab/*`, bao gồm 3 chế độ:

### 4.1. Chế Độ Kịch Bản (Deterministic Scenarios)
Gồm 13 kịch bản kiểm thử toàn diện, chạy trực tiếp qua implementation thực tế:
1. `scenario_1_long_full_cycle`: LONG từ Arm -> Market fill (Ask + Slippage) -> TP hit (Bid).
2. `scenario_2_short_sl_cycle`: SHORT từ Arm -> Market fill (Bid - Slippage) -> SL hit (Ask).
3. `scenario_3_executable_sides`: Kiểm tra đa dạng giá Bid/Ask đảm bảo LONG chỉ thoát ở Bid, SHORT chỉ thoát ở Ask.
4. `scenario_4_near_entry_hysteresis`: Giá dao động quanh ngưỡng kích hoạt không gây spam alert.
5. `scenario_5_conflict_409`: UI chọn SHORT nhưng backend đã đổi sang LONG -> Từ chối 409, không fill lệnh.
6. `scenario_6_order_types_and_gaps`: Kiểm chứng lệnh LIMIT và STOP với các tình huống chưa chạm, chạm và gap giá.
7. `scenario_7_stale_quote_rejection`: Quote quá thời hạn 15s bị từ chối thực thi, không tạo fill ảo.
8. `scenario_8_guards_block_at_fill`: Khóa lệnh khi tin tức đỏ (News Blackout) hoặc setup hết hạn.
9. `scenario_9_net_rr_filter`: Spread/Slippage giãn làm Net R:R < 2.0 -> Từ chối lệnh ngay tại fill.
10. `scenario_10_daily_loss_budget`: Đạt giới hạn 2 lệnh thua liên tiếp hoặc chạm trần lỗ ngày -> Chặn mở lệnh mới.
11. `scenario_11_pre_entry_extremes`: Nến entry có râu dài trước thời điểm `opened_at` không bị tính nhầm là TP/SL.
12. `scenario_12_concurrent_fill_race`: Hai session fill đồng thời chỉ sinh ra đúng 1 position (Max-1-position invariant).
13. `scenario_13_telegram_outbox_rollback`: Lỗi Outbox/Lesson trong cùng Unit of Work kích hoạt database rollback hoàn toàn.

### 4.2. Chế Độ Replay Lịch Sử (Historical Replay / Backtest)
- **Zero Lookahead:** Duyệt từng thanh nến theo thứ tự thời gian. Chỉ báo và cấu trúc SMC chỉ được xác nhận khi nến đóng.
- **Mô Hình Khớp Lệnh Thực Tế:**
  - LONG mua ở `Ask = Close + Spread/2 + Slippage`, bán ở `Bid = Close - Spread/2 - Slippage`.
  - SHORT bán ở `Bid = Close - Spread/2 - Slippage`, mua lại ở `Ask = Close + Spread/2 + Slippage`.
  - Phí giao dịch tính chuẩn 0.04% taker fee mỗi chiều.
- **Báo Cáo Số Liệu Đầy Đủ:**
  - Net PnL, Win Rate %, Profit Factor, Max Drawdown % (tính từ running peak equity), Expectancy R, MAE/MFE.
  - Phân tích chi tiết theo phiên giao dịch (Á, Âu, Mỹ theo giờ UTC+7).

### 4.3. Chế Độ Stress Test (Kiểm Thử Sức Chịu Đựng)
- Chạy ma trận tham số đa biến trên cùng tập dữ liệu:
  - Spread Multiplier: `1.0x`, `2.0x`, `3.0x`
  - Slippage Multiplier: `1.0x`, `2.0x`, `3.0x`
  - Fee Multiplier: `1.0x`, `2.0x`
  - Latency: `0ms`, `500ms`, `2000ms`
- Xuất bảng ma trận suy giảm hiệu năng (Degradation Matrix) giúp đánh giá độ nhạy của chiến lược khi thanh khoản thị trường sụt giảm.

---

## 5. Hướng Dẫn Sử Dụng và Vận Hành

### 5.1. Khởi Chạy Hệ Thống
Chạy script tự động khởi động cả Backend và Frontend:
```bash
cd aurum-desk
./run.sh
```
- **Backend:** `http://127.0.0.1:8000` (API docs: `http://127.0.0.1:8000/docs`)
- **Frontend:** `http://localhost:5173`

### 5.2. Chạy Kiểm Thử Scenarios Bằng CLI
```bash
# Chạy 1 kịch bản cụ thể:
curl -s -X POST http://127.0.0.1:8000/api/v1/lab/scenarios/run/scenario_1_long_full_cycle | jq .

# Chạy toàn bộ 13 kịch bản:
curl -s -X POST http://127.0.0.1:8000/api/v1/lab/scenarios/run-all | jq .
```

### 5.3. Chạy Replay và Stress Test Bằng CLI
```bash
# Chạy Replay lịch sử với vốn $10,000, risk 1%:
curl -s -X POST http://127.0.0.1:8000/api/v1/lab/replay/run \
  -H "Content-Type: application/json" \
  -d '{"initial_capital": 10000, "risk_pct": 1.0, "leverage": 10, "spread_usd": 0.20, "slippage_usd": 0.10}' | jq .

# Chạy Stress Test ma trận:
curl -s -X POST http://127.0.0.1:8000/api/v1/lab/stress/run \
  -H "Content-Type: application/json" \
  -d '{"initial_capital": 10000, "risk_pct": 1.0, "spread_factors": [1.0, 2.0, 3.0], "slippage_factors": [1.0, 2.0]}' | jq .
```

---

## 6. Báo Cáo Kết Quả Kiểm Thử (Verification Summary)

Toàn bộ các bài kiểm thử bắt buộc đã được thực thi và xác minh độc lập:

| Bộ Kiểm Thử | Công Cụ | Số Lượng Test | Kết Quả | Thời Gian |
| :--- | :--- | :---: | :---: | :---: |
| **Backend Full Test Suite** | `pytest` | 57 / 57 | **PASSED** | 0.98s |
| **Lab Scenarios (13 Scenarios)** | `ScenarioRunner` API | 13 / 13 | **PASSED** | 0.42s |
| **Frontend Selection Invariants** | `vitest` | 3 / 3 | **PASSED** | 0.24s |
| **Frontend TypeScript Build** | `tsc -b && vite build` | 1963 modules | **PASSED** | 0.54s |
| **Frontend Lint** | `oxlint` | 12 files | **0 ERRORS** | 0.08s |
| **Database Migration Idempotency**| `python migrate.py` | 2 lần liên tiếp | **PASSED** | 0.12s |

---

## 7. Các Hạn Chế và Giới Hạn Cần Lưu Ý

1. **Phạm vi Local Paper Trading:**
   - Hệ thống V5 được xây dựng và kiểm thử hoàn toàn trên môi trường Paper Trading Local cho cặp XAUUSDT.
   - Tuyệt đối **không kết nối API key đặt lệnh thật** của sàn Bitget; không có rủi ro tài chính phát sinh.
2. **Hạn Chế Dữ Liệu Lịch Sử (Historical Intrabar):**
   - Dữ liệu OHLC nến chuẩn không chứa thông tin biến động giá chi tiết trong thanh nến (intrabar tick order). Do đó, đối với các thanh nến có cả High chạm TP và Low chạm SL, hệ thống áp dụng giả định bảo thủ `AMBIGUOUS_BAR_SL_FIRST` nhằm tránh thiên lệch kết quả quá lạc quan.
3. **Mô Phỏng Funding Rate:**
   - Các tập dữ liệu lịch sử chuẩn CSV/JSON không kèm theo tỷ lệ Funding thực tế từng block 8 giờ. Báo cáo Replay ghi nhận rõ thành phần này là `EXCLUDED/ESTIMATED` để đảm bảo tính minh bạch học thuật.
