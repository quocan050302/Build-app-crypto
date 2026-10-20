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


# ==============================================================================
# V16.39 PURE CANDIDATE QUALITY SCORING ENGINE (SECTIONS 031 - 065)
# ==============================================================================

@dataclass(frozen=True)
class QualityScoreFactor:
    """
    V16.39 Section 037: Structured Factor contract for explainable quality scoring.
    Every factor has verifiable evidence, measurement, and earned vs max points.
    """
    code: str
    group: str  # 'HTF_CONTEXT', 'POI_QUALITY', 'LIQUIDITY_SCENARIO', 'LTF_CONFIRMATION', 'EXECUTION_QUALITY'
    earned_points: float
    max_points: float
    status: str  # 'FULL', 'PARTIAL', 'ZERO', 'UNKNOWN', 'NOT_APPLICABLE'
    explanation_vi: str
    evidence_ids: Tuple[str, ...] = ()
    known_at_ms: Optional[int] = None
    measurement: Optional[Dict[str, Any]] = None
    rule_version: str = "v16.39"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "group": self.group,
            "earned_points": round(self.earned_points, 2),
            "max_points": round(self.max_points, 2),
            "status": self.status,
            "explanation_vi": self.explanation_vi,
            "evidence_ids": list(self.evidence_ids),
            "known_at_ms": self.known_at_ms,
            "measurement": self.measurement,
            "rule_version": self.rule_version,
        }


@dataclass(frozen=True)
class CandidateQualityAssessment:
    """
    V16.39 Section 036: Complete immutable snapshot of candidate quality score.
    Deterministic, JSON-serializable, with factor breakdown and governance traceability.
    """
    assessment_id: str
    candidate_key: str
    model_id: str
    base_score: float
    lesson_delta: float
    final_score: float
    threshold: float
    decision: str  # ELIGIBLE_BY_SCORE, BELOW_THRESHOLD, BLOCKED_REQUIRED, BLOCKED_GUARD, INSUFFICIENT_DATA, ERROR
    as_of_ms: int
    profile_version: str
    config_fingerprint: str
    factors: Tuple[QualityScoreFactor, ...]
    missing_data: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "candidate_key": self.candidate_key,
            "model_id": self.model_id,
            "base_score": round(self.base_score, 2),
            "lesson_delta": round(self.lesson_delta, 2),
            "final_score": round(self.final_score, 2),
            "threshold": round(self.threshold, 2),
            "decision": self.decision,
            "as_of_ms": self.as_of_ms,
            "profile_version": self.profile_version,
            "config_fingerprint": self.config_fingerprint,
            "factors": [f.to_dict() for f in self.factors],
            "missing_data": list(self.missing_data),
            "blockers": list(self.blockers),
            "group_scores": self.group_scores(),
        }

    def group_scores(self) -> Dict[str, Dict[str, float]]:
        res: Dict[str, Dict[str, float]] = {}
        for f in self.factors:
            if f.group not in res:
                res[f.group] = {"earned": 0.0, "max": 0.0}
            res[f.group]["earned"] = round(res[f.group]["earned"] + f.earned_points, 2)
            res[f.group]["max"] = round(res[f.group]["max"] + f.max_points, 2)
        return res


def assess_candidate_quality(
    candidate: Any,
    context: Dict[str, Any],
    profile: Optional[Any] = None,
    as_of_ms: Optional[int] = None,
    approved_rules: Optional[Sequence[Any]] = None,
    threshold: Optional[float] = None,
    lesson_delta: Optional[float] = None,
) -> CandidateQualityAssessment:
    """
    V16.39 Section 038-052: Pure, deterministic candidate quality evaluator.
    No DB, no network, no time.time() inside.
    Evaluates 5 groups totaling exactly 100 points:
      1. HTF/Context: 20 pts
      2. POI Quality: 20 pts
      3. Liquidity Scenario: 15 pts
      4. LTF Confirmation: 25 pts
      5. Execution Quality: 20 pts

    Hard rule: Missing required trigger -> decision = BLOCKED_REQUIRED.
    High score CANNOT compensate for missing structural trigger (Section 040).
    """
    now_ms = int(as_of_ms if as_of_ms is not None else getattr(candidate, "as_of_ms", 0) or 0)
    cand_key = str(getattr(candidate, "candidate_key", "cand_unknown"))
    model_id = str(getattr(candidate, "model_id", "STANDARD_SMC"))
    direction = str(getattr(candidate, "direction", "LONG")).upper()
    is_eligible = bool(getattr(candidate, "is_eligible", True))

    calc = getattr(candidate, "calculation", None)
    snap = getattr(candidate, "snapshot", None)
    evidence = getattr(candidate, "evidence", {}) or {}
    if isinstance(evidence, (list, tuple)):
        evidence = {"items": list(evidence)}

    resolved_threshold = float(threshold if threshold is not None else (getattr(profile, "quality_arm_threshold", 70.0) if profile else 70.0))
    profile_ver = str(getattr(profile, "quality_profile_version", "v16.39") if profile else (str(profile) if isinstance(profile, str) else "v16.39"))
    explicit_lesson_delta = lesson_delta

    factors: List[QualityScoreFactor] = []
    missing_data: List[str] = []
    blockers: List[str] = []

    # --------------------------------------------------------------------------
    # GROUP 1: HTF / CONTEXT (20 points total: 10 + 5 + 5)
    # --------------------------------------------------------------------------
    ctx_src = str(context.get("context_source") or "DATABASE_LOADED").upper()
    htf_bias = str(context.get("htf_bias") or getattr(snap, "htf_bias", "") or "UNKNOWN").upper()
    h1_align = str(context.get("h1_alignment") or getattr(snap, "h1_alignment", "") or "UNKNOWN").upper()

    # Factor 1.1: Context Provenance (10 pts)
    if ctx_src in ("INJECTED_REPLAY", "DATABASE_LOADED") and htf_bias not in ("UNKNOWN", ""):
        f_prov = QualityScoreFactor(
            code="HTF_CONTEXT_PROVENANCE",
            group="HTF_CONTEXT",
            earned_points=10.0,
            max_points=10.0,
            status="FULL",
            explanation_vi="Xu hướng HTF có dữ liệu nến xác thực từ nguồn chuẩn.",
            known_at_ms=now_ms,
            measurement={"source": ctx_src, "bias": htf_bias},
        )
    elif htf_bias not in ("UNKNOWN", ""):
        f_prov = QualityScoreFactor(
            code="HTF_CONTEXT_PROVENANCE",
            group="HTF_CONTEXT",
            earned_points=6.0,
            max_points=10.0,
            status="PARTIAL",
            explanation_vi="Xu hướng HTF có dữ liệu nhưng chỉ từ một nguồn trực tiếp.",
            known_at_ms=now_ms,
            measurement={"source": ctx_src, "bias": htf_bias},
        )
    else:
        missing_data.append("HTF_BIAS")
        f_prov = QualityScoreFactor(
            code="HTF_CONTEXT_PROVENANCE",
            group="HTF_CONTEXT",
            earned_points=0.0,
            max_points=10.0,
            status="UNKNOWN",
            explanation_vi="Chưa xác định được xu hướng khung thời gian lớn HTF.",
            known_at_ms=now_ms,
            measurement={"source": ctx_src, "bias": "UNKNOWN"},
        )
    factors.append(f_prov)

    # Factor 1.2: HTF / LTF Model Alignment (5 pts)
    is_aligned = False
    if "CONTINUATION" in model_id:
        is_aligned = (direction == "LONG" and htf_bias == "BULLISH") or (direction == "SHORT" and htf_bias == "BEARISH")
    elif "REVERSAL" in model_id or "SWEEP_RECLAIM" in model_id:
        is_aligned = (htf_bias in ("RANGE", "UNCERTAIN")) or (direction == "LONG" and htf_bias in ("BEARISH", "BULLISH")) or (direction == "SHORT")
    else:
        is_aligned = (direction == "LONG" and htf_bias in ("BULLISH", "NEUTRAL")) or (direction == "SHORT" and htf_bias in ("BEARISH", "NEUTRAL"))

    if is_aligned:
        f_align = QualityScoreFactor(
            code="HTF_MODEL_ALIGNMENT",
            group="HTF_CONTEXT",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi=f"Hướng {direction} phù hợp với xu hướng và mô hình {model_id}.",
            known_at_ms=now_ms,
            measurement={"direction": direction, "htf_bias": htf_bias},
        )
    elif htf_bias != "UNKNOWN":
        f_align = QualityScoreFactor(
            code="HTF_MODEL_ALIGNMENT",
            group="HTF_CONTEXT",
            earned_points=2.5,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Hướng {direction} ngược một phần xu hướng HTF ({htf_bias}).",
            known_at_ms=now_ms,
            measurement={"direction": direction, "htf_bias": htf_bias},
        )
    else:
        f_align = QualityScoreFactor(
            code="HTF_MODEL_ALIGNMENT",
            group="HTF_CONTEXT",
            earned_points=1.0,
            max_points=5.0,
            status="UNKNOWN",
            explanation_vi="Không có đủ dữ liệu để so khớp hướng HTF.",
            known_at_ms=now_ms,
        )
    factors.append(f_align)

    # Factor 1.3: Dealing Range Position (5 pts)
    dr = context.get("dealing_range") or getattr(snap, "dealing_range", None)
    dr_pos = getattr(dr, "position", None) if dr else None
    if dr_pos is not None and math.isfinite(dr_pos):
        if (direction == "LONG" and dr_pos <= 0.50) or (direction == "SHORT" and dr_pos >= 0.50):
            f_dr = QualityScoreFactor(
                code="HTF_DEALING_RANGE_POSITION",
                group="HTF_CONTEXT",
                earned_points=5.0,
                max_points=5.0,
                status="FULL",
                explanation_vi=f"Vị thế {direction} nằm trong vùng Discount/Premium thuận lợi (pos={dr_pos:.2f}).",
                known_at_ms=now_ms,
                measurement={"dr_position": dr_pos},
            )
        elif 0.40 <= dr_pos <= 0.60:
            f_dr = QualityScoreFactor(
                code="HTF_DEALING_RANGE_POSITION",
                group="HTF_CONTEXT",
                earned_points=3.0,
                max_points=5.0,
                status="PARTIAL",
                explanation_vi=f"Vị thế nằm ở dải cân bằng Equilibrium (pos={dr_pos:.2f}).",
                known_at_ms=now_ms,
                measurement={"dr_position": dr_pos},
            )
        else:
            f_dr = QualityScoreFactor(
                code="HTF_DEALING_RANGE_POSITION",
                group="HTF_CONTEXT",
                earned_points=1.0,
                max_points=5.0,
                status="PARTIAL",
                explanation_vi=f"Vị thế {direction} ở nửa bất lợi của Dealing Range (pos={dr_pos:.2f}).",
                known_at_ms=now_ms,
                measurement={"dr_position": dr_pos},
            )
    else:
        f_dr = QualityScoreFactor(
            code="HTF_DEALING_RANGE_POSITION",
            group="HTF_CONTEXT",
            earned_points=2.5,
            max_points=5.0,
            status="UNKNOWN",
            explanation_vi="Dealing range chưa xác định, dùng điểm tham chiếu trung tính.",
            known_at_ms=now_ms,
        )
    factors.append(f_dr)

    # --------------------------------------------------------------------------
    # GROUP 2: POI QUALITY (20 points total: 5 + 5 + 5 + 5)
    # --------------------------------------------------------------------------
    zone = context.get("zone")
    touch_count = int(getattr(zone, "touch_count", 1) if zone else 1)
    freshness = str(getattr(zone, "freshness_state", "FRESH") if zone else "FRESH").upper()
    has_zone_source = bool(getattr(candidate, "zone_id", None) or getattr(zone, "zone_id", None) or evidence.get("zone_id") or evidence.get("fvg_id") or evidence.get("fvg_zone"))

    # Factor 2.1: POI Source Confirmed (5 pts)
    if has_zone_source:
        f_poi_src = QualityScoreFactor(
            code="POI_SOURCE_CONFIRMED",
            group="POI_QUALITY",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi="Vùng POI có nguồn gốc swing / FVG được xác nhận.",
            known_at_ms=now_ms,
        )
    else:
        f_poi_src = QualityScoreFactor(
            code="POI_SOURCE_CONFIRMED",
            group="POI_QUALITY",
            earned_points=2.5,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi="Vùng POI tổng quát, chưa gắn với FVG/OB độc lập.",
            known_at_ms=now_ms,
        )
    factors.append(f_poi_src)

    # Factor 2.2: Freshness / Lifecycle (5 pts)
    if touch_count <= 1 and freshness != "OVER_MITIGATED":
        f_fresh = QualityScoreFactor(
            code="POI_FRESHNESS_LIFECYCLE",
            group="POI_QUALITY",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi=f"Vùng POI tươi mới (số lần chạm: {touch_count}).",
            known_at_ms=now_ms,
            measurement={"touch_count": touch_count, "freshness": freshness},
        )
    elif touch_count == 2:
        f_fresh = QualityScoreFactor(
            code="POI_FRESHNESS_LIFECYCLE",
            group="POI_QUALITY",
            earned_points=3.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Vùng POI đã chạm {touch_count} lần, độ phản ứng giảm nhẹ.",
            known_at_ms=now_ms,
            measurement={"touch_count": touch_count, "freshness": freshness},
        )
    else:
        f_fresh = QualityScoreFactor(
            code="POI_FRESHNESS_LIFECYCLE",
            group="POI_QUALITY",
            earned_points=1.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Vùng POI đã chạm {touch_count} lần, nguy cơ bị thủng cao.",
            known_at_ms=now_ms,
            measurement={"touch_count": touch_count, "freshness": freshness},
        )
    factors.append(f_fresh)

    # Factor 2.3: Reaction Quality at POI (5 pts)
    has_reaction = bool(
        evidence.get("is_displacement")
        or evidence.get("displacement_detected")
        or evidence.get("mss_detected")
        or evidence.get("retest_detected")
        or getattr(candidate, "trigger_chain", None)
    )
    if has_reaction:
        f_react = QualityScoreFactor(
            code="POI_REACTION_QUALITY",
            group="POI_QUALITY",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi="Có phản ứng từ chối giá và dịch chuyển rõ ràng tại POI.",
            known_at_ms=now_ms,
        )
    else:
        f_react = QualityScoreFactor(
            code="POI_REACTION_QUALITY",
            group="POI_QUALITY",
            earned_points=2.5,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi="Phản ứng tại vùng POI ở mức trung bình.",
            known_at_ms=now_ms,
        )
    factors.append(f_react)

    # Factor 2.4: Confluence Non-overlapping (5 pts)
    has_sr_conf = bool(getattr(candidate, "sr_evidence", None) or context.get("sr_zones"))
    if has_sr_conf:
        f_conf = QualityScoreFactor(
            code="POI_CONFLUENCE_NON_OVERLAPPING",
            group="POI_QUALITY",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi="Có hợp lưu với mốc hỗ trợ/kháng cự S/R mà không trùng lặp bằng chứng.",
            known_at_ms=now_ms,
        )
    else:
        f_conf = QualityScoreFactor(
            code="POI_CONFLUENCE_NON_OVERLAPPING",
            group="POI_QUALITY",
            earned_points=3.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi="Vùng POI đơn lẻ, không có hợp lưu S/R bổ sung.",
            known_at_ms=now_ms,
        )
    factors.append(f_conf)

    # --------------------------------------------------------------------------
    # GROUP 3: LIQUIDITY SCENARIO (15 points total: 5 + 5 + 5)
    # --------------------------------------------------------------------------
    has_liq_ref = bool(
        evidence.get("sweep_id")
        or evidence.get("sweep_level")
        or evidence.get("liquidity_level_id")
        or evidence.get("cluster_id")
        or context.get("liq_levels")
    )
    # Factor 3.1: Liquidity Reference Sourced (5 pts)
    if has_liq_ref:
        f_liq_ref = QualityScoreFactor(
            code="LIQ_REFERENCE_SOURCED",
            group="LIQUIDITY_SCENARIO",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi="Xác định rõ mốc thanh khoản quét hoặc tham chiếu.",
            known_at_ms=now_ms,
        )
    else:
        f_liq_ref = QualityScoreFactor(
            code="LIQ_REFERENCE_SOURCED",
            group="LIQUIDITY_SCENARIO",
            earned_points=2.5,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi="Tham chiếu thanh khoản dựa trên swing cục bộ gần nhất.",
            known_at_ms=now_ms,
        )
    factors.append(f_liq_ref)

    # Factor 3.2: Target Validity (5 pts)
    tp_val = float(getattr(candidate, "take_profit", 0.0) or 0.0)
    planned_entry = float(getattr(candidate, "planned_entry", 0.0) or 0.0)
    has_valid_target = (tp_val > 0.0) and (
        (direction == "LONG" and tp_val > planned_entry)
        or (direction == "SHORT" and tp_val < planned_entry)
    )
    if has_valid_target:
        f_target = QualityScoreFactor(
            code="LIQ_TARGET_VALIDITY",
            group="LIQUIDITY_SCENARIO",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi=f"Mục tiêu TP ({tp_val:.2f}) có cấu trúc hình học đúng hướng.",
            known_at_ms=now_ms,
            measurement={"tp": tp_val, "entry": planned_entry},
        )
    else:
        blockers.append("INVALID_TARGET_GEOMETRY")
        f_target = QualityScoreFactor(
            code="LIQ_TARGET_VALIDITY",
            group="LIQUIDITY_SCENARIO",
            earned_points=0.0,
            max_points=5.0,
            status="ZERO",
            explanation_vi="Mục tiêu TP không hợp lệ hoặc sai phía so với entry.",
            known_at_ms=now_ms,
            measurement={"tp": tp_val, "entry": planned_entry},
        )
    factors.append(f_target)

    # Factor 3.3: Path and Obstacles (5 pts)
    gross_rr = float(getattr(candidate, "gross_rr", 0.0) or (calc.gross_rr if calc else 0.0))
    if gross_rr >= 1.5:
        f_path = QualityScoreFactor(
            code="LIQ_PATH_AND_OBSTACLES",
            group="LIQUIDITY_SCENARIO",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi=f"Biên độ tới mục tiêu đủ rộng (gross RR={gross_rr:.2f}).",
            known_at_ms=now_ms,
            measurement={"gross_rr": gross_rr},
        )
    elif gross_rr >= 1.2:
        f_path = QualityScoreFactor(
            code="LIQ_PATH_AND_OBSTACLES",
            group="LIQUIDITY_SCENARIO",
            earned_points=3.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Biên độ tới mục tiêu hẹp (gross RR={gross_rr:.2f}).",
            known_at_ms=now_ms,
            measurement={"gross_rr": gross_rr},
        )
    else:
        f_path = QualityScoreFactor(
            code="LIQ_PATH_AND_OBSTACLES",
            group="LIQUIDITY_SCENARIO",
            earned_points=1.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Biên độ quá hẹp tới mục tiêu (gross RR={gross_rr:.2f}).",
            known_at_ms=now_ms,
            measurement={"gross_rr": gross_rr},
        )
    factors.append(f_path)

    # --------------------------------------------------------------------------
    # GROUP 4: LTF CONFIRMATION QUALITY (25 points total: 10 + 5 + 5 + 5)
    # --------------------------------------------------------------------------
    has_seq = bool(
        getattr(candidate, "trigger_chain", None)
        or (evidence.get("sweep_detected") and evidence.get("mss_detected"))
        or evidence.get("retest_detected")
    )
    # Factor 4.1: Timeline / Sequence Quality (10 pts)
    if has_seq:
        f_seq = QualityScoreFactor(
            code="LTF_TIMELINE_SEQUENCE",
            group="LTF_CONFIRMATION",
            earned_points=10.0,
            max_points=10.0,
            status="FULL",
            explanation_vi="Trình tự chuỗi trigger M1 được xác nhận theo thứ tự nhân quả.",
            known_at_ms=now_ms,
        )
    elif is_eligible:
        f_seq = QualityScoreFactor(
            code="LTF_TIMELINE_SEQUENCE",
            group="LTF_CONFIRMATION",
            earned_points=6.0,
            max_points=10.0,
            status="PARTIAL",
            explanation_vi="Chuỗi trigger cơ bản hợp lệ theo model.",
            known_at_ms=now_ms,
        )
    else:
        f_seq = QualityScoreFactor(
            code="LTF_TIMELINE_SEQUENCE",
            group="LTF_CONFIRMATION",
            earned_points=0.0,
            max_points=10.0,
            status="ZERO",
            explanation_vi="Thiếu trình tự xác nhận LTF bắt buộc.",
            known_at_ms=now_ms,
        )
    factors.append(f_seq)

    # Factor 4.2: Displacement Quality (5 pts)
    has_disp = bool(evidence.get("is_displacement") or evidence.get("displacement_detected"))
    disp_body = float(evidence.get("displacement_body_atr", 0.8) if has_disp else 0.5)
    if has_disp or disp_body >= 0.70:
        f_disp = QualityScoreFactor(
            code="LTF_DISPLACEMENT_QUALITY",
            group="LTF_CONFIRMATION",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi="Thân nến phá vỡ/dịch chuyển dứt khoát, vượt ngưỡng ATR.",
            known_at_ms=now_ms,
            measurement={"displacement_body_atr": disp_body},
        )
    else:
        f_disp = QualityScoreFactor(
            code="LTF_DISPLACEMENT_QUALITY",
            group="LTF_CONFIRMATION",
            earned_points=3.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi="Biên độ dịch chuyển ở mức vừa phải.",
            known_at_ms=now_ms,
            measurement={"displacement_body_atr": disp_body},
        )
    factors.append(f_disp)

    # Factor 4.3: Retest / Reaction Quality (5 pts)
    has_retest = bool(evidence.get("retest_detected") or evidence.get("fvg_retest") or "CONTINUATION" in model_id)
    if has_retest:
        f_retest = QualityScoreFactor(
            code="LTF_REACTION_RETEST",
            group="LTF_CONFIRMATION",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi="Retest chính xác vào vùng giá kiểm tra trước khi kích hoạt.",
            known_at_ms=now_ms,
        )
    else:
        f_retest = QualityScoreFactor(
            code="LTF_REACTION_RETEST",
            group="LTF_CONFIRMATION",
            earned_points=2.5,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi="Retest nông hoặc khớp theo giá thị trường.",
            known_at_ms=now_ms,
        )
    factors.append(f_retest)

    # Factor 4.4: Trigger Freshness (5 pts)
    trigger_known_at = int(evidence.get("trigger_known_at") or now_ms)
    trigger_age_ms = max(0, now_ms - trigger_known_at)
    if trigger_age_ms <= 180_000:  # <= 3 minutes
        f_fresh_trig = QualityScoreFactor(
            code="LTF_TRIGGER_FRESHNESS",
            group="LTF_CONFIRMATION",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi=f"Tín hiệu xác nhận mới ({trigger_age_ms // 1000}s trước).",
            known_at_ms=now_ms,
            measurement={"age_ms": trigger_age_ms},
        )
    elif trigger_age_ms <= 360_000:  # <= 6 minutes
        f_fresh_trig = QualityScoreFactor(
            code="LTF_TRIGGER_FRESHNESS",
            group="LTF_CONFIRMATION",
            earned_points=3.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Tín hiệu xác nhận trong giới hạn cho phép ({trigger_age_ms // 1000}s).",
            known_at_ms=now_ms,
            measurement={"age_ms": trigger_age_ms},
        )
    else:
        f_fresh_trig = QualityScoreFactor(
            code="LTF_TRIGGER_FRESHNESS",
            group="LTF_CONFIRMATION",
            earned_points=1.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Tín hiệu đã xuất hiện lâu ({trigger_age_ms // 1000}s), độ trễ cao.",
            known_at_ms=now_ms,
            measurement={"age_ms": trigger_age_ms},
        )
    factors.append(f_fresh_trig)

    # --------------------------------------------------------------------------
    # GROUP 5: EXECUTION QUALITY (20 points total: 5 + 5 + 5 + 5)
    # --------------------------------------------------------------------------
    quote = context.get("quote")
    bid = float(getattr(quote, "bid", 0.0) if hasattr(quote, "bid") else (quote or {}).get("bid", 0.0))
    ask = float(getattr(quote, "ask", 0.0) if hasattr(quote, "ask") else (quote or {}).get("ask", 0.0))
    spread = round(ask - bid, 3) if ask >= bid > 0 else 0.15

    # Factor 5.1: Spread vs Budget (5 pts)
    if spread <= 0.20:
        f_spread = QualityScoreFactor(
            code="EXEC_SPREAD_BUDGET",
            group="EXECUTION_QUALITY",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi=f"Spread rất tốt ({spread:.2f} USD ≤ 0.20 USD).",
            known_at_ms=now_ms,
            measurement={"spread": spread},
        )
    elif spread <= 0.35:
        f_spread = QualityScoreFactor(
            code="EXEC_SPREAD_BUDGET",
            group="EXECUTION_QUALITY",
            earned_points=3.5,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Spread chấp nhận được ({spread:.2f} USD).",
            known_at_ms=now_ms,
            measurement={"spread": spread},
        )
    else:
        f_spread = QualityScoreFactor(
            code="EXEC_SPREAD_BUDGET",
            group="EXECUTION_QUALITY",
            earned_points=1.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Spread rộng ({spread:.2f} USD), tốn phí thực thi.",
            known_at_ms=now_ms,
            measurement={"spread": spread},
        )
    factors.append(f_spread)

    # Factor 5.2: Slippage Budget (5 pts)
    slip = float(getattr(calc, "slippage_usd", 0.10) if calc else 0.10)
    if slip <= 0.15:
        f_slip = QualityScoreFactor(
            code="EXEC_SLIPPAGE_BUDGET",
            group="EXECUTION_QUALITY",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi=f"Ước lượng trượt giá an toàn ({slip:.2f} USD).",
            known_at_ms=now_ms,
            measurement={"slippage_usd": slip},
        )
    elif slip <= 0.30:
        f_slip = QualityScoreFactor(
            code="EXEC_SLIPPAGE_BUDGET",
            group="EXECUTION_QUALITY",
            earned_points=3.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Trượt giá trong ngưỡng trung bình ({slip:.2f} USD).",
            known_at_ms=now_ms,
            measurement={"slippage_usd": slip},
        )
    else:
        f_slip = QualityScoreFactor(
            code="EXEC_SLIPPAGE_BUDGET",
            group="EXECUTION_QUALITY",
            earned_points=1.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Trượt giá ước lượng cao ({slip:.2f} USD).",
            known_at_ms=now_ms,
            measurement={"slippage_usd": slip},
        )
    factors.append(f_slip)

    # Factor 5.3: Entry Distance / Chase Risk (5 pts)
    atr_val = float(context.get("atr") or 2.0)
    curr_px = (bid + ask) / 2.0 if bid > 0 and ask > 0 else planned_entry
    dist_price = abs(curr_px - planned_entry)
    dist_atr = round(dist_price / atr_val, 2) if atr_val > 0 else 0.2
    if dist_atr <= 0.40:
        f_dist = QualityScoreFactor(
            code="EXEC_ENTRY_DISTANCE",
            group="EXECUTION_QUALITY",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi=f"Giá hiện tại sát điểm vào lệnh ({dist_atr:.2f} ATR), không bị chase.",
            known_at_ms=now_ms,
            measurement={"distance_atr": dist_atr},
        )
    elif dist_atr <= 0.80:
        f_dist = QualityScoreFactor(
            code="EXEC_ENTRY_DISTANCE",
            group="EXECUTION_QUALITY",
            earned_points=3.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Khoảng cách vào lệnh vừa phải ({dist_atr:.2f} ATR).",
            known_at_ms=now_ms,
            measurement={"distance_atr": dist_atr},
        )
    else:
        f_dist = QualityScoreFactor(
            code="EXEC_ENTRY_DISTANCE",
            group="EXECUTION_QUALITY",
            earned_points=1.0,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"Khoảng cách xa ({dist_atr:.2f} ATR), có nguy cơ đuổi giá.",
            known_at_ms=now_ms,
            measurement={"distance_atr": dist_atr},
        )
    factors.append(f_dist)

    # Factor 5.4: Net Room and Clearance (5 pts)
    min_net_rr = float(getattr(profile, "min_net_rr", 1.5) if profile else 1.5)
    net_rr = float(getattr(candidate, "net_rr", 0.0) or (calc.net_rr if calc else 0.0))
    if net_rr >= min_net_rr:
        f_rr = QualityScoreFactor(
            code="EXEC_NET_ROOM_CLEARANCE",
            group="EXECUTION_QUALITY",
            earned_points=5.0,
            max_points=5.0,
            status="FULL",
            explanation_vi=f"R:R ròng đạt chuẩn yêu cầu ({net_rr:.2f}R ≥ {min_net_rr:.2f}R).",
            known_at_ms=now_ms,
            measurement={"net_rr": net_rr, "min_net_rr": min_net_rr},
        )
    elif net_rr >= 1.2:
        f_rr = QualityScoreFactor(
            code="EXEC_NET_ROOM_CLEARANCE",
            group="EXECUTION_QUALITY",
            earned_points=2.5,
            max_points=5.0,
            status="PARTIAL",
            explanation_vi=f"R:R ròng ({net_rr:.2f}R) tiệm cận ngưỡng yêu cầu ({min_net_rr:.2f}R).",
            known_at_ms=now_ms,
            measurement={"net_rr": net_rr, "min_net_rr": min_net_rr},
        )
    else:
        f_rr = QualityScoreFactor(
            code="EXEC_NET_ROOM_CLEARANCE",
            group="EXECUTION_QUALITY",
            earned_points=0.0,
            max_points=5.0,
            status="ZERO",
            explanation_vi=f"R:R ròng quá thấp sau chi phí ({net_rr:.2f}R < 1.2R).",
            known_at_ms=now_ms,
            measurement={"net_rr": net_rr, "min_net_rr": min_net_rr},
        )
    factors.append(f_rr)

    # Compute base score
    base_score = round(sum(f.earned_points for f in factors), 2)
    # Ensure base score strictly within [0.0, 100.0]
    base_score = max(0.0, min(100.0, base_score))

    # Evaluate approved lesson score adjustments if enabled (SECTIONS 121 - 126)
    if explicit_lesson_delta is not None:
        lesson_delta = explicit_lesson_delta
    else:
        lesson_delta = 0.0
        if getattr(profile, "lesson_score_adjustment_enabled", False) and approved_rules:
            raw_delta = 0.0
            for r in approved_rules:
                if not getattr(r, "enabled", False) or not getattr(r, "is_approved", False):
                    continue
                r_eff = str(getattr(r, "effect", "") or "").upper()
                if r_eff == "ADD_SCORE":
                    raw_delta += 5.0
                elif r_eff == "DEDUCT_SCORE":
                    raw_delta -= 5.0
            # Cap score adjustment at +/- 15 points
            lesson_delta = max(-15.0, min(15.0, raw_delta))

    final_score = round(max(0.0, min(100.0, base_score + lesson_delta)), 2)

    # Decision logic (SECTIONS 040, 053, 054)
    # 1. Hard structural trigger check: Score CANNOT bypass missing required trigger!
    if not is_eligible or getattr(candidate, "reason_code", None) in ("WAITING_M1_RETEST", "WAITING_M1_STRUCTURE", "TRIGGER_EXPIRED"):
        decision = "BLOCKED_REQUIRED"
        blockers.append("MISSING_REQUIRED_TRIGGER")
    elif blockers:
        decision = "BLOCKED_GUARD"
    elif final_score >= resolved_threshold:
        decision = "ELIGIBLE_BY_SCORE"
    else:
        decision = "BELOW_THRESHOLD"

    assessment_id = f"q_{cand_key}_{now_ms}"
    cfg_fp = hashlib.sha256(f"{profile_ver}_{resolved_threshold}_{base_score}".encode()).hexdigest()[:12]

    return CandidateQualityAssessment(
        assessment_id=assessment_id,
        candidate_key=cand_key,
        model_id=model_id,
        base_score=base_score,
        lesson_delta=lesson_delta,
        final_score=final_score,
        threshold=resolved_threshold,
        decision=decision,
        as_of_ms=now_ms,
        profile_version=profile_ver,
        config_fingerprint=cfg_fp,
        factors=tuple(factors),
        missing_data=tuple(missing_data),
        blockers=tuple(blockers),
    )


