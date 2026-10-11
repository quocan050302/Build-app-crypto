"""
Test V8 Repro & Contract Suite:
Demonstrates specific bugs identified in V7.2 baseline before V8 implementation:
- Bug A: send_telegram_direct returns success=True on HTTP 200 + {"ok": false}
- Bug B: send_telegram_direct ignores parameters.retry_after in HTTP 429 JSON
- Bug F: TradeLifecycleService._create_lesson confuses MANUAL_CLOSE with TP_HIT and SL_HIT
- Bug G: crud.get_lessons hides newly created lessons because is_approved=False
"""

import json
import time
import pytest
from unittest.mock import AsyncMock, patch
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import crud
from services.telegram_service import send_telegram_direct, TelegramSendResult
from services.trade_lifecycle_service import TradeLifecycleService


@pytest.fixture
def mem_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


import asyncio

def test_repro_bug_a_http200_ok_false():
    """Bug A: Telegram API responds 200 OK but with ok: false in body."""
    async def _run():
        mock_resp = httpx.Response(
            status_code=200,
            json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            res = await send_telegram_direct(bot_token="test_token", chat_id="12345", text="hello")
            # Must NOT be success!
            assert res.success is False, f"Expected success=False on ok=False, got {res}"
            assert res.error_code in ("CHAT_NOT_FOUND", "BAD_REQUEST", "INVALID_RESPONSE")
    asyncio.run(_run())


def test_repro_bug_b_http429_parameters_retry_after():
    """Bug B: Telegram API responds 429 with parameters.retry_after=37."""
    async def _run():
        mock_resp = httpx.Response(
            status_code=429,
            json={"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 37}}
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            res = await send_telegram_direct(bot_token="test_token", chat_id="12345", text="hello")
            assert res.success is False
            assert res.retry_after_sec == 37, f"Expected retry_after_sec=37, got {res.retry_after_sec}"
    asyncio.run(_run())


def test_repro_bug_f_manual_close_not_tp_or_sl(mem_db):
    """Bug F: Manual close with profit must NOT be recorded as Take Profit; manual close with loss must NOT be SL."""
    now_ms = int(time.time() * 1000)
    
    # 1. Manual close with profit
    order_profit = models.PaperOrder(
        id="ord-manual-profit",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2680.0,
        actual_exit=2660.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        realized_pnl_net=0.43,
        realized_r=0.43,
        exit_cause="MANUAL_CLOSE",
        created_at=now_ms,
        opened_at=now_ms,
        closed_at=now_ms
    )
    mem_db.add(order_profit)
    mem_db.commit()

    TradeLifecycleService._create_lesson(mem_db, order_profit, now_ms)
    lesson_profit = mem_db.query(models.Lesson).filter(models.Lesson.related_trade_id == "ord-manual-profit").first()
    assert lesson_profit is not None
    # Must NOT claim "đạt Take Profit"
    assert "Take Profit" not in lesson_profit.reflection, f"Manual close profit incorrectly claims TP: {lesson_profit.reflection}"
    assert "đóng chủ động" in lesson_profit.reflection.lower() or "thủ công" in lesson_profit.reflection.lower() or "manual" in lesson_profit.reflection.lower()

    # 2. Manual close with loss
    order_loss = models.PaperOrder(
        id="ord-manual-loss",
        direction="LONG",
        state="closed",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2680.0,
        actual_exit=2645.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        realized_pnl_net=-0.43,
        realized_r=-0.43,
        exit_cause="MANUAL_CLOSE",
        created_at=now_ms,
        opened_at=now_ms,
        closed_at=now_ms
    )
    mem_db.add(order_loss)
    mem_db.commit()

    TradeLifecycleService._create_lesson(mem_db, order_loss, now_ms)
    lesson_loss = mem_db.query(models.Lesson).filter(models.Lesson.related_trade_id == "ord-manual-loss").first()
    assert lesson_loss is not None
    # Must NOT claim "chạm SL"
    assert "chạm SL" not in lesson_loss.reflection, f"Manual close loss incorrectly claims SL: {lesson_loss.reflection}"
    assert "đóng chủ động" in lesson_loss.reflection.lower() or "thủ công" in lesson_loss.reflection.lower() or "manual" in lesson_loss.reflection.lower()


def test_repro_bug_g_lessons_visibility_for_ui(mem_db):
    """Bug G: Unapproved draft lessons must be retrievable for UI review, not hidden."""
    now_ms = int(time.time() * 1000)
    draft_lesson = models.Lesson(
        created_at=now_ms,
        title="Bài học mới đóng lệnh",
        category="EXECUTION",
        reflection="Quan sát đóng lệnh thủ công",
        action_rule="Kiểm tra lại cấu trúc",
        is_approved=False
    )
    mem_db.add(draft_lesson)
    mem_db.commit()

    # get_lessons for UI should be able to fetch pending review lessons
    lessons = crud.get_lessons(mem_db, status="ALL")
    assert len(lessons) >= 1, "crud.get_lessons should return draft lessons when status='ALL'"
