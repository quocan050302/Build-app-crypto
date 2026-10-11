# V13.6 IMPLEMENTATION MAP & TRACEABILITY MATRIX
## AURUM DESK — REPLAY EXECUTION, ACCOUNTING & INTEGRITY INTEGRATION

---

### 1. KIẾN TRÚC TỔNG THỂ & DÒNG CHẢY DỮ LIỆU THỰC THI (CAUSAL EXECUTION FLOW)

Luồng thực thi trong ReplayEngine.run_replay():
```
[ReplayMarketEvent (CLOSE)]
       │
       ▼
[smc_engine / ny_strategy_variants / daily_research_scheduler]
       │ (Phát hiện tín hiệu / Lập kế hoạch giá)
       ▼
[CandidatePlan (Direction, Planned Entry, SL, TP, Net RR, Risk)]
       │
       ▼
[submit_replay_order()] ──► [ReplayPendingOrder (SUBMITTED / PENDING)]
                                    │ (Chờ nến tiếp theo)
                                    ▼
[ReplayMarketEvent (OPEN của nến kế tiếp)]
       │
       ▼
[try_fill_pending_order()]
       ├── Kiểm tra tính hợp lệ & thời hạn (Expiry)
       ├── Tính giá khớp thực tế: adverse spread + slippage
       ├── Tái thẩm định hình học giá (Price Geometry)
       ├── Tái thẩm định Net R:R sau fill (>= min_net_rr)
       └── Đóng băng Initial Risk USDT
       │
       ▼
[ReplayPosition (OPEN)] + [ENTRY_FEE Posting (-Fee)] ──► [Ledger & Cash Update]
       │
       ▼ (Theo dõi các nến tiếp theo)
[ReplayMarketEvent (CLOSE các nến sau khớp)]
       │
       ▼
[evaluate_position_exit()]
       ├── Phân giải chạm SL / TP (Conservative: chạm cả 2 ưu tiên SL)
       ├── Trượt giá bất lợi khi thoát lệnh (Adverse Exit Slippage)
       └── [compute_closed_trade_accounting()]
                 ├── Gross PnL = Sign * (Exit - Entry) * Qty * Multiplier
                 ├── Exit Fee = Exit * Qty * Taker Fee Rate
                 ├── Net PnL = Gross PnL - Entry Fee - Exit Fee
                 └── Realized R = Net PnL / Initial Risk USDT
       │
       ▼
[ReplayPosition (CLOSED)] + [Gross PnL & Exit Fee Postings] ──► [Ledger & Cash Update]
       │
       ▼
[aggregate_replay_metrics()] ──► [ReplaySummary / canonical result]
       │
       ▼
[run_replay_integrity_checks()] (Fail-closed nếu thiếu decision_time hoặc UNKNOWN direction)
       │
       ▼
[V12ExcelExporter / _export_all_artifacts()] ──► [report.json, trades.csv, .xlsx]
       │
       ▼
[verify_exported_artifacts()] (Mở và đối soát chi tiết từng dòng, từng cell)
```

---

### 2. BẢNG ÁNH XẠ FILE → HÀM → CALLER → SIDE EFFECTS (PHẦN 140)

| STT | Tập tin & Tên hàm | Trách nhiệm (Role) | Input / Output | Caller thực tế | Tác động trạng thái (Mutation Ownership) | Kiểm thử xác thực (Test ID) |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **01** | `backend/lab/replay_contracts.py`<br>`ReplayPendingOrder` [ĐÃ CÓ] | Dataclass lưu trữ lệnh chờ khớp độc lập. | Input: order fields<br>Output: ReplayPendingOrder instance | `submit_replay_order()` | Không thay đổi vốn hay quota ngày. | `test_v13_5_execution_acceptance.py` |
| **02** | `backend/lab/replay_contracts.py`<br>`ReplayPosition` [ĐÃ CÓ] | Dataclass lưu trữ vị thế đang mở hoặc đã đóng. | Input: position fields<br>Output: ReplayPosition instance | `try_fill_pending_order()`, `evaluate_position_exit()` | Quản lý trạng thái OPEN / CLOSED của vị thế. | `test_v13_5_execution_acceptance.py` |
| **03** | `backend/lab/replay_execution.py`<br>`submit_replay_order()` [ĐÃ CÓ] | Tạo pending order từ candidate plan đã được thẩm định. | Input: candidate, session_id, decision_ms...<br>Output: ReplayPendingOrder (SUBMITTED) | `ReplayEngine.run_replay()` (Baseline, Adaptive, Scheduled) | Không trừ tiền mặt, không tăng fills quota. | `test_v13_6_engine_execution_integration.py` |
| **04** | `backend/lab/replay_execution.py`<br>`try_fill_pending_order()` [ĐÃ CÓ] | Mô phỏng khớp lệnh tại giá mở nến kế tiếp kèm spread/slippage bất lợi. | Input: order, event (OPEN), costs, capital...<br>Output: (ReplayPosition, entry_posting, status) | `ReplayEngine.run_replay()` tại OPEN phase | Tạo ReplayPosition OPEN, sinh bút toán ENTRY_FEE. | `test_v13_6_engine_execution_integration.py` |
| **05** | `backend/lab/replay_execution.py`<br>`evaluate_position_exit()` [ĐÃ CÓ] | Đánh giá điều kiện chạm SL/TP với giải quyết bảo thủ cho nến ambiguous. | Input: position, event, costs...<br>Output: (accounting_dict, exit_cause) | `ReplayEngine.run_replay()` tại CLOSE phase | Cập nhật holding_bars; chuyển trạng thái CLOSED khi chạm stop. | `test_v13_5_execution_acceptance.py` |
| **06** | `backend/lab/replay_execution.py`<br>`compute_closed_trade_accounting()` [ĐÃ CÓ] | Tính toán hạch toán chuẩn production (gross, phí 2 chiều, net, realized R). | Input: position, exit_price, costs...<br>Output: Closed trade accounting dict & postings | `evaluate_position_exit()`, `ReplayEngine` | Tính toán thuần túy (pure arithmetic), sinh bút toán GROSS_PNL và EXIT_FEE. | `test_v13_5_accounting_acceptance.py` |
| **07** | `backend/lab/replay_metrics.py`<br>`partition_trade_records()` [ĐÃ CÓ] | Phân loại danh sách lệnh thành CLOSED, OPEN, INVALID. | Input: all_trades list<br>Output: (closed, open, invalid) | `aggregate_replay_metrics()`, `run_replay_integrity_checks()` | Pure partition, không thay đổi phần tử. | `test_v13_5_metrics_acceptance.py` |
| **08** | `backend/lab/replay_metrics.py`<br>`aggregate_replay_metrics()` [ĐÃ CÓ] | Nguồn tổng hợp chỉ số duy nhất cho toàn hệ thống. | Input: all_trades, ledger, equity_curve...<br>Output: canonical metrics dict | `ReplayEngine.run_replay()` ở cuối run | Không làm thay đổi dữ liệu gốc. | `test_v13_6_engine_execution_integration.py` |
| **09** | `backend/lab/replay_integrity.py`<br>`run_replay_integrity_checks()` [SỬA ĐỔI] | Kiểm toán 7 điều kiện bắt buộc, fail-closed nếu thiếu decision_time hoặc UNKNOWN. | Input: result, closed_trades, ledger, daily_stats...<br>Output: integrity_summary dict | `ReplayEngine.run_replay()`, acceptance runner | Pure validation, trả về PASS hoặc FAIL có mã lỗi rõ ràng. | `test_v13_6_negative_integrity.py` |
| **10** | `backend/lab/replay_integrity.py`<br>`verify_exported_artifacts()` [SỬA ĐỔI] | Mở và đối soát chi tiết nội dung file JSON, CSV, XLSX với kết quả canonical. | Input: result, artifacts_dir<br>Output: (is_valid, error_list) | `ReplayEngine._export_all_artifacts()`, acceptance runner | Kiểm tra đọc (read-only), không chỉnh sửa file. | `test_v13_6_negative_artifacts.py` |
| **11** | `backend/lab/daily_research_scheduler.py`<br>`resolve_research_range()` [SỬA ĐỔI] | Resolve mốc thời gian start/end (end-exclusive) và nhãn ngày đồng bộ. | Input: request, now_ms<br>Output: (start_ts, end_ts, metadata) | `ReplayEngine.run_replay()`, runner | Xác định phạm vi thời gian chuẩn hóa. | `test_v13_5_data_range_acceptance.py` |
| **12** | `backend/lab/daily_research_scheduler.py`<br>`build_session_interval()` [ĐÃ CÓ] | Tính toán cửa sổ phiên Mỹ và deadline theo ZoneInfo America/New_York. | Input: ny_date, policy<br>Output: session interval dict | `ReplayEngine.run_replay()`, scheduler | Pure calendar math, xử lý đúng minute=0. | `test_v13_5_data_range_acceptance.py` |
| **13** | `frontend/src/utils/deriveResearchVerdict.ts`<br>`deriveResearchVerdict()` [ĐÃ CÓ] | Phán quyết độc lập 3 trạng thái (Technical, Cadence, Economic) cho UI người mới. | Input: run response data<br>Output: ResearchVerdict object | `ResearchTab.tsx` | Pure mapping sang thông điệp tiếng Việt dễ hiểu. | `frontend/src/v13_5_research_acceptance.test.ts` |
| **14** | `backend/lab/replay_engine.py`<br>`run_replay()` [SỬA ĐỔI] | Orchestrator thực thi replay, gọi trực tiếp các module execution/accounting/metrics. | Input: ReplayRunRequest, background_tasks...<br>Output: ReplayRunResponse | API endpoint `/api/replay/run`, CLI runners | Quản lý vòng đời run, DB cách ly, xuất artifacts. | `test_v13_6_engine_execution_integration.py` |

---

### 3. CÁC ĐƯỜNG ĐI ĐÃ NGỪNG DÙNG (DEPRECATED PATHS)
1. **Ngừng gán trực tiếp `active_trade = { ... }` tại `sim_time`**: Toàn bộ lệnh (Baseline, Adaptive B1/B2, Scheduled Paper) đều phải tạo pending order qua `submit_replay_order()` và chỉ được khớp tại nến tiếp theo qua `try_fill_pending_order()`.
2. **Ngừng tính toán exit và accounting thủ công trong thân hàm `run_replay()`**: Toàn bộ exit logic và hạch toán PnL / phí / realized R được giao cho `evaluate_position_exit()` và `compute_closed_trade_accounting()`.
3. **Ngừng tính toán lặp lại các chỉ số summary**: Thay thế toàn bộ khối tính tay wins, losses, winrate, net PnL, profit factor bằng một lần gọi `aggregate_replay_metrics()`.
