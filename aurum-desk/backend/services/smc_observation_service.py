import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from services.smc_scenario_types import ScenarioSnapshot

logger = logging.getLogger("smc_observation_service")

def record_observation_snapshot(db: Session, snapshot: ScenarioSnapshot, note: Optional[str] = None) -> None:
    """
    Ghi nhận snapshot cấu trúc giấy (đang quan sát) cho việc backtest 
    và thu thập dữ liệu; không dính rủi ro real.
    """
    # In a real implementation this might write to a specific table for forward paper observation.
    # Currently, we just log it or save it without triggering live orders.
    logger.info(f"Forward PAPER observation recorded: {snapshot.scenario_id} - State: {snapshot.state.value} - Note: {note}")
