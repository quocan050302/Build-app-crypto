# BÁO CÁO NGHIỆM THU V15.5 — RUNTIME LIFECYCLE CLOSURE
**DURABLE HYPOTHESIS EXPIRY + EXACT BREAK QUALIFICATION**
**COMPLETE ACCEPTANCE REGISTRATION + REAL ARM/FILL/CLOSE LIFECYCLE TESTS**

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Reviewed Baseline:** `859addc5702b22ca16e37ea93691bfd30b3738e2`
- **Delivery Commit:** `fix(v15.5): persist hypothesis lifetime and verify exact-break execution lifecycle`

---

## 1. TỔNG QUAN BỐN VẤN ĐỀ VÀ ROOT CAUSE

| # | Vấn đề Reviewer Xác Nhận | Root Cause Trong Mã Nguồn Baseline | Giải Pháp Sửa Đổi Thực Tế |
|---|---|---|---|
| **1** | **Hypothesis expiry được giữ giữa cycles nhưng không bền qua restart** | `ScalpWatcherOrchestrator._active_hypotheses` chỉ lưu trên bộ nhớ RAM (dict Python). Khi restart process hoặc tạo instance mới, cache rỗng khiến `build_liquidity_hypotheses` tái tạo hypothesis mới với `created_at_ms = now_ms` và `expiry_ms = now_ms + ttl_ms`, làm trôi expiry về tương lai. | Thêm cơ chế lưu trữ bền vững qua bảng `models.SystemConfig` (key scheme hash bounded: `hlc:{symbol}:{hash24}`). Hàm `load_durable_hypotheses_for_symbol` nạp anchor gốc (`created_at_ms`, `expiry_ms`, `revision`, `status`) trước khi reconcile. `stage_hypothesis_lifecycle` đưa vào transaction chu kỳ và commit đồng thời. Expiry không bao giờ bị dời khi restart. |
| **2** | **first_break_bar được chọn nhưng displacement dùng max body của breaking_bars về sau** | Trong `ScalpM1ModelService.evaluate_m1_reversal_candidate`, `first_break_bar = breaking_bars[0]` và `break_prefix_atr` được tính tại `first_break_idx`. Tuy nhiên, kiểm tra xung lực dùng `max_break_body = max(abs(float(b.close) - float(b.open)) for b in breaking_bars)`. Một nến lớn xuất hiện sau có thể hợp thức hóa nến phá vỡ đầu tiên dù thân nến đầu tiên rất yếu. | Viết helper thuần `qualify_selected_break(candles, break_idx, direction, config, fallback_atr)`. Chỉ đánh giá thân nến, hướng nến (LONG: tăng, SHORT: giảm) và prefix ATR của đúng cây nến được chọn làm phá vỡ. Nến lớn về sau không thể hợp thức hóa nến phá vỡ yếu trước đó. Ghi nhận chính xác `break_body` vào evidence node `ev_choch`. |
| **3** | **Acceptance runner chưa đăng ký V15.3 và V15.4** | Biến `targeted_files` trong `tools/run_v14_4_2_acceptance.py` kết thúc ở `tests/test_v15_2_liquidity_engine.py` (74 files), bỏ sót `tests/test_v15_3_causal_wiring.py` và `tests/test_v15_4_causal_entry.py`. | Đăng ký đầy đủ `tests/test_v15_3_causal_wiring.py`, `tests/test_v15_4_causal_entry.py`, và suite mới `tests/test_v15_5_lifecycle_closure.py` vào `targeted_files` (77 files tổng cộng). |
| **4** | **Hai test V15.4 mang tên full lifecycle mới assert đến state=armed** | Hai test `test_full_causal_long_lifecycle_arm_fill_tp` và `test_full_causal_short_lifecycle_arm_fill_sl` trong `test_v15_4_causal_entry.py` dừng lại ở `assert order.state == "armed"`. Chưa kích hoạt fill thực tế và close thực tế qua `TradeLifecycleService`. | Mở rộng cả hai test trong `test_v15_4_causal_entry.py` và bổ sung các test chuyên sâu trong `test_v15_5_lifecycle_closure.py` đi qua đầy đủ các chặng: Candidate -> Armed -> `execute_fill` (`paper_open`, `actual_entry`, `opened_at`) -> `execute_close` (`closed`, `actual_exit`, `exit_cause`, realized net PnL) -> Domain Events (`order.armed`, `trade.opened`, `trade.closed`) -> Notification Outbox (`ARMED`, `FILLED`, `TP_HIT` / `SL_HIT`). |

---

## 2. CALL CHAIN & KIẾN TRÚC PERSISTENCE

### 2.1. Chuỗi gọi Durable Hypothesis Lifecycle
```
ScalpWatcherOrchestrator.evaluate_cycle(db, symbol, now_ms=t_recv, ...)
  │
  ├── 1. load_durable_hypotheses_for_symbol(db, symbol)
  │      └── load_hypothesis_lifecycle(db, hyp_id, symbol)
  │             └── SystemConfig.query.filter(key == "hlc:{sym}:{hash24}")
  │             └── Parse JSON + Validate integer timestamps, revision, schema_version=1, fingerprint
  │
  ├── 2. build_liquidity_hypotheses(..., existing_hypotheses=combined_hyps)
  │      └── reconcile_hypothesis_lifecycle(existing_hypotheses, new_hypotheses, now_ms)
  │             └── Preserves created_at_ms, expiry_ms
  │             └── Terminal status protection: "EXPIRED" / "INVALIDATED" remain immutable
  │             └── now_ms >= expiry_ms -> status = "EXPIRED"
  │
  ├── 3. stage_hypothesis_lifecycle(db, symbol, hyp, now_ms=t_recv)  [Stage-only, no commit]
  │      └── db.merge(models.SystemConfig(key=key, value=payload, updated_at=t_recv))
  │      └── Expired durable hypotheses not in current bounded zones also staged as EXPIRED
  │
  ├── 4. Candidate evaluations (if zone_hyp.status == "EXPIRED" -> OpportunityReasonCode.HYPOTHESIS_EXPIRED, blocks order arming)
  │
  └── 5. db.commit()
         ├── On Success: self._active_hypotheses[symbol] = hypotheses (In-memory cache synced)
         └── On Failure: db.rollback() + self._active_hypotheses.pop(symbol, None) (Cache invalidated)
```

### 2.2. Chuỗi đánh giá Exact Break Qualification
```
ScalpM1ModelService.evaluate_m1_reversal_candidate(...)
  │
  ├── Tìm breaking_bars sau sweep đóng nến qua break_level (CHOCH)
  ├── first_break_bar = breaking_bars[0], first_break_idx = closed_1m.index(first_break_bar)
  │
  ├── qualify_selected_break(closed_1m, first_break_idx, direction, cfg, fallback_atr=atr_1m)
  │      ├── bar = closed_1m[first_break_idx]
  │      ├── Direction validation:
  │      │     LONG: close > open (nến tăng)
  │      │     SHORT: close < open (nến giảm)
  │      ├── body = abs(close - open) > 0 (loại trừ Doji, NaN, vô cực)
  │      ├── prefix_atr = compute_atr(closed_1m[:first_break_idx + 1], period=cfg.displacement_atr_period)
  │      └── body >= cfg.displacement_body_atr_min * prefix_atr
  │
  ├── If not qualified:
  │      └── Return ScalpCandidate(is_eligible=False, reason_code="WAITING_M1_STRUCTURE", missing_conditions=[fail_reason])
  │
  └── Evidence Node:
         └── ev_choch: displacement_body = break_body, displacement_atr = prefix_atr
```

---

## 3. CHỨNG MINH TÁI HIỆN & KHẮC PHỤC (REPRO & VERIFICATION)

### 3.1. Tái hiện lỗi Expiry trôi qua Restart
- **Trước khi sửa (Baseline 859addc):**
  - T0 = 1,000,000,000 ms: Hypothesis được tạo với `expiry_ms = 1,014,400,000` (T0 + 4h).
  - Restart process (tạo instance `ScalpWatcherOrchestrator` mới).
  - T1 = 1,003,600,000 ms (1h sau): Instance mới không có state trong DB, tạo mới hypothesis với `created_at_ms = 1,003,600,000` và `expiry_ms = 1,018,000,000` -> **Expiry bị dời tới 1h**.
- **Sau khi sửa (V15.5):**
  - T0: Hypothesis được lưu bền vững vào `SystemConfig` với `created_at_ms = 1,000,000,000`, `expiry_ms = 1,014,400,000`.
  - Restart instance mới tại T1 = 1,003,600,000 ms: `load_durable_hypotheses_for_symbol` nạp bản ghi từ DB.
  - Sau khi reconcile: `created_at_ms == 1,000,000,000` và `expiry_ms == 1,014,400,000` **được bảo toàn tuyệt đối**.
  - Restart tại T2 = 1,014,410,000 ms (sau deadline): Trạng thái xác nhận `EXPIRED`, candidate bị chặn với `OpportunityReasonCode.HYPOTHESIS_EXPIRED`, không phát sinh lệnh armed.

### 3.2. Tái hiện lỗi Mượn Xung Lực Nến Sau
- **Trước khi sửa (Baseline 859addc):**
  - Nến 15 (first break): `open=2615.10`, `close=2615.20` -> `body = 0.10` (< 0.50 * ATR).
  - Nến 16: `open=2615.20`, `close=2622.00` -> `body = 6.80`.
  - Baseline dùng `max_break_body = max(0.10, 6.80) = 6.80` -> **Hợp thức hóa nến 15 yếu**.
- **Sau khi sửa (V15.5):**
  - `qualify_selected_break(candles, break_idx=15, direction="LONG", ...)` trả về `is_qual = False`, `body = 0.10`, reason: `"Thân nến phá vỡ không đạt ngưỡng xung lực (0.10 < 0.50)"`.
  - Ứng viên bị từ chối chính xác tại nến yếu.

### 3.3. Đăng ký Runner Toàn Diện
- File `tools/run_v14_4_2_acceptance.py`:
  - Đã thêm: `"tests/test_v15_3_causal_wiring.py"`, `"tests/test_v15_4_causal_entry.py"`, `"tests/test_v15_5_lifecycle_closure.py"`.
  - Tổng số file targeted: **77 files** (tăng từ 74).

### 3.4. Vòng đời Giao Dịch Thực Tế ARM -> FILL -> CLOSE
- **LONG TP Path:**
  1. `evaluate_cycle`: Order được arm (`state="armed"`, `planned_entry=2622.5`, `stop_loss=2601.95`, `take_profit=2675.0`).
  2. `TradeLifecycleService.execute_fill`: Chuyển sang `paper_open`, `actual_entry=2622.5`, `opened_at` hợp lệ, DayAudit `fills_count >= 1`.
  3. `TradeLifecycleService.execute_close`: Chốt tại `actual_exit=2675.0`, `exit_cause="TP_HIT"`, `realized_pnl_net > 0`, `state="closed"`.
  4. Domain events: `order.armed`, `trade.opened`, `trade.closed` cam kết atomic. Outbox: `ARMED`, `FILLED`, `TP_HIT`.
- **SHORT SL Path:**
  1. `evaluate_cycle`: Order được arm (`take_profit=2595.0 < planned_entry=2647.5 < stop_loss=2668.05`).
  2. `TradeLifecycleService.execute_fill`: Chuyển sang `paper_open`, `actual_entry=2647.5`.
  3. `TradeLifecycleService.execute_close`: Cắt lỗ tại `actual_exit=2668.05`, `exit_cause="SL_HIT"`, `realized_pnl_net < 0`, `state="closed"`.
  4. Domain events: `order.armed`, `trade.opened`, `trade.closed`. Outbox: `ARMED`, `FILLED`, `SL_HIT`.

---

## 4. KẾT QUẢ KIỂM THỬ THỰC TẾ

| Lệnh Thực Thi | Số Ca Test | Kết Quả | Thời Gian | Mã Thoát |
|---|---|---|---|---|
| `pytest tests/test_v15_5_lifecycle_closure.py` | 13 | **13 passed** | 0.54s | 0 |
| `pytest tests/test_v15_4_causal_entry.py` | 26 | **26 passed** | 0.41s | 0 |
| `pytest tests/test_v15_3_causal_wiring.py` | 10 | **10 passed** | 0.20s | 0 |
| `pytest tests/ -k "v15"` | 107 | **107 passed** (1469 deselected) | 2.98s | 0 |
| **Acceptance Runner Targeted List (77 files)** | 723 | **723 passed, 0 failed** | 48.77s | 0 |
| `frontend: npm run build` (tsc -b + vite build) | N/A | **0 errors, bundle 1010 kB** | 1.47s | 0 |
| `frontend: npx oxlint` | 97 files | **0 errors (29 warnings)** | 0.20s | 0 |
| `frontend: npx vitest run` | 226 | **226 passed (26 test files)** | 1.80s | 0 |

---

## 5. DANH SÁCH FILE THAY ĐỔI
1. `backend/services/liquidity_scenario_service.py`:
   - Thêm `HypothesisLoadStatus`, `DurableHypothesisRecord`.
   - Thêm `compute_hypothesis_source_fingerprint`, `get_hypothesis_storage_key`.
   - Thêm `load_hypothesis_lifecycle`, `load_durable_hypotheses_for_symbol`, `stage_hypothesis_lifecycle`.
   - Cập nhật `reconcile_hypothesis_lifecycle` bảo toàn terminal status (`EXPIRED`, `INVALIDATED`, `FULFILLED`) và neo thời gian.
2. `backend/services/scalp_m1_model_service.py`:
   - Thêm `qualify_selected_break(candles, break_idx, direction, config, fallback_atr)`.
   - Sửa nhánh LONG và SHORT chỉ dùng thuộc tính của `first_break_bar`.
   - Loại bỏ hoàn toàn `max_break_body`.
3. `backend/services/scalp_watcher_orchestrator.py`:
   - Nạp `load_durable_hypotheses_for_symbol` đầu chu kỳ.
   - Stage bền vững `stage_hypothesis_lifecycle` cho toàn bộ hypotheses trong cùng transaction.
   - Stage `EXPIRED` cho các hypotheses quá hạn kể cả khi zone không còn trong `bounded_zones`.
   - Đồng bộ cache `_active_hypotheses` khi commit thành công, xóa cache khi rollback.
4. `backend/tests/test_v15_4_causal_entry.py`:
   - Mở rộng `test_full_causal_long_lifecycle_arm_fill_tp` và `test_full_causal_short_lifecycle_arm_fill_sl` xác nhận trọn vẹn ARM -> FILL -> CLOSE với events và outbox.
5. `tools/run_v14_4_2_acceptance.py`:
   - Đăng ký `tests/test_v15_3_causal_wiring.py`, `tests/test_v15_4_causal_entry.py`, `tests/test_v15_5_lifecycle_closure.py` vào `targeted_files`.
6. `backend/tests/test_v15_5_lifecycle_closure.py` (Mới):
   - 13 ca kiểm thử chuyên sâu cho restart expiry durability, exact break qualification, runner registration, và real execution lifecycle.
