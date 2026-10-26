"""
backend/services/shared_exit_evaluator.py
V14.4.4 - Shared Pure Order Exit Evaluator.

PHẦN 039, 040, 041, 106:
Pure helper shared by BOTH realtime execution (process_exit_tick) and
asynchronous late evidence reconciliation (market_reconciliation_service).

Invariants:
1. LONG exit requires executable Bid. If Bid is missing, returns unavailable_reason="MISSING_EXECUTABLE_SIDE".
   Never falls back to Last with price_basis "BID".
2. SHORT exit requires executable Ask. If Ask is missing, returns unavailable_reason="MISSING_EXECUTABLE_SIDE".
   Never falls back to Last with price_basis "ASK".
3. Gap pricing parity: applied_exit_price reflects executable price when gapping past trigger
   (e.g. SL 4170 with Bid 4169 -> applied_exit_price is 4169.0).
4. Symmetrical for LONG and SHORT.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ExitDecision:
    triggered: bool
    cause: str
    trigger_level: float
    applied_exit_price: float
    price_basis: str
    evidence_reference: Optional[str] = None
    unavailable_reason: Optional[str] = None


def evaluate_order_exit(
    order: Any,
    executable_quote: Dict[str, Any],
    policy_snapshot: Optional[Dict[str, Any]] = None,
) -> ExitDecision:
    """Pure exit evaluator shared across realtime monitoring and historical reconciliation.

    order: models.PaperOrder or dictionary representation of order snapshot.
    executable_quote: dictionary containing bid, ask, last, high, low, evidence_id.
    """
    def _get(field_name: str, default: Any = None) -> Any:
        if isinstance(order, dict):
            return order.get(field_name, default)
        return getattr(order, field_name, default)

    direction = _get("direction")
    if direction not in ("LONG", "SHORT"):
        return ExitDecision(
            triggered=False,
            cause="",
            trigger_level=0.0,
            applied_exit_price=0.0,
            price_basis="NONE",
            unavailable_reason="UNSUPPORTED_OR_UNKNOWN_DIRECTION",
        )

    stop_loss = _get("stop_loss")
    take_profit = _get("take_profit")
    liquidation = _get("estimated_liquidation")
    ev_id = executable_quote.get("evidence_id")

    # Check for tp_slippage from cost_snapshot or policy_snapshot
    tp_slip = 0.0
    cost_snap = _get("cost_snapshot")
    if cost_snap:
        try:
            snap = json.loads(cost_snap) if isinstance(cost_snap, str) else cost_snap
            tp_slip = float(snap.get("tp_slippage_usd", 0.0))
        except Exception:
            tp_slip = 0.0
    elif policy_snapshot and "tp_slippage_usd" in policy_snapshot:
        try:
            tp_slip = float(policy_snapshot["tp_slippage_usd"])
        except Exception:
            tp_slip = 0.0

    # Bar extremes (candle evaluation)
    high = executable_quote.get("high")
    low = executable_quote.get("low")
    if high is not None and low is not None and stop_loss is not None and take_profit is not None:
        if direction == "LONG":
            sl_hit = low <= stop_loss
            tp_hit = high >= take_profit
            if sl_hit and tp_hit:
                return ExitDecision(
                    triggered=True,
                    cause="AMBIGUOUS_BAR_SL_FIRST",
                    trigger_level=stop_loss,
                    applied_exit_price=stop_loss,
                    price_basis="BAR_EXTREMES",
                    evidence_reference=ev_id,
                )
            elif sl_hit:
                applied_sl = min(stop_loss, low) if low is not None else stop_loss
                return ExitDecision(
                    triggered=True,
                    cause="SL_HIT",
                    trigger_level=stop_loss,
                    applied_exit_price=applied_sl,
                    price_basis="BAR_EXTREMES",
                    evidence_reference=ev_id,
                )
            elif tp_hit:
                applied_tp = round(take_profit - tp_slip, 2)
                return ExitDecision(
                    triggered=True,
                    cause="TP_HIT",
                    trigger_level=take_profit,
                    applied_exit_price=applied_tp,
                    price_basis="BAR_EXTREMES",
                    evidence_reference=ev_id,
                )
        elif direction == "SHORT":
            sl_hit = high >= stop_loss
            tp_hit = low <= take_profit
            if sl_hit and tp_hit:
                return ExitDecision(
                    triggered=True,
                    cause="AMBIGUOUS_BAR_SL_FIRST",
                    trigger_level=stop_loss,
                    applied_exit_price=stop_loss,
                    price_basis="BAR_EXTREMES",
                    evidence_reference=ev_id,
                )
            elif sl_hit:
                applied_sl = max(stop_loss, high) if high is not None else stop_loss
                return ExitDecision(
                    triggered=True,
                    cause="SL_HIT",
                    trigger_level=stop_loss,
                    applied_exit_price=applied_sl,
                    price_basis="BAR_EXTREMES",
                    evidence_reference=ev_id,
                )
            elif tp_hit:
                applied_tp = round(take_profit + tp_slip, 2)
                return ExitDecision(
                    triggered=True,
                    cause="TP_HIT",
                    trigger_level=take_profit,
                    applied_exit_price=applied_tp,
                    price_basis="BAR_EXTREMES",
                    evidence_reference=ev_id,
                )

    # Tick evaluation: strict Bid/Ask requirement (no fallback to Last)
    bid = executable_quote.get("bid")
    ask = executable_quote.get("ask")

    if direction == "LONG":
        if bid is None or not isinstance(bid, (int, float)):
            return ExitDecision(
                triggered=False,
                cause="",
                trigger_level=0.0,
                applied_exit_price=0.0,
                price_basis="NONE",
                unavailable_reason="MISSING_EXECUTABLE_SIDE",
            )

        # 1. Liquidation check
        if liquidation is not None and bid <= liquidation:
            return ExitDecision(
                triggered=True,
                cause="LIQUIDATED",
                trigger_level=liquidation,
                applied_exit_price=bid,
                price_basis="BID",
                evidence_reference=ev_id,
            )

        # 2. Stop Loss check (gap price applied if bid < stop_loss)
        if stop_loss is not None and bid <= stop_loss:
            return ExitDecision(
                triggered=True,
                cause="SL_HIT",
                trigger_level=stop_loss,
                applied_exit_price=bid,
                price_basis="BID",
                evidence_reference=ev_id,
            )

        # 3. Take Profit check
        if take_profit is not None and bid >= take_profit:
            applied_tp = round(take_profit - tp_slip, 2)
            return ExitDecision(
                triggered=True,
                cause="TP_HIT",
                trigger_level=take_profit,
                applied_exit_price=applied_tp,
                price_basis="BID",
                evidence_reference=ev_id,
            )

    elif direction == "SHORT":
        if ask is None or not isinstance(ask, (int, float)):
            return ExitDecision(
                triggered=False,
                cause="",
                trigger_level=0.0,
                applied_exit_price=0.0,
                price_basis="NONE",
                unavailable_reason="MISSING_EXECUTABLE_SIDE",
            )

        # 1. Liquidation check
        if liquidation is not None and ask >= liquidation:
            return ExitDecision(
                triggered=True,
                cause="LIQUIDATED",
                trigger_level=liquidation,
                applied_exit_price=ask,
                price_basis="ASK",
                evidence_reference=ev_id,
            )

        # 2. Stop Loss check (gap price applied if ask > stop_loss)
        if stop_loss is not None and ask >= stop_loss:
            return ExitDecision(
                triggered=True,
                cause="SL_HIT",
                trigger_level=stop_loss,
                applied_exit_price=ask,
                price_basis="ASK",
                evidence_reference=ev_id,
            )

        # 3. Take Profit check
        if take_profit is not None and ask <= take_profit:
            applied_tp = round(take_profit + tp_slip, 2)
            return ExitDecision(
                triggered=True,
                cause="TP_HIT",
                trigger_level=take_profit,
                applied_exit_price=applied_tp,
                price_basis="ASK",
                evidence_reference=ev_id,
            )

    return ExitDecision(
        triggered=False,
        cause="",
        trigger_level=0.0,
        applied_exit_price=0.0,
        price_basis="NONE",
    )
