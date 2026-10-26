# BẢN ĐỒ KIẾN TRÚC & PHẠM VI TRIỂN KHAI AURUM V17.6.2
## OBSERVABILITY INTEGRITY, TRANSACTION SAFETY & CALL GRAPH MAP

**Hệ thống**: AURUM DESK — XAUUSDT SMC/ICT Paper Trading Engine  
**Phiên bản**: V17.6.2  
**Baseline SHA**: `b2fe799583a5d33fae530970c187bfa3f7426440` (V17.6.1 Release)  

---

## I. PRODUCTION CALL GRAPH & TRANSACTION BOUNDARIES

```text
[Watchdog / Worker Loop] (Background Thread)
    |
    +-- ScalpWatcherOrchestrator.evaluate_cycle(db, symbol, ...)
           |
           +-- record_engine_heartbeat(db, state="RUNNING", ...) [BORROWED SESSION: FLUSH ONLY, NO COMMIT]
           |
           +-- Strategy Evaluation & Candidate Generation
           |
           +-- Risk Guards & Invariant Validation (validate_arm_invariants)
           |
           +-- _arm_paper_candidate() -> Creates/Updates PaperOrder
           |
           +-- db.commit() [BUSINESS TRANSACTION COMMIT BY ORCHESTRATOR OWNER]
           |
           +-- record_evaluation_cycle(db=None / owned session) [ISOLATED TELEMETRY COMMIT]
                  |
                  +-- EngineEvaluationCycleRecord (Durable cycle history in DB)
                  +-- Update in-memory ring buffer

[Observation REST API] (FastAPI Routes in main.py)
    |
    +-- GET /api/v1/observation/snapshot
    |      |
    |      +-- EngineObservabilityService.build_observation_dashboard_snapshot(db) [STRICT READ-ONLY]
    |             |
    |             +-- build_engine_health_snapshot() -> Separate Process Liveness & Market State
    |             +-- build_market_feed_health_snapshot() -> Canonical Quote & Sorted Candle Validity
    |             +-- calculate_observation_coverage() -> Overlap Query & Merged Intervals (No fake fallback)
    |             +-- OpportunityFunnelService.summarize_opportunity_funnel() -> Strict Run Isolation
    |             +-- list_recent_decisions() -> Symbol Verified & Safe Cursor Validation (422 on error)
    |             +-- get_latest_waiting_setups() -> Active WAITING only (Filters out ARMED/FILLED/EXPIRED)
    |             +-- extract_financial_summary_from_run() -> Authoritative PaperResearchOrderLink (No leakage)
    |             +-- list_active_engine_incidents() -> DB Persisted Active Incidents (Preserves History)
    |
    +-- GET /api/v1/observation/decisions
           |
           +-- list_recent_decisions(db, symbol, cursor) -> Validates cursor; 422 if invalid

[Frontend Realtime React Dashboard]
    |
    +-- ObservationDashboard.tsx
    |      |
    |      +-- useObservationDashboard(symbol, runId, windowHours, timezone)
    |             |
    |             +-- Timezone-Aware Today Midnight (America/New_York vs Asia/Ho_Chi_Minh)
    |             +-- WebSocket Event Listener (wsClient)
    |             |      |
    |             |      +-- extractObservationEventType(raw) -> Unwraps DOMAIN_EVENT envelopes
    |             |      +-- Debounced Refresh (300ms) with Run ID Awareness
    |             |
    |             +-- Request Generation Token -> Prevents async race conditions from stale responses
```

---

## II. QUY TẮC PHÂN ĐỊNH QUYỀN SỞ HỮU SESSION (SESSION OWNERSHIP CONTRACT)

| Hàm | Loại Session | Hành Vi Được Phép | Hành Vi Bị Cấm |
|---|---|---|---|
| `record_engine_heartbeat(db, ...)` | **Borrowed** (khi `db` được truyền) | Add / Flush model | **TUYỆT ĐỐI KHÔNG `commit()`, `rollback()`, `close()`** |
| `persist_engine_heartbeat(...)` | **Owned** (tự mở qua `SessionLocal`) | Commit, Rollback on error, Close | Không chia sẻ session cho luồng khác |
| `record_engine_incident(db, ...)` | **Borrowed** (khi `db` được truyền) | Add / Flush model | **TUYỆT ĐỐI KHÔNG `commit()`, `rollback()`, `close()`** |
| `resolve_engine_incident(db, ...)` | **Borrowed** (khi `db` được truyền) | Query / Update / Flush | **TUYỆT ĐỐI KHÔNG `commit()`, `rollback()`, `close()`** |
| `resolve_active_incidents(db, ...)` | **Borrowed** (khi `db` được truyền) | Query / Bulk Update / Flush | **TUYỆT ĐỐI KHÔNG `commit()`, `rollback()`, `close()`** |
| `record_evaluation_cycle(...)` | **Owned** (hoặc `db=None`) | Lưu durable cycle vào DB, commit độc lập | Không rollback transaction của nghiệp vụ |

---

## III. MA TRẬN FILE SỬA ĐỔI VÀ BẢO VỆ INVARIANTS

1. `backend/models.py`:
   - Bổ sung model `EngineEvaluationCycleRecord` lưu trữ lịch sử chu kỳ đánh giá (idempotent qua `cycle_id` duy nhất).
2. `backend/services/engine_observability_service.py`:
   - Sửa `record_engine_heartbeat`: Session ownership compliance (chỉ flush khi borrowed, commit khi owned).
   - Thêm `persist_engine_heartbeat` cho việc lưu độc lập.
   - Sửa `record_engine_incident`, `resolve_engine_incident`, `resolve_active_incidents`: Chỉ commit khi session owned.
   - Sửa `record_evaluation_cycle`: Lưu vào `EngineEvaluationCycleRecord` bền vững trong DB và in-memory buffer.
   - Sửa `detect_stalled_engine`: Phân tách độc lập `process_state` và `market_state`.
   - Sửa `build_market_feed_health_snapshot`: Kiểm tra tính hợp lệ toàn diện của báo giá và sắp xếp nến đóng.
   - Sửa `calculate_observation_coverage`: Xử lý giao thoa khoảng thời gian (overlap), clipping, merge intervals; loại bỏ fake `cycles * cadence` fallback.
   - Sửa `build_observation_dashboard_snapshot`:
     - Lọc `waiting_setups` theo latest state per opportunity.
     - Khóa hoàn toàn unscoped financial fallback; chỉ dùng `PaperResearchOrderLink`.
     - Lấy incidents từ DB `EngineIncidentRecord.status == 'ACTIVE'`.
   - Sửa `list_recent_decisions` & cursor decoding: Trả về lỗi 422 rõ ràng cho cursor sai lệch.
3. `backend/services/scalp_watcher_orchestrator.py`:
   - Đảm bảo transaction boundaries: telemetry không commit đè lên business session.
4. `frontend/src/hooks/useObservationDashboard.ts`:
   - Bóc tách sự kiện `DOMAIN_EVENT` lồng nhau trong `extractObservationEventType`.
   - Tính toán Today midnight chuẩn theo timezone NY/VN.
   - Thêm request cancellation token chống race condition khi chuyển run hoặc window.
5. `tools/run_v17_6_2_acceptance.py`:
   - Runner kiểm tra nghiêm ngặt 100% mandatory gates, xác thực git clean và source SHA.
