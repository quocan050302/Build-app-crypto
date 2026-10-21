# V16.39.15 ACCEPTANCE REPORT
## Báo Cáo Nghiệm Thu Hợp Nhất Chính Sách Net R:R Trên Luồng Paper Entry và Chart

**Ngày nghiệm thu:** 2026-10-21  
**Phiên bản:** Aurum V16.39.15  
**Nhánh Git:** `fix/v14-1-rr-flow`  
**Commit Baseline:** `fix(v16.39.13): close score detail lifecycle and response identity gaps` (`1844cb595db0fb295c379470a84567fc6418c56c`)

---

### 1. Tóm Tắt Mục Tiêu & Vấn Đề Đã Giải Quyết (Executive Summary)

Khi người dùng cấu hình `min_net_rr = 1.5` trên hệ thống PAPER, các lệnh có tỷ lệ Net R:R trong vùng $[1.5, 2.0)$ trước đây gặp xung đột nghiêm trọng:
1. **Bị từ chối hoặc chặn oan tại tầng chiến lược:** `evaluate_setup_eligibility`, `StrategyService.evaluate_upcoming_setups`, và manual ARM endpoint tại `main.py` hardcode `min_net_rr: 2.0`, dẫn đến setup bị gán nhãn `INSUFFICIENT_RR` hoặc `can_arm: False`.
2. **Hiển thị sai lệch trên biểu đồ:** Plugin `RiskRewardPrimitive.ts` hardcode `d.estimatedNetRR >= 2.0` để quyết định màu sắc, hiển thị cảnh báo `Chưa đạt R:R` màu vàng cho các setup đã đủ điều kiện $\ge 1.5$.
3. **Phá vỡ tính bất biến của Snapshot:** Khi lệnh đã ARM ở ngưỡng 1.5, nếu DB thay đổi lên 2.0, lệnh bị kiểm tra sai ngưỡng hoặc hiển thị sai trạng thái trong nhật ký giao dịch.

**Bản vá V16.39.15 đã giải quyết triệt để:**
- Thiết lập dịch vụ trung tâm `rr_policy_service.py` với mô hình ưu tiên phân cấp rõ ràng (SystemConfig > TradingPolicy > Default 2.0) và cơ chế fail-closed chặt chẽ khi dữ liệu DB bị lỗi.
- Mở rộng sàn cho phép của `min_net_rr` xuống $1.5$ trong chế độ PAPER, bảo vệ nghiêm ngặt chống boolean, `None`, số âm, NaN, hoặc vô cực.
- Đồng bộ hóa toàn bộ luồng Manual ARM, Auto ARM, Setup Eligibility, Preview Quality, và Execution Coordinator tuân thủ ngưỡng chính sách động.
- Bảo vệ tính bất biến của `min_net_rr_at_arm` trong snapshot: Lệnh đã ARM được bảo vệ ngưỡng fill độc lập với các thay đổi chính sách DB sau đó.
- Cập nhật frontend: primitive renderer, draft preview, chart overlay, diagnostic card, drawer chi tiết, và catalog thông báo tiếng Việt.

---

### 2. Bằng Chứng Nghiệm Thu Tự Động (Automated Verification Evidence)

#### 2.1 Frontend Kiểm Thử Đơn Vị (Vitest)
```bash
npm --prefix frontend test
```
- **Tổng số tệp test:** 34 / 34 passed (100%).
- **Tổng số ca kiểm thử:** 357 / 357 passed (100%).
- **Thời gian thực thi:** 2.32s.
- **Bao gồm bộ test chuyên biệt V16.39.15:** `src/__tests__/v16_39_15_rr_policy_frontend.test.ts` (5 tests pass):
  - A12.1: `resolvePrimitiveRrStatus` tô màu xanh (`#26a69a`) khi net RR $\ge 1.5$.
  - A12.2: `resolvePrimitiveRrStatus` tô màu vàng cảnh báo (`#eab308`) và hiển thị `· Chưa đạt R:R` khi net RR $< minNetRR$.
  - A12.3: `resolvePrimitiveRrStatus` tô màu xám trung tính (`#a1a1aa`) và hiển thị `· Ngưỡng chưa xác định` cho lệnh lịch sử không có snapshot.
  - A12.4: `calculateClientRiskReward` tôn trọng chính xác `minNetRR=1.5` trên setup LONG.
  - A12.5: `calculateClientRiskReward` tôn trọng chính xác `minNetRR=1.5` trên setup SHORT.

#### 2.2 Frontend Linter (Oxlint / ESLint)
```bash
npm --prefix frontend run lint
```
- **Kết quả:** 0 lỗi (0 errors), 39 cảnh báo cũ (39 warnings) trên 116 tệp tin.

#### 2.3 Frontend Production Build
```bash
npm --prefix frontend run build
```
- **Kết quả:** `tsc -b && vite build` hoàn thành không có lỗi (Exit Code 0).
- **Tài nguyên sinh ra:**
  - `dist/index.html` (0.45 kB)
  - `dist/assets/index-D0INdZpl.css` (45.66 kB)
  - `dist/assets/index-Ct-ehXzP.js` (1,063.64 kB)

#### 2.4 Backend Bộ Kiểm Thử Chuyên Biệt V16.39.15
```bash
PYTHONPATH=. ./venv/bin/pytest tests/test_v16_39_15_rr_policy_unification.py -v
```
- **Kết quả:** 35 / 35 passed (100%).
- **Chi tiết các nhóm ca kiểm thử:**
  - **A01 - A03 (Phân cấp ưu tiên):** Xác thực thứ tự SystemConfig (1.5) > TradingPolicy (2.0) > Default (2.0).
  - **A04 (Fail-closed khi DB hỏng):** Dữ liệu chuỗi rỗng, không phải số, số âm, NaN, Inf, boolean đều ném `RrPolicyConfigError`, không fallback âm thầm.
  - **A05 & A13 (Kiểm tra dữ liệu & Biên):** Chặn giá trị $< 1.5$, chặn boolean/None; kiểm tra biên 1.49 (bị chặn), 1.50 (chấp nhận), 1.51 (chấp nhận).
  - **A06 & A14 (Giao dịch nguyên tử & Rollback):** Staging không tự ý commit; lỗi ở trường phụ rollback toàn diện cả 2 bảng.
  - **A07 (PlanFinancialContext):** Mang đầy đủ `min_net_rr`, nguồn `SYSTEM_CONFIG` và mã băm 8 ký tự.
  - **A08 (EligibilityService):** Setup net RR ~ 1.84 hợp lệ ở ngưỡng 1.5 và bị chặn ở ngưỡng 2.0.
  - **A09 (Frozen Order Immutability):** Lệnh frozen tính `meets_min_rr` theo snapshot 1.5 dù DB hiện hành là 2.0.
  - **A10 (Coordinator Fill):** Lệnh armed ở ngưỡng 1.5 khớp thành công tại net RR 1.84 dù DB hiện hành là 2.0.
  - **A11 (Coordinator Degradation Reject):** Trượt giá làm net RR suy giảm dưới snapshot 1.5 bị từ chối an toàn với mã `NET_RR_TOO_LOW`.
  - **A12 (Snapshot Persistence):** Snapshot lưu trữ chính xác `min_net_rr_at_arm`.

#### 2.5 Backend Bộ Kiểm Thử Hồi Quy V16.39 (Regression Suites)
- `tests/test_v16_39_*.py`: 225 / 225 passed (100%).
- `tests/test_v10_4_cost_and_rr.py`, `tests/test_v14_1_rr_preview.py`: 23 passed.
- `tests/test_v16_35_rr_propagation_telegram_proximity.py`, `tests/test_v16_36_paper_tonight_production_regression.py`, `tests/test_v16_38_adaptive_paper_readiness.py`: 117 passed.

---

### 3. Checklist Nghiệm Thu Chi Tiết (Acceptance Checklist)

| Mục kiểm tra | Trạng thái | Ghi chú nghiệm thu |
|---|:---:|---|
| Không sửa công thức SMC, bias D1/H4/H1, triggers nến | ĐẠT | Giữ nguyên 100% logic cấu trúc thị trường |
| Không sửa công thức phí hoặc trượt giá | ĐẠT | Giữ nguyên mô hình Bitget USDT-M Classic |
| Ngưỡng tối thiểu PAPER cho phép $\ge 1.5$ | ĐẠT | Hạ sàn từ 2.0 xuống 1.5 có bảo vệ kiểu |
| Ưu tiên SystemConfig > TradingPolicy > Default | ĐẠT | Xác thực qua test A01, A02, A03 |
| Fail-closed khi cấu hình DB biến dạng | ĐẠT | Ném `RrPolicyConfigError`, xác thực qua test A04 |
| Snapshot lệnh đã ARM là bất biến | ĐẠT | `min_net_rr_at_arm` bảo vệ lệnh qua test A09, A10, A11 |
| Biểu đồ hiển thị màu sắc và text theo ngưỡng động | ĐẠT | Xanh khi $\ge minNetRR$, vàng khi thiếu, xám khi chưa rõ |
| Diagnostic Card hỗ trợ nhập và badge chưa lưu | ĐẠT | `min="1.5"`, badge `Đang áp dụng 1:x (Chưa lưu)` |
| Bổ sung mã lỗi `NET_RR_TOO_LOW` có giải thích | ĐẠT | Đã thêm vào `userMessageCatalog.ts` |
| Toàn bộ Frontend build & test sạch lỗi | ĐẠT | 357 vitest pass, 0 lint error, tsc build pass |
| Toàn bộ Backend test chuyên biệt & hồi quy pass | ĐẠT | 35 test unification pass, > 365 regression pass |

---

### 4. Kết Luận
Bản vá Aurum V16.39.15 đã hoàn thành toàn diện, bảo đảm tính an toàn cao nhất cho hệ thống giao dịch tự động và thủ công trong chế độ PAPER, bảo toàn trọn vẹn dữ liệu lịch sử và logic quản lý vốn. Bản vá sẵn sàng để commit và push lên nhánh `fix/v14-1-rr-flow`.
