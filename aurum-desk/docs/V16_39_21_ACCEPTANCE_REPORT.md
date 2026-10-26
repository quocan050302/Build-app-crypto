# AURUM V16.39.21 — ACCEPTANCE REPORT
**Báo Cáo Nghiệm Thu Khép Nghiệm Thu R:R: Fixture Canonical, Browser Assertions Bắt Buộc, Báo Cáo Có Bằng Chứng**

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline SHA:** `e3817e1488aafcf6574e791d556e04a562691134`
- **Baseline Commit Message:** `fix(v16.39.20): propagate RR verification state to settings and drawer`
- **Thời điểm nghiệm thu:** 22/10/2026

---

## 1. Tóm Tắt Kết Quả Nghiệm Thu (Executive Gate Summary)

| Cổng Kiểm Tra (Gate) | Lệnh Thực Thi Thực Tế | Exit Code | Số Lượng / Chi Tiết | Trạng Thái | Bằng Chứng & Kết Quả |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Fixture Canonical Preflight** | `& "backend\venv\Scripts\python.exe" -m pytest -q backend/tests/test_v16_39_21_e2e_financial_fixtures.py` | 0 | **5/5 tests passed** (0.24s) | **PASS** | `MID_LONG` (2674) đạt 1.7677R; `MID_SHORT` (2626) đạt 1.7681R; metamorphic invariance; từ chối geometry sai; chứng minh baseline 2668 chỉ đạt 1.28R. |
| **2. UI Observability & Invariance Unit** | `npx vitest run src/__tests__/v16_39_21_rr_observability.test.tsx` | 0 | **6/6 tests passed** (0.22s) | **PASS** | Phản ánh `data-testid="sidebar-net-rr-row"` MET (ở 1.50) và NOT_MET (ở 2.50); bảo toàn giá geometry; Drawer threshold 1.50, 2.50 và 2.00 (Lúc ARM). |
| **3. Browser Final Acceptance (Strict E2E)** | `npx playwright test tests/e2e/v16_39_21_rr_final_acceptance.e2e.ts` | 0 | **3/3 tests passed** (1.0m) | **PASS** | Chạy trên Google Chrome thật (API port 8899, Web port 5299 cô lập). 100% assertions bắt buộc, không có optional branch hay silent catch. |
| **4. Browser V20 Suite Regression** | `npx playwright test tests/e2e/v16_39_20_rr_policy_acceptance.e2e.ts` | 0 | **2/2 tests passed** (18.8s) | **PASS** | Đã nâng cấp các locator thành bắt buộc; kiểm chứng Save không reload và snapshot isolation. |
| **5. Browser V19 Suite Regression** | `npx playwright test tests/e2e/v16_39_19_rr_no_reload.e2e.ts` | 0 | **2/2 tests passed** (11.3s) | **PASS** | Kiểm chứng Save qua UI và window token không reload của V19. |
| **6. Frontend Unit Regression** | `npm test` (trong `frontend`) | 0 | **40 files / 447 tests passed** | **PASS** | Toàn bộ 40 files unit test pass 100% (tăng từ 39 files / 441 tests ở baseline; thêm suite V21 với 6 tests mới). |
| **7. Frontend Linting** | `npm run lint` (`oxlint`) | 0 | **0 errors, 49 warnings** trên 129 files | **PASS** | 0 errors cú pháp hoặc type; 49 warnings (tương đương baseline 48 warnings). |
| **8. Frontend TypeScript & Vite Build** | `npm run build` (`tsc -b && vite build`) | 0 | 2020 modules transformed | **PASS** | TypeScript compile sạch, bundle Vite production thành công trong 1.30s (warning chunk vendor > 500 kB đã biết). |
| **9. Backend Pytest Regression** | `& "backend\venv\Scripts\python.exe" -m pytest -q $files` | 0 | **417 passed**, 1 warning (36.90s) | **PASS** | Toàn bộ regression backend pass (tăng từ 412 tests ở baseline; thêm 5 tests fixture V21). 1 warning Starlette/httpx baseline. |
| **10. State & Mutation Isolation** | Query `GET /__e2e/state` cuối run | 0 | `orders: 1`, `outbox: 0` | **PASS** | Tuyệt đối không có lệnh trade phát sinh ngoài ý muốn và không có tin nhắn Telegram nào lọt ra outbox. |

---

## 2. Bảng Đối Chiếu Cam Kết vs Bằng Chứng Thực Tế (Claims vs Evidence Matrix)

| Cam kết trong yêu cầu | Assertion kiểm chứng | Dữ liệu / Vị trí quan sát thực tế | Trạng thái |
| :--- | :--- | :--- | :--- |
| **Bỏ công thức giả trong fixture Net R:R** | `test_fake_formula_regression_vs_canonical_reality` | `backend/tests/e2e_server.py::_upsert_setup` gọi `_calculate_e2e_plan_financials`, lưu đúng `CalculationResult.net_rr` từ production calculator thật. | **PASS** |
| **Fixture LONG mới đạt $1.50 < \text{Net RR} < 2.50$** | `assert 1.5 < low.net_rr < 2.5`<br>`assert low.meets_min_rr is True` | `PLANS['MID_LONG']` (Entry 2650, SL 2640, TP 2674) cho actual Net R:R = **1.7677**. | **PASS** |
| **Fixture SHORT mới đạt $1.50 < \text{Net RR} < 2.50$** | `assert 1.5 < low.net_rr < 2.5`<br>`assert low.meets_min_rr is True` | `PLANS['MID_SHORT']` (Entry 2650, SL 2660, TP 2626) cho actual Net R:R = **1.7681**. | **PASS** |
| **Loại bỏ toàn bộ `catch(() => false)` trong E2E** | `await expect(card).toBeVisible()`<br>`await card.getByTestId('focus-setup-chart').click()` | Tệp `v16_39_21_rr_final_acceptance.e2e.ts` và `v16_39_20_rr_policy_acceptance.e2e.ts` không còn bất kỳ khối `if (isVisible().catch(...))` nào. Selector sai sẽ fail ngay lập tức. | **PASS** |
| **Bằng chứng Không Reload (Single Document)** | `assertSameDocument(page, runMarkerToken)` | Đặt `window.__RR_FINAL_DOC__` ngẫu nhiên. Kiểm tra sau Save 1.50, sau chọn setup, sau mở Drawer, sau Save 2.50, sau xem order frozen, sau quay lại Current. Marker 100% nguyên vẹn. | **PASS** |
| **R:R MET tại ngưỡng tối thiểu 1.50** | `expect(netRrRow).toHaveAttribute('data-rr-status', 'MET')`<br>`expect(netRrRow).toHaveAttribute('data-rr-required', '1.5')` | QuickDecisionSidebar hàng Net R:R hiển thị `data-rr-status="MET"` khi áp dụng ngưỡng 1.50 và actual Net R:R là 1.77. | **PASS** |
| **Drawer hiển thị `1:1.50 (Hiện hành)`** | `expect(thresholdVal1).toContainText('1:1.50 (Hiện hành)')` | TradeDetailsDrawer mở qua nút Sidebar thật `[data-testid="sidebar-open-trade-details"]`, tab Rủi Ro hiển thị chính xác nhãn Current 1.50. | **PASS** |
| **Bảo toàn hình học (Geometry Invariance)** | `expect(entryEl).toContainText(...)`<br>`expect(actualNetRrAfterSave).toBeCloseTo(actualNetRr, 2)` | Trước và sau khi Save 2.50: Giá Entry ($2650), SL ($2640 hoặc $2660), TP ($2674 hoặc $2626) và actual Net R:R (1.77) hoàn toàn không thay đổi. | **PASS** |
| **R:R NOT_MET & ARM CTA Blocked tại 2.50** | `expect(netRrRow).toHaveAttribute('data-rr-status', 'NOT_MET')`<br>`expect(ctaBtn).toHaveAttribute('data-can-submit', 'false')` | Khi áp dụng ngưỡng 2.50: QuickDecisionSidebar hiển thị `data-rr-status="NOT_MET"`, nút submit CTA bị khóa với lý do Net R:R 1.77 chưa đạt 2.50. | **PASS** |
| **Drawer cập nhật `1:2.50 (Hiện hành)`** | `expect(thresholdVal2).toContainText('1:2.50 (Hiện hành)')` | TradeDetailsDrawer cập nhật tức thì trên cùng document hiển thị `1:2.50 (Hiện hành)`. | **PASS** |
| **Cô lập Snapshot Order Lịch Sử (`ord-frozen-v20`)** | `expect(thresholdFrozen).toContainText('1:2.00 (Lúc ARM)')`<br>`expect(thresholdFrozen).not.toContainText('2.50')` | Focus lệnh đã đóng từ tab Journal qua nút `[data-testid="journal-focus-chart"][data-order-id="ord-frozen-v20"]`: Drawer hiển thị độc lập ngưỡng snapshot lúc ARM là 2.00, không bị ngưỡng Current 2.50 ghi đè. | **PASS** |
| **Quay lại setup CURRENT sau khi xem frozen** | `expect(thresholdReturn).toContainText('1:2.50 (Hiện hành)')` | Chọn lại setup Current trên chart: Drawer hiển thị chuẩn xác ngưỡng Current 2.50, chứng minh không có hiện tượng rò rỉ snapshot vào trạng thái Current. | **PASS** |
| **Mobile Form Interaction (390x844)** | `saveMinimumThroughUi` / form submit + `scrollWidth <= clientWidth + 2` | Điền 1.80, submit form thật, bắt response POST HTTP 200, xác nhận cập nhật nhãn `1:1.80` và không có thanh cuộn ngang. | **PASS** |

---

## 3. Bảng Dữ Liệu Hình Học & Đánh Giá Giữa Hai Lần Save

### Setup LONG (`e2e-mid-long`)
- **Trước khi Save (ở ngưỡng 1.50):**
  - Planned Entry: `$2650` (2,650.00 USDT)
  - Stop Loss: `$2640` (2,640.00 USDT)
  - Take Profit: `$2674` (2,674.00 USDT)
  - Direction: `LONG`
  - Actual Net R:R: `1.77` (1.7677)
  - Required Min Net R:R: `1.5`
  - Status: `MET`
  - Drawer Threshold: `1:1.50 (Hiện hành)`
  - CTA Button: Đạt điều kiện R:R (Nếu các guards khác thỏa mãn thì cho phép submit)
- **Sau khi Save trên cùng document (ở ngưỡng 2.50):**
  - Planned Entry: `$2650` (Giữ nguyên tuyệt đối)
  - Stop Loss: `$2640` (Giữ nguyên tuyệt đối)
  - Take Profit: `$2674` (Giữ nguyên tuyệt đối)
  - Direction: `LONG` (Không đổi)
  - Actual Net R:R: `1.77` (Giữ nguyên tuyệt đối)
  - Required Min Net R:R: `2.5` (Cập nhật tức thì)
  - Status: `NOT_MET` (Cập nhật tức thì)
  - Drawer Threshold: `1:2.50 (Hiện hành)` (Cập nhật tức thì)
  - CTA Button: `data-can-submit="false"`, Aria-label chứa blocker `2.5`

### Setup SHORT (`e2e-mid-short`)
- **Trước khi Save (ở ngưỡng 1.50):**
  - Planned Entry: `$2650` (2,650.00 USDT)
  - Stop Loss: `$2660` (2,660.00 USDT)
  - Take Profit: `$2626` (2,626.00 USDT)
  - Direction: `SHORT`
  - Actual Net R:R: `1.77` (1.7681)
  - Required Min Net R:R: `1.5`
  - Status: `MET`
  - Drawer Threshold: `1:1.50 (Hiện hành)`
- **Sau khi Save trên cùng document (ở ngưỡng 2.50):**
  - Planned Entry: `$2650` (Giữ nguyên tuyệt đối)
  - Stop Loss: `$2660` (Giữ nguyên tuyệt đối)
  - Take Profit: `$2626` (Giữ nguyên tuyệt đối)
  - Direction: `SHORT` (Không đổi)
  - Actual Net R:R: `1.77` (Giữ nguyên tuyệt đối)
  - Required Min Net R:R: `2.5` (Cập nhật tức thì)
  - Status: `NOT_MET` (Cập nhật tức thì)
  - Drawer Threshold: `1:2.50 (Hiện hành)` (Cập nhật tức thì)
  - CTA Button: `data-can-submit="false"`

---

## 4. Chi Tiết Lệnh Thực Thi & Logs

```bash
# 1. Unit & Preflight Fixtures:
python -m pytest -q backend/tests/test_v16_39_21_e2e_financial_fixtures.py
# Kết quả: 5 passed in 0.24s

# 2. Frontend Observability Unit:
npm test -- src/__tests__/v16_39_21_rr_observability.test.tsx
# Kết quả: 6 passed (6) in 218ms

# 3. Final Playwright Browser Acceptance:
$env:AURUM_E2E_API_PORT="8899"; $env:AURUM_E2E_WEB_PORT="5299"; $env:AURUM_E2E_PYTHON="D:\build app-crypto\aurum-desk\backend\venv\Scripts\python.exe"; npx playwright test tests/e2e/v16_39_21_rr_final_acceptance.e2e.ts
# Kết quả: 3 passed (1.0m) [LONG, SHORT, MOBILE]

# 4. Regression Browser V20:
$env:AURUM_E2E_API_PORT="8899"; $env:AURUM_E2E_WEB_PORT="5299"; $env:AURUM_E2E_PYTHON="D:\build app-crypto\aurum-desk\backend\venv\Scripts\python.exe"; npx playwright test tests/e2e/v16_39_20_rr_policy_acceptance.e2e.ts
# Kết quả: 2 passed (18.8s)

# 5. Regression Browser V19:
$env:AURUM_E2E_API_PORT="8899"; $env:AURUM_E2E_WEB_PORT="5299"; $env:AURUM_E2E_PYTHON="D:\build app-crypto\aurum-desk\backend\venv\Scripts\python.exe"; npx playwright test tests/e2e/v16_39_19_rr_no_reload.e2e.ts
# Kết quả: 2 passed (11.3s)

# 6. Toàn bộ Unit Frontend:
npm test
# Kết quả: 40 passed (40) files, 447 passed (447) tests in 3.48s

# 7. Linting:
npm run lint
# Kết quả: 0 errors, 49 warnings (baseline)

# 8. Build:
npm run build
# Kết quả: Exit code 0, 2020 modules transformed, dist built in 1.30s

# 9. Toàn bộ Pytest Backend Regression:
pytest -q <417 tests>
# Kết quả: 417 passed, 1 warning in 36.90s
```

---

## 5. Kết Luận Khép Nghiệm Thu

Phiên bản **Aurum V16.39.21** đã giải quyết triệt để 3 điểm tồn đọng cuối cùng của nghiệm thu R:R:
1. Đã thay thế công thức giả `gross - 0.05` trong test fixture bằng `domain_calculator.calculate_risk_reward` canonical.
2. Đã loại bỏ 100% các câu lệnh rẽ nhánh bỏ qua kiểm tra (`if (isVisible().catch(() => false))`) trong toàn bộ Playwright specs. Mọi thao tác chọn setup, mở drawer, focus chart, và submit form đều có locator thật và assertions bắt buộc.
3. Đã bổ sung đầy đủ bằng chứng kiểm thử MET, NOT_MET, chặn nút ARM theo minimum 2.50, bảo toàn tuyệt đối hình học giá (entry/SL/TP), cô lập ngưỡng snapshot của lệnh lịch sử (2.00 Lúc ARM), và khôi phục ngưỡng Current 2.50 khi quay lại setup hiện hành.

Tất cả các gates nghiệm thu đã **ĐẠT (PASS)** toàn phần. Sẵn sàng commit, push và khép lại phạm vi R:R để chuyển sang tính năng tiếp theo.
