# V17.9.3.1.2.9 — Liquidity Provenance Closure & Independent Strategy Failure Diagnosis

- **Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Grounded V128 Remote HEAD**: `e35f2739eb66eee4e4f2ae924c806d98d3f3ab95`
- **Tested Phase A Executable SHA**: `199a953eedf27bf5983458fdf2f0a4375f3a49cb`
- **Canonical Dataset SHA-256**: `9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`
- **Run ID**: `20261107T051531Z_199a953eedf2`
- **Prepublication Engineering Verdict**: `PASS` (G01..G39 PASS; G40 BLOCKED pending external Phase C push receipt)
- **Signal Discovery Verdict**: `SOURCE_VERIFIED_BOUNDED_15_DISTINCT_CLOSED_TRADES_IN_SI1_SI2_SI3_ZERO_IN_SI4_TO_SI9`
- **Trading Edge Verdict**: `INCONCLUSIVE_UNDERPOWERED`
- **Fresh OOS Status**: `REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS`
- **Quote Provenance Verdict**: `SYNTHETIC_RESEARCH_QUOTE_CLOSE_PLUS_MINUS_0.10_USD`
- **Activation Ready**: `NO`

## P0/P1 Defects Closed in V17.9.3.1.2.9
1. **P0-A (`evaluate_signal_intelligence_gate_v1793122`)**: Removed synthetic `prop_target_id` + `prop_tp` pool injection; requires detector-confirmed MTF swings with contiguous post-pivot coverage.
2. **P0-B (`build_signal_quality_evidence_v1793122`)**: Separated `target_geometrically_feasible` from `target_pool_proven` and required both for `target_reachable`.
3. **P0-C (`_resolve_unswept_liquidity_pool_target_v1793123`)**: Added `_verify_post_pivot_m1_coverage_v1793129` so empty or gapped M1 bars fail closed as `UNKNOWN` (`False`), while observed sweeps return `PROVEN_SWEPT`.
4. **P0-D (`_build_causal_swing_pools_from_swings_v1793124`)**: Removed `known_at_ms` fallback to `occurred_at_ms`.
5. **P1-A (`replay_single_si_policy_v1793122`)**: Repaired vacuous `SI9_H5` reachability gate to check verified `target_selected_pool_bound` + `target_unswept` + ex-ante geometric feasibility.

## All Policies x 3 Fill Modes Replay Summary

| Policy | Fill Mode | Closed | Wins | Losses | Net USD | Expectancy R | Open | Gap |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| SI0_OFF_BASELINE | LEGACY_CLOSE_FILL | 164 | 10 | 154 | -313.6541 | -0.777 | 73 | 0 |
| SI0_OFF_BASELINE | NEXT_BAR_OPEN_BID_ASK | 166 | 9 | 157 | -345.8971 | -0.8474 | 74 | 1 |
| SI0_OFF_BASELINE | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 173 | 9 | 164 | -381.7661 | -0.8968 | 73 | 1 |
| SI1_MODEL_QUALITY_AND_ARBITRATION | LEGACY_CLOSE_FILL | 6 | 0 | 6 | -14.8307 | -1.0001 | 4 | 0 |
| SI1_MODEL_QUALITY_AND_ARBITRATION | NEXT_BAR_OPEN_BID_ASK | 6 | 0 | 6 | -14.5997 | -0.9845 | 4 | 0 |
| SI1_MODEL_QUALITY_AND_ARBITRATION | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 6 | 0 | 6 | -15.1845 | -1.0239 | 4 | 0 |
| SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION | LEGACY_CLOSE_FILL | 9 | 0 | 9 | -22.2001 | -1.0 | 7 | 0 |
| SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION | NEXT_BAR_OPEN_BID_ASK | 9 | 0 | 9 | -22.2001 | -1.0 | 7 | 0 |
| SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 9 | 0 | 9 | -23.3519 | -1.0518 | 7 | 0 |
| SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY | LEGACY_CLOSE_FILL | 6 | 0 | 6 | -14.8307 | -1.0001 | 4 | 0 |
| SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY | NEXT_BAR_OPEN_BID_ASK | 6 | 0 | 6 | -14.5997 | -0.9845 | 4 | 0 |
| SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 6 | 0 | 6 | -15.1845 | -1.0239 | 4 | 0 |
| SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN | LEGACY_CLOSE_FILL | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN | NEXT_BAR_OPEN_BID_ASK | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET | LEGACY_CLOSE_FILL | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET | NEXT_BAR_OPEN_BID_ASK | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI6_COMBINED_PREDECLARED | LEGACY_CLOSE_FILL | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI6_COMBINED_PREDECLARED | NEXT_BAR_OPEN_BID_ASK | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI6_COMBINED_PREDECLARED | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI7_CORE_TRADING_LOGIC_REPAIRED | LEGACY_CLOSE_FILL | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI7_CORE_TRADING_LOGIC_REPAIRED | NEXT_BAR_OPEN_BID_ASK | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI7_CORE_TRADING_LOGIC_REPAIRED | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI8_RESEARCH_DISCOVERY_REPAIRED | LEGACY_CLOSE_FILL | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI8_RESEARCH_DISCOVERY_REPAIRED | NEXT_BAR_OPEN_BID_ASK | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI8_RESEARCH_DISCOVERY_REPAIRED | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS | LEGACY_CLOSE_FILL | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS | NEXT_BAR_OPEN_BID_ASK | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
