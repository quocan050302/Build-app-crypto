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
from services.quote_validator import QuoteValidator, ValidatedQuote, normalize_execution_quote
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
        self._latest_hypothesis_batch_result: Dict[str, Any] = {}
        self._latest_hypothesis_authority: Dict[str, Any] = {}
        self._latest_cycle_outcome: Dict[str, Dict[str, Any]] = {}
        self._arm_rejections: Dict[str, Any] = {}

    def get_latest_hypothesis_batch_result(self, symbol: str = "XAUUSDT") -> Optional[Any]:
        with self._lock:
            return self._latest_hypothesis_batch_result.get(symbol)

    def get_latest_hypothesis_authority(self, symbol: str = "XAUUSDT") -> Optional[Any]:
        with self._lock:
            return self._latest_hypothesis_authority.get(symbol)

    def get_latest_cycle_outcome(self, symbol: str = "XAUUSDT") -> Optional[Dict[str, Any]]:
        with self._lock:
            outcome = self._latest_cycle_outcome.get(symbol)
            return dict(outcome) if outcome else None

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
        candles_1h: Optional[Sequence[Any]] = None,
        candles_4h: Optional[Sequence[Any]] = None,
        htf_bias: Optional[str] = None,
        h1_alignment: Optional[str] = None,
        config: Optional[M1ScalpConfig] = None,
        auto_enabled: Optional[bool] = None,
        feed_health: Optional[Dict[str, Any]] = None,
        execution_financial_context: Optional[Any] = None,
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

        # Validate quote & price using canonical execution quote normalizer (V15.8 PROMPT 086-096, 116-119)
        if quote is not None:
            q = normalize_execution_quote(quote, now_ms=t_recv)
        else:
            raw_t = ticker if ticker is not None else collector_service.latest_ticker
            q = normalize_execution_quote(raw_t, now_ms=t_recv) if raw_t else None

        bid = float(getattr(q, "bid", 0.0) if hasattr(q, "bid") else (q or {}).get("bid", 0.0))
        ask = float(getattr(q, "ask", 0.0) if hasattr(q, "ask") else (q or {}).get("ask", 0.0))
        import math
        is_q_valid = (
            q is not None
            and bool(getattr(q, "is_valid", False))
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
        c_4h = candles_4h if candles_4h is not None else crud.get_candles(db, symbol, "4H", limit=40, ascending=True, cutoff_ms=t_recv)
        c_1h = candles_1h if candles_1h is not None else crud.get_candles(db, symbol, "1H", limit=60, ascending=True, cutoff_ms=t_recv)
        c_15m = candles_15m if candles_15m is not None else crud.get_candles(db, symbol, "15M", limit=120, ascending=True, cutoff_ms=t_recv)
        c_5m = candles_5m if candles_5m is not None else crud.get_candles(db, symbol, "5M", limit=150, ascending=True, cutoff_ms=t_recv)
        c_1m = candles_1m if candles_1m is not None else crud.get_candles(db, symbol, "1M", limit=100, ascending=True, cutoff_ms=t_recv)

        from services.cg01_context_adapter import normalize_cg01_candle
        if c_4h and isinstance(c_4h[0], dict):
            c_4h = [normalize_cg01_candle(b, "4H", symbol) for b in c_4h]
        if c_1h and isinstance(c_1h[0], dict):
            c_1h = [normalize_cg01_candle(b, "1H", symbol) for b in c_1h]
        if c_15m and isinstance(c_15m[0], dict):
            c_15m = [normalize_cg01_candle(b, "15M", symbol) for b in c_15m]
        if c_5m and isinstance(c_5m[0], dict):
            c_5m = [normalize_cg01_candle(b, "5M", symbol) for b in c_5m]
        if c_1m and isinstance(c_1m[0], dict):
            c_1m = [normalize_cg01_candle(b, "1M", symbol) for b in c_1m]

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

            is_touching = (new_state == ZoneState.TOUCHED.value)
            new_episode = ZoneWatchlistService.track_zone_touch_episode(z, bar_id_or_ts=t_recv, is_touching=is_touching)
            if new_episode and prev_state != ZoneState.TOUCHED.value:
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
            load_durable_hypotheses_for_symbol,
            stage_hypothesis_lifecycle,
            stage_hypothesis_status_transition,
            get_hypothesis_storage_key,
            LifecycleStageResult,
            HypothesisLoadStatus,
            HypothesisBatchLoadResult,
            is_lifecycle_stage_success,
            HypothesisWriteOutcome,
            LifecycleCycleAuthority,
            derive_cycle_status,
            check_candidate_lifecycle_authority,
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

        # Load durable hypothesis lifecycle from DB (SECTIONS 042, 046, 061 - 087)
        batch_result = load_durable_hypotheses_for_symbol(db, symbol)
        durable_hyps_map = batch_result.records
        blocked_keys = set(batch_result.blocked_keys)
        blocked_ids = set(batch_result.blocked_identities)

        # Clean in-memory cache if any cached hypothesis is corrupted in DB (SECTION 096)
        existing_hyps = [
            h for h in self._active_hypotheses.get(symbol, [])
            if h.hypothesis_id not in blocked_ids
            and get_hypothesis_storage_key(symbol, h.hypothesis_id) not in blocked_keys
        ]
        existing_ids = {h.hypothesis_id for h in existing_hyps}
        for d_id, d_rec in durable_hyps_map.items():
            if d_id not in existing_ids:
                existing_hyps.append(d_rec)

        use_multi_zone = bool(
            getattr(cfg, "v16_profile_enabled", True)
            and getattr(cfg, "multi_zone_hypotheses_enabled", False)
        )
        if use_multi_zone:
            from services.liquidity_scenario_service import build_execution_hypotheses_for_zones
            unfiltered_hyps = build_execution_hypotheses_for_zones(
                bounded_zones, liq_levels, dealing_range, current_px, regime, t_recv,
                session_cap_ms=session_ctx.expiry_cap_ms if session_ctx else None,
                existing_hypotheses=existing_hyps,
            )
        else:
            unfiltered_hyps = build_liquidity_hypotheses(
                bounded_zones, liq_levels, dealing_range, current_px, regime, t_recv,
                session_cap_ms=session_ctx.expiry_cap_ms if session_ctx else None,
                existing_hypotheses=existing_hyps,
            )

        hypotheses: List[Any] = []
        blocked_zone_issues: Dict[str, Any] = {}
        for h in unfiltered_hyps:
            h_key = get_hypothesis_storage_key(symbol, h.hypothesis_id)
            if h.hypothesis_id in blocked_ids or h_key in blocked_keys:
                matching_issue = next(
                    (iss for iss in batch_result.issues if iss.storage_key == h_key or iss.hypothesis_id == h.hypothesis_id),
                    None
                )
                if getattr(h, "watched_zone", None):
                    blocked_zone_issues[h.watched_zone.zone_id] = matching_issue
                continue
            hypotheses.append(h)

        # Stage durable hypothesis records in same cycle transaction (SECTIONS 040, 043, 061 - 065)
        staged_ids = set()
        entry_authorized_hypotheses: List[Any] = []
        cycle_authority = LifecycleCycleAuthority()

        for h in hypotheses:
            h_key = get_hypothesis_storage_key(symbol, h.hypothesis_id)
            z_id = h.watched_zone.zone_id if getattr(h, "watched_zone", None) else None
            try:
                res = stage_hypothesis_lifecycle(db, symbol, h, now_ms=t_recv)
            except Exception as e:
                logger.warning(f"Exception during stage_hypothesis_lifecycle for {h.hypothesis_id}: {e}")
                res = LifecycleStageResult.FAILED

            success = is_lifecycle_stage_success(res)
            rcode = None
            if not success:
                if res == LifecycleStageResult.BLOCKED:
                    rcode = OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.value
                    cycle_authority.has_write_blocks = True
                else:
                    rcode = OpportunityReasonCode.HYPOTHESIS_WRITE_FAILED.value
                    cycle_authority.has_write_failures = True
                cycle_authority.write_issues_count += 1
            else:
                staged_ids.add(h.hypothesis_id)
                entry_authorized_hypotheses.append(h)

            outcome = HypothesisWriteOutcome(
                hypothesis_id=h.hypothesis_id,
                storage_key=h_key,
                zone_id=z_id,
                operation="UPSERT_HYPOTHESIS",
                result=res if isinstance(res, LifecycleStageResult) else LifecycleStageResult.FAILED,
                is_success=success,
                reason_code=rcode,
            )
            cycle_authority.write_outcomes[h.hypothesis_id] = outcome
            if z_id:
                cycle_authority.zone_outcomes[z_id] = outcome

            # SECTION 046: Fatal persistence failure or poisoned session: stop staging immediately
            if not getattr(db, "is_active", True) or res == LifecycleStageResult.FAILED:
                cycle_authority.has_write_failures = True
                cycle_authority.write_status = "FAILED"
                break

        if cycle_authority.has_write_failures or not getattr(db, "is_active", True):
            return self._finalize_failed_cycle(
                db, symbol,
                t_recv=t_recv,
                t_start=t_start,
                batch_result=batch_result,
                cycle_authority=cycle_authority,
                bounded_zones_count=len(bounded_zones),
                hypotheses_count=len(hypotheses),
                regime=regime,
                liq_levels_count=len(liq_levels),
            )

        # Ensure any durable hypothesis past its expiry deadline transitions to EXPIRED (SECTIONS 116 - 120)
        for d_id, d_rec in durable_hyps_map.items():
            if d_id not in staged_ids and t_recv >= d_rec.expiry_ms and d_rec.status == "ACTIVE":
                try:
                    exp_res = stage_hypothesis_status_transition(db, symbol, d_rec, desired_status="EXPIRED", now_ms=t_recv)
                except Exception as e:
                    logger.warning(f"Exception during stage_hypothesis_status_transition for {d_id}: {e}")
                    exp_res = LifecycleStageResult.FAILED

                exp_success = is_lifecycle_stage_success(exp_res)
                exp_key = get_hypothesis_storage_key(symbol, d_id)
                exp_rcode = None
                if not exp_success:
                    if exp_res == LifecycleStageResult.BLOCKED:
                        exp_rcode = OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.value
                        cycle_authority.has_write_blocks = True
                    else:
                        exp_rcode = OpportunityReasonCode.HYPOTHESIS_WRITE_FAILED.value
                        cycle_authority.has_write_failures = True
                    cycle_authority.write_issues_count += 1

                exp_outcome = HypothesisWriteOutcome(
                    hypothesis_id=d_id,
                    storage_key=exp_key,
                    zone_id=d_rec.source_zone_id,
                    operation="EXPIRE_DURABLE",
                    result=exp_res if isinstance(exp_res, LifecycleStageResult) else LifecycleStageResult.FAILED,
                    is_success=exp_success,
                    reason_code=exp_rcode,
                )
                cycle_authority.write_outcomes[f"{d_id}:EXPIRE"] = exp_outcome
                if not getattr(db, "is_active", True) or exp_res == LifecycleStageResult.FAILED:
                    cycle_authority.has_write_failures = True
                    cycle_authority.write_status = "FAILED"
                    break

        if cycle_authority.has_write_failures or not getattr(db, "is_active", True):
            return self._finalize_failed_cycle(
                db, symbol,
                t_recv=t_recv,
                t_start=t_start,
                batch_result=batch_result,
                cycle_authority=cycle_authority,
                bounded_zones_count=len(bounded_zones),
                hypotheses_count=len(hypotheses),
                regime=regime,
                liq_levels_count=len(liq_levels),
            )

        # Compute cycle write_status
        if cycle_authority.has_write_failures:
            cycle_authority.write_status = "FAILED"
        elif cycle_authority.has_write_blocks:
            cycle_authority.write_status = "BLOCKED"
        else:
            cycle_authority.write_status = "OK"

        cycle_authority.entry_authorized_hypotheses = entry_authorized_hypotheses

        display_hypotheses_by_zone = {h.watched_zone.zone_id: h for h in hypotheses if getattr(h, "watched_zone", None)}
        entry_hypotheses_by_zone = {h.watched_zone.zone_id: h for h in entry_authorized_hypotheses if getattr(h, "watched_zone", None)}
        assessments_map: Dict[str, Any] = {}
        for z in bounded_zones:
            r_score = rank_scores_by_id.get(z.zone_id)
            if r_score:
                h = display_hypotheses_by_zone.get(z.zone_id)
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
            self._active_liquidity_levels[symbol] = liq_levels
            self._active_dealing_range[symbol] = dealing_range
            self._active_regime[symbol] = regime

        # 4. Model evaluations
        candidates: List[ScalpCandidate] = []
        g_settings = risk_settings_service.get_settings(db)
        audit = crud.get_or_create_today_audit(db, clock=clock)

        # V16.7 Group D: Canonical Risk Authority
        # g_settings.risk_pct is the default authority for non-replay callers.
        # Fallback is only used if g_settings.risk_pct is missing.
        # Explicit execution_financial_context overrides for replay.
        from services.plan_financial_context_service import resolve_execution_risk
        explicit_replay_risk = getattr(execution_financial_context, "risk_pct", None) if execution_financial_context else None
        account_risk = float(g_settings.risk_pct) if (g_settings and g_settings.risk_pct is not None) else float(getattr(cfg, "fallback_risk_pct", 0.10))
        risk_resolution = resolve_execution_risk(
            account_risk_pct=account_risk,
            explicit_replay_risk_pct=explicit_replay_risk,
        )
        resolved_risk_pct = float(risk_resolution["effective_risk_pct"])

        capital_val = float(getattr(execution_financial_context, "capital_usdt", None) or (audit.current_equity if audit else 1000.0))

        policy_dict = {
            "capital_usdt": capital_val,
            "risk_pct": resolved_risk_pct,
            "leverage": int(g_settings.requested_leverage),
            "margin_mode": str(g_settings.margin_mode),
            "max_daily_trades": getattr(policy, "max_daily_fills", 3) if policy else 3,
            "risk_source": risk_resolution["source"],
        }

        # A. SMC M1 Reversal Model on watched zones
        if scalp_mode != ScalpMode.OFF.value and c_1m:
            for z in bounded_zones:
                if z.state in (ZoneState.NEAR.value, ZoneState.TOUCHED.value, ZoneState.WAITING_TRIGGER.value):
                    # Check if zone's hypothesis read authority failed (SECTIONS 095 - 098)
                    if z.zone_id in blocked_zone_issues or batch_result.read_status == "READ_FAILED":
                        issue = blocked_zone_issues.get(z.zone_id)
                        if batch_result.read_status == "READ_FAILED":
                            rcode = OpportunityReasonCode.HYPOTHESIS_STORE_UNAVAILABLE.value
                        elif issue and issue.status == HypothesisLoadStatus.UNSUPPORTED_VERSION:
                            rcode = OpportunityReasonCode.HYPOTHESIS_SCHEMA_UNSUPPORTED.value
                        else:
                            rcode = OpportunityReasonCode.HYPOTHESIS_STATE_CORRUPT.value

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
                                reason_codes=(rcode,),
                                market_event_received_at=t_recv,
                                details={"message": "Dữ liệu theo dõi kịch bản bị lỗi, bot đang tạm chặn lệnh liên quan."},
                            ),
                            now_ms=t_recv,
                        )
                        continue

                    # Check write authority (V15.7 SECTIONS 061 - 090)
                    if cycle_authority.has_write_failures:
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
                                reason_codes=(OpportunityReasonCode.HYPOTHESIS_WRITE_FAILED.value,),
                                market_event_received_at=t_recv,
                                details={"message": "Chưa thể chuẩn bị lệnh vì bot chưa lưu được trạng thái kịch bản. Bot đang giữ nguyên các lệnh hiện có và sẽ đánh giá lại ở chu kỳ tiếp theo."},
                            ),
                            now_ms=t_recv,
                        )
                        continue

                    z_outcome = cycle_authority.zone_outcomes.get(z.zone_id)
                    if z_outcome is not None and not z_outcome.is_success:
                        rcode = z_outcome.reason_code or OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.value
                        msg = "Kịch bản này đang bị chặn vì trạng thái lưu không cho phép cập nhật." if rcode == OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.value else "Chưa thể chuẩn bị lệnh vì bot chưa lưu được trạng thái kịch bản. Bot đang giữ nguyên các lệnh hiện có và sẽ đánh giá lại ở chu kỳ tiếp theo."
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
                                reason_codes=(rcode,),
                                market_event_received_at=t_recv,
                                details={"message": msg},
                            ),
                            now_ms=t_recv,
                        )
                        continue

                    zone_hyp = entry_hypotheses_by_zone.get(z.zone_id)
                    if zone_hyp is None:
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
                                reason_codes=(OpportunityReasonCode.HYPOTHESIS_AUTHORITY_MISSING.value,),
                                market_event_received_at=t_recv,
                                details={"message": "Thiếu dữ liệu xác thực kịch bản, tạm chặn vào lệnh."},
                            ),
                            now_ms=t_recv,
                        )
                        continue

                    if zone_hyp.status == "EXPIRED" or t_recv >= getattr(zone_hyp, "expiry_ms", float("inf")):
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
                                reason_codes=(OpportunityReasonCode.HYPOTHESIS_EXPIRED.value,),
                                market_event_received_at=t_recv,
                            ),
                            now_ms=t_recv,
                        )
                        continue

                    cand = ScalpM1ModelService.evaluate_m1_reversal_candidate(
                        z, c_1m, q, policy_dict, now_ms=t_recv, config=cfg, context_levels=liq_levels
                    )
                    if cand.is_eligible:
                        cand.hypothesis_id = zone_hyp.hypothesis_id
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

            has_valid_zone_authority = (not bounded_zones) or any(
                getattr(h, "status", "") == "ACTIVE" and t_recv < getattr(h, "expiry_ms", float("inf"))
                for h in entry_authorized_hypotheses
            )

            # B. SMC M1 Continuation Model (SECTIONS 080 - 082) - DIRECT authority
            if not cycle_authority.has_write_failures:
                cand_cont = ScalpM1ModelService.evaluate_m1_continuation_candidate(
                    c_1m, c_5m, q, bias, align, policy_dict, now_ms=t_recv, config=cfg
                )
                if cand_cont.is_eligible:
                    candidates.append(cand_cont)

            # B2. SMC M1 FVG Continuation Model (Rules 046 - 060) - DIRECT authority, V16 profile gated
            if (
                getattr(cfg, "v16_profile_enabled", True)
                and getattr(cfg, "fvg_continuation_enabled", True)
                and not cycle_authority.has_write_failures
            ):
                cand_fvg_cont = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
                    closed_1m=c_1m, closed_5m=c_5m, quote=q, htf_bias=bias, h1_alignment=align,
                    policy_settings=policy_dict, now_ms=t_recv, config=cfg, context_levels=liq_levels
                )
                if cand_fvg_cont.is_eligible:
                    candidates.append(cand_fvg_cont)

            # B3. SMC M1 Sweep Reclaim Model (Rules 061 - 074, 080) - CLUSTER authority, V16 profile gated
            if (
                getattr(cfg, "v16_profile_enabled", True)
                and getattr(cfg, "sweep_reclaim_enabled", True)
                and not cycle_authority.has_write_failures
            ):
                from services.smc_liquidity_service import build_liquidity_clusters, update_cluster_state
                liq_clusters = build_liquidity_clusters(liq_levels, timeframe="15M", symbol=symbol, now_ms=t_recv)
                for cluster in liq_clusters:
                    # Update cluster state in runtime (Rule 080)
                    update_cluster_state(cluster, c_1m, now_ms=t_recv)
                    cycle_authority.cluster_outcomes[cluster.id] = cluster
                    if getattr(cluster, "state", "") in ("SWEPT", "RECLAIMED"):
                        cand_sr = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
                            closed_1m=c_1m, cluster=cluster, quote=q, policy_settings=policy_dict,
                            now_ms=t_recv, config=cfg, context_levels=liq_levels
                        )
                        if cand_sr.is_eligible:
                            candidates.append(cand_sr)

        # C. Daily NY Research Model (SECTIONS 080 - 083)
        if cfg.daily_ny_research_enabled and not cycle_authority.has_write_failures:
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

        # D. CG-01 Paper Profile Model (V16.2 Rules 001 - 035, 121 - 125)
        cg01_mode = getattr(cfg, "cg01_profile_mode", None)
        if cg01_mode is None:
            cg01_mode = "PAPER_EXECUTION" if getattr(cfg, "cg01_enabled", False) else "OFF"

        if cg01_mode != "OFF" and c_1m:
            from services.cg01_engine import evaluate_cg01_candidate
            from services.cg01_types import CG01Config
            from services.trading_policy_service import resolve_effective_daily_policy
            eff_daily = resolve_effective_daily_policy(db, "default")
            cg01_cfg = getattr(cfg, "cg01_config", None)
            if cg01_cfg is None:
                cg01_cfg = CG01Config(
                    enabled=True,
                    profile_mode=cg01_mode,
                    daily_min_goal=eff_daily.min_goal,
                    daily_max_cap=eff_daily.max_fills,
                    daily_ny_goal=eff_daily.ny_goal,
                )
            closed_ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
            costs_to_pass = getattr(cg01_cfg, "costs", None) or getattr(cfg, "costs", None)
            cg01_dec = evaluate_cg01_candidate(
                closed_ctx, q, config=cg01_cfg, now_ms=t_recv,
                capital=policy_dict["capital_usdt"], risk_pct=policy_dict["risk_pct"],
                costs=costs_to_pass,
            )
            if cg01_dec.candidate:
                candidates.append(cg01_dec.candidate)

        # Candidate Router & Deduplication (Rules 093 - 096)
        from services.scalp_m1_model_service import evaluate_smc_opportunity_candidates
        candidates = evaluate_smc_opportunity_candidates(candidates, htf_bias=bias or "UNKNOWN")

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

            exec_profile = getattr(cfg, "execution_profile", "BASELINE")
            is_cg01_model = (cand.model_id == ModelIdentifier.CG01_SWEEP_MSS_FVG.value)

            # Rule 019 & 035, 102, 104: Execution profile arbitration
            should_shadow = (effective_scalp_mode == ScalpMode.SHADOW.value)
            if not should_shadow:
                if is_cg01_model:
                    cg01_pm = getattr(cfg, "cg01_profile_mode", None)
                    if cg01_pm != "PAPER_EXECUTION" or exec_profile != "CG01":
                        should_shadow = True
                else:
                    if exec_profile == "CG01":
                        should_shadow = True

            if should_shadow:
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
                    db, cand, now_ms=t_recv, clock=clock, quote=q, policy_settings=policy_dict, config=cfg, feed_health=feed_health, auto_enabled=auto_enabled, authority_context=cycle_authority
                )
                if armed_order:
                    break

        commit_ok = False
        rollback_ok = False
        try:
            db.commit()
            commit_ok = True
        except Exception as e:
            logger.warning(f"Error committing scalp watcher cycle: {e}")
            try:
                db.rollback()
                rollback_ok = True
            except Exception as rb_e:
                logger.error(f"Error rolling back after commit failure: {rb_e}")
                rollback_ok = False
            armed_order = None

        t_cycle_done = int(time.time() * 1000)

        cycle_status = derive_cycle_status(
            read_status=batch_result.read_status,
            write_status=cycle_authority.write_status,
            commit_ok=commit_ok,
            has_failed_write=cycle_authority.has_write_failures,
            has_blocked_write=cycle_authority.has_write_blocks,
        )

        if commit_ok:
            with self._lock:
                self._active_hypotheses[symbol] = entry_authorized_hypotheses
                self._latest_hypothesis_batch_result[symbol] = batch_result
                self._latest_hypothesis_authority[symbol] = cycle_authority
                self._latest_cycle_outcome[symbol] = {
                    "status": cycle_status,
                    "transaction_status": "COMMITTED",
                    "rollback_status": "NONE",
                    "as_of_ms": t_recv,
                }
        else:
            with self._lock:
                self._active_hypotheses.pop(symbol, None)
                self._latest_hypothesis_batch_result.pop(symbol, None)
                self._latest_hypothesis_authority.pop(symbol, None)
                self._latest_cycle_outcome[symbol] = {
                    "status": "DEGRADED",
                    "transaction_status": "FAILED",
                    "rollback_status": "ROLLED_BACK" if rollback_ok else "ROLLBACK_FAILED",
                    "as_of_ms": t_recv,
                    "write_status": "FAILED",
                    "message": "Bot tạm dừng chuẩn bị lệnh mới vì chưa lưu được dữ liệu. Các lệnh đã có vẫn được giữ nguyên; hệ thống sẽ đánh giá lại khi kết nối dữ liệu hoạt động.",
                }
        try:
            daily_goal_eval = TradingPolicyService.evaluate_daily_frequency_goal(
                db, now_ms=t_recv, clock=clock, daily_min_goal=getattr(cfg, "daily_min_goal", 2)
            )
            daily_goal_dict = daily_goal_eval.to_dict()
        except Exception as dg_e:
            logger.debug(f"Failed to evaluate daily frequency goal: {dg_e}")
            daily_goal_dict = {}

        return {
            "status": cycle_status if commit_ok else "DEGRADED",
            "lifecycle_read_status": batch_result.read_status,
            "lifecycle_write_status": cycle_authority.write_status,
            "lifecycle_write_issues_count": cycle_authority.write_issues_count,
            "transaction_status": "COMMITTED" if commit_ok else "FAILED",
            "rollback_status": "NONE" if commit_ok else ("ROLLED_BACK" if rollback_ok else "ROLLBACK_FAILED"),
            "as_of_ms": t_recv,
            "zones_count": len(bounded_zones),
            "candidates_count": len(candidates) if commit_ok else 0,
            "armed_order_id": armed_order.id if armed_order and commit_ok else None,
            "evaluation_duration_ms": max(0, t_cycle_done - t_start),
            "hypotheses_count": len(hypotheses),
            "entry_authorized_hypotheses_count": len(entry_authorized_hypotheses) if commit_ok else 0,
            "issues_count": len(batch_result.issues),
            "liquidity_levels_count": len(liq_levels),
            "market_regime": regime,
            "daily_goal": daily_goal_dict,
        }

    def _finalize_failed_cycle(
        self,
        db: Session,
        symbol: str,
        *,
        t_recv: int,
        t_start: int,
        batch_result: Any,
        cycle_authority: Any,
        bounded_zones_count: int = 0,
        hypotheses_count: int = 0,
        regime: str = "UNKNOWN",
        liq_levels_count: int = 0,
    ) -> Dict[str, Any]:
        """
        Failure finalization helper for aborted/failed cycle transactions (V15.8 PROMPT 049-056).
        Rolls back the active transaction without making further queries on a poisoned session.
        Clears authoritative cache and publishes runtime diagnostic outcome.
        """
        rollback_ok = False
        try:
            db.rollback()
            rollback_ok = True
        except Exception as rb_err:
            logger.error(f"Error executing rollback during failed cycle finalization: {rb_err}")
            rollback_ok = False

        cycle_authority.write_status = "FAILED"
        cycle_authority.has_write_failures = True

        with self._lock:
            self._active_hypotheses.pop(symbol, None)
            self._latest_hypothesis_batch_result.pop(symbol, None)
            self._latest_hypothesis_authority.pop(symbol, None)
            self._latest_cycle_outcome[symbol] = {
                "status": "DEGRADED",
                "transaction_status": "FAILED",
                "rollback_status": "ROLLED_BACK" if rollback_ok else "ROLLBACK_FAILED",
                "as_of_ms": t_recv,
                "write_status": "FAILED",
                "message": "Bot tạm dừng chuẩn bị lệnh mới vì chưa lưu được dữ liệu. Các lệnh đã có vẫn được giữ nguyên; hệ thống sẽ đánh giá lại khi kết nối dữ liệu hoạt động.",
            }

        t_cycle_done = int(time.time() * 1000)
        return {
            "status": "DEGRADED",
            "lifecycle_read_status": getattr(batch_result, "read_status", "UNKNOWN"),
            "lifecycle_write_status": "FAILED",
            "lifecycle_write_issues_count": getattr(cycle_authority, "write_issues_count", 1),
            "transaction_status": "FAILED",
            "rollback_status": "ROLLED_BACK" if rollback_ok else "ROLLBACK_FAILED",
            "as_of_ms": t_recv,
            "zones_count": bounded_zones_count,
            "candidates_count": 0,
            "armed_order_id": None,
            "evaluation_duration_ms": max(0, t_cycle_done - t_start),
            "hypotheses_count": hypotheses_count,
            "entry_authorized_hypotheses_count": 0,
            "issues_count": len(getattr(batch_result, "issues", [])),
            "liquidity_levels_count": liq_levels_count,
            "market_regime": regime,
            "daily_goal": {},
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
        authority_context: Optional[Any] = None,
        session_context: Optional[Any] = None,
    ) -> Optional[models.PaperOrder]:
        """
        Guarded ARM path under entry_mutation_lock (PHẦN 025 - 033, 059).
        Never bypasses entry guards or daily quotas.
        """
        cfg = config if config is not None else load_effective_scalp_config(db)
        snap = cand.snapshot
        calc = cand.calculation

        # Reason-aware repeated arm-rejection suppression (Rule 109)
        suppression_ms = getattr(cfg, "arm_rejection_suppression_ms", 30_000)
        with self._lock:
            last_rej = self._arm_rejections.get(cand.candidate_key)
            if last_rej:
                last_ts, last_reason = last_rej
                if (now_ms - last_ts) < suppression_ms:
                    logger.debug(f"Candidate {cand.candidate_key} suppressed due to repeated rejection ({last_reason})")
                    return None

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

        # 0b. Atomic Daily Cap & Pending Opening Intent check (Rules 008, 131)
        audit = crud.get_or_create_today_audit(db, clock=clock)
        effective_max_cap = int(getattr(cfg, "daily_max_cap", 3) or 3)
        armed_open_count = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").count()
        if (audit.fills_count + armed_open_count) >= effective_max_cap:
            logger.info(
                f"Daily cap reached: fills={audit.fills_count}, armed={armed_open_count}, cap={effective_max_cap}. "
                f"Refusing to arm candidate {cand.candidate_key}"
            )
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
                    reason_codes=("DAILY_CAP_REACHED",),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                    details={"fills_count": audit.fills_count, "armed_count": armed_open_count, "daily_max_cap": effective_max_cap},
                ),
                now_ms=now_ms,
            )
            return None

        # 1. Idempotency Check: prevent duplicate orders for the same signal (PHẦN 058, Rule 128)
        from services.scalp_m1_model_service import candidate_execution_fingerprint
        cand_fp = candidate_execution_fingerprint(cand)
        idempotency_key = f"scalp_arm_{cand_fp}"
        existing_order = db.query(models.PaperOrder).filter(
            (models.PaperOrder.idempotency_key == idempotency_key) |
            (models.PaperOrder.idempotency_key == f"scalp_arm_{cand.candidate_key}")
        ).first()
        if existing_order:
            logger.info(f"Idempotent hit: PaperOrder {existing_order.id} already armed for {cand.candidate_key}")
            return existing_order

        # 1b. Check candidate lifecycle authority (SECTIONS 088 - 093)
        from services.liquidity_scenario_service import check_candidate_lifecycle_authority
        is_auth, auth_reason = check_candidate_lifecycle_authority(cand, authority_context)
        if not is_auth:
            logger.info(f"Refusing to arm candidate {cand.candidate_key}: lifecycle authority rejected ({auth_reason})")
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
                    reason_codes=(auth_reason or OpportunityReasonCode.GUARD_BLOCKED.value,),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                ),
                now_ms=now_ms,
            )
            return None

        # 1c. Validate and normalize execution quote before mutations (V15.8 PROMPT 120, 121, 126)
        v_quote = normalize_execution_quote(quote, now_ms=now_ms)
        if not v_quote.is_valid or v_quote.is_stale:
            reason = v_quote.rejection_code or ("TICKER_STALE" if v_quote.is_stale else "QUOTE_INVALID")
            msg = (
                "Giá nhận được đã cũ nên bot chưa chuẩn bị lệnh mới. Đang chờ báo giá mới từ nguồn dữ liệu."
                if v_quote.is_stale
                else (v_quote.rejection_detail or "Chưa xác định được thời điểm của báo giá nên bot tạm chưa vào lệnh.")
            )
            logger.info(f"Quote invalid or stale at arm boundary for {cand.candidate_key}: {reason} ({msg})")
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
                    reason_codes=(reason,),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                    details={"message": msg},
                ),
                now_ms=now_ms,
            )
            return None

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
            validated_quote=v_quote,
            feed_health=actual_feed_health,
            min_net_rr=cfg.min_net_rr,
            clock=clock,
        )

        decision = evaluate_entry_guard(db, req)
        if not decision.allowed:
            logger.info(f"Guard blocked {cand.model_id} candidate: {decision.primary_reason if hasattr(decision, 'primary_reason') else decision.reason_codes}")
            rej_reason = decision.reason_codes[0] if decision.reason_codes else "GUARD_BLOCKED"
            with self._lock:
                self._arm_rejections[cand.candidate_key] = (now_ms, rej_reason)
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

        # 5. Guard passed: Create PaperOrder with canonical sizing and schema fields (PHẦN 025 - 030, Rule 031 - 034)
        with self._lock:
            self._arm_rejections.pop(cand.candidate_key, None)
        from services.cg01_types import candidate_execution_order_type, resolve_candidate_order_expiry
        resolved_order_type = candidate_execution_order_type(cand, legacy_default="MARKET")
        try:
            resolved_expires_at = resolve_candidate_order_expiry(cand, now_ms=now_ms, session_context=session_context)
        except Exception as e:
            logger.warning(f"[_arm_paper_candidate] Expiry resolution failed for candidate {cand.candidate_key}: {e}")
            return None

        # Rule 032: Check expiry before mutation - reject if already expired
        if now_ms >= resolved_expires_at:
            logger.info(f"[_arm_paper_candidate] Candidate {cand.candidate_key} expired before arm (now={now_ms} >= expires={resolved_expires_at})")
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
                    reason_codes=("TRIGGER_EXPIRED",),
                    planned_entry=cand.planned_entry,
                    stop_loss=cand.stop_loss,
                    take_profit=cand.take_profit,
                    net_rr=cand.net_rr,
                ),
                now_ms=now_ms,
            )
            return None

        from services.order_plan_snapshot_service import build_arm_plan_snapshot
        cand_slip = getattr(calc, "slippage_usd", None)
        if cand_slip is None and getattr(cfg, "cg01_config", None):
            cand_slip = getattr(cfg.cg01_config, "slippage_usd", None)
        effective_risk = policy_settings.get("risk_pct") if policy_settings.get("risk_pct") is not None else float(getattr(calc, "effective_risk_pct", cfg.fallback_risk_pct))
        arm_snap = build_arm_plan_snapshot(
            calc,
            order_type=resolved_order_type,
            direction=cand.direction,
            planned_entry=cand.planned_entry,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            capital_usdt=policy_settings.get("capital_usdt", 1000.0),
            risk_pct=effective_risk,
            leverage=calc.leverage,
            margin_mode=calc.margin_mode,
            now_ms=now_ms,
            policy_config_version=cfg.config_version,
            risk_config_version=cfg.config_version,
            slippage_usd=cand_slip,
        )
        arm_snap["expires_at"] = resolved_expires_at

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
            order_type=resolved_order_type,
            timeframe=snap.setup_timeframe if snap else "1M",
            planned_entry=cand.planned_entry,
            actual_entry=None,
            stop_loss=cand.stop_loss,
            take_profit=cand.take_profit,
            quantity=qty,
            initial_risk_usdt=float(calc.net_risk_usdt),
            risk_pct=effective_risk,
            gross_rr=float(calc.gross_rr),
            estimated_net_rr=float(calc.net_rr),
            leverage=calc.leverage,
            margin_mode=calc.margin_mode,
            estimated_liquidation=calc.estimated_liquidation,
            opened_at=None,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=resolved_expires_at,
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

        # 6. Publish order.armed domain event atomically within caller's db transaction (PHẦN 019, 020, 021, 138, Rule 034)
        # EventBus handles outbox notification deduplicated under the same transaction.
        from services.event_bus import event_bus
        event_bus.publish_event(
            event_type="order.armed",
            aggregate_id=order.id,
            payload={
                "order_id": order.id,
                "setup_id": ws.id,
                "direction": cand.direction,
                "order_type": order.order_type,
                "planned_entry": cand.planned_entry,
                "stop_loss": cand.stop_loss,
                "take_profit": cand.take_profit,
                "net_rr": calc.net_rr,
                "expires_at": resolved_expires_at,
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
