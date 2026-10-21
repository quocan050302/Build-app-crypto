# AURUM V16.39.12 — IMPLEMENTATION MAP

## 1. Overview
Phiên bản V16.39.12 sửa chữa chính xác cơ chế cache binding điểm chất lượng setup/order, chuẩn hóa hợp đồng factor canonical từ evaluator production sang API và UI, giải quyết triệt để vấn đề detail caller/drawer integration, xử lý cost snapshot lỗi không làm sập API (AttributeError), loại bỏ các giá trị suy đoán/bịa đặt mặc định, và chuẩn hóa trạng thái snapshot lịch sử (`HISTORICAL_AS_OF` vs `CURRENT_PERSISTED`).

---

## 2. File & Function Implementation Map

### 2.1 Backend: Score Display & Cache Binding
- **File**: `backend/services/order_quality_display_service.py`
  - `CandidateQualityDisplayKey`: Dataclass frozen chứa `(symbol, setup_id, setup_instance_id, candidate_key, direction)`.
  - `make_candidate_quality_display_key(symbol, setup_id, setup_instance_id, candidate_key, direction) -> CandidateQualityDisplayKey`: Helper chuẩn hóa identity, không ghép chuỗi hay split delimiter dấu `:`.
  - `parse_candidate_quality_assessment(raw_data) -> Tuple[Optional[Dict], str]`: Parser thuần túy, an toàn kiểu. Không tự bịa `base_score = final_score`, `threshold = 70.0`, `lesson_delta = 0.0`, hay `profile_version = "v16.39"`. Mọi trường thiếu metadata giữ `None`. Chuẩn hóa đầy đủ các trường canonical của `QualityScoreFactor` (`code`, `group`, `earned_points`, `max_points`, `status`, `explanation_vi`, `evidence_ids`, `known_at_ms`, `measurement`, `rule_version`) cùng các alias tương thích ngược (`factor_id`, `score`, `max_score`, `reason`).
  - `resolve_order_assessment(order) -> Tuple[Optional[Dict], str, str]`: Xử lý an toàn các kiểu JSON scalar, list, null (`"[]"`, `"null"`, `"5"`, `""`) trả về `INVALID` kèm `reason_code="MALFORMED_COST_SNAPSHOT_TYPE"` mà không ném `AttributeError`. Kiểm tra identity giữa `order.setup_instance_id` và `validated["candidate_key"]`.
  - `build_order_score_display(order) -> CandidateScoreDisplay`: Xây dựng projection summary cho list/card, thuần đọc, không ghi DB.
  - `build_order_score_detail(order) -> CandidateScoreDetail`: Xây dựng projection chi tiết gồm danh sách canonical factors, group scores, blockers, missing data.
  - *Caller*: `backend/main.py` (`PaperOrderResponse.candidate_score_summary`, `/api/v1/orders/{order_id}/quality-score`), `services/scalp_watcher_orchestrator.py`.
  - *Tests*: `backend/tests/test_v16_39_12_score_binding_contract.py`.

- **File**: `backend/services/scalp_watcher_orchestrator.py`
  - `record_candidate_quality_display(assessment, symbol, setup_id, setup_instance_id, candidate_key, direction) -> Optional[CandidateScoreDisplay]`: Lưu trữ display cache sử dụng `CandidateQualityDisplayKey`. Xác nhận `assessment.candidate_key` khớp caller binding; không evict nhầm khi key đã tồn tại; lock thread-safe.
  - `get_candidate_quality_display(symbol, setup_id, setup_instance_id, candidate_key, direction) -> Optional[CandidateScoreDisplay]`: Truy vấn exact-only matching. Loại bỏ hoàn toàn fallback matching sang instance khác cùng setup_id/direction. Khi query instance 'NEW', tuyệt đối không trả về score 78 của instance 'old'.
  - *Caller*: `backend/main.py:get_upcoming_setups`, `scalp_watcher_orchestrator.py:_arm_paper_candidate`.
  - *Tests*: `backend/tests/test_v16_39_12_score_binding_contract.py`.

### 2.2 Backend: Historical State View & Financial Snapshot
- **File**: `backend/services/paper_runtime_snapshot_service.py`
  - `compute_position_state_view(orders, requested_as_of_ms, observed_at_ms, view_mode) -> PositionStateView`: Hàm thuần túy phân biệt rõ hai chế độ:
    - `CURRENT_PERSISTED`: Đếm trạng thái các vị thế hiện có trong DB (loại trừ các order tạo trong tương lai `created_at >= requested_as_of_ms`). Trả về `historical_state_available=True`.
    - `HISTORICAL_AS_OF`: Khi cửa sổ kết thúc trong quá khứ (`requested_as_of_ms < observed_at_ms`), trả về `open_positions_count=None`, `armed_orders_count=None`, `historical_state_available=False`, `reason="HISTORICAL_TRANSITIONS_UNAVAILABLE"`.
  - `compute_financial_metrics(...)`: Tự động xác định view mode, tính opening equity chính xác, phân tách accounting đóng trong window khỏi trạng thái vị thế mở lịch sử.
  - `build_paper_runtime_snapshot(...)`: Tích hợp các trường lịch sử vào `metrics_payload`: `state_view`, `state_as_of_ms`, `state_observed_at_ms`, `historical_state_available`, `historical_state_reason`.
  - `render_markdown(...)`: Hiển thị `"Chưa đủ lịch sử xác minh"` khi `open_positions_count is None` thay vì hiển thị `"0"` hoặc `"None"`.
  - *Caller*: `GET /api/v1/paper/runtime-snapshot`, CLI `tools/paper_runtime_snapshot.py`.
  - *Tests*: `backend/tests/test_v16_39_12_historical_snapshot_states.py`.

### 2.3 Backend: Test Launcher Route
- **File**: `backend/tests/e2e_server.py`
  - `@app.post("/__e2e/seed_order")`: Route test-only hỗ trợ seed order kèm `cost_snapshot` chứa `quality_assessment` vào DB tạm cô lập. Tự động liên kết `setup_instance_id` với `candidate_key` để đảm bảo identity verification nhất quán.
  - *Caller*: `frontend/tests/e2e/v16_39_11_candidate_score.e2e.ts`.

---

### 2.4 Frontend: Types & Utilities
- **File**: `frontend/src/types/candidateScore.ts`
  - Cập nhật interface `CandidateScoreFactor` với các trường canonical: `code`, `group`, `earned_points`, `max_points`, `status`, `explanation_vi`, `evidence_ids`, `known_at_ms`, `measurement`, `rule_version`.
  - Cập nhật `CandidateScoreDetail` bổ sung `order_id?: string | null` và `group_scores?: Record<string, Record<string, number>>`.
- **File**: `frontend/src/utils/candidateScore.ts`
  - `translateDecision(decision)`: Dịch chính xác `ELIGIBLE_BY_SCORE` -> "Đạt ngưỡng điểm", `BELOW_THRESHOLD` -> "Dưới ngưỡng điểm", `BLOCKED_REQUIRED` -> "Thiếu điều kiện bắt buộc", `BLOCKED_GUARD` -> "Bị chặn bởi bộ lọc bảo vệ".
  - `formatCandidateScore(rawSummary)`: Xử lý thứ tự ưu tiên (style precedence) cho blockers và insufficient data trước threshold số học.
  - `getSelectedScoreTarget(binding, selectedIntent) -> ScoreTarget | null`: Phân biệt rõ ràng mục tiêu điểm giữa `ORDER` và `SETUP` dựa trên `binding.source` và `selectedIntent.source`. Ngăn chặn việc gửi request `/orders/{setupId}/quality-score` nhầm setup ID sang endpoint orders.
  - *Tests*: `frontend/src/candidateScore.test.ts`.

### 2.5 Frontend: Hook & Components
- **File**: `frontend/src/hooks/useCandidateScoreDetail.ts`
  - Hook quản lý vòng đời tải chi tiết điểm cho một order duy nhất (`orderId`, `expectedAssessmentId`, `enabled`).
  - Sử dụng generation counter (`reqGenRef`) và `AbortController` để ngăn race condition (khi chọn B, response chậm của A bị bỏ qua).
  - Xóa ngay dữ liệu của A khi chuyển sang B (Rule 078).
  - Xác minh `returnedOrderId === requestedId` và `returnedDetail.assessment_id === expectedAssessmentId` (Rules 079, 138, 139).
  - *Tests*: `frontend/src/__tests__/useCandidateScoreDetail.test.ts`.
- **File**: `frontend/src/components/CandidateScoreDetails.tsx`
  - Hiển thị canonical factors với điểm số `{+earnedVal/maxVal}`, icon trạng thái (`FULL`, `PARTIAL`, `ZERO`, `NOT_APPLICABLE`), và giải thích tiếng Việt.
  - Hiển thị `'—'` trung thực khi thiếu metadata (không tự điền 70 hay 0).
  - Hiển thị lỗi inline kèm nút "Thử lại" (Rule 082) mà không làm sập giao diện.
- **File**: `frontend/src/JournalTab.tsx`
  - Nối caller thực tế bằng cách gọi hook `useCandidateScoreDetail` cho `selectedTrade.id`.
  - Truyền `detail={selectedScoreDetail}` vào `<CandidateScoreDetails>`.
- **File**: `frontend/src/components/TradeDetailsDrawer.tsx`
  - Tích hợp `getSelectedScoreTarget` để nhận diện chính xác `ORDER` vs `SETUP`.
  - Tái sử dụng `(selectedRawBundle as any)?.candidate_score_detail` khi có sẵn; nếu thiếu và target là `ORDER`, kích hoạt hook fallback.
- **File**: `frontend/tests/e2e/v16_39_11_candidate_score.e2e.ts`
  - Xác thực seed order trả về HTTP 200 (không bỏ qua lỗi).
  - Đối chiếu điểm 78/100 (Order A) và 64/100 (Order B) qua API endpoint production trước khi mở browser.
  - Xác thực badge `78/100` hiển thị chính xác trên giao diện Desktop và Mobile (390x844).
