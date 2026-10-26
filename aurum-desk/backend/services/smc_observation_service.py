import hashlib
import json
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert
import models
from services.smc_scenario_types import ScenarioSnapshot

logger = logging.getLogger("smc_observation_service")


def read_order_field(order: Any, name: str, default: Any = None) -> Any:
    """Helper to uniformly read fields from ORM models or dicts without coercing 0/False to default."""
    if isinstance(order, dict):
        return order.get(name, default)
    return getattr(order, name, default)


def build_closed_outcome_payload(order: Any) -> dict:
    """Extract closed outcome fields preserving exact numeric values (0.0, negatives) and distinguishing None."""
    fields = (
        'direction', 'planned_entry', 'actual_entry', 'stop_loss',
        'take_profit', 'actual_exit', 'realized_pnl_net', 'realized_r',
        'exit_cause', 'scenario_snapshot_id', 'strategy_family',
    )
    payload = {name: read_order_field(order, name) for name in fields}
    payload['order_id'] = read_order_field(order, 'id')
    if payload.get('strategy_family') is None:
        payload['strategy_family'] = 'STANDARD_SMC'
    return payload


def resolve_closed_observation_time(order: Any, caller_as_of_ms: Optional[int] = None) -> int:
    closed_at = read_order_field(order, 'closed_at')
    value = closed_at if closed_at is not None else caller_as_of_ms
    if type(value) is not int or value <= 0:
        raise ValueError('CLOSED_OBSERVATION_TIME_MISSING')
    return value


def persist_paper_observation(
    db: Session,
    *,
    namespace: str,
    event_kind: str,
    snapshot_id: Optional[str],
    scenario_id: Optional[str],
    order_id: Optional[str],
    as_of_ms: int,
    payload: dict
) -> str:
    if namespace != 'paper' or event_kind not in ('SCENARIO_MATERIAL', 'ORDER_CLOSED'):
        raise ValueError('INVALID_PAPER_OBSERVATION_CONTEXT')
    if type(as_of_ms) is not int or as_of_ms <= 0:
        raise ValueError('OBSERVATION_TIME_INVALID')
    if event_kind == 'SCENARIO_MATERIAL' and not snapshot_id:
        raise ValueError('OBSERVATION_IDENTITY_MISSING')
    if event_kind == 'ORDER_CLOSED' and not order_id:
        raise ValueError('OBSERVATION_ORDER_MISSING')

    key = json.dumps([namespace, event_kind, snapshot_id, order_id], separators=(',', ':'))
    record_id = 'obs-' + hashlib.sha256(key.encode()).hexdigest()
    serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

    try:
        with db.begin_nested():
            # ON CONFLICT DO NOTHING is idempotent, NOT permission to overwrite a historical fact.
            stmt = insert(models.PaperObservation).values(
                id=record_id,
                namespace=namespace,
                event_kind=event_kind,
                snapshot_id=snapshot_id,
                scenario_id=scenario_id,
                order_id=order_id,
                as_of_ms=as_of_ms,
                payload_json=serialized,
            ).on_conflict_do_nothing(index_elements=['id'])
            db.execute(stmt)
            db.flush()

            row = db.get(models.PaperObservation, record_id)
            if row is None or row.payload_json != serialized or row.as_of_ms != as_of_ms:
                raise ValueError('OBSERVATION_IMMUTABILITY_CONFLICT')
    except Exception:
        raise

    return record_id


def record_observation_snapshot(db: Session, snapshot: ScenarioSnapshot, note: Optional[str] = None) -> Optional[str]:
    """
    Ghi nhận snapshot cấu trúc giấy (đang quan sát) cho việc backtest 
    và thu thập dữ liệu; không dính rủi ro real.
    """
    payload = snapshot.to_dict() if hasattr(snapshot, "to_dict") else dict(snapshot)
    if note:
        payload["_note"] = note
    try:
        rec_id = persist_paper_observation(
            db,
            namespace="paper",
            event_kind="SCENARIO_MATERIAL",
            snapshot_id=snapshot.snapshot_id,
            scenario_id=snapshot.scenario_id,
            order_id=None,
            as_of_ms=snapshot.as_of_ms,
            payload=payload,
        )
        logger.info(f"Forward PAPER observation recorded: {rec_id} for {snapshot.scenario_id}")
        return rec_id
    except Exception as exc:
        logger.warning(f"Error persisting paper observation snapshot: {exc}")
        raise


def record_closed_outcome(db: Session, order: Any, note: Optional[str] = None, as_of_ms: Optional[int] = None) -> Optional[str]:
    """
    Ghi nhận kết quả của order đã CLOSED vào bảng paper_observations.
    Preserves exact 0.0 values for realized_pnl_net and realized_r.
    """
    payload = build_closed_outcome_payload(order)
    if note:
        payload["_note"] = note

    resolved_as_of_ms = resolve_closed_observation_time(order, as_of_ms)
    order_id = payload["order_id"]
    snapshot_id = payload["scenario_snapshot_id"]
    scenario_id = read_order_field(order, "scenario_id")

    try:
        rec_id = persist_paper_observation(
            db,
            namespace="paper",
            event_kind="ORDER_CLOSED",
            snapshot_id=snapshot_id,
            scenario_id=scenario_id,
            order_id=order_id,
            as_of_ms=resolved_as_of_ms,
            payload=payload,
        )
        logger.info(f"Closed outcome observation recorded: {rec_id} for order {order_id}")
        return rec_id
    except Exception as exc:
        logger.warning(f"Error persisting closed outcome observation: {exc}")
        raise
