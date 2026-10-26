# V16.39.11 ACCEPTANCE REPORT
## Hoàn Thiện Báo Cáo Paper & Hiển Thị Điểm Chất Lượng Trên Setup/Lệnh

**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `fix/v14-1-rr-flow`  
**Baseline Git Commit:** `0affe0f85d482d05d7f23ead846bd8d2ae05ae7c`  
**Commit Target:** V16.39.11  
**Project:** `aurum-desk`  
**Audit Date:** 2026-10-21 UTC  

---

### 1. BẢNG TỔNG HỢP KẾT QUẢ NGHIỆM THU

| Hạng Mục | Baseline (V16.39.10) | Sau Sửa (V16.39.11) | Trạng Thái |
| :--- | :--- | :--- | :--- |
| **Run Scope Mismatch Isolation** | Trả row mismatch, hiển thị +25 USD / 1 closed | Chặn tại boundary, trả `RUN_SCOPE_MISMATCH`, tài chính & funnel = `None` | **PASSED** |
| **Missing Run Query Isolation** | Funnel rơi về query toàn bộ symbol | Bắt buộc `run_id` hợp lệ; trả funnel rỗng có nhãn cô lập | **PASSED** |
| **Enum Phễu Cơ Hội** | Dùng chuỗi sai `PLANS_VALID` -> `plans_valid = 0` | Dùng production enum `OpportunityStage.PLAN_VALID` ("PLAN_VALID") -> đếm đúng | **PASSED** |
| **Vốn Đầu Kỳ Cửa Sổ Con** | Dùng `initial_capital` đầu run -> drawdown 10% sai lệch | `compute_window_opening_equity`: cộng verified closed trades trước `start_ms` -> drawdown ~9.09% | **PASSED** |
| **Lệnh Tương Lai Trong Window** | Order tạo sau `end` vẫn bị tính vào counts | Lọc chặt `order.created_at >= end_ms`, đếm chính xác historical state | **PASSED** |
| **Closed Trade Chưa Có PnL** | Coi như tổng đã hoàn tất, có thể báo Winrate 100% | Báo `accounting_status = "PARTIAL"`, hiển thị `verified_net_pnl_subtotal_usd`, không kết luận toàn bộ | **PASSED** |
| **Bảo Toàn Điểm Khi Khớp Lệnh** | `quality_assessment` bị mất sau `build_fill_plan_snapshot` | Whitelist `copy.deepcopy(arm_snap["quality_assessment"])` -> giữ nguyên 100% | **PASSED** |
| **Bảo Toàn Điểm Qua Lifecycle** | Chưa có endpoint & DTO chuẩn | ARM -> FILL -> CLOSE -> RESTART đều đọc cùng frozen snapshot | **PASSED** |
| **Hiển Thị Điểm UI (Badge)** | Không có badge điểm số | Badge "78/100" hiện tại setup, sidebar, position, journal, chart header | **PASSED** |
| **Cảnh Báo & Bản Chất Điểm** | Dễ nhầm với xác suất thắng | Disclaimer rõ ràng: "Điểm chất lượng setup CG-01, không phải xác suất thắng" | **PASSED** |
| **Backend Extended Regression** | 337 tests passed | **355 tests passed** (18 tests mới, 0 failed, 1 deprecation warning) | **PASSED** |
| **Frontend Test Suite** | 276 tests passed | **293 tests passed** (17 tests mới trong `candidateScore.test.ts`, 0 failed) | **PASSED** |
| **Frontend Production Build** | Không lỗi | `tsc -b && vite build` hoàn thành trong 1.32s, 0 errors | **PASSED** |

---

### 2. CHI TIẾT SỬA LỖI VÀ BẰNG CHỨNG THỰC NGHIỆM

#### Bug 1: Run Scope Mismatch & Missing Run Isolation
* **Baseline Behavior:** Khi yêu cầu scope `CG01` nhưng run ID trong DB mang scope `OTHER`, `find_existing_observation_run()` vẫn trả row data khiến `build_paper_runtime_snapshot()` tính toán và trả về 1 lệnh, +25 USD. Khi request run ID không tồn tại, query funnel gọi `read_run_funnel_records(run_id=None)` làm rò rỉ cơ hội từ các run khác.
* **V16.39.11 Fix:** `find_existing_observation_run()` trả về `resolution_status = "RUN_SCOPE_MISMATCH"` và không trả row cho pipeline tài chính. `build_paper_runtime_snapshot()` kiểm tra nghiêm ngặt `if resolution_status != "FOUND"`, đặt toàn bộ chỉ số tài chính bằng `None` và phễu rỗng. `read_run_funnel_records()` yêu cầu `run_id` khác rỗng.
* **Test Verification:** `test_001_scope_mismatch_prevents_financial_leakage` và `test_002_missing_run_returns_empty_isolated_funnel` trong `backend/tests/test_v16_39_11_snapshot_selector_accounting.py`.

#### Bug 2: Production Funnel Enum (`OpportunityStage.PLAN_VALID`)
* **Baseline Behavior:** Aggregator phễu kiểm tra `rec.stage == "PLANS_VALID"`. Trong production, enum chuẩn là `OpportunityStage.PLAN_VALID.value == "PLAN_VALID"`. Hậu quả là `plans_valid` luôn bằng 0 dù orchestrator đã chuyển trạng thái thành công.
* **V16.39.11 Fix:** `aggregate_funnel_readonly()` kiểm tra `rec.stage in (OpportunityStage.PLAN_VALID.value, "PLANS_VALID")` và ghi nhận đúng vào trường `plans_valid`.
* **Test Verification:** `test_008_plan_valid_production_enum_aggregated` ghi nhận record thật qua `OpportunityFunnelService.record_opportunity_transition()`, assert `plans_valid == 1`.

#### Bug 3: Sub-Window Opening Equity & Drawdown %
* **Baseline Behavior:** Khi người dùng xem báo cáo cho một cửa sổ con bắt đầu tại `start_ms`, drawdown % vẫn chia cho `initial_capital` ban đầu của toàn run (ví dụ 1000 USD), bỏ qua các khoản lãi/lỗ đã đóng trước `start_ms`.
* **V16.39.11 Fix:** Bổ sung hàm thuần `compute_window_opening_equity(orders, initial_capital, start_ms)`. Vốn đầu kỳ của cửa sổ bằng `initial_capital` cộng tổng realized PnL của tất cả các lệnh đã đóng trước `start_ms`. Nếu trước `start_ms` có lệnh thiếu PnL, opening equity được đánh dấu unverified và drawdown % trả `None`.
* **Test Verification:** `test_003_subwindow_opening_equity_and_drawdown` (vốn 1000, +100 trước start, -100 trong window -> opening = 1100, drawdown = 100 USD, max_drawdown_pct ≈ 9.09% thay vì 10%).

#### Bug 4: Historical State Window Counts
* **Baseline Behavior:** Số lượng lệnh mở (`open_positions_count`) và lên nòng (`armed_orders_count`) sử dụng trạng thái hiện tại của DB, đếm cả những lệnh được tạo ra sau `end_ms`.
* **V16.39.11 Fix:** Lọc bỏ tất cả các lệnh có `created_at >= end_ms`. Đối với các cửa sổ lịch sử kết thúc trong quá khứ, đánh dấu `historical_state_available` trung thực.
* **Test Verification:** `test_005_future_orders_excluded_from_historical_window` tạo order sau `end_ms` với state `OPEN`, kết quả đếm của cửa sổ trả về 0.

#### Bug 5: Unresolved Closed Trades Accounting
* **Baseline Behavior:** Nếu một lệnh đã đóng nhưng `realized_pnl_net` là `None`, báo cáo vẫn lấy các lệnh đã biết tính Winrate 100% và Net PnL như thể toàn bộ dữ liệu đã hoàn tất.
* **V16.39.11 Fix:** Bổ sung `accounting_status = "PARTIAL"`, tách riêng `verified_net_pnl_subtotal_usd` và `verified_winrate_pct`. Markdown ghi rõ "Phần đã xác nhận: X USD trên N kết quả" và liệt kê số lượng lệnh chưa thể đối soát.
* **Test Verification:** `test_006_unresolved_pnl_reports_partial_subtotal` (1 win +10 USD, 1 closed None -> verified subtotal = 10 USD, accounting_status = PARTIAL).

#### Bug 6: Mất `quality_assessment` sau `build_fill_plan_snapshot()`
* **Baseline Behavior:** Khi candidate được ARM, orchestrator lưu `arm_snap["quality_assessment"] = assessment.to_dict()`. Khi lệnh khớp (fill), `TradeLifecycleService.execute_fill()` gọi `build_fill_plan_snapshot()`, nhưng `extract_immutable_entry_metadata()` không đưa `quality_assessment` vào whitelist. Kết quả: cost_snapshot mới bị gán `quality_assessment = None`, mất điểm hoàn toàn.
* **V16.39.11 Fix:** Đưa `quality_assessment` vào whitelist của `extract_immutable_entry_metadata()` và sử dụng `copy.deepcopy()` để chống mutable alias.
* **Test Verification:** `test_001_fill_plan_snapshot_preserves_candidate_quality_score` và `test_002_fill_plan_snapshot_deep_copy_no_mutable_alias` trong `backend/tests/test_v16_39_11_score_display.py`.

#### Bug 7: DTO Hiển Thị & Điểm Bất Biến Lịch Sử
* **Baseline Behavior:** `schemas.PaperOrderResponse` thiếu trường điểm; các endpoint `/api/v1/position`, `/api/v1/journal`, `/api/v1/orders/{id}/chart-evidence` không trả về điểm chất lượng của lệnh.
* **V16.39.11 Fix:**
  - Tạo service thuần `backend/services/order_quality_display_service.py` với DTO `CandidateScoreDisplay` và `CandidateScoreDetail`.
  - Thêm `@property candidate_score_summary` trên `PaperOrder` (parse trực tiếp từ `cost_snapshot` sẵn có, không thêm cột DB, không query N+1).
  - Thêm `candidate_score_summary` vào `PaperOrderResponse` và `ChartEvidenceDTO`.
  - Thêm endpoint chuyên dụng `GET /api/v1/orders/{order_id}/quality-score`.
* **Test Verification:** `test_007_paper_order_response_serialization_parity` và `test_006_score_detail_contains_factors_and_blockers`.

#### Bug 8: Giao Diện Frontend Hiển Thị Badge "78/100"
* **Implementation:**
  - `frontend/src/types/candidateScore.ts`: Định nghĩa typed models `CandidateScoreSummary` và `CandidateScoreDetail`.
  - `frontend/src/utils/candidateScore.ts`: Các hàm thuần `normalizeCandidateScore()`, `formatCandidateScore()`, `translateDecision()`. Không tự chuyển đổi thành phần trăm `%`, giữ nguyên format `78/100`.
  - `frontend/src/components/CandidateScoreBadge.tsx`: Badge nhỏ gọn với tabular-nums font, accessible `aria-label`, tooltip chi tiết và `stopPropagation` khi click.
  - `frontend/src/components/CandidateScoreDetails.tsx`: Khung xem chi tiết gồm Điểm gốc (83), Bài học (-5), Điểm cuối (78/100), Ngưỡng lúc đánh giá (70), Badge FROZEN và disclaimer bắt buộc.
  - Tích hợp tại 5 vị trí:
    1. Header thẻ `upcomingSetups` (App.tsx): Cạnh state READY/ARMED/WAITING.
    2. QuickDecisionSidebar: Trong thẻ `selectedIntent` và thẻ `activePosition`.
    3. JournalTab: Trong cột Hướng của bảng giao dịch và trong Drawer chi tiết lệnh.
    4. SelectedChartHeader: Hiển thị ngay trên thanh công cụ chart khi chọn lệnh.
    5. TradeDetailsDrawer: Hiển thị chi tiết điểm CG-01 bên cạnh bằng chứng cấu trúc SMC.

---

### 3. SỐ LIỆU KIỂM THỬ THỰC TẾ

#### A. Backend Tight Suite (V16.39.7 - V16.39.11)
```powershell
$env:PYTHONPATH='backend;.'; backend\venv\Scripts\python.exe -m pytest -q (Resolve-Path backend/tests/test_v16_39_11*.py, backend/tests/test_v16_39_10*.py, backend/tests/test_v16_39_9*.py, backend/tests/test_v16_39_8*.py, backend/tests/test_v16_39_7*.py)
```
* **Kết quả:** **83 passed, 1 warning (Starlette deprecation), 21.93s**.
* **Exit code:** 0.

#### B. Backend Extended Suite (Rule 182)
```powershell
$env:PYTHONPATH='backend;.'; backend\venv\Scripts\python.exe -m pytest -q (Resolve-Path backend/tests/test_v16_39*.py, backend/tests/test_v16_38_adaptive_paper_readiness.py, backend/tests/test_v16_36_paper_tonight_production_regression.py, backend/tests/test_v16_35_rr_propagation_telegram_proximity.py, backend/tests/test_v16_34_cg01_runtime_ownership_consistency.py, backend/tests/test_v14_4_1_production_pipeline.py, backend/tests/test_v14_4_2_entry_regression.py, backend/tests/test_v16_3_cg01_pipeline.py, backend/tests/test_v16_3_cg01_contracts.py, backend/tests/test_v15_0_telegram_and_api.py, backend/tests/test_migration_runtime_upgrade.py, backend/tests/test_setup_quality_api_v14.py, backend/tests/test_setup_quality_service_v14.py, backend/tests/test_v16_15_scheduler_lifespan.py)
```
* **Kết quả:** **355 passed, 1 warning, 40.01s** (Baseline là 337 tests, tăng thêm 18 tests mới thuộc scope V16.39.11).
* **Exit code:** 0.

#### C. Frontend Test Suite
```powershell
npm test
```
* **Kết quả:** **30 test files, 293 passed (17 tests trong `candidateScore.test.ts`), 2.49s**.
* **Exit code:** 0.

#### D. Frontend Linter & Build
```powershell
npm run lint
npm run build
```
* **Kết quả:**
  - `oxlint`: 0 errors.
  - `tsc -b && vite build`: Hoàn thành trong **1.32s**, tạo bundle thành công không có lỗi typecheck.
* **Exit code:** 0.

---

### 4. HƯỚNG DẪN DÀNH CHO NGƯỜI DÙNG MỚI

1. **Ý nghĩa của điểm "78/100":**
   - Điểm số thể hiện mức độ đáp ứng các tiêu chuẩn kỹ thuật của setup theo chiến lược CG-01 (Liquidity Sweep, MSS, FVG, Displacement, v.v.).
   - **ĐIỂM 78/100 KHÔNG PHẢI XÁC SUẤT THẮNG 78%.** Thị trường luôn có rủi ro bất ngờ.
   - **ĐIỂM CAO KHÔNG ĐƯỢC VƯỢT QUA CÁC CHỐT CHẶN (GUARDS):** Dù điểm đạt 95/100 nhưng có tin tức Đỏ (News Blackout) hoặc Spread giãn thì hệ thống vẫn chặn không cho vào lệnh.
2. **Cấu trúc điểm:**
   - **Điểm gốc (Base Score):** Điểm thuần từ các tiêu chí kỹ thuật (ví dụ 83).
   - **Điều chỉnh bài học (Lesson Delta):** Điểm cộng hoặc trừ từ các quy tắc bài học đã được duyệt (ví dụ -5).
   - **Điểm cuối (Final Score):** $83 + (-5) = 78$.
   - **Ngưỡng tại lúc đánh giá (Threshold):** Mức điểm tối thiểu để được phép lên nòng (mặc định 70).
3. **Tính bất biến của điểm lịch sử:**
   - Lệnh sau khi ARM sẽ được khóa điểm (FROZEN) vĩnh viễn trong snapshot. Lệnh thắng hay lệnh thua sau đó **không** làm thay đổi điểm vào lệnh ban đầu. Lệnh cũ không được chấm lại bằng thuật toán mới.
4. **Cách đọc trạng thái báo cáo:**
   - `FOUND`: Run hợp lệ, dữ liệu khớp scope.
   - `RUN_SCOPE_MISMATCH`: Run được yêu cầu khác với chiến lược đang chạy, hệ thống cách ly an toàn và không hiển thị số liệu tài chính sai.
   - `PARTIAL`: Báo cáo có chứa một số lệnh chưa có dữ liệu PnL đóng hoàn chỉnh. Các số liệu hiển thị là "Phần đã xác nhận" trên tập mẫu đã biết, không phải kết quả tuyệt đối của toàn bộ lệnh.

---

### 5. HẠN CHẾ CÒN LẠI VÀ LƯU Ý DỮ LIỆU CŨ (HERITAGE DATA)

1. **Lệnh tạo trước bản vá V16.39.11:** Các lệnh đã fill trong quá khứ bị mất `quality_assessment` do bug cũ được hiển thị trung thực là `Chưa có điểm` (`LEGACY_NO_SCORE`). Hệ thống tuân thủ nguyên tắc không backfill dữ liệu giả tạo.
2. **Cửa sổ thời gian trong quá khứ:** Các báo cáo xem lại quá khứ không tự động suy diễn trạng thái mở/đóng nếu không có bản ghi lịch sử chuyển đổi tương ứng (`historical_state_available = False`).
