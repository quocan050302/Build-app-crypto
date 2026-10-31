# V17.9.0.1.2 — Strict Eligibility Invariant Report (`DEF-179012-01` & `DEF-179012-02`)

- **Final Code SHA (`FINAL_CODE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Evaluator Version:** `17.9.0.1.1`
- **Negative Controls Verified (`N01`–`N50`):** `234/234` (`status=PASS`)

## 1. Global Positive Eligibility Contract (`validate_canonical_executability`)
A strategy decision is executable **ONLY** when:
1. `isinstance(decision, DecisionResult)`
2. `decision.decision_status == DecisionStatus.ELIGIBLE.value` (strict positive whitelist; all other statuses fail closed)
3. `decision.setup_validity == 'VALID'`
4. `valid_decision_lineage(decision, context)` (`decision_id`, 64-hex `decision_fingerprint`, 64-hex `input_snapshot_hash`, 64-hex `configuration_hash`)
5. `valid_direction_entry_sl_tp(decision)` (`direction == effective_direction in {'LONG', 'SHORT'}`, finite positive `entry_reference`, `selected_sl`, `take_profit`, directional SL/TP geometry)
6. `valid_required_risk_intent(decision)` (`net_rr > 0`, `risk_or_sizing_intent.status == 'ELIGIBLE'`, empty `rejection_reason_codes`)
7. `valid_point_in_time_inputs(context)` (`causal_valid=True`, closed M1/M5/M15/H1/H4 candles)

## 2. Non-ELIGIBLE Fail-Closed Matrix (`N01`–`N09`)
| Control | Status Tested | Canonical Executable | Legacy Promotion Blocked | Backtest Trade Blocked |
|---|---|---|---|---|
| `N01` | `NO_TRADE` | `False` | `True` (`REJECT`) | `True` (`0` trades) |
| `N02` | `REJECTED` | `False` | `True` (`REJECT`) | `True` (`0` trades) |
| `N03` | `VALID_SETUP_REJECTED_BY_RISK` | `False` | `True` (`REJECT`) | `True` (`0` trades) |
| `N04` | `NO_VALID_SETUP` | `False` | `True` (`REJECT`) | `True` (`0` trades) |
| `N05` | `WAITING_CONFIRMATION` | `False` | `True` (`REJECT`) | `True` (`0` trades) |
| `N06` | `MODEL_DISABLED` | `False` | `True` (`REJECT`) | `True` (`0` trades) |
| `N07` | `MISSING_MARKET_DATA` | `False` | `True` (`REJECT`) | `True` (`0` trades) |
| `N08` | `UNKNOWN` | `False` | `True` (`REJECT`) | `True` (`0` trades) |
| `N09` | `MALFORMED_UNRECOGNIZED_STATUS` / `None` | `False` | `True` (`REJECT`) | `True` (`0` trades) |
