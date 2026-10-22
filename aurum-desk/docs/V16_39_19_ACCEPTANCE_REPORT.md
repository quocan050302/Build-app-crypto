# AURUM V16.39.19 — ACCEPTANCE REPORT
**Báo Cáo Nghiệm Thu Khép Hai Nhánh Trạng Thái R:R & Save Settings Không Reload**

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline SHA:** `a4bd56965e3f1a5be4a9f4ac127d72b8f9616db2`
- **Ngày thực hiện:** 22/10/2026

---

## 1. Tóm Tắt Kết Quả Nghiệm Thu (Executive Summary)

| Cổng Kiểm Tra | Trạng Thái | Số Lượng / Chi Tiết | Lệnh Thực Thi |
| :--- | :--- | :--- | :--- |
| **Failure A (ERROR -> READY)** | **PASS** | Tái hiện & Sửa triệt để; ERROR ưu tiên trên value/isConfirmed | `npx vitest run src/__tests__/v16_39_19_rr_closure.test.tsx` |
| **Failure B (INVALID -> UNKNOWN)** | **PASS** | Tái hiện & Sửa triệt để; INVALID explicit đứng trước null check | `npx vitest run src/__tests__/v16_39_19_rr_closure.test.tsx` |
| **Browser No-Reload Acceptance** | **PASS** | 2/2 tests pass; Token window xác nhận 100% không reload | `npx playwright test tests/e2e/v16_39_19_rr_no_reload.e2e.ts` |
| **Frontend Unit & Component Suites** | **PASS** | **38 files / 429 tests pass** (0 failed) | `npm test` |
| **Frontend ESLint** | **PASS** | **0 errors** / 48 warnings | `npm run lint` |
| **Frontend Build (TypeScript + Vite)** | **PASS** | `tsc -b && vite build` thành công | `npm run build` |
| **Backend Pytest Regression** | **PASS** | **412 tests pass** (0 failed) trong 32.91s | `python -m pytest -q <regression_files>` |
| **Bảo toàn Chiến lược & Dữ liệu** | **PASS** | Giữ nguyên SMC engine, formulas, readonly orders, no Telegram | Review git diff & test guards |

---

## 2. Chi Tiết Tái Hiện & Khắc Phục Hai Failures Production

### FAILURE A: CURRENT ERROR Bị Nâng Thành READY
- **Dữ liệu tái hiện:**
  ```typescript
  const current = { value: 1.5, source: 'SYSTEM_CONFIG', isConfirmed: true, status: 'ERROR' };
  const view = resolveSelectionRrPolicy({ source: 'DRAFT' }, current);
  const result = compareNetRr(1.7, view);
  ```
- **Trước khi sửa (Baseline):** Resolver kiểm tra `blacklist` chỉ thấy `loading/LOADING`, bỏ qua `ERROR`, thấy `value: 1.5` và `isConfirmed: true` nên trả về `kind: 'CURRENT', status: 'READY', minNetRR: 1.5`. Comparator so sánh `1.7 >= 1.5` trả về `MET` sai nghiêm trọng.
- **Sau khi sửa (V16.39.19):**
  - Thêm `normalizeCurrentPolicyReadStatus` chuẩn hóa case.
  - Trong `resolveSelectionRrPolicy`, nếu `normStatus === 'ERROR'` thì explicit ERROR luôn thắng `value`/`isConfirmed`; trả về `kind: 'CURRENT', status: 'ERROR', isAuthoritative: false, minNetRR: null`.
  - Trong `compareNetRr`, phát hiện `policyView.kind === 'CURRENT' && policyView.status === 'ERROR'`, trả về `status: 'UNKNOWN', meetsMin: false, label: 'Lỗi cấu hình R:R'`.
  - `QuickDecisionSidebar` hiển thị blocker `POLICY_GATE` ("Lỗi cấu hình R:R") và disable nút ARM.
  - `CG01DiagnosticCard` hiển thị badge "Lỗi tải cấu hình".

### FAILURE B: SNAPSHOT INVALID Bị Gộp Với MISSING
- **Dữ liệu tái hiện:**
  ```typescript
  const intent = {
    source: 'OPEN_POSITION',
    order_id: 'bad-a',
    rrPolicyStatus: 'INVALID',
    minNetRR: null,
    rrPolicySource: 'ORDER_SNAPSHOT',
  };
  const view = resolveSelectionRrPolicy(intent, { value: 1.5, isConfirmed: true, status: 'READY' });
  const result = compareNetRr(1.7, view);
  ```
- **Trước khi sửa (Baseline):** Resolver trả về đúng `status: 'INVALID'`, nhưng trong `compareNetRr`, điều kiện `if (policyView.status === 'MISSING' || minRR === null)` đứng TRƯỚC `if (policyView.status === 'INVALID')`. Do `minNetRR === null`, comparator rơi vào nhánh MISSING và trả về `status: 'UNKNOWN'` với label "Chưa lưu ngưỡng lúc ARM", làm mất thông tin hỏng dữ liệu.
- **Sau khi sửa (V16.39.19):**
  - Trong `compareNetRr`, kiểm tra explicit `policyView.status === 'INVALID'` TRƯỚC nhánh null/MISSING.
  - Comparator trả về đúng `status: 'INVALID', meetsMin: false, label: 'Ngưỡng snapshot lúc ARM không hợp lệ'`.
  - `TradeDetailsDrawer` hiển thị rõ ràng "Ngưỡng snapshot lúc ARM không hợp lệ".

---

## 3. Tách Biệt Lỗi Đọc (Read Error) và Lỗi Lưu (Save Mutation Error)

1. **Lỗi Đọc (GET /api/scalp/config thất bại):**
   - Đặt `policyReadStatus: 'ERROR'`, `errorOrigin: 'READ'`, `readError: message`.
   - Giữ lại `saved.value` cũ làm `lastKnown` để người dùng tham khảo, nhưng `App.currentPolicyContext` gán `status: 'ERROR', isConfirmed: false` để ngăn chặn các quyết định lệnh mới.
2. **Lỗi Lưu (POST /api/scalp/config thất bại):**
   - Đặt `errorOrigin: 'SAVE'`, `saveError: message`.
   - `policyReadStatus` vẫn giữ nguyên trạng thái xác thực trước đó (ví dụ `READY`).
   - Cấu hình `saved` cũ (ví dụ 1.50) không bị xóa và tiếp tục có hiệu lực.
   - Bản nháp người dùng vừa nhập (`draftText`, ví dụ 2.50) được giữ dirty để người dùng có thể chỉnh sửa lại hoặc bấm Thử lại, không bị reset đột ngột.
3. **Lỗi Đồng Bộ Đánh Giá Phụ Thuộc (Post-save callback error):**
   - `onSaveSuccess` được bọc an toàn trong `Promise.resolve().catch(...)` trong `useRrPolicySettings.save()`. Lỗi refresh upcoming/analysis không làm ném exception, không làm rollback trạng thái save đã thành công của server.

---

## 4. Nghiệm Thu Browser E2E Không Reload (No-Reload Proof)

- **File test:** `frontend/tests/e2e/v16_39_19_rr_no_reload.e2e.ts`
- **Môi trường:** Isolated test backend `e2e_server.py` trên port 8790, Vite web server trên port 5199, Chromium browser.
- **Quy trình kiểm chứng:**
  1. Điều hướng duy nhất: `page.goto('/')` đúng 1 lần tại bước khởi tạo.
  2. Tạo marker token ngẫu nhiên: `Reflect.set(window, '__NO_RELOAD_TOKEN__', token)`. (Token nằm trên window object, không thể tồn tại nếu trang bị reload).
  3. Mở CG-01 settings: badge ban đầu hiển thị `1:2.00` (server default).
  4. Mở Settings Card: điền `#min-net-rr-input` = `1.50`, bấm nút submit `Lưu R:R`.
  5. Chờ và bắt phản hồi `POST /api/scalp/config` (status 200, `min_net_rr: 1.5`).
  6. Assert card hiển thị `1:1.50`, badge hiển thị `1:1.50`.
  7. **Đọc lại token trên window:** Vẫn nguyên vẹn `token` ban đầu -> **Chứng minh 0 reload**.
  8. Chuyển tab SPA sang Chart rồi quay lại Paper: token vẫn nguyên vẹn.
  9. Điền `2.50` và bấm Lưu R:R lần 2 (Dual-direction verification).
  10. Chờ và bắt phản hồi `POST /api/scalp/config` (status 200, `min_net_rr: 2.5`).
  11. Assert card hiển thị `1:2.50`, badge hiển thị `1:2.50`.
  12. **Đọc lại token trên window lần cuối:** Vẫn nguyên vẹn `token` ban đầu.
- **Kết quả:**
  ```text
  Running 2 tests using 1 worker
    ok 1 tests\e2e\v16_39_19_rr_no_reload.e2e.ts:29:3 › E01-ACCEPTANCE: UI Save updates CURRENT policy dual-direction (1.50 and 2.50) without page reload (3.7s)
    ok 2 tests\e2e\v16_39_19_rr_no_reload.e2e.ts:171:3 › E02-MOBILE: Settings Card and Form actions are accessible on 390x844 viewport (1.4s)
    2 passed (12.4s)
  ```

---

## 5. Kết Quả Chạy Kiểm Tra (Verification Log Summary)

### A. Frontend Vitest
- Lệnh: `npm test`
- Thư mục: `frontend/`
- Kết quả: **38 passed files, 429 passed tests, 0 failed** trong 3.76s. Exit code: 0.

### B. Frontend ESLint
- Lệnh: `npm run lint`
- Thư mục: `frontend/`
- Kết quả: **0 errors, 48 warnings** (tất cả là warnings có từ baseline trước đó). Exit code: 0.

### C. Frontend Build
- Lệnh: `npm run build` (`tsc -b && vite build`)
- Thư mục: `frontend/`
- Kết quả: **TypeScript check sạch 100%, Vite bundle hoàn tất trong 1.62s**. Exit code: 0.

### D. Playwright E2E Browser
- Lệnh: `$env:AURUM_E2E_API_PORT="8790"; npx playwright test tests/e2e/v16_39_19_rr_no_reload.e2e.ts`
- Kết quả: **2 passed (12.4s)**. Exit code: 0.
- Lệnh hồi quy V18: `$env:AURUM_E2E_API_PORT="8790"; npx playwright test tests/e2e/v16_39_18_rr_contract.e2e.ts`
- Kết quả: **3 passed (12.9s)**. Exit code: 0.

### E. Backend Pytest Regression
- Lệnh:
  ```powers
  $testFiles = @(
      (Get-ChildItem backend/tests/test_v16_39*.py | ForEach-Object { $_.FullName })
      "backend/tests/test_v16_38_adaptive_paper_readiness.py"
      "backend/tests/test_v16_36_paper_tonight_production_regression.py"
      "backend/tests/test_v16_35_rr_propagation_telegram_proximity.py"
      "backend/tests/test_v16_34_cg01_runtime_ownership_consistency.py"
      "backend/tests/test_v14_4_1_production_pipeline.py"
      "backend/tests/test_v14_4_2_entry_regression.py"
      "backend/tests/test_v16_3_cg01_pipeline.py"
      "backend/tests/test_v16_3_cg01_contracts.py"
      "backend/tests/test_v15_0_telegram_and_api.py"
      "backend/tests/test_migration_runtime_upgrade.py"
      "backend/tests/test_setup_quality_api_v14.py"
      "backend/tests/test_setup_quality_service_v14.py"
      "backend/tests/test_v16_15_scheduler_lifespan.py"
  )
  $env:PYTHONPATH="backend;."
  & "d:\build app-crypto\aurum-desk\backend\venv\Scripts\python.exe" -m pytest -q @testFiles
  ```
- Kết quả: **412 passed, 1 warning trong 32.91s**. Exit code: 0.

---

## 6. Checklist Bảo Toàn Hệ Thống

- [x] Không sửa đổi công thức SMC, POI, Sweep, FVG, session timing.
- [x] Không thay đổi công thức phí, trượt giá, margin requirements của Bitget Paper model.
- [x] Không làm tròn trước so sánh (giữ `actual >= minNetRR` full precision).
- [x] Không sửa đổi DB runtime hay vị thế live của người dùng.
- [x] Không nới lỏng guards: news blackout, daily fills cap (3), lesson blockers vẫn giữ nguyên.
- [x] Lệnh frozen snapshot (`ARMED`, `OPEN`, `CLOSED`) giữ nguyên ngưỡng snapshot lúc ARM; thay đổi Settings chỉ tác động lên CURRENT.
- [x] Không tự động kích hoạt Auto Paper, không gửi Telegram thực tế.
