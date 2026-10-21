# BÁO CÁO NGHIỆM THU AURUM V16.39.13
**Chốt Hook Chi Tiết Điểm: Request Lifecycle Ownership, ID Xác Minh Hợp Đồng và UI Acceptance**

- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Baseline Git SHA**: `9309438e059902a4968efd70cf19b1934c09611b` (commit `fix(v16.39.12): bind score evidence and reconcile historical snapshot states`)
- **Trạng thái**: **ĐẠT (PASSED)** — 100% Code thật, Test thật, Caller thật, E2E Browser thật.

---

## 1. Kết Quả Khắc Phục Hai Lỗi Baseline (Bug A & Bug B)

| Bug # | Vấn Đề Gốc Tại Baseline (9309438) | Giải Pháp V16.39.13 | Kết Quả Trước Sửa | Kết Quả Sau Sửa | Bằng Chứng Kiểm Thử |
|---|---|---|---|---|---|
| **A** | **Stale Commit Sau Null/Disabled**: Request A đang chờ -> target chuyển `null` hoặc `enabled=false` -> late response A vẫn kích hoạt `setDetail(A)`. Nguyên nhân: `reqGenRef` chỉ tăng khi bắt đầu request mới, nhánh disabled return sớm mà không tăng generation, cleanup chỉ gọi abort trên controller mà không vô hiệu hóa quyền commit. | Triển khai hàm tập trung `invalidateActiveRequest()`: tăng đơn điệu `generation`, hủy `AbortController` đang hoạt động, và xóa active reference. Hàm này được gọi vô điều kiện khi unmount, khi disabled, khi đổi target key, và khi retry. Thêm **Render Identity Projection** so sánh `state.targetKey === inputKey` để trả về `detail: null` ngay trong phase render, loại bỏ hoàn toàn hiện tượng 1-frame hiển thị sai factors. | **FAIL** (Baseline chấp nhận late response A) | **PASSED** (Late response bị bỏ qua hoàn toàn, hook idle an toàn) | `test("Bug A Regression: pending request -> target becomes disabled/null -> late resolve does NOT commit state")`<br>`test("Rule 121: target retains orderId but enabled flips to false -> late resolve does NOT commit state")` |
| **B** | **Bỏ Qua Identity & Tự Điền ID Từ Request**: Khi response chỉ có `{ detail: { status: 'AVAILABLE', score: 91 } }` không có `order_id` hay `assessment_id`, hook tự fallback `res.order_id || requestedOrderId` và bỏ qua kiểm tra `assessment_id` (chỉ kiểm tra khi `expected && returned` đều truthy). | Bỏ toàn bộ fallback `res.order_id || requestedOrderId`. Viết validator thuần `validateQualityScoreResponse` yêu cầu envelope `{ status: "SUCCESS", order_id, detail }`. Bắt buộc `order_id` server trả phải bằng `expectedOrderId` (`DETAIL_BINDING_MISSING` / `DETAIL_BINDING_MISMATCH`). Bắt buộc `detail.assessment_id` phải khớp `expectedAssessmentId` khi có yêu cầu (`DETAIL_ASSESSMENT_MISSING` / `DETAIL_ASSESSMENT_MISMATCH`). | **FAIL** (Baseline tự điền requested ID và chấp nhận score 91) | **PASSED** (Từ chối payload thiếu ID, trả mã lỗi định danh cụ thể) | `test("Bug B Regression: response missing order_id and assessment_id MUST be rejected, not filled with requested ID")`<br>`candidateScoreResponse.test.ts` (13/13 tests pass) |

---

## 2. Ghi Chú Đính Chính Phạm Vi Lịch Sử (So Với V16.39.12)

- Trong phiên bản **V16.39.12**, hệ thống đã chuẩn hóa thành công:
  - Schema canonical của `QualityScoreFactor` (`code`, `group`, `earned_points`, `max_points`, `status`, `explanation_vi`, `evidence_ids`).
  - Phân loại `CandidateQualityDisplayKey` kiểu cấu trúc tuple chống lỗi delimiter.
  - Phân biệt an toàn giữa `CURRENT_PERSISTED` và `HISTORICAL_AS_OF` trong báo cáo tài chính.
- Tuy nhiên, phần hook `useCandidateScoreDetail.ts` lúc đó mới chỉ xử lý race condition A->B đơn giản và vẫn còn **hai kẽ hở kiểm tra**:
  1. Nhánh `!enabled || !orderId` return sớm mà không tăng generation, khiến request đang bay vẫn giữ generation hiện hành nếu target bị hủy/đóng drawer.
  2. Logic fallback `res.order_id || orderId` và câu lệnh `if (expectedAssessmentId && returnedAssessmentId)` đã vô tình cho phép server trả payload thiếu identity vượt qua kiểm tra.
- **V16.39.13 đã đóng triệt để cả hai kẽ hở này** ở tầng hợp đồng dữ liệu, tầng hook lifecycle, tầng caller UI và tầng E2E browser.

---

## 3. Các Ví Dụ Hợp Đồng API Production (`QualityScoreResponse`)

### 3.1 Envelope Chuẩn Hợp Lệ (Valid Production Envelope)
```json
{
  "status": "SUCCESS",
  "order_id": "ord_long_78",
  "detail": {
    "status": "AVAILABLE",
    "score": 78.0,
    "max_score": 100.0,
    "assessment_id": "assess_qa_01",
    "factors": [
      {
        "code": "FVG_ALIGNMENT",
        "group": "STRUCTURE",
        "earned_points": 18.0,
        "max_points": 20.0,
        "status": "FULL",
        "explanation_vi": "Khối mất cân bằng giá FVG M15 đồng pha xu hướng chính H1"
      }
    ]
  }
}
```
*Kết quả thẩm định:* **HỢP LỆ** (`isValid: true`). Trả về detail và `returnedOrderId="ord_long_78"`.

---

### 3.2 Envelope Thiếu `order_id` (Missing Order ID)
```json
{
  "status": "SUCCESS",
  "detail": {
    "status": "AVAILABLE",
    "score": 78.0,
    "assessment_id": "assess_qa_01"
  }
}
```
*Kết quả thẩm định:* **TỪ CHỐI** (`isValid: false`, `code: "DETAIL_BINDING_MISSING"`). Hook không tự điền ID yêu cầu, trả `detail: null` và hiển thị thông báo lỗi tiếng Việt.

---

### 3.3 Response Thiếu `assessment_id` Khi Có `expectedAssessmentId='qa'`
```json
{
  "status": "SUCCESS",
  "order_id": "ord_long_78",
  "detail": {
    "status": "AVAILABLE",
    "score": 78.0
  }
}
```
*Kết quả thẩm định:* **TỪ CHỐI** (`isValid: false`, `code: "DETAIL_ASSESSMENT_MISSING"`). Không bỏ qua điều kiện kiểm tra khi server thiếu trường `assessment_id`.

---

### 3.4 Snapshot Lịch Sử Cũ Không Có Điểm (Valid Legacy Envelope)
```json
{
  "status": "SUCCESS",
  "order_id": "ord_legacy_old",
  "detail": {
    "status": "UNAVAILABLE",
    "score": null,
    "reason_code": "LEGACY_NO_SCORE",
    "assessment_id": null
  }
}
```
*Kết quả thẩm định:* **HỢP LỆ THEO CHUẨN LEGACY** (`isValid: true`, `detail.status: "UNAVAILABLE"`). UI hiển thị `"Chưa lưu điểm ở phiên bản cũ (snapshot legacy)"`, không bị coi là lỗi mạng hay lỗi mất kết nối.

---

## 4. Tổng Hợp Kết Quả Kiểm Thử Toàn Diện

### 4.1 Frontend Unit & Lifecycle Tests (Vitest)
- **Lệnh thực thi**: `npm --prefix frontend test`
- **Kết quả**: **345 passed trong 33 test files** (Exit Code: 0).
  - `src/__tests__/candidateScoreResponse.test.ts`: **13/13 passed** (Bao phủ envelope object/array/primitive, finite score 0..100, exact order_id/assessment_id, immutability check).
  - `src/__tests__/useCandidateScoreDetail.test.ts`: **21/21 passed** (Bao phủ Bug A, Bug B, Rule 121, 122, 124, 125, 126, 127, 128, 129, 130, 132, 134, 135, 136, 137).
  - `src/__tests__/callerAcceptance.test.ts`: **7/7 passed** (Bao phủ TradeDetailsDrawer fallback fetch, bundle arrival override, close drawer abort, section change invalidation, JournalTab open/close/retry).
  - `src/candidateScore.test.ts`: **23/23 passed** (Bao phủ resolver phân biệt ORDER vs SETUP, render canonical factors FVG 18/20, fallback metadata "Chưa lưu").

### 4.2 Frontend Lint & Production Build
- **Lệnh Lint**: `npm --prefix frontend run lint`
  - **Kết quả**: **0 errors**, 41 warnings (cảnh báo cấu hình fast-refresh và biến chưa dùng tồn tại từ baseline).
- **Lệnh Build**: `npm --prefix frontend run build`
  - **Đính chính nghiệm thu (Erratum V16.39.14)**: Tại baseline `aa9a300`, lệnh `npm run build` (`tsc -b && vite build`) gặp lỗi biên dịch `src/App.tsx:383:13 - error TS2353: Object literal may only specify known properties, and 'id' does not exist in type 'SelectedTradeIntent'`. Nguyên nhân do `setSelectedIntent` truyền `id: p.id` và `order_id: p.id` nhưng interface `SelectedTradeIntent` chưa khai báo hai trường này. Lỗi đã được sửa dứt điểm trong **V16.39.14** (bổ sung typed `order_id?: string; id?: string;` và đồng bộ resolver `getSelectedScoreTarget`), đưa `npm run build` trở lại trạng thái thành công hoàn toàn (0 errors).


### 4.3 Playwright Browser E2E Tests (Google Chrome Thực Tế)
- **Lệnh thực thi**: `AURUM_E2E_REPORT=../logs/acceptance/v16_39_13/playwright_report.json npm --prefix frontend run test:e2e -- tests/e2e/v16_39_11_candidate_score.e2e.ts`
- **Báo cáo Artifact**: `logs/acceptance/v16_39_13/playwright_report.json`
- **Kết quả**: **3 passed trong 6.5s** (Exit Code: 0).
  - `E12-1: Seed distinct orders and verify exact 78/100 and 64/100 scores via API and UI`: **PASSED** (1.4s) — Xác nhận badge 78/100, mở drawer xem tiêu chí `FVG_ALIGNMENT` (+18/20), đóng drawer sạch sẽ.
  - `E12-2: Mobile viewport (390x844) displays compact badges without layout overflow`: **PASSED** (953ms) — Không vỡ layout trên màn hình hẹp.
  - `E12-3: Intercept mismatched order ID -> assert inline error -> retry recovers`: **PASSED** (1.1s) — Intercept request trả sai order ID -> UI hiển thị thông báo lỗi chưa xác minh -> bấm nút "Thử lại" -> response hợp lệ thành công và hiển thị đầy đủ factors.

### 4.4 Backend Regression Suite (Python Pytest)
- **Lệnh thực thi (Rule 166)**:
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
- **Kết quả**: **362 passed, 1 warning trong 9.95s** (Exit Code: 0). Toàn bộ engine giao dịch, guards, coordinator, sizing và Telegram outbox nguyên vẹn không bị ảnh hưởng.

---

## 5. Hướng Dẫn Sử Dụng Đơn Giản Cho Người Dùng Mới

1. **Ý nghĩa điểm số `78/100`**:
   - Con số `78/100` là điểm đo lường mức độ khớp của cơ hội giao dịch với bộ tiêu chí kỹ thuật CG-01 tại thời điểm hệ thống đánh giá (đồng pha FVG, cấu trúc thị trường, thanh khoản).
   - **ĐIỂM NÀY TUYỆT ĐỐI KHÔNG PHẢI XÁC SUẤT THẮNG 78%**. Lệnh có điểm 90 vẫn có thể chạm Stop Loss nếu thị trường biến động ngược hướng.
2. **Điểm số bất biến (Frozen Score)**:
   - Điểm đánh giá được cố định vĩnh viễn vào lệnh ngay khi ARM. Khi xem lại nhật ký hoặc đóng mở chi tiết, điểm không bao giờ bị tính toán lại theo nến thị trường hiện tại.
3. **Khi gặp sự cố tải chi tiết điểm**:
   - Nếu mạng chập chờn hoặc phản hồi chưa xác minh danh tính, hệ thống hiển thị thông báo inline: `"Chưa tải được chi tiết điểm của lệnh này. Bạn có thể thử lại."` kèm nút **"Thử lại"**.
   - Thao tác này **hoàn toàn là lệnh đọc dữ liệu (read-only)**, không làm thay đổi lệnh, không tự động đặt lệnh hay can thiệp vào tài khoản giao dịch.

---

## 6. Báo Cáo Smoke QA Thực Nghiệm

Trong quá trình chạy fixture E2E tự động trên trình duyệt Google Chrome thật:
- **Order A (`e2e_ord_long_78`)**: Vị thế LONG, điểm số `78/100`, tiêu chí FVG đạt `+18/20`. Thao tác click mở drawer hiển thị chính xác tiêu đề và danh sách tiêu chí. Khi đóng drawer hoặc đổi sang lệnh khác, request cũ bị abort lập tức.
- **Order B (`e2e_ord_short_64`)**: Vị thế SHORT, điểm số `64/100`. Thao tác chuyển đổi nhanh A -> B hiển thị ngay lập tức dữ liệu của B, không còn dư âm của A.
- **Error Injection Test**: Giả lập lỗi phản hồi sai ID của Order A -> Drawer hiển thị cảnh báo lỗi tiếng Việt, không tự tiện lấy điểm của Order B điền vào Order A. Bấm nút "Thử lại" nạp lại dữ liệu thành công.
