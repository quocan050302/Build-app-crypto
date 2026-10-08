# AURUM DESK — BỘ QUY TẮC CHIẾN LƯỢC SMC/ICT VÀ QUẢN TRỊ RỦI RO (Phiên bản 1.0.0)

Tài liệu này định nghĩa chính xác các quy tắc giao dịch có thể kiểm chứng được (deterministic rules), thuật toán tính toán và logic hệ thống được triển khai trong mã nguồn Aurum Desk.

---

## 1. Môi trường Giao Dịch & Quy Cách Hợp Đồng
- **Cặp giao dịch duy nhất:** `XAUUSDT` trên sàn Bitget (USDT-Margined Perpetual Futures).
- **Đặc tả hợp đồng:**
  - Quy cách hợp đồng: 1 hợp đồng = 1 ounce troy (oz) vàng thế giới.
  - Bước giá tối thiểu (Tick size): $0.01.
  - Bước khối lượng tối thiểu: 0.01 oz.
  - Đòn bẩy giả lập kiểm tra ký quỹ: Mặc định 3x.
  - Phí giao dịch giả lập: 0.04% mỗi chiều (round-trip 0.08%).
  - Trượt giá giả định (Slippage): $0.10 / oz trên lệnh thị trường.
- **Khung thời gian (Timeframes):**
  - Khung thời gian: `D` (Daily), `4H` (4 Giờ), `1H` (1 Giờ), `15M` (15 Phút), `5M` (5 Phút), `1M` (1 Phút).
  - Quy ước: 1M trên UI là một phút (`1m` nội bộ).
  - Tín hiệu cấu trúc chỉ sử dụng **nến đã đóng (Closed Candle)**. Nến đang chạy chỉ cập nhật hiển thị, không dùng làm căn cứ xác nhận cấu trúc.

---

## 2. Định Nghĩa Cấu Trúc Thị Trường SMC/ICT

### 2.1. Đỉnh/Đáy Xoay Chiều (Swing High / Swing Low)
- **Quy tắc 2 trái / 2 phải (2-bar pivot):**
  - Điểm $i$ là Swing High khi: $High_i > High_{i-1}$, $High_i > High_{i-2}$, $High_i > High_{i+1}$, $High_i > High_{i+2}$.
  - Điểm $i$ là Swing Low khi: $Low_i < Low_{i-1}$, $Low_i < Low_{i-2}$, $Low_i < Low_{i+1}$, $Low_i < Low_{i+2}$.
  - **Thời điểm xác nhận (Confirmation Time):** Một đỉnh/đáy tại nến $i$ chỉ được xác nhận tại thời điểm nến $i+2$ đóng cửa. Tuyệt đối không sử dụng thông tin tương lai trong backtest/replay trước thời điểm $i+2$.

### 2.2. Xu Hướng Thị Trường (Market Trend)
- **Tăng (BULLISH):** Chuỗi đỉnh sau cao hơn đỉnh trước (HH) và đáy sau cao hơn đáy trước (HL).
- **Giảm (BEARISH):** Chuỗi đỉnh sau thấp hơn đỉnh trước (LH) và đáy sau thấp hơn đáy trước (LL).
- **Đi ngang (RANGING):** Khi chưa tạo đủ chuỗi swing phân định rõ.

### 2.3. Phá Vỡ Cấu Trúc (BOS & CHoCH)
- **BOS (Break of Structure):** Giá đóng cửa của nến vượt qua Swing Point đã xác nhận theo đúng hướng xu hướng hiện hành.
- **CHoCH (Change of Character):** Giá đóng cửa của nến phá vỡ Swing Point đã xác nhận ngược lại xu hướng trước đó, báo hiệu tiềm năng đảo chiều xu hướng.

### 2.4. Quét Thanh Khoản (Liquidity Sweep)
- **Quy tắc Sweep:** Râu nến (wick) vượt qua mức đỉnh/đáy đã xác nhận trước đó (hoặc PDH/PDL), nhưng giá đóng cửa (close) lại rút chân về phía trong mức đó.
- Nến quét thanh khoản là điều kiện tiên quyết kích hoạt trạng thái xem xét vào lệnh (Candidate).

### 2.5. Vùng Mất Cân Bằng (Fair Value Gap - FVG)
- **Bullish FVG:** $Low_{i} > High_{i-2}$. Vùng khoảng trống giá nằm giữa $[High_{i-2}, Low_{i}]$.
- **Bearish FVG:** $High_{i} < Low_{i-2}$. Vùng khoảng trống giá nằm giữa $[High_{i}, Low_{i-2}]$.
- **Vòng đời FVG (Lifecycle):**
  1. `created`: Được hình thành bởi chuỗi 3 nến.
  2. `confirmed`: Nến thứ 3 đóng cửa xác nhận khoảng trống.
  3. `partially_mitigated`: Nến sau đó chạm vào vùng FVG nhưng chưa vượt qua đầu bên kia.
  4. `fully_mitigated`: Giá vượt qua toàn bộ khoảng trống giá, FVG hết hiệu lực.

### 2.6. Vùng Giá Chiết Khấu / Đắt Đỏ (Premium & Discount)
- Dealing Range: Xác định bởi Swing High và Swing Low gần nhất.
- Mức cân bằng (Equilibrium): $EQ = \frac{Dealing\_High + Dealing\_Low}{2}$.
- **Discount (< EQ):** Vùng giá rẻ, chỉ xem xét tìm kiếm lệnh Mua (LONG).
- **Premium (> EQ):** Vùng giá đắt, chỉ xem xét tìm kiếm lệnh Bán (SHORT).

---

## 3. Quy Trình Kích Hoạt Setup Giao Dịch v1

Mô hình setup chuẩn mực tuân thủ chuỗi sự kiện có thứ tự thời gian:
$$\text{Liquidity Sweep} \longrightarrow \text{Displacement / CHoCH} \longrightarrow \text{Retracement vào FVG} \longrightarrow \text{Execution Confirmation}$$

### 3.1. Các Điều Kiện Kiểm Chứng (Checklist)
1. **Đồng thuận đa khung thời gian:** D và 4H cùng xu hướng; 1H không đối lập. Nếu xung đột $\rightarrow$ Giữ trạng thái No-trade.
2. **Thanh khoản:** Phải có Liquidity Sweep đỉnh hoặc đáy gần nhất.
3. **Vị trí vào lệnh:** Long tại vùng Discount; Short tại vùng Premium.
4. **Vùng POI:** Có FVG hợp lệ còn hiệu lực làm điểm đỡ/cản.
5. **Điểm cắt lỗ (Stop Loss - SL):**
   - Lệnh Long: Đặt dưới mức thấp nhất của râu nến Sweep hoặc Swing Low gần nhất trừ đi $1.5 \times ATR(14)$ làm buffer.
   - Lệnh Short: Đặt trên mức cao nhất của râu nến Sweep hoặc Swing High gần nhất cộng thêm $1.5 \times ATR(14)$ làm buffer.
6. **Điểm chốt lời (Take Profit - TP):**
   - Đặt tại vùng thanh khoản đối diện (Swing High/Low đối diện) hoặc đảm bảo tối thiểu $2.5 \times \text{Stop Distance}$.
7. **Tỷ lệ R:R:**
   - Gross R:R $\ge 2.0$.
   - Net R:R (sau phí giao dịch và trượt giá) $\ge 1.9$.

---

## 4. Công Thức Tính Toán Khối Lượng & R:R

### 4.1. Tỷ lệ Lợi Nhuận / Rủi Ro (Gross & Net RR)
- Lệnh Long:
  $$\text{Gross RR} = \frac{TP - Entry}{Entry - SL}$$
- Lệnh Short:
  $$\text{Gross RR} = \frac{Entry - TP}{SL - Entry}$$
- Net RR:
  $$\text{Net RR} = \frac{\text{Lợi nhuận ròng dự kiến sau phí và trượt giá}}{\text{Tổng chi phí rủi ro tối đa nếu chạm SL}}$$

### 4.2. Tính Khối Lượng Vị Thế (Position Sizing)
- Ngân sách rủi ro: $\text{Risk Amount} = \text{Vốn hiện tại} \times 0.5\%$.
- Rủi ro trên mỗi đơn vị:
  $$\text{Risk Per Unit} = |Entry - SL| + \text{Slippage} + (Entry + SL) \times \text{Fee Rate}$$
- Khối lượng hợp đồng:
  $$\text{Quantity} = \max\left(0.01, \left\lfloor \frac{\text{Risk Amount}}{\text{Risk Per Unit}} \times 100 \right\rfloor / 100\right)$$

---

## 5. Quản Trị Rủi Ro & Bộ Đếm Ngày (Paper Trading Rules)

1. **Vốn giả lập ban đầu:** 1.000,00 USDT.
2. **Rủi ro mỗi lệnh:** Cố định 0,5% vốn ($5.00 trên $1.000). Tối đa cấu hình 1,0%.
3. **Số lệnh tối đa trong ngày:** Tối đa **3 lần mở lệnh/ngày** theo giờ UTC+7. Không mở thêm khi đã đạt 3 lệnh.
4. **Số vị thế đồng thời:** Tối đa **1 vị thế mở** tại một thời điểm. Tuyệt đối không nhồi lệnh (averaging down), không gấp thếp (martingale).
5. **Dừng giao dịch chuỗi thua:** Dừng mở lệnh mới trong ngày nếu chịu **2 lệnh lỗ liên tiếp**.
6. **Giới hạn lỗ ngày (Daily Loss Cap):** 1,5% vốn đầu ngày ($15.00). Nếu tổng lỗ ghi nhận cộng với rủi ro lệnh mới có thể vượt quá $15.00 thì lập tức chặn mở mới.
7. **Thời gian nghỉ ngơi (Cooldown):** Bắt buộc nghỉ **30 phút** sau khi đóng bất kỳ vị thế nào trước khi được phép mở lệnh tiếp theo.
8. **Khung giờ Blackout tin tức:**
   - Tin USD High Impact (CPI, NFP, GDP, PCE, Retail Sales, Jobless Claims): Chặn mở lệnh trước 30 phút và sau 15 phút.
   - Tin FOMC / Lãi suất Fed: Chặn mở lệnh trước 60 phút và sau 30 phút.
9. **Xử lý nến lưỡng lự (Ambiguous Bar):** Nếu trong cùng một nến cả TP và SL đều bị chạm tới mà không có tick xác định thứ tự, hệ thống áp dụng nguyên tắc thận trọng tối đa: **SL hit trước**.
