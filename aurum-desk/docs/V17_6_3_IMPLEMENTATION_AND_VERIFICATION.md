# AURUM V17.6.3 — Production Persistence & Coverage Verification

## Scope and status

This is a **candidate patch**, not an ACCEPTED release. It is intentionally
isolated on `fix/v17-6-3-production-persistence-coverage`, based on
`fix/v14-1-rr-flow`. No LIVE exchange execution changes were intended.

## Implementation

- **Watcher persistence:** Calls to `record_engine_heartbeat()` and
  `record_evaluation_cycle()` in `ScalpWatcherOrchestrator.evaluate_cycle()`
  and `run_watcher_loop()` now pass `db=None`. This uses an independently
  owned and committed telemetry session rather than a borrowed business
  transaction that is closed without a telemetry commit. Validate SQLite lock
  behavior and concurrent scheduler ownership in an integrated environment.
- **Coverage:** `build_market_open_interval_ranges()` segments the configured
  New York market schedule at exact UTC-hour boundaries for the existing
  daily/weekend-hour configuration. GOOD, DEGRADED, and DOWN observation
  intervals are intersected with those eligible ranges. DOWN takes priority
  over DEGRADED, and DEGRADED over GOOD. Remaining time is UNVERIFIED.
- **Restart readback:** Dashboard latest-cycle lookup uses
  `get_latest_persisted_cycle()`, with run and symbol identity checks, rather
  than only a process-local ring buffer.
- **Missing run:** Financial metrics for a non-existent observation run are
  nullable instead of a fictional verified $1,000 balance.
- **Timezone:** `getTodayMidnightMs()` resolves the selected timezone's
  calendar midnight rather than subtracting elapsed local clock seconds on
  23/25-hour DST transition dates.

## Added regression cases

- `backend/tests/test_v17_6_3_production_persistence.py` calls an actual
  `ScalpWatcherOrchestrator.evaluate_cycle()` disabled path with a disk-backed
  SQLite database, closes the caller session without committing, and performs
  a fresh-session readback.
- `backend/tests/test_v17_6_3_market_eligible_coverage.py` covers GOOD data
  in a closed market, GOOD/DEGRADED overlap, and both NY DST seasons.
- `frontend/src/__tests__/v17_6_3_dst_midnight.test.ts` verifies midnight
  at the spring-forward and fall-back dates.
- `tools/run_v17_6_3_acceptance.py` runs full backend tests, frontend Vitest,
  build, lint and Playwright. It parses actual test counts and stores logs
  outside the Git worktree. It refuses to mark a dirty or unverified source as
  accepted.

## Required validation before merge

Run in a clean local checkout with backend/frontend dependencies installed:

```bash
cd aurum-desk
python tools/run_v17_6_3_acceptance.py
```

The runner prints the exact path of an externally stored `acceptance.json`
and returns nonzero unless every mandatory gate passes.

Additionally validate on the app's **actual configured database** (not a
test fixture) with safe PAPER/SHADOW observation:
1. A completed or skipped watcher cycle remains visible after process restart.
2. The business transaction cannot be committed early by telemetry.
3. A configured New York weekend supplies no fraudulent healthy time.
4. Incident and observation-run identities remain separate.
5. The frontend displays `N/A` instead of inventing financial evidence.
6. No LIVE exchange orders are submitted.

## Limits

The configured trading calendar may not cover every Bitget holiday or
exception; provider-specific calendar verification is still necessary.
The added unit tests do not prove uninterrupted 7–14 day PAPER uptime.
At time of authoring, **no full regression execution was performed by the
assistant in the application's runtime environment**; do not mark this
candidate RELEASE ACCEPTED without the above evidence.
