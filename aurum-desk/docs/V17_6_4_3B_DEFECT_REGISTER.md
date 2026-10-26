# AURUM V17.6.4.3b — DEFECT REGISTER
**Version:** V17.6.4.3b  
**Status:** RESOLVED & VERIFIED (100% Gates Passing)  
**Baseline SHA:** `0d08fd8c7762e0e5d066192ba618a0b3baf25fed`  
**Branch:** `fix/v14-1-rr-flow`  

---

| Defect ID | Severity | Defect Description | Root Cause | Remediation | Verification Proof | Status |
|---|---|---|---|---|---|---|
| `CFP-01` | P0 | Watcher replaces missing quote timestamp with current time (`q_ts = ... or t_recv`) | `scalp_watcher_orchestrator.py` defaulted missing timestamps to receive time | Implemented `resolve_watcher_observation_provenance()`; fails closed with `QUOTE_TIMESTAMP_UNVERIFIED` | `test_fp01`, `test_fp19` | **RESOLVED** |
| `CFP-02` | P0 | Watcher replaces missing quote source with `WS` (`q_src = ... or "WS"`) | Hardcoded `"WS"` fallback in watcher and observability service | Resolved source directly from quote/adapter; defaults to `"UNKNOWN"`, degrading coverage | `test_fp02`, `test_fp15`, `test_eh02` | **RESOLVED** |
| `CFP-03` | P0 | Watcher replaces disconnected transport with `CONNECTED` | Ternary check replaced disconnected state with `"CONNECTED"` | Preserved `BitgetWSService.transport_state` faithfully across all states | `test_fp03`, `test_fp04`, `test_fp20` | **RESOLVED** |
| `CFP-04` | P0 | Synthetic canonical snapshot could produce `GOOD` coverage | Non-authoritative quotes lacked epoch check | Injected connection epoch validation; mismatched epochs evaluate to `DEGRADED` | `test_fp07`, `test_fp22` | **RESOLVED** |
| `CFP-05` | P0 | Scheduler gate did not execute scheduler evaluation | Acceptance tested direct watcher instead of `evaluate_symbol_now()` | Created `test_v17_6_4_3b_scheduler_runtime.py` with 18 real execution tests | `SC01` – `SC18`, `G08` – `G17` | **RESOLVED** |
| `CFP-06` | P0 | UNVERIFIED feed could produce `HEALTHY` dashboard health | Dashboard snapshot fell through to `HEALTHY` without requiring `feed_qual == "GOOD"` | Required `feed_qual == "GOOD"` as prerequisite for `HEALTHY`; UNVERIFIED feed yields `UNVERIFIED` | `EH01` – `EH16`, `G18` – `G23` | **RESOLVED** |
| `CFP-07` | P0 | Borrowed unique-conflict returned `ALREADY_EXISTS` without durability proof | In `_persist_interval_record()`, integrity error on borrowed session assumed committed duplicate | Executed independent readback probe; returns `RETRYABLE_FAILURE` unless row is committed | `UC01` – `UC16`, `G24` – `G27` | **RESOLVED** |
| `CFP-08` | P1 | Engine health and dashboard evidence could disagree | Dashboard snapshot masked feed errors if process was running | Separated engine state from evidence health; dashboard reports both accurately | `EH16` | **RESOLVED** |
| `CFP-09` | P0 | Acceptance relied on weak scheduler/safety proof | Incomplete test gate coverage for real scheduler runtime | Expanded acceptance suite to 36 mandatory gates covering full runtime | `tools/run_v17_6_4_3b_acceptance.py` | **RESOLVED** |
| `CFP-10` | P0 | Tested SHA and release evidence needed independent reconciliation | Release proof could drift if post-test files changed | Enforced `G35` SHA match and audit log hash verification before release certification | `G35`, `AI01`, `AI11` | **RESOLVED** |
