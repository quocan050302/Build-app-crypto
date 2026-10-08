import time
import json
import asyncio
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, UploadFile, File, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

import models, schemas, crud, bitget_data, smc_engine
from database import engine, get_db
from paper_broker import PaperBroker
from research_engine import generate_research_report, get_current_session_info
import news_service

# Ensure all tables exist
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Aurum Desk API",
    description="Backend API cho Aurum Desk: XAUUSDT Research, SMC/ICT Engine và Auto Paper Trading",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Track active WebSocket clients
active_websockets: List[WebSocket] = []

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        while True:
            # Keep-alive ping
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)


# ==================== 1. SYSTEM HEALTH ====================

@app.get("/health", response_model=schemas.SystemHealthResponse)
def health_check(symbol: str = "XAUUSDT", timeframe: str = "15M", db: Session = Depends(get_db)):
    """
    Comprehensive system health:
    - Data feed connectivity
    - DB integrity
    - Data freshness
    - Strategy engine availability
    """
    now_ms = int(time.time() * 1000)
    feed_connected = False
    try:
        ticker = bitget_data.fetch_ticker(symbol)
        feed_connected = True
    except Exception:
        ticker = None

    candles = crud.get_candles(db, symbol, timeframe, limit=1)
    last_candle_time = candles[0].timestamp if candles else 0
    freshness_sec = round((now_ms - last_candle_time) / 1000.0, 1) if last_candle_time else 9999.0
    is_stale = freshness_sec > (30 * 60) # Stale if older than 30 mins

    return schemas.SystemHealthResponse(
        status="ok" if (feed_connected and not is_stale) else ("degraded" if feed_connected else "disconnected"),
        timestamp=now_ms,
        feed_connected=feed_connected,
        db_connected=True,
        collector_alive=True,
        data_freshness_sec=freshness_sec,
        data_is_stale=is_stale,
        engine_state="active" if feed_connected else "disconnected",
        active_timeframe=timeframe,
        candle_count=len(candles),
        strategy_version="1.0.0",
        paper_trading_mode="SIMULATION"
    )


# ==================== 2. CANDLES & TICKER ====================

@app.get("/api/v1/ticker/{symbol}")
def get_ticker(symbol: str = "XAUUSDT"):
    try:
        return bitget_data.fetch_ticker(symbol)
    except bitget_data.BitgetDataError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@app.get("/api/v1/candles/{symbol}/{timeframe}", response_model=schemas.CandleListResponse)
def get_candles_endpoint(
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    limit: int = Query(150, ge=10, le=300),
    db: Session = Depends(get_db)
):
    """Retrieve validated candles from SQLite cache with freshness metadata."""
    now_ms = int(time.time() * 1000)
    candles = crud.get_candles(db, symbol, timeframe, limit=limit, ascending=True)
    
    last_time = candles[-1].timestamp if candles else None
    freshness_sec = (now_ms - last_time) / 1000.0 if last_time else 9999.0
    is_stale = freshness_sec > (30 * 60)

    # Convert to Pydantic models
    candle_models = [schemas.Candle.model_validate(c) for c in candles]

    return schemas.CandleListResponse(
        symbol=symbol,
        timeframe=timeframe,
        candles=candle_models,
        count=len(candle_models),
        is_stale=is_stale,
        last_candle_time=last_time,
        server_time=now_ms
    )


@app.post("/api/v1/candles/sync")
def sync_candles(
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    limit: int = Query(150, ge=10, le=200),
    db: Session = Depends(get_db)
):
    """Sync latest candles from Bitget into SQLite atomically."""
    t0 = time.time()
    try:
        new_candles, server_time = bitget_data.fetch_candles(symbol, timeframe, limit)
        if new_candles:
            synced_count = crud.bulk_upsert_candles(db, new_candles)
            latency_ms = round((time.time() - t0) * 1000, 1)

            # Check price tick against any active paper order
            try:
                ticker = bitget_data.fetch_ticker(symbol)
                PaperBroker.process_price_tick(
                    db,
                    current_bid=ticker["bid"],
                    current_ask=ticker["ask"],
                    candle_high=new_candles[-1].high,
                    candle_low=new_candles[-1].low
                )
            except Exception:
                pass

            return {
                "status": "success",
                "symbol": symbol,
                "timeframe": timeframe,
                "synced_count": synced_count,
                "latest_candle_time": new_candles[-1].timestamp,
                "latest_is_closed": new_candles[-1].is_closed,
                "latency_ms": latency_ms,
                "server_time": server_time
            }
        return {"status": "empty", "message": "Không nhận được nến mới từ sàn"}
    except bitget_data.BitgetDataError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


# ==================== 3. SMC/ICT ANALYSIS ====================

@app.get("/api/v1/analysis/{symbol}/{timeframe}")
def get_market_analysis(
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    db: Session = Depends(get_db)
):
    """
    Run full SMC/ICT multi-timeframe analysis:
    - Swing pivots (2 left, 2 right confirmation)
    - Trend (HH/HL vs LH/LL)
    - FVGs lifecycle & sweeps
    - Premium/Discount dealing range
    - Hard filters checklist
    - Position Sizing & Net RR calculation
    """
    # 1. Fetch candles from DB
    candles = crud.get_candles(db, symbol, timeframe, limit=150, ascending=True)

    # Auto sync if DB has not enough candles
    if len(candles) < 30:
        try:
            new_candles, _ = bitget_data.fetch_candles(symbol, timeframe, limit=150)
            if new_candles:
                crud.bulk_upsert_candles(db, new_candles)
                candles = crud.get_candles(db, symbol, timeframe, limit=150, ascending=True)
        except Exception:
            pass

    # 2. Get current live ticker for bid/ask
    ticker = None
    try:
        ticker = bitget_data.fetch_ticker(symbol)
    except Exception:
        if candles:
            ticker = {"bid": candles[-1].close, "ask": candles[-1].close, "last": candles[-1].close}

    # 3. Check news blackout
    now_ms = int(time.time() * 1000)
    is_blackout, blackout_reason, _ = crud.check_news_blackout(db, now_ms)

    # 4. Check day audit
    day_audit = crud.get_or_create_today_audit(db)

    # 5. Execute SMC Engine
    analysis_result = smc_engine.evaluate_smc_setup(
        candles=candles,
        symbol=symbol,
        timeframe=timeframe,
        ticker_data=ticker,
        day_audit=day_audit,
        is_news_blackout=is_blackout,
        news_blackout_reason=blackout_reason
    )

    return analysis_result


# In-memory persisted Auto Paper State
auto_paper_state = {"enabled": True}

# Background worker for exit checks independent of frontend chart
async def background_exit_monitor():
    while True:
        try:
            await asyncio.sleep(2.5)
            # Create a localized DB session
            from database import SessionLocal
            db = SessionLocal()
            try:
                active = crud.get_active_position(db)
                if active and active.state == "paper_open":
                    ticker = bitget_data.fetch_ticker(active.instrument)
                    PaperBroker.process_price_tick(
                        db,
                        current_bid=ticker["bid"],
                        current_ask=ticker["ask"]
                    )
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception:
            pass

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_exit_monitor())


# ==================== 4. PAPER TRADING & RISK ====================

@app.get("/api/v1/auto/state")
def get_auto_state():
    """Retrieve persisted auto paper trading state."""
    return {"auto_paper_enabled": auto_paper_state["enabled"]}


@app.post("/api/v1/auto/state")
def set_auto_state(body: dict):
    """Toggle auto paper trading state."""
    enabled = bool(body.get("enabled", True))
    auto_paper_state["enabled"] = enabled
    return {"status": "success", "auto_paper_enabled": auto_paper_state["enabled"]}


@app.post("/api/v1/orders/preview")
def preview_order_calc(payload: dict):
    """
    Authoritative domain calculation preview for frontend draft tool or manual order entry.
    """
    from domain_calculator import calculate_risk_reward
    direction = payload.get("direction", "LONG")
    entry = float(payload.get("entry", 0))
    sl = float(payload.get("stop_loss", 0))
    tp = float(payload.get("take_profit", 0))
    capital = float(payload.get("capital", 1000.0))
    risk_pct = float(payload.get("risk_pct", 0.25))
    qty_override = payload.get("quantity_override")

    result = calculate_risk_reward(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        capital=capital,
        risk_pct=risk_pct,
        quantity_override=float(qty_override) if qty_override is not None else None
    )
    return result.model_dump()


@app.post("/api/v1/orders/amend/{order_id}")
def amend_order_endpoint(order_id: str, payload: dict, db: Session = Depends(get_db)):
    """Amend Stop Loss or Take Profit of an active paper position."""
    new_sl = payload.get("stop_loss")
    new_tp = payload.get("take_profit")
    try:
        updated = PaperBroker.amend_open_position(
            db=db,
            order_id=order_id,
            new_sl=float(new_sl) if new_sl is not None else None,
            new_tp=float(new_tp) if new_tp is not None else None
        )
        return {
            "status": "success",
            "message": f"Đã cập nhật vị thế {updated.id}: SL={updated.stop_loss}, TP={updated.take_profit}",
            "order": schemas.PaperOrderResponse.model_validate(updated)
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/account/status", response_model=schemas.DayAuditResponse)
def get_account_status(db: Session = Depends(get_db)):
    """
    Retrieve today's simulated equity, separate position & fills counters, and risk limits.
    Separates 'Vị thế đang mở' (0/1) from 'Lệnh đã vào hôm nay' (0/3).
    """
    audit = crud.get_or_create_today_audit(db)
    active_pos = crud.get_active_position(db)
    now_ms = int(time.time() * 1000)
    cooldown_sec = max(0, int((audit.cooldown_until - now_ms) / 1000)) if audit.cooldown_until else 0

    return schemas.DayAuditResponse(
        date_str=audit.date_str,
        initial_equity=audit.initial_equity,
        current_equity=audit.current_equity,
        realized_pnl_today=audit.realized_pnl_today,
        fills_count=audit.fills_count,
        today_fills_count=audit.fills_count,
        active_positions_count=1 if active_pos else 0,
        armed_orders_count=0,
        max_daily_fills=3,
        consecutive_losses=audit.consecutive_losses,
        max_consecutive_losses=2,
        daily_loss_limit_usdt=round(audit.initial_equity * 0.015, 2),
        cooldown_remaining_sec=cooldown_sec,
        is_blocked=audit.is_blocked,
        block_reason=audit.block_reason,
        auto_paper_active=auto_paper_state["enabled"]
    )


@app.get("/api/v1/positions/active")
def get_active_paper_position(db: Session = Depends(get_db)):
    """Retrieve currently open paper position and unrealized PnL."""
    active = crud.get_active_position(db)
    if not active:
        return {"has_active_position": False, "position": None}

    # Fetch live ticker to compute unrealized PnL
    curr_price = active.actual_entry or active.planned_entry
    unrealized_pnl = 0.0
    try:
        ticker = bitget_data.fetch_ticker(active.instrument)
        curr_price = ticker["bid"] if active.direction == "LONG" else ticker["ask"]
        dir_mult = 1.0 if active.direction == "LONG" else -1.0
        unrealized_pnl = round((curr_price - (active.actual_entry or active.planned_entry)) * active.quantity * dir_mult, 2)
    except Exception:
        pass

    return {
        "has_active_position": True,
        "position": schemas.PaperOrderResponse.model_validate(active),
        "current_price": curr_price,
        "unrealized_pnl": unrealized_pnl
    }


@app.post("/api/v1/orders/paper")
def create_paper_order(order: schemas.PaperOrderCreate, db: Session = Depends(get_db)):
    """Open a new simulated paper order with risk and rule checks."""
    ticker = bitget_data.fetch_ticker(order.instrument)
    try:
        created_order = PaperBroker.execute_market_order(
            db=db,
            order_create=order,
            current_bid=ticker["bid"],
            current_ask=ticker["ask"]
        )
        return {
            "status": "success",
            "message": f"Đã mở vị thế Paper Trading: {created_order.direction} tại {created_order.actual_entry}",
            "order": schemas.PaperOrderResponse.model_validate(created_order)
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/orders/close/{order_id}")
def close_paper_order(order_id: str, db: Session = Depends(get_db)):
    """Manually close an active paper position at market bid/ask."""
    active = crud.get_paper_order(db, order_id)
    if not active or active.state != "paper_open":
        raise HTTPException(status_code=404, detail="Không tìm thấy vị thế đang mở với ID này")

    ticker = bitget_data.fetch_ticker(active.instrument)
    exit_price = ticker["bid"] if active.direction == "LONG" else ticker["ask"]

    entry_p = active.actual_entry or active.planned_entry
    qty = active.quantity
    direction_mult = 1.0 if active.direction == "LONG" else -1.0
    gross_pnl = (exit_price - entry_p) * qty * direction_mult
    fee_cost = (entry_p + exit_price) * qty * 0.0004
    net_pnl = round(gross_pnl - fee_cost, 2)
    realized_r = round(net_pnl / active.initial_risk_usdt, 2) if active.initial_risk_usdt > 0 else 0.0

    closed = crud.update_paper_order_state(
        db,
        order_id=order_id,
        new_state="closed",
        actual_exit=exit_price,
        realized_pnl=net_pnl,
        realized_r=realized_r,
        exit_cause="MANUAL_CLOSE"
    )
    crud.record_trade_close_audit(db, net_pnl)
    PaperBroker._create_post_trade_lesson(db, closed)

    return {
        "status": "success",
        "message": f"Đã đóng vị thế {closed.direction} tại {exit_price}. PnL: ${net_pnl:+.2f} ({realized_r:+.2f}R)",
        "order": schemas.PaperOrderResponse.model_validate(closed)
    }


# ==================== 5. ECONOMIC NEWS & BLACKOUT ====================

@app.get("/api/v1/news")
def get_economic_news(db: Session = Depends(get_db)):
    """Retrieve upcoming economic events and blackout status."""
    now_ms = int(time.time() * 1000)
    is_blackout, reason, remaining_min = crud.check_news_blackout(db, now_ms)

    # Next 10 events
    events = (
        db.query(models.EconomicNews)
        .filter(models.EconomicNews.scheduled_at >= now_ms - (3 * 3600 * 1000))
        .order_by(models.EconomicNews.scheduled_at.asc())
        .limit(20)
        .all()
    )

    return {
        "blackout_status": {
            "is_blackout": is_blackout,
            "reason": reason,
            "remaining_minutes": remaining_min
        },
        "events": events
    }


@app.post("/api/v1/news/import")
async def import_news_calendar(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import Forex Factory JSON or CSV economic calendar."""
    content = await file.read()
    content_str = content.decode("utf-8", errors="replace")

    parsed_items = []
    if file.filename.endswith(".json"):
        parsed_items = news_service.parse_forex_factory_json(content_str)
    elif file.filename.endswith(".csv"):
        parsed_items = news_service.parse_csv_calendar(content_str)
    else:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file định dạng .json hoặc .csv")

    imported_count = crud.bulk_upsert_news(db, parsed_items)
    return {
        "status": "success",
        "filename": file.filename,
        "imported_count": imported_count
    }


# ==================== 6. RESEARCH REPORTS ====================

@app.get("/api/v1/reports")
def get_reports(limit: int = 5, db: Session = Depends(get_db)):
    reports = crud.get_latest_reports(db, limit)
    session_info = get_current_session_info()
    return {
        "session_info": session_info,
        "reports": reports
    }


@app.post("/api/v1/reports/generate")
def create_report(report_type: str = "SESSION_REPORT", db: Session = Depends(get_db)):
    report = generate_research_report(db, report_type)
    return {
        "status": "success",
        "report_id": report.id,
        "session_name": report.session_name,
        "content_markdown": report.content_markdown
    }


# ==================== 7. JOURNAL & LESSONS ====================

@app.get("/api/v1/journal")
def get_journal(limit: int = 50, db: Session = Depends(get_db)):
    orders = crud.list_paper_orders(db, limit)
    return [schemas.PaperOrderResponse.model_validate(o) for o in orders]


@app.get("/api/v1/lessons")
def get_lessons(setup_type: Optional[str] = None, db: Session = Depends(get_db)):
    lessons = crud.get_lessons(db, setup_type)
    return lessons


# ==================== 8. KNOWLEDGE HUB ====================

@app.get("/api/v1/education")
def get_education_hub():
    """Built-in Vietnamese Knowledge Hub for XAUUSDT and SMC trading."""
    return [
        {
            "id": "intro_xauusdt",
            "title": "Hợp Đồng Vàng XAUUSDT Perpetual Futures Trên Bitget",
            "category": "CƠ BẢN",
            "content": """
XAUUSDT trên sàn Bitget là hợp đồng tương lai vĩnh cửu (Perpetual Futures) ký quỹ bằng USDT, tham chiếu giá vàng thế giới.
- **Quy cách hợp đồng:** 1 hợp đồng tương đương 1 ounce troy vàng.
- **Bước giá (Tick size):** 0.01 USDT.
- **Bước khối lượng:** 0.01 hợp đồng.
- **Đòn bẩy:** Giả lập an toàn 3x (ký quỹ ban đầu ~33%).
- **Lưu ý quan trọng:** Đây là phái sinh tài chính tham chiếu vàng, không phải vàng vật chất (SJC, 9999).
            """
        },
        {
            "id": "smc_core",
            "title": "Phương Pháp SMC/ICT: Cấu Trúc Và Thanh Khoản",
            "category": "CHIẾN LƯỢC",
            "content": """
Chiến lược giao dịch Aurum Desk áp dụng các quy tắc SMC/ICT có thể kiểm chứng:
1. **Swing High / Low:** Xác nhận khi có đủ 2 nến trái và 2 nến phải đã đóng.
2. **Liquidity Sweep (Quét thanh khoản):** Nến đẩy râu (wick) vượt đỉnh/đáy cũ nhưng rút chân đóng nến bên trong.
3. **CHoCH (Change of Character):** Phá vỡ cấu trúc đỉnh/đáy ngược chiều báo hiệu khả năng đảo chiều.
4. **BOS (Break of Structure):** Phá vỡ đỉnh/đáy tiếp diễn xu hướng.
5. **FVG (Fair Value Gap):** Vùng mất cân bằng giữa 3 nến liền kề.
6. **Premium vs Discount:** Chỉ Mua tại Discount (< 50% dealing range) và Bán tại Premium (> 50%).
            """
        },
        {
            "id": "risk_management",
            "title": "Quy Tắc Quản Trị Rủi Ro Và Paper Trading",
            "category": "RỦI RO",
            "content": """
Nguyên tắc quản trị vốn nghiêm ngặt của Aurum Desk:
- **Vốn giả lập:** 1.000 USDT.
- **Rủi ro mỗi lệnh:** 0.5% vốn ($5.00/lệnh).
- **Tối đa số lệnh:** Không quá 3 lệnh mở trong ngày (UTC+7).
- **Giới hạn lỗ ngày (Daily Loss Cap):** 1.5% ($15.00/ngày).
- **Ngừng lỗ chuỗi:** Dừng mở lệnh mới trong ngày nếu chịu 2 lệnh thua liên tiếp.
- **Thời gian nghỉ (Cooldown):** 30 phút sau khi đóng bất kỳ vị thế nào.
- **Tỷ lệ R:R:** Chỉ vào lệnh khi tỷ lệ Lợi nhuận / Rủi ro thực (Net R:R) >= 2.0.
            """
        }
    ]
