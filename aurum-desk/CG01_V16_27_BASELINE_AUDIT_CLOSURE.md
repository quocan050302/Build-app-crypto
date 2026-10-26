# BÁO CÁO KIỂM TOÁN VÀ KHÉP LẠI ĐỢT ĐO BASELINE V16.27
**Tài liệu:** `CG01_V16_27_BASELINE_AUDIT_CLOSURE.md`
**Trạng thái kiểm toán (Audit Status):** `AUDIT_CLOSED_WITH_LIMITATIONS`
**Trạng thái hiệu suất chiến lược (Performance Status):** `PERFORMANCE_UNVERIFIED`
**Nhánh:** `fix/v14-1-rr-flow`
**Commit SHA Baseline Production:** [`33dcd791f2ccfba8e3b3c19e5a44318cdf295ad8`](https://github.com/quocan050302/Build-app-crypto/commit/33dcd791f2ccfba8e3b3c19e5a44318cdf295ad8)
**Commit SHA Tham chiếu Kiểm toán:** [`470fbcba24d0016830babc403d14ab59cbd9302d`](https://github.com/quocan050302/Build-app-crypto/commit/470fbcba24d0016830babc403d14ab59cbd9302d)
**Thời gian hoàn thành kiểm toán:** 2026-10-18T12:50:00Z (19:50:00 PM giờ VN)

---

## 1. TÓM TẮT DÀNH CHO NGƯỜI MỚI (EXECUTIVE SUMMARY)

* **Tiến trình nào đã bắt đầu:** Lượt replay 30 ngày lịch (khởi chạy lúc 11:59:24 VN, tích lũy 338 phút CPU, bị dừng có kiểm soát lúc 18:36:11 VN theo lệnh người dùng).
* **Tiến trình nào chưa bắt đầu:** Lượt replay 14 ngày và lượt 14 ngày đối chứng tính lặp lại (Reproducibility) đã được dừng lại để tránh tiêu tốn thêm thời gian CPU.
* **Đã xác minh được gì (Verified Evidence):**
  1. **Mã nguồn production đóng băng 100%:** `backend/` và `frontend/` không bị thay đổi bất kỳ ký tự nào so với commit `33dcd79`.
  2. **Ba khuyết tật mã nguồn cốt lõi trong `cg01_engine.py`:** Thiếu ATR M1 thực tế (mặc định 1.0); FVG mitigation bị bỏ qua (`pass`); Multi-POI early return chặn POI và hướng ngược lại.
  3. **Lỗi phân trang Downloader:** Giả thuyết ranh giới phân trang (`current_end = earliest - 1` trong khi mô hình Bitget dùng mốc đóng nến) đã được chứng minh và giải quyết qua 8 test trực tiếp vòng lặp `download_m1_candles()`. Không phải do đóng cửa cuối tuần.
  4. **Parity của Fast Aggregation:** Bộ đệm gom cụm nến $O(1)$ pass các trường hợp test trong research tools suite (bao gồm M15, H1, H4, prefixes và boundaries).
  5. **Mapping Closed Trades Canonical:** Đã chuẩn hóa hàm `normalize_replay_closed_trade()` tiêu thụ đúng schema của `CG01ReplayExecutionAdapter.collect_state()` (`trade_id`, `entry_time`, `exit_time`, `net_pnl_usd`, `realized_r`, `initial_risk_usdt`), không để mất timestamp hay holding duration.
  6. **Đính chính tệp log thực thi:** Đã mở ngoại lệ trong `.gitignore` (`!research_artifacts/**/*.log`) để tệp `task_execution.log` (vết ngắt `KeyboardInterrupt` tại `detect_confirmed_continuation_break`) được bàn giao đầy đủ vào kho lưu trữ, khắc phục lỗi 404.
* **Chưa xác minh được gì (Unverified):**
  * Sổ lệnh giao dịch (closed-trade ledger), Net PnL, Winrate, Expectancy, Max Drawdown. Khi tiến trình bị dừng, DB in-memory trong RAM bị giải phóng nên không có bằng chứng sổ lệnh để khẳng định số 0 hay bất kỳ con số nào khác.
  * Live API Semantics của Bitget trên môi trường mạng thật chưa được probe trực tiếp trong patch này; chỉ kiểm chứng qua mock test harness.
* **Kết luận hành động:** Đợt đo baseline V16.27 chính thức khép lại trung thực ở trạng thái `AUDIT_CLOSED_WITH_LIMITATIONS`. Không chạy lại replay dài nhiều giờ. Sẵn sàng chuyển giao toàn bộ kết luận để bước sang triển khai **V16.28**.

---

## 2. BẢNG ĐỐI SOÁT CHỈ SỐ (AUDITED METRICS TABLE)

| Chỉ số (Metric) | Giá trị Báo cáo Cũ | Giá trị Kiểm toán Mới | Trạng thái (Status) | Đường dẫn Bằng chứng (Evidence Path) | Ghi chú & Lý do đính chính |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Strategy Baseline Commit** | `33dcd79` | `33dcd79` | `VERIFIED` | `33dcd791f2ccfba8e3b3c19e5a44318cdf295ad8` | Mã nguồn production nguyên trạng |
| **Audit Head Commit** | `3a8f0bd` | `470fbcb` | `VERIFIED` | `470fbcba24d0016830babc403d14ab59cbd9302d` | Commit artifacts và công cụ |
| **Elapsed Wall-Clock** | 6h 36m 47s | 6h 36m 47s | `REPORTED_ARTIFACT` | `task_execution.log` | Từ 11:59:24 đến 18:36:11 VN |
| **Reported CPU Time** | 338m 23s | 338m 23s | `REPORTED_NOT_VERIFIED` | `task_execution.log` | Báo cáo trong log/metrics thô, chưa có OS audit trail độc lập |
| **Callstack lúc ngắt** | `detect_confirmed_continuation_break` | `detect_confirmed_continuation_break` | `VERIFIED` | `task_execution.log:L43-46` | Ghi nhận tại thời điểm nhận `SIGINT` (đã commit vào repo) |
| **Số nến đã xử lý** | 42.986 (Dataset) | `null` | `UNVERIFIED` | `null` | DB in-memory không kịp checkpoint ra đĩa |
| **Timestamp nến cuối** | `1792281540000` | `null` | `UNVERIFIED` | `null` | Không dùng timestamp cuối của dataset thay thế |
| **Số lệnh đã khớp (Fills)** | 0 | `null` | `UNVERIFIED` | `null` | Không có snapshot cuối |
| **Số lệnh đã đóng (Closed)** | 0 | `null` | `UNVERIFIED` | `null` | Không có sổ lệnh (trade ledger) được ghi nhận |
| **Lợi nhuận ròng (Net PnL)** | 0.00 USDT | `null` | `UNVERIFIED` | `null` | Không dùng số 0 thay dữ liệu chưa xác minh |
| **Tỷ lệ thắng (Winrate)** | N/A | **N/A** | `NOT_APPLICABLE` | `null` | Lý do: `UNVERIFIED_EXECUTION_RESULTS` |
| **Kỳ vọng (Expectancy)** | N/A | **N/A** | `NOT_APPLICABLE` | `null` | Lý do: `INSUFFICIENT_SAMPLE` |
| **Profit Factor** | N/A | **N/A** | `NOT_APPLICABLE` | `null` | Lý do: `UNVERIFIED_EXECUTION_RESULTS` |
| **Số lệnh đang mở (Open)** | 0 | `null` | `UNVERIFIED` | `null` | Không có snapshot cuối |
| **Số lệnh chờ (Pending)** | 0 | `null` | `UNVERIFIED` | `null` | Không có snapshot cuối |
| **Max Drawdown (Closed)** | 0.00% | `null` | `UNVERIFIED` | `null` | Không có chuỗi equity hoàn chỉnh |
| **Max Drawdown (MTM)** | 0.00% | `null` | `UNVERIFIED` | `null` | Không có chuỗi MTM hoàn chỉnh |

---

## 3. BẢNG KIỂM TOÁN TẬP DỮ LIỆU & PHÂN TRANG (DATA AUDIT TABLE)

| Mục kiểm toán | Kết quả kiểm toán thực tế | Trạng thái | Chi tiết kỹ thuật & Khắc phục |
| :--- | :--- | :--- | :--- |
| **Nguồn dữ liệu** | Bitget USDT-FUTURES (`XAUUSDT`) | `REPORTED_METADATA` | Khai báo API: `/api/v2/mix/market/history-candles` |
| **Dataset 30 ngày** | 42.986 nến M1 (99.5% coverage) | `VERIFIED` | Logical SHA-256: `51060062...a521a1` |
| **Dataset 14 ngày** | 20.060 nến M1 (99.5% coverage) | `VERIFIED` | Logical SHA-256: `80d788dd...060806` |
| **Conflicting Duplicates** | 0 | `VERIFIED` | Đã kiểm định hình học OHLC |
| **Hiện tượng 214 Gaps** | Mỗi gap đúng 120s (thiếu 1 nến M1), lặp lại mỗi 201 phút | `CONFIRMED_ISSUE` | Phân bố: 214 trang $\times$ 200 nến = 42.800 nến |
| **Semantics của Bitget API** | `endTime` là mốc đóng nến (`close_time <= endTime`) | `TESTED_VIA_MOCK_HARNESS` | Mô hình phân trang close-time được kiểm thử qua unit tests; live probe mạng thật chưa thực hiện |
| **Nguyên nhân lỗi 214 Gaps** | `current_end = earliest - 1` trong downloader | `PAGINATION_HYPOTHESIS_CONFIRMED_IN_TESTS` | Trừ 1ms khiến bỏ qua nến có `close_time = earliest`; đã kiểm chứng qua test |
| **Sửa đổi Downloader** | `current_end = earliest` + strict progress check | `FIXED_AND_TESTED` | `tools/test_baseline_research_tools.py` (8 test downloader mới) |

---

## 4. BẢNG KIỂM TOÁN RUNTIME MONKEYPATCH & PARITY (RUNTIME AUDIT TABLE)

| Hàm bị can thiệp | Thay thế trong Harness | Phạm vi & Scoping | Trạng thái Parity | Bằng chứng kiểm thử tự động |
| :--- | :--- | :--- | :--- | :--- |
| `replay_driver_mod.aggregate_closed_bars` | `fast_aggregate_closed_bars` | Đã chuyển sang `fast_aggregation_context()` (khôi phục trong `finally`) | **PARITY IN TEST CASES** | Khớp trên M15, H1, H4, prefixes, boundaries trong test suite |
| `replay_driver_mod.select_closed_candles` | `fast_select_closed_candles` | Đã chuyển sang `fast_aggregation_context()` (khôi phục trong `finally`) | **PARITY IN TEST CASES** | Khớp trên 1M closed candles trong test suite |
| **Cache Isolation** | Tách riêng theo `(symbol, timeframe)` | Gọi `reset_fast_caches()` giữa các lượt | `VERIFIED` | Đã test cô lập symbol XAUUSDT vs BTCUSDT |
| **Tùy chọn Native** | Cờ CLI `--native` | Chạy 100% unpatched native aggregators khi cần | `SUPPORTED` | Đã tích hợp trong `eval_cg01_v16_27_baseline.py` |
| **Export Per-Run** | Lưu ngay sau mỗi run | Không đợi toàn bộ chuỗi run hoàn tất | `HARDENED` | Đã test atomic write cho từng lượt run |

---

## 5. BẢNG ĐÍNH CHÍNH KHẲNG ĐỊNH (CLAIM CORRECTION TABLE)

| Khẳng định ban đầu | Đính chính kiểm toán chính thức | Lý do & Căn cứ kỹ thuật |
| :--- | :--- | :--- |
| *"Đã kiểm tra: 30 ngày lịch"* | **Tập dữ liệu mục tiêu 30 ngày lịch; quá trình thực thi bị gián đoạn (INTERRUPTED) trước khi hoàn tất.** | Tách bạch giữa độ dài tập dữ liệu đầu vào và phạm vi tính toán thực tế đã hoàn thành. |
| *"0 lệnh đóng phát sinh, Max DD 0.00%"* | **Số lệnh đóng và Drawdown là UNVERIFIED.** | Tiến trình bị dừng khi DB in-memory chưa được ghi ra đĩa. Không dùng số 0 thay thế cho dữ liệu chưa xác minh. |
| *"214 khoảng gaps do đóng cửa phiên cuối tuần"* | **214 khoảng gaps bắt nguồn từ lỗi phân trang downloader (`current_end = earliest - 1`).** | Đo đạc mô hình phân trang xác nhận `endTime` là mốc đóng nến; việc trừ 1ms đã làm sót đúng 1 nến ở mỗi ranh giới trang (mỗi 201 phút). |
| *"Semantics Bitget API xác nhận bằng probe trực tiếp (VERIFIED_BY_PROBE)"* | **Semantics close-time được kiểm chứng qua mock test suite; exchange live semantics unverified nếu không có live network probe.** | Tránh khẳng định vượt mức bằng chứng khi không gọi network trực tiếp tới sàn trong patch. |
| *"100% parity tuyệt đối qua 14/14 tests"* | **Pass các trường hợp test trong suite (hiện tại 31 tests phân định 5 nhóm).** | Không dùng test hữu hạn để tuyên bố tuyệt đối trên mọi tập đầu vào khả dĩ. |
| *"Ba lỗi logic cốt lõi đã dập tắt toàn bộ setup"* | **Ba lỗi logic trong `cg01_engine.py` là khuyết tật mã nguồn đã được chứng minh, nhưng mối quan hệ nhân quả tuyệt đối cần được kiểm chứng đối chứng trong V16.28.** | Chưa chạy bản sửa đối chứng thì không kết luận nhân quả tuyệt đối 100%. |
| *"Thời gian chạy replay sẽ giảm xuống dưới 3 phút"* | **Đã loại bỏ cam kết thời gian chưa được đo đạc chính thức.** | Ước tính thời gian phải dựa trên đo lường thực tế sau khi triển khai cắt lát lịch sử. |
| *"Không commit, không push trong đợt đo"* | **Mã nguồn production được đóng băng 100% trong đợt đo; các commit chỉ nằm ở công cụ và báo cáo.** | Phân biệt rõ đóng băng mã nguồn production với việc bàn giao công cụ nghiên cứu và tài liệu. |

---

## 6. DANH MỤC TỆP ARTIFACTS THỰC TẾ (ARTIFACT MANIFEST)

| Đường dẫn tệp | Kích thước | Vai trò tài liệu | Tính toàn vẹn / Trạng thái |
| :--- | :--- | :--- | :--- |
| [`aurum-desk/CG01_V16_27_BASELINE_PERFORMANCE_REPORT.md`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/CG01_V16_27_BASELINE_PERFORMANCE_REPORT.md) | ~16 KB | Báo cáo hiệu suất baseline chính (đã gắn thông báo kiểm toán) | Đã cập nhật đính chính |
| [`aurum-desk/CG01_V16_27_BASELINE_AUDIT_CLOSURE.md`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/CG01_V16_27_BASELINE_AUDIT_CLOSURE.md) | ~14 KB | Báo cáo kiểm toán và khép lại đợt nghiên cứu | Tệp hiện tại |
| [`aurum-desk/research_artifacts/cg01_v16_27_baseline_20261018/metrics_audited.json`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/research_artifacts/cg01_v16_27_baseline_20261018/metrics_audited.json) | 7.5 KB | Dữ liệu máy đọc được đã kiểm toán (thay thế `metrics_partial.json`) | Đã cập nhật |
| [`aurum-desk/research_artifacts/cg01_v16_27_baseline_20261018/metrics_partial.json`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/research_artifacts/cg01_v16_27_baseline_20261018/metrics_partial.json) | 3.2 KB | Bản ghi thô lịch sử ban đầu | Bảo tồn nguyên trạng (Rule 061) |
| [`aurum-desk/research_artifacts/cg01_v16_27_baseline_20261018/task_execution.log`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/research_artifacts/cg01_v16_27_baseline_20261018/task_execution.log) | 3.1 KB | Log thực thi tiến trình và callstack `KeyboardInterrupt` | Đã un-ignore và tracked |
| [`aurum-desk/tools/download_v16_27_m1_dataset.py`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/tools/download_v16_27_m1_dataset.py) | 13 KB | Downloader đã sửa lỗi phân trang boundary và theo dõi duplicate | Đã sửa và kiểm chứng |
| [`aurum-desk/tools/eval_cg01_v16_27_baseline.py`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/tools/eval_cg01_v16_27_baseline.py) | 28 KB | Harness đã chuẩn hóa closed-trade mapping canonical schema và CSV export | Đã sửa và kiểm chứng |
| [`aurum-desk/tools/test_baseline_research_tools.py`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/tools/test_baseline_research_tools.py) | 26 KB | Suite kiểm thử tự động toàn diện (31 tests phân định 5 nhóm) | 31/31 PASSED (0.82s) |
| `research_artifacts/.../data/xauusdt_m1_30d.json` | 6.8 MB | Dataset M1 30 ngày Bitget | SHA: `51060062...` |
| `research_artifacts/.../data/xauusdt_m1_14d.json` | 3.2 MB | Dataset M1 14 ngày Bitget | SHA: `80d788dd...` |

---

## 7. KẾT QUẢ KIỂM THỬ TỰ ĐỘNG & REGRESSION (RAW TEST LOGS)

### Gate 1 & Gate 2 & Gate 4: Suite kiểm thử công cụ nghiên cứu (`test_baseline_research_tools.py`)
```text
============================= test session starts ==============================
platform darwin -- Python 3.13.0, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk
collected 31 items

tools/test_baseline_research_tools.py::test_downloader_boundary_no_gaps_logic PASSED [  3%]
tools/test_baseline_research_tools.py::test_downloader_boundary_off_by_one_reproduction PASSED [  6%]
tools/test_baseline_research_tools.py::test_downloader_duplicate_detection_conflicting_and_identical PASSED [  9%]
tools/test_baseline_research_tools.py::test_downloader_gap_calculation_definition PASSED [ 12%]
tools/test_baseline_research_tools.py::test_downloader_invalid_geometry_and_non_finite PASSED [ 16%]
tools/test_baseline_research_tools.py::test_downloader_genuine_pagination_three_pages_traversal PASSED [ 19%]
tools/test_baseline_research_tools.py::test_downloader_genuine_no_progress_termination PASSED [ 22%]
tools/test_baseline_research_tools.py::test_downloader_genuine_http_failure_first_page PASSED [ 25%]
tools/test_baseline_research_tools.py::test_downloader_genuine_http_failure_subsequent_page PASSED [ 29%]
tools/test_baseline_research_tools.py::test_downloader_genuine_api_code_error PASSED [ 32%]
tools/test_baseline_research_tools.py::test_downloader_genuine_empty_data_early_termination PASSED [ 35%]
tools/test_baseline_research_tools.py::test_downloader_genuine_max_pages_limit PASSED [ 38%]
tools/test_baseline_research_tools.py::test_downloader_genuine_duplicate_and_conflict_preservation PASSED [ 41%]
tools/test_baseline_research_tools.py::test_aggregation_parity_timeframes PASSED [ 45%]
tools/test_baseline_research_tools.py::test_select_closed_candles_parity PASSED [ 48%]
tools/test_baseline_research_tools.py::test_prefix_evolution_parity PASSED [ 51%]
tools/test_baseline_research_tools.py::test_boundary_conditions_parity PASSED [ 54%]
tools/test_baseline_research_tools.py::test_cache_reset_and_symbol_isolation PASSED [ 58%]
tools/test_baseline_research_tools.py::test_fast_aggregation_context_restores_on_success PASSED [ 61%]
tools/test_baseline_research_tools.py::test_fast_aggregation_context_restores_on_exception PASSED [ 64%]
tools/test_baseline_research_tools.py::test_honest_metrics_on_unverified_sample PASSED [ 67%]
tools/test_baseline_research_tools.py::test_immediate_per_run_export PASSED [ 70%]
tools/test_baseline_research_tools.py::test_mapping_canonical_adapter_schema PASSED [ 74%]
tools/test_baseline_research_tools.py::test_mapping_long_win_short_loss_and_breakeven PASSED [ 77%]
tools/test_baseline_research_tools.py::test_mapping_canonical_precedence_over_legacy PASSED [ 80%]
tools/test_baseline_research_tools.py::test_mapping_legacy_field_compatibility PASSED [ 83%]
tools/test_baseline_research_tools.py::test_mapping_missing_pnl_authority_handling PASSED [ 87%]
tools/test_baseline_research_tools.py::test_mapping_invalid_and_missing_timestamps PASSED [ 90%]
tools/test_baseline_research_tools.py::test_mapping_duplicate_trade_ids_detection PASSED [ 93%]
tools/test_baseline_research_tools.py::test_mapping_closed_equity_drawdown_chronology_sorting PASSED [ 96%]
tools/test_baseline_research_tools.py::test_mapping_csv_export_content_and_null_formatting PASSED [100%]

============================== 31 passed in 0.82s ==============================
```

### Gate 3: Hồi quy Baseline V16.27 (`test_v16_27_event_dispatch_lifecycle.py`)
```text
======================== 22 passed, 2 warnings in 0.85s ========================
```

---

## 8. KẾT LUẬN VÀ BÀN GIAO CHÍNH THỨC

1. **Đợt nghiên cứu baseline V16.27 chính thức khép lại** với bằng chứng trung thực, đầy đủ và minh bạch. Không còn tình trạng dùng số 0 thay thế cho kết quả bị ngắt giữa chừng, không nhầm lẫn lỗi phân trang với đóng cửa phiên cuối tuần, và chứng minh được parity của bộ đệm thuật toán trong các trường hợp test quy định.
2. **Mã nguồn production được bảo toàn tuyệt đối:** Toàn bộ sửa đổi nằm trong thư mục `tools/` và tài liệu báo cáo, không gây bất kỳ rủi ro nào cho hệ thống giao dịch hiện hành.
3. **Sẵn sàng cho V16.28:** Đầy đủ dữ liệu, công cụ và bằng chứng đã sẵn sàng để người dùng tiến hành triển khai bản sửa lỗi V16.28 tiếp theo.
