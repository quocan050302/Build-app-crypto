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
| `backend/services/smc_scenario_config.py` | `describe_model_requirements` **[MỚI]** | `(config: ScenarioConfig) -> Dict[str, Any]` (liệt kê sweep, break, displacement, fvg, retest, target; M15 POI required/supporting) | `main.py`, `smc_scenario_summary.py`, API endpoints | `test_v14_4_2_requirements.py` | NOT_IMPLEMENTED |
| `backend/services/smc_scenario_types.py` | `ScenarioSnapshot` **[HIỆN CÓ]** | Bổ sung `config_version: int`, `requirement_profile: str` vào schema snapshot | `smc_scenario_service.py` | `test_v14_4_2_requirements.py` | NOT_IMPLEMENTED |
| `backend/services/smc_scenario_binding.py` | `verify_scenario_binding` **[HIỆN CÓ]** | Kiểm tra config version & profile khớp với lệnh mới; bảo vệ provenance lịch sử | `entry_guard_service.py`, `eligibility_service.py` | `test_v14_4_2_requirements.py` | NOT_IMPLEMENTED |
| `backend/services/chart_evidence_service.py` | `read_snapshot_provenance` **[MỚI]** | `(snapshot_dict: dict, current_config: ScenarioConfig) -> dict` | `main.py` (`GET /orders/{id}/chart-evidence`) | `test_v14_4_2_requirements.py` | NOT_IMPLEMENTED |
| `backend/services/smc_session_forecast_service.py` | `evaluate_session_forecasts` **[HIỆN CÓ]** | Bổ sung trường `relation_status` (`SUPPORTS`, `CONFLICTS`, `UNCERTAIN`) đối chiếu với NY scenario | `strategy_service.py` (`evaluate_market_reading_cycle`) | `test_v14_4_2_forecast_requirements.py` | NOT_IMPLEMENTED |
| `backend/services/smc_scenario_summary.py` | `build_entry_explanation` **[MỚI]** | Trả về structured context gồm bối cảnh, level theo dõi, sweep, break, FVG, target và guard audit | API chi tiết lệnh, Drawer frontend | `test_v14_4_2_requirements.py` | NOT_IMPLEMENTED |
| `backend/services/smc_observation_service.py` | `record_observation_snapshot` **[MỚI]** | Ghi nhận dữ liệu snapshot quan sát forward PAPER có kiểm soát, cô lập rủi ro | `strategy_service.py` | `test_v14_4_2_requirements.py` | NOT_IMPLEMENTED |
| `frontend/src/utils/chartAnnotationLayout.ts` | `layoutAnnotationLabels` **[HIỆN CÓ]** | Nhận `_viewport: Rect`, tính toán candidate lanes, clamp & fit toàn bộ nhãn trong pane | `SMCStructurePrimitive.ts` | `v14_4_2_annotation_layout.test.ts` | NOT_IMPLEMENTED |
| `frontend/src/plugins/RiskRewardPrimitive.ts` | `RiskRewardPrimitive` **[HIỆN CÓ]** | Kéo SL/TP cho SHORT giữ nguyên hướng, chặn thao tác drag cho 4 trạng thái order readonly | `ChartComponent.tsx` | `v14_4_2_chart_closure.e2e.ts` | NOT_IMPLEMENTED |
| `frontend/src/ChartComponent.tsx` | Pointer & RAF Lifecycle **[HIỆN CÓ]** | Coalesce RAF paint, rollback an toàn khi PointerCancel / lost capture, chuẩn hóa đơn vị ms ở accessor | Thao tác chart trên giao diện | `v14_4_2_chart_closure.e2e.ts` | NOT_IMPLEMENTED |
| `frontend/src/hooks/useSMCScenarios.ts` | Quản lý Resync & Sequence Gap **[HIỆN CÓ]** | Tách rời side-effect ra khỏi React updater, xử lý epoch reset khi reconnect socket | WebSocket subscriber | `v14_4_2_selection_ws.test.ts` | NOT_IMPLEMENTED |
| `frontend/src/hooks/useChartLayers.ts` | `useChartLayers` **[MỚI]** | Lưu trữ tùy chọn bật/tắt layer vào `localStorage` có version schema bảo vệ | `ChartLayerMenu.tsx` | `v14_4_2_chart_closure.e2e.ts` | NOT_IMPLEMENTED |
| `frontend/src/JournalTab.tsx` | Nút Chart trên Mobile **[HIỆN CÓ]** | Bố trí layout sticky / compact row để nút Chart nằm hoàn toàn trong viewport màn hình hẹp (390x844) | Bảng lịch sử lệnh Journal | `v14_4_2_mobile.e2e.ts` | NOT_IMPLEMENTED |
| `frontend/src/components/TradeDetailsDrawer.tsx` | `EvidenceAccordion` & `SnapshotProvenance` **[MỚI]** | Hiển thị bảng chi tiết các bước xác nhận SMC và thông tin provenance của snapshot | Drawer chi tiết lệnh | `v14_4_2_chart_closure.e2e.ts` | NOT_IMPLEMENTED |
| `frontend/src/components/ChartEvidenceList.tsx` | `ChartEvidenceList` **[MỚI]** | Hỗ trợ danh sách bằng chứng dạng danh sách phím tắt (Enter focus, Esc đóng) | Menu popup trên chart | `v14_4_2_chart_closure.e2e.ts` | NOT_IMPLEMENTED |
