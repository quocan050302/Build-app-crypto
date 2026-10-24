# BẢNG ĐĂNG KÝ VÀ THEO DÕI LỖI KỸ THUẬT AURUM V17.6.1
## OBSERVABILITY CORRECTNESS & RELIABILITY DEFECT REGISTER

**Hệ thống**: AURUM DESK — XAUUSDT SMC/ICT Paper Trading Engine  
**Phiên bản mục tiêu**: V17.6.1  
**Nhánh Git**: `fix/v14-1-rr-flow`  
**Git Baseline**: V17.6 (`ac435bd4cf5f97de22a47673b519b46fd972f7de`)  
**Tài liệu**: Quy chuẩn phân tích nguyên nhân gốc rễ (RCA), ca tái hiện lỗi, giải pháp kỹ thuật và tiêu chí nghiệm thu độc lập cho 12 khiếm khuyết quan sát (OBS-01 đến OBS-12).

---

## Danh Mục Khiếm Khuyết Tổng Hợp

| Mã Lỗi | Hạng Mục | Mô Tả Khiếm Khuyết | Mức Độ | Trạng Thái |
|:---:|---|---|:---:|:---:|
| **OBS-01** | Feed Timestamps | Trường timestamp báo giá (`CanonicalQuote`) sai lệch giữa `exchange_ts_ms` và `timestamp` | CRITICAL | VERIFIED (test_v17_6_1_quote_timestamps.py) |
| **OBS-02** | Candle Freshness | Độ tươi mới của nến đóng (`is_closed`) chưa được xác minh đầy đủ theo biên thời gian | HIGH | VERIFIED (test_v17_6_1_quote_timestamps.py) |
| **OBS-03** | Telemetry Durability | Dữ liệu heartbeat và cycle telemetry có thể bị rollback cùng transaction nghiệp vụ | CRITICAL | VERIFIED (test_v17_6_1_telemetry_resilience.py) |
| **OBS-04** | Engine Health & Stall | Nhận diện worker instance ID và phát hiện treo (process vs evaluation) chưa tách bạch | HIGH | VERIFIED (test_v17_6_engine_observability.py) |
| **OBS-05** | Run Data Isolation | Dashboard có thể tổng hợp hoặc hiển thị lẫn lộn dữ liệu giữa các run khác nhau | CRITICAL | VERIFIED (test_v17_6_1_run_isolation.py) |
| **OBS-06** | Uptime Mathematics | Công thức tính tỷ lệ bao phủ quan sát (Uptime Coverage %) sai lệch phạm vi cửa sổ | HIGH | VERIFIED (test_v17_6_1_market_hours_dst.py) |
| **OBS-07** | Market Hours & DST | Nhận diện giờ nghỉ thị trường XAUUSDT dùng giờ UTC tĩnh, sai lệch 1 giờ khi đổi DST New York | HIGH | VERIFIED (test_v17_6_1_market_hours_dst.py) |
| **OBS-08** | WebSocket Reconciliation | WebSocket client nhận sự kiện lồng (nested event envelope) không kích hoạt làm mới UI | MEDIUM | VERIFIED (v17_6_1_observability_ui.test.tsx) |
| **OBS-09** | Truthful UI Fallbacks | UI có thể hiển thị giá trị mặc định sai lệch (0 hoặc `$0.00`) thay vì `N/A` khi chưa có dữ liệu | HIGH | VERIFIED (v17_6_1_observability_ui.test.tsx) |
| **OBS-10** | Acceptance Rigor | Runner nghiệm thu có thể bỏ sót kiểm tra cây Git sạch hoặc stale test artifacts | HIGH | VERIFIED (run_v17_6_1_acceptance.py) |
| **OBS-11** | Regression Coverage | Thiếu kiểm thử hồi quy bao phủ cho các ca biên ngắt kết nối và phục hồi sau lỗi | MEDIUM | VERIFIED (15 test suites PASS) |
| **OBS-12** | Incidents Persistence | Lưu vết sự cố (`engine_incidents`) chưa có cơ chế tự động resolve khi hệ thống phục hồi | MEDIUM | VERIFIED (test_v17_6_1_telemetry_resilience.py) |

---

## Chi Tiết Kỹ Thuật Từng Khiếm Khuyết

### OBS-01: Mismatch Trường Timestamp Báo Giá Market Quote

- **Hiện tượng**:
  Trong `EngineObservabilityService.build_market_feed_health_snapshot()`, mã nguồn truy cập:
  ```python
  quote_ts = getattr(quote, "timestamp", None) or getattr(quote, "time", None)
  ```
  Tuy nhiên, lớp authoritative `CanonicalQuote` (định nghĩa tại `backend/services/quote_validator.py`) sử dụng hai trường độc lập:
  - `exchange_ts_ms: Optional[int]` (thời điểm sàn Bitget phát sinh tick)
  - `received_at_ms: int` (thời điểm backend nhận tick qua WebSocket)
- **Hậu quả**:
  `quote_ts` luôn nhận giá trị `None` khi `quote` là một đối tượng `CanonicalQuote` chuẩn. Kết quả là `quote_age_sec` bị tính là `None` hoặc fallback sai, khiến trạng thái feed freshness bị đánh giá sai thành `CONNECTED_STALE` hoặc `UNKNOWN` dù dữ liệu đang trực tiếp.
- **Giải pháp**:
  1. Xây dựng hàm thuần túy `resolve_canonical_quote_timestamps(quote, now_ms=now_ms) -> Dict[str, Any]`.
  2. Xác thực kiểu dữ liệu nghiêm ngặt: số nguyên hợp lệ, không boolean, không NaN, không âm, không quá 60s trong tương lai.
  3. Tính toán rõ hai độ trễ: `exchange_age_ms = now_ms - exchange_ts_ms` và `receive_age_ms = now_ms - received_at_ms`.
  4. Xác định `transport_delay_ms = received_at_ms - exchange_ts_ms`.
  5. Fallback qua `collector_service.latest_ticker` nếu `bitget_ws_service.latest_quote` tạm thời rỗng.
- **Tiêu chí nghiệm thu**: Test suite `test_v17_6_1_feed_timestamps.py` xác minh `exchange_ts_ms` và `received_at_ms` được phân giải chính xác 100%.

---

### OBS-02: Xác Minh Độ Tươi Mới Nến Đóng (Closed-Candle Freshness)

- **Hiện tượng**:
  Hàm kiểm tra nến đóng lấy `closed_ts = candles[-1].timestamp`. Trong luồng nến real-time, nến cuối cùng thường là **nến đang hình thành (forming / unclosed bar)** với timestamp bắt đầu của nến, chưa phải nến đã đóng.
- **Hậu quả**:
  Thời gian nến đóng bị tính sai lệch một khoảng bằng đúng độ dài khung thời gian (ví dụ: lệch 1 phút với M1, lệch 1 giờ với H1), dẫn đến cảnh báo nến cũ giả tạo hoặc bỏ sót nến bị thiếu.
- **Giải pháp**:
  1. Xác định nến đóng dựa trên cờ `is_closed == True` hoặc `candle.timestamp + interval_ms <= now_ms`.
  2. Tính thời điểm đóng thực tế `candle_closed_at_ms = candle.timestamp + interval_ms`.
  3. Kiểm tra tính liên tục của chuỗi nến (gap detection): `t[i] - t[i-1] == interval_ms` (trừ khoảng nghỉ cuối tuần).
- **Tiêu chí nghiệm thu**: Kiểm thử với cả nến đang chạy (unclosed) và nến đã đóng (closed), xác minh độ trễ nến tính chính xác đến từng mili-giây.

---

### OBS-03: Tính Bền Vững Của Dữ Liệu Heartbeat & Cycle Telemetry

- **Hiện tượng**:
  `record_engine_heartbeat` và `record_evaluation_cycle` nhận đối tượng `db: Session` từ luồng nghiệp vụ `scalp_watcher_orchestrator.evaluate_cycle()`. Nếu luồng nghiệp vụ gặp ngoại lệ hoặc bị `db.rollback()`, bản ghi heartbeat và cycle telemetry vừa tạo cũng bị rollback theo.
- **Hậu quả**:
  Mất dấu vết kiểm toán tại đúng thời điểm quan trọng nhất (khi có lỗi xảy ra), khiến hệ thống không thể trả lời câu hỏi "Tại sao chu kỳ đó bị lỗi?".
- **Giải pháp**:
  1. Cô lập phiên ghi dữ liệu quan sát: Sử dụng `db.begin_nested()` (savepoint) hoặc một session chuyên biệt cho telemetry.
  2. Bắt mọi ngoại lệ ghi telemetry và commit độc lập, đảm bảo rằng lỗi nghiệp vụ không xóa bản ghi quan sát, và lỗi quan sát không phá vỡ logic giao dịch.
- **Tiêu chí nghiệm thu**: Tái hiện ngoại lệ trong `evaluate_cycle()`, xác minh bản ghi cycle với trạng thái `FAILED` và lý do lỗi vẫn được lưu bền vững trong DB.

---

### OBS-04: Quản Lý Engine Instance ID & Phát Hiện Treo (Stall Detection)

- **Hiện tượng**:
  `build_engine_health_snapshot` truy vấn bản ghi heartbeat mới nhất trong bảng `engine_heartbeats`. Khi ứng dụng khởi động lại, các bản ghi của instance cũ vẫn tồn tại trong database. Nếu không đối chiếu với `current_instance_id`, hệ thống có thể đọc nhầm trạng thái của một instance đã chết.
- **Hậu quả**:
  Báo động giả `STALLED` ngay khi vừa khởi động bot, hoặc ngược lại che giấu sự cố nếu instance mới chưa kịp ghi heartbeat.
- **Giải pháp**:
  1. Ưu tiên đọc bản ghi theo `engine_instance_id == self._current_instance_id`.
  2. Tách biệt hai loại treo:
     - **Process Stall**: Tiến trình dừng gửi heartbeat (`now_ms - last_process_heartbeat_at_ms > 15_000`).
     - **Evaluation Stall**: Tiến trình vẫn sống nhưng vòng lặp đánh giá bị chặn ở một hàm I/O (`now_ms - last_evaluation_started_at_ms > 15_000` và chưa có `completed_at_ms`).
- **Tiêu chí nghiệm thu**: Test mô phỏng worker khởi động lại và worker bị block I/O, xác minh trạng thái trả về chính xác tương ứng.

---

### OBS-05: Cô Lập Dữ Liệu Nghiên Cứu Theo Run (Observation Run Isolation)

- **Hiện tượng**:
  Khi người dùng xem dashboard quan sát của một `run_id` cụ thể, một số truy vấn (như waiting conditions, blocked categories, hoặc recent decisions) nếu không lọc chặt chẽ theo `run_id` có thể gom cả dữ liệu của các run trước đó.
- **Hậu quả**:
  Số liệu phễu cơ hội và tỷ lệ thắng bị sai lệch, vi phạm nguyên tắc bất biến phân tách nghiên cứu (Rules 025, 092).
- **Giải pháp**:
  1. Mọi câu truy vấn database trong `build_observation_dashboard_snapshot()` bắt buộc phải có điều kiện `filter(Record.run_id == run_id)`.
  2. Nếu không truyền `run_id`, tự động phân giải run đang hoạt động (`status == 'COLLECTING'`) gần nhất của symbol đó. Nếu không có run nào, trả về trạng thái rỗng `NO_ACTIVE_RUN`, tuyệt đối không cộng dồn các run khác nhau.
- **Tiêu chí nghiệm thu**: Tạo hai run với các setup và lệnh khác nhau, xác minh snapshot của run A không chứa bất kỳ dữ liệu nào của run B.

---

### OBS-06: Chuẩn Hóa Công Thức Tính Độ Bao Phủ Quan Sát (Uptime Math)

- **Hiện tượng**:
  Thời gian cửa sổ kỳ vọng (`expected_observation_hours`) đôi khi lấy toàn bộ độ dài cửa sổ (ví dụ: 24h đối với Today) mà không tính đến thời điểm run thực sự bắt đầu, hoặc không trừ chính xác thời gian thị trường đóng cửa cuối tuần nằm trong cửa sổ đó.
- **Hậu quả**:
  Tỷ lệ bao phủ quan sát bị tụt xuống quá thấp (ví dụ: bot mới chạy 1h trong ngày thì bị tính 1h / 24h = 4.2% LOW, dù bot chạy hoàn hảo 100% thời gian kể từ khi bật).
- **Giải pháp**:
  1. Cửa sổ đánh giá hiệu lực của run: `effective_start = max(window_start_ms, run_started_at_ms)`, `effective_end = min(window_end_ms, now_ms)`.
  2. `active_market_ms = calculate_open_market_duration_ms(effective_start, effective_end)`.
  3. `expected_observation_hours = active_market_ms / 3_600_000.0`.
  4. `coverage_pct = (actual_evaluated_hours / expected_observation_hours) * 100.0` (giới hạn `[0.0, 100.0]`).
  5. Nếu `expected_observation_hours == 0` (chạy hoàn toàn trong kỳ nghỉ cuối tuần), trả về `100.0%` với trạng thái `MARKET_CLOSED`.
- **Tiêu chí nghiệm thu**: Kiểm thử toán học với các mốc thời gian: mới bật bot, chạy xuyên kỳ nghỉ cuối tuần, và chạy đủ 24h ngày thường.

---

### OBS-07: Giờ Nghỉ Thị Trường & Đổi Giờ Mùa Hè New York (DST Handling)

- **Hiện tượng**:
  Hàm `is_xauusdt_weekend_break()` kiểm tra giờ đóng cửa bằng mốc UTC cố định (thứ Sáu 21:00 UTC đến Chủ Nhật 22:00 UTC). Tuy nhiên, giờ giao dịch của vàng XAUUSDT (sàn COMEX / CME và Bitget futures) đóng cửa cố định theo giờ **New York**:
  - Đóng cửa: **Thứ Sáu 17:00 New York Time**
  - Mở cửa: **Chủ Nhật 18:00 New York Time**
  - Giờ bảo trì hàng ngày: **Thứ Hai - Thứ Năm 17:00 đến 18:00 New York Time**
- **Hậu quả**:
  Vào mùa đông (EST, UTC-5), thứ Sáu 17:00 NY là 22:00 UTC. Việc dùng mốc 21:00 UTC khiến bot tưởng thị trường đóng cửa sớm hơn 1 tiếng; và ngược lại khi chuyển sang giờ mùa hè (EDT, UTC-4).
- **Giải pháp**:
  1. Sử dụng thư viện `zoneinfo.ZoneInfo("America/New_York")` để chuyển đổi timestamp sang giờ New York thực tế.
  2. Kiểm tra chính xác theo wall-clock time của New York:
     - Thứ Sáu sau 17:00 NY -> Đóng cửa.
     - Toàn bộ Thứ Bảy -> Đóng cửa.
     - Chủ Nhật trước 18:00 NY -> Đóng cửa.
     - Thứ Hai đến Thứ Năm từ 17:00 đến 18:00 NY -> Giờ bảo trì hằng ngày.
- **Tiêu chí nghiệm thu**: Kiểm tra timestamp tại cả hai giai đoạn EDT và EST, xác minh chuyển trạng thái `WAITING_MARKET` chính xác đến từng phút.

---

### OBS-08: Xử Lý Gói Tin Lồng WebSocket & Cơ Chế Làm Mới Giao Diện

- **Hiện tượng**:
  Trong `useObservationDashboard.ts`, WebSocket listener kiểm tra trực tiếp `payload.type === 'DECISION_EVENT'`. Khi WebSocket server gửi gói tin có dạng phong bì lồng (ví dụ: `{ type: 'EVENT', data: { type: 'DECISION_EVENT', ... } }`), listener bỏ qua thông báo.
- **Hậu quả**:
  Giao diện không tự động cập nhật ngay khi bot có quyết định mới, buộc người dùng phải nhấn nút "Làm mới" thủ công.
- **Giải pháp**:
  1. Chuẩn hóa hàm bóc tách sự kiện `extractWsEventType(data)` hỗ trợ cả cấu trúc phẳng và cấu trúc lồng (`data.type`, `data.event`, `data.payload?.type`, `data.data?.type`).
  2. Bổ sung cơ chế debounced refresh (300ms) kết hợp `AbortController` để hạn chế tải server khi bot quét liên tục.
- **Tiêu chí nghiệm thu**: Kiểm thử unit test trên frontend với các mẫu gói tin WebSocket khác nhau.

---

### OBS-09: Nguyên Tắc Trình Bày Fail-Closed Trên UI (Truthful Presentation)

- **Hiện tượng**:
  Một số thành phần UI khi nhận giá trị `null` hoặc `undefined` có thể rơi vào fallback sai lầm thành `0` hoặc `$0.00` (ví dụ: hiển thị Winrate `0%` thay vì `N/A (0 lệnh)` khi tài khoản chưa đóng lệnh nào).
- **Hậu quả**:
  Tạo cảm giác bot đang giao dịch thua lỗ (winrate 0%) dù thực tế bot chưa vào lệnh nào.
- **Giải pháp**:
  1. Rà soát toàn bộ các hàm render trong `ObservationDashboard.tsx`.
  2. Mọi trường chưa có số liệu phải hiển thị dứt khoát `N/A` hoặc kèm chú thích rõ ràng.
- **Tiêu chí nghiệm thu**: Kiểm thử frontend component với snapshot rỗng, xác minh không xuất hiện số liệu giả định.

---

### OBS-10: Nâng Cấp Bộ Chạy Nghiệm Thu Tự Động (Acceptance Rigor)

- **Hiện tượng**:
  Bộ chạy `run_v17_6_acceptance.py` cần đảm bảo tính nghiêm ngặt tuyệt đối: không được phép bỏ qua bất kỳ gate nào, không được dùng cache kết quả cũ, và phải xác thực mã nguồn Git sạch trước khi kết luận `ACCEPTED`.
- **Hậu quả**:
  Nguy cơ phát hành bản release có lỗi tiềm ẩn nếu runner chấp nhận kết quả không đầy đủ.
- **Giải pháp**:
  1. Kiểm tra trạng thái Git sạch ở đầu và cuối runner.
  2. Xóa toàn bộ file JUnit XML cũ trước khi chạy từng gate test.
  3. Tính toán lại mã hash SHA-256 của toàn bộ file nguồn quan trọng vào `manifest.json`.
- **Tiêu chí nghiệm thu**: Runner thực thi trọn vẹn 16 gates và ghi nhận kết quả trung thực.

---

### OBS-11: Mở Rộng Bộ Test Hồi Quy (Regression Coverage)

- **Hiện tượng**:
  Cần bổ sung các ca kiểm thử chuyên sâu cho:
  - Timestamp authority của CanonicalQuote (OBS-01)
  - Timezone DST New York và weekend break (OBS-07)
  - Độc lập run (OBS-05)
  - Kháng lỗi transaction rollback (OBS-03)
- **Giải pháp**:
  Xây dựng các test file chuyên biệt cho V17.6.1:
  - `backend/tests/test_v17_6_1_quote_timestamps.py`
  - `backend/tests/test_v17_6_1_market_hours_dst.py`
  - `backend/tests/test_v17_6_1_run_isolation.py`
  - `backend/tests/test_v17_6_1_telemetry_resilience.py`
- **Tiêu chí nghiệm thu**: 100% test mới pass và toàn bộ test cũ pass.

---

### OBS-12: Quản Lý & Lưu Vết Sự Cố (Incident Persistence Lifecycle)

- **Hiện tượng**:
  Khi phát hiện sự cố (như rớt mạng feed hoặc stall), bản ghi trong bảng `engine_incidents` được tạo nhưng chưa có hàm tự động cập nhật `is_resolved = True` và `resolved_at_ms = now_ms` khi mạng phục hồi hoặc engine chạy lại bình thường.
- **Hậu quả**:
  Bảng incidents tích tụ các sự cố cũ dưới dạng "đang diễn ra", khiến thẻ quan sát báo sai số lượng sự cố tích cực.
- **Giải pháp**:
  1. Bổ sung phương thức `resolve_active_incidents(db, symbol, incident_type, resolved_at_ms)` trong `engine_observability_service.py`.
  2. Tự động gọi khi feed chuyển về `CONNECTED_FRESH` hoặc engine gửi heartbeat bình thường.
- **Tiêu chí nghiệm thu**: Kiểm thử ghi nhận sự cố khi ngắt kết nối và tự động đóng sự cố khi kết nối lại thành công.
