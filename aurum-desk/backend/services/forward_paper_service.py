"""
backend/services/forward_paper_service.py
V14.4.13 — Forward Paper Practice Session, Verified Outcomes, Correct Drawdown, and Truthful Recovery.

Enforces:
1. Strict session membership binding (no OR created_at leak, no session_instance_id collision).
2. Pure read-only outcome classification (confirmed vs unverified vs pending review vs invalid).
3. Correct point-in-time running peak maximum drawdown algorithm (both USD and percentage maxima).
4. Truthful reporting metrics (only confirmed trades in winrate denominator, realized R with valid basis).
5. Safe math: division by zero returns 0.0 or None, no NaN / Infinity, zero DB mutations on report.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session

import models

logger = logging.getLogger("forward_paper")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _resolve_runtime_commit() -> str:
    """PHẦN 029: Resolves current runtime commit SHA without network calls or hardcoded old SHA."""
    env_sha = os.environ.get("AURUM_TESTED_SHA") or os.environ.get("GIT_COMMIT")
    if env_sha and len(env_sha) >= 7:
        return env_sha.strip()
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "UNKNOWN"


@dataclass
class ForwardSessionConfig:
    session_id: str
    strategy_commit: str
    start_time_ms: int
    virtual_capital: float = 1000.0
    frozen_risk_config: Dict[str, Any] = field(default_factory=dict)
    session_policy: str = "ALL_SESSIONS_WITH_NY_RESERVE"
    enabled_strategy_families: List[str] = field(
        default_factory=lambda: ["STANDARD_SMC", "NY_QUOTA_PAPER"]
    )
    cost_assumptions: Dict[str, Any] = field(default_factory=dict)
    data_source: str = "REALTIME_OR_STREAM"
    status: str = "ACTIVE"
    updated_at_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "strategy_commit": self.strategy_commit,
            "start_time_ms": self.start_time_ms,
            "virtual_capital": self.virtual_capital,
            "frozen_risk_config": dict(self.frozen_risk_config),
            "session_policy": self.session_policy,
            "enabled_strategy_families": list(self.enabled_strategy_families),
            "cost_assumptions": dict(self.cost_assumptions),
            "data_source": self.data_source,
            "status": self.status,
            "updated_at_ms": self.updated_at_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ForwardSessionConfig:
        return cls(
            session_id=str(data.get("session_id") or ""),
            strategy_commit=str(data.get("strategy_commit") or "UNKNOWN"),
            start_time_ms=int(data.get("start_time_ms") or 0),
            virtual_capital=float(data.get("virtual_capital") or 1000.0),
            frozen_risk_config=dict(data.get("frozen_risk_config") or {}),
            session_policy=str(data.get("session_policy") or "ALL_SESSIONS_WITH_NY_RESERVE"),
            enabled_strategy_families=list(
                data.get("enabled_strategy_families") or ["STANDARD_SMC", "NY_QUOTA_PAPER"]
            ),
            cost_assumptions=dict(data.get("cost_assumptions") or {}),
            data_source=str(data.get("data_source") or "REALTIME_OR_STREAM"),
            status=str(data.get("status") or "ACTIVE"),
            updated_at_ms=int(data.get("updated_at_ms") or 0),
        )


@dataclass
class ForwardOrderMembership:
    """PHẦN 017: Contract for forward practice order membership."""
    forward_session_id: str
    order_id: str
    bound_at_ms: int
    config_revision: int = 1
    runtime_commit: str = "UNKNOWN"
    membership_status: str = "ARMED"  # ARMED, FILLED, CLOSED, REJECTED, EXPIRED
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "forward_session_id": self.forward_session_id,
            "order_id": self.order_id,
            "bound_at_ms": self.bound_at_ms,
            "config_revision": self.config_revision,
            "runtime_commit": self.runtime_commit,
            "membership_status": self.membership_status,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ForwardOrderMembership:
        return cls(
            forward_session_id=str(data.get("forward_session_id") or ""),
            order_id=str(data.get("order_id") or ""),
            bound_at_ms=int(data.get("bound_at_ms") or 0),
            config_revision=int(data.get("config_revision") or 1),
            runtime_commit=str(data.get("runtime_commit") or "UNKNOWN"),
            membership_status=str(data.get("membership_status") or "ARMED"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class MembershipStageResult:
    status: str  # "STAGED", "ALREADY_APPLIED", "NOT_BOUND", "DATA_INVALID", "CONFLICTING_MEMBERSHIP", "SESSION_NOT_FOUND", "SESSION_INACTIVE", "INVALID_ORDER"
    order_id: str
    forward_session_id: Optional[str] = None
    membership: Optional[ForwardOrderMembership] = None
    reason_codes: List[str] = field(default_factory=list)
    tracking_repair_needed: bool = False


@dataclass(frozen=True)
class RecoveryOutcomeAuthority:
    status: str  # "VERIFIED_PAPER", "UNVERIFIED", "CONSERVATIVE_SIMULATION", "UNRESOLVED", "CONFLICTED"
    confidence: Optional[str] = None
    recovery_status: Optional[str] = None
    exit_mode: str = "NORMAL"  # "NORMAL", "RECOVERY", "MANUAL", "SIMULATION"
    countable_for_confirmed: bool = False
    terminal_event_id: Optional[str] = None
    reason_codes: List[str] = field(default_factory=list)


def save_forward_session(db: Session, config: ForwardSessionConfig) -> bool:
    """Saves forward paper session configuration into SystemConfig."""
    if not config.session_id or not math.isfinite(config.virtual_capital) or config.virtual_capital <= 0:
        return False
    key = f"fwd_sess:{config.session_id}"[:50]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    config.updated_at_ms = now_ms
    val_json = json.dumps(config.to_dict(), allow_nan=False)

    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if not rec:
        rec = models.SystemConfig(key=key, value=val_json, updated_at=now_ms)
        db.add(rec)
    else:
        rec.value = val_json
        rec.updated_at = now_ms

    # Maintain active pointer if active
    if config.status == "ACTIVE":
        active_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == "fwd_sess:active").first()
        if not active_rec:
            active_rec = models.SystemConfig(key="fwd_sess:active", value=config.session_id, updated_at=now_ms)
            db.add(active_rec)
        else:
            old_active_id = active_rec.value
            if old_active_id and old_active_id != config.session_id:
                old_sess = load_forward_session(db, old_active_id)
                if old_sess and old_sess.status == "ACTIVE":
                    old_sess.status = "STOPPED"
                    old_sess.updated_at_ms = now_ms
                    old_key = f"fwd_sess:{old_active_id}"[:50]
                    old_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == old_key).first()
                    if old_rec:
                        old_rec.value = json.dumps(old_sess.to_dict(), allow_nan=False)
                        old_rec.updated_at = now_ms
            active_rec.value = config.session_id
            active_rec.updated_at = now_ms
    elif config.status in ("STOPPED", "COMPLETED"):
        active_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == "fwd_sess:active").first()
        if active_rec and active_rec.value == config.session_id:
            active_rec.value = ""
            active_rec.updated_at = now_ms

    try:
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save forward paper session {config.session_id}: {e}")
        return False


def stop_forward_paper_session(db: Session, session_id: str, now_ms: Optional[int] = None) -> bool:
    """PHẦN 021, 031: Stops an active forward session and atomically clears active pointer."""
    sess = load_forward_session(db, session_id)
    if not sess:
        return False
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    sess.status = "STOPPED"
    sess.updated_at_ms = now_ms
    return save_forward_session(db, sess)


def load_forward_session(db: Session, session_id: str) -> Optional[ForwardSessionConfig]:
    """Loads a forward paper session configuration from SystemConfig."""
    if not session_id:
        return None
    key = f"fwd_sess:{session_id}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if not rec or not rec.value:
        return None
    try:
        return ForwardSessionConfig.from_dict(json.loads(rec.value))
    except Exception as e:
        logger.error(f"Failed to parse forward paper session {session_id}: {e}")
        return None


def load_active_forward_session(db: Session) -> Optional[ForwardSessionConfig]:
    """Loads current active forward paper session."""
    active_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == "fwd_sess:active").first()
    if not active_rec or not active_rec.value:
        return None
    sess = load_forward_session(db, active_rec.value)
    if sess and sess.status == "ACTIVE":
        return sess
    return None


def create_forward_paper_session(
    db: Session,
    session_id: Optional[str] = None,
    virtual_capital: float = 1000.0,
    session_policy: str = "ALL_SESSIONS_WITH_NY_RESERVE",
    risk_config: Optional[Dict[str, Any]] = None,
    cost_assumptions: Optional[Dict[str, Any]] = None,
    commit_sha: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> ForwardSessionConfig:
    """PHẦN 043, 044, 045: Creates a new forward paper practice session with frozen parameters.
    Does NOT overwrite user live account settings or reset existing trade history.
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    sess_id = session_id or f"fwd_{datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    resolved_commit = commit_sha or _resolve_runtime_commit()

    frozen_risk = risk_config or {
        "risk_pct": 0.25,
        "max_daily_fills": 3,
        "min_net_rr": 2.0,
        "leverage": 5,
        "margin_mode": "ISOLATED",
    }
    frozen_costs = cost_assumptions or {
        "fees_pct": 0.10,
        "slippage_pct": 0.10,
    }

    config = ForwardSessionConfig(
        session_id=sess_id,
        strategy_commit=resolved_commit,
        start_time_ms=now_ms,
        virtual_capital=virtual_capital,
        frozen_risk_config=frozen_risk,
        session_policy=session_policy,
        enabled_strategy_families=["STANDARD_SMC", "NY_QUOTA_PAPER"],
        cost_assumptions=frozen_costs,
        data_source="REALTIME_OR_STREAM",
        status="ACTIVE",
        updated_at_ms=now_ms,
    )
    save_forward_session(db, config)
    return config


# ---------------------------------------------------------------------------
# PHẦN 017–025: Forward Order Membership Subsystem
# ---------------------------------------------------------------------------

def get_order_forward_membership(db: Session, order_id: str) -> Optional[ForwardOrderMembership]:
    """Retrieves forward session membership for a given order."""
    if not order_id:
        return None
    key = f"fwd_mem:{order_id}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if not rec or not rec.value:
        return None
    try:
        return ForwardOrderMembership.from_dict(json.loads(rec.value))
    except Exception:
        return None


def _get_session_order_ids(db: Session, session_id: str) -> List[str]:
    """Loads list of order IDs bound to a forward session."""
    key = f"fwd_sess_ords:{session_id}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    if not rec or not rec.value:
        return []
    try:
        ids = json.loads(rec.value)
        return list(ids) if isinstance(ids, list) else []
    except Exception:
        return []


def _append_session_order_id(db: Session, session_id: str, order_id: str, now_ms: int) -> bool:
    """Appends order ID to forward session index atomically."""
    key = f"fwd_sess_ords:{session_id}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    current_ids = []
    if rec and rec.value:
        try:
            val = json.loads(rec.value)
            if isinstance(val, list):
                current_ids = val
        except Exception:
            current_ids = []
    if order_id not in current_ids:
        current_ids.append(order_id)
        val_json = json.dumps(current_ids)
        if not rec:
            rec = models.SystemConfig(key=key, value=val_json, updated_at=now_ms)
            db.add(rec)
        else:
            rec.value = val_json
            rec.updated_at = now_ms
    return True


def prepare_membership_update_payload(
    existing_payload: Any,
    new_status: str,
    now_ms: int,
) -> Tuple[bool, Optional[str], List[str]]:
    """PHẦN 021: Pure parse, schema, identity, and status validation without ORM mutation.
    Returns (is_valid, serialized_payload, reason_codes).
    """
    if isinstance(existing_payload, str):
        try:
            data = json.loads(existing_payload)
        except Exception:
            return False, None, ["CORRUPT_MEMBERSHIP_JSON"]
    elif isinstance(existing_payload, dict):
        data = dict(existing_payload)
    else:
        return False, None, ["INVALID_MEMBERSHIP_PAYLOAD_TYPE"]

    if not isinstance(data, dict):
        return False, None, ["ROOT_PAYLOAD_NOT_DICT"]

    order_id = data.get("order_id")
    if not order_id or not isinstance(order_id, str):
        return False, None, ["MISSING_OR_INVALID_ORDER_ID"]

    session_id = data.get("forward_session_id")
    if not session_id or not isinstance(session_id, str):
        return False, None, ["MISSING_OR_INVALID_FORWARD_SESSION_ID"]

    valid_statuses = {"BOUND", "ARMED", "FILLED", "CLOSED", "REJECTED", "CANCELLED", "EXPIRED"}
    if new_status not in valid_statuses:
        return False, None, [f"INVALID_NEW_STATUS_{new_status}"]

    data["membership_status"] = new_status
    data["updated_at_ms"] = now_ms

    try:
        serialized = json.dumps(data, allow_nan=False)
        return True, serialized, []
    except Exception as e:
        return False, None, [f"SERIALIZATION_FAILED: {e}"]


def stage_forward_membership_status(
    db: Session,
    order_id: str,
    new_status: str,
    now_ms: Optional[int] = None,
) -> MembershipStageResult:
    """PHẦN 018, 020, 022: Stages forward session membership status update under caller's transaction.
    Guarantees:
    - Never calls db.commit() or db.rollback().
    - Pure validation errors return DATA_INVALID with tracking_repair_needed=True without failing parent session.
    - SQL/autoflush exceptions propagate to caller (transaction owner) so owner can handle rollback.
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    mem_key = f"fwd_mem:{order_id}"[:50]
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
    if not rec or not rec.value:
        return MembershipStageResult(
            status="NOT_BOUND",
            order_id=order_id,
            reason_codes=["NOT_BOUND"],
        )

    # Check if already applied
    sess_id = None
    try:
        raw_dict = json.loads(rec.value)
        if isinstance(raw_dict, dict):
            sess_id = raw_dict.get("forward_session_id")
            if raw_dict.get("membership_status") == new_status:
                return MembershipStageResult(
                    status="ALREADY_APPLIED",
                    order_id=order_id,
                    forward_session_id=sess_id,
                )
    except Exception:
        pass

    is_valid, serialized, reasons = prepare_membership_update_payload(rec.value, new_status, now_ms)
    if not is_valid or not serialized:
        return MembershipStageResult(
            status="DATA_INVALID",
            order_id=order_id,
            forward_session_id=sess_id,
            reason_codes=reasons,
            tracking_repair_needed=True,
        )

    rec.value = serialized
    rec.updated_at = now_ms
    db.flush()
    return MembershipStageResult(
        status="STAGED",
        order_id=order_id,
        forward_session_id=sess_id,
    )


def stage_forward_membership_binding(
    db: Session,
    session_id: str,
    order: models.PaperOrder,
    now_ms: Optional[int] = None,
    config_revision: int = 1,
    runtime_commit: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> MembershipStageResult:
    """PHẦN 018, 020, 023: Stages forward session membership binding under caller's transaction.
    Guarantees:
    - Never calls db.commit() or db.rollback().
    - Validates target session and order.
    - Idempotent: repeated binding of same order/session returns ALREADY_APPLIED.
    - Stages membership and session order-ID index together in same transaction.
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    sess = load_forward_session(db, session_id)
    if not sess:
        return MembershipStageResult(
            status="SESSION_NOT_FOUND",
            order_id=getattr(order, "id", "") or "",
            forward_session_id=session_id,
            reason_codes=[f"Forward session {session_id} not found"],
        )
    if sess.status != "ACTIVE":
        return MembershipStageResult(
            status="SESSION_INACTIVE",
            order_id=getattr(order, "id", "") or "",
            forward_session_id=session_id,
            reason_codes=[f"Forward session {session_id} is {sess.status}"],
        )
    if not order or not getattr(order, "id", None):
        return MembershipStageResult(
            status="INVALID_ORDER",
            order_id="",
            forward_session_id=session_id,
            reason_codes=["Missing paper order or order id"],
        )

    # Check existing membership
    existing = get_order_forward_membership(db, order.id)
    if existing:
        if existing.forward_session_id == session_id:
            return MembershipStageResult(
                status="ALREADY_APPLIED",
                order_id=order.id,
                forward_session_id=session_id,
                membership=existing,
            )
        return MembershipStageResult(
            status="CONFLICTING_MEMBERSHIP",
            order_id=order.id,
            forward_session_id=existing.forward_session_id,
            reason_codes=[f"Order {order.id} is already bound to forward session {existing.forward_session_id}"],
        )

    resolved_commit = runtime_commit or sess.strategy_commit or _resolve_runtime_commit()
    initial_status = "ARMED" if getattr(order, "state", "") == "armed" else "BOUND"

    mem = ForwardOrderMembership(
        forward_session_id=session_id,
        order_id=order.id,
        bound_at_ms=now_ms,
        config_revision=config_revision,
        runtime_commit=resolved_commit,
        membership_status=initial_status,
        metadata=dict(metadata or {}),
    )

    mem_key = f"fwd_mem:{order.id}"[:50]
    val_json = json.dumps(mem.to_dict(), allow_nan=False)
    rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
    if not rec:
        rec = models.SystemConfig(key=mem_key, value=val_json, updated_at=now_ms)
        db.add(rec)
    else:
        rec.value = val_json
        rec.updated_at = now_ms

    _append_session_order_id(db, session_id, order.id, now_ms)
    db.flush()

    return MembershipStageResult(
        status="STAGED",
        order_id=order.id,
        forward_session_id=session_id,
        membership=mem,
    )


def stage_order_to_active_forward_session(
    db: Session,
    order: models.PaperOrder,
    now_ms: Optional[int] = None,
) -> MembershipStageResult:
    """Stage-only convenience: stages binding to currently active forward session if one exists."""
    active = load_active_forward_session(db)
    if not active:
        return MembershipStageResult(
            status="NOT_BOUND",
            order_id=getattr(order, "id", "") or "",
            reason_codes=["NO_ACTIVE_FORWARD_SESSION"],
        )
    return stage_forward_membership_binding(db, active.session_id, order, now_ms=now_ms)


def bind_order_to_forward_session(
    db: Session,
    session_id: str,
    order: models.PaperOrder,
    now_ms: Optional[int] = None,
    config_revision: int = 1,
    runtime_commit: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """PHẦN 019: Standalone convenience wrapper: stages and commits binding in its own transaction."""
    res = stage_forward_membership_binding(
        db=db,
        session_id=session_id,
        order=order,
        now_ms=now_ms,
        config_revision=config_revision,
        runtime_commit=runtime_commit,
        metadata=metadata,
    )
    if res.status == "ALREADY_APPLIED":
        return {"success": True, "status": "ALREADY_BOUND", "membership": res.membership}
    if res.status != "STAGED":
        return {"success": False, "status": res.status, "reason": (res.reason_codes or ["FAILED"])[0]}

    try:
        db.commit()
        return {"success": True, "status": "BOUND", "membership": res.membership}
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to bind order {order.id} to forward session {session_id}: {e}")
        return {"success": False, "status": "PERSISTENCE_FAILED", "reason": str(e)}


def bind_order_to_active_forward_session(
    db: Session,
    order: models.PaperOrder,
    now_ms: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """PHẦN 019: Standalone convenience helper: binds order to currently active forward session with commit."""
    active = load_active_forward_session(db)
    if not active:
        return None
    return bind_order_to_forward_session(db, active.session_id, order, now_ms=now_ms)


def update_order_forward_membership_status(
    db: Session,
    order_id: str,
    new_status: str,
    now_ms: Optional[int] = None,
) -> bool:
    """PHẦN 019: Standalone convenience helper: stages and commits membership status update."""
    res = stage_forward_membership_status(db, order_id, new_status, now_ms=now_ms)
    if res.status in ("STAGED", "ALREADY_APPLIED"):
        try:
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            logger.warning(f"Could not commit forward membership status for {order_id}: {e}")
            return False
    return False


def load_forward_session_orders(
    db: Session,
    session_id: str,
    as_of_ms: Optional[int] = None,
    filters: Optional[Dict[str, Any]] = None,
) -> List[models.PaperOrder]:
    """PHẦN 025: Canonical query returning orders strictly belonging to the forward session.
    Never relies on loose created_at ranges or leaks orders across sessions.
    If session_id is not found, returns empty list.
    """
    sess = load_forward_session(db, session_id)
    if not sess:
        return []

    order_ids = _get_session_order_ids(db, session_id)
    q = db.query(models.PaperOrder)
    if order_ids:
        q = q.filter(
            (models.PaperOrder.id.in_(order_ids)) |
            (models.PaperOrder.session_instance_id == session_id)
        )
    else:
        q = q.filter(models.PaperOrder.session_instance_id == session_id)

    if as_of_ms is not None:
        q = q.filter(models.PaperOrder.created_at <= as_of_ms)

    orders = q.order_by(models.PaperOrder.created_at.asc()).all()
    return orders


# ---------------------------------------------------------------------------
# PHẦN 004, 038–044: Outcome Authority & Classification
# ---------------------------------------------------------------------------

VALID_EXIT_CAUSES: Set[str] = {
    "TP_HIT",
    "SL_HIT",
    "AMBIGUOUS_BAR_SL_FIRST",
    "LIQUIDATED",
    "LIQUIDATION",
    "MANUAL",
    "MANUAL_CLOSE",
    "EXPIRY",
    "GAP_SL",
    "GAP_TP",
    "SL_HIT_RECONCILED",
    "TP_HIT_RECONCILED",
}


def read_outcome_authority(
    order: models.PaperOrder,
    terminal_event: Optional[Dict[str, Any]] = None,
    review_record: Optional[Dict[str, Any]] = None,
) -> RecoveryOutcomeAuthority:
    """PHẦN 004, 038, 039, 040, 041, 042, 043:
    Pure extraction of outcome authority from real ORM fields and provenance.
    Categories:
    - VERIFIED_PAPER: Valid normal paper close or verified recovery (CONFIRMED / CONFIRMED_TICK).
    - CONSERVATIVE_SIMULATION: Conservative simulation (SIMULATED_AMBIGUOUS, ASSUMED_CONSERVATIVE, AMBIGUOUS_BAR_SL_FIRST).
    - UNRESOLVED: Recovery status UNRESOLVED / UNRESOLVED_GAP / RECOVERING, or review pending.
    - CONFLICTED: Terminal conflict review requiring manual review.
    - UNVERIFIED: Missing exit metadata, invalid exit cause, or unverified recovery.
    """
    # 1. Active Review Record
    if review_record:
        rev_st = review_record.get("status")
        if rev_st == "MANUAL_REVIEW_REQUIRED":
            return RecoveryOutcomeAuthority(
                status="CONFLICTED",
                exit_mode="CONFLICT",
                countable_for_confirmed=False,
                reason_codes=["CONFLICT_REQUIRES_MANUAL_REVIEW"],
            )
        if rev_st in ("PENDING_REVIEW", "RETRY_SCHEDULED", "RETRYING"):
            return RecoveryOutcomeAuthority(
                status="UNRESOLVED",
                exit_mode="REVIEW",
                countable_for_confirmed=False,
                reason_codes=review_record.get("reason_codes") or ["REVIEW_PENDING"],
            )

    # 2. State & Basic Closed Exit Metadata
    state = getattr(order, "state", "") or ""
    if state != "closed":
        return RecoveryOutcomeAuthority(
            status="UNVERIFIED",
            exit_mode="NOT_CLOSED",
            countable_for_confirmed=False,
            reason_codes=[f"ORDER_NOT_CLOSED_{state}"],
        )

    closed_at = getattr(order, "closed_at", None)
    exit_cause = getattr(order, "exit_cause", None)
    if not closed_at or not exit_cause:
        return RecoveryOutcomeAuthority(
            status="UNVERIFIED",
            exit_mode="UNVERIFIED",
            countable_for_confirmed=False,
            reason_codes=["UNVERIFIED_EXIT_METADATA"],
        )

    if exit_cause not in VALID_EXIT_CAUSES:
        return RecoveryOutcomeAuthority(
            status="UNVERIFIED",
            exit_mode="UNVERIFIED",
            countable_for_confirmed=False,
            reason_codes=[f"INVALID_EXIT_CAUSE_{exit_cause}"],
        )

    # 3. Recovery Fields (real columns: recovery_status, recovery_confidence)
    rec_status = getattr(order, "recovery_status", "NONE") or "NONE"
    rec_conf = getattr(order, "recovery_confidence", None)

    # If recovery_confidence is set to UNRESOLVED or recovery_status is unresolved:
    if rec_conf == "UNRESOLVED" or rec_status in ("UNRESOLVED_GAP", "RECOVERING"):
        return RecoveryOutcomeAuthority(
            status="UNRESOLVED",
            confidence=rec_conf,
            recovery_status=rec_status,
            exit_mode="RECOVERY",
            countable_for_confirmed=False,
            reason_codes=["UNRESOLVED_RECOVERY"],
        )

    # If conservative simulation:
    if rec_conf in ("ASSUMED_CONSERVATIVE", "SIMULATED_AMBIGUOUS") or exit_cause == "AMBIGUOUS_BAR_SL_FIRST":
        return RecoveryOutcomeAuthority(
            status="CONSERVATIVE_SIMULATION",
            confidence=rec_conf,
            recovery_status=rec_status,
            exit_mode="SIMULATION",
            countable_for_confirmed=False,
            reason_codes=["CONSERVATIVE_SIMULATION"],
        )

    # If verified recovery:
    if rec_conf in ("CONFIRMED", "CONFIRMED_TICK"):
        return RecoveryOutcomeAuthority(
            status="VERIFIED_PAPER",
            confidence=rec_conf,
            recovery_status=rec_status,
            exit_mode="RECOVERY",
            countable_for_confirmed=True,
            reason_codes=[],
        )

    # If recovery_confidence is None (normal live PAPER trade closed via execute_close)
    if rec_conf is None:
        exit_mode = "MANUAL" if exit_cause in ("MANUAL", "MANUAL_CLOSE") else "NORMAL"
        return RecoveryOutcomeAuthority(
            status="VERIFIED_PAPER",
            confidence=None,
            recovery_status=rec_status,
            exit_mode=exit_mode,
            countable_for_confirmed=True,
            reason_codes=[],
        )

    # If an unknown/unsupported recovery_confidence string was recorded:
    return RecoveryOutcomeAuthority(
        status="UNVERIFIED",
        confidence=rec_conf,
        recovery_status=rec_status,
        exit_mode="UNKNOWN",
        countable_for_confirmed=False,
        reason_codes=[f"UNKNOWN_RECOVERY_CONFIDENCE_{rec_conf}"],
    )


def classify_forward_outcome(
    order: models.PaperOrder,
    review_record: Optional[Dict[str, Any]] = None,
    as_of_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """PHẦN 034, 044: Pure classification of an order's outcome using authoritative outcome extraction.
    Classifications:
    - CONFIRMED: Closed with valid authority, finite PnL, no unresolved conflict.
    - CONSERVATIVE_SIMULATION: Closed under ambiguous/conservative recovery assumption; excluded from clean confirmed denominator.
    - UNVERIFIED: Closed but missing canonical exit cause or closed_at timestamp or unverified recovery.
    - REVIEW_PENDING: Has active background reconciliation review task or unresolved recovery.
    - CONFLICTED: Has terminal conflicting evidence or requires manual review.
    - OPEN: Active paper_open position.
    - ARMED: Armed order waiting for trigger.
    - REJECTED_OR_EXPIRED: Expired, invalidated, or rejected order.
    - DATA_INVALID: Non-finite PnL (NaN, Inf) or missing essential price data.
    """
    state = getattr(order, "state", "") or ""

    if state == "armed":
        return {
            "classification": "ARMED",
            "countable_for_confirmed": False,
            "outcome_type": None,
            "pnl": None,
            "r": None,
            "reasons": ["ARMED_ORDER"],
        }

    if state in ("draft", "candidate", "expired", "invalidated", "rejected", "cancelled"):
        return {
            "classification": "REJECTED_OR_EXPIRED",
            "countable_for_confirmed": False,
            "outcome_type": None,
            "pnl": None,
            "r": None,
            "reasons": [state.upper()],
        }

    if state == "paper_open":
        return {
            "classification": "OPEN",
            "countable_for_confirmed": False,
            "outcome_type": None,
            "pnl": None,
            "r": None,
            "reasons": ["OPEN_POSITION"],
        }

    if state == "closed":
        # Check if as_of_ms excludes this close
        if as_of_ms is not None and getattr(order, "closed_at", None):
            if order.closed_at > as_of_ms:
                return {
                    "classification": "OPEN",
                    "countable_for_confirmed": False,
                    "outcome_type": None,
                    "pnl": None,
                    "r": None,
                    "reasons": ["CLOSED_AFTER_AS_OF"],
                }

        pnl = getattr(order, "realized_pnl_net", None)
        if pnl is None:
            return {
                "classification": "DATA_INVALID",
                "countable_for_confirmed": False,
                "outcome_type": None,
                "pnl": None,
                "r": None,
                "reasons": ["MISSING_REALIZED_PNL"],
            }
        if not math.isfinite(pnl):
            return {
                "classification": "DATA_INVALID",
                "countable_for_confirmed": False,
                "outcome_type": None,
                "pnl": None,
                "r": None,
                "reasons": ["NON_FINITE_REALIZED_PNL"],
            }

        auth = read_outcome_authority(order, review_record=review_record)
        r_val = getattr(order, "realized_r", None)
        valid_r = r_val if (r_val is not None and math.isfinite(r_val)) else None
        outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

        if auth.status == "CONFLICTED":
            return {
                "classification": "CONFLICTED",
                "countable_for_confirmed": False,
                "outcome_type": None,
                "pnl": None,
                "r": None,
                "reasons": auth.reason_codes or ["CONFLICTED"],
            }
        if auth.status == "UNRESOLVED":
            return {
                "classification": "REVIEW_PENDING",
                "countable_for_confirmed": False,
                "outcome_type": None,
                "pnl": None,
                "r": None,
                "reasons": auth.reason_codes or ["UNRESOLVED"],
            }
        if auth.status == "CONSERVATIVE_SIMULATION":
            return {
                "classification": "CONSERVATIVE_SIMULATION",
                "countable_for_confirmed": False,
                "outcome_type": outcome,
                "pnl": float(pnl),
                "r": valid_r,
                "reasons": auth.reason_codes or ["CONSERVATIVE_SIMULATION"],
            }
        if auth.status == "UNVERIFIED":
            return {
                "classification": "UNVERIFIED",
                "countable_for_confirmed": False,
                "outcome_type": None,
                "pnl": None,
                "r": None,
                "reasons": auth.reason_codes or ["UNVERIFIED"],
            }

        # VERIFIED_PAPER
        return {
            "classification": "CONFIRMED",
            "countable_for_confirmed": True,
            "outcome_type": outcome,
            "pnl": float(pnl),
            "r": valid_r,
            "reasons": [],
        }

    return {
        "classification": "DATA_INVALID",
        "countable_for_confirmed": False,
        "outcome_type": None,
        "pnl": None,
        "r": None,
        "reasons": [f"UNKNOWN_STATE_{state}"],
    }


# ---------------------------------------------------------------------------
# PHẦN 046–050: Correct Realized Drawdown Algorithm
# ---------------------------------------------------------------------------

def compute_realized_drawdown(
    starting_capital: float,
    ordered_cashflows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """PHẦN 046, 047, 050: Computes true maximum realized drawdown from canonical closed trade cashflows.
    Guarantees:
    - Calculates drawdown percentage at EACH step using the running peak at THAT step.
    - USD maximum and percentage maximum are computed independently.
    - Preserves references for peaks and troughs.
    - Handles required edge cases:
        1000 -> 900 -> 2000 => Max DD USD: 100, Max DD %: 10.0%
        1000 -> 800 -> 2000 -> 1700 => Max DD USD: 300, Max DD %: 20.0%
    """
    if not math.isfinite(starting_capital) or starting_capital <= 0:
        return {
            "max_drawdown_usd": 0.0,
            "max_drawdown_pct": 0.0,
            "status": "DATA_INVALID",
            "peak_usd_ref": None,
            "trough_usd_ref": None,
            "peak_pct_ref": None,
            "trough_pct_ref": None,
        }

    if not ordered_cashflows:
        return {
            "max_drawdown_usd": 0.0,
            "max_drawdown_pct": 0.0,
            "status": "NO_TRADES",
            "peak_usd_ref": starting_capital,
            "trough_usd_ref": starting_capital,
            "peak_pct_ref": starting_capital,
            "trough_pct_ref": starting_capital,
        }

    # Sort cashflows deterministically by closed_at then order_id
    sorted_flows = sorted(
        ordered_cashflows,
        key=lambda x: (int(x.get("closed_at") or 0), str(x.get("order_id") or "")),
    )

    equity = starting_capital
    running_peak = starting_capital

    max_dd_usd = 0.0
    max_dd_pct = 0.0

    peak_usd_ref = starting_capital
    trough_usd_ref = starting_capital
    peak_pct_ref = starting_capital
    trough_pct_ref = starting_capital

    for flow in sorted_flows:
        pnl = float(flow.get("net_pnl") or 0.0)
        equity += pnl

        if equity > running_peak:
            running_peak = equity

        dd_usd = running_peak - equity
        dd_pct = (dd_usd / running_peak * 100.0) if running_peak > 0 else 0.0

        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
            peak_usd_ref = running_peak
            trough_usd_ref = equity

        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            peak_pct_ref = running_peak
            trough_pct_ref = equity

    return {
        "max_drawdown_usd": round(max_dd_usd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "status": "OK",
        "peak_usd_ref": round(peak_usd_ref, 2),
        "trough_usd_ref": round(trough_usd_ref, 2),
        "peak_pct_ref": round(peak_pct_ref, 2),
        "trough_pct_ref": round(trough_pct_ref, 2),
    }


# ---------------------------------------------------------------------------
# PHẦN 055–058: Truthful Statistical Report Generator
# ---------------------------------------------------------------------------

def generate_forward_paper_report(
    db: Session,
    session_id: str,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """PHẦN 055–058: Generates truthful statistical report for a forward paper practice session.
    Guarantees:
    - Pure read query: does NOT mutate trading state in DB.
    - Confirmed outcomes only in winrate/drawdown denominator.
    - Realized drawdown computed from closed trade equity curve via point-in-time running peak algorithm.
    - Safe math: no NaN, no Infinity.
    - INSUFFICIENT_SAMPLE flag if < 30 confirmed trades.
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    sess = load_forward_session(db, session_id)
    if not sess:
        return {
            "session_id": session_id,
            "status": "NOT_FOUND",
            "error": f"Forward session {session_id} not found",
            "days_observed": 0,
            "total_fills": 0,
            "confirmed_closed_trades": 0,
            "confirmed_wins": 0,
            "confirmed_losses": 0,
            "breakevens": 0,
            "net_pnl": 0.0,
            "winrate": 0.0,
            "profit_factor": None,
            "expectancy_r": 0.0,
            "realized_drawdown_usd": 0.0,
            "realized_drawdown_pct": 0.0,
            "pending_count": 0,
            "review_count": 0,
            "sample_status": "INSUFFICIENT_SAMPLE",
            "strategy_breakdown": {},
            "session_breakdown": {},
            "needs_improvement": [],
            "next_steps": [],
        }

    start_ts = sess.start_time_ms
    starting_capital = sess.virtual_capital

    # PHẦN 002, 025: Strict forward membership selector
    orders = load_forward_session_orders(db, session_id, as_of_ms=now_ms)

    # Preload review records for session orders only (no leak from other sessions)
    session_order_ids = {o.id for o in orders}
    review_map: Dict[str, Dict[str, Any]] = {}
    orphan_reviews = 0
    all_rev_configs = db.query(models.SystemConfig).filter(
        models.SystemConfig.key.like("recon_rev:%")
    ).all()
    for rc in all_rev_configs:
        ord_id = rc.key.split("recon_rev:", 1)[-1]
        if ord_id in session_order_ids:
            try:
                review_map[ord_id] = json.loads(rc.value)
            except Exception:
                pass
        else:
            # Check if this review belongs to an order in another forward session
            other_order = db.query(models.PaperOrder).filter(models.PaperOrder.id == ord_id).first()
            if not other_order:
                # Orphan review record in database (e.g. test fixture without PaperOrder row)
                try:
                    rev_data = json.loads(rc.value)
                    if rev_data.get("status") in ("PENDING_REVIEW", "RETRY_SCHEDULED", "RETRYING", "MANUAL_REVIEW_REQUIRED"):
                        orphan_reviews += 1
                except Exception:
                    pass

    total_fills = 0
    confirmed_closed: List[models.PaperOrder] = []
    cashflows: List[Dict[str, Any]] = []

    wins: List[models.PaperOrder] = []
    losses: List[models.PaperOrder] = []
    breakevens: List[models.PaperOrder] = []

    open_orders: List[models.PaperOrder] = []
    armed_orders: List[models.PaperOrder] = []
    review_pending_orders: List[models.PaperOrder] = []
    unverified_orders: List[models.PaperOrder] = []
    conservative_simulation_orders: List[models.PaperOrder] = []
    data_invalid_orders: List[models.PaperOrder] = []

    valid_r_list: List[float] = []
    missing_r_count = 0

    for o in orders:
        st = getattr(o, "state", "") or ""
        if st in ("paper_open", "closed"):
            total_fills += 1

        rev_rec = review_map.get(o.id)
        classification = classify_forward_outcome(o, review_record=rev_rec, as_of_ms=now_ms)
        cls_code = classification["classification"]

        if cls_code == "CONFIRMED":
            confirmed_closed.append(o)
            cashflows.append({
                "order_id": o.id,
                "net_pnl": classification["pnl"],
                "closed_at": getattr(o, "closed_at", 0) or 0,
            })
            out_type = classification["outcome_type"]
            if out_type == "WIN":
                wins.append(o)
            elif out_type == "LOSS":
                losses.append(o)
            elif out_type == "BREAKEVEN":
                breakevens.append(o)

            if classification["r"] is not None:
                valid_r_list.append(classification["r"])
            else:
                missing_r_count += 1

        elif cls_code == "OPEN":
            open_orders.append(o)
        elif cls_code == "ARMED":
            armed_orders.append(o)
        elif cls_code in ("REVIEW_PENDING", "CONFLICTED"):
            review_pending_orders.append(o)
        elif cls_code == "CONSERVATIVE_SIMULATION":
            conservative_simulation_orders.append(o)
        elif cls_code == "UNVERIFIED":
            unverified_orders.append(o)
        elif cls_code == "DATA_INVALID":
            data_invalid_orders.append(o)

    # Days observed
    if start_ts > 0:
        dt_start = datetime.fromtimestamp(start_ts / 1000, tz=VN_TZ)
        dt_end = datetime.fromtimestamp(now_ms / 1000, tz=VN_TZ)
        days_observed = max(1, (dt_end.date() - dt_start.date()).days + 1)
    else:
        days_observed = 0

    confirmed_count = len(confirmed_closed)
    net_pnl = sum((o.realized_pnl_net or 0.0) for o in confirmed_closed)
    winrate = (len(wins) / confirmed_count) if confirmed_count > 0 else 0.0

    # Profit factor
    total_win_pnl = sum((o.realized_pnl_net or 0.0) for o in wins)
    total_loss_pnl = sum((o.realized_pnl_net or 0.0) for o in losses)
    if abs(total_loss_pnl) > 0:
        profit_factor: Optional[float] = round(total_win_pnl / abs(total_loss_pnl), 3)
    else:
        profit_factor = None

    # Expectancy in R
    expectancy_r = round(sum(valid_r_list) / len(valid_r_list), 3) if valid_r_list else 0.0

    # Drawdown using true point-in-time algorithm
    dd_result = compute_realized_drawdown(starting_capital, cashflows)

    # Strategy family split
    standard_trades = [o for o in confirmed_closed if getattr(o, "strategy_family", "") == "STANDARD_SMC"]
    fallback_trades = [o for o in confirmed_closed if getattr(o, "strategy_family", "") == "NY_QUOTA_PAPER"]

    def _family_stats(trades: List[models.PaperOrder]) -> Dict[str, Any]:
        cnt = len(trades)
        w = [t for t in trades if (t.realized_pnl_net or 0.0) > 0]
        l = [t for t in trades if (t.realized_pnl_net or 0.0) < 0]
        be = [t for t in trades if (t.realized_pnl_net or 0.0) == 0]
        pnl = sum((t.realized_pnl_net or 0.0) for t in trades)
        wr = (len(w) / cnt) if cnt > 0 else 0.0
        return {
            "total_trades": cnt,
            "wins": len(w),
            "losses": len(l),
            "breakevens": len(be),
            "net_pnl": round(pnl, 2),
            "winrate": round(wr, 3),
        }

    # Session split (canonical DST-aware resolution + explicit UNKNOWN_OR_OFF bucket)
    session_map: Dict[str, List[models.PaperOrder]] = {
        "ASIA": [],
        "LONDON": [],
        "NEW_YORK": [],
        "UNKNOWN_OR_OFF": [],
    }
    for o in confirmed_closed:
        sess_tag = getattr(o, "session_instance_id", "") or ""
        tag_upper = sess_tag.upper()
        if "ASIA" in tag_upper or "TOKYO" in tag_upper:
            session_map["ASIA"].append(o)
        elif "LON" in tag_upper:
            session_map["LONDON"].append(o)
        elif "NY" in tag_upper or "NEW_YORK" in tag_upper:
            session_map["NEW_YORK"].append(o)
        else:
            op_at = getattr(o, "opened_at", 0) or getattr(o, "created_at", 0) or 0
            if op_at > 0:
                try:
                    from services.smc_scenario_service import session_for
                    s_name, _ = session_for(op_at)
                    if s_name == "ASIA":
                        session_map["ASIA"].append(o)
                    elif s_name == "LONDON":
                        session_map["LONDON"].append(o)
                    elif s_name == "NY":
                        session_map["NEW_YORK"].append(o)
                    else:
                        session_map["UNKNOWN_OR_OFF"].append(o)
                except Exception:
                    session_map["UNKNOWN_OR_OFF"].append(o)
            else:
                session_map["UNKNOWN_OR_OFF"].append(o)

    session_breakdown = {
        s_name: _family_stats(s_orders)
        for s_name, s_orders in session_map.items()
    }

    sample_status = "SUFFICIENT_SAMPLE" if confirmed_count >= 30 else "INSUFFICIENT_SAMPLE"

    active_review_count = len(review_pending_orders) + orphan_reviews
    needs_improvement = []
    if active_review_count > 0:
        needs_improvement.append(f"{active_review_count} order(s) require reconciliation review or quarantine inspection")
    if sample_status == "INSUFFICIENT_SAMPLE":
        needs_improvement.append(f"Sample count ({confirmed_count}) below 30-trade minimum for statistical edge validation")
    if len(losses) > len(wins) and confirmed_count > 5:
        needs_improvement.append("Loss count exceeds wins; review stop-loss placement and liquidity sweep confirmations")

    next_steps = [
        "Continue forward practice without manual intervention or strategy parameter adjustments",
        "Maintain strict NY session quota and single position limit",
    ]
    if active_review_count > 0:
        next_steps.insert(0, "Investigate corrupt snapshots or data gaps for quarantined review orders")

    return {
        "session_id": session_id,
        "days_observed": days_observed,
        "total_fills": total_fills,
        "confirmed_closed_trades": confirmed_count,
        "confirmed_wins": len(wins),
        "confirmed_losses": len(losses),
        "breakevens": len(breakevens),
        "net_pnl": round(net_pnl, 2),
        "winrate": round(winrate, 3),
        "profit_factor": profit_factor,
        "expectancy_r": expectancy_r,
        "valid_r_count": len(valid_r_list),
        "missing_r_count": missing_r_count,
        "realized_drawdown_usd": dd_result["max_drawdown_usd"],
        "realized_drawdown_pct": dd_result["max_drawdown_pct"],
        "drawdown_details": dd_result,
        "pending_count": len(open_orders),
        "armed_count": len(armed_orders),
        "review_count": active_review_count,
        "unverified_count": len(unverified_orders) + len(conservative_simulation_orders),
        "conservative_simulation_count": len(conservative_simulation_orders),
        "data_invalid_count": len(data_invalid_orders),
        "sample_status": sample_status,
        "strategy_breakdown": {
            "standard_smc": _family_stats(standard_trades),
            "ny_quota_paper": _family_stats(fallback_trades),
            "combined": _family_stats(confirmed_closed),
        },
        "session_breakdown": session_breakdown,
        "needs_improvement": needs_improvement,
        "next_steps": next_steps,
    }


def export_forward_paper_data(
    db: Session,
    session_id: str,
    format: str = "dict",
) -> Any:
    """PHẦN 044, 071: Exports forward paper trade records strictly from the session membership.
    Does not invent fabricated values; missing values remain None / empty.
    """
    orders = load_forward_session_orders(db, session_id)

    # Preload review records
    session_order_ids = {o.id for o in orders}
    review_map: Dict[str, Dict[str, Any]] = {}
    if session_order_ids:
        rev_configs = db.query(models.SystemConfig).filter(
            models.SystemConfig.key.like("recon_rev:%")
        ).all()
        for rc in rev_configs:
            ord_id = rc.key.split("recon_rev:", 1)[-1]
            if ord_id in session_order_ids:
                try:
                    review_map[ord_id] = json.loads(rc.value)
                except Exception:
                    pass

    rows = []
    for o in orders:
        rev_rec = review_map.get(o.id)
        cls_info = classify_forward_outcome(o, review_record=rev_rec)

        trade_rev = db.query(models.TradeReview).filter(models.TradeReview.trade_id == o.id).first()
        row = {
            "forward_session_id": session_id,
            "order_id": o.id,
            "session_instance_id": getattr(o, "session_instance_id", "") or "",
            "direction": o.direction,
            "strategy_family": getattr(o, "strategy_family", "STANDARD_SMC"),
            "state": o.state,
            "outcome_classification": cls_info["classification"],
            "review_status": rev_rec.get("status") if rev_rec else None,
            "review_reason": (rev_rec.get("reason_codes") or [None])[0] if rev_rec else None,
            "planned_entry": o.planned_entry,
            "actual_entry": o.actual_entry,
            "stop_loss": o.stop_loss,
            "take_profit": o.take_profit,
            "actual_exit": o.actual_exit,
            "fees": getattr(o, "fees_assumption", 0.0),
            "realized_pnl_net": o.realized_pnl_net,
            "realized_r": o.realized_r,
            "exit_cause": o.exit_cause,
            "invalidation_reason": o.invalidation_reason,
            "user_notes": trade_rev.user_notes if trade_rev else None,
            "psychology": trade_rev.psychology_before if trade_rev else None,
            "created_at": o.created_at,
            "closed_at": o.closed_at,
        }
        rows.append(row)

    if format == "csv":
        if not rows:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()

    return rows


# Backward-compatible alias
build_forward_session_report = generate_forward_paper_report
