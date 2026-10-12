import hashlib
import json
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert
import models
from services.smc_scenario_types import ScenarioSnapshot

logger = logging.getLogger("smc_observation_service")


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


def record_closed_outcome(db: Session, order: Any, note: Optional[str] = None) -> Optional[str]:
    """
    Ghi nhận kết quả của order đã CLOSED vào bảng paper_observations.
    """
    order_id = getattr(order, "id", None) or (order.get("id") if isinstance(order, dict) else None)
    snapshot_id = getattr(order, "scenario_snapshot_id", None)
    if not snapshot_id and isinstance(order, dict):
        snapshot_id = order.get("scenario_snapshot_id")

    payload = {
        "order_id": order_id,
        "direction": getattr(order, "direction", None) or (order.get("direction") if isinstance(order, dict) else None),
        "planned_entry": getattr(order, "planned_entry", None) or (order.get("planned_entry") if isinstance(order, dict) else None),
        "actual_entry": getattr(order, "actual_entry", None) or (order.get("actual_entry") if isinstance(order, dict) else None),
        "stop_loss": getattr(order, "stop_loss", None) or (order.get("stop_loss") if isinstance(order, dict) else None),
        "take_profit": getattr(order, "take_profit", None) or (order.get("take_profit") if isinstance(order, dict) else None),
        "actual_exit": getattr(order, "actual_exit", None) or (order.get("actual_exit") if isinstance(order, dict) else None),
        "realized_pnl_net": getattr(order, "realized_pnl_net", None) or (order.get("realized_pnl_net") if isinstance(order, dict) else None),
        "realized_r": getattr(order, "realized_r", None) or (order.get("realized_r") if isinstance(order, dict) else None),
        "exit_cause": getattr(order, "exit_cause", None) or (order.get("exit_cause") if isinstance(order, dict) else None),
        "scenario_snapshot_id": snapshot_id,
        "strategy_family": getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
    }
    if note:
        payload["_note"] = note

    closed_at = getattr(order, "closed_at", None) or (order.get("closed_at") if isinstance(order, dict) else None)
    from services.clock import live_clock
    as_of_ms = int(closed_at or live_clock.now_ms())

    try:
        rec_id = persist_paper_observation(
            db,
            namespace="paper",
            event_kind="ORDER_CLOSED",
            snapshot_id=snapshot_id,
            scenario_id=getattr(order, "scenario_id", None) or (order.get("scenario_id") if isinstance(order, dict) else None),
            order_id=order_id,
            as_of_ms=as_of_ms,
            payload=payload,
        )
        logger.info(f"Closed outcome observation recorded: {rec_id} for order {order_id}")
        return rec_id
    except Exception as exc:
        logger.warning(f"Error persisting closed outcome observation: {exc}")
        raise
