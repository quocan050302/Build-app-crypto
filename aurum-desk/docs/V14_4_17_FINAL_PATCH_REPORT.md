# V14.4.17 Final Patch Report: Strict Repair Provenance, Original Config Preservation, and Asyncio Test Correctness

## 1. Baseline & Environment
- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline HEAD:** `8fb70290c73a2549b1f3a28e2dc48bdd9e399d04` (Runtime implementation `514259a9`)
- **Version:** V14.4.17
- **Target OS / Python:** macOS / Python 3.13.0

---

## 2. Issues Reproductions & Root Causes

### 2.1 Corrupt Payload / Missing Proof Self-Certification
- **Issue:** In `validate_repair_session()`, when `original_damaged_payload` was corrupted JSON (e.g., `"{{invalid"`), the function caught the JSON exception and ignored it. Because `task.forward_session_id` pointed to an existing session in the database, `validate_repair_session()` treated the session's existence as sufficient proof, marking the repair as `REPAIRED` even though no independent binding proof existed.
- **Fix:** Introduced structured provenance parsing (`parse_repair_source_payload`) and resolution (`resolve_repair_provenance`). If damaged payload is malformed or missing and no independent complete proof (`fwd_mem` or `fwd_snap`) is verified, repair strictly transitions to `MANUAL_REVIEW_REQUIRED` with reason codes `REPAIR_PROOF_MALFORMED` or `REPAIR_PROOF_MISSING`.

### 2.2 Hardcoded Config Revision & Sentinel Fallbacks
- **Issue:** `repair_forward_membership_task()` previously hardcoded `config_revision = 1` during reconstructed membership creation. If the original order was bound under `config_revision=7`, repair replaced it with revision 1, destroying configuration history. In addition, missing runtime commits were subject to fallback to current HEAD or loose defaults.
- **Fix:** `resolve_repair_provenance()` extracts and strictly preserves the original `config_revision`, `runtime_commit`, and `bound_at_ms`. Hardcoded defaults (like `1` or `now_ms`) are completely eliminated for historical binding. If original config revision or commit cannot be proven, the task fails closed to `MANUAL_REVIEW_REQUIRED`.

### 2.3 Asyncio Worker Test Parametrization to Trio
- **Issue:** Worker unit tests in `test_v14_4_16_repair_runtime.py` rely on `asyncio` primitives (`asyncio.create_task`, `asyncio.sleep`) matching production FastAPI lifespan execution. AnyIO parametrized these tests across both `asyncio` and `trio`, causing 2 failures under Trio (`test_wrep02` and `test_wrep03`).
- **Fix:** Added module-scoped `@pytest.fixture def anyio_backend(): return "asyncio"` to restrict AnyIO test runner to `asyncio`, strictly aligning with the production runtime architecture.

---

## 3. Implemented Architecture & Proof Rules

### 3.1 Structured Data & Reason Codes
- **Dataclass `RepairProvenanceResolution`:**
  - `status`: `"VERIFIED"` | `"INSUFFICIENT"` | `"CONFLICT"`
  - `forward_session_id`: Optional target forward session ID
  - `config_revision`: Resolved positive integer revision
  - `runtime_commit`: Resolved original SHA commit
  - `bound_at_ms`: Resolved original binding timestamp in ms
  - `source_names`: List of validated source keys (`damaged_payload`, `fwd_mem`, `fwd_snap`, `session_orders_index`)
  - `reason_codes`: Standardized diagnostics tokens (`REPAIR_PROOF_MISSING`, `REPAIR_PROOF_MALFORMED`, `REPAIR_PROOF_INVALID_SCHEMA`, `REPAIR_SESSION_NOT_FOUND`, `REPAIR_SESSION_CONFLICT`, `REPAIR_ORDER_CONFLICT`, `REPAIR_CONFIG_REVISION_MISSING`, `REPAIR_CONFIG_REVISION_CONFLICT`, `REPAIR_RUNTIME_COMMIT_MISSING`, `REPAIR_RUNTIME_COMMIT_CONFLICT`, `REPAIR_BOUND_AT_MISSING`, `REPAIR_BOUND_AT_CONFLICT`)
  - `diagnostics`: Debug dictionary preserving original damaged payloads and contextual metadata.

### 3.2 Provenance Collection & Verification Rules
1. **Target Session Existence:** Validates target session exists via `load_forward_session()`. If not found, fails closed immediately to `MANUAL_REVIEW_REQUIRED`.
2. **Payload Schema & Types:** Pure parsing via `parse_repair_source_payload()`. Rejects booleans disguised as ints (`isinstance(True, int)` protection), non-integers, and negative timestamps.
3. **Multi-Source Cross-Check & Conflict Detection:**
   - Detects mismatches between damaged payload and independent storage records (`fwd_mem`, `fwd_snap`).
   - If task requests session B while original proofs belong to session A, flags `REPAIR_SESSION_CONFLICT` without transferring order.
   - If payload references order Y but task is for order X, flags `REPAIR_ORDER_CONFLICT`.
   - If order is indexed in multiple competing sessions, flags `REPAIR_SESSION_CONFLICT`.
4. **Preservation of Historical Metadata:**
   - `config_revision`: Original positive integer strictly preserved.
   - `runtime_commit`: Original SHA strictly preserved; never substituted with current HEAD or loose sentinels.
   - `bound_at_ms`: Original binding timestamp strictly preserved; never substituted with `opened_at` or current repair time.
5. **Stage-Only Atomic Transactions:**
   - Repair worker transaction owns commit/rollback.
   - Staging functions (`stage_repair_manual_review`, `stage_forward_membership`, `stage_forward_session_order_index`) perform zero commits/rollbacks.

---

## 4. Test Execution & Evidence

### 4.1 New V14.4.17 Behavioral Test Suite (`test_v14_4_17_repair_provenance.py`)
| Case ID | Description | Result |
|---|---|---|
| `P17_01` | Malformed JSON with existing session fails closed | PASS |
| `P17_02` | Missing proof fails closed | PASS |
| `P17_03` | Nonexistent session fails closed | PASS |
| `P17_04` | Session conflict triggers manual review without transfer | PASS |
| `P17_05` | Order ID mismatch triggers manual review | PASS |
| `P17_06` | Preserves original config revision (revision 7 != 1) | PASS |
| `P17_07` | Preserves original runtime commit without HEAD substitution | PASS |
| `P17_08` | Preserves original bound_at_ms distinct from opened_at/now_ms | PASS |
| `P17_09` | Malformed payload with independent complete proof verifies | PASS |
| `P17_10` | Conflicting session indexes trigger manual review | PASS |
| `P17_11` | Missing config_revision in payload fails closed without default 1 | PASS |
| `P17_12` | Schema protection rejects invalid field types (bool, str, negative) | PASS |
| `P17_13` | Valid repair is strictly idempotent across repeated invocations | PASS |
| `P17_14` | Owner commit failure rolls back cleanly without partial persistence | PASS |
| `P17_15` | Manual review commit failure handled safely | PASS |
| `P17_16` | Zero financial mutations on PaperOrder or DomainEvents | PASS |
| `P17_17` | Repaired membership does not forge terminal authority in reports | PASS |
| `P17_18` | Worker process real path continues batch despite malformed task | PASS |

**Result:** 21 passed (including 4 parameterized type tests) in 0.98s. Exit code: 0.

### 4.2 Targeted Backend Acceptance Suite
- **Command:** `pytest <61 targeted test files> -q -p no:cacheprovider`
- **Files executed:** 61 files
- **Total tests passed:** 543 passed, 0 failed
- **Exit code:** 0

### 4.3 Acceptance Runner (`run_v14_4_2_acceptance.py`)
- `backend_targeted`: PASS (543 passed)
- `frontend_typecheck`: PASS
- `frontend_lint`: PASS
- `frontend_vitest`: PASS
- `frontend_build`: PASS
- `git_diff_check`: PASS
- `browser_e2e`: INCOMPLETE (flagged `--skip-e2e` per targeted scope)

---

## 5. Scope Invariants & Boundaries
- **Trading & Lifecycle Invariants Preserved:**
  - SMC detection, entry, arm, fill, TP, and SL logic: Unchanged.
  - Session policies (Asia, London, NY): Unchanged.
  - Daily quota, NY reserve, risk sizing, leverage, and margin formulas: Unchanged.
  - Real-time websocket feed, charting layout, and Telegram delivery: Unchanged.
- **Frontend Code:** No modifications to frontend code or dependencies.
- **Database Safety:** All tests executed against isolated test databases (`TESTING=1`, `AURUM_DB_PATH=:memory:`); production database untouched.

---

## 6. What Was Tested vs Not Tested
- **Verified by Automated Tests:**
  - Strict provenance validation for malformed JSON, missing proof, and schema mismatch.
  - Non-mutation during failure modes (`MANUAL_REVIEW_REQUIRED`).
  - Strict retention of `config_revision`, `runtime_commit`, and `bound_at_ms`.
  - Atomicity and rollback on database commit failures.
  - Worker fair keyset batch processing on real production execution paths.
  - Terminal event authority preservation in reports and exports.
  - Scoped asyncio execution of background repair worker tests.
- **Not Tested (Explicit Out of Scope):**
  - Full browser Playwright E2E suite (skipped via `--skip-e2e` as patch is strictly backend metadata).
  - Live mainnet trading or real financial transactions (strictly paper/reconciliation).
