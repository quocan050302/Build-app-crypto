# AURUM DESK V17.8.0.1 — REPLAY CALIBRATION & HONEST DISCLOSURE REPORT (`DEF-178-13`)

**Defect ID:** `DEF-178-13`  
**Severity:** P1 (Quantitative Research Honesty & API Disclosure Defect)  
**Affected Files:**  
- `backend/services/unicorn_replay_service.py`
- `backend/services/stop_placement_research_service.py`
- `backend/main.py`
- `backend/tests/test_v17_8_runtime_integration.py`

---

## 1. Defect Description

Independent source audit (`DEF-178-13`) identified two weaknesses in the V17.8.0 replay research layer:
1. **Hardcoded `1.25×` Buffer Multiplier in Variant D:** `evaluate_opportunity_across_variants` in `backend/services/unicorn_replay_service.py` computed Variant D's stop loss using `buf_d = buf_c * 1.25` without disclosing whether Variant D was using an experimental fixed multiplier or an empirically calibrated P90 noise buffer.
2. **Empty Replay Endpoint Status:** `GET /api/v1/research/stop-placement/replay-comparison` in `backend/main.py` returned `status="AVAILABLE"` even when `total_plans_found == 0` and no replay comparison batch had been executed.

---

## 2. Honest Calibration & API Disclosure Implementation

### 2.1 Empirical P90 Noise Calibration (`calibrate_empirical_p90_noise_buffer`)
Added `NoiseCalibrationSample`, `NoiseCalibrationResult`, and `calibrate_empirical_p90_noise_buffer` in `backend/services/unicorn_replay_service.py`:
- Accepts historical post-entry adverse excursion samples (`NoiseCalibrationSample(opportunity_id, session, adverse_excursion_usd, atr_m1, spread, structurally_valid)`).
- Filters for structurally valid setups (`structurally_valid=True`, finite `atr_m1 > 0`, `adverse_excursion_usd >= 0`).
- When `valid_samples >= min_samples` (default `5`), computes the exact empirical 90th-percentile (`P90`) of `adverse_excursion_usd / atr_m1` and returns:
  - `calibration_mode = "EMPIRICAL_P90_NOISE_CALIBRATION"`
  - `p90_atr_m1_multiplier` and `p90_buffer_usd`
  - `disclosure` string documenting the sample size and P90 multiplier.
- When no sample set is provided or `valid_samples < min_samples`, explicitly returns:
  - `calibration_mode = "EXPERIMENTAL_FIXED_MULTIPLIER"`
  - `disclosure = "EXPERIMENTAL_FIXED_MULTIPLIER (1.25x Variant C buffer; insufficient empirical sample ... for P90 calibration)"`

### 2.2 Honest `/api/v1/research/stop-placement/replay-comparison` Status Codes
In `backend/main.py`:
- When `plans` is empty (`len(plans) == 0`): returns `status = "NO_REPLAY_RESULTS"`, `reason_code = "NO_COMPLETED_REPLAY_BATCH"`, `metrics = None`.
- When `plans` exist but none have `resolution_status in ("RESOLVED", "COMPLETED")`: returns `status = "INSUFFICIENT_REPLAY_DATA"`, `reason_code = "NO_RESOLVED_REPLAY_OUTCOMES"`, `metrics = None`.
- Only returns `status = "AVAILABLE"` when resolved replay outcomes exist, and always labels Variant D's default metadata with `calibration_mode = "EXPERIMENTAL_FIXED_MULTIPLIER"`.

---

## 3. Verification Evidence

- `test_def_178_13_honest_replay_calibration_and_endpoint` (`backend/tests/test_v17_8_runtime_integration.py` — Gate `G52`):
  - Verifies Variant D without samples sets `calibration_mode == "EXPERIMENTAL_FIXED_MULTIPLIER"`.
  - Verifies Variant D with 10 valid `NoiseCalibrationSample` records sets `calibration_mode == "EMPIRICAL_P90_NOISE_CALIBRATION"` and applies the calibrated P90 buffer.
  - Verifies `/api/v1/research/stop-placement/replay-comparison` returns `NO_REPLAY_RESULTS` on an empty DB and `INSUFFICIENT_REPLAY_DATA` when only unresolved plans exist.

