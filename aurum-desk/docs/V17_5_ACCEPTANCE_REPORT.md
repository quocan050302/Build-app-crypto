# AURUM V17.5 — ACCEPTANCE & VERIFICATION REPORT

**Feature:** AURUM V17.5 — Structural SL, Execution Evidence & Paired Paper Research  
**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `2c67c28dc9dfd2c869477fc7082a05a4671ba043`  
**Status:** `IMPLEMENTATION_VERIFIED`  
**Empirical Edge Status:** `EMPIRICAL_UNPROVEN` (Forward paper research required; zero false winrate claims)  

---

## 1. Executive Summary & Objective

In prior versions, scalp models (M1 Continuation, M1 FVG Continuation, M1 Reversal, M1 Sweep Reclaim, CG01) placed stop losses based on tight candle edges or fixed tick offsets (e.g. 5 ticks = 0.05 on XAUUSDT). In high-volatility regimes, normal market breathing easily triggered these tight stops without invalidating the underlying trade thesis.

AURUM V17.5 resolves this by:
1. **Model-Specific Invalidation Anchors:**
   - **Continuation:** Origin swing high (SHORT) or swing low (LONG) that generated the break leg.
   - **FVG Continuation:** Origin extreme of the leg producing the FVG/break, rather than hugging the FVG edge.
   - **Sweep Reclaim / Reversal / CG01:** Actual swept extreme of the confirmed episode.
2. **Causal Volatility & Spread Buffer:**
   - Buffer = $\max(\text{tick\_floor}, k_{\text{M1}} \cdot \text{ATR}_{\text{M1}}, k_{\text{M5}} \cdot \text{ATR}_{\text{M5}}, k_{\text{spread}} \cdot \text{spread})$.
   - Outward quantization using Decimal arithmetic to preserve instrument tick grids without inward drift.
3. **Target Invariance:**
   - Baseline TP is calculated from legacy rules and kept **strictly frozen**. Widening SL does not expand TP.
4. **Three Rollout Modes:**
   - `LEGACY`: Uses original stop geometry (Variant A).
   - `SHADOW_COMPARE` (Default): Executes legacy Variant A while recording paired research plans (A, B, C) in `PaperObservation`.
   - `PAPER_STRUCTURAL`: Applies Variant C (structural anchor + dynamic buffer) for **new paper orders** when valid.
5. **Decoupled Lesson Assessment:**
   - Plan compliance does not automatically validate stop quality. When stop evidence is absent, lessons factually reflect unproven stop quality rather than asserting valid structure.

---

## 2. Test Matrix & Results

| Test Category | Suite File | Total Tests | Status |
|---|---|---|---|
| Pure Calculation & ATR | `backend/tests/test_v17_5_stop_placement.py` | 6 | **PASS** |
| Anchor Binding & Leg Linkage | `backend/tests/test_v17_5_anchor_binding.py` | 5 | **PASS** |
| Risk & Sizing Invariants | `backend/tests/test_v17_5_risk_invariants.py` | 3 | **PASS** |
| Policy API & CAS Fencing | `backend/tests/test_v17_5_policy_api.py` | 5 | **PASS** |
| Exit Evidence & Side Parity | `backend/tests/test_v17_5_exit_evidence.py` | 3 | **PASS** |
| Model Integration & Routing | `backend/tests/test_v17_5_model_integration.py` | 2 | **PASS** |
| End-to-End Production Flow | `backend/tests/test_v17_5_production_flow.py` | 2 | **PASS** |
| Post-Trade Review & Lessons | `backend/tests/test_v17_5_stop_review.py` | 2 | **PASS** |
| Research Service & Excursions | `backend/tests/test_v17_5_stop_research.py` | 3 | **PASS** |
| **Total V17.5 Tests** | - | **31** | **ALL PASS** |
| **Frontend TypeScript & Vite Build** | `tsc -b && vite build` | - | **PASS (0 errors)** |

---

## 3. Screenshot Geometry Verification (Parts 007, 177)

The 4 SHORT orders observed on the user's UI were tested under `tests/test_v17_5_risk_invariants.py::test_screenshot_geometry_fixtures`:
- Order 1: 3989.92 / 3998.94 / 3967.72 -> SL dist 9.02, TP dist 22.20
- Order 2: 4006.04 / 4007.22 / 3995.29 -> SL dist 1.18, TP dist 10.75
- Order 3: 4036.82 / 4038.07 / 4020.71 -> SL dist 1.25, TP dist 16.11
- Order 4: 4030.65 / 4031.00 / 4020.71 -> SL dist 0.35, TP dist 9.94

*Note:* These fixtures confirm mathematical and gross R:R fidelity. In accordance with system instructions, no retroactive candle paths were fabricated and no claims are made regarding whether wider stops would have won these past trades.

---

## 4. Operator Guide & Cutover Procedure

1. **Default State:**
   - The system boots into `SHADOW_COMPARE`. Live paper trading continues with legacy geometry while shadow observations are accumulated.
2. **Switching to Structural Stop Loss:**
   - Navigate to the **Cấu hình (Settings)** tab in the UI.
   - Locate the **Chế độ Điểm Dừng Lỗ (Stop Loss Placement)** card.
   - Select `PAPER_STRUCTURAL (Áp dụng SL Cấu Trúc + Buffer Cho Lệnh Mới)`.
   - Click **Lưu Cấu Hình (Save)**.
3. **Safety Guarantees:**
   - Switching modes affects **only new paper orders**.
   - Open positions, armed limit orders, and historical orders are completely untouched.
   - Live trading (`LIVE` scope) is strictly rejected by the server execution scope guard.
