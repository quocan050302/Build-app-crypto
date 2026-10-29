# AURUM DESK V17.9.0 — Disk Budget and Resource Safety Report

- **Release Version**: `V17.9.0`
- **Branch**: `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Tested Executable Source SHA (`FINAL_CODE_SHA`)**: `76f725a232ab3bda5459b40efad95a08dfca7fd1`
- **Module**: `tools/test_resource_guard.py`
- **Disk Safety Verdict**: `DISK_BUDGET_AND_CLEANUP_VERIFIED`

---

## 1. Disk Budget Architecture

`TestResourceGuard` (`tools/test_resource_guard.py`) prevents disk exhaustion during parallel regression and acceptance runs:
- **Minimum Free Disk Threshold**: `MIN_FREE_DISK_GB = 3.0` GB (`3,221,225,472` bytes).
- **Maximum Run Artifact Growth Budget**: `MAX_RUN_DISK_USAGE_GB = 1.5` GB (`1,610,612,736` bytes).
- **Maximum Uncompressed Single Log Size**: `MAX_SINGLE_LOG_MB = 25.0` MB (`26,214,400` bytes).
- **Preflight & Runtime Enforcement**:
  - `preflight_check()` verifies available disk space before launching any worker subprocess.
  - `check_runtime_budget()` samples free disk space and run-directory byte growth after every batch; if free space drops below `MIN_FREE_DISK_GB` or run usage exceeds `MAX_RUN_DISK_USAGE_GB`, it raises `DiskBudgetExceededError("BLOCKED_DISK_BUDGET: ...")`.

---

## 2. Scoped Worker Temp Directory Cleanup & Log Compression

1. **Worker Temp Directory Management**:
   - All temporary worker databases and pytest basetemp directories are created under `tmp/v17_9_0/<RUN_ID>/worker_<id>`.
   - `cleanup_worker_temp_dir(worker_id)` removes each worker's directory immediately upon batch completion (`try ... finally`).
   - `cleanup_run_temp_dir()` removes `tmp/v17_9_0/<RUN_ID>/` at run completion and records `temp_directories_cleaned_up: true` in `disk_budget_report.json`.
2. **Streaming Log Compression**:
   - `enforce_log_size_limit(log_path)` checks completed log files against `max_single_log_bytes`.
   - Any log exceeding the threshold is compressed to `<log>.gz`, a 200-line `<log>.tail.log` is retained for quick inspection, and the uncompressed SHA-256 is recorded.

---

## 3. Verification Evidence (`G102_DISK_BUDGET_GUARD_AND_TEMP_CLEANUP`)

- **`test_34_disk_budget_guard_blocks_when_free_space_below_threshold`**: Verified `DiskBudgetExceededError` with `BLOCKED_DISK_BUDGET` when `min_free_disk_gb` exceeds available disk space.
- **`test_35_worker_temp_directory_cleanup_after_completion`**: Verified `create_worker_temp_dir` -> `cleanup_worker_temp_dir` -> `cleanup_run_temp_dir` leaves zero residual files on disk.
- **`test_36_large_log_compression_and_hash_preservation`**: Verified automatic `.gz` compression, `.tail.log` creation, and SHA-256 preservation.

