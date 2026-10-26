# HỢP ĐỒNG BẰNG CHỨNG QUAN SÁT & TÍNH TOÀN VẸN CHỈ SỐ TELEMETRY
## AURUM DESK — PHIÊN BẢN V17.6.2

---

### 1. MỤC ĐÍCH & PHẠM VI HỢP ĐỒNG (PURPOSE & SCOPE)

Tài liệu này xác lập các chuẩn mực bất biến (invariants) về tính trung thực, chính xác và nhất quán toán học của hệ thống đo lường quan sát (Observability Subsystem) trong AURUM V17.6.2.

Mọi số liệu hiển thị trên Dashboard Quan Sát Bot (Telemetry Dashboard) và trả về qua API phải tuân thủ nghiêm ngặt nguyên tắc: **"Sự thật khách quan từ dữ liệu thực — Không xấp xỉ nhân tạo, Không rò rỉ phạm vi, Không suy đoán khi thiếu dữ liệu"**.

---

### 2. HỢP ĐỒNG TOÁN HỌC ĐỘ BAO PHỦ QUAN SÁT (COVERAGE & UPTIME CONTRACT) — [FIX-02]

#### 2.1. Loại bỏ hoàn toàn công thức xấp xỉ ảo
- **Cơ chế cũ (V17.6.1 trở về trước)**: Khi không có dữ liệu khoảng chu kỳ, hệ thống nhân tạo ra thời gian quan sát dựa trên `cycles * 5000ms`. Đây là số liệu giả mạo vì chu kỳ có thể bị gián đoạn, bỏ qua, hoặc chạy trên khoảng thời gian khác.
- **Quy tắc V17.6.2**: Loại bỏ hoàn toàn fallback này. Thời gian quan sát thực tế CHỈ ĐƯỢC TÍNH từ các khoảng thời gian có đo lường thực tế (`observed_intervals_ms`).

#### 2.2. Thuật toán giao cắt và gộp khoảng thời gian thuần túy (Pure Interval Math)
1. **Truy vấn giao cắt thời gian trong CSDL**:
   ```sql
   WHERE end_ms > effective_start AND start_ms < effective_end
   ```
2. **Cắt khoảng thời gian vào ranh giới cửa sổ khảo sát (Clipping)**:
   ```python
   def clip_interval(c_start: int, c_end: int, w_start: int, w_end: int) -> Optional[Tuple[int, int]]:
       s = max(c_start, w_start)
       e = min(c_end, w_end)
       return (s, e) if s < e else None
   ```
3. **Gộp các khoảng thời gian trùng lặp (Interval Merging)**:
   - Sắp xếp các khoảng hợp lệ theo `start`.
   - Gộp các khoảng liền kề hoặc giao nhau: `if curr.start <= last.end: last.end = max(last.end, curr.end)`.
4. **Xử lý khi thị trường đóng cửa toàn phần**:
   - Nếu `expected_market_open_ms == 0` (ví dụ toàn bộ cửa sổ nằm trong cuối tuần hoặc giờ bảo trì):
     - `coverage_pct = None`
     - `coverage_state = "MARKET_CLOSED"`
     - Tránh hoàn toàn lỗi chia cho 0 (`ZeroDivisionError`) hoặc hiển thị 0.0% sai lệch.

---

### 3. HỢP ĐỒNG PHÂN LẬP SỐ LIỆU TÀI CHÍNH (FINANCIAL METRICS RUN ISOLATION) — [FIX-03]

1. **Phân lập tuyệt đối theo `run_id`**:
   - Mọi số liệu tài chính sau phí (Closed Trades, Net PnL, Winrate, Max Drawdown, Equity) PHẢI gắn liền với một phiên nghiên cứu hoặc phiên vận hành cụ thể (`target_run_id`).
   - Cấm triệt để việc truy vấn toàn bộ bảng `paper_orders` không có điều kiện `run_id` khi không tìm thấy lệnh trong phiên hiện tại.
2. **Nguyên tắc Thất bại Đóng (Fail-Closed Presentation)**:
   - Nếu `target_run_id` là `None` hoặc chưa có liên kết lệnh (`PaperResearchOrderLink`):
     - Trả về `financial_summary = None`.
     - Frontend bắt buộc hiển thị trạng thái `N/A` (Không khả dụng) thay vì tự ý hiển thị `$0.00` hay con số vốn giả lập `$1,000.00`.
   - Nếu `closed_trades == 0`: Tỷ lệ thắng (Winrate) phải trả về `0.0` với ghi chú rõ ràng `N/A (0 lệnh)`.

---

### 4. HỢP ĐỒNG ĐỘ TIN CẬY DỮ LIỆU & BÁO GIÁ THỊ TRƯỜNG (MARKET DATA INTEGRITY) — [FIX-07, FIX-14, FIX-15]

1. **Kiểm tra cấu trúc báo giá chuẩn tắc (Canonical Quote Validation)**:
   - Báo giá được coi là hợp lệ khi và chỉ khi:
     - Giá `bid > 0` và `ask > 0`.
     - Giá trị số thực hữu hạn (không phải `NaN`, `Infinity`).
     - Không bị đảo ngược spread: `ask >= bid`.
     - Trạng thái truyền thông không phải `INVALID`.
2. **Phân cấp trạng thái vận chuyển và tính mới (Transport State & Freshness)**:
   - Ưu tiên 1: Nếu WebSocket đang `CONNECTING` -> `freshness_state = "RECONNECTING"`.
   - Ưu tiên 2: Nếu WebSocket đang `DISCONNECTED` hoặc chưa có báo giá -> `freshness_state = "DISCONNECTED"`.
   - Ưu tiên 3: Nếu kết nối bình thường nhưng dữ liệu số bị lỗi cấu trúc -> `freshness_state = "INVALID_QUOTE"`.
   - Ưu tiên 4: Phân tách rõ nguồn cấp báo giá: `"WS"` (WebSocket thời gian thực) hay `"REST_FALLBACK"` (dự phòng qua REST API).
3. **Tính toàn vẹn của chuỗi nến (Candle Sequence)**:
   - Bộ đệm nến M1/M5 phải được sắp xếp tăng dần theo thời gian mở nến (`t_start`).
   - Loại bỏ hoàn toàn các nến đóng có mốc kết thúc vượt quá thời gian hiện tại (`t_end > now_ms`).

---

### 5. HỢP ĐỒNG TÍNH BỀN VỮNG & IDEMPOTENCY CỦA CHU KỲ (CYCLE DURABILITY & IDEMPOTENCY) — [FIX-13]

1. **Bảng lưu trữ bền vững `engine_evaluation_cycles`**:
   - Mọi chu kỳ phân tích SMC/ICT của engine đều được lưu trữ trực tiếp vào cơ sở dữ liệu với khóa định danh duy nhất `cycle_id`.
   - Ghi nhận đầy đủ: `started_at_ms`, `completed_at_ms`, `duration_ms`, `symbol`, `decision_status`, `reason_primary`, `memory_rss_mb`.
2. **Xử lý trùng lặp an toàn (Idempotency)**:
   - Nếu engine gửi lại cùng một `cycle_id` (do retry hoặc reconnect), hệ thống bỏ qua việc ghi mới, trả về cờ `deduplicated: True`, bảo đảm không làm biến dạng số liệu thống kê chu kỳ.

---

### 6. BẢNG ĐỐI CHIẾU KIỂM BẰNG CHỨNG TỰ ĐỘNG (VERIFICATION MATRIX)

| Mã Hợp Đồng | Khiếm Khuyết Khắc Phục | Suite Kiểm Thử Tự Động | Trạng Thái Nghiệm Thu |
|:---|:---:|:---|:---:|
| **EVD-CV01..08** | FIX-02 (Coverage clipping, zero duration, overlap) | `test_v17_6_2_coverage_intervals.py` | **100% PASS** (8/8) |
| **EVD-FIN01..02**| FIX-03 (Financial run isolation & fail-closed) | `test_v17_6_2_financial_run_isolation.py` | **100% PASS** (2/2) |
| **EVD-FD01..04** | FIX-07, FIX-14, FIX-15 (Quote validity, REST fallback) | `test_v17_6_2_feed_authority.py` | **100% PASS** (4/4) |
| **EVD-CY01..04** | FIX-13 (Cycle durability, restart, idempotency) | `test_v17_6_2_cycle_durability.py` | **100% PASS** (4/4) |
| **EVD-PG01..05** | FIX-09 (422 cursor validation, symbol filter) | `test_v17_6_2_decision_pagination.py` | **100% PASS** (5/5) |
| **EVD-RP01..03** | FIX-06, FIX-08, FIX-10 (Heartbeat stall, waiting funnel) | `test_v17_6_2_defects_reproduction.py` | **100% PASS** (3/3) |
