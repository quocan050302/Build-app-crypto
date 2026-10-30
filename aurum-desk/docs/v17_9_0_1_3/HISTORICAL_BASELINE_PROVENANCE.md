# V17.9.0.1.2 — Independent Historical Baseline Provenance (`DEF-179012-03` Closure)

## 1. Baseline Source Provenance & Repository-Relative Path Resolution
- **Pre-Refactor Git Commit SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` (V17.8.0.4 — last verified commit prior to V17.9.0 shared evaluator extraction).
- **Git Top-Level Directory:** `D:\build app-crypto`
- **Git Commit Object Verified:** `True`
- **Provenance Verification Status:** `PASS` (`verified_files=15/15`)
- **Historical Dependency Bundle SHA-256:** `c9ea5033de46499cbbd7032d824eecfb6007f0f1bd44f5f25e2168a74ab9c261`
- **Path Resolution Fix (`DEF-179012-03`):** Resolved Git top-level path prefix `aurum-desk/backend/...` when inspecting `git show <commit>:aurum-desk/backend/...` and extracting `git archive <commit> aurum-desk/backend`.
- **Non-Self-Referential Guarantee:** `backend/services/v17_9_0_1_baseline_oracle.py` contains zero imports or invocations of `SharedStrategyEvaluator` or `shared_strategy_evaluator`, and executes inside an isolated Python subprocess rooted at the extracted `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` source tree.

## 2. Historical Source File SHA-256 Digests at Commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`

| Repository-Relative Path | App-Relative Path | Size (Bytes) | SHA-256 |
|---|---|---:|---|
| `aurum-desk/backend/domain_calculator.py` | `backend/domain_calculator.py` | 23934 | `1f074ee5c0ba8d0647fb422976b67be89a9f5d96eccb0bccdd5fac8abdafb94f` |
| `aurum-desk/backend/services/cg01_engine.py` | `backend/services/cg01_engine.py` | 75578 | `e96b3ab54cf80a4c984d666435875ff8b4c0ff0e7118c1a55680e72aba9a4e5a` |
| `aurum-desk/backend/services/liquidity_scenario_service.py` | `backend/services/liquidity_scenario_service.py` | 79593 | `4e0800f300239cd625ac5b2412162623fe1173c7de900f2e07c22062cdfd3e05` |
| `aurum-desk/backend/services/market_shadow_service.py` | `backend/services/market_shadow_service.py` | 111526 | `59b85954be4fe826ccc93f877a6745651cdb82ffdb25919bf6ac7d5590541dea` |
| `aurum-desk/backend/services/quote_validator.py` | `backend/services/quote_validator.py` | 32339 | `f7fa210bd9d07175519d27f8426d7fe4295199a5503d19f7cc922d0ecd6b2b93` |
| `aurum-desk/backend/services/scalp_m1_model_service.py` | `backend/services/scalp_m1_model_service.py` | 139744 | `c538eab1f753e5ef0b9a97c6114e4b9920251777a75866f9465972f8313aab99` |
| `aurum-desk/backend/services/scalp_m1_types.py` | `backend/services/scalp_m1_types.py` | 28947 | `7e6d02bc4fe52cf51d1b7198e3a597fea6131c3f0cf018e07b4bab93ee99fc9e` |
| `aurum-desk/backend/services/scalp_watcher_orchestrator.py` | `backend/services/scalp_watcher_orchestrator.py` | 168059 | `efd1dec4b38843583652101a6227c41255b992f8c13a54898a0ba3036c60056f` |
| `aurum-desk/backend/services/smc_liquidity_service.py` | `backend/services/smc_liquidity_service.py` | 19700 | `4fd49d4c6e93f3dd6cd35fa00a8bee33a0c06ab19e4a7e12437dd403935ddb2e` |
| `aurum-desk/backend/services/stop_placement_research_service.py` | `backend/services/stop_placement_research_service.py` | 40113 | `9bd9a4222f4933e7274b120063082205cf35010a3d72dbccca722aac1e129842` |
| `aurum-desk/backend/services/stop_placement_service.py` | `backend/services/stop_placement_service.py` | 137663 | `7c4371b6aa80d2977c5b80b498c3095b341e20f1f609714b0dd7323eeb07256f` |
| `aurum-desk/backend/services/stop_placement_types.py` | `backend/services/stop_placement_types.py` | 22879 | `68070833337c5b8b6f13cb8c22d9100ef5e43c327839a4bba548053fa4e285a8` |
| `aurum-desk/backend/services/unicorn_evidence_service.py` | `backend/services/unicorn_evidence_service.py` | 42705 | `6e6225b7243cf734674599e18798bef1d773ee7a2aa78eb8c26d0abffca6dc78` |
| `aurum-desk/backend/services/v17_8_backtest_runner.py` | `backend/services/v17_8_backtest_runner.py` | 137627 | `f0f017d8dc2750b334dbbc4309c1534816b94cee4e3dfc9044a00aa43e9c9872` |
| `aurum-desk/backend/smc_engine.py` | `backend/smc_engine.py` | 43890 | `aff32b03f7938b98c7300a84aa0ba4ec3623e9ef0a1fa5d9281693b818b9ca7b` |

## 3. Isolated Historical Subprocess Module Provenance

| Loaded Module | Resolved File in Isolated Historical Tree | SHA-256 |
|---|---|---|
| `domain_calculator` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\domain_calculator.py` | `1f074ee5c0ba8d0647fb422976b67be89a9f5d96eccb0bccdd5fac8abdafb94f` |
| `services.cg01_engine` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\services\cg01_engine.py` | `e96b3ab54cf80a4c984d666435875ff8b4c0ff0e7118c1a55680e72aba9a4e5a` |
| `services.liquidity_scenario_service` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\services\liquidity_scenario_service.py` | `4e0800f300239cd625ac5b2412162623fe1173c7de900f2e07c22062cdfd3e05` |
| `services.market_shadow_service` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\services\market_shadow_service.py` | `59b85954be4fe826ccc93f877a6745651cdb82ffdb25919bf6ac7d5590541dea` |
| `services.scalp_m1_model_service` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\services\scalp_m1_model_service.py` | `c538eab1f753e5ef0b9a97c6114e4b9920251777a75866f9465972f8313aab99` |
| `services.scalp_watcher_orchestrator` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\services\scalp_watcher_orchestrator.py` | `efd1dec4b38843583652101a6227c41255b992f8c13a54898a0ba3036c60056f` |
| `services.smc_liquidity_service` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\services\smc_liquidity_service.py` | `4fd49d4c6e93f3dd6cd35fa00a8bee33a0c06ab19e4a7e12437dd403935ddb2e` |
| `services.stop_placement_research_service` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\services\stop_placement_research_service.py` | `9bd9a4222f4933e7274b120063082205cf35010a3d72dbccca722aac1e129842` |
| `services.stop_placement_service` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\services\stop_placement_service.py` | `7c4371b6aa80d2977c5b80b498c3095b341e20f1f609714b0dd7323eeb07256f` |
| `services.unicorn_evidence_service` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\services\unicorn_evidence_service.py` | `6e6225b7243cf734674599e18798bef1d773ee7a2aa78eb8c26d0abffca6dc78` |
| `services.v17_8_backtest_runner` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\services\v17_8_backtest_runner.py` | `f0f017d8dc2750b334dbbc4309c1534816b94cee4e3dfc9044a00aa43e9c9872` |
| `smc_engine` | `C:\Users\ADMIN\AppData\Local\Temp\aurum_v179012_hist_baseline_c356d6551e13_22544\aurum-desk\backend\smc_engine.py` | `aff32b03f7938b98c7300a84aa0ba4ec3623e9ef0a1fa5d9281693b818b9ca7b` |

## 4. Frozen Artifact Integrity (LF Byte-Exact)
- `BASELINE_DECISIONS.jsonl` SHA-256: `4583f0a7c552a4a32ea3abf99eacb86a6afb161f87c1d8a812ab39405af1f3db`
- `BASELINE_DECISIONS.jsonl` Size (Bytes): `317559`
- Total Frozen Characterization Decisions: `55` across all 11 mandatory categories and 5 production models.
