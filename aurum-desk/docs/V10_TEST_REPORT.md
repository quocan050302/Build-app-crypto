# V10 Test Report: Governed Lesson Rules & Lifecycle Verification

## 1. Tóm Tắt Kết Quả Kiểm Thử (Executive Summary)

- **Tổng số bài kiểm thử Backend:** 191 / 191 **PASS (100%)**
- **Tổng số bài kiểm thử Frontend:** 43 / 43 **PASS (100%)**
- **TypeScript & Production Build:** **PASS (0 errors)**
- **Thời gian chạy kiểm thử:** ~8.57s (Backend) + ~0.75s (Frontend)
- **Trạng thái:** **READY FOR DEPLOYMENT**

---

## 2. Lệnh Kiểm Thử & Môi Trường (Commands & Environment)

```bash
# 1. Backend Unit & Integration Tests (191 tests)
PYTHONPATH=backend backend/venv/bin/pytest backend/tests

# 2. V10 Lesson Rules Dedicated Test Suite (17 scenarios)
PYTHONPATH=backend backend/venv/bin/pytest backend/tests/test_v10_lesson_rules.py -v

# 3. Frontend Vitest Suites (43 tests)
npm test -- --run (trong thư mục frontend/)

# 4. Frontend Typecheck & Production Build
npm run build (trong thư mục frontend/)
```

---

## 3. Ma Trận Kịch Bản Bắt Buộc (Scenarios Matrix L01 – L22)

| Mã | Tên kịch bản | Phạm vi kiểm tra | Trạng thái |
| :--- | :--- | :--- | :--- |
| **L01** | Green advisory toggle | Bật/tắt bài học Xanh không làm thay đổi quyết định vào lệnh baseline | **PASS** |
| **L02** | Yellow warning evaluation | Bài học Vàng hiển thị cảnh báo với metric thật, ghi trace, không pause Auto | **PASS** |
| **L03** | Red active rule block | Bài học Đỏ có cấu trúc hợp lệ (Approved + Enabled + Valid) chặn Arm/Fill | **PASS** |
| **L04** | Red inactive / invalid safe | Bài học Đỏ Pending, Disabled, Invalid hoặc sai Scope không bao giờ chặn | **PASS** |
| **L05** | Archive & Reject isolation | Archived/Rejected bị loại triệt để khỏi active rules; Pending filter chuẩn | **PASS** |
| **L06** | Baseline rule integrity | Quy tắc bài học không thể nới lỏng hay vô hiệu hóa baseline risk guards | **PASS** |
| **L07** | Prose action rule immunity | Sửa văn bản prose tự do không tác động tới execution engine | **PASS** |
| **L08** | Optimistic locking (409) | Chỉnh sửa rule với revision cũ trả về HTTP 409 Conflict, giữ nguyên draft | **PASS** |
| **L09** | READY state preservation | Setup giữ nguyên trạng thái SMC READY, chỉ can_arm = False khi bị chặn | **PASS** |
| **L10** | Before Fill recheck | BEFORE_ARM đạt nhưng BEFORE_FILL phát hiện spread tăng -> reject an toàn | **PASS** |
| **L11** | Open position immunity | Vị thế đang mở không bị sửa SL/TP hay can thiệp bởi bài học đỏ mới | **PASS** |
| **L12** | Fail-safe engine exception | Khi thiếu dữ liệu hoặc lỗi dịch vụ, baseline guards vẫn an toàn 100% | **PASS** |
| **L13** | Multiple conflicts resolution| Ràng buộc chặt hơn được ưu tiên; Xanh không bao giờ override Đỏ | **PASS** |
| **L14** | Scope segregation | Phân tách rành mạch Manual, Auto và NY_QUOTA_PAPER scope | **PASS** |
| **L15** | Idempotency & deduplication | Không phát sinh duplicate warning events hay outbox spam trên batch quote | **PASS** |
| **L16** | Trace snapshot immutability | Historical trades giữ nguyên snapshot cũ khi rule bị sửa hoặc archive | **PASS** |
| **L17** | Plan adjustment preview | Đề xuất plan chỉ hiển thị tính toán preview, không tự áp dụng | **PASS** |
| **L18** | Shadow mode validation | Shadow mode ghi nhận `would_block = True` vào trace mà không chặn fill | **PASS** |
| **L19** | Idempotent migration | Migration SQLite additive an toàn, bảo tồn toàn bộ dữ liệu lịch sử | **PASS** |
| **L20** | Replay time-travel safe | Không rò rỉ (leak) bài học tương lai vào các đợt đối soát quá khứ | **PASS** |
| **L21** | Psychology self-report | Ghi chú tâm lý chỉ mang tính tự báo cáo, không thành hard filter | **PASS** |
| **L22** | Atomic failure rollback | Rollback giao dịch khi reject fill, không tăng daily fill count | **PASS** |

---

## 4. Kiểm Thử Giao Diện & Trải Nghiệm Người Dùng (UI Verification)

1. **Bộ Nhớ Bài Học (JournalTab):**
   - Đã bổ sung tab **"Lưu Trữ" (ARCHIVED)** bên cạnh Chờ Duyệt, Đã Duyệt, Từ Chối.
   - Nhãn giải thích cập nhật chuẩn: *"Phân loại 3 màu: Xanh (Tham khảo), Vàng (Cảnh báo trước Entry), Đỏ (Chặn Entry có cấu trúc)."*
   - Huy hiệu 3 màu trực quan:
     - `XANH: CHỈ THAM KHẢO`
     - `VÀNG: ĐANG CẢNH BÁO` / `VÀNG: ĐÃ TẮT CẢNH BÁO`
     - `ĐỎ: ĐANG CHẶN ENTRY` / `ĐỎ: CHƯA THỂ ÁP DỤNG` (khi chưa cấu hình predicate) / `ĐỎ: ĐÃ TẮT CHẶN`
   - Nút bật/tắt tức thì (`● Đang Bật` / `○ Đã Tắt`) cho từng quy tắc đã duyệt.
   - Nút **"Cấu Hình Quy Tắc (V10)"** mở modal chuyên dụng:
     - Chọn mức độ (Xanh / Vàng / Đỏ).
     - Cấu hình điều kiện theo danh mục whitelist (`spread`, `net_rr`, `distance_to_entry_atr`...).
     - Nút **Kiểm Định Quy Tắc (Validate)** trả về báo cáo tính hợp lệ ngay lập tức.
     - Khóa lạc quan chống xung đột phiên bản (409 Conflict handling).
   - Chi tiết lệnh (Trade Drawer Tab A): Bổ sung khu vực hiển thị **"Bài Học & Quy Tắc Quản Trị Tại Thời Điểm Vào Lệnh (Snapshot Trace)"** bảo đảm tính bất biến lịch sử.

2. **Bảng Kế Hoạch Vào Lệnh (ExpectedEntryPanel):**
   - Tích hợp khu vực **"Quy Tắc & Bộ Nhớ Bài Học (V10)"**:
     - Hiển thị rõ số lượng bài học phù hợp.
     - Nếu có rule Đỏ chặn: Hiển thị hộp cảnh báo màu đỏ với thông báo rõ ràng *"Chưa thể đặt lệnh: Quy tắc #... đang hạn chế entry..."*.
     - Nếu có rule Vàng: Hiển thị cảnh báo số liệu thật (spread, R:R...) mà không khóa nút Arm.
     - Nếu có rule Xanh: Hiển thị tóm tắt kinh nghiệm tham khảo.
     - Nếu không có bài học phù hợp: Hiển thị thông báo trung tính *"Không có bài học phù hợp cho thiết lập này"*.
