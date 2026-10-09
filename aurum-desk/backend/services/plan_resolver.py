import math
import json
import time
from typing import Dict, Any, Optional, Tuple, Union
from domain_calculator import validate_price_geometry

class PlanResolutionError(Exception):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class PlanResolutionDict(dict):
    """
    Subclasses dict to support both dictionary access (['entry'], ['level_source'])
    and attribute access (.resolved_entry, .resolved_sl, .is_valid, .error_code)
    so both domain services and acceptance tests work seamlessly.
    """
    def __getitem__(self, key: str) -> Any:
        if key in self:
            return super().__getitem__(key)
        if key == "geometry_error":
            return self.get("error_message")
        if key == "is_geometry_valid":
            return self.get("is_valid", False)
        return super().__getitem__(key)

    def __getattr__(self, name: str) -> Any:
        if name in self:
            return self[name]
        if name == "resolved_entry":
            return self.get("entry")
        elif name == "resolved_sl":
            return self.get("stop_loss")
        elif name == "resolved_tp":
            return self.get("take_profit")
        elif name == "is_valid":
            return self.get("is_valid", self.get("is_geometry_valid", False))
        elif name == "geometry_error":
            return self.get("geometry_error", self.get("error_message"))
        raise AttributeError(f"'PlanResolutionDict' has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


PlanResolutionResult = PlanResolutionDict


def resolve_plan_levels(
    setup: Any = None,
    custom_entry: Optional[float] = None,
    custom_sl: Optional[float] = None,
    custom_tp: Optional[float] = None,
    direction: Optional[str] = None,
    confirmed_entry: Optional[float] = None,
    confirmed_sl: Optional[float] = None,
    confirmed_tp: Optional[float] = None,
    provisional_entry: Optional[float] = None,
    provisional_sl: Optional[float] = None,
    provisional_tp: Optional[float] = None,
    raise_on_error: bool = False,
) -> PlanResolutionDict:
    """
    Authoritative backend price and level resolver for WatchSetup/PendingOrder.
    Enforces V10.2 Plan Snapshot Invariants:
    1. Direction is mandatory: MUST be 'LONG' or 'SHORT'. Never defaults to 'LONG'.
    2. Atomic level selection: never mixes confirmed entry with provisional SL/TP.
    3. Confirmed levels only valid if all 3 exist and match current direction geometry.
    4. Incomplete or mismatched confirmed sets are rejected with explicit reason codes.
    5. Returns canonical PlanResolutionDict snapshot.
    """
    def _extract(field_name: str, explicit_val: Any) -> Any:
        if explicit_val is not None:
            return explicit_val
        if setup is None:
            return None
        if isinstance(setup, dict):
            return setup.get(field_name)
        return getattr(setup, field_name, None)

    eff_dir = _extract("direction", direction)
    eff_c_entry = _extract("confirmed_entry", confirmed_entry)
    eff_c_sl = _extract("confirmed_sl", confirmed_sl)
    eff_c_tp = _extract("confirmed_tp", confirmed_tp)
    eff_p_entry = _extract("provisional_entry", provisional_entry)
    eff_p_sl = _extract("provisional_sl", provisional_sl)
    eff_p_tp = _extract("provisional_tp", provisional_tp)

    reset_stale = False

    # 1. Mandatory direction check
    if not eff_dir or eff_dir not in ("LONG", "SHORT"):
        err = PlanResolutionError(
            code="UNKNOWN_DIRECTION",
            message=f"Hướng lệnh không hợp lệ hoặc bị thiếu: '{eff_dir}' (Bắt buộc là LONG hoặc SHORT)"
        )
        if raise_on_error:
            raise err
        return PlanResolutionDict(
            is_valid=False,
            is_geometry_valid=False,
            error_code="UNKNOWN_DIRECTION",
            error_message=err.message,
            direction=eff_dir,
            entry=None,
            stop_loss=None,
            take_profit=None,
            level_source="invalid",
            reset_stale_confirmed=False,
        )

    # 2. Custom Draft Levels
    has_custom = any(x is not None for x in (custom_entry, custom_sl, custom_tp))
    if has_custom:
        if custom_entry is None or custom_sl is None or custom_tp is None:
            err = PlanResolutionError(
                code="INCOMPLETE_CUSTOM_LEVELS",
                message="Mức giá tùy chỉnh phải cung cấp đầy đủ cả Entry, Stop Loss và Take Profit"
            )
            if raise_on_error:
                raise err
            return PlanResolutionDict(
                is_valid=False,
                is_geometry_valid=False,
                error_code="INCOMPLETE_CUSTOM_LEVELS",
                error_message=err.message,
                direction=eff_dir,
                entry=None,
                stop_loss=None,
                take_profit=None,
                level_source="invalid",
                reset_stale_confirmed=False,
            )
        entry = float(custom_entry)
        sl = float(custom_sl)
        tp = float(custom_tp)
        level_source = "custom"
    else:
        # 3. Inspect Confirmed Levels
        confirmed_count = sum(1 for x in (eff_c_entry, eff_c_sl, eff_c_tp) if x is not None)
        if confirmed_count in (1, 2):
            err = PlanResolutionError(
                code="CONFIRMED_LEVELS_INCOMPLETE",
                message="Bộ mức giá đã xác nhận bị thiếu trường; không cho phép lai ghép với mức giá dự kiến."
            )
            if raise_on_error:
                raise err
            return PlanResolutionDict(
                is_valid=False,
                is_geometry_valid=False,
                error_code="CONFIRMED_LEVELS_INCOMPLETE",
                error_message=err.message,
                direction=eff_dir,
                entry=None,
                stop_loss=None,
                take_profit=None,
                level_source="invalid",
                reset_stale_confirmed=True,
            )

        if confirmed_count == 3:
            # Check geometry of confirmed levels
            is_c_valid, c_err = validate_price_geometry(eff_dir, eff_c_entry, eff_c_sl, eff_c_tp)
            setup_state = _extract("state", None)

            if setup_state in ("ARMED", "PAPER_OPEN"):
                if not is_c_valid:
                    err = PlanResolutionError(
                        code="INVALID_ARMED_GEOMETRY",
                        message=f"Lệnh đã Arm/Open có hình học giá không hợp lệ: {c_err}"
                    )
                    if raise_on_error:
                        raise err
                    return PlanResolutionDict(
                        is_valid=False,
                        is_geometry_valid=False,
                        error_code="INVALID_ARMED_GEOMETRY",
                        error_message=err.message,
                        direction=eff_dir,
                        entry=None,
                        stop_loss=None,
                        take_profit=None,
                        level_source="invalid",
                        reset_stale_confirmed=True,
                    )
                entry = float(eff_c_entry)
                sl = float(eff_c_sl)
                tp = float(eff_c_tp)
                level_source = "confirmed"
            else:
                if not is_c_valid:
                    # Stale confirmed levels from previous direction or flipped setup!
                    reset_stale = True
                    # If provisional is available, check if provisional is valid
                    if eff_p_entry is not None and eff_p_sl is not None and eff_p_tp is not None:
                        is_p_valid, p_err = validate_price_geometry(eff_dir, eff_p_entry, eff_p_sl, eff_p_tp)
                        if is_p_valid:
                            entry = float(eff_p_entry)
                            sl = float(eff_p_sl)
                            tp = float(eff_p_tp)
                            level_source = "provisional"
                        else:
                            err = PlanResolutionError(
                                code="INVALID_GEOMETRY",
                                message=f"Mức giá không hợp lệ cho hướng {eff_dir}: {c_err or p_err}"
                            )
                            if raise_on_error:
                                raise err
                            return PlanResolutionDict(
                                is_valid=False,
                                is_geometry_valid=False,
                                error_code="INVALID_GEOMETRY",
                                error_message=err.message,
                                direction=eff_dir,
                                entry=None,
                                stop_loss=None,
                                take_profit=None,
                                level_source="invalid",
                                reset_stale_confirmed=True,
                            )
                    else:
                        err = PlanResolutionError(
                            code="INVALID_GEOMETRY",
                            message=f"Mức giá đã xác nhận không hợp lệ cho hướng {eff_dir}: {c_err}"
                        )
                        if raise_on_error:
                            raise err
                        return PlanResolutionDict(
                            is_valid=False,
                            is_geometry_valid=False,
                            error_code="INVALID_GEOMETRY",
                            error_message=err.message,
                            direction=eff_dir,
                            entry=None,
                            stop_loss=None,
                            take_profit=None,
                            level_source="invalid",
                            reset_stale_confirmed=True,
                        )
                else:
                    entry = float(eff_c_entry)
                    sl = float(eff_c_sl)
                    tp = float(eff_c_tp)
                    level_source = "confirmed"
        else:
            # 4. Provisional levels
            if eff_p_entry is None or eff_p_sl is None or eff_p_tp is None:
                err = PlanResolutionError(
                    code="MISSING_PROVISIONAL_LEVELS",
                    message="Không tìm thấy mức giá dự kiến cho setup."
                )
                if raise_on_error:
                    raise err
                return PlanResolutionDict(
                    is_valid=False,
                    is_geometry_valid=False,
                    error_code="MISSING_PROVISIONAL_LEVELS",
                    error_message=err.message,
                    direction=eff_dir,
                    entry=None,
                    stop_loss=None,
                    take_profit=None,
                    level_source="invalid",
                    reset_stale_confirmed=False,
                )
            entry = float(eff_p_entry)
            sl = float(eff_p_sl)
            tp = float(eff_p_tp)
            level_source = "provisional"

    # Strictly check finite and positive numbers
    for name, val in (("Entry", entry), ("Stop Loss", sl), ("Take Profit", tp)):
        if not math.isfinite(val):
            err = PlanResolutionError(code="PRICES_NOT_FINITE", message=f"Mức giá {name} không phải là số hữu hạn: {val}")
            if raise_on_error:
                raise err
            return PlanResolutionDict(
                is_valid=False,
                is_geometry_valid=False,
                error_code="PRICES_NOT_FINITE",
                error_message=err.message,
                direction=eff_dir,
                entry=None,
                stop_loss=None,
                take_profit=None,
                level_source="invalid",
                reset_stale_confirmed=reset_stale,
            )
        if val <= 0:
            err = PlanResolutionError(code="PRICES_MUST_BE_POSITIVE", message=f"Mức giá {name} phải lớn hơn 0: {val}")
            if raise_on_error:
                raise err
            return PlanResolutionDict(
                is_valid=False,
                is_geometry_valid=False,
                error_code="PRICES_MUST_BE_POSITIVE",
                error_message=err.message,
                direction=eff_dir,
                entry=None,
                stop_loss=None,
                take_profit=None,
                level_source="invalid",
                reset_stale_confirmed=reset_stale,
            )

    is_valid, geom_err = validate_price_geometry(eff_dir, entry, sl, tp)
    if not is_valid:
        err = PlanResolutionError(code="INVALID_GEOMETRY", message=geom_err or f"Hình học giá không hợp lệ cho {eff_dir}")
        if raise_on_error:
            raise err
        return PlanResolutionDict(
            is_valid=False,
            is_geometry_valid=False,
            error_code="INVALID_GEOMETRY",
            error_message=err.message,
            direction=eff_dir,
            entry=None,
            stop_loss=None,
            take_profit=None,
            level_source="invalid",
            reset_stale_confirmed=reset_stale or (level_source == "confirmed"),
        )

    now_ms = int(time.time() * 1000)

    return PlanResolutionDict(
        is_valid=True,
        is_geometry_valid=True,
        error_code=None,
        error_message=None,
        setup_id=_extract("id", None),
        setup_instance_id=_extract("setup_instance_id", None),
        revision=_extract("version", 1) or _extract("revision", 1) or 1,
        symbol=_extract("symbol", "XAUUSDT") or "XAUUSDT",
        timeframe=_extract("timeframe", "15M") or "15M",
        direction=eff_dir,
        state=_extract("state", "WATCHING"),
        entry=round(entry, 2),
        stop_loss=round(sl, 2),
        take_profit=round(tp, 2),
        level_source=level_source,
        reset_stale_confirmed=reset_stale,
        quantity=_extract("quantity", 0.05),
        gross_rr=_extract("gross_rr", 0.0),
        net_rr=_extract("net_rr", 0.0),
        risk_usdt=_extract("risk_usdt", 2.5),
        config_version=_extract("config_version", 1),
        conditions_met=_extract("conditions_met", "[]"),
        conditions_remaining=_extract("conditions_remaining", "[]"),
        calculated_at=now_ms,
    )
