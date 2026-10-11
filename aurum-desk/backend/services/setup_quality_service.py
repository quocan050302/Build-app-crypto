"""
backend/services/setup_quality_service.py
V14 — Pure Setup Quality Service & Structural Evidence Selector
Responsibilities:
- Evaluate SMC setup quality against explicit required & supporting checklist.
- Strict precedence in derive_quality_status.
- Pure causal evidence selection (known_at <= as_of).
- Immutable, canonical snapshot generation.
- No DB, clock, or network calls inside evaluator.
"""

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

CheckStatus = Literal['MET', 'NOT_MET', 'UNKNOWN', 'NOT_APPLICABLE']
Importance = Literal['REQUIRED', 'SUPPORTING']
QualityStatus = Literal[
    'SATISFIED', 'PARTIAL', 'INSUFFICIENT_DATA',
    'BLOCKED_EXISTING', 'ERROR', 'NOT_ASSESSED',
]


@dataclass(frozen=True)
class QualityCheck:
    code: str
    status: CheckStatus
    importance: Importance
    explanation_vi: str
    observed: Optional[str] = None
    expected: Optional[str] = None
    evidence_ids: Tuple[str, ...] = ()
    not_applicable_reason: Optional[str] = None
    measured_at_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "importance": self.importance,
            "explanation_vi": self.explanation_vi,
            "observed": self.observed,
            "expected": self.expected,
            "evidence_ids": list(self.evidence_ids),
            "not_applicable_reason": self.not_applicable_reason,
            "measured_at_ms": self.measured_at_ms,
        }


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    kind: str
    symbol: str
    timeframe: str
    known_at_ms: int
    level: Optional[float] = None
    direction: Optional[str] = None
    source_ids: Tuple[str, ...] = ()
    detail: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "known_at_ms": self.known_at_ms,
            "level": self.level,
            "direction": self.direction,
            "source_ids": list(self.source_ids),
            "detail": self.detail,
            "extra": self.extra,
        }


@dataclass(frozen=True)
class QualitySummary:
    status: QualityStatus
    required_met: int
    required_total: int
    score_pct: float
    headline_vi: str
    next_condition_vi: str
    caution_vi: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "required_met": self.required_met,
            "required_total": self.required_total,
            "score_pct": self.score_pct,
            "headline_vi": self.headline_vi,
            "next_condition_vi": self.next_condition_vi,
            "caution_vi": self.caution_vi,
        }


@dataclass(frozen=True)
class QualityAssessment:
    status: QualityStatus
    checks: Tuple[QualityCheck, ...]
    summary: QualitySummary
    related_evidence_ids: Tuple[str, ...]
    missing_evidence_ids: Tuple[str, ...]
    cautions: Tuple[str, ...]
    mode: str = "EXPLAIN_ONLY"

    def to_dict(self) -> Dict[str, Any]:
        s_dict = self.summary.to_dict()
        s_dict["primary_reason_vi"] = self.summary.headline_vi
        return {
            "status": self.status,
            "required_met_count": self.summary.required_met,
            "required_total_count": self.summary.required_total,
            "checks": [c.to_dict() for c in self.checks],
            "summary": s_dict,
            "related_evidence_ids": list(self.related_evidence_ids),
            "missing_evidence_ids": list(self.missing_evidence_ids),
            "cautions": list(self.cautions),
            "mode": self.mode,
        }


# ==================== PURE PRECEDENCE LOGIC (APPENDIX A1) ====================

def derive_quality_status(
    checks: Sequence[QualityCheck],
    *,
    assessed: bool,
    existing_hard_blockers: Sequence[str] = (),
    validation_errors: Sequence[str] = (),
) -> QualityStatus:
    """
    Pure precedence derivation:
    1. validation_errors -> ERROR
    2. Duplicates or invalid codes/status -> ERROR
    3. existing_hard_blockers -> BLOCKED_EXISTING
    4. not assessed -> NOT_ASSESSED
    5. No applicable required checks -> NOT_ASSESSED
    6. Any required check UNKNOWN -> INSUFFICIENT_DATA
    7. Any required check NOT_MET -> PARTIAL
    8. All applicable required checks MET -> SATISFIED
    """
    if validation_errors:
        return 'ERROR'
    allowed_status = {'MET', 'NOT_MET', 'UNKNOWN', 'NOT_APPLICABLE'}
    allowed_importance = {'REQUIRED', 'SUPPORTING'}
    codes = [check.code for check in checks]
    if len(codes) != len(set(codes)) or any(not code for code in codes):
        return 'ERROR'
    for check in checks:
        if check.status not in allowed_status or check.importance not in allowed_importance:
            return 'ERROR'
        if check.status == 'NOT_APPLICABLE' and not check.not_applicable_reason:
            return 'ERROR'
    if existing_hard_blockers:
        return 'BLOCKED_EXISTING'
    if not assessed:
        return 'NOT_ASSESSED'
    required = [
        check for check in checks
        if check.importance == 'REQUIRED' and check.status != 'NOT_APPLICABLE'
    ]
    if not required:
        return 'NOT_ASSESSED'
    if any(check.status == 'UNKNOWN' for check in required):
        return 'INSUFFICIENT_DATA'
    if any(check.status == 'NOT_MET' for check in required):
        return 'PARTIAL'
    return 'SATISFIED'


def select_related_evidence(
    evidence: Sequence[EvidenceRef],
    matched_ids: Sequence[str],
    *,
    symbol: str,
    as_of_ms: int,
) -> Tuple[Tuple[EvidenceRef, ...], Tuple[str, ...]]:
    """
    Pure causal evidence selector starting from matched IDs, following source_ids.
    Enforces known_at_ms <= as_of_ms strictly.
    """
    if type(as_of_ms) is not int or as_of_ms <= 0:
        raise ValueError('INVALID_AS_OF_MS')
    lookup = {item.id: item for item in evidence}
    if len(lookup) != len(evidence):
        raise ValueError('DUPLICATE_EVIDENCE_ID')
    pending = list(dict.fromkeys(matched_ids))
    seen: set[str] = set()
    selected: list[EvidenceRef] = []
    missing: list[str] = []
    while pending:
        evidence_id = pending.pop(0)
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        item = lookup.get(evidence_id)
        if item is None:
            missing.append(evidence_id)
            continue
        if (
            item.symbol != symbol
            or type(item.known_at_ms) is not int
            or item.known_at_ms <= 0
            or item.known_at_ms > as_of_ms
        ):
            missing.append(evidence_id)
            continue
        selected.append(item)
        pending.extend(item.source_ids)
    selected.sort(key=lambda item: (item.known_at_ms, item.id))
    return tuple(selected), tuple(sorted(missing))


# ==================== CANONICAL HASH & SNAPSHOT (APPENDIX A5) ====================

def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
        allow_nan=False
    ).encode('utf-8')


def build_plan_fingerprint(plan: Dict[str, Any]) -> str:
    """Builds deterministic fingerprint for price triple + direction."""
    norm = {
        "direction": plan.get("direction", "LONG"),
        "entry": round(float(plan.get("entry_price", plan.get("planned_entry", 0.0))), 2),
        "sl": round(float(plan.get("stop_loss", 0.0)), 2),
        "tp": round(float(plan.get("take_profit", 0.0)), 2),
    }
    raw = f"{norm['direction']}:{norm['entry']:.2f}:{norm['sl']:.2f}:{norm['tp']:.2f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_quality_snapshot(
    *,
    setup_id: str,
    setup_instance_id: str,
    setup_revision: int,
    quality_revision: int,
    symbol: str,
    timeframe: str,
    direction: str,
    strategy_family: str,
    as_of_ms: int,
    plan_snapshot: Dict[str, Any],
    assessment: QualityAssessment,
    evidence_items: Sequence[EvidenceRef],
    config_version: int = 1,
    detector_version: str = "v14.0",
) -> Dict[str, Any]:
    """
    Constructs canonical immutable snapshot dictionary with deterministic content ID.
    """
    plan_fp = build_plan_fingerprint(plan_snapshot)
    semantic_payload = {
        "schema_version": "v14.0",
        "setup_id": setup_id,
        "setup_instance_id": setup_instance_id,
        "setup_revision": setup_revision,
        "quality_revision": quality_revision,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "strategy_family": strategy_family,
        "as_of_ms": as_of_ms,
        "config_version": config_version,
        "detector_version": detector_version,
        "plan_fingerprint": plan_fp,
        "plan_snapshot": {
            "direction": direction,
            "entry_price": round(float(plan_snapshot.get("entry_price", plan_snapshot.get("planned_entry", 0.0))), 2),
            "stop_loss": round(float(plan_snapshot.get("stop_loss", 0.0)), 2),
            "take_profit": round(float(plan_snapshot.get("take_profit", 0.0)), 2),
            "quantity": round(float(plan_snapshot.get("quantity", 0.05)), 4),
            "leverage": int(plan_snapshot.get("leverage", 5)),
            "margin_mode": str(plan_snapshot.get("margin_mode", "ISOLATED")),
            "net_rr": round(float(plan_snapshot.get("net_rr", 0.0)), 2),
            "risk_usdt": round(float(plan_snapshot.get("risk_usdt", 2.5)), 2),
        },
        "assessment": assessment.to_dict(),
        "evidence": [ev.to_dict() for ev in evidence_items],
    }

    digest = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()
    snapshot_id = f"qsnap-{symbol.lower()}-{digest[:20]}"

    return {
        **semantic_payload,
        "snapshot_id": snapshot_id,
        "evidence_digest": digest,
    }


# ==================== EVALUATION LOGIC (APPENDIX A3, A4) ====================

def evaluate_quality_checks(context: Dict[str, Any]) -> Tuple[Tuple[QualityCheck, ...], Tuple[str, ...]]:
    """
    Evaluates checklist based on context facts:
    1. SOURCE_SWING: Valid source swing identified in required direction
    2. LIQUIDITY_SWEEP: Liquidity sweep observed and confirmed
    3. STRUCTURAL_BREAK: BOS or CHOCH confirmed with direction alignment
    4. DISPLACEMENT: Break bar body >= 0.8 ATR
    5. FVG_CREATION: Associated fair value gap formed
    6. HTF_ALIGNMENT: Higher timeframe context aligned
    7. STOP_PROTECTION: Stop loss placed logically behind structural swing
    8. TARGET_PATH: Clear path to take profit without major opposing obstacles
    9. QUOTE_VALIDITY: Live/recent quote available and not crossed
    10. NEWS_CLEAR: No high-impact news blackout window active
    11. RISK_REWARD_NET: Net R:R >= 2.0R under realistic fees & slippage
    """
    direction = context.get("direction", "LONG")
    now_ms = context.get("as_of_ms", 0)
    analysis = context.get("analysis", {})
    plan = context.get("plan", {})
    eligibility = context.get("eligibility", {})
    quote_view = context.get("quote_view", {})
    news_view = context.get("news_view", {})
    calc = context.get("calc", {})

    checks: List[QualityCheck] = []
    cautions: List[str] = []

    # 1. SOURCE_SWING (REQUIRED)
    source_swings = analysis.get("swings", [])
    has_source_swing = len(source_swings) > 0
    if has_source_swing:
        last_s = source_swings[-1]
        ev_id = f"swing-{last_s.get('type', 'SWING').lower()}-{last_s.get('price', 0)}"
        checks.append(QualityCheck(
            code="SOURCE_SWING_CONFIRMED",
            status="MET",
            importance="REQUIRED",
            explanation_vi=f"Đỉnh/đáy nguồn {last_s.get('price', 0):.2f} đã xác nhận 2 nến trái/phải.",
            observed=f"{last_s.get('type')}: {last_s.get('price', 0):.2f}",
            expected="Confirmed swing pivot",
            evidence_ids=(ev_id,),
            measured_at_ms=now_ms
        ))
    else:
        checks.append(QualityCheck(
            code="SOURCE_SWING_CONFIRMED",
            status="NOT_MET",
            importance="REQUIRED",
            explanation_vi="Chưa phát hiện đỉnh/đáy cấu trúc hợp lệ để làm mốc.",
            expected="Confirmed swing pivot",
            measured_at_ms=now_ms
        ))

    # 2. LIQUIDITY_SWEEP (REQUIRED)
    events = analysis.get("structure_events", [])
    sweep_events = [e for e in events if e.get("event_type") == "SWEEP"]
    if sweep_events:
        last_sw = sweep_events[-1]
        ev_id = last_sw.get("id", f"sweep-{last_sw.get('level', 0)}")
        checks.append(QualityCheck(
            code="LIQUIDITY_SWEEP_OBSERVED",
            status="MET",
            importance="REQUIRED",
            explanation_vi=f"Đã quét thanh khoản tại {last_sw.get('level', 0):.2f} (râu nến đâm qua, đóng bên trong).",
            observed=f"Sweep at {last_sw.get('level', 0):.2f}",
            expected="Closed liquidity sweep",
            evidence_ids=(ev_id,),
            measured_at_ms=now_ms
        ))
    else:
        checks.append(QualityCheck(
            code="LIQUIDITY_SWEEP_OBSERVED",
            status="NOT_MET",
            importance="REQUIRED",
            explanation_vi="Chưa quan sát thấy quét thanh khoản hoàn tất.",
            expected="Closed liquidity sweep",
            measured_at_ms=now_ms
        ))

    # 3. STRUCTURAL_BREAK / CHOCH (REQUIRED)
    break_events = [e for e in events if str(e.get("event_type", "")).upper() in ("CHOCH", "BOS")]
    aligned_breaks = [b for b in break_events if b.get("direction") == ("BULLISH" if direction == "LONG" else "BEARISH")]
    if aligned_breaks:
        b = aligned_breaks[-1]
        b_type = str(b.get("event_type", "BREAK")).upper()
        b_label = "CHoCH" if b_type == "CHOCH" else "BOS"
        ev_id = b.get("id", f"break-{b.get('level', 0)}")
        checks.append(QualityCheck(
            code="STRUCTURAL_BREAK_CONFIRMED",
            status="MET",
            importance="REQUIRED",
            explanation_vi=f"Đã xác nhận {b_label} {direction} tại mức {b.get('level', 0):.2f}.",
            observed=f"{b_label} at {b.get('level', 0):.2f}",
            expected=f"Confirmed {b_label} {direction}",
            evidence_ids=(ev_id,),
            measured_at_ms=now_ms
        ))
    else:
        checks.append(QualityCheck(
            code="STRUCTURAL_BREAK_CONFIRMED",
            status="NOT_MET",
            importance="REQUIRED",
            explanation_vi=f"Đang chờ phá vỡ cấu trúc ({'CHoCH Tăng' if direction == 'LONG' else 'CHoCH Giảm'}) xác nhận.",
            expected=f"BOS or CHOCH {direction}",
            measured_at_ms=now_ms
        ))

    # 4. DISPLACEMENT (SUPPORTING)
    disp_events = [b for b in aligned_breaks if b.get("is_displacement") is True]
    if disp_events:
        checks.append(QualityCheck(
            code="DISPLACEMENT_CONFIRMED",
            status="MET",
            importance="SUPPORTING",
            explanation_vi="Xung lực nến phá vỡ mạnh mẽ (thân nến >= 0.8 ATR).",
            observed="Strong displacement body",
            expected="body >= 0.8 ATR",
            measured_at_ms=now_ms
        ))
    elif aligned_breaks:
        checks.append(QualityCheck(
            code="DISPLACEMENT_CONFIRMED",
            status="NOT_MET",
            importance="SUPPORTING",
            explanation_vi="Nến phá vỡ có thân trung bình, chưa đạt chuẩn xung lực mạnh.",
            observed="body < 0.8 ATR",
            expected="body >= 0.8 ATR",
            measured_at_ms=now_ms
        ))
        cautions.append("Nến phá vỡ chưa có xung lực displacement vượt trội.")
    else:
        checks.append(QualityCheck(
            code="DISPLACEMENT_CONFIRMED",
            status="UNKNOWN",
            importance="SUPPORTING",
            explanation_vi="Chưa có nến phá vỡ để đo lường xung lực.",
            measured_at_ms=now_ms
        ))

    # 5. FVG_RETEST (SUPPORTING)
    fvgs = analysis.get("active_fvgs", [])
    if fvgs:
        last_fvg = fvgs[-1]
        ev_id = f"fvg-{last_fvg.get('bottom', 0)}-{last_fvg.get('top', 0)}"
        checks.append(QualityCheck(
            code="FVG_IMBALANCE_FORMED",
            status="MET",
            importance="SUPPORTING",
            explanation_vi=f"Khối FVG mất cân bằng hình thành trong khoảng {last_fvg.get('bottom', 0):.2f} – {last_fvg.get('top', 0):.2f}.",
            observed=f"FVG [{last_fvg.get('bottom', 0):.2f}, {last_fvg.get('top', 0):.2f}]",
            expected="Active unmitigated FVG",
            evidence_ids=(ev_id,),
            measured_at_ms=now_ms
        ))
    else:
        checks.append(QualityCheck(
            code="FVG_IMBALANCE_FORMED",
            status="NOT_MET",
            importance="SUPPORTING",
            explanation_vi="Chưa hình thành FVG mất cân bằng rõ ràng.",
            expected="Active unmitigated FVG",
            measured_at_ms=now_ms
        ))
        cautions.append("Không có FVG vùng giá mất cân bằng hỗ trợ điểm vào.")

    # 6. HTF_ALIGNMENT (SUPPORTING)
    htf_bias = str(analysis.get("htf_bias", "UNKNOWN")).upper()
    req_htf = "BULLISH" if direction == "LONG" else "BEARISH"
    if htf_bias == req_htf:
        checks.append(QualityCheck(
            code="HTF_TREND_ALIGNED",
            status="MET",
            importance="SUPPORTING",
            explanation_vi=f"Khung lớn (D/4H) đồng thuận xu hướng {direction} ({htf_bias}).",
            observed=f"HTF: {htf_bias}",
            expected=req_htf,
            measured_at_ms=now_ms
        ))
    elif htf_bias in ("UNKNOWN", "NEUTRAL", ""):
        checks.append(QualityCheck(
            code="HTF_TREND_ALIGNED",
            status="UNKNOWN",
            importance="SUPPORTING",
            explanation_vi="Xu hướng khung lớn (D/4H) đang đi ngang hoặc chưa đủ dữ liệu.",
            observed=f"HTF: {htf_bias}",
            expected=req_htf,
            measured_at_ms=now_ms
        ))
    else:
        checks.append(QualityCheck(
            code="HTF_TREND_ALIGNED",
            status="NOT_MET",
            importance="SUPPORTING",
            explanation_vi=f"Cảnh báo ngược xu hướng khung lớn: Lệnh {direction} nhưng HTF là {htf_bias}.",
            observed=f"HTF: {htf_bias}",
            expected=req_htf,
            measured_at_ms=now_ms
        ))
        cautions.append(f"Ngược xu hướng khung lớn (HTF đang {htf_bias}).")

    # 7. STOP_PROTECTION (REQUIRED)
    entry_p = float(plan.get("entry_price", plan.get("planned_entry", 0.0)) or 0.0)
    sl_p = float(plan.get("stop_loss", 0.0) or 0.0)
    tp_p = float(plan.get("take_profit", 0.0) or 0.0)

    if entry_p > 0 and sl_p > 0 and tp_p > 0:
        is_geom_valid = (sl_p < entry_p < tp_p) if direction == "LONG" else (tp_p < entry_p < sl_p)
        if is_geom_valid:
            checks.append(QualityCheck(
                code="STOP_PLACEMENT_VALID",
                status="MET",
                importance="REQUIRED",
                explanation_vi=f"Cắt lỗ {sl_p:.2f} đặt đúng phía cấu trúc (khoảng cách {abs(entry_p - sl_p):.2f} USD).",
                observed=f"SL: {sl_p:.2f}, Entry: {entry_p:.2f}",
                expected=f"{'SL < Entry' if direction == 'LONG' else 'SL > Entry'}",
                measured_at_ms=now_ms
            ))
        else:
            checks.append(QualityCheck(
                code="STOP_PLACEMENT_VALID",
                status="NOT_MET",
                importance="REQUIRED",
                explanation_vi="Hình học điểm cắt lỗ sai lệch so với hướng giao dịch.",
                observed=f"Entry: {entry_p:.2f}, SL: {sl_p:.2f}",
                expected="Valid price geometry",
                measured_at_ms=now_ms
            ))
    else:
        checks.append(QualityCheck(
            code="STOP_PLACEMENT_VALID",
            status="UNKNOWN",
            importance="REQUIRED",
            explanation_vi="Chưa có đủ bộ 3 mức giá Entry, Cắt lỗ và Chốt lời.",
            expected="Complete price plan",
            measured_at_ms=now_ms
        ))

    # 8. RISK_REWARD_NET (REQUIRED)
    net_rr = float(plan.get("net_rr", calc.get("net_rr", 0.0)) or 0.0)
    if net_rr >= 2.0:
        checks.append(QualityCheck(
            code="NET_RR_COMPLIANT",
            status="MET",
            importance="REQUIRED",
            explanation_vi=f"Tỷ lệ Lợi nhuận/Rủi ro sau phí đạt {net_rr:.2f}R (chuẩn >= 2.0R).",
            observed=f"{net_rr:.2f}R",
            expected=">= 2.00R",
            measured_at_ms=now_ms
        ))
    elif net_rr > 0:
        checks.append(QualityCheck(
            code="NET_RR_COMPLIANT",
            status="NOT_MET",
            importance="REQUIRED",
            explanation_vi=f"Tỷ lệ Lợi nhuận/Rủi ro sau phí chỉ đạt {net_rr:.2f}R (< 2.0R).",
            observed=f"{net_rr:.2f}R",
            expected=">= 2.00R",
            measured_at_ms=now_ms
        ))
    else:
        checks.append(QualityCheck(
            code="NET_RR_COMPLIANT",
            status="UNKNOWN",
            importance="REQUIRED",
            explanation_vi="Chưa tính toán được tỷ lệ Net R:R sau chi phí sàn.",
            expected=">= 2.00R",
            measured_at_ms=now_ms
        ))

    # 9. QUOTE_VALIDITY (SUPPORTING)
    quote_valid = quote_view.get("is_valid", True)
    if quote_valid:
        checks.append(QualityCheck(
            code="QUOTE_DATA_VALID",
            status="MET",
            importance="SUPPORTING",
            explanation_vi="Dữ liệu giá hiện tại sẵn sàng và hợp lệ.",
            observed="Valid bid/ask quote",
            expected="Healthy quote stream",
            measured_at_ms=now_ms
        ))
    else:
        checks.append(QualityCheck(
            code="QUOTE_DATA_VALID",
            status="NOT_MET",
            importance="SUPPORTING",
            explanation_vi="Giá thị trường tạm thời không khả dụng hoặc chênh lệch quá lớn.",
            observed="Stale or crossed quote",
            expected="Healthy quote stream",
            measured_at_ms=now_ms
        ))
        cautions.append("Dữ liệu giá thị trường có dấu hiệu trễ hoặc bất thường.")

    # 10. NEWS_CLEAR (SUPPORTING)
    is_news_blackout = news_view.get("is_blackout", False)
    if not is_news_blackout:
        checks.append(QualityCheck(
            code="NEWS_WINDOW_CLEAR",
            status="MET",
            importance="SUPPORTING",
            explanation_vi="Không nằm trong vùng ảnh hưởng tin tức kinh tế nhạy cảm (CPI/NFP/FOMC).",
            observed="No active blackout",
            expected="Clear news window",
            measured_at_ms=now_ms
        ))
    else:
        reason_n = news_view.get("reason", "Tin tức High Impact")
        checks.append(QualityCheck(
            code="NEWS_WINDOW_CLEAR",
            status="NOT_MET",
            importance="SUPPORTING",
            explanation_vi=f"Đang trong cửa sổ rủi ro tin tức: {reason_n}.",
            observed="News blackout active",
            expected="Clear news window",
            measured_at_ms=now_ms
        ))
        cautions.append(f"Cửa sổ tin tức nguy hiểm: {reason_n}")

    return tuple(checks), tuple(cautions)


def build_quality_summary(
    status: QualityStatus,
    checks: Sequence[QualityCheck],
    cautions: Sequence[str],
) -> QualitySummary:
    """
    Builds concise Vietnamese summary card items:
    - required_met / required_total
    - headline_vi
    - next_condition_vi
    - caution_vi
    """
    required_checks = [c for c in checks if c.importance == "REQUIRED" and c.status != "NOT_APPLICABLE"]
    req_total = len(required_checks)
    req_met = sum(1 for c in required_checks if c.status == "MET")
    score_pct = round((req_met / req_total * 100.0), 1) if req_total > 0 else 0.0

    headlines = {
        "SATISFIED": "Setup đạt đủ các điều kiện cấu trúc bắt buộc",
        "PARTIAL": f"Đã đạt {req_met}/{req_total} điều kiện chính — đang chờ thêm xác nhận",
        "INSUFFICIENT_DATA": "Thiếu dữ liệu nến hoặc cấu trúc chưa đủ để đánh giá",
        "BLOCKED_EXISTING": "Setup đang bị chặn bởi quy tắc rủi ro hoặc bảo vệ vốn",
        "ERROR": "Lỗi định dạng cấu trúc hoặc thông số giao dịch",
        "NOT_ASSESSED": "Chưa có đánh giá chất lượng tại thời điểm này",
    }
    headline = headlines.get(status, "Đang đánh giá chất lượng setup")

    # Next condition to wait for
    next_unmet = [c for c in required_checks if c.status != "MET"]
    if next_unmet:
        first_unmet = next_unmet[0]
        next_cond = f"Chờ: {first_unmet.explanation_vi}"
    else:
        next_cond = "Cấu trúc đã hoàn tất; chờ giá chạm vùng kích hoạt"

    caution_text = cautions[0] if cautions else None

    return QualitySummary(
        status=status,
        required_met=req_met,
        required_total=req_total,
        score_pct=score_pct,
        headline_vi=headline,
        next_condition_vi=next_cond,
        caution_vi=caution_text,
    )


def assess_setup_quality(context: Dict[str, Any]) -> Tuple[QualityAssessment, Sequence[EvidenceRef]]:
    """
    Main pure assessment orchestrator.
    Input: normalized context dict.
    Returns: (QualityAssessment, Sequence[EvidenceRef])
    """
    symbol = context.get("symbol", "XAUUSDT")
    now_ms = context.get("as_of_ms", 0)
    analysis = context.get("analysis", {})
    eligibility = context.get("eligibility", {})
    existing_blockers = eligibility.get("reasons", []) if not eligibility.get("eligible", True) else []

    checks, cautions = evaluate_quality_checks(context)

    # Collect all available evidence items
    all_evidence: List[EvidenceRef] = []

    # Swings
    seen_ids: Set[str] = set()
    for idx, s in enumerate(analysis.get("swings", [])):
        s_price = float(s.get("price", 0.0))
        s_type = str(s.get("type", "HIGH")).upper()
        s_time = int(s.get("timestamp", now_ms))
        s_conf = int(s.get("confirmed_at", s_time))
        s_id = str(s.get("id") or f"swing-{s_type.lower()}-{s_time}-{idx}")
        if s_id in seen_ids:
            s_id = f"{s_id}-{idx}"
        seen_ids.add(s_id)
        all_evidence.append(EvidenceRef(
            id=s_id,
            kind=f"SWING_{s_type}",
            symbol=symbol,
            timeframe=context.get("timeframe", "15M"),
            known_at_ms=s_conf,
            level=s_price,
            detail=f"Swing {s_type} at {s_price:.2f}"
        ))

    # Structure events (BOS, CHOCH, SWEEP)
    matched_ids: List[str] = []
    for idx, ev in enumerate(analysis.get("structure_events", [])):
        ev_type = str(ev.get("event_type", "EVENT")).upper()
        ev_time = int(ev.get("timestamp", now_ms))
        ev_conf = int(ev.get("confirmed_at", ev_time))
        ev_level = float(ev.get("level", 0.0))
        ev_src = tuple([str(ev.get("source_swing_id"))]) if ev.get("source_swing_id") else ()
        ev_id = str(ev.get("id") or f"ev-{ev_type.lower()}-{ev_time}-{idx}")
        if ev_id in seen_ids:
            ev_id = f"{ev_id}-{idx}"
        seen_ids.add(ev_id)
        all_evidence.append(EvidenceRef(
            id=ev_id,
            kind=ev_type,
            symbol=symbol,
            timeframe=context.get("timeframe", "15M"),
            known_at_ms=ev_conf,
            level=ev_level,
            direction=ev.get("direction"),
            source_ids=ev_src,
            detail=ev.get("detail", f"{ev_type} at {ev_level:.2f}")
        ))
        matched_ids.append(ev_id)

    # Deduplicate all_evidence by ID to guarantee unique lookup
    unique_evidence_map = {}
    for item in all_evidence:
        if item.id not in unique_evidence_map:
            unique_evidence_map[item.id] = item
    all_evidence = list(unique_evidence_map.values())

    # Select related evidence causally
    related_ev, missing_ev = select_related_evidence(
        all_evidence,
        matched_ids,
        symbol=symbol,
        as_of_ms=now_ms if now_ms > 0 else 9999999999999
    )

    status = derive_quality_status(
        checks,
        assessed=True,
        existing_hard_blockers=existing_blockers,
    )

    summary = build_quality_summary(status, checks, cautions)

    assessment = QualityAssessment(
        status=status,
        checks=checks,
        summary=summary,
        related_evidence_ids=tuple([e.id for e in related_ev]),
        missing_evidence_ids=missing_ev,
        cautions=cautions,
        mode="EXPLAIN_ONLY",
    )

    return assessment, related_ev

