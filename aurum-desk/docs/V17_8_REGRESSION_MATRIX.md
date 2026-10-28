# AURUM DESK V17.8 — FULL REGRESSION TEST MATRIX (GATES G01 TO G42)

## 1. Overview
The Aurum Desk V17.8 release acceptance framework expands the 28 gates of V17.7 to 42 mandatory release gates. Every gate must achieve a verified `PASS` status with machine-readable evidence on the exact final commit before release approval.

---

## 2. Gate Definitions & Verification Commands

| Gate ID | Gate Name | Subsystem / Scope | Verification Target / Command |
|---|---|---|---|
| **G01** | `G01_BRANCH_INTEGRITY` | Git Ancestry | `git branch --show-current == feat/v17-8...` & `merge-base HEAD origin/master` |
| **G02** | `G02_TEST_DB_BOOTSTRAP` | DB Safety | `backend/tests/test_v17_7_database_safety.py` (test_dbs_01, test_dbs_09) |
| **G03** | `G03_UNSAFE_PATH_REJECTION` | DB Safety | `backend/tests/test_v17_7_database_safety.py` (test_dbs_02..10) |
| **G04** | `G04_DESTRUCTIVE_FIXTURE_ISOLATION` | DB Safety | `backend/tests/test_v17_7_database_safety.py` (test_dbs_11..15) |
| **G05** | `G05_RAW_ATR_PROVENANCE` | Stop Placement | `backend/tests/test_v17_7_atr_provenance.py` (8 tests) |
| **G06** | `G06_REQUIRED_M1_ATR_FAIL_CLOSED` | Stop Placement | `backend/tests/test_v17_7_atr_provenance.py` (7 tests) |
| **G07** | `G07_ATR_BUFFER_MATH_CORRECTNESS` | Stop Placement | `backend/tests/test_v17_7_atr_provenance.py` (3 tests) |
| **G08** | `G08_STRUCTURAL_ANCHOR_CAUSAL` | Stop Placement | `backend/tests/test_v17_7_adaptive_stop_loss.py` (4 tests) |
| **G09** | `G09_DISTANT_SL_FINANCIAL_INTEGRITY` | Stop Placement | `backend/tests/test_v17_7_adaptive_stop_loss.py` (4 tests) |
| **G10** | `G10_CG01_EVALUATOR_INTEGRATION` | Evaluator | `backend/tests/test_v17_7_adaptive_stop_loss.py` (3 tests) |
| **G11** | `G11_M1_EVALUATOR_INTEGRATION` | Evaluator | `backend/tests/test_v17_7_adaptive_stop_loss.py` (7 tests) |
| **G12** | `G12_ORCHESTRATOR_REAL_ARM` | Orchestrator | `backend/tests/test_v17_7_adaptive_stop_loss.py` (2 tests) |
| **G13** | `G13_REAL_ARM_CONCURRENCY` | Concurrency | `backend/tests/test_v17_7_adaptive_stop_loss.py` (3 tests) |
| **G14** | `G14_FILL_TIME_FINANCIAL_INVARIANTS` | Fill Guards | `backend/tests/test_v17_7_adaptive_stop_loss.py` (2 tests) |
| **G15** | `G15_PAPER_EXIT_AND_PERSISTENCE` | Paper Exit | `backend/tests/test_v17_7_adaptive_stop_loss.py` (4 tests) |
| **G16** | `G16_LIVE_ORDER_SAFETY_BOUNDARY` | Safety | Active HTTP boundary spy + 3 negative control tests |
| **G17** | `G17_EXISTING_SL_DSL_CE_REGRESSION` | V17.7 Suite | `backend/tests/test_v17_7_adaptive_stop_loss.py` (100 tests) |
| **G18** | `G18_V17_5_REGRESSION` | V17.5 Suite | `backend/tests/test_v17_5*.py` (200+ tests) |
| **G19** | `G19_V17_6_REGRESSION` | V17.6 Suite | `backend/tests/test_v17_6*.py` (300+ tests) |
| **G20** | `G20_FULL_BACKEND_PYTEST` | Full Backend | `python -m pytest backend/tests` (3000+ tests) |
| **G21** | `G21_FRONTEND_VITEST` | Frontend Tests | `npm test -- --run` in `frontend/` |
| **G22** | `G22_FRONTEND_BUILD` | Frontend Build | `npm run build` in `frontend/` |
| **G23** | `G23_FRONTEND_LINT` | Frontend Lint | `npx oxlint` in `frontend/` |
| **G24** | `G24_PLAYWRIGHT_BROWSER_E2E` | Browser E2E | `npx playwright test tests/e2e/v17_5_3_stop_browser.e2e.ts` |
| **G25** | `G25_DETERMINISTIC_REPLAY` | Replay | `backend/tests/test_v17_7_adaptive_stop_loss.py` (2 tests) |
| **G26** | `G26_ACCEPTANCE_RUNNER_NEGATIVE_TESTS` | Negative Tests | `backend/tests/test_v17_7_adaptive_stop_loss.py` (test_ce_19) |
| **G27** | `G27_ARTIFACT_SOURCE_SHA_INTEGRITY` | Integrity | `backend/tests/test_v17_7_adaptive_stop_loss.py` (test_ce_20) |
| **G28** | `G28_DOCS_DEFECT_REGISTER_CONSISTENCY` | Documentation | 14 required V17.8 docs verified non-empty |
| **G29** | `G29_UNICORN_BREAKER_DETECTION` | Unicorn Engine | `backend/tests/test_v17_8_unicorn_evidence.py` (2 tests) |
| **G30** | `G30_UNICORN_FVG_OVERLAP` | Unicorn Confluence | `backend/tests/test_v17_8_unicorn_evidence.py` (2 tests) |
| **G31** | `G31_CROSS_TIMEFRAME_CAUSALITY` | Causality | `backend/tests/test_v17_8_unicorn_evidence.py` (2 tests) |
| **G32** | `G32_STRUCTURAL_INVALIDATION_AUTHORITY` | Invalidation Authority | `backend/tests/test_v17_8_structural_sl.py` (test_unicorn_distal) |
| **G33** | `G33_MODEL_SPECIFIC_SL_SELECTION` | Model SL Logic | `backend/tests/test_v17_8_structural_sl.py` (test_fvg_origin_fallback) |
| **G34** | `G34_ATR_SPREAD_NOISE_PROTECTION` | Volatility Buffer | `backend/tests/test_v17_8_quote_noise_guard.py` (2 tests) |
| **G35** | `G35_LIQUIDITY_TP_INDEPENDENCE` | TP Independence | `backend/tests/test_v17_8_structural_sl.py` (test_fail_closed_wide_sl) |
| **G36** | `G36_ENTRY_QUOTE_PROXIMITY` | Quote Distance Guard | `backend/tests/test_v17_8_quote_noise_guard.py` (3 tests) |
| **G37** | `G37_RISK_BUDGET_AND_NET_RR` | Risk Conservation | `backend/tests/test_v17_8_structural_sl.py` (2 tests) |
| **G38** | `G38_COMPLETE_ARM_FILL_EXIT` | Order Lifecycle | `backend/tests/test_v17_7_adaptive_stop_loss.py` (6 tests) |
| **G39** | `G39_HISTORICAL_REPLAY_DETERMINISM` | Replay Harness | `backend/tests/test_v17_8_replay_comparison.py` (3 tests) |
| **G40** | `G40_FEATURE_FLAG_AND_LEGACY_PARITY` | Feature Flags | `backend/tests/test_v17_7_adaptive_stop_loss.py` (4 tests) |
| **G41** | `G41_NEGATIVE_CONTROLS_AND_ANTI_FABRICATION` | Negative Controls | `backend/tests/test_v17_8_quote_noise_guard.py` & replay tests (3 tests) |
| **G42** | `G42_EXACT_SOURCE_AND_ARTIFACT_INTEGRITY` | Exact Commit | Run-scoped bundle leaf verification & unmutated composite SHA |
