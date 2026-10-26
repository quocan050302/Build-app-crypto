# BÁO CÁO NGHIỆM THU KỸ THUẬT V16.10 — CG-01 END-TO-END WIRING REPAIR

- **Commit Base**: `74174e0cc2a8dd94b7459d4bed4b3a5afa4184c2`
- **Commit Được Kiểm Thử**: `74174e0cc2a8dd94b7459d4bed4b3a5afa4184c2`
- **Diff Hash Working Tree**: `37fb1ddcb18555fafb725da066f980039a73d1952ccce4ae9d2de44a9c8b6741`
- **Run ID**: `run_v16_10_1792162373`
- **Thời điểm tạo**: 2026-10-16 14:53:13 UTC
- **Technical Status**: `PASS`
- **Historical Status**: `BLOCKED_DATA`
- **Performance Status**: `NOT_EVALUATED`
- **Bằng chứng mô phỏng**: `DATA_SYNTHESIZED_DEMO`
- **Config Hash**: `c968d679c4f87fe5`

---

## 1. Tóm Tắt Trạng Thái Các Cổng Kỹ Thuật (Technical Acceptance Gates)

| Tên Cổng | Yêu Cầu Trường Hợp Kiểm Thử | Trạng Thái | File Bằng Chứng | Lý Do Lỗi (nếu có) |
| :--- | :--- | :---: | :--- | :--- |
| `causal_ohlc_path` | 5/5 cases | **PASS** | `test_v16_10_causal_ohlc_path` | None |
| `arm_sizing_contract` | 4/4 cases | **PASS** | `test_v16_10_arm_to_fill_sizing` | None |
| `funnel_attribution` | 5/5 cases | **PASS** | `test_v16_10_funnel_attribution` | None |
| `runner_result_authority` | 3/3 cases | **PASS** | `test_v16_10_runner_result_authority` | None |
| `legacy_causal_execution` | 5/5 cases | **PASS** | `test_v16_9_causal_ohlc_execution` | None |
| `full_regression_v16` | 1/1 cases | **PASS** | `v16_full_suite` | None |
| `git_cleanliness` | 1/1 cases | **PASS** | `git_diff_check` | None |

---

## 2. Kết Quả Thực Thi Mô Phỏng & Đối Soát Sizing/Funnel

### 2.1 Cấu Hình Hiệu Lực (Authoritative Effective Config)
```json
{
  "initial_capital": 5000.0,
  "risk_pct": 0.1,
  "slippage_usd": 0.0,
  "maker_fee_rate": 0.0002,
  "taker_fee_rate": 0.0004,
  "contract_multiplier": 1.0,
  "capital_mode": "CURRENT_ACCOUNT_BALANCE",
  "execution_model": "ASSUMED_OHLC_PATH",
  "provenance": {
    "initial_capital": 5000.0,
    "risk_pct": 0.1,
    "slippage_usd": 0.0,
    "slippage_source": "CALLER_COSTS",
    "maker_fee_rate": 0.0002,
    "maker_fee_source": "CALLER_COSTS",
    "taker_fee_rate": 0.0004,
    "taker_fee_source": "CALLER_COSTS"
  },
  "authority_pipeline": "CG01ReplayExecutionAdapter + ScalpWatcherOrchestrator + ExecutionCoordinator"
}
```

### 2.2 Sizing & Thực Thi Giao Dịch
- **Số lệnh khớp (Total Fills)**: 1
- **Số lệnh đóng (Closed Trades)**: 0
- **Vị thế mở (Open Positions)**: 1
- **Lệnh chờ (Pending Orders)**: 0
- **Lợi nhuận ròng sau phí (Net Realized PnL)**: None USD
- **Drawdown tối đa MTM**: 0.05%
- **Vốn ban đầu**: 5000.0 USD
- **Vốn kết thúc**: 5000.0 USD

### 2.3 Phân Tách Funnel Chiến Lược & Demo Thực Thi
- **Tổng số chu kỳ (Cycle Count)**: 3
- **Số episode duy nhất (Unique Episode Count)**: 0
- **CG-01 Observed Arms**: 0
- **CG-01 Persisted Arms**: 0
- **Discrepancy (Lệch đối soát)**: []
- **Seeded Demo Orders**: 2

### 2.4 Chi Tiết Phễu Quyết Định (Rejection Funnel)
| Danh Mục | Số Lượng | Mô Tả |
| :--- | :---: | :--- |
| `MODEL_DISABLED` | 0 | CG-01 model disabled in configuration |
| `NO_QUOTE` | 0 | Cycle received no valid quote data |
| `WAIT_WINDOW` | 0 | Outside permitted session/schedule window |
| `WAIT_POI` | 0 | Awaiting higher timeframe POI alignment |
| `CONTEXT_NOT_READY` | 3 | HTF context or warmup insufficient for evaluation |
| `OBSERVING_WAITING_TRIGGER` | 0 | Active observation awaiting valid sweep / displacement |
| `GUARD_BLOCKED` | 0 | Candidate setup blocked by risk/news/account/cooldown guards |
| `SYSTEM_DEGRADED` | 0 | System or database degraded during evaluation cycle |
| `ARMED_CANDIDATES` | 0 | Setups successfully validated and armed for execution |
| `EXPIRED_BEFORE_FILL` | 0 | Armed orders expired prior to reaching limit price |
| `CANCELLED_ORDERS` | 0 | Armed orders cancelled prior to execution |

---

## 3. Danh Mục Tạo Phẩm (Artifacts)

Toàn bộ dữ liệu chứng thực đã được kết xuất tại thư mục `artifacts/run_v16_10_1792162373/`:
- `acceptance.json`: Toàn văn kết quả nghiệm thu kỹ thuật và dữ liệu kiểm toán.
- `manifest.json`: Mã băm SHA-256 đối soát toàn vẹn tất cả file artifact.
- `test_results.json`: Chi tiết thực thi và stdout/stderr của từng test suite.
- `effective_config.json`: Cấu hình tài chính hiệu lực từ driver run_context.
- `strategy_funnel.json`: Thống kê phân rã funnel sạch và đối soát arm CG-01.
- `execution_demo.json`: Thống kê lệnh demo được cô lập riêng.
- `trades.csv`: Sổ lệnh giao dịch có cờ `intrabar_ambiguous`.
- `daily_stats.csv`: Thống kê hạn mức theo ngày giao dịch chuẩn.
- `equity_mtm.csv`: Chuỗi Mark-to-Market và Max Drawdown thời gian thực.
- `equity_realized.csv`: Chuỗi biến động vốn thực tế.
- `rejection_funnel.csv`: Bảng thống kê các giai đoạn loại bỏ lệnh.

---
**Kết luận**: Phiên bản V16.10 hoàn thành xuất sắc việc khắc phục bốn điểm nối: Thực thi OHLC nhân quả không lookahead, Đấu nối chuẩn vốn cố định và tỷ lệ rủi ro qua Paper Candidate Snapshot, Thẩm quyền cấu hình hiệu lực và xác thực dataset, Phân tách phễu quyết định và đối soát không max().