# V17.9.3.1.2.7 verification
Tested executable: `c20adf96828ccc09eb007cc5bfab6dd3edc671ac`.
Engineering: BLOCKED; prepublication: PASS.
Activation: NO. Fresh untouched OOS: NOT_AVAILABLE; October history was previously exposed.
Final remote publication is a separate phase; no unperformed push is certified here.
Failure: No stage exception.

| Function | Responsibility |
|---|---|
| derive_pre_entry_features_from_causal_context_v1793127 | Closed OHLC to source-bound tri-state evidence. |
| build_verified_baseline_pre_entry_feature_ledger_v1793127 | Reconstruct every canonical opportunity without pseudo-features. |
| replay_single_si_policy_v1793122 | Reject unproven research candidates before arbitration; preserve raw intents. |
| compute_matched_policy_ab_v1793127 | Injective decision/trade pairing, separate matched and portfolio deltas. |
| verify_matched_ab_against_raw_ledgers_v1793127 | Reload separate disk ledgers and rebuild the complete comparison. |
| classify_observed_loss_v1793127 | Report measured observations; keep unsupported causes UNKNOWN. |
| verify_fresh_full_junit | Exact fresh collection/JUnit bijection and on-disk mtime/hash checks. |
| verify_raw_accounting_and_worker_proofs | Recompute metrics, costs, statistics and bind worker hashes to raw rows. |

See source inventory for exact file paths and line numbers; raw large ledgers use JSONL.gz.

## Actual completed execution

Command: backend/venv/Scripts/python.exe tools/run_v17_9_3_1_2_7_verification.py --tested-code-sha c20adf96828ccc09eb007cc5bfab6dd3edc671ac
Fresh JUnit: executed 5058, passed 5050, failures 0, errors 0, skipped 8.
Mutation proof: 16 previous and 12 new executable RED/GREEN controls; see the two 19_* evidence files.

| Policy | Fill mode | Closed | Wins | Losses | Net USD | Expectancy R | Open | Gap |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| SI0_OFF_BASELINE | LEGACY_CLOSE_FILL | 164 | 10 | 154 | -313.6541 | -0.777 | 73 | 0 |
| SI0_OFF_BASELINE | NEXT_BAR_OPEN_BID_ASK | 166 | 9 | 157 | -345.8971 | -0.8474 | 74 | 1 |
| SI0_OFF_BASELINE | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 173 | 9 | 164 | -381.7661 | -0.8968 | 73 | 1 |
| SI1_MODEL_QUALITY_AND_ARBITRATION | LEGACY_CLOSE_FILL | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI1_MODEL_QUALITY_AND_ARBITRATION | NEXT_BAR_OPEN_BID_ASK | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI1_MODEL_QUALITY_AND_ARBITRATION | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION | LEGACY_CLOSE_FILL | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION | NEXT_BAR_OPEN_BID_ASK | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY | LEGACY_CLOSE_FILL | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY | NEXT_BAR_OPEN_BID_ASK | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
| SI3_MSS_DISPLACEMENT_AND_CONFIRMED_ENTRY | STRESS_WIDER_SPREAD_SLIPPAGE_2X | 0 | 0 | 0 | 0 | 0.0 | 0 | 0 |
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

Strategy verdict: INCONCLUSIVE_UNDERPOWERED. Activation: NO.
Quote assumption: synthetic close +/- USD 0.10. Previously exposed October history is not fresh untouched OOS.
Original failed attempts remain preserved locally; only this completed frozen execution is certified here.
Final Phase C SHA is recorded externally after its push; immutable artifacts here retain their prepublication verdict.
