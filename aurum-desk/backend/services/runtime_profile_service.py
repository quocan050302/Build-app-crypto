import os
import threading
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple

ENV_BE_RUNTIME_PROFILE = "AURUM_BE_RUNTIME_PROFILE"
ENV_RUNTIME_PROFILE = ENV_BE_RUNTIME_PROFILE

PROFILE_TRADING = "TRADING"
PROFILE_OBSERVE_ONLY = "OBSERVE_ONLY"
PROFILE_MARKET_SHADOW = "MARKET_SHADOW"

ALLOWED_RUNTIME_PROFILES: Tuple[str, ...] = (
    PROFILE_TRADING,
    PROFILE_OBSERVE_ONLY,
    PROFILE_MARKET_SHADOW,
)

# In OBSERVE_ONLY mode, zero background trading workers are permitted to start.
OBSERVE_ONLY_ALLOWED_WORKERS: Tuple[str, ...] = ()

# In MARKET_SHADOW mode, only isolated shadow market-data & shadow analysis workers may start;
# zero trading/order-execution workers are permitted.
MARKET_SHADOW_ALLOWED_WORKERS: Tuple[str, ...] = (
    "bitget_ws_service.run_ws_loop[MARKET_SHADOW]",
    "market_shadow_service.run_shadow_loop",
)

ALL_BACKGROUND_TRADING_WORKERS: Tuple[str, ...] = (
    "bitget_ws_service.run_ws_loop",
    "execution_consumer.run_consumer_loop",
    "candle_cache_service.run_persistence_worker",
    "collector_service.run_collector_loop",
    "strategy_service.run_strategy_loop",
    "proximity_service.run_proximity_loop",
    "execution_coordinator.run_coordinator_loop",
    "exit_monitor.run_exit_monitor_loop",
    "market_reconciliation_service.run_late_reconciliation_loop",
    "process_notification_outbox",
    "forward_repair_worker.run_repair_loop",
    "lesson_repair_worker.run_repair_loop",
    "scalp_watcher_orchestrator.run_watcher_loop",
    "cg01_evaluation_scheduler.run",
    "cg01_projection_worker.run",
)


@dataclass(frozen=True)
class RuntimeCapabilities:
    can_read_public_market_data: bool
    can_evaluate_shadow_strategies: bool
    can_write_shadow_research: bool
    can_read_operational_data: bool
    can_write_operational_data: bool
    can_arm_paper: bool
    can_fill_paper: bool
    can_close_paper: bool
    can_submit_live_exchange_order: bool
    can_modify_trading_policy: bool

    def to_dict(self) -> Dict[str, bool]:
        return asdict(self)


PROFILE_CAPABILITIES: Dict[str, RuntimeCapabilities] = {
    PROFILE_TRADING: RuntimeCapabilities(
        can_read_public_market_data=True,
        can_evaluate_shadow_strategies=True,
        can_write_shadow_research=True,
        can_read_operational_data=True,
        can_write_operational_data=True,
        can_arm_paper=True,
        can_fill_paper=True,
        can_close_paper=True,
        can_submit_live_exchange_order=False,
        can_modify_trading_policy=True,
    ),
    PROFILE_OBSERVE_ONLY: RuntimeCapabilities(
        can_read_public_market_data=False,
        can_evaluate_shadow_strategies=False,
        can_write_shadow_research=False,
        can_read_operational_data=True,
        can_write_operational_data=False,
        can_arm_paper=False,
        can_fill_paper=False,
        can_close_paper=False,
        can_submit_live_exchange_order=False,
        can_modify_trading_policy=False,
    ),
    PROFILE_MARKET_SHADOW: RuntimeCapabilities(
        can_read_public_market_data=True,
        can_evaluate_shadow_strategies=True,
        can_write_shadow_research=True,
        can_read_operational_data=True,
        can_write_operational_data=False,
        can_arm_paper=False,
        can_fill_paper=False,
        can_close_paper=False,
        can_submit_live_exchange_order=False,
        can_modify_trading_policy=False,
    ),
}

_COUNTERS_LOCK = threading.Lock()
_DEFAULT_COUNTERS: Dict[str, int] = {
    "BACKGROUND_TRADING_WORKERS_STARTED": 0,
    "UNAUTHORIZED_TRADING_WORKERS_STARTED": 0,
    "SHADOW_WORKERS_STARTED": 0,
    "PAPER_ORDERS_CREATED": 0,
    "PAPER_ORDERS_FILLED": 0,
    "PAPER_POSITIONS_CLOSED": 0,
    "LIVE_EXCHANGE_ORDERS_SENT": 0,
    "OPERATIONAL_DB_WRITES": 0,
    "SHADOW_DB_WRITES": 0,
    "BLOCKED_WORKER_START_ATTEMPTS": 0,
    "BLOCKED_ARM_ATTEMPTS": 0,
    "BLOCKED_FILL_ATTEMPTS": 0,
    "BLOCKED_CLOSE_ATTEMPTS": 0,
    "BLOCKED_LIVE_ORDER_ATTEMPTS": 0,
    "BLOCKED_POLICY_MUTATION_ATTEMPTS": 0,
    "BLOCKED_HTTP_MUTATION_ATTEMPTS": 0,
    "BLOCKED_DB_WRITE_ATTEMPTS": 0,
    "BLOCKED_STARTUP_MUTATION_ATTEMPTS": 0,
    "UNAUTHORIZED_DB_WRITES": 0,
    "UNEXPECTED_RUNTIME_ERRORS": 0,
}
_SAFETY_COUNTERS: Dict[str, int] = dict(_DEFAULT_COUNTERS)


def resolve_be_runtime_profile(explicit_value: Optional[str] = None) -> str:
    """
    Resolve and validate AURUM_BE_RUNTIME_PROFILE.
    Defaults to TRADING when unset or empty for backward compatibility.
    Fails closed with RuntimeError if set to an unrecognized profile value.
    """
    raw = explicit_value if explicit_value is not None else os.environ.get(ENV_BE_RUNTIME_PROFILE, "")
    normalized = str(raw or "").strip().upper()
    if not normalized:
        return PROFILE_TRADING
    if normalized not in ALLOWED_RUNTIME_PROFILES:
        raise RuntimeError(
            f"INVALID_BE_RUNTIME_PROFILE: Unsupported {ENV_BE_RUNTIME_PROFILE}={raw!r}. "
            f"Allowed values: {ALLOWED_RUNTIME_PROFILES}"
        )
    return normalized


def get_runtime_capabilities(profile: Optional[str] = None) -> RuntimeCapabilities:
    """Return the typed RuntimeCapabilities contract for the active or specified runtime profile."""
    resolved = resolve_be_runtime_profile(profile)
    return PROFILE_CAPABILITIES[resolved]


def is_observe_only_runtime() -> bool:
    """Return True if the active runtime profile is OBSERVE_ONLY (validating env var first)."""
    return resolve_be_runtime_profile() == PROFILE_OBSERVE_ONLY


def is_market_shadow_runtime() -> bool:
    """Return True if the active runtime profile is MARKET_SHADOW (validating env var first)."""
    return resolve_be_runtime_profile() == PROFILE_MARKET_SHADOW


def is_non_trading_runtime() -> bool:
    """Return True if the active runtime profile forbids trading and operational DB writes (OBSERVE_ONLY or MARKET_SHADOW)."""
    return resolve_be_runtime_profile() in (PROFILE_OBSERVE_ONLY, PROFILE_MARKET_SHADOW)


def can_write_operational_db() -> bool:
    """Return True only when the active runtime profile permits operational trading DB writes."""
    return get_runtime_capabilities().can_write_operational_data


def increment_safety_counter(key: str, delta: int = 1) -> int:
    with _COUNTERS_LOCK:
        _SAFETY_COUNTERS[key] = int(_SAFETY_COUNTERS.get(key, 0)) + int(delta)
        return _SAFETY_COUNTERS[key]


def get_runtime_safety_counters() -> Dict[str, int]:
    with _COUNTERS_LOCK:
        return dict(_SAFETY_COUNTERS)


def reset_runtime_safety_counters() -> None:
    with _COUNTERS_LOCK:
        _SAFETY_COUNTERS.clear()
        _SAFETY_COUNTERS.update(_DEFAULT_COUNTERS)


def get_runtime_safety_snapshot() -> Dict[str, Any]:
    profile = resolve_be_runtime_profile()
    caps = get_runtime_capabilities(profile)
    if profile == PROFILE_OBSERVE_ONLY:
        allowed_workers = list(OBSERVE_ONLY_ALLOWED_WORKERS)
        blocked_workers = list(ALL_BACKGROUND_TRADING_WORKERS)
    elif profile == PROFILE_MARKET_SHADOW:
        allowed_workers = list(MARKET_SHADOW_ALLOWED_WORKERS)
        blocked_workers = list(ALL_BACKGROUND_TRADING_WORKERS)
    else:
        allowed_workers = list(ALL_BACKGROUND_TRADING_WORKERS)
        blocked_workers = []

    return {
        "profile": profile,
        "runtime_profile": profile,
        "observe_only": profile == PROFILE_OBSERVE_ONLY,
        "market_shadow": profile == PROFILE_MARKET_SHADOW,
        "trading_enabled": profile == PROFILE_TRADING,
        "capabilities": caps.to_dict(),
        "allowed_workers": allowed_workers,
        "blocked_trading_workers": blocked_workers,
        "counters": get_runtime_safety_counters(),
    }


get_runtime_profile = resolve_be_runtime_profile
get_runtime_profile_status = get_runtime_safety_snapshot


def assert_worker_allowed_in_current_profile(worker_name: str) -> None:
    """
    Fail closed if `worker_name` is not permitted to start in the active runtime profile.
    """
    profile = resolve_be_runtime_profile()
    if profile == PROFILE_OBSERVE_ONLY:
        if worker_name not in OBSERVE_ONLY_ALLOWED_WORKERS:
            increment_safety_counter("BLOCKED_WORKER_START_ATTEMPTS", 1)
            increment_safety_counter("UNAUTHORIZED_TRADING_WORKERS_STARTED", 0)
            raise RuntimeError(
                f"OBSERVE_ONLY_WORKER_FORBIDDEN: Worker {worker_name!r} is forbidden in {PROFILE_OBSERVE_ONLY}."
            )
    elif profile == PROFILE_MARKET_SHADOW:
        allowed_set = set(MARKET_SHADOW_ALLOWED_WORKERS) | {
            "bitget_ws_shadow_consumer",
            "market_shadow_evaluator",
        }
        if worker_name not in allowed_set:
            increment_safety_counter("BLOCKED_WORKER_START_ATTEMPTS", 1)
            raise RuntimeError(
                f"MARKET_SHADOW_EXECUTION_FORBIDDEN: Worker {worker_name!r} is forbidden in {PROFILE_MARKET_SHADOW}."
            )



def assert_runtime_capability(
    capability: str,
    operation: str,
    counter_key: Optional[str] = None,
) -> None:
    """
    Fail closed if the active runtime profile does not grant `capability`.
    Raises OBSERVE_ONLY_BLOCKED in OBSERVE_ONLY and MARKET_SHADOW_EXECUTION_FORBIDDEN in MARKET_SHADOW.
    """
    profile = resolve_be_runtime_profile()
    caps = PROFILE_CAPABILITIES[profile]
    allowed = bool(getattr(caps, capability, False))
    if not allowed:
        if counter_key:
            increment_safety_counter(counter_key, 1)
        if profile == PROFILE_MARKET_SHADOW:
            raise RuntimeError(
                f"MARKET_SHADOW_EXECUTION_FORBIDDEN: {operation} (requires {capability}) "
                f"is forbidden when {ENV_BE_RUNTIME_PROFILE}={PROFILE_MARKET_SHADOW}"
            )
        if profile == PROFILE_OBSERVE_ONLY:
            raise RuntimeError(
                f"OBSERVE_ONLY_BLOCKED: {operation} (requires {capability}) "
                f"is forbidden when {ENV_BE_RUNTIME_PROFILE}={PROFILE_OBSERVE_ONLY}"
            )
        raise RuntimeError(
            f"RUNTIME_CAPABILITY_FORBIDDEN: {operation} (requires {capability}) "
            f"is forbidden when {ENV_BE_RUNTIME_PROFILE}={profile}"
        )


def assert_not_observe_only(operation: str, counter_key: Optional[str] = None) -> None:
    """
    Fail closed if invoked while AURUM_BE_RUNTIME_PROFILE is OBSERVE_ONLY or MARKET_SHADOW.
    Preserves OBSERVE_ONLY_BLOCKED error code for OBSERVE_ONLY and raises
    MARKET_SHADOW_EXECUTION_FORBIDDEN for MARKET_SHADOW.
    """
    profile = resolve_be_runtime_profile()
    if profile == PROFILE_OBSERVE_ONLY:
        if counter_key:
            increment_safety_counter(counter_key, 1)
        raise RuntimeError(
            f"OBSERVE_ONLY_BLOCKED: {operation} is forbidden when {ENV_BE_RUNTIME_PROFILE}={PROFILE_OBSERVE_ONLY}"
        )
    if profile == PROFILE_MARKET_SHADOW:
        if counter_key:
            increment_safety_counter(counter_key, 1)
        raise RuntimeError(
            f"MARKET_SHADOW_EXECUTION_FORBIDDEN: {operation} is forbidden when {ENV_BE_RUNTIME_PROFILE}={PROFILE_MARKET_SHADOW}"
        )


assert_trading_mutation_allowed = assert_not_observe_only

