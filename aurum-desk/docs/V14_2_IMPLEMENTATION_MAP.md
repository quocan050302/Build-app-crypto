# V14.2 — Implementation Map (Entry Guards)

Branch `fix/v14-1-rr-flow`, base `a47c35b9be95533bdb82a83908dd7d513935aa9f`.
Paths are relative to `aurum-desk/`. Status: **EXISTING** (untouched), **MODIFIED**, **NEW**.

## 1. Backend

| Status | File / symbol | Input → Output | Real callers | Tests |
|---|---|---|---|---|
| NEW | `backend/services/news_blackout_service.py` — `normalize_impact`, `is_fed_event`, `compute_news_blackout` (pure), `evaluate_news_blackout(db, now_ms)` (read-only ORM adapter), `NewsBlackoutView` | events + `now_ms` → `NewsBlackoutView` (window start/end, ceil remaining, primary = latest end, covered ids, calendar_status) | `crud.check_news_blackout`, `trading_policy_service._news_gate`, `entry_guard_service._check_news`, `news_service._import_impact` | `test_v14_2_news_blackout.py` |
| MODIFIED | `backend/crud.py::check_news_blackout` | `(db, now_ms)` → legacy tuple `(bool, reason, minutes)` (unchanged shape; thin wrapper over the service) | `smc_engine`, strategy/UI (unchanged callers) | `test_v14_2_news_blackout.py` (tuple == DTO) |
| MODIFIED | `backend/news_service.py::parse_csv_calendar_preview`, `parse_forex_factory_json` | impact `High/high/red/đỏ` → `High`; missing country/currency stays `""` (never guessed as `USD`) | import endpoints | `test_v14_2_news_blackout.py` |
| MODIFIED | `backend/services/trading_policy_service.py::evaluate_entry_policy` (+ `_news_gate`) | adds `NEWS_BLACKOUT` to actual `allowed/can_enter` in **both** the inactive-policy early return and the active policy; adds `news_view`; return shape/aliases kept; fail closed | `eligibility_service`, `entry_guard_service`, `execution_coordinator`, `strategy_service._auto_arm_candidate` | `test_v14_2_entry_guard.py::test_policy_*` |
| NEW | `backend/services/entry_guard_service.py` — `EntryGuardRequest`, `EntryGuardDecision`, `EntryCheck`, `aggregate_entry_checks`, `evaluate_entry_guard(db, req)`, `entry_mutation_lock`, `serialize_entry_mutation`, `quote_age_ms_of`, `read_execution_feed_health` | request (stage ARM/FILL, plan, calculator result, validated quote, identity) → decision (`allowed`, `blockers`, `warnings`, `news_view`, `lesson_result`, `binding`, `plan_kind`). A missing/UNKNOWN check blocks (`ENTRY_GUARD_DATA_UNAVAILABLE`). Not a second calculator: it consumes `calculate_risk_reward` output. | `eligibility_service`, `main.manual_arm_setup` (via eligibility), `strategy_service._auto_arm_candidate`, `paper_broker.execute_market_order`, `execution_coordinator.evaluate_orders_sync` | `test_v14_2_entry_guard.py` |
| MODIFIED | `backend/services/eligibility_service.py::evaluate_setup_eligibility` | same signature + optional kwargs (`execution_mode`, `origin`, `clock`, `validated_quote`, `feed_health`, `current_order_id`); one ARM guard call replaces the separate state/policy/news/lesson blocks; structure chain = sweep+displacement+FVG evidence (no longer `h1_alignment == ALIGNED`); `revision` now maps to `setup.version`; additive keys `plan_kind, direction_source, stage, allowed, blockers, warnings, news_view, binding, plan_fingerprint, entry_guard, *_version`; all legacy keys kept | `main.manual_arm_setup`, `main.get_upcoming_setups`, `GET /setups/eligibility/{id}` | `test_v14_2_entry_guard.py`, v5.1/v6/v10 suites |
| MODIFIED | `backend/main.py::manual_arm_setup` | decorated with `serialize_entry_mutation`; guard decision comes from eligibility (same decision for response and arm) | HTTP `POST /api/v1/setups/arm[/{id}]` | `test_v14_2_entry_guard.py::test_manual_arm_*`, `test_waiting_*`, `test_retrace_*` |
| MODIFIED | `backend/main.py::create_paper_order` | passes `validation.bid/ask` (canonical), `validated_quote`, `now_ms` to the broker | HTTP `POST /api/v1/orders/paper` | `test_market_*` |
| MODIFIED | `backend/paper_broker.py::execute_market_order` (+ NEW `evaluate_account_guard`) | guard stage FILL / `DIRECT_MARKET` runs after calculator+budget and **before** `crud.create_paper_order` → no orphan order; `ValueError("NEWS_BLACKOUT:…" / "LESSON_RULE_BLOCKED:…" / "ENTRY_GUARD_BLOCKED:…")`; `can_open_position` delegates to `evaluate_account_guard` (same messages) | `main.create_paper_order`, any internal caller | `test_direct_broker_call_*` |
| MODIFIED | `backend/services/execution_coordinator.py::evaluate_orders_sync` | after quote validation + trigger + actual-fill calc → FILL guard with `current_order_id`; transient blocks (stale quote, feed degraded, trigger not hit, data unavailable) keep the order armed with no write; permanent blocks (news, red lesson, RR, account) → one `execute_reject`; allowed → record trace + `execute_fill`. Exits are not gated. | `run_coordinator_loop`, `execution_consumer` | `test_news_after_arm_*`, `test_red_rule_enabled_after_arm_*`, `test_stale_quote_*` |
| MODIFIED | `backend/services/strategy_service.py` (`evaluate_upcoming_setups`, `_auto_arm_candidate`, new `_log_auto_arm_block`) | stores additive `plan_kind` (CONFIRMED/PROVISIONAL) and `direction_source` (SIGNAL/TREND/UNCONFIRMED); auto-arm resolves the atomic plan, runs the real calculator, validates the collector quote and calls the ARM guard (AUTO / AUTO_STRATEGY); blocks are logged once per (setup, version, reasons) | `run_strategy_loop` | `test_auto_arm_*` |
| MODIFIED | `backend/services/entry_decision_service.py::record_stage_decision` | history entry also stores `entry_gate` (stage, news id, lesson ids, plan identity); ARM and FILL snapshots are separate | arm/fill paths | `test_red_rule_enabled_after_arm_*` |
| MODIFIED | `backend/models.py::WatchSetup`, `backend/database.py` (`v7_ws_cols`) | two nullable columns `plan_kind`, `direction_source` (additive `ALTER TABLE`) | strategy_service, eligibility | all |
| EXISTING (untouched) | `domain_calculator.calculate_risk_reward`, `validate_price_geometry`, `plan_resolver.resolve_plan_levels`, `QuoteValidator`, `LessonRuleService`, `TradeLifecycleService.process_exit_tick/execute_close` | single formula/geometry/quote/lesson/exit sources | — | V10/V14.1 suites |
| NEW (test-only) | `backend/tests/e2e_server.py` | isolated temp DB, quote feeder over `/ws`, `/__e2e/*` controls; **not** imported by production | Playwright `webServer` | `frontend/tests/e2e` |

## 2. Frontend

| Status | File / symbol | Purpose | Caller | Tests |
|---|---|---|---|---|
| NEW | `frontend/src/utils/entryGate.ts` — `isSamePlan`, `isEligibilityBoundToIntent`, `getEntryDistanceLabel`, `deriveEntryDecisionView`, types `EntryEligibility`, `PlanBinding`, `NewsView` | binding check (setup id, instance, version, direction, resolved prices), WAITING/BLOCKED/CAN_ARM/ARMED/OPEN view, distance label (`Điểm vào chưa xác nhận` for provisional) | `QuickDecisionSidebar` | `src/v14_2_entry_gate.test.ts` (22) |
| MODIFIED | `frontend/src/components/QuickDecisionSidebar.tsx` | eligibility used only when bound (else `STALE_ELIGIBILITY`, submit disabled); signed net R:R/reward (`??`, no `>0` hiding); `NET_REWARD_NON_POSITIVE`/`UNCONFIRMED_ENTRY` blockers; one decision row (`#entry-decision-status`) + chips opening the **existing** drawer sections (`#entry-news-chip`, `#entry-lesson-chip`); CTA `#entry-submit-cta` enabled only when `decisionView.canSubmit`; coarse `nowMs` (5 s) for freshness | `App.tsx` | unit + e2e |
| MODIFIED | `frontend/src/App.tsx` | `SelectedTradeIntent` + additive `plan_kind`, `plan_fingerprint`, `risk_config_version` filled in `handleFocusSetupOnChart`; `data-setup-id` on setup cards | — | e2e |
| MODIFIED | `frontend/src/utils/userMessageCatalog.ts` | Vietnamese messages `NET_REWARD_NON_POSITIVE`, `ENTRY_GUARD_DATA_UNAVAILABLE`, `UNCONFIRMED_ENTRY`, `STALE_ELIGIBILITY`, `QUOTE_STALE` | sidebar | unit |
| MODIFIED | `frontend/vite.config.ts` | `AURUM_API_TARGET` env (default unchanged `http://127.0.0.1:8000`) so browser tests can proxy to the isolated backend | Playwright | e2e |
| NEW | `frontend/playwright.config.ts`, `frontend/tests/e2e/v14_2_entry_guard.e2e.ts`, script `npm run test:e2e`, devDependency `@playwright/test` | full-App browser tests on the locally installed Google Chrome | — | 9 tests |

## 3. Production entry mutation map (every route that creates an `armed` / `paper_open` order)

| Route | Code | Guard stage | Guard call |
|---|---|---|---|
| manual arm | `main.py::manual_arm_setup` | ARM (MANUAL/MANUAL_WEB) | `evaluate_setup_eligibility` → `evaluate_entry_guard` |
| auto arm (standard + NY fallback) | `strategy_service._auto_arm_candidate` | ARM (AUTO/AUTO_STRATEGY) | `evaluate_entry_guard` |
| manual MARKET (HTTP) | `main.create_paper_order` → `PaperBroker.execute_market_order` | FILL / DIRECT_MARKET | guard inside the broker |
| manual MARKET (direct call) | `PaperBroker.execute_market_order` | FILL / DIRECT_MARKET | same function |
| pending MARKET/LIMIT/STOP | `execution_coordinator.evaluate_orders_sync` → `TradeLifecycleService.execute_fill` | FILL | `evaluate_entry_guard` before `execute_fill` |
| exits | `process_exit_tick`, `execute_close`, manual close, amend SL | none (by design) | — |

`backend/lab/*` (scenario/soak/v9 runners) seed `armed`/`paper_open` rows directly. They are the Testing Lab, are not reachable from the live entry routes above and were **not changed** in V14.2; their DB isolation was not re-verified here.

## 4. Check order and primary reason

ARM: identity → strategy → geometry → financial → account → session → news → lessons → quote → feed.
FILL: identity → order_state → trigger → geometry → financial → account → session → news → lessons → quote → feed.
The primary reason is the first failing check; every blocker is kept for the detail drawer.
