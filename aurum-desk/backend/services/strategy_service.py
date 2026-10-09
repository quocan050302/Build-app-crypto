import time
import json
import uuid
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud, smc_engine
from services.collector_service import collector_service
from services.event_bus import event_bus
from services.trading_policy_service import TradingPolicyService
from services.ny_fallback_service import NYFallbackService

logger = logging.getLogger(__name__)

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
            now_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
            ticker = collector_service.latest_ticker or {"bid": candles[-1].close, "ask": candles[-1].close, "last": candles[-1].close}
            is_blackout, blackout_reason, _ = crud.check_news_blackout(db, now_ms)
            day_audit = crud.get_or_create_today_audit(db)

            from services.risk_settings_service import risk_settings_service
            global_settings = risk_settings_service.get_settings(db)
            leverage = global_settings.requested_leverage
            margin_mode = global_settings.margin_mode
            risk_pct = global_settings.risk_pct
            config_version = global_settings.config_version
            capital = day_audit.current_equity if day_audit else 1000.0

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
                margin_mode=margin_mode,
                risk_pct=risk_pct
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

            # Real liquidity target levels
            sh = analysis.get("swing_high", current_p + 10.0)
            sl_val = analysis.get("swing_low", current_p - 10.0)
            fallback_sl = round(current_p - max(atr * 2.0, 5.0), 2) if direction == "LONG" else round(current_p + max(atr * 2.0, 5.0), 2)
            fallback_tp = round(sh, 2) if direction == "LONG" else round(sl_val, 2)

            provisional_sl = sig.get("stop_loss", fallback_sl) if sig else fallback_sl
            provisional_tp = sig.get("targets", [{}])[0].get("price", fallback_tp) if sig and sig.get("targets") else fallback_tp

            # Calculate authoritative risk-reward for provisional setup using real equity
            from domain_calculator import calculate_risk_reward, validate_price_geometry
            calc_prov = calculate_risk_reward(
                direction=direction,
                planned_entry=provisional_entry,
                stop_loss=provisional_sl,
                take_profit=provisional_tp,
                capital_usdt=capital,
                risk_pct=risk_pct,
                leverage=leverage,
                margin_mode=margin_mode
            )

            # Do NOT artificially inflate target by 2.8x. If Net RR is insufficient, report NET_RR_TOO_LOW
            if not calc_prov.meets_min_rr:
                if setup_stage == "READY":
                    setup_stage = "WATCHING"
                    analysis["reason_code"] = "NET_RR_TOO_LOW"

            # Validate price geometry strictly (LONG: sl < entry < tp, SHORT: tp < entry < sl)
            is_geom_valid, geom_err = validate_price_geometry(direction, provisional_entry, provisional_sl, provisional_tp)
            if not is_geom_valid:
                if setup_stage == "READY":
                    setup_stage = "INVALID_GEOMETRY"

            invalidation_price = provisional_sl
            inval_reason = "Giá phá vỡ mức Stop Loss hoặc vi phạm cấu trúc đối diện"

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
                    strategy_family="STANDARD_SMC",
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
                    gross_rr=sig.get("gross_rr", calc_prov.gross_rr) if sig else calc_prov.gross_rr,
                    net_rr=sig.get("estimated_net_rr", calc_prov.net_rr) if sig else calc_prov.net_rr,
                    risk_usdt=sig.get("initial_risk_usdt", calc_prov.net_risk_usdt) if sig else calc_prov.net_risk_usdt,
                    quantity=sig.get("quantity", calc_prov.quantity) if sig else calc_prov.quantity,
                    leverage=leverage,
                    margin_mode=margin_mode,
                    risk_pct=risk_pct,
                    requested_risk_pct=risk_pct,
                    effective_risk_pct=risk_pct,
                    risk_profile="STANDARD",
                    config_version=config_version,
                    estimated_liquidation=sig.get("estimated_liquidation", calc_prov.estimated_liquidation) if sig else calc_prov.estimated_liquidation,
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
                if watch_setup.state in ("ARMED", "PAPER_OPEN"):
                    watch_setup.distance_to_entry_atr = dist_atr
                    watch_setup.distance_to_entry_usdt = dist_usdt
                    watch_setup.updated_at = now_ms
                    # No setup.updated event needed while armed/open
                else:
                    direction_changed = (watch_setup.direction != direction)
                    state_changed = (watch_setup.state != setup_stage)
                    entry_diff = abs(watch_setup.provisional_entry - provisional_entry)
                    levels_changed = entry_diff > (atr * 0.1)
                    has_material_change = direction_changed or state_changed or levels_changed

                    if has_material_change:
                        if direction_changed:
                            watch_setup.setup_instance_id = setup_instance_id
                            watch_setup.version += 1
                            watch_setup.direction = direction
                        elif state_changed or levels_changed:
                            watch_setup.version += 1

                        if setup_stage == "READY" and watch_setup.state != "READY":
                            watch_setup.setup_instance_id = setup_instance_id

                        watch_setup.state = setup_stage
                        watch_setup.strategy_family = "STANDARD_SMC"
                        watch_setup.htf_bias = analysis.get("htf_bias", "UNKNOWN")
                        watch_setup.h1_alignment = analysis.get("h1_alignment", "UNKNOWN")
                        watch_setup.provisional_entry = provisional_entry
                        watch_setup.provisional_sl = provisional_sl
                        watch_setup.provisional_tp = provisional_tp
                        watch_setup.invalidation_price = invalidation_price
                        watch_setup.invalidation_reason = inval_reason
                        watch_setup.gross_rr = sig.get("gross_rr", calc_prov.gross_rr) if sig else calc_prov.gross_rr
                        watch_setup.net_rr = sig.get("estimated_net_rr", calc_prov.net_rr) if sig else calc_prov.net_rr
                        watch_setup.risk_usdt = sig.get("initial_risk_usdt", calc_prov.net_risk_usdt) if sig else calc_prov.net_risk_usdt
                        watch_setup.quantity = sig.get("quantity", calc_prov.quantity) if sig else calc_prov.quantity
                        watch_setup.leverage = leverage
                        watch_setup.margin_mode = margin_mode
                        watch_setup.risk_pct = risk_pct
                        watch_setup.config_version = config_version
                        watch_setup.estimated_liquidation = sig.get("estimated_liquidation", calc_prov.estimated_liquidation) if sig else calc_prov.estimated_liquidation
                        watch_setup.conditions_met = json.dumps(analysis.get("conditions_met", []))
                        watch_setup.conditions_remaining = json.dumps(analysis.get("missing_conditions", []))
                        watch_setup.distance_to_entry_atr = dist_atr
                        watch_setup.distance_to_entry_usdt = dist_usdt
                        watch_setup.updated_at = now_ms
                        db.commit()

                        # Broadcast only on material change
                        active_inst_id = watch_setup.setup_instance_id or setup_instance_id
                        event_bus.publish_event(
                            event_type="setup.ready" if setup_stage == "READY" else "setup.updated",
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

            # Proximity evaluation
            from services.proximity_service import proximity_service
            tg_cfg = db.query(models.TelegramConfig).first()
            proximity_service.evaluate_setup_proximity(db, watch_setup, ticker, atr, tg_cfg)

            # Auto Arming if enabled and setup is READY
            if setup_stage == "READY" and self.get_auto_state(db):
                self._auto_arm_candidate(db, watch_setup, sig, now_ms, now_dt)

            # ========================================================
            # V7 New York Session Fallback Evaluation
            # ========================================================
            policy = TradingPolicyService.get_active_policy(db, symbol)
            policy_eval = TradingPolicyService.evaluate_entry_policy(db, symbol, now_dt)
            if policy_eval.get("quota_state") == "SEEKING_FALLBACK" and policy_eval.get("ny_fills", 0) == 0:
                candles_5m = crud.get_candles(db, symbol, "5M", limit=60, ascending=True)
                if len(candles_5m) >= 15:
                    rem_budget = 15.0 # Max remaining risk budget
                    fb_res = NYFallbackService.evaluate_fallback_setup(
                        symbol=symbol,
                        candles_5m=candles_5m,
                        current_price=current_p,
                        htf_bias=collector_service.d_4h_bias,
                        h1_alignment=collector_service.h1_alignment,
                        policy=policy,
                        account_equity=capital,
                        remaining_risk_allowance_usdt=rem_budget,
                        leverage=leverage,
                        margin_mode=margin_mode,
                        ticker_data=ticker
                    )
                    if fb_res.get("is_eligible") and fb_res.get("candidate"):
                        fb_cand = fb_res["candidate"]
                        # Store fallback setup in watch setup or auto arm
                        fb_setup_id = f"watch-{symbol}-5M-fallback"
                        fb_watch = db.query(models.WatchSetup).filter(models.WatchSetup.id == fb_setup_id).first()
                        if not fb_watch:
                            fb_watch = models.WatchSetup(
                                id=fb_setup_id,
                                setup_instance_id=fb_cand["signal_id"],
                                version=1,
                                strategy="NY_FALLBACK_V1",
                                strategy_family="NY_QUOTA_PAPER",
                                direction=fb_cand["direction"],
                                timeframe="5M",
                                state="READY",
                                htf_bias=collector_service.d_4h_bias,
                                h1_alignment=collector_service.h1_alignment,
                                provisional_entry=fb_cand["planned_entry"],
                                provisional_sl=fb_cand["stop_loss"],
                                provisional_tp=fb_cand["targets"][0]["price"],
                                gross_rr=fb_cand["gross_rr"],
                                net_rr=fb_cand["estimated_net_rr"],
                                risk_usdt=fb_cand["initial_risk_usdt"],
                                quantity=fb_cand["quantity"],
                                leverage=fb_cand["leverage"],
                                margin_mode=fb_cand["margin_mode"],
                                risk_pct=fb_cand["effective_risk_pct"],
                                requested_risk_pct=fb_cand["requested_risk_pct"],
                                effective_risk_pct=fb_cand["effective_risk_pct"],
                                risk_profile="QUOTA",
                                config_version=config_version,
                                estimated_liquidation=fb_cand["estimated_liquidation"],
                                created_at=now_ms,
                                updated_at=now_ms,
                                expires_at=fb_cand["expires_at"]
                            )
                            db.add(fb_watch)
                            db.commit()

                        if self.get_auto_state(db):
                            self._auto_arm_candidate(db, fb_watch, fb_cand, now_ms, now_dt)

        except Exception as e:
            logger.error(f"Error in evaluate_upcoming_setups: {str(e)}", exc_info=True)
        finally:
            db.close()

    def _auto_arm_candidate(
        self,
        db: Session,
        watch_setup: models.WatchSetup,
        sig: Dict[str, Any],
        now_ms: int,
        now_dt: datetime
    ):
        """Auto arm READY setup if all execution & policy guards pass."""
        # 1. Check if there is already an active position or armed order
        active_pos = crud.get_active_position(db)
        if active_pos:
            return

        armed_order = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
        if armed_order:
            return

        # 2. Check Trading Policy (Max 3 fills/day, NY window, reservation)
        symbol = getattr(watch_setup, "instrument", None) or "XAUUSDT"
        policy_eval = TradingPolicyService.evaluate_entry_policy(db, symbol, now_dt)
        if not policy_eval["allowed"]:
            logger.info(f"Auto-arm blocked by trading policy: {policy_eval['reason_code']}")
            return

        # 3. Check price geometry strictly
        from domain_calculator import validate_price_geometry
        is_geom_valid, _ = validate_price_geometry(
            watch_setup.direction,
            watch_setup.provisional_entry,
            watch_setup.provisional_sl,
            watch_setup.provisional_tp
        )
        if not is_geom_valid:
            return

        # 4. Check Day Audit limits
        audit = crud.get_or_create_today_audit(db)
        if audit.is_blocked or audit.fills_count >= 3 or audit.consecutive_losses >= 2:
            return
        if audit.cooldown_until and now_ms < audit.cooldown_until:
            return

        # 5. Effective risk determination (preserve 0.10% cap for fallback)
        family = getattr(watch_setup, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC"
        eff_risk_pct = watch_setup.risk_pct or 0.25
        risk_profile = "STANDARD"
        if family == "NY_QUOTA_PAPER":
            eff_risk_pct = min(eff_risk_pct, 0.10)
            risk_profile = "QUOTA"

        # 5.5 V10.1 Lesson Rules Evaluation (BEFORE_ARM for AUTO)
        from services.entry_decision_service import EntryDecisionService
        lesson_context = EntryDecisionService.build_context(
            stage="BEFORE_ARM",
            symbol=symbol,
            direction=watch_setup.direction,
            strategy_family=family,
            timeframe=watch_setup.timeframe,
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            planned_entry=watch_setup.provisional_entry,
            stop_loss=watch_setup.provisional_sl,
            take_profit=watch_setup.provisional_tp,
            net_rr=watch_setup.net_rr,
            now_ms=now_ms,
            session_instance_id=policy_eval.get("session_instance_id"),
            distance_to_entry_atr=getattr(watch_setup, "distance_to_entry_atr", None),
            setup_id=watch_setup.id
        )
        lesson_eval = EntryDecisionService.evaluate_entry_rules(db, lesson_context)
        if not lesson_eval["can_proceed"]:
            logger.info(f"Auto-arm blocked by lesson rule: {lesson_eval['blocking_reasons']}")
            return
        if lesson_eval["warning_messages"]:
            logger.info(f"Auto-arm lesson warnings (non-blocking): {lesson_eval['warning_messages']}")

        # 6. Create armed paper order with complete V7 metadata
        order_id = f"order-{uuid.uuid4().hex[:8]}"
        new_order = models.PaperOrder(
            id=order_id,
            setup_id=watch_setup.id,
            signal_id=sig.get("signal_id", f"sig-{now_ms}"),
            instrument=symbol,
            direction=watch_setup.direction,
            state="armed",
            order_type="MARKET",
            timeframe=watch_setup.timeframe,
            strategy_family=family,
            strategy_version="7.0.0",
            session_instance_id=policy_eval.get("session_instance_id"),
            policy_config_version=getattr(policy_eval.get("policy"), "version", 1) if policy_eval.get("policy") else 1,
            requested_risk_pct=watch_setup.risk_pct,
            effective_risk_pct=eff_risk_pct,
            risk_profile=risk_profile,
            planned_entry=watch_setup.provisional_entry,
            stop_loss=watch_setup.provisional_sl,
            take_profit=watch_setup.provisional_tp,
            quantity=watch_setup.quantity,
            initial_risk_usdt=watch_setup.risk_usdt,
            risk_pct=eff_risk_pct,
            gross_rr=watch_setup.gross_rr,
            estimated_net_rr=watch_setup.net_rr,
            leverage=watch_setup.leverage,
            margin_mode=watch_setup.margin_mode,
            estimated_liquidation=watch_setup.estimated_liquidation,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=now_ms + (2 * 3600 * 1000),
            origin="AUTO_STRATEGY",
            execution_mode="AUTO",
            arm_decision_snapshot=json.dumps(lesson_eval),
            lessons_retrieved=json.dumps(lesson_eval.get("lessons_retrieved_snapshot", []))
        )
        EntryDecisionService.record_stage_decision(new_order, "BEFORE_ARM", lesson_eval, now_ms=now_ms)
        db.add(new_order)
        watch_setup.state = "ARMED"

        event_bus.publish_event(
            event_type="order.armed",
            aggregate_id=order_id,
            payload={
                "order_id": order_id,
                "setup_id": watch_setup.id,
                "direction": watch_setup.direction,
                "strategy_family": family,
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
                await asyncio.sleep(6.0)
                await asyncio.to_thread(self.evaluate_upcoming_setups, "XAUUSDT", "15M")
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error(f"Strategy loop unexpected error: {e}", exc_info=True)
                await asyncio.sleep(4.0)

strategy_service = StrategyService()
