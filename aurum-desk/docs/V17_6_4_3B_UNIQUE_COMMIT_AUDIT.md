# AURUM V17.6.4.3b — UNIQUE CONFLICT DURABILITY & READBACK AUDIT
**Version:** V17.6.4.3b  
**Scope:** Borrowed Session Conflict Verification & Independent Readback Probe  
**Auditor:** Principal Database & Transaction Integrity Specialist  

---

## 1. Problem Statement & Defect CFP-07

In earlier versions of `ObservationIntervalService._persist_interval_record()`, when an integrity or unique constraint violation occurred on a borrowed database session (`borrow_session=True`), the method immediately returned:
```python
return IntervalPersistenceResult(status="ALREADY_EXISTS")
```
This assumed that the unique constraint failure was caused by a prior committed record with identical logical identity. However, in SQLite and SQLAlchemy:
1. An uncommitted duplicate inserted earlier in the *same uncommitted outer transaction* will trigger a uniqueness conflict upon `flush()`.
2. If the outer transaction later rolls back, the record was never durable!
3. By returning `ALREADY_EXISTS` prematurely, the pending queue evicted the interval, leading to lost observation interval coverage.

---

## 2. Technical Remediation & Independent Probe Verification

In V17.6.4.3b:
When an `IntegrityError` or unique constraint occurs on a borrowed session:
1. The service executes an **independent readback probe** using a separate, non-borrowed connection.
2. The probe searches for an existing durable row matching `run_id`, `symbol`, `scope`, `worker_role`, `evidence_origin`, `mode`, and `interval_identity` or matching start/end bounds.
3. **If and only if** the committed row exists in the independent probe:
   - It returns `status="ALREADY_EXISTS"`, safe to evict.
4. **If the row is NOT found** in the independent probe (i.e. uncommitted collision in borrowed session or lock failure):
   - It returns `status="RETRYABLE_FAILURE"`, `error_code="UNVERIFIED_UNIQUE_CONFLICT"`, `retryable=True`.
   - The interval remains in the pending queue (`SEALED_PENDING`) and is retried on the next flush cycle once the outer transaction commits or rolls back.

---

## 3. Verified Verification Matrix (UC01 – UC16)

All 16 invariants (`UC01` through `UC16`) are actively verified by `backend/tests/test_v17_6_4_3b_unique_commit_truth.py` with 100% pass rate.
