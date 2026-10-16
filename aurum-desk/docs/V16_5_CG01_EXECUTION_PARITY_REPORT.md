# BÁO CÁO NGHIỆM THU KỸ THUẬT V16.5 — CG-01 EXPIRY, SLIPPAGE & SHARED REPLAY EXECUTION

**Base Commit**: `3ae49911`  
**Tested Commit / Tree**: `3ae4991190d09dc04f8f30fd4ebfc565bb1012d8`  
**Run ID**: `run_v16_5_1792137057`  
**Thời điểm tạo**: 2026-10-16 07:51:35 UTC  
**Technical Acceptance**: `PASS`  
**Replay Validity**: `BLOCKED_DATA`  
**Strategy Performance**: `NOT_EVALUATED`  

---

## 1. Tóm tắt Kết quả Nghiệm thu theo Gates (Appendix E)

- **Expiry Pipeline**: **PASS** — 7/7 tests passed (`test_v16_5_expiry_pipeline.py`). Candidate deadline lan truyền chính xác sang PaperOrder.expires_at, snapshot và event bus payload mà không bị hardcode now+2h hay reset tuổi trigger.
- **Cost Consistency**: **PASS** — 6/6 tests passed (`test_v16_5_cost_consistency.py`). Slippage 0.0 được bảo toàn nghiêm ngặt (không ép về 0.10); fill price LONG market = ask + slippage, SHORT = bid - slippage; không trừ phí trượt giá 2 lần khi entry_has_slippage=True.
- **Broker Tick Execution**: **PASS** — ExecutionCoordinator xử lý lệnh pending theo đúng ranh giới thời gian (now >= expires_at hủy trước khi fill) và quote bid/ask thực tế.
- **Replay Shared Execution**: **PASS** — 6/6 tests passed (`test_v16_5_replay_shared_execution.py`). CG01ReplayDriver và CG01ReplayExecutionAdapter dùng chung toàn bộ ScalpWatcherOrchestrator, ExecutionCoordinator, PaperBroker và TradeLifecycleService trên SQLite lab.
- **Ledger Reconciliation**: **PASS** — 6/6 tests passed (`test_v16_5_ledger_reconciliation.py`). Đối soát hoàn toàn total_fills = closed + open; tính toán net PnL đối chiếu độc lập; vị thế mở cuối kỳ báo winrate=null.
- **Legacy Regression**: **PASS** — 100% test suites hồi quy (V14, V15, V16) đều PASS không lỗi.

---

## 2. Bảng Đối chiếu Kỹ thuật Trước và Sau Sửa (Root Cause & Fixes)

| ID Nhóm | File / Hàm | Hành vi Trước Sửa (Baseline) | Hành vi Sau Sửa (V16.5) | Bằng chứng Kiểm thử |
|---|---|---|---|---|
| **Candidate Expiry Propagation (Rule 008, 031-034)** | `scalp_watcher_orchestrator.py` (`_arm_paper_candidate`) | Hardcode `expires_at = now_ms + 2*3600*1000`, bỏ qua candidate deadline từ trigger. | Gọi `resolve_candidate_order_expiry` lan truyền `cand.valid_until_ms` chính xác sang order, snapshot và event payload. | `test_v16_5_expiry_pipeline.py` (7 tests) |
| **Exact Deadline Boundary (Rule 036, 037)** | `execution_coordinator.py` (`evaluate_orders_sync`) | Kiểm tra expiry bằng `>` và return sớm khi quote invalid hoặc có vị thế mở. | Kiểm tra `>=` exact boundary; tách housekeeping expiry cho remaining_armed trước khi validate quote / single position. | `test_rule_036_coordinator_exact_boundary`, `test_rule_037_expiry_housekeeping_on_stale_quote` |
| **Authoritative Slippage Resolution (Rule 009, 051-053)** | `cg01_types.py`, `cg01_engine.py`, `domain_calculator.py` | Biểu thức `getattr(costs, 'slippage_usd', 0.10) or 0.10` làm `0.0` bị ép thành `0.10`. | Dùng `resolve_slippage_usd`: bảo toàn `0.0`, float parsing, reject negative/NaN/inf. | `test_rule_052_resolve_slippage_usd`, `test_rule_060_coordinator_zero_slippage_execution` |
| **Directional Market Pricing (Rule 056-058)** | `execution_coordinator.py`, `order_plan_snapshot_service.py` | Chi phí trượt giá dùng default hoặc bị ghi đè không đồng nhất giữa planned và actual. | LONG market = `ask + slippage`, SHORT market = `bid - slippage`; snapshot lưu trữ đúng snapshot hiệu lực. | `test_rule_057_058_market_entry_pricing_directional`, `test_rule_060` |
| **No Double Slippage Charge (Rule 062, 063)** | `domain_calculator.py`, `execution_coordinator.py` | Khi giá đã gồm slippage, nếu tính lại risk/reward có thể trừ slippage 2 lần. | Khi `entry_has_slippage=True`, `entry_slippage_usdt = 0.0`; giữ nguyên SL exit slippage. | `test_rule_062_no_double_entry_slippage_in_calculator` |
| **Replay Architecture Parity (Rule 010, 081-100)** | `cg01_replay_driver.py`, `cg01_replay_execution_adapter.py` | Driver cũ tự duy trì dictionary vị thế và tự tính gross PnL/fees độc lập. | Chuyển toàn bộ quyền thực thi sang `CG01ReplayExecutionAdapter` gọi trực tiếp orchestrator, coordinator và PaperBroker. | `test_v16_5_replay_shared_execution.py` (6 tests) |
| **Ledger Reconciliation (Rule 121-135)** | `cg01_replay_execution_adapter.py` | Không tính trades mở cuối kỳ vào fills; winrate tính cả trường hợp net negative do phí. | `total_fills = closed + open`; winrate strictly yêu cầu `net_pnl_usd > 0.0`; range kết thúc khi còn vị thế mở báo winrate=null. | `test_v16_5_ledger_reconciliation.py` (6 tests) |

---

## 3. Bản đồ Triển khai Thực tế (Implementation Map)

1. **`backend/services/cg01_types.py`**:
   - Cung cấp `ExecutionDeadline` và `resolve_cg01_execution_deadline`: xác định thời hạn thực thi nhỏ nhất giữa TTL từ trigger, max trigger age, và session window end.
   - Cung cấp `resolve_slippage_usd`: bảo toàn giá trị `0.0`, từ chối giá trị âm, vô hạn hoặc NaN.
   - Cung cấp `resolve_candidate_order_expiry`: bảo đảm CG-01 candidate có thời hạn hợp lệ trước khi ARM.
2. **`backend/domain_calculator.py`**:
   - Bổ sung trường `slippage_usd: float = 0.10` vào `CalculationResult`.
   - Lan truyền `costs.slippage_usd` vào `CalculationResult`.
3. **`backend/services/order_plan_snapshot_service.py`**:
   - Cập nhật `build_arm_plan_snapshot` và `build_fill_plan_snapshot` sử dụng `resolve_slippage_usd` bảo toàn giá trị `0.0`.
4. **`backend/services/cg01_evidence_adapter.py`**:
   - Mở rộng `build_cg01_snapshot` ghi nhận `trigger_known_at`, `execution_valid_until`, và `deadline_constraints` vào bằng chứng lệnh.
5. **`backend/services/cg01_engine.py`**:
   - Định giá planned entry market dùng `resolve_slippage_usd`.
   - Đánh giá candidate deadline qua `resolve_cg01_execution_deadline`.
   - Trả `CG01Stage.EXPIRED` khi `t_now >= valid_until`.
6. **`backend/services/scalp_watcher_orchestrator.py`**:
   - `_arm_paper_candidate` phân giải expiry qua `resolve_candidate_order_expiry`.
   - Từ chối các candidate đã hết hạn.
   - Lưu trữ chính xác `PaperOrder.expires_at`, snapshot, và event payload cùng deadline.
7. **`backend/services/execution_coordinator.py`**:
   - Thực hiện housekeeping hủy các lệnh pending quá hạn (`now >= expires_at`) trước khi kiểm tra quote hay vị thế mở.
   - Phân giải `order_slip` từ snapshot bằng `resolve_slippage_usd`.
   - Định giá fill price: LONG market = `ask + order_slip`, SHORT market = `bid - order_slip`.
   - Truyền `entry_has_slippage=True` và `costs=order_costs` vào cả hai lượt tính `calculate_risk_reward`.
8. **`backend/lab/cg01_replay_execution_adapter.py`**:
   - Tạo adapter điều phối `on_bar_close`, `on_quote`, `collect_state`, và `finalize_run` trên SQLite lab.
   - Gọi trực tiếp `ExecutionCoordinator.evaluate_orders_sync` và `PaperBroker.process_price_tick`.
   - Tổng hợp chỉ số kế toán chính xác: PnL, drawdowns, winrate theo net PnL > 0.
9. **`backend/lab/cg01_replay_driver.py`**:
   - Tích hợp chạy simulation thông qua `CG01ReplayExecutionAdapter`.

---

## 4. Chi tiết Kết quả Kiểm thử Kỹ thuật (Technical Test Suite Results)

| Bước Kiểm tra | Lệnh Thực thi | Exit Code | Thời gian | Trạng thái |
|---|---|---|---|---|
| `git_diff_check` | `git diff --check` | `0` | `0.057s` | **PASS** |
| `backend_tests` | `D:\build app-crypto\aurum-desk\backend\venv\Scripts\python.exe -m pytest tests/test_v16_5_expiry_pipeline.py tests/test_v16_5_cost_consistency.py tests/test_v16_5_replay_shared_execution.py tests/test_v16_5_ledger_reconciliation.py tests/test_v16_4_cg01_broker_execution.py tests/test_v16_4_cg01_causal_entry.py tests/test_v16_4_cg01_replay_ledger.py tests/test_v16_3_cg01_contracts.py tests/test_v16_3_cg01_pipeline.py tests/test_v16_3_cg01_replay.py tests/test_v16_2_repro_and_micro_break.py tests/test_v16_2_causal_evidence_chain.py tests/test_v16_2_cg01_long_short.py tests/test_v16_2_cg01_guards_lifecycle.py tests/test_v16_opportunity_engine.py tests/test_v15_1_execution_lifecycle.py tests/test_v14_4_1_production_pipeline.py tests/test_v14_2_entry_guard.py -q -p no:cacheprovider` | `0` | `17.353s` | **PASS** |
| `frontend_typecheck` | `npx.cmd tsc -b` | `0` | `6.253s` | **PASS** |
| `frontend_lint` | `npx.cmd oxlint` | `0` | `1.191s` | **PASS** |
| `frontend_vitest` | `npx.cmd vitest run` | `0` | `3.411s` | **PASS** |
| `frontend_build` | `npm.cmd run build` | `0` | `9.567s` | **PASS** |

---

## 5. Trạng thái Vận hành & Cảnh báo Minh bạch (Operational Disclosure)

- **Bảo toàn Cấu hình Production**: Tính năng CG-01 tiếp tục duy trì trạng thái mặc định **`OFF`** trong database và cấu hình hệ thống.
- **Chính sách Giới hạn Lệnh**: Mục tiêu 2 fills/ngày và cap tối đa 3 fills/ngày là giới hạn quản trị rủi ro; tuyệt đối không ép lệnh khi thị trường không có tín hiệu đạt chuẩn.
- **Đánh giá Hiệu suất Replay**: Trạng thái hiệu suất là **`NOT_EVALUATED`** do chưa nạp tệp dữ liệu thị trường M1 thật. Toàn bộ các test pass ở trên là kiểm thử độ đúng kỹ thuật trên fixtures giả lập độc lập.

---
*Báo cáo được tạo tự động bởi tools/run_v16_5_acceptance.py.*