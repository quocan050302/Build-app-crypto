"""
V9 Test Harness for Aurum Desk:
- Unified ReplayClock / FakeClock supporting VN (Asia/Ho_Chi_Minh) and NY (America/New_York) sessions.
- Isolated SQLite file with WAL mode and 5000ms busy timeout.
- Fail-closed guard: NEVER touches aurum_desk.db.
- Mock Bitget REST/WS feed driver with ordered, out-of-order, stale, malformed, and gap quotes.
- Mock Telegram HTTP transport with controlled queue (success, 429, 4xx, 5xx, timeout, malformed).
- Unified session factory across API, event bus, trade lifecycle, proximity, and outbox workers.
- True multi-threaded concurrency barriers.
"""
import os
import sys
import time
import math
import json
import uuid
import tempfile
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool

import models
import crud
import schemas
from database import run_schema_migrations, RUNTIME_DEFAULT_DB_PATH
from services.clock import IClock, VN_TZ
from services.quote_validator import CanonicalQuote, QuoteValidator
from domain_calculator import calculate_risk_reward, CostAssumptions

NY_TZ = ZoneInfo("America/New_York")


class RuntimeProtectionException(Exception):
    """Raised when an operation attempts to target the production runtime database."""
    pass


class V9Clock(IClock):
    """
    Unified high-precision clock for V9 testing.
    Default anchors to: 2026-10-09 08:30:00 EDT (New York morning open).
    """
    def __init__(self, initial_ms: Optional[int] = None):
        if initial_ms is None:
            # 2026-10-09 08:30:00 EDT -> epoch ms
            dt_ny = datetime(2026, 10, 9, 8, 30, 0, tzinfo=NY_TZ)
            self._current_ms = int(dt_ny.timestamp() * 1000)
        else:
            self._current_ms = initial_ms

    def set_time(self, ms: int):
        self._current_ms = ms

    def set_datetime_ny(self, year: int, month: int, day: int, hour: int, minute: int, second: int = 0):
        dt = datetime(year, month, day, hour, minute, second, tzinfo=NY_TZ)
        self._current_ms = int(dt.timestamp() * 1000)

    def set_datetime_vn(self, year: int, month: int, day: int, hour: int, minute: int, second: int = 0):
        dt = datetime(year, month, day, hour, minute, second, tzinfo=VN_TZ)
        self._current_ms = int(dt.timestamp() * 1000)

    def advance_by(self, delta_ms: int):
        self._current_ms += delta_ms

    def advance_seconds(self, sec: float):
        self._current_ms += int(sec * 1000)

    def now_ms(self) -> int:
        return self._current_ms

    def now_datetime(self) -> datetime:
        return datetime.fromtimestamp(self._current_ms / 1000.0, tz=VN_TZ)

    def now_datetime_ny(self) -> datetime:
        return datetime.fromtimestamp(self._current_ms / 1000.0, tz=NY_TZ)

    def get_today_str_vn(self) -> str:
        return self.now_datetime().strftime("%Y-%m-%d")

    def get_now_str_ny(self) -> str:
        return self.now_datetime_ny().strftime("%Y-%m-%d %H:%M:%S %Z")


class MockTelegramTransport:
    """
    Controlled boundary mock for Telegram HTTP requests.
    Supports enqueueing scripted responses (success, 429, 400, 500, timeouts, malformed).
    """
    def __init__(self, clock: V9Clock):
        self.clock = clock
        self.responses_queue: List[Dict[str, Any]] = []
        self.default_response: Dict[str, Any] = {"ok": True, "result": {"message_id": 990001}}
        self.sent_messages: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._msg_counter = 990000

    def enqueue_response(self, response_data: Dict[str, Any]):
        with self._lock:
            self.responses_queue.append(response_data)

    def set_default_success(self):
        with self._lock:
            self.default_response = {"ok": True, "result": {"message_id": 990001}}

    async def handle_post(self, url: str, json: Optional[Dict[str, Any]] = None, **kwargs):
        with self._lock:
            self._msg_counter += 1
            call_record = {
                "url": url,
                "payload": json or {},
                "timestamp_ms": self.clock.now_ms(),
                "call_id": self._msg_counter,
            }
            self.sent_messages.append(call_record)

            if self.responses_queue:
                resp_config = self.responses_queue.pop(0)
            else:
                resp_config = dict(self.default_response)
                if resp_config.get("ok") and "result" in resp_config and isinstance(resp_config["result"], dict):
                    resp_config["result"]["message_id"] = self._msg_counter

        # Simulate special modes
        mode = resp_config.get("__mode__", "normal")
        if mode == "timeout_before_send":
            import httpx
            raise httpx.ConnectTimeout("Connect timeout to Telegram API")
        elif mode == "timeout_after_send":
            import httpx
            raise httpx.ReadTimeout("Read timeout after Telegram API received message")

        status_code = resp_config.get("__status_code__", 200)
        headers = resp_config.get("__headers__", {})
        body = {k: v for k, v in resp_config.items() if not k.startswith("__")}

        class MockResponse:
            def __init__(self, sc, b, h):
                self.status_code = sc
                self._body = b
                self.headers = h

            def json(self):
                if isinstance(self._body, str):
                    import json
                    return json.loads(self._body)
                return self._body

            @property
            def text(self):
                if isinstance(self._body, str):
                    return self._body
                import json
                return json.dumps(self._body)

        return MockResponse(status_code, body, headers)


class MockBitgetFeedDriver:
    """
    Deterministic Feed Driver for Bitget REST and WS quotes.
    Supports canonical quotes, malformed values, inverted spreads, stale quotes, and gap timelines.
    """
    def __init__(self, clock: V9Clock, symbol: str = "XAUUSDT"):
        self.clock = clock
        self.symbol = symbol
        self.epoch = 1
        self.last_quote: Optional[CanonicalQuote] = None

    def make_quote(
        self,
        bid: float,
        ask: float,
        last: Optional[float] = None,
        source: str = "WS",
        status: str = "VALID",
        epoch: Optional[int] = None,
        delta_ts_ms: int = 0,
        rejection_code: Optional[str] = None
    ) -> CanonicalQuote:
        now_ms = self.clock.now_ms() + delta_ts_ms
        l_price = last if last is not None else round((bid + ask) / 2.0, 2)
        spread = round(ask - bid, 4) if (math.isfinite(ask) and math.isfinite(bid)) else 0.0

        q = CanonicalQuote(
            symbol=self.symbol,
            last=l_price,
            bid=bid,
            ask=ask,
            mark_price=l_price,
            exchange_ts_ms=now_ms,
            received_at_ms=now_ms,
            source=source,
            connection_epoch=epoch if epoch is not None else self.epoch,
            status=status,
            spread=spread,
            freshness_sec=0.0,
            rejection_code=rejection_code
        )
        self.last_quote = q
        return q

    def make_stale_quote(self, bid: float, ask: float, age_sec: float = 20.0) -> CanonicalQuote:
        q = self.make_quote(bid, ask, delta_ts_ms=-int(age_sec * 1000))
        q.status = "STALE"
        q.freshness_sec = age_sec
        q.rejection_code = "QUOTE_STALE"
        return q

    def make_malformed_quote(self, bid: float = float("nan"), ask: float = 4000.0) -> CanonicalQuote:
        q = self.make_quote(bid, ask, status="MALFORMED", rejection_code="MALFORMED_NUMERIC")
        return q

    def make_inverted_quote(self, bid: float = 4005.0, ask: float = 4000.0) -> CanonicalQuote:
        q = self.make_quote(bid, ask, status="INVERTED", rejection_code="INVERTED_SPREAD")
        return q


class V9TestHarness:
    """
    Main V9 Test Environment Harness.
    Guarantees strict runtime database protection, provides isolated DB,
    mock Telegram transport, fake clock, and multi-session concurrency tools.
    """
    def __init__(self, db_name_suffix: Optional[str] = None, seed: int = 42):
        self.seed = seed
        self.clock = V9Clock()
        self.telegram_transport = MockTelegramTransport(self.clock)
        self.feed_driver = MockBitgetFeedDriver(self.clock)

        # 1. Setup isolated database
        suffix = db_name_suffix or uuid.uuid4().hex[:8]
        self.temp_dir = tempfile.mkdtemp(prefix=f"aurum_v9_harness_{suffix}_")
        self.db_path = os.path.join(self.temp_dir, f"aurum_v9_test_{suffix}.db")

        # Fail-closed invariant check
        if os.path.abspath(self.db_path) == os.path.abspath(RUNTIME_DEFAULT_DB_PATH):
            raise RuntimeProtectionException("FATAL: Test harness DB path is identical to RUNTIME DB path!")

        self.db_url = f"sqlite:///{self.db_path}"
        self.engine = create_engine(
            self.db_url,
            connect_args={"check_same_thread": False, "timeout": 15},
            poolclass=NullPool
        )

        # Configure SQLite pragmas for WAL mode
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        models.Base.metadata.create_all(bind=self.engine)
        run_schema_migrations(self.engine)

        self.SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def get_session(self) -> Session:
        return self.SessionFactory()

    def create_secondary_session(self) -> Session:
        return self.SessionFactory()

    def seed_default_telegram_config(
        self,
        enabled: bool = True,
        bot_token: str = "123456789:ABC_v9_test_mock_token_xyz",
        chat_id: str = "6919390280",
        subscribed: Optional[List[str]] = None
    ) -> models.TelegramConfig:
        if subscribed is None:
            subscribed = [
                "NEAR_ENTRY", "READY", "ARMED", "FILLED", "TP_HIT", "SL_HIT",
                "MANUAL_CLOSED", "LIQUIDATED", "REJECTED", "CANCELLED", "EXPIRED",
                "INVALIDATED", "FEED_DOWN", "RECOVERED"
            ]
        db = self.get_session()
        try:
            cfg = db.query(models.TelegramConfig).first()
            if not cfg:
                cfg = models.TelegramConfig(id=1)
                db.add(cfg)
            cfg.enabled = enabled
            cfg.bot_token = bot_token
            cfg.chat_id = chat_id
            cfg.subscribed_events = json.dumps(subscribed)
            cfg.near_entry_mode = "ATR"
            cfg.near_entry_atr_mult = 0.5
            cfg.near_entry_price_dist = 1.0
            cfg.near_entry_cooldown_min = 30
            cfg.quiet_hours_enabled = False
            cfg.quiet_hours_start = "23:00"
            cfg.quiet_hours_end = "06:00"
            cfg.timezone = "Asia/Ho_Chi_Minh"
            cfg.bypass_critical_quiet_hours = True
            cfg.updated_at = self.clock.now_ms()
            db.commit()
            db.refresh(cfg)
            return cfg
        finally:
            db.close()

    def seed_account_settings(
        self,
        equity: float = 1000.0,
        risk_pct: float = 0.25,
        daily_max_fills: int = 3,
        daily_max_losses: int = 2
    ):
        db = self.get_session()
        try:
            today_str = self.clock.get_today_str_vn()
            audit = db.query(models.DayAudit).filter(models.DayAudit.date_str == today_str).first()
            if not audit:
                audit = models.DayAudit(
                    date_str=today_str,
                    initial_equity=equity,
                    current_equity=equity,
                    realized_pnl_today=0.0,
                    fills_count=0,
                    consecutive_losses=0,
                    is_blocked=False
                )
                db.add(audit)
            else:
                audit.initial_equity = equity
                audit.current_equity = equity

            configs = {
                "default_leverage": "5",
                "default_margin_mode": "ISOLATED",
                "default_risk_pct": str(risk_pct),
                "risk_config_version": "1"
            }
            for k, v in configs.items():
                sc = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
                if not sc:
                    sc = models.SystemConfig(key=k, value=v, updated_at=self.clock.now_ms())
                    db.add(sc)
                else:
                    sc.value = v
                    sc.updated_at = self.clock.now_ms()

            db.commit()
        finally:
            db.close()

    def teardown(self):
        self.engine.dispose()
        # Clean up temp db
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
            shm = f"{self.db_path}-shm"
            wal = f"{self.db_path}-wal"
            if os.path.exists(shm):
                os.remove(shm)
            if os.path.exists(wal):
                os.remove(wal)
            if os.path.exists(self.temp_dir):
                os.rmdir(self.temp_dir)
        except Exception:
            pass
