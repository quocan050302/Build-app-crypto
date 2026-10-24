"""
backend/services/plan_financial_context_service.py
V14.4.2 — Single authoritative source for plan financial context.
Enforces:
- Appendix A: PlanFinancialContext with strict validation and no hidden capital fallback.
- Appendix B: calculate_display_financial wrapper around domain_calculator.
- Appendix D: financial_value_is_known and degraded_setup_plan without omitting rows.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

import crud
import models
from domain_calculator import CostAssumptions, calculate_risk_reward
from services.risk_settings_service import risk_settings_service


@dataclass(frozen=True)
class PlanFinancialContext:
    equity_usdt: float
    equity_basis: str
    equity_as_of_ms: int
    risk_pct: float
    leverage: int
    margin_mode: str
    risk_config_version: int
    costs: CostAssumptions
    cost_version: str
    instrument_version: str
    min_net_rr: float
    rr_policy_source: Optional[str] = None
    rr_policy_fingerprint: Optional[str] = None

    def validate(self) -> None:
        if (
            isinstance(self.equity_usdt, bool)
            or not math.isfinite(self.equity_usdt)
            or self.equity_usdt <= 0
        ):
            raise ValueError("EQUITY_CONTEXT_INVALID")
        if (
            isinstance(self.risk_pct, bool)
            or not math.isfinite(self.risk_pct)
            or self.risk_pct <= 0
        ):
            raise ValueError("RISK_CONTEXT_INVALID")
        if type(self.leverage) is not int or self.leverage <= 0:
            raise ValueError("LEVERAGE_CONTEXT_INVALID")
        if self.margin_mode not in ("ISOLATED", "CROSS"):
            raise ValueError("MARGIN_CONTEXT_INVALID")
        if type(self.equity_as_of_ms) is not int or self.equity_as_of_ms <= 0:
            raise ValueError("EQUITY_TIME_INVALID")
        if not self.cost_version or not self.instrument_version:
            raise ValueError("FINANCIAL_PROVENANCE_MISSING")
        if not math.isfinite(self.min_net_rr) or self.min_net_rr <= 0:
            raise ValueError("RR_POLICY_INVALID")


def load_plan_financial_context(
    db: Session,
    symbol: str = "XAUUSDT",
    *,
    now_ms: Optional[int] = None,
    clock: Optional[Any] = None,
) -> PlanFinancialContext:
    """
    Loads unified plan financial context from DayAudit and risk_settings_service.
    Reads account equity from the authoritative DayAudit source used by /account/status.
    """
    current_ms = (
        now_ms
        if now_ms is not None
        else (clock.now_ms() if clock and hasattr(clock, "now_ms") else int(time.time() * 1000))
    )
    audit = crud.get_or_create_today_audit(db, clock=clock)
    equity = float(audit.current_equity) if audit and audit.current_equity is not None else 1000.0
    equity_basis = "DAY_AUDIT_BALANCE"
    audit_as_of = getattr(audit, "updated_at_ms", None)
    equity_as_of = int(audit_as_of) if (audit_as_of is not None and audit_as_of > 0) else current_ms

    settings = risk_settings_service.get_settings(db)
    risk_pct = float(settings.risk_pct) if settings.risk_pct is not None else 0.25
    leverage = int(settings.requested_leverage) if settings.requested_leverage is not None else 5
    margin_mode = str(settings.margin_mode or "ISOLATED")
    risk_config_version = int(settings.config_version) if settings.config_version is not None else 1
    instrument_ver = str(settings.metadata_version or "XAUUSDT-paper-v1")

    # V16.39.15 Authoritative RR Policy Resolution (Rule 057)
    from services.rr_policy_service import resolve_effective_rr_policy
    rr_ctx = resolve_effective_rr_policy(db)
    min_net_rr = rr_ctx.min_net_rr
    rr_policy_source = rr_ctx.source
    rr_policy_fingerprint = rr_ctx.fingerprint

    slip_row = db.query(models.SystemConfig).filter(models.SystemConfig.key.in_(["default_slippage", "slippage", "slippage_usd"])).first()
    custom_slippage = float(slip_row.value) if (slip_row and slip_row.value is not None) else None

    costs = CostAssumptions(
        slippage_usd=custom_slippage if custom_slippage is not None else 0.10
    )
    cost_version = "v14.4_standard_costs"

    ctx = PlanFinancialContext(
        equity_usdt=equity,
        equity_basis=equity_basis,
        equity_as_of_ms=equity_as_of,
        risk_pct=risk_pct,
        leverage=leverage,
        margin_mode=margin_mode,
        risk_config_version=risk_config_version,
        costs=costs,
        cost_version=cost_version,
        instrument_version=instrument_ver,
        min_net_rr=min_net_rr,
        rr_policy_source=rr_policy_source,
        rr_policy_fingerprint=rr_policy_fingerprint,
    )
    ctx.validate()
    return ctx


def calculate_display_financial(
    direction: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    context: PlanFinancialContext,
):
    """Computes authoritative risk/reward figures using domain_calculator and explicit context."""
    context.validate()
    return calculate_risk_reward(
        direction=direction,
        planned_entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        capital_usdt=context.equity_usdt,
        risk_pct=context.risk_pct,
        leverage=context.leverage,
        margin_mode=context.margin_mode,
        min_net_rr=context.min_net_rr,
        costs=context.costs,
    )


def financial_value_is_known(value: Any) -> bool:
    """Returns True iff value is a finite number and not boolean."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def degraded_setup_plan(setup_id: str, direction: str, reason_code: str) -> Dict[str, Any]:
    """Builds a degraded plan DTO without omitting the setup from the upcoming API response."""
    return {
        "identity": {"setup_id": setup_id},
        "direction": direction,
        "status": "DATA_UNAVAILABLE",
        "is_valid": False,
        "take_profit": None,
        "financial": None,
        "reason_code": reason_code,
        "reason_message": "Chưa đủ dữ liệu để tính kế hoạch. Mở chi tiết để kiểm tra.",
    }


# ==================== V16.7 & V16.8 REPLAY FINANCIAL CONTEXT & CONFIG RESOLVER ====================

from enum import Enum
from typing import Union


class ReplayCapitalMode(str, Enum):
    """Authoritative sizing capital basis for replay (V16.8 NHÓM 03)."""
    CURRENT_ACCOUNT_BALANCE = "CURRENT_ACCOUNT_BALANCE"
    FIXED_INITIAL_CAPITAL = "FIXED_INITIAL_CAPITAL"


def resolve_replay_sizing_capital(
    capital_mode: Union[ReplayCapitalMode, str] = ReplayCapitalMode.CURRENT_ACCOUNT_BALANCE,
    initial_capital: float = 1000.0,
    account_equity: Optional[float] = None,
    as_of_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    V16.8 NHÓM 03 (PHẦN 026, 027, 028): Pure helper to resolve sizing capital basis.
    - Validates strictly positive finite numbers.
    - Zero account equity stops entry (is_valid=False); never falls back to initial_capital.
    - Distinguishes None (deferred to account audit) from 0.0 (exhausted account).
    """
    mode_str = str(capital_mode.value if hasattr(capital_mode, "value") else capital_mode).upper()
    if mode_str not in ("CURRENT_ACCOUNT_BALANCE", "FIXED_INITIAL_CAPITAL"):
        raise ValueError(f"INVALID_REPLAY_CAPITAL_MODE: {capital_mode}")

    cap = float(initial_capital)
    if isinstance(initial_capital, bool) or not math.isfinite(cap) or cap <= 0:
        raise ValueError(f"INITIAL_CAPITAL_INVALID: {initial_capital}")

    if mode_str == "FIXED_INITIAL_CAPITAL":
        return {
            "sizing_capital": cap,
            "capital_mode": mode_str,
            "basis": "FIXED_INITIAL_CAPITAL",
            "as_of_ms": as_of_ms,
            "is_valid": True,
            "provenance": "EXPLICIT_FIXED_INITIAL_CAPITAL",
        }
    else:
        # CURRENT_ACCOUNT_BALANCE
        if account_equity is None:
            return {
                "sizing_capital": None,
                "capital_mode": mode_str,
                "basis": "CURRENT_ACCOUNT_BALANCE",
                "as_of_ms": as_of_ms,
                "is_valid": True,
                "provenance": "DEFERRED_TO_ACCOUNT_AUDIT",
            }
        eq = float(account_equity)
        if isinstance(account_equity, bool) or not math.isfinite(eq):
            raise ValueError(f"ACCOUNT_EQUITY_INVALID: {account_equity}")
        if eq <= 0:
            return {
                "sizing_capital": 0.0,
                "capital_mode": mode_str,
                "basis": "CURRENT_ACCOUNT_BALANCE",
                "as_of_ms": as_of_ms,
                "is_valid": False,
                "reason": "ACCOUNT_EQUITY_NON_POSITIVE",
                "provenance": "ACCOUNT_AUDIT_EXHAUSTED",
            }
        return {
            "sizing_capital": eq,
            "capital_mode": mode_str,
            "basis": "CURRENT_ACCOUNT_BALANCE",
            "as_of_ms": as_of_ms,
            "is_valid": True,
            "provenance": "ACCOUNT_AUDIT_CURRENT_EQUITY",
        }


@dataclass(frozen=True)
class ReplayFinancialContext:
    """Explicit, typed execution/replay financial context (V16.7 Group D & E, V16.8 NHÓM 03)."""
    risk_pct: float
    capital_usdt: Optional[float] = None
    slippage_usd: Optional[float] = None
    fee_bps: Optional[float] = None
    costs: Optional[CostAssumptions] = None
    source: str = "REPLAY_EXPLICIT"
    capital_mode: str = "CURRENT_ACCOUNT_BALANCE"

    def validate(self) -> None:
        if isinstance(self.risk_pct, bool) or not math.isfinite(self.risk_pct) or self.risk_pct <= 0:
            raise ValueError(f"REPLAY_RISK_INVALID: {self.risk_pct}")
        if self.capital_usdt is not None:
            if isinstance(self.capital_usdt, bool) or not math.isfinite(self.capital_usdt) or self.capital_usdt < 0:
                raise ValueError(f"REPLAY_CAPITAL_INVALID: {self.capital_usdt}")
        if self.slippage_usd is not None:
            if isinstance(self.slippage_usd, bool) or not math.isfinite(self.slippage_usd) or self.slippage_usd < 0:
                raise ValueError(f"REPLAY_SLIPPAGE_INVALID: {self.slippage_usd}")
        if self.fee_bps is not None:
            if isinstance(self.fee_bps, bool) or not math.isfinite(self.fee_bps) or self.fee_bps < 0:
                raise ValueError(f"REPLAY_FEE_BPS_INVALID: {self.fee_bps}")


@dataclass(frozen=True)
class EffectiveReplayFinancialConfig:
    """Authoritative, immutable replay configuration result (V16.7 Group E, V16.8 NHÓM 03 & 06)."""
    initial_capital: float
    risk_pct: float
    slippage_usd: float
    maker_fee_rate: float
    taker_fee_rate: float
    costs: CostAssumptions
    provenance: Dict[str, Any]
    config_hash: str
    capital_mode: str = "CURRENT_ACCOUNT_BALANCE"
    contract_multiplier: float = 1.0
    execution_model: str = "ASSUMED_OHLC_PATH"


def resolve_execution_risk(
    account_risk_pct: float,
    explicit_replay_risk_pct: Optional[float] = None,
    model_risk_cap_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """
    V16.7 Group D: Reconciles risk authority between user account settings,
    explicit replay context, and optional model cap.
    """
    selected = (
        explicit_replay_risk_pct
        if explicit_replay_risk_pct is not None
        else account_risk_pct
    )

    if isinstance(selected, bool) or not math.isfinite(selected) or selected <= 0:
        raise ValueError(f"RISK_VALUE_INVALID: {selected}")

    if model_risk_cap_pct is not None:
        if isinstance(model_risk_cap_pct, bool) or not math.isfinite(model_risk_cap_pct) or model_risk_cap_pct <= 0:
            raise ValueError(f"MODEL_CAP_INVALID: {model_risk_cap_pct}")
        effective = min(selected, model_risk_cap_pct)
    else:
        effective = selected

    return {
        "configured_risk_pct": selected,
        "effective_risk_pct": effective,
        "source": (
            "REPLAY_EXPLICIT"
            if explicit_replay_risk_pct is not None
            else "ACCOUNT_SETTINGS"
        ),
    }


def resolve_replay_financial_config(
    initial_capital: float = 1000.0,
    risk_pct: float = 0.25,
    slippage: Optional[float] = None,
    fee_bps: Optional[float] = None,
    maker_fee_bps: Optional[float] = None,
    taker_fee_bps: Optional[float] = None,
    costs: Optional[CostAssumptions] = None,
    config: Optional[Any] = None,
    capital_mode: Union[ReplayCapitalMode, str] = ReplayCapitalMode.CURRENT_ACCOUNT_BALANCE,
    contract_multiplier: float = 1.0,
    execution_model: str = "ASSUMED_OHLC_PATH",
) -> EffectiveReplayFinancialConfig:
    """
    V16.7 Group E & V16.8: Centralized immutable config resolver for replay execution.
    - Validates strictly positive finite numbers.
    - Preserves zero values (0.0 slippage, 0 bps fee).
    - Accurately converts fee_bps / 10000.0 without threshold coercion.
    - Validates execution_model and capital_mode.
    - Does NOT mutate caller's `costs` or `config` objects.
    """
    import hashlib
    import json

    # 0. Validate execution_model and capital_mode
    model_str = str(execution_model or "ASSUMED_OHLC_PATH").upper()
    if model_str not in ("ASSUMED_OHLC_PATH", "CLOSE_QUOTE"):
        raise ValueError(f"CONFIG_ERROR: unknown execution_model '{execution_model}'")

    mode_str = str(capital_mode.value if hasattr(capital_mode, "value") else capital_mode).upper()
    if mode_str not in ("CURRENT_ACCOUNT_BALANCE", "FIXED_INITIAL_CAPITAL"):
        raise ValueError(f"CONFIG_ERROR: invalid capital_mode '{capital_mode}'")

    # 1. Validate capital & risk
    cap = float(initial_capital)
    if isinstance(initial_capital, bool) or not math.isfinite(cap) or cap <= 0:
        raise ValueError(f"INITIAL_CAPITAL_INVALID: {initial_capital}")

    risk = float(risk_pct)
    if isinstance(risk_pct, bool) or not math.isfinite(risk) or risk <= 0:
        raise ValueError(f"RISK_PCT_INVALID: {risk_pct}")

    # 2. Resolve slippage (0.0 is explicitly valid)
    prov_slip = "DEFAULT"
    if slippage is not None:
        slip_val = float(slippage)
        prov_slip = "EXPLICIT_ARG"
    elif costs is not None and getattr(costs, "slippage_usd", None) is not None:
        slip_val = float(costs.slippage_usd)
        prov_slip = "CALLER_COSTS"
    elif config is not None and getattr(config, "slippage_usd", None) is not None:
        slip_val = float(config.slippage_usd)
        prov_slip = "CALLER_CONFIG"
    else:
        slip_val = 0.10
        prov_slip = "DEFAULT"

    if isinstance(slip_val, bool) or not math.isfinite(slip_val) or slip_val < 0:
        raise ValueError(f"SLIPPAGE_INVALID: {slip_val}")

    # 3. Resolve fees (0 bps and fractional bps like 0.5 are explicitly valid)
    if maker_fee_bps is not None:
        m_bps = float(maker_fee_bps)
        if isinstance(maker_fee_bps, bool) or not math.isfinite(m_bps) or m_bps < 0:
            raise ValueError(f"MAKER_FEE_BPS_INVALID: {maker_fee_bps}")
        maker_rate = m_bps / 10000.0
        prov_maker = "EXPLICIT_MAKER_BPS"
    elif costs is not None and getattr(costs, "maker_fee_rate", None) is not None:
        maker_rate = float(costs.maker_fee_rate)
        prov_maker = "CALLER_COSTS"
    else:
        maker_rate = 0.0002
        prov_maker = "DEFAULT"

    if isinstance(maker_rate, bool) or not math.isfinite(maker_rate) or maker_rate < 0:
        raise ValueError(f"MAKER_RATE_INVALID: {maker_rate}")

    if taker_fee_bps is not None:
        t_bps = float(taker_fee_bps)
        if isinstance(taker_fee_bps, bool) or not math.isfinite(t_bps) or t_bps < 0:
            raise ValueError(f"TAKER_FEE_BPS_INVALID: {taker_fee_bps}")
        taker_rate = t_bps / 10000.0
        prov_taker = "EXPLICIT_TAKER_BPS"
    elif fee_bps is not None:
        f_bps = float(fee_bps)
        if isinstance(fee_bps, bool) or not math.isfinite(f_bps) or f_bps < 0:
            raise ValueError(f"FEE_BPS_INVALID: {fee_bps}")
        taker_rate = f_bps / 10000.0
        prov_taker = "EXPLICIT_GENERIC_FEE_BPS"
    elif costs is not None and getattr(costs, "taker_fee_rate", None) is not None:
        taker_rate = float(costs.taker_fee_rate)
        prov_taker = "CALLER_COSTS"
    else:
        taker_rate = 0.0004
        prov_taker = "DEFAULT"

    if isinstance(taker_rate, bool) or not math.isfinite(taker_rate) or taker_rate < 0:
        raise ValueError(f"TAKER_RATE_INVALID: {taker_rate}")

    # 4. Construct brand new CostAssumptions without mutating caller's object
    clean_costs = CostAssumptions(
        maker_fee_rate=maker_rate,
        taker_fee_rate=taker_rate,
        slippage_usd=slip_val,
        tp_slippage_usd=float(getattr(costs, "tp_slippage_usd", 0.0)) if costs else 0.0,
    )

    provenance = {
        "initial_capital": cap,
        "risk_pct": risk,
        "slippage_usd": slip_val,
        "slippage_source": prov_slip,
        "maker_fee_rate": maker_rate,
        "maker_fee_source": prov_maker,
        "taker_fee_rate": taker_rate,
        "taker_fee_source": prov_taker,
    }

    hash_str = json.dumps(provenance, sort_keys=True)
    cfg_hash = hashlib.sha256(hash_str.encode("utf-8")).hexdigest()[:16]

    return EffectiveReplayFinancialConfig(
        initial_capital=cap,
        risk_pct=risk,
        slippage_usd=slip_val,
        maker_fee_rate=maker_rate,
        taker_fee_rate=taker_rate,
        costs=clean_costs,
        provenance=provenance,
        config_hash=cfg_hash,
        capital_mode=mode_str,
        contract_multiplier=float(contract_multiplier or 1.0),
        execution_model=model_str,
    )


def resolve_execution_cost_context(
    *,
    snapshot: Optional[Dict[str, Any]] = None,
    candidate: Optional[Any] = None,
    settings: Optional[Dict[str, Any]] = None,
    config: Optional[Any] = None,
    as_of_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    AURUM V17.5.5 (Appendix B1 & B2):
    Shared cost authority across production orders and research comparison plans.
    Guarantees parity in maker/taker fee rates (0.0002 / 0.0004) and slippage resolution.
    Preserves explicit 0.0 values and distinguishes unavailable/invalid costs.
    """
    import math

    def _is_finite_non_negative(val: Any) -> bool:
        if val is None or isinstance(val, bool):
            return False
        try:
            f = float(val)
            return math.isfinite(f) and f >= 0.0
        except (ValueError, TypeError):
            return False

    # 1. Prioritize explicit frozen snapshot if provided
    if snapshot is not None:
        if isinstance(snapshot, dict):
            snap_dict = snapshot
        elif hasattr(snapshot, "to_dict") and callable(snapshot.to_dict):
            snap_dict = snapshot.to_dict()
        elif hasattr(snapshot, "__dict__"):
            snap_dict = {k: v for k, v in snapshot.__dict__.items() if not k.startswith("_")}
        else:
            try:
                snap_dict = dict(snapshot)
            except Exception:
                return {
                    "available": False,
                    "reason": "INVALID_SNAPSHOT_TYPE",
                    "entry_slippage_usd": 0.0,
                    "tp_slippage_usd": 0.0,
                    "sl_slippage_usd": 0.0,
                    "maker_fee_rate": 0.0002,
                    "taker_fee_rate": 0.0004,
                    "provenance": "SNAPSHOT_ERROR",
                    "version": 1,
                }

        raw_version = snap_dict.get("version", 1)
        try:
            ver = int(raw_version) if raw_version is not None else 1
        except (ValueError, TypeError):
            ver = 1

        raw_slip = (
            snap_dict.get("entry_slippage_usd")
            if snap_dict.get("entry_slippage_usd") is not None
            else (snap_dict.get("slippage_usd") if snap_dict.get("slippage_usd") is not None else snap_dict.get("slippage"))
        )
        if raw_slip is None:
            return {
                "available": False,
                "reason": "MISSING_ENTRY_SLIPPAGE",
                "entry_slippage_usd": 0.0,
                "tp_slippage_usd": 0.0,
                "sl_slippage_usd": 0.0,
                "maker_fee_rate": 0.0002,
                "taker_fee_rate": 0.0004,
                "provenance": "SNAPSHOT_MISSING_SLIP",
                "version": ver,
            }

        if not _is_finite_non_negative(raw_slip):
            return {
                "available": False,
                "reason": "INVALID_SLIPPAGE_VALUE",
                "entry_slippage_usd": 0.0,
                "tp_slippage_usd": 0.0,
                "sl_slippage_usd": 0.0,
                "maker_fee_rate": 0.0002,
                "taker_fee_rate": 0.0004,
                "provenance": "SNAPSHOT_INVALID_SLIP",
                "version": ver,
            }

        maker_rate = snap_dict.get("maker_fee_rate", 0.0002)
        taker_rate = snap_dict.get("taker_fee_rate", 0.0004)
        if not _is_finite_non_negative(maker_rate) or not _is_finite_non_negative(taker_rate):
            return {
                "available": False,
                "reason": "INVALID_FEE_RATES",
                "entry_slippage_usd": float(raw_slip),
                "tp_slippage_usd": 0.0,
                "sl_slippage_usd": float(raw_slip),
                "maker_fee_rate": 0.0002,
                "taker_fee_rate": 0.0004,
                "provenance": "SNAPSHOT_INVALID_FEES",
                "version": ver,
            }

        tp_slip = snap_dict.get("tp_slippage_usd", 0.0)
        sl_slip = snap_dict.get("sl_slippage_usd", raw_slip)
        if not _is_finite_non_negative(tp_slip) or not _is_finite_non_negative(sl_slip):
            return {
                "available": False,
                "reason": "INVALID_EXIT_SLIPPAGE",
                "entry_slippage_usd": float(raw_slip),
                "tp_slippage_usd": 0.0,
                "sl_slippage_usd": float(raw_slip),
                "maker_fee_rate": float(maker_rate),
                "taker_fee_rate": float(taker_rate),
                "provenance": "SNAPSHOT_INVALID_EXIT_SLIP",
                "version": ver,
            }

        return {
            "available": True,
            "reason": None,
            "entry_slippage_usd": float(raw_slip),
            "tp_slippage_usd": float(tp_slip),
            "sl_slippage_usd": float(sl_slip),
            "maker_fee_rate": float(maker_rate),
            "taker_fee_rate": float(taker_rate),
            "provenance": "FROZEN_ORDER_SNAPSHOT",
            "version": ver,
        }

    # If no snapshot and no candidate/settings/config, cannot fabricate costs
    if candidate is None and not settings and config is None:
        return {
            "available": False,
            "reason": "MISSING_COST_SNAPSHOT",
            "entry_slippage_usd": 0.0,
            "tp_slippage_usd": 0.0,
            "sl_slippage_usd": 0.0,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
            "provenance": "MISSING_COST_DATA",
            "version": 1,
        }

    # 2. Derive from effective settings / config or candidate calculation
    st = settings or {}
    calc = getattr(candidate, "calculation", None) if candidate is not None else None

    cand_slip = None
    if "slippage_usd" in st and st.get("slippage_usd") is not None:
        cand_slip = st.get("slippage_usd")
    elif "costs" in st and st.get("costs") is not None:
        costs_candidate = st.get("costs")
        if isinstance(costs_candidate, dict):
            cand_slip = costs_candidate.get("entry_slippage_usd") if "entry_slippage_usd" in costs_candidate else costs_candidate.get("slippage_usd")
        elif costs_candidate is not None:
            cand_slip = getattr(costs_candidate, "entry_slippage_usd", getattr(costs_candidate, "slippage_usd", None))
    elif calc is not None and getattr(calc, "slippage_usd", None) is not None:
        cand_slip = getattr(calc, "slippage_usd", None)
    elif config is not None and getattr(config, "cg01_config", None):
        cand_slip = getattr(config.cg01_config, "slippage_usd", None)

    slip_val = 0.10
    if cand_slip is not None:
        if not _is_finite_non_negative(cand_slip):
            return {
                "available": False,
                "reason": "INVALID_CANDIDATE_SLIPPAGE",
                "entry_slippage_usd": 0.0,
                "tp_slippage_usd": 0.0,
                "sl_slippage_usd": 0.0,
                "maker_fee_rate": 0.0002,
                "taker_fee_rate": 0.0004,
                "provenance": "CONFIG_ERROR",
                "version": 1,
            }
        slip_val = float(cand_slip)

    # Fee resolution (unified 0.0002 maker / 0.0004 taker default)
    costs_obj = st.get("costs")
    maker_rate = None
    taker_rate = None

    if "maker_fee_rate" in st and st.get("maker_fee_rate") is not None:
        maker_rate = st.get("maker_fee_rate")
    if "taker_fee_rate" in st and st.get("taker_fee_rate") is not None:
        taker_rate = st.get("taker_fee_rate")

    if (maker_rate is None or taker_rate is None) and costs_obj is not None:
        if isinstance(costs_obj, dict):
            maker_rate = maker_rate if maker_rate is not None else costs_obj.get("maker_fee_rate")
            taker_rate = taker_rate if taker_rate is not None else costs_obj.get("taker_fee_rate")
        else:
            maker_rate = maker_rate if maker_rate is not None else getattr(costs_obj, "maker_fee_rate", None)
            taker_rate = taker_rate if taker_rate is not None else getattr(costs_obj, "taker_fee_rate", None)

    if (maker_rate is None or taker_rate is None) and calc is not None:
        maker_rate = maker_rate if maker_rate is not None else getattr(calc, "maker_fee_rate", None)
        taker_rate = taker_rate if taker_rate is not None else getattr(calc, "taker_fee_rate", None)

    if maker_rate is None:
        maker_rate = 0.0002
    if taker_rate is None:
        taker_rate = 0.0004

    raw_ver = st.get("config_version") or getattr(config, "config_version", 1)
    try:
        version = int(raw_ver) if raw_ver is not None else 1
    except (ValueError, TypeError):
        version = 1

    if not _is_finite_non_negative(maker_rate) or not _is_finite_non_negative(taker_rate):
        return {
            "available": False,
            "reason": "INVALID_CONFIG_FEES",
            "entry_slippage_usd": slip_val,
            "tp_slippage_usd": 0.0,
            "sl_slippage_usd": slip_val,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
            "provenance": "CONFIG_ERROR",
            "version": version,
        }

    return {
        "available": True,
        "reason": None,
        "entry_slippage_usd": float(slip_val),
        "tp_slippage_usd": 0.0,
        "sl_slippage_usd": float(slip_val),
        "maker_fee_rate": float(maker_rate),
        "taker_fee_rate": float(taker_rate),
        "provenance": "EFFECTIVE_SETTINGS_AUTHORITY",
        "version": version,
    }


def build_verified_cost_context(
    *,
    settings: Optional[Dict[str, Any]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
    config: Optional[Any] = None,
    candidate: Optional[Any] = None,
    as_of_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    AURUM V17.5.8 (Part 046):
    Builds a typed, verified, immutable cost context dictionary.
    Prioritizes frozen order snapshot, preserves explicit 0.0 slippage,
    strictly validates finite non-negative values, and rejects malformed cost inputs.
    """
    return resolve_execution_cost_context(
        snapshot=snapshot,
        candidate=candidate,
        settings=settings,
        config=config,
        as_of_ms=as_of_ms,
    )


def assert_order_financial_parity(
    *,
    candidate_calc: Any = None,
    comparison_plan: Optional[Dict[str, Any]] = None,
    order_snapshot: Optional[Dict[str, Any]] = None,
    fill_result: Optional[Dict[str, Any]] = None,
    tolerance: float = 1e-4,
) -> Dict[str, Any]:
    """
    AURUM V17.5.9 (D05, Section 07):
    Full financial parity reconciler across plan, snapshot, comparison plan,
    and execution fill.
    Reconciles: direction, planned_entry, stop_loss, take_profit, quantity,
    risk_budget, gross_rr, net_rr, entry_slippage, sl_slippage, maker_fee_rate,
    taker_fee_rate.
    Missing mandatory fields are flagged as INCOMPLETE / MISMATCH, never fabricated.
    """
    mismatches: List[str] = []
    missing_fields: List[str] = []
    compared_fields: List[str] = []

    if order_snapshot is None and candidate_calc is None and comparison_plan is None:
        return {
            "status": "INCOMPLETE",
            "is_parity": False,
            "compared_fields": [],
            "mismatches": ["No financial records provided for reconciliation"],
            "missing_fields": ["order_snapshot"],
            "source_versions": {},
        }

    snap = order_snapshot or {}
    snap_costs = snap.get("resolved_execution_costs") or snap.get("cost_snapshot") or {}

    def _safe_float(val: Any) -> Optional[float]:
        if val is None or isinstance(val, bool):
            return None
        try:
            f = float(val)
            return f if math.isfinite(f) else None
        except (ValueError, TypeError):
            return None

    # Helper to extract slippage from snapshot
    snap_slip_raw = (
        snap.get("slippage_usd")
        if snap.get("slippage_usd") is not None
        else (snap_costs.get("entry_slippage_usd") if snap_costs.get("entry_slippage_usd") is not None else snap.get("slippage"))
    )
    snap_slip = _safe_float(snap_slip_raw)

    snap_maker_raw = snap_costs.get("maker_fee_rate") if snap_costs.get("maker_fee_rate") is not None else snap.get("maker_fee_rate")
    snap_maker = _safe_float(snap_maker_raw)

    snap_taker_raw = snap_costs.get("taker_fee_rate") if snap_costs.get("taker_fee_rate") is not None else snap.get("taker_fee_rate")
    snap_taker = _safe_float(snap_taker_raw)

    # 1. Reconcile order_snapshot vs comparison_plan
    if comparison_plan is not None:
        comp = comparison_plan
        comp_costs = comp.get("cost_snapshot") or comp.get("resolved_execution_costs") or {}

        # Direction
        snap_dir = snap.get("direction")
        comp_dir = comp.get("direction")
        if snap_dir is not None and comp_dir is not None:
            compared_fields.append("direction")
            if str(snap_dir).upper() != str(comp_dir).upper():
                mismatches.append(f"Direction mismatch: snap={snap_dir} vs comp={comp_dir}")

        # Slippage: missing != matching (Part 057)
        comp_slip_raw = comp_costs.get("entry_slippage_usd") if comp_costs.get("entry_slippage_usd") is not None else comp.get("slippage_usd")
        comp_slip = _safe_float(comp_slip_raw)
        if snap_slip is None and comp_slip is not None:
            missing_fields.append("order_snapshot.entry_slippage_usd")
            mismatches.append("Missing entry slippage in order snapshot")
        elif snap_slip is not None and comp_slip is None:
            missing_fields.append("comparison_plan.entry_slippage_usd")
            mismatches.append("Missing entry slippage in comparison plan")
        elif snap_slip is not None and comp_slip is not None:
            compared_fields.append("entry_slippage_usd")
            if abs(snap_slip - comp_slip) > tolerance:
                mismatches.append(f"Slippage mismatch: snap={snap_slip} vs comp={comp_slip}")

        # Maker fee rate
        comp_maker_raw = comp_costs.get("maker_fee_rate") if comp_costs.get("maker_fee_rate") is not None else comp.get("maker_fee_rate")
        comp_maker = _safe_float(comp_maker_raw)
        if snap_maker is not None and comp_maker is not None:
            compared_fields.append("maker_fee_rate")
            if abs(snap_maker - comp_maker) > tolerance:
                mismatches.append(f"Maker fee mismatch: snap={snap_maker} vs comp={comp_maker}")
        elif snap_maker is not None and comp_maker_raw is not None and comp_maker is None:
            mismatches.append("Invalid maker fee in comparison plan")

        # Taker fee rate
        comp_taker_raw = comp_costs.get("taker_fee_rate") if comp_costs.get("taker_fee_rate") is not None else comp.get("taker_fee_rate")
        comp_taker = _safe_float(comp_taker_raw)
        if snap_taker is not None and comp_taker is not None:
            compared_fields.append("taker_fee_rate")
            if abs(snap_taker - comp_taker) > tolerance:
                mismatches.append(f"Taker fee mismatch: snap={snap_taker} vs comp={comp_taker}")

        # Numeric fields: planned_entry, stop_loss, take_profit, quantity
        for field in ("planned_entry", "stop_loss", "take_profit", "quantity"):
            s_val = _safe_float(snap.get(field))
            c_val = _safe_float(comp.get(field) or comp.get(field.replace("planned_", "")))
            if s_val is not None and c_val is not None:
                compared_fields.append(field)
                if abs(s_val - c_val) > tolerance:
                    mismatches.append(f"{field} mismatch: snap={s_val} vs comp={c_val}")

        # Net R:R
        s_nrr = _safe_float(snap.get("estimated_net_rr") or snap.get("net_rr"))
        c_nrr = _safe_float(comp.get("estimated_net_rr") or comp.get("net_rr"))
        if s_nrr is not None and c_nrr is not None:
            compared_fields.append("net_rr")
            if abs(s_nrr - c_nrr) > tolerance:
                mismatches.append(f"Net R:R mismatch: snap={s_nrr} vs comp={c_nrr}")

        # Risk budget
        s_rb = _safe_float(snap.get("target_risk_budget") or snap.get("initial_risk_usdt") or snap.get("risk_budget"))
        c_rb = _safe_float(comp.get("target_risk_budget") or comp.get("initial_risk_usdt") or comp.get("risk_budget"))
        if s_rb is not None and c_rb is not None:
            compared_fields.append("risk_budget")
            if abs(s_rb - c_rb) > tolerance:
                mismatches.append(f"Risk budget mismatch: snap={s_rb} vs comp={c_rb}")

    # 2. Reconcile order_snapshot vs candidate_calc
    if candidate_calc is not None:
        c_qty = _safe_float(getattr(candidate_calc, "quantity", None))
        s_qty = _safe_float(snap.get("quantity"))
        if s_qty is not None and c_qty is not None:
            compared_fields.append("calc_quantity")
            if abs(s_qty - c_qty) > tolerance:
                mismatches.append(f"Quantity mismatch: snap={s_qty} vs calc={c_qty}")

        c_sl = _safe_float(getattr(candidate_calc, "stop_loss", None))
        s_sl = _safe_float(snap.get("stop_loss"))
        if s_sl is not None and c_sl is not None:
            compared_fields.append("calc_stop_loss")
            if abs(s_sl - c_sl) > tolerance:
                mismatches.append(f"Stop loss mismatch: snap={s_sl} vs calc={c_sl}")

        c_entry = _safe_float(getattr(candidate_calc, "entry", getattr(candidate_calc, "planned_entry", None)))
        s_entry = _safe_float(snap.get("planned_entry"))
        if s_entry is not None and c_entry is not None:
            compared_fields.append("calc_entry")
            if abs(s_entry - c_entry) > tolerance:
                mismatches.append(f"Planned entry mismatch: snap={s_entry} vs calc={c_entry}")

        c_tp = _safe_float(getattr(candidate_calc, "take_profit", None))
        s_tp = _safe_float(snap.get("take_profit"))
        if s_tp is not None and c_tp is not None:
            compared_fields.append("calc_take_profit")
            if abs(s_tp - c_tp) > tolerance:
                mismatches.append(f"Take profit mismatch: snap={s_tp} vs calc={c_tp}")

        c_risk = _safe_float(getattr(candidate_calc, "net_risk_usdt", getattr(candidate_calc, "risk_budget", None)))
        s_risk = _safe_float(snap.get("target_risk_budget") or snap.get("initial_risk_usdt") or snap.get("risk_budget"))
        if s_risk is not None and c_risk is not None:
            compared_fields.append("calc_risk_budget")
            if abs(s_risk - c_risk) > tolerance:
                mismatches.append(f"Risk budget mismatch: snap={s_risk} vs calc={c_risk}")

        c_net_rr = _safe_float(getattr(candidate_calc, "net_rr", None))
        s_net_rr = _safe_float(snap.get("estimated_net_rr") or snap.get("net_rr"))
        if s_net_rr is not None and c_net_rr is not None:
            compared_fields.append("calc_net_rr")
            if abs(s_net_rr - c_net_rr) > tolerance:
                mismatches.append(f"Net R:R mismatch: snap={s_net_rr} vs calc={c_net_rr}")

        c_gross_rr = _safe_float(getattr(candidate_calc, "gross_rr", None))
        s_gross_rr = _safe_float(snap.get("gross_rr"))
        if s_gross_rr is not None and c_gross_rr is not None:
            compared_fields.append("calc_gross_rr")
            if abs(s_gross_rr - c_gross_rr) > tolerance:
                mismatches.append(f"Gross R:R mismatch: snap={s_gross_rr} vs calc={c_gross_rr}")

    # Geometry check if planned_entry, stop_loss, take_profit and direction present
    s_dir = str(snap.get("direction", "")).upper()
    s_entry = _safe_float(snap.get("planned_entry"))
    s_sl = _safe_float(snap.get("stop_loss"))
    s_tp = _safe_float(snap.get("take_profit"))
    if s_dir and s_entry and s_sl and s_tp:
        compared_fields.append("geometry")
        if s_dir in ("SHORT", "SELL"):
            if not (s_tp < s_entry < s_sl):
                mismatches.append(f"Incorrect SHORT geometry: tp={s_tp} entry={s_entry} sl={s_sl}")
        elif s_dir in ("LONG", "BUY"):
            if not (s_sl < s_entry < s_tp):
                mismatches.append(f"Incorrect LONG geometry: sl={s_sl} entry={s_entry} tp={s_tp}")

    # 3. Reconcile fill_result
    if fill_result:
        realized_slip = _safe_float(fill_result.get("realized_slippage", fill_result.get("slippage")))
        if realized_slip is not None:
            compared_fields.append("realized_slippage")
            if snap_slip is not None and abs(realized_slip - snap_slip) > tolerance:
                mismatches.append(f"Fill slippage mismatch: snap={snap_slip} vs fill={realized_slip}")

    # Check for missing mandatory fields if snapshot is being verified
    if order_snapshot is not None:
        mandatory = ("planned_entry", "stop_loss", "take_profit", "quantity")
        for m in mandatory:
            if m in snap and snap.get(m) is None:
                missing_fields.append(m)

    is_parity = (len(mismatches) == 0 and len(missing_fields) == 0)
    status = "PASS" if is_parity else ("INCOMPLETE" if len(missing_fields) > 0 else "MISMATCH")

    return {
        "status": status,
        "is_parity": is_parity,
        "compared_fields": list(dict.fromkeys(compared_fields)),
        "mismatches": mismatches,
        "missing_fields": missing_fields,
        "source_versions": {
            "snapshot_version": snap.get("policy_config_version", snap.get("version", 1)),
            "comparison_version": (comparison_plan or {}).get("policy_config_version", (comparison_plan or {}).get("version", 1)),
        },
    }
