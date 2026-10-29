# AURUM DESK V17.8.0.4 — STRICT UNICORN CAUSAL CHAIN VALIDATION

## 1. Executive Summary
- **Final Code SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Verification Gate:** `G80_UNICORN_CAUSAL_CHAIN_STRICT_VALIDATION` (`PASS`)
- **Detector Implementation:** `backend/services/unicorn_evidence_service.py` (`detect_unicorn_evidence(..., strict_causal_binding=True)`)

## 2. Mandatory 6-Link Causal Chain Enforced
Every `UNICORN_CONFIRMED` setup must satisfy:
1. Prior confirmed swing high/low exists (`swing_id`, `prior_swing_confirmed_at_ms`).
2. Liquidity sweep or failed swing occurs before structure break.
3.Impulsive displacement breaks structure (`BOS`/`MSS` with `mss_or_bos_id` and `displacement_Confirmed_at_ms <= as_of_ms`).
4. Failed opposing candle/zone becomes a confirmed Breaker block (`confirmed_at_ms <= as_of_ms`).
5. A 3-candle FVG from the **same displacement leg** geometrically overlaps `[overlap_low, overlap_high]` with `overlap_low < overlap_high`.
6. Price retests the overlapping Breaker+FVG confluence zone **after** confirmation (`retest_bar_open_ms >= max(breaker.confirmed_at_ms, fvg.confirmed_at_ms)`) without breaching the Breaker distal invalidation boundary before entry.

## 3. Ten Mandatory Validation Cases Verified in `G80`
All 10 mandatory positive and negative causal test cases (Bullish/Bearish valid Unicorn, unrelated FVG+Breaker rejection `CAUSAL_SWING_MISMATCH`, non-overlapping FVG+Breaker `NO_GEOMETRIC_OVERLAP`, unclosed candle rejection, future candle rejection, distal breach invalidation `UNICORN_INVALIDATED`, pre-retest `WAITING_RETEST`, ordinary FVG `MISSING_BREAKER`, and missing BOS `UNICORN_EVIDENCE_INSUFFICIENT`) are verified in `TestG80UnicornStrictCausalValidation`.
