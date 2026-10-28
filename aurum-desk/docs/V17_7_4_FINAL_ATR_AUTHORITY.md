# AURUM DESK V17.7.4 — FINAL ATR AUTHORITY
**Strict Provenance, Unified Volatility Validation & Execution-Grade Barrier**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Reference HEAD:** `40f52d96f02b08ceaae6a2cfc9ff2673960dd949`

---

## 1. Overview & Core Philosophy

In algorithmic trading execution, structural stop-loss placement must rely on true, unadulterated market volatility. A critical vulnerability in legacy implementations was the conflation of **weighted buffer components** (such as $k_{\text{m1}} \times \text{ATR}_{\text{raw}}$) with **authoritative raw ATR metrics**. In AURUM DESK V17.7.4:
1. `PAPER_STRUCTURAL` mode is strictly restricted to raw ATR with cryptographically and causally verified provenance.
2. Weighted buffers, loose scalars, and historical compatibility data can **never** substitute for authoritative raw ATR.
3. If authoritative volatility evidence is missing or invalid, the execution engine **fails closed** (`WAITING_VOLATILITY_EVIDENCE`), disarming candidate trades before order creation.

---

## 2. Mathematical Definition & Algorithm

### 2.1 Classical SMA True Range (`SMA_TR_V1`)
For a contiguous sequence of closed price bars $i \in \{1, \dots, N\}$ where $N \ge \text{period} + 1$:

$$\text{TR}_i = \max\Big(H_i - L_i, \; |H_i - C_{i-1}|, \; |L_i - C_{i-1}|\Big)$$

$$\text{ATR}_N = \frac{1}{\text{period}} \sum_{j = N - \text{period} + 1}^{N} \text{TR}_j$$

- **Cadence Integrity:** Bars within the active calculation window must maintain contiguous time intervals ($\Delta t = 60\,000\text{ ms}$ for 1M; $\Delta t = 300\,000\text{ ms}$ for 5M). Any cadence gap invalidates the calculation.
- **Causal Invariance:** Only closed bars where $t_{\text{open}} + \text{duration} \le t_{\text{decision\_cutoff}}$ are admissible. Open candles or candles closing in the future are strictly rejected.
- **Explicit Closure Flag:** Candles with `is_closed is False` are discarded immediately, preventing unclosed intra-bar volatility from leaking into execution decisions.

### 2.2 Dynamic Buffer vs. Raw Volatility Isolation
The dynamic buffer calculation combines risk floor and volatility components:

$$\text{Buffer} = \max\Big(\text{tick\_floor}, \; k_{\text{m1}} \times \text{ATR}_{\text{raw\_m1}}, \; k_{\text{m5}} \times \text{ATR}_{\text{raw\_m5}}, \; k_{\text{spread}} \times \text{Spread}\Big)$$

- `StopBufferResult.components` stores the weighted contributions (e.g. $k_{\text{m1}} \times \text{ATR}_{\text{raw\_m1}}$).
- `StopBufferResult.raw_atr_m1` stores exclusively the unscaled raw metric $\text{ATR}_{\text{raw\_m1}}$.
- It is architecturally impossible to derive or reverse-calculate `raw_atr_m1` from `components`.

---

## 3. The Unified ATR Authority Validator (`validate_stop_atr_authority`)

To prevent inconsistent validation across modules, a single authoritative validator contract is implemented in `stop_placement_service.py` and enforced across the planner, all model evaluators, and the ARM critical section.

### 3.1 Contract Checks & Reason Codes

```
[Input Object] ──► 1. Schema & Type Check (Not None, has status & value)
               ──► 2. Status == "AVAILABLE"
               ──► 3. Numeric Value: Finite, > 0.0, NOT bool
               ──► 4. Timeframe: Matches expected ("1M" / "5M")
               ──► 5. Symbol: Matches candidate symbol ("XAUUSDT")
               ──► 6. Period: Matches policy configuration (default 14)
               ──► 7. Method: "SMA_TR_V1"
               ──► 8. Bar Count: bars_count >= period + 1
               ──► 9. Causal Closure: last_closed_at_ms <= decision_cutoff
               ──► 10. Future Timestamps: No evidence timestamp > decision_cutoff
               ──► 11. Metadata Consistency: bars_count == source_candle_count
               ──► 12. Freshness Gate: cutoff - last_closed_at_ms <= max_staleness
               ──► [PASS / FAIL + Stable Reason Code]
```

#### Standardized Rejection Codes
- `ATR_MISSING`: Input reference is null.
- `ATR_PROVENANCE_MISSING`: Object lacks required schema attributes.
- `ATR_STATUS_UNAVAILABLE`: Object status is not `AVAILABLE`.
- `ATR_NON_POSITIVE_OR_NON_FINITE`: ATR value is non-numeric, boolean, $\le 0.0$, NaN, or infinite.
- `ATR_TIMEFRAME_MISMATCH`: Timeframe does not match the evaluation slot (e.g., 5M supplied to M1 slot).
- `ATR_SYMBOL_MISMATCH`: ATR instrument does not match candidate instrument.
- `ATR_PERIOD_MISMATCH`: Calculation period differs from policy setting.
- `ATR_METHOD_UNSUPPORTED`: Algorithm is not an approved versioned method.
- `ATR_SOURCE_COUNT_INSUFFICIENT`: Insufficient source candles to compute valid TR window.
- `ATR_SOURCE_AFTER_DECISION`: Closed bar timestamp violates causality.
- `ATR_PROVENANCE_INCONSISTENT`: Duplicate metadata fields contain conflicting values.
- `ATR_STALE_FOR_DECISION`: Volatility evidence exceeds policy staleness threshold.

---

## 4. Single-Resolution Planner (`plan_structural_stop`)

In `PAPER_STRUCTURAL` mode:
1. Authority resolution occurs **once** at Step 9b.
2. Fallback to loose scalars (`atr_m1_val`) or weighted buffer components is completely eliminated.
3. If M1 authority is invalid or missing:
   - `decision.status = "WAITING_VOLATILITY_EVIDENCE"`
   - `decision.selected_sl = None`
   - `decision.applied_variant = "NONE"`
   - `decision.distance_assessment = "DISTANCE_ATR_UNAVAILABLE"`
   - `decision.volatility_context = {"volatility_gate_status": "FAIL_CLOSED", "volatility_gate_reason": <reason>}`

### 4.1 Structural SL Mathematical Formulas
When authority is validated:
- **LONG Trade:**
  $$\text{Anchor Stop} = \lfloor \text{Anchor Price} - \text{Buffer} \rfloor_{\text{tick}}$$
  $$\text{Protective Stop} = \lfloor \text{Planned Entry} - \text{Min Protective Distance} \rfloor_{\text{tick}}$$
  $$\text{Selected Structural SL} = \min(\text{Anchor Stop}, \; \text{Protective Stop})$$

- **SHORT Trade:**
  $$\text{Anchor Stop} = \lceil \text{Anchor Price} + \text{Buffer} \rceil_{\text{tick}}$$
  $$\text{Protective Stop} = \lceil \text{Planned Entry} + \text{Min Protective Distance} \rceil_{\text{tick}}$$
  $$\text{Selected Structural SL} = \max(\text{Anchor Stop}, \; \text{Protective Stop})$$

- **Normalized Distance Ratio:**
  $$\text{Distance Ratio (ATR)} = \frac{|\text{Planned Entry} - \text{Selected Structural SL}|}{\text{Validated Raw ATR M1}}$$
  *(Weighted buffer is NEVER used as the divisor).*

---

## 5. Integration Across Call Chains

1. **Scalp Watcher Orchestrator (`evaluate_cycle`):**
   - Resolves policy once per cycle.
   - Computes `atr_m1_obj` and `atr_m5_obj` with explicit symbol binding (`symbol="XAUUSDT"`).
   - Injects typed `StopATRResult(status="UNAVAILABLE")` if candle feed is missing or unclosed.
2. **Model Services (`ScalpM1ModelService`):**
   - All four M1 models (Reversal, Continuation, FVG Continuation, Sweep Reclaim) receive `stop_context` and pass verified ATR objects into `apply_stop_placement_to_candidate`.
   - Scalar ambiguity is eliminated; `eff_scalar_m1` is set to `None` in `PAPER_STRUCTURAL`.
3. **ARM Barrier (`_arm_paper_candidate` & `validate_applied_stop_decision`):**
   - Enforces execution-grade ATR evidence validation inside the serialized entry mutation boundary.
   - Verifies that `volatility_context["atr_m1"]` matches `buffer.raw_atr_m1` within numerical tolerance ($10^{-4}$).
   - Detects and rejects any forged `VALID` structural decision lacking authentic underlying volatility evidence.

---
*Signed and Approved for Production Paper Opt-In Evaluation.*
