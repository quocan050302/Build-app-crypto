"""
backend/services/cg01_daily_bias_service.py
V16.30 — Evidence-Based Daily Bias Engine & Direction Policy Dispatcher for CG-01.

Implements Rules 001 - 130:
- Pure, causal Daily Bias evaluation based on user checklist:
  1. Liquidity swept / unswept (PDH, PDL, HTF key swings).
  2. Range location: Discount vs Premium in dealing range.
  3. Reactions at HTF zones (FVG/OB).
- H4 structure as primary anchor for direction; H1 as session context (pullback/continuation).
- Optional D1 for macro context and conflict warning without blocking legacy execution.
- Direction Policy Dispatcher supporting LEGACY_H1, DAILY_BIAS_SHADOW, and DAILY_BIAS_PRIMARY.
- Full snapshot immutability, authority token, and JSON serialization.
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
import zoneinfo

from services.cg01_types import (
    CG01Config,
    CG01DirectionPolicy,
)
from services.cg01_daily_bias_types import (
    CG01DailyBiasDirection,
    CG01DailyBiasStatus,
    CG01DailyBiasSnapshot,
    CG01DailyLiquidityLevel,
    CG01DailyDealingRange,
    CG01HTFZoneReaction,
    CG01BiasAuthorityResult,
)
from services.cg01_config_factory import compute_cg01_config_fingerprint
from services.smc_regime_service import build_htf_context, HTFContext
from services.htf_structure_service import (
    filter_closed_candles,
    resolve_cg01_direction_context,
    resolve_timeframe_ms,
)

logger = logging.getLogger("cg01_daily_bias_service")

# Thread-safe in-memory registry for authoritative current daily bias (Rules 041, 043, 044)
# Bounded storage: exactly one active entry per symbol.
_bias_authority_registry: Dict[str, Dict[str, Any]] = {}
_bias_registry_lock = threading.Lock()

# Thread-safe runtime ownership epoch tracking (Rules 031 - 036, 048, 074, 075)
_runtime_ownership_epoch: int = 1
_runtime_ownership_lock = threading.Lock()


def get_current_runtime_ownership_epoch() -> int:
    """Return the current active runtime ownership epoch."""
    with _runtime_ownership_lock:
        return _runtime_ownership_epoch


def bump_runtime_ownership_epoch() -> int:
    """Advance the runtime ownership epoch upon owner handoff or configuration change."""
    global _runtime_ownership_epoch
    with _runtime_ownership_lock:
        _runtime_ownership_epoch += 1
        return _runtime_ownership_epoch


def reset_runtime_ownership_epoch(new_epoch: int = 1) -> None:
    """Reset the runtime ownership epoch (for test isolation and runtime restart)."""
    global _runtime_ownership_epoch
    with _runtime_ownership_lock:
        _runtime_ownership_epoch = new_epoch


def publish_current_cg01_bias_authority(
    symbol: str,
    bias: Optional[CG01DailyBiasSnapshot],
    config: Optional[CG01Config] = None,
    config_fingerprint: str = "",
    evaluated_at_ms: Optional[int] = None,
    source: str = "ACTIVE_PIPELINE",
    is_active_owner: bool = True,
    owner: str = "WATCHER",
    generation: Optional[int] = None,
    input_version: str = "",
) -> bool:
    """
    Dedicated backend boundary for publishing current daily bias authority for execution (Rules 035 - 048).
    - Checks runtime execution ownership (rejects SHADOW / non-active owners, Rules 032, 037, 038).
    - Anti-rewind check: refuses out-of-order writes (T1 < T2) or stale/future generations (Rules 027, 035, 045, 048).
    - Validates lease freshness against runtime ownership epoch even if registry is empty (Rule 075).
    - Fail-closed on owner resolution exception (Rule 020, 041).
    - Atomic commit boundary validation inside registry lock (Rules 036 - 038).
    - Validates consistency between snapshot symbol, config fingerprint, and trading_day_key (Rules 047-053).
    - Deepcopies snapshot to enforce immutability (Rule 043).
    - Stores strictly in bounded single-entry per symbol registry (Rule 041).
    """
    # Rule 032, 037, 038: Check active ownership and reject shadow/historical
    if not is_active_owner or any(k in source.upper() or k in owner.upper() for k in ("SHADOW", "HISTORICAL", "REPLAY")):
        logger.debug(f"[publish_current_cg01_bias_authority] Refused publish for {symbol}: non-active owner ({owner}/{source})")
        return False

    current_epoch = get_current_runtime_ownership_epoch()
    effective_generation = generation if generation is not None else current_epoch

    # Rule 034, 035, 073, 074, 075: Exact epoch match (reject both stale and future generation leases)
    if effective_generation != current_epoch:
        logger.info(f"[publish_current_cg01_bias_authority] Refused publish for {symbol}: lease generation {effective_generation} != current runtime epoch {current_epoch}")
        return False

    # Rule 020, 034, 041, 072: Check publisher owner against current runtime execution owner with fail-closed semantics
    try:
        from services.cg01_scheduler_service import cg01_evaluation_scheduler, resolve_entry_execution_owner
        owner_info = resolve_entry_execution_owner(cg01_evaluation_scheduler)
        active_exec_owner = owner_info.get("execution_owner", "WATCHER")
        if owner != active_exec_owner:
            logger.info(f"[publish_current_cg01_bias_authority] Refused publish for {symbol}: publisher owner {owner} != runtime active owner {active_exec_owner}")
            return False
    except Exception as e:
        logger.error(f"[publish_current_cg01_bias_authority] Exception verifying runtime owner for {symbol}: {e} - failing closed")
        return False

    # Resolve config fingerprint
    computed_fp = compute_cg01_config_fingerprint(config) if config is not None else ""
    if config is not None and config_fingerprint:
        if computed_fp != config_fingerprint:
            logger.warning(f"[publish_current_cg01_bias_authority] Refused publish for {symbol}: explicit fingerprint ({config_fingerprint}) != computed ({computed_fp})")
            return False

    fp = config_fingerprint or computed_fp
    if not fp and bias is not None:
        fp = bias.get("config_fingerprint", "") if isinstance(bias, dict) else getattr(bias, "config_fingerprint", "")

    # Rule 047 - 051: Check snapshot metadata consistency
    if bias is not None:
        snap_sym = bias.get("symbol", "") if isinstance(bias, dict) else getattr(bias, "symbol", "")
        if snap_sym and snap_sym != symbol:
            logger.warning(f"[publish_current_cg01_bias_authority] Refused publish for {symbol}: snapshot symbol ({snap_sym}) != requested ({symbol})")
            return False

        snap_fp = bias.get("config_fingerprint", "") if isinstance(bias, dict) else getattr(bias, "config_fingerprint", "")
        if snap_fp and fp and snap_fp != fp:
            logger.warning(f"[publish_current_cg01_bias_authority] Refused publish for {symbol}: snapshot fingerprint ({snap_fp}) != config fingerprint ({fp})")
            return False

        # Rule 052, 078: Check trading_day_key for READY snapshots
        snap_status = bias.get("status", "") if isinstance(bias, dict) else getattr(bias, "status", "")
        if snap_status == "READY":
            snap_day = bias.get("trading_day_key", "") if isinstance(bias, dict) else getattr(bias, "trading_day_key", "")
            if not snap_day or not isinstance(snap_day, str) or not snap_day.strip():
                logger.warning(f"[publish_current_cg01_bias_authority] Refused publish for {symbol}: READY snapshot missing trading_day_key")
                return False

    now_ts = evaluated_at_ms if evaluated_at_ms is not None else int(time.time() * 1000)

    with _bias_registry_lock:
        # Rules 036, 037, 038: Atomic commit boundary verification inside lock
        latest_epoch = get_current_runtime_ownership_epoch()
        if latest_epoch != effective_generation:
            logger.info(f"[publish_current_cg01_bias_authority] Refused publish for {symbol}: epoch advanced to {latest_epoch} during commit (lease was {effective_generation})")
            return False

        curr = _bias_authority_registry.get(symbol)
        if curr is not None:
            curr_gen = curr.get("generation", 1)
            curr_eval_ts = curr.get("evaluated_at_ms", 0) or 0

            # Rule 048: Stale generation cannot overwrite newer generation
            if effective_generation < curr_gen:
                logger.info(f"[publish_current_cg01_bias_authority] Refused publish for {symbol}: generation {effective_generation} < {curr_gen}")
                return False

            # Rule 045: Out-of-order write rejection (T1 completing after T2)
            if effective_generation == curr_gen and now_ts < curr_eval_ts:
                logger.info(f"[publish_current_cg01_bias_authority] Refused out-of-order publish for {symbol}: eval_ts {now_ts} < current {curr_eval_ts}")
                return False

        # Rule 043: Deep copy snapshot to enforce immutability
        stored_bias = copy.deepcopy(bias) if bias is not None else None

        # Rule 044: Publish metadata
        entry = {
            "symbol": symbol,
            "bias": stored_bias,
            "config_fingerprint": fp,
            "evaluated_at_ms": now_ts,
            "source": source,
            "owner": owner,
            "generation": effective_generation,
            "input_version": input_version,
            "is_active_owner": True,
        }
        _bias_authority_registry[symbol] = entry
        return True



def record_current_cg01_bias_authority(
    symbol: str,
    bias: CG01DailyBiasSnapshot,
    config_fingerprint: str = "",
    evaluated_at_ms: Optional[int] = None,
    source: str = "PIPELINE",
    generation: Optional[int] = None,
    owner: str = "WATCHER",
    is_active_owner: bool = True,
    config: Optional[CG01Config] = None,
) -> bool:
    """
    Backward-compatible adapter that delegates to publish_current_cg01_bias_authority.
    """
    return publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=bias,
        config=config,
        config_fingerprint=config_fingerprint,
        evaluated_at_ms=evaluated_at_ms,
        source=source,
        is_active_owner=is_active_owner,
        owner=owner,
        generation=generation,
    )


def clear_current_cg01_bias_authority(symbol: Optional[str] = None, reset_epoch: bool = False) -> None:
    """
    For testing isolation and restart simulation (Rule 044, 047, 049).
    Rule 047: Registry clear does NOT reset the ownership epoch to 1 unless explicitly requested for test resets.
    """
    with _bias_registry_lock:
        if symbol:
            _bias_authority_registry.pop(symbol, None)
            keys_to_del = [k for k in _bias_authority_registry if k.startswith(f"{symbol}:")]
            for k in keys_to_del:
                del _bias_authority_registry[k]
        else:
            _bias_authority_registry.clear()
            if reset_epoch:
                reset_runtime_ownership_epoch(1)


def resolve_current_cg01_bias_authority(
    symbol: str = "XAUUSDT",
    now_ms: Optional[int] = None,
    config: Optional[CG01Config] = None,
    config_fingerprint: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    clock: Optional[Any] = None,
) -> CG01BiasAuthorityResult:
    """
    Single source of truth for current Daily Bias authority across ARM and FILL (Rules 051 - 075).
    - Pure read-only (does NOT mutate registry, Rule 051).
    - Requires effective config / fingerprint; fails closed if missing (Rule 053).
    - Rejects config mismatch without falling back to latest (Rule 055).
    - Validates freshness, time consistency, trading day rollover, and mandatory metadata.
    """
    effective_now = now_ms if now_ms is not None else (clock.now_ms() if clock else int(time.time() * 1000))

    # 1. Explicit context evaluation (pure, no registry mutation, Rules 051, 052)
    if context and ("4H" in context or "closed_candles_by_tf" in context):
        eval_ctx = context.get("closed_candles_by_tf") or context
        cfg = config or CG01Config()
        fp = config_fingerprint or compute_cg01_config_fingerprint(cfg)
        bias_snap = evaluate_cg01_daily_bias(
            eval_ctx,
            as_of_ms=effective_now,
            config=cfg,
            symbol=symbol,
        )
        is_avail = (bias_snap.status == CG01DailyBiasStatus.READY.value and bool(bias_snap.allowed_directions))
        return CG01BiasAuthorityResult(
            available=is_avail,
            status=bias_snap.status,
            bias=copy.deepcopy(bias_snap),
            reason=None if is_avail else f"Daily bias status: {bias_snap.status}",
            source="EVALUATED_CONTEXT",
            evaluated_at_ms=effective_now,
            symbol=symbol,
            config_fingerprint=fp,
        )

    # 2. Rule 043: If both config and explicit config_fingerprint are provided, they must not conflict!
    req_fp = config_fingerprint
    if config is not None:
        computed_fp = compute_cg01_config_fingerprint(config)
        if req_fp and req_fp != computed_fp:
            return CG01BiasAuthorityResult(
                available=False,
                status="CONFIG_MISMATCH",
                bias=None,
                reason=f"Argument conflict: explicit config_fingerprint ({req_fp}) does not match computed fingerprint of provided config ({computed_fp})",
                source="ARGUMENT_CONFLICT",
                evaluated_at_ms=effective_now,
                symbol=symbol,
                config_fingerprint=req_fp,
            )
        if not req_fp:
            req_fp = computed_fp

    if not req_fp:
        return CG01BiasAuthorityResult(
            available=False,
            status="CONFIG_UNAVAILABLE",
            bias=None,
            reason="Effective CG01 configuration not provided to authority resolver",
            source="UNAVAILABLE",
            evaluated_at_ms=None,
            symbol=symbol,
            config_fingerprint="",
        )

    # 3. Read-only registry lookup in bounded symbol table (Rule 041)
    with _bias_registry_lock:
        entry = _bias_authority_registry.get(symbol)

    if not entry or entry.get("bias") is None:
        return CG01BiasAuthorityResult(
            available=False,
            status="UNAVAILABLE",
            bias=None,
            reason="Current daily bias authority not ready or not bootstrapped",
            source="UNAVAILABLE",
            evaluated_at_ms=None,
            symbol=symbol,
            config_fingerprint=req_fp,
        )

    bias_snap: CG01DailyBiasSnapshot = entry["bias"]
    snap_fp = bias_snap.get("config_fingerprint", "") if isinstance(bias_snap, dict) else getattr(bias_snap, "config_fingerprint", "")
    auth_fp = entry.get("config_fingerprint") or snap_fp or ""
    eval_ts = entry.get("evaluated_at_ms") or 0
    entry_gen = entry.get("generation", 1)

    # Rule 034, 036, 058: Owner epoch validation - reject stale owner epoch
    current_epoch = get_current_runtime_ownership_epoch()
    if entry_gen < current_epoch:
        return CG01BiasAuthorityResult(
            available=False,
            status="STALE_OWNER_EPOCH",
            bias=copy.deepcopy(bias_snap),
            reason=f"Current daily bias authority generation ({entry_gen}) is older than active runtime epoch ({current_epoch})",
            source=entry["source"],
            evaluated_at_ms=eval_ts,
            symbol=symbol,
            config_fingerprint=req_fp,
        )

    # Rule 055, 056, 089, 090: Exact entry config match (NO fallback to latest!)
    if auth_fp != req_fp:
        return CG01BiasAuthorityResult(
            available=False,
            status="CONFIG_MISMATCH",
            bias=copy.deepcopy(bias_snap),
            reason=f"Current daily bias authority config fingerprint ({auth_fp}) does not match effective config ({req_fp})",
            source=entry["source"],
            evaluated_at_ms=eval_ts,
            symbol=symbol,
            config_fingerprint=req_fp,
        )

    # Rule 048-050: Snapshot config fingerprint must exist and match effective config
    if not snap_fp or not str(snap_fp).strip():
        return CG01BiasAuthorityResult(
            available=False,
            status="MISSING_FINGERPRINT",
            bias=copy.deepcopy(bias_snap),
            reason="Current daily bias authority snapshot is missing config_fingerprint",
            source=entry["source"],
            evaluated_at_ms=eval_ts,
            symbol=symbol,
            config_fingerprint=req_fp,
        )

    if snap_fp != req_fp:
        return CG01BiasAuthorityResult(
            available=False,
            status="CONFIG_FINGERPRINT_MISMATCH",
            bias=copy.deepcopy(bias_snap),
            reason=f"Snapshot config_fingerprint ({snap_fp}) does not match effective config ({req_fp})",
            source=entry["source"],
            evaluated_at_ms=eval_ts,
            symbol=symbol,
            config_fingerprint=req_fp,
        )


    b_status = bias_snap.get("status") if isinstance(bias_snap, dict) else getattr(bias_snap, "status", None)
    if b_status == "INVALIDATED":
        return CG01BiasAuthorityResult(
            available=False,
            status="INVALIDATED",
            bias=copy.deepcopy(bias_snap),
            reason="Current daily bias authority snapshot is INVALIDATED",
            source=entry["source"],
            evaluated_at_ms=eval_ts,
            symbol=symbol,
            config_fingerprint=req_fp,
        )

    # Rule 062: Time consistency - snapshot cannot be from the future
    if eval_ts > effective_now:
        return CG01BiasAuthorityResult(
            available=False,
            status="TIME_INCONSISTENT",
            bias=copy.deepcopy(bias_snap),
            reason="Snapshot evaluated_at is in future relative to decision time",
            source=entry["source"],
            evaluated_at_ms=eval_ts,
            symbol=symbol,
            config_fingerprint=req_fp,
        )

    # Rule 052, 058, 061, 078: Trading day consistency / rollover (mandatory matching day)
    tz_str = getattr(config, "ny_timezone", "America/New_York") if config else "America/New_York"
    expected_day = resolve_trading_day_key(effective_now, tz_name=tz_str)
    snap_day = bias_snap.get("trading_day_key", "") if isinstance(bias_snap, dict) else getattr(bias_snap, "trading_day_key", "")
    if not snap_day or not isinstance(snap_day, str) or not snap_day.strip():
        return CG01BiasAuthorityResult(
            available=False,
            status="MISSING_TRADING_DAY",
            bias=copy.deepcopy(bias_snap),
            reason="Current daily bias authority snapshot is missing trading_day_key",
            source=entry["source"],
            evaluated_at_ms=eval_ts,
            symbol=symbol,
            config_fingerprint=req_fp,
        )

    if snap_day != expected_day:
        return CG01BiasAuthorityResult(
            available=False,
            status="DAY_MISMATCH",
            bias=copy.deepcopy(bias_snap),
            reason=f"Current daily bias authority trading day ({snap_day}) does not match current day ({expected_day})",
            source=entry["source"],
            evaluated_at_ms=eval_ts,
            symbol=symbol,
            config_fingerprint=req_fp,
        )

    # Rule 051, 093: Symbol matching
    snap_sym = bias_snap.get("symbol", "") if isinstance(bias_snap, dict) else getattr(bias_snap, "symbol", "")
    if not snap_sym or snap_sym != symbol:
        return CG01BiasAuthorityResult(
            available=False,
            status="SYMBOL_MISMATCH",
            bias=copy.deepcopy(bias_snap),
            reason=f"Authority symbol ({snap_sym}) does not match requested symbol ({symbol})",
            source=entry["source"],
            evaluated_at_ms=eval_ts,
            symbol=symbol,
            config_fingerprint=req_fp,
        )

    # Rule 067-074: Required fields on current snapshot
    b_token = bias_snap.get("authority_token") if isinstance(bias_snap, dict) else getattr(bias_snap, "authority_token", None)
    b_dirs = bias_snap.get("allowed_directions") if isinstance(bias_snap, dict) else getattr(bias_snap, "allowed_directions", None)

    if not b_token or not isinstance(b_token, str) or not b_token.strip():
        return CG01BiasAuthorityResult(
            available=False,
            status="MISSING_TOKEN",
            bias=copy.deepcopy(bias_snap),
            reason="Current daily bias authority snapshot is missing authority_token",
            source=entry["source"],
            evaluated_at_ms=eval_ts,
            symbol=symbol,
            config_fingerprint=req_fp,
        )

    if not isinstance(b_dirs, (list, tuple)) or not b_dirs or not all(d in ("LONG", "SHORT") for d in b_dirs):
        if b_status == CG01DailyBiasStatus.READY.value:
            return CG01BiasAuthorityResult(
                available=False,
                status="INVALID_DIRECTIONS",
                bias=copy.deepcopy(bias_snap),
                reason="Current daily bias authority snapshot has invalid or empty allowed_directions",
                source=entry["source"],
                evaluated_at_ms=eval_ts,
                symbol=symbol,
                config_fingerprint=req_fp,
            )

    is_avail = (b_status == CG01DailyBiasStatus.READY.value and bool(b_dirs))
    return CG01BiasAuthorityResult(
        available=is_avail,
        status=b_status or "UNAVAILABLE",
        bias=copy.deepcopy(bias_snap) if bias_snap is not None else None,
        reason=None if is_avail else f"Daily bias status: {b_status}",
        source=entry["source"],
        evaluated_at_ms=eval_ts,
        symbol=symbol,
        config_fingerprint=req_fp,
    )


def build_cg01_authority_publish_context(
    symbol: str = "XAUUSDT",
    config: Optional[CG01Config] = None,
    research_context: Optional[Any] = None,
    db: Optional[Session] = None,
    caller_identity: Optional[str] = None,
    evaluation_origin: Optional[str] = None,
    execution_scope: Optional[str] = None,
    auto_enabled: Optional[bool] = None,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Rules 022, 023 - 036, 041 - 046:
    Arbitrates runtime authority publish eligibility.
    Rejects SHADOW observation mode, non-owner scheduler runs, and historical/replay context.
    Captures runtime ownership epoch lease for publication.
    """
    current_epoch = get_current_runtime_ownership_epoch()
    caller = (evaluation_origin or caller_identity or "WATCHER").upper()
    scope = (execution_scope or "").upper()

    # 1. Check explicit historical / replay / research origins
    if caller in ("HISTORICAL", "REPLAY", "RESEARCH", "BACKTEST") or scope in ("HISTORICAL", "REPLAY"):
        return {
            "is_active_owner": False,
            "owner": "HISTORICAL",
            "source": f"HISTORICAL_{caller or scope}",
            "generation": current_epoch,
            "can_execute": False,
            "scope": "HISTORICAL",
        }

    # 2. Check research_context
    if research_context is not None:
        obs_mode = getattr(research_context, "observation_mode", None)
        if obs_mode and str(obs_mode).upper() in ("SHADOW", "HISTORICAL", "REPLAY"):
            return {
                "is_active_owner": False,
                "owner": str(obs_mode).upper(),
                "source": f"{str(obs_mode).upper()}_RESEARCH",
                "generation": current_epoch,
                "can_execute": False,
                "scope": str(obs_mode).upper(),
            }
        if getattr(research_context, "is_historical", False) or getattr(research_context, "scope", "") in ("HISTORICAL", "REPLAY"):
            return {
                "is_active_owner": False,
                "owner": "HISTORICAL",
                "source": "HISTORICAL_RESEARCH_CONTEXT",
                "generation": current_epoch,
                "can_execute": False,
                "scope": "HISTORICAL",
            }

    # 3. Check config profile mode
    prof_mode = getattr(config, "profile_mode", None)
    if prof_mode and str(prof_mode).upper() in ("SHADOW", "PAPER_SHADOW", "OFF"):
        return {
            "is_active_owner": False,
            "owner": "SHADOW",
            "source": f"CONFIG_{prof_mode}",
            "generation": current_epoch,
            "can_execute": False,
            "scope": "SHADOW",
        }

    # 4. Arbitrate execution owner via scheduler service
    try:
        from services.cg01_scheduler_service import (
            cg01_evaluation_scheduler,
            resolve_entry_execution_owner,
        )
        owner_info = resolve_entry_execution_owner(cg01_evaluation_scheduler, db=db)
        exec_owner = owner_info.get("execution_owner", "WATCHER")
        can_exec = owner_info.get("can_execute", True) if auto_enabled is None else bool(auto_enabled)

        if exec_owner == "SCHEDULER":
            if caller == "SCHEDULER":
                is_active = True
                owner_label = "SCHEDULER"
            else:
                is_active = False
                owner_label = "WATCHDOG"
        else:
            if caller == "SCHEDULER":
                is_active = False
                owner_label = "SCHEDULER_NON_OWNER"
            else:
                is_active = True
                owner_label = "WATCHER"

        return {
            "is_active_owner": is_active,
            "owner": owner_label,
            "source": "ACTIVE_PIPELINE" if is_active else f"INACTIVE_{owner_label}",
            "generation": current_epoch,
            "can_execute": can_exec,
            "scope": "PRODUCTION",
        }
    except Exception as e:
        logger.warning(f"[build_cg01_authority_publish_context] Error resolving owner: {e}")
        return {
            "is_active_owner": False,
            "owner": "UNKNOWN_FAIL_CLOSED",
            "source": "FAIL_CLOSED",
            "generation": current_epoch,
            "can_execute": False,
            "scope": "UNKNOWN",
        }


def validate_cg01_bias_authority_binding(
    candidate_bias: Optional[Any],
    current_authority: Optional[Any],
    order_direction: str,
    symbol: str = "XAUUSDT",
    effective_fingerprint: Optional[str] = None,
    is_persisted_binding: bool = False,
) -> Tuple[bool, str, str, str]:
    """
    Rule 066: Shared validator helper for both ARM guard and FILL lifecycle.
    Validates candidate daily bias against current authority snapshot.
    Returns: (valid: bool, status_code: str, message_vi: str, action: str)
    action: "PASS" | "BLOCK" | "DEFER" | "INVALIDATE"
    """
    if candidate_bias is None:
        return False, "DAILY_BIAS_MISSING", "Candidate PRIMARY thiếu snapshot bias ngày.", "INVALIDATE"

    # 1. Candidate metadata checks
    cand_status = candidate_bias.get("status") if isinstance(candidate_bias, dict) else getattr(candidate_bias, "status", None)
    if cand_status == "INVALIDATED":
        return False, "DAILY_BIAS_INVALIDATED", "Bias ngày của candidate đã mất hiệu lực.", "INVALIDATE"
    if cand_status == "UNAVAILABLE":
        return False, "DAILY_BIAS_UNAVAILABLE", "Bias ngày của candidate không sẵn sàng.", "DEFER"
    if cand_status == "NEUTRAL":
        return False, "DAILY_BIAS_NEUTRAL", "Bias ngày trung tính (NEUTRAL), không cho phép mở lệnh.", "INVALIDATE"
    if cand_status is None and not is_persisted_binding:
        return False, "DAILY_BIAS_NOT_READY", "Candidate snapshot thiếu status (bắt buộc READY).", "BLOCK"
    if cand_status is not None and cand_status != "READY":
        return False, "DAILY_BIAS_NOT_READY", f"Bias ngày của candidate không ở trạng thái READY ({cand_status}).", "DEFER"

    auth_token = candidate_bias.get("authority_token") if isinstance(candidate_bias, dict) else getattr(candidate_bias, "authority_token", None)
    if not auth_token or not isinstance(auth_token, str) or not auth_token.strip():
        return False, "DAILY_BIAS_MISSING_TOKEN", "Candidate PRIMARY thiếu authority token hợp lệ.", "DEFER"

    cand_dirs = candidate_bias.get("allowed_directions") if isinstance(candidate_bias, dict) else getattr(candidate_bias, "allowed_directions", ())
    if not isinstance(cand_dirs, (list, tuple)) or not cand_dirs or not all(d in ("LONG", "SHORT") for d in cand_dirs):
        return False, "DAILY_BIAS_INVALID_DIRECTIONS", "Candidate PRIMARY có allowed_directions không hợp lệ hoặc rỗng.", "DEFER"

    if order_direction not in cand_dirs:
        return False, "DAILY_BIAS_DIRECTION_MISMATCH", f"Hướng lệnh {order_direction} không khớp hướng được phép của bias candidate ({cand_dirs}).", "INVALIDATE"

    cand_fp = candidate_bias.get("config_fingerprint") if isinstance(candidate_bias, dict) else getattr(candidate_bias, "config_fingerprint", None)
    if not cand_fp or not isinstance(cand_fp, str) or not cand_fp.strip():
        return False, "DAILY_BIAS_MISSING_FINGERPRINT", "Candidate PRIMARY thiếu config fingerprint.", "DEFER"

    # 2. Current authority checks
    if current_authority is None:
        return False, "DAILY_BIAS_CURRENT_UNAVAILABLE", "Thiếu dữ liệu current daily bias authority.", "DEFER"

    cur_reason = None
    if isinstance(current_authority, CG01BiasAuthorityResult):
        cur_avail = current_authority.available
        cur_status = current_authority.status
        cur_bias = current_authority.bias
        cur_fp = current_authority.config_fingerprint
        cur_reason = current_authority.reason
    elif isinstance(current_authority, dict):
        cur_avail = current_authority.get("available")
        cur_status = current_authority.get("status")
        cur_bias = current_authority.get("bias") or current_authority
        cur_fp = current_authority.get("config_fingerprint")
        cur_reason = current_authority.get("reason")
    else:
        cur_avail = getattr(current_authority, "available", True)
        cur_status = getattr(current_authority, "status", None)
        cur_bias = current_authority
        cur_fp = getattr(current_authority, "config_fingerprint", None)
        cur_reason = getattr(current_authority, "reason", None)

    # Rule 057: available=False cannot pass even if bias is populated!
    if cur_avail is False:
        action = "INVALIDATE" if cur_status == "INVALIDATED" else "DEFER"
        code = "CONFIG_FINGERPRINT_MISMATCH" if cur_status in ("CONFIG_MISMATCH", "CONFIG_FINGERPRINT_MISMATCH") else (cur_status or "DAILY_BIAS_CURRENT_UNAVAILABLE")
        msg = cur_reason or f"Bias ngày hiện tại không khả dụng ({cur_status})."
        return False, code, msg, action

    # Current authority status code evaluation
    if cur_status == "INVALIDATED":
        return False, "DAILY_BIAS_CURRENT_INVALIDATED", "Bias ngày hiện tại đã mất hiệu lực.", "INVALIDATE"
    if cur_status == "CONFIG_MISMATCH":
        return False, "CONFIG_FINGERPRINT_MISMATCH", "Cấu hình giao dịch hiện tại không khớp cấu hình của bias authority.", "DEFER"
    if cur_status in ("UNAVAILABLE", "NOT_BOOTSTRAPPED", "CONFIG_UNAVAILABLE"):
        return False, "DAILY_BIAS_CURRENT_UNAVAILABLE", "Bias ngày hiện tại chưa sẵn sàng hoặc chưa bootstrap.", "DEFER"
    if cur_status == "TIME_INCONSISTENT":
        return False, "DAILY_BIAS_CURRENT_TIME_INCONSISTENT", "Thời gian đánh giá bias ngày hiện tại ở tương lai.", "DEFER"
    if cur_status == "DAY_MISMATCH":
        return False, "DAILY_BIAS_CURRENT_DAY_MISMATCH", "Bias ngày hiện tại thuộc ngày giao dịch khác ngày hiện tại.", "DEFER"
    if cur_status in ("MISSING_TOKEN", "DAILY_BIAS_CURRENT_MISSING_TOKEN"):
        return False, "DAILY_BIAS_CURRENT_MISSING_TOKEN", "Bias ngày hiện tại thiếu authority token.", "DEFER"
    if cur_status in ("MISSING_FINGERPRINT", "DAILY_BIAS_CURRENT_MISSING_FINGERPRINT"):
        return False, "DAILY_BIAS_CURRENT_MISSING_FINGERPRINT", "Bias ngày hiện tại thiếu config fingerprint.", "DEFER"
    if cur_status in ("INVALID_DIRECTIONS", "DAILY_BIAS_CURRENT_INVALID_DIRECTIONS"):
        return False, "DAILY_BIAS_CURRENT_INVALID_DIRECTIONS", "Bias ngày hiện tại có allowed_directions không hợp lệ.", "DEFER"
    if cur_status != "READY":
        return False, "DAILY_BIAS_CURRENT_NOT_READY", f"Bias ngày hiện tại không ở trạng thái READY ({cur_status}).", "DEFER"

    if cur_bias is None:
        return False, "DAILY_BIAS_CURRENT_UNAVAILABLE", "Thiếu snapshot bias ngày hiện tại.", "DEFER"

    # 3. Fingerprint validation (Rules 029, 054, 069)
    if not cur_fp and isinstance(cur_bias, dict):
        cur_fp = cur_bias.get("config_fingerprint")
    elif not cur_fp and cur_bias is not None:
        cur_fp = getattr(cur_bias, "config_fingerprint", None)

    if not cur_fp or not isinstance(cur_fp, str) or not cur_fp.strip():
        return False, "DAILY_BIAS_CURRENT_MISSING_FINGERPRINT", "Bias ngày hiện tại thiếu config fingerprint.", "DEFER"

    if cand_fp != cur_fp:
        return False, "CONFIG_FINGERPRINT_MISMATCH", f"Config fingerprint của candidate ({cand_fp}) không khớp bias ngày hiện tại ({cur_fp}).", "DEFER"

    if effective_fingerprint and (effective_fingerprint != cur_fp or effective_fingerprint != cand_fp):
        return False, "CONFIG_FINGERPRINT_MISMATCH", f"Config fingerprint hiệu dụng ({effective_fingerprint}) không khớp candidate hoặc bias ngày hiện tại ({cur_fp}).", "DEFER"

    # 4. Token validation (Rules 053, 068, 074)
    cur_token = cur_bias.get("authority_token") if isinstance(cur_bias, dict) else getattr(cur_bias, "authority_token", None)
    if not cur_token or not isinstance(cur_token, str) or not cur_token.strip():
        return False, "DAILY_BIAS_CURRENT_MISSING_TOKEN", "Bias ngày hiện tại thiếu authority token.", "DEFER"

    if auth_token != cur_token:
        return False, "DAILY_BIAS_AUTHORITY_MISMATCH", "Authority token của candidate không khớp với bias ngày hiện tại.", "INVALIDATE"

    # 5. Allowed directions validation (Rules 055, 070, 072)
    cur_dirs = cur_bias.get("allowed_directions") if isinstance(cur_bias, dict) else getattr(cur_bias, "allowed_directions", None)
    if not isinstance(cur_dirs, (list, tuple)) or not cur_dirs or not all(d in ("LONG", "SHORT") for d in cur_dirs):
        return False, "DAILY_BIAS_CURRENT_INVALID_DIRECTIONS", "Bias ngày hiện tại có allowed_directions không hợp lệ hoặc rỗng.", "DEFER"

    if order_direction not in cur_dirs:
        return False, "DAILY_BIAS_DIRECTION_MISMATCH", f"Hướng lệnh {order_direction} không thuộc hướng được phép của bias ngày hiện tại ({cur_dirs}).", "INVALIDATE"

    # 6. Symbol validation (Rules 056, 093)
    cur_sym = cur_bias.get("symbol") if isinstance(cur_bias, dict) else getattr(cur_bias, "symbol", None)
    if not cur_sym or cur_sym != symbol:
        return False, "DAILY_BIAS_SYMBOL_MISMATCH", f"Symbol của bias ngày ({cur_sym}) không khớp lệnh ({symbol}).", "INVALIDATE"

    cand_sym = candidate_bias.get("symbol") if isinstance(candidate_bias, dict) else getattr(candidate_bias, "symbol", None)
    if cand_sym and cand_sym != symbol:
        return False, "DAILY_BIAS_SYMBOL_MISMATCH", f"Symbol của candidate ({cand_sym}) không khớp lệnh ({symbol}).", "INVALIDATE"

    return True, "READY", "Daily bias authority binding hợp lệ.", "PASS"


def resolve_trading_day_key(as_of_ms: int, tz_name: str = "America/New_York") -> str:
    """
    Rule 064: Groups trading day according to canonical timezone (default America/New_York).
    Returns YYYY-MM-DD string.
    """
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = datetime.timezone(datetime.timedelta(hours=-4))
    dt = datetime.datetime.fromtimestamp(as_of_ms / 1000.0, tz=datetime.timezone.utc).astimezone(tz)
    return dt.strftime("%Y-%m-%d")


def compute_daily_bias_authority_token(
    symbol: str,
    trading_day_key: str,
    direction: str,
    status: str,
    h4_last_closed_ms: int,
    invalidation_price: Optional[float] = None,
    config_fingerprint: str = "",
) -> str:
    """
    Rule 067: Authority token used to bind candidate to daily bias.
    Separates structural validity from cosmetic text updates.
    """
    payload = {
        "symbol": symbol,
        "trading_day_key": trading_day_key,
        "direction": direction,
        "status": status,
        "h4_last_closed_ms": h4_last_closed_ms,
        "invalidation_price": invalidation_price,
        "config_fingerprint": config_fingerprint,
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_daily_liquidity_facts(
    closed_candles_by_tf: Optional[Dict[str, Sequence[Any]]] = None,
    as_of_ms: int = 0,
    symbol: str = "XAUUSDT",
    closed_context: Optional[Dict[str, Sequence[Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Rules 058, 101 - 107: Checklist Fact 1 - Liquidity swept / unswept.
    Evaluates PDH/PDL from completed previous day and confirmed HTF swings.
    """
    facts: List[Dict[str, Any]] = []
    bundle = closed_candles_by_tf or closed_context or {}

    # 1. PDH / PDL from D1 candles if available
    d1_candles = bundle.get("1D") or bundle.get("D") or []
    if d1_candles:
        # Find the last completed daily candle before as_of_ms
        dur_1d = resolve_timeframe_ms("1D")
        completed_days = [
            c for c in d1_candles
            if ((int(c.get("open_time") or c.get("timestamp") or 0) if isinstance(c, dict)
                 else int(getattr(c, "open_time", None) or getattr(c, "timestamp", 0))) + dur_1d <= as_of_ms)
        ]
        if completed_days:
            prev_day = completed_days[-1]
            pdh = float(prev_day.get("high") if isinstance(prev_day, dict) else getattr(prev_day, "high", 0.0))
            pdl = float(prev_day.get("low") if isinstance(prev_day, dict) else getattr(prev_day, "low", 0.0))
            pd_ts = int(prev_day.get("open_time") or prev_day.get("timestamp") or 0) if isinstance(prev_day, dict) else int(getattr(prev_day, "open_time", 0) or getattr(prev_day, "timestamp", 0))

            # Check if swept on subsequent candles (e.g. H1/15M)
            dur_1h = resolve_timeframe_ms("1H")
            h1_candles = bundle.get("1H") or []
            pdh_swept = False
            pdl_swept = False
            swept_pdh_ts = None
            swept_pdl_ts = None

            for bar in h1_candles:
                b_open = int(bar.get("open_time") or bar.get("timestamp") or 0) if isinstance(bar, dict) else int(getattr(bar, "open_time", 0) or getattr(bar, "timestamp", 0))
                b_close_ms = b_open + dur_1h
                # Rule 079, 121: Causality boundary - do not evaluate candles after as_of_ms
                if as_of_ms and b_close_ms > as_of_ms:
                    continue
                # Rule 082: Check H1 candles after previous day completed
                if b_open >= pd_ts + dur_1d:
                    b_high = float(bar.get("high") if isinstance(bar, dict) else getattr(bar, "high", 0.0))
                    b_low = float(bar.get("low") if isinstance(bar, dict) else getattr(bar, "low", 0.0))
                    b_close = float(bar.get("close") if isinstance(bar, dict) else getattr(bar, "close", 0.0))

                    # Sweep: wick pierced level but candle closed back inside
                    if b_high > pdh and b_close <= pdh:
                        pdh_swept = True
                        swept_pdh_ts = b_close_ms
                    if b_low < pdl and b_close >= pdl:
                        pdl_swept = True
                        swept_pdl_ts = b_close_ms

            facts.append(CG01DailyLiquidityLevel(
                level_id=f"pdh_{symbol}_{pd_ts}",
                level_type="PDH",
                price=pdh,
                timeframe="1D",
                status="SWEPT" if pdh_swept else "UNSWEPT",
                swept_at_ms=swept_pdh_ts,
                known_at_ms=pd_ts + dur_1d,
            ).to_dict())

            facts.append(CG01DailyLiquidityLevel(
                level_id=f"pdl_{symbol}_{pd_ts}",
                level_type="PDL",
                price=pdl,
                timeframe="1D",
                status="SWEPT" if pdl_swept else "UNSWEPT",
                swept_at_ms=swept_pdl_ts,
                known_at_ms=pd_ts + dur_1d,
            ).to_dict())

    # 2. H4 Swing Highs / Lows
    h4_candles = bundle.get("4H") or []
    if len(h4_candles) >= 5:
        from services.liquidity_scenario_service import identify_confirmed_swings
        shs, sls = identify_confirmed_swings(h4_candles, "4H", left_bars=2, right_bars=2, symbol=symbol, as_of_ms=as_of_ms)
        for s in shs[-2:]:
            s_id = s.get("id") if isinstance(s, dict) else getattr(s, "id", getattr(s, "swing_id", "sh"))
            s_price = float(s.get("price") if isinstance(s, dict) else getattr(s, "price", 0.0))
            s_swept = bool(s.get("is_swept") if isinstance(s, dict) else getattr(s, "is_swept", False))
            s_swept_at = s.get("swept_at_ms") if isinstance(s, dict) else getattr(s, "swept_at_ms", None)
            s_known_at = int(s.get("known_at_ms") if isinstance(s, dict) else getattr(s, "known_at_ms", as_of_ms))
            facts.append(CG01DailyLiquidityLevel(
                level_id=f"h4_sh_{s_id}",
                level_type="H4_SWING_HIGH",
                price=s_price,
                timeframe="4H",
                status="SWEPT" if s_swept else "UNSWEPT",
                swept_at_ms=s_swept_at,
                known_at_ms=s_known_at,
            ).to_dict())
        for s in sls[-2:]:
            s_id = s.get("id") if isinstance(s, dict) else getattr(s, "id", getattr(s, "swing_id", "sl"))
            s_price = float(s.get("price") if isinstance(s, dict) else getattr(s, "price", 0.0))
            s_swept = bool(s.get("is_swept") if isinstance(s, dict) else getattr(s, "is_swept", False))
            s_swept_at = s.get("swept_at_ms") if isinstance(s, dict) else getattr(s, "swept_at_ms", None)
            s_known_at = int(s.get("known_at_ms") if isinstance(s, dict) else getattr(s, "known_at_ms", as_of_ms))
            facts.append(CG01DailyLiquidityLevel(
                level_id=f"h4_sl_{s_id}",
                level_type="H4_SWING_LOW",
                price=s_price,
                timeframe="4H",
                status="SWEPT" if s_swept else "UNSWEPT",
                swept_at_ms=s_swept_at,
                known_at_ms=s_known_at,
            ).to_dict())

    return facts


def build_daily_range_facts(
    closed_candles_by_tf: Optional[Dict[str, Sequence[Any]]] = None,
    as_of_ms: int = 0,
    symbol: str = "XAUUSDT",
    closed_context: Optional[Dict[str, Sequence[Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Rules 059, 108 - 112: Checklist Fact 2 - Dealing range position & midpoint.
    Calculates Dealing Range from confirmed H4/H1 swings and evaluates
    whether current price is in DISCOUNT, PREMIUM, or EQUILIBRIUM.
    """
    bundle = closed_candles_by_tf or closed_context or {}
    h4_candles = bundle.get("4H") or []
    h1_candles = bundle.get("1H") or []
    ref_candles = h4_candles if len(h4_candles) >= 5 else h1_candles
    ref_tf = "4H" if len(h4_candles) >= 5 else "1H"

    if not ref_candles or len(ref_candles) < 5:
        return None

    from services.liquidity_scenario_service import identify_confirmed_swings
    shs, sls = identify_confirmed_swings(ref_candles, ref_tf, left_bars=2, right_bars=2, symbol=symbol, as_of_ms=as_of_ms)
    if not shs or not sls:
        return None

    high_anchor = shs[-1]
    low_anchor = sls[-1]
    upper = float(high_anchor.get("price") if isinstance(high_anchor, dict) else getattr(high_anchor, "price", 0.0))
    lower = float(low_anchor.get("price") if isinstance(low_anchor, dict) else getattr(low_anchor, "price", 0.0))
    if upper <= lower:
        return None

    midpoint = round((upper + lower) / 2.0, 2)

    # Current price from last closed candle
    last_c = ref_candles[-1]
    last_price = float(last_c.get("close") if isinstance(last_c, dict) else getattr(last_c, "close", 0.0))

    tol = (upper - lower) * 0.05
    if abs(last_price - midpoint) <= tol:
        zone = "EQUILIBRIUM"
    elif last_price > midpoint:
        zone = "PREMIUM"
    else:
        zone = "DISCOUNT"

    high_id = high_anchor.get("id") if isinstance(high_anchor, dict) else getattr(high_anchor, "id", getattr(high_anchor, "swing_id", "high"))
    low_id = low_anchor.get("id") if isinstance(low_anchor, dict) else getattr(low_anchor, "id", getattr(low_anchor, "swing_id", "low"))

    return CG01DailyDealingRange(
        range_id=f"dr_{symbol}_{ref_tf}_{low_id}_{high_id}",
        upper=upper,
        lower=lower,
        midpoint=midpoint,
        price_zone=zone,
        high_anchor_id=str(high_id),
        low_anchor_id=str(low_id),
        is_valid=True,
    ).to_dict()


def build_htf_zone_reactions(
    closed_candles_by_tf: Optional[Dict[str, Sequence[Any]]] = None,
    as_of_ms: int = 0,
    symbol: str = "XAUUSDT",
    closed_context: Optional[Dict[str, Sequence[Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Rules 060, 113 - 117, 078 - 087: Checklist Fact 3 - Reaction at HTF FVG/OB zones.
    Evaluates whether HTF zones are respected, violated, untested, or in-zone.
    BPR is explicitly reported as BPR_NOT_IMPLEMENTED/UNAVAILABLE (Rule 116).
    """
    reactions: List[Dict[str, Any]] = []
    bundle = closed_candles_by_tf or closed_context or {}
    h4_candles = bundle.get("4H") or []

    dur_4h = resolve_timeframe_ms("4H")
    # Rule 074: Filter H4 candles strictly causal up to as_of_ms
    h4_closed = [
        c for c in h4_candles
        if ((int(c.get("open_time") or c.get("timestamp") or 0) if isinstance(c, dict)
             else int(getattr(c, "open_time", None) or getattr(c, "timestamp", 0))) + dur_4h <= as_of_ms)
    ]

    if len(h4_closed) >= 3:
        # Check for H4 FVG from most recent down
        for i in range(len(h4_closed) - 1, 1, -1):
            c1 = h4_closed[i - 2]
            c2 = h4_closed[i - 1]
            c3 = h4_closed[i]

            h1 = float(c1.get("high") if isinstance(c1, dict) else getattr(c1, "high", 0.0))
            l1 = float(c1.get("low") if isinstance(c1, dict) else getattr(c1, "low", 0.0))
            h3 = float(c3.get("high") if isinstance(c3, dict) else getattr(c3, "high", 0.0))
            l3 = float(c3.get("low") if isinstance(c3, dict) else getattr(c3, "low", 0.0))
            ts3 = int(c3.get("open_time") or c3.get("timestamp") or 0) if isinstance(c3, dict) else int(getattr(c3, "open_time", 0) or getattr(c3, "timestamp", 0))
            formation_known_at = ts3 + dur_4h

            # Bullish FVG: low of candle 3 > high of candle 1 (zone: [bottom=h1, top=l3])
            if l3 > h1:
                subsequent = h4_closed[i + 1:]
                reaction = "UNTESTED"
                reaction_known_at = formation_known_at
                confirmed_candle_ts = ts3

                for bar in subsequent:
                    bar_open_time_ms = int(bar.get("open_time") or bar.get("timestamp") or 0) if isinstance(bar, dict) else int(getattr(bar, "open_time", 0) or getattr(bar, "timestamp", 0))
                    bar_open_price = float(bar.get("open") if isinstance(bar, dict) else getattr(bar, "open", 0.0))
                    bar_high = float(bar.get("high") if isinstance(bar, dict) else getattr(bar, "high", 0.0))
                    bar_low = float(bar.get("low") if isinstance(bar, dict) else getattr(bar, "low", 0.0))
                    bar_close = float(bar.get("close") if isinstance(bar, dict) else getattr(bar, "close", 0.0))
                    bar_close_time_ms = bar_open_time_ms + dur_4h

                    # Rules 080 - 084: Priority to violation, touch geometry, separation of price and time
                    if bar_close < h1:
                        reaction = "VIOLATED"
                        reaction_known_at = bar_close_time_ms
                        confirmed_candle_ts = bar_open_time_ms
                        break
                    elif bar_low <= l3 and bar_high >= h1:
                        if bar_close >= l3:
                            reaction = "RESPECTED"
                            reaction_known_at = bar_close_time_ms
                            confirmed_candle_ts = bar_open_time_ms
                        else:
                            reaction = "IN_ZONE"
                            reaction_known_at = bar_close_time_ms
                            confirmed_candle_ts = bar_open_time_ms

                reactions.append(CG01HTFZoneReaction(
                    zone_id=f"h4_fvg_bull_{ts3}",
                    zone_type="FVG",
                    timeframe="4H",
                    top=l3,
                    bottom=h1,
                    reaction=reaction,
                    confirmed_candle_ts=confirmed_candle_ts,
                    known_at_ms=reaction_known_at,
                ).to_dict())
                break

            # Bearish FVG: high of candle 3 < low of candle 1 (zone: [bottom=h3, top=l1])
            elif h3 < l1:
                subsequent = h4_closed[i + 1:]
                reaction = "UNTESTED"
                reaction_known_at = formation_known_at
                confirmed_candle_ts = ts3

                for bar in subsequent:
                    bar_open_time_ms = int(bar.get("open_time") or bar.get("timestamp") or 0) if isinstance(bar, dict) else int(getattr(bar, "open_time", 0) or getattr(bar, "timestamp", 0))
                    bar_open_price = float(bar.get("open") if isinstance(bar, dict) else getattr(bar, "open", 0.0))
                    bar_high = float(bar.get("high") if isinstance(bar, dict) else getattr(bar, "high", 0.0))
                    bar_low = float(bar.get("low") if isinstance(bar, dict) else getattr(bar, "low", 0.0))
                    bar_close = float(bar.get("close") if isinstance(bar, dict) else getattr(bar, "close", 0.0))
                    bar_close_time_ms = bar_open_time_ms + dur_4h

                    # Rules 080 - 084: Priority to violation, touch geometry, separation of price and time
                    if bar_close > l1:
                        reaction = "VIOLATED"
                        reaction_known_at = bar_close_time_ms
                        confirmed_candle_ts = bar_open_time_ms
                        break
                    elif bar_high >= h3 and bar_low <= l1:
                        if bar_close <= h3:
                            reaction = "RESPECTED"
                            reaction_known_at = bar_close_time_ms
                            confirmed_candle_ts = bar_open_time_ms
                        else:
                            reaction = "IN_ZONE"
                            reaction_known_at = bar_close_time_ms
                            confirmed_candle_ts = bar_open_time_ms

                reactions.append(CG01HTFZoneReaction(
                    zone_id=f"h4_fvg_bear_{ts3}",
                    zone_type="FVG",
                    timeframe="4H",
                    top=l1,
                    bottom=h3,
                    reaction=reaction,
                    confirmed_candle_ts=confirmed_candle_ts,
                    known_at_ms=reaction_known_at,
                ).to_dict())
                break

    return reactions


def evaluate_cg01_daily_bias(
    closed_context: Dict[str, Sequence[Any]],
    as_of_ms: int,
    config: Optional[CG01Config] = None,
    symbol: str = "XAUUSDT",
    h4_ctx: Optional[HTFContext] = None,
    h1_ctx: Optional[HTFContext] = None,
    range_pos: Optional[str] = None,
) -> CG01DailyBiasSnapshot:
    """
    Rules 091 - 100: Pure causal function evaluating CG-01 Evidence-Based Daily Bias.
    - H4 structure is primary directional anchor (Rule 093).
    - H1 structure is session context (continuation vs pullback vs transition) (Rules 097, 098).
    - D1 is macro context and conflict warning (Rule 099). Missing D1 is UNAVAILABLE, not fatal (Rule 079).
    - Zero side-effects, deterministic and reproducible.
    """
    c_4h = closed_context.get("4H") or []
    c_1h = closed_context.get("1H") or []
    c_1d = closed_context.get("1D") or closed_context.get("D") or []

    trading_day_key = resolve_trading_day_key(as_of_ms, config.ny_timezone if config else "America/New_York")
    from services.cg01_config_factory import compute_cg01_config_fingerprint
    cfg_fp = compute_cg01_config_fingerprint(config or CG01Config())

    # 1. Evaluate H4 Structure (Anchor)
    if h4_ctx is None:
        h4_ctx = build_htf_context(c_4h, "4H", as_of_ms=as_of_ms, config=config, symbol=symbol)
    h4_bias = getattr(h4_ctx, "bias", "UNKNOWN")
    h4_phase = getattr(h4_ctx, "phase", "UNDETERMINED")
    h4_status = getattr(h4_ctx, "data_status", "NO_DATA")

    # 2. Evaluate H1 Context (Session Relation)
    if h1_ctx is None:
        h1_ctx = build_htf_context(c_1h, "1H", as_of_ms=as_of_ms, config=config, symbol=symbol)
    h1_bias = getattr(h1_ctx, "bias", "UNKNOWN")
    h1_phase = getattr(h1_ctx, "phase", "UNDETERMINED")

    # 3. Evaluate D1 Context (Optional Macro Context)
    d1_structure_dict: Optional[Dict[str, Any]] = None
    if c_1d and len(c_1d) >= 5:
        d1_ctx: HTFContext = build_htf_context(c_1d, "1D", as_of_ms=as_of_ms, config=config, symbol=symbol)
        d1_bias = getattr(d1_ctx, "bias", "UNKNOWN")
        d1_relation = "SUPPORTS" if d1_bias == h4_bias else ("CONFLICTS" if d1_bias in ("BULLISH", "BEARISH") else "NEUTRAL")
        d1_structure_dict = {
            "bias": d1_bias,
            "status": "READY",
            "relation": d1_relation,
            "bars": len(c_1d),
        }
    else:
        d1_structure_dict = {
            "bias": "UNAVAILABLE",
            "status": "UNAVAILABLE",
            "relation": "UNAVAILABLE",
            "bars": len(c_1d),
        }

    # 4. Determine Direction, Status & Invalidation Rule from H4 Anchor (Rules 093 - 096, 118 - 120)
    direction = CG01DailyBiasDirection.NEUTRAL.value
    status = CG01DailyBiasStatus.UNAVAILABLE.value
    allowed_directions: Tuple[str, ...] = ()
    reason_codes: List[str] = []
    if d1_structure_dict.get("relation") == "CONFLICTS":
        reason_codes.append("D1_HTF_CONFLICT")
    elif d1_structure_dict.get("relation") == "SUPPORTS":
        reason_codes.append("D1_HTF_CONFIRMATION")
    invalidation_rule: Dict[str, Any] = {}
    h4_inval_price: Optional[float] = None

    if h4_status != "READY" or h4_phase in ("INSUFFICIENT_DATA", "UNDETERMINED"):
        direction = CG01DailyBiasDirection.NEUTRAL.value
        status = CG01DailyBiasStatus.UNAVAILABLE.value
        allowed_directions = ()
        reason_codes.append("H4_DATA_INSUFFICIENT")
    elif h4_phase == "TRANSITION":
        direction = CG01DailyBiasDirection.NEUTRAL.value
        status = CG01DailyBiasStatus.INVALIDATED.value
        allowed_directions = ()
        reason_codes.append("H4_STRUCTURE_TRANSITION_INVALIDATED")
    elif h4_bias == "BULLISH":
        direction = CG01DailyBiasDirection.BULLISH.value
        status = CG01DailyBiasStatus.READY.value
        allowed_directions = ("LONG",)
        reason_codes.append("H4_BULLISH_ANCHOR")

        # Invalidation reference: H4 protected low
        prot_low = h4_ctx.protected_low
        prot_low_px = prot_low.get("price") if isinstance(prot_low, dict) else getattr(prot_low, "price", None)
        h4_inval_price = float(prot_low_px) if prot_low_px else None
        invalidation_rule = {
            "type": "H4_PROTECTED_LOW",
            "price": h4_inval_price,
            "reference_price": h4_inval_price,
            "rule": "Nến H4 đóng cửa dưới đáy được bảo vệ (Protected Low)",
            "timeframe": "4H",
        }
    elif h4_bias == "BEARISH":
        direction = CG01DailyBiasDirection.BEARISH.value
        status = CG01DailyBiasStatus.READY.value
        allowed_directions = ("SHORT",)
        reason_codes.append("H4_BEARISH_ANCHOR")

        # Invalidation reference: H4 protected high
        prot_high = h4_ctx.protected_high
        prot_high_px = prot_high.get("price") if isinstance(prot_high, dict) else getattr(prot_high, "price", None)
        h4_inval_price = float(prot_high_px) if prot_high_px else None
        invalidation_rule = {
            "type": "H4_PROTECTED_HIGH",
            "price": h4_inval_price,
            "reference_price": h4_inval_price,
            "rule": "Nến H4 đóng cửa trên đỉnh được bảo vệ (Protected High)",
            "timeframe": "4H",
        }
    elif h4_bias == "RANGE":
        direction = CG01DailyBiasDirection.NEUTRAL.value
        status = CG01DailyBiasStatus.NEUTRAL.value
        allowed_directions = ()
        reason_codes.append("H4_RANGE_NEUTRAL")
    else:
        direction = CG01DailyBiasDirection.NEUTRAL.value
        status = CG01DailyBiasStatus.NEUTRAL.value
        allowed_directions = ()
        reason_codes.append("H4_BIAS_NEUTRAL")

    # 5. H1 Session Context Label (Rules 097, 098)
    h1_session_label = "UNCERTAIN"
    if direction == "BULLISH":
        if h1_bias == "BEARISH":
            h1_session_label = "PULLBACK"
            reason_codes.append("H1_BEARISH_PULLBACK")
        elif h1_bias == "BULLISH":
            h1_session_label = "CONTINUATION"
            reason_codes.append("H1_BULLISH_CONTINUATION")
        elif h1_phase == "TRANSITION":
            h1_session_label = "TRANSITION"
            reason_codes.append("H1_TRANSITION")
    elif direction == "BEARISH":
        if h1_bias == "BULLISH":
            h1_session_label = "PULLBACK"
            reason_codes.append("H1_BULLISH_PULLBACK")
        elif h1_bias == "BEARISH":
            h1_session_label = "CONTINUATION"
            reason_codes.append("H1_BEARISH_CONTINUATION")
        elif h1_phase == "TRANSITION":
            h1_session_label = "TRANSITION"
            reason_codes.append("H1_TRANSITION")

    h1_session_dict = {
        "bias": h1_bias,
        "phase": h1_phase,
        "relation_label": h1_session_label,
    }

    # 6. Checklist Facts (Liquidity, Range, Zone Reactions)
    liq_facts = build_daily_liquidity_facts(closed_context, as_of_ms, symbol)
    range_facts = build_daily_range_facts(closed_context, as_of_ms, symbol)
    zone_reactions = build_htf_zone_reactions(closed_context, as_of_ms, symbol)

    # 7. Authority Token (Rule 067)
    h4_last_ms = h4_ctx.last_closed_ms or 0
    auth_token = compute_daily_bias_authority_token(
        symbol=symbol,
        trading_day_key=trading_day_key,
        direction=direction,
        status=status,
        h4_last_closed_ms=h4_last_ms,
        invalidation_price=h4_inval_price,
        config_fingerprint=cfg_fp,
    )

    # 8. Human-readable Vietnamese summary (Rules 062, 151 - 155)
    summary_status = ""
    summary_detail = ""
    if status == CG01DailyBiasStatus.READY.value:
        dir_vi = "TĂNG (LONG)" if direction == "BULLISH" else "GIẢM (SHORT)"
        h1_vi = "đang hồi" if h1_session_label == "PULLBACK" else ("tiếp diễn đồng thuận" if h1_session_label == "CONTINUATION" else "đang chuyển tiếp")
        summary_status = f"Bias ngày: Ưu tiên {dir_vi}. H1 {h1_vi}."
        summary_detail = f"H4 cấu trúc {direction}; H1 {h1_session_label}. Cho phép tìm kiếm thiết lập {allowed_directions[0]} CG-01."
    elif status == CG01DailyBiasStatus.INVALIDATED.value:
        summary_status = "Bias ngày mất hiệu lực: Cấu trúc H4 đang chuyển tiếp."
        summary_detail = "Cấu trúc H4 bị phá vỡ; hệ thống tạm ngưng cấp quyền hướng mới cho đến khi có cấu trúc xác nhận."
    elif status == CG01DailyBiasStatus.UNAVAILABLE.value:
        summary_status = "Chưa xác định được bias ngày: Thiếu dữ liệu cấu trúc H4."
        summary_detail = "Chưa đủ nến H4 hợp lệ để xác định mốc đỉnh/đáy được bảo vệ."
    else:
        summary_status = "Bias ngày trung lập: H4 đang tích lũy biên độ (Range)."
        summary_detail = "Cấu trúc thị trường chưa có xu hướng rõ ràng."

    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=trading_day_key,
        bias_id=f"bias_{symbol}_{trading_day_key}_{h4_last_ms}",
        direction=direction,
        status=status,
        allowed_directions=allowed_directions,
        authority_token=auth_token,
        revision=1,
        algorithm_version="v16.30",
        config_fingerprint=cfg_fp,
        evaluated_at_ms=as_of_ms,
        facts_known_at_ms=h4_last_ms,
        h4_structure=h4_ctx.to_dict(),
        h1_session_context=h1_session_dict,
        d1_structure=d1_structure_dict,
        liquidity_facts=liq_facts,
        range_facts=range_facts,
        zone_reaction_facts=zone_reactions,
        invalidation_rule=invalidation_rule,
        reason_codes=tuple(reason_codes),
        ui_summary_vi={"status": summary_status, "detail": summary_detail},
    )
    return snap


def resolve_cg01_entry_direction_policy(
    closed_context: Dict[str, Sequence[Any]],
    config: Optional[CG01Config] = None,
    as_of_ms: Optional[int] = None,
    symbol: str = "XAUUSDT",
    h4_ctx: Optional[HTFContext] = None,
    h1_ctx: Optional[HTFContext] = None,
    range_pos: str = "EQUILIBRIUM",
) -> Dict[str, Any]:
    """
    Rules 037 - 041, 121 - 127: Direction Selection Policy Dispatcher.
    - LEGACY_H1: Default, calls resolve_cg01_direction_context (H1 primary).
    - DAILY_BIAS_SHADOW: Evaluates Daily Bias for display/telemetry, but delegates
      execution direction to legacy H1.
    - DAILY_BIAS_PRIMARY: Evaluates Daily Bias as authoritative source.
      allowed_directions strictly enforced; does NOT fallback to H1 if bias is not ready!
    """
    now_ms = as_of_ms or int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    policy = getattr(config, "direction_policy", CG01DirectionPolicy.LEGACY_H1.value) if config else CG01DirectionPolicy.LEGACY_H1.value

    # Build contexts if not supplied
    if h4_ctx is None:
        c_4h = closed_context.get("4H") or []
        h4_ctx = build_htf_context(c_4h, "4H", as_of_ms=now_ms, config=config, symbol=symbol)
    if h1_ctx is None:
        c_1h = closed_context.get("1H") or []
        h1_ctx = build_htf_context(c_1h, "1H", as_of_ms=now_ms, config=config, symbol=symbol)

    # 1. LEGACY_H1: pure baseline behavior
    if policy == CG01DirectionPolicy.LEGACY_H1.value or policy == "LEGACY_H1":
        dir_ctx = resolve_cg01_direction_context(h4_ctx, h1_ctx, range_pos=range_pos, config=config)
        return {
            "directions_to_evaluate": dir_ctx.get("directions_to_evaluate", []),
            "direction_context": dir_ctx,
            "daily_bias": None,
            "direction_policy": CG01DirectionPolicy.LEGACY_H1.value,
            "primary_context_source": dir_ctx.get("primary_context_source", "H1_PRIMARY"),
            "readiness": dir_ctx.get("readiness", "READY"),
            "reason_codes": dir_ctx.get("reason_codes", ()),
        }

    # 2. Compute Daily Bias for SHADOW and PRIMARY
    daily_bias = evaluate_cg01_daily_bias(
        closed_context,
        as_of_ms=now_ms,
        config=config,
        symbol=symbol,
        h4_ctx=h4_ctx,
        h1_ctx=h1_ctx,
        range_pos=range_pos,
    )

    # 3. DAILY_BIAS_SHADOW: Evaluate bias, but execution direction remains legacy H1
    if policy == CG01DirectionPolicy.DAILY_BIAS_SHADOW.value or policy == "DAILY_BIAS_SHADOW":
        dir_ctx = resolve_cg01_direction_context(h4_ctx, h1_ctx, range_pos=range_pos, config=config)
        return {
            "directions_to_evaluate": dir_ctx.get("directions_to_evaluate", []),
            "direction_context": dir_ctx,
            "daily_bias": daily_bias,
            "direction_policy": CG01DirectionPolicy.DAILY_BIAS_SHADOW.value,
            "primary_context_source": "H1_PRIMARY_DAILY_BIAS_SHADOW",
            "readiness": dir_ctx.get("readiness", "READY"),
            "reason_codes": dir_ctx.get("reason_codes", ()),
        }

    # 4. DAILY_BIAS_PRIMARY: Authoritative Daily Bias (Rules 041, 055, 056, 123-127)
    allowed = list(daily_bias.allowed_directions)
    readiness = "READY" if daily_bias.status == CG01DailyBiasStatus.READY.value and allowed else "NOT_READY"
    reason_codes = list(daily_bias.reason_codes)

    if not allowed:
        if daily_bias.status == CG01DailyBiasStatus.INVALIDATED.value:
            reason_codes.append("DAILY_BIAS_INVALIDATED")
        elif daily_bias.status == CG01DailyBiasStatus.UNAVAILABLE.value:
            reason_codes.append("DAILY_BIAS_UNAVAILABLE")
        else:
            reason_codes.append("DAILY_BIAS_NEUTRAL")

    dir_ctx = {
        "directions_to_evaluate": allowed,
        "primary_context_source": "DAILY_BIAS_PRIMARY",
        "relation": daily_bias.h1_session_context.get("relation_label", "UNKNOWN"),
        "readiness": readiness,
        "reason_codes": tuple(reason_codes),
        "policy_version": "v16.30",
        "daily_bias": daily_bias.to_dict(),
    }

    return {
        "directions_to_evaluate": allowed,
        "direction_context": dir_ctx,
        "daily_bias": daily_bias,
        "direction_policy": CG01DirectionPolicy.DAILY_BIAS_PRIMARY.value,
        "primary_context_source": "DAILY_BIAS_PRIMARY",
        "readiness": readiness,
        "reason_codes": tuple(reason_codes),
    }
