"""
backend/services/rr_policy_service.py
V16.39.15 - Authoritative Shared Risk:Reward Policy Service

Provides pure validation, resolution, and staging for min_net_rr configuration:
- Resolves effective policy priority: SystemConfig > TradingPolicy > Default 2.0.
- Rejects malformed, non-finite, negative, or boolean values.
- Enforces min_net_rr >= 1.5 in PAPER mode.
- Computes deterministic configuration fingerprints.
- Stages atomic updates without premature commit.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from sqlalchemy.orm import Session


class RrPolicyConfigError(ValueError):
    """Raised when persisted or input min_net_rr configuration is invalid."""
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class RrPolicySource(str, Enum):
    SYSTEM_CONFIG = "SYSTEM_CONFIG"
    LEGACY_TRADING_POLICY = "LEGACY_TRADING_POLICY"
    ORDER_SNAPSHOT = "ORDER_SNAPSHOT"
    DEFAULT = "DEFAULT"


DEFAULT_MIN_NET_RR = 2.0
MIN_SUPPORTED_NET_RR = 1.5


@dataclass(frozen=True)
class RrPolicyContext:
    min_net_rr: float
    source: str
    fingerprint: str
    as_of_ms: Optional[int] = None


def compute_rr_policy_fingerprint(min_net_rr: float) -> str:
    """Computes a stable deterministic 8-char fingerprint from normalized min_net_rr."""
    normalized = f"{float(min_net_rr):.4f}"
    return hashlib.sha256(f"rr_policy_v1_{normalized}".encode("utf-8")).hexdigest()[:8]


def validate_new_min_net_rr(raw: Any) -> float:
    """
    Validates input min_net_rr from HTTP payload or user settings:
    - Rejects None or boolean (bool is subclass of int in Python!).
    - Must be a finite number >= 1.5.
    - Returns float.
    """
    if raw is None:
        raise RrPolicyConfigError(
            "INVALID_VALUE",
            "Min Net R:R không được để trống (null)."
        )

    if isinstance(raw, bool):
        raise RrPolicyConfigError(
            "INVALID_TYPE",
            "Min Net R:R phải là số hữu hạn, không được truyền giá trị boolean."
        )

    try:
        val = float(raw)
    except (TypeError, ValueError):
        raise RrPolicyConfigError(
            "INVALID_VALUE",
            f"Min Net R:R không phải là số hợp lệ: {raw!r}"
        )

    if not math.isfinite(val):
        raise RrPolicyConfigError(
            "INVALID_VALUE",
            "Min Net R:R phải là số hữu hạn (không chấp nhận NaN hoặc Infinity)."
        )

    if val < MIN_SUPPORTED_NET_RR:
        raise RrPolicyConfigError(
            "RR_BELOW_MINIMUM_ALLOWED",
            f"Min Net R:R phải là số hữu hạn từ {MIN_SUPPORTED_NET_RR:.1f} trở lên trong chế độ PAPER hiện tại."
        )

    return round(val, 4)


def parse_persisted_min_net_rr(raw: Any) -> Optional[float]:
    """
    Parses persisted min_net_rr string/float from DB:
    - If raw is None, returns None (indicates absent key for fallback).
    - If raw is boolean or invalid string/number/NaN/Inf or < 1.0, raises RrPolicyConfigError.
    """
    if raw is None:
        return None

    if isinstance(raw, bool):
        raise RrPolicyConfigError(
            "INVALID_PERSISTED_TYPE",
            f"Giá trị cấu hình min_net_rr lưu trong DB là boolean: {raw!r}"
        )

    if isinstance(raw, str):
        raw_str = raw.strip()
        if not raw_str:
            raise RrPolicyConfigError(
                "INVALID_PERSISTED_EMPTY",
                "Giá trị cấu hình min_net_rr lưu trong DB là chuỗi rỗng."
            )
        try:
            val = float(raw_str)
        except ValueError:
            raise RrPolicyConfigError(
                "INVALID_PERSISTED_VALUE",
                f"Giá trị cấu hình min_net_rr lưu trong DB không thể chuyển thành số: {raw!r}"
            )
    else:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            raise RrPolicyConfigError(
                "INVALID_PERSISTED_VALUE",
                f"Giá trị cấu hình min_net_rr lưu trong DB không hợp lệ: {raw!r}"
            )

    if not math.isfinite(val):
        raise RrPolicyConfigError(
            "INVALID_PERSISTED_NON_FINITE",
            "Giá trị cấu hình min_net_rr lưu trong DB là NaN hoặc Infinity."
        )

    if val < MIN_SUPPORTED_NET_RR:
        raise RrPolicyConfigError(
            "INVALID_PERSISTED_BELOW_FLOOR",
            f"Giá trị cấu hình min_net_rr lưu trong DB ({val}) thấp hơn sàn hỗ trợ ({MIN_SUPPORTED_NET_RR})."
        )

    return round(val, 4)


def resolve_effective_rr_policy(db: Optional[Session]) -> RrPolicyContext:
    """
    Pure read-only resolver for authoritative min_net_rr:
    1. Checks SystemConfig for canonical key 'min_net_rr'.
       - If present and valid, returns RrPolicyContext with source=SYSTEM_CONFIG.
       - If present and malformed, raises RrPolicyConfigError (no silent fallback!).
    2. If SystemConfig key is absent, checks TradingPolicy 'default' row.
       - If present and valid, returns RrPolicyContext with source=LEGACY_TRADING_POLICY.
       - If present and malformed, raises RrPolicyConfigError.
    3. If both sources are absent, returns default 2.0 with source=DEFAULT.
    """
    if db is None:
        fp = compute_rr_policy_fingerprint(DEFAULT_MIN_NET_RR)
        return RrPolicyContext(
            min_net_rr=DEFAULT_MIN_NET_RR,
            source=RrPolicySource.DEFAULT.value,
            fingerprint=fp,
            as_of_ms=None,
        )

    import models

    # 1. Check SystemConfig
    sys_row = db.query(models.SystemConfig).filter(models.SystemConfig.key == "min_net_rr").first()
    if sys_row is not None and sys_row.value is not None:
        parsed_val = parse_persisted_min_net_rr(sys_row.value)
        if parsed_val is not None:
            fp = compute_rr_policy_fingerprint(parsed_val)
            return RrPolicyContext(
                min_net_rr=parsed_val,
                source=RrPolicySource.SYSTEM_CONFIG.value,
                fingerprint=fp,
                as_of_ms=sys_row.updated_at,
            )

    # 2. Fallback to TradingPolicy
    pol_row = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()
    if pol_row is not None and pol_row.min_net_rr is not None:
        parsed_pol_val = parse_persisted_min_net_rr(pol_row.min_net_rr)
        if parsed_pol_val is not None:
            fp = compute_rr_policy_fingerprint(parsed_pol_val)
            return RrPolicyContext(
                min_net_rr=parsed_pol_val,
                source=RrPolicySource.LEGACY_TRADING_POLICY.value,
                fingerprint=fp,
                as_of_ms=pol_row.updated_at,
            )

    # 3. Default fallback
    fp = compute_rr_policy_fingerprint(DEFAULT_MIN_NET_RR)
    return RrPolicyContext(
        min_net_rr=DEFAULT_MIN_NET_RR,
        source=RrPolicySource.DEFAULT.value,
        fingerprint=fp,
        as_of_ms=None,
    )


def stage_min_net_rr_update(
    db: Session,
    min_net_rr: Any,
    now_ms: Optional[int] = None
) -> RrPolicyContext:
    """
    Stages canonical SystemConfig and compatibility TradingPolicy min_net_rr in the given session.
    Does NOT call db.commit() or db.rollback() - caller owns transaction boundary.
    """
    import time
    import models

    val = validate_new_min_net_rr(min_net_rr)
    current_time = now_ms if now_ms is not None else int(time.time() * 1000)

    # 1. Stage SystemConfig canonical row
    cfg_row = db.query(models.SystemConfig).filter(models.SystemConfig.key == "min_net_rr").first()
    if cfg_row:
        cfg_row.value = str(val)
        cfg_row.updated_at = current_time
    else:
        db.add(models.SystemConfig(key="min_net_rr", value=str(val), updated_at=current_time))

    # 2. Stage TradingPolicy compatibility row
    pol_row = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()
    if pol_row:
        pol_row.min_net_rr = val
        pol_row.updated_at = current_time
        pol_row.version = (pol_row.version or 1) + 1
    else:
        # Create default policy with this min_net_rr
        from policy_constants import get_default_policy_values
        defaults = dict(get_default_policy_values())
        defaults["min_net_rr"] = val
        defaults["updated_at"] = current_time
        pol_row = models.TradingPolicy(**defaults)
        db.add(pol_row)

    fp = compute_rr_policy_fingerprint(val)
    return RrPolicyContext(
        min_net_rr=val,
        source=RrPolicySource.SYSTEM_CONFIG.value,
        fingerprint=fp,
        as_of_ms=current_time,
    )
