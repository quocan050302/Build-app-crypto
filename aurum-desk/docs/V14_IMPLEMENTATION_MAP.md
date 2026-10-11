# V14 IMPLEMENTATION MAP — BỘ ĐÁNH GIÁ CHẤT LƯỢNG SMC VÀ BẰNG CHỨNG THEO LỆNH TRÊN CHART

## 1. Metadata
- **Project**: Aurum Desk
- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `feature/aurum-repair-smc-rr`
- **Base HEAD**: `c7f9e54db7886349dc7bcd053caec7aa3013d2ff`
- **Feature Scope**: V14 Explainable SMC Setup Quality Assessment & Focused Chart Evidence (EXPLAIN_ONLY)
- **Status**: Implemented & Verified

---

## 2. File & Symbol Mapping

| File Path | Symbol / Function | Role | Input | Output | Callers | Tests |
|---|---|---|---|---|---|---|
| `backend/services/setup_quality_service.py` | `derive_quality_status` | Pure status derivation with strict precedence | `checks`, `assessed`, `existing_hard_blockers` | `QualityStatus` (`SATISFIED`, `PARTIAL`, `INSUFFICIENT_DATA`, `BLOCKED_EXISTING`, `ERROR`, `NOT_ASSESSED`) | `assess_setup_quality` | `test_setup_quality_service_v14.py::test_derive_quality_status_precedence` |
| `backend/services/setup_quality_service.py` | `select_related_evidence` | Pure causal evidence traversal | `evidence`, `matched_ids`, `symbol`, `as_of_ms` | `(tuple[EvidenceRef], tuple[str])` | `assess_setup_quality` | `test_setup_quality_service_v14.py::test_select_related_evidence_strict_causality` |
| `backend/services/setup_quality_service.py` | `evaluate_quality_checks` | Pure evaluation of REQUIRED & SUPPORTING criteria | `context` | `(tuple[QualityCheck], tuple[str])` | `assess_setup_quality` | `test_setup_quality_service_v14.py::test_full_chain_long_setup` |
| `backend/services/setup_quality_service.py` | `build_quality_summary` | Fact-based Vietnamese summary | `status`, `checks`, `cautions` | `QualitySummary` | `assess_setup_quality` | `test_setup_quality_service_v14.py` |
| `backend/services/setup_quality_service.py` | `assess_setup_quality` | Pure assessment orchestrator | Normalized context dict | `(QualityAssessment, Sequence[EvidenceRef])` | `StrategyService._assess_and_save_quality`, `preview_setup_quality` | `test_setup_quality_service_v14.py` |
| `backend/services/setup_quality_service.py` | `build_quality_snapshot` | Canonical immutable snapshot builder | Plan, assessment, evidence | `Dict[str, Any]` (canonical hash) | `_assess_and_save_quality`, `preview_setup_quality` | `test_setup_quality_service_v14.py` |
| `backend/models.py` | `SetupQualitySnapshot` | Idempotent persistence table | Schema version, snapshot ID, payload JSON, hash | ORM Model | `setup_quality_repository.py` | `test_setup_quality_api_v14.py` |
| `backend/services/setup_quality_repository.py` | `save_quality_snapshot` | Save immutable snapshot | DB session, snapshot dict | Snapshot ID | `StrategyService._assess_and_save_quality` | `test_setup_quality_api_v14.py` |
| `backend/services/setup_quality_repository.py` | `get_latest_snapshot_for_setup` | Read latest snapshot for setup ID | DB session, setup ID, instance ID | Snapshot dict or None | `GET /api/v1/setups/{id}/quality` | `test_setup_quality_api_v14.py` |
| `backend/services/setup_quality_repository.py` | `get_snapshots_batch` | Batch read snapshots to prevent N+1 | DB session, setup IDs | Dict[setup_id, snapshot] | `GET /api/v1/setups/upcoming` in `main.py` | `test_setup_quality_api_v14.py` |
| `backend/main.py` | `get_setup_quality` | GET endpoint for setup quality | `setup_id`, instance/revision query | `QualityDetailResponse` (404/409/200) | Frontend `api.getSetupQuality` | `test_setup_quality_api_v14.py::test_get_setup_quality_endpoint` |
| `backend/main.py` | `get_quality_snapshot_by_id` | GET endpoint for snapshot by ID | `snapshot_id` | Snapshot payload JSON | Frontend `api.getQualitySnapshot` | `test_setup_quality_api_v14.py` |
| `backend/main.py` | `preview_setup_quality` | POST calculation endpoint for custom drafts | `DraftQualityPreviewRequest` | `DRAFT_PREVIEW` with plan fingerprint & calc | Frontend `api.previewSetupQuality` | `test_setup_quality_api_v14.py::test_preview_setup_quality_draft` |
| `frontend/src/plugins/SMCStructurePrimitive.ts` | `SMCStructurePaneRenderer.draw` | Lightweight Charts v5.2 renderer for SMC | Swings, StructureEvents (BOS, CHOCH, SWEEP), Zones (FVG/POI) | Canvas Rendering Target 2D | `ChartComponent.tsx` | `v14_chart_evidence.test.ts`, Browser demo |
| `frontend/src/utils/chartEvidence.ts` | `normalizeStructureKind` | Canonical normalizer for event kinds | `value: unknown` | `'BOS' \| 'CHOCH' \| 'SWEEP' \| null` | `buildChartEvidenceBundle` | `v14_chart_evidence.test.ts` |
| `frontend/src/utils/chartEvidence.ts` | `msToChartTime` | Timestamp normalizer | `value: unknown` (ms) | `UTCTimestamp \| null` (s) | `ChartComponent.tsx` | `v14_chart_evidence.test.ts` |
| `frontend/src/utils/chartEvidence.ts` | `hasValidGeometry` | Strict geometry validator | `ResolvedChartPlan` | `boolean` (LONG: SL < entry < TP; SHORT: TP < entry < SL) | `handleFocusSetupOnChart`, `onOverlayChange` | `v14_chart_evidence.test.ts` |
| `frontend/src/utils/chartEvidence.ts` | `buildChartEvidenceBundle` | Evidence bundle builder for chart | `QualitySnapshot`, `selectionKey` | `ChartEvidenceBundle` | `App.tsx` (memoized) | `v14_chart_evidence.test.ts` |
| `frontend/src/api/client.ts` | `api.getSetupQuality` | API client method | `setupId`, options (generation signal) | Promise<QualityDetail> | `App.tsx` | `v14_chart_evidence.test.ts` |
| `frontend/src/api/client.ts` | `api.previewSetupQuality` | API client method | `payload`, signal | Promise<DraftPreview> | `App.tsx` (`handleCopySetupToDraft`, `onOverlayChange`) | `v14_chart_evidence.test.ts` |
| `frontend/src/ChartComponent.tsx` | `ChartComponent` | Canvas chart with focused evidence layer | `selectedEvidence`, `evidenceMode`, `focusRequest` | Interactive TradingView Lightweight Chart | `App.tsx` | Browser demo, unit tests |
| `frontend/src/components/QuickDecisionSidebar.tsx` | `QuickDecisionSidebar` | 4-line compact quality card | `selectedQualitySnapshot`, `selectedQualityLoading` | Compact UI card | `App.tsx` | Browser demo |
| `frontend/src/components/TradeDetailsDrawer.tsx` | `TradeDetailsDrawer` | Section `'quality'` | `selectedQualitySnapshot`, `selectedQualityLoading` | Detailed checklist & evidence drawer | `App.tsx` | Browser demo |
| `frontend/src/App.tsx` | `handleFocusSetupOnChart` | Setup focus with generation token & cancel | `setup` | State update & focused chart overlay | Upcoming card, Sidebar | Browser demo |
| `frontend/src/App.tsx` | `onOverlayChange` | Drag levels -> Draft mutation | `updated: RiskRewardData` | `source: 'DRAFT'`, debounced preview | Chart pointer drag | Browser demo |

---

## 3. Data Flow Diagram
```
[Closed Candles + Quote + News]
               │
               ▼
   [Strategy Analysis Evidence]
               │
               ▼
 [Pure assess_setup_quality()] ──> (derive_quality_status, select_related_evidence)
               │
               ▼
   [Immutable QualitySnapshot] ──> (SetupQualityRepository.save_quality_snapshot)
               │
               ├────────────────────────────────────────┐
               ▼                                        ▼
   [GET /setups/upcoming]               [GET /setups/{id}/quality]
               │                                        │
               ▼                                        ▼
   [Upcoming List Badge]                [Generation-scoped Selection]
                                                        │
                                                        ▼
                                       [buildChartEvidenceBundle]
                                                        │
                                       ┌────────────────┴────────────────┐
                                       ▼                                 ▼
                          [SMCStructurePrimitive]              [QuickDecisionSidebar]
                          - Purple CHoCH line                  - 4-line compact card
                          - Amber SWEEP glyph                  - Status badge & counts
                          - Translucent FVG zones              - TradeDetailsDrawer section
```
