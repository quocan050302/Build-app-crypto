import time
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, Tuple, List, Sequence
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import models, crud
from services.clock import live_clock, IClock

logger = logging.getLogger(__name__)

def _fallback_search_allowed(policy, window, quota, now_ms: int) -> bool:
    """Single official 'may we SEARCH for a fallback?' condition (V14.3). Not an arm/fill permission."""
    if getattr(policy, "entry_session_policy", "") == "ALL_SESSIONS_SETUP_FIRST":
        return False
    from services.ny_quota_helpers import should_search_ny_fallback
    return should_search_ny_fallback(
        policy_active=bool(getattr(policy, "is_active", True)),
        mode=getattr(policy, "mode", "PAPER"),
        fallback_enabled=bool(getattr(policy, "ny_fallback_enabled", False)),
        now_ms=now_ms,
        window_start_ms=window["window_start_ms"],
        fallback_start_ms=window["fallback_start_ms"],
        window_end_ms=window["window_end_ms"],
        target_fills=int(quota.target_fills or 0),
        total_fills=int(quota.total_fills or 0),
    )



@dataclass(frozen=True)
class UpgradeSummary:
    changed: bool
    before_version: int
    after_version: int
    old_mode: str
    new_mode: str
    marker: str
    audit_id: Optional[str]
    status: str


@dataclass(frozen=True)
class EntrySessionContext:
    policy_mode: str
    policy_version: int
    strategy_family: str
    analysis_session: str
    as_of_ms: int
    in_ny_entry_window: bool
    ny_start_ms: int
    ny_end_ms: int
    standard_window_restriction: Optional[Tuple[int, int]]
    expiry_cap_ms: Optional[int]
    reason_codes: List[str] = field(default_factory=list)


def resolve_entry_session_context(
    policy: Any,
    family: str = "STANDARD_SMC",
    now_ms: Optional[int] = None,
    clock: Any = None,
) -> EntrySessionContext:
    """PHẦN 018, 019, 034, 035: Pure context resolver mapping session policy and strategy family to entry restrictions."""
    c = clock
    current_time = now_ms if now_ms is not None else (c.now_ms() if c else int(time.time() * 1000))
    ny_win = TradingPolicyService.get_ny_session_window(policy=policy, now_ms=current_time, clock=c)

    from services.smc_scenario_service import session_for
    analysis_session, _ = session_for(current_time)

    reason_codes: List[str] = []

    if policy is None:
        reason_codes.append("MISSING_POLICY")
        return EntrySessionContext(
            policy_mode="UNKNOWN",
            policy_version=1,
            strategy_family=family,
            analysis_session=analysis_session,
            as_of_ms=current_time,
            in_ny_entry_window=False,
            ny_start_ms=0,
            ny_end_ms=0,
            standard_window_restriction=(0, 0),
            expiry_cap_ms=current_time,
            reason_codes=reason_codes,
        )

    pol_mode = getattr(policy, "entry_session_policy", "ALL_SESSIONS_WITH_NY_RESERVE")
    pol_ver = getattr(policy, "version", 1) or 1

    in_ny = bool(ny_win.get("is_in_window", False))
    ny_start = int(ny_win.get("window_start_ms", 0))
    ny_end = int(ny_win.get("window_end_ms", 0))

    standard_families = (
        "STANDARD_SMC",
        "SMC_M1_REVERSAL",
        "SMC_M1_CONTINUATION",
        "SMC_M1_FVG_CONTINUATION",
        "SMC_M1_SWEEP_RECLAIM",
        "SCALP_M1_REVERSAL",
        "SCALP_M1_CONTINUATION",
        "CG01_SWEEP_MSS_FVG",
    )

    if family in ("NY_QUOTA_PAPER", "NY_DAILY_RESEARCH"):
        restriction = (ny_start, ny_end)
        expiry_cap = ny_end
    elif family in standard_families:
        if pol_mode == "NY_ONLY":
            restriction = (ny_start, ny_end)
            expiry_cap = ny_end
        elif pol_mode == "ALL_SESSIONS_WITH_NY_RESERVE":
            restriction = None
            expiry_cap = None
        elif pol_mode == "ALL_SESSIONS_SETUP_FIRST":
            restriction = None
            expiry_cap = None
        else:
            restriction = (0, 0)
            expiry_cap = current_time
            reason_codes.append("UNSUPPORTED_SESSION_POLICY")
    else:
        restriction = (0, 0)
        expiry_cap = current_time
        reason_codes.append("UNKNOWN_STRATEGY_FAMILY")

    return EntrySessionContext(
        policy_mode=pol_mode,
        policy_version=pol_ver,
        strategy_family=family,
        analysis_session=analysis_session,
        as_of_ms=current_time,
        in_ny_entry_window=in_ny,
        ny_start_ms=ny_start,
        ny_end_ms=ny_end,
        standard_window_restriction=restriction,
        expiry_cap_ms=expiry_cap,
        reason_codes=reason_codes,
    )


def compute_promoted_setup_expiry(
    now_ms: int,
    scenario_expires_at: Optional[int],
    scenario_ttl_ms: int,
    session_context: Optional[EntrySessionContext] = None,
) -> Optional[int]:
    """PHẦN 028, 029: Pure setup expiry calculation honoring model TTL, scenario expiry, and permitted session cap."""
    if now_ms <= 0 or scenario_ttl_ms <= 0:
        return None
    if scenario_expires_at is not None and scenario_expires_at <= now_ms:
        return None

    ttl_target = now_ms + scenario_ttl_ms
    cand = min(ttl_target, scenario_expires_at) if scenario_expires_at is not None else ttl_target

    if session_context and session_context.expiry_cap_ms is not None:
        cand = min(cand, session_context.expiry_cap_ms)

    if cand <= now_ms:
        return None
    return cand


@dataclass
class DailyFrequencyGoalEvaluation:
    daily_fills: int
    daily_min_goal: int
    daily_max_cap: int
    ny_fills: int
    ny_goal: int
    status: str
    reason_vi: str
    is_blocked: bool
    remaining_daily_slots: int
    ny_reserve_active: bool
    policy_version: int = 1
    shortfall_breakdown: Optional[Dict[str, int]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "daily_fills": self.daily_fills,
            "daily_min_goal": self.daily_min_goal,
            "daily_max_cap": self.daily_max_cap,
            "ny_fills": self.ny_fills,
            "ny_goal": self.ny_goal,
            "policy_version": self.policy_version,
            "status": self.status,
            "reason_vi": self.reason_vi,
            "is_blocked": self.is_blocked,
            "remaining_daily_slots": self.remaining_daily_slots,
            "ny_reserve_active": self.ny_reserve_active,
            "shortfall_breakdown": self.shortfall_breakdown or {},
        }


@dataclass(frozen=True)
class EffectiveDailyPolicy:
    min_goal: int
    max_fills: int
    ny_goal: int
    reserve_ny_slot: bool
    trading_day_timezone: str
    policy_version: int
    config_source: str
    account_scope: str = "default"


def resolve_effective_daily_policy(db: Session, account_scope: str = "default") -> EffectiveDailyPolicy:
    """
    V16.2 Rules 021 - 028: Canonical daily policy resolver.
    Single authoritative resolver returning min_goal, max_fills, ny_goal, reserve_ny_slot,
    trading_day_timezone, policy_version, and config_source.
    Ensures zero is treated as a valid value (never coerced to default via 'val or default').
    Synchronizes TradingPolicy and SystemConfig.
    """
    policy = TradingPolicyService.get_or_create_policy(db)

    # 1. max_fills (canonical source: policy.max_daily_fills)
    if policy.max_daily_fills is not None:
        max_fills = int(policy.max_daily_fills)
    else:
        sys_cap = db.query(models.SystemConfig).filter(models.SystemConfig.key == "daily_max_cap").first()
        max_fills = int(sys_cap.value) if sys_cap and sys_cap.value is not None else 3
        policy.max_daily_fills = max_fills

    # 2. ny_goal (canonical source: policy.ny_min_fills)
    if getattr(policy, "entry_session_policy", "") == "ALL_SESSIONS_SETUP_FIRST":
        ny_goal = 0
    elif policy.ny_min_fills is not None:
        ny_goal = int(policy.ny_min_fills)
    else:
        sys_ny = db.query(models.SystemConfig).filter(models.SystemConfig.key == "daily_ny_goal").first()
        ny_goal = int(sys_ny.value) if sys_ny and sys_ny.value is not None else 1
        policy.ny_min_fills = ny_goal

    # 3. min_goal (canonical source: policy.daily_min_goal)
    if hasattr(policy, "daily_min_goal") and policy.daily_min_goal is not None:
        min_goal = int(policy.daily_min_goal)
    else:
        sys_min = db.query(models.SystemConfig).filter(models.SystemConfig.key == "daily_min_goal").first()
        min_goal = int(sys_min.value) if sys_min and sys_min.value is not None else 2
        try:
            policy.daily_min_goal = min_goal
        except Exception:
            pass

    if getattr(policy, "entry_session_policy", "") == "ALL_SESSIONS_SETUP_FIRST":
        reserve_ny = False
    else:
        reserve_ny = bool(policy.reserve_ny_slot) if policy.reserve_ny_slot is not None else True
    tz = getattr(policy, "daily_timezone", "Asia/Ho_Chi_Minh") or "Asia/Ho_Chi_Minh"
    ver = int(policy.version or 1)

    return EffectiveDailyPolicy(
        min_goal=min_goal,
        max_fills=max_fills,
        ny_goal=ny_goal,
        reserve_ny_slot=reserve_ny,
        trading_day_timezone=tz,
        policy_version=ver,
        config_source="TRADING_POLICY",
        account_scope=account_scope,
    )


class TradingPolicyService:
    """
    Pure Business Trading Policy & Quota Service:
    - Enforces max 3 fills per day UTC+7 across all routes (manual, standard, fallback).
    - Enforces mandatory NY session policy (08:00 - 11:00 NY) with minimum 1 fill target.
    - Manages NY slot reservation (ALL_SESSIONS_WITH_NY_RESERVE reserves 3rd slot for NY).
    - Manages NY SessionQuota lifecycle: NOT_STARTED, SEEKING_STANDARD, SEEKING_FALLBACK,
      ARMED_PENDING_FILL, FULFILLED, BLOCKED, MISSED.
    - Injected clock support for deterministic replay and testing.
    """

    @staticmethod
    def get_default_policy() -> models.TradingPolicy:
        from policy_constants import get_default_policy_values
        defaults = dict(get_default_policy_values())
        defaults["updated_at"] = int(time.time() * 1000)
        return models.TradingPolicy(**defaults)

    @staticmethod
    def get_policy(db: Session) -> Optional[models.TradingPolicy]:
        return db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()

    @staticmethod
    def get_or_create_policy(db: Session, commit: bool = True) -> models.TradingPolicy:
        policy = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()
        if not policy:
            policy = TradingPolicyService.get_default_policy()
            db.add(policy)
            try:
                if commit:
                    db.commit()
                    db.refresh(policy)
                else:
                    db.flush()
            except IntegrityError:
                db.rollback()
                policy = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()
                if not policy:
                    raise
        return policy

    @staticmethod
    def upgrade_session_policy_contract(
        db: Session,
        *,
        target_symbol: str = "XAUUSDT",
        migration_id: str = "V14_4_5_ALL_SESSIONS_NY_RESERVE",
        desired_mode: str = "ALL_SESSIONS_WITH_NY_RESERVE",
        now_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        PHẦN 008, 009, 083, 085: Idempotent session policy upgrade with durable SystemConfig marker & audit record.
        Upgrades legacy NY_ONLY default to ALL_SESSIONS_WITH_NY_RESERVE for authorized target deployment.
        Gated by persisted SystemConfig migration marker: once applied, does NOT overwrite future explicit user NY_ONLY choices.
        Preserves custom capital/leverage/risk settings.
        """
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        marker_key = f"mig_marker:{migration_id}:{target_symbol}"[:50]

        # Check if migration marker was already committed to DB (PHẦN 009, 085)
        existing_marker = db.query(models.SystemConfig).filter(models.SystemConfig.key == marker_key).first()
        policy = TradingPolicyService.get_or_create_policy(db)
        old_mode = policy.entry_session_policy
        before_version = policy.version or 1

        if existing_marker:
            # Marker already applied on a prior startup; preserve user's explicit policy choices!
            return {
                "changed": False,
                "before_version": before_version,
                "after_version": before_version,
                "old_mode": old_mode,
                "new_mode": old_mode,
                "marker": migration_id,
                "audit_id": None,
                "status": "ALREADY_APPLIED",
            }

        if old_mode == "ALL_SESSIONS_SETUP_FIRST":
            # Guard explicit user choice of ALL_SESSIONS_SETUP_FIRST (PHẦN 078, 149)
            if not existing_marker:
                marker_row = models.SystemConfig(
                    key=marker_key,
                    value=json.dumps({
                        "migration_id": migration_id,
                        "status": "SKIPPED_USER_MODE",
                        "applied_at": int(now),
                        "target_symbol": target_symbol,
                        "old_mode": old_mode,
                        "new_mode": old_mode,
                        "before_version": before_version,
                        "after_version": before_version,
                    }),
                )
                db.add(marker_row)
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            return {
                "changed": False,
                "before_version": before_version,
                "after_version": before_version,
                "old_mode": old_mode,
                "new_mode": old_mode,
                "marker": migration_id,
                "audit_id": None,
                "status": "PRESERVED_ALL_SESSIONS_SETUP_FIRST",
            }

        if old_mode != desired_mode:
            policy.entry_session_policy = desired_mode
            policy.version = before_version + 1
            policy.updated_at = now

            # Audit event
            audit_event = models.DomainEvent(
                event_id=f"mig_{migration_id}_{int(now)}",
                sequence=int(now),
                event_type="policy.upgraded",
                aggregate_id=policy.id,
                occurred_at=int(now),
                published_at=int(now),
                payload=json.dumps({
                    "migration_id": migration_id,
                    "target_symbol": target_symbol,
                    "old_mode": old_mode,
                    "new_mode": desired_mode,
                    "before_version": before_version,
                    "after_version": policy.version,
                }),
            )
            db.add(audit_event)

            # Persist durable completion marker (PHẦN 009, 085)
            marker_row = models.SystemConfig(
                key=marker_key,
                value=json.dumps({
                    "migration_id": migration_id,
                    "status": "APPLIED",
                    "applied_at": int(now),
                    "target_symbol": target_symbol,
                    "old_mode": old_mode,
                    "new_mode": desired_mode,
                    "before_version": before_version,
                    "after_version": policy.version,
                    "audit_ref": audit_event.event_id,
                }),
                updated_at=int(now),
            )
            db.add(marker_row)
            db.commit()
            db.refresh(policy)
            return {
                "changed": True,
                "before_version": before_version,
                "after_version": policy.version,
                "old_mode": old_mode,
                "new_mode": desired_mode,
                "marker": migration_id,
                "audit_id": audit_event.event_id,
                "status": "APPLIED",
            }

        # Policy was already in desired mode; write marker so future runs know migration is recorded
        marker_row = models.SystemConfig(
            key=marker_key,
            value=json.dumps({
                "migration_id": migration_id,
                "status": "APPLIED",
                "applied_at": int(now),
                "target_symbol": target_symbol,
                "old_mode": old_mode,
                "new_mode": desired_mode,
                "before_version": before_version,
                "after_version": before_version,
                "audit_ref": None,
            }),
            updated_at=int(now),
        )
        db.add(marker_row)
        db.commit()
        return {
            "changed": False,
            "before_version": before_version,
            "after_version": before_version,
            "old_mode": old_mode,
            "new_mode": desired_mode,
            "marker": migration_id,
            "audit_id": None,
            "status": "APPLIED",
        }

    @staticmethod
    def update_policy(db: Session, update_data: Dict[str, Any], clock: Optional[IClock] = None) -> models.TradingPolicy:
        from policy_constants import validate_trading_policy_update
        c = clock or live_clock
        now_ms = c.now_ms()
        policy = TradingPolicyService.get_policy(db)
        if not policy:
            policy = TradingPolicyService.get_default_policy()
            db.add(policy)
            db.flush()

        # Check server execution scope if setting ALL_SESSIONS_SETUP_FIRST (PHẦN 104, 106, Phụ lục C)
        if update_data.get("entry_session_policy") == "ALL_SESSIONS_SETUP_FIRST":
            from services.entry_session_policy_service import resolve_server_execution_scope
            server_scope = resolve_server_execution_scope(db)
            if server_scope != "PAPER":
                raise ValueError(
                    f"PAPER_ONLY_SESSION_PROFILE: Chế độ ALL_SESSIONS_SETUP_FIRST chỉ cho phép áp dụng trên scope PAPER; máy chủ hiện tại là {server_scope}."
                )

        # Centralized validation
        validated = validate_trading_policy_update(policy, update_data)

        try:
            # V16.39.15: If min_net_rr is present, stage canonical SystemConfig and compatibility TradingPolicy atomically (Rules 033, 035, 044, 045)
            if "min_net_rr" in validated:
                from services.rr_policy_service import stage_min_net_rr_update
                stage_min_net_rr_update(db, validated["min_net_rr"], now_ms=now_ms)
                policy = TradingPolicyService.get_policy(db)

            # Update other attributes
            for k, v in validated.items():
                if k != "min_net_rr" and hasattr(policy, k) and k not in ("id", "version", "updated_at"):
                    setattr(policy, k, v)

            # V17.4: Synchronize SystemConfig timing and NY goal when entry_session_policy is updated
            if "entry_session_policy" in validated:
                new_session_pol = validated["entry_session_policy"]
                if new_session_pol == "ALL_SESSIONS_SETUP_FIRST":
                    policy.ny_min_fills = 0
                    policy.reserve_ny_slot = False
                    policy.ny_fallback_enabled = False
                    row_tp = db.query(models.SystemConfig).filter(models.SystemConfig.key == "timing_profile").first()
                    if row_tp:
                        row_tp.value = "CG01_ALL_SESSIONS_PAPER"
                        row_tp.updated_at = now_ms
                    else:
                        db.add(models.SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=now_ms))
                    row_ny = db.query(models.SystemConfig).filter(models.SystemConfig.key == "daily_ny_goal").first()
                    if row_ny:
                        row_ny.value = "0"
                        row_ny.updated_at = now_ms
                    else:
                        db.add(models.SystemConfig(key="daily_ny_goal", value="0", updated_at=now_ms))
                elif new_session_pol in ("ALL_SESSIONS_WITH_NY_RESERVE", "NY_ONLY"):
                    row_tp = db.query(models.SystemConfig).filter(models.SystemConfig.key == "timing_profile").first()
                    if row_tp and row_tp.value == "CG01_ALL_SESSIONS_PAPER":
                        row_tp.value = "SILVER_BULLET_LONDON_NY"
                        row_tp.updated_at = now_ms

            policy.version = (policy.version or 1) + 1
            policy.updated_at = now_ms
            db.commit()
            db.refresh(policy)
            return policy
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def get_ny_session_window(
        policy: Optional[models.TradingPolicy] = None,
        now_ms: Optional[int] = None,
        clock: Optional[IClock] = None
    ) -> Dict[str, Any]:
        """
        Calculates authoritative epoch ms boundaries for current NY trading window.
        Uses standard IANA timezones (ZoneInfo) to accurately handle Daylight Saving Time (EDT vs EST).
        """
        c = clock or live_clock
        current_time = now_ms if now_ms is not None else c.now_ms()
        p = policy if policy is not None else TradingPolicyService.get_default_policy()

        ny_tz = ZoneInfo(p.ny_timezone or "America/New_York")
        vn_tz = ZoneInfo(p.daily_timezone or "Asia/Ho_Chi_Minh")

        now_ny = datetime.fromtimestamp(current_time / 1000.0, tz=ny_tz)
        now_vn = datetime.fromtimestamp(current_time / 1000.0, tz=vn_tz)

        # Parse start and end hours/minutes
        h_start, m_start = map(int, p.ny_entry_start.split(":"))
        h_end, m_end = map(int, p.ny_entry_end.split(":"))
        h_fb, m_fb = map(int, p.ny_fallback_start.split(":"))

        # Baseline NY date for the current/most relevant session
        session_ny_date = now_ny.date()
        session_instance_id = f"NY-{session_ny_date.strftime('%Y-%m-%d')}"

        dt_start_ny = datetime(session_ny_date.year, session_ny_date.month, session_ny_date.day, h_start, m_start, tzinfo=ny_tz)
        dt_end_ny = datetime(session_ny_date.year, session_ny_date.month, session_ny_date.day, h_end, m_end, tzinfo=ny_tz)
        dt_fb_ny = datetime(session_ny_date.year, session_ny_date.month, session_ny_date.day, h_fb, m_fb, tzinfo=ny_tz)

        start_ms = int(dt_start_ny.timestamp() * 1000)
        end_ms = int(dt_end_ny.timestamp() * 1000)
        fb_ms = int(dt_fb_ny.timestamp() * 1000)

        dt_start_vn = dt_start_ny.astimezone(vn_tz)
        dt_end_vn = dt_end_ny.astimezone(vn_tz)

        # Determine phase
        if current_time < start_ms:
            phase = "BEFORE_WINDOW"
        elif start_ms <= current_time < fb_ms:
            phase = "IN_STANDARD_WINDOW"
        elif fb_ms <= current_time < end_ms:
            phase = "IN_FALLBACK_WINDOW"
        else:
            phase = "AFTER_WINDOW"

        is_in_window = (start_ms <= current_time < end_ms)

        return {
            "session_instance_id": session_instance_id,
            "session_date_ny": session_ny_date.strftime("%Y-%m-%d"),
            "session_date_vn": dt_start_vn.strftime("%Y-%m-%d"),
            "window_start_ms": start_ms,
            "window_end_ms": end_ms,
            "fallback_start_ms": fb_ms,
            "ny_time_str": now_ny.strftime("%H:%M:%S"),
            "vn_time_str": now_vn.strftime("%H:%M:%S"),
            "local_ny_time": now_ny.strftime("%H:%M:%S"),
            "local_vn_time": now_vn.strftime("%H:%M:%S"),
            "ny_window_str": f"{p.ny_entry_start} - {p.ny_entry_end} NY",
            "vn_window_str": f"{dt_start_vn.strftime('%H:%M')} - {dt_end_vn.strftime('%H:%M')} VN",
            "phase": phase,
            "is_in_window": is_in_window,
            "is_fallback_active": (fb_ms <= current_time < end_ms) and bool(p.ny_fallback_enabled)
        }

    @staticmethod
    def get_or_create_session_quota(
        db: Session,
        policy: Optional[models.TradingPolicy] = None,
        now_ms: Optional[int] = None,
        clock: Optional[IClock] = None
    ) -> models.SessionQuota:
        c = clock or live_clock
        current_time = now_ms if now_ms is not None else c.now_ms()
        p = policy or TradingPolicyService.get_policy(db) or TradingPolicyService.get_default_policy()

        window = TradingPolicyService.get_ny_session_window(p, now_ms=current_time, clock=c)
        instance_id = window["session_instance_id"]

        quota = db.query(models.SessionQuota).filter(models.SessionQuota.session_instance_id == instance_id).first()
        if not quota:
            utc_date_str = datetime.fromtimestamp(current_time / 1000.0, tz=ZoneInfo("UTC")).strftime("%Y-%m-%d")
            quota = models.SessionQuota(
                account_namespace="default",
                symbol=p.symbol or "XAUUSDT",
                session_instance_id=instance_id,
                session_date_utc=utc_date_str,
                session_date_vn=window["session_date_vn"],
                session_date_ny=window["session_date_ny"],
                window_start_ms=window["window_start_ms"],
                window_end_ms=window["window_end_ms"],
                fallback_start_ms=window["fallback_start_ms"],
                target_fills=0 if getattr(p, "entry_session_policy", "") == "ALL_SESSIONS_SETUP_FIRST" else (int(p.ny_min_fills) if p.ny_min_fills is not None else 1),
                standard_fills=0,
                fallback_fills=0,
                total_fills=0,
                quota_status="NOT_STARTED" if window["phase"] == "BEFORE_WINDOW" else "SEEKING_STANDARD",
                last_status_reason="Khởi tạo phiên theo dõi chỉ tiêu phiên Mỹ",
                last_evaluated_at=current_time,
                created_at=current_time,
                updated_at=current_time
            )
            db.add(quota)
            try:
                db.commit()
            except IntegrityError:
                # V14.3: the UI poll (ny-session GET) and the strategy loop can create the same session
                # row concurrently; losing that race is not an error - read the winner's row.
                db.rollback()
                quota = db.query(models.SessionQuota).filter(
                    models.SessionQuota.session_instance_id == instance_id).first()
                if quota is None:
                    raise
                return quota
            db.refresh(quota)
        return quota

    @staticmethod
    def _missed_reason_text(db: Session, quota: models.SessionQuota) -> str:
        """Generic closing sentence + the MEASURED primary cause when this session has an audit (V14.3)."""
        base = "Hết cửa sổ phiên Mỹ nhưng không có lệnh PAPER nào được khớp"
        try:
            from services.ny_opportunity_audit_service import NYOpportunityAuditService
            from services.ny_quota_helpers import classify_reason
            diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id=quota.session_instance_id)
            if diag["availability"] != "MEASURED":
                return (base + " (phiên này chưa có dữ liệu quét để giải thích nguyên nhân)")[:255]
            code = diag.get("primary_missed_reason")
            if not code:
                return base
            cat = diag.get("missed_category")
            label = {"DATA_UNAVAILABLE": "dữ liệu không đủ để kết luận", "NO_SIGNAL": "không có tín hiệu",
                     "SIGNAL_BLOCKED": "có tín hiệu nhưng bị chặn", "ARMED_NOT_FILLED": "đã đặt lệnh nhưng chưa khớp"}.get(cat, cat or "")
            return f"{base} — {label}: {classify_reason(code)[1]}"[:255]
        except Exception:  # noqa: BLE001 - never let a diagnostic text break the quota state machine
            return base

    @staticmethod
    def update_quota_lifecycle_state(
        db: Session,
        policy: Optional[models.TradingPolicy] = None,
        now_ms: Optional[int] = None,
        clock: Optional[IClock] = None
    ) -> models.SessionQuota:
        """
        Transitions the quota state machine deterministically based on time,
        active position, armed orders, and day audit limiters.
        """
        c = clock or live_clock
        current_time = now_ms if now_ms is not None else c.now_ms()
        p = policy or TradingPolicyService.get_policy(db) or TradingPolicyService.get_default_policy()
        quota = TradingPolicyService.get_or_create_session_quota(db, p, now_ms=current_time, clock=c)
        window = TradingPolicyService.get_ny_session_window(p, now_ms=current_time, clock=c)

        # Early branch for ALL_SESSIONS_SETUP_FIRST: NY quota is not required / not applicable (Parts 013, 074, 107)
        if getattr(p, "entry_session_policy", "") == "ALL_SESSIONS_SETUP_FIRST":
            quota.quota_status = "NOT_APPLICABLE"
            quota.last_status_reason = "Không đặt chỉ tiêu riêng cho phiên Mỹ (ALL_SESSIONS_SETUP_FIRST)"
            quota.last_evaluated_at = current_time
            quota.updated_at = current_time
            try:
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.warning(f"Failed to commit quota lifecycle transition: {exc}")
            return quota

        # 1. If quota already fulfilled
        if quota.total_fills >= quota.target_fills:
            quota.quota_status = "FULFILLED"
            quota.last_status_reason = f"Đã hoàn thành chỉ tiêu phiên Mỹ: {quota.total_fills}/{quota.target_fills} lệnh khớp"
            quota.last_evaluated_at = current_time
            quota.updated_at = current_time
            try:
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.warning(f"Failed to commit quota lifecycle transition: {exc}")
            return quota

        # 2. Before entry window
        if window["phase"] == "BEFORE_WINDOW":
            quota.quota_status = "NOT_STARTED"
            quota.last_status_reason = f"Chưa tới cửa sổ phiên Mỹ ({window['vn_window_str']})"
            quota.last_evaluated_at = current_time
            quota.updated_at = current_time
            try:
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.warning(f"Failed to commit quota lifecycle transition: {exc}")
            return quota

        # 3. After entry window has closed
        if window["phase"] == "AFTER_WINDOW":
            if quota.total_fills == 0:
                quota.quota_status = "MISSED"
                quota.last_status_reason = TradingPolicyService._missed_reason_text(db, quota)
            quota.last_evaluated_at = current_time
            quota.updated_at = current_time
            try:
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.warning(f"Failed to commit quota lifecycle transition: {exc}")
            return quota

        # 4. In window (Standard or Fallback): check block conditions
        day_audit = crud.get_or_create_today_audit(db, clock=c)
        active_pos = crud.get_active_position(db)
        armed_order = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
        is_blackout, blackout_reason, _ = crud.check_news_blackout(db, current_time)

        if day_audit.fills_count >= 3:
            quota.quota_status = "BLOCKED"
            quota.last_status_reason = "Đã chạm trần 3 lệnh tối đa trong ngày (UTC+7)"
        elif day_audit.consecutive_losses >= 2:
            quota.quota_status = "BLOCKED"
            quota.last_status_reason = "Đã dừng giao dịch sau 2 lệnh lỗ liên tiếp"
        elif day_audit.is_blocked:
            quota.quota_status = "BLOCKED"
            quota.last_status_reason = f"Tạm dừng giao dịch: {day_audit.block_reason}"
        elif is_blackout:
            quota.quota_status = "BLOCKED"
            quota.last_status_reason = f"Đang trong khung Blackout tin tức: {blackout_reason}"
        elif active_pos:
            quota.quota_status = "BLOCKED"
            quota.last_status_reason = f"Đang duy trì vị thế mở ({active_pos.direction} tại {active_pos.actual_entry:.2f})"
        elif armed_order:
            quota.quota_status = "ARMED_PENDING_FILL"
            quota.last_status_reason = f"Đang có lệnh chờ khớp ({armed_order.direction} tại {armed_order.planned_entry:.2f})"
        elif window["phase"] == "IN_FALLBACK_WINDOW" and _fallback_search_allowed(p, window, quota, current_time):
            # V14.3: same official condition as the strategy (flag/mode/target/window all considered).
            quota.quota_status = "SEEKING_FALLBACK"
            quota.last_status_reason = "Đang tìm cơ hội Fallback nghiên cứu (sau giờ fallback)"
        elif window["phase"] == "IN_FALLBACK_WINDOW" and not getattr(p, "ny_fallback_enabled", False):
            quota.quota_status = "SEEKING_STANDARD"
            quota.last_status_reason = "Fallback đang tắt — chỉ quét cơ hội SMC chuẩn"
        elif getattr(p, "entry_session_policy", "") == "ALL_SESSIONS_SETUP_FIRST":
            if quota.total_fills > 0:
                quota.quota_status = "FULFILLED"
                quota.last_status_reason = f"Đã ghi nhận {quota.total_fills} lệnh khớp phiên Mỹ (analytics)"
            else:
                quota.quota_status = "NOT_APPLICABLE"
                quota.last_status_reason = "Không đặt chỉ tiêu riêng cho phiên Mỹ"
        else:
            quota.quota_status = "SEEKING_STANDARD"
            quota.last_status_reason = "Đang quét cơ hội SMC chuẩn trong phiên Mỹ"

        quota.last_evaluated_at = current_time
        quota.updated_at = current_time
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(f"Failed to commit quota lifecycle transition: {exc}")
        return quota

    @staticmethod
    def get_active_policy(db: Session, symbol: str = "XAUUSDT") -> models.TradingPolicy:
        return TradingPolicyService.get_or_create_policy(db)

    @staticmethod
    def _news_gate(db: Session, now_ms: int) -> Dict[str, Any]:
        """
        V14.2: structured news gate that applies even when the session policy is inactive.
        Fail closed: if the calendar cannot be read the entry is not allowed.
        Returns {"blocked": bool, "reason_code": str|None, "message": str|None, "view": dict|None}.
        """
        try:
            from services.news_blackout_service import evaluate_news_blackout
            view = evaluate_news_blackout(db, now_ms)
        except Exception as e:  # noqa: BLE001 - fail closed, never allow on error
            logger.error(f"News gate unavailable: {e}", exc_info=True)
            return {
                "blocked": True,
                "reason_code": "ENTRY_GUARD_DATA_UNAVAILABLE",
                "message": "ENTRY_GUARD_DATA_UNAVAILABLE: Chưa đọc được lịch tin tức, tạm dừng vào lệnh mới.",
                "view": None,
            }
        if view.is_blackout:
            return {
                "blocked": True,
                "reason_code": "NEWS_BLACKOUT",
                "message": f"NEWS_BLACKOUT: {view.reason_text()} - còn khoảng {view.remaining_minutes} phút",
                "view": view.to_dict(),
            }
        return {"blocked": False, "reason_code": None, "message": None, "view": view.to_dict()}

    @staticmethod
    def evaluate_entry_policy(
        db: Session,
        order_type_or_symbol: str = "MARKET",
        strategy_family_or_time: Any = "STANDARD_SMC",
        clock: Optional[IClock] = None,
        now_ms: Optional[int] = None,
        order_type: str = "MARKET",
        strategy_family: str = "STANDARD_SMC"
    ) -> Dict[str, Any]:
        """
        Unified gatekeeper for ALL entry paths (manual, auto standard, auto fallback).
        Returns whether an entry is permitted under the current trading policy and quota rules.
        """
        c = clock or live_clock
        current_time = now_ms

        if isinstance(strategy_family_or_time, datetime):
            current_time = int(strategy_family_or_time.timestamp() * 1000)
        elif isinstance(order_type_or_symbol, datetime):
            current_time = int(order_type_or_symbol.timestamp() * 1000)

        if current_time is None:
            current_time = c.now_ms()

        policy = TradingPolicyService.get_policy(db)
        news_gate = TradingPolicyService._news_gate(db, current_time)
        if not policy or not getattr(policy, "is_active", True):
            # Session policy inactive: safety news gate still applies.
            _inactive_blocked = news_gate["blocked"]
            return {
                "can_enter": not _inactive_blocked,
                "allowed": not _inactive_blocked,
                "reason_codes": [news_gate["reason_code"]] if _inactive_blocked else [],
                "reason_code": news_gate["reason_code"] if _inactive_blocked else None,
                "block_reasons": [news_gate["message"]] if _inactive_blocked else [],
                "reason_message": news_gate["message"] if _inactive_blocked else None,
                "news_view": news_gate["view"],
                "session_instance_id": "DEFAULT",
                "policy_config_version": 1,
                "policy": policy,
                "quota_state": "NOT_APPLICABLE",
                "quota_status": "NOT_APPLICABLE",
                "ny_fills": 0,
                "ny_fill_count": 0,
                "ny_min_fills": 1,
                "ny_target_fills": 1,
                "daily_fills": 0,
                "daily_fill_count": 0,
                "max_daily_fills": 3,
                "remaining_daily_slots": 3,
                "reserved_slots": 0,
                "is_in_ny_window": True,
                "is_fallback_active": False,
                "local_ny_time": "",
                "local_vn_time": "",
                "ny_window_display": "",
                "vn_window_display": "",
                "minutes_to_window_start": None,
                "minutes_to_fallback": None,
                "minutes_to_window_end": None,
                "window_info": {}
            }

        window = TradingPolicyService.get_ny_session_window(policy, now_ms=current_time, clock=c)
        quota = TradingPolicyService.update_quota_lifecycle_state(db, policy, now_ms=current_time, clock=c)
        day_audit = crud.get_or_create_today_audit(db, clock=c)

        can_enter = True
        reason_codes = []
        block_reasons = []

        # 1. Mode check
        if policy.mode != "PAPER":
            can_enter = False
            reason_codes.append("UNSUPPORTED_MODE")
            block_reasons.append("Chỉ hỗ trợ chế độ PAPER trading.")

        # 2. Daily Fill Cap (Strict: Max 3 fills per UTC+7 day)
        if day_audit.fills_count >= policy.max_daily_fills:
            can_enter = False
            reason_codes.append("MAX_DAILY_ENTRIES")
            block_reasons.append(f"Đã đạt giới hạn tối đa {policy.max_daily_fills} lệnh khớp/ngày (UTC+7)")

        # 3. Session Window & Slot Reservation Policy
        if policy.entry_session_policy == "NY_ONLY":
            if not window["is_in_window"]:
                can_enter = False
                reason_codes.append("OUTSIDE_ENTRY_WINDOW")
                block_reasons.append(
                    f"OUTSIDE_ENTRY_WINDOW: Ngoài cửa sổ giao dịch phiên Mỹ ({window['ny_window_str']} = {window['vn_window_str']}). "
                    f"Chính sách NY_ONLY chỉ cho phép mở lệnh trong khung giờ này."
                )
        elif policy.entry_session_policy == "ALL_SESSIONS_WITH_NY_RESERVE":
            from policy_constants import compute_reserved_capacity
            q_fills = 0
            try:
                if quota is not None:
                    q_fills = int(getattr(quota, "total_fills", 0) or 0)
            except Exception:
                q_fills = 0
            d_fills = 0
            try:
                if day_audit is not None:
                    d_fills = int(getattr(day_audit, "fills_count", 0) or 0)
            except Exception:
                d_fills = 0

            target_ny = int(getattr(policy, "ny_min_fills", 1)) if getattr(policy, "ny_min_fills", None) is not None else 1
            cap = compute_reserved_capacity(
                max_fills=int(getattr(policy, "max_daily_fills", 3) or 3),
                daily_fills=d_fills,
                ny_fills=q_fills,
                target=target_ny,
                reserve_enabled=bool(getattr(policy, "reserve_ny_slot", True)),
            )
            if not window["is_in_window"] and cap["available_outside_ny"] == 0 and cap["remaining"] > 0:
                can_enter = False
                reason_codes.append("NY_SLOT_RESERVED")
                block_reasons.append(
                    f"NY_SLOT_RESERVED: Đã sử dụng {d_fills} slot giao dịch trong ngày. "
                    f"Slot còn lại bắt buộc được dự trữ cho mục tiêu {target_ny} lệnh phiên Mỹ."
                )
        elif policy.entry_session_policy == "ALL_SESSIONS_SETUP_FIRST":
            # No NY window restriction, no NY reservation, no NY quota requirement (Parts 064, 065)
            pass
        else:
            can_enter = False
            reason_codes.append("UNSUPPORTED_SESSION_POLICY")
            block_reasons.append(f"Chính sách session không được hỗ trợ: {policy.entry_session_policy}")

        # 3b. V14.3: a NY quota fallback entry exists only inside the NY entry window, whatever the
        # session policy (ALL_SESSIONS_WITH_NY_RESERVE would otherwise let it fill at/after window end).
        if strategy_family == "NY_QUOTA_PAPER":
            if policy.entry_session_policy == "ALL_SESSIONS_SETUP_FIRST":
                can_enter = False
                reason_codes.append("FALLBACK_DISABLED")
                block_reasons.append("Chế độ ALL_SESSIONS_SETUP_FIRST không áp dụng NY quota fallback.")
            elif not window["is_in_window"] and "OUTSIDE_ENTRY_WINDOW" not in reason_codes:
                can_enter = False
                reason_codes.append("OUTSIDE_ENTRY_WINDOW")
                block_reasons.append(
                    f"OUTSIDE_ENTRY_WINDOW: Lệnh fallback phiên Mỹ chỉ được mở trong cửa sổ {window['ny_window_str']} "
                    f"({window['vn_window_str']}); cửa sổ đã đóng."
                )

        # 4. News blackout (V14.2): actual allowed=False, independent of quota_status.
        if news_gate["blocked"]:
            can_enter = False
            reason_codes.append(news_gate["reason_code"])
            block_reasons.append(news_gate["message"])

        remaining_daily_slots = max(0, policy.max_daily_fills - getattr(day_audit, "fills_count", 0))
        try:
            quota_fills = getattr(quota, "total_fills", 0) or 0
        except Exception:
            quota_fills = 0

        if policy.entry_session_policy == "ALL_SESSIONS_SETUP_FIRST":
            reserved_slots = 0
        else:
            reserved_slots = 1 if (policy.reserve_ny_slot and quota_fills == 0 and remaining_daily_slots > 0) else 0

        # Minutes calculations
        min_to_start = None
        min_to_fb = None
        min_to_end = None
        if current_time < window["window_start_ms"]:
            min_to_start = max(0, int((window["window_start_ms"] - current_time) / 60000))
        if current_time < window["fallback_start_ms"]:
            min_to_fb = max(0, int((window["fallback_start_ms"] - current_time) / 60000))
        if current_time < window["window_end_ms"]:
            min_to_end = max(0, int((window["window_end_ms"] - current_time) / 60000))

        return {
            "can_enter": can_enter,
            "allowed": can_enter,
            "reason_codes": reason_codes,
            "reason_code": reason_codes[0] if reason_codes else None,
            "block_reasons": block_reasons,
            "reason_message": block_reasons[0] if block_reasons else None,
            "news_view": news_gate["view"],
            "session_instance_id": window["session_instance_id"],
            "policy_config_version": policy.version,
            "policy": policy,
            "quota_state": quota.quota_status,
            "quota_status": quota.quota_status,
            "quota_reason": quota.last_status_reason,
            "ny_fills": quota.total_fills,
            "ny_fill_count": quota.total_fills,
            "ny_min_fills": quota.target_fills,
            "ny_target_fills": quota.target_fills,
            "daily_fills": day_audit.fills_count,
            "daily_fill_count": day_audit.fills_count,
            "max_daily_fills": policy.max_daily_fills,
            "remaining_daily_slots": remaining_daily_slots,
            "reserved_slots": reserved_slots,
            "is_in_ny_window": window["is_in_window"],
            "is_fallback_active": bool(window["is_fallback_active"]),
            "local_ny_time": window["local_ny_time"],
            "local_vn_time": window["local_vn_time"],
            "ny_window_display": window["ny_window_str"],
            "vn_window_display": window["vn_window_str"],
            "minutes_to_window_start": min_to_start,
            "minutes_to_fallback": min_to_fb,
            "minutes_to_window_end": min_to_end,
            "window_info": window
        }

    @staticmethod
    def record_quota_fill(
        db: Session,
        order: models.PaperOrder,
        clock: Optional[IClock] = None,
        commit: bool = False
    ) -> models.SessionQuota:
        """
        Atomically records an opened fill into the SessionQuota ledger.
        Ensures quota is fulfilled if order opened in NY window.
        """
        c = clock or live_clock
        now_ms = order.opened_at or c.now_ms()
        policy = TradingPolicyService.get_policy(db) or TradingPolicyService.get_default_policy()
        window = TradingPolicyService.get_ny_session_window(policy, now_ms=now_ms, clock=c)
        quota = TradingPolicyService.get_or_create_session_quota(db, policy, now_ms=now_ms, clock=c)

        # Check if opened_at falls into NY window
        if window["window_start_ms"] <= now_ms < window["window_end_ms"]:
            family = getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC"
            if family == "NY_QUOTA_PAPER":
                quota.fallback_fills += 1
            else:
                quota.standard_fills += 1
            quota.total_fills += 1
            quota.quota_status = "FULFILLED"
            quota.last_status_reason = f"Đã hoàn thành chỉ tiêu phiên Mỹ bằng lệnh {order.direction} (#{order.id})"
            order.session_instance_id = quota.session_instance_id

        quota.last_evaluated_at = now_ms
        quota.updated_at = now_ms
        if commit:
            db.commit()
            db.refresh(quota)
        return quota

    @staticmethod
    def record_fill(
        db: Session,
        symbol: str = "XAUUSDT",
        fill_time: Optional[datetime] = None,
        order_id: Optional[str] = None,
        is_ny_quota_candidate: bool = True,
        order: Optional[models.PaperOrder] = None,
        clock: Optional[IClock] = None,
        commit: bool = False
    ) -> Dict[str, Any]:
        """
        Convenience wrapper for TradeLifecycleService to atomically record fill
        into SessionQuota and return session details.
        """
        target_order = order
        if not target_order and order_id:
            target_order = db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()

        if target_order:
            quota = TradingPolicyService.record_quota_fill(db, target_order, clock=clock, commit=commit)
            return {
                "session_instance_id": quota.session_instance_id,
                "quota_status": quota.quota_status,
                "total_fills": quota.total_fills
            }

        # Fallback if order object not yet flushed
        policy = TradingPolicyService.get_policy(db)
        c = clock or live_clock
        now_ms = int(fill_time.timestamp() * 1000) if fill_time else c.now_ms()
        window = TradingPolicyService.get_ny_session_window(policy, now_ms=now_ms, clock=c)
        quota = TradingPolicyService.get_or_create_session_quota(db, policy, now_ms=now_ms, clock=c)

        if window["window_start_ms"] <= now_ms < window["window_end_ms"]:
            quota.total_fills += 1
            quota.standard_fills += 1
            quota.quota_status = "FULFILLED"
            quota.last_status_reason = f"Đã hoàn thành chỉ tiêu phiên Mỹ ({order_id or 'FILL'})"
            quota.last_evaluated_at = now_ms
            quota.updated_at = now_ms
            if commit:
                db.commit()

        return {
            "session_instance_id": quota.session_instance_id,
            "quota_status": quota.quota_status,
            "total_fills": quota.total_fills
        }

    @staticmethod
    def evaluate_daily_frequency_goal(
        db: Session,
        now_ms: Optional[int] = None,
        clock: Optional[IClock] = None,
        daily_min_goal: Optional[int] = None,
        effective_policy: Optional[EffectiveDailyPolicy] = None,
    ) -> DailyFrequencyGoalEvaluation:
        """
        V16 Daily Frequency Goal Evaluator (Rules 028-030, 126-140, V16.2 Rules 021-028).
        Evaluates real daily fills vs daily minimum goal, hard cap, and NY session goal.
        States: SEARCHING, MINIMUM_MET, CAP_REACHED, SHORTFALL, BLOCKED.
        Does NOT place orders or bypass guards.
        """
        c = clock or live_clock
        current_time = now_ms if now_ms is not None else c.now_ms()
        policy = TradingPolicyService.get_or_create_policy(db)
        eff_policy = effective_policy or resolve_effective_daily_policy(db)
        window = TradingPolicyService.get_ny_session_window(policy, now_ms=current_time, clock=c)
        quota = TradingPolicyService.get_or_create_session_quota(db, policy, now_ms=current_time, clock=c)
        day_audit = crud.get_or_create_today_audit(db, clock=c)

        d_fills = int(getattr(day_audit, "fills_count", 0) or 0)
        ny_fills = int(getattr(quota, "total_fills", 0) or 0)
        daily_max_cap = eff_policy.max_fills
        ny_goal = eff_policy.ny_goal
        target_min_goal = eff_policy.min_goal if daily_min_goal is None else daily_min_goal
        remaining_slots = max(0, daily_max_cap - d_fills)

        # Check NY reservation (Rule 128)
        ny_reserve_active = False
        if (
            not window.get("is_in_window", False)
            and window.get("phase") == "BEFORE_WINDOW"
            and ny_fills < ny_goal
            and remaining_slots <= (ny_goal - ny_fills)
            and bool(eff_policy.reserve_ny_slot)
        ):
            ny_reserve_active = True

        is_blocked = bool(getattr(day_audit, "is_blocked", False))
        if day_audit.cooldown_until and current_time < day_audit.cooldown_until:
            is_blocked = True

        status = "SEARCHING"
        reason_vi = ""

        if is_blocked:
            status = "BLOCKED"
            reason_vi = f"Tạm dừng giao dịch: {day_audit.block_reason or 'Đang trong thời gian cooldown/bảo vệ rủi ro'}"
        elif d_fills >= daily_max_cap:
            status = "CAP_REACHED"
            reason_vi = f"Đã đạt giới hạn tối đa {daily_max_cap} lệnh khớp trong ngày (UTC+7)."
        elif d_fills >= target_min_goal:
            status = "MINIMUM_MET"
            if remaining_slots > 0:
                reason_vi = f"Đã hoàn thành mục tiêu tối thiểu {target_min_goal} lệnh (hiện có {d_fills} fills). Có thể đón nhận thêm tối đa {remaining_slots} cơ hội chất lượng cao."
            else:
                reason_vi = f"Đã đạt mục tiêu tối thiểu {target_min_goal} lệnh và sử dụng hết hạn ngạch ngày."
        elif window.get("phase") == "AFTER_WINDOW" and d_fills < target_min_goal:
            if getattr(policy, "entry_session_policy", "") == "ALL_SESSIONS_SETUP_FIRST":
                # For ALL_SESSIONS_SETUP_FIRST, the trading day ends by daily_timezone bounds, not NY window end (Part 071)
                daily_tz_name = getattr(eff_policy, "trading_day_timezone", "Asia/Ho_Chi_Minh") or "Asia/Ho_Chi_Minh"
                _, _, trade_day_end_ms = resolve_trade_day_bounds(current_time, daily_tz_name)
                if current_time >= trade_day_end_ms:
                    status = "SHORTFALL"
                    reason_vi = f"Kết thúc ngày chưa đạt mục tiêu tối thiểu: {d_fills}/{target_min_goal} lệnh (DAILY_GOAL_SHORTFALL)."
                else:
                    status = "SEARCHING"
                    reason_vi = f"Đang tìm kiếm cơ hội đủ chuẩn (hiện có {d_fills}/{target_min_goal} lệnh, phiên Mỹ đã đóng nhưng ngày giao dịch vẫn tiếp diễn)."
            else:
                status = "SHORTFALL"
                reason_vi = f"Kết thúc ngày chưa đạt mục tiêu tối thiểu: {d_fills}/{target_min_goal} lệnh (DAILY_GOAL_SHORTFALL)."
        else:
            status = "SEARCHING"
            reserve_note = " (1 slot đang được bảo lưu cho phiên Mỹ)" if ny_reserve_active else ""
            reason_vi = f"Đang quét tìm cơ hội hợp lệ: đã khớp {d_fills}/{target_min_goal} lệnh tối thiểu (tối đa {daily_max_cap}){reserve_note}."

        shortfall_breakdown = None
        if status in ("SHORTFALL", "SEARCHING") and d_fills < target_min_goal:
            try:
                sf = summarize_daily_shortfall(db, now_ms=current_time, clock=c, daily_min_goal=target_min_goal)
                shortfall_breakdown = sf.get("category_breakdown")
            except Exception as sf_err:
                logger.debug(f"Failed to compute shortfall breakdown: {sf_err}")
                shortfall_breakdown = None

        return DailyFrequencyGoalEvaluation(
            daily_fills=d_fills,
            daily_min_goal=target_min_goal,
            daily_max_cap=daily_max_cap,
            ny_fills=ny_fills,
            ny_goal=ny_goal,
            status=status,
            reason_vi=reason_vi,
            is_blocked=is_blocked,
            remaining_daily_slots=remaining_slots,
            ny_reserve_active=ny_reserve_active,
            policy_version=eff_policy.policy_version,
            shortfall_breakdown=shortfall_breakdown,
        )


TradingPolicyService.resolve_effective_daily_policy = staticmethod(resolve_effective_daily_policy)


SHORTFALL_CATEGORIES = (
    "data",
    "market_hours",
    "model_confirmation",
    "cost",
    "news",
    "lessons",
    "risk",
    "open_position",
    "execution_error",
)


def categorize_shortfall_reason(code: str) -> str:
    """Categorizes a rejection or shortfall reason code into the 9 canonical V16 categories (Rule 136)."""
    c = str(code).upper()
    if any(k in c for k in ("NEWS", "BLACKOUT")):
        return "news"
    if any(k in c for k in ("LESSON",)):
        return "lessons"
    if any(k in c for k in ("DATA", "QUOTE", "FEED", "STALE", "TICKER")):
        return "data"
    if any(k in c for k in ("SESSION", "MARKET_HOUR", "OUTSIDE_SESSION", "WEEKEND", "RESERVE")):
        return "market_hours"
    if any(k in c for k in ("BREAK", "SWING", "FVG", "CLUSTER", "RECLAIM", "SWEEP", "DISPLACEMENT", "STRUCTURE", "CONFIRM")):
        return "model_confirmation"
    if any(k in c for k in ("SPREAD", "COST", "RR", "STOP", "GEOMETRY")):
        return "cost"
    if any(k in c for k in ("RISK", "LOSS", "CAPITAL", "EQUITY", "CAP", "COOLDOWN")):
        return "risk"
    if any(k in c for k in ("POSITION", "OPEN_POSITION", "ACTIVE_POSITION")):
        return "open_position"
    return "execution_error"


def summarize_daily_shortfall(
    db: Optional[Session] = None,
    *,
    now_ms: Optional[int] = None,
    clock: Optional[Any] = None,
    daily_min_goal: int = 2,
    funnel_records: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """
    V16.1 Rule 136: Categorizes daily opportunity shortfall reasons across:
    data, market_hours, model_confirmation, cost, news, lessons, risk, open_position, execution_error.
    Reports primary cause and count of distinct opportunities impacted.
    """
    c = clock or live_clock
    current_time = now_ms if now_ms is not None else c.now_ms()
    category_counts: Dict[str, int] = {cat: 0 for cat in SHORTFALL_CATEGORIES}
    reason_counts: Dict[str, int] = {}
    distinct_opps_by_cat: Dict[str, set] = {cat: set() for cat in SHORTFALL_CATEGORIES}
    all_distinct_opps: set = set()

    raw_items: List[Dict[str, Any]] = []

    if funnel_records:
        for r in funnel_records:
            if hasattr(r, "to_dict"):
                raw_items.append(r.to_dict())
            elif isinstance(r, dict):
                raw_items.append(r)
    elif db is not None:
        try:
            rows = db.query(models.OpportunityFunnelRecord).filter(
                models.OpportunityFunnelRecord.stage.in_([
                    "GUARD_BLOCKED", "EXPIRED", "INVALIDATED"
                ])
            ).all()
            for row in rows:
                opp_id = row.opportunity_id
                reasons = []
                try:
                    reasons = json.loads(row.reason_codes_json or "[]")
                except Exception:
                    pass
                raw_items.append({"opportunity_id": opp_id, "reason_codes": reasons})
        except Exception as e:
            logger.warning(f"Error querying funnel records for shortfall summary: {e}")

    for item in raw_items:
        opp_id = item.get("opportunity_id") or "unknown_opp"
        all_distinct_opps.add(opp_id)
        codes = item.get("reason_codes", [])
        if not codes:
            codes = ["NO_SMC_SETUP"]
        for code in codes:
            cat = categorize_shortfall_reason(code)
            category_counts[cat] = category_counts.get(cat, 0) + 1
            reason_counts[code] = reason_counts.get(code, 0) + 1
            distinct_opps_by_cat[cat].add(opp_id)

    primary_cat = "model_confirmation"
    max_cat_count = -1
    for cat in SHORTFALL_CATEGORIES:
        cnt = len(distinct_opps_by_cat[cat])
        if cnt > max_cat_count:
            max_cat_count = cnt
            primary_cat = cat

    primary_reason = "NO_SMC_SETUP"
    if reason_counts:
        primary_reason = max(reason_counts.items(), key=lambda kv: kv[1])[0]

    return {
        "has_shortfall": True,
        "primary_category": primary_cat,
        "primary_reason": primary_reason,
        "distinct_opportunities_impacted": len(all_distinct_opps),
        "category_breakdown": {cat: len(distinct_opps_by_cat[cat]) for cat in SHORTFALL_CATEGORIES},
        "reason_breakdown": dict(sorted(reason_counts.items(), key=lambda kv: -kv[1])),
    }


TradingPolicyService.summarize_daily_shortfall = staticmethod(summarize_daily_shortfall)
trading_policy_service = TradingPolicyService()


def apply_runtime_contract_upgrades(
    db: Session,
    *,
    deployment_scope: str = "XAUUSDT",
    now_ms: Optional[int] = None,
) -> UpgradeSummary:
    """PHẦN 008, 083, 084: Bootstrap runtime contract upgrade for target deployment.
    Called before background workers start; idempotent with durable SystemConfig marker and audit.
    """
    res = TradingPolicyService.upgrade_session_policy_contract(
        db,
        target_symbol=deployment_scope,
        migration_id="V14_4_5_ALL_SESSIONS_NY_RESERVE",
        desired_mode="ALL_SESSIONS_WITH_NY_RESERVE",
        now_ms=now_ms,
    )
    return UpgradeSummary(
        changed=res["changed"],
        before_version=res["before_version"],
        after_version=res["after_version"],
        old_mode=res["old_mode"],
        new_mode=res["new_mode"],
        marker=res["marker"],
        audit_id=res.get("audit_id"),
        status=res.get("status", "APPLIED" if res["changed"] else "NOOP"),
    )

evaluate_daily_frequency_goal = TradingPolicyService.evaluate_daily_frequency_goal


def resolve_trade_day_bounds(timestamp_ms: int, timezone_name: str = "Asia/Ho_Chi_Minh") -> Tuple[str, int, int]:
    """
    V16.7 Group J: Resolves canonical trade day string, start_ms, and end_ms
    using standard IANA timezone handling to correctly account for DST (23h or 25h days).
    """
    tz = ZoneInfo(timezone_name)
    sec = timestamp_ms / 1000.0 if timestamp_ms > 1e11 else float(timestamp_ms)
    local_dt = datetime.fromtimestamp(sec, tz=tz)
    local_day = local_dt.date()

    start_local = datetime.combine(
        local_day,
        dtime.min,
        tzinfo=tz,
    )

    end_local = datetime.combine(
        local_day + timedelta(days=1),
        dtime.min,
        tzinfo=tz,
    )

    return (
        local_day.isoformat(),
        int(start_local.timestamp() * 1000),
        int(end_local.timestamp() * 1000),
    )
