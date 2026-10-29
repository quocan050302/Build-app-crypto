# V17.9.0.1 — Acceptance Gate Repair Report (`G89`–`G105`)

## 1. Root Cause of V17.9.0 Gate Selector Misalignment
In `V17.9.0`, `backend/tests/test_v17_9_shared_parity_and_async_infra.py` defined 40 tests (`test_01` through `test_40`), where:
- `test_01`–`test_05` tested `MarketDecisionContext` (5 tests), shifting `StrategyDecisionSnapshot` tests to `test_06`–`test_08` and per-model Stage-1 characterization tests to `test_09`–`test_13`.
- Consequently, `G90`–`G95` and `G98`–`G105` in `tools/run_v17_8_acceptance.py` had off-by-two `-k` selectors (e.g., `G101` pointed to `test_31 or test_32 or test_33` instead of `test_39`, and `G104` pointed to `test_39` instead of `test_30 or test_31`).

## 2. V17.9.0.1 Repair Applied
1. Updated `tools/run_v17_8_acceptance.py` and `tools/acceptance_verdict.py` (`AUTHORITATIVE_GATE_MAPPING_V17_9_0_1`) so every gate `G89`–`G105` maps 1-to-1 to both its exact test functions in `backend/tests/test_v17_9_shared_parity_and_async_infra.py` and its corresponding live runtime integration / negative acceptance tests in `backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py`.
2. Added `normalize_v17_9_0_1_gate_status` and `determine_v17_9_0_1_top_level_verdict` in `tools/acceptance_verdict.py` so `SKIPPED`, `NOT_RUN`, `NOT_EXECUTED`, `BLOCKED`, or `UNKNOWN` are never converted into `PASS`.
