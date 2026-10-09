"""
Aurum Desk V10.1 - Authoritative Entry Decision Service
Unifies governed lesson rule evaluation and decision recording across all entry paths:
- Manual Market (POST /api/v1/orders/paper)
- Manual Arm (POST /api/v1/setups/arm)
- Auto Arm (StrategyService._auto_arm_candidate)
- Pending Fill (ExecutionCoordinator.evaluate_orders_sync)
- NY Fallback Arm & Fill (NYFallbackService)

Guarantees:
1. Baseline guards (max 3 fills/day, max 1 open pos, max 1 armed order, risk limits) remain independent.
2. Canonical typed execution context with distinct session label and session_instance_id.
3. Strict error policy: no silent passes; active entry-blocking failures fail-safe with diagnostics.
4. Independent decision traces: arm_decision_snapshot vs fill_decision_snapshot.
"""

import time
import json
import uuid
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

import models
from services.lesson_rule_service import LessonRuleService
from services.lesson_policy_service import LessonPolicyService

logger = logging.getLogger(__name__)


class EntryDecisionService:
    @staticmethod
    def build_context(
        stage: str,
        symbol: str = "XAUUSDT",
        direction: str = "LONG",
        strategy_family: str = "STANDARD_SMC",
        timeframe: Optional[str] = "15M",
        execution_mode: str = "AUTO",
        origin: str = "UNKNOWN",
        planned_entry: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        net_rr: Optional[float] = None,
        gross_rr: Optional[float] = None,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        spread: Optional[float] = None,
        now_ms: Optional[int] = None,
        session_instance_id: Optional[str] = None,
        session_tag: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        distance_to_entry_atr: Optional[float] = None,
        quote_age_ms: Optional[int] = None,
        setup_id: Optional[str] = None,
        order_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Builds a canonical, normalized execution context for lesson rule evaluation.
        Distinguishes general session tags ('ASIA', 'LONDON', 'NY') from session_instance_id.
        """
        current_now = now_ms if now_ms is not None else int(time.time() * 1000)

        # Derive session tag if not explicitly provided
        derived_sessions = LessonRuleService.get_current_session_tags(current_now)
        primary_session = session_tag or (derived_sessions[0] if derived_sessions else "NY")

        # Derive spread
        effective_spread = spread
        if effective_spread is None and ask is not None and bid is not None:
            effective_spread = round(float(ask) - float(bid), 2)

        return {
            "stage": stage,
            "symbol": symbol,
            "direction": direction,
            "strategy_family": strategy_family or "STANDARD_SMC",
            "timeframe": timeframe,
            "execution_mode": execution_mode or "UNKNOWN",
            "origin": origin or "UNKNOWN",
            "planned_entry": planned_entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "net_rr": net_rr,
            "gross_rr": gross_rr,
            "bid": bid,
            "ask": ask,
            "spread": effective_spread,
            "now_ms": current_now,
            "session": primary_session,
            "session_tags": derived_sessions,
            "session_instance_id": session_instance_id,
            "evidence": evidence or {},
            "distance_to_entry_atr": distance_to_entry_atr,
            "quote_age_ms": quote_age_ms,
            "setup_id": setup_id,
            "order_id": order_id,
            **kwargs,
        }

    @classmethod
    def build_context_from_order(
        cls,
        order: models.PaperOrder,
        stage: str,
        fill_price: Optional[float] = None,
        calc_result: Optional[Any] = None,
        now_ms: Optional[int] = None,
        current_bid: Optional[float] = None,
        current_ask: Optional[float] = None,
        quote_age_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Convenience builder to construct context from an existing PaperOrder.
        Preserves original execution_mode and origin.
        """
        effective_now = now_ms if now_ms is not None else int(time.time() * 1000)
        p_entry = fill_price if fill_price is not None else (order.actual_entry or order.planned_entry)
        net_rr = getattr(calc_result, "net_rr", order.estimated_net_rr)
        gross_rr = getattr(calc_result, "gross_rr", order.gross_rr)

        exec_mode = getattr(order, "execution_mode", None) or "AUTO"
        origin = getattr(order, "origin", None) or "UNKNOWN"

        effective_bid = current_bid
        effective_ask = current_ask
        if effective_bid is None or effective_ask is None:
            try:
                from services.collector_service import collector_service
                if getattr(collector_service, "latest_ticker", None):
                    t_bid = collector_service.latest_ticker.get("bid")
                    t_ask = collector_service.latest_ticker.get("ask")
                    if effective_bid is None:
                        effective_bid = t_bid
                    if effective_ask is None:
                        effective_ask = t_ask
            except Exception:
                pass

        return cls.build_context(
            stage=stage,
            symbol=order.instrument or "XAUUSDT",
            direction=order.direction,
            strategy_family=getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
            timeframe=getattr(order, "timeframe", "15M"),
            execution_mode=exec_mode,
            origin=origin,
            planned_entry=p_entry,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            net_rr=net_rr,
            gross_rr=gross_rr,
            bid=effective_bid,
            ask=effective_ask,
            now_ms=effective_now,
            session_instance_id=order.session_instance_id,
            quote_age_ms=quote_age_ms,
            setup_id=order.setup_id,
            order_id=order.id,
        )

    @classmethod
    def evaluate_entry_rules(
        cls,
        db: Session,
        context: Dict[str, Any],
        policy_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Authoritative evaluation entry point for all stages (BEFORE_ARM, BEFORE_FILL).
        Enforces server policy flags and strict error handling (no silent bypass).
        """
        now_ms = context.get("now_ms") or int(time.time() * 1000)
        policy = policy_snapshot or LessonPolicyService.get_policy(db)
        correlation_id = f"diag-{uuid.uuid4().hex[:8]}"

        try:
            active_rules = LessonRuleService.retrieve_active_rules(db, context, decision_time=now_ms)
            eval_result = LessonRuleService.evaluate_rules(context, active_rules, feature_flags=policy)
            eval_result["correlation_id"] = correlation_id
            eval_result["evaluated_at"] = now_ms
            eval_result["stage"] = context.get("stage", "BEFORE_ARM")
            eval_result["policy_version"] = policy.get("version", 1)
            return eval_result

        except Exception as e:
            logger.error(
                f"[{correlation_id}] Exception during entry rule evaluation for stage {context.get('stage')}: {e}",
                exc_info=True,
            )
            entry_blocking_active = policy.get("lesson_entry_rules_enabled", True) and not policy.get(
                "lesson_shadow_mode", False
            )

            if entry_blocking_active:
                # X01, X02: Active blocking mode must fail safe with clear reason and diagnostic ID
                return {
                    "can_proceed": False,
                    "blocking_reasons": [
                        f"LESSON_EVALUATION_FAILED: Chưa kiểm tra được quy tắc bài học ({correlation_id})"
                    ],
                    "warning_messages": [],
                    "advisory_notes": [],
                    "matched_rules": [],
                    "evaluations": [],
                    "lessons_retrieved_snapshot": [],
                    "correlation_id": correlation_id,
                    "evaluated_at": now_ms,
                    "stage": context.get("stage", "BEFORE_ARM"),
                    "error": str(e),
                }
            else:
                # Advisory or Shadow mode: allow baseline decision but log diagnostic warning
                return {
                    "can_proceed": True,
                    "blocking_reasons": [],
                    "warning_messages": [
                        f"Chẩn đoán đánh giá bài học: Gặp sự cố tạm thời ({correlation_id})"
                    ],
                    "advisory_notes": [],
                    "matched_rules": [],
                    "evaluations": [],
                    "lessons_retrieved_snapshot": [],
                    "correlation_id": correlation_id,
                    "evaluated_at": now_ms,
                    "stage": context.get("stage", "BEFORE_ARM"),
                    "error": str(e),
                }

    @staticmethod
    def record_stage_decision(
        order: models.PaperOrder,
        stage: str,
        eval_result: Dict[str, Any],
        now_ms: Optional[int] = None,
    ):
        """
        Persists stage decision snapshots cleanly without overwriting prior evidence (Finding M).
        Sets arm_decision_snapshot or fill_decision_snapshot and updates chronological lessons_retrieved.
        """
        current_time = now_ms if now_ms is not None else int(time.time() * 1000)
        snapshot_json = json.dumps(eval_result)

        if stage == "BEFORE_ARM":
            order.arm_decision_snapshot = snapshot_json
        elif stage == "BEFORE_FILL":
            order.fill_decision_snapshot = snapshot_json

        # Maintain chronological lessons_retrieved list
        history: List[Dict[str, Any]] = []
        if order.lessons_retrieved:
            try:
                parsed = json.loads(order.lessons_retrieved)
                if isinstance(parsed, list):
                    history = parsed
                elif isinstance(parsed, dict):
                    history = [parsed]
            except Exception:
                history = []

        history.append({
            "stage": stage,
            "recorded_at": current_time,
            "can_proceed": eval_result.get("can_proceed", True),
            "correlation_id": eval_result.get("correlation_id"),
            "snapshot": eval_result.get("lessons_retrieved_snapshot", []),
            "warning_messages": eval_result.get("warning_messages", []),
            "blocking_reasons": eval_result.get("blocking_reasons", []),
        })

        order.lessons_retrieved = json.dumps(history)
