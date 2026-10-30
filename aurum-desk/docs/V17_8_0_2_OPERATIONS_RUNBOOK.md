# AURUM DESK V17.8.0.2 — SAFE OBSERVATION OPERATIONS RUNBOOK

## 1. Starting the Backend in Safe Observation-Only Mode (Windows PowerShell)

Always run background observation against a dedicated disposable SQLite database (never `backend/aurum_desk.db`):

```powershell
$env:AURUM_BE_RUNTIME_PROFILE = "OBSERVE_ONLY"
$env:AURUM_DB_PATH = "$env:TEMP\aurum_observe_only_runtime.db"
cd "d:\build app-crypto\aurum-desk\backend"
& "d:\build app-crypto\aurum-desk\backend\venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
```

## 2. Verifying Runtime Profile & Safety Counters
Query `/api/v1/runtime/profile` and `/health`:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/runtime/profile" | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" | ConvertTo-Json -Depth 5
```

Confirm:
- `runtime_profile == "OBSERVE_ONLY"`
- `observe_only == true`
- `trading_enabled == false`
- `counters.BACKGROUND_TRADING_WORKERS_STARTED == 0`
- `counters.PAPER_ORDERS_CREATED == 0`
- `counters.PAPER_ORDERS_FILLED == 0`
- `counters.PAPER_POSITIONS_CLOSED == 0`
- `counters.LIVE_EXCHANGE_ORDERS_SENT == 0`
- `counters.UNAUTHORIZED_DB_WRITES == 0`

## 3. Verifying Acceptance Bundle Integrity Offline
```powershell
cd "d:\build app-crypto\aurum-desk"
& "d:\build app-crypto\aurum-desk\backend\venv\Scripts\python.exe" tools/artifact_verifier.py docs/artifacts/v17_8_0_2/run_v17_8_0_2_1793186387461_14624
```
