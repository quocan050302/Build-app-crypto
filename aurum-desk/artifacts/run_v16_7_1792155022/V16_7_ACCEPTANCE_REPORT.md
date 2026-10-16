# BÁO CÁO NGHIỆM THU KỸ THUẬT V16.7 — EXECUTION GUARD REPAIR, RISK AUTHORITY AND VERIFIED REPLAY

**Commit Base**: `7b721d58de447c351c4805884baede95a4eae6c7`  
**Commit Được Kiểm Thử**: `7b721d58de447c351c4805884baede95a4eae6c7`  
**Diff Hash Working Tree**: `e5675d8cbb3a4ebb227001eae59a2734cdf181347e6978bb1f92752992b0b7bb`  
**Run ID**: `run_v16_7_1792155022`  
**Thời điểm tạo**: 2026-10-16 12:50:46 UTC  
**Technical Status**: `PASS`  
**Historical Status**: `DATA_SYNTHESIZED_DEMO`  
**Performance Status**: `EVALUATED_DEMO`  

---

## 1. Tóm tắt Kết quả Nghiệm thu theo Gates Kỹ thuật

- **Execution Guard Parity**: **PASS** — Xóa bỏ hoàn toàn đường bypass trực tiếp (`Case B`), triệt tiêu `TypeError` do truyền thừa tham số `recovery_metadata`. 100% lệnh khớp (fills) trong replay đều đi qua `ExecutionCoordinator.evaluate_orders_sync()` và chịu đầy đủ các chốt chặn `BEFORE_FILL` (News blackout, Lesson RED/YELLOW, Daily cap 3 lệnh, 1 vị thế mở duy nhất, Expiry deadline, Feed/Quote sanity).
- **Risk Authority Reconciliation**: **PASS** — Khôi phục thẩm quyền rủi ro chuẩn tắc: `RiskSettings.risk_pct` (từ DB) là thẩm quyền mặc định trong forward/live paper trading; `M1ScalpConfig.fallback_risk_pct` chỉ đóng vai trò dự phòng cấp 3 khi DB trống; Replay chỉ override khi có `ReplayFinancialContext` tường minh. Quy đổi chính xác 0.10% rủi ro tương ứng 5.0 USD trên vốn 5000 USD.
- **Replay Costs & Config Immutability**: **PASS** — Không làm biến tính (mutate) đối tượng cấu hình của caller (`CG01Config`, `CostAssumptions`). Bảo toàn chính xác phí 0 bps (0.0), 0.5 bps (0.00005) và trượt giá 0.0 USD.
- **Measurement Integrity & Rollover**: **PASS** — `DayAudit` qua các ngày giữ nguyên equity lũy kế, không bị reset `initial_equity` về vốn ban đầu của toàn kỳ. Bảo toàn `cash = 0.0` (không ép về `initial_capital`). Ranh giới ngày giao dịch sử dụng `resolve_trade_day_bounds` hỗ trợ chuẩn IANA timezone và chuyển dịch DST (23h/25h).
- **Full Regression Suite**: **PASS** — Toàn bộ các bộ kiểm thử hồi quy từ V16.1 đến V16.7 (125 tests) đều đạt trạng thái PASS.

---

## 2. Bằng chứng Thực nghiệm Khách quan (Empirical Evidence)

### A. Loại bỏ Đường Bypass & Tuân thủ Guard Trước Khớp (Guard Parity Proof)
Thiết lập kiểm thử trường hợp pending order gặp nến quét qua cả Entry và SL:
- **Hành vi cũ (V16.6)**: Bỏ qua `ExecutionCoordinator`, gọi thẳng `execute_fill(..., recovery_metadata=...)` gây lỗi `TypeError` hoặc khớp lệnh ngay cả khi đang có tin tức đỏ / chạm daily cap.
- **Hành vi mới (V16.7)**: Nến được phân rã thành các waypoints tất định (`WP0: open -> WP1: extreme 1 -> WP2: extreme 2 -> WP3: close`). Từng waypoint được gửi qua `ExecutionCoordinator.evaluate_orders_sync()`. Nếu tại thời điểm waypoint lệnh bị chặn bởi News / Lessons / Daily Cap / Expiry, coordinator từ chối khớp lệnh hợp lệ. Nếu khớp hợp lệ, thoát lệnh nội nến được xử lý qua `PaperBroker.process_price_tick()` gắn cờ `intrabar_ambiguous = True` bảo thủ mà không gây lỗi tham số.

### B. Đối soát Thẩm quyền Rủi ro (Risk Authority Proof)
So sánh kết quả phân giải rủi ro giữa các luồng:
1. **Luồng Live / Forward Paper Trading**:
   - Cấu hình DB `RiskSettings(risk_pct=0.25)`
   - `resolve_execution_risk(db_settings, replay_context=None, fallback_config)`
   - -> Kết quả: `risk_pct = 0.25`, `authority = 'DB_SETTINGS'`. Tuyệt đối không bị fallback ghi đè.
2. **Luồng Replay Lab với Override tường minh**:
   - Truyền `ReplayFinancialContext(risk_pct=0.10, initial_capital=5000.0)`
   - -> Kết quả: `risk_pct = 0.10`, `initial_risk_usd = 5.00`, `authority = 'REPLAY_OVERRIDE'`.
3. **Luồng DB trống (Fallback cấp 3)**:
   - DB rỗng, không có replay context, `fallback_risk_pct = 0.20`
   - -> Kết quả: `risk_pct = 0.20`, `authority = 'FALLBACK_CONFIG'`.

### C. Bằng chứng Bất biến Cấu hình & Chi phí (Cost Immutability Proof)
- Caller truyền `CostAssumptions(maker_fee_rate=0.00005, taker_fee_rate=0.00005, slippage_usd=0.0)`.
- Qua toàn bộ chu kỳ replay, đối tượng của caller được bảo vệ toàn vẹn: `slippage_usd` vẫn là `0.0`, `maker_fee_rate` vẫn là `0.00005`.
- Hai tiến trình replay chạy đồng thời hoặc xen kẽ với các mức vốn khác nhau (`$1,000` và `$10,000`) không gây rò rỉ trạng thái hay ghi đè cấu hình lẫn nhau.

### D. Đối soát Thống kê Ngày, Rollover và Tiền mặt (Rollover & Daily Stats Proof)
- Ranh giới ngày giao dịch được tính toán bởi `resolve_trade_day_bounds`:
  - Hỗ trợ đầy đủ các múi giờ IANA (`America/New_York`, `UTC`, `Asia/Ho_Chi_Minh`).
  - Tự động nhận diện ngày chuyển dịch giờ mùa hè (DST 23 giờ hoặc 25 giờ).
- `DayAudit` qua các ngày ghi nhận đúng `equity_start` của ngày T+1 bằng `equity_close` của ngày T, không tái thiết lập về vốn ban đầu của toàn kỳ.
- Giá trị tiền mặt `cash = 0.0` (cháy tài khoản hoặc rút hết) được giữ nguyên, không bị biểu thức `curr_cash or initial_capital` cưỡng bức hoàn nguyên về `$5000`.

---

## 3. Bảng Chi tiết Kết quả Kiểm thử Kỹ thuật (Technical Test Suite Results)

| Tên Test Suite | Lệnh Thực thi | Exit Code | Thời gian | Passed | Failed | Trạng thái |
|---|---|---|---|---|---|---|
| `git_diff_check` | `git diff --check` | `0` | `0.015s` | - | - | **PASS** |
| `test_v16_7_execution_guard_parity` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_7_execution_guard_parity.py -v` | `0` | `1.549s` | 10 | 0 | **PASS** |
| `test_v16_7_risk_authority` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_7_risk_authority.py -v` | `0` | `1.309s` | 5 | 0 | **PASS** |
| `test_v16_7_replay_costs` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_7_replay_costs.py -v` | `0` | `1.135s` | 6 | 0 | **PASS** |
| `test_v16_6_measurement_integrity` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_6_measurement_integrity.py -v` | `0` | `1.272s` | 6 | 0 | **PASS** |
| `test_v16_5_replay_shared_execution` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_5_replay_shared_execution.py -v` | `0` | `1.611s` | 6 | 0 | **PASS** |
| `test_v16_5_ledger_reconciliation` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_5_ledger_reconciliation.py -v` | `0` | `1.432s` | 6 | 0 | **PASS** |
| `test_v16_5_cost_consistency` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_5_cost_consistency.py -v` | `0` | `1.127s` | 6 | 0 | **PASS** |
| `test_v16_5_expiry_pipeline` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_5_expiry_pipeline.py -v` | `0` | `1.429s` | 7 | 0 | **PASS** |
| `test_v16_4_cg01_replay_ledger` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_4_cg01_replay_ledger.py -v` | `0` | `1.19s` | 10 | 0 | **PASS** |
| `test_v16_4_cg01_broker_execution` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_4_cg01_broker_execution.py -v` | `0` | `1.84s` | 8 | 0 | **PASS** |
| `test_v16_4_cg01_causal_entry` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_4_cg01_causal_entry.py -v` | `0` | `0.971s` | 7 | 0 | **PASS** |
| `test_v16_3_cg01_replay` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_3_cg01_replay.py -v` | `0` | `0.872s` | 5 | 0 | **PASS** |
| `test_v16_3_cg01_pipeline` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_3_cg01_pipeline.py -v` | `0` | `1.408s` | 6 | 0 | **PASS** |
| `test_v16_opportunity_engine` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests/test_v16_opportunity_engine.py -v` | `0` | `1.006s` | 16 | 0 | **PASS** |
| `v16_full_suite` | `/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/bin/python3 -m pytest tests -k v16 -q` | `0` | `4.454s` | 125 | 0 | **PASS** |

---

## 4. Danh sách File Sửa đổi và Bổ sung trong V16.7

1. **`backend/services/plan_financial_context_service.py`**:
   - Bổ sung dataclass `ReplayFinancialContext` và `EffectiveReplayFinancialConfig`.
   - Bổ sung hàm pure function `resolve_replay_financial_config` tạo cấu hình immutable.
   - Bổ sung hàm thẩm quyền `resolve_execution_risk` phân giải rủi ro theo thứ tự ưu tiên chuẩn tắc.
2. **`backend/services/trading_policy_service.py`**:
   - Bổ sung hàm `resolve_trade_day_bounds(timestamp_ms, timezone_name)` hỗ trợ chuẩn IANA và DST.
3. **`backend/services/scalp_watcher_orchestrator.py`**:
   - Nhận diện `execution_financial_context` trong `evaluate_cycle`.
   - Kết nối `resolve_execution_risk` để xác định tỷ lệ rủi ro và chi phí chuẩn tắc.
4. **`backend/lab/cg01_replay_execution_adapter.py`**:
   - Deep-copy caller config ngay tại khởi tạo nhằm bảo vệ tính bất biến.
   - Tách biệt `bootstrap_replay_settings` (chạy 1 lần khi bắt đầu) và `sync_runtime_settings` (chạy trong phiên).
   - Xóa bỏ Case B direct fill bypass; định tuyến toàn bộ waypoints qua `ExecutionCoordinator.evaluate_orders_sync()`.
   - Khử trùng lặp candle events qua `self._processed_candles`.
   - Hỗ trợ mô hình khớp `ASSUMED_OHLC_PATH` và `CLOSE_QUOTE`.
5. **`backend/lab/cg01_replay_driver.py`**:
   - Hỗ trợ hệ số hợp đồng `contract_multiplier` trong tính toán MTM equity.
   - Bảo toàn giá trị tiền mặt thực tế khi `cash = 0.0`.
   - Tích hợp `resolve_trade_day_bounds` vào phân định ngày giao dịch.
   - Ghi nhận `equity_realized_series` và phân loại `rejection_funnel`.
6. **`backend/tests/test_v16_7_execution_guard_parity.py`** (10 tests mới): Kiểm tra toàn diện guard trước khớp qua coordinator.
7. **`backend/tests/test_v16_7_risk_authority.py`** (5 tests mới): Kiểm tra thẩm quyền rủi ro DB vs Replay Context vs Fallback.
8. **`backend/tests/test_v16_7_replay_costs.py`** (6 tests mới): Kiểm tra tính bất biến của chi phí và cấu hình.
9. **`tools/run_v16_7_acceptance.py`**: Acceptance runner tự động hóa cho V16.7.
10. **`docs/V16_7_IMPLEMENTATION_MAP.md`**: Sơ đồ triển khai và ánh xạ hàm chi tiết.

---
*Báo cáo được lập tự động bởi tools/run_v16_7_acceptance.py từ bằng chứng thực thi thực tế.*