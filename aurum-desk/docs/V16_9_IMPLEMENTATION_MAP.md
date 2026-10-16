# BẢN ĐỒ THỰC THI V16.9 (IMPLEMENTATION MAP)
## CG-01 CAUSAL EXECUTION, REAL HISTORICAL REPLAY, DECISION FUNNEL AND SIZING CONTRACT REPAIR

Tài liệu này đối chiếu chi tiết từng hàm, module, input/output, callers, thay đổi và test bảo vệ theo quy định **PHẦN 186**.

---

### 1. `backend/lab/cg01_replay_execution_adapter.py`

#### `build_assumed_bar_events(bar, path_policy, spread, execution_model, assumption_version)`
- **Loại**: [MỚI]
- **Kiểu**: Pure Function
- **Input**:
  - `bar: Dict[str, Any]` (open, high, low, close, timestamp, bar_open_time, bar_duration_ms, bid, ask)
  - `path_policy: str = "COLOR_BASED"`
  - `spread: Optional[float] = None`
  - `execution_model: str = "ASSUMED_OHLC_PATH"`
  - `assumption_version: int = 1`
- **Output**: `List[Dict[str, Any]]` (4 synthetic waypoint events: OPEN -> LOW/HIGH -> HIGH/LOW -> CLOSE)
- **Hành vi**:
  - Kiểm tra tính hợp lệ hình học nến (`INVALID_OHLC_GEOMETRY` nếu high < low hoặc open/close nằm ngoài khoảng [low, high]).
  - Phân tách đường đi theo màu nến (Nến xanh: OPEN -> LOW -> HIGH -> CLOSE; Nến đỏ: OPEN -> HIGH -> LOW -> CLOSE).
  - Gán timestamp tăng dần đơn điệu (`t0 <= t1 <= t2 <= t3 == bar_close_ms`).
  - Gán synthetic quote bid/ask tương ứng với từng waypoint.
- **Callers**: `CG01ReplayExecutionAdapter.on_quote`.
- **Test bảo vệ**: `tests/test_v16_9_causal_ohlc_execution.py::test_build_assumed_bar_events_pure_helper`.

#### `CG01ReplayExecutionAdapter.on_quote(event)`
- **Loại**: [HIỆN CÓ — TÁI CẤU TRÚC NHÓM 01-03]
- **Thay đổi**:
  - Loại bỏ hoàn toàn lookahead pre-pass đóng lệnh tại `bar_open` dựa trên đỉnh/đáy toàn nến trong tương lai.
  - Sử dụng `build_assumed_bar_events` để mở rộng nến thành 4 sự kiện tuần tự.
  - Tịnh tiến đồng hồ `self.clock.set_time(sub_ts)` theo từng waypoint; chỉ đóng vị thế hoặc khớp lệnh giới hạn khi giá waypoint vi phạm mức kích hoạt.
  - Xử lý các thanh nến mơ hồ (chạm cả SL và TP) theo nguyên tắc bảo thủ `AMBIGUOUS_BAR_SL_FIRST`: đóng tại mức giá `stop_loss` và timestamp của waypoint chạm SL đầu tiên.
- **Callers**: `CG01ReplayDriver.run`, `CG01ReplayExecutionAdapter.on_candle`.
- **Test bảo vệ**: `tests/test_v16_9_causal_ohlc_execution.py::test_no_future_close_at_open_long`, `test_no_future_close_at_open_short`, `test_close_quote_mode_ignores_wicks`, `test_unknown_execution_model_rejected`.

---

### 2. `backend/services/trade_lifecycle_service.py`

#### `TradeLifecycleService.process_exit_tick(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**:
  - Cập nhật thứ tự ưu tiên gán `occurred_at`: `occurred_at = tick_timestamp or now_ms or candle_timestamp`.
  - Đảm bảo sub-events từ replay adapter luôn ghi nhận đúng `closed_at` theo thời điểm waypoint thay vì bị đè bởi `candle_timestamp` (bar_open).
- **Callers**: `PaperBroker.process_price_tick`.
- **Test bảo vệ**: `tests/test_v16_9_causal_ohlc_execution.py::test_no_future_close_at_open_long`, `test_no_future_close_at_open_short`.

#### `TradeLifecycleService.execute_fill(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**:
  - Bổ sung tham số `sizing_context: Optional[Dict[str, Any]] = None`.
  - Chuyển tiếp `sizing_context` sang `build_fill_plan_snapshot` để lưu vết hợp đồng sizing vào `cost_snapshot`.
- **Callers**: `ExecutionCoordinator.evaluate_orders_sync`.
- **Test bảo vệ**: `tests/test_v16_9_executable_sizing_contract.py::test_coordinator_fill_persists_sizing_contract`.

---

### 3. `backend/services/execution_coordinator.py`

#### `resolve_executable_sizing_context(order, current_equity, capital_mode=None, order_cost_snap=None)`
- **Loại**: [MỚI]
- **Kiểu**: Pure Function
- **Input**:
  - `order: Any`
  - `current_equity: float`
  - `capital_mode: Optional[str] = None`
  - `order_cost_snap: Optional[Dict[str, Any]] = None`
- **Output**: `Dict[str, Any]` chứa:
  - `capital_mode`: `CURRENT_ACCOUNT_BALANCE` hoặc `FIXED_INITIAL_CAPITAL`
  - `target_capital`: Vốn mục tiêu khi arm lệnh
  - `target_budget`: Ngân sách rủi ro dự kiến
  - `account_capital_at_fill`: Vốn tài khoản tại thời điểm khớp
  - `account_safety_cap`: Giới hạn an toàn dựa trên vốn hiện hành
  - `executable_budget`: Ngân sách rủi ro thực tế cho phép thực thi
  - `adjustment_reason`: `"NONE"` hoặc `"ACCOUNT_SAFETY_CAP"`
- **Callers**: `ExecutionCoordinator.evaluate_orders_sync`.
- **Test bảo vệ**: `tests/test_v16_9_executable_sizing_contract.py::test_resolve_executable_sizing_context_current_mode`, `test_resolve_executable_sizing_context_fixed_mode`, `test_resolve_executable_sizing_context_excess_equity`, `test_resolve_executable_sizing_context_short_direction`.

#### `ExecutionCoordinator.evaluate_orders_sync(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**:
  - Tích hợp `resolve_executable_sizing_context` để xác định chính xác `executable_budget` và nguồn vốn tái thẩm định trước khi khớp lệnh.
  - Truyền `sizing_context` vào `TradeLifecycleService.execute_fill`.
- **Callers**: `CG01ReplayExecutionAdapter.on_quote`, `PaperBroker`.
- **Test bảo vệ**: `tests/test_v16_9_executable_sizing_contract.py::test_coordinator_fill_persists_sizing_contract`, `test_legacy_order_fill_without_sizing_context`.

---

### 4. `backend/services/order_plan_snapshot_service.py`

#### `build_arm_plan_snapshot(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**:
  - Thêm tham số: `capital_mode: str = "CURRENT_ACCOUNT_BALANCE"`, `target_capital: Optional[float] = None`, `target_risk_budget: Optional[float] = None`.
  - Ghi nhận đầy đủ thông tin hợp đồng sizing vào snapshot: `capital_mode`, `target_capital`, `target_risk_budget`, `account_capital_at_fill`, `executable_risk_budget`, `actual_risk_after_rounding`, `sizing_adjustment_reason`.
- **Callers**: `ScalpWatcherOrchestrator._arm_scalp_order`, tests.

#### `build_fill_plan_snapshot(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**:
  - Thêm tham số: `sizing_context: Optional[Dict[str, Any]] = None`.
  - Lưu trữ và kế thừa các trường sizing từ `sizing_context` hoặc `arm_dict`:
    `capital_mode`, `target_capital`, `target_risk_budget`, `account_capital_at_fill`, `executable_risk_budget`, `actual_risk_after_rounding`, `sizing_adjustment_reason`.
- **Callers**: `TradeLifecycleService.execute_fill`.
- **Test bảo vệ**: `tests/test_v16_9_executable_sizing_contract.py::test_coordinator_fill_persists_sizing_contract`.

---

### 5. `backend/services/scalp_watcher_orchestrator.py`

#### `serialize_cg01_evaluation(...)`
- **Loại**: [MỚI]
- **Kiểu**: Pure Function
- **Input**:
  - `evaluated: bool`
  - `symbol: str`
  - `timestamp_ms: int`
  - `decision_stage: str`
  - `filter_results: Optional[Dict[str, Any]]`
  - `signal_reason: Optional[str]`
  - `candidate_order: Optional[Dict[str, Any]]`
  - `armed_order_id: Optional[str]`
  - `rejection_reason: Optional[str]`
- **Output**: `Dict[str, Any]` (canonical standardized evaluation payload)
- **Callers**: `ScalpWatcherOrchestrator.evaluate_cycle`.
- **Test bảo vệ**: `tests/test_v16_9_cg01_decision_funnel.py::test_serialize_cg01_evaluation_pure_helper`.

#### `ScalpWatcherOrchestrator.evaluate_cycle(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**:
  - Đảm bảo từ điển `cg01_evaluation` luôn luôn xuất hiện trong kết quả trả về của chu trình, kể cả các điểm return sớm (`MODEL_DISABLED`, `NO_QUOTE`, `TIMEFRAME_WINDOW_SKIP`).
  - Ghi nhận `armed_order_id` khi lệnh LIMIT được arm thành công vào cơ sở dữ liệu.
- **Callers**: `CG01ReplayExecutionAdapter.on_bar_close`, `main.py`.
- **Test bảo vệ**: `tests/test_v16_9_cg01_decision_funnel.py::test_orchestrator_returns_cg01_evaluation_on_observing`, `test_funnel_counts_armed_order_correctly`.

---

### 6. `backend/lab/cg01_replay_driver.py`

#### `normalize_replay_cycle_observation(cycle_res, as_of_ms)`
- **Loại**: [MỚI]
- **Kiểu**: Pure Function
- **Input**: `cycle_res: Optional[Dict[str, Any]]`, `as_of_ms: int`
- **Output**: `Dict[str, Any]` chuẩn hóa chứa `evaluated`, `decision_stage`, `rejection_reason`, `armed_order_id`.
- **Callers**: `CG01ReplayDriver.run`.
- **Test bảo vệ**: `tests/test_v16_9_cg01_decision_funnel.py::test_normalize_replay_cycle_observation`.

#### `CG01ReplayDriver.run()`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**:
  - Tích hợp `normalize_replay_cycle_observation` vào quy trình thu thập quan sát tại mỗi nến đóng `on_bar_close`.
  - Phễu quyết định `rejection_funnel` tổng hợp chuẩn xác các giai đoạn quan sát mà không làm mất thông tin từ orchestrator.
- **Callers**: `tools/run_v16_9_acceptance.py`, tests.
- **Test bảo vệ**: `tests/test_v16_9_cg01_decision_funnel.py::test_funnel_counts_armed_order_correctly`.

---

### 7. `tools/run_v16_9_acceptance.py`
- **Loại**: [MỚI]
- **Chức năng**:
  - Tách bạch hoàn toàn giữa hàm mô phỏng mẫu (`run_synthetic_execution_demo`) và hàm chạy replay lịch sử thật (`run_historical_cg01_replay`).
  - Tuyệt đối không inject seed orders hoặc `setup_hook` khi chạy nhánh dữ liệu lịch sử.
  - Nếu `--m1-dataset` vắng mặt hoặc tệp không tồn tại: báo cáo `historical_status = "BLOCKED_DATA"` và `performance_status = "NOT_EVALUATED"`.
  - Đánh giá 6 Evidence-based Technical Gates:
    1. `causal_execution`: `tests/test_v16_9_causal_ohlc_execution.py`
    2. `historical_routing`: `tests/test_v16_9_historical_runner.py`
    3. `decision_contract`: `tests/test_v16_9_cg01_decision_funnel.py`
    4. `executable_sizing`: `tests/test_v16_9_executable_sizing_contract.py`
    5. `full_regression_v16`: `tests -k v16 -q`
    6. `git_cleanliness`: `git diff --check`
  - Xuất 9 tệp artifact và báo cáo nghiệm thu `docs/V16_9_ACCEPTANCE_REPORT.md`.
  - Thoát mã lỗi khác 0 nếu có bất kỳ cổng kỹ thuật bắt buộc nào không đạt.
