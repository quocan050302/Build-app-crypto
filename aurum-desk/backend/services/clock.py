import time
from abc import ABC, abstractmethod
from datetime import datetime
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

class IClock(ABC):
    @abstractmethod
    def now_ms(self) -> int:
        """Returns current timestamp in milliseconds"""
        pass

    @abstractmethod
    def now_datetime(self) -> datetime:
        """Returns current timezone-aware datetime in Asia/Ho_Chi_Minh (UTC+7)"""
        pass

    def get_today_str_vn(self) -> str:
        """Returns YYYY-MM-DD string in Asia/Ho_Chi_Minh timezone"""
        return self.now_datetime().strftime("%Y-%m-%d")


class LiveClock(IClock):
    """Real system wall clock used for live paper trading"""
    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def now_datetime(self) -> datetime:
        return datetime.now(VN_TZ)


class ReplayClock(IClock):
    """Controllable event-driven clock used for Replay & Lab testing"""
    def __init__(self, initial_ms: int):
        self._current_ms = initial_ms

    def set_time(self, ms: int):
        self._current_ms = ms

    def advance_by(self, delta_ms: int):
        self._current_ms += delta_ms

    def now_ms(self) -> int:
        return self._current_ms

    def now_datetime(self) -> datetime:
        return datetime.fromtimestamp(self._current_ms / 1000.0, tz=VN_TZ)


# Default system singleton clock
live_clock = LiveClock()
FakeClock = ReplayClock
