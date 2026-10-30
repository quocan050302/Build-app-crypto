# AURUM DESK V17.8.0.1 — CAUSAL UNICORN PROVENANCE & EPISODE BINDING PROOF (`DEF-178-10`)

**Defect ID:** `DEF-178-10`  
**Severity:** P1 (SMC/ICT Structural Integrity & Anti-Lookahead Defect)  
**Affected Files:**  
- `backend/services/unicorn_evidence_types.py`
- `backend/services/unicorn_evidence_service.py`
- `backend/services/stop_placement_service.py`
- `backend/tests/test_v17_8_runtime_integration.py`

---

## 1. Defect Description

Independent source audit of `unicorn_evidence_service.py` and `stop_placement_service.py` identified four causal weaknesses:
1. **Pullback OB Causal Ordering:** `detect_breaker_blocks` searched `[0, i)` for the opposing Order Block without requiring the pullback OB to form *after* the broken swing (`broken_swing.timestamp_ms <= ob_bar.timestamp_ms < break_bar.timestamp_ms`).
2. **Episode Binding:** `detect_unicorn_evidence` checked price overlap `[max(breaker.low, fvg.low), min(breaker.high, fvg.high)]` without enforcing that the FVG and Breaker belong to the same causal displacement episode (`abs(fvg_confirmed_ms - breaker.confirmed_at_ms) <= max_episode_gap_ms`).
3. **Non-Null `invalidation_price` on Invalid Evidence:** When a Unicorn candidate failed validation (e.g., `WAITING_RETEST` or `INVALIDATED_DISTAL_BREACH`), `invalidation_price` could still carry a numeric price instead of `None`.
4. **Dropped Context in `resolve_stop_anchor`:** `stop_placement_service.resolve_stop_anchor` did not forward `selected_break`, `selected_fvg`, or `selected_reference` when delegating to `resolve_stop_anchor_v17_7`.

---

## 2. Causal Sequence & Provenance Implementation

### 2.1 Mandatory Causal Sequence
Both Bullish and Bearish Unicorn detection strictly enforce:
1. Prior Swing formed and confirmed (`broken_swing_id`, `broken_swing_ts < ob_ts`).
2. Opposing Pullback Order Block formed after the swing (`breaker_source_ob_id`, `broken_swing_ts <= ob_ts < break_ts`).
3. Impulsive Displacement & Structure Break (`mss_or_bos_id`, `displacement_id`) closing beyond both the prior swing and the opposing OB (`confirmed_at_ms <= as_of_ms`).
4. Fair Value Gap (`selected_fvg_id`) formed during the same causal displacement episode (`_is_same_causal_episode` within 8 bars of `breaker.confirmed_at_ms`) and geometrically overlapping `[overlap_low, overlap_high]` by at least `min_overlap_ticks * tick_size`.
5. Causal Retest (`retest_timestamp_ms >= max(breaker.confirmed_at_ms, fvg_confirmed_ms)`) without any closed candle breaching `breaker.distal_price`.

### 2.2 Required Provenance Fields on `UnicornEvidence`
- `unicorn_id`
- `selected_fvg_id` & `fvg_id`
- `breaker_id` & `breaker_source_ob_id`
- `broken_swing_id`
- `mss_or_bos_id` & `displacement_id`
- `setup_episode_id`
- `overlap_low` & `overlap_high`
- `invalidation_price` (guaranteed `None` whenever `is_valid is False`)
- `confirmed_at_ms`, `known_at_ms`, `decision_cutoff_ms`

### 2.3 Negative Controls Verified (`test_def_178_10_causal_unicorn_provenance_and_negative_controls`)
- **Stale Episode Mismatch:** FVG confirmed > 8 bars away from Breaker fails closed with `UnicornValidityStatus.EPISODE_MISMATCH` (`is_valid=False, invalidation_price=None`).
- **Lookahead FVG / Bar Rejection:** `selected_fvg.confirmed_at_ms > as_of_ms` fails closed with `UnicornValidityStatus.FUTURE_DATA_REJECTED`.
- **Unclosed HTF Candle Rejection:** `htf_evidence={"is_closed": False}` or `close_time_ms > as_of_ms` fails closed with `UnicornValidityStatus.UNCLOSED_EVIDENCE_REJECTED`.
- **Consumed / Invalidated FVG Rejection:** `selected_fvg={"consumed": True}` fails closed with `UnicornValidityStatus.CONSUMED_OR_INVALIDATED_FVG`.
- **Wrong Direction Overlap:** Bullish FVG passed to `SHORT` evaluation fails closed with `UnicornValidityStatus.WRONG_DIRECTION_OVERLAP`.
- **Invalidated Distal Boundary:** Price closing beyond `breaker.distal_price` after confirmation fails closed with `UnicornValidityStatus.INVALIDATED_DISTAL_BREACH` and `invalidation_price=None`.

