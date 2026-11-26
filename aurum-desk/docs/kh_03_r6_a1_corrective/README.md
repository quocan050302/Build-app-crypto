# AURUM DESK — KH-03.R6.A1 Corrective Revision (`P1-L` / `P1-M` / `P1-N`)

- **Scope:** Post-review corrective closure inside `KH-03.R6.A1` (OFFLINE / QA LOOPBACK ONLY; external market capture remains hard-blocked `BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED`).
- **Reviewed Baseline Tested Executable:** `133980ac9e28e663df8284617bc4306791a0f86a` (published HEAD `ff69871d466d0df9925e62f6dd336d633a369714`).
- **Branch:** `feature/v17-9-2-structural-sl-tp-from-qa`

## Root-Cause Closures
1. **P1-L — Custody Single-Writer, Determinism & Atomicity:**
   - Replaced concurrent direct `R6CustodyJournal.persist_r6_verified_chunk()` calls from `ThreadPoolExecutor` workers with a bounded, backpressure-enforced, anti-starvation ordered producer-consumer queue (`R6A1BoundedOrderedCustodyQueue`) drained strictly by a single custody writer authority (`R6A1CustodyWriterAuthority` + OS-level `R6A1CrossProcessCustodyLock`).
   - Full transaction lock (`_tx_lock` + OS store lock) across head read, sequence allocation, `prev_hash`, duplicate checks, byte budget checks, durable commit intent (`intents/`), atomic staging + `fsync` + `os.replace`, ordered JSONL chain append + `fsync`, anchor sync, and `recover_r6_custody_intents()`.
   - Separated deterministic content identity (`semantic_chain_digest`) from nondeterministic audit fields (`child_pid`, timestamps) so `W1 / W2 / W4 / W1-repeat` produce byte-identical logical custody order & hashes regardless of transport completion order.
2. **P1-M — Hard Disk Budget, Atomic Reservations & No Over-Allocation:**
   - Removed `max(65_536, ...)` and `max(262_144, ...)` cap enlargement in `run_r6_a1_acquisition_session()`.
   - Implemented strict integer `min(...)` cap intersection (`compute_effective_disk_budget_contract()`) across signed grant, immutable plan, policy ceilings, and verified target volume headroom.
   - Added atomic durable cross-process/cross-thread `R6A1DiskBudgetLedger` enforcing `committed + active_reserved + uncertain <= cap` on BOTH session and global volume scopes, accounting for raw wire, normalized UTF-8 JSON, staging overlap, 4 KiB filesystem allocation slack, intent/journal/anchor, SQLite WAL/SHM, manifests, logs, and pre-reserved emergency audit budget.
3. **P1-N — Exception-Safe Claim, Finalization & Quarantine:**
   - Moved `claim_r6_grant_session_atomic()` INSIDE guarded lifecycle (`try / except BaseException / finally`) with durable pre-claim reconciliation (`reconcile_r6_a1_claim_after_exception`) so post-commit/pre-return claim exceptions are reconciled and aborted once rather than leaking `CLAIMED` grants.
   - Isolated every finalizer substep (`finalize_r6_a1_session_safely()`) with independent error capture so telemetry/reporting exceptions in `finally:` NEVER skip ledger finalization; preserved original error separately from cleanup errors; added compare-and-set monotonic terminal transition + DB readback verification + fail-closed `STORE_UNPROVEN_TERMINAL` marker; enforced `derive_r6_a1_terminal_disposition()` separating durable ledger state from acquisition success.

## Documents
- [SPEC_KH_03_R6_A1_CORRECTIVE_v1.0.md](SPEC_KH_03_R6_A1_CORRECTIVE_v1.0.md)
- [FUNCTION_OWNERSHIP_MATRIX.md](FUNCTION_OWNERSHIP_MATRIX.md)
