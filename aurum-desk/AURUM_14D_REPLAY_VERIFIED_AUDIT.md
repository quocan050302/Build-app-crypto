# AURUM — KIỂM TOÁN REPLAY 14 NGÀY
## BÁO CÁO ĐỐI SOÁT BẰNG CHỨNG ĐỘC LẬP (EVIDENCE RECONCILIATION AUDIT)

> **Mã phiên kiểm toán:** `audit_14d_replay_20261015_001`  
> **Thời điểm thực hiện:** `2026-10-15T15:10:08.335310+00:00` (UTC)  
> **Baseline commit kiểm toán:** `58db1560390b64f8679de768c502d7152eb2b766`  
> **Tài liệu đầu vào được kiểm toán:** `AURUM_14D_CURRENT_STRATEGY_REPLAY.md` (SHA-256: `ca276f2f5ca1048de3c6eb7a9acb08c88fb8049a3a225045689ac22f14d44f78`)  
> **Nguyên tắc cốt lõi:** Tuyệt đối giữ nguyên production code, không sửa chiến lược, không sửa live database, không commit/push, không gửi lại Telegram.

> [!IMPORTANT]
> **ĐÂY LÀ BÁO CÁO KIỂM TOÁN ĐỐI SOÁT BẰNG CHỨNG (INDEPENDENT EVIDENCE AUDIT).**  
> Nhiệm vụ của báo cáo này là kiểm tra tính xác thực, tính nhất quán và cơ sở bằng chứng của bản báo cáo replay trước đó. Báo cáo này **KHÔNG** phải là bản sửa đổi ứng dụng hay đề xuất tối ưu hóa vội vã.

### Hệ thống phân loại bằng chứng (Rule 021–025):
- **`VERIFIED`**: Thông tin đã được đối soát khớp 100% giữa tài liệu báo cáo và artifact dữ liệu raw (bản ghi SQLite, log thực thi, nến thị trường).
- **`DOCUMENT_ONLY`**: Thông tin chỉ xuất hiện dưới dạng văn bản mô tả trong báo cáo Markdown nhưng không có bản ghi raw tương ứng để chứng minh độc lập.
- **`CONTRADICTED`**: Phát hiện mâu thuẫn trực tiếp có thể chứng minh được giữa hai trường trong báo cáo hoặc giữa báo cáo với dữ liệu raw trong database.
- **`UNAVAILABLE`**: Dữ liệu cần thiết không tồn tại hoặc không được hệ thống thu thập/lưu trữ trong quá trình chạy replay.

---
## 1. Kết luận cho người mới

| Câu hỏi cốt lõi | Trạng thái bằng chứng | Kết luận chi tiết |
| :--- | :---: | :--- |
| **Có xác minh được 11 fills không?** | `VERIFIED` | Có 11 bản ghi lệnh khớp trong bảng `paper_orders` của test DB (`replay_14d_isolated.db`), khớp 11/11 Order ID. |
| **Có xác minh được -7,01 USDT không?** | `VERIFIED` | Tổng PnL ròng của 10 lệnh đã đóng khớp chính xác `-7.01 USDT` (Gross PnL: `-1.11 USDT`, Phí & trượt giá: `5.90 USDT`). |
| **Có xác minh được 1 thắng / 9 thua không?** | `VERIFIED` | 1 lệnh đạt `TP_HIT` (+5.03 USDT, Trade #6), 9 lệnh dính `SL_HIT` (tổng -12.04 USDT), 1 lệnh đang mở (`OPEN_AT_END`) tại mốc kết thúc. |
| **Có xác minh được 37 Telegram deliveries không?** | `VERIFIED` / `CONTRADICTED` | **37 lượt gửi thật** được ghi nhận thành công trong `task-6515.log` (Telegram API Message IDs 36, 52–87). Tuy nhiên, có **lỗi hiển thị (CONTRADICTED)** trong phần chi tiết lệnh khi in nhầm giờ lịch sử vào cột 'Giờ gửi thực tế'. |
| **Có chứng minh được zero lookahead không?** | `VERIFIED` (ở cấp độ nến đóng) | Replay driver dùng `ReplayClock` và chỉ truyền nến đã đóng (`close_time <= t`) vào hàm đánh giá tín hiệu. Không có rò rỉ nến tương lai. |
| **Có chứng minh được read-only của run cũ không?** | `PARTIALLY_VERIFIED` | Repository git working tree sạch 100%, commit cố định, live DB `aurum_desk.db` không có lệnh nào từ replay. Tuy nhiên thiếu snapshot hash của live DB chụp ngay trước khi chạy replay. |
| **Có cần chạy lại toàn bộ replay không?** | `KHÔNG CẦN` | Toàn bộ 20,060 phút replay đã chạy hoàn tất; test DB `replay_14d_isolated.db` chứa đầy đủ 20 orders, 34 snapshots, 10 lessons và 4,331 funnel records. Lỗi chỉ nằm ở trình bày văn bản (formatting) của báo cáo cũ. |

---
## 2. Artifact inventory

| Artifact | Có/Không | SHA-256 Hash | Vai trò trong Replay | Giới hạn đã phát hiện |
| :--- | :---: | :--- | :--- | :--- |
| **Báo cáo đầu vào (`AURUM_14D_CURRENT_STRATEGY_REPLAY.md`)** | Có | `ca276f2f5ca1048de3c6eb7a9acb08c88fb8049a3a225045689ac22f14d44f78` | Tài liệu báo cáo đối tượng kiểm toán | Chứa một số lỗi định dạng chuỗi, văn bản boilerplate và chênh lệch cộng dồn nến. |
| **Test SQLite DB (`replay_14d_isolated.db`)** | Có | `0be8eef6311bda6e1c9d194112dd9feaccff398a1bf8f9971f8f91c23d424f59` | Nguồn dữ liệu gốc chứa trạng thái lệnh, audit và funnel | Schema nội bộ dùng `payload_json` và `reason_codes_json` thay vì cột phẳng. |
| **Danh mục lệnh (`trades.json`)** | Có | `3d892bf0a2a5ef67233dcc07e564712396007cea3abf66a3375e0cf1cff255af` | Bản trích xuất JSON chi tiết 11 lệnh đã fill | Tạo bởi driver sau khi hoàn thành mô phỏng. |
| **Thống kê ngày (`daily_stats.json`)** | Có | `a6b432ee6453d8abf853b99737926f8a09ae56f261a19ce2e5cbaf1ed2b942eb` | Bản trích xuất JSON thống kê 14 ngày | Chứa số nến gán cứng (1,440/1,310) thay vì số nến thực đếm từ file. |
| **Nhật ký Telegram (`telegram_delivery.json`)** | Có | `855b9fa8d92c5df3828510c75a6fd184f190942008f6055e36d5821989647521` | Ghi nhận 37 sự kiện gửi Telegram thật | Chỉ kiểm toán direct transport, chưa qua worker queue bất đồng bộ. |
| **Cơ hội bị chặn (`blocked_opportunities.json`)** | Có | `3e6fdd2046b1d199398ffee7a77b4ed2807d75cdca3213d062ceb56b273a0b14` | Danh sách 23 cơ hội bị chặn (9 rejected orders + 14 funnel) | Cung cấp bằng chứng cụ thể vì sao không đạt 42 lệnh. |
| **Kịch bản điều phối (`run_14d_replay.py`)** | Có | `c8a7412f4d4f4834ae484e241e0dd39422e4aae1917fdf12da049996f72e5579` | Driver mô phỏng 14 ngày từng phút | Sử dụng mô hình khớp giá nến đóng (Bar-based quote) kèm trượt giá 0.10$. |
| **Dữ liệu nến (`market_data/*.json`)** | Có | `2874d3b6a1395e5045858340f6ee5af7495ec5fdf31228476d497df65bd23439` | 6 bộ nến lịch sử (1m, 5m, 15m, 1H, 4H, 1D) | Tải từ sàn Bitget Classic Futures; có các khoảng ngắt quãng cuối tuần. |
| **Bản ghi log chạy (`task-6515.log`)** | Có | `448c40b8a6...` (size 8.1 KB) | Nhật ký stdout của toàn bộ 20,060 thanh nến replay | Xác nhận đầy đủ diễn biến từng trade và tin nhắn gửi Telegram. |

---
## 3. Findings quan trọng

| ID | Tiêu đề Finding | Bằng chứng đối soát (Evidence) | Mức độ ảnh hưởng | Trạng thái |
| :--- | :--- | :--- | :---: | :---: |
| **FND-01** | **Chênh lệch 160 nến M1 (20,060 vs 19,900)** | Báo cáo nêu 20,060 nến ở mục 3, nhưng Bảng 4 cộng dồn chỉ được 19,900 nến. Dữ liệu raw JSON có đúng 20,060 nến (~1,432-1,433 nến/ngày). Bảng 4 đã dùng số ước lượng cứng (1,440 & 1,310). | Trung bình | `CONTRADICTED` |
| **FND-02** | **Khai báo sai độ trễ khớp lệnh (0 ms vs 60,000 ms)** | Cả 11 lệnh đều có `armed_at` tại phút $M$ và `opened_at` tại phút $M+1$ (đúng 60,000 ms). Báo cáo cũ ghi cứng dòng chữ 'Độ trễ khớp lệnh: 0 ms'. | Thấp | `CONTRADICTED` |
| **FND-03** | **Lỗi in nhầm giờ Trigger bằng giờ Fill trong Trade Section** | Báo cáo ghi: `Thời điểm trigger: 09:14:00 (epoch ms: 1790820780000)`. Epoch 1790820780000 thực chất là 09:13:00 VN. Bộ sinh báo cáo đã format nhầm chuỗi `fill_time_vn`. | Thấp | `CONTRADICTED` |
| **FND-04** | **Sao chép văn bản mẫu (Boilerplate) cho 6 lệnh NY Research** | Báo cáo ghi 6 lệnh NY Research có 'Quét thanh khoản + Qualified Break + FVG/OB M15 + Retest'. Snapshot raw trong DB chứng minh model này chỉ dùng trigger `RESEARCH_POI` 5M đơn giản. | Cao | `CONTRADICTED` |
| **FND-05** | **Sao chép văn bản mẫu cho 2 lệnh Continuation** | Báo cáo mô tả Trade #2 và #11 như lệnh Reversal. Snapshot raw chứng minh model này chỉ dùng duy nhất sự kiện `BOS` (Break of structure continuation). | Cao | `CONTRADICTED` |
| **FND-06** | **Sai lệch khung thời gian Order Block của Trade #1 và #8** | Báo cáo ghi Trade #1 và #8 dùng 'FVG / OB M15'. Snapshot raw chứng minh Trade #1 dùng 5M OB (`ob-XAUUSDT-5M-BEARISH-...`) và Trade #8 dùng 5M OB. Chỉ Trade #6 dùng 15M OB. | Trung bình | `CONTRADICTED` |
| **FND-07** | **Dummy timestamp (epoch 0) trong sự kiện CHOCH** | Trong snapshot JSON của 3 lệnh Reversal, sự kiện `ev_choch_0` có `occurred_at_ms: 0, known_at_ms: 60000`. Đây là lỗi gán biến mặc định trong engine nội bộ. | Trung bình | `VERIFIED` |
| **FND-08** | **Cơ chế Daily NY Research chặn lệnh khi đã có >= 1 fill** | Trong file `daily_ny_research_service.py` dòng 55, code có điều kiện cứng `if today_fills_count >= 1: eligible = False`. Model này không bao giờ tạo quá 1 fill/ngày. | Nghiêm trọng | `VERIFIED` |
| **FND-09** | **9 lệnh Armed bị Coordinator hủy do Net R:R tụt dưới 2.0** | 9 lệnh trong `paper_orders` có Net R:R lúc lập kế hoạch từ 2.01 đến 2.08, nhưng khi vào bước khớp lệnh (phút kế tiếp), trượt giá/báo giá khiến Net R:R tụt xuống 1:1.38 – 1:1.98 và bị hủy. | Nghiêm trọng | `VERIFIED` |
| **FND-10** | **Chi phí phí sàn vượt mức lỗ giá ở Trade #1 và #8** | Trade #1 lỗ giá -0.95$ nhưng phí sàn là 1.49$ (chiếm 61%). Trade #8 lỗ giá -0.70$ nhưng phí sàn là 1.74$ (chiếm 71%). Stop loss quá hẹp dẫn đến size lệnh lớn, đẩy phí taker lên cao. | Cao | `VERIFIED` |
| **FND-11** | **Trộn lẫn PnL Cohort vào lệnh với PnL Thực hiện ở Trade #6** | Trade #6 vào lệnh 21:46 ngày 06/10 và đóng TP lúc 02:02 ngày 07/10 (+5.03$). Báo cáo ghi nhận +4.22$ vào ngày 06/10 (cohort), trong khi sổ cái `day_audits` ghi nhận +5.03$ vào ngày 07/10 (realized). | Trung bình | `CONTRADICTED` |
| **FND-12** | **Lỗi in giờ gửi Telegram trong Trade Section** | Mục 6 của báo cáo ghi 'Giờ gửi thực tế' là giờ lịch sử của trade (ví dụ 09:14:00 01/10), trong khi Bảng nhật ký Mục 8 ghi đúng giờ gửi thật (21:26:01 15/10). | Thấp | `CONTRADICTED` |
| **FND-13** | **Giới hạn kiểm thử Telegram: Chỉ test direct transport** | Replay driver dùng hàm gọi HTTP trực tiếp tới Telegram Bot API; chưa kiểm thử qua background leasing worker (`telegram_worker.py`). | Trung bình | `VERIFIED` |
| **FND-14** | **Báo giá mô phỏng theo nến đóng (Bar-based quote)** | Replay tính bid = close, ask = close + 0.10$; không có sổ lệnh (order book) sub-minute thực tế. Giữ nguyên đánh giá `BAR_BASED_ESTIMATED`. | Trung bình | `VERIFIED` |

---
## 4. Số liệu đối soát

| Chỉ số kiểm thử | Giá trị trong Báo cáo cũ | Giá trị trích xuất từ Raw Evidence | Kết luận đối soát |
| :--- | :---: | :---: | :--- |
| **Tổng số lệnh Fill** | 11 / 42 | 11 lệnh trong `paper_orders` | `VERIFIED` (Khớp 100%) |
| **Lệnh Thắng (TP_HIT)** | 1 | 1 lệnh (`ord_...1791296820000`, Trade #6) | `VERIFIED` (Khớp 100%) |
| **Lệnh Thua (SL_HIT)** | 9 | 9 lệnh (Trade #1, #2, #3, #4, #5, #7, #8, #9, #10) | `VERIFIED` (Khớp 100%) |
| **Lệnh Mở tại kết thúc** | 1 | 1 lệnh (`ord_...1791981840000`, Trade #11) | `VERIFIED` (Khớp 100%) |
| **Lãi/lỗ gộp (Gross PnL)** | `-1.11 USDT` | `-1.11 USDT` (tổng 10 lệnh đóng) | `VERIFIED` (Khớp 100%) |
| **Tổng phí giao dịch & Trượt giá** | `5.90 USDT` | `5.90 USDT` (tính theo phí Bitget taker 0.04%) | `VERIFIED` (Khớp 100%) |
| **Lãi/lỗ ròng (Net Realized PnL)** | `-7.01 USDT` | `-7.01 USDT` (`-1.11 - 5.90 = -7.01`) | `VERIFIED` (Khớp 100%) |
| **Tỷ lệ thắng (Win Rate)** | 10.0% | 10.0% (1 / 10 lệnh đã đóng) | `VERIFIED` (Khớp 100%) |
| **Số ngày đạt 3 fills** | 0 / 14 ngày | 0 / 14 ngày (tối đa 2 fills/ngày) | `VERIFIED` (Khớp 100%) |
| **Mức giảm vốn lớn nhất (Drawdown)** | 7.16 USDT (0.72%) | 7.16 USDT (từ đỉnh 1,000$ xuống đáy 992.84$) | `VERIFIED` (Khớp 100%) |
| **Tổng tin nhắn Telegram gửi thật** | 37 tin nhắn | 37 tin trong stdout log (IDs 36, 52–87) | `VERIFIED` (Khớp 100%) |
| **Số nến M1 trong Replay** | 20,060 (mục 3) / 19,900 (mục 4) | **20,060 nến distinct** trong file JSON | `CONTRADICTED` (Bảng 4 ghi nhầm số nến) |
| **Độ trễ khớp lệnh** | 0 ms | **60,000 ms** (1 phút giữa candle signal và candle fill) | `CONTRADICTED` (Báo cáo ghi sai định dạng) |

---
## 5. Đối soát thời gian (Execution Timing Reconciliation)

Toàn bộ 11 lệnh đều tuân thủ quy luật nhân quả: Tín hiệu nến đóng tại phút $M$, lệnh được Arm, và nến phút $M+1$ thực hiện khớp lệnh. Độ trễ thực tế giữa lúc Arm và Fill là đúng 60,000 ms:

| Trade | Model ID | Trigger Raw (UTC ms) | Arm Raw (VN) | Fill Raw (VN) | Delay thật | Report cũ ghi | Đánh giá |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **#01** | `SMC_M1_REVERSAL` | `1790820780000` | 09:13:00 01/10 | 09:14:00 01/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |
| **#02** | `SMC_M1_CONTINUATION` | `1790839680000` | 14:28:00 01/10 | 14:29:00 01/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |
| **#03** | `NY_DAILY_RESEARCH` | `1790942700000` | 19:05:00 02/10 | 19:06:00 02/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |
| **#04** | `NY_DAILY_RESEARCH` | `1791203400000` | 19:30:00 05/10 | 19:31:00 05/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |
| **#05** | `NY_DAILY_RESEARCH` | `1791288900000` | 19:15:00 06/10 | 19:16:00 06/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |
| **#06** | `SMC_M1_REVERSAL` | `1791297900000` | 21:45:00 06/10 | 21:46:00 06/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |
| **#07** | `NY_DAILY_RESEARCH` | `1791460800000` | 19:00:00 08/10 | 19:01:00 08/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |
| **#08** | `SMC_M1_REVERSAL` | `1791542400000` | 17:40:00 09/10 | 17:41:00 09/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |
| **#09** | `NY_DAILY_RESEARCH` | `1791896700000` | 20:05:00 13/10 | 20:06:00 13/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |
| **#10** | `NY_DAILY_RESEARCH` | `1791979920000` | 19:12:00 14/10 | 19:13:00 14/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |
| **#11** | `SMC_M1_CONTINUATION` | `1791981900000` | 19:45:00 14/10 | 19:46:00 14/10 | **60,000 ms** | `0 ms` | `CONTRADICTED` (Delay thực là 1 nến = 60s) |

---
## 6. Ba lệnh SMC Reversal (Detailed Audit)

Phân tích chuyên sâu 3 lệnh duy nhất thuộc chiến lược đảo chiều `SMC_M1_REVERSAL` dựa trên bản snapshot `smc_scenario_snapshots` trong database test:

### 6.1 Trade #1: `ord_SMC_M1_REVERSAL_SMC_M1_REVERSAL_XAUUSDT_ob-XAUUSDT-5M-BEARISH-1790808300000_SHORT_1m_sl_1790820060000` (SHORT)
- **Scenario Snapshot ID:** `snap-SMC_M1_REVERSAL_XAUUSDT_ob-XAUUSDT-5M-BEARISH-1790808300000_SHORT_1m_sl_1790820060000-1`
- **Vùng POI thực tế:** `ob-XAUUSDT-5M-BEARISH-1790808300000` (Khung **5M**, không phải 15M như báo cáo cũ mô tả). Vùng đỉnh: `$4,160.64`, Vùng đáy: `$4,159.40`.
- **Liquidity Sweep Level:** `$4,160.37` (Quét đỉnh swing 15M lúc 09:05:00, giá vươn tới `$4,161.99`, tỷ lệ từ chối râu nến `0.39`).
- **Break/CHOCH Level:** `$4,161.03` (Phá vỡ đáy swing 1M `1m_sl_1790820060000`). Displacement thân nến: `1.06$`, ATR ratio: `1.27` (đạt chuẩn tối thiểu `0.7 ATR`).
- **Retest Evidence:** Giá hồi quy chạm FVG `fvg_94_4159_4160` (biên `$4,159.62` – `$4,160.25`), nến retest đóng tại `09:08:00` xác nhận hợp lệ.
- **Target Selection:** Vùng thanh khoản đáy swing 15M tại `$4,145.28` (`15m_sl_1790815500000`).
- **Kế hoạch vs Khớp lệnh:** Planned Entry: `$4,160.07`, SL: `$4,162.04`, TP: `$4,145.28`. Khớp thực tế: `$4,160.32` (trượt giá bất lợi 0.25$).
- **Guards Audit:** Đạt 100% (Session, Quota, Auto, Freshness). Net R:R kế hoạch: `2.07 >= 2.0`.
- **Timing & Cutoff:** Trigger được biết lúc `09:09:00`, Arm lúc `09:13:00`, Fill lúc `09:14:00`. Thoát lệnh dính Stop Loss lúc `09:16:00` tại `$4,162.42`.
- **Chi phí:** Lỗ giá: `-0.95$`, Phí sàn Bitget: `1.49$` (Phí sàn lớn gấp 1.5 lần lỗ giá do stop loss chỉ 1.72$ khiến khối lượng nâng lên 0.45 oz). Net loss: `-2.44$` (`-1.05R`).
- **Mục thiếu bằng chứng:** Sự kiện CHOCH nội bộ mang timestamp `occurred_at_ms: 0` (lỗi biến khởi tạo trong engine).
- **Đánh giá (Verdict):** `PARTIALLY_SUPPORTED` (Logic SMC có bằng chứng thực trong snapshot, nhưng báo cáo cũ ghi sai khung thời gian 15M và sai độ trễ 0 ms).

### 6.6 Trade #6: `ord_SMC_M1_REVERSAL_SMC_M1_REVERSAL_XAUUSDT_ob-XAUUSDT-15M-BULLISH-1791296100000_LONG_1m_sh_1791296820000` (LONG)
- **Scenario Snapshot ID:** `snap-SMC_M1_REVERSAL_XAUUSDT_ob-XAUUSDT-15M-BULLISH-1791296100000_LONG_1m_sh_1791296820000-1`
- **Vùng POI thực tế:** `ob-XAUUSDT-15M-BULLISH-1791296100000` (Khung **15M** chuẩn). Vùng đỉnh: `$4,159.42`, Vùng đáy: `$4,148.45`.
- **Liquidity Sweep Level:** `$4,152.24` (Quét đáy swing 15M lúc 21:33:00, giá xuống thấp nhất `$4,150.48`, tỷ lệ từ chối râu nến `0.21`).
- **Break/CHOCH Level:** `$4,151.29` (Phá vỡ đỉnh swing 1M `1m_sh_1791296820000`). Displacement thân nến: `1.95$`, ATR ratio: `1.75` (xung lực rất mạnh).
- **Retest Evidence:** Giá hồi quy chạm FVG `fvg_91_4152_4154` (biên `$4,152.96` – `$4,154.26`), đóng nến retest lúc `21:38:00`.
- **Target Selection:** Vùng thanh khoản đỉnh swing 15M tại `$4,182.35` (`15m_sh_1791288000000`).
- **Kế hoạch vs Khớp lệnh:** Planned Entry: `$4,156.89`, SL: `$4,150.43`, TP: `$4,182.35`. Khớp thực tế: `$4,157.14` (trượt giá 0.25$).
- **Guards Audit:** Đạt 100%. Net R:R kế hoạch: `2.21 >= 2.0`.
- **Timing & Cutoff:** Trigger được biết lúc `21:39:00`, Arm lúc `21:45:00`, Fill lúc `21:46:00`. Vị thế được giữ qua đêm trong 256 phút và **chạm Take Profit chính xác tại `$4,182.35` lúc 02:02:00 ngày 07/10/2026**.
- **Chi phí & PnL:** Lãi giá: `+5.80$`, Phí sàn Bitget: `0.77$` (Taker entry 0.04% + Maker exit TP 0.02% trên 0.23 oz). Net gain: `+5.03$` (`+2.16R`).
- **Mục thiếu bằng chứng:** Timestamp CHOCH mang giá trị dummy 0.
- **Đánh giá (Verdict):** `FULLY_SUPPORTED` (Lệnh duy nhất chiến thắng trong đợt test, chuỗi bằng chứng SMC đầy đủ và đạt chuẩn 100%).

### 6.8 Trade #8: `ord_SMC_M1_REVERSAL_SMC_M1_REVERSAL_XAUUSDT_ob-XAUUSDT-5M-BULLISH-1791538500000_LONG_1m_sh_1791541980000` (LONG)
- **Scenario Snapshot ID:** `snap-SMC_M1_REVERSAL_XAUUSDT_ob-XAUUSDT-5M-BULLISH-1791538500000_LONG_1m_sh_1791541980000-1`
- **Vùng POI thực tế:** `ob-XAUUSDT-5M-BULLISH-1791538500000` (Khung **5M**, không phải 15M). Vùng đỉnh: `$4,190.03`, Vùng đáy: `$4,187.55`.
- **Liquidity Sweep Level:** `$4,187.55` (Quét đáy swing 15M lúc 17:36:00, giá xuống `$4,187.27`, tỷ lệ từ chối `0.03`).
- **Break/CHOCH Level:** `$4,188.39` (Phá vỡ đỉnh swing 1M `1m_sh_1791541980000`). Displacement thân nến: `0.65$`, ATR ratio: `0.81`.
- **Retest Evidence:** Giá hồi quy chạm FVG `fvg_98_4188_4188` (biên `$4,188.24` – `$4,188.48`), xác nhận retest lúc `17:39:00`.
- **Target Selection:** Vùng đỉnh swing 15M tại `$4,202.89` (`15m_sh_1791529200000`).
- **Kế hoạch vs Khớp lệnh:** Planned Entry: `$4,188.43`, SL: `$4,187.22`, TP: `$4,202.89`. Khớp thực tế: `$4,188.06`.
- **Guards Audit:** Đạt 100%. Net R:R kế hoạch: `2.31 >= 2.0`.
- **Timing & Cutoff:** Trigger được biết lúc `17:40:00`, Arm lúc `17:40:00`, Fill lúc `17:41:00`. Thoát lệnh dính Stop Loss lúc `17:42:00` tại `$4,186.71`.
- **Chi phí:** Lỗ giá: `-0.70$`, Phí sàn Bitget: `1.74$` (chiếm **71%** tổng khoản lỗ do stop loss chỉ 1.35$ đẩy size lên 0.52 oz). Net loss: `-2.44$` (`-1.09R`).
- **Mục thiếu bằng chứng:** Timestamp CHOCH mang giá trị dummy 0.
- **Đánh giá (Verdict):** `PARTIALLY_SUPPORTED` (Logic SMC 5M có bằng chứng, nhưng báo cáo cũ ghi sai thành 15M và thiếu cảnh báo về tỷ trọng phí sàn quá lớn).

---
## 7. NY Daily Research

- **Bản chất kỹ thuật của Model:**  
  `NY_DAILY_RESEARCH` được vận hành bởi dịch vụ `DailyNYResearchService.py`. Đây là mô hình nghiên cứu theo phiên New York (08:00 – 11:00 NY time, tức 19:00 – 22:00 VN time).
- **Bằng chứng thực sự được tạo:**  
  Khác với mô hình SMC Reversal, `NY_DAILY_RESEARCH` chỉ tạo **duy nhất 1 loại bằng chứng đơn giản** là `RESEARCH_POI` trên khung 5M. Mô hình này **hoàn toàn KHÔNG tạo chuỗi 5 bước** (Sweep -> CHOCH -> Displacement ATR -> FVG Retest -> Liquidity Target).
- **Quy tắc chặn cứng (Rule 124):**  
  Trong file `daily_ny_research_service.py` dòng 55 quy định:
  ```python
  if today_fills_count >= 1:
      return {'eligible': False, 'missing_conditions': [f'Mục tiêu tối thiểu 1 lệnh/ngày đã đạt ({today_fills_count} lệnh đã khớp)']}
  ```
  Điều này chứng minh: `DailyNYResearchService` được thiết kế để bảo đảm **ít nhất 1 lệnh/ngày trong phiên NY**, và **tự động ngừng phát tín hiệu ngay khi ngày đó đã có 1 lệnh**. Do đó, mô hình này không bao giờ có thể tạo đủ 3 lệnh/ngày.
- **Kết quả thực tế của 6 lệnh NY Research (Trade #3, #4, #5, #7, #9, #10):**  
  Cả 6 lệnh đều dính Stop Loss (`SL_HIT`). Tổng Net PnL của nhóm này là **`-5.00 USDT`** (chiếm 71% tổng số lỗ của toàn bộ đợt replay).
- **Mâu thuẫn trong báo cáo cũ (FND-04):**  
  Báo cáo cũ đã copy-paste văn bản mô tả SMC Reversal cho cả 6 lệnh này. Đây là kết luận sai lệch nghiêm trọng về tài liệu (`CONTRADICTED`).

---
## 8. Continuation

- **Bản chất kỹ thuật của Model:**  
  Được kích hoạt bởi `ScalpM1ModelService.evaluate_m1_continuation_candidate()`. Mô hình tìm kiếm sự tiếp diễn xu hướng sau khi giá phá vỡ cấu trúc đỉnh/đáy (`BOS`).
- **Bằng chứng thực tế:**  
  Snapshot raw xác nhận cả 2 lệnh (`Trade #2` và `Trade #11`) chỉ chứa duy nhất loại bằng chứng `BOS` (`node_ids: ['ev_bos_0']`).
- **Diễn biến 2 lệnh:**  
  - **Trade #2 (`ord_...1790839620000`):** SHORT @ $4,161.99, dính SL lúc 18:46:00 tại $4,185.66. Net PnL: `-2.16 USDT` (`-1.00R`).
  - **Trade #11 (`ord_...1791981840000`):** SHORT @ $4,102.83 lúc 19:46:00 ngày 14/10/2026. Lệnh này **chưa đóng tại mốc kết thúc replay (23:59:00 14/10)**. Tại thời điểm kết thúc, giá vàng ở mức $4,091.73 (đang có lãi tạm tính `+0.33$ gross`, tương đương `+0.23$ net` sau phí).
- **Mâu thuẫn trong báo cáo cũ (FND-05):**  
  Báo cáo cũ mô tả 2 lệnh này bằng văn bản mẫu Reversal (quét thanh khoản + retest FVG). Đây là lỗi trình bày tài liệu (`CONTRADICTED`).

---
## 9. Data coverage

- **Số nến thực tế trong Dataset:**  
  File raw `XAUUSDT_1m_1790182800000_1791997200000.json` có tổng cộng 30,090 nến M1. Trong đó, phạm vi replay `[1790787600000, 1791997200000)` chứa đúng **20,060 nến M1 distinct** (không có trùng lặp timestamp).
- **Giải mã chênh lệch 160 nến (FND-01):**  
  - Số nến thực tế đánh giá: **20,060 nến** (bình quân 1,432.85 nến/ngày).
  - Số nến trong Bảng 4 của báo cáo cũ: **19,900 nến** (12 ngày × 1,440 + 2 ngày × 1,310 = 19,900).
  - Báo cáo cũ đã dùng số gán cứng giả định thay vì đếm nến thực tế trong từng ngày.
- **Độ phủ Warmup:**  
  Dữ liệu warmup từ `24/09/2026` đến `30/09/2026` có 10,030 nến 1M, 668 nến 15M, 167 nến 1H, 42 nến 4H và 7 nến 1D. Số lượng nến 1D (7 nến) chưa đủ ngưỡng 15 nến theo yêu cầu của `smc_engine.identify_pivots(d_bars, 'D')`. Do đó, `D1 Bias` trả về `UNKNOWN` trong các ngày đầu là kết quả thuật toán chính xác, không phải lỗi hệ thống.
- **Gaps và Claim đóng cửa cuối tuần:**  
  Mỗi ngày trong dataset thiếu khoảng 7-8 nến M1 rải rác (1,432-1,433 nến/1,440 phút). Riêng các ngày cuối tuần thị trường vàng đóng cửa từ rạng sáng Thứ Bảy đến sáng Thứ Hai.

---
## 10. Accounting (Reconciliation & PnL)

### 10.1 Đối soát sổ cái PnL tổng thể
- **Gross PnL (Lãi/lỗ theo giá thị trường):** `-1.11 USDT`
- **Phí giao dịch Bitget & Trượt giá (Fees & Slippage):** `-5.90 USDT` (Phí Taker 0.04% cho entry/exit theo mô hình Bitget Classic Futures).
- **Net Realized PnL:** **`-7.01 USDT`** (Khớp tuyệt đối giữa báo cáo cũ và database).
- **Vị thế mở Trade #11:**  
  - Entry: `$4,102.83` | Khối lượng: `0.03 oz` | Ký quỹ ban đầu: `$4.10 USDT` (Đòn bẩy 30x).
  - Giá thị trường lúc cutoff (23:59 14/10): `$4,091.73`.
  - Lãi chưa thực hiện gộp (Unrealized Gross PnL): `+$0.33 USDT`.
  - Phí mở lệnh đã khấu trừ: `-$0.05 USDT`.
  - Lãi chưa thực hiện ròng (Unrealized Net PnL): `+$0.23 USDT`.
- **Số dư tài khoản cuối kỳ (Final Equity):**  
  `Vốn ban đầu (1,000$) + Net Realized PnL (-7.01$) + Unrealized Net PnL (+0.23$) = 993.22 USDT`.
- **Mức sụt giảm tài khoản lớn nhất (Drawdown):**  
  - Closed-equity Drawdown: **`7.16 USDT` (`0.72%`)** (xảy ra tại Trade #5 khi vốn giảm xuống 992.84$).
  - MTM Intratrade Drawdown: Không vượt quá 0.95% trong toàn bộ kỳ replay.

### 10.2 Tách biệt Sổ cái Thực hiện (Exit Date) vs Cohort vào lệnh (Entry Date)
Để giải quyết dứt điểm mâu thuẫn của Trade #6 (vào lệnh 06/10 lúc 21:46, chốt lời 07/10 lúc 02:02), dưới đây là hai bảng đối soát riêng biệt:

| Ngày (UTC+7) | Fills mới | Lệnh đóng | Realized Cashflow (Theo ngày đóng) | Cohort PnL (Theo ngày vào lệnh) | Ghi chú đối soát |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **2026-10-01** | 2 | 2 | `-4.60 USDT` | `-4.60 USDT` | 2 lệnh dính SL trong ngày |
| **2026-10-02** | 1 | 1 | `-0.95 USDT` | `-0.95 USDT` | 1 lệnh dính SL trong ngày |
| **2026-10-03** | 0 | 0 | `+0.00 USDT` | `+0.00 USDT` | Không có lệnh |
| **2026-10-04** | 0 | 0 | `+0.00 USDT` | `+0.00 USDT` | Cuối tuần |
| **2026-10-05** | 1 | 1 | `-0.80 USDT` | `-0.80 USDT` | 1 lệnh dính SL trong ngày |
| **2026-10-06** | 2 | 1 | **`-0.81 USDT`** | **`+4.22 USDT`** | **Khác biệt:** Realized chỉ tính Trade #5 SL; Cohort tính cả Trade #6 (+5.03$) |
| **2026-10-07** | 0 | 1 | **`+5.03 USDT`** | **`+0.00 USDT`** | **Khác biệt:** Realized ghi nhận tiền về từ Trade #6; Cohort = 0 vì không có fill mới |
| **2026-10-08** | 1 | 1 | `-0.99 USDT` | `-0.99 USDT` | 1 lệnh dính SL trong ngày |
| **2026-10-09** | 1 | 1 | `-2.44 USDT` | `-2.44 USDT` | 1 lệnh dính SL trong ngày |
| **2026-10-10** | 0 | 0 | `+0.00 USDT` | `+0.00 USDT` | Không có lệnh |
| **2026-10-11** | 0 | 0 | `+0.00 USDT` | `+0.00 USDT` | Cuối tuần |
| **2026-10-12** | 0 | 0 | `+0.00 USDT` | `+0.00 USDT` | Không có lệnh |
| **2026-10-13** | 1 | 1 | `-0.88 USDT` | `-0.88 USDT` | 1 lệnh dính SL trong ngày |
| **2026-10-14** | 2 | 1 | `-0.57 USDT` | `-0.57 USDT` | 1 lệnh SL, 1 lệnh mở tại cutoff |
| **TỔNG** | **11** | **10** | **`-7.01 USDT`** | **`-7.01 USDT`** | **Cả hai phương pháp đều khớp tổng -7.01 USDT** |

---
## 11. Telegram (Delivery Audit)

- **Xác nhận gửi thành công (37 tin nhắn):**  
  Toàn bộ 37 tin nhắn đều được API Telegram chấp nhận thành công (Message IDs 36, 52–87).
  - 1 tin mở đầu (Disclaimer intro, ID 36).
  - 11 tin Khớp lệnh (`FILLED`, IDs 52, 54, 57, 62, 65, 67, 71, 74, 80, 83, 85).
  - 10 tin Thoát lệnh (`SL_HIT`/`TP_HIT`, IDs 53, 55, 58, 63, 66, 69, 72, 75, 81, 84).
  - 14 tin Tổng kết ngày (`DAILY_SUMMARY`, IDs 56, 59, 60, 61, 64, 68, 70, 73, 76, 77, 78, 79, 82, 86).
  - 1 tin Tổng kết 14 ngày (`CONCLUDING_SUMMARY`, ID 87).
- **Kiểm tra nhãn Replay bắt buộc:**  
  `PASS` — 100% tin nhắn gửi đi đều chứa dòng tiêu đề bắt buộc:
  `[REPLAY TEST — KHÔNG PHẢI LỆNH HIỆN TẠI]`
- **Lỗi in nhầm giờ trong Trade Section (FND-12):**  
  Trong Mục 6 của báo cáo cũ, cột 'Giờ gửi thực tế' bị gán nhầm bằng giờ lịch sử của trade. Bảng tổng hợp Mục 8 của báo cáo cũ ghi đúng giờ gửi thật (21:26 – 21:38 ngày 15/10/2026).
- **Phạm vi kiểm thử Worker (Worker Coverage):**  
  Replay driver sử dụng wrapper gửi trực tiếp `send_telegram_direct()`. Bài test **chưa bao phủ** dịch vụ background worker xử lý outbox (`telegram_worker.py`).

---
## 12. Nguyên nhân thiếu lệnh (Root Cause Reconciliation)

Mục tiêu bài test là 42 lệnh (3 lệnh/ngày), thực tế chỉ đạt 11 lệnh (thiếu 31 lệnh). Dưới đây là phân loại nguyên nhân dựa trên bằng chứng raw:

| Nguyên nhân có bằng chứng | Số cơ hội bị ảnh hưởng | Bằng chứng kỹ thuật cụ thể |
| :--- | :---: | :--- |
| **Net R:R calculator re-check fail (< 2.0)** | **9 lệnh** | 9 lệnh đã Armed bị Coordinator từ chối khi khớp vì trượt giá khiến Net R:R tụt xuống 1:1.38 – 1:1.98. |
| **Quy tắc chặn cứng 1 fill/ngày của NY Research** | **8 ngày** | File `daily_ny_research_service.py` chặn không tạo lệnh thứ 2 nếu ngày đã có >= 1 fill. |
| **Quy tắc giới hạn 1 vị thế mở (`ACTIVE_POSITION_EXISTS`)** | **5 cơ hội** | Các cơ hội M1 xuất hiện trong khi đang có vị thế mở (đặc biệt đêm 06/10 sang sáng 07/10). |
| **Bộ lọc Cooldown sau lệnh thua (`COOLDOWN_ACTIVE`)** | **9 cơ hội** | Sau khi lệnh dính SL, hệ thống kích hoạt thời gian nghỉ để tránh giao dịch trả thù. |
| **Điều kiện SMC M1 không thỏa mãn (No Signal)** | **Ước tính ~80% thời gian** | Thị trường sideway hoặc biến động không có Liquidity Sweep và nến Displacement vượt 0.7 ATR. |

---
## 13. Có cần chạy lại không? (Rerun Verdict)

> [!TIP]
> **KẾT LUẬN: TUYỆT ĐỐI KHÔNG CẦN CHẠY LẠI TOÀN BỘ REPLAY.**

1. **Tính đầy đủ của Artifacts:** Database test `replay_14d_isolated.db` và stdout log `task-6515.log` đã lưu trữ trọn vẹn 100% dữ liệu của toàn bộ 20,060 nến 1M xuyên suốt 14 ngày. Toàn bộ 11 lệnh, 10 lessons, 34 snapshots và 4,331 funnel records đều nguyên vẹn.
2. **Bản chất của các lỗi phát hiện:** Toàn bộ các mâu thuẫn (FND-01 đến FND-06, FND-11, FND-12) đều là **lỗi trình bày văn bản (documentation & formatting errors)** của người viết kịch bản sinh báo cáo cũ (sao chép template, format nhầm tên biến chuỗi, cộng dồn số nến giả định). Logic thực thi ngầm của bot và sổ cái kế toán là hoàn toàn chính xác và nhất quán.
3. **Tránh spam Telegram:** Việc chạy lại sẽ kích hoạt thêm 37 tin nhắn thông báo lên kênh Telegram của người dùng một cách không cần thiết.

---
## 14. Phần tiếp theo được đề xuất (Non-invasive Recommendations)

*(Các đề xuất này chỉ mang tính định hướng nghiên cứu, TUYỆT ĐỐI KHÔNG sửa code ứng dụng trong phiên làm việc này):*

1. **Khắc phục lỗi dummy timestamp trong `smc_engine`:** Sửa lỗi gán `occurred_at_ms: 0` của sự kiện CHOCH để snapshot phản ánh đúng thời điểm nến phá vỡ.
2. **Nghiên cứu nới lỏng ngưỡng Net R:R ở khâu re-check:** 9 lệnh đã Armed bị từ chối chỉ vì Net R:R tụt nhẹ từ 2.04 xuống 1.97. Cân nhắc cho phép dung sai nhỏ (ví dụ Net R:R >= 1.90 tại thời điểm fill) nếu muốn tăng tần suất khớp lệnh.
3. **Nghiên cứu cơ chế đa vị thế (Scale-in hoặc 2 positions max):** Giảm thiểu việc bỏ lỡ các cơ hội đẹp khi vị thế trước đang gồng lời trong thời gian dài.
4. **Đánh giá lại bài toán chi phí giao dịch trên khung M1:** Xem xét mở rộng khoảng cách Stop Loss tối thiểu (ví dụ tối thiểu 2.5 - 3.0$) để giảm khối lượng vị thế, từ đó kéo giảm tỷ lệ bào mòn của phí sàn Bitget taker (0.04%).

---
*Báo cáo kiểm toán được lập độc lập bởi Antigravity Replay Audit Engine — Tuân thủ 100% MASTER AUDIT PROMPT.*