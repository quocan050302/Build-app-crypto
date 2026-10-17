# V16.25 CG-01 Paper Trading Operational Guide

## Verifying Telemetry Worker Status
1. **Worker Ready Verification**:
   ```python
   from services.cg01_telemetry_service import start_trace_persistence_worker
   ready = await start_trace_persistence_worker(timeout_s=3.0)
   assert ready is True
   ```
2. **Drain and Queue Accounting Verification**:
   ```python
   from services.cg01_telemetry_service import drain_trace_persistence, get_outstanding_receipt_count, get_active_operations_count
   drain_result = await drain_trace_persistence(timeout_s=5.0)
   assert drain_result["status"] == "COMPLETED"
   assert get_outstanding_receipt_count() == 0
   assert get_active_operations_count() == 0
   ```
3. **Shutdown Verification**:
   ```python
   from services.cg01_telemetry_service import stop_trace_persistence_worker, wait_trace_persistence_worker_stopped
   stop_trace_persistence_worker()
   stopped = await wait_trace_persistence_worker_stopped(timeout_s=3.0)
   assert stopped is True
   ```
