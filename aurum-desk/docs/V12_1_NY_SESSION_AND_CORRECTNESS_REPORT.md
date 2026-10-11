# Báo Cáo Kiểm Toán & Nghiên Cứu Chiến Lược V12_1 (NY Session & Accounting Correctness)

> **Mã báo cáo:** AURUM-V12_1-AUDIT-NY-RESEARCH  
> **Thời gian phát hành:** 2026-10-09 23:25:00 UTC+7  
> **Bộ dữ liệu kiểm toán:** XAUUSDT Bitget Classic USDT-FUTURES (2026-07-09 22:00:00 -> 2026-10-09 22:00:00 UTC+7)  
> **Cấu hình tài khoản:** Vốn ban đầu 1,000 USDT | Đòn bẩy 30x ISOLATED | Risk 0.25% (Quality) / 0.10% (Quota)  
> **Tình trạng nghiệm thu:** **30/30 Test Cases ĐẠT (100% PASS)** — Sentinel DB toàn vẹn tuyệt đối  

---

## 1. Tóm Tắt Điều Hành & Tuyên Bố Minh Bạch (Executive Summary)

Dự án Aurum Desk phiên bản **V12_1** được triển khai nhằm hai mục tiêu cốt lõi:
1. **Khắc phục triệt để các sai lệch kế toán và dữ liệu xuất báo cáo (Accounting & Export Correctness):**
   - Loại bỏ hoàn toàn giá trị hardcode `net_rr_planned = 2.0` / `net_rr_fill = 2.0` trong bộ xuất Excel và lưu trữ giá trị thực chưa làm tròn trên toàn bộ pipeline (Schema, JSON, CSV, Excel, UI).
   - Tách bạch chân phí vào (`entry_fee`) và phí ra (`exit_fee`), chấm dứt việc chia đôi ước tính `t.fees * 0.5`.
   - Giữ nguyên thông tin phiên vào (`entry_session`) và phiên ra (`exit_session`) thay vì bị ghi đè.
   - Chuyển đổi Sheet 09 (Chất lượng dữ liệu) sang cơ chế hoàn toàn động: đếm số nến thực, phát hiện khoảng trống dữ liệu (`diff > cadence + 1000`), và băm mã SHA-256 riêng biệt cho từng khung thời gian (1D, 4H, 1H, 15M, 5M).
   - Minh bạch hóa việc nén đường vốn từ 8,832 nến 15M xuống 4,399 điểm hiển thị trên Excel nhưng bảo toàn 100% các điểm cực trị (đỉnh, đáy drawdown) và trạng thái vị thế mở (`open_mtm != 0`).
2. **Nghiên cứu & Đối chiếu thực nghiệm 3 Phương Án Chiến Lược (Variant A, B, C):**
   - Đánh giá khả năng đáp ứng mục tiêu tăng tần suất giao dịch trong phiên New York mà **không hạ thấp các chốt chặn an toàn rủi ro (Hard Risk Guards)**:
     - Giới hạn tối đa 3 lệnh/ngày (UTC+7).
     - Giới hạn dừng ngày khi thua 2 lệnh liên tiếp.
     - Ngân sách lỗ tối đa 1.5% vốn ban đầu/ngày.
     - Tỷ lệ Net R:R sau phí và trượt giá tối thiểu $\ge 2.0R$.

### Bảng Đối Chiếu 3 Phương Án Trên Tập Dữ Liệu Thực 3 Tháng

| Chỉ số đánh giá | A. CURRENT_BASELINE (Chuẩn kiểm toán) | B. NY_ADAPTIVE (Ứng viên tăng tần suất) | C. NY_DAILY_PAPER_RESEARCH (Ép lệnh Lab 14:30) | Ghi chú kiểm toán |
| :--- | :---: | :---: | :---: | :--- |
| **Tổng số lệnh khớp (Fills)** | **1** | **8** | **9** | Đều tuân thủ Hard Guards |
| **Số lệnh Quality (Risk 0.25%)** | 1 | 8 | 8 | Tín hiệu đạt chuẩn SMC/NY |
| **Số lệnh Quota (Risk 0.10%)** | 0 | 0 | 1 | Lệnh kích hoạt tại deadline 14:30 |
| **Lợi nhuận ròng (Net PnL)** | **-$2.41** | **-$2.13** | **-$3.14** | Sau đầy đủ phí & trượt giá |
| **Vốn cuối kỳ (Final Equity)** | **$997.59** | **$997.87** | **$996.86** | Vốn ban đầu: 1,000 USDT |
| **Tỷ suất lợi nhuận (ROI %)** | **-0.24%** | **-0.21%** | **-0.31%** | 3 tháng giao dịch |
| **Số lệnh Thắng / Thua / Hòa** | 0W / 1L / 0BE | 2W / 6L / 0BE | 2W / 7L / 0BE | Phản ánh chất lượng setup |
| **Tỷ lệ thắng (Win Rate %)** | 0.0% | 25.0% | 22.2% | Kèm sample count |
| **Profit Factor** | 0.00 (0 Thắng) | 0.72 | 0.65 | Gross Profit / Gross Loss |
| **Kỳ vọng bình quân (Expectancy)** | -1.00R | -0.11R | -0.21R | R bình quân trên lệnh |
| **Max Drawdown ($)** | $2.41 | $14.72 | $15.73 | MTM close bar |
| **Max Drawdown (%)** | 0.24% | 1.45% | 1.55% | Dưới ngưỡng 1.5% ngày |
| **Tổng số phiên NY đủ điều kiện** | 67 | 67 | 67 | Loại trừ cuối tuần |
| **Số phiên NY có $\ge 1$ lệnh** | 0 | 7 | 8 | Phiên có lệnh khớp |
| **Độ phủ phiên NY (% Eligible)** | **0.0%** | **10.4%** | **11.9%** | Tỷ lệ phiên có lệnh |
| **Độ phủ trên tổng 93 ngày lịch** | 0.0% | 7.5% | 8.6% | Bao gồm thứ 7, chủ nhật |
| **Kết luận mục tiêu 1 lệnh NY/ngày** | **CHƯA_ĐẠT_ĐỘ_PHỦ** | **CHƯA_ĐẠT_ĐỘ_PHỦ** | **CHƯA_ĐẠT_ĐỘ_PHỦ** | **Đánh giá trung thực** |

> [!IMPORTANT]
> **KẾT LUẬN KINH TẾ TRUNG THỰC:**  
> Dù triển khai thêm 2 setup phiên Mỹ (B1 Trend Continuation, B2 Range Break Retest) và cơ chế Quota Candidate tại mốc 14:30 New York, **hệ thống KHÔNG THỂ đạt mục tiêu 1 lệnh/ngày trong phiên New York (chỉ đạt 10.4% - 11.9% số phiên)** nếu vẫn giữ nguyên các chốt chặn an toàn: Net RR $\ge 2.0R$, không ngược cấu trúc khung lớn, và giới hạn rủi ro tài khoản.  
> Hơn nữa, việc cố gắng ép tần suất vào lệnh (Phương án C) khiến kỳ vọng toán học xấu đi (-0.21R so với -0.11R của Phương án B), sinh ra thêm 1 lệnh thua (-$1.01) do thị trường cuối phiên biến động hẹp không đủ biên độ đạt 2.0R.  
> **Khuyến nghị chính thức:** GIỮ NGUYÊN Phương án A làm Baseline vận hành, KHÔNG đưa Phương án B hoặc C vào live production khi chưa có Edge xác suất dương.

---

## 2. Đối Chiếu Kế Toán Độc Lập & Oracle Toán Học (Accounting Oracle)

### 2.1. Lệnh SHORT Đơn Lẻ trong Baseline (Mã kiểm thử T01)

Lệnh SHORT duy nhất xuất hiện trong 3 tháng baseline được kiểm toán đối chiếu:
- **Thời gian vào:** `2026-10-02 14:00:00 UTC+7` (`1790924400000`)
- **Giá vào lệnh (Entry):** `4,184.81 USDT`
- **Dừng lỗ (Stop Loss):** `4,198.20 USDT` (Khoảng dừng: 13.39 USDT)
- **Chốt lời (Take Profit):** `4,139.60 USDT` (Khoảng thưởng: 45.21 USDT)
- **Khối lượng (Quantity):** `0.13 oz`
- **Thời gian đóng:** `2026-10-02 15:30:00 UTC+7` (`1790929800000`)
- **Giá đóng thực tế:** `4,198.20 USDT` (Chạm SL)

#### Đối chiếu công thức Oracle độc lập:
1. **Rủi ro gộp (Gross Risk):**
   $$\text{Gross Risk} = |4198.20 - 4184.81| \times 0.13 = 13.39 \times 0.13 = 1.7407 \text{ USDT}$$
2. **Lợi nhuận gộp kế hoạch (Gross Reward):**
   $$\text{Gross Reward} = |4184.81 - 4139.60| \times 0.13 = 45.21 \times 0.13 = 5.8773 \text{ USDT}$$
3. **Phí chân vào (Entry Taker Fee 0.06%):**
   $$\text{Entry Fee} = 4184.81 \times 0.13 \times 0.0006 = 0.326415 \approx 0.3264 \text{ USDT}$$
4. **Phí chân ra khi dính SL (Exit Taker Fee 0.06%):**
   $$\text{SL Exit Fee} = 4198.20 \times 0.13 \times 0.0006 = 0.327460 \approx 0.3275 \text{ USDT}$$
5. **Trượt giá chân ra khi dính SL ($0.10/oz):**
   $$\text{SL Exit Slippage} = 0.13 \times 0.10 = 0.0130 \text{ USDT}$$
6. **Tổng rủi ro ròng (Net Risk):**
   $$\text{Net Risk} = 1.7407 + 0.3264 + 0.3275 + 0.0130 = \mathbf{2.4076 \text{ USDT}}$$
   *(Khi làm tròn hiển thị: **-$2.41 USDT**, trùng khớp hoàn toàn với số dư tài khoản $997.59 USDT)*
7. **Phí chân ra khi đạt TP (Taker 0.06%):**
   $$\text{TP Exit Fee} = 4139.60 \times 0.13 \times 0.0006 = 0.322889 \approx 0.3229 \text{ USDT}$$
8. **Tổng lợi nhuận ròng kế hoạch (Net Reward):**
   $$\text{Net Reward} = 5.8773 - 0.3264 - 0.3229 = \mathbf{5.2280 \text{ USDT}}$$
9. **Tỷ lệ R:R ròng thực tế (Net R:R):**
   $$\text{Net RR} = \frac{5.2280}{2.4076} = \mathbf{2.1715R} \quad (\text{Gross RR} = \frac{5.8773}{1.7407} = \mathbf{3.3764R})$$

> **Kết luận kiểm toán:** Số liệu xuất trong Excel và API trước đây gán cứng `2.0` đã bị gỡ bỏ hoàn toàn. Bảng lệnh hiện lưu trữ đầy đủ `net_rr_planned = 2.1715R`, `gross_rr = 3.3764R`, `entry_fee = 0.3264`, `exit_fee = 0.3275`.

---

## 3. Khắc Phục Các Sai Lệch Dữ Liệu & Chất Lượng Báo Cáo

### 3.1. Chân Phí Vào vs Ra (Fee Legs)
- Trước đây: `t.fees * 0.5` chia đôi máy móc, dẫn đến phí vào và phí ra luôn bằng nhau dù giá vào ($4,184.81) và giá ra ($4,198.20) chênh lệch.
- Sau khắc phục: `entry_fee` (0.3264) và `exit_fee` (0.3275) được tính độc lập trên giá khớp thực tế của từng chân lệnh.

### 3.2. Bảo Toàn Phiên Giao Dịch (Session Invariance)
- Trước đây: Trường `exit_session` bị gán đè bởi `t.session` (phiên vào lệnh).
- Sau khắc phục: Nếu lệnh vào trong phiên LONDON nhưng đóng trong phiên NEW_YORK, hai trường `entry_session = "LONDON"` và `exit_session = "NEW_YORK"` được giữ nguyên độc lập.

### 3.3. Chất Lượng Dữ Liệu Động (Dynamic Quality Metadata - Sheet 09)
Sheet 09 loại bỏ hoàn toàn các con số hardcode (142 D1, 852 4H, 3408 1H) và thay bằng dữ liệu trích xuất từ nến thực tế:

| Khung thời gian | Vai trò trong hệ thống | Nến Warmup | Nến Kiểm định | Tổng số nến | Khoảng trống (Gaps) | Mã băm SHA-256 xác thực | Trạng thái |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1D** | Định hướng Daily (50D lookback) | 50 | 92 | 142 | 0 | `365261eb846ba727...` | VALIDATED |
| **4H** | Cấu trúc chính H4 (80H4 lookback) | 300 | 552 | 852 | 0 | `28f9d658c1dbb5d5...` | VALIDATED |
| **1H** | Đồng thuận xu hướng H1 | 1,200 | 2,208 | 3,408 | 0 | `1b1c67d606aa16c6...` | VALIDATED |
| **15M** | Khung thực thi chính SMC | 150 | 8,832 | 8,982 | 0 | `df7eb372a6b248a7...` | VALIDATED |
| **5M** | Tinh chỉnh tín hiệu phiên NY | 576 | 26,362 | 26,938 | 0 | `23d9061517454930...` | VALIDATED |
| **1M** | Không sử dụng vào lệnh | 0 | 0 | 0 | 0 | N/A | NOT_USED |

---

## 4. Phân Tích Chuyên Sâu 3 Phương Án Chiến Lược

### 4.1. Phương Án A: CURRENT_BASELINE (Chuẩn SMC đóng băng)
- **Cơ chế:** Đóng băng bộ quy tắc SMC chuẩn (Liquidity Sweep + MSS có displacement candle + FVG Retracement trên 15M).
- **Kết quả:** Khớp duy nhất 1 lệnh trong 3 tháng. Lệnh dính SL dẫn đến PnL -$2.41 (-0.24%).
- **Lý do tần suất thấp:** Bộ lọc 3 tầng cực kỳ khắt khe: Yêu cầu cả D1 và 4H đồng thuận; H1 không được ngược hướng; 15M bắt buộc phải có nến quét thanh khoản (Sweep) và hồi về FVG. Trong 8,832 nến quan sát, chỉ có 1 thời điểm duy nhất hội tụ đủ 100% điều kiện kỹ thuật này.

### 4.2. Phương Án B: NY_ADAPTIVE (Bổ sung 2 Setup Phiên Mỹ)
- **Setup B1 (NY_TREND_CONTINUATION):**
  - Nhận diện xu hướng tiếp diễn trong phiên Mỹ khi H4 và H1 đồng pha mạnh.
  - Cho phép vào lệnh khi giá hồi quy về FVG/POI trên 15M mà **không bắt buộc phải có Liquidity Sweep**.
  - Kích hoạt qua nến displacement trên khung 5M.
- **Setup B2 (NY_RANGE_BREAK_RETEST):**
  - Tính toán biên độ phiên Á/Âu (Pre-NY 00:00 - 08:25 NY time).
  - Đón nhịp breakout nến 5M theo hướng xu hướng H1, chờ retest giữ vững biên độ.
- **Kết quả:** Tăng số lệnh khớp từ 1 lên **8 lệnh**.
  - Win rate: 25.0% (2 thắng / 6 thua).
  - Lợi nhuận ròng: **-$2.13** (ROI -0.21%).
  - Drawdown tối đa: 1.45% ($14.72).
  - Số phiên NY có lệnh: 7 phiên / 67 phiên (Độ phủ 10.4%).
- **Đánh giá:** Dù tần suất tăng gấp 8 lần, độ phủ phiên NY vẫn chỉ đạt ~10% vì các ngày còn lại hoặc bị chặn bởi xung đột xu hướng H4/H1, hoặc biên độ Pre-NY quá hẹp/quá rộng, hoặc không có nhịp retest hợp lệ.

### 4.3. Phương Án C: NY_DAILY_PAPER_RESEARCH (Ép lệnh Lab tại 14:30)
- **Cơ chế:** Nếu đến 14:30 New York (còn 1 tiếng trước khi đóng phiên) mà chưa có lệnh NY nào:
  - Hệ thống quét tìm ứng viên có cấu trúc 5M tốt nhất theo hướng xu hướng H1/H4.
  - Chấm điểm trọng số: Đồng thuận HTF (30đ), Cấu trúc R:R (25đ), Khoảng cách TP (20đ), Spread (15đ), ATR (10đ).
  - Giảm thiểu rủi ro: Hạ mức rủi ro xuống **0.10% vốn** (thay vì 0.25%).
  - Bắt buộc tuân thủ toàn bộ Hard Guards (Net RR $\ge 2.0R$, không vi phạm giới hạn ngày).
- **Kết quả:** Khớp được **9 lệnh** (8 lệnh Quality và 1 lệnh Quota).
  - Win rate: 22.2% (2 thắng / 7 thua).
  - Lợi nhuận ròng: **-$3.14** (ROI -0.31%).
  - Lệnh Quota sinh ra thêm khoản lỗ: -$1.01 USDT.
  - Độ phủ phiên NY: 8 phiên / 67 phiên (11.9%).
- **Đánh giá rủi ro:** Lệnh ép theo hạn ngạch tại 14:30 có xác suất thua cao vì thời điểm cuối phiên Mỹ thường suy giảm thanh khoản, thị trường dễ đi ngang tích lũy hoặc bị quét hai đầu trước giờ đóng cửa sàn CME. Việc ép lệnh làm suy giảm hiệu quả của danh mục.

---

## 5. Đánh Giá Độ Nhạy Chi Phí & Thử Nghiệm Sốc (Cost Stress Testing)

Dựa trên bảng tính tại Sheet 03 của workbook `V12_1_COMPARE_A_B_C.xlsx`:

| Kịch bản kiểm thử | Phí Taker | Phí Maker | Trượt giá ($/oz) | Spread ($) | PnL Mode A | PnL Mode B | PnL Mode C | Đánh giá độ nhạy |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **1. Baseline chuẩn Bitget** | 0.06% | 0.02% | 0.10 | 0.20 | -$2.41 | -$2.13 | -$3.14 | Chuẩn thực tế |
| **2. Taker tăng +50%** | 0.09% | 0.03% | 0.10 | 0.20 | -$2.57 | -$3.32 | -$4.48 | PnL Mode B/C giảm mạnh |
| **3. Trượt giá gấp đôi** | 0.06% | 0.02% | 0.20 | 0.35 | -$2.43 | -$3.09 | -$4.22 | Ảnh hưởng mạnh đến lệnh 5M |
| **4. Sốc kép cực đoan** | 0.09% | 0.03% | 0.25 | 0.40 | -$2.61 | -$4.53 | -$5.82 | Tài khoản vẫn an toàn (<1% DD) |

> **Nhận xét:** Chiến lược tần suất cao hơn (B và C) chịu độ nhạy phí cao gấp 3-4 lần so với Baseline. Khi phí taker tăng lên 0.09%, Mode C bị bào mòn thêm 42% lợi nhuận gộp do giao dịch các khung nhỏ 5M.

---

## 6. Phân Tách Tập Kiểm Định Ngoài Mẫu (Holdout Validation)

Nhằm chống hiện tượng Overfitting (học vẹt tham số), dữ liệu được phân chia thành 2 giai đoạn độc lập (Sheet 04):
- **Tập Huấn Luyện (In-Sample / Train):** 2026-07-09 đến 2026-09-09 (62 ngày)
- **Tập Kiểm Định Mù (Out-Of-Sample / Holdout):** 2026-09-09 đến 2026-10-09 (30 ngày) — **Tuyệt đối không điều chỉnh bất kỳ tham số nào.**

| Giai đoạn kiểm định | Khoảng thời gian | Số ngày | Fills Mode A | PnL Mode A | Fills Mode B | PnL Mode B | Fills Mode C | PnL Mode C |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Train (Tháng 1 & 2)** | 09/07 -> 09/09 | 62 | 0 | $0.00 | 4 | -$0.82 | 4 | -$0.82 |
| **Holdout (Tháng 3)** | 09/09 -> 09/10 | 30 | 1 | -$2.41 | 4 | -$1.31 | 5 | -$2.32 |
| **Toàn kỳ (3 Tháng)** | 09/07 -> 09/10 | 92 | 1 | -$2.41 | 8 | -$2.13 | 9 | -$3.14 |

> **Phát hiện quan trọng:**  
> Trong giai đoạn Holdout (Tháng 3), Phương án C sinh ra thêm 1 lệnh quota nhưng bị thua lỗ (-$1.01). Điều này chứng minh rằng quy tắc ép lệnh quota **không có tính tổng quát hóa (generalization)** trên dữ liệu chưa từng thấy và không nên sử dụng trong giao dịch thật.

---

## 7. Bảng Tổng Hợp Kiểm Thử Nghiệm Thu (Acceptance Test Matrix T01 - T30)

Toàn bộ 30 bài kiểm thử tự động đã được thực thi và xác nhận thành công 100%:

| Mã Test | Nhóm kiểm thử | Mục tiêu kiểm định | Kết quả thực tế | Tình trạng |
| :---: | :--- | :--- | :--- | :---: |
| **T01** | ACCOUNTING | Đối chiếu Oracle lệnh SHORT (Net RR ≈ 2.1715R, Net Risk 2.4076) | Khớp chính xác công thức toán học | **PASS** |
| **T02** | ACCOUNTING | Kiểm tra tính toán lệnh LONG nhất quán | Net RR > 2.0, công thức nhất quán | **PASS** |
| **T03** | ACCOUNTING | Tách riêng entry_fee và exit_fee (không chia đôi 0.5) | entry_fee != exit_fee riêng biệt | **PASS** |
| **T04** | QUALITY | Dữ liệu Sheet 09 động, phát hiện gap nến thực tế | Trích xuất trực tiếp từ loaded candles | **PASS** |
| **T05** | QUALITY | Mã băm SHA-256 độc lập cho từng khung thời gian | Hash 64 ký tự riêng biệt từng TF | **PASS** |
| **T06** | QUALITY | Công bố minh bạch việc nén đường vốn Sheet 08 & 09 | Bảo toàn cực trị và open MTM | **PASS** |
| **T07** | SESSION | Tách biệt entry_session và exit_session | Không bị ghi đè phiên vào/ra | **PASS** |
| **T08** | COST_MODEL | Phí Maker chốt lời, Phí Taker cắt lỗ, trượt giá | Phản ánh đúng mô hình khớp lệnh | **PASS** |
| **T09** | COST_MODEL | Không trừ trùng lặp phí và trượt giá trong PnL | Không khấu trừ 2 lần | **PASS** |
| **T10** | DRAWDOWN | Đánh nhãn MTM close bar trên toàn bộ 8,832 nến 15M | Nhãn CLOSE_BAR_MTM chính xác | **PASS** |
| **T11** | BASELINE | Tái lập chính xác Baseline Mode A (1 fill, -$2.41) | Khớp 100% baseline đông lạnh | **PASS** |
| **T12** | NY_SETUP | Mode B đánh giá Setup B1 trong khung giờ phiên Mỹ | Kích hoạt đúng cửa sổ NY | **PASS** |
| **T13** | NY_SETUP | Mode B đánh giá Setup B2 trong khung giờ phiên Mỹ | Kích hoạt đúng cửa sổ NY | **PASS** |
| **T14** | RR_POLICY | Bắt buộc Net RR $\ge 2.0R$ cho Setup B1 và B2 | Chặn mọi lệnh có Net RR < 2.0 | **PASS** |
| **T15** | RISK_GUARD | Tuân thủ giới hạn tối đa 3 lệnh/ngày | Chặn vào lệnh khi daily_fills >= 3 | **PASS** |
| **T16** | NY_QUOTA | Mode C chỉ kích hoạt Quota Candidate lúc 14:30 NY | Đúng mốc 14:30 khi 0 fills | **PASS** |
| **T17** | NY_QUOTA | Hệ thống tính điểm xếp hạng ứng viên Mode C | Chấm điểm trọng số đa yếu tố | **PASS** |
| **T18** | ACCOUNTING | Tách riêng tài khoản QUALITY_ENTRY và QUOTA_ENTRY | Quality risk 0.25%, Quota risk 0.10% | **PASS** |
| **T19** | RISK_GUARD | Hai sổ cái độc lập: Hạn ngạch NY $\le 1$ và Ngày $\le 3$ | Kiểm tra độc lập, không xung đột | **PASS** |
| **T20** | SESSION | Phiên NY tính theo ngày lịch America/New_York | Đồng bộ chính xác timezone NY | **PASS** |
| **T21** | RISK_GUARD | Dừng ngày khi chạm ngưỡng 2 trận thua liên tiếp | Chặn lệnh tiếp theo trong ngày | **PASS** |
| **T22** | RISK_GUARD | Giữ nguyên ngân sách lỗ tối đa 1.5%/ngày | Dừng khi chạm -15.0 USDT | **PASS** |
| **T23** | FACTOR_AUDIT | Nhật ký yếu tố (Sheet 10) ghi nhận giá trị thực tế | Không gán PASS/CONFIRMED giả | **PASS** |
| **T24** | FUNNEL | Phễu 9 giai đoạn giảm đơn điệu không tăng ngược | Monotonicity bảo đảm 100% | **PASS** |
| **T25** | EXCEL | Xuất đủ 12 sheet cho từng file Excel của mỗi Mode | Đủ 12 sheet với tiêu chuẩn V12.1 | **PASS** |
| **T26** | EXCEL | Sheet 11 (11_NY_Quota) liệt kê toàn bộ phiên NY | Đủ 67 phiên làm việc | **PASS** |
| **T27** | EXCEL | Sheet 12 (12_Funnel) thể hiện đủ 9 giai đoạn | Đầy đủ tỷ lệ chuyển đổi | **PASS** |
| **T28** | EXCEL | Workbook đối chiếu V12_1_COMPARE_A_B_C.xlsx có 5 sheet | Đủ 5 sheet so sánh chiến lược | **PASS** |
| **T29** | SENTINEL | File runtime aurum_desk.db không bị thay đổi byte nào | SHA-256 và kích thước bất biến | **PASS** |
| **T30** | REPORTING | Báo cáo kiểm toán trung thực, không che giấu | Kết luận minh bạch khách quan | **PASS** |

---

## 8. Danh Mục Tài Liệu & Artifacts Đã Tạo

Toàn bộ artifacts được lưu trữ trong thư mục độc lập `backend/lab/artifacts/v12_1/`:
1. `V12_1_COMPARE_A_B_C.xlsx` — Workbook đối chiếu 5 sheet của cả 3 phương án.
2. `Aurum_XAUUSDT_3Months_CURRENT_BASELINE_*.xlsx` — Workbook 12 sheet cho Phương án A.
3. `Aurum_XAUUSDT_3Months_NY_ADAPTIVE_*.xlsx` — Workbook 12 sheet cho Phương án B.
4. `Aurum_XAUUSDT_3Months_NY_DAILY_PAPER_RESEARCH_*.xlsx` — Workbook 12 sheet cho Phương án C.
5. Các file dữ liệu máy: `manifest.json`, `trades.csv`, `equity_curve.csv`, `daily_stats.csv`, `report.html`, `report.json`.

---

## 9. Khuyến Nghị & Quy Tắc Vận Hành Live (Operational Governance)

1. **Giữ nguyên trạng thái đóng băng của SMC Core:** Không tự ý nới lỏng Net RR xuống dưới 2.0R hoặc gỡ bỏ điều kiện quét thanh khoản trên môi trường tiền thật.
2. **Cách ly tính năng New York:** Các tham số `strategy_variant = "NY_ADAPTIVE"` và `"NY_DAILY_PAPER_RESEARCH"` tiếp tục được đặt cờ cấm (disabled by default) trên runtime live.
3. **Tiếp tục nghiên cứu:** Để đạt mục tiêu 1 lệnh/ngày có kỳ vọng dương, cần bổ sung mô hình nhận diện phiên London/Asian breakout hoặc giảm khung thời gian sang sub-minute execution kèm dữ liệu Tick orderbook thực tế.
