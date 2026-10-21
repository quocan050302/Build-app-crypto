"""
backend/services/smc_scenario_repository.py
V14.4.1 — Persistence of scenarios and IMMUTABLE decision snapshots (canonical ScenarioSnapshot shape).

No function here commits: the transaction boundary belongs to the caller (strategy cycle), which publishes
events only after a successful commit.
"""

import json
import logging
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from models import SMCScenario, SMCScenarioSnapshot
from services.smc_scenario_types import ScenarioSnapshot, TERMINAL_STATES

logger = logging.getLogger('smc_scenario_repository')


def _state_str(snapshot: ScenarioSnapshot) -> str:
    return snapshot.state.value if hasattr(snapshot.state, 'value') else str(snapshot.state)


def save_immutable_scenario_snapshot(db: Session, snapshot: ScenarioSnapshot) -> SMCScenarioSnapshot:
    """Same snapshot_id + same digest -> returns the existing row (idempotent). Same id with a DIFFERENT
    digest is an immutability conflict (ValueError). Does NOT commit."""
    existing = db.query(SMCScenarioSnapshot).filter(SMCScenarioSnapshot.snapshot_id == snapshot.snapshot_id).first()
    if existing is not None:
        if existing.evidence_digest != snapshot.evidence_digest:
            raise ValueError(
                f'SNAPSHOT_IMMUTABILITY_CONFLICT: snapshot {snapshot.snapshot_id} exists with different digest '
                f'({existing.evidence_digest} vs {snapshot.evidence_digest})')
        return existing

    payload = snapshot.to_dict()
    plan_fp = (snapshot.plan or {}).get('plan_fingerprint') if isinstance(snapshot.plan, dict) else None
    row = SMCScenarioSnapshot(
        snapshot_id=snapshot.snapshot_id, scenario_id=snapshot.scenario_id, instance_id=snapshot.instance_id,
        revision=snapshot.revision, symbol=snapshot.symbol, timeframe=snapshot.setup_timeframe,
        direction=snapshot.direction, state=_state_str(snapshot), as_of_ms=snapshot.as_of_ms,
        evidence_cutoff_ms=snapshot.evidence_cutoff_ms, evidence_digest=snapshot.evidence_digest,
        plan_fingerprint=plan_fp, payload=json.dumps(payload, ensure_ascii=False), created_at=snapshot.as_of_ms,
    )
    db.add(row)
    db.flush()
    return row


def upsert_scenario_revision(db: Session, snapshot: ScenarioSnapshot, is_material_change: bool = True
                             ) -> Tuple[SMCScenario, Optional[SMCScenarioSnapshot], bool]:
    """Upserts the current-scenario pointer; if material also stores the immutable snapshot. Returns
    (scenario_row, snapshot_row_or_none, created_new_snapshot). Does NOT commit."""
    scenario_row = db.query(SMCScenario).filter(SMCScenario.scenario_id == snapshot.scenario_id).first()

    snapshot_row = None
    if is_material_change:
        # Check if this snapshot_id exists with a different digest, advance revision if needed
        existing = db.query(SMCScenarioSnapshot).filter(SMCScenarioSnapshot.snapshot_id == snapshot.snapshot_id).first()
        if existing is not None and existing.evidence_digest != snapshot.evidence_digest:
            max_rev_row = db.query(SMCScenarioSnapshot).filter(
                SMCScenarioSnapshot.instance_id == snapshot.instance_id
            ).order_by(SMCScenarioSnapshot.revision.desc()).first()
            if max_rev_row:
                if max_rev_row.evidence_digest == snapshot.evidence_digest:
                    snapshot_row = max_rev_row
                else:
                    snapshot.revision = max_rev_row.revision + 1
                    snapshot_row = save_immutable_scenario_snapshot(db, snapshot)
            else:
                snapshot_row = save_immutable_scenario_snapshot(db, snapshot)
        else:
            snapshot_row = save_immutable_scenario_snapshot(db, snapshot)

    current_snapshot_id = snapshot_row.snapshot_id if snapshot_row else (
        scenario_row.current_snapshot_id if scenario_row else snapshot.snapshot_id)

    payload_str = json.dumps(snapshot.to_dict(), ensure_ascii=False)
    fields = dict(
        instance_id=snapshot.instance_id, revision=snapshot.revision, direction=snapshot.direction,
        state=_state_str(snapshot), as_of_ms=snapshot.as_of_ms, anchor_level_id=snapshot.anchor_level_id,
        target_level_id=snapshot.target_level_id, summary_vi=snapshot.summary_vi,
        current_snapshot_id=current_snapshot_id, payload=payload_str, updated_at=snapshot.as_of_ms,
    )
    if scenario_row is None:
        scenario_row = SMCScenario(
            scenario_id=snapshot.scenario_id, symbol=snapshot.symbol, timeframe=snapshot.setup_timeframe,
            strategy_family=snapshot.family, created_at=snapshot.as_of_ms, **fields)
        db.add(scenario_row)
    else:
        for k, v in fields.items():
            setattr(scenario_row, k, v)
    db.flush()
    return scenario_row, snapshot_row, snapshot_row is not None


def get_scenario(db: Session, scenario_id: str) -> Optional[SMCScenario]:
    return db.query(SMCScenario).filter(SMCScenario.scenario_id == scenario_id).first()


def get_scenario_snapshot(db: Session, snapshot_id: str) -> Optional[SMCScenarioSnapshot]:
    return db.query(SMCScenarioSnapshot).filter(SMCScenarioSnapshot.snapshot_id == snapshot_id).first()


def get_active_scenarios(db: Session, symbol: str = 'XAUUSDT', limit: int = 5) -> List[SMCScenario]:
    return db.query(SMCScenario).filter(
        SMCScenario.symbol == symbol, SMCScenario.state.notin_(['EXPIRED', 'SUPERSEDED'])
    ).order_by(SMCScenario.updated_at.desc()).limit(limit).all()


def snapshot_from_row(row: SMCScenarioSnapshot) -> ScenarioSnapshot:
    """Strict reader; payloads of an older schema raise ValueError('LEGACY_SNAPSHOT_SCHEMA')."""
    return ScenarioSnapshot.from_dict(json.loads(row.payload))


def get_previous_scenarios(db: Session, symbol: str, limit: int = 40) -> List[ScenarioSnapshot]:
    """Latest persisted revision of each scenario of `symbol` (current pointer rows and recent snapshots),
    as canonical snapshots. Rows of a legacy schema are skipped (they can neither be resurrected nor continued)."""
    out: List[ScenarioSnapshot] = []
    by_instance: dict[str, ScenarioSnapshot] = {}

    rows = db.query(SMCScenario).filter(SMCScenario.symbol == symbol).order_by(SMCScenario.updated_at.desc()).limit(limit).all()
    for r in rows:
        try:
            snap = ScenarioSnapshot.from_dict(json.loads(r.payload))
            out.append(snap)
            if snap.instance_id not in by_instance or snap.revision > by_instance[snap.instance_id].revision:
                by_instance[snap.instance_id] = snap
        except (ValueError, KeyError, TypeError):
            logger.info('Skipping legacy/malformed scenario row %s', r.scenario_id)

    # Also inspect recent snapshots from SMCScenarioSnapshot so non-current/historical instances
    # retain their highest revision and do not collide if an anchor re-appears.
    snap_rows = db.query(SMCScenarioSnapshot).filter(
        SMCScenarioSnapshot.symbol == symbol
    ).order_by(SMCScenarioSnapshot.created_at.desc()).limit(limit * 2).all()
    for sr in snap_rows:
        try:
            snap = ScenarioSnapshot.from_dict(json.loads(sr.payload))
            if snap.instance_id not in by_instance:
                out.append(snap)
                by_instance[snap.instance_id] = snap
            elif snap.revision > by_instance[snap.instance_id].revision:
                out = [s for s in out if s.instance_id != snap.instance_id]
                out.append(snap)
                by_instance[snap.instance_id] = snap
        except Exception:
            pass

    return out
