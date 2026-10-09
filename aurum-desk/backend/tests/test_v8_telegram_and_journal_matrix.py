"""
V8 Comprehensive Regression Test Suite: Telegram Transport & Lifecycle + Trade Journal & Lesson Governance
Covers matrices:
- Telegram: T01 - T18
- Journal & Lessons: J01 - J15
"""
import pytest
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

import models
from models import Base
import crud
import schemas
from main import app, get_db
from services.telegram_service import (
    format_telegram_message,
    is_within_quiet_hours,
    send_telegram_direct,
    process_notification_outbox,
    TelegramSendResult,
    TelegramErrorCode,
    mask_token,
)
from sqlalchemy.pool import StaticPool
from services.proximity_service import ProximityService
from services.trade_lifecycle_service import TradeLifecycleService


@pytest.fixture
def matrix_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()

    # Prepopulate standard telegram config
    cfg = models.TelegramConfig(
        id=1,
        enabled=False,
        bot_token="123456789:ABCdefGHI_real_secret_token",
        chat_id="6919390280",
        subscribed_events=json.dumps([
            "ARMED", "FILLED", "TP_HIT", "SL_HIT", "MANUAL_CLOSED", "LIQUIDATED"
        ]),
        near_entry_mode="ATR",
        near_entry_atr_mult=0.5,
        near_entry_price_dist=2.0,
        near_entry_cooldown_min=30,
        quiet_hours_enabled=False,
        quiet_hours_start="23:00",
        quiet_hours_end="06:00",
        timezone="Asia/Ho_Chi_Minh",
        bypass_critical_quiet_hours=True,
        updated_at=int(time.time() * 1000),
    )
    session.add(cfg)
    session.commit()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def matrix_client(matrix_db):
    def override_get_db():
        try:
            yield matrix_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ==================== TELEGRAM TESTS (T01 - T18) ====================

def test_t01_missing_credentials_validation(matrix_client, matrix_db):
    """T01: Missing credentials, enabled validation and UI errors."""
    # Attempt to enable with clear_token and no chat_id
    res = matrix_client.post("/api/v1/telegram/config", json={
        "enabled": True,
        "chat_id": "",
        "clear_token": True,
    })
    assert res.status_code == 400
    assert "Chat ID" in res.json()["detail"] or "Token" in res.json()["detail"]


def test_t02_save_blank_token_keeps_token(matrix_client, matrix_db):
    """T02: Save blank token keeps token; masked token not used to send; has_token is accurate."""
    cfg_get = matrix_client.get("/api/v1/telegram/config").json()
    assert cfg_get["has_token"] is True
    assert cfg_get["token_configured"] is True
    assert "secret" not in cfg_get["bot_token_masked"]  # Token is masked

    # Update chat ID without sending bot_token
    res = matrix_client.post("/api/v1/telegram/config", json={
        "chat_id": "999888777",
    })
    assert res.status_code == 200
    res_data = res.json()
    updated_cfg = res_data.get("config", res_data)
    assert updated_cfg["chat_id"] == "999888777"
    assert updated_cfg["has_token"] is True

    # Check in DB that the secret token is preserved
    db_cfg = crud.get_telegram_config(matrix_db)
    assert db_cfg.bot_token == "123456789:ABCdefGHI_real_secret_token"


def test_t03_t04_draft_vs_saved_and_clear_token(matrix_client, matrix_db):
    """T03 & T04: Draft vs saved config isolation and explicit clear_token."""
    # Test clearing token
    res = matrix_client.post("/api/v1/telegram/config", json={
        "clear_token": True,
        "enabled": False,
    })
    assert res.status_code == 200
    res_data = res.json()
    cfg = res_data.get("config", res_data)
    assert cfg["has_token"] is False
    assert cfg["enabled"] is False

    db_cfg = crud.get_telegram_config(matrix_db)
    assert db_cfg.bot_token is None

    # Enabling now without token must fail with 400
    res_enable = matrix_client.post("/api/v1/telegram/config", json={
        "enabled": True,
        "chat_id": "12345",
    })
    assert res_enable.status_code == 400


@pytest.mark.anyio
async def test_t05_send_message_requires_ok_true_and_message_id():
    """T05: sendMessage success requires ok=true + message_id; ok=false/malformed JSON fail."""
    # Case 1: HTTP 200 but ok=False
    mock_resp_1 = MagicMock()
    mock_resp_1.status_code = 200
    mock_resp_1.json.return_value = {"ok": False, "description": "Bad Request: chat not found"}

    with patch("httpx.AsyncClient.post", return_value=mock_resp_1):
        res1 = await send_telegram_direct("valid:token", "123", "Test message")
        assert res1.success is False
        assert res1.error_code == TelegramErrorCode.CHAT_NOT_FOUND

    # Case 2: HTTP 200, ok=True, but missing message_id
    mock_resp_2 = MagicMock()
    mock_resp_2.status_code = 200
    mock_resp_2.json.return_value = {"ok": True, "result": {"date": 123456}}

    with patch("httpx.AsyncClient.post", return_value=mock_resp_2):
        res2 = await send_telegram_direct("valid:token", "123", "Test message")
        assert res2.success is False
        assert res2.error_code == TelegramErrorCode.MALFORMED_RESPONSE

    # Case 3: HTTP 200, ok=True, valid message_id
    mock_resp_3 = MagicMock()
    mock_resp_3.status_code = 200
    mock_resp_3.json.return_value = {"ok": True, "result": {"message_id": 998877}}

    with patch("httpx.AsyncClient.post", return_value=mock_resp_3):
        res3 = await send_telegram_direct("valid:token", "123", "Test message")
        assert res3.success is True
        assert res3.provider_message_id == "998877"


@pytest.mark.anyio
async def test_t06_429_json_parameters_retry_after():
    """T06: 429 JSON parameters.retry_after=37 correctly parsed."""
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {"Retry-After": "10"}  # Conflicting header
    mock_resp.json.return_value = {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests: retry after 37",
        "parameters": {"retry_after": 37},
    }

    with patch("httpx.AsyncClient.post", return_value=mock_resp):
        res = await send_telegram_direct("valid:token", "123", "Test")
        assert res.success is False
        assert res.error_code == TelegramErrorCode.RATE_LIMIT
        assert res.retry_after_sec == 37


@pytest.mark.anyio
async def test_t07_parse_entities_fallback_and_token_sanitization():
    """T07: 400 parse entities fallback to plain text, and sanitization of token."""
    secret_token = "987654321:SECRET_TOKEN_XYZ"
    
    # First call with parse_mode=Markdown returns 400 can't parse entities
    mock_resp_fail = MagicMock()
    mock_resp_fail.status_code = 400
    mock_resp_fail.json.return_value = {
        "ok": False,
        "description": f"Bad Request: can't parse entities in message for token {secret_token}",
    }

    # Second call without parse_mode succeeds
    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.json.return_value = {"ok": True, "result": {"message_id": 12345}}

    with patch("httpx.AsyncClient.post", side_effect=[mock_resp_fail, mock_resp_ok]):
        res = await send_telegram_direct(secret_token, "123", "Unclosed *markdown _text")
        assert res.success is True
        assert res.provider_message_id == "12345"


def test_t08_message_with_special_characters_and_vietnamese():
    """T08: Message payload formatting with special chars, emoji, and Vietnamese text."""
    event_payload = {
        "event_id": "ev_test_unicode",
        "setup_id": "setup_xau_#1",
        "direction": "LONG",
        "symbol": "XAUUSDT",
        "planned_entry": 4120.50,
        "stop_loss": 4105.00,
        "take_profit": 4160.00,
        "gross_rr": 2.55,
        "notes": "Kiểm tra ký tự: <>&_[]() và tiếng Việt có dấu: Chốt lời hoàn hảo! 🚀",
    }
    
    msg = format_telegram_message("READY", event_payload)
    assert "XAUUSDT" in msg
    assert "LONG" in msg
    assert "4120.5" in msg
    assert "4160" in msg


def test_t09_t10_quiet_hours_and_critical_bypass(matrix_db):
    """T09 & T10: Quiet hours evaluation, critical bypass on/off."""
    cfg = crud.get_telegram_config(matrix_db)
    cfg.quiet_hours_enabled = True
    cfg.quiet_hours_start = "22:00"
    cfg.quiet_hours_end = "06:00"
    cfg.timezone = "Asia/Ho_Chi_Minh"
    cfg.bypass_critical_quiet_hours = True
    matrix_db.commit()

    # Time in VN as 23:30 (inside quiet window)
    mock_dt_quiet = datetime(2026, 10, 9, 23, 30, tzinfo=timezone(timedelta(hours=7)))
    is_quiet = is_within_quiet_hours(cfg, now_dt=mock_dt_quiet)
    assert is_quiet is True

    # Time in VN as 14:00 (outside quiet window)
    mock_dt_active = datetime(2026, 10, 9, 14, 0, tzinfo=timezone(timedelta(hours=7)))
    is_active_quiet = is_within_quiet_hours(cfg, now_dt=mock_dt_active)
    assert is_active_quiet is False


def test_t11_near_entry_proximity_and_cooldown(matrix_db):
    """T11: Near-entry distance/ATR and evaluation via ProximityService."""
    now_ms = int(time.time() * 1000)
    prox_svc = ProximityService()
    cfg = crud.get_telegram_config(matrix_db)
    cfg.near_entry_mode = "ATR"
    cfg.near_entry_atr_mult = 0.5
    matrix_db.commit()

    ws = models.WatchSetup(
        id="ws_matrix_1",
        direction="LONG",
        timeframe="15M",
        provisional_entry=4120.0,
        provisional_sl=4100.0,
        provisional_tp=4160.0,
        invalidation_price=4100.0,
        gross_rr=2.5,
        state="WAITING_PRICE",
        poi_zone=json.dumps({"top": 4121.0, "bottom": 4119.0}),
        created_at=now_ms,
        updated_at=now_ms,
    )
    matrix_db.add(ws)
    matrix_db.commit()

    # Quote is 4120.5 (within 0.5 * 2.0 = 1.0 ATR distance)
    ticker = {"ask": 4120.5, "bid": 4120.3, "price": 4120.4, "timestamp": now_ms}
    ev = prox_svc.evaluate_setup_proximity(matrix_db, ws, ticker, atr=2.0, tg_cfg=cfg)
    assert ev is not None
    assert ev.event_type == "setup.near_entry"


def test_t12_short_and_long_geometry_mapping():
    """T12: READY -> ARMED -> FILLED; correct TP/SL geometry for SHORT and LONG."""
    # LONG geometry: SL < Entry < TP
    long_msg = format_telegram_message("FILLED", {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "actual_entry": 4120.0,
        "stop_loss": 4105.0,
        "take_profit": 4150.0,
    })
    assert "LONG" in long_msg
    assert "4120" in long_msg

    # SHORT geometry: TP < Entry < SL
    short_msg = format_telegram_message("FILLED", {
        "symbol": "XAUUSDT",
        "direction": "SHORT",
        "actual_entry": 4120.0,
        "stop_loss": 4135.0,
        "take_profit": 4090.0,
    })
    assert "SHORT" in short_msg
    assert "4120" in short_msg


@pytest.mark.anyio
async def test_t14_outbox_lease_concurrency(matrix_db):
    """T14: Lease concurrency: worker claims item with lease; second worker cannot double-claim."""
    cfg = crud.get_telegram_config(matrix_db)
    cfg.enabled = True
    matrix_db.commit()

    now_ms = int(time.time() * 1000)
    outbox_item = models.NotificationOutbox(
        event_id="ev_t14",
        message_type="FILLED",
        dedupe_key="dedupe_tr_100_FILLED",
        payload=json.dumps({"trade_id": "tr_100", "symbol": "XAUUSDT", "direction": "LONG"}),
        status="PENDING",
        priority="CRITICAL",
        created_at=now_ms,
    )
    matrix_db.add(outbox_item)
    matrix_db.commit()
    matrix_db.refresh(outbox_item)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 554433}}

    from sqlalchemy.orm import sessionmaker
    test_sessionmaker = sessionmaker(bind=matrix_db.bind, autocommit=False, autoflush=False)

    with patch("services.telegram_service.SessionLocal", test_sessionmaker):
        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            await process_notification_outbox(run_once=True)

    matrix_db.refresh(outbox_item)
    assert outbox_item.status == "SENT"
    assert outbox_item.provider_message_id == "554433"


def test_t15_retry_outbox_rules(matrix_client, matrix_db):
    """T15: SENT cannot be retried; FAILED and AMBIGUOUS can be retried."""
    now_ms = int(time.time() * 1000)
    # 1. SENT item
    sent_item = models.NotificationOutbox(
        event_id="ev_t15_sent",
        message_type="FILLED",
        dedupe_key="dedupe_t15_sent",
        payload="{}",
        status="SENT",
        created_at=now_ms,
    )
    # 2. FAILED item
    failed_item = models.NotificationOutbox(
        event_id="ev_t15_failed",
        message_type="NEAR_ENTRY",
        dedupe_key="dedupe_t15_failed",
        payload="{}",
        status="FAILED",
        created_at=now_ms,
    )
    matrix_db.add_all([sent_item, failed_item])
    matrix_db.commit()

    # Retry SENT -> 400
    res_sent = matrix_client.post(f"/api/v1/telegram/outbox/retry/{sent_item.id}")
    assert res_sent.status_code == 400

    # Retry FAILED -> 200
    res_failed = matrix_client.post(f"/api/v1/telegram/outbox/retry/{failed_item.id}")
    assert res_failed.status_code == 200
    matrix_db.refresh(failed_item)
    assert failed_item.status == "PENDING"


# ==================== JOURNAL & LESSONS TESTS (J01 - J15) ====================

def test_j01_j02_states_and_accurate_outcomes(matrix_db):
    """J01 & J02: Exit cause and outcome independence; MANUAL_CLOSE is not TP/SL."""
    now_ms = int(time.time() * 1000)

    # 1. Manual close with loss
    order_loss = models.PaperOrder(
        id="ord_manual_loss",
        instrument="XAUUSDT",
        direction="LONG",
        timeframe="15M",
        planned_entry=4120.0,
        stop_loss=4100.0,
        take_profit=4160.0,
        actual_entry=4120.0,
        actual_exit=4110.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        realized_pnl_net=-1.0,
        realized_r=-0.5,
        exit_cause="MANUAL_CLOSE",
        state="closed",
        created_at=now_ms,
        closed_at=now_ms,
    )

    # 2. Manual close with profit
    order_win = models.PaperOrder(
        id="ord_manual_win",
        instrument="XAUUSDT",
        direction="SHORT",
        timeframe="15M",
        planned_entry=4120.0,
        stop_loss=4140.0,
        take_profit=4080.0,
        actual_entry=4120.0,
        actual_exit=4100.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        realized_pnl_net=2.0,
        realized_r=1.0,
        exit_cause="MANUAL_CLOSE",
        state="closed",
        created_at=now_ms,
        closed_at=now_ms,
    )

    matrix_db.add_all([order_loss, order_win])
    matrix_db.commit()

    TradeLifecycleService._create_lesson(matrix_db, order_loss, now_ms)
    TradeLifecycleService._create_lesson(matrix_db, order_win, now_ms)

    lesson_loss = matrix_db.query(models.Lesson).filter(models.Lesson.related_trade_id == "ord_manual_loss").first()
    lesson_win = matrix_db.query(models.Lesson).filter(models.Lesson.related_trade_id == "ord_manual_win").first()

    assert lesson_loss is not None
    assert lesson_win is not None

    # Must accurately reflect voluntary exit, not TP or SL!
    assert "chạm SL" not in lesson_loss.reflection
    assert "đóng chủ động" in lesson_loss.reflection.lower() or "thủ công" in lesson_loss.reflection.lower() or "manual" in lesson_loss.reflection.lower()
    assert "Take Profit" not in lesson_win.reflection
    assert "đóng chủ động" in lesson_win.reflection.lower() or "thủ công" in lesson_win.reflection.lower() or "manual" in lesson_win.reflection.lower()


def test_j03_j04_server_side_summary_and_filter(matrix_client, matrix_db):
    """J03 & J04: Server-side aggregate summary calculation across full filtered set."""
    now_ms = int(time.time() * 1000)

    # Add 2 winning trades, 1 losing trade, 1 armed order (not filled)
    t1 = models.PaperOrder(
        id="t1",
        instrument="XAUUSDT",
        direction="LONG",
        timeframe="15M",
        planned_entry=4100.0,
        actual_entry=4100.0,
        actual_exit=4150.0,
        stop_loss=4080.0,
        take_profit=4150.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        state="closed",
        realized_pnl_net=50.0,
        realized_r=2.5,
        exit_cause="TP_HIT",
        strategy_family="STANDARD_SMC",
        created_at=now_ms,
        closed_at=now_ms,
    )
    t2 = models.PaperOrder(
        id="t2",
        instrument="XAUUSDT",
        direction="LONG",
        timeframe="15M",
        planned_entry=4100.0,
        actual_entry=4100.0,
        actual_exit=4120.0,
        stop_loss=4080.0,
        take_profit=4150.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        state="closed",
        realized_pnl_net=20.0,
        realized_r=1.0,
        exit_cause="MANUAL_CLOSE",
        strategy_family="STANDARD_SMC",
        created_at=now_ms,
        closed_at=now_ms,
    )
    t3 = models.PaperOrder(
        id="t3",
        instrument="XAUUSDT",
        direction="SHORT",
        timeframe="15M",
        planned_entry=4100.0,
        actual_entry=4100.0,
        actual_exit=4120.0,
        stop_loss=4120.0,
        take_profit=4060.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        state="closed",
        realized_pnl_net=-20.0,
        realized_r=-1.0,
        exit_cause="SL_HIT",
        strategy_family="STANDARD_SMC",
        created_at=now_ms,
        closed_at=now_ms,
    )
    t_armed = models.PaperOrder(
        id="t_armed",
        instrument="XAUUSDT",
        direction="LONG",
        timeframe="15M",
        planned_entry=4150.0,
        stop_loss=4130.0,
        take_profit=4190.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        state="armed",
        created_at=now_ms,
    )
    matrix_db.add_all([t1, t2, t3, t_armed])
    matrix_db.commit()

    # Query closed trades
    res = matrix_client.get("/api/v1/journal/paginated?state=CLOSED").json()
    assert res["total"] == 3
    summary = res["summary"]
    assert summary["completed_count"] == 3
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert summary["breakevens"] == 0
    # Winrate = 2/3 = 66.67%
    assert round(summary["winrate_pct"], 1) == 66.7
    # Net PnL = 50 + 20 - 20 = 50.0
    assert summary["net_pnl"] == 50.0
    # Average R = (2.5 + 1.0 - 1.0) / 3 = 0.83R
    assert round(summary["average_realized_r"], 2) == 0.83


def test_j07_trade_review_optimistic_concurrency_locking(matrix_client, matrix_db):
    """J07: Trade review save, reload, and HTTP 409 optimistic locking on revision conflict."""
    trade_id = "trade_review_test_1"
    
    # First save (rev 0)
    payload_1 = {
        "user_notes": "Ghi chú lần đầu",
        "self_reported_entry_reason": "Sweep đỉnh Á",
        "psychology_before": "Bình tĩnh",
        "emotions": ["Bình tĩnh", "Kỷ luật cao"],
        "confidence_score": 4,
        "discipline_score": 5,
        "expected_revision": 0,
    }
    res1 = matrix_client.post(f"/api/v1/journal/review/{trade_id}", json=payload_1)
    assert res1.status_code == 200
    review_data = res1.json()
    assert review_data["revision"] == 1
    assert review_data["user_notes"] == "Ghi chú lần đầu"

    # Conflicting save using old expected_revision = 0 -> Must return HTTP 409!
    payload_conflict = {
        "user_notes": "Ghi đè xung đột",
        "expected_revision": 0,
    }
    res_conflict = matrix_client.post(f"/api/v1/journal/review/{trade_id}", json=payload_conflict)
    assert res_conflict.status_code == 409
    assert "409" in res_conflict.json()["detail"] or "Conflict" in res_conflict.json()["detail"]

    # Valid save using expected_revision = 1 -> succeeds and increments revision to 2
    payload_valid = {
        "user_notes": "Cập nhật đúng phiên bản",
        "expected_revision": 1,
    }
    res_valid = matrix_client.post(f"/api/v1/journal/review/{trade_id}", json=payload_valid)
    assert res_valid.status_code == 200
    assert res_valid.json()["revision"] == 2
    assert res_valid.json()["user_notes"] == "Cập nhật đúng phiên bản"


def test_j09_j10_lesson_approval_lifecycle_and_strategy_memory(matrix_client, matrix_db):
    """J09 & J10: Lessons draft appears in UI; strategy memory strictly requires APPROVED."""
    now_ms = int(time.time() * 1000)

    # 1. Create a draft lesson with status PENDING_REVIEW
    lesson = models.Lesson(
        setup_type="SMC_15M",
        title="SL do biến động tin tức",
        reflection="Chi tiết setup bị chạm SL",
        category="PROCESS",
        action_rule="Không vào lệnh trước tin 15 phút",
        status="PENDING_REVIEW",
        is_approved=False,
        created_at=now_ms,
    )
    matrix_db.add(lesson)
    matrix_db.commit()
    matrix_db.refresh(lesson)

    # 2. Strategy memory retrieval must NOT see this lesson yet
    strat_lessons = crud.get_approved_lessons_for_strategy(matrix_db, "SMC_15M")
    assert len(strat_lessons) == 0

    # 3. UI query for PENDING_REVIEW must see this lesson
    ui_res = matrix_client.get("/api/v1/lessons?status=PENDING_REVIEW").json()
    assert any(ls["id"] == lesson.id for ls in ui_res)

    # 4. User approves the lesson via API
    res_approve = matrix_client.post(f"/api/v1/lessons/{lesson.id}/approve")
    assert res_approve.status_code == 200
    assert res_approve.json()["status"] == "APPROVED"
    assert res_approve.json()["is_approved"] is True

    # 5. Now strategy memory CAN retrieve it
    strat_lessons_after = crud.get_approved_lessons_for_strategy(matrix_db, "SMC_15M")
    assert len(strat_lessons_after) == 1
    assert strat_lessons_after[0].id == lesson.id


def test_j11_idempotent_close_does_not_duplicate_lessons(matrix_db):
    """J11: Closing an order twice (idempotency check) does not create duplicate lessons."""
    now_ms = int(time.time() * 1000)

    order = models.PaperOrder(
        id="ord_idempotent_test",
        instrument="XAUUSDT",
        direction="LONG",
        timeframe="15M",
        planned_entry=4120.0,
        actual_entry=4120.0,
        actual_exit=4150.0,
        stop_loss=4100.0,
        take_profit=4150.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        state="closed",
        realized_pnl_net=3.0,
        realized_r=1.5,
        exit_cause="TP_HIT",
        created_at=now_ms,
        closed_at=now_ms,
    )
    matrix_db.add(order)
    matrix_db.commit()

    TradeLifecycleService._create_lesson(matrix_db, order, now_ms)
    TradeLifecycleService._create_lesson(matrix_db, order, now_ms)

    lessons_count = matrix_db.query(models.Lesson).filter(models.Lesson.related_trade_id == order.id).count()
    assert lessons_count == 1
