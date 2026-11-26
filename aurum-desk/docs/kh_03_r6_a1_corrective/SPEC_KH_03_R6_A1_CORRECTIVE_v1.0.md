# SPEC: KH-03.R6.A1-CORRECTIVE-v1.0 (P1-L / P1-M / P1-N)

- **Precedence:** Supplements `SPEC_KH_03_R6_A1_v1.0.md` specifically to close post-review findings `P1-L`, `P1-M`, and `P1-N` without relaxing any existing security gate or modifying protected strategy/production/dataset/OOS boundaries.
- **Mode:** OFFLINE / QA LOOPBACK ONLY (`A1_EXTERNAL_CAPTURE_ENABLED = False`).

## Documented Acyclic Lock Hierarchy (Zero Network I/O While Holding Locks)
1. **Level 1 — `session_lifecycle_lock`:** Guards `cancel_epoch`, intake close, and finalization ownership.
2. **Level 2 — `custody_writer_lock`:** Guards `R6CustodyJournal._tx_lock` + `R6A1CrossProcessCustodyLock` (`.custody_writer.oslock` + `writer.lock.json`).
3. **Level 3 — `disk_budget_ledger_lock`:** Guards `R6A1DiskBudgetLedger` OS lock + SQLite `BEGIN IMMEDIATE` transaction.
4. **Level 4 — `grant_ledger_lock`:** Guards `R6DurableGrantLedger._anchor_lock` + SQLite `BEGIN IMMEDIATE` transaction.

**Invariant:** No thread/process may acquire lock Level `i` while holding Level `j > i`. Network transport (`supervise_r6_session`) and subprocess waits NEVER execute while holding Levels 2, 3, or 4.

## Mandatory Corrective Gates (`C01..C12` — Separate from 36 Original A1 Gates & 0/4 External R6.B/C/D)
- `C01_BASELINE_RED_REPRODUCTION`: Real isolated baseline RED on `133980ac9e28e663df8284617bc4306791a0f86a` (`RED-L1`, `RED-M1`, `RED-N1a`, `RED-N1b` + positive control).
- `C02_SINGLE_WRITER_ATOMICITY_RESTART`: Full transaction lock, OS cross-process writer ownership, unauthorized direct caller denial, durable intent crash recovery & no concurrent seal.
- `C03_DETERMINISTIC_W1_W2_W4_CUSTODY`: Shuffled `W1/W2/W4` completions yield identical logical custody order, `semantic_chain_digest`, and conserved byte totals.
- `C04_BOUNDED_QUEUE_BACKPRESSURE_NO_LATE_WRITE`: Item + byte bounded queue, reserved head-slot anti-starvation, producer-error abort/drain without deadlock or late write, forged/expired envelope denial.
- `C05_HARD_CAP_MIN_INTERSECTION_NO_ENLARGEMENT`: Removal of `max()` enlargement, strict `min()` cap intersection, strict integer validation (rejects bool/float/NaN/neg/overflow).
- `C06_ATOMIC_DISK_RESERVATIONS_PEAK_OVERHEAD`: Pre-materialization atomic reservations covering raw + normalized UTF-8 + staging + intent/journal/anchor + WAL/manifest/logs + emergency reserve; uncertain bytes retained on failure/crash.
- `C07_REAL_TELEMETRY_PRESSURE_CLEANUP_ENOSPC`: Safe ENOSPC/pressure fail-closed, volume/symlink/junction substitution rejection, verified run-owned temp cleanup, stale/NaN telemetry rejection, `<=4` descendants, `0` orphans.
- `C08_CLAIM_EXCEPTION_DURABLE_TERMINAL_RECONCILE`: Pre-claim exception = 0 consumption; post-claim/pre-return exception reconciled & aborted once; CAS idempotent finalizer; `UNPROVEN_TERMINAL` fail-closed store marker.
- `C09_FINALIZER_ISOLATION_CANCEL_OWNED_REAP`: Telemetry failure in `finally:` never skips ledger finalize; original vs cleanup errors preserved separately; `KeyboardInterrupt`/cancel cleaned & propagated; unreaped worker quarantines & hard-blocks store.
- `C10_COMPOUND_CRASH_QUARANTINE_REPLAY_NO_SEAL`: Compound `L+M+N` fault, restart one-shot replay prevention, durable `FINALIZED` after `ABORTED` != acquisition PASS, positive QA E2E chain verified.
- `C11_INDEPENDENT_VERIFIER_MUTATIONS_INHERITED`: Separate-OS verifier (`verifier_pid != orchestrator_pid`), `>=18` new corrective mutations + `201` inherited mutations, `41` new `L/M/N` pairs + `55` A1 + `56` R6 + `91` C1/C2 + `288` inherited regression + exact full pytest JUnit bijection.
- `C12_PROTECTED_SOURCE_PARITY_FROZEN_REMOTE`: Zero protected/locked file drift, `0` OOS reads/evals, `0` external market requests, full A1/SI8/adapter/D4 & R4 1x/6-cost/E1-E3 parity, frozen `tested_executable_sha` + verified live HTTPS remote SHA-256 readback.
