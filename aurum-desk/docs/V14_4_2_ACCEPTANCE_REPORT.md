- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Base Commit**: `7b92773` — fix(v14.4.2): add day_audits.updated_at_ms and forecast_observations to migrate.py
- **Runtime Baseline**: `6fe7a70` — fix(v14.4.2): reconcile financial provenance and causal realtime execution
- **Thời gian nghiệm thu**: 2026-10-12
- **Môi trường thực thi**: macOS (Darwin 24.1.0), Node.js v22.22.3, Python 3.13.0, Chrome Headless (Playwright)
- **Kết quả tổng thể**: **PASS (100% các bước và 85/85 test cases bắt buộc đều ĐẠT)**

---

## 1. Tóm Tắt Kết Quả Các Bước Kiểm Tra (Step Summary)

Toàn bộ quá trình kiểm tra được điều phối và xác nhận độc lập qua script `tools/run_v14_4_2_acceptance.py`:

| Bước kiểm tra (Step) | Lệnh thực thi | Thời gian | Kết quả | Chi tiết / Log |
|---|---|---|---|---|
| **1. Backend Targeted** | `pytest tests/test_v14_4_2_*.py tests/test_v14_4_1_*.py tests/test_v14_3_*.py tests/test_realtime_*.py tests/test_forecast_*.py tests/test_migration_*.py -q` | 11.4s | **PASS** | 233 passed, 0 failed, 1 warning |
| **2. Frontend Typecheck** | `npx tsc -b` | 4.0s | **PASS** | 0 error |
| **3. Frontend Lint** | `npx oxlint` | 0.4s | **PASS** | 0 error, 29 warnings |
| **4. Frontend Vitest** | `npx vitest run --reporter=default --reporter=junit` | 1.6s | **PASS** | 26 test files, 226 tests passed |
| **5. Frontend Build** | `npm run build` | 4.4s | **PASS** | Vite production bundle build hoàn tất |
| **6. Browser E2E** | `npx playwright test (5 suites)` | 95.6s | **PASS** | 47 browser tests passed (21 closure, 5 soak, 4 mobile, 9 sessions, 8 selection source) |
| **7. Benchmark Latency** | `run_v14_4_chart_acceptance.py --cycles 600` | 4.9s | **PASS** | 600 chu kỳ hoàn tất, p50: 7.36ms, p95: 8.37ms, max: 58.79ms |
| **8. Git Diff Check** | `git diff --check` | 0.0s | **PASS** | Không có trailing whitespace hoặc merge conflict markers |
| **OVERALL** | Toàn bộ 8 gate | ~122s | **PASS** | Đủ 85/85 required case IDs bắt buộc đạt 100% |

---

## 2. Bảng Ma Trận Chi Tiết 85 Test Cases Bắt Buộc (Case ID Matrix)

Tất cả 85 case IDs bắt buộc được kiểm thử thực tế trên hệ thống backend cô lập và trình duyệt Playwright thực (không dùng mock rỗng hay `assert True`):

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
| **R01** | Chart tab đang mở, WebSocket kết nối | Đóng socket giả lập ngắt mạng | Socket tự động reconnect theo backoff, generation tăng | Reconnect thành công (`isOpen = true`, `generation > genBefore`), subscriber count ổn định | `v14_4_2_realtime_soak.e2e.ts` |
| **R02** | Client đang lắng nghe domain events | Dispatch domain event với revision nhảy cóc (gap) | Kích hoạt debounce GET `/smc/scenarios` để đồng bộ lại dữ liệu | Endpoint GET `/smc/scenarios` được gọi chính xác và cập nhật | `v14_4_2_realtime_soak.e2e.ts` |
| **R03** | Ứng dụng đang chạy có dữ liệu | Chuyển đổi tab/selection liên tục 100 chu kỳ | Không phát sinh uncaught exception, không rò rỉ bộ nhớ hay freeze UI | 0 page errors, giao diện phản hồi mượt mà | `v14_4_2_realtime_soak.e2e.ts` |
| **R04** | Feeder live quote đang gửi tick | Quan sát giá thị trường hiển thị và queue | Giá vàng cập nhật số dương hữu hạn, định dạng đúng, queue/subscribers bounded | Hiển thị giá chuẩn xác, subscribers < 50 | `v14_4_2_realtime_soak.e2e.ts` |
| **R05** | ChartComponent mount/unmount | Chuyển đổi qua lại các tab 20 lần (soak) | Số lượng subscribers quay về đúng baseline, không rò rỉ | Final subscribers == initial subscribers (exact baseline) | `v14_4_2_realtime_soak.e2e.ts` |

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
| **T09** | Màn hình chính | Bấm "Vẽ Thử Kế Hoạch (Draft)" và chọn Bán | Tạo draft SHORT độc lập, assert giá trị số entry/sl/tp/qty/risk, 0 mutation | State: `draft`, direction: `SHORT`, 0 mutations, finite values | `v14_4_2_selection_source.e2e.ts` |
| **T10** | Preload draft RR rồi chọn kịch bản WAITING_BREAK không plan | Nhấp chọn kịch bản WAITING_BREAK trên sidebar | Header chuyển thành SCENARIO và xóa sạch hộp R:R | `readRR() == null`, không lẫn draft cũ | `v14_4_2_selection_source.e2e.ts` |
| **T11** | Endpoint chart evidence trả về HTTP 500 | Nhấp chọn kịch bản sau khi đã có RR trước đó | Hiển thị thông báo lỗi rõ ràng, xóa sạch R:R overlay cũ, không giữ bằng chứng cũ | Banner lỗi xuất hiện, 0 stale R:R overlay | `v14_4_2_selection_source.e2e.ts` |
| **T12** | Đang xem setup có kế hoạch hợp lệ | Chuyển sang chọn kịch bản WAITING_BREAK không plan | Xóa ngay lập tức hộp R:R của setup cũ, không giữ R:R cũ | Hộp R:R được dọn sạch hoàn toàn | `v14_4_2_selection_source.e2e.ts` |
| **T13** | Setup SHORT hợp lệ trên Upcoming Tab | Nhấp "Chép sang Bản Nháp (Draft)" | Tạo bản nháp mới mang cùng các mức giá và khối lượng nhưng trạng thái uncertified | State chuyển sang `draft`, giữ nguyên bộ giá gốc | `v14_4_2_selection_source.e2e.ts` |
| **T14** | Kịch bản có plan SMC đã được chứng nhận | Chọn kịch bản lên biểu đồ | Hộp R:R hiển thị ở chế độ `readOnly: true` (không cho kéo sửa giá) | `readOnly: true` bảo vệ kế hoạch chuẩn SMC | `v14_4_2_selection_source.e2e.ts` |
| **T15** | Chuyển đổi nhanh A -> B trong khi A phản hồi chậm | Bấm chọn kịch bản A rồi chuyển đổi tab nhanh | Không xuất hiện lỗi unhandled, 0 race condition | 0 lỗi hệ thống, giao diện mượt | `v14_4_2_selection_source.e2e.ts` |
| **T16** | Đang trong chế độ Draft | Bấm chuyển đổi giữa Mua và Bán | Cập nhật hướng draft tức thì và cho phép chỉnh sửa mức giá | Chuyển đổi linh hoạt giữa LONG và SHORT | `v14_4_2_selection_source.e2e.ts` |

### Nhóm 8: Financial Context & Pricing Correctness (FIN01 – FIN05)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **FIN01** | DayAudit equity 2000 USDT | Gọi `load_plan_financial_context` & `build_setup_display_plan` | Vốn 2000 USDT được sử dụng, quantity = 0.56 oz, risk = 4.94 USDT | Khớp chính xác vốn 2000, loại bỏ hoàn toàn hardcoded 1000 | `test_v14_4_2_financial_context_regression.py` |
| **FIN02** | Setup có các trường tài chính mang giá trị None | Đánh giá và hiển thị kế hoạch | Không văng TypeError, không fake 0, `financial` trả về None có kiểm soát | 0 TypeError, financial nullable an toàn | `test_v14_4_2_financial_context_regression.py` |
| **FIN03** | Upcoming API nhận danh sách setup gồm hàng lỗi và hàng chuẩn | Gọi endpoint `GET /api/v1/smc/upcoming` | Cả 2 hàng đều có mặt trong response; hàng lỗi trả về degraded item thay vì bị omit | 0 hàng bị omit bởi `continue`, đầy đủ diagnostics | `test_v14_4_2_financial_context_regression.py` |
| **FIN04** | Kiểm tra các mức vốn 500, 1000, 2000 và LONG mirror | Tính toán kế hoạch và kích thước vị thế | Kích thước tỷ lệ thuận với equity, LONG/SHORT tính toán đối xứng | Vốn 500: 0.14 oz, 1000: 0.28 oz, 2000: 0.56 oz; LONG mirror chuẩn | `test_v14_4_2_financial_context_regression.py` |
| **FIN05** | Frozen order lưu trữ lúc arm/fill | Đổi cấu hình vốn / phí / đòn bẩy hiện tại | Lệnh lịch sử giữ nguyên snapshot vốn cũ, không bị reprice bởi cấu hình mới | Lệnh cũ bảo toàn 100% snapshot ban đầu | `test_v14_4_2_financial_context_regression.py` |

### Nhóm 9: 15M Engine Liquidity & Causal Pivot Selection (LIQ01 – LIQ03)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **LIQ01** | Engine 15M output thực tế | Chuyển qua `adapt_legacy_liquidity_candidates` | Làm giàu đủ confirmed_at/is_confirmed từ pivot đóng, không bị lọc sạch | Adapter chấp nhận candidate hợp lệ | `test_v14_4_2_provisional_plan_regression.py` |
| **LIQ02** | Mapping raw swings & dealing ranges | Xử lý SWING HIGH/LOW và DEALING_HIGH/LOW | Phân biệt pivot xác nhận với dealing range | Đúng taxonomy và trạng thái causal | `test_v14_4_2_provisional_plan_regression.py` |
| **LIQ03** | Chuẩn hóa Timeframe D/1D và M1..H4 | Tính toán known_at và confirmation cutoff | Đúng duration theo TF, không fallback bừa 300000ms | Khớp chuẩn xác cutoff nến đóng | `test_v14_4_2_provisional_plan_regression.py` |

### Nhóm 10: Realtime Quote Ordering & Execution Worker (RT01 – RT09)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **RT01** | Hai quote nhận lệch thứ tự (Quote mới -> Quote cũ) | Dispatch qua `classify_quote_order` | Quote cũ phân loại LATE_EVIDENCE, latest price không bị lùi | Latest price giữ monotonic tăng dần | `test_realtime_source_ordering.py` |
| **RT02** | Quote cùng timestamp nhưng khác payload | Dispatch kiểm tra | Nhận diện cập nhật hợp lệ, không đánh đồng DUPLICATE | Bảo toàn tick có bid/ask thay đổi | `test_realtime_source_ordering.py` |
| **RT03** | Reconnect và REST fallback | Chuyển nguồn dữ liệu | Epoch transport mới nhưng source watermark không reset về 0 | Tránh replay duplicate fill | `test_realtime_source_ordering.py` |
| **RT04** | Late quote đến sau khi order mở | Reconcile late quote | Không áp dụng TP/SL hồi tố cho vị thế chưa tồn tại tại source time | Causal timeline bảo toàn | `test_realtime_source_ordering.py` |
| **RT05** | Late quote cho position đang mở | Reconcile late quote | Lưu audit và xử lý theo timeline thay vì đóng ở now | Khớp đúng giá/thời gian | `test_realtime_source_ordering.py` |
| **RT06** | Execution worker chạy với DB trễ | Xử lý tick trong single worker thread | Không block asyncio event loop, 1 in-flight duy nhất | Event loop phản hồi mượt mà | `test_realtime_execution_worker.py` |
| **RT07** | Queue tick bị tràn (overflow) | Đưa vào hàng đợi quá tải | Đánh dấu gap, chặn lệnh mới, bảo vệ chronology | Entry bị block an toàn | `test_realtime_execution_worker.py` |
| **RT08** | Candle persistence retry overflow | Lưu trữ nến vào SQLite | Đánh dấu context incomplete, không nuốt nến âm thầm | Gap telemetry ghi nhận chính xác | `test_realtime_execution_worker.py` |
| **RT09** | Worker gặp lỗi / cancellation | Huỷ tác vụ và khởi động lại | Rollback transaction sạch sẽ, shutdown có kiểm soát | 0 task leak, trạng thái an toàn | `test_realtime_execution_worker.py` |

### Nhóm 11: Persistent Forecast Snapshots & Causal Outcomes (FC01 – FC12)
| Case ID | Tiền điều kiện (Preconditions) | Thao tác (Actions) | Kết quả kỳ vọng (Expected) | Kết quả thực tế (Actual) | Artifact / File kiểm thử |
|---|---|---|---|---|---|
| **FC01** | Dự đoán phiên giao dịch | Tạo snapshot trước kết quả | Lưu snapshot bất biến, không biết trước tương lai | Snapshot lưu thành công trước outcome | `test_forecast_forward_observation.py` |
| **FC02** | Payload dự đoán không đổi | Ghi nhận snapshot lần 2 | Dedupe nội dung theo digest, không sinh bản ghi trùng | Trả về ID snapshot hiện hữu | `test_forecast_forward_observation.py` |
| **FC03** | Thay đổi điều kiện/mục tiêu nến đóng | Ghi nhận dự đoán mới | Tăng revision và bảo toàn snapshot cũ | Revision mới sinh ra, snapshot cũ giữ nguyên | `test_forecast_forward_observation.py` |
| **FC04** | Quan sát lịch sử trước as_of_ms | Đánh giá outcome | Từ chối quan sát có lookback sai quy tắc | Rejection thành công | `test_forecast_forward_observation.py` |
| **FC05** | Hết hạn phiên (horizon expiry) | Đánh giá khi chưa chạm target/invalidation | Outcome là `EXPIRED` | Outcome: `EXPIRED` | `test_forecast_forward_observation.py` |
| **FC06** | Giá chạm target trước xác nhận nến | Đánh giá kết quả | Là `WATCHED_LIQUIDITY_TOUCH`, không tính là WIN trade | Không ngộ nhận win trade | `test_forecast_forward_observation.py` |
| **FC07** | Đã xác nhận, chạm target trước invalidation | Đánh giá kết quả | `CONFIRMED_TARGET_BEFORE_INVALIDATION` | Outcome: Target reached hợp lệ | `test_forecast_forward_observation.py` |
| **FC08** | Giá chạm invalidation trước target | Đánh giá kết quả | `INVALIDATED_BEFORE_TARGET` | Outcome: Invalidation ghi nhận | `test_forecast_forward_observation.py` |
| **FC09** | Nến đồng thời chạm cả target và invalidation | Đánh giá kết quả | Đánh dấu `AMBIGUOUS`, không tự chọn WIN | Outcome: `AMBIGUOUS` minh bạch | `test_forecast_forward_observation.py` |
| **FC10** | Dữ liệu bị gián đoạn (feed gap) | Đánh giá trong khoảng gap | Outcome là `DATA_UNAVAILABLE` | Không giả định thắng thua | `test_forecast_forward_observation.py` |
| **FC11** | Mô hình chưa hiệu chuẩn OOS | Kiểm tra trường xác suất | Trả về `UNAVAILABLE` / `null`, không tạo % giả | Xác suất trung thực null | `test_forecast_forward_observation.py` |
| **FC12** | Lỗi SQL savepoint khi lưu outcome | Ghi nhận lỗi DB | Best-effort isolation, không làm hỏng transaction chính | Transaction an toàn | `test_forecast_forward_observation.py` |

---

## 3. Đo Lường Hiệu Năng & Độ Trễ (Benchmark Results)

Kết quả đo đạc từ `tools/run_v14_4_chart_acceptance.py` trên 600 chu kỳ nến thực:

- **Số chu kỳ kiểm tra (Cycles)**: 600 chu kỳ
- **Số chu kỳ xác nhận cấu trúc thành công**: 600/600 chu kỳ
- **Tổng thời gian thực thi (Total Time)**: 5.14 giây
- **Thông lượng (Throughput)**: **116.7 chu kỳ / giây**
- **Độ trễ đánh giá kịch bản (Latency)**:
  - **p50**: 7.36 ms
  - **p95**: 8.37 ms (ngưỡng trần quy định: 250.0 ms)
  - **Max**: 58.79 ms
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
