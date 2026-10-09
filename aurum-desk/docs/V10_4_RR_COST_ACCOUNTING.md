# ANTIGRAVITY — V10.4: Thống Nhất R:R, Chi Phí và PnL Toàn Bộ Luồng LONG/SHORT

## 1. Mục Tiêu & Phạm Vi (Scope)
V10.4 chuẩn hóa toàn diện công thức tính toán Risk:Reward (R:R), chi phí giao dịch (phí maker/taker + trượt giá slippage), ký quỹ, thanh lý và PnL thực nhận xuyên suốt tất cả các chặng trong hệ thống Aurum:
`strategy` → `watch setup` → `resolver/eligibility` → `preview/draft/drag` → `arm/manual/Auto` → `executable quote` → `fill` → `exit` → `audit/equity/journal/lessons` → `Telegram`.

Tất cả các thành phần tuân thủ nghiêm ngặt các nguyên tắc:
- **Paper-only**: Không kích hoạt lệnh live lên sàn giao dịch thực tế.
- **Bảo toàn dữ liệu Runtime**: Cơ sở dữ liệu runtime `aurum_desk.db` được bảo vệ nguyên vẹn qua sentinel fixture, không bị truncate hay sửa đổi vị thế đang mở.
- **Tính toán trung thực**: Không tự ý co hẹp SL hoặc kéo dãn TP để làm đẹp tỷ lệ R:R; không hứa hẹn lợi nhuận ảo; ngưỡng `minNetRR` giữ nguyên mặc định 2.0 (1:2.00).

---

## 2. Chuẩn Hóa Công Thức Kinh Tế (Authoritative Formulations)

### 2.1. Hình Học Giá (Price Geometry)
Kiểm tra tính thứ tự đơn điệu nghiêm ngặt (không dùng hàm `abs()` che giấu sai lệch hướng):
- **LONG**: $SL < Entry < TP$ (Khoảng cách dừng: $\Delta_{SL} = Entry - SL$; Khoảng cách mục tiêu: $\Delta_{TP} = TP - Entry$).
- **SHORT**: $TP < Entry < SL$ (Khoảng cách dừng: $\Delta_{SL} = SL - Entry$; Khoảng cách mục tiêu: $\Delta_{TP} = Entry - TP$).
- Các trường hợp hướng không xác định hoặc giá không dương/vô hạn đều bị từ chối ngay với mã lỗi `UNKNOWN_DIRECTION` hoặc `PRICES_NOT_FINITE`.

### 2.2. Tỷ Lệ Gross R:R & PnL Theo Giá (Price-Only Gross Economics)
$$\text{Gross Loss} = Q \times M \times \Delta_{SL}$$
$$\text{Gross Reward} = Q \times M \times \Delta_{TP}$$
$$\text{Gross R:R} = \frac{\text{Gross Reward}}{\text{Gross Loss}} = \frac{\Delta_{TP}}{\Delta_{SL}}$$

- Đối với cả lệnh **LONG** và **SHORT**, Gross R:R **luôn là một số dương**, phản ánh tỷ lệ khoảng cách giá mục tiêu trên khoảng cách giá cắt lỗ. Công thức không bao giờ bị đảo ngược hay mang dấu âm đối với SHORT.

### 2.3. Bóc Tách Chi Phí & Net R:R (Separated Cost Breakdown)
Phí giao dịch và trượt giá được tính toán riêng biệt cho 2 nhánh (nhánh dừng lỗ và nhánh chốt lời):

| Thành phần | Nhánh Cắt Lỗ (SL Branch) | Nhánh Chốt Lời (TP Branch) | Ghi chú |
| :--- | :--- | :--- | :--- |
| **Phí Mở Lệnh (Entry Fee)** | $Q \times M \times Entry \times r_{\text{taker}}$ | $Q \times M \times Entry \times r_{\text{taker}}$ | $r_{\text{taker}} = 0.0004$ (0.04%) |
| **Phí Đóng Lệnh (Exit Fee)** | $Q \times M \times SL \times r_{\text{taker}}$ | $Q \times M \times TP \times r_{\text{exit}}$ | $r_{\text{maker}} = 0.0002$ nếu Limit/Maker; $0.0004$ nếu Market/Taker |
| **Trượt Giá Mở (Entry Slippage)** | $Q \times M \times \text{slip}_{Entry}$ | $Q \times M \times \text{slip}_{Entry}$ | Bỏ qua nếu lệnh khớp thực tế đã chịu slippage |
| **Trượt Giá Đóng (Exit Slippage)** | $Q \times M \times \text{slip}_{SL}$ | $Q \times M \times \text{slip}_{TP}$ | Mặc định 0.0 cho Maker TP (F1); cấu hình rõ ràng cho Market TP (F1NEW) |

**Tổng Rủi Ro Ròng (Net Risk USDT)**:
$$\text{Net Risk} = \text{Gross Loss} + \text{Entry Fee} + \text{SL Exit Fee} + \text{Entry Slippage} + \text{SL Slippage}$$

**Lợi Nhuận Ròng Dự Kiến (Net Reward USDT)**:
$$\text{Net Reward} = \text{Gross Reward} - \text{Entry Fee} - \text{TP Exit Fee} - \text{Entry Slippage} - \text{TP Slippage}$$

**Tỷ Lệ Net R:R Dự Kiến**:
$$\text{Net R:R} = \frac{\text{Net Reward}}{\text{Net Risk}}$$

**Điều Kiện Chấp Thuận (Eligibility Policy)**:
- Lệnh chỉ hợp lệ khi $\text{Net Reward} > 0$ và $\text{Net R:R} \ge \text{minNetRR}$ (mặc định 2.0).
- Kiểm tra số thực chính xác không làm tròn lên non nớt (ví dụ 1.9999 không bị làm tròn thành 2.00 để lách luật).

### 2.4. Khối Lượng Vị Thế (Quantity Sizing)
Sử dụng phép chia nguyên số học Decimal:
$$\text{Raw Qty} = \frac{\text{Budget USDT}}{\text{Risk Per Unit}}$$
$$Q = \left\lfloor \frac{\text{Raw Qty}}{\text{Step}} \right\rfloor \times \text{Step}$$
Tránh hoàn toàn lỗi biểu diễn số chấm động IEEE-754 (như hiện tượng $0.30 \to 0.29$).

---

## 3. Mô Hình Ký Quỹ & Thanh Lý Bitget USDT-M (10 Tiers)

Khung bậc ký quỹ 10 tầng của Bitget Futures:
- **Tầng 1**: Tối đa $20,000 USDT notional, đòn bẩy tối đa 100x, MMR 0.50%, Deduction 0.0.
- **Tầng 2**: $20,000 - $200,000 USDT, đòn bẩy 75x, MMR 1.00%, Deduction 0.0.
- **Tầng 3**: $200,000 - $500,000 USDT, đòn bẩy 50x, MMR 1.50%, Deduction 0.0.
- ...
- **Tầng 10**: $100,000,000 - $200,000,000 USDT, đòn bẩy 1x, MMR 60.00%, Deduction 0.0.

Giá thanh lý cách ly (Isolated Liquidation Price - $LP$):
- **LONG**: $LP = \frac{Entry \cdot Q \cdot M - \text{InitialMargin} - \text{Deduction}}{Q \cdot M \cdot (1 - MMR - r_{\text{taker}})}$
- **SHORT**: $LP = \frac{Entry \cdot Q \cdot M + \text{InitialMargin} + \text{Deduction}}{Q \cdot M \cdot (1 + MMR + r_{\text{taker}})}$

Hệ thống tự động kích hoạt blocker `LIQUIDATION_BEFORE_SL` hoặc `LIQUIDATION_BUFFER_TOO_TIGHT` nếu $LP$ nằm trước hoặc quá gần mức cắt lỗ $SL$.

---

## 4. Đồng Bộ Hóa Xuyên Suốt Vòng Đời Lệnh

1. **Preview, Draft & Kéo Thả (Chart Drag)**:
   - Kéo biên phải (Right-edge drag) **chỉ thay đổi số nến chiếu `projectedBars`**, giữ nguyên 100% các mức giá, khối lượng, Gross/Net R:R (S04).
   - Kéo mức giá (Entry, SL, TP) gửi kèm `quantityOverride` bảo lưu khối lượng người dùng chỉ định, không tự động co giãn size ngầm.
2. **Arming & Lưu Vết (Trace Immutability)**:
   - Ghi nhận đầy đủ snapshot quyết định `arm_decision_snapshot` và snapshot chi phí `cost_snapshot`.
3. **Khớp Lệnh (Fill Execution)**:
   - Ghi nhận `cost_snapshot` chính thức kèm tỷ lệ phí thực tế, hệ số hợp đồng `multiplier`, và bóc tách 6 chân chi phí.
4. **Đóng Vị Thế (Close & PnL Accounting)**:
   - Tính toán PnL gộp `gross_pnl = (exit_price - entry_price) * qty * mult * direction_mult`.
   - Khấu trừ chính xác phí mở (taker) và phí đóng (maker cho TP nếu có, taker cho SL).
   - Cập nhật `realized_pnl_net` và `realized_r` vào cơ sở dữ liệu.
5. **Giao Diện Người Dùng (Dual UI Display)**:
   - Sidebar và Drawer hiển thị song song cả Gross và Net: R:R Gross 1:X (Net 1:Y), Lỗ theo giá vs Lỗ ròng dự kiến, Lãi theo giá vs Lãi ròng dự kiến.
   - Thẻ hiển thị blocker chi tiết khi R:R ròng chưa đạt chuẩn.

---

## 5. Báo Cáo Nghiệm Thu Tự Động (Automated Test Report)

### 5.1. Backend Test Suite (Pytest)
```
======================= 343 passed, 1 warning in 10.56s ========================
```
- **Tổng số bài test backend**: 343 bài test.
- **Trạng thái**: **100% PASSED** (0 failures, 0 errors).
- **Bộ test mới `test_v10_4_cost_and_rr.py`**:
  - `test_c01_legacy_f1_regression_matches_decimal_oracle`: PASS (đối chiếu Decimal oracle độc lập).
  - `test_c02_f1new_explicit_tp_slippage_model`: PASS (mô hình trượt giá TP minh bạch).
  - `test_c03_f2_f3_short_and_long_geometry_gross_rr_and_pnl_signs`: PASS (Gross R:R SHORT > 0, dấu PnL nhất quán).
  - `test_c04_maker_vs_taker_fee_legs`: PASS.
  - `test_c05_entry_slippage_omitted_when_already_applied`: PASS.
  - `test_c06_multiplier_scaling`: PASS.
  - `test_c07_step_floored_quantity_exact_decimal`: PASS.
  - `test_c08_invalid_quantity_override_rejected`: PASS.
  - `test_c09_unknown_direction_rejected`: PASS.
  - `test_c10_non_positive_net_reward_blocked`: PASS.
  - `test_c11_meets_min_rr_unrounded_check`: PASS.
  - `test_c12_gross_metrics_invariant_under_leverage_change`: PASS.
  - `test_i01_bitget_10_tiers_and_zero_deduction`: PASS.
  - `test_i02_liquidation_buffer_checks`: PASS.
  - `test_p01_snapshot_to_dict_and_contract_fields`: PASS.
  - `test_x01_fill_records_actual_cost_snapshot`: PASS.
  - `test_x04_tp_exit_pnl_formula`: PASS.
  - `test_x05_sl_exit_pnl_formula_short`: PASS.

### 5.2. Frontend Test Suite (Vitest)
```
 Test Files  10 passed (10)
      Tests  94 passed (94)
```
- **Tổng số bài test frontend**: 94 bài test qua 10 test files.
- **Trạng thái**: **100% PASSED** (0 failures, 0 errors).
- **Bộ test mới `v10_4_cost_and_rr.test.ts`**:
  - C01 - C12 parity tests: PASS.
  - I01 10 tiers test: PASS.
  - I02 Liquidation calculation test: PASS.
  - S04 Right-edge drag resize invariance test: PASS.

### 5.3. Frontend Production Build
```
✓ built in 806ms
dist/index.html                   0.45 kB │ gzip:   0.29 kB
dist/assets/index-q5xfsfcJ.css   37.28 kB │ gzip:   7.28 kB
dist/assets/index-DaDeFKNJ.js   804.32 kB │ gzip: 228.07 kB
```
- **Trạng thái**: **0 Errors, Clean Build**.
