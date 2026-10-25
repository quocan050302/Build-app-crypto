# KIỂM TOÁN TÍNH PHÂN LẬP & TRUY NGUYÊN DANH TÍNH PHIÊN NGHIÊN CỨU (RUN ID)
## AURUM DESK — PHIÊN BẢN V17.6.4 (Phase M, PART 161)

---

### 1. VẤN ĐỀ TRỌNG YẾU TRƯỚC V17.6.4 (THE PROBLEM)

Trong các phiên bản trước V17.6.4, hệ thống quan sát gặp phải tình trạng rò rỉ và mất mát danh tính phiên nghiên cứu (`run_id`):
1. **Chu kỳ mồ côi (Orphan Cycles)**: Khi `research_context` bị thiếu hoặc chu kỳ gặp lỗi ném exception trong quá trình tính toán SMC, hàm xử lý lỗi ghi nhận chu kỳ vào CSDL với `run_id = None`.
2. **Nghiệp vụ bỏ qua phân lập**: Scheduler và Watcher gọi `record_engine_heartbeat` hoặc `record_evaluation_cycle` mà không truyền phiên nghiên cứu hiện hành.
3. **Hiển thị nhầm lẫn giữa các phiên**: API và Dashboard tổng hợp số liệu của Phiên B vào báo cáo của Phiên A, vi phạm nguyên tắc truy nguyên bằng chứng.

---

### 2. CƠ CHẾ GIẢI QUYẾT TRONG V17.6.4 (THE SOLUTION)

#### 2.1. Phân Giải `effective_run_id` Tập Trung
Trong `backend/services/scalp_watcher_orchestrator.py`:

```python
# Phân giải run_id hợp lệ ngay từ đầu chu kỳ đánh giá
effective_run_id = None
if research_context and getattr(research_context, "run_id", None):
    effective_run_id = research_context.run_id
else:
    active_run = resolve_active_observation_run(db, symbol=symbol)
    if active_run:
        effective_run_id = active_run.run_id
```

#### 2.2. Lan Truyền Bắt Buộc trên Mọi Nhánh Kết Thúc
`effective_run_id` được truyền một cách tất định vào mọi điểm thoát của chu kỳ:
- **Nhánh vô hiệu hóa (Disabled/Paused)**: Ghi nhận chu kỳ với `run_id=effective_run_id`.
- **Nhánh thiếu báo giá (Missing Quote)**: Ghi nhận chu kỳ với `run_id=effective_run_id`.
- **Nhánh thành công (Completed/Evaluated)**: Ghi nhận chu kỳ với `run_id=effective_run_id`.
- **Nhánh thất bại (Failed/Crashed)**: `_finalize_failed_cycle(db, ..., run_id=effective_run_id)`.

#### 2.3. Ranh Giới Thời Gian Khảo Sát Bị Giới Hạn Bởi Vòng Đời Run (Run Boundary Clipping)
Trong `calculate_observation_coverage`:
- Khi người dùng truy vấn `start_ms` và `end_ms` cho một `run_id`:
  - `effective_start = max(start_ms, run.started_at_ms)`
  - `effective_end = min(end_ms, run.ended_at_ms or end_ms)`
- Nếu `effective_end <= effective_start`: Hệ thống trả về trạng thái trung thực `coverage_state = "NO_ELIGIBLE_WINDOW"`, `coverage_pct = None`, không bịa đặt số liệu 0.0% hay 100.0%.

---

### 3. MA TRẬN BẰNG CHỨNG KIỂM THỬ (VERIFICATION MATRIX)

| Test ID | Tình Huống Kiểm Thử | Hành Vi Xác Minh | Kết Quả |
|:---|:---|:---|:---:|
| **RID01** | Khởi chạy chu kỳ chuẩn có `research_context` | `cycle.run_id` lưu đúng ID của ngữ cảnh | **PASS** |
| **RID02** | Khởi chạy không có context nhưng có active run trong DB | Tự động phân giải và gán đúng active `run_id` | **PASS** |
| **RID03** | Khởi chạy khi không có active run trong DB | Gán an toàn `run_id = None` không gây lỗi | **PASS** |
| **RID04** | Chu kỳ bị vô hiệu hóa (Strategy Disabled) | `cycle.run_id` được bảo toàn chính xác | **PASS** |
| **RID05** | Chu kỳ thiếu báo giá (No Quote) | `cycle.run_id` được bảo toàn chính xác | **PASS** |
| **RID06** | Chu kỳ bị crash bất ngờ (Unhandled Exception) | `_finalize_failed_cycle` lưu đúng `run_id` | **PASS** |
| **RID07** | Phân lập truy vấn Run A và Run B | Lệnh và chu kỳ của Run A không xuất hiện ở Run B | **PASS** |
| **RID08** | Heartbeat của Watcher loop | Lưu đúng `run_id` của phiên đang hoạt động | **PASS** |
| **RID09** | Cắt ranh giới cửa sổ khảo sát (Run Boundary Clipping) | Không tính thời gian trước khi Run được tạo | **PASS** |
| **RID10** | Cắt ranh giới khi Run đã đóng (Ended Run) | Không tính thời gian sau khi Run kết thúc | **PASS** |
| **RID11** | Cửa sổ truy vấn nằm hoàn toàn trước khi Run bắt đầu | Trả về `NO_ELIGIBLE_WINDOW` | **PASS** |
| **RID12** | Restart động cơ đọc lại từ CSDL | Khôi phục chính xác chu kỳ mới nhất theo đúng `run_id` | **PASS** |
