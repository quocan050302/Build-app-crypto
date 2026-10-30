# AURUM DESK V17.8.0.1 — FORENSIC TRUTH RECONCILIATION (`DEF-178-14`)

**Defect ID:** `DEF-178-14`  
**Severity:** P1 (Forensic Documentation & Quantitative Accuracy Defect)  
**Target Historical Trade:** `ord_SMC_M1_FVG_CONTINUATION_fvg_cont_SHORT_fvg-bear-1M-1793102220000_1793102400000`  
**Verification Test:** `backend/tests/test_v17_8_runtime_integration.py::test_def_178_14_forensic_audit_truth_reconciliation` (Gate `G51`)

---

## 1. Purpose of This Reconciliation

Independent audit (`DEF-178-14`) identified discrepancies between the primary forensic audit (`docs/V17_8_STOP_OUT_FORENSIC_AUDIT.md`) and summary paragraphs in `docs/V17_8_FINAL_RELEASE_REPORT.md` and `docs/V17_8_REPLAY_RESEARCH_PROTOCOL.md`. Specifically, summary documents contained unverified hand-calculated estimates ("~9 seconds hold time", "+5.60R", "0.15 oz -> $2.48 risk") that conflicted with the immutable SQLite database record and the authoritative domain calculator (`domain_calculator.calculate_risk_reward`).

This document establishes the single authoritative, machine-verified record for `ord_SMC_M1_FVG_CONTINUATION_fvg_cont_SHORT_fvg-bear-1M-1793102220000_1793102400000`.

---

## 2. Immutable Database Fact Table (`paper_orders` & `cost_snapshot`)

| Field | Authoritative Persisted Value | Source |
|---|---|---|
| `id` | `ord_SMC_M1_FVG_CONTINUATION_fvg_cont_SHORT_fvg-bear-1M-1793102220000_1793102400000` | `paper_orders.id` |
| `setup_id` | `watch-XAUUSDT-SMC_M1_FVG_CONTINUATION-fvg_cont_SHORT_fvg-bear-1M-1793102220000_1793102400000` | `paper_orders.setup_id` |
| `direction` / `instrument` | `SHORT` / `XAUUSDT` | `paper_orders` |
| `planned_entry` | `4021.18` | `paper_orders.planned_entry` |
| `actual_entry` | `4021.08` | `paper_orders.actual_entry` |
| `stop_loss` (Executed Variant A) | `4021.67` (`FVG Top 4021.62 + 5 ticks`) | `paper_orders.stop_loss` |
| `take_profit` | `4002.34` | `paper_orders.take_profit` |
| `actual_exit` | `4021.74` (`SL_HIT` via Ask tick `tick_1793102574926`) | `paper_orders.actual_exit` |
| `quantity` (Executed Variant A) | `0.64 oz` | `paper_orders.quantity` |
| `realized_pnl_net` | `-2.48 USDT` (`-0.99 R`) | `paper_orders.realized_pnl_net` |
| `armed_at` | `1793102462074` (`2026-10-27 19:01:02.074 +07:00`) | `paper_orders.armed_at` |
| `opened_at` (`filled_at_ms`) | `1793102463329` (`2026-10-27 19:01:03.329 +07:00`) | `paper_orders.opened_at` |
| `closed_at` (`closed_at_ms`) | `1793102574926` (`2026-10-27 19:02:54.926 +07:00`) | `paper_orders.closed_at` |
| **Exact Hold Duration** | **`111,597 ms` (`111.597 seconds`)** | `closed_at - opened_at` |
| `atr_m1` / `atr_m5` at ARM | `0.9814` / `3.3836` | `cost_snapshot.stop_placement.volatility_context` |
| `bid` / `ask` / `spread` at ARM | `4021.08` / `4021.09` / `0.01 USDT` | `cost_snapshot.stop_placement.volatility_context` |

---

## 3. Reconciliation of Prior Contradictions

| Topic | Prior Inaccurate Claim (in V17.8.0 Summary Docs) | Authoritative Machine-Verified Truth (V17.8.0.1) | Root Cause of Prior Discrepancy |
|---|---|---|---|
| **Hold Duration** | `"~9 seconds"` (in `V17_8_FINAL_RELEASE_REPORT.md`) | **`111,597 ms` (`111.597 seconds`)** (`1793102574926 - 1793102463329`) | Narrative typo in summary report; `V17_8_STOP_OUT_FORENSIC_AUDIT.md` Section 2.1 had the exact `111,597 ms` figure. |
| **Spread at ARM** | `"0.20 USDT"` (in `V17_8_FINAL_RELEASE_REPORT.md`) | **`0.01 USDT`** (`Bid=4021.08, Ask=4021.09` at ARM; exit slippage was `0.07 USDT` at `4021.74`) | Confused synthetic replay stress-test spread (`0.20`) with the actual ARM snapshot spread (`0.01`). |
| **Hypothetical Structural SL (`4024.80`) Risk-Budget Auto-Sizing (`$2.50` budget)** | `"Quantity downscales to 0.15 oz ($2.50 risk budget conserved)... +5.60R"` | **`quantity = 0.35 oz`**, **`net_risk_usdt = 2.50 USDT`**, **`net_reward_usdt = 5.40 USDT` (`net_rr = 2.1617R` taker TP)** or **`5.68 USDT` (`net_rr = 2.2738R` maker TP)** | `calculate_risk_reward(direction="SHORT", planned_entry=4021.08, stop_loss=4024.80, take_profit=4002.34, capital=1000.0, risk_pct=0.25)` sizes `0.35 oz` for a `$3.72` stop distance (`$7.1315/oz` total unit risk including Bitget taker fees + slippage). |
| **Hypothetical Structural SL (`4024.80`) at Fixed `0.15 oz`** | `"0.15 oz -> $2.48 net risk, +5.60R"` | **`quantity = 0.15 oz`**, **`net_risk_usdt = 1.07 USDT`**, **`net_reward_usdt = 2.31 USDT` (`net_rr = 2.1617R` taker TP)** or **`2.43 USDT` (`net_rr = 2.2738R` maker TP)** | At `0.15 oz` and `$3.72` stop distance (`4024.80 - 4021.08`), gross loss is `$0.558` and net risk after fees/slippage is `$1.07 USDT`, not `$2.48 USDT`. Furthermore, `+5.68 USDT` is the *dollar net reward* at `0.35 oz` (maker TP), which was mislabeled as `+5.60R` (`gross_rr` is `5.0376R`, `net_rr` is `2.1617R` / `2.2738R`). |
| **Actual Historical Displacement Origin SL (`4022.57`)** | `Anchor = 4022.23` (`1m_sh_1793102220000`), `Buffer = 0.34`, `SL = 4022.57` | **`quantity = 0.71 oz`**, **`net_risk_usdt = 2.49 USDT`**, **`net_reward_usdt = 10.97 USDT`**, **`net_rr = 4.4139R`** | Verified directly via `calculate_risk_reward(direction="SHORT", planned_entry=4021.08, stop_loss=4022.57, take_profit=4002.34, capital=1000.0, risk_pct=0.25)`. |

