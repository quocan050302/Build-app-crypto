"""
Aurum Desk V13 — Immutable Research Context & Time Engine.
Guarantees strict causal information availability:
- No leakage from future candles
- No leakage from live collector_service singleton
- Explicit timezones: Asia/Ho_Chi_Minh (VN_DATE), America/New_York (NY_SESSION_DATE)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NY_TZ = ZoneInfo("America/New_York")
LONDON_TZ = ZoneInfo("Europe/London")
TOKYO_TZ = ZoneInfo("Asia/Tokyo")
UTC_TZ = ZoneInfo("UTC")


class ResearchMode:
    CURRENT_ASOF = "CURRENT_ASOF"
    HISTORICAL_ASOF = "HISTORICAL_ASOF"
    POST_SESSION_REVIEW = "POST_SESSION_REVIEW"


class DateBasis:
    VN_DATE = "VN_DATE"
    NY_SESSION_DATE = "NY_SESSION_DATE"


class SessionType:
    ALL = "ALL"
    TOKYO = "TOKYO"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"


@dataclass(frozen=True)
class ResearchContext:
    symbol: str = "XAUUSDT"
    mode: str = ResearchMode.CURRENT_ASOF
    selected_date: str = ""                # YYYY-MM-DD
    date_basis: str = DateBasis.VN_DATE    # VN_DATE or NY_SESSION_DATE
    selected_session: str = SessionType.NEW_YORK
    as_of_ms: int = 0                      # UTC epoch ms: information strictly capped at or before this
    data_available_until_ms: int = 0       # Must satisfy data_available_until_ms <= as_of_ms
    captured_at_ms: int = 0                # Time when this report calculation was invoked
    display_timezone: str = "Asia/Ho_Chi_Minh"
    config_hash: Optional[str] = None
    dataset_hash: Optional[str] = None
    news_hash: Optional[str] = None
    rules_hash: Optional[str] = None
    cost_model_version: str = "v13.0"
    limitations: List[str] = field(default_factory=list)

    def is_historical(self) -> bool:
        return self.mode in (ResearchMode.HISTORICAL_ASOF, ResearchMode.POST_SESSION_REVIEW)

    def as_of_dt_utc(self) -> datetime:
        return datetime.fromtimestamp(self.as_of_ms / 1000.0, tz=timezone.utc)

    def as_of_dt_vn(self) -> datetime:
        return self.as_of_dt_utc().astimezone(VN_TZ)

    def as_of_dt_ny(self) -> datetime:
        return self.as_of_dt_utc().astimezone(NY_TZ)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mode": self.mode,
            "selected_date": self.selected_date,
            "date_basis": self.date_basis,
            "selected_session": self.selected_session,
            "as_of_ms": self.as_of_ms,
            "as_of_vn": self.as_of_dt_vn().strftime("%H:%M %d/%m/%Y"),
            "as_of_ny": self.as_of_dt_ny().strftime("%H:%M %Y-%m-%d"),
            "data_available_until_ms": self.data_available_until_ms,
            "captured_at_ms": self.captured_at_ms,
            "display_timezone": self.display_timezone,
            "config_hash": self.config_hash,
            "dataset_hash": self.dataset_hash,
            "news_hash": self.news_hash,
            "rules_hash": self.rules_hash,
            "cost_model_version": self.cost_model_version,
            "limitations": list(self.limitations)
        }


def build_research_context(
    selected_date: Optional[str] = None,
    time_of_day: Optional[str] = None,        # HH:MM
    session: Optional[str] = None,
    date_basis: Optional[str] = None,
    mode: Optional[str] = None,
    now_ms: Optional[int] = None
) -> ResearchContext:
    """
    Factory function to construct validated, immutable ResearchContext.
    """
    if now_ms is None:
        import time
        now_ms = int(time.time() * 1000)

    now_utc = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    now_vn = now_utc.astimezone(VN_TZ)

    resolved_date_basis = date_basis or DateBasis.VN_DATE
    resolved_session = session or SessionType.NEW_YORK

    # Determine mode
    if selected_date:
        today_vn_str = now_vn.strftime("%Y-%m-%d")
        if selected_date < today_vn_str:
            resolved_mode = mode or ResearchMode.HISTORICAL_ASOF
        elif selected_date == today_vn_str:
            resolved_mode = mode or ResearchMode.CURRENT_ASOF
        else:
            resolved_mode = ResearchMode.HISTORICAL_ASOF
    else:
        resolved_date = now_vn.strftime("%Y-%m-%d")
        resolved_mode = mode or ResearchMode.CURRENT_ASOF
        selected_date = resolved_date

    # Resolve as_of_ms
    limitations = []
    if resolved_mode == ResearchMode.CURRENT_ASOF:
        as_of_ms = now_ms
    else:
        # For historical date, resolve based on date and time_of_day or session default
        try:
            date_parts = [int(p) for p in selected_date.split("-")]
            if time_of_day:
                hour_str, min_str = time_of_day.split(":")
                hour = int(hour_str)
                minute = int(min_str)
            else:
                # Default session premarket / active entry time
                if resolved_session == SessionType.NEW_YORK:
                    # 08:00 NY time
                    hour, minute = 8, 0
                elif resolved_session == SessionType.LONDON:
                    hour, minute = 8, 0
                elif resolved_session == SessionType.TOKYO:
                    hour, minute = 9, 0
                else:
                    hour, minute = 14, 0

            if resolved_date_basis == DateBasis.NY_SESSION_DATE or (time_of_day is None and resolved_session == SessionType.NEW_YORK):
                target_dt = datetime(date_parts[0], date_parts[1], date_parts[2], hour, minute, 0, tzinfo=NY_TZ)
            else:
                target_dt = datetime(date_parts[0], date_parts[1], date_parts[2], hour, minute, 0, tzinfo=VN_TZ)

            as_of_ms = int(target_dt.astimezone(timezone.utc).timestamp() * 1000)
            if as_of_ms > now_ms:
                limitations.append("THỜI_ĐIỂM_TƯƠNG_LAI: Thời điểm chọn chưa diễn ra. Đã tự động điều chỉnh về hiện tại.")
                as_of_ms = now_ms
        except Exception:
            as_of_ms = now_ms
            limitations.append("LỖI_ĐỊNH_DẠNG_NGÀY: Không thể phân tích ngày. Sử dụng thời điểm hiện tại.")

    return ResearchContext(
        symbol="XAUUSDT",
        mode=resolved_mode,
        selected_date=selected_date,
        date_basis=resolved_date_basis,
        selected_session=resolved_session,
        as_of_ms=as_of_ms,
        data_available_until_ms=as_of_ms,
        captured_at_ms=now_ms,
        display_timezone="Asia/Ho_Chi_Minh",
        limitations=limitations
    )
