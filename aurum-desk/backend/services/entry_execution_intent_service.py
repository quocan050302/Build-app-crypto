"""
Entry Execution Intent Service (V14.4.2)
Resolves execution intent (LIMIT vs MARKET) for setups based on model descriptors,
quote proximity, price geometry, and policy invariants.
"""
from dataclasses import dataclass
from math import isfinite
from typing import Literal, Optional, Dict, Any

Direction = Literal['LONG', 'SHORT']


@dataclass(frozen=True)
class EntryIntentDecision:
    allowed: bool
    order_type: Optional[str]  # "LIMIT" or "MARKET"
    entry_mode: str  # "RETEST_LIMIT", "CONFIRMATION_MARKET", "MANUAL_DIRECT", "ENTRY_MODE_UNAVAILABLE"
    planned_entry: Optional[float]
    reason_code: str
    message_vi: str
    policy_version: int = 1
    model_id: Optional[str] = None
    expiry_ms: Optional[int] = None
    max_entry_drift: Optional[float] = None


def decide_retest_limit_placement(
    direction: str,
    entry: float,
    bid: float,
    ask: float,
) -> tuple[bool, str, str]:
    """Pure placement decision for certified retest limit orders."""
    values = (entry, bid, ask)
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not isfinite(x) or x <= 0 for x in values):
        return False, "QUOTE_OR_ENTRY_INVALID", "Giá entry hoặc quote không hợp lệ"
    if ask < bid or direction not in ("LONG", "SHORT"):
        return False, "QUOTE_OR_DIRECTION_INVALID", "Chiều lệnh hoặc spread quote không hợp lệ"

    # Default certified retest rule: do NOT place a limit order if already marketable
    marketable = (ask <= entry) if direction == "LONG" else (bid >= entry)
    if marketable:
        return False, "ENTRY_ZONE_ALREADY_REACHED", "Giá thị trường đã chạm hoặc vượt qua vùng Entry"

    return True, "RETEST_PENDING_ALLOWED", "Cho phép đặt lệnh chờ retest LIMIT"


def is_limit_triggered(direction: str, entry: float, bid: float, ask: float) -> bool:
    """Evaluates if a limit order should trigger based on actual directional quotes."""
    if direction == "LONG":
        return ask <= entry
    if direction == "SHORT":
        return bid >= entry
    raise ValueError(f"UNKNOWN_DIRECTION: {direction}")


def resolve_entry_execution_intent(
    setup: Any,
    model_descriptor: Optional[Dict[str, Any]] = None,
    quote: Optional[Any] = None,
    now_ms: Optional[int] = None,
) -> EntryIntentDecision:
    """Authoritative resolver for setup execution intent."""
    from services.plan_resolver import resolve_plan_levels

    plan = resolve_plan_levels(setup)
    if not plan.get("is_valid"):
        return EntryIntentDecision(
            allowed=False,
            order_type=None,
            entry_mode="ENTRY_MODE_UNAVAILABLE",
            planned_entry=None,
            reason_code="INVALID_PRICE_GEOMETRY",
            message_vi=plan.get("error_message") or "Hình học giá không hợp lệ",
        )

    planned_entry = plan["entry"]
    direction = getattr(setup, "direction", None) or "LONG"
    family = getattr(setup, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC"

    # Check model descriptor
    # Standard SMC certified retest setups use RETEST_LIMIT by default
    model_mode = (model_descriptor or {}).get("execution_mode") or "RETEST_LIMIT"
    model_id = (model_descriptor or {}).get("model_id") or "SMC_CERTIFIED_RETEST"
    policy_version = (model_descriptor or {}).get("policy_version", 1)

    if model_mode == "LEGACY_POLICY":
        return EntryIntentDecision(
            allowed=True,
            order_type="MARKET",
            entry_mode="LEGACY_POLICY",
            planned_entry=planned_entry,
            reason_code="LEGACY_POLICY_ALLOWED",
            message_vi="Lệnh legacy theo chính sách tương thích",
            policy_version=policy_version,
            model_id=model_id,
        )

    if model_mode == "CONFIRMATION_MARKET":
        return EntryIntentDecision(
            allowed=True,
            order_type="MARKET",
            entry_mode="CONFIRMATION_MARKET",
            planned_entry=planned_entry,
            reason_code="MARKET_CONFIRMATION_ALLOWED",
            message_vi="Lệnh thị trường có xác nhận nến đóng",
            policy_version=policy_version,
            model_id=model_id,
        )

    # For RETEST_LIMIT: check placement validity if quote is available
    if quote is not None:
        bid = getattr(quote, "bid", None)
        ask = getattr(quote, "ask", None)
        if bid is not None and ask is not None:
            can_place, code, msg = decide_retest_limit_placement(direction, planned_entry, bid, ask)
            if not can_place:
                return EntryIntentDecision(
                    allowed=False,
                    order_type="LIMIT",
                    entry_mode="RETEST_LIMIT",
                    planned_entry=planned_entry,
                    reason_code=code,
                    message_vi=msg,
                    policy_version=policy_version,
                    model_id=model_id,
                )

    return EntryIntentDecision(
        allowed=True,
        order_type="LIMIT",
        entry_mode="RETEST_LIMIT",
        planned_entry=planned_entry,
        reason_code="RETEST_LIMIT_ALLOWED",
        message_vi="Lệnh chờ LIMIT tại vùng retest SMC",
        policy_version=policy_version,
        model_id=model_id,
    )
