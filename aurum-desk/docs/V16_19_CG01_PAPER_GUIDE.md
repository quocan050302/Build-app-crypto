# Hướng Dẫn Vận Hành CG-01 Paper (V16.19)

### 1. Quan Sát (Shadow) Khác Tự Động Vào Lệnh (Active)
- **Chế độ Quan sát (SHADOW)**:
  - Hệ thống liên tục thu thập nến đã đóng, chuẩn hóa dữ liệu đa khung thời gian (4H, 1H, 15M, 1M).
  - Đánh giá tín hiệu CG-01 theo cấu hình hiệu lực và giá quote thực tế.
  - **Không bao giờ tạo lệnh (`armed_order_id = None`)**, không thay đổi các vùng active hoặc gửi cảnh báo giao dịch.
- **Chế độ Thực thi Paper (ACTIVE)**:
  - Chỉ khi người dùng bật `Global Auto ON` và đáp ứng mọi chính sách giao dịch (Risk, Cap, News, Lesson), lệnh mới được tạo (`ARMED`).

### 2. Cách Xác Nhận Worker và Feed Khỏe
- **Kiểm tra Feed**:
  - `ValidatedQuote`: quote phải tươi (`freshness_sec < 5.0`), spread hợp lệ và không stale.
- **Kiểm tra Worker Health**:
  - Telemetry Worker: Hàng đợi `_TRACE_QUEUE` được rút định kỳ bởi worker nền độc lập.
  - Projection Worker: Kiểm tra bảng `paper_research_checkpoints` có `last_sequence` tiến đều.

### 3. Cách Xem Lý Do Chưa Arm Lệnh
- Khi có thiết lập nhưng không vào lệnh, xem trường `cg01_evaluation.guard_summary` hoặc `OpportunityStage`:
  - `GUARD_BLOCKED`: Bị chặn bởi một trong các guard (ví dụ: `AUTO_DISABLED`, `DAILY_CAP_REACHED`, `NEWS_BLACKOUT`, `RED_LESSON`).
  - `EVIDENCE_READY`: Đã đủ điều kiện nhưng đang ở chế độ SHADOW hoặc chờ trigger.

### 4. Cách Xem Run và Mode Hiện Tại
- Truy vấn API `/api/v1/paper-runs/active` hoặc hàm `resolve_active_observation_run(db, symbol="XAUUSDT", mode="ACTIVE")`.
- `scope`: Bắt buộc là `CG01`.
- `execution_mode`: `PAPER_EXECUTION` (ACTIVE) hoặc `SHADOW` (Quan sát).

### 5. Cách Xuất Thống Kê và Dừng An Toàn
- **Xuất thống kê**: Gọi hàm `build_cg01_paper_run_summary(run_id, db)` để lấy báo cáo đầy đủ sau phí, bảo đảm chỉ tính các lệnh có liên kết nhân quả chính thức.
- **Dừng an toàn**:
  - Gọi `flush_pending_traces(timeout_s=5.0)` để lưu hết trace còn trong hàng đợi.
  - Dừng scheduler và worker nền qua cơ chế lifespan (`stop_persistence_worker()`, `stop_projection_worker()`).
