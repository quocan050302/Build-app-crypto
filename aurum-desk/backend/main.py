import time
import json
import uuid
import asyncio
import math
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, UploadFile, File, Query, Body, WebSocket, WebSocketDisconnect
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
from services.entry_guard_service import serialize_entry_mutation
from services.eligibility_service import evaluate_setup_eligibility
from lab.scenario_runner import ScenarioRunner
from lab.replay_engine import ReplayEngine
from lab.stress_tester import StressTester
from lab.job_manager import ReplayJobManager, ReplayQueueFullException

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

        # V14.4.1+: Reconcile SMC scenarios with their highest immutable snapshot
        scenarios = db.query(models.SMCScenario).all()
        scen_count = 0
        for s in scenarios:
            max_snap = db.query(models.SMCScenarioSnapshot).filter(
                models.SMCScenarioSnapshot.scenario_id == s.scenario_id
            ).order_by(
                models.SMCScenarioSnapshot.revision.desc(),
                models.SMCScenarioSnapshot.created_at.desc()
            ).first()
            if max_snap and max_snap.revision > s.revision:
                s.revision = max_snap.revision
                s.state = max_snap.state
                s.current_snapshot_id = max_snap.snapshot_id
                s.instance_id = max_snap.instance_id
                s.payload = max_snap.payload
                s.updated_at = max_snap.created_at
                scen_count += 1
        if scen_count > 0:
            db.commit()
            print(f"Reconciled {scen_count} desynchronized SMC scenarios to latest snapshot.")
    finally:
        db.close()

_LIFESPAN_STARTUP_RESULT: Dict[str, Any] = {}
_LIFESPAN_SHUTDOWN_RESULT: Dict[str, Any] = {}


def get_last_startup_result() -> Dict[str, Any]:
    """Return the structured result of the most recent lifespan startup sequence."""
    return dict(_LIFESPAN_STARTUP_RESULT)


def get_lifespan_startup_result() -> Dict[str, Any]:
    """Alias for get_last_startup_result."""
    return dict(_LIFESPAN_STARTUP_RESULT)


def get_last_shutdown_result() -> Dict[str, Any]:
    """Return the structured result of the most recent lifespan shutdown sequence."""
    return dict(_LIFESPAN_SHUTDOWN_RESULT)


def get_lifespan_shutdown_result() -> Dict[str, Any]:
    """Alias for get_last_shutdown_result."""
    return dict(_LIFESPAN_SHUTDOWN_RESULT)


def _apply_lifespan_shutdown_status(new_status: str) -> None:
    """Apply shutdown status adhering to Rule 096 precedence: FAILED > TIMED_OUT > COMPLETED_WITH_LOSS > COMPLETED."""
    global _LIFESPAN_SHUTDOWN_RESULT
    precedence = {"FAILED": 4, "TIMED_OUT": 3, "COMPLETED_WITH_LOSS": 2, "COMPLETED": 1}
    curr_status = _LIFESPAN_SHUTDOWN_RESULT.get("status", "COMPLETED")
    if precedence.get(new_status, 0) > precedence.get(curr_status, 0):
        _LIFESPAN_SHUTDOWN_RESULT["status"] = new_status


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan:
    Starts and manages background workers cleanly,
    cancels and awaits clean shutdown when stopped.
    """
    # 0. Sync broken DB states before starting loops
    reconcile_stuck_orders()

    global _LIFESPAN_STARTUP_RESULT
    _LIFESPAN_STARTUP_RESULT = {
        "status": "STARTING",
        "telemetry_worker_ready": False,
        "errors": [],
    }

    # PHẦN 007, 008, 010, 084: Apply runtime contract upgrades (policy session upgrade to ALL_SESSIONS_WITH_NY_RESERVE)
    db_upgrade = SessionLocal()
    try:
        from services.trading_policy_service import apply_runtime_contract_upgrades
        upgrade_summary = apply_runtime_contract_upgrades(db_upgrade, deployment_scope="XAUUSDT")
        logger.info(f"Runtime contract upgrade completed: {upgrade_summary}")
    except Exception as e:
        logger.error(f"Fatal error during runtime contract upgrade at startup: {e}")
        raise
    finally:
        db_upgrade.close()

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

    from services.market_reconciliation_service import market_reconciliation_service
    from services.forward_repair_worker import forward_repair_worker
    from services.lesson_repair_worker import lesson_repair_worker
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    from services.cg01_scheduler_service import cg01_evaluation_scheduler
    from services.cg01_projection_worker import cg01_projection_worker
    from services.cg01_telemetry_service import (
        run_trace_persistence_worker,
        start_trace_persistence_worker,
        stop_trace_persistence_worker,
        wait_trace_persistence_worker_stopped,
        get_trace_worker_stop_error,
        flush_pending_traces,
        drain_trace_persistence,
    )

    try:
        worker_ready = await start_trace_persistence_worker(timeout_s=3.0)
        if worker_ready:
            _LIFESPAN_STARTUP_RESULT["status"] = "HEALTHY"
            _LIFESPAN_STARTUP_RESULT["telemetry_worker_ready"] = True
        else:
            logger.warning("Telemetry persistence worker failed ready handshake; operating degraded.")
            _LIFESPAN_STARTUP_RESULT["status"] = "STARTUP_DEGRADED"
            _LIFESPAN_STARTUP_RESULT["telemetry_worker_ready"] = False
    except Exception as we:
        logger.error(f"Error starting telemetry persistence worker: {we}")
        _LIFESPAN_STARTUP_RESULT["status"] = "FAILED"
        _LIFESPAN_STARTUP_RESULT["telemetry_worker_ready"] = False
        _LIFESPAN_STARTUP_RESULT["errors"].append(str(we))

    tasks = [
        asyncio.create_task(bitget_ws_service.run_ws_loop()),
        asyncio.create_task(execution_consumer.run_consumer_loop()),
        asyncio.create_task(candle_cache_service.run_persistence_worker()),
        asyncio.create_task(collector_service.run_collector_loop()),
        asyncio.create_task(strategy_service.run_strategy_loop()),
        asyncio.create_task(proximity_service.run_proximity_loop()),
        asyncio.create_task(execution_coordinator.run_coordinator_loop()),
        asyncio.create_task(exit_monitor.run_exit_monitor_loop()),
        asyncio.create_task(market_reconciliation_service.run_late_reconciliation_loop()),
        asyncio.create_task(process_notification_outbox()),
        asyncio.create_task(forward_repair_worker.run_repair_loop()),
        asyncio.create_task(lesson_repair_worker.run_repair_loop()),
        asyncio.create_task(scalp_watcher_orchestrator.run_watcher_loop()),
        asyncio.create_task(cg01_evaluation_scheduler.run()),
        asyncio.create_task(cg01_projection_worker.run()),
    ]
    yield
    # Graceful shutdown: orderly stop of worker loops before cancelling (V16.22 / V16.23 / V16.24 Rules 086-100)
    global _LIFESPAN_SHUTDOWN_RESULT
    _LIFESPAN_SHUTDOWN_RESULT = {
        "status": "COMPLETED",
        "scheduler_drained": True,
        "telemetry_drain": {},
        "websocket_drain": {},
        "worker_stopped": True,
        "errors": [],
    }

    bitget_ws_service.stop()
    forward_repair_worker.stop()
    lesson_repair_worker.stop()
    scalp_watcher_orchestrator.stop()
    cg01_evaluation_scheduler.stop()

    sched_ok = False
    try:
        sched_ok = await cg01_evaluation_scheduler.wait_evaluations_drained(timeout_s=3.0)
        if not sched_ok:
            logger.warning("CG01 evaluation scheduler did not drain all evaluations within timeout.")
            _LIFESPAN_SHUTDOWN_RESULT["scheduler_drained"] = False
            _apply_lifespan_shutdown_status("TIMED_OUT")
    except Exception as se:
        logger.error(f"Error waiting for scheduler evaluations: {se}")
        _LIFESPAN_SHUTDOWN_RESULT["errors"].append(str(se))
        _apply_lifespan_shutdown_status("FAILED")

    cg01_projection_worker.stop()

    drain_res = {}
    try:
        drain_res = await drain_trace_persistence(timeout_s=5.0)
        _LIFESPAN_SHUTDOWN_RESULT["telemetry_drain"] = drain_res
        d_status = drain_res.get("status", "")
        if d_status in ("TIMED_OUT", "FAILED", "COMPLETED_WITH_LOSS"):
            logger.warning(f"Telemetry drain incomplete or degraded on shutdown: status={d_status}, details={drain_res}")
            _apply_lifespan_shutdown_status(d_status)
    except Exception as te:
        logger.error(f"Error draining traces on shutdown: {te}")
        _LIFESPAN_SHUTDOWN_RESULT["errors"].append(str(te))
        _apply_lifespan_shutdown_status("FAILED")

    stop_trace_persistence_worker()
    worker_stopped = False
    try:
        worker_stopped = await wait_trace_persistence_worker_stopped(timeout_s=2.0)
        _LIFESPAN_SHUTDOWN_RESULT["worker_stopped"] = bool(worker_stopped)
        if not worker_stopped:
            logger.warning("Telemetry persistence worker did not stop cleanly within timeout.")
            _apply_lifespan_shutdown_status("TIMED_OUT")
        stop_err = get_trace_worker_stop_error()
        if stop_err:
            _LIFESPAN_SHUTDOWN_RESULT["errors"].append(f"Worker task error: {stop_err}")
            _apply_lifespan_shutdown_status("FAILED")
    except Exception as stop_e:
        logger.error(f"Error waiting for worker stop on shutdown: {stop_e}")
        _LIFESPAN_SHUTDOWN_RESULT["errors"].append(str(stop_e))
        _apply_lifespan_shutdown_status("FAILED")

    try:
        rep_res = await forward_repair_worker.shutdown(timeout_s=5.0)
        if rep_res and getattr(rep_res, "status", "") == "TIMED_OUT":
            logger.warning(f"Forward repair worker shutdown timed out: {rep_res}")
    except Exception as rep_err:
        logger.error(f"Error during forward repair worker shutdown: {rep_err}")
    try:
        l_rep_res = await lesson_repair_worker.shutdown(timeout_s=5.0)
        if l_rep_res and getattr(l_rep_res, "status", "") == "TIMED_OUT":
            logger.warning(f"Lesson repair worker shutdown timed out: {l_rep_res}")
    except Exception as l_rep_err:
        logger.error(f"Error during lesson repair worker shutdown: {l_rep_err}")
    try:
        recon_res = await market_reconciliation_service.shutdown(timeout_s=5.0)
        if recon_res and recon_res.status == "TIMED_OUT":
            logger.warning(f"Market reconciliation shutdown timed out: {recon_res}")
    except Exception as re_err:
        logger.error(f"Error during market reconciliation shutdown: {re_err}")
    try:
        res_ec = await execution_consumer.stop(timeout=2.0)
        if res_ec and res_ec.get("status") == "TIMED_OUT":
            logger.warning(f"Execution consumer stop timed out: {res_ec}")
    except Exception as ce:
        logger.error(f"Error stopping execution consumer: {ce}")
    try:
        res_cw = await candle_cache_service.stop_persistence_worker(timeout=2.0)
        if res_cw and res_cw.get("status") == "TIMED_OUT":
            logger.warning(f"Candle persistence worker stop timed out: {res_cw}")
    except Exception as cwe:
        logger.error(f"Error stopping candle persistence worker: {cwe}")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    try:
        event_bus.begin_dispatch_shutdown()
        ws_drain = await event_bus.drain_websocket_broadcasts(timeout_s=3.0)
        _LIFESPAN_SHUTDOWN_RESULT["websocket_drain"] = ws_drain
        if ws_drain.get("status") == "TIMED_OUT":
            logger.warning(f"WebSocket broadcast drain timed out on shutdown: {ws_drain}")
            _apply_lifespan_shutdown_status("TIMED_OUT")
    except Exception as dwe:
        logger.error(f"Error draining websocket broadcasts on shutdown: {dwe}")
        _LIFESPAN_SHUTDOWN_RESULT["errors"].append(str(dwe))
        _apply_lifespan_shutdown_status("FAILED")
    finally:
        event_bus.unbind_main_loop()

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
@app.get("/api/v1/health", response_model=schemas.SystemHealthResponse)
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
    start_ms: Optional[int] = Query(None, gt=0),
    end_ms: Optional[int] = Query(None, gt=0),
    db: Session = Depends(get_db)
):
    from services.candle_cache_service import candle_cache_service
    now_ms = int(time.time() * 1000)
    if (start_ms is None) != (end_ms is None):
        raise HTTPException(status_code=422, detail="START_AND_END_REQUIRED_TOGETHER")
    if start_ms is not None and end_ms is not None:
        # Historical selection window: bounded DB read, independent from the live cache.
        if end_ms < start_ms or end_ms > now_ms + 86_400_000:
            raise HTTPException(status_code=422, detail="INVALID_WINDOW")
        window_rows = crud.get_candles_in_range(db, symbol, timeframe, start_ms, end_ms, limit=500)
        window_models = [schemas.Candle.model_validate(c) for c in window_rows]
        window_last = window_models[-1].timestamp if window_models else None
        return schemas.CandleListResponse(
            symbol=symbol, timeframe=timeframe, candles=window_models, count=len(window_models),
            is_stale=False, last_candle_time=window_last, server_time=now_ms)
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
    from services.setup_quality_repository import setup_quality_repository
    from services.setup_display_plan_service import build_setup_display_plan
    from services.plan_financial_context_service import load_plan_financial_context, degraded_setup_plan

    fin_context = None
    try:
        fin_context = load_plan_financial_context(db)
    except Exception as exc:
        logger.warning(f"Could not load plan financial context: {exc}")

    quality_map = setup_quality_repository.get_snapshots_batch(db, [s.id for s in setups])

    # Batch load bound orders to wire frozen order financial snapshots
    from services.order_plan_snapshot_service import select_bound_order_for_setup, resolve_order_display_snapshot

    setup_ids = [s.id for s in setups]
    bound_orders = (
        db.query(models.PaperOrder)
        .filter(models.PaperOrder.setup_id.in_(setup_ids))
        .order_by(models.PaperOrder.created_at.desc())
        .all()
    ) if setup_ids else []

    orders_by_setup: Dict[str, List[Any]] = {}
    for o in bound_orders:
        orders_by_setup.setdefault(o.setup_id, []).append(o)

    setup_items = []
    for s in setups:
        try:
            cond_met = json.loads(s.conditions_met) if s.conditions_met else []
            cond_rem = json.loads(s.conditions_remaining) if s.conditions_remaining else []
            eligibility = evaluate_setup_eligibility(db, s)
            quality_sum = quality_map.get(s.id, {}).get("assessment", {}).get("summary")
            quality_snap_id = quality_map.get(s.id, {}).get("snapshot_id")

            candidates = orders_by_setup.get(s.id, [])
            bound_order = select_bound_order_for_setup(s, candidates)
            frozen_snap = None
            pricing_mode = "CURRENT_PREVIEW"
            is_frozen = str(s.state).upper() in ("ARMED", "PAPER_OPEN", "CLOSED") or (bound_order and bound_order.state in ("armed", "paper_open"))
            if bound_order and is_frozen:
                frozen_snap = resolve_order_display_snapshot(bound_order)
                pricing_mode = "FROZEN_ORDER"

            resolved_plan = build_setup_display_plan(
                s,
                quality_sum,
                financial_context=fin_context,
                pricing_mode=pricing_mode,
                frozen_snapshot=frozen_snap
            )
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
                "eligibility": eligibility,
                "quality_summary": quality_sum,
                "quality_snapshot_id": quality_snap_id,
                "resolved_display_plan": resolved_plan
            })
        except Exception as exc:
            s_dict = getattr(s, "__dict__", {}) if hasattr(s, "__dict__") else {}
            s_id = s_dict.get("id", "unknown")
            s_dir = s_dict.get("direction", "SHORT")
            logger.warning(f"Error building setup item {s_id}: {exc}")
            setup_items.append({
                "id": s_id,
                "version": s_dict.get("version", 1),
                "strategy": s_dict.get("strategy", "SMC_V1"),
                "direction": s_dir,
                "timeframe": s_dict.get("timeframe", "15M"),
                "state": s_dict.get("state", "WATCHING"),
                "htf_bias": s_dict.get("htf_bias", "UNKNOWN"),
                "h1_alignment": s_dict.get("h1_alignment", "UNKNOWN"),
                "trigger_mode": s_dict.get("trigger_mode", "CONFIRMED_CLOSE"),
                "provisional_entry": s_dict.get("provisional_entry", None),
                "provisional_sl": s_dict.get("provisional_sl", None),
                "provisional_tp": s_dict.get("provisional_tp", None),
                "confirmed_entry": s_dict.get("confirmed_entry", None),
                "confirmed_sl": s_dict.get("confirmed_sl", None),
                "confirmed_tp": s_dict.get("confirmed_tp", None),
                "invalidation_price": s_dict.get("invalidation_price", None),
                "invalidation_reason": s_dict.get("invalidation_reason", None),
                "gross_rr": s_dict.get("gross_rr", None),
                "net_rr": s_dict.get("net_rr", None),
                "risk_usdt": s_dict.get("risk_usdt", None),
                "quantity": s_dict.get("quantity", None),
                "leverage": s_dict.get("leverage", 5),
                "margin_mode": s_dict.get("margin_mode", "ISOLATED"),
                "estimated_liquidation": s_dict.get("estimated_liquidation", None),
                "conditions_met": [],
                "conditions_remaining": [],
                "distance_to_entry_atr": s_dict.get("distance_to_entry_atr", None),
                "distance_to_entry_usdt": s_dict.get("distance_to_entry_usdt", None),
                "setup_instance_id": s_dict.get("setup_instance_id", None),
                "near_entry_alerted_at": s_dict.get("near_entry_alerted_at", None),
                "near_entry_distance_price": s_dict.get("near_entry_distance_price", None),
                "near_entry_distance_atr": s_dict.get("near_entry_distance_atr", None),
                "entry_zone_low": s_dict.get("entry_zone_low", None),
                "entry_zone_high": s_dict.get("entry_zone_high", None),
                "created_at": s_dict.get("created_at", None),
                "updated_at": s_dict.get("updated_at", None),
                "eligibility": None,
                "quality_summary": None,
                "quality_snapshot_id": None,
                "resolved_display_plan": degraded_setup_plan(s_id, s_dir, "DEGRADED_PROCESSING"),
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
@serialize_entry_mutation
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
        if req_body.expected_direction:
            if req_body.expected_direction not in ("LONG", "SHORT"):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "INVALID_DIRECTION",
                        "message": f"Hướng lệnh không hợp lệ: '{req_body.expected_direction}'. Bắt buộc là LONG hoặc SHORT."
                    }
                )
            if watch_setup.direction != req_body.expected_direction:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SETUP_CHANGED",
                        "message": f"Xung đột hướng lệnh: Setup {req_body.expected_direction} bạn chọn đã thay đổi thành {watch_setup.direction}; hãy xem lại.",
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

    # 3. Authoritative Plan Resolver (V10.2 Plan Snapshot)
    from services.plan_resolver import resolve_plan_levels, PlanResolutionError
    try:
        resolved_plan = resolve_plan_levels(
            watch_setup,
            custom_entry=req_body.planned_entry if req_body else None,
            custom_sl=req_body.stop_loss if req_body else None,
            custom_tp=req_body.take_profit if req_body else None
        )
        entry = resolved_plan["entry"]
        sl = resolved_plan["stop_loss"]
        tp = resolved_plan["take_profit"]
    except PlanResolutionError as pe:
        raise HTTPException(
            status_code=400,
            detail={
                "code": pe.code,
                "message": pe.message,
                "direction": watch_setup.direction
            }
        )

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

    from services.order_plan_snapshot_service import build_arm_plan_snapshot
    arm_snap = build_arm_plan_snapshot(
        calc_res,
        order_type=order_type,
        direction=watch_setup.direction,
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        capital_usdt=equity,
        risk_pct=eff_risk_pct,
        leverage=calc_res.leverage,
        margin_mode=calc_res.margin_mode,
        now_ms=now_ms,
        policy_config_version=elig.get("policy_config_version", 1),
        risk_config_version=watch_setup.config_version or 1,
    )

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
        expires_at=now_ms + (2 * 3600 * 1000),
        origin="MANUAL_WEB",
        execution_mode="MANUAL",
        arm_decision_snapshot=json.dumps(elig.get("lesson_evaluations", [])),
        lessons_retrieved=json.dumps(elig.get("lessons_retrieved_snapshot", [])),
        cost_snapshot=json.dumps(arm_snap, ensure_ascii=False)
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

    now_ms = int(time.time() * 1000)
    watch_setup.state = "CANCELLED"
    watch_setup.updated_at = now_ms

    event_bus.publish_event(
        event_type="setup.cancelled",
        aggregate_id=setup_id,
        payload={
            "setup_id": setup_id,
            "direction": watch_setup.direction,
            "reason": "Người dùng chủ động hủy theo dõi setup"
        },
        db=db,
        occurred_at=now_ms
    )

    # If there was an armed order, cancel it too
    armed = db.query(models.PaperOrder).filter(models.PaperOrder.setup_id == setup_id, models.PaperOrder.state == "armed").first()
    if armed:
        armed.state = "cancelled"
        armed.closed_at = now_ms
        event_bus.publish_event(
            event_type="order.cancelled",
            aggregate_id=armed.id,
            payload={
                "order_id": armed.id,
                "setup_id": setup_id,
                "direction": armed.direction,
                "reason": "Hủy lệnh theo setup"
            },
            db=db,
            occurred_at=now_ms
        )

    db.commit()
    return {"status": "success", "message": f"Đã hủy theo dõi setup {setup_id}"}


@app.post("/api/v1/setups/cancel")
def cancel_setup_body(body: Dict[str, Any], db: Session = Depends(get_db)):
    setup_id = body.get("setup_id")
    if not setup_id:
        raise HTTPException(status_code=400, detail="Thiếu setup_id")
    return cancel_setup(setup_id=setup_id, db=db)


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
        # Use the canonical (validated, finite, normalised) bid/ask, never the raw ticker strings.
        created_order = PaperBroker.execute_market_order(
            db=db,
            order_create=order,
            current_bid=validation.bid,
            current_ask=validation.ask,
            validated_quote=validation,
            now_ms=now_ms,
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
    ALLOWED_RETRY_STATUSES = {"FAILED", "AMBIGUOUS", "RETRYING"}
    if item.status not in ALLOWED_RETRY_STATUSES:
        if item.status == "SENT":
            raise HTTPException(status_code=400, detail="Mục này đã gửi thành công tới Telegram, không gửi lại để tránh tin nhắn trùng")
        elif item.status == "SENDING":
            raise HTTPException(status_code=400, detail="Tin nhắn đang được gửi đi bởi hệ thống, không thể đặt lại trong lúc đang gửi")
        elif item.status == "PENDING":
            raise HTTPException(status_code=400, detail="Tin nhắn đã nằm trong hàng đợi chờ gửi")
        elif item.status == "SUPPRESSED":
            raise HTTPException(status_code=400, detail="Cơ hội đã hết hạn hoặc bị loại bỏ, không thể gửi lại")
        else:
            raise HTTPException(status_code=400, detail=f"Không thể thử lại tin nhắn ở trạng thái '{item.status}'")

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
@app.get("/api/reports")
def get_reports(
    research_date: Optional[str] = Query(None),
    date_basis: Optional[str] = Query("VN_DATE"),
    session: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    limit: int = Query(20),
    cursor: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    reports = crud.get_latest_reports(
        db=db,
        limit=limit,
        research_date=research_date,
        date_basis=date_basis,
        session=session,
        mode=mode,
        cursor=cursor
    )
    session_info = get_current_session_info()

    # Enrich report objects with parsed JSON fields
    enriched = []
    for r in reports:
        rep_dict = {
            "id": r.id,
            "report_type": r.report_type,
            "created_at": r.created_at,
            "session_name": r.session_name,
            "d_4h_bias": r.d_4h_bias,
            "h1_alignment": r.h1_alignment,
            "content_markdown": r.content_markdown,
            "strategy_version": r.strategy_version,
            "research_date": getattr(r, "research_date", None),
            "date_basis": getattr(r, "date_basis", "VN_DATE"),
            "mode": getattr(r, "mode", "CURRENT_ASOF"),
            "as_of_ms": getattr(r, "as_of_ms", r.created_at),
            "market_regime": getattr(r, "market_regime", None) or "CHƯA_XÁC_ĐỊNH",
            "data_coverage_status": getattr(r, "data_coverage_status", None) or "CHƯA_KIỂM_ĐỊNH",
            "quality_score": getattr(r, "quality_score", None),
            "scenarios": json.loads(r.scenarios) if r.scenarios else None,
            "structured_scenarios": json.loads(r.structured_scenarios) if getattr(r, "structured_scenarios", None) else (json.loads(r.scenarios) if r.scenarios else None),
            "timeframe_matrix": json.loads(r.timeframe_matrix) if getattr(r, "timeframe_matrix", None) else None,
            "provenance_metadata": json.loads(r.provenance_metadata) if getattr(r, "provenance_metadata", None) else None,
            "review_reference": json.loads(r.review_reference) if getattr(r, "review_reference", None) else None,
        }
        enriched.append(rep_dict)

    return {
        "session_info": session_info,
        "reports": enriched,
        "filter_context": {
            "research_date": research_date,
            "date_basis": date_basis,
            "session": session,
            "mode": mode
        }
    }


@app.get("/api/v1/reports/{report_id}")
def get_report_detail(report_id: int, db: Session = Depends(get_db)):
    report = crud.get_report_by_id(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo nghiên cứu này.")

    return {
        "id": report.id,
        "report_type": report.report_type,
        "created_at": report.created_at,
        "session_name": report.session_name,
        "d_4h_bias": report.d_4h_bias,
        "h1_alignment": report.h1_alignment,
        "content_markdown": report.content_markdown,
        "strategy_version": report.strategy_version,
        "research_date": getattr(report, "research_date", None),
        "date_basis": getattr(report, "date_basis", "VN_DATE"),
        "mode": getattr(report, "mode", "CURRENT_ASOF"),
        "as_of_ms": getattr(report, "as_of_ms", report.created_at),
        "market_regime": getattr(report, "market_regime", None) or "CHƯA_XÁC_ĐỊNH",
        "data_coverage_status": getattr(report, "data_coverage_status", None) or "CHƯA_KIỂM_ĐỊNH",
        "quality_score": getattr(report, "quality_score", None),
        "scenarios": json.loads(report.scenarios) if report.scenarios else None,
        "structured_scenarios": json.loads(report.structured_scenarios) if getattr(report, "structured_scenarios", None) else (json.loads(report.scenarios) if report.scenarios else None),
        "timeframe_matrix": json.loads(report.timeframe_matrix) if getattr(report, "timeframe_matrix", None) else None,
        "provenance_metadata": json.loads(report.provenance_metadata) if getattr(report, "provenance_metadata", None) else None,
        "review_reference": json.loads(report.review_reference) if getattr(report, "review_reference", None) else None,
    }


@app.get("/api/v1/reports/{report_id}/review")
def get_report_post_session_review(report_id: int, db: Session = Depends(get_db)):
    report = crud.get_report_by_id(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo để đánh giá kết quả.")

    from research_review import evaluate_scenario_outcome_and_mfe_mae
    scenarios = json.loads(report.structured_scenarios or report.scenarios or "{}")
    as_of = getattr(report, "as_of_ms", report.created_at)

    # Get candles after as_of up to +12 hours using strict range
    horizon_ms = 12 * 3600 * 1000
    subsequent_candles = crud.get_candles_in_range(
        db,
        symbol="XAUUSDT",
        timeframe="15M",
        start_ms=as_of + 1,
        end_ms=as_of + horizon_ms,
        limit=100
    )
    after_candles = [
        {"timestamp": c.timestamp, "open": c.open, "high": c.high, "low": c.low, "close": c.close}
        for c in subsequent_candles
        if hasattr(c, "timestamp")
    ]

    bullish_eval = evaluate_scenario_outcome_and_mfe_mae(scenarios.get("bullish", {}), after_candles)
    bearish_eval = evaluate_scenario_outcome_and_mfe_mae(scenarios.get("bearish", {}), after_candles)

    return {
        "report_id": report_id,
        "as_of_ms": as_of,
        "eval_candles_count": len(after_candles),
        "bullish_review": bullish_eval,
        "bearish_review": bearish_eval,
        "evaluated_at": int(time.time() * 1000)
    }


@app.post("/api/v1/reports/generate")
@app.post("/api/v1/reports/generate/")
@app.post("/api/v1/reports")
@app.post("/api/v1/reports/")
@app.post("/api/v1/reports/create")
@app.post("/api/v1/reports/new")
@app.post("/api/reports/generate")
@app.post("/api/reports")
@app.get("/api/v1/reports/generate")
def create_report(
    report_type: Optional[str] = Query(None),
    payload: Optional[schemas.ReportGeneratePayload] = Body(None),
    db: Session = Depends(get_db)
):
    effective_type = (payload.report_type if payload and payload.report_type else None) or report_type or "SESSION_REPORT"
    selected_date = payload.selected_date if payload else None
    time_of_day = payload.time_of_day if payload else None
    session = payload.session if payload else None
    date_basis = payload.date_basis if payload else None
    mode = payload.mode if payload else None
    as_of_ms = payload.as_of_ms if payload else None
    custom_candles = payload.custom_candles_15m if payload else None

    report = generate_research_report(
        db=db,
        report_type=effective_type,
        candles_15m=custom_candles,
        selected_date=selected_date,
        time_of_day=time_of_day,
        session=session,
        date_basis=date_basis,
        mode=mode,
        as_of_ms=as_of_ms
    )
    return {
        "status": "success",
        "report_id": report.id,
        "session_name": report.session_name,
        "content_markdown": report.content_markdown,
        "research_date": getattr(report, "research_date", None),
        "as_of_ms": getattr(report, "as_of_ms", report.created_at),
        "market_regime": getattr(report, "market_regime", "UNKNOWN"),
        "structured_scenarios": json.loads(report.structured_scenarios) if getattr(report, "structured_scenarios", None) else None,
        "timeframe_matrix": json.loads(report.timeframe_matrix) if getattr(report, "timeframe_matrix", None) else None,
        "data_coverage_status": getattr(report, "data_coverage_status", "PROVIDED_VALIDATED")
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


@app.post("/api/v1/lessons/validate-predicate", response_model=schemas.RuleValidationResponse)
def validate_lesson_predicate(req: schemas.RuleValidationRequest):
    from services.lesson_rule_service import LessonRuleService
    is_valid, msg, report = LessonRuleService.validate_predicate(
        predicate_input=req.predicate,
        severity=req.severity,
        effect=req.effect
    )
    status_str = "VALID" if is_valid else "INVALID"
    return schemas.RuleValidationResponse(
        is_valid=is_valid,
        status=status_str,
        message=msg,
        report=report
    )


@app.post("/api/v1/lessons/{lesson_id}/approve", response_model=schemas.LessonItem)
def approve_lesson(lesson_id: int, db: Session = Depends(get_db)):
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": f"Không tìm thấy bài học #{lesson_id}"})
    now_ms = int(time.time() * 1000)
    lesson.is_approved = True
    lesson.status = "APPROVED"
    lesson.reviewed_at = now_ms

    from services.lesson_rule_service import LessonRuleService
    is_valid, msg, report = LessonRuleService.validate_rule_behavior(lesson)
    # Does not auto-enable on approve (U01)
    if not is_valid:
        lesson.validation_status = report.get("status", "INVALID")
    else:
        lesson.validation_status = "VALID"
    lesson.validated_at = now_ms
    lesson.validation_report = json.dumps(report)

    trail = []
    if lesson.audit_trail:
        try:
            trail = json.loads(lesson.audit_trail)
        except Exception:
            trail = [lesson.audit_trail]
    trail.append({"action": "APPROVED", "timestamp": now_ms, "actor": "USER", "validation": lesson.validation_status})
    lesson.audit_trail = json.dumps(trail)

    db.commit()
    db.refresh(lesson)
    LessonRuleService.invalidate_cache()
    return schemas.LessonItem.model_validate(lesson)


@app.post("/api/v1/lessons/{lesson_id}/approve-and-activate", response_model=schemas.LessonItem)
def approve_and_activate_lesson(
    lesson_id: int,
    req: schemas.LessonApproveAndActivateRequest,
    db: Session = Depends(get_db),
):
    """
    V16.39.1 Section 086 - 095: Atomic Approve and Activate contract.
    Validates optimistic revision, predicate & scope, activates rule atomically,
    sets effective_at=now_ms, increments revision, records audit trail, and invalidates cache.
    """
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": f"Không tìm thấy bài học #{lesson_id}"})

    cur_rev = getattr(lesson, "revision", 1) or 1
    if req.expected_revision != cur_rev:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REVISION_MISMATCH",
                "message": f"Bài học đã được cập nhật bởi thao tác khác (phiên bản hiện tại: {cur_rev}, yêu cầu: {req.expected_revision}). Vui lòng tải lại trang.",
            },
        )

    now_ms = int(time.time() * 1000)

    # 1. Validate severity enum (Section 087)
    target_severity = lesson.severity
    if req.selected_severity:
        sev = str(req.selected_severity).upper()
        if sev in ("GREEN", "INFO"):
            target_severity = "INFO"
        elif sev in ("YELLOW", "WARNING", "WARN"):
            target_severity = "WARNING"
        elif sev in ("RED", "CRITICAL"):
            target_severity = "CRITICAL"
        else:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_SEVERITY", "message": f"Unsupported severity: {req.selected_severity}. Allowed: GREEN, YELLOW, RED, INFO, WARNING, CRITICAL."}
            )

    # 2. Validate effect enum (Section 088)
    target_effect = lesson.effect
    if req.selected_effect:
        eff = str(req.selected_effect).upper()
        allowed_effects = ("ANNOTATE", "WARN_ENTRY", "BLOCK_ENTRY", "ADD_SCORE", "DEDUCT_SCORE", "PROPOSE_PLAN_ADJUSTMENT")
        if eff not in allowed_effects:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_EFFECT", "message": f"Unsupported effect: {req.selected_effect}. Allowed: {allowed_effects}."}
            )
        target_effect = eff

    # 3. Validate scope object (Section 089)
    target_scope = lesson.scope
    if req.scope is not None:
        if isinstance(req.scope, dict):
            target_scope = json.dumps(req.scope)
        elif isinstance(req.scope, str):
            try:
                parsed_sc = json.loads(req.scope)
                if not isinstance(parsed_sc, dict):
                    raise ValueError()
                target_scope = json.dumps(parsed_sc)
            except Exception:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "INVALID_SCOPE", "message": "Scope must be a valid JSON object dictionary."}
                )
        else:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_SCOPE", "message": "Scope must be a dictionary object."}
            )

    # 4. Validate predicate and delta (Section 090)
    target_predicate = lesson.predicate
    if req.predicate is not None:
        if isinstance(req.predicate, dict):
            target_predicate = json.dumps(req.predicate)
        elif isinstance(req.predicate, str):
            try:
                parsed_pred = json.loads(req.predicate)
                if not isinstance(parsed_pred, dict):
                    raise ValueError()
                target_predicate = json.dumps(parsed_pred)
            except Exception:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "INVALID_PREDICATE", "message": "Predicate must be a valid JSON object dictionary."}
                )
        else:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_PREDICATE", "message": "Predicate must be a dictionary object."}
            )

    if req.score_delta is not None:
        if isinstance(req.score_delta, bool) or not isinstance(req.score_delta, (int, float)) or not math.isfinite(req.score_delta):
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_DELTA", "message": "score_delta must be a finite numeric value."}
            )
        try:
            pred_obj = json.loads(target_predicate) if target_predicate else {}
        except Exception:
            pred_obj = {}
        pred_obj["score_delta"] = float(req.score_delta)
        target_predicate = json.dumps(pred_obj)

    if target_effect in ("ADD_SCORE", "DEDUCT_SCORE"):
        try:
            p_check = json.loads(target_predicate) if target_predicate else {}
        except Exception:
            p_check = {}
        sd_check = p_check.get("score_delta")
        if sd_check is None or isinstance(sd_check, bool) or not math.isfinite(float(sd_check)):
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_SCORE_RULE", "message": "Score adjustment rule must have an explicit finite score_delta."}
            )
        if not p_check.get("metric") or not p_check.get("operator"):
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_SCORE_RULE", "message": "Score adjustment rule must configure a typed predicate with metric and operator."}
            )

    # Mutate ORM object only after all input validation succeeds (Section 091)
    lesson.severity = target_severity
    lesson.effect = target_effect
    lesson.scope = target_scope
    lesson.predicate = target_predicate

    from services.lesson_rule_service import LessonRuleService
    is_valid, msg, report = LessonRuleService.validate_rule_behavior(lesson)
    if not is_valid:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RULE_VALIDATION_FAILED",
                "message": msg or "Quy tắc không vượt qua kiểm tra cấu trúc.",
                "report": report,
            },
        )

    lesson.is_approved = True
    lesson.status = "APPROVED"
    lesson.enabled = bool(req.activate)
    lesson.validation_status = "VALID"
    lesson.validated_at = now_ms
    lesson.validation_report = json.dumps(report)
    lesson.reviewed_at = now_ms
    lesson.effective_at = now_ms
    lesson.revision = cur_rev + 1

    trail = []
    if lesson.audit_trail:
        try:
            trail = json.loads(lesson.audit_trail)
        except Exception:
            trail = [lesson.audit_trail]
    trail.append({
        "action": "APPROVED_AND_ACTIVATED" if req.activate else "APPROVED",
        "timestamp": now_ms,
        "actor": "USER",
        "note": req.review_note,
        "revision": lesson.revision,
    })
    lesson.audit_trail = json.dumps(trail)

    db.commit()
    db.refresh(lesson)
    LessonRuleService.invalidate_cache()
    LessonRuleService.invalidate_cache()
    return schemas.LessonItem.model_validate(lesson)


@app.post("/api/v1/lessons/{lesson_id}/reject", response_model=schemas.LessonItem)
def reject_lesson(lesson_id: int, db: Session = Depends(get_db)):
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": f"Không tìm thấy bài học #{lesson_id}"})
    now_ms = int(time.time() * 1000)
    lesson.is_approved = False
    lesson.status = "REJECTED"
    lesson.enabled = False
    lesson.reviewed_at = now_ms

    from services.lesson_rule_service import LessonRuleService
    trail = []
    if lesson.audit_trail:
        try:
            trail = json.loads(lesson.audit_trail)
        except Exception:
            trail = [lesson.audit_trail]
    trail.append({"action": "REJECTED", "timestamp": now_ms, "actor": "USER"})
    lesson.audit_trail = json.dumps(trail)

    db.commit()
    db.refresh(lesson)
    LessonRuleService.invalidate_cache()
    return schemas.LessonItem.model_validate(lesson)


@app.post("/api/v1/lessons/{lesson_id}/archive", response_model=schemas.LessonItem)
def archive_lesson(lesson_id: int, db: Session = Depends(get_db)):
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": f"Không tìm thấy bài học #{lesson_id}"})
    now_ms = int(time.time() * 1000)
    lesson.status = "ARCHIVED"
    lesson.is_approved = False
    lesson.enabled = False

    from services.lesson_rule_service import LessonRuleService
    trail = []
    if lesson.audit_trail:
        try:
            trail = json.loads(lesson.audit_trail)
        except Exception:
            trail = [lesson.audit_trail]
    trail.append({"action": "ARCHIVED", "timestamp": now_ms, "actor": "USER"})
    lesson.audit_trail = json.dumps(trail)

    db.commit()
    db.refresh(lesson)
    LessonRuleService.invalidate_cache()
    return schemas.LessonItem.model_validate(lesson)


@app.post("/api/v1/lessons/{lesson_id}/toggle-enable", response_model=schemas.LessonItem)
def toggle_lesson_enable(lesson_id: int, db: Session = Depends(get_db)):
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": f"Không tìm thấy bài học #{lesson_id}"})
    now_ms = int(time.time() * 1000)
    current_enabled = bool(lesson.enabled) if lesson.enabled is not None else True
    lesson.enabled = not current_enabled

    from services.lesson_rule_service import LessonRuleService
    trail = []
    if lesson.audit_trail:
        try:
            trail = json.loads(lesson.audit_trail)
        except Exception:
            trail = [lesson.audit_trail]
    trail.append({"action": "TOGGLE_ENABLED", "enabled": lesson.enabled, "timestamp": now_ms, "actor": "USER"})
    lesson.audit_trail = json.dumps(trail)

    db.commit()
    db.refresh(lesson)
    LessonRuleService.invalidate_cache()
    return schemas.LessonItem.model_validate(lesson)


@app.post("/api/v1/lessons/{lesson_id}/set-enable", response_model=schemas.LessonItem)
def set_lesson_enable(lesson_id: int, req: schemas.LessonEnableRequest, db: Session = Depends(get_db)):
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": f"Không tìm thấy bài học #{lesson_id}"})

    if req.expected_revision is not None and getattr(lesson, 'revision', None) is not None:
        if req.expected_revision != lesson.revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "STALE_EDIT",
                    "message": f"Dữ liệu bài học đã thay đổi (revision hiện tại {lesson.revision}, bạn gửi {req.expected_revision}).",
                    "current_revision": lesson.revision,
                    "expected_revision": req.expected_revision
                }
            )

    # Idempotent guard: if already in desired state, return directly (U03)
    if bool(lesson.enabled) == req.enabled:
        return schemas.LessonItem.model_validate(lesson)

    now_ms = int(time.time() * 1000)
    lesson.enabled = req.enabled
    if hasattr(lesson, 'revision') and lesson.revision is not None:
        lesson.revision += 1

    trail = []
    if lesson.audit_trail:
        try:
            trail = json.loads(lesson.audit_trail)
        except Exception:
            trail = [lesson.audit_trail]
    trail.append({"action": "SET_ENABLED", "enabled": lesson.enabled, "timestamp": now_ms, "actor": "USER"})
    lesson.audit_trail = json.dumps(trail)

    db.commit()
    db.refresh(lesson)
    from services.lesson_rule_service import LessonRuleService
    LessonRuleService.invalidate_cache()
    return schemas.LessonItem.model_validate(lesson)


@app.get("/api/v1/lessons/policy", response_model=schemas.LessonPolicyConfig)
def get_lesson_policy(db: Session = Depends(get_db)):
    from services.lesson_policy_service import LessonPolicyService
    cfg = LessonPolicyService.get_policy(db)
    return schemas.LessonPolicyConfig.model_validate(cfg)


@app.put("/api/v1/lessons/policy", response_model=schemas.LessonPolicyConfig)
def update_lesson_policy(update: schemas.LessonPolicyUpdate, db: Session = Depends(get_db)):
    from services.lesson_policy_service import LessonPolicyService
    try:
        updated = LessonPolicyService.update_policy(
            db,
            updates=update.model_dump(exclude_unset=True),
            expected_version=update.expected_version
        )
        return schemas.LessonPolicyConfig.model_validate(updated)
    except ValueError as e:
        raise HTTPException(status_code=409, detail={"code": "POLICY_CONFLICT", "message": str(e)})


@app.get("/api/v1/system/capabilities")
def get_system_capabilities():
    from services.lesson_policy_service import LessonPolicyService
    return LessonPolicyService.get_runtime_capabilities()


@app.get("/api/v1/lessons/repair/status")
def get_lesson_repair_status(db: Session = Depends(get_db)):
    from services.lesson_repair_worker import lesson_repair_worker
    return lesson_repair_worker.get_status(db=db)


@app.put("/api/v1/lessons/{lesson_id}", response_model=schemas.LessonItem)
def update_lesson(lesson_id: int, update: schemas.LessonUpdate, db: Session = Depends(get_db)):
    lesson = db.query(models.Lesson).filter(models.Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail={"code": "LESSON_NOT_FOUND", "message": f"Không tìm thấy bài học #{lesson_id}"})

    # Optimistic concurrency locking (L08)
    if update.revision is not None and getattr(lesson, 'revision', None) is not None:
        if update.revision != lesson.revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "STALE_EDIT",
                    "message": f"Dữ liệu bài học đã thay đổi (revision hiện tại {lesson.revision}, bạn gửi {update.revision}). Vui lòng tải lại trước khi lưu.",
                    "current_revision": lesson.revision,
                    "expected_revision": update.revision
                }
            )

    now_ms = int(time.time() * 1000)
    rule_changed = False

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
    if update.severity is not None and update.severity != lesson.severity:
        lesson.severity = update.severity
        rule_changed = True
    if update.effect is not None and update.effect != lesson.effect:
        lesson.effect = update.effect
        rule_changed = True
    if update.enabled is not None:
        lesson.enabled = update.enabled
    if update.predicate is not None and update.predicate != lesson.predicate:
        lesson.predicate = update.predicate
        rule_changed = True
    if update.scope is not None and update.scope != lesson.scope:
        lesson.scope = update.scope
        rule_changed = True
    if update.stage is not None and update.stage != lesson.stage:
        lesson.stage = update.stage
        rule_changed = True
    if update.status is not None:
        lesson.status = update.status
        if update.status == "APPROVED":
            lesson.is_approved = True
        elif update.status in ("REJECTED", "PENDING_REVIEW", "ARCHIVED"):
            lesson.is_approved = False

    from services.lesson_rule_service import LessonRuleService
    if rule_changed:
        lesson.version = (lesson.version or 1) + 1
        is_valid, msg, report = LessonRuleService.validate_predicate(
            predicate_input=lesson.predicate,
            severity=lesson.severity or "INFO",
            effect=lesson.effect or "ANNOTATE"
        )
        lesson.validation_status = "VALID" if is_valid else "INVALID"
        lesson.validated_at = now_ms
        lesson.validation_report = json.dumps(report)

    lesson.revision = (lesson.revision or 1) + 1

    trail = []
    if lesson.audit_trail:
        try:
            trail = json.loads(lesson.audit_trail)
        except Exception:
            trail = [lesson.audit_trail]
    trail.append({"action": "UPDATE", "timestamp": now_ms, "actor": "USER", "version": lesson.version, "revision": lesson.revision})
    lesson.audit_trail = json.dumps(trail)

    db.commit()
    db.refresh(lesson)
    LessonRuleService.invalidate_cache()
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


# ==================== V12.2 LAB ASYNC JOB ENDPOINTS ====================

@app.post("/api/v1/lab/jobs", response_model=schemas.JobCreateResponse, status_code=202)
def create_lab_job(request: schemas.ReplayRunRequest):
    """
    Submits a replay backtest job to the bounded background worker.
    Returns 202 Accepted with job_id and initial configuration hash.
    Maps queue-full capacity condition to HTTP 429.
    """
    job_mgr = ReplayJobManager.get_instance()
    try:
        return job_mgr.submit_job(request)
    except ReplayQueueFullException as e:
        raise HTTPException(status_code=429, detail=str(e))
    except RuntimeError as e:
        if "hàng đợi replay đã đầy" in str(e).lower():
            raise HTTPException(status_code=429, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/lab/jobs/{job_id}", response_model=schemas.JobStatusResponse)
def get_lab_job_status(job_id: str):
    """
    Polls the current status, progress, and effective config of a submitted replay job.
    """
    job_mgr = ReplayJobManager.get_instance()
    status = job_mgr.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tác vụ với job_id: {job_id}")
    return status


@app.post("/api/v1/lab/jobs/{job_id}/cancel", response_model=schemas.JobCancelResponse)
def cancel_lab_job(job_id: str):
    """
    Cancels an active or queued replay job.
    """
    job_mgr = ReplayJobManager.get_instance()
    res = job_mgr.cancel_job(job_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tác vụ với job_id: {job_id}")
    return res


@app.get("/api/v1/lab/jobs/{job_id}/result", response_model=schemas.ReplayRunResponse)
def get_lab_job_result(job_id: str):
    """
    Retrieves the final ReplayRunResponse for a completed job.
    """
    job_mgr = ReplayJobManager.get_instance()
    status = job_mgr.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tác vụ với job_id: {job_id}")
    if status.status != "SUCCEEDED":
        raise HTTPException(status_code=400, detail=f"Tác vụ chưa hoàn tất thành công (trạng thái hiện tại: {status.status})")
    res = job_mgr.get_job_result(job_id)
    if not res:
        raise HTTPException(status_code=404, detail="Kết quả tác vụ không khả dụng")
    return res


@app.get("/api/v1/lab/jobs/{job_id}/artifacts/{artifact_id}")
def download_lab_job_artifact(job_id: str, artifact_id: str):
    """
    Securely downloads an export artifact from a completed replay job with path traversal protection.
    """
    from fastapi.responses import FileResponse
    job_mgr = ReplayJobManager.get_instance()
    try:
        file_path = job_mgr.resolve_artifact_path(job_id, artifact_id)
        return FileResponse(path=file_path, filename=artifact_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (PermissionError, ValueError) as e:
        raise HTTPException(status_code=403, detail=str(e))


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
    PHẦN 010, 011, 098: Update or customize Trading Policy.
    Uses centralized TradingPolicyService.update_policy which delegates
    to validate_trading_policy_update. Bypassing validation via HTTP is strictly prevented.
    """
    from services.trading_policy_service import TradingPolicyService
    from services.clock import live_clock

    patch = req.model_dump(exclude_unset=True)
    try:
        updated = TradingPolicyService.update_policy(db, patch, clock=live_clock)
        return updated
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


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
    from services.clock import live_clock
    from datetime import datetime, timezone
    # V14.3: one injectable clock source (live_clock) instead of a direct wall-clock read.
    now_dt = datetime.fromtimestamp(live_clock.now_ms() / 1000.0, tz=timezone.utc)
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
        reason_message=eval_res.get("reason_message"),
        fallback_enabled=(bool(getattr(eval_res.get("policy"), "ny_fallback_enabled", False))
                          if eval_res.get("policy") is not None else None),
        fallback_start_ny=(getattr(eval_res.get("policy"), "ny_fallback_start", None)
                           if eval_res.get("policy") is not None else None),
        window_end_ny=(getattr(eval_res.get("policy"), "ny_entry_end", None)
                       if eval_res.get("policy") is not None else None),
        quota_reason=eval_res.get("quota_reason"),
        target_fills=eval_res.get("ny_target_fills"),
    )


@app.get("/api/v1/ny-quota/diagnostics", response_model=schemas.NYQuotaDiagnosticsResponse)
def get_ny_quota_diagnostics_endpoint(
    symbol: str = "XAUUSDT",
    date: Optional[str] = Query(None, description="NY session date YYYY-MM-DD (default: current NY session)"),
    db: Session = Depends(get_db)
):
    """
    V14.3 read model: why a NY session did / did not produce PAPER fills (scan coverage, unique candidates,
    funnel, typed blockers, one missed summary). GET only: never creates orders, quota rows or notifications.
    Sessions without an audit return availability=UNAVAILABLE and null counts (never fake zeros).
    """
    from datetime import datetime as _dt
    from services.trading_policy_service import TradingPolicyService
    from services.ny_opportunity_audit_service import NYOpportunityAuditService
    from services.ny_quota_helpers import classify_reason
    from services.clock import live_clock
    ny_tz = ZoneInfo("America/New_York")
    now_ms = live_clock.now_ms()
    policy = TradingPolicyService.get_policy(db) or TradingPolicyService.get_default_policy()
    today_ny = _dt.fromtimestamp(now_ms / 1000.0, tz=ny_tz).strftime("%Y-%m-%d")
    if date is None:
        date = today_ny
    try:
        day = _dt.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="date phải có dạng YYYY-MM-DD (ngày phiên New York)")
    # window of THAT NY date (pure computation, no DB write)
    probe_ms = int(_dt(day.year, day.month, day.day, 12, 0, tzinfo=ny_tz).timestamp() * 1000)
    window = TradingPolicyService.get_ny_session_window(policy, now_ms=probe_ms)
    is_today = date == today_ny
    window_view = {
        "ny_window": window["ny_window_str"], "vn_window": window["vn_window_str"],
        "window_start_ms": window["window_start_ms"], "window_end_ms": window["window_end_ms"],
        "fallback_start_ms": window["fallback_start_ms"],
        "fallback_enabled": bool(getattr(policy, "ny_fallback_enabled", False)),
        "phase": TradingPolicyService.get_ny_session_window(policy, now_ms=now_ms)["phase"] if is_today else None,
    }
    base = dict(session_instance_id=window["session_instance_id"], session_date_ny=window["session_date_ny"],
                session_date_vn=window["session_date_vn"], symbol=symbol, is_today=is_today,
                window=window_view, generated_at=now_ms)
    if date > today_ny:
        return schemas.NYQuotaDiagnosticsResponse(availability="NOT_STARTED", **base)
    diag = NYOpportunityAuditService.build_diagnostics(
        db, session_instance_id=window["session_instance_id"], session_date_ny=window["session_date_ny"])
    msg = classify_reason(diag["primary_missed_reason"])[1] if diag.get("primary_missed_reason") else None
    return schemas.NYQuotaDiagnosticsResponse(
        **base, availability=diag["availability"], quota=diag["quota"], scans=diag["scans"],
        unique_candidates=diag["unique_candidates"], financially_eligible=diag["financially_eligible"],
        armed=diag["armed"], filled=diag["filled"], top_blockers=diag["top_blockers"],
        primary_missed_reason=diag["primary_missed_reason"], primary_missed_message_vi=msg,
        missed_category=diag["missed_category"], last_system_error=diag["last_system_error"],
        summary_written=diag["summary_written"], coverage=diag["coverage"], freshness=diag["freshness"],
        candidates=diag["candidates"])



# ==================== V14 SETUP QUALITY ENDPOINTS ====================

@app.get("/api/v1/setups/{setup_id}/quality")
def get_setup_quality(
    setup_id: str,
    setup_instance_id: Optional[str] = None,
    expected_revision: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    PHẦN 60: Retrieve latest quality snapshot and detailed checklist for a setup.
    Read-only endpoint with revision consistency validation.
    """
    watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == setup_id).first()
    if not watch_setup:
        raise HTTPException(status_code=404, detail="SETUP_NOT_FOUND")

    if expected_revision is not None and watch_setup.version != expected_revision:
        raise HTTPException(
            status_code=409,
            detail=f"SETUP_REVISION_MISMATCH: expected {expected_revision}, current {watch_setup.version}"
        )

    from services.setup_quality_repository import setup_quality_repository
    snapshot = setup_quality_repository.get_latest_snapshot_for_setup(db, setup_id, setup_instance_id)

    if not snapshot:
        return {
            "setup_id": setup_id,
            "setup_instance_id": watch_setup.setup_instance_id,
            "setup_revision": watch_setup.version,
            "status": "NOT_ASSESSED",
            "assessment": {
                "status": "NOT_ASSESSED",
                "checks": [],
                "summary": {
                    "status": "NOT_ASSESSED",
                    "required_met": 0,
                    "required_total": 0,
                    "score_pct": 0.0,
                    "headline_vi": "Chưa có đánh giá chất lượng tại thời điểm này",
                    "next_condition_vi": "Đang chờ chu kỳ phân tích tiếp theo",
                    "caution_vi": None
                },
                "related_evidence_ids": [],
                "missing_evidence_ids": [],
                "cautions": [],
                "mode": "EXPLAIN_ONLY"
            },
            "evidence": []
        }

    return snapshot


@app.get("/api/v1/quality/snapshots/{snapshot_id}")
def get_quality_snapshot(snapshot_id: str, db: Session = Depends(get_db)):
    """
    PHẦN 61: Retrieve exact immutable quality snapshot by snapshot_id.
    """
    from services.setup_quality_repository import setup_quality_repository
    snapshot = setup_quality_repository.get_snapshot_by_id(db, snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="SNAPSHOT_NOT_FOUND")
    return snapshot


@app.post("/api/v1/quality/preview")
async def preview_setup_quality(
    req: schemas.DraftQualityPreviewRequest,
    db: Session = Depends(get_db)
):
    """
    PHẦN 62: Pure on-the-fly quality calculation for custom or edited draft levels.
    Does NOT write to database, does NOT mutate orders or historical snapshots.
    """
    from services.analysis_cache_service import analysis_cache_service
    from services.bitget_ws_service import bitget_ws_service
    from services.setup_quality_service import assess_setup_quality, build_plan_fingerprint
    from domain_calculator import calculate_risk_reward, CostAssumptions

    curr_quote = bitget_ws_service.latest_quote
    analysis = await analysis_cache_service.get_or_compute_analysis(
        symbol=req.symbol,
        timeframe=req.timeframe,
        latest_quote=curr_quote
    )

    now_ms = int(time.time() * 1000)
    costs = CostAssumptions()
    from services.risk_settings_service import risk_settings_service
    settings = risk_settings_service.get_settings(db)
    # Read the latest account balance without creating an audit row on preview.
    latest_audit = db.query(models.DayAudit).order_by(models.DayAudit.id.desc()).first()
    capital = latest_audit.current_equity if latest_audit else 1000.0
    effective_leverage = req.leverage if req.leverage is not None else settings.requested_leverage
    effective_margin = req.margin_mode if req.margin_mode is not None else settings.margin_mode
    effective_risk = req.risk_pct if req.risk_pct is not None else settings.risk_pct
    calc = calculate_risk_reward(
        direction=req.direction,
        entry=req.entry_price,
        sl=req.stop_loss,
        tp=req.take_profit,
        capital=capital,
        risk_pct=effective_risk,
        quantity_override=req.quantity,
        costs=costs,
        leverage=effective_leverage,
        margin_mode=effective_margin,
        min_net_rr=2.0
    )

    plan_dict = {
        "direction": req.direction,
        "entry_price": req.entry_price,
        "stop_loss": req.stop_loss,
        "take_profit": req.take_profit,
        "quantity": calc.quantity,
        "leverage": effective_leverage,
        "margin_mode": effective_margin,
        "net_rr": calc.net_rr,
        "risk_usdt": calc.net_risk_usdt,
    }

    q_context = {
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "direction": req.direction,
        "as_of_ms": now_ms,
        "analysis": analysis,
        "plan": plan_dict,
        "eligibility": {"eligible": True, "reasons": []},
        "quote_view": {"is_valid": True, "price": req.entry_price},
        "news_view": {"is_blackout": False},
        "calc": {"net_rr": calc.net_rr}
    }

    assessment, related_ev = assess_setup_quality(q_context)
    plan_fp = build_plan_fingerprint(plan_dict)

    return {
        "preview_type": "DRAFT_PREVIEW",
        "plan_fingerprint": plan_fp,
        "assessment": assessment.to_dict(),
        "evidence": [e.to_dict() for e in related_ev],
        "calc": {
            **calc.model_dump(),
            "gross_rr": calc.gross_rr,
            "net_rr": calc.net_rr,
            "net_risk_usdt": calc.net_risk_usdt,
            "net_reward_usdt": calc.net_reward_usdt,
            "can_execute": calc.can_execute,
            "meets_min_rr": calc.meets_min_rr,
            "is_valid": calc.is_valid,
        }
    }


# ==================== V14.4 SMC SCENARIO & EVIDENCE ENDPOINTS ====================

@app.get("/api/v1/smc/session-forecasts", response_model=schemas.SessionForecastsDTO)
def get_session_forecasts(symbol: str = "XAUUSDT", day: Optional[str] = None):
    """
    V14.4.1: cached Asia/Europe/NY conditional forecasts as-of the last market-reading cycle.
    Read-only: this GET never runs the scenario engine, creates an order or trains a model. A `day` that is not
    the cached NY session date returns NOT_CACHED (never a recomputation, never a future as-of).
    """
    import re
    from datetime import datetime as _dt
    if not re.fullmatch(r"[A-Z0-9]{3,20}", symbol or ""):
        raise HTTPException(status_code=422, detail="INVALID_SYMBOL")
    if day is not None:
        try:
            _dt.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=422, detail="INVALID_DAY")
    cached = strategy_service.session_forecasts_cache.get(symbol)
    if not cached:
        return schemas.SessionForecastsDTO(symbol=symbol, status="NOT_READY", day=day, forecasts={}, coverage={},
                                          data_issues=[])
    if day is not None and cached.get("day") != day:
        return schemas.SessionForecastsDTO(symbol=symbol, status="NOT_CACHED", day=day, as_of_ms=cached.get("as_of_ms"),
                                          forecasts={}, coverage={}, data_issues=[])
    return schemas.SessionForecastsDTO(
        symbol=symbol, status="ERROR" if cached.get("error") else "OK", day=cached.get("day"),
        as_of_ms=cached.get("as_of_ms"), forecasts=cached.get("forecasts") or {}, coverage=cached.get("coverage") or {},
        data_issues=cached.get("data_issues") or [])


@app.get("/api/v1/smc/forecast-observations/summary")
def get_forecast_observations_summary(
    symbol: str = "XAUUSDT",
    session: Optional[str] = None,
    from_ms: Optional[int] = None,
    to_ms: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    V14.4.2: Readonly summary of causal forward forecast outcomes. Does not trigger background jobs or DB writes.
    """
    import re
    if not re.fullmatch(r"[A-Z0-9]{3,20}", symbol or ""):
        raise HTTPException(status_code=422, detail="INVALID_SYMBOL")
    from services.smc_forecast_observation_service import summarize_forecast_observations
    return summarize_forecast_observations(
        db=db,
        session=session,
        symbol=symbol,
        from_ms=from_ms,
        to_ms=to_ms
    )



@app.get("/api/v1/smc/scenarios", response_model=List[schemas.ScenarioDTO])
def get_smc_scenarios(
    symbol: str = "XAUUSDT",
    limit: int = 5,
    db: Session = Depends(get_db)
):
    """
    PHẦN 059: Read-only endpoint returning current primary and alternative scenarios.
    """
    from services.smc_scenario_repository import get_active_scenarios
    import json
    rows = get_active_scenarios(db, symbol=symbol, limit=limit)
    results = []
    for r in rows:
        payload_dict = None
        if r.payload:
            try:
                payload_dict = json.loads(r.payload)
            except Exception:
                payload_dict = None
        results.append(schemas.ScenarioDTO(
            scenario_id=r.scenario_id,
            instance_id=r.instance_id,
            revision=r.revision,
            symbol=r.symbol,
            timeframe=r.timeframe,
            strategy_family=r.strategy_family,
            direction=r.direction,
            state=r.state,
            as_of_ms=r.as_of_ms,
            summary_vi=r.summary_vi,
            current_snapshot_id=r.current_snapshot_id,
            anchor_level_id=r.anchor_level_id,
            target_level_id=r.target_level_id,
            payload=payload_dict,
        ))
    return results


@app.get("/api/v1/smc/scenarios/{scenario_id}/snapshots/{snapshot_id}")
def get_scenario_snapshot(
    scenario_id: str,
    snapshot_id: str,
    db: Session = Depends(get_db)
):
    """
    PHẦN 060: Retrieve exact immutable scenario snapshot by snapshot_id and scenario_id.
    """
    from services.smc_scenario_repository import get_scenario_snapshot as repo_get_snapshot
    import json
    row = repo_get_snapshot(db, snapshot_id)
    if not row or row.scenario_id != scenario_id:
        raise HTTPException(status_code=404, detail="SCENARIO_SNAPSHOT_NOT_FOUND")
    try:
        return json.loads(row.payload)
    except Exception:
        raise HTTPException(status_code=500, detail="SNAPSHOT_PAYLOAD_CORRUPT")


@app.get("/api/v1/smc/scenarios/{scenario_id}/chart-evidence", response_model=schemas.ChartEvidenceDTO)
def get_scenario_chart_evidence(
    scenario_id: str,
    snapshot_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    V14.4.1: typed chart bundle of ONE scenario (exact snapshot when `snapshot_id` is given, otherwise the
    scenario's current snapshot). The raw snapshot endpoint above is not a chart payload.
    """
    from services.chart_evidence_service import build_selected_chart_bundle
    from services.smc_scenario_repository import get_scenario, get_scenario_snapshot as repo_get_snapshot, snapshot_from_row
    scenario = get_scenario(db, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="SCENARIO_NOT_FOUND")
    wanted = snapshot_id or scenario.current_snapshot_id
    row = repo_get_snapshot(db, wanted) if wanted else None
    if row is None:
        raise HTTPException(status_code=404, detail="SCENARIO_SNAPSHOT_NOT_FOUND")
    if row.scenario_id != scenario_id:
        raise HTTPException(status_code=409, detail="SCENARIO_SNAPSHOT_MISMATCH")
    try:
        snap = snapshot_from_row(row)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        logger.warning(f"Scenario snapshot parse error for {scenario_id} snapshot {wanted}: {e}")
        raise HTTPException(status_code=409, detail="LEGACY_SNAPSHOT_SCHEMA")
    try:
        bundle = build_selected_chart_bundle(snap, source_type="SCENARIO", binding_id=scenario_id)
        return schemas.ChartEvidenceDTO(**bundle)
    except Exception as exc:
        req_id = f"req-{int(time.time()*1000)}"
        logger.exception(f"[{req_id}] Unexpected error building scenario chart evidence for {scenario_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"INTERNAL_EVIDENCE_ERROR:{req_id}")


def _empty_chart_bundle(*, source: str, obj_id: str, instance_id, revision, symbol, direction, state, as_of_ms,
                        family, summary, plan, invalidation_price, warnings):
    return schemas.ChartEvidenceDTO(
        binding={"source": source, "id": obj_id, "instance_id": instance_id, "revision": revision, "symbol": symbol,
                 "direction": direction, "plan_fingerprint": None, "snapshot_id": None},
        as_of_ms=as_of_ms, evidence_cutoff_ms=as_of_ms, strategy_family=family or "STANDARD_SMC", state=state,
        summary_vi=summary, risk_reward_plan=plan, invalidation_price=invalidation_price, warnings=warnings,
        is_empty=True, chain={"verified": False, "reasons": warnings, "advisories": []})


@app.get("/api/v1/setups/{setup_id}/chart-evidence", response_model=schemas.ChartEvidenceDTO)
def get_setup_chart_evidence(
    setup_id: str,
    setup_instance_id: Optional[str] = None,
    expected_revision: Optional[int] = None,
    plan_fingerprint: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    V14.4.1: exact chart evidence of a selected setup. The requested identity (instance/revision/fingerprint)
    is compared with what the server loaded; any mismatch is a 409 - never a "latest" fallback, and the
    requested fingerprint is never echoed back as if the server had verified it.
    """
    watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == setup_id).first()
    if not watch_setup:
        raise HTTPException(status_code=404, detail="SETUP_NOT_FOUND")
    if expected_revision is not None and watch_setup.version != expected_revision:
        raise HTTPException(status_code=409,
                            detail=f"SETUP_REVISION_MISMATCH: expected {expected_revision}, current {watch_setup.version}")
    if setup_instance_id is not None and watch_setup.setup_instance_id != setup_instance_id:
        raise HTTPException(status_code=409, detail="SETUP_INSTANCE_MISMATCH")

    from services.chart_evidence_service import build_selected_chart_bundle, adapt_legacy_quality_snapshot
    from services.smc_scenario_repository import get_scenario_snapshot as repo_get_snapshot, snapshot_from_row
    from services.plan_resolver import resolve_plan_levels
    plan_resolved = resolve_plan_levels(watch_setup)

    if getattr(watch_setup, "scenario_snapshot_id", None):
        snap_row = repo_get_snapshot(db, watch_setup.scenario_snapshot_id)
        if snap_row is None:
            raise HTTPException(status_code=409, detail="SETUP_SNAPSHOT_MISSING")
        try:
            snap = snapshot_from_row(snap_row)
        except ValueError:
            raise HTTPException(status_code=409, detail="LEGACY_SNAPSHOT_SCHEMA")
        if (snap.instance_id != watch_setup.setup_instance_id or snap.direction != watch_setup.direction
                or snap.symbol != (getattr(watch_setup, "instrument", None) or "XAUUSDT")):
            raise HTTPException(status_code=409, detail="SETUP_SNAPSHOT_MISMATCH")
        if plan_fingerprint is not None and plan_fingerprint != snap.plan_fingerprint:
            raise HTTPException(status_code=409, detail="SETUP_PLAN_FINGERPRINT_MISMATCH")
        bundle = build_selected_chart_bundle(snap, source_type="SETUP", binding_id=setup_id)
        bundle["binding"]["revision"] = watch_setup.version
        return schemas.ChartEvidenceDTO(**bundle)

    # Legacy: exact quality snapshot ref (never "latest for setup"), uncertified label.
    qid = getattr(watch_setup, "quality_snapshot_id", None)
    if qid:
        from services.setup_quality_repository import setup_quality_repository
        legacy_snap = setup_quality_repository.get_snapshot_by_id(db, qid)
        if legacy_snap:
            return schemas.ChartEvidenceDTO(**adapt_legacy_quality_snapshot(legacy_snap, plan_data=plan_resolved))

    return _empty_chart_bundle(
        source="SETUP", obj_id=setup_id, instance_id=watch_setup.setup_instance_id, revision=watch_setup.version,
        symbol="XAUUSDT", direction=watch_setup.direction, state=watch_setup.state or "WATCHING",
        as_of_ms=watch_setup.updated_at or watch_setup.created_at, family=watch_setup.strategy_family,
        summary="Chưa có bằng chứng cấu trúc đã xác nhận cho setup này (chưa được đánh giá).",
        plan=plan_resolved if plan_resolved.get("is_valid") else None,
        invalidation_price=watch_setup.invalidation_price, warnings=["NO_EVIDENCE_FOR_SETUP"])


@app.get("/api/v1/orders/{order_id}/chart-evidence", response_model=schemas.ChartEvidenceDTO)
def get_order_chart_evidence(
    order_id: str,
    db: Session = Depends(get_db)
):
    """
    V14.4.1: ACTUAL order geometry + the evidence frozen at arm time (exact refs on the order row).
    The scenario plan never overrides the order's own entry/SL/TP, and "latest" structure is never used.
    """
    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="ORDER_NOT_FOUND")

    from services.chart_evidence_service import build_selected_chart_bundle, adapt_legacy_quality_snapshot
    from services.smc_scenario_repository import get_scenario_snapshot as repo_get_snapshot, snapshot_from_row

    stamp = order.opened_at or order.armed_at or order.created_at
    net_reward = None
    cost_dict = {}
    if order.cost_snapshot:
        try:
            cost_dict = json.loads(order.cost_snapshot) if isinstance(order.cost_snapshot, str) else dict(order.cost_snapshot)
            net_reward = cost_dict.get("net_reward_usdt")
        except Exception:
            cost_dict = {}

    order_plan = {
        "is_valid": True, "id": order.id, "direction": order.direction, "state": order.state,
        "planned_entry": order.planned_entry, "actual_entry": order.actual_entry, "actual_exit": order.actual_exit,
        "stop_loss": order.stop_loss, "take_profit": order.take_profit, "invalidation_price": order.stop_loss,
        "gross_rr": order.gross_rr, "net_rr": order.estimated_net_rr, "quantity": order.quantity,
        "net_risk_usdt": order.initial_risk_usdt,
        "net_reward_usdt": net_reward,
        "realized_pnl_net": order.realized_pnl_net, "realized_r": order.realized_r,
        "opened_at": order.opened_at, "closed_at": order.closed_at,
        "entry_time": (stamp // 1000) if stamp else None, "entry_time_ms": stamp,
        "exit_time_ms": order.closed_at,
        "fees": {
            "entry_fee_usdt": cost_dict.get("entry_fee_usdt"),
            "sl_exit_fee_usdt": cost_dict.get("sl_exit_fee_usdt"),
            "tp_exit_fee_usdt": cost_dict.get("tp_exit_fee_usdt"),
            "entry_slippage_usdt": cost_dict.get("entry_slippage_usdt"),
            "sl_exit_slippage_usdt": cost_dict.get("sl_exit_slippage_usdt"),
            "tp_exit_slippage_usdt": cost_dict.get("tp_exit_slippage_usdt"),
        } if cost_dict else None,
    }

    if getattr(order, "scenario_snapshot_id", None):
        snap_row = repo_get_snapshot(db, order.scenario_snapshot_id)
        if snap_row is None:
            raise HTTPException(status_code=409, detail="ORDER_SNAPSHOT_MISSING")
        try:
            snap = snapshot_from_row(snap_row)
        except ValueError:
            raise HTTPException(status_code=409, detail="LEGACY_SNAPSHOT_SCHEMA")
        if order.setup_instance_id and snap.instance_id != order.setup_instance_id:
            raise HTTPException(status_code=409, detail="ORDER_SNAPSHOT_MISMATCH")
        if snap.direction != order.direction:
            raise HTTPException(status_code=409, detail="ORDER_SNAPSHOT_DIRECTION_MISMATCH")
        
        from services.smc_scenario_config import active_scenario_config
        from services.chart_evidence_service import read_snapshot_provenance
        
        bundle = build_selected_chart_bundle(snap, source_type="ORDER", binding_id=order_id, order_data=order_plan)
        bundle["binding"]["order_state"] = order.state
        bundle["provenance"] = read_snapshot_provenance(snap.to_dict(), active_scenario_config)
        return schemas.ChartEvidenceDTO(**bundle)

    qid = getattr(order, "quality_snapshot_id", None)
    if qid:
        from services.setup_quality_repository import setup_quality_repository
        legacy_snap = setup_quality_repository.get_snapshot_by_id(db, qid)
        if legacy_snap:
            return schemas.ChartEvidenceDTO(**adapt_legacy_quality_snapshot(legacy_snap, order_data=order_plan))

    return _empty_chart_bundle(
        source="ORDER", obj_id=order_id, instance_id=order.setup_instance_id, revision=1,
        symbol=order.instrument or "XAUUSDT", direction=order.direction,
        state=order.state.upper() if order.state else "CLOSED",
        as_of_ms=order.closed_at or order.opened_at or order.armed_at or order.created_at,
        family=order.strategy_family,
        summary=f"Lệnh {order.direction} ({order.state}). Không có bằng chứng được lưu lúc đặt lệnh (lệnh cũ).",
        plan=order_plan, invalidation_price=order.stop_loss, warnings=["LEGACY_ORDER_NO_EVIDENCE"])


@app.get("/api/v1/orders/{order_id}/review")
def get_order_post_trade_review_endpoint(order_id: str, db: Session = Depends(get_db)):
    """
    V16.39 Section 078 - 080: Returns persisted post-trade review audit record for an order.
    """
    from services.post_trade_review_service import get_post_trade_review
    rev = get_post_trade_review(db, order_id)
    if not rev:
        raise HTTPException(
            status_code=404,
            detail={"code": "REVIEW_NOT_FOUND", "message": f"Chưa có đánh giá sau giao dịch cho lệnh #{order_id}"}
        )
    return {
        "status": "SUCCESS",
        "review": rev.to_dict(),
    }


# ==================== V15.0 SCALP WATCHER & OPPORTUNITY FUNNEL ====================

@app.get("/api/scalp/config")
def get_scalp_config_endpoint(db: Session = Depends(get_db)):
    from services.scalp_m1_types import load_effective_scalp_config
    from services.trading_policy_service import resolve_effective_daily_policy
    cfg = load_effective_scalp_config(db)
    eff_policy = resolve_effective_daily_policy(db)
    config_data = {
        "scalp_m1_enabled": (cfg.scalp_m1_mode != "OFF"),
        "scalp_m1_mode": cfg.scalp_m1_mode,
        "mode": cfg.scalp_m1_mode,
        "daily_ny_research_enabled": cfg.daily_ny_research_enabled,
        "min_closed_m1_bars": cfg.min_closed_m1_bars,
        "displacement_body_atr_min": cfg.displacement_body_atr_min,
        "min_net_rr": cfg.min_net_rr,
        "fallback_risk_pct": cfg.fallback_risk_pct,
        "near_zone_enter_atr": cfg.near_zone_enter_atr,
        "near_zone_exit_atr": cfg.near_zone_exit_atr,
        "max_trigger_age_ms": cfg.max_trigger_age_ms,
        "max_entry_distance_atr": cfg.max_entry_distance_atr,
        "max_active_zones": cfg.max_active_zones,
        "config_version": cfg.config_version,
        "v16_profile_enabled": cfg.v16_profile_enabled,
        "fvg_continuation_enabled": cfg.fvg_continuation_enabled,
        "sweep_reclaim_enabled": cfg.sweep_reclaim_enabled,
        "multi_zone_hypotheses_enabled": cfg.multi_zone_hypotheses_enabled,
        "orderflow_shadow_enabled": cfg.orderflow_shadow_enabled,
        "daily_min_goal": eff_policy.min_goal,
        "daily_max_cap": eff_policy.max_fills,
        "daily_ny_goal": eff_policy.ny_goal,
        "policy_version": eff_policy.policy_version,
        "cg01_enabled": getattr(cfg, "cg01_enabled", False),
        "cg01_profile_mode": getattr(cfg, "cg01_profile_mode", "OFF"),
        "execution_profile": getattr(cfg, "execution_profile", "BASELINE"),
        "adaptive_model_allowlist": list(getattr(cfg, "adaptive_model_allowlist", ())),
        "cg01_direction_policy": getattr(cfg, "cg01_direction_policy", "LEGACY_H1"),
        "timing_profile": getattr(cfg, "timing_profile", "SILVER_BULLET_LONDON_NY"),
        "effective_timing_profile": getattr(cfg, "timing_profile", "SILVER_BULLET_LONDON_NY"),
        "quality_scoring_enabled": getattr(cfg, "quality_scoring_enabled", False),
        "quality_arm_threshold": getattr(cfg, "quality_arm_threshold", 70.0),
        "quality_profile_version": getattr(cfg, "quality_profile_version", "v16.39"),
        "lesson_score_adjustment_enabled": getattr(cfg, "lesson_score_adjustment_enabled", False),
    }
    return {
        "status": "SUCCESS",
        "effective_config": config_data,
        **config_data,
    }


@app.post("/api/scalp/config")
def update_scalp_config_endpoint(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    from services.scalp_m1_types import ScalpMode, load_effective_scalp_config
    from services.trading_policy_service import resolve_effective_daily_policy, TradingPolicyService
    now_ms = int(time.time() * 1000)

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="INVALID_PAYLOAD: JSON object required")

    def _parse_strict_int(val: Any, field_name: str) -> int:
        if isinstance(val, bool):
            raise HTTPException(status_code=400, detail=f"INVALID_TYPE: {field_name} must be a strict integer, not a boolean")
        if not isinstance(val, int):
            raise HTTPException(status_code=400, detail=f"INVALID_TYPE: {field_name} must be an integer")
        return int(val)

    def _parse_strict_bool(val: Any, field_name: str) -> bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            v_low = val.strip().lower()
            if v_low == "true":
                return True
            if v_low == "false":
                return False
        raise HTTPException(
            status_code=400,
            detail=f"INVALID_BOOLEAN: {field_name} must be a strict boolean (true/false)"
        )

    # 1. Atomic validation of all daily goal parameters first (Rules 025, 026, 027)
    cur_policy = resolve_effective_daily_policy(db)
    has_daily_update = any(k in payload for k in ("daily_min_goal", "daily_max_cap", "daily_ny_goal"))
    min_g = cur_policy.min_goal
    max_c = cur_policy.max_fills
    ny_g = cur_policy.ny_goal

    if "daily_min_goal" in payload:
        min_g = _parse_strict_int(payload["daily_min_goal"], "daily_min_goal")
    if "daily_max_cap" in payload:
        max_c = _parse_strict_int(payload["daily_max_cap"], "daily_max_cap")
    if "daily_ny_goal" in payload:
        ny_g = _parse_strict_int(payload["daily_ny_goal"], "daily_ny_goal")

    if has_daily_update:
        if not (0 <= min_g <= max_c):
            raise HTTPException(
                status_code=400,
                detail=f"INVALID_DAILY_GOAL_CONFIG: daily_min_goal ({min_g}) must be between 0 and daily_max_cap ({max_c})"
            )
        if not (0 <= ny_g <= max_c):
            raise HTTPException(
                status_code=400,
                detail=f"INVALID_DAILY_NY_GOAL_CONFIG: daily_ny_goal ({ny_g}) must be between 0 and daily_max_cap ({max_c})"
            )

    mode = payload.get("scalp_m1_mode")
    if mode is None and "mode" in payload:
        mode = payload.get("mode")

    if mode is not None:
        if not isinstance(mode, str):
            raise HTTPException(status_code=400, detail="INVALID_SCALP_MODE: must be a string")
        mode_clean = mode.strip().upper()
        if mode_clean not in (ScalpMode.OFF.value, ScalpMode.SHADOW.value, ScalpMode.PAPER.value):
            raise HTTPException(status_code=400, detail="INVALID_SCALP_MODE: must be OFF, SHADOW, or PAPER")
        cfg_row = db.query(models.SystemConfig).filter(models.SystemConfig.key == "scalp_m1_mode").first()
        if cfg_row:
            cfg_row.value = mode_clean
            cfg_row.updated_at = now_ms
        else:
            db.add(models.SystemConfig(key="scalp_m1_mode", value=mode_clean, updated_at=now_ms))

    for bool_field in (
        "daily_ny_research_enabled",
        "v16_profile_enabled",
        "fvg_continuation_enabled",
        "sweep_reclaim_enabled",
        "multi_zone_hypotheses_enabled",
        "orderflow_shadow_enabled",
        "cg01_enabled",
    ):
        if bool_field in payload:
            b_val = payload[bool_field]
            if isinstance(b_val, bool):
                val_bool = b_val
            elif isinstance(b_val, str) and b_val.strip().lower() in ("true", "false"):
                val_bool = (b_val.strip().lower() == "true")
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"INVALID_BOOLEAN: {bool_field} must be a strict boolean (true/false)"
                )
            val_str = "true" if val_bool else "false"
            row = db.query(models.SystemConfig).filter(models.SystemConfig.key == bool_field).first()
            if row:
                row.value = val_str
                row.updated_at = now_ms
            else:
                db.add(models.SystemConfig(key=bool_field, value=val_str, updated_at=now_ms))

    policy_input = payload.get("cg01_direction_policy") or payload.get("direction_policy")
    if policy_input is not None:
        if not isinstance(policy_input, str):
            raise HTTPException(status_code=400, detail="INVALID_DIRECTION_POLICY: must be a string")
        pol_clean = policy_input.strip().upper()
        if pol_clean not in ("LEGACY_H1", "DAILY_BIAS_SHADOW", "DAILY_BIAS_PRIMARY"):
            raise HTTPException(status_code=400, detail="INVALID_DIRECTION_POLICY: must be LEGACY_H1, DAILY_BIAS_SHADOW, or DAILY_BIAS_PRIMARY")
        cfg_pol = db.query(models.SystemConfig).filter(models.SystemConfig.key == "cg01_direction_policy").first()
        if cfg_pol:
            cfg_pol.value = pol_clean
            cfg_pol.updated_at = now_ms
        else:
            db.add(models.SystemConfig(key="cg01_direction_policy", value=pol_clean, updated_at=now_ms))

    if "cg01_profile_mode" in payload:
        pm_val = str(payload["cg01_profile_mode"]).strip().upper()
        if pm_val not in ("OFF", "SHADOW", "PAPER_SHADOW", "PAPER_EXECUTION"):
            raise HTTPException(status_code=400, detail="INVALID_CG01_PROFILE_MODE: must be OFF, SHADOW, or PAPER_EXECUTION")
        cfg_pm = db.query(models.SystemConfig).filter(models.SystemConfig.key == "cg01_profile_mode").first()
        if cfg_pm:
            cfg_pm.value = pm_val
            cfg_pm.updated_at = now_ms
        else:
            db.add(models.SystemConfig(key="cg01_profile_mode", value=pm_val, updated_at=now_ms))

    if "execution_profile" in payload:
        from services.scalp_m1_types import ExecutionProfile
        ep_raw = payload["execution_profile"]
        if not isinstance(ep_raw, str):
            raise HTTPException(
                status_code=400,
                detail="INVALID_EXECUTION_PROFILE: Chế độ giao dịch không được hỗ trợ."
            )
        ep_val = ep_raw.strip().upper()
        if ep_val not in (ExecutionProfile.BASELINE.value, ExecutionProfile.CG01.value, ExecutionProfile.PAPER_ADAPTIVE_SMC.value):
            raise HTTPException(
                status_code=400,
                detail="INVALID_EXECUTION_PROFILE: Chế độ giao dịch không được hỗ trợ."
            )

        # Rule 027: Paper-only profile guard
        db_scope_row = db.query(models.SystemConfig).filter(models.SystemConfig.key == "execution_scope").first()
        effective_scope = db_scope_row.value.strip().upper() if db_scope_row and db_scope_row.value else "PAPER"
        req_scope = str(payload.get("execution_scope", payload.get("scope", effective_scope))).strip().upper()
        if ep_val == ExecutionProfile.PAPER_ADAPTIVE_SMC.value and (req_scope == "LIVE" or effective_scope == "LIVE"):
            raise HTTPException(
                status_code=400,
                detail="PAPER_ONLY_PROFILE_VIOLATION: PAPER_ADAPTIVE_SMC chỉ được phép chạy trong môi trường PAPER."
            )

        cfg_ep = db.query(models.SystemConfig).filter(models.SystemConfig.key == "execution_profile").first()
        if cfg_ep:
            cfg_ep.value = ep_val
            cfg_ep.updated_at = now_ms
        else:
            db.add(models.SystemConfig(key="execution_profile", value=ep_val, updated_at=now_ms))

    if "adaptive_model_allowlist" in payload:
        from services.scalp_m1_types import normalize_adaptive_model_allowlist
        raw_al = payload["adaptive_model_allowlist"]
        try:
            norm_allowlist = normalize_adaptive_model_allowlist(raw_al)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=f"INVALID_ADAPTIVE_ALLOWLIST: {ve}")

        import json
        al_json = json.dumps(list(norm_allowlist))
        cfg_al = db.query(models.SystemConfig).filter(models.SystemConfig.key == "adaptive_model_allowlist").first()
        if cfg_al:
            cfg_al.value = al_json
            cfg_al.updated_at = now_ms
        else:
            db.add(models.SystemConfig(key="adaptive_model_allowlist", value=al_json, updated_at=now_ms))

    if "min_net_rr" in payload:
        try:
            rr_val = float(payload["min_net_rr"])
            if rr_val <= 0 or not math.isfinite(rr_val):
                raise ValueError()
            cfg_rr = db.query(models.SystemConfig).filter(models.SystemConfig.key == "min_net_rr").first()
            if cfg_rr:
                cfg_rr.value = str(rr_val)
                cfg_rr.updated_at = now_ms
            else:
                db.add(models.SystemConfig(key="min_net_rr", value=str(rr_val), updated_at=now_ms))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="INVALID_VALUE: min_net_rr must be a positive number")

    if "timing_profile" in payload:
        tp_input = payload["timing_profile"]
        if not isinstance(tp_input, str):
            raise HTTPException(status_code=400, detail="INVALID_TIMING_PROFILE: must be a string")
        tp_val = tp_input.strip().upper()
        if tp_val not in ("SILVER_BULLET_LONDON_NY", "CG01_NY_PAPER_EXPANDED"):
            raise HTTPException(
                status_code=400,
                detail="INVALID_TIMING_PROFILE: must be SILVER_BULLET_LONDON_NY or CG01_NY_PAPER_EXPANDED"
            )
        cfg_tp = db.query(models.SystemConfig).filter(models.SystemConfig.key == "timing_profile").first()
        if cfg_tp:
            cfg_tp.value = tp_val
            cfg_tp.updated_at = now_ms
        else:
            db.add(models.SystemConfig(key="timing_profile", value=tp_val, updated_at=now_ms))

    if "max_trigger_age_ms" in payload:
        try:
            ttl_val = int(payload["max_trigger_age_ms"])
            if ttl_val <= 0:
                raise ValueError()
            cfg_ttl = db.query(models.SystemConfig).filter(models.SystemConfig.key == "max_trigger_age_ms").first()
            if cfg_ttl:
                cfg_ttl.value = str(ttl_val)
                cfg_ttl.updated_at = now_ms
            else:
                db.add(models.SystemConfig(key="max_trigger_age_ms", value=str(ttl_val), updated_at=now_ms))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="INVALID_VALUE: max_trigger_age_ms must be a positive integer")

    if "max_entry_distance_atr" in payload:
        try:
            dist_val = float(payload["max_entry_distance_atr"])
            if dist_val <= 0 or not math.isfinite(dist_val):
                raise ValueError()
            cfg_dist = db.query(models.SystemConfig).filter(models.SystemConfig.key == "max_entry_distance_atr").first()
            if cfg_dist:
                cfg_dist.value = str(dist_val)
                cfg_dist.updated_at = now_ms
            else:
                db.add(models.SystemConfig(key="max_entry_distance_atr", value=str(dist_val), updated_at=now_ms))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="INVALID_VALUE: max_entry_distance_atr must be a positive number")

    # Quality Scoring configuration (Sections 033 - 035, 061, 126, 127, 128)
    if "quality_scoring_enabled" in payload:
        q_en = _parse_strict_bool(payload["quality_scoring_enabled"], "quality_scoring_enabled")
        cfg_q_en = db.query(models.SystemConfig).filter(models.SystemConfig.key == "quality_scoring_enabled").first()
        if cfg_q_en:
            cfg_q_en.value = "1" if q_en else "0"
            cfg_q_en.updated_at = now_ms
        else:
            db.add(models.SystemConfig(key="quality_scoring_enabled", value="1" if q_en else "0", updated_at=now_ms))

    if "quality_arm_threshold" in payload:
        raw_th = payload["quality_arm_threshold"]
        if isinstance(raw_th, bool) or not isinstance(raw_th, (int, float)) or not math.isfinite(raw_th):
            raise HTTPException(status_code=400, detail="INVALID_VALUE: quality_arm_threshold must be a finite number")
        th_val = float(raw_th)
        if th_val < 0.0 or th_val > 100.0:
            raise HTTPException(status_code=400, detail="INVALID_VALUE: quality_arm_threshold must be between 0 and 100")
        cfg_th = db.query(models.SystemConfig).filter(models.SystemConfig.key == "quality_arm_threshold").first()
        if cfg_th:
            cfg_th.value = str(th_val)
            cfg_th.updated_at = now_ms
        else:
            db.add(models.SystemConfig(key="quality_arm_threshold", value=str(th_val), updated_at=now_ms))

    if "quality_profile_version" in payload:
        raw_ver = payload["quality_profile_version"]
        if not isinstance(raw_ver, str) or not raw_ver.strip():
            raise HTTPException(status_code=400, detail="CONFIG_INVALID: quality_profile_version must be a non-empty string")
        q_ver = raw_ver.strip()
        allowed_profiles = {"v16.39", "v16.39.1", "default", "conservative", "aggressive"}
        if q_ver not in allowed_profiles:
            raise HTTPException(status_code=400, detail=f"CONFIG_INVALID: Unknown quality_profile_version '{q_ver}'. Allowed: {sorted(list(allowed_profiles))}")
        cfg_q_ver = db.query(models.SystemConfig).filter(models.SystemConfig.key == "quality_profile_version").first()
        if cfg_q_ver:
            cfg_q_ver.value = q_ver
            cfg_q_ver.updated_at = now_ms
        else:
            db.add(models.SystemConfig(key="quality_profile_version", value=q_ver, updated_at=now_ms))

    if "lesson_score_adjustment_enabled" in payload:
        l_adj = _parse_strict_bool(payload["lesson_score_adjustment_enabled"], "lesson_score_adjustment_enabled")
        cfg_l_adj = db.query(models.SystemConfig).filter(models.SystemConfig.key == "lesson_score_adjustment_enabled").first()
        if cfg_l_adj:
            cfg_l_adj.value = "1" if l_adj else "0"
            cfg_l_adj.updated_at = now_ms
        else:
            db.add(models.SystemConfig(key="lesson_score_adjustment_enabled", value="1" if l_adj else "0", updated_at=now_ms))

    # Persist validated daily frequency goals to both TradingPolicy and SystemConfig
    if has_daily_update:
        pol = TradingPolicyService.get_or_create_policy(db)
        pol.max_daily_fills = max_c
        pol.ny_min_fills = ny_g
        pol.daily_min_goal = min_g
        pol.version = (pol.version or 1) + 1
        pol.updated_at = now_ms

        for d_key, d_val in (("daily_min_goal", min_g), ("daily_max_cap", max_c), ("daily_ny_goal", ny_g)):
            d_row = db.query(models.SystemConfig).filter(models.SystemConfig.key == d_key).first()
            if d_row:
                d_row.value = str(d_val)
                d_row.updated_at = now_ms
            else:
                db.add(models.SystemConfig(key=d_key, value=str(d_val), updated_at=now_ms))

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to persist scalp config: {e}")
        raise HTTPException(status_code=500, detail="DATABASE_ERROR: failed to persist config")

    effective_cfg = load_effective_scalp_config(db)
    synced_policy = resolve_effective_daily_policy(db)
    out_config = {
        "scalp_m1_enabled": (effective_cfg.scalp_m1_mode != "OFF"),
        "scalp_m1_mode": effective_cfg.scalp_m1_mode,
        "mode": effective_cfg.scalp_m1_mode,
        "daily_ny_research_enabled": effective_cfg.daily_ny_research_enabled,
        "min_closed_m1_bars": effective_cfg.min_closed_m1_bars,
        "displacement_body_atr_min": effective_cfg.displacement_body_atr_min,
        "min_net_rr": effective_cfg.min_net_rr,
        "fallback_risk_pct": effective_cfg.fallback_risk_pct,
        "max_trigger_age_ms": effective_cfg.max_trigger_age_ms,
        "max_entry_distance_atr": effective_cfg.max_entry_distance_atr,
        "config_version": effective_cfg.config_version,
        "v16_profile_enabled": effective_cfg.v16_profile_enabled,
        "fvg_continuation_enabled": effective_cfg.fvg_continuation_enabled,
        "sweep_reclaim_enabled": effective_cfg.sweep_reclaim_enabled,
        "multi_zone_hypotheses_enabled": effective_cfg.multi_zone_hypotheses_enabled,
        "orderflow_shadow_enabled": effective_cfg.orderflow_shadow_enabled,
        "daily_min_goal": synced_policy.min_goal,
        "daily_max_cap": synced_policy.max_fills,
        "daily_ny_goal": synced_policy.ny_goal,
        "policy_version": synced_policy.policy_version,
        "cg01_enabled": getattr(effective_cfg, "cg01_enabled", False),
        "cg01_profile_mode": getattr(effective_cfg, "cg01_profile_mode", "OFF"),
        "execution_profile": getattr(effective_cfg, "execution_profile", "BASELINE"),
        "adaptive_model_allowlist": list(getattr(effective_cfg, "adaptive_model_allowlist", ())),
        "cg01_direction_policy": getattr(effective_cfg, "cg01_direction_policy", "LEGACY_H1"),
        "timing_profile": getattr(effective_cfg, "timing_profile", "SILVER_BULLET_LONDON_NY"),
        "effective_timing_profile": getattr(effective_cfg, "timing_profile", "SILVER_BULLET_LONDON_NY"),
        "quality_scoring_enabled": getattr(effective_cfg, "quality_scoring_enabled", False),
        "quality_arm_threshold": getattr(effective_cfg, "quality_arm_threshold", 70.0),
        "quality_profile_version": getattr(effective_cfg, "quality_profile_version", "v16.39"),
        "lesson_score_adjustment_enabled": getattr(effective_cfg, "lesson_score_adjustment_enabled", False),
    }
    return {
        "status": "SUCCESS",
        "updated_at": now_ms,
        "effective_config": out_config,
        **out_config,
    }


@app.get("/api/scalp/daily-status")
@app.get("/api/v1/policy/daily-status")
def get_scalp_daily_status_endpoint(db: Session = Depends(get_db)):
    """
    V16.1 Rule 138, V16.2 Rules 021-028: Canonical daily status API.
    Returns trading_day_id, fills_count, min_goal, cap, ny_fills, reservation_state, search_state, primary_reason.
    """
    from services.trading_policy_service import TradingPolicyService, resolve_effective_daily_policy
    eff_policy = resolve_effective_daily_policy(db)
    now_ms = int(time.time() * 1000)
    day_audit = crud.get_or_create_today_audit(db)
    goal_eval = TradingPolicyService.evaluate_daily_frequency_goal(
        db, now_ms=now_ms, effective_policy=eff_policy
    )
    return {
        "status": "SUCCESS",
        "trading_day_id": day_audit.date_str,
        "fills_count": goal_eval.daily_fills,
        "min_goal": eff_policy.min_goal,
        "cap": eff_policy.max_fills,
        "ny_fills": goal_eval.ny_fills,
        "ny_goal": eff_policy.ny_goal,
        "policy_version": eff_policy.policy_version,
        "reservation_state": "RESERVED" if goal_eval.ny_reserve_active else "NONE",
        "search_state": goal_eval.status,
        "primary_reason": goal_eval.reason_vi,
        "shortfall_breakdown": goal_eval.shortfall_breakdown or {},
    }


@app.get("/api/scalp/watchlist")
def get_scalp_watchlist_endpoint(symbol: str = "XAUUSDT"):
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    zones = scalp_watcher_orchestrator.get_active_zones(symbol)
    return {
        "symbol": symbol,
        "count": len(zones),
        "zones": [z.to_dict() for z in zones],
    }


@app.get("/api/scalp/cg01/evaluation")
def get_scalp_cg01_evaluation_endpoint(symbol: str = "XAUUSDT"):
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    return scalp_watcher_orchestrator.get_latest_cg01_evaluation(symbol)


@app.get("/api/scalp/funnel")
def get_scalp_funnel_endpoint(
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    model_id: Optional[str] = None,
    session_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from services.opportunity_funnel_service import OpportunityFunnelService
    summary = OpportunityFunnelService.summarize_opportunity_funnel(
        db, start_ms=start_ms, end_ms=end_ms, model_id=model_id, session_id=session_id
    )
    return summary


@app.get("/api/scalp/comparison")
def get_scalp_comparison_endpoint(
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    db: Session = Depends(get_db),
):
    from services.model_comparison_service import ModelComparisonService
    report = ModelComparisonService.generate_comparison_report(
        db, start_ms=start_ms, end_ms=end_ms
    )
    return report


@app.get("/api/scalp/liquidity-map")
def get_scalp_liquidity_map_endpoint(symbol: str = "XAUUSDT"):
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    levels = scalp_watcher_orchestrator.get_active_liquidity_levels(symbol)
    return {
        "symbol": symbol,
        "count": len(levels),
        "levels": [
            {
                "id": l.id,
                "price": l.price,
                "side": l.side,
                "source_kind": l.source_kind,
                "state": l.state,
                "label_vi": l.label_vi,
                "is_partial": getattr(l, "is_partial", False),
                "known_at_ms": l.known_at_ms,
            }
            for l in levels
        ],
    }


@app.get("/api/scalp/hypotheses")
def get_scalp_hypotheses_endpoint(symbol: str = "XAUUSDT"):
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    hyps = scalp_watcher_orchestrator.get_active_hypotheses(symbol)
    batch_res = scalp_watcher_orchestrator.get_latest_hypothesis_batch_result(symbol)
    authority = scalp_watcher_orchestrator.get_latest_hypothesis_authority(symbol)
    outcome = scalp_watcher_orchestrator.get_latest_cycle_outcome(symbol)
    data_quality = "HEALTHY"
    issues_count = 0
    if batch_res is not None:
        data_quality = "DEGRADED" if batch_res.read_status != "COMPLETE" else "HEALTHY"
        issues_count = len(batch_res.issues)
    if authority is not None:
        if authority.has_write_failures:
            data_quality = "DEGRADED"
        elif authority.has_write_blocks and data_quality != "DEGRADED":
            data_quality = "PARTIAL"
        issues_count += authority.write_issues_count
    if outcome is not None and outcome.get("status") == "DEGRADED":
        data_quality = "DEGRADED"
        issues_count = max(issues_count, 1)
    return {
        "symbol": symbol,
        "count": len(hyps),
        "data_quality": data_quality,
        "issues_count": issues_count,
        "hypotheses": [h.to_dict() if hasattr(h, "to_dict") else h for h in hyps],
    }


@app.get("/api/scalp/zone-assessment/{zone_id}")
def get_scalp_zone_assessment_endpoint(zone_id: str, symbol: str = "XAUUSDT"):
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    assessment = scalp_watcher_orchestrator.get_zone_assessment_explanation(symbol, zone_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="ZONE_NOT_FOUND: zone not active or does not exist")
    return assessment


@app.get("/api/v1/paper/runtime-snapshot")
def get_paper_runtime_snapshot_endpoint(
    run_id: Optional[str] = None,
    symbol: str = "XAUUSDT",
    scope: str = "CG01",
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    V16.39.10: Canonical forward paper runtime observation endpoint (Rules 101-105).
    Strictly read-only, zero side-effects, does not create runs, policy, or checkpoints.
    """
    if start_ms is not None and end_ms is not None and end_ms <= start_ms:
        raise HTTPException(
            status_code=422,
            detail=f"end_ms ({end_ms}) must be strictly greater than start_ms ({start_ms})",
        )

    db.autoflush = False
    from services.cg01_scheduler_service import cg01_evaluation_scheduler
    from services.paper_runtime_snapshot_service import build_paper_runtime_snapshot

    providers = {"scheduler": cg01_evaluation_scheduler}
    snapshot = build_paper_runtime_snapshot(
        db,
        run_id=run_id,
        symbol=symbol,
        scope=scope,
        start_ms=start_ms,
        end_ms=end_ms,
        runtime_providers=providers,
        source_mode="HTTP",
    )
    return snapshot
