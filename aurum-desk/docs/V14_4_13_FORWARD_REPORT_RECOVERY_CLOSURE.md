# V14.4.13 Forward Session Binding, Verified Outcomes, Realized Drawdown & Truthful Recovery Closure Report

## 1. Metadata & Execution Summary
- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Baseline Reviewer SHA**: `aef803cadb3f02efe2a35d8ce9e2bedd53b55903`
- **Actual Base SHA**: `aef803cadb3f02efe2a35d8ce9e2bedd53b55903`
- **Report Date**: 2026-10-14
- **Testing Mode**: Strict PAPER / Dry-run only (No live Bitget orders, no real Telegram notifications, no user DB/ledger overwrites)
- **Targeted Backend Files**: 53 files (457 baseline tests + 18 V14.4.13 tests = **475 tests**)
- **Mandatory Manifest Cases**: All required cases PASS (including S01–S03 [MBR01–MBR03], O01–O02 [AUTH01–AUTH02], D01 [DD01], R01–R04 [REC01–REC04])

---

## 2. Before / After Bug Traces & Mathematical Proofs

### A. Forward Session Membership Leak (PHẦN 002, 016–027)
- **Before Fix**:
  - `generate_forward_paper_report` and `export_forward_paper_data` used temporal heuristics:
    ```sql
    session_instance_id == sess.session_id OR created_at >= sess.start_time_ms
    ```
  - Any order created after `start_time_ms` was treated as part of the session, leaking unrelated orders, overlapping test runs, and unattached trades into the session report.
  - In addition, storing session identity in `PaperOrder.session_instance_id` risked corrupting trading session quota tags (`ASIA_...`, `LONDON_...`, `NY_...`).
- **After Fix**:
  - Introduced explicit `ForwardOrderMembership` abstraction persisted in `SystemConfig` (`key="fwd_order_mem:<order_id>"` and index `"fwd_sess_ords:<session_id>"`).
  - Explicit binding via `bind_order_to_forward_session` and `bind_order_to_active_forward_session`.
  - Boundary: Automatically bound upon ARM in `StrategyService._auto_arm_candidate`, and membership status updated during `TradeLifecycleService.execute_fill`, `execute_close`, and `execute_reject`.
  - Preserves `PaperOrder.session_instance_id` for NY quota and session tracking intact without overwrite.
  - Queries (`load_forward_session_orders`) select strictly from explicit session index. Overlapping sessions (Session A and Session B) and unbound orders are cleanly isolated. Unknown session IDs return empty / `NOT_FOUND`.

### B. Confirmed Outcomes vs Lifecycle State (PHẦN 003, 033–045)
- **Before Fix**:
  - Every order with lifecycle state `closed` was dumped into `confirmed_closed_trades`.
  - Orders closed with unverified data, corrupt PnL (`None` or `NaN`), or pending conflict reviews were factored into winrate and net PnL, with missing PnL often silently masked as `or 0.0` (falsely treated as breakeven).
- **After Fix**:
  - Clear separation: `closed` is a lifecycle stage; `CONFIRMED` is an outcome authority status.
  - Implemented pure classifier `classify_forward_outcome` returning:
    - `CONFIRMED`: Verified terminal close (`TP_HIT`, `SL_HIT`, `MANUAL_CLOSE`, etc.), valid closed timestamp, finite non-null PnL and R, and no unresolved conflict review.
    - `REVIEW_PENDING`: Closed or open with active review task.
    - `CONFLICTED`: Closed with unresolved conflict audit evidence.
    - `UNVERIFIED`: Missing exit cause or closed timestamp.
    - `DATA_INVALID`: Missing or non-finite PnL/R (`realized_pnl_net is None` is NOT breakeven).
    - `OPEN` / `ARMED` / `REJECTED_OR_EXPIRED`.
  - Strict denominator: `winrate = confirmed_wins / (confirmed_wins + confirmed_losses + breakevens)`. Quarantined, conflicted, unverified, and invalid trades are strictly excluded from the winrate denominator.
  - Report queries pre-load review records for member orders without mutating any trading tables.

### C. Realized Maximum Drawdown Calculation (PHẦN 004, 046–051)
- **Before Fix**:
  - The legacy algorithm tracked `max_drawdown_usd`, but calculated percentage drawdown as:
    ```python
    dd_pct = (max_dd_usd / final_peak_equity) * 100.0  # BUG!
    ```
  - In a sequence like `1000 -> 900 -> 2000`, the peak at the time of the trough was \$1000 (drawdown = \$100, which is 10.0%). Dividing by final peak \$2000 falsely yielded 5.0%.
- **After Fix**:
  - Implemented `compute_realized_drawdown` tracking point-in-time running peak at each closed cashflow.
  - Independent USD and % maxima tracking:
    - **Repro Example 1**: `1000 -> 900 -> 2000`
      - Starting: \$1000
      - Trade 1: -\$100 (equity \$900, peak \$1000) $\to$ DD USD = \$100, DD % = 10.0%
      - Trade 2: +\$1100 (equity \$2000, peak \$2000) $\to$ DD USD = \$0, DD % = 0.0%
      - Max DD USD: **\$100.0**, Max DD %: **10.0%** (satisfies PHẦN 051)
    - **Repro Example 2**: `1000 -> 800 -> 2000 -> 1700`
      - Starting: \$1000
      - Trade 1: -\$200 (equity \$800, peak \$1000) $\to$ DD USD = \$200, DD % = 20.0%
      - Trade 2: +\$1200 (equity \$2000, peak \$2000) $\to$ DD USD = \$0, DD % = 0.0%
      - Trade 3: -\$300 (equity \$1700, peak \$2000) $\to$ DD USD = \$300, DD % = 15.0%
      - Max DD USD: **\$300.0** (occurred at trade 3)
      - Max DD %: **20.0%** (occurred at trade 1)
      - Proves USD and % maxima occur at different points and must be tracked independently.
  - Negative equity is never clamped to 100% to obscure insolvency; edge cases (empty, only wins, same timestamp tie-break) are handled deterministically.

### D. Truthful Recovery & Progress Continuation (PHẦN 005, 052–061)
- **Before Fix**:
  - `retry_review_task` unconditionally marked review records as `RECOVERED` whenever `read_complete=True`.
  - `read_complete=True` simply means the reader finished scanning evidence pages; it does NOT mean coverage is complete, terminal conflict is resolved, or lifecycle close has been committed.
  - Every retry attempt reset window progress to 0, preventing large windows from advancing across bounded budget slices.
- **After Fix**:
  - Implemented `classify_review_resolution` distinguishing:
    - `STILL_INVALID`: Context still corrupted; back off without event spam.
    - `MORE_WORK`: Read budget exhausted; cursor advanced and persisted.
    - `WAITING_COVERAGE`: Terminal candidate found but coverage assessment is incomplete/partial/unknown. Does NOT close order or mark recovered.
    - `REVIEW_REQUIRED`: Terminal conflict detected (e.g. quote before recorded close); audit committed; task transitions to `MANUAL_REVIEW_REQUIRED`. Never fakes recovered.
    - `APPLY_FAILED`: Terminal candidate valid but DB execution failed; stays in retry.
    - `RECOVERED`: Terminal decision successfully applied/committed, or closed order verified clean.
    - `NO_TERMINAL_CONFIRMED`: Evidence read complete, no exit hit; order remains open/unconfirmed.
  - Continuation: `retry_review_task` preserves `EvidenceProgress` accumulator and cursor across retries. Large windows advance across budget slices without resetting to 0. Reset occurs only once when `trigger_source_repair_recheck` increments `source_revision`.

---

## 3. Production Architecture & Call Flow

```
[ Market Events / Realtime Quotes ]
               │
               ▼
[ StrategyService: evaluate_market_reading_cycle ]
               │
               ├─► Candidate Certified (READY)
               │
               ▼
[ StrategyService: _auto_arm_candidate ] (ARM boundary)
               │
               ├─► PaperOrder state = "armed"
               ├─► bind_order_to_active_forward_session()  <── Explicit forward binding
               │
               ▼
[ ExecutionCoordinator / TradeLifecycleService: execute_fill ]
               │
               ├─► PaperOrder state = "paper_open"
               ├─► update_order_forward_membership_status("FILLED")
               │
               ▼
[ TradeLifecycleService: execute_close ]
               │
               ├─► PaperOrder state = "closed"
               ├─► update_order_forward_membership_status("CLOSED")
               │
               ▼
[ Forward Paper Queries & Reporting ]
               │
               ├─► load_forward_session_orders()   <── Pure index query (no time leak)
               ├─► classify_forward_outcome()      <── Verified authority status
               ├─► compute_realized_drawdown()     <── Point-in-time running peak DD
               └─► generate_forward_paper_report() <── Read-only pure aggregation
```

---

## 4. Test Execution & Gate Verification

### Commands Run:
1. `backend/venv/Scripts/python.exe -m pytest backend/tests/test_v14_4_13_forward_membership.py backend/tests/test_v14_4_13_outcome_authority.py backend/tests/test_v14_4_13_drawdown.py backend/tests/test_v14_4_13_review_resolution.py -v`
   - **Result**: 18 passed in 0.95s.
2. `backend/venv/Scripts/python.exe tools/run_v14_4_2_acceptance.py --python backend/venv/Scripts/python.exe`
   - **Targeted files**: 53 files.
   - **All Gates Passed**:
     - `backend_targeted`: PASS (475 passed)
     - `frontend_typecheck`: PASS
     - `frontend_lint`: PASS
     - `frontend_vitest`: PASS
     - `frontend_build`: PASS
     - `browser_e2e`: PASS
     - `benchmark`: PASS
     - `git_diff_check`: PASS

---

## 5. Preservation & Safety Disclaimers
1. **SMC & Risk Rules**: Preserved standard SMC certified chain, entry conditions, SL/TP pricing, risk limits, 3-session execution, and protective exits.
2. **Quota Invariant**: Preserved max 3 actual fills/day UTC+7 and NY reserve protection.
3. **No Financial Guarantees**: This acceptance verifies software behavioral correctness, truthful reporting, and deterministic recovery. It makes NO claim of guaranteed future profits or automated trading success.
