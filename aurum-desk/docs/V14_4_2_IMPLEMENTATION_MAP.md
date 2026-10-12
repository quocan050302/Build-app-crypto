# V14.4.2 Implementation Map

Branch `fix/v14-1-rr-flow`, base commit `ebd3b72` (fix(lab): syntax repair on top of `579ecdb`).
Scope: Complete chart interaction correctness, realtime resilience, and end-to-end acceptance per feature.

Labels: **[HIỆN CÓ]** = existed in the baseline, **[MỚI]** = added/extended in V14.4.2.

---

## 1. Mục tiêu & Các nguyên tắc bất biến (Invariants)

1. **Phương pháp SMC & Ngữ cảnh 3 phiên**: Phân tích kịch bản có điều kiện Á / Âu / Mỹ ("nếu... thì..."). Chỉ khi xác nhận cấu trúc thật (Sweep -> Break/Displacement -> FVG bounds thật -> Closed Retest) mới tạo setup. Phân tích 3 phiên **không** tự bật giao dịch phiên Á/Âu.
2. **Setup thật & Bảo vệ rủi ro (Guards)**: Setup tuân thủ cơ chế PAPER ONLY, default `NY_ONLY` (khung giờ vào lệnh 08:00–11:00 NY), tối đa 3 fills/ngày, 1 vị thế mở tại một thời điểm, fallback cap 0.10%, giữ nguyên các guards (news / lessons / account / quote / feed / cost / policy).
3. **Bằng chứng Chart (Chart Evidence)**: Bấm chọn đối tượng nào (`SCENARIO` / `SETUP` / `ORDER` / `DRAFT`) thì chart hiển thị chính xác facts và snapshot của riêng đối tượng đó. Tọa độ, bounds FVG, Swing và R:R theo đúng hướng thực tế.
4. **Xác suất trung thực (Honest Probability)**: Chưa có mô hình/bộ dữ liệu Out-Of-Sample (OOS) được kiểm định thực tế thì xác suất hiển thị `null` / `UNAVAILABLE` / `NOT_VERIFIED`. Tuyệt đối không biến điểm checklist thành tỷ lệ % win giả.
5. **Bảo toàn dữ liệu & An toàn hệ thống**: Không reset hard, không force push, bảo vệ nguyên vẹn live database, live positions, cài đặt rủi ro của người dùng.

---

## 2. Thứ tự triển khai các chặng (Stages A → F)

| Stage | Tên chặng | Phạm vi & Modules chính | Mục tiêu & Checkpoint Gate |
|---|---|---|---|
| **Stage A** | Baseline & Contracts | `test_v14_4_1_production_pipeline`, `test_v14_4_1_contracts`, `test_v14_3_*`, `vitest`, `oxlint`, `build` | Baseline hoàn toàn xanh (136 tests backend, 187 tests frontend pass, build sạch). |
| **Stage B** | Checklist Model & Forecast | `smc_scenario_config`, `smc_scenario_binding`, `smc_session_forecast_service`, `smc_scenario_summary`, `smc_observation_service` | Thêm descriptor yêu cầu model, phân biệt M15 POI (bắt buộc/hỗ trợ), quan hệ forecast 3 phiên (`SUPPORTS`/`CONFLICTS`/`UNCERTAIN`), snapshot provenance. |
| **Stage C** | Chart Layout & Accessibility | `chartAnnotationLayout`, `SMCStructurePrimitive`, `RiskRewardPrimitive`, `TradeDetailsDrawer`, `ChartEvidenceList`, `useChartLayers`, `JournalTab` | Bounded viewport clamping cho labels, reserved rectangles cho R:R, mobile Chart button tap được trực tiếp (không dispatchEvent), Drawer/List chi tiết bằng chứng. |
| **Stage D** | Drag, Reconnect & Performance | `ChartComponent`, `wsClient`, `useSMCScenarios`, `useSelectedChart`, E2E test suites | Kéo thả DRAFT SHORT giữ đúng hướng, PointerCancel rollback an toàn, xử lý sequence gap & reconnect, soak 100 selection cycles, stream 10k ticks mượt mà. |
| **Stage E** | Browser Cases & Production Matrix | `v14_4_2_chart_closure.e2e.ts`, `v14_4_2_realtime_soak.e2e.ts`, `v14_4_2_mobile.e2e.ts`, `test_v14_4_2_entry_regression` | Kiểm thử Playwright trên browser thực (D01–D12, R01–R05, M01–M04, E01–E07), 4 trạng thái order readonly, ma trận guards (tin tức/bài học). |
| **Stage F** | Acceptance Runner, Report & Git | `tools/run_v14_4_2_acceptance.py`, `docs/V14_4_2_ACCEPTANCE_REPORT.md`, `docs/V14_4_2_FORWARD_PAPER_GUIDE.md` | Runner độc lập kiểm chứng toàn bộ, chụp ảnh visual QA, xuất báo cáo nghiệm thu trung thực, commit và push lên nhánh `fix/v14-1-rr-flow`. |

---

## 3. Bảng chữ ký hàm, Hợp đồng & Callers (Call Chain)

| Module / Contract | Hàm / Biểu tượng | Input → Output | Caller thực tế | Kiểm thử liên quan | Trạng thái |
|---|---|---|---|---|---|
| `backend/services/smc_scenario_config.py` | `describe_model_requirements` **[MỚI]** | `(config: ScenarioConfig) -> Dict[str, Any]` (liệt kê sweep, break, displacement, fvg, retest, target; M15 POI required/supporting) | `smc_scenario_binding.py`, `smc_scenario_summary.py`, `chart_evidence_service.py` | `test_v14_4_2_requirements.py::test_rq01_model_requirements_descriptor` | **IMPLEMENTED & TESTED** |
| `backend/services/smc_scenario_types.py` | `ScenarioSnapshot` **[HIỆN CÓ]** | Bổ sung `config_version: int`, `requirement_profile: str` vào schema snapshot | `smc_scenario_service.py::build_scenario_snapshot` | `test_v14_4_2_requirements.py::test_rq02_snapshot_carries_profile_and_version` | **IMPLEMENTED & TESTED** |
| `backend/services/smc_scenario_binding.py` | `verify_scenario_binding` **[HIỆN CÓ]** | Kiểm tra config version & profile khớp với lệnh mới; bảo vệ provenance lịch sử | `entry_guard_service.py`, `eligibility_service.py::evaluate_setup_eligibility` | `test_v14_4_2_requirements.py::test_rq03_binding_verification_rejects_version_mismatch` | **IMPLEMENTED & TESTED** |
| `backend/services/chart_evidence_service.py` | `read_snapshot_provenance` **[MỚI]** | `(snapshot_dict: dict, current_config: ScenarioConfig) -> dict` | `main.py` (`GET /orders/{id}/chart-evidence`) | `test_v14_4_2_requirements.py::test_rq04_provenance_audit_preserves_historical_snapshots` | **IMPLEMENTED & TESTED** |
| `backend/services/smc_session_forecast_service.py` | `evaluate_session_forecasts` **[HIỆN CÓ]** | Bổ sung trường `relation_status` (`SUPPORTS`, `CONFLICTS`, `UNCERTAIN`) đối chiếu với NY scenario | `strategy_service.py` (`evaluate_market_reading_cycle`) | `test_v14_4_2_forecast_requirements.py::test_s02_forecast_relationship` | **IMPLEMENTED & TESTED** |
| `backend/services/smc_scenario_summary.py` | `build_entry_explanation` **[MỚI]** | Trả về structured context gồm bối cảnh, level theo dõi, sweep, break, FVG, target và guard audit | `chart_evidence_service.py`, Drawer frontend | `test_v14_4_2_requirements.py::test_rq05_entry_explanation_structure` | **IMPLEMENTED & TESTED** |
| `backend/services/smc_observation_service.py` | `record_observation_snapshot` **[MỚI]** | Ghi nhận dữ liệu snapshot quan sát forward PAPER có kiểm soát, cô lập rủi ro | `strategy_service.py::evaluate_upcoming_setups`, `trade_lifecycle_service.py::execute_close` | `test_v14_4_2_entry_regression.py::test_s06_close_and_new_signal` | **IMPLEMENTED & TESTED** |
| `frontend/src/utils/chartAnnotationLayout.ts` | `layoutAnnotationLabels` **[HIỆN CÓ]** | Nhận `_viewport: Rect`, tính toán candidate lanes, clamp & fit toàn bộ nhãn trong pane | `SMCStructurePrimitive.ts` | `v14_4_2_annotation_layout.test.ts` (8/8 pass) | **IMPLEMENTED & TESTED** |
| `frontend/src/plugins/RiskRewardPrimitive.ts` | `RiskRewardPrimitive` **[HIỆN CÓ]** | Kéo SL/TP cho SHORT giữ nguyên hướng, chặn thao tác drag cho 4 trạng thái order readonly | `ChartComponent.tsx` | `v14_4_2_chart_closure.e2e.ts` (D01–D12) | **IMPLEMENTED & TESTED** |
| `frontend/src/ChartComponent.tsx` | Pointer & RAF Lifecycle **[HIỆN CÓ]** | Coalesce RAF paint, rollback an toàn khi PointerCancel / lost capture, chuẩn hóa đơn vị ms ở accessor | Thao tác chart trên giao diện | `v14_4_2_chart_closure.e2e.ts` (D08–D10) | **IMPLEMENTED & TESTED** |
| `frontend/src/hooks/useSMCScenarios.ts` | Quản lý Resync & Sequence Gap **[HIỆN CÓ]** | Tách rời side-effect ra khỏi React updater, xử lý epoch reset khi reconnect socket | WebSocket subscriber | `v14_4_2_selection_ws.test.ts` (6/6 pass), `v14_4_2_realtime_soak.e2e.ts` (R01–R05) | **IMPLEMENTED & TESTED** |
| `frontend/src/hooks/useChartLayers.ts` | `useChartLayers` **[MỚI]** | Lưu trữ tùy chọn bật/tắt layer vào `localStorage` có version schema bảo vệ | `ChartComponent.tsx`, `ChartLayerMenu.tsx` | `v14_4_2_chart_closure.e2e.ts` (E07) | **IMPLEMENTED & TESTED** |
| `frontend/src/JournalTab.tsx` | Nút Chart trên Mobile **[HIỆN CÓ]** | Bố trí layout sticky / compact row để nút Chart nằm hoàn toàn trong viewport màn hình hẹp (390x844) | Bảng lịch sử lệnh Journal | `v14_4_2_mobile.e2e.ts` (M01–M04) | **IMPLEMENTED & TESTED** |
| `frontend/src/components/TradeDetailsDrawer.tsx` | `EvidenceAccordion` & `SnapshotProvenance` **[MỚI]** | Hiển thị bảng chi tiết các bước xác nhận SMC và thông tin provenance của snapshot | Drawer chi tiết lệnh | `v14_4_2_chart_closure.e2e.ts` (E06) | **IMPLEMENTED & TESTED** |
| `frontend/src/components/ChartEvidenceList.tsx` | `ChartEvidenceList` **[MỚI]** | Hỗ trợ danh sách bằng chứng dạng modal phím tắt (Enter focus, Esc đóng) | `ChartComponent.tsx` | `v14_4_2_chart_closure.e2e.ts` (E08) | **IMPLEMENTED & TESTED** |
| `frontend/src/utils/evidenceFocus.ts` | `flattenEvidenceBundle`, `buildEvidenceAnchorIndex`, `computeEvidenceFocusRange` **[MỚI]** | Chuẩn hóa tọa độ timestamps (`_ms` & camelCase), deduplicate nodes và tính toán focus range | `ChartComponent.tsx` | `v14_4_2_evidence_focus.test.ts` (F01–F06), `v14_4_2_chart_closure.e2e.ts` (E08, E09) | **IMPLEMENTED & TESTED** |
| `frontend/src/utils/scenarioEvents.ts` | `applyScenarioUpdate`, `buildScenarioEventKey`, `pruneSeenDigests` **[MỚI]** | Hàm thuần khiết áp dụng domain event, deduplicate digest có trần 256 keys | `useSMCScenarios.ts` | `v14_4_2_selection_ws.test.ts` (RT01–RT08) | **IMPLEMENTED & TESTED** |
| `frontend/src/plugins/RiskRewardPrimitive.ts` | `getReservedRects` **[MỚI]** | Xuất bounding box nhãn R:R (SL/Entry/TP) dưới dạng `readonly Rect[]` | `ChartComponent.tsx` | `v14_4_2_annotation_layout.test.ts` (L01–L05) | **IMPLEMENTED & TESTED** |
| `frontend/src/plugins/SMCStructurePrimitive.ts` | `setExclusions`, `setFocusedEvidenceId` **[MỚI]** | Nhận vùng cấm vẽ nhãn từ R:R primitive và làm nổi bật visual evidence được focus | `ChartComponent.tsx` | `v14_4_2_annotation_layout.test.ts` (L01–L05), `v14_4_2_chart_closure.e2e.ts` (E03, E08) | **IMPLEMENTED & TESTED** |
| `backend/services/smc_observation_service.py` | `read_order_field`, `savepoint_fault_isolation` **[MỚI]** | Bảo toàn giá trị thực `0.0` cho `realized_pnl_net` & `realized_r`, cô lập lỗi quan sát bằng `db.begin_nested()` | `trade_lifecycle_service.py` | `test_v14_4_2_entry_regression.py` (O06–O11) | **IMPLEMENTED & TESTED** |
| `backend/tests/test_v14_4_2_acceptance_runner.py` | Unit tests Acceptance Runner (A01–A08) **[MỚI]** | Kiểm chứng cơ chế phát hiện thiếu case, stale report, whitelisting và coverage manifest | `tools/run_v14_4_2_acceptance.py` | `test_v14_4_2_acceptance_runner.py` (A01–A08) | **IMPLEMENTED & TESTED** |
