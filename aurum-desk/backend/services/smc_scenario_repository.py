import json
import logging
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from models import SMCScenario, SMCScenarioSnapshot
from services.smc_scenario_types import ScenarioSnapshot

logger = logging.getLogger('smc_scenario_repository')

def save_immutable_scenario_snapshot(
    db: Session,
    snapshot: ScenarioSnapshot,
) -> SMCScenarioSnapshot:
    """
    Saves an immutable scenario snapshot.
    If a snapshot with the same snapshot_id exists, checks digest.
    If same digest, returns existing; if different digest, raises ValueError (immutability conflict).
    Does NOT commit the transaction: transaction boundary is owned by caller.
    """
    existing = db.query(SMCScenarioSnapshot).filter(
        SMCScenarioSnapshot.snapshot_id == snapshot.snapshot_id
    ).first()

    if existing is not None:
        if existing.evidence_digest != snapshot.evidence_digest:
            raise ValueError(
                f'SNAPSHOT_IMMUTABILITY_CONFLICT: snapshot {snapshot.snapshot_id} exists with different digest '
                f'({existing.evidence_digest} vs {snapshot.evidence_digest})'
            )
        return existing

    payload_str = json.dumps(snapshot.to_dict(), ensure_ascii=False)

    row = SMCScenarioSnapshot(
        snapshot_id=snapshot.snapshot_id,
        scenario_id=snapshot.scenario_id,
        instance_id=snapshot.instance_id,
        revision=snapshot.revision,
        symbol=snapshot.symbol,
        timeframe=snapshot.setup_timeframe,
        direction=snapshot.direction,
        state=snapshot.state.value if hasattr(snapshot.state, 'value') else str(snapshot.state),
        as_of_ms=snapshot.as_of_ms,
        evidence_cutoff_ms=snapshot.evidence_cutoff_ms,
        evidence_digest=snapshot.evidence_digest,
        plan_fingerprint=snapshot.plan.plan_fingerprint if snapshot.plan else None,
        payload=payload_str,
        created_at=snapshot.as_of_ms,
    )
    db.add(row)
    db.flush()
    return row

def upsert_scenario_revision(
    db: Session,
    snapshot: ScenarioSnapshot,
    is_material_change: bool = True,
) -> Tuple[SMCScenario, Optional[SMCScenarioSnapshot], bool]:
    """
    Upserts the current scenario pointer and, if material, saves an immutable snapshot.
    Returns (scenario_row, snapshot_row_or_none, created_new_snapshot).
    Transaction is NOT committed here.
    """
    state_str = snapshot.state.value if hasattr(snapshot.state, 'value') else str(snapshot.state)
    payload_str = json.dumps(snapshot.to_dict(), ensure_ascii=False)

    scenario_row = db.query(SMCScenario).filter(
        SMCScenario.scenario_id == snapshot.scenario_id
    ).first()

    created_snapshot = False
    snapshot_row = None

    if is_material_change:
        snapshot_row = save_immutable_scenario_snapshot(db, snapshot)
        created_snapshot = True

    current_snapshot_id = snapshot_row.snapshot_id if snapshot_row else (
        scenario_row.current_snapshot_id if scenario_row else snapshot.snapshot_id
    )

    if scenario_row is None:
        scenario_row = SMCScenario(
            scenario_id=snapshot.scenario_id,
            instance_id=snapshot.instance_id,
            revision=snapshot.revision,
            symbol=snapshot.symbol,
            timeframe=snapshot.setup_timeframe,
            strategy_family=snapshot.strategy_family,
            direction=snapshot.direction,
            state=state_str,
            as_of_ms=snapshot.as_of_ms,
            anchor_level_id=snapshot.liquidity_anchor.level_id if snapshot.liquidity_anchor else None,
            target_level_id=snapshot.target_anchor.level_id if snapshot.target_anchor else None,
            summary_vi=snapshot.summary_vi,
            current_snapshot_id=current_snapshot_id,
            payload=payload_str,
            created_at=snapshot.as_of_ms,
            updated_at=snapshot.as_of_ms,
        )
        db.add(scenario_row)
    else:
        scenario_row.instance_id = snapshot.instance_id
        scenario_row.revision = snapshot.revision
        scenario_row.direction = snapshot.direction
        scenario_row.state = state_str
        scenario_row.as_of_ms = snapshot.as_of_ms
        scenario_row.anchor_level_id = snapshot.liquidity_anchor.level_id if snapshot.liquidity_anchor else None
        scenario_row.target_level_id = snapshot.target_anchor.level_id if snapshot.target_anchor else None
        scenario_row.summary_vi = snapshot.summary_vi
        scenario_row.current_snapshot_id = current_snapshot_id
        scenario_row.payload = payload_str
        scenario_row.updated_at = snapshot.as_of_ms

    db.flush()
    return scenario_row, snapshot_row, created_snapshot

def get_scenario(db: Session, scenario_id: str) -> Optional[SMCScenario]:
    return db.query(SMCScenario).filter(SMCScenario.scenario_id == scenario_id).first()

def get_scenario_snapshot(db: Session, snapshot_id: str) -> Optional[SMCScenarioSnapshot]:
    return db.query(SMCScenarioSnapshot).filter(SMCScenarioSnapshot.snapshot_id == snapshot_id).first()

def get_active_scenarios(
    db: Session,
    symbol: str = 'XAUUSDT',
    limit: int = 5,
) -> List[SMCScenario]:
    return db.query(SMCScenario).filter(
        SMCScenario.symbol == symbol,
        SMCScenario.state.notin_(['EXPIRED', 'SUPERSEDED'])
    ).order_by(SMCScenario.updated_at.desc()).limit(limit).all()
