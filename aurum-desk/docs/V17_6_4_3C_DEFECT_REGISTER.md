# AURUM V17.6.4.3c — DEFECT REGISTER
**Version:** V17.6.4.3c  
**Baseline SHA:** `a08097e4cfab20f2a67b3a6ab71be46a3dd978ca`  
**Branch:** `fix/v14-1-rr-flow`  
**Application Root:** `aurum-desk/`  

---

| ID | Priority | Defect Title | Description & Root Cause | Target Location | Status |
|---|---|---|---|---|---|
| **CPA-01** | P0 | Raw quote transport metadata overrides trusted adapter transport | `resolve_watcher_observation_provenance()` reads raw quote `transport_state` before checking adapter or allows raw quote to claim `CONNECTED` when adapter is `DISCONNECTED` or absent. | `backend/services/scalp_watcher_orchestrator.py` | VERIFIED |
| **CPA-02** | P0 | Source metadata can impersonate verified WebSocket provenance | Raw quote dictionary with `quote_source: "WS"` self-authorizes as `WS` without verification against trusted feed adapter. | `backend/services/scalp_watcher_orchestrator.py` | VERIFIED |
| **CPA-03** | P0 | Missing exchange timestamp can still qualify for GOOD | Provenance logic only fails closed if BOTH exchange and receive timestamps are missing; receive-only evidence can become GOOD. | `backend/services/scalp_watcher_orchestrator.py`, `quote_validator.py` | VERIFIED |
| **CPA-04** | P0 | Missing quote epoch may be treated as authoritative | Default `is_authoritative_for_epoch = True` treats missing epoch as matching; quotes without epoch can produce GOOD coverage. | `backend/services/scalp_watcher_orchestrator.py` | VERIFIED |
| **CPA-05** | P1 | Scheduler tests use weak persistence assertions | Assertions like `assert trace_cnt > 0 or cycle_cnt > 0 or res is not None` pass even if no DB records were persisted. | `backend/tests/test_v17_6_4_3c_scheduler_durability.py` | VERIFIED |
| **CPA-06** | P1 | Scheduler lifecycle test does not prove independent durable readback | `test_sc18` calls `s.commit()` but does not read back the cycle record using an independent database Session. | `backend/tests/test_v17_6_4_3c_scheduler_durability.py` | VERIFIED |
| **CPA-07** | P0 | Playwright is absent from mandatory release gates | `run_v17_6_4_3b_acceptance.py` omitted mandatory Playwright browser E2E execution from its required gate list. | `tools/run_v17_6_4_3c_acceptance.py` (Gate G38) | VERIFIED |
| **CPA-08** | P0 | Zero-live-order gate relies on source regex | `verify_zero_live_execution()` only scans Python source files for regex patterns, lacking runtime order-submission boundary verification. | `backend/tests/test_v17_6_4_3c_zero_live_orders_runtime.py`, `run_v17_6_4_3c_acceptance.py` (Gate G39) | VERIFIED |
| **CPA-09** | P1 | Source/evidence reconciliation checks log presence more than integrity | Release verification checks `log_file.exists()` without computing and comparing SHA-256 checksums against manifest. | `tools/run_v17_6_4_3c_acceptance.py` (Gate G43) | VERIFIED |
| **CPA-10** | P1 | Acceptance tests validate a simplified helper instead of the real runner | `test_ai*` tests exercised a standalone mock function rather than the real acceptance runner logic. | `backend/tests/test_v17_6_4_3c_acceptance_integrity.py` | VERIFIED |
| **CPA-11** | P1 | Health and coverage must remain consistent across disconnect/reconnect | Coverage and health state transitions across disconnect, reconnect, and recovery must be strictly reconciled. | `backend/services/engine_observability_service.py`, `test_v17_6_4_3b_health_truth.py` | VERIFIED |
| **CPA-12** | P1 | Existing regression and trading parity evidence is incomplete | Full 46-gate matrix and trading strategy parity must be confirmed without SMC/ICT mathematical drift. | `tools/run_v17_6_4_3c_acceptance.py`, `test_v17_6_4_2_paper_parity.py` | VERIFIED |
