# AURUM DESK V17.8.0.1 — UNICORN DETECTOR CONTRACT FIX (`DEF-178-07`)

**Defect ID:** `DEF-178-07`  
**Severity:** P0 (Release Blocking Runtime Defect)  
**Affected Files:**  
- `backend/services/unicorn_evidence_service.py`
- `backend/services/scalp_m1_model_service.py`
- `backend/tests/test_v17_8_runtime_integration.py`

---

## 1. Defect Description & Reproduction

In `ScalpM1ModelService._evaluate_fvg_continuation_model` (`backend/services/scalp_m1_model_service.py`), when `stop_policy.v18_unicorn_detection_enabled` or `stop_policy.v18_structural_intelligence_enabled` was `True`, the evaluator invoked:

```python
unicorn_ev = detect_unicorn_evidence(
    symbol="XAUUSDT",
    direction=direction,
    closed_bars=closed_1m,
    as_of_ms=now_ms,
    selected_fvg=selected_fvg_desc,
    confirmed_swings=(sh_list, sl_list),
    tick_size=cfg.tick_size,
)
```

However, `detect_unicorn_evidence` in `backend/services/unicorn_evidence_service.py` previously accepted `candidate_fvgs: Optional[List[Dict[str, Any]]] = None` and did **not** accept `selected_fvg`. Enabling Unicorn detection in the real M1 FVG Continuation evaluator path therefore raised `TypeError: detect_unicorn_evidence() got an unexpected keyword argument 'selected_fvg'`.

---

## 2. Canonical API Contract Implemented

`detect_unicorn_evidence` in `backend/services/unicorn_evidence_service.py` now enforces a single canonical contract supporting both `selected_fvg` (authoritative active candidate FVG) and `candidate_fvgs` (for standalone research/unit callers):

```python
def detect_unicorn_evidence(
    closed_bars: Sequence[Any],
    direction: str,
    as_of_ms: int,
    *,
    symbol: str = "XAUUSDT",
    timeframe: str = "1M",
    min_overlap_ticks: float = 1.0,
    tick_size: float = 0.01,
    confirmed_swings: Optional[Any] = None,
    selected_fvg: Optional[Dict[str, Any]] = None,
    candidate_fvgs: Optional[List[Dict[str, Any]]] = None,
    htf_evidence: Optional[Dict[str, Any]] = None,
    min_displacement_body_atr: float = 0.40,
) -> UnicornEvidence:
```

### Fail-Closed Invariants Enforced when `selected_fvg` is Provided:
1. **No Unrelated FVG Substitution:** When `selected_fvg` is supplied by the strategy evaluator, `detect_unicorn_evidence` evaluates **only** `selected_fvg`. It never falls back to scanning `closed_bars` for a different FVG.
2. **Type & Geometry Validation:** If `selected_fvg` is not a dict or has missing/invalid `bottom`/`top` (`fvg_high <= fvg_low`), it fails closed with `UnicornValidityStatus.NO_FVG` and `invalidation_price=None`.
3. **Direction Binding:** If `selected_fvg.direction` disagrees with the candidate `direction`, it fails closed with `UnicornValidityStatus.WRONG_DIRECTION_OVERLAP`.
4. **Anti-Lookahead Timestamp Validation:** If `selected_fvg.confirmed_at_ms > as_of_ms` or `selected_fvg.known_at_ms > as_of_ms`, it fails closed with `UnicornValidityStatus.FUTURE_DATA_REJECTED`.
5. **Consumed / Mitigated FVG Rejection:** If `selected_fvg` is marked `consumed=True`, `invalidated=True`, or closed bars after `fvg_confirmed_ms` fully closed beyond the FVG distal edge prior to the retest, it fails closed with `UnicornValidityStatus.CONSUMED_OR_INVALIDATED_FVG`.
6. **Provenance Preservation:** `selected_fvg_id` and `fvg_id` are preserved verbatim on the returned `UnicornEvidence`.

---

## 3. Verification Evidence

- **`test_def_178_07_unicorn_detector_canonical_selected_fvg_contract`** (`backend/tests/test_v17_8_runtime_integration.py`):
  - Exercises `ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate()` with `v18_unicorn_detection_enabled=True` and `v18_structural_intelligence_enabled=True`.
  - Confirms deterministic candidate generation with `BREAKER_DISTAL_BOUNDARY` stop placement and matching `selected_fvg_id`.
  - Confirms non-overlapping `selected_fvg` does not silently substitute another FVG from the bar series.
- **`test_def_178_07_negative_control_interface_breakage_caught`** (`backend/tests/test_v17_8_runtime_integration.py`):
  - Negative control proving that if a caller invokes an incompatible signature without `selected_fvg`, the runtime harness immediately catches the `TypeError`.

