# V16.28 CG-01 Implementation Map

| Symbol / Class / Function | Module | Input / Output | Ownership / Boundary | Associated Tests |
|---|---|---|---|---|
| `build_cg01_displacement_atr_series` | `services/cg01_engine.py` | `(closed_m1, period, as_of_ms) -> Dict[int, float]` | Pure causal ATR computation with True Range and Wilder smoothing | `test_v16_28_atr_exact_formula_and_convention`, `test_v16_28_atr_causality_and_prefix_invariance` |
| `resolve_cg01_mss_and_displacement` | `services/cg01_engine.py` | `(..., atr_1m: Optional[Union[float, Dict]]) -> Optional[Dict]` | Dynamic ATR lookups per break candle; fallback scalar compatibility preserved | `test_v16_28_atr_dynamic_volatility_low_vs_high`, `test_v16_28_atr_pre_break_baseline_not_self_referential` |
| `assess_cg01_fvg_lifecycle` | `services/cg01_engine.py` | `(fvg, closed_m1, dir, as_of_ms, config, mode, sl) -> Dict` | Pure lifecycle analyzer: CE touch, penetration depth, freshness, invalidation | `test_v16_28_fvg_formation_known_at_third_candle_close`, `test_v16_28_fvg_lifecycle_fresh_candidate`, `test_v16_28_fvg_lifecycle_limit_used_before_arm` |
| `select_cg01_leg_fvg` | `services/cg01_engine.py` | `(closed_m1, mss, dir, as_of_ms, config) -> Optional[Dict]` | Evaluates primary and secondary leg patterns against lifecycle | `test_v16_28_fvg_lifecycle_primary_invalid_secondary_valid` |
| `resolve_cg01_retest` | `services/cg01_context_adapter.py` | `(closed_m1, fvg, dir, as_of_ms, config, sl) -> CG01RetestResult` | Invalidation tracking across all candles following confirmed retest | `test_v16_28_fvg_lifecycle_post_retest_structural_invalidation` |
| `evaluate_cg01_candidate` | `services/cg01_engine.py` | `(bundle, quote, config, now_ms) -> CG01Decision` | Multi-POI exhaustive evaluation loop, ATR warmup enforcement, executable prioritization | `test_v16_28_multi_poi_waiting_first_does_not_block_executable_second`, `test_v16_28_e2e_production_long_flow`, `test_v16_28_e2e_production_short_flow` |
