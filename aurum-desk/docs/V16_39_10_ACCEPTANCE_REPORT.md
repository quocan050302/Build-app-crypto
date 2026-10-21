# V16.39.10 ACCEPTANCE REPORT — READ-ONLY PAPER RUNTIME SNAPSHOT & CONTRACT RECONCILIATION

## 1. Metadata
- **Repository:** `quocan050302/Build-app-crypto`
- **Project:** `aurum-desk`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline Commit:** `ac5655722a5de2c38ed04f5c6c83d5457b731d65` (*test(v16.39.9): align acceptance evidence and verify paper runtime readiness*)
- **Date:** 2026-10-21 UTC
- **Test Results:**
  - **Nhóm hẹp V16.39.7 - V16.39.10:** 65 passed (39 baseline + 26 new tests), 1 deprecation warning, thời gian 23.92s.
  - **Nhóm hồi quy mở rộng (Full Regression):** 337 passed (311 baseline + 26 new tests), 1 deprecation warning, thời gian 43.26s.
- **Status:** PASS (Strictly Read-Only, 0 DB mutations, canonical Schema 2.0, full backward compatibility verified).

---

## 2. Tổng Quan Mục Tiêu & Sửa Đổi (Executive Summary)

### Vấn Đề Tồn Tại Trước Patch (Baseline Flaws)
1. **Side-effects ghi ngầm trong các đường đọc (Hidden DB writes):**
   - Nhánh HTTP gọi `/api/scalp/daily-status` -> kích hoạt `crud.get_or_create_today_audit(db)` tạo bản ghi `DayAudit` mới và commit vào database.
   - Nhánh offline gọi `resolve_effective_daily_policy(session)` -> gọi `TradingPolicyService.get_or_create_policy(db)` tạo và commit bản ghi `TradingPolicy` mặc định trên DB trống.
   - Nhánh tóm tắt gọi `build_cg01_paper_run_summary()` -> gọi `cg01_projection_worker.get_health_status(db)` -> gọi `get_or_create_checkpoint(db)` và `db.add / db.flush` khi thiếu checkpoint.
2. **Lệch Schema giữa HTTP và Offline:**
   - `collect_existing_run_summary()` nhánh HTTP trả `total_closed_trades` và `net_realized_pnl_usd` ở cấp ngoài, trong khi `build_runtime_snapshot()` đọc `run_summary.trades` và `financial_metrics_post_fee`, khiến nhánh HTTP luôn bị số liệu 0.
   - Tool đọc `trades.total_trades_count`, nhưng summary gốc chỉ có `total_closed_trades`, `open_trades_count` và `armed_orders_count`, dẫn đến `orders_count` hiển thị 0 ngay cả khi có lệnh.
3. **Hiển thị sai trạng thái Quyền Auto & Worker Health:**
   - Khi `auto_paper_trading` chưa cấu hình (`None`), renderer Markdown hiển thị `TẮT (OFF)` thay vì `CHƯA XÁC MINH (UNVERIFIED)`.
   - `/health` không có trường `running`; tool cũ đọc `h_data.running` và default `False`, dẫn đến backend khỏe mạnh nhưng báo worker không chạy.
   - Khi chạy offline, worker health không thể quan sát process nhưng lại hiển thị nhầm lẫn.
4. **Đường dẫn Database sai lệch:**
   - Tool cũ tìm `backend/data/trading.db` không tồn tại, trong khi runtime database chuẩn là `backend/aurum_desk.db`.

### Các Biện Pháp Khắc Phục Triệt Để (V16.39.10 Architecture)
1. **Module Đọc Riêng `backend/services/paper_runtime_snapshot_service.py`:**
   - Tạo bộ hàm đọc thuần túy (pure readers): `read_existing_policy_config`, `read_auto_authority`, `find_existing_observation_run`, `read_linked_orders`, `compute_financial_metrics`, `read_run_funnel_records`, `aggregate_funnel_readonly`, `read_process_health`, `build_paper_runtime_snapshot`.
   - Tuyệt đối không gọi `commit()`, `add()`, `flush()`, không kích hoạt migration hoặc khởi động worker.
2. **Endpoint Chuẩn Hóa `GET /api/v1/paper/runtime-snapshot` trong `backend/main.py`:**
   - Thiết lập `db.autoflush = False`.
   - Nhận diện `cg01_evaluation_scheduler` đang sống trong process backend để trả truthful health.
   - Kiểm tra chặt chẽ cửa sổ thời gian (trả HTTP 422 nếu `end_ms <= start_ms`).
   - Trả payload Schema 2.0 đồng nhất 100% với nhánh offline.
3. **Công Cụ `tools/paper_runtime_snapshot.py` Tái Cấu Trúc:**
   - Hỗ trợ `--url` và `--db` loại trừ lẫn nhau (mutually exclusive).
   - Hàm `resolve_snapshot_db_path`: ưu tiên `--db` -> `AURUM_DB_PATH` -> `backend/aurum_desk.db`. Nếu file không tồn tại, báo lỗi `DATABASE_NOT_FOUND` và thoát, tuyệt đối không tự tạo file DB trống.
   - Hàm `open_snapshot_session`: sử dụng SQLite URI `mode=ro` kết hợp `PRAGMA query_only = ON;`, bảo vệ tầng database vật lý không thể bị ghi.
4. **Tính Toán Tài Chính & Trạng Thái Trung Thực:**
   - Đúng mẫu số: 0 lệnh đóng -> `winrate_pct = None` (N/A) và `expectancy_usd_per_trade = None` (N/A).
   - Hòa vốn (+10, -10): `net_realized_pnl = 0.0`, `expectancy_usd_per_trade = 0.0` -> render rõ ràng `+0.00 USD/lệnh` hoặc `0.00 USD/lệnh`, không biến thành `N/A`.
   - Quyền Auto 3 trạng thái: `True` (BẬT), `False` (TẮT), `None` (CHƯA XÁC MINH).
   - Offline worker health: trạng thái `UNKNOWN` với `running = None`.
   - Khử trùng thông tin nhạy cảm: `redact_sensitive_text` bảo vệ Telegram token, Bitget secrets, passwords.

---

## 3. Bảng Ánh Xạ Hàm & Callers Thực Tế (Function & Caller Matrix)

| File | Tên Hàm / Route | Input Parameters | Output Type | Caller Thực Tế | Tests Bao Phủ |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `backend/services/paper_runtime_snapshot_service.py` | `make_snapshot_context` | `symbol, scope, requested_run_id, start_ms, end_ms, now_ms` | `dict` | `build_paper_runtime_snapshot` | `test_010_make_snapshot_context_deterministic_and_window_validation` |
| `backend/services/paper_runtime_snapshot_service.py` | `read_existing_policy_config` | `db: Session` | `dict` | `build_paper_runtime_snapshot` | `test_020_missing_policy_does_not_create_row` |
| `backend/services/paper_runtime_snapshot_service.py` | `read_auto_authority` | `db: Session` | `dict` | `build_paper_runtime_snapshot` | `test_020_auto_authority_three_states` |
| `backend/services/paper_runtime_snapshot_service.py` | `find_existing_observation_run` | `db, run_id, symbol, scope` | `Tuple[Record, str]` | `build_paper_runtime_snapshot` | `test_110_selector_mismatch_and_not_found` |
| `backend/services/paper_runtime_snapshot_service.py` | `read_linked_orders` | `db, run_id, symbol` | `Tuple[List[dict], dict]` | `build_paper_runtime_snapshot` | `test_030_http_and_offline_parity` |
| `backend/services/paper_runtime_snapshot_service.py` | `compute_financial_metrics` | `orders, initial_capital, start_ms, end_ms` | `dict` | `build_paper_runtime_snapshot` | `test_040_empty_orders_yield_clean_nulls`, `test_050_zero_expectancy_renders_clean_zero_not_na`, `test_060_verified_financial_accounting`, `test_070_running_peak_closed_equity_drawdown`, `test_080_half_open_window_inclusion`, `test_090_order_counts_by_state_including_rejected` |
| `backend/services/paper_runtime_snapshot_service.py` | `read_run_funnel_records` | `db, run_id, symbol, start_ms, end_ms` | `List[Record]` | `build_paper_runtime_snapshot` | `test_050_full_table_content_comparison_shows_zero_changes` |
| `backend/services/paper_runtime_snapshot_service.py` | `aggregate_funnel_readonly` | `records: Sequence` | `dict` | `build_paper_runtime_snapshot` | `test_100_funnel_stage_events_vs_unique_opportunities` |
| `backend/services/paper_runtime_snapshot_service.py` | `read_process_health` | `runtime_providers: Optional[dict]` | `dict` | `build_paper_runtime_snapshot` | `test_070_offline_worker_health_is_unknown` |
| `backend/services/paper_runtime_snapshot_service.py` | `validate_finite_metrics` | `payload: Any` | `None` | `build_paper_runtime_snapshot`, `parse_http_snapshot` | `test_030_validate_finite_metrics_rejects_nan_and_inf` |
| `backend/services/paper_runtime_snapshot_service.py` | `build_paper_runtime_snapshot` | `db, run_id, symbol, scope, start_ms, end_ms, now_ms, runtime_providers, source_mode` | `dict` | `get_paper_runtime_snapshot_endpoint`, `tools.paper_runtime_snapshot.main` | `test_010_sql_audit_listener_proves_zero_mutations`, `test_030_http_and_offline_parity` |
| `backend/services/paper_runtime_snapshot_service.py` | `render_markdown` | `snapshot: dict` | `str` | `tools.paper_runtime_snapshot.main` | `test_050_zero_expectancy_renders_clean_zero_not_na`, `test_060_cli_output_dir_atomic_generation` |
| `backend/services/paper_runtime_snapshot_service.py` | `redact_sensitive_text` | `content: str` | `str` | `load_runtime_sources`, `main` | `test_070_secrets_redaction` |
| `backend/main.py` | `GET /api/v1/paper/runtime-snapshot` | `run_id, symbol, scope, start_ms, end_ms, db` | `dict` | Fast API HTTP Client, `load_runtime_sources` | `test_010_api_endpoint_canonical_response_and_422`, `test_020_api_endpoint_never_creates_day_audit` |
| `tools/paper_runtime_snapshot.py` | `resolve_snapshot_db_path` | `explicit_path, environ` | `Optional[Path]` | `load_runtime_sources`, `main` | `test_050_cli_missing_db_exits_with_error` |
| `tools/paper_runtime_snapshot.py` | `open_snapshot_session` | `db_path: Path` | `Generator[Session]` | `main` | `test_060_real_sqlite_mode_ro_prevents_writes` |
| `tools/paper_runtime_snapshot.py` | `load_runtime_sources` | `url, db_path, run_id, symbol, scope, start_ms, end_ms` | `dict` | `main`, legacy tests | `test_020_snapshot_backend_unreachable_reports_runtime_not_available` |
| `tools/paper_runtime_snapshot.py` | `main` | CLI arguments | `int` (Exit code) | CLI shell invocation | `test_040_cli_mutually_exclusive_url_and_db`, `test_050_cli_missing_db_exits_with_error`, `test_060_cli_output_dir_atomic_generation` |

---

## 4. Bằng Chứng Thực Thi & Kết Quả Kiểm Thử (Verification Evidence)

### 4.1. Lệnh Chạy Nhóm Hẹp (Tight Test Group)
```powershell
$env:PYTHONPATH='backend;.'; .\backend\venv\Scripts\python.exe -m pytest -q `
    backend/tests/test_v16_39_10_snapshot_contract.py `
    backend/tests/test_v16_39_10_snapshot_readonly.py `
    backend/tests/test_v16_39_10_snapshot_api_cli.py `
    backend/tests/test_v16_39_9_coordinator_policy_acceptance.py `
    backend/tests/test_v16_39_9_paper_runtime_snapshot.py `
    backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py `
    backend/tests/test_v16_39_7_mode_trust_lesson_api_acceptance.py
```
**Kết quả thực tế:**
```
.................................................................        [100%]
============================== warnings summary ===============================
backend\venv\Lib\site-packages\fastapi\testclient.py:1
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
65 passed, 1 warning in 23.92s
```
*(Bao gồm 39 tests baseline V16.39.7-9 + 26 tests mới của V16.39.10)*

### 4.2. Lệnh Chạy Nhóm Hồi Quy Mở Rộng (Full Extended Regression Suite)
```powershell
$files = (Resolve-Path backend/tests/test_v16_39*.py | ForEach-Object { $_.Path }) + @(
    "backend/tests/test_v16_38_adaptive_paper_readiness.py",
    "backend/tests/test_v16_36_paper_tonight_production_regression.py",
    "backend/tests/test_v16_35_rr_propagation_telegram_proximity.py",
    "backend/tests/test_v16_34_cg01_runtime_ownership_consistency.py",
    "backend/tests/test_v14_4_1_production_pipeline.py",
    "backend/tests/test_v14_4_2_entry_regression.py",
    "backend/tests/test_v16_3_cg01_pipeline.py",
    "backend/tests/test_v16_3_cg01_contracts.py",
    "backend/tests/test_v15_0_telegram_and_api.py",
    "backend/tests/test_migration_runtime_upgrade.py",
    "backend/tests/test_setup_quality_api_v14.py",
    "backend/tests/test_setup_quality_service_v14.py",
    "backend/tests/test_v16_15_scheduler_lifespan.py"
)
$env:PYTHONPATH='backend;.'; .\backend\venv\Scripts\python.exe -m pytest -q $files
```
**Kết quả thực tế:**
```
........................................................................ [ 21%]
........................................................................ [ 42%]
........................................................................ [ 64%]
........................................................................ [ 85%]
.................................................                        [100%]
============================== warnings summary ===============================
backend\venv\Lib\site-packages\fastapi\testclient.py:1
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
337 passed, 1 warning in 43.26s
```
*(Bao gồm 311 tests baseline mở rộng + 26 tests mới của V16.39.10 = 337 tests pass 100%)*

---

## 5. Mẫu Báo Cáo Thực Tế (Sample Output [TEST_FIXTURE])

Khi chạy lệnh snapshot với cơ sở dữ liệu mẫu kiểm thử có 1 lệnh thắng (+15.00 USD), kết quả xuất ra như sau:

### 5.1. JSON Output (Schema Version 2.0)
```json
{
  "schema_version": "2.0",
  "generated_at": 1792571644158,
  "source_mode": "OFFLINE_DB",
  "status": "SNAPSHOT_READY",
  "runtime_commit": "ac5655722a5de2c38ed04f5c6c83d5457b731d65",
  "reporter_commit": "ac5655722a5de2c38ed04f5c6c83d5457b731d65",
  "time_window": {
    "start_ms": 1792571579193,
    "end_ms": 1792571644158
  },
  "effective_config": {
    "policy_present": false,
    "policy": {
      "present": false,
      "id": "default",
      "max_daily_fills": null,
      "daily_min_goal": null,
      "ny_min_fills": null,
      "reserve_ny_slot": null,
      "daily_timezone": null,
      "entry_session_policy": null,
      "is_active": null,
      "version": null
    },
    "daily_max_cap": null,
    "daily_min_goal": null,
    "ny_min_fills": null,
    "reserve_ny_slot": null,
    "execution_profile": "BASELINE",
    "cg01_enabled": false,
    "cg01_profile_mode": "OFF",
    "timing_profile": "SILVER_BULLET_LONDON_NY",
    "min_net_rr": 2.0,
    "quality_scoring_enabled": false,
    "quality_arm_threshold": 70.0,
    "lesson_score_adjustment_enabled": false,
    "auto_enabled": null,
    "auto_source": "PERSISTED_DB",
    "auto_reason": "NOT_CONFIGURED"
  },
  "worker_health": {
    "status": "UNKNOWN",
    "running": null,
    "source": "OFFLINE_DB_READ_ONLY",
    "detail": "Worker process health cannot be determined from offline database alone."
  },
  "run_summary": {
    "run_id": "run_demo",
    "resolution_status": "FOUND",
    "status": "COLLECTING",
    "symbol": "XAUUSDT",
    "scope": "CG01",
    "started_at_ms": 1792571579193,
    "ended_at_ms": null,
    "execution_mode": "PAPER_EXECUTION",
    "observation_mode": "SHADOW",
    "initial_capital_usdt": 1000.0,
    "config_fingerprint": "DEFAULT",
    "software_version": "V16.18",
    "association_metadata": {
      "total_links_found": 1,
      "unique_orders_linked": 1,
      "dangling_order_links_count": 0,
      "mismatched_instrument_count": 0
    }
  },
  "funnel_summary": {
    "total_unique_opportunities": 0,
    "zones_detected": 0,
    "zones_watched": 0,
    "zones_touched": 0,
    "trigger_confirmed": 0,
    "plans_valid": 0,
    "guard_blocked": 0,
    "armed": 0,
    "filled": 0,
    "expired": 0,
    "invalidated": 0,
    "top_rejection_reasons": [],
    "latency_metrics": {
      "sample_count": 0,
      "median_ms": null,
      "p95_ms": null
    }
  },
  "metrics": {
    "duration_minutes": 1.1,
    "opportunities_count": 0,
    "orders_count": 1,
    "state_counts": {
      "closed": 1
    },
    "open_positions_count": 0,
    "armed_orders_count": 0,
    "closed_trades_count": 1,
    "unresolved_closed_count": 0,
    "net_realized_pnl_usd": 15.0,
    "winrate_pct": 100.0,
    "expectancy_usd_per_trade": 15.0,
    "max_drawdown_usd": 0.0,
    "max_drawdown_pct": 0.0,
    "drawdown_type": "CLOSED_EQUITY_DRAWDOWN"
  },
  "errors": [],
  "warnings": [],
  "limitations": [
    "AUTO_UNVERIFIED: Quyền tự động (Auto Trading) chưa được xác minh hoặc chưa cấu hình.",
    "OFFLINE_MODE: Không thể xác nhận trạng thái worker từ ảnh chụp cơ sở dữ liệu."
  ]
}
```

### 5.2. Markdown Output
```markdown
# Báo Cáo Quan Sát Vận Hành PAPER (V16.39.10)

> **Đã quan sát:** 1.1 phút · 0 cơ hội · 1 lệnh.
**Lời/lỗ sau phí:** +15.00 USD.
**Tỷ lệ thắng:** 100.0%.

---

## 1. Trạng Thái Vận Hành
- **Tình trạng tổng thể**: `SNAPSHOT_READY`
- **Nguồn dữ liệu**: `OFFLINE_DB`
- **Commit mã nguồn**: `ac5655722a5d`
- **Worker thực thi**: `UNKNOWN (Không xác nhận được từ ảnh chụp DB)`
- **Quyền tự động (Auto Trading)**: `CHƯA XÁC MINH (UNVERIFIED)`
- **Mã phiên quan sát (Run ID)**: `run_demo` (Trạng thái: `COLLECTING`)

## 2. Phễu Cơ Hội & Thực Thi (Opportunity Funnel)
- Vùng quét (Zones Detected): **0**
- Chờ kích hoạt (Trigger Confirmed): **0**
- Kế hoạch hợp lệ (Plans Valid): **0**
- Lệnh đã đặt chờ (ARMED): **0**
- Lệnh đã khớp (FILLED): **0**
- Vị thế đang mở (Open Positions): **0**

### Lý do chặn/bỏ lỡ chính:
*(Chưa ghi nhận sự kiện chặn trong khoảng thời gian này)*

## 3. Kết Quả Tài Chính Sau Phí (Post-Fee Performance)
- **Tổng lệnh đã đóng (Closed Trades)**: `1` (Chưa xác nhận: `0`)
- **Lợi nhuận ròng (Net Realized PnL)**: `+15.00 USD`
- **Tỷ lệ thắng (Winrate)**: `100.0%`
- **Kỳ vọng bình quân (Expectancy)**: `+15.00 USD/lệnh`
- **Sụt giảm vốn đỉnh (Max Drawdown)**: `0.00 USD (0.00%)`

## 4. Hạn Chế & Lưu Ý Nghiệm Thu (Limitations)
- AUTO_UNVERIFIED: Quyền tự động (Auto Trading) chưa được xác minh hoặc chưa cấu hình.
- OFFLINE_MODE: Không thể xác nhận trạng thái worker từ ảnh chụp cơ sở dữ liệu.

## 5. Bước Tiếp Theo
1. Nếu quyền Auto đang `CHƯA XÁC MINH` hoặc `TẮT`, giữ nguyên trạng thái quan sát cho đến khi người dùng quyết định bật.
2. Không cưỡng ép lệnh (No forced trades) chỉ để đạt chỉ tiêu ngày.
3. Khi phiên Mỹ (NY) mở cửa, kiểm tra lại snapshot để đánh giá phản ứng giá và các vùng FVG/Sweep thực tế.
```

---

## 6. Hướng Dẫn Vận Hành Cho Người Mới (Operational Guide)

### 6.1. Chế Độ HTTP (Khi backend FastAPI đang chạy)
```powershell
$env:PYTHONPATH="backend;."; python tools/paper_runtime_snapshot.py --url http://127.0.0.1:8000 --format markdown
```

### 6.2. Chế Độ Offline DB (Đọc trực tiếp ảnh chụp database)
```powershell
$env:PYTHONPATH="backend;."; python tools/paper_runtime_snapshot.py --db backend/aurum_desk.db --format markdown
```
Hoặc chỉ định rõ run ID đã có:
```powershell
$env:PYTHONPATH="backend;."; python tools/paper_runtime_snapshot.py --db backend/aurum_desk.db --run-id <RUN_ID> --format both
```

### 6.3. Xuất Tệp Báo Cáo Nguyên Tử (Atomic Output)
```powershell
$env:PYTHONPATH="backend;."; python tools/paper_runtime_snapshot.py --db backend/aurum_desk.db --format both --output-dir reports/runtime_snapshot
```
Tệp sẽ được ghi an toàn vào `reports/runtime_snapshot/paper_runtime_snapshot.json` và `paper_runtime_snapshot.md`.

---

## 7. Giới Hạn Nghiệm Thu & Cam Kết Phạm Vi (Scope Commitments)
1. **Không Tuyên Bố Lợi Nhuận Thực:**
   - Các kết quả số liệu trong báo cáo trên xuất phát từ dữ liệu kiểm thử (TEST_FIXTURE), không chứng minh tài khoản thực tế của người dùng đã có lời.
2. **Không Tự Bật Quyền Auto:**
   - Công cụ hoàn toàn chỉ đọc, không có nút bấm hay logic kích hoạt Auto Trading ngầm.
3. **Bảo Toàn 100% Chiến Lược:**
   - Bộ quy tắc CG-01, HTF bias, scoring, các bài học đã duyệt, R:R tối thiểu 1.5, phân bổ rủi ro, chính sách phiên (Á/Âu/Mỹ) và các guards (max 3 fills, 1 open position, news blackout) được bảo toàn tuyệt đối không bị thay đổi.
