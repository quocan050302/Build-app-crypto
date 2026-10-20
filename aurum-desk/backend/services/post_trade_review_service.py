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
    provenance_issues: List[str] = field(default_factory=list)
    decision_time_ms: Optional[int] = None
    requirements_version: str = "v16.39"

    @property
    def failed_checks(self) -> List[str]:
        return self.checks_failed

    @property
    def passed_checks(self) -> List[str]:
        return self.checks_passed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "violation_code": self.violation_code,
            "checks_passed": list(self.checks_passed),
            "checks_failed": list(self.checks_failed),
            "missing_evidence": list(self.missing_evidence),
            "evidence_refs": list(self.evidence_refs),
            "provenance_issues": list(self.provenance_issues),
            "decision_time_ms": self.decision_time_ms,
            "requirements_version": self.requirements_version,
        }


def canonical_model_family(family: Optional[str]) -> str:
    """
    V16.39.3 Section 025, 053, 056:
    Canonical strategy model normalization for review and trigger binding.
    """
    m = str(family or "").upper().strip()
    if "CONTINUATION_FVG" in m or "FVG_CONTINUATION" in m:
        return "FVG_CONTINUATION"
    if "CONTINUATION" in m:
        return "CONTINUATION"
    if "SWEEP_RECLAIM" in m:
        return "SWEEP_RECLAIM"
    if "CG01" in m:
        return "CG01"
    if "NY_QUOTA" in m:
        return "NY_QUOTA"
    if "REVERSAL" in m:
        return "REVERSAL"
    return m or "UNKNOWN"


def resolve_model_review_requirements(
    model_id: Optional[str],
    version: str = "v16.39",
    entry_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    V16.39.2 Section 065 - 070, V16.39.5 Rule 086, 126, 127:
    Resolves factual review requirements for each execution model.
    Models have distinct causal proof requirements:
    - CONTINUATION: BOS required, does NOT require Sweep.
    - FVG_CONTINUATION: FVG, RETEST, BOS required.
    - REVERSAL: POI, SWEEP, CHOCH required.
    - SWEEP_RECLAIM: SWEEP, RECLAIM required.
    - CG01: POI, MSS required; RETEST required for MARKET retest, FVG required for LIMIT.
    - NY_QUOTA: POI required (partial fallback).
    """
    c_mid = canonical_model_family(model_id)
    if c_mid == "FVG_CONTINUATION":
        return {
            "model_id": "FVG_CONTINUATION",
            "required_kinds": ["FVG", "RETEST", "BOS"],
            "trigger_kinds": ["BOS"],
            "allow_no_sweep": True,
            "requires_trigger_chain": True,
        }
    elif c_mid == "CONTINUATION":
        return {
            "model_id": "CONTINUATION",
            "required_kinds": ["BOS"],
            "trigger_kinds": ["BOS"],
            "allow_no_sweep": True,
            "requires_trigger_chain": True,
        }
    elif c_mid == "SWEEP_RECLAIM":
        return {
            "model_id": "SWEEP_RECLAIM",
            "required_kinds": ["SWEEP", "RECLAIM"],
            "trigger_kinds": ["RECLAIM"],
            "allow_no_sweep": False,
            "requires_trigger_chain": True,
        }
    elif c_mid == "CG01":
        is_limit = (str(entry_mode).upper() == "LIMIT_AT_CE_AFTER_CONFIRMATION") or ("LIMIT" in str(entry_mode).upper())
        req_kinds = ["POI", "MSS", "FVG"] if is_limit else ["POI", "MSS", "RETEST"]
        return {
            "model_id": "CG01",
            "required_kinds": req_kinds,
            "trigger_kinds": ["MSS"],
            "allow_no_sweep": False,
            "requires_trigger_chain": True,
        }
    elif c_mid == "NY_QUOTA":
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
    V16.39.3 Section 016 - 040:
    Builds strongly-typed entry review context from historical bound artifacts for a closed order.
    Decodes ORM SMCScenarioSnapshot row via snapshot_from_row into ScenarioSnapshot dataclass.
    Enforces strict symbol, direction, model family, and instance bindings.
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

    # 3. Scenario evidence decoding (Section 016 - 020)
    scenario_evidence: Dict[str, Any] = {}
    scen_id = getattr(order, "scenario_snapshot_id", None)
    if scen_id:
        try:
            from services.smc_scenario_repository import get_scenario_snapshot, snapshot_from_row
            scen_row = get_scenario_snapshot(db, scen_id)
            if scen_row is None:
                provenance_issues.append(f"BOUND_SCENARIO_NOT_FOUND: {scen_id}")
            else:
                snapshot = None
                try:
                    snapshot = snapshot_from_row(scen_row)
                except Exception as dec_err:
                    provenance_issues.append(f"CORRUPT_BOUND_SCENARIO: {dec_err}")

                if snapshot is not None:
                    # ID consistency check (Section 022)
                    if scen_row.snapshot_id != scen_id or snapshot.snapshot_id != scen_id:
                        provenance_issues.append("PROVENANCE_CONFLICT_SNAPSHOT_ID")

                    # Order symbol binding (Section 023)
                    ord_sym = getattr(order, "instrument", None) or getattr(order, "symbol", None)
                    if ord_sym and snapshot.symbol and ord_sym != snapshot.symbol:
                        provenance_issues.append(f"PROVENANCE_CONFLICT_SYMBOL: order={ord_sym} vs snap={snapshot.symbol}")

                    # Order direction binding (Section 024)
                    ord_dir = getattr(order, "direction", None)
                    if ord_dir and snapshot.direction:
                        ord_dir_norm = "LONG" if str(ord_dir).upper() in ("LONG", "BUY") else ("SHORT" if str(ord_dir).upper() in ("SHORT", "SELL") else None)
                        snap_dir_norm = "LONG" if str(snapshot.direction).upper() in ("LONG", "BUY") else ("SHORT" if str(snapshot.direction).upper() in ("SHORT", "SELL") else None)
                        if ord_dir_norm != snap_dir_norm:
                            provenance_issues.append(f"PROVENANCE_CONFLICT_DIRECTION: order={ord_dir} vs snap={snapshot.direction}")

                    # Model binding (Section 025)
                    ord_fam = getattr(order, "strategy_family", None)
                    if ord_fam and snapshot.family:
                        if canonical_model_family(ord_fam) != canonical_model_family(snapshot.family):
                            provenance_issues.append(f"PROVENANCE_CONFLICT_MODEL: order={ord_fam} vs snap={snapshot.family}")

                    # Instance binding (Section 026)
                    ord_inst = getattr(order, "setup_instance_id", None)
                    if ord_inst and snapshot.instance_id and ord_inst != snapshot.instance_id:
                        provenance_issues.append(f"PROVENANCE_CONFLICT_INSTANCE: order={ord_inst} vs snap={snapshot.instance_id}")

                    scenario_evidence["evidence"] = snapshot.evidence
                    scenario_evidence["trigger_chain"] = snapshot.trigger_chain
                    scenario_evidence["context"] = snapshot.context
                    scenario_evidence["family"] = snapshot.family
                    scenario_evidence["direction"] = snapshot.direction
                    scenario_evidence["symbol"] = snapshot.symbol
                    scenario_evidence["as_of_ms"] = snapshot.as_of_ms
                    scenario_evidence["evidence_cutoff_ms"] = snapshot.evidence_cutoff_ms
                    scenario_evidence["plan"] = snapshot.plan
                    scenario_evidence["snapshot_id"] = snapshot.snapshot_id
        except Exception as e:
            provenance_issues.append(f"SCENARIO_LOOKUP_FAILED: {e}")

    # Fallback to StrategyEvidence if scenario snapshot didn't provide evidence (Section 029, 030)
    ev_id = getattr(order, "evidence_snapshot_id", None)
    if not scenario_evidence.get("evidence") and ev_id:
        try:
            import models
            ev_row = db.query(models.StrategyEvidence).filter(models.StrategyEvidence.id == ev_id).first()
            if ev_row and hasattr(ev_row, "evidence_json") and getattr(ev_row, "evidence_json", None):
                parsed_ev = json.loads(ev_row.evidence_json)
                if isinstance(parsed_ev, dict):
                    scenario_evidence["evidence"] = parsed_ev.get("evidence") or parsed_ev.get("items") or parsed_ev.get("nodes") or parsed_ev
                    if "trigger_chain" in parsed_ev:
                        scenario_evidence["trigger_chain"] = parsed_ev["trigger_chain"]
                elif isinstance(parsed_ev, list):
                    scenario_evidence["evidence"] = parsed_ev
            elif ev_row:
                ev_list = []
                for col_name, kind in [
                    ("sweep_evidence", "SWEEP"),
                    ("displacement_evidence", "MSS"),
                    ("fvg_evidence", "FVG"),
                    ("retest_evidence", "RETEST"),
                ]:
                    val = getattr(ev_row, col_name, None)
                    if val:
                        try:
                            d = json.loads(val) if isinstance(val, str) else val
                            if isinstance(d, dict):
                                if "kind" not in d:
                                    d["kind"] = kind
                                ev_list.append(d)
                            elif isinstance(d, list):
                                ev_list.extend(d)
                        except Exception:
                            pass
                if ev_list:
                    scenario_evidence["evidence"] = ev_list
                    scenario_evidence["family"] = getattr(ev_row, "strategy_family", None)
                    scenario_evidence["direction"] = getattr(ev_row, "direction", None)
                    scenario_evidence["as_of_ms"] = getattr(ev_row, "created_at", None)
                    trigger_nodes = [n["id"] for n in ev_list if n.get("kind") in ("MSS", "BOS", "CHOCH", "RECLAIM")]
                    if trigger_nodes:
                        scenario_evidence["trigger_chain"] = {
                            "verified": True,
                            "model_id": getattr(ev_row, "strategy_family", "CG01"),
                            "node_ids": trigger_nodes,
                        }
        except Exception as e:
            provenance_issues.append(f"STRATEGY_EVIDENCE_LOOKUP_FAILED: {e}")

    # Fallback to arm_snapshot evidence if present (Section 032)
    if not scenario_evidence.get("evidence") and arm_snapshot:
        if "evidence" in arm_snapshot:
            scenario_evidence["evidence"] = arm_snapshot["evidence"]
        if "trigger_chain" in arm_snapshot:
            scenario_evidence["trigger_chain"] = arm_snapshot["trigger_chain"]

    if not scenario_evidence.get("evidence"):
        provenance_issues.append("MISSING_SCENARIO_EVIDENCE")

    # 4. Guard audit from checklist_snapshot (Section 034)
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

    decision_as_of_ms = (
        getattr(order, "armed_at", None)
        or scenario_evidence.get("evidence_cutoff_ms")
        or scenario_evidence.get("as_of_ms")
        or getattr(order, "created_at", None)
    )

    if any("CORRUPT" in iss for iss in provenance_issues):
        source_status = "CORRUPT_BOUND_SCENARIO"
    elif any("PROVENANCE_CONFLICT" in iss for iss in provenance_issues):
        source_status = "PROVENANCE_CONFLICT"
    elif "MISSING_SCENARIO_EVIDENCE" in provenance_issues or not scenario_evidence.get("evidence"):
        source_status = "MISSING_SOURCE"
    elif not scen_id and ev_id and scenario_evidence.get("evidence"):
        source_status = "FALLBACK_STRATEGY_EVIDENCE"
    else:
        source_status = "BOUND_SCENARIO"

    return {
        "source_status": source_status,
        "arm_snapshot": arm_snapshot,
        "fill_snapshot": fill_snapshot,
        "scenario_evidence": scenario_evidence,
        "trigger_chain": scenario_evidence.get("trigger_chain"),
        "guard_audit": guard_audit,
        "model_requirements": model_reqs,
        "provenance_issues": provenance_issues,
        "decision_as_of_ms": decision_as_of_ms,
        "direction": getattr(order, "direction", None) or scenario_evidence.get("direction"),
        "symbol": getattr(order, "instrument", None) or scenario_evidence.get("symbol"),
        "scenario_snapshot_id": scen_id,
        "evidence_snapshot_id": ev_id,
        "bound_ids": {
            "scenario_snapshot_id": scen_id,
            "evidence_snapshot_id": ev_id,
            "setup_instance_id": getattr(order, "setup_instance_id", None),
            "order_id": getattr(order, "id", None),
        },
    }


def classify_review_provenance_issues(issues: Sequence[str]) -> Tuple[bool, List[str]]:
    """
    V16.39.4 Section 086:
    Classifies provenance issues into hard verification-blocking conflicts vs informational warnings.
    Hard conflicts:
    - INSTANCE_MISMATCH
    - SNAPSHOT_ID_MISMATCH
    - SYMBOL_MISMATCH
    - DIRECTION_MISMATCH
    - MODEL_MISMATCH
    - CORRUPT_BOUND_SCENARIO
    - BOUND_SCENARIO_NOT_FOUND
    - PROVENANCE_CONFLICT
    """
    hard_conflict_prefixes = (
        "INSTANCE_MISMATCH",
        "SNAPSHOT_ID_MISMATCH",
        "SYMBOL_MISMATCH",
        "DIRECTION_MISMATCH",
        "MODEL_MISMATCH",
        "CORRUPT_BOUND_SCENARIO",
        "BOUND_SCENARIO_NOT_FOUND",
        "PROVENANCE_CONFLICT",
    )
    conflicts = []
    for issue in issues or ():
        issue_str = str(issue).strip()
        if any(p in issue_str for p in hard_conflict_prefixes):
            conflicts.append(issue_str)
    has_hard_conflict = len(conflicts) > 0
    return has_hard_conflict, conflicts


def assess_entry_compliance_for_review(
    order: Any = None,
    entry_snapshot: Optional[Dict[str, Any]] = None,
    fill_snapshot: Optional[Dict[str, Any]] = None,
    audit: Optional[Any] = None,
    model_requirements: Optional[Dict[str, Any]] = None,
    decision_as_of_ms: Optional[int] = None,
    provenance_issues: Optional[List[str]] = None,
    review_context: Optional[Dict[str, Any]] = None,
) -> EntryComplianceResult:
    """
    V16.39.3 / V16.39.4 Sections 041 - 088, 094, 101 - 108:
    Assesses entry and fill compliance against model requirements, causal timeline, and geometry.
    Filters out future nodes, wrong-symbol nodes, and direction-contradicting nodes.
    Validates trigger chain references against strictly valid factual nodes.
    Outputs: VERIFIED_COMPLIANT, VERIFIED_VIOLATION, or UNVERIFIABLE.
    """
    passed: List[str] = []
    failed: List[str] = []
    missing: List[str] = []
    refs: List[str] = []

    # Read and classify all provenance issues (Section 029, 084 - 088, 165)
    raw_provenance = list(provenance_issues or [])
    if review_context and isinstance(review_context, dict):
        raw_provenance.extend(review_context.get("provenance_issues") or [])
    if entry_snapshot and isinstance(entry_snapshot, dict):
        raw_provenance.extend(entry_snapshot.get("provenance_issues") or [])
    raw_provenance = list(dict.fromkeys(raw_provenance))

    has_hard_conflict, hard_conflicts = classify_review_provenance_issues(raw_provenance)
    if has_hard_conflict:
        return EntryComplianceResult(
            status="UNVERIFIABLE",
            reason=f"Phát hiện xung đột nguồn dữ liệu (provenance conflict): {', '.join(hard_conflicts)}.",
            missing_evidence=["RESOLVED_PROVENANCE"],
            checks_passed=passed,
            checks_failed=["PROVENANCE_INTEGRITY"],
            provenance_issues=raw_provenance,
            decision_time_ms=decision_as_of_ms or (getattr(order, "armed_at", None) if order else None),
        )

    # If neither entry_snapshot nor audit nor order is provided, it is UNVERIFIABLE
    if entry_snapshot is None and audit is None and order is None:
        return EntryComplianceResult(
            status="UNVERIFIABLE",
            reason="Thiếu entry snapshot và compliance audit.",
            missing_evidence=["ENTRY_SNAPSHOT", "COMPLIANCE_AUDIT"],
            provenance_issues=raw_provenance,
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
                    provenance_issues=raw_provenance,
                )
            if audit.get("risk_violated"):
                return EntryComplianceResult(
                    status="VERIFIED_VIOLATION",
                    reason="Vượt hạn mức rủi ro theo audit.",
                    violation_code="RISK_BUDGET_VIOLATION",
                    checks_failed=["RISK_BUDGET"],
                    provenance_issues=raw_provenance,
                )
            if audit.get("compliance_verified") is True:
                passed.append("AUDIT_COMPLIANCE_VERIFIED")

    # 2. Entry snapshot validation
    if not entry_snapshot:
        return EntryComplianceResult(
            status="UNVERIFIABLE",
            reason="Thiếu entry snapshot để xác minh bằng chứng và điều kiện vào lệnh.",
            missing_evidence=["ENTRY_SNAPSHOT"],
            provenance_issues=raw_provenance,
        )

    # Resolve decision cutoff timestamp (Section 031, 043 - 046, 101 - 105, 162)
    decision_time = (
        decision_as_of_ms
        or entry_snapshot.get("decision_as_of_ms")
        or entry_snapshot.get("evidence_cutoff_ms")
        or entry_snapshot.get("as_of_ms")
        or (getattr(order, "armed_at", None) if order else None)
    )

    if decision_time is None:
        missing.append("DECISION_CUTOFF")

    # Resolve candidate symbol and direction
    ord_sym = (
        (getattr(order, "instrument", None) if order else None)
        or (getattr(order, "symbol", None) if order else None)
        or entry_snapshot.get("symbol")
    )
    s_dir = (
        (getattr(order, "direction", None) if order else None)
        or entry_snapshot.get("direction")
    )
    s_dir_norm = None
    if s_dir:
        s_dir_norm = "LONG" if str(s_dir).upper() in ("LONG", "BUY") else ("SHORT" if str(s_dir).upper() in ("SHORT", "SELL") else None)

    # Geometry verification in snapshot (Section 030, 052, 076, 094, 161)
    s_plan = (entry_snapshot.get("plan") or {}) if isinstance(entry_snapshot, dict) else {}
    s_entry = (
        entry_snapshot.get("planned_entry")
        or entry_snapshot.get("entry_price")
        or s_plan.get("entry")
        or s_plan.get("planned_entry")
        or (getattr(order, "planned_entry", None) if order else None)
    )
    s_sl = (
        entry_snapshot.get("planned_sl")
        or entry_snapshot.get("stop_loss")
        or s_plan.get("stop_loss")
        or s_plan.get("sl")
        or (getattr(order, "stop_loss", None) if order else None)
    )
    s_tp = (
        entry_snapshot.get("planned_tp")
        or entry_snapshot.get("take_profit")
        or s_plan.get("take_profit")
        or s_plan.get("tp")
        or (getattr(order, "take_profit", None) if order else None)
    )

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
                    provenance_issues=raw_provenance,
                    decision_time_ms=decision_time,
                )
            elif s_dir_norm == "SHORT" and s_sl <= s_entry:
                return EntryComplianceResult(
                    status="VERIFIED_VIOLATION",
                    reason=f"Hình học lệnh vi phạm: SL ({s_sl:.2f}) <= Entry ({s_entry:.2f}) cho SHORT",
                    violation_code="GEOMETRY_VIOLATION",
                    checks_failed=["SNAPSHOT_GEOMETRY"],
                    provenance_issues=raw_provenance,
                    decision_time_ms=decision_time,
                )
            elif s_tp is None:
                # Section 030, 094, 161: Complete PRICE_GEOMETRY requires direction, entry, SL, AND TP!
                missing.append("PLANNED_TP")
            else:
                s_tp = float(s_tp)
                if not math.isfinite(s_tp) or s_tp <= 0:
                    missing.append("NUMERIC_GEOMETRY")
                elif s_dir_norm == "LONG" and s_tp <= s_entry:
                    return EntryComplianceResult(
                        status="VERIFIED_VIOLATION",
                        reason=f"Hình học lệnh vi phạm: TP ({s_tp:.2f}) <= Entry ({s_entry:.2f}) cho LONG",
                        violation_code="GEOMETRY_VIOLATION",
                        checks_failed=["SNAPSHOT_GEOMETRY"],
                        provenance_issues=raw_provenance,
                        decision_time_ms=decision_time,
                    )
                elif s_dir_norm == "SHORT" and s_tp >= s_entry:
                    return EntryComplianceResult(
                        status="VERIFIED_VIOLATION",
                        reason=f"Hình học lệnh vi phạm: TP ({s_tp:.2f}) >= Entry ({s_entry:.2f}) cho SHORT",
                        violation_code="GEOMETRY_VIOLATION",
                        checks_failed=["SNAPSHOT_GEOMETRY"],
                        provenance_issues=raw_provenance,
                        decision_time_ms=decision_time,
                    )
                else:
                    passed.append("PRICE_GEOMETRY")
        except (ValueError, TypeError):
            missing.append("NUMERIC_GEOMETRY")

    # 3. Model requirements resolution (Section 053, 065 - 070, V16.39.5 Rule 086)
    model_id = str(
        entry_snapshot.get("model_id")
        or entry_snapshot.get("family")
        or entry_snapshot.get("strategy_family")
        or (getattr(order, "strategy_family", None) if order else None)
        or "STANDARD_SMC"
    ).upper()
    entry_mode = (
        entry_snapshot.get("entry_mode")
        or (entry_snapshot.get("plan") or {}).get("entry_mode")
        or (entry_snapshot.get("entry_intent") or {}).get("entry_mode")
        or (getattr(order, "entry_mode", None) if order else None)
    )
    reqs = model_requirements or resolve_model_review_requirements(model_id, entry_mode=entry_mode)
    can_model = canonical_model_family(model_id)

    # 4. Extract and validate evidence nodes (Section 041 - 050, 059)
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

    valid_nodes_by_id: Dict[str, Any] = {}
    valid_nodes_by_kind: Dict[str, List[Any]] = {}
    observed_kinds: set = set()
    node_issues: List[str] = []

    for raw_node in nodes:
        if isinstance(raw_node, dict):
            nid = str(raw_node.get("id") or "")
            k = str(raw_node.get("kind") or "").upper().strip()
            sym = raw_node.get("symbol")
            dirn = raw_node.get("direction")
            occ = raw_node.get("occurred_at_ms")
            knw = raw_node.get("known_at_ms")
        elif hasattr(raw_node, "id"):
            nid = str(getattr(raw_node, "id") or "")
            k = str(getattr(raw_node, "kind") or "").upper().strip()
            sym = getattr(raw_node, "symbol", None)
            dirn = getattr(raw_node, "direction", None)
            occ = getattr(raw_node, "occurred_at_ms", None)
            knw = getattr(raw_node, "known_at_ms", None)
        else:
            continue

        if not nid or not k:
            node_issues.append("NODE_MISSING_ID_OR_KIND")
            continue

        # Validate timestamps (Section 052, 102, 156, 162, 163)
        if isinstance(knw, bool) or isinstance(occ, bool):
            node_issues.append(f"NODE_{nid}_INVALID_TIMESTAMP")
            continue
        if knw is None:
            node_issues.append(f"NODE_{nid}_MISSING_TIMESTAMP")
            continue
        if occ is None:
            occ = knw
        try:
            occ_int = int(occ)
            knw_int = int(knw)
        except (ValueError, TypeError):
            node_issues.append(f"NODE_{nid}_INVALID_TIMESTAMP")
            continue

        if occ_int <= 0 or knw_int <= 0:
            node_issues.append(f"NODE_{nid}_NONPOSITIVE_TIMESTAMP")
            continue

        if knw_int < occ_int:
            node_issues.append(f"NODE_{nid}_INVERTED_TIMELINE")
            continue

        # Causality check: must be known at or before decision time! (Section 043, 046, 106, 148, 164)
        if decision_time is None:
            node_issues.append(f"NODE_{nid}_MISSING_DECISION_CUTOFF")
            continue
        if knw_int > int(decision_time):
            node_issues.append(f"NODE_{nid}_FUTURE_TIMESTAMP")
            continue

        # Symbol check (Section 048, 149)
        if sym and ord_sym and str(sym).upper() != str(ord_sym).upper():
            node_issues.append(f"NODE_{nid}_SYMBOL_MISMATCH")
            continue

        # Direction check for direction-sensitive kinds (Section 049, 150)
        if dirn and s_dir_norm:
            node_dir_norm = "LONG" if str(dirn).upper() in ("LONG", "BUY", "BULLISH") else ("SHORT" if str(dirn).upper() in ("SHORT", "SELL", "BEARISH") else None)
            if k in ("BOS", "CHOCH", "MSS", "SWEEP", "PULLBACK", "SWEEP_LOW", "SWEEP_HIGH"):
                if node_dir_norm and node_dir_norm != s_dir_norm:
                    node_issues.append(f"NODE_{nid}_DIRECTION_MISMATCH")
                    continue

        # Duplicate ID check: dedup stable
        if nid in valid_nodes_by_id:
            continue

        valid_nodes_by_id[nid] = raw_node
        if k not in valid_nodes_by_kind:
            valid_nodes_by_kind[k] = []
        valid_nodes_by_kind[k].append(raw_node)
        observed_kinds.add(k)
        if k == "LIQUIDITY_SWEEP":
            observed_kinds.add("SWEEP")
        refs.append(nid)

    # 5. Check trigger chain (Section 055, 056, 057, 063, V16.39.5 Rule 121, 122)
    tc = entry_snapshot.get("trigger_chain")
    tc_verified = False
    requires_tc = reqs.get("requires_trigger_chain", True)

    if isinstance(tc, dict):
        if tc.get("verified") is True:  # STRICT True check
            tc_model = canonical_model_family(tc.get("model_id"))
            # Model match check (Section 029, 056, 154)
            if tc_model and can_model and tc_model != can_model:
                failed.append("TRIGGER_MODEL_MISMATCH")
                return EntryComplianceResult(
                    status="UNVERIFIABLE",
                    reason=f"Trigger chain model '{tc_model}' không khớp với model lệnh '{can_model}'.",
                    missing_evidence=["MODEL_TRIGGER_MATCH"],
                    checks_failed=failed,
                    provenance_issues=list(dict.fromkeys(raw_provenance + node_issues)),
                    decision_time_ms=decision_time,
                )

            tc_nodes = list(tc.get("node_ids") or [])
            if requires_tc and len(tc_nodes) == 0:
                failed.append("TRIGGER_CHAIN_EMPTY_REFS")
                tc_verified = False
            else:
                all_refs_valid = True
                for ref_id in tc_nodes:
                    ref_str = str(ref_id)
                    if ref_str not in valid_nodes_by_id:
                        all_refs_valid = False
                        failed.append(f"TRIGGER_REF_NOT_FOUND_{ref_str}")
                if all_refs_valid:
                    tc_verified = True
                    passed.append("TRIGGER_CHAIN_VERIFIED")
                else:
                    tc_verified = False
        else:
            failed.append("TRIGGER_CHAIN_UNVERIFIED")
    elif entry_snapshot.get("compliance_verified") is True or (isinstance(audit, dict) and audit.get("compliance_verified") is True):
        # Legacy claim: allowed ONLY if the model does NOT require a trigger chain (e.g. NY_QUOTA)
        if not requires_tc and observed_kinds:
            tc_verified = True
            passed.append("COMPLIANCE_FLAG_VERIFIED")
        else:
            failed.append("TRIGGER_CHAIN_UNVERIFIED")
    else:
        if requires_tc:
            failed.append("TRIGGER_CHAIN_UNVERIFIED")
        else:
            tc_verified = True

    # 6. Temporal sequence check (Section 059, 158, V16.39.5 Rule 126, 169)
    if can_model == "SWEEP_RECLAIM":
        sweeps = valid_nodes_by_kind.get("SWEEP", [])
        reclaims = valid_nodes_by_kind.get("RECLAIM", [])
        if sweeps and reclaims:
            earliest_sweep = min(s.get("occurred_at_ms", 0) if isinstance(s, dict) else getattr(s, "occurred_at_ms", 0) for s in sweeps)
            latest_reclaim = max(r.get("occurred_at_ms", 0) if isinstance(r, dict) else getattr(r, "occurred_at_ms", 0) for r in reclaims)
            if latest_reclaim < earliest_sweep:
                failed.append("INVALID_TEMPORAL_SEQUENCE_SWEEP_RECLAIM")

    if can_model == "FVG_CONTINUATION":
        fvgs = valid_nodes_by_kind.get("FVG", [])
        retests = valid_nodes_by_kind.get("RETEST", [])
        if fvgs and retests:
            fvg_known = min(f.get("known_at_ms", 0) if isinstance(f, dict) else getattr(f, "known_at_ms", 0) for f in fvgs)
            retest_occ = min(r.get("occurred_at_ms", 0) if isinstance(r, dict) else getattr(r, "occurred_at_ms", 0) for r in retests)
            if retest_occ < fvg_known:
                failed.append("INVALID_TEMPORAL_SEQUENCE_FVG_RETEST")

    if can_model == "CG01":
        sweeps = valid_nodes_by_kind.get("SWEEP", []) + valid_nodes_by_kind.get("LIQUIDITY_SWEEP", [])
        msses = valid_nodes_by_kind.get("MSS", [])
        retests = valid_nodes_by_kind.get("RETEST", [])

        # 1. Sweep before MSS
        if sweeps and msses:
            earliest_sweep = min(s.get("occurred_at_ms", 0) if isinstance(s, dict) else getattr(s, "occurred_at_ms", 0) for s in sweeps)
            earliest_mss = min(m.get("occurred_at_ms", 0) if isinstance(m, dict) else getattr(m, "occurred_at_ms", 0) for m in msses)
            if earliest_mss < earliest_sweep:
                failed.append("INVALID_TEMPORAL_SEQUENCE_CG01_SWEEP_MSS")

        # 2. MSS before Retest (for MARKET retest mode)
        if msses and retests:
            earliest_mss = min(m.get("occurred_at_ms", 0) if isinstance(m, dict) else getattr(m, "occurred_at_ms", 0) for m in msses)
            earliest_retest = min(r.get("occurred_at_ms", 0) if isinstance(r, dict) else getattr(r, "occurred_at_ms", 0) for r in retests)
            if earliest_retest < earliest_mss:
                failed.append("INVALID_TEMPORAL_SEQUENCE_CG01_MSS_RETEST")

    # 7. Verify required kinds for the specific model (Section 061 - 071)
    req_kinds = reqs.get("required_kinds", [])
    missing_req: List[str] = []

    for rk in req_kinds:
        if rk == "SWEEP":
            if not any(k in observed_kinds for k in ("SWEEP", "SWEEP_LOW", "SWEEP_HIGH")):
                missing_req.append("REQUIRED_NODE_SWEEP")
        elif rk in ("SWEEP_LOW", "SWEEP_HIGH"):
            if rk not in observed_kinds and "SWEEP" not in observed_kinds:
                missing_req.append(f"REQUIRED_NODE_{rk}")
        elif rk in ("CHOCH", "MSS"):
            if not any(k in observed_kinds for k in ("CHOCH", "MSS", "BREAK")):
                missing_req.append(f"REQUIRED_NODE_{rk}")
        elif rk not in observed_kinds:
            missing_req.append(f"REQUIRED_NODE_{rk}")

    if requires_tc and not tc_verified:
        missing_req.append("TRIGGER_CHAIN_UNVERIFIED")

    has_ref_failures = any("TRIGGER_REF_NOT_FOUND" in f for f in failed)
    has_empty_chain = "TRIGGER_CHAIN_EMPTY_REFS" in failed
    has_seq_failures = any("INVALID_TEMPORAL_SEQUENCE" in f for f in failed)

    all_issues = list(dict.fromkeys(raw_provenance + node_issues))

    if "DECISION_CUTOFF" in missing or missing_req or "NUMERIC_GEOMETRY" in missing or "PLANNED_TP" in missing or has_ref_failures or has_empty_chain or has_seq_failures:
        missing.extend(missing_req)
        if "DECISION_CUTOFF" in missing:
            fail_reason = "Thiếu mốc thời gian quyết định (decision cutoff) để xác minh tính nhân quả của bằng chứng."
        else:
            fail_reason = f"Thiếu hoặc không xác minh được bằng chứng bắt buộc cho mô hình {model_id}: {', '.join(missing_req or failed or missing)}."
        return EntryComplianceResult(
            status="UNVERIFIABLE",
            reason=fail_reason,
            missing_evidence=list(dict.fromkeys(missing)),
            checks_passed=passed,
            checks_failed=list(dict.fromkeys(failed + missing_req)),
            evidence_refs=refs,
            provenance_issues=all_issues,
            decision_time_ms=decision_time,
        )

    passed.append("MODEL_REQUIREMENTS_VERIFIED")
    return EntryComplianceResult(
        status="VERIFIED_COMPLIANT",
        reason=f"Entry snapshot và audit tuân thủ mô hình {model_id} đã xác minh đầy đủ.",
        checks_passed=passed or ["COMPLIANCE_VERIFIED"],
        evidence_refs=refs,
        provenance_issues=all_issues,
        decision_time_ms=decision_time,
    )


def classify_post_trade_lesson(
    review: PostTradeReview,
    entry_snapshot: Optional[Dict[str, Any]] = None,
    audit: Optional[Any] = None,
    compliance_result: Optional[EntryComplianceResult] = None,
) -> LessonClassification:
    """
    V16.39.1 Sections 015, 096 - 110, V16.39.3 Section 077:
    Pure deterministic classifier for post-trade reviews based on empirical evidence.
    Accepts pre-computed EntryComplianceResult to guarantee single assessment.
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
        if review.planned_entry is not None and review.planned_sl is not None and review.planned_entry <= review.planned_sl:
            is_wrong_geometry = True
        elif review.planned_tp is not None and review.planned_entry is not None and review.planned_tp > 0 and review.planned_tp <= review.planned_entry:
            is_wrong_geometry = True
    elif review.direction in ("SHORT", "SELL"):
        if review.planned_entry is not None and review.planned_sl is not None and review.planned_entry >= review.planned_sl:
            is_wrong_geometry = True
        elif review.planned_tp is not None and review.planned_entry is not None and review.planned_tp > 0 and review.planned_tp >= review.planned_entry:
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

    # 3. Assess compliance from entry_snapshot and audit (Section 077, 097)
    if compliance_result is not None:
        comp = compliance_result
    else:
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
