# AURUM DESK V17.8.0.1 — PAPER PILOT READINESS & OPERATOR RUNBOOK

**Version:** V17.8.0.1  
**Branch:** `feat/v17-8-unicorn-structural-sl-tp-intelligence`  
**Execution Scope:** `PAPER` Only (`LIVE` Permanently Prohibited)  

---

## 1. Default Safe State & Non-Activation Guarantee

1. **PAPER Trading Only:** `LIVE_EXCHANGE_ORDERS_SENT == 0` is enforced at the code, policy, and HTTP boundary levels. Any attempt to pass `scope="LIVE"` or `application_scope="LIVE"` to `/api/v1/policy/stop-placement` is rejected with HTTP 400 (`LIVE_SCOPE_FORBIDDEN`).
2. **Default Policy Configuration:**
   - `mode`: `"SHADOW_COMPARE"`
   - `v18_structural_intelligence_enabled`: `False`
   - `v18_unicorn_detection_enabled`: `False`
   - `v18_noise_research_enabled`: `False`
   - `v18_execution_policy_version`: `1`
3. **Explicit Mode Separation (`PAPER_STRUCTURAL` vs `v18_structural_intelligence_enabled`):**
   - Enabling `mode="PAPER_STRUCTURAL"` with `v18_structural_intelligence_enabled=False` runs the V17.7 structural anchor selector (`PROTECTED_SWING` / `SWEEP_EXTREME`) and does **not** activate V17.8 Unicorn Breaker override or displacement-origin fallback.
   - Executing V17.8 structural stops requires explicit operator opt-in (`mode="PAPER_STRUCTURAL"` AND `v18_structural_intelligence_enabled=True`).

---

## 2. Operator Opt-In Procedure (Paper Pilot)

### Step 1: Inspect Current Policy
```bash
curl -s http://127.0.0.1:8000/api/v1/policy/stop-placement
```

### Step 2: Enable Shadow Observation First (Recommended Stage 1)
```bash
curl -X PUT http://127.0.0.1:8000/api/v1/policy/stop-placement \
  -H "Content-Type: application/json" \
  -d '{
    "expected_version": 1,
    "mode": "SHADOW_COMPARE",
    "v18_structural_intelligence_enabled": false,
    "v18_unicorn_detection_enabled": true,
    "v18_noise_research_enabled": true,
    "v18_execution_policy_version": 1,
    "reason": "Enable V17.8.0.1 Unicorn shadow observation"
  }'
```

### Step 3: Opt In to Executable V17.8 Structural Stops in PAPER Mode (Stage 2)
```bash
curl -X PUT http://127.0.0.1:8000/api/v1/policy/stop-placement \
  -H "Content-Type: application/json" \
  -d '{
    "expected_version": 2,
    "mode": "PAPER_STRUCTURAL",
    "v18_structural_intelligence_enabled": true,
    "v18_unicorn_detection_enabled": true,
    "v18_noise_research_enabled": true,
    "v18_execution_policy_version": 1,
    "reason": "Opt in to V17.8.0.1 executable structural SL in PAPER mode"
  }'
```

### Step 4: Instant Rollback to Shadow or Legacy Mode
```bash
curl -X PUT http://127.0.0.1:8000/api/v1/policy/stop-placement \
  -H "Content-Type: application/json" \
  -d '{
    "expected_version": 3,
    "mode": "SHADOW_COMPARE",
    "v18_structural_intelligence_enabled": false,
    "v18_unicorn_detection_enabled": false,
    "reason": "Rollback to SHADOW_COMPARE"
  }'
```

