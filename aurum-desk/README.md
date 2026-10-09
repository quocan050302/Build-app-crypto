# AURUM DESK — HỆ THỐNG NGHIÊN CỨU XAUUSDT & AUTO PAPER TRADING SMC/ICT

**AURUM DESK** là ứng dụng chuyên biệt chạy cục bộ (local Windows) phục vụ việc phân tích cấu trúc thị trường SMC/ICT đa khung thời gian (D, 4H, 1H, 15M, 5M, 1M), nghiên cứu sâu 3 phiên giao dịch Á – Âu – Mỹ, tự động nhận diện tín hiệu và thực thi **Paper Trading (vốn giả lập $1,000 USDT)** trên hợp đồng tương lai vĩnh cửu vàng **XAUUSDT Bitget**.

Ứng dụng tích hợp trực tiếp công cụ hiển thị **Long/Short Position Overlay** trên biểu đồ nến (TradingView-style R:R Primitive) với vùng xanh lợi nhuận, vùng đỏ rủi ro, nhãn giá trục Y và thống kê R:R thực tế.

---

## 1. Công Nghệ & Kiến Trúc Hệ Thống

- **Biểu đồ tài chính:** TradingView **Lightweight Charts v5.2.1** với plugin tùy biến `RiskRewardPrimitive` (triển khai theo chuẩn `ISeriesPrimitive<Time>` và `IPrimitivePaneRenderer`).
- **Giao diện người dùng:** React 19 + TypeScript + Vite + TailwindCSS (giao diện Dark Charcoal `#121215`, điểm nhấn màu vàng Aurum `#eab308`).
- **Backend API:** Python + FastAPI + Uvicorn + Pydantic v2.
- **Cơ sở dữ liệu:** SQLite cục bộ (chế độ **WAL - Write-Ahead Logging** và timeout 15s chống lock) tại `backend/aurum_desk.db`, có `UniqueConstraint(symbol, timeframe, timestamp)` chống trùng lặp nến.
- **Nguồn dữ liệu:** Bitget REST & WebSocket API cho hợp đồng `XAUUSDT` (`USDT-FUTURES`).
- **Kiểm thử tự động:** Pytest unit tests cho logic SMC/ICT, tính toán R:R, quản trị vốn và bộ lọc tin tức.

---

## 2. Hướng Dẫn Cài Đặt & Khởi Chạy Trên Windows

### Cách 1: Khởi chạy nhanh bằng PowerShell (Khuyên Dùng)

Mở **PowerShell** tại thư mục `aurum-desk` và chạy:

```powershell
.\run.ps1
```

Script sẽ tự động:
1. Kiểm tra và khởi tạo môi trường ảo Python `venv` nếu chưa có.
2. Tự động cài đặt các thư viện trong `backend/requirements.txt`.
3. Chạy kiểm tra và migrate cơ sở dữ liệu SQLite (`backend/migrate.py`).
4. Kiểm tra và cài đặt `node_modules` cho Frontend nếu chưa có.
5. Khởi động Backend FastAPI tại `http://127.0.0.1:8000`.
6. Khởi động Frontend Vite tại `http://localhost:5174`.

### Cách 2: Khởi chạy thủ công từng phần

**Khởi chạy Backend:**
```powershell
Set-Location .\backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python migrate.py
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

**Khởi chạy Frontend:**
```powershell
Set-Location .\frontend
npm install
npm run dev
```

Truy cập giao diện tại: `http://localhost:5174`.

---

## 3. Tính Năng Nổi Bật

### A. Biểu Đồ & Risk/Reward Position Tool (TradingView Style)
- Tích hợp lớp phủ đồ họa vị thế Long / Short trực tiếp trên canvas biểu đồ:
  - **Vị thế Mua (LONG):** Vùng xanh từ Entry lên TP; Vùng đỏ từ SL lên Entry.
  - **Vị thế Bán (SHORT):** Vùng xanh từ TP xuống Entry; Vùng đỏ từ Entry lên SL.
  - Nhãn trung tâm: `LONG/SHORT · R:R gross 1:x (net 1:y) · PAPER`.
  - Hộp thông tin SL: Giá cắt lỗ, khoảng cách, rủi ro ($ / %), khối lượng oz.
  - Hộp thông tin TP: Giá chốt lời, lợi nhuận dự kiến ròng ($ / %).
  - Nhãn màu tương ứng trên trục giá (Price Axis Views): Xanh lá (TP), Đỏ (SL), Xanh dương/Vàng (Entry).
  - Công cụ **Draft R:R Tool** cho phép người dùng kéo thử mức giá, tự động tính khối lượng vị thế và tỷ lệ R:R trước khi vào lệnh.

### B. Bộ Quy Tắc Chiến Lược SMC/ICT v1.0.0
- **Pivot Swings:** Xác nhận 2 nến trái + 2 nến phải đã đóng (`pivot_time` tại nến $i$, xác nhận tại $i+2$, không có lookahead bias).
- **Cấu trúc thị trường:** Xác định xu hướng HH/HL (Tăng), LH/LL (Giảm) hoặc Ranging.
- **BOS & CHoCH:** Nhận diện phá vỡ cấu trúc tiếp diễn hoặc đảo chiều dựa trên nến đóng cửa.
- **Liquidity Sweeps:** Phát hiện râu nến quét qua đỉnh/đáy cũ nhưng rút chân đóng nến bên trong.
- **FVG (Fair Value Gap):** Quản lý vòng đời 3 nến: `created` $\rightarrow$ `confirmed` $\rightarrow$ `partially_mitigated` $\rightarrow$ `fully_mitigated`.
- **Dealing Range:** Định vị mức cân bằng Equilibrium (EQ), chỉ Mua tại vùng Discount (< EQ) và Bán tại vùng Premium (> EQ).

### C. Quản Trị Rủi Ro & Paper Trading Nghiêm Ngặt
- **Vốn giả lập:** 1.000,00 USDT.
- **Rủi ro mỗi lệnh:** Cố định 0,5% vốn ($5.00/lệnh).
- **Hạn mức ngày:** Tối đa **3 lần mở lệnh/ngày** (giờ UTC+7). Không mở thêm khi đã đủ 3 lệnh.
- **Giới hạn số vị thế:** Tối đa 1 vị thế mở đồng thời. Không nhồi lệnh, không martingale.
- **Quy tắc dừng lỗ liên tiếp:** Khóa mở lệnh mới trong ngày nếu chịu **2 lệnh lỗ liên tiếp**.
- **Giới hạn lỗ ngày (Daily Loss Cap):** 1,5% vốn ($15.00/ngày).
- **Thời gian hồi phục (Cooldown):** Bắt buộc nghỉ ngơi 30 phút sau khi đóng bất kỳ vị thế nào.
- **Khớp lệnh bảo thủ:** Mua tại Ask + trượt giá, Bán tại Bid - trượt giá. Nến ambiguous chạm cả TP và SL luôn ưu tiên SL hit trước.

### D. Lịch Tin Tức Vĩ Mô & Khung Giờ Blackout
- Nhập lịch kinh tế miễn phí từ Forex Factory JSON hoặc file CSV.
- Tự động nhận diện tin USD High Impact (CPI, PCE, NFP, GDP, FOMC, ISM, Retail Sales, Jobless Claims).
- **Chế độ Blackout:** Tự động chặn mở lệnh mới trước 30 phút và sau 15 phút tin High Impact (FOMC: trước 60m / sau 30m).

### E. Nghiên Cứu 3 Phiên Á – Âu – Mỹ & Premarket
- Báo cáo premarket lúc 06:30 UTC+7.
- Nhận diện múi giờ IANA chính xác cho Tokyo (`Asia/Tokyo`), London (`Europe/London`), New York (`America/New_York`) có xử lý giờ mùa hè (DST).
- Tự động xây dựng 3 kịch bản: Tăng (Bullish), Giảm (Bearish) và Chờ đợi (No-trade) kèm mức giá kích hoạt và điểm invalidation.

### F. Nhật Ký (Journal) & Thư Viện Bài Học
- Lưu snapshot đầy đủ trước khi vào lệnh.
- Ghi nhận chi tiết kết quả thực tế sau khi đóng: PnL ròng, R thực tế, lý do đóng.
- Nút **"Xem Trên Chart"** giúp chuyển đổi khung thời gian và đưa camera chart focus đúng vào các mức Entry/SL/TP của lệnh trong quá khứ.
- Truy xuất các bài học đã phê duyệt trước khi cân nhắc tín hiệu mới.

---

## 4. Chạy Kiểm Thử Tự Động (Testing)

Để chạy bộ unit test tự động:

```powershell
$env:PYTHONPATH="backend"
.\backend\venv\Scripts\pytest.exe backend/tests
```

Kết quả kiểm thử bao gồm:
- Kiểm tra tính toán Gross RR = 2.0 và Net RR trên các bộ số thực nghiệm.
- Kiểm tra Pivot xác nhận tại $i+2$ không nhìn trước tương lai.
- Kiểm tra vòng đời FVG và quét thanh khoản (Liquidity Sweep).
- Kiểm tra khóa hạn mức 3 lệnh/ngày, khóa 2 lệnh thua liên tiếp và trần lỗ 1.5%.
- Kiểm tra phân tích lịch tin tức JSON/CSV và cửa sổ Blackout.
