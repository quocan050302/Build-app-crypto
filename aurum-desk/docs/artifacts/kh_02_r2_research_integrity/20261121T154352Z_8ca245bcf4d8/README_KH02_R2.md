# AURUM DESK — KH-02.R2 | Research Integrity & Data Readiness

- **Spec Version**: `KH-02.R2-v1.0`
- **Run ID**: `20261121T154352Z_8ca245bcf4d8`
- **Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Observed Start HEAD**: `8ca245bcf4d849c44426d047dc025609259bc21d`
- **Tested Executable SHA**: `8ca245bcf4d849c44426d047dc025609259bc21d`
- **Prior KH-02.R1 Accepted Run**: `20261121T033338Z_b99ad30313a6` (untouched, read-only reference)
- **Safety Modes**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`

## 1. Orthogonal Verdicts

| Dimension | Verdict |
|---|---|
| `ENGINEERING_VERDICT` | **PASS_WITH_OWNER_DISK_WAIVER** |
| `OFFLINE_RESEARCH_INTEGRITY_LOGIC_VERDICT` | **PASS** |
| `FULL_REGRESSION_VERDICT` | **PASS** |
| `RESOURCE_SAFETY_VERDICT` | **WAIVED_BY_OWNER_LOW_DISK** |
| `BASELINE_PARITY_VERDICT` | **PASS** |
| `ENTRY_TARGET_INTEGRITY_VERDICT` | **PASS** |
| `PAIRED_COMPARISON_VERDICT` | **IDENTIFIABLE_WITH_PARTIAL_COMMON_SUPPORT** |
| `COST_RECONCILIATION_VERDICT` | **PASS** |
| `DATA_PROVENANCE_VERDICT` | **PASS** |
| `FUTURE_OOS_READINESS_VERDICT` | **BLOCKED_NO_VERIFIED_NEW_DATA** |
| `FRESH_OOS_VERDICT` | **BLOCKED_NO_UNTOUCHED_DATA** |
| `RESEARCH_ECONOMIC_VERDICT` | **NO_PROVEN_EDGE** |
| `ACTIVATION_READY` | **NO** |
| `PUBLICATION_VERDICT` | **PRE_PUSH_PENDING_WITH_OWNER_DISK_WAIVER** |

## 2. P0 Swept-Target Audit on KH-02.R1 (Read-Only Reproduction)

- **Finding Classification**: `R1_SUSPECT_CONFIRMED_ON_RESEARCH_PATH_ONLY` (Production affected: `False`)
- **Directional Post-Warmup M1 Opportunities Audited**: `44692`
- **R1 Raw Targets Selected**: `3610` | **Already-Swept Raw Targets**: `584` (`16.18%`)
- **R1 Risk-Eligible Candidates**: `1039` | **Already-Swept Risk-Eligible**: `162` (`15.59%`)
- **Breakdown (Raw)**: `{'CONFIRMED_SWING_ALREADY_SWEPT_ON_SOURCE_HTF': 294, 'GENUINELY_UNSWEPT_LABEL_COLLISION_ONLY': 2049, 'GENUINELY_UNSWEPT_VALID': 977, 'HTF_POOL_ALREADY_SWEPT_BY_INTERVENING_M1': 290}`
- **Synthetic T01..T10 Suite**: `10/10 PASS`

### Per-Arm Swept Target Contamination in R1

| Arm | R1 Eligible | Swept Total | Swept Rate | Swept on Source HTF | Swept by Intervening M1 |
|---|---:|---:|---:|---:|---:|
| `B2_H1` | 191 | 3 | 1.57% | 3 | 0 |
| `B2_H2` | 1027 | 161 | 15.68% | 64 | 97 |
| `B2_H3` | 511 | 88 | 17.22% | 25 | 63 |
| `B2_H123` | 83 | 1 | 1.2% | 1 | 0 |
| `NC_NULL_TRIGGER` | 1039 | 162 | 15.59% | 64 | 98 |

## 3. Chronological 1-Position Replay (NEXT_BAR_OPEN_BID_ASK): R1 vs R2 Verified-Unswept

| Arm | R1 Closed (W/L) | R1 Net USD | R1 Exp R | R2 Closed (W/L) | R2 Net USD | R2 Exp R | Balance Quality (`|SMD|<=0.10`) |
|---|---|---|---|---|---|---|---|
| `B2_H1` | 58 (1W/57L) | -137.6058 | -0.9619 | 73 (1W/72L) | -175.438 | -0.9737 | `MATCHING_INADEQUATE` |
| `B2_H2` | 170 (8W/162L) | -357.2638 | -0.8522 | 203 (7W/196L) | -441.6118 | -0.8806 | `BALANCED_COMMON_SUPPORT` |
| `B2_H3` | 108 (7W/101L) | -211.7256 | -0.7951 | 114 (5W/109L) | -239.6588 | -0.8504 | `BALANCED_COMMON_SUPPORT` |
| `B2_H123` | 22 (0W/22L) | -55.0756 | -1.016 | 23 (0W/23L) | -57.6807 | -1.0169 | `BALANCED_COMMON_SUPPORT` |
| `NC_NULL_TRIGGER` | 170 (8W/162L) | -357.2638 | -0.8522 | 203 (7W/196L) | -441.6118 | -0.8806 | `CONTROL_BASELINE` |

## 4. Fixed-Fill Same-IDs vs Full-Universe Rerisked Cost Audit

- Historical quotes: `MODELED_BID_ASK` (spread `$0.20` USD, NO observed L1 BBO).
- Actual account effective fee tier: `UNKNOWN_NOT_LOCALLY_VERIFIED` (`0.0004` domain default vs `0.0006` public reference).

| Scenario (`FULL_RERISKED_DYNAMIC_POPULATION`) | Raw R2 Eligible | Delta vs 1x | B2_H1 Closed / Net USD | B2_H2 Closed / Net USD | B2_H3 Closed / Net USD | B2_H123 Closed / Net USD |
|---|---:|---:|---|---|---|---|
| `1X_BASE_0p0004` | 1323 | 0 | 73 / -175.438 | 203 / -441.6118 | 114 / -239.6588 | 23 / -57.6807 |
| `FEE_ONLY_0p0006` | 838 | -485 | 41 / -84.88 | 138 / -299.6384 | 84 / -190.3363 | 12 / -21.0497 |
| `2X_ALL_COST_0p0004` | 542 | -781 | 24 / -59.7975 | 111 / -253.7513 | 70 / -158.8308 | 6 / -14.8701 |
| `2X_ALL_COST_0p0006` | 200 | -1123 | 5 / -12.3382 | 46 / -113.8658 | 39 / -96.6987 | 5 / -12.5217 |
| `3X_ALL_COST_0p0004` | 195 | -1128 | 5 / -12.3146 | 45 / -112.2769 | 37 / -92.0618 | 5 / -12.426 |
| `3X_ALL_COST_0p0006` | 77 | -1246 | 2 / -4.8151 | 14 / -34.0747 | 9 / -21.8573 | 0 / 0.0 |

## 5. New Functions Added & Locked Files Kept As-Is

- Locked KEEP-AS-IS files modified: `0` (0 required).
- Existing files modified: `0` (0 required).
- Forbidden order/exchange imports: `0`.

| Function | Module | Artifact |
|---|---|---|
| `audit_r2_reference_inputs_and_invariants` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `02_r1_source_integrity_and_snapshot_registry.json` |
| `build_r2_causal_unswept_level_ledger` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `04_asof_unswept_level_ledger_and_mutation_proof.jsonl.gz` |
| `audit_r2_r1_target_eligibility_counterexamples` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `05_r1_target_eligibility_counterexamples_and_root_cause.json` |
| `resolve_r2_research_only_structural_target` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `06_r2_target_repair_full_opportunity_conservation.jsonl.gz` |
| `build_r2_paired_factorial_entry_geometry_design` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `07_paired_entry_geometry_factorial_control_mapping.jsonl.gz` |
| `compute_r2_stratified_common_support_balance` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `08_strata_common_support_balance_and_matching_quality.json` |
| `replay_r2_same_opportunity_paired_counterfactuals` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `09_chronological_1position_three_fill_paired_replays.json` |
| `audit_r2_fill_time_net_risk_and_realized_tail` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `10_fill_time_net_risk_and_realized_tail_audit.json` |
| `reprice_r2_fixed_fills_cost_counterfactual` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `11_fixed_fill_same_ids_fee_spread_slippage_1x2x3x.json` |
| `rerun_r2_full_universe_by_cost_scenario` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `12_full_universe_rerisked_1x2x3x_and_fee_004_006.json` |
| `audit_r2_data_lineage_and_gap_sensitivity` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `13_m1_m15_h1_gaps_source_latency_quote_fee_provenance.json` |
| `prepare_r2_prospective_oos_ingestion_and_escrow` | `aurum-desk/backend/services/kh_02_r2_prospective_data_service.py` | `14_prospective_oos_escrow_collection_readiness_no_peek.json` |
| `run_kh02_r2_42_red_green_fault_suite` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `19_real_red_green_fault_injections.json` |
| `evaluate_r2_orthogonal_science_and_engineering_gates_from_disk` | `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py` | `21_orthogonal_engineering_science_economic_oos_gate_matrix.json` |
| `verify_r2_artifacts_independently_from_disk` | `aurum-desk/tools/kh_02_r2_independent_verifier.py` | `22_independent_os_disk_verifier_sha256_manifest.json` |

## 6. QA, Resources, Regression & Gates

- **RED/GREEN Faults**: `42/42` (`PASS`)
- **Full Pytest Regression**: `PASS` (collected=`5538`, executed=`5538`)
- **Focused KH-02.R2 Unit Tests**: `{'included_in_full_repo_bijection': True, 'status': 'PASS'}`
- **Min Available RAM**: `2352.2 MiB` (hard floor `>= 512.0 MiB`)
- **Min Disk Free**: `5.6567 GiB` (strict preflight floor `>= 20.5 GiB`; shortfall `13.3627 GiB`)
- **Max Descendant Children / Orphans**: `4` / `0`
- **OOS Readiness**: `BLOCKED_NO_VERIFIED_NEW_DATA` (`OOS_EVALUATIONS = 0`)

### Gate Matrix (`21_orthogonal_engineering_science_economic_oos_gate_matrix.json`)

| Gate | Name | Status | Detail |
|---|---|---|---|
| `Gate 01` | Branch, clean source, real HEAD and tested SHA | **PASS** | branch=feature/v17-9-2-structural-sl-tp-from-qa, sha=8ca245bcf4d8, clean=True |
| `Gate 02` | Zero locked/production/R1 files modified & AST freeze | **PASS** | locked_mod=0, existing_mod=0 |
| `Gate 03` | Zero LIVE/PAPER/AUTO/PRODUCTION side effects & ACTIVATION_READY=NO | **PASS** | modes={'AUTO': 'OFF', 'LIVE': 'OFF', 'PAPER': 'OFF', 'PRODUCTION': 'OFF'}, activation=NO |
| `Gate 04` | R1 baseline hashes/manifests & strict D4 ontology preserved | **PASS** | f02=PASS |
| `Gate 05` | Unswept target ledger causal/confirmed/sweep/latency tests | **PASS** | f04_exists=True, t01_t10_all_passed=True |
| `Gate 06` | R1 target eligibility bug fairly proven or NOT_REPRODUCED | **PASS** | classification=R1_SUSPECT_CONFIRMED_ON_RESEARCH_PATH_ONLY, raw_swept=584 |
| `Gate 07` | R2 strictly as-of unswept targets & zero TP stretching | **PASS** | f06_exists=True |
| `Gate 08` | Paired E/G/X estimands frozen & denominators reconciled | **PASS** | frozen=True, f07_exists=True |
| `Gate 09` | Common-support covariate balance & MATCHING_INADEQUATE honesty | **PASS** | paired_verdict=IDENTIFIABLE_WITH_PARTIAL_COMMON_SUPPORT |
| `Gate 10` | Fill-time planned net risk <= $2.50, net R:R >= 2.0 & tail disclosure | **PASS** | dec_v=0, fill_v=0 |
| `Gate 11` | Chronological 1-position 3-fill replay, zero overlap/lookahead | **PASS** | overlaps=0 |
| `Gate 12` | Fixed-fill SAME_IDS & full-universe RERISKED cost reconciliation | **PASS** | f11=PASS, f12=PASS |
| `Gate 13` | M1/HTF gaps, source latency, modeled BBO & unknown fee disclosure | **PASS** | span_days=20.3479 |
| `Gate 14` | OOS escrow contract write-once & OOS_EVALUATIONS == 0 | **PASS** | readiness=BLOCKED_NO_VERIFIED_NEW_DATA, evals=0 |
| `Gate 15` | Full repository pytest regression & 1:1 JUnit bijection | **PASS** | status=PASS, collected=5538, executed=5538 |
| `Gate 16` | Async W1/W2/W4/W1_repeat determinism & workload-specific speeds | **PASS** | status=PASS |
| `Gate 17` | RAM >= 512 MiB, children <= 4, orphans == 0, strict disk >= 20.5 GiB | **WAIVED_BY_OWNER_LOW_DISK** | min_ram=2352.2 MiB, min_disk=5.6567 GiB, max_ch=4, orphans=0, owner_waived=True |
| `Gate 18` | >= 40 unique real RED/GREEN fault controls | **PASS** | passed=42/42 |
| `Gate 19` | Separate-OS-process independent disk verifier & SHA256 manifest | **PENDING_SEPARATE_OS_VERIFIER** | verifier=PENDING |
| `Gate 20` | Safe commit, push & true remote SHA/blob readback | **PENDING_REMOTE_PUSH** | publication_status=PRE_PUSH_PENDING_WITH_OWNER_DISK_WAIVER |
