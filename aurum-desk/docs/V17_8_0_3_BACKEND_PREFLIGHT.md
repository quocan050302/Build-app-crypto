# AURUM DESK V17.8.0.3 — ISOLATED BACKEND PREFLIGHT REPORT

**Release:** `V17.8.0.3`  
**Profiles Verified:** `OBSERVE_ONLY` (`G57`) and `MARKET_SHADOW` (`G74`)

---

## 1. Isolated `MARKET_SHADOW` Uvicorn Subprocess Preflight (`G74`)

1. **Disposable Database Isolation**:
   - Operational SQLite DB created in isolated `test_root`, WAL checkpointed and switched to `journal_mode=DELETE`, with `operational_db_sha256_before` recorded prior to process launch.
   - Dedicated Shadow Research SQLite DB created at a strictly distinct path (`AURUM_SHADOW_DB_PATH`).
2. **Standalone Process Execution**:
   - Launched `python -m uvicorn main:app` with `AURUM_BE_RUNTIME_PROFILE=MARKET_SHADOW`.
3. **Read-Only Shadow API Verification**:
   - `GET /health` -> `200 OK`
   - `GET /api/v1/runtime/profile` -> `200 OK` (`runtime_profile = "MARKET_SHADOW"`, `market_shadow = True`)
   - `GET /api/v1/shadow/market/status` -> `200 OK`
   - `GET /api/v1/shadow/market/quotes` -> `200 OK`
   - `GET /api/v1/shadow/market/candles?timeframe=1M&limit=10` -> `200 OK`
   - `GET /api/v1/shadow/opportunities` -> `200 OK`
   - `GET /api/v1/shadow/unicorn/evidence` -> `200 OK`
   - `GET /api/v1/shadow/research/backtests` -> `200 OK`
4. **Negative-Control HTTP Mutation Verification**:
   - `POST /api/v1/orders/paper` -> `403 Forbidden` (`MARKET_SHADOW_EXECUTION_FORBIDDEN`)
   - `PUT /api/v1/policy/stop-placement` -> `403 Forbidden` (`MARKET_SHADOW_EXECUTION_FORBIDDEN`)
   - `POST /api/v1/shadow/market/status` -> `403 Forbidden` (`SHADOW_API_READ_ONLY_VIOLATION`)
5. **Zero Operational DB Mutation & Safety Counters**:
   - `operational_db_sha256_before == operational_db_sha256_during == operational_db_sha256_after` (`operational_db_immutable = True`)
   - `PAPER_ORDERS_CREATED = 0`
   - `PAPER_ORDERS_FILLED = 0`
   - `PAPER_POSITIONS_CLOSED = 0`
   - `LIVE_EXCHANGE_ORDERS_SENT = 0`
   - `OPERATIONAL_DB_WRITES = 0`
   - `UNAUTHORIZED_DB_WRITES = 0`
   - `BACKGROUND_TRADING_WORKERS_STARTED = 0`
   - `SHADOW_WORKERS_STARTED = 2`

