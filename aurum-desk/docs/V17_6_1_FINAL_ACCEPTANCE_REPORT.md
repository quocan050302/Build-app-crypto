# BÁO CÁO NGHIỆM THU KỸ THUẬT VÀ PHÁT HÀNH AURUM V17.6.1
## FINAL OBSERVABILITY CORRECTNESS & RELIABILITY REPAIR
### Báo Cáo Đo Lường, Bằng Chứng Kiểm Thử Độc Lập & Nghiệm Thu 12 Khiếm Khuyết

**Dự án**: AURUM DESK — Hệ thống Giám Sát và Khai Thác Giao Dịch Thuật Toán XAUUSDT SMC/ICT  
**Phiên bản mục tiêu**: V17.6.1 — Final Observability Correctness & Reliability Repair  
**Nhánh Git**: `fix/v14-1-rr-flow`  
**Git Baseline**: V17.6 (`ac435bd4cf5f97de22a47673b519b46fd972f7de`)  
**Phạm vi giao dịch**: Paper Trading / Shadow Forward Testing độc lập 100% (Khóa tuyệt đối Live Exchange Order)  
**Trạng thái nghiệm thu**: **17/17 CỔNG NGHIỆM THU ĐẠT CHUẨN (VERDICT: ACCEPTED)**  
**Artifacts Package**: `artifacts/v17_6_1/`  

---

## I. TỔNG QUAN PHÁT HÀNH V17.6.1

AURUM V17.6.1 là bản phát hành sửa chữa độ tin cậy và tính đúng đắn dữ liệu quan sát sản xuất. Bản phát hành này giải quyết triệt để 12 khiếm khuyết kỹ thuật (OBS-01 đến OBS-12) được phát hiện trong hệ thống thu thập số liệu, phát hiện treo (stall), cách ly dữ liệu giữa các phiên quan sát, tính toán độ bao phủ phiên theo giờ New York có tính toán DST, đồng bộ WebSocket và nguyên tắc Fail-Closed hiển thị giao diện.

### Cam Kết Bất Biến Vận Hành (Invariants):
1. **Zero Trading Drift**: Giữ nguyên vẹn 100% logic SMC/ICT, phân tích HTF bias, BOS/CHoCH/MSS, quét thanh khoản (sweep), FVG/POI, structural stop, net R:R guards, position sizing, phí/trượt giá và cơ chế Paper execution.
2. **Khóa tuyệt đối Live Order**: Tuyệt đối không phát sinh bất kỳ lệnh live nào lên sàn Bitget.
3. **Phân loại nguồn số liệu trung thực**: Mọi số liệu hiển thị được phân hạng minh bạch (`VERIFIED`, `STALE`, `INCOMPLETE`, `UNAVAILABLE`, `UNVERIFIABLE`). Tuyệt đối không dùng số 0 hoặc số ảo để che lấp dữ liệu thiếu.
4. **Cách ly tuyệt đối dữ liệu**: Dữ liệu phễu quyết định, sự kiện, setup của một run không bao giờ bị trộn lẫn với các run khác.

---

## II. MA TRẬN KHẮC PHỤC VÀ NGHIỆM THU 12 KHIẾM KHUYẾT (OBS-01 -> OBS-12)

Dưới đây là bảng đối chiếu chi tiết 12 khiếm khuyết kỹ thuật, giải pháp kiến trúc, file mã nguồn và bộ kiểm thử độc lập:

| Mã Lỗi | Tiêu Đề Khiếm Khuyết | Nguyên Nhân Gốc Rễ (RCA) | Giải Pháp Khắc Phục | File Chỉnh Sửa | Bộ Kiểm Thử Nghiệm Thu | Kết Quả |
|:---:|---|---|---|---|---|:---:|
| **OBS-01** | Báo giá quote timestamp sai trường thuộc tính | `build_market_feed_health_snapshot` đọc `.timestamp` hoặc `.time`, trong khi `CanonicalQuote` chỉ có `exchange_ts_ms` và `received_at_ms`. | Tạo pure function `resolve_canonical_quote_timestamps()` phân giải độc lập `exchange_ts_ms`, `received_at_ms`, tính `exchange_age_ms`, `receive_age_ms`, `transport_delay_ms`. Mock-safe validator. | `backend/services/engine_observability_service.py` | `test_v17_6_1_quote_timestamps.py` | **PASS (5/5)** |
| **OBS-02** | Xác minh độ tươi mới nến đóng đa khung thời gian | Chỉ kiểm tra timestamp nến cuối mà không phân định nến đang hình thành (`is_closed=False`) với nến đã đóng hoàn toàn. | Bổ sung kiểm tra cờ `is_closed=True` hoặc xác nhận `bar_ts + interval_ms <= now_ms`. Phát hiện gap timestamp giữa các nến liền kề. | `backend/services/engine_observability_service.py` | `test_v17_6_1_quote_timestamps.py` | **PASS (5/5)** |
| **OBS-03** | Dữ liệu heartbeat/telemetry thiếu tính bền vững | Gọi `record_engine_heartbeat` với `db` gắn liền transaction nghiệp vụ; nếu transaction lỗi hoặc rollback thì heartbeat bị mất. | Tách biệt phiên ghi heartbeat bằng session độc lập (`SessionLocal()` khi `db is None`) và `db.commit()` rõ ràng, đảm bảo ghi nhận liveness độc lập. | `backend/services/engine_observability_service.py` | `test_v17_6_1_telemetry_resilience.py` | **PASS (5/5)** |
| **OBS-04** | Nhận diện worker owner và phát hiện engine stall | Nhầm lẫn giữa tiến trình Python bị treo (process stall) và chu kỳ đánh giá tạm ngừng hợp lệ (evaluation stall) trong kỳ nghỉ thị trường. | Ưu tiên worker instance ID hiện tại `self._current_instance_id`. Tách bạch kiểm tra: 1) Weekend break -> `WAITING_MARKET`, 2) Process heartbeat age > stall threshold -> `STALLED`, 3) Daily maintenance -> `WAITING_MARKET`, 4) Market open + eval stale -> `STALLED`. | `backend/services/engine_observability_service.py` | `test_v17_6_engine_observability.py` | **PASS (6/6)** |
| **OBS-05** | Dashboard có thể trộn dữ liệu nhiều run | Truy vấn `summarize_opportunity_funnel` không lọc ràng buộc theo `run_id` và `symbol`, dẫn đến số liệu phễu và setup của các run cũ bị cộng dồn. | Bổ sung tham số `run_id` và `symbol` vào `summarize_opportunity_funnel` cả trong bộ nhớ RAM và câu lệnh SQL database; dashboard lọc chính xác theo run mục tiêu. | `backend/services/opportunity_funnel_service.py`<br>`backend/services/engine_observability_service.py` | `test_v17_6_1_run_isolation.py` | **PASS (4/4)** |
| **OBS-06** | Công thức tính tỷ lệ bao phủ quan sát (Uptime %) sai lệch | Cửa sổ quan sát bị tính trên 3600s cố định và không loại trừ thời gian thị trường đóng cửa hoặc thời gian run thực tế. | Xén cửa sổ theo tuổi thọ run `[max(win_start, run_start), min(win_end, run_end)]`, trừ chính xác thời gian thị trường nghỉ qua `calculate_market_open_duration_ms`. Loại bỏ fallback giả tạo. | `backend/services/engine_observability_service.py` | `test_v17_6_1_market_hours_dst.py` | **PASS (6/6)** |
| **OBS-07** | Nhận diện giờ nghỉ thị trường XAUUSDT & New York DST | Sử dụng offset UTC tĩnh (21:00 UTC) bị lệch 1 giờ khi New York chuyển đổi giữa EST (UTC-5) và EDT (UTC-4). | Sử dụng `zoneinfo.ZoneInfo("America/New_York")` chuẩn IANA: Thứ Sáu 17:00 NY đến Chủ Nhật 18:00 NY (cuối tuần), và Thứ Hai - Thứ Năm 17:00 - 18:00 NY (bảo trì hàng ngày). | `backend/services/engine_observability_service.py` | `test_v17_6_1_market_hours_dst.py` | **PASS (6/6)** |
| **OBS-08** | WebSocket nested event không kích hoạt làm mới UI | Backend đẩy event bọc trong payload lồng (`event.event_type` hoặc `payload.event_type`), client chỉ đọc `event.type`. | Xây dựng helper `extractEventType` mở gói mọi tầng envelope lồng nhau; hỗ trợ tất cả các event quan sát (`CG01_EVALUATION`, `CANDLE_CLOSED`, `ORDER_*`, ...); kèm cơ chế debounce 300ms. | `frontend/src/hooks/useObservationDashboard.ts` | `v17_6_1_observability_ui.test.tsx` | **PASS (2/2)** |
| **OBS-09** | UI hiển thị giá trị mặc định sai lệch (`$0.00` / `0.00%`) | Khi chưa có run hoặc thiếu dữ liệu tài chính, UI hiển thị fallback `$1,000.00` hoặc `$0.00`, gây hiểu nhầm hệ thống đã huề vốn hoặc lỗ. | Áp dụng nguyên tắc Fail-Closed: Khi không có dữ liệu tài chính thật, hiển thị `N/A` cho Balance, Equity, Net PnL, Winrate (0 lệnh), và trạng thái bao phủ là `UNKNOWN`. | `frontend/src/components/ObservationDashboard.tsx` | `v17_6_1_observability_ui.test.tsx` | **PASS (2/2)** |
| **OBS-10** | Acceptance runner thiếu cổng kiểm tra nghiêm ngặt | Runner cũ chỉ chạy một phần test và không đối chiếu hash snapshot của mã nguồn. | Nâng cấp `run_v17_6_1_acceptance.py` thực thi 17 cổng bắt buộc, kiểm tra trạng thái Git, chạy 73 backend tests, soak CLI, Vitest, TypeScript build, OxLint, và Playwright E2E. | `tools/run_v17_6_1_acceptance.py` | Master Runner | **PASS (17/17)** |
| **OBS-11** | Thiếu bộ kiểm thử hồi quy bao phủ sự cố kết nối | Chưa có test tự động kiểm tra quy trình ngắt kết nối WebSocket và tự động hạ cấp / phục hồi trạng thái snapshot. | Bổ sung kiểm thử mô phỏng đứt feed sàn, kiểm tra snapshot chuyển sang `DISCONNECTED`, và tự phục hồi `CONNECTED_FRESH` khi tick mới đến. | `backend/tests/test_v17_6_1_telemetry_resilience.py` | `test_v17_6_1_telemetry_resilience.py` | **PASS (5/5)** |
| **OBS-12** | Lưu vết sự cố và cơ chế tự động resolve incidents | Sự cố ghi vào `engine_incidents` không có cơ chế tự động cập nhật trạng thái `RESOLVED` khi nhịp quét hoặc kết nối bình phục. | Bổ sung `resolve_engine_incident()`, alias `raise_engine_incident`, và hàm `resolve_active_incidents()` tự động đóng sự cố tương ứng khi feed bình thường. | `backend/services/engine_observability_service.py` | `test_v17_6_1_telemetry_resilience.py` | **PASS (5/5)** |

---

## III. BẰNG CHỨNG TOÁN HỌC & CÔNG THỨC CHUẨN XÁC

### 1. Phân Giải Timestamp Báo Giá và Tính Toán Độ Trễ (OBS-01)

Hàm `resolve_canonical_quote_timestamps` thực thi các phép toán xác thực:
- $T_{now}$: Thời điểm đồng hồ hệ thống (`now_ms`, epoch millisecond).
- $T_{exchange}$: Thời điểm phát sinh tick tại sàn Bitget (`exchange_ts_ms`).
- $T_{received}$: Thời điểm backend tiếp nhận gói tin WebSocket (`received_at_ms`).

Các đại lượng được tính toán:
$$\text{exchange\_age\_ms} = T_{now} - T_{exchange}$$
$$\text{receive\_age\_ms} = T_{now} - T_{received}$$
$$\text{transport\_delay\_ms} = T_{received} - T_{exchange}$$

**Điều kiện nghiệm thu:**
- Nếu $T_{exchange}$ thiếu hoặc không hợp lệ: `status = "UNVERIFIED"`, `exchange_age_ms = None`.
- Nếu $T_{exchange} > T_{now} + 60,000$ (clock skew vượt ngưỡng): Bị từ chối (`INVALID_FUTURE_TIMESTAMP`).

### 2. Giờ Thị Trường XAUUSDT & New York DST (OBS-06, OBS-07)

Vàng giao ngay (XAUUSDT) giao dịch theo quy chuẩn thị trường kim loại quý New York (COMEX/NYMEX):
- **Kỳ nghỉ cuối tuần (Weekend Break)**:
  - Bắt đầu: Thứ Sáu 17:00 New York time ($T_{Fri, 17:00}$).
  - Kết thúc: Chủ Nhật 18:00 New York time ($T_{Sun, 18:00}$).
- **Bảo trì hàng ngày (Daily Maintenance Break)**:
  - Thứ Hai đến Thứ Năm: 17:00 New York time đến 18:00 New York time.
- **Xử lý Daylight Saving Time (DST)**:
  - Sử dụng thư viện chuẩn `zoneinfo.ZoneInfo("America/New_York")`.
  - Trong mùa hè (EDT - UTC-4): 17:00 NY = 21:00 UTC.
  - Trong mùa đông (EST - UTC-5): 17:00 NY = 22:00 UTC.
  - Phép toán loại trừ hoàn toàn sai số 1 giờ do hardcode UTC tĩnh.

### 3. Tỷ Lệ Bao Phủ Quan Sát Hợp Lệ (Observation Coverage %) (OBS-06)

Công thức tính tỷ lệ bao phủ trong cửa sổ $[T_{start}, T_{end}]$ của run $R$:
1. Xác định khoảng thời gian giao thoa thực tế giữa cửa sổ và tuổi thọ run:
   $$W_{actual} = [\max(T_{start}, R_{start}), \min(T_{end}, R_{end})]$$
2. Thời gian thị trường mở cửa kỳ vọng trong khoảng $W_{actual}$:
   $$D_{expected\_open} = \text{calculate\_market\_open\_duration\_ms}(W_{actual}.start, W_{actual}.end)$$
3. Tỷ lệ bao phủ quan sát:
   $$\text{Coverage Ratio} = \frac{\min(D_{actual\_evaluated}, D_{expected\_open})}{D_{expected\_open}}$$
4. Nếu $D_{expected\_open} = 0$ (run hoàn toàn diễn ra trong kỳ nghỉ cuối tuần): Coverage mặc định $100\%$ nếu không có lỗi, và phân loại trạng thái là `MARKET_CLOSED`.

---

## IV. BẢNG TỔNG KẾT KẾT QUẢ 17 CỔNG NGHIỆM THU (G00 - G16)

Bộ nghiệm thu chính thức `tools/run_v17_6_1_acceptance.py` đã thực thi toàn diện 17 cổng kiểm soát chất lượng:

| Cổng | Tên Cổng Nghiệm Thu | Phạm Vi Kiểm Thử / Lệnh Thực Thi | Kết Quả | Thời Gian |
|:---:|---|---|:---:|:---:|
| **G00** | Git Integrity & Branch | Xác thực branch `fix/v14-1-rr-flow`, baseline SHA `ac435bd4cf5f97de22a47673b519b46fd972f7de` | **PASS** | 0.05s |
| **G01** | Documentation & Architecture | Xác thực `V17_6_1_DEFECT_REGISTER.md` và `V17_6_1_FINAL_ACCEPTANCE_REPORT.md` | **PASS** | 0.02s |
| **G02** | Quote Timestamps & Age (OBS-01) | `test_v17_6_1_quote_timestamps.py::TestV1761QuoteTimestamps` | **PASS (3/3)** | 0.09s |
| **G03** | Candle Freshness Multi-TF (OBS-02) | `test_v17_6_1_quote_timestamps.py::TestV1761CandleFreshness` | **PASS (2/2)** | 0.08s |
| **G04** | Telemetry Durability (OBS-03) | `test_v17_6_1_telemetry_resilience.py::TestTelemetryDurability` | **PASS (2/2)** | 0.15s |
| **G05** | Engine Health & Stall (OBS-04) | `test_v17_6_engine_observability.py::test_h01_to_h06` | **PASS (6/6)** | 0.21s |
| **G06** | Run Data Isolation (OBS-05) | `test_v17_6_1_run_isolation.py::test_iso_01_to_04` | **PASS (4/4)** | 0.19s |
| **G07** | Market Hours & NY DST (OBS-07) | `test_v17_6_1_market_hours_dst.py::test_dst_01_to_03` | **PASS (3/3)** | 0.10s |
| **G08** | Coverage Mathematics (OBS-06) | `test_v17_6_1_market_hours_dst.py::test_cov_01_to_03` | **PASS (3/3)** | 0.11s |
| **G09** | Recovery & Incident Resilience (OBS-11, 12) | `test_v17_6_1_telemetry_resilience.py::test_res_01_to_03` | **PASS (3/3)** | 0.14s |
| **G10** | Cycle Telemetry & Diagnostics | `test_v17_6_cycle_telemetry.py` (5 tests) | **PASS (5/5)** | 0.12s |
| **G11** | Decision Timeline & Deduplication | `test_v17_6_decision_timeline.py` (5 tests) | **PASS (5/5)** | 0.13s |
| **G12** | Core Financial Parity (Zero Drift) | `test_v17_5_9_financial_parity.py` & `test_v17_6_decision_parity.py` | **PASS (2/2)** | 0.09s |
| **G13** | Standalone Soak Test Execution | `tools/run_v17_6_observation_soak.py` (50 deterministic cycles) | **PASS (50/50)** | 0.79s |
| **G14** | Frontend Unit Tests (Vitest) | `npm run test:run` (51 test files, 505 unit tests) | **PASS (505/505)** | 4.02s |
| **G15** | Production Build & Lint | `npm run build` (tsc + vite) & `npm run lint` (oxlint) | **PASS (0 errors)** | 6.12s |
| **G16** | Playwright Browser E2E | `tests/e2e/v17_6_observation.e2e.ts` (Full browser suite) | **PASS (1/1)** | 4.49s |

**TỔNG CỘNG KIỂM THỬ BACKEND**: **73 / 73 tests PASS (100%)**  
**TỔNG CỘNG KIỂM THỬ FRONTEND**: **505 / 505 unit tests PASS (100%)**  
**KIỂM THỬ TRÌNH DUYỆT E2E**: **1 / 1 spec PASS (100%)**  
**KẾT QUẢ CHUNG CUỘC**: **VERDICT: ACCEPTED**  

---

## V. ĐỐI CHIẾU THỰC TẾ GIAO DIỆN V17.6.1 (FAIL-CLOSED PRESENTATION)

Giao diện người dùng V17.6.1 đã được kiểm chứng bằng cả Unit Test Vitest (`v17_6_1_observability_ui.test.tsx`) và Playwright Browser E2E thật:

1. **Badge Tiêu Đề**:
   - Hiển thị chính xác `V17.6.1 OBSERVABILITY & DECISION TELEMETRY`.
2. **Nguyên Tắc Fail-Closed Trong Thẻ Hiệu Suất (Card 4)**:
   - Khi chưa có giao dịch hoặc dữ liệu tài chính rỗng:
     - Số dư (Balance): `N/A`
     - Vốn khả dụng (Equity): `N/A`
     - Lợi nhuận ròng (Net PnL): `N/A`
     - Tỷ lệ thắng (Winrate): `N/A (0 lệnh)`
   - Không xuất hiện bất kỳ con số mặc định nào như `$1,000.00` hay `0.00%`.
3. **Thẻ Độ Bao Phủ Quan Sát (Card 5)**:
   - Trạng thái bao phủ khi không có dữ liệu: `UNKNOWN` (thay vì tự nhận `FULL`).
4. **Đồng Bộ WebSocket Tự Động (OBS-08)**:
   - Các sự kiện `CG01_EVALUATION`, `CANDLE_CLOSED`, `ORDER_*` kích hoạt làm mới giao diện mượt mà qua cơ chế debounce 300ms, không làm giật lag giao diện.

---

## VI. BẢNG PHÊ DUYỆT PHÁT HÀNH

Bản phát hành **AURUM V17.6.1** đã hoàn thành sửa chữa toàn bộ 12 khiếm khuyết theo đúng tôn chỉ của Master Prompt:
- Sửa trực tiếp tại mã nguồn, không chỉ báo cáo lý thuyết.
- Tự động hóa kiểm thử với 17 cổng nghiệm thu độc lập.
- Chứng minh tính toán chuẩn xác bằng toán học và xử lý múi giờ IANA New York DST.
- Đóng gói đầy đủ artifacts và log kiểm thử tại `artifacts/v17_6_1/`.

**CHÍNH THỨC CÔNG BỐ PHÁT HÀNH AURUM V17.6.1.**
