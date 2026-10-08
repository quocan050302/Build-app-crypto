# AURUM DESK V6.1 — BÁO CÁO KỸ THUẬT VÀ TÀI LIỆU KHÔI PHỤC VỊ THẾ & KIỂM THỬ ĐỘC LẬP
**Tài liệu tham chiếu:** `docs/v6_1_active_position_repair.md`  
**Ngày thực hiện:** 08/10/2026  
**Nhánh:** `feature/aurum-repair-smc-rr`  
**Phạm vi:** Sửa READY/Cross eligibility, bảo toàn vị thế đang mở, cách ly fixture kiểm thử, sửa hiển thị liquidation 0.00, phân tách checklist rủi ro và khôi phục kết quả sau offline.

---

## 1. NGUYÊN NHÂN GỐC RỄ (ROOT CAUSES: BẰNG CHỨNG THỰC TẾ VS GIẢ THUYẾT)

### 1.1. Lọt Fixture Test `watch-cross-15M` Vào Database Runtime
- **Bằng chứng:** Trong database runtime `backend/aurum_desk.db`, tồn tại bản ghi `watch-cross-15M` (SMC_V5, READY LONG, Entry 4120, SL 4110, TP 4160, leverage 5, margin_mode CROSS).
- **Nguyên nhân gốc rễ:** File kiểm thử `backend/tests/test_v6_error_and_arm_levels.py` trước đây trực tiếp import `SessionLocal, engine` từ `database.py`. Nếu chạy pytest khi biến môi trường `AURUM_DB_PATH` chưa được gán bên ngoài, database URL mặc định trỏ về `backend/aurum_desk.db`. File test này còn thực thi lệnh `db.query(models.WatchSetup).delete()` và insert fixture test thẳng vào production database!
- **Giải pháp triệt để:**
  - Thiết lập cơ chế tự phát hiện `is_pytest = "pytest" in sys.modules or os.getenv("TESTING") == "1"` trong `backend/database.py`. Nếu đang chạy test mà không có `AURUM_DB_PATH` riêng, tự động chuyển hướng sang file database tạm độc lập (`aurum_isolated_test_<pid>.db`).
  - Thêm `backend/tests/conftest.py` với session-scoped sentinel fixture `guard_runtime_database`, kiểm tra và khẳng định inode/hash của file production DB `backend/aurum_desk.db` không bao giờ bị xóa hay thay đổi bởi bất kỳ test suite nào.
  - Cung cấp fixture `isolated_db` (in-memory SQLite độc lập) và `client` (TestClient ghi đè `app.dependency_overrides[get_db]`).

### 1.2. Nút Arm Trong Danh Sách Upcoming Không Đồng Bộ Hợp Đồng Eligibility
- **Bằng chứng:** Sidebar kiểm tra `isCrossBlocked` để khóa nút Arm/Open, nhưng danh sách Upcoming Setup (`App.tsx`) chỉ kiểm tra `['READY', 'WAITING_PRICE', 'WAITING_RETRACE'].includes(s.state)` mà không kiểm tra chế độ Cross margin hay giới hạn 1 vị thế mở.
- **Nguyên nhân:** Thiếu một cơ chế đánh giá điều kiện tập trung (Single Source of Truth) chia sẻ giữa backend, Upcoming Setups, preview sidebar và thao tác Arm.
- **Giải pháp:** Xây dựng `backend/services/eligibility_service.py` với hàm `evaluate_setup_eligibility`. Cung cấp hợp đồng thống nhất: `can_arm`, `can_execute`, `reason_codes`, `block_reasons`, gắn trực tiếp vào payload GET `/api/v1/setups/upcoming`, endpoint GET `/api/v1/setups/eligibility/{setup_id}`, và re-check nguyên tử tại POST `/api/v1/setups/arm`.

### 1.3. Lỗi Strict Identity Khi Arm Setup (Card A Nhận Levels Dragged Từ Card B)
- **Bằng chứng:** Trong `handleArmWatchSetup`, điều kiện kiểm tra dữ liệu kéo trên chart sử dụng:
  `selectedIntent.setup_id === setupId || selectedIntent.source === 'WATCH_SETUP'`
- **Hệ quả:** Toán tử `||` làm cho mọi thao tác click Arm trên Card A đều lấy nhầm Entry/SL/TP kéo thủ công của Card B đang được chọn trên chart.
- **Giải pháp:** Sửa thành điều kiện nghiêm ngặt `selectedIntent.setup_id === setupId`. Nếu người dùng click Arm một card khác với setup đang chọn, payload gửi lên backend sẽ dùng levels gốc có thẩm quyền (authoritative) của chính card đó, tuyệt đối không bị lẫn lộn. Idempotency key chuyển sang định dạng ổn định theo phiên bản: `arm-${setupId}-v${revision}` thay vì `Date.now()`.

### 1.4. Hiển Thị Thanh Lý Ước Tính $0.00 Khi Giá Trị Là Null / 0
- **Bằng chứng:** Ảnh chụp hiển thị vị thế PAPER LONG 30x tại 4120.31 nhưng thanh lý ước tính hiển thị `$0.00`.
- **Nguyên nhân gốc rễ:** 
  1. Trong JavaScript: biểu thức `${s.estimated_liquidation?.toFixed(2) || '---'}` khi `estimated_liquidation === 0` sẽ thực hiện `0.toFixed(2)` thành chuỗi `"0.00"`. Chuỗi `"0.00"` mang giá trị truthy trong JS, do đó fallback `'---'` không bao giờ được kích hoạt, dẫn đến render `$0.00`.
  2. Plugin vẽ trên biểu đồ `RiskRewardPrimitive.ts` chưa có guard kiểm tra `estimatedLiquidation > 0`, có thể vẽ đường ngang tại mức giá 0.
- **Giải pháp:**
  - Frontend: Kiểm tra rõ ràng `typeof lp === 'number' && lp > 0 ? \`\$\${lp.toFixed(2)}\` : 'Chưa có ước tính hợp lệ'`.
  - Backend: Trong endpoint GET `/api/v1/positions/active`, nếu `estimated_liquidation` trong DB là null hoặc <= 0 nhưng vị thế là ISOLATED, backend tự động tính toán read-only fallback estimate từ chính thông số snapshot của lệnh (`actual_entry`, `quantity`, `leverage`), không ghi đè hay thay đổi bất kỳ trường dữ liệu lịch sử nào trong database.
  - Primitive Chart: Bảo vệ bằng guard `estimatedLiquidation > 0` trong toàn bộ quá trình tính toán tọa độ, axis label và render canvas.

### 1.5. Nhầm Lẫn Giữa Giám Sát Vị Thế Đang Mở Và Bộ Lọc Tín Hiệu Nến Mới
- **Bằng chứng:** Khi có vị thế mở (Net R:R 2.04), checklist bên dưới lại báo "Chưa có thiết lập R:R".
- **Nguyên nhân:** Checklist ở sidebar đọc từ `analysis.checklist`, đây là bộ lọc đánh giá xem *nến 15M hiện tại có hình thành tín hiệu vào lệnh mới hay không*. Do giá đang chạy và chưa có cấu trúc sweep/MSS mới trên nến hiện tại, `calc_res` của tín hiệu mới là `None`, dẫn đến hiển thị "Chưa có thiết lập R:R". Người dùng nhìn vào tưởng rằng vị thế mở của mình bị thiếu R:R.
- **Giải pháp:**
  - Tách hẳn một Card riêng màu xanh lục: **GIÁM SÁT RỦI RO VỊ THẾ HIỆN TẠI** khi `activePosition` tồn tại, hiển thị rõ Net R:R lúc khớp (1:2.04) đạt chuẩn `PASS >= 2.0` và trạng thái giám sát realtime của `ExitMonitor`.
  - Đổi tên tiêu đề checklist bên dưới thành: **BỘ LỌC TÍN HIỆU MỚI (SMC 15M)** kèm chú thích: "Đánh giá điều kiện cho cơ hội mới (Độc lập với vị thế đang chạy)". Nếu chưa có tín hiệu mới, dòng R:R ghi rõ "Chưa có setup mới hình thành trên nến hiện tại".

---

## 2. DỊCH VỤ ĐỐI SOÁT & CÁCH LY FIXTURE (`maintenance_service.py`)

- **Cơ chế:** Viết công cụ bảo trì có thể chạy qua CLI hoặc REST API:
  - `python backend/services/maintenance_service.py --dry-run` (Mặc định, chỉ đọc)
  - `python backend/services/maintenance_service.py --quarantine` (Thực thi cách ly an toàn)
- **Tiêu chí đối soát chính xác (Exact-Match Evidence):**
  - Khớp chính xác ID (`watch-cross-15M` hoặc `watch-test-15M`), chiến lược `SMC_V5`, hướng, đòn bẩy, và các mức giá provisional Entry/SL/TP.
- **Nguyên tắc an toàn tối thượng:**
  - Tuyệt đối không xóa dòng khỏi database để bảo toàn tính toàn vẹn quan hệ.
  - Kiểm tra liên kết: Nếu setup đang liên kết với bất kỳ `PaperOrder` nào ở trạng thái `paper_open` hoặc `armed`, lệnh cách ly sẽ BỎ QUA và cảnh báo, không được chạm vào.
  - Khi cách ly: Chuyển trạng thái sang `INVALIDATED` với lý do `TEST_FIXTURE_CONTAMINATION: Đối soát xác nhận fixture kiểm thử lọt vào dữ liệu, đã cách ly an toàn`.
  - Tính lũy đẳng (Idempotent): Chạy lần 2, 3 không sinh lỗi và báo cáo 0 bản ghi bị ảnh hưởng. Đã chạy thử nghiệm trên database và cách ly an toàn `watch-cross-15M`.

---

## 3. KHÔI PHỤC VỊ THẾ SAU OFFLINE (`position_recovery_service.py`)

### 3.1. Phạm vi và Nguyên tắc
- Khi máy bị sleep, mất điện, kill process hoặc đứt mạng, backend khi khởi động lại (`lifespan`) hoặc khi WebSocket feed kết nối lại sẽ kích hoạt đối soát.
- Chỉ đối soát các vị thế đã ở trạng thái `paper_open` trước khi mất kết nối.
- Không tự động kích hoạt (fill) các lệnh chờ `ARMED` trong thời gian offline thành lệnh mới; không mở lệnh từ các cơ hội bị bỏ lỡ.

### 3.2. Điểm kiểm soát (Checkpoint)
- Bổ sung các trường vào `models.PaperOrder` (hỗ trợ SQLite non-destructive migration):
  - `last_processed_market_timestamp`: Mốc thời gian thị trường cuối cùng đã được đánh giá.
  - `recovery_status`: Trạng thái khôi phục (`UP_TO_DATE`, `RECOVERING`, `RECOVERED`, `RECOVERY_INCOMPLETE`).
  - `recovery_confidence`: Mức độ tin cậy (`CONFIRMED` khi có bằng chứng rõ ràng, `ASSUMED_CONSERVATIVE` khi nến biến động mạnh chạm cả TP và SL).
  - `occurred_at`: Thời điểm sự kiện thoát lệnh xảy ra trên nến lịch sử.
  - `discovered_at`: Thời điểm mở lại máy và phát hiện sự kiện.
  - `resolved_through`: Mốc thời gian nến đã hoàn thành đối soát.

### 3.3. Thuật toán Replay Theo Thứ Tự Thời Gian (Chronological Replay)
1. Tải các nến đã đóng (`closed candles`) từ `lower_bound = last_processed_market_timestamp` đến thời điểm hiện tại.
2. Sắp xếp tăng dần theo thời gian (`timestamp.asc()`).
3. Bỏ qua phần nến xảy ra trước `opened_at` (không lấy extremes trước khi vào lệnh).
4. Đánh giá từng nến:
   - **LONG:** Chạm TP nếu `high >= take_profit`; Chạm SL nếu `low <= stop_loss`.
   - **SHORT:** Chạm TP nếu `low <= take_profit`; Chạm SL nếu `high >= stop_loss`.
   - **Thoát lệnh đầu tiên (First Exit):** Dừng ngay tại nến đầu tiên vi phạm TP hoặc SL.
   - **Xử lý nến lưỡng cực (Ambiguous Bar):** Nếu trong cùng một nến chạm cả TP và SL, áp dụng nguyên tắc bảo thủ của SMC Engine (`AMBIGUOUS_BAR_CONSERVATIVE_SL`), ghi nhận thoát tại SL với `confidence="ASSUMED_CONSERVATIVE"`. Không bao giờ tự nhận là thắng chắc chắn (confirmed win).
5. Thực thi đóng vị thế nguyên tử qua `TradeLifecycleService.execute_close`:
   - Ghi nhận `occurred_at` là thời điểm nến lịch sử.
   - Tính toán PnL, phí, slippage chính xác theo snapshot ban đầu của lệnh.
   - Cập nhật `DayAudit` cho đúng ngày theo giờ Việt Nam (UTC+7) của ngày xảy ra sự kiện (`occurred_at`), không dồn lỗ/lãi của ngày hôm qua vào ngày hôm nay.
   - Tạo thông báo Telegram Outbox có gắn badge `[ĐỐI SOÁT SAU OFFLINE]` với dedupe key chống gửi lặp.

---

## 4. KẾT QUẢ KIỂM THỬ ĐỘC LẬP (TEST RUN RESULTS)

### 4.1. Backend Pytest Suite
- **Lệnh chạy:**
  ```bash
  PYTHONPATH=backend ./backend/venv/bin/pytest backend/tests/ -v
  ```
- **Kết quả:** **84 / 84 tests PASSED (100%) trong 1.32 giây.**
  - `test_sentinel_runtime_db_isolated`: PASSED (Chứng minh runtime DB `aurum_desk.db` không bị ảnh hưởng).
  - `test_shared_eligibility_cross_blocked`: PASSED (Chặn hoàn toàn Cross margin).
  - `test_shared_eligibility_active_position_blocked`: PASSED (Chặn arm/open khi có vị thế mở).
  - `test_shared_eligibility_valid_setup`: PASSED (Đủ điều kiện Isolated arm thành công).
  - `test_fixture_maintenance_quarantine`: PASSED (Audit và cách ly fixture chuẩn xác, lũy đẳng).
  - `test_offline_position_recovery_tp_hit`: PASSED (Khôi phục lệnh chạm TP trong quá khứ).
  - `test_offline_position_recovery_sl_hit`: PASSED (Khôi phục lệnh chạm SL trong quá khứ).
  - `test_offline_position_recovery_ambiguous_bar`: PASSED (Xử lý bảo thủ nến chạm cả TP/SL).
  - `test_offline_position_recovery_no_exit_keeps_open`: PASSED (Vị thế còn nằm trong khoảng an toàn tiếp tục chạy).
  - `test_offline_position_recovery_idempotency`: PASSED (Không lặp lại thao tác đóng hay outbox).
  - `test_arm_setup_accepts_custom_chart_levels_and_rejects_insufficient_rr`: PASSED.
  - `test_arm_setup_cross_margin_unsupported`: PASSED.

### 4.2. Risk Lab 13 Scenarios
- **Lệnh chạy:**
  ```bash
  PYTHONPATH=backend ./backend/venv/bin/python -c "from lab.scenario_runner import ScenarioRunner; res = ScenarioRunner.run_all(); print([r.status for r in res])"
  ```
- **Kết quả:** **13 / 13 scenarios PASS (100%).**

### 4.3. Frontend Build & Vitest
- **Lệnh chạy:**
  ```bash
  npm run build --prefix frontend
  npm test --prefix frontend -- --run
  ```
- **Kết quả:**
  - TypeScript & Vite build: **0 lỗi, hoàn thành trong 864ms.**
  - Vitest: **16 / 16 tests PASSED trong 455ms.**

---

## 5. HƯỚNG DẪN TRIỂN KHAI AN TOÀN (ROLLOUT CHECKLIST KHI CÓ VỊ THẾ MỞ)

> [!IMPORTANT]
> **Hiện trạng:** Code đã được sửa chữa toàn diện và kiểm thử 100% đạt chuẩn trên môi trường cô lập. Tuy nhiên, backend runtime của ứng dụng đang theo dõi lệnh mở của người dùng. **Theo đúng cam kết an toàn, Antigravity KHÔNG tự ý kill/restart backend runtime khi còn vị thế mở.**

### Quy Trình Triển Khai Khi Lệnh Đóng (Hoặc Ngoài Giờ Giao Dịch):
1. **Kiểm tra trạng thái lệnh:** Đảm bảo không còn vị thế mở (`has_active_position == false`) hoặc lệnh đã chạm TP/SL tự nhiên.
2. **Sao lưu database (Consistent Backup):**
   ```bash
   sqlite3 backend/aurum_desk.db ".backup 'backend/aurum_desk.db.backup_rollout'"
   ```
3. **Chạy công cụ bảo trì đối soát fixture:**
   ```bash
   python backend/services/maintenance_service.py --quarantine
   ```
4. **Khởi động lại backend server:**
   - Dừng tiến trình cũ (PID 99409).
   - Khởi động lại:
     ```bash
     export PYTHONPATH=backend
     ./backend/venv/bin/python -m uvicorn main:app --port 8000
     ```
5. **Kiểm tra Frontend:**
   - Frontend đã tự động cập nhật qua Vite HMR.
   - Xác nhận:
     - Thẻ giám sát vị thế và thẻ bộ lọc tín hiệu mới được phân tách rõ ràng.
     - Thanh lý hiển thị số dương hoặc `"Chưa có ước tính hợp lệ"`, không còn `$0.00`.
     - Nút Arm trong danh sách Upcoming setup hiển thị lý do chặn chính xác khi chọn Cross margin hoặc khi đang có lệnh mở.
