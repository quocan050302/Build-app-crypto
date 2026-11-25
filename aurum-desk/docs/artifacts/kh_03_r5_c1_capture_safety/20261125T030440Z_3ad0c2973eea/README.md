# AURUM DESK — KH-03.R5.C1 | Capture Safety & Authorization Hardening

- **Run ID**: `20261125T030440Z_3ad0c2973eea` | **Branch**: `feature/v17-9-2-structural-sl-tp-from-qa` | **Tested SHA**: `3ad0c2973eeaa11f1e635b7a82beddeb4b5a8db8`
- **Classification**: `ENGINEERING_PASS_CAPTURE_SAFETY_HARDENED_OFFLINE_ONLY_CAPTURE_BLOCKED_OWNER_AUTHORIZATION_ECONOMIC_NO_PROVEN_EDGE`
- **Reproduce Command**: `python aurum-desk/tools/run_kh_03_r5_c1_acceptance.py`
- **Verify Command**: `python aurum-desk/tools/kh_03_r5_c1_independent_verifier.py --run-dir aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/20261125T030440Z_3ad0c2973eea --expected-run-id 20261125T030440Z_3ad0c2973eea --expected-tested-sha 3ad0c2973eeaa11f1e635b7a82beddeb4b5a8db8 --no-write-manifest`
- **Safety Lock**: `ACTIVATION_READY=NO | PRODUCTION=LIVE=PAPER=AUTO=OFF | OOS=0 | EXTERNAL_MARKET_REQUESTS=0 | NO_PROVEN_EDGE | KH04_ENTRY=BLOCKED_NO_DEV_CANDIDATE`

```text
AURUM DESK — BAN GIAO CHU SO HUU KH-03.R5.C1 (TIENG VIET)
======================================================================
SPEC: KH-03.R5.C1-v1.0 | RUN_ID: 20261125T030440Z_3ad0c2973eea
BRANCH: feature/v17-9-2-structural-sl-tp-from-qa | BASELINE_HEAD: 09493feb845210a629f3c0f174a2c6edfaf2ef23 | TESTED_SHA: 3ad0c2973eeaa11f1e635b7a82beddeb4b5a8db8
CLASSIFICATION: ENGINEERING_PASS_CAPTURE_SAFETY_HARDENED_OFFLINE_ONLY_CAPTURE_BLOCKED_OWNER_AUTHORIZATION_ECONOMIC_NO_PROVEN_EDGE
ENGINEERING=PASS | RESOURCE_SAFETY=PASS | FULL_REGRESSION=PASS | BACKTEST_PARITY=PASS
CAPTURE_AUTHORIZATION=BLOCKED_OWNER_AUTHORIZATION | CAPTURE_EXECUTION=BLOCKED_OWNER_AUTHORIZATION | CAPTURE_PROVENANCE=NO_REAL_CAPTURE | LIVE_NETWORK=NOT_RUN_BY_DESIGN
DATA_READINESS=INCOMPLETE | BBO=MISSING | FUNDING=MISSING | ACCOUNT_FEE=UNKNOWN_NOT_LOCALLY_VERIFIED | FIDELITY=INCOMPLETE
ECONOMIC=NO_PROVEN_EDGE | FRESH_OOS=BLOCKED_NO_UNTOUCHED_DATA | KH04=BLOCKED_NO_DEV_CANDIDATE | OOS=0 | EXTERNAL_MARKET_REQUESTS=0 | REAL_OBSERVED=0 | ACTIVATION_READY=NO | PRODUCTION/LIVE/PAPER/AUTO=OFF
CI_STATUS=CI_UNKNOWN_NO_ACTIONS_RUN | PUBLICATION=PRE_PUSH_PENDING

1. TAI HIEN RED GOC & GIA CO P1-A / P1-B / P1-C:
- Real Baseline RED on 09493feb845210a629f3c0f174a2c6edfaf2ef23: p1_a=True, p1_b=True, p1_c=True, exit=0, stdout_sha256=bb8e5a92639cb410...
- P1-A: validate_r5_operator_capture_scope + plan_r5_capture_batches + capture_r5_public_batch gan ket chat scope->plan->dispatch bang process-local R5TrustedScopeReceipt/R5TrustedPlanReceipt; default-deny R5OwnerAttestationVerifier chan live capture khi chua co bo xac thuc ngoai.
- P1-B: _check_r5_dispatch_guard kiem tra lai deadline/cancel/expiry/plan/resource SAU moi wait; timeout gioi han theo thoi gian con lai; khong cat ngan Retry-After; dat cho atomic cho request va wire bytes; huy/xa in-flight co gioi han va bo phan hoi tre.
- P1-C: validate_r5_resource_sample_strict + _require_r5_resource_monitor chan None/{}/thieu truong/NaN/Inf/bool/str/am/cu/tuong lai/sai volume/ngoai le; R5DevCustodyJournal.persist_snapshot bat buoc raw_sha256, kiem tra kich thuoc truoc khi doc va chi dem accepted=0 cho tep staging da xoa khi abort.

2. KIEM THU, FAULT CONTROLS, VERIFIER & PARITY:
- Full Repo Pytest JUnit 1:1: collected=5920, executed=5920, passed=5912, skipped=8, mandatory_skipped=0, failed=0, error=0
- Fault controls: 336/336 (288 ke thua + 48 moi C1)
- Separate-OS Verifier Mutations: 22/22 moi C1 + 89/89 ke thua
- Baseline Parity: A1=56710/0, SI8=6375/0, Adapter=246/0, D4=0
- Fresh R4 Replay (REUSED DEV 1x): closed=203 (7W/196L), gross=-164.9541 USD, fees=276.6577 USD, net=-441.6118 USD, exp=-0.8806R, >2.50USD_tails=139

3. BENCHMARK W1/W2/W4/W1_REPEAT & TAI NGUYEN:
- Pytest subset (155): means={'w=1': 53.638, 'w=2': 44.295, 'w=4': 37.325}, w1_repeat=51.901s, fastest=w=4, applied=3 (safe_cap=3), full_suite_extrapolated=False
- Science slice (8000): means={'w=1': 2.406, 'w=2': 2.011, 'w=4': 2.143}, w1_repeat=2.701s, fastest=w=2, applied=2
- Resources: min_disk=44.3879 GiB, min_ram=1186.5 MiB, peak_rss=2461.28 MiB, max_children=4, orphans=0, waiver=False

4. LUU Y PHAM VI & BUOC TIEP THEO:
- C1 chi gia co an toan capture offline; KHONG thay doi chien luoc, KHONG tao edge moi, KHONG phai quyen cho phep R6 hay thu thap live.
- Live capture giu BLOCKED_OWNER_AUTHORIZATION; transport C4 khoa khong ho tro dynamic wire read cap < transport.max_response_bytes (bi chan fail-closed neu vuot ngan sach).

5. 30 CONG (G01..G30):
  G01_BRANCH_SHA_CLEAN: PASS
  G02_KEEP_AS_IS: PASS
  G03_PRIOR_SEALED_DATASET_OOS: PASS
  G04_REAL_RED_REPRODUCTION: PASS
  G05_STRICT_GRANT_SCHEMA: PASS
  G06_TRUST_BOUNDARY: PASS
  G07_EFFECTIVE_SCOPE: PASS
  G08_PLAN_INTEGRITY: PASS
  G09_DISPATCH_REVALIDATION: PASS
  G10_QA_ISOLATION: PASS
  G11_DEADLINE_POST_WAIT: PASS
  G12_TIMEOUT_REMAINING: PASS
  G13_RATE_RETRY: PASS
  G14_ATOMIC_REQUEST_BUDGET: PASS
  G15_BYTE_BUDGET: PASS
  G16_CANCELLATION_DRAIN: PASS
  G17_RESOURCE_STRICT: PASS
  G18_DISK_RAM_GUARD: PASS
  G19_STAGING_CUSTODY: PASS
  G20_ABORT_STATUS_TRUTH: PASS
  G21_FULL_PYTEST: PASS
  G22_BASELINE_PARITY: PASS
  G23_BACKTEST_PARITY: PASS
  G24_DETERMINISTIC_WORKERS: PASS
  G25_MEASURED_WORKER_POLICY: PASS
  G26_FAULT_CONTROLS: PASS
  G27_INDEPENDENT_VERIFIER: PENDING_SEPARATE_OS_VERIFIER
  G28_MUTATION_RESISTANCE: PASS
  G29_ECONOMIC_AND_ACTIVATION: PASS
  G30_REMOTE_PUBLICATION: PRE_PUSH_PENDING
```
