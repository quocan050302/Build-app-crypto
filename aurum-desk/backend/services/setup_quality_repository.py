"""
backend/services/setup_quality_repository.py
V14 — Repository for immutable SetupQualitySnapshot persistence and retrieval.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
import models

logger = logging.getLogger("setup_quality_repository")


class SetupQualityRepository:
    @staticmethod
    def save_quality_snapshot(db: Session, snapshot_data: Dict[str, Any]) -> str:
        snapshot_id = snapshot_data["snapshot_id"]
        existing = db.query(models.SetupQualitySnapshot).filter(
            models.SetupQualitySnapshot.snapshot_id == snapshot_id
        ).first()

        payload_str = json.dumps(snapshot_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

        if existing:
            # Idempotency check: if identical, return existing ID without mutating
            if existing.evidence_digest == snapshot_data.get("evidence_digest"):
                return snapshot_id
            raise ValueError(f"QUALITY_SNAPSHOT_CONFLICT: {snapshot_id}")

        now_ms = int(time.time() * 1000)
        summary = snapshot_data.get("assessment", {}).get("summary", {})

        record = models.SetupQualitySnapshot(
            snapshot_id=snapshot_id,
            setup_id=snapshot_data.get("setup_id"),
            setup_instance_id=snapshot_data.get("setup_instance_id"),
            setup_revision=snapshot_data.get("setup_revision", 1),
            quality_revision=snapshot_data.get("quality_revision", 1),
            symbol=snapshot_data.get("symbol", "XAUUSDT"),
            timeframe=snapshot_data.get("timeframe", "15M"),
            direction=snapshot_data.get("direction", "LONG"),
            strategy_family=snapshot_data.get("strategy_family", "STANDARD_SMC"),
            as_of_ms=snapshot_data.get("as_of_ms", now_ms),
            status=summary.get("status", "NOT_ASSESSED"),
            score_pct=summary.get("score_pct", 0.0),
            required_met=summary.get("required_met", 0),
            required_total=summary.get("required_total", 0),
            summary_vi=summary.get("headline_vi", ""),
            payload_json=payload_str,
            evidence_digest=snapshot_data.get("evidence_digest"),
            created_at=now_ms,
        )

        db.add(record)
        db.commit()
        db.refresh(record)
        return snapshot_id

    @staticmethod
    def get_snapshot_by_id(db: Session, snapshot_id: str) -> Optional[Dict[str, Any]]:
        record = db.query(models.SetupQualitySnapshot).filter(
            models.SetupQualitySnapshot.snapshot_id == snapshot_id
        ).first()
        if not record:
            return None
        return json.loads(record.payload_json)

    @staticmethod
    def get_latest_snapshot_for_setup(
        db: Session,
        setup_id: str,
        instance_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        query = db.query(models.SetupQualitySnapshot).filter(
            models.SetupQualitySnapshot.setup_id == setup_id
        )
        if instance_id:
            query = query.filter(models.SetupQualitySnapshot.setup_instance_id == instance_id)

        record = query.order_by(models.SetupQualitySnapshot.created_at.desc()).first()
        if not record:
            return None
        return json.loads(record.payload_json)

    @staticmethod
    def get_snapshots_batch(db: Session, setup_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Batch loads latest snapshot for a list of setup_ids to prevent N+1 queries."""
        if not setup_ids:
            return {}
        records = db.query(models.SetupQualitySnapshot).filter(
            models.SetupQualitySnapshot.setup_id.in_(setup_ids)
        ).order_by(models.SetupQualitySnapshot.created_at.desc()).all()

        out = {}
        for r in records:
            if r.setup_id not in out:
                out[r.setup_id] = json.loads(r.payload_json)
        return out


setup_quality_repository = SetupQualityRepository()
