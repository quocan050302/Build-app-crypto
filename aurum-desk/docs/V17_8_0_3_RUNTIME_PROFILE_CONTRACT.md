# AURUM DESK V17.8.0.3 — RUNTIME PROFILE & EXECUTION BOUNDARY CONTRACT

**Release:** `V17.8.0.3`  
**Module:** `backend/services/runtime_profile_service.py`

---

## 1. Supported Runtime Profiles

`AURUM_BE_RUNTIME_PROFILE` supports three validated values (defaulting to `TRADING` when unset; failing closed with `INVALID_BE_RUNTIME_PROFILE` on any unknown value):

| Capability | `TRADING` | `OBSERVE_ONLY` | `MARKET_SHADOW` |
|---|---|---|---|
| `can_read_public_market_data` | `True` | `False` | `True` |
| `can_evaluate_shadow_strategies` | `True` | `False` | `True` |
| `can_write_shadow_research` | `True` | `False` | `True` |
| `can_read_operational_data` | `True` | `True` | `True` |
| `can_write_operational_data` | `True` | `False` | `False` |
| `can_arm_paper` | `True` | `False` | `False` |
| `can_fill_paper` | `True` | `False` | `False` |
| `can_close_paper` | `True` | `False` | `False` |
| `can_submit_live_exchange_order` | `False` | `False` | `False` |
| `can_modify_trading_policy` | `True` | `False` | `False` |

---

## 2. Guarded Execution & Persistence Sinks

Every order-mutating, policy-mutating, and operational DB write boundary calls `assert_runtime_capability(...)` or `assert_operational_db_write_allowed(...)` and raises `RuntimeError("MARKET_SHADOW_EXECUTION_FORBIDDEN: ...")` in `MARKET_SHADOW`:

1. `ScalpWatcherOrchestrator._arm_paper_candidate` (`can_arm_paper`)
2. `ExecutionCoordinator.evaluate_orders_sync` (`can_fill_paper`)
3. `ExecutionConsumer.evaluate_quote_sync` (`can_fill_paper`)
4. `PaperBroker.check_pending_fills` (`can_fill_paper`)
5. `TradeLifecycleService.execute_fill` (`can_fill_paper`)
6. `TradeLifecycleService.execute_reject` (`can_close_paper`)
7. `TradeLifecycleService.execute_close` (`can_close_paper`)
8. `TradeLifecycleService.process_exit_tick` (`can_close_paper`)
9. `crud.create_paper_order` (`can_arm_paper`)
10. `crud.update_paper_order_state` (`can_write_operational_data`)
11. `StopPlacementPolicyService.update_policy` (`can_modify_trading_policy`)
12. `TradingPolicyService.update_policy` (`can_modify_trading_policy`)
13. SQLAlchemy `before_flush` and DBAPI `before_cursor_execute` listeners on the operational engine (`backend/database.py`)
14. FastAPI HTTP middleware `enforce_observe_only_http_boundary` (`backend/main.py`), which blocks all `POST`/`PUT`/`PATCH`/`DELETE` requests in both `OBSERVE_ONLY` and `MARKET_SHADOW` with HTTP `403` (`MARKET_SHADOW_EXECUTION_FORBIDDEN`) and blocks all non-`GET`/`HEAD`/`OPTIONS` requests to `/api/v1/shadow/*` across all profiles.

