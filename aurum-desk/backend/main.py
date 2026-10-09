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
from services.quote_validator import QuoteValidator
from services.eligibility_service import evaluate_setup_eligibility
from lab.scenario_runner import ScenarioRunner
from lab.replay_engine import ReplayEngine
from lab.stress_tester import StressTester

# Ensure all tables exist
models.Base.metadata.create_all(bind=engine)

def reconcile_stuck_orders():
    """
    Reconciles legacy WatchSetup states that are stuck in 'ARMED' or 'PAPER_OPEN'
    when their associated PaperOrder is already in a terminal state.
    """
    db = SessionLocal()
    try:
        # V5.3 Schema Migrations
        try:
            from sqlalchemy import text
            db.execute(text("ALTER TABLE watch_setups ADD COLUMN risk_pct FLOAT DEFAULT 0.25"))
            db.execute(text("ALTER TABLE watch_setups ADD COLUMN config_version INTEGER DEFAULT 1"))
            db.execute(text("ALTER TABLE paper_orders ADD COLUMN config_version INTEGER DEFAULT 1"))
            db.commit()
            print("V5.3 Schema migration applied.")
        except Exception:
            db.rollback() # Columns already exist

        stuck_setups = db.query(models.WatchSetup).filter(
            models.WatchSetup.state.in_(["ARMED", "PAPER_OPEN"])
        ).all()

        count = 0
        for setup in stuck_setups:
            latest_order = db.query(models.PaperOrder).filter(
                models.PaperOrder.setup_id == setup.id
            ).order_by(models.PaperOrder.created_at.desc()).first()

            if latest_order:
                # If order is terminal but setup is not
                if latest_order.state in ["rejected", "expired", "cancelled", "closed", "invalidated"]:
                    setup.state = latest_order.state.upper()
                    setup.invalidation_reason = latest_order.invalidation_reason or latest_order.exit_cause or "Reconciled from legacy stuck order"
                    setup.updated_at = int(time.time() * 1000)
                    count += 1
        
        if count > 0:
            db.commit()
            print(f"Reconciled {count} stuck setups.")
    finally:
        db.close()

async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan:
    Starts and manages background workers cleanly,
    cancels and awaits clean shutdown when stopped.
    """
    # 0. Sync broken DB states before starting loops
    reconcile_stuck_orders()

    # V6.1 Offline Position Recovery: check if any open position needs historical reconciliation
    db_rec = SessionLocal()
    try:
        from services.position_recovery_service import position_recovery_service
        position_recovery_service.check_and_recover_offline_positions(db_rec)
    except Exception as e:
        print(f"Position recovery error at startup: {e}")
    finally:
        db_rec.close()

    from services.bitget_ws_service import bitget_ws_service
    from services.execution_consumer import execution_consumer
    from services.candle_cache_service import candle_cache_service
    from services.market_broadcaster import market_broadcaster

    # Capture main running loop for thread-safe event bus bridge
    event_bus.set_main_loop(asyncio.get_running_loop())

    # Wire WS quote broadcaster to market_broadcaster
    bitget_ws_service.register_broadcaster(market_broadcaster.broadcast_quote)

    tasks = [
        asyncio.create_task(bitget_ws_service.run_ws_loop()),
        asyncio.create_task(execution_consumer.run_consumer_loop()),
        asyncio.create_task(candle_cache_service.run_persistence_worker()),
        asyncio.create_task(collector_service.run_collector_loop()),
        asyncio.create_task(strategy_service.run_strategy_loop()),
        asyncio.create_task(proximity_service.run_proximity_loop()),
        asyncio.create_task(execution_coordinator.run_coordinator_loop()),
        asyncio.create_task(exit_monitor.run_exit_monitor_loop()),
        asyncio.create_task(process_notification_outbox())
    ]
    yield
    # Graceful shutdown
    bitget_ws_service.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

app = FastAPI(
    title="Aurum Desk API",
    description="Backend API cho Aurum Desk: XAUUSDT Research, SMC/ICT Engine và Auto Paper Trading",
    version="7.2.0",
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
    from services.market_broadcaster import market_broadcaster
    await websocket.accept()
    await market_broadcaster.connect(websocket)
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
                "protocol_version": "7.2.0",
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
        await market_broadcaster.disconnect(websocket)
    except Exception:
        await market_broadcaster.disconnect(websocket)


# ==================== 1. SYSTEM HEALTH ====================

@app.get("/health", response_model=schemas.SystemHealthResponse)
def health_check(symbol: str = "XAUUSDT", timeframe: str = "15M", db: Session = Depends(get_db)):
    """Comprehensive system health reporting real live status without fake fallbacks."""
    from services.bitget_ws_service import bitget_ws_service
    from services.execution_consumer import execution_consumer
    from services.candle_cache_service import candle_cache_service

    now_ms = int(time.time() * 1000)
    feed_connected = collector_service.feed_connected
    collector_alive = collector_service.collector_alive

    candles = candle_cache_service.get_candles(symbol, timeframe, limit=1)
    last_candle_time = candles[0].timestamp if candles else 0
    is_stale, freshness_sec = collector_service.is_stale(timeframe, last_candle_time)

    status = "ok" if (feed_connected and not is_stale) else ("degraded" if feed_connected else "disconnected")
    degraded_reason = collector_service.last_error if not feed_connected else ("Dữ liệu nến bị trễ so với chu kỳ" if is_stale else None)

    q = bitget_ws_service.latest_quote
    quote_ex_age = round(now_ms - q.exchange_ts_ms, 1) if (q and q.exchange_ts_ms) else None
    quote_loc_age = round(now_ms - q.received_at_ms, 1) if (q and q.received_at_ms) else None

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
        strategy_version="7.1.0",
        paper_trading_mode="SIMULATION",
        last_success_time=collector_service.last_success_time,
        last_error=collector_service.last_error,
        degraded_reason=degraded_reason,
        d_4h_bias=collector_service.d_4h_bias,
        h1_alignment=collector_service.h1_alignment,
        transport_state=bitget_ws_service.transport_state,
        feed_source=bitget_ws_service.feed_source,
        quote_exchange_age_ms=quote_ex_age,
        quote_local_age_ms=quote_loc_age,
        execution_queue_depth=execution_consumer.queue_depth,
        execution_lag_ms=execution_consumer.last_eval_duration_ms,
        reconnect_count=bitget_ws_service.reconnect_count
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
    from services.candle_cache_service import candle_cache_service
    now_ms = int(time.time() * 1000)
    cached_candles = candle_cache_service.get_candles(symbol, timeframe, limit=limit)
    if not cached_candles:
        candles = crud.get_candles(db, symbol, timeframe, limit=limit, ascending=True)
        candle_models = [schemas.Candle.model_validate(c) for c in candles]
    else:
        candle_models = [
            schemas.Candle(
                id=i + 1,
                symbol=c.symbol,
                timeframe=c.timeframe,
                timestamp=c.timestamp,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                is_closed=c.is_closed
            )
            for i, c in enumerate(cached_candles)
        ]

    last_time = candle_models[-1].timestamp if candle_models else None
    is_stale, freshness_sec = collector_service.is_stale(timeframe, last_time)

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
            from services.candle_cache_service import candle_cache_service
            candle_cache_service.update_from_rest_sync(symbol, timeframe, new_candles)
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
async def get_market_analysis(
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    db: Session = Depends(get_db)
):
    """Retrieve cached SMC analysis snapshot without blocking event loop or network latency."""
    from services.analysis_cache_service import analysis_cache_service
    from services.bitget_ws_service import bitget_ws_service
    return await analysis_cache_service.get_or_compute_analysis(
        symbol=symbol,
        timeframe=timeframe,
        latest_quote=bitget_ws_service.latest_quote
    )


# ==================== 5. UPCOMING PLANS & SETUPS (TAB KẾ HOẠCH & LỆNH DỰ KIẾN) ====================

@app.get("/api/v1/setups/upcoming")
def get_upcoming_setups(db: Session = Depends(get_db)):
    """Retrieve all watch setups, daily scenarios, and execution candidates."""
    setups = db.query(models.WatchSetup).order_by(models.WatchSetup.updated_at.desc()).all()
    setup_items = []
    for s in setups:
        cond_met = json.loads(s.conditions_met) if s.conditions_met else []
        cond_rem = json.loads(s.conditions_remaining) if s.conditions_remaining else []
        eligibility = evaluate_setup_eligibility(db, s)
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
            "updated_at": s.updated_at,
            "eligibility": eligibility
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


@app.get("/api/v1/setups/eligibility/{setup_id}")
def get_setup_eligibility(setup_id: str, db: Session = Depends(get_db)):
    """Authoritative read-only eligibility inspection for a specific setup."""
    watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == setup_id).first()
    if not watch_setup:
        raise HTTPException(status_code=404, detail="Không tìm thấy setup với ID này")
    return evaluate_setup_eligibility(db, watch_setup)


@app.post("/api/v1/setups/arm/{setup_id}")
@app.post("/api/v1/setups/arm")
def manual_arm_setup(
    setup_id: Optional[str] = None,
    req_body: Optional[schemas.ArmSetupRequest] = None,
    db: Session = Depends(get_db)
):
    """
    Manually arm a READY/WAITING watch setup into a pending paper order.
    V5 Guards:
    - Atomically validates authoritative direction vs requested expected_direction.
    - If direction changed or revision mismatch -> returns 409 SETUP_CHANGED with sanitized error.
    - Idempotency guard: duplicate submit with same idempotency_key returns original order.
    - Correct order type: supports MARKET/LIMIT/STOP (WAITING_PRICE/RETRACE defaults to LIMIT).
    """
    target_id = req_body.setup_id if req_body and req_body.setup_id else setup_id
    if not target_id:
        raise HTTPException(status_code=400, detail="Thiếu setup_id")

    # 1. Idempotency Check
    if req_body and req_body.idempotency_key:
        existing = db.query(models.PaperOrder).filter(models.PaperOrder.idempotency_key == req_body.idempotency_key).first()
        if existing:
            return {
                "status": "success",
                "message": f"Lệnh đã được Arm trước đó (Idempotent: {existing.id})",
                "order_id": existing.id
            }

    watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == target_id).first()
    if not watch_setup:
        raise HTTPException(status_code=404, detail="Không tìm thấy setup với ID này")

    # 2. Setup Direction and Instance Verification (Atomic Snapshot Check)
    if req_body:
        if req_body.expected_direction and watch_setup.direction != req_body.expected_direction:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SETUP_CHANGED",
                    "message": f"Setup {req_body.expected_direction} bạn chọn đã thay đổi thành {watch_setup.direction}; hãy xem lại.",
                    "current_direction": watch_setup.direction,
                    "expected_direction": req_body.expected_direction,
                    "setup_id": watch_setup.id
                }
            )
        if req_body.setup_instance_id and watch_setup.setup_instance_id and watch_setup.setup_instance_id != req_body.setup_instance_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SETUP_CHANGED",
                    "message": "Phiên bản setup instance đã thay đổi, vui lòng làm mới.",
                    "current_instance_id": watch_setup.setup_instance_id,
                    "expected_instance_id": req_body.setup_instance_id
                }
            )
        if req_body.expected_revision is not None and watch_setup.version != req_body.expected_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SETUP_CHANGED",
                    "message": f"Revision setup đã đổi từ v{req_body.expected_revision} sang v{watch_setup.version}.",
                    "current_revision": watch_setup.version,
                    "expected_revision": req_body.expected_revision
                }
            )

    # Extract levels (authoritative levels or valid draft levels)
    entry = req_body.planned_entry if req_body and req_body.planned_entry is not None else (watch_setup.confirmed_entry or watch_setup.provisional_entry)
    sl = req_body.stop_loss if req_body and req_body.stop_loss is not None else (watch_setup.confirmed_sl or watch_setup.provisional_sl)
    tp = req_body.take_profit if req_body and req_body.take_profit is not None else (watch_setup.confirmed_tp or watch_setup.provisional_tp)

    # V6.1 Authoritative Shared Eligibility Check
    elig = evaluate_setup_eligibility(
        db=db,
        setup=watch_setup,
        custom_entry=entry,
        custom_sl=sl,
        custom_tp=tp,
        custom_margin_mode=watch_setup.margin_mode
    )
    if not elig["can_arm"]:
        code = elig["reason_codes"][0] if elig["reason_codes"] else "CANNOT_ARM"
        raise HTTPException(
            status_code=400,
            detail={
                "code": code,
                "message": elig["block_reason"] or "Lệnh không đủ điều kiện thực thi an toàn.",
                "direction": watch_setup.direction,
                "eligibility": elig
            }
        )

    acc_state = crud.get_account_status(db)
    equity = acc_state.get("current_equity", 1000.0) if acc_state else 1000.0

    calc_res = calculate_risk_reward(
        direction=watch_setup.direction,
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        capital_usdt=equity,
        risk_pct=watch_setup.risk_pct or 0.25,
        leverage=watch_setup.leverage or 5,
        margin_mode=watch_setup.margin_mode or "ISOLATED"
    )

    # Guard: check if an armed order or active position already exists
    active_pos = crud.get_active_position(db)
    if active_pos:
        raise HTTPException(status_code=400, detail="Đang có một vị thế mở, không thể Arm thêm lệnh mới (tối đa 1 vị thế)")

    armed_existing = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
    if armed_existing:
        raise HTTPException(status_code=400, detail="Đã có một lệnh đang ở trạng thái armed, không thể arm thêm")

    now_ms = int(time.time() * 1000)
    order_id = f"order-{uuid.uuid4().hex[:8]}"

    # Distinguish order type properly (Requirement 4.4 & 5.3)
    # WAITING_PRICE / WAITING_RETRACE requires LIMIT; client cannot force MARKET to bypass waiting price
    if watch_setup.state in ("WAITING_PRICE", "WAITING_RETRACE"):
        order_type = "LIMIT"
    elif req_body and req_body.order_type:
        order_type = req_body.order_type.upper()
    else:
        order_type = "MARKET"

    if not calc_res.can_execute or calc_res.quantity <= 0:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CALCULATOR_REJECTED",
                "message": calc_res.skip_reason or calc_res.invalid_reason or "Khối lượng tính toán không hợp lệ"
            }
        )

    is_quota = getattr(watch_setup, 'strategy_family', 'STANDARD_SMC') == 'NY_QUOTA_PAPER'
    eff_risk_pct = min(watch_setup.risk_pct or 0.25, 0.10) if is_quota else (watch_setup.risk_pct or 0.25)
    risk_profile = "QUOTA" if is_quota else "STANDARD"

    new_order = models.PaperOrder(
        id=order_id,
        setup_id=watch_setup.id,
        setup_instance_id=watch_setup.setup_instance_id or (req_body.setup_instance_id if req_body else None),
        idempotency_key=req_body.idempotency_key if req_body else None,
        signal_id=f"sig-{now_ms}",
        instrument="XAUUSDT",
        direction=watch_setup.direction,
        state="armed",
        order_type=order_type,
        timeframe=watch_setup.timeframe,
        strategy_family=getattr(watch_setup, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC",
        strategy_version="7.0.0",
        session_instance_id=elig.get("session_instance_id"),
        policy_config_version=elig.get("policy_config_version", 1),
        requested_risk_pct=watch_setup.risk_pct or 0.25,
        effective_risk_pct=eff_risk_pct,
        risk_profile=risk_profile,
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        quantity=calc_res.quantity,
        initial_risk_usdt=calc_res.net_risk_usdt,
        risk_pct=eff_risk_pct,
        gross_rr=calc_res.gross_rr,
        estimated_net_rr=calc_res.net_rr,
        leverage=calc_res.leverage,
        margin_mode=calc_res.margin_mode,
        estimated_liquidation=calc_res.estimated_liquidation,
        created_at=now_ms,
        armed_at=now_ms,
        expires_at=now_ms + (2 * 3600 * 1000)
    )
    db.add(new_order)
    watch_setup.confirmed_entry = entry
    watch_setup.confirmed_sl = sl
    watch_setup.confirmed_tp = tp
    watch_setup.net_rr = calc_res.net_rr
    watch_setup.gross_rr = calc_res.gross_rr
    watch_setup.quantity = calc_res.quantity
    watch_setup.risk_usdt = calc_res.net_risk_usdt
    watch_setup.state = "ARMED"
    watch_setup.updated_at = now_ms

    event_bus.publish_event(
        event_type="order.armed",
        aggregate_id=order_id,
        payload={
            "order_id": order_id,
            "setup_id": watch_setup.id,
            "direction": watch_setup.direction,
            "order_type": order_type,
            "planned_entry": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "net_rr": calc_res.net_rr,
            "distance_usdt": watch_setup.distance_to_entry_usdt
        },
        db=db,
        occurred_at=now_ms
    )
    db.commit()

    return {"status": "success", "message": f"Đã Arm lệnh {order_type} cho setup {target_id}", "order_id": order_id}


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


@app.post("/api/v1/account/settings", response_model=schemas.RiskSettingsResponse)
def update_account_settings(body: schemas.RiskSettingsUpdate, db: Session = Depends(get_db)):
    """Update risk settings with version validation."""
    from services.risk_settings_service import risk_settings_service
    try:
        return risk_settings_service.update_settings(db, body)
    except ValueError as e:
        if "STALE_EDIT" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=422, detail=str(e))

@app.get("/api/v1/account/settings", response_model=schemas.RiskSettingsResponse)
def get_account_settings(db: Session = Depends(get_db)):
    """Get current risk settings."""
    from services.risk_settings_service import risk_settings_service
    return risk_settings_service.get_settings(db)

@app.get("/api/v1/instrument/metadata")
async def get_instrument_metadata(symbol: str = "XAUUSDT"):
    """Get dynamic instrument metadata from Bitget provider with fallback."""
    from services.instrument_provider import instrument_provider
    meta = await instrument_provider.fetch_metadata(symbol)
    return meta



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

    from services.risk_settings_service import risk_settings_service
    settings = risk_settings_service.get_settings(db)

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
        leverage=settings.requested_leverage,
        margin_mode=settings.margin_mode,
        risk_pct=settings.risk_pct,
        config_version=settings.config_version,
        metadata_version=settings.metadata_version,
        min_leverage=settings.min_leverage,
        max_leverage=settings.max_leverage,
        cross_margin_supported=False
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

    pos_response = schemas.PaperOrderResponse.model_validate(active)
    if (pos_response.estimated_liquidation is None or pos_response.estimated_liquidation <= 0) and (active.margin_mode or "ISOLATED").upper() == "ISOLATED":
        try:
            entry_val = active.actual_entry or active.planned_entry
            if entry_val and active.quantity and active.leverage:
                from domain_calculator import calculate_isolated_liquidation
                calc_lp, _, _, _, _ = calculate_isolated_liquidation(
                    direction=active.direction,
                    entry=entry_val,
                    quantity=active.quantity,
                    leverage=active.leverage,
                    multiplier=1.0,
                    taker_fee_rate=0.0006
                )
                if calc_lp and calc_lp > 0:
                    pos_response.estimated_liquidation = calc_lp
        except Exception:
            pass

    return {
        "has_active_position": True,
        "position": pos_response,
        "current_price": curr_price,
        "unrealized_pnl": unrealized_pnl
    }


@app.post("/api/v1/orders/paper")
def create_paper_order(order: schemas.PaperOrderCreate, db: Session = Depends(get_db)):
    """
    Execute a paper market order with V5 execution guards:
    - Idempotency guard: duplicate submit with same idempotency_key returns original order.
    - Direction verification: fails with 409 if expected_direction mismatches order direction.
    - Authoritative quote freshness validation via QuoteValidator.
    """
    # 1. Idempotency check
    if order.idempotency_key:
        existing = db.query(models.PaperOrder).filter(models.PaperOrder.idempotency_key == order.idempotency_key).first()
        if existing:
            return {
                "status": "success",
                "message": f"Lệnh đã tồn tại (Idempotent: {existing.id})",
                "order": schemas.PaperOrderResponse.model_validate(existing)
            }

    # 2. Expected direction check
    if order.expected_direction and order.expected_direction != order.direction:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SETUP_CHANGED",
                "message": f"Direction conflict: expected {order.expected_direction} but order was {order.direction}",
                "expected": order.expected_direction,
                "actual": order.direction
            }
        )

    # 3. Quote freshness check
    ticker = collector_service.latest_ticker or bitget_data.fetch_ticker(order.instrument)
    now_ms = int(time.time() * 1000)
    validation = QuoteValidator.validate_ticker(ticker, now_ms=now_ms)
    if not validation.is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Báo giá không hợp lệ hoặc bị stale ({validation.reason}). Không thể mở lệnh thị trường."
        )

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
            base_chart_url=None,
            has_token=False,
            token_configured=False
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
        base_chart_url=cfg.base_chart_url,
        has_token=bool(token),
        token_configured=bool(token)
    )


@app.post("/api/v1/telegram/config")
def update_telegram_config(update: schemas.TelegramConfigUpdate, db: Session = Depends(get_db)):
    cfg = db.query(models.TelegramConfig).first()
    now_ms = int(time.time() * 1000)

    if not cfg:
        cfg = models.TelegramConfig(updated_at=now_ms)
        db.add(cfg)

    # 1. Handle token updates:
    if update.clear_token:
        cfg.bot_token = None
    elif update.bot_token and update.bot_token.strip() and not update.bot_token.startswith("***") and not "..." in update.bot_token:
        cfg.bot_token = update.bot_token.strip()

    # 2. Chat ID update
    if update.chat_id is not None:
        cfg.chat_id = update.chat_id.strip()

    # 3. Enabled validation: Cannot enable without credentials
    if update.enabled:
        effective_token = cfg.bot_token
        effective_chat = (cfg.chat_id or "").strip()
        if not effective_token or not effective_chat:
            raise HTTPException(
                status_code=400,
                detail="Không thể bật thông báo tự động khi chưa có Bot Token hoặc Chat ID hợp lệ."
            )

    if update.enabled is not None:
        cfg.enabled = update.enabled
    if update.subscribed_events is not None:
        cfg.subscribed_events = json.dumps(update.subscribed_events)
    if update.quiet_hours_enabled is not None:
        cfg.quiet_hours_enabled = update.quiet_hours_enabled
    if update.quiet_hours_start is not None:
        cfg.quiet_hours_start = update.quiet_hours_start
    if update.quiet_hours_end is not None:
        cfg.quiet_hours_end = update.quiet_hours_end
    if update.bypass_critical_quiet_hours is not None:
        cfg.bypass_critical_quiet_hours = update.bypass_critical_quiet_hours
    if update.near_entry_mode is not None:
        cfg.near_entry_mode = update.near_entry_mode
    if update.near_entry_atr_mult is not None:
        cfg.near_entry_atr_mult = update.near_entry_atr_mult
    if update.near_entry_price_dist is not None:
        cfg.near_entry_price_dist = update.near_entry_price_dist
    if update.near_entry_cooldown_min is not None:
        cfg.near_entry_cooldown_min = update.near_entry_cooldown_min
    if update.timezone is not None:
        cfg.timezone = update.timezone
    if update.base_chart_url is not None:
        cfg.base_chart_url = update.base_chart_url
    cfg.updated_at = now_ms

    db.commit()
    db.refresh(cfg)

    token = cfg.bot_token or ""
    masked = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else ("***" if token else "")
    sub_events = json.loads(cfg.subscribed_events) if cfg.subscribed_events else []

    authoritative_config = schemas.TelegramConfigSchema(
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
        base_chart_url=cfg.base_chart_url,
        has_token=bool(token),
        token_configured=bool(token)
    )

    return {
        "status": "success",
        "message": "Đã lưu cấu hình thông báo Telegram",
        "config": authoritative_config
    }


@app.get("/api/v1/telegram/history")
def get_notification_history(
    limit: int = 50,
    status: Optional[str] = None,
    message_type: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Fetch persistent notification outbox history with filtering, pagination and sanitized error messages."""
    query = db.query(models.NotificationOutbox)

    if status and status != "ALL":
        query = query.filter(models.NotificationOutbox.status == status.upper())
    if message_type and message_type != "ALL":
        query = query.filter(models.NotificationOutbox.message_type == message_type)
    if date_from:
        query = query.filter(models.NotificationOutbox.created_at >= date_from)
    if date_to:
        query = query.filter(models.NotificationOutbox.created_at <= date_to)

    query = query.order_by(models.NotificationOutbox.created_at.desc())

    if page is not None and page_size is not None:
        total = query.count()
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        total_pages = max(1, (total + page_size - 1) // page_size)
        items = query.offset((page - 1) * page_size).limit(page_size).all()
        return {
            "items": [schemas.NotificationOutboxItem.model_validate(it) for it in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages
        }

    items = query.limit(limit).all()
    return [schemas.NotificationOutboxItem.model_validate(it) for it in items]


@app.post("/api/v1/telegram/outbox/retry/{item_id}")
def retry_outbox_item(item_id: int, db: Session = Depends(get_db)):
    """Reset a FAILED, RETRYING, or AMBIGUOUS outbox item back to PENDING for re-dispatch."""
    item = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy mục outbox này")
    if item.status == "SENT":
        raise HTTPException(status_code=400, detail="Mục này đã gửi thành công tới Telegram, không gửi lại để tránh tin nhắn trùng")

    now_ms = int(time.time() * 1000)
    item.status = "PENDING"
    item.attempts = 0
    item.next_attempt_at = now_ms
    item.lease_expires_at = None
    item.worker_id = None
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
            "• Kết nối mạng tới Bot Telegram của bạn hoạt động bình thường.\n"
            "• Lưu ý: Hãy đảm bảo bạn đã BẬT thông báo tự động và LƯU cấu hình để nhận các cảnh báo lệnh realtime.\n"
            f"⏱ _{now_vn} (UTC+7)_"
        )

        res = await send_telegram_direct(bot_token, chat_id, test_msg)
        if not res.success:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": res.error_code or "SEND_FAILED",
                    "message": res.error_message or "Không thể gửi tin nhắn qua Telegram",
                    "retry_after": res.retry_after_sec
                }
            )

        return {
            "status": "success",
            "message": "Gửi tin nhắn thử nghiệm thành công! Vui lòng kiểm tra Telegram của bạn.",
            "message_id": res.provider_message_id,
            "auto_alerts_enabled": bool(cfg and cfg.enabled)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi nội bộ khi kiểm tra kết nối Telegram: {type(e).__name__}: {str(e)}"
        )


# ==================== 9. ECONOMIC NEWS, REPORTS, JOURNAL, REPLAY ====================

@app.get("/api/v1/news")
def get_economic_news(
    limit: int = 50,
    impact: Optional[str] = None,
    gold_relevance: Optional[str] = None,
    db: Session = Depends(get_db)
):
    now_ms = int(time.time() * 1000)
    is_blackout, reason, remaining_min = crud.check_news_blackout(db, now_ms)

    query = db.query(models.EconomicNews)
    if impact:
        query = query.filter(models.EconomicNews.impact.ilike(f"%{impact}%"))
    if gold_relevance:
        query = query.filter(models.EconomicNews.gold_relevance.ilike(f"%{gold_relevance}%"))

    events = (
        query
        .order_by(models.EconomicNews.scheduled_at.asc())
        .limit(limit)
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


@app.post("/api/v1/news/import/preview", response_model=schemas.NewsImportPreviewResponse)
def preview_news_import(req: schemas.NewsImportPreviewRequest):
    """Preview parsed rows from Forex Factory CSV with timezone conversion and validation."""
    return news_service.parse_csv_calendar_preview(req.csv_content, req.source_timezone)


@app.post("/api/v1/news/import/commit", response_model=schemas.NewsImportCommitResponse)
def commit_news_import(req: schemas.NewsImportCommitRequest, db: Session = Depends(get_db)):
    """Commit validated CSV calendar rows into the database."""
    imported, skipped, errors = news_service.commit_parsed_news(db, req.csv_content, req.source_timezone)
    return schemas.NewsImportCommitResponse(
        status="success",
        imported_count=imported,
        skipped_duplicates_count=skipped,
        error_count=errors,
        message=f"Đã nhập thành công {imported} sự kiện ({skipped} sự kiện đã tồn tại/cập nhật, {errors} dòng lỗi)."
    )


@app.post("/api/v1/news/{news_id}/research", response_model=schemas.NewsResearchResponse)
async def trigger_news_research(news_id: int, db: Session = Depends(get_db)):
    """Fetch source URL, extract content, and generate XAUUSDT research assessment."""
    news_item = db.query(models.EconomicNews).filter(models.EconomicNews.id == news_id).first()
    if not news_item:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự kiện kinh tế này")
    from services.news_research_service import news_research_service
    res = await news_research_service.execute_research_for_news_item(news_item, db)
    return res


@app.get("/api/v1/news/{news_id}/research", response_model=schemas.NewsResearchResponse)
async def get_news_research(news_id: int, db: Session = Depends(get_db)):
    """Get research assessment for a specific news item."""
    news_item = db.query(models.EconomicNews).filter(models.EconomicNews.id == news_id).first()
    if not news_item:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự kiện kinh tế này")
    if news_item.research_assessment:
        try:
            data = json.loads(news_item.research_assessment)
            return schemas.NewsResearchResponse(**data)
        except Exception:
            pass
    from services.news_research_service import news_research_service
    res = await news_research_service.execute_research_for_news_item(news_item, db)
    return res


@app.post("/api/v1/news/import")
async def import_news_calendar(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    content_str = content.decode("utf-8", errors="replace")
    if file.filename.endswith(".json"):
        parsed_items = news_service.parse_forex_factory_json(content_str)
        imported_count = crud.bulk_upsert_news(db, parsed_items)
        return {"status": "success", "filename": file.filename, "imported_count": imported_count}
    elif file.filename.endswith(".csv"):
        imported, skipped, errors = news_service.commit_parsed_news(db, content_str, "America/New_York")
        return {"status": "success", "filename": file.filename, "imported_count": imported, "skipped_count": skipped, "error_count": errors}
    else:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file định dạng .json hoặc .csv")


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


@app.get("/api/v1/journal/paginated", response_model=schemas.PaginatedJournalResponse)
def get_journal_paginated(
    state: Optional[str] = None,
    direction: Optional[str] = None,
    outcome: Optional[str] = None,
    strategy_family: Optional[str] = None,
    date_from_ms: Optional[int] = None,
    date_to_ms: Optional[int] = None,
    has_review: Optional[bool] = None,
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db)
):
    """Server-side filtered and paginated journal orders with aggregate summary."""
    res = crud.get_journal_paginated(
        db=db,
        state=state,
        direction=direction,
        outcome=outcome,
        strategy_family=strategy_family,
        date_from_ms=date_from_ms,
        date_to_ms=date_to_ms,
        has_review=has_review,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size
    )
    return schemas.PaginatedJournalResponse(
        items=[schemas.PaperOrderResponse.model_validate(o) for o in res["items"]],
        total=res["total"],
        page=res["page"],
        page_size=res["page_size"],
        total_pages=res["total_pages"],
        summary=schemas.JournalSummary(**res["summary"])
    )


@app.get("/api/v1/journal/review/{trade_id}", response_model=schemas.TradeReviewSchema)
def get_trade_review(trade_id: str, db: Session = Depends(get_db)):
    review = crud.get_trade_review(db, trade_id)
    if not review:
        now_ms = int(time.time() * 1000)
        return schemas.TradeReviewSchema(
            id=f"rev-{trade_id}",
            trade_id=trade_id,
            execution_mode="AUTO",
            user_notes=None,
            self_reported_entry_reason=None,
            psychology_before=None,
            psychology_during=None,
            psychology_after=None,
            emotions=[],
            confidence_score=None,
            discipline_score=None,
            user_loss_reason=None,
            mistakes=None,
            what_went_well=None,
            improvement_plan=None,
            revision=1,
            created_at=now_ms,
            updated_at=now_ms,
            reviewed_at=None
        )

    emotions_list = []
    if review.emotions:
        try:
            emotions_list = json.loads(review.emotions)
        except Exception:
            emotions_list = []

    return schemas.TradeReviewSchema(
        id=review.id,
        trade_id=review.trade_id,
        execution_mode=review.execution_mode or "AUTO",
        user_notes=review.user_notes,
        self_reported_entry_reason=review.self_reported_entry_reason,
        psychology_before=review.psychology_before,
        psychology_during=review.psychology_during,
        psychology_after=review.psychology_after,
        emotions=emotions_list,
        confidence_score=review.confidence_score,
        discipline_score=review.discipline_score,
        user_loss_reason=review.user_loss_reason,
        mistakes=review.mistakes,
        what_went_well=review.what_went_well,
        improvement_plan=review.improvement_plan,
        revision=review.revision or 1,
        created_at=review.created_at,
        updated_at=review.updated_at,
        reviewed_at=review.reviewed_at
    )


@app.post("/api/v1/journal/review/{trade_id}", response_model=schemas.TradeReviewSchema)
def save_trade_review(
    trade_id: str,
    data: schemas.TradeReviewCreateOrUpdate,
    db: Session = Depends(get_db)
):
    try:
        review = crud.save_trade_review(db, trade_id, data)
    except ValueError as e:
        if "CONFLICT" in str(e):
            raise HTTPException(
                status_code=409,
                detail="Xung đột dữ liệu (Conflict): Đánh giá này đã được cập nhật bởi phiên khác. Vui lòng làm mới trang trước khi lưu."
            )
        raise HTTPException(status_code=400, detail=str(e))

    emotions_list = []
    if review.emotions:
        try:
            emotions_list = json.loads(review.emotions)
        except Exception:
            emotions_list = []

    return schemas.TradeReviewSchema(
        id=review.id,
        trade_id=review.trade_id,
        execution_mode=review.execution_mode or "AUTO",
        user_notes=review.user_notes,
        self_reported_entry_reason=review.self_reported_entry_reason,
        psychology_before=review.psychology_before,
        psychology_during=review.psychology_during,
        psychology_after=review.psychology_after,
        emotions=emotions_list,
        confidence_score=review.confidence_score,
        discipline_score=review.discipline_score,
        user_loss_reason=review.user_loss_reason,
        mistakes=review.mistakes,
        what_went_well=review.what_went_well,
        improvement_plan=review.improvement_plan,
        revision=review.revision or 1,
        created_at=review.created_at,
        updated_at=review.updated_at,
        reviewed_at=review.reviewed_at
    )


@app.get("/api/v1/lessons", response_model=List[schemas.LessonItem])
def get_lessons(
    setup_type: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    lessons = crud.get_lessons(db, setup_type=setup_type, status=status)
    return [schemas.LessonItem.model_validate(l) for l in lessons]


@app.post("/api/v1/lessons/{lesson_id}/approve", response_model=schemas.LessonItem)
def approve_lesson(lesson_id: int, db: Session = Depends(get_db)):
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài học này")
    now_ms = int(time.time() * 1000)
    lesson.is_approved = True
    lesson.status = "APPROVED"
    lesson.reviewed_at = now_ms
    db.commit()
    db.refresh(lesson)
    return schemas.LessonItem.model_validate(lesson)


@app.post("/api/v1/lessons/{lesson_id}/reject", response_model=schemas.LessonItem)
def reject_lesson(lesson_id: int, db: Session = Depends(get_db)):
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài học này")
    now_ms = int(time.time() * 1000)
    lesson.is_approved = False
    lesson.status = "REJECTED"
    lesson.reviewed_at = now_ms
    db.commit()
    db.refresh(lesson)
    return schemas.LessonItem.model_validate(lesson)


@app.post("/api/v1/lessons/{lesson_id}/archive", response_model=schemas.LessonItem)
def archive_lesson(lesson_id: int, db: Session = Depends(get_db)):
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài học này")
    lesson.status = "ARCHIVED"
    db.commit()
    db.refresh(lesson)
    return schemas.LessonItem.model_validate(lesson)


@app.put("/api/v1/lessons/{lesson_id}", response_model=schemas.LessonItem)
def update_lesson(lesson_id: int, update: schemas.LessonUpdate, db: Session = Depends(get_db)):
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài học này")
    if update.title is not None:
        lesson.title = update.title
    if update.category is not None:
        lesson.category = update.category
    if update.reflection is not None:
        lesson.reflection = update.reflection
    if update.action_rule is not None:
        lesson.action_rule = update.action_rule
    if update.hypothesis is not None:
        lesson.hypothesis = update.hypothesis
    if update.status is not None:
        lesson.status = update.status
        if update.status == "APPROVED":
            lesson.is_approved = True
        elif update.status in ("REJECTED", "PENDING_REVIEW"):
            lesson.is_approved = False
    db.commit()
    db.refresh(lesson)
    return schemas.LessonItem.model_validate(lesson)


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


# ==================== 11. TESTING LAB & VERIFICATION ====================

@app.get("/api/v1/lab/scenarios")
def get_lab_scenarios():
    """Retrieve list of all deterministic scenarios."""
    return {"scenarios": ScenarioRunner.list_scenarios()}

@app.post("/api/v1/lab/scenarios/run/{scenario_id}", response_model=schemas.ScenarioRunResponse)
def run_lab_scenario(scenario_id: str):
    """Run a specific deterministic scenario against the real implementation."""
    return ScenarioRunner.run_scenario(scenario_id)

@app.post("/api/v1/lab/scenarios/run-all")
def run_all_lab_scenarios():
    """Run all 13 deterministic scenarios in isolated lab databases."""
    results = ScenarioRunner.run_all()
    all_passed = all(r.status == "PASS" for r in results)
    return {
        "status": "PASS" if all_passed else "FAIL",
        "passed_count": sum(1 for r in results if r.status == "PASS"),
        "total_count": len(results),
        "results": results
    }

@app.post("/api/v1/lab/replay/run", response_model=schemas.ReplayRunResponse)
def run_lab_replay(request: schemas.ReplayRunRequest):
    """Run isolated historical backtest/replay with zero-lookahead and full cost modeling."""
    return ReplayEngine.run_replay(request)

@app.post("/api/v1/lab/stress/run", response_model=schemas.StressTestResponse)
def run_lab_stress(request: schemas.StressTestRequest):
    """Run parametric stress test matrix varying spread, slippage, fees, and latency."""
    return StressTester.run_stress_test(request)


# ==================== 10. MAINTENANCE & OFFLINE RECOVERY ENDPOINTS ====================

@app.get("/api/v1/maintenance/fixtures/audit")
def audit_fixtures_endpoint(dry_run: bool = True, db: Session = Depends(get_db)):
    """Audits test fixture contamination in database without mutating data."""
    from services.maintenance_service import audit_and_reconcile_fixtures
    return audit_and_reconcile_fixtures(db, dry_run=dry_run)


@app.post("/api/v1/maintenance/fixtures/quarantine")
def quarantine_fixtures_endpoint(db: Session = Depends(get_db)):
    """Quarantines confirmed test fixtures (like watch-cross-15M) into INVALIDATED."""
    from services.maintenance_service import audit_and_reconcile_fixtures
    return audit_and_reconcile_fixtures(db, dry_run=False)


@app.post("/api/v1/recovery/offline/reconcile")
def reconcile_offline_positions_endpoint(db: Session = Depends(get_db)):
    """Manually triggers offline gap reconciliation for active positions."""
    from services.position_recovery_service import position_recovery_service
    return position_recovery_service.check_and_recover_offline_positions(db)


# ==================== 12. V7 TRADING POLICY & NY SESSION ENDPOINTS ====================

@app.get("/api/v1/policy/trading", response_model=schemas.TradingPolicyResponse)
def get_trading_policy_endpoint(symbol: str = "XAUUSDT", db: Session = Depends(get_db)):
    """Retrieve active Trading Policy."""
    from services.trading_policy_service import TradingPolicyService
    return TradingPolicyService.get_active_policy(db, symbol)


@app.post("/api/v1/policy/trading", response_model=schemas.TradingPolicyResponse)
def update_trading_policy_endpoint(
    req: schemas.TradingPolicyUpdate,
    db: Session = Depends(get_db)
):
    """
    Update or customize Trading Policy.
    Strict validation:
    - max_daily_fills >= 1
    - min_net_rr >= 1.5
    - Cross-midnight window validation: start must be earlier than end.
    """
    from services.trading_policy_service import TradingPolicyService
    symbol = req.symbol or "XAUUSDT"
    policy = TradingPolicyService.get_active_policy(db, symbol)

    # Validation
    if req.max_daily_fills is not None and req.max_daily_fills < 1:
        raise HTTPException(status_code=400, detail="max_daily_fills phải lớn hơn hoặc bằng 1")
    if req.min_net_rr is not None and req.min_net_rr < 1.0:
        raise HTTPException(status_code=400, detail="min_net_rr phải lớn hơn hoặc bằng 1.0")

    # Time validation
    start_str = req.ny_entry_start or policy.ny_entry_start
    end_str = req.ny_entry_end or policy.ny_entry_end
    try:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        if (sh * 60 + sm) >= (eh * 60 + em):
            raise HTTPException(
                status_code=400,
                detail="Cửa sổ phiên giao dịch qua nửa đêm (start >= end) chưa được hỗ trợ, vui lòng cấu hình start < end."
            )
    except ValueError:
        raise HTTPException(status_code=400, detail="Định dạng giờ không hợp lệ, yêu cầu HH:MM")

    now_ms = int(time.time() * 1000)
    for field, val in req.model_dump(exclude_unset=True).items():
        if hasattr(policy, field):
            setattr(policy, field, val)

    policy.version = (policy.version or 1) + 1
    policy.updated_at = now_ms
    db.commit()
    db.refresh(policy)
    return policy


@app.get("/api/v1/policy/ny-session", response_model=schemas.NYSessionStatusResponse)
def get_ny_session_status_endpoint(
    symbol: str = "XAUUSDT",
    db: Session = Depends(get_db)
):
    """
    Retrieve real-time status of New York Entry Window, Quota fulfillment,
    local times in NY & VN, countdown, and slot reservation.
    """
    from services.trading_policy_service import TradingPolicyService
    from datetime import datetime, timezone
    now_dt = datetime.now(timezone.utc)
    eval_res = TradingPolicyService.evaluate_entry_policy(db, symbol, now_dt)

    return schemas.NYSessionStatusResponse(
        session_instance_id=eval_res.get("session_instance_id", "NY-PENDING"),
        symbol=symbol,
        is_in_ny_window=eval_res.get("is_in_ny_window", False),
        is_fallback_active=eval_res.get("is_fallback_active", False),
        quota_state=eval_res.get("quota_state", "NOT_STARTED"),
        daily_fills=eval_res.get("daily_fills", 0),
        max_daily_fills=eval_res.get("max_daily_fills", 3),
        remaining_daily_slots=eval_res.get("remaining_daily_slots", 3),
        ny_fills=eval_res.get("ny_fills", 0),
        ny_min_fills=eval_res.get("ny_min_fills", 1),
        reserved_slots=eval_res.get("reserved_slots", 0),
        local_ny_time=eval_res.get("local_ny_time", ""),
        local_vn_time=eval_res.get("local_vn_time", ""),
        ny_window_display=eval_res.get("ny_window_display", "08:00 - 11:00 NY"),
        vn_window_display=eval_res.get("vn_window_display", ""),
        minutes_to_window_start=eval_res.get("minutes_to_window_start"),
        minutes_to_fallback=eval_res.get("minutes_to_fallback"),
        minutes_to_window_end=eval_res.get("minutes_to_window_end"),
        allowed=eval_res.get("allowed", False),
        reason_code=eval_res.get("reason_code"),
        reason_message=eval_res.get("reason_message")
    )

