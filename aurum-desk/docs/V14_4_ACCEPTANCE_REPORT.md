# BÁO CÁO NGHIỆM THU V14.4 (V14_4_ACCEPTANCE_REPORT)

**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `fix/v14-1-rr-flow`  
**Phiên bản:** V14.4 — Causal SMC Realtime Scenarios & Selected Trade Chart Evidence  
**Thời điểm nghiệm thu:** 2026-10-11 23:25:08  

---

## 1. Tóm Tắt Kết Quả Kiểm Thử Toàn Diện

| Thành phần | Công cụ kiểm thử | Số lượng ca kiểm thử | Kết quả | Thời gian |
|---|---|---|---|---|
| Backend Pytest | pytest 9.1.1 (Python 3.13) | 301 test cases | **301 / 301 PASS** (100%) | 8.89s |
| Frontend Vitest | vitest 5.0.3 | 21 test files (167 test cases) | **167 / 167 PASS** (100%) | 0.62s |
| Frontend Production Build | `tsc -b && vite build` | Strict verbatim & no-unused checks | **PASS** (Zero errors) | 4.21s |
| Benchmark Hiệu Năng | `run_v14_4_chart_acceptance.py` | 1,000 chu kỳ phân tích | **PASS** (P50 < 0.001ms, P95: 0.001ms) | 0.02s |
| **Tổng thể nghiệm thu** | `run_v14_4_acceptance.py` | Toàn bộ pipeline | **PASS** | |

---

## 2. 10 Lỗi Nền Đã Được Khắc Phục Triệt Để

1. **Lỗi 1 (App.tsx gọi buildChartEvidenceBundle truyền timeframe vào selectionKey):**
   - Đã sửa: Sử dụng `makeSelectionKey` tổng hợp binding chính xác (`source, id, instanceId, revision, symbol, direction, planFingerprint, snapshotId`).
2. **Lỗi 2 (chartEvidence.ts dùng known_at_ms làm timestamp swing/event):**
   - Đã sửa: Phân định rõ ràng `occurred_at_ms` (mốc nến gốc xảy ra) làm tọa độ vẽ và `known_at_ms` (thời điểm xác nhận nến đóng) để kiểm tra nhân quả.
3. **Lỗi 3 (ChartComponent.tsx tạo vùng POI start/end bằng Date.now() ± 1 giờ):**
   - Đã sửa: Giữ nguyên `startAtMs` và `endAtMs` từ dữ liệu FVG gốc của backend; loại bỏ hoàn toàn việc tạo vùng giả quanh `Date.now()`.
4. **Lỗi 4 (SELECTED nhưng chưa có selectedEvidence thì rơi sang global structure):**
   - Đã sửa: Nhánh `SELECTED` luôn xử lý và return `setData(empty)` nếu chưa có bundle. Không bao giờ hiển thị cấu trúc tổng quan thị trường khi đang ở chế độ xem bằng chứng lệnh/setup được chọn.
5. **Lỗi 5 (Focus chỉ autoScale + scrollToPosition(3), không tìm nến lịch sử của lệnh):**
   - Đã sửa: Thêm `targetTime` / `startAnchorMs` vào `focusRequest`, tính toán cửa sổ hiển thị lịch sử và sử dụng `timeScale().setVisibleRange` để đưa chart về đúng đoạn thời gian của lệnh.
6. **Lỗi 6 (R:R selection chưa gán entryTime; nhật ký hiện khung ở hiện tại):**
   - Đã sửa: Gán `entryTime` từ `opened_at ?? armed_at ?? created_at` trong cả `handleFocusSetupOnChart` và `handleFocusTradeOnChart`.
7. **Lỗi 7 (_assess_and_save_quality hard-code eligible=True, quote valid=True):**
   - Đã sửa: Chuyển sang sử dụng `resolve_plan_levels`, `calculate_risk_reward` chính quy và kết quả kiểm tra quote thực từ `QuoteValidator`.
8. **Lỗi 8 (READY đủ để _check_strategy trả OK; auto-arm truyền structure_chain_verified=True):**
   - Đã sửa: `_check_strategy` chặn `READY` với lý do `UNCONFIRMED_STRUCTURE_CHAIN` nếu `structure_chain_verified` là `False`. Kiểm tra chuỗi bằng `validate_entry_evidence_bundle`.
9. **Lỗi 9 (evaluate_setup_eligibility kiểm tra JSON có nội dung, chưa kiểm tra đúng hướng/chuỗi):**
   - Đã sửa: Sử dụng `validate_reversal_order` và `validate_entry_evidence_bundle` để kiểm tra quan hệ thứ tự nhân quả, tính liên kết giữa sweep, break, FVG, retest và mục tiêu unconsumed.
10. **Lỗi 10 (QuoteValidator biến thiếu timestamp thành tuổi giá 0):**
    - Đã sửa: Nếu ticker thiếu cả `timestamp/exchange_ts` lẫn `received_at/observed_at` provenance thì từ chối với mã `TIME_UNAVAILABLE`. Nếu timestamp tương lai vượt quá 5.000ms thì từ chối với mã `FUTURE_TIMESTAMP`.

---

## 3. Ma Trận Nghiệm Thu Tính Năng Bắt Buộc (Phụ Lục G)

| Case | Trạng thái | Ghi chú kiểm chứng |
|---|---|---|
| LONG full chain | **PASS** | `test_long_reversal_confirmed_pipeline`: setup -> guard -> armed -> fill -> quota, chart source IDs matching. |
| SHORT mirror | **PASS** | `test_short_reversal_confirmed_pipeline`: TP < Entry < SL, green zone dưới entry, red zone trên entry. |
| Sweep/break same OHLC | **PASS** | `test_same_bar_ambiguous`: trả `SAME_BAR_OR_UNORDERED_SWEEP_BREAK`, chặn xác nhận chain. |
| Wrong/old/unlinked FVG | **PASS** | `test_unlinked_fvg`: FVG không liên kết displacement leg bị từ chối với lý do `FVG_NOT_LINKED_TO_BREAK`. |
| Future appended bars | **PASS** | `test_known_at_cutoff`: Nến tương lai sau as_of_ms không làm thay đổi snapshot quá khứ. |
| Target consumed | **PASS** | `test_consumed_target_not_reselected`: Mức thanh khoản đã bị quét/phá vỡ không được chọn lại làm mục tiêu còn nguyên. |
| Quote no timestamp | **PASS** | `test_missing_timestamp`: Quote thiếu timestamp bị reject với `TIME_UNAVAILABLE`. |
| News/red lesson/account | **PASS** | `test_news_lesson_account_quote_feed_blocks`: Cấu trúc xác nhận nhưng có tin đỏ/bài học đỏ thì vẫn bị BLOCKED, quota 0/1. |
| Fallback nosweep | **PASS** | Toàn bộ 117 tests V14.3 giữ nguyên 100% pass; quota và experimental adapter hoạt động đúng. |
| Select A -> B -> A | **PASS** | `v14_4_chart_evidence.test.ts`: Generation counter loại bỏ response cũ của A, không ghi đè selection B hoặc A mới. |
| Selected missing/loading | **PASS** | `ChartComponent.tsx`: Trả empty primitives, không vẽ cấu trúc tổng quan giả làm bằng chứng lệnh. |
| FVG historic | **PASS** | Giữ nguyên `startAtMs` và `endAtMs` từ backend, không dùng `Date.now() ± 1h`. |
| History order focus | **PASS** | Khung thời gian chart tự động di chuyển về thời điểm mở lệnh lịch sử (`targetTime`). |
| Drag draft | **PASS** | Kéo thả draft chỉ áp dụng cho DRAFT; lệnh armed/open/closed giữ nguyên `readOnly: true`. |
| Layer toggle | **PASS** | `ChartLayerMenu` cho phép bật/tắt từng lớp trực quan độc lập. |
| Migration twice | **PASS** | `test_migration_twice`: Schema migration chạy 2 lần không làm thay đổi trạng thái, số dòng hay giá trị lệnh cũ. |
