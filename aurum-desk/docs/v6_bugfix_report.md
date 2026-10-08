# BÁO CÁO SỬA LỖI & NGHIỆM THU — AURUM DESK V6
**Hệ Thống:** AURUM DESK V6 (XAUUSDT Bitget Classic USDT-M Perpetual Paper Trading)  
**Nhánh:** `feature/aurum-repair-smc-rr`  
**Ngày Thực Hiện:** 08/10/2026  
**Trạng Thái:** HOÀN THÀNH TOÀN DIỆN (13/13 Kịch bản Lab PASS, Risk Settings Async State & Capability Hoàn Chỉnh, 72/72 Pytest Tests PASS, Frontend Build & Vitest PASS)

---

## 1. TỔNG QUAN HAI NHÓM LỖI & TRƯỚC/SAU SỬA ĐỔI (BEFORE / AFTER)

### Nhóm A: Form Đòn Bẩy (Leverage), Chế Độ Ký Quỹ Cross & State Async
* **Trước khi sửa (Before):**
  1. `App.tsx`: `refreshAccountAndHealth` đóng (close over) giá trị `isSettingsDirty = false` từ lần render đầu tiên do dependency array chỉ có `[timeframe]`. Kết quả: Mỗi chu kỳ polling 5 giây, response trả về từ backend ghi đè lên giá trị slider người dùng đang kéo.
  2. Bất đồng bộ đua dữ liệu (Async race condition): Các request polling được phát đi trước khi người dùng kéo slider nếu trả về muộn (in-flight response) vẫn ghi đè lên bản nháp (draft).
  3. Thanh trượt slider bị hardcode `max={50}` và nhãn `50x (Max Cap)`, trong khi metadata Bitget USDT-M perpetual thực tế cho phép tới `100x`.
  4. Lời chú thích rủi ro bị hardcode giá trị `$2.50` thay vì tính động theo công thức `Equity × risk_pct / 100`.
  5. Chế độ **CROSS** có thể click chọn được nhưng không có cảnh báo năng lực (capability notice). Khi vào lệnh, hệ thống bị calculator chặn với lỗi `CROSS_MARGIN_UNSUPPORTED` mà người dùng không được thông báo trước minh bạch.
  6. Backend `risk_settings_service` kiểm tra `expected_config_version < current` thay vì kiểm tra nghiêm ngặt `expected_config_version == current` (CAS - Compare-And-Swap), tiềm ẩn rủi ro lost-update khi có 2 phiên chỉnh sửa cùng lúc.

* **Sau khi sửa (After):**
  1. Tách biệt hoàn toàn `savedRiskSettings` (cấu hình chính thức đã lưu trên DB/backend, dùng cho lệnh thực tế) và `draftRiskSettings` (bản nháp người dùng đang kéo/chỉnh).
  2. Sử dụng `isDirtyRef`, `draftRevisionRef`, `inFlightSaveRevisionRef`, và `pollSequenceRef` để kiểm soát race condition. Bất kỳ polling response nào xuất phát trước thao tác kéo hoặc trả về muộn đều bị hủy bỏ, tuyệt đối không ghi đè draft đang chỉnh.
  3. Giới hạn slider `min` và `max` (1x - 100x) được lấy động từ Bitget instrument metadata (`/api/v1/instrument/metadata`), đi kèm các nút preset nhanh `1x, 5x, 20x, 50x, 100x` và ô nhập số trực tiếp.
  4. Lời chú thích và tính toán rủi ro USD được cập nhật động: `${draftRiskSettings.risk_pct}% vốn = $${((currentEquity * draftRiskSettings.risk_pct) / 100).toFixed(2)}`.
  5. Thiết lập **Hợp Đồng Năng Lực (Capability Contract)** rõ ràng cho Cross: Khi người dùng chọn CROSS, giao diện hiển thị ngay banner cảnh báo màu đỏ/cam: `CẢNH BÁO NĂNG LỰC: CHƯA HỖ TRỢ THỰC THI CROSS TRONG PAPER TRADING (CROSS_MARGIN_UNSUPPORTED)`. Lựa chọn này được lưu lại cho mục đích tham khảo nhưng lệnh thực tế sẽ bị chặn minh bạch kèm lý do rõ ràng.
  6. Backend áp dụng chuẩn strict CAS: `if update.expected_config_version != current_version: raise STALE_EDIT (HTTP 409 Conflict)`. Hỗ trợ partial update nguyên tử mà không bị reset các trường không truyền.
  7. Đồng bộ hóa pending orders và `WatchSetup` khi `config_version` thay đổi thông qua `ExecutionCoordinator`, tính toán lại quantity, margin, liquidation price và net R:R.

---

### Nhóm B: Isolated Risk Lab 8/13 → 13/13 Kịch Bản Hoàn Chỉnh
* **Trước khi sửa (Before):**
  - Chỉ 8/13 kịch bản đạt kết quả PASS.
  - Bị lỗi runtime do `execution_coordinator.py` gọi hàm không tồn tại `crud.get_account_status(db)`.
  - Thiếu context isolation cho `InstrumentProvider`, dẫn tới việc các run kịch bản có thể phụ thuộc lẫn nhau hoặc phụ thuộc vào cache live toàn cục.
  - Các kịch bản lệnh Full Cycle LONG/SHORT, Order Semantics, Spread Spike R:R, và Concurrent Fills bị FAIL.

* **Sau khi sửa (After):**
  - Đã khắc phục toàn bộ nguyên nhân gốc rễ, đưa **13/13 kịch bản đạt PASS 100%**.
  - Bổ sung `crud.get_account_status(db, clock=c)` với clock injection chuẩn xác.
  - Bổ sung `InstrumentProvider.freeze(metadata)` qua `contextvars` đảm bảo môi trường Lab hoàn toàn cô lập, không ảnh hưởng cache của app.
  - Mỗi kịch bản chạy trên SQLite `:memory:` riêng biệt, đóng kết nối trong khối `finally`, không chạm vào DB thật hay gửi Telegram thật.

---

## 2. CHI TIẾT 5 FAILING SCENARIOS TRONG RISK LAB & GIẢI PHÁP SỬA CHỮA

| Scenario ID | Tên Kịch Bản | Kết Quả Ban Đầu (First Failure) | Nguyên Nhân Gốc Rễ (Root Cause) | Giải Pháp Sửa Chữa (Fix Applied) |
|---|---|---|---|---|
| **`scenario_1_long_full_cycle`** | 1. LONG Full Cycle | FAIL tại Bước 1 (Arm Order): Order không thể tính toán Sizing / NameError `INSTRUMENT_METADATA` | File `domain_calculator.py` bị thiếu import / tham chiếu biến toàn cục `INSTRUMENT_METADATA`, khiến `calculate_risk_reward` văng lỗi runtime khi tính quantity/margin cho lệnh Long. | Sửa `domain_calculator.py`: Lấy metadata động qua `instrument_provider.get_metadata_sync("XAUUSDT")`. Cập nhật Scenario 1 seed order qua calculator chuẩn. |
| **`scenario_2_short_full_cycle`** | 2. SHORT Full Cycle | FAIL tại Bước 1 (Arm Order): Lỗi tương tự Scenario 1 do `calculate_risk_reward` bị crash | Giống Scenario 1, lệnh Short bị crash do thiếu metadata khi tính toán liquidation price và sizing. | Sử dụng calculator chuẩn với metadata động, kiểm tra chu trình SHORT: Armed → Triggered tại Bid - Slippage → Đóng tại TP (Ask <= TP) với Net PnL dương. |
| **`scenario_6_order_types_market_limit_stop`** | 6. Order Types Semantics (MARKET, LIMIT, STOP) | FAIL tại Bước 3: Handler cũ chỉ có BUY LIMIT và một unknown type, thiếu hoàn toàn MARKET, SELL LIMIT, BUY STOP, SELL STOP | Kịch bản chưa cài đặt đủ các loại lệnh theo tên gọi; ngoài ra giữa các sub-tests trong cùng một kịch bản bị vướng giới hạn `cooldown_remaining_sec` và `fills_count == 3` từ sub-test trước. | Mở rộng handler đầy đủ 10 bước kiểm tra: MARKET (LONG/SHORT), BUY LIMIT (met/unmet), SELL LIMIT (met/unmet), BUY STOP (met/unmet), SELL STOP (met/unmet), và rejected type. Reset cooldown/quota cô lập giữa các sub-tests. |
| **`scenario_9_spread_spike_rr_rejection`** | 9. Spread Spike làm hỏng Net R:R (< 2.0) | FAIL tại Bước 1: Baseline order ban đầu bị thiết lập với Net R:R = 1.48 (< 2.0), không đạt điều kiện cơ sở | Fixture của kịch bản sử dụng mức giá entry 2650, SL 2640, TP 2670 có Net R:R < 2.0 ngay từ đầu, vi phạm quy tắc invariant tối thiểu 2.0 trước khi shock xảy ra. | Điều chỉnh fixture baseline chuẩn: Entry 2650, SL 2645, TP 2670 đạt Net R:R = 2.43 (>= 2.0). Sau đó áp dụng shock trượt giá và spread giãn mạnh, khiến Net R:R tụt xuống 1.41 (< 2.0), khẳng định hệ thống từ chối lệnh với mã `NET_RR_TOO_LOW` và không tăng số lệnh trong ngày. |
| **`scenario_12_concurrent_fills_max_one_pos`** | 12. Concurrency Safety (Max 1 Open Pos) | FAIL / Thiếu kiểm thử đua luồng thực sự: Handler cũ chạy tuần tự trong 1 session duy nhất | Chưa kiểm tra được race condition giữa 2 database sessions độc lập cạnh tranh mở vị thế cùng một thời điểm. | Nâng cấp kịch bản 3 bước: Bước 1-2 kiểm tra invariant tuần tự; Bước 3 tạo 2 Session SQLAlchemy riêng biệt cùng truy vấn và cố gắng khớp 2 lệnh cạnh tranh. Đảm bảo chỉ duy nhất 1 lệnh được phép mở (`paper_open`), lệnh còn lại bị từ chối, duy trì tổng vị thế mở = 1. |

---

## 3. BẢNG KẾT QUẢ ĐẠT ĐƯỢC 13/13 KỊCH BẢN KIỂM THỬ XÁC ĐỊNH

Tất cả 13 kịch bản đã được chạy và xác nhận thành công qua lệnh:
`PYTHONPATH=backend ./backend/venv/bin/python -c "from lab.scenario_runner import ScenarioRunner; results = ScenarioRunner.run_all(); print('\n'.join([f'{r.scenario_id}: {r.status} (steps: {len(r.steps)})' for r in results])); assert all(r.status == 'PASS' for r in results)"`

| STT | Kịch Bản (Scenario ID) | Tên Kịch Bản & Mô Tả | Số Bước Kiểm Tra | Trạng Thái |
|:---:|---|---|:---:|:---:|
| 1 | `scenario_1_long_full_cycle` | 1. LONG Full Cycle: Armed → Fill tại Ask+Slippage → TP hit tại Bid | 3 bước | **PASS** |
| 2 | `scenario_2_short_full_cycle` | 2. SHORT Full Cycle: Armed → Fill tại Bid-Slippage → TP hit tại Ask | 3 bước | **PASS** |
| 3 | `scenario_3_spread_executable_sides` | 3. Spread Executable Sides: Long dùng Ask, Short dùng Bid | 2 bước | **PASS** |
| 4 | `scenario_4_near_entry_hysteresis` | 4. Near-Entry Hysteresis: Cảnh báo vùng vào lệnh và chống spam alert | 2 bước | **PASS** |
| 5 | `scenario_5_setup_selection_conflict` | 5. Setup Conflict Guard: Chống xung đột giữa setup thủ công và auto | 2 bước | **PASS** |
| 6 | `scenario_6_order_types_market_limit_stop` | 6. Order Types Semantics: MARKET, BUY/SELL LIMIT, BUY/SELL STOP hai chiều | 10 bước | **PASS** |
| 7 | `scenario_7_stale_malformed_quote_rejection` | 7. Quote Sanity & Freshness: Từ chối giá trễ >15s, NaN, âm, đảo spread | 2 bước | **PASS** |
| 8 | `scenario_8_news_blackout_and_expiry` | 8. News Blackout & Expiry Guard: Chặn fill khi có tin High Impact và hết hạn lệnh | 2 bước | **PASS** |
| 9 | `scenario_9_spread_spike_rr_rejection` | 9. Spread Spike làm hỏng Net R:R (< 2.0): Từ chối an toàn khi R:R sau phí < 2.0 | 3 bước | **PASS** |
| 10 | `scenario_10_daily_guards_consecutive_losses` | 10. Daily Guards: Khóa giao dịch sau 2 lệnh lỗ liên tiếp, tối đa 3 lệnh/ngày | 1 bước | **PASS** |
| 11 | `scenario_11_intrabar_opened_at_guard` | 11. Intrabar Opened_at Guard: Không tính râu nến trước thời điểm mở lệnh | 2 bước | **PASS** |
| 12 | `scenario_12_concurrent_fills_max_one_pos` | 12. Concurrency Safety: 2 Database Sessions cạnh tranh, bảo toàn max 1 vị thế | 3 bước | **PASS** |
| 13 | `scenario_13_outbox_persistence_isolated` | 13. Notification Outbox Isolation: Lưu sự kiện outbox mà không gửi Telegram thật | 1 bước | **PASS** |

---

## 4. BẢO TOÀN CÁC LUỒNG NGHIỆP VỤ HIỆN TẠI (REGRESSION VERIFICATION)

Các luồng hệ thống đã được kiểm chứng bằng 72/72 pytest tests và test thực tế trên UI:
1. **Phân tích đa khung SMC/ICT**: Đa khung D, 4H, 1H, 15M, 5M, 1M, Order Blocks, Liquidity Sweeps, CHOCH được duy trì nguyên vẹn.
2. **Setup Selection Conflict**: Setup SHORT đã chọn bởi người dùng không bị ghi đè bởi global bias LONG.
3. **Thước đo R:R trên biểu đồ**: Thao tác kéo thả các mốc Entry, Stop Loss, Take Profit trên Lightweight Charts giữ nguyên geometry chính xác.
4. **Lifecycle & Reconciliation V5.2**: Quá trình ARMED → FILLED → CLOSED/CANCELLED/REJECTED hoạt động trơn tru.
5. **Tin tức & Timezone**: Import tin tức kinh tế, chuyển đổi múi giờ UTC+7 và cửa sổ Blackout tin đỏ hoạt động đồng bộ.
6. **Quote Validator & Net R:R >= 2.0**: Kiểm tra bid/ask hợp lệ, trừ phí taker 0.04%-0.06% và trượt giá, bắt buộc net R:R >= 2.0.
7. **Bảo vệ tài khoản**: Tối đa 1 vị thế mở đồng thời, tối đa 3 lệnh khớp mỗi ngày theo múi giờ UTC+7, giới hạn lỗ ngày 1.5% ($15).
8. **Telegram Notifications**: Deduplication tin nhắn near-entry, outbox queueing và retry được cách ly hoàn toàn khỏi Lab.

---

## 5. KẾT QUẢ KIỂM THỬ THỰC TẾ (COMMANDS & TEST RESULTS)

### Backend Pytest Suite:
```bash
PYTHONPATH=backend ./backend/venv/bin/pytest backend/tests/
# Kết quả: 72 passed, 1 warning in 1.46s (100% PASS)
```

### Lab Scenarios Suite:
```bash
PYTHONPATH=backend ./backend/venv/bin/python -c "from lab.scenario_runner import ScenarioRunner; results = ScenarioRunner.run_all(); print('\n'.join([f'{r.scenario_id}: {r.status} (steps: {len(r.steps)})' for r in results])); assert all(r.status == 'PASS' for r in results)"
# Kết quả: 13/13 PASS
```

### Frontend Vitest Suite:
```bash
npm test
# Kết quả: 2 test files passed, 7/7 tests passed (100% PASS)
```

### Frontend Typecheck & Build:
```bash
npm run build
# Kết quả: tsc -b && vite build thành công không lỗi (0 error, build time 626ms)
```

### Frontend Linter:
```bash
npm run lint
# Kết quả: oxlint chạy trên 13 files, 0 errors.
```

### Browser End-to-End Smoke Test:
Browser subagent đã thực hiện kiểm tra thực tế trên `http://localhost:5173`:
- Tab `Quản Trị Vốn & Ký Quỹ`: Click preset `50x` → hiển thị banner `Draft v2 -> v3` kèm nút `Hủy Bản Nháp`.
- Click `CROSS` → hiển thị `CẢNH BÁO NĂNG LỰC: CHƯA HỖ TRỢ THỰC THI CROSS` và huy hiệu `Chặn Execution`.
- Click `Lưu Cài Đặt (Draft)` → lưu thành công phiên bản `Config: v3`, thông báo toast xanh và đồng bộ giao diện.

---

## 6. HẠN CHẾ CÒN LẠI VỀ CHẾ ĐỘ CROSS (CROSS MARGIN CAVEAT)
- Trong phiên bản V6, cơ chế **Cross Margin** được hỗ trợ ở cấp độ **Tùy Chọn Cấu Hình & Giao Diện (Preference & Configuration Contract)**, nhưng tính năng thực thi lệnh (execution) với Cross margin trong môi trường Bitget Paper Trading được chủ động chặn với mã lỗi `CROSS_MARGIN_UNSUPPORTED`.
- **Lý do kỹ thuật**: Chế độ Cross yêu cầu mô hình tính toán thanh lý trên toàn bộ số dư khả dụng của tài khoản (Account-wide Liquidation Model), chia sẻ rủi ro giữa nhiều tài sản khác nhau. Để đảm bảo an toàn tuyệt đối cho người dùng trong môi trường Paper trading hiện tại, hệ thống khuyến nghị và ưu tiên bảo vệ rủi ro theo chế độ **ISOLATED** (cô lập rủi ro trên từng vị thế).
