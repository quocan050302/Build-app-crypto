# V16.39.16 IMPLEMENTATION MAP
## Cài Đặt Min Net R:R Toàn App + Khép Kín Preview / Snapshot / Chart

**Phiên bản:** Aurum V16.39.16  
**Nhánh Git:** `fix/v14-1-rr-flow`  
**Thư mục làm việc:** `aurum-desk`  
**Baseline commit:** `e539c6cee132e77914e04977e49eea8c70d14e1c`  
**Ngày cập nhật:** 2026-10-21  

---

### 1. Kiến Trúc & Mục Tiêu Kỹ Thuật (Architecture & Root Cause)

#### 1.1 Vấn đề cốt lõi cần giải quyết
1. **Thiếu Card Cài Đặt Tập Trung:** Người dùng cần một nơi lưu trữ cấu hình Min Net R:R tập trung, độc lập với nút lưu cấu hình đòn bẩy/ký quỹ, đặt tại tab "Quản Trị Vốn & Ký Quỹ" (`activeTab === 'paper'`).
2. **Sidebar Không Bị Quá Tải:** Sidebar (`CG01DiagnosticCard` / `QuickDecisionSidebar`) không tạo thêm tab mới hay panel cồng kềnh; chỉ hiển thị badge R:R áp dụng (read-only) kèm nút "Chỉnh trong Cài đặt" để chuyển tab và scroll mượt tới card cài đặt.
3. **Hardcode 2.0 Trong Quality Preview:** Backend `evaluate_quality_checks` trong `setup_quality_service.py` trước đây so sánh `net_rr >= 2.0` cứng, khiến setup có net R:R 1.84 bị đánh rớt tiêu chuẩn R:R dù chính sách hiệu lực là 1.50R.
4. **Rơi Rớt Metadata Trên 5 Producers:**
   - Producer 1: Refresh vị thế mở `p` trong `refreshAccountAndHealth` rơi rớt `minNetRR`, `rrPolicySource`, `rrPolicyFingerprint`.
   - Producer 2: `handleFocusTradeOnChart` rơi rớt `minNetRR`, `rrPolicySource`, `rrPolicyFingerprint`.
   - Producer 3: `handleFocusSetupOnChart` không copy `minNetRR` từ `planToOverlay` sang `selectedIntent`.
   - Producer 4: Tạo draft từ setup (`handleCopySetupToDraft`) không áp dụng `rrPolicy.saved.value`.
   - Producer 5: Kéo thả draft trên chart (`onOverlayChange`) làm mất `minNetRR` và thông tin chính sách.
5. **Chế Độ Lịch Sử Bất Biến (Frozen Snapshot Invariant):** Lệnh quá khứ không có snapshot ngưỡng tuyệt đối KHÔNG được gán mặc định 2.0 giả tạo; phải trả về `min_net_rr: null`, `meets_min_rr: null`, và `rr_compliance_status: "UNKNOWN"`.

---

### 2. Chi Tiết Triển Khai Backend

#### 2.1 `backend/services/setup_quality_service.py`
- Bổ sung helper thuần túy `resolve_quality_rr_threshold(context)`:
  - Trích xuất `min_net_rr` từ `context["profile"]["min_net_rr"]` hoặc `context["min_net_rr"]`.
  - Kiểm tra tính hợp lệ: số thực hữu hạn $\ge 1.50$.
  - Trả về `(threshold, source)`. Nếu không có, dự phòng an toàn `(2.0, "LEGACY_DEFAULT")`.
- Cập nhật hàm thuần túy `evaluate_quality_checks`:
  - Thay thế ngưỡng 2.0 hardcode bằng `threshold` động.
  - Thực hiện so sánh không làm tròn số học: `net_rr >= threshold`.
  - Sinh chuỗi giải thích tiếng Việt động: `f"Tỷ lệ Net R:R sau phí ({net_rr:.2f}R) đạt ngưỡng yêu cầu ({threshold:.2f}R)"` hoặc `f"Tỷ lệ Net R:R sau phí ({net_rr:.2f}R) thấp hơn ngưỡng tối thiểu ({threshold:.2f}R)"`.

#### 2.2 `backend/services/strategy_service.py`
- Trong `_assess_and_save_quality`:
  - Phân giải chính sách hiệu lực: `rr_ctx = resolve_effective_rr_policy(db)`.
  - Truyền `q_context["profile"] = {"min_net_rr": rr_ctx.min_net_rr}` vào `assess_setup_quality`.
  - Lưu trữ `OrderPlanSnapshot` với đầy đủ `min_net_rr_at_arm`.

#### 2.3 `backend/services/setup_display_plan_service.py`
- Bổ sung helper `read_frozen_rr_threshold(source_dict)`:
  - Đọc `min_net_rr_at_arm` hoặc `min_net_rr` từ snapshot dictionary hoặc DTO.
  - Phân loại tri-state nghiêm ngặt:
    - Nếu có giá trị hợp lệ $\ge 1.50$: trả về `(float, source)`.
    - Nếu thiếu hoặc null: trả về `(None, "HISTORICAL_ORDER_NO_THRESHOLD_SNAPSHOT")`.
- Cập nhật nhánh lệnh đóng băng và `STORED_LEGACY`:
  - Nếu thiếu snapshot ngưỡng: gán `eff_snap_min_rr = None`, `meets_min_rr = None`, `rr_compliance_status = "UNKNOWN"`.
  - Tuyệt đối không fallback về 2.0 giả mạo lịch sử.

#### 2.4 `backend/schemas.py`
- Bổ sung `cost_snapshot: Optional[str] = None` vào `PaperOrderResponse` để chuyển tiếp nguyên vẹn snapshot JSON về frontend.

---

### 3. Chi Tiết Triển Khai Frontend

#### 3.1 `frontend/src/utils/rrPolicyView.ts`
- Module mới đảm nhận phân giải và chuẩn hóa trạng thái Min Net R:R:
  - `normalizeRrPolicyResponse(raw)`: Kiểm tra phản hồi từ `/api/scalp/config` và `/api/v1/policy/trading`, yêu cầu giá trị $\ge 1.50$.
  - `resolveOrderRrView(orderDto)`: Phân loại đơn hàng lịch sử thành `KNOWN` | `MISSING` | `INVALID`.
  - `getOrderRrFields(orderDto)`: Trích xuất an toàn `{ minNetRR, rrPolicySource, rrPolicyFingerprint }` cho `RiskRewardData` và `SelectedTradeIntent`.

#### 3.2 `frontend/src/hooks/useRrPolicySettings.ts`
- Hook quản lý state duy nhất (Single State Owner) cho Min Net R:R:
  - Quản lý `saved`, `draftText`, `status`, `error`, `successMessage`.
  - Mutation độc lập: `POST /api/scalp/config` chỉ gửi `{ min_net_rr: value }`, không chạm tới đòn bẩy/ký quỹ của form tài khoản.
  - Race protection: Sử dụng `requestGenRef` và `AbortController` để chống ghi đè khi mạng trễ.
  - Không làm mất bản nháp người dùng đang gõ khi polling nền hoàn tất.

#### 3.3 `frontend/src/components/RrPolicySettingsCard.tsx`
- Component điều khiển đặt tại tab `activeTab === 'paper'`:
  - Có định danh `id="rr-policy-settings-card"`.
  - Các nút preset nhanh: 1.50, 2.00, 2.50, 3.00 (chỉ sửa nháp, không auto-save).
  - Input số thực tùy chỉnh: `min="1.5"`, `step="0.01"`.
  - Nút "Lưu Cài Đặt (Min Net R:R)" và nút "Khôi Phục" độc lập hoàn toàn với form đòn bẩy.
  - Hướng dẫn rõ ràng bằng tiếng Việt về bản chất bộ lọc sau phí, không can thiệp hình học SL/TP.

#### 3.4 Khép kín 5 Producers trong `frontend/src/App.tsx`
1. **Producer 1 (Vị thế mở `p`):** Sử dụng `getOrderRrFields(p)` trong `refreshAccountAndHealth` để nạp `minNetRR`, `rrPolicySource`, `rrPolicyFingerprint` vào cả `overlay` và `selectedIntent`.
2. **Producer 2 (Focus trade trên chart):** Sử dụng `getOrderRrFields(trade)` trong `handleFocusTradeOnChart` để nạp chính xác metadata vào `overlay` và `selectedIntent`.
3. **Producer 3 (Focus setup trên chart):** Bổ sung `minNetRR`, `rrPolicySource`, `rrPolicyFingerprint` từ `overlay` vào `setSelectedIntent`.
4. **Producer 4 (Tạo draft từ setup):** Gán `minNetRR: draftMinNetRR` (ưu tiên `overlay.minNetRR ?? rrPolicy.saved.value`) vào cả `overlayWithRr` và `selectedIntent`.
5. **Producer 5 (Kéo thả draft trên chart):** `onOverlayChange` bảo toàn `minNetRR`, `rrPolicySource`, `rrPolicyFingerprint` trong `activeOverlay` và `selectedIntent`.

#### 3.5 Đồng bộ Sidebar và Chart
- `CG01DiagnosticCard.tsx` & `QuickDecisionSidebar.tsx`:
  - Hiển thị badge áp dụng `1:{(appliedMinNetRR ?? 2.0).toFixed(2)}` (read-only).
  - Nút "Chỉnh trong Cài đặt" gọi `onOpenRrSettings`, chuyển `activeTab` sang `'paper'` và scroll mượt tới `#rr-policy-settings-card`.
- `ChartComponent.tsx`:
  - Khi `effectiveMinNetRR` thay đổi, giữ nguyên mức giá Entry/SL/TP và thanh dự phóng hiện có của draft, chỉ tính toán lại `calc` tài chính.
- `TradeDetailsDrawer.tsx`:
  - Nhận `policy` đồng bộ với `rrPolicy.saved.value`.

---

### 4. Bảng Đối Soát Tệp Tin Thay Đổi (File Diff Matrix)

| STT | Tệp tin | Trạng thái | Mô tả thay đổi |
|---|---|---|---|
| 1 | `backend/services/setup_quality_service.py` | MODIFIED | Bổ sung `resolve_quality_rr_threshold`, bỏ hardcode 2.0 trong `evaluate_quality_checks` |
| 2 | `backend/services/strategy_service.py` | MODIFIED | Truyền profile `min_net_rr` vào `_assess_and_save_quality` |
| 3 | `backend/services/setup_display_plan_service.py` | MODIFIED | Bổ sung `read_frozen_rr_threshold`, gán `None` / `UNKNOWN` cho lệnh lịch sử thiếu snapshot |
| 4 | `backend/schemas.py` | MODIFIED | Thêm `cost_snapshot` vào `PaperOrderResponse` |
| 5 | `backend/tests/test_v16_39_16_rr_settings_and_preview_gaps.py` | NEW | 9 bài kiểm thử backend bao phủ toàn bộ gap quality & display plan |
| 6 | `frontend/src/utils/rrPolicyView.ts` | NEW | Normalizer, tri-state order view resolver, và `getOrderRrFields` extractor |
| 7 | `frontend/src/hooks/useRrPolicySettings.ts` | NEW | Single state owner hook quản lý draft, save, status, và race protection |
| 8 | `frontend/src/components/RrPolicySettingsCard.tsx` | NEW | Giao diện cài đặt Min Net R:R độc lập tại tab Quản Trị Vốn & Ký Quỹ |
| 9 | `frontend/src/utils/planLevels.ts` | MODIFIED | Bổ sung trường `min_net_rr` nullable và compliance status vào `ResolvedDisplayPlan` |
| 10 | `frontend/src/components/CG01DiagnosticCard.tsx` | MODIFIED | Loại bỏ editor độc lập, hiển thị badge read-only + nút điều hướng |
| 11 | `frontend/src/components/QuickDecisionSidebar.tsx` | MODIFIED | Chuyển tiếp props `appliedMinNetRR`, `rrPolicySource`, `onOpenRrSettings` |
| 12 | `frontend/src/ChartComponent.tsx` | MODIFIED | Bảo toàn hình học draft khi policy đổi; drag handler dùng policy hiện hành |
| 13 | `frontend/src/App.tsx` | MODIFIED | Tích hợp hook, card cài đặt, liên kết sidebar, và khép kín 5 producers |
| 14 | `frontend/src/__tests__/v16_39_16_rr_policy_settings.test.ts` | NEW | 14 bài kiểm thử vitest cho normalizer, tri-state view, extractor, và overlay |

---

### 5. Kết Quả Kiểm Thử (Verification Summary)

- **Backend Unit Tests:**
  - `test_v16_39_16_rr_settings_and_preview_gaps.py`: 9/9 tests PASSED.
  - Regression test suite (15 file kiểm thử trọng yếu): 227/227 tests PASSED in 5.15s.
- **Frontend Unit Tests:**
  - `v16_39_16_rr_policy_settings.test.ts`: 14/14 tests PASSED.
  - Full frontend suite: 35 test files, 371/371 tests PASSED in 1.41s.
- **Frontend Build & Linter:**
  - `npm run build`: `tsc -b && vite build` SUCCEEDED (0 errors).
  - `npm run lint`: Oxlint SUCCEEDED (0 errors, 44 warnings pre-existing).
