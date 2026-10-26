# Hướng Dẫn Vận Hành Forward Paper V14.4.2 (Forward Paper Guide)

Tài liệu này cung cấp hướng dẫn chi tiết và quy trình chuẩn cho việc vận hành, theo dõi và đánh giá hệ thống quan sát giao dịch **Forward Paper** theo phương pháp SMC (Smart Money Concepts) trên phiên bản **V14.4.2**.

---

## 1. Nguyên Tắc & Quy Chuẩn Bất Biến (Core Invariants)

1. **Chế độ PAPER ONLY**: Tuyệt đối không kết nối tài khoản thật, không gửi lệnh lên sàn trực tiếp (Bitget) hay bắn tín hiệu đặt lệnh tự động ra ngoài kênh Telegram thật.
2. **Khung giao dịch NY_ONLY**:
   - Khung giờ vào lệnh hợp lệ duy nhất: **08:00 – 11:00 EST** (tương đương 13:00 – 16:00 UTC, hoặc 20:00 – 23:00 giờ Việt Nam theo giờ mùa đông).
   - Ngoài khung giờ này, hệ thống chỉ chạy chế độ đọc thị trường và quan sát kịch bản Á / Âu.
3. **Giới hạn số lệnh & vị thế**:
   - **Tối đa 3 fills/ngày**: Sau khi có 3 lệnh được khớp trong ngày, cơ chế quota sẽ tự động khóa mọi tín hiệu Arm mới.
   - **Tối đa 1 vị thế mở**: Tại một thời điểm chỉ cho phép duy nhất 1 lệnh ở trạng thái `OPEN`. Không nhồi lệnh (no martingale, no pyramiding).
4. **Quản lý rủi ro & Fallback Cap**:
   - Rủi ro tối đa cố định: **0.10%** tổng vốn danh nghĩa cho mỗi lệnh.
   - Tỷ lệ lợi nhuận / rủi ro tối thiểu: **Net R:R >= 2.0** (đã khấu trừ phí maker/taker và trượt giá dự kiến).
5. **Cơ chế News Blackout & Guards**:
   - Chặn Arm trong vòng **15 phút trước và sau** các tin tức kinh tế quan trọng (High Impact / Red News).
   - Kiểm tra bộ lọc bài học thua lỗ (Loss Lessons Guard), độ giãn spread (Quote Guard), và tình trạng kết nối websocket (Feed Guard).

---

## 2. Quy Trình Phân Tích Kịch Bản 3 Phiên (3-Session Analysis)

Hệ thống đánh giá sự luân chuyển thanh khoản qua 3 phiên liên tiếp:

```
[Phiên Á (Asia)]  ───>  [Phiên Âu (London)]  ───>  [Phiên Mỹ (New York)]
(Tạo vùng Range)       (Quét thanh khoản Á)       (Xác nhận hướng đi chính)
```

### Quan hệ giữa các phiên (`relation_status`):
- **SUPPORTS**: Khi phiên Á và Âu xác nhận cấu trúc cùng hướng với kịch bản chuẩn bị vào lệnh phiên NY (ví dụ: Á/Âu tích lũy tạo thanh khoản đáy, sau đó quét thanh khoản và đảo chiều tăng trước giờ mở cửa NY).
- **CONFLICTS**: Khi hướng đi hoặc tín hiệu sweep của phiên trước đi ngược lại hoàn toàn với giả định của phiên NY (ví dụ: phiên NY dự kiến Buy nhưng phiên Âu vừa phá vỡ cấu trúc giảm mạnh và tạo FVG giảm lớn).
- **UNCERTAIN**: Khi thị trường đi ngang biên hẹp, không có sự kiện phá vỡ (MSS/CHoCH) hoặc thiếu dữ liệu nến đóng.

---

## 3. Vòng Đời Của Một Lệnh Quan Sát (Observation Lifecycle)

Mỗi kịch bản tiềm năng trải qua các trạng thái tuần tự và được lưu vết chặt chẽ:

1. **CANDIDATE (Ứng viên)**: Kịch bản xuất hiện khi phát hiện vùng mất cân bằng (FVG) hoặc mức thanh khoản (EQH/EQL) đang chờ quét.
2. **WAITING_SWEEP / WAITING_BREAK**: Chờ nến đóng thực tế quét qua đỉnh/đáy và có nến phá vỡ cấu trúc có thân nến rõ ràng (Displacement).
3. **STRUCTURE_CONFIRMED (READY)**: Đã thỏa mãn toàn bộ checklist SMC:
   - Đã quét thanh khoản (Liquidity Sweep).
   - Đã phá vỡ cấu trúc (MSS/CHoCH).
   - Đã xác nhận vùng FVG còn hiệu lực.
   - Nến retest đã đóng chạm vào vùng vào lệnh.
4. **ARMED (Sẵn sàng khớp)**: Khi người dùng bấm Arm hoặc chế độ Auto-Paper kích hoạt trong phiên NY và toàn bộ các Guards đều xanh (`can_arm = true`).
5. **PAPER_OPEN (Vị thế đang mở)**: Lệnh đã khớp theo giá thị trường hoặc limit. Hệ thống hiển thị R:R cố định và khóa toàn bộ thao tác chỉnh sửa (`readOnly = true`).
6. **PAPER_CLOSED (Đã đóng)**: Khi giá chạm SL, chạm TP, hoặc hết giờ phiên giao dịch (NY Session Close).
   - Toàn bộ dữ liệu kết quả (PnL, Realized R, Lý do đóng) được tự động ghi nhận vào bảng cơ sở dữ liệu `paper_observations`.

---

## 4. Hướng Dẫn Thao Tác Cho Người Dùng (Operator Checklist)

### Bước 1: Kiểm Tra Trạng Thái Hệ Thống Đầu Phiên
- Mở tab **Kế hoạch & Lệnh dự kiến (Upcoming Setups)** trước 08:00 EST.
- Kiểm tra widget **Phiên Giao Dịch**: Đảm bảo hiển thị đúng múi giờ và thời gian đếm ngược đến NY Session.
- Xem danh sách kịch bản phiên Á/Âu và cột **Quan hệ kịch bản** (`SUPPORTS` / `CONFLICTS`).

### Bước 2: Xem Bằng Chứng Trên Chart (Chart Evidence)
- Bấm **"Xem Trên Chart"** ở bất kỳ setup nào.
- Chart sẽ tự động focus vào mốc thời gian của tín hiệu và hiển thị:
  - Các đỉnh/đáy swing (`H`, `L`, `HH`, `LL`).
  - Đường phá vỡ cấu trúc (`BOS` / `CHoCH`).
  - Hộp vùng FVG (xanh cho Bullish, đỏ cho Bearish).
- Bấm nút **"Chi tiết"** trên thanh header của chart để mở drawer **Bằng chứng xác nhận SMC**:
  - Đối chiếu từng bước trong `EvidenceAccordion`.
  - Kiểm tra badge **Snapshot Provenance** để chắc chắn snapshot gắn liền với đúng phiên bản thuật toán.

### Bước 3: Thử Nghiệm Kế Hoạch Với Bản Nháp (Draft)
- Nếu muốn tùy chỉnh điểm vào lệnh cá nhân mà không ảnh hưởng đến setup chuẩn của bot: Bấm **"Chép sang Bản Nháp (Draft)"**.
- Kéo thả các đường SL, TP hoặc Body R:R trực tiếp trên chart:
  - Khi kéo SL của lệnh SHORT lên trên -> SL tăng.
  - Khi vô tình kéo SL xuống dưới Entry của lệnh SHORT -> Hệ thống hiển thị cảnh báo đỏ và khóa nút Arm để bảo vệ an toàn.
- Bản nháp chỉ tồn tại ở bộ nhớ client, không tạo bất kỳ mutation hay lệnh thật nào.

### Bước 4: Kiểm Tra Sau Phiên (Post-Session Review)
- Mở tab **Nhật Ký (Journal)**.
- Bấm nút **"Chart"** trên từng hàng lệnh đã đóng để xem lại chính xác hình ảnh snapshot lúc lệnh diễn ra.
- Ghi chú tâm lý, lý do vào lệnh vào mục **Chi Tiết & Note** để phục vụ việc tự động học hỏi của bộ lọc Loss Lessons.

---

## 5. Tuyên Bố Minh Bạch Về Xác Suất (Honest Probability Statement)

- Hệ thống hiện tại **KHÔNG** đưa ra tỷ lệ phần trăm xác suất thắng (Win Rate %) hay độ tin cậy mô hình dạng số liệu giả lập.
- Tất cả các trường xác suất đều hiển thị minh bạch là `UNAVAILABLE` hoặc `NOT_VERIFIED` cho đến khi có bộ dữ liệu Out-Of-Sample (OOS) độc lập được kiểm định qua tối thiểu 100 lệnh forward paper có ghi nhận thực tế trong bảng `paper_observations`.
