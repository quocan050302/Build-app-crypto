# Aurum Desk V10.1 — Lesson Entry Repair & Acceptance Documentation

## 1. Giới Thiệu & Mục Tiêu Nghiệm Thu V10.1
Aurum Desk V10.1 tập trung giải quyết triệt để và toàn diện bài toán **quản trị bài học kinh nghiệm (Lesson Rules Governance)** trên toàn bộ các đường vào lệnh (Entry Paths).

### Nguyên Tắc Bất Biến (Invariants)
1. **Độc lập với Vị thế đang mở (Open Position Invariant):**
   - Tuyệt đối không can thiệp, không sửa đổi giá (Entry, Stop Loss, Take Profit), khối lượng hay tự ý đóng các vị thế đang mở vì bất kỳ quy tắc bài học nào.
   - Các lệnh đóng vị thế (`execute_close`), kích hoạt TP/SL, hay dừng lỗ khẩn cấp hoạt động độc lập và không phụ thuộc vào trạng thái lỗi của engine bài học.
2. **Độc lập với Baseline Risk Guards:**
   - Các chốt chặn rủi ro cơ bản: Tối đa 1 vị thế mở (`MAX_OPEN_POSITIONS = 1`), Tối đa 1 lệnh chờ khớp (`MAX_ARMED_ORDERS = 1`), Tối đa 3 lệnh khớp/ngày (`MAX_DAILY_ENTRIES = 3`), giới hạn chuỗi lỗ (`consecutive_losses < 2`), khoảng thời gian tin tức đỏ (News Blackout Window) và tỷ lệ Net R:R tối thiểu $\ge 2.0$ là các điều kiện tiên quyết, độc lập với các cờ bài học.
3. **Quy Tắc Đánh Giá Bài Học:**
   - **Xanh (INFO / ANNOTATE):** Chỉ đóng vai trò tham khảo; ghi nhận nhật ký, không thay đổi thông số lệnh, không chặn lệnh.
   - **Vàng (WARNING / WARN_ENTRY):** Đưa ra cảnh báo trung thực trên giao diện và nhật ký lệnh; không chặn vào lệnh (cả Manual lẫn Auto).
   - **Đỏ (CRITICAL / BLOCK_ENTRY):** Chỉ chặn entry khi được cấu hình ĐÃ DUYỆT (`APPROVED`), ĐANG BẬT (`enabled=True`), ĐÃ KIỂM THỬ HÀNH VI (`VALID`), CÒN HIỆU LỰC (`effective_at <= now < expiry_at`), KHÔNG BỊ ARCHIVED/REJECTED, và khớp chính xác toàn bộ phạm vi (Scope).
4. **Chế độ Giám sát ngầm (Shadow Mode):**
   - Khi bật `lesson_shadow_mode = True`, các quy tắc Đỏ chỉ đánh dấu `would_block = True` trên snapshot phân tích mà không chặn luồng vào lệnh thực tế.

---

## 2. Phân Tích Nguyên Nhân Gốc (Root Cause Analysis - Findings A đến J)

| Mã | Phát Hiện (Finding) | Nguyên Nhân Cũ (Before) | Giải Pháp Sửa Đổi V10.1 (After) |
|---|---|---|---|
| **A** | Thiếu bảng chính sách bài học có thẩm quyền và phiên bản CAS | Cờ feature flags nằm rải rác hoặc đọc trực tiếp từ client/env; không có kiểm soát phiên bản khi cập nhật đồng thời. | Tạo bảng `SystemConfig` lưu trữ `lesson_policy_version`, `lesson_advisory_enabled`, `lesson_entry_rules_enabled`, `lesson_shadow_mode`, `plan_adjustment_enabled` kèm kiểm tra khóa lạc quan (CAS) `expected_version`. |
| **B** | Lỗ hổng giả mạo cờ từ Client (Spoofing) | Client có thể gửi `lesson_shadow_mode` hay `bypass_lessons` qua payload API để né chốt chặn. | Loại bỏ quyền kiểm soát cờ từ client. Server là nguồn thẩm quyền duy nhất tải cờ từ `LessonPolicyService.get_policy(db)`. |
| **C** | Thiếu endpoint chuyên dụng cho toggle bật/tắt | API toggle dùng method PUT chung dễ ghi đè mất nháp hoặc race condition khi double-click. | Thêm endpoint `POST /api/v1/lessons/{id}/set-enable` truyền trạng thái mong muốn (`desired_state`) và kiểm tra `expected_revision`. |
| **D** | Lệnh thị trường thủ công (Manual Market) bỏ qua đánh giá bài học | `PaperBroker.execute_market_order` tính R:R xong mở thẳng vị thế qua `TradeLifecycleService.execute_fill` mà không chạy rule engine. | Tích hợp `EntryDecisionService.evaluate_entry_rules(stage='BEFORE_FILL')` ngay trước khi mở lệnh. Ghi nhận snapshot và ném lỗi `LESSON_RULE_BLOCKED` nếu vi phạm. |
| **E** | Manual Arm thiếu ngữ cảnh chuẩn hóa và kiểm soát cờ | `POST /api/v1/setups/arm/{id}` không ghi nhận snapshot quyết định arm và không truyền `execution_mode="MANUAL"`. | Thiết lập canonical context (`execution_mode="MANUAL"`, `origin="MANUAL_WEB"`), kiểm tra server policy, lưu `arm_decision_snapshot`. |
| **F** | Auto Arm thiếu execution_mode và snapshot arm | `StrategyService._auto_arm_candidate` tạo `PaperOrder` mà không lưu vết bài học ở giai đoạn `BEFORE_ARM`. | Bổ sung đánh giá `BEFORE_ARM` trong `_auto_arm_candidate`, thiết lập `execution_mode="AUTO"`, `origin="AUTO_STRATEGY"`, lưu snapshot. |
| **G** | Pending Fill ghi đè vết quyết định arm | `ExecutionCoordinator.evaluate_orders_sync` dùng chung trường vết và bắt ngoại lệ im lặng (`except Exception: pass`). | Phân tách rạch ròi `arm_decision_snapshot` và `fill_decision_snapshot`. Loại bỏ `except Exception: pass`, áp dụng chính sách fail-safe có chẩn đoán. |
| **H** | NY Fallback Arm thiếu cô lập scope | Setup NY Fallback bị đánh giá lẫn với quy tắc của `STANDARD_SMC`. | Cô lập rõ ràng `strategy_family="NY_QUOTA_PAPER"` trong context scope; giữ nguyên hạn mức rủi ro 0.10%. |
| **I** | Lọc Scope trong `retrieve_active_rules` có lỗ hổng | Thiếu lọc timeframe/stage; xử lý execution mode bằng wildcard ngầm định; JSON scope lỗi áp dụng cho tất cả. | Chuẩn hóa session tags (`ASIA`, `LONDON`, `NY`); cách ly JSON lỗi vào diện cách ly (Quarantine); lọc nghiêm ngặt mode `MANUAL` / `AUTO` / `UNKNOWN`. |
| **J** | Quy tắc kích hoạt chỉ bằng cú pháp (Syntax only) | Quy tắc chỉ kiểm tra định dạng JSON sơ bộ đã cho phép kích hoạt, dễ gây crash lúc runtime. | Bổ sung `validate_rule_behavior` chạy thử với các fixture mẫu tất định trước khi công nhận `validation_status="VALID"`. |

---

## 3. Kiến Trúc & Luồng Dữ Liệu (Data Flow Architecture)

### 3.1 Cấu Trúc Bảng Dữ Liệu Bổ Sung (Additive Migrations)
Trong bảng `paper_orders`, 4 cột mới được bổ sung an toàn mà không làm mất dữ liệu hiện tại:
- `origin`: Nguồn phát sinh lệnh (`MANUAL_WEB`, `AUTO_STRATEGY`, `NY_FALLBACK`, `UNKNOWN`).
- `execution_mode`: Chế độ thực thi (`MANUAL`, `AUTO`, `UNKNOWN`).
- `arm_decision_snapshot`: Chuỗi JSON lưu vết quyết định giai đoạn `BEFORE_ARM`.
- `fill_decision_snapshot`: Chuỗi JSON lưu vết quyết định giai đoạn `BEFORE_FILL`.
- `lessons_retrieved`: Mảng JSON ghi nhận lịch sử quyết định theo thứ tự thời gian.

### 3.2 Sơ Đồ Khớp Lệnh và Điểm Đánh Giá Bài Học

```
Manual Market:
[POST /orders/paper] -> [Baseline Risk Checks] -> [EntryDecisionService.evaluate(BEFORE_FILL)] -> [execute_fill] -> Open Position

Manual Arm:
[POST /setups/arm]   -> [Eligibility & Policy] -> [EntryDecisionService.evaluate(BEFORE_ARM)]  -> [State: armed]
                                                                                                        |
                                                                                                        v
                                                                             [ExecutionCoordinator (BEFORE_FILL)] -> [execute_fill] -> Open Position

Auto Arm:
[Strategy Signal]    -> [Trading Policy Quota] -> [EntryDecisionService.evaluate(BEFORE_ARM)]  -> [State: armed]
                                                                                                        |
                                                                                                        v
                                                                             [ExecutionCoordinator (BEFORE_FILL)] -> [execute_fill] -> Open Position
```

---

## 4. Báo Cáo Kiểm Thử Tự Động Toàn Diện
Tất cả 69 kịch bản kiểm thử quy định trong `backend/lab/v10_1_manifest.json` đã được tự động hóa hoàn toàn trong `tests/test_v10_1_acceptance.py` và runner `backend/lab/v10_1_acceptance_runner.py`.

### Kết quả chạy kiểm thử:
```bash
PYTHONPATH=. venv/bin/python -m lab.v10_1_acceptance_runner --suite all --seed 42 --report-dir docs/v10_1_artifacts
```
- **Tổng số ca kiểm thử:** 69 / 69
- **Kết quả:** 69 PASSED (100.0%)
- **Hồi quy Backend toàn bộ:** 260 / 260 PASSED (100.0%)
- **Hồi quy Frontend toàn bộ:** 43 / 43 PASSED (7/7 test files)
- **Frontend Production Build:** PASSED (`dist/` bundle created cleanly)
- **Mã thoát (Exit Code):** `0`

Chi tiết kết quả lưu trữ tại:
- [Báo cáo chi tiết định dạng Markdown](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/docs/V10_1_TEST_REPORT.md)
- [Bản ghi kết quả JSON máy đọc](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/docs/v10_1_artifacts/v10_1_results.json)
- [Ma trận nghiệm thu 69 ID](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/docs/V10_1_ACCEPTANCE_MATRIX.md)
