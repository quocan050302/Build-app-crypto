# TÀI LIỆU HỆ THỐNG THÔNG BÁO V9.1 CHO NGƯỜI MỚI (AURUM DESK)

## 1. Mục Tiêu & Triết Lý Thiết Kế
Phiên bản **V9.1** thực hiện cuộc đại tu toàn diện hệ thống thông báo, cảnh báo lỗi và hiển thị trạng thái giao dịch trên toàn bộ ứng dụng **Aurum Desk**, tập trung vào trải nghiệm của **người mới bắt đầu hiểu căn bản về trading**.

Mỗi thông báo và trạng thái trong ứng dụng tuân thủ nghiêm ngặt **4 câu hỏi cốt lõi**:
1. **Điều gì vừa xảy ra?** (Title ngắn gọn 1 câu, không hiển thị mã lỗi constant như `PAPER_OPEN`, `LIQUIDATED`, `ARMED` làm từ ngữ duy nhất).
2. **Vì sao xảy ra, dựa trên dữ liệu nào?** (Summary 1–2 câu giải thích rõ nguyên nhân với các thông số thực tế: Net R:R, đòn bẩy, giá Bid/Ask, thời gian tin tức, snapshot rủi ro...).
3. **Trạng thái thực tế của lệnh là gì?** (Lệnh đang chờ khớp `ARMED`, đã khớp mở vị thế `FILLED`, đã đóng do `TP`/`SL`/`MANUAL`, bị từ chối `REJECTED`, hay chưa rõ kết quả `UNKNOWN` do mất kết nối mạng).
4. **Cần làm gì tiếp theo, hoặc cần chờ tới khi nào?** (Hướng dẫn hành động cụ thể, thời gian cooldown hoặc đếm ngược kết thúc cửa sổ tin tức, gợi ý kiểm tra cài đặt hoặc đối soát trạng thái mà không khuyến khích thao tác liều lĩnh như tăng đòn bẩy hay kéo giãn TP).

---

## 2. Mô Hình Dữ Liệu Typed Presentation Model (`UserMessage`)

Được định nghĩa tại [userMessage.ts](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/types/userMessage.ts):

```typescript
export type MessageSeverity = 'info' | 'success' | 'warning' | 'error';
export type OutcomeCertainty = 'confirmed' | 'pending' | 'unknown' | 'estimated';

export type SemanticActionType =
  | 'OPEN_POSITION'
  | 'VIEW_PENDING_ORDER'
  | 'REFRESH_SETUP'
  | 'OPEN_NEWS'
  | 'OPEN_SETTINGS'
  | 'OPEN_TELEGRAM_HISTORY'
  | 'VIEW_TRADE'
  | 'RECONCILE_STATUS'
  | 'DISMISS';

export interface SemanticAction {
  type: SemanticActionType;
  label: string;
  payload?: Record<string, any>;
}

export type SemanticActionInput = SemanticAction | SemanticActionType;

export interface TechnicalDetails {
  code?: string;
  http_status?: number;
  correlation_id?: string;
  timestamp?: number;
  operation?: string;
  raw_error?: string;
  field_errors?: Array<{ field: string; message: string }>;
  [key: string]: any;
}

export interface UserMessage {
  id: string;
  code: string;
  severity: MessageSeverity;
  title: string;
  summary: string;
  explanation?: string;
  impact?: string;
  next_steps?: string;
  params?: Record<string, any>;
  action?: SemanticActionInput;
  operation?: string;
  entity_id?: string;
  event_id?: string;
  occurred_at: number;
  retryable?: boolean;
  outcome_certainty: OutcomeCertainty;
  technical_details?: TechnicalDetails;
  read?: boolean;
}
```

### Điểm đặc biệt của kiến trúc:
- **Dữ liệu phân tầng**:
  - `title` + `summary`: Ngắn gọn, súc tích hiển thị ngay trên Toast nổi.
  - `explanation` + `impact` + `next_steps`: Nằm trong popup "Xem chi tiết & hướng dẫn", giải thích cặn kẽ cơ chế thị trường và quản lý rủi ro cho người mới.
  - `technical_details`: Thu gọn ở dưới cùng (mã lỗi code, HTTP status, correlation ID, field errors), dành cho developer / chẩn đoán lỗi nhưng đã được lọc bỏ hoàn toàn các thông tin nhạy cảm (token, mật khẩu, raw headers).
- **Phân loại độ chắc chắn (`outcome_certainty`)**:
  - `confirmed`: Thao tác đã được backend commit và xác nhận authoritative.
  - `pending`: Đang nằm trong hàng đợi xử lý (ví dụ Telegram rate limit retry).
  - `unknown`: Mất kết nối hoặc request timeout giữa chừng, **tuyệt đối không khẳng định lệnh đã thành công hay thất bại**; hướng dẫn người dùng đối soát authoritative.
  - `estimated`: Kết quả ước tính từ dữ liệu đối soát lúc app tắt (offline reconciliation), không khẳng định là live tick.

---

## 3. Danh Mục Thông Báo Toàn Diện (Catalog & Vietnamese Templates)

Được định nghĩa tập trung tại [userMessageCatalog.ts](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/utils/userMessageCatalog.ts):

| Mã Lỗi / Sự Kiện | Tiêu Đề Tiếng Việt | Nội Dung Tóm Tắt & Giải Thích | Hướng Dẫn Hành Động Tiếp Theo |
| :--- | :--- | :--- | :--- |
| **`ACTIVE_POSITION_EXISTS`** | Bạn đang có một lệnh mở | App giới hạn tối đa 1 vị thế đồng thời để bảo vệ vốn. Vị thế đang chạy được ExitMonitor giám sát tự động. | Xem vị thế đang mở; chờ lệnh kết thúc trước khi tạo lệnh mới. Không vội vàng đóng lệnh để né guard. |
| **`ARMED_ORDER_EXISTS`** | Bạn đã có một lệnh chờ khớp | Hệ thống đang duy trì 1 lệnh chờ kích hoạt theo điều kiện giá Bid/Ask. Bạn chưa có vị thế mở từ lệnh này. | Xem lệnh chờ hoặc hủy lệnh trong danh sách Kế Hoạch nếu bạn muốn đổi chiến lược. |
| **`WAITING_STRUCTURE`** | Chưa đủ tín hiệu để đặt lệnh | Hệ thống đang chờ cấu trúc nến SMC xác nhận (BOS, CHoCH hoặc quét thanh khoản FVG). | Kiên nhẫn chờ nến đóng cửa xác nhận cấu trúc chuẩn, không vào lệnh sớm trước tín hiệu. |
| **`STRATEGY_NOT_READY`** | Kế hoạch chưa sẵn sàng | Kế hoạch giao dịch chưa hoàn tất toàn bộ các điều kiện an toàn và chỉ báo kỹ thuật theo cấu hình. | Chờ các điều kiện hoàn tất. Điều này không có nghĩa hệ thống bị lỗi. |
| **`SETUP_TERMINAL`** | Kế hoạch này đã kết thúc | Kế hoạch SMC đã hết hạn hiệu lực, bị hủy bỏ hoặc đã hoàn thành chu kỳ. | Làm mới danh sách Kế Hoạch để theo dõi các cơ hội mới xuất hiện. |
| **`NEWS_BLACKOUT`** | Tạm dừng vào lệnh gần giờ có tin | Hệ thống khóa quyền vào lệnh trước và sau thời điểm công bố tin tức kinh tế quan trọng (Đỏ/Cam). | Mở tab Tin Tức & Blackout để xem thời gian tin ra và thời điểm mở khóa lệnh. |
| **`INSUFFICIENT_RR`** | Lợi nhuận dự kiến chưa đủ so với rủi ro | Tỷ lệ Net R:R sau khi trừ phí giao dịch 2 chiều và trượt giá chưa đạt mức tối thiểu yêu cầu (1:2.0). | Chờ kế hoạch có điểm vào lệnh tốt hơn; không tự ý kéo TP ra xa chỉ để đủ số. |
| **`INVALID_PRICE_GEOMETRY`** | Giá vào, cắt lỗ và chốt lời chưa hợp lệ | LONG yêu cầu: SL < Entry < TP. SHORT yêu cầu: TP < Entry < SL. Các mức giá không được trùng nhau. | Kiểm tra lại thước đo R:R trên biểu đồ và điều chỉnh các mức giá hợp lý. |
| **`CALCULATOR_INVALID`** | Kế hoạch chưa đáp ứng điều kiện thực thi | Máy tính rủi ro không thể xác định khối lượng vào lệnh hợp lệ do khoảng cách SL quá hẹp hoặc vượt ngưỡng. | Kiểm tra lại khoảng cách cắt lỗ và số dư ký quỹ. |
| **`MAX_DAILY_ENTRIES`** | Đã đạt số lệnh tối đa hôm nay | Tài khoản đã khớp đủ 3 lượt lệnh tối đa cho phép trong ngày (tính theo múi giờ Việt Nam). | Đóng máy nghỉ ngơi để bảo toàn kỷ luật giao dịch. Hạn mức sẽ tự làm mới vào ngày mai. |
| **`MAX_CONSECUTIVE_LOSSES`**| App đã tạm dừng sau chuỗi lệnh thua | Hệ thống ghi nhận 2 lệnh lỗ liên tiếp. Cơ chế tự động khóa để ngăn ngừa tâm lý gỡ gạc (revenge trading). | Dành thời gian ghi chép nhật ký, xem lại nguyên nhân thua lỗ trước khi vào lệnh tiếp theo. |
| **`DAY_BLOCKED`** | Đang bảo vệ giới hạn lỗ trong ngày | Tổng mức lỗ trong ngày đã chạm ngưỡng tối đa (1.5% vốn). Toàn bộ quyền vào lệnh mới bị khóa. | Không cố gắng gỡ gạc. Xem báo cáo tổng kết ngày trong tab Báo Cáo. |
| **`COOLDOWN_ACTIVE`** | Đang trong thời gian nghỉ giãn cách | Áp dụng khoảng thời gian nghỉ giữa các lệnh để thị trường hấp thụ biến động. | Đồng hồ đếm ngược đang chạy, hãy thư giãn chờ hệ thống mở lại quyền Arm. |
| **`OUTSIDE_ENTRY_WINDOW`** | Chưa đến khung giờ được phép vào lệnh | Nằm ngoài khung giờ giao dịch phiên Mỹ theo cấu hình (08:00 - 11:00 NY, tương ứng phiên tối VN). | Chờ đến phiên New York mở cửa để có thanh khoản và biên độ di chuyển tốt nhất. |
| **`NY_SLOT_RESERVED`** | Đang giữ lượt giao dịch cho phiên Mỹ | Hệ thống ưu tiên giữ ít nhất 1 lượt giao dịch cho phiên New York có xác suất cao nhất. | Chờ phiên Mỹ bắt đầu để tận dụng lượt giao dịch tối ưu. |
| **`CROSS_MARGIN_UNSUPPORTED`**| Chế độ Cross chưa hỗ trợ thực thi PAPER | Chế độ thực thi mô phỏng PAPER hiện yêu cầu Isolated Margin để cô lập rủi ro trên từng lệnh. | Chuyển chế độ ký quỹ sang ISOLATED trong tab Quản Trị Vốn. |
| **`TICKER_STALE`** | Giá nhận được đã cũ | Luồng dữ liệu nến hoặc giá thị trường bị trễ so với thời gian thực. Tạm dừng để tránh trượt giá. | Hệ thống đang chờ dữ liệu nến mới từ WebSocket. |
| **`FEED_DISCONNECTED`** | Mất kết nối luồng dữ liệu thị trường | Không nhận được báo giá thị trường. Tạm thời khóa các thao tác giao dịch để bảo đảm an toàn. | Kiểm tra kết nối mạng Internet. App sẽ tự động kết nối lại khi có mạng. |
| **`EXECUTION_FEED_DEGRADED`**| Dữ liệu thực thi đang bị gián đoạn | Phát hiện khoảng trống dữ liệu hoặc feed suy giảm chất lượng. | Hệ thống tạm dừng khớp lệnh để đối soát tính toàn vẹn của nến. |
| **`MALFORMED_QUOTE`** | Dữ liệu giá không hợp lệ | Báo giá nhận được có cấu trúc bất thường, spread đảo ngược hoặc chứa giá trị NaN/Infinity. | App tự động loại bỏ báo giá lỗi để tránh tính sai rủi ro; tự chờ dữ liệu chuẩn. |
| **`STALE_EDIT`** | Dữ liệu đã thay đổi từ lúc bạn mở màn hình | Phiên bản kế hoạch hoặc cài đặt rủi ro trên máy chủ đã thay đổi (Optimistic Locking). | Bản nháp được giữ nguyên; bấm làm mới để xem bản cập nhật mới nhất và xác nhận lại. |
| **`SETUP_CONFLICT`** | Xung đột phiên bản kế hoạch | Trạng thái kế hoạch đã thay đổi trên hệ thống trước khi lệnh arm kịp xử lý. | Bấm làm mới để đồng bộ kế hoạch mới nhất. |
| **`INSUFFICIENT_MARGIN`** | Số dư ký quỹ không đủ | Số tiền ký quỹ yêu cầu vượt quá vốn khả dụng hiện tại trong tài khoản PAPER. | Giảm tỷ lệ rủi ro mỗi lệnh (%) hoặc đóng các vị thế không cần thiết. Không vội tăng đòn bẩy. |
| **`UNAUTHORIZED`** | Bot Telegram chưa được xác thực | Bot Token Telegram không hợp lệ hoặc bị từ chối truy cập. | Kiểm tra và cập nhật lại Bot Token chính xác trong tab Cài Đặt Telegram. |
| **`CHAT_NOT_FOUND`** | Không tìm thấy cuộc trò chuyện Telegram | Chat ID không tồn tại hoặc bạn chưa bấm `/start` tương tác với bot trên ứng dụng Telegram. | Mở Telegram, tìm bot và bấm **Start**, sau đó nhập lại Chat ID. |
| **`FORBIDDEN`** | Bot Telegram bị chặn | Bot Telegram đã bị người dùng chặn (Block) hoặc bị kích khỏi nhóm nhận tin. | Mở Telegram và chọn "Unblock bot" để tiếp tục nhận thông báo. |
| **`RATE_LIMIT`** | Telegram yêu cầu chờ trước khi gửi tiếp | Telegram Bot API giới hạn tần suất gửi tin. Hệ thống đã đưa tin vào hàng đợi retry. | Không bấm gửi thử liên tục để tránh bị Telegram gia hạn thời gian phạt. |
| **`AMBIGUOUS`** | Chưa xác nhận được tin nhắn đã tới Telegram | Mạng bị ngắt trong lúc gửi yêu cầu đến Telegram. Tin nhắn có thể đã được chuyển đến máy chủ. | Mở ứng dụng Telegram trên điện thoại kiểm tra trước khi bấm gửi lại để tránh bị trùng tin. |
| **`TIMEOUT`** | Chưa xác nhận được kết quả thao tác | Yêu cầu POST mở/đóng/arm bị timeout mạng. Backend có thể đã ghi nhận commit thành công. | **Tuyệt đối không bấm lại liên tục**. Bấm "Đối Soát Trạng Thái" để kiểm tra authoritative. |
| **`UNKNOWN`** | Chưa xử lý được thao tác này | Phản hồi chưa mong đợi từ hệ thống. Dữ liệu trạng thái vẫn được bảo toàn an toàn. | Bấm làm mới để đối soát trạng thái trước khi thử lại. Mã lỗi kỹ thuật được lưu trong chi tiết. |
| **`TRADE_OPENED`** | Lệnh mua/bán đã khớp (PAPER) | Vị thế mô phỏng PAPER đã được mở tại giá thị trường với đòn bẩy và chi phí thực tế. | Vị thế đang chạy và được ExitMonitor giám sát tự động. Xem biểu đồ hoặc Quản Trị Vốn. |
| **`TRADE_CLOSED_TP`** | Vị thế đã đóng: Đạt Chốt Lời (TP) | Giá thị trường chạm ngưỡng Take Profit. Lợi nhuận ròng (tiền và R) đã cộng vào số dư sau phí. | Xem chi tiết lệnh và ghi nhận kinh nghiệm trong tab Nhật Ký & Bài Học. |
| **`TRADE_CLOSED_SL`** | Vị thế đã đóng: Chạm Cắt Lỗ (SL) | Thị trường đi ngược xu hướng và chạm Stop Loss đã định. Kỷ luật cắt lỗ bảo vệ số vốn còn lại. | Khoản lỗ ròng đã hạch toán an toàn. Xem lại tâm lý giao dịch trong tab Nhật Ký. |
| **`TRADE_CLOSED_MANUAL`** | Vị thế đã đóng chủ động theo giá thị trường | Bạn đã đóng vị thế chủ động trước khi chạm TP/SL. Kết quả lãi/lỗ được chốt dứt điểm. | Phân biệt rõ đóng lệnh tay với SL/TP. Ghi chú lý do đóng lệnh vào nhật ký. |
| **`ORDER_ARMED`** | Đã đặt lệnh chờ khớp | Lệnh đang chờ điều kiện giá Bid/Ask chạm vùng kích hoạt. Bạn chưa có vị thế mở từ lệnh này. | Theo dõi lệnh trong danh sách Kế Hoạch hoặc hủy lệnh nếu bạn muốn đổi hướng. |
| **`SETUP_READY`** | Có kế hoạch giao dịch đủ điều kiện | Kế hoạch SMC đã đáp ứng các tiêu chí chiến lược. Bạn chưa có vị thế mới; cần kiểm tra giá trước khi khớp. | Xem xét kế hoạch trên biểu đồ hoặc bấm Arm để đưa vào hàng đợi chờ khớp. |

---

## 4. Bộ Chuẩn Hóa Lỗi Toàn Diện (`normalizeAppError.ts`)

Được triển khai tại [normalizeAppError.ts](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/utils/normalizeAppError.ts):

### Khả năng nhận diện và chuẩn hóa:
1. **Axios Response với cấu trúc chi tiết (`data.detail`)**:
   - Nhận diện `detail` dạng Object có `code`, `message`, `params`, `eligibility`, `reason_codes`.
   - Nhận diện `detail` dạng chuỗi có prefix legacy (ví dụ `ACTIVE_POSITION_EXISTS: Đã có vị thế đang chạy`).
   - Nhận diện `detail` dạng mảng lỗi FastAPI 422 Validation Error:
     - Dịch trường tiếng Anh sang tiếng Việt tự nhiên (`planned_entry` -> `Giá vào lệnh`, `stop_loss` -> `Giá cắt lỗ`, `take_profit` -> `Giá chốt lời`, `leverage` -> `Đòn bẩy`, `risk_pct` -> `Tỷ lệ rủi ro`, `chat_id` -> `Chat ID Telegram`...).
     - Chuyển `field required` thành `"Bạn chưa nhập Giá cắt lỗ."` đúng ngữ cảnh, không dùng tiếng Anh cụt lủn.
2. **Phân biệt xung đột HTTP 409 theo ngữ cảnh thao tác (`context.operation`)**:
   - `arm_setup` bị 409 -> `SETUP_CHANGED` ("Kế hoạch đã có sự thay đổi").
   - `save_risk_settings` bị 409 -> `STALE_EDIT` ("Dữ liệu đã thay đổi từ lúc bạn mở màn hình").
   - `save_journal_review` bị 409 -> `STALE_EDIT` ("Nhật ký đã được cập nhật từ trước").
   - 409 thông thường -> `SETUP_CONFLICT` ("Xung đột phiên bản kế hoạch").
3. **Bảo mật và khử trùng dữ liệu nhạy cảm (`sanitizeText`)**:
   - Tự động tìm và thay thế Token Bot Telegram (`123456789:ABCdef...` -> `[REDACTED_TELEGRAM_BOT_TOKEN]`).
   - Lọc bỏ Bearer Authorization token (`Bearer eyJhbG...` -> `Bearer [REDACTED]`).
   - Lọc bỏ mật khẩu, query params chứa credential hoặc secret.
4. **Hàm định dạng chuẩn hóa**:
   - `formatPrice(val, precision)`: Hỗ trợ hiển thị số thập phân theo locale vi-VN (`2.745,50`), xử lý null/NaN/Infinity trả về `"—”`.
   - `formatCurrency(val)`: Giữ nguyên số 0 (`0,00 USDT`), số âm (`-5,25 USDT`), null trả về `"chưa có dữ liệu"`.
   - `formatRRRatio(val)`: Định dạng `1:2.50`, bảo vệ chống giá trị vô lý `<= 0` thành `"Chưa đạt"`.
   - `formatLeverage(val)`: Hiển thị `5×`, `50×`, không gọi mức đòn bẩy trần là lỗi.

---

## 5. Kiến Trúc Thông Báo Toast Bounded & Trung Tâm Thông Báo

### Các thành phần chính:
- **`NotificationProvider`** ([NotificationContext.tsx](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/context/NotificationContext.tsx)):
  - Hàng đợi giới hạn: **Tối đa 3 toast hiển thị cùng lúc** (`MAX_VISIBLE_TOASTS = 3`), toast mới đẩy vào đầu hàng đợi nhưng không làm mất các thông báo quan trọng.
  - Bộ nhớ lịch sử phiên: **Lưu trữ tối đa 50 thông báo** gần nhất (`MAX_HISTORY_ITEMS = 50`) để người dùng mở xem lại bất cứ lúc nào qua Drawer.
  - Cơ chế chống lặp sự kiện (Deduplication): Ngăn chặn việc bắn duplicate toast trong vòng 3000ms khi HTTP response và WebSocket domain event phát gần nhau.
  - Tự động đóng thông minh: Success tự đóng sau 7 giây; Info tự đóng sau 10 giây; **Warning và Error được giữ lại** cho đến khi người dùng chủ động đóng hoặc thao tác xong.
  - Tạm dừng khi rê chuột (Hover Pause): Giữ toast không bị biến mất khi người dùng đang đọc hoặc chuẩn bị bấm nút.
  - Điều hướng phím tắt: Bấm phím **ESC** tự động đóng Modal chi tiết hoặc Drawer lịch sử.
- **`NotificationToastContainer`** ([NotificationToastContainer.tsx](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/components/NotificationToastContainer.tsx)):
  - Toast hiển thị gọn gàng ở góc trên bên phải, có badge độ chắc chắn (`Chưa rõ kết quả` khi timeout/mất mạng), nút "Xem chi tiết & hướng dẫn", và nút Semantic Action.
- **`UserMessageDetailsModal`** ([UserMessageDetailsModal.tsx](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/components/UserMessageDetailsModal.tsx)):
  - Modal chi tiết cung cấp lời giải thích toàn diện chia thành 3 mục:
    1. **Điều gì vừa xảy ra?**
    2. **Tác động đến tài khoản / lệnh của bạn**
    3. **Bạn cần làm gì tiếp theo?**
  - Khối thông tin kỹ thuật thu gọn dành cho chẩn đoán: Hiển thị Code, HTTP Status, Operation, Timestamp, và Field Errors (đã qua khử trùng).
- **`NotificationCenterDrawer`** ([NotificationCenterDrawer.tsx](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/frontend/src/components/NotificationCenterDrawer.tsx)):
  - Thanh trượt từ bên phải với các tab lọc: **Tất cả**, **Lệnh & Vị Thế**, **Cảnh Báo & Lỗi**.
  - Tính năng "Đánh dấu tất cả đã đọc" và "Xóa lịch sử".

---

## 6. Toàn Bộ Files Đã Audit & Nâng Cấp

1. **`frontend/src/types/userMessage.ts`** *(Mới)*: Định nghĩa toàn bộ kiểu dữ liệu presentation model.
2. **`frontend/src/utils/userMessageCatalog.ts`** *(Mới)*: Chứa từ điển hơn 34 mã lỗi và mẫu giải thích tiếng Việt.
3. **`frontend/src/utils/normalizeAppError.ts`** *(Mới)*: Normalizer chuyển đổi lỗi Axios/FastAPI/string/WebSocket sang `UserMessage`.
4. **`frontend/src/context/NotificationContext.tsx`** *(Mới)*: Provider quản lý state toast, queue, drawer và lịch sử.
5. **`frontend/src/components/NotificationToastContainer.tsx`** *(Mới)*: Component hiển thị tối đa 3 toast nổi.
6. **`frontend/src/components/UserMessageDetailsModal.tsx`** *(Mới)*: Modal chi tiết và hướng dẫn xử lý.
7. **`frontend/src/components/NotificationCenterDrawer.tsx`** *(Mới)*: Drawer lịch sử thông báo phiên làm việc.
8. **`frontend/src/App.tsx`**:
   - Bọc toàn bộ ứng dụng trong `<NotificationProvider>`.
   - **Xóa bỏ toàn bộ 12 lời gọi `alert(...)`**, thay thế hoàn toàn bằng `notifyError` hoặc `notify`.
   - Kết nối WebSocket Domain Events (`trade.opened`, `trade.closed`, `order.armed`, `setup.ready`, `feed.degraded`, `feed.recovered`) trực tiếp vào bộ sinh `buildTradeEventMessage`.
   - Thêm nút chuông "Thông báo" trên Header với badge số lượng tin chưa đọc.
   - Xử lý timeout POST với trạng thái `outcome_certainty: 'unknown'` và hành động `RECONCILE_STATUS`.
9. **`frontend/src/ExpectedEntryPanel.tsx`**:
   - Xóa bỏ hiển thị mã lỗi constant thô `"Chặn: <reason_code>"`.
   - Tạo mảng `blockers` có cấu trúc: Thẻ lý do chính nổi bật (`primaryBlocker`) và accordion mở rộng `"Còn N điều kiện an toàn cần kiểm tra"`.
10. **`frontend/src/NYSessionPanel.tsx`**:
    - Thay thế mã lỗi thô trong tiêu đề bằng từ điển tiếng Việt (ví dụ `TẠM DỪNG: ĐÃ ĐẠT SỐ LỆNH TỐI ĐA HÔM NAY`).
11. **`frontend/src/TestingLabComponent.tsx`**:
    - **Xóa bỏ toàn bộ 4 lời gọi `alert(...)`**, thay bằng `onNotify`.
12. **`frontend/src/JournalTab.tsx`**:
    - **Xóa bỏ toàn bộ 4 lời gọi `alert(...)`**, thay bằng `showToast`.
    - Chuẩn hóa thông báo duyệt bài học: *"Đã duyệt bài học. Bài học đủ điều kiện được chiến lược tham khảo khi phù hợp."* (không khẳng định bài học đã được nạp ngay vào bộ nhớ lệnh sau).
13. **`frontend/src/TelegramTab.tsx`**:
    - **Xóa bỏ toàn bộ 2 lời gọi `alert(...)`**, thay bằng `showToast`.
    - Phân biệt rõ: Gửi thử thành công khác với bot tự động đang bật; đưa tin vào hàng đợi khác với đã gửi thành công.
14. **`frontend/src/v9_1_user_messages.test.ts`** *(Mới)*:
    - 19 bài kiểm thử đơn vị và tích hợp cho V9.1 catalog, normalizer, sanitization, domain events và formatting.

---

## 7. Kết Quả Kiểm Thử Toàn Diện

### 1. Frontend Test Suite (Vitest)
```bash
npm test -- --run
```
- **Tổng số test files**: 7 files passed (100%)
- **Tổng số tests**: 43 tests passed (100%)
  - `src/v9_1_user_messages.test.ts` (19 tests) - **PASS**
  - `src/v5_1_geometry_and_news.test.ts` (4 tests) - **PASS**
  - `src/selection_logic.test.ts` (5 tests) - **PASS**
  - `src/v7_2_realtime.test.ts` (2 tests) - **PASS**
  - `src/v7_1_realtime.test.ts` (1 test) - **PASS**
  - `src/v8_tabs.test.ts` (5 tests) - **PASS**
  - `src/extract_error.test.ts` (7 tests) - **PASS**

### 2. Frontend Production Build & TypeCheck
```bash
npm run build
# tsc -b && vite build
```
- **Kết quả**: Thành công 100%, 0 lỗi TypeScript, 0 lỗi bundle.

### 3. Frontend Linter (Oxlint)
```bash
npm run lint
```
- **Kết quả**: 0 lỗi lint (0 errors).

### 4. Backend Pytest Suite
```bash
PYTHONPATH=./backend ./backend/venv/bin/pytest backend/tests/ -q
```
- **Kết quả**: **174 passed** in 5.29s (100% PASS).

### 5. Browser Visual Subagent Audit
- **Trạng thái**: BLOCKED / NOT_RUN do dịch vụ subagent gặp lỗi ngoại vi `UNAVAILABLE (code 503): No capacity available for model gemini-3-flash on the server`.
- Ứng dụng đã được kiểm chứng hoạt động trực tiếp qua HTTP server và Vite dev server tại cổng 5173.

---

## 8. Bảo Toàn Logic & Quy Tắc An Toàn (Behavior Preserved)
- Giữ nguyên 100% các chốt an toàn giao dịch: Chế độ PAPER TRADING, giới hạn tối đa 3 lệnh/ngày, tối đa 1 vị thế mở, tối đa 1 lệnh chờ (armed), chính sách phiên New York, bộ lọc tin tức kinh tế (News Blackout), dừng giao dịch khi chạm giới hạn lỗ.
- Không nới lỏng trading guards chỉ để dễ hiển thị thông báo.
- Bảo tồn toàn vẹn cơ sở dữ liệu `aurum_desk.db`, không thực hiện reset DB hay seed dữ liệu giả trên môi trường runtime.
- Mã lỗi kỹ thuật (`code`, `reason_codes`) luôn được lưu trữ trong `technical_details` phục vụ logging và automation tests, không bị xóa bỏ.
