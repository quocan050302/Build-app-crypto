# V16.39.16 ACCEPTANCE REPORT
## Báo Cáo Nghiệm Thu Cài Đặt Min Net R:R Toàn App + Khép Kín Preview / Snapshot / Chart

**Ngày nghiệm thu:** 2026-10-21  
**Phiên bản:** Aurum V16.39.16  
**Nhánh Git:** `fix/v14-1-rr-flow`  
**Commit Baseline:** `fix(v16.39.15): unify effective net RR policy across paper entry and chart` (`e539c6cee132e77914e04977e49eea8c70d14e1c`)  

---

### 1. Tóm Tắt Mục Tiêu & Vấn Đề Đã Giải Quyết (Executive Summary)

Sau bản vá V16.39.15 (hợp nhất chính sách Net R:R động), hệ thống vẫn còn một số điểm hở về giao diện người dùng và tính toàn vẹn snapshot:
1. **Thiếu Card Cài Đặt Tập Trung & Độc Lập:** Người dùng cần một nơi lưu trữ cấu hình Min Net R:R tập trung, độc lập với nút lưu cấu hình đòn bẩy/ký quỹ, đặt tại tab "Quản Trị Vốn & Ký Quỹ" (`activeTab === 'paper'`).
2. **Sidebar Không Bị Quá Tải:** Sidebar (`CG01DiagnosticCard` / `QuickDecisionSidebar`) không tạo thêm tab mới hay panel cồng kềnh; chỉ hiển thị badge R:R áp dụng (read-only) kèm nút "Chỉnh trong Cài đặt" để chuyển tab và scroll mượt tới card cài đặt `#rr-policy-settings-card`.
3. **Hardcode 2.0 Trong Quality Preview:** Backend `evaluate_quality_checks` trong `setup_quality_service.py` trước đây so sánh `net_rr >= 2.0` cứng, khiến setup có net R:R 1.84 bị đánh rớt tiêu chuẩn R:R dù chính sách hiệu lực là 1.50R.
4. **Rơi Rớt Metadata Trên 5 Producers:**
   - Producer 1: Refresh vị thế mở `p` trong `refreshAccountAndHealth` rơi rớt `minNetRR`, `rrPolicySource`, `rrPolicyFingerprint`.
   - Producer 2: `handleFocusTradeOnChart` rơi rớt `minNetRR`, `rrPolicySource`, `rrPolicyFingerprint`.
   - Producer 3: `handleFocusSetupOnChart` không copy `minNetRR` từ `planToOverlay` sang `selectedIntent`.
   - Producer 4: Tạo draft từ setup (`handleCopySetupToDraft`) không áp dụng `rrPolicy.saved.value`.
   - Producer 5: Kéo thả draft trên chart (`onOverlayChange`) làm mất `minNetRR` và thông tin chính sách.
5. **Chế Độ Lịch Sử Bất Biến (Frozen Snapshot Invariant):** Lệnh quá khứ không có snapshot ngưỡng tuyệt đối KHÔNG được gán mặc định 2.0 giả tạo; phải trả về `min_net_rr: null`, `meets_min_rr: null`, và `rr_compliance_status: "UNKNOWN"`.

**Bản vá V16.39.16 đã hoàn thành xuất sắc 100% các mục tiêu trên:**
- Card cài đặt chuyên biệt `RrPolicySettingsCard` đã được tích hợp tại tab "Quản Trị Vốn & Ký Quỹ". Nút Lưu R:R độc lập với form đòn bẩy, mutation API chỉ gửi `{ min_net_rr: value }`.
- Sidebar gọn gàng: chỉ hiển thị badge read-only kèm nút "Chỉnh trong Cài đặt" với smooth scroll.
- Khép kín toàn bộ 5 producers mà không làm thất thoát metadata hay bịa đặt ngưỡng lịch sử.
- Toàn bộ backend và frontend kiểm thử đạt 100% tỷ lệ pass.

---

### 2. Bằng Chứng Nghiệm Thu Tự Động (Automated Verification Evidence)

#### 2.1 Backend Unit Tests Chuyên Biệt V16.39.16
```bash
PYTHONPATH=. ./venv/bin/pytest tests/test_v16_39_16_rr_settings_and_preview_gaps.py -v
```
- **Kết quả:** 9 / 9 passed (100%) trong 0.08s.
- **Chi tiết các ca kiểm thử:**
  - `test_resolve_quality_rr_threshold_sources`: Phân giải đúng ngưỡng từ context profile, fallbacks legacy an toàn.
  - `test_evaluate_quality_checks_respects_configured_threshold`: Đánh giá chất lượng setup tôn trọng ngưỡng động (1.50R, 2.50R), giải thích tiếng Việt chính xác.
  - `test_evaluate_quality_checks_unrounded_boundaries`: Kiểm tra biên không làm tròn số học (1.49R fail, 1.50R pass).
  - `test_evaluate_quality_checks_handles_invalid_or_missing_threshold`: Xử lý giá trị ngưỡng không hợp lệ hoặc thiếu.
  - `test_read_frozen_rr_threshold`: Đọc chính xác snapshot đóng băng, trả về tri-state (`KNOWN` | `MISSING` | `INVALID`).
  - `test_build_setup_display_plan_frozen_known_snapshot`: Hiển thị lệnh đóng băng với snapshot đã lưu.
  - `test_build_setup_display_plan_frozen_missing_snapshot`: Lệnh thiếu snapshot ngưỡng trả về `min_net_rr: None`, `meets_min_rr: None`, `rr_compliance_status: "UNKNOWN"` (không bịa 2.0).
  - `test_build_setup_display_plan_stored_legacy_missing_threshold`: Nhánh STORED_LEGACY giữ nguyên trạng thái UNKNOWN khi thiếu ngưỡng.
  - `test_preview_setup_quality_api_dynamic_rr`: Endpoint API preview quality đánh giá theo ngưỡng DB thực tế.

#### 2.2 Backend Core Regression Test Suites
```bash
PYTHONPATH=. ./venv/bin/pytest tests/test_v16_39_16_rr_settings_and_preview_gaps.py \
  tests/test_v16_39_15_rr_policy_unification.py \
  tests/test_v16_38_adaptive_paper_readiness.py \
  tests/test_v16_36_paper_tonight_production_regression.py \
  tests/test_v16_35_rr_propagation_telegram_proximity.py \
  tests/test_v16_34_cg01_runtime_ownership_consistency.py \
  tests/test_v14_4_1_production_pipeline.py \
  tests/test_v14_4_2_entry_regression.py \
  tests/test_v16_3_cg01_pipeline.py \
  tests/test_v16_3_cg01_contracts.py \
  tests/test_v15_0_telegram_and_api.py \
  tests/test_migration_runtime_upgrade.py \
  tests/test_setup_quality_api_v14.py \
  tests/test_setup_quality_service_v14.py \
  tests/test_v16_39_paper_learning_regression.py -q
```
- **Kết quả:** 227 / 227 passed (100%) trong 5.15s.

#### 2.3 Frontend Kiểm Thử Đơn Vị (Vitest)
```bash
npx vitest run src/__tests__/v16_39_16_rr_policy_settings.test.ts
npm test
```
- **Tệp chuyên biệt V16.39.16:** 14 / 14 passed (100%).
- **Toàn bộ frontend:** 35 / 35 test files, 371 / 371 tests passed (100%) trong 1.41s.

#### 2.4 Frontend Linter (Oxlint)
```bash
npm run lint
```
- **Kết quả:** 0 lỗi (0 errors), 44 warnings cũ trên 120 files.

#### 2.5 Frontend Production Build
```bash
npm run build
```
- **Kết quả:** `tsc -b && vite build` SUCCEEDED (Exit Code 0).
- Không có lỗi type checking hay bundling.

---

### 3. Checklist Nghiệm Thu Chi Tiết (Acceptance Checklist)

| Yêu cầu kiểm tra | Trạng thái | Ghi chú nghiệm thu |
|---|:---:|---|
| Card cài đặt Min Net R:R tại tab Quản Trị Vốn & Ký Quỹ | ĐẠT | `RrPolicySettingsCard` tại `activeTab === 'paper'`, có id `rr-policy-settings-card` |
| Nút Lưu R:R độc lập với form đòn bẩy/ký quỹ | ĐẠT | Chỉ gửi `{ min_net_rr: value }`, không submit sibling form |
| Sidebar hiển thị badge read-only + nút điều hướng | ĐẠT | Badge `1:{(appliedMinNetRR ?? 2.0).toFixed(2)}`, click chuyển tab và scroll mượt |
| Preview Quality đánh giá theo ngưỡng động | ĐẠT | `evaluate_quality_checks` dùng ngưỡng `resolve_quality_rr_threshold` |
| Không bịa đặt 2.0 cho lệnh lịch sử thiếu snapshot | ĐẠT | Trả về `min_net_rr: null`, `meets_min_rr: null`, `rr_compliance_status: "UNKNOWN"` |
| Khép kín Producer 1 (Vị thế mở `p`) | ĐẠT | Nạp `minNetRR`, `rrPolicySource`, `rrPolicyFingerprint` qua `getOrderRrFields` |
| Khép kín Producer 2 (Focus trade trên chart) | ĐẠT | Nạp đầy đủ metadata qua `getOrderRrFields` vào overlay & selectedIntent |
| Khép kín Producer 3 (Focus setup trên chart) | ĐẠT | Copy `minNetRR` từ `planToOverlay` sang `selectedIntent` |
| Khép kín Producer 4 (Tạo draft từ setup) | ĐẠT | Sử dụng `rrPolicy.saved.value` cho draft |
| Khép kín Producer 5 (Kéo thả draft trên chart) | ĐẠT | `onOverlayChange` bảo toàn `minNetRR` và policy metadata |
| Giữ nguyên hình học draft khi policy đổi | ĐẠT | ChartComponent giữ nguyên Entry/SL/TP/bars, chỉ tính lại tài chính |
| Bảo vệ Race Condition | ĐẠT | `useRrPolicySettings` có generation ref và AbortController |
| Không thay đổi công thức SMC, bias D1/H4/H1, triggers nến | ĐẠT | Giữ nguyên 100% core trading domain |
| Không thay đổi công thức sizing / leverage / liquidation | ĐẠT | Giữ nguyên Bitget USDT-Perpetual rules |

---

### 4. Kết Luận Bàn Giao
Bản vá Aurum V16.39.16 đã được hoàn thành đầy đủ, sạch sẽ, đáp ứng toàn diện mọi yêu cầu kỹ thuật và ràng buộc kiến trúc. Mã nguồn sẵn sàng để commit và push lên nhánh `fix/v14-1-rr-flow`.
