# AURUM Desk V17.9.2.1 — Implementation & Defect Repair Diff Summary

- **Run ID:** `v17_9_2_1_20261101T074221Z_b95ef57b237c`
- **Branch:** `feature/v17-9-2-structural-sl-tp-from-qa`
- **Pre-Edit V17.9.2 SHA:** `369fa2645b01c747ae386f8fd4aa4fd955d56a95`
- **Tested V17.9.2.1 Code SHA:** `b95ef57b237cc7024ac3ef6674591794b0a059a8`

## 1. Files Modified / Added Since `369fa2645b01`

```text
.../backend/services/shared_strategy_evaluator.py  |    6 +
 .../v17_9_2_structural_stop_intelligence.py        |  445 +++-
 .../tests/test_v17_9_2_1_validation_closure.py     |  996 +++++++++
 .../tools/run_v17_9_2_1_closure_verification.py    | 2167 ++++++++++++++++++++
 .../tools/run_v17_9_2_structural_stop_backtest.py  |    6 +
 aurum-desk/tools/run_v17_9_2_verification.py       |   13 +-
 6 files changed, 3576 insertions(+), 57 deletions(-)
```

## 2. Exact Defect Repairs Implemented

1. **`backend/services/v17_9_2_structural_stop_intelligence.py` (`resolve_causal_target_candidates`):**
   - Added `_is_pool_swept_cross_tf` checking all closed LTF/HTF candle series (`1M`, `5M`, `15M`, `1H`, `4H`)
     after `occurred_at_ms` up to `decision_timestamp_ms` so HTF swing pools and `PDH`/`PDL` levels already swept
     by recent LTF price action inside the current HTF candle are excluded from causal target candidates.
2. **`backend/services/v17_9_2_structural_stop_intelligence.py` (`collect_causal_stop_anchor_candidates`):**
   - Extended `_add_candidate` invalidation validation across `strictly_closed_m5` and `strictly_closed_m15`
     in addition to `strictly_closed_m1` so older `M5`/`M15` swing anchors invalidated prior to the 120-minute
     `M1` window are properly marked `is_invalidated=True` (`ANCHOR_ALREADY_INVALIDATED`).
3. **`backend/services/v17_9_2_structural_stop_intelligence.py` (`classify_structure_stop_outcome`, `measure_premature_stop_rate`):**
   - Repaired unreachable `DELAYED_INEVITABLE_LOSS` branch (structure break within `delayed_break_window_bars=5`
     M1 bars of stop hit without hitting TP) and added forensic sub-classes (`PURE_NOISE_SWEEP_STRUCTURE_HELD`,
     `NOISE_SWEEP_THEN_TP_HIT_EX_POST`, `PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK`,
     `DELAYED_INEVITABLE_BREAK_WITHIN_5_BARS`, `IMMEDIATE_STRUCTURE_INVALIDATION`,
     `INTRATRADE_PROVIDER_GAP_BEFORE_EXIT`) plus Wilson 95% confidence intervals.
4. **`backend/services/v17_9_2_structural_stop_intelligence.py` (`simulate_order_fill_and_outcome_sensitivity`):**
   - Added realistic quote-aware forward fill sensitivity simulator comparing `LEGACY_CLOSE_FILL`,
     `NEXT_BAR_OPEN_BID_ASK`, and `STRESS_WIDER_SPREAD_SLIPPAGE_2X` with gap-spanning entry rejection
     (`NO_FILL_ENTRY_GAP`) and intratrade gap handling (`DATA_GAP_UNRESOLVED`, `ADVERSE_GAP_OPEN_STOP_LOSS`).
