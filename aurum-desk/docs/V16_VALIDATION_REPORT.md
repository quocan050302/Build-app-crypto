# Báo cáo Kiểm toán và Nghiệm thu Toàn diện V16: SMC Liquidity Opportunity Engine

**Dự án:** Aurum Desk - SMC Liquidity Opportunity Engine  
**Mã commit kiểm toán:** Trên nhánh `fix/v14-1-rr-flow`  
**Ngày báo cáo:** 15/10/2026 (Asia/Ho_Chi_Minh)  
**Tác giả:** Đội ngũ Kỹ thuật & Kiểm toán Độc lập  

---

## Tóm tắt Điều hành (Executive Summary)

Bản phát hành **V16** tập trung mở rộng và chuẩn hóa năng lực phát hiện cơ hội SMC (Smart Money Concepts) trên luồng Scalp M1, hướng tới mục tiêu tần suất **2–3 filled paper trades mỗi ngày giao dịch đủ điều kiện** với **giới hạn cứng (hard daily cap) tuyệt đối không quá 3 fills/ngày**, đồng thời **bảo toàn nguyên vẹn 100% các nguyên tắc kiểm soát rủi ro, luồng giao dịch hiện tại, và tính trung thực của dữ liệu**.

### Các kết quả then chốt đã đạt được:
1. **Triệt tiêu lỗi timestamp CHOCH/BOS (`occurred_at_ms: 0`)**: Áp dụng hàm trích xuất thời gian nến chuẩn hóa `candle_open_ms()`, gắn đúng mốc thời gian nến M1 phá vỡ thực tế.
2. **Khắc phục lỗi hiển thị Chart Coordinate 0**: Bổ sung bộ lọc trong `SMCStructurePrimitive.ts`, tuyệt đối không vẽ các nhãn hoặc mức giá có tọa độ `<= 0` hoặc timestamp `<= 0`.
3. **Multi-Zone Execution Hypotheses**: Thiết lập bộ sinh giả thuyết cho tất cả các vùng quét thanh khoản với ID ổn định `hyp-zone-{zone_id}`, tách biệt hoàn toàn khỏi thứ hạng hiển thị, ngăn chặn hiện tượng identity churn.
4. **Cụm thanh khoản & Trạng thái Nhân quả**: Triển khai `LiquidityCluster` và cỗ máy trạng thái nhân quả (`UNSWEPT` → `SWEPT` → `RECLAIMED` / `ACCEPTED_BEYOND`). Round numbers được đặt thành cấu hình thứ cấp và bị loại trừ khỏi bộ chọn target swing để bảo vệ Net R:R $\ge 2.0$.
5. **Đặc tả 5 mô hình chuẩn hóa trong Model Registry**: Tích hợp Fail-Closed Guard, ngăn chặn mọi candidate không có trong danh mục mô hình đã phê duyệt.
6. **2 Mô hình Scalp M1 mới**:
   - `SMC_M1_FVG_CONTINUATION`: Vào lệnh theo xu hướng khi nến M1 retest vào FVG hợp lệ và đóng nến xác nhận.
   - `SMC_M1_SWEEP_RECLAIM`: Bắt đảo chiều sau khi cụm thanh khoản bị quét và đóng nến reclaim quay lại trong vùng.
7. **Bộ điều phối Daily Frequency Goal & Hard Cap 3 Fills**: Đánh giá tiến độ từng ngày (`0 -> 1 -> 2 -> 3`), tự động bảo lưu slot cuối cùng cho phiên NY (`ny_fills < 1`), và gắn nhãn `SHORTFALL` khi hết phiên mà chưa đạt chỉ tiêu.
8. **Lớp quan sát Orderflow Depth**: Tích hợp `BitgetPublicDepthAdapter` kiểm tra tính hợp lệ của sổ lệnh (monotonic, không crossed, freshness $\le 5000$ms), phục vụ ghi nhận shadow phục vụ nghiên cứu trượt giá, không can thiệp lệnh live.

---

## 1. Kiểm toán Từng Lớp Chức năng (Layered Breakdown)

### Lớp 1: Tính đúng đắn của Dữ liệu & Timestamps (Correctness)
- **Hàm `candle_open_ms(bar)`**: Hỗ trợ đồng thời đối tượng (`open_time`, `timestamp`, `time`) và dictionary. Yêu cầu giá trị thời gian nguyên dương hợp lệ.
- **CHOCH/BOS timestamps**: `qualify_selected_break`, `evaluate_m1_reversal_candidate` và `evaluate_m1_continuation_candidate` sử dụng `candle_open_ms(break_bar)` để gán `occurred_at_ms`. Kết quả kiểm thử khẳng định 100% timestamps `> 0`.
- **Chart Coordinate Guard**: `SMCStructurePrimitive.ts` kiểm tra nghiêm ngặt `level > 0` và `price > 0`. Bất kỳ mốc nào không hợp lệ đều bị bỏ qua trong pipeline render của Lightweight Charts.

### Lớp 2: Bằng chứng & Giả thuyết Đa vùng (Evidence & Multi-Zone Hypotheses)
- **Stable Hypothesis IDs**: Hàm `build_execution_hypotheses_for_zones()` sinh ID cố định `hyp-zone-{zone_id}`. Dù zone thay đổi thứ tự ưu tiên (presentation rank) do cập nhật điểm số, hypothesis ID vẫn bất biến, bảo vệ tính liên tục của vòng đời lệnh.
- **Liquidity Clusters**: Hàm `build_liquidity_clusters()` gom các mốc thanh khoản lân cận theo dung sai tick (`cluster_tolerance_ticks`). Hàm `update_cluster_state()` cập nhật trạng thái nến tiếp theo một cách nhân quả, lưu lại `swept_extreme` để tính toán Stop Loss chuẩn xác.
- **Loại trừ Round Number khỏi Target Selection**: Khi gộp các mức round number vào liquidity map, `select_unconsumed_target()` loại bỏ hoàn toàn các mốc có `source_kind == "ROUND_NUMBER"`. Nhờ vậy, target luôn hướng tới các đỉnh/đáy swing cấu trúc thật, đảm bảo Net R:R luôn $\ge 2.0$.

### Lớp 3: Hiệu năng & Danh mục Mô hình (Model Registry & Candidate Routing)
- **Model Registry (`smc_model_registry.py`)**: Đăng ký 5 mô hình chuẩn:
  1. `SMC_M1_REVERSAL` (Thẩm quyền Zone: Bắt buộc)
  2. `SMC_M1_CONTINUATION` (Thẩm quyền Zone: Bắt buộc)
  3. `SMC_M1_FVG_CONTINUATION` (Thẩm quyền Zone: Không bắt buộc)
  4. `SMC_M1_SWEEP_RECLAIM` (Thẩm quyền Zone: Không bắt buộc)
  5. `SMC_SESSION_OPEN_EXPANSION` (Chế độ Shadow)
- **Fail-Closed Guard**: `check_candidate_lifecycle_authority()` tra cứu registry. Bất kỳ candidate nào có `model_id` lạ hoặc vi phạm thẩm quyền zone đều bị từ chối với lý do `UNREGISTERED_MODEL` hoặc `ZONE_LIFECYCLE_UNAUTHORIZED`.
- **Router & Deduplication**: Hàm `evaluate_smc_opportunity_candidates()` gom toàn bộ candidate đủ điều kiện, lọc trùng (deduplicate) theo setup key và hướng giao dịch, sắp xếp ưu tiên theo `net_rr` cao nhất.
- **Suppression cảnh báo từ chối ARM**: Bổ sung bộ đệm suppression 60s cho cùng một lý do từ chối để tránh spam log và quá tải hệ thống.

### Lớp 4: Mục tiêu Tần suất Hàng ngày & Giới hạn Cứng (Daily Frequency & Hard Cap)
- **Đánh giá tiến độ hàng ngày**: Hàm `evaluate_daily_frequency_goal()` quản lý số fills hôm nay:
  - Fills = 0: Mục tiêu tiếp theo là 1.
  - Fills = 1: Mục tiêu tiếp theo là 2.
  - Fills = 2: Mục tiêu tiếp theo là 3, kèm điều kiện bảo lưu cho phiên NY.
  - Fills = 3: Đạt trần cứng, khóa hoàn toàn mọi cơ hội vào lệnh mới trong ngày (`can_accept_new_fill = False`).
- **Bảo lưu cho phiên NY (NY Reservation)**: Nếu `ny_fills < 1` và `today_fills == 2`, hệ thống khóa giao dịch ở phiên London late, dành riêng slot fill thứ 3 cho phiên New York.
- **Cảnh báo thiếu hụt (Shortfall Tracking)**: Khi phiên giao dịch kết thúc (ví dụ sau 11:00 NY) mà tổng fills trong ngày chưa đạt 2, hệ thống tự động ghi nhận cờ `SHORTFALL` để thống kê hiệu quả thị trường.

### Lớp 5: Tính Bất biến của Luồng Thực thi & Quản trị Rủi ro (Invariants)
- **Tỷ lệ Net R:R tối thiểu**: Tất cả các mô hình mới (`FVG_CONTINUATION`, `SWEEP_RECLAIM`) đều phải vượt qua hàm `calculate_risk_reward()` với `min_net_rr >= 2.0` (sau khi khấu trừ đầy đủ spread, slippage và 2 chiều phí taker commission).
- **Giới hạn 1 vị thế mở**: Hệ thống chỉ cho phép duy nhất 1 lệnh ở trạng thái `OPEN` hoặc `ARMED`. Khi có vị thế đang chạy, tất cả các candidate mới đều bị chặn.
- **Chế độ Paper Trading An toàn**: Không có API key thật của sàn, không có logic gửi lệnh khớp thật lên Bitget; toàn bộ khớp lệnh thông qua Paper Execution Engine nội bộ.

### Lớp 6: Độ phủ Dữ liệu Sổ lệnh (Bitget Public Depth Shadow)
- **Kiểm định Sổ lệnh**: `validate_orderbook_levels()` kiểm tra:
  - Bids xếp giảm dần, Asks xếp tăng dần.
  - Giá Ask tốt nhất luôn lớn hơn Bid tốt nhất (không crossed book).
  - Tuổi dữ liệu sổ lệnh không vượt quá 5.000ms.
- **Bộ chuyển đổi Phi chặn (Non-blocking Shadow Adapter)**: `BitgetPublicDepthAdapter` ghi nhận độ sâu thanh khoản công khai của cặp XAUUSDT, tính toán trượt giá lý thuyết phục vụ phân tích mà không gây nghẽn luồng xử lý nến M1.

---

## 2. Kết quả Kiểm thử Kỹ thuật (Technical Test Suite Matrix)

| Bộ kiểm thử | Số bài kiểm tra | Kết quả | Ghi chú |
|---|---|---|---|
| `backend/tests/test_v16_opportunity_engine.py` | 10 / 10 | **PASS (100%)** | Xác thực CHOCH/BOS, Multi-zone Hypotheses, Liquidity Clusters, Model Registry, 2 Models mới, Deduplication Router, Daily Frequency Progression, Orderbook Validation |
| `backend/tests/test_v15_0_m1_scalp_models.py` | 4 / 4 | **PASS (100%)** | Bảo toàn tính toán M1 Reversal & Continuation, Target Selection, Net R:R $\ge 2.0$ |
| `backend/tests/test_v15_1_execution_lifecycle.py` | 16 / 16 | **PASS (100%)** | Vòng đời Scalp Execution, Transaction Rollback, Rate Limits, Lifecycle Authority |
| `backend/tests/test_v12_4_acceptance.py` | 12 / 12 | **PASS (100%)** | Khắc phục casing assertion của `posting_id` (hỗ trợ cả `post-` và `POST-`) |
| Frontend Vitest Test Suites | 26 / 26 files (226 tests) | **PASS (100%)** | Bổ sung coordinate guards, label layout, translation tiếng Việt cho các strategy families mới |
| Frontend Production Build (`tsc -b && vite build`) | Toàn bộ bundle | **SUCCESS** | Biên dịch TypeScript và đóng gói Vite sạch, không có lỗi |

---

## 3. Kết luận Nghiệm thu

Mã nguồn phiên bản V16 đã được hoàn thiện đúng theo đặc tả kỹ thuật:
- Tăng cường khả năng tìm kiếm cơ hội giao dịch hợp lệ thông qua việc giải phóng các kịch bản đa vùng và bổ sung 2 mô hình SMC M1 thực tế.
- Giữ vững kỷ luật rủi ro cao nhất: Giới hạn cứng 3 fills/ngày, duy trì Net R:R $\ge 2.0$, bảo toàn 1 vị thế mở duy nhất, và vận hành hoàn toàn trong chế độ Paper Trading.
- Tất cả các bài kiểm tra kỹ thuật đều đạt yêu cầu, sẵn sàng bàn giao và đồng bộ lên repository.
