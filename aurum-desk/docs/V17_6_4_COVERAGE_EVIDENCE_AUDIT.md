# KIỂM TOÁN TÍNH TOÀN VẸN BẰNG CHỨNG ĐỘ BAO PHỦ QUAN SÁT 24/7
## AURUM DESK — PHIÊN BẢN V17.6.4 (Phase M, PART 161)

---

### 1. NGUYÊN TẮC BẰNG CHỨNG QUAN SÁT (OBSERVATION EVIDENCE PRINCIPLES)

1. **Bằng Chứng Bền Vững (Durable Evidence Only)**:
   - Nghiêm cấm hoàn toàn việc ước lượng thời gian hoạt động (uptime) từ số lượng chu kỳ (`cycle_count * 5s`).
   - Mọi giây quan sát phải được bảo chứng bằng các khoảng quan sát thực tế (`PaperObservationInterval`) được tạo lập liên tục từ các tín hiệu nhịp tim (heartbeats).
2. **Mẫu Số Bao Phủ 24/7 (24/7 Denominator Truth)**:
   - Đối với hợp đồng Bitget XAUUSDT perpetual, giao dịch mở liên tục 24/7.
   - Mẫu số độ bao phủ là toàn bộ thời gian trôi qua thực tế trong khoảng khảo sát (`effective_end - effective_start`).
   - Không được khấu trừ thời gian thứ Bảy/Chủ Nhật thành "thị trường đóng cửa".
3. **Thứ Bậc Phân Cấp Chất Lượng (Quality Precedence)**:
   - Trong các khoảng thời gian bị chồng lấn: `DOWN` ưu tiên cao hơn `DEGRADED`, và `DEGRADED` ưu tiên cao hơn `GOOD`.
   - Chỉ các khoảng thời gian đạt chất lượng `GOOD` và `DEGRADED` mới được tính vào độ sẵn sàng vận hành.

---

### 2. CƠ CHẾ SINH KHOẢNG QUAN SÁT (`ObservationIntervalService`)

- **Nhận diện nhịp tim kế tiếp**: Khi nhận được tín hiệu nhịp tim mới với mốc thời gian $T_{new}$:
  - Tính khoảng cách với nhịp tim trước đó: $\Delta t = T_{new} - T_{prev}$.
  - Nếu $\Delta t \le 45,000 \text{ ms}$ và cùng trạng thái chất lượng: Kéo dài khoảng quan sát hiện hành: $T_{end} = T_{new}$.
  - Nếu $\Delta t > 45,000 \text{ ms}$ hoặc thay đổi chất lượng: Đóng khoảng hiện hành, lưu vào CSDL qua `db.get_bind()`, và mở một khoảng quan sát mới bắt đầu từ $T_{new}$.
- **Xử lý trùng lặp và ghi đè an toàn (Idempotency)**: Nếu nhận tín hiệu nhịp tim có timestamp $\le T_{end}$, bỏ qua để chống duplicate.
- **Xả đệm khi dừng hệ thống (`flush_active_intervals`)**: Lưu toàn bộ các khoảng đang mở xuống CSDL bền vững.

---

### 3. THUẬT TOÁN ĐỘ BAO PHỦ QUAN SÁT (`calculate_observation_coverage`)

```
   Cửa sổ truy vấn [start_ms, end_ms]
   |========================================================================|
      [Run.started_at_ms]                                  [Run.ended_at_ms]
      |----------------------------------------------------|
                           Cắt ranh giới:
      |====================================================|
                     Cửa sổ hữu hiệu: [effective_start, effective_end]
                     Mẫu số = effective_end - effective_start (24/7)

   Khoảng quan sát DB (GOOD):
          [========]          [===============]        [=======]
              |                      |                     |
              v                      v                     v
   Giao cắt & Hợp nhất:
          [========]          [===============]        [=======]
          Tử số = Tổng thời lượng GOOD giao cắt với Cửa sổ hữu hiệu

   Tỷ lệ bao phủ = (Tử số / Mẫu số) * 100%
```

- **Chính sách**: `coverage_policy_version = "V17.6.4_BITGET_PERPETUAL_24_7"`.
- **Nguồn lịch**: `schedule_source = "BITGET_PRODUCT_SPEC"`.
- **Trạng thái đặc biệt**:
  - Khi không tìm thấy `run_id`: `coverage_state = "RUN_NOT_FOUND"`, `coverage_pct = None`.
  - Khi cửa sổ khảo sát không hợp lệ hoặc nằm ngoài vòng đời Run: `coverage_state = "NO_ELIGIBLE_WINDOW"`, `coverage_pct = None`.

---

### 4. MA TRẬN BẰNG CHỨNG KIỂM THỬ TỰ ĐỘNG

#### 4.1. Bộ Kiểm Thử Sinh Khoảng Quan Sát (`test_v17_6_4_interval_producer.py`)
| Test ID | Tình Huống Kiểm Thử | Trạng Thái Kỳ Vọng | Kết Quả |
|:---|:---|:---|:---:|
| **IP01** | Nhịp tim liên tục dưới 45s gộp thành khoảng liên tục | 1 khoảng quan sát duy nhất kéo dài | **PASS** |
| **IP02** | Khoảng cách nhịp tim vượt 45s (Gap > 45s) | Tách thành 2 khoảng quan sát riêng biệt | **PASS** |
| **IP03** | Chuyển đổi chất lượng từ GOOD sang DEGRADED | Đóng khoảng GOOD, mở khoảng DEGRADED mới | **PASS** |
| **IP04** | Chuyển đổi chất lượng từ DEGRADED sang DOWN | Đóng khoảng DEGRADED, mở khoảng DOWN mới | **PASS** |
| **IP05** | Nhận tín hiệu timestamp trùng hoặc lùi thời gian | Idempotent, không tạo khoảng lỗi | **PASS** |
| **IP06** | Xả đệm khi shutdown (`flush_active_intervals`) | Lưu thành công khoảng đang mở vào CSDL | **PASS** |
| **IP07** | Cô lập khoảng quan sát theo từng `run_id` | Run A và Run B có khoảng quan sát tách biệt | **PASS** |
| **IP08** | Ghi nhận khoảng quan sát độc lập với transaction caller | Commit độc lập qua `db.get_bind()` | **PASS** |
| **IP09** | Khôi phục và nối tiếp khoảng quan sát sau khởi động lại | Nối tiếp an toàn nếu trong ngưỡng 45s | **PASS** |
| **IP10** | Giới hạn độ dài tối đa của một khoảng quan sát (Max Span) | Tự động phân đoạn an toàn | **PASS** |
| **IP11** | Ghi nhận đồng thời đa luồng (Thread-Safety) | Không xảy ra race condition hay dead lock | **PASS** |
| **IP12** | Xử lý an toàn khi CSDL bị khóa tạm thời | Retry thành công không làm mất nhịp tim | **PASS** |

#### 4.2. Bộ Kiểm Thử Toán Học Độ Bao Phủ 24/7 (`test_v17_6_4_24_7_coverage.py`)
| Test ID | Tình Huống Kiểm Thử | Trạng Thái Kỳ Vọng | Kết Quả |
|:---|:---|:---|:---:|
| **CV01** | Quan sát hoàn hảo 24h thứ Bảy trên hợp đồng 24/7 | `coverage_pct = 100.0%`, `state = "FULL"` | **PASS** |
| **CV02** | Thứ Bảy 0 có khoảng quan sát nào | `coverage_pct = 0.0%`, `state = "LOW"` | **PASS** |
| **CV03** | Chủ Nhật quan sát một nửa thời gian (12h/24h) | `coverage_pct = 50.0%`, `state = "PARTIAL"` | **PASS** |
| **CV04** | Cuối tuần không còn bị coi là "thị trường đóng cửa" | Không bao giờ trả về `MARKET_CLOSED` | **PASS** |
| **CV05** | Truy vấn `run_id` không tồn tại | `coverage_state = "RUN_NOT_FOUND"`, `pct = None` | **PASS** |
| **CV06** | Phân lập tính toán giữa Run A và Run B | Bằng chứng của Run A không tăng coverage Run B | **PASS** |
| **CV07** | Cắt ranh giới cửa sổ khảo sát (Run Boundary Clipping) | Mẫu số tính từ thời điểm Run bắt đầu | **PASS** |
| **CV08** | Cửa sổ truy vấn kết thúc trước khi Run bắt đầu | `coverage_state = "NO_ELIGIBLE_WINDOW"` | **PASS** |
| **CV09** | Thứ bậc phân cấp: DOWN lấn át DEGRADED và GOOD | Khoảng DOWN không được tính vào GOOD | **PASS** |
| **CV10** | Gộp các khoảng quan sát bị trùng lặp thời gian | Tránh cộng dồn thời gian ảo (Overlap Merge) | **PASS** |
| **CV11** | Cắt khoảng quan sát thò ra ngoài cửa sổ khảo sát | Chỉ tính phần giao cắt nằm trong cửa sổ | **PASS** |
| **CV12** | Chính sách bao phủ phản ánh đúng phiên bản V17.6.4 | `coverage_policy_version = "...24_7"` | **PASS** |
| **CV13** | Nguồn lịch hợp đồng phản ánh đúng đặc tả Bitget | `schedule_source = "BITGET_PRODUCT_SPEC"` | **PASS** |
| **CV14** | Mẫu số thời gian lớn (7 ngày liên tục) | Tính toán chính xác trên 168 giờ | **PASS** |
| **CV15** | Động cơ bị dừng 2 giờ giữa chừng | Giảm chính xác tỷ lệ tương ứng khoảng dừng | **PASS** |
| **CV16** | Đọc lại từ CSDL thực sau khi xóa RAM cache | Kết quả tính toán không đổi | **PASS** |
| **CV17** | Mất kết nối báo giá biến khoảng quan sát thành DEGRADED | Tách bạch giữa GOOD và DEGRADED | **PASS** |
| **CV18** | Không có bằng chứng DB thì không suy diễn từ chu kỳ | Tuyệt đối không fallback sang ước lượng ảo | **PASS** |
