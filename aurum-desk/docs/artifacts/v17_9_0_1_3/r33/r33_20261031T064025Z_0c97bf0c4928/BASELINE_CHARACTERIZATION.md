# V17.9.0.1.2 — Independent Pre-Refactor Baseline Characterization

- **Baseline Commit SHA (V17.8.0.4 Pre-V17.9.0):** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Git Commit Object Verified:** `True`
- **Historical Source Files Verified:** `15/15` (`bundle_sha256=c9ea5033de46499cbbd7032d824eecfb6007f0f1bd44f5f25e2168a74ab9c261`)
- **Isolated Historical Subprocess Execution:** `PASS` (`ORIGINAL_HISTORICAL_RUNTIME_SUBPROCESS_VERIFIED`)
- **Independent Oracle Module:** `backend/services/v17_9_0_1_baseline_oracle.py` (`IndependentPreRefactorBaselineOracle`)
- **Self-Referential:** `False` (zero imports or calls to `SharedStrategyEvaluator`; executed in isolated subprocess against `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` files)
- **Total Characterization Scenarios Evaluated:** `55` (11 categories x 5 production models)
- **Exact Parity Matches with `SharedStrategyEvaluator`:** `55/55` (`100.00%`)
- **5-Way Cross-Runtime Parity Matches:** `55/55`
- **Real Historical Dataset Scenarios Evaluated:** `150` (`matches=150/150`)
- **Baseline Decisions SHA-256 (LF byte-exact):** `ac546763cf26a5a2acfa135f960b2749c69a69f1fedb34f1cc789ce5fa3f06bd`

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
