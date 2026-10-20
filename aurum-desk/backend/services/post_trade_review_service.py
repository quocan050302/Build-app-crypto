"""
backend/services/post_trade_review_service.py
V16.37 - Evidence-based Post-Trade Review & Lesson Hypotheses Engine.
PHẦN 121 - 140.

Provides:
- build_post_trade_review: Analyzes frozen entry evidence, execution metrics, and exit outcome.
  Classifies losses as STRATEGY_VALID_LOSS, EXECUTION_SLIPPAGE, DATA_ISSUE, or UNVERIFIABLE.
- summarize_lesson_evidence: Groups observations by model, regime, and session to form reviewable hypotheses.
- Strict safety rules:
  * Lessons always start in 'PROPOSED' / 'INACTIVE' status.
  * Lessons never automatically mutate SL/TP or active risk parameters.
  * No superstitious explanations ("cá mập quét SL", "bot sợ hãi"); only empirical facts.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class LossClassification(str, Enum):
    STRATEGY_VALID_LOSS = "STRATEGY_VALID_LOSS"
    EXECUTION_SLIPPAGE = "EXECUTION_SLIPPAGE"
    DATA_ISSUE = "DATA_ISSUE"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    UNVERIFIABLE = "UNVERIFIABLE"
    NOT_A_LOSS = "NOT_A_LOSS"


class LessonStatus(str, Enum):
    PROPOSED = "PROPOSED"
    INACTIVE = "INACTIVE"
    APPROVED = "APPROVED"
    DISABLED = "DISABLED"


class LessonSeverity(str, Enum):
    GREEN = "GREEN"      # Supporting evidence / reference
    YELLOW = "YELLOW"    # Warning to be investigated
    RED = "RED"          # Approved hard constraint (requires explicit approval)


@dataclass
class PostTradeReview:
    """Detailed post-trade review audit artifact (PHẦN 121 - 127)."""
    order_id: int
    symbol: str
    model_id: str
    direction: str
    planned_entry: float
    actual_entry: float
    planned_sl: float
    planned_tp: float
    exit_price: float
    exit_cause: str                          # "TP_HIT", "SL_HIT", "TIMEOUT", "MANUAL_CANCEL"
    gross_pnl: float
    net_pnl: float
    realized_rr: float
    hold_time_ms: int
    entry_slippage_usd: float
    loss_classification: str
    evidence_ids: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)
    proposed_lessons: List[Dict[str, Any]] = field(default_factory=list)
    as_of_ms: int = 0
    version: str = "v16.37"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "model_id": self.model_id,
            "direction": self.direction,
            "planned_entry": self.planned_entry,
            "actual_entry": self.actual_entry,
            "planned_sl": self.planned_sl,
            "planned_tp": self.planned_tp,
            "exit_price": self.exit_price,
            "exit_cause": self.exit_cause,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "realized_rr": self.realized_rr,
            "hold_time_ms": self.hold_time_ms,
            "entry_slippage_usd": self.entry_slippage_usd,
            "loss_classification": self.loss_classification,
            "evidence_ids": list(self.evidence_ids),
            "observations": list(self.observations),
            "proposed_lessons": list(self.proposed_lessons),
            "as_of_ms": self.as_of_ms,
            "version": self.version,
        }


def build_post_trade_review(
    order: Any,
    outcome: Dict[str, Any],
    entry_evidence: Optional[Dict[str, Any]] = None,
    now_ms: int = 0,
) -> PostTradeReview:
    """
    PHẦN 124 - 128: Builds empirical post-trade review from immutable order, outcome, and entry snapshot.
    Does NOT invent causes; distinguishes valid strategy loss from slippage or data anomalies.
    """
    ev = entry_evidence or {}
    order_id = getattr(order, "id", 0)
    symbol = getattr(order, "symbol", "XAUUSDT")
    model_id = getattr(order, "strategy_family", getattr(order, "model_id", "UNKNOWN"))
    direction = getattr(order, "direction", "LONG")

    planned_entry = float(getattr(order, "planned_entry", getattr(order, "entry_price", 0.0)) or 0.0)
    actual_entry = float(getattr(order, "actual_entry", getattr(order, "fill_price", planned_entry)) or planned_entry)
    planned_sl = float(getattr(order, "stop_loss", 0.0) or 0.0)
    planned_tp = float(getattr(order, "take_profit", 0.0) or 0.0)

    exit_price = float(outcome.get("exit_price", actual_entry) or actual_entry)
    exit_cause = str(outcome.get("exit_cause", "UNKNOWN"))
    gross_pnl = round(float(outcome.get("gross_pnl", 0.0)), 2)
    net_pnl = round(float(outcome.get("net_pnl", gross_pnl)), 2)

    risk_budget = abs(actual_entry - planned_sl)
    if risk_budget > 0:
        if direction in ("LONG", "BUY"):
            realized_rr = round((exit_price - actual_entry) / risk_budget, 2)
        else:
            realized_rr = round((actual_entry - exit_price) / risk_budget, 2)
    else:
        realized_rr = 0.0

    entry_slippage_usd = round(abs(actual_entry - planned_entry), 2)
    hold_time_ms = max(0, int(outcome.get("closed_at_ms", now_ms)) - int(getattr(order, "filled_at", now_ms) or now_ms))

    observations: List[str] = []
    proposed_lessons: List[Dict[str, Any]] = []

    # Classify loss / outcome
    if net_pnl >= 0:
        loss_cls = LossClassification.NOT_A_LOSS.value
        observations.append(f"Giao dịch có lãi ({net_pnl:+.2f} USDT, {realized_rr:+.2f}R). Kế hoạch đạt mục tiêu.")
    else:
        # Evaluate loss cause
        if entry_slippage_usd > 1.0:
            loss_cls = LossClassification.EXECUTION_SLIPPAGE.value
            observations.append(f"Trượt giá entry đáng kể ({entry_slippage_usd:.2f} USD).")
            proposed_lessons.append({
                "lesson_id": f"lesson_slip_{order_id}",
                "observation": f"Slippage {entry_slippage_usd:.2f} USD tại entry lệnh #{order_id}",
                "severity": LessonSeverity.YELLOW.value,
                "status": LessonStatus.PROPOSED.value,
                "proposed_action": "WARN_HIGH_SLIPPAGE",
                "affected_model": model_id,
            })
        elif exit_cause in ("SL_HIT", "STOP_LOSS"):
            loss_cls = LossClassification.STRATEGY_VALID_LOSS.value
            observations.append("Giá chạm ngưỡng cắt lỗ theo kế hoạch cấu trúc (SL hợp lệ của chiến lược).")
            proposed_lessons.append({
                "lesson_id": f"lesson_sl_{order_id}",
                "observation": f"Lệnh #{order_id} chạm SL theo cấu trúc dự kiến.",
                "severity": LessonSeverity.GREEN.value,
                "status": LessonStatus.PROPOSED.value,
                "proposed_action": "LOG_STRATEGY_SAMPLE",
                "affected_model": model_id,
            })
        elif "DATA" in exit_cause or "TIMEOUT" in exit_cause:
            loss_cls = LossClassification.DATA_ISSUE.value
            observations.append(f"Đóng lệnh do nguyên nhân dữ liệu/thời gian: {exit_cause}.")
        else:
            loss_cls = LossClassification.UNVERIFIABLE.value
            observations.append(f"Kết quả lỗ chưa xác định nguyên nhân chi tiết ({exit_cause}).")

    ev_ids = [str(x) for x in ev.get("evidence_ids", [])]

    return PostTradeReview(
        order_id=order_id,
        symbol=symbol,
        model_id=model_id,
        direction=direction,
        planned_entry=planned_entry,
        actual_entry=actual_entry,
        planned_sl=planned_sl,
        planned_tp=planned_tp,
        exit_price=exit_price,
        exit_cause=exit_cause,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        realized_rr=realized_rr,
        hold_time_ms=hold_time_ms,
        entry_slippage_usd=entry_slippage_usd,
        loss_classification=loss_cls,
        evidence_ids=ev_ids,
        observations=observations,
        proposed_lessons=proposed_lessons,
        as_of_ms=now_ms,
    )


def summarize_lesson_evidence(
    reviews: Sequence[PostTradeReview],
) -> Dict[str, Any]:
    """
    PHẦN 129: Groups reviews and counts statistics by model and outcome.
    Never merges disparate models into a single generic rule.
    """
    by_model: Dict[str, Dict[str, Any]] = {}

    for rev in reviews:
        m = rev.model_id
        if m not in by_model:
            by_model[m] = {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "valid_losses": 0,
                "slippage_losses": 0,
                "data_losses": 0,
                "total_net_pnl": 0.0,
                "realized_r_sum": 0.0,
                "proposed_lessons_count": 0,
            }
        stats = by_model[m]
        stats["total_trades"] += 1
        stats["total_net_pnl"] = round(stats["total_net_pnl"] + rev.net_pnl, 2)
        stats["realized_r_sum"] = round(stats["realized_r_sum"] + rev.realized_rr, 2)
        stats["proposed_lessons_count"] += len(rev.proposed_lessons)

        if rev.net_pnl >= 0:
            stats["wins"] += 1
        else:
            stats["losses"] += 1
            if rev.loss_classification == LossClassification.STRATEGY_VALID_LOSS.value:
                stats["valid_losses"] += 1
            elif rev.loss_classification == LossClassification.EXECUTION_SLIPPAGE.value:
                stats["slippage_losses"] += 1
            elif rev.loss_classification == LossClassification.DATA_ISSUE.value:
                stats["data_losses"] += 1

    return {
        "models": by_model,
        "total_reviews": len(reviews),
    }
