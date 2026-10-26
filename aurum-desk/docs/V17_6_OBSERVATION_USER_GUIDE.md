# HƯỚNG DẪN VẬN HÀNH QUAN SÁT & TRÍ TUỆ QUYẾT ĐỊNH BOT (AURUM V17.6)

**Hệ thống**: AURUM DESK — XAUUSDT SMC/ICT Paper Trading Engine  
**Phiên bản**: V17.6 LIVE TELEMETRY  
**Tài liệu**: Sổ Tay Hướng Dẫn Vận Hành & Khai Thác Dashboard Quan Sát Dành Cho Trader / Operator  

---

## 1. Giới Thiệu Tổng Quan

Dashboard **Quan Sát Bot (Telemetry)** trong AURUM V17.6 được thiết kế nhằm mục đích duy nhất: **Minh bạch hóa 100% hoạt động ngầm của bot giao dịch tự động**.

Trước phiên bản V17.6, khi bot không vào lệnh, người dùng thường hoang mang:
- *Bot có đang chạy không hay bị đơ?*
- *Dữ liệu giá có bị mất kết nối không?*
- *Bot đã phát hiện setup nào chưa, đang chờ điều kiện gì hay bị chặn bởi bộ lọc nào?*
- *Hiệu suất tài chính sau phí thực sự là bao nhiêu?*

V17.6 trả lời toàn bộ các câu hỏi trên bằng dữ liệu đo lường trực tiếp, không giả định, không che giấu lỗi, và tuân thủ nguyên tắc **Fail-Closed** (thiếu dữ liệu sẽ hiển thị `N/A`, tuyệt đối không dùng số ảo).

---

## 2. Truy Cập Dashboard Quan Sát

1. Mở ứng dụng AURUM DESK.
2. Trên thanh điều hướng chính, nhấn vào tab **Quan Sát Bot (Telemetry)** (biểu tượng con mắt `Eye`).
3. Dashboard sẽ tải toàn bộ snapshot tình trạng hệ thống từ endpoint `/api/v1/observation/summary`.

---

## 3. Ý Nghĩa 5 Thẻ Sức Khỏe Điều Hành (Executive Health Cards)

Nằm ngay đầu dashboard là 5 thẻ đo lường then chốt, giúp bạn đánh giá toàn bộ hệ thống chỉ trong 3 giây:

```
+---------------------+---------------------+---------------------+---------------------+---------------------+
| 1. Bot Engine Health| 2. Báo Giá & Dữ Liệu| 3. Đánh Giá SMC/ICT | 4. Thực Thi Paper   | 5. Độ Bao Phủ Uptime|
|  [ĐANG CHẠY/WAITING]|  [TRỰC TIẾP & MỚI]  |       [ARMED]       |    [AUTO PAUSED]    |     [100% FULL]     |
| Heartbeat: 1s trước | Trễ: 0.2s           | Chu kỳ: 42ms        | Lệnh ARM: 1         | Giờ quan sát: 24h   |
| Chu kỳ quét: 1s     | Bid/Ask: 2725.1/2725| Regime: NORMAL      | Khớp hôm nay: 1/3   | Restarts: 0         |
+---------------------+---------------------+---------------------+---------------------+---------------------+
```

### Thẻ 1: Bot Engine Health (Sức Khỏe Tiến Trình Bot)
- **ĐANG CHẠY (RUNNING)**: Tiến trình bot đang hoạt động bình thường, chu kỳ quét diễn ra đều đặn theo chu kỳ (mặc định 5 giây/lần).
- **CHỜ THỊ TRƯỜNG (WAITING)**: Thị trường vàng XAUUSDT đang đóng cửa cuối tuần (từ tối thứ Sáu đến sáng thứ Hai theo giờ New York). Bot tạm dừng quét và chuyển sang chế độ ngủ đông an toàn.
- **SUY GIẢM (DEGRADED)**: Dữ liệu nến hoặc giá bị chậm trễ, bot hạ cấp tần suất quét để tránh phán đoán sai lệch.
- **BỊ TREO (STALLED)**: Đã quá 3 chu kỳ quét (15 giây) mà tiến trình không gửi heartbeat. Hệ thống sẽ cảnh báo đỏ ngay lập tức.
- **Heartbeat vs Chu kỳ quét**: Heartbeat đo sự sống của tiến trình; Chu kỳ quét đo thời điểm hoàn tất một vòng phân tích thuật toán SMC/ICT gần nhất.

### Thẻ 2: Báo Giá & Dữ Liệu (Market Feed & Data Health)
- **TRỰC TIẾP & MỚI (LIVE)**: WebSocket luồng giá từ sàn kết nối ổn định, độ trễ báo giá < 3 giây.
- **CŨ / CHẬM (STALE)**: Độ trễ báo giá vượt quá 5 giây (mạng chập chờn hoặc sàn nghẽn).
- **MẤT KẾT NỐI (DISCONNECTED)**: Không nhận được báo giá trong hơn 15 giây.
- **Độ mới nến (1M, 5M, 15M, 1H)**: Các badge xanh hiển thị nến đóng gần nhất của từng khung thời gian có đúng hạn hay không.

### Thẻ 3: Đánh Giá SMC/ICT (Cycle Intelligence)
- Hiển thị kết quả của chu kỳ phân tích gần nhất:
  - `ARMED`: Đã phát hiện setup hội tụ đủ 100% điều kiện và đã đặt lệnh chờ PAPER LIMIT.
  - `WAITING`: Có setup SMC tiềm năng nhưng đang chờ nến đóng hoặc chờ xác nhận phá vỡ cấu trúc (MSS).
  - `BLOCKED`: Setup bị chặn bởi bộ lọc (R:R không đủ, spread cao, tin tức đỏ CPI, hoặc hết hạn mức phiên).
  - `NO_SETUP`: Thị trường không có tín hiệu SMC hợp lệ trong nến hiện tại.
- **Thời gian chu kỳ (Cycle Duration)**: Thời gian xử lý tính bằng mili-giây (thường từ 20ms - 80ms).

### Thẻ 4: Thực Thi Paper Safe (Paper Execution Safety)
- **AUTO ACTIVE / PAUSED**: Trạng thái của công tắc Paper Trading tự động.
- **Lệnh đang ARM**: Số lượng lệnh chờ đang đặt trong sổ lệnh ảo.
- **Vị thế đang mở**: Số vị thế paper đang chạy thực tế.
- **Lệnh đã khớp hôm nay**: Tiến độ sử dụng hạn mức lệnh trong ngày (ví dụ: `1 / 3 lệnh`, trần 3 lệnh/ngày để chống over-trading).
- **Live Trading: KHÓA TUYỆT ĐỐI**: Khẳng định kiến trúc bảo vệ cô lập 100%, không bao giờ gửi lệnh ra sàn thật.

### Thẻ 5: Độ Bao Phủ Quan Sát (Uptime & Coverage)
- **Tỷ lệ bao phủ (Coverage %)**: Tỷ lệ phần trăm thời gian bot quan sát liên tục trong giờ mở cửa thị trường.
- **Công thức tính**: `(Thời gian quan sát thực tế / Thời gian thị trường mở cửa trong cửa sổ) * 100%`. Thời gian nghỉ cuối tuần được loại trừ chính xác khỏi mẫu số để không làm méo mó tỷ lệ uptime.
- **Trạng thái**: `FULL` (>= 95%), `ACCEPTABLE` (>= 85%), `LOW` (< 85%).

---

## 4. Các Phân Hệ Chi Tiết (Deep-Dive Panels)

### 4.1. Phễu Chuyển Đổi Cơ Hội (Opportunity Funnel)
Mô tả trực quan từng bước lọc của thuật toán SMC/ICT:
1. **Đã Phát Hiện (Detected)**: Quét thấy vùng cung/cầu (OB/FVG).
2. **Đang Theo Dõi (Watched)**: Giá đang di chuyển về vùng quan tâm.
3. **Đã Chạm Vùng (Touched)**: Giá chạm vào điểm vào lệnh tiềm năng.
4. **Đã Trigger (Triggered)**: Có phản ứng quét thanh khoản (Liquidity Sweep).
5. **Hợp Lệ (Qualified)**: Cấu trúc xác nhận MSS và có FVG hợp lệ.
6. **Bị Chặn (Guard Blocked)**: Không vượt qua được Guard rủi ro (R:R < 2.0, tin tức, trần lệnh).
7. **Đã Đặt Lệnh (ARM)**: Lệnh LIMIT được đưa vào trạng thái chờ khớp.
8. **Đã Khớp (Filled)**: Giá chạm mức entry, lệnh chính thức vào trạng thái OPEN.

### 4.2. Điều Kiện SMC Đang Chờ (Waiting Conditions)
Khi bot ở trạng thái `WAITING`, panel này liệt kê chính xác setup nào đang chờ điều kiện gì:
- Ví dụ:
  - `Quét thanh khoản (Liquidity Sweep)`: ĐÃ ĐẠT (Xanh lá).
  - `Phá vỡ cấu trúc (MSS)`: ĐANG CHỜ (Vàng cam - Chờ nến M1 đóng cửa qua đỉnh/đáy then chốt).
  - `Khoảng trống giá trị hợp lý (FVG)`: CHƯA ĐẠT (Xám).

### 4.3. Lý Do Không Vào Lệnh (Blocked Reasons)
Phân loại các trường hợp bot từ chối vào lệnh:
- **Tỷ lệ Net R:R (NET_RR)**: Tỷ lệ lợi nhuận/rủi ro sau khi trừ trượt giá và phí giao dịch nhỏ hơn ngưỡng tối thiểu 2.0.
- **Tin Tức Kinh Tế (NEWS)**: Nằm trong vùng cấm giao dịch trước/sau tin CPI, NFP, FOMC.
- **Hạn Mức Phiên (SESSION_QUOTA)**: Đã đạt trần số lệnh cho phép trong phiên hiện tại.
- **Trượt Giá / Spread (SPREAD)**: Độ chênh lệch Bid/Ask vượt quá ngưỡng an toàn.

### 4.4. Hiệu Suất Thực Tế Sau Phí (Committed Paper Ledger)
Trích xuất từ sổ cái kế toán giao dịch ảo:
- **Vốn Khởi Điểm**: Mặc định $1,000.00 USDT.
- **Lợi Nhuận Ròng (Net PnL)**: Lãi/lỗ thực tế sau khi trừ phí hoa hồng Maker/Taker và trượt giá 2 đầu.
- **Tỷ Lệ Thắng (Winrate)**:
  - Nếu chưa đóng lệnh nào: Hiển thị rõ **`N/A (0 lệnh)`** (không hiển thị 0% hay 100%).
  - Khi đã có lệnh đóng: Tính chính xác `(Lệnh thắng / Tổng lệnh đóng) * 100%`.
- **Drawdown Lớn Nhất**: Mức sụt giảm tài khoản tối đa từ đỉnh.
- **Vốn Hiện Tại (Equity)**: Vốn ban đầu + Lợi nhuận ròng.

---

## 5. Dòng Thời Gian Quyết Định & Thao Tác (Decision Timeline)

Bảng quyết định hiển thị 20 sự kiện gần nhất theo thứ tự thời gian đảo ngược:
- **Thời Gian**: Định dạng theo múi giờ bạn đã chọn (Giờ VN hoặc Giờ NY).
- **Model**: Tên mô hình SMC (ví dụ `CG01_SWEEP_MSS_FVG`).
- **Hướng**: `LONG` (Mua) hoặc `SHORT` (Bán).
- **Trạng Thái**: `ARMED`, `BLOCKED`, `WAITING`, `NO_SETUP`.
- **Phân Loại**: Danh mục nguyên nhân (Cấu trúc, R:R, Tin tức, Hạn mức...).
- **Lý Do Quyết Định**: Giải thích song ngữ (Tiếng Việt súc tích + Thông số kỹ thuật chi tiết).
- **Thao Tác (Actions)**:
  - **Xem Biểu Đồ (Focus Chart)**: Chuyển ngay sang tab Chart và căn chỉnh tầm nhìn vào cây nến diễn ra setup.
  - **Xem Lệnh (View Order)**: Mở chi tiết lệnh ảo tương ứng nếu setup đã chuyển sang trạng thái ARMED hoặc FILLED.

---

## 6. Các Phím Điều Khiển (Toolbar Controls)

1. **Chọn Khung Thời Gian (Time Range)**:
   - `Hôm Nay`: Chỉ xem dữ liệu của ngày giao dịch hiện tại (bắt đầu từ 00:00 UTC).
   - `7D`, `14D`, `30D`: Mở rộng phạm vi quan sát để đánh giá độ ổn định và tỷ lệ bao phủ dài hạn.
2. **Chuyển Đổi Múi Giờ**:
   - `Giờ VN (ICT)`: Giờ Việt Nam (UTC+7), thuận tiện theo dõi theo lịch sinh hoạt hàng ngày.
   - `Giờ NY (ET)`: Giờ New York (UTC-4/UTC-5), chuẩn đối chiếu với phiên mở cửa London/New York của vàng.
3. **Nút Làm Mới (Refresh)**:
   - Nhấn để ép cập nhật snapshot mới nhất ngay lập tức mà không cần đợi chu kỳ polling tự động.
