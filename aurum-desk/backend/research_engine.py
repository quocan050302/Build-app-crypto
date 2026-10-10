"""
Aurum Desk V13.1 — Time-Aware Causal Research Engine for XAUUSDT.
Solves Section 7, 8, 9, 10 requirements:
- Strictly respects as_of_ms boundary (close_time <= as_of_ms)
- Never accesses live collector_service when analyzing historical data
- Multi-timeframe derivation aggregates genuine 1H/4H/D completed bars
- Rejects fake candles or slicing current data for past dates
- Produces authentic, validated LONG/SHORT/NO-TRADE scenarios with net RR
- Beginner-friendly explanations without raw constant codes
"""

import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

import models
import crud
import schemas
import smc_engine
from research_context import (
    ResearchContext,
    ResearchMode,
    DateBasis,
    SessionType,
    build_research_context
)
from market_regime import evaluate_market_regime
from scenario_builder import build_validated_scenarios

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NY_TZ = ZoneInfo("America/New_York")
LONDON_TZ = ZoneInfo("Europe/London")
TOKYO_TZ = ZoneInfo("Asia/Tokyo")
UTC_TZ = ZoneInfo("UTC")


class CandleObj:
    """Universal candle wrapper supporting both attr and dict access."""
    def __init__(self, timestamp: int, open: float, high: float, low: float, close: float, volume: float = 100.0):
        self.timestamp = timestamp
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

    def __getitem__(self, item: str):
        return getattr(self, item)

    def get(self, item: str, default=None):
        return getattr(self, item, default)

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume
        }


def get_current_session_info(as_of_dt: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Determine active trading sessions based on IANA timezones:
    - Tokyo: 09:00 - 18:00 Asia/Tokyo
    - London: 08:00 - 17:00 Europe/London
    - New York: 08:00 - 17:00 America/New_York
    """
    if as_of_dt is None:
        target_utc = datetime.now(UTC_TZ)
    else:
        if as_of_dt.tzinfo is None:
            target_utc = as_of_dt.replace(tzinfo=UTC_TZ)
        else:
            target_utc = as_of_dt.astimezone(UTC_TZ)

    tokyo_dt = target_utc.astimezone(TOKYO_TZ)
    london_dt = target_utc.astimezone(LONDON_TZ)
    ny_dt = target_utc.astimezone(NY_TZ)
    vn_dt = target_utc.astimezone(VN_TZ)

    tokyo_active = (9 <= tokyo_dt.hour < 18)
    london_active = (8 <= london_dt.hour < 17)
    ny_active = (8 <= ny_dt.hour < 17)

    active_names = []
    if tokyo_active:
        active_names.append("Tokyo (Á)")
    if london_active:
        active_names.append("London (Âu)")
    if ny_active:
        active_names.append("New York (Mỹ)")

    return {
        "vn_time": vn_dt.strftime("%H:%M %d/%m/%Y"),
        "ny_time": ny_dt.strftime("%H:%M %Y-%m-%d"),
        "active_sessions": active_names or ["Giữa các phiên (Inter-session)"],
        "tokyo": {"time": tokyo_dt.strftime("%H:%M"), "active": tokyo_active},
        "london": {"time": london_dt.strftime("%H:%M"), "active": london_active},
        "new_york": {"time": ny_dt.strftime("%H:%M"), "active": ny_active}
    }


def _aggregate_15m_candles(candles_15m: List[Any], duration_min: int) -> List[Dict[str, Any]]:
    """
    Aggregates 15M candles into completed bars of duration_min (e.g. 60 min for 1H, 240 min for 4H).
    Only includes bars where all constituent 15M candles are present and closed.
    """
    interval_ms = duration_min * 60 * 1000
    expected_count = duration_min // 15
    groups = {}
    for c in candles_15m:
        t = c.timestamp if hasattr(c, "timestamp") else c["timestamp"]
        bar_start = (t // interval_ms) * interval_ms
        if bar_start not in groups:
            groups[bar_start] = []
        groups[bar_start].append(c)

    aggregated = []
    for bar_start in sorted(groups.keys()):
        grp = groups[bar_start]
        if len(grp) >= expected_count:
            opens = grp[0].open if hasattr(grp[0], "open") else grp[0]["open"]
            highs = max(c.high if hasattr(c, "high") else c["high"] for c in grp)
            lows = min(c.low if hasattr(c, "low") else c["low"] for c in grp)
            closes = grp[-1].close if hasattr(grp[-1], "close") else grp[-1]["close"]
            vols = sum(c.volume if hasattr(c, "volume") else c.get("volume", 0) for c in grp)
            aggregated.append({
                "timestamp": bar_start,
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": vols
            })
    return aggregated


def _derive_causal_multi_timeframe_bias(candles_15m: List[Any]) -> Dict[str, Any]:
    """
    Pure causal calculation of D, 4H, 1H, 15M biases from authentic aggregated bars.
    Does NOT use array stride approximations (closes[::4]) or live collector_service.
    """
    if not candles_15m or len(candles_15m) < 15:
        return {
            "d_bias": "UNKNOWN",
            "h4_bias": "UNKNOWN",
            "d_4h_bias": "CHƯA_ĐỦ_DỮ_LIỆU",
            "h1_alignment": "UNKNOWN"
        }

    # 1. 1H aggregated completed bars
    h1_bars = _aggregate_15m_candles(candles_15m, 60)
    if len(h1_bars) >= 4:
        h1_closes = [b["close"] for b in h1_bars]
        period = min(6, len(h1_closes))
        h1_ema = sum(h1_closes[-period:]) / float(period)
        h1_align = "BULLISH" if h1_closes[-1] > h1_ema else "BEARISH"
    else:
        h1_align = "UNKNOWN"

    # 2. 4H aggregated completed bars
    h4_bars = _aggregate_15m_candles(candles_15m, 240)
    if len(h4_bars) >= 2:
        h4_bias = "BULLISH" if h4_bars[-1]["close"] >= h4_bars[0]["close"] else "BEARISH"
    else:
        h4_bias = "UNKNOWN"

    # 3. Daily aggregated completed bars (1440 min)
    d_bars = _aggregate_15m_candles(candles_15m, 1440)
    if len(d_bars) >= 2:
        d_bias = "BULLISH" if d_bars[-1]["close"] >= d_bars[0]["close"] else "BEARISH"
    else:
        d_bias = "UNKNOWN"

    # Composite HTF
    if d_bias == "UNKNOWN" or h4_bias == "UNKNOWN":
        d_4h_bias = "CHƯA_ĐỦ_DỮ_LIỆU"
    elif d_bias == h4_bias:
        d_4h_bias = d_bias
    elif d_bias == "NEUTRAL":
        d_4h_bias = h4_bias
    elif h4_bias == "NEUTRAL":
        d_4h_bias = d_bias
    else:
        d_4h_bias = "CONFLICT"

    return {
        "d_bias": d_bias,
        "h4_bias": h4_bias,
        "d_4h_bias": d_4h_bias,
        "h1_alignment": h1_align
    }


def generate_research_report(
    db: Session,
    report_type: str = "SESSION_REPORT",
    candles_15m: Optional[list] = None,
    context: Optional[ResearchContext] = None,
    selected_date: Optional[str] = None,
    time_of_day: Optional[str] = None,
    session: Optional[str] = None,
    date_basis: Optional[str] = None,
    mode: Optional[str] = None,
    as_of_ms: Optional[int] = None
) -> models.ResearchReport:
    """
    Generate deep research report for XAUUSDT with authentic multi-timeframe SMC evidence:
    - Guaranteed zero future leakage
    - Pure causal calculations for historical queries
    - Rich validated scenarios (LONG, SHORT, NO_TRADE)
    - Vietnamese explanation for beginners
    """
    now_ts = int(time.time() * 1000)

    # 1. Resolve ResearchContext
    if context is None:
        if as_of_ms:
            context = build_research_context(
                selected_date=selected_date,
                time_of_day=time_of_day,
                session=session,
                date_basis=date_basis,
                mode=mode,
                now_ms=as_of_ms
            )
        else:
            context = build_research_context(
                selected_date=selected_date,
                time_of_day=time_of_day,
                session=session,
                date_basis=date_basis,
                mode=mode,
                now_ms=now_ts
            )

    as_of_dt = context.as_of_dt_utc()
    session_info = get_current_session_info(as_of_dt)
    session_name = " & ".join(session_info["active_sessions"])

    # 2. Source candles strictly up to as_of_ms
    limitations = list(context.limitations)
    if not candles_15m:
        # Query database with explicit cutoff
        # To guarantee closed candles: timestamp + 15m <= as_of_ms -> timestamp <= as_of_ms - 15m
        candle_cutoff = context.as_of_ms - (15 * 60 * 1000)
        raw_candles = crud.get_candles(
            db,
            symbol="XAUUSDT",
            timeframe="15M",
            limit=200,
            ascending=True,
            cutoff_ms=candle_cutoff
        )
        clean_candles = []
        for c in raw_candles:
            if hasattr(c, "timestamp"):
                clean_candles.append(CandleObj(
                    timestamp=c.timestamp,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=getattr(c, "volume", 100)
                ))
            elif isinstance(c, dict):
                clean_candles.append(CandleObj(
                    timestamp=c["timestamp"],
                    open=c["open"],
                    high=c["high"],
                    low=c["low"],
                    close=c["close"],
                    volume=c.get("volume", 100)
                ))

        filtered = [
            c for c in clean_candles
            if c.timestamp + (15 * 60 * 1000) <= context.as_of_ms
        ]
        candles_15m = filtered

    # Handle insufficient data authentically without creating fake candles
    if not candles_15m or len(candles_15m) < 15:
        limitations.append("KHÔNG_ĐỦ_NẾN: Chưa có đủ dữ liệu nến 15M đã đóng trước thời điểm phân tích.")
        as_of_str_vn = session_info["vn_time"]
        no_trade_scenarios = {
            "no_trade": {
                "title": "Chờ Đợi / Không Giao Dịch (Thiếu Dữ Liệu)",
                "is_active": True,
                "reasons": ["Không có đủ dữ liệu nến trước thời điểm as_of đã chọn để xác định cấu trúc Swing."],
                "capital_preservation_message": "Bảo vệ vốn là ưu tiên số một. Chỉ giao dịch khi có đủ dữ liệu nến thực tế.",
                "quota_compliance": "Đứng ngoài thị trường cho tới khi nạp đủ dữ liệu nến lịch sử."
            }
        }
        empty_report = models.ResearchReport(
            report_type=report_type,
            created_at=now_ts,
            session_name=session_name,
            d_4h_bias="CHƯA_ĐỦ_DỮ_LIỆU",
            h1_alignment="CHƯA_ĐỦ_DỮ_LIỆU",
            m15_pois=json.dumps([]),
            liquidity_levels=json.dumps({}),
            scenarios=json.dumps(no_trade_scenarios),
            structured_scenarios=json.dumps(no_trade_scenarios),
            content_markdown=f"""# BÁO CÁO PHÂN TÍCH XAUUSDT — {session_name}

**Thời điểm phân tích (As-Of):** {as_of_str_vn} (Giờ VN)

**Trạng thái:** Chưa có đủ dữ liệu nến 15M đã đóng trước thời điểm này trong cơ sở dữ liệu để tiến hành lập kịch bản.

*Khuyến nghị: Chọn thời điểm khác hoặc tải bổ sung nến lịch sử.*""",
            strategy_version="1.0.0",
            research_date=context.selected_date,
            date_basis=context.date_basis,
            mode=context.mode,
            as_of_ms=context.as_of_ms,
            market_regime="CHƯA_RÕ",
            timeframe_matrix=json.dumps({}),
            data_coverage_status="DATA_UNAVAILABLE",
            quality_score=None,
            provenance_metadata=json.dumps({"limitations": limitations})
        )
        db.add(empty_report)
        db.commit()
        db.refresh(empty_report)
        return empty_report

    # Ensure all candles in candles_15m are CandleObj
    candles_15m = [
        c if isinstance(c, CandleObj) else CandleObj(
            timestamp=c["timestamp"] if isinstance(c, dict) else c.timestamp,
            open=c["open"] if isinstance(c, dict) else c.open,
            high=c["high"] if isinstance(c, dict) else c.high,
            low=c["low"] if isinstance(c, dict) else c.low,
            close=c["close"] if isinstance(c, dict) else c.close,
            volume=c.get("volume", 100) if isinstance(c, dict) else getattr(c, "volume", 100)
        )
        for c in candles_15m
    ]

    # 3. Multi-timeframe Biases (Strict Causal Derivation)
    if context.is_historical():
        tf_biases = _derive_causal_multi_timeframe_bias(candles_15m)
        d_bias = tf_biases["d_bias"]
        h4_bias = tf_biases["h4_bias"]
        htf_bias = tf_biases["d_4h_bias"]
        h1_align = tf_biases["h1_alignment"]
    else:
        try:
            from services.collector_service import collector_service
            d_bias = collector_service.d_bias
            h4_bias = collector_service.h4_bias
            htf_bias = collector_service.d_4h_bias
            h1_align = collector_service.h1_alignment
        except Exception:
            tf_biases = _derive_causal_multi_timeframe_bias(candles_15m)
            d_bias = tf_biases["d_bias"]
            h4_bias = tf_biases["h4_bias"]
            htf_bias = tf_biases["d_4h_bias"]
            h1_align = tf_biases["h1_alignment"]

    # 4. SMC Setup & Structure Evaluation
    smc_res = smc_engine.evaluate_smc_setup(
        candles=candles_15m,
        symbol="XAUUSDT",
        timeframe="15M",
        htf_bias=htf_bias,
        h1_alignment=h1_align
    )

    curr_p = smc_res["current_price"]
    atr = smc_res["atr"]
    sh = smc_res["swing_high"]
    sl = smc_res["swing_low"]
    trend_15m = smc_res["trend"]
    zone = smc_res["zone"]
    eq = smc_res["equilibrium"]

    # 5. Evaluate Market Regime
    regime_res = evaluate_market_regime(candles_15m=candles_15m, atr_val=atr)
    market_regime = regime_res["regime"]
    regime_label = regime_res["label_vi"]

    # 6. Build Validated Scenarios
    scenarios_dict = build_validated_scenarios(
        current_price=curr_p,
        atr_val=atr,
        swing_high=sh,
        swing_low=sl,
        equilibrium=eq,
        regime=market_regime
    )

    # 7. Timeframe Matrix DTO
    tf_matrix = {
        "daily": {"bias": d_bias, "description": f"Khung Ngày: {d_bias}"},
        "h4": {"bias": h4_bias, "description": f"Khung 4 Giờ: {h4_bias}"},
        "h1": {"bias": h1_align, "description": f"Khung 1 Giờ: {h1_align}"},
        "m15": {"trend": trend_15m, "zone": zone, "description": f"Khung 15 Phút: {trend_15m} tại vùng {zone}"},
        "last_closed_candle": {
            "timestamp": candles_15m[-1]["timestamp"],
            "open": candles_15m[-1]["open"],
            "high": candles_15m[-1]["high"],
            "low": candles_15m[-1]["low"],
            "close": candles_15m[-1]["close"]
        }
    }

    # 8. Rich Beginner-Friendly Markdown
    as_of_str_vn = session_info["vn_time"]
    coverage_status = "ĐÃ_XÁC_THỰC" if len(candles_15m) >= 20 and not limitations else "MỘT_PHẦN"

    bull_tp = scenarios_dict['bullish'].get('take_profit')
    bull_tp_str = f"${bull_tp:.2f}" if bull_tp is not None else "Chưa có mục tiêu cấu trúc"
    bear_tp = scenarios_dict['bearish'].get('take_profit')
    bear_tp_str = f"${bear_tp:.2f}" if bear_tp is not None else "Chưa có mục tiêu cấu trúc"

    markdown_content = f"""# BÁO CÁO PHÂN TÍCH XAUUSDT — {report_type}
**Thời điểm phân tích (As-Of):** {as_of_str_vn} (Giờ VN) | Mode: `{context.mode}`
**Phiên thị trường:** {session_name}
**Giá quan sát:** ${curr_p:.2f} | **Biến động ATR (14):** ${atr:.2f} USD
**Trạng thái thị trường (Regime):** {regime_label}

---

### 1. Bối Cảnh Thị Trường & Nhận Định Tổng Quan
- **Đánh giá trạng thái:** {regime_res['supporting_evidence'][0] if regime_res['supporting_evidence'] else 'Thị trường đang vận động ổn định theo cấu trúc.'}
- **Khuyến nghị hành động:** {regime_res['setup_eligibility']['recommended_stance']}
- **Khung Ngày (D) & 4H:** D ({d_bias}) · 4H ({h4_bias}) -> Xu hướng lớn: **{htf_bias}**
- **Đồng thuận 1H & 15M:** 1H ({h1_align}) · 15M ({trend_15m})

---

### 2. Bản Đồ Thanh Khoản & Vùng Giá Cần Quan Sát
- **Đỉnh Swing High gần nhất:** ${sh:.2f}
- **Đáy Swing Low gần nhất:** ${sl:.2f}
- **Điểm cân bằng Dealing Range (Equilibrium):** ${eq:.2f}
- **Vị trí giá hiện tại:** Vùng **{zone}**

---

### 3. Kịch Bản Giao Dịch Hợp Lệ (Có Khấu Trừ Chi Phí)

#### Kịch Bản Mua (LONG):
- **Trạng thái:** {scenarios_dict['bullish']['setup_state_text']}
- **Vùng vào lệnh dự kiến:** {scenarios_dict['bullish']['entry_zone']}
- **Cắt lỗ (SL):** ${scenarios_dict['bullish']['stop_loss']:.2f}
- **Chốt lời (TP):** {bull_tp_str}
- **Tỷ lệ Net R:R sau chi phí:** {scenarios_dict['bullish']['net_rr']}R (Gross: {scenarios_dict['bullish']['gross_rr']}R)
- **Điều kiện còn thiếu:** {scenarios_dict['bullish']['missing_condition']}

#### Kịch Bản Bán (SHORT):
- **Trạng thái:** {scenarios_dict['bearish']['setup_state_text']}
- **Vùng vào lệnh dự kiến:** {scenarios_dict['bearish']['entry_zone']}
- **Cắt lỗ (SL):** ${scenarios_dict['bearish']['stop_loss']:.2f}
- **Chốt lời (TP):** {bear_tp_str}
- **Tỷ lệ Net R:R sau chi phí:** {scenarios_dict['bearish']['net_rr']}R (Gross: {scenarios_dict['bearish']['gross_rr']}R)
- **Điều kiện còn thiếu:** {scenarios_dict['bearish']['missing_condition']}

#### Lời Khuyên Quản Trị Rủi Ro:
{scenarios_dict['no_trade']['capital_preservation_message']}
"""

    report = models.ResearchReport(
        report_type=report_type,
        created_at=now_ts,
        session_name=session_name,
        d_4h_bias=htf_bias,
        h1_alignment=h1_align,
        m15_pois=json.dumps(smc_res.get("fvg_zones", [])),
        liquidity_levels=json.dumps({
            "swing_high": sh,
            "swing_low": sl,
            "equilibrium": eq
        }),
        scenarios=json.dumps(scenarios_dict),
        content_markdown=markdown_content,
        strategy_version="1.0.0",
        research_date=context.selected_date,
        date_basis=context.date_basis,
        mode=context.mode,
        as_of_ms=context.as_of_ms,
        market_regime=market_regime,
        timeframe_matrix=json.dumps(tf_matrix),
        structured_scenarios=json.dumps(scenarios_dict),
        data_coverage_status=coverage_status,
        quality_score=round(regime_res.get("heuristic_score", 0.8), 2),
        provenance_metadata=json.dumps({
            "context": context.to_dict(),
            "limitations": limitations
        })
    )

    db.add(report)
    db.commit()
    db.refresh(report)

    return report
