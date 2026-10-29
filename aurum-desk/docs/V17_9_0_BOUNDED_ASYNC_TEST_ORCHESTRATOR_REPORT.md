# AURUM DESK V17.9.0 — Bounded Asynchronous Test Orchestrator Report

- **Release Version**: `V17.9.0`
- **Branch**: `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Tested Executable Source SHA (`FINAL_CODE_SHA`)**: `76f725a232ab3bda5459b40efad95a08dfca7fd1`
- **Module**: `tools/run_v17_9_async_regression.py`
- **Async Test Infrastructure Verdict**: `BOUNDED_ASYNC_EXECUTION_VERIFIED`

---

## 1. Bounded Concurrency Design

`BoundedAsyncTestOrchestrator` (`tools/run_v17_9_async_regression.py`) executes independent pytest batches concurrently using `asyncio.create_subprocess_exec` guarded by `asyncio.Semaphore(max_workers)`:
- **Worker Formula**: `MAX_PYTEST_WORKERS = min(4, max(2, CPU_COUNT // 2))`
- **Per-Worker Isolation**:
  - Dedicated `AURUM_TEST_RUN_ID = <run_id>_w<worker_idx>_<batch_id>`
  - Dedicated `AURUM_TEST_ROOT = tmp/v17_9_0/<RUN_ID>/worker_<worker_idx>_<batch_id>/test_root`
  - Dedicated `--basetemp = tmp/v17_9_0/<RUN_ID>/worker_<worker_idx>_<batch_id>/basetemp`
  - Dedicated JUnit XML output (`--junitxml=...`) and log file (`logs/<batch_id>.log`).

---

## 2. Node Inventory Accounting & Anti-Skip Verification

For every batch executed by `BoundedAsyncTestOrchestrator`:
1. `collect_expected_node_ids()` runs `pytest --collect-only -q` to obtain the exact set of expected test node IDs before execution.
2. After subprocess termination, `_parse_junit_xml_inventory()` parses the batch JUnit XML and verifies:
   $$\text{expected\_nodes} = \text{completed\_nodes} + \text{skipped\_nodes} + \text{failed\_nodes}$$
   $$\text{unaccounted\_nodes} = \text{expected\_nodes} - (\text{completed\_nodes} \cup \text{skipped\_nodes} \cup \text{failed\_nodes}) = \emptyset$$
3. Any non-zero exit code, failed testcase, error testcase, or non-empty `unaccounted_nodes` marks the batch and overall run as `FAIL`.

---

## 3. Verification Evidence (`G101_BOUNDED_ASYNC_TEST_RUNNER_INTEGRITY`)

- **`test_31_bounded_async_test_orchestrator_parallel_batches`**: Verified 2 parallel batches execute with `max_workers=2`, `unaccounted_nodes_count == 0`, and `orphan_processes_remaining == 0`.
- **`test_32_bounded_async_runner_detects_failed_test_in_worker`**: Verified that a failing test inside a worker subprocess causes `status == "FAIL"` and records the exact failed node ID.
- **`test_33_bounded_async_runner_detects_missing_unaccounted_node`**: Verified that an incomplete/truncated JUnit XML missing a collected test node triggers `unaccounted_nodes_count > 0` and fails closed.

