# V16.7 Implementation Map: CG-01 Execution Guard Repair, Risk Authority and Verified Replay

**Repository**: `quocan050302/Build-app-crypto`  
**Branch**: `fix/v14-1-rr-flow`  
**Baseline Commit**: `7b721d58de447c351c4805884baede95a4eae6c7`  
**Scope**: Triệt tiêu lỗi TypeError và loại bỏ đường bypass direct fill; khôi phục thẩm quyền rủi ro chuẩn tắc (DB settings priority vs explicit replay financial context); bảo toàn tính bất biến của cấu hình chi phí replay; chuẩn hóa ranh giới ngày IANA/DST và bảo toàn rollover equity.

---

## 1. Mục tiêu & Nguyên tắc Bất biến (Core Invariants)

1. **Đường Entry Duy Nhất (Single Guaranteed Entry Pipeline)**:
   - Toàn bộ lệnh Limit / Market trong replay bắt buộc phải đi qua `ExecutionCoordinator.evaluate_orders_sync()`.
   - Tuyệt đối không còn đường tắt `Case B` (gọi trực tiếp `execute_fill` bỏ qua coordinator).
   - Áp dụng đầy đủ 100% các chốt chặn `BEFORE_FILL`: News blackout, Lesson RED/YELLOW, Daily hard cap (3 lệnh), 1 vị thế mở duy nhất (`active_position_count <= 1`), Expiry deadline, Feed/Quote sanity.
   - Triệt tiêu hoàn toàn lỗi `TypeError` khi gặp nến quét cả Entry và Stop Loss: không truyền tham số lạ vào `execute_fill`.

2. **Thẩm quyền Rủi ro Chuẩn tắc (Canonical Risk Authority Hierarchy)**:
   - Cấp 1 (Ưu tiên cao nhất trong live/forward paper): `RiskSettings.risk_pct` từ cơ sở dữ liệu.
   - Cấp 2 (Chỉ áp dụng trong lab/replay khi có chỉ định rõ ràng): `ReplayFinancialContext.risk_pct`.
   - Cấp 3 (Dự phòng cuối cùng khi DB trống và không có replay context): `M1ScalpConfig.fallback_risk_pct`.
   - Tỷ lệ 0.10% rủi ro trên vốn 5,000 USD quy đổi chính xác ra 5.00 USD `initial_risk_usd`.

3. **Tính Bất biến của Cấu hình Chi phí Replay (Cost & Config Immutability)**:
   - Các đối tượng `CG01Config` và `CostAssumptions` do caller truyền vào tuyệt đối không bị biến tính (mutated).
   - Bảo toàn chính xác các giá trị biên: trượt giá `slippage_usd = 0.0`, phí Maker/Taker `0.0` (0 bps) hoặc `0.00005` (0.5 bps).
   - Hai phiên replay chạy song song hoặc xen kẽ hoàn toàn độc lập, không gây rò rỉ trạng thái.

4. **Chuẩn hóa Ranh giới Ngày & Bảo toàn Tiền mặt (Day Bounds & Cash Integrity)**:
   - Sử dụng `resolve_trade_day_bounds` tính toán theo chuẩn IANA timezone (`America/New_York`), xử lý chính xác ngày chuyển dịch giờ mùa hè (DST 23h hoặc 25h).
   - `DayAudit` qua các ngày giữ nguyên equity lũy kế, không reset `initial_equity` về vốn ban đầu của toàn kỳ.
   - Giá trị tiền mặt `cash = 0.0` được bảo toàn nguyên vẹn, không bị ép về `initial_capital`.

---

## 2. Bảng Ánh xạ Triển khai Chi tiết (Symbol & Function Mapping)

| File | Biểu tượng (Symbol) | Hiện có / Mới | Input | Output | Caller thực tế | Thay đổi cốt lõi | Tests kiểm chứng |
|---|---|---|---|---|---|---|---|
| `backend/services/plan_financial_context_service.py` | `ReplayFinancialContext` | **MỚI** | Dataclass attributes: `initial_capital`, `risk_pct`, `slippage_usd`, `maker_fee_rate`, `taker_fee_rate`, `contract_multiplier`, `execution_model`, `daily_max_cap` | `ReplayFinancialContext` instance | `CG01ReplayExecutionAdapter`, test suites | Cung cấp đối tượng chứa các thông số tài chính tường minh cho môi trường replay | `test_v16_7_risk_authority.py`, `test_v16_7_replay_costs.py` |
| `backend/services/plan_financial_context_service.py` | `EffectiveReplayFinancialConfig` | **MỚI** | Dataclass attributes tương ứng với `frozen=True` | `EffectiveReplayFinancialConfig` instance | `resolve_replay_financial_config`, `CG01ReplayExecutionAdapter` | Cấu hình tài chính đã được chuẩn hóa và đóng băng để ngăn chặn biến tính | `test_v16_7_replay_costs.py` |
| `backend/services/plan_financial_context_service.py` | `resolve_replay_financial_config` | **MỚI** | `(override: Optional[ReplayFinancialContext], fallback_config: Optional[Any] = None)` | `EffectiveReplayFinancialConfig` | `CG01ReplayExecutionAdapter.__init__`, runners | Hàm pure function tạo cấu hình hiệu lực bất biến mà không ghi đè vào tham số đầu vào | `test_v16_7_replay_costs.py` |
| `backend/services/plan_financial_context_service.py` | `resolve_execution_risk` | **MỚI** | `(db_risk_settings: Optional[Any], replay_context: Optional[ReplayFinancialContext], fallback_config: Optional[Any] = None)` | `Tuple[float, float, str]` (`risk_pct`, `initial_risk_usd`, `risk_authority`) | `ScalpWatcherOrchestrator.evaluate_cycle` | Phân giải tỷ lệ rủi ro theo đúng thứ tự thẩm quyền chuẩn tắc: DB settings > Replay Context > Fallback config | `test_v16_7_risk_authority.py` |
| `backend/services/trading_policy_service.py` | `resolve_trade_day_bounds` | **MỚI** | `(timestamp_ms: int, timezone_name: str = "America/New_York")` | `Tuple[str, int, int]` (`date_str`, `day_start_ms`, `day_end_ms`) | `CG01ReplayDriver.run`, chính sách giao dịch | Tính toán ranh giới ngày giao dịch chuẩn IANA, nhận diện chính xác các ngày DST 23h và 25h | `test_v16_7_execution_guard_parity.py`, `test_v16_6_measurement_integrity.py` |
| `backend/services/scalp_watcher_orchestrator.py` | `ScalpWatcherOrchestrator.evaluate_cycle` | **HIỆN CÓ** | `(event, db, execution_financial_context=None)` | `Dict[str, Any]` | `CG01ReplayExecutionAdapter.on_bar_close`, live watcher | Kết nối `resolve_execution_risk`, truyền `slippage_usd`, `costs` và `risk_pct` vào snapshot và công thức định kích thước vị thế | `test_v16_7_risk_authority.py`, `test_v16_6_measurement_integrity.py` |
| `backend/lab/cg01_replay_execution_adapter.py` | `CG01ReplayExecutionAdapter.__init__` | **HIỆN CÓ** | `(config: Optional[CG01Config] = None, financial_context: Optional[ReplayFinancialContext] = None)` | `None` | `CG01ReplayDriver` | Thực hiện deep-copy cấu hình của caller; khởi tạo `effective_config` và `financial_context` | `test_v16_7_replay_costs.py` |
| `backend/lab/cg01_replay_execution_adapter.py` | `CG01ReplayExecutionAdapter.bootstrap_replay_settings` | **MỚI** | `() -> None` | `None` | `CG01ReplayExecutionAdapter.__init__` | Khởi tạo cấu hình tài chính lab ban đầu trong DB (RiskSettings, DayAudit initial_equity) một lần duy nhất | `test_v16_7_replay_costs.py`, `test_v16_7_execution_guard_parity.py` |
| `backend/lab/cg01_replay_execution_adapter.py` | `CG01ReplayExecutionAdapter.sync_runtime_settings` | **HIỆN CÓ** | `(current_equity: Optional[float] = None) -> None` | `None` | `CG01ReplayExecutionAdapter.on_quote`, `on_bar_close` | Cập nhật equity hiện tại vào RiskSettings và DayAudit mà không làm tái thiết lập `initial_equity` của ngày sau về vốn ban đầu của toàn kỳ | `test_v16_7_risk_authority.py` |
| `backend/lab/cg01_replay_execution_adapter.py` | `CG01ReplayExecutionAdapter.on_quote` | **HIỆN CÓ** | `(quote: Dict[str, Any]) -> None` | `None` | `CG01ReplayDriver.run` | Xóa bỏ Case B direct fill bypass; định tuyến các waypoints qua `ExecutionCoordinator.evaluate_orders_sync()`; xử lý thoát lệnh nội nến qua `PaperBroker.process_price_tick()`; hỗ trợ `ASSUMED_OHLC_PATH` và `CLOSE_QUOTE`; khử lặp nến qua `_processed_candles` | `test_v16_7_execution_guard_parity.py` |
| `backend/lab/cg01_replay_driver.py` | `compute_equity_mark` | **HIỆN CÓ** | `(cash: float, open_position: Optional[Dict[str, Any]], current_price: float, contract_multiplier: float = 1.0)` | `float` | `CG01ReplayDriver.run` | Bổ sung hệ số hợp đồng `contract_multiplier` trong công thức tính Mark-to-Market equity | `test_v16_6_measurement_integrity.py` |
| `backend/lab/cg01_replay_driver.py` | `CG01ReplayDriver.run` | **HIỆN CÓ** | `() -> Dict[str, Any]` | `Dict[str, Any]` | Acceptance runner, test scripts | Bảo toàn giá trị tiền mặt thực tế khi `cash = 0.0`; áp dụng `resolve_trade_day_bounds`; ghi nhận `equity_realized_series` và phân loại `rejection_funnel` | `test_v16_6_measurement_integrity.py`, `run_v16_7_acceptance.py` |
| `backend/tests/test_v16_7_execution_guard_parity.py` | Bộ 10 bài test nghiệm thu Guard Parity | **MỚI** | Synthetic candles & quotes | Pytest test results | Pytest runner | Kiểm thử các trường hợp Pending Entry + SL ambiguity không crash, Expiry housekeeping, News blackout, Daily cap, 1-position invariant, Red/Yellow lessons, Quote sanity, CLOSE_QUOTE model | 10 tests PASS |
| `backend/tests/test_v16_7_risk_authority.py` | Bộ 5 bài test thẩm quyền rủi ro | **MỚI** | Mock RiskSettings & ReplayFinancialContext | Pytest test results | Pytest runner | Kiểm thử ưu tiên DB settings trong non-replay, explicit replay context override, model risk cap, 0.10% scaling ($5 trên $5000), xác thực số hữu hạn | 5 tests PASS |
| `backend/tests/test_v16_7_replay_costs.py` | Bộ 6 bài test tính bất biến của chi phí | **MỚI** | Custom CostAssumptions & CG01Config | Pytest test results | Pytest runner | Kiểm thử phí 0 bps, phí 0.5 bps, trượt giá 0.0, bảo toàn nguyên vẹn caller config, phân lập 2 phiên replay xen kẽ | 6 tests PASS |
| `tools/run_v16_7_acceptance.py` | Runner nghiệm thu tự động | **MỚI** | CLI flags (`--dry-run`, `--run-id`, `--m1-dataset`) | Exit code 0/1, JSON/CSV/MD artifacts | Terminal / CI pipeline | Thực thi 16 bước kiểm thử, trích xuất số liệu thực tế, chạy giả lập đối soát và xuất bộ artifact | Toàn bộ các bước PASS |

---

## 3. Lệnh Chạy Kiểm Thử Xác Thực (Verification Commands)

```bash
# 1. Kiểm tra định dạng git diff
git diff --check

# 2. Chạy 3 bộ test mới của V16.7
PYTHONPATH=backend backend/venv/bin/python3 -m pytest backend/tests/test_v16_7_execution_guard_parity.py -v
PYTHONPATH=backend backend/venv/bin/python3 -m pytest backend/tests/test_v16_7_risk_authority.py -v
PYTHONPATH=backend backend/venv/bin/python3 -m pytest backend/tests/test_v16_7_replay_costs.py -v

# 3. Chạy kiểm thử hồi quy toàn diện V16 (125 tests)
PYTHONPATH=backend backend/venv/bin/python3 -m pytest backend/tests -k v16 -q

# 4. Thực thi runner nghiệm thu V16.7
backend/venv/bin/python3 tools/run_v16_7_acceptance.py
```
