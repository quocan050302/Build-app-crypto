import os
import threading
from typing import Any, Dict, Optional, Tuple

ENV_BE_RUNTIME_PROFILE = "AURUM_BE_RUNTIME_PROFILE"
PROFILE_TRADING = "TRADING"
PROFILE_OBSERVE_ONLY = "OBSERVE_ONLY"
ALLOWED_RUNTIME_PROFILES: Tuple[str, ...] = (PROFILE_TRADING, PROFILE_OBSERVE_ONLY)

ALL_BACKGROUND_TRADING_WORKERS: Tuple[str, ...] = (
    "bitget_ws_service.run_ws_loop",
    "execution_consumer.run_consumer_loop",
    "candle_cache_service.initialize",
    "collector_service.run_collector_loop",
    "strategy_service.run_strategy_loop",
    "proximity_service.run_proximity_loop",
    "execution_coordinator.run_coordinator_loop",
    "exit_monitor.run_exit_monitor_loop",
    "market_reconciliation_service.run_reconciliation_loop",
    "process_notification_outbox",
    "forward_repair_worker.run",
    "lesson_repair_worker.run_loop",
    "scalp_watcher_orchestrator.run_watcher_loop",
    "cg01_evaluation_scheduler.run",
    "cg01_projection_worker.run",
)

OBSERVE_ONLY_ALLOWED_WORKERS: Tuple[str, ...] = ()

_COUNTER_LOCK = threading.Lock()

_DEFAULT_COUNTERS: Dict[str, int] = {
    "PAPER_ORDERS_CREATED": 0,
    "PAPER_ORDERS_FILLED": 0,
    "PAPER_POSITIONS_CLOSED": 0,
    "LIVE_EXCHANGE_ORDERS_SENT": 0,
    "UNAUTHORIZED_DB_WRITES": 0,
    "BACKGROUND_TRADING_WORKERS_STARTED": 0,
    "UNEXPECTED_RUNTIME_ERRORS": 0,
    "BLOCKED_DB_WRITE_ATTEMPTS": 0,
    "BLOCKED_HTTP_MUTATION_ATTEMPTS": 0,
    "BLOCKED_ARM_ATTEMPTS": 0,
    "BLOCKED_FILL_ATTEMPTS": 0,
    "BLOCKED_CLOSE_ATTEMPTS": 0,
    "BLOCKED_WORKER_START_ATTEMPTS": 0,
    "BLOCKED_STARTUP_MUTATION_ATTEMPTS": 0,
}

_RUNTIME_SAFETY_COUNTERS: Dict[str, int] = dict(_DEFAULT_COUNTERS)


def resolve_be_runtime_profile(raw_value: Optional[str] = None) -> str:
    """
    Resolves and validates AURUM_BE_RUNTIME_PROFILE.
    Defaults to TRADING when unset or empty.
    Fails closed with RuntimeError if set to an unknown value.
    """
    raw = raw_value if raw_value is not None else os.environ.get(ENV_BE_RUNTIME_PROFILE, "")
    if raw is None or str(raw).strip() == "":
        return PROFILE_TRADING
    normalized = str(raw).strip().upper()
    if normalized not in ALLOWED_RUNTIME_PROFILES:
        raise RuntimeError(
            f"INVALID_BE_RUNTIME_PROFILE: Unsupported {ENV_BE_RUNTIME_PROFILE}={raw!r}. "
            f"Allowed values: {list(ALLOWED_RUNTIME_PROFILES)}"
        )
    return normalized


def is_observe_only_runtime() -> bool:
    """Returns True when AURUM_BE_RUNTIME_PROFILE is OBSERVE_ONLY. Fails closed on invalid profile."""
    return resolve_be_runtime_profile() == PROFILE_OBSERVE_ONLY


def reset_runtime_safety_counters() -> None:
    with _COUNTER_LOCK:
        _RUNTIME_SAFETY_COUNTERS.clear()
        _RUNTIME_SAFETY_COUNTERS.update(_DEFAULT_COUNTERS)


def increment_safety_counter(name: str, amount: int = 1) -> None:
    with _COUNTER_LOCK:
        _RUNTIME_SAFETY_COUNTERS[name] = int(_RUNTIME_SAFETY_COUNTERS.get(name, 0)) + int(amount)


def get_runtime_safety_counters() -> Dict[str, int]:
    with _COUNTER_LOCK:
        return dict(_RUNTIME_SAFETY_COUNTERS)


def get_runtime_safety_snapshot() -> Dict[str, Any]:
    profile = resolve_be_runtime_profile()
    observe_only = profile == PROFILE_OBSERVE_ONLY
    return {
        "runtime_profile": profile,
        "observe_only": observe_only,
        "trading_enabled": not observe_only,
        "live_trading_prohibited": True,
        "allowed_workers": list(OBSERVE_ONLY_ALLOWED_WORKERS) if observe_only else list(ALL_BACKGROUND_TRADING_WORKERS),
        "blocked_trading_workers": list(ALL_BACKGROUND_TRADING_WORKERS) if observe_only else [],
        "counters": get_runtime_safety_counters(),
    }


def assert_not_observe_only(operation: str, counter_key: Optional[str] = None) -> None:
    """
    Hard boundary guard for order execution, worker loops, and startup mutations.
    Raises RuntimeError immediately when invoked under OBSERVE_ONLY runtime profile.
    """
    if is_observe_only_runtime():
        if counter_key:
            increment_safety_counter(counter_key, 1)
        raise RuntimeError(
            f"OBSERVE_ONLY_BLOCKED: Operation '{operation}' is prohibited under "
            f"{ENV_BE_RUNTIME_PROFILE}={PROFILE_OBSERVE_ONLY}."
        )

