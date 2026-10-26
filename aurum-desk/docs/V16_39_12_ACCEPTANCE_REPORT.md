# BÁO CÁO NGHIỆM THU AURUM V16.39.12
**Triển khai thực tế — Sửa chính xác Binding Điểm, Factor Breakdown, Drawer Integration, Dữ liệu lỗi và Trạng thái Lịch sử**

- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Baseline Git SHA**: `d8cd87cc581935ecd57e94f33e0ae72f04a36602` (tiếp sau commit `5099c7a`)
- **Trạng thái**: **ĐẠT (PASSED)** — 100% Code thật, Test thật, Caller thật, E2E Browser thật.

---

## 1. Kết Quả Kiểm Tra Các Bug Đã Tái Hiện

| Bug # | Vấn Đề Gốc (Baseline) | Giải Pháp V16.39.12 | Kết Quả Nghiệm Thu | Bằng Chứng Kiểm Thử |
|---|---|---|---|---|
| **1** | `get_candidate_quality_display()` yêu cầu `setup_instance_id='NEW'` nhưng fallback trả về score 78 của `'old'`. Key chuỗi split `:` bị nhầm khi key chứa `:`. | Thay key chuỗi bằng `CandidateQualityDisplayKey` (dataclass frozen) gồm `(symbol, setup_id, setup_instance_id, candidate_key, direction)`. Getter chuyển thành exact-only matching, loại bỏ hoàn toàn fallback matching chéo instance. | **PASSED** | `test_cache_exact_instance_binding_no_cross_instance_fallback`<br>`test_candidate_key_with_colons_lookup` |
| **2** | `QualityScoreFactor.to_dict()` production trả `code/group/earned_points/max_points/status/explanation_vi...` nhưng adapter lại map sang `factor_id/name/reason/score/max_score`. Mất giải thích tiếng Việt và sai điểm thành phần. | Đồng bộ schema canonical xuyên suốt: `QualityScoreFactor` -> `CandidateScoreFactor` DTO -> UI component. Hiển thị điểm `+earned/max`, icon trạng thái (`FULL/PARTIAL/ZERO/NOT_APPLICABLE`), và giữ nguyên `explanation_vi`. | **PASSED** | `test_production_factors_preservation_and_breakdown`<br>`src/candidateScore.test.ts` (renders canonical factors FVG 18/20) |
| **3** | `JournalTab` truyền summary và orderId vào `CandidateScoreDetails` nhưng không fetch detail. `getOrderQualityScore()` chưa có caller thực tế. `TradeDetailsDrawer` dùng nhầm `setup_id` của setup làm order ID. | Tạo hook `useCandidateScoreDetail.ts` quản lý race condition, AbortController và generation check. Tạo resolver `getSelectedScoreTarget` phân biệt rõ `ORDER` vs `SETUP`. Nối caller thực tế trong `JournalTab` và `TradeDetailsDrawer`. | **PASSED** | `src/__tests__/useCandidateScoreDetail.test.ts` (6/6 tests pass)<br>`src/candidateScore.test.ts` (Target Resolver 4/4 tests pass) |
| **4** | `build_order_score_display()` gặp `cost_snapshot` scalar/list/null (`"[]"`, `"null"`, `"5"`) bị `AttributeError` do dùng `.get` trên non-dict. | Thêm helper `resolve_order_assessment(order)` kiểm tra kiểu dữ liệu sau `json.loads`. Nếu không phải dict, trả về trạng thái `INVALID` kèm `reason_code="MALFORMED_COST_SNAPSHOT_TYPE"` an toàn không 500. | **PASSED** | `test_malformed_cost_snapshot_matrix_no_exceptions`<br>`test_paper_order_response_pydantic_serialization` |
| **5** | Snapshot thiếu metadata tự gán `base_score=final_score`, `threshold=70.0`, `lesson_delta=0.0`, `profile_version="v16.39"`. | Parser giữ nguyên `None` cho mọi trường thiếu metadata. UI hiển thị `'—'` ("Chưa lưu") trung thực thay vì dựng số liệu giả. | **PASSED** | `test_no_fabricated_metadata_defaults`<br>`src/candidateScore.test.ts` |
| **6** | `compute_financial_metrics()` dùng state hiện tại cho cửa sổ quá khứ. `state_as_of_ms` bị bỏ qua; `historical_state_available` thiếu trong payload. | Triển khai `compute_position_state_view` phân biệt `CURRENT_PERSISTED` và `HISTORICAL_AS_OF`. Khi cửa sổ kết thúc trong quá khứ (`end < now_ms`), trả về `open_positions_count=None`, `historical_state_available=False`, lý do `HISTORICAL_TRANSITIONS_UNAVAILABLE`. Markdown hiển thị `"Chưa đủ lịch sử xác minh"`. | **PASSED** | `test_v16_39_12_historical_snapshot_states.py` (2/2 tests pass) |
| **7** | E2E V16.39.11 gọi `/__e2e/seed_order` chưa có trong test launcher, `.catch(() => {})` bỏ qua lỗi, chỉ assert badge bất kỳ visible. | Bổ sung `@app.post("/__e2e/seed_order")` trong `backend/tests/e2e_server.py`. E2E test assert response status 200, đối chiếu điểm qua endpoint production, assert chính xác `78/100` của Order A và `64/100` của Order B. | **PASSED** | `frontend/tests/e2e/v16_39_11_candidate_score.e2e.ts` (2/2 tests pass trên Google Chrome thật) |

---

## 2. Bảng Hợp Đồng Canonical (Factor & Decision Mapping)

### 2.1 Bảng Mapping Factor
| Evaluator Production (`QualityScoreFactor`) | API Response (`detail.factors[]`) | TypeScript DTO (`CandidateScoreFactor`) | UI Component (`CandidateScoreDetails`) |
|---|---|---|---|
| `code: str` | `code: str` | `code: string` | Mã tiêu chí (VD: `FVG_ALIGNMENT`) |
| `group: str` | `group: str` | `group?: string` | Nhóm tiêu chí (VD: `STRUCTURE`) |
| `earned_points: float` | `earned_points: float` | `earned_points?: number` | Điểm đạt được: `+{earned_points}` |
| `max_points: float` | `max_points: float` | `max_points?: number` | Điểm tối đa: `/{max_points}` |
| `status: str` | `status: str` | `status?: string` | Icon: `FULL` (xanh lá), `PARTIAL` (vàng), `ZERO` (xám) |
| `explanation_vi: str` | `explanation_vi: str` | `explanation_vi?: string` | Giải thích chi tiết tiếng Việt |
| `evidence_ids: list[str]`| `evidence_ids: list[str]` | `evidence_ids?: string[]` | Danh sách định danh bằng chứng |
| `known_at_ms: int` | `known_at_ms: int` | `known_at_ms?: number` | Thời điểm biết bằng chứng |
| `measurement: dict` | `measurement: dict` | `measurement?: any` | Đo lường số liệu cụ thể |
| `rule_version: str` | `rule_version: str` | `rule_version?: string` | Phiên bản quy tắc đánh giá |

### 2.2 Bảng Mapping Quyết Định (Decision)
| Decision Code Backend | Nhãn Hiển Thị UI Tiếng Việt | Precedence Styling |
|---|---|---|
| `ELIGIBLE_BY_SCORE` | **Đạt ngưỡng điểm** | Xanh ngọc (`bg-cyan-950 text-cyan-300`) |
| `BELOW_THRESHOLD` | **Dưới ngưỡng điểm** | Hổ phách (`bg-amber-950 text-amber-300`) |
| `BLOCKED_REQUIRED` | **Thiếu điều kiện bắt buộc** | Đỏ cảnh báo (`bg-rose-950 text-rose-300`) — Ưu tiên cao hơn điểm số |
| `BLOCKED_GUARD` | **Bị chặn bởi bộ lọc bảo vệ** | Đỏ cảnh báo (`bg-rose-950 text-rose-300`) — Ưu tiên cao hơn điểm số |
| `INSUFFICIENT_DATA` | **Dữ liệu chưa đầy đủ** | Hổ phách (`bg-amber-950 text-amber-300`) |
| `ERROR` | **Lỗi đánh giá** | Hổ phách (`bg-amber-950 text-amber-300`) |

---

## 3. Tổng Hợp Kết Quả Kiểm Thử Toàn Diện

### 3.1 Backend Test Suites
- **Bộ kiểm tra V16.39.12 mới**:
  - `backend/tests/test_v16_39_12_score_binding_contract.py`: **5 passed**
  - `backend/tests/test_v16_39_12_historical_snapshot_states.py`: **2 passed**
  - Tổng: **7 passed** trong 0.24s (Exit Code: 0).
- **Bộ kiểm tra toàn bộ V16.39**:
  - `backend/tests/test_v16_39*.py`: **190 passed, 1 deprecation warning** trong 4.63s (Exit Code: 0).
- **Bộ kiểm tra hồi quy mở rộng (Extended Regression Suite)**:
  - Command: `PYTHONPATH=backend:. python3 -m pytest -q backend/tests/test_v16_39*.py backend/tests/test_v16_38_adaptive_paper_readiness.py backend/tests/test_v16_36_paper_tonight_production_regression.py backend/tests/test_v16_35_rr_propagation_telegram_proximity.py backend/tests/test_v16_34_cg01_runtime_ownership_consistency.py backend/tests/test_v14_4_1_production_pipeline.py backend/tests/test_v14_4_2_entry_regression.py backend/tests/test_v16_3_cg01_pipeline.py backend/tests/test_v16_3_cg01_contracts.py backend/tests/test_v15_0_telegram_and_api.py backend/tests/test_migration_runtime_upgrade.py backend/tests/test_setup_quality_api_v14.py backend/tests/test_setup_quality_service_v14.py backend/tests/test_v16_15_scheduler_lifespan.py`
  - Kết quả: **362 passed, 1 warning** trong 10.12s (Exit Code: 0).

### 3.2 Frontend Unit & Integration Tests (Vitest)
- Command: `npm --prefix frontend test`
- Kết quả: **310 passed trong 31 test files** (Exit Code: 0).
  - `src/__tests__/useCandidateScoreDetail.test.ts`: **6/6 passed**
  - `src/candidateScore.test.ts`: **23/23 passed**
  - Tất cả 29 files hồi quy khác: **281/281 passed**

### 3.3 Frontend Lint & Build
- `npm --prefix frontend run lint`: **0 errors**, 37 warnings (đã tồn tại từ baseline).
- `npm --prefix frontend run build`: **Thành công** (`tsc -b && vite build` tạo bundle chuẩn trong 1.00s).

### 3.4 Playwright Browser E2E Tests
- Command: `npm --prefix frontend run test:e2e -- tests/e2e/v16_39_11_candidate_score.e2e.ts`
- Trình duyệt thực thi: Google Chrome channel thật (`1512x857` & `390x844`).
- Kết quả: **2 passed** trong 7.4s (Exit Code: 0).
  - `E12-1: Seed distinct orders and verify exact 78/100 and 64/100 scores via API and UI`: **PASSED** (1.2s)
  - `E12-2: Mobile viewport (390x844) displays compact badges without layout overflow`: **PASSED** (948ms)

---

## 4. Hướng Dẫn Sử Dụng Đơn Giản Cho Người Dùng

1. **Ý nghĩa điểm số 78/100**:
   - Con số `78/100` là điểm phản ánh mức độ đáp ứng các tiêu chuẩn kỹ thuật CG-01 của setup tại thời điểm đánh giá (FVG, thanh khoản, đồng pha khung thời gian).
   - **ĐIỂM NÀY TUYỆT ĐỐI KHÔNG PHẢI LÀ XÁC SUẤT THẮNG 78%**. Điểm cao không thể tự động vượt qua các chốt chặn bảo vệ (Guards: tin tức đỏ, trượt giá, hạn mức rủi ro ngày).
2. **Điểm bất biến (Frozen) vs Điểm hiện tại (Current)**:
   - Khi một lệnh được ARM, điểm đánh giá tại thời điểm đó được đóng băng (frozen) vĩnh viễn vào `cost_snapshot` của lệnh.
   - Khi xem lại nhật ký hoặc vị thế đang chạy, điểm luôn giữ nguyên giá trị lúc ARM, không bị thay đổi theo biến động nến tiếp theo.
3. **Ngưỡng điểm chưa lưu & Lệnh cũ (Legacy)**:
   - Nếu snapshot lịch sử thiếu thông tin ngưỡng hoặc điểm gốc, hệ thống hiển thị dấu gạch ngang `'—'` ("Chưa lưu"), không tự tiện điền số 70 hay giả định không có bài học.
   - Các lệnh từ phiên bản cũ chưa có hệ thống chấm điểm hiển thị nhãn `"Chưa lưu điểm ở phiên bản cũ (snapshot legacy)"`, không tính lại từ biểu đồ hiện tại.
4. **Báo cáo lịch sử trung thực**:
   - Khi xem báo cáo tài khoản trong một khoảng thời gian quá khứ, nếu cơ sở dữ liệu chưa có đủ log chuyển trạng thái để tái dựng số vị thế đang mở tại thời điểm kết thúc, hệ thống sẽ ghi rõ `"Chưa đủ lịch sử xác minh"` thay vì giả định là 0 vị thế.
