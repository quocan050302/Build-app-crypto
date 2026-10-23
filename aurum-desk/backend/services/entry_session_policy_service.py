"""
backend/services/entry_session_policy_service.py
V17.4.1: Coordinated Entry Session Policy & Timing Profile Service with Atomic Database CAS.

Implements Parts 023-040, 063, 081-084, 091-095, 100-106:
- Authoritative server execution scope resolution (never trusts client body over DB).
- Immutable plan: build_entry_session_update_plan.
- Authoritative database CAS: apply_entry_session_update_plan via conditional SQL UPDATE.
- Optimistic concurrency control (409 StalePolicyVersionError with fresh DB version).
- Pure resolver: resolve_entry_session_settings.
- Staging helper: stage_entry_session_settings (pure DB staging, NO commit).
- Atomic coordinator: update_entry_session_settings (single commit, rollback on failure).
- Strictly preserves user Auto authority, direction policies, capital, leverage, risk, and open positions.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import update, select
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

SUPPORTED_NON_LIVE_SCOPES = ("PAPER", "PAPER_EXECUTION", "PAPER_SHADOW", "SHADOW", "SIMULATION")


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


@dataclass(frozen=True)
class EntrySessionUpdatePlan:
    """Immutable validated plan for entry session settings update."""
    policy_id: str
    expected_version: int
    symbol: str
    mode: str
    timing_profile: str
    ny_goal: int
    reserve_ny_slot: bool
    ny_fallback_enabled: bool
    server_scope: str
    updated_at_ms: int


def resolve_server_execution_scope(db: Session) -> str:
    """
    PHẦN 011, 063, 101: Authoritative server execution scope.
    Reads SystemConfig 'execution_scope' directly from database.
    Never trusts or accepts client body execution_scope as authority.
    """
    row = db.query(models.SystemConfig).filter(models.SystemConfig.key == "execution_scope").first()
    if row and row.value and str(row.value).strip():
        return str(row.value).strip().upper()
    return "PAPER"


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

    is_paper_scope = str(execution_scope).upper() in SUPPORTED_NON_LIVE_SCOPES

    if pol_mode == ALL_SESSIONS_SETUP_FIRST:
        if not is_paper_scope:
            reason_codes.append("PAPER_ONLY_SESSION_PROFILE")
            config_conflict = True
        if timing_prof != CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value:
            reason_codes.append("ENTRY_SESSION_CONFIG_CONFLICT")
            config_conflict = True
        persisted_goal = getattr(policy, "ny_min_fills", None)
        if persisted_goal is not None and int(persisted_goal) != 0:
            reason_codes.append("NON_ZERO_GOAL_UNDER_SETUP_FIRST")
            config_conflict = True
        if bool(getattr(policy, "reserve_ny_slot", False)):
            reason_codes.append("RESERVE_ACTIVE_UNDER_SETUP_FIRST")
            config_conflict = True
        if bool(getattr(policy, "ny_fallback_enabled", False)):
            reason_codes.append("FALLBACK_ACTIVE_UNDER_SETUP_FIRST")
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
        "status": "CONFLICT" if config_conflict else "CONFIRMED",
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
    pending = next(
        (obj for obj in db.new if isinstance(obj, models.SystemConfig) and obj.key == key),
        None,
    )
    row = pending
    if row is None:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if row is None:
        db.add(models.SystemConfig(key=key, value=value, updated_at=now_ms))
    else:
        row.value = value
        row.updated_at = now_ms


def stage_entry_session_config_mirrors(
    db: Session,
    *,
    timing_profile: str,
    ny_goal: int,
    now_ms: int,
) -> None:
    """
    PHẦN 031 - 040 (Appendix A): Stages timing_profile and daily_ny_goal SystemConfig mirrors.
    Validates all inputs before staging. Does NOT commit, rollback, bump version, or mutate TradingPolicy.
    """
    from services.cg01_types import CG01TimingProfile

    valid_timing = {member.value for member in CG01TimingProfile}
    if not isinstance(timing_profile, str) or timing_profile not in valid_timing:
        raise SessionPolicyConfigError(
            f"Timing profile không hợp lệ: {timing_profile}.",
            code="ENTRY_SESSION_CONFIG_CONFLICT",
        )
    if type(ny_goal) is not int or ny_goal < 0:
        raise SessionPolicyConfigError(
            f"Mục tiêu NY phải là số nguyên không âm: {ny_goal}.",
            code="ENTRY_SESSION_CONFIG_CONFLICT",
        )

    _upsert_system_config_no_commit(db, "timing_profile", timing_profile, now_ms)
    _upsert_system_config_no_commit(db, "daily_ny_goal", str(ny_goal), now_ms)


def build_entry_session_update_plan(
    db: Session,
    patch: Any,
    now_ms: int,
    expected_version: Optional[int] = None,
    symbol: str = "XAUUSDT",
) -> EntrySessionUpdatePlan:
    """
    PHẦN 081: Validates and normalizes request into an immutable EntrySessionUpdatePlan.
    Does NOT mutate database or stage objects.
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

    # Server execution scope is authoritative (PHẦN 011, 062, 063, 101)
    server_scope = resolve_server_execution_scope(db)
    is_paper_scope = server_scope in SUPPORTED_NON_LIVE_SCOPES

    if desired_mode == ALL_SESSIONS_SETUP_FIRST:
        if not is_paper_scope:
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
        ny_goal = 0
        reserve_ny_slot = False
        ny_fallback_enabled = False
        timing_profile = CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value
    elif desired_mode in (ALL_SESSIONS_WITH_NY_RESERVE, NY_ONLY):
        desired_timing = patch_dict.get("timing_profile")
        if not desired_timing or desired_timing == CG01TimingProfile.CG01_ALL_SESSIONS_PAPER.value:
            desired_timing = "SILVER_BULLET_LONDON_NY"
        if desired_timing not in ("SILVER_BULLET_LONDON_NY", "CG01_NY_PAPER_EXPANDED"):
            raise SessionPolicyConfigError(
                f"timing_profile không hợp lệ cho chế độ legacy: {desired_timing}.",
                code="INVALID_TIMING_PROFILE",
            )
        timing_profile = desired_timing
        ny_min = patch_dict.get("ny_min_fills")
        ny_goal = 1 if (ny_min is None or ny_min <= 0) else int(ny_min)
        reserve_ny_slot = bool(patch_dict.get("reserve_ny_slot", True))
        ny_fallback_enabled = bool(patch_dict.get("ny_fallback_enabled", True))
    else:
        raise SessionPolicyConfigError(
            f"Unsupported entry_session_policy: {desired_mode}",
            code="UNSUPPORTED_SESSION_POLICY",
        )

    # Determine expected version (strict integer)
    ev = expected_version if expected_version is not None else patch_dict.get("expected_version")
    if ev is not None:
        if isinstance(ev, bool) or not isinstance(ev, int) or ev < 1:
            raise SessionPolicyConfigError("expected_version must be a positive integer", code="INVALID_VERSION_TYPE")
        exp_v = ev
    else:
        # Fall back to reading DB current version if omitted
        curr_row = db.query(models.TradingPolicy.version).filter(models.TradingPolicy.id == "default").first()
        exp_v = int(curr_row[0] if curr_row else 1)

    target_symbol = str(patch_dict.get("symbol", symbol) or "XAUUSDT").strip().upper()
    if target_symbol != "XAUUSDT":
        raise SessionPolicyConfigError(f"Unsupported symbol: {target_symbol}. Aurum only supports XAUUSDT.", code="UNSUPPORTED_SYMBOL")

    return EntrySessionUpdatePlan(
        policy_id="default",
        expected_version=exp_v,
        symbol=target_symbol,
        mode=desired_mode,
        timing_profile=timing_profile,
        ny_goal=ny_goal,
        reserve_ny_slot=reserve_ny_slot,
        ny_fallback_enabled=ny_fallback_enabled,
        server_scope=server_scope,
        updated_at_ms=now_ms,
    )


def apply_entry_session_update_plan(
    db: Session, plan: EntrySessionUpdatePlan
) -> Tuple[models.TradingPolicy, str, bool]:
    """
    PHẦN 082, 083: Authoritative Database CAS Update.
    Executes conditional UPDATE TradingPolicy SET ... WHERE id = :id AND version = :expected_version.
    Only proceeds if rowcount == 1, ensuring zero lost updates across concurrent sessions.
    """
    from services.trading_policy_service import TradingPolicyService

    # Bootstrap default policy in current transaction if not created yet
    TradingPolicyService.get_or_create_policy(db, commit=False)

    # Acquire database version fence via conditional same-value UPDATE (PHẦN 064, Phụ lục B)
    stmt_fence = (
        update(models.TradingPolicy)
        .where(
            models.TradingPolicy.id == plan.policy_id,
            models.TradingPolicy.version == plan.expected_version,
        )
        .values(version=models.TradingPolicy.version)
        .execution_options(synchronize_session=False)
    )
    fence_result = db.execute(stmt_fence)

    if fence_result.rowcount != 1:
        # Version mismatch in authoritative database - stale version error
        db.rollback()
        fresh_v = db.execute(
            select(models.TradingPolicy.version).where(models.TradingPolicy.id == plan.policy_id)
        ).scalar()
        raise StalePolicyVersionError(
            f"STALE_POLICY_VERSION: Cấu hình đã bị thay đổi ở nơi khác (phiên bản hiện tại: {fresh_v}, kỳ vọng: {plan.expected_version}).",
            code="STALE_POLICY_VERSION",
            current_version=fresh_v if fresh_v is not None else 1,
            expected_version=plan.expected_version,
        )

    # Fence matched! Read fresh scalar/mapping columns directly from DB without identity map bias
    fresh_policy = db.execute(
        select(
            models.TradingPolicy.id,
            models.TradingPolicy.version,
            models.TradingPolicy.entry_session_policy,
            models.TradingPolicy.ny_min_fills,
            models.TradingPolicy.reserve_ny_slot,
            models.TradingPolicy.ny_fallback_enabled,
        ).where(models.TradingPolicy.id == plan.policy_id)
    ).mappings().one()

    timing_val = db.execute(
        select(models.SystemConfig.value).where(models.SystemConfig.key == "timing_profile")
    ).scalar() or "SILVER_BULLET_LONDON_NY"

    goal_val = db.execute(
        select(models.SystemConfig.value).where(models.SystemConfig.key == "daily_ny_goal")
    ).scalar()

    is_noop = (
        fresh_policy["entry_session_policy"] == plan.mode
        and timing_val == plan.timing_profile
        and fresh_policy["ny_min_fills"] == plan.ny_goal
        and (goal_val is None or str(goal_val).strip() == str(plan.ny_goal))
        and bool(fresh_policy["reserve_ny_slot"]) == plan.reserve_ny_slot
        and bool(fresh_policy["ny_fallback_enabled"]) == plan.ny_fallback_enabled
    )
    if is_noop:
        curr_policy = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == plan.policy_id).first()
        if curr_policy is not None:
            db.expire(curr_policy)
        refreshed_policy = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == plan.policy_id).first()
        return refreshed_policy, plan.timing_profile, False

    # Execute authoritative mutating CAS update (PHẦN 069, 082)
    stmt_mutate = (
        update(models.TradingPolicy)
        .where(
            models.TradingPolicy.id == plan.policy_id,
            models.TradingPolicy.version == plan.expected_version,
        )
        .values(
            entry_session_policy=plan.mode,
            ny_min_fills=plan.ny_goal,
            reserve_ny_slot=plan.reserve_ny_slot,
            ny_fallback_enabled=plan.ny_fallback_enabled,
            version=plan.expected_version + 1,
            updated_at=plan.updated_at_ms,
        )
        .execution_options(synchronize_session=False)
    )
    result_mutate = db.execute(stmt_mutate)

    if result_mutate.rowcount != 1:
        db.rollback()
        fresh_v = db.execute(
            select(models.TradingPolicy.version).where(models.TradingPolicy.id == plan.policy_id)
        ).scalar()
        raise StalePolicyVersionError(
            f"STALE_POLICY_VERSION: Cấu hình đã bị thay đổi ở nơi khác (phiên bản hiện tại: {fresh_v}, kỳ vọng: {plan.expected_version}).",
            code="STALE_POLICY_VERSION",
            current_version=fresh_v if fresh_v is not None else 1,
            expected_version=plan.expected_version,
        )

    # Stage paired SystemConfig updates in the same transaction (PHẦN 061, Phụ lục B1)
    stage_entry_session_config_mirrors(
        db,
        timing_profile=plan.timing_profile,
        ny_goal=plan.ny_goal,
        now_ms=plan.updated_at_ms,
    )

    # Stage audit DomainEvent in the same transaction
    audit_event = models.DomainEvent(
        event_id=f"entry_session_upd_{int(plan.updated_at_ms)}_{plan.expected_version + 1}",
        sequence=int(plan.updated_at_ms),
        event_type="entry_session_policy.updated",
        aggregate_id=plan.policy_id,
        occurred_at=int(plan.updated_at_ms),
        published_at=int(plan.updated_at_ms),
        payload=json.dumps({
            "entry_session_policy": plan.mode,
            "timing_profile": plan.timing_profile,
            "ny_min_fills": plan.ny_goal,
            "reserve_ny_slot": plan.reserve_ny_slot,
            "ny_fallback_enabled": plan.ny_fallback_enabled,
            "before_version": plan.expected_version,
            "after_version": plan.expected_version + 1,
            "execution_scope": plan.server_scope,
        }),
    )
    db.add(audit_event)

    # Flush pending updates and expire cached policy ORM object so it reloads CAS state
    db.flush()
    curr_policy = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == plan.policy_id).first()
    if curr_policy is not None:
        db.expire(curr_policy)
    updated = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == plan.policy_id).first()
    return updated, plan.timing_profile, True


def stage_entry_session_settings(
    db: Session,
    patch: Any,
    current_policy: Optional[models.TradingPolicy] = None,
    current_scalp_config: Optional[Any] = None,
    now_ms: Optional[int] = None,
    execution_scope: str = "PAPER",
) -> Tuple[models.TradingPolicy, str]:
    """
    PHẦN 081: Stages TradingPolicy and SystemConfig paired updates in the active session.
    Delegates to plan + CAS. Does NOT commit. Caller owns commit / rollback.
    """
    t_now = now_ms if now_ms is not None else int(time.time() * 1000)
    plan = build_entry_session_update_plan(db, patch, now_ms=t_now)
    updated, staged_timing, _ = apply_entry_session_update_plan(db, plan)
    return updated, staged_timing


def update_entry_session_settings(
    db: Session,
    patch: Any,
    now_ms: Optional[int] = None,
    clock: Optional[IClock] = None,
) -> Any:
    """
    PHẦN 082: Coordinated atomic update service.
    Normalizes request into immutable plan, executes DB CAS, commits once.
    On failure rolls back transaction completely.
    """
    c = clock or live_clock
    t_now = now_ms if now_ms is not None else c.now_ms()

    from services.strategy_service import strategy_service
    from schemas import EntrySessionSettingsResponse

    plan = build_entry_session_update_plan(db, patch, now_ms=t_now)

    try:
        updated_policy, applied_timing, modified = apply_entry_session_update_plan(db, plan)
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
        execution_scope=plan.server_scope,
        auto_execution_enabled=auto_enabled,
        source="PERSISTED",
        reason_codes=[],
        message="Cấu hình phiên đã được áp dụng thành công.",
    )
