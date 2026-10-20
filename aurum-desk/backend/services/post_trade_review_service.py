"""
backend/services/post_trade_review_service.py
V16.37 - Evidence-based Post-Trade Review & Lesson Hypotheses Engine.
PHẦN 121 - 140.

Provides:
- build_post_trade_review: Analyzes frozen entry evidence, execution metrics, and exit outcome.
  Classifies losses as STRATEGY_VALID_LOSS, EXECUTION_SLIPPAGE, DATA_ISSUE, or UNVERIFIABLE.
- summarize_lesson_evidence: Groups observations by model, regime, and session to form reviewable hypotheses.
- Strict safety rules:
  * Lessons always start in 'PROPOSED' / 'INACTIVE' status.
  * Lessons never automatically mutate SL/TP or active risk parameters.
  * No superstitious explanations ("cá mập quét SL", "bot sợ hãi"); only empirical facts.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class LossClassification(str, Enum):
    STRATEGY_VALID_LOSS = "STRATEGY_VALID_LOSS"
    EXECUTION_SLIPPAGE = "EXECUTION_SLIPPAGE"
    DATA_ISSUE = "DATA_ISSUE"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    UNVERIFIABLE = "UNVERIFIABLE"
    NOT_A_LOSS = "NOT_A_LOSS"


class LessonStatus(str, Enum):
    PROPOSED = "PROPOSED"
    INACTIVE = "INACTIVE"
    APPROVED = "APPROVED"
    DISABLED = "DISABLED"


class LessonSeverity(str, Enum):
    GREEN = "GREEN"      # Supporting evidence / reference
    YELLOW = "YELLOW"    # Warning to be investigated
    RED = "RED"          # Approved hard constraint (requires explicit approval)


@dataclass
class PostTradeReview:
    """Detailed post-trade review audit artifact (PHẦN 121 - 127)."""
    order_id: str
    symbol: str
    model_id: str
    direction: str
    planned_entry: float
    actual_entry: float
    planned_sl: float
    planned_tp: float
    exit_price: float
    exit_cause: str                          # "TP_HIT", "SL_HIT", "TIMEOUT", "MANUAL_CANCEL"
    gross_pnl: float
    net_pnl: float
    realized_rr: float
    hold_time_ms: Optional[int]
    entry_slippage_usd: float
    loss_classification: str
    outcome_status: str = "WIN"              # "WIN", "BREAKEVEN", "LOSS" (Rule 126)
    signed_slippage_usd: float = 0.0
    adverse_slippage_usd: float = 0.0
    gross_price_rr: float = 0.0
    evidence_ids: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)
    proposed_lessons: List[Dict[str, Any]] = field(default_factory=list)
    as_of_ms: int = 0
    version: str = "v16.37"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": str(self.order_id),
            "symbol": self.symbol,
            "model_id": self.model_id,
            "direction": self.direction,
            "planned_entry": self.planned_entry,
            "actual_entry": self.actual_entry,
            "planned_sl": self.planned_sl,
            "planned_tp": self.planned_tp,
            "exit_price": self.exit_price,
            "exit_cause": self.exit_cause,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "realized_rr": self.realized_rr,
            "outcome_status": self.outcome_status,
            "gross_price_rr": self.gross_price_rr,
            "hold_time_ms": self.hold_time_ms,
            "entry_slippage_usd": self.entry_slippage_usd,
            "signed_slippage_usd": self.signed_slippage_usd,
            "adverse_slippage_usd": self.adverse_slippage_usd,
            "loss_classification": self.loss_classification,
            "evidence_ids": list(self.evidence_ids),
            "observations": list(self.observations),
            "proposed_lessons": list(self.proposed_lessons),
            "as_of_ms": self.as_of_ms,
            "version": self.version,
        }


def build_post_trade_review(
    order: Any,
    outcome: Optional[Dict[str, Any]] = None,
    entry_evidence: Optional[Dict[str, Any]] = None,
    now_ms: int = 0,
) -> PostTradeReview:
    """
    PHẦN 124 - 128, V16.38 Rules 121 - 127:
    Builds empirical post-trade review from immutable order, outcome, and entry snapshot.
    Does NOT invent causes; distinguishes valid strategy loss from slippage or data anomalies.
    """
    outcome = outcome or {}
    ev = entry_evidence or {}
    order_id = str(getattr(order, "id", "") or outcome.get("order_id", ""))
    symbol = getattr(order, "instrument", getattr(order, "symbol", outcome.get("symbol", "XAUUSDT")))
    model_id = getattr(order, "strategy_family", getattr(order, "model_id", outcome.get("strategy_family", "UNKNOWN")))
    direction = getattr(order, "direction", outcome.get("direction", "LONG"))

    planned_entry = float(getattr(order, "planned_entry", getattr(order, "entry_price", 0.0)) or 0.0)
    actual_entry = float(getattr(order, "actual_entry", getattr(order, "fill_price", planned_entry)) or planned_entry)
    planned_sl = float(getattr(order, "stop_loss", 0.0) or 0.0)
    planned_tp = float(getattr(order, "take_profit", 0.0) or 0.0)

    exit_price = float(outcome.get("exit_price", getattr(order, "actual_exit", None)) or getattr(order, "actual_exit", None) or actual_entry)
    exit_cause = str(outcome.get("exit_cause", getattr(order, "exit_cause", "UNKNOWN")) or getattr(order, "exit_cause", "UNKNOWN"))
    gross_pnl = round(float(outcome.get("gross_pnl", 0.0)), 2)
    net_pnl = round(float(outcome.get("net_pnl", getattr(order, "realized_pnl_net", gross_pnl)) or 0.0), 2)

    # Accounting Realized R (Rule 124)
    realized_rr_val = outcome.get("realized_r", getattr(order, "realized_r", None))
    if realized_rr_val is not None:
        realized_rr = round(float(realized_rr_val), 2)
    else:
        init_risk = float(getattr(order, "initial_risk_usdt", 0.0) or 0.0)
        realized_rr = round(net_pnl / init_risk, 2) if init_risk > 0 else 0.0

    # Gross Price R based on entry/exit geometry
    risk_budget = abs(actual_entry - planned_sl)
    if risk_budget > 0:
        if direction in ("LONG", "BUY"):
            gross_price_rr = round((exit_price - actual_entry) / risk_budget, 2)
        else:
            gross_price_rr = round((actual_entry - exit_price) / risk_budget, 2)
    else:
        gross_price_rr = 0.0

    # Slippage (Rule 127)
    if direction in ("LONG", "BUY"):
        signed_slippage_usd = round(actual_entry - planned_entry, 2)
    else:
        signed_slippage_usd = round(planned_entry - actual_entry, 2)
    adverse_slippage_usd = round(max(0.0, signed_slippage_usd), 2)
    entry_slippage_usd = round(abs(actual_entry - planned_entry), 2)

    # Hold time (Rule 123, Section 083: Inverted timestamp is DATA_INVALID, never clamp to 0)
    opened_at = getattr(order, "opened_at", getattr(order, "filled_at", None))
    closed_at = getattr(order, "closed_at", outcome.get("closed_at_ms", outcome.get("closed_at")))
    if opened_at is not None and closed_at is not None:
        try:
            o_int = int(opened_at)
            c_int = int(closed_at)
            if c_int < o_int:
                hold_time_ms = None  # Inverted timestamps: invalid, never max(0, ...)
            else:
                hold_time_ms = c_int - o_int
        except (ValueError, TypeError):
            hold_time_ms = None
    else:
        hold_time_ms = None

    observations: List[str] = []
    proposed_lessons: List[Dict[str, Any]] = []

    # Check unconfirmed outcome (Section 076)
    order_state = getattr(order, "state", None)
    has_closed_marker = (
        order_state in ("closed", "liquidated")
        or bool(outcome.get("closed_at_ms"))
        or bool(outcome.get("closed_at"))
        or bool(getattr(order, "closed_at", None))
        or bool(outcome.get("exit_cause"))
    )
    exit_price_val = outcome.get("exit_price", getattr(order, "actual_exit", None))
    has_exit_price = exit_price_val is not None and float(exit_price_val) > 0.0
    has_accounting = (
        outcome.get("net_pnl") is not None
        or outcome.get("realized_pnl") is not None
        or getattr(order, "realized_pnl_net", None) is not None
    )

    is_confirmed_close = has_closed_marker and has_exit_price and has_accounting
    if not is_confirmed_close:
        outcome_status = "OUTCOME_UNCONFIRMED"
        loss_cls = LossClassification.UNVERIFIABLE.value
        observations.append("Kết quả lệnh chưa được xác nhận; vị thế chưa đóng hoặc chưa hoàn tất ghi sổ.")
    else:
        EPSILON = 0.001
        # Classify outcome (Rule 125, 126)
        if net_pnl > EPSILON:
            outcome_status = "WIN"
            loss_cls = LossClassification.NOT_A_LOSS.value
            observations.append(f"Giao dịch có lãi ({net_pnl:+.2f} USDT, {realized_rr:+.2f}R). Kế hoạch đạt mục tiêu.")
        elif abs(net_pnl) <= EPSILON:
            outcome_status = "BREAKEVEN"
            loss_cls = LossClassification.NOT_A_LOSS.value
            observations.append(f"Giao dịch hòa vốn ({net_pnl:+.2f} USDT).")
        else:
            outcome_status = "LOSS"
            # Evaluate loss cause
            if adverse_slippage_usd > 1.0:
                loss_cls = LossClassification.EXECUTION_SLIPPAGE.value
                observations.append(f"Trượt giá entry bất lợi ({adverse_slippage_usd:.2f} USD).")
                proposed_lessons.append({
                    "lesson_id": f"lesson_slip_{order_id}",
                    "observation": f"Slippage bất lợi {adverse_slippage_usd:.2f} USD tại entry lệnh #{order_id}",
                    "severity": LessonSeverity.YELLOW.value,
                    "status": LessonStatus.PROPOSED.value,
                    "proposed_action": "WARN_HIGH_SLIPPAGE",
                    "affected_model": model_id,
                })
            elif exit_cause in ("SL_HIT", "STOP_LOSS"):
                loss_cls = LossClassification.STRATEGY_VALID_LOSS.value
                observations.append("Giá chạm ngưỡng cắt lỗ theo kế hoạch cấu trúc (SL hợp lệ của chiến lược).")
                proposed_lessons.append({
                    "lesson_id": f"lesson_sl_{order_id}",
                    "observation": f"Lệnh #{order_id} chạm SL theo cấu trúc dự kiến.",
                    "severity": LessonSeverity.GREEN.value,
                    "status": LessonStatus.PROPOSED.value,
                    "proposed_action": "LOG_STRATEGY_SAMPLE",
                    "affected_model": model_id,
                })
            elif "DATA" in exit_cause or "TIMEOUT" in exit_cause:
                loss_cls = LossClassification.DATA_ISSUE.value
                observations.append(f"Đóng lệnh do nguyên nhân dữ liệu/thời gian: {exit_cause}.")
            else:
                loss_cls = LossClassification.UNVERIFIABLE.value
                observations.append(f"Kết quả lỗ chưa xác định nguyên nhân chi tiết ({exit_cause}).")

    ev_ids = [str(x) for x in ev.get("evidence_ids", [])]

    return PostTradeReview(
        order_id=order_id,
        symbol=symbol,
        model_id=model_id,
        direction=direction,
        planned_entry=planned_entry,
        actual_entry=actual_entry,
        planned_sl=planned_sl,
        planned_tp=planned_tp,
        exit_price=exit_price,
        exit_cause=exit_cause,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        realized_rr=realized_rr,
        outcome_status=outcome_status,
        gross_price_rr=gross_price_rr,
        hold_time_ms=hold_time_ms,
        entry_slippage_usd=entry_slippage_usd,
        signed_slippage_usd=signed_slippage_usd,
        adverse_slippage_usd=adverse_slippage_usd,
        loss_classification=loss_cls,
        evidence_ids=ev_ids,
        observations=observations,
        proposed_lessons=proposed_lessons,
        as_of_ms=now_ms,
    )


def summarize_lesson_evidence(
    reviews: Sequence[PostTradeReview],
) -> Dict[str, Any]:
    """
    PHẦN 129, V16.39 Section 084:
    Groups reviews and counts statistics by model and outcome.
    Explicitly distinguishes WIN, BREAKEVEN, LOSS, and UNCONFIRMED.
    Never merges disparate models into a single generic rule.
    """
    by_model: Dict[str, Dict[str, Any]] = {}

    for rev in reviews:
        m = rev.model_id
        if m not in by_model:
            by_model[m] = {
                "total_trades": 0,
                "wins": 0,
                "breakevens": 0,
                "losses": 0,
                "unconfirmed": 0,
                "valid_losses": 0,
                "slippage_losses": 0,
                "data_losses": 0,
                "total_net_pnl": 0.0,
                "realized_r_sum": 0.0,
                "proposed_lessons_count": 0,
            }
        stats = by_model[m]
        stats["total_trades"] += 1
        stats["total_net_pnl"] = round(stats["total_net_pnl"] + rev.net_pnl, 2)
        stats["realized_r_sum"] = round(stats["realized_r_sum"] + rev.realized_rr, 2)
        stats["proposed_lessons_count"] += len(rev.proposed_lessons)

        if rev.outcome_status == "OUTCOME_UNCONFIRMED":
            stats["unconfirmed"] += 1
        elif rev.outcome_status == "WIN" or rev.net_pnl > 0.001:
            stats["wins"] += 1
        elif rev.outcome_status == "BREAKEVEN" or abs(rev.net_pnl) <= 0.001:
            stats["breakevens"] += 1
        else:
            stats["losses"] += 1
            if rev.loss_classification == LossClassification.STRATEGY_VALID_LOSS.value:
                stats["valid_losses"] += 1
            elif rev.loss_classification == LossClassification.EXECUTION_SLIPPAGE.value:
                stats["slippage_losses"] += 1
            elif rev.loss_classification == LossClassification.DATA_ISSUE.value:
                stats["data_losses"] += 1

    return {
        "models": by_model,
        "total_reviews": len(reviews),
    }


@dataclass
class LessonClassification:
    """
    V16.39 Section 093: Governed 3-Color Lesson Classification Contract.
    Suggested color mapped to canonical severity:
    - GREEN <-> INFO (Observation/positive reference/valid loss complying with plan)
    - YELLOW <-> WARNING (Uncertainty/adverse slippage/missing evidence to verify)
    - RED <-> CRITICAL (Verified severe violation of rule, risk or geometry)
    """
    suggested_color: str                # "GREEN", "YELLOW", "RED"
    canonical_severity: str             # "INFO", "WARNING", "CRITICAL"
    classification_code: str
    reason_vi: str
    verified_facts: Dict[str, Any]
    missing_evidence: List[str]
    proposed_effect: str                # "ANNOTATE", "WARN_ENTRY", "BLOCK_ENTRY"
    suggested_scope: Dict[str, Any]
    classifier_version: str = "v16.39"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suggested_color": self.suggested_color,
            "canonical_severity": self.canonical_severity,
            "classification_code": self.classification_code,
            "reason_vi": self.reason_vi,
            "verified_facts": dict(self.verified_facts),
            "missing_evidence": list(self.missing_evidence),
            "proposed_effect": self.proposed_effect,
            "suggested_scope": dict(self.suggested_scope),
            "classifier_version": self.classifier_version,
        }


@dataclass
class EntryComplianceResult:
    status: str  # "VERIFIED_COMPLIANT", "VERIFIED_VIOLATION", "UNVERIFIABLE"
    reason: str
    violation_code: Optional[str] = None
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "violation_code": self.violation_code,
            "checks_passed": list(self.checks_passed),
            "checks_failed": list(self.checks_failed),
            "missing_evidence": list(self.missing_evidence),
            "evidence_refs": list(self.evidence_refs),
        }


def resolve_model_review_requirements(model_id: Optional[str], version: str = "v16.39") -> Dict[str, Any]:
    """
    V16.39.2 Section 065 - 070:
    Resolves factual review requirements for each execution model.
    Models have distinct causal proof requirements:
    - CONTINUATION: BOS required, does NOT require Sweep.
    - FVG_CONTINUATION: FVG, RETEST, BOS required.
    - REVERSAL: POI, SWEEP, CHOCH required.
    - SWEEP_RECLAIM: SWEEP, RECLAIM required.
    - CG01: POI, MSS, RETEST required.
    - NY_QUOTA: POI required (partial fallback).
    """
    mid = str(model_id or "").upper()
    if "CONTINUATION_FVG" in mid or "FVG_CONTINUATION" in mid:
        return {
            "model_id": "FVG_CONTINUATION",
            "required_kinds": ["FVG", "RETEST", "BOS"],
            "trigger_kinds": ["BOS"],
            "allow_no_sweep": True,
            "requires_trigger_chain": True,
        }
    elif "CONTINUATION" in mid:
        return {
            "model_id": "CONTINUATION",
            "required_kinds": ["BOS"],
            "trigger_kinds": ["BOS"],
            "allow_no_sweep": True,
            "requires_trigger_chain": True,
        }
    elif "SWEEP_RECLAIM" in mid:
        return {
            "model_id": "SWEEP_RECLAIM",
            "required_kinds": ["SWEEP", "RECLAIM"],
            "trigger_kinds": ["RECLAIM"],
            "allow_no_sweep": False,
            "requires_trigger_chain": True,
        }
    elif "CG01" in mid:
        return {
            "model_id": "CG01",
            "required_kinds": ["POI", "MSS", "RETEST"],
            "trigger_kinds": ["MSS"],
            "allow_no_sweep": False,
            "requires_trigger_chain": True,
        }
    elif "NY_QUOTA" in mid:
        return {
            "model_id": "NY_QUOTA",
            "required_kinds": ["POI"],
            "trigger_kinds": ["POI"],
            "allow_no_sweep": True,
            "requires_trigger_chain": False,
        }
    else:  # REVERSAL or default SMC
        return {
            "model_id": "REVERSAL",
            "required_kinds": ["POI", "SWEEP", "CHOCH"],
            "trigger_kinds": ["CHOCH"],
            "allow_no_sweep": False,
            "requires_trigger_chain": True,
        }


def build_entry_review_context(db: Session, order: Any) -> Dict[str, Any]:
    """
    V16.39.2 Section 021 - 030:
    Builds strongly-typed entry review context from historical bound artifacts for a closed order.
    Extracts arm snapshot, fill snapshot, scenario evidence, guard audit, and model requirements.
    Never uses current live market context to explain past trades.
    """
    provenance_issues: List[str] = []

    # 1. Arm decision snapshot
    arm_snapshot: Optional[Dict[str, Any]] = None
    raw_arm = getattr(order, "arm_decision_snapshot", None)
    if raw_arm:
        try:
            arm_snapshot = json.loads(raw_arm) if isinstance(raw_arm, str) else dict(raw_arm)
        except Exception as e:
            provenance_issues.append(f"CORRUPT_ARM_SNAPSHOT: {e}")
    else:
        provenance_issues.append("MISSING_ARM_SNAPSHOT")

    # 2. Fill decision snapshot
    fill_snapshot: Optional[Dict[str, Any]] = None
    raw_fill = getattr(order, "fill_decision_snapshot", None)
    if raw_fill:
        try:
            fill_snapshot = json.loads(raw_fill) if isinstance(raw_fill, str) else dict(raw_fill)
        except Exception as e:
            provenance_issues.append(f"CORRUPT_FILL_SNAPSHOT: {e}")
    else:
        provenance_issues.append("MISSING_FILL_SNAPSHOT")

    # 3. Scenario evidence
    scenario_evidence: Dict[str, Any] = {}
    scen_id = getattr(order, "scenario_snapshot_id", None)
    if scen_id:
        try:
            from services.smc_scenario_repository import get_scenario_snapshot
            scen_snap = get_scenario_snapshot(db, scen_id)
            if scen_snap:
                scenario_evidence["evidence"] = scen_snap.evidence
                scenario_evidence["trigger_chain"] = getattr(scen_snap, "trigger_chain", None)
                scenario_evidence["context"] = getattr(scen_snap, "context", {})
                scenario_evidence["family"] = getattr(scen_snap, "family", None)
                scenario_evidence["direction"] = getattr(scen_snap, "direction", None)
        except Exception as e:
            provenance_issues.append(f"SCENARIO_LOOKUP_FAILED: {e}")

    # Fallback to StrategyEvidence if scenario snapshot didn't provide evidence
    if not scenario_evidence.get("evidence"):
        ev_id = getattr(order, "evidence_snapshot_id", None)
        if ev_id:
            try:
                import models
                ev_row = db.query(models.StrategyEvidence).filter(models.StrategyEvidence.id == ev_id).first()
                if ev_row and ev_row.evidence_json:
                    scenario_evidence["evidence"] = json.loads(ev_row.evidence_json)
            except Exception as e:
                provenance_issues.append(f"STRATEGY_EVIDENCE_LOOKUP_FAILED: {e}")

    # Fallback to arm_snapshot evidence if present
    if not scenario_evidence.get("evidence") and arm_snapshot:
        if "evidence" in arm_snapshot:
            scenario_evidence["evidence"] = arm_snapshot["evidence"]
        if "trigger_chain" in arm_snapshot:
            scenario_evidence["trigger_chain"] = arm_snapshot["trigger_chain"]

    if not scenario_evidence.get("evidence"):
        provenance_issues.append("MISSING_SCENARIO_EVIDENCE")

    # 4. Guard audit from checklist_snapshot
    guard_audit: Optional[Dict[str, Any]] = None
    raw_chk = getattr(order, "checklist_snapshot", None)
    if raw_chk:
        try:
            guard_audit = json.loads(raw_chk) if isinstance(raw_chk, str) else dict(raw_chk)
        except Exception as e:
            provenance_issues.append(f"CORRUPT_CHECKLIST_SNAPSHOT: {e}")

    # 5. Model requirements
    family = getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC"
    model_reqs = resolve_model_review_requirements(family)

    return {
        "arm_snapshot": arm_snapshot,
        "fill_snapshot": fill_snapshot,
        "scenario_evidence": scenario_evidence,
        "guard_audit": guard_audit,
        "model_requirements": model_reqs,
        "provenance_issues": provenance_issues,
    }


def assess_entry_compliance_for_review(
    order: Any = None,
    entry_snapshot: Optional[Dict[str, Any]] = None,
    fill_snapshot: Optional[Dict[str, Any]] = None,
    audit: Optional[Any] = None,
    model_requirements: Optional[Dict[str, Any]] = None,
) -> EntryComplianceResult:
    """
    V16.39.2 Section 097:
    Assesses entry and fill compliance against model requirements, geometry, and risk rules.
    Outputs: VERIFIED_COMPLIANT, VERIFIED_VIOLATION, or UNVERIFIABLE.
    """
    passed: List[str] = []
    failed: List[str] = []
    missing: List[str] = []
    refs: List[str] = []

    # If neither entry_snapshot nor audit nor order is provided, it is UNVERIFIABLE
    if entry_snapshot is None and audit is None and order is None:
        return EntryComplianceResult(
            status="UNVERIFIABLE",
            reason="Thiếu entry snapshot và compliance audit.",
            missing_evidence=["ENTRY_SNAPSHOT", "COMPLIANCE_AUDIT"],
        )

    # 1. Audit validation
    if audit is not None:
        if isinstance(audit, dict):
            if audit.get("violation_detected") or audit.get("policy_violation"):
                return EntryComplianceResult(
                    status="VERIFIED_VIOLATION",
                    reason=str(audit.get("reason") or "Audit phát hiện vi phạm quy tắc thực thi."),
                    violation_code="AUDIT_POLICY_VIOLATION",
                    checks_failed=["AUDIT_COMPLIANCE"],
                )
            if audit.get("risk_violated"):
                return EntryComplianceResult(
                    status="VERIFIED_VIOLATION",
                    reason="Vượt hạn mức rủi ro theo audit.",
                    violation_code="RISK_BUDGET_VIOLATION",
                    checks_failed=["RISK_BUDGET"],
                )
            if audit.get("compliance_verified") is True:
                passed.append("AUDIT_COMPLIANCE_VERIFIED")

    # 2. Entry snapshot validation
    if not entry_snapshot:
        return EntryComplianceResult(
            status="UNVERIFIABLE",
            reason="Thiếu entry snapshot để xác minh bằng chứng và điều kiện vào lệnh.",
            missing_evidence=["ENTRY_SNAPSHOT"],
        )

    # Geometry verification in snapshot (Section 103)
    s_dir = entry_snapshot.get("direction")
    s_entry = entry_snapshot.get("planned_entry", entry_snapshot.get("entry_price"))
    s_sl = entry_snapshot.get("planned_sl", entry_snapshot.get("stop_loss"))
    s_tp = entry_snapshot.get("planned_tp", entry_snapshot.get("take_profit"))
    s_dir_norm = None
    if s_dir:
        s_dir_norm = "LONG" if str(s_dir).upper() in ("LONG", "BUY") else ("SHORT" if str(s_dir).upper() in ("SHORT", "SELL") else None)

    if not s_dir_norm or s_entry is None or s_sl is None:
        missing.append("NUMERIC_GEOMETRY")
    else:
        try:
            s_entry = float(s_entry)
            s_sl = float(s_sl)
            if not (math.isfinite(s_entry) and math.isfinite(s_sl)) or s_entry <= 0 or s_sl <= 0:
                missing.append("NUMERIC_GEOMETRY")
            elif s_dir_norm == "LONG" and s_sl >= s_entry:
                return EntryComplianceResult(
                    status="VERIFIED_VIOLATION",
                    reason=f"Hình học lệnh vi phạm: SL ({s_sl:.2f}) >= Entry ({s_entry:.2f}) cho LONG",
                    violation_code="GEOMETRY_VIOLATION",
                    checks_failed=["SNAPSHOT_GEOMETRY"],
                )
            elif s_dir_norm == "SHORT" and s_sl <= s_entry:
                return EntryComplianceResult(
                    status="VERIFIED_VIOLATION",
                    reason=f"Hình học lệnh vi phạm: SL ({s_sl:.2f}) <= Entry ({s_entry:.2f}) cho SHORT",
                    violation_code="GEOMETRY_VIOLATION",
                    checks_failed=["SNAPSHOT_GEOMETRY"],
                )
            else:
                if s_tp is not None:
                    s_tp = float(s_tp)
                    if not math.isfinite(s_tp) or s_tp <= 0:
                        missing.append("NUMERIC_GEOMETRY")
                    elif s_dir_norm == "LONG" and s_tp <= s_entry:
                        return EntryComplianceResult(
                            status="VERIFIED_VIOLATION",
                            reason=f"Hình học lệnh vi phạm: TP ({s_tp:.2f}) <= Entry ({s_entry:.2f}) cho LONG",
                            violation_code="GEOMETRY_VIOLATION",
                            checks_failed=["SNAPSHOT_GEOMETRY"],
                        )
                    elif s_dir_norm == "SHORT" and s_tp >= s_entry:
                        return EntryComplianceResult(
                            status="VERIFIED_VIOLATION",
                            reason=f"Hình học lệnh vi phạm: TP ({s_tp:.2f}) >= Entry ({s_entry:.2f}) cho SHORT",
                            violation_code="GEOMETRY_VIOLATION",
                            checks_failed=["SNAPSHOT_GEOMETRY"],
                        )
                    else:
                        passed.append("PRICE_GEOMETRY")
                else:
                    passed.append("PRICE_GEOMETRY")
        except (ValueError, TypeError):
            missing.append("NUMERIC_GEOMETRY")

    # 3. Model requirements resolution (Section 065 - 070)
    model_id = str(
        entry_snapshot.get("model_id")
        or entry_snapshot.get("strategy_family")
        or (getattr(order, "strategy_family", None) if order else None)
        or "STANDARD_SMC"
    ).upper()
    reqs = model_requirements or resolve_model_review_requirements(model_id)

    # 4. Extract evidence nodes and observed kinds (Section 013 repair)
    raw_ev = entry_snapshot.get("evidence")
    nodes: List[Any] = []
    if isinstance(raw_ev, (list, tuple)):
        nodes = list(raw_ev)
    elif isinstance(raw_ev, dict):
        if "items" in raw_ev and isinstance(raw_ev["items"], (list, tuple)):
            nodes = list(raw_ev["items"])
        elif "ev_nodes" in raw_ev and isinstance(raw_ev["ev_nodes"], (list, tuple)):
            nodes = list(raw_ev["ev_nodes"])
        elif "nodes" in raw_ev and isinstance(raw_ev["nodes"], (list, tuple)):
            nodes = list(raw_ev["nodes"])

    observed_kinds: set = set()
    for node in nodes:
        if isinstance(node, dict):
            k = str(node.get("kind") or "").upper()
            nid = str(node.get("id") or "")
            if k:
                observed_kinds.add(k)
            if nid:
                refs.append(nid)
        elif hasattr(node, "kind"):
            k = str(getattr(node, "kind") or "").upper()
            nid = str(getattr(node, "id") or "")
            if k:
                observed_kinds.add(k)
            if nid:
                refs.append(nid)

    # Extract kinds from legacy flags if dictionary format:
    if not observed_kinds and isinstance(raw_ev, dict):
        if raw_ev.get("has_bos") or raw_ev.get("bos_id"):
            observed_kinds.add("BOS")
        if raw_ev.get("has_sweep") or raw_ev.get("sweep_detected") or raw_ev.get("sweep_id"):
            observed_kinds.add("SWEEP")
            observed_kinds.add("SWEEP_LOW" if s_dir_norm == "LONG" else "SWEEP_HIGH")
        if raw_ev.get("has_trigger") or raw_ev.get("mss_detected") or raw_ev.get("choch_detected"):
            observed_kinds.add("CHOCH")
            observed_kinds.add("MSS")
        if raw_ev.get("retest_detected") or raw_ev.get("fvg_retest"):
            observed_kinds.add("RETEST")
        if raw_ev.get("fvg_id") or raw_ev.get("fvg_zone"):
            observed_kinds.add("FVG")
        if raw_ev.get("zone_id") or raw_ev.get("poi_confirmed"):
            observed_kinds.add("POI")

    # 5. Check trigger chain (Section 024, 061)
    tc = entry_snapshot.get("trigger_chain")
    tc_verified = False
    if isinstance(tc, dict):
        if tc.get("verified") is True:  # STRICT True check
            tc_model = str(tc.get("model_id") or "").upper()
            # Model match check (Section 029 & 158)
            if tc_model and model_id and not (tc_model in model_id or model_id in tc_model):
                failed.append("TRIGGER_MODEL_MISMATCH")
                return EntryComplianceResult(
                    status="UNVERIFIABLE",
                    reason=f"Trigger chain model '{tc_model}' không khớp với model lệnh '{model_id}'.",
                    missing_evidence=["MODEL_TRIGGER_MATCH"],
                    checks_failed=failed,
                )
            tc_verified = True
            passed.append("TRIGGER_CHAIN_VERIFIED")
            for nid in tc.get("node_ids") or ():
                refs.append(str(nid))
        else:
            failed.append("TRIGGER_CHAIN_UNVERIFIED")
    elif entry_snapshot.get("compliance_verified") is True or (audit and audit.get("compliance_verified") is True):
        # Legacy claim: allowed if trigger/required node exists and no failed tc
        if observed_kinds:
            tc_verified = True
            passed.append("LEGACY_TRIGGER_CLAIM_VERIFIED")

    # 6. Verify required kinds for the specific model (Section 013, 065 - 071)
    req_kinds = reqs.get("required_kinds", [])
    trigger_kinds = reqs.get("trigger_kinds", req_kinds)
    missing_req: List[str] = []

    is_legacy_verified = bool(entry_snapshot.get("compliance_verified") or (audit and audit.get("compliance_verified")))
    kinds_to_check = trigger_kinds if (is_legacy_verified and len(nodes) == 1 and not tc) else req_kinds

    for rk in kinds_to_check:
        if rk == "SWEEP":
            if not any(k in observed_kinds for k in ("SWEEP", "SWEEP_LOW", "SWEEP_HIGH")):
                missing_req.append("REQUIRED_NODE_SWEEP")
        elif rk in ("SWEEP_LOW", "SWEEP_HIGH"):
            if rk not in observed_kinds and "SWEEP" not in observed_kinds:
                missing_req.append(f"REQUIRED_NODE_{rk}")
        elif rk in ("CHOCH", "MSS"):
            if not any(k in observed_kinds for k in ("CHOCH", "MSS", "BREAK")):
                missing_req.append("REQUIRED_NODE_TRIGGER")
        elif rk not in observed_kinds:
            missing_req.append(f"REQUIRED_NODE_{rk}")

    requires_tc = reqs.get("requires_trigger_chain", True)
    if requires_tc and not tc_verified:
        missing_req.append("TRIGGER_CHAIN_UNVERIFIED")

    if missing_req or "NUMERIC_GEOMETRY" in missing:
        missing.extend(missing_req)
        return EntryComplianceResult(
            status="UNVERIFIABLE",
            reason=f"Thiếu bằng chứng bắt buộc cho mô hình {model_id}: {', '.join(missing_req or missing)}.",
            missing_evidence=list(dict.fromkeys(missing)),
            checks_passed=passed,
            checks_failed=failed + missing_req,
            evidence_refs=refs,
        )

    passed.append("MODEL_REQUIREMENTS_VERIFIED")
    return EntryComplianceResult(
        status="VERIFIED_COMPLIANT",
        reason=f"Entry snapshot và audit tuân thủ mô hình {model_id} đã xác minh đầy đủ.",
        checks_passed=passed or ["COMPLIANCE_VERIFIED"],
        evidence_refs=refs,
    )


def classify_post_trade_lesson(
    review: PostTradeReview,
    entry_snapshot: Optional[Dict[str, Any]] = None,
    audit: Optional[Any] = None,
) -> LessonClassification:
    """
    V16.39.1 Sections 015, 096 - 110:
    Pure deterministic classifier for post-trade reviews based on empirical evidence.
    Hierarchy:
    1. Verified serious violation (rule violation, wrong geometry, risk exceeded) -> RED (CRITICAL)
    2. Missing evidence, unconfirmed cause, unverified compliance, adverse execution -> YELLOW (WARNING)
    3. Valid plan compliance ONLY when verified by entry_snapshot/audit -> GREEN (INFO)
    Confirmed SL without entry snapshot/audit -> YELLOW (UNVERIFIABLE_CAUSE).
    """
    facts = {
        "order_id": review.order_id,
        "symbol": review.symbol,
        "model_id": review.model_id,
        "direction": review.direction,
        "net_pnl": review.net_pnl,
        "realized_rr": review.realized_rr,
        "exit_cause": review.exit_cause,
        "adverse_slippage_usd": review.adverse_slippage_usd,
        "loss_classification": review.loss_classification,
    }
    missing_evidence: List[str] = []
    scope = {
        "symbol": review.symbol,
        "strategy_family": review.model_id,
        "direction": review.direction,
        "execution_mode": "PAPER",
        "session": "ALL",
    }

    # 1. Full geometry verification (Section 103)
    is_wrong_geometry = False
    if review.direction in ("LONG", "BUY"):
        if review.planned_entry <= review.planned_sl:
            is_wrong_geometry = True
        elif review.planned_tp > 0 and review.planned_tp <= review.planned_entry:
            is_wrong_geometry = True
    elif review.direction in ("SHORT", "SELL"):
        if review.planned_entry >= review.planned_sl:
            is_wrong_geometry = True
        elif review.planned_tp > 0 and review.planned_tp >= review.planned_entry:
            is_wrong_geometry = True
    elif review.direction not in ("LONG", "BUY", "SHORT", "SELL"):
        is_wrong_geometry = True

    is_rule_violation = (review.loss_classification == LossClassification.POLICY_VIOLATION.value)

    if is_rule_violation or is_wrong_geometry:
        reason = "Đỏ vì phát hiện vi phạm quy tắc thực thi hoặc cấu trúc lệnh không hợp lệ."
        if is_wrong_geometry:
            reason = "Đỏ vì hình học lệnh vi phạm: khoảng cách SL/TP đảo ngược với hướng giao dịch."
        return LessonClassification(
            suggested_color="RED",
            canonical_severity="CRITICAL",
            classification_code="VERIFIED_RULE_VIOLATION",
            reason_vi=reason,
            verified_facts=facts,
            missing_evidence=missing_evidence,
            proposed_effect="WARN_ENTRY",
            suggested_scope=scope,
        )

    # 2. Check outcome confirmation (Section 118)
    if review.outcome_status == "OUTCOME_UNCONFIRMED":
        missing_evidence.append("OUTCOME_CONFIRMATION")
        return LessonClassification(
            suggested_color="YELLOW",
            canonical_severity="WARNING",
            classification_code="OUTCOME_UNCONFIRMED",
            reason_vi="Vàng vì kết quả lệnh chưa được xác nhận hoàn tất; cần thêm dữ liệu để phân tích.",
            verified_facts=facts,
            missing_evidence=missing_evidence,
            proposed_effect="WARN_ENTRY",
            suggested_scope=scope,
        )

    # 3. Assess compliance from entry_snapshot and audit (Section 097)
    if entry_snapshot is None:
        snap_copy = None
    else:
        snap_copy = dict(entry_snapshot)
        if "model_id" not in snap_copy and getattr(review, "model_id", None):
            snap_copy["model_id"] = review.model_id

    comp = assess_entry_compliance_for_review(
        order=None,
        entry_snapshot=snap_copy,
        fill_snapshot=None,
        audit=audit,
    )

    if comp.status == "VERIFIED_VIOLATION":
        return LessonClassification(
            suggested_color="RED",
            canonical_severity="CRITICAL",
            classification_code="VERIFIED_RULE_VIOLATION",
            reason_vi=f"Đỏ vì phát hiện vi phạm thực thi: {comp.reason}",
            verified_facts=facts,
            missing_evidence=comp.missing_evidence,
            proposed_effect="WARN_ENTRY",
            suggested_scope=scope,
        )

    # Section 015 & 098 & 099 & 101: If compliance is unverified, default to YELLOW!
    if comp.status == "UNVERIFIABLE":
        missing_evidence.extend(comp.missing_evidence or ["ENTRY_SNAPSHOT"])
        return LessonClassification(
            suggested_color="YELLOW",
            canonical_severity="WARNING",
            classification_code="UNVERIFIABLE_CAUSE",
            reason_vi="Vàng vì thiếu bằng chứng snapshot hoặc audit tuân thủ kế hoạch; chưa đủ dữ liệu để xác nhận đạt chuẩn.",
            verified_facts=facts,
            missing_evidence=list(set(missing_evidence)),
            proposed_effect="WARN_ENTRY",
            suggested_scope=scope,
        )

    # 4. Check for adverse slippage or execution data issues
    if review.loss_classification in (LossClassification.UNVERIFIABLE.value, LossClassification.DATA_ISSUE.value):
        missing_evidence.append("VERIFIABLE_EXIT_DATA")
        return LessonClassification(
            suggested_color="YELLOW",
            canonical_severity="WARNING",
            classification_code="UNVERIFIABLE_CAUSE",
            reason_vi="Vàng vì thiếu bằng chứng snapshot hoặc kết quả đóng lệnh do vấn đề dữ liệu/thời gian; cần xác minh thêm.",
            verified_facts=facts,
            missing_evidence=list(set(missing_evidence)),
            proposed_effect="WARN_ENTRY",
            suggested_scope=scope,
        )

    if review.adverse_slippage_usd > 1.0 or review.loss_classification == LossClassification.EXECUTION_SLIPPAGE.value:
        return LessonClassification(
            suggested_color="YELLOW",
            canonical_severity="WARNING",
            classification_code="ADVERSE_SLIPPAGE_OBSERVED",
            reason_vi=f"Vàng vì trượt giá entry bất lợi ({review.adverse_slippage_usd:.2f} USD) vượt ngưỡng cho phép; cần theo dõi điều kiện khớp lệnh.",
            verified_facts=facts,
            missing_evidence=missing_evidence,
            proposed_effect="WARN_ENTRY",
            suggested_scope=scope,
        )

    # 5. Verified Compliance -> GREEN (INFO) (Section 100)
    if review.outcome_status == "WIN":
        reason = f"Xanh vì giao dịch tuân thủ kế hoạch đã xác minh và đạt mục tiêu lợi nhuận ({review.net_pnl:+.2f} USDT, {review.realized_rr:+.2f}R)."
        code = "PLAN_COMPLIANT_WIN"
    elif review.outcome_status == "BREAKEVEN":
        reason = f"Xanh vì giao dịch tuân thủ kế hoạch đã xác minh và đóng hòa vốn ({review.net_pnl:+.2f} USDT)."
        code = "PLAN_COMPLIANT_BREAKEVEN"
    elif review.loss_classification == LossClassification.STRATEGY_VALID_LOSS.value or review.exit_cause in ("SL_HIT", "STOP_LOSS"):
        reason = "Xanh vì dừng lỗ hợp lệ theo đúng cấu trúc và tuân thủ kế hoạch đã xác minh; tiếp tục thu thập mẫu dữ liệu."
        code = "PLAN_COMPLIANT_VALID_LOSS"
    else:
        reason = "Xanh vì kế hoạch thực thi đạt chuẩn tuân thủ đã xác minh; ghi nhận quan sát cho các chu kỳ tiếp theo."
        code = "PLAN_COMPLIANT_OBSERVATION"

    return LessonClassification(
        suggested_color="GREEN",
        canonical_severity="INFO",
        classification_code=code,
        reason_vi=reason,
        verified_facts=facts,
        missing_evidence=missing_evidence,
        proposed_effect="ANNOTATE",
        suggested_scope=scope,
    )


def save_post_trade_review(db: Session, review: PostTradeReview) -> str:
    """
    V16.39 Section 078 - 080: Saves post-trade review into post_trade_reviews table.
    Idempotent on order_id. Does NOT call db.commit() to preserve caller transaction.
    """
    import models
    rec = db.query(models.PostTradeReviewRecord).filter(models.PostTradeReviewRecord.order_id == review.order_id).first()
    rev_dict = review.to_dict()
    rev_json = json.dumps(rev_dict, ensure_ascii=False)
    now_ms = review.as_of_ms or int(time.time() * 1000)

    if rec is None:
        rec_id = f"rev_{review.order_id}"
        rec = models.PostTradeReviewRecord(
            id=rec_id,
            order_id=review.order_id,
            symbol=review.symbol,
            model_id=review.model_id,
            direction=review.direction,
            planned_entry=review.planned_entry,
            actual_entry=review.actual_entry,
            planned_sl=review.planned_sl,
            planned_tp=review.planned_tp,
            exit_price=review.exit_price,
            exit_cause=review.exit_cause,
            gross_pnl=review.gross_pnl,
            net_pnl=review.net_pnl,
            realized_rr=review.realized_rr,
            outcome_status=review.outcome_status,
            hold_time_ms=review.hold_time_ms,
            entry_slippage_usd=review.entry_slippage_usd,
            loss_classification=review.loss_classification,
            review_json=rev_json,
            created_at=now_ms,
        )
        db.add(rec)
    else:
        rec.symbol = review.symbol
        rec.model_id = review.model_id
        rec.direction = review.direction
        rec.planned_entry = review.planned_entry
        rec.actual_entry = review.actual_entry
        rec.planned_sl = review.planned_sl
        rec.planned_tp = review.planned_tp
        rec.exit_price = review.exit_price
        rec.exit_cause = review.exit_cause
        rec.gross_pnl = review.gross_pnl
        rec.net_pnl = review.net_pnl
        rec.realized_rr = review.realized_rr
        rec.outcome_status = review.outcome_status
        rec.hold_time_ms = review.hold_time_ms
        rec.entry_slippage_usd = review.entry_slippage_usd
        rec.loss_classification = review.loss_classification
        rec.review_json = rev_json

    db.flush()
    return rec.id


def get_post_trade_review(db: Session, order_id: str) -> Optional[PostTradeReview]:
    """
    V16.39 Section 080: Retrieves post trade review record by order_id.
    """
    import models
    rec = db.query(models.PostTradeReviewRecord).filter(models.PostTradeReviewRecord.order_id == order_id).first()
    if not rec:
        return None
    try:
        data = json.loads(rec.review_json)
        return PostTradeReview(**data)
    except Exception as e:
        logger.warning(f"Failed to deserialize review_json for order {order_id}: {e}")
        return PostTradeReview(
            order_id=rec.order_id,
            symbol=rec.symbol,
            model_id=rec.model_id,
            direction=rec.direction,
            planned_entry=rec.planned_entry,
            actual_entry=rec.actual_entry,
            planned_sl=rec.planned_sl,
            planned_tp=rec.planned_tp,
            exit_price=rec.exit_price,
            exit_cause=rec.exit_cause,
            gross_pnl=rec.gross_pnl,
            net_pnl=rec.net_pnl,
            realized_rr=rec.realized_rr,
            hold_time_ms=rec.hold_time_ms,
            entry_slippage_usd=rec.entry_slippage_usd,
            loss_classification=rec.loss_classification,
            outcome_status=rec.outcome_status,
            as_of_ms=rec.created_at,
        )
