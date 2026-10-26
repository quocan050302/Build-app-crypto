# BẢN ĐỒ THỰC THI V16.10 (IMPLEMENTATION MAP)
## CG-01 END-TO-END WIRING REPAIR: CAUSAL EXECUTION, ARM SIZING, HISTORICAL RESULTS AND FUNNEL

Tài liệu này đối chiếu chi tiết từng hàm, module, input/output, callers, thay đổi và test bảo vệ cho 4 khu vực sửa chữa (Area A, B, C, D) theo tiêu chuẩn AURUM DESK V16.10.

---

### 1. Khu Vực A: Thực Thi OHLC Nhân Quả & Loại Bỏ Pre-pass Đóng Lệnh Nhìn Trước

#### `backend/lab/cg01_replay_execution_adapter.py`

##### `build_assumed_bar_events(bar, path_policy, spread, execution_model, assumption_version)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Kiểu**: Pure Function
- **Input**:
  - `bar: Dict[str, Any]` (open, high, low, close, timestamp, bar_open_time, bar_duration_ms, bid, ask)
  - `path_policy: str = "COLOR_BASED"`
  - `spread: Optional[float] = None`
  - `execution_model: str = "ASSUMED_OHLC_PATH"`
  - `assumption_version: int = 1`
- **Output**: `List[Dict[str, Any]]` (4 synthetic waypoint events)
- **Thay đổi V16.10**:
  - Xác thực nghiêm ngặt `path_policy == "COLOR_BASED"`. Nếu gặp policy lạ, ném ngoại lệ `CONFIG_ERROR`.
  - Phân tách tuần tự:
    - Nến Đỏ (`open >= close`): `OPEN -> HIGH -> LOW -> CLOSE`. (Long chạm TP tại HIGH trước khi chạm SL tại LOW; Short chạm SL tại HIGH trước khi chạm TP tại LOW).
    - Nến Xanh (`open < close`): `OPEN -> LOW -> HIGH -> CLOSE`. (Long chạm SL tại LOW trước khi chạm TP tại HIGH; Short chạm TP tại LOW trước khi chạm SL tại HIGH).
- **Callers**: `CG01ReplayExecutionAdapter.on_quote`.
- **Test bảo vệ**: `tests/test_v16_10_causal_ohlc_path.py::test_long_red_candle_hits_tp_at_high_before_sl_at_low`, `test_short_green_candle_hits_tp_at_low_before_sl_at_high`, `test_unknown_path_policy_raises_config_error`.

##### `CG01ReplayExecutionAdapter.on_quote(event)`
- **Loại**: [HIỆN CÓ — TÁI CẤU TRÚC]
- **Thay đổi V16.10**:
  - **Loại bỏ hoàn toàn pre-pass đóng lệnh nhìn trước**: Xóa toàn bộ khối lệnh kiểm tra `is_ambig` và cưỡng bức đóng `AMBIGUOUS_BAR_SL_FIRST` trước vòng lặp waypoint.
  - Chuyển giao toàn bộ việc khớp lệnh và thoát lệnh sang xử lý tuần tự qua hàm trợ năng `_process_replay_quote_event`.
  - Đảm bảo đồng hồ `ReplayClock` tịnh tiến đơn điệu không giật lùi (`t0 <= t1 <= t2 <= t3 == bar_close_ms`).
  - Đánh giá cờ mơ hồ sau khi nến kết thúc qua `_evaluate_post_bar_ambiguity` (chỉ ghi nhận metadata, không can thiệp giá thoát hoặc PnL).
- **Callers**: `CG01ReplayDriver.run`.
- **Test bảo vệ**: `tests/test_v16_10_causal_ohlc_path.py::test_monotonic_clock_invariant_during_bar_processing`, `test_cooldown_starts_at_exact_exit_timestamp`.

##### `CG01ReplayExecutionAdapter._process_replay_quote_event(ev, bar_open, bar_dur)`
- **Loại**: [MỚI]
- **Kiểu**: Adapter Private Method
- **Input**:
  - `ev: Dict[str, Any]` (waypoint event chứa timestamp_ms, price, bid, ask)
  - `bar_open: int`
  - `bar_dur: int`
- **Output**: `None`
- **Hành vi**:
  - Tịnh tiến đồng hồ về `sub_ts = ev["timestamp_ms"]`.
  - Bước 1: Thẩm định thoát vị thế đang mở sẵn trước waypoint này qua `PaperBroker.process_price_tick`. Giá thoát tại SL/TP được bảo tồn tại mức giá hợp đồng, không bị trượt ảo về đỉnh/đáy râu nến.
  - Bước 2: Thẩm định khớp lệnh limit chờ qua `ExecutionCoordinator.evaluate_orders_sync`.
  - Bước 3: Nếu lệnh vừa khớp tại chính waypoint này, tiếp tục thẩm định thoát vị thế tại cùng waypoint.
- **Callers**: `CG01ReplayExecutionAdapter.on_quote`.

##### `CG01ReplayExecutionAdapter._evaluate_post_bar_ambiguity(low, high, bar_open, bar_close)`
- **Loại**: [MỚI]
- **Kiểu**: Adapter Private Method
- **Hành vi**:
  - Duyệt các lệnh đã mở/đóng trong thanh nến vừa xử lý.
  - Nếu biên độ nến `[low, high]` chạm cả mức SL và TP, gán cờ `order.intrabar_ambiguous = True` cho mục đích kiểm toán.
  - **Tuyệt đối không viết lại**: `actual_exit`, `closed_at`, `realized_pnl_net`, `fees`, quota ngày.
- **Callers**: `CG01ReplayExecutionAdapter.on_quote`.

---

### 2. Khu Vực B: Đấu Nối Sizing Vốn Cố Định Qua Real Paper Candidate Snapshot

#### `backend/services/scalp_watcher_orchestrator.py`

##### `ScalpWatcherOrchestrator._arm_paper_candidate(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi V16.10**:
  - Truy xuất chính thống `capital_mode` từ cấu hình/chính sách (`FIXED_INITIAL_CAPITAL` hoặc `CURRENT_ACCOUNT_BALANCE`).
  - Xác định chính xác `target_capital` và `target_budget` tương ứng.
  - Truyền trực tiếp `capital_mode`, `target_capital`, `target_risk_budget` vào `build_arm_plan_snapshot(...)`.
  - **Triệt tiêu hoàn toàn code "vá dict" ở phía test**: Caller và test nhận snapshot hoàn chỉnh từ domain engine mà không phải sửa tay `snapshot["capital_mode"]`.
- **Callers**: `ScalpWatcherOrchestrator.evaluate_cycle`.
- **Test bảo vệ**: `tests/test_v16_10_arm_to_fill_sizing.py::test_real_orchestrator_arm_fixed_capital_snapshot_long`, `test_real_orchestrator_arm_fixed_capital_snapshot_short`.

##### `ScalpWatcherOrchestrator.evaluate_cycle(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi V16.10**:
  - Xử lý rollback cơ sở dữ liệu nguyên tử khi commit thất bại.
  - Dọn dẹp sạch `cg01_eval_data["armed_order_id"] = None`, gán `arm_outcome = "TRANSACTION_ROLLBACK"` và `stage = "DEGRADED"`.
- **Test bảo vệ**: `tests/test_v16_10_arm_to_fill_sizing.py::test_account_degraded_after_arm_applies_safety_cap`, `tests/test_v16_10_funnel_attribution.py::test_transaction_rollback_clears_armed_order_and_categorizes_system_degraded`.

#### `backend/services/smc_model_registry.py`

##### `get_model_spec(model_id)` & `is_registered_model(model_id)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi V16.10**:
  - Bổ sung ánh xạ bí danh (alias mapping): Chuỗi `"CG01"` tự động phân giải thành `ModelIdentifier.CG01_SWEEP_MSS_FVG.value` (`"CG01_SWEEP_MSS_FVG"`).
  - Đảm bảo `check_candidate_lifecycle_authority` luôn xác thực thành công thẩm quyền của model CG01.

---

### 3. Khu Vực C: Thẩm Quyền Kết Quả Lịch Sử & Effective Config

#### `backend/lab/cg01_replay_driver.py`

##### `CG01ReplayDriver._blocked_data_response(reason)`
- **Loại**: [MỚI]
- **Kiểu**: Driver Helper Method
- **Input**: `reason: str`
- **Output**: `Dict[str, Any]` (Chuẩn phản hồi `BLOCKED_DATA`)
- **Hành vi**:
  - Khởi tạo `EffectiveReplayFinancialConfig` chuẩn từ `resolve_replay_financial_config`.
  - Đóng gói đầy đủ `run_context` chứa `effective_config` và `config_hash`.
  - Đảm bảo mọi nhánh dữ liệu lỗi (file thiếu, file thư mục, JSON hỏng, timestamp đảo lộn) đều trả về `BLOCKED_DATA` kèm thẩm quyền cấu hình rõ ràng.
- **Callers**: `CG01ReplayDriver.run`.
- **Test bảo vệ**: `tests/test_v16_10_runner_result_authority.py::test_missing_path_directory_or_corrupt_json_produces_blocked_data_with_run_context`.

#### `tools/run_v16_10_acceptance.py`

##### `main()` & Runner Pipeline
- **Loại**: [MỚI]
- **Hành vi**:
  - Xác thực nghiêm ngặt dataset M1 được truyền qua `--m1-dataset`: kiểm tra `os.path.isfile`, cú pháp JSON hợp lệ và độ dài tối thiểu 2 nến.
  - Nếu người dùng truyền dataset sai/hỏng: giữ nguyên `historical_status = "BLOCKED_DATA"` và `performance_status = "NOT_EVALUATED"`, không tự ý chạy demo thay thế rồi báo thành công.
  - Nếu không truyền `--m1-dataset`: chạy demo kỹ thuật cô lập, đánh dấu `historical_status = "BLOCKED_DATA"`, `performance_status = "NOT_EVALUATED"`.
  - **Đồng bộ Effective Config**: Đọc trực tiếp từ `replay_res["run_context"]["effective_config"]`. Khi demo chạy với slippage 0.0, file `effective_config.json` xuất ra đúng `slippage_usd: 0.0` (không bị ghi đè 0.10).
  - Kiểm toán `execution_demo["seeded_orders_count"] == 0` khi chạy historical replay.
- **Test bảo vệ**: `tests/test_v16_10_runner_result_authority.py::test_config_parity_demo_zero_slippage_in_run_context`, `test_zero_seed_assertion_in_historical_run`.

---

### 4. Khu Vực D: Phân Tách Phễu Quyết Định & Đơn Vị Đo Lường Sạch

#### `backend/lab/cg01_replay_driver.py`

##### `normalize_replay_cycle_observation(cycle_res)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi V16.10**:
  - Tách bạch rõ ràng `cg01_evaluation.armed_order_id` (chỉ gán khi `transaction_status == "COMMITTED"` và do CG-01 sinh ra) và `global_armed_id` (lệnh từ các model khác).
  - Ghi nhận `candidate_key`, `episode_key`, cờ `eligible`, `guard_summary`.
- **Test bảo vệ**: `tests/test_v16_10_funnel_attribution.py::test_global_order_from_other_model_excluded_from_cg01_funnel`.

##### `CG01ReplayDriver.run()` — Khối Tổng Hợp Phễu
- **Loại**: [HIỆN CÓ — TÁI CẤU TRÚC NHÓM 10-15]
- **Thay đổi V16.10**:
  - Ghi nhận danh sách `seeded_order_ids` ngay sau khi `setup_hook` chạy.
  - Tách toàn bộ lệnh trong DB thành 2 nhóm: `seeded_orders` (lệnh demo) và `non_seeded_orders` (lệnh replay).
  - Lọc lệnh CG-01 chuẩn từ `non_seeded_orders` qua `strategy_family` và `observed_cg01_arm_ids`.
  - **Xóa bỏ hoàn toàn lệnh `max()`**: Đối soát minh bạch `cg01_observed_arms` và `cg01_persisted_arms`; ghi nhận `reconciliation_discrepancy`.
  - Phân loại chu kỳ theo 8 danh mục độc lập không gộp chung:
    1. `MODEL_DISABLED`
    2. `NO_QUOTE`
    3. `WAIT_WINDOW`
    4. `WAIT_POI`
    5. `CONTEXT_NOT_READY`
    6. `OBSERVING_WAITING_TRIGGER`
    7. `GUARD_BLOCKED`
    8. `SYSTEM_DEGRADED`
  - Đếm sạch đơn vị đo: `cycle_count` (tổng chu kỳ) tách biệt với `unique_episode_count` (số episode riêng biệt).
  - Kết xuất 2 cấu trúc độc lập: `strategy_funnel` và `execution_demo`.
- **Test bảo vệ**: `tests/test_v16_10_funnel_attribution.py` (toàn bộ 5 test cases).

---

### 5. Danh Sách Test Suites Nghiệm Thu V16.10

| Test Suite | Đường Dẫn | Số Test | Trọng Tâm Nghiệm Thu |
| :--- | :--- | :---: | :--- |
| `test_v16_10_causal_ohlc_path` | `backend/tests/test_v16_10_causal_ohlc_path.py` | 5 | Đường đi OHLC nhân quả không lookahead, không giật lùi đồng hồ, cờ mơ hồ hậu kỳ |
| `test_v16_10_arm_to_fill_sizing` | `backend/tests/test_v16_10_arm_to_fill_sizing.py` | 4 | Đấu nối sizing vốn cố định qua orchestrator thật, triệt tiêu sửa snapshot test-side |
| `test_v16_10_funnel_attribution` | `backend/tests/test_v16_10_funnel_attribution.py` | 5 | Phân rã phễu quyết định, loại trừ lệnh seeded và lệnh ngoài model, đối soát không max() |
| `test_v16_10_runner_result_authority` | `backend/tests/test_v16_10_runner_result_authority.py` | 3 | Xác thực thẩm quyền runner, lưu vết slippage 0.0, zero seed assertion |
| `v16_full_suite` | `backend/tests/test_v16_*.py` | 184 | Toàn bộ hồi quy phiên bản V16 (184 test passed 100%) |
