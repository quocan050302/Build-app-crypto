# Hướng Dẫn Vận Hành CG-01 Paper (V16.18)

Tài liệu hướng dẫn thực dụng dành cho kỹ sư và người vận hành hệ thống AurumDesk theo chuẩn V16.18.

---

## 1. Xác Nhận Feed Khỏe (Market Data Feed Health)
Kiểm tra trạng thái WebSocket Bitget và nến đóng qua API:
```bash
curl -s http://localhost:8000/api/cg01/health | jq .
```
- **Tiêu chuẩn khỏe**:
  - `feed_status`: `"HEALTHY"`
  - `last_quote_age_ms`: `< 3000` (độ trễ dưới 3 giây)
  - `candle_cache`: nến 1M, 5M, 15M, 1H, 4H liên tục, không có `DATA_GAP`.

---

## 2. Cách Bắt Đầu Đợt Quan Sát (Start Paper Observation Run)
Tạo đợt nghiên cứu quan sát mới với định danh và phân vùng dữ liệu cách ly:
```bash
curl -X POST http://localhost:8000/api/cg01/paper/run/start \
  -H "Content-Type: application/json" \
  -d '{"symbol": "XAUUSDT", "scope": "CG01", "initial_capital_usdt": 1000.0}'
```
- Phản hồi trả về `run_id` (ví dụ: `run_cg01_xauusdt_...`), trạng thái `COLLECTING`.
- **Lưu ý quan trọng (Rule 006, 057)**: Bắt đầu quan sát KHÔNG tự động bật giao dịch tiền thật hay tự động arm lệnh.

---

## 3. Xem Trạng Thái Global Auto Đang ON Hay OFF
```bash
curl -s http://localhost:8000/api/auto/state | jq .
```
- Nếu `auto_enabled: false`: Bot chỉ quan sát, ghi nhận candidate và trace chẩn đoán.
- Muốn chuyển đổi trạng thái:
```bash
curl -X POST http://localhost:8000/api/auto/toggle -H "Content-Type: application/json" -d '{"enabled": false}'
```

---

## 4. Xác Nhận Profile Đang Sử Dụng (Profile Verification)
Hệ thống CG-01 phân biệt 2 profile:
- `PAPER_SHADOW`: Chế độ bóng (đánh giá thuần dữ liệu, ghi trace, không arm lệnh, không sửa watchlist/hypothesis sản xuất).
- `PAPER_EXECUTION`: Chế độ paper execution (được phép arm paper order khi và chỉ khi thỏa mãn đồng thời: `global_auto=True`, `is_owner=True`, thỏa mãn toàn bộ guards tin tức, quota, bài học).

Xác nhận profile hiện hành:
```bash
curl -s http://localhost:8000/api/cg01/telemetry/traces?limit=1 | jq '.[0].observation_mode'
```

---

## 5. Đọc Lý Do Không Vào Lệnh (Skip & Guard Blocked Reasons)
Khi thị trường có biến động nhưng bot không vào lệnh, truy xuất trace mới nhất:
```bash
curl -s http://localhost:8000/api/cg01/telemetry/traces?symbol=XAUUSDT&limit=5 | jq '.[] | {trace_id, status: .evaluation_status, skip_reason, delays: .delays}'
```
Các mã lý do phổ biến theo nghiệp vụ chuẩn:
- `GLOBAL_AUTO_DISABLED`: Tín hiệu hợp lệ nhưng người dùng đang để auto tắt.
- `HTF_CONTEXT_UNCERTAIN`: Cấu trúc 4H/1H chưa hình thành xu hướng rõ ràng.
- `NO_SWEEP_CONFIRMED`: Chưa quét thanh khoản vùng liquidity pool.
- `DISPLACEMENT_MSS_MISSING`: Không có sóng displacement phá vỡ cấu trúc thị trường.
- `FVG_UNTOUCHED`: Giá chưa chạm vùng Fair Value Gap hợp lệ.
- `RISK_REWARD_INSUFFICIENT`: Net RR sau phí và trượt giá không đạt ngưỡng tối thiểu.
- `NEWS_GUARD_ACTIVE`: Đang trong khoảng thời gian đóng băng do tin tức vĩ mô đỏ.
- `DAILY_CAP_REACHED`: Đã đạt giới hạn số lệnh tối đa trong ngày (quota limit).

---

## 6. Cách Xuất Báo Cáo Nghiệm Thu & Vận Hành
Chạy acceptance runner V16.18 để kiểm thử toàn diện và cập nhật các báo cáo:
```bash
python3 tools/run_v16_18_cg01_runtime_acceptance.py
```
Các báo cáo kết xuất tự động gồm:
- `docs/V16_18_CG01_ACCEPTANCE_REPORT.md`: Báo cáo kết quả 7 gate kỹ thuật.
- `docs/V16_18_CG01_IMPLEMENTATION_MAP.md`: Sơ đồ các hàm, transaction owner và file tương ứng.
- `docs/V16_18_CG01_PAPER_RUN_REPORT.md`: Báo cáo phản ánh dữ liệu thực tế từ database.
- `artifacts/run_v16_18_<timestamp>/acceptance_results.json`: Bằng chứng JSON chi tiết.

---

## 7. Cách Dừng An Toàn (Safe Shutdown)
Để kết thúc một đợt nghiên cứu quan sát:
```bash
curl -X POST http://localhost:8000/api/cg01/paper/run/complete \
  -H "Content-Type: application/json" \
  -d '{"run_id": "<RUN_ID>"}'
```
Khi dừng app: Lifespan sẽ tự động gửi tín hiệu dừng tới `cg01_evaluation_scheduler`, `cg01_projection_worker`, `stop_trace_persistence_worker()` và gọi `flush_pending_traces(timeout_s=3.0)` để ghi nhận toàn bộ dữ liệu pending vào SQLite trước khi tiến trình tắt hoàn toàn.
