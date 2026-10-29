# AURUM DESK V17.9.0 — Checkpoint and Resume Verification Report

- **Release Version**: `V17.9.0`
- **Branch**: `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Tested Executable Source SHA (`FINAL_CODE_SHA`)**: `76f725a232ab3bda5459b40efad95a08dfca7fd1`
- **Module**: `tools/v17_9_execution_state.py`

---

## 1. Atomic Execution State Persistence

`ExecutionStateManager` (`tools/v17_9_execution_state.py`) persists checkpoint state to `docs/artifacts/v17_9_0/<RUN_ID>/execution_state.json` via atomic temp-file write + `os.replace()`:
- Records `run_id`, `source_sha`, `status`, `completed_batches`, `failed_batches`, `pending_batches`, and `batch_records` (with `exit_code`, `junit_xml_path`, `junit_xml_sha256`, `passed`, `failed`, `skipped`, `errors`, `duration_ms`).

---

## 2. Fail-Closed Resumption & Invalidation Rules

When resuming from a prior `execution_state.json`, `can_reuse_batch(batch_id)` enforces four strict invariants:
1. **Source SHA Match**: `state["source_sha"] == current_source_sha`. If `source_sha` changed, `validate_for_resume()` invalidates all prior checkpoints (`SOURCE_SHA_MISMATCH`) and clears `completed_batches`.
2. **Exit Code Zero & Zero Failures**: `rec["exit_code"] == 0`, `rec["failed"] == 0`, `rec["errors"] == 0`, and `rec["passed"] > 0`.
3. **JUnit XML Existence & SHA-256 Match**: The referenced `junit_xml_path` must exist on disk, be non-empty, and match the recorded `junit_xml_sha256`.
4. **Well-Formed JUnit XML**: `ET.parse(xml_path)` must succeed with zero `<failure>` or `<error>` elements.

---

## 3. Verification Evidence (`G103_CHECKPOINT_RESUME_AND_INVALIDATION`)

- **`test_37_checkpoint_resume_reuses_valid_batch_and_invalidates_on_sha_mismatch`**: Verified that a completed batch with valid JUnit XML is reused when `source_sha` matches, and invalidated immediately when `source_sha` changes.
- **`test_38_checkpoint_invalidated_when_junit_xml_corrupt_or_missing`**: Verified that corrupting or deleting the JUnit XML causes `can_reuse_batch()` to return `(False, "JUNIT_XML_HASH_MISMATCH")` / `(False, "JUNIT_XML_MISSING_OR_EMPTY")`.

