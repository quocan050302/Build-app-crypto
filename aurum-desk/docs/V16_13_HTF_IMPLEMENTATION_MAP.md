# V16.13 HTF IMPLEMENTATION MAP

This document maps all functions, classes, and types modified or introduced in V16.13 to their inputs, outputs, production callers, and verifying tests.

---

## 1. Engine & State Machine (`backend/services/htf_structure_service.py`)

### `evaluate_reference_close_break`
- **Status:** IMPLEMENTED
- **Input:**
  - `direction: str` ("BULLISH" | "BEARISH")
  - `reference_price: float`
  - `closed_bar: Any` (must provide `close: float`, `open_time: int`, `close_time: int`)
  - `settings: HTFSettings` (provides `break_buffer_ticks: int`, `tick_size: float`)
- **Output:** `ReferenceCloseBreakResult(is_confirmed: bool, direction: str, reference_price: float, close_price: float, required_threshold: float, buffer_amount: float, reason: str)`
- **Production Caller:**
  - `confirm_transition_structure()` (lines 665-680)
  - `step_htf_state()` for Bullish BOS (line 793), Bearish BOS (line 830), Bullish Protected Break (line 803), Bearish Protected Break (line 840)
- **Tests:** `backend/tests/test_v16_13_htf_confirmation.py::test_break_buffer_enforced`, `test_no_bullish_choch_when_candle_close_below_reference`, `test_valid_close_break_confirms_structure`

### `classify_post_invalidation_sequence`
- **Status:** IMPLEMENTED
- **Input:**
  - `fresh_swings: List[Dict[str, Any]]` (swings with `occurred_at_ms >= invalidation_cutoff_ms`)
  - `settings: HTFSettings`
  - `last_confirmed_bias: Optional[str]`
- **Output:** `Optional[PostInvalidationSequence]` (`direction: str`, `reference_swing: Dict[str, Any]`, `evidence_swings: List[Dict[str, Any]]`, `is_reconfirmation: bool`)
- **Production Caller:**
  - `confirm_transition_structure()` (lines 645-660)
- **Tests:** `backend/tests/test_v16_13_htf_confirmation.py::test_pre_invalidation_pivot_timing_rejected`, `test_valid_close_break_confirms_structure`

### `select_transition_break_reference`
- **Status:** IMPLEMENTED
- **Input:**
  - `state: HTFState`
  - `sequence: PostInvalidationSequence`
  - `known_swings: List[Dict[str, Any]]`
  - `bar_close_ms: int`
- **Output:** `Optional[Dict[str, Any]]` (Reference swing with `known_at_ms <= bar_close_ms`)
- **Production Caller:**
  - `confirm_transition_structure()` (line 655)
- **Tests:** `backend/tests/test_v16_13_htf_confirmation.py::test_no_bullish_choch_when_candle_close_below_reference`

### `build_htf_settings_fingerprint`
- **Status:** IMPLEMENTED
- **Input:** `settings: Dict[str, Any]`
- **Output:** `str` (e.g. `htf_cfg_7822cf8260d95ab0` - deterministic SHA256 of canonical sorted keys)
- **Production Caller:**
  - `evaluate_htf_structure()` (lines 940-950)
  - `build_cg01_snapshot()` in `cg01_evidence_adapter.py` (lines 310-315)
- **Tests:** `backend/tests/test_v16_13_htf_config_propagation.py::test_settings_fingerprint_deterministic_and_unique`

### `confirm_transition_structure`
- **Status:** IMPLEMENTED (Rewritten)
- **Input:** `state: HTFState`, `bar: Any`, `known_h: List[Dict]`, `known_l: List[Dict]`, `settings: HTFSettings`, `as_of_ms: int`
- **Output:** `Optional[HTFStepResult]`
- **Production Caller:**
  - `step_htf_state()` (line 768)
- **Tests:** `backend/tests/test_v16_13_htf_confirmation.py`, `backend/tests/test_v16_12_htf_state_machine.py`

---

## 2. Adapter & Context Layer

### `build_htf_context` (`backend/services/smc_regime_service.py`)
- **Status:** IMPLEMENTED
- **Input:** `candles: Sequence[Any]`, `timeframe: str`, `as_of_ms: Optional[int]`, `symbol: str`, `min_bars: Optional[int] = None`, `config: Optional[Any] = None`
- **Output:** `HTFContext` (includes `effective_settings`, `config_version`, `settings_fingerprint`)
- **Production Caller:**
  - `scalp_watcher_orchestrator.py::evaluate_cycle()` (lines 145-160)
  - `collector_service.py::update_htf_context()` (lines 530-550)
  - `cg01_engine.py::build_cg01_context()` (lines 182-183)
- **Tests:** `backend/tests/test_v16_13_htf_config_propagation.py::test_adapter_config_only_does_not_override_with_default`, `test_adapter_explicit_override_precedence`

### `build_cg01_context` (`backend/services/cg01_engine.py`)
- **Status:** IMPLEMENTED
- **Input:** `closed_context: Dict[str, Any]`, `now_ms: int`, `config: Optional[CG01Config]`
- **Output:** `Dict[str, Any]` (CG-01 context dictionary with `status`, `h4_bias`, `h1_bias`, `context_source`)
- **Production Caller:**
  - `scalp_watcher_orchestrator.py::evaluate_cycle()` (lines 165-175)
  - `evaluate_cg01_candidate()` (line 645)
- **Tests:** `backend/tests/test_v16_11_htf_structure.py::test_case_10`, `test_case_14`, `test_case_15`

### `build_cg01_snapshot` (`backend/services/cg01_evidence_adapter.py`)
- **Status:** IMPLEMENTED
- **Input:** candidate parameters, `htf_data: Dict[str, Any]`, `config: CG01Config`
- **Output:** `ScenarioSnapshot` with `htf_context` containing `effective_settings`, `config_version`, `settings_fingerprint`, `context_source`
- **Production Caller:**
  - `scalp_watcher_orchestrator.py::_arm_paper_candidate()` (lines 280-305)
- **Tests:** `backend/tests/test_v16_13_htf_config_propagation.py::test_htf_context_roundtrip_preserves_settings_and_fingerprint`, `backend/tests/test_v16_12_cg01_execution.py`

---

## 3. Frontend & Display Contracts

### `readStructuralReferencePrice` (`frontend/src/types/smcScenario.ts`)
- **Status:** IMPLEMENTED
- **Input:** `ref: StructuralReferenceDTO | number | null | undefined`
- **Output:** `number | null` (Strictly finite number or null, eliminates `$NaN`)
- **Production Caller:**
  - `frontend/src/components/EvidenceAccordion.tsx` (lines 160-165)
- **Tests:** `frontend/src/v16_13_htf_evidence.test.ts` (6 tests passing)

### `EvidenceAccordion` (`frontend/src/components/EvidenceAccordion.tsx`)
- **Status:** IMPLEMENTED
- **Input Props:** `htfContext?: HtfContextDTO | null`, `explanation`, `provenance`, etc.
- **Production Caller:**
  - `frontend/src/components/TradeDetailsDrawer.tsx` (line 314)
- **Tests:** `frontend/src/v16_13_htf_evidence.test.ts`

---

## 4. Execution & Guards Verification

### `ExecutionCoordinator` & `PaperBroker`
- **Status:** TESTED & VERIFIED
- **Coverage:**
  - Long Flow to TP (`test_long_full_execution_flow_with_htf_evidence`)
  - Long Flow to SL (`test_long_sl_execution_and_evidence_immutability`)
  - Short Flow to TP (`test_short_full_execution_flow_with_htf_evidence`)
  - Short Flow to SL (`test_short_sl_execution_and_evidence_immutability`)
  - Order Expiry Enforcement (`test_order_expiry_prevents_late_fill`)
  - Single Position Guard (`test_single_position_guard_prevents_concurrent_fill`)
  - News Blackout Guard (`test_news_guard_blackout_blocks_trade`)
- **Tests:** `backend/tests/test_v16_13_cg01_execution.py`, `backend/tests/test_v16_12_cg01_execution.py`
