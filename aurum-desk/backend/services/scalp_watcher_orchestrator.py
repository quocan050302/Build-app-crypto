"""
backend/services/scalp_watcher_orchestrator.py
V15.0 - Autonomous SMC Scalp Watcher Orchestrator (PHẦN 054 - 071).

Coordinates:
- Zone extraction and bounded watchlist updates on 15M/5M closures.
- Continuous near-zone proximity evaluation with hysteresis and cooldown.
- Autonomous M1 Scalp evaluation (Reversal & Continuation) on 1M closures.
- Daily NY Research model evaluation (PAPER ONLY) on eligible NY trading days.
- Opportunity funnel transitions recording with latency tracking.
- Canonical entry guards validation under serialize_entry_mutation for PAPER mode.
- Telegram notification dispatch via outbox pipeline (ZONE_NEAR, TRIGGER_CONFIRMED, ORDER_ARMED).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy.orm import Session

import crud
import models
from database import SessionLocal
from domain_calculator import CalculationResult, CostAssumptions
from services.collector_service import collector_service
from services.daily_ny_research_service import DailyNYResearchService
from services.entry_guard_service import (
    EntryGuardRequest,
    evaluate_entry_guard,
    serialize_entry_mutation,
)
from services.opportunity_funnel_service import OpportunityFunnelService
from services.quote_validator import QuoteValidator
from services.risk_settings_service import risk_settings_service
from services.scalp_m1_model_service import ScalpCandidate, ScalpM1ModelService
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityEvaluation,
    OpportunityReasonCode,
    OpportunityStage,
    ScalpMode,
    WatchedZone,
    ZoneState,
    get_scalp_config,
    load_effective_scalp_config,
)
from services.smc_scenario_binding import promote_confirmed_scenario
from services.telegram_service import queue_telegram_notification
from services.trading_policy_service import TradingPolicyService, resolve_entry_session_context
from services.zone_watchlist_service import ZoneWatchlistService

logger = logging.getLogger(__name__)


class ScalpWatcherOrchestrator:
    def __init__(self):
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock() if hasattr(asyncio, "Lock") else None
        self._active_zones: Dict[str, List[WatchedZone]] = {}  # symbol -> list of zones
        self._last_alert_times: Dict[str, int] = {}            # zone_id:alert_type -> timestamp
        self._running = False
        self._last_m1_evaluated_ts: Dict[str, int] = {}
        self._active_hypotheses: Dict[str, List[Any]] = {}
        self._active_liquidity_levels: Dict[str, List[Any]] = {}
        self._active_dealing_range: Dict[str, Any] = {}
        self._active_regime: Dict[str, str] = {}
        self._active_zone_assessments: Dict[str, Dict[str, Any]] = {}
        self._active_market_context: Dict[str, Dict[str, Any]] = {}

    def get_active_zones(self, symbol: str = "XAUUSDT") -> List[WatchedZone]:
        with self._lock:
            return list(self._active_zones.get(symbol, []))

    def set_active_zones(self, symbol: str, zones: List[WatchedZone]):
        with self._lock:
            self._active_zones[symbol] = list(zones)

    def get_active_hypotheses(self, symbol: str = "XAUUSDT") -> List[Any]:
        with self._lock:
            return list(self._active_hypotheses.get(symbol, []))

    def get_active_liquidity_levels(self, symbol: str = "XAUUSDT") -> List[Any]:
        with self._lock:
            return list(self._active_liquidity_levels.get(symbol, []))

    def get_zone_assessment_explanation(self, symbol: str, zone_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns real multi-dimensional assessment and Vietnamese explanation for zone (SECTIONS 083 - 086, 164).
        Never uses hardcoded scores or fake constants. Returns None (404) if zone not found.
        """
        with self._lock:
            assessments = self._active_zone_assessments.get(symbol, {})
            if zone_id in assessments:
                dto = dict(assessments[zone_id])
                dto["win_probability"] = None
                return dto

            zones = self._active_zones.get(symbol, [])
            matching = [z for z in zones if z.zone_id == zone_id]
            if not matching:
                return None
            zone = matching[0]

            ctx = self._active_market_context.get(symbol, {})
            c_px = ctx.get("current_price", zone.midpoint)
            c_atr = ctx.get("atr", 1.0)
            c_as_of = ctx.get("as_of_ms", 0)
            dr = self._active_dealing_range.get(symbol)
            levels = self._active_liquidity_levels.get(symbol, [])
            hyps = self._active_hypotheses.get(symbol, [])
            matching_hyp = next((h for h in hyps if getattr(h, "watched_zone", None) and h.watched_zone.zone_id == zone_id), None)
            from services.liquidity_scenario_service import rank_zones_multi_dimensional, build_zone_assessment_explanation
            if dr is not None:
                pairs = rank_zones_multi_dimensional([zone], levels, dr, c_px, c_atr, now_ms=c_as_of)
                if pairs:
                    return build_zone_assessment_explanation(
                        zone, pairs[0][1], matching_hyp, current_price=c_px, atr=c_atr, as_of_ms=c_as_of, is_stale=True
                    )
            return None

    def evaluate_cycle(
        self,
        db: Session,
        symbol: str = "XAUUSDT",
        *,
        now_ms: Optional[int] = None,
        clock: Any = None,
        ticker: Optional[Dict[str, Any]] = None,
        quote: Any = None,
        candles_1m: Optional[Sequence[Any]] = None,
        candles_5m: Optional[Sequence[Any]] = None,
        candles_15m: Optional[Sequence[Any]] = None,
        htf_bias: Optional[str] = None,
        h1_alignment: Optional[str] = None,
        config: Optional[M1ScalpConfig] = None,
        auto_enabled: Optional[bool] = None,
        feed_health: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Single-cycle autonomous evaluation (PHẦN 015 - 024, 054 - 056).
        Can be driven synchronously by tests / replay or asynchronously by background watcher.
        Uses load_effective_scalp_config(db) if config is not injected.
        """
        t_recv = int(now_ms if now_ms is not None else int(time.time() * 1000))
        t_start = int(time.time() * 1000)
        cfg = config if config is not None else load_effective_scalp_config(db)

        # Global auto stop policy check (PHẦN 024)
        if auto_enabled is None:
            try:
                from services.strategy_service import strategy_service
                global_auto = strategy_service.get_auto_state(db)
            except Exception:
                global_auto = False  # Fail closed for execution (PHẦN 024)
        else:
            global_auto = bool(auto_enabled)

        # Check feature flag
        scalp_mode = cfg.scalp_m1_mode
        if scalp_mode == ScalpMode.OFF.value and not cfg.daily_ny_research_enabled:
            return {
                "status": "DISABLED",
                "scalp_mode": scalp_mode,
                "as_of_ms": t_recv,
                "zones_count": 0,
                "candidates_count": 0,
                "candidates": [],
                "armed_order_id": None,
            }

        # Validate quote & price (PHẦN 035, 080)
        if quote is not None:
            if hasattr(quote, "is_valid"):
                q = quote
            elif isinstance(quote, dict):
                from services.quote_validator import ValidatedQuote
                bid_val = float(quote.get("bid", 0.0))
                ask_val = float(quote.get("ask", 0.0))
                import math
                valid = (
                    bool(quote.get("is_valid", True))
                    and not bool(quote.get("is_stale", False))
                    and bid_val > 0
                    and ask_val >= bid_val
                    and math.isfinite(bid_val)
                    and math.isfinite(ask_val)
                )
                q = ValidatedQuote(
                    is_valid=valid,
                    bid=bid_val,
                    ask=ask_val,
                    last=float(quote.get("last", (bid_val + ask_val) / 2.0 if valid else 0.0)),
                    spread=float(ask_val - bid_val if valid else 0.0),
                    exchange_time=quote.get("exchange_time") or t_recv,
                    observed_at=quote.get("observed_at") or t_recv,
                    freshness_sec=float(quote.get("freshness_sec", 0.0)),
                    is_stale=bool(quote.get("is_stale", False)),
                    rejection_code=quote.get("rejection_code"),
                    rejection_detail=quote.get("rejection_detail"),
                )
            else:
                q = quote
        else:
            raw_t = ticker if ticker is not None else collector_service.latest_ticker
            q = QuoteValidator.validate_ticker(raw_t, now_ms=t_recv) if raw_t else None

        bid = float(getattr(q, "bid", 0.0) if hasattr(q, "bid") else (q or {}).get("bid", 0.0))
        ask = float(getattr(q, "ask", 0.0) if hasattr(q, "ask") else (q or {}).get("ask", 0.0))
        import math
        is_q_valid = (
            bool(getattr(q, "is_valid", False))
            and not bool(getattr(q, "is_stale", False))
            and bid > 0
            and ask >= bid
            and math.isfinite(bid)
            and math.isfinite(ask)
        )

        if not is_q_valid:
            return {
                "status": "NO_QUOTE",
                "as_of_ms": t_recv,
                "zones_count": 0,
                "candidates_count": 0,
                "candidates": [],
                "armed_order_id": None,
            }

        current_px = (bid + ask) / 2.0

        # 1. Load or use injected candles
        c_15m = candles_15m if candles_15m is not None else crud.get_candles(db, symbol, "15M", limit=120, ascending=True, cutoff_ms=t_recv)
        c_5m = candles_5m if candles_5m is not None else crud.get_candles(db, symbol, "5M", limit=150, ascending=True, cutoff_ms=t_recv)
        c_1m = candles_1m if candles_1m is not None else crud.get_candles(db, symbol, "1M", limit=100, ascending=True, cutoff_ms=t_recv)

        import smc_engine
        atr_15m = smc_engine.compute_atr(c_15m, period=14) if c_15m else 2.0
        if atr_15m <= 0:
            atr_15m = 2.0

        bias = htf_bias if htf_bias is not None else collector_service.d_4h_bias
        align = h1_alignment if h1_alignment is not None else collector_service.h1_alignment

        # 2. Watchlist update from 15M/5M closures
        existing_zones = self.get_active_zones(symbol)
        extracted = ZoneWatchlistService.extract_zones_from_candles(c_15m, "15M", symbol, as_of_ms=t_recv, atr=atr_15m)
        if c_5m:
            extracted += ZoneWatchlistService.extract_zones_from_candles(c_5m, "5M", symbol, as_of_ms=t_recv, atr=atr_15m)

        # Merge new zones by zone_id
        zone_map = {z.zone_id: z for z in existing_zones}
        for nz in extracted:
            if nz.zone_id not in zone_map:
                zone_map[nz.zone_id] = nz
                OpportunityFunnelService.record_opportunity_transition(
                    db,
                    OpportunityEvaluation(
                        opportunity_id=f"opp_{nz.zone_id}",
                        symbol=symbol,
                        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
                        direction=nz.direction,
                        zone_id=nz.zone_id,
                        as_of_ms=t_recv,
                        stage=OpportunityStage.ZONE_DETECTED.value,
                        market_event_received_at=t_recv,
                    ),
                    now_ms=t_recv,
                )

        # 3. Proximity evaluation & ranking with hysteresis
        current_zones = list(zone_map.values())
        for z in current_zones:
            prev_state = z.state
            new_state, dist_atr = ZoneWatchlistService.evaluate_zone_proximity(z, bid, ask, atr_15m, cfg)
            z.state = new_state
            z.proximity_distance_atr = dist_atr
            z.last_evaluated_at = t_recv

            if new_state == ZoneState.TOUCHED.value and prev_state != ZoneState.TOUCHED.value:
                z.touch_count += 1
                OpportunityFunnelService.record_opportunity_transition(
                    db,
                    OpportunityEvaluation(
                        opportunity_id=f"opp_{z.zone_id}",
                        symbol=symbol,
                        direction=z.direction,
                        zone_id=z.zone_id,
                        as_of_ms=t_recv,
                        stage=OpportunityStage.ZONE_TOUCHED.value,
                        market_event_received_at=t_recv,
                    ),
                    now_ms=t_recv,
                )

            # NEAR notification with hysteresis and cooldown
            if new_state == ZoneState.NEAR.value and prev_state != ZoneState.NEAR.value:
                alert_key = f"{z.zone_id}:NEAR"
                last_alert = self._last_alert_times.get(alert_key, 0)
                if (t_recv - last_alert) > 15 * 60 * 1000:
                    self._last_alert_times[alert_key] = t_recv
                    OpportunityFunnelService.record_opportunity_transition(
                        db,
                        OpportunityEvaluation(
                            opportunity_id=f"opp_{z.zone_id}",
                            symbol=symbol,
                            direction=z.direction,
                            zone_id=z.zone_id,
                            as_of_ms=t_recv,
                            stage=OpportunityStage.PRICE_NEAR.value,
                            market_event_received_at=t_recv,
                        ),
                        now_ms=t_recv,
                    )
                    # Queue Telegram notification
                    if scalp_mode != ScalpMode.OFF.value:
                        queue_telegram_notification(
                            db=db,
                            item_type="NEAR_ENTRY",
                            data={
                                "setup_id": z.zone_id,
                                "direction": z.direction,
                                "reference_price": current_px,
                                "entry_zone_low": z.bottom,
                                "entry_zone_high": z.top,
                                "entry_target": z.midpoint,
                                "distance_price": abs(current_px - z.midpoint),
                                "distance_atr": dist_atr,
                                "atr": atr_15m,
                                "auto_paper_enabled": (scalp_mode == ScalpMode.PAPER.value and global_auto),
                            },
                            occurred_at=t_recv,
                        )

        # Build Liquidity Map, Dealing Range & Hypotheses (SECTIONS 028, 071 - 096)
        from services.liquidity_scenario_service import (
            identify_confirmed_swings,
            build_liquidity_hypotheses,
            compute_dealing_range,
            classify_market_regime,
            rank_zones_multi_dimensional,
            build_zone_assessment_explanation,
        )
        from services.smc_liquidity_service import build_liquidity_map, update_liquidity_states

        ref_candles = c_15m if c_15m else (c_5m if c_5m else c_1m)
        ref_tf = "15M" if c_15m else ("5M" if c_5m else "1M")
        shs, sls = identify_confirmed_swings(ref_candles, timeframe=ref_tf, left_bars=2, right_bars=2, symbol=symbol, as_of_ms=t_recv) if ref_candles else ([], [])
        liq_levels = build_liquidity_map(
            symbol=symbol,
            timeframe=ref_tf,
            swing_highs=shs,
            swing_lows=sls,
            now_ms=t_recv,
        )
        if c_1m:
            update_liquidity_states(liq_levels, c_1m, t_recv, candle_duration_ms=60_000)

        dealing_range = compute_dealing_range(shs + sls, t_recv, symbol=symbol)
        regime = classify_market_regime(ref_candles if ref_candles else c_1m, atr_15m, t_recv)

        policy = TradingPolicyService.get_or_create_policy(db)
        session_ctx = resolve_entry_session_context(policy, "STANDARD_SMC", now_ms=t_recv, clock=clock)

        # Multi-dimensional ranking of active zones (SECTIONS 081, 097, 098)
        scored_pairs = rank_zones_multi_dimensional(
            current_zones, liq_levels, dealing_range, current_px, atr_15m, htf_bias=bias, now_ms=t_recv
        )
        ranked_zones = [p[0] for p in scored_pairs]
        rank_scores_by_id = {p[0].zone_id: p[1] for p in scored_pairs}

        bounded_zones = ZoneWatchlistService.prune_and_bound_watchlist(ranked_zones, current_px, t_recv, cfg)
        self.set_active_zones(symbol, bounded_zones)

        hypotheses = build_liquidity_hypotheses(
            bounded_zones, liq_levels, dealing_range, current_px, regime, t_recv,
            session_cap_ms=session_ctx.expiry_cap_ms if session_ctx else None,
        )

        hyp_by_zone = {h.watched_zone.zone_id: h for h in hypotheses if getattr(h, "watched_zone", None)}
        assessments_map: Dict[str, Any] = {}
        for z in bounded_zones:
            r_score = rank_scores_by_id.get(z.zone_id)
            if r_score:
                h = hyp_by_zone.get(z.zone_id)
                dto = build_zone_assessment_explanation(
                    z, r_score, h, current_price=current_px, atr=atr_15m, as_of_ms=t_recv, is_stale=False
                )
                assessments_map[z.zone_id] = dto

        with self._lock:
            self._active_zone_assessments[symbol] = assessments_map
            self._active_market_context[symbol] = {
                "current_price": current_px,
                "atr": atr_15m,
                "as_of_ms": t_recv,
                "regime": regime,
            }
            self._active_hypotheses[symbol] = hypotheses
            self._active_liquidity_levels[symbol] = liq_levels
            self._active_dealing_range[symbol] = dealing_range
            self._active_regime[symbol] = regime

        # 4. Model evaluations
        candidates: List[ScalpCandidate] = []
        g_settings = risk_settings_service.get_settings(db)
        audit = crud.get_or_create_today_audit(db, clock=clock)

        policy_dict = {
            "capital_usdt": float(audit.current_equity if audit else 1000.0),
            "risk_pct": float(g_settings.risk_pct),
            "leverage": int(g_settings.requested_leverage),
            "margin_mode": str(g_settings.margin_mode),
            "max_daily_trades": getattr(policy, "max_daily_fills", 3) if policy else 3,
        }

        # A. SMC M1 Reversal Model on watched zones
        if scalp_mode != ScalpMode.OFF.value and c_1m:
            for z in bounded_zones:
                if z.state in (ZoneState.NEAR.value, ZoneState.TOUCHED.value, ZoneState.WAITING_TRIGGER.value):
                    cand = ScalpM1ModelService.evaluate_m1_reversal_candidate(
                        z, c_1m, q, policy_dict, now_ms=t_recv, config=cfg, context_levels=liq_levels
                    )
                    if cand.is_eligible:
                        candidates.append(cand)
                    else:
                        OpportunityFunnelService.record_opportunity_transition(
                            db,
                            OpportunityEvaluation(
                                opportunity_id=f"opp_{z.zone_id}",
                                symbol=symbol,
                                model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
                                direction=z.direction,
                                zone_id=z.zone_id,
                                as_of_ms=t_recv,
                                stage=OpportunityStage.TRIGGER_PENDING.value,
                                reason_codes=tuple([cand.reason_code] if cand.reason_code else ()),
                                market_event_received_at=t_recv,
                            ),
                            now_ms=t_recv,
                        )

            # B. SMC M1 Continuation Model
            cand_cont = ScalpM1ModelService.evaluate_m1_continuation_candidate(
                c_1m, c_5m, q, bias, align, policy_dict, now_ms=t_recv, config=cfg
            )
            if cand_cont.is_eligible:
                candidates.append(cand_cont)

        # C. Daily NY Research Model
        if cfg.daily_ny_research_enabled:
            today_fills = audit.fills_count if audit else 0
            ny_session_ctx = resolve_entry_session_context(policy, "NY_DAILY_RESEARCH", now_ms=t_recv, clock=clock)
            is_ny = bool(ny_session_ctx.in_ny_entry_window)
            cand_res = DailyNYResearchService.evaluate_daily_research_candidate(
                c_5m, q, bias, align, policy_dict, today_fills, now_ms=t_recv, is_ny_session=is_ny, config=cfg
            )
            if cand_res.get("is_eligible"):
                calc = cand_res.get("calculation")
                candidates.append(ScalpCandidate(
                    is_eligible=True,
                    model_id=ModelIdentifier.NY_DAILY_RESEARCH.value,
                    direction=cand_res["direction"],
                    candidate_key=cand_res["candidate_key"],
                    symbol=symbol,
                    planned_entry=cand_res["planned_entry"],
                    stop_loss=cand_res["stop_loss"],
                    take_profit=cand_res["take_profit"],
                    gross_rr=cand_res["gross_rr"],
                    net_rr=cand_res["net_rr"],
                    calculation=calc,
                    evidence=cand_res["evidence"],
                    trigger_chain=cand_res["trigger_chain"],
                    snapshot=cand_res["snapshot"],
                    as_of_ms=t_recv,
                ))

        # 5. Execution or Shadow Handling
        t_eval_done = int(time.time() * 1000)
        armed_order = None

        effective_scalp_mode = scalp_mode
        # Global auto stop policy check: if stopped, downgrade PAPER to SHADOW (PHẦN 024)
        if not global_auto and effective_scalp_mode == ScalpMode.PAPER.value:
            effective_scalp_mode = ScalpMode.SHADOW.value

        for cand in candidates:
            opp_eval = OpportunityEvaluation(
                opportunity_id=f"opp_{cand.candidate_key}",
                symbol=symbol,
                model_id=cand.model_id,
                direction=cand.direction,
                zone_id=cand.zone_id,
                as_of_ms=t_recv,
                stage=OpportunityStage.TRIGGER_CONFIRMED.value,
                market_event_received_at=t_recv,
                evaluation_started_at=t_start,
                evaluation_completed_at=t_eval_done,
                trigger_known_at=t_recv,
                planned_entry=cand.planned_entry,
                stop_loss=cand.stop_loss,
                take_profit=cand.take_profit,
                net_rr=cand.net_rr,
            )
            OpportunityFunnelService.record_opportunity_transition(db, opp_eval, now_ms=t_recv)

            if effective_scalp_mode == ScalpMode.SHADOW.value:
                # In SHADOW mode: record hypothetical plan, do NOT arm or fill order (PHẦN 024, 079)
                logger.info(f"[SHADOW] Qualified {cand.model_id} candidate: {cand.direction} @ {cand.planned_entry}")
                opp_eval = OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=t_recv,
                    stage=OpportunityStage.PLAN_VALID.value,
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                )
                OpportunityFunnelService.record_opportunity_transition(db, opp_eval, now_ms=t_recv)

            elif effective_scalp_mode == ScalpMode.PAPER.value or (cand.model_id == ModelIdentifier.NY_DAILY_RESEARCH.value and cfg.daily_ny_research_enabled and global_auto):
                # In PAPER mode: validate through canonical entry guards (PHẦN 025 - 033, 059)
                armed_order = self._arm_paper_candidate(
                    db, cand, now_ms=t_recv, clock=clock, quote=q, policy_settings=policy_dict, config=cfg, feed_health=feed_health, auto_enabled=auto_enabled
                )
                if armed_order:
                    break

        try:
            db.commit()
        except Exception as e:
            logger.warning(f"Error committing scalp watcher cycle: {e}")
            db.rollback()
            armed_order = None

        t_cycle_done = int(time.time() * 1000)

        return {
            "status": "OK",
            "as_of_ms": t_recv,
            "zones_count": len(bounded_zones),
            "candidates_count": len(candidates),
            "armed_order_id": armed_order.id if armed_order else None,
            "evaluation_duration_ms": max(0, t_cycle_done - t_start),
            "hypotheses_count": len(hypotheses),
            "liquidity_levels_count": len(liq_levels),
            "market_regime": regime,
        }

    @serialize_entry_mutation
    def _arm_paper_candidate(
        self,
        db: Session,
        cand: ScalpCandidate,
        *,
        now_ms: int,
        clock: Any,
        quote: Any,
        policy_settings: Dict[str, Any],
        config: Optional[M1ScalpConfig] = None,
        feed_health: Optional[Dict[str, Any]] = None,
        auto_enabled: Optional[bool] = None,
    ) -> Optional[models.PaperOrder]:
        """
        Guarded ARM path under entry_mutation_lock (PHẦN 025 - 033, 059).
        Never bypasses entry guards or daily quotas.
        """
        cfg = config if config is not None else load_effective_scalp_config(db)
        snap = cand.snapshot
        calc = cand.calculation

        # Validate calculation output (PHẦN 026, 037)
        if calc is None or not getattr(calc, "is_valid", False) or not getattr(calc, "can_execute", False):
            logger.info(f"Candidate calculation is invalid or cannot execute: {getattr(calc, 'skip_reason', None) or getattr(calc, 'invalid_reason', None)}")
            return None

        qty = float(getattr(calc, "quantity", 0.0))
        if qty <= 0 or not math.isfinite(qty):
            logger.info(f"Invalid calculator quantity: {qty}")
            return None

        # 0. Critical section auto check (PHẦN 024, 025, 026)
        if auto_enabled is not None:
            is_auto = bool(auto_enabled)
        else:
            try:
                from services.strategy_service import strategy_service
                is_auto = strategy_service.get_auto_state(db)
            except Exception as e:
                logger.warning(f"Error checking auto state in arm critical section: {e}")
                is_auto = False

        if not is_auto:
            logger.info(f"Auto trading disabled or unreadable at arm time, refusing to arm candidate {cand.candidate_key}")
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=("GLOBAL_AUTO_DISABLED",),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                ),
                now_ms=now_ms,
            )
            return None

        # 1. Idempotency Check: prevent duplicate orders for the same signal (PHẦN 058)
        idempotency_key = f"scalp_arm_{cand.candidate_key}"
        existing_order = db.query(models.PaperOrder).filter(models.PaperOrder.idempotency_key == idempotency_key).first()
        if existing_order:
            logger.info(f"Idempotent hit: PaperOrder {existing_order.id} already armed for {cand.candidate_key}")
            return existing_order

        # 2. Immutable WatchSetup instance (PHẦN 031)
        ws_id = f"watch-{cand.symbol}-{cand.model_id}-{cand.candidate_key}"
        ws = db.query(models.WatchSetup).filter(models.WatchSetup.id == ws_id).first()
        if ws is None:
            ws = models.WatchSetup(
                id=ws_id,
                setup_instance_id=cand.candidate_key,
                direction=cand.direction,
                timeframe=snap.setup_timeframe if snap else "1M",
                state="READY",
                provisional_entry=cand.planned_entry,
                provisional_sl=cand.stop_loss,
                provisional_tp=cand.take_profit,
                invalidation_price=cand.stop_loss,
                strategy_family=cand.model_id,
                created_at=now_ms,
                updated_at=now_ms,
            )
            db.add(ws)
        else:
            ws.direction = cand.direction
            ws.state = "READY"
            ws.provisional_entry = cand.planned_entry
            ws.provisional_sl = cand.stop_loss
            ws.provisional_tp = cand.take_profit
            ws.invalidation_price = cand.stop_loss
            ws.strategy_family = cand.model_id
            ws.updated_at = now_ms
        db.flush()

        # 3. Save immutable scenario snapshot for chart evidence linking (PHẦN 022, 023, 069, 070)
        scenario_snap_id = None
        if snap is not None:
            from services.smc_scenario_repository import save_immutable_scenario_snapshot
            try:
                snap_row = save_immutable_scenario_snapshot(db, snap)
                scenario_snap_id = snap_row.snapshot_id
            except Exception as e:
                logger.warning(f"Error persisting scenario snapshot: {e}")
                err_code = "SNAPSHOT_IMMUTABILITY_CONFLICT" if "IMMUTABILITY_CONFLICT" in str(e).upper() else "SNAPSHOT_PERSISTENCE_FAILED"
                OpportunityFunnelService.record_opportunity_transition(
                    db,
                    OpportunityEvaluation(
                        opportunity_id=f"opp_{cand.candidate_key}",
                        symbol=cand.symbol,
                        model_id=cand.model_id,
                        direction=cand.direction,
                        zone_id=cand.zone_id,
                        as_of_ms=now_ms,
                        stage=OpportunityStage.GUARD_BLOCKED.value,
                        reason_codes=(err_code,),
                        planned_entry=cand.planned_entry,
                        stop_loss=cand.stop_loss,
                        take_profit=cand.take_profit,
                        net_rr=cand.net_rr,
                    ),
                    now_ms=now_ms,
                )
                return None

        # 4. Canonical Entry Guard evaluation (PHẦN 004, 034, 035)
        from services.entry_guard_service import read_execution_feed_health
        actual_feed_health = feed_health if feed_health is not None else read_execution_feed_health()

        req = EntryGuardRequest(
            stage="ARM",
            now_ms=now_ms,
            symbol=cand.symbol,
            direction=cand.direction,
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            setup_id=ws.id,
            setup_instance_id=cand.candidate_key,
            setup_state=ws.state,
            timeframe=snap.setup_timeframe if snap else "1M",
            strategy_family=cand.model_id,
            planned_entry=cand.planned_entry,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            calc_result=calc,
            validated_quote=quote,
            feed_health=actual_feed_health,
            min_net_rr=cfg.min_net_rr,
            clock=clock,
        )

        decision = evaluate_entry_guard(db, req)
        if not decision.allowed:
            logger.info(f"Guard blocked {cand.model_id} candidate: {decision.primary_reason if hasattr(decision, 'primary_reason') else decision.reason_codes}")
            OpportunityFunnelService.record_opportunity_transition(
                db,
                OpportunityEvaluation(
                    opportunity_id=f"opp_{cand.candidate_key}",
                    symbol=cand.symbol,
                    model_id=cand.model_id,
                    direction=cand.direction,
                    zone_id=cand.zone_id,
                    as_of_ms=now_ms,
                    stage=OpportunityStage.GUARD_BLOCKED.value,
                    reason_codes=tuple(decision.reason_codes),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                ),
                now_ms=now_ms,
            )
            return None

        # 5. Guard passed: Create PaperOrder with canonical sizing and schema fields (PHẦN 025 - 030)
        from services.order_plan_snapshot_service import build_arm_plan_snapshot
        arm_snap = build_arm_plan_snapshot(
            calc,
            order_type="MARKET",
            direction=cand.direction,
            planned_entry=cand.planned_entry,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            capital_usdt=policy_settings.get("capital_usdt", 1000.0),
            risk_pct=float(getattr(calc, "effective_risk_pct", cfg.fallback_risk_pct)),
            leverage=calc.leverage,
            margin_mode=calc.margin_mode,
            now_ms=now_ms,
            policy_config_version=cfg.config_version,
            risk_config_version=cfg.config_version,
        )

        order_id = f"ord_{cand.model_id}_{cand.candidate_key}"
        order = models.PaperOrder(
            id=order_id,
            setup_id=ws.id,
            setup_instance_id=cand.candidate_key,
            idempotency_key=idempotency_key,
            signal_id=f"sig-{cand.candidate_key}",
            instrument=cand.symbol,
            direction=cand.direction,
            state="armed",
            order_type="MARKET",
            timeframe=snap.setup_timeframe if snap else "1M",
            planned_entry=cand.planned_entry,
            actual_entry=None,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            quantity=qty,
            initial_risk_usdt=float(calc.net_risk_usdt),
            risk_pct=float(getattr(calc, "effective_risk_pct", cfg.fallback_risk_pct)),
            gross_rr=float(calc.gross_rr),
            estimated_net_rr=float(calc.net_rr),
            leverage=calc.leverage,
            margin_mode=calc.margin_mode,
            estimated_liquidation=calc.estimated_liquidation,
            opened_at=None,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=now_ms + (2 * 3600 * 1000),
            origin="AUTO_STRATEGY",
            execution_mode="AUTO",
            strategy_family=cand.model_id,
            strategy_version="15.0",
            scenario_snapshot_id=scenario_snap_id,
            arm_decision_snapshot=json.dumps(decision.to_dict() if hasattr(decision, 'to_dict') else {}, ensure_ascii=False),
            cost_snapshot=json.dumps(arm_snap, ensure_ascii=False),
        )
        db.add(order)
        ws.state = "ARMED"
        ws.confirmed_entry = cand.planned_entry
        ws.confirmed_sl = cand.stop_loss
        ws.confirmed_tp = cand.take_profit
        ws.net_rr = calc.net_rr
        ws.gross_rr = calc.gross_rr
        ws.quantity = qty
        ws.risk_usdt = calc.net_risk_usdt
        db.flush()

        OpportunityFunnelService.record_opportunity_transition(
            db,
            OpportunityEvaluation(
                opportunity_id=f"opp_{cand.candidate_key}",
                symbol=cand.symbol,
                model_id=cand.model_id,
                direction=cand.direction,
                zone_id=cand.zone_id,
                as_of_ms=now_ms,
                stage=OpportunityStage.ARMED.value,
                armed_at=now_ms,
                order_id=order.id,
                watch_setup_id=ws.id,
                planned_entry=cand.planned_entry,
                stop_loss=cand.stop_loss,
                take_profit=cand.take_profit,
                net_rr=cand.net_rr,
            ),
            now_ms=now_ms,
        )

        # 6. Publish order.armed domain event atomically within caller's db transaction (PHẦN 019, 020, 021, 138)
        # EventBus handles outbox notification deduplicated under the same transaction.
        from services.event_bus import event_bus
        event_bus.publish_event(
            event_type="order.armed",
            aggregate_id=order.id,
            payload={
                "order_id": order.id,
                "setup_id": ws.id,
                "direction": cand.direction,
                "order_type": "MARKET",
                "planned_entry": cand.planned_entry,
                "stop_loss": cand.stop_loss,
                "take_profit": cand.take_profit,
                "net_rr": calc.net_rr,
            },
            db=db,
            occurred_at=now_ms,
        )

        return order
    async def run_watcher_loop(self):
        """Periodic background evaluation loop for autonomous scalp watcher."""
        self._running = True
        while self._running:
            try:
                await asyncio.sleep(2.0)
                def _eval():
                    db = SessionLocal()
                    try:
                        self.evaluate_cycle(db, "XAUUSDT")
                    finally:
                        db.close()
                await asyncio.to_thread(_eval)
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error(f"Scalp watcher loop error: {e}", exc_info=True)
                await asyncio.sleep(2.0)

    def stop(self):
        self._running = False


scalp_watcher_orchestrator = ScalpWatcherOrchestrator()
