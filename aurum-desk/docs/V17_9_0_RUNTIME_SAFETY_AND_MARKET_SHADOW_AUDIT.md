# AURUM DESK V17.9.0 — Runtime Safety and MARKET_SHADOW Audit

- **Release Version**: `V17.9.0`
- **Branch**: `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Tested Executable Source SHA (`FINAL_CODE_SHA`)**: `76f725a232ab3bda5459b40efad95a08dfca7fd1`
- **Market Shadow Verdict**: `SAFE_FOR_MARKET_SHADOW_RESEARCH_ONLY`
- **Trading Authorization**: `AUTO_PAPER_NOT_AUTHORIZED_BY_V17_9_0` / `LIVE_TRADING_NOT_AUTHORIZED`

---

## 1. Preserved Safety Invariants (`G53`–`G74`, `G85`)

All runtime isolation and trading-prohibition invariants from V17.7 through V17.8.0.4 remain intact and verified under V17.9.0:
1. **`OBSERVE_ONLY` Profile (`G53`–`G57`)**:
   - Zero background trading or shadow workers started (`BACKGROUND_TRADING_WORKERS_STARTED == 0`).
   - Operational SQLite DB is opened read-only and verified byte-for-byte immutable before, during, and after Uvicorn preflight (`db_sha256_before == db_sha256_during == db_sha256_after`).
   - All mutating HTTP endpoints (`POST /api/v1/orders/paper`, `PUT /api/v1/policy/stop-placement`) fail closed with HTTP `403`.
2. **`MARKET_SHADOW` Profile (`G59`–`G66`, `G71`–`G74`, `G85`)**:
   - Consumes public market data and evaluates all 5 models through `SharedStrategyEvaluator` (`MarketShadowService.evaluate_shared_decision_snapshot`).
   - Writes exclusively to isolated `shadow_research.db` (`AURUM_SHADOW_DB_PATH != AURUM_DB_PATH`).
   - Operational SQLite DB remains byte-for-byte immutable (`operational_db_sha256_before == operational_db_sha256_during == operational_db_sha256_after`).
   - All 7 execution and mutation safety counters remain strictly `0`:
     - `PAPER_ORDERS_CREATED == 0`
     - `PAPER_ORDERS_FILLED == 0`
     - `PAPER_POSITIONS_CLOSED == 0`
     - `LIVE_EXCHANGE_ORDERS_SENT == 0`
     - `UNAUTHORIZED_DB_WRITES == 0`
     - `BACKGROUND_TRADING_WORKERS_STARTED == 0`
     - `UNEXPECTED_RUNTIME_ERRORS == 0`

