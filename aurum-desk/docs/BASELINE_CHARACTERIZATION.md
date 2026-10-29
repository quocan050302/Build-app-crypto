# V17.9.0.1 — Independent Pre-Refactor Baseline Characterization

- **Baseline Commit SHA (V17.8.0.4 Pre-V17.9.0):** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Independent Oracle Module:** `backend/services/v17_9_0_1_baseline_oracle.py` (`IndependentPreRefactorBaselineOracle`)
- **Self-Referential:** `False` (zero imports or calls to `SharedStrategyEvaluator`)
- **Total Scenarios Evaluated:** `55` (11 categories x 5 production models)
- **Exact Parity Matches with `SharedStrategyEvaluator`:** `55/55` (`100.00%`)
- **Baseline Decisions SHA-256:** `92edbd743b1568c16cb01351d46d78db5e581569a8d2752c3ecb22863dc0b608`

## Category Coverage Matrix

| Category | Scenarios | Parity Matches | Divergences |
|---|---:|---:|---:|
| `LONG` | 5 | 5 | 0 |
| `SHORT` | 5 | 5 | 0 |
| `NO_TRADE` | 5 | 5 | 0 |
| `Rejected entry` | 5 | 5 | 0 |
| `Missing market data` | 5 | 5 | 0 |
| `Insufficient HTF history` | 5 | 5 | 0 |
| `Conflicting structure` | 5 | 5 | 0 |
| `Invalid SL/TP` | 5 | 5 | 0 |
| `Risk rejection` | 5 | 5 | 0 |
| `Session/time restrictions` | 5 | 5 | 0 |
| `Boundary conditions` | 5 | 5 | 0 |

## Model Coverage Matrix

| Production Model | Scenarios | Parity Matches | Divergences |
|---|---:|---:|---:|
| `SMC_M1_REVERSAL` | 11 | 11 | 0 |
| `SMC_M1_CONTINUATION` | 11 | 11 | 0 |
| `SMC_M1_FVG_CONTINUATION` | 11 | 11 | 0 |
| `SMC_M1_SWEEP_RECLAIM` | 11 | 11 | 0 |
| `CG01_SWEEP_MSS_FVG` | 11 | 11 | 0 |
