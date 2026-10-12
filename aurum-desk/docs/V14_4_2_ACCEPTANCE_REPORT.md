# Báo Cáo Nghiệm Thu V14.4.2 (V14.4.2 Acceptance Report)

- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Base Commit**: `3d850b2` — fix(v14.4.2): complete realtime sync, observation savepoints, and chart evidence acceptance
- **Thời gian nghiệm thu**: 2026-10-12
- **Môi trường thực thi**: macOS (Darwin 24.1.0), Node.js v22.22.3, Python 3.13.0, Chrome Headless (Playwright)
- **Kết quả tổng thể**: **PASS (100% các bước và 56/56 test cases bắt buộc đều ĐẠT)**

---

## 1. Tóm Tắt Kết Quả Các Bước Kiểm Tra (Step Summary)

Toàn bộ quá trình kiểm tra được điều phối và xác nhận độc lập qua script `tools/run_v14_4_2_acceptance.py`:

| Bước kiểm tra (Step) | Lệnh thực thi | Thời gian | Kết quả | Chi tiết / Log |
|---|---|---|---|---|
| **1. Backend Targeted** | `pytest tests/test_v14_4_2_*.py tests/test_v14_4_1_*.py tests/test_v14_3_*.py -q` | 10.1s | **PASS** | 194 passed, 0 failed, 1 warning |
| **2. Frontend Typecheck** | `npx tsc -b` | 3.8s | **PASS** | 0 error |
| **3. Frontend Lint** | `npx oxlint` | 0.3s | **PASS** | 0 error, 29 warnings |
| **4. Frontend Vitest** | `npx vitest run --reporter=default --reporter=junit` | 1.4s | **PASS** | 26 test files, 226 tests passed |
| **5. Frontend Build** | `npm run build` | 4.6s | **PASS** | Vite production bundle build hoàn tất |
| **6. Browser E2E** | `npx playwright test (5 suites)` | 89.3s | **PASS** | 47 browser tests passed (21 closure, 5 soak, 4 mobile, 9 sessions, 8 selection source) |
| **7. Benchmark Latency** | `run_v14_4_chart_acceptance.py --cycles 600` | 4.9s | **PASS** | 600 chu kỳ hoàn tất, p50: 15.6ms, p95: 22.3ms |
| **8. Git Diff Check** | `git diff --check` | 0.0s | **PASS** | Không có trailing whitespace hoặc merge conflict markers |
| **OVERALL** | Toàn bộ 8 gate | ~114s | **PASS** | Đủ 56/56 required case IDs bắt buộc đạt 100% |

---

## 2. Bảng Ma Trận Chi Tiết 56 Test Cases Bắt Buộc (Case ID Matrix)

Tất cả 56 case IDs bắt buộc được kiểm thử thực tế trên hệ thống backend cô lập và trình duyệt Playwright thực (không dùng mock rỗng hay `assert True`):

### Nhóm 1: Drag Lifecycle & Readonly Orders (D01 – D12)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **D01** | Setup SHORT candidate trên Upcoming Tab | Nhấp "Chép sang Bản Nháp (Draft)" | Mở Chart tab, tạo overlay draft SHORT với SL > Entry > TP, không tạo order/mutation | Overlay draft hiển thị chính xác, 0 mutations, state: `draft` | `v14_4_2_chart_closure.e2e.ts` |
| **D02** | Draft SHORT hiển thị trên chart | Kéo handle SL lên trên (deltaY = -30px) | Stop loss tăng lên, Entry và TP giữ nguyên, hướng vẫn là SHORT | SL tăng, Entry & TP cố định, 0 mutations | `v14_4_2_chart_closure.e2e.ts` |
| **D03** | Draft SHORT hiển thị trên chart | Kéo handle TP xuống dưới (deltaY = +30px) | Take profit giảm xuống, Entry và SL giữ nguyên | TP giảm, Entry & SL cố định, 0 mutations | `v14_4_2_chart_closure.e2e.ts` |
| **D04** | Draft SHORT hiển thị trên chart | Kéo handle SL xuống dưới Entry (deltaY = +150px) | Đánh dấu hình học không hợp lệ (`isValid: false`), chặn Arm, cảnh báo hình học | `isValid: false`, invalidReason gán đúng, không lật hướng | `v14_4_2_chart_closure.e2e.ts` |
| **D05** | Draft SHORT hiển thị trên chart | Kéo handle TP lên trên Entry (deltaY = -150px) | Đánh dấu hình học không hợp lệ (`isValid: false`), chặn Arm | `isValid: false`, invalidReason gán đúng, không lật hướng | `v14_4_2_chart_closure.e2e.ts` |
| **D06** | Draft SHORT hiển thị trên chart | Kéo body R:R lên trên (deltaY = -20px) | Dịch chuyển song song Entry, SL, TP với khoảng cách delta tuyệt đối bằng nhau | deltaEntry == deltaSL == deltaTP (sai số < 0.05) | `v14_4_2_chart_closure.e2e.ts` |
| **D07** | Draft SHORT hiển thị trên chart | Kéo cạnh phải (right edge) mở rộng R:R (deltaX = +40px) | Số nến dự phóng `projectedBars` tăng lên mà không làm thay đổi các mức giá | `projectedBars` tăng từ 25 lên >= 28, giá không đổi | `v14_4_2_chart_closure.e2e.ts` |
| **D08** | Draft SHORT đang được kéo thả nhanh | Di chuyển chuột nhiều bước và thả chuột (pointerup) | Flush frame cuối cùng, cập nhật giá trị chính xác của frame kết thúc | Frame cuối cùng được commit chính xác vào overlay state | `v14_4_2_chart_closure.e2e.ts` |
| **D09** | Đang kéo dở handle SL | Kích hoạt sự kiện hệ thống `pointercancel` | Khôi phục toàn bộ mức giá về trạng thái ban đầu trước khi kéo | Giá SL rollback về đúng mức trước khi drag | `v14_4_2_chart_closure.e2e.ts` |
| **D10** | Đang kéo dở handle SL | Mất pointer capture (`lostpointercapture`) | Rollback về trạng thái trước drag, không làm đóng băng pan/zoom của chart | Rollback sạch sẽ, chart tiếp tục pan bình thường | `v14_4_2_chart_closure.e2e.ts` |
| **D11** | Setup LONG candidate chép sang draft | Kéo SL xuống (deltaY = +20px), kéo TP lên (deltaY = -20px) | Phản ứng đối xứng: SL < Entry < TP, hướng LONG được bảo toàn | SL giảm, TP tăng, cấu trúc LONG hợp lệ | `v14_4_2_chart_closure.e2e.ts` |
| **D12** | Lệnh ở 4 trạng thái: ARMED, OPEN, CLOSED, INVALIDATED | Thử kéo các handle SL/TP/Body của lệnh | `readOnly = true`, con trỏ chuột không đổi thành resize, tọa độ không đổi, 0 mutations | Hoàn toàn bất biến (immutable) trên cả 4 trạng thái, 0 request ghi | `v14_4_2_chart_closure.e2e.ts` |

### Nhóm 2: Realtime WebSocket & Soak Resilience (R01 – R05)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **R01** | Chart tab đang mở, WebSocket kết nối | Đóng socket giả lập ngắt mạng | Socket tự động reconnect theo backoff, giữ duy nhất 1 kết nối active | Reconnect thành công (`isOpen = true`), subscriber count ổn định | `v14_4_2_realtime_soak.e2e.ts` |
| **R02** | Client đang lắng nghe domain events | Dispatch domain event với revision nhảy cóc (gap) | Kích hoạt debounce GET `/smc/scenarios` để đồng bộ lại dữ liệu | Endpoint GET `/smc/scenarios` được gọi chính xác và cập nhật | `v14_4_2_realtime_soak.e2e.ts` |
| **R03** | Ứng dụng đang chạy có dữ liệu | Chuyển đổi tab liên tục qua lại 100 chu kỳ | Không phát sinh uncaught exception, không rò rỉ bộ nhớ hay freeze UI | 0 page errors, giao diện phản hồi mượt mà | `v14_4_2_realtime_soak.e2e.ts` |
| **R04** | Feeder live quote đang gửi tick | Quan sát giá thị trường hiển thị trong 3 giây | Giá vàng cập nhật liên tục, định dạng tiền tệ đúng, queue không tràn | Hiển thị giá chuẩn xác theo luồng tick | `v14_4_2_realtime_soak.e2e.ts` |
| **R05** | ChartComponent mount/unmount | Chuyển đổi qua lại các tab 5 lần | Số lượng subscribers quay về baseline, không tăng lũy tiến | Final subscribers <= initial subscribers + 5 | `v14_4_2_realtime_soak.e2e.ts` |

### Nhóm 3: Mobile Reachability & Responsiveness (M01 – M04)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **M01** | Viewport 390x844 (mobile phone) | Nhấp nút "Chart" trên dòng lịch sử Journal | Bấm tự nhiên không dùng `force: true`, mở đúng chart lệnh lịch sử | Nút nằm gọn trong màn hình, mở đúng Chart tab | `v14_4_2_mobile.e2e.ts` |
| **M02** | Viewport 390x844 (mobile phone) | Mở drawer Chi Tiết & Note, sau đó đóng | Drawer mở và đóng mượt mà, không làm sai lệch vị thế hay quota | 0 mutations, đóng mở hoàn chỉnh | `v14_4_2_mobile.e2e.ts` |
| **M03** | Viewport mobile chạm (touch enabled) | Mở Layer Menu và tương tác các điều khiển | Các menu và modal nằm hoàn toàn trong khung nhìn 390px | Menu hiển thị gọn gàng, không bị tràn màn hình | `v14_4_2_mobile.e2e.ts` |
| **M04** | Thay đổi kích thước màn hình responsive | Đổi viewport: 1366x768, 1440x900, 390x844, 390x663 | Chart canvas giữ kích thước tối thiểu sử dụng được, không bị vỡ bố cục | Width > 250px, Height > 150px trên mọi kích thước | `v14_4_2_mobile.e2e.ts` |

### Nhóm 4: Bằng Chứng Chart & Provenance (E01 – E09)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **E01** | Chọn lệnh hoặc setup trên danh sách | Kiểm tra các thực thể bằng chứng được vẽ | Hiển thị đúng các node Swing, Break, FVG, Retest và Liquidity của lệnh | Bằng chứng hiển thị khớp trigger chain của snapshot | `v14_4_2_chart_closure.e2e.ts` |
| **E02** | Snapshot bằng chứng có timestamps | Kiểm tra tọa độ thời gian các node | Tọa độ `occurred_at_ms` khớp nến đóng nguồn, không vượt quá `cutoff_ms` | Timestamp bảo toàn nhân quả 100% | `v14_4_2_chart_closure.e2e.ts` |
| **E03** | Khung hiển thị có mật độ nhãn dày | Kiểm tra va chạm giữa nhãn SMC và hộp R:R | Toàn bộ nhãn nằm trong khung nhìn và né vùng cấm của R:R (exclusions) | 100% nhãn không đè lên nhãn SL/TP/Entry | `v14_4_2_chart_closure.e2e.ts` |
| **E04** | Chọn lệnh quá khứ có config cũ | Quan sát nhãn phiên bản provenance | Hiển thị nhãn version lịch sử chính xác, không tự ý gán luật hiện tại | Nhãn "Phiên bản lúc vào lệnh, có khác biệt" hiển thị đúng | `v14_4_2_chart_closure.e2e.ts` |
| **E05** | Đang xem bằng chứng của một lệnh | Bấm nút xóa lựa chọn (Clear Selection) | Dọn dẹp toàn bộ overlay R:R và bằng chứng riêng của lệnh, trả về tổng quan | Canvas sạch sẽ, không rò rỉ vết tích của lệnh cũ | `v14_4_2_chart_closure.e2e.ts` |
| **E06** | Chọn một lệnh trên bảng lịch sử | Mở Trade Details Drawer | Thông tin id, snapshot, hướng và chuỗi bằng chứng khớp 100% với chart | Dữ liệu Drawer và Chart đồng nhất tuyệt đối | `v14_4_2_chart_closure.e2e.ts` |
| **E07** | Người dùng bật/tắt các lớp layer hiển thị | Tải lại trang (F5) | Các tùy chọn hiển thị layer được khôi phục từ `localStorage` | Tùy chọn layer được lưu trữ bền vững và đúng schema | `v14_4_2_chart_closure.e2e.ts` |
| **E08** | Bằng chứng nằm ngoài khung nhìn chart | Mở danh sách bằng chứng và bấm "Xem" | Chart tự động cuộn (setVisibleRange) đến mốc thời gian của bằng chứng | Viewport dịch chuyển bao hàm timestamp, highlight node | `v14_4_2_chart_closure.e2e.ts` |
| **E09** | Chuyển đổi nhanh giữa hai lệnh A và B | Kiểm tra highlight focus của lệnh A | Focus ID của lệnh A bị hủy ngay lập tức, không rò rỉ sang lệnh B | 0 rò rỉ focus state qua các lần chuyển selection | `v14_4_2_chart_closure.e2e.ts` |

### Nhóm 5: Forecast & Entry Regression (S01 – S06)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **S01** | Chu kỳ quét thị trường chạy tại mốc `T` | Kiểm tra nến nạp vào engine | Chỉ đọc nến đóng trước `as_of_ms`, loại bỏ hoàn toàn nến tương lai | Không rò rỉ dữ liệu nến tương lai | `test_v14_4_2_forecast_requirements.py` |
| **S02** | Dự đoán 3 phiên Á / Âu / Mỹ | So sánh tương quan hướng với kịch bản NY | Trả về quan hệ `SUPPORTS`, `CONFLICTS`, hoặc `UNCERTAIN` minh bạch | Quan hệ phiên được phân loại chính xác theo bối cảnh | `test_v14_4_2_forecast_requirements.py` |
| **S03** | Khảo sát các chế độ POI M15 | Kiểm tra điều kiện POI required / supporting | Tuân thủ đúng cấu hình requirement profile của hệ thống | POI mode được tuân thủ nghiêm ngặt | `test_v14_4_2_forecast_requirements.py` |
| **S04** | Hệ thống chưa nạp mô hình OOS | Truy vấn tỷ lệ xác suất thành công | Hiển thị `UNAVAILABLE` / `NOT_VERIFIED`, không tự tính % win giả | Xác suất là `null`, trường ghi chú minh bạch `NOT_VERIFIED` | `test_v14_4_2_forecast_requirements.py` |
| **S05** | Có tin tức đỏ hoặc bài học thua lỗ gần đây | Đánh giá điều kiện Arm / Vào lệnh | Kích hoạt bộ chặn News Guard, Loss Lesson Guard, Account Guard, Quote Guard | Trạng thái chặn đúng nguyên nhân, `can_arm = false` | `test_v14_4_2_entry_regression.py` |
| **S06** | Đang có 1 lệnh mở, sau đó lệnh đóng lại | Tạo setup mới sau khi lệnh cũ đã đóng hoàn tất | Lệnh cũ được ghi nhận outcome vào `PaperObservation`, setup mới được phép arm | Lưu `PaperObservation` thành công, mở khóa cho tín hiệu mới | `test_v14_4_2_entry_regression.py` |

### Nhóm 6: Provisional Plan & Hai Ảnh Số Liệu (T01 – T08, T17 – T20)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **T01** | Ảnh 1: SHORT Entry 4178.16, SL 4186.50, TP 4179.00 | Đánh giá qua `build_provisional_plan` | `INVALID_GEOMETRY` vì TP > Entry; không tạo TP giả | `status: INVALID_GEOMETRY`, `reason: INVALID_SHORT_GEOMETRY` | `test_v14_4_2_provisional_plan_regression.py` |
| **T02** | Setup SHORT có pivot đáy 4170.00 chưa bị quét | Đánh giá qua `build_provisional_plan` | Chọn 4170.00 làm target dự kiến (TP < Entry < SL) | `target_price: 4170.00`, `status: AVAILABLE` | `test_v14_4_2_provisional_plan_regression.py` |
| **T03** | Pivot 4170.00 đã SWEPT, pivot 4160.00 nến tương lai | Đánh giá target thanh khoản | Loại bỏ level đã swept và level tương lai; trả `NO_TARGET` | `status: NO_TARGET`, `target_price: None` | `test_v14_4_2_provisional_plan_regression.py` |
| **T04** | Setup LONG Entry 4180.00, SL 4170.00, TP 4179.00 | Đánh giá qua `build_provisional_plan` | `INVALID_GEOMETRY` vì TP < Entry đối với lệnh Mua | `status: INVALID_GEOMETRY`, `reason: INVALID_LONG_GEOMETRY` | `test_v14_4_2_provisional_plan_regression.py` |
| **T05** | Tín hiệu signal confirmed có đủ Entry/SL/TP | Đánh giá mức giá dự kiến | Giữ nguyên triple gốc, không ghép confirmed Entry với provisional SL/TP | Triple nguyên tử được bảo toàn, không trộn lẫn | `test_v14_4_2_provisional_plan_regression.py` |
| **T06** | Lệnh đang ở trạng thái ARMED hoặc PAPER_OPEN | Quét cập nhật upcoming setups | Mức giá của lệnh đã arm/open bị đóng băng, không bị ghi đè | 100% frozen levels, không biến đổi giá | `test_v14_4_2_provisional_plan_regression.py` |
| **T07** | Endpoint `GET /api/v1/setups/upcoming` | Truy vấn danh sách setup dự kiến | Trả về `resolved_display_plan` hợp nhất, nhất quán với row | DTO chuẩn hóa đầy đủ trường, 0 mutation | `test_v14_4_2_provisional_plan_regression.py` |
| **T08** | Setup có kế hoạch invalid (Ảnh 1) | Kiểm tra trường tài chính trong DTO | `is_valid: false`, `financial: null`, không hiển thị default 0.05 oz / $2.5 | `financial is None`, loại bỏ hoàn toàn fallback giả | `test_v14_4_2_provisional_plan_regression.py` |
| **T17** | Ảnh 2: SHORT 4179.18 / 4184.45 / 4159.18, Q 0.28 | Tính toán tài chính qua `calculate_risk_reward` | Gross RR: 3.7951, Net RR: 1.879, Net Risk: $2.47, Net Reward: $4.64 | Khớp chính xác 100% số liệu ảnh 2, `meets_min_rr: false` | `test_v14_4_2_provisional_plan_regression.py` |
| **T18** | Ảnh 1: SHORT sai hình học | Kiểm tra kết quả tài chính | `is_valid: false`, rủi ro và khối lượng bằng 0.0 | `quantity: 0.0, net_risk_usdt: 0.0, net_rr: 0.0` | `test_v14_4_2_provisional_plan_regression.py` |
| **T19** | Kế hoạch có net reward âm hoặc ngược hướng | Kiểm tra signed R:R | Không dùng `Math.abs` để biến reward âm thành lợi nhuận | `net_reward <= 0` đánh dấu không hợp lệ, không chuyển thành dương | `test_v14_4_2_provisional_plan_regression.py` |
| **T20** | Kiểm tra cost assumptions giữa LONG và SHORT | Tính toán phí giao dịch và trượt giá | Snapshot chi phí đồng nhất cho cả 2 hướng, không nhân khống leverage | Tính toán chi phí chính xác và nhất quán | `test_v14_4_2_provisional_plan_regression.py` |

### Nhóm 7: Selection Source, Draft Isolation & Error 500 (T09 – T16)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **T09** | Màn hình chính | Bấm "Vẽ Thử Kế Hoạch (Draft)" và chọn Bán | Tạo draft SHORT độc lập có nhãn "Bản nháp tự vẽ; TP chưa có bằng chứng SMC", 0 mutation | State: `draft`, direction: `SHORT`, 0 mutations | `v14_4_2_selection_source.e2e.ts` |
| **T10** | Ảnh 2: Scenario ở trạng thái WAITING_BREAK không có plan | Nhấp chọn kịch bản WAITING_BREAK trên sidebar | Header chuyển thành SCENARIO nhưng KHÔNG hiển thị hộp R:R | Không có overlay R:R, không lẫn draft | `v14_4_2_selection_source.e2e.ts` |
| **T11** | Endpoint chart evidence trả về HTTP 500 | Nhấp chọn kịch bản | Hiển thị thông báo lỗi rõ ràng, xóa sạch R:R overlay cũ, không giữ bằng chứng cũ | Banner lỗi xuất hiện, 0 stale R:R overlay | `v14_4_2_selection_source.e2e.ts` |
| **T12** | Đang xem setup có kế hoạch hợp lệ | Chuyển sang chọn kịch bản WAITING_BREAK không plan | Xóa ngay lập tức hộp R:R của setup cũ, không giữ R:R cũ | Hộp R:R được dọn sạch hoàn toàn | `v14_4_2_selection_source.e2e.ts` |
| **T13** | Setup SHORT hợp lệ trên Upcoming Tab | Nhấp "Vẽ Thử" (Copy sang Draft) | Tạo bản nháp mới mang cùng các mức giá nhưng trạng thái uncertified | State chuyển sang `draft`, giữ nguyên bộ giá | `v14_4_2_selection_source.e2e.ts` |
| **T14** | Kịch bản có plan SMC đã được chứng nhận | Chọn kịch bản lên biểu đồ | Hộp R:R hiển thị ở chế độ `readOnly: true` (không cho kéo sửa giá) | `readOnly: true` bảo vệ kế hoạch chuẩn SMC | `v14_4_2_selection_source.e2e.ts` |
| **T15** | Chuyển đổi nhanh A -> B trong khi A phản hồi chậm | Bấm chọn kịch bản A rồi bấm ngay kịch bản B | Phản hồi muộn của A bị loại bỏ theo `generation`, chỉ hiển thị bundle B | 0 race condition, chỉ bundle B được nạp | `v14_4_2_selection_source.e2e.ts` |
| **T16** | Đang trong chế độ Draft | Bấm chuyển đổi giữa Mua và Bán | Cập nhật hướng draft tức thì và cho phép chỉnh sửa mức giá | Chuyển đổi linh hoạt giữa LONG và SHORT | `v14_4_2_selection_source.e2e.ts` |

---

## 3. Đo Lường Hiệu Năng & Độ Trễ (Benchmark Results)

Kết quả đo đạc từ `tools/run_v14_4_chart_acceptance.py` trên 600 chu kỳ nến thực:

- **Số chu kỳ kiểm tra (Cycles)**: 600 chu kỳ
- **Số chu kỳ xác nhận cấu trúc thành công**: 600/600 chu kỳ
- **Tổng thời gian thực thi (Total Time)**: 4.887 giây
- **Thông lượng (Throughput)**: **122.8 chu kỳ / giây**
- **Độ trễ đánh giá kịch bản (Latency)**:
  - **p50**: 15.576 ms
  - **p95**: 22.346 ms (ngưỡng trần quy định: 250.0 ms)
  - **Max**: 34.152 ms
- **Tăng trưởng bộ nhớ (Memory Growth)**: **0.106 MB** (ngưỡng trần quy định: 25.0 MB)
- **Tuyên bố đo lường trung thực**: Các chỉ số render canvas của trình duyệt và độ dài hàng đợi frontend được kiểm tra độc lập qua bộ test Playwright 47 cases, không báo cáo khống vào benchmark pure Python.

---

## 4. Cam Kết An Toàn & Bảo Toàn Dữ Liệu (Invariants & Safety)

1. **Chế độ PAPER ONLY**: 100% các lệnh và quan sát được ghi nhận vào bảng `paper_observations` và `orders` loại PAPER, không gửi bất kỳ lệnh nào lên API sàn thật (Bitget) hay kênh Telegram thật.
2. **Khung giao dịch NY_ONLY**: Chỉ cho phép khớp lệnh trong phiên New York (08:00–11:00 NY time). Phân tích Á/Âu mang tính chất định hướng (`SUPPORTS`/`CONFLICTS`).
3. **Giới hạn số lệnh**: Tối đa 3 fills/ngày, tối đa 1 vị thế mở tại một thời điểm, cap rủi ro fallback 0.10%.
4. **Không rò rỉ tương lai**: Dữ liệu nến chỉ được đọc đến mốc `as_of_ms` của nến đã đóng.
5. **Zero Mutation on Inspect / Drag**: Các thao tác xem bằng chứng, mở drawer, cuộn trang hay kéo thả thử nghiệm không tạo ra bất kỳ request ghi (POST/PUT/DELETE) nào đến các endpoint giao dịch.
6. **Xác suất trung thực**: Mọi chỉ số xác suất khi chưa có dataset Out-Of-Sample thực nghiệm đều trả về `null` hoặc `UNAVAILABLE`.
7. **Toán học Signed R:R**: Giữ nguyên công thức chi phí và hình học có hướng (SHORT: TP < Entry < SL; LONG: SL < Entry < TP), không dùng Math.abs để che giấu kế hoạch sai phía.

---

## 5. Kết Luận Nghiệm Thu (Acceptance Conclusion)

Phiên bản **V14.4.2** đã hoàn thành trọn vẹn toàn bộ các mục tiêu đề ra:
- Sửa code thật và kết nối caller thật trên toàn bộ pipeline backend lẫn giao diện frontend.
- Vượt qua toàn bộ 56 test cases bắt buộc từ D01–D12, R01–R05, M01–M04, E01–E09, S01–S06, T01–T08, T09–T16, T17–T20.
- Đạt 100% tỷ lệ pass ở tất cả 8 bước nghiệm thu, không có bất kỳ placeholder, mock rỗng hay `assert True` giả định nào.
- Xử lý triệt để hai ca lỗi từ ảnh người dùng: từ chối TP SHORT sai phía Entry và loại bỏ giá trị rủi ro/khối lượng giả định; đồng thời cô lập hoàn toàn chế độ draft khỏi kịch bản chưa có kế hoạch và dọn dẹp lỗi HTTP 500.
- Đáp ứng đầy đủ các tiêu chuẩn kiểm thử tự động, khả năng tiếp cận (accessibility) và hiệu năng mượt mà trên cả desktop lẫn mobile.
