# Báo Cáo Nghiệm Thu V10.3: UI Giao Dịch Vừa Màn Hình & Sửa Spam Toast `setup.updated`

## 1. Thông Tin Chung & Cam Kết Bảo Vệ Luồng
- **Repository:** `https://github.com/quocan050302/Build-app-crypto`
- **Branch:** `feature/aurum-repair-smc-rr`
- **SHA Đối Chiếu Đầu Vào:** `03229db75f02d5b20beb670f40c23b4e42134f10` (tiếp nối `50bf209e696df71647a585a3d9771aabbca8c9ab`)
- **Nguyên tắc an toàn runtime:**
  - Database runtime SQLite (`aurum_desk.db`) tuyệt đối không bị reset, truncate, seed hay xoá.
  - Vị thế Paper đang mở của người dùng được giữ nguyên vẹn; không restart runtime, không gửi lệnh thật lên sàn Bitget.
  - Mọi bài test backend và frontend đều chạy trên database SQLite `:memory:` hoặc mock isolated sandbox riêng biệt.

---

## 2. Nguyên Nhân Lỗi & Cơ Chế Khắc Phục Tận Gốc

### A. Lỗi Spam Toast Đỏ Lặp (`setup.updated`)
- **Nguyên nhân gốc rễ:**
  1. Trong `frontend/src/App.tsx`, WebSocket handler khi nhận `DOMAIN_EVENT` với event `setup.updated` đã chuyển tiếp vào `buildTradeEventMessage(type, payload)` -> `notify(userMsg)`.
  2. Trong `frontend/src/utils/normalizeAppError.ts`, hàm `buildTradeEventMessage` gán mặc định `code = 'TRADE_EVENT'` khi không tìm thấy mapping riêng cho `setup.updated`.
  3. `getCatalogTemplate` không có mã `TRADE_EVENT` hay `SETUP_UPDATED`, dẫn tới fallback về mã `UNKNOWN` với `severity = 'error'`, `defaultCertainty = 'unknown'`.
  4. Trong `NotificationContext.tsx`, toast lỗi (`error`) không bao giờ tự động ẩn (`auto-dismiss = false`), và `MAX_VISIBLE_TOASTS = 3`. Do đó, mỗi khi có event `setup.updated` được phát ra để đồng bộ kế hoạch, hệ thống lại push một thông báo lỗi đỏ nghiêm trọng chiếm dụng màn hình.
- **Giải pháp triệt để:**
  - `buildTradeEventMessage` nhận diện `setup.updated` và trả về `null`. State kế hoạch vẫn được đồng bộ vào `upcomingSetups` và stale banner trong `App.tsx`, nhưng không tạo bất kỳ toast thông báo nào.
  - Sự kiện domain event lạ hoặc không hợp lệ trả về `null` thay vì fallback sang lỗi `UNKNOWN` của người dùng.
  - Thêm các template chuẩn vào `userMessageCatalog.ts`: `GENERIC_INFO`, `GENERIC_SUCCESS`, `GENERIC_WARNING`, `SETUP_UPDATED`, `RISK_BUDGET_EXCEEDED` để legacy `showToast` không mượn template `UNKNOWN`.
  - Hiệu chỉnh bản mẫu `UNKNOWN`: loại bỏ khẳng định chưa kiểm chứng rằng "dữ liệu đã được bảo toàn tuyệt đối", cung cấp hành động đối soát chuẩn xác (`RECONCILE_STATUS`).
  - Quản lý sự cố lặp (repeated incidents) trong `NotificationContext.tsx`: gom các lỗi nền lặp lại theo composite key (`operation:code`), lưu `first_seen`, `last_seen`, `count`. Khi người dùng đã dismiss, lỗi nền không popup lại gây quấy rầy.
  - Giới hạn presentation toast nổi: hiển thị 1 toast compact ưu tiên cao nhất kèm counter badge `+N khác` để mở Notification Center Drawer.

### B. Bảng Đối Chiếu Mapping Event Trước & Sau Sửa Đổi
| Event / Tình Huống | Hành vi Trước (V10.2) | Hành vi Sau (V10.3) | Semantics & Ghi Chú |
| :--- | :--- | :--- | :--- |
| `setup.updated` | Bắn toast đỏ `UNKNOWN`: "Chưa hoàn tất được thao tác này" | Trả về `null`; không có toast | Đồng bộ ngầm trạng thái kế hoạch & stale plan banner |
| `setup.ready` | Bắn toast `SETUP_READY` mỗi lần nhận event | Dedupe theo `setup_id + revision + instance_id` | Chỉ báo 1 lần khi chuyển sang READY thực sự |
| `trade.opened` | ID ngẫu nhiên, dễ nhầm replay | ID composite xác định theo `order_id` / `trade_id` | Phân biệt lệnh khác nhau, chặn replay trùng lặp |
| `trade.closed` (TP/SL) | Mapping cơ bản | Phân định rõ TP (`TRADE_CLOSED_TP`), SL (`TRADE_CLOSED_SL`), Manual (`TRADE_CLOSED_MANUAL`), Reconciled | Giữ nguyên PnL ròng, Net R:R và certainty |
| Domain event lạ / lỗi format | Biến thành lỗi người dùng `UNKNOWN` | Trả về `null`, ghi log chẩn đoán | Không đổ lỗi hệ thống lên người dùng |
| Lỗi nền lặp lại (Polling/Network) | Bắn toast mới mỗi 3s/45s | Gom thành Incident, tăng `count`, không tái hiện nếu đã dismiss | Chỉ tái hiện khi có escalation hoặc recovery |
| Legacy `showToast` info/success | Mượn explanation/action của `UNKNOWN` | Sử dụng template `GENERIC_INFO/SUCCESS/WARNING` | Đúng độ chắc chắn (`confirmed`), action phù hợp |

---

## 3. Kiến Trúc Giao Diện Mới (Fit Viewport Desktop)

### A. App Shell & Layout Viewport
- **Container gốc:** Đổi từ `min-h-screen` sang `h-screen h-[100dvh] max-h-[100dvh] overflow-hidden select-none`.
- **Main Workspace:** `flex-1 min-h-0 flex flex-col lg:flex-row gap-3 overflow-hidden`.
- **Chart Column:** `flex-1 min-w-0 min-h-0 flex flex-col h-full overflow-hidden`. Bỏ thuộc tính `min-h-[560px]` cứng.
- **ResizeObserver cho Chart:** Thay thế listener `window.onresize` bằng `ResizeObserver` theo dõi trực tiếp kích thước vùng chứa biểu đồ, kết hợp điều tiết qua `requestAnimationFrame` và cleanup an toàn khi unmount. Không recreate series hay socket khi kích thước container thay đổi.
- **Tab nội bộ:** Các tab `upcoming`, `smc`, `paper`, `lab`, `reports`, `news`, `journal`, `telegram`, `education` sử dụng `flex-1 min-h-0 overflow-y-auto` cuộn nội bộ độc lập.

### B. Component `QuickDecisionSidebar` (Sidebar Quyết Định Nhanh)
- Component độc lập (`frontend/src/components/QuickDecisionSidebar.tsx`), độ rộng cố định `w-full lg:w-[360px] xl:w-[380px] shrink-0 h-full flex flex-col min-h-0`:
  1. **Header (`shrink-0`):** Hiển thị hướng lệnh (`LONG`/`SHORT`), cặp tiền `XAUUSDT`, khung thời gian, nhãn trạng thái tiếng Việt (`Sẵn Sàng`, `Đã Lên Nòng`, `Đang Theo Dõi`), live quote và độ tươi dữ liệu (`freshness`).
  2. **Body (`flex-1 min-h-0 overflow-y-auto`):**
     - Thẻ vị thế mở (Open Position Card) ưu tiên trên cùng nếu đang có vị thế PAPER chạy: Entry thực tế, SL, TP, PnL live, R:R khớp lệnh, đòn bẩy và nút đóng vị thế ngay.
     - Thẻ kế hoạch chọn: khoảng cách live tới Entry (USDT), 1 hàng 3 cột Entry / SL / TP dễ đọc.
     - Rủi ro dự kiến USDT, NetRR, GrossRR, số lượng, đòn bẩy, ký quỹ ước tính.
     - Banner cảnh báo kế hoạch cũ (`stalePlanNotice`): nút "Xem bản mới" để cập nhật toàn bộ snapshot.
     - Lý do chặn chính (nếu có): cảnh báo hình học giá không hợp lệ, chế độ Cross Margin chưa hỗ trợ, đã có lệnh mở/chờ.
     - Nút tắt điều hướng kèm badges: Điều kiện (3/4), Bài học ([đỏ/vàng/xanh counts]), Lệnh dự kiến (N setup).
  3. **Footer (`shrink-0` - Luôn Nhìn Thấy Trong Viewport):**
     - Trạng thái `ARMED`: Hiện "LỆNH CHỜ ĐÃ ĐƯỢC TẠO — CHƯA KHỚP" và nút "Hủy Lệnh Chờ".
     - Trạng thái `READY`/`WATCHING`: Nút "Lên Nòng Lệnh Chờ (Arm Paper)" hoặc nút mở lệnh thị trường, kèm text giải thích dễ hiểu khi nút bị vô hiệu hoá.

### C. Component `TradeDetailsDrawer` (Drawer Chi Tiết Phân Tích)
- Host điều phối trượt cạnh phải (`frontend/src/components/TradeDetailsDrawer.tsx`), kích thước `w-full max-w-[480px]` (tối đa 90vw), chiều cao `100dvh`:
  1. **Tab Điều Kiện (`conditions`):** Checklist điều kiện SMC đã đạt và còn thiếu, bằng chứng cấu trúc nến, các blocker ngăn vào lệnh, bước tiếp theo.
  2. **Tab Bài Học (`lessons`):** Toàn bộ bài học quy chuẩn được chuẩn hoá qua `normalizeLessonItem`, phân loại theo mức độ đỏ (`CRITICAL`), vàng (`WARN`), xanh (`INFO`), hiển thị rule identity, hiệu lực (`BLOCK_ENTRY`, `WARN_ENTRY`), thời điểm đánh giá và hành động khuyến nghị.
  3. **Tab Lệnh Dự Kiến (`upcoming`):** Danh sách toàn bộ các kịch bản phiên, bộ lọc trạng thái, nút "Xem trên chart" để focus kịch bản.
  4. **Tab Rủi Ro (`risk`):** Chi tiết phân rã chi phí phí giao dịch (Maker/Taker), trượt giá dự kiến (Slippage), ký quỹ ban đầu (Initial Margin), ký quỹ duy trì (Maintenance Margin), giá thanh lý ước tính Isolated.
  5. **Tab Phiên & Chính Sách (`session`):** Thông tin phiên Á - Âu - Mỹ, đồng hồ đếm ngược, hạn ngạch lệnh trong ngày (0/3), slot dành riêng phiên Mỹ (0/1), chính sách rủi ro.
- **Quản lý tương tác:** Đóng bằng phím ESC, click backdrop overlay, bẫy focus, return focus về nút trigger, không tự động đóng khi có quote tick hay trade event mới.

---

## 4. Ma Trận Kiểm Thử Nghiệm Thu (Test Matrix Manifest)

### Nhóm T: Routing Thông Báo & Chống Trùng Lặp (T01 - T10)
| ID | Nội dung kiểm tra | Lệnh chạy | Kết quả | Bằng chứng |
| :--- | :--- | :--- | :--- | :--- |
| **T01** | `setup.updated` đi qua subscription không tạo toast UNKNOWN/error | `npm run test` | **PASS** | `buildTradeEventMessage('setup.updated') === null` |
| **T02** | 100 sự kiện `setup.updated` burst không tạo 100 toast/history | `npm run test` | **PASS** | 100 events lọc trả về 0 UserMessage |
| **T03** | `setup.ready` chuyển trạng thái tạo 1 thông báo; replay có dedupe_key đồng nhất | `npm run test` | **PASS** | `msg1.dedupe_key === msg2.dedupe_key`, code `SETUP_READY` |
| **T04** | Hai `trade.opened` khác ID hiện đúng thực thể riêng; cùng event replay chỉ 1 | `npm run test` | **PASS** | `eventA.entity_id !== eventB.entity_id`, replay trùng key |
| **T05** | Domain event không hợp lệ / rỗng trả về null an toàn, không crash | `npm run test` | **PASS** | Unknown event type & null payload return `null` |
| **T06** | Backend error thật (`ARMED_ORDER_EXISTS`, `RISK_BUDGET_EXCEEDED`) chuẩn hoá đúng template | `npm run test` | **PASS** | Đúng mã catalog, severity và semantic action |
| **T07** | Lỗi nền lặp lại tạo incident dedupe key; tăng count, không spam popup | `npm run test` | **PASS** | `norm1.dedupe_key === norm2.dedupe_key`, severity `warning` |
| **T08** | Mutation timeout gán certainty `unknown` kèm action `RECONCILE_STATUS` | `npm run test` | **PASS** | `outcome_certainty: 'unknown'`, không hứa kết quả |
| **T09** | Legacy template `GENERIC_INFO/SUCCESS/WARNING` không mượn UNKNOWN | `npm run test` | **PASS** | Đầy đủ defaultSeverity và defaultCertainty chuẩn |
| **T10** | `generateCompositeEventId` sinh ID xác định từ envelope, không random | `npm run test` | **PASS** | Cùng payload trả về cùng 1 composite ID chuỗi |

### Nhóm U: Layout, Viewport & Trải Nghiệm Người Dùng (U01 - U11)
| ID | Nội dung kiểm tra | Lệnh chạy | Kết quả | Bằng chứng |
| :--- | :--- | :--- | :--- | :--- |
| **U01** | Desktop viewport contract: App root `h-screen overflow-hidden`, sidebar `shrink-0 h-full` | `npm run test` | **PASS** | Zero vertical document scroll contract verified |
| **U02** | Header / NYSessionPanel compact 1 dòng: `Phiên Mỹ • NY 08:10 • VN 19:10 • 0/3 hôm nay • Mỹ 0/1 • Auto bật` | `npm run test` | **PASS** | Chuỗi format compact chính xác theo yêu cầu |
| **U03** | Inner tab containers dùng `flex-1 min-h-0 overflow-y-auto` thay vì `max-h-[750px]` cứng | `npm run test` | **PASS** | Không có giới hạn chiều cao pixel cố định |
| **U04** | Setup 20 bài học / 15 blockers không làm giãn main layout; drawer đủ chi tiết | `npm run test` | **PASS** | `normalizeLessonItem` phân tách 20 bài học với severity đúng |
| **U05** | Cảnh báo hình học giá sai / stale quote hiển thị rõ ràng kèm giải thích tiếng Việt | `npm run test` | **PASS** | `validatePriceGeometry` trả về lý do chặn cụ thể |
| **U06** | Mở/đóng/chuyển section drawer bảo toàn 100% `selectedIntent`, levels, direction, revision | `npm run test` | **PASS** | Snapshot intent bất biến qua các thao tác drawer |
| **U07** | Phím ESC đóng drawer, focus management, không gọi trade API | `npm run test` | **PASS** | Callback ESC đóng drawer thành công |
| **U08** | ResizeObserver điều tiết bằng `requestAnimationFrame` ngăn redundant recalculations | `npm run test` | **PASS** | 5 resize calls chỉ kích hoạt 1 frame render duy nhất |
| **U09** | Thước đo R:R tính toán Gross và Net RR chính xác, khấu trừ phí và trượt giá | `npm run test` | **PASS** | `calculateClientRiskReward` LONG/SHORT đạt Net RR > 1.5 |
| **U10** | Sidebar hiển thị nhãn tiếng Việt chuẩn cho READY, ARMED, DRAFT, OPEN, CLOSED | `npm run test` | **PASS** | `getVietnameseStatus` dịch chính xác mọi trạng thái |
| **U11** | Chuyển tab giữa chart, news, journal không làm thay đổi trạng thái lệnh/overlay | `npm run test` | **PASS** | Overlay và plannedEntry giữ nguyên khi đổi tab |

### Nhóm F: Luồng Nghiệp Vụ & Bất Biến Backend (F01 - F08)
| ID | Nội dung kiểm tra | Lệnh chạy | Kết quả | Bằng chứng |
| :--- | :--- | :--- | :--- | :--- |
| **F01** | SHORT setup: Arm lệnh qua API lưu đúng hướng SHORT, mức giá xác nhận | `pytest backend/tests/test_v10_3_lifecycle_and_invariants.py` | **PASS** | DB lưu `confirmed_entry = 2750.0`, `direction = 'SHORT'`, `state = 'ARMED'` |
| **F02** | Thiếu hướng lệnh hợp lệ bị từ chối 400/422, không tự động fallback LONG | `pytest backend/tests/test_v10_3_lifecycle_and_invariants.py` | **PASS** | API trả về 422 Unprocessable Entity khi thiếu direction |
| **F03** | Stale revision/instance mismatch trả về HTTP 409 Conflict, chặn order mới | `pytest backend/tests/test_v10_3_lifecycle_and_invariants.py` | **PASS** | HTTP 409 trả về khi gửi expected_revision = 4 trong khi DB là 5 |
| **F04** | Vị thế PaperOrder đang mở giữ nguyên trạng thái khi thao tác UI | `pytest backend/tests/test_v10_3_lifecycle_and_invariants.py` | **PASS** | `/api/v1/positions/active` trả về đúng vị thế SHORT hiện tại |
| **F05** | Backend guards: từ chối Cross Margin và Net R:R < 1.5 | `pytest backend/tests/test_v10_3_lifecycle_and_invariants.py` | **PASS** | `can_execute = False`, mã lỗi `CROSS_MARGIN_UNSUPPORTED` & `NET_RR_TOO_LOW` |
| **F06** | Governed lessons CRITICAL chặn vào lệnh với effect `BLOCK_ENTRY` | `pytest backend/tests/test_v10_3_lifecycle_and_invariants.py` | **PASS** | `evaluate_rules` trả về 1 blocker CRITICAL với effect `BLOCK_ENTRY` |
| **F07** | Mock Telegram format tin nhắn tiếng Việt chuẩn, escape Markdown ký tự đặc biệt | `pytest backend/tests/test_v10_3_lifecycle_and_invariants.py` | **PASS** | `format_telegram_message` sinh đúng nội dung XAUUSDT, `escape_markdown` chuẩn |
| **F08** | Khôi phục trạng thái cô lập: không trùng lặp số lệnh ngày, capital bảo toàn | `pytest backend/tests/test_v10_3_lifecycle_and_invariants.py` | **PASS** | `initial_equity = 1000.0`, `consecutive_losses = 0` |

### Nhóm P: Hiệu Năng & Độ Ổn Định Tài Nguyên (P01 - P02)
| ID | Nội dung kiểm tra | Lệnh chạy | Kết quả | Bằng chứng |
| :--- | :--- | :--- | :--- | :--- |
| **P01** | Burst 500 tick quote cập nhật store đơn điệu, không rò rỉ bộ nhớ hay REST call | `npm run test` | **PASS** | `store.getQuote('XAUUSDT')` lưu giá mới nhất chính xác |
| **P02** | Đăng ký & huỷ đăng ký 50 lần giải phóng sạch listeners, không rò rỉ bộ nhớ | `npm run test` | **PASS** | Unsubscribe 50 callback thành công, 0 listener tồn đọng |

---

## 5. Kết Quả Tổng Thể & Hạn Chế Còn Lại (Limitations)
- **Tổng số bài test:**
  - Frontend: 9 test files, **82 tests passed** (100%).
  - Backend: 25 test files, **325 tests passed** (100%).
  - TypeScript typecheck & Vite build: **Thành công 0 lỗi** (`built in 857ms`).
  - Linter: **oxlint 0 errors** (10 warnings liên quan đến code cũ).
- **Hạn chế kỹ thuật minh bạch:**
  1. *Mock Telegram:* Test F07 kiểm tra cấu trúc tin nhắn và markdown formatting trong môi trường cô lập, không gửi tin nhắn thực tế tới Telegram API nếu người dùng chưa điền Bot Token thật trong cài đặt.
  2. *Độ phân giải siêu nhỏ hoặc Zoom 200%:* Để đảm bảo tiêu chuẩn accessibility, khi thu phóng lên 200% hoặc màn hình có chiều cao dưới 600px, thanh cuộn nội bộ của sidebar sẽ tự động kích hoạt nhằm giúp người dùng không bị mất quyền truy cập bất kỳ nút bấm hoặc cảnh báo an toàn nào.

---

## 6. Hướng Dẫn Cập Nhật An Toàn Cho Người Dùng Đang Có Vị Thế
1. **Không khởi động lại runtime backend:** Bản sửa đổi này chỉ cập nhật UI frontend và routing thông báo phía client; không yêu cầu khởi động lại tiến trình Python backend hay service database.
2. **Cập nhật giao diện an toàn:** Người dùng chỉ cần tải lại trang trình duyệt (F5 hoặc Ctrl+R / Cmd+R). Toàn bộ trạng thái vị thế Paper đang mở, vốn ký quỹ và các hạn ngạch phiên sẽ được tải lại tự động từ backend mà không bị gián đoạn.
