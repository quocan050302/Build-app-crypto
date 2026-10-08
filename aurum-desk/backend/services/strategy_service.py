import time
import json
import uuid
import asyncio
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud, smc_engine
from services.collector_service import collector_service
from services.event_bus import event_bus

class StrategyService:
    def __init__(self):
        self._running = False

    def get_auto_state(self, db: Session) -> bool:
        cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "auto_paper_trading").first()
        if cfg and cfg.value.lower() == "true":
            return True
        return False

    def set_auto_state(self, db: Session, enabled: bool) -> bool:
        now_ms = int(time.time() * 1000)
        cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "auto_paper_trading").first()
        val_str = "true" if enabled else "false"
        if cfg:
            cfg.value = val_str
            cfg.updated_at = now_ms
        else:
            cfg = models.SystemConfig(key="auto_paper_trading", value=val_str, updated_at=now_ms)
            db.add(cfg)
        db.commit()

        event_bus.publish_event(
            event_type="auto.changed",
            aggregate_id="system_config",
            payload={"auto_paper_enabled": enabled}
        )
        return enabled

    def evaluate_upcoming_setups(self, symbol: str = "XAUUSDT", timeframe: str = "15M"):
        """
        Evaluate market conditions and maintain watch_setups progression:
        WATCHING -> WAITING_PRICE -> WAITING_SWEEP -> WAITING_MSS -> WAITING_RETRACE -> READY -> ARMED.
        """
        db: Session = SessionLocal()
        try:
            candles = crud.get_candles(db, symbol, timeframe, limit=150, ascending=True)
            if len(candles) < 20:
                return

            now_ms = int(time.time() * 1000)
            ticker = collector_service.latest_ticker or {"bid": candles[-1].close, "ask": candles[-1].close, "last": candles[-1].close}
            is_blackout, blackout_reason, _ = crud.check_news_blackout(db, now_ms)
            day_audit = crud.get_or_create_today_audit(db)

            # Read system leverage & margin mode
            lev_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_leverage").first()
            leverage = int(lev_cfg.value) if lev_cfg else 5

            margin_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_margin_mode").first()
            margin_mode = margin_cfg.value if margin_cfg else "ISOLATED"

            analysis = smc_engine.evaluate_smc_setup(
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

            setup_stage = analysis.get("setup_stage", "WATCHING")
            sig = analysis.get("active_signal")
            current_p = analysis.get("current_price", candles[-1].close)
            atr = analysis.get("atr", 2.0)

            # Determine watch setup ID for current timeframe
            setup_id = f"watch-{symbol}-{timeframe}"
            watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == setup_id).first()

            direction = sig.get("direction", "LONG" if analysis.get("trend") == "BULLISH" else "SHORT") if sig else ("LONG" if analysis.get("trend") == "BULLISH" else "SHORT")
            provisional_entry = sig.get("planned_entry", current_p) if sig else current_p

            # Direction-aware provisional fallback levels (V5.1 Geometry Fix)
            if direction == "LONG":
                fallback_sl = round(current_p - 2 * atr, 2)
                fallback_tp = round(current_p + 4 * atr, 2)
            else:
                fallback_sl = round(current_p + 2 * atr, 2)
                fallback_tp = round(current_p - 4 * atr, 2)

            provisional_sl = sig.get("stop_loss", fallback_sl) if sig else fallback_sl
            provisional_tp = sig.get("targets", [{}])[0].get("price", fallback_tp) if sig and sig.get("targets") else fallback_tp

            # Validate price geometry strictly (LONG: sl < entry < tp, SHORT: tp < entry < sl)
            from domain_calculator import validate_price_geometry
            is_geom_valid, geom_err = validate_price_geometry(direction, provisional_entry, provisional_sl, provisional_tp)
            if not is_geom_valid:
                # If geometry fails, do not allow setup to be READY
                if setup_stage == "READY":
                    setup_stage = "INVALID_GEOMETRY"

            invalidation_price = provisional_sl
            inval_reason = "Giá phá vỡ mức Stop Loss hoặc vi phạm cấu trúc đối diện"

            # Determine setup instance ID to avoid locking future setups
            candle_ts = candles[-1].timestamp if candles else now_ms
            setup_instance_id = sig.get("signal_id") if sig else f"setup-{symbol}-{timeframe}-{candle_ts}"

            dist_usdt = round(abs(current_p - provisional_entry), 2)
            dist_atr = round(dist_usdt / atr, 2) if atr > 0 else 0.0

            if not watch_setup:
                watch_setup = models.WatchSetup(
                    id=setup_id,
                    setup_instance_id=setup_instance_id,
                    version=1,
                    strategy="SMC_V1",
                    direction=direction,
                    timeframe=timeframe,
                    state=setup_stage,
                    htf_bias=analysis.get("htf_bias", "UNKNOWN"),
                    h1_alignment=analysis.get("h1_alignment", "UNKNOWN"),
                    poi_zone=json.dumps({"zone": analysis.get("zone"), "eq": analysis.get("equilibrium")}),
                    trigger_mode="CONFIRMED_CLOSE",
                    provisional_entry=provisional_entry,
                    provisional_sl=provisional_sl,
                    provisional_tp=provisional_tp,
                    invalidation_price=invalidation_price,
                    invalidation_reason=inval_reason,
                    gross_rr=sig.get("gross_rr", 0.0) if sig else 0.0,
                    net_rr=sig.get("estimated_net_rr", 0.0) if sig else 0.0,
                    risk_usdt=sig.get("initial_risk_usdt", 0.0) if sig else 0.0,
                    quantity=sig.get("quantity", 0.0) if sig else 0.0,
                    leverage=leverage,
                    margin_mode=margin_mode,
                    estimated_liquidation=sig.get("estimated_liquidation") if sig else None,
                    conditions_met=json.dumps(analysis.get("conditions_met", [])),
                    conditions_remaining=json.dumps(analysis.get("missing_conditions", [])),
                    distance_to_entry_atr=dist_atr,
                    distance_to_entry_usdt=dist_usdt,
                    created_at=now_ms,
                    updated_at=now_ms,
                    expires_at=now_ms + (4 * 3600 * 1000)
                )
                db.add(watch_setup)
                db.commit()
            else:
                # Do NOT overwrite an active ARMED or PAPER_OPEN order's watch setup!
                if watch_setup.state in ("ARMED", "PAPER_OPEN"):
                    # Setup is active in order execution, update only distance/telemetry
                    watch_setup.distance_to_entry_atr = dist_atr
                    watch_setup.distance_to_entry_usdt = dist_usdt
                    watch_setup.updated_at = now_ms
                    db.commit()
                else:
                    # Business revision guard: only increment on significant structural change
                    direction_changed = (watch_setup.direction != direction)
                    state_changed = (watch_setup.state != setup_stage)
                    entry_diff = abs(watch_setup.provisional_entry - provisional_entry)
                    levels_changed = entry_diff > (atr * 0.1)

                    if direction_changed:
                        # New setup instance when direction changes
                        watch_setup.setup_instance_id = setup_instance_id
                        watch_setup.version += 1
                        watch_setup.direction = direction
                    elif state_changed or levels_changed:
                        watch_setup.version += 1

                    if setup_stage == "READY" and watch_setup.state != "READY":
                        watch_setup.setup_instance_id = setup_instance_id

                    watch_setup.state = setup_stage
                    watch_setup.htf_bias = analysis.get("htf_bias", "UNKNOWN")
                    watch_setup.h1_alignment = analysis.get("h1_alignment", "UNKNOWN")
                    watch_setup.provisional_entry = provisional_entry
                    watch_setup.provisional_sl = provisional_sl
                    watch_setup.provisional_tp = provisional_tp
                    watch_setup.invalidation_price = invalidation_price
                    watch_setup.invalidation_reason = inval_reason
                    watch_setup.gross_rr = sig.get("gross_rr", 0.0) if sig else 0.0
                    watch_setup.net_rr = sig.get("estimated_net_rr", 0.0) if sig else 0.0
                    watch_setup.risk_usdt = sig.get("initial_risk_usdt", 0.0) if sig else 0.0
                    watch_setup.quantity = sig.get("quantity", 0.0) if sig else 0.0
                    watch_setup.leverage = leverage
                    watch_setup.margin_mode = margin_mode
                    watch_setup.estimated_liquidation = sig.get("estimated_liquidation") if sig else None
                    watch_setup.conditions_met = json.dumps(analysis.get("conditions_met", []))
                    watch_setup.conditions_remaining = json.dumps(analysis.get("missing_conditions", []))
                    watch_setup.distance_to_entry_atr = dist_atr
                    watch_setup.distance_to_entry_usdt = dist_usdt
                    watch_setup.updated_at = now_ms
                    db.commit()

            # Proximity evaluation
            from services.proximity_service import proximity_service
            tg_cfg = db.query(models.TelegramConfig).first()
            proximity_service.evaluate_setup_proximity(db, watch_setup, ticker, atr, tg_cfg)

            # Broadcast setup updated with setup_instance_id
            active_inst_id = watch_setup.setup_instance_id or setup_instance_id
            event_bus.publish_event(
                event_type="setup.updated" if setup_stage != "READY" else "setup.ready",
                aggregate_id=active_inst_id,
                aggregate_version=watch_setup.version,
                payload={
                    "setup_id": watch_setup.id,
                    "setup_instance_id": active_inst_id,
                    "direction": watch_setup.direction,
                    "state": watch_setup.state,
                    "planned_entry": watch_setup.provisional_entry,
                    "stop_loss": watch_setup.provisional_sl,
                    "take_profit": watch_setup.provisional_tp,
                    "net_rr": watch_setup.net_rr,
                    "risk_usdt": watch_setup.risk_usdt,
                    "conditions_met": analysis.get("conditions_met", []),
                    "missing_conditions": analysis.get("missing_conditions", [])
                }
            )

            # Auto Arming if enabled and setup is READY
            if setup_stage == "READY" and self.get_auto_state(db):
                self._auto_arm_candidate(db, watch_setup, sig, now_ms)

        finally:
            db.close()

    def _auto_arm_candidate(
        self,
        db: Session,
        watch_setup: models.WatchSetup,
        sig: Dict[str, Any],
        now_ms: int
    ):
        """Auto arm READY setup if all execution guards pass."""
        # 1. Check if there is already an active position or armed order
        active_pos = crud.get_active_position(db)
        if active_pos:
            return

        armed_order = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
        if armed_order:
            return

        # 2. Check price geometry strictly
        from domain_calculator import validate_price_geometry
        is_geom_valid, _ = validate_price_geometry(
            watch_setup.direction,
            watch_setup.provisional_entry,
            watch_setup.provisional_sl,
            watch_setup.provisional_tp
        )
        if not is_geom_valid:
            return

        # 3. Check Day Audit limits
        audit = crud.get_or_create_today_audit(db)
        if audit.is_blocked or audit.fills_count >= 3 or audit.consecutive_losses >= 2:
            return
        if audit.cooldown_until and now_ms < audit.cooldown_until:
            return

        # 3. Create armed paper order
        order_id = f"order-{uuid.uuid4().hex[:8]}"
        new_order = models.PaperOrder(
            id=order_id,
            setup_id=watch_setup.id,
            signal_id=sig.get("signal_id", f"sig-{now_ms}"),
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

        # Publish order.armed in the same unit of work
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

    async def run_strategy_loop(self):
        """Periodic background evaluation loop independent of browser activity."""
        self._running = True
        while True:
            try:
                await asyncio.sleep(4.0)
                self.evaluate_upcoming_setups("XAUUSDT", "15M")
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                await asyncio.sleep(3.0)

strategy_service = StrategyService()
