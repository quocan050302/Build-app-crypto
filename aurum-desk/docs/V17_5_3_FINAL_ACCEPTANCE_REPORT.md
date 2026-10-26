# AURUM V17.5.3 — FINAL ACCEPTANCE REPORT
**Bản Chốt Nghiệm Thu Kỹ Thuật: Xác Minh Vị Trí SL Cấu Trúc, Khớp Khảo Sát Nghiên Cứu và Nghiệm Thu Chuỗi Production Toàn Diện**

**Date:** 2026-10-24  
**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `cb51fae448356c3c74af9a9057628d7a2b34dd00`  
**Patch Proposal:** V17.5.3 Production Verification Closure  
**Technical Status:** `IMPLEMENTATION_VERIFIED`  
**Empirical Status:** `EMPIRICAL_UNPROVEN` (chưa có dữ liệu forward PAPER đủ lớn, không cam kết tỷ lệ thắng hay không bị quét SL)  

---

## 1. Tóm Tắt Khép 7 Lỗi Ranh Giới Sản Xuất (R1 – R7) & Positive Control Twins

| Lỗi | Mô tả tại HEAD (`cb51fae`) | Nguyên nhân gốc | Giải pháp V17.5.3 | Kết quả kiểm chứng (Negative Probe + Positive Twin) |
| :--- | :--- | :--- | :--- | :--- |
| **R1** | **Missing Quote Time** | Quote truyền vào thiếu trường thời gian (`timestamp` / `time` là None hoặc `<= 0`), nhưng `advance_stop_comparison` vẫn cho phép trigger fill mô phỏng. | Bổ sung kiểm tra bắt buộc `quote_ts` phải tồn tại và `> 0`; quote thiếu thời gian bị từ chối mở và giữ nguyên trạng thái `WAIT_ENTRY`. | **PASS** (`test_r1_missing_quote_time_cannot_open` & `test_r1_positive_twin_valid_quote_time_opens`) |
| **R2** | **Future Quote Timestamp** | Quote có timestamp trong tương lai so với thời điểm đánh giá (`quote_ts > as_of_ms + 5000`), gây rò rỉ dữ liệu tương lai vào khảo sát. | Kiểm tra ràng buộc thời gian: từ chối các quote có `quote_ts > as_of_ms + 5000`, giữ nguyên trạng thái `WAIT_ENTRY`. | **PASS** (`test_r2_future_quote_cannot_open` & `test_r2_positive_twin_synchronous_quote_opens`) |
| **R3** | **Expired Comparison Plan** | Kế hoạch so sánh có `expires_at_ms`, nhưng khi thời gian vượt quá hạn chót, hệ thống không chuyển trạng thái mà vẫn để `WAIT_ENTRY` vô tận. | Khi `as_of_ms >= plan.expires_at_ms`, tự động chuyển trạng thái từ `WAIT_ENTRY` sang `EXPIRED`. | **PASS** (`test_r3_expired_plan_cannot_open` & `test_r3_positive_twin_unexpired_plan_opens`) |
| **R4** | **Order Intent Mismatch** | Biến thể so sánh là lệnh LIMIT hoặc STOP nhưng `advance_stop_comparison` luôn đối xử như MARKET và khớp ngay lập tức theo giá kế hoạch mà không kiểm tra giá quote thực tế. | Xây dựng `evaluate_comparison_entry` khớp hoàn toàn logic của `ExecutionCoordinator`: LIMIT SHORT chỉ khớp khi `bid >= limit_price`, LIMIT LONG chỉ khớp khi `ask <= limit_price`. | **PASS** (`test_r4_short_limit_away_from_entry_cannot_fill_at_planned_price` & `test_r4_positive_twin_short_limit_reached_fills`) |
| **R5** | **Future Break In Descriptor** | `build_selected_break_descriptor` và `resolve_bound_stop_leg` không kiểm tra `decision_cutoff_ms`, cho phép nến phá vỡ hoặc pivot xảy ra sau thời điểm quyết định gắn vào anchor. | Thêm kiểm tra nghiêm ngặt: loại bỏ các break có `b_open > cutoff` hoặc `b_close > cutoff` (trả về `{}`); loại bỏ các pivot có `p_occ > cutoff` hoặc `p_known > cutoff`. | **PASS** (`test_r5_future_break_cannot_bind_anchor` & `test_r5_positive_twin_causal_break_binds_anchor`) |
| **R6** | **NameError in Durable Handler** | Trong `advance_stop_comparisons_durable`, khối `except Exception` gọi `logger.warning(...)` nhưng module chưa khởi tạo biến `logger`, gây lỗi vỡ luồng không an toàn (`NameError`). | Khởi tạo `logger = logging.getLogger(__name__)` tại phạm vi module `stop_placement_research_service.py`. Khối lỗi trả về `{"status": "ERROR", "error": str(e), "changed": 0}` mà không sập. | **PASS** (`test_r6_durable_error_returns_error_without_nameerror` & `test_r6_positive_twin_durable_success`) |
| **R7** | **Stale Backlog Quote Fill** | Worker xử lý quote tồn đọng (backlog) có độ trễ lớn (`quote_age > 15,000ms` hoặc `is_stale=True`) nhưng vẫn kích hoạt fill lệnh nghiên cứu. | Bổ sung kiểm tra độ tươi của quote: quote cũ hơn 15,000ms hoặc được đánh dấu `is_stale` sẽ bị chặn fill, giữ lệnh ở trạng thái `WAIT_ENTRY`. | **PASS** (`test_r7_actual_worker_must_not_research_fill_stale_backlog` & `test_r7_positive_twin_actual_worker_fills_fresh_quote`) |

---

## 2. Toàn Bộ Chuỗi Sản Xuất: Từ `evaluate_cycle` Đến Post-Trade Review & API

V17.5.3 hoàn thiện chuỗi kiểm thử khép kín không phụ thuộc vào lệnh giả lập (`PaperOrder(state='armed')` bằng tay):

```
[M1/5M Candles + Live Context]
             │
             ▼
[ScalpWatcherOrchestrator.evaluate_cycle]
             │
             ├── Directional Bias: htf_bias="BEARISH"/"BULLISH", h1_alignment="ALIGNED"
             ├── Policy CAS: Đọc chính sách authoritative từ DB
             ├── Model Detector: evaluate_m1_continuation_candidate
             │     └── Causal Barrier: build_selected_break_descriptor loại bỏ break sau cutoff
             │     └── Bound Leg: resolve_bound_stop_leg chọn origin pivot trước break và trước cutoff
             │     └── Anchor: resolve_stop_anchor tính SL với buffer quantized outward
             └── Arming: _arm_paper_candidate xác minh validate_applied_stop_decision
                         và lưu PaperOrder với state='armed'
             │
             ▼
[ExecutionCoordinator.evaluate_orders_sync]
             │
             ├── Quota & Guards: Single active position, daily cap 3, risk budget invariant
             └── Fill Trigger: Khớp MARKET lệnh SHORT trên BID, LONG trên ASK
             │
             ▼
[order_plan_snapshot_service.extract_immutable_entry_metadata]
             │
             └── Cải tiến V17.5.3: Whitelist đầy đủ stop_placement, stop_comparison,
                 stop_comparison_plan_id, stop_mode_at_arm, stop_policy_version, applied_variant
                 vào cost_snapshot khi fill; ngăn chặn việc mất snapshot khi lệnh chuyển trạng thái!
             │
             ▼
[TradeLifecycleService.process_exit_tick & execute_close]
             │
             ├── Pure Exit: SHORT exit trên ASK, LONG exit trên BID khi chạm TP hoặc SL
             └── Trade Closure: state='closed', ghi nhận realized_pnl, fees, audit trail
             │
             ▼
[PostTradeReviewService.classify_post_trade_lesson]
             │
             └── Bài học: Ghi nhận GREEN cho lệnh có bằng chứng SL cấu trúc hợp lệ
             │
             ▼
[GET /api/v1/orders/{order_id}/stop-evidence]
             │
             └── Trả về 200 OK với status="AVAILABLE" (không còn bị rớt về "UNAVAILABLE_LEGACY")
```

---

## 3. Walkthrough Chi Tiết Lệnh Thực Nghiệm

### A. Walkthrough Lệnh SHORT Continuation (End-to-End)
1. **Dữ liệu đầu vào:**
   - 15 nến M1 hình thành xu hướng giảm, nến phá vỡ tại `t = 240,000ms` xuyên thủng Swing Low `2,000.5` và đóng tại `2,000.0`.
   - Origin Swing High tại `2,002.0` mở lúc `60,000ms`, xác nhận lúc `180,000ms` (< break open `240,000ms`).
   - Context: `htf_bias="BEARISH"`, `h1_alignment="ALIGNED"`, `decision_cutoff_ms = 240,000ms`.
2. **Khảo sát Anchor & SL:**
   - Anchor High = `2,002.0`.
   - Volatility Buffer = `max(tick_floor 0.05, 0.5 * ATR 0.20, spread 0.10) = 0.10`.
   - Raw SL = `2,002.0 + 0.10 = 2,002.10` (SHORT ceil quantization).
   - TP = `1,996.00`.
   - Geometry: `TP (1,996.0) < Entry (2,000.0) < SL (2,002.10)`.
3. **Thực thi qua Orchestrator & Coordinator:**
   - `evaluate_cycle` phát hiện candidate và vũ trang lệnh `armed_order_id` với `state="armed"`.
   - Quote khớp vào: `bid=2000.0, ask=2000.10` -> Coordinator fill SHORT tại `2,000.0`.
   - Lệnh chuyển sang `state="paper_open"`. `cost_snapshot` lưu giữ trọn vẹn thông tin `stop_placement`.
4. **Thoát lệnh & Hậu kiểm:**
   - Quote thoát: `ask=1995.90` (chạm Take Profit) -> Lifecycle khớp thoát tại `1,996.0`.
   - Lệnh chuyển sang `state="closed"`.
   - Phân loại bài học qua `classify_post_trade_lesson`: `lesson="GREEN"`.
   - Truy vấn API `GET /api/v1/orders/{order_id}/stop-evidence`:
     * HTTP 200 OK
     * `status: "AVAILABLE"`
     * `applied_variant: "PAPER_STRUCTURAL"`
     * `facts.anchor_price: 2002.0`
     * `facts.stop_loss: 2002.10`

### B. Walkthrough Lệnh LONG Continuation (End-to-End)
1. **Dữ liệu đầu vào:**
   - 15 nến M1 hình thành xu hướng tăng, nến phá vỡ tại `t = 240,000ms` vượt qua Swing High `2,001.5` và đóng tại `2,002.0`.
   - Origin Swing Low tại `1,998.0` mở lúc `60,000ms` (< break open `240,000ms`).
   - Context: `htf_bias="BULLISH"`, `h1_alignment="ALIGNED"`, `decision_cutoff_ms = 240,000ms`.
2. **Khảo sát Anchor & SL:**
   - Anchor Low = `1,998.0`.
   - Volatility Buffer = `0.10`.
   - Raw SL = `1,998.0 - 0.10 = 1,997.90` (LONG floor quantization).
   - TP = `2,006.00`.
   - Geometry: `SL (1,997.90) < Entry (2,002.0) < TP (2,006.0)`.
3. **Thực thi qua Orchestrator & Coordinator:**
   - `evaluate_cycle` tạo lệnh armed -> Coordinator fill LONG trên ASK `2,002.0`.
   - Lệnh chuyển sang `state="paper_open"`.
4. **Thoát lệnh & Hậu kiểm:**
   - Quote thoát: `bid=2006.10` (chạm Take Profit) -> Exit tại `2,006.0`.
   - Lệnh `state="closed"`. Phân loại bài học `GREEN`.
   - Truy vấn API `/stop-evidence`: `status: "AVAILABLE"`, `anchor_price: 1998.0`, `stop_loss: 1997.90`.

### C. Giao Diện Người Dùng & Geometry Trực Quan (Browser E2E)
- **SHORT Order:** Hiển thị đúng thứ tự hình học `TP < Entry < SL` cùng nhãn anchor `Origin Swing High: 2,002.00` và buffer `0.10`.
- **LONG Order:** Hiển thị đúng thứ tự hình học `SL < Entry < TP` cùng nhãn anchor `Origin Swing Low: 1,998.00` và buffer `0.10`.
- **Responsive Mobile (390x844):** Modal `StopComparisonModal` và component `StopPlacementDetails` co giãn hoàn hảo, không bị tràn ngang (`scrollWidth <= clientWidth`).

---

## 4. Kết Quả Đo Lường Nghiệm Thu Qua 10 Cổng Kỹ Thuật

Toàn bộ chỉ số được đo lường thực tế từ runner tự động `tools/run_v17_5_3_acceptance.py`:

| Cổng kiểm thử | Phạm vi | Số test chạy | Thời gian | Kết quả |
| :--- | :--- | :--- | :--- | :--- |
| **Gate 1** | V17.5.3 Seven Boundary Probes (R1 - R7 + positive control twins) | 14 | 1.49s | **PASS** (14/14) |
| **Gate 2** | V17.5.3 Full Production Cycle evaluate_cycle to Review & API | 3 | 1.35s | **PASS** (3/3) |
| **Gate 3** | V17.5.2 Baseline Suites (P1 - P4 & Detector to Exit) | 7 | 1.50s | **PASS** (7/7) |
| **Gate 4** | V17.5.1 Feature Suites & Probes (R1 - R6 red probes, CAS, costs) | 26 | 2.13s | **PASS** (26/26) |
| **Gate 5** | Existing V17.5 Regression Suites | 31 | 1.46s | **PASS** (31/31) |
| **Gate 6** | Prior Regressions (CG01, sessions, authority) | 35 | 4.29s | **PASS** (35/35) |
| **Gate 7** | Frontend Vitest Unit & Contract Suite | 501 (48 files) | 4.98s | **PASS** (501/501) |
| **Gate 8** | Frontend Production Build (`npm run build`) | - | 5.84s | **PASS** (0 errors) |
| **Gate 9** | Frontend Lint (`npm run lint`) | - | 0.42s | **PASS** (0 errors) |
| **Gate 10** | Playwright Browser Verification (Chrome E2E) | 3 | 6.97s | **PASS** (3/3) |

**Tổng hợp:**
- **Tổng số backend unit/integration tests:** 116 / 116 PASS (100%)
- **Tổng số frontend tests:** 501 / 501 PASS (100%)
- **Tổng số browser E2E tests:** 3 / 3 PASS (100%)
- **Tổng số test toàn hệ thống:** 620 / 620 PASS (100%)
- **Acceptance Manifest:** `artifacts/v17_5_3/acceptance.json`

---

## 5. Các Bất Biến Vận Hành & Khuyến Cáo Cho Nhà Giao Dịch

1. **Bảo tồn giới hạn rủi ro và luật chiến lược:**
   - Giới hạn 1 vị thế mở duy nhất (`single active position limit`).
   - Ngân sách rủi ro 3 giao dịch tối đa mỗi ngày (`daily cap = 3`).
   - Khung giờ hoạt động không đổi: NY Open / London theo cấu hình chuẩn.
   - Khi vị trí SL thay đổi khoảng cách, khối lượng vào lệnh (`quantity`) tự động điều chỉnh nghịch đảo để giữ mức rủi ro USD (`risk_usd`) cố định theo cấu hình.
2. **Khảo sát nghiên cứu hoàn toàn cách ly:**
   - Dữ liệu khảo sát so sánh biến thể A/B/C được vận hành trong transaction cô lập qua `advance_stop_comparisons_durable`.
   - Lỗi phát sinh trong luồng khảo sát (nếu có) được rollback sạch và ghi log mà không gây ảnh hưởng đến luồng giao dịch sản xuất.
3. **Cảnh báo thực nghiệm (Empirical Caveat):**
   - Trạng thái kỹ thuật: `IMPLEMENTATION_VERIFIED`.
   - Trạng thái thực nghiệm: `EMPIRICAL_UNPROVEN`. Mặc dù thuật toán đã được xác minh chặt chẽ về mặt nguyên nhân - kết quả và logic hình học, hiệu quả sinh lời hoặc tỷ lệ thắng của biến thể C cần được theo dõi qua dữ liệu forward PAPER trong thời gian đủ dài trước khi cân nhắc triển khai vốn thật.
