# AURUM DESK V17.8 — FORENSIC STOP-OUT ROOT CAUSE AUDIT

**Audit Date:** 2026-10-28  
**Audit Scope:** Operational Database (`aurum-desk/backend/aurum_desk.db`) Read-Only Forensic Extraction & Exact Structural Replay  
**Primary Target Order:** `ord_SMC_M1_FVG_CONTINUATION_fvg_cont_SHORT_fvg-bear-1M-1793102220000_1793102400000`  
**Secondary Target Order:** `ord_SMC_M1_FVG_CONTINUATION_fvg_cont_SHORT_fvg-bear-1M-1793099640000_1793099760000`  
**Auditor:** Quantitative Strategy Engineer & Lead Trading Systems Architect  

---

## 1. EXECUTIVE SUMMARY & FORENSIC VERDICT

A rigorous forensic audit of the historical operational SQLite database (`aurum-desk/backend/aurum_desk.db`, 3.55 GB) was conducted in strict read-only mode (`mode=ro`). The exact trade records, domain event streams, paper observation snapshots, and 1-minute/5-minute candlestick data were retrieved and deterministically replayed.

### Primary Forensic Findings:
1. **Root Cause Diagnosis:** **`LEGACY_EDGE_STOP`** combined with **`ENTRY_TOO_CLOSE_TO_STOP`** and **`SPREAD_EXPOSURE`**.
2. **Execution Mode at ARM:** The system operated under `SHADOW_COMPARE` mode (`stop_policy_version=1`). Under this policy, executable orders always execute **Variant A (Legacy)** while computing Variant B and C in shadow observation.
3. **The 5-Tick Edge Trap:** Variant A placed the executable stop loss at `FVG Top + 5 ticks` ($4021.62 + 0.05 = \$4021.67$). With an actual market fill at $4021.08, the net distance to the stop was merely **$0.59 USDT** (59 ticks).
4. **Volatility Mismatch:** At the exact decision timestamp ($1793102462074$ ms / 19:01:02 VN time), the validated M1 ATR was **$0.9814 USDT** and M5 ATR was **$3.3836 USDT**. A stop distance of $0.59 was only **0.60× M1 ATR** and **0.17× M5 ATR**, leaving the position entirely unprotected from standard high-frequency quote noise and bid-ask spread fluctuation.
5. **Structural Alternative Disqualification:** Variant C (Structural) was disqualified (`MISSING_STRUCTURAL_ANCHOR`) because the legacy origin selector bound to an obsolete break from 18:42:00 whose origin ($4021.06) had already been breached by the subsequent move to $4022.23 (`ANCHOR_ALREADY_INVALIDATED`), failing to identify the true displacement origin of the current FVG episode.
6. **Secondary Confirmation:** Audit of a companion trade from the same session (`ord_...1793099640000`) confirmed the exact same phenomenon: stopped out in **22.18 seconds** on Variant A ($4020.99 SL vs $4020.06 Entry, 0.93 distance vs 1.44 M1 ATR), while Variant C's structural stop at $4022.30 was valid and would have comfortably survived.

---

## 2. PRIMARY TRADE INVESTIGATION: 19:01 ASIA/HO_CHI_MINH (OCTOBER 27, 2026)

### 2.1 Complete Immutable Record Verification

| Field | Database Record Value | Verification Status |
|---|---|---|
| **Order ID** | `ord_SMC_M1_FVG_CONTINUATION_fvg_cont_SHORT_fvg-bear-1M-1793102220000_1793102400000` | Exact Match |
| **Setup ID** | `watch-XAUUSDT-SMC_M1_FVG_CONTINUATION-fvg_cont_SHORT_fvg-bear-1M-1793102220000_1793102400000` | Verified |
| **Instrument & Direction** | `XAUUSDT` / `SHORT` | Verified |
| **Model** | `SMC_M1_FVG_CONTINUATION` | Verified |
| **Planned Entry** | `4021.18` | Verified |
| **Actual Entry** | `4021.08` | Verified |
| **Stop Loss (Frozen)** | `4021.67` | Verified |
| **Take Profit** | `4002.34` | Verified |
| **Actual Exit Price** | `4021.74` | Verified |
| **Exit Cause** | `SL_HIT` | Verified |
| **Realized Net PnL** | `-2.48 USDT` | Verified |
| **Realized R** | `-0.99 R` | Verified |
| **Authorized Quantity** | `0.64` (Risk: 2.50 USDT @ 20x leverage) | Verified |
| **Armed Timestamp** | `1793102462074` (2026-10-27 19:01:02.074 +07:00) | Verified |
| **Filled (Opened) Timestamp** | `1793102463329` (2026-10-27 19:01:03.329 +07:00) | Verified |
| **Closed Timestamp** | `1793102574926` (2026-10-27 19:02:54.926 +07:00) | Verified |
| **Total Holding Duration** | `111,597 ms` (~111.6 seconds) | Verified |

### 2.2 Candidacy & Candlestick Chronicle (1M XAUUSDT)

| Timestamp (VN Time) | Open | High | Low | Close | Volume | Event / Meaning |
|---|---|---|---|---|---|---|
| 18:56:00 | 4021.62 | 4022.14 | 4021.62 | 4022.01 | 8.30 | Pre-break accumulation |
| 18:57:00 | 4022.01 | **4022.23** | 4021.25 | 4021.38 | 24.17 | **Displacement Candle 1 (High: 4022.23 = Local Episode High)** |
| 18:58:00 | 4021.38 | 4021.46 | 4020.83 | 4020.85 | 32.66 | Displacement Candle 2 (Bearish body) |
| 18:59:00 | 4020.85 | 4021.18 | 4020.84 | 4021.18 | 18.14 | Candle 3: FVG formed between 4021.18 and 4021.25/4021.62 |
| 19:00:00 | 4021.18 | 4021.54 | 4019.99 | 4021.54 | 41.84 | FVG confirmed at candle close |
| 19:01:00 | 4021.54 | 4021.54 | 4020.73 | 4020.91 | 8.13 | **Retest into FVG; Order Armed (19:01:02) & Filled (19:01:03)** |
| 19:02:00 | 4020.91 | 4021.57 | 4020.45 | 4021.57 | 10.96 | In-trade; Ask tick touches 4021.74 @ 19:02:54.926 |
| 19:03:00 | 4021.57 | 4022.72 | 4021.57 | 4022.58 | 12.13 | Subsequent expansion |

---

## 3. PHASE 0 MANDATORY AUDIT QUESTIONS & DIRECT EVIDENCE

### Q1: Did the order use Variant A (Legacy) or Variant C (Structural)?
**Answer:** **Variant A (Legacy).**  
**Evidence:** `cost_snapshot.stop_mode_at_arm == "SHADOW_COMPARE"` and `cost_snapshot.applied_variant == "A"`. The orchestrator executes `Variant A` whenever in `SHADOW_COMPARE` mode.

### Q2: What stop placement policy was effective at ARM?
**Answer:**  
```json
{
  "stop_policy_version": 1,
  "mode": "SHADOW_COMPARE",
  "atr_period": 14,
  "atr_method": "SMA_TR_V1",
  "min_buffer_ticks": 5.0,
  "atr_m1_multiplier": 0.25,
  "atr_m5_multiplier": 0.1,
  "spread_multiplier": 1.5
}
```

### Q3: What exact FVG and structural anchor were selected?
**Answer:**  
- **FVG Selected:** `fvg-bear-1M-1793102220000_1793102400000` (Top: $4021.62, Bottom: $4021.18).
- **Structural Anchor Attempted:** Swing High pivot `1m_sh_1793099820000` with price **$4021.06** (formed at 18:17:00 VN time).
- **Final Anchor Stored:** `anchor: None` (rejected because it was already invalidated).

### Q4: Did the chosen FVG top plus the legacy buffer produce 4021.67?
**Answer:** **YES.**  
`sl_price = round(fvg_top + (cfg.sl_buffer_ticks * cfg.tick_size), 2)`  
`4021.62 + (5 * 0.01) = 4021.67`. Exactly 4021.67.

### Q5: Was a valid structural alternative available at decision time?
**Answer:** **YES.**  
The actual swing high / displacement origin of the setup was formed at 18:57:00 with High = **$4022.23** (confirmed swing high `1m_sh_1793102220000`).

### Q6: If available, why was it not selected?
**Answer:**  
The legacy origin binding mechanism (`select_protected_origin_for_break`) relied on an earlier confirmed break (`break_1793101320000` at 18:42:00) rather than isolating the immediate FVG displacement episode. It chose an old pivot from 18:17:00 ($4021.06). When `resolve_stop_anchor` checked if price had traded above $4021.06 between 18:17:00 and 19:01:02, it saw the rise to $4022.23 at 18:57:00, correctly returning `ANCHOR_ALREADY_INVALIDATED`. Because the anchor was marked invalid, Variant C had no anchor (`MISSING_STRUCTURAL_ANCHOR`), and the system executed Variant A.

### Q7: What were ATR M1, ATR M5, Bid, Ask, and spread at the decision?
**Answer:**  
- **ATR M1:** `0.9814`
- **ATR M5:** `3.3836`
- **Bid at ARM:** `4021.08`
- **Ask at ARM:** `4021.09`
- **Spread:** `0.01 USDT`

### Q8: How far was the executable Ask from SL at FILL?
**Answer:**  
- **Executable Ask at Fill:** `~4021.09`
- **Stop Loss:** `4021.67`
- **Distance:** **$0.58 USDT** (58 ticks).
- Relative to M1 ATR (0.9814), this distance was only **0.59× ATR M1**.

### Q9: What evidence triggered the actual exit?
**Answer:**  
Stored in `cost_snapshot.exit_evidence`:
```json
{
  "basis": "ASK",
  "evidence_reference": "tick_1793102574926",
  "trigger_level": 4021.67,
  "applied_exit_price": 4021.74,
  "quote_timestamp": 1793102574926,
  "is_candle_eval": false,
  "candle_high": null,
  "candle_low": null
}
```

### Q10: Was the trade stopped by tick Ask, candle high, gap pricing, or another mechanism?
**Answer:**  
**Stopped by real-time tick Ask.** An Ask quote of $4021.74 breached the $4021.67 trigger level at timestamp 1793102574926, filling with 0.07 slippage at $4021.74.

### Q11: Would an alternative structural SL have passed all financial guards?
**Answer:** **YES (Verified via Authoritative `domain_calculator.calculate_risk_reward`).**  
- **Case A — Historical Displacement Origin SL (`4022.57` = `4022.23 + 0.34` buffer):**
  - Distance from Entry (`4021.08`): `$1.49 USDT`.
  - Authoritative `calculate_risk_reward` (`$2.50` risk budget): `quantity = 0.71 oz`, `net_risk_usdt = 2.49 USDT`, `net_reward_usdt = 10.97 USDT`, `net_rr = 4.4139R`.
- **Case B — Wider Unicorn / Structural SL (`4024.80`):**
  - Distance from Entry (`4021.08`): `$3.72 USDT`.
  - Auto-sized for `$2.50` risk budget: `quantity = 0.35 oz`, `net_risk_usdt = 2.50 USDT`, `net_reward_usdt = 5.40 USDT` (`net_rr = 2.1617R` taker TP) or `5.68 USDT` (`net_rr = 2.2738R` maker TP).
  - Fixed `0.15 oz` quantity: `net_risk_usdt = 1.07 USDT`, `net_reward_usdt = 2.31 USDT` (`net_rr = 2.1617R` taker TP) or `2.43 USDT` (`net_rr = 2.2738R` maker TP).
- **Financial Invariants:** Risk budget conserved (`<= $2.50 USDT`), liquidation price well above `$4790`, margin within bounds (`can_execute = True`, `meets_min_rr = True`).
- *(See `docs/V17_8_0_1_FORENSIC_TRUTH_RECONCILIATION.md` for complete V17.8.0.1 calculator reconciliation).*

### Q12: Was this a legitimate structural invalidation or a potential premature stop-out?
**Answer:**  
**PREMATURE STOP-OUT.**  
At 19:02:54 (when the trade stopped at 4021.74), the structural high of the episode ($4022.23) was **never breached**. The 1-minute candle high for 19:02 was only 4021.57. A brief microstructure Ask tick spiked to 4021.74, taking out the fragile 5-tick buffer ($4021.67) while the structural thesis was still intact.

---

## 4. SECONDARY STOP-OUT AUDIT

Querying recent `SL_HIT` occurrences across `paper_orders` revealed another identical failure mode:

### Order ID: `ord_SMC_M1_FVG_CONTINUATION_fvg_cont_SHORT_fvg-bear-1M-1793099640000_1793099760000`
- **Opened At:** `1793099821810` (18:17:01 VN time)
- **Closed At:** `1793099843989` (18:17:23 VN time)
- **Holding Time:** **22.18 seconds!**
- **Mode:** `SHADOW_COMPARE` (Executed Variant A)
- **Planned Entry:** $4020.16 | **Actual Entry:** $4020.06
- **Variant A SL (Executed):** **$4020.99** (FVG Edge + 5 ticks) -> Stop distance: **$0.93 USDT**
- **Actual Exit:** $4021.07 (Ask tick breach)
- **ATR at Decision:** M1 ATR = **$1.4371**, M5 ATR = **$4.7429**
- **Variant C SL (Computed in Shadow):**
  - Anchor: `1m_sh_1793099400000` (Price = $4021.82)
  - Buffer: $0.4743 (ATR M5)
  - **Variant C SL:** **$4022.30**
  - **Variant C Status:** `is_valid: True`, `can_execute: True`, `meets_min_rr: True` (Net R:R = 2.61R, Qty = 0.45)
- **Analysis:** Had Variant C been executed, the trade would **not** have been stopped out at 4021.07 after 22 seconds, as its SL ($4022.30) was well beyond the noise.

---

## 5. ROOT CAUSE TAXONOMY & ARCHITECTURAL GAPS

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      HISTORICAL ROOT CAUSE ANATOMY                      │
├──────────────────────────────────┬──────────────────────────────────────┤
│ Phenomenon                       │ Classification & Systemic Vulnerability│
├──────────────────────────────────┼──────────────────────────────────────┤
│ FVG Top + 5 ticks ($0.05)        │ LEGACY_EDGE_STOP                     │
│ SL distance 0.59 vs ATR 0.98     │ ENTRY_TOO_CLOSE_TO_STOP              │
│ Ask tick slip through 5 ticks    │ SPREAD_EXPOSURE                      │
│ Bind to 45m old breached pivot   │ EPISODE_BINDING_MISMATCH             │
│ Shadow mode forced Variant A     │ DEPLOYMENT_MODE_LIMITATION           │
└──────────────────────────────────┴──────────────────────────────────────┘
```

### Necessary V17.8 Architectural Pillars:
1. **ICT Unicorn Evidence Engine (Phase 1):** Detect true Breaker Block + FVG overlap with deterministic causal binding to the same setup episode.
2. **Unified Structural Stop Loss Intelligence (Phase 2):** Prioritize thesis-bound structural invalidation (Breaker distal boundary or local displacement origin) rather than FVG edge; enforce minimum entry-relative noise protection.
3. **Take Profit Independence (Phase 3):** Anchor TP strictly to opposing liquidity (EQL/EQH/Session Extremes), never adjusting TP to artificially fix poor SL geometry.
4. **Market Noise & Quote Protection Guard (Phase 4):** Strictly reject ARM or FILL if `executable_ask - selected_sl` (or bid for LONG) is within the minimum protective threshold (e.g., `< max(0.5×ATR, 1.5×Spread)`).
5. **Exact Financial Resizing (Phase 6):** Dynamically downsize quantities on structural stops to strictly preserve the authorized risk budget ($2.50 USDT).

*Report Certified by Release Auditor against Operational Database Artifacts.*
