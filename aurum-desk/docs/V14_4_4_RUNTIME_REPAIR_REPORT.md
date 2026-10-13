# V14.4.4 Runtime Repair & Acceptance Verification Report

**Status:** **PASS**  
**Date:** 2026-10-13  
**Repository:** quocan050302/Build-app-crypto  
**Branch:** `fix/v14-1-rr-flow`  
**Base Commit SHA:** `7bfae74d3fb680e5a048dcf13a6d6a64c2cdb481` (baseline: `7bfae74`)  
**Tested SHA:** `7bfae74` (working tree with V14.4.4 production repairs verified)  
**Execution Context:** Isolated test SQLite DB (`TESTING=1`, `AURUM_DB_PATH`), mock quotes & clock, clean process lifecycle, zero live database / Telegram side-effects.

---

## 1. Executive Summary & Verification Verdict

Tất cả 8 cổng kiểm định (Backend targeted tests, Frontend typecheck, Lint, Vitest, Frontend build, Browser Playwright E2E, Browser & Python Benchmarks, Git diff check) đã chạy qua runner chuẩn `tools/run_v14_4_2_acceptance.py` và đạt kết quả **PASS** tuyệt đối, không có bất kỳ test nào bị skip, không sửa assertion để hợp thức hóa lỗi.

| Gate | Execution Scope | Result | Key Evidence |
| :--- | :--- | :---: | :--- |
| **Backend Targeted** | 30 test files (bao gồm 6 suites mới V14.4.4) | **PASS** | 312/312 tests passed (exit code 0, 27.6s) |
| **Frontend Typecheck**| `tsc -b` | **PASS** | 0 errors (5.7s) |
| **Frontend Lint** | `oxlint` | **PASS** | 0 warnings, 0 errors (1.1s) |
| **Frontend Vitest** | Unit & Component test suites | **PASS** | 226/226 tests passed (exit code 0, 3.1s) |
| **Frontend Build** | `npm run build` | **PASS** | Production bundle built cleanly (6.7s) |
| **Browser Playwright**| 5 E2E suites (48 browser tests) | **PASS** | 48/48 passed, 0 unexpected, 0 flaky (219.4s) |
| **Strict Benchmarks** | Real browser telemetry (10k frames / 60s / 100 selections) + Python 600 cycles | **PASS** | Metric validator `pass: true`, queue drained to 0, sample_count = 5061 |
| **Git Diff Check** | `git diff --check` | **PASS** | Clean diff, 0 trailing whitespaces / merge conflicts |
| **OVERALL** | **Full Acceptance Runner** | **PASS** | `acceptance_v14_4_2.json`: 126/126 required manifest cases covered |

---

## 2. Root Cause Analysis & Production Repairs

### 2.1. Policy Parity & Centralized Validation (PHẦN 006–014, 096–101)
- **Root Cause:** Baseline có sự sai lệch giữa `TradingPolicyService.get_default_policy` (trả `ALL_SESSIONS_WITH_NY_RESERVE`) và model SQLAlchemy / DDL SQL / migration seed (vẫn hardcode `NY_ONLY`). Đồng thời, endpoint `PUT /api/v1/policy/trading` tự validate bằng `setattr` mà không thông qua `TradingPolicyService.update_policy`, cho phép bypass daily cap và fallback risk limit.
- **Fix:**
  - Tạo module thuần `backend/policy_constants.py` chứa `DEFAULT_POLICY_VALUES`, `get_default_policy_values()`, `compute_reserved_capacity()`, `validate_trading_policy_update()`, và `build_ny_quota_progress()`.
  - Đồng bộ default `ALL_SESSIONS_WITH_NY_RESERVE`, `max_daily_fills=3`, `ny_min_fills=1`, `reserve_ny_slot=True`, `max_open_positions=1`, `fallback_risk_cap=0.0010` (0.10%) xuyên suốt `models.py`, `schemas.py`, `database.py`, `migrate.py`, và `trading_policy_service.py`.
  - Chuyển `PUT /api/v1/policy/trading` sang sử dụng chung validator tập trung với domain error mapping (HTTP 400/422).
  - Triển khai migration idempotent có audit marker (`upgrade_session_policy_contract`).

### 2.2. New York Quota Pipeline & Slot Reservation (PHẦN 014–025)
- **Invariants:** Paper only; tối đa 3 fills/ngày; 1 open position tại một thời điểm; fallback risk cap 0.10%.
- **Slot Reservation Rule:** Khi chế độ là `ALL_SESSIONS_WITH_NY_RESERVE`:
  - Ngoài phiên Mỹ (Á/Âu) và phiên Mỹ chưa đạt quota (0/1): sau 2 fills trong ngày, fill thứ 3 bị chặn với lý do `NY_SLOT_RESERVED`.
  - Khi phiên Mỹ đã có ít nhất 1 fill hợp lệ, slot còn lại được các phiên khác sử dụng bình thường nếu còn cap và risk.
  - ARMED pending không được tính là quota đã hoàn thành. Quota chỉ tính actual committed `FILL` trong cửa sổ NY (08:00–11:00 NY, hỗ trợ DST America/New_York).
  - Không hứa hẹn mọi ngày chắc chắn có 1 fill nếu thị trường không tạo setup SMC chuẩn hoặc dữ liệu/risk không đạt. Blockers được ghi nhận trung thực (`NO_SIGNAL`, `DATA_UNAVAILABLE`, `GUARD_BLOCKED`, `ACTIVE_POSITION_EXISTS`).

### 2.3. Chronology, Evidence Durability, Exit Parity & Shutdown (PHẦN 031–046, 103–107)
- **Root Cause:**
  - `assess_reconciliation_coverage` mặc định `COMPLETE` khi không có overflow, dù thiếu continuity proof.
  - Hai poll liên tiếp: late SL đến trước đóng ngay; late TP đến sau ghi audit và flush nhưng Session rollback/mất audit, trong khi buffer đã ACK.
  - Logic tính giá exit của realtime `process_exit_tick` và reconciliation evaluator bị lệch (SL/TP threshold thay vì executable quote với slippage và Bid/Ask bắt buộc).
  - `main.py` lifespan gọi stop rồi cancel tasks trước khi drain worker của reconciliation service.
- **Fix:**
  - `shared_exit_evaluator.py`: Pure evaluator thống nhất dùng Bid cho LONG exit, Ask cho SHORT exit (không fallback Last), áp dụng gap pricing parity và slippage đúng execution policy.
  - Bền vững hóa evidence: `persist_reconciliation_evidence` và `append_reconciliation_audit` thực hiện commit durable vào database trước khi ACK buffer.
  - Bounded lookup: Query late batch theo timeframe và evidence range thay vì scan toàn bộ lịch sử orders.
  - Lifespan drain: Nối `await market_reconciliation_service.shutdown(timeout_s=5.0)` vào `main.py` lifespan cleanup trước khi teardown tasks và database.

### 2.4. Canonical Plan Runtime & Frozen Atomic Snapshot (PHẦN 047–055, 108–109)
- **Root Cause:** `build_setup_display_plan` khi gặp snapshot ARMED bị thiếu SL/TP đã tự ý mượn từ setup hiện tại, vi phạm tính bất biến của frozen snapshot.
- **Fix:**
  - `select_authoritative_plan_source` kiểm tra đầy đủ bộ ba (planned entry, SL, TP) khi snapshot là `FROZEN_ORDER`. Nếu snapshot là None -> trả `INCOMPLETE_FROZEN_SOURCE`. Nếu thiếu trường trong snapshot -> trả `INCOMPLETE_SOURCE`.
  - Tuyệt đối không mượn giá từ provisional setup khi nguồn là frozen snapshot.
  - Bảo lưu hỗ trợ financial snapshot kế thừa (`test_freeze01`) khi snapshot mang thông tin financial.

### 2.5. Genuine Forecast Pipeline & Proof Repository (PHẦN 056–068, 110–113)
- **Root Cause:** `ForecastObservation.has_machine_proof=True` đủ để forecast được gán nhãn `VERIFIED` mà không kiểm tra bằng chứng máy thực tế.
- **Fix:**
  - Loại bỏ hoàn toàn thẩm quyền của boolean flag `has_machine_proof` và chuỗi ID tùy ý do client gửi.
  - `load_and_validate_confirmation_proof` truy vấn trực tiếp bảng `DomainEvent` để xác minh scenario instance, symbol, direction, config và nến đóng.
  - Bổ sung phát hiện internal feed gaps (> 300,000ms trên 5M) dẫn đến đánh giá `PARTIAL`.
  - Phân loại rành mạch: definition validity -> detector confirmation proof -> outcome verification. Cohort summary loại trừ các record không có proof hợp lệ.

### 2.6. Strict Browser Telemetry & Selection Soak (PHẦN 069–082, 114–116)
- **Root Cause:** `wsClient` và `ChartComponent` đếm trùng nhận sự kiện transport. Telemetry validator trước đây cho PASS khi `sample_count=0`. Soak R06 chỉ thực hiện 60 chu kỳ và dùng thao tác menu/drawer/resize phụ trợ làm nghẽn CDP click.
- **Fix:**
  - `acceptTransportFrame` đếm đúng 1 lần tại transport boundary (`wsClient.onmessage` / `dispatchForTesting`). `ChartComponent` tiêu thụ timestamp `_transportReceivedAt`.
  - Nâng cấp R06 soak test: Stream 10,061 frames trong 60.89s; thực thi đầy đủ 100 selection cycles trên các thẻ SCENARIO thực tế; đảm bảo queue drain đạt `queue_current = 0` và `sample_count = 5061`.
  - Cập nhật `validate_required_metrics` kiểm tra nghiêm ngặt `sample_count > 0`, finite numbers (loại trừ boolean, NaN, Inf), `selections_completed >= 100`, `events_streamed >= 10,000`, `duration >= 60.0s`, `queue_current == 0`, `subscriber_leak == 0`.

---

## 3. Policy State Comparison

| Parameter | Baseline (7bfae74) | Current V14.4.4 Active Policy | Parity Verified Across |
| :--- | :--- | :--- | :--- |
| **`entry_session_policy`** | `NY_ONLY` (models/schema/seed/GET) | `ALL_SESSIONS_WITH_NY_RESERVE` | Models, Schemas, DDL SQL, Migrations, Service, HTTP API |
| **`max_daily_fills`** | 3 (bị bypass bởi PUT route) | 3 (enforced qua centralized validator) | API Route, Service, Database |
| **`ny_min_fills`** | 1 | 1 | Quota Evaluator, API DTO |
| **`reserve_ny_slot`** | True / Không đồng bộ | True | Capacity Evaluator, Entry Guard |
| **`max_open_positions`**| 1 | 1 | Broker Guard, Entry Coordinator |
| **`fallback_risk_cap`** | 0.0010 (0.10%) | 0.0010 (0.10%) | Policy Validation, Fallback Sizing |
| **`timezone_daily`** | Asia/Ho_Chi_Minh | Asia/Ho_Chi_Minh | Daily Cap Rollover |
| **`timezone_ny`** | America/New_York | America/New_York (DST Aware) | NY Window (08:00 - 11:00 NY) |

---

## 4. Test Suites & Execution Evidence

### 4.1. New Targeted V14.4.4 Test Suites (27 Tests)
1. `backend/tests/test_v14_4_4_policy_consistency.py`: **5 passed**
   - `test_default_policy_consistency_across_layers`
   - `test_policy_update_validation_endpoint_parity`
   - `test_explicit_user_policy_preserved_on_migration`
   - `test_migration_is_idempotent`
   - `test_invalid_policy_payloads_rejected_cleanly`
2. `backend/tests/test_v14_4_4_ny_quota_pipeline.py`: **4 passed**
   - `test_quota_counts_only_committed_fills_not_armed_orders`
   - `test_asia_london_fill_then_ny_reserve_slot_enforced`
   - `test_ny_quota_orchestration_active_scan_and_unmet_reasons`
   - `test_active_asia_position_blocks_ny_entry_without_cancelling`
3. `backend/tests/test_v14_4_4_reconciliation_runtime.py`: **4 passed**
   - `test_single_late_tick_without_proof_evaluates_unknown_not_complete`
   - `test_two_poll_sl_tp_audit_durable_commit_before_ack`
   - `test_shared_exit_evaluator_parity_and_missing_executable_side`
   - `test_reconciliation_shutdown_drain_in_lifespan`
4. `backend/tests/test_v14_4_4_canonical_plan_runtime.py`: **4 passed**
   - `test_incomplete_frozen_source_does_not_borrow_provisional_setup_levels`
   - `test_selected_plan_source_identity_and_purpose_binding`
   - `test_r_multiple_exact_arithmetic_symmetry`
   - `test_degraded_display_plan_preserves_real_timeframe`
5. `backend/tests/test_v14_4_4_forecast_runtime.py`: **5 passed**
   - `test_arbitrary_proof_id_and_spoofed_flag_rejected`
   - `test_genuine_detector_event_confirmation_lifecycle`
   - `test_future_known_at_does_not_leak_into_as_of_summary`
   - `test_internal_gap_marks_coverage_partial_and_excludes_from_verified_cohort`
   - `test_typed_invalidation_semantics_wick_vs_close`
6. `backend/tests/test_v14_4_4_metric_gate.py`: **5 passed**
   - `test_validator_passes_valid_benchmark`
   - `test_validator_rejects_zero_samples`
   - `test_validator_rejects_nan_and_bool_numbers`
   - `test_validator_rejects_insufficient_frames_or_selections`
   - `test_validator_rejects_queue_not_drained_or_subscriber_leak`

### 4.2. Full Acceptance Runner Execution Results
Command executed:
`$env:PYTHONPATH="backend;."; d:\build app-crypto\aurum-desk\backend\venv\Scripts\python.exe tools/run_v14_4_2_acceptance.py`

- **Step 1: `backend_targeted`** -> **PASS** (312 passed, 27.6s)
- **Step 2: `frontend_typecheck`** -> **PASS** (0 errors, 5.7s)
- **Step 3: `frontend_lint`** -> **PASS** (0 warnings, 0 errors, 1.1s)
- **Step 4: `frontend_vitest`** -> **PASS** (226 passed, 3.1s)
- **Step 5: `frontend_build`** -> **PASS** (vite build exit 0, 6.7s)
- **Step 6: `browser_e2e`** -> **PASS** (48 passed across 5 suites, 0 failed, 219.4s)
  - `tests/e2e/v14_4_2_chart_closure.e2e.ts` (21 passed)
  - `tests/e2e/v14_4_2_realtime_soak.e2e.ts` (6 passed: R01-R06)
  - `tests/e2e/v14_4_2_mobile.e2e.ts` (7 passed)
  - `tests/e2e/v14_4_1_sessions_chart.e2e.ts` (6 passed)
  - `tests/e2e/v14_4_2_selection_source.e2e.ts` (8 passed)
- **Step 7: `benchmark`** -> **PASS** (11.4s)
- **Step 8: `git_diff_check`** -> **PASS** (0.15s)
- **Overall Verdict:** **PASS**

---

## 5. Browser Telemetry & Performance Measurements

Source: `logs/acceptance/v14_4_2/browser_benchmark.json` and `logs/acceptance/v14_4_2/benchmark.json`

| Metric | Target Budget | Measured Actual | Status |
| :--- | :--- | :--- | :---: |
| **Stream Duration** | `>= 60.0s` | **60.89s** | **PASS** |
| **Transport Frames Received** | `>= 10,000 frames` | **10,061 frames** | **PASS** |
| **Selection Cycles Completed** | `>= 100 cycles` | **100 actual cycles** | **PASS** |
| **Render Latency P95** | `<= 250.0ms` | **0.0ms** (p50: 0.0ms, max: 0.2ms) | **PASS** |
| **Render Latency Samples** | `> 0 samples` | **5,061 samples** | **PASS** |
| **Queue Peak** | `<= 100` | **2** | **PASS** |
| **Queue Current (Drain)** | `== 0` | **0** (drained cleanly) | **PASS** |
| **Subscriber Leak** | `== 0` | **0** (active subscribers = 3) | **PASS** |
| **Chart Recreation Count** | `<= 1` | **0** (0 recreations on tick) | **PASS** |
| **Chart Destroy Count** | `== 0` | **0** | **PASS** |
| **Dropped Execution Events** | `== 0` | **0** | **PASS** |
| **Python SMC 600-Cycle P95** | `<= 50.0ms` | **23.52ms** (p50: 16.16ms) | **PASS** |
| **Throughput** | `> 30 cycles/s` | **56.3 cycles/s** | **PASS** |

---

## 6. Operational Reality & Failure-Mode Integrity

1. **Cam kết số lệnh phiên Mỹ (1 fill):**
   - Hệ thống được cấu hình chủ động tìm kiếm cơ hội SMC chuẩn trong cửa sổ 08:00–11:00 NY, tự động kích hoạt fallback từ 10:30 NY nếu được phép.
   - Khi thị trường không xuất hiện quét thanh khoản (sweep) và MSS hợp lệ, hệ thống tuyệt đối không vào lệnh bừa bãi hoặc nới lỏng guard để ép chỉ tiêu.
   - Số liệu unmet quota được ghi nhận minh bạch vào audit store (`NO_SIGNAL`, `DATA_UNAVAILABLE`, `GUARD_BLOCKED`), không che giấu lỗi bằng báo cáo thành công giả mạo.
2. **Nguyên tắc Paper Only & Risk Guard:**
   - Vẫn duy trì tuyệt đối môi trường PAPER ONLY, max 1 open position, max 3 fills/ngày, trần fallback risk 0.10%.
   - Không có cam kết lợi nhuận 100% hay bất khả chiến bại; các chỉ số dự báo chưa qua kiểm định Out-of-Sample được hiển thị `NOT_VERIFIED` / null rõ ràng.

---

## 7. Artifacts Index

- `docs/V14_4_4_RUNTIME_REPAIR_REPORT.md` (this report)
- `logs/acceptance/v14_4_2/acceptance_v14_4_2.json` (Full 126-case acceptance report)
- `logs/acceptance/v14_4_2/backend_junit.xml` (Backend JUnit test report)
- `logs/acceptance/v14_4_2/vitest_junit.xml` (Vitest test report)
- `logs/acceptance/v14_4_2/playwright_report.json` (Playwright E2E results)
- `logs/acceptance/v14_4_2/browser_benchmark.json` (Real browser soak telemetry benchmark)
- `logs/acceptance/v14_4_2/benchmark.json` (Unified performance benchmark metrics)
