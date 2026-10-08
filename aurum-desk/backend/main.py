import time
import json
import uuid
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, UploadFile, File, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

import models, schemas, crud, bitget_data, smc_engine
from database import engine, get_db, SessionLocal
from paper_broker import PaperBroker
from research_engine import generate_research_report, get_current_session_info
import news_service
from services.event_bus import event_bus
from services.collector_service import collector_service
from services.strategy_service import strategy_service
from services.execution_coordinator import execution_coordinator
from services.exit_monitor import exit_monitor
from services.telegram_service import process_notification_outbox, send_telegram_direct
from services.volume_service import volume_analyzer
from services.proximity_service import proximity_service
from services.trade_lifecycle_service import TradeLifecycleService
from domain_calculator import calculate_risk_reward

# Ensure all tables exist
models.Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan:
    Starts and manages background workers cleanly,
    cancels and awaits clean shutdown when stopped.
    """
    tasks = [
        asyncio.create_task(collector_service.run_collector_loop()),
        asyncio.create_task(strategy_service.run_strategy_loop()),
        asyncio.create_task(proximity_service.run_proximity_loop()),
        asyncio.create_task(execution_coordinator.run_coordinator_loop()),
        asyncio.create_task(exit_monitor.run_exit_monitor_loop()),
        asyncio.create_task(process_notification_outbox())
    ]
    yield
    # Graceful shutdown
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

app = FastAPI(
    title="Aurum Desk API",
    description="Backend API cho Aurum Desk: XAUUSDT Research, SMC/ICT Engine và Auto Paper Trading",
    version="4.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== WEBSOCKET WITH DOMAIN EVENTS ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await event_bus.connect(websocket)
    try:
        # Send initial state snapshot on connection
        db = SessionLocal()
        try:
            active_pos = crud.get_active_position(db)
            audit = crud.get_or_create_today_audit(db)
            armed_count = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").count()
            recent_events = (
                db.query(models.DomainEvent)
                .order_by(models.DomainEvent.sequence.desc())
                .limit(20)
                .all()
            )
            snapshot = {
                "type": "SNAPSHOT",
                "timestamp": int(time.time() * 1000),
                "feed_connected": collector_service.feed_connected,
                "d_4h_bias": collector_service.d_4h_bias,
                "h1_alignment": collector_service.h1_alignment,
                "active_positions_count": 1 if active_pos else 0,
                "today_fills_count": audit.fills_count,
                "armed_orders_count": armed_count,
                "auto_paper_enabled": strategy_service.get_auto_state(db),
                "recent_events": [
                    {
                        "event_id": e.event_id,
                        "sequence": e.sequence,
                        "event_type": e.event_type,
                        "aggregate_id": e.aggregate_id,
                        "occurred_at": e.occurred_at,
                        "payload": json.loads(e.payload) if e.payload else {}
                    }
                    for e in reversed(recent_events)
                ]
            }
            await websocket.send_text(json.dumps(snapshot))
        finally:
            db.close()

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await event_bus.disconnect(websocket)
    except Exception:
        await event_bus.disconnect(websocket)


# ==================== 1. SYSTEM HEALTH ====================

@app.get("/health", response_model=schemas.SystemHealthResponse)
def health_check(symbol: str = "XAUUSDT", timeframe: str = "15M", db: Session = Depends(get_db)):
    """Comprehensive system health reporting real live status without fake fallbacks."""
    now_ms = int(time.time() * 1000)
    feed_connected = collector_service.feed_connected
    collector_alive = collector_service.collector_alive

    candles = crud.get_candles(db, symbol, timeframe, limit=1)
    last_candle_time = candles[0].timestamp if candles else 0
    is_stale, freshness_sec = collector_service.is_stale(timeframe, last_candle_time)

    status = "ok" if (feed_connected and not is_stale) else ("degraded" if feed_connected else "disconnected")
    degraded_reason = collector_service.last_error if not feed_connected else ("Dữ liệu nến bị trễ so với chu kỳ" if is_stale else None)

    return schemas.SystemHealthResponse(
        status=status,
        timestamp=now_ms,
        feed_connected=feed_connected,
        db_connected=True,
        collector_alive=collector_alive,
        data_freshness_sec=freshness_sec,
        data_is_stale=is_stale,
        engine_state="active" if feed_connected else "disconnected",
        active_timeframe=timeframe,
        candle_count=len(candles),
        strategy_version="4.0.0",
        paper_trading_mode="SIMULATION",
        last_success_time=collector_service.last_success_time,
        last_error=collector_service.last_error,
        degraded_reason=degraded_reason,
        d_4h_bias=collector_service.d_4h_bias,
        h1_alignment=collector_service.h1_alignment
    )


# ==================== 2. CANDLES & TICKER ====================

@app.get("/api/v1/ticker/{symbol}")
def get_ticker(symbol: str = "XAUUSDT"):
    if collector_service.latest_ticker and collector_service.latest_ticker.get("symbol") == symbol:
        return collector_service.latest_ticker
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
    now_ms = int(time.time() * 1000)
    candles = crud.get_candles(db, symbol, timeframe, limit=limit, ascending=True)
    last_time = candles[-1].timestamp if candles else None
    is_stale, freshness_sec = collector_service.is_stale(timeframe, last_time)

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
    t0 = time.time()
    try:
        new_candles, server_time = bitget_data.fetch_candles(symbol, timeframe, limit)
        if new_candles:
            synced_count = crud.bulk_upsert_candles(db, new_candles)
            latency_ms = round((time.time() - t0) * 1000, 1)

            try:
                ticker = collector_service.latest_ticker or bitget_data.fetch_ticker(symbol)
                PaperBroker.process_price_tick(
                    db,
                    current_bid=ticker["bid"],
                    current_ask=ticker["ask"],
                    candle_high=new_candles[-1].high,
                    candle_low=new_candles[-1].low,
                    candle_timestamp=new_candles[-1].timestamp
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


# ==================== 3. MULTI-TIMEFRAME MATRIX & RVOL ====================

@app.get("/api/v1/market/matrix", response_model=schemas.MarketMatrixResponse)
def get_market_matrix(symbol: str = "XAUUSDT", db: Session = Depends(get_db)):
    """Return trend and status matrix across all supported timeframes."""
    now_ms = int(time.time() * 1000)
    matrix_items: List[schemas.MarketMatrixItem] = []

    for tf in ["D", "4H", "1H", "15M", "5M", "1M"]:
        candles = crud.get_candles(db, symbol, tf, limit=40, ascending=True)
        if candles:
            sh, sl = smc_engine.identify_pivots(candles, tf)
            trend = smc_engine.determine_trend(candles, sh, sl)
            atr = smc_engine.compute_atr(candles, 14)
            last_ts = candles[-1].timestamp
            is_stale, fresh_sec = collector_service.is_stale(tf, last_ts)

            # Zone
            eq = (max(c.high for c in candles[-20:]) + min(c.low for c in candles[-20:])) / 2.0
            zone = "DISCOUNT" if candles[-1].close < eq else "PREMIUM"

            matrix_items.append(schemas.MarketMatrixItem(
                timeframe=tf,
                trend=trend,
                zone=zone,
                current_price=candles[-1].close,
                atr=atr,
                last_candle_time=last_ts,
                is_stale=is_stale,
                freshness_sec=fresh_sec
            ))
        else:
            matrix_items.append(schemas.MarketMatrixItem(
                timeframe=tf,
                trend="UNKNOWN",
                zone="EQUILIBRIUM",
                current_price=0.0,
                atr=2.0,
                last_candle_time=0,
                is_stale=True,
                freshness_sec=9999.0
            ))

    return schemas.MarketMatrixResponse(
        symbol=symbol,
        d_4h_bias=collector_service.d_4h_bias,
        h1_alignment=collector_service.h1_alignment,
        server_time=now_ms,
        matrix=matrix_items
    )


@app.get("/api/v1/volume/rvol/{symbol}/{timeframe}", response_model=schemas.RvolResponse)
def get_rvol(symbol: str = "XAUUSDT", timeframe: str = "15M", db: Session = Depends(get_db)):
    candles = crud.get_candles(db, symbol, timeframe, limit=50, ascending=True)
    res = volume_analyzer.calculate_rvol(candles)
    return schemas.RvolResponse(
        symbol=symbol,
        timeframe=timeframe,
        rvol=res.get("rvol"),
        status=res.get("status", "UNKNOWN"),
        volume=res.get("volume", 0.0),
        baseline_volume=res.get("baseline_volume", 0.0),
        classification=res.get("classification", "UNKNOWN"),
        samples_used=res.get("samples_used", 0),
        unit=res.get("unit", "oz")
    )


# ==================== 4. SMC/ICT ANALYSIS ====================

@app.get("/api/v1/analysis/{symbol}/{timeframe}")
def get_market_analysis(
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    db: Session = Depends(get_db)
):
    """Run full causal SMC setup evaluation with real D/4H bias and real H1 context."""
    candles = crud.get_candles(db, symbol, timeframe, limit=150, ascending=True)

    if len(candles) < 20:
        try:
            new_candles, _ = bitget_data.fetch_candles(symbol, timeframe, limit=150)
            if new_candles:
                crud.bulk_upsert_candles(db, new_candles)
                candles = crud.get_candles(db, symbol, timeframe, limit=150, ascending=True)
        except Exception:
            pass

    ticker = collector_service.latest_ticker
    if not ticker:
        try:
            ticker = bitget_data.fetch_ticker(symbol)
        except Exception:
            if candles:
                ticker = {"bid": candles[-1].close, "ask": candles[-1].close, "last": candles[-1].close}

    now_ms = int(time.time() * 1000)
    is_blackout, blackout_reason, _ = crud.check_news_blackout(db, now_ms)
    day_audit = crud.get_or_create_today_audit(db)

    lev_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_leverage").first()
    leverage = int(lev_cfg.value) if lev_cfg else 5

    margin_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_margin_mode").first()
    margin_mode = margin_cfg.value if margin_cfg else "ISOLATED"

    analysis_result = smc_engine.evaluate_smc_setup(
        candles=candles,
        symbol=symbol,
        timeframe=timeframe,
        ticker_data=ticker,
        day_audit=day_audit,
        is_news_blackout=is_blackout,
        news_blackout_reason=blackout_reason,
        htf_bias=collector_service.d_4h_bias,
        h1_alignment=collector_service.h1_alignment,
        leverage=leverage,
        margin_mode=margin_mode
    )

    return analysis_result


# ==================== 5. UPCOMING PLANS & SETUPS (TAB KẾ HOẠCH & LỆNH DỰ KIẾN) ====================

@app.get("/api/v1/setups/upcoming")
def get_upcoming_setups(db: Session = Depends(get_db)):
    """Retrieve all watch setups, daily scenarios, and execution candidates."""
    setups = db.query(models.WatchSetup).order_by(models.WatchSetup.updated_at.desc()).all()
    setup_items = []
    for s in setups:
        cond_met = json.loads(s.conditions_met) if s.conditions_met else []
        cond_rem = json.loads(s.conditions_remaining) if s.conditions_remaining else []
        setup_items.append({
            "id": s.id,
            "version": s.version,
            "strategy": s.strategy,
            "direction": s.direction,
            "timeframe": s.timeframe,
            "state": s.state,
            "htf_bias": s.htf_bias,
            "h1_alignment": s.h1_alignment,
            "trigger_mode": s.trigger_mode,
            "provisional_entry": s.provisional_entry,
            "provisional_sl": s.provisional_sl,
            "provisional_tp": s.provisional_tp,
            "confirmed_entry": s.confirmed_entry,
            "confirmed_sl": s.confirmed_sl,
            "confirmed_tp": s.confirmed_tp,
            "invalidation_price": s.invalidation_price,
            "invalidation_reason": s.invalidation_reason,
            "gross_rr": s.gross_rr,
            "net_rr": s.net_rr,
            "risk_usdt": s.risk_usdt,
            "quantity": s.quantity,
            "leverage": s.leverage,
            "margin_mode": s.margin_mode,
            "estimated_liquidation": s.estimated_liquidation,
            "conditions_met": cond_met,
            "conditions_remaining": cond_rem,
            "distance_to_entry_atr": s.distance_to_entry_atr,
            "distance_to_entry_usdt": s.distance_to_entry_usdt,
            "setup_instance_id": s.setup_instance_id,
            "near_entry_alerted_at": s.near_entry_alerted_at,
            "near_entry_distance_price": s.near_entry_distance_price,
            "near_entry_distance_atr": s.near_entry_distance_atr,
            "entry_zone_low": s.entry_zone_low or s.provisional_entry,
            "entry_zone_high": s.entry_zone_high or s.provisional_entry,
            "created_at": s.created_at,
            "updated_at": s.updated_at
        })

    # Generate Daily Scenarios
    scenarios = {
        "bullish": {
            "title": "Kịch bản Mua (Bullish)",
            "condition": "HTF duy trì Bullish, giá kiểm tra vùng Discount và xuất hiện Sweep Đáy + MSS tăng.",
            "target": "Đỉnh phiên trước (PDH) hoặc Swing High chưa bị quét.",
            "invalidation": "Nến D/4H đóng dưới Swing Low gần nhất."
        },
        "bearish": {
            "title": "Kịch bản Bán (Bearish)",
            "condition": "HTF duy trì Bearish, giá hồi về vùng Premium và xuất hiện Sweep Đỉnh + MSS giảm.",
            "target": "Đáy phiên trước (PDL) hoặc Swing Low chưa bị quét.",
            "invalidation": "Nến D/4H đóng trên Swing High gần nhất."
        },
        "no_trade": {
            "title": "Kịch bản Đứng ngoài (No-Trade)",
            "condition": "Cửa sổ Blackout tin tức USD High Impact, hoặc thị trường đi ngang biên độ hẹp (Chop/Ranging), hoặc Net R:R < 2.0.",
            "action": "Không mở lệnh. Giữ nguyên vốn và bảo toàn quota ngày."
        }
    }

    return {
        "setups": setup_items,
        "scenarios": scenarios,
        "server_time": int(time.time() * 1000)
    }


@app.post("/api/v1/setups/arm/{setup_id}")
def manual_arm_setup(setup_id: str, db: Session = Depends(get_db)):
    """Manually arm a READY watch setup into a pending paper order."""
    watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == setup_id).first()
    if not watch_setup:
        raise HTTPException(status_code=404, detail="Không tìm thấy setup với ID này")

    if watch_setup.state not in ("READY", "WAITING_PRICE", "WAITING_RETRACE"):
        raise HTTPException(status_code=400, detail=f"Không thể Arm setup đang ở trạng thái {watch_setup.state}. Chỉ arm khi setup READY hoặc chờ khớp.")

    # Guard: check if an armed order or active position already exists
    active_pos = crud.get_active_position(db)
    if active_pos:
        raise HTTPException(status_code=400, detail="Đang có một vị thế mở, không thể Arm thêm lệnh mới (tối đa 1 vị thế)")

    armed_existing = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
    if armed_existing:
        raise HTTPException(status_code=400, detail="Đã có một lệnh đang ở trạng thái armed, không thể arm thêm")

    now_ms = int(time.time() * 1000)
    order_id = f"order-{uuid.uuid4().hex[:8]}"

    new_order = models.PaperOrder(
        id=order_id,
        setup_id=watch_setup.id,
        signal_id=f"sig-{now_ms}",
        instrument="XAUUSDT",
        direction=watch_setup.direction,
        state="armed",
        order_type="MARKET",
        timeframe=watch_setup.timeframe,
        planned_entry=watch_setup.provisional_entry,
        stop_loss=watch_setup.provisional_sl,
        take_profit=watch_setup.provisional_tp,
        quantity=watch_setup.quantity,
        initial_risk_usdt=watch_setup.risk_usdt,
        risk_pct=0.25,
        gross_rr=watch_setup.gross_rr,
        estimated_net_rr=watch_setup.net_rr,
        leverage=watch_setup.leverage,
        margin_mode=watch_setup.margin_mode,
        estimated_liquidation=watch_setup.estimated_liquidation,
        created_at=now_ms,
        armed_at=now_ms,
        expires_at=now_ms + (2 * 3600 * 1000)
    )
    db.add(new_order)
    watch_setup.state = "ARMED"

    event_bus.publish_event(
        event_type="order.armed",
        aggregate_id=order_id,
        payload={
            "order_id": order_id,
            "setup_id": watch_setup.id,
            "direction": watch_setup.direction,
            "order_type": "MARKET",
            "planned_entry": watch_setup.provisional_entry,
            "stop_loss": watch_setup.provisional_sl,
            "take_profit": watch_setup.provisional_tp,
            "net_rr": watch_setup.net_rr,
            "distance_usdt": watch_setup.distance_to_entry_usdt
        },
        db=db,
        occurred_at=now_ms
    )
    db.commit()

    return {"status": "success", "message": f"Đã Arm lệnh cho setup {setup_id}", "order_id": order_id}


@app.post("/api/v1/setups/cancel/{setup_id}")
def cancel_setup(setup_id: str, db: Session = Depends(get_db)):
    watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == setup_id).first()
    if not watch_setup:
        raise HTTPException(status_code=404, detail="Không tìm thấy setup")

    watch_setup.state = "CANCELLED"
    db.commit()

    # If there was an armed order, cancel it too
    armed = db.query(models.PaperOrder).filter(models.PaperOrder.setup_id == setup_id, models.PaperOrder.state == "armed").first()
    if armed:
        armed.state = "cancelled"
        armed.closed_at = int(time.time() * 1000)
        db.commit()

    return {"status": "success", "message": f"Đã hủy theo dõi setup {setup_id}"}


# ==================== 6. AUTO PAPER TRADING & SETTINGS ====================

@app.get("/api/v1/auto/state")
def get_auto_state(db: Session = Depends(get_db)):
    """Retrieve persisted auto paper trading state from database."""
    enabled = strategy_service.get_auto_state(db)
    return {"auto_paper_enabled": enabled}


@app.post("/api/v1/auto/state")
def set_auto_state(body: dict, db: Session = Depends(get_db)):
    """Toggle persisted auto paper trading state."""
    raw_val = body.get("enabled", False)
    enabled = True if (raw_val is True or str(raw_val).lower() == "true") else False
    res = strategy_service.set_auto_state(db, enabled)
    return {"status": "success", "auto_paper_enabled": res}


@app.post("/api/v1/account/settings")
def update_account_settings(body: dict, db: Session = Depends(get_db)):
    """Update default leverage and margin mode settings."""
    now_ms = int(time.time() * 1000)
    lev = int(body.get("leverage", 5))
    mm = str(body.get("margin_mode", "ISOLATED")).upper()

    lev_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_leverage").first()
    if lev_cfg:
        lev_cfg.value = str(lev)
        lev_cfg.updated_at = now_ms
    else:
        db.add(models.SystemConfig(key="default_leverage", value=str(lev), updated_at=now_ms))

    mm_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_margin_mode").first()
    if mm_cfg:
        mm_cfg.value = mm
        mm_cfg.updated_at = now_ms
    else:
        db.add(models.SystemConfig(key="default_margin_mode", value=mm, updated_at=now_ms))

    db.commit()
    return {"status": "success", "leverage": lev, "margin_mode": mm}


# ==================== 7. ORDERS PREVIEW & EXECUTION ====================

@app.post("/api/v1/orders/preview")
def preview_order_calc(payload: dict, db: Session = Depends(get_db)):
    """Authoritative domain calculation preview with real equity, leverage, margin, and liquidation."""
    direction = payload.get("direction", "LONG")
    entry = float(payload.get("entry", 0))
    sl = float(payload.get("stop_loss", 0))
    tp = float(payload.get("take_profit", 0))

    audit = crud.get_or_create_today_audit(db)
    capital = float(payload.get("capital") or audit.current_equity or 1000.0)
    risk_pct = float(payload.get("risk_pct", 0.25))
    qty_override = payload.get("quantity_override")
    leverage = int(payload.get("leverage", 5))
    margin_mode = str(payload.get("margin_mode", "ISOLATED"))

    result = calculate_risk_reward(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        capital=capital,
        risk_pct=risk_pct,
        quantity_override=float(qty_override) if qty_override is not None else None,
        leverage=leverage,
        margin_mode=margin_mode
    )
    return result.model_dump()


@app.get("/api/v1/account/status", response_model=schemas.DayAuditResponse)
def get_account_status(db: Session = Depends(get_db)):
    """Retrieve simulated equity, dynamic counters (fills 0/3, active 0/1, armed N), and risk limits."""
    audit = crud.get_or_create_today_audit(db)
    active_pos = crud.get_active_position(db)
    armed_count = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").count()
    auto_enabled = strategy_service.get_auto_state(db)

    now_ms = int(time.time() * 1000)
    cooldown_sec = max(0, int((audit.cooldown_until - now_ms) / 1000)) if audit.cooldown_until else 0

    lev_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_leverage").first()
    leverage = int(lev_cfg.value) if lev_cfg else 5

    margin_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_margin_mode").first()
    margin_mode = margin_cfg.value if margin_cfg else "ISOLATED"

    return schemas.DayAuditResponse(
        date_str=audit.date_str,
        initial_equity=audit.initial_equity,
        current_equity=audit.current_equity,
        realized_pnl_today=audit.realized_pnl_today,
        fills_count=audit.fills_count,
        today_fills_count=audit.fills_count,
        active_positions_count=1 if active_pos else 0,
        armed_orders_count=armed_count,
        max_daily_fills=3,
        consecutive_losses=audit.consecutive_losses,
        max_consecutive_losses=2,
        daily_loss_limit_usdt=round(audit.initial_equity * 0.015, 2),
        cooldown_remaining_sec=cooldown_sec,
        is_blocked=audit.is_blocked,
        block_reason=audit.block_reason,
        auto_paper_active=auto_enabled,
        leverage=leverage,
        margin_mode=margin_mode
    )


@app.get("/api/v1/positions/active")
def get_active_paper_position(db: Session = Depends(get_db)):
    active = crud.get_active_position(db)
    if not active:
        return {"has_active_position": False, "position": None}

    curr_price = active.actual_entry or active.planned_entry
    unrealized_pnl = 0.0
    try:
        ticker = collector_service.latest_ticker or bitget_data.fetch_ticker(active.instrument)
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
    ticker = collector_service.latest_ticker or bitget_data.fetch_ticker(order.instrument)
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
    active = crud.get_paper_order(db, order_id)
    if not active or active.state != "paper_open":
        raise HTTPException(status_code=404, detail="Không tìm thấy vị thế đang mở với ID này")

    ticker = collector_service.latest_ticker or bitget_data.fetch_ticker(active.instrument)
    exit_price = ticker["bid"] if active.direction == "LONG" else ticker["ask"]

    closed = TradeLifecycleService.execute_close(
        db=db,
        order_id=order_id,
        exit_price=exit_price,
        exit_cause="MANUAL_CLOSE"
    )
    if not closed:
        raise HTTPException(status_code=400, detail="Không thể đóng vị thế (có thể đã được đóng bởi vòng giám sát)")

    return {
        "status": "success",
        "message": f"Đã đóng vị thế {closed.direction} tại {exit_price}. PnL: ${closed.realized_pnl_net:+.2f} ({closed.realized_r:+.2f}R)",
        "order": schemas.PaperOrderResponse.model_validate(closed)
    }


@app.post("/api/v1/orders/amend/{order_id}")
def amend_order_endpoint(order_id: str, payload: dict, db: Session = Depends(get_db)):
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


# ==================== 8. TELEGRAM CONFIG & TEST ====================

@app.get("/api/v1/telegram/config", response_model=schemas.TelegramConfigSchema)
def get_telegram_config(db: Session = Depends(get_db)):
    cfg = db.query(models.TelegramConfig).first()
    if not cfg:
        return schemas.TelegramConfigSchema(
            enabled=False,
            bot_token_masked="",
            chat_id="",
            subscribed_events=["READY", "NEAR_ENTRY", "ARMED", "FILLED", "TP_HIT", "SL_HIT", "MANUAL_CLOSED", "LIQUIDATED", "REJECTED", "INVALIDATED", "FEED_DOWN"],
            quiet_hours_enabled=False,
            quiet_hours_start="23:00",
            quiet_hours_end="06:00",
            bypass_critical_quiet_hours=True,
            near_entry_mode="ATR",
            near_entry_atr_mult=0.5,
            near_entry_price_dist=2.0,
            near_entry_cooldown_min=30,
            timezone="Asia/Ho_Chi_Minh",
            base_chart_url=None
        )

    # Mask token
    token = cfg.bot_token or ""
    masked = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else ("***" if token else "")
    sub_events = json.loads(cfg.subscribed_events) if cfg.subscribed_events else []

    return schemas.TelegramConfigSchema(
        enabled=cfg.enabled or False,
        bot_token_masked=masked,
        chat_id=cfg.chat_id or "",
        subscribed_events=sub_events,
        quiet_hours_enabled=cfg.quiet_hours_enabled or False,
        quiet_hours_start=cfg.quiet_hours_start or "23:00",
        quiet_hours_end=cfg.quiet_hours_end or "06:00",
        bypass_critical_quiet_hours=cfg.bypass_critical_quiet_hours if cfg.bypass_critical_quiet_hours is not None else True,
        near_entry_mode=cfg.near_entry_mode or "ATR",
        near_entry_atr_mult=cfg.near_entry_atr_mult if cfg.near_entry_atr_mult is not None else 0.5,
        near_entry_price_dist=cfg.near_entry_price_dist if cfg.near_entry_price_dist is not None else 2.0,
        near_entry_cooldown_min=cfg.near_entry_cooldown_min if cfg.near_entry_cooldown_min is not None else 30,
        timezone=cfg.timezone or "Asia/Ho_Chi_Minh",
        base_chart_url=cfg.base_chart_url
    )


@app.post("/api/v1/telegram/config")
def update_telegram_config(update: schemas.TelegramConfigUpdate, db: Session = Depends(get_db)):
    cfg = db.query(models.TelegramConfig).first()
    now_ms = int(time.time() * 1000)

    if not cfg:
        cfg = models.TelegramConfig(updated_at=now_ms)
        db.add(cfg)

    cfg.enabled = update.enabled
    if update.bot_token and update.bot_token.strip() and not update.bot_token.startswith("***") and not "..." in update.bot_token:
        cfg.bot_token = update.bot_token.strip()
    cfg.chat_id = update.chat_id.strip() if update.chat_id else ""
    cfg.subscribed_events = json.dumps(update.subscribed_events)
    cfg.quiet_hours_enabled = update.quiet_hours_enabled
    cfg.quiet_hours_start = update.quiet_hours_start
    cfg.quiet_hours_end = update.quiet_hours_end
    cfg.bypass_critical_quiet_hours = update.bypass_critical_quiet_hours
    cfg.near_entry_mode = update.near_entry_mode
    cfg.near_entry_atr_mult = update.near_entry_atr_mult
    cfg.near_entry_price_dist = update.near_entry_price_dist
    cfg.near_entry_cooldown_min = update.near_entry_cooldown_min
    cfg.timezone = update.timezone
    cfg.base_chart_url = update.base_chart_url
    cfg.updated_at = now_ms

    db.commit()
    return {"status": "success", "message": "Đã lưu cấu hình thông báo Telegram"}


@app.get("/api/v1/telegram/history", response_model=List[schemas.NotificationOutboxItem])
def get_notification_history(limit: int = 50, db: Session = Depends(get_db)):
    """Fetch persistent notification outbox history with sanitized status and delivery attempts."""
    items = (
        db.query(models.NotificationOutbox)
        .order_by(models.NotificationOutbox.created_at.desc())
        .limit(limit)
        .all()
    )
    return items


@app.post("/api/v1/telegram/outbox/retry/{item_id}")
def retry_outbox_item(item_id: int, db: Session = Depends(get_db)):
    """Reset a FAILED or RETRYING outbox item back to PENDING for re-dispatch."""
    item = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy mục outbox này")
    if item.status == "SENT":
        raise HTTPException(status_code=400, detail="Mục này đã gửi thành công tới Telegram, không gửi lại để tránh tin nhắn trùng")

    now_ms = int(time.time() * 1000)
    item.status = "PENDING"
    item.attempts = 0
    item.next_attempt_at = now_ms
    item.error_message = None
    db.commit()
    return {"status": "success", "message": f"Đã đưa tin nhắn #{item_id} trở lại hàng đợi gửi"}


@app.post("/api/v1/telegram/test")
async def test_telegram_connection(req: schemas.TelegramTestRequest, db: Session = Depends(get_db)):
    """Send test message to verify Telegram bot token and chat_id."""
    try:
        cfg = db.query(models.TelegramConfig).first()
        bot_token = req.bot_token or (cfg.bot_token if cfg else None)
        chat_id = req.chat_id or (cfg.chat_id if cfg else None)

        if not bot_token or not chat_id:
            raise HTTPException(
                status_code=400,
                detail="Thiếu Bot Token hoặc Chat ID. Vui lòng nhập đầy đủ cả hai thông tin trước khi gửi thử nghiệm."
            )

        now_vn = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%H:%M:%S %d/%m/%Y")
        test_msg = (
            "🤖 *AURUM DESK — TEST KẾT NỐI TELEGRAM THÀNH CÔNG*\n\n"
            "• Hệ thống cảnh báo tự động XAUUSDT đã kết nối thành công với bot của bạn.\n"
            "• Bạn sẽ nhận được thông báo khi có setup READY, lệnh ARMED, hoặc lệnh FILLED.\n"
            f"⏱ _{now_vn} (UTC+7)_"
        )

        success, msg_id, err = await send_telegram_direct(bot_token, chat_id, test_msg)
        if not success:
            raise HTTPException(status_code=400, detail=err or "Không thể gửi tin nhắn qua Telegram")

        return {
            "status": "success",
            "message": "Gửi tin nhắn thử nghiệm thành công! Vui lòng kiểm tra Telegram trên điện thoại của bạn.",
            "message_id": msg_id
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi nội bộ máy chủ khi kiểm tra Telegram: {type(e).__name__}: {str(e)}")


# ==================== 9. ECONOMIC NEWS, REPORTS, JOURNAL, REPLAY ====================

@app.get("/api/v1/news")
def get_economic_news(db: Session = Depends(get_db)):
    now_ms = int(time.time() * 1000)
    is_blackout, reason, remaining_min = crud.check_news_blackout(db, now_ms)
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
    content = await file.read()
    content_str = content.decode("utf-8", errors="replace")
    if file.filename.endswith(".json"):
        parsed_items = news_service.parse_forex_factory_json(content_str)
    elif file.filename.endswith(".csv"):
        parsed_items = news_service.parse_csv_calendar(content_str)
    else:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file định dạng .json hoặc .csv")

    imported_count = crud.bulk_upsert_news(db, parsed_items)
    return {"status": "success", "filename": file.filename, "imported_count": imported_count}


@app.get("/api/v1/reports")
def get_reports(limit: int = 5, db: Session = Depends(get_db)):
    reports = crud.get_latest_reports(db, limit)
    session_info = get_current_session_info()
    return {"session_info": session_info, "reports": reports}


@app.post("/api/v1/reports/generate")
def create_report(report_type: str = "SESSION_REPORT", db: Session = Depends(get_db)):
    report = generate_research_report(db, report_type)
    return {
        "status": "success",
        "report_id": report.id,
        "session_name": report.session_name,
        "content_markdown": report.content_markdown
    }


@app.get("/api/v1/journal")
def get_journal(limit: int = 50, db: Session = Depends(get_db)):
    orders = crud.list_paper_orders(db, limit)
    return [schemas.PaperOrderResponse.model_validate(o) for o in orders]


@app.get("/api/v1/lessons")
def get_lessons(setup_type: Optional[str] = None, db: Session = Depends(get_db)):
    return crud.get_lessons(db, setup_type)


@app.get("/api/v1/education")
def get_education_hub():
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
- **Đòn bẩy:** Mặc định an toàn 5x Isolated.
- **Lưu ý quan trọng:** Đây là sản phẩm PAPER TRADING mô phỏng thực tế chi phí, không phải tài khoản tiền thật.
            """
        },
        {
            "id": "smc_core",
            "title": "Phương Pháp SMC/ICT: Cấu Trúc Và Chuỗi Xác Nhận",
            "category": "CHIẾN LƯỢC",
            "content": """
Chiến lược giao dịch Aurum Desk áp dụng chuỗi kiểm chứng tuần tự theo thời gian thực (Causal Sequence):
1. **Bối cảnh Đa khung (HTF Context):** Xác định xu hướng khung D/4H và mức cản thanh khoản trọng yếu.
2. **Vùng Quan Tâm (15M POI):** Giá nằm trong Premium (>50% range) để Bán hoặc Discount (<50% range) để Mua.
3. **Liquidity Sweep (Quét thanh khoản):** Râu nến đâm thủng đỉnh/đáy confirmed swing và đóng nến quay trở lại bên trong.
4. **Displacement & MSS/CHoCH:** Nến xung lực mạnh phá vỡ cấu trúc đỉnh/đáy ngược chiều ngay sau Sweep.
5. **FVG (Fair Value Gap):** Vùng mất cân bằng được tạo bởi xung lực đó.
6. **Retrace:** Chờ giá hồi về FVG/POI trước khi mở lệnh với tỷ lệ Net R:R >= 2.0.
            """
        },
        {
            "id": "risk_management",
            "title": "Quy Tắc Quản Trị Rủi Ro Và Đòn Bẩy (FTMO Inspired)",
            "category": "RỦI RO",
            "content": """
Nguyên tắc quản trị rủi ro nghiêm ngặt của Aurum Desk:
- **Vốn giả lập:** Ký quỹ ban đầu 1.000 USDT (hoặc số dư thực tế sau giao dịch).
- **Rủi ro mỗi lệnh:** 0.25% vốn (Hard cap tối đa 0.5%).
- **Hạn mức ngày:** Không quá 3 lệnh mở trong ngày (UTC+7).
- **Giới hạn lỗ ngày (Daily Loss Cap):** 1.5% vốn đầu ngày.
- **Dừng chuỗi:** Khóa mở lệnh ngày nếu chịu 2 lệnh dừng lỗ liên tiếp.
- **Thời gian nghỉ (Cooldown):** 30 phút sau khi đóng bất kỳ vị thế nào.
- **Tỷ lệ Net R:R:** Tối thiểu 1:2.0 sau khi trừ trượt giá và phí giao dịch taker/maker.
- **Đệm thanh lý (Liquidation Buffer):** Luôn đảm bảo khoảng cách an toàn giữa Stop Loss và mức thanh lý ước tính.
            """
        }
    ]
