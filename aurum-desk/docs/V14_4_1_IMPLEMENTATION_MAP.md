# V14.4.1 Implementation Map

Branch `fix/v14-1-rr-flow`, base reviewed commit `b444d76` (feat(v14.4)).
Scope: turn the V14.4 scaffolding into a flow that is **actually called in production** and is **proven in a real
browser**. Labels: **[HIỆN CÓ]** = existed in the base, **[MỚI]** = added by V14.4.1.

> This document describes what the code does today. Anything not listed under "Implemented" is listed under
> "Not implemented / NOT_VERIFIED" in [V14_4_1_ACCEPTANCE_REPORT.md](V14_4_1_ACCEPTANCE_REPORT.md).

## 1. Product acceptance contract

1. Read the context of all three sessions (Asia / Europe / NY, app research convention) and publish **conditional
   forecasts** ("nếu … thì …") with facts, watched liquidity, confirmation requirements, target/invalidation and
   as-of time. Forecasts never claim intent, never claim a win probability.
2. Confirm SMC causally: sweep -> measured displacement break -> real FVG bound to the displacement leg -> closed
   retest, all with `known_at <= cutoff`. Only then may a setup be created.
3. A setup is **PAPER only** and goes through the unchanged guards (news / lessons / account / quote / feed / cost /
   policy). Default `NY_ONLY`, entry window 08:00–11:00 NY, max 3 fills/day, one position, V14.3 fallback cap 0.10 %.
   Three-session analysis does **not** enable Asia/Europe entries.
4. Selecting a scenario / setup / order shows the evidence of **that object only** on a clean chart.
5. No probability, no winrate is invented: unavailable = `null` + `UNAVAILABLE`/`NOT_VERIFIED`.

Definitions that are never mixed: forecast · structure confirmed · entry eligible · order filled (PAPER) ·
empirical net winrate · predicted TP-before-SL probability · checklist/ranking score (never shown with a % win).

## 2. Production wiring (call chain)

```
StrategyService.evaluate_upcoming_setups                      [HIỆN CÓ, now the real caller]
 └─ StrategyService.evaluate_market_reading_cycle             [MỚI]  exception -> SYSTEM_ERROR, never blocks exits/fallback
     ├─ smc_scenario_service.load_closed_context              D/4H/1H/15M/5M, fail-closed data issues
     ├─ smc_scenario_service.evaluate_scenarios               pure; sessions + liquidity state + MTF chain + plan
     ├─ smc_scenario_repository (upsert/transition/immutable snapshot)  one owner transaction, commit, THEN event
     ├─ session_forecasts_cache (smc_session_forecast_service) read-only cache for the forecast GET
     └─ promote_confirmed_scenario -> _upsert_watch_setup      same WatchSetup path, no second broker pipeline
 └─ _auto_arm_candidate -> entry_guard_service (certified chain, exact binding) -> PaperOrder(ARMED) [frozen refs]
ExecutionCoordinator.evaluate_orders_sync -> BEFORE_FILL guard recheck -> fill -> quota (actual fills only)
```

## 3. Signature and caller map

| Function / contract | Signature (actual) | Production caller | Real-call tests |
|---|---|---|---|
| `evaluate_scenarios` [HIỆN CÓ, rewritten] | `(symbol, context: ClosedContext, previous_scenarios, config, now_ms, quote=None, settings=None, entry_window=None) -> List[ScenarioSnapshot]` | `evaluate_market_reading_cycle` | `test_v14_4_1_contracts`, `test_v14_4_scenario_*`, benchmark |
| `load_closed_context` [HIỆN CÓ] | `(candles_by_tf, now_ms, config) -> ClosedContext` (typed `blocking_issues`) | `evaluate_market_reading_cycle` | contracts, fail-closed tests |
| `classify_regime` [HIỆN CÓ] | `(htf_bias, h1_alignment, atr_relative=None)` fed by `build_htf_context` [MỚI] | `evaluate_scenarios` | contracts |
| `build_liquidity_map` [HIỆN CÓ] | `(swing_highs, swing_lows, session_ranges: dict, previous_day)` | `evaluate_scenarios` | `test_v14_4_liquidity` |
| `select_unconsumed_target` [HIỆN CÓ] | exact args from `smc_liquidity_service` (no `**kwargs`) | `evaluate_scenarios` after `update_liquidity_state` | liquidity tests |
| `make_scenario_identity` [HIỆN CÓ] | `(symbol, session_id, family, direction, anchor_id)`; date from injected `now_ms`+ZoneInfo | `evaluate_scenarios` | sessions tests |
| `adapt_detected_fvg` [MỚI] | detector `type/top/bottom/timestamp/confirmed_at` -> typed FVG evidence (exact bounds) | `evaluate_scenarios` | contracts (both directions) |
| `build_scenario_plan` [HIỆN CÓ] | financial settings from the saved risk profile; no `$1000/5x` default; no ±20 target | `evaluate_scenarios` | pipeline tests |
| `build_selected_chart_bundle` [HIỆN CÓ] | `(snapshot, source_type=, binding_id=)` -> compact bundle (swings, events, displacements, retests, zones, liquidity, path, plan) | scenario / setup chart endpoints | contracts |
| `build_selected_chart_bundle_from_dict` [HIỆN CÓ] | order branch: ACTUAL order geometry + frozen evidence | `GET /orders/{id}/chart-evidence` | contracts |
| `validate_entry_evidence_bundle` [HIỆN CÓ] | exact binding: setup id / instance / revision / direction / symbol / plan hash / versions / digest / cutoff | manual **and** auto arm (`eligibility_service`, `entry_guard_service`) | entry-guard tests |
| `evaluate_session_forecasts` [MỚI] | as-of Asia / London / NY forecasts, partial-session aware | market-reading cycle -> `session_forecasts_cache` | `test_v14_4_1_session_forecasts` |
| `get_session_forecasts` [MỚI] | `GET /api/v1/smc/session-forecasts?symbol&day` — **cached, read-only** | frontend `useSessionForecasts` | contracts |
| candles window [HIỆN CÓ, extended] | `GET /api/v1/candles/{symbol}/{tf}?start_ms&end_ms` (both or 422; bounded 500 rows; DB, not live cache) | `ChartComponent.runFocus` | contracts |
| `ProbabilityEstimate`, `wilson_interval`, `estimate_expected_net_r`, registry [MỚI] | `smc_probability_*` | forecast/scenario DTO `probability` field | probability tests |

## 4. Endpoints

| Endpoint | Source of the selection |
|---|---|
| `GET /api/v1/smc/scenarios/{id}/chart-evidence?snapshot_id=` | Scenario click (exact snapshot, 409 on mismatch) |
| `GET /api/v1/setups/{id}/chart-evidence?setup_instance_id&expected_revision&plan_fingerprint` | Setup click (409 on any mismatch, never "latest") |
| `GET /api/v1/orders/{id}/chart-evidence` | Order click (top-level bundle, frozen at arm, actual Entry/SL/TP) |
| `GET /api/v1/smc/session-forecasts` | Sidebar session card |

`PaperOrderResponse` exposes nullable `scenario_snapshot_id`, `quality_snapshot_id`, `evidence_snapshot_id`;
auto-arm writes each ref into **its own table** (the old copy of `evidence_snapshot_id` into `quality_snapshot_id`
is removed).

## 5. Frontend ownership

One selection owner: `useSelectedChart` (key + generation + `AbortSignal`) used by `App.tsx` for
SCENARIO / SETUP / ORDER / DRAFT. The reducer takes the generation from the owner (a second counter drifted under
React StrictMode and silently dropped every response — found by the browser test, fixed). `validateSelectedBundle`
checks source / id / instance / revision / symbol / direction / plan fingerprint / snapshot / cutoff before dispatch.
WS: the real envelope `DOMAIN_EVENT{event:{event_type:'scenario.updated',…}}` is parsed in `utils/scenarioEvents.ts`;
polling runs only while the socket is down.

## 6. Test-only surfaces (never in production builds)

* `backend/tests/e2e_server.py` `/__e2e/smc/reset|state`: seeds the causal fixture candles + pinned clock + quote
  and runs the **real** cycle / guard / arm / coordinator fill.
* `window.__AURUM_CHART_TEST__` in `ChartComponent.tsx` behind `import.meta.env.DEV` (tree-shaken in `vite build`):
  read-only accessor for hit registry / R:R geometry / visible range.
