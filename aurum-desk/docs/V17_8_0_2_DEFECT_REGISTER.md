# AURUM DESK V17.8.0.2 — DEFECT REGISTER

## 1. Complete V17.8 / V17.8.0.1 / V17.8.0.2 Defect Register

| Defect ID | Severity | Component | Description | Resolution & Verification Gate | Status |
|---|---|---|---|---|---|
| `DEF-178-07` | P0 | `scalp_m1_model_service.py` / `unicorn_evidence_service.py` | Evaluator-to-detector signature and `selected_fvg` binding contract | Canonical `detect_unicorn_evidence` signature and `selected_fvg_id` check (`G43`) | **CLOSED** |
| `DEF-178-08` | P0 | `stop_placement_policy_service.py` / `main.py` | V17.8 feature flag persistence, CAS version check, reload, and mode separation | Full DB persistence, reload, CAS, and `LIVE` scope rejection (`G44`) | **CLOSED** |
| `DEF-178-09` | P0 | `execution_coordinator.py` / `entry_guard_service.py` | ARM-to-FILL `min_protective_distance` persistence and pre-FILL quote breach check | Persisted in `order_plan_snapshot_service`, re-validated at FILL (`G45`, `G46`) | **CLOSED** |
| `DEF-178-10` | P0 | `unicorn_evidence_service.py` | Causal breaker-to-FVG episode binding and unclosed HTF bar rejection | Enforced causal episode interval and closed-bar cutoffs (`G47`, `G48`) | **CLOSED** |
| `DEF-178-11` | P0 | `test_v17_8_runtime_integration.py` | End-to-end runtime integration coverage across all 25 critical scenarios | 10 comprehensive integration tests covering all 25 scenarios (`G49`) | **CLOSED** |
| `DEF-178-12` | P1 | `SLTPIntelligenceView.tsx` / `App.tsx` | Mounted frontend SL/TP Intelligence UI verification | Verified by Playwright E2E suite `v17_8_0_1_sltp_intelligence.e2e.ts` (`G50`) | **CLOSED** |
| `DEF-178-13` | P1 | `stop_placement_research_service.py` / `main.py` | Honest replay comparison status and Variant D empirical P90 calibration disclosure | Discloses `EXPERIMENTAL_FIXED_MULTIPLIER` and `NO_REPLAY_RESULTS` honestly (`G52`) | **CLOSED** |
| `DEF-178-14` | P1 | `V17_8_STOP_OUT_FORENSIC_AUDIT.md` | Forensic stop-out hold time (`111,597 ms`) and calculator reconciliation | Verified against `calculate_risk_reward` in `test_def_178_14` (`G51`) | **CLOSED** |
| `DEF-178-15` | P0 | `tools/run_v17_8_acceptance.py` / `acceptance_verdict.py` | Expand acceptance runner to 52+ mandatory release gates | Expanded to 58 mandatory release gates (`G01`–`G58`) | **CLOSED** |
| `DEF-178-16` | P0 | `.gitignore` / `docs/artifacts/v17_8_0_2/` / `tools/artifact_verifier.py` | Unpublished acceptance artifact bundle and `.gitignore` exclusion of artifact `.log` files | Un-ignored `!docs/artifacts/v17_8_0_2/**/*.log`, added CLI verifier to `artifact_verifier.py`, executed & published exact-commit bundle `run_v17_8_0_2_1793186387461_14624` (`G58`) | **CLOSED** |
| `DEF-178-17` | P0 | `runtime_profile_service.py` / `database.py` / `main.py` / `crud.py` | Lack of dedicated non-mutating backend observation runtime profile (`OBSERVE_ONLY`) | Implemented 5-layer `AURUM_BE_RUNTIME_PROFILE=OBSERVE_ONLY` isolation (`G53`–`G57`) | **CLOSED** |
