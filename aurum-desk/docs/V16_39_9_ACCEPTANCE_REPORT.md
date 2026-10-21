# V16.39.9 ACCEPTANCE REPORT — FINAL ACCEPTANCE ALIGNMENT AND FORWARD PAPER READINESS

## 1. Metadata
- **Repository:** `quocan050302/Build-app-crypto`
- **Project:** `aurum-desk`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline Commit:** `ef8cff7c82cf2e6495b1b7e3bf9788ed1fd6d836` (*test(v16.39.8): verify lesson API effects through production ARM flow*)
- **Status:** PASS (All mandatory acceptance criteria and forward paper readiness gates verified test-first)

---

## 2. Three Core Deliverables (Ba Đầu Việc)

### Đầu việc 1: Sửa Báo Cáo V16.39.8 Cho Khớp Source
1. **Khớp Tên Hàm Thu Thập:**
   - Cập nhật toàn bộ bảng ánh xạ và matrix trong `docs/V16_39_8_ACCEPTANCE_REPORT.md` để khớp chính xác với tên hàm được pytest collect (`test_010_...` đến `test_080_...`).
2. **Khắc Phục Alias Trùng Lặp Collection:**
   - Bỏ tiền tố `test_` khỏi alias tương thích `test_030_orchestrator_lesson_effect_threshold_crossing` trong `backend/tests/test_v16_39_7_mode_trust_lesson_api_acceptance.py` chuyển thành `orchestrator_lesson_effect_threshold_crossing`.
   - Kết quả: pytest collect chính xác 20 tests duy nhất trong suite V16.39.7 (thay vì 21 do alias kép) và 8 tests trong V16.39.8.
3. **Làm Rõ Cơ Chế Khớp Lệnh Tại Gate 4:**
   - Báo cáo V16.39.8 được hiệu chỉnh để nêu rõ: Gate 4 kiểm chứng khớp trực tiếp qua `TradeLifecycleService.execute_fill`, trong khi việc quét báo giá, hạn giờ và chính sách qua `ExecutionCoordinator.evaluate_orders_sync` được đưa vào phạm vi kiểm chứng chuyên biệt tại V16.39.9.
4. **Ghi Rõ Phạm Vi Fixture & Profile:**
   - Bổ sung ghi chú rõ ràng về việc fixture V16.39.8 đặt `policy.is_active = False` để cô lập đường ống lesson-to-ARM; kiểm chứng chính sách hoạt động thực tế (`policy.is_active = True`) được hoàn tất tại V16.39.9.

### Đầu việc 2: Kiểm Tra Coordinator Fill và Policy Active Trong DB Test
Triển khai bộ test tích hợp toàn diện trong `backend/tests/test_v16_39_9_coordinator_policy_acceptance.py` đi qua đầy đủ chu trình:
`evaluate_cycle -> PaperOrder ARMED -> ExecutionCoordinator.evaluate_orders_sync -> TradeLifecycleService.execute_fill -> process_exit_tick -> close / review / lesson`.

1. **LONG Limit Fill & Tránh Khớp Kép (No Double Fill):**
   - Candidate LONG được arm qua `evaluate_cycle`.
   - `ExecutionCoordinator.evaluate_orders_sync` nhận báo giá hợp lệ `ask <= planned_entry`, thực hiện khớp lệnh chuyển trạng thái sang `paper_open`, ghi nhận `actual_entry` và `opened_at`.
   - Gọi lại lần thứ hai với cùng báo giá: điều phối viên kiểm tra vị thế mở hiện hữu và trả về an toàn, không tạo vị thế trùng lặp (Idempotent).
   - Thoát lệnh chốt lời (TP) qua `process_exit_tick`, sinh bản ghi `PostTradeReviewRecord` và tạo bài học `Lesson` ở trạng thái chờ duyệt (`is_approved=False`).
2. **SHORT Limit Fill & Bất Biến Hình Học (Geometry Invariance):**
   - Candidate BEARISH SHORT được arm với hình học hợp lệ: `take_profit (2642.0) < planned_entry (2650.0) < stop_loss (2655.0)`.
   - `ExecutionCoordinator.evaluate_orders_sync` nhận `bid >= planned_entry`, kích hoạt khớp SHORT LIMIT, bảo toàn hướng `SHORT` và `actual_entry`.
3. **Active Trading Policy Cho Phép Khớp Trong Phiên Mỹ (NY Allowed):**
   - Cấu hình `policy.is_active = True` và `entry_session_policy = "NY_ONLY"`.
   - Tại thời điểm 10:00 EDT (nằm trong cửa sổ 08:00 - 11:00 NY), `evaluate_entry_policy` trả về `allowed=True`. Lệnh ARMED được `evaluate_orders_sync` khớp thành công.
4. **Active Trading Policy Chặn Ngoài Phiên Mỹ (Outside NY Blocked):**
   - Tại thời điểm 13:00 EDT (sau khi cửa sổ 11:00 kết thúc), chính sách trả về `allowed=False` với mã lỗi `OUTSIDE_ENTRY_WINDOW`.
   - `ExecutionCoordinator` gọi `TradeLifecycleService.execute_reject`, chuyển trạng thái lệnh sang `rejected`, ghi lý do vào `invalidation_reason`, bảo toàn không mở vị thế.
5. **Active Trading Policy Chặn Khi Đạt Giới Hạn Ngày (Daily Cap 3 Fills Blocked):**
   - Khi `day_audit.fills_count = 3`, chính sách kích hoạt `MAX_DAILY_ENTRIES`. Lệnh chờ bị từ chối an toàn, không vi phạm giới hạn 3 lệnh/ngày.
6. **Active Trading Policy Chặn Khi Có Tin Tức Mạnh (News Blackout Blocked):**
   - Sự kiện tin tức `EconomicNews` USD tác động mạnh ("High") trong cửa sổ tin tức kích hoạt `NEWS_BLACKOUT`. Điều phối viên từ chối khớp lệnh, bảo vệ vốn.

### Đầu việc 3: Chuẩn Bị Báo Cáo Quan Sát PAPER Từ Service Hiện Có
Xây dựng công cụ quan sát chỉ đọc `tools/paper_runtime_snapshot.py` tuân thủ nghiêm ngặt các nguyên tắc vận hành:
1. **Tái Sử Dụng Hoàn Toàn Service Đã Có:**
   - Tích hợp `PaperResearchRunService` (`build_cg01_paper_run_summary`), `OpportunityFunnelService` (`summarize_opportunity_funnel`), `TradingPolicyService`, `load_effective_scalp_config`, và `classify_reason` từ `ny_quota_helpers`.
2. **Bảo Đảm Chỉ Đọc (Strictly Read-Only):**
   - Thiết lập mặc định `create_if_missing=False`. Không tự tạo run giả mạo.
   - Không gọi `start_observation_run` hay `create_forward_paper_session`.
   - Kết nối SQLite qua cờ đọc an toàn `mode=ro`.
   - Không can thiệp quyền tự động (`auto_enabled`), không gửi lệnh ra sàn Bitget.
3. **Hỗ Trợ Hai Chế Độ Nguồn:**
   - *HTTP Mode (`--url`):* Truy vấn trực tiếp backend đang chạy qua các endpoints `/api/health`, `/api/scalp/watchlist`, `/api/scalp/cg01/evaluation`, `/api/scalp/funnel`, `/api/scalp/daily-status`. Nếu backend không hoạt động, trả về `RUNTIME_NOT_AVAILABLE`.
   - *Offline DB Mode (`--db`):* Đọc cơ sở dữ liệu SQLite cục bộ ở chế độ read-only, báo cáo trung thực worker health là `UNKNOWN` (không tự suy đoán worker đang chạy).
4. **Báo Cáo Trung Thực & Thân Thiện Người Mới:**
   - Khi chưa có lệnh đóng (`closed_trades_count == 0`), tỷ lệ thắng (`winrate_pct`) và kỳ vọng (`expectancy`) được ghi nhận là `N/A`, tránh phép chia cho 0 hoặc sinh giá trị `NaN`/`Infinity` trong JSON.
   - Định dạng Markdown súc tích với tóm tắt 3 dòng dễ hiểu:
     - `Đã quan sát: ... phút · ... cơ hội · ... lệnh.`
     - `Lời/lỗ sau phí: ... USD.`
     - `Tỷ lệ thắng: N/A (chưa đủ mẫu lệnh đóng) hoặc ...%.`
   - Bảng phân loại lý do chặn/bỏ lỡ với nhãn tiếng Việt rõ ràng.

---

## 3. Acceptance Matrix

| Case ID | Scenario / Verification Focus | Target Component / File | Input / Trigger | Expected Output | Actual Result | Status |
|---|---|---|---|---|---|---|
| **ACC-01** | Coordinator LONG Limit Fill & Idempotency | `ExecutionCoordinator` / `test_010` | Báo giá `ask <= planned_entry` sau khi ARM | Lệnh chuyển sang `paper_open`, khớp đúng giá; gọi lại không nhân đôi lệnh; thoát lệnh TP lãi ròng dương; sinh review và lesson | `paper_open`, 1 vị thế mở duy nhất, TP_HIT lãi dương, review & pending lesson tạo thành công | **PASS** |
| **ACC-02** | Coordinator SHORT Limit Fill & Geometry | `ExecutionCoordinator` / `test_020` | Báo giá `bid >= planned_entry` với hình học SHORT | Lệnh chuyển sang `paper_open`, giữ nguyên hướng SHORT, `TP < Entry < SL` | Khớp đúng giá, hướng SHORT bảo toàn, thoát lệnh TP_HIT thành công | **PASS** |
| **ACC-03** | Active Policy Trong Phiên Mỹ Cho Phép Khớp | `TradingPolicyService` + `Coordinator` / `test_030` | 10:00 EDT trong cửa sổ 08:00 - 11:00 NY, `policy.is_active=True` | Chính sách trả về `allowed=True`, điều phối viên khớp lệnh thành công | `allowed=True`, lệnh chuyển thành `paper_open` | **PASS** |
| **ACC-04** | Active Policy Ngoài Phiên Mỹ Chặn Khớp | `TradingPolicyService` + `Coordinator` / `test_040` | 13:00 EDT ngoài cửa sổ NY (AFTER_WINDOW) | Chính sách trả về `allowed=False`, lệnh bị từ chối với lý do `OUTSIDE_ENTRY_WINDOW`, không mở vị thế | Lệnh chuyển sang `rejected`, `invalidation_reason` chứa `OUTSIDE_ENTRY_WINDOW`, 0 vị thế mở | **PASS** |
| **ACC-05** | Active Policy Chặn Khi Đạt Cap 3 Lệnh | `TradingPolicyService` + `Coordinator` / `test_050` | `day_audit.fills_count = 3` | Chính sách kích hoạt `MAX_DAILY_ENTRIES`, điều phối viên từ chối lệnh | Lệnh chuyển sang `rejected`, `invalidation_reason` chứa `MAX_DAILY_ENTRIES` | **PASS** |
| **ACC-06** | Active Policy Chặn Khi Có Tin Tức Mạnh | `TradingPolicyService` + `Coordinator` / `test_060` | Sự kiện tin tức USD High impact trong blackout | Cổng tin tức trả về `blocked=True`, điều phối viên từ chối lệnh với lý do `NEWS_BLACKOUT` | Lệnh chuyển sang `rejected`, `invalidation_reason` chứa `NEWS_BLACKOUT` | **PASS** |
| **ACC-TOOL-01** | Snapshot Không Có Run Báo NOT_FOUND | `tools/paper_runtime_snapshot.py` / `test_010` | Truy vấn run_id không tồn tại trong DB | Báo cáo `NOT_FOUND`, không tự ý tạo run mới, không sinh số liệu giả | `NOT_FOUND`, 0 run mới được tạo trong DB | **PASS** |
| **ACC-TOOL-02** | Snapshot Backend Không Khả Dụng | `tools/paper_runtime_snapshot.py` / `test_020` | Truy vấn cổng HTTP không hoạt động | Trả về `RUNTIME_NOT_AVAILABLE`, không ném unhandled exception | `RUNTIME_NOT_AVAILABLE`, limitations ghi nhận lỗi kết nối | **PASS** |
| **ACC-TOOL-03** | Snapshot 0 Lệnh Đóng Xử Lý N/A | `tools/paper_runtime_snapshot.py` / `test_030` | DB chỉ có lệnh mở/chờ, 0 lệnh đã đóng | `winrate_pct` và `expectancy` là `None` (N/A), JSON không chứa `NaN`/`Infinity` | Winrate và Expectancy trả về N/A, JSON hợp lệ 100% | **PASS** |
| **ACC-TOOL-04** | Snapshot Worker Dừng Báo STOPPED | `tools/paper_runtime_snapshot.py` / `test_040` | Trạng thái worker là STOPPED | Snapshot phản ánh trung thực tình trạng `STOPPED` | Status = `STOPPED`, worker running = False | **PASS** |
| **ACC-TOOL-05** | Hợp Đồng Chỉ Đọc (Strict Read-Only) | `tools/paper_runtime_snapshot.py` / `test_050` | Thực thi snapshot trên DB | Không gọi các hàm ghi, số lượng orders/runs/funnel giữ nguyên tuyệt đối | Không thay đổi số lượng bản ghi trong DB, 0 side-effects | **PASS** |

---

## 4. Test Execution Evidence

### 1. Suite Nghiệm Thu Coordinator & Active Policy (V16.39.9)
```powershell
$env:PYTHONPATH="backend;."; .\backend\venv\Scripts\pytest.exe backend/tests/test_v16_39_9_coordinator_policy_acceptance.py -v
```
**Kết quả thực tế:**
```
collected 6 items
test_010_coordinator_long_limit_fill_and_no_double_fill PASSED [ 16%]
test_020_coordinator_short_limit_fill_and_geometry PASSED      [ 33%]
test_030_policy_active_ny_window_allows_fill PASSED            [ 50%]
test_040_policy_active_outside_window_blocks_fill PASSED       [ 66%]
test_050_policy_active_daily_cap_blocks_fill PASSED            [ 83%]
test_060_policy_active_news_blackout_blocks_fill PASSED        [100%]
============================== 6 passed in 1.73s ==============================
```

### 2. Suite Nghiệm Thu Công Cụ Snapshot (V16.39.9)
```powershell
$env:PYTHONPATH="backend;."; .\backend\venv\Scripts\pytest.exe backend/tests/test_v16_39_9_paper_runtime_snapshot.py -v
```
**Kết quả thực tế:**
```
collected 5 items
test_010_snapshot_no_run_reports_not_found PASSED                                  [ 20%]
test_020_snapshot_backend_unreachable_reports_runtime_not_available PASSED        [ 40%]
test_030_snapshot_zero_closed_trades_has_na_winrate_and_clean_json PASSED         [ 60%]
test_040_snapshot_worker_stopped_reported_honestly PASSED                         [ 80%]
test_050_snapshot_strict_read_only_contract PASSED                                 [100%]
============================== 5 passed in 10.65s ==============================
```

### 3. Suite Hồi Quy Tổng Hợp (V16.39.7 + V16.39.8 + V16.39.9)
```powershell
$env:PYTHONPATH="backend;."; .\backend\venv\Scripts\pytest.exe backend/tests/test_v16_39_7_mode_trust_lesson_api_acceptance.py backend/tests/test_v16_39_8_production_lesson_arm_acceptance.py backend/tests/test_v16_39_9_coordinator_policy_acceptance.py backend/tests/test_v16_39_9_paper_runtime_snapshot.py -v
```
**Kết quả thực tế:**
```
======================= 39 passed, 1 warning in 13.43s ========================
```

### 4. Toàn Bộ Toàn Suite V16.39 (10 files)
```powershell
$env:PYTHONPATH="backend;."; .\backend\venv\Scripts\pytest.exe (Get-ChildItem backend/tests/test_v16_39_*.py | ForEach-Object { $_.FullName }) -q
```
**Kết quả thực tế:**
```
139 passed, 1 warning in 16.66s
```

---

## 5. Hướng Dẫn Vận Hành & Lệnh Sử Dụng Tool Cho Người Mới

Công cụ `tools/paper_runtime_snapshot.py` cho phép người dùng quan sát hệ thống ở trạng thái thực tế mà không lo làm hỏng hay thay đổi cấu hình giao dịch.

### Lệnh 1: Quan Sát Qua Backend Đang Chạy (HTTP Mode)
Khi backend FastAPI đang chạy trên cổng 8000:
```powershell
$env:PYTHONPATH="backend;."; python tools/paper_runtime_snapshot.py --url http://127.0.0.1:8000 --format markdown
```

### Lệnh 2: Quan Sát Trực Tiếp Database Cục Bộ (Offline DB Mode)
Khi backend không chạy hoặc muốn đọc nhanh ảnh chụp cơ sở dữ liệu:
```powershell
$env:PYTHONPATH="backend;."; python tools/paper_runtime_snapshot.py --db backend/data/trading.db --format markdown
```

### Lệnh 3: Xuất Báo Cáo Ra Tệp Markdown và JSON
Để lưu kết quả vào thư mục báo cáo (ví dụ: `reports/`):
```powershell
$env:PYTHONPATH="backend;."; python tools/paper_runtime_snapshot.py --output-dir reports/runtime_snapshot
```
Kết quả sẽ được ghi vào `reports/runtime_snapshot/paper_runtime_snapshot.md` và `reports/runtime_snapshot/paper_runtime_snapshot.json`.

---

## 6. Hạn Chế & Lưu Ý Nghiệm Thu (Limitations & Forward Notes)
1. **Không Tuyên Bố Lợi Nhuận Khi Chưa Đủ Mẫu:**
   - Việc toàn bộ test logic đạt 100% chứng minh tính đúng đắn kỹ thuật (correctness), không chứng minh hệ thống chắc chắn sinh lời trong mọi điều kiện thị trường tương lai.
   - Mọi báo cáo quan sát PAPER khi chưa có giao dịch đóng phải duy trì tỷ lệ thắng và kỳ vọng ở mức `N/A`.
2. **Quyền Tự Động (Auto Authority):**
   - Công cụ snapshot không tự ý bật chế độ Auto. Quyền giao dịch hoàn toàn do người dùng kiểm soát.
3. **Không Ép Lệnh (No Forced Trades):**
   - Chỉ tiêu ngày (Daily Goal) trong chính sách là mục tiêu định hướng quan sát; các bộ lọc an toàn (News, Spread, Score >= 70.0, Net RR >= 1.5, Max Cap = 3) luôn có quyền ưu tiên tối thượng để bảo toàn vốn.

---

## 7. Ghi Chú Kiểm Chứng Bổ Sung (V16.39.10 Audit Addendum)
> [!NOTE]
> Kiểm tra độc lập tại thời điểm V16.39.10 cho thấy:
> - **Phần Coordinator & Policy (Đầu việc 2):** Đã được kiểm chứng hoàn toàn chính xác và toàn vẹn qua 39 tests baseline và 311 tests hồi quy mở rộng.
> - **Phần Snapshot (Đầu việc 3):** Phiên bản V16.39.9 tiền khai có một số giới hạn và thiếu hụt coverage kỹ thuật:
>   1. `load_runtime_sources` trong nhánh HTTP gọi `/api/scalp/daily-status` (có side-effect gọi `get_or_create_today_audit` tạo `DayAudit`) và gọi `/api/scalp/cg01/evaluation` thay vì endpoint snapshot chuyên biệt.
>   2. `resolve_effective_daily_policy` trong nhánh offline gọi `get_or_create_policy`, có khả năng commit `TradingPolicy` mặc định trên database trống.
>   3. `build_cg01_paper_run_summary` gọi `projection_worker.get_health_status(db)`, hàm này có nhánh `get_or_create_checkpoint` sinh checkpoint mới.
>   4. Schema payload giữa HTTP và offline bị lệch trường (`trades.total_trades_count` vs `total_closed_trades`), dẫn đến `orders_count` hiển thị 0.
>   5. `auto_enabled` missing bị ép thành `False` (hiển thị TẮT thay vì CHƯA XÁC MINH), và worker health suy luận từ `/health` bị gán `False` vì thiếu trường `running`.
>   6. Đường dẫn DB mặc định tìm `backend/data/trading.db` không tồn tại thay vì `backend/aurum_desk.db`.
>
> Toàn bộ các hạn chế trên đã được khắc phục triệt để, tái hiện qua regression test đỏ và kiểm chứng nghiêm ngặt trong **V16.39.10** với `backend/services/paper_runtime_snapshot_service.py`, endpoint `GET /api/v1/paper/runtime-snapshot`, và 3 file test mới.
