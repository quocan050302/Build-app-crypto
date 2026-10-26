# BÁO CÁO NGHIỆM THU V15.1 — COMPLETE SCALP EXECUTION FLOW
**Tài liệu tham chiếu:** `docs/V15_1_SCALP_EXECUTION_REPAIR_REPORT.md`  
**Branch:** `fix/v14-1-rr-flow`  
**Baseline Commit SHA:** `8adcb19222627f053c5916ddd951a72e1d6f27a4`  
**Thời gian hoàn thành:** 2026-10-15  

---

## 1. TỔNG QUAN & MỤC TIÊU PATCH

V15.1 hoàn thiện luồng thực thi Autonomous Scalp M1 và Daily NY Research hiện có trên nền tảng V15.0 mà không làm thay đổi hay suy yếu bất kỳ logic bảo vệ (guards) hoặc mô hình M5 `STANDARD_SMC` hiện tại:
- **Chuỗi thực thi hoàn chỉnh:** RUNTIME CONFIG → CAUSAL SIGNAL → VALIDATED PLAN → GUARDED ARM → FILL → CLOSE → TELEGRAM → JOURNAL.
- **Phạm vi nghiêm ngặt:** PAPER / SHADOW / OFF ONLY (tuyệt đối không REAL, không live credentials, không can thiệp live DB).
- **Chia sẻ hạn ngạch:** M5, M1 và NY Research dùng chung trần tối đa 3 fills/ngày (`DayAudit.fills_count`).

---

## 2. BẢNG ĐỐI CHIẾU LỖI TRƯỚC VÀ SAU SỬA (BUGS TRƯỚC / SAU)

| # | Hạng mục | Trước khi sửa (V15.0) | Sau khi sửa (V15.1) |
|---|---|---|---|
| 1 | **Config Persistence & Runtime Resolution** | `POST /api/scalp/config` lưu `SystemConfig` vào DB nhưng `evaluate_cycle()` gọi `get_scalp_config()` đọc object RAM `active_scalp_config`. API và Watcher bị lệch cấu hình. | Triển khai `load_effective_scalp_config(db)`. API GET/POST và Watcher `evaluate_cycle()` cùng đọc một nguồn DB duy nhất. Nếu `config=None`, Watcher tự động nạp từ DB. |
| 2 | **Validation Mode & Boolean** | Chấp nhận chuỗi không kiểm tra, có nguy cơ `bool("false") == True`. Thiếu kiểm tra mode hợp lệ. | Strict parsing boolean: chuỗi `"false"`, `"0"` chuyển thành `False`. Strict mode: chỉ chấp nhận `OFF`, `SHADOW`, `PAPER`, từ chối mode lạ với HTTP 422. |
| 3 | **Order Fields & Typos** | `_arm_paper_candidate()` dùng `cand.calculation.risk_usdt` (sai field), `estimated_gross_rr` (sai field), `source="AUTO_STRATEGY"` (PaperOrder không có cột `source`). | Đã chuẩn hóa: `initial_risk_usdt = calc.net_risk_usdt`, `gross_rr = calc.gross_rr`, `estimated_net_rr = calc.net_rr`, `origin = "AUTO_STRATEGY"`. |
| 4 | **Quantity Sizing** | Hardcode `quantity = 0.1` trong lệnh arm, vi phạm nguyên tắc sizing theo rủi ro. | Bỏ hoàn toàn hardcode `0.1`. Sizing lấy từ `calc.quantity` của `CalculationResult` canonical sau khi qua kiểm tra tính hợp lệ và khả thi. |
| 5 | **Feed Health Reader** | Hardcode `feed_health={"status": "CONNECTED"}`. | Gọi `read_execution_feed_health()` từ service chuẩn hoặc nhận feed health từ runner/test. Phát hiện feed bị ngắt/chậm thì chặn arm ngay lập tức. |
| 6 | **Quote Freshness & NameError** | `exec_price if exec_price > 0 else break_close` bị `NameError: name 'break_close' is not defined`. Dùng fallback nến che quote thiếu. | Sửa dứt điểm NameError. Candidate bắt buộc có `ValidatedQuote` hợp lệ (`bid > 0, ask >= bid, not stale`), không fallback nến để tạo fill giả. |
| 7 | **Causal Retest** | Mô hình Reversal chỉ kiểm tra Break mà không kiểm tra nến Retest đã đóng. | Bổ sung kiểm tra Closed M1 Retest. Nếu chưa có nến M1 đóng chạm vùng/FVG, trả trạng thái `WAITING_M1_RETEST`, không gắn eligible. |
| 8 | **Candidate Identity & Dedup** | Candidate key dùng `now_ms` làm định danh khiến cùng tín hiệu sinh ra ID mới mỗi tick, gây spam order và alert. | Định danh dựa trên: Symbol + Model + Zone ID + Direction + ID nến swing/break. Khóa `idempotency_key = f"scalp_arm_{cand.candidate_key}"` chặn trùng lặp tuyệt đối. |
| 9 | **Chart Evidence Snapshot Link** | Order không gắn snapshot bất biến, chart lịch sử có thể đọc nhầm setup đang bị mutate. | Order lưu `scenario_snapshot_id` liên kết với `models.SMCScenarioSnapshot`. Endpoint `/api/v1/orders/{order_id}/chart-evidence` trả về evidence đóng băng không đổi. |
| 10 | **EntryGuardRequest Interface** | Truyền `watch_setup_id` thay vì `setup_id`, thiếu `setup_state="READY"`. | Chuẩn hóa tham số: `setup_id=ws.id`, `setup_instance_id=cand.candidate_key`, `setup_state=ws.state`, `timeframe="1M"`. |

---

## 3. CÁC FILE VÀ HÀM ĐÃ SỬA ĐỔI

1. **`backend/services/scalp_m1_types.py`:**
   - Thêm hàm `load_effective_scalp_config(db: Any = None) -> M1ScalpConfig` đọc và parse cấu hình từ `models.SystemConfig` một cách an toàn.
   - Bổ sung auto-healing cho `WatchedZone.__post_init__` để tự tính `invalidation_level` hợp lý cho cả BULLISH và BEARISH khi không truyền giá trị.
2. **`backend/main.py`:**
   - `get_scalp_config_endpoint`: sử dụng `load_effective_scalp_config(db)`.
   - `update_scalp_config_endpoint`: strict mode validation (`OFF`, `SHADOW`, `PAPER`), strict boolean parser cho `daily_ny_research_enabled`, xử lý transaction atomic, rollback và trả về `effective_config`.
3. **`backend/services/scalp_m1_model_service.py`:**
   - Khắc phục NameError `break_close`. Điểm vào yêu cầu authoritative quote (`ask` cho LONG, `bid` cho SHORT).
   - Thêm xác nhận nến M1 đã đóng retest sau displacement break.
   - Thêm `RETEST` evidence node vào danh sách bằng chứng cấu trúc.
   - Truyền `min_net_rr=cfg.min_net_rr` vào canonical `calculate_risk_reward`.
   - Kiểm tra chặt chẽ `calc_res.is_valid` và `calc_res.can_execute`.
   - Tạo candidate key ổn định theo mốc nến xác nhận.
4. **`backend/services/daily_ny_research_service.py`:**
   - Bổ sung quote freshness check.
   - Cắt giảm risk nghiên cứu qua `eff_risk = min(policy_settings.risk_pct, cfg.fallback_risk_pct)`.
   - Ghi nhận `ScenarioState.OBSERVING` với nhãn `NY_DAILY_RESEARCH` (không giả mạo `STRUCTURE_CONFIRMED`).
   - Tạo candidate key ổn định theo mốc nến 5M đã đóng.
5. **`backend/services/scalp_watcher_orchestrator.py`:**
   - `evaluate_cycle`: Tự động nạp effective config từ DB nếu `config=None`. Tôn trọng `global_auto` (nếu auto tắt, tự động hạ PAPER thành SHADOW). Bổ sung `armed_order_id: None` cho các nhánh early return. Đo tổng thời gian cycle sau khi commit.
   - `_arm_paper_candidate`: Sửa toàn bộ schema PaperOrder (`initial_risk_usdt`, `quantity` từ calculator, `gross_rr`, `estimated_net_rr`, `origin="AUTO_STRATEGY"`, `scenario_snapshot_id`, `idempotency_key`). Sửa tham số truyền vào `EntryGuardRequest`.
6. **`backend/tests/test_v15_1_execution_lifecycle.py`:**
   - Tạo mới bộ 16 integration tests phủ toàn bộ vòng đời và các trường hợp biên của V15.1.
7. **`tools/run_v14_4_2_acceptance.py`:**
   - Đăng ký `tests/test_v15_1_execution_lifecycle.py` vào danh sách `targeted_files`.

---

## 4. BẰNG CHỨNG THỰC THI VÒNG ĐỜI (LIFECYCLE EVIDENCE)

### 4.1. Vòng đời lệnh LONG đầy đủ (Test `test_v15_1_full_long_lifecycle_arm_fill_tp`)
```
[PHASE 1: CANDIDATE]
Zone FVG [2600.0, 2620.0] -> M1 Sweep Low @ 2602.0 -> Break Displacement CHOCH @ 2626.0 -> Closed Retest @ 2622.0.
Planned Entry: 2622.50 | SL: 2601.95 | TP: 2673.88 | Net R:R: 2.15

[PHASE 2: GUARDED ARM]
Canonical Guards: PASS (Identity, Strategy, Geometry, Financial, Account, Session, News, Lessons, Quote, Feed).
PaperOrder Created: ord_SMC_M1_REVERSAL_... | State: armed | Qty: 0.12 (từ Net Risk $2.50) | Origin: AUTO_STRATEGY
Telegram Outbox Queued: ARMED

[PHASE 3: EXECUTE FILL]
TradeLifecycleService.execute_fill() @ 2622.50.
State: paper_open | Actual Entry: 2622.50 | DayAudit.fills_count: 1
DomainEvent: trade.opened | Telegram Outbox Queued: FILLED

[PHASE 4: EXECUTE CLOSE]
TradeLifecycleService.execute_close() @ 2673.88 (TP_HIT).
State: closed | Actual Exit: 2673.88 | Exit Cause: TP_HIT | Realized Net PnL: > 0 USDT
Telegram Outbox Queued: TP_HIT
```

### 4.2. Vòng đời lệnh SHORT đầy đủ (Test `test_v15_1_full_short_lifecycle_arm_fill_sl`)
```
[PHASE 1: CANDIDATE]
Zone FVG [2650.0, 2670.0] -> M1 Sweep High @ 2668.0 -> Break Displacement CHOCH @ 2644.0 -> Closed Retest @ 2648.0.
Planned Entry: 2647.50 | SL: 2668.05 | TP: 2606.40 | Geometry: TP < Entry < SL | Net R:R: > 1.8

[PHASE 2: GUARDED ARM]
PaperOrder Created: State: armed | Qty > 0 | Gross RR > 0 | Net RR > 0 | Origin: AUTO_STRATEGY
Telegram Outbox Queued: ARMED

[PHASE 3: EXECUTE FILL]
TradeLifecycleService.execute_fill() @ 2647.50.
State: paper_open | Actual Entry: 2647.50

[PHASE 4: EXECUTE CLOSE]
TradeLifecycleService.execute_close() @ 2668.05 (SL_HIT).
State: closed | Actual Exit: 2668.05 | Exit Cause: SL_HIT | Realized Net PnL: < 0 USDT
Telegram Outbox Queued: SL_HIT
```

### 4.3. Cách ly chế độ SHADOW (Test `test_v15_1_shadow_mode_isolation`)
- Candidate hợp lệ được phát hiện.
- `armed_order_id` trả về `None`.
- Số lượng `PaperOrder` trong cơ sở dữ liệu: **0**.
- Số lượng thông báo `ARMED` trong outbox: **0**.
- Chuyển tiếp phễu cơ hội ghi nhận: `PLAN_VALID`.

### 4.4. Tính bất biến của Chart Evidence (Test `test_v15_1_chart_evidence_endpoint_immutable`)
1. Lệnh LONG được Arm, sinh ra `order_id` liên kết `scenario_snapshot_id`.
2. Gọi `GET /api/v1/orders/{order_id}/chart-evidence`: trả về `direction: "LONG"`, `planned_entry: 2622.5`.
3. Sửa đổi `WatchSetup` trong DB sang `direction: "SHORT"`, `provisional_entry: 9999.0` (giả lập drift thị trường sau đó).
4. Gọi lại `GET /api/v1/orders/{order_id}/chart-evidence`: vẫn trả về `direction: "LONG"`, `planned_entry: 2622.5` đóng băng bất biến.

---

## 5. KẾT QUẢ KIỂM THỬ THỰC TẾ (TEST RESULTS)

### 5.1. Bộ test mới V15.1 (`tests/test_v15_1_execution_lifecycle.py`)
```powershell
backend\venv\Scripts\python.exe -m pytest tests/test_v15_1_execution_lifecycle.py -v
======================== 16 passed, 1 warning in 1.48s ========================
```
Danh sách 16 test case đạt:
1. `test_v15_1_config_api_persists_to_db_and_read_by_runtime_orchestrator`: PASS
2. `test_v15_1_config_strict_validation_and_parsing`: PASS
3. `test_v15_1_long_paper_arm_positive_control`: PASS
4. `test_v15_1_short_paper_arm_positive_control`: PASS
5. `test_v15_1_full_long_lifecycle_arm_fill_tp`: PASS
6. `test_v15_1_full_short_lifecycle_arm_fill_sl`: PASS
7. `test_v15_1_shadow_mode_isolation`: PASS
8. `test_v15_1_quote_and_feed_health_negatives`: PASS
9. `test_v15_1_causal_reversal_negative_controls`: PASS
10. `test_v15_1_cost_and_min_net_rr_threshold`: PASS
11. `test_v15_1_signal_dedup_and_idempotency_across_eval_cycles`: PASS
12. `test_v15_1_shared_daily_limit_guard`: PASS
13. `test_v15_1_daily_ny_research_model_behavior`: PASS
14. `test_v15_1_commit_failure_atomicity`: PASS
15. `test_v15_1_chart_evidence_endpoint_immutable`: PASS
16. `test_v15_1_performance_bounded_evaluation`: PASS

### 5.2. Toàn bộ 9 suite V15 (V15.0 + V15.1)
```powershell
backend\venv\Scripts\python.exe -m pytest tests/test_v15_0_* tests/test_v15_1_* -q
44 passed, 1 warning in 2.01s
```

### 5.3. Full Acceptance Runner (`tools/run_v14_4_2_acceptance.py --skip-e2e`)
```
=== V14.4.2 ACCEPTANCE REPORT ===
  backend_targeted       PASS (660 passed in 67.1s)
  frontend_typecheck     PASS (0 errors in 8.8s)
  frontend_lint          PASS (0 errors in 1.2s)
  frontend_vitest        PASS (3.2s)
  frontend_build         PASS (9.8s)
  git_diff_check         PASS (0.1s, exit code 0)
```

---

## 6. HƯỚNG DẪN CẤU HÌNH & BẬT TÍNH NĂNG (OPERATIONAL GUIDE)

### Cách chuyển chế độ (OFF / SHADOW / PAPER):
1. **Xem cấu hình hiệu lực hiện tại:**
   ```http
   GET /api/scalp/config
   ```
2. **Bật chế độ SHADOW (quan sát, ghi phễu, không sinh lệnh PaperOrder):**
   ```http
   POST /api/scalp/config
   Content-Type: application/json

   {
     "scalp_m1_mode": "SHADOW"
   }
   ```
3. **Bật chế độ PAPER (thực thi có bảo vệ qua Entry Guard):**
   ```http
   POST /api/scalp/config
   Content-Type: application/json

   {
     "scalp_m1_mode": "PAPER"
   }
   ```
4. **Tắt hoàn toàn tính năng Scalp M1:**
   ```http
   POST /api/scalp/config
   Content-Type: application/json

   {
     "scalp_m1_mode": "OFF",
     "daily_ny_research_enabled": false
   }
   ```

---

## 7. GIỚI HẠN ĐÃ BIẾT (KNOWN LIMITATIONS) & NHỮNG GÌ CHƯA TEST
- **Chưa có nến M1 real-time WebSocket ngoài sàn thật trong suite test:** Toàn bộ test sử dụng chuỗi nến causal fixture và tick quote kiểm soát để đảm bảo tính tất định (deterministic).
- **Trình duyệt E2E Playwright:** Được bỏ qua (`--skip-e2e`) do môi trường dev local không bật headless browser display server; các bài test frontend được kiểm tra qua typecheck, linter oxlint, vitest và production build.
- **Không có lệnh REAL:** Hạn chế cố ý của V15.1 — chỉ phục vụ kiểm thử giấy và nghiên cứu tự động.
