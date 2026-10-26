# AURUM V16.39.19 — IMPLEMENTATION MAP
**Sửa Hai Nhánh Trạng Thái R:R & Nghiệm Thu Save Settings Không Reload**
*Mã phiên bản:* V16.39.19  
*Branch:* `fix/v14-1-rr-flow`  
*Baseline SHA:* `a4bd56965e3f1a5be4a9f4ac127d72b8f9616db2` (Commit: `fix(v16.39.18): repair RR view contracts precision and verified policy propagation`)  
*HEAD hiện tại:* Bao gồm commit `9dbfe274891483b558c55c99885082fc043184ab` sửa schema `resolved_through: Optional[Union[int, str]] = None`.

---

## 1. Bản Đồ Hàm & Contracts Đã Sửa

| Tệp / Vị trí | Hàm / Biểu tượng | Bản chất | Input Contract Shape | Output / Hành vi chuẩn hóa |
| :--- | :--- | :--- | :--- | :--- |
| `frontend/src/utils/rrPolicyView.ts` | `normalizeCurrentPolicyReadStatus` | **MỚI** | `status?: string \| null` | Chuẩn hóa case insensitive về `'READY' \| 'LOADING' \| 'ERROR' \| 'UNCONFIRMED'`. Trả về `'UNCONFIRMED'` cho null/undefined/giá trị không nhận diện. |
| `frontend/src/utils/rrPolicyView.ts` | `resolveSelectionRrPolicy` | **SỬA** | `(intent: any, currentPolicy?: CurrentPolicyContext)` | **Xử lý Failure A**: Frozen order snapshot có ưu tiên tối cao. Nếu đánh giá current context, trạng thái `ERROR` được ưu tiên tuyệt đối trên giá trị finite hoặc `isConfirmed: true` cũ; trả về `kind: 'CURRENT', status: 'ERROR', isAuthoritative: false, minNetRR: null`. |
| `frontend/src/utils/rrPolicyView.ts` | `compareNetRr` | **SỬA** | `(actualNetRR: number \| null, policyView: SelectionRrPolicyView)` | **Xử lý Failure B**: Đặt kiểm tra explicit `ORDER_SNAPSHOT.status === 'INVALID'` TRƯỚC nhánh generic `MISSING \|\| minRR === null`. Khi CURRENT gặp `status === 'ERROR'`, trả về `UNKNOWN, meetsMin: false, label: 'Lỗi cấu hình R:R'`. Giữ nguyên full-precision comparison `actual >= minRR` ở nhánh hợp lệ. |
| `frontend/src/hooks/useRrPolicySettings.ts` | `useRrPolicySettings` | **SỬA** | Hook nội bộ quản lý đọc/ghi Settings Min Net R:R | Bổ sung `policyReadStatus`, `errorOrigin`, `saveError`, `readError`. Phân biệt rạch ròi: Lỗi GET đặt `policyReadStatus: 'ERROR', errorOrigin: 'READ'`, giữ `saved` làm lastKnown; Lỗi POST đặt `errorOrigin: 'SAVE'`, bảo toàn `saved` confirmed cũ không bị xóa, giữ draft dirty; `onSaveSuccess` bọc trong `Promise.resolve` cô lập lỗi callback khỏi save commit. |
| `frontend/src/App.tsx` | `currentPolicyContext` | **SỬA** | `useMemo` kết hợp `rrPolicy` và `tradingPolicy` | Kiểm tra `rrPolicy.policyReadStatus === 'ERROR'` trước khi xem xét `saved.value`/`isConfirmed`, trả về context `status: 'ERROR', isConfirmed: false`. Truyền `rrPolicyStatus` đồng bộ xuống mọi consumer UI. |
| `frontend/src/components/CG01DiagnosticCard.tsx` | `cg01-applied-rr-badge` | **SỬA** | `appliedMinNetRR`, `rrPolicyStatus`, `isRrPolicyConfirmed` | Khi `rrPolicyStatus === 'ERROR'` hiển thị `'Lỗi tải cấu hình'`, không bịa `'1:1.50'` hay `'Đang áp dụng'`. |
| `frontend/src/components/TradeDetailsDrawer.tsx` | R:R Snapshot Label | **SỬA** | `selectedIntent`, `appliedMinNetRR`, `policy` | Nhánh `selView.status === 'INVALID'` đứng trước nhánh missing; hiển thị chính xác `'Ngưỡng snapshot lúc ARM không hợp lệ'` (không bị gộp thành `'Chưa lưu ngưỡng lúc ARM'`). |
| `frontend/src/components/QuickDecisionSidebar.tsx` | `POLICY_GATE` blocker | **SỬA** | `selectionPolicyView`, `rrComparison` | Phân biệt blocker: tiêu đề `'Lỗi cấu hình R:R'` khi ERROR, `'Chưa xác nhận cấu hình R:R'` khi LOADING, `'Cấu hình R:R không hợp lệ'` khi INVALID. Nút ARM bị disabled an toàn. |
| `frontend/src/api/client.ts` | `TradingPolicy` | **SỬA** | TypeScript Interface | Bổ sung `rr_policy_status?: string` phục vụ type safety đồng bộ backend response. |
| `frontend/src/__tests__/v16_39_19_rr_closure.test.tsx` | Test Suite Regression | **MỚI** | 18 unit & component tests production thực tế | Tái hiện Failure A, Failure B, controls, hook error separation, và DOM rendering của production components. |
| `frontend/tests/e2e/v16_39_19_rr_no_reload.e2e.ts` | Playwright E2E Suite | **MỚI** | Playwright Chromium (port 8790 isolated backend) | Kiểm chứng Save qua UI Form cập nhật 2 chiều (1.50 và 2.50) trong cùng một document, xác minh window token không bị mất (cấm reload). |

---

## 2. Chuỗi Truyền Dẫn & Error Separation Dataflow

```mermaid
graph TD
    subgraph "Backend API"
        GET_API[/api/scalp/config GET]
        POST_API[/api/scalp/config POST]
    end

    subgraph "Hook Boundary (useRrPolicySettings)"
        GET_API -->|Read Success| HOOK_READY[policyReadStatus: READY, errorOrigin: null]
        GET_API -->|Read Error| HOOK_READ_ERR[policyReadStatus: ERROR, errorOrigin: READ, saved: lastKnown]
        POST_API -->|Save Error| HOOK_SAVE_ERR[errorOrigin: SAVE, policyReadStatus: READY, saved cũ giữ nguyên, draft dirty]
        POST_API -->|Save Success| HOOK_SAVED[saved mới, policyReadStatus: READY, onSaveSuccess callback]
    end

    subgraph "App Projection (App.tsx)"
        HOOK_READ_ERR --> CURRENT_ERR[currentPolicyContext: status ERROR, isConfirmed false]
        HOOK_READY --> CURRENT_OK[currentPolicyContext: status READY, value canonical]
        HOOK_SAVED --> CURRENT_OK
    end

    subgraph "Resolver & Comparator (rrPolicyView.ts)"
        CURRENT_ERR --> RESOLVER[resolveSelectionRrPolicy]
        CURRENT_OK --> RESOLVER
        ORDER_INTENT[SelectedTradeIntent: OPEN_POSITION] --> RESOLVER
        
        RESOLVER -->|Frozen Order Priority| ORDER_VIEW[ORDER_SNAPSHOT: KNOWN / INVALID / MISSING]
        RESOLVER -->|Current Gating| CURRENT_VIEW[CURRENT: READY / ERROR / LOADING]

        ORDER_VIEW --> COMPARATOR[compareNetRr]
        CURRENT_VIEW --> COMPARATOR
    end

    subgraph "UI Consumers"
        COMPARATOR -->|ORDER INVALID| DRAWER[TradeDetailsDrawer: Ngưỡng snapshot lúc ARM không hợp lệ]
        COMPARATOR -->|ORDER MISSING| DRAWER_MISSING[TradeDetailsDrawer: Chưa lưu ngưỡng lúc ARM]
        COMPARATOR -->|CURRENT ERROR| SIDEBAR[QuickDecisionSidebar: POLICY_GATE - Lỗi cấu hình R:R]
        CURRENT_ERR --> BADGE[CG01DiagnosticCard: Lỗi tải cấu hình]
        CURRENT_OK --> BADGE_OK[CG01DiagnosticCard: 1:x.xx]
    end
```

---

## 3. Truth Table 6 Chiều Kiểm Chứng

| Intent Source | Policy Input Status | Snapshot / Policy Value | Actual Net R:R | Resolver View Status | Comparator Result Status | Meets Min? | UI Label / Reason |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `DRAFT` | `ERROR` (isConfirmed: true) | 1.50 | 1.70 | `ERROR` (non-authoritative) | `UNKNOWN` | **false** | *Lỗi cấu hình R:R* (Failure A repaired) |
| `DRAFT` | `LOADING` | null | 1.70 | `LOADING` | `UNKNOWN` | **false** | *Đang tải cấu hình R:R...* |
| `DRAFT` | `READY` | 1.50 | 1.70 | `READY` | `MET` | **true** | Đạt yêu cầu |
| `DRAFT` | `READY` | 2.50 | 1.70 | `READY` | `NOT_MET` | **false** | *chưa đạt 1:2.50* |
| `DRAFT` | `READY` | 1.50 | 1.49996 | `READY` | `NOT_MET` | **false** | *chưa đạt 1:1.50* (full-precision) |
| `OPEN_POSITION` | `INVALID` | null | 1.70 | `INVALID` | `INVALID` | **false** | *Ngưỡng snapshot lúc ARM không hợp lệ* (Failure B repaired) |
| `OPEN_POSITION` | `MISSING` | null | 1.70 | `MISSING` | `UNKNOWN` | **false** | *Chưa lưu ngưỡng lúc ARM* |
| `OPEN_POSITION` | `KNOWN` | 1.50 | 1.70 (CURRENT ERROR) | `KNOWN` (1.50) | `MET` | **true** | Đạt ngưỡng lúc ARM (Frozen độc lập) |
| `DRAFT` | `READY` | 1.50 | NaN / Infinity / null | `READY` | `INVALID` | **false** | *R:R không hợp lệ* (Actual invalid) |

---

## 4. UI Consumer Inventory & Test Evidence

| UI Component | Phạm vi áp dụng | Thuộc tính / State | Test tương ứng | Kết quả xác minh |
| :--- | :--- | :--- | :--- | :--- |
| `QuickDecisionSidebar` | CURRENT Selection | `appliedMinNetRR`, `rrPolicyStatus`, `isRrPolicyConfirmed` | `v16_39_19_rr_closure.test.tsx` (Test 18) | **PASS** — Chặn ARM, hiển thị `POLICY_GATE` ("Lỗi cấu hình R:R"). |
| `TradeDetailsDrawer` | Historical Order Snapshot | `selectedIntent` (mapped), `activeSection="risk"` | `v16_39_19_rr_closure.test.tsx` (Test 17) | **PASS** — Hiển thị "Ngưỡng snapshot lúc ARM không hợp lệ" riêng biệt với MISSING. |
| `CG01DiagnosticCard` | CURRENT Policy Badge | `appliedMinNetRR`, `rrPolicyStatus` | `v16_39_19_rr_closure.test.tsx` (Test 16) | **PASS** — Hiển thị "Lỗi tải cấu hình" khi ERROR. |
| `RrPolicySettingsCard` | Settings Mutation Form | `saved`, `draftText`, `status`, `error` | `v16_39_19_rr_no_reload.e2e.ts` (E01, E02) | **PASS** — Nhập form 1.50 và 2.50, cập nhật tức thì no-reload. |
| `ChartComponent` | Canvas Overlay | `currentPolicyContext.value` | `npm run build` + Vitest full suite | **PASS** — Không fallback cứng 2.0; không dịch chuyển hình học. |
