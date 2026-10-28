# AURUM DESK V17.8.0.1 — FEATURE FLAG PERSISTENCE & POLICY LIFECYCLE PROOF (`DEF-178-08`)

**Defect ID:** `DEF-178-08`  
**Severity:** P0 (Release Blocking Configuration & Safety Defect)  
**Affected Files:**  
- `backend/services/stop_placement_types.py`
- `backend/services/stop_placement_policy_service.py`
- `backend/schemas.py`
- `backend/main.py`
- `backend/tests/test_v17_8_runtime_integration.py`

---

## 1. Defect Description

In V17.8.0, `StopPlacementPolicy` (`backend/services/stop_placement_types.py`) declared four V17.8 fields:
- `v18_structural_intelligence_enabled: bool = False`
- `v18_unicorn_detection_enabled: bool = False`
- `v18_noise_research_enabled: bool = False`
- `v18_execution_policy_version: int = 1`

However, `resolve_stop_placement_policy` and `update_stop_placement_policy` (`backend/services/stop_placement_policy_service.py`) did not read, validate, or persist these fields in `system_config`, and `StopPlacementPolicyResponse` / `StopPlacementPolicyUpdate` (`backend/schemas.py`) did not expose them via `/api/v1/policy/stop-placement`.

---

## 2. Complete Lifecycle Implementation

```text
DATABASE CONFIG (system_config)
       ↓
POLICY RESOLVER (resolve_stop_placement_policy)
       ↓
VALIDATED POLICY OBJECT (StopPlacementPolicy)
       ↓
POLICY API (GET/PUT /api/v1/policy/stop-placement)
       ↓
STRATEGY EVALUATOR (ScalpM1ModelService)
       ↓
STOP PLACEMENT ENGINE (resolve_stop_anchor / apply_stop_placement_to_candidate)
       ↓
PAPER EXECUTION (ScalpWatcherOrchestrator -> ExecutionCoordinator)
```

### Key Safety & Validation Invariants:
1. **Safe Default-Off Values:**
   - `mode = "SHADOW_COMPARE"`
   - `v18_structural_intelligence_enabled = False`
   - `v18_unicorn_detection_enabled = False`
   - `v18_noise_research_enabled = False`
   - `v18_execution_policy_version = 1`
2. **Strict Boolean & Integer Validation:**
   - `_parse_strict_bool_config` and `_validate_strict_bool` require `type(raw) is bool` on API updates (`"true"`, `1`, `0`, `None` are rejected with HTTP 400 `StopPolicyValidationError`).
   - `_validate_strict_positive_int` requires `type(raw) is int` (`not isinstance(raw, bool)`) and `raw >= 1`.
3. **Compare-and-Swap (CAS) & No-Op Semantics:**
   - `expected_version` is checked under `_POLICY_UPDATE_LOCK` against persisted `stop_policy_version`; mismatches raise `StopPolicyConflictError` (HTTP 409).
   - True no-op updates (where all effective values match current persisted state) return the current policy without incrementing `version`.
4. **LIVE Scope Prohibition:**
   - Both `application_scope` and `scope` fields are validated; any non-`PAPER` value (`"LIVE"`, etc.) raises `StopPolicyValidationError` (`LIVE_SCOPE_FORBIDDEN`).

---

## 3. Explicit Execution Mode vs Feature Flag Matrix

| `mode` | `v18_structural_intelligence_enabled` | `v18_unicorn_detection_enabled` | Runtime Behavior |
|---|---|---|---|
| `LEGACY` | `False` / `True` | `False` / `True` | Executes **Variant A** (Legacy FVG Edge + 5 ticks). V17.8 structural SL is not executed. |
| `SHADOW_COMPARE` | `False` / `True` | `True` | Executes **Variant A** (Legacy SL) while computing Variants B and C (including Unicorn evidence) in shadow observation and persisting comparison plans. |
| `PAPER_STRUCTURAL` | `False` | `False` / `True` | Executes **V17.7-style structural stop** (`PROTECTED_SWING` / `SWEEP_EXTREME`) without enabling V17.8 Unicorn Breaker override or displacement-origin fallback. |
| `PAPER_STRUCTURAL` | `True` | `True` | Executes **V17.8 structural stop** (`BREAKER_DISTAL_BOUNDARY` when causal Unicorn evidence is confirmed, or `FVG_DISPLACEMENT_ORIGIN` / `PROTECTED_SWING` with dynamic ATR/spread protection). |

---

## 4. Verification Evidence

- `test_def_178_08_feature_flags_full_lifecycle_and_api` (`backend/tests/test_v17_8_runtime_integration.py`): Verifies default-off state, intentional enablement, DB persistence across new session reload, no-op version stability, CAS conflict rejection (409), invalid boolean/int rejection (400), LIVE scope rejection (400), and flag disablement.
- `test_def_178_08_paper_structural_without_v18_flag_uses_v17_7_anchor` (`backend/tests/test_v17_8_runtime_integration.py`): Verifies that selecting `PAPER_STRUCTURAL` while `v18_structural_intelligence_enabled=False` uses V17.7 `PROTECTED_SWING` and does not silently activate V17.8 Unicorn Breaker stops.

