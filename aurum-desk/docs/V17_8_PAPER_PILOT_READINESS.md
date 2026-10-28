# AURUM DESK V17.8 — PAPER PILOT READINESS & RUNTIME SAFETY AUDIT

## 1. Readiness Assessment & Deployment Status
- **Release Version:** V17.8.0
- **Execution Target:** Isolated PAPER Environment only.
- **LIVE Dispatch Policy:** Permanently disabled (`LIVE_EXCHANGE_ORDERS_SENT == 0`).
- **Feature Flag Initial State (Reconciled in V17.8.0.1 `DEF-178-08`):**
  - `v18_structural_intelligence_enabled = False` (default off)
  - `v18_unicorn_detection_enabled = False` (default off; operator may enable for Shadow comparison)
  - `v18_noise_research_enabled = False` (default off; operator may enable for Research mode)
  - `v18_execution_policy_version = 1`
  - Default mode: `SHADOW_COMPARE`
  - *(See `docs/V17_8_0_1_PAPER_PILOT_READINESS.md` for full V17.8.0.1 persisted flag lifecycle).*

---

## 2. Safety Invariants Confirmed
1. **No Automatic Structural Activation:** Historical users remain on their existing modes (`LEGACY` or `SHADOW_COMPARE`). Activation of `PAPER_STRUCTURAL` requires explicit operator configuration.
2. **Account Guard Isolation:** Equity limits ($1,000 USDT base, max 2 concurrent orders, max daily loss limits) remain strictly enforced by `PaperBroker.evaluate_account_guard()`.
3. **Database Guard Isolation:** `database.assert_test_database_isolated()` guarantees zero interaction with runtime operational databases during any test or simulation.
4. **Idempotency & Concurrent ARM Safety:** Mutex locking on `ARM` operations prevents duplicate orders or race conditions on the same candidate.

---

## 3. Operator Opt-In Procedure
To enable executable V17.8 structural stop loss placement in paper trading:
1. Ensure the system is running in paper mode.
2. Update the strategy configuration:
   ```json
   {
     "stop_placement_mode": "PAPER_STRUCTURAL",
     "algorithm_version": "V17_8_0",
     "v18_structural_intelligence_enabled": true
   }
   ```
3. Monitor real-time logs via `SLTPIntelligenceView.tsx` on the dashboard.
