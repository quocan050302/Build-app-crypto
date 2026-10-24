"""
backend/services/stop_placement_policy_service.py
AURUM V17.5 — Policy Resolution and Atomic Persistence for Stop Placement Configuration.
Manages SystemConfig keys under the `stop_placement_v1.*` namespace.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

import models
from services.stop_placement_types import StopPlacementMode, StopPlacementPolicy


def resolve_stop_placement_policy(db: Session) -> StopPlacementPolicy:
    """
    Reads authoritative stop placement policy from SystemConfig.
    Pure read operation: does NOT commit, modify, or insert default rows on GET.
    Defaults to SHADOW_COMPARE (version 1) if keys are not yet configured.
    """
    rows = db.query(models.SystemConfig).filter(
        models.SystemConfig.key.like("stop_placement_v1.%")
    ).all()
    config_map = {r.key: r.value for r in rows}

    mode = config_map.get("stop_placement_v1.mode", StopPlacementMode.SHADOW_COMPARE.value)
    if mode not in (StopPlacementMode.LEGACY.value, StopPlacementMode.SHADOW_COMPARE.value, StopPlacementMode.PAPER_STRUCTURAL.value):
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
    )


def validate_stop_placement_policy(
    payload: Dict[str, Any],
    current: StopPlacementPolicy,
) -> Dict[str, Any]:
    """
    Validates updated policy values strictly.
    Rejects booleans for integer fields, non-finite floats, negative values, and unknown modes.
    """
    validated: Dict[str, Any] = {}

    if "mode" in payload:
        mode_val = payload["mode"]
        valid_modes = {m.value for m in StopPlacementMode}
        if mode_val not in valid_modes:
            raise ValueError(f"Chế độ đặt SL không hợp lệ: {mode_val}. Hợp lệ: {valid_modes}")
        validated["mode"] = mode_val
    else:
        validated["mode"] = current.mode

    if "min_buffer_ticks" in payload:
        raw = payload["min_buffer_ticks"]
        if type(raw) is not int or raw < 0:
            raise ValueError("min_buffer_ticks phải là số nguyên không âm.")
        validated["min_buffer_ticks"] = raw
    else:
        validated["min_buffer_ticks"] = current.min_buffer_ticks

    if "atr_period" in payload:
        raw = payload["atr_period"]
        if type(raw) is not int or raw <= 0:
            raise ValueError("atr_period phải là số nguyên dương (> 0).")
        validated["atr_period"] = raw
    else:
        validated["atr_period"] = current.atr_period

    for f_name, cur_val in (
        ("atr_m1_multiplier", current.atr_m1_multiplier),
        ("atr_m5_multiplier", current.atr_m5_multiplier),
        ("spread_multiplier", current.spread_multiplier),
    ):
        if f_name in payload:
            raw = payload[f_name]
            if type(raw) is bool or not isinstance(raw, (int, float)):
                raise ValueError(f"{f_name} phải là số thực không âm.")
            val = float(raw)
            if not math.isfinite(val) or val < 0.0:
                raise ValueError(f"{f_name} phải là số thực hữu hạn không âm.")
            validated[f_name] = val
        else:
            validated[f_name] = cur_val

    return validated


def _upsert_system_config_no_commit(db: Session, key: str, value: str, now_ms: int) -> None:
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


def update_stop_placement_policy(
    db: Session,
    payload: Dict[str, Any],
    *,
    expected_version: int,
    now_ms: Optional[int] = None,
    server_scope: Optional[str] = None,
) -> Tuple[StopPlacementPolicy, bool]:
    """
    Atomically updates stop placement policy with CAS version fence.
    Returns (updated_policy, is_mutation).
    True no-op returns (current_policy, False) without incrementing version or updating timestamps.
    """
    import time
    from services.entry_session_policy_service import resolve_server_execution_scope

    t_now = now_ms if now_ms is not None else int(time.time() * 1000)
    current = resolve_stop_placement_policy(db)

    # Validate version strictly
    if type(expected_version) is not int:
        raise ValueError("expected_version phải là số nguyên.")
    if expected_version != current.version:
        raise ValueError(
            f"Phiên bản cấu hình không khớp: hiện tại là {current.version}, client gửi {expected_version}."
        )

    validated = validate_stop_placement_policy(payload, current)

    # Server execution scope gate
    if validated["mode"] == StopPlacementMode.PAPER_STRUCTURAL.value:
        effective_scope = server_scope if server_scope is not None else resolve_server_execution_scope(db)
        if effective_scope == "LIVE":
            raise ValueError(
                "Chế độ PAPER_STRUCTURAL chỉ được phép áp dụng trên môi trường PAPER (máy chủ hiện là LIVE)."
            )

    # Check for true no-op
    is_noop = (
        validated["mode"] == current.mode
        and validated["min_buffer_ticks"] == current.min_buffer_ticks
        and validated["atr_period"] == current.atr_period
        and abs(validated["atr_m1_multiplier"] - current.atr_m1_multiplier) < 1e-6
        and abs(validated["atr_m5_multiplier"] - current.atr_m5_multiplier) < 1e-6
        and abs(validated["spread_multiplier"] - current.spread_multiplier) < 1e-6
    )
    if is_noop:
        return current, False

    new_version = current.version + 1
    _upsert_system_config_no_commit(db, "stop_placement_v1.mode", validated["mode"], t_now)
    _upsert_system_config_no_commit(db, "stop_placement_v1.version", str(new_version), t_now)
    _upsert_system_config_no_commit(db, "stop_placement_v1.min_buffer_ticks", str(validated["min_buffer_ticks"]), t_now)
    _upsert_system_config_no_commit(db, "stop_placement_v1.atr_period", str(validated["atr_period"]), t_now)
    _upsert_system_config_no_commit(db, "stop_placement_v1.atr_m1_multiplier", str(validated["atr_m1_multiplier"]), t_now)
    _upsert_system_config_no_commit(db, "stop_placement_v1.atr_m5_multiplier", str(validated["atr_m5_multiplier"]), t_now)
    _upsert_system_config_no_commit(db, "stop_placement_v1.spread_multiplier", str(validated["spread_multiplier"]), t_now)

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
    )
    return updated, True
