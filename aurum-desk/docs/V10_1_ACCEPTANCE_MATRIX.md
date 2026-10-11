# V10.1 Acceptance Test Matrix

Tài liệu đặc tả ma trận kiểm thử chấp nhận tự động cho V10.1 (V10.1 Governed Lesson Decisions Across All Entry Paths).

## Danh Mục Phân Loại và Chỉ Số Bắt Buộc

| Nhóm | Số ID | Mô tả phạm vi |
| :--- | :--- | :--- |
| **A (Acceptance Scenarios)** | A01 – A16 (16) | Đánh giá quy tắc ở mọi đường vào lệnh (Manual Market, Manual Arm, Auto Arm, Pending Fill, NY Fallback). |
| **S (Semantic & Scope Rules)** | S01 – S13 (13) | Ba màu Xanh/Vàng/Đỏ, scope direction, mode, timeframe, session, và quarantine malformed scope. |
| **P (Policy & Feature Flags)** | P01 – P06 (6) | Server-authoritative policy, shadow mode, feature rollback, syntax vs behavior validation. |
| **X (Error Policy & Diagnostics)** | X01 – X08 (8) | Xóa silent pass (`except: pass`), fail-safe với diagnostic correlation ID khi lỗi DB/evaluator. |
| **U (UI, API & State Mutation)** | U01 – U08 (8) | Phân biệt route 404 vs lesson 404, set-enable mong muốn, revision locking 409, toast dedupe. |
| **T (Decision Tracing)** | T01 – T05 (5) | Tách biệt `arm_decision_snapshot` và `fill_decision_snapshot`, truy vết bất biến qua chu kỳ lệnh. |
| **R (Invariants & Regression)** | R01 – R08 (8) | Vị thế đang mở và exit monitor (TP/SL) không bị ảnh hưởng bởi lesson rules, giữ nguyên max1 open/armed. |
| **B (Browser Acceptance)** | B01 – B05 (5) | Kiểm thử luồng thực tế trên browser/E2E với UI và backend instance. |
| **Tổng Cộng** | **69 IDs** | Toàn bộ 69 ID phải được thực thi và PASS. |

---

## Chi Tiết 69 Kịch Bản Kiểm Thử

### A. Acceptance Scenarios (A01 – A16)
- **A01**: Manual market LONG, green advisory -> Khớp 1 fill hợp lệ, ghi nhận advisory trace, không siết chặt guard.
- **A02**: Manual market SHORT, yellow warning -> Fill thành công, cảnh báo ghi vào snapshot/trace nhưng không chặn lệnh.
- **A03**: Manual market active red matched -> Không fill lệnh, trả về lỗi 400 `LESSON_RULE_BLOCKED`, counters/quota không tăng.
- **A04**: Manual market red unmatched -> Fill baseline thành công khi điều kiện thị trường không vi phạm ngưỡng đỏ.
- **A05**: Manual Arm LONG active red matched -> Không tạo armed order mới, setup giữ trạng thái ban đầu, thông báo chặn rõ ràng.
- **A06**: Manual Arm SHORT rồi Fill -> Thứ tự khớp duy trì execution_mode là `MANUAL` và origin `MANUAL_WEB`, không bị coordinator biến thành `AUTO`.
- **A07**: Auto Arm matched red -> Strategy service không arm setup, ghi log và trace chẩn đoán, không pause Auto loop.
- **A08**: Auto yellow -> Ghi warning vào trace, tiếp tục Auto Arm bình thường mà không đòi hỏi xác nhận thủ công.
- **A09**: NY Fallback riêng scope -> Rule cho NY_QUOTA_PAPER chỉ áp dụng cho fallback, không can thiệp vào STANDARD_SMC.
- **A10**: LIMIT LONG Last touch, Ask chưa touch -> Không fill lệnh; chỉ khớp khi giá thực tế chạm Ask.
- **A11**: LIMIT SHORT Last touch, Bid chưa touch -> Không fill lệnh; chỉ khớp khi giá thực tế chạm Bid.
- **A12**: MARKET/STOP/LIMIT hai hướng qua shared final guard trước khi chuyển trạng thái atomic sang `paper_open`.
- **A13**: Rule/flag đổi giữa Arm và Fill -> Recheck version và policy tại thời điểm khớp, không dùng preview cũ.
- **A14**: Hai requests/sessions đồng thời -> Barrier/latch đảm bảo chỉ duy nhất 1 position mở và 1 lần tăng DayAudit.
- **A15**: Duplicate/idempotent requests -> Cùng idempotency_key trả về kết quả cũ, không tạo duplicate persistent traces.
- **A16**: Preview pass nhưng quote/risk/news thay đổi trước Fill -> Baseline recheck chặn fill an toàn.

### S. Semantic & Scope Rules (S01 – S13)
- **S01**: Rule INFO (Xanh) tuyệt đối không làm thay đổi entry/SL/TP/qty/leverage hoặc nới lỏng baseline guard.
- **S02**: Rule WARN (Vàng) không block lệnh ở cả manual market, manual arm và auto arm.
- **S03**: Rule BLOCK (Đỏ) hợp lệ, đã duyệt, đã bật và đúng hiệu lực thời gian sẽ chặn lệnh thực sự.
- **S04**: Rule ở trạng thái PENDING, REJECTED, ARCHIVED, DISABLED hoặc INVALID không được kích hoạt chặn.
- **S05**: Rule Đỏ thiếu structured predicate hợp lệ không thể tự động trở thành blocker thực thi.
- **S06**: Scope direction LONG/SHORT lọc chính xác; rule chỉ áp dụng cho hướng đã cấu hình.
- **S07**: Scope execution_mode MANUAL/AUTO lọc nghiêm ngặt; nếu context thiếu hoặc UNKNOWN thì không áp dụng wildcard ngầm.
- **S08**: Scope symbol và strategy_family áp dụng đúng cho từng cặp và mô hình chiến lược.
- **S09**: Scope timeframe (15M, 5M) và stage (BEFORE_ARM, BEFORE_FILL) được thực thi chuẩn xác.
- **S10**: Scope session tag ('ASIA', 'LONDON', 'NY') phân biệt rạch ròi với `session_instance_id`.
- **S11**: Xử lý ranh giới phiên NY DST và nửa đêm VN chuẩn xác thông qua FakeClock.
- **S12**: Malformed scope JSON bị cách ly (quarantine), không bị lỗi ép coi thành áp dụng cho ALL.
- **S13**: `effective_at` và `expiry_at` chặn future leak khi chạy historical replay.

### P. Policy & Feature Flags (P01 – P06)
- **P01**: Policy config (`lesson_advisory_enabled`, `lesson_entry_rules_enabled`, `lesson_shadow_mode`) được lưu server-authoritative và nhất quán qua restart.
- **P02**: Khi tắt `lesson_entry_rules_enabled`, các baseline guards (Risk, Quota, Day Limits) vẫn hoạt động 100% độc lập.
- **P03**: Ở Shadow mode, rule Đỏ ghi nhận `would_block = True` nhưng vẫn cho phép lệnh vào nếu baseline đạt.
- **P04**: So sánh A/B cùng fixture: Shadow mode cho phép fill vs Active mode chặn fill thực sự.
- **P05**: Client gửi payload không thể tự ý gắn cờ bypass guard hoặc giả mạo server policy flags.
- **P06**: Quy tắc chỉ có `SYNTAX_VALID` chưa đủ điều kiện behavior validation thì không tự động kích hoạt chặn.

### X. Error Policy & Diagnostics (X01 – X08)
- **X01**: Evaluator gặp lỗi ngoại lệ trước Manual Fill -> Không silent bypass; chặn lệnh an toàn kèm mã tương quan (correlation ID).
- **X02**: Evaluator gặp lỗi trước Auto Arm/Fill -> Thực thi chính sách an toàn, không làm crash toàn bộ engine.
- **X03**: Lỗi DB khi truy vấn rule -> Ghi diagnostic log, rollback transaction đúng boundary, không tạo partial state.
- **X04**: Metric bắt buộc của rule Đỏ bị thiếu (`strict_data`) -> Báo `LESSON_RULE_DATA_UNAVAILABLE`, không fake match.
- **X05**: Dữ liệu cảnh báo tùy chọn bị thiếu -> Thông báo trung thực, không suy diễn sai lệch.
- **X06**: Sự cố tạm thời của hệ thống bài học không xóa nhầm pending order hoặc setup đang chờ.
- **X07**: Quá trình đóng vị thế (TP, SL, Manual Close, Liquidation) luôn hoạt động ngay cả khi database bài học gặp sự cố.
- **X08**: Lỗi đánh giá bài học không làm bỏ qua các giới hạn an toàn tài khoản (Max 1 Open, Max 3 Daily Fills).

### U. UI, API & State Mutation (U01 – U08)
- **U01**: Duyệt bài học từ UI (`POST /approve`) -> Trả về trạng thái APPROVED nhưng chưa tự động kích hoạt (`enabled = False`).
- **U02**: Bật/tắt trạng thái mong muốn (`POST /set-enable`) -> Lưu đúng và bền vững qua reload.
- **U03**: Nhấp đúp (Double-click/retry) vào nút bật/tắt -> Idempotent, bảo vệ bằng revision guard, không bị đảo 2 lần.
- **U04**: Xung đột chỉnh sửa quy tắc (Stale Edit 409) -> Giữ nguyên bản nháp và thông báo rõ ràng cho người dùng.
- **U05**: Phân biệt mã lỗi 404: Route API không tồn tại vs Bài học không tồn tại.
- **U06**: Chẩn đoán lỗi proxy hoặc phiên bản backend cũ thông qua API capabilities.
- **U07**: Các yêu cầu GET ngầm phía sau không ghi đè lên dữ liệu vừa được người dùng lưu.
- **U08**: Tránh spam toast khi nhận chùm lỗi liên tiếp, hiển thị chi tiết hành động chính xác.

### T. Decision Tracing (T01 – T05)
- **T01**: Snapshot Arm (`arm_decision_snapshot`) và Fill (`fill_decision_snapshot`) được lưu riêng biệt, không ghi đè nhau.
- **T02**: Sửa hoặc lưu trữ bài học hiện tại không làm thay đổi các vết quyết định của các lệnh trong quá khứ.
- **T03**: Phân biệt rành mạch giữa các danh sách: retrieved, evaluated, matched, applied và shadow.
- **T04**: Các cảnh báo bài học được lưu vào Nhật Ký / UI Event, không chỉ xuất hiện ở file log.
- **T05**: Lệnh thủ công từ phiên bản cũ (legacy) có origin UNKNOWN không bị gán nhãn giả mạo thành AUTO.

### R. Invariants & Regression Safety (R01 – R08)
- **R01**: Vị thế đang mở khi thêm, sửa hoặc tắt rule Đỏ thì SL/TP/khối lượng/rủi ro hoàn toàn bất biến.
- **R02**: Quản lý đóng lệnh thủ công, chạm SL/TP, thanh lý và tính PnL không bị hồi quy.
- **R03**: Phục hồi trạng thái offline / reconciliation xác định đúng 1 lần đóng vị thế duy nhất.
- **R04**: Các giới hạn bất biến: Max 1 vị thế mở, Max 1 lệnh armed, Max 3 lệnh/ngày VN, Risk/News lockouts luôn đạt.
- **R05**: Migration database có tính lũy kế (additive), chạy nhiều lần không làm mất dữ liệu người dùng.
- **R06**: Môi trường kiểm thử có sentinel cách ly hoàn toàn, không đụng chạm đến runtime DB.
- **R07**: Tắt tính năng bài học (rollback) -> Hệ thống cơ sở hoạt động ổn định, các lệnh thoát vị thế không gián đoạn.
- **R08**: Cập nhật bài học không bao giờ tự ý dời TP ra xa hoặc nới rộng rủi ro ngoài kế hoạch.

### B. Browser Acceptance Scenarios (B01 – B05)
- **B01**: Trình duyệt vào lệnh thị trường gặp rule Đỏ bị chặn -> Tắt quy tắc -> Báo giá chuẩn cho phép khớp lệnh -> Chạm TP -> Xác nhận hiển thị trong Nhật Ký.
- **B02**: Trình duyệt kiểm tra Auto Arm gặp cảnh báo Vàng -> Arm thành công -> Khớp lệnh -> Chạm SL -> Snapshot quyết định bất biến.
- **B03**: Trình duyệt kiểm tra Shadow mode: rule Đỏ ghi nhận `would_block` -> Bật chế độ chặn -> Lệnh cùng điều kiện bị chặn thực sự.
- **B04**: Trình duyệt thực hiện duyệt bài học, bật/tắt, chỉnh sửa điều kiện, lưu trữ và tải lại trang -> Dữ liệu lưu bền vững đúng logic.
- **B05**: Khởi động lại ứng dụng khi đang có lệnh pending và open -> Cache và cờ cấu hình khôi phục chính xác, giám sát thoát vị thế ổn định.
