# BẢNG ĐĂNG KÝ VÀ THEO DÕI LỖI KỸ THUẬT AURUM V17.6.2
## OBSERVABILITY INTEGRITY, TRANSACTION SAFETY & VERIFIED RELEASE DEFECT REGISTER

**Hệ thống**: AURUM DESK — XAUUSDT SMC/ICT Paper Trading Engine  
**Phiên bản mục tiêu**: V17.6.2  
**Nhánh Git**: `fix/v14-1-rr-flow`  
**Git Baseline**: V17.6.1 (`b2fe799583a5d33fae530970c187bfa3f7426440`)  
**Tài liệu**: Danh mục chi tiết 16 khiếm khuyết kỹ thuật (FIX-01 đến FIX-16) cần khắc phục triệt để.

---

## Danh Mục Khiếm Khuyết Tổng Hợp

| Mã Lỗi | Ưu Tiên | Phân Loại | Mô Tả Khiếm Khuyết | Trạng Thái |
|:---:|:---:|---|---|:---:|
| **FIX-01** | P0 | Transaction Safety | `record_engine_heartbeat` và các hàm incident gọi `target_db.commit()` ngay cả trên borrowed caller session | PENDING |
| **FIX-02** | P0 | Coverage Mathematics | Tính toán coverage lọc interval dạng inclusion thay vì overlap (`end > win_start and start < win_end`), thiếu interval clipping/merging, fallback sai bằng `cycles * cadence` | PENDING |
| **FIX-03** | P0 | Financial Isolation | `build_observation_dashboard_snapshot` fallback sang truy vấn toàn bộ `PaperOrder` theo symbol mà không lọc theo run | PENDING |
| **FIX-04** | P0 | Acceptance Provenance | Runner chấp nhận git dirty hoặc thiếu kiểm tra toàn diện 100% các cổng bắt buộc | PENDING |
| **FIX-05** | P1 | WebSocket Events | `useObservationDashboard.ts` nhận envelope `DOMAIN_EVENT` trả về ngay type thô mà không unwrap `raw.event?.event_type` | PENDING |
| **FIX-06** | P1 | Engine Health | `detect_stalled_engine` ưu tiên weekend break trước stale heartbeat khiến tiến trình chết bị báo WAITING_MARKET thay vì STALLED | PENDING |
| **FIX-07** | P1 | Quote Validity | `build_market_feed_health_snapshot` chưa kiểm tra tính hữu hạn của bid/ask, `ask >= bid`, trạng thái `is_valid`, clock skew, và phân biệt nguồn REST fallback | PENDING |
| **FIX-08** | P1 | Waiting State | Waiting setup hiển thị các cơ hội cũ đã chuyển sang ARMED/FILLED/EXPIRED vì chỉ lọc `stage.in_(["WAITING", "WATCHING"])` mà không lấy latest state | PENDING |
| **FIX-09** | P1 | Decision & Cursor | `list_recent_decisions` không lọc theo symbol thực chất và cursor decode bỏ qua ngoại lệ thay vì trả về mã lỗi 422 | PENDING |
| **FIX-10** | P1 | Incident Persistence | Dashboard lấy incidents từ in-memory cache thay vì truy vấn `EngineIncidentRecord.status == 'ACTIVE'` từ DB | PENDING |
| **FIX-11** | P1 | Timezone Today | UI tính Today midnight bằng browser local time thay vì múi giờ New York / Vietnam tương ứng | PENDING |
| **FIX-12** | P1 | Regression Suites | Runner chưa thực thi đầy đủ các bộ kiểm thử hồi quy của V17.5.9 | PENDING |
| **FIX-13** | P1 | Cycle Durability | Evaluation cycles chỉ lưu trong ring buffer RAM, chưa có bảng `engine_evaluation_cycles` để lưu vết bền bỉ và đảm bảo idempotency | PENDING |
| **FIX-14** | P2 | Candle Freshness | Xác minh nến đóng chưa kiểm tra thứ tự sắp xếp và không loại bỏ nến có cờ đóng nhưng timestamp ở tương lai | PENDING |
| **FIX-15** | P2 | Market Calendar | Tính toán thời gian mở cửa thị trường dùng sampling xấp xỉ thay vì biên chính xác có kiểm chứng | PENDING |
| **FIX-16** | P2 | Frontend Race | Hook `useObservationDashboard` thiếu request generation token để ngăn chặn async response cũ ghi đè dữ liệu mới | PENDING |

---

## Chi Tiết Kỹ Thuật 16 Khiếm Khuyết

### FIX-01 (P0): Telemetry Commits Caller-Owned Transaction
- **RCA**: Trong `record_engine_heartbeat()`, `record_engine_incident()`, `resolve_engine_incident()`, `resolve_active_incidents()`, lệnh `target_db.commit()` được thực thi ngay cả khi `target_db` là `db` session được caller truyền vào (`close_session = False`).
- **Hậu quả**: Khi `ScalpWatcherOrchestrator.evaluate_cycle()` hoặc caller khác sửa đổi business model (ví dụ candidate, order, risk state) nhưng chưa commit, việc ghi telemetry vô tình commit sớm các thay đổi nghiệp vụ đó, phá vỡ transaction boundary.
- **Khắc phục**: Quy định rõ:
  - Nếu session là borrowed (`db is not None`): KHÔNG ĐƯỢC `commit()`, `rollback()`, hoặc `close()`. Chỉ `flush()` nếu cần.
  - Nếu session do hàm tự tạo (`db is None`): Mở owned session `SessionLocal()`, commit trong `try`, rollback khi lỗi, và close trong `finally`.
  - Caller muốn độc lập commit telemetry có thể gọi hàm chuyên dụng hoặc truyền `db=None`.

### FIX-02 (P0): Coverage Interval Clipping & Overlap Errors
- **RCA**: `calculate_observation_coverage()` truy vấn `start_ms >= effective_start` và `end_ms <= effective_end`, loại bỏ các interval giao nhau nhưng vượt biên cửa sổ. Ngoài ra, dùng fallback `min(denominator_ms, cycles * cadence * 1000)` giả định uptime mà không có timestamp evidence.
- **Khắc phục**:
  - Truy vấn overlap chuẩn: `end_ms > effective_start and start_ms < effective_end`.
  - Viết pure function `clip_interval` và `merge_time_intervals` để loại bỏ thời gian chồng lấn.
  - Loại bỏ hoàn toàn fallback `cycles * cadence` khỏi verified coverage.
  - Phân loại 5 trạng thái: `GOOD`, `DEGRADED`, `DOWN`, `UNVERIFIED`, `MARKET_CLOSED`. Khi market open ms = 0, coverage_pct = None và state = `MARKET_CLOSED`.

### FIX-03 (P0): Run Financial Fallback Leakage
- **RCA**: `build_observation_dashboard_snapshot()` khi không tìm thấy run summary đã fallback sang truy vấn tất cả `PaperOrder` theo `instrument == symbol` trong khoảng thời gian, gây trộn PnL giữa các run khác nhau.
- **Khắc phục**: Nghiêm cấm unscoped financial query. Chỉ lấy orders liên kết qua `PaperResearchOrderLink` của `run_id`. Nếu không có run_id hoặc thiếu liên kết, trả về status `INCOMPLETE` / `UNAVAILABLE`.

### FIX-04 (P0): Acceptance Accepts Dirty/Incomplete Source
- **RCA**: Runner V17.6.1 chỉ kiểm tra số lượng test mà không bắt buộc `git.is_dirty == False` và không đối chiếu chính xác tested source SHA với implementation commit.
- **Khắc phục**: Viết `tools/run_v17_6_2_acceptance.py` bắt buộc 100% các mandatory gates PASS, git clean, và xác thực đúng hash của implementation SHA.

### FIX-05 (P1): Nested DOMAIN_EVENT Refresh Failure
- **RCA**: Khi backend gửi `{ type: "DOMAIN_EVENT", event: { event_type: "order.armed" } }`, helper client đọc `raw.type` thấy "DOMAIN_EVENT" và dừng lại, không phát hiện được sự kiện con có thuộc observation hay không.
- **Khắc phục**: Xây dựng `extractObservationEventType` ưu tiên bóc tách `raw.event?.event_type` / `raw.payload?.event_type` trước khi fallback sang type cha.

### FIX-06 (P1): Engine Stale Heartbeat Hidden by Market Closure
- **RCA**: `detect_stalled_engine` kiểm tra `is_xauusdt_weekend_break` ở đầu hàm, trả về `WAITING_MARKET` kể cả khi heartbeat của worker đã chết từ lâu.
- **Khắc phục**: Tách biệt 2 trục: `process_state` (RUNNING, STALLED, STOPPED) và `market_state` (OPEN, WEEKEND_CLOSED, DAILY_MAINTENANCE). Nếu heartbeat stale > threshold, process_state luôn là `STALLED`.

### FIX-07 (P1): Market Quote Freshness Can Be Incorrectly Asserted
- **RCA**: `build_market_feed_health_snapshot` chỉ xem timestamp mà chưa kiểm tra tính hợp lệ của số liệu báo giá (`CanonicalQuote.is_valid`, `bid > 0`, `ask >= bid`, finite numbers, clock skew, phân biệt nguồn WS vs REST ticker).
- **Khắc phục**: Kiểm tra đầy đủ tính hợp lệ của quote, phân loại rõ `quote_source`, `transport_source`, từ chối quote đảo spread (`ask < bid`) hoặc NaN.

### FIX-08 (P1): Latest WAITING Setup Can Be Outdated
- **RCA**: `waiting_setups` truy vấn các row có `stage.in_(["WAITING", "WATCHING"])`. Nếu một setup đã chuyển sang ARMED hoặc EXPIRED ở các event sau, nó vẫn xuất hiện trong danh sách chờ.
- **Khắc phục**: Truy vấn theo latest event của từng `opportunity_id`. Chỉ hiển thị nếu latest stage thực sự là WAITING hoặc WATCHING.

### FIX-09 (P1): Decision Symbol Filtering & Cursor Validation Incomplete
- **RCA**: `list_recent_decisions` nhận tham số `symbol` nhưng không lọc `symbol` trong query. Pagination cursor bị lỗi bị bắt bằng broad except và bỏ qua.
- **Khắc phục**: Đảm bảo symbol filtering chặt chẽ; decode base64 cursor với kiểm tra format, timestamp, ID; ném HTTP 422 nếu cursor không hợp lệ.

### FIX-10 (P1): Incident Dashboard Ignores Persisted Active Records
- **RCA**: Dashboard lấy incidents từ `self._active_incidents` (RAM), làm mất danh sách sự cố khi backend restart.
- **Khắc phục**: Truy vấn trực tiếp từ bảng `EngineIncidentRecord` với `status == 'ACTIVE'`, phân lọc theo `run_id` và `symbol`.

### FIX-11 (P1): Today Timezone Uses Browser-Local Midnight
- **RCA**: Hook frontend tính `d.setHours(0,0,0,0)` theo giờ local trình duyệt thay vì múi giờ được chọn (Asia/Ho_Chi_Minh hoặc America/New_York).
- **Khắc phục**: Tính midnight theo IANA timezone chuẩn thông qua `Intl.DateTimeFormat` và tính timestamp tương ứng.

### FIX-12 (P1): Full V17.5.9 Regression Not Executed
- **RCA**: Runner chỉ chạy 1 suite financial parity mà bỏ qua các suite trọng yếu khác của V17.5.9.
- **Khắc phục**: Đưa toàn bộ các test suites V17.5.9 vào danh sách gate bắt buộc của runner V17.6.2.

### FIX-13 (P1): Cycle Idempotency and Durability Unproven
- **RCA**: Chu kỳ đánh giá chỉ lưu trong RAM ring buffer, không thể phục hồi sau restart và không chống trùng lặp.
- **Khắc phục**: Tạo bảng `engine_evaluation_cycles` (`EngineEvaluationCycleRecord`), lưu vết chu kỳ bền bỉ với `cycle_id` duy nhất (idempotent).

### FIX-14 (P2): Candle Freshness Source and Ordering Weaknesses
- **RCA**: Tìm nến đóng không xác minh thứ tự timestamp và không loại trừ nến có timestamp trong tương lai.
- **Khắc phục**: Sắp xếp nến theo timestamp tăng dần, từ chối nến có timestamp đóng ở tương lai.

### FIX-15 (P2): Market-Calendar Closure Assumptions Require Authority
- **RCA**: Tính toán thời gian mở cửa dùng sampling gần đúng.
- **Khắc phục**: Xác định chính xác các khoảng thời gian thị trường mở cửa theo lịch New York chuẩn.

### FIX-16 (P2): Frontend Stale Request/Run Reconciliation Weaknesses
- **RCA**: Khi đổi run_id hoặc cửa sổ thời gian nhanh, async response cũ có thể hoàn tất sau và đè dữ liệu mới.
- **Khắc phục**: Thêm request token tracking trong `useObservationDashboard` để bỏ qua các phản hồi lỗi thời.
