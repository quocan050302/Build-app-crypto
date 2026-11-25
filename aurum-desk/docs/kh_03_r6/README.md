# AURUM DESK — KH-03.R6: Observed Market Data Acquisition (Durable Authorization, Bounded Transport & Data Quality)

- **Spec Version:** `KH-03.R6-v1.0`
- **Repository:** `https://github.com/quocan050302/Build-app-crypto`
- **Mandatory Branch:** `feature/v17-9-2-structural-sl-tp-from-qa`
- **Base Reviewed HEAD:** `fd0df117ca38f0ac72312d27265dcdabaa5a0dba`
- **Prior Accepted Phase (`KH-03.R5.C2`):**
  - Tested executable SHA: `68549a8a44a424eb58f9c21bec49199ab296eb1a`
  - Evidence commit SHA: `f79419f959639994b6d631df16cd352bbcbb2e69`
  - Run ID: `20261125T061403Z_68549a8a44a4`
  - Manifest 21 SHA-256: `6a65843b16cf7b97b3011afcaa2e8495c2ec90aaaadc35dbf0148338cb3d2778`
  - Post-push receipt 23 SHA-256: `8bb861863395ecf4545f14d275bb02d765b13d2fc413956a632e5e6480b00989`

---

## 1. Stage Model & Safety Boundaries

KH-03.R6 separates engineering readiness from owner-authorized external market data acquisition into four distinct stages with independent verdicts:

| Stage | Scope | External Market Sockets | Default Status Without Owner Grant |
| :--- | :--- | :--- | :--- |
| **Stage A** | Offline engineering, source-doc contract verification, durable SQLite+anchor grant ledger, owned subprocess transport + Windows Job Object / POSIX PGID termination & reap, real `127.0.0.1` loopback HTTP/WS tests, strict Decimal market-data validators, custody/partition guards, full repo regression + R4 replay + parity + >=56 paired controls + >=30 independent verifier mutations | **0** (public docs audit is research, not API capture) | Executed & independently verified (`32/32` mandatory Stage A gates) |
| **Stage B** | Authorized external read-only public smoke (`XAUUSDT` Classic v2 metadata, limited closed M1, 1 funding page, bounded `books1` WS if verified) | Only after certified Stage A + genuine local Ed25519 owner trust root + one-shot signed smoke grant + durable SQLite claim | `BLOCKED_OWNER_AUTHORIZATION` (`0/4` Stage B/C executed) |
| **Stage C** | Bounded DEV collection & local sealing outside Git (separate one-shot DEV grant; no perpetual background daemon) | Only after Stage B PASS + separate signed DEV grant | `NOT_RUN_BLOCKED_OWNER_AUTHORIZATION` |
| **Stage D** | Readiness registry handoff for R7 (coverage, funding, metadata, fee/slippage honesty; R7 itself is NOT executed in R6) | 0 | `NOT_RUN_BLOCKED_OWNER_AUTHORIZATION` |

**Hard Invariants Always Enforced:**
- `PRODUCTION = OFF` | `LIVE = OFF` | `PAPER = OFF` | `AUTO = OFF` | `ACTIVATION_READY = NO`
- `ECONOMIC = NO_PROVEN_EDGE` | `FRESH_OOS = BLOCKED_NO_UNTOUCHED_DATA` | `KH04_ENTRY = BLOCKED_NO_DEV_CANDIDATE` | `OOS_EVALUATIONS = 0`
- Zero modifications to locked production/strategy/Entry/SL/TP/sizing/adapters/fee defaults/D4/A1/SI8, canonical DEV dataset (`9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`), OOS escrow, KH-02 C2/C3/C4, R3.C1/C2 worker policy, R5.C2 live hard-block, or any prior sealed evidence/pointer (`R1..R5.C2`).

---

## 2. Dependency Audit & Threat Model

### Dependency Decision (Zero New Packages Installed)
All required cryptographic, WebSocket, SQLite, OS process containment, and telemetry primitives are already present in the verified local Python 3.12 environment:
- **Ed25519 Owner Signature Verification:** `cryptography==50.0.2` (already installed; genuine `Ed25519PublicKey.verify`, zero custom cryptography, zero embedded owner key).
- **Bounded Public WebSocket Client & Loopback QA Server:** `websockets==17.4` (already installed; RFC 6455 framing with strict `max_size`, `max_queue`, explicit text `ping`/`pong` heartbeat, strict TLS for external `wss://`).
- **Durable Cross-Process Grant Ledger:** Python stdlib `sqlite3` (`3.49.1`, `BEGIN IMMEDIATE`, `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=FULL`) paired with an externally synced monotonic generation anchor file.
- **Owned Transport Process Containment & Reap:** Python stdlib `ctypes` Windows Job Object (`CreateJobObjectW` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + `AssignProcessToJobObject` + `TerminateJobObject`) on Windows and process group (`os.setsid` + `os.killpg`) on POSIX, verified via `psutil==7.2.2` PID + `create_time` OS identity checks.
- **Bounded Streaming REST GET:** Python stdlib `http.client` + `ssl` + `certifi==2026.10.23` + `zlib` streaming decompression caps.

### Threat Model Disclosure
- **Mitigated:** Accidental or adversarial grant/plan replay across processes or restarts; partial write/crash budget refund; SQLite rollback/swap/truncation/missing-DB silent reset; slow-drip/chunked/gzip-bomb/WS-frame memory exhaustion; non-cooperative transport child or descendant leaks; QA-to-observed provenance promotion; symlink/NTFS-junction/OOS path traversal; fake readiness PASS injection.
- **Explicitly Outside Threat Model (Disclosed):** A fully compromised local OS administrator/root account or a stolen owner Ed25519 private signing key. Owner private signing material must NEVER enter the repository, logs, CLI arguments, chat, or app runtime.

---

## 3. Module Architecture & Function Ownership Map (100% Additive)

All KH-03.R6 implementation lives in new modules; **0 locked files are modified**:

| Module | Path | Primary Responsibilities (`F01`–`F29`) |
| :--- | :--- | :--- |
| **M1** | `aurum-desk/backend/services/kh_03_r6_source_contract_service.py` | `F01` official Bitget Classic v2 contract registry (with real `2026-11-25` docs digests & v2/v3 non-mixing rule), `F02` strict endpoint/param/TLS/WS contract validator, `F03` strict Decimal instrument identity validator, `F04` capability matrix (`DOC_VERIFIED` vs `OBSERVED_CONTRACT_VERIFIED`). |
| **M2** | `aurum-desk/backend/services/kh_03_r6_durable_grant_service.py` | `F05` local out-of-Git Ed25519 owner trust root loader (no default key), `F06` strict canonical grant verifier, `F07` stable grant identity, `F08` SQLite+anchor durable ledger (fail-closed on missing/corrupt/rolled-back store), `F09` atomic one-shot session claim, `F10` pre-dispatch I/O budget reservation (known+uncertain+outstanding), `F11` start/settle/finalize state machine, `F12` crash/restart recovery. |
| **M3** | `aurum-desk/backend/services/kh_03_r6_bounded_transport_service.py` | `F13` frozen bound capture plan, `F14` owned subprocess launcher with Windows Job Object / POSIX PGID isolation, `F15` streaming bounded REST GET (deadline, chunk/decompress caps, no redirect, Retry-After), `F16` bounded public WS session (ack/epoch/heartbeat/frame/queue/bounded reconnect), `F17` supervisor + forcible tree termination & OS reap verification, `F18` strict 0.5s multi-volume resource guard & bounded queue writer + isolated `127.0.0.1` loopback QA servers. |
| **M4** | `aurum-desk/backend/services/kh_03_r6_market_data_service.py` | `F19` OHLCV half-open pagination, dedup, conflict quarantine, unclosed exclusion; `F20` BBO event normalization & epoch validation; `F21` separate observed-event vs valid state-known 1s slot coverage; `F22` funding settlement completeness (no zero-fill); `F23` owner fee/slippage honesty registry; `F24` deterministic gap ledger. |
| **M5** | `aurum-desk/backend/services/kh_03_r6_custody_readiness_service.py` | `F25` DEV_ONLY/QA_FIXTURE partition & symlink/junction/OOS deny-before-open guard; `F26` R6 raw provenance receipts (`QA_LOOPBACK` vs `RAW_OBSERVED_VERIFIED`); `F27` chunked atomic custody journal + anchor + crash recovery + read-only verifier; `F28` sealed DEV dataset manifest; `F29` 36-gate stage-scoped readiness engine & Vietnamese handover builder. |
| **M6** | `aurum-desk/backend/services/kh_03_r6_acceptance_service.py` | R6 paired fault truth engine (`REGISTERED_FAULT_PREDICATES_R6`, `execute_r6_fault_pair`, `validate_r6_fault_pair`, `derive_r6_fault_verdict`), ownership/keep-as-is/secret scanner, unsigned owner review template generator, R6 fixture ingest determinism benchmark (`W1/W2/W4/W1-repeat`). |
| **T1** | `aurum-desk/tools/run_kh_03_r6_acceptance.py` | Offline Stage A engineering acceptance orchestrator (`--phase offline --workers auto`). Never opens external market sockets. |
| **T2** | `aurum-desk/tools/run_kh_03_r6_capture.py` | Explicit acquisition CLI (`--stage smoke|dev`). Defaults to `--dry-run` (zero sockets, emits unsigned owner review manifest). Requires genuine local owner trust root, provisioned SQLite ledger, frozen Stage A release manifest, and signed one-shot grant before any external I/O. |
| **T3** | `aurum-desk/tools/kh_03_r6_independent_verifier.py` | Separate-OS read-only disk verifier recomputing all 36 stage-scoped gates from raw artifacts and sealing `26_independent_OS_verifier_manifest_sha256.json`. |

---

## 4. CLI Contracts (Local Paths Only — Zero Secrets in CLI or Git)

```powershell
# Stage A — Offline Engineering Readiness & Full Regression (0 external market sockets)
python aurum-desk/tools/run_kh_03_r6_acceptance.py --phase offline --workers auto

# Stage B — Dry-Run Smoke Plan Validation & Unsigned Owner Review Template (0 sockets)
python aurum-desk/tools/run_kh_03_r6_capture.py --stage smoke --dry-run --plan <local-smoke-plan.json>

# Stage B — Owner-Authorized External Smoke (ONLY after Stage A certified + local owner trust & signed grant)
python aurum-desk/tools/run_kh_03_r6_capture.py --stage smoke --grant <local-signed-smoke-grant.json> --owner-config <local-owner-config.json> --release-manifest <local-stageA-release-manifest.json>

# Stage C — Dry-Run DEV Collection Plan Validation (0 sockets)
python aurum-desk/tools/run_kh_03_r6_capture.py --stage dev --dry-run --plan <local-dev-plan.json>

# Stage C — Owner-Authorized Bounded DEV Collection (ONLY after Stage B PASS + separate signed DEV grant)
python aurum-desk/tools/run_kh_03_r6_capture.py --stage dev --grant <local-signed-dev-grant.json> --owner-config <local-owner-config.json> --release-manifest <local-stageA-release-manifest.json>

# Independent Read-Only Separate-OS Verifier
python aurum-desk/tools/kh_03_r6_independent_verifier.py --run-dir <unique-run-dir>
```

---

## 5. Local Data Stores Outside Git & Retention Policy

All sensitive local stores MUST reside on a local fixed NTFS/POSIX volume **outside the Git repository** with restricted OS ACLs (never on UNC/network/cloud-sync drives, never committed to Git):
- Owner Trust Root JSON (Ed25519 public key + fingerprint + revocation metadata)
- Durable SQLite Grant Ledger (`ledger.sqlite3`) + External Anchor (`ledger.anchor.json`)
- Raw DEV Market Data Chunks + Custody Chain + Custody Anchor (`DEV_ONLY` root, bounded per-run size & explicit owner retention policy)
- Signed One-Shot Owner Grants (local files never checked into Git)

Only redacted aggregate manifests, QA receipts, SHA-256 digests, and Stage A engineering verification artifacts are committed under `aurum-desk/docs/artifacts/kh_03_r6_observed_market_data/`.
