# AURUM DESK - Strategy Rules v1

## 1. Môi trường & Khung thời gian
- Cặp giao dịch: **XAUUSDT** (Bitget USDT-margined perpetual futures)
- Khung thời gian phân tích: **D (Daily), 4H, 1H, 15M, 5M, 1M**
- Sử dụng nến đã đóng để xác nhận cấu trúc. Nến đang chạy không dùng để xác nhận tín hiệu.

## 2. Định nghĩa cấu trúc thị trường (Market Structure)
- **Pivot High / Low:** Xác nhận bằng 2 nến trái + 2 nến phải đã đóng.
- **Xu hướng (Trend):** 
  - Tăng (Bullish): Đỉnh sau cao hơn (HH) và đáy sau cao hơn (HL).
  - Giảm (Bearish): Đỉnh sau thấp hơn (LH) và đáy sau thấp hơn (LL).
  - Khác: Range / Neutral (Đi ngang).
- **BOS (Break of Structure):** Nến đóng cửa (close) vượt qua swing point (đỉnh/đáy) đã xác nhận theo hướng của xu hướng hiện tại.
- **CHoCH (Change of Character):** Phá vỡ cấu trúc ngược lại bối cảnh xu hướng trước đó.

## 3. Các khái niệm SMC/ICT
- **Sweep (Quét thanh khoản):** Râu nến (wick) vượt qua swing point đã xác nhận, nhưng giá đóng cửa (close) lại rút chân về phía trong.
- **FVG (Fair Value Gap):** Khoảng trống giá giữa 3 nến liền kề. Xác định trạng thái mitigation (chưa/một phần/hoàn toàn).
- **OB (Order Block):** Nến ngược chiều cuối cùng trước một sóng đẩy mạnh (displacement) tạo ra BOS/CHoCH. 
- **Premium/Discount:** Định nghĩa vùng giá đắt/rẻ dựa trên dealing range gần nhất. Long tại vùng Discount, Short tại vùng Premium.

## 4. Setup giao dịch v1
Setup v1 tập trung vào quy trình: **Liquidity sweep → Displacement/CHoCH hoặc BOS → Retracement vào FVG → Xác nhận vào (Entry)**.

**Điều kiện cụ thể:**
1. **Đồng thuận đa khung thời gian:** D và 4H cùng xu hướng. 1H không được đối lập (nếu xung đột -> No-trade).
2. **Thanh khoản:** Phải có một vùng liquidity (PDH, PDL, session high/low, swing cũ) được xác định.
3. **Thứ tự sự kiện:** Sweep xảy ra -> Tiếp theo là Structural Confirmation (CHoCH/BOS) đúng kịch bản.
4. **Vùng chờ (POI - Point of Interest):** FVG hợp lệ còn tuổi thọ, nằm ở vị trí premium (cho short) hoặc discount (cho long) phù hợp.
5. **Trigger / Entry:** Có tín hiệu xác nhận execution trên nến 5M/1M đã đóng (có nến xác nhận hoặc limit order nếu rủi ro cho phép).
6. **Stop Loss (SL):** Đặt ngoài vùng invalidation (điểm cực trị của sweep) cộng thêm buffer (dựa trên ATR).
7. **Take Profit (TP):** Dựa trên vùng thanh khoản đối diện hoặc cấu trúc giá tiếp theo.
8. **Risk to Reward (R:R):** Kỳ vọng R:R tối thiểu 2.0 (sau phí/slippage).
9. **Bộ lọc (Filters):** News blackout, độ giãn spread, freshness của dữ liệu, risk limit (tối đa 1% account), thời gian trong 3 phiên (Asia, London, NY).
