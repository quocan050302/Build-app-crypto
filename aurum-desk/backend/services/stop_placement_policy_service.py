"""
backend/services/stop_placement_policy_service.py
AURUM V17.8.0.1 — Policy Resolution and Atomic Persistence for Stop Placement Configuration.
Manages SystemConfig keys under the `stop_placement_v1.*` namespace, including V17.8 feature flags.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.orm import Session

import models
from services.stop_placement_types import StopPlacementMode, StopPlacementPolicy


class StopPolicyConflictError(ValueError):
    """Raised when expected_version does not match current version at DB fence."""
    code = "STALE_STOP_POLICY_VERSION"

    def __init__(self, message: str, fresh_version: Optional[int] = None):
        super().__init__(message)
        self.fresh_version = fresh_version


class StopPolicyValidationError(ValueError):
    """Raised when payload fields fail validation."""
    code = "INVALID_STOP_POLICY_PAYLOAD"


def _parse_strict_persisted_bool(raw_val: Any, default: bool = False) -> bool:
    """
    Strictly parses a persisted boolean configuration value.
    Never enables safety-sensitive flags from numeric strings ('1'), 'yes', or arbitrary strings.
    """
    if type(raw_val) is bool:
        return raw_val
    if isinstance(raw_val, str):
        if raw_val in ("true", "True"):
            return True
        if raw_val in ("false", "False"):
            return False
    return default


def ensure_stop_policy_version_row(db: Session, now_ms: int) -> None:
    """Ensures version row exists for conditional SQL fences without committing."""
    from sqlalchemy import select
    res = db.execute(
        select(models.SystemConfig.value)
        .where(models.SystemConfig.key == "stop_placement_v1.version")
    ).scalar_one_or_none()
    if res is None:
        try:
            db.execute(
                models.SystemConfig.__table__.insert().values(
                    key="stop_placement_v1.version",
                    value="1",
                    updated_at=now_ms,
                )
            )
            db.flush()
        except Exception:
            pass


def resolve_stop_placement_policy(db: Session) -> StopPlacementPolicy:
    """
    Reads authoritative stop placement policy from SystemConfig.
    Queries fresh scalar fields directly to bypass stale ORM entity identity maps.
    Pure read operation: does NOT commit, modify, or insert default rows on GET.
    Defaults to SHADOW_COMPARE (version 1) and all V17.8 flags False if keys are not configured.
    """
    from sqlalchemy import select
    stmt = select(models.SystemConfig.key, models.SystemConfig.value).where(
        models.SystemConfig.key.like("stop_placement_v1.%")
    )
    rows = db.execute(stmt).fetchall()
    config_map = {r[0]: r[1] for r in rows}

    mode = config_map.get("stop_placement_v1.mode", StopPlacementMode.SHADOW_COMPARE.value)
    if mode not in (
        StopPlacementMode.LEGACY.value,
        StopPlacementMode.SHADOW_COMPARE.value,
        StopPlacementMode.PAPER_STRUCTURAL.value,
    ):
        mode = StopPlacementMode.SHADOW_COMPARE.value

    try:
        version = int(config_map.get("stop_placement_v1.version", "1"))
    except (ValueError, TypeError):
        version = 1

    try:
        atr_period = int(config_map.get("stop_placement_v1.atr_period", "14"))
    except (ValueError, TypeError):
        atr_period = 14

    try:
        min_buffer_ticks = int(config_map.get("stop_placement_v1.min_buffer_ticks", "5"))
    except (ValueError, TypeError):
        min_buffer_ticks = 5

    try:
        atr_m1_mult = float(config_map.get("stop_placement_v1.atr_m1_multiplier", "0.25"))
    except (ValueError, TypeError):
        atr_m1_mult = 0.25

    try:
        atr_m5_mult = float(config_map.get("stop_placement_v1.atr_m5_multiplier", "0.10"))
    except (ValueError, TypeError):
        atr_m5_mult = 0.10

    try:
        spread_mult = float(config_map.get("stop_placement_v1.spread_multiplier", "1.50"))
    except (ValueError, TypeError):
        spread_mult = 1.50

    try:
        min_protective_ticks = int(config_map.get("stop_placement_v1.min_protective_ticks", "10"))
    except (ValueError, TypeError):
        min_protective_ticks = 10

    try:
        min_dist_m1_mult = float(config_map.get("stop_placement_v1.min_distance_atr_m1_mult", "0.50"))
    except (ValueError, TypeError):
        min_dist_m1_mult = 0.50

    try:
        min_dist_m5_mult = float(config_map.get("stop_placement_v1.min_distance_atr_m5_mult", "0.20"))
    except (ValueError, TypeError):
        min_dist_m5_mult = 0.20

    try:
        min_dist_spread_mult = float(config_map.get("stop_placement_v1.min_distance_spread_mult", "2.0"))
    except (ValueError, TypeError):
        min_dist_spread_mult = 2.0

    try:
        max_stop_dist_atr = float(config_map.get("stop_placement_v1.max_stop_distance_atr", "4.0"))
    except (ValueError, TypeError):
        max_stop_dist_atr = 4.0

    try:
        max_spread_usd = float(config_map.get("stop_placement_v1.max_spread_usd", "2.0"))
    except (ValueError, TypeError):
        max_spread_usd = 2.0

    algo_version = config_map.get("stop_placement_v1.algorithm_version", "V17_8_0")

    v18_struct_enabled = _parse_strict_persisted_bool(
        config_map.get("stop_placement_v1.v18_structural_intelligence_enabled"), default=False
    )
    v18_unicorn_enabled = _parse_strict_persisted_bool(
        config_map.get("stop_placement_v1.v18_unicorn_detection_enabled"), default=False
    )
    v18_noise_enabled = _parse_strict_persisted_bool(
        config_map.get("stop_placement_v1.v18_noise_research_enabled"), default=False
    )
    try:
        raw_v18_ver = config_map.get("stop_placement_v1.v18_execution_policy_version", "1")
        v18_exec_ver = int(raw_v18_ver)
        if v18_exec_ver < 1:
            v18_exec_ver = 1
    except (ValueError, TypeError):
        v18_exec_ver = 1

    return StopPlacementPolicy(
        mode=mode,
        version=version,
        atr_period=atr_period,
        atr_method="SMA_TR_V1",
        min_buffer_ticks=min_buffer_ticks,
        atr_m1_multiplier=atr_m1_mult,
        atr_m5_multiplier=atr_m5_mult,
        spread_multiplier=spread_mult,
        application_scope="NEW_PAPER_ORDERS",
        min_protective_ticks=min_protective_ticks,
        min_distance_atr_m1_mult=min_dist_m1_mult,
        min_distance_atr_m5_mult=min_dist_m5_mult,
        min_distance_spread_mult=min_dist_spread_mult,
        max_stop_distance_atr=max_stop_dist_atr,
        max_spread_usd=max_spread_usd,
        algorithm_version=algo_version,
        v18_structural_intelligence_enabled=v18_struct_enabled,
        v18_unicorn_detection_enabled=v18_unicorn_enabled,
        v18_noise_research_enabled=v18_noise_enabled,
        v18_execution_policy_version=v18_exec_ver,
    )


def validate_stop_placement_policy(
    payload: Dict[str, Any],
    current: StopPlacementPolicy,
) -> Dict[str, Any]:
    """
    Validates updated policy values strictly.
    Rejects booleans for integer fields, strings/numbers/nulls for boolean feature flags,
    non-finite floats, negative values, and unknown modes.
    """
    validated: Dict[str, Any] = {}

    if "mode" in payload:
        mode_val = payload["mode"]
        valid_modes = {m.value for m in StopPlacementMode}
        if mode_val not in valid_modes:
            raise StopPolicyValidationError(f"Chế độ đặt SL không hợp lệ: {mode_val}. Hợp lệ: {valid_modes}")
        validated["mode"] = mode_val
    else:
        validated["mode"] = current.mode

    if "min_buffer_ticks" in payload:
        raw = payload["min_buffer_ticks"]
        if type(raw) is not int or raw < 0:
            raise StopPolicyValidationError("min_buffer_ticks phải là số nguyên không âm.")
        validated["min_buffer_ticks"] = raw
    else:
        validated["min_buffer_ticks"] = current.min_buffer_ticks

    if "min_protective_ticks" in payload:
        raw = payload["min_protective_ticks"]
        if type(raw) is not int or raw < 0:
            raise StopPolicyValidationError("min_protective_ticks phải là số nguyên không âm.")
        validated["min_protective_ticks"] = raw
    else:
        validated["min_protective_ticks"] = current.min_protective_ticks

    if "atr_period" in payload:
        raw = payload["atr_period"]
        if type(raw) is not int or raw <= 0:
            raise StopPolicyValidationError("atr_period phải là số nguyên dương (> 0).")
        validated["atr_period"] = raw
    else:
        validated["atr_period"] = current.atr_period

    for f_name, cur_val in (
        ("atr_m1_multiplier", current.atr_m1_multiplier),
        ("atr_m5_multiplier", current.atr_m5_multiplier),
        ("spread_multiplier", current.spread_multiplier),
        ("min_distance_atr_m1_mult", current.min_distance_atr_m1_mult),
        ("min_distance_atr_m5_mult", current.min_distance_atr_m5_mult),
        ("min_distance_spread_mult", current.min_distance_spread_mult),
        ("max_stop_distance_atr", current.max_stop_distance_atr),
        ("max_spread_usd", current.max_spread_usd),
    ):
        if f_name in payload:
            raw = payload[f_name]
            if type(raw) is bool or not isinstance(raw, (int, float)):
                raise StopPolicyValidationError(f"{f_name} phải là số thực không âm.")
            val = float(raw)
            if not math.isfinite(val) or val < 0.0:
                raise StopPolicyValidationError(f"{f_name} phải là số thực hữu hạn không âm.")
            validated[f_name] = val
        else:
            validated[f_name] = cur_val

    if "algorithm_version" in payload:
        raw_algo = payload["algorithm_version"]
        if not isinstance(raw_algo, str) or not raw_algo.strip():
            raise StopPolicyValidationError("algorithm_version phải là chuỗi không rỗng.")
        validated["algorithm_version"] = raw_algo.strip()
    else:
        validated["algorithm_version"] = current.algorithm_version

    # Strict boolean validation for V17.8 feature flags
    for flag_name, cur_flag in (
        ("v18_structural_intelligence_enabled", current.v18_structural_intelligence_enabled),
        ("v18_unicorn_detection_enabled", current.v18_unicorn_detection_enabled),
        ("v18_noise_research_enabled", current.v18_noise_research_enabled),
    ):
        if flag_name in payload:
            raw_flag = payload[flag_name]
            if type(raw_flag) is not bool:
                raise StopPolicyValidationError(
                    f"{flag_name} phải là kiểu boolean nguyên bản (true/false), không chấp nhận chuỗi, số hoặc null."
                )
            validated[flag_name] = raw_flag
        else:
            validated[flag_name] = bool(cur_flag)

    if "v18_execution_policy_version" in payload:
        raw_v18_ver = payload["v18_execution_policy_version"]
        if type(raw_v18_ver) is not int or raw_v18_ver < 1:
            raise StopPolicyValidationError("v18_execution_policy_version phải là số nguyên dương (>= 1).")
        validated["v18_execution_policy_version"] = raw_v18_ver
    else:
        validated["v18_execution_policy_version"] = int(current.v18_execution_policy_version)

    for scope_key in ("scope", "application_scope"):
        if scope_key in payload:
            raw_scope = str(payload[scope_key] or "").upper()
            if raw_scope not in ("PAPER", "NEW_PAPER_ORDERS"):
                raise StopPolicyValidationError(
                    f"{scope_key}={payload[scope_key]} bị từ chối: chính sách SL chỉ được phép trên PAPER."
                )

    return validated


def update_stop_placement_policy(
    db: Session,
    payload: Dict[str, Any],
    *,
    expected_version: int,
    now_ms: Optional[int] = None,
    server_scope: Optional[str] = None,
) -> Tuple[StopPlacementPolicy, bool]:
    """
    Atomically updates stop placement policy with CAS version fence at the DB level.
    Returns (updated_policy, is_mutation).
    True no-op returns (current_policy, False) without incrementing version or updating timestamps.
    """
    import time
    from sqlalchemy import select, update
    from services.entry_session_policy_service import resolve_server_execution_scope

    t_now = now_ms if now_ms is not None else int(time.time() * 1000)

    # Validate version argument strictly
    if type(expected_version) is not int or expected_version < 1:
        raise StopPolicyValidationError("expected_version phải là số nguyên dương (>= 1).")

    # Bootstrap version row if absent
    ensure_stop_policy_version_row(db, t_now)

    # 1. Acquire DB CAS fence: conditional UPDATE where key and value match expected_version
    fence_res = db.execute(
        update(models.SystemConfig)
        .where(models.SystemConfig.key == "stop_placement_v1.version")
        .where(models.SystemConfig.value == str(expected_version))
        .values(value=str(expected_version))
        .execution_options(synchronize_session=False)
    )
    if fence_res.rowcount != 1:
        actual_row = db.execute(
            select(models.SystemConfig.value)
            .where(models.SystemConfig.key == "stop_placement_v1.version")
        ).scalar_one_or_none()
        actual_ver = actual_row if actual_row is not None else "1"
        fresh_v = int(actual_ver) if str(actual_ver).isdigit() else 1
        raise StopPolicyConflictError(
            f"Phiên bản cấu hình không khớp: hiện tại tại DB là {actual_ver}, client gửi {expected_version}.",
            fresh_version=fresh_v,
        )

    # Read current state freshly from DB scalars
    current = resolve_stop_placement_policy(db)
    validated = validate_stop_placement_policy(payload, current)

    # Server execution scope gate (LIVE trading prohibited for PAPER_STRUCTURAL and V17.8 structural execution)
    if (
        validated["mode"] == StopPlacementMode.PAPER_STRUCTURAL.value
        or validated["v18_structural_intelligence_enabled"] is True
        or validated["v18_unicorn_detection_enabled"] is True
    ):
        effective_scope = server_scope if server_scope is not None else resolve_server_execution_scope(db)
        if str(effective_scope).upper() == "LIVE":
            raise StopPolicyValidationError(
                "Chế độ PAPER_STRUCTURAL và tính năng V17.8 chỉ được phép áp dụng trên môi trường PAPER (LIVE bị cấm)."
            )

    # Check for true no-op across all persisted fields including V17.8 feature flags
    is_noop = (
        validated["mode"] == current.mode
        and validated["min_buffer_ticks"] == current.min_buffer_ticks
        and validated["min_protective_ticks"] == current.min_protective_ticks
        and validated["atr_period"] == current.atr_period
        and abs(validated["atr_m1_multiplier"] - current.atr_m1_multiplier) < 1e-6
        and abs(validated["atr_m5_multiplier"] - current.atr_m5_multiplier) < 1e-6
        and abs(validated["spread_multiplier"] - current.spread_multiplier) < 1e-6
        and abs(validated["min_distance_atr_m1_mult"] - current.min_distance_atr_m1_mult) < 1e-6
        and abs(validated["min_distance_atr_m5_mult"] - current.min_distance_atr_m5_mult) < 1e-6
        and abs(validated["min_distance_spread_mult"] - current.min_distance_spread_mult) < 1e-6
        and abs(validated["max_stop_distance_atr"] - current.max_stop_distance_atr) < 1e-6
        and abs(validated["max_spread_usd"] - current.max_spread_usd) < 1e-6
        and validated["algorithm_version"] == current.algorithm_version
        and validated["v18_structural_intelligence_enabled"] == current.v18_structural_intelligence_enabled
        and validated["v18_unicorn_detection_enabled"] == current.v18_unicorn_detection_enabled
        and validated["v18_noise_research_enabled"] == current.v18_noise_research_enabled
        and validated["v18_execution_policy_version"] == current.v18_execution_policy_version
    )
    if is_noop:
        return current, False

    new_version = current.version + 1

    def _upsert_scalar(k: str, val: str):
        res = db.execute(
            update(models.SystemConfig)
            .where(models.SystemConfig.key == k)
            .values(value=val, updated_at=t_now)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount == 0:
            db.execute(
                models.SystemConfig.__table__.insert().values(
                    key=k,
                    value=val,
                    updated_at=t_now,
                )
            )

    _upsert_scalar("stop_placement_v1.mode", validated["mode"])
    _upsert_scalar("stop_placement_v1.version", str(new_version))
    _upsert_scalar("stop_placement_v1.min_buffer_ticks", str(validated["min_buffer_ticks"]))
    _upsert_scalar("stop_placement_v1.min_protective_ticks", str(validated["min_protective_ticks"]))
    _upsert_scalar("stop_placement_v1.atr_period", str(validated["atr_period"]))
    _upsert_scalar("stop_placement_v1.atr_m1_multiplier", str(validated["atr_m1_multiplier"]))
    _upsert_scalar("stop_placement_v1.atr_m5_multiplier", str(validated["atr_m5_multiplier"]))
    _upsert_scalar("stop_placement_v1.spread_multiplier", str(validated["spread_multiplier"]))
    _upsert_scalar("stop_placement_v1.min_distance_atr_m1_mult", str(validated["min_distance_atr_m1_mult"]))
    _upsert_scalar("stop_placement_v1.min_distance_atr_m5_mult", str(validated["min_distance_atr_m5_mult"]))
    _upsert_scalar("stop_placement_v1.min_distance_spread_mult", str(validated["min_distance_spread_mult"]))
    _upsert_scalar("stop_placement_v1.max_stop_distance_atr", str(validated["max_stop_distance_atr"]))
    _upsert_scalar("stop_placement_v1.max_spread_usd", str(validated["max_spread_usd"]))
    _upsert_scalar("stop_placement_v1.algorithm_version", validated["algorithm_version"])
    _upsert_scalar(
        "stop_placement_v1.v18_structural_intelligence_enabled",
        "true" if validated["v18_structural_intelligence_enabled"] else "false",
    )
    _upsert_scalar(
        "stop_placement_v1.v18_unicorn_detection_enabled",
        "true" if validated["v18_unicorn_detection_enabled"] else "false",
    )
    _upsert_scalar(
        "stop_placement_v1.v18_noise_research_enabled",
        "true" if validated["v18_noise_research_enabled"] else "false",
    )
    _upsert_scalar(
        "stop_placement_v1.v18_execution_policy_version",
        str(validated["v18_execution_policy_version"]),
    )

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise e

    updated = StopPlacementPolicy(
        mode=validated["mode"],
        version=new_version,
        atr_period=validated["atr_period"],
        atr_method="SMA_TR_V1",
        min_buffer_ticks=validated["min_buffer_ticks"],
        atr_m1_multiplier=validated["atr_m1_multiplier"],
        atr_m5_multiplier=validated["atr_m5_multiplier"],
        spread_multiplier=validated["spread_multiplier"],
        application_scope="NEW_PAPER_ORDERS",
        min_protective_ticks=validated["min_protective_ticks"],
        min_distance_atr_m1_mult=validated["min_distance_atr_m1_mult"],
        min_distance_atr_m5_mult=validated["min_distance_atr_m5_mult"],
        min_distance_spread_mult=validated["min_distance_spread_mult"],
        max_stop_distance_atr=validated["max_stop_distance_atr"],
        max_spread_usd=validated["max_spread_usd"],
        algorithm_version=validated["algorithm_version"],
        v18_structural_intelligence_enabled=validated["v18_structural_intelligence_enabled"],
        v18_unicorn_detection_enabled=validated["v18_unicorn_detection_enabled"],
        v18_noise_research_enabled=validated["v18_noise_research_enabled"],
        v18_execution_policy_version=validated["v18_execution_policy_version"],
    )
    return updated, True
