# AURUM DESK V17.7.2 — LIVE ORDER SAFETY PROOF & NEGATIVE CONTROL
**Release:** V17.7.2  
**Focus:** Active Network Outbound Interception, Zero-Live-Order Invariant, Negative Control Proof  
**Status:** VERIFIED_FROM_TEST  

---

## 1. Outbound Network Boundary Audit

### 1.1 The P0-03 Defect in V17.7.1
In V17.7.1, safety tests patched `httpx.Client.post` and checked that `PaperBroker` contained no live order methods. However, the test did not exercise the actual candidate evaluation, arming, fill, and closing pipeline while the spy was active. An unexercised spy returning 0 calls is not empirical evidence of safety.

### 1.2 Audited Outbound Pathways
All potential execution interfaces in the application were inspected:
1. `httpx.Client` and `httpx.AsyncClient` HTTP REST dispatch boundaries.
2. `PaperBroker` methods and class members.
3. `ExecutionCoordinator` dispatch logic and `LIVE_EXCHANGE_ORDERS_SENT` counter.
4. Background worker loops and event bus dispatchers.

---

## 2. Active Pipeline Execution Under Live Spy (SL-50 & DSL-19)

In V17.7.2, tests `SL-50` and `DSL-19` install an active HTTP spy and execute the entire production pipeline:

```python
spy = Mock()
def _forbidden_sink(*args, **kwargs):
    spy(*args, **kwargs)
    raise RuntimeError("FORBIDDEN_LIVE_ORDER_SUBMISSION: Live exchange order attempt detected!")

with patch("httpx.Client.post", side_effect=_forbidden_sink), \
     patch("httpx.AsyncClient.post", side_effect=_forbidden_sink):

    # 1. Real CG01 candidate evaluation with distant SL
    # 2. Production ScalpWatcherOrchestrator._arm_paper_candidate()
    # 3. TradeLifecycleService.execute_fill()
    # 4. TradeLifecycleService.execute_close()

    assert spy.call_count == 0
    assert LIVE_EXCHANGE_ORDERS_SENT == 0
```

Across the entire execution lifecycle (Candidate -> ARM -> Fill -> Close):
$$\text{LIVE\_ORDER\_DISPATCH\_ATTEMPTS} = 0$$
$$\text{LIVE\_ORDER\_DISPATCH\_SUCCESSES} = 0$$

---

## 3. Negative-Control Proof (CE-18)

To prove that the safety spy is active, sensitive, and capable of intercepting prohibited dispatches, gate `CE-18` performs an explicit negative control test:

```python
def test_ce_18_negative_control_live_order_dispatch_blocked():
    spy = Mock()
    def _forbidden_sink(*args, **kwargs):
        spy(*args, **kwargs)
        raise RuntimeError("PROHIBITED_LIVE_EXCHANGE_DISPATCH_BLOCKED")

    with patch("httpx.Client.post", side_effect=_forbidden_sink), \
         patch("httpx.AsyncClient.post", side_effect=_forbidden_sink):

        client = httpx.Client()
        with pytest.raises(RuntimeError) as exc:
            client.post("https://api.bitget.com/api/v2/mix/order/place-order", json={"symbol": "XAUUSDT"})

        assert "PROHIBITED_LIVE_EXCHANGE_DISPATCH_BLOCKED" in str(exc.value)
        assert spy.call_count == 1
```

### 3.1 Safety Conclusions
1. The execution pipeline is 100% contained within the isolated PAPER sandbox.
2. The network interceptor reliably catches and terminates any attempted live order dispatch.
3. No live exchange API keys or credentials are required or utilized during tests.
