# BÁO CÁO NGHIỆM THU KỸ THUẬT V16.4 — CG-01 CAUSAL ENTRY, ORDER TYPE & REPLAY EXECUTION REPAIR

**Thời điểm tạo**: 2026-10-16 06:47:05 UTC  
**Commit SHA**: `bc6715deb78f7e3f5898e7d20211afbbaf27a8b2`  
**Run ID**: `run_v16_4_1792133195`  
**Technical Acceptance**: `PASS`  
**Replay Validity**: `BLOCKED_DATA`  
**Strategy Performance**: `NOT_EVALUATED`  

---

## 1. Tóm tắt Dành cho Người Dùng Mới (Executive Summary)

- **Bốn nhóm lỗi cốt lõi đã được sửa triệt để**:
  1. **Nến chưa đóng không được xác nhận Retest**: Hàm `evaluate_cg01_candidate` lọc context qua `build_cg01_closed_context` và hàm `resolve_cg01_retest` chỉ chấp nhận nến đóng (`close_time_ms <= as_of_ms`). Nến mở ở T không thể kích hoạt EXECUTABLE tại T.
  2. **Giá Planned Entry thị trường sau Retest**: Khi ở mode `CONFIRMED_RETEST_MARKET`, planned entry dùng giá quote executable thực tế (`ask + slippage` cho LONG, `bid - slippage` cho SHORT) và tính lại net R:R qua calculator chính danh, thay vì dùng giá CE làm giá fill giả tưởng.
  3. **Đồng bộ hóa Order Type**: Khắc phục hardcode `'MARKET'` trong `_arm_paper_candidate`. Cả 3 vị trí (`build_arm_plan_snapshot`, `models.PaperOrder`, và `event payload`) đều lưu chính xác `order_type='LIMIT'` cho Limit at CE và `'MARKET'` cho Market Retest.
  4. **Đối soát Thống kê Replay & Trạng thái**: `total_fills` được tính trung thực theo `closed_trades + open_positions`. Khi thiếu dataset M1 thật, trạng thái là `BLOCKED_DATA` và các metrics là `null` (không báo PnL +0.00 / 0% DD giả mạo).
- **Kiểm thử Kỹ thuật Nền tảng**:
  - 18 suites kiểm thử backend với 100% tests PASS (không hồi quy, không skip).
  - 25 tests mới V16.4 kiểm chứng chặt chẽ nến đóng, order type persistence, broker tick execution qua `ExecutionCoordinator`, và ledger reconciliation.

## 2. Bảng Đối chiếu Trước và Sau Sửa (Reproduction Matrix)

| ID Lỗi | File / Vị trí | Hành vi Trước Sửa (Baseline) | Hành vi Sau Sửa (V16.4) | Test Chứng minh |
|---|---|---|---|---|
| **Retest Nến Mở (Rule 007)** | `cg01_engine.py` / `cg01_context_adapter.py` | Retest bar mở tại T, đóng tại T+60s; gọi evaluator tại T vẫn trả EXECUTABLE (lookahead). | Evaluator tại T trả `WAIT_ENTRY` (reason `WAIT_RETEST`), chỉ trả EXECUTABLE sau khi nến đã đóng tại T+60s. | `test_rule_141_regression_retest_open_bar_not_executable` |
| **Giá Market Planned Entry (Rule 008)** | `cg01_engine.py` (`evaluate_cg01_candidate`) | Mode `CONFIRMED_RETEST_MARKET` gán `planned_entry = CE (2623.75)` dù quote ask là 2625.60. | `planned_entry` lấy `ask + slippage` thực tế, tính lại net R:R; nếu giá chạy xa sẽ từ chối (`RR_BELOW_MINIMUM`). | `test_rule_145_market_price_long`, `test_rule_146_market_price_short` |
| **Hardcode Order Type (Rule 009)** | `scalp_watcher_orchestrator.py` (`_arm_paper_candidate`) | Hardcoded `order_type='MARKET'` trong snapshot, model DB và event bus payload. | Phân giải `resolved_order_type = candidate_execution_order_type(cand)` đồng bộ cả 3 nơi (`LIMIT` cho CE, `MARKET` cho retest). | `test_rule_147_limit_type_persistence`, `test_rule_160_restart_persistence` |
| **Khớp Lệnh Limit Coordinator (Rule 066, 067)** | `execution_coordinator.py` (`evaluate_orders_sync`) | Limit có nguy cơ bị khớp sai hoặc khớp ngay lập tức khi chưa chạm giá. | BUY LIMIT chỉ kích hoạt khi `ask <= planned_entry`; SELL LIMIT chỉ kích hoạt khi `bid >= planned_entry`. | `test_rule_148_long_limit_touch_fill`, `test_rule_149_short_limit_touch_fill` |
| **Thống kê Replay Fills (Rule 010, 111, 112)** | `cg01_replay_driver.py` (`run`) | `total_fills = len(closed_trades)`, bỏ sót vị thế mở cuối kỳ; metrics trả 0.0 khi thiếu dữ liệu. | `total_fills = closed + open`; metrics trả `None` (null) khi `BLOCKED_DATA`. | `test_rule_163_replay_missing_data`, `test_rule_166_open_end_counts` |

## 3. Bản đồ Thực thi Thực tế (Implementation Map)

1. **`backend/services/cg01_types.py`**:
   - Bổ sung dataclass `CG01EntryIntent` lưu trữ: `entry_mode`, `order_type`, `reference_ce`, `planned_entry`, `price_basis`, `trigger_known_at`, `valid_until`, `quote_timestamp`.
   - Cung cấp các helpers chuẩn hóa: `resolve_cg01_order_type(entry_mode)` và `candidate_execution_order_type(cand)`.
2. **`backend/services/scalp_m1_model_service.py`**:
   - Bổ sung các trường optional cho `ScalpCandidate`: `order_type: str = 'MARKET'`, `entry_intent: Optional[Dict[str, Any]] = None`, `reference_ce: Optional[float] = None`.
3. **`backend/services/cg01_context_adapter.py`**:
   - Bổ sung `CG01RetestResult` và hàm `resolve_cg01_retest(closed_m1, fvg, direction, as_of_ms, config, structural_sl)`: chỉ xét nến đóng sau FVG formation time và đóng trước hoặc tại `as_of_ms`.
4. **`backend/services/cg01_engine.py`**:
   - Chốt chặn ngay đầu `evaluate_cg01_candidate`: gọi `build_cg01_closed_context(context, as_of_ms=t_now)`. Toàn bộ chuỗi detectors phía sau chỉ đọc context đã lọc nến đóng.
   - Phân biệt rõ hai entry mode: `LIMIT_AT_CE_AFTER_CONFIRMATION` đặt limit tại CE; `CONFIRMED_RETEST_MARKET` gọi `resolve_cg01_retest` và định giá theo quote executable bid/ask + slippage.
   - Ghi nhận `last_rejection_stage` và `last_rejection_reason` (ví dụ `RR_BELOW_MINIMUM` khi giá thị trường chạy xa khỏi FVG).
5. **`backend/services/scalp_watcher_orchestrator.py`**:
   - Cập nhật `_arm_paper_candidate` sử dụng `resolved_order_type = candidate_execution_order_type(cand)` cho cả 3 vị trí: snapshot, `models.PaperOrder`, và `event_bus.publish_event`.
6. **`backend/paper_broker.py`**:
   - Chuyển tiếp đầy đủ `bar_duration_ms`, `now_ms`, `clock`, `session_tag`, `tick_timestamp` vào `TradeLifecycleService.process_exit_tick`.
7. **`backend/lab/cg01_replay_driver.py`**:
   - Kiểm tra timestamps không giảm và phát hiện conflicting duplicate timestamps -> trả `BLOCKED_DATA`.
   - Ghi nhận cờ `is_ambiguous` và `ambiguity_reason` khi xảy ra collision SL và TP trong cùng một nến.
   - Sửa công thức `winrate`: chỉ tính trade có `net_pnl_usd > 0.0` là win sau khi trừ đầy đủ phí.

## 4. Chi tiết Kết quả Kiểm thử Kỹ thuật (Technical Test Results)

| Bước Kiểm tra | Lệnh Thực thi | Exit Code | Thời gian | Trạng thái |
|---|---|---|---|---|
| `git_diff_check` | `git diff --check` | `0` | `0.066s` | **PASS** |
| `backend_tests` | `D:\build app-crypto\aurum-desk\backend\venv\Scripts\python.exe -m pytest tests/test_v16_opportunity_engine.py tests/test_v15_0_scalp_types.py tests/test_v15_1_execution_lifecycle.py tests/test_v15_2_liquidity_engine.py tests/test_v15_3_causal_wiring.py tests/test_v15_4_causal_entry.py tests/test_v14_4_1_production_pipeline.py tests/test_v14_4_1_entry_guards.py tests/test_v16_2_repro_and_micro_break.py tests/test_v16_2_causal_evidence_chain.py tests/test_v16_2_cg01_long_short.py tests/test_v16_2_cg01_guards_lifecycle.py tests/test_v16_3_cg01_contracts.py tests/test_v16_3_cg01_pipeline.py tests/test_v16_3_cg01_replay.py tests/test_v16_4_cg01_causal_entry.py tests/test_v16_4_cg01_broker_execution.py tests/test_v16_4_cg01_replay_ledger.py -q -p no:cacheprovider` | `0` | `9.574s` | **PASS** |
| `frontend_typecheck` | `npx.cmd tsc -b` | `0` | `6.863s` | **PASS** |
| `frontend_lint` | `npx.cmd oxlint` | `0` | `1.085s` | **PASS** |
| `frontend_vitest` | `npx.cmd vitest run` | `0` | `3.289s` | **PASS** |
| `frontend_build` | `npm.cmd run build` | `0` | `7.605s` | **PASS** |

## 5. Trạng thái Vận hành & Giới hạn Cảnh báo (Operational Disclosure)

- **Trạng thái CG-01 hiện tại**: Giữ nguyên trạng thái mặc định **`OFF`** trong cấu hình cơ sở dữ liệu và production. Tuyệt đối không tự động kích hoạt tài khoản thật.
- **Mục tiêu 2 fills/ngày, tối đa 3 fills/ngày**: Đây là quota giới hạn rủi ro vận hành theo cấu hình của người dùng, KHÔNG phải cam kết hay lời hứa hẹn tần suất giao dịch của thị trường. Nếu thị trường không tạo setup đạt chuẩn causal và net R:R >= 2.0, hệ thống tuân thủ kiên nhẫn đứng ngoài.
- **Hiệu suất Chiến lược (Strategy Performance)**: Hiện tại ghi nhận **`NOT_EVALUATED`** do chưa cung cấp tệp dữ liệu M1 lịch sử chuẩn. Các kiểm thử unit/integration với dữ liệu synthetic chỉ chứng minh tính đúng đắn kỹ thuật của logic thực thi, không phải bằng chứng về khả năng sinh lời.

---
*Báo cáo được tạo tự động bởi tools/run_v16_4_acceptance.py.*