# BẢN ĐỒ THỰC THI V16.8 (IMPLEMENTATION MAP)
## CG-01 REPLAY FINANCIAL AUTHORITY, CAUSAL EVENTS AND EVIDENCE REPAIR

Tài liệu này đối chiếu chi tiết từng hàm, module, input/output, callers, thay đổi và test bảo vệ theo quy định **PHẦN 186**.

---

### 1. `backend/services/plan_financial_context_service.py`

#### `ReplayCapitalMode`
- **Loại**: [MỚI]
- **Kiểu**: `str, Enum`
- **Giá trị**:
  - `CURRENT_ACCOUNT_BALANCE = "CURRENT_ACCOUNT_BALANCE"` (Mặc định chuẩn)
  - `FIXED_INITIAL_CAPITAL = "FIXED_INITIAL_CAPITAL"` (Chỉ dùng khi khai báo tường minh)
- **Callers**: `resolve_replay_sizing_capital`, `resolve_replay_financial_config`, `CG01ReplayExecutionAdapter`, `CG01ReplayDriver`.
- **Test bảo vệ**: `tests/test_v16_8_capital_basis.py::test_resolve_replay_sizing_capital_pure_helper`.

#### `resolve_replay_sizing_capital(...)`
- **Loại**: [MỚI]
- **Input**:
  - `capital_mode: Any`
  - `initial_capital: float`
  - `current_account_equity: Optional[float] = None`
  - `as_of_ms: Optional[int] = None`
- **Output**: `Tuple[float, str, Optional[int]]` (sizing_capital, capital_source, as_of_ms)
- **Hành vi**:
  - Kiểm tra `capital_mode` hợp lệ (`CONFIG_ERROR` nếu sai).
  - Từ chối `NaN`, `infinity`, hoặc equity `<= 0` (`ACCOUNT_EQUITY_NON_POSITIVE`).
  - Phân biệt rõ `0.0` và `None` (không dùng truthiness `val or default`).
- **Callers**: `CG01ReplayExecutionAdapter.on_bar_close`, unit tests.
- **Test bảo vệ**: `tests/test_v16_8_capital_basis.py::test_resolve_replay_sizing_capital_pure_helper`, `test_zero_capital_fails_closed_without_fallback`.

#### `EffectiveReplayFinancialConfig`
- **Loại**: [HIỆN CÓ — MỞ RỘNG]
- **Thuộc tính mới bổ sung**:
  - `capital_mode: str = "CURRENT_ACCOUNT_BALANCE"`
  - `contract_multiplier: float = 1.0`
  - `execution_model: str = "ASSUMED_OHLC_PATH"`
- **Callers**: `resolve_replay_financial_config`, `CG01ReplayExecutionAdapter.__init__`.

#### `resolve_replay_financial_config(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**: Thêm tham số `capital_mode` và `execution_model`; xác thực `execution_model` phải thuộc `{"CLOSE_QUOTE", "ASSUMED_OHLC_PATH"}` (`CONFIG_ERROR` nếu không hợp lệ).
- **Callers**: `CG01ReplayExecutionAdapter.__init__`, `tools/run_v16_8_acceptance.py`.
- **Test bảo vệ**: `tests/test_v16_8_execution_model_and_time.py::test_unknown_execution_model_rejected`.

---

### 2. `backend/services/scalp_watcher_orchestrator.py`

#### `ScalpWatcherOrchestrator.evaluate_cycle(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Input**: `execution_financial_context: Optional[ReplayFinancialContext]`, các tham số nến, quote, symbol.
- **Thay đổi**:
  - Thay thế kiểm tra truthiness bằng kiểm tra tường minh `if capital_override is not None:`.
  - Kiểm tra xác thực vốn: nếu `capital <= 0`, trả về ngay lập tức `status="BLOCKED"`, `reason="ACCOUNT_EQUITY_NON_POSITIVE"`, tuyệt đối không fallback về 5000 hay 1000.
  - Ghi nhận `capital_source` (`"REPLAY_EXPLICIT_CAPITAL"` hoặc `"ACCOUNT_AUDIT"`) và `capital_mode` vào `policy_dict`.
- **Callers**: `CG01ReplayExecutionAdapter.on_bar_close`, `main.py`, test suites.
- **Test bảo vệ**: `tests/test_v16_8_capital_basis.py::test_account_reduction_scales_risk_in_current_account_mode`, `test_account_gain_scales_risk_in_current_account_mode`, `test_zero_capital_fails_closed_without_fallback`.

---

### 3. `backend/lab/cg01_replay_execution_adapter.py`

#### `CG01ReplayExecutionAdapter.__init__(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**: Bổ sung tham số `capital_mode: Any = "CURRENT_ACCOUNT_BALANCE"` và chuyển tiếp cho `resolve_replay_financial_config`.
- **Callers**: `CG01ReplayDriver.run`, test suites.

#### `CG01ReplayExecutionAdapter.bootstrap_replay_settings(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**: Thêm cờ `self._bootstrapped = True`. Gọi lại phương thức trên cùng một run không bao giờ reset `current_equity` hay xóa lịch sử giao dịch (idempotent).
- **Callers**: `__init__`, test recovery.
- **Test bảo vệ**: `tests/test_v16_8_capital_basis.py::test_bootstrap_idempotency`.

#### `CG01ReplayExecutionAdapter.on_bar_close(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**: Trong chế độ `CURRENT_ACCOUNT_BALANCE`, truyền `capital_usdt=None` vào `ReplayFinancialContext` để Orchestrator tính toán kích thước vị thế (sizing) hoàn toàn dựa trên `audit.current_equity` hiện hành của tài khoản thay vì giữ cố định `self.initial_capital`.
- **Callers**: `CG01ReplayDriver.run`.
- **Test bảo vệ**: `tests/test_v16_8_capital_basis.py::test_account_reduction_scales_risk_in_current_account_mode`.

#### `CG01ReplayExecutionAdapter.on_quote(...)`
- **Loại**: [HIỆN CÓ — TÁI CẤU TRÚC TOÀN DIỆN]
- **Thay đổi**:
  1. **Điều phối mô hình trước (Model Dispatch First)**: Xác định `execution_model` trước mọi xử lý vị thế.
  2. **CLOSE_QUOTE Model**: Chỉ đánh giá quote tại giá close (`close_px`). Đặt `candle_high = close_px` và `candle_low = close_px` khi gọi `PaperBroker.process_price_tick`, đảm bảo râu nến (wicks) tuyệt đối không kích hoạt exit hay fill lệnh.
  3. **ASSUMED_OHLC_PATH Model**: Xử lý 4 waypoints theo chuỗi thời gian tăng dần đơn điệu (`t0 <= t1 <= t2 <= t3 == now_ms`).
  4. **Thời gian không đảo chiều**: Loại bỏ lệnh gọi `self.clock.set_time(now_ms)` ở đầu hàm trước khi chạy vòng lặp waypoints xuất phát từ `bar_open`.
  5. **Config Validation**: Ném `ValueError("CONFIG_ERROR: ...")` nếu gặp model lạ.
- **Callers**: `CG01ReplayDriver.run`, `CG01ReplayExecutionAdapter.on_candle`.
- **Test bảo vệ**: `tests/test_v16_8_execution_model_and_time.py` (tất cả 6 bài test).

#### `CG01ReplayExecutionAdapter.collect_state(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**:
  - Sắp xếp danh sách lệnh đóng `closed_orders` theo `(int(o.closed_at or 0), str(o.id or ""))`.
  - Trích xuất `contract_multiplier` và `multiplier` từ `cost_snapshot` vào `open_position`.
  - Ưu tiên phí thực tế `entry_fee_usdt` đã lưu trữ trong snapshot trước khi ước tính.
- **Callers**: `CG01ReplayDriver.run`, reporting tools.
- **Test bảo vệ**: `tests/test_v16_8_statistics_and_accounting.py::test_contract_multiplier_in_collect_state_and_equity_mark`, `test_actual_persisted_entry_fee_used`, `test_closed_trades_chronological_sort`.

---

### 4. `backend/lab/cg01_replay_driver.py`

#### `compute_equity_mark(cash, open_position, current_price)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**: Trích xuất `mult = float(open_position.get("contract_multiplier", open_position.get("multiplier", 1.0)) or 1.0)` để quy chiếu PnL chưa đóng chuẩn xác theo hợp đồng có multiplier khác 1.
- **Callers**: `CG01ReplayDriver.run`, tests.
- **Test bảo vệ**: `tests/test_v16_8_statistics_and_accounting.py::test_contract_multiplier_in_collect_state_and_equity_mark`.

#### `_is_in_ny_session(ts_val, policy=None)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**: Chuyển sang sử dụng `TradingPolicyService.get_ny_session_window(policy=policy, now_ms=now_ms)` để tôn trọng cấu hình phiên động từ policy thay vì hardcode 08:00 - 17:00.
- **Callers**: `CG01ReplayDriver.run`.
- **Test bảo vệ**: `tests/test_v16_8_statistics_and_accounting.py::test_session_policy_configurable`.

#### `CG01ReplayDriver.__init__(...)`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**: Bổ sung `capital_mode`, `execution_model`, `trading_day_timezone` và chuyển tiếp cho adapter.
- **Callers**: `tools/run_v16_8_acceptance.py`, tests.

#### `CG01ReplayDriver.run()`
- **Loại**: [HIỆN CÓ — SỬA ĐỔI]
- **Thay đổi**:
  - Lấy `effective_daily_policy = resolve_effective_daily_policy(lab_db)` và `effective_tz_name`.
  - Gọi trực tiếp `resolve_trade_day_bounds(now_ms, effective_tz_name)` cho từng nến để phân nhóm ngày giao dịch và xác định ranh giới ngày, loại bỏ việc chuyển đổi gián tiếp qua chuỗi ngày NY.
  - Thu thập kết quả trả về từ `adapter.on_bar_close(bar_event)` vào danh sách quan sát phễu quyết định `decision_observations` và tổng hợp vào `rejection_funnel`.
  - Phân loại rõ ràng các giai đoạn phễu: `CONTEXT_NOT_READY`, `OBSERVING_WAITING_TRIGGER`, `GUARD_BLOCKED`, `ARMED_CANDIDATES`, `EXPIRED_BEFORE_FILL`, `CANCELLED_ORDERS`.
- **Callers**: `tools/run_v16_8_acceptance.py`, tests.
- **Test bảo vệ**: `tests/test_v16_8_statistics_and_accounting.py::test_daily_stats_canonical_timezone_vietnam`, `test_missing_dataset_returns_blocked_data`.

---

### 5. `tools/run_v16_8_acceptance.py`
- **Loại**: [MỚI]
- **Chức năng**:
  - Thực thi kiểm thử tĩnh `git diff --check`.
  - Chạy 3 test suites mới của V16.8 và 6 test suites hồi quy V16.
  - Phân tích machine-readable test outputs để xác minh từng cổng kỹ thuật có đủ các case bắt buộc (Evidence-based Gates).
  - Chạy mô phỏng mẫu và serialize `EffectiveReplayFinancialConfig`.
  - Xuất 8 tệp artifact và báo cáo nghiệm thu `docs/V16_8_ACCEPTANCE_REPORT.md`.
  - Thoát mã lỗi khác 0 nếu có bất kỳ cổng kỹ thuật bắt buộc nào không đạt.
