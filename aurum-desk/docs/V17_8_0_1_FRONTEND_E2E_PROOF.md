# AURUM DESK V17.8.0.1 — FRONTEND MOUNTED COMPONENT & PLAYWRIGHT E2E PROOF (`DEF-178-12`)

**Defect ID:** `DEF-178-12`  
**Severity:** P1 (UI Integration & Operator Observability Defect)  
**Affected Files:**  
- `frontend/src/components/SLTPIntelligenceView.tsx`
- `frontend/src/JournalTab.tsx`
- `frontend/src/__tests__/v17_8_0_1_sltp_intelligence.test.tsx`
- `frontend/tests/e2e/v17_8_0_1_sltp_intelligence.e2e.ts`

---

## 1. Defect Description

In V17.8.0, `frontend/src/components/SLTPIntelligenceView.tsx` was created as a standalone file but was never imported or rendered inside any production screen (`App.tsx`, `JournalTab.tsx`, etc.). Furthermore, there were no Vitest component tests or Playwright E2E browser tests verifying that an operator could inspect V17.8 stop-loss provenance in the running application.

---

## 2. Production UI Integration (`JournalTab.tsx` & `SLTPIntelligenceView.tsx`)

1. **Mounted in Journal Trade Review Drawer (`frontend/src/JournalTab.tsx`):**
   - Imported `SLTPIntelligenceView` into `JournalTab.tsx` and mounted `<SLTPIntelligenceView orderId={selectedTrade.id} />` directly inside the Trade Detail & Review Drawer between `PostTradeDiagnosticSection` and `TradeChartSection`.
2. **Honest Field Rendering (`frontend/src/components/SLTPIntelligenceView.tsx`):**
   - Renders `stop_mode_at_arm` (`sltp-mode-badge`), `applied_variant` (`sltp-applied-variant-badge`), `direction`, `actual_entry` (`sltp-entry-price`), `executed_sl` (`sltp-executed-sl`), `take_profit` (`sltp-take-profit`), and `min_protective_distance` (`sltp-protective-distance`).
   - Renders Financial Authorization summary (`quantity`, `initial_risk_usdt`, `estimated_net_rr`).
   - Renders Side-by-Side Comparison (`sltp-variant-a-sl`, `sltp-variant-b-sl`, `sltp-variant-c-sl`) clearly distinguishing `[Đã thực thi]` (Executed) vs `[Ứng viên nghiên cứu]` (Shadow/Research Candidate).
   - Renders ICT Unicorn & Causal Provenance (`breaker_id`, `selected_fvg_id`, `invalidation_price`, `overlap_low`/`overlap_high`, `setup_episode_id`, `validity_status`).
   - Extracts nested volatility values (`atr_m1.value`, `atr_m5.value`, `spread_snapshot.spread`) without crashing.
   - Displays `UNAVAILABLE_LEGACY` and `NOT_RECORDED` for historical orders without fabricating `stop_mode_at_arm` or `applied_variant`.

---

## 3. Verification Evidence

### 3.1 Vitest Component Suite (`frontend/src/__tests__/v17_8_0_1_sltp_intelligence.test.tsx`)
- `renders full V17.8.0.1 Unicorn structural stop intelligence when PAPER_STRUCTURAL + Variant C is executed`: **PASS**
- `distinguishes executed Legacy SL (Variant A) from research Unicorn candidate SL in SHADOW_COMPARE mode`: **PASS**
- `renders UNAVAILABLE_LEGACY and NOT_RECORDED for historical orders without fabricating fields`: **PASS**
- `fetches stop evidence by orderId when evidenceData prop is not provided`: **PASS**

### 3.2 Real Chromium Playwright E2E Suite (`frontend/tests/e2e/v17_8_0_1_sltp_intelligence.e2e.ts` — Gate `G50`)
- `P01: Renders full V17.8.0.1 Unicorn structural stop intelligence in Journal Drawer`: **PASS**
- `P02: Distinguishes executed Legacy SL (A) from research Unicorn candidate SL in SHADOW_COMPARE mode`: **PASS**
- `P03: Displays UNAVAILABLE_LEGACY and NOT_RECORDED for historical order without fabricating mode or variant`: **PASS**

