# V17.9.0.1.2 — Historical Baseline Original Runtime Report (`DEF-179012-C04` Closure)

- **Baseline Commit SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Historical Execution Authority:** `ORIGINAL_HISTORICAL_COMMIT_MODULES`
- **Copied Current Oracle Entry (`hist_oracle_entry.py`):** `False`
- **Forbidden Current-Tree Modules Absent (`sys.modules`):** `True`
- **Historical Orchestrator Loaded:** `ScalpWatcherOrchestrator`
- **Isolated Historical Modules Verified:** `11`
- **Characterization Scenarios Evaluated:** `55` (`parity_matches=55/55`)
- **Real Historical Dataset Scenarios Evaluated:** `150` (`parity_matches=150/150`)
- **Real Historical Scenarios JSONL SHA-256:** `097c2f7b6761d5d0e666f71db7825ffa1f9f7f0d455a9ba0242b18fcb49c0753` (`216198` bytes)

## 1. Loaded Historical Runtime & Domain Modules (`c356d6551e13cd6af61cea5c3ab1f4aae1548af2`)

| Module | Resolved File in Isolated Historical Tree | On-Disk SHA-256 |
|---|---|---|
| `domain_calculator` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/domain_calculator.py` | `1f074ee5c0ba8d0647fb422976b67be89a9f5d96eccb0bccdd5fac8abdafb94f` |
| `services.cg01_engine` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/services/cg01_engine.py` | `e96b3ab54cf80a4c984d666435875ff8b4c0ff0e7118c1a55680e72aba9a4e5a` |
| `services.liquidity_scenario_service` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/services/liquidity_scenario_service.py` | `4e0800f300239cd625ac5b2412162623fe1173c7de900f2e07c22062cdfd3e05` |
| `services.scalp_m1_model_service` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/services/scalp_m1_model_service.py` | `c538eab1f753e5ef0b9a97c6114e4b9920251777a75866f9465972f8313aab99` |
| `services.scalp_watcher_orchestrator` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/services/scalp_watcher_orchestrator.py` | `efd1dec4b38843583652101a6227c41255b992f8c13a54898a0ba3036c60056f` |
| `services.smc_liquidity_service` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/services/smc_liquidity_service.py` | `4fd49d4c6e93f3dd6cd35fa00a8bee33a0c06ab19e4a7e12437dd403935ddb2e` |
| `services.stop_placement_research_service` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/services/stop_placement_research_service.py` | `9bd9a4222f4933e7274b120063082205cf35010a3d72dbccca722aac1e129842` |
| `services.stop_placement_service` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/services/stop_placement_service.py` | `7c4371b6aa80d2977c5b80b498c3095b341e20f1f609714b0dd7323eeb07256f` |
| `services.unicorn_evidence_service` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/services/unicorn_evidence_service.py` | `6e6225b7243cf734674599e18798bef1d773ee7a2aa78eb8c26d0abffca6dc78` |
| `services.v17_8_backtest_runner` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/services/v17_8_backtest_runner.py` | `f0f017d8dc2750b334dbbc4309c1534816b94cee4e3dfc9044a00aa43e9c9872` |
| `smc_engine` | `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_33364/aurum-desk/backend/smc_engine.py` | `aff32b03f7938b98c7300a84aa0ba4ec3623e9ef0a1fa5d9281693b818b9ca7b` |

## 2. Real Historical Market Scenarios Summary (`150` Evaluations = `30` Timestamps x `5` Models)

| Scenario ID | Timestamp (ms) | Model | Direction | Status | Entry | SL | TP | Input Hash | Config Hash | Parity Match |
|---|---:|---|---|---|---:|---:|---:|---|---|---|
| `HIST_1791438480000_SMC_M1_REVERSAL` | `1791438480000` | `SMC_M1_REVERSAL` | `LONG` | `MISSING_MARKET_DATA` | `None` | `None` | `None` | `1c273cc5cf71...` | `d39f13332060...` | `True` |
| `HIST_1791438480000_SMC_M1_CONTINUATION` | `1791438480000` | `SMC_M1_CONTINUATION` | `LONG` | `MISSING_MARKET_DATA` | `None` | `None` | `None` | `b44029ffecc1...` | `d39f13332060...` | `True` |
| `HIST_1791438480000_SMC_M1_FVG_CONTINUATION` | `1791438480000` | `SMC_M1_FVG_CONTINUATION` | `LONG` | `MISSING_MARKET_DATA` | `None` | `None` | `None` | `bc87b1be2136...` | `d39f13332060...` | `True` |
| `HIST_1791438480000_SMC_M1_SWEEP_RECLAIM` | `1791438480000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `MISSING_MARKET_DATA` | `None` | `None` | `None` | `8c572ba905f0...` | `d39f13332060...` | `True` |
| `HIST_1791438480000_CG01_SWEEP_MSS_FVG` | `1791438480000` | `CG01_SWEEP_MSS_FVG` | `LONG` | `MISSING_MARKET_DATA` | `None` | `None` | `None` | `02eccfea3295...` | `d39f13332060...` | `True` |
| `HIST_1791439800000_SMC_M1_REVERSAL` | `1791439800000` | `SMC_M1_REVERSAL` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.41` | `4122.5` | `4130.75` | `fdd7b9abaabd...` | `d39f13332060...` | `True` |
| `HIST_1791439800000_SMC_M1_CONTINUATION` | `1791439800000` | `SMC_M1_CONTINUATION` | `LONG` | `WAITING_CONFIRMATION` | `4125.41` | `None` | `None` | `e9b00b82022f...` | `d39f13332060...` | `True` |
| `HIST_1791439800000_SMC_M1_FVG_CONTINUATION` | `1791439800000` | `SMC_M1_FVG_CONTINUATION` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.41` | `4122.5` | `4130.75` | `31dab9d6caad...` | `d39f13332060...` | `True` |
| `HIST_1791439800000_SMC_M1_SWEEP_RECLAIM` | `1791439800000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.41` | `4122.5` | `4130.75` | `686a6b5f9eb8...` | `d39f13332060...` | `True` |
| `HIST_1791439800000_CG01_SWEEP_MSS_FVG` | `1791439800000` | `CG01_SWEEP_MSS_FVG` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.41` | `4122.5` | `4130.75` | `23ae847f6670...` | `d39f13332060...` | `True` |
| `HIST_1791440100000_SMC_M1_REVERSAL` | `1791440100000` | `SMC_M1_REVERSAL` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.93` | `4123.36` | `4130.75` | `50644ec5d8a0...` | `d39f13332060...` | `True` |
| `HIST_1791440100000_SMC_M1_CONTINUATION` | `1791440100000` | `SMC_M1_CONTINUATION` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.93` | `4123.36` | `4130.75` | `32c641d51d8d...` | `d39f13332060...` | `True` |
| `HIST_1791440100000_SMC_M1_FVG_CONTINUATION` | `1791440100000` | `SMC_M1_FVG_CONTINUATION` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.93` | `4123.36` | `4130.75` | `164c00e022f0...` | `d39f13332060...` | `True` |
| `HIST_1791440100000_SMC_M1_SWEEP_RECLAIM` | `1791440100000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.93` | `4123.36` | `4130.75` | `641c41ffb652...` | `d39f13332060...` | `True` |
| `HIST_1791440100000_CG01_SWEEP_MSS_FVG` | `1791440100000` | `CG01_SWEEP_MSS_FVG` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.93` | `4123.36` | `4130.75` | `bbbee06efeeb...` | `d39f13332060...` | `True` |
| `HIST_1791440160000_SMC_M1_REVERSAL` | `1791440160000` | `SMC_M1_REVERSAL` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.16` | `4123.43` | `4130.75` | `5bafda2d9b3f...` | `d39f13332060...` | `True` |
| `HIST_1791440160000_SMC_M1_CONTINUATION` | `1791440160000` | `SMC_M1_CONTINUATION` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.16` | `4123.63` | `4130.75` | `db607acba650...` | `d39f13332060...` | `True` |
| `HIST_1791440160000_SMC_M1_FVG_CONTINUATION` | `1791440160000` | `SMC_M1_FVG_CONTINUATION` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.16` | `4123.63` | `4130.75` | `b064c96a0147...` | `d39f13332060...` | `True` |
| `HIST_1791440160000_SMC_M1_SWEEP_RECLAIM` | `1791440160000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.16` | `4123.43` | `4130.75` | `e7b88c6ee9cd...` | `d39f13332060...` | `True` |
| `HIST_1791440160000_CG01_SWEEP_MSS_FVG` | `1791440160000` | `CG01_SWEEP_MSS_FVG` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4125.16` | `4123.63` | `4130.75` | `5d4a26143d95...` | `d39f13332060...` | `True` |
| `HIST_1791440400000_SMC_M1_REVERSAL` | `1791440400000` | `SMC_M1_REVERSAL` | `LONG` | `WAITING_CONFIRMATION` | `4123.66` | `None` | `None` | `38ed25725ab7...` | `d39f13332060...` | `True` |
| `HIST_1791440400000_SMC_M1_CONTINUATION` | `1791440400000` | `SMC_M1_CONTINUATION` | `SHORT` | `VALID_SETUP_REJECTED_BY_RISK` | `4123.46` | `4126.4` | `4121.54` | `6530a90c851a...` | `d39f13332060...` | `True` |
| `HIST_1791440400000_SMC_M1_FVG_CONTINUATION` | `1791440400000` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | `VALID_SETUP_REJECTED_BY_RISK` | `4123.46` | `4131.15` | `4121.54` | `91a1c6b1ce2f...` | `d39f13332060...` | `True` |
| `HIST_1791440400000_SMC_M1_SWEEP_RECLAIM` | `1791440400000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `WAITING_CONFIRMATION` | `4123.66` | `None` | `None` | `4c591f66b29a...` | `d39f13332060...` | `True` |
| `HIST_1791440400000_CG01_SWEEP_MSS_FVG` | `1791440400000` | `CG01_SWEEP_MSS_FVG` | `SHORT` | `VALID_SETUP_REJECTED_BY_RISK` | `4123.46` | `4131.15` | `4121.54` | `6b4635afca1e...` | `d39f13332060...` | `True` |
| `HIST_1791441540000_SMC_M1_REVERSAL` | `1791441540000` | `SMC_M1_REVERSAL` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4121.22` | `4118.58` | `4125.79` | `a3aa15a4a727...` | `d39f13332060...` | `True` |
| `HIST_1791441540000_SMC_M1_CONTINUATION` | `1791441540000` | `SMC_M1_CONTINUATION` | `LONG` | `WAITING_CONFIRMATION` | `4121.22` | `None` | `None` | `2da92ebd7835...` | `d39f13332060...` | `True` |
| `HIST_1791441540000_SMC_M1_FVG_CONTINUATION` | `1791441540000` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | `VALID_SETUP_REJECTED_BY_RISK` | `4121.02` | `4126.42` | `4119.01` | `462f924a133a...` | `d39f13332060...` | `True` |
| `HIST_1791441540000_SMC_M1_SWEEP_RECLAIM` | `1791441540000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4121.22` | `4118.58` | `4125.79` | `91b3d3716a28...` | `d39f13332060...` | `True` |
| `HIST_1791441540000_CG01_SWEEP_MSS_FVG` | `1791441540000` | `CG01_SWEEP_MSS_FVG` | `SHORT` | `VALID_SETUP_REJECTED_BY_RISK` | `4121.02` | `4126.42` | `4119.01` | `711f2d71856a...` | `d39f13332060...` | `True` |
| `HIST_1791448740000_SMC_M1_REVERSAL` | `1791448740000` | `SMC_M1_REVERSAL` | `SHORT` | `VALID_SETUP_REJECTED_BY_RISK` | `4130.28` | `4132.55` | `4127.96` | `0c70c070b812...` | `d39f13332060...` | `True` |
| `HIST_1791448740000_SMC_M1_CONTINUATION` | `1791448740000` | `SMC_M1_CONTINUATION` | `LONG` | `WAITING_CONFIRMATION` | `4130.48` | `None` | `None` | `be8166395501...` | `d39f13332060...` | `True` |
| `HIST_1791448740000_SMC_M1_FVG_CONTINUATION` | `1791448740000` | `SMC_M1_FVG_CONTINUATION` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4130.48` | `4123.15` | `4133.45` | `171e11f41afa...` | `d39f13332060...` | `True` |
| `HIST_1791448740000_SMC_M1_SWEEP_RECLAIM` | `1791448740000` | `SMC_M1_SWEEP_RECLAIM` | `SHORT` | `VALID_SETUP_REJECTED_BY_RISK` | `4130.28` | `4132.55` | `4127.96` | `5f82a3d17e6e...` | `d39f13332060...` | `True` |
| `HIST_1791448740000_CG01_SWEEP_MSS_FVG` | `1791448740000` | `CG01_SWEEP_MSS_FVG` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4130.48` | `4123.15` | `4133.45` | `70c05479eff0...` | `d39f13332060...` | `True` |
| `HIST_1791681540000_SMC_M1_REVERSAL` | `1791681540000` | `SMC_M1_REVERSAL` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4196.6` | `4195.03` | `None` | `aeccd82caac1...` | `d39f13332060...` | `True` |
| `HIST_1791681540000_SMC_M1_CONTINUATION` | `1791681540000` | `SMC_M1_CONTINUATION` | `SHORT` | `WAITING_CONFIRMATION` | `4196.4` | `None` | `None` | `78d1043fefdd...` | `d39f13332060...` | `True` |
| `HIST_1791681540000_SMC_M1_FVG_CONTINUATION` | `1791681540000` | `SMC_M1_FVG_CONTINUATION` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4196.6` | `4195.03` | `None` | `1ebdd7ab883a...` | `d39f13332060...` | `True` |
| `HIST_1791681540000_SMC_M1_SWEEP_RECLAIM` | `1791681540000` | `SMC_M1_SWEEP_RECLAIM` | `SHORT` | `WAITING_CONFIRMATION` | `4196.4` | `None` | `None` | `e099845d28ba...` | `d39f13332060...` | `True` |
| `HIST_1791681540000_CG01_SWEEP_MSS_FVG` | `1791681540000` | `CG01_SWEEP_MSS_FVG` | `LONG` | `VALID_SETUP_REJECTED_BY_RISK` | `4196.6` | `4195.03` | `None` | `cc81e91c8a9e...` | `d39f13332060...` | `True` |
