# V14.4 Implementation Map — Causal SMC Scenarios & Chart Evidence Selection

## 1. Backend Core Architecture

| Function / Symbol | Status | File | Input | Output | Mutation | Callers | Tests |
|---|---|---|---|---|---|---|---|
| `validate_reversal_order` | [MỚI] | `backend/services/smc_scenario_types.py` | `symbol, direction, as_of_ms, sweep, brk, fvg, retest` | `ChainCheck` (verified, reasons, ids) | None (Pure) | `validate_entry_evidence_bundle` | `test_v14_4_scenario_causality.py` |
| `make_evidence_id` | [MỚI] | `backend/services/smc_scenario_types.py` | `symbol, timeframe, kind, occurred_at_ms, price` | `str` (deterministic ID) | None (Pure) | Liquidity & detector services | `test_v14_4_scenario_causality.py` |
| `make_scenario_identity` | [MỚI] | `backend/services/smc_scenario_types.py` | `symbol, session_date, family, direction, anchor_id` | `str` (deterministic ID) | None (Pure) | Scenario service | `test_v14_4_scenario_causality.py` |
| `ScenarioConfig` | [MỚI] | `backend/services/smc_scenario_config.py` | Configuration parameters | Config instance | None | Scenario engine | `test_v14_4_scenario_causality.py` |
| `build_session_range` | [MỚI] | `backend/services/smc_session_context.py` | `symbol, session_name, bars, now_ms` | `SessionRange` (high, low, is_complete) | None (Pure) | `build_liquidity_map` | `test_v14_4_sessions.py` |
| `build_previous_day_levels` | [MỚI] | `backend/services/smc_session_context.py` | `symbol, daily_bars, now_ms` | `Dict[str, float]` (PDH, PDL, PDC, PDO) | None (Pure) | `build_liquidity_map` | `test_v14_4_sessions.py` |
| `cluster_equal_levels` | [MỚI] | `backend/services/smc_liquidity_service.py` | `swings, tolerance_pct` | `List[LiquidityLevel]` (EQH/EQL) | None (Pure) | `build_liquidity_map` | `test_v14_4_liquidity.py` |
| `build_liquidity_map` | [MỚI] | `backend/services/smc_liquidity_service.py` | `symbol, swings, session_ranges, pd_levels, now_ms` | `List[LiquidityLevel]` | None (Pure) | `evaluate_scenarios` | `test_v14_4_liquidity.py` |
| `update_liquidity_state` | [MỚI] | `backend/services/smc_liquidity_service.py` | `levels, closed_candles, now_ms` | `List[LiquidityLevel]` (UNTOUCHED/SWEPT/BROKEN/CONSUMED) | None (Pure) | `evaluate_scenarios` | `test_v14_4_liquidity.py` |
| `select_unconsumed_target` | [MỚI] | `backend/services/smc_liquidity_service.py` | `levels, direction, entry_price` | `Optional[LiquidityLevel]` | None (Pure) | `evaluate_scenarios` | `test_v14_4_liquidity.py` |
| `qualify_displacement` | [MỚI] | `backend/services/smc_chain_service.py` | `candle, prefix_candles, min_atr_ratio` | `Dict` (is_qualified, body_size, atr, ratio) | None (Pure) | `validate_reversal_chain` | `test_v14_4_scenario_causality.py` |
| `validate_chain_order` | [MỚI] | `backend/services/smc_chain_service.py` | `nodes: List[EvidenceNode]` | `Tuple[bool, str]` | None (Pure) | `validate_reversal_chain` | `test_v14_4_scenario_causality.py` |
| `validate_reversal_chain` | [MỚI] | `backend/services/smc_chain_service.py` | `context, direction, now_ms` | `ChainValidationResult` | None (Pure) | `evaluate_scenarios` | `test_v14_4_scenario_pipeline.py` |
| `build_scenario_plan` | [MỚI] | `backend/services/smc_scenario_plan.py` | `direction, entry, sl, tp, equity, risk_pct` | `ScenarioPlan` | None (Pure) | `evaluate_scenarios` | `test_v14_4_scenario_pipeline.py` |
| `build_scenario_summary_vi` | [MỚI] | `backend/services/smc_scenario_summary.py` | `context, anchor, confirmed, waiting, target, invalidation` | `str` (Vietnamese explanation) | None (Pure) | `evaluate_scenarios` | `test_v14_4_scenario_pipeline.py` |
| `evaluate_scenarios` | [MỚI] | `backend/services/smc_scenario_service.py` | `symbol, context, previous, config, now_ms, quote` | `List[ScenarioSnapshot]` | None (Pure) | `evaluate_scenario_cycle` | `test_v14_4_scenario_pipeline.py` |
| `evaluate_scenario_cycle` | [MỚI] | `backend/services/smc_scenario_service.py` | `db, symbol, now_ms, closed_context, quote` | `List[ScenarioSnapshot]` | DB upsert revision | `evaluate_upcoming_setups` | `test_v14_4_scenario_pipeline.py` |
| `build_selected_chart_bundle` | [MỚI] | `backend/services/chart_evidence_service.py` | `snapshot, selection_binding` | `Dict` (exact evidence bundle) | None (Pure) | API `/chart-evidence` endpoints | `test_v14_4_scenario_pipeline.py` |
| `QuoteValidator.validate_ticker` | [HIỆN CÓ] Sửa | `backend/services/quote_validator.py` | `ticker, now` | `ValidationResult` | None | `strategy_service`, `execution_coordinator` | `test_v14_4_quote_provenance.py` |

---

## 2. Frontend Components & Visual Evidence Architecture

| Component / Function | Status | File | Purpose | Callers | Tests |
|---|---|---|---|---|---|
| `chartSelectionKey` | [MỚI] | `frontend/src/types/smcScenario.ts` | Builds unique identity key for chart bindings (source, id, instance, revision, direction, planFingerprint, snapshotId) | Selection reducer & bundle builder | `v14_4_chart_evidence.test.ts` |
| `selectedChartReducer` | [MỚI] | `frontend/src/hooks/useSelectedChart.ts` | Guarded generation reducer for atomic selection transitions with A->B->A race protection | `useSelectedChart` hook | `v14_4_chart_evidence.test.ts` |
| `useSMCScenarios` | [MỚI] | `frontend/src/hooks/useSMCScenarios.ts` | Realtime subscriber and polling manager for current SMC scenarios | `App.tsx` | Vitest suite |
| `SMCStructurePrimitive` | [HIỆN CÓ] Mở rộng | `frontend/src/plugins/SMCStructurePrimitive.ts` | Canvas renderer for true FVG times, displacement outlines, retest markers, liquidity levels, and conditional scenario path; implements ISeriesPrimitiveBase hitTest | `ChartComponent.tsx` | Vitest & build |
| `ChartComponent` | [HIỆN CÓ] Sửa | `frontend/src/ChartComponent.tsx` | Renders chart, ensures SELECTED mode NEVER falls back to global market structure, uses authentic startAtMs/endAtMs for FVGs, handles historical trade focus | `App.tsx` | Vitest & build |
| `SelectedChartHeader` | [MỚI] | `frontend/src/components/SelectedChartHeader.tsx` | Compact top bar displaying selected target identity, direction, state, and plan levels | `ChartComponent.tsx` | Vitest & build |
| `ChartLayerMenu` | [MỚI] | `frontend/src/components/ChartLayerMenu.tsx` | Popup menu to toggle individual evidence layers (swings, liquidity, FVG, breaks, scenario path, R:R) | `ChartComponent.tsx` | Vitest & build |
| `ChartEvidenceTooltip` | [MỚI] | `frontend/src/components/ChartEvidenceTooltip.tsx` | Detailed hover/click tooltip for canvas SMC annotations | `ChartComponent.tsx` | Vitest & build |
| `SMCScenarioCard` | [MỚI] | `frontend/src/components/SMCScenarioCard.tsx` | Compact 4-6 line scenario card for sidebar display | `QuickDecisionSidebar.tsx` | Vitest & build |
| `layoutAnnotationLabels` | [MỚI] | `frontend/src/utils/chartAnnotationLayout.ts` | Layout collision resolver ensuring Entry/SL/TP priority badges remain visible | Canvas renderer | `v14_4_annotation_layout.test.ts` |
