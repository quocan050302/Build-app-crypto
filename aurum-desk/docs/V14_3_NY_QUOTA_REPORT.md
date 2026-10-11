# V14.3 — NY Quota / Fallback Repair Report

Branch `fix/v14-1-rr-flow` · base `e6bd319` (V14.2). Scope: **functional correctness of the NY-session PAPER quota flow
and the measurement of why few orders are placed.** This is *not* a performance claim: no win-rate / profit improvement
is asserted, and no guard, cap or min-RR was loosened to reach a 1/1 counter.

## 1. Bugs fixed

| # | Bug (reproduced on `e6bd319`) | Fix |
|---|---|---|
| 1 | `ny_fallback_enabled=false` still produced `SEEKING_FALLBACK`; strategy searched from the quota label | One predicate (`should_search_ny_fallback` → `_fallback_search_allowed`) used by quota state, DTO (`window["is_fallback_active"]`) and the strategy. Disabled ⇒ `SEEKING_STANDARD` + “Fallback đang tắt”; generator is never called |
| 2 | Fallback `WatchSetup` built without `invalidation_price` → `NOT NULL constraint failed` (every fallback silently failed) | Dedicated `upsert_ny_fallback_setup` sets `invalidation_price = validated SL`, flushes inside the real transaction; constraint untouched |
| 3 | Existing `watch-XAUUSDT-5M-fallback` row was never updated; old READY/REJECTED row could be re-armed with a new signal | Upsert with session-scoped instance id: CREATED / UPDATED (whole plan + version++, confirmed triple cleared) / UNCHANGED / FROZEN (ARMED, PAPER_OPEN) / TERMINAL_RETAINED |
| 4 | `time.time()` in generator/strategy | Injected clock/`now_ms` through cycle → generator → upsert → arm → fill; NY session date follows the clock source |
| 5 | Hard-coded `rem_budget=15.0` and a 0.01 % risk floor | `compute_remaining_ny_risk_budget` mirrors the broker guard (1.5 % of initial equity − realized loss − reserved armed risk); budget 0 ⇒ `NO_REMAINING_RISK`; risk floor-rounded, hard cap 0.10 % |
| 6 | Unclosed M5 bars used for ATR/pivots/FVG | `closed_candles_at` (explicit `is_closed`, `ts+5m ≤ decision`, sorted/deduped, conflicting duplicates rejected); ≥15 closed bars; FVG must be confirmed before the retest |
| 7 | `calc_res.reason_code` → `AttributeError` swallowed by the loop | Uses `skip_reason` / `invalid_reason`; controlled `FINANCIAL_REJECTED` |
| 8 | Standard branch returned early when 15M < 20 bars, skipping NY flow/diagnostics | NY cycle always runs; `STANDARD_DATA_MISSING` recorded instead of silence |
| 9 | `test_fallback_activation_at_10_30_ny` passed vacuously | Positive fixture, unconditional `is_eligible is True`, then full chain |
| 10 | `get_or_create_session_quota` used `policy.ny_min_fills` (crash when policy is None) and could raise `IntegrityError` when UI poll and strategy loop create the row concurrently (found by the browser run) | Uses `p.ny_min_fills`; losing the race returns the winner’s row (regression test) |

Also: `NY_QUOTA_PAPER` is blocked outside the NY entry window in the guard (arm at exactly 11:00 ⇒ rejected, quota `MISSED`);
expiry = `min(now+45 m, window_end)`; `/api/v1/policy/ny-session` reads `live_clock` (injectable) instead of the wall clock.

## 2. Measurement (why few orders)

`NYOpportunityAudit` / `NYSessionAudit` (additive tables) store per session: scans, unique candidates (deduped by
session + family + evidence identity), blockers by stage, arms, fills, primary missed reason. Reasons are typed
DATA / STRATEGY / SAFETY / EXECUTION / SYSTEM (code for machines, Vietnamese for people). Fills are read from the
order (`opened_at`), never from a candidate/READY flag. One summary per session (restart-safe).
`GET /api/v1/ny-quota/diagnostics?symbol&date=YYYY-MM-DD` is read-only; sessions without an audit return
`UNAVAILABLE` with null counts (not fake zeros).

## 3. Decisions / semantics

* Only an **actual PAPER fill** inside the NY window moves quota (`TradeLifecycleService`, unchanged). READY / ARMED /
  rejected / preview never count. Max 3/day and max 1 position unchanged.
* Pending fallback order when the user disables fallback mid-session: **kept armed** (it may still fill; no position is
  auto-closed). Disabling only stops *new* searching. Covered by a test.
* Standard setup has priority; an armed standard order blocks fallback (one order per cycle).
* Generator target rule (documented small change): most recent **confirmed** pivot on the correct side of entry; no
  `recent_high/low` fallback. Structure label is `FALLBACK_STRUCTURE` / family `NY_QUOTA_PAPER` (experimental); no
  `STRICT_SMC`, no fabricated sweep, `structure_chain_verified` is not set to pass guards.
* Tick/spam control: one scan ≠ one setup; `seen_count` increments once per scan; material revisions are traced.

## 4. Tests (this environment, branch working tree before commit)

| Suite | Result |
|---|---|
| Baseline `test_v7_trading_policy_quota.py` + `test_v14_2_entry_guard.py` | 100 passed |
| New `test_v14_3_ny_quota.py` + `test_v14_3_frequency_audit.py` | 117 passed (112 + 5) |
| Backend full (`backend/tests`, 2 pre-existing baseline failures deselected) | **895 passed**, 2 deselected, rc=0 |
| Frontend unit (vitest) | 159 passed (7 new) |
| Frontend `build` / `lint` | rc=0 / 0 errors (15 pre-existing warnings) |
| Browser (real Chrome, isolated DB, `v14_2` + `v14_3`) | 15 passed (9 + 6), run 3× stable |

Integrated tests run the real chain (NY cycle → generator → upsert → guarded arm → coordinator → lifecycle → quota) for LONG
and SHORT; only candles, the clock and the quote are fixtures. Negative variants (news, red lesson, stale quote, feed gap,
account limit) assert no fill, counter 0 and the exact reason while the funnel still shows the signal.

**Out-of-scope baseline failures (unchanged, deselected):** `test_v12_4_acceptance.py::test_v124_01_canonical_ledger_schema_and_export`,
`test_v13_5_integrity_acceptance.py::test_01_integrity_pass_on_valid_inputs`. The result above is *scoped* acceptance, not “global clean”.

## 5. Frequency audit (`backend/lab/ny_quota_frequency_audit.py`)

Isolated (fresh in-memory DB per day), runs the production cycle + coordinator with injected clock, as-of HTF table,
causal bars only. Outputs `daily_funnel.csv`, `sessions.csv`, `blockers.csv`, `summary.json`.
Functional proof: a multi-day synthetic fixture (partial day, missing day, flat day, setup day) yields `PARTIAL`/`MISSING`
with null counters, a measured zero for the flat day, and one real fill for the setup day.

**Real-data status: `NOT_VERIFIED`.** The repo holds XAUUSDT 15m research JSON and only ~3.5 days of 5M in the dev DB; there is
no ≥3-month XAUUSDT M5 + HTF-bias dataset. Running the CLI over 2026-07-01…2026-09-30 reports 92 requested / 0 covered days,
all counters `null`. No synthetic bars/trades were created; no claim about 3-month trade frequency is made.

## 6. Limitations (unresolved, not certified)

* SMC chain provenance / the hard-coded quality score of the standard engine are untouched; the engine is not certified.
* `entry_mutation_lock` is process-local (single backend process assumed).
* Audit execution assumptions: synthetic quote from M5 close ± assumed spread, no exit simulation, no PnL, no news/lessons
  replay (labelled `COUNTERFACTUAL_NO_NEWS_NO_LESSONS`); standard 15M flow not measured in the lab.
* Browser harness: `/__e2e/ny/reset` deletes rows while the page may poll, producing harmless 500s in the launcher log only.
* Forward live-paper observation has **not** been performed; no live DB/account was touched; nothing deployed.
