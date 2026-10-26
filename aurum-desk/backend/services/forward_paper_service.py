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
import hashlib
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


# ---------------------------------------------------------------------------
# V14.4.15: Terminal Event View & Durable Membership Repair Task Contracts
# ---------------------------------------------------------------------------

KNOWN_NORMAL_RECOVERY_STATUSES: Set[Optional[str]] = {"NONE", "UP_TO_DATE", None, ""}
KNOWN_RECOVERED_STATUSES: Set[str] = {"RECOVERED"}
KNOWN_UNRESOLVED_STATUSES: Set[str] = {"UNRESOLVED_GAP", "RECOVERING", "RECOVERY_INCOMPLETE", "UNRESOLVED"}
KNOWN_ALL_RECOVERY_STATUSES: Set[Optional[str]] = (
    KNOWN_NORMAL_RECOVERY_STATUSES | KNOWN_RECOVERED_STATUSES | KNOWN_UNRESOLVED_STATUSES
)


@dataclass(frozen=True)
class TerminalEventView:
    event_id: str
    event_type: str
    order_id: str
    aggregate_version: int
    occurred_at: int
    published_at: int
    exit_cause: str
    actual_exit: float
    realized_pnl: float
    direction: str
    actual_entry: Optional[float] = None
    realized_r: Optional[float] = None
    recovery_status: Optional[str] = None
    strategy_family: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class TerminalEventDecodeResult:
    status: str  # "VALID", "INVALID", "UNSUPPORTED", "MISSING"
    event_view: Optional[TerminalEventView] = None
    reason_code: Optional[str] = None


@dataclass
class ForwardMembershipRepairTask:
    task_id: str
    order_id: str
    forward_session_id: Optional[str] = None
    target_status: str = "CLOSED"  # "ARMED", "FILLED", "CLOSED", "REJECTED"
    status: str = "PENDING"  # "PENDING", "RETRY_SCHEDULED", "PROCESSING", "REPAIRED", "MANUAL_REVIEW_REQUIRED"
    attempts: int = 0
    max_attempts: int = 5
    next_retry_at_ms: Optional[int] = None
    created_at_ms: int = 0
    updated_at_ms: int = 0
    repaired_at_ms: Optional[int] = None
    reason_codes: List[str] = field(default_factory=list)
    last_error: Optional[str] = None
    original_damaged_payload: Optional[str] = None
    source_revision: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "order_id": self.order_id,
            "forward_session_id": self.forward_session_id,
            "target_status": self.target_status,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "next_retry_at_ms": self.next_retry_at_ms,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "repaired_at_ms": self.repaired_at_ms,
            "reason_codes": self.reason_codes,
            "last_error": self.last_error,
            "original_damaged_payload": self.original_damaged_payload,
            "source_revision": self.source_revision,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ForwardMembershipRepairTask":
        return cls(
            task_id=str(data.get("task_id", "")),
            order_id=str(data.get("order_id", "")),
            forward_session_id=data.get("forward_session_id"),
            target_status=str(data.get("target_status", "CLOSED")),
            status=str(data.get("status", "PENDING")),
            attempts=int(data.get("attempts", 0)),
            max_attempts=int(data.get("max_attempts", 5)),
            next_retry_at_ms=data.get("next_retry_at_ms"),
            created_at_ms=int(data.get("created_at_ms", 0)),
            updated_at_ms=int(data.get("updated_at_ms", 0)),
            repaired_at_ms=data.get("repaired_at_ms"),
            reason_codes=list(data.get("reason_codes") or []),
            last_error=data.get("last_error"),
            original_damaged_payload=data.get("original_damaged_payload"),
            source_revision=int(data.get("source_revision", 1)),
        )


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

    default_min_rr = 2.0
    if db is not None:
        try:
            from services.rr_policy_service import resolve_effective_rr_policy, RrPolicyConfigError
            rr_ctx = resolve_effective_rr_policy(db)
            if rr_ctx and rr_ctx.min_net_rr is not None and rr_ctx.min_net_rr >= 1.5:
                default_min_rr = float(rr_ctx.min_net_rr)
        except Exception as e:
            from services.rr_policy_service import RrPolicyConfigError
            if isinstance(e, RrPolicyConfigError):
                raise
            pass

    default_risk = {
        "risk_pct": 0.25,
        "max_daily_fills": 3,
        "min_net_rr": default_min_rr,
        "leverage": 5,
        "margin_mode": "ISOLATED",
    }
    if risk_config:
        frozen_risk = {**default_risk, **risk_config}
        if "min_net_rr" not in risk_config or risk_config["min_net_rr"] is None:
            frozen_risk["min_net_rr"] = default_min_rr
        else:
            override_val = risk_config["min_net_rr"]
            if isinstance(override_val, bool) or not isinstance(override_val, (int, float)) or not math.isfinite(override_val) or override_val < 1.0:
                raise ValueError(f"Giá trị min_net_rr không hợp lệ trong risk_config: {override_val!r}")
            frozen_risk["min_net_rr"] = round(float(override_val), 4)
    else:
        frozen_risk = default_risk

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
# V14.4.15: Durable Forward Membership Repair Functions
# ---------------------------------------------------------------------------

def _repair_task_key(order_id: str) -> str:
    key = f"fwd_rep:{order_id}"
    if len(key) <= 50:
        return key
    h = hashlib.sha256(order_id.encode()).hexdigest()[:40]
    return f"fwd_rep:{h}"


def _append_repair_task_to_queue(db: Session, order_id: str, now_ms: int) -> bool:
    key = "fwd_repair_queue"
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


def stage_forward_repair_task(
    db: Session,
    order_id: str,
    forward_session_id: Optional[str] = None,
    target_status: str = "CLOSED",
    reason_codes: Optional[List[str]] = None,
    now_ms: Optional[int] = None,
    original_damaged_payload: Optional[str] = None,
) -> Optional[ForwardMembershipRepairTask]:
    """PHẦN 031, 034, 037, 038, 040: Stages a durable forward membership repair task.
    Guarantees:
    - Never calls db.commit() or db.rollback().
    - Persists atomically with lifecycle owner transaction under key fwd_rep:<order_id>.
    - Idempotent: does not duplicate tasks for same order.
    - Monotonic: cannot downgrade CLOSED to FILLED.
    - Resolves forward_session_id from existing sources; does NOT default to active session.
    - If order has no forward intent, returns None (no-op, per PHẦN 037).
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    reasons = list(reason_codes or [])

    # 1. Resolve forward_session_id if not explicitly provided
    resolved_session_id = forward_session_id
    damaged_payload = original_damaged_payload

    mem_key = f"fwd_mem:{order_id}"[:50]
    rec_mem = db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
    has_mem_record = rec_mem is not None and bool(rec_mem.value)

    if damaged_payload is None and has_mem_record:
        damaged_payload = rec_mem.value
        if not resolved_session_id:
            try:
                parsed = json.loads(rec_mem.value)
                if isinstance(parsed, dict):
                    resolved_session_id = parsed.get("forward_session_id")
            except Exception:
                pass

    if not resolved_session_id:
        matching_sessions = []
        sess_configs = db.query(models.SystemConfig).filter(
            models.SystemConfig.key.like("fwd_sess_ords:%")
        ).all()
        for sc in sess_configs:
            try:
                ord_list = json.loads(sc.value)
                if isinstance(ord_list, list) and order_id in ord_list:
                    s_id = sc.key.split("fwd_sess_ords:", 1)[-1]
                    matching_sessions.append(s_id)
            except Exception:
                pass

        if len(matching_sessions) == 1:
            resolved_session_id = matching_sessions[0]
        elif len(matching_sessions) > 1:
            reasons.append("CONFLICTING_SESSION_INDEXES")

    # If still no session resolved:
    # Check if order has forward intent
    has_forward_intent = has_mem_record
    if not has_forward_intent:
        order_obj = db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
        if order_obj:
            sess_inst = getattr(order_obj, "session_instance_id", "") or ""
            if sess_inst.startswith("fwd_"):
                has_forward_intent = True
                resolved_session_id = sess_inst

    if not has_forward_intent and not resolved_session_id:
        # PHẦN 037: Order without forward intent is a no-op
        return None

    # Status precedence: ARMED (1) < FILLED (2) < CLOSED (3) / REJECTED (3)
    status_ranks = {"ARMED": 1, "FILLED": 2, "CLOSED": 3, "REJECTED": 3}
    target_rank = status_ranks.get(target_status, 1)

    task_key = _repair_task_key(order_id)
    rec_task = db.query(models.SystemConfig).filter(models.SystemConfig.key == task_key).first()

    initial_status = "PENDING"
    if not resolved_session_id:
        initial_status = "MANUAL_REVIEW_REQUIRED"
        if "UNKNOWN_FORWARD_SESSION_INTENT" not in reasons:
            reasons.append("UNKNOWN_FORWARD_SESSION_INTENT")

    if rec_task and rec_task.value:
        try:
            existing_task = ForwardMembershipRepairTask.from_dict(json.loads(rec_task.value))
            existing_rank = status_ranks.get(existing_task.target_status, 1)
            # Monotonic: don't downgrade target status
            effective_target = target_status if target_rank >= existing_rank else existing_task.target_status
            existing_reasons = set(existing_task.reason_codes) | set(reasons)

            existing_task.target_status = effective_target
            existing_task.reason_codes = list(existing_reasons)
            existing_task.updated_at_ms = now_ms
            if resolved_session_id and not existing_task.forward_session_id:
                existing_task.forward_session_id = resolved_session_id
                if existing_task.status == "MANUAL_REVIEW_REQUIRED":
                    existing_task.status = "PENDING"
            if damaged_payload:
                existing_task.original_damaged_payload = damaged_payload

            rec_task.value = json.dumps(existing_task.to_dict())
            rec_task.updated_at = now_ms
            _append_repair_task_to_queue(db, order_id, now_ms)
            db.flush()
            return existing_task
        except Exception:
            pass

    task_id = f"fwd_rep_{order_id}"
    new_task = ForwardMembershipRepairTask(
        task_id=task_id,
        order_id=order_id,
        forward_session_id=resolved_session_id,
        target_status=target_status,
        status=initial_status,
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        reason_codes=reasons,
        original_damaged_payload=damaged_payload,
    )
    val_json = json.dumps(new_task.to_dict())
    if not rec_task:
        rec_task = models.SystemConfig(key=task_key, value=val_json, updated_at=now_ms)
        db.add(rec_task)
    else:
        rec_task.value = val_json
        rec_task.updated_at = now_ms

    _append_repair_task_to_queue(db, order_id, now_ms)
    db.flush()
    return new_task


def load_due_forward_repairs(
    db: Session,
    now_ms: Optional[int] = None,
    limit: int = 50,
) -> List[ForwardMembershipRepairTask]:
    """PHẦN 042, 033, 034: Loads bounded list of due forward membership repair tasks using a durable cursor.
    Guarantees fair keyset round-robin scanning so prefix repaired/manual tasks do not starve tail tasks.
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)

    queue_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == "fwd_repair_queue").first()
    if not queue_rec or not queue_rec.value:
        return []

    try:
        order_ids = json.loads(queue_rec.value)
        if not isinstance(order_ids, list):
            return []
    except Exception:
        return []

    total_queue = len(order_ids)
    if total_queue == 0:
        return []

    # Read durable cursor
    last_index = 0
    cursor_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == "fwd_repair_cursor").first()
    if cursor_rec and cursor_rec.value:
        try:
            cdata = json.loads(cursor_rec.value)
            if isinstance(cdata, dict):
                last_index = int(cdata.get("last_index", 0))
        except Exception:
            last_index = 0

    start_idx = last_index % total_queue if total_queue > 0 else 0

    # We inspect up to min(total_queue, max(limit * 3, 30)) items per cycle starting from start_idx
    inspect_limit = min(total_queue, max(limit * 3, 30))
    due_tasks: List[ForwardMembershipRepairTask] = []
    inspected_count = 0

    for i in range(total_queue):
        if inspected_count >= inspect_limit or len(due_tasks) >= limit:
            break
        idx = (start_idx + i) % total_queue
        inspected_count += 1
        ord_id = order_ids[idx]

        task_key = _repair_task_key(ord_id)
        task_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == task_key).first()
        if not task_rec or not task_rec.value:
            continue
        try:
            task = ForwardMembershipRepairTask.from_dict(json.loads(task_rec.value))
            if task.status in ("PENDING", "RETRY_SCHEDULED"):
                if task.next_retry_at_ms is None or task.next_retry_at_ms <= now_ms:
                    due_tasks.append(task)
        except Exception:
            continue

    # Update durable cursor to advance beyond inspected items
    new_last_index = (start_idx + inspected_count) % total_queue
    cursor_val = json.dumps({"last_index": new_last_index, "updated_at_ms": now_ms})
    if not cursor_rec:
        cursor_rec = models.SystemConfig(key="fwd_repair_cursor", value=cursor_val, updated_at=now_ms)
        db.add(cursor_rec)
    else:
        cursor_rec.value = cursor_val
        cursor_rec.updated_at = now_ms

    try:
        db.commit()
    except Exception:
        db.rollback()

    return due_tasks


# ---------------------------------------------------------------------------
# V14.4.17: Strict Repair Provenance, Original Config Preservation & Constants
# ---------------------------------------------------------------------------

REPAIR_PROOF_MISSING = "REPAIR_PROOF_MISSING"
REPAIR_PROOF_MALFORMED = "REPAIR_PROOF_MALFORMED"
REPAIR_PROOF_INVALID_SCHEMA = "REPAIR_PROOF_INVALID_SCHEMA"
REPAIR_SESSION_NOT_FOUND = "REPAIR_SESSION_NOT_FOUND"
REPAIR_SESSION_CONFLICT = "REPAIR_SESSION_CONFLICT"
REPAIR_ORDER_CONFLICT = "REPAIR_ORDER_CONFLICT"
REPAIR_CONFIG_REVISION_MISSING = "REPAIR_CONFIG_REVISION_MISSING"
REPAIR_CONFIG_REVISION_CONFLICT = "REPAIR_CONFIG_REVISION_CONFLICT"
REPAIR_RUNTIME_COMMIT_MISSING = "REPAIR_RUNTIME_COMMIT_MISSING"
REPAIR_RUNTIME_COMMIT_CONFLICT = "REPAIR_RUNTIME_COMMIT_CONFLICT"
REPAIR_BOUND_AT_MISSING = "REPAIR_BOUND_AT_MISSING"
REPAIR_BOUND_AT_CONFLICT = "REPAIR_BOUND_AT_CONFLICT"
REPAIR_SOURCE_CONFLICT = "REPAIR_SOURCE_CONFLICT"
REPAIR_INDEX_CONFLICT = "REPAIR_INDEX_CONFLICT"
REPAIR_SOURCE_UNVERIFIED = "REPAIR_SOURCE_UNVERIFIED"
REPAIR_SOURCE_ORDER_MISMATCH = "REPAIR_SOURCE_ORDER_MISMATCH"


@dataclass(frozen=True)
class RepairProvenanceResolution:
    """PHẦN 018: Structured resolution for forward paper membership repair provenance."""
    status: str  # "VERIFIED", "INSUFFICIENT", "CONFLICT"
    forward_session_id: Optional[str] = None
    config_revision: Optional[int] = None
    runtime_commit: Optional[str] = None
    bound_at_ms: Optional[int] = None
    source_names: List[str] = field(default_factory=list)
    reason_codes: List[str] = field(default_factory=list)
    diagnostics: Optional[Dict[str, Any]] = None


@dataclass
class RepairProvenanceSource:
    """PHẦN 021: Structured representation of a repair provenance candidate source."""
    name: str
    kind: str  # "ORIGINAL_BINDING", "LEGACY_BINDING", "REPAIRED_MEMBERSHIP", "INDEX_REFERENCE", "TASK_REQUEST"
    parse_status: str  # "VALID", "MALFORMED", "INVALID_SCHEMA", "ABSENT"
    payload: Optional[Dict[str, Any]] = None
    reason_codes: List[str] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def parse_repair_source_payload(raw_payload: Optional[str]) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """PHẦN 020, 021, 022: Pure structured parser for membership payload strings.
    Distinguishes: ABSENT, VALID, MALFORMED, INVALID_SCHEMA.
    Checks field types strictly without applying loose defaults.
    """
    if raw_payload is None or not str(raw_payload).strip():
        return "ABSENT", None, REPAIR_PROOF_MISSING

    raw_str = str(raw_payload).strip()
    try:
        parsed = json.loads(raw_str)
    except Exception:
        return "MALFORMED", None, REPAIR_PROOF_MALFORMED

    if not isinstance(parsed, dict):
        return "INVALID_SCHEMA", None, REPAIR_PROOF_INVALID_SCHEMA

    # Validate specific fields if present
    if "config_revision" in parsed:
        cr = parsed["config_revision"]
        if isinstance(cr, bool) or not isinstance(cr, int) or cr <= 0:
            return "INVALID_SCHEMA", None, REPAIR_PROOF_INVALID_SCHEMA

    if "bound_at_ms" in parsed:
        bat = parsed["bound_at_ms"]
        if isinstance(bat, bool) or not isinstance(bat, (int, float)) or not math.isfinite(bat) or bat <= 0:
            return "INVALID_SCHEMA", None, REPAIR_PROOF_INVALID_SCHEMA

    if "order_id" in parsed:
        oid = parsed["order_id"]
        if not isinstance(oid, str) or not oid.strip():
            return "INVALID_SCHEMA", None, REPAIR_PROOF_INVALID_SCHEMA

    if "forward_session_id" in parsed:
        fsid = parsed["forward_session_id"]
        if fsid is not None and (not isinstance(fsid, str) or not fsid.strip()):
            return "INVALID_SCHEMA", None, REPAIR_PROOF_INVALID_SCHEMA

    if "runtime_commit" in parsed:
        rc = parsed["runtime_commit"]
        if rc is not None and (not isinstance(rc, str) or not rc.strip()):
            return "INVALID_SCHEMA", None, REPAIR_PROOF_INVALID_SCHEMA

    return "VALID", parsed, None


def validate_repair_binding_source(
    source: RepairProvenanceSource,
    order: models.PaperOrder,
) -> Dict[str, Any]:
    """PHẦN 025: Validates a single binding candidate source against the order.
    Checks order_id, forward_session_id, config_revision, runtime_commit, bound_at_ms.
    Returns dictionary with validation outcome, extracted fields, and reason codes.
    Zero mutations.
    """
    res: Dict[str, Any] = {
        "valid": False,
        "order_match": True,
        "is_complete": False,
        "fields": {},
        "reason_codes": [],
        "diagnostics": {},
    }
    if source.parse_status != "VALID" or not isinstance(source.payload, dict):
        res["reason_codes"].append(source.parse_status)
        return res

    data = source.payload

    # Order ID validation: record under order key claiming another order is a conflict
    if "order_id" in data:
        p_oid = str(data["order_id"]).strip()
        if p_oid and p_oid != order.id:
            res["order_match"] = False
            res["reason_codes"].extend([REPAIR_ORDER_CONFLICT, REPAIR_SOURCE_ORDER_MISMATCH])
            res["diagnostics"]["payload_order_id"] = p_oid
            res["diagnostics"]["expected_order_id"] = order.id
            return res

    fields: Dict[str, Any] = {}

    if "forward_session_id" in data:
        fsid = str(data["forward_session_id"]).strip()
        if fsid:
            fields["forward_session_id"] = fsid

    if "config_revision" in data:
        cr = data["config_revision"]
        if not isinstance(cr, bool) and isinstance(cr, int) and cr > 0:
            fields["config_revision"] = int(cr)
        else:
            res["reason_codes"].append(REPAIR_PROOF_INVALID_SCHEMA)

    if "runtime_commit" in data:
        rc = str(data["runtime_commit"]).strip()
        if rc:
            fields["runtime_commit"] = rc

    if "bound_at_ms" in data:
        bat = data["bound_at_ms"]
        if not isinstance(bat, bool) and isinstance(bat, (int, float)) and math.isfinite(bat) and bat > 0:
            fields["bound_at_ms"] = int(bat)
        else:
            res["reason_codes"].append(REPAIR_PROOF_INVALID_SCHEMA)

    res["fields"] = fields
    has_all_4 = (
        "forward_session_id" in fields
        and "config_revision" in fields
        and "runtime_commit" in fields
        and "bound_at_ms" in fields
    )
    res["is_complete"] = has_all_4
    res["valid"] = (len(res["reason_codes"]) == 0 and len(fields) > 0)
    return res


def classify_repair_binding_payload_origin(
    payload: Any,
    default_kind: str = "ORIGINAL_BINDING",
) -> str:
    """PHẦN 018, 019, 020, 021, 022: Shared pure helper to classify whether a payload
    originates from a repaired membership record or an original/legacy binding.
    Detects repair markers:
    - top-level 'repaired_by'
    - metadata['repaired_by']
    - metadata['repair_task_id']
    - metadata['provenance_sources']
    Pure function: no DB, no network, no mutations. Safely handles None, non-dict metadata.
    Returns: 'REPAIRED_MEMBERSHIP' if repaired marker detected, otherwise default_kind.
    """
    if not isinstance(payload, dict):
        return default_kind

    if payload.get("repaired_by"):
        return "REPAIRED_MEMBERSHIP"

    meta = payload.get("metadata")
    if isinstance(meta, dict):
        if meta.get("repaired_by") or meta.get("repair_task_id") or meta.get("provenance_sources"):
            return "REPAIRED_MEMBERSHIP"

    return default_kind


def is_complete_original_binding_proof(
    source: Optional[RepairProvenanceSource],
    validation: Dict[str, Any],
    order: models.PaperOrder,
) -> bool:
    """PHẦN 021, 022, 023, 024, 026: Determines whether a provenance source
    constitutes a complete, valid, independent original binding proof.
    Requires:
    - parse_status == "VALID" and payload is dict
    - kind != "REPAIRED_MEMBERSHIP" (cannot be repaired self-proof)
    - payload does not contain repaired origin markers
    - validation["valid"] is True
    - validation["order_match"] is True
    - validation["is_complete"] is True (has forward_session_id, config_revision, runtime_commit, bound_at_ms)
    - order identity explicitly confirmed in payload: str(payload.get("order_id", "")).strip() == order.id
    Pure read query, zero mutations.
    """
    if not source:
        return False
    if source.parse_status != "VALID" or not isinstance(source.payload, dict):
        return False
    if source.kind == "REPAIRED_MEMBERSHIP":
        return False
    if classify_repair_binding_payload_origin(source.payload) == "REPAIRED_MEMBERSHIP":
        return False
    if not validation.get("valid", False):
        return False
    if not validation.get("order_match", False):
        return False
    if not validation.get("is_complete", False):
        return False
    payload_order_id = str(source.payload.get("order_id", "")).strip()
    if not payload_order_id or payload_order_id != order.id:
        return False
    return True


def validate_repair_session_indexes(
    index_sessions: List[str],
    target_session_id: Optional[str],
) -> Tuple[str, List[str], Dict[str, Any]]:
    """PHẦN 042, 043, 044: Validates order presence across session order indexes.
    Returns: (status: "CONSISTENT" | "MISSING" | "CONFLICT", reason_codes, diagnostics).
    If order is present in any session other than target_session_id -> CONFLICT.
    Presence of target_session_id in multi-session indexes does NOT dismiss conflict.
    """
    clean_indexes = [str(s).strip() for s in index_sessions if str(s).strip()]
    target = str(target_session_id).strip() if target_session_id else None

    if not clean_indexes:
        return "MISSING", [], {"index_sessions": []}

    if target:
        conflicting = [s for s in clean_indexes if s != target]
        if conflicting:
            return (
                "CONFLICT",
                [REPAIR_INDEX_CONFLICT, REPAIR_SESSION_CONFLICT],
                {
                    "index_sessions": clean_indexes,
                    "target_session_id": target,
                    "conflicting_sessions": conflicting,
                },
            )
        return "CONSISTENT", [], {"index_sessions": clean_indexes}

    # If target is None but there are multiple index sessions
    if len(set(clean_indexes)) > 1:
        return (
            "CONFLICT",
            [REPAIR_INDEX_CONFLICT, REPAIR_SESSION_CONFLICT],
            {"index_sessions": clean_indexes},
        )
    return "CONSISTENT", [], {"index_sessions": clean_indexes}


def compare_repair_binding_sources(
    sources_to_compare: List[Tuple[str, Dict[str, Any]]],
) -> Tuple[str, Dict[str, Any], List[str], Dict[str, Any]]:
    """PHẦN 032, 033, 034: Compares binding fields across multiple sources.
    Strictly order-invariant (reversing sources list yields identical status and reason).
    Checks forward_session_id, config_revision, runtime_commit, bound_at_ms.
    Returns: (status: "VERIFIED" | "INSUFFICIENT" | "CONFLICT", resolved_fields, reason_codes, diagnostics).
    """
    if not sources_to_compare:
        return "INSUFFICIENT", {}, [REPAIR_PROOF_MISSING], {}

    claimed_sessions: Dict[str, str] = {}
    claimed_revisions: Dict[str, int] = {}
    claimed_commits: Dict[str, str] = {}
    claimed_bound_at: Dict[str, int] = {}

    for s_name, s_fields in sources_to_compare:
        if "forward_session_id" in s_fields and s_fields["forward_session_id"]:
            claimed_sessions[s_name] = str(s_fields["forward_session_id"]).strip()
        if "config_revision" in s_fields and s_fields["config_revision"] is not None:
            claimed_revisions[s_name] = int(s_fields["config_revision"])
        if "runtime_commit" in s_fields and s_fields["runtime_commit"]:
            claimed_commits[s_name] = str(s_fields["runtime_commit"]).strip()
        if "bound_at_ms" in s_fields and s_fields["bound_at_ms"] is not None:
            claimed_bound_at[s_name] = int(s_fields["bound_at_ms"])

    # 1. Compare session
    if len(set(claimed_sessions.values())) > 1:
        return (
            "CONFLICT",
            {},
            [REPAIR_SESSION_CONFLICT, REPAIR_SOURCE_CONFLICT],
            {"conflicting_sessions": claimed_sessions},
        )

    # 2. Compare revision
    if len(set(claimed_revisions.values())) > 1:
        return (
            "CONFLICT",
            {},
            [REPAIR_CONFIG_REVISION_CONFLICT, REPAIR_SOURCE_CONFLICT],
            {"conflicting_revisions": claimed_revisions},
        )

    # 3. Compare runtime commit
    if len(set(claimed_commits.values())) > 1:
        return (
            "CONFLICT",
            {},
            [REPAIR_RUNTIME_COMMIT_CONFLICT, REPAIR_SOURCE_CONFLICT],
            {"conflicting_commits": claimed_commits},
        )

    # 4. Compare bound_at_ms
    if len(set(claimed_bound_at.values())) > 1:
        return (
            "CONFLICT",
            {},
            [REPAIR_BOUND_AT_CONFLICT, REPAIR_SOURCE_CONFLICT],
            {"conflicting_bound_at": claimed_bound_at},
        )

    # Check completeness
    if not claimed_sessions:
        return "INSUFFICIENT", {}, [REPAIR_PROOF_MISSING], {"missing": "forward_session_id"}
    if not claimed_revisions:
        return "INSUFFICIENT", {}, [REPAIR_CONFIG_REVISION_MISSING], {"missing": "config_revision"}
    if not claimed_commits:
        return "INSUFFICIENT", {}, [REPAIR_RUNTIME_COMMIT_MISSING], {"missing": "runtime_commit"}
    if not claimed_bound_at:
        return "INSUFFICIENT", {}, [REPAIR_BOUND_AT_MISSING], {"missing": "bound_at_ms"}

    resolved = {
        "forward_session_id": next(iter(claimed_sessions.values())),
        "config_revision": next(iter(claimed_revisions.values())),
        "runtime_commit": next(iter(claimed_commits.values())),
        "bound_at_ms": next(iter(claimed_bound_at.values())),
    }
    return "VERIFIED", resolved, [], {}


def collect_repair_provenance_sources(
    db: Session,
    task: ForwardMembershipRepairTask,
    order: models.PaperOrder,
) -> Dict[str, Any]:
    """PHẦN 023, 024, 025: Read-only collector for all candidate provenance sources.
    Never commits, mutates, or calls network.
    Returns dictionary with parsed sources and structured RepairProvenanceSource objects.
    """
    dp_status, dp_data, dp_err = parse_repair_source_payload(task.original_damaged_payload)
    dp_kind = classify_repair_binding_payload_origin(dp_data, default_kind="ORIGINAL_BINDING")
    dp_source = RepairProvenanceSource(
        name="original_damaged_payload",
        kind=dp_kind,
        parse_status=dp_status,
        payload=dp_data,
        reason_codes=[dp_err] if dp_err else [],
        diagnostics={"raw_payload": task.original_damaged_payload, "classified_kind": dp_kind},
    )

    mem_key = f"fwd_mem:{order.id}"[:50]
    rec_mem = db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
    db_mem_status, db_mem_data, db_mem_err = parse_repair_source_payload(rec_mem.value if rec_mem else None)
    db_mem_kind = classify_repair_binding_payload_origin(db_mem_data, default_kind="ORIGINAL_BINDING")
    is_repaired = (db_mem_kind == "REPAIRED_MEMBERSHIP")
    db_mem_source = RepairProvenanceSource(
        name="db_membership",
        kind=db_mem_kind,
        parse_status=db_mem_status,
        payload=db_mem_data,
        reason_codes=[db_mem_err] if db_mem_err else [],
        diagnostics={"is_repaired": is_repaired, "classified_kind": db_mem_kind},
    )

    snap_key = f"fwd_snap:{order.id}"[:50]
    rec_snap = db.query(models.SystemConfig).filter(models.SystemConfig.key == snap_key).first()
    db_snap_status, db_snap_data, db_snap_err = parse_repair_source_payload(rec_snap.value if rec_snap else None)
    db_snap_kind = classify_repair_binding_payload_origin(db_snap_data, default_kind="LEGACY_BINDING")
    db_snap_source = RepairProvenanceSource(
        name="db_snapshot",
        kind=db_snap_kind,
        parse_status=db_snap_status,
        payload=db_snap_data,
        reason_codes=[db_snap_err] if db_snap_err else [],
        diagnostics={"has_production_writer": False, "classified_kind": db_snap_kind},
    )

    order_sess_inst = getattr(order, "session_instance_id", "") or ""

    matching_indexes = []
    sess_configs = db.query(models.SystemConfig).filter(
        models.SystemConfig.key.like("fwd_sess_ords:%")
    ).all()
    for sc in sess_configs:
        try:
            ord_list = json.loads(sc.value)
            if isinstance(ord_list, list) and order.id in ord_list:
                s_id = sc.key.split("fwd_sess_ords:", 1)[-1]
                matching_indexes.append(s_id)
        except Exception:
            pass

    return {
        "damaged_payload_status": dp_status,
        "damaged_payload": dp_data,
        "damaged_payload_err": dp_err,
        "db_mem_status": db_mem_status,
        "db_mem_payload": db_mem_data,
        "db_snap_status": db_snap_status,
        "db_snap_payload": db_snap_data,
        "order_session_instance_id": order_sess_inst,
        "session_indexes": matching_indexes,
        "structured_sources": [dp_source, db_mem_source, db_snap_source],
    }


def resolve_repair_provenance(
    db: Session,
    task: ForwardMembershipRepairTask,
    order: models.PaperOrder,
) -> RepairProvenanceResolution:
    """PHẦN 026, 048: Resolves provenance across all candidate sources.
    Returns structured RepairProvenanceResolution (VERIFIED, INSUFFICIENT, CONFLICT).
    Guarantees:
    - Pure read query: zero mutations.
    - All-source validation: eliminates first-source-wins.
    - Compares all candidate sources and flags conflicts across revisions, commits, bounds, sessions.
    - Identifies index conflicts even when target session is present.
    - Repaired memberships cannot self-certify without independent provenance.
    - Rejects records under order key claiming another order ID.
    - Session existence verified via load_forward_session.
    - Preserves exact historical bound_at_ms, config_revision, runtime_commit.
    """
    sources = collect_repair_provenance_sources(db, task, order)
    dp_source, db_mem_source, db_snap_source = sources["structured_sources"]

    # 0. If task explicitly specifies a forward_session_id, verify target session exists in DB
    if task.forward_session_id:
        target_session_obj = load_forward_session(db, task.forward_session_id)
        if not target_session_obj:
            return RepairProvenanceResolution(
                status="INSUFFICIENT",
                reason_codes=[
                    REPAIR_SESSION_NOT_FOUND,
                    f"NONEXISTENT_FORWARD_SESSION: {task.forward_session_id}",
                ],
                diagnostics={"missing_session_id": task.forward_session_id},
            )

    # 1. Validate each candidate binding source individually
    v_dp = validate_repair_binding_source(dp_source, order)
    v_mem = validate_repair_binding_source(db_mem_source, order)
    v_snap = validate_repair_binding_source(db_snap_source, order)

    # 1.1 Strict Order ID Mismatch Check (PHẦN 026, 027, C18-05):
    # A record stored under order key that claims a different order_id is a corruption/conflict!
    for s_name, v_res in [
        ("original_damaged_payload", v_dp),
        ("db_membership", v_mem),
        ("db_snapshot", v_snap),
    ]:
        if not v_res["order_match"]:
            return RepairProvenanceResolution(
                status="CONFLICT",
                reason_codes=[REPAIR_ORDER_CONFLICT, REPAIR_SOURCE_ORDER_MISMATCH],
                diagnostics={"source": s_name, "details": v_res["diagnostics"]},
            )

    # 2. Check damaged payload integrity vs independent proof availability
    dp_status = dp_source.parse_status
    has_complete_original_payload = is_complete_original_binding_proof(dp_source, v_dp, order)
    has_complete_independent_snapshot = is_complete_original_binding_proof(db_snap_source, v_snap, order)
    has_complete_original_membership = is_complete_original_binding_proof(db_mem_source, v_mem, order)

    independent_complete_sources = []
    if has_complete_original_membership:
        independent_complete_sources.append(("db_membership", v_mem["fields"]))
    if has_complete_independent_snapshot:
        independent_complete_sources.append(("db_snapshot", v_snap["fields"]))

    if dp_status == "MALFORMED":
        if not independent_complete_sources:
            return RepairProvenanceResolution(
                status="INSUFFICIENT",
                reason_codes=[REPAIR_PROOF_MALFORMED],
                diagnostics={"damaged_payload": task.original_damaged_payload},
            )
    elif dp_status == "INVALID_SCHEMA":
        if not independent_complete_sources:
            return RepairProvenanceResolution(
                status="INSUFFICIENT",
                reason_codes=[REPAIR_PROOF_INVALID_SCHEMA],
                diagnostics={"damaged_payload": task.original_damaged_payload},
            )
    elif dp_status == "ABSENT":
        if not independent_complete_sources and not task.forward_session_id:
            return RepairProvenanceResolution(
                status="INSUFFICIENT",
                reason_codes=[REPAIR_PROOF_MISSING],
                diagnostics={"order_id": order.id},
            )

    # 3. Repaired Source Self-Proof Check (PHẦN 014, 016, 033, C18-13, S19-01, C20-01, C20-05):
    # Any source marked REPAIRED_MEMBERSHIP cannot self-certify or become original authority
    # unless there is an independent, complete, valid original binding proof.
    repaired_sources = [s for s in [db_mem_source, dp_source, db_snap_source] if s.kind == "REPAIRED_MEMBERSHIP"]
    if repaired_sources:
        has_independent_original_proof = (
            has_complete_original_payload
            or has_complete_independent_snapshot
            or has_complete_original_membership
        )
        if not has_independent_original_proof:
            repaired_names = [s.name for s in repaired_sources]
            return RepairProvenanceResolution(
                status="INSUFFICIENT",
                reason_codes=[REPAIR_SOURCE_UNVERIFIED],
                diagnostics={
                    "unverified_source": repaired_names[0] if repaired_names else "repaired_source",
                    "unverified_sources": repaired_names,
                    "order_id": order.id,
                    "reason": "Repaired source cannot self-certify without independent original provenance",
                    "damaged_payload_status": dp_status,
                    "damaged_payload_complete": has_complete_original_payload,
                    "snapshot_complete": has_complete_independent_snapshot,
                    "membership_complete": has_complete_original_membership,
                },
            )

    # 4. Gather ALL candidate binding sources for comparison (PHẦN 012: No first-source-wins!)
    cand_sources: List[Tuple[str, Dict[str, Any]]] = []
    if dp_status == "VALID" and v_dp["valid"] and v_dp["fields"]:
        cand_sources.append(("original_damaged_payload", v_dp["fields"]))
    if v_mem["valid"] and v_mem["fields"]:
        cand_sources.append(("db_membership", v_mem["fields"]))
    if v_snap["valid"] and v_snap["fields"]:
        cand_sources.append(("db_snapshot", v_snap["fields"]))

    # Additional contextual session claims (for conflict detection)
    context_session_claims: Dict[str, str] = {}
    if task.forward_session_id:
        context_session_claims["task"] = str(task.forward_session_id).strip()
    order_sess_inst = sources.get("order_session_instance_id", "") or ""
    if order_sess_inst and order_sess_inst.startswith("fwd_sess_"):
        context_session_claims["order_instance"] = str(order_sess_inst).strip()

    if not cand_sources and not context_session_claims:
        return RepairProvenanceResolution(
            status="INSUFFICIENT",
            reason_codes=[REPAIR_PROOF_MISSING],
        )

    # 5. Cross-check session claims across candidate sources AND context claims (PHẦN 070: partial conflict detection)
    all_session_claims: Dict[str, str] = dict(context_session_claims)
    for s_name, s_fields in cand_sources:
        if "forward_session_id" in s_fields and s_fields["forward_session_id"]:
            all_session_claims[s_name] = str(s_fields["forward_session_id"]).strip()

    if len(set(all_session_claims.values())) > 1:
        return RepairProvenanceResolution(
            status="CONFLICT",
            reason_codes=[
                REPAIR_SESSION_CONFLICT,
                f"PROVENANCE_SESSION_MISMATCH: conflicting sessions {all_session_claims}",
                REPAIR_SOURCE_CONFLICT,
            ],
            diagnostics={"claimed_sessions": all_session_claims},
        )

    # 6. Compare candidate binding sources for revision, commit, bound_at, and session
    cmp_status, resolved_fields, cmp_reasons, cmp_diag = compare_repair_binding_sources(cand_sources)
    if cmp_status == "CONFLICT":
        return RepairProvenanceResolution(
            status="CONFLICT",
            reason_codes=cmp_reasons,
            diagnostics=cmp_diag,
        )
    elif cmp_status == "INSUFFICIENT":
        return RepairProvenanceResolution(
            status="INSUFFICIENT",
            reason_codes=cmp_reasons,
            diagnostics=cmp_diag,
        )

    target_session_id = resolved_fields["forward_session_id"]

    # 7. Verify target session exists in DB
    target_session_obj = load_forward_session(db, target_session_id)
    if not target_session_obj:
        return RepairProvenanceResolution(
            status="INSUFFICIENT",
            reason_codes=[REPAIR_SESSION_NOT_FOUND, f"NONEXISTENT_FORWARD_SESSION: {target_session_id}"],
            diagnostics={"missing_session_id": target_session_id},
        )

    # 8. Validate session order indexes (PHẦN 014, 042, 043, 044: Index conflict handling)
    index_sessions = sources.get("session_indexes", []) or []
    idx_status, idx_reasons, idx_diag = validate_repair_session_indexes(index_sessions, target_session_id)
    if idx_status == "CONFLICT":
        return RepairProvenanceResolution(
            status="CONFLICT",
            reason_codes=idx_reasons,
            diagnostics=idx_diag,
        )

    # 9. All sources verified and consistent!
    source_names = [s[0] for s in cand_sources]
    return RepairProvenanceResolution(
        status="VERIFIED",
        forward_session_id=target_session_id,
        config_revision=resolved_fields["config_revision"],
        runtime_commit=resolved_fields["runtime_commit"],
        bound_at_ms=resolved_fields["bound_at_ms"],
        source_names=source_names,
        reason_codes=[],
        diagnostics={"sources": source_names, "indexes": index_sessions},
    )


def stage_repair_manual_review(
    db: Session,
    task: ForwardMembershipRepairTask,
    reason_codes: List[str],
    now_ms: int,
    error_msg: Optional[str] = None,
) -> None:
    """PHẦN 039: Stage-only helper to transition task to MANUAL_REVIEW_REQUIRED.
    Preserves task raw payload and never calls db.commit() or db.rollback().
    """
    task.status = "MANUAL_REVIEW_REQUIRED"
    task.updated_at_ms = now_ms
    for rc in reason_codes:
        if rc and rc not in task.reason_codes:
            task.reason_codes.append(rc)
    if error_msg:
        task.last_error = error_msg
    elif reason_codes:
        task.last_error = reason_codes[0]
    task_key = _repair_task_key(task.order_id)
    trec = db.query(models.SystemConfig).filter(models.SystemConfig.key == task_key).first()
    if not trec:
        trec = models.SystemConfig(key=task_key, value=json.dumps(task.to_dict()), updated_at=now_ms)
        db.add(trec)
    else:
        trec.value = json.dumps(task.to_dict())
        trec.updated_at = now_ms


def validate_repair_session(
    db: Session,
    task: ForwardMembershipRepairTask,
    order: models.PaperOrder,
) -> Tuple[bool, Optional[str]]:
    """PHẦN 038: Compatibility helper for callers of validate_repair_session.
    Delegates directly to resolve_repair_provenance to eliminate divergent validation logic.
    """
    resolution = resolve_repair_provenance(db, task, order)
    if resolution.status == "VERIFIED":
        return True, None
    reason = resolution.reason_codes[0] if resolution.reason_codes else "PROVENANCE_NOT_VERIFIED"
    return False, reason


def repair_forward_membership_task(
    db: Session,
    task: ForwardMembershipRepairTask,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """PHẦN 044, 045: Executes repair for a single ForwardMembershipRepairTask.
    Reconstructs membership and index from verified immutable sources.
    Atomic repair owner commit. Zero financial side-effects.
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)

    if task.status == "REPAIRED":
        return {"success": True, "status": "ALREADY_REPAIRED", "task": task}

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == task.order_id).first()
    if not order:
        stage_repair_manual_review(db, task, [REPAIR_PROOF_MISSING], now_ms, f"Order {task.order_id} not found in DB")
        try:
            db.commit()
        except Exception:
            db.rollback()
            return {"success": False, "status": "COMMIT_FAILED", "error": "db_commit_failed"}
        return {"success": False, "status": "MANUAL_REVIEW_REQUIRED", "reason": f"Order {task.order_id} not found"}

    resolution = resolve_repair_provenance(db, task, order)
    if resolution.status != "VERIFIED":
        err_reason = resolution.reason_codes[0] if resolution.reason_codes else "PROVENANCE_NOT_VERIFIED"
        if len(resolution.reason_codes) > 1:
            err_reason = f"{resolution.reason_codes[0]}: {resolution.reason_codes[1]}"
        stage_repair_manual_review(db, task, resolution.reason_codes, now_ms, err_reason)
        try:
            db.commit()
        except Exception:
            db.rollback()
            return {"success": False, "status": "COMMIT_FAILED", "error": "db_commit_failed"}
        return {
            "success": False,
            "status": "MANUAL_REVIEW_REQUIRED",
            "reason": err_reason,
            "resolution": resolution,
        }

    # Monotonic resolution: check actual order state in DB
    order_state = getattr(order, "state", "") or ""
    effective_status = task.target_status
    if order_state == "closed":
        effective_status = "CLOSED"
    elif order_state == "paper_open" and task.target_status in ("ARMED", "BOUND"):
        effective_status = "FILLED"

    repaired_mem = ForwardOrderMembership(
        forward_session_id=resolution.forward_session_id,
        order_id=order.id,
        bound_at_ms=resolution.bound_at_ms,
        config_revision=resolution.config_revision,
        runtime_commit=resolution.runtime_commit,
        membership_status=effective_status,
        metadata={
            "repaired_by": "ForwardMembershipRepairTask",
            "task_id": task.task_id,
            "repaired_at_ms": now_ms,
            "provenance_sources": resolution.source_names,
            "provenance_validation_version": "1.0.0",
        },
    )

    try:
        # Stage repaired membership
        mem_key = f"fwd_mem:{order.id}"[:50]
        val_json = json.dumps(repaired_mem.to_dict(), allow_nan=False)
        mrec = db.query(models.SystemConfig).filter(models.SystemConfig.key == mem_key).first()
        if not mrec:
            mrec = models.SystemConfig(key=mem_key, value=val_json, updated_at=now_ms)
            db.add(mrec)
        else:
            mrec.value = val_json
            mrec.updated_at = now_ms

        # Stage session order index
        _append_session_order_id(db, resolution.forward_session_id, order.id, now_ms)

        # Stage task as REPAIRED
        task.status = "REPAIRED"
        task.repaired_at_ms = now_ms
        task.updated_at_ms = now_ms
        task_key = _repair_task_key(task.order_id)
        trec = db.query(models.SystemConfig).filter(models.SystemConfig.key == task_key).first()
        if trec:
            trec.value = json.dumps(task.to_dict())
            trec.updated_at = now_ms

        db.commit()
        return {"success": True, "status": "REPAIRED", "task": task, "membership": repaired_mem}
    except Exception as exc:
        db.rollback()
        task.attempts += 1
        task.last_error = str(exc)
        task.updated_at_ms = now_ms
        if task.attempts >= task.max_attempts:
            task.status = "MANUAL_REVIEW_REQUIRED"
        else:
            task.status = "RETRY_SCHEDULED"
            delay_ms = min(60000, (2 ** task.attempts) * 1000)
            task.next_retry_at_ms = now_ms + delay_ms

        # Persist task retry state
        try:
            task_key = _repair_task_key(task.order_id)
            trec = db.query(models.SystemConfig).filter(models.SystemConfig.key == task_key).first()
            if trec:
                trec.value = json.dumps(task.to_dict())
                trec.updated_at = now_ms
                db.commit()
        except Exception:
            db.rollback()

        return {"success": False, "status": task.status, "error": str(exc)}


def process_due_forward_repairs(
    db: Optional[Session] = None,
    now_ms: Optional[int] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """PHẦN 041, 042: Worker entrypoint to process due forward membership repairs.
    Can use own SessionLocal if db is None.
    """
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)

    owns_session = False
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
        owns_session = True

    processed = 0
    repaired = 0
    failed = 0
    manual_review = 0

    try:
        due_tasks = load_due_forward_repairs(db, now_ms=now_ms, limit=limit)
        for t in due_tasks:
            processed += 1
            res = repair_forward_membership_task(db, t, now_ms=now_ms)
            if res.get("success"):
                repaired += 1
            elif res.get("status") == "MANUAL_REVIEW_REQUIRED":
                manual_review += 1
            else:
                failed += 1
    finally:
        if owns_session:
            db.close()

    return {
        "processed": processed,
        "repaired": repaired,
        "failed": failed,
        "manual_review": manual_review,
    }


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


# ---------------------------------------------------------------------------
# V14.4.15: Terminal Event Decoding and Binding Functions
# ---------------------------------------------------------------------------

def decode_terminal_event(event_obj: Any) -> TerminalEventDecodeResult:
    """PHẦN 016, 017: Pure immutable decoding of a domain event into a TerminalEventView.
    Returns TerminalEventDecodeResult with status in ("VALID", "INVALID", "UNSUPPORTED", "MISSING").
    """
    if event_obj is None:
        return TerminalEventDecodeResult(status="MISSING", reason_code="MISSING_TERMINAL_EVENT")

    if isinstance(event_obj, TerminalEventView):
        return TerminalEventDecodeResult(status="VALID", event_view=event_obj)

    if isinstance(event_obj, dict) and event_obj.get("status") == "MISSING":
        return TerminalEventDecodeResult(status="MISSING", reason_code="MISSING_TERMINAL_EVENT")

    event_id = None
    event_type = None
    aggregate_id = None
    aggregate_version = 1
    occurred_at = 0
    published_at = 0
    payload_data = None

    if isinstance(event_obj, models.DomainEvent):
        event_id = getattr(event_obj, "event_id", "") or ""
        event_type = getattr(event_obj, "event_type", "") or ""
        aggregate_id = getattr(event_obj, "aggregate_id", "") or ""
        aggregate_version = getattr(event_obj, "aggregate_version", 1) or 1
        occurred_at = getattr(event_obj, "occurred_at", 0) or 0
        published_at = getattr(event_obj, "published_at", 0) or 0
        raw_p = getattr(event_obj, "payload", None)
        if isinstance(raw_p, str):
            try:
                payload_data = json.loads(raw_p)
            except Exception:
                return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_PAYLOAD_INVALID_JSON")
        elif isinstance(raw_p, dict):
            payload_data = raw_p
        else:
            return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_PAYLOAD_MISSING")

    elif isinstance(event_obj, dict):
        event_id = event_obj.get("event_id") or event_obj.get("id") or "dict_event"
        event_type = event_obj.get("event_type") or event_obj.get("type") or "trade.closed"
        aggregate_id = event_obj.get("aggregate_id") or event_obj.get("order_id") or ""
        aggregate_version = event_obj.get("aggregate_version", 1) or 1
        occurred_at = event_obj.get("occurred_at", 0) or 0
        published_at = event_obj.get("published_at", 0) or 0
        raw_p = event_obj.get("payload")
        if raw_p is not None:
            if isinstance(raw_p, str):
                try:
                    payload_data = json.loads(raw_p)
                except Exception:
                    return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_PAYLOAD_INVALID_JSON")
            elif isinstance(raw_p, dict):
                payload_data = raw_p
        else:
            payload_data = event_obj
    else:
        return TerminalEventDecodeResult(status="INVALID", reason_code="UNKNOWN_EVENT_OBJECT_TYPE")

    if event_type not in ("trade.closed", "trade.liquidated"):
        return TerminalEventDecodeResult(
            status="UNSUPPORTED",
            reason_code=f"UNSUPPORTED_TERMINAL_EVENT_{event_type}",
        )

    if not isinstance(payload_data, dict):
        return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_PAYLOAD_INVALID")

    order_id = payload_data.get("order_id") or payload_data.get("trade_id") or aggregate_id
    if not order_id or not isinstance(order_id, str):
        return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_MISSING_ORDER_ID")

    exit_cause = payload_data.get("exit_cause")
    if not exit_cause or not isinstance(exit_cause, str):
        return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_MISSING_EXIT_CAUSE")

    direction = payload_data.get("direction")
    if not direction or direction not in ("LONG", "SHORT"):
        return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_MISSING_DIRECTION")

    actual_exit_raw = payload_data.get("actual_exit")
    if actual_exit_raw is None or isinstance(actual_exit_raw, bool):
        return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_PAYLOAD_INVALID")
    try:
        actual_exit = float(actual_exit_raw)
        if not math.isfinite(actual_exit):
            return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_PAYLOAD_INVALID")
    except (ValueError, TypeError):
        return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_PAYLOAD_INVALID")

    realized_pnl_raw = payload_data.get("realized_pnl")
    if realized_pnl_raw is None or isinstance(realized_pnl_raw, bool):
        return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_PAYLOAD_INVALID")
    try:
        realized_pnl = float(realized_pnl_raw)
        if not math.isfinite(realized_pnl):
            return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_PAYLOAD_INVALID")
    except (ValueError, TypeError):
        return TerminalEventDecodeResult(status="INVALID", reason_code="TERMINAL_EVENT_PAYLOAD_INVALID")

    actual_entry_raw = payload_data.get("actual_entry")
    actual_entry = None
    if actual_entry_raw is not None and not isinstance(actual_entry_raw, bool):
        try:
            val = float(actual_entry_raw)
            if math.isfinite(val):
                actual_entry = val
        except (ValueError, TypeError):
            pass

    realized_r_raw = payload_data.get("realized_r")
    realized_r = None
    if realized_r_raw is not None and not isinstance(realized_r_raw, bool):
        try:
            val = float(realized_r_raw)
            if math.isfinite(val):
                realized_r = val
        except (ValueError, TypeError):
            pass

    view = TerminalEventView(
        event_id=event_id or "unknown_event_id",
        event_type=event_type,
        order_id=order_id,
        aggregate_version=aggregate_version,
        occurred_at=occurred_at,
        published_at=published_at,
        exit_cause=exit_cause,
        actual_exit=actual_exit,
        realized_pnl=realized_pnl,
        direction=direction,
        actual_entry=actual_entry,
        realized_r=realized_r,
        recovery_status=payload_data.get("recovery_status"),
        strategy_family=payload_data.get("strategy_family"),
        raw_payload=payload_data,
    )
    return TerminalEventDecodeResult(status="VALID", event_view=view)


def validate_terminal_binding(
    order: models.PaperOrder,
    event_view: TerminalEventView,
    as_of_ms: Optional[int] = None,
) -> Tuple[bool, List[str]]:
    """PHẦN 018, 019: Validates binding consistency between PaperOrder and decoded TerminalEventView.
    Checks: order_id, direction, exit_cause, actual_exit price, realized_pnl, and as_of boundary.
    """
    reasons: List[str] = []

    # 1. Order ID match
    if event_view.order_id != getattr(order, "id", None):
        return False, ["TERMINAL_EVENT_ORDER_MISMATCH"]

    # 2. Direction match
    if event_view.direction != getattr(order, "direction", None):
        reasons.append("TERMINAL_EVENT_DIRECTION_MISMATCH")

    # 3. Exit cause match
    if event_view.exit_cause != getattr(order, "exit_cause", None):
        reasons.append("TERMINAL_EVENT_EXIT_CAUSE_MISMATCH")

    # 4. Numeric tolerance: actual_exit (tolerance 0.05)
    order_exit = getattr(order, "actual_exit", None)
    if order_exit is not None:
        if abs(event_view.actual_exit - float(order_exit)) > 0.05:
            reasons.append("TERMINAL_EVENT_NUMERIC_MISMATCH")

    # 5. Numeric tolerance: realized_pnl (tolerance 0.05)
    order_pnl = getattr(order, "realized_pnl_net", None)
    if order_pnl is not None:
        if abs(event_view.realized_pnl - float(order_pnl)) > 0.05:
            if "TERMINAL_EVENT_NUMERIC_MISMATCH" not in reasons:
                reasons.append("TERMINAL_EVENT_NUMERIC_MISMATCH")

    # 6. As-of boundary (PHẦN 019)
    if as_of_ms is not None:
        if event_view.occurred_at > as_of_ms or event_view.published_at > as_of_ms:
            reasons.append("TERMINAL_EVENT_POST_AS_OF")

    if reasons:
        return False, reasons
    return True, []


def load_terminal_events_for_orders(
    db: Session,
    order_ids: List[str],
    as_of_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """PHẦN 021, 022: Preloads and decodes terminal events for a list of order IDs.
    Returns map of order_id -> TerminalEventView (or conflict dict if contradictory events exist).
    """
    if not order_ids:
        return {}

    query = db.query(models.DomainEvent).filter(
        models.DomainEvent.aggregate_id.in_(order_ids),
        models.DomainEvent.event_type.in_(["trade.closed", "trade.liquidated"]),
    )
    if as_of_ms is not None:
        query = query.filter(models.DomainEvent.occurred_at <= as_of_ms)

    events = query.order_by(models.DomainEvent.sequence.asc()).all()

    grouped: Dict[str, List[models.DomainEvent]] = {}
    for ev in events:
        grouped.setdefault(ev.aggregate_id, []).append(ev)

    results: Dict[str, Any] = {}
    for ord_id, ev_list in grouped.items():
        if len(ev_list) == 1:
            dec = decode_terminal_event(ev_list[0])
            if dec.status == "VALID" and dec.event_view:
                results[ord_id] = dec.event_view
            else:
                results[ord_id] = ev_list[0]
        else:
            decoded_views: List[TerminalEventView] = []
            for ev in ev_list:
                dec = decode_terminal_event(ev)
                if dec.status == "VALID" and dec.event_view:
                    decoded_views.append(dec.event_view)

            if not decoded_views:
                results[ord_id] = ev_list[-1]
                continue

            first = decoded_views[0]
            is_conflicted = False
            for other in decoded_views[1:]:
                if (
                    other.exit_cause != first.exit_cause
                    or abs(other.actual_exit - first.actual_exit) > 0.05
                    or abs(other.realized_pnl - first.realized_pnl) > 0.05
                ):
                    is_conflicted = True
                    break

            if is_conflicted:
                results[ord_id] = {
                    "status": "CONFLICTED",
                    "reason": "CONTRADICTORY_TERMINAL_EVENTS",
                    "events": ev_list,
                }
            else:
                results[ord_id] = first

    return results


def read_outcome_authority(
    order: models.PaperOrder,
    terminal_event: Optional[Any] = None,
    review_record: Optional[Dict[str, Any]] = None,
    as_of_ms: Optional[int] = None,
    require_terminal_event: bool = False,
) -> RecoveryOutcomeAuthority:
    """PHẦN 004, 012, 013, 014, 015, 018, 027:
    Pure extraction of outcome authority from real ORM fields and provenance.
    Categories:
    - VERIFIED_PAPER: Valid normal paper close or verified recovery (CONFIRMED / CONFIRMED_TICK).
    - CONSERVATIVE_SIMULATION: Conservative simulation (SIMULATED_AMBIGUOUS, ASSUMED_CONSERVATIVE, AMBIGUOUS_BAR_SL_FIRST).
    - UNRESOLVED: Recovery status UNRESOLVED / UNRESOLVED_GAP / RECOVERING, or review pending.
    - CONFLICTED: Terminal conflict review requiring manual review or contradictory terminal events.
    - UNVERIFIED: Missing exit metadata, invalid exit cause, unsupported recovery status, or unverified recovery.
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

    # PHẦN 012, 013: Validate recovery status before checking confidence
    if rec_status not in KNOWN_ALL_RECOVERY_STATUSES:
        return RecoveryOutcomeAuthority(
            status="UNVERIFIED",
            confidence=rec_conf,
            recovery_status=rec_status,
            exit_mode="UNKNOWN",
            countable_for_confirmed=False,
            reason_codes=[f"UNSUPPORTED_RECOVERY_STATUS_{rec_status}"],
        )

    # 4. Terminal Contradictions
    if isinstance(terminal_event, dict) and terminal_event.get("status") == "CONFLICTED":
        return RecoveryOutcomeAuthority(
            status="CONFLICTED",
            confidence=rec_conf,
            recovery_status=rec_status,
            exit_mode="CONFLICT",
            countable_for_confirmed=False,
            reason_codes=["CONTRADICTORY_TERMINAL_EVENTS"],
        )

    # 5. Authority Classification Matrix for non-confirmed categories (PHẦN 014, 021)
    # If recovery_confidence is set to UNRESOLVED or recovery_status is unresolved:
    if rec_conf == "UNRESOLVED" or rec_status in KNOWN_UNRESOLVED_STATUSES:
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

    # 6. Terminal Event Validation for Confirmed Trades (PHẦN 015, 018, 027)
    bound_event_id: Optional[str] = None
    if terminal_event is not None and not (isinstance(terminal_event, dict) and terminal_event.get("status") == "MISSING"):
        dec = decode_terminal_event(terminal_event)
        if dec.status != "VALID" or not dec.event_view:
            return RecoveryOutcomeAuthority(
                status="UNVERIFIED",
                confidence=rec_conf,
                recovery_status=rec_status,
                exit_mode="INVALID_EVENT",
                countable_for_confirmed=False,
                reason_codes=[dec.reason_code or "INVALID_TERMINAL_EVENT"],
            )
        is_valid, bind_reasons = validate_terminal_binding(order, dec.event_view, as_of_ms=as_of_ms)
        if not is_valid:
            return RecoveryOutcomeAuthority(
                status="UNVERIFIED",
                confidence=rec_conf,
                recovery_status=rec_status,
                exit_mode="BINDING_FAILED",
                countable_for_confirmed=False,
                reason_codes=bind_reasons,
            )
        bound_event_id = dec.event_view.event_id
    elif require_terminal_event or (isinstance(terminal_event, dict) and terminal_event.get("status") == "MISSING"):
        return RecoveryOutcomeAuthority(
            status="UNVERIFIED",
            confidence=rec_conf,
            recovery_status=rec_status,
            exit_mode="UNVERIFIED",
            countable_for_confirmed=False,
            reason_codes=["MISSING_TERMINAL_EVENT", "LEGACY_UNAVAILABLE"],
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
            terminal_event_id=bound_event_id,
        )

    # PHẦN 013: RECOVERED status with confidence=None requires explicit verification
    if rec_status in KNOWN_RECOVERED_STATUSES and rec_conf is None:
        return RecoveryOutcomeAuthority(
            status="UNVERIFIED",
            confidence=None,
            recovery_status=rec_status,
            exit_mode="RECOVERY",
            countable_for_confirmed=False,
            reason_codes=["MISSING_RECOVERY_CONFIDENCE_FOR_RECOVERED"],
            terminal_event_id=bound_event_id,
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
            terminal_event_id=bound_event_id,
        )

    # If an unknown/unsupported recovery_confidence string was recorded:
    return RecoveryOutcomeAuthority(
        status="UNVERIFIED",
        confidence=rec_conf,
        recovery_status=rec_status,
        exit_mode="UNKNOWN",
        countable_for_confirmed=False,
        reason_codes=[f"UNKNOWN_RECOVERY_CONFIDENCE_{rec_conf}"],
        terminal_event_id=bound_event_id,
    )


def classify_forward_outcome(
    order: models.PaperOrder,
    review_record: Optional[Dict[str, Any]] = None,
    as_of_ms: Optional[int] = None,
    terminal_event: Optional[Any] = None,
    require_terminal_event: bool = False,
) -> Dict[str, Any]:
    """PHẦN 034, 044, 028: Pure classification of an order's outcome using authoritative outcome extraction.
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

        auth = read_outcome_authority(
            order,
            terminal_event=terminal_event,
            review_record=review_record,
            as_of_ms=as_of_ms,
            require_terminal_event=require_terminal_event,
        )
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


def strict_classify_forward_outcome(
    order: models.PaperOrder,
    terminal_event: Optional[Any] = None,
    review_record: Optional[Dict[str, Any]] = None,
    as_of_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """PHẦN 016: Strict outcome classification requiring terminal event matching.
    Missing event maps explicitly to MISSING; calls classify_forward_outcome with require_terminal_event=True.
    """
    event_arg = terminal_event if terminal_event is not None else {"status": "MISSING"}
    return classify_forward_outcome(
        order=order,
        review_record=review_record,
        as_of_ms=as_of_ms,
        terminal_event=event_arg,
        require_terminal_event=True,
    )


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
    session_order_ids_list = [o.id for o in orders]
    terminal_events_map = load_terminal_events_for_orders(db, session_order_ids_list, as_of_ms=now_ms)

    # Count pending repair tasks for this forward session
    tracking_repair_pending_count = 0
    q_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == "fwd_repair_queue").first()
    if q_rec and q_rec.value:
        try:
            q_order_ids = json.loads(q_rec.value)
            if isinstance(q_order_ids, list):
                for q_oid in q_order_ids:
                    t_key = _repair_task_key(q_oid)
                    t_rec = db.query(models.SystemConfig).filter(models.SystemConfig.key == t_key).first()
                    if t_rec and t_rec.value:
                        t_data = json.loads(t_rec.value)
                        if t_data.get("forward_session_id") == session_id and t_data.get("status") in ("PENDING", "RETRY_SCHEDULED", "PROCESSING"):
                            tracking_repair_pending_count += 1
        except Exception:
            pass

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
        ev = terminal_events_map.get(o.id)
        classification = strict_classify_forward_outcome(o, terminal_event=ev, review_record=rev_rec, as_of_ms=now_ms)
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
    recorded_closed_pnl = sum((o.realized_pnl_net or 0.0) for o in orders if getattr(o, "state", "") == "closed")
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
        "recorded_closed_pnl": round(recorded_closed_pnl, 2),
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
        "tracking_repair_pending_count": tracking_repair_pending_count,
        "unverified_closed_trades": len(unverified_orders),
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

    # Preload review records and terminal events
    session_order_ids = {o.id for o in orders}
    session_order_ids_list = [o.id for o in orders]
    terminal_events_map = load_terminal_events_for_orders(db, session_order_ids_list)
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
        ev = terminal_events_map.get(o.id)
        cls_info = strict_classify_forward_outcome(o, terminal_event=ev, review_record=rev_rec)

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
