# V14.1 R:R FLOW & ATOMIC PLAN IMPLEMENTATION MAP

## 1. Overview
- **Repository**: `quocan050302/Build-app-crypto`
- **Base Commit**: `e41b52ca51d1f35d1f99a30ef94d3bf49a91e3be` (master, PR #1 merged V14)
- **Branch**: `fix/v14-1-rr-flow`
- **Scope**: Repair LONG/SHORT R:R calculation, viewport autoscale, atomic price resolution, signed net ratio formatting, and draft/selection isolation.
- **Operating Mode**: `EXPLAIN_ONLY` (retains core execution, sizing, risk guards, and NY session policies).

---

## 2. Symbol & File Mapping

| File Path | Symbol / Function | Status | Role | Inputs | Outputs | Callers | Tests |
|---|---|---|---|---|---|---|---|
| `frontend/src/utils/planLevels.ts` | `resolveSetupPriceLevels` | **NEW** | Atomic price resolver mirroring backend `plan_resolver` | `SetupPriceLevels` | `PlanResolution` (`isValid: true, plan: AtomicPlan` or `isValid: false, reason: string`) | `App.handleFocusSetupOnChart`, `App.handleCopySetupToDraft` | `v14_1_rr_flow.test.ts::test_atomic_resolution` |
| `frontend/src/utils/planLevels.ts` | `formatRR` | **NEW** | Signed R:R string formatter | `number \| null \| undefined` | String (e.g. `1:0.36`, `1:-0.19`, `—`) | `RiskRewardPrimitive`, `QuickDecisionSidebar` | `v14_1_rr_flow.test.ts::test_signed_formatter` |
| `frontend/src/plugins/RiskRewardPrimitive.ts` | `RiskRewardPrimitive.autoscaleInfo` | **NEW** | Lightweight Charts v5.2 autoscale hook | None (reads `this._data`) | `AutoscaleInfo \| null` | Lightweight Charts series price scale engine | `v14_1_rr_flow.test.ts::test_autoscale` |
| `frontend/src/plugins/RiskRewardPrimitive.ts` | `RiskRewardPrimitive.hitTest` | **MODIFIED** | Read-only interaction guard | `x: number, y: number` | `DragHitResult \| null` (returns `null` when `readOnly` or locked state) | Pointer drag event handlers | `v14_1_rr_flow.test.ts::test_readonly_hittest` |
| `frontend/src/plugins/RiskRewardPrimitive.ts` | `RiskRewardPaneRenderer.draw` | **MODIFIED** | Canvas renderer for R:R bands & central badge | Canvas target | 2D canvas drawing (amber warning for non-positive net reward) | Lightweight Charts pane view update | `rr-chart-harness.html`, Vitest |
| `frontend/src/App.tsx` | `handleFocusSetupOnChart` | **MODIFIED** | Atomic resolution before view switch | `setup: any` | Viewport & overlay update with candidate state | Upcoming cards, Sidebar | Integration / UI |
| `frontend/src/App.tsx` | `handleCopySetupToDraft` | **MODIFIED** | Draft creation with atomic resolution & request invalidation | `setup: any` | New draft overlay & selection | Upcoming card action button | Integration / UI |
| `frontend/src/App.tsx` | `handleFocusTradeOnChart` | **MODIFIED** | Locked trade view with read-only overlay | `trade: any` | Read-only overlay (`paper_open` / `closed`) | Trade history / active position | Integration / UI |
| `frontend/src/App.tsx` | `onOverlayChange` | **MODIFIED** | Pointer drag handler switching to `source: 'DRAFT'` | `updated: RiskRewardData` | Updated draft intent with cancelable preview | Chart pointer drag | Integration / UI |
| `frontend/src/ChartComponent.tsx` | `ChartComponent` | **MODIFIED** | Price scale autoScale trigger & draft isolation | Props (`activeOverlay`, `focusRequest`) | Chart canvas rendering | `App.tsx` | Vitest / Canvas harness |
| `frontend/src/components/QuickDecisionSidebar.tsx` | `QuickDecisionSidebar` | **MODIFIED** | R:R formatting with nullish coalescing `??` | Props | 4-line compact decision card showing signed net R:R | `App.tsx` | Integration / UI |
| `backend/schemas.py` | `DraftQualityPreviewRequest` | **MODIFIED** | Schema constraints & entry alias validation | Request payload | Validated Pydantic model (`direction: Literal['LONG', 'SHORT']`) | `POST /api/v1/quality/preview` | `test_v14_1_rr_preview.py` |
| `backend/main.py` | `preview_setup_quality` | **MODIFIED** | Preview calculator using requested quantity & latest balance | `DraftQualityPreviewRequest` | Preview JSON with full calc model dump | Frontend `api.previewSetupQuality` | `test_v14_1_rr_preview.py` |

---

## 3. Data & Interaction Flow Diagram

```
                 [User clicks setup in Upcoming / Chart]
                                    │
                                    ▼
                     [resolveSetupPriceLevels(setup)]
                                    │
                  ┌─────────────────┴─────────────────┐
                  ▼                                   ▼
        [isValid === false]                  [isValid === true]
                  │                                   │
         (Show warning toast)           (AtomicPlan: entry, SL, TP)
                                                      │
                       ┌──────────────────────────────┴──────────────────────────────┐
                       ▼                                                             ▼
             [App.selectedIntent]                                           [App.activeOverlay]
      (candidate / armed / readOnly)                               (state: 'candidate', signed net RR)
                       │                                                             │
                       ▼                                                             ▼
            [QuickDecisionSidebar]                                          [ChartComponent]
       - Uses `??` (preserves 0 & -0.19)                                             │
       - Formats 1:x.xx or '—'                                                       ▼
       - Warns if net reward <= 0                                        [RiskRewardPrimitive]
                                                                         - autoscaleInfo (SL visible)
                                                                         - Amber box if net reward <= 0
                                                                         - hitTest guarded if readOnly
                                                                                     │
                                                                   [User drags candidate / draft]
                                                                                     │
                                                                                     ▼
                                                                           [App.onOverlayChange]
                                                                         - Switches source: 'DRAFT'
                                                                         - Clears server SMC snapshot
                                                                         - Calls cancelable preview
                                                                                     │
                                                                                     ▼
                                                                        [POST /api/v1/quality/preview]
                                                                         - Uses requested quantity (0.29)
                                                                         - Uses latest DayAudit balance
                                                                         - Read-only; no DB mutations
```

---

## 4. Screenshot Fixture Accounting Verification

For the reference SHORT setup:
- Entry: `4199.79`
- Stop Loss: `4204.79` (Stop distance = `5.00`)
- Take Profit: `4198.00` (Target distance = `1.79`)
- Quantity: `0.29`
- Display Capital: `999.57`
- Leverage: `30`
- Margin Mode: `ISOLATED`

### Calculations:
1. **Gross Loss** = `0.29 * 5.00 * 1.0` = **`1.45 USDT`**
2. **Gross Reward** = `0.29 * 1.79 * 1.0` = **`0.52 USDT`**
3. **Gross R:R** = `0.52 / 1.45` = `1.79 / 5.00` = **`1:0.358`**
4. **Fees & Slippage**:
   - Entry taker fee + slippage: `~0.50 USDT`
   - Exit taker fee + slippage: `~0.50 USDT`
5. **Net Risk** = Gross Loss + Costs = **`2.48 USDT`**
6. **Net Reward** = Gross Reward - Costs = `0.52 - 1.00` = **`-0.48 USDT`**
7. **Net R:R** = `-0.48 / 2.48` = **`1:-0.19`**
8. **Outcome**:
   - Geometry is valid (`TP < entry < SL`).
   - `can_execute` is **`False`** (net reward is negative and net R:R < 2.0).
   - Display shows amber target box warning ("TP không bù chi phí") and sidebar displays signed Net `1:-0.19` instead of hiding as `--`.
