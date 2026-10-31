# V17.9.0.1.2 — Historical Baseline Independent Execution Report

- **Baseline Commit SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Isolated Execution Status:** `PASS` (`ORIGINAL_HISTORICAL_RUNTIME_SUBPROCESS_VERIFIED`)
- **Extracted Historical Backend Directory:** `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_88484/aurum-desk/backend`
- **Historical Dependency Bundle SHA-256:** `c9ea5033de46499cbbd7032d824eecfb6007f0f1bd44f5f25e2168a74ab9c261`
- **Characterization Scenarios Evaluated:** `55` (`parity_matches=55/55`, `divergences=0`)
- **Real Historical Dataset Scenarios Evaluated:** `150` (`parity_matches=150/150`)

## 1. Isolated Historical Dependency Verification

| Module | Loaded From Historical Tree | On-Disk SHA-256 |
|---|---|---|
| `domain_calculator` | `True` | `1f074ee5c0ba8d0647fb422976b67be89a9f5d96eccb0bccdd5fac8abdafb94f` |
| `services.cg01_engine` | `True` | `e96b3ab54cf80a4c984d666435875ff8b4c0ff0e7118c1a55680e72aba9a4e5a` |
| `services.liquidity_scenario_service` | `True` | `4e0800f300239cd625ac5b2412162623fe1173c7de900f2e07c22062cdfd3e05` |
| `services.market_shadow_service` | `True` | `59b85954be4fe826ccc93f877a6745651cdb82ffdb25919bf6ac7d5590541dea` |
| `services.scalp_m1_model_service` | `True` | `c538eab1f753e5ef0b9a97c6114e4b9920251777a75866f9465972f8313aab99` |
| `services.scalp_watcher_orchestrator` | `True` | `efd1dec4b38843583652101a6227c41255b992f8c13a54898a0ba3036c60056f` |
| `services.smc_liquidity_service` | `True` | `4fd49d4c6e93f3dd6cd35fa00a8bee33a0c06ab19e4a7e12437dd403935ddb2e` |
| `services.stop_placement_research_service` | `True` | `9bd9a4222f4933e7274b120063082205cf35010a3d72dbccca722aac1e129842` |
| `services.stop_placement_service` | `True` | `7c4371b6aa80d2977c5b80b498c3095b341e20f1f609714b0dd7323eeb07256f` |
| `services.unicorn_evidence_service` | `True` | `6e6225b7243cf734674599e18798bef1d773ee7a2aa78eb8c26d0abffca6dc78` |
| `services.v17_8_backtest_runner` | `True` | `f0f017d8dc2750b334dbbc4309c1534816b94cee4e3dfc9044a00aa43e9c9872` |
| `smc_engine` | `True` | `aff32b03f7938b98c7300a84aa0ba4ec3623e9ef0a1fa5d9281693b818b9ca7b` |

## 2. Representative Real-Data Historical Baseline Decisions

| Scenario ID | Timestamp (ms) | Model | Direction | Status | Entry | SL | TP | Decision Fingerprint | Parity Match |
|---|---:|---|---|---|---:|---:|---:|---|---|
| `HIST_1791438480000_SMC_M1_REVERSAL` | `1791438480000` | `SMC_M1_REVERSAL` | `LONG` | `MISSING_MARKET_DATA` | `None` | `None` | `None` | `908c68ea5f85f34e...` | `True` |
| `HIST_1791438480000_SMC_M1_CONTINUATION` | `1791438480000` | `SMC_M1_CONTINUATION` | `LONG` | `MISSING_MARKET_DATA` | `None` | `None` | `None` | `ae9c716c0d77d79a...` | `True` |
| `HIST_1791438480000_SMC_M1_FVG_CONTINUATION` | `1791438480000` | `SMC_M1_FVG_CONTINUATION` | `LONG` | `MISSING_MARKET_DATA` | `None` | `None` | `None` | `3d656606728863c4...` | `True` |
| `HIST_1791438480000_SMC_M1_SWEEP_RECLAIM` | `1791438480000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `MISSING_MARKET_DATA` | `None` | `None` | `None` | `2ee5f0cb9f2bbdb5...` | `True` |
| `HIST_1791438480000_CG01_SWEEP_MSS_FVG` | `1791438480000` | `CG01_SWEEP_MSS_FVG` | `LONG` | `MISSING_MARKET_DATA` | `None` | `None` | `None` | `a110fefa0cebf4a2...` | `True` |
| `HIST_1791439800000_SMC_M1_REVERSAL` | `1791439800000` | `SMC_M1_REVERSAL` | `LONG` | `NO_VALID_SETUP` | `4125.41` | `None` | `None` | `0a3af1344a897924...` | `True` |
| `HIST_1791439800000_SMC_M1_CONTINUATION` | `1791439800000` | `SMC_M1_CONTINUATION` | `LONG` | `NO_VALID_SETUP` | `4125.41` | `None` | `None` | `f051768afffeab34...` | `True` |
| `HIST_1791439800000_SMC_M1_FVG_CONTINUATION` | `1791439800000` | `SMC_M1_FVG_CONTINUATION` | `LONG` | `NO_VALID_SETUP` | `4125.41` | `None` | `None` | `6682eacca8206ea4...` | `True` |
| `HIST_1791439800000_SMC_M1_SWEEP_RECLAIM` | `1791439800000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `NO_VALID_SETUP` | `4125.41` | `None` | `None` | `137cf206bc841882...` | `True` |
| `HIST_1791439800000_CG01_SWEEP_MSS_FVG` | `1791439800000` | `CG01_SWEEP_MSS_FVG` | `LONG` | `NO_VALID_SETUP` | `4125.41` | `None` | `None` | `61816eb54d64b0f8...` | `True` |
| `HIST_1791440100000_SMC_M1_REVERSAL` | `1791440100000` | `SMC_M1_REVERSAL` | `LONG` | `NO_VALID_SETUP` | `4125.93` | `None` | `None` | `b8b636622d5cb414...` | `True` |
| `HIST_1791440100000_SMC_M1_CONTINUATION` | `1791440100000` | `SMC_M1_CONTINUATION` | `LONG` | `NO_VALID_SETUP` | `4125.93` | `None` | `None` | `639d8bad50ca06bd...` | `True` |
| `HIST_1791440100000_SMC_M1_FVG_CONTINUATION` | `1791440100000` | `SMC_M1_FVG_CONTINUATION` | `LONG` | `NO_VALID_SETUP` | `4125.93` | `None` | `None` | `70e440ae162c4126...` | `True` |
| `HIST_1791440100000_SMC_M1_SWEEP_RECLAIM` | `1791440100000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `NO_VALID_SETUP` | `4125.93` | `None` | `None` | `f9beef4be3d5a360...` | `True` |
| `HIST_1791440100000_CG01_SWEEP_MSS_FVG` | `1791440100000` | `CG01_SWEEP_MSS_FVG` | `LONG` | `NO_VALID_SETUP` | `4125.93` | `None` | `None` | `369d5b9c0471e367...` | `True` |
| `HIST_1791440160000_SMC_M1_REVERSAL` | `1791440160000` | `SMC_M1_REVERSAL` | `LONG` | `NO_VALID_SETUP` | `4125.16` | `None` | `None` | `38b363c6aa0627d5...` | `True` |
| `HIST_1791440160000_SMC_M1_CONTINUATION` | `1791440160000` | `SMC_M1_CONTINUATION` | `LONG` | `NO_VALID_SETUP` | `4125.16` | `None` | `None` | `8d169307ba6be382...` | `True` |
| `HIST_1791440160000_SMC_M1_FVG_CONTINUATION` | `1791440160000` | `SMC_M1_FVG_CONTINUATION` | `LONG` | `NO_VALID_SETUP` | `4125.16` | `None` | `None` | `ac769a1e30eee132...` | `True` |
| `HIST_1791440160000_SMC_M1_SWEEP_RECLAIM` | `1791440160000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `NO_VALID_SETUP` | `4125.16` | `None` | `None` | `f7c614baf04aec08...` | `True` |
| `HIST_1791440160000_CG01_SWEEP_MSS_FVG` | `1791440160000` | `CG01_SWEEP_MSS_FVG` | `LONG` | `NO_VALID_SETUP` | `4125.16` | `None` | `None` | `790864cc032141f4...` | `True` |
| `HIST_1791440400000_SMC_M1_REVERSAL` | `1791440400000` | `SMC_M1_REVERSAL` | `LONG` | `NO_VALID_SETUP` | `4123.66` | `None` | `None` | `d27bc2bf878e5d97...` | `True` |
| `HIST_1791440400000_SMC_M1_CONTINUATION` | `1791440400000` | `SMC_M1_CONTINUATION` | `SHORT` | `NO_VALID_SETUP` | `4123.46` | `None` | `None` | `d8ed53a8d6bd74f4...` | `True` |
| `HIST_1791440400000_SMC_M1_FVG_CONTINUATION` | `1791440400000` | `SMC_M1_FVG_CONTINUATION` | `SHORT` | `NO_VALID_SETUP` | `4123.46` | `None` | `None` | `d171ad3f314a3c68...` | `True` |
| `HIST_1791440400000_SMC_M1_SWEEP_RECLAIM` | `1791440400000` | `SMC_M1_SWEEP_RECLAIM` | `LONG` | `NO_VALID_SETUP` | `4123.66` | `None` | `None` | `2d73a10d761f9aa3...` | `True` |
| `HIST_1791440400000_CG01_SWEEP_MSS_FVG` | `1791440400000` | `CG01_SWEEP_MSS_FVG` | `SHORT` | `NO_VALID_SETUP` | `4123.46` | `None` | `None` | `32a60c7be3091ffc...` | `True` |
