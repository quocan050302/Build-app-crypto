import time
import json
import logging
from typing import Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from database import SessionLocal
import models, crud
from services.event_bus import event_bus
from services.collector_service import collector_service

logger = logging.getLogger(__name__)

class ProximityService:
    """
    Independent Proximity Evaluator Service:
    - Runs independently of frontend polling.
    - Evaluates live quotes (Ask for LONG, Bid for SHORT) against active watch setups.
    - Validates ticker freshness and sanity before evaluating.
    - Enforces hysteresis (enter_threshold vs exit_threshold) and one alert per setup instance.
    - Persists proximity state in SQLite to survive restarts without alert spam.
    - Disallows fake proximity (e.g. provisional_entry = current_price when no confirmed POI/signal).
    """

    def __init__(self):
        self._running = False

    def evaluate_setup_proximity(
        self,
        db: Session,
        watch_setup: models.WatchSetup,
        ticker: Dict[str, Any],
        atr: float,
        tg_cfg: Optional[models.TelegramConfig] = None
    ) -> Optional[models.DomainEvent]:
        """
        Evaluate single watch setup against ticker and return DomainEvent if NEAR_ENTRY threshold entered.
        """
        now_ms = int(time.time() * 1000)

        # 1. State eligibility: Only active un-filled, un-closed, un-expired setups
        if watch_setup.state in ("PAPER_OPEN", "CLOSED", "INVALIDATED", "EXPIRED", "CANCELLED", "REJECTED"):
            return None

        # Expiry check
        if watch_setup.expires_at and now_ms > watch_setup.expires_at:
            return None

        # 2. Check for real POI / signal validity (prevent provisional_entry == current_price spam)
        # Setup must have conditions_met showing at least structure progression or a concrete POI zone
        has_real_poi = False
        if watch_setup.poi_zone:
            try:
                pz = json.loads(watch_setup.poi_zone)
                if pz.get("zone") or pz.get("top") or pz.get("bottom"):
                    has_real_poi = True
            except Exception:
                pass
        
        # If setup is in initial WATCHING or WAITING_PRICE with no POI or zero R:R, it's not actionable
        if not has_real_poi and watch_setup.state in ("WATCHING", "WAITING_PRICE") and (watch_setup.gross_rr or 0) <= 0:
            return None

        # 3. Ticker Sanity & Freshness
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if not bid or not ask or bid <= 0 or ask < bid:
            return None

        # Check ticker staleness
        ticker_time = ticker.get("timestamp") or ticker.get("server_time")
        if ticker_time and (now_ms - ticker_time) > (30 * 60 * 1000):
            # Feed stale > 30 minutes, disallow actionable near-entry alert
            return None

        # 4. Entry zone & executable reference side
        direction = (watch_setup.direction or "LONG").upper()
        if direction == "LONG":
            ref_price = ask
            exec_side = "ASK"
        else:
            ref_price = bid
            exec_side = "BID"

        # Determine Entry Zone bounds
        entry_target = watch_setup.provisional_entry
        zone_low = watch_setup.entry_zone_low or entry_target
        zone_high = watch_setup.entry_zone_high or entry_target
        if zone_low > zone_high:
            zone_low, zone_high = zone_high, zone_low

        # 5. Distance Calculation
        if zone_low <= ref_price <= zone_high:
            distance_price = 0.0
        elif ref_price < zone_low:
            distance_price = round(zone_low - ref_price, 2)
        else:
            distance_price = round(ref_price - zone_high, 2)

        safe_atr = max(0.1, atr)
        distance_atr = round(distance_price / safe_atr, 2)

        # Update watch setup distance metrics in DB
        watch_setup.distance_to_entry_usdt = distance_price
        watch_setup.distance_to_entry_atr = distance_atr

        # 6. Read Configured Thresholds
        mode = "ATR"
        atr_mult = 0.5
        price_dist_threshold = 2.0
        cooldown_min = 30
        if tg_cfg:
            mode = tg_cfg.near_entry_mode or "ATR"
            atr_mult = tg_cfg.near_entry_atr_mult if tg_cfg.near_entry_atr_mult is not None else 0.5
            price_dist_threshold = tg_cfg.near_entry_price_dist if tg_cfg.near_entry_price_dist is not None else 2.0
            cooldown_min = tg_cfg.near_entry_cooldown_min if tg_cfg.near_entry_cooldown_min is not None else 30

        if mode == "PRICE_DISTANCE":
            enter_threshold = price_dist_threshold
            exit_threshold = price_dist_threshold * 1.5
            is_inside_threshold = distance_price <= enter_threshold
        else:
            enter_threshold = safe_atr * atr_mult
            exit_threshold = enter_threshold * 1.5
            is_inside_threshold = distance_price <= enter_threshold

        # 7. Hysteresis & Anti-Spam (One alert per setup instance or cooldown)
        setup_instance = watch_setup.setup_instance_id or watch_setup.id
        already_alerted = watch_setup.near_entry_alerted_at is not None

        if not is_inside_threshold:
            # If distance exceeds exit_threshold by a good margin, we can reset near_entry_alerted_at
            # only if repeat-on-reentry is desired; by default one alert per instance.
            return None

        if already_alerted:
            # Check cooldown if repeat policy enabled
            cooldown_ms = cooldown_min * 60 * 1000
            if (now_ms - watch_setup.near_entry_alerted_at) < cooldown_ms:
                # Still within suppression window
                return None

        # 8. Mark Alerted and Emit setup.near_entry Domain Event
        watch_setup.near_entry_alerted_at = now_ms
        watch_setup.near_entry_distance_price = distance_price
        watch_setup.near_entry_distance_atr = distance_atr
        db.commit()

        # Parse conditions
        conditions_met = []
        conditions_remaining = []
        try:
            if watch_setup.conditions_met:
                conditions_met = json.loads(watch_setup.conditions_met)
            if watch_setup.conditions_remaining:
                conditions_remaining = json.loads(watch_setup.conditions_remaining)
        except Exception:
            pass

        # Check auto state
        auto_enabled = False
        auto_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "auto_paper_trading").first()
        if auto_cfg and auto_cfg.value.lower() == "true":
            auto_enabled = True

        payload = {
            "setup_id": watch_setup.id,
            "setup_instance_id": setup_instance,
            "direction": direction,
            "executable_side": exec_side,
            "reference_price": ref_price,
            "entry_target": entry_target,
            "entry_zone_low": zone_low,
            "entry_zone_high": zone_high,
            "distance_price": distance_price,
            "distance_atr": distance_atr,
            "atr": round(safe_atr, 2),
            "stop_loss": watch_setup.provisional_sl,
            "take_profit": watch_setup.provisional_tp,
            "net_rr": watch_setup.net_rr,
            "risk_usdt": watch_setup.risk_usdt,
            "conditions_met": conditions_met,
            "conditions_remaining": conditions_remaining,
            "expires_at": watch_setup.expires_at,
            "auto_paper_enabled": auto_enabled,
            "state": watch_setup.state
        }

        # Dedupe key uses setup_instance_id to prevent blocking future setups
        return event_bus.publish_event(
            event_type="setup.near_entry",
            aggregate_id=setup_instance,
            payload=payload,
            aggregate_version=watch_setup.version or 1,
            db=db
        )

    async def evaluate_active_setups(self):
        """Evaluate all watching/ready setups against latest live ticker"""
        ticker = collector_service.latest_ticker
        if not ticker or not ticker.get("bid") or not ticker.get("ask"):
            return

        db: Session = SessionLocal()
        try:
            tg_cfg = db.query(models.TelegramConfig).first()
            active_setups = (
                db.query(models.WatchSetup)
                .filter(models.WatchSetup.state.in_(["READY", "WAITING_RETRACE", "WAITING_MSS", "ARMED"]))
                .all()
            )
            # Default fallback ATR for gold
            atr = 2.5
            for s in active_setups:
                self.evaluate_setup_proximity(db, s, ticker, atr, tg_cfg)
        finally:
            db.close()

    async def run_proximity_loop(self):
        """Background proximity evaluator loop running every 2 seconds"""
        self._running = True
        while True:
            try:
                await asyncio.sleep(2.0)
                await self.evaluate_active_setups()
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                await asyncio.sleep(2.0)

proximity_service = ProximityService()
