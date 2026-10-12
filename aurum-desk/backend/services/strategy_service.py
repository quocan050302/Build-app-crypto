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
from services.entry_guard_service import serialize_entry_mutation

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

    def evaluate_ny_quota_cycle(self, db: Session, symbol: str, context) -> Dict[str, Any]:
        """V14.3 NY quota orchestration (fallback search -> dedicated upsert -> guarded auto-arm).

        Only orchestrates; the standard SMC flow is untouched. `context` is a
        `services.ny_quota_service.NYCycleContext` (injected clock/quote/data). The caller's DB session is
        used as-is (no SessionLocal reopened). Search is NOT permission: arm re-runs the V14.2 entry guard.
        """
        from services.ny_quota_service import run_ny_quota_cycle
        if context.auto_enabled is None:
            context.auto_enabled = self.get_auto_state(db)
        return run_ny_quota_cycle(db, symbol, context, arm_fn=self._auto_arm_candidate)

    def evaluate_upcoming_setups(
        self,
        symbol: str = "XAUUSDT",
        timeframe: str = "15M",
        *,
        db: Optional[Session] = None,
        clock: Any = None,
        ticker: Optional[Dict[str, Any]] = None,
        quote: Any = None,
        htf_bias: Optional[str] = None,
        h1_alignment: Optional[str] = None,
        feed_health: Optional[Dict[str, Any]] = None,
        candles_5m: Optional[list] = None,
        auto_enabled: Optional[bool] = None,
    ):
        """
        Evaluate market conditions and maintain watch_setups progression:
        WATCHING -> WAITING_PRICE -> WAITING_SWEEP -> WAITING_MSS -> WAITING_RETRACE -> READY -> ARMED.

        V14.3: keyword-only injection (db/clock/ticker/quote/bias/feed/candles/auto) makes the whole flow
        deterministic for tests and the frequency audit. Live callers pass nothing and behave as before.
        The NY quota cycle always runs (even when the 15M data is too short for the standard flow).
        """
        owns_db = db is None
        if owns_db:
            db = SessionLocal()
        ny_ctx = None
        ny_done = False
        try:
            now_ms = clock.now_ms() if clock is not None else int(time.time() * 1000)
            now_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
            raw_ticker = ticker if ticker is not None else collector_service.latest_ticker
            from services.ny_quota_service import NYCycleContext
            ny_ctx = NYCycleContext(
                now_ms=now_ms, clock=clock, quote=quote, ticker=raw_ticker,
                htf_bias=htf_bias if htf_bias is not None else collector_service.d_4h_bias,
                h1_alignment=h1_alignment if h1_alignment is not None else collector_service.h1_alignment,
                candles_5m=candles_5m, feed_health=feed_health, auto_enabled=auto_enabled,
            )
            # V14.4.1: market-reading cycle (scenarios -> certified setup -> guarded auto-arm). It is isolated:
            # any failure is logged as SYSTEM_ERROR and never blocks the legacy flow, NY fallback or exits.
            self.evaluate_market_reading_cycle(
                db, symbol, now_ms=now_ms, now_dt=now_dt, clock=clock, quote=quote,
                feed_health=feed_health, auto_enabled=auto_enabled, ticker=raw_ticker,
            )
            candles = crud.get_candles(db, symbol, timeframe, limit=150, ascending=True, cutoff_ms=now_ms)
            if len(candles) < 20:
                # V14.3: do not return silently - the NY cycle still audits/searches with its own 5M data.
                ny_ctx.standard_status = "STANDARD_DATA_MISSING"
                self.evaluate_ny_quota_cycle(db, symbol, ny_ctx)
                ny_done = True
                return

            ticker = raw_ticker or {"bid": candles[-1].close, "ask": candles[-1].close, "last": candles[-1].close}
            is_blackout, blackout_reason, _ = crud.check_news_blackout(db, now_ms)
            day_audit = crud.get_or_create_today_audit(db, clock=clock)

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
                htf_bias=ny_ctx.htf_bias,
                h1_alignment=ny_ctx.h1_alignment,
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
            # V14.2: provenance of the plan. Without an active signal the plan is only an observation
            # (entry = current price). A trend that is neither BULLISH nor BEARISH defaults the DISPLAY
            # direction to SHORT, which is NOT a confirmed SELL signal -> direction_source UNCONFIRMED.
            plan_kind = "CONFIRMED" if sig else "PROVISIONAL"
            if sig:
                direction_source = "SIGNAL"
            elif analysis.get("trend") in ("BULLISH", "BEARISH"):
                direction_source = "TREND"
            else:
                direction_source = "UNCONFIRMED"
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
                    plan_kind=plan_kind,
                    direction_source=direction_source,
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
                    # D12: Never mutate direction or confirmed levels of ARMED or PAPER_OPEN setup
                else:
                    direction_changed = (watch_setup.direction != direction)
                    state_changed = (watch_setup.state != setup_stage)
                    entry_diff = abs(watch_setup.provisional_entry - provisional_entry) if watch_setup.provisional_entry is not None else 0.0
                    sl_diff = abs(watch_setup.provisional_sl - provisional_sl) if (watch_setup.provisional_sl is not None and provisional_sl is not None) else 0.0
                    tp_diff = abs(watch_setup.provisional_tp - provisional_tp) if (watch_setup.provisional_tp is not None and provisional_tp is not None) else 0.0
                    threshold = max(atr * 0.1, 0.05)
                    levels_changed = (entry_diff > threshold) or (sl_diff > threshold) or (tp_diff > threshold)

                    cond_met_str = json.dumps(analysis.get("conditions_met", []))
                    cond_rem_str = json.dumps(analysis.get("missing_conditions", []))
                    evidence_changed = (watch_setup.conditions_met != cond_met_str) or (watch_setup.conditions_remaining != cond_rem_str)
                    config_changed = (watch_setup.config_version != config_version)
                    instance_changed = (watch_setup.setup_instance_id != setup_instance_id)

                    has_material_change = direction_changed or state_changed or levels_changed or evidence_changed or config_changed or instance_changed

                    if has_material_change:
                        watch_setup.version += 1
                        if direction_changed or instance_changed:
                            watch_setup.setup_instance_id = setup_instance_id
                            watch_setup.direction = direction
                            # D01, D02, F4: Clear stale confirmed levels on new direction or instance
                            watch_setup.confirmed_entry = None
                            watch_setup.confirmed_sl = None
                            watch_setup.confirmed_tp = None

                        if setup_stage == "READY" and watch_setup.state != "READY":
                            watch_setup.setup_instance_id = setup_instance_id

                        watch_setup.state = setup_stage
                        watch_setup.strategy_family = "STANDARD_SMC"
                        watch_setup.plan_kind = plan_kind
                        watch_setup.direction_source = direction_source
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
                        watch_setup.conditions_met = cond_met_str
                        watch_setup.conditions_remaining = cond_rem_str
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
                                "revision": watch_setup.version,
                                "direction": watch_setup.direction,
                                "state": watch_setup.state,
                                "planned_entry": watch_setup.provisional_entry,
                                "stop_loss": watch_setup.provisional_sl,
                                "take_profit": watch_setup.provisional_tp,
                                "net_rr": watch_setup.net_rr,
                                "risk_usdt": watch_setup.risk_usdt,
                                "config_version": watch_setup.config_version,
                                "conditions_met": analysis.get("conditions_met", []),
                                "missing_conditions": analysis.get("missing_conditions", [])
                            }
                        )

            # Proximity evaluation
            from services.proximity_service import proximity_service
            tg_cfg = db.query(models.TelegramConfig).first()
            proximity_service.evaluate_setup_proximity(db, watch_setup, ticker, atr, tg_cfg)

            # V14 Setup Quality Assessment
            self._assess_and_save_quality(
                db=db,
                watch_setup=watch_setup,
                analysis=analysis,
                candles=candles,
                ticker=ticker,
                is_news_blackout=is_blackout,
                news_blackout_reason=blackout_reason,
                now_ms=now_ms
            )

            # Auto Arming if enabled and setup is READY
            if auto_enabled is None:
                ny_ctx.auto_enabled = self.get_auto_state(db)
            if setup_stage == "READY" and ny_ctx.auto_enabled:
                self._auto_arm_candidate(db, watch_setup, sig, now_ms, now_dt,
                                         clock=clock, quote=quote, feed_health=feed_health)

            # ========================================================
            # V7/V14.3 New York Session quota cycle (fallback search/upsert/arm + audit)
            # ========================================================
            self.evaluate_ny_quota_cycle(db, symbol, ny_ctx)
            ny_done = True

        except Exception as e:
            logger.error(f"Error in evaluate_upcoming_setups: {str(e)}", exc_info=True)
            if ny_ctx is not None and not ny_done:
                # A standard-flow failure must not silence the NY quota diagnostics/search.
                try:
                    db.rollback()
                    ny_ctx.standard_status = "STANDARD_SYSTEM_ERROR"
                    self.evaluate_ny_quota_cycle(db, symbol, ny_ctx)
                except Exception as e2:  # noqa: BLE001
                    logger.error(f"NY quota cycle after standard failure also failed: {e2}", exc_info=True)
        finally:
            if owns_db:
                db.close()


    # ------------------------------------------------------------------ V14.4.1 market reading
    last_market_reading: Dict[str, Any] = {}
    # V14.4.1: cached three-session forecasts per symbol, filled ONLY by the cycle (GET never runs the engine).
    session_forecasts_cache: Dict[str, Dict[str, Any]] = {}

    def _cache_session_forecasts(self, symbol: str, candles_by_tf: Dict[str, list], now_ms: int, cfg: Any,
                                 primary: Any, issues: Any) -> None:
        try:
            from services.smc_scenario_service import load_closed_context
            from services.smc_session_forecast_service import evaluate_session_forecasts
            ctx = load_closed_context(candles_by_tf, now_ms, cfg)
            fc = evaluate_session_forecasts(symbol, ctx, now_ms, cfg, primary_scenario=primary)
            self.session_forecasts_cache[symbol] = {
                "symbol": symbol, "as_of_ms": now_ms, "day": next(iter(fc.values())).session_id.split(":")[-1],
                "forecasts": {k: v.to_dict() for k, v in fc.items()},
                "coverage": {k: {"data_status": v.data_status, "is_partial": v.is_partial,
                                 **{f: (v.session_range or {}).get(f) for f in
                                    ("coverage_status", "coverage_ratio", "bars_count", "expected_bars", "gap_bars")}}
                             for k, v in fc.items()},
                "data_issues": [getattr(i, "code", str(i)) for i in (issues or [])],
            }
        except Exception as exc:  # noqa: BLE001 - forecast is observation only
            logger.error(f"SYSTEM_ERROR in session forecast: {exc}", exc_info=True)
            self.session_forecasts_cache[symbol] = {
                "symbol": symbol, "as_of_ms": now_ms, "day": None, "forecasts": {}, "coverage": {},
                "data_issues": ["SYSTEM_ERROR"], "error": str(exc)}

    def _load_scenario_candles(self, db: Session, symbol: str, now_ms: int) -> Dict[str, list]:
        """Bounded window: M5/M15 from the previous NY day's Asia open to now; closed D/4H/1H history."""
        from services.smc_session_context import scenario_history_start_ms
        start = scenario_history_start_ms(now_ms)
        return {
            "5M": crud.get_candles(db, symbol, "5M", limit=900, ascending=True, cutoff_ms=now_ms, start_ms=start),
            "15M": crud.get_candles(db, symbol, "15M", limit=300, ascending=True, cutoff_ms=now_ms, start_ms=start),
            "1H": crud.get_candles(db, symbol, "1H", limit=80, ascending=True, cutoff_ms=now_ms),
            "4H": crud.get_candles(db, symbol, "4H", limit=80, ascending=True, cutoff_ms=now_ms),
            "D": crud.get_candles(db, symbol, "D", limit=12, ascending=True, cutoff_ms=now_ms),
        }

    def evaluate_market_reading_cycle(
        self, db: Session, symbol: str, *, now_ms: int, now_dt: datetime, clock: Any = None, quote: Any = None,
        feed_health: Optional[Dict[str, Any]] = None, auto_enabled: Optional[bool] = None,
        ticker: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Scenario cycle: persist material revisions (commit), publish events AFTER the commit, promote at most
        ONE confirmed scenario with a valid atomic plan to the WatchSetup table, then (auto only) hand it to
        `_auto_arm_candidate` - the same guarded arm used by every other family. Never raises."""
        from services.smc_scenario_config import active_scenario_config as cfg
        if not cfg.scenario_explain_enabled:
            self.last_market_reading = {"status": "DISABLED_BY_FLAG", "as_of_ms": now_ms}
            return self.last_market_reading
        try:
            from domain_calculator import CostAssumptions
            from services.quote_validator import QuoteValidator
            from services.risk_settings_service import risk_settings_service
            from services.smc_scenario_binding import promote_confirmed_scenario
            from services.smc_scenario_plan import FinancialSettings
            from services.smc_scenario_service import evaluate_scenario_cycle, select_primary_scenario
            from services.smc_scenario_types import ScenarioState

            candles_by_tf = self._load_scenario_candles(db, symbol, now_ms)
            g = risk_settings_service.get_settings(db)
            audit = crud.get_or_create_today_audit(db, clock=clock)
            settings = FinancialSettings(
                capital_usdt=float(audit.current_equity), risk_pct=float(g.risk_pct),
                leverage=int(g.requested_leverage), margin_mode=str(g.margin_mode), costs=CostAssumptions(),
                instrument_version=str(getattr(g, "metadata_version", None) or "unknown"),
            )
            win = TradingPolicyService.get_ny_session_window(now_ms=now_ms, clock=clock)
            entry_window = (win["window_start_ms"], win["window_end_ms"])
            if quote is not None:
                q = quote
            else:
                raw = ticker if ticker is not None else collector_service.latest_ticker
                q = QuoteValidator.validate_ticker(raw, now_ms=now_ms) if raw else None

            result = evaluate_scenario_cycle(
                db, symbol, candles_by_tf, q, now_ms, config=cfg, settings=settings, entry_window=entry_window)
            self._cache_session_forecasts(symbol, candles_by_tf, now_ms, cfg, result.primary, result.issues)

            promotable = [s for s in result.snapshots if s.state == ScenarioState.STRUCTURE_CONFIRMED
                          and not s.blockers and (s.plan or {}).get("is_valid")]
            winner = select_primary_scenario(promotable) if promotable else None
            ws = None
            if winner is not None:
                ws = promote_confirmed_scenario(
                    db, winner, now_ms=now_ms, risk_config_version=int(g.config_version),
                    leverage=settings.leverage, margin_mode=settings.margin_mode, risk_pct=settings.risk_pct,
                    expires_at_ms=min(now_ms + cfg.scenario_ttl_ms, entry_window[1]) if entry_window[1] > now_ms
                    else now_ms)
                if ws is not None and ws.state == "READY":
                    self._assess_and_save_quality(
                        db=db, watch_setup=ws,
                        analysis={"htf_bias": ws.htf_bias, "h1_alignment": ws.h1_alignment, "setup_stage": "READY",
                                  "conditions_met": json.loads(ws.conditions_met or "[]"), "missing_conditions": []},
                        candles=candles_by_tf.get("5M") or [], ticker=ticker, now_ms=now_ms)
            db.commit()   # persist FIRST ...

            for s in result.changed + result.superseded:   # ... then publish (join + second commit)
                event_bus.publish_event(
                    event_type="scenario.updated", aggregate_id=s.instance_id, aggregate_version=s.revision,
                    db=db, occurred_at=now_ms,
                    payload={"scenario_id": s.scenario_id, "instance_id": s.instance_id, "snapshot_id": s.snapshot_id,
                             "revision": s.revision, "symbol": s.symbol, "session_id": s.session_id,
                             "state": s.state.value, "direction": s.direction, "digest": s.digest})
            if result.changed or result.superseded:
                db.commit()

            armed = None
            if ws is not None and ws.state == "READY":
                auto = auto_enabled if auto_enabled is not None else self.get_auto_state(db)
                if auto:
                    armed = self._auto_arm_candidate(
                        db, ws, {"signal_id": winner.instance_id}, now_ms, now_dt,
                        clock=clock, quote=q, feed_health=feed_health)
            self.last_market_reading = {
                "status": "OK", "as_of_ms": now_ms, "scenarios": len(result.snapshots),
                "changed": len(result.changed), "promoted": ws.id if ws is not None else None,
                "armed": armed, "issues": [i.code for i in result.issues]}
        except Exception as exc:  # noqa: BLE001 - observation must never stop exits / NY cycle
            logger.error(f"SYSTEM_ERROR in market-reading cycle: {exc}", exc_info=True)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            self.last_market_reading = {"status": "SYSTEM_ERROR", "as_of_ms": now_ms, "error": str(exc)}
        return self.last_market_reading

    def _upsert_watch_setup(self, db: Session, smc_data: Dict[str, Any]) -> models.WatchSetup:
        symbol = smc_data.get("symbol", "XAUUSDT")
        timeframe = smc_data.get("timeframe", "15M")
        setup_id = smc_data.get("id") or f"watch-{symbol}-{timeframe}"
        watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == setup_id).first()

        now_ms = int(time.time() * 1000)
        direction = smc_data.get("direction", "LONG")
        setup_stage = smc_data.get("state", "READY")
        provisional_entry = smc_data.get("planned_entry", 2050.0)
        provisional_sl = smc_data.get("stop_loss", 2040.0)
        provisional_tp = smc_data.get("take_profit", 2070.0)
        atr = smc_data.get("atr", 2.0)
        setup_instance_id = smc_data.get("setup_instance_id") or f"inst-{symbol}-{timeframe}-{direction.lower()}-{now_ms}"
        config_version = smc_data.get("config_version", 1)
        invalidation_price = smc_data.get("invalidation_price", provisional_sl)
        inval_reason = smc_data.get("invalidation_reason", "Sweep / Structure Breach")
        cond_met_str = json.dumps(smc_data.get("conditions_met", []))
        cond_rem_str = json.dumps(smc_data.get("conditions_remaining", []))

        if not watch_setup:
            watch_setup = models.WatchSetup(
                id=setup_id,
                timeframe=timeframe,
                direction=direction,
                state=setup_stage,
                setup_instance_id=setup_instance_id,
                version=1,
                provisional_entry=provisional_entry,
                provisional_sl=provisional_sl,
                provisional_tp=provisional_tp,
                invalidation_price=invalidation_price,
                invalidation_reason=inval_reason,
                gross_rr=smc_data.get("gross_rr", 2.0),
                net_rr=smc_data.get("net_rr", 1.8),
                risk_usdt=smc_data.get("risk_usdt", 2.5),
                quantity=smc_data.get("quantity", 0.05),
                leverage=smc_data.get("leverage", 5),
                margin_mode=smc_data.get("margin_mode", "ISOLATED"),
                risk_pct=smc_data.get("risk_pct", 0.25),
                config_version=config_version,
                conditions_met=cond_met_str,
                conditions_remaining=cond_rem_str,
                created_at=now_ms,
                updated_at=now_ms,
            )
            db.add(watch_setup)
            db.commit()
            db.refresh(watch_setup)
            return watch_setup

        if watch_setup.state == "PAPER_OPEN":
            watch_setup.updated_at = now_ms
            db.commit()
            db.refresh(watch_setup)
            return watch_setup

        if watch_setup.state == "ARMED" and watch_setup.direction == direction and (not setup_instance_id or watch_setup.setup_instance_id == setup_instance_id):
            watch_setup.updated_at = now_ms
            db.commit()
            db.refresh(watch_setup)
            return watch_setup

        direction_changed = (watch_setup.direction != direction)
        state_changed = (watch_setup.state != setup_stage)
        entry_diff = abs(watch_setup.provisional_entry - provisional_entry) if watch_setup.provisional_entry is not None else 0.0
        sl_diff = abs(watch_setup.provisional_sl - provisional_sl) if (watch_setup.provisional_sl is not None and provisional_sl is not None) else 0.0
        tp_diff = abs(watch_setup.provisional_tp - provisional_tp) if (watch_setup.provisional_tp is not None and provisional_tp is not None) else 0.0
        threshold = max(atr * 0.1, 0.05)
        levels_changed = (entry_diff > threshold) or (sl_diff > threshold) or (tp_diff > threshold)

        evidence_changed = (watch_setup.conditions_met != cond_met_str) or (watch_setup.conditions_remaining != cond_rem_str)
        config_changed = (watch_setup.config_version != config_version)
        instance_changed = (watch_setup.setup_instance_id != setup_instance_id)

        has_material_change = direction_changed or state_changed or levels_changed or evidence_changed or config_changed or instance_changed

        if has_material_change:
            watch_setup.version += 1
            if direction_changed or instance_changed:
                watch_setup.setup_instance_id = setup_instance_id
                watch_setup.direction = direction
                watch_setup.confirmed_entry = None
                watch_setup.confirmed_sl = None
                watch_setup.confirmed_tp = None
                # Cancel previous armed orders for this setup since setup changed
                old_armed = db.query(models.PaperOrder).filter(
                    models.PaperOrder.setup_id == watch_setup.id,
                    models.PaperOrder.state == "armed"
                ).all()
                for o in old_armed:
                    o.state = "cancelled"

            watch_setup.state = setup_stage
            watch_setup.provisional_entry = provisional_entry
            watch_setup.provisional_sl = provisional_sl
            watch_setup.provisional_tp = provisional_tp
            watch_setup.invalidation_price = invalidation_price
            watch_setup.invalidation_reason = inval_reason
            watch_setup.gross_rr = smc_data.get("gross_rr", watch_setup.gross_rr)
            watch_setup.net_rr = smc_data.get("net_rr", watch_setup.net_rr)
            watch_setup.risk_usdt = smc_data.get("risk_usdt", watch_setup.risk_usdt)
            watch_setup.quantity = smc_data.get("quantity", watch_setup.quantity)
            watch_setup.config_version = config_version
            watch_setup.conditions_met = cond_met_str
            watch_setup.conditions_remaining = cond_rem_str
            watch_setup.updated_at = now_ms
            db.commit()
            db.refresh(watch_setup)

        return watch_setup

    @serialize_entry_mutation
    def _auto_arm_candidate(
        self,
        db: Session,
        watch_setup: models.WatchSetup,
        sig: Dict[str, Any],
        now_ms: int,
        now_dt: datetime,
        *,
        clock: Any = None,
        quote: Any = None,
        feed_health: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Auto arm READY setup if all execution & policy guards pass.

        V14.3: returns {"armed": bool, "codes": [...], "order_id": str|None, "message": str|None} so the NY
        cycle can audit WHY an arm did not happen. `clock`/`quote`/`feed_health` are injected by the NY
        cycle (historical determinism); live callers leave them None and keep the previous behaviour.
        """
        def _no(codes, message=None):
            return {"armed": False, "codes": list(codes), "order_id": None, "message": message}

        # 1. Check if there is already an active position or armed order
        active_pos = crud.get_active_position(db)
        if active_pos:
            return _no(["ACTIVE_POSITION_EXISTS"])

        armed_order = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
        if armed_order:
            return _no(["ARMED_ORDER_EXISTS"])

        # 2. Check Trading Policy (Max 3 fills/day, NY window, reservation)
        symbol = getattr(watch_setup, "instrument", None) or "XAUUSDT"
        policy_eval = TradingPolicyService.evaluate_entry_policy(
            db, symbol, now_dt, clock=clock, now_ms=now_ms,
            strategy_family=getattr(watch_setup, "strategy_family", None) or "STANDARD_SMC",
        )
        if not policy_eval["allowed"]:
            logger.info(f"Auto-arm blocked by trading policy: {policy_eval['reason_code']}")
            self._log_auto_arm_block(
                watch_setup, [str(policy_eval.get("reason_code") or "POLICY_BLOCKED")],
                policy_eval.get("message") or policy_eval.get("reason"),
            )
            return _no(policy_eval.get("reason_codes") or [str(policy_eval.get("reason_code") or "POLICY_BLOCKED")],
                       policy_eval.get("reason_message"))

        # 3. Check price geometry strictly
        from domain_calculator import validate_price_geometry
        is_geom_valid, _ = validate_price_geometry(
            watch_setup.direction,
            watch_setup.provisional_entry,
            watch_setup.provisional_sl,
            watch_setup.provisional_tp
        )
        if not is_geom_valid:
            return _no(["INVALID_GEOMETRY"])

        # 4. Check Day Audit limits
        audit = crud.get_or_create_today_audit(db, clock=clock)
        if audit.is_blocked:
            return _no(["DAY_BLOCKED"])
        if audit.fills_count >= 3:
            return _no(["DAILY_FILL_CAP"])
        if audit.consecutive_losses >= 2:
            return _no(["MAX_CONSECUTIVE_LOSSES"])
        if audit.cooldown_until and now_ms < audit.cooldown_until:
            return _no(["COOLDOWN_ACTIVE"])

        # 5. Effective risk determination (preserve 0.10% cap for fallback)
        family = getattr(watch_setup, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC"
        eff_risk_pct = watch_setup.risk_pct or 0.25
        risk_profile = "STANDARD"
        if family == "NY_QUOTA_PAPER":
            eff_risk_pct = min(eff_risk_pct, 0.10)
            risk_profile = "QUOTA"

        # 5.5 V14.2 Common Entry Guard (stage ARM, mode AUTO / origin AUTO_STRATEGY).
        # Same orchestrator as manual arm / manual market / pending fill: structure state, atomic plan,
        # real calculator result, account, session policy, news blackout, governed lessons, quote, feed.
        from services.entry_decision_service import EntryDecisionService
        from services.entry_guard_service import EntryGuardRequest, evaluate_entry_guard
        from services.plan_resolver import resolve_plan_levels
        from services.quote_validator import QuoteValidator
        from services.setup_quality_service import build_plan_fingerprint
        from domain_calculator import calculate_risk_reward

        # V14.3: a NY fallback candidate (same instance) is armed at most once. A rejected/expired/closed
        # order of the same candidate identity is never retried "until it happens to fill".
        if family == "NY_QUOTA_PAPER" and getattr(watch_setup, "setup_instance_id", None):
            used = db.query(models.PaperOrder.id).filter(
                models.PaperOrder.setup_instance_id == watch_setup.setup_instance_id).first()
            if used is not None:
                return _no(["CANDIDATE_ALREADY_USED"], "Ứng viên fallback này đã được dùng để đặt lệnh")

        plan = resolve_plan_levels(watch_setup)
        if not plan.get("is_valid"):
            self._log_auto_arm_block(watch_setup, ["INVALID_PRICE_GEOMETRY"], plan.get("error_message"))
            return _no(["INVALID_PRICE_GEOMETRY"], plan.get("error_message"))
        plan_entry, plan_sl, plan_tp = plan["entry"], plan["stop_loss"], plan["take_profit"]

        policy_obj = policy_eval.get("policy")
        min_rr = getattr(policy_obj, "min_net_rr", None) or 2.0
        calc_res = calculate_risk_reward(
            direction=watch_setup.direction,
            planned_entry=plan_entry,
            stop_loss=plan_sl,
            take_profit=plan_tp,
            capital_usdt=audit.current_equity,
            risk_pct=eff_risk_pct,
            min_net_rr=min_rr,
            leverage=watch_setup.leverage or 5,
            margin_mode=watch_setup.margin_mode or "ISOLATED",
            now_ms=now_ms,
        )

        if quote is not None:
            auto_quote = quote
        else:
            raw_ticker = collector_service.latest_ticker
            auto_quote = QuoteValidator.validate_ticker(raw_ticker, now_ms=now_ms) if raw_ticker else None

        # V14.4.1: certified verification = the SAME validator as manual arm. READY is never evidence:
        # scenario ref -> exact snapshot binding; legacy StrategyEvidence -> measured validator; neither -> None
        # (unknown), which the certified guard blocks. NY_QUOTA_PAPER keeps its own adapter (None here).
        try:
            from services.smc_scenario_binding import verify_setup_structure
            scenario_verified, _ev_dict, _binding = verify_setup_structure(
                db, watch_setup, symbol=symbol, now_ms=now_ms,
                plan_levels={"entry": plan_entry, "stop_loss": plan_sl, "take_profit": plan_tp},
            )
        except Exception as exc:  # noqa: BLE001 - a verifier failure must block, never pass
            logger.error(f"Structure verification failed for {watch_setup.id}: {exc}", exc_info=True)
            scenario_verified = False

        guard = evaluate_entry_guard(db, EntryGuardRequest(
            stage="ARM",
            now_ms=now_ms,
            symbol=symbol,
            direction=watch_setup.direction,
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            planned_entry=plan_entry,
            stop_loss=plan_sl,
            take_profit=plan_tp,
            calc_result=calc_res,
            validated_quote=auto_quote,
            strategy_family=family,
            order_type="MARKET",
            timeframe=watch_setup.timeframe,
            plan_kind=getattr(watch_setup, "plan_kind", None) or "CONFIRMED",
            direction_source=getattr(watch_setup, "direction_source", None),
            setup_id=watch_setup.id,
            setup_instance_id=watch_setup.setup_instance_id,
            setup_version=watch_setup.version,
            setup_state=watch_setup.state,
            plan_fingerprint=build_plan_fingerprint({
                "direction": watch_setup.direction, "planned_entry": plan_entry,
                "stop_loss": plan_sl, "take_profit": plan_tp,
            }),
            risk_config_version=watch_setup.config_version,
            min_net_rr=min_rr,
            distance_to_entry_atr=getattr(watch_setup, "distance_to_entry_atr", None),
            session_instance_id=policy_eval.get("session_instance_id"),
            structure_chain_verified=(scenario_verified if family != "NY_QUOTA_PAPER" else None),
            clock=clock,
            feed_health=feed_health,
        ))
        if not guard.allowed:
            self._log_auto_arm_block(watch_setup, guard.reason_codes, (guard.primary_reason or {}).get("message_vi"))
            return _no(guard.reason_codes, (guard.primary_reason or {}).get("message_vi"))
        self._last_auto_block = None
        lesson_eval = dict(guard.lesson_result or {
            "can_proceed": True, "blocking_reasons": [], "warning_messages": [],
            "advisory_notes": [], "lessons_retrieved_snapshot": [],
        })
        if lesson_eval.get("warning_messages"):
            logger.info(f"Auto-arm lesson warnings (non-blocking): {lesson_eval['warning_messages']}")
        lesson_eval["entry_gate"] = guard.summary()

        # 6. Create armed paper order with complete V7 metadata
        order_id = f"order-{uuid.uuid4().hex[:8]}"
        new_order = models.PaperOrder(
            id=order_id,
            setup_id=watch_setup.id,
            signal_id=sig.get("signal_id", f"sig-{now_ms}"),
            setup_instance_id=getattr(watch_setup, "setup_instance_id", None),
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
            planned_entry=plan_entry,
            stop_loss=plan_sl,
            take_profit=plan_tp,
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
            expires_at=(
                min(now_ms + (2 * 3600 * 1000), watch_setup.expires_at)
                if family == "NY_QUOTA_PAPER" and watch_setup.expires_at
                else now_ms + (2 * 3600 * 1000)
            ),
            origin="AUTO_STRATEGY",
            execution_mode="AUTO",
            scenario_snapshot_id=getattr(watch_setup, "scenario_snapshot_id", None),
            quality_snapshot_id=getattr(watch_setup, "quality_snapshot_id", None),
            evidence_snapshot_id=getattr(watch_setup, "evidence_snapshot_id", None),
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
                "planned_entry": plan_entry,
                "stop_loss": plan_sl,
                "take_profit": plan_tp,
                "net_rr": watch_setup.net_rr,
                "distance_usdt": watch_setup.distance_to_entry_usdt
            },
            db=db,
            occurred_at=now_ms
        )
        db.commit()
        return {"armed": True, "codes": [], "order_id": order_id, "message": None}

    def _log_auto_arm_block(self, watch_setup, codes, message=None):
        """Log an auto-arm block once per (setup, version, reasons); the 6s loop must not spam."""
        signature = (watch_setup.id, watch_setup.version, tuple(codes))
        if getattr(self, "_last_auto_block", None) == signature:
            return
        self._last_auto_block = signature
        logger.info(f"Auto-arm blocked by entry guard: {list(codes)} {message or ''}".strip())

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


    def _assess_and_save_quality(
        self,
        db: Session,
        watch_setup: models.WatchSetup,
        analysis: Dict[str, Any],
        candles: list,
        ticker: Optional[Dict[str, Any]],
        is_news_blackout: bool = False,
        news_blackout_reason: Optional[str] = None,
        now_ms: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        if not watch_setup:
            return None
        now_ms = now_ms or int(time.time() * 1000)
        try:
            from services.setup_quality_service import assess_setup_quality, build_quality_snapshot
            from services.setup_quality_repository import setup_quality_repository

            from services.plan_resolver import resolve_plan_levels
            from services.quote_validator import QuoteValidator

            resolved_p = resolve_plan_levels(watch_setup)
            curr_p = ticker.get("last", ticker.get("close", 0.0)) if ticker else (candles[-1].close if candles else 0.0)
            entry_p = resolved_p.get("entry") if resolved_p.get("is_valid") else (watch_setup.confirmed_entry or watch_setup.provisional_entry or curr_p)
            # V14.4.1: an invalid plan is NOT repaired with SL/TP offsets or default size; unknown stays None.
            sl_p = resolved_p.get("stop_loss") if resolved_p.get("is_valid") else (watch_setup.confirmed_sl or watch_setup.provisional_sl)
            tp_p = resolved_p.get("take_profit") if resolved_p.get("is_valid") else (watch_setup.confirmed_tp or watch_setup.provisional_tp)

            q_res = QuoteValidator.validate_ticker(ticker, now_ms=now_ms) if ticker else None
            quote_valid = q_res.is_valid if q_res else False

            plan_dict = {
                "direction": watch_setup.direction,
                "entry_price": entry_p,
                "stop_loss": sl_p,
                "take_profit": tp_p,
                "quantity": watch_setup.quantity,
                "leverage": watch_setup.leverage,
                "margin_mode": watch_setup.margin_mode,
                "net_rr": watch_setup.net_rr,
                "risk_usdt": watch_setup.risk_usdt,
            }

            q_context = {
                "symbol": "XAUUSDT",
                "timeframe": watch_setup.timeframe or "15M",
                "direction": watch_setup.direction,
                "as_of_ms": now_ms,
                "analysis": analysis,
                "plan": plan_dict,
                "eligibility": {"eligible": bool(resolved_p.get("is_valid")), "reasons": []},
                "quote_view": {"is_valid": quote_valid, "price": curr_p},
                "news_view": {"is_blackout": is_news_blackout, "reason": news_blackout_reason},
                "calc": {"net_rr": watch_setup.net_rr or 0.0}
            }

            assessment, rel_ev = assess_setup_quality(q_context)
            snapshot = build_quality_snapshot(
                setup_id=watch_setup.id,
                setup_instance_id=watch_setup.setup_instance_id or f"inst-{watch_setup.id}-{now_ms}",
                setup_revision=watch_setup.version or 1,
                quality_revision=1,
                symbol="XAUUSDT",
                timeframe=watch_setup.timeframe or "15M",
                direction=watch_setup.direction,
                strategy_family=watch_setup.strategy_family or "STANDARD_SMC",
                as_of_ms=now_ms,
                plan_snapshot=plan_dict,
                assessment=assessment,
                evidence_items=rel_ev
            )

            saved_id = setup_quality_repository.save_quality_snapshot(db, snapshot)
            # V14.4.1: exact ref of THIS assessment (SetupQualitySnapshot table), not StrategyEvidence.
            watch_setup.quality_snapshot_id = saved_id
            return snapshot
        except Exception as e:
            logger.warning(f"Error computing setup quality for {watch_setup.id}: {e}")
            return None

strategy_service = StrategyService()
