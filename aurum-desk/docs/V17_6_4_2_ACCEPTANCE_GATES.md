# AURUM DESK — V17.6.4.2 ACCEPTANCE GATES SPECIFICATION
## 32-Gate Master Production Acceptance Suite (Section 12 / Part 147–153)

**Document Version:** 1.0.0  
**Target Release:** V17.6.4.2  
**Baseline SHA:** `f033d4aa69aef03901ab672173b24c8df236b35e`  
**Classification:** Release Quality Standard · Acceptance Specifications · Continuous Verification

---

### 1. Architectural Philosophy & Rules of Engagement

The V17.6.4.2 Acceptance Runner enforces 32 mandatory gates ($G00$ to $G31$). Release acceptance guarantees that AURUM DESK is mathematically sound, free of strategy drift, incapable of dispatching unauthorized live exchange orders, and reliably captures all observation telemetry on disk.

#### Non-Negotiable Gate Rules

1. **No Soft PASS (**PART 149**)**:
   Any gate returning `SKIPPED`, `BLOCKED`, `NOT_EXECUTED`, or `NO_TESTS` disqualifies release acceptance. `status` must be strictly `"PASS"`.
2. **Execution Proof & JUnit Verification (**PART 111 & 112**)**:
   Every test gate must execute a real test process, record its command, duration, and output tail, write an external log, compute an evidence sha256 checksum, and produce a valid JUnit XML report with `test_count > 0` and `failed == 0`.
3. **Clean Worktree & Unchanged SHA (**PART 107 & 152**)**:
   Release mode requires a clean working tree before tests ($G00$) and after tests ($G31$) on branch `fix/v14-1-rr-flow`. Any dirty tracked or untracked file produces `REJECTED`.
4. **Developer Mode Isolation (**PART 108 & 116**)**:
   When run with `--mode dev` or `--allow-dirty`, the runner can never issue `ACCEPTED`. Its verdict is strictly `DEVELOPMENT_TEST_ONLY`.

---

### 2. Complete 32-Gate Master Gate Directory

| Gate | Gate Name | Subsystem / Defect | Test Files & Commands | Mandatory Pass Criteria |
|---|---|---|---|---|
| **G00** | `G00_clean_source` | Git Hygiene | `git branch`, `git status --porcelain` | Branch is `fix/v14-1-rr-flow`; clean working tree. |
| **G01** | `G01_rotation_failure_durability` | ECI-01 (Rotation queue) | `pytest test_v17_6_4_2_pending_interval_commit.py -k test_pi01..04` | Sealed pending interval state machine survives rotation. |
| **G02** | `G02_checkpoint_failure_durability` | ECI-01 (Checkpoint durability) | `pytest test_v17_6_4_1_interval_durability.py` | Mid-interval checkpoints persist without losing active state. |
| **G03** | `G03_quality_transition_failure_durability` | ECI-01 (Quality rotation) | `pytest test_v17_6_4_1_quality_transitions.py` | Quality changes rotate active interval into pending queue. |
| **G04** | `G04_pending_retry_idempotency` | ECI-04 / ECI-12 / ECI-13 | `pytest test_v17_6_4_2_pending_interval_commit.py` | Pending queue idempotency, backoff, and retry metrics. |
| **G05** | `G05_sqlite_concurrent_writer_safety` | Concurrency isolation | `pytest test_v17_6_4_telemetry_database_isolation.py` | SQLite WAL multi-session concurrent write resilience. |
| **G06** | `G06_feed_timestamp_authority` | ECI-02 (Timestamp authority) | `pytest test_v17_6_4_2_quote_authority.py` | Reconciles quote age, timestamps, and crossed books. |
| **G07** | `G07_feed_transport_source_authority` | ECI-02 (Transport authority) | `pytest test_v17_6_4_1_feed_evidence_truth.py` | Disconnected or unknown transport state fails closed. |
| **G08** | `G08_metadata_good_bypass_prevention` | ECI-08 (Anti-bypass) | `pytest test_v17_6_4_2_metadata_good_bypass.py` | Metadata flags cannot manufacture GOOD quality. |
| **G09** | `G09_cycle_commit_truth` | ECI-07 (Cycle commit) | `pytest test_v17_6_4_2_cycle_commit_truth.py` | Session borrowing yields PENDING_COMMIT; owned yields COMMITTED. |
| **G10** | `G10_dashboard_persistence_truth` | ECI-03 (Persistence truth) | `pytest test_v17_6_4_2_persistence_status.py` | Unverified/degraded persistence surfaced authoritatively. |
| **G11** | `G11_latest_persisted_cycle_truth` | ECI-06 (No RAM fallback) | `pytest test_v17_6_4_1_cycle_commit_consistency.py` | `get_latest_persisted_cycle` returns strictly DB rows. |
| **G12** | `G12_replay_test_isolation` | ECI-09 (Replay isolation) | `pytest test_v17_6_4_1_origin_isolation.py` | REPLAY and BACKTEST telemetry strictly separated from LIVE. |
| **G13** | `G13_run_identity_isolation` | Multi-run isolation | `pytest test_v17_6_4_cycle_run_identity.py` | RUN_A cannot overwrite or merge with RUN_B data. |
| **G14** | `G14_coverage_mathematics` | 24/7 Coverage ledger | `pytest test_v17_6_4_24_7_coverage.py` | Continuous observation hours and coverage percentage. |
| **G15** | `G15_engine_liveness` | Process monitoring | `pytest test_v17_6_engine_heartbeat.py` | Heartbeat cadence, process liveness vs evaluation health. |
| **G16** | `G16_bitget_perpetual_market_calendar` | 24/7 Perpetual calendar | `pytest test_v17_6_4_contract_calendar.py` | Bitget Gold continuous hours (DST, weekend, underlying ref). |
| **G17** | `G17_actual_watcher_integration` | ECI-09 (Watcher integration) | `pytest test_v17_6_4_2_watcher_integration.py` | Exercises actual `ScalpWatcherOrchestrator.evaluate_cycle()`. |
| **G18** | `G18_actual_scheduler_integration` | Interval producer | `pytest test_v17_6_4_interval_producer.py` | Cadence loop, scheduled flushing, background tasks. |
| **G19** | `G19_crash_restart_readback` | Crash recovery | `pytest test_v17_6_4_1_real_db_integration.py` | Disk database readback matches state across process restart. |
| **G20** | `G20_api_contract` | HTTP Endpoint schema | `pytest test_v17_6_4_observation_api.py` | FastAPI observation summary, dashboard, and health routes. |
| **G21** | `G21_frontend_unit_tests` | ECI-03 UI | `npm test -- src/__tests__/v17_6_4_2_observation_status.test.tsx ...` | Banner renders on UNVERIFIED & DEGRADED; badge styled. |
| **G22** | `G22_full_backend_tests` | Full regression | `pytest backend/tests -q` | Complete backend test suite (240+ tests) 100% passing. |
| **G23** | `G23_full_frontend_vitest` | Full regression | `npm test -- --run` | Complete frontend Vitest suite (548+ tests) 100% passing. |
| **G24** | `G24_frontend_build` | Production bundle | `npm run build` | Vite production bundle compiles with zero errors. |
| **G25** | `G25_frontend_lint` | Code hygiene | `npm run lint` | Oxlint / ESLint passes with zero syntax or type errors. |
| **G26** | `G26_playwright_e2e` | E2E Browser acceptance | `npx playwright test v17_6_observation.e2e.ts` | Real headless browser verifies 5 cards, panels, timelines. |
| **G27** | `G27_deterministic_soak` | Long-run stability | `pytest test_v17_6_soak.py` | Multi-cycle deterministic soak with zero leaks or stalls. |
| **G28** | `G28_behavioral_strategy_parity` | ECI-10 (SMC Strategy Parity) | `pytest test_v17_6_4_2_paper_parity.py -k test_sp` | Strict fixture match for Entry, SL, TP, Net R:R, position size. |
| **G29** | `G29_zero_live_order_runtime_verification` | ECI-11 (Zero Live Orders) | `pytest test_v17_6_4_2_paper_parity.py -k test_zo` | Mock transport asserts `LIVE_EXCHANGE_ORDERS_SENT == 0`. |
| **G30** | `G30_acceptance_runner_negative_tests` | ECI-04 / ECI-05 (Runner Integrity) | `pytest test_v17_6_4_2_acceptance_integrity.py` | AC01–AC16 negative tests prove runner cannot falsely accept. |
| **G31** | `G31_source_sha_and_artifact_integrity` | ECI-14 (Artifact Integrity) | Git SHA match & artifact sha256 checksums | Pre/post SHA match; worktree clean; all 30 evidence logs intact. |
