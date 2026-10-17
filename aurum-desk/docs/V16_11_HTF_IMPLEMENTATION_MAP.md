# V16.11 HTF CAUSAL MARKET STRUCTURE — IMPLEMENTATION MAP

**Module:** Higher-Timeframe (HTF) Market Structure, Provenance, and Multi-Timeframe Policy  
**Version:** V16.11  
**Baseline Commit:** `7a2f37c274167892f7737717517db708604d4343`  
**Target Branch:** `fix/v14-1-rr-flow`  

---

## 1. Architectural Overview

```
Raw Candles (SQLite / Live WSS / Replay Feed)
  │
  ▼
`filter_closed_candles` (causal time boundary: open_ms + duration_ms <= as_of_ms, reject is_closed=False)
  │
  ▼
`identify_causal_swings` (pivot occurrence index `i`, confirmation at `i + right_bars` close, tick tolerance EQH/EQL)
  │
  ▼
`evaluate_htf_structure` (chronological state evolution, protected high/low tracking, BOS/CHoCH classification, sweep rejection vs close break)
  │
  ▼
`HTFStructureSnapshot` & `HTFContext` (per-timeframe facts: bias, phase, reference_levels, protected_levels, event known_at_ms)
  │
  ├─────────────────────────────────────────────────┐
  ▼                                                 ▼
`CollectorService.update_htf_context`             `build_cg01_context`
- Queries `get_closed_candles`                    - Evaluates H4 and H1 at identical as_of
- Evaluates D, 4H, 1H at same as_of_ms            - Calls `resolve_cg01_direction_context`
- Publishes atomic `self.htf_contexts`            - Passes facts to `evaluate_cg01_candidate`
  │                                                 │
  ▼                                                 ▼
API / WebSocket (`/api/v1/market/matrix`)         CG-01 Engine Execution & Lifecycle
```

---

## 2. Component & Function Implementation Matrix

### 2.1 `backend/services/htf_structure_service.py` (New Canonical HTF Authority)

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Compatibility |
|:---|:---|:---|:---|:---|:---|:---|
| `filter_closed_candles` | `candles`, `timeframe`, `as_of_ms` | `List[Candle]` | `evaluate_htf_structure`, `build_htf_context` | Filters only bars where `open_time + duration <= as_of_ms` and `is_closed != False`. Sorts ascending. | `test_case_07`, `test_case_13` | Accepts both ORM objects and dicts. |
| `identify_causal_swings` | `closed_candles`, `timeframe`, `left_bars`, `right_bars`, `as_of_ms`, `tick_size` | `Tuple[List[Dict], List[Dict]]` | `evaluate_htf_structure`, `build_cg01_context` | Pivot at `i` requires 2 left bars and 2 closed right bars (`i + 2`). `known_at_ms` set to confirmation close. Applies tick tolerance for `EQH`/`EQL`. | `test_case_06`, `test_case_08` | 100% causal; no lookahead. |
| `evaluate_htf_structure` | `candles`, `timeframe`, `as_of_ms`, `config`, `symbol` | `HTFStructureSnapshot` | `build_htf_context`, replay drivers | Pure chronological state machine. Tracks protected high/low. Breaches by close trigger `TRANSITION`. Wick sweeps recorded without flipping external bias. No SMA fallback. | `test_case_01`, `test_case_02`, `test_case_03`, `test_case_04`, `test_case_05`, `test_case_12` | Returns complete rich snapshot with legacy compatible strings. |
| `resolve_cg01_direction_context` | `h4_ctx`, `h1_ctx`, `range_pos`, `config` | `Dict[str, Any]` | `build_cg01_context`, `evaluate_cg01_candidate` | Disentangles FACTS, RELATION, and ENTRY POLICY. H1 is primary execution context. H1 bearish in H4 bullish marked `DIVERGENT` (pullback). Insufficient HTF marked `NOT_READY`. | `test_case_09`, `test_case_14`, `test_case_15` | Returns explicit reasons and directions. |

---

### 2.2 `backend/services/smc_regime_service.py`

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Compatibility |
|:---|:---|:---|:---|:---|:---|:---|
| `HTFContext` (dataclass) | (fields) | `HTFContext` instance | Widely across backend | Added additive fields: `last_confirmed_bias`, `phase`, `as_of_ms`, `last_bar_open_ms`, `last_bar_close_ms`, `reference_high`, `reference_low`, `protected_high`, `protected_low`, `latest_structure_event`, `indicator_bias`, `algorithm_version`, `data_status`. | `test_case_01`, `test_case_02`, `test_case_11` | Preserves all existing keys in `to_dict()`. |
| `build_htf_context` | `closed_candles`, `timeframe`, `min_bars`, `as_of_ms`, `config`, `symbol` | `HTFContext` | `collector_service`, `cg01_engine`, `scalp_watcher_orchestrator`, `smc_scenario_service` | Delegates directly to `evaluate_htf_structure`. Calculates true `last_bar_close_ms = open_ms + duration_ms`. Rejects SMA fallback. | `test_case_01` to `test_case_15` | 100% backward compatible keyword arguments. |
| `h1_alignment_from` | `h4: HTFContext`, `h1: HTFContext` | `str` (`ALIGNED`, `DIVERGENT`, `UNKNOWN`) | `collector_service`, `scalp_watcher_orchestrator`, `smc_scenario_service` | Supports `TRANSITION` and `RANGE` phases without false consensus. | `test_case_09` | Preserves return types expected by callers. |

---

### 2.3 `backend/smc_engine.py`

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Compatibility |
|:---|:---|:---|:---|:---|:---|:---|
| `_label_swings` | `swings`, `kind`, `tick_size` | `None` (in-place) | `identify_pivots` | Added tick tolerance (`tick_size + 1e-6`) to classify `EQH` and `EQL`. | `test_case_06` | Default tick size preserves existing behavior. |
| `determine_causal_trend_at` | `candles`, `candle_idx`, `swing_highs`, `swing_lows`, `current_time` | `str` (`BULLISH`, `BEARISH`, `RANGING`, `UNKNOWN`) | `determine_trend`, `detect_sweeps_and_breaks_causal`, `smc_scenario_service` | Fixed early return bug: when `sh2>sh1 and sl2>sl1`, verifies `close >= sl2` (if broken, returns RANGING). When `sh2<sh1 and sl2<sl1`, verifies `close <= sh2` (if broken, returns RANGING). Eliminated SMA fallback for market structure (returns `UNKNOWN`). | `test_case_02`, `test_case_03`, `test_case_05` | Signature and return strings unchanged. |
| `determine_trend` | `candles`, `swing_highs`, `swing_lows`, `as_of_ms`, `timeframe` | `str` | `collector_service`, `main.py`, `replay_engine.py` | Replaced artificial `+ 100000000` with actual `as_of_ms` or candle close boundary (`timestamp + duration_ms`). | `test_case_10`, `test_case_12` | Signature compatible; optional kwargs added. |

---

### 2.4 `backend/crud.py`

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Compatibility |
|:---|:---|:---|:---|:---|:---|:---|
| `get_candles` | `db`, `symbol`, `timeframe`, `limit`, `ascending`, `cutoff_ms`, `start_ms`, `closed_only` | `List[Candle]` | All database queries | Added optional `closed_only: bool = False` filter for `models.Candle.is_closed.is_(True)`. | `test_case_13` | Default `False` guarantees zero disruption to existing callers and real-time feeds. |
| `get_closed_candles` | `db`, `symbol`, `timeframe`, `limit`, `ascending`, `cutoff_ms`, `start_ms` | `List[Candle]` | `collector_service.update_htf_context` | Explicit helper fetching closed-only candles for HTF context updates. | `test_case_13` | New helper; no existing callers affected. |

---

### 2.5 `backend/services/collector_service.py`

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Compatibility |
|:---|:---|:---|:---|:---|:---|:---|
| `CollectorService.__init__` | None | None | Application lifecycle | Added `self.htf_contexts: Dict[str, Any] = {}`. | N/A | Additive field. |
| `update_htf_context` | `symbol: str = "XAUUSDT"` | `None` | Background collector thread, startup | Queries closed-only candles (`get_closed_candles`), evaluates D, 4H, 1H at a unified `as_of_ms`, calls canonical `build_htf_context`, and publishes `htf_contexts` atomically. | `test_case_13` | Updates `d_bias`, `h4_bias`, `d_4h_bias`, and `h1_alignment` with causal values. |

---

### 2.6 `backend/services/cg01_engine.py`

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Compatibility |
|:---|:---|:---|:---|:---|:---|:---|
| `build_cg01_context` | `closed_context`, `now_ms`, `config` | `Dict[str, Any]` | `evaluate_cg01_candidate`, replay driver | Passes `as_of_ms=now_ms` to `build_htf_context`. Calls `resolve_cg01_direction_context` to attach multi-TF relation and direction policy. | `test_case_09`, `test_case_10` | Additive keys in returned dictionary. |
| `evaluate_cg01_candidate` | `context`, `current_quote`, `config`, `now_ms`, `capital`, `risk_pct`, `costs` | `CG01Decision` | Orchestrator, coordinator, replay driver | Uses `directions_to_evaluate` from `direction_context`. If HTF structure is UNKNOWN or insufficient, returns OBSERVING with safe rationale instead of blindly opening both directions. Formats newbie drawer Vietnamese explanation with H4, H1, and relation. | `test_case_14`, `test_case_15` | Core entry model triggers, FVG, MSS, and SL/TP calculation remain intact. |

---

### 2.7 `backend/services/scalp_watcher_orchestrator.py`

| Function / Symbol | Inputs | Outputs | Callers | Logic Changes & Invariants | Associated Tests | Compatibility |
|:---|:---|:---|:---|:---|:---|:---|
| `evaluate_cycle` | (cycle parameters) | Cycle results | Orchestrator loop, replay driver | When `candles_4h` and `candles_1h` are injected, evaluates HTF context causally via `build_htf_context` instead of leaking live `collector_service.d_4h_bias`. | `test_case_10` | Eliminates live singleton leak in replay/tests. |

---

## 3. Verification & Mandatory Gates Status

- **Gate A (Closed-Candle Causality):** PASS (`test_case_07`, `test_case_13`)
- **Gate B (Swing & Structure State):** PASS (`test_case_01`, `test_case_02`, `test_case_03`, `test_case_04`)
- **Gate C (Provenance & No SMA Fallback):** PASS (`test_case_05`)
- **Gate D (Collector & CG-01 Consistency):** PASS (`test_case_09`, `test_case_10`)
- **Gate E (Positive Entry Compatibility):** PASS (`test_case_14`, `test_case_15`)
- **Gate F (Legacy Regression):** PASS (All 180 legacy V16 tests pass)
