# AURUM DESK — BỘ QUY TẮC CHIẾN LƯỢC SMC/ICT VÀ QUẢN TRỊ RỦI RO (Phiên bản V4.0)

Tài liệu này định nghĩa chính xác và có thể kiểm chứng (deterministic invariants) toàn bộ cấu trúc hệ thống, quy tắc chiến lược SMC/ICT, mô hình ký quỹ & thanh lý Bitget, và quy trình quản trị rủi ro triển khai trong mã nguồn Aurum Desk V4.

---

## 1. Môi trường Giao Dịch & Quy Cách Hợp Đồng Bitget Classic
- **Cặp giao dịch duy nhất:** `XAUUSDT` trên sàn Bitget (USDT-Margined Perpetual Futures). Không nhầm lẫn với XAUUSD CFD/spot hay token vàng bọc PAXG/XAUT.
- **Đặc tả hợp đồng (Instrument Metadata):**
  - Quy cách: 1 hợp đồng = 1 ounce troy (oz) vàng thế giới.
  - Multiplier: $1.0$.
  - Bước giá tối thiểu (Tick size): $0.01 USDT$.
  - Bước khối lượng tối thiểu (Qty step): $0.01 oz$.
  - Khối lượng tối thiểu (Min Qty): $0.01 oz$.
  - Giá trị vị thế tối thiểu (Min Notional): $5.00 USDT$.
  - Tiers hạn mức ký quỹ Bitget:
    - **Tier 1:** Notional $\le 50,000$ USDT $\rightarrow$ Max đòn bẩy $50\times$, MMR = $0.5\%$, Khấu trừ = $0$ USDT.
    - **Tier 2:** Notional $\le 100,000$ USDT $\rightarrow$ Max đòn bẩy $25\times$, MMR = $1.0\%$, Khấu trừ = $250$ USDT.
    - **Tier 3:** Notional $> 100,000$ USDT $\rightarrow$ Max đòn bẩy $15\times$, MMR = $2.0\%$, Khấu trừ = $1,250$ USDT.
- **Phí giao dịch và trượt giá:**
  - Taker fee rate: $0.04\%$ ($0.0004$).
  - Maker fee rate: $0.02\%$ ($0.0002$).
  - Giả định trượt giá thị trường (Slippage): $0.10 USDT / oz$.
  - Lệnh chốt lời (TP) kích hoạt theo giá thị trường áp dụng taker fee assumption nhằm phản ánh chi phí thực tế.
  - Loại bỏ hoàn toàn lỗi tính trượt giá hai lần (double-count slippage): khi giá khớp `actual_entry` đã bao gồm slippage, thuật toán không cộng thêm slippage vào rủi ro ròng một lần nữa.
- **Khung thời gian (Timeframes):**
  - Hệ thống thu thập và xử lý độc lập 6 khung thời gian: `D` (Daily), `4H` (4 Giờ), `1H` (1 Giờ), `15M` (15 Phút), `5M` (5 Phút), `1M` (1 Phút).
  - Tín hiệu cấu trúc chỉ sử dụng **nến đã đóng (Closed Candle)**. Nến đang chạy chỉ cập nhật hiển thị, không dùng làm căn cứ xác nhận cấu trúc hay kích hoạt breakout.

---

## 2. Cấu Trúc Thị Trường SMC/ICT Causal (Không Lookahead)

### 2.1. Đỉnh/Đáy Xoay Chiều (2-Bar Pivots)
- **Quy tắc 2 trái / 2 phải:**
  - Điểm $i$ là Swing High khi: $High_i > High_{i-1}$, $High_i > High_{i-2}$, $High_i > High_{i+1}$, $High_i > High_{i+2}$.
  - Điểm $i$ là Swing Low khi: $Low_i < Low_{i-1}$, $Low_i < Low_{i-2}$, $Low_i < Low_{i+1}$, $Low_i < Low_{i+2}$.
- **Thời điểm xác nhận (Confirmation Time):**
  - Swing High/Low tại nến $i$ chỉ được xác nhận tại thời điểm nến $i+2$ **đóng cửa** (`confirmed_at = close_at của nến i+2`).
  - Nến $i+2$ chưa đóng (`is_closed == False`) chỉ được hiển thị dưới dạng tiềm năng (potential), tuyệt đối không được dùng để trigger tín hiệu hay xác lập BOS/CHoCH.

### 2.2. Event Engine Tuần Tự & Tính Bền Vững (Replay Invariance)
- Tại thời điểm $t$, xu hướng (trend) và đỉnh/đáy bảo vệ (protected high/low) được tính toán hoàn toàn từ dữ liệu đã đóng trước $t$.
- Tuyệt đối không dùng xu hướng ở cuối chuỗi dữ liệu lịch sử để gán nhãn hồi tố cho BOS/CHoCH của các nến quá khứ.
- **BOS (Break of Structure):** Nến đóng cửa vượt qua mức cấu trúc theo đúng hướng xu hướng hiện hành kèm khoảng đệm buffer.
- **CHoCH (Change of Character):** Nến đóng cửa phá vỡ swing point được bảo vệ ngược lại xu hướng đã xác lập.
- **Khử trùng lặp mức phá vỡ:** Mức cấu trúc sau khi bị phá vỡ sẽ được chuyển sang trạng thái đã tiêu thụ (`consumed`). Các nến đóng tiếp theo nằm ngoài mức này không được tạo thêm sự kiện BOS/CHoCH lặp lại.

### 2.3. Quét Thanh Khoản (Liquidity Sweep) vs Phá Vỡ (Break)
- Râu nến (wick) xuyên qua đỉnh/đáy đã xác nhận nhưng giá đóng cửa (close) lại rút chân nằm bên trong mức đó được xác định là **Sweep**, không phải BOS/CHoCH.
- Nến quét thanh khoản là điều kiện tiên quyết kích hoạt chuỗi setup v1.

### 2.4. Vùng Mất Cân Bằng (Fair Value Gap - FVG)
- **Bullish FVG:** $Low_{i} > High_{i-2}$, vùng mất cân bằng là $[High_{i-2}, Low_{i}]$.
- **Bearish FVG:** $High_{i} < Low_{i-2}$, vùng mất cân bằng là $[High_{i}, Low_{i-2}]$.
- Lưu trữ đủ 3 ID nến hình thành, thời điểm xác nhận, và trạng thái: `created` $\rightarrow$ `confirmed` $\rightarrow$ `partially_mitigated` $\rightarrow$ `fully_mitigated`.

---

## 3. Khung Phân Tích Đa Khung Thời Gian & Chuỗi Setup V1

### 3.1. Phân Công Vai Trò Đa Khung
- **D / 4H (HTF Context):** Xác lập xu hướng chính (BULLISH / BEARISH / RANGING). Nếu thiếu dữ liệu HTF, hệ thống trả về `UNKNOWN` hoặc `WAITING`, tuyệt đối không fallback lấy khung nhỏ (LTF) đè lên làm HTF.
- **1H (Alignment):** Xác nhận pha điều chỉnh (pullback) hay đồng thuận với HTF.
- **15M (POI & Liquidity):** Xác định vùng Point of Interest (FVG, Order Block) và mức thanh khoản cần quét.
- **5M (Trigger & Displacement):** Chờ nến quét thanh khoản (Sweep), xung lực đảo chiều (Displacement), tạo FVG mới và hồi quy (Retrace).
- **1M (Refinement):** Tùy chọn tinh chỉnh điểm vào và tối ưu Stop Loss.

### 3.2. Chuỗi Trình Tự Bắt Buộc Của Setup v1
Một setup chỉ được chuyển sang trạng thái `READY` khi trải qua đầy đủ chuỗi sự kiện có quan hệ nhân quả:
$$\text{HTF Context} \longrightarrow \text{15M POI} \longrightarrow \text{Closed Sweep đúng phía} \longrightarrow \text{Displacement + MSS sau Sweep} \longrightarrow \text{FVG tạo bởi Displacement} \longrightarrow \text{Retrace} \longrightarrow \text{Guards PASS}$$
- Nếu thiếu bất kỳ bước nào trong chuỗi (ví dụ: có sweep nhưng không có displacement, hoặc có FVG nhưng không sweep), hệ thống giữ trạng thái chờ (`WAITING_...`), không tự ý nâng cấp thành setup sẵn sàng.

---

## 4. Vòng Đời Lệnh & Máy Trạng Thái (State Machine)

### 4.1. Các Trạng Thái Vòng Đời
```
WATCHING ──> WAITING_PRICE ──> WAITING_SWEEP ──> WAITING_MSS ──> WAITING_RETRACE
                                                                      │
                                                                      ▼
CLOSED <── PAPER_OPEN <── TRIGGERED <── ARMED <───────────────── READY
  │
  └── Hoặc các nhánh kết thúc: INVALIDATED / EXPIRED / CANCELLED / REJECTED
```
- **WATCHING / WAITING_...:** Đang theo dõi, giá chưa đạt POI hoặc đang chờ các bước xác nhận SMC. Giá chạm Entry ở trạng thái này tạo **0 lệnh và không tiêu tốn quota**.
- **READY:** Đã thỏa mãn toàn bộ chuỗi xác nhận SMC và các bộ lọc rủi ro. Sẵn sàng để Arm.
- **ARMED:** Lệnh đã được đưa vào trạng thái chờ kích hoạt với mức giá đóng băng (frozen levels). Chỉ khớp khi giá thị trường chạm đúng điều kiện quote side sau thời điểm `armed_at`.
- **TRIGGERED:** Đã có tín hiệu chạm giá, đang gửi tới Execution Coordinator.
- **PAPER_OPEN:** Lệnh đã được khớp và ghi nhận vị thế vào cơ sở dữ liệu và sổ cái (ledger).
- **CLOSED:** Vị thế đã đóng do chạm TP, chạm SL, hoặc đóng thủ công.
- **LIQUIDATED:** Vị thế bị thanh lý cưỡng bức khi giá Mark chạm giá thanh lý ước tính.

---

## 5. Mô Hình Ký Quỹ & Thanh Lý Bitget Isolated

### 5.1. Công Thức Giá Thanh Lý Ước Tính (Isolated Liquidation Price)
Theo tài liệu chính thức Bitget Classic USDT-Margined Futures:
- $\text{Notional} = \text{Quantity} \times \text{Multiplier} \times \text{Entry}$
- $\text{Initial Margin} = \frac{\text{Notional}}{\text{Leverage}}$
- $\text{Maintenance Margin} = \text{Notional} \times \text{MMR} - \text{Deduction}$

**Đối với vị thế LONG:**
$$\text{LP}_{\text{long}} = \frac{\text{Entry} \times Q \times M - \text{Initial Margin} - \text{Deduction}}{Q \times M \times (1 - \text{MMR} - \text{TakerFeeRate})}$$

**Đối với vị thế SHORT:**
$$\text{LP}_{\text{short}} = \frac{\text{Entry} \times Q \times M + \text{Initial Margin} + \text{Deduction}}{Q \times M \times (1 + \text{MMR} + \text{TakerFeeRate})}$$

### 5.2. Bất Biến Bảo Vệ Thanh Lý (Liquidation Invariants)
1. **Thứ tự hình học bắt buộc:**
   - Lệnh LONG: $\text{LP} < \text{Stop Loss} < \text{Entry}$.
   - Lệnh SHORT: $\text{Entry} < \text{Stop Loss} < \text{LP}$.
2. **Khoảng đệm an toàn tối thiểu (Safety Buffer):**
   - Khoảng cách $|\text{SL} - \text{LP}| \ge 5.00 USDT$ hoặc $\ge 2 \times ATR(14)$.
   - Nếu đòn bẩy quá cao dẫn tới $\text{LP}$ nằm trước hoặc quá gần $\text{SL}$, hệ thống lập tức từ chối thực thi với mã lỗi `LIQUIDATION_BEFORE_SL` hoặc `LIQUIDATION_BUFFER_TOO_TIGHT`.
3. **Tính độc lập của rủi ro khi đổi đòn bẩy:**
   - Thay đổi đòn bẩy từ $5\times$ sang $20\times$ giữ nguyên khối lượng $Q$, Entry, SL, TP thì rủi ro tính bằng USD ($\text{Net Risk USD}$) và lợi nhuận kỳ vọng ($\text{Gross Reward}$) **hoàn toàn không đổi**. Đòn bẩy chỉ làm thay đổi số tiền ký quỹ ban đầu yêu cầu và khoảng cách tới giá thanh lý.

---

## 6. Khối Lượng Tương Đối (RVOL) & Bối Cảnh Volume

- **Nguyên tắc nhân quả:** RVOL chỉ so sánh khối lượng của nến đóng hiện tại với baseline các nến đóng **trong quá khứ**, hoàn toàn không bao gồm nến hiện tại trong mẫu số (`denominator excludes current bar`).
- **Phân loại RVOL:**
  - $\text{RVOL} \ge 2.0$: Ultra High Volume (khối lượng đột biến bất thường).
  - $1.5 \le \text{RVOL} < 2.0$: High Volume.
  - $0.8 \le \text{RVOL} < 1.5$: Normal Volume.
  - $\text{RVOL} < 0.8$: Low Volume.
- **Ngưỡng mẫu tối thiểu:** Cần tối thiểu 10 nến lịch sử để tính baseline. Nếu thiếu mẫu, trả về trạng thái `UNKNOWN` với `rvol = None`, không bao giờ trả số 1.0 giả lập.

---

## 7. Quản Trị Rủi Ro & Bộ Đếm Nghiêm Ngặt

1. **Vốn ban đầu:** $1,000.00$ USDT giả lập (Paper).
2. **Rủi ro mỗi lệnh:** Mặc định $0.25\%$ vốn khả dụng thực tế, giới hạn tối đa (hard cap) $0.50\%$. Tính toán khối lượng động theo vốn thực tế, không dùng số 1,000 cố định sau khi vốn thay đổi.
3. **Giới hạn số lệnh:** Tối đa **3 lệnh khớp/ngày** theo giờ UTC+7. Sau khi đạt 3 lệnh, hệ thống khóa mở lệnh mới cho tới 00:00 UTC+7 hôm sau.
4. **Giới hạn vị thế mở:** Tối đa **1 vị thế mở** tại một thời điểm.
5. **Dừng chuỗi thua:** Dừng mở lệnh trong ngày nếu chịu **2 lệnh lỗ liên tiếp**.
6. **Hạn mức lỗ ngày (Daily Loss Budget):** $1.5\%$ vốn đầu ngày. Lợi nhuận kiếm được trong ngày không được dùng để nới rộng hạn mức lỗ.
7. **Thời gian làm nguội (Cooldown):** Bắt buộc nghỉ **30 phút** sau khi đóng vị thế trước khi được mở lệnh tiếp theo.
8. **Khung giờ Blackout tin tức USD:**
   - Tin High Impact (CPI, NFP, GDP, PCE): Chặn mở lệnh trước 30 phút và sau 15 phút.
   - Tin FOMC / Lãi suất Fed: Chặn mở lệnh trước 60 phút và sau 30 phút.
9. **Xử lý nến bao trùm (Ambiguous Bar):** Nếu trong cùng một nến cả TP và SL đều bị chạm tới, hệ thống áp dụng nguyên tắc thận trọng tối đa: **SL hit trước**.
10. **Thông báo Telegram Outbox:** Sử dụng hàng đợi bền vững lưu trong SQLite với cơ chế retry lũy thừa kèm jitter, kiểm tra giờ yên lặng (quiet hours), và bảo mật tuyệt đối token không rò rỉ vào log hay giao diện frontend.
