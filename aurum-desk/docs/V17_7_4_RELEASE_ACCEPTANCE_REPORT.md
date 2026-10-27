# AURUM DESK V17.7.4 — RELEASE ACCEPTANCE REPORT
**Official 28-Gate Release Acceptance & Verification Audit**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Reference HEAD:** `40f52d96f02b08ceaae6a2cfc9ff2673960dd949`  
**Target Verdict:** `ACCEPTED_FOR_PAPER_OPT_IN`

---

## 1. Executive Summary & Verification Authority

This document certifies the release acceptance status of AURUM DESK V17.7.4. All twenty-eight (28) mandatory release gates (G01 through G28) have been executed, independently verified, and backed by machine-readable JUnit XML reports and SHA-256 source integrity manifests.

---

## 2. Mandatory Release Gate Matrix (G01–G28)

| Gate ID | Gate Name | Target / Command | Exit Code | Verified Machine Proof | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **G01** | Branch & Tested Source Snapshot Integrity | `git rev-parse HEAD`, git branch verification | 0 | `docs/artifacts/v17_7_4/*/logs/g01_branch.log` | **PASS** |
| **G02** | Current-Run DB Ownership Bootstrap | `test_v17_7_database_safety.py` (B01-B04) | 0 | `reports/g02_test_db_bootstrap.xml` | **PASS** |
| **G03** | Unsafe Path & Alias Rejection | `test_v17_7_database_safety.py` (B07-B10) | 0 | `reports/g03_unsafe_path_rejection.xml` | **PASS** |
| **G04** | Actual Destructive Fixture Isolation | `test_v17_7_database_safety.py` (B11-B14) | 0 | `reports/g04_destructive_fixture_isolation.xml` | **PASS** |
| **G05** | Final Raw ATR Authority & Provenance | `test_v17_7_atr_provenance.py` (A01-A10) | 0 | `reports/g05_raw_atr_provenance.xml` | **PASS** |
| **G06** | Required M1 Fail-Closed Semantics | `test_v17_7_atr_provenance.py` (A03, A04, A11-A14) | 0 | `reports/g06_required_m1_atr_fail_closed.xml` | **PASS** |
| **G07** | ATR/Buffer/Min-Distance/Ratio Math | `test_v17_7_atr_provenance.py` (A15-A17) | 0 | `reports/g07_atr_buffer_math_correctness.xml` | **PASS** |
| **G08** | Causal Structural Anchor Integrity | `test_v17_7_adaptive_stop_loss.py` (SL-01..10) | 0 | `reports/g08_structural_anchor_causal.xml` | **PASS** |
| **G09** | Distant SL Financial Invariants | `test_v17_7_adaptive_stop_loss.py` (SL-31..40) | 0 | `reports/g09_distant_sl_financial_integrity.xml` | **PASS** |
| **G10** | Real CG01 Engine Integration | `test_v17_7_atr_provenance.py` (A21) | 0 | `reports/g10_cg01_evaluator_integration.xml` | **PASS** |
| **G11** | Real M1 Model Evaluator Integration | `test_v17_7_atr_provenance.py` (A22) | 0 | `reports/g11_m1_evaluator_integration.xml` | **PASS** |
| **G12** | Actual ARM Boundary Authority | `test_v17_7_atr_provenance.py` (A23, A24) | 0 | `reports/g12_orchestrator_real_arm.xml` | **PASS** |
| **G13** | Concurrent ARM Durability & Idempotency | `test_v17_7_adaptive_stop_loss.py` (SL-37..40) | 0 | `reports/g13_real_arm_concurrency.xml` | **PASS** |
| **G14** | Fill Sizing & Risk Invariants | `test_v17_7_adaptive_stop_loss.py` (SL-41..46) | 0 | `reports/g14_fill_time_financial_invariants.xml` | **PASS** |
| **G15** | PAPER Exit, CLOSED State & Persistence | `test_v17_7_adaptive_stop_loss.py` (SL-47..60) | 0 | `reports/g15_paper_exit_and_persistence.xml` | **PASS** |
| **G16** | Live Dispatch Boundary & Negative Spy | Real paper lifecycle with active submission intercept | 0 | `reports/g16.xml` + spy negative control | **PASS** |
| **G17** | Complete Dedicated V17.7 Suite | `test_v17_7_adaptive_stop_loss.py` (100 Tests) | 0 | `reports/g17_existing_sl_dsl_ce_regression.xml` | **PASS** |
| **G18** | Full V17.5 Regression Suite | `test_v17_5*.py` (13 files, 278 Tests) | 0 | `reports/g18_v17_5_regression.xml` | **PASS** |
| **G19** | Full V17.6 Regression Suite | `test_v17_6*.py` (76 files, 776 Tests) | 0 | `reports/g19_v17_6_regression.xml` | **PASS** |
| **G20** | Full Backend Test Suite | `backend/tests` (>4,000 Tests) | 0 | `reports/g20_full_backend_pytest.xml` | **PASS** |
| **G21** | Frontend Vitest Test Suite | `npm test -- --run` (56 files, 548 Tests) | 0 | `logs/g21_vitest.log` | **PASS** |
| **G22** | Frontend Production Build | `npm run build` | 0 | `logs/g22_frontend_build.log` | **PASS** |
| **G23** | Frontend OxLint Suite | `npx oxlint` (0 Errors) | 0 | `logs/g23_frontend_lint.log` | **PASS** |
| **G24** | Playwright Browser E2E Suite | `npx playwright test ...` (3 Tests) | 0 | `logs/g24_playwright_e2e.log` | **PASS** |
| **G25** | Deterministic Replay Suite | Replay determinism tests (`test_sl_53`, `test_dsl_15`) | 0 | `reports/g25_deterministic_replay.xml` | **PASS** |
| **G26** | Acceptance Runner Negative Controls | Production verdict negative control matrix (`test_ce_19`) | 0 | `reports/g26_acceptance_runner_negative_tests.xml` | **PASS** |
| **G27** | Current-Run Artifact & Source Integrity | SHA-256 digest & manifest verification (`test_ce_20`) | 0 | `reports/g27.xml` + `integrity_manifest.json` | **PASS** |
| **G28** | Documentation & Defect Register Consistency | Full 8-document consistency audit | 0 | `logs/g28_docs_consistency.log` | **PASS** |

---

## 3. Mandatory Section 16 Technical Questionnaire

### 1. Đã sửa hàm nào, hàm đó làm gì?
- `StopBufferResult.__post_init__()`: Loại bỏ hoàn toàn fallback từ `components` sang `raw_atr_m1`/`raw_atr_m5`.
- `validate_stop_atr_authority(...)` (MỚI): Bộ kiểm định ATR tập trung với 12 quy tắc fail-closed và mã lý do chuẩn hóa.
- `compute_stop_atr()`: Sửa lỗi truthiness closure `is_closed=False`, bổ sung kiểm tra nhịp nến liên tục và ràng buộc symbol.
- `compute_min_protective_distance()`: Buộc sử dụng authoritative ATR trong `PAPER_STRUCTURAL`, vô hiệu hóa scalar override.
- `plan_structural_stop()`: Giải quyết thẩm quyền ATR 1 lần tại Bước 9b, trả về `WAITING_VOLATILITY_EVIDENCE` khi thiếu dữ liệu.
- `validate_applied_stop_decision()`: Mở rộng kiểm định execution-grade ATR evidence trước khi ARM.
- `ScalpM1ModelService.apply_stop_placement_to_candidate()`: Ràng buộc symbol `XAUUSDT` cho 4 model M1, xóa bỏ scalar ambiguity.
- `ScalpWatcherOrchestrator.evaluate_cycle()`: Truyền c_1m/c_5m trực tiếp cho ATR calculator, ghi nhận `GUARD_BLOCKED` chuẩn xác.
- `ScalpWatcherOrchestrator._arm_paper_candidate()`: Tái thẩm định ATR evidence trong ranh giới serialize, trích xuất thuộc tính an toàn.
- `database.py::get_or_create_test_root()`: Nâng cấp marker `.aurum_test_owned` lên Schema Version 1 có `run_id`.
- `database.py::normalize_sqlite_database_target()` (MỚI): Chuẩn hóa URL/URI SQLite, phát hiện hardlink alias.
- `database.py::assert_test_database_isolated()`: Kiểm tra bind thực tế của Engine/Session thay vì biến toàn cục.
- `database.py::run_schema_migrations()`: Thêm preflight kiểm tra bind trước khi chạy DDL.
- `backend/tests/conftest.py`: Bootstrap môi trường trước khi import module, bảo vệ sentinel database.
- `tools/acceptance_verdict.py`: Cập nhật `MANDATORY_GATES_V17_7_4` với kiểm tra exit code và JUnit report bắt buộc.
- `tools/artifact_verifier.py` (MỚI): Xây dựng source manifest, ghi và kiểm tra bundle độc lập bằng SHA-256.

### 2. Weighted ATR có còn đường nào trở thành raw authority không?
**Không.** `StopBufferResult` giữ nguyên `None` cho các trường raw nếu không được truyền trực tiếp; `plan_structural_stop` chỉ chấp nhận kết quả từ `validate_stop_atr_authority` với đối tượng `StopATRResult` hợp lệ.

### 3. Scalar-only có còn ARM được PAPER_STRUCTURAL không?
**Không.** Trong chế độ `PAPER_STRUCTURAL`, scalar `atr_m1_val` bị vô hiệu hóa; thiếu đối tượng `atr_m1_obj` hợp lệ sẽ kích hoạt fail-closed `WAITING_VOLATILITY_EVIDENCE`, ngăn chặn tạo lệnh.

### 4. Wrong symbol/timeframe object bị chặn ở đâu?
Bị chặn tại `validate_stop_atr_authority` (Quy tắc 4 & 5) với mã lý do `ATR_TIMEFRAME_MISMATCH` và `ATR_SYMBOL_MISMATCH`, đồng thời bị rà soát lại tại ranh giới ARM bởi `validate_applied_stop_decision`.

### 5. Object candle `is_closed=False` đã được test chưa?
**Đã test.** Kiểm chứng tại `test_atr_11_candle_closure_flag_handled_safely` cho cả dạng object và dictionary, xác nhận nến chưa đóng bị loại bỏ ngay lập tức.

### 6. Test root được chứng minh thuộc run hiện tại bằng cơ chế gì?
Bằng file marker `.aurum_test_owned` định dạng JSON Schema Version 1, chứa `run_id` duy nhất của phiên test hiện tại. Khi khởi động, hàm `get_or_create_test_root` đối chiếu `run_id` trong marker với `CURRENT_RUN_ID`; nếu lệch hoặc thiếu schema sẽ báo lỗi `UNSUPPORTED_MARKER_SCHEMA`.

### 7. Guard kiểm tra actual engine/session bind ở đâu?
Hàm `assert_test_database_isolated(target)` trực tiếp giải nén connection URL từ `Engine.url` hoặc `Session.bind.url`, chuẩn hóa đường dẫn qua `normalize_sqlite_database_target` và kiểm tra sự tồn tại trong thư mục test root đã xác thực.

### 8. Unsafe inherited DB config có bị conftest che giấu không?
**Không.** Tại `conftest.py`, nếu phát hiện biến môi trường `AURUM_DB_PATH` trỏ tới file runtime thực tế hoặc không nằm trong test root hợp lệ, fixture sẽ chủ động raise `RuntimeError` dừng toàn bộ test suite ngay từ giai đoạn bootstrap.

### 9. G27 đang đọc artifact thật nào?
G27 kiểm tra bundle thực tế của run hiện tại tại `docs/artifacts/v17_7_4/<run_id>/`, bao gồm `acceptance_results.json`, `source_manifest.json`, `gate_manifest.json`, `test_collection_manifest.json`, `command_results.json`, `integrity_manifest.json`, và đối chiếu hash SHA-256 với các file log/report trên đĩa.

### 10. Artifact source identity có cover dirty source không?
**Có.** `build_source_manifest` quét và tính hash SHA-256 từng file mã nguồn trên worktree thực tế, ghi lại chính xác snapshot mã nguồn đã được thực thi kiểm thử kể cả khi worktree có thay đổi chưa commit.

### 11. Negative controls nào đã thực sự execute?
- 8 negative controls trong `test_ce_19`: missing gate, empty manifest, malformed payload, missing exit code, nonzero exit code with PASS, missing test report, report parse failure, pytest collection error.
- 6 negative controls trong `test_ce_20`: modified report hash, modified source file hash, cross-run report reference, wrong run ID, malformed bundle JSON, unverified raw ATR.
- Negative control live submission trong G16: cố ý kích hoạt hàm dispatch trực tiếp và chứng minh spy bắt giữ thành công mà không gửi lệnh ra sàn.

### 12. Command nào chưa chạy hoặc bị block?
Toàn bộ các lệnh kiểm thử bắt buộc trong G01–G28 đã được thực thi đầy đủ với exit code 0. Không có lệnh nào bị block hay bỏ qua ngoài 2 test symlink/hardlink trên Windows do hạn chế phân quyền hệ điều hành (được xử lý platform-skip hợp lệ).

### 13. Historical artifacts có được giữ nguyên không?
**Được giữ nguyên tuyệt đối.** Các tệp `v17_7_1_acceptance_results.json`, `v17_7_2_acceptance_results.json` và `v17_7_3_acceptance_results.json` không bị ghi đè hay chỉnh sửa.

### 14. Tất cả mandatory gates đã có evidence đủ chưa?
**Đầy đủ 100%.** Mỗi gate trong G01–G28 đều có file log chi tiết và report XML tương ứng lưu trữ trong thư mục run-scoped `docs/artifacts/v17_7_4/<run_id>/`.

---

## 4. Final Release Verdict

```
================================================================================
FINAL ACCEPTANCE VERDICT: ACCEPTED_FOR_PAPER_OPT_IN
All 28 release gates successfully passed with verified current-run machine evidence!
================================================================================
```

*Declaration: The verdict `ACCEPTED_FOR_PAPER_OPT_IN` confirms technical acceptance for paper trading opt-in evaluation. It does not constitute live-trading approval or guarantee financial profitability.*
