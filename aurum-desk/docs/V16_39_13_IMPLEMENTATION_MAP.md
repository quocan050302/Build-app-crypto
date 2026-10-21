# AURUM V16.39.13 — IMPLEMENTATION MAP
**Chốt Hook Chi Tiết Điểm: Request Lifecycle Ownership, ID Xác Minh và UI Acceptance**

## 1. Tổng Quan & Phạm Vi
Bản vá V16.39.13 hoàn thiện toàn diện vòng đời request (Lifecycle Ownership) và xác minh danh tính phản hồi (Response Identity Verification) của `useCandidateScoreDetail`. Đóng vĩnh viễn 2 lỗ hổng đã được chứng minh trên baseline:
1. **Lỗ hổng A (Stale Commit after Null/Disabled)**: Request A đang chờ -> target đổi sang `null` hoặc `enabled=false` (đóng drawer/hủy chọn) -> response A đến muộn vẫn commit `setDetail(A)`.
2. **Lỗ hổng B (Missing ID Auto-Fill Loophole)**: Response thiếu `order_id` hoặc thiếu `assessment_id` (khi caller kỳ vọng `expectedAssessmentId`) vẫn được chấp nhận do hook tự điền requested ID và bỏ qua điều kiện xác minh khi ID trả về là null/falsy.

---

## 2. Bản Đồ Hàm & Interface

### 2.1 Contracts & Types (`frontend/src/types/candidateScore.ts`)
| Type / Interface | Định Nghĩa | Mục Đích & Ràng Buộc |
|---|---|---|
| `QualityScoreResponse` | `{ status: 'SUCCESS' \| string, order_id?: string, detail?: CandidateScoreDetail \| null }` | Hợp đồng envelope production trả về từ `GET /api/v1/orders/{order_id}/quality-score`. |
| `QualityScoreErrorCode` | `'DETAIL_BINDING_MISSING' \| 'DETAIL_BINDING_MISMATCH' \| 'DETAIL_ASSESSMENT_MISSING' \| 'DETAIL_ASSESSMENT_MISMATCH' \| 'INVALID_DETAIL_PAYLOAD' \| 'FAILED_TO_LOAD_DETAIL'` | Tập mã lỗi chuẩn hóa cho validation và error state. |
| `ValidatedScoreDetailResult` | `{ valid: true, detail: CandidateScoreDetail, orderId: string }` | Kết quả xác minh thành công kèm exact orderId từ server. |
| `InvalidScoreDetailResult` | `{ valid: false, errorCode: QualityScoreErrorCode, errorMessage: string }` | Kết quả xác minh thất bại kèm thông báo tiếng Việt. |
| `ScoreDetailValidationResult` | `ValidatedScoreDetailResult \| InvalidScoreDetailResult` | Discriminated union cho kết quả validator. |

### 2.2 Tiện Ích Thuần Túy (`frontend/src/utils/candidateScore.ts`)
| Hàm | Đầu Vào (Input) | Đầu Ra (Output) | Caller Production | Unit Test |
|---|---|---|---|---|
| `makeScoreRequestKey()` | `orderId?: string \| null`, `expectedAssessmentId?: string \| null` | `string \| null` (JSON tuple `[id, assessId]`) | `useCandidateScoreDetail` | `candidateScoreResponse.test.ts` |
| `translateQualityScoreError()` | `code?: string \| null` | `string` (thông báo tiếng Việt dễ hiểu) | `CandidateScoreDetails`, `validateQualityScoreResponse` | `candidateScoreResponse.test.ts` |
| `validateQualityScoreResponse()` | `raw: unknown`, `expected: { orderId: string, expectedAssessmentId?: string \| null }` | `ScoreDetailValidationResult` | `useCandidateScoreDetail` | `candidateScoreResponse.test.ts` |
| `getSelectedScoreTarget()` | `binding?: ChartBinding \| null`, `selectedIntent?: SelectedTradeIntent \| null` | `ScoreTarget \| null` (`kind: 'ORDER' \| 'SETUP'`, `id`, `assessmentId`) | `TradeDetailsDrawer` | `candidateScore.test.ts` |

### 2.3 Hook Production (`frontend/src/hooks/useCandidateScoreDetail.ts`)
- **Options**: `{ orderId?: string | null, expectedAssessmentId?: string | null, enabled?: boolean }`
- **Output**: `{ detail: CandidateScoreDetail | null, loading: boolean, error: string | null, returnedOrderId: string | null, refetch: () => void }`
- **Các cơ chế kiểm soát bất biến**:
  - `activeRequestRef`: Ref duy nhất quản lý `{ generation: number, controller: AbortController | null, targetKey: string | null }`.
  - `invalidateActiveRequest()`: Helper tăng generation đơn điệu, abort controller hiện tại, xóa targetKey. Gọi trên: đổi target, disable (`enabled=false`), clear (`orderId=null`), retry, và unmount.
  - **Single Initiator Effect**: `useEffect` là nơi duy nhất khởi động network request. `refetch()` chỉ tăng `retryRevision` để kích hoạt effect có ownership rõ ràng, không tạo controller riêng lẻ.
  - **Strict Post-Await Guards**: Sau `await api.getOrderQualityScore(...)`, kiểm tra 3 chốt chặn:
    1. `controller.signal.aborted === false`
    2. `activeRequestRef.current.generation === currentGen`
    3. `activeRequestRef.current.targetKey === currentTargetKey`
  - **Render Identity Projection**: Dẫn xuất `isKeyMatching = Boolean(currentTargetKey && dataState.targetKey === currentTargetKey)`. Khi target đổi từ A sang B, ngay tại frame render đầu tiên trước khi effect chạy, `detail`, `error`, và `returnedOrderId` đều lập tức trả về `null`, loại bỏ hoàn toàn hiện tượng chớp factors cũ của A dưới tiêu đề B.

### 2.4 API Client Typed Boundary (`frontend/src/api/client.ts`)
- `getOrderQualityScore: async (orderId: string, signal?: AbortSignal): Promise<QualityScoreResponse>`:
  - Giữ nguyên envelope `{ status: 'SUCCESS', order_id, detail }` từ `res.data`.
  - Chuyển `signal` xuống axios.
  - Không bóc tách `detail` trước khi validator có cơ hội xác minh `order_id`.

### 2.5 Caller Integration
1. **`frontend/src/JournalTab.tsx`**:
   - Sử dụng `isJournalDrawerOpen = Boolean(selectedTrade?.id)`.
   - Gọi `useCandidateScoreDetail({ orderId: selectedTrade?.id, expectedAssessmentId: selectedTrade?.candidate_score_summary?.assessment_id, enabled: isJournalDrawerOpen })`.
   - Nối đầy đủ `loading={isScoreDetailLoading}`, `error={scoreDetailError}`, `onRetry={refetchScoreDetail}` vào `<CandidateScoreDetails>`.
   - Sửa an toàn kiểm tra nullish `selectedTrade.realized_r != null` chống TypeError.
2. **`frontend/src/components/TradeDetailsDrawer.tsx`**:
   - `isScoreNeeded = Boolean(isOpen && activeSection === 'quality' && !bundleDetail && scoreTarget?.kind === 'ORDER')`.
   - Fallback bị vô hiệu hóa và request in-flight bị abort ngay khi `bundleDetail` về, khi đóng drawer, hoặc khi chuyển section khác.
   - Default `upcomingSetups = []` chống crash khi caller không truyền.
3. **`frontend/src/components/CandidateScoreDetails.tsx`**:
   - Hiển thị inline error banner có nút "Thử lại" (`disabled={loading}`, `type="button"`).
   - Bảo toàn hiển thị loading/error khi `activeData` chưa có hoặc `UNAVAILABLE`.
   - Hiển thị nhãn `"Chưa lưu"` cho các trường metadata persisted là `null` (`base_score`, `lesson_delta`, `threshold`).
4. **`frontend/src/App.tsx`**:
   - Cập nhật `selectedIntent` cho `source === 'OPEN_POSITION'` sử dụng đúng `order_id: p.id` và `id: p.id`, ngăn ngừa việc nhầm `setup_id` của setup thành order ID.

---

## 3. Bản Đồ Kiểm Thử (Test Map)

| Suite | File Kiểm Thử | Số Tests | Nội Dung Bao Phủ |
|---|---|---|---|
| **Response Validator** | `frontend/src/__tests__/candidateScoreResponse.test.ts` | 13 | Kiểm tra thuần `validateQualityScoreResponse`, `makeScoreRequestKey`, `translateQualityScoreError`. Rejection flat payload, missing/mismatched ID, missing assessment ID, immutability. |
| **Hook Lifecycle** | `frontend/src/__tests__/useCandidateScoreDetail.test.ts` | 21 | Kiểm tra toàn bộ Rules 111-137: Reproduction Bug A & B, cancel-to-disabled with ID retained, A->B->A race, stale rejection, retry same target, retry disabled, unmount, StrictMode, render identity projection, dedupe. |
| **Component Unit** | `frontend/src/candidateScore.test.ts` | 23 | Normalization, format, translation, badge, drawer metrics ("Chưa lưu"), resolver. |
| **Caller Acceptance** | `frontend/src/__tests__/callerAcceptance.test.ts` | 7 | Drawer fallback lifecycle, bundle arrival priority, section change invalidation, drawer close abort, JournalTab opening/closing and retry. |
| **Browser E2E** | `frontend/tests/e2e/v16_39_11_candidate_score.e2e.ts` | 3 | E12-1 (Seed distinct orders, verify exact API & UI badge 78/100, open drawer, verify FVG 18/20); E12-2 (Mobile 390x844 layout); E12-3 (Pending close abort, mismatched identity inline error, retry button recovery). |
| **Backend Regression** | 14 test files theo Rule 166 | 362 | Toàn bộ pipeline, coordinator, scoring invariants, risk guards, telegram, session policy không bị suy suyển. |
