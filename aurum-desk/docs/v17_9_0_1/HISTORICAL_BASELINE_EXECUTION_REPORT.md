# V17.9.0.1.2 — Historical Baseline Independent Execution Report

- **Baseline Commit SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Isolated Execution Status:** `PASS` (`ISOLATED_HISTORICAL_SUBPROCESS_VERIFIED`)
- **Extracted Historical Backend Directory:** `/private/var/folders/n_/hz396std3x792_n2yrmrcw640000gn/T/aurum_v179012_hist_baseline_c356d6551e13_12991/aurum-desk/backend`
- **Historical Dependency Bundle SHA-256:** `c9ea5033de46499cbbd7032d824eecfb6007f0f1bd44f5f25e2168a74ab9c261`
- **Characterization Scenarios Evaluated:** `55` (`parity_matches=55/55`, `divergences=0`)
- **Real Historical Dataset Scenarios Evaluated:** `0` (`parity_matches=0/0`)

## 1. Isolated Historical Dependency Verification

| Module | Loaded From Historical Tree | On-Disk SHA-256 |
|---|---|---|
| `domain_calculator` | `True` | `1f074ee5c0ba8d0647fb422976b67be89a9f5d96eccb0bccdd5fac8abdafb94f` |
| `services.cg01_engine` | `True` | `e96b3ab54cf80a4c984d666435875ff8b4c0ff0e7118c1a55680e72aba9a4e5a` |
| `services.liquidity_scenario_service` | `True` | `4e0800f300239cd625ac5b2412162623fe1173c7de900f2e07c22062cdfd3e05` |
| `services.scalp_m1_model_service` | `True` | `c538eab1f753e5ef0b9a97c6114e4b9920251777a75866f9465972f8313aab99` |
| `services.smc_liquidity_service` | `True` | `4fd49d4c6e93f3dd6cd35fa00a8bee33a0c06ab19e4a7e12437dd403935ddb2e` |
| `services.stop_placement_research_service` | `True` | `9bd9a4222f4933e7274b120063082205cf35010a3d72dbccca722aac1e129842` |
| `services.stop_placement_service` | `True` | `7c4371b6aa80d2977c5b80b498c3095b341e20f1f609714b0dd7323eeb07256f` |
| `services.unicorn_evidence_service` | `True` | `6e6225b7243cf734674599e18798bef1d773ee7a2aa78eb8c26d0abffca6dc78` |
| `smc_engine` | `True` | `aff32b03f7938b98c7300a84aa0ba4ec3623e9ef0a1fa5d9281693b818b9ca7b` |
