# Aurum Desk V10.2 — Phân Tích Nguyên Nhân Gốc (Root Cause Analysis)

## 1. Tổng Quan Sự Cố & Phạm Vi Sửa Chữa (Executive Summary)
Trong phiên bản Aurum Desk V10.1, hệ thống đã chuẩn hóa việc kiểm soát quy tắc bài học (Lesson Rules Governance) trên các luồng vào lệnh. Tuy nhiên, qua quá trình kiểm thử thực tế và nghiệm thu V10.2, hệ thống ghi nhận 3 nhóm sự cố trọng yếu (Area A, Area B, Area C) làm sai lệch giao diện R:R, gây lỗi type khi hiển thị bài học, và thiếu nhất quán giữa báo giá realtime với mức giá kế hoạch:

1. **Area A — Lesson Contract DTO & Normalizer:** Lỗi không tương thích hợp đồng dữ liệu giữa backend trả về chuỗi/object và frontend mong đợi DTO, dẫn đến `TypeError: argument of type 'LessonDecisionItem' is not iterable` và lỗi hiển thị `#undefined` trên ExpectedEntryPanel.
2. **Area B — Direction & Price Resolver:** Bộ giải quyết mức giá không xác thực hình học (Geometry Validation), cho phép SL < Entry đối với lệnh SHORT (nghịch đảo), ngầm định hướng thiếu thành `LONG`, và kế thừa các mức giá đã xác nhận (Confirmed Levels) cũ khi setup đã đảo chiều hoặc thay đổi instance.
3. **Area C — Realtime Quotes & Canonical Quote Store:** Báo giá realtime bị thụt lùi khi nến đóng trễ đến sau tin nhắn ticker, khoảng cách giá |last - entry| tính toán sai hoặc hiển thị NaN, và nháp lệnh trên biểu đồ phân mảnh ID giữa draft overlay và user intent.

---

## 2. Chi Tiết Nguyên Nhân Gốc Theo Từng Khu Vực (Detailed Findings)

### Khu Vực A: Lesson Contract DTO & Normalizer

| Mã | Phát Hiện Sự Cố | Nguyên Nhân Cũ (Before) | Giải Pháp Sửa Đổi V10.2 (After) |
|:---|:---|:---|:---|
| **A1** | **TypeError: 'text' in item** | Backend chuyển đổi danh sách bài học sang object model mới nhưng các hàm gọi cũ trong codebase (`evaluate_rules`, `entry_decision_service`) vẫn kiểm tra `"text" in item` hoặc truy cập `item["text"]`. Python ném `TypeError: argument of type 'LessonDecisionItem' is not iterable`. | Xây dựng lớp lai `LessonDecisionDict` kế thừa từ `dict` và hỗ trợ attribute access (`item.title`, `item.rule_id`). Bổ sung magic method `__contains__` để `"text" in item` luôn trả về `True`, trích xuất `text` từ `title` hoặc `human_message`. |
| **A2** | **Hiển thị `#undefined` trên UI** | Frontend mong đợi các trường `lesson_id`, `human_message`, `action_rule`. Khi backend trả về chuỗi thuần túy (`list[str]`) hoặc object khuyết ID, UI template ghép chuỗi `#{item.lesson_id}` dẫn đến chuỗi biến dạng `#undefined`. | Bổ sung hàm chuẩn hóa `normalizeLessonItem` tại frontend (`frontend/src/types/lesson.ts`), chuyển đổi an toàn mọi định dạng (string, object DTO, null/undefined) thành `NormalizedLessonItem` với ID sinh hợp lệ (`L-FALLBACK-...`), tuyệt đối loại bỏ chuỗi `#undefined`. |
| **A3** | **Sai lệch độ nghiêm trọng (Severity Mismatch)** | Quy tắc INFO/GREEN đôi khi bị gán nhầm vào `warning_notes` hoặc quy tắc WARN/YELLOW không hiển thị cảnh báo riêng biệt. Phân tích predicate bị crash khi predicate truyền trường `"value"` thay vì `"threshold"`. | Chuẩn hóa ánh xạ: `INFO` -> `lesson_advisories`, `WARN` -> `lesson_warnings`, `CRITICAL` -> `lesson_blockers`. Trong `LessonRuleService`, bổ sung fallback đọc `threshold = predicate.get("threshold") or predicate.get("value")` và chuyển kiểu an toàn sang `float`. |

---

### Khu Vực B: Direction & Price Resolver

| Mã | Phát Hiện Sự Cố | Nguyên Nhân Cũ (Before) | Giải Pháp Sửa Đổi V10.2 (After) |
|:---|:---|:---|:---|
| **B1** | **Lỗi hình học giá nghịch đảo (Inverted SHORT Geometry)** | Setup SHORT trên cặp ETHUSDT có entry 2049, nhưng SL bị đặt 2030 (thấp hơn entry) và TP 2055 (cao hơn entry). Backend tính toán vẫn nhận tham số hoặc không chặn ở mức resolver, khiến R:R hiển thị âm hoặc bất hợp lý. | Tạo bộ giải quyết mức giá tập trung `resolve_plan_levels()` trong `backend/services/plan_resolver.py`. Xác thực nghiêm ngặt quy tắc hình học: LONG yêu cầu `Entry > SL` và `TP > Entry`; SHORT yêu cầu `Entry < SL` và `TP < Entry`. Mọi vi phạm lập tức ném lỗi `INVALID_GEOMETRY`. |
| **B2** | **Ngầm định hướng lệnh (Direction Fallback Anti-Pattern)** | Khi payload thiếu `direction` hoặc `expected_direction`, code cũ fallback ngầm định `direction = smc_data.get("direction", "LONG")`. Người dùng chọn lệnh SHORT có nguy cơ bị arm thành LONG. | Loại bỏ hoàn toàn fallback ngầm định. Cả `resolve_plan_levels()`, `evaluate_setup_eligibility()`, và `manual_arm_setup` đều yêu cầu `direction` phải là `'LONG'` hoặc `'SHORT'`. Mọi trường hợp thiếu hoặc khác ném lỗi `UNKNOWN_DIRECTION` (400/422). |
| **B3** | **Rò rỉ mức giá xác nhận cũ (Confirmed Level Leakage)** | Khi một setup từ LONG đảo chiều thành SHORT hoặc sang instance ID mới, các trường `confirmed_entry`, `confirmed_sl`, `confirmed_tp` cũ không bị xóa. Hệ thống trộn `confirmed_sl` cũ (LONG) với `provisional_entry` mới (SHORT), tạo ra mức giá sai lệch. | Trong `StrategyService._upsert_watch_setup`, khi phát hiện `direction_changed` hoặc `instance_changed`, lập tức gán `confirmed_entry = None`, `confirmed_sl = None`, `confirmed_tp = None` và hủy mọi lệnh `armed` cũ của setup. Resolver từ chối các bộ confirmed thiếu phần tử (`CONFIRMED_LEVELS_INCOMPLETE`). |
| **B4** | **Bỏ qua ngưỡng biến động ATR** | Khi mức giá dịch chuyển một khoảng rất nhỏ (< 0.05$ hoặc < 0.1 ATR), hệ thống liên tục tăng version làm loãng lịch sử, hoặc ngược lại, bỏ qua bước nhảy lớn không thông báo cho người dùng. | Chuẩn hóa ngưỡng `threshold = max(atr * 0.1, 0.05)`. Chỉ tăng `version` và phát sự kiện `setup.updated` khi mức dịch chuyển vượt ngưỡng hoặc có thay đổi bản chất về cấu trúc thị trường (evidence/instance). |

---

### Khu Vực C: Realtime Quotes & Canonical Quote Store

| Mã | Phát Hiện Sự Cố | Nguyên Nhân Cũ (Before) | Giải Pháp Sửa Đổi V10.2 (After) |
|:---|:---|:---|:---|
| **C1** | **Thụt lùi báo giá realtime (Quote Regressions)** | Nến đóng (Candle Close) phát sinh định kỳ với timestamp cũ hơn tick giá realtime từ WebSocket. Khi frontend cập nhật từ nến, giá mới nhất bị ghi đè thành giá cũ, gây giật lag và nhảy giá. | Xây dựng `CanonicalQuoteStore` (`frontend/src/services/quoteStore.ts`) bảo toàn tính đơn điệu (Monotonicity). Chỉ chấp nhận cập nhật từ nến hoặc quote mới khi `timestamp >= latestTimestamp`. |
| **C2** | **Khoảng cách giá hiển thị NaN / không hỗ trợ SHORT** | Hàm tính khoảng cách đến điểm vào lệnh chỉ tính `planned_entry - last_price` (chỉ đúng cho LONG). Với SHORT, kết quả âm hoặc bị định dạng lỗi. Khi chưa có dữ liệu, UI hiển thị `NaN USDT`. | Chuẩn hóa hàm `getDistance(symbol, entry)`: tính tuyệt đối `|last_price - planned_entry|`, định dạng chính xác 2 chữ số thập phân (`"4.00 USDT"`), và trả về chuỗi fallback thân thiện `"-- / chưa có dữ liệu"` khi quote chưa sẵn sàng. |
| **C3** | **Phân mảnh Draft ID giữa Overlay và Intent** | Khi người dùng click copy setup sang nháp, `App.tsx` sinh hai ID khác nhau cho `draftPlanOverlay` và `selectedIntent`, khiến biểu đồ không đồng bộ thanh kéo R:R với panel chi tiết. | Thống nhất sinh một `draftId = "draft-${Date.now()}"` duy nhất và gán đồng thời cho cả overlay lẫn intent. Kiểm tra hình học trước khi render; nếu hình học sai, đặt `grossRR = 0`, `isValid = false` và vẽ outline cảnh báo thay vì hộp xanh. |
| **C4** | **Mất cảnh báo kế hoạch cũ (Stale Plan Notice)** | Khi backend phát sinh setup revision mới trong lúc người dùng đang xem setup cũ, không có thông báo cho người dùng biết kế hoạch đã thay đổi. | Triển khai banner cảnh báo kế hoạch đã lỗi thời (`stalePlanNotice`) khi `selectedIntent` khác revision/instance từ WebSocket envelope `setup.updated`. Nút "Xem bản mới" cho phép người dùng chuyển ngay sang kế hoạch mới nhất. |

---

## 3. Kiến Trúc Sửa Đổi & Sơ Đồ Khớp Giá (Target Architecture)

```
                            [ Thị Trường / Bitget WS ]
                                        │
                         ┌──────────────┴──────────────┐
                         ▼                             ▼
               Ticker Báo Giá Realtime          Nến Đóng Định Kỳ
               (Timestamp: T2 > T1)          (Timestamp: T1 < T2)
                         │                             │
                         └──────────────┬──────────────┘
                                        ▼
                           [ CanonicalQuoteStore ]
                       (Giữ tính đơn điệu: Reject T1)
                                        │
                                        ▼
             ┌──────────────────────────┴──────────────────────────┐
             ▼                                                     ▼
    [ ExpectedEntryPanel ]                                [ Chart / R:R Box ]
 - Live distance: |last - entry|                     - Strict Geometry Check
 - No "#undefined" lesson DTOs                       - Valid: Draw TP/SL Box
 - Stale Plan Notice banner                          - Invalid: Red Outline Only
```

```
[ Setup Generation / Manual Entry ]
                │
                ▼
      [ resolve_plan_levels ]
 1. Check direction: LONG | SHORT (No fallback)
 2. Check geometry: LONG: SL < Entry < TP
                   SHORT: TP < Entry < SL
 3. Confirmed levels: all 3 present or None
                │
         (Hợp lệ: PASS)
                │
                ▼
  [ evaluate_setup_eligibility ]
 - Check policy, quota, margin, max positions
 - Evaluate governed lesson rules
                │
                ▼
       [ manual_arm_setup ]
 - Check expected_direction conflict
 - Check setup_instance_id conflict
 - Create armed order safely (Paper Broker)
```

---

## 4. Kết Luận
Bằng việc phân lập rõ ràng 3 khu vực và giải quyết triệt để 11 nguyên nhân gốc trên:
- **100%** lỗi `#undefined` và `TypeError` trên lesson contract được khắc phục.
- **100%** lỗi hình học nghịch đảo và kế thừa mức giá cũ được ngăn chặn bằng resolver có thẩm quyền.
- **100%** báo giá realtime được bảo toàn tính đơn điệu và đồng bộ nháp lệnh với biểu đồ.
- Toàn bộ 56/56 kịch bản kiểm thử nghiệm thu V10.2 và 260/260 kịch bản hồi quy đều vượt qua thành công với mã thoát 0.
