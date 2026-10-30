# AURUM DESK V17.8.0.3 — OPERATIONS RUNBOOK (`OBSERVE_ONLY` & `MARKET_SHADOW`)

**Release:** `V17.8.0.3`  
**Trading Authorization:** `ZERO`

---

## 1. Starting Backend in `MARKET_SHADOW` (Live Public Feed + Continuous Unicorn Research, Zero Trading)

```bash
export AURUM_BE_RUNTIME_PROFILE=MARKET_SHADOW
export AURUM_SHADOW_DB_PATH="$(pwd)/shadow_data/aurum_shadow_research.db"
PYTHONPATH=backend python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### Expected Behavior in `MARKET_SHADOW`
- Connects to Bitget public WebSocket (`wss://ws.bitget.com/v2/ws/public`) for `XAUUSDT` (`ticker` + `1M`/`5M`/`15M`/`1H`/`4H`/`1D` candles).
- Runs `MarketShadowService.run_shadow_loop()` to evaluate closed-bar SMC/ICT Unicorn setups and persist shadow research into `AURUM_SHADOW_DB_PATH`.
- Operational trading database (`backend/aurum_desk.db`) is strictly read-only (`OPERATIONAL_DB_WRITES = 0`).
- Zero PAPER orders armed, filled, or closed (`PAPER_ORDERS_CREATED = 0`, `PAPER_ORDERS_FILLED = 0`, `PAPER_POSITIONS_CLOSED = 0`).
- Zero LIVE exchange orders (`LIVE_EXCHANGE_ORDERS_SENT = 0`).

---

## 2. Starting Backend in `OBSERVE_ONLY` (Offline Static Inspection, Zero Background Workers)

```bash
export AURUM_BE_RUNTIME_PROFILE=OBSERVE_ONLY
PYTHONPATH=backend python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
```

---

## 3. Running Real Historical Backtest & Verifying Acceptance Bundle

```bash
# Re-run real historical XAUUSDT backtest (Variants A/B/C/D, IS/OOS, stress tests)
PYTHONPATH=backend python3 backend/services/v17_8_backtest_runner.py

# Run full 75-gate acceptance runner
python3 tools/run_v17_8_acceptance.py

# Verify latest acceptance bundle
python3 tools/artifact_verifier.py
```

