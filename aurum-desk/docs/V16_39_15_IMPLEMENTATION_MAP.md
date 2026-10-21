# V16.39.15 IMPLEMENTATION MAP
## Thống Nhất Chính Sách Tỷ Lệ Lợi Nhuận/Rủi Rõ Ròng (Net R:R) Trên Toàn Bộ Luồng Paper Trading và Chart

**Ngày cập nhật:** 2026-10-21  
**Mã bản vá:** Aurum V16.39.15  
**Nhánh:** `fix/v14-1-rr-flow`  
**Mục tiêu chính:** Hợp nhất triệt để ngưỡng `min_net_rr = 1.5` trên luồng PAPER, loại bỏ các điểm chặn cứng 2.0 (hardcode) tại backend và frontend, đảm bảo các setup chất lượng trong vùng $[1.5, 2.0)$ được ARM, hiển thị trên chart và thực thi lệnh nhất quán mà không phá vỡ tính bất biến của snapshot lệnh đã ARM.

---

### 1. Kiến Trúc & Nguyên Lý Giải Quyết (Architecture & Root Cause)

#### 1.1 Vấn đề cốt lõi (Root Cause)
Trước V16.39.15, khi người dùng hoặc cấu hình hệ thống thiết lập `min_net_rr = 1.5` trong chế độ PAPER:
1. Sàn kiểm tra tính hợp lệ tại `policy_constants.py` hoặc schema Pydantic chặn cứng giá trị $< 2.0$.
2. Hàm `evaluate_setup_eligibility`, `StrategyService.evaluate_upcoming_setups`, và manual ARM endpoint tại `main.py` tự gán mặc định `min_net_rr = 2.0` cho `calculate_risk_reward`.
3. `SetupDisplayPlanService` khi render plan cho lệnh đã ARM/Open (frozen order) lại so sánh `net_rr >= 2.0` thay vì ngưỡng `min_net_rr_at_arm` đã chốt tại thời điểm ARM.
4. Plugin đồ họa biểu đồ `RiskRewardPrimitive.ts` hardcode `d.estimatedNetRR >= 2.0` để tô màu xanh lá (`#26a69a`) hoặc vàng (`#eab308`), khiến các lệnh hợp lệ ở mức 1.7x bị hiển thị nhầm là "Chưa đạt R:R".

#### 1.2 Mô hình thứ tự ưu tiên (3-Tier Resolution Hierarchy)
Dịch vụ mới [rr_policy_service.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/rr_policy_service.py) giải quyết chính sách hiệu lực thuần túy (pure read-only):
1. **Tier 1 (Canonical):** `models.SystemConfig` với khóa `"min_net_rr"` (chuỗi số thực $\ge 1.5$).
2. **Tier 2 (Compatibility):** `models.TradingPolicy` dòng `"default"` trường `min_net_rr`.
3. **Tier 3 (Default):** Hằng số an toàn `DEFAULT_MIN_NET_RR = 2.0`.
*Lưu ý bảo mật & tính toàn vẹn:* Nếu dữ liệu đã lưu trong DB bị hỏng (chuỗi rỗng, không phải số, NaN, Inf, boolean, $< 1.5$), hệ thống lập tức ném ngoại lệ có định kiểu `RrPolicyConfigError` (fail-closed), tuyệt đối không tự ý rơi vào 2.0 giả tạo.

#### 1.3 Tính bất biến của Snapshot lệnh (Snapshot Immutability)
- Khi một setup được ARM (tự động qua `StrategyService._auto_arm_candidate` hoặc bấm tay qua `/api/v1/trade/arm`), ngưỡng `min_net_rr` hiệu lực tại thời điểm đó được ghi cố định vào `OrderPlanSnapshot.cost_snapshot["min_net_rr_at_arm"]`.
- Khi lệnh Limit khớp (`ExecutionCoordinator.evaluate_orders_sync`), điều phối viên đọc `min_net_rr_at_arm` từ snapshot để kiểm tra trượt giá. Dù sau khi ARM quản trị viên có đổi chính sách lên 2.0 hay xuống 1.5, lệnh đã ARM vẫn tuân theo đúng ngưỡng tại thời điểm ARM của nó.

---

### 2. Danh Mục Các Tệp Đã Triển Khai (File Modifications Map)

| STT | Tệp tin | Trách nhiệm & Thay đổi chính |
|---|---|---|
| 1 | [backend/services/rr_policy_service.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/rr_policy_service.py) | **Mới tạo**: Cung cấp resolver `resolve_effective_rr_policy(db)`, bộ kiểm tra `validate_new_min_net_rr(raw)`, parser `parse_persisted_min_net_rr(raw)`, hàm `stage_min_net_rr_update(db, val, now_ms)` (không commit trước), và mã băm `compute_rr_policy_fingerprint`. |
| 2 | [backend/policy_constants.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/policy_constants.py) | Hạ sàn `min_net_rr` từ 2.0 xuống 1.5 trong `validate_trading_policy_update`, cập nhật thông điệp tiếng Việt. |
| 3 | [backend/services/scalp_m1_types.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/scalp_m1_types.py) | `load_effective_scalp_config(db)` tích hợp `resolve_effective_rr_policy(db)` trực tiếp, bảo lưu `RrPolicyConfigError`. |
| 4 | [backend/services/plan_financial_context_service.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/plan_financial_context_service.py) | Mở rộng `PlanFinancialContext` mang thêm `min_net_rr`, `rr_policy_source`, `rr_policy_fingerprint`. |
| 5 | [backend/services/eligibility_service.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/eligibility_service.py) | `evaluate_setup_eligibility` phân giải `min_rr` từ `resolve_effective_rr_policy` và truyền vào `calculate_risk_reward`. Xuất `min_net_rr`, `rr_policy_source`, `rr_policy_fingerprint`. |
| 6 | [backend/services/strategy_service.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/strategy_service.py) | Thay hardcode `min_net_rr: 2.0` trong `evaluate_upcoming_setups` và truyền `min_net_rr` vào `build_arm_plan_snapshot` trong `_auto_arm_candidate`. |
| 7 | [backend/services/setup_display_plan_service.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/setup_display_plan_service.py) | `is_frozen_order` đọc `min_net_rr_at_arm` từ snapshot để tính `meets_min_rr`; xuất `min_net_rr`, `rr_policy_source`, `rr_policy_fingerprint` trong DTO. |
| 8 | [backend/services/trading_policy_service.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/trading_policy_service.py) | Cập nhật `TradingPolicyService.update_policy` gọi `stage_min_net_rr_update` và bao bọc giao dịch rollback an toàn. |
| 9 | [backend/schemas.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/schemas.py) | Bổ sung field validator chặn boolean và None rõ ràng; thêm `effective_min_net_rr`, `rr_policy_source`, `rr_policy_fingerprint` vào `TradingPolicyResponse`. |
| 10 | [backend/main.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/main.py) | Sửa flow Manual ARM (dòng 1021-1081), `preview_setup_quality`, `get_scalp_config_endpoint`, `update_scalp_config_endpoint` (rollback đồng bộ cả 2 bảng), và `get_trading_policy_endpoint` (read-only không mutate DB). |
| 11 | [frontend/src/plugins/RiskRewardPrimitive.ts](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/plugins/RiskRewardPrimitive.ts) | Thay hardcode `2.0` trong `renderer` và `hitTest` bằng helper `resolvePrimitiveRrStatus(d)`, hỗ trợ hiển thị trung tính (xám) khi không rõ ngưỡng lịch sử. |
| 12 | [frontend/src/ChartComponent.tsx](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/ChartComponent.tsx) | Tiếp nhận `effectiveMinNetRR`, truyền vào draft tool, drag handler và overlay sync. |
| 13 | [frontend/src/utils/planLevels.ts](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/utils/planLevels.ts) | `planToOverlay` trích xuất và truyền `minNetRR` cùng nguồn gốc cấu hình vào overlay dữ liệu của chart. |
| 14 | [frontend/src/App.tsx](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/App.tsx) | Thêm `minNetRR` vào `SelectedTradeIntent`, truyền `effectiveMinNetRR={tradingPolicy?.min_net_rr ?? 2.0}` vào `ChartComponent`. |
| 15 | [frontend/src/components/CG01DiagnosticCard.tsx](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/components/CG01DiagnosticCard.tsx) | Cập nhật input `min="1.5"`, hiển thị badge `Đang áp dụng 1:x (Chưa lưu)`, đồng bộ với state policy. |
| 16 | [frontend/src/components/TradeDetailsDrawer.tsx](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/components/TradeDetailsDrawer.tsx) | Hiển thị rõ nhãn ngưỡng R:R áp dụng (Lúc ARM vs Hiện hành). |
| 17 | [frontend/src/utils/userMessageCatalog.ts](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/utils/userMessageCatalog.ts) | Bổ sung mã lỗi `NET_RR_TOO_LOW` với giải thích chi tiết bằng tiếng Việt. |
| 18 | [frontend/src/utils/calculator.ts](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/utils/calculator.ts) | Thêm `minNetRR` vào `ClientCalcResult` và các nhánh trả về hợp lệ cũng như trả về sớm (early returns). |

---

### 3. Ma Trận Kiểm Thử Tự Động (Test Verification Matrix)

#### 3.1 Backend Tests (`backend/tests/test_v16_39_15_rr_policy_unification.py`)
- **A01:** Ưu tiên `SystemConfig` (1.5) so với `TradingPolicy` (2.0) $\rightarrow$ kết quả 1.5, nguồn `SYSTEM_CONFIG`.
- **A02:** Dự phòng `TradingPolicy` khi thiếu `SystemConfig` $\rightarrow$ kết quả 2.5, nguồn `LEGACY_TRADING_POLICY`.
- **A03:** Mặc định hệ thống khi vắng cả hai bảng $\rightarrow$ kết quả 2.0, nguồn `DEFAULT`.
- **A04:** Giá trị lưu trong DB bị biến dạng (`"abc"`, `"-1.0"`, `"0.5"`, `"nan"`, `"inf"`, `""`) $\rightarrow$ ném `RrPolicyConfigError` (không âm thầm fallback).
- **A05:** Kiểm tra dữ liệu đầu vào $\rightarrow$ chặn $< 1.5$, chặn kiểu boolean, chặn `None`, chặn NaN/Inf.
- **A06:** Staging nguyên tử và rollback $\rightarrow$ xác nhận rollback giữ nguyên trạng thái cũ ở cả 2 bảng.
- **A07:** `load_plan_financial_context` mang đầy đủ `min_net_rr`, nguồn và mã băm.
- **A08:** `evaluate_setup_eligibility` cho phép setup có net RR ~ 1.84 khi policy là 1.5 và từ chối khi policy là 2.0.
- **A09:** Lệnh frozen hiển thị `meets_min_rr=True` theo snapshot 1.5 dù DB hiện hành đã nâng lên 2.0.
- **A10:** `ExecutionCoordinator` khớp lệnh có net RR 1.84 theo snapshot 1.5 dù DB đã đổi lên 2.0.
- **A11:** `ExecutionCoordinator` từ chối lệnh an toàn khi trượt giá làm net RR suy giảm dưới snapshot 1.5.
- **A12:** `build_arm_plan_snapshot` lưu đúng `min_net_rr_at_arm`.
- **A13:** Kiểm tra biên 1.49 (bị từ chối), 1.50 (chấp nhận), 1.51 (chấp nhận).
- **A14:** Endpoint `/api/scalp/config` rollback nguyên tử cả 2 bảng khi một trường phụ không hợp lệ.

#### 3.2 Frontend Tests (`frontend/src/__tests__/v16_39_15_rr_policy_frontend.test.ts`)
- **A12.1:** `resolvePrimitiveRrStatus` trả về màu xanh lá (`#26a69a`) khi net RR $\ge 1.5$.
- **A12.2:** `resolvePrimitiveRrStatus` trả về màu vàng cảnh báo (`#eab308`) và text `' · Chưa đạt R:R'` khi net RR $< minNetRR$.
- **A12.3:** `resolvePrimitiveRrStatus` xử lý trung tính màu xám (`#a1a1aa`) và text `' · Ngưỡng chưa xác định'` cho các lệnh lịch sử không có snapshot.
- **A12.4:** `calculateClientRiskReward` tôn trọng chính xác `minNetRR=1.5` trên setup LONG.
- **A12.5:** `calculateClientRiskReward` kiểm tra hướng SHORT với net RR trong khoảng $[1.5, 2.0)$.

---

### 4. Kết Quả Kiểm Tra Độc Lập (Build & Test Evidence)
- **Frontend Vitest:** 34 test files, 357 tests PASSED (100%).
- **Frontend ESLint:** 0 errors (39 warnings hiện hữu).
- **Frontend TypeScript & Vite Build:** `tsc -b && vite build` thành công, exit code 0.
- **Backend Pytest (`test_v16_39_15_rr_policy_unification.py`):** 35 tests PASSED (100%).
- **Backend Critical Regression Suites:** 59 tests PASSED (100%).
