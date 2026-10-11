# V13.8 IMPLEMENTATION MAP & TRACEABILITY MATRIX
## AURUM DESK — CAUSAL REPLAY ACCOUNTING, STRICT ARTIFACT VERIFICATION & DETERMINISTIC ACCEPTANCE

---

### 1. KIẾN TRÚC TỔNG THỂ & DÒNG CHẢY DỮ LIỆU THỰC THI (V13.8 PIPELINE)

Luồng thực thi trong `ReplayEngine.run_replay()`:
```
[ReplayMarketEvent (CLOSE)]
       │
       ▼
[smc_engine / ny_strategy_variants / daily_research_scheduler]
       │ (Phát hiện tín hiệu / Lập kế hoạch giá với nguồn cấu trúc thực, measured_move_multiplier đóng băng duy nhất)
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
       │       └── ReplayContext.record_posting() (Kiểm tra posting_index trước khi trừ tiền, idempotency tuyệt đối)
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
[ReplayEngine Phase 2 Exit Recording]
       ├── ReplayContext.record_posting() (Ghi nhận GROSS_PROFIT, EXIT_FEE vào sổ cái duy nhất)
       └── Đồng bộ hóa cash_balance = float(replay_ctx.current_cash), loại bỏ hoàn toàn divergence
                                  │
                                  ▼
[End of Run: verify_exported_artifacts()]
       ├── Đối soát report.json (Khớp Run ID, Range, Hashes, PnL, Fees, Trades Count)
       ├── Đối soát trades.csv (Xác thực math.isfinite() chặn triệt để NaN, đối chiếu từng lệnh)
       ├── Đối soát trades.xlsx (Xác thực tập hợp ID chính xác, phát hiện trade ID lạ, kiểm tra finite mọi ô)
       └── Integrity Checks (Kiểm tra SHA256 hex nghiêm ngặt, từ chối hash giả mạo)
```

---

### 2. MA TRẬN TRUY XUẤT CÁC TẬP TIN SỬA ĐỔI & BỔ SUNG (TRACEABILITY MATRIX)

| Mã yêu cầu | Tập tin nguồn | Ký hiệu / Hàm can thiệp | Trách nhiệm & Hợp đồng thực thi | Tập tin kiểm thử liên quan |
| :--- | :--- | :--- | :--- | :--- |
| **Phần 32, 123-127** | `backend/lab/replay_engine.py` | `ReplayContext.record_posting()` | Sửa triệt để bug trừ tiền trước khi kiểm tra deduplicate: kiểm tra `posting_index` và payload conflict trước khi mutate `self.current_cash`. Chặn NaN và bool. Đồng bộ `cash_balance` với `current_cash`. | `backend/tests/test_v13_8_ledger_idempotency.py` |
| **Phần 07, 51, 134** | `backend/lab/daily_research_scheduler.py` | `build_scheduled_price_plan()` | Xóa bỏ hoàn toàn vòng lặp tìm kiếm multiplier `for mm_mult in [2.0, 2.5, 3.0, 3.5]`. Sử dụng duy nhất một `measured_move_multiplier` đóng băng từ policy (mặc định 3.5). Nếu không đạt Net R:R thì trả reject reason. | `backend/tests/test_v13_8_target_provenance.py` |
| **Phần 139** | `backend/lab/replay_evidence_utils.py` | `assert_exact_id_set()`, `assert_finite_equal()` | Cung cấp hàm kiểm tra tập hợp ID chính xác (phát hiện trade ID lạ hoặc thiếu) và so sánh số hữu hạn (loại bỏ bypass so sánh với NaN). | `backend/tests/test_v13_7_evidence_utils.py` |
| **Phần 08, 139-142** | `backend/lab/replay_integrity.py` | `verify_exported_artifacts()`, `run_replay_integrity_checks()` | Đối soát artifacts nghiêm ngặt: kiểm tra `math.isfinite()` trên CSV và Excel; kiểm tra tập hợp ID và bắt lỗi `XLSX_UNKNOWN_TRADE_ID`; kiểm tra regex hex 16-64 ký tự và từ chối `FAKE` hash. | `backend/tests/test_v13_8_artifact_contract.py` |
| **Phần 23, 24, 25** | `backend/lab/replay_execution.py` | `try_fill_pending_order()`, `reject_order()` | Duy trì terminal order guard chặn các trạng thái `FILLED`, `REJECTED`, `EXPIRED`, `CANCELLED`; hàm `reject_order` chuẩn hóa trạng thái; caller dọn dẹp sạch `pending_orders`. | `backend/tests/test_v13_8_order_lifecycle.py` |
| **Phần 36-40, 99, 152** | `backend/lab/replay_execution.py` | `evaluate_position_exit()`, `compute_closed_trade_accounting()` | Tính toán kế toán thuần túy (gross, fee, net, realized R). Xử lý gap giá mở cửa qua SL và adverse slippage; hỗ trợ maker TP và taker SL. | `backend/tests/test_v13_8_execution_costs.py` |
| **Phần 46-48, 102** | `backend/lab/replay_contracts.py` & `replay_execution.py` | `submit_replay_order()`, `try_fill_pending_order()` | Biên thứ tự sự kiện nhân quả (CLOSE nến trước -> quyết định/submit -> OPEN nến kế -> fill). Phân bổ hạn ngạch ngày theo đồng hồ nến OPEN. | `backend/tests/test_v13_8_causal_driver.py` |
| **Phần 61, 103, 135** | `backend/lab/daily_research_scheduler.py` | `audit_session_timeframes()` | Thẩm định lưới thời gian phiên, loại bỏ logic chấp nhận nửa phiên, kiểm tra warmup lookback đầy đủ. | `backend/tests/test_v13_8_data_audit.py` |
| **Phần 55, 104, 153** | `backend/lab/daily_research_scheduler.py` | `derive_cadence_status()` | Xác định trạng thái tần suất nghiêm ngặt: `unmet_sessions > 0` thì kết luận `UNMET`, không hạ tiêu chí xuống 80% để tự nhận PASS. | `backend/tests/test_v13_8_cadence_contract.py` |
| **Phần 88, 90, 143-146** | `tools/run_v13_8_acceptance.py` | `main()`, `run_stage_5_deterministic_repeat()` | Runner nghiệm thu V13.8 độc lập: 5 stage đầy đủ, parse test count thật từ JUnit XML, chạy lặp 2 lượt kiểm tra tính tất định 100%, ghi xuất `acceptance.json`. | `tools/run_v13_8_acceptance.py` |

---

### 3. DANH SÁCH 9 BỘ KIỂM THỬ V13.8 MỚI (BACKEND TEST SUITES)

1. `backend/tests/test_v13_8_order_lifecycle.py`: 4 tests (Terminal orders guard, reject_order helper, waiting on non-OPEN events).
2. `backend/tests/test_v13_8_ledger_idempotency.py`: 4 tests (Deduplicate 5 lần lặp giữ nguyên tiền, conflict payload/amount raise trước khi sửa tiền, từ chối NaN và bool).
3. `backend/tests/test_v13_8_execution_costs.py`: 4 tests (Kế toán tính tay LONG/SHORT taker/maker, multiplier 10, gap SL handling).
4. `backend/tests/test_v13_8_target_provenance.py`: 4 tests (Measured move từ range, reject range sai, reject geometry sai, single frozen multiplier không loop).
5. `backend/tests/test_v13_8_causal_driver.py`: 2 tests (Biên sự kiện nhân quả không fill ngược quá khứ, phân bổ quota nửa đêm VN).
6. `backend/tests/test_v13_8_data_audit.py`: 3 tests (Từ chối nửa phiên, chấp nhận phiên đầy đủ, kiểm tra warmup).
7. `backend/tests/test_v13_8_artifact_contract.py`: 4 tests (Negative controls: TP bị sửa, NaN trong CSV, trade ID lạ trong Excel, FAKE hash).
8. `backend/tests/test_v13_8_cadence_contract.py`: 5 tests (87% coverage vẫn trả UNMET, PASS khi 0 unmet, NOT_APPLICABLE, IN_PROGRESS).
9. `backend/tests/test_v13_8_engine_e2e.py`: 2 tests (Chạy ReplayEngine thực trên baseline và adaptive scheduled).

**Tổng cộng kiểm thử tự động V13.8**: **32 tests** (kết hợp với bộ test hiện hữu đạt **42 tests backend** trong runner V13.8), **100% PASSED**.

---

### 4. BẢO VỆ TUYỆT ĐỐI HỆ THỐNG LIVE & TÍNH CÁCH LY (LIVE ISOLATION)

- Toàn bộ quá trình chạy replay và kiểm thử được thực thi trong môi trường sandbox in-memory SQLite và thư mục artifacts riêng biệt (`backend/lab/artifacts/v12_2/`).
- Không tương tác với API Bitget thực tế, không sinh order thật.
- Không gửi thông báo lịch sử spam Telegram.
- Không thay đổi cấu hình live, cơ sở dữ liệu live hoặc vị thế đang mở của người dùng.
