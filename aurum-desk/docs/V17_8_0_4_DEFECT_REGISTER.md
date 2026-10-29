# AURUM DESK V17.8.0.4 — DEFECT REGISTER & CLOSURE PROOF

| Defect ID | Severity | Description & Root Cause | Files Repaired | Verification Gates & Tests | Status |
|---|---|---|---|---|---|
| `DEF-17803-05` | P0 | `aurum-desk/.gitignore` `*.log` rule blocked `docs/artifacts/v17_8_0_3/.../logs/*.log` from being committed to remote Git | `aurum-desk/.gitignore`, `tools/artifact_verifier.py` | `G76_REMOTE_ACCEPTANCE_LOG_COMPLETENESS`, `G88_EXACT_COMMIT_REMOTE_EVIDENCE_VERIFICATION` | **CLOSED** |
| `DEF-17803-06` | P0 | `build_source_manifest()` walked working directory instead of committed Git tree, including untracked files and `.DS_Store` | `tools/artifact_verifier.py`, `aurum-desk/.gitignore` | `G77_GIT_REPRODUCIBLE_SOURCE_MANIFEST`, `G78_SOURCE_MANIFEST_NEGATIVE_CONTROLS` | **CLOSED** |
| `DEF-17803-07` | P0 | Backtest opportunity extractor evaluated only FVG retests, synthesized `atr_m1 * 2.1`, and lacked matched-cohort stop-out isolation | `backend/services/v17_8_backtest_runner.py`, `backend/services/unicorn_evidence_service.py`, `backend/services/market_shadow_service.py` | `G79`–`G84`, `G86_BACKTEST_ANTI_BIAS_NEGATIVE_CONTROLS` | **CLOSED** |
| `DEF-17804-01` | P1 | `identify_confirmed_swings()` in `liquidity_scenario_service.py` raised `AttributeError` when passed `dict` candles instead of object candles | `backend/services/liquidity_scenario_service.py` | `G79_PRODUCTION_BACKTEST_SHADOW_DECISION_PARITY` | **CLOSED** |
| `DEF-17804-02` | P1 | `BitgetWSService._resolve_active_shadow_sink()` did not check `is_market_shadow_runtime()` first and `unregister_shadow_sink()` fell back to default sink | `backend/services/bitget_ws_service.py` | `G85_MARKET_SHADOW_ROUTING_REGRESSION` | **CLOSED** |
