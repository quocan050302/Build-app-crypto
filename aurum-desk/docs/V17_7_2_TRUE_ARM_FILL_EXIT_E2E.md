# AURUM DESK V17.7.2 — TRUE ARM, FILL, AND EXIT END-TO-END LIFECYCLE
**Release:** V17.7.2  
**Focus:** Production ARM Boundary, Durable State Transitions, Fill Quantity Sizing, Bid/Ask Exit Evaluation  
**Status:** VERIFIED_FROM_TEST & VERIFIED_FROM_PERSISTENCE  

---

## 1. Production ARM Boundary Integration (SL-43 & CE-13)

### 1.1 The P1-02 Problem in V17.7.1
In V17.7.1, end-to-end tests manually created `PaperOrder(state="armed")`, completely bypassing:
- Opportunity funnel transition checks.
- WatchSetup instance binding.
- Immutable Scenario Snapshot persistence.
- Real Entry Guard verification (session, risk, active position, daily cap).
- ARM plan snapshot serialization.

### 1.2 The Production ARM Invocation in V17.7.2
In V17.7.2, orders are armed exclusively through `ScalpWatcherOrchestrator._arm_paper_candidate()`:
```python
armed_order = orch._arm_paper_candidate(
    db=db,
    cand=candidate,
    now_ms=now_ms,
    clock=live_clock,
    quote=validated_quote,
    policy_settings=policy_settings,
    auto_enabled=True,
)
assert armed_order is not None
assert armed_order.state == "armed"
```

### 1.3 Cross-Session Durability Verification
After `_arm_paper_candidate` completes:
1. Transaction is committed via `db.commit()`.
2. Initial DB session is closed via `db.close()`.
3. A fresh, independent session is instantiated via `SessionLocal()`.
4. Order is re-queried from disk.
5. All frozen fields are verified: `stop_loss`, `take_profit`, `planned_entry`, `quantity`, `initial_risk_usdt`, and embedded stop placement metadata.

---

## 2. Execution Coordinator Fill Sizing (SL-46 & CE-14)

### 2.1 Quantity Preservation & Downsizing Invariant
At fill time, market conditions or risk parameters may differ from the moment of candidate generation.
Under Section 10.3 and Gate SL-46 / CE-14:
$$\text{final\_quantity} \le \text{armed\_quantity}$$

In `TradeLifecycleService.execute_fill()`:
```python
effective_fill_qty = (
    min(float(calc_result.quantity), float(order.quantity))
    if (order.quantity is not None and float(order.quantity) > 0)
    else float(calc_result.quantity)
)
```
- If the recalculation permits a smaller size (e.g., due to widened slippage or reduced equity), the position is downsized to protect risk capital.
- If the recalculation permits a larger size (e.g., higher capital or lower volatility), the position is **never** increased beyond the frozen `armed_quantity`.
- The stop loss price cannot be widened during fill under any circumstances.

---

## 3. Bid/Ask Order Exit Triggering & Realized PnL (SL-47, SL-48, SL-60 & CE-15)

### 3.1 Asymmetric Bid/Ask Stop Triggering
Market orders and stops must trigger against executable liquidity, not mid-prices or synthetic averages:
- **LONG Positions:** Exit (SL or TP) is triggered when executable **Bid** breaches the threshold.
- **SHORT Positions:** Exit (SL or TP) is triggered when executable **Ask** breaches the threshold.

This is verified via `evaluate_order_exit()` in `backend/services/shared_exit_evaluator.py`.

### 3.2 Persisted Close & Lifecycle Completion
Once triggered:
1. `TradeLifecycleService.execute_close()` transitions the order from `paper_open` to `closed`.
2. Computes `realized_pnl_net` factoring in trading fees and entry/exit slippage.
3. Records `exit_cause` (e.g., `TP_HIT`, `SL_HIT`).
4. Persists the closed state to SQLite.
5. Durability verified by re-querying in a 3rd independent session.

```text
Lifecycle Stage Trace:
Candidate (CG01/M1)
    ↓ [ScalpWatcherOrchestrator._arm_paper_candidate]
ARMED (PaperOrder.state = "armed", frozen SL/TP/Qty)
    ↓ [TradeLifecycleService.execute_fill]
PAPER_OPEN (PaperOrder.state = "paper_open", actual_entry, qty <= armed_qty)
    ↓ [evaluate_order_exit (Bid for Long, Ask for Short)]
TRIGGERED (ExitResult.triggered = True, cause = TP_HIT / SL_HIT)
    ↓ [TradeLifecycleService.execute_close]
CLOSED (PaperOrder.state = "closed", realized_pnl_net, exit_evidence)
```
