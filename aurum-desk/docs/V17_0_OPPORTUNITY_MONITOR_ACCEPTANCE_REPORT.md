# BÁO CÁO NGHIỆM THU CHỨC NĂNG AURUM V17.0
*(Theo Dõi Cơ Hội Paper và Lý Do Chưa Vào Lệnh — Minimalist UI, Real Data)*

---

## 1. Thông Tin Baseline & Git Metadata
- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline Commit (Prompt):** `b9fa526cbad11791373fc1a0bf3bba75e162ff0f`
- **Message Baseline:** `fix(v16.39.21): verify canonical RR fixtures and strict browser acceptance`
- **Phạm vi cam kết:** Bổ sung tính năng quan sát read-only cho Paper Trading. Không chạm vào chiến lược giao dịch, không thay đổi R:R Calculator hoặc settings minimum, không tạo workers/queues chạy nền mới.

---

## 2. Tóm Tắt Chức Năng Triển Khai
Chức năng Aurum V17.0 giải quyết dứt điểm nhu cầu của người dùng mới khi quan sát hệ thống tự động:
1. **Biết bot đang làm gì ngay lập tức:** Thẻ trạng thái CG-01 trên Sidebar hiển thị một câu mô tả xúc tích (ví dụ: *Đang theo dõi · Chưa có setup đủ điều kiện*, *Đang chờ · Chờ nến M1 xác nhận*, hoặc *Tạm chặn · Tin mạnh trong thời gian bảo vệ*).
2. **Không làm chật chội màn hình chính:** Thêm liên kết văn bản nhỏ **"Xem cơ hội"** trên thẻ `CG01DiagnosticCard`. Chart và Sidebar giữ nguyên chiều cao và khoảng trống giao dịch, không chèn thêm bảng biểu hay nhật ký nến phức tạp.
3. **Mở Drawer quan sát theo nhu cầu:** Khi bấm "Xem cơ hội", một Drawer duy nhất mở ra (chiều rộng 560–640px trên Desktop, toàn màn hình trên Mobile 390x844).
4. **Master / Detail tiện lợi:** Danh sách phân trang hiển thị các cơ hội theo mốc thời gian, chiều (LONG/SHORT), trạng thái và lý do ngắn gọn. Bấm vào một dòng sẽ mở chi tiết ngay trong Drawer (gồm kế hoạch giá dự kiến, tiến trình các mốc đã ghi nhận, kết quả lệnh nếu đã khớp, và nút xem trên biểu đồ).
5. **Xem trên biểu đồ an toàn:** Nút "Xem Vị Trí Trên Biểu Đồ" tự động đóng Drawer và chuyển quyền chọn (selected intent/setup) về Chart thông qua handler chuẩn của hệ thống, giúp người dùng quan sát trực quan ngay trên nến.
6. **Tải báo cáo tóm tắt (.md):** Người dùng có thể tải báo cáo Markdown về phiên giao dịch bất kỳ lúc nào để đánh giá lại, không tự động gửi tin nhắn rác ra Telegram.

---

## 3. Kiến Trúc Dữ Liệu & Tính Trung Thực (Data Contracts)
Tuân thủ nghiêm ngặt các nguyên tắc trung thực dữ liệu quy định trong V17.0:
- **Đặc thù bảng `opportunity_funnel_records`:** Bảng được lưu theo cơ chế UPSERT duy nhất theo cặp khóa `(opportunity_id, stage)`. Nếu cùng một giai đoạn được cập nhật nhiều lần, chỉ snapshot cuối cùng được lưu giữ. Do đó, hệ thống không tuyên bố đây là log append-only của từng tick, mà hiển thị chính xác là **"Các mốc đã ghi nhận"**.
- **Phân tách ba tầng thông tin:**
  1. *Trạng thái trực tiếp (Live Status):* Lấy từ `useCG01RealtimeState` đang có, không gọi thêm polling HTTP hay WebSocket thứ hai.
  2. *Quan sát cơ hội (Opportunity Observations):* Gồm các bản ghi funnel và trace telemetry đã persisted.
  3. *Kết quả thực thi (Order Outcomes):* Chỉ lấy từ `PaperOrder` và `PaperResearchOrderMilestone` đã cam kết (committed) và liên kết chính xác bằng `PaperResearchOrderLink`.
- **Hạch toán tài chính nhất quán:** Kế thừa toàn bộ logic tính toán tài chính từ `build_paper_runtime_snapshot` của `paper_runtime_snapshot_service.py`. Nếu 0 lệnh đóng, hiển thị rõ `N/A`, không tự động gán `0%` hay `0.00 USD`. Khi chưa có lệnh hòa vốn thực sự, không hiển thị màu xanh giả tạo.
- **Không tự động sinh lệnh hay bài học (Zero Side-Effects):** Toàn bộ API endpoint mới chạy ở chế độ Read-Only với `db.autoflush = False`. Kiểm thử bắt sự kiện SQLAlchemy khẳng định không có bất kỳ lệnh `INSERT`, `UPDATE`, `DELETE` hay `DDL` nào được phát ra khi truy vấn monitor.

---

## 4. Bảng Kết Quả Kiểm Thử Toàn Diện (Full Test Suite Matrix)

### A. Kiểm Thử Backend (Python / Pytest)
- **Môi trường:** Python 3.11.9, pytest 9.1.1, Windows
- **Lệnh chạy:**
  ```powershell
  $env:PYTHONPATH="backend;."; & "backend/venv/Scripts/python.exe" -m pytest backend/tests/test_v17_0_opportunity_monitor_read_model.py backend/tests/test_v17_0_opportunity_monitor_metrics.py backend/tests/test_v17_0_opportunity_monitor_api_readonly.py
  ```
  **Kết quả:** **22 passed, 1 warning in 0.48s**
- **Kiểm thử hồi quy R:R & Funnel:**
  ```powershell
  $env:PYTHONPATH="backend;."; & "backend/venv/Scripts/python.exe" -m pytest backend/tests/test_v16_39_15_rr_policy_unification.py backend/tests/test_v16_39_16_rr_settings_and_preview_gaps.py backend/tests/test_v16_39_17_rr_sync.py backend/tests/test_v16_39_9_paper_runtime_snapshot.py backend/tests/test_v16_39_10_snapshot_readonly.py backend/tests/test_v16_14_missed_opportunity_funnel.py
  ```
  **Kết quả:** **68 passed in 13.65s**
- **Tổng số test backend qua kiểm tra:** **439 passed** (tăng từ 417 baseline + 22 test mới).

### B. Kiểm Thử Đơn Vị & Thành Phần Frontend (Vitest / Happy-DOM)
- **Môi trường:** Vitest 5.0.3, Happy-DOM, React 18
- **Lệnh chạy:**
  ```bash
  npm test
  ```
  **Kết quả:** **466 passed across 43 test files in 4.92s** (tăng từ 447 baseline + 19 test mới: 10 tests `paperOpportunityText.test.ts`, 3 tests `usePaperOpportunityMonitor.test.tsx`, 6 tests `PaperOpportunityMonitorDrawer.test.tsx`).

### C. Kiểm Tra Chuẩn Mã (Lint) & Biên Dịch Sản Phẩm (Build)
- **Kiểm tra chuẩn mã:**
  ```bash
  npm run lint
  ```
  **Kết quả:** **0 errors**, 56 warnings (đều là warnings baseline có từ trước về ref và state-in-effect của các component cũ).
- **Biên dịch sản phẩm:**
  ```bash
  npm run build
  ```
  **Kết quả:** `tsc -b && vite build` hoàn thành với **Exit code 0**; tạo thành công bundle `dist/assets/index-0VcZiJAH.js` (1,127.72 kB) và `dist/assets/index-viqE4L7u.css` (46.93 kB).

### D. Kiểm Thử Trình Duyệt Thực Tế (Playwright E2E)
- **Môi trường:** Google Chrome channel, backend `e2e_server.py` với SQLite cô lập (`TESTING=1`, không kết nối mạng/Bitget thật), cổng độc lập API 8795, Web 5299.
- **Suite V17.0 Opportunity Monitor:**
  ```powershell
  $env:AURUM_E2E_API_PORT="8795"; $env:AURUM_E2E_WEB_PORT="5299"; npm run test:e2e -- tests/e2e/v17_0_opportunity_monitor.e2e.ts
  ```
  **Kết quả:** **2 passed in 13.1s**
  - *Gate 1-8 (Desktop 1512x857):* Collapsed layout -> Mở Drawer -> Xem Master list -> Lọc 7 ngày -> Xem Chi tiết & Timeline -> Trở lại danh sách -> Xem trên biểu đồ (Drawer đóng, Setup được focus) -> Tải báo cáo Markdown -> Nhấn phím Escape đóng Drawer -> Khẳng định 0 lệnh phát sinh, outbox = 0.
  - *Gate 9 (Mobile 390x844):* Mở Drawer toàn màn hình, nút đóng hiển thị rõ ràng, layout cuộn nội bộ không bị tràn ngang (horizontal overflow).
- **Suite Hồi Quy R:R V16.39.21 Final Acceptance:**
  ```powershell
  $env:AURUM_E2E_API_PORT="8795"; $env:AURUM_E2E_WEB_PORT="5299"; npm run test:e2e -- tests/e2e/v16_39_21_rr_final_acceptance.e2e.ts
  ```
  **Kết quả:** **3 passed in 1.1m**
  - `V16.39.21-ACCEPTANCE-LONG`: Đạt (27.4s)
  - `V16.39.21-ACCEPTANCE-SHORT`: Đạt (26.6s)
  - `V16.39.21-MOBILE`: Đạt (2.5s)

---

## 5. Danh Sách Tệp Thay Đổi & Tạo Mới

### Tệp Tạo Mới
1. `backend/services/paper_opportunity_monitor_service.py`: Service đọc dữ liệu, phân trang SQL, group canonical identities, build DTOs và render Markdown.
2. `backend/tests/test_v17_0_opportunity_monitor_read_model.py`: Test kiểm tra thuần túy logic gom nhóm, parse JSON, handling UPSERT.
3. `backend/tests/test_v17_0_opportunity_monitor_metrics.py`: Test tính toán số liệu thống kê funnel và độ trễ telemetry.
4. `backend/tests/test_v17_0_opportunity_monitor_api_readonly.py`: Test API endpoints với SQLAlchemy mutation listener đảm bảo zero side-effects.
5. `frontend/src/types/paperOpportunityMonitor.ts`: Định nghĩa TypeScript interfaces chuẩn cho monitor.
6. `frontend/src/utils/paperOpportunityText.ts`: Helper thuần túy chuyển đổi trạng thái bot sang ngôn ngữ thân thiện.
7. `frontend/src/hooks/usePaperOpportunityMonitor.ts`: React hook quản trị vòng đời fetch, abort controller và cadence 30s.
8. `frontend/src/components/OpportunityTimeline.tsx`: Component hiển thị dòng thời gian các mốc ghi nhận.
9. `frontend/src/components/PaperOpportunityMonitorDrawer.tsx`: Component Drawer quan sát tổng hợp Master/Detail.
10. `frontend/src/__tests__/paperOpportunityText.test.ts`: Unit test văn bản trạng thái.
11. `frontend/src/__tests__/usePaperOpportunityMonitor.test.tsx`: Unit test hook React.
12. `frontend/src/__tests__/PaperOpportunityMonitorDrawer.test.tsx`: Component test cho Drawer.
13. `frontend/tests/e2e/v17_0_opportunity_monitor.e2e.ts`: Playwright acceptance test cho V17.0.
14. `docs/V17_0_OPPORTUNITY_MONITOR_IMPLEMENTATION_MAP.md`: Bản đồ triển khai chi tiết.
15. `docs/V17_0_OPPORTUNITY_MONITOR_ACCEPTANCE_REPORT.md`: Báo cáo nghiệm thu hoàn chỉnh này.

### Tệp Cập Nhật (Additive, Không Gây Phá Vỡ)
1. `backend/main.py`: Đăng ký 4 endpoints GET monitor mới với `db.autoflush=False`.
2. `backend/tests/e2e_server.py`: Thêm endpoint kiểm thử `POST /__e2e/seed_monitor`.
3. `frontend/src/api/client.ts`: Thêm 4 hàm client typed gọi monitor endpoints.
4. `frontend/src/components/CG01DiagnosticCard.tsx`: Bổ sung callback `onOpenOpportunityMonitor` và nút "Xem cơ hội".
5. `frontend/src/components/QuickDecisionSidebar.tsx`: Chuyển tiếp callback `onOpenOpportunityMonitor`.
6. `frontend/src/App.tsx`: Quản lý state mở Drawer và mount component.

---

## 6. Hướng Dẫn Vận Hành Dành Cho Người Dùng Mới
- **Khi quan sát bình thường:** Người dùng chỉ cần nhìn thẻ CG-01 ở thanh Sidebar bên phải. Thẻ sẽ cho biết bot đang làm gì (đang theo dõi, đang chờ nến M1, bị chặn bởi tin tức, hay đã kích hoạt ARM).
- **Khi muốn biết lý do cụ thể hoặc xem lại phiên:** Bấm vào chữ **"Xem cơ hội"** màu xanh trên thẻ CG-01.
- **Trong Drawer:**
  - Phần trên là tóm tắt nhanh số lượng cơ hội phát hiện, số lần trigger, số lần ARM và khớp.
  - Danh sách phía dưới hiển thị từng cơ hội. Bấm vào một dòng để xem chi tiết vùng POI/FVG, mức giá dự kiến (Entry/SL/TP) và dòng thời gian các mốc.
  - Muốn xem vùng đó trên biểu đồ nến, bấm **"Xem Vị Trí Trên Biểu Đồ"**. Drawer sẽ tự động đóng lại và chart sẽ zoom vào vùng cơ hội tương ứng.
  - Cuối phiên giao dịch, bấm **"Tải Báo Cáo (.md)"** để lưu lại nhật ký phân tích dạng văn bản Markdown.
