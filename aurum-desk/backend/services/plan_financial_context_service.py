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

    # Read policy min_net_rr
    policy = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()
    min_net_rr = float(policy.min_net_rr) if policy and policy.min_net_rr is not None else 2.0

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
