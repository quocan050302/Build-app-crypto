# V16.39.11 IMPLEMENTATION MAP
## Paper Report Reconciliations & Candidate Quality Score Display Closure

**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `fix/v14-1-rr-flow`  
**Baseline Git Commit:** `0affe0f85d482d05d7f23ead846bd8d2ae05ae7c`  
**Release:** V16.39.11  

---

### 1. File, Function & Component Matrix

| Component / File | Symbol / Function | Status | Input | Output | Callers | Tests |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `backend/services/paper_runtime_snapshot_service.py` | `compute_window_opening_equity` | **NEW** | `orders, initial_capital, start_ms` | `(opening_equity, is_verified, unverified_count)` | `compute_financial_metrics` | `test_003_subwindow_opening_equity_and_drawdown`, `test_004_subwindow_opening_equity_unresolved_trade` |
| `backend/services/paper_runtime_snapshot_service.py` | `find_existing_observation_run` | **MODIFIED** | `session, run_id, symbol, scope` | `(row, resolution_status)` | `build_paper_runtime_snapshot`, tools | `test_001_scope_mismatch_prevents_financial_leakage`, `test_002_missing_run_returns_empty_isolated_funnel` |
| `backend/services/paper_runtime_snapshot_service.py` | `compute_financial_metrics` | **MODIFIED** | `orders, initial_capital, window, run_start_ms` | `Financial metrics dictionary with subtotal & accounting_status` | `build_paper_runtime_snapshot` | `test_005_future_orders_excluded_from_historical_window`, `test_006_unresolved_pnl_reports_partial_subtotal`, `test_007_open_orders_only_reports_zero_pnl_not_unresolved` |
| `backend/services/paper_runtime_snapshot_service.py` | `aggregate_funnel_readonly` | **MODIFIED** | `funnel_records` | `Funnel summary with plans_valid` | `build_paper_runtime_snapshot` | `test_008_plan_valid_production_enum_aggregated` |
| `backend/services/paper_runtime_snapshot_service.py` | `read_run_funnel_records` | **MODIFIED** | `session, run_id, symbol, start_ms, end_ms` | `List[OpportunityFunnelRecord]` | `build_paper_runtime_snapshot` | `test_002_missing_run_returns_empty_isolated_funnel` |
| `backend/services/paper_runtime_snapshot_service.py` | `render_markdown` | **MODIFIED** | `snapshot_dict` | `Markdown string` | CLI, tests | `test_006_unresolved_pnl_reports_partial_subtotal` |
| `tools/paper_runtime_snapshot.py` | `parse_http_snapshot` | **MODIFIED** | `payload` | `Validated dict or raises ValueError` | CLI HTTP fetch | `test_009_http_schema_validation_strict` |
| `tools/paper_runtime_snapshot.py` | `build_runtime_snapshot` | **MODIFIED** | `sources, run_id, start_ms, end_ms` | `Runtime snapshot dictionary` | CLI, legacy tests | `test_040_snapshot_worker_stopped_reported_honestly` |
| `backend/services/order_plan_snapshot_service.py` | `extract_immutable_entry_metadata` | **MODIFIED** | `arm_snapshot` | `Dict with preserved deep-copied quality_assessment` | `build_fill_plan_snapshot` | `test_001_fill_plan_snapshot_preserves_candidate_quality_score`, `test_002_fill_plan_snapshot_deep_copy_no_mutable_alias` |
| `backend/services/order_quality_display_service.py` | `CandidateScoreDisplay`, `CandidateScoreDetail` | **NEW** | Pydantic fields | Typed display DTOs | `PaperOrder`, API endpoints | `test_003_score_display_pure_parser_variants`, `test_004_zero_score_is_valid` |
| `backend/services/order_quality_display_service.py` | `parse_candidate_quality_assessment` | **NEW** | `raw (str or dict)` | `(validated_dict, reason_code)` | `build_order_score_display`, `build_order_score_detail` | `test_003_score_display_pure_parser_variants` |
| `backend/services/order_quality_display_service.py` | `build_order_score_display` | **NEW** | `PaperOrder` | `CandidateScoreDisplay` | `PaperOrder.candidate_score_summary` | `test_005_legacy_orders_without_snapshot_report_unavailable` |
| `backend/services/order_quality_display_service.py` | `build_order_score_detail` | **NEW** | `PaperOrder` | `CandidateScoreDetail` | `/api/v1/orders/{id}/quality-score` | `test_006_score_detail_contains_factors_and_blockers` |
| `backend/models.py` | `PaperOrder.candidate_score_summary` | **NEW** | `self` | `Optional[Dict[str, Any]]` | Pydantic `PaperOrderResponse` | `test_007_paper_order_response_serialization_parity` |
| `backend/schemas.py` | `PaperOrderResponse.candidate_score_summary` | **NEW** | Pydantic model field | Serialized JSON dictionary | All journal/order endpoints | `test_007_paper_order_response_serialization_parity` |
| `backend/schemas.py` | `ChartEvidenceDTO.candidate_score_summary`, `candidate_score_detail` | **NEW** | Pydantic model fields | Serialized JSON dictionaries | `/api/v1/orders/{id}/chart-evidence` | `test_007_paper_order_response_serialization_parity` |
| `backend/services/scalp_watcher_orchestrator.py` | `record_candidate_quality_display`, `get_candidate_quality_display` | **NEW** | `binding_key, assessment` | Cached dict copy | `_arm_paper_candidate`, `get_upcoming_setups` | `test_003_score_display_pure_parser_variants` |
| `backend/main.py` | `get_order_quality_score` | **NEW** | `order_id, db` | `CandidateScoreDetail` | Frontend drawer / inspection | `test_006_score_detail_contains_factors_and_blockers` |
| `backend/main.py` | `get_upcoming_setups` | **MODIFIED** | `request, db` | Setup list with `candidate_score_summary` | Frontend `upcomingSetups` | `candidateScore.test.ts` |
| `frontend/src/types/candidateScore.ts` | `CandidateScoreSummary`, `CandidateScoreDetail` | **NEW** | TypeScript interfaces | Typed frontend models | All UI score components | `candidateScore.test.ts` |
| `frontend/src/utils/candidateScore.ts` | `normalizeCandidateScore`, `formatCandidateScore`, `translateDecision` | **NEW** | Raw API payload | Formatted display model (`78/100`) | `CandidateScoreBadge`, `CandidateScoreDetails` | `candidateScore.test.ts` (17 tests) |
| `frontend/src/components/CandidateScoreBadge.tsx` | `<CandidateScoreBadge />` | **NEW** | `summary, compact, onOpenDetails, labelPrefix` | Compact React JSX badge | Setup cards, sidebar, table, chart header | `candidateScore.test.ts` |
| `frontend/src/components/CandidateScoreDetails.tsx` | `<CandidateScoreDetails />` | **NEW** | `summary, detail, orderId` | Rich inspection breakdown | `TradeDetailsDrawer`, `JournalTab` drawer | `candidateScore.test.ts` |
| `frontend/src/components/SelectedChartHeader.tsx` | `<SelectedChartHeader />` | **MODIFIED** | `candidateScore` prop | Header bar with badge | `ChartComponent.tsx` | `candidateScore.test.ts` |
| `frontend/src/ChartComponent.tsx` | `<ChartComponent />` | **MODIFIED** | `selectedCandidateScore` prop | Passed to `SelectedChartHeader` | `App.tsx` | `candidateScore.test.ts` |
| `frontend/src/JournalTab.tsx` | `<JournalTab />` | **MODIFIED** | Table row & drawer | Badge in table, details in drawer | Main App tab | `candidateScore.test.ts` |
| `frontend/src/components/QuickDecisionSidebar.tsx` | `<QuickDecisionSidebar />` | **MODIFIED** | `selectedIntent, activePosition` | Badges in priority cards | Right sidebar | `candidateScore.test.ts` |
| `frontend/src/App.tsx` | `SelectedTradeIntent`, Setup Cards | **MODIFIED** | Full integration | Propagated score across lifecycle | Root App | `candidateScore.test.ts` |

---

### 2. Architectural Call Chains

#### Flow A: Paper Runtime Snapshot Accounting & Isolation
```
CLI / API Request (run_id, scope, start_ms, end_ms)
  │
  ▼
paper_runtime_snapshot_service.find_existing_observation_run()
  ├─ If scope mismatch -> resolution_status = "RUN_SCOPE_MISMATCH" (isolated: no row returned for accounting)
  ├─ If requested run not found -> resolution_status = "NOT_FOUND"
  └─ If resolved -> resolution_status = "FOUND", run_record returned
  │
  ▼
paper_runtime_snapshot_service.build_paper_runtime_snapshot()
  ├─ Gated strictly on resolution_status == "FOUND"
  ├─ If NOT "FOUND":
  │    metrics = None (unresolved counts)
  │    funnel_summary = empty isolated funnel
  │    status = resolution_status
  └─ If "FOUND":
       ├─ compute_window_opening_equity(orders, initial_capital, start_ms)
       │    └─ Sums verified closed trades before start_ms
       ├─ compute_financial_metrics(orders, initial_capital, window, run_start_ms)
       │    ├─ Filters out future orders (created_at >= end_ms)
       │    ├─ Tracks unresolved closed trades (realized_pnl_net is None)
       │    └─ Sets accounting_status = "COMPLETE" | "PARTIAL"
       └─ aggregate_funnel_readonly(funnel_records)
            └─ Matches OpportunityStage.PLAN_VALID ("PLAN_VALID") -> plans_valid
```

#### Flow B: Candidate Quality Score Preserved Across Lifecycle
```
ScalpWatcherOrchestrator._arm_paper_candidate()
  │  (Candidate quality assessed: base 83, lesson -5, final 78, threshold 70)
  ├─ record_candidate_quality_display(binding_key, assessment)  [Volatile cache for UI setups]
  └─ build_arm_plan_snapshot(...) -> arm_snap["quality_assessment"] = assessment.to_dict()
       │  [PaperOrder.cost_snapshot saved with state = ARMED]
       │
       ▼ (Order triggers and fills)
ExecutionCoordinator / TradeLifecycleService.execute_fill()
  │
  ▼
order_plan_snapshot_service.build_fill_plan_snapshot(arm_snapshot, fill_execution)
  └─ extract_immutable_entry_metadata(arm_snapshot)
       └─ Whitelist: copy.deepcopy(arm_snapshot["quality_assessment"])
            │  [Preserved across fill with zero data loss or mutable alias]
            │
            ▼ (Order closes via TP / SL / manual)
TradeLifecycleService.process_exit_tick() / close_trade()
  └─ PaperOrder.cost_snapshot retains immutable quality_assessment
```

#### Flow C: Unified Read-Only Score Projection to UI
```
PaperOrder (in DB)
  │
  ▼
PaperOrder.candidate_score_summary (@property)
  └─ order_quality_display_service.build_order_score_display(self)
       ├─ Parses PaperOrder.cost_snapshot["quality_assessment"]
       ├─ Validates score bounds (0-100, finite number, no bool)
       └─ Returns CandidateScoreDisplay(score=78.0, base=83.0, delta=-5.0, threshold=70.0, frozen=True)
            │
            ▼
schemas.PaperOrderResponse / API Endpoints (/position, /journal, /orders/{id}/chart-evidence)
  │
  ▼
Frontend API Client (api.getJournal, api.getOrderChartEvidence, etc.)
  │
  ▼
normalizeCandidateScore(raw) -> formatCandidateScore(summary)
  │  [Formats displayText: "78/100", no % symbol, disclaimer: not winrate]
  ▼
<CandidateScoreBadge summary={...} />
  ├─ UpcomingSetups card header (App.tsx)
  ├─ QuickDecisionSidebar (selectedIntent & activePosition)
  ├─ JournalTab (orders table direction column)
  └─ SelectedChartHeader (chart viewport top bar)
  │
  └─ (Click on badge or details button)
       ▼
<CandidateScoreDetails summary={...} detail={...} />
  └─ Displays base 83, lesson -5, final 78, threshold 70, FROZEN badge, and explicit disclaimer
```
