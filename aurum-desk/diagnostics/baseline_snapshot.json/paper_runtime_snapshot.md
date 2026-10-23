# Báo Cáo Quan Sát Vận Hành PAPER (V16.39.11)

> **Đã quan sát:** 5420.6 phút · 558 cơ hội · 1 lệnh.
**Lời/lỗ sau phí:** +4.27 USD.
**Tỷ lệ thắng:** 100.0%.

---

## 1. Trạng Thái Vận Hành
- **Tình trạng tổng thể**: `SNAPSHOT_READY`
- **Nguồn dữ liệu**: `OFFLINE_DB`
- **Commit mã nguồn**: `4977a21deb47`
- **Worker thực thi**: `UNKNOWN (Không xác nhận được từ ảnh chụp DB)`
- **Quyền tự động (Auto Trading)**: `CHƯA XÁC MINH (UNVERIFIED)`
- **Mã phiên quan sát (Run ID)**: `run_cg01_xauusdt_shadow_1792401106011` (Trạng thái: `COLLECTING`)

## 2. Phễu Cơ Hội & Thực Thi (Opportunity Funnel)
- Vùng quét (Zones Detected): **517**
- Chờ kích hoạt (Trigger Confirmed): **41**
- Kế hoạch hợp lệ (Plans Valid): **0**
- Lệnh đã đặt chờ (ARMED): **1**
- Lệnh đã khớp (FILLED): **0**
- Vị thế đang mở (Open Positions): **0**

### Lý do chặn/bỏ lỡ chính:
| Mã chặn | Số lần | Nhóm | Giải thích |
| :--- | :--- | :--- | :--- |
| `HYPOTHESIS_AUTHORITY_MISSING` | 225 | SAFETY | Mã kiểm tra: HYPOTHESIS_AUTHORITY_MISSING (Chưa có mô tả chi tiết) |
| `WAITING_M1_STRUCTURE` | 44 | SAFETY | Mã kiểm tra: WAITING_M1_STRUCTURE (Chưa có mô tả chi tiết) |
| `EXECUTION_FEED_DEGRADED` | 39 | EXECUTION | Nguồn giá thực thi đang gián đoạn |
| `WAITING_LIQUIDITY_REFERENCE` | 36 | SAFETY | Mã kiểm tra: WAITING_LIQUIDITY_REFERENCE (Chưa có mô tả chi tiết) |
| `ACTIVE_POSITION_EXISTS` | 1 | SAFETY | Đang có vị thế mở |

## 3. Kết Quả Tài Chính Sau Phí (Post-Fee Performance)
- **Tổng lệnh đã đóng (Closed Trades)**: `1` (Chưa xác nhận: `0`)
- **Lợi nhuận ròng (Net Realized PnL)**: `+4.27 USD`
- **Tỷ lệ thắng (Winrate)**: `100.0%`
- **Kỳ vọng bình quân (Expectancy)**: `+4.27 USD/lệnh`
- **Sụt giảm vốn đỉnh (Max Drawdown)**: `0.00 USD (0.00%)`

## 4. Hạn Chế & Lưu Ý Nghiệm Thu (Limitations)
- AUTO_UNVERIFIED: Quyền tự động (Auto Trading) chưa được xác minh hoặc chưa cấu hình.
- OFFLINE_MODE: Không thể xác nhận trạng thái worker từ ảnh chụp cơ sở dữ liệu.

## 5. Bước Tiếp Theo
1. Nếu quyền Auto đang `CHƯA XÁC MINH` hoặc `TẮT`, giữ nguyên trạng thái quan sát cho đến khi người dùng quyết định bật.
2. Không cưỡng ép lệnh (No forced trades) chỉ để đạt chỉ tiêu ngày.
3. Khi phiên Mỹ (NY) mở cửa, kiểm tra lại snapshot để đánh giá phản ứng giá và các vùng FVG/Sweep thực tế.
