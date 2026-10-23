"""
backend/services/entry_session_policy_service.py
V17.4: Coordinated Entry Session Policy & Timing Profile Service.

Implements Parts 023-040, 081-084, 091-094, 100:
- Pure resolver: resolve_entry_session_settings(policy, scalp_config, execution_scope)
- Staging helper: stage_entry_session_settings (pure DB staging, NO commit)
- Atomic coordinator: update_entry_session_settings (commits exactly once in a single transaction)
- Strictly preserves user Auto authority, direction policies, capital, leverage, risk, and open positions.
- Strictly validates PAPER scope: rejects LIVE execution scope attempts.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

import models
from policy_constants import (
    ALL_SESSIONS_SETUP_FIRST,
    ALL_SESSIONS_WITH_NY_RESERVE,
    NY_ONLY,
    VALID_ENTRY_SESSION_POLICIES,
)
from services.cg01_types import CG01TimingProfile
from services.clock import IClock, live_clock

logger = logging.getLogger("entry_session_policy_service")


class SessionPolicyConfigError(ValueError):
    """Raised when an entry session configuration conflict or illegal parameter occurs."""
    def __init__(self, message: str, code: str = "ENTRY_SESSION_CONFIG_CONFLICT"):
        super().__init__(message)
        self.code = code
        self.message = message


class StalePolicyVersionError(ValueError):
    """Raised when optimistic concurrency check fails (expected_version != current_version)."""
    def __init__(
        self,
        message: str,
        code: str = "STALE_POLICY_VERSION",
        current_version: int = 1,
        expected_version: Optional[int] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.current_version = current_version
        self.expected_version = expected_version


def resolve_entry_session_settings(
    policy: Optional[models.TradingPolicy],
    scalp_config: Optional[Any],
    execution_scope: str = "PAPER",
) -> Dict[str, Any]:
    """
    PHẦN 023, 024: Pure resolver returning effective entry session policy, timing,
    NY goal, reservation, fallback, version, and reason codes.
    Does NOT infer OFF as enabled or alter Auto / model enable flags.
    """
    pol_mode = getattr(policy, "entry_session_policy", ALL_SESSIONS_WITH_NY_RESERVE) or ALL_SESSIONS_WITH_NY_RESERVE
    timing_prof = getattr(scalp_config, "timing_profile", "SILVER_BULLET_LONDON_NY") or "SILVER_BULLET_LONDON_NY"
    policy_ver = int(getattr(policy, "version", 1) or 1)

    reason_codes: List[str] = []
    config_conflict = False

    is_paper_scope = str(execution_scope).upper() in (
        "PAPER", "PAPER_EXECUTION", "PAPER_SHADOW", "SHADOW", "SIMULATION"
    )

    if pol_mode == ALL_SESSIONS_SETUP_FIRST:
        if not is_paper_scope:
            reason_codes.append("PAPER_ONLY_SESSION_PROFILE")
            config_conflict = True
        if timing_prof != CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value:
            reason_codes.append("ENTRY_SESSION_CONFIG_CONFLICT")
            config_conflict = True
        effective_ny_goal = 0
        effective_reserve = False
        effective_fallback = False
    elif pol_mode == ALL_SESSIONS_WITH_NY_RESERVE:
        if timing_prof == CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value:
            reason_codes.append("ENTRY_SESSION_CONFIG_CONFLICT")
            config_conflict = True
        effective_ny_goal = int(getattr(policy, "ny_min_fills", 1) or 1)
        effective_reserve = bool(getattr(policy, "reserve_ny_slot", True))
        effective_fallback = bool(getattr(policy, "ny_fallback_enabled", True))
    elif pol_mode == NY_ONLY:
        if timing_prof == CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value:
            reason_codes.append("ENTRY_SESSION_CONFIG_CONFLICT")
            config_conflict = True
        effective_ny_goal = int(getattr(policy, "ny_min_fills", 1) or 1)
        effective_reserve = bool(getattr(policy, "reserve_ny_slot", True))
        effective_fallback = bool(getattr(policy, "ny_fallback_enabled", True))
    else:
        reason_codes.append("UNSUPPORTED_SESSION_POLICY")
        config_conflict = True
        effective_ny_goal = 1
        effective_reserve = False
        effective_fallback = False

    return {
        "entry_session_policy": pol_mode,
        "timing_profile": timing_prof,
        "effective_ny_goal": effective_ny_goal,
        "reserve_ny_slot": effective_reserve,
        "ny_fallback_enabled": effective_fallback,
        "policy_version": policy_ver,
        "execution_scope": execution_scope,
        "config_conflict": config_conflict,
        "reason_codes": reason_codes,
    }


def _upsert_system_config_no_commit(db: Session, key: str, value: str, now_ms: int) -> None:
    """Helper to stage SystemConfig updates in the active transaction without committing."""
    row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if row:
        row.value = value
        row.updated_at = now_ms
    else:
        db.add(models.SystemConfig(key=key, value=value, updated_at=now_ms))


def stage_entry_session_settings(
    db: Session,
    patch: Any,
    current_policy: models.TradingPolicy,
    current_scalp_config: Optional[Any],
    now_ms: int,
    execution_scope: str = "PAPER",
) -> Tuple[models.TradingPolicy, str]:
    """
    PHẦN 081: Stages TradingPolicy and SystemConfig paired updates in the existing session.
    Does NOT commit. Caller owns commit / rollback.
    """
    patch_dict = (
        patch.model_dump(exclude_unset=False)
        if hasattr(patch, "model_dump")
        else (patch.dict() if hasattr(patch, "dict") else dict(patch))
    )
    desired_mode = str(patch_dict.get("entry_session_policy", "")).strip().upper()
    if desired_mode not in VALID_ENTRY_SESSION_POLICIES:
        raise SessionPolicyConfigError(
            f"entry_session_policy không hợp lệ: {desired_mode!r}. Cho phép: {', '.join(VALID_ENTRY_SESSION_POLICIES)}.",
            code="UNSUPPORTED_SESSION_POLICY",
        )

    # Concurrency control: check expected version if supplied (PHẦN 093, 145)
    expected_ver = patch_dict.get("expected_version")
    current_ver = int(current_policy.version or 1)
    if expected_ver is not None:
        if isinstance(expected_ver, bool) or not isinstance(expected_ver, int):
            raise SessionPolicyConfigError("expected_version must be an integer", code="INVALID_VERSION_TYPE")
        if current_ver != expected_ver:
            raise StalePolicyVersionError(
                f"STALE_POLICY_VERSION: Cấu hình đã được thay đổi ở nơi khác (phiên bản hiện tại: {current_ver}, gửi lên: {expected_ver}).",
                code="STALE_POLICY_VERSION",
                current_version=current_ver,
                expected_version=expected_ver,
            )

    # Scope verification (PHẦN 016, 042, 153)
    req_scope = str(patch_dict.get("execution_scope", execution_scope)).strip().upper()
    is_paper = req_scope in ("PAPER", "PAPER_EXECUTION", "PAPER_SHADOW", "SHADOW", "SIMULATION")

    if desired_mode == ALL_SESSIONS_SETUP_FIRST:
        if not is_paper:
            raise SessionPolicyConfigError(
                "PAPER_ONLY_SESSION_PROFILE: Chế độ ALL_SESSIONS_SETUP_FIRST chỉ cho phép áp dụng trên scope PAPER.",
                code="PAPER_ONLY_SESSION_PROFILE",
            )
        desired_timing = patch_dict.get("timing_profile", CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value)
        if desired_timing != CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value:
            raise SessionPolicyConfigError(
                f"ENTRY_SESSION_CONFIG_CONFLICT: Chế độ ALL_SESSIONS_SETUP_FIRST bắt buộc đi cùng timing {CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value}.",
                code="ENTRY_SESSION_CONFIG_CONFLICT",
            )

        # Stage TradingPolicy changes
        current_policy.entry_session_policy = ALL_SESSIONS_SETUP_FIRST
        current_policy.ny_min_fills = 0
        current_policy.reserve_ny_slot = False
        current_policy.ny_fallback_enabled = False

        # Stage paired SystemConfig changes (PHẦN 091)
        _upsert_system_config_no_commit(db, "timing_profile", CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value, now_ms)
        _upsert_system_config_no_commit(db, "daily_ny_goal", "0", now_ms)
        staged_timing = CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value

    elif desired_mode in (ALL_SESSIONS_WITH_NY_RESERVE, NY_ONLY):
        desired_timing = patch_dict.get("timing_profile")
        if not desired_timing or desired_timing == CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value:
            desired_timing = "SILVER_BULLET_LONDON_NY"
        if desired_timing not in ("SILVER_BULLET_LONDON_NY", "CG01_NY_PAPER_EXPANDED"):
            raise SessionPolicyConfigError(
                f"timing_profile không hợp lệ cho chế độ legacy: {desired_timing}.",
                code="INVALID_TIMING_PROFILE",
            )

        current_policy.entry_session_policy = desired_mode
        ny_min = patch_dict.get("ny_min_fills")
        current_policy.ny_min_fills = 1 if (ny_min is None or ny_min <= 0) else int(ny_min)
        current_policy.reserve_ny_slot = bool(patch_dict.get("reserve_ny_slot", True))
        current_policy.ny_fallback_enabled = bool(patch_dict.get("ny_fallback_enabled", True))

        _upsert_system_config_no_commit(db, "timing_profile", desired_timing, now_ms)
        _upsert_system_config_no_commit(db, "daily_ny_goal", str(current_policy.ny_min_fills), now_ms)
        staged_timing = desired_timing

    # Increment version atomically once
    current_policy.version = current_ver + 1
    current_policy.updated_at = now_ms

    # Audit event in same transaction (PHẦN 099)
    audit_event = models.DomainEvent(
        event_id=f"entry_session_upd_{int(now_ms)}_{current_policy.version}",
        sequence=int(now_ms),
        event_type="entry_session_policy.updated",
        aggregate_id=current_policy.id,
        occurred_at=int(now_ms),
        published_at=int(now_ms),
        payload=json.dumps({
            "entry_session_policy": current_policy.entry_session_policy,
            "timing_profile": staged_timing,
            "ny_min_fills": current_policy.ny_min_fills,
            "reserve_ny_slot": current_policy.reserve_ny_slot,
            "ny_fallback_enabled": current_policy.ny_fallback_enabled,
            "before_version": current_ver,
            "after_version": current_policy.version,
            "execution_scope": req_scope,
        }),
    )
    db.add(audit_event)

    return current_policy, staged_timing


def update_entry_session_settings(
    db: Session,
    patch: Any,
    now_ms: Optional[int] = None,
    clock: Optional[IClock] = None,
) -> Any:
    """
    PHẦN 082: Coordinated atomic update service.
    Loads current policy & config, stages paired fields, increments version,
    and commits exactly once in a single transaction.
    """
    c = clock or live_clock
    t_now = now_ms if now_ms is not None else c.now_ms()

    from services.trading_policy_service import TradingPolicyService
    from services.scalp_m1_types import load_effective_scalp_config
    from services.strategy_service import strategy_service
    from schemas import EntrySessionSettingsResponse

    patch_dict = (
        patch.model_dump(exclude_unset=False)
        if hasattr(patch, "model_dump")
        else (patch.dict() if hasattr(patch, "dict") else dict(patch))
    )

    policy = TradingPolicyService.get_or_create_policy(db)
    scalp_cfg = load_effective_scalp_config(db)

    # Resolve execution scope
    db_scope_row = db.query(models.SystemConfig).filter(models.SystemConfig.key == "execution_scope").first()
    eff_scope = db_scope_row.value.strip().upper() if db_scope_row and db_scope_row.value else "PAPER"
    req_scope = str(patch_dict.get("execution_scope", eff_scope)).strip().upper()

    try:
        updated_policy, applied_timing = stage_entry_session_settings(
            db, patch_dict, policy, scalp_cfg, now_ms=t_now, execution_scope=req_scope
        )
        db.commit()
        db.refresh(updated_policy)
    except Exception:
        db.rollback()
        raise

    auto_enabled = False
    try:
        auto_enabled = bool(strategy_service.get_auto_state(db))
    except Exception:
        auto_enabled = False

    return EntrySessionSettingsResponse(
        status="CONFIRMED",
        entry_session_policy=updated_policy.entry_session_policy,
        timing_profile=applied_timing,
        effective_ny_goal=int(updated_policy.ny_min_fills or 0),
        reserve_ny_slot=bool(updated_policy.reserve_ny_slot),
        ny_fallback_enabled=bool(updated_policy.ny_fallback_enabled),
        max_daily_fills=int(updated_policy.max_daily_fills or 3),
        daily_timezone=updated_policy.daily_timezone or "Asia/Ho_Chi_Minh",
        policy_version=int(updated_policy.version or 1),
        execution_scope=req_scope,
        auto_execution_enabled=auto_enabled,
        source="PERSISTED",
        reason_codes=[],
        message="Cấu hình phiên đã được áp dụng thành công.",
    )
