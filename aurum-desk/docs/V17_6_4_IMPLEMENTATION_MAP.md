# BẢN ĐỒ KIẾN TRÚC & PHẠM VI TRIỂN KHAI AURUM V17.6.4
## AURUM DESK — PRODUCTION OBSERVABILITY INTEGRITY & RUN-TIME CORRECTNESS
### Phiên bản: V17.6.4 | Nhánh: fix/v14-1-rr-flow | Baseline HEAD: 34e722848ae6d6414290c223156ec98e59db3a1d

---

### 1. SƠ ĐỒ LUỒNG DỮ LIỆU SẢN XUẤT (PRODUCTION DATA LIFECYCLE)

```
Bitget Market Feed (WebSocket/REST) [24/7 Perpetual Contract]
    │
    ▼
Market Calendar Service (Phân biệt: Lịch Contract 24/7 vs Phiên Gold CME vs Cửa Sổ Chiến Lược AURUM)
    │
    ▼
Scalp Watcher Loop / CG01 Scheduler (Arbitration quyền sở hữu: WATCHER vs SCHEDULER)
    │
    ▼
evaluate_cycle(db, symbol, research_context, ...)
    ├── 1. Xác định chính xác run_id (từ research_context hoặc active-run resolver)
    ├── 2. Đánh giá SMC/ICT (BOS, CHoCH, FVG, POI, Sweep, Net R:R, Guards)
    ├── 3. Cam kết transaction kinh doanh: db.commit() hoặc db.rollback()
    ├── 4. Ghi nhận Telemetry Chu Kỳ (persist_evaluation_cycle_with_owned_session trên bind của db)
    └── 5. Cập nhật Engine Heartbeat (rõ ràng vai trò WATCHER / SCHEDULER)
          │
          ▼
Observation Interval Producer (Ghép cặp heartbeat thành các khoảng quan sát GOOD/DEGRADED/DOWN vào DB)
    │
    ▼
Observation Dashboard API (/observation/summary, /status)
    │ (Tính toán Coverage trên mẫu số 24/7 giao cắt với các khoảng interval thực tế trong DB)
    ▼
React Dashboard (ObservationDashboard.tsx)
    [Hiển thị trung thực: NOT_EVALUATED thay vì NO_SETUP giả, N/A thay vì $1,000 ảo]
```

---

### 2. MA TRẬN CÁC HÀM THAY ĐỔI (FUNCTION MODIFICATION MATRIX)

| Tệp Tin | Hàm / Lớp | Hành Vi Trước (V17.6.3) | Hành Vi Sau (V17.6.4) | Lý Do Kỹ Thuật | Kiểm Thử Bắt Buộc |
|:---|:---|:---|:---|:---|:---|
| `backend/services/market_calendar_service.py` | `get_contract_trading_availability`, `get_underlying_reference_session`, `get_strategy_execution_session` | Chưa có service tách biệt 3 loại lịch | Module chuẩn hóa phân tách rõ: (1) Lịch Bitget XAUUSDT perpetual 24/7, (2) Phiên tham chiếu vàng CME, (3) Cửa sổ chiến lược NY | Tách biệt trạng thái thị trường và quyền vào lệnh chiến lược | `test_v17_6_4_contract_calendar.py` (CAL01–12) |
| `backend/services/engine_observability_service.py` | `build_market_open_interval_ranges` | Chỉ tính giờ New York (trừ cuối tuần và bảo trì) | Trả về khoảng thời gian đầy đủ 24/7 cho Bitget XAUUSDT perpetual | Mẫu số tính Uptime phản ánh đúng tính chất hợp đồng phái sinh 24/7 | `test_v17_6_4_24_7_coverage.py` (CV01–18) |
| `backend/services/engine_observability_service.py` | `calculate_observation_coverage` | Sử dụng lịch tham chiếu vàng đóng cuối tuần | Sử dụng mẫu số 24/7, giao cắt với khoảng quan sát `PaperObservationInterval` thực tế trong DB; fail-closed khi thiếu run | Độ bao phủ toán học chính xác 100%, không bịa đặt 100% khi bot offline cuối tuần | `test_v17_6_4_24_7_coverage.py` |
| `backend/services/engine_observability_service.py` | `build_market_feed_health_snapshot` | Chỉ trả về 1 cờ `is_market_closed` | Bổ sung các trường phân tách: `contract_schedule`, `contract_operational_state`, `underlying_reference_state`, `strategy_session_state` | Frontend và API hiển thị rõ ràng từng khía cạnh trạng thái | `test_v17_6_4_contract_calendar.py` |
| `backend/services/engine_observability_service.py` | `record_evaluation_cycle` & `persist_evaluation_cycle_with_owned_session` | Mở `database.SessionLocal()` toàn cục khi `db=None` | Hỗ trợ trích xuất `bind` từ `db` caller (`sessionmaker(bind=db.get_bind())`) để commit độc lập mà không rò rỉ sang DB toàn cục | Bảo vệ cô lập DB giữa Test, Replay và Live, giải quyết triệt để lỗi rò rỉ DB | `test_v17_6_4_telemetry_database_isolation.py` (DB01–12) |
| `backend/services/scalp_watcher_orchestrator.py` | `evaluate_cycle` | Gọi telemetry với `db=None` và không truyền `run_id` | Truyền `db` của caller để trích xuất engine context; xác định và truyền `run_id=effective_run_id` cho mọi nhánh kết quả | Đảm bảo chu kỳ gắn với đúng `run_id` và đúng database context của caller | `test_v17_6_4_cycle_run_identity.py` (RID01–12) |
| `backend/services/observation_interval_service.py` | `ObservationIntervalService` | Chưa có producer tự động ghi `PaperObservationInterval` | Xây dựng producer chuyên trách: gom các heartbeat liên tiếp hợp lệ thành các khoảng `GOOD`/`DEGRADED`/`DOWN` và lưu vào DB | Cung cấp dữ liệu đầu vào bền vững cho thuật toán tính toán độ bao phủ | `test_v17_6_4_interval_producer.py` (IP01–16) |
| `backend/services/engine_observability_service.py` | `get_latest_persisted_cycle` | Ưu tiên RAM buffer không đối chiếu chặt chẽ `run_id` | Truy vấn DB có lọc chính xác `run_id` và `symbol`; RAM buffer chỉ trả về nếu khớp danh tính và hợp lệ | Khắc phục hiển thị sai chu kỳ của run khác | `test_v17_6_4_cycle_run_identity.py` |
| `frontend/src/components/ObservationDashboard.tsx` | UI rendering | `latest_cycle?.decision_status \|\| 'NO_SETUP'` | Hiển thị `NOT_EVALUATED` khi không có chu kỳ, `N/A` khi không có dữ liệu tài chính hoặc run not found | Tránh hiển thị sai lệch đánh lừa người dùng | `v17_6_4_observation_truth.test.tsx` (UI01–12) |
| `backend/main.py` | `/observation/status`, `/observation/summary` | Phản hồi thiếu nhất quán khi `run_id` không tồn tại | Trả về phản hồi rõ ràng `status: "NOT_FOUND"`, các trường run-specific là `null` | Đảm bảo tính nhất quán của hợp đồng API | `test_v17_6_4_observation_api.py` (API01–10) |
| `tools/run_v17_6_4_acceptance.py` | Master Runner | Chưa tích hợp kiểm tra 27 gates toàn diện V17.6.4 | Kiểm tra toàn bộ 27 gates: Git clean, Market calendar, Run identity, DB isolation, Interval producer, Coverage math, Parity, Frontend, E2E | Cung cấp chứng chỉ nghiệm thu release đáng tin cậy 100% | Full execution |
