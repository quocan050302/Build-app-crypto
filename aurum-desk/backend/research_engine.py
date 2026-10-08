import time
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
import models, crud, smc_engine

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
    Generate deep research report for XAUUSDT with SMC evidence and scenarios:
    Bullish, Bearish, and No-Trade.
    """
    now_ms = int(time.time() * 1000)
    session_info = get_current_session_info()
    session_name = " & ".join(session_info["active_sessions"])

    if not candles_15m:
        candles_15m = crud.get_candles(db, "XAUUSDT", "15M", limit=100)

    smc_res = smc_engine.evaluate_smc_setup(candles_15m, "XAUUSDT", "15M")
    curr_p = smc_res["current_price"]
    atr = smc_res["atr"]
    sh = smc_res["swing_high"]
    sl = smc_res["swing_low"]
    trend = smc_res["trend"]
    zone = smc_res["zone"]

    # Bullish scenario
    scenarios = {
        "bullish": {
            "title": f"Kịch bản Tăng (Bullish Continuation / Reversal)",
            "condition": f"Giá giữ vững vùng hỗ trợ {sl:.2f} hoặc quét râu đáy (Sweep) rồi đóng nến rút chân trên 15M",
            "trigger_level": round(curr_p + 0.5 * atr, 2),
            "target": round(max(sh, curr_p + 2.5 * atr), 2),
            "invalidation": round(sl - 1.0 * atr, 2),
            "expiry": "Hết phiên hiện tại"
        },
        "bearish": {
            "title": f"Kịch bản Giảm (Bearish Rejection)",
            "condition": f"Giá kiểm tra lại kháng cự {sh:.2f} ở vùng {zone} nhưng không đóng cửa vượt qua được",
            "trigger_level": round(curr_p - 0.5 * atr, 2),
            "target": round(min(sl, curr_p - 2.5 * atr), 2),
            "invalidation": round(sh + 1.0 * atr, 2),
            "expiry": "Hết phiên hiện tại"
        },
        "no_trade": {
            "title": "Kịch bản Chờ Đợi (No-Trade Default)",
            "reason": "Khi giá di chuyển lưng chừng giữa dealing range hoặc trước thềm tin tức vĩ mô USD High Impact",
            "action": "Bảo toàn vốn giả lập, tuân thủ nguyên tắc không quá 3 lệnh/ngày"
        }
    }

    markdown_content = f"""# BÁO CÁO PHÂN TÍCH XAUUSDT — {report_type}
**Thời gian tạo:** {session_info['vn_time']} (UTC+7)  
**Phiên giao dịch:** {session_name}  
**Giá hiện tại:** ${curr_p:.2f} | **ATR (14):** ${atr:.2f}

---

### 1. Bối cảnh Cấu trúc Đa Khung
- **Xu hướng chủ đạo (D/4H/1H):** {trend}
- **Vị thế Dealing Range:** {zone} (Cân bằng tại ${smc_res['equilibrium']:.2f})
- **Kháng cự Swing High:** ${sh:.2f}
- **Hỗ trợ Swing Low:** ${sl:.2f}
- **Sự kiện cấu trúc gần nhất:** {smc_res['last_event']}

### 2. Vùng Thanh Khoản & POI (Fair Value Gaps)
- Số lượng FVG còn hiệu lực: {len(smc_res['active_fvgs'])}
- Thanh khoản mục tiêu: Đỉnh ${sh:.2f} / Đáy ${sl:.2f}

### 3. Kịch Bản Giao Dịch
- **Tăng (Bullish):** Kích hoạt nếu giá phản ứng tích cực tại vùng Discount quanh ${sl:.2f}, mục tiêu hướng về ${scenarios['bullish']['target']:.2f}.
- **Giảm (Bearish):** Kích hoạt nếu giá quét thanh khoản tại đỉnh ${sh:.2f} và hình thành CHoCH trên khung 5M/1M, mục tiêu về ${scenarios['bearish']['target']:.2f}.
- **Chờ đợi (No-Trade):** Tuyệt đối không giao dịch khi chưa có Liquidity Sweep hoặc trong khung giờ Blackout tin tức.
"""

    report_record = {
        "report_type": report_type,
        "created_at": now_ms,
        "session_name": session_name,
        "d_4h_bias": trend,
        "h1_alignment": "CONSENSUS",
        "m15_pois": json.dumps(smc_res["active_fvgs"]),
        "liquidity_levels": json.dumps(smc_res["liquidity_levels"]),
        "scenarios": json.dumps(scenarios),
        "content_markdown": markdown_content,
        "strategy_version": "1.0.0"
    }

    return crud.save_research_report(db, report_record)
