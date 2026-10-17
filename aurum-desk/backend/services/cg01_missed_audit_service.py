"""
backend/services/cg01_missed_audit_service.py
V16.14 - CG-01 Missed Opportunity Audit & Funnel Classification Service.

Enforces:
- Rules 091-100: Structured opportunity funnel stages and evidence-based reason classification.
- Rules 101-110: Causal sequential audit without lookahead / hindsight cherry-picking.
- Rule 094: Strict separation between intentional POLICY_BLOCKED and technical latency misses.
- Rule 097: ENTRY_NEVER_TOUCHED is not mislabeled as a detector failure.
- Appendix A #5: CG01MissedOpportunityAuditService.audit_run() contract.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy.orm import Session

import models
from services.scalp_m1_types import OpportunityReasonCode, OpportunityStage

logger = logging.getLogger(__name__)


@dataclass
class OpportunityAuditRecord:
    """Detailed audit classification for a single tracked opportunity."""
    opportunity_id: str
    symbol: str
    direction: str
    as_of_ms: int
    terminal_stage: str
    classification: str     # "FILLED", "POLICY_BLOCKED", "TECHNICAL_MISS", "MARKET_NON_FILL", "INVALIDATED"
    miss_reason: Optional[str] = None
    evidence_ids: List[str] = field(default_factory=list)
    timestamps: Dict[str, Optional[int]] = field(default_factory=dict)
    price_levels: Dict[str, Optional[float]] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CG01MissedOpportunityAuditService:
    """
    Appendix A #5: Audit service for CG-01 paper runs and opportunity analysis.
    Identifies exact stage where setups did not progress to a filled order,
    grounded in causal timestamps and structural facts.
    """

    @classmethod
    def classify_opportunity(
        cls,
        eval_records: Sequence[Dict[str, Any]],
        order: Optional[Dict[str, Any]] = None,
        max_retrace_delay_ms: int = 500,
    ) -> OpportunityAuditRecord:
        """
        Pure classification function for an opportunity progression (Rules 094-100).
        Evaluates sequence of stage evaluations and any associated paper order.
        """
        if not eval_records:
            return OpportunityAuditRecord(
                opportunity_id="UNKNOWN",
                symbol="XAUUSDT",
                direction="UNKNOWN",
                as_of_ms=0,
                terminal_stage="UNKNOWN",
                classification="UNVERIFIABLE",
                miss_reason="DATA_UNAVAILABLE",
                notes="No evaluation records found for opportunity",
            )

        # Sort chronologically by as_of_ms
        sorted_evals = sorted(eval_records, key=lambda x: int(x.get("as_of_ms") or 0))
        first_eval = sorted_evals[0]
        last_eval = sorted_evals[-1]

        opp_id = str(first_eval.get("opportunity_id") or "UNKNOWN")
        symbol = str(first_eval.get("symbol") or "XAUUSDT")
        direction = str(first_eval.get("direction") or "LONG")
        as_of_ms = int(first_eval.get("as_of_ms") or 0)

        all_stages = [e.get("stage") for e in sorted_evals]
        all_reasons = set()
        for e in sorted_evals:
            for r in e.get("reason_codes", []):
                all_reasons.add(r)

        price_levels = {
            "planned_entry": last_eval.get("planned_entry"),
            "stop_loss": last_eval.get("stop_loss"),
            "take_profit": last_eval.get("take_profit"),
        }

        timestamps = {
            "market_event_received_at": first_eval.get("market_event_received_at"),
            "evaluation_started_at": first_eval.get("evaluation_started_at"),
            "evaluation_completed_at": last_eval.get("evaluation_completed_at"),
            "armed_at": last_eval.get("armed_at"),
            "filled_at": last_eval.get("filled_at"),
        }

        evidence_ids = list(last_eval.get("evidence_ids", []))

        # 1. Did it successfully fill?
        if OpportunityStage.FILLED.value in all_stages or (order and order.get("state") in ("filled", "closed")):
            return OpportunityAuditRecord(
                opportunity_id=opp_id,
                symbol=symbol,
                direction=direction,
                as_of_ms=as_of_ms,
                terminal_stage=OpportunityStage.FILLED.value,
                classification="FILLED",
                miss_reason=None,
                evidence_ids=evidence_ids,
                timestamps=timestamps,
                price_levels=price_levels,
                notes="Order successfully reached filled state.",
            )

        # 2. Rule 094: Was it blocked by a guard / risk / quota / lesson policy?
        policy_reasons = {
            "GUARD_BLOCKED", "NEWS_BLACKOUT", "LESSON_POLICY_BLOCKED",
            "DAILY_LIMIT_REACHED", "ACTIVE_POSITION_EXISTS", "POLICY_BLOCKED",
            "SPREAD_EXCEEDED", "FEED_DEGRADED"
        }
        if OpportunityStage.GUARD_BLOCKED.value in all_stages or any(r in policy_reasons for r in all_reasons):
            matching_policy = [r for r in all_reasons if r in policy_reasons]
            reason = matching_policy[0] if matching_policy else "POLICY_BLOCKED"
            return OpportunityAuditRecord(
                opportunity_id=opp_id,
                symbol=symbol,
                direction=direction,
                as_of_ms=as_of_ms,
                terminal_stage=OpportunityStage.GUARD_BLOCKED.value,
                classification="POLICY_BLOCKED",
                miss_reason=reason,
                evidence_ids=evidence_ids,
                timestamps=timestamps,
                price_levels=price_levels,
                notes=f"Intentional policy block enforced by protective guards: {reason}.",
            )

        # 3. Rule 097: Was order armed, but market never retraced to entry?
        if OpportunityStage.ARMED.value in all_stages or (order and order.get("state") in ("armed", "expired")):
            if order and order.get("state") == "expired":
                return OpportunityAuditRecord(
                    opportunity_id=opp_id,
                    symbol=symbol,
                    direction=direction,
                    as_of_ms=as_of_ms,
                    terminal_stage=OpportunityStage.EXPIRED.value,
                    classification="MARKET_NON_FILL",
                    miss_reason="ENTRY_NEVER_TOUCHED",
                    evidence_ids=evidence_ids,
                    timestamps=timestamps,
                    price_levels=price_levels,
                    notes="Order was armed causally, but market price never touched planned entry before expiry.",
                )

        # 4. Technical / Latency Misses (Rules 095, 096)
        t_recv = timestamps.get("market_event_received_at")
        t_start = timestamps.get("evaluation_started_at")
        t_done = timestamps.get("evaluation_completed_at")

        if t_recv and t_start and (t_start - t_recv) > max_retrace_delay_ms:
            return OpportunityAuditRecord(
                opportunity_id=opp_id,
                symbol=symbol,
                direction=direction,
                as_of_ms=as_of_ms,
                terminal_stage=last_eval.get("stage", "UNKNOWN"),
                classification="TECHNICAL_MISS",
                miss_reason="DETECTION_LATE",
                evidence_ids=evidence_ids,
                timestamps=timestamps,
                price_levels=price_levels,
                notes=f"Scheduler delay exceeded budget: {t_start - t_recv}ms > {max_retrace_delay_ms}ms.",
            )

        if OpportunityReasonCode.ENTRY_TOO_FAR.value in all_reasons:
            return OpportunityAuditRecord(
                opportunity_id=opp_id,
                symbol=symbol,
                direction=direction,
                as_of_ms=as_of_ms,
                terminal_stage=last_eval.get("stage", "UNKNOWN"),
                classification="MARKET_NON_FILL",
                miss_reason="RUNAWAY",
                evidence_ids=evidence_ids,
                timestamps=timestamps,
                price_levels=price_levels,
                notes="Price moved away rapidly beyond valid entry retrace limit.",
            )

        # 5. Invalidated or expired before arm
        if OpportunityStage.INVALIDATED.value in all_stages or "INVALIDATED" in all_reasons:
            return OpportunityAuditRecord(
                opportunity_id=opp_id,
                symbol=symbol,
                direction=direction,
                as_of_ms=as_of_ms,
                terminal_stage=OpportunityStage.INVALIDATED.value,
                classification="INVALIDATED",
                miss_reason="INVALIDATED",
                evidence_ids=evidence_ids,
                timestamps=timestamps,
                price_levels=price_levels,
                notes="Setup invalidated by opposing structure break prior to arming.",
            )

        # Default fallback
        term_stage = last_eval.get("stage", "UNKNOWN")
        return OpportunityAuditRecord(
            opportunity_id=opp_id,
            symbol=symbol,
            direction=direction,
            as_of_ms=as_of_ms,
            terminal_stage=term_stage,
            classification="MARKET_NON_FILL",
            miss_reason="TRIGGER_EXPIRED",
            evidence_ids=evidence_ids,
            timestamps=timestamps,
            price_levels=price_levels,
            notes=f"Opportunity concluded at stage {term_stage} without filling.",
        )

    @classmethod
    def audit_run(
        cls,
        run_id: str,
        *,
        db: Optional[Session] = None,
        records: Optional[Sequence[Dict[str, Any]]] = None,
        orders: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Audit a paper run's opportunities and generate structured bottleneck breakdown (Rules 101-110).
        """
        all_evals: List[Dict[str, Any]] = []
        orders_map: Dict[str, Dict[str, Any]] = {}

        if orders:
            for o in orders:
                setup_id = o.get("setup_instance_id") or o.get("signal_id") or o.get("id")
                if setup_id:
                    orders_map[str(setup_id)] = o

        if records:
            all_evals.extend(records)
        elif db is not None:
            # Query OpportunityFunnelRecord from DB
            rows = db.query(models.OpportunityFunnelRecord).filter(
                models.OpportunityFunnelRecord.session_id == run_id
            ).all()
            for r in rows:
                try:
                    payload = json.loads(r.payload_json) if r.payload_json else {}
                except Exception:
                    payload = {}
                payload.setdefault("opportunity_id", r.opportunity_id)
                payload.setdefault("stage", r.stage)
                payload.setdefault("symbol", r.symbol)
                payload.setdefault("direction", r.direction)
                payload.setdefault("as_of_ms", r.as_of_ms)
                try:
                    payload["reason_codes"] = json.loads(r.reason_codes_json or "[]")
                except Exception:
                    payload["reason_codes"] = []
                all_evals.append(payload)

            # Query PaperOrders
            db_orders = db.query(models.PaperOrder).filter(
                models.PaperOrder.origin.in_(["AUTO_STRATEGY", "CG01", run_id])
            ).all()
            for o in db_orders:
                orders_map[o.id] = {
                    "id": o.id,
                    "state": o.state,
                    "direction": o.direction,
                    "setup_instance_id": o.setup_instance_id,
                }

        # Group by opportunity_id
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for ev in all_evals:
            opp_id = str(ev.get("opportunity_id") or "UNKNOWN")
            grouped.setdefault(opp_id, []).append(ev)

        audits: List[OpportunityAuditRecord] = []
        for opp_id, ev_list in grouped.items():
            matching_order = None
            for oid, od in orders_map.items():
                if opp_id in oid or (od.get("setup_instance_id") and od["setup_instance_id"] in opp_id):
                    matching_order = od
                    break
            audits.append(cls.classify_opportunity(ev_list, order=matching_order))

        # Metrics aggregation
        total_unique = len(audits)
        filled_count = sum(1 for a in audits if a.classification == "FILLED")
        policy_blocked_count = sum(1 for a in audits if a.classification == "POLICY_BLOCKED")
        technical_miss_count = sum(1 for a in audits if a.classification == "TECHNICAL_MISS")
        market_non_fill_count = sum(1 for a in audits if a.classification == "MARKET_NON_FILL")
        invalidated_count = sum(1 for a in audits if a.classification == "INVALIDATED")

        # Bottlenecks by reason
        reason_counts: Dict[str, int] = {}
        for a in audits:
            if a.miss_reason:
                reason_counts[a.miss_reason] = reason_counts.get(a.miss_reason, 0) + 1

        sorted_bottlenecks = sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        bottlenecks_summary = [
            {
                "reason": k,
                "count": v,
                "pct_of_total": round((v / max(1, total_unique)) * 100.0, 1),
            }
            for k, v in sorted_bottlenecks
        ]

        # Representative examples: Long and Short (Rule 109)
        long_example = next((a.to_dict() for a in audits if a.direction == "LONG"), None)
        short_example = next((a.to_dict() for a in audits if a.direction == "SHORT"), None)

        return {
            "run_id": run_id,
            "total_unique_opportunities": total_unique,
            "actual_filled_count": filled_count,
            "policy_blocked_count": policy_blocked_count,
            "technical_miss_count": technical_miss_count,
            "market_non_fill_count": market_non_fill_count,
            "invalidated_count": invalidated_count,
            "capture_rate_pct": round((filled_count / max(1, total_unique)) * 100.0, 2),
            "top_bottlenecks": bottlenecks_summary,
            "representative_examples": {
                "long": long_example,
                "short": short_example,
            },
        }
