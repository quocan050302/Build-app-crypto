# V17.9.0.1.2 — Historical Backtest Canonical Authority Report (`DEF-179012-02` & `DEF-179012-C02`)

- **Final Code SHA (`FINAL_CODE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Backtest Executed Source SHA:** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Authoritative Simulated Trades:** `18` (`Win Rate=38.89%`, `Net PnL=$-16.3506`)

## 1. Two-Stage Positive Eligibility Gate & Chronological Trade Lineage in `v17_8_backtest_runner.py`
1. **Stage 1 (`_extract_historical_opportunities_chronologically`):** Replaced negative blacklist with positive `shared_bt_snap.decision_status == DecisionStatus.ELIGIBLE.value` and `validate_canonical_executability(shared_bt_snap, context=bt_shared_ctx)` before extracting any opportunity.
2. **Stage 2 (`evaluate_and_simulate_variants_on_opportunities`):** Changed default `canonical_is_eligible` fallback from `True` to `False`, requiring explicit `canon_status == DecisionStatus.ELIGIBLE.value`, `canon_is_eligible is True`, valid `decision_id` / `decision_fingerprint`, and valid `validate_canonical_executability` on embedded `shared_decision_snapshot`.
3. **Stage 3 (`execute_chronological_backtest_window_for_parity` & `reconcile_backtest_trade_decision_lineage`):** Verifies that all 18 simulated trades link 1-to-1 to unique `ELIGIBLE` canonical decisions (`unlinked_trades_count=0`, `duplicate_decision_id_count=0`, `non_eligible_trade_count=0`).
