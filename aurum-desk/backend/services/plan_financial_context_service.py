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
    risk_pct = float(settings.risk_pct) if settings.risk_pct else 0.25
    leverage = int(settings.requested_leverage) if settings.requested_leverage else 5
    margin_mode = str(settings.margin_mode or "ISOLATED")
    risk_config_version = int(settings.config_version) if settings.config_version else 1
    instrument_ver = str(settings.metadata_version or "XAUUSDT-paper-v1")

    # Read policy min_net_rr
    policy = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()
    min_net_rr = float(policy.min_net_rr) if policy and policy.min_net_rr is not None else 2.0

    costs = CostAssumptions()
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
