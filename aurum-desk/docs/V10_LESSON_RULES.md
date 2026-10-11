# V10: Lesson Memory Integration & Governance Architecture

## 1. Mục Tiêu & Nguyên Tắc Cốt Lõi (Core Principles)
Hệ thống **V10 Lesson Governance** kết nối bài học kinh nghiệm (`Lesson`) đã được phê duyệt và cấu hình vào quy trình ra quyết định vào lệnh (Manual & Auto), đồng thời bảo toàn 100% các hàng rào an toàn gốc (baseline guards).

### Nguyên Tắc An Toàn Tuyệt Đối:
1. **Bảo toàn quản lý vị thế đang mở (Open Position Invariant):** Không bao giờ can thiệp, đóng, thay đổi SL/TP/khối lượng/đòn bẩy của bất kỳ vị thế đang mở nào do bài học hay quy tắc mới. Exit monitor (TP/SL/Liquidation/Manual) tiếp tục hoạt động độc lập và không thể bị gián đoạn.
2. **Không tự động biến prose text thành mã thực thi:** Tuyệt đối không dùng `eval()`, `exec()`, raw dynamic SQL hay gọi LLM trên từng tick giá. Mọi quy tắc thực thi đều qua **Structured Predicate Whitelist**.
3. **Phân tách độc lập 4 trục trạng thái:**
   - **Phê duyệt (`approval_status`):** `PENDING_REVIEW`, `APPROVED`, `REJECTED`, `ARCHIVED`.
   - **Kích hoạt (`enabled`):** `True` / `False`.
   - **Kiểm định cấu trúc (`validation_status`):** `VALID`, `INVALID`, `DRAFT`.
   - **Mức độ tác động (`severity` / `effect`):** Xanh / Vàng / Đỏ.
4. **Không nới lỏng baseline guards:** Bài học không thể hạ thấp các tiêu chuẩn an toàn cốt lõi (ví dụ: `net_rr` tối thiểu của rule không được phép dưới `2.0`, không override max 3 fills/day, max 1 open position, max 1 armed order, news lockouts).
5. **Không hứa hẹn lợi nhuận/winrate:** Màu Xanh chỉ mang tính tham khảo kinh nghiệm, không bảo đảm thắng; Màu Đỏ là hàng rào kỷ luật rủi ro, không phải tín hiệu đảo chiều.

---

## 2. Hệ Thống 3 Màu & Hiệu Lực Thực Tế (Three-Color Semantics)

| Màu | Phân loại (`severity`) | Hành động (`effect`) | Trạng thái hiển thị | Tác dụng được phép |
| :--- | :--- | :--- | :--- | :--- |
| **Xanh** | `INFO` | `ANNOTATE` | **CHỈ THAM KHẢO** | Hiển thị bài học/kinh nghiệm trong bảng kế hoạch lệnh; không can thiệp, không chặn, không nới điều kiện hay tăng rủi ro. |
| **Vàng** | `WARNING` | `WARN_ENTRY` | **ĐANG CẢNH BÁO** (khi Approved + Enabled) | Hiển thị cảnh báo với chỉ số đo lường thực tế (spread, R:R...) trước khi arm/fill; ghi vào `lessons_retrieved` snapshot; **không tự động chặn hoặc làm dừng Auto mode**. |
| **Đỏ** | `CRITICAL` | `BLOCK_ENTRY` | **ĐANG CHẶN ENTRY** (khi Approved + Enabled + Valid) | Chặn tạo lệnh chờ mới (`BEFORE_ARM`) hoặc chặn khớp lệnh (`BEFORE_FILL`) khi điều kiện cấu trúc vi phạm; trả về reason code rõ ràng `LESSON_RULE_BLOCKED`; hiển thị nguyên nhân và khuyến nghị xử lý. |

> **Lưu ý trạng thái Đỏ:**
> - Nếu người dùng cấu hình mức Đỏ nhưng chưa có predicate hợp lệ (`validation_status != 'VALID'`), hệ thống hiển thị badge **"CHƯA THỂ ÁP DỤNG"** và **tuyệt đối không âm thầm chặn lệnh**.
> - Nếu bài học bị Tắt (`enabled == False`), hiển thị **"ĐÃ TẮT CHẶN"**.
> - Bài học Xanh/Vàng không bao giờ có thể ghi đè (override) quy tắc Đỏ hoặc baseline risk guard.

---

## 3. Danh Mục Điều Kiện Hợp Lệ (Structured Predicate Whitelist)

Mọi quy tắc cấu trúc chỉ được xây dựng từ danh mục metrics đã được định kiểu (`typed parameters`) và có sẵn dữ liệu kiểm chứng:

| Metric | Kiểu dữ liệu | Ý nghĩa | Giới hạn an toàn (Safe Bounds) |
| :--- | :--- | :--- | :--- |
| `spread` | Float (points) | Spread giá vàng hiện tại | Ngưỡng `>= 0.0` (vd: `>= 0.40`) |
| `net_rr` | Float | Tỷ lệ R:R ròng dự kiến | Bắt buộc `threshold >= 2.0` (ngăn nới lỏng baseline) |
| `distance_to_entry_atr` | Float | Khoảng cách tới entry theo đơn vị ATR | Ngưỡng `>= 0.0` (vd: `<= 0.5`) |
| `quote_age_ms` | Float (ms) | Độ trễ báo giá thị trường | Ngưỡng `>= 0.0` (vd: `<= 3000`) |
| `evidence.sweep_detected`| Boolean | Đã xác nhận quét thanh khoản | `True` / `False` |
| `evidence.fvg_found` | Boolean | Đã phát hiện Fair Value Gap | `True` / `False` |
| `evidence.structure_confirmed`| Boolean | Đã xác nhận cấu trúc MSS/Displacement | `True` / `False` |
| `session` | String / List | Phiên giao dịch (`ASIA`, `LONDON`, `NY`) | In / Not In tập phiên hợp lệ |
| `entry_window` | Dict (`start`, `end`)| Khung giờ giao dịch UTC/VN | Định dạng `HH:MM` |

### Xử Lý Khi Thiếu Dữ Liệu Đầu Vào (Missing-Data Semantics):
- Với quy tắc **Vàng (`WARN_ENTRY`)**: Nếu thiếu metric kiểm tra, trả về trạng thái không đánh giá được: *"Chưa đủ dữ liệu đánh giá bài học này"*, không kết luận match và không chặn.
- Với quy tắc **Đỏ (`BLOCK_ENTRY`)**: Nếu thiếu dữ liệu bắt buộc (ví dụ không có quote live để tính spread), hệ thống kích hoạt chính sách fail-safe với mã lỗi riêng `LESSON_RULE_DATA_UNAVAILABLE`, không biến thành setup bị hủy sai lệch.
- **Fail-safe Engine Exception:** Nếu xảy ra ngoại lệ không mong muốn trong `LessonRuleService`, toàn bộ baseline guards vẫn được giữ nguyên vẹn 100%, ghi nhật ký diagnostics, và không bao giờ ảnh hưởng tới exit monitor.

---

## 4. Kiến Trúc Vòng Đời & Điểm Nối (Lifecycle Integration Hooks)

```
[Watch Setup / Market Quote]
          │
          ▼
   ┌──────────────┐
   │ BEFORE_ARM   │ ──► [EligibilityService]
   └──────────────┘           │
          │                   ├─ Evaluate Baseline Guards (Risk, Cap, News, Margin)
          │                   └─ LessonRuleService.evaluate_rules(scope="MANUAL"|"AUTO")
          │                             │
          │                             ├─ GREEN: Attach advisory notes
          │                             ├─ YELLOW: Record warning snapshot
          │                             └─ RED (Active): Block arm -> LESSON_RULE_BLOCKED
          ▼
 [PaperOrder: ARMED] ──► Lưu immutable `lessons_retrieved` snapshot
          │
          ▼
   ┌──────────────┐
   │ BEFORE_FILL  │ ──► [ExecutionCoordinator]
   └──────────────┘           │
          │                   ├─ Re-evaluate live quote & active rules
          │                   └─ RED Matched -> Reject order safely (Không tăng daily fills)
          ▼
 [Trade: FILLED/OPEN] ──► Giữ nguyên immutable snapshot cũ, không đổi khi rule sửa/archive
          │
          ▼
 [Exit Monitor / TP / SL / Reconciliation] ──► 100% Độc lập, không bị ảnh hưởng bởi Lesson Rules
```

---

## 5. Quản Trị Phiên Bản & Độc Lập Dữ Liệu (Governance & Immutability)
1. **Optimistic Locking (`revision`):** Mọi cập nhật quy tắc bài học đều kiểm tra trường `revision`. Nếu phiên bản gửi lên không khớp, API lập tức trả về `HTTP 409 Conflict (STALE_EDIT)` để tránh ghi đè dữ liệu giữa các phiên làm việc.
2. **Phiên bản độc lập (`version`):** Khi một bài học đang hoạt động được chỉnh sửa điều kiện, phiên bản mới được tạo ra (`version + 1`).
3. **Tính bất biến lịch sử (Historical Immutability):** Các lệnh đã khớp (`Trade`) lưu giữ snapshot nguyên bản `lessons_retrieved` tại thời điểm quyết định. Việc Sửa (`Edit`), Lưu trữ (`Archive`) hay Từ chối (`Reject`) bài học sau này **tuyệt đối không làm biến đổi snapshot hay lịch sử lệnh cũ**.
4. **Loại trừ triệt để Legacy Queries:**
   - Bộ lọc `PENDING_REVIEW` chỉ truy vấn chính xác `status == 'PENDING_REVIEW'`, hoàn toàn loại bỏ `REJECTED`.
   - Hàm `get_approved_lessons_for_strategy` và `retrieve_active_rules` chỉ lấy các bài học có `status == 'APPROVED'`, `is_approved == True`, và loại bỏ dứt điểm `ARCHIVED` và `REJECTED`.

---

## 6. Cờ Tính Năng & Kế Hoạch Rollback (Feature Flags & Rollback)

Hệ thống được bảo vệ bởi 3 cờ tính năng độc lập trong `backend/config.py`:
- `lesson_advisory_enabled` (Mặc định: `True`): Cho phép hiển thị bài học tham khảo (Xanh).
- `lesson_entry_rules_enabled` (Mặc định: `True`): Cho phép thực thi cảnh báo (Vàng) và chặn entry (Đỏ).
- `plan_adjustment_enabled` (Mặc định: `False`): Chế độ đề xuất điều chỉnh kế hoạch; khi tắt, hiển thị "Chỉ đề xuất", không tự ý thay đổi giá.
- `lesson_rules_shadow_mode` (Mặc định: `False`): Khi bật chế độ Shadow, engine ghi nhận `would_block: True` vào snapshot để kiểm chứng thực tế mà không chặn lệnh vào.

### Quy Trình Rollback Khẩn Cấp:
Nếu cần vô hiệu hóa toàn bộ tác động của lesson rules trong production:
1. Đặt biến môi trường `AURUM_LESSON_ENTRY_RULES_ENABLED=false`.
2. Khởi động lại ứng dụng.
3. Toàn bộ logic giao dịch lập tức quay về baseline SMC và risk guards chuẩn mà không làm mất bất kỳ dữ liệu bài học hay lệnh nào.
