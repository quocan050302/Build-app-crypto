# BÁO CÁO NGHIỆM THU AURUM V16.39.14
**Patch Nhỏ: Sửa Lỗi Build TypeScript TS2353 và Thống Nhất Type Định Danh Lệnh**

- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Baseline Git SHA**: `aa9a300f718e22fa9543f86cbb1f2c1142914a12` (commit `fix(v16.39.13): close score detail lifecycle and response identity gaps`)
- **Trạng thái**: **ĐẠT (PASSED)** — 100% Code thật, TypeScript compile chuẩn, Test thật, Caller thật, Browser E2E thật.

---

## 1. Tái Hiện Lỗi Baseline & Nguyên Nhân Gốc (Root Cause)

### 1.1 Hiện Tượng Lỗi Lúc Build Baseline (aa9a300)
Khi chạy lệnh `npm --prefix frontend run build` (`tsc -b && vite build`):
```text
src/App.tsx:383:13 - error TS2353: Object literal may only specify known properties, and 'id' does not exist in type 'SelectedTradeIntent | ((prevState: SelectedTradeIntent | null) => SelectedTradeIntent | null)'.

383             id: p.id,
                ~~

Found 1 error in src/App.tsx:383
```

### 1.2 Phân Tích Nguyên Nhân Gốc
1. **Lệch Pha Hợp Đồng Type**: Trong V16.39.13, khi sửa producer refresh vị thế đang chạy (`activePosition` trong `App.tsx`), `setSelectedIntent` đã truyền `id: p.id`, `order_id: p.id` và `setup_id: p.id`. Tuy nhiên, interface `SelectedTradeIntent` chỉ mới khai báo `setup_id?: string` mà chưa có `order_id` hay `id`. Do đó, trình biên dịch TypeScript `tsc -b` bắt lỗi `TS2353` (excess property check).
2. **Kẽ Hở Ép Kiểu Ở Resolver**: Tại `frontend/src/utils/candidateScore.ts`, hàm `getSelectedScoreTarget()` đọc hai trường này bằng `(selectedIntent as any).order_id || (selectedIntent as any).id`, trong khi type tham số đầu vào không khai báo chúng.
3. **Producer Chọn Lệnh Chưa Nhất Quán**: Handler chọn lệnh từ journal (`handleFocusTradeOnChart`) mới chỉ truyền `setup_id: trade.id` mà chưa truyền `order_id` và `id` một cách tường minh.

---

## 2. Các Thay Đổi Triển Khai (Minimal & Type-Safe)

| File | Thành Phần Sửa | Chi Tiết Kỹ Thuật |
|---|---|---|
| `frontend/src/App.tsx` | `SelectedTradeIntent` | Bổ sung `order_id?: string` (định danh PaperOrder canonical) và `id?: string` (alias tương thích legacy) với chú thích rõ ràng. Không dùng `[key: string]: any` hay `@ts-ignore`. |
| `frontend/src/App.tsx` | `handleFocusTradeOnChart` | Bổ sung `order_id: trade.id` và `id: trade.id` bên cạnh `setup_id: trade.id` để đồng nhất 100% cả hai luồng tạo `OPEN_POSITION` (vị thế đang mở và lệnh nhật ký). |
| `frontend/src/utils/candidateScore.ts` | `getSelectedScoreTarget()` | Cập nhật type tham số đầu vào khai báo `id?: string; order_id?: string;`. Đọc trực tiếp `selectedIntent.order_id \|\| selectedIntent.id \|\| selectedIntent.setup_id`, loại bỏ hoàn toàn `as any`. |
| `frontend/src/candidateScore.test.ts` | Unit Tests | Bổ sung 7 test cases kiểm tra độ ưu tiên định danh (`order_id` > `id` > `setup_id`), bảo đảm tương thích với fixture dùng `SelectedTradeIntent`, ưu tiên `binding.kind === 'ORDER'`, và xác nhận `WATCH_SETUP` không bị nhận nhầm thành `ORDER`. |
| `docs/V16_39_13_ACCEPTANCE_REPORT.md` | Báo cáo cũ | Bổ sung mục đính chính nghiệm thu (Erratum) ghi nhận lỗi TS2353 tại baseline và trỏ tới bản vá V16.39.14. |

---

## 3. Bảng Khảo Sát Callers & Hợp Đồng Dữ Liệu

| Caller / Producer | Thuộc Tính Truyền | Output Của `getSelectedScoreTarget` | Hành Vi Bảo Toàn |
|---|---|---|---|
| `App.tsx` (refresh `activePosition`) | `source: 'OPEN_POSITION'`, `order_id: p.id`, `id: p.id`, `setup_id: p.id` | `{ kind: 'ORDER', id: p.id }` | Không gọi endpoint bằng setup ID; chọn đúng PaperOrder. |
| `App.tsx` (`handleFocusTradeOnChart`) | `source: 'OPEN_POSITION'`, `order_id: trade.id`, `id: trade.id`, `setup_id: trade.id` | `{ kind: 'ORDER', id: trade.id }` | Nhất quán với luồng position; giữ alias cho consumers legacy. |
| Chart Binding Context | `binding: { kind: 'ORDER', id: 'ord-bound' }` | `{ kind: 'ORDER', id: 'ord-bound' }` | Binding cụ thể trên chart luôn có độ ưu tiên cao nhất. |
| Setup Watchboard (`WATCH_SETUP`) | `source: 'WATCH_SETUP'`, `setup_id: s.id` | `{ kind: 'SETUP', id: s.id }` | Không gán `order_id`; không gọi `/orders/.../quality-score`. |
| Scenario / Draft | `source: 'SCENARIO'` hoặc `'DRAFT'` | `null` | Không gán order ID giả định; giữ nguyên logic hiện có. |

---

## 4. Kết Quả Kiểm Thử Toàn Diện Sau Patch

### 4.1 Production Build (TypeScript & Vite)
- **Lệnh thực thi**: `npm --prefix frontend run build`
- **Kết quả**: **Thành công 100% (0 errors, Exit Code: 0)**.
  - `tsc -b`: Không còn lỗi `TS2353`.
  - `vite build`: Hoàn thành đóng gói bundle production trong 920ms (`dist/assets/index-BjFbhba0.js`).

### 4.2 Frontend Unit & Integration Tests (Vitest)
- **Lệnh thực thi**: `npm --prefix frontend test`
- **Kết quả**: **352 passed trong 33 test files** trong 1.24s (Exit Code: 0).
  - Tăng từ 345 lên 352 tests do bổ sung 7 tests mới cho `getSelectedScoreTarget` trong [candidateScore.test.ts](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/candidateScore.test.ts).
  - Tất cả các tests hồi quy trước đó (`useCandidateScoreDetail.test.ts`: 21/21, `candidateScoreResponse.test.ts`: 13/13, `callerAcceptance.test.ts`: 7/7) tiếp tục **PASS** tuyệt đối.

### 4.3 Frontend Lint
- **Lệnh thực thi**: `npm --prefix frontend run lint`
- **Kết quả**: **0 errors**, 39 warnings (cảnh báo cấu hình baseline, không phát sinh warning mới nào).

### 4.4 Backend Extended Regression Suite (Pytest)
- **Lệnh thực thi**:
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
- **Kết quả**: **362 passed, 1 warning trong 9.60s** (Exit Code: 0).

### 4.5 Playwright Browser E2E Tests (Google Chrome Thực Tế)
- **Lệnh thực thi**: `AURUM_E2E_REPORT=../logs/acceptance/v16_39_14/playwright_report.json npm --prefix frontend run test:e2e -- tests/e2e/v16_39_11_candidate_score.e2e.ts`
- **Kết quả**: **3 passed trong 8.0s** (Exit Code: 0).
  - Báo cáo chi tiết: `logs/acceptance/v16_39_14/playwright_report.json`.

---

## 5. Hướng Dẫn Kỹ Thuật Cho Lập Trình Viên & Người Dùng

1. **Bản vá hoàn toàn ở tầng Frontend Type Contract**:
   - Không có thay đổi database schema, không cần chạy migration dữ liệu.
   - Không can thiệp vào thuật toán chấm điểm, ngưỡng ARM, sizing, quy tắc bảo vệ (guards) hay outbox Telegram.
2. **Nguyên tắc bảo toàn định danh**:
   - `order_id` là mã số PaperOrder thật; `setup_id` trong ngữ cảnh `OPEN_POSITION` được giữ làm alias tương thích cho các component legacy cần tham chiếu.
   - Khi chọn lệnh, drawer và hook luôn gửi request theo đúng mã PaperOrder (`p.id` hoặc `trade.id`), không bao giờ gửi nhầm ID của setup gốc.
