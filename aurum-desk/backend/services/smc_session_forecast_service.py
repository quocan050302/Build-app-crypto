"""
backend/services/smc_session_forecast_service.py
V14.4.1 - Conditional, causal three-session reading (ASIA / LONDON(Europe) / NY).

A forecast is a CONDITIONAL HYPOTHESIS ("nếu ... thì ..."), not an event and not an order:
  * built only from closed bars known at `as_of_ms` (a running session is partial, never final);
  * direction may stay None (observation) - it is never forced to LONG/SHORT;
  * no claim about market-maker intent, no fixed "Asia accumulates -> Europe sweeps -> NY runs" script;
  * it never enables Asia/Europe trading: `entry_eligible` is always False here (entry = confirmed setup +
    the common guards, in the configured NY window only);
  * probability stays UNAVAILABLE unless a validated registry model says otherwise (none exists by default).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import smc_engine
from services.smc_liquidity_service import build_liquidity_map, select_unconsumed_target, update_liquidity_states
from services.smc_regime_service import atr_relative, build_htf_context, classify_regime, h1_alignment_from
from services.smc_scenario_config import ScenarioConfig, active_scenario_config
from services.smc_scenario_service import (
    ClosedContext, M5_MS, SETUP_TF, build_session_context, detect_level_sweeps,
)
from services.smc_session_context import SESSION_CONVENTION_VERSION, SessionRange
from services.smc_session_features import (
    compute_asia_expansion, compute_clv, compute_europe_position, feature_record,
)

logger = logging.getLogger("smc_session_forecast")

SESSIONS = ("ASIA", "LONDON", "NY")
SESSION_LABEL_VI = {"ASIA": "Á", "LONDON": "Âu", "NY": "Mỹ"}
FORECAST_SCHEMA_VERSION = "v14.4.1-forecast"
ENTRY_NOTE_VI = ("Dự đoán chỉ là giả thuyết có điều kiện. Vào lệnh chỉ khi có setup đã xác nhận cấu trúc và qua đủ "
                 "bộ bảo vệ (tin, bài học, tài khoản, báo giá, nguồn dữ liệu), trong cửa sổ NY mặc định.")


@dataclass
class SessionForecast:
    session: str
    session_id: str
    as_of_ms: int
    data_status: str                         # OK | PARTIAL | NOT_STARTED | DATA_UNAVAILABLE
    is_partial: bool
    hypothesis_type: str                     # see build_* functions
    facts: List[str] = field(default_factory=list)
    primary: Optional[Dict[str, Any]] = None
    alternative: Optional[Dict[str, Any]] = None
    watched_liquidity: List[Dict[str, Any]] = field(default_factory=list)
    target: Optional[Dict[str, Any]] = None
    confirmation: List[str] = field(default_factory=list)
    invalidation: List[str] = field(default_factory=list)
    features: Dict[str, Any] = field(default_factory=dict)
    session_range: Optional[Dict[str, Any]] = None
    summary_vi: str = ""
    scenario_refs: List[Dict[str, Any]] = field(default_factory=list)
    probability: Dict[str, Any] = field(default_factory=lambda: {
        "status": "UNAVAILABLE", "p_session_event": None, "support_n": None,
        "note": "Chưa có mô hình xác suất được kiểm định cho sự kiện phiên."})
    entry_eligible: bool = False
    entry_note_vi: str = ENTRY_NOTE_VI
    relation_status: Optional[str] = None
    invalidation_price: Optional[float] = None
    convention_version: str = SESSION_CONVENTION_VERSION
    schema_version: str = FORECAST_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version, "session": self.session,
            "session_label_vi": SESSION_LABEL_VI[self.session], "session_id": self.session_id,
            "as_of_ms": self.as_of_ms, "data_status": self.data_status, "is_partial": self.is_partial,
            "hypothesis_type": self.hypothesis_type, "facts": list(self.facts), "primary": self.primary,
            "alternative": self.alternative, "watched_liquidity": list(self.watched_liquidity),
            "target": self.target, "confirmation": list(self.confirmation), "invalidation": list(self.invalidation),
            "invalidation_price": self.invalidation_price,
            "features": self.features, "session_range": self.session_range, "summary_vi": self.summary_vi,
            "scenario_refs": list(self.scenario_refs), "probability": self.probability,
            "entry_eligible": self.entry_eligible, "entry_note_vi": self.entry_note_vi,
            "relation_status": self.relation_status,
            "convention_version": self.convention_version,
        }


def _hyp(direction: Optional[str], thesis: str, conditions: Sequence[str], inval: Optional[str] = None, inval_price: Optional[float] = None) -> Dict[str, Any]:
    res = {"direction": direction, "thesis_vi": thesis, "conditions_vi": list(conditions), "invalidation_vi": inval}
    if inval_price is not None:
        res["invalidation_price"] = inval_price
    return res


def _lvl_brief(l: Any) -> Dict[str, Any]:
    return {"id": l.id, "price": l.price, "label_vi": l.label_vi, "state": l.state, "side": l.side,
            "source_kind": l.source_kind, "is_partial": l.is_partial, "known_at_ms": l.known_at_ms}


def _unavailable(session: str, session_id: str, now_ms: int, status: str, reason: str, rng: Optional[SessionRange],
                 facts: Optional[List[str]] = None) -> SessionForecast:
    f = list(facts or []) + [reason]
    return SessionForecast(
        session=session, session_id=session_id, as_of_ms=now_ms, data_status=status, is_partial=True,
        hypothesis_type="UNKNOWN", facts=f, session_range=rng.to_dict() if rng else None,
        summary_vi=f"Phiên {SESSION_LABEL_VI[session]}: chưa đủ dữ liệu để đọc ({reason}). Đang quan sát.")


def _closed_in(m5: Sequence[Any], start_ms: int, cutoff_ms: int) -> List[Any]:
    return [c for c in m5 if c.timestamp >= start_ms and c.timestamp + M5_MS <= cutoff_ms]


def _target_for(levels, direction: Optional[str], price: Optional[float], now_ms: int) -> Optional[Dict[str, Any]]:
    if direction not in ("LONG", "SHORT") or price is None:
        return None
    t = select_unconsumed_target(levels, direction, price, as_of_ms=now_ms)
    return _lvl_brief(t) if t is not None else None


def build_asia_forecast(*, session_id, now_ms, rng: SessionRange, htf_text: str, pd, levels, m5, price) -> SessionForecast:
    if rng.coverage_status == "NOT_STARTED":
        return _unavailable("ASIA", session_id, now_ms, "NOT_STARTED", "phiên Á chưa bắt đầu", rng,
                            [f"HTF: {htf_text}"])
    if rng.coverage_status in ("MISSING", "UNKNOWN") or rng.high is None:
        return _unavailable("ASIA", session_id, now_ms, "DATA_UNAVAILABLE", "thiếu nến M5 của phiên Á", rng)
    partial = rng.is_partial
    status = "PARTIAL" if partial else "OK"
    if rng.coverage_status != "VALID":
        status = "DATA_UNAVAILABLE"
    height = round(rng.high - rng.low, 2)
    clv = compute_clv(rng.high, rng.low, rng.close_as_of)
    feats = {"asia_range": feature_record("asia_range", height, known_at_ms=rng.cutoff_ms or now_ms, source="M5_CLOSED"),
             "clv": feature_record("clv", clv, known_at_ms=rng.cutoff_ms or now_ms, source="M5_CLOSED"),
             "K_A": feature_record("K_A", compute_asia_expansion(height, []), known_at_ms=rng.cutoff_ms or now_ms,
                                   source="NEEDS_20_COMPLETED_ASIA_SESSIONS")}
    facts = [f"Biên Á{' (đang hình thành)' if partial else ''}: {rng.low:.2f} – {rng.high:.2f} (rộng {height:.2f}).",
             f"Giá đóng gần nhất {rng.close_as_of:.2f}; HTF: {htf_text}."]
    if rng.coverage_status != "VALID":
        facts.append(f"Độ phủ dữ liệu phiên Á thấp ({rng.coverage_ratio:.0%}), không dùng để dự đoán.")
        return SessionForecast(session="ASIA", session_id=session_id, as_of_ms=now_ms, data_status=status,
                               is_partial=partial, hypothesis_type="UNKNOWN", facts=facts, features=feats,
                               session_range=rng.to_dict(),
                               summary_vi="Phiên Á: dữ liệu chưa đủ độ phủ nên chưa đưa ra giả thuyết.")
    sweeps = [s for s in detect_level_sweeps(levels, _closed_in(m5, rng.start_ms, rng.cutoff_ms or now_ms), symbol="")
              if s["level_kind"] in ("PDH", "PDL")]
    watched = [_lvl_brief(l) for l in levels if l.source_kind in ("PDH", "PDL")]
    if sweeps:
        s = sweeps[-1]
        up = s["type"] == "SWEEP_HIGH"
        direction = "SHORT" if up else "LONG"
        hyp, p, alt = "SWEEP_REJECTION_PENDING", _hyp(
            direction, f"Nếu giá tiếp tục đóng nến dưới {s['level']:.2f} (đã quét {s['level_label']} rồi đóng trở lại) "
            f"thì theo dõi đảo chiều {'giảm' if up else 'tăng'}; chưa phải xác nhận.",
            ["Cần phá cấu trúc có dịch chuyển (displacement) cùng hướng sau cú quét."],
            f"Giá đóng {'trên' if up else 'dưới'} cực trị bấc {s['wick_extreme']:.2f}.",
            inval_price=round(float(s["wick_extreme"]), 2)), \
            _hyp(None, "Nếu giá đóng ngoài mức vừa quét thì cú quét là phá vỡ thật: chuyển sang theo dõi tiếp diễn.", [])
    elif clv is not None and clv >= 0.6 and htf_text.startswith("Tăng"):
        hyp, p, alt = "TREND_EXTENSION", _hyp(
            "LONG", "Nếu giá giữ phía trên nửa biên Á và HTF còn tăng thì theo dõi tiếp diễn lên đỉnh Á.",
            ["Giữ trên giữa biên Á", "Không có cú quét đỉnh Á đóng ngược lại"], f"Đóng dưới đáy Á {rng.low:.2f}",
            inval_price=round(float(rng.low), 2)), \
            _hyp(None, "Nếu giá quay xuống dưới giữa biên thì trở lại quan sát trong biên.", [])
    elif clv is not None and clv <= -0.6 and htf_text.startswith("Giảm"):
        hyp, p, alt = "TREND_EXTENSION", _hyp(
            "SHORT", "Nếu giá giữ phía dưới nửa biên Á và HTF còn giảm thì theo dõi tiếp diễn xuống đáy Á.",
            ["Giữ dưới giữa biên Á", "Không có cú quét đáy Á đóng ngược lại"], f"Đóng trên đỉnh Á {rng.high:.2f}",
            inval_price=round(float(rng.high), 2)), \
            _hyp(None, "Nếu giá quay lên trên giữa biên thì trở lại quan sát trong biên.", [])
    else:
        hyp, p, alt = "RANGE_OBSERVATION", _hyp(
            None, f"Nếu giá còn trong biên {rng.low:.2f}–{rng.high:.2f} thì chỉ quan sát biên; phá biên cần nến đóng ngoài.",
            ["Chờ nến đóng ngoài biên hoặc cú quét có đóng ngược lại"]), _hyp(
            None, "Nếu biên bị phá và giữ ngoài thì chuyển sang theo dõi tiếp diễn.", [])
    tgt = _target_for(levels, p["direction"], price, now_ms)
    return SessionForecast(
        session="ASIA", session_id=session_id, as_of_ms=now_ms, data_status=status, is_partial=partial,
        hypothesis_type=hyp, facts=facts, primary=p, alternative=alt, watched_liquidity=watched, target=tgt,
        confirmation=p["conditions_vi"], invalidation=[p["invalidation_vi"]] if p.get("invalidation_vi") else [],
        invalidation_price=p.get("invalidation_price"),
        features=feats, session_range=rng.to_dict(),
        summary_vi=f"Phiên Á ({'đang chạy' if partial else 'đã xong'}): {p['thesis_vi']}")


def build_london_forecast(*, session_id, now_ms, asia: SessionRange, lon: SessionRange, htf_text: str, levels, m5, price
                          ) -> SessionForecast:
    if asia.coverage_status != "VALID" or asia.high is None:
        return _unavailable("LONDON", session_id, now_ms, "DATA_UNAVAILABLE",
                            "thiếu/không đủ độ phủ biên Á làm mốc", lon, [f"HTF: {htf_text}"])
    if lon.coverage_status == "NOT_STARTED":
        return SessionForecast(
            session="LONDON", session_id=session_id, as_of_ms=now_ms, data_status="NOT_STARTED", is_partial=True,
            hypothesis_type="UNKNOWN", facts=[f"Biên Á đã biết: {asia.low:.2f} – {asia.high:.2f}; phiên Âu chưa bắt đầu."],
            watched_liquidity=[_lvl_brief(l) for l in levels if l.id.startswith("liq-asia")],
            session_range=lon.to_dict(), summary_vi="Phiên Âu chưa bắt đầu: sẽ kiểm tra việc phá/giữ biên Á.")
    bars = _closed_in(m5, lon.start_ms, lon.cutoff_ms or now_ms)
    partial = lon.is_partial
    if not bars or lon.coverage_status in ("MISSING", "UNKNOWN") or lon.coverage_status == "LOW_COVERAGE":
        return _unavailable("LONDON", session_id, now_ms, "DATA_UNAVAILABLE", "thiếu nến M5 phiên Âu", lon,
                            [f"Biên Á: {asia.low:.2f} – {asia.high:.2f}"])
    first_up = next((c for c in bars if c.high > asia.high), None)
    first_dn = next((c for c in bars if c.low < asia.low), None)
    closes_above = [c for c in bars if c.close > asia.high]
    closes_below = [c for c in bars if c.close < asia.low]
    last = bars[-1]
    x_e = compute_europe_position(last.close, asia.high, asia.low)
    feats = {"X_E": feature_record("X_E", x_e, known_at_ms=last.timestamp + M5_MS, source="M5_CLOSED_AS_OF"),
             "clv_london": feature_record("clv_london", compute_clv(lon.high, lon.low, lon.close_as_of),
                                          known_at_ms=lon.cutoff_ms or now_ms, source="M5_CLOSED")}
    facts = [f"Biên Á: {asia.low:.2f} – {asia.high:.2f}.",
             f"Phiên Âu{' (đang chạy)' if partial else ''}: đóng gần nhất {last.close:.2f}, cực trị {lon.low:.2f} – {lon.high:.2f}."]
    if first_up is not None:
        facts.append(f"Bấc vượt đỉnh Á lúc nến {first_up.timestamp} (chỉ là chạm, chưa phải BOS).")
    if first_dn is not None:
        facts.append(f"Bấc vượt đáy Á lúc nến {first_dn.timestamp} (chỉ là chạm, chưa phải BOS).")
    watched = [_lvl_brief(l) for l in levels if l.id.startswith("liq-asia")]
    if closes_above and last.close > asia.high:
        hyp = "BREAKOUT_HOLD_PENDING"
        p = _hyp("LONG", "Nếu nến M5 tiếp tục đóng trên đỉnh Á thì theo dõi tiếp diễn tăng; cần retest giữ vùng.",
                 ["Đóng giữ trên đỉnh Á", "Retest FVG/đỉnh Á không đóng xuyên xuống"], f"Đóng lại dưới đỉnh Á {asia.high:.2f}",
                 inval_price=round(float(asia.high), 2))
        alt = _hyp("SHORT", "Nếu giá đóng ngược vào trong biên Á thì cú phá là quét thanh khoản: theo dõi đảo chiều giảm.",
                   ["Đóng ngược vào biên", "Phá cấu trúc giảm có displacement"])
    elif closes_below and last.close < asia.low:
        hyp = "BREAKOUT_HOLD_PENDING"
        p = _hyp("SHORT", "Nếu nến M5 tiếp tục đóng dưới đáy Á thì theo dõi tiếp diễn giảm; cần retest giữ vùng.",
                 ["Đóng giữ dưới đáy Á", "Retest FVG/đáy Á không đóng xuyên lên"], f"Đóng lại trên đáy Á {asia.low:.2f}",
                 inval_price=round(float(asia.low), 2))
        alt = _hyp("LONG", "Nếu giá đóng ngược vào trong biên Á thì cú phá là quét thanh khoản: theo dõi đảo chiều tăng.",
                   ["Đóng ngược vào biên", "Phá cấu trúc tăng có displacement"])
    elif first_up is not None and last.close <= asia.high and not closes_above:
        hyp = "FALSE_BREAKOUT_PENDING"
        p = _hyp("SHORT", "Nếu giá giữ trong biên sau khi quét đỉnh Á thì theo dõi đảo chiều giảm; chưa xác nhận.",
                 ["Phá cấu trúc giảm có displacement sau cú quét"], f"Đóng trên cực trị bấc {first_up.high:.2f}",
                 inval_price=round(float(first_up.high), 2))
        alt = _hyp(None, "Nếu nến đóng trên đỉnh Á thì đây là phá vỡ thật, không còn là quét.", [])
    elif first_dn is not None and last.close >= asia.low and not closes_below:
        hyp = "FALSE_BREAKOUT_PENDING"
        p = _hyp("LONG", "Nếu giá giữ trong biên sau khi quét đáy Á thì theo dõi đảo chiều tăng; chưa xác nhận.",
                 ["Phá cấu trúc tăng có displacement sau cú quét"], f"Đóng dưới cực trị bấc {first_dn.low:.2f}",
                 inval_price=round(float(first_dn.low), 2))
        alt = _hyp(None, "Nếu nến đóng dưới đáy Á thì đây là phá vỡ thật, không còn là quét.", [])
    else:
        hyp = "RANGE_OBSERVATION"
        p = _hyp(None, "Nếu giá còn trong biên Á thì chỉ quan sát; chờ nến đóng ngoài hoặc cú quét có đóng ngược lại.", [])
        alt = _hyp(None, "Nếu biên Á bị phá và giữ ngoài thì chuyển sang theo dõi tiếp diễn.", [])
    tgt = _target_for(levels, p["direction"], price, now_ms)
    return SessionForecast(
        session="LONDON", session_id=session_id, as_of_ms=now_ms, data_status="PARTIAL" if partial else "OK",
        is_partial=partial, hypothesis_type=hyp, facts=facts, primary=p, alternative=alt, watched_liquidity=watched,
        target=tgt, confirmation=p["conditions_vi"],
        invalidation=[p["invalidation_vi"]] if p.get("invalidation_vi") else [],
        invalidation_price=p.get("invalidation_price"),
        features=feats,
        session_range=lon.to_dict(), summary_vi=f"Phiên Âu ({'đang chạy' if partial else 'đã xong'}): {p['thesis_vi']}")


def build_ny_forecast(*, session_id, now_ms, asia: SessionRange, lon: SessionRange, ny: SessionRange, htf_text: str,
                      regime: str, levels, m5, price, london: SessionForecast, primary_scenario=None) -> SessionForecast:
    if ny.coverage_status == "NOT_STARTED":
        return SessionForecast(
            session="NY", session_id=session_id, as_of_ms=now_ms, data_status="NOT_STARTED", is_partial=True,
            hypothesis_type="UNKNOWN", facts=[f"HTF: {htf_text}.", *london.facts[:2]],
            session_range=ny.to_dict(), summary_vi="Phiên Mỹ chưa bắt đầu: sẽ dùng thông tin Á/Âu đã biết để theo dõi.")
    if asia.coverage_status != "VALID":
        return _unavailable("NY", session_id, now_ms, "DATA_UNAVAILABLE", "thiếu biên Á đủ độ phủ", ny, [f"HTF: {htf_text}"])
    facts = [f"HTF: {htf_text}; chế độ thị trường: {regime}.",
             f"Biên Á {asia.low:.2f} – {asia.high:.2f}."]
    if lon.coverage_status == "VALID" and lon.high is not None:
        facts.append(f"Âu {'đang chạy' if lon.is_partial else 'đã xong'} (as-of): {lon.low:.2f} – {lon.high:.2f}, "
                     f"đóng gần nhất {lon.close_as_of:.2f}.")
    else:
        facts.append("Dữ liệu phiên Âu chưa đủ độ phủ: không dùng cho luận điểm.")
    primary = london.primary or {}
    d = primary.get("direction")
    lone = london.hypothesis_type
    h_align = None
    if htf_text.startswith("Tăng"):
        h_align = "LONG"
    elif htf_text.startswith("Giảm"):
        h_align = "SHORT"
    watched = list(london.watched_liquidity)
    inval_numeric = getattr(london, "invalidation_price", None) or primary.get("invalidation_price")
    if regime == "SHOCK":
        hyp = "OBSERVATION_SHOCK"
        p = _hyp(None, "Biến động bất thường (SHOCK): chỉ quan sát, không dùng luận điểm tiếp diễn/đảo chiều.", [])
        alt = None
    elif lone == "BREAKOUT_HOLD_PENDING" and d and (h_align in (None, d)):
        hyp = "CONTINUATION_WATCH"
        htf_note = "cùng chiều HTF" if h_align == d else "HTF chưa rõ nên chưa xác nhận cùng chiều"
        p = _hyp(d, f"Nếu Âu giữ ngoài biên Á và retest giữ vùng thì theo dõi tiếp diễn {'tăng' if d == 'LONG' else 'giảm'} "
                    f"trong phiên Mỹ ({htf_note}).",
                 ["Retest FVG/biên Á không đóng xuyên", "Phá cấu trúc cùng hướng có displacement"],
                 primary.get("invalidation_vi"),
                 inval_price=inval_numeric)
        alt = london.alternative
    elif lone in ("FALSE_BREAKOUT_PENDING", "BREAKOUT_HOLD_PENDING") and d:
        hyp = "REVERSAL_WATCH" if lone == "FALSE_BREAKOUT_PENDING" else "OBSERVATION_CONFLICT"
        p = _hyp(d if lone == "FALSE_BREAKOUT_PENDING" else None,
                 ("Nếu giá reclaim biên Á và có MSS có displacement thì theo dõi đảo chiều."
                  if lone == "FALSE_BREAKOUT_PENDING" else
                  "Âu phá biên nhưng ngược HTF: xung đột, chỉ quan sát tới khi một bên được xác nhận."),
                 ["Sweep → reclaim → MSS có displacement → FVG → retest"],
                 primary.get("invalidation_vi"),
                 inval_price=inval_numeric)
        alt = london.alternative
    else:
        hyp = "RANGE_OBSERVATION"
        p = _hyp(None, "Chưa có tiếp diễn/đảo chiều rõ từ Á–Âu: quan sát các mức thanh khoản và chờ chuỗi cấu trúc.",
                 ["Chờ sweep/break có đóng nến xác nhận"])
        alt = None
    tgt = _target_for(levels, p["direction"], price, now_ms)
    refs = []
    relation_status = "UNCERTAIN"
    if primary_scenario is not None:
        refs.append({"scenario_id": primary_scenario.scenario_id, "snapshot_id": primary_scenario.snapshot_id,
                     "state": primary_scenario.state.value if hasattr(primary_scenario.state, "value") else str(primary_scenario.state), "direction": primary_scenario.direction})
        if p.get("direction") and primary_scenario.direction:
            if p.get("direction") == primary_scenario.direction:
                relation_status = "SUPPORTS"
            else:
                relation_status = "CONFLICTS"

    return SessionForecast(
        session="NY", session_id=session_id, as_of_ms=now_ms,
        data_status="PARTIAL" if ny.is_partial else "OK", is_partial=ny.is_partial, hypothesis_type=hyp,
        facts=facts, primary=p, alternative=alt, watched_liquidity=watched, target=tgt,
        confirmation=p["conditions_vi"], invalidation=[p["invalidation_vi"]] if p.get("invalidation_vi") else [],
        invalidation_price=p.get("invalidation_price"),
        session_range=ny.to_dict(), scenario_refs=refs,
        features={"x_e": london.features.get("X_E")},
        relation_status=relation_status,
        summary_vi=f"Phiên Mỹ ({'đang chạy' if ny.is_partial else 'đã xong'}): {p['thesis_vi']}")


def _htf_text(bias: str) -> str:
    return {"BULLISH": "Tăng (H4)", "BEARISH": "Giảm (H4)", "RANGE": "Đi ngang (H4)"}.get(bias, "Chưa rõ (thiếu dữ liệu H4)")


def evaluate_session_forecasts(
    symbol: str, context: ClosedContext, now_ms: int, config: Optional[ScenarioConfig] = None,
    primary_scenario: Any = None,
) -> Dict[str, SessionForecast]:
    """ASIA/LONDON/NY forecasts as known at `now_ms`. Appending future bars never changes an earlier forecast
    because every input is filtered by closed-bar time <= now_ms."""
    cfg = config or active_scenario_config
    m5 = context.get("5M")
    sctx = build_session_context(context, now_ms, cfg)
    date = sctx["date"]
    ranges: Dict[str, SessionRange] = sctx["ranges"]
    h4 = build_htf_context(context.get("4H"), "4H")
    h1 = build_htf_context(context.get("1H"), "1H")
    regime = classify_regime(h4.bias, h1_alignment_from(h4, h1), atr_relative(m5)) if m5 else "UNKNOWN"
    htf = _htf_text(h4.bias)
    price = float(m5[-1].close) if m5 else None

    levels: List[Any] = []
    if len(m5) >= cfg.min_atr_bars:
        sh, sl = smc_engine.identify_pivots(m5, timeframe=SETUP_TF)
        atr = smc_engine.compute_atr(m5, period=cfg.displacement_atr_period)
        liq_ranges = {k: v for k, v in ranges.items() if k in ("ASIA", "LONDON") and k != sctx["session_name"]}
        levels = build_liquidity_map(
            symbol=symbol, timeframe=SETUP_TF, swing_highs=sh, swing_lows=sl, session_ranges=liq_ranges,
            previous_day=sctx["previous_day"], tolerance_atr=cfg.eqh_eql_tolerance_atr, atr=atr, now_ms=now_ms)
        update_liquidity_states(levels, m5, now_ms, M5_MS)

    sid = lambda s: f"{symbol}:{s}:{date}"   # noqa: E731
    asia = build_asia_forecast(session_id=sid("ASIA"), now_ms=now_ms, rng=ranges["ASIA"], htf_text=htf,
                               pd=sctx["previous_day"], levels=levels, m5=m5, price=price)
    london = build_london_forecast(session_id=sid("LONDON"), now_ms=now_ms, asia=ranges["ASIA"], lon=ranges["LONDON"],
                                   htf_text=htf, levels=levels, m5=m5, price=price)
    ny = build_ny_forecast(session_id=sid("NY"), now_ms=now_ms, asia=ranges["ASIA"], lon=ranges["LONDON"],
                           ny=ranges["NY"], htf_text=htf, regime=regime, levels=levels, m5=m5, price=price,
                           london=london, primary_scenario=primary_scenario)
    return {"ASIA": asia, "LONDON": london, "NY": ny}


def rank_forecasts(forecasts: Dict[str, SessionForecast], current_session: str) -> List[SessionForecast]:
    """Order for display: the current session first, then by how much confirmed data backs the reading
    (OK > PARTIAL > rest). Structural ordering only: this is NOT a probability or winrate."""
    weight = {"OK": 2, "PARTIAL": 1}
    return sorted(forecasts.values(),
                  key=lambda f: (f.session != current_session, -weight.get(f.data_status, 0), SESSIONS.index(f.session)))
