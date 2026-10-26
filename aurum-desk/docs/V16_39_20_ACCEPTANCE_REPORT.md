# AURUM V16.39.20 — ACCEPTANCE REPORT
**Báo Cáo Nghiệm Thu Khép Trạng Thái R:R Từ Settings Đến Drawer & Thay Đổi Điều Kiện ARM**

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline SHA:** `5f1880a9105fc88965140e2144c01a95e0bbb1e1`
- **Baseline Commit:** `fix(v16.39.19): preserve RR error states and verify settings save propagation`
- **Thời điểm nghiệm thu:** 22/10/2026

---

## 1. Tóm Tắt Kết Quả Nghiệm Thu (Executive Summary)

| Cổng Kiểm Tra (Gate) | Lệnh Thực Thi Thực Tế | Exit Code | Số Lượng / Chi Tiết | Trạng Thái | Ghi Chú & Bằng Chứng |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **FAILURE C: Settings Read Error** | `npx vitest run src/__tests__/v16_39_20_rr_ui_state.test.tsx -t "Failure C"` | 0 | 3/3 tests passed | **PASS** | Nhãn *"Cấu hình gần nhất: 1:1.50 — Chưa xác minh lại"*; nút disabled *"Chưa xác minh cấu hình"*; không còn *"Đã đồng bộ"* hay *"Đang áp dụng"*. |
| **FAILURE D: Drawer Context Propagation** | `npx vitest run src/__tests__/v16_39_20_rr_ui_state.test.tsx -t "Failure D"` | 0 | 5/5 tests passed | **PASS** | Drawer nhận nguyên `currentPolicyContext` ERROR -> hiển thị *"Chưa xác minh lại cấu hình R:R"*; order frozen KNOWN giữ nguyên *"1:2.00 (Lúc ARM)"*. |
| **Sync Semantics & Callback Bridge** | `npx vitest run src/__tests__/v16_39_20_rr_ui_state.test.tsx -t "Settings Card Edge Cases & Evaluation Sync"` | 0 | 4/4 tests passed | **PASS** | `onSaveSuccess` trả Promise qua ref; theo dõi PENDING -> READY / ERROR mà không rollback save đã commit. |
| **Frontend Unit & Component Suites** | `npm test` (trong `frontend`) | 0 | **39 files / 441 tests passed** | **PASS** | Toàn bộ regression pass (tăng từ 38 files/429 tests ở baseline; thêm suite V20 12 tests). Log có warning socket hang up/ECONNRESET của happy-dom như đã ghi nhận. |
| **Frontend TypeScript & Vite Build** | `npm run build` (`tsc -b && vite build`) | 0 | 2020 modules transformed | **PASS** | Build thành công trong 1.51s; có cảnh báo chunk vendor > 500 kB bình thường. |
| **Frontend Lint** | `npm run lint` (`oxlint`) | 0 | **0 errors, 48 warnings** trên 127 files | **PASS** | Đo đạc thực tế chính xác 48 warnings (không có lỗi cú pháp hay type lọt lưới). |
| **Backend Pytest Regression** | `& "backend\venv\Scripts\python.exe" -m pytest -q <regression_files>` | 0 | **412 tests passed**, 1 warning | **PASS** | Hoàn thành trong 34.87s; 1 warning `StarletteDeprecationWarning` httpx có từ baseline. |
| **Browser E2E Full Acceptance** | `npx playwright test tests/e2e/v16_39_20_rr_policy_acceptance.e2e.ts` | 0 | **2/2 tests passed** (11.9s) | **PASS** | Thực thi trên Google Chrome thật (port API 8899, Web 5299 cô lập). Window marker `__RR_V20_DOC__` chứng minh 0 reload xuyên suốt flow. |
| **Browser E2E V19 Regression** | `npx playwright test tests/e2e/v16_39_19_rr_no_reload.e2e.ts` | 0 | **2/2 tests passed** (10.7s) | **PASS** | Bảo toàn bằng chứng Save no-reload của V19. |

---

## 2. Chi Tiết Tái Hiện & Khắc Phục Hai Failures Production

### FAILURE C: Settings Hiển Thị Đã Áp Dụng Khi Read Error
- **Triệu chứng trước sửa:** Khi GET refresh hoặc revalidation gặp lỗi mạng/500, hook có `policyReadStatus: 'ERROR'`, `errorOrigin: 'READ'`, và `saved.value: 1.5`. Tuy nhiên `RrPolicySettingsCard` chỉ nhận props chung (`status: 'error'`, `saved`), dẫn đến việc header vẫn hiển thị `Đang áp dụng: 1:1.50 (SYSTEM_CONFIG)` và nút submit không dirty vẫn ghi `R:R Đã Đồng Bộ (1:1.50)`. Người dùng bị lừa rằng cấu hình vẫn đang hoạt động bình thường trong khi Sidebar đã chặn lệnh vì lỗi xác minh.
- **Khắc phục triệt để:**
  1. Tạo hàm chuẩn hóa hiển thị `deriveSettingsRrDisplay({ saved, policyReadStatus })`:
     - Nếu `policyReadStatus === 'ERROR'`: Chuyển sang mode `READ_ERROR`. Nếu có `saved.value` > 0 thì heading là *"Cấu hình gần nhất"* kèm nhãn *"Chưa xác minh lại"*; nếu không có value thì hiển thị *"Chưa xác minh cấu hình"*; `canClaimSynchronized = false`.
     - Nếu `policyReadStatus === 'READY'` và `saved.isConfirmed`: Mode `READY`, hiển thị *"Đang áp dụng: 1:x.xx"*; `canClaimSynchronized = true`.
  2. Mở rộng props của `RrPolicySettingsCard` tiếp nhận `policyReadStatus`, `errorOrigin`, `readError`, `saveError`, `evaluationSyncStatus`, `evaluationSyncError`.
  3. Cập nhật nút submit: Khi không dirty và `canClaimSynchronized === false`, nút bị disabled với nhãn *"Chưa xác minh cấu hình"*, tuyệt đối không ghi *"R:R Đã Đồng Bộ"*.
  4. Nút thử lại phân định rõ ràng: Nếu là lỗi đọc (`READ`), hiển thị *"Tải Lại Cấu Hình"* và gọi `onRetryRead`; nếu là lỗi đồng bộ đánh giá (`evaluationSyncStatus === 'ERROR'`), hiển thị *"Thử Lại Đồng Bộ"* và gọi `onRetrySync`.

### FAILURE D: App Làm Mất Context ERROR Trước Khi Đến Drawer
- **Triệu chứng trước sửa:** Tại `App.tsx`, `currentPolicyContext` đã có `{ status: 'ERROR', isConfirmed: false, value: 1.5 }` khi GET lỗi. Tuy nhiên khi render `TradeDetailsDrawer`, App chỉ truyền `appliedMinNetRR={currentEffectiveMinNetRR}` và `policy={tradingPolicy}`. Drawer gọi `resolveSelectionRrPolicy` với context tạo lại thiếu `status` và `isConfirmed`, khiến Drawer rơi vào fallback số cũ và hiển thị `1:1.50 (Hiện hành)`, mâu thuẫn trực tiếp với Sidebar đang báo `Lỗi cấu hình R:R`.
- **Khắc phục triệt để:**
  1. Mở rộng `TradeDetailsDrawerProps` tiếp nhận prop chính chủ `currentPolicyContext?: CurrentPolicyContext | null`.
  2. Tại `App.tsx` (dòng 1770), truyền nguyên vẹn `currentPolicyContext={currentPolicyContext}` xuống Drawer.
  3. Trong `TradeDetailsDrawer`, sử dụng `currentPolicyContext` trực tiếp cho `resolveSelectionRrPolicy(selectedIntent, currentPolicyContext)`.
  4. Tạo hàm format chuẩn `formatDrawerRrPolicyThreshold(view)`:
     - Khi `view.kind === 'CURRENT'` và `view.status === 'ERROR'`: Trả về *"Chưa xác minh lại cấu hình R:R"*, không bao giờ gắn nhãn *"(Hiện hành)"*.
     - Khi `view.kind === 'CURRENT'` và `view.status === 'READY'`: Trả về `1:x.xx (Hiện hành)`.
     - Khi `view.kind === 'ORDER_SNAPSHOT'` và `view.status === 'KNOWN'`: Trả về `1:x.xx (Lúc ARM)` (Bất chấp current context đang bị lỗi kết nối).

---

## 3. Tách Biệt Ba Tầng Lỗi Hệ Thống (Three-Tier Error Separation)

| Tầng Lỗi | Nguyên Nhân Gốc | Dữ Liệu Lưu Cũ (`saved`) | Dữ Liệu Nháp (`draftText`) | Nhãn Giao Diện | Thao Tác Thử Lại |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Read Error (GET Lỗi)** | GET `/api/scalp/config` thất bại hoặc revalidation không thành công | Giữ lại làm `lastKnown` tham khảo, không còn tính authoritative | Giữ nguyên | Card: *"Cấu hình gần nhất: 1:x.xx — Chưa xác minh lại"*<br/>Drawer: *"Chưa xác minh lại cấu hình R:R"*<br/>Sidebar: *"Lỗi cấu hình R:R"* | Bấm *"Tải Lại Cấu Hình"* (Chỉ phát GET kiểm tra lại) |
| **2. Save Mutation Error (POST Lỗi)** | POST `/api/scalp/config` thất bại (mạng rớt hoặc server 500) | Giữ nguyên giá trị confirmed trước đó (ví dụ 1.50) | Giữ dirty (ví dụ 2.50) không bị reset mất dữ liệu | Card: *"Đang áp dụng: 1:1.50"*, kèm cảnh báo *"Lưu cấu hình thất bại: ..."*<br/>Submit: *"Lưu R:R (1:2.50)"* | Bấm *"Lưu R:R"* để gửi lại mutation POST |
| **3. Dependent Sync Error (Post-Save Refresh Lỗi)** | POST lưu thành công, nhưng một trong các API phụ thuộc (`/upcoming`, `/analysis`, `/account`) bị lỗi | Cập nhật giá trị mới (ví dụ 2.50) thành công vào backend | Reset dirty | Card: *"Đã lưu — một phần đánh giá chưa cập nhật"*, kèm cảnh báo vàng<br/>Submit: Disabled tương ứng | Bấm *"Thử Lại Đồng Bộ"* (Chỉ gọi các GET phụ thuộc, KHÔNG POST lại cấu hình) |

---

## 4. Nghiệm Thu Trình Duyệt Thực Tế (Browser E2E Acceptance Evidence)

Bộ kiểm thử `frontend/tests/e2e/v16_39_20_rr_policy_acceptance.e2e.ts` đã chạy thành công trên Google Chrome thật kết nối test server cô lập (port 8899 / web 5299):

### Bằng chứng 1: Bằng chứng Không Reload (Single-Document Window Token)
- Trước khi thực hiện bất kỳ thao tác nào, một token ngẫu nhiên được gắn trực tiếp vào RAM của tab trình duyệt:
  ```javascript
  const runMarkerToken = `RR_V20_TOKEN_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  Reflect.set(window, '__RR_V20_DOC__', runMarkerToken);
  ```
- Sau khi Save 1.50 -> chuyển tab sang Upcoming -> mở Drawer -> chuyển tab sang Paper -> Save 2.50 -> chuyển tab sang Journal -> chọn Order frozen -> quay lại Current:
  Token `window.__RR_V20_DOC__` được đọc lại ở mỗi bước và khẳng định **100% khớp với `runMarkerToken` ban đầu**. Điều này chứng minh toàn bộ chu trình đồng bộ diễn ra hoàn toàn trong nội bộ SPA, không hề có page reload.

### Bằng chứng 2: Setup Actual ~1.75 Đổi Trạng Thái Guard Giữa Hai Ngưỡng
- Test server tạo fixture `e2e-mid-long` với:
  - Planned Entry: `2650.0`, StopLoss: `2640.0`, TakeProfit: `2668.0`.
  - Risk = 10.0, Reward = 18.0 -> Gross R:R = 1.80.
  - Phí sàn & trượt giá production (-0.05R) -> **Actual Net R:R = 1.75**.
- **Khi Save ngưỡng tối thiểu 1.50:**
  - $1.75 \ge 1.50$ -> R:R Guard: **MET** (Đạt chuẩn).
  - Drawer hiển thị: `1:1.50 (Hiện hành)`.
  - Hình học chart giữ nguyên: Entry 2650.0, SL 2640.0, TP 2668.0 (không bị co kéo).
- **Khi Save ngưỡng tối thiểu 2.50 trên cùng document:**
  - $1.75 < 2.50$ -> R:R Guard: **NOT_MET** (Không đạt chuẩn).
  - Nút ARM bị disabled và hiển thị lý do không đạt yêu cầu Net R:R tối thiểu 1:2.50.
  - Drawer cập nhật tức thì: `1:2.50 (Hiện hành)`.

### Bằng chứng 3: Bảo Toàn Snapshot Order Lịch Sử (Frozen Isolation)
- Test server nạp một lệnh lịch sử `ord-frozen-v20` đã đóng với `min_net_rr_at_arm = 2.00`.
- Sau khi đã Save ngưỡng Current toàn app lên 2.50, mở xem chi tiết lệnh `ord-frozen-v20` trong tab Journal:
  - Drawer hiển thị chính xác: `1:2.00 (Lúc ARM)`.
  - Tuyệt đối không bị ghi đè thành 2.50 (ngưỡng Current mới) hay 1.50.
- Chọn lại setup Current: Ngưỡng tối thiểu vẫn là 2.50, chứng minh không có hiện tượng rò rỉ (leakage) snapshot của order vào policy toàn app.

### Bằng chứng 4: Khả Năng Thao Tác Trên Giao Diện Di Động (390x844)
- Test `E02-MOBILE` thiết lập viewport 390x844:
  - Input và nút submit `Lưu R:R` hiển thị đầy đủ, có thể click bình thường mà không bị che khuất.
  - `document.documentElement.scrollWidth <= clientWidth + 2`: Đảm bảo không xảy ra hiện tượng tràn ngang (horizontal overflow).

---

## 5. Hướng Dẫn Vận Hành Ngắn Cho Người Dùng

1. **Ý nghĩa Min Net R:R:** Đây là ngưỡng **sàn tối thiểu sau phí** để setup được phép vào lệnh (ARM). Hệ thống **không** tự ý dịch chuyển TP/SL của bạn để ép về đúng tỷ lệ này. Ví dụ cài 1.50 thì lệnh đạt 1.80R hay 2.50R đều hợp lệ.
2. **Khi thấy "Cấu hình gần nhất: 1:x.xx — Chưa xác minh lại":** Điều này báo hiệu frontend mất kết nối tạm thời tới API backend. Bạn hãy bấm nút **Tải Lại Cấu Hình** màu đỏ để thử lại. Trong thời gian này, các lệnh mới sẽ bị chặn an toàn.
3. **Khi thấy "Đã lưu — một phần đánh giá chưa cập nhật":** Cấu hình mới của bạn **đã được lưu an toàn vào cơ sở dữ liệu**. Cảnh báo này chỉ xuất hiện khi danh sách setup dự kiến hoặc dữ liệu phân tích SMC đang mất thêm vài giây để tải lại. Bạn có thể bấm **Thử Lại Đồng Bộ** để cập nhật ngay.
4. **Lệnh cũ trong sổ lệnh:** Mọi lệnh đã kích hoạt (ARM/OPEN/CLOSED) đều giữ nguyên snapshot ngưỡng tại thời điểm kích hoạt (ví dụ `1:2.00 (Lúc ARM)`), không bao giờ bị thay đổi khi bạn chỉnh sửa cài đặt sau này.

---

## 6. Hạn Chế Môi Trường & Xác Nhận Bất Biến

- **Hạn chế đo lường:** Quá trình chạy Vitest trên happy-dom có xuất hiện cảnh báo `ECONNRESET / socket hang up` do các request Axios nền của hook realtime bị abort khi teardown window test; điều này không ảnh hưởng tới tính đúng đắn của logic (toàn bộ 441 unit tests đều pass).
- **Cảnh báo Lint:** `oxlint` ghi nhận chính xác 48 warnings từ baseline (chủ yếu liên quan đến React hooks ref/set-state-in-effect trong các component biểu đồ cũ), 0 errors.
- **Xác nhận bất biến tuyệt đối:** Không thay đổi các quy tắc SMC/ICT/CG01, daily bias, HTF/LTF, POI/FVG/OB, session quota phiên Á/Âu/Mỹ, news guard, loss cooldown, fee formula, accounting, Telegram outbox hay DB live. Không thực hiện lệnh live sàn Bitget.
