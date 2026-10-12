# Báo Cáo Nghiệm Thu V14.4.2 (V14.4.2 Acceptance Report)

- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Base Commit**: `eb4eeb2` — feat(v14.4.2): implement stage B-F chart closure and readonly drag
- **Thời gian nghiệm thu**: 2026-10-12
- **Môi trường thực thi**: Windows 11, Node.js v20+, Python 3.11+, Chrome Headless (Playwright)
- **Kết quả tổng thể**: **PASS (100% các bước và 36 test cases bắt buộc đều ĐẠT)**

---

## 1. Tóm Tắt Kết Quả Các Bước Kiểm Tra (Step Summary)

Toàn bộ quá trình kiểm tra được điều phối và xác nhận độc lập qua script `tools/run_v14_4_2_acceptance.py`:

| Bước kiểm tra (Step) | Lệnh thực thi | Thời gian | Kết quả | Chi tiết / Log |
|---|---|---|---|---|
| **1. Backend Targeted** | `pytest tests/test_v14_4_2_*.py tests/test_v14_4_1_*.py tests/test_v14_3_*.py -q` | 17.5s | **PASS** | 177 passed, 0 failed, 1 warning |
| **2. Frontend Typecheck** | `npx tsc -b` | 5.9s | **PASS** | 0 error |
| **3. Frontend Lint** | `npx oxlint` | 1.2s | **PASS** | 0 error, 29 warnings |
| **4. Frontend Vitest** | `npx vitest run` | 3.6s | **PASS** | 26 test files, 219 tests passed |
| **5. Frontend Build** | `npm run build` | 7.4s | **PASS** | Vite production bundle build hoàn tất trong 1.35s |
| **6. Browser E2E** | `npx playwright test (4 suites)` | 141.0s | **PASS** | 39 browser tests passed (21 closure, 5 soak, 4 mobile, 9 sessions) |
| **7. Benchmark Latency** | `run_v14_4_chart_acceptance.py --cycles 600` | 10.4s | **PASS** | 600 cycles, p50: 15.6ms, p95: 22.3ms (<250ms limit), mem growth: 0.106MB |
| **8. Git Diff Check** | `git diff --check` | 0.1s | **PASS** | 0 whitespace errors, clean diff |
| **OVERALL** | Toàn bộ 8 gate | ~187s | **PASS** | Đủ 36/36 case IDs bắt buộc + 46 unit test cases chuyên biệt |

---

## 2. Bảng Ma Trận Chi Tiết 36 Test Cases Bắt Buộc (Case ID Matrix)

Tất cả 36 case IDs bắt buộc (D01–D12, R01–R05, M01–M04, E01–E09, S01–S06) được kiểm thử thực tế trên hệ thống backend cô lập và trình duyệt Playwright thực (không dùng mock rỗng hay `assert True`):

### Nhóm 1: Drag Lifecycle & Readonly Orders (D01 – D12)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **D01** | Setup SHORT candidate trên Upcoming Tab | Nhấp "Chép sang Bản Nháp (Draft)" | Mở Chart tab, tạo overlay draft SHORT với SL > Entry > TP, không tạo order/mutation | Overlay draft hiển thị chính xác, 0 mutations, state: `draft` | [v14_4_2_chart_closure.e2e.ts:29](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L29) |
| **D02** | Draft SHORT hiển thị trên chart | Kéo handle SL lên trên (deltaY = -30px) | Stop loss tăng lên, Entry và TP giữ nguyên, hướng vẫn là SHORT | SL tăng, Entry & TP cố định, 0 mutations | [v14_4_2_chart_closure.e2e.ts:60](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L60) |
| **D03** | Draft SHORT hiển thị trên chart | Kéo handle TP xuống dưới (deltaY = +30px) | Take profit giảm xuống, Entry và SL giữ nguyên | TP giảm, Entry & SL cố định, 0 mutations | [v14_4_2_chart_closure.e2e.ts:83](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L83) |
| **D04** | Draft SHORT hiển thị trên chart | Kéo handle SL xuống dưới Entry (deltaY = +150px) | Đánh dấu hình học không hợp lệ (`isValid: false`), chặn Arm, cảnh báo hình học | `isValid: false`, invalidReason gán đúng, không lật hướng | [v14_4_2_chart_closure.e2e.ts:106](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L106) |
| **D05** | Draft SHORT hiển thị trên chart | Kéo handle TP lên trên Entry (deltaY = -150px) | Đánh dấu hình học không hợp lệ (`isValid: false`), chặn Arm | `isValid: false`, invalidReason gán đúng, không lật hướng | [v14_4_2_chart_closure.e2e.ts:125](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L125) |
| **D06** | Draft SHORT hiển thị trên chart | Kéo body R:R lên trên (deltaY = -20px) | Dịch chuyển song song Entry, SL, TP với khoảng cách delta tuyệt đối bằng nhau | deltaEntry == deltaSL == deltaTP (sai số < 0.05) | [v14_4_2_chart_closure.e2e.ts:142](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L142) |
| **D07** | Draft SHORT hiển thị trên chart | Kéo cạnh phải (right edge) mở rộng R:R (deltaX = +40px) | Số nến dự phóng `projectedBars` tăng lên mà không làm thay đổi các mức giá | `projectedBars` tăng từ 25 lên >= 28, giá không đổi | [v14_4_2_chart_closure.e2e.ts:169](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L169) |
| **D08** | Draft SHORT đang được kéo thả nhanh | Di chuyển chuột nhiều bước và thả chuột (pointerup) | Flush frame cuối cùng, cập nhật giá trị chính xác của frame kết thúc | Frame cuối cùng được commit chính xác vào overlay state | [v14_4_2_chart_closure.e2e.ts:191](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L191) |
| **D09** | Đang kéo dở handle SL | Kích hoạt sự kiện hệ thống `pointercancel` | Khôi phục toàn bộ mức giá về trạng thái ban đầu trước khi kéo | Giá SL rollback về đúng mức trước khi drag | [v14_4_2_chart_closure.e2e.ts:216](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L216) |
| **D10** | Đang kéo dở handle SL | Mất pointer capture (`lostpointercapture`) | Rollback về trạng thái trước drag, không làm đóng băng pan/zoom của chart | Rollback sạch sẽ, chart tiếp tục pan bình thường | [v14_4_2_chart_closure.e2e.ts:241](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L241) |
| **D11** | Setup LONG candidate chép sang draft | Kéo SL xuống (deltaY = +20px), kéo TP lên (deltaY = -20px) | Phản ứng đối xứng: SL < Entry < TP, hướng LONG được bảo toàn | SL giảm, TP tăng, cấu trúc LONG hợp lệ | [v14_4_2_chart_closure.e2e.ts:263](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L263) |
| **D12** | Lệnh ở 4 trạng thái: ARMED, OPEN, CLOSED, INVALIDATED | Thử kéo các handle SL/TP/Body của lệnh | `readOnly = true`, con trỏ chuột không đổi thành resize, tọa độ không đổi, 0 mutations | Hoàn toàn bất biến (immutable) trên cả 4 trạng thái, 0 request ghi | [v14_4_2_chart_closure.e2e.ts:289](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L289) |

### Nhóm 2: Realtime WebSocket & Soak Resilience (R01 – R05)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **R01** | Chart tab đang mở, WebSocket kết nối | Đóng socket giả lập ngắt mạng | Socket tự động reconnect theo backoff, giữ duy nhất 1 kết nối active | Reconnect thành công (`isOpen = true`), subscriber count ổn định | [v14_4_2_realtime_soak.e2e.ts:19](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts#L19) |
| **R02** | Client đang lắng nghe domain events | Dispatch domain event với revision nhảy cóc (gap) | Kích hoạt debounce GET `/smc/scenarios` để đồng bộ lại dữ liệu | Endpoint GET `/smc/scenarios` được gọi chính xác và cập nhật | [v14_4_2_realtime_soak.e2e.ts:54](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts#L54) |
| **R03** | Ứng dụng đang chạy có dữ liệu | Chuyển đổi tab liên tục qua lại 100 chu kỳ | Không phát sinh uncaught exception, không rò rỉ bộ nhớ hay freeze UI | 0 page errors, giao diện phản hồi mượt mà | [v14_4_2_realtime_soak.e2e.ts:87](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts#L87) |
| **R04** | Feeder live quote đang gửi tick | Quan sát giá thị trường hiển thị trong 3 giây | Giá vàng cập nhật liên tục, định dạng tiền tệ đúng, queue không tràn | Hiển thị giá `$X,XXX.XX` chuẩn xác theo luồng tick | [v14_4_2_realtime_soak.e2e.ts:105](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts#L105) |
| **R05** | ChartComponent mount/unmount | Chuyển đổi qua lại các tab 5 lần | Số lượng subscribers quay về baseline, không tăng lũy tiến | Final subscribers <= initial subscribers + 5 | [v14_4_2_realtime_soak.e2e.ts:118](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts#L118) |

### Nhóm 3: Mobile Reachability & Responsiveness (M01 – M04)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **M01** | Viewport mobile 390x844 (iPhone 14) | Cuộn bảng Journal và tap trực tiếp vào nút "Chart" | Nút Chart nằm trong viewport màn hình hẹp, tap tự nhiên bằng ngón tay không bị che khuất | Nút Chart bấm được không cần `force: true` hay `dispatchEvent`, chuyển tab Chart | [v14_4_2_mobile.e2e.ts:20](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_mobile.e2e.ts#L20) |
| **M02** | Viewport mobile 390x844 | Mở drawer "Chi Tiết & Note" từ Journal rồi đóng lại | Drawer mở đúng dialog, đóng sạch sẽ bằng nút X, không gửi mutation API | Dialog mở/đóng chuẩn accessibility, 0 mutations | [v14_4_2_mobile.e2e.ts:63](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_mobile.e2e.ts#L63) |
| **M03** | Viewport mobile 390x844 | Mở menu cài đặt layer nến SMC trên chart | Menu popup và các toggle nằm trọn vẹn trong chiều rộng 390px, không tràn viền | Bounding box menu <= 395px, đóng khi tap ra ngoài | [v14_4_2_mobile.e2e.ts:92](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_mobile.e2e.ts#L92) |
| **M04** | Nhiều kích thước viewport: 1366x768, 1440x900, 390x844, 390x663 | Đổi kích thước cửa sổ và mở chart | Chart pane co giãn linh hoạt, duy trì chiều rộng > 250px và chiều cao > 150px | Mọi viewport đều có chart bounds khả dụng | [v14_4_2_mobile.e2e.ts:115](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_mobile.e2e.ts#L115) |

### Nhóm 4: Evidence Closure & Visual Layout (E01 – E09)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **E01** | Setup có chuỗi SMC đã xác nhận | Nhấp "Xem Trên Chart" từ Upcoming tab | Chart hiển thị đầy đủ cấu trúc: Swings, Structure Events, Zones FVG, Liquidity | `readHits` trả về danh mục EVENT / SWING đầy đủ | [v14_4_2_chart_closure.e2e.ts:325](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L325) |
| **E02** | Bundle bằng chứng đã render | Kiểm tra tọa độ thời gian các điểm hits | Tọa độ `timeMs` là số nguyên dương hợp lệ, gắn với mốc nến đóng thực tế | `Number.isSafeInteger(timeMs) == true`, không có NaN/null | [v14_4_2_chart_closure.e2e.ts:339](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L339) |
| **E03** | Khung chart có kích thước xác định | Đặt nhiều nhãn FVG, MSS, Sweep cạnh nhau | Thuật toán `layoutAnnotationLabels` xếp nhãn theo làn, clamp vừa khít viewport pane | Nhãn nằm hoàn toàn trong bounding box của chart | [v14_4_2_chart_closure.e2e.ts:355](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L355) |
| **E04** | Lệnh lịch sử cũ đã đóng được lưu snapshot | Bấm "Chart" từ Journal tab | Hiển thị badge provenance snapshot với đúng version của snapshot lịch sử | Header hiển thị badge `LỊCH SỬ LỆNH (FROZEN)` kèm Snapshot ID | [v14_4_2_chart_closure.e2e.ts:364](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L364) |
| **E05** | Đang xem bằng chứng setup | Bấm "✕ Hủy chọn" trên header chart | Xóa sạch các annotations của setup, không rò rỉ cấu trúc thị trường toàn cục | `smcHits` trở về rỗng (length = 0) | [v14_4_2_chart_closure.e2e.ts:376](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L376) |
| **E06** | Lệnh đã focus trên chart | Bấm nút "Chi tiết" trên SelectedChartHeader | Mở TradeDetailsDrawer hiển thị đúng Snapshot Provenance badge và Evidence Accordion | `snapshot-provenance-badge` và `evidence-accordion-toggle` hiển thị | [v14_4_2_chart_closure.e2e.ts:392](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L392) |
| **E07** | Đổi cài đặt layer trong ChartLayerMenu | Tải lại trang (reload) | Cài đặt được lưu trong `localStorage` key `aurum.chart.layers.v1` và khôi phục lại | Settings được khôi phục chính xác sau reload | [v14_4_2_chart_closure.e2e.ts:416](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L416) |
| **E08** | Lệnh lịch sử có bằng chứng ngoài vùng nhìn hiện tại | Mở danh sách bằng chứng và chọn một node lịch sử | Chart tự động dịch chuyển (scroll) đến mốc thời gian của node và focus chính xác | Visible logical range dịch chuyển bao quát node mốc, `focusedEvidenceId` khớp | [v14_4_2_chart_closure.e2e.ts:446](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L446) |
| **E09** | Chọn bằng chứng và hủy chọn nhanh | Chuyển đổi focus giữa các setup hoặc đóng drawer | Yêu cầu focus không bị rò rỉ sang đối tượng kế tiếp, trạng thái focus an toàn | Hủy chọn xóa sạch focused state, không có race condition | [v14_4_2_chart_closure.e2e.ts:475](file:///d:/build%20app-crypto/aurum-desk/frontend/tests/e2e/v14_4_2_chart_closure.e2e.ts#L475) |

### Nhóm 5: Backend Requirements, Guards & Forecast (S01 – S06)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **S01** | Băng nến lịch sử có mốc `as_of_ms` | Gọi hàm đánh giá kịch bản | Không sử dụng bất kỳ nến nào sau mốc `as_of_ms` (zero lookahead) | Toàn bộ nến phân tích <= `as_of_ms`, không có rò rỉ tương lai | [test_v14_4_2_forecast_requirements.py:27](file:///d:/build%20app-crypto/aurum-desk/backend/tests/test_v14_4_2_forecast_requirements.py#L27) |
| **S02** | Kịch bản Á, Âu, Mỹ đã được tính toán | Đối chiếu quan hệ giữa phiên Á/Âu với phiên NY | Phân loại chính xác: `SUPPORTS`, `CONFLICTS`, hoặc `UNCERTAIN` | Cùng hướng -> `SUPPORTS`, ngược hướng -> `CONFLICTS`, trung tính -> `UNCERTAIN` | [test_v14_4_2_forecast_requirements.py:65](file:///d:/build%20app-crypto/aurum-desk/backend/tests/test_v14_4_2_forecast_requirements.py#L65) |
| **S03** | Khung thời gian M15 POI | Kiểm tra phân biệt vùng M15 | Phân định rõ ràng POI M15 là bắt buộc (`REQUIRED`) hay chỉ hỗ trợ (`SUPPORTING`) | POI mode được phản ánh trung thực trong descriptor model | [test_v14_4_2_forecast_requirements.py:109](file:///d:/build%20app-crypto/aurum-desk/backend/tests/test_v14_4_2_forecast_requirements.py#L109) |
| **S04** | Hệ thống chưa nạp mô hình OOS | Truy vấn tỷ lệ xác suất thành công | Hiển thị `UNAVAILABLE` / `NOT_VERIFIED`, không tự tính % win giả | Xác suất là `null`, trường ghi chú minh bạch `NOT_VERIFIED` | [test_v14_4_2_forecast_requirements.py:132](file:///d:/build%20app-crypto/aurum-desk/backend/tests/test_v14_4_2_forecast_requirements.py#L132) |
| **S05** | Có tin tức đỏ hoặc bài học thua lỗ gần đây | Đánh giá điều kiện Arm / Vào lệnh | Kích hoạt bộ chặn News Guard, Loss Lesson Guard, Account Guard, Quote Guard | Trạng thái chặn đúng nguyên nhân, `can_arm = false` | [test_v14_4_2_entry_regression.py:20](file:///d:/build%20app-crypto/aurum-desk/backend/tests/test_v14_4_2_entry_regression.py#L20) |
| **S06** | Đang có 1 lệnh mở, sau đó lệnh đóng lại | Tạo setup mới sau khi lệnh cũ đã đóng hoàn tất | Lệnh cũ được ghi nhận outcome vào `PaperObservation`, setup mới được phép arm | Lưu `PaperObservation` thành công, mở khóa cho tín hiệu mới | [test_v14_4_2_entry_regression.py:68](file:///d:/build%20app-crypto/aurum-desk/backend/tests/test_v14_4_2_entry_regression.py#L68) |

---

## 3. Đo Lường Hiệu Năng & Độ Trễ (Benchmark Results)

Kết quả đo đạc từ `tools/run_v14_4_chart_acceptance.py` trên 600 chu kỳ nến thực:

- **Số chu kỳ kiểm tra (Cycles)**: 600 chu kỳ
- **Số chu kỳ xác nhận cấu trúc thành công**: 600/600 chu kỳ
- **Tổng thời gian thực thi (Total Time)**: 9.345 giây
- **Thông lượng (Throughput)**: **64.2 chu kỳ / giây**
- **Độ trễ đánh giá kịch bản (Latency)**:
  - **p50**: 15.576 ms
  - **p95**: 22.346 ms (ngưỡng trần quy định: 250.0 ms)
  - **Max**: 34.152 ms
- **Tăng trưởng bộ nhớ (Memory Growth)**: **0.106 MB** (ngưỡng trần quy định: 25.0 MB)
- **Tuyên bố đo lường trung thực**: Các chỉ số render canvas của trình duyệt và độ dài hàng đợi frontend được kiểm tra độc lập qua bộ test Playwright 39 cases, không báo cáo khống vào benchmark pure Python.


---

## 4. Cam Kết An Toàn & Bảo Toàn Dữ Liệu (Invariants & Safety)

1. **Chế độ PAPER ONLY**: 100% các lệnh và quan sát được ghi nhận vào bảng `paper_observations` và `orders` loại PAPER, không gửi bất kỳ lệnh nào lên API sàn thật (Bitget) hay kênh Telegram thật.
2. **Khung giao dịch NY_ONLY**: Chỉ cho phép khớp lệnh trong phiên New York (08:00–11:00 NY time). Phân tích Á/Âu mang tính chất định hướng (`SUPPORTS`/`CONFLICTS`).
3. **Giới hạn số lệnh**: Tối đa 3 fills/ngày, tối đa 1 vị thế mở tại một thời điểm, cap rủi ro fallback 0.10%.
4. **Không rò rỉ tương lai**: Dữ liệu nến chỉ được đọc đến mốc `as_of_ms` của nến đã đóng.
5. **Zero Mutation on Inspect / Drag**: Các thao tác xem bằng chứng, mở drawer, cuộn trang hay kéo thả thử nghiệm không tạo ra bất kỳ request ghi (POST/PUT/DELETE) nào đến các endpoint giao dịch.
6. **Xác suất trung thực**: Mọi chỉ số xác suất khi chưa có dataset Out-Of-Sample thực nghiệm đều trả về `null` hoặc `UNAVAILABLE`.

---

## 5. Kết Luận Nghiệm Thu (Acceptance Conclusion)

Phiên bản **V14.4.2** đã hoàn thành trọn vẹn toàn bộ các mục tiêu đề ra:
- Sửa code thật và kết nối caller thật trên toàn bộ pipeline backend lẫn giao diện frontend.
- Vượt qua toàn bộ 36 test cases bắt buộc từ D01–D12, R01–R05, M01–M04, E01–E09, S01–S06 kèm 46 unit test cases chuyên biệt.
- Đạt 100% tỷ lệ pass ở tất cả 8 bước nghiệm thu, không có bất kỳ placeholder, mock rỗng hay `assert True` giả định nào.
- Đáp ứng đầy đủ các tiêu chuẩn kiểm thử tự động, khả năng tiếp cận (accessibility) và hiệu năng mượt mà trên cả desktop lẫn mobile.
