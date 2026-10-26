# V14.4.20 Final Patch Report
## Prevent Copied Repaired Data From Becoming Original Binding Proof

### 1. Executive Summary & Baseline
- **Target Repository:** `quocan050302/Build-app-crypto`
- **Target Branch:** `fix/v14-1-rr-flow`
- **Reviewed Baseline Head:** `4d9bb7de` (V14.4.19, 63 targeted files, 585 passed)
- **Patch Version:** V14.4.20
- **Scope:** Correct classification of repair binding provenance sources across staging copies, ensuring that copying a repaired membership record into `original_damaged_payload` or legacy snapshots never transforms repaired data into an independent original binding proof.

---

### 2. Reproduction of the Vulnerability (Before vs After)

#### Production Staging Path (Before):
1. An existing forward membership record in the DB had repair markers (e.g. `metadata.repaired_by = "ForwardMembershipRepairTask"`).
2. `stage_forward_repair_task(db, order_id)` was called without explicitly supplying `original_damaged_payload`.
3. Per design to preserve raw historical evidence, `stage_forward_repair_task()` copied `rec_mem.value` into `task.original_damaged_payload`.
4. `collect_repair_provenance_sources()` unconditionally tagged `original_damaged_payload` with `kind = "ORIGINAL_BINDING"`.
5. `is_complete_original_binding_proof()` checked only `source.kind != "REPAIRED_MEMBERSHIP"`, so the copied repaired payload was deemed an independent complete original proof.
6. `resolve_repair_provenance()` returned `status = "VERIFIED"`, allowing circular self-proof simply because the repaired data was duplicated into `original_damaged_payload`.

#### Production Staging Path (After V14.4.20):
1. An existing forward membership record has repair markers.
2. `stage_forward_repair_task()` preserves raw evidence by storing the raw membership string in `task.original_damaged_payload`.
3. `collect_repair_provenance_sources()` parses `task.original_damaged_payload` and invokes the shared pure classifier `classify_repair_binding_payload_origin()`.
4. The classifier detects the repair markers (`repaired_by`, `metadata.repaired_by`, `metadata.repair_task_id`, or `metadata.provenance_sources`) and assigns `kind = "REPAIRED_MEMBERSHIP"`.
5. `is_complete_original_binding_proof()` rejects `dp_source` both because `source.kind == "REPAIRED_MEMBERSHIP"` and defense-in-depth inspection of `source.payload`.
6. `resolve_repair_provenance()` evaluates all repaired sources (`[s for s in [db_mem_source, dp_source, db_snap_source] if s.kind == "REPAIRED_MEMBERSHIP"]`) and confirms there is no independent original proof.
7. Resolution returns `status = "INSUFFICIENT"`, `reason_codes = ["REPAIR_SOURCE_UNVERIFIED"]`.
8. `repair_forward_membership_task()` marks the task `MANUAL_REVIEW_REQUIRED` without any financial or state mutations.

---

### 3. Implementation Details

#### Shared Origin Classifier:
Added pure helper `classify_repair_binding_payload_origin(payload, default_kind="ORIGINAL_BINDING") -> str` in [forward_paper_service.py](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/forward_paper_service.py):
- **Contract Markers Recognized:**
  - Top-level `repaired_by` (legacy support).
  - Nested `metadata.repaired_by`.
  - Nested `metadata.repair_task_id`.
  - Nested `metadata.provenance_sources`.
- **Pure Function Guarantees:**
  - Zero database interactions, zero network calls.
  - Zero mutations on input dictionaries.
  - Gracefully handles `None`, non-dictionary metadata, and unexpected data types without exceptions.
  - Preserves legitimate original memberships (does not misclassify standard metadata like `config_revision`, `runtime_commit`, `bound_at_ms`).

#### Universal Source Origin Classification:
Updated `collect_repair_provenance_sources()`:
- `dp_source`: Classified using `classify_repair_binding_payload_origin(dp_data, default_kind="ORIGINAL_BINDING")`.
- `db_mem_source`: Classified using `classify_repair_binding_payload_origin(db_mem_data, default_kind="ORIGINAL_BINDING")`.
- `db_snap_source`: Classified using `classify_repair_binding_payload_origin(db_snap_data, default_kind="LEGACY_BINDING")`.

#### Defense in Depth in Eligibility Helper:
In `is_complete_original_binding_proof(source, validation, order)`:
- Added check `classify_repair_binding_payload_origin(source.payload) == "REPAIRED_MEMBERSHIP"`.
- Prevents any caller or mislabeled source object from treating repaired content as original authority.

#### Closed Repaired Source Self-Proof Gate:
In `resolve_repair_provenance()`:
- Evaluates all candidate sources marked `REPAIRED_MEMBERSHIP`:
  ```python
  repaired_sources = [s for s in [db_mem_source, dp_source, db_snap_source] if s.kind == "REPAIRED_MEMBERSHIP"]
  ```
- If any repaired source exists and there is no genuine independent original proof (complete, valid original membership, original damaged payload, or independent legacy snapshot), resolution fails closed immediately with `status = "INSUFFICIENT"` and `reason_codes = ["REPAIR_SOURCE_UNVERIFIED"]`.
- Closes the edge case where DB membership was deleted or missing after copying, preventing `original_damaged_payload` from standing alone as an unverified authority.

---

### 4. Test Verification & Results

#### New Test Suite:
`backend/tests/test_v14_4_20_copied_repair_provenance.py` covering Cases C20-01 through C20-23:
- **C20-01:** Copied repaired membership staging fails closed (persisted -> reloaded -> resolved -> manual review).
- **C20-02:** Top-level `repaired_by` marker via staging fails closed.
- **C20-03:** `metadata.repair_task_id` marker via staging fails closed.
- **C20-04:** `metadata.provenance_sources` marker via staging fails closed.
- **C20-05:** Membership deleted from DB after copy fails closed (missing DB membership loophole closed).
- **C20-06:** All sources repaired (damaged payload + DB membership + snapshot) fails closed.
- **C20-07:** Real genuine original membership copied via staging correctly succeeds.
- **C20-08:** Real genuine original payload with repaired DB membership correctly succeeds.
- **C20-09:** Repaired payload with genuine independent legacy snapshot succeeds.
- **C20-10:** Repaired legacy snapshot alone fails closed.
- **C20-11:** Partial original payload fails closed (regression V14.4.19 preserved).
- **C20-12:** Empty `{}` payload fails closed (regression V14.4.19 preserved).
- **C20-13:** Original vs repaired source conflict fails closed.
- **C20-14:** Index conflict across sessions fails closed (regression V14.4.18 preserved).
- **C20-15:** Order ID mismatch in payload fails closed.
- **C20-16:** Existing task restage preserves raw evidence while failing closed on repaired copy.
- **C20-17:** JSON roundtrip classification parity across serialize, persist, and reload.
- **C20-18:** Malformed metadata (None, string, list, non-repair dict) does not crash or falsely flag.
- **C20-19:** Zero financial mutations on `PaperOrder` or `DomainEvents` upon failed repair.
- **C20-20:** `ForwardRepairWorker.process_once()` batch path handles both copied self-proof and valid tasks without crashing.
- **C20-21:** Transaction owner commit failure cleanly rolls back without partial DB state.
- **C20-22:** Idempotent re-execution of failed tasks.
- **C20-23:** Classifier helper purity (input dictionary deep-copy invariance).

#### Test Run Summary:
1. **Isolated New Suite:**
   ```bash
   PYTHONPATH=backend ./backend/venv/bin/python -m pytest backend/tests/test_v14_4_20_copied_repair_provenance.py -v
   ```
   **Result:** 26 passed, 0 failed (1.16s, Exit code 0).

2. **Full Targeted Backend Suite (64 files):**
   ```bash
   PYTHONPATH=backend ./backend/venv/bin/python -m pytest <64 targeted files> -q -p no:cacheprovider
   ```
   **Result:** 611 passed, 0 failed, 2 warnings in 29.00s (Exit code 0).
   *(Baseline was 585 passed across 63 files; +26 passed = 611 passed).*

---

### 5. Invariants & Scope Verification
- **Trading Flow:** Unchanged. SMC detection, scenario generation, setup qualification, auto-arm, entry, fill, close, SL/TP, risk sizing, R:R calculation, session policy, NY reserve, and daily limits remain untouched.
- **Guards:** Unchanged. News guards, lesson guards, account guards, quote/feed guards, cost guards, and risk guards remain active.
- **UI / Web Services:** No frontend modifications. Chart, sidebar, journal UI, Telegram alerts, and WebSocket feeds remain untouched.
- **Database Isolation:** All tests run exclusively against in-memory SQLite (`:memory:`), never mutating live databases or settings.
- **Raw Evidence:** Unaltered. Original payload strings remain bit-for-bit preserved in storage.

---

### 6. Untested Areas & External Boundaries
- Physical Bitget live exchange REST/WebSocket order fills (intentionally excluded; paper trading simulation only).
- Live Telegram bot network dispatch (mocked in tests per isolation policy).
