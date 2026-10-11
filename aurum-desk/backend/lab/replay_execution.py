"""
AURUM DESK — REPLAY EXECUTION SIMULATOR (V13.5)
Strictly simulates:
- Order submission (pending order creation without altering cash or quota)
- Fill simulation (next open/quote, adverse spread/slippage, post-fill geometry & Net R:R revalidation)
- Position exit simulation (gap adverse exits, conservative ambiguous resolution, post-fill only)
- Production accounting (gross pnl, entry/exit fees, net pnl, realized R frozen at fill)
"""

import uuid
from typing import Dict, Any, Optional, Tuple, List
from lab.replay_contracts import ReplayPendingOrder, ReplayMarketEvent, ReplayPosition, validate_direction
from lab.replay_evidence_utils import TERMINAL_ORDER_STATUSES, ACTIVE_ORDER_STATUSES, normalize_order_status, stable_id
from domain_calculator import calculate_risk_reward, validate_price_geometry, CostAssumptions


def submit_replay_order(
    candidate_plan: Optional[Dict[str, Any]] = None,
    session_id: str = "",
    decision_ms: int = 0,
    earliest_execution_ms: int = 0,
    expiry_ms: int = 0,
    entry_type: str = "SMC_CONTEXT_SCHEDULED_PAPER",
    candidate: Optional[Dict[str, Any]] = None,
    leverage: int = 30,
    margin_mode: str = "ISOLATED",
    **kwargs
) -> ReplayPendingOrder:
    """
    PHẦN 35, 117: Creates an immutable pending order from a candidate plan.
    Does NOT modify cash ledger, equity, or session fills count!
    Accepts both candidate_plan and candidate parameter aliases.
    """
    plan = candidate if candidate is not None else (candidate_plan or {})
    order_id = f"ord-{uuid.uuid4().hex[:8]}"
    decision_ts = decision_ms if decision_ms > 0 else int(kwargs.get("sim_time", kwargs.get("decision_time", plan.get("decision_ms", 0))))
    earliest_ms = max(earliest_execution_ms, decision_ts)
    setup_id = plan.get("setup_id", f"setup-{decision_ts}")
    calc = plan.get("calc")

    calc_qty = getattr(calc, "quantity", None) or getattr(calc, "position_size", None)
    qty = float(calc_qty if calc_qty is not None else plan.get("quantity", 0.0))
    net_rr = float(getattr(calc, "net_rr", plan.get("planned_net_rr", 0.0)))

    planned_entry = float(plan.get("planned_entry") if plan.get("planned_entry") is not None else plan.get("entry_price", 0.0))
    planned_sl = float(plan.get("planned_sl") if plan.get("planned_sl") is not None else plan.get("stop_loss", 0.0))
    planned_tp = float(plan.get("planned_tp") if plan.get("planned_tp") is not None else plan.get("take_profit", 0.0))

    lev = kwargs.get("leverage", leverage or plan.get("leverage", 30))
    mm = kwargs.get("margin_mode", margin_mode or plan.get("margin_mode", "ISOLATED"))

    return ReplayPendingOrder(
        order_id=order_id,
        setup_id=setup_id,
        session_id=session_id,
        entry_type=entry_type,
        direction=plan["direction"],
        decision_ms=decision_ts,
        earliest_execution_ms=earliest_ms,
        expiry_ms=expiry_ms,
        planned_entry=planned_entry,
        planned_sl=planned_sl,
        planned_tp=planned_tp,
        planned_net_rr=net_rr,
        quantity=qty,
        leverage=lev,
        margin_mode=mm,
        stop_model=plan.get("stop_model", "STRUCTURAL"),
        target_model=plan.get("target_model", "STRUCTURAL"),
        target_source=plan.get("target_source", "UNKNOWN"),
        status="SUBMITTED",
        created_at_ms=decision_ms,
        missing_confirmations=plan.get("missing_confirmations"),
        confidence_kind=plan.get("confidence_kind"),
        entry_model=plan.get("entry_model"),
        reason=plan.get("reason")
    )


def reject_order(order: ReplayPendingOrder, reason_code: str) -> Tuple[None, None, str]:
    status = normalize_order_status(order.status)
    if status in TERMINAL_ORDER_STATUSES:
        return None, None, f"TERMINAL_ORDER:{status}"
    order.status = "REJECTED"
    order.rejection_reason = reason_code
    return None, None, reason_code


def resolve_exit_fee_rate(exit_cause: str, costs: CostAssumptions, is_ambiguous: bool = False) -> float:
    maker_tp = (exit_cause == "TAKE_PROFIT"
                and bool(getattr(costs, "tp_is_maker", False))
                and not is_ambiguous)
    return costs.maker_fee_rate if maker_tp else costs.taker_fee_rate


def try_fill_pending_order(
    order: ReplayPendingOrder,
    event: ReplayMarketEvent,
    costs: CostAssumptions,
    capital: float,
    risk_pct: float,
    spread_usd: float = 0.35,
    min_net_rr: float = 2.0
) -> Tuple[Optional[ReplayPosition], Optional[Dict[str, Any]], str]:
    """
    PHẦN 36, 37, 38: Simulates realistic execution at valid market event.
    Revalidates geometry and Net R:R after adverse spread and slippage.
    Returns (position, entry_fee_posting, status_reason).
    """
    status = normalize_order_status(order.status)
    if status in TERMINAL_ORDER_STATUSES:
        return None, None, f"TERMINAL_ORDER:{status}"
    if status not in ACTIVE_ORDER_STATUSES:
        raise ValueError(f"INVALID_FILL_ORDER_STATUS:{status}")
    if event.kind != "OPEN":
        return None, None, "WAITING_EXECUTION_EVENT"

    validate_direction(order.direction)

    # PHẦN 26, 47, 102: Must not execute before decision_ms or earliest_execution_ms
    min_exec_ts = max(order.earliest_execution_ms, order.decision_ms)
    if event.timestamp < min_exec_ts:
        return None, None, "WAITING_EARLIEST_EXECUTION"

    if order.expiry_ms > 0 and event.timestamp >= order.expiry_ms:
        order.status = "EXPIRED"
        order.rejection_reason = "EXPIRED_BEFORE_EXECUTION"
        return None, None, "EXPIRED"

    # Execution at open price with adverse spread & slippage
    if order.direction == "LONG":
        fill_price = round(event.open_price + (0.5 * spread_usd) + costs.slippage_usd, 2)
    else:
        fill_price = round(event.open_price - (0.5 * spread_usd) - costs.slippage_usd, 2)

    # Re-validate geometry with actual fill price
    is_geom_valid, geom_err = validate_price_geometry(order.direction, fill_price, order.planned_sl, order.planned_tp)
    if not is_geom_valid:
        return reject_order(order, f"POST_FILL_GEOMETRY_INVALID: {geom_err}")

    # Re-calculate Net R:R with actual fill price
    calc = calculate_risk_reward(
        direction=order.direction,
        entry=fill_price,
        sl=order.planned_sl,
        tp=order.planned_tp,
        capital=capital,
        risk_pct=risk_pct,
        costs=costs,
        entry_has_slippage=True,
        leverage=order.leverage,
        margin_mode=order.margin_mode,
        min_net_rr=min_net_rr
    )

    if not calc.can_execute or calc.net_rr < min_net_rr:
        return reject_order(order, f"POST_FILL_NET_RR_{calc.net_rr:.2f}_BELOW_{min_net_rr}")

    # Initial risk in USDT frozen at fill time
    risk_val = getattr(calc, "net_risk_usdt", None) or getattr(calc, "budget_usdt", 0.0)
    initial_risk_usdt = round(float(risk_val), 2)
    if initial_risk_usdt <= 0.0:
        initial_risk_usdt = round(capital * risk_pct / 100.0, 2)

    calc_qty = getattr(calc, "quantity", None) or getattr(calc, "position_size", 0.0)
    qty = float(calc_qty)
    entry_fee = round(fill_price * qty * costs.taker_fee_rate, 4)

    order.status = "FILLED"
    order.executed_at_ms = event.timestamp
    order.fill_price = fill_price

    position_id = f"pos-{uuid.uuid4().hex[:8]}"
    position = ReplayPosition(
        position_id=position_id,
        order_id=order.order_id,
        setup_id=order.setup_id,
        session_id=order.session_id,
        entry_type=order.entry_type,
        direction=order.direction,
        entry_time=event.timestamp,
        entry_price=fill_price,
        stop_loss=order.planned_sl,
        take_profit=order.planned_tp,
        quantity=qty,
        leverage=order.leverage,
        margin_mode=order.margin_mode,
        initial_risk_usdt=initial_risk_usdt,
        planned_net_rr=calc.net_rr,
        entry_fee=entry_fee,
        stop_model=order.stop_model,
        target_model=order.target_model,
        target_source=order.target_source,
        status="OPEN",
        decision_time=order.decision_ms,
        execution_time=event.timestamp,
        entry_slippage=round(qty * costs.slippage_usd, 4),
        net_rr_fill=calc.net_rr,
        gross_rr=calc.gross_rr,
        net_risk_usdt=calc.net_risk_usdt,
        net_reward_usdt=calc.net_reward_usdt,
        ny_session_id=order.session_id,
        missing_confirmations=order.missing_confirmations,
        confidence_kind=order.confidence_kind,
        entry_model=order.entry_model,
        reason=order.reason
    )

    entry_posting = {
        "posting_id": f"post-{uuid.uuid4().hex[:8]}",
        "trade_id": position_id,
        "event_id": f"evt-{event.sequence}",
        "posting_type": "ENTRY_FEE",
        "amount": -entry_fee,
        "amount_usdt": -entry_fee,
        "timestamp": event.timestamp,
        "description": f"Entry taker fee for {position.direction} at {fill_price}"
    }

    return position, entry_posting, "FILLED"


def evaluate_position_exit(
    position: ReplayPosition,
    event: ReplayMarketEvent,
    costs: CostAssumptions,
    multiplier: float = 1.0
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    PHẦN 40, 42, 43, 44: Evaluates position exit against a new market bar.
    Conservative resolution: if both SL and TP touched in same candle, resolves to STOP_LOSS.
    """
    if position.status != "OPEN":
        return None, f"POSITION_ALREADY_{position.status}"
    if position.direction not in ("LONG", "SHORT"):
        return None, "INVALID_DIRECTION"
    if event.timestamp < position.entry_time:
        return None, "PRE_ENTRY_EVENT"

    position.holding_bars += 1

    hit_tp = False
    hit_sl = False

    if position.direction == "LONG":
        if event.high_price >= position.take_profit:
            hit_tp = True
        if event.low_price <= position.stop_loss:
            hit_sl = True
    else:  # SHORT
        if event.low_price <= position.take_profit:
            hit_tp = True
        if event.high_price >= position.stop_loss:
            hit_sl = True

    if not hit_tp and not hit_sl:
        return None, "STILL_OPEN"

    is_ambiguous = hit_tp and hit_sl
    if is_ambiguous or hit_sl:
        exit_cause = "STOP_LOSS"
        # Preserve adverse gap execution even under ambiguous same-candle hits (PHẦN 36)
        if position.direction == "LONG":
            exit_fill = min(position.stop_loss, event.open_price) if event.open_price < position.stop_loss else position.stop_loss
        else:
            exit_fill = max(position.stop_loss, event.open_price) if event.open_price > position.stop_loss else position.stop_loss
    else:
        exit_cause = "TAKE_PROFIT"
        exit_fill = position.take_profit

    # Adverse slippage on exit (PHẦN 37)
    if exit_cause == "STOP_LOSS":
        if position.direction == "LONG":
            exit_fill = round(exit_fill - costs.slippage_usd, 2)
        else:
            exit_fill = round(exit_fill + costs.slippage_usd, 2)
    else:  # TAKE_PROFIT
        if not getattr(costs, "tp_is_maker", False):
            tp_slip = getattr(costs, "tp_slippage_usd", costs.slippage_usd)
            if position.direction == "LONG":
                exit_fill = round(exit_fill - tp_slip, 2)
            else:
                exit_fill = round(exit_fill + tp_slip, 2)

    accounting = compute_closed_trade_accounting(
        position=position,
        exit_price=exit_fill,
        exit_time=event.timestamp,
        exit_cause=exit_cause,
        costs=costs,
        multiplier=multiplier,
        is_ambiguous=is_ambiguous
    )

    return accounting, exit_cause


def compute_closed_trade_accounting(
    position: ReplayPosition,
    exit_price: float,
    exit_time: int,
    exit_cause: str,
    costs: CostAssumptions,
    multiplier: float = 1.0,
    is_ambiguous: bool = False
) -> Dict[str, Any]:
    """
    PHẦN 44: Production trade accounting calculation.
    Exact formula:
    gross_pnl = sign * (exit_fill - entry_fill) * qty * multiplier
    net_pnl = gross_pnl - entry_fee - exit_fee
    realized_r = net_pnl / initial_risk_usdt
    """
    sign = 1.0 if position.direction == "LONG" else -1.0
    gross_pnl = round(sign * (exit_price - position.entry_price) * position.quantity * multiplier, 4)
    fee_rate = resolve_exit_fee_rate(exit_cause, costs, is_ambiguous)
    exit_fee = round(exit_price * position.quantity * fee_rate * multiplier, 4)
    net_pnl = round(gross_pnl - position.entry_fee - exit_fee, 4)

    initial_risk = position.initial_risk_usdt
    if initial_risk > 0.0:
        realized_r = round(net_pnl / initial_risk, 2)
    else:
        realized_r = None

    position.status = "CLOSED"
    position.exit_time = exit_time
    position.exit_price = exit_price
    position.exit_cause = exit_cause
    position.exit_fee = exit_fee
    position.gross_pnl = gross_pnl
    position.net_pnl = net_pnl
    position.realized_r = realized_r
    position.is_ambiguous = is_ambiguous

    postings = [
        {
            "posting_id": f"post-{uuid.uuid4().hex[:8]}",
            "trade_id": position.position_id,
            "event_id": f"exit-{exit_time}",
            "posting_type": "REALIZED_GROSS_PNL",
            "amount": gross_pnl,
            "amount_usdt": gross_pnl,
            "timestamp": exit_time,
            "description": f"Realized gross PnL for {position.direction} closed at {exit_price}"
        },
        {
            "posting_id": f"post-{uuid.uuid4().hex[:8]}",
            "trade_id": position.position_id,
            "event_id": f"exit-{exit_time}",
            "posting_type": "EXIT_FEE",
            "amount": -exit_fee,
            "amount_usdt": -exit_fee,
            "timestamp": exit_time,
            "description": f"Exit taker fee for {position.direction} closed at {exit_price}"
        }
    ]

    return {
        "position": position,
        "exit_price": exit_price,
        "exit_time": exit_time,
        "exit_cause": exit_cause,
        "gross_pnl": gross_pnl,
        "entry_fee": position.entry_fee,
        "exit_fee": exit_fee,
        "total_fees": round(position.entry_fee + exit_fee, 4),
        "net_pnl": net_pnl,
        "realized_r": realized_r,
        "initial_risk_usdt": initial_risk,
        "is_ambiguous": is_ambiguous,
        "postings": postings
    }
