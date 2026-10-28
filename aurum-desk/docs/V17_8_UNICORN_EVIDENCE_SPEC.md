# AURUM DESK V17.8 — ICT UNICORN EVIDENCE SPECIFICATION

**Version:** V17.8.0  
**Module:** `backend/services/unicorn_evidence_service.py` & `unicorn_evidence_types.py`  
**Test Suite:** `backend/tests/test_v17_8_unicorn_evidence.py`  
**Status:** IMPLEMENTED & UNIT TESTED (PASS)  

---

## 1. PURPOSE AND DEFINITION

The ICT Unicorn Model represents a high-probability institutional confluence setup defined by the simultaneous presence of:
1. **A Breaker Block (MSS/BOS converted Order Block)**
2. **A Fair Value Gap (FVG)** formed during the impulsive displacement leg
3. **Geometric Price Overlap** between the Breaker Block and FVG
4. **Causal Setup Retest** strictly after confirmation of both components
5. **Structural Invalidation Level** anchored to the distal boundary of the Breaker Block (or thesis sweep extreme)

---

## 2. STRUCTURAL ANATOMY & SYMMETRY

The engine supports full mathematical symmetry between Bullish and Bearish configurations.

### 2.1 Bullish Unicorn (LONG Direction)
1. **Swing High Reference ($SH$):** Confirmed pivot high known at or before the break bar.
2. **Bearish Order Block ($OB_{\text{down}}$):** The down-close candle(s) preceding the low of the swing.
   - Boundaries: $\text{Breaker}_{\text{low}} = OB_{\text{low}}$, $\text{Breaker}_{\text{high}} = OB_{\text{high}}$.
3. **MSS/BOS Break Bar:** The initial candle closing strictly above $SH$ with displacement body.
   - Confirmation: Closing time of the break bar.
4. **Bullish FVG:** Created by the displacement leg:
   - Boundaries: $[\text{FVG}_{\text{bottom}}, \text{FVG}_{\text{top}}]$.
5. **Price Overlap Condition:**
   $$\text{overlap}_{\text{low}} = \max(\text{Breaker}_{\text{low}}, \text{FVG}_{\text{bottom}})$$
   $$\text{overlap}_{\text{high}} = \min(\text{Breaker}_{\text{high}}, \text{FVG}_{\text{top}})$$
   $$\text{overlap}_{\text{high}} - \text{overlap}_{\text{low}} \ge \text{min\_overlap\_ticks} \times \text{tick\_size}$$
6. **Distal Invalidation Level:**
   $$\text{invalidation\_price} = \text{Breaker}_{\text{low}}$$
7. **Retest:** Subsequent closed or active price touches the overlap zone:
   $$\text{Bar}_{\text{low}} \le \text{overlap}_{\text{high}} \quad \text{and} \quad \text{Bar}_{\text{high}} \ge \text{overlap}_{\text{low}}$$

### 2.2 Bearish Unicorn (SHORT Direction)
1. **Swing Low Reference ($SL$):** Confirmed pivot low known at or before the break bar.
2. **Bullish Order Block ($OB_{\text{up}}$):** The up-close candle(s) preceding the high of the swing.
   - Boundaries: $\text{Breaker}_{\text{low}} = OB_{\text{low}}$, $\text{Breaker}_{\text{high}} = OB_{\text{high}}$.
3. **MSS/BOS Break Bar:** The initial candle closing strictly below $SL$ with displacement body.
   - Confirmation: Closing time of the break bar.
4. **Bearish FVG:** Created by the displacement leg:
   - Boundaries: $[\text{FVG}_{\text{bottom}}, \text{FVG}_{\text{top}}]$.
5. **Price Overlap Condition:**
   $$\text{overlap}_{\text{low}} = \max(\text{Breaker}_{\text{low}}, \text{FVG}_{\text{bottom}})$$
   $$\text{overlap}_{\text{high}} = \min(\text{Breaker}_{\text{high}}, \text{FVG}_{\text{top}})$$
   $$\text{overlap}_{\text{high}} - \text{overlap}_{\text{low}} \ge \text{min\_overlap\_ticks} \times \text{tick\_size}$$
6. **Distal Invalidation Level:**
   $$\text{invalidation\_price} = \text{Breaker}_{\text{high}}$$
7. **Retest:** Subsequent closed or active price touches the overlap zone:
   $$\text{Bar}_{\text{high}} \ge \text{overlap}_{\text{low}} \quad \text{and} \quad \text{Bar}_{\text{low}} \le \text{overlap}_{\text{high}}$$

---

## 3. STRICT NON-NEGOTIABLE INVARIANTS

1. **Zero Future Candle Dependency:** Only closed candles where $t_{\text{close}} \le \text{as\_of\_ms}$ are evaluated.
2. **Initial Break Authority:** Only the initial breaking candle converts an Order Block into a Breaker Block. Subsequent candles trading in continuation do not generate duplicate/mutated breaker blocks.
3. **No Phantom Overlap:** If Breaker and FVG are disjoint, status is explicitly `NO_GEOMETRIC_OVERLAP`.
4. **Prior Breach Invalidation:** If closed price breaches the distal level prior to the decision cutoff, status is `UNICORN_INVALIDATED` (`DISTAL_LEVEL_BREACHED`).
5. **Retest Pre-condition:** A retest is recognized only if it occurs strictly **after** both Breaker confirmation and FVG confirmation.

---

## 4. TYPED EVIDENCE DATA CONTRACT

```python
@dataclass(frozen=True)
class UnicornEvidence:
    unicorn_id: str
    symbol: str
    direction: str
    setup_episode_id: str
    breaker_id: Optional[str]
    breaker_type: Optional[str]
    breaker_low: Optional[float]
    breaker_high: Optional[float]
    breaker_distal_price: Optional[float]
    breaker_confirmed_at_ms: Optional[int]
    fvg_id: Optional[str]
    fvg_low: Optional[float]
    fvg_high: Optional[float]
    fvg_confirmed_at_ms: Optional[int]
    overlap_low: Optional[float]
    overlap_high: Optional[float]
    mss_or_bos_id: Optional[str]
    displacement_id: Optional[str]
    sweep_id: Optional[str]
    invalidation_source_id: Optional[str]
    invalidation_price: Optional[float]
    retest_timestamp_ms: Optional[int]
    known_at_ms: int
    decision_cutoff_ms: int
    validity_status: str
    reason_codes: Tuple[str, ...]
    algorithm_version: str = "V17_8_0"
```

*Specification Certified & Verified.*
