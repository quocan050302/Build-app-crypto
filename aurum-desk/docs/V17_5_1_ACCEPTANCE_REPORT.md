# AURUM V17.5.1 — FINAL FEATURE CLOSURE ACCEPTANCE REPORT
**BÁO CÁO NGHIỆM THU TÍNH NĂNG V17.5.1: STRUCTURAL STOP LOSS & REAL PAPER EVIDENCE**

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Base Commit:** `0c2f2cee6e82163b6bab623402ee37767516ebc1`
- **Technical Status:** `IMPLEMENTATION_VERIFIED`
- **Empirical Status:** `EMPIRICAL_UNPROVEN`
- **Operational Cutover:** `CUTOVER_NOT_OBSERVED` (User selection required in Settings for new paper trades; existing positions preserved)

---

## 1. Executive Summary & Verification Gates

Tất cả các lỗi kỹ thuật đã được tái hiện độc lập, sửa đổi tận gốc tại boundary thực thi của hệ thống và kiểm chứng bằng test suite tự động hoàn chỉnh. Không sử dụng mock để che lỗi; không nới lỏng assertions; không chỉnh sửa lệnh armed/open/closed đã có trong cơ sở dữ liệu.

| Cổng Kiểm Tra | Phạm Vi Kiểm Tra | Số Lượng Tests | Kết Quả | Thời Gian |
|---|---|---|---|---|
| **Gate 1** | Tái hiện & đóng 6 Red Probes (R1 - R6) | 6 tests | **PASS** | ~3.1s |
| **Gate 2** | V17.5.1 Targeted Feature Suites (Anchor, Structural Gate, ATR, CAS, Runtime, Production Flow, Guards, Costs) | 20 tests | **PASS** | ~14.2s |
| **Gate 3** | Existing V17.5 Feature Suites | 31 tests | **PASS** | ~4.5s |
| **Gate 4** | Prior Regression Suites (CG01, Session Roundtrip, Mirrors, Baseline, No-op) | 35 tests | **PASS** | ~49.8s |
| **Gate 5** | Frontend Vitest & Contract Suites | 501 tests | **PASS** | ~6.6s |
| **Gate 6** | Frontend Production Build (`tsc -b && vite build`) | Full Bundle | **PASS** | ~8.5s |
| **Gate 7** | Frontend Biome / ESLint Linting | 151 files | **PASS** (0 errors) | ~0.7s |

**Tổng cộng Backend Tests:** 92 tests PASSED (0 failures).  
**Tổng cộng Frontend Tests:** 501 tests PASSED (0 failures).

---

## 2. Six Red Probes: Root Cause Analysis & Resolutions

### Probe R1 — `apply_stop_placement_to_candidate`
- **Baseline Defect:** Khi cấu hình `PAPER_STRUCTURAL` và thiếu anchor hợp lệ, hệ thống trả về `stop_dec.status="WAITING_EVIDENCE"` nhưng vẫn tính toán Variant A và trả về `calc.can_execute=True`. Evaluator tiếp tục gửi candidate sang ARM.
- **Root Cause:** Thiếu rào chắn fail-closed khi Variant C không thể hình thành.
- **Fix:** Chuyển sang trả về `StopApplicationResult(allowed=False, selected_sl=None, calculation=None)` khi ở chế độ `PAPER_STRUCTURAL` mà thiếu anchor hoặc ATR không khả dụng. Khối ARM từ chối dứt khoát không mở lệnh.

### Probe R2 — `resolve_stop_anchor`
- **Baseline Defect:** Continuation / FVG chọn pivot mới nhất trong toàn bộ lịch sử, kể cả pivot xảy ra sau break bar của leg.
- **Root Cause:** Thiếu ràng buộc `leg_cutoff_ms` giới hạn thời gian xảy ra của pivot bảo vệ phải trước điểm phá vỡ.
- **Fix:** Đưa `leg_cutoff_ms` vào `resolve_stop_anchor` và `resolve_bound_stop_leg`. Loại bỏ toàn bộ các pivot có `occurred_at_ms > leg_cutoff_ms`. Sắp xếp các pivot hợp lệ giảm dần theo thời gian (gần điểm break nhất trước).

### Probe R3 — `compute_stop_atr`
- **Baseline Defect:** Timeframe M5 bị gán nhầm thời lượng nến mặc định 60,000ms thay vì 300,000ms. Kết quả `last_closed_at_ms` bị ghi nhận sớm 4 phút.
- **Root Cause:** `compute_stop_atr` mặc định `bar_duration_ms=60_000` cho mọi timeframe.
- **Fix:** Thiết lập từ điển chuẩn `TIMEFRAME_MS = {"1M": 60_000, "5M": 300_000}`. Từ chối mọi sai lệch thời lượng (`TIMEFRAME_DURATION_MISMATCH`). Đảm bảo `last_closed_at_ms = open_time + 300_000` với M5.

### Probe R4 — `advance_stop_comparison`
- **Baseline Defect:** Hàm sử dụng `updated = dict(plan_state)` (shallow copy), dẫn đến việc chỉnh sửa nested dictionary làm thay đổi trực tiếp dữ liệu đầu vào của caller.
- **Root Cause:** Thiếu tính thuần khiết (purity) trong xử lý tiến trình nghiên cứu.
- **Fix:** Chuyển sang sử dụng `copy.deepcopy(plan_state)`. Đầu vào của caller giữ nguyên vẹn 100%.

### Probe R5 — `advance_stop_comparison` Quote Validation
- **Baseline Defect:** Quote rỗng hoặc không hợp lệ (`{}`) vẫn kích hoạt chuyển trạng thái từ `WAIT_ENTRY` sang `OPEN`.
- **Root Cause:** Thiếu bước kiểm tra tính hợp lệ toán học của quote trước khi kích hoạt biến thể.
- **Fix:** Kiểm tra chặt chẽ `math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask >= bid`. Nếu quote rỗng hoặc không hợp lệ, giữ nguyên trạng thái `WAIT_ENTRY`.

### Probe R6 — `update_stop_placement_policy` Database CAS Fence
- **Baseline Defect:** Hàm chỉ đọc ORM entity và so sánh `expected_version` trong bộ nhớ Python rồi upsert thông thường. Session giữ cache cũ có thể ghi đè phiên bản mới của session khác.
- **Root Cause:** Thiếu câu lệnh SQL có điều kiện (`WHERE value == expected_version`) tại tầng cơ sở dữ liệu.
- **Fix:** Triển khai conditional SQL `UPDATE models.SystemConfig WHERE key='stop_placement_v1.version' AND value=str(expected_version)`. Nếu `rowcount != 1`, truy vấn giá trị scalar thực tế từ DB và ném `StopPolicyConflictError(code="STALE_STOP_POLICY_VERSION", fresh_version=actual_ver)`.

---

## 3. Production Runtime Wiring for Research

- **Điểm nối sở hữu duy nhất:** `ExecutionConsumer.evaluate_quote_sync` được gắn hàm `advance_active_stop_comparisons(db, quote, as_of_ms=proc_now_ms)`.
- **Độc lập luồng giao dịch:** Nghiên cứu được kích hoạt sau khi xử lý xong lệnh thực tế, trên cả hai nhánh:
  1. Khi đang có lệnh mở hoặc lệnh vừa đóng SL/TP.
  2. Khi không có lệnh mở nào trên tài khoản.
- **Tiếp tục theo dõi sau khi lệnh thực đóng:** Khi Variant A đóng SL trên tài khoản thực, `advance_active_stop_comparisons` tiếp tục theo dõi diễn biến của Variant B và C trên các tick tiếp theo để đo lường MAE/MFE và kết quả sau SL.

---

## 4. End-to-End Walkthrough: SHORT & LONG Examples

### A. SHORT Walkthrough (Variant C dưới `PAPER_STRUCTURAL`)
1. **Setup & POI:** Vùng cản FVG tại 2000.0. M1 Continuation SHORT được phát hiện.
2. **Anchor Leg:** Đỉnh bảo vệ của leg phá vỡ `PROTECTED_LEG_HIGH` tại 2008.0 (xảy ra trước break bar).
3. **ATR & Buffer:** ATR M1 đo được là 1.50. Dynamic buffer = 1.50 (dominant: `atr_m1`).
4. **SL Cấu Trúc:** `selected_sl = 2008.0 + 1.50 = 2009.50` (làm tròn outward lên trên cho lệnh SHORT).
5. **TP Bất Biến:** TP cơ sở giữ nguyên tại 1975.0 (không kéo dãn TP để bù SL).
6. **Canonical Sizing:**
   - Vốn: 1000 USDT | Rủi ro: 0.25% (Ngân sách: 2.50 USDT) | Đòn bẩy: 5x.
   - Khoảng cách SL: 9.50 USDT | Khoảng cách TP: 25.00 USDT.
   - Khối lượng tính toán: 0.22 oz (giảm so với 0.45 oz của Variant A khoảng cách 5.0).
   - Net Risk: 2.49 USDT <= 2.50 USDT (bảo toàn ngân sách rủi ro).
   - Net R:R: 1:2.15 >= 2.0 (đạt ngưỡng).
7. **ARM Barrier:** `validate_applied_stop_decision` xác nhận `cand_sl == calc_sl == decision_sl == 2009.50`. Lưu snapshot vào `cost_snapshot`.
8. **Fill:** Khớp lệnh tại 1999.90 (sau trượt giá bán 0.10 USDT).
9. **Exit:** Thị trường chạm 1975.0, `TradeLifecycleService` đóng lệnh `TP_HIT` với lãi ròng dương.
10. **Review:** Phân loại bài học `GREEN` (INFO), ghi nhận `applied_variant="C"` có căn cứ cấu trúc.

### B. LONG Walkthrough (Variant C dưới `PAPER_STRUCTURAL`)
1. **Setup:** M1 Continuation LONG tại 2000.0.
2. **Anchor Leg:** Đáy bảo vệ `PROTECTED_LEG_LOW` tại 1992.0 (xảy ra trước điểm phá đỉnh).
3. **SL Cấu Trúc:** `selected_sl = 1992.0 - 1.50 = 1990.50` (làm tròn outward xuống dưới cho lệnh LONG).
4. **TP Bất Biến:** TP cơ sở tại 2025.0.
5. **Canonical Sizing:** Khối lượng tính toán tự động điều chỉnh theo khoảng cách 9.50 USDT, đảm bảo rủi ro ròng không vượt 2.50 USDT.
6. **Fill & Exit:** Khớp lệnh BUY tại 2000.00, chốt lời tại 2025.00 khi Bid chạm TP. Phân loại bài học `GREEN`.

---

## 5. UI Contract & Display Accuracy

- **StopComparisonModal:** Đọc đồng thời cả `variants['A']` và `variant_a`, `variant_b`, `variant_c`. Hiển thị đầy đủ 3 mức SL, khoảng cách, khối lượng và Net R:R.
- **StopPlacementDetails:**
  - Badge chỉ hiển thị **"Xác minh cấu trúc (C)"** màu xanh ngọc (emerald) khi và chỉ khi `status === 'VALID' && anchor && applied_variant === 'C'`.
  - Hiển thị **"SL Legacy hợp lệ (A)"** màu hổ phách khi ở chế độ Shadow/Legacy và áp dụng SL cố định A.
  - Hiển thị **"Chờ bằng chứng cấu trúc"** khi ở chế độ C nhưng thiếu anchor.
  - Accordion kỹ thuật hiển thị đúng phương thức đo ATR (`SMA_TR_V1`) và không tự bịa thông số khi dữ liệu chưa được đo.

---

## 6. Hướng Dẫn Vận Hành & Khuyến Cáo Rủi Ro

1. **Chế độ Mặc Định:** Mặc định hệ thống vẫn giữ nguyên `SHADOW_COMPARE` để bảo vệ an toàn cho các lệnh đang chạy. Lệnh thực vẫn dùng SL cố định A, trong khi dữ liệu so sánh B và C được ghi nhận trong nghiên cứu Paper song song.
2. **Chuyển sang `PAPER_STRUCTURAL`:** Người dùng chủ động vào tab **Cài Đặt** (Settings), chọn `PAPER_STRUCTURAL` và nhấn **Lưu Cấu Hình**.
3. **Bảo toàn Lệnh Hiện Hữu:** Tuyệt đối không thay đổi SL, TP, hoặc khối lượng của các lệnh đã armed, open hoặc closed.
4. **Khuyến Cáo Trung Thực (Empirical Status):**
   - Trạng thái kỹ thuật: `IMPLEMENTATION_VERIFIED`.
   - Trạng thái thực nghiệm: `EMPIRICAL_UNPROVEN`.
   - SL cấu trúc rộng hơn giúp tránh bị quét bởi nhiễu giá nhỏ, nhưng đồng thời làm giảm khối lượng vị thế và đòi hỏi quãng đường TP dài hơn để đạt cùng tỷ lệ R:R. Cần thu thập đủ dữ liệu forward testing trước khi đánh giá hiệu quả lợi nhuận.
