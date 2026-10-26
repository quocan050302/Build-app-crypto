# V14.4.1 Acceptance Report

Branch `fix/v14-1-rr-flow` · base `b444d76` · produced by `tools/run_v14_4_1_acceptance.py`
(structured argv, per-step logs under `logs/acceptance/v14_4_1/`, no hard-coded PASS).

**Overall runner verdict: `PASS_SCOPED`** — every executed step passed, except three pre-existing baseline backend
failures (listed below) which are reported as scoped, **not** as "all clean".

## 1. Executed checks

| Step | Result | Detail |
|---|---|---|
| Backend full (`pytest tests`) | **PASS_SCOPED** | 981 passed, 5 skipped, **3 failed (baseline)** in 350 s |
| Backend targeted (V14.4.1 + V14.3 NY quota/frequency) | PASS | 176 passed |
| Frontend `tsc -b` | PASS | |
| Frontend `oxlint` | PASS | 0 errors (23 warnings, not compared against the baseline) |
| Frontend `vitest run` | PASS | 22 files, 187 tests |
| Frontend `vite build` | PASS | chunk-size warning only |
| Browser e2e (real Chrome, isolated DB) | PASS | 9 / 9 (see §3) |
| Scenario-engine benchmark | PASS | real tape, 600 cycles: P50 ≈ 14.8 ms, P95 ≈ 18.9 ms (Windows, 12 CPU) |
| `git diff --check` | PASS | |

Baseline failures (they fail identically before V14.4.1; not caused by this work, not fixed here):
`test_v11_historical_replay.py::test_s01_and_s02_soak_burst_and_fault_injection` (needs the POSIX `resource` module),
`test_v12_4_acceptance.py::test_v124_01_canonical_ledger_schema_and_export`,
`test_v13_5_integrity_acceptance.py::test_01_integrity_pass_on_valid_inputs`.

## 2. Evidence status matrix

| Area | Status | Basis |
|---|---|---|
| **PASS_FUNCTIONAL** (real LONG and SHORT: detector → scenario → persist → watch → certified guard → ARMED → coordinator fill → quota; blocked chains; V14.3 fallback; red news / lessons / quote / feed / account blockers; exits unaffected) | **PASS** | `test_v14_4_1_production_pipeline.py`, `_entry_guards.py`, `_contracts.py`, `_scenario_fail_closed.py`, `_session_forecasts.py`, `_repository.py`; 117 V14.3 tests unchanged |
| **PASS_CHART** (real click selection, exact evidence IDs/prices on the canvas, real FVG bounds, SHORT R:R direction, no global leak, frozen order snapshot, read-only open order, delayed A→B, bounded history window + "Về hiện tại", layer menu keyboard, 1366/1440/390 layouts) | **PASS (scoped, see §3 limits)** | `frontend/tests/e2e/v14_4_1_sessions_chart.e2e.ts` |
| **PASS_PROBABILITY_RESEARCH** | **NOT_VERIFIED** | No real out-of-sample dataset exists in the repo. The probability layer (types, Wilson interval, expected-net-R, registry, chronological purged split, labels) is code-correct and unit-tested on synthetic fixtures only. All live probability fields are `null` / `UNAVAILABLE`. **Metrics are null, not 0.** |
| Profitability / three-month frequency | **NOT_VERIFIED** | Not measured. "SMC improvement implemented" is not a profit claim. Target stays ≤ 1 NY fill/day (max 3); a legitimate `NO_ENTRY` with a reason is a valid outcome. |

## 3. What the browser suite proves — and what it does not

Proves (assertions on the chart's own hit registry and R:R geometry through a DEV-only accessor, plus network contract):
scenario / setup / order click each load **their** bundle (exact snapshot / instance / revision binding), the drawn
items belong to that bundle only, the FVG price lies inside the detector's real top/bottom, SHORT reward is drawn below
entry, an order keeps its frozen snapshot and its actual Entry/SL/TP, a pointer drag on an open order sends no
mutation and moves nothing, a superseded response cannot overwrite the later selection, a closed order older than the
loaded window triggers a bounded `start_ms/end_ms` fetch and the viewport anchors there, ESC closes the layer popup
and restores focus.

Does **not** prove / known limits (honest list):
* Visual QA screenshots (`logs/v14_4_1/qa_*.png`) show the evidence is drawn and legible, but on the dense fixture the
  labels cluster at the right edge and the SL line sits close to the pane edge — readability was reviewed by eye only.
* Mobile 390×844: the layout test measures the chart pane (≥ 240 × 200). The Journal "Chart" button sits in a
  horizontally scrolling table, outside the viewport; the test activates it with `dispatchEvent`, so **real-pointer
  reachability of that button on a phone is not verified** (likely needs a horizontal scroll).
* Not covered by the browser suite: draft SL/TP pointer drag on a SHORT draft (read-only order drag only), 100×
  selection/pan/zoom soak, WS reconnect resync, `PointerCancel` restore. The corresponding code exists but is
  NOT_VERIFIED in a browser.
* Benchmark measures the pure backend engine only. Browser render latency, WS subscriber count, queue length and
  chart recreation under a 10,000-tick stream are **NOT_MEASURED**.
* The e2e fixture is synthetic (`smc_fixtures`); no real-market replay was run.
* `oxlint` warnings were not diffed against the pre-change baseline.

## 4. Implemented vs not implemented (against the 145-part spec)

Implemented and exercised: parts 005–021 (contracts, signatures, FVG/target/sweep/displacement/retest truth, fail-closed
context), 022–035 (session forecasts, as-of ranges, PDH/PDL, liquidity state), 036–046 (arbitration, production wiring,
transaction/commit-then-event, flags, certified guard with exact binding, fallback adapter, calculator source, frozen
refs, BEFORE_FILL recheck), 047–060 (probability layer as shadow/UNAVAILABLE, isolated lab CLIs), 061–077 and 090–104,
108–113, 115–119 (forecast card, probability badge, selection owner, exact endpoints, bundle validation, renderer rewrite,
focus + history window), 078–089 (tests, runners, docs).

Partially done / not done (so they are **not** claimed): `TradeDetailsDrawer` scenario accordion, `SMCScenarioCard`
height tuning beyond the existing 5-line summary, persisted layer preferences, `buildSelectionFocusRequest` anchors
(App passes a single `targetTime`), a dedicated `EvidenceList` for keyboard access to canvas evidence, R:R draft-drag
browser test, mobile pointer reachability, probability dataset/CLI on real data.

## 5. Reproducing

```
backend\venv\Scripts\python.exe tools\run_v14_4_1_acceptance.py          # full, ~7 min
backend\venv\Scripts\python.exe tools\run_v14_4_1_acceptance.py --skip-e2e   # records e2e as NOT_RUN (overall cannot PASS)
```
Browser tests use ports 18765/15199 by default (override `AURUM_E2E_API_PORT` / `AURUM_E2E_WEB_PORT`).
