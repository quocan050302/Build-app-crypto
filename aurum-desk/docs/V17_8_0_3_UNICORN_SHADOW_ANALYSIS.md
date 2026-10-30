# AURUM DESK V17.8.0.3 — CONTINUOUS UNICORN SHADOW ANALYSIS & LIFECYCLE

**Release:** `V17.8.0.3`  
**Module:** `backend/services/market_shadow_service.py`

---

## 1. Closed-Bar Causal Evaluation Rules

`MarketShadowService.evaluate_shadow_cycle(as_of_ms=...)` evaluates all 5 production strategy models (`SMC_M1_CONTINUATION`, `SMC_M1_REVERSAL`, `SMC_M1_SWEEP_RECLAIM`, `SMC_M1_FVG_CONTINUATION`, `CG01_SWEEP_MSS_FVG`) under strict causal guards:

1. **Closed-Bar Filtering**:
   - M1, M5, and M15 candles are filtered by `is_closed is True` and `close_time <= decision_cutoff_ms`.
   - Provisional (`is_closed = False`) M5/M15 candles are explicitly excluded from HTF structure and Unicorn confluence (`unclosed_m5_rejected` telemetry flag).
   - Any candle with `close_time > decision_cutoff_ms` is rejected (`FUTURE_CANDLE_LOOKAHEAD_REJECTED`).
2. **Executable Quote Freshness Guard**:
   - Requires a valid, fresh `CanonicalQuote` (`age <= 15s`, `bid > 0`, `ask > 0`, `bid <= ask`).
   - LONG setups use `entry_reference = quote.ask` and check stop distance against executable `quote.bid`.
   - SHORT setups use `entry_reference = quote.bid` and check stop distance against executable `quote.ask`.
3. **ICT Unicorn Confluence (`evaluate_unicorn_confluence`)**:
   - Evaluates failed Order Block -> displacement structure break (`BOS`/`MSS`) -> Breaker Block -> geometrically overlapping Fair Value Gap (`FVG`) (`overlap_low < overlap_high`) -> causal retest without distal boundary breach.
4. **Structural SL/TP & Authoritative `$2.50` Risk Sizing**:
   - Computes Legacy SL (Variant A), V17.7 Structural SL (Variant B), V17.8 Unicorn/Structural SL with dynamic volatility buffer (Variant C), and Noise-Multiplier Research SL (Variant D).
   - Evaluates structural liquidity TP (`select_structural_target`) independently of SL width.
   - Calls `domain_calculator.calculate_trade_plan` with `risk_usd = 2.50` to verify post-fee net R:R (`>= 1.5`) and risk conservation (`net_risk <= $2.50`).

---

## 2. Shadow Opportunity State Machine & Hypothetical Monitoring

Every shadow opportunity carries:
- `execution_status = "NEVER_EXECUTED"`
- `research_disclosure = "RESEARCH ONLY — NO ORDER EXECUTED"`

Allowed state transitions (`SHADOW_STATE_TRANSITIONS`):
- `DETECTED` -> `WAITING_CONFIRMATION`, `CONFIRMED`, `ELIGIBLE`, `REJECTED`, `INVALIDATED`, `EXPIRED`
- `ELIGIBLE` -> `INVALIDATED`, `EXPIRED`, `OBSERVATION_COMPLETED`

Hypothetical tick/candle monitoring (`monitor_open_opportunities_on_quote` / `monitor_open_opportunities_on_closed_candle`):
- Tracks `SHADOW_ENTRY_NOT_TRIGGERED` -> `SHADOW_SIMULATED_ENTRY` -> `SHADOW_SIMULATED_EXIT_TP` / `SHADOW_SIMULATED_EXIT_SL` / `EXIT_SEQUENCE_UNVERIFIABLE` (when a bar spans both SL and TP without tick sequence evidence).
- Never creates or mutates any `PaperOrder` or operational DB row.

