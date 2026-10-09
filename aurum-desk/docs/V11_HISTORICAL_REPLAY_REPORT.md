# Báo Cáo Nghiệm Thu V11: Historical Replay 1 Tháng & Soak Stability Testing

## 1. Thông Tin Môi Trường & Phiên Bản Baseline
- **Kho lưu trữ (Repo)**: `https://github.com/quocan050302/Build-app-crypto`
- **Nhánh (Branch)**: `feature/aurum-repair-smc-rr`
- **Thư mục Project**: `aurum-desk/`
- **Baseline Git HEAD**: `b89d885d1fa3994b789d99f586cc4f53f5370a7f` (V10.4)
- **Môi trường thực thi**: macOS Darwin, Python 3.13.0, Node v20+, Vite Dev Server (Port 5174), Uvicorn FastAPI Server (Port 8000)
- **Cơ sở dữ liệu runtime (`backend/aurum_desk.db`)**: **Được bảo vệ 100% nguyên vẹn (Sentinel PASS)**, không có bất kỳ lệnh DELETE/UPDATE/MIGRATE destructive nào trên DB runtime. Mọi phiên Replay và Soak chạy trên SQLite In-Memory độc lập theo từng `run_id`.

---

## 2. Nguyên Nhân Lỗi Timeout & Các Sửa Đổi Thực Hiện

### 2.1. Lỗi Timeout `timeout of 10000ms exceeded` trên Giao Diện UI
- **Hiện tượng**: Khi người dùng nhấn nút *"Bắt Đầu Historical Backtest"* trên Testing Lab, sau đúng 10 giây giao diện hiển thị thông báo lỗi `timeout of 10000ms exceeded`.
- **Nguyên nhân gốc rễ**:
  1. `frontend/src/api/client.ts` khởi tạo `apiClient` Axios với cấu hình mặc định `timeout: 10000` (10 giây). Lời gọi `runLabReplay(...)` không có cấu hình timeout riêng.
  2. Quá trình Replay yêu cầu nạp dữ liệu lịch sử nến đa khung thời gian (15M, 1H, 4H, 1D) qua Bitget API cho 45 ngày (30 ngày đánh giá + 15 ngày warmup), mất khoảng 12-15 giây trong lần tải đầu tiên, dẫn đến việc Axios client tự động abort request khi chạm mốc 10.000ms.
  3. Proxy Vite tại `frontend/vite.config.ts` thiếu cấu hình timeout dài hạn cho các tác vụ tính toán Lab.
  4. Vòng lặp Replay trong `backend/lab/replay_engine.py` thực hiện reslicing mảng nến quá nhiều lần mà không tận dụng con trỏ tiến (pointer increments).

- **Giải pháp & Sửa đổi**:
  - `frontend/src/api/client.ts`: Tăng timeout riêng cho `runLabReplay`, `runLabStress`, `runAllLabScenarios` lên **180.000ms (3 phút)**.
  - `frontend/vite.config.ts`: Cấu hình proxy `/api` với timeout **300.000ms (5 phút)**.
  - `backend/lab/historical_market_data.py`: Bổ sung cơ chế local file disk cache tại `backend/lab/data/` giúp các lần chạy lặp lại chỉ mất **0ms I/O**.
  - `backend/lab/replay_engine.py`: Tối ưu hóa vòng lặp duyệt nến với con trỏ HTF tăng dần và pre-allocated `CandleProxy`, rút ngắn thời gian xử lý toàn bộ 4.383 nến 15M xuống chỉ còn **2,63 giây**.

### 2.2. Khắc Phục Wallclock Leak trong `smc_engine.py`
- Hàm `smc_engine.evaluate_smc_setup` trước đó hardcode `now_ms = int(time.time() * 1000)` và kiểm tra `data_fresh_pass = (now_ms - last_data_at) < (30 * 60 * 1000)`. Khi chạy replay với nến lịch sử năm 2026, toàn bộ nến bị từ chối với lý do `STALE_QUOTE`.
- Đã bổ sung tham số tùy chọn `now_ms: Optional[int] = None` vào `evaluate_smc_setup` và truyền `sim_time` từ `ReplayClock` trong quá trình replay, đảm bảo tính nhân quả thời gian (causal time) 100%.

### 2.3. Khắc Phục Lỗi Logic Swing Targets trong `smc_engine.py`
- Logic cũ lấy phần tử swing cuối cùng mà không kiểm tra vị trí tương quan với giá hiện tại, dẫn đến việc tính toán mức TP bị đảo chiều (ví dụ: lệnh SHORT nhưng TP lại cao hơn giá Entry).
- Đã bổ sung bộ lọc hình học nghiêm ngặt: Lệnh LONG chỉ nhận Swing High > `current_price`, lệnh SHORT chỉ nhận Swing Low < `current_price` (fallback Dealing Range).

### 2.4. Sửa Đổi Khớp Lệnh `ExecutionCoordinator`
- Bảo toàn khối lượng lệnh đã Arm (`qty_to_use = min(order.quantity, calc_budget.quantity)` hoặc giữ nguyên khi `resize_policy == "STRICT_FIXED"`), tuyệt đối không tự ý phóng to kích thước vị thế khi giá biến động thuận lợi.

---

## 3. Dữ Liệu Thị Trường & Thiết Lập Quản Trị Rủi Ro

### 3.1. Dữ liệu nến Bitget Classic Futures
- **Cặp giao dịch**: `XAUUSDT` (USDT Perpetual Contract).
- **Khoảng thời gian Replay**: 30 ngày dương lịch đầy đủ:
  - Bắt đầu (`start_ts`): `1788962400000` (2026-09-09 21:00:00 UTC+7).
  - Kết thúc (`end_ts`): `1791554400000` (2026-10-09 21:00:00 UTC+7).
- **Giai đoạn Warmup**: 15 ngày trước `start_ts` (`1787666400000` đến `1788962400000`).
- **Tổng số nến 15M**: 4.383 nến.
- **Dataset Hash**: `bc7f036e9e2ef536cf67c050dcdc3dfd1235dde36ec72a3a32362408c0a2a674`.
- **Độ tin cậy dữ liệu (Data Fidelity)**: `HISTORICAL_CLOSED_CANDLES_WITH_ESTIMATED_EXECUTION` (Nến đóng xác thực từ sàn Bitget kết hợp mô hình chi phí V10.4).

### 3.2. Cấu hình Vốn & Rủi Ro
- **Vốn ban đầu**: `1.000,00 USDT`.
- **Đòn bẩy**: `30x ISOLATED` (Đòn bẩy phục vụ tính ký quỹ ban đầu, **không nhân rủi ro lên 30 lần**).
- **Tỷ lệ rủi ro mỗi lệnh**: `0,25%` vốn hiện tại (~`2,50 USDT`).
- **Hạn mức giao dịch ngày**: Tối đa 3 lệnh/ngày (`max_daily_fills = 3`).
- **Giới hạn chuỗi thua**: Dừng ngày khi chạm 2 lệnh thua liên tiếp (`max_consecutive_losses = 2`), reset lúc 00:00 UTC+7.
- **Tỷ lệ Net R:R tối thiểu**: $\ge 2,0$.

---

## 4. Kết Quả Chạy Replay 1 Tháng (Main Run)

| Chỉ Số Đánh Giá | Giá Trị Thực Tế | Ghi Chú Kỹ Thuật |
| :--- | :--- | :--- |
| **Run ID** | `v11-replay-c69eab14` | Artifacts lưu tại `backend/lab/artifacts/main_1m_v11/` |
| **Vốn ban đầu (Initial Equity)** | `1.000,00 USDT` | |
| **Số dư tiền mặt cuối kỳ (Cash)** | `997,64 USDT` | |
| **Vốn ròng cuối kỳ (Final Equity)** | `997,64 USDT` | Bao gồm Mark-to-Market nếu có vị thế mở |
| **Lợi nhuận ròng (Net PnL)** | `-2,36 USDT` (`-0,24%`) | Phản ánh đầy đủ chi phí phí và trượt giá |
| **Tổng số lệnh thực hiện** | `1 lệnh` | Lệnh SHORT ngày 2026-10-09 (Session London) |
| **Số lệnh Thắng / Thua** | `0 Thắng / 1 Thua` | Thoát lệnh do `SL_HIT` |
| **Tỷ lệ thắng (Win Rate)** | `0,0%` | |
| **Profit Factor** | `0,0` | Không phát sinh lệnh thắng để chia |
| **Tỷ lệ R kỳ vọng (Expectancy R)** | `-1,0 R` | Đúng bằng rủi ro kế hoạch $1R$ |
| **Sụt giảm vốn tối đa (Max Drawdown)** | `0,24%` (`2,36 USDT`) | Đo đạc liên tục trên từng nến theo Mark-to-Market |
| **Tổng phí giao dịch (Total Fees)** | `0,47 USDT` | Phí Taker 0.04% Bitget V10.4 |
| **Tổng trượt giá (Total Slippage)** | `0,02 USDT` | Directional adverse slippage |

### Chi Tiết Lệnh Giao Dịch
- **Mã lệnh**: `trade-v11-replay-c69eab14-3601`
- **Hướng**: `SHORT`
- **Thời gian vào lệnh**: `1790924400000` (Giá khớp: `4184.81 USDT`)
- **Thời gian thoát lệnh**: `1790928900000` (Giá thoát: `4198.20 USDT`, lý do: `SL_HIT`)
- **Khối lượng**: `0.14 oz` | **Rủi ro ban đầu**: `2.36 USDT` | **Net PnL**: `-2.36 USDT`

### Phân Tích Kỹ Thuật Về Tần Suất Lệnh (1 Lệnh/Tháng)
- Chiến lược SMC của hệ thống hoạt động với các bộ lọc nhân quả cực kỳ khắt khe:
  1. **Đồng thuận đa khung thời gian (HTF Consensus)**: Nến D1 và H4 phải đồng thuận cấu trúc (Bullish/Bearish), nến H1 phải xác nhận căn chỉnh (`h1_align == True`).
  2. **Bộ lọc phiên & Tin tức**: Chỉ Arm lệnh trong cửa sổ thanh khoản (London/New York), tự động block khi có tin tức đỏ.
  3. **Yêu cầu Net R:R $\ge 2.0$**: Sau khi trừ phí sàn hai chiều và trượt giá, tỷ lệ R:R ròng phải đạt từ 2,0 trở lên. Đa phần các nhịp hồi ngắn trong tháng không đáp ứng đủ biên độ TP hợp lệ so với Swing Low đối ứng.

---

## 5. Kết Quả Kiểm Thử Độ Ổn Định & Chịu Tải (Soak Stability Testing)

Đã xây dựng module kiểm thử độ ổn định độc lập `backend/lab/soak_tester.py` (Đáp ứng chỉ tiêu `S01` và `S02`):
- **Kiểm thử chịu tải đột biến (Burst Load)**: Đẩy liên tục 100 quotes trong thời gian ngắn (tương đương tốc độ >100 quotes/s). Hệ thống xử lý hoàn tất trong `0.122 giây`, hàng đợi duy trì trong ngưỡng cho phép (`queue_size <= max_queue_size`), không có hiện tượng nghẽn lag hay tràn bộ nhớ.
- **Tiêm lỗi mạng & dữ liệu (Fault Injection - S01)**:
  - Inverted Spread (Bid > Ask): Bị `QuoteValidator` chặn 100%.
  - Negative Price / Zero Price: Bị `QuoteValidator` chặn 100%.
  - Stale Quote (Timestamp lệch quá ngưỡng): Bị `QuoteValidator` chặn 100%.
- **Giám sát rò rỉ bộ nhớ (Memory Leak Check - S02)**:
  - RSS ban đầu: `44.88 MB`
  - RSS sau kiểm thử: `44.88 MB`
  - Tăng trưởng RSS: `< 0.0001 MB` (Bounded memory).
  - Trạng thái kiểm thử: **PASS 100%**.

---

## 6. Tổng Hợp Kết Quả Bộ Kiểm Thử Hệ Thống (Test Matrix)

| Test Suite | File Kiểm Thử | Số Lượng Tests | Kết Quả |
| :--- | :--- | :---: | :---: |
| **V11 Acceptance Matrix** | `backend/tests/test_v11_historical_replay.py` | 12 / 12 | **PASS (100%)** |
| **V5 Authoritative Lab** | `backend/tests/test_v5_authoritative_lab.py` | 11 / 11 | **PASS (100%)** |
| **Toàn bộ Backend Tests** | Toàn bộ 26 test suites trong `backend/tests/` | 344 / 344 | **PASS (100%)** |
| **Toàn bộ Frontend Tests** | Toàn bộ test suites trong `frontend/src/` | 94 / 94 | **PASS (100%)** |
| **Frontend Production Build** | `npm run build` | - | **Thành công (Exit 0)** |

---

## 7. Các Giới Hạn & Giả Định (Limitations & Assumptions)
1. **Mẫu thử 1 tháng không chứng minh edge dài hạn**: Kết quả giao dịch 1 tháng trên lịch sử là một mẫu thử xác thực logic phần mềm, không đảm bảo hay dự đoán lợi nhuận trong tương lai.
2. **Ước tính khớp lệnh (Estimated Execution)**: Dữ liệu nến 15M không chứa sổ lệnh tick bid/ask từng mili-giây; giá khớp và trượt giá được tính toán theo mô hình chi phí Bitget V10.4.
3. **Lịch kinh tế lịch sử**: Trong chế độ replay offline, các sự kiện tin tức được giả định không có tin bất thường trừ khi có tập tin lịch sử đi kèm.
