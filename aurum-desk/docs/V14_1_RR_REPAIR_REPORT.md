# V14.1 R:R repair from master — local review patch

Base: `e41b52ca51d1f35d1f99a30ef94d3bf49a91e3be` (master, merged V14).
Working branch: `fix/v14-1-rr-flow`.
No commit/push or deployment has been performed by the patch author.

The screenshot SHORT has valid direction geometry: entry 4199.79, SL 4204.79,
TP 4198.00. Its price risk is 5 and price reward is 1.79, so gross R:R is
0.358. At quantity 0.29, the existing fee/slippage model produces gross loss
1.45, gross reward 0.52, net risk 2.48 and net reward -0.48. The target must
remain unchanged; this is an unqualified provisional plan, not a reason to
inflate TP or reverse direction. The screenshot also clips SL above the
candle price range and hides negative net R:R in the sidebar.

## Changes

- `resolveSetupPriceLevels` mirrors the existing backend atomic price resolver:
  no confirmed/provisional hybrids; invalid armed plans remain invalid.
- Focus/copy handlers resolve prices before switching view and invalidate old
  quality requests. Watching setups display as candidates rather than user drafts.
- A local drawing draft only overrides the overlay for which drawing began.
  Selecting another server setup exits that drawing mode.
- R:R primitive contributes finite entry/SL/TP prices to autoscale, excludes
  liquidation, retains actual price-band geometry and marks nonpositive net
  target reward amber. Negative net R:R is preserved in sidebar and chart.
- Position overlays are read-only before hit testing or React overlay mutation.
  Draft/candidate handles remain available for local editing.
- Drag copies canonical monetary fields and discards old SMC quality evidence.
  Preview request cancellation prevents a previous plan's response being reused.
- Preview calculates with requested quantity, the latest account balance and
  saved settings for omitted inputs; returns the complete calculator payload.
  Request validation rejects invalid sizing/direction and nonfinite prices.
- Existing V14 test fixture was corrected to the production wire type. The
  original master production build failed on its entry/summary fields.

## Verification

Baseline: 16 frontend suites / 123 tests passed; production build failed TS2353.
Repair: 17 frontend suites / 130 tests passed; production build passed.
Targeted backend suite: 34 tests passed (one dependency deprecation warning).
New tests cover screenshot money/ratio, signed values, atomic resolution,
finite autoscale, SHORT coordinate orientation, locked position hit testing,
preview quantity and unchanged fixture order/snapshot/audit counts.

Commands, from `aurum-desk`:

```sh
cd frontend
npm test
npm run build
```

From `aurum-desk`, with backend dependencies installed in an isolated venv:

```sh
PYTHONPATH=backend python -m pytest backend/tests/test_v14_1_rr_preview.py backend/tests/test_v10_4_cost_and_rr.py backend/tests/test_v13_5_price_plan_acceptance.py backend/tests/test_setup_quality_api_v14.py backend/tests/test_risk_and_broker.py -q
```

`git diff --check` also passed. Build retains the preexisting large-bundle warning.

## Required remaining verification

Browser gate: **NOT_VERIFIED**. Installing Playwright Chromium failed because
the downloaded ZIP was truncated/invalid. `frontend/tests/rr-chart-harness.html`
is prepared for real canvas validation. Unit coordinate tests are not browser
pixel proof. Complete App selection/draft/drag/viewport tests before claiming
full UI acceptance. The harness is test-only and is not wired into production.

The endpoint no-write test stubs its analysis provider. Audit the real analysis
cache path separately before claiming the whole preview pipeline never writes.

The broader V14 causal assessment, matched SMC chain, FVG/source provenance,
snapshot revision checks, quality repository transaction isolation and lifecycle
snapshot linking remain separate unresolved work. This R:R patch does not
certify those items or show increased winrate. The historical replay remains
paused and no live positions, database or Telegram credentials were modified.
