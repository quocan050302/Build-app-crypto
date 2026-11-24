# AURUM DESK — KH-03.R5 | Observed Market Data Foundation & Execution Fidelity

- **Run ID**: `20261124T135139Z_d9e24be7864d` | **Branch**: `feature/v17-9-2-structural-sl-tp-from-qa` | **Tested SHA**: `d9e24be7864de6ad886b8581c65df86bbd531275`
- **Classification**: `ENGINEERING_PASS_DATA_FOUNDATION_READY_CAPTURE_BLOCKED_OWNER_AUTHORIZATION_ECONOMIC_NO_PROVEN_EDGE`
- **Safety Lock**: `ACTIVATION_READY=NO | PRODUCTION=LIVE=PAPER=AUTO=OFF | OOS_EVALUATIONS=0 | NO_PROVEN_EDGE | KH04_ENTRY=BLOCKED_NO_DEV_CANDIDATE`

```text
AURUM DESK — BAN GIAO CHU SO HUU KH-03.R5 (TIENG VIET)
======================================================================
SPEC: KH-03.R5-v1.0 | RUN_ID: 20261124T135139Z_d9e24be7864d
BRANCH: feature/v17-9-2-structural-sl-tp-from-qa | TESTED_EXECUTABLE_SHA: d9e24be7864de6ad886b8581c65df86bbd531275
CLASSIFICATION: ENGINEERING_PASS_DATA_FOUNDATION_READY_CAPTURE_BLOCKED_OWNER_AUTHORIZATION_ECONOMIC_NO_PROVEN_EDGE
ENGINEERING=PASS | RESOURCE_SAFETY=PASS | FULL_REGRESSION=PASS | BACKTEST_PARITY=PASS
CAPTURE_AUTHORIZATION=BLOCKED_OWNER_AUTHORIZATION | CAPTURE_EXECUTION=BLOCKED_OWNER_AUTHORIZATION | CAPTURE_PROVENANCE=NO_REAL_CAPTURE
DATA_READINESS=INCOMPLETE | OBSERVED_BBO=MISSING | ACCOUNT_FEE=UNKNOWN_NOT_LOCALLY_VERIFIED | FUNDING=MISSING | EXECUTION_FIDELITY=INCOMPLETE
ECONOMIC=NO_PROVEN_EDGE | FRESH_OOS=BLOCKED_NO_UNTOUCHED_DATA | KH04=BLOCKED_NO_DEV_CANDIDATE | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO | PRODUCTION/LIVE/PAPER/AUTO=OFF
CI_STATUS=CI_UNKNOWN_NO_ACTIONS_RUN | PUBLICATION=PRE_PUSH_PENDING

1. TRANG THAI THU THAP DU LIEU THUC (KHONG GIA LAP):
- External requests attempted: 0 | Accepted observed records: 0 | QA offline fixture records (tach rieng): 3
- Thu thap cong khai ben ngoai bi CHAN AN TOAN (CAPTURE_BLOCKED_OWNER_AUTHORIZATION) vi prompt nay KHONG thay the phe duyet van hanh rieng cua chu so huu; KHONG yeu cau API key/mat khau; KHONG goi endpoint private.
- Nguon du lieu thieu: L1 BBO lien tuc <=1s (REST chi co snapshot, BLOCKED_CONTINUOUS_BBO), funding history (chua co trong C2/C3 allowlist), bieu phi tai khoan thuc te (UNKNOWN_NOT_LOCALLY_VERIFIED), slippage/fill mix (MISSING), fresh untouched OOS (ABSENT).

2. KIEM THU TUOI, REPLAY R4 & PARITY:
- Full Repo Pytest JUnit 1:1: collected=5886, executed=5886, passed=5884, skipped=2, mandatory_skipped=0, failed=0, error=0 (max_workers=3)
- Baseline Parity: A1=56710/0, SI8=6375/0, Adapter=246/0, D4=0
- Fresh R4 Replay (REUSED DEV 1x, khong dung du lieu moi): closed=203 (7W/196L), gross=-164.9541 USD, fees=276.6577 USD, net=-441.6118 USD, exp=-0.8806R, open_censored=28, gap=2, >2.50USD_tails=139
- Faults: 288/288 (240 ke thua + 48 moi R5) | Verifier Mutations: 20/20 moi R5 + 69/69 ke thua

3. HIEU NANG W1/W2/W4/W1_REPEAT & TAI NGUYEN:
- Pytest subset (155): means={'w=1': 25.986, 'w=2': 17.229, 'w=4': 13.19}, w1_repeat=23.883s (var=0.0881), fastest=w=4, applied=3 (safe_cap=3), full_suite_extrapolated=False
- Science slice (8000): means={'w=1': 1.219, 'w=2': 0.957, 'w=4': 1.008}, w1_repeat=1.042s (var=0.1699), fastest=w=2, applied=2
- Resources: min_disk=23.4394 GiB, min_ram=2246.22 MiB, peak_rss=2170.3 MiB, max_children=4, orphans=0, waiver=False
- Reused read-only source modules audited: 10 (0 locked production/science files modified).

4. QUYET DINH TIEP THEO CUA CHU SO HUU:
- Lua chon A: Dung nghien cuu chien luoc tren tap DEV thang 10/2026 (da dung lai nhieu lan, chi 17 ngay NY).
- Lua chon B: Neu muon tiep tuc trong mot pha moi, chu so huu phe duyet ro rang pham vi thu thap du lieu cong khai DEV gioi han (symbol, productType, endpoint cong khai, max requests/bytes/thoi gian, thu muc local ngoai Git) va cung cap xac nhan bieu phi da che thong tin nhay cam (KHONG gui API key/mat khau). Tuyet doi KHONG bat trading.

5. 26 CONG (G01..G26):
  G01_BRANCH_CLEAN_FROZEN_SHA: PASS
  G02_PROTECTED_BYTES_AST_SOURCE_OWNERSHIP: PASS
  G03_PRIOR_DATASET_ARTIFACT_ESCROW_INTEGRITY: PASS
  G04_PUBLIC_CONTRACT_SOURCE_CAPABILITY_TRUTH: PASS
  G05_AUTHORIZATION_FAIL_CLOSED: PASS
  G06_CAPTURE_BUDGET_TRANSPORT_INTEGRITY: PASS
  G07_RAW_NORMALIZED_PROVENANCE: PASS
  G08_METADATA_IDENTITY_PRECISION: PASS
  G09_OHLCV_LINEAGE_GAP_SEMANTICS: PASS
  G10_BBO_COVERAGE_HONESTY: PASS
  G11_FUNDING_SCHEDULE_RATE_TRUTH: PASS
  G12_ACCOUNT_FEE_SLIPPAGE_UNKNOWN_HONESTY: PASS
  G13_DEV_OOS_PARTITION_NO_ACCESS: PASS
  G14_CUSTODY_RACE_CRASH_LOCK_ANCHOR: PASS
  G15_R4_REAL_REPLAY_EXACT_ACCOUNTING_ANCHORS: PASS
  G16_NO_SCIENCE_PROD_CHANGE_RISK_TAIL_DISCLOSURE: PASS
  G17_FULL_FRESH_PYTEST_COLLECTION_JUNIT_BIJECTION: PASS
  G18_A1_SI8_ADAPTERS_D4_FULL_PARITY: PASS
  G19_DETERMINISTIC_W1_W2_W4_W1REPEAT: PASS
  G20_MEASURED_WORKER_POLICY: PASS
  G21_DISK_RAM_PID_NO_WAIVER: PASS
  G22_INHERITED_AND_NEW_RED_GREEN_FAULTS: PASS
  G23_SEPARATE_OS_VERIFIER_RAW_EVIDENCE: PENDING_SEPARATE_OS_VERIFIER
  G24_VERIFIER_MUTATION_ATTACKS: PASS
  G25_NO_ACTIVATION_OOS_ALPHA_CLAIM: PASS
  G26_PUBLICATION_REMOTE_PROOF: PRE_PUSH_PENDING
```
