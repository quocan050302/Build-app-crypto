# AURUM DESK V17.8.0.2 — ISOLATED BACKEND PREFLIGHT REPORT (PHASE G)

## 1. Preflight Execution Environment
- **Artifact File:** `aurum-desk/docs/artifacts/v17_8_0_2/run_v17_8_0_2_1793186387461_14624/runtime/observe_only_preflight.json`
- **Raw Uvicorn Log:** `aurum-desk/docs/artifacts/v17_8_0_2/run_v17_8_0_2_1793186387461_14624/logs/g57_observe_only_uvicorn_preflight.log`
- **Command:** `python -m uvicorn main:app --host 127.0.0.1 --port 8815`
- **Environment:** `AURUM_BE_RUNTIME_PROFILE=OBSERVE_ONLY`, `AURUM_DB_PATH=C:\Users\ADMIN\AppData\Local\Temp\aurum_test_root_crkv80ox\observe_only_preflight_run_v17_8_0_2_17.db`
- **Preflight Status:** `PASS`

## 2. Database Byte-for-Byte Immutability Proof
| Measurement | Value |
|---|---|
| `db_size_bytes_before` | `925696` |
| `db_size_bytes_after` | `925696` |
| `db_sha256_before` | `28c4620f426c606298a51ff849fc02fd92fadc7594a8e583c95784e58fe475ce` |
| `db_sha256_during` | `28c4620f426c606298a51ff849fc02fd92fadc7594a8e583c95784e58fe475ce` |
| `db_sha256_after` | `28c4620f426c606298a51ff849fc02fd92fadc7594a8e583c95784e58fe475ce` |
| `wal_exists_after` | `false` |
| `shm_exists_after` | `false` |
| `paper_orders_row_count` | `0` |
| `db_immutable` | `true` |

## 3. Verified Safety Counters
```text
PAPER_ORDERS_CREATED = 0
PAPER_ORDERS_FILLED = 0
PAPER_POSITIONS_CLOSED = 0
LIVE_EXCHANGE_ORDERS_SENT = 0
UNAUTHORIZED_DB_WRITES = 0
BACKGROUND_TRADING_WORKERS_STARTED = 0
UNEXPECTED_RUNTIME_ERRORS = 0
BLOCKED_HTTP_MUTATION_ATTEMPTS = 2
```

## 4. Verified HTTP Endpoints & Negative Controls
- `GET /health` -> `200 OK`
- `GET /api/v1/runtime/profile` -> `200 OK` (`runtime_profile="OBSERVE_ONLY"`, `observe_only=true`, `trading_enabled=false`)
- `GET /api/v1/policy/stop-placement` -> `200 OK` (`mode="SHADOW_COMPARE"`, `v18_structural_intelligence_enabled=false`)
- `GET /api/v1/observation/status` -> `200 OK`
- `GET /api/v1/paper/orders` -> `200 OK` (`[]`)
- `GET /api/v1/orders/pending` -> `200 OK` (`[]`)
- `GET /api/v1/research/stop-placement/replay-comparison` -> `200 OK` (`NO_REPLAY_RESULTS`)
- `GET /api/v1/replay/stop-placement-comparison` -> `200 OK` (`NO_REPLAY_RESULTS`)
- `POST /api/v1/orders/paper` (Negative Control) -> `403 Forbidden` (`OBSERVE_ONLY_MUTATION_BLOCKED`)
- `PUT /api/v1/policy/stop-placement` (Negative Control) -> `403 Forbidden` (`OBSERVE_ONLY_MUTATION_BLOCKED`)
