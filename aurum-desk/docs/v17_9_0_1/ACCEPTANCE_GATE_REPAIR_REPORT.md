# V17.9.0.1.1 — Acceptance Gate & Provenance Repair Report (`G01`–`G107`)

## 1. Root Cause of V17.9.0 Gate Selector Misalignment & Stale SHA Overwrite
1. In `V17.9.0`, `backend/tests/test_v17_9_shared_parity_and_async_infra.py` defined 40 tests (`test_01` through `test_40`), shifting `StrategyDecisionSnapshot` tests to `test_06`–`test_08` and per-model Stage-1 characterization tests to `test_09`–`test_13`.
2. In `V17.9.0.1`, `tools/generate_v17_9_0_1_artifacts.py` re-ran `G89`–`G105` on the new commit while reusing `G01`–`G88` from a prior run directory and overwriting `run_results['tested_source_sha'] = code_sha`.

## 2. V17.9.0.1.1 Repair Applied
1. Updated `tools/run_v17_8_acceptance.py` and `tools/acceptance_verdict.py` (`AUTHORITATIVE_GATE_MAPPING_V17_9_0_1`) so every gate `G89`–`G105` maps 1-to-1 to both its exact test functions in `backend/tests/test_v17_9_shared_parity_and_async_infra.py` and its corresponding live runtime integration / fault-injection acceptance tests in `backend/tests/test_v17_9_0_1_runtime_integration_and_gates.py`.
2. Added `verify_gate_provenance_integrity` and `expected_source_sha` enforcement in `normalize_v17_9_0_1_gate_status` and `determine_v17_9_0_1_top_level_verdict` so any gate with a missing or stale `tested_source_sha != FINAL_CODE_SHA` is normalized to `INVALID_EVIDENCE` and blocks release (`BLOCKED`).
