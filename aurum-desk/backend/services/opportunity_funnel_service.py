"""
backend/services/opportunity_funnel_service.py
V15.0 - Opportunity Funnel Tracking & Reporting Service (PHẦN 026 - 032).

Provides:
- record_opportunity_transition: Deduplicated transition tracking with latency metrics.
- summarize_opportunity_funnel: Structured statistical summary of the funnel progression.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy.orm import Session

import models
from services.scalp_m1_types import OpportunityEvaluation, OpportunityStage

logger = logging.getLogger(__name__)


class OpportunityFunnelService:
    @staticmethod
    def record_opportunity_transition(
        db: Optional[Session],
        eval_data: OpportunityEvaluation,
        *,
        now_ms: Optional[int] = None,
    ) -> OpportunityEvaluation:
        """
        Record or update an opportunity transition in the funnel (PHẦN 028, 029).
        Idempotent by (opportunity_id, stage): updates timestamps and latencies if row exists.
        """
        t_now = int(now_ms if now_ms is not None else (eval_data.as_of_ms or 0))

        if db is not None:
            try:
                row = db.query(models.OpportunityFunnelRecord).filter(
                    models.OpportunityFunnelRecord.opportunity_id == eval_data.opportunity_id,
                    models.OpportunityFunnelRecord.stage == eval_data.stage,
                ).first()

                reasons_str = json.dumps(list(eval_data.reason_codes), ensure_ascii=False)
                evidence_str = json.dumps(list(eval_data.evidence_ids), ensure_ascii=False)
                metrics_str = json.dumps({
                    "market_event_received_at": eval_data.market_event_received_at,
                    "evaluation_started_at": eval_data.evaluation_started_at,
                    "evaluation_completed_at": eval_data.evaluation_completed_at,
                    "trigger_known_at": eval_data.trigger_known_at,
                    "armed_at": eval_data.armed_at,
                    "filled_at": eval_data.filled_at,
                })
                payload_str = json.dumps(eval_data.to_dict(), ensure_ascii=False)

                eff_run_id = eval_data.run_id
                if not eff_run_id:
                    try:
                        from services.paper_research_run_service import resolve_active_observation_run
                        act_run = resolve_active_observation_run(db, symbol=eval_data.symbol)
                        if act_run:
                            eff_run_id = act_run.run_id
                    except Exception:
                        pass

                if row is None:
                    row = models.OpportunityFunnelRecord(
                        opportunity_id=eval_data.opportunity_id,
                        symbol=eval_data.symbol,
                        model_id=eval_data.model_id,
                        model_version=eval_data.model_version,
                        direction=eval_data.direction,
                        session_id=eval_data.session_id,
                        run_id=eff_run_id,
                        zone_id=eval_data.zone_id,
                        as_of_ms=eval_data.as_of_ms,
                        stage=eval_data.stage,
                        reason_codes_json=reasons_str,
                        evidence_ids_json=evidence_str,
                        metrics_json=metrics_str,
                        payload_json=payload_str,
                        created_at=t_now,
                        updated_at=t_now,
                    )
                    db.add(row)
                else:
                    row.updated_at = t_now
                    if eff_run_id and not getattr(row, "run_id", None):
                        row.run_id = eff_run_id
                    row.reason_codes_json = reasons_str
                    row.evidence_ids_json = evidence_str
                    row.metrics_json = metrics_str
                    row.payload_json = payload_str
                db.flush()
            except Exception as e:
                logger.warning(f"Error persisting OpportunityFunnelRecord: {e}")

        return eval_data

    @staticmethod
    def summarize_opportunity_funnel(
        db: Optional[Session],
        *,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
        model_id: Optional[str] = None,
        session_id: Optional[str] = None,
        records: Optional[Sequence[OpportunityEvaluation]] = None,
    ) -> Dict[str, Any]:
        """
        Pure and database-backed funnel summary (PHẦN 032).
        Returns counts for zones detected, touched, confirmed, blocked, armed, filled,
        top rejection reasons, and latency percentiles.
        """
        raw_items: List[Dict[str, Any]] = []

        if records:
            for r in records:
                raw_items.append(r.to_dict())
        elif db is not None:
            query = db.query(models.OpportunityFunnelRecord)
            if start_ms is not None:
                query = query.filter(models.OpportunityFunnelRecord.as_of_ms >= start_ms)
            if end_ms is not None:
                query = query.filter(models.OpportunityFunnelRecord.as_of_ms <= end_ms)
            if model_id is not None:
                query = query.filter(models.OpportunityFunnelRecord.model_id == model_id)
            if session_id is not None:
                query = query.filter(models.OpportunityFunnelRecord.session_id == session_id)

            rows = query.all()
            for row in rows:
                try:
                    payload = json.loads(row.payload_json)
                except Exception:
                    payload = {
                        "opportunity_id": row.opportunity_id,
                        "stage": row.stage,
                        "model_id": row.model_id,
                        "reason_codes": json.loads(row.reason_codes_json or "[]"),
                    }
                raw_items.append(payload)

        # Stage aggregations
        unique_opps = set()
        stage_counts: Dict[str, int] = {s.value: 0 for s in OpportunityStage}
        reason_counts: Dict[str, int] = {}
        decision_latencies: List[float] = []

        for item in raw_items:
            opp_id = item.get("opportunity_id")
            if opp_id:
                unique_opps.add(opp_id)
            stage = item.get("stage", "")
            if stage in stage_counts:
                stage_counts[stage] += 1

            for r in item.get("reason_codes", []):
                reason_counts[r] = reason_counts.get(r, 0) + 1

            t_start = item.get("evaluation_started_at")
            t_done = item.get("evaluation_completed_at")
            if t_start and t_done and t_done >= t_start:
                decision_latencies.append(float(t_done - t_start))

        # Latency metrics calculation
        decision_latencies.sort()
        lat_median = None
        lat_p95 = None
        if decision_latencies:
            n = len(decision_latencies)
            lat_median = round(decision_latencies[n // 2], 2)
            p95_idx = min(int(n * 0.95), n - 1)
            lat_p95 = round(decision_latencies[p95_idx], 2)

        sorted_reasons = sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top_reasons = [{"code": k, "count": v} for k, v in sorted_reasons[:5]]

        return {
            "total_unique_opportunities": len(unique_opps),
            "zones_detected": stage_counts.get(OpportunityStage.ZONE_DETECTED.value, 0),
            "zones_watched": stage_counts.get(OpportunityStage.ZONE_WATCHED.value, 0),
            "price_near": stage_counts.get(OpportunityStage.PRICE_NEAR.value, 0),
            "zones_touched": stage_counts.get(OpportunityStage.ZONE_TOUCHED.value, 0),
            "trigger_confirmed": stage_counts.get(OpportunityStage.TRIGGER_CONFIRMED.value, 0),
            "plans_valid": stage_counts.get(OpportunityStage.PLAN_VALID.value, 0),
            "guard_blocked": stage_counts.get(OpportunityStage.GUARD_BLOCKED.value, 0),
            "armed": stage_counts.get(OpportunityStage.ARMED.value, 0),
            "filled": stage_counts.get(OpportunityStage.FILLED.value, 0),
            "expired": stage_counts.get(OpportunityStage.EXPIRED.value, 0),
            "invalidated": stage_counts.get(OpportunityStage.INVALIDATED.value, 0),
            "top_rejection_reasons": top_reasons,
            "latency_metrics": {
                "sample_count": len(decision_latencies),
                "median_ms": lat_median,
                "p95_ms": lat_p95,
            },
        }
