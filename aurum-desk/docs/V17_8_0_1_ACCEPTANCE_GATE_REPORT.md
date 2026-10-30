# AURUM DESK V17.8.0.1 — 52-GATE ACCEPTANCE REPORT (`DEF-178-15`)

**Defect ID:** `DEF-178-15`  
**Runner:** `tools/run_v17_8_acceptance.py`  
**Verdict Engine:** `tools/acceptance_verdict.py`  
**Artifact Bundle Directory:** `docs/artifacts/v17_8_0_1/<RUN_ID>/`

---

## 1. Acceptance Architecture Upgrade (`DEF-178-15`)

To prevent runtime integration, persistence, UI mounting, or forensic discrepancies from escaping release verification, `tools/acceptance_verdict.py` and `tools/run_v17_8_acceptance.py` were extended from 42 gates (`G01`–`G42`) to **52 mandatory gates (`G01`–`G52`)**:

1. **Retained Gates `G01`–`G42`:** All V17.7.4 and V17.8.0 gates remain mandatory and fail-closed.
2. **Upgraded `G28_DOCS_DEFECT_REGISTER_CONSISTENCY`:** Verifies all 14 `V17_8_*.md` documents plus all 12 `V17_8_0_1_*.md` documents (26 total documents, each >= 200 bytes).
3. **Upgraded `G38_COMPLETE_ARM_FILL_EXIT`:** Executes both `backend/tests/test_v17_7_adaptive_stop_loss.py` and `backend/tests/test_v17_8_runtime_integration.py` (`min_passed=7`).
4. **Added V17.8.0.1 Mandatory Gates (`G43`–`G52`):**
   - `G43_UNICORN_RUNTIME_INTERFACE_CONTRACT`: Verifies `detect_unicorn_evidence` canonical `selected_fvg` contract through `ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate()` + negative control (`min_passed=2`).
   - `G44_FEATURE_FLAG_PERSISTENCE_AND_RELOAD`: Verifies `v18_*` flags DB persistence, reload, CAS, no-op, strict boolean validation, LIVE rejection, and `PAPER_STRUCTURAL` mode separation (`min_passed=2`).
   - `G45_FILL_TIME_PROTECTIVE_DISTANCE_GUARD`: Verifies `min_protective_distance` persisted at ARM and enforced at FILL in `ExecutionCoordinator` (`min_passed=3`).
   - `G46_FILL_TIME_STOP_BREACH_QUOTE_GUARD`: Verifies `INVALID_GEOMETRY_STOP_BREACHED` at ARM and FILL when executable quote breaches SL (`min_passed=3`).
   - `G47_CAUSAL_UNICORN_EPISODE_BINDING`: Verifies causal episode binding (`_is_same_causal_episode`), pullback OB ordering after broken swing, and full provenance IDs (`min_passed=1`).
   - `G48_ANTI_LOOKAHEAD_AND_UNCLOSED_CANDLE_REJECTION`: Verifies future timestamp rejection, unclosed HTF bar rejection, consumed FVG rejection, and `invalidation_price=None` on invalid Unicorn evidence (`min_passed=1`).
   - `G49_END_TO_END_RUNTIME_INTEGRATION`: Verifies all 12 runtime scenarios (`CANDIDATE -> ARM -> FILL -> CLOSE -> /stop-evidence`) (`min_passed=1`).
   - `G50_FRONTEND_MOUNTED_SLTP_INTELLIGENCE_VIEW_E2E`: Executes real Chromium Playwright E2E suite `tests/e2e/v17_8_0_1_sltp_intelligence.e2e.ts` against the mounted `SLTPIntelligenceView` in the Journal Drawer (`min_passed=2`).
   - `G51_FORENSIC_AUDIT_TRUTH_RECONCILIATION`: Verifies exact hold duration (`111,597 ms`) and authoritative `calculate_risk_reward` figures for the forensic trade (`min_passed=1`).
   - `G52_HONEST_REPLAY_COMPARISON_DISCLOSURE`: Verifies `EXPERIMENTAL_FIXED_MULTIPLIER` vs `EMPIRICAL_P90_NOISE_CALIBRATION` and honest `NO_REPLAY_RESULTS` / `INSUFFICIENT_REPLAY_DATA` endpoint statuses (`min_passed=1`).
5. **Non-Destructive Artifact Isolation:** V17.8.0.1 bundles are written exclusively under `docs/artifacts/v17_8_0_1/<RUN_ID>/`, preserving historical `docs/artifacts/v17_8/` bundles untouched.

