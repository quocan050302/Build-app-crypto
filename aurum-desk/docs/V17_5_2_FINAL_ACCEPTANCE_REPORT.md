# AURUM V17.5.2 — FINAL ACCEPTANCE REPORT
**Bản Chốt Nghiệm Thu Kỹ Thuật Cho Structural Stop Loss & Causal Paper Research**

**Date:** 2026-10-24  
**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `e2b614b5e4c5f9748294eb1eca09995c4d490dd9`  
**Patch Proposal:** V17.5.2 Production Closure  
**Technical Status:** `IMPLEMENTATION_VERIFIED`  
**Empirical Status:** `EMPIRICAL_UNPROVEN` (chưa có dữ liệu forward PAPER đủ lớn, không cam kết tỷ lệ thắng hay không bị quét SL)  

---

## 1. Tóm Tắt Khép 4 Lỗi Ranh Giới Sản Xuất (P1 – P4)

| Lỗi | Mô tả tại HEAD (`e2b614b`) | Nguyên nhân gốc | Giải pháp V17.5.2 | Kết quả kiểm chứng |
| :--- | :--- | :--- | :--- | :--- |
| **P1** | **Caller metadata mismatch** | `evaluate_m1_continuation_candidate` & `evaluate_m1_fvg_continuation_candidate` truyền `{id, bar}` thô hoặc thiếu `start_ms`; `resolve_stop_anchor` không đọc được cutoff bar nên bỏ lọc thời gian, có thể chọn pivot sau break. | Thêm `build_selected_break_descriptor` chuẩn hóa; trích xuất `break_open_ms`, `broken_at_ms`, `known_at_ms`, `last_sl`/`last_sh` reference; kết nối `resolve_bound_stop_leg` làm resolver chính với kiểm tra `p_occ < break_time` và nến đối chiếu. | **PASS** (`test_probe_p1_caller_descriptor_mismatch` & `test_full_detector_to_exit_short_continuation`) |
| **P2** | **Research durability** | `ExecutionConsumer._process_quote_worker` gọi `evaluate_quote_sync` chỉ flush `advance_active_stop_comparisons`, không commit; đóng Session làm mất tiến trình so sánh A/B/C. | Tạo `advance_stop_comparisons_durable` mở `SessionLocal` riêng, advance, commit khi có thay đổi, rollback khi lỗi và đóng chắc chắn; tách biệt khỏi transaction giao dịch chính. | **PASS** (`test_probe_p2_research_worker_durability`: fresh Session đọc thấy state `OPEN` mà test không cần commit hộ) |
| **P3** | **Research causal timeline** | `advance_stop_comparison` không kiểm tra mốc thời gian quote trước plan cutoff (`quote_ts < plan_cutoff`), khiến quote quá khứ mở biến thể hồi tố. | Thêm Causal Timeline Gate trong `advance_stop_comparison`: bắt buộc `quote_ts >= plan_cutoff` và `as_of_ms >= plan_cutoff`; quote trước mốc giữ nguyên `WAIT_ENTRY`. | **PASS** (`test_probe_p3_causal_timeline_gate`) |
| **P4** | **Research eligibility** | `advance_stop_comparison` chỉ kiểm tra `can_execute=True`, bỏ qua `meets_min_rr=False`, dẫn đến biến thể không đạt R:R tối thiểu vẫn được mở mô phỏng. | Thêm Full Eligibility Check: bắt buộc đồng thời `is_valid and can_execute and meets_min_rr`. Biến thể không đạt Net R:R bị chặn mở (`WAIT_ENTRY`). | **PASS** (`test_probe_p4_research_variant_eligibility`) |

---

## 2. Bịt Khoảng Trống Nghiệm Thu: Detector-to-Exit Hoàn Chỉnh

Các bài kiểm tra trước đây (`test_v17_5_1_production_flow.py`) tạo `PaperOrder(state='armed')` bằng tay, bỏ qua khâu phát hiện (detector) và vũ trang (`_arm_paper_candidate`).  
V17.5.2 bổ sung suite `tests/test_v17_5_2_detector_to_exit.py` kiểm chứng toàn bộ chuỗi thực thi thực tế:

```
[15 Closed Candles M1] 
         │
         ▼
[evaluate_m1_continuation_candidate]
         │ (Tạo M1Candidate với bound leg descriptor & anchor C)
         ▼
[ScalpWatcherOrchestrator._arm_paper_candidate]
         │ (Kiểm tra validate_applied_stop_decision: cand_sl == calc_sl == decision_sl)
         │ (Tạo PaperOrder state='armed' với đầy đủ snapshot provenance)
         ▼
[ExecutionCoordinator.evaluate_orders_sync]
         │ (Khớp lệnh MARKET theo quote hợp lệ, cập nhật state='paper_open')
         ▼
[TradeLifecycleService.process_exit_tick]
         │ (Khớp TP/SL: SHORT exit trên ASK, LONG exit trên BID)
         │ (Cập nhật state='closed', ghi nhận realized_pnl, fees)
         ▼
[PostTradeReviewService.classify_post_trade_lesson]
         │ (Phân loại bài học GREEN cho lệnh có bằng chứng anchor cấu trúc hợp lệ)
```

### Các kịch bản đã kiểm chứng:
1. **SHORT Continuation End-to-End**: Nến giảm M1 phá vỡ swing low -> detector tạo candidate SHORT -> Orchestrator ARM lệnh với SL đặt trên đỉnh bảo vệ (có buffer quantized ceil) -> Coordinator fill trên BID -> Lifecycle exit trên ASK khi chạm TP -> Review ghi nhận GREEN.
2. **LONG Continuation End-to-End**: Nến tăng M1 phá vỡ swing high -> detector tạo candidate LONG -> Orchestrator ARM lệnh với SL đặt dưới đáy bảo vệ (quantized floor) -> Coordinator fill trên ASK -> Lifecycle exit trên BID khi chạm TP -> Review ghi nhận GREEN.
3. **Negative Guards Verification**: Chặn ARM khi Auto=False, hoặc Net R:R không đạt ngưỡng cấu hình, hoặc thiếu bằng chứng liên kết leg.

---

## 3. Walkthrough Chi Tiết Lệnh Thực Nghiệm

### A. Walkthrough Lệnh SHORT
- **Nến nguồn:** 15 nến M1, nến phá vỡ (break bar) mở tại `t = 240,000ms`, đóng tại `2,000.0`.
- **Reference bị phá:** Swing Low trước đó tại `2,000.5`.
- **Origin Pivot hợp lệ:** Swing High tại `2,002.0` mở lúc `60,000ms`, xác nhận lúc `180,000ms` (< break bar open `240,000ms`).
- **Pivot gây nhiễu sau break:** Swing High tại `2,001.0` xảy ra lúc `420,000ms` (> `240,000ms`) -> **BỊ LOẠI**.
- **Tính toán SL:**
  - Anchor High = `2,002.0`
  - Buffer = `max(tick_floor 0.05, 0.5 * ATR 0.20, spread 0.10) = 0.10`
  - Raw SL = `2,002.0 + 0.10 = 2,002.10` (SHORT ceil quantization)
- **Geometry:** `TP (1,996.0) < Entry (2,000.0) < SL (2,002.10)`
- **Thực thi:**
  - Lệnh armed: `actual_entry=None`, `state="armed"`
  - Quote vào: `bid=2000.0, ask=2000.10` -> Fill tại `2,000.0` (sau slippage: `1,999.90`)
  - Quote thoát: `ask=1995.90` -> Chạm TP -> Exit tại `1,996.0`
  - Đóng lệnh: `state="closed"`, `realized_pnl > 0`, `lesson="GREEN"`.

### B. Walkthrough Lệnh LONG
- **Nến nguồn:** 15 nến M1, nến phá vỡ mở tại `t = 240,000ms`, đóng tại `2,002.0`.
- **Reference bị phá:** Swing High trước đó tại `2,001.5`.
- **Origin Pivot hợp lệ:** Swing Low tại `1,998.0` mở lúc `60,000ms` (< `240,000ms`).
- **Tính toán SL:**
  - Anchor Low = `1,998.0`
  - Buffer = `0.10`
  - Raw SL = `1,998.0 - 0.10 = 1,997.90` (LONG floor quantization)
- **Geometry:** `SL (1,997.90) < Entry (2,000.0) < TP (2,004.0)`
- **Thực thi:**
  - Quote vào: `bid=1999.90, ask=2000.0` -> Fill tại `2,000.0`
  - Quote thoát: `bid=2004.10` -> Chạm TP -> Exit tại `2,004.0`
  - Đóng lệnh: `state="closed"`, `realized_pnl > 0`, `lesson="GREEN"`.

---

## 4. Các Bất Biến Được Bảo Toàn (Invariants Preserved)

1. **Rollout Policy & Mặc định:**
   - Mặc định khi thiếu cấu hình vẫn là `SHADOW_COMPARE` (chạy thực thi Variant A, ghi nhận và mô phỏng B/C song song).
   - Chỉ khi người dùng chủ động chọn và lưu `PAPER_STRUCTURAL` trong Settings thì lệnh PAPER mới mới thực thi theo Variant C.
   - Không can thiệp DB runtime để tự bật Variant C.
2. **Không nới lỏng SL hay tăng đòn bẩy ngầm:**
   - Không tăng hệ số ATR hoặc tick floor để cố tình giảm số lần chạm SL.
   - Khi SL rộng hơn, khối lượng (`quantity`) được tự động tính toán lại giảm xuống để giữ nguyên ngân sách rủi ro (`risk_usd`).
3. **Bảo toàn vị thế và lịch sử:**
   - Không cập nhật hồi tố `stop_loss`, `quantity`, hay `take_profit` của các lệnh đã `armed`, `open`, hoặc `closed`.
   - Không viết lại PnL hay bài học lịch sử.
4. **Cô lập Transaction:**
   - Worker research chạy trên transaction riêng, commit độc lập. Lỗi phân tích mô phỏng không làm rollback lệnh giao dịch thực tế đã khớp.
5. **Kỷ luật giao dịch:**
   - Giữ nguyên daily cap 3, tối đa 1 vị thế mở, setup-first mọi phiên (không ưu tiên phiên New York).
   - Exit side chính xác: SHORT ASK, LONG BID.

---

## 5. Kết Luận & Hướng Dẫn Vận Hành Tiếp Theo

1. **Trạng thái kỹ thuật:** Khép hoàn toàn 4 lỗi ranh giới (P1-P4) và hoàn thiện kiểm chứng detector-to-exit. Toàn bộ backend suites (99 tests) và frontend suites (501 tests) cùng frontend build đều đạt `PASS`.
2. **Trạng thái thực nghiệm:** `EMPIRICAL_UNPROVEN`. Việc sửa đúng logic kỹ thuật không đồng nghĩa với cam kết lệnh luôn thắng hoặc không bao giờ chạm SL. Hiệu quả giao dịch thực tế cần được đo lường qua thời gian giao dịch PAPER với dữ liệu thị trường trực tiếp.
3. **Khuyến nghị vận hành:**
   - Giữ nguyên cấu hình vốn và rủi ro hiện tại.
   - Tiếp tục quan sát các lệnh PAPER được thực thi theo Variant C, đối chiếu nhật ký bài học và các chỉ số MAE/MFE.
