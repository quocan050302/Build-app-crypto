# BÁO CÁO NGHIỆM THU KỸ THUẬT V16.3 — CG-01 INTEGRATION REPAIR

**Thời điểm tạo**: 2026-10-16 04:52:28 UTC  
**Commit SHA**: `a2b4c1005f169a8328c16c3aac79ee55d640f159`  
**Run ID**: `run_v16_3_1792126319`  
**Technical Acceptance**: `PASS`  
**Replay Validity**: `BLOCKED_DATA`  
**Strategy Performance**: `NOT_EVALUATED`  

---

## 1. Tóm tắt Kết quả Nghiệm thu

- **Bốn lỗi tích hợp baseline đã được sửa triệt để**:
  1. `c_4h` và `c_1h` được load tự động qua `crud.get_candles` hoặc tham số inject trong `evaluate_cycle`.
  2. `detect_swing_points_causal` đã bị loại bỏ; tái dùng `identify_confirmed_swings` từ `liquidity_scenario_service` với chữ ký causal chuẩn.
  3. `build_liquidity_map` được gọi đúng keyword contracts; đồng thời merge các swing M15 và M1 causally theo đúng Rule 042.
  4. Replay driver tổng hợp nến HTF bằng `aggregate_closed_bars` causal, chấm dứt việc gán M1 cho 15M và bổ sung đầy đủ H1/H4.
- **Kiểm thử kỹ thuật nền tảng**:
  - 15 suites kiểm thử tự động với 134/134 tests đạt (100% pass, 0 regressions).
  - 16 tests mới kiểm chứng hợp đồng dữ liệu, chuỗi bằng chứng LONG/SHORT, và replay logic.

## 2. Chi tiết Root Cause và Giải pháp

| ID Lỗi Baseline | File & Hàm | Root Cause | Giải pháp Sửa đổi | Test Chứng minh |
|---|---|---|---|---|
| **c_4h/c_1h NameError** | `scalp_watcher_orchestrator.py` (`evaluate_cycle`) | Biến `c_4h` và `c_1h` được truyền vào `closed_ctx` mà không được khai báo hay load từ DB. | Bổ sung nạp nến H4/H1 từ `crud.get_candles` và hỗ trợ optional injection `candles_4h`, `candles_1h`. | `test_rule_162_orchestrator_enabled_cg01_no_name_error` |
| **Import Không Tồn Tại** | `cg01_engine.py` (`build_cg01_context`) | Import hàm `detect_swing_points_causal` từ `smc_liquidity_service` không tồn tại ở baseline. | Thay bằng `identify_confirmed_swings` và `compute_dealing_range` từ `liquidity_scenario_service`. | `test_rule_161_repaired_import_and_liquidity_contracts` |
| **Sai Chữ Ký Liquidity Map** | `cg01_engine.py` & `cg01_context_adapter.py` | `build_liquidity_map` bị gọi sai thứ tự positional arguments và truyền nến thay vì swings. | Tạo `build_cg01_liquidity_context` chuẩn hóa keyword arguments và merge levels M15 + M1. | `test_rule_161_repaired_import_and_liquidity_contracts` |
| **Replay Gán M1 cho 15M** | `lab/cg01_replay_driver.py` | Lấy slice 120 nến M1 gán trực tiếp cho timeframe 15M và bỏ qua H1/H4. | Áp dụng `aggregate_closed_bars` gom nến M1 thành 15M, 1H, 4H hoàn toàn đóng nến trước `now_ms`. | `test_rule_164_htf_aggregation_boundaries_and_incomplete_bucket` |

## 3. Call Chain Mục tiêu Thực tế

```text
DB / Replay Data Feed
  └─► normalize_cg01_candle (CanonicalCandle: attribute & dict access, strict OHLC)
       └─► aggregate_closed_bars (Zero-lookahead HTF grouping: 15M, 1H, 4H)
            └─► evaluate_cg01_candidate
                 ├─► build_cg01_context (H4/H1 swing structure trend + 15M dealing range)
                 ├─► ZoneWatchlistService.extract_zones_from_candles (15M POI)
                 ├─► build_cg01_liquidity_context (15M + 1M confirmed swings & levels)
                 ├─► detect_cg01_sweep (sell-side for LONG, buy-side for SHORT near POI)
                 ├─► resolve_cg01_mss_and_displacement (M1 structural break + body >= 0.8 ATR)
                 ├─► select_cg01_leg_fvg (3-candle gap on same leg + 50% CE price)
                 ├─► resolve_executable_target (structural TP level opposing entry)
                 ├─► calculate_risk_reward (canonical sizing, fees, slippage, net R:R >= 2.0)
                 └─► ScalpCandidate (trigger_chain + immutable ScenarioSnapshot)
                      └─► ScalpWatcherOrchestrator.evaluate_cycle
                           ├─► Arbitration (SHADOW vs PAPER_EXECUTION profile)
                           ├─► BEFORE_ARM Entry Guards (entry_guard_service)
                           ├─► _arm_paper_candidate -> PaperOrder ('armed')
                           ├─► Future executable price event
                           ├─► BEFORE_FILL Guards
                           ├─► TradeLifecycleService.execute_fill -> ('paper_open')
                           └─► TradeLifecycleService.execute_close -> ('closed')
```

## 4. Bảng Kết quả Kiểm thử Chi tiết

| Tên Bước Kiểm Tra | Lệnh Thực Thi | Kết Quả | Thời Gian |
|---|---|---|---|
| `git_diff_check` | `git diff --check` | **PASS** | 0.043s |
| `backend_tests` | `D:\build app-crypto\aurum-desk\backend\venv\Scripts\python.e...` | **PASS** | 8.829s |
| `frontend_typecheck` | `npx.cmd tsc -b` | **PASS** | 6.534s |
| `frontend_lint` | `npx.cmd oxlint` | **PASS** | 1.132s |
| `frontend_vitest` | `npx.cmd vitest run` | **PASS** | 3.208s |
| `frontend_build` | `npm.cmd run build` | **PASS** | 8.337s |

## 5. Trạng thái Đánh giá Chiến lược & Hạn chế

- **Replay Data Validity**: `BLOCKED_DATA`
- **Strategy Performance**: `NOT_EVALUATED`
- **Ghi chú**: Thiếu tệp dữ liệu M1 nến đóng chuẩn (Rules 013, 163, 195). Không dùng nến 5M/15M giả lập M1.

## 6. Tính năng Hoãn lại (Deferred)

- Unicorn Model (DEFERRED)
- IFVG (Inversion FVG) (DEFERRED)
- SMT Divergence (DEFERRED)
- Tiered Risk Allocation (DEFERRED)
- Early Close via Velocity (DEFERRED)

## 7. Kết luận Kỹ thuật

> [!NOTE]
> Toàn bộ các yêu cầu kỹ thuật của **V16.3 CG-01 INTEGRATION REPAIR** đã hoàn tất đạt chuẩn 100%.
> Hệ thống đã sẵn sàng cho chế độ quan sát `SHADOW` và thử nghiệm `PAPER_EXECUTION`.