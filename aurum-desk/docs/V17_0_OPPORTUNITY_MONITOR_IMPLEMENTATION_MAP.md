# AURUM V17.0 — BẢN ĐỒ TRIỂN KHAI THEO DÕI CƠ HỘI PAPER & LÝ DO CHƯA VÀO LỆNH
*(V17.0 Paper Opportunity & Non-Entry Reason Monitor — Implementation Map)*

## 1. Tổng Quan Phạm Vi & Nguyên Tắc Khóa
- **Mục tiêu:** Cung cấp khả năng quan sát minh bạch, trung thực và tối giản cho người dùng mới:
  - Bot đang theo dõi vùng nào, đang chờ điều kiện gì hoặc bị chặn vì sao?
  - Cơ hội đã đi qua các mốc nào, có ARM/khớp không, kết quả sau phí nếu đã đóng?
  - Ít lệnh là do chưa có setup, chờ trigger, policy/guard chặn hay vấn đề dữ liệu/hệ thống?
- **Nguyên tắc khóa kiến trúc (Bảo toàn V16.39.21):**
  - **Hoàn toàn Read-Only:** Không sửa thuật toán CG01/SMC, bias, trigger, POI ranking, score threshold, minimum R:R, sizing, phí/trượt giá, phiên giao dịch, news/lessons/account guards.
  - **Không tự động hóa thừa:** Không tự động ARM, không bật AutoPaper, không start research run, không phát tín hiệu Telegram outbox, không tự động sinh bài học (lesson).
  - **UI tối giản:** Màn hình giao dịch tái sử dụng `CG01DiagnosticCard` hiển thị câu trạng thái ngắn gọn + CTA "Xem cơ hội". Toàn bộ chi tiết kỹ thuật/timeline mở theo nhu cầu trong duy nhất một Drawer (Master/Detail), không mở popup lồng popup, không chèn bảng/logs vào chart canvas.
  - **Tính trung thực dữ liệu:** Bảng `opportunity_funnel_records` lưu theo cơ chế UPSERT `(opportunity_id, stage)`. Timeline chỉ biểu diễn **các mốc đã ghi nhận còn lưu**, không giả mạo lịch sử từng tick, không gắn lý do cuối cùng vào thời điểm tạo ban đầu.

---

## 2. Bản Đồ Hàm & Module Backend (`backend/`)

| File / Module | Hàm / Thành Phần | Vai Trò & Cơ Chế | Tái Sử Dụng / Mới | Caller / Endpoint | Bộ Test Kiểm Thử |
|---|---|---|---|---|---|
| `backend/services/paper_opportunity_monitor_service.py` | `make_monitor_query_context` | Khóa `now_ms` cố định cho toàn bộ response, validate symbol, scope, half-open interval (`start_ms <= ts < end_ms`), giới hạn range <= 31 ngày, decode cursor an toàn. | MỚI | Monitor endpoints trong `main.py` | `test_v17_0_opportunity_monitor_read_model.py` |
| `backend/services/paper_opportunity_monitor_service.py` | `normalize_monitor_stage_row` | Parse an toàn `payload_json`, `metrics_json`, `reason_codes_json`, fallback giá trị mặc định, bảo vệ chống JSON hỏng, phân tách rõ `created_at` (lần đầu ghi nhận mốc) và `updated_at` (cập nhật snapshot gần nhất). | MỚI (pure) | `list_monitor_opportunities`, `build_opportunity_monitor_detail` | `test_v17_0_opportunity_monitor_read_model.py` |
| `backend/services/paper_opportunity_monitor_service.py` | `link_monitor_order_evidence` | Batch load liên kết `PaperResearchOrderLink`, `PaperOrder`, và `PaperResearchOrderMilestone`. Kiểm tra scope/symbol của order trước khi trả `MonitorChartTarget`. Link dangling/mismatch trả `UNVERIFIABLE`. | MỚI | `group_monitor_opportunities`, `build_opportunity_monitor_detail` | `test_v17_0_opportunity_monitor_read_model.py` |
| `backend/services/paper_opportunity_monitor_service.py` | `group_monitor_opportunities` | Gom nhóm canonical opportunities theo `(run_id, opportunity_id)`. Xác định last-observed stage và lý do snapshot gần nhất. Không dùng max-rank để bỏ qua invalidation. | MỚI (pure) | `list_monitor_opportunities` | `test_v17_0_opportunity_monitor_read_model.py` |
| `backend/services/paper_opportunity_monitor_service.py` | `list_monitor_opportunities` | Truy vấn phân trang SQL chuẩn, filter theo run/symbol/time, batch load stage rows & order links. Trả về `MonitorListPage` kèm pagination metadata. | MỚI (read-only) | `GET /api/v1/paper/opportunity-monitor/opportunities` | `test_v17_0_opportunity_monitor_api_readonly.py` |
| `backend/services/paper_opportunity_monitor_service.py` | `build_opportunity_monitor_detail` | Đọc toàn bộ milestones đã ghi nhận, plan giá dự kiến, kết quả order đã cam kết (committed), độ trễ telemetry, phân loại missed classifier nếu đủ dữ liệu. | MỚI (read-only) | `GET /api/v1/paper/opportunity-monitor/opportunities/{opportunity_id}` | `test_v17_0_opportunity_monitor_api_readonly.py` |
| `backend/services/paper_opportunity_monitor_service.py` | `build_opportunity_monitor_summary` | Tổng hợp thống kê funnel (tổng cơ hội duy nhất, các mốc chạm vùng, trigger, blocked, armed, filled). Tái sử dụng `paper_runtime_snapshot_service.py` cho số liệu tài chính đã xác minh. | MỚI (read-only) | `GET /api/v1/paper/opportunity-monitor/summary` | `test_v17_0_opportunity_monitor_metrics.py`, `test_v17_0_opportunity_monitor_api_readonly.py` |
| `backend/services/paper_opportunity_monitor_service.py` | `render_opportunity_monitor_markdown` | Kết xuất báo cáo Markdown tổng kết phiên, allowlist trường dữ liệu an toàn, ghi nhận rõ hạn chế dữ liệu (coarse UPSERT, small sample size). | MỚI (pure) | `GET /api/v1/paper/opportunity-monitor/report.md` | `test_v17_0_opportunity_monitor_api_readonly.py` |
| `backend/main.py` | 4 Additive Endpoints | `GET /api/v1/paper/opportunity-monitor/summary`<br>`GET /api/v1/paper/opportunity-monitor/opportunities`<br>`GET /api/v1/paper/opportunity-monitor/opportunities/{opportunity_id}`<br>`GET /api/v1/paper/opportunity-monitor/report.md`<br>Tất cả chạy với `db.autoflush=False`, zero writes. | MỚI | Frontend API Client | `test_v17_0_opportunity_monitor_api_readonly.py` |
| `backend/services/paper_runtime_snapshot_service.py` | `find_existing_observation_run`, `build_paper_runtime_snapshot` | Tái sử dụng toàn bộ logic tìm kiếm run và tính toán số liệu tài chính đã xác nhận (verified accounting). Không viết lại công thức PnL thứ hai. | TÁI SỬ DỤNG | `build_opportunity_monitor_summary` | `test_v16_39_9_paper_runtime_snapshot.py` |
| `backend/services/cg01_missed_opportunity_audit_service.py` | `CG01MissedOpportunityAuditService.classify_opportunity` | Phân loại cơ hội bị lỡ: POLICY_BLOCKED, TECHNICAL_MISS, MARKET_NON_FILL. Không suy diễn sai khi thiếu tape/coverage. | TÁI SỬ DỤNG | `build_opportunity_monitor_detail` | `test_v16_14_missed_opportunity_funnel.py` |

---

## 3. Bản Đồ Thành Phần & Module Frontend (`frontend/`)

| File / Component | Thành Phần / Hook | Vai Trò & Cơ Chế | Tái Sử Dụng / Mới | Caller / Nơi Sử Dụng | Bộ Test Kiểm Thử |
|---|---|---|---|---|---|
| `frontend/src/types/paperOpportunityMonitor.ts` | Type Definitions | Khai báo các interface hợp đồng DTO: `OpportunityMonitorSummary`, `OpportunityMonitorListItem`, `OpportunityMonitorDetail`, `ObservedMilestone`, `MonitorChartTarget`, `MonitorFilterState`. | MỚI | Toàn bộ monitor pipeline | `tsc -b` (Typecheck), unit tests |
| `frontend/src/utils/paperOpportunityText.ts` | `deriveBotWaitingSummary` | Hàm thuần túy chuyển đổi `CG01DisplayState` sang headline, lý do ngắn gọn và sắc thái hiển thị cho người mới bắt đầu. Ưu tiên cảnh báo mất kết nối và dữ liệu chậm. | MỚI (pure) | `CG01DiagnosticCard.tsx` | `paperOpportunityText.test.ts` (10 tests) |
| `frontend/src/api/client.ts` | Client API Methods | 4 phương thức typed kết nối backend: `getOpportunityMonitorSummary`, `getOpportunityMonitorOpportunities`, `getOpportunityMonitorDetail`, `getOpportunityMonitorReportMarkdown`. Hỗ trợ `AbortSignal`. | MỚI | `usePaperOpportunityMonitor.ts`, Drawer | `usePaperOpportunityMonitor.test.tsx` |
| `frontend/src/hooks/usePaperOpportunityMonitor.ts` | `usePaperOpportunityMonitor` | Hook quản trị vòng đời fetch: chỉ gọi API khi `open=true`, quản lý `generationId` chống race-condition, tự động abort khi đóng hoặc đổi filter, giới hạn chu kỳ làm mới tối đa 30s chỉ khi tab active và xem ngày hiện hành (CURRENT). | MỚI | `PaperOpportunityMonitorDrawer.tsx` | `usePaperOpportunityMonitor.test.tsx` (3 tests) |
| `frontend/src/components/OpportunityTimeline.tsx` | `OpportunityTimeline` | Component biểu diễn trực quan các mốc đã ghi nhận: thời điểm snapshot, nhãn tiếng Việt, lý do và ID bằng chứng. Phân biệt rõ mốc đã ghi nhận và mốc giả định. | MỚI | `PaperOpportunityMonitorDrawer.tsx` | `PaperOpportunityMonitorDrawer.test.tsx` |
| `frontend/src/components/PaperOpportunityMonitorDrawer.tsx` | `PaperOpportunityMonitorDrawer` | Drawer quan sát duy nhất (560-640px desktop, full width/height 390x844 mobile). Giao diện Master/Detail, bộ lọc tối giản, tải báo cáo markdown, focus chart an toàn. | MỚI | `App.tsx` | `PaperOpportunityMonitorDrawer.test.tsx` (6 tests), `v17_0_opportunity_monitor.e2e.ts` (2 tests) |
| `frontend/src/components/CG01DiagnosticCard.tsx` | Tích hợp CTA & Status | Nhận prop `onOpenOpportunityMonitor`. Hiển thị trạng thái tóm tắt 1 dòng + nút bấm "Xem cơ hội". Không làm tăng chiều cao sidebar, không render timeline khi chưa mở. | CẬP NHẬT | `QuickDecisionSidebar.tsx` | `v17_0_opportunity_monitor.e2e.ts` |
| `frontend/src/components/QuickDecisionSidebar.tsx` | Props Forwarding | Chuyển tiếp callback `onOpenOpportunityMonitor` từ `App` tới `CG01DiagnosticCard`. Duy trì toàn bộ invariant R:R và selection cũ. | CẬP NHẬT | `App.tsx` | `v16_39_21_rr_final_acceptance.e2e.ts` |
| `frontend/src/App.tsx` | State Owner & Mounting | Khai báo `isOpportunityMonitorOpen`, truyền callback vào `QuickDecisionSidebar`, mount `<PaperOpportunityMonitorDrawer>` sau `<TradeDetailsDrawer>`. | CẬP NHẬT | Top-level render tree | `npm test`, E2E suites |

---

## 4. Bảng Đối Chiếu Các Cổng Kiểm Nghiệm (Verification Gates)

| Gate | Mô Tả | Lệnh Thực Thi | Kết Quả Đạt Được |
|---|---|---|---|
| **Gate 1: Backend Pure Models & Helpers** | Kiểm tra normalize, group, DTO parsing, UPSERT handling. | `pytest backend/tests/test_v17_0_opportunity_monitor_read_model.py` | **11 passed in 0.32s** |
| **Gate 2: Backend Financial & Latency Metrics** | Kiểm tra unique opportunity counts, accounting reuse, sample count, null handling. | `pytest backend/tests/test_v17_0_opportunity_monitor_metrics.py` | **5 passed in 0.12s** |
| **Gate 3: Backend Zero-Mutation Read-Only Contract** | Bắt sự kiện SQLAlchemy, khẳng định 0 INSERT/UPDATE/DELETE/DDL, `autoflush=False`. | `pytest backend/tests/test_v17_0_opportunity_monitor_api_readonly.py` | **6 passed in 0.18s** |
| **Gate 4: Full Backend Regression** | Toàn bộ suite backend bao gồm R:R invariants, funnel, telemetry, snapshot. | `pytest backend/tests/` | **439 passed in 36.89s** |
| **Gate 5: Frontend Unit & Text Mapping** | Kiểm tra deriveBotWaitingSummary, formatters, trạng thái stale/disconnected. | `npm test -- src/__tests__/paperOpportunityText.test.ts` | **10 passed in 250ms** |
| **Gate 6: Frontend Hook Lifecycle & Race** | Kiểm tra hook abort on close, generation counter, không fetch khi closed. | `npm test -- src/__tests__/usePaperOpportunityMonitor.test.tsx` | **3 passed in 180ms** |
| **Gate 7: Frontend Component & Master-Detail** | Kiểm tra drawer open/close, row click detail, back button, Escape key, focus chart callback. | `npm test -- src/__tests__/PaperOpportunityMonitorDrawer.test.tsx` | **6 passed in 268ms** |
| **Gate 8: Full Frontend Regression** | Toàn bộ 43 file test frontend không có hồi quy. | `npm test` | **466 passed in 4.92s** |
| **Gate 9: TypeScript Typecheck & Production Build** | Không `ts-ignore` bừa bãi, bundle client Vite hoàn chỉnh. | `npm run build` | **tsc -b && vite build: Code 0** |
| **Gate 10: Linter Integrity** | Kiểm tra chuẩn code toàn bộ repo frontend. | `npm run lint` | **0 errors, 56 warnings baseline** |
| **Gate 11: Playwright Desktop & Mobile E2E** | Luồng người dùng hoàn chỉnh: Collapsed card -> Drawer -> Master/Detail -> Focus Chart -> Report -> Escape & Mobile layout. | `npm run test:e2e -- tests/e2e/v17_0_opportunity_monitor.e2e.ts` | **2 passed in 13.1s** |
| **Gate 12: Playwright R:R Regression E2E** | Bảo toàn tuyệt đối luồng R:R V16.39.21 (LONG, SHORT, Frozen snapshot, Mobile). | `npm run test:e2e -- tests/e2e/v16_39_21_rr_final_acceptance.e2e.ts` | **3 passed in 1.1m** |
