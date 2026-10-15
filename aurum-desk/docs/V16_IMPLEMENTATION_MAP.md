# V16 Implementation Map: SMC Liquidity Opportunity Engine

Repository: `quocan050302/Build-app-crypto`  
Branch: `fix/v14-1-rr-flow`  
Baseline: `58db1560390b64f8679de768c502d7152eb2b766`  
Scope: SMC Multi-Zone Opportunity Engine, Causal Liquidity Clusters, 2 New Executable Scalp Models, Daily Frequency Goal Evaluator (2-3 fills, hard cap 3), Bitget Public Depth Shadow Layer, Chart Primitive Coordinate Guards.

---

## 1. Mục tiêu & Các nguyên tắc bất biến (Invariants)

1. **Daily Goal & Hard Daily Cap (Rules 028-030, 126-140)**:
   - Mục tiêu: Hướng tới 2–3 filled paper trades mỗi ngày giao dịch đủ điều kiện.
   - Giới hạn cứng: Tuyệt đối không bao giờ vượt quá **3 fills/ngày**.
   - Bảo lưu slot cho NY session: Nếu chưa có fill nào trong phiên NY (`ny_fills < 1`) và tổng fills đã đạt 2, hệ thống khóa không cho phép fill thêm ở London late, bảo lưu slot cuối cùng cho NY.
2. **Preserve Existing Trading Flow & Risk Guards**:
   - Chỉ 1 vị thế mở duy nhất tại một thời điểm (`active_position_count <= 1`).
   - Ngưỡng tỷ lệ lợi nhuận/rủi ro: `min_net_rr >= 2.0` (tính toán đầy đủ sau trừ phí commission, spread, và slippage).
   - Paper trading only: Tuyệt đối không gửi live orders lên sàn giao dịch.
   - Giữ nguyên toàn bộ feed guard, quote sanity guard, transaction rollback, và lifecycle authority.
3. **Causal Evidence & Real SMC Principles**:
   - Không bịa đặt khối lệnh (OB), FVG hay thanh khoản.
   - Sửa triệt để lỗi timestamp CHOCH/BOS (`occurred_at_ms: 0`): trích xuất đúng `candle_open_ms` của nến M1 phá vỡ thực tế.
   - Multi-zone hypotheses: Stable hypothesis ID dạng `hyp-zone-{zone_id}` tách biệt hoàn toàn khỏi thứ hạng hiển thị, loại bỏ tình trạng identity churn.
   - Cụm thanh khoản (Liquidity Clusters): Phân loại và cập nhật trạng thái nhân quả (`UNSWEPT` -> `SWEPT` -> `RECLAIMED` / `ACCEPTED_BEYOND`).
   - Round Numbers: Là cấu hình thứ cấp (mặc định tắt trong `build_liquidity_map()`), bị loại khỏi `select_unconsumed_target()` để không cướp target đỉnh/đáy swing làm suy giảm R:R.
4. **Model Registry & Fail-Closed Guard (Rules 090-092)**:
   - 5 mô hình chuẩn hóa trong `smc_model_registry.py`.
   - Bất kỳ candidate nào có `model_id` không nằm trong registry đều bị từ chối ngay lập tức (fail-closed).
5. **Chart Visual Integrity (Rule 146)**:
   - Chart primitives (`SMCStructurePrimitive.ts`) kiểm tra nghiêm ngặt `level > 0`, `price > 0`, và timestamps hợp lệ, triệt tiêu hoàn toàn lỗi vẽ bám trục tọa độ 0.

---

## 2. Bảng chữ ký hàm, Hợp đồng & Call Chain

| Module / Contract | Hàm / Biểu tượng | Input → Output | Caller thực tế | Kiểm thử liên quan | Trạng thái |
|---|---|---|---|---|---|
| `backend/services/scalp_m1_model_service.py` | `candle_open_ms` | `(bar: Any) -> Optional[int]` (trích xuất open_time/timestamp/time dương) | `qualify_selected_break`, `evaluate_m1_reversal_candidate`, `evaluate_m1_continuation_candidate`, `evaluate_m1_fvg_continuation_candidate`, `evaluate_m1_sweep_reclaim_candidate` | `test_v16_opportunity_engine.py::test_canonical_candle_open_ms`, `test_v16_opportunity_engine.py::test_choch_bos_timestamps_not_zero` | **VERIFIED** |
| `backend/services/scalp_m1_model_service.py` | `evaluate_m1_fvg_continuation_candidate` | `(closed_1m, htf_bias, h1_alignment, quote, policy_settings, now_ms, config, context_levels) -> ScalpCandidate` | `evaluate_smc_opportunity_candidates` | `test_v16_opportunity_engine.py::test_m1_fvg_continuation_positive_and_negative` | **VERIFIED** |
| `backend/services/scalp_m1_model_service.py` | `evaluate_m1_sweep_reclaim_candidate` | `(closed_1m, cluster, quote, policy_settings, now_ms, config, context_levels) -> ScalpCandidate` | `evaluate_smc_opportunity_candidates` | `test_v16_opportunity_engine.py::test_m1_sweep_reclaim_positive_and_negative` | **VERIFIED** |
| `backend/services/scalp_m1_model_service.py` | `evaluate_smc_opportunity_candidates` | Router đánh giá toàn bộ models, deduplicate theo setup key, sort theo Net R:R | `ScalpWatcherOrchestrator.evaluate_cycle` | `test_v16_opportunity_engine.py::test_candidate_router_and_deduplication` | **VERIFIED** |
| `backend/services/liquidity_scenario_service.py` | `build_execution_hypotheses_for_zones` | `(zones, htf_bias, now_ms) -> List[Dict[str, Any]]` với ID ổn định `hyp-zone-{zone_id}` | `ScalpWatcherOrchestrator.evaluate_cycle` | `test_v16_opportunity_engine.py::test_multi_zone_execution_hypotheses_builder` | **VERIFIED** |
| `backend/services/smc_liquidity_service.py` | `build_liquidity_clusters` & `update_cluster_state` | Nhóm levels theo tolerance ticks; cập nhật trạng thái SWEPT / RECLAIMED / ACCEPTED_BEYOND | `ScalpWatcherOrchestrator.evaluate_cycle` | `test_v16_opportunity_engine.py::test_liquidity_clusters_builder_and_state_transition` | **VERIFIED** |
| `backend/services/smc_liquidity_service.py` | `select_unconsumed_target` | Loại trừ `source_kind == "ROUND_NUMBER"`, chọn unconsumed swing target thật | `resolve_executable_target`, `build_execution_hypotheses_for_zones` | `test_v15_0_m1_scalp_models.py`, `test_v16_opportunity_engine.py::test_round_number_target_exclusion_preserves_rr` | **VERIFIED** |
| `backend/services/smc_model_registry.py` | Model Registry & `is_model_registered`, `requires_zone_lifecycle_authority` | Quản lý đặc tả 5 mô hình, kiểm tra thẩm quyền zone | `check_candidate_lifecycle_authority`, candidate evaluators | `test_v16_opportunity_engine.py::test_model_registry_fail_closed` | **VERIFIED** |
| `backend/services/trading_policy_service.py` | `evaluate_daily_frequency_goal` | Theo dõi tiến độ fills (0->1->2->3), khóa NY reservation, gắn nhãn SHORTFALL khi hết phiên | `ScalpWatcherOrchestrator.evaluate_cycle`, API policies | `test_v16_opportunity_engine.py::test_daily_frequency_goal_progression` | **VERIFIED** |
| `backend/services/bitget_depth_service.py` | `validate_orderbook_levels` & `BitgetPublicDepthAdapter` | Kiểm tra thứ tự giá, không crossed book, max age <= 5000ms, shadow logging | `ScalpWatcherOrchestrator` shadow cycle | `test_v16_opportunity_engine.py::test_bitget_depth_adapter_validation` | **VERIFIED** |
| `backend/services/scalp_watcher_orchestrator.py` | Reason-aware Arm Rejection Suppression | Giảm tải log rejection lặp lại cùng reason trong vòng 60s TTL | `ScalpWatcherOrchestrator._arm_paper_candidate` | `test_v15_1_execution_lifecycle.py` | **VERIFIED** |
| `frontend/src/plugins/SMCStructurePrimitive.ts` | Zero Coordinate Filter | Bỏ qua các mức `level <= 0`, `price <= 0`, hoặc timestamp <= 0 | Lightweight-charts rendering pipeline | Vitest frontend suite (226 tests pass) | **VERIFIED** |
| `frontend/src/components/TradeDetailsDrawer.tsx` | `formatStrategyFamilyVi` | Dịch tên hiển thị TV cho 5 strategy families | Drawer chi tiết giao dịch | Vitest frontend suite | **VERIFIED** |

---

## 3. Lệnh chạy kiểm thử xác thực (Test Commands)

```bash
# 1. Chạy bộ kiểm thử V16 Opportunity Engine
PYTHONPATH=backend backend/venv/bin/pytest backend/tests/test_v16_opportunity_engine.py -v

# 2. Chạy bộ hồi quy V15 M1 Scalp Models & Execution Lifecycle
PYTHONPATH=backend backend/venv/bin/pytest backend/tests/test_v15_0_m1_scalp_models.py backend/tests/test_v15_1_execution_lifecycle.py -v

# 3. Chạy toàn bộ kiểm thử backend
PYTHONPATH=backend backend/venv/bin/pytest backend/tests/

# 4. Kiểm thử frontend và build production bundle
cd frontend && npm run test && npm run build
```
