# AURUM DESK - Lịch trình phát triển & Kế hoạch ngắn

## 1. Mục tiêu hiện tại (Vertical Slice)
Tạo ra một hệ thống cơ bản chạy được để chứng minh thiết kế (Vertical slice), bao gồm:
- **Backend (FastAPI):**
  - Kết nối Bitget REST API lấy giá XAUUSDT (Kline/Candle data).
  - Lưu nến vào SQLite (Candle store).
  - API trả về dữ liệu nến cho chart frontend.
  - Sức khỏe hệ thống (System health API).
- **Frontend (React + Vite + TailwindCSS):**
  - Trading workspace layout (Dark theme charcoal).
  - Tích hợp TradingView Lightweight Charts hiển thị XAUUSDT.
  - Các nút đổi khung thời gian (1M, 5M, 15M, 1H, 4H, D).
  - Chức năng tự động cập nhật chart (có thể dùng polling trước rồi tính WebSocket sau).

## 2. Bước 1: Setup Backend
- Khởi tạo FastAPI project.
- Cài đặt `sqlalchemy`, `sqlite`, `ccxt` (hoặc requests trần tùy chọn, nhưng nên dùng API HTTP chuẩn theo doc Bitget: `https://api.bitget.com/api/v2/mix/market/candles`).
- Tạo bảng SQLite lưu candle.
- Viết job kéo lịch sử nến cơ bản (bootstrap).

## 3. Bước 2: Setup Frontend
- Dùng `lightweight-charts` thư viện.
- Fetch dữ liệu từ backend và hiển thị lên chart.
- Tạo UI workspace cơ bản.

## 4. Bước 3: Hoàn thiện Vertical Slice
- Chạy đồng thời 2 service (Backend + Frontend).
- Đảm bảo dữ liệu lên biểu đồ mượt mà, đổi khung thời gian hoạt động.
- Viết script `run.ps1` để dễ dàng start toàn bộ.
