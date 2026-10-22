# AURUM V17.2 — FINAL ACCEPTANCE REPORT
**Báo Cáo Nghiệm Thu Toàn Diện V17.2: Đóng Request Races, Kiểm Soát Revision Provenance, Giới Hạn Cửa Sổ Thời Gian [start_ms, end_ms) và Ràng Buộc Run Scope Cho Paper Opportunity Monitor**

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Project:** `aurum-desk`
- **Baseline HEAD:** `135370a0bc1fb86ba2a1557af97dd9ee500c479e`
- **Baseline Commit Message:** `fix(v17.1): bind monitor query scope and hydrate canonical chart targets`
- **Thời điểm nghiệm thu:** 22/10/2026

---

## 1. Tóm Tắt 4 Lỗi Gốc & Kết Quả 5 Kiểm Thử Tái Hiện (Root Causes & Repro Probes)

| Mã Lỗi / Probe | Hiện Tượng Trước Bản Vá (Baseline Actual) | Bản Chất Lỗi Gốc (Root Cause) | Hành Vi Sau Bản Vá (V17.2 Expected & Verified) | Bằng Chứng Kiểm Thử |
| :--- | :--- | :--- | :--- | :--- |
| **Bug A / Probe E1** (Focus Muộn Đổi Chart) | Mở detail A -> Bấm Focus -> Bấm Back về list -> Phản hồi A đến muộn -> `onFocusSetupOnChart` vẫn bị gọi, drawer tự đóng và chart bị nhảy sang A. | `PaperOpportunityMonitorDrawer.handleBackToList` chỉ dọn state selection/error cục bộ mà không hủy in-flight focus request hoặc tăng generation token. | Khi bấm Back, `invalidateFocusRequest()` hủy AbortController và tăng `focusGenerationRef`. Phản hồi đến muộn bị loại bỏ, không gọi callback, không đóng drawer, không đổi chart. | `v17_2_monitor_repro.test.tsx::test_repro_e1_focus_response_delayed_after_back_does_not_focus_or_close` **PASS**;<br>`v17_2_opportunity_monitor.e2e.ts::V17.2 Gate 2` **PASS** |
| **Bug B / Probe E2 & E3** (Bỏ Qua Setup Revision) | Drawer không gửi `expected_revision`. Backend `build_monitor_chart_focus_context` chỉ so version khi client có truyền `expected_revision`. Funnel ghi revision 1, WatchSetup hiện version 2; request UI nhận `AVAILABLE` v2. | Frontend thiếu `expected_revision` trong params request; Backend không tự trích xuất revision đã ghi nhận trong funnel stage để tự bảo vệ provenance khi client bỏ sót param. | Frontend truyền `expected_revision: target.revision` lên API. Backend tự động trích xuất recorded revision từ funnel stages và trả về `STALE_TARGET` (`SETUP_REVISION_MISMATCH`) ngay cả khi client không truyền `expected_revision`. | `v17_2_monitor_repro.test.tsx::test_repro_e2e_drawer_sends_expected_revision_for_setup` **PASS**;<br>`test_v17_2_monitor_context.py::test_repro_e3_recorded_revision_mismatch_without_param` **PASS** |
| **Bug C / Probe E4** (Detail Timeline Lọt Ngoài Window) | Query khoảng `[1000, 1500)`, funnel ở 1200, milestone `CLOSED` ở 2000. Timeline trong detail hiển thị cả `CLOSED` ở 2000 ms. | `build_opportunity_monitor_detail` chỉ lọc milestone theo `run_id` và `order_id` mà không có điều kiện `timestamp_ms >= start_ms` và `< end_ms`. | Thêm điều kiện `timestamp_ms >= effective_query.start_ms` và `< effective_query.end_ms` vào query milestone. Milestone 999 ms (trước start), 1500 ms (tại mốc kết thúc nửa mở) và 2000 ms (CLOSED tương lai) bị loại bỏ 100%. | `test_v17_2_monitor_context.py::test_repro_e4_detail_milestone_and_trace_cutoff` **PASS** |
| **Bug D / Probe E5** (Overview Trộn Hai Run Khi Active Run Đổi) | `fetchOverviewData` gọi song song summary và list không có `run_id`. Khi active run đổi giữa 2 calls, summary nhận run A còn list nhận run B; hook publish cả hai tạo context lai. | Hook không pin run_id trước khi gọi list; không so khớp `query_key` giữa summary và list responses. | Hook chuyển sang luồng tuần tự (bootstrap summary -> pin run/window -> list explicit context -> xác minh `assertMonitorResponseContext` -> publish nguyên cặp). Nếu phát hiện mismatch thì từ chối publish. | `v17_2_monitor_repro.test.tsx::test_repro_e5_mixed_run_mismatch_rejected` **PASS** |

---

## 2. Bảng Tổng Hợp Kiểm Thử & Các Cổng Nghiệm Thu (Executive Quality Gates)

| Cổng Kiểm Tra (Gate) | Lệnh Thực Thi Thực Tế | Exit Code | Số Lượng / Chi Tiết | Trạng Thái | Ghi Chú & Kết Quả |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gate 1: Backend V17.2 Context & Invariants** | `& "backend\venv\Scripts\python.exe" -m pytest backend/tests/test_v17_2_monitor_context.py -v` | 0 | **7/7 tests passed** (0.53s) | **PASS** | Tái hiện E3, E4, active vs completed run, future order closure cutoff, single-resolve report, revision matrix, zero mutations. |
| **Gate 2: Backend V17.1 & V17.0 Full Monitor Suite** | `& "backend\venv\Scripts\python.exe" -m pytest backend/tests/test_v17_0_opportunity_monitor_api_readonly.py backend/tests/test_v17_0_opportunity_monitor_read_model.py backend/tests/test_v17_0_opportunity_monitor_metrics.py backend/tests/test_v17_1_monitor_scope_and_focus.py backend/tests/test_v17_2_monitor_context.py backend/tests/test_v16_39_21_e2e_financial_fixtures.py -v` | 0 | **39/39 tests passed** (1.28s) | **PASS** | Toàn bộ 6 suites monitor backend và financial fixtures đều pass 100%. 1 warning Starlette deprecation đã biết. |
| **Gate 3: Frontend Full Unit & Repro Tests** | `npm test` (trong `frontend`) | 0 | **44 test files / 478 tests passed** (4.00s) | **PASS** | Thêm tệp `v17_2_monitor_repro.test.tsx` (3 repro tests), bảo toàn 43 test files hiện có. |
| **Gate 4: Frontend Typecheck & Build** | `npm run build` (`tsc -b && vite build`) | 0 | 2024 modules transformed (1.75s) | **PASS** | Compile TypeScript hoàn toàn không có lỗi; bundle Vite production thành công. |
| **Gate 5: Frontend Linting** | `npm run lint` (`oxlint`) | 0 | 0 errors, 54 legacy warnings trên 139 files | **PASS** | 0 lỗi cú pháp hoặc typing mới do bản vá gây ra. |
| **Gate 6: Browser V17.2 E2E Acceptance** | `npm run test:e2e -- tests/e2e/v17_2_opportunity_monitor.e2e.ts` | 0 | **3/3 tests passed** (13.5s) | **PASS** | Kiểm chứng download file .md thật bằng `waitForEvent('download')`, delayed focus Back cancellation, và mobile viewport 390x844 không tràn ngang. |
| **Gate 7: Browser V17.0 E2E Regression** | `npm run test:e2e -- tests/e2e/v17_0_opportunity_monitor.e2e.ts` | 0 | **2/2 tests passed** (12.6s) | **PASS** | Desktop full workflow và mobile layout pass sau khi cập nhật fixture seed có revision/instance. |
| **Gate 8: Browser RR Acceptance Regression** | `npm run test:e2e -- tests/e2e/v16_39_21_rr_final_acceptance.e2e.ts` | 0 | **3/3 tests passed** (1.0m) | **PASS** | Bảo toàn 100% các luồng R:R LONG, SHORT, và mobile settings form đã nghiệm thu ở V16.39.21. |
| **Gate 9: Zero SQL Mutations Gate** | `SqlMutationWatcher` gắn trên SQLAlchemy engine trong unit và API tests | 0 | **0 mutations** | **PASS** | Xác nhận 0 INSERT/UPDATE/DELETE/REPLACE trên tất cả các route GET `/summary`, `/opportunities`, `/detail`, `/chart-target`, `/report.md`. |

---

## 3. Ma Trận Tài Nguyên & Ràng Buộc Ngữ Cảnh (Context Resources Matrix)

| Resource / Endpoint | Params Yêu Cầu Thực Tế | Nguồn Gốc `run_id` | Xử Lý `query_key` | Khoảng Thời Gian (`window`) | Cơ Chế Xác Thực Phản Hồi |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Summary (`GET /summary`)** | `symbol`, `scope`, `run_id?`, `model_id?`, `session_id?`, `start_ms?`, `end_ms?` | Trả về `coverage.run_id` và `query_scope.run_id` của active run (nếu không chỉ định) hoặc explicit run. | Tạo `query_key` canonical từ fingerprint không chứa pagination. | Nửa mở `[start_ms, end_ms)`; nếu không truyền `start_ms`, pin theo `run.started_at_ms`. | Client dùng `asResolvedMonitorQuery` kiểm chứng tính hợp lệ trước khi cho phép gọi list. |
| **List (`GET /opportunities`)** | `toMonitorRequestParams(boundQuery)` + `limit`, `cursor?` | Bắt buộc truyền `run_id` đã pin từ Summary bootstrap; không để server tự đoán run khác. | Server trả `query_key` trong envelope; so khớp bằng `assertMonitorResponseContext`. | Kế thừa chính xác `[start_ms, end_ms)` từ Summary đã bind. | Client kiểm tra `assertMonitorResponseContext(page, q, 'LIST')`. Mismatch bị từ chối commit. |
| **Detail (`GET /opportunities/{id}`)** | `toMonitorRequestParams(boundQuery)` | Kế thừa từ `boundQuery` của item đang chọn. | Server trả `query_key` khớp context; timeline chỉ chứa milestone trong window. | Cắt tỉa (cutoff) nghiêm ngặt: `timestamp_ms >= start_ms` và `< end_ms`. | Client kiểm tra identity `selectedOpportunityId` và query key trước khi render. Bỏ chọn lập tức hủy detail. |
| **Chart Target (`GET /chart-target`)** | `toMonitorRequestParams(boundQuery)`, `expected_target_id`, `expected_kind`, `expected_revision?` | Kế thừa từ `boundQuery`. | Kiểm tra `result.query_key === captured.query_key`. | Bounded theo scope của cơ hội. | Client chạy `validateMonitorChartFocusContext`: kiểm tra kind, id, symbol, revision, geometry và atomic plan. |
| **Report Export (`GET /report.md`)** | `toMonitorRequestParams(boundQuery)`, `limit=50` | Server resolve một lần duy nhất; summary và list dùng chung một run. | File Markdown có header chứa `run_id`, khoảng thời gian địa phương (+07:00), và filters. | Khoảng thời gian nửa mở đã pin. | Client kiểm tra token export, tải file blob an toàn và dọn dẹp URL blob sau khi hoàn tất. |

---

## 4. Ma Trận Chống Đua Request (Concurrency & Invalidation Matrix)

| Tình Huống Đua (Race Scenario) | Hành Động Kích Hoạt | Trạng Thái In-Flight Cũ | Cơ Chế Bảo Vệ | Hậu Quả Trên Chart / UI |
| :--- | :--- | :--- | :--- | :--- |
| **1. Focus rồi Back** | User bấm Focus A -> Bấm Back về list. | Request chart target của A đang chờ backend phản hồi. | `handleBackToList` gọi `invalidateFocusRequest()` (tăng `focusGenerationRef`, abort signal). Khi phản hồi về, `isCurrentFocusRequest` trả về false. | **Chart không đổi, Drawer không đóng**, Master list hiển thị bình thường. |
| **2. Focus rồi Chọn Row B** | User bấm Focus A -> Bấm Back -> Chọn Row B. | Request chart target của A hoàn tất sau khi B đã mở. | `handleSelectRow` gọi `invalidateFocusRequest()`. `captured.opportunityId !== currentId`. | **Không rò rỉ dữ liệu A sang B**, chart không focus A. |
| **3. Focus rồi Đóng Drawer** | User bấm Focus A -> Đóng drawer bằng nút X / Backdrop / Escape. | Request chart target của A hoàn tất sau khi drawer đóng. | `requestCloseMonitor` gọi `invalidateFocusRequest()`. Khi drawer đóng, `open === false`. | **Chart giữ nguyên**, không tự ý nhảy target. |
| **4. Unmount Khi Request Đang Chạy** | Drawer unmount khi focus hoặc export đang await. | Promise pending. | `useEffect` cleanup gọi `invalidateFocusRequest()`, hủy AbortController, không gọi `setState` sau unmount. | **Không có memory leak hay React warning**. |
| **5. Pagination Khi Bộ Lọc Đổi** | Page 2 của filter A đang tải -> User chuyển sang filter B. | Response page 2 của A trả về sau khi B bắt đầu. | `loadNextPage` kiểm tra `token === pageSequenceRef.current` và `q.query_key === currentQueryKey`. | **Dữ liệu A bị hủy bỏ, không append nhầm vào danh sách B**. |
| **6. Refresh Chu Kỳ 30s** | Interval refresh kích hoạt khi user đang xem trang. | Overview chu kỳ mới tải xong. | Publish nguyên cặp summary + page 1. Nếu `query_key` thay đổi, detail cũ được làm mới an toàn. | **Không trộn summary mới với danh sách cũ**. |

---

## 5. Bằng Chứng Tải File Markdown Thực Tế (Real Report Download Evidence)

Trích xuất từ kiểm thử Playwright thực tế (`v17_2_opportunity_monitor.e2e.ts::V17.2 Gate 1 & 4`):
- **Tên file tải về:** `paper-opportunity-report-XAUUSDT-2026-10-22-to-2026-10-22.md`
- **Thời gian lập báo cáo:** Múi giờ chuẩn `Asia/Ho_Chi_Minh` (+07:00).
- **Nội dung header xác nhận:**
  ```markdown
  # BÁO CÁO THEO DÕI CƠ HỘI PAPER & LÝ DO CHƯA VÀO LỆNH (V17.1)
  - **Mã sản phẩm:** `XAUUSDT`
  - **Phạm vi quan sát:** `CG01`
  - **Phiên quan sát (Run ID):** `run_e2e_v17_2`
  - **Thời điểm lập báo cáo:** 22/10/2026 ... (Asia/Ho_Chi_Minh)
  ```
- **Thông báo giới hạn danh sách:** Khi số lượng cơ hội vượt quá 50, báo cáo ghi rõ:
  `*(Đang hiển thị 50/N cơ hội; báo cáo giới hạn 50 dòng)*`.

---

## 6. Bằng Chứng Bất Biến Giao Dịch & Zero Writes (Read-Only Invariants)

1. **Zero SQL Mutations:**
   - Được bảo vệ bằng `db.autoflush = False` trên tất cả các endpoint.
   - Kiểm chứng bằng `SqlMutationWatcher` lắng nghe sự kiện `before_cursor_execute` trên SQLAlchemy engine:
     * `test_zero_sql_mutations_across_all_monitor_endpoints` thực thi 5 route GET và ghi nhận chính xác **0 câu lệnh** `INSERT`, `UPDATE`, `DELETE`, `REPLACE`, `CREATE`, `ALTER`, `DROP`.
2. **Không Can Thiệp Giao Dịch Thật:**
   - Hoàn toàn không sửa đổi hoặc gọi các service: `execution_coordinator`, `smc_engine`, `strategy_engine`, `cg01_detector`, `trade_manager`.
   - Ngưỡng R:R, tham số tin tức, quota phiên/ngày, sizing, leverage, Telegram bot outbox được giữ nguyên 100%. `state.outbox` duy trì = 0 sau toàn bộ các thao tác monitor.

---

## 7. Giới Hạn Kỹ Thuật Còn Lại (Known Technical Limitations)

1. **Cơ Chế UPSERT Của Bảng Funnel:**
   Bảng `OpportunityFunnelRecord` lưu snapshot theo cặp `(opportunity_id, stage)`. Do đó, khi một stage được cập nhật mốc mới, snapshot mới ghi đè bản ghi cũ; hệ thống không phải là một append-only log lưu từng tick giá microsecond. Báo cáo và giao diện luôn hiển thị limitation này minh bạch cho người dùng.
2. **Bản Ghi Lịch Sử Thiếu Revision (Legacy Records):**
   Đối với các cơ hội cũ trước V17.1 không có trường `revision` hoặc `setup_instance_id` trong payload, hệ thống cho phép xem các thông số đã ghi nhận nhưng sẽ chặn việc Focus lên biểu đồ với mã `SETUP_REVISION_UNVERIFIED` để bảo đảm không vô tình hiển thị sai phiên bản kế hoạch hiện tại.
