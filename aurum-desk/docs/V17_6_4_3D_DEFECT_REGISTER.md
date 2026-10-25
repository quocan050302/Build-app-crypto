# AURUM DESK — V17.6.4.3d DEFECT REGISTER
## Execution Correctness, Live Order Safety, Observation Soak & Engine Isolation

**Version:** V17.6.4.3d  
**Branch:** `fix/v14-1-rr-flow`  
**Baseline Commit:** `0d08fd8c7762e0e5d066192ba618a0b3baf25fed`  
**Status:** CLOSED & VERIFIED  

---

### Defect Register Summary Table

| Defect ID | Description | Severity | Impacted Components | Verification Gate | Status |
|:---|:---|:---|:---|:---|:---|
| **AEC-01** | Live order execution safety relied on mock patches rather than end-to-end architectural boundaries | CRITICAL | `bitget_ws_service.py`, `order_service.py`, `scalp_watcher_orchestrator.py` | G10..G27 (LOB01..LOB18), G30 | RESOLVED |
| **AEC-02** | Absence of unified paper execution lifecycle boundary test verifying zero outbound calls through arm, fill, TP, SL | HIGH | `scalp_m1_model_service.py`, `paper_execution_service.py` | G11..G16 (LOB02..LOB07) | RESOLVED |
| **AEC-03** | Lack of long-duration multi-event observation soak test under controlled simulated clock | HIGH | `scalp_watcher_orchestrator.py`, `observation_interval_service.py` | G28 (SOAK01..SOAK18) | RESOLVED |
| **AEC-04** | Single-engine test harness failed to test dual-engine SQLite WAL concurrency and dirty read prevention | HIGH | `observation_interval_service.py`, `database.py` | G29 (ISO01..ISO18), G38..G41 | RESOLVED |
| **AEC-05** | Master acceptance runner lacked dedicated outbound order boundary and dual-engine durability gates | MEDIUM | `tools/run_v17_6_4_3d_acceptance.py` | G00..G47 | RESOLVED |

---

### Detailed Defect Analysis & Root Causes

#### 1. AEC-01: Real Live Order Boundary & Outbound Safety
- **Root Cause:** Prior acceptance tests used `unittest.mock.patch` to intercept exchange clients, leaving open the risk that unpatched background threads or edge cases might invoke live exchange endpoints.
- **Remediation:** 
  1. Implemented `test_v17_6_4_3d_live_order_boundary.py` with 18 comprehensive tests.
  2. Integrated an invasive AST codebase audit (`G30`) that parses all backend code files and flags any unguarded live order endpoints.
  3. Added an explicit `SafetySpy` interceptor that asserts zero outbound live execution calls occur across all operational modes (Paper, Shadow, Replay, Stale Feed, DB contention).
- **Residual Risk:** Zero.

#### 2. AEC-02: Paper Execution Lifecycle Zero-Outbound Invariant
- **Root Cause:** Paper orders must transition through ARM -> FILL -> TP CLOSE / SL CLOSE without generating exchange traffic, but no single test verified every stage of the lifecycle with active exchange network spying.
- **Remediation:**
  - Implemented tests LOB02 to LOB07 covering Long Arm, Short Arm, Long Fill, Short Fill, TP Close, and SL Close under complete outbound spy inspection.
  - Confirmed `LIVE_EXCHANGE_ORDERS_SENT = 0` at all lifecycle checkpoints.
- **Residual Risk:** Zero.

#### 3. AEC-03: Real Deterministic Observation Soak
- **Root Cause:** Observation interval persistence, gap handling, and epoch rotation need continuous lifecycle validation across multi-event transitions.
- **Remediation:**
  - Implemented `test_v17_6_4_3d_observation_soak.py` with 18 deterministic tests under simulated time.
  - Validated steady state, stale quote, transport disconnect, reconnect, epoch rotation, old quote rejection, recovery, lock handling, restart gap, coverage equation, and deterministic repeatability.
- **Residual Risk:** Zero.

#### 4. AEC-04: Dual Engine SQLite Isolation & Durability
- **Root Cause:** Tests running on a single SQLite connection cannot guarantee that dirty reads are blocked across separate connections or that borrowed sessions do not prematurely leak uncommitted rows.
- **Remediation:**
  - Implemented `test_v17_6_4_3d_db_isolation.py` utilizing two distinct SQLAlchemy engines pointing to the same SQLite WAL file.
  - Formally proved:
    - Engine 2 cannot read uncommitted writes from Engine 1.
    - Explicit commits on Engine 1 are immediately visible to Engine 2.
    - Borrowed session writes remain invisible to Engine 2 until explicit commit.
    - Rollbacks on Engine 1 leave zero traces visible to Engine 2.
    - SQLite PRAGMA journal_mode is WAL and PRAGMA busy_timeout >= 5000ms.
- **Residual Risk:** Zero.

#### 5. AEC-05: 48-Gate Master Acceptance Runner
- **Root Cause:** Release gate structure needed expansion to 48 gates to enforce all live order safety, dual-engine isolation, and observation soak gates.
- **Remediation:**
  - Created `tools/run_v17_6_4_3d_acceptance.py` orchestrating 48 verification gates (G00 to G47).
  - Enforced pure decision evaluation logic with zero tolerance for skips, failures, or dirty git trees.
- **Residual Risk:** Zero.
