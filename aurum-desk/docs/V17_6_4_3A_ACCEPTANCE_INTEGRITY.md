# AURUM V17.6.4.3a Acceptance Integrity Specification

## Executive Summary
Prior acceptance runs had two critical testing loopholes (**AC43A-05, AC43A-06**):
1. **Permissive Test Discovery**: `pytest --collect-only` returning exit code 2 (e.g. collection error or syntax issue) could still pass if partial tests were parsed.
2. **Ignored Skips**: Suites could pass with unexpected test skips (`skipped > 0`), hiding unexecuted tests or missing dependencies.

**Master Prompt V17.6.4.3a** establishes strict acceptance integrity enforced by `tools/run_v17_6_4_3a_acceptance.py`.

---

## Strict Verification Gates (G00 – G31)

| Gate | Name | Target Function / Assertion | Requirement |
|---|---|---|---|
| `G00` | Clean Branch & Source | Git worktree & branch check | Clean tree, branch `fix/v14-1-rr-flow` |
| `G01` | Borrowed Flush Returns PENDING_COMMIT | `ObservationIntervalService._persist_interval_record` | Status must be `PENDING_COMMIT` |
| `G02` | Outer Transaction Commit Acknowledgement | Outer commit confirmation | Row durably present after commit |
| `G03` | Rollback Clears Uncommitted Rows | Transaction rollback | 0 rows in independent session |
| `G04` | No Premature Business Commit | Outer `PaperOrder` rollback | Business order never committed prematurely |
| `G05` | Pending Interval Acknowledgement Integrity | `persist_pending_intervals` | Kept in queue until outer commit |
| `G06` | SQLite Concurrent Writer & Retry | Multi-connection SQLite | Handled cleanly with retry |
| `G07` | Forged Typed GOOD Rejected | `record_engine_heartbeat` | Forged dataclass rejected as `UNVERIFIED` |
| `G08` | Forged Dictionary GOOD Rejected | `record_engine_heartbeat` | Forged dict rejected as `UNVERIFIED` |
| `G09` | Metadata-Only GOOD Rejected | `record_engine_heartbeat` | Boolean flags alone cannot authorize GOOD |
| `G10` | Valid Canonical GOOD Accepted | Canonical quote validation | Valid Bitget WS quote authorizes GOOD |
| `G11` | Real Watcher Quote Authority | `ScalpWatcherOrchestrator.evaluate_cycle` | Real watcher produces canonical interval |
| `G12` | Real Scheduler Quote Authority | Scheduler cadence integration | Real scheduler records canonical interval |
| `G13` | Replay / Test Separation | `evidence_origin` filtering | REPLAY/TEST never update LIVE uptime |
| `G14` | Database A/B Pending Isolation | Multi-DB pending queues | Pending failure in DB A does not pollute DB B |
| `G15` | Database A/B Health Isolation | Multi-DB health snapshots | Engine health scoped strictly to target DB |
| `G16` | Run ID Isolation | Multi-run scoping | Different run IDs within same DB remain isolated |
| `G17` | Current Evidence & Durable Commit Agreement | Snapshot persistence status | Dashboard & Engine Health agree on status |
| `G18` | Strict Backend Discovery Exit Code | `run_backend_discovery` | `code == 0` AND `collected > 0` AND no errors |
| `G19` | Unexpected Skip Rejection | `run_cmd` skip detection | Any unexpected skip immediately fails gate |
| `G20` | Full Backend Test Execution | `pytest backend/tests -q` | 100% pass across entire test suite |
| `G21` | Frontend Vitest | `npm test -- --run` | All unit/component tests pass |
| `G22` | Frontend Build | `npm run build` | Production Vite build succeeds |
| `G23` | Frontend Lint | `npm run lint` | Zero lint errors |
| `G24` | Playwright E2E | Browser E2E suite | Real observation dashboard flows pass |
| `G25` | SQLite Restart / Readback | Engine restart readback | Persisted rows recovered faithfully |
| `G26` | Deterministic Observation Soak | Multi-cycle observation harness | Zero drift, zero memory leak across cycles |
| `G27` | Behavioral Strategy Parity | SMC/ICT setup evaluation | Identical geometry and decision parity |
| `G28` | Zero LIVE Order Runtime Verification | Exchange order interceptor | `LIVE_EXCHANGE_ORDERS_SENT == 0` |
| `G29` | Acceptance Runner Negative Tests | `test_v17_6_4_3a_acceptance_integrity.py` | All 16 runner failure modes verified |
| `G30` | Clean Source After Testing | Post-test git status | Zero dirty/untracked files |
| `G31` | Source SHA & Artifact Reconciliation | Pre/post SHA and log integrity | Exact SHA match, all evidence logs hashed |

---

## Verdict Formulation
A release verdict of `ACCEPTED` strictly requires:
```python
accepted = (
    release_mode
    and correct_branch
    and source_clean_before
    and source_clean_after
    and source_sha_unchanged
    and all_required_gates_present
    and all_required_gates_executed
    and all_required_gates_passed
    and not has_unexpected_skips
)
```
Any unexpected skip, missing gate, nonzero exit code, or dirty source file results in immediate **`REJECTED`** verdict.
