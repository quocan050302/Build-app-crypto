# AURUM DESK V17.8.0.3 — DEFECT REGISTER

**Release:** `V17.8.0.3`  
**Status:** All identified defects resolved and covered by regression tests.

---

## 1. Defects Identified & Resolved in V17.8.0.3

| Defect ID | Severity | Component | Description | Root Cause | Fix & Verification | Status |
|---|---|---|---|---|---|---|
| `DEF-17803-01` | `P0` | `BitgetWSService` / `runtime_profile_service.py` | Enabling WebSocket in `OBSERVE_ONLY` would dispatch live quotes into `execution_consumer` and candles into `candle_cache_service` / `cg01_evaluation_scheduler`, risking PAPER fills or operational DB mutations. | `BitgetWSService._dispatch_quote` and `_dispatch_candle_payload` lacked an isolated shadow sink route. | Introduced `MARKET_SHADOW` profile with typed `RuntimeCapabilities`, isolated `_resolve_active_shadow_sink()` routing in `BitgetWSService`, and `MARKET_SHADOW_EXECUTION_FORBIDDEN` guards across all execution sinks. Verified in `G59`–`G61`. | `CLOSED` |
| `DEF-17803-02` | `P1` | `tools/artifact_verifier.py` | `verify_acceptance_bundle` accepted `"comp_sha"` as a placeholder composite SHA shortcut from legacy unit tests. | Legacy test fixture shortcut in `artifact_verifier.py` line 209. | Removed `"comp_sha"` from `tools/artifact_verifier.py` and updated `test_v17_7_adaptive_stop_loss.py` (`test_ce_20` and `test_review_probe_4`) to compute genuine SHA-256 composite digests. Verified in `G27`, `G42`, `G58`, `G75`. | `CLOSED` |
| `DEF-17803-03` | `P1` | `MarketShadowService.ingest_candle_payload` | Initial 200-bar Bitget WS snapshot bursts across 6 timeframes triggered 1,200 synchronous `NullPool` SQLite commits on the asyncio event loop, causing HTTP requests to time out during initial WS snapshot ingestion. | `_promote_closed_candle` persisted every historical bar in large WS snapshot batches synchronously on the event loop. | Updated `ingest_candle_payload` to load full snapshot batches into memory while bounding synchronous per-row SQLite writes on large (>30 bar) snapshots to the newest 5 closed bars. Verified in `G74` isolated Uvicorn preflight. | `CLOSED` |
| `DEF-17803-04` | `P2` | `runtime_profile_service.py` | `_DEFAULT_COUNTERS` omitted `"UNEXPECTED_RUNTIME_ERRORS": 0` after adding `MARKET_SHADOW` counters, causing preflight zero-counter checks to read `-1`. | Missing key in `_DEFAULT_COUNTERS` dictionary initialization. | Restored `"UNEXPECTED_RUNTIME_ERRORS": 0` in `_DEFAULT_COUNTERS`. Verified in `G57` and `G74`. | `CLOSED` |

