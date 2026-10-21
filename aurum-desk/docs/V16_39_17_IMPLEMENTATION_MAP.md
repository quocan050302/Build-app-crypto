# V16.39.17 IMPLEMENTATION MAP
## Đồng Bộ Min Net R:R Từ Settings Qua Mọi Luồng Current Và Toàn Bộ UI

**Phiên bản:** Aurum V16.39.17  
**Nhánh Git:** `fix/v14-1-rr-flow`  
**Thư mục làm việc:** `aurum-desk`  
**Baseline commit:** `b3c407e4703a1b91d72980e2393ba66b98c1bf10`  
**Ngày cập nhật:** 2026-10-21  

---

### 1. Kiến Trúc & Mục Tiêu Kỹ Thuật (Architecture & Scope)

#### 1.1 Mục Tiêu Trọng Tâm
1. **Đồng Bộ Hoàn Toàn Ngưỡng Đã Lưu:** Khi người dùng lưu `min_net_rr = 1.50` (hoặc bất kỳ giá trị hợp lệ nào trong $[1.20, 5.00]$):
   - Mọi luồng đánh giá hiện hành (**CURRENT**): setup chưa arm, preview, draft trên chart, quyết định ARM mới, thanh bên quyết định nhanh, thẻ thiết lập, đánh giá chu kỳ SMC không bao giờ bị chặn bởi logic hardcode `2.0`.
   - Setup có Net R:R nằm trong khoảng $[1.50, 2.00)$ phải được hiển thị hợp lệ (xanh), nút ARM mở khóa, không xuất hiện blocker `NET_RR_TOO_LOW`.
2. **Bảo Tồn Tuyệt Đối Invariant:**
   - **A. CURRENT:** Dùng ngưỡng đã lưu mới nhất từ settings/database. Khi settings đổi, draft trên chart và draft intent tự động rebase tài chính tức thì theo ngưỡng mới mà không làm thay đổi hình học vào lệnh (geometry Entry/SL/TP).
   - **B. FROZEN (Lệnh đã ARM/Khớp/Đóng, Bằng chứng lịch sử):** Giữ nguyên snapshot đóng băng tại thời điểm arm. Nếu thiếu snapshot lịch sử, hiển thị `min_net_rr: null` và trạng thái tuân thủ `UNKNOWN` (tuyệt đối không bịa đặt 2.0 hoặc 1.5).
   - **C. RESEARCH (Nghiên cứu/Backtest/Forward Paper):** Kế thừa cấu hình hiệu lực tại thời điểm khởi tạo phiên nếu không có ghi đè; các phiên đã chạy giữ nguyên snapshot.
   - **D. UNKNOWN:** Hiển thị trung tính, không cho phép ARM dựa trên chính sách phỏng đoán.
3. **Giữ Nguyên Mô Hình Cốt Lõi:**
   - Không thay đổi trigger cấu trúc SMC, thiên kiến HTF D1/H4/H1.
   - Không thay đổi công thức tính khối lượng, mô hình phí/trượt giá hai chiều thực tế, mô hình ký quỹ, hoặc hàng đợi Telegram outbox.

---

### 2. Bản Đồ Thành Phần Frontend

#### 2.1 `frontend/src/utils/rrPolicyView.ts`
- **`resolveSelectionRrPolicy`**: Phân giải chính sách đa tầng (CURRENT vs ORDER_SNAPSHOT vs UNKNOWN vs INVALID), ưu tiên snapshot lệnh đã arm trước, fallback về settings hiện hành cho draft/watch setup.
- **`compareNetRr`**: Đánh giá so sánh động giữa Net R:R thực tế và ngưỡng chính sách:
  - Trả về cấu trúc chuẩn: `{ status: 'MET' | 'NOT_MET' | 'UNKNOWN' | 'INVALID', actualNetRR, requiredMinNetRR, diff, label, meetsMin }`.
  - Hỗ trợ định dạng nhãn động tiếng Việt: `Đạt 1:X.XX (Yêu cầu 1:Y.YY)` hoặc `Chưa đạt 1:Y.YY (Thực tế 1:X.XX)`.
- **`rebaseDraftRrPolicy`**: Tính toán lại các thông số tài chính của overlay bản nháp khi ngưỡng chính sách thay đổi, bảo toàn nguyên vẹn tọa độ Entry, SL, TP, số nến dự phóng, hướng lệnh và khối lượng.
- **`rebaseTradeIntentRrPolicy`**: Cập nhật intent kiểu `DRAFT`, không làm biến dạng các intent vị thế hoặc setup đã arm.

#### 2.2 `frontend/src/hooks/useRrPolicySettings.ts`
- **Khởi tạo An toàn:** `saved.value` khởi tạo là `null` với `isConfirmed: false` (không hiển thị 2.0 giả mạo trước khi nhận phản hồi từ server).
- **Phân tách Trạng thái Nhập liệu:** Sử dụng `isUserEditedRef` để duy trì `isDirty = true` ngay cả khi người dùng đang gõ dở chuỗi không hợp lệ (như `1.`, `abc`), ngăn chặn việc background poll ghi đè lên thao tác người dùng.
- **Strict Number Parsing:** Sử dụng `Number(draftText.trim())` để từ chối các chuỗi có tiền tố số như `1.5abc`.
- **Lifecycle & Error Isolation:** Tích hợp `AbortController` chống race-condition, bọc `options?.onSaveSuccess` trong `try/catch` để lỗi từ callback không làm gãy luồng xác nhận lưu.

#### 2.3 `frontend/src/components/QuickDecisionSidebar.tsx`
- Loại bỏ hoàn toàn so sánh hardcode `currentNetRR < 2.0`.
- Sử dụng `compareNetRr(currentNetRR, selectionPolicyView)`:
  - Thêm blocker `NET_RR_TOO_LOW` với nhãn tham chiếu động chính xác (ví dụ `1:2.50` nếu cài đặt 2.5, hoặc không bị chặn nếu cài đặt 1.5 và R:R là 1.84).
  - Cập nhật màu sắc chỉ báo và text cảnh báo phụ `(chưa đạt 1:X.XX)` theo thời gian thực.

#### 2.4 `frontend/src/ExpectedEntryPanel.tsx`
- Đồng bộ màu sắc hiển thị Net R:R theo `compareNetRr`.

#### 2.5 `frontend/src/NYSessionPanel.tsx`
- Hiển thị văn bản tìm kiếm điều kiện vào lệnh fallback theo ngưỡng động `appliedMinNetRR ?? policy?.effective_min_net_rr`.

#### 2.6 `frontend/src/components/TradeDetailsDrawer.tsx`
- Sử dụng `resolveSelectionRrPolicy`, định dạng số thập phân chuẩn 2 chữ số (ví dụ `1:1.75`), loại bỏ chuỗi hardcode `1:2.0 (Mặc định)`.

#### 2.7 `frontend/src/components/RrPolicySettingsCard.tsx`
- Xử lý an toàn khi `saved.value === null` (hiển thị trạng thái đang tải thay vì đoán trước 2.0).

#### 2.8 `frontend/src/utils/userMessageCatalog.ts` & `researchReasonText.ts`
- Loại bỏ các chuỗi fallback `1:2.00` và `1:1.50` cứng trong `INSUFFICIENT_RR` và `NET_RR_TOO_LOW`.
- Hiển thị thông điệp trung tính khi thiếu tham số ngưỡng cụ thể.

#### 2.9 `frontend/src/App.tsx`
- Tích hợp điều phối viên trung tâm `currentEffectiveMinNetRR = rrPolicy.saved.value ?? tradingPolicy?.effective_min_net_rr ?? 2.0`.
- Triển khai `handleRrPolicySaved`:
  - Cập nhật state cục bộ `tradingPolicy`.
  - Rebase ngay lập tức bản nháp chart đang mở (`activeOverlay`) và intent được chọn (`selectedIntent`).
  - Gọi đồng thời `refreshAccountAndHealth()`, `refreshUpcoming()`, `refreshAnalysis()`.
- Truyền `appliedMinNetRR={currentEffectiveMinNetRR}` tới `QuickDecisionSidebar`, `TradeDetailsDrawer`, `NYSessionPanel`, và `ChartComponent`.
- Áp dụng kiểm tra ngưỡng động trong bảng kịch bản thị trường sắp tới (`scenarios.no_trade` và `displayPlan.financial`).

---

### 3. Bản Đồ Thành Phần Backend

#### 3.1 `backend/services/smc_scenario_config.py`
- Bổ sung helper thuần túy `build_current_scenario_config(base, min_net_rr)` sử dụng `dataclasses.replace` để tạo bản sao cấu hình chu kỳ mà không đột biến trạng thái toàn cục.

#### 3.2 `backend/services/strategy_service.py`
- Trong `evaluate_market_reading_cycle`:
  - Phân giải chính sách hiệu lực: `rr_ctx = resolve_effective_rr_policy(db)`.
  - Tạo cấu hình chu kỳ với ngưỡng phân giải: `cfg = build_current_scenario_config(active_scenario_config, min_net_rr=rr_ctx.min_net_rr)`.
  - Đảm bảo các setup phát hiện mới mang chính xác ngưỡng cấu hình từ database.

#### 3.3 `backend/services/forward_paper_service.py`
- Trong `create_forward_paper_session`:
  - Phân giải `default_min_rr` từ database qua `resolve_effective_rr_policy(db)`.
  - Khi người dùng truyền `risk_config` tùy chỉnh bán phần, hợp nhất an toàn mà không làm mất `min_net_rr`.

#### 3.4 `backend/services/cg01_config_factory.py`
- Cập nhật phân giải `min_net_rr`: ưu tiên `scalp_cfg.min_net_rr`, fallback sang `resolve_effective_rr_policy(db)` nếu có `db`, trước khi dự phòng mặc định.

---

### 4. Ma Trận Kiểm Thử & Xác Nhận (Test Matrix)

| Phạm vi kiểm thử | File kiểm thử | Số lượng test | Kết quả |
| :--- | :--- | :--- | :--- |
| **Frontend Unit & Integration** | `src/__tests__/v16_39_17_rr_sync.test.ts` | 21 tests | **PASSED** |
| **Frontend RR Policy Settings** | `src/__tests__/v16_39_16_rr_policy_settings.test.ts` | 14 tests | **PASSED** |
| **Frontend Full Vitest Suite** | 36 test files (bao gồm candidate score, annotation, realtime, v14.1..v14.4) | 392 tests | **PASSED (392/392)** |
| **Frontend Production Build** | `tsc -b && vite build` | N/A | **PASSED (Exit Code 0)** |
| **Frontend Linter** | `oxlint` | 121 files | **PASSED (0 Errors)** |
| **Backend V16.39.17 Suite** | `backend/tests/test_v16_39_17_rr_sync.py` | 6 tests | **PASSED** |
| **Backend V16.39 & V16.38** | `backend/tests/test_v16_39_*.py` + `test_v16_38_*.py` | 268 tests | **PASSED (268/268)** |
| **Backend V16.34 - V16.36** | `backend/tests/test_v16_36_*.py` + `test_v16_35_*.py` + `test_v16_34_*.py` | 86 tests | **PASSED (86/86)** |
| **Backend V14.1 - V14.3** | `backend/tests/test_v14_1_*.py` + `test_v14_2_*.py` + `test_v14_3_*.py` | 250 tests | **PASSED (250/250)** |
