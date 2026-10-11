# V13.7 IMPLEMENTATION MAP & TRACEABILITY MATRIX
## AURUM DESK — TERMINAL REPLAY LIFECYCLE, LEDGER IDEMPOTENCY & CELL-BY-CELL ARTIFACT VERIFICATION

---

### 1. KIẾN TRÚC TỔNG THỂ & DÒNG CHẢY DỮ LIỆU THỰC THI (V13.7 CAUSAL PIPELINE)

Luồng thực thi trong ReplayEngine.run_replay():
```
[ReplayMarketEvent (CLOSE)]
       │
       ▼
[smc_engine / ny_strategy_variants / daily_research_scheduler]
       │ (Phát hiện tín hiệu / Lập kế hoạch giá với nguồn cấu trúc thực, loại bỏ SL-distance multiplier)
       ▼
[CandidatePlan (Direction, Entry, SL, TP, Target Source, Target Model, Net RR)]
       │
       ▼
[submit_replay_order()] ──► [ReplayPendingOrder (SUBMITTED / PENDING)]
       │                          │ (Ghi nhận all_orders history, không trừ vốn hay quota)
       │                          ▼
       │                  [ReplayMarketEvent (OPEN của nến kế tiếp)]
       │                          │
       │                          ▼
       └──────────────────► [try_fill_pending_order()]
                                  ├── Chặn tức thì trạng thái kết thúc (Terminal Guard: FILLED/REJECTED/EXPIRED/CANCELLED)
                                  ├── Kiểm tra nhân quả thời gian: event.timestamp >= max(earliest_execution_ms, decision_ms)
                                  ├── Tính giá khớp thực tế: adverse spread + slippage
                                  ├── Tái thẩm định hình học giá (Price Geometry)
                                  ├── Tái thẩm định Net R:R sau fill (>= min_net_rr)
                                  └── Gọi reject_order() nếu không hợp lệ (gán order.status = "REJECTED")
                                  │
                                  ▼
[ReplayEngine Phase 1 Cleanup]
       ├── Kiểm tra normalize_order_status(p_order.status)
       ├── Nếu FILLED: tạo ReplayPosition (OPEN) + ENTRY_FEE Posting (-Fee)
       ├── Nếu REJECTED / EXPIRED / CANCELLED: lưu vào all_orders, loại bỏ ngay khỏi active pending_orders
       └── Giữ lại chỉ các lệnh ACTIVE (SUBMITTED, PENDING)
                                  │
                                  ▼ (Theo dõi các nến tiếp theo)
[ReplayMarketEvent (CLOSE các nến sau khớp)]
       │
       ▼
[evaluate_position_exit()]
       ├── Phân giải chạm SL / TP (Conservative: chạm cả 2 ưu tiên SL, trượt giá bất lợi bảo thủ)
       ├── resolve_exit_fee_rate() (Maker fee nếu TP limit, Taker fee nếu Stop loss hoặc Ambiguous)
       └── [compute_closed_trade_accounting()]
                 ├── Gross PnL = Sign * (Exit - Entry) * Qty * Multiplier
                 ├── Exit Fee = Exit * Qty * resolved_fee_rate
                 ├── Net PnL = Gross PnL - Entry Fee - Exit Fee
                 └── Realized R = Net PnL / Initial Risk USDT
       │
       ▼
[ReplayPosition (CLOSED)] + [Gross PnL & Exit Fee Postings] ──► [Ledger & Cash Update]
       │
       ▼
[record_posting()] (Kiểm tra idempotency: lặp lại cùng posting_id thì giữ nguyên, xung đột amount thì raise lỗi)
       │
       ▼
[aggregate_replay_metrics()] ──► [ReplaySummary / canonical result]
       │
       ▼
[run_replay_integrity_checks()] (Kiểm tra 7 invariants + causal timeline + hash verification)
       │
       ▼
[V12ExcelExporter / _export_all_artifacts()] ──► [report.json, trades.csv, .xlsx]
       │
       ▼
[verify_exported_artifacts()] (Mở và đối soát chi tiết từng dòng, từng cell, từng ID, giá và PnL)
```

---

### 2. BẢNG ÁNH XẠ FILE → HÀM → CALLER → SIDE EFFECTS (PHẦN 10)

| STT | Tập tin & Tên hàm / Ký hiệu | Trách nhiệm (Role) | Input / Output | Caller thực tế | Tác động trạng thái (Mutation Ownership) | Kiểm thử xác thực (Test ID) |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **01** | `backend/lab/replay_evidence_utils.py`<br>`TERMINAL_ORDER_STATUSES`, `ACTIVE_ORDER_STATUSES` | Tập hợp bất biến trạng thái lệnh. | Frozenset strings | `normalize_order_status()`, `try_fill_pending_order()` | Không có | `test_v13_7_evidence_utils.py` |
| **02** | `backend/lab/replay_evidence_utils.py`<br>`normalize_order_status()` | Chuẩn hóa Enum/String trạng thái lệnh. | Input: Any (str/Enum)<br>Output: str chuẩn | `try_fill_pending_order()`, `ReplayEngine.run_replay()` | Ném lỗi nếu status không thuộc tập hợp cho phép. | `test_v13_7_evidence_utils.py` |
| **03** | `backend/lab/replay_evidence_utils.py`<br>`read_required()` | Đọc trường bắt buộc không chấp nhận None/missing. | Input: obj, key<br>Output: value | `index_unique()`, verifiers | Báo lỗi `MISSING_FIELD` nếu thiếu; chấp nhận 0/False/"". | `test_v13_7_evidence_utils.py` |
| **04** | `backend/lab/replay_evidence_utils.py`<br>`finite_number()` | Thẩm định số thực hữu hạn, loại trừ bool/NaN/Inf. | Input: value, name<br>Output: float | `compare_trade_numbers()`, calculator adapters | Ném lỗi nếu NaN/Inf/bool/None. | `test_v13_7_evidence_utils.py` |
| **05** | `backend/lab/replay_evidence_utils.py`<br>`decimal_amount()` | Thẩm định và chuyển đổi tiền tệ sang Decimal chính xác. | Input: value, name<br>Output: Decimal | `apply_posting_once()`, `compare_trade_numbers()` | Ném lỗi nếu không thể biểu diễn số học chính xác. | `test_v13_7_evidence_utils.py` |
| **06** | `backend/lab/replay_evidence_utils.py`<br>`stable_id()` | Tạo ID xác định từ canonical JSON digest. | Input: prefix, payload<br>Output: deterministic ID string | Ledger posting, position identity | Không có (Pure function). | `test_v13_7_evidence_utils.py` |
| **07** | `backend/lab/replay_evidence_utils.py`<br>`index_unique()` | Chỉ mục hóa danh sách theo ID duy nhất. | Input: rows, key<br>Output: dict[id -> row] | `verify_exported_artifacts()`, CSV/XLSX checkers | Báo lỗi nếu trùng ID (`DUPLICATE_ID`). | `test_v13_7_evidence_utils.py` |
| **08** | `backend/lab/replay_evidence_utils.py`<br>`compare_trade_numbers()` | So sánh chi tiết từng trường số của lệnh với ngưỡng dung sai. | Input: expected, actual, fields, trade_id<br>Output: list of error strings | `verify_exported_artifacts()` | Không mutate; thu thập danh sách mismatch. | `test_v13_7_evidence_utils.py`, `test_v13_7_artifact_contract.py` |
| **09** | `backend/lab/replay_evidence_utils.py`<br>`apply_posting_once()` | Áp dụng posting vào ledger với tính chất idempotent. | Input: LedgerState, raw_posting<br>Output: bool (True nếu mới, False nếu lặp) | `ReplayContext.record_posting()` | Cập nhật `ledger.cash` và index; ném lỗi nếu cùng ID khác payload. | `test_v13_7_evidence_utils.py`, `test_v13_7_ledger_idempotency.py` |
| **10** | `backend/lab/replay_execution.py`<br>`reject_order()` | Helper chuyển trạng thái lệnh sang REJECTED một cách chuẩn hóa. | Input: order, reason_code<br>Output: (None, None, reason_code) | `try_fill_pending_order()` | Cập nhật `order.status = "REJECTED"`, `order.rejection_reason`. | `test_v13_7_order_lifecycle.py` |
| **11** | `backend/lab/replay_execution.py`<br>`resolve_exit_fee_rate()` | Xác định tỷ lệ phí maker/taker dựa trên exit cause và ambiguity. | Input: exit_cause, costs, is_ambiguous<br>Output: float fee rate | `compute_closed_trade_accounting()` | Không có (Pure function). | `test_v13_7_execution_costs.py` |
| **12** | `backend/lab/replay_execution.py`<br>`submit_replay_order()` | Tạo ReplayPendingOrder với nguồn nhân quả và tham số chính xác. | Input: candidate_plan, sim_time, ...<br>Output: ReplayPendingOrder | `ReplayEngine.run_replay()` (3 nhánh: baseline, adaptive, scheduled) | Tạo pending order; không trừ vốn, không tăng quota. | `test_v13_7_causal_driver.py`, `test_v13_5_execution_acceptance.py` |
| **13** | `backend/lab/replay_execution.py`<br>`try_fill_pending_order()` | Thẩm định khớp lệnh nến OPEN với Terminal Guard và Causal Timing. | Input: order, event, costs, capital, risk_pct<br>Output: (position, posting, reason) | `ReplayEngine.run_replay()` (Phase 1) | Chuyển order thành FILLED hoặc REJECTED; đóng băng initial risk. | `test_v13_7_order_lifecycle.py`, `test_v13_7_causal_driver.py` |
| **14** | `backend/lab/replay_engine.py`<br>`ReplayEngine.run_replay()` | Vòng lặp điều phối mô phỏng replay nến theo nến. | Input: ReplayRunRequest<br>Output: ReplayRunResponse | API, CLI runner, Test suites | Sở hữu vòng đời vị thế, pending orders, cash ledger, và artifacts. | `test_v13_7_engine_e2e.py`, `test_v13_2_replay_repair.py` |
| **15** | `backend/lab/daily_research_scheduler.py`<br>`build_scheduled_price_plan()` | Lập kế hoạch giá PAPER từ cấu trúc thị trường (loại bỏ SL-distance loop). | Input: direction, context, quote, policy<br>Output: (plan, err) | `evaluate_scheduled_entry()` | Không mutate; trả về cấu trúc TP/SL dựa trên swing hoặc measured move. | `test_v13_7_target_provenance.py` |
| **16** | `backend/lab/daily_research_scheduler.py`<br>`build_measured_move_target()` | Tính toán mục tiêu measured move từ độ rộng range đã biết. | Input: reference_range, direction, anchor, multiplier<br>Output: (target_dict, err) | `build_scheduled_price_plan()` | Không mutate; ném lỗi nếu range không hợp lệ. | `test_v13_7_target_provenance.py` |
| **17** | `backend/lab/daily_research_scheduler.py`<br>`derive_cadence_status()` | Xác định trạng thái tần suất nghiêm ngặt (PASS/UNMET/IN_PROGRESS). | Input: summary dict<br>Output: str status | `summarize_cadence()`, API | Không hạ tiêu chí xuống 80% để PASS; 1 phiên unmet => UNMET. | `test_v13_7_cadence_contract.py` |
| **18** | `backend/lab/daily_research_scheduler.py`<br>`summarize_cadence()` | Tổng hợp kết quả các phiên NY và gắn cadence_status. | Input: session_outcomes, calendar_days<br>Output: summary dict | `ReplayEngine.run_replay()` | Tính toán số phiên eligible, fills, unmet, in_progress, coverage_pct. | `test_v13_7_cadence_contract.py` |
| **19** | `backend/lab/daily_research_scheduler.py`<br>`audit_session_timeframes()` | Kiểm tra độ đầy đủ dữ liệu nến của phiên (dung sai ranh giới 4 bar). | Input: interval, bundle, required_tfs<br>Output: SessionDataAudit | `assess_session_data()` | Xác định is_market_open, is_complete, warmup_status. | `test_v13_7_data_audit.py`, `test_v13_5_data_range_acceptance.py` |
| **20** | `backend/lab/replay_integrity.py`<br>`run_replay_integrity_checks()` | Kiểm định 7 tiêu chí bất biến toàn vẹn dữ liệu Replay. | Input: positions, summary, orders, ...<br>Output: dict status + checks list | `ReplayEngine.run_replay()` | Fail-closed nếu vi phạm bất kỳ tiêu chí nào; kiểm tra hash thật. | `test_v13_6_negative_integrity.py` |
| **21** | `backend/lab/replay_integrity.py`<br>`verify_exported_artifacts()` | Mở và đối soát chi tiết JSON, CSV, XLSX theo từng cell và field. | Input: result, artifacts_dir<br>Output: (bool, list[errors]) | `ReplayEngine.run_replay()`, runner | So sánh trade IDs, direction, entry, SL, TP, exit, net PnL, overview cells. | `test_v13_7_artifact_contract.py` |
| **22** | `tools/run_v13_7_acceptance.py`<br>`main()` | Runner nghiệm thu tự động toàn bộ 5 stage V13.7. | CLI args<br>Output: acceptance.json + exit code | Kỹ sư / CI / Pair programming | Xuất acceptance.json với 3 trạng thái độc lập. | Toàn bộ suite kiểm thử |
