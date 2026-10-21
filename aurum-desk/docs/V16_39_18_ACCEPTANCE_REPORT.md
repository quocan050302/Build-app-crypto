# BÁO CÁO NGHIỆM THU AURUM V16.39.18
**Khép Hợp Đồng R:R Settings → Current Backend → Toàn Bộ UI → Order Snapshot**

- **Dự án:** `aurum-desk` (`quocan050302/Build-app-crypto`)
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline Commit:** `04d107c54d40759adae5da9fcca050dca56ae96e` (fix(v16.39.17))
- **Thời điểm nghiệm thu:** 2026-10-21T23:25:00+07:00

---

## 1. Kết Quả Hai Probes Tái Hiện Lỗi (Fail-Before vs Pass-After)

### Probe 1: Mapped SelectedTradeIntent (Input A)
- **Input:**
  ```typescript
  const inputA = {
    source: 'OPEN_POSITION',
    id: 'order-a',
    order_id: 'order-a',
    setup_id: 'order-a',
    minNetRR: 1.5,
    rrPolicySource: 'ORDER_SNAPSHOT',
    rrPolicyFingerprint: 'arm-fp',
    estimatedNetRR: 1.7,
    orderType: 'LIMIT',
    leverage: 5,
    marginMode: 'ISOLATED'
  };
  const currentPolicy = { value: 2.5, source: 'SYSTEM_CONFIG', isConfirmed: true };
  ```
- **Hành vi trên Baseline (FAIL):**
  `resolveSelectionRrPolicy(inputA, currentPolicy)` gọi `resolveOrderRrView(inputA)`. Do `inputA` là mapped object (`minNetRR`), parser tìm trường `cost_snapshot` trong raw snake_case DTO không thấy, trả về:
  `{ kind: 'ORDER_SNAPSHOT', status: 'MISSING', minNetRR: null }`.
  Kết quả trên UI (`TradeDetailsDrawer`): hiển thị sai nhãn *"Chưa lưu ngưỡng lúc ARM"*.
- **Hành vi sau Patch V16.39.18 (PASS):**
  `resolveMappedOrderRrView` nhận diện trực tiếp projection `minNetRR: 1.5` trên `SelectedTradeIntent`, trả về:
  `{ kind: 'ORDER_SNAPSHOT', status: 'KNOWN', minNetRR: 1.5, source: 'ORDER_SNAPSHOT', fingerprint: 'arm-fp' }`.
  Kết quả trên UI (`TradeDetailsDrawer`): hiển thị chính xác *"1:1.50 (Lúc ARM)"* dưới ngưỡng current 2.50.

### Probe 2: So Sánh Sát Biên Không Làm Tròn (Input B)
- **Input:**
  `actualNetRR = 1.49996` và `requiredMinNetRR = 1.50`
- **Hành vi trên Baseline (FAIL):**
  `compareNetRr` tính `diff = Number((1.49996 - 1.5).toFixed(4))` $\rightarrow$ `-0.0000` $\rightarrow$ `-0`.
  Biểu thức `diff >= -1e-6` cho ra `true`, trả về `status: 'MET', meetsMin: true` (sai lệch so với tính toán khắt khe của calculator).
- **Hành vi sau Patch V16.39.18 (PASS):**
  Bỏ qua làm tròn `toFixed(4)` trước quyết định, dùng trực tiếp `meets = actual >= minRR` full-precision.
  `1.49996 >= 1.50` cho kết quả `false`, trả về `status: 'NOT_MET', meetsMin: false`.
  Đồng thời các biên chuẩn xác:
  - `1.499999` $\rightarrow$ `NOT_MET, meetsMin: false`
  - `1.500000` $\rightarrow$ `MET, meetsMin: true`
  - `1.500001` $\rightarrow$ `MET, meetsMin: true`

---

## 2. Đính Chính Về Bộ Tests V17 & Bổ Sung Tests UI Thực Tế

- **Đính chính:** 21 tests trong `v16_39_17_rr_sync.test.ts` ở phiên bản trước chủ yếu là helper/utility tests (tự dựng array blockers và gọi resolver). Chưa có bài kiểm tra render component thực tế hoặc test vòng đời của React Hook.
- **Bổ sung V16.39.18 (`v16_39_18_rr_contract.test.tsx`):**
  1. Render component thực tế bằng React 19 `createRoot` và `act`:
     - `TradeDetailsDrawer`: kiểm tra hiển thị thực tế trên DOM với Input A (`1:1.50 (Lúc ARM)`) và Missing snapshot (`Chưa lưu ngưỡng lúc ARM`).
     - `QuickDecisionSidebar`: kiểm tra chặn lệnh `POLICY_GATE` khi policy unconfirmed/loading; kiểm tra blocker `NET_RR_TOO_LOW` khi 1.70 < 2.50.
     - `CG01DiagnosticCard`: kiểm tra panel settings hiển thị `Đang tải...` khi unconfirmed và `1:1.50` khi confirmed.
  2. Test vòng đời React Hook `useRrPolicySettings`:
     - Trạng thái unconfirmed/loading ban đầu với `saved.value === null`.
     - Phản hồi GET server chuyển trạng thái thành `ready` và `isConfirmed: true`.
     - Gửi duy nhất body `{ min_net_rr: 1.5 }` khi save.
     - Bảo toàn bản thảo người dùng khi save thất bại hoặc khi có background refresh.
  3. Playwright E2E browser tests thực tế (`tests/e2e/v16_39_18_rr_contract.e2e.ts`):
     - Chạy qua server cô lập `e2e_server.py`.
     - Xác nhận luồng thay đổi R:R 2.00 $\rightarrow$ 1.50 và 2.50 hiển thị ngay trên UI browser.
     - Kiểm tra không tràn layout trên màn hình di động 390x844.

---

## 3. Nhật Ký Lệnh Kiểm Tra Thực Tế (Execution Evidence)

### 3.1. Frontend Unit & Integration Tests (Vitest)
- **Command:** `npm test`
- **Cwd:** `aurum-desk/frontend`
- **Exit Code:** `0`
- **Kết quả:**
  ```text
  Test Files  37 passed (37)
       Tests  411 passed (411)
    Duration  1.65s
  ```
  *(Bao gồm 36 files test cũ + 1 suite mới `v16_39_18_rr_contract.test.tsx` với 19 tests production component/hook)*.

### 3.2. Frontend Typecheck & Build
- **Command:** `npm run build` (`tsc -b && vite build`)
- **Cwd:** `aurum-desk/frontend`
- **Exit Code:** `0`
- **Kết quả:**
  ```text
  ✓ 2020 modules transformed.
  dist/index.html                   0.45 kB
  dist/assets/index-DA3DwUZa.css   46.23 kB
  dist/assets/index-t73FmbDj.js 1,089.79 kB
  ✓ built in 978ms
  ```

### 3.3. Frontend Linter
- **Command:** `npm run lint`
- **Cwd:** `aurum-desk/frontend`
- **Exit Code:** `0`
- **Kết quả:** `0 errors, 45 warnings` (toàn bộ 45 warnings là baseline pre-existing, không phát sinh lỗi mới).

### 3.4. Backend Python Test Suite (Pytest)
- **Command:**
  ```bash
  PYTHONPATH=backend:. backend/venv/bin/python -m pytest -q \
    backend/tests/test_v16_39*.py \
    backend/tests/test_v16_38_adaptive_paper_readiness.py \
    backend/tests/test_v16_36_paper_tonight_production_regression.py \
    backend/tests/test_v16_35_rr_propagation_telegram_proximity.py \
    backend/tests/test_v16_34_cg01_runtime_ownership_consistency.py \
    backend/tests/test_v14_4_1_production_pipeline.py \
    backend/tests/test_v14_4_2_entry_regression.py \
    backend/tests/test_v16_3_cg01_pipeline.py \
    backend/tests/test_v16_3_cg01_contracts.py \
    backend/tests/test_v15_0_telegram_and_api.py \
    backend/tests/test_migration_runtime_upgrade.py \
    backend/tests/test_setup_quality_api_v14.py \
    backend/tests/test_setup_quality_service_v14.py \
    backend/tests/test_v16_15_scheduler_lifespan.py
  ```
- **Cwd:** `aurum-desk`
- **Exit Code:** `0`
- **Kết quả:**
  ```text
  412 passed, 1 warning in 10.94s
  ```

### 3.5. Playwright E2E Browser Tests
- **Command:** `AURUM_E2E_PYTHON=../backend/venv/bin/python npx playwright test tests/e2e/v16_39_18_rr_contract.e2e.ts`
- **Cwd:** `aurum-desk/frontend`
- **Exit Code:** `0`
- **Kết quả:**
  ```text
  Running 3 tests using 1 worker
    ✓ E01: Min Net R:R default 2.00 renders in CG-01 settings panel and updates to 1.50 (1.6s)
    ✓ E02: Custom Min Net R:R 2.50 reflects strictly in CG-01 applied badge (875ms)
    ✓ E03: Mobile viewport (390x844) renders without horizontal overflow (764ms)
  3 passed (6.2s)
  ```

---

## 4. Bảo Toàn Phạm Vi & Không Can Thiệp Ngoài Hợp Đồng

1. **Chiến lược & Guards:** Giữ nguyên 100% logic phát hiện SMC, HTF Bias (D1/H4/H1), bộ lọc phiên (London, NY), giới hạn quota 3 lệnh/ngày, tin tức USD High Impact blackout, kiểm tra tài khoản và quy tắc bài học đã duyệt.
2. **Quản lý rủi ro:** Không thay đổi công thức tính sizing, đòn bẩy hoặc phí sàn/trượt giá.
3. **Lịch sử lệnh đóng băng:** Lệnh ARMED, OPEN, CLOSED giữ nguyên snapshot lịch sử. Không sửa database thực tế để đồng bộ lệnh cũ theo Settings hiện tại.
4. **Không phụ thuộc thư viện mới:** Giải quyết thuần túy qua TypeScript và React sẵn có.

---

## 5. Hướng Dẫn Người Dùng Ngắn Gọn (User Guide)

- **Vị trí cấu hình R:R:** Vào mục Cài đặt (Settings) hoặc bấm "Chỉnh trong Cài đặt" tại thẻ CG-01 Diagnostics.
- **Ý nghĩa chỉ số:** Đây là **Min Net R:R sau chi phí** (phí sàn + trượt giá), không phải tỷ lệ R:R giá thuần (gross R:R) và không phải xác suất thắng.
- **Cập nhật:** Khi lưu (Save) thành công (ví dụ `1.50` hoặc `2.50`), toàn bộ các tính năng hiện hành (Sidebar, Draft, Drawer, CG-01 Card, Upcoming, NY Session) đều đồng bộ cùng ngưỡng. Lệnh đã mở hoặc đã đóng trong quá khứ tiếp tục hiển thị snapshot lúc ARM.
