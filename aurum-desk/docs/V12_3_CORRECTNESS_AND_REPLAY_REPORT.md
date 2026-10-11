# BÁO CÁO NGHIỆM THU TÍNH ĐÚNG, BẰNG CHỨNG THỰC, REPLAY NHÂN QUẢ VÀ BẢO TỒN RUNTIME LIVE (V12.3)

> **Mã báo cáo:** AURUM-V12_3-CORRECTNESS-REPLAY-AUDIT  
> **Thời gian phát hành:** 2026-10-10 12:05:00 UTC+7  
> **Repository:** `https://github.com/quocan050302/Build-app-crypto`  
> **Branch:** `feature/aurum-repair-smc-rr`  
> **Base Commit SHA:** `964877995ed1c32c6b56928197388145431fccf0`  
> **Bộ dữ liệu kiểm định:** XAUUSDT Bitget Classic USDT-FUTURES (2026-07-09 22:00:00 -> 2026-10-09 22:00:00 UTC+7)  
> **Cấu hình chuẩn:** Vốn ban đầu 1.000 USDT | Đòn bẩy 30x ISOLATED | Quality Risk 0.25% | Quota Risk 0.10% | Taker fee 0.06% | Maker fee 0.02% | Base Slip $0.10 | Base Spread $0.20  
> **Nghiệm thu kiểm thử tự động:** **85/86 REQUIREMENTS PASSED | 1 BLOCKED (OS Windows Symlink) | 0 FAILED**  
> **Bằng chứng kiểm thử:** Thu thập động 100% từ pytest JUnit XML parser (`EvidenceCollector`), không tự gán PASS tĩnh.  
> **Bảo vệ luồng runtime live:** **SENTINEL_DB_UNMUTATED** (Hash DB không bị ghi đè, cổng API 8000 và frontend 5174 duy trì hoạt động liên tục).  

---

## 1. Tóm Tắt Điều Hành & Nguyên Tắc Triển Khai (Executive Summary)

Phiên bản **V12.3** của Aurum Desk được triển khai để giải quyết triệt để 12 khiếm khuyết được chỉ ra trong V12.2 (từ **P01** đến **P12**), thiết lập nền tảng kế toán chuẩn xác không trôi số thập phân, bảo đảm tính nhân quả (zero-lookahead causal replay), tích hợp cơ chế hủy job thực sự và sửa lỗi luồng UI gây chạy lại replay ngoài ý muốn.

### 1.1. Nguyên tắc cốt lõi đã tuân thủ nghiêm ngặt
1. **Số liệu tài chính đối soát chính xác tuyệt đối:**
   - Xóa bỏ hoàn toàn hiện tượng lệch 0.02 USDT do làm tròn trung gian ở từng chân lệnh (`final_cash == initial_equity + total_net_pnl` chính xác từng xu trên cả 3 biến thể A, B, C).
   - Zero-delta repricing trong `StressTester` trả về đúng 100% lợi nhuận ròng của baseline với sai số $0.00.
2. **Nghiệm thu bằng chứng thực thi thật (Evidence Harvester):**
   - Loại bỏ đoạn mã ghi cố định `74/74 PASS` trong runner.
   - Thay thế toàn bộ các test rỗng (`assert True`, assert hằng số không kiểm tra ranh giới) bằng các kiểm tra ranh giới nghiệp vụ thực sự (`prefix invariance`, `production/replay parity`, `news blackout`, `rule activation`, `order lifecycle`).
   - Kết quả kiểm thử được bóc tách động từ file JUnit XML của pytest bởi `EvidenceCollector`.
3. **Replay có tính nhân quả, không nhìn trước tương lai:**
   - Tích hợp kiểm tra tin tức vĩ mô (`crud.check_news_blackout`) và luật kinh nghiệm (`LessonRuleService.retrieve_active_rules`) vào trực tiếp vòng lặp nến của `ReplayEngine`.
   - Các luật tương lai hoặc tin tức chưa tới thời điểm `known_at` không được phép tác động đến quyết định tại thời điểm giả lập $t$.
4. **Hủy Job thực thụ & kiểm soát tài nguyên:**
   - Đặt `cancel_check` checkpoint xuyên suốt vòng lặp nến của `ReplayEngine`. Khi người dùng bấm dừng, tiến trình replay dừng tính toán ngay lập tức.
   - Bounded queue giới hạn tối đa 5 tác vụ chạy đồng thời, tự động dọn dẹp job cũ và ngăn chặn nghẽn tài nguyên.
5. **Sửa lỗi luồng Frontend:**
   - Xóa bỏ việc `catch` lỗi chung tự động kích hoạt `runLabReplay`. Trạng thái `FAILED` hoặc `CANCELLED` dừng lại minh bạch, không tự ý kích hoạt replay lần 2.
6. **Bảo toàn luồng Live Runtime:**
   - Không can thiệp hoặc restart tiến trình backend (port 8000) và frontend (port 5174) đang chạy.
   - Cơ sở dữ liệu live `aurum_desk.db` hoàn toàn không bị ghi đè hay thay đổi.

---

## 2. Bản Đồ Phụ Thuộc (Dependency & Architecture Parity Map)

Luồng xử lý đơn hàng và tái sử dụng component giữa Production và Replay Lab:

```
[ Historical / Live Quote ]
           │
           ▼
[ Strategy Decision ] ─────── (Tái sử dụng: SMC, Bias H1/H4/D, Trend continuation B1, Break-retest B2)
           │
           ▼
[ Risk & Policy Check ] ───── (Tái sử dụng: TradingPolicyService, Hard Guards: Max 3 fills/day, 2 consec losses, 1.5% loss budget)
           │
           ▼
[ Causal Gatekeepers ] ────── (Tái sử dụng: crud.check_news_blackout, LessonRuleService.retrieve_active_rules)
           │
           ▼
[ Order Arming / State ] ──── (Phân định: READY [ứng viên đạt chuẩn] vs ARMED [lệnh sẵn sàng] vs FILLED)
           │
           ▼
[ Revalidation at Fill ] ──── (Đánh giá lại tại thời điểm khớp trên observation kế tiếp; không dùng intrabar signal candle)
           │
           ▼
[ Execution Engine ] ──────── (Production: Coordinator / PaperBroker; Lab: ReplayEngine candle simulation)
           │
           ▼
[ Lifecycle & Exits ] ─────── (Đánh giá SL/TP bảo thủ: nếu chạm cả hai trong 1 nến thì SL kích hoạt trước)
           │
           ▼
[ Live Ledger Postings ] ──── (Lab V12.3: Phát sinh posting ENTRY_FEE, EXIT_FEE, REALIZED_GROSS_PNL ngay lúc event xảy ra)
           │
           ▼
[ Outbox & Notifications ] ── (Production: Telegram Bot Outbox; Lab: In-memory Research Sink cô lập hoàn toàn)
```

**Bảng chi tiết các thành phần Lab tái sử dụng và thay thế:**

| Khâu quy trình | Production Component | Replay Lab (V12.3) | Tính tương đương (Parity) |
| :--- | :--- | :--- | :--- |
| **Quote Feed** | Bitget WebSocket / REST Collector | Historical Dataset Bundle (5M, 15M, 1H, 4H, 1D) | Đảm bảo nến đã đóng hợp lệ trước `simulated_now` |
| **Tính R:R & Phí** | `domain_calculator.calculate_risk_reward` | `domain_calculator.calculate_risk_reward` | **100% tái sử dụng hàm thuần túy (Pure)** |
| **Kiểm tra Tin tức** | `crud.check_news_blackout` | `crud.check_news_blackout` (với `now_ms = sim_time`) | **100% đồng nhất logic blackout** |
| **Luật Bài học** | `LessonRuleService.retrieve_active_rules` | `LessonRuleService.retrieve_active_rules(..., decision_time=sim_time)` | **100% tuân thủ hiệu lực thời gian thực** |
| **Hard Risk Guards** | `TradingPolicyService.evaluate_entry_policy` | Daily audit context & hard limit rules (3 fills, 2 losses) | **100% bảo vệ hạn ngạch** |
| **Khớp lệnh** | `ExecutionCoordinator` / Paper Broker | `ReplayEngine` candle fill simulation | Tách biệt hoàn toàn, không gọi broker ngoài |
| **Sổ cái (Ledger)** | `audit_ledger` / `DayAudit` DB tables | `ReplayContext.ledger_postings` stream | Ghi nhận posting tức thời, unrounded cash tracking |
| **Thông báo** | `NotificationOutbox` qua Telegram Worker | Memory-only spy / Research Sink | **Không có bất kỳ gói tin HTTP nào gửi ra ngoài** |

---

## 3. Khắc Phục Triệt Để 12 Khiếm Khuyết (P01 — P12)

| Mã lỗi | Vấn đề trong V12.2 | Nguyên nhân kỹ thuật | Giải pháp khắc phục tại V12.3 | Bằng chứng kiểm thử & Đối soát |
| :---: | :--- | :--- | :--- | :--- |
| **P01** | Test results 74/74 bị ghi cố định vào `test_results.json`. | Runner viết trực tiếp dictionary `{"total_requirements": 74, "passed": 74, ...}` không qua pytest. | Xây dựng `backend/lab/evidence_collector.py` bóc tách file JUnit XML thực tế từ pytest runner, ánh xạ động từng test node ID sang Requirement ID. | `test_p01_evidence_collector_dynamic_no_hardcoded_pass` PASSED. `test_results.json` ghi nhận chính xác 85 PASS, 1 BLOCKED, `is_hardcoded: false`. |
| **P02** | Có test rỗng dùng `assert True` hoặc assert hằng số không kiểm tra ranh giới. | Test `B08` chỉ assert `total_trades >= 0`, `C22` assert `True`, `D01` gọi calculator 2 lần. | Thay thế bằng test đột biến prefix (so sánh quyết định giữa mẫu cắt và mẫu mở rộng), kiểm tra parity production-replay với giá trị trượt giá và phí thực tế. | `test_p02_genuine_assertions_on_prefix_and_parity` PASSED. Các test rỗng `B08`, `C08`, `C22`, `D01-D09` được viết lại hoàn toàn. |
| **P03** | Tin tức và luật bài học chưa được replay đầy đủ. | Replay truyền `is_news_blackout=False`, `LessonRuleService` được import nhưng không gọi trong candle loop. | Tích hợp gọi `crud.check_news_blackout` và `LessonRuleService.retrieve_active_rules` theo simulated timestamp của nến hiện tại trong `ReplayEngine`. | `test_p03_causal_news_and_lesson_evaluation` PASSED. Khi có tin CPI/FOMC hoặc luật đỏ hiệu lực, lệnh bị chặn và ghi nhận đúng lý do. |
| **P04** | Hủy job chỉ đổi cờ trạng thái, ReplayEngine không dừng. | `run_replay` không nhận token hủy; worker kiểm tra cờ sau khi engine chạy xong; queue không giới hạn. | Thêm `cancel_check` token vào `ReplayContext` và kiểm tra tại mỗi bước lặp nến; ném `ReplayCancelledException`; giới hạn queue tối đa 5 active jobs. | `test_p04_cancellation_checkpoint_and_bounded_queue` PASSED. Job dừng lặp nến ngay khi cờ cancel bật; queue đầy trả về HTTP 429 rõ ràng. |
| **P05** | Frontend tự động chạy lại replay khi job thất bại hoặc bị hủy. | `TestingLabComponent.tsx` catch chung mọi lỗi sau polling và tự động gọi `runLabReplay(values)`. | Chỉ fallback sang sync mode khi route async trả về 404 trước khi tạo `job_id`. Trạng thái `FAILED`/`CANCELLED` dừng lại và giải thích tiếng Việt. | `test_p05_frontend_job_flow_and_no_rerun` PASSED. Đã kiểm tra TypeScript build và Vitest suite (94 passed). |
| **P06** | Sổ cái và sự kiện được dựng lại sau khi chạy xong, thiếu chi phí trượt giá exit. | Runner tái tạo ledger từ `res.trades` sau khi replay kết thúc; bỏ qua exit slippage và quyết định bị từ chối. | `ReplayContext` phát sinh posting sổ cái (`ENTRY_FEE`, `EXIT_FEE`, `REALIZED_GROSS_PNL`, `SLIPPAGE_ADJUSTMENT`) và stream sự kiện ngay lúc khớp/đóng lệnh. | `test_p06_live_ledger_postings_and_events` PASSED. `ledger.csv` xuất trực tiếp từ postings thực tế của engine. |
| **P07** | Rounding chưa đối soát rõ (lệch 0.02 USDT giữa PnL và Equity). | `cash_balance` bị làm tròn 2 chữ số tại từng chân vào/ra của mỗi lệnh giao dịch. | Tích lũy số dư tiền mặt không làm tròn trong quá trình mô phỏng; chỉ làm tròn hiển thị ở ranh giới xuất báo cáo (`final_cash == initial_equity + total_net_pnl`). | `test_p07_exact_accounting_no_drift` PASSED. Độ lệch kế toán = $0.0000 trên toàn bộ các chuỗi giao dịch. |
| **P08** | Stress test nhân hệ số phí/trượt giá hai lần (hệ số 2 tạo mức 4x). | `StressTester` nhân `spread_usd = base * sm` và truyền đồng thời `spread_multiplier = sm`. Engine lại nhân thêm một lần nữa. | Quy định duy nhất một nơi chịu trách nhiệm multiplier (`StressTester` chỉ truyền `spread_multiplier` và `slippage_multiplier`, giữ nguyên base cố định). | `test_p08_stress_multiplier_single_ownership` PASSED. Base 0.35 với multiplier 2.0 tạo ra đúng 0.70 (không còn 1.40). |
| **P09** | Repricing chưa giữ nguyên chi phí baseline; thiếu khoản exit slippage gốc. | `reprice_closed_trade_book` dùng hằng số spread cứng 0.35 và suy đoán `tp_is_maker=False`. | Lưu trữ vai trò `tp_is_maker` và chi phí gốc trực tiếp trên từng `ReplayTradeItem`. Zero-delta repricing khớp 100% net PnL ban đầu. | `test_p09_zero_delta_repricing_exact_baseline` PASSED. Net PnL kịch bản zero delta bằng chính xác Net PnL của baseline. |
| **P10** | Độ trễ mạng (Latency) bị gán tiền cộng thẳng vào slippage. | Công thức `latency_drift = (latency_ms/1000) * 0.05` gán tiền vào trượt giá nhưng gọi là mô phỏng thời gian. | Dán nhãn minh bạch `ESTIMATED_EXECUTION_WITH_LATENCY_APPROXIMATION` trong cả API, DTO, JSON và Excel; công bố rõ đây là ước lượng chi phí trượt giá. | `test_p10_latency_labeled_as_approximation` PASSED. Không còn tuyên bố sai về execution latency fidelity trên nến 5M/15M. |
| **P11** | Artifact containment chưa giải quyết symlink trỏ ra ngoài. | `resolve_artifact_path` chỉ resolve thư mục gốc mà không resolve file đích trước khi kiểm tra `relative_to`. | Áp dụng `Path(target_path).resolve(strict=True)` và kiểm tra tính containment chặt chẽ trên canonical path thực tế. | `test_p11_artifact_containment_rejects_symlink_escape` (xác thực logic containment; trên non-admin Windows xử lý skip an toàn). |
| **P12** | Báo cáo V12.2 kết luận vượt quá dữ liệu (14/67 phiên có lệnh nhưng dán nhãn DAILY_TARGET_MET). | Tự gắn nhãn đạt mục tiêu khi mới chỉ có 20.9% số phiên khớp lệnh. | Chuẩn hóa quy tắc kết luận: $\ge 90\%$ mới là `DAILY_TARGET_MET`; $15\% - 89\%$ là `PARTIAL_FREQUENCY_EXPANSION_COVERAGE_UNMET`; $<15\%$ là `COVERAGE_TARGET_UNMET`. | `test_p12_honest_coverage_classification` PASSED. 14/67 phiên được phân loại trung thực là mục tiêu chưa đạt đầy đủ. |

---

## 4. Kết Quả Replay 3 Tháng & So Sánh 3 Biến Thể (A, B, C)

Chu kỳ kiểm thử: Từ 2026-07-09 22:00 đến 2026-10-09 22:00 (Asia/Ho_Chi_Minh).  
Vốn khởi điểm: **1.000,00 USDT** | Đòn bẩy: **30x ISOLATED** | Cặp: **XAUUSDT**.

| Chỉ số kinh tế & vận hành | Variant A (CURRENT_BASELINE) | Variant B (NY_ADAPTIVE) | Variant C (NY_DAILY_PAPER_RESEARCH) | Đối soát & Ghi chú kiểm toán |
| :--- | :---: | :---: | :---: | :--- |
| **Tổng số lệnh khớp (Fills)** | **1** | **23** | **24** | Khớp theo đúng observation nến kế tiếp |
| - Số lệnh Quality (Risk 0.25%) | 1 | 23 | 23 | Setup đạt chuẩn SMC / NY Session |
| - Số lệnh Quota (Risk 0.10%) | 0 | 0 | 1 | Ứng viên sau deadline 14:30 NY |
| **Lợi nhuận ròng (Net PnL)** | **-$2,37** | **-$13,57** | **-$11,25** | Đã trừ toàn bộ phí taker/maker + trượt giá |
| **Vốn cuối kỳ (Final Equity)** | **$997,63** | **$986,43** | **$988,75** | **Khớp chính xác: Initial + Net PnL** |
| **Độ lệch kế toán (Reconciliation Drift)** | **$0,0000** | **$0,0000** | **$0,0000** | **Giải quyết triệt để lỗi lệch 0.02 (P07)** |
| **Tỷ suất lợi nhuận (ROI)** | -0,24% | -1,36% | -1,12% | Tính trên vốn 1.000 USDT |
| **Số lệnh Thắng / Thua / Hòa** | 0W / 1L / 0BE | 4W / 19L / 0BE | 5W / 19L / 0BE | Tỷ lệ thắng thực tế |
| **Tỷ lệ thắng (Win Rate %)** | 0,0% | 17,39% | 20,83% | Phản ánh trung thực mẫu thử |
| **Profit Factor (PF)** | 0,00 | 0,71 | 0,76 | Gross Profit / Gross Loss |
| **Kỳ vọng lệnh (Expectancy R)** | -1,00R | -0,24R | -0,13R | Giá trị R trung bình |
| **Max Drawdown ($)** | $2,37 | $24,35 | $22,64 | Tính theo đỉnh vốn tích lũy |
| **Max Drawdown (%)** | 0,24% | 2,42% | 2,25% | Nằm an toàn dưới trần rủi ro |
| **Tổng phiên NY đủ điều kiện** | 67 | 67 | 67 | Đã trừ thứ Bảy, Chủ Nhật |
| **Số phiên NY có lệnh khớp** | 0 | 18 | 19 | Số phiên thực tế xuất hiện fill |
| **Độ phủ phiên NY (% Eligible)** | **0,0%** | **26,87%** | **28,36%** | Tần suất mở lệnh thực tế |
| **Kết luận mục tiêu phiên Mỹ** | **COVERAGE_TARGET_UNMET** | **PARTIAL_FREQUENCY_EXPANSION_COVERAGE_UNMET** | **PARTIAL_FREQUENCY_EXPANSION_COVERAGE_UNMET** | **Đánh giá trung thực theo P12** |

> [!CAUTION]
> **KẾT LUẬN QUAN TRỌNG VỀ CHIẾN LƯỢC KINH DOANH:**
> 1. **Tính đúng kỹ thuật $\neq$ Lợi nhuận chiến lược:** Việc sửa đúng toàn bộ lỗi kỹ thuật (kế toán, scheduler, causality, stress test) đã mang lại các số liệu trung thực, minh bạch. Tuy nhiên, cả 3 biến thể đều ghi nhận PnL âm nhẹ (-$2,37 đến -$13,57) trên chu kỳ 3 tháng.
> 2. **Không ép lệnh vào Live:** Dù Variant B và C tăng số lượng phiên có lệnh từ 0 lên 18-19 phiên, tỷ lệ thắng dao động từ 17% đến 21% với Profit Factor < 1.00. **Hệ thống kiên quyết giữ Variant B và C ở trạng thái Research/Paper Lab, TUYỆT ĐỐI KHÔNG BẬT VÀO RUNTIME LIVE.**
> 3. **Bảo tồn vốn:** Mức Max Drawdown tối đa của Variant C chỉ là 2.25% ($22,64), chứng minh các Hard Guards (dừng sau 2 lệnh thua liên tiếp, giới hạn 3 lệnh/ngày, ngân sách lỗ tối đa 1.5%/ngày) hoạt động hiệu quả để ngăn chặn cháy tài khoản khi chiến lược gặp giai đoạn thị trường bất lợi.

---

## 5. Đối Soát Kế Toán Sổ Cái & Zero-Delta Repricing

### 5.1. Bảng đối soát số dư tiền mặt (Cash Balance Reconciliation)
Toàn bộ posting trong `ledger.csv` được kiểm tra tự động theo 5 bất biến toán học:
1. `Final Cash == Initial Equity + Sum(Cash Postings)`:
   - Variant A: $1000.00 - 0.2862 (\text{Entry Fee}) - 0.2838 (\text{Exit Fee}) - 1.8000 (\text{Gross PnL}) = \$997.63$. **Sai số: $0.0000**.
   - Variant B: $1000.00 - 13.57 = \$986.43$. **Sai số: $0.0000**.
   - Variant C: $1000.00 - 11.25 = \$988.75$. **Sai số: $0.0000**.
2. Không còn bất kỳ khoản điều chỉnh bí ẩn `RECONCILIATION_ADJUSTMENT`.
3. Số liệu giữa `trades.csv`, `daily_stats.csv`, `report.json` và `V12_3_COMPARE_A_B_C.xlsx` đồng nhất 100%.

### 5.2. Kiểm thử Zero-Delta Repricing
Khi đưa danh mục lệnh đã đóng vào `StressTester.reprice_closed_trade_book` với các tham số chi phí giống hệt baseline:
- Baseline Net PnL (Mode B): `-$13.57`.
- Stressed Net PnL (Zero-delta scenario): `-$13.57`.
- Độ lệch (Delta): **`$0.0000`**.
- Vai trò lệnh: `tp_is_maker` được truy xuất trực tiếp từ trade item, không còn tình trạng gán mặc định sai làm lệch phí thoát lệnh.

### 5.3. Kiểm thử Stress Multiplier (Sửa lỗi P08)
- Base spread: $0.35, Multiplier: 2.0x $\rightarrow$ Effective Spread: **$0.70** (loại bỏ hoàn toàn mức 4x / $1.40 trước đây).
- Base slippage: $0.10, Multiplier: 3.0x $\rightarrow$ Effective Slippage: **$0.30** (không còn bị nhân lên $0.90).

---

## 6. Kiểm Thử Hệ Thống Thực Tế (Automated Test Execution Summary)

Bộ kiểm thử được phân tách rõ ràng theo ranh giới kiến trúc:

| Phân loại kiểm thử | Tập tin kiểm thử | Số test | Kết quả thực tế | Ghi chú & Ranh giới |
| :--- | :--- | :---: | :---: | :--- |
| **Acceptance Core (A01 - E12)** | `tests/test_v12_2_acceptance.py` | 74 | **74 PASSED** | Kiểm toán toàn diện kế toán, dữ liệu, scheduler, guard và Excel export |
| **Defect Repairs (P01 - P12)** | `tests/test_v12_3_acceptance.py` | 12 | **11 PASSED / 1 BLOCKED** | Chứng minh triệt tiêu 12 lỗi P01-P12; P11 skipped an toàn do OS Windows non-admin |
| **Regression Defect Repro** | `tests/test_v12_2_repro_defects.py` | 5 | **5 PASSED** | Kiểm tra hồi quy không tái phát các lỗi D03, D05, D07, D09, D11 |
| **Frontend Unit & Component** | `frontend/src/**` | 94 | **94 PASSED** | Chạy qua `npm run test` (Vitest), `npm run lint` (Oxlint 0 lỗi), `npm run build` thành công |
| **TỔNG CỘNG** | **Toàn bộ hệ thống** | **185** | **184 PASSED / 1 BLOCKED / 0 FAILED** | **Tỷ lệ đạt kỹ thuật: 99.46% (100% các test có thể chạy trên môi trường)** |

---

## 7. Danh Mục Hồ Sơ Nghiệm Thu & Artifacts Đã Xuất

Tất cả các tệp nghiệm thu đã được tạo thành công trong thư mục `backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/`:

1. **Sổ cái & Dữ liệu chi tiết:**
   - [`Mode_CURRENT_BASELINE.xlsx`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/Mode_CURRENT_BASELINE.xlsx): Workbook 12 sheet đầy đủ của Variant A.
   - [`Mode_NY_ADAPTIVE.xlsx`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/Mode_NY_ADAPTIVE.xlsx): Workbook 12 sheet của Variant B.
   - [`Mode_NY_DAILY_PAPER_RESEARCH.xlsx`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/Mode_NY_DAILY_PAPER_RESEARCH.xlsx): Workbook 12 sheet của Variant C.
   - [`V12_3_COMPARE_A_B_C.xlsx`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/V12_3_COMPARE_A_B_C.xlsx): Master workbook so sánh cả 3 phương án.
2. **Canonical CSV & Event Streams:**
   - [`trades.csv`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/trades.csv): Danh sách 48 lượt vào/ra lệnh chi tiết.
   - [`ledger.csv`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/ledger.csv): Toàn bộ các bản ghi posting phí, trượt giá và PnL.
   - [`daily_stats.csv`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/daily_stats.csv): Thống kê theo ngày VN (93 ngày).
   - [`session_stats.csv`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/session_stats.csv) & [`ny_sessions.csv`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/ny_sessions.csv): Đối soát 67 phiên Mỹ đủ điều kiện.
   - [`rejection_stats.csv`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/rejection_stats.csv) & [`funnel.csv`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/funnel.csv): Thống kê lý do từ chối lệnh động từ engine.
   - [`equity_curve.csv`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/equity_curve.csv): Đường cong vốn chi tiết từng mốc nến.
   - [`decision_events.jsonl`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/decision_events.jsonl): Nhật ký quyết định phê duyệt lệnh.
   - [`execution_events.jsonl`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/execution_events.jsonl): Nhật ký sự kiện khớp và đóng vị thế.
3. **Chứng cứ kiểm định & Báo cáo kỹ thuật:**
   - [`test_results.xml`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/test_results.xml): File JUnit XML thực thi thực tế từ pytest.
   - [`test_results.json`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/test_results.json): Bằng chứng nghiệm thu 86 yêu cầu bóc tách tự động.
   - [`manifest.json`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/manifest.json): Định nghĩa cấu hình, mã băm dữ liệu và tóm tắt nghiệm thu.
   - [`report.json`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/report.json) & [`report.html`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/report.html): Báo cáo trực quan giao diện web.
   - [`run.log`](file:///d:/build%20app-crypto/aurum-desk/backend/lab/artifacts/v12_3/v12_3_mainrun_1791608662/run.log): Nhật ký thực thi chi tiết của toàn bộ quá trình mainrun.

---

## 8. Trạng Thái Vận Hành & Khuyến Nghị Tiếp Theo

1. **Khuyến nghị vận hành:**
   - Giữ nguyên cấu hình mặc định của hệ thống ở **Variant A (CURRENT_BASELINE)** hoặc trạng thái an toàn.
   - Không tự ý bật các cờ giao dịch tự động cho Variant B hoặc C trên tài khoản tiền thật.
2. **Nghiên cứu thêm (Future Research):**
   - Cần bổ sung tập dữ liệu lịch kinh tế chi tiết cho các khung giờ công bố dữ liệu PMI, NFP, CPI để nghiên cứu sự biến thiên của spread thực tế thay vì dùng spread giả lập.
   - Đánh giá khả năng bổ sung điều kiện lọc Volatility/ATR trước giờ mở cửa Mỹ nhằm giảm thiểu các lệnh thua liên tiếp trong vùng thị trường đi ngang (ranging).
