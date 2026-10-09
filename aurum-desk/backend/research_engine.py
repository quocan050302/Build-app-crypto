import time
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
import models, crud, smc_engine
from services.collector_service import collector_service

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TOKYO_TZ = ZoneInfo("Asia/Tokyo")
LONDON_TZ = ZoneInfo("Europe/London")
NY_TZ = ZoneInfo("America/New_York")

def get_current_session_info() -> Dict[str, Any]:
    """
    Determine active trading sessions based on IANA timezones:
    - Tokyo: 09:00 - 18:00 Asia/Tokyo
    - London: 08:00 - 17:00 Europe/London
    - New York: 08:00 - 17:00 America/New_York
    """
    now_utc = datetime.now(ZoneInfo("UTC"))
    
    tokyo_dt = now_utc.astimezone(TOKYO_TZ)
    london_dt = now_utc.astimezone(LONDON_TZ)
    ny_dt = now_utc.astimezone(NY_TZ)
    vn_dt = now_utc.astimezone(VN_TZ)

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
        "active_sessions": active_names or ["Giữa các phiên (Inter-session)"],
        "tokyo": {"time": tokyo_dt.strftime("%H:%M"), "active": tokyo_active},
        "london": {"time": london_dt.strftime("%H:%M"), "active": london_active},
        "new_york": {"time": ny_dt.strftime("%H:%M"), "active": ny_active}
    }


def generate_research_report(
    db: Session,
    report_type: str = "SESSION_REPORT",
    candles_15m: Optional[list] = None
) -> models.ResearchReport:
    """
    Generate deep research report for XAUUSDT with authentic multi-timeframe SMC evidence:
    Independent Daily, 4H, 1H, and 15M context, with Bullish, Bearish, and No-Trade scenarios.
    """
    now_ms = int(time.time() * 1000)
    session_info = get_current_session_info()
    session_name = " & ".join(session_info["active_sessions"])

    if not candles_15m:
        candles_15m = crud.get_candles(db, "XAUUSDT", "15M", limit=100)

    # Independent multi-timeframe analysis
    d_bias = collector_service.d_bias
    h4_bias = collector_service.h4_bias
    htf_bias = collector_service.d_4h_bias
    h1_align = collector_service.h1_alignment

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

    # Scenarios
    scenarios = {
        "bullish": {
            "title": "Kịch bản Tăng (Bullish Continuation / Reversal)",
            "condition": f"Giá giữ vững vùng hỗ trợ {sl:.2f} hoặc quét râu đáy (Sweep) rồi đóng nến rút chân trên 15M kèm Displacement và FVG retest",
            "trigger_level": round(curr_p + 0.5 * atr, 2),
            "target": round(sh, 2),
            "invalidation": round(sl - 1.0 * atr, 2),
            "expiry": "Hết phiên hiện tại"
        },
        "bearish": {
            "title": "Kịch bản Giảm (Bearish Rejection)",
            "condition": f"Giá kiểm tra lại kháng cự {sh:.2f} ở vùng {zone} nhưng không đóng cửa vượt qua được, xuất hiện MSS giảm",
            "trigger_level": round(curr_p - 0.5 * atr, 2),
            "target": round(sl, 2),
            "invalidation": round(sh + 1.0 * atr, 2),
            "expiry": "Hết phiên hiện tại"
        },
        "no_trade": {
            "title": "Kịch bản Chờ Đợi (No-Trade Default)",
            "reason": "Khi thị trường chưa có chuỗi Liquidity Sweep + MSS + FVG đầy đủ, ngoài cửa sổ phiên Mỹ (08:00-11:00 NY), hoặc trước thềm tin tức USD High Impact",
            "action": "Bảo toàn vốn giả lập, tuân thủ nguyên tắc tối đa 3 lệnh/ngày và mục tiêu 1 lệnh phiên Mỹ"
        }
    }

    htf_status_desc = f"D: {d_bias} | 4H: {h4_bias} (Tổng hợp HTF: {htf_bias})"
    if htf_bias == "CONFLICT":
        htf_status_desc += " [Cảnh báo: D và 4H đang xung đột hướng]"

    markdown_content = f"""# BÁO CÁO PHÂN TÍCH XAUUSDT — {report_type}
**Thời gian tạo:** {session_info['vn_time']} (UTC+7)  
**Phiên giao dịch:** {session_name}  
**Giá hiện tại:** ${curr_p:.2f} | **ATR (14):** ${atr:.2f}

---

### 1. Bối cảnh Cấu trúc Đa Khung Thời Gian (Độc Lập)
- **Bối cảnh Daily (D):** {d_bias}
- **Xu hướng 4H:** {h4_bias}
- **Tổng hợp Bias HTF:** {htf_bias}
- **Đồng thuận 1H (H1 Alignment):** {h1_align}
- **Cấu trúc khung theo dõi (15M):** {trend_15m}
- **Vị thế Dealing Range:** {zone} (Equilibrium tại ${smc_res['equilibrium']:.2f})
- **Kháng cự Swing High:** ${sh:.2f}
- **Hỗ trợ Swing Low:** ${sl:.2f}
- **Sự kiện cấu trúc gần nhất:** {smc_res['last_event']}

### 2. Vùng Thanh Khoản & POI (Fair Value Gaps)
- Số lượng FVG còn hiệu lực: {len(smc_res['active_fvgs'])}
- Thanh khoản mục tiêu thực tế: Đỉnh ${sh:.2f} / Đáy ${sl:.2f}
- Trạng thái Setup SMC V7: {smc_res['setup_stage']} ({smc_res['reason_code']})

### 3. Kịch Bản Giao Dịch
- **Tăng (Bullish):** {scenarios['bullish']['condition']} -> Mục tiêu ${scenarios['bullish']['target']:.2f}.
- **Giảm (Bearish):** {scenarios['bearish']['condition']} -> Mục tiêu ${scenarios['bearish']['target']:.2f}.
- **Chờ đợi (No-Trade):** {scenarios['no_trade']['reason']} -> {scenarios['no_trade']['action']}.
"""

    report = models.ResearchReport(
        created_at=now_ms,
        title=f"Phân Tích XAUUSDT {session_info['vn_time']}",
        report_type=report_type,
        session=session_name,
        htf_bias=htf_bias,
        h1_alignment=h1_align,
        current_price=curr_p,
        atr=atr,
        swing_high=sh,
        swing_low=sl,
        equilibrium=smc_res["equilibrium"],
        content_markdown=markdown_content,
        scenarios_json=json.dumps(scenarios)
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report
