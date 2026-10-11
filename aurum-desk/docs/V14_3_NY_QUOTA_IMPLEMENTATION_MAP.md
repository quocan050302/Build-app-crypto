# V14.3 — NY Quota / Fallback Implementation Map

Branch `fix/v14-1-rr-flow` · base `e6bd319cebcda9f990dc55b9004b3eda05d48bdc` (V14.2) · version label **V14.3**.
Paths are relative to `aurum-desk/`. Status column: **NEW** = created in V14.3, **MOD** = existing symbol changed,
**EXISTING** = read, reused unchanged.

## 1. Runtime flow after V14.3

```
StrategyService.run_strategy_loop (6 s, unchanged cadence)
 └─ evaluate_upcoming_setups(symbol, tf, *, db, clock, ticker, quote, htf_bias, h1_alignment, feed_health,
 │                          candles_5m, auto_enabled)                                   [MOD: injectable]
 │    ├─ standard 15M SMC flow (unchanged; STANDARD_DATA_MISSING recorded instead of silent return)
 │    └─ evaluate_ny_quota_cycle(db, symbol, NYCycleContext)                            [NEW]
 │         └─ ny_quota_service.run_ny_quota_cycle                                       [NEW]
 │              policy → get_ny_session_window → update_quota_lifecycle_state
 │              → should_search_ny_fallback (pure)                                      [NEW]
 │              → NYFallbackService.evaluate_fallback_setup (pure generator, closed bars) [MOD: rewritten]
 │              → NYOpportunityAuditService.record_*                                    [NEW]
 │              → entry_mutation_lock { upsert_ny_fallback_setup → _auto_arm_candidate } [NEW / MOD]
 └─ execution_coordinator.evaluate_orders_sync → TradeLifecycleService → SessionQuota fill (EXISTING, only actual fills)
```

State link (IDs only, never display names): `WatchSetup.session_instance_id` + `WatchSetup.setup_instance_id`
→ `PaperOrder.session_instance_id` + `PaperOrder.setup_instance_id` → `SessionQuota.session_instance_id`
→ `NYOpportunityAudit(session_instance_id, strategy_family, candidate_key = setup_instance_id)`.

## 2. Backend symbols

| Symbol (file) | Status | Inputs → outputs | Callers | Tests |
|---|---|---|---|---|
| `should_search_ny_fallback` (`services/ny_quota_helpers.py`) | NEW | policy_active, mode, fallback_enabled, now/window/fallback bounds, target, total → `bool` (permission to **search**, never to arm/fill) | `trading_policy_service._fallback_search_allowed`, `run_ny_quota_cycle` | `test_v14_3_ny_quota.py` (search policy, trap) |
| `remaining_ny_risk_budget`, `effective_fallback_risk_pct` (same) | NEW | finite budget/equity/caps → remaining USDT / risk % (floor-rounded, hard cap 0.10, 0 when budget 0) | `compute_remaining_ny_risk_budget`, generator | budget tests |
| `closed_candles_at`, `validate_candle_ohlc` (same) | NEW | candles+now+duration → sorted, deduped closed bars / data issue code | generator, strategy reader | closed-bar tests |
| `ny_setup_instance_id`, `ny_signal_id`, `stable_hash`, `fallback_expiry_ms` (same) | NEW | session+symbol+direction+FVG → stable identity ≤ column length; `min(now+45m, window_end)` | generator, upsert | identity/expiry tests |
| `REASONS`, `classify_reason`, `primary_reason` (same) | NEW | code → (DATA/STRATEGY/SAFETY/EXECUTION/SYSTEM, Vietnamese text) | audit service, API | taxonomy tests |
| `NYFallbackService.evaluate_fallback_setup` (`services/ny_fallback_service.py`) | MOD (rewritten, pure) | kw-only `now_ms/clock/window/quote/requested_risk_pct`, closed M5 + HTF → `{strategy_candidate, structure, candidate, is_eligible, reason_code, reason_category, diagnostics}` | `run_ny_quota_cycle` | positive LONG/SHORT, closed-bar, budget, calculator-rejection tests |
| `upsert_ny_fallback_setup` (`services/ny_quota_service.py`) | NEW | db, candidate, session, now_ms → `{outcome: CREATED/UPDATED/UNCHANGED/FROZEN/TERMINAL_RETAINED, row, binding}`; sets `invalidation_price = SL`; flush; re-reads under `entry_mutation_lock` | `_search_and_act` | NOT NULL, existing row, two-day, persistence tests |
| `run_ny_quota_cycle`, `NYCycleContext`, `compute_remaining_ny_risk_budget` (same) | NEW | context → scan result dict; budget from `DayAudit` (1.5 % of initial equity − realized loss − reserved armed risk) | `StrategyService.evaluate_ny_quota_cycle` | whole-flow tests |
| `StrategyService.evaluate_ny_quota_cycle` (`services/strategy_service.py`) | NEW | `(db, symbol, context)` → dict | `evaluate_upcoming_setups` | whole-flow tests |
| `StrategyService.evaluate_upcoming_setups` | MOD | adds `db/clock/ticker/quote/htf_bias/h1_alignment/feed_health/candles_5m/auto_enabled`; NY cycle always runs; db closed only if owned | `run_strategy_loop`, tests, lab | all integrated tests |
| `StrategyService._auto_arm_candidate` | MOD | adds `clock/quote/feed_health`; returns `{armed, codes, order_id, message}`; NY dedupe by `setup_instance_id` (`CANDIDATE_ALREADY_USED`); order carries `setup_instance_id`; expiry ≤ watch expiry | standard flow, NY cycle | competition, deadline tests |
| `_fallback_search_allowed`, `TradingPolicyService.update_quota_lifecycle_state` (`services/trading_policy_service.py`) | MOD | same predicate as the generator; disabled → `SEEKING_STANDARD` + “Fallback đang tắt”; `get_or_create_session_quota` uses `p.ny_min_fills` | strategy, endpoints | policy/DTO parity tests |
| `TradingPolicyService.evaluate_entry_policy` | MOD | `is_fallback_active = window["is_fallback_active"]`; adds `quota_reason`; blocks `NY_QUOTA_PAPER` outside window | `EntryGuardService._check_session` | window/DTO parity, deadline tests |
| `EntryGuardService._check_session` (`services/entry_guard_service.py`) | MOD | forwards `strategy_family` | arm / fill | deadline tests |
| `NYOpportunityAuditService` (`services/ny_opportunity_audit_service.py`) | NEW | `record_scan/record_candidate/mark_stage/funnel/finalize_session_summary/build_diagnostics`; dedupe by (session, family, candidate_key) | cycle, diagnostics API | dedupe/restart/summary tests |
| `models.WatchSetup.session_instance_id`, `NYOpportunityAudit`, `NYSessionAudit` (`models.py`) | MOD/NEW | additive columns/tables; `invalidation_price` stays `nullable=False` | upsert, audit | migration (twice) test |
| `database.run_schema_migrations` | MOD | additive `watch_setups.session_instance_id` (nullable; old rows keep NULL) | startup | migration test |
| `GET /api/v1/ny-quota/diagnostics`, `GET /api/v1/policy/ny-session` (`main.py`, `schemas.py`) | NEW / MOD | typed read model; null = not measured; ny-session now uses `live_clock` and returns `fallback_enabled, fallback_start_ny, window_end_ny, quota_reason, target_fills` | UI | endpoint tests |
| `lab/ny_quota_frequency_audit.py` | NEW | M5/HTF CSV + date range → `daily_funnel.csv, sessions.csv, blockers.csv, summary.json`; isolated in-memory DB per day; production cycle/coordinator | CLI | `test_v14_3_frequency_audit.py` |

## 3. Frontend symbols

| Symbol | Status | Notes |
|---|---|---|
| `utils/nyQuotaLabels.ts` | NEW | pure labels; counter = actual fills only; `null → "—"` |
| `NYSessionPanel.tsx` | MOD | one reason line; `SEEKING_FALLBACK` shown as searching only when flag on; test ids |
| `components/NYQuotaDiagnosticsCard.tsx` | NEW | inside the existing session drawer; date filter, GET only; server decides "today" |
| `api/client.ts` | MOD | `NYSessionStatus` additive fields, `NYQuotaDiagnostics`, `api.getNYQuotaDiagnostics` |
| `TradeDetailsDrawer.tsx` | MOD | renders the card (no new tab/panel) |

## 4. Test-only tooling (not production)

`tests/ny_quota_fixtures.py` (explicit-closed M5 stories, DST-safe NY times), `tests/e2e_server.py` (adds an
injectable launcher clock + `/__e2e/ny/*`; production `main.py` has no backdoor), `tests/e2e/v14_3_ny_quota.e2e.ts`.
