import json
import time
import uuid
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from database import SessionLocal
import models

logger = logging.getLogger(__name__)

CRITICAL_NOTIF_TYPES = {"FILLED", "TP_HIT", "SL_HIT", "MANUAL_CLOSED", "LIQUIDATED"}

def resolve_notification_type(event_type: str, payload: Dict[str, Any]) -> Optional[str]:
    """Map domain event type and payload cause to notification message type."""
    if event_type == "setup.ready":
        return "READY"
    elif event_type == "setup.near_entry":
        return "NEAR_ENTRY"
    elif event_type == "order.armed":
        return "ARMED"
    elif event_type == "trade.opened":
        return "FILLED"
    elif event_type == "order.rejected":
        return "REJECTED"
    elif event_type == "trade.liquidated":
        return "LIQUIDATED"
    elif event_type == "trade.closed":
        cause = (payload.get("exit_cause") or "").upper()
        if cause == "TP_HIT":
            return "TP_HIT"
        elif cause in ("SL_HIT", "AMBIGUOUS_BAR_SL_FIRST"):
            return "SL_HIT"
        elif cause == "MANUAL_CLOSE":
            return "MANUAL_CLOSED"
        elif cause == "LIQUIDATED":
            return "LIQUIDATED"
        return "CLOSED"
    elif event_type == "setup.invalidated":
        return "INVALIDATED"
    elif event_type == "setup.expired":
        return "EXPIRED"
    elif event_type == "feed.degraded":
        return "FEED_DOWN"
    elif event_type == "feed.recovered":
        return "RECOVERED"
    return None


def is_notification_subscribed(notif_type: str, subscribed_list: List[str]) -> bool:
    """Check subscription with legacy backward compatibility."""
    if notif_type in subscribed_list:
        return True
    # Legacy CLOSED covers TP_HIT, SL_HIT, MANUAL_CLOSED
    if notif_type in ("TP_HIT", "SL_HIT", "MANUAL_CLOSED") and "CLOSED" in subscribed_list:
        return True
    # Legacy ARMED_NEAR_ENTRY covers ARMED and NEAR_ENTRY
    if notif_type in ("ARMED", "NEAR_ENTRY") and "ARMED_NEAR_ENTRY" in subscribed_list:
        return True
    return False


class EventBus:
    """
    Central Domain Event Bus:
    - Persists typed domain events into SQLite with monotonic sequence numbers.
    - Supports atomic unit-of-work transactions (accepts caller DB session).
    - Enqueues events into notification_outbox when matching subscription rules.
    - Priority-aware (CRITICAL vs STANDARD).
    - Broadcasts events to all active WebSocket clients post-commit.
    """
    def __init__(self):
        self._active_connections: List[WebSocket] = []
        self._lock: Optional[asyncio.Lock] = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    def set_main_loop(self, loop: asyncio.AbstractEventLoop):
        self._main_loop = loop

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def connect(self, websocket: WebSocket):
        pass

    async def disconnect(self, websocket: WebSocket):
        pass

    def publish_event(
        self,
        event_type: str,
        aggregate_id: str,
        payload: Dict[str, Any],
        schema_version: str = "1.0.0",
        aggregate_version: int = 1,
        db: Optional[Session] = None,
        occurred_at: Optional[int] = None
    ) -> models.DomainEvent:
        """
        Record domain event and outbox notification.
        If db is passed, joins the caller's transaction without committing (unit-of-work).
        If db is None, creates a self-contained session and commits.
        """
        now_ms = int(time.time() * 1000)
        occurred_at_ms = occurred_at or now_ms
        event_id = str(uuid.uuid4())

        owns_session = False
        if db is None:
            db = SessionLocal()
            owns_session = True

        try:
            # Query last sequence number
            last_ev = db.query(models.DomainEvent).order_by(models.DomainEvent.sequence.desc()).first()
            seq = (last_ev.sequence + 1) if last_ev else 1

            domain_ev = models.DomainEvent(
                event_id=event_id,
                sequence=seq,
                schema_version=schema_version,
                event_type=event_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                occurred_at=occurred_at_ms,
                published_at=now_ms,
                payload=json.dumps(payload)
            )
            db.add(domain_ev)

            # Check if Telegram notification is subscribed for this event type
            self._check_and_enqueue_notification(db, event_type, aggregate_id, payload, event_id, occurred_at_ms, now_ms)

            if owns_session:
                db.commit()
                db.refresh(domain_ev)
            else:
                db.flush()

            # Schedule async WebSocket broadcast
            event_dict = {
                "event_id": event_id,
                "sequence": seq,
                "schema_version": schema_version,
                "event_type": event_type,
                "aggregate_id": aggregate_id,
                "aggregate_version": aggregate_version,
                "occurred_at": occurred_at_ms,
                "published_at": now_ms,
                "payload": payload
            }

            if owns_session:
                self.dispatch_websocket_broadcast(event_dict)
            else:
                # Post-commit dispatch: guarantee event is ONLY broadcast after successful commit!
                from sqlalchemy import event as sa_event
                def on_after_commit(session):
                    self.dispatch_websocket_broadcast(event_dict)
                sa_event.listen(db, "after_commit", on_after_commit, once=True)

            return domain_ev

        except Exception as e:
            if owns_session:
                db.rollback()
            raise e
        finally:
            if owns_session:
                db.close()

    def _check_and_enqueue_notification(
        self,
        db: Session,
        event_type: str,
        aggregate_id: str,
        payload: Dict[str, Any],
        event_id: str,
        occurred_at_ms: int,
        now_ms: int
    ):
        """Enqueue to notification_outbox if Telegram is configured and subscribed."""
        notif_type = resolve_notification_type(event_type, payload)
        if not notif_type:
            return

        tg_cfg = db.query(models.TelegramConfig).first()
        if not tg_cfg or not tg_cfg.enabled or not tg_cfg.bot_token or not tg_cfg.chat_id:
            return

        # Check subscribed events
        subscribed = []
        if tg_cfg.subscribed_events:
            try:
                subscribed = json.loads(tg_cfg.subscribed_events)
            except Exception:
                subscribed = []

        if not is_notification_subscribed(notif_type, subscribed):
            return

        # Dedupe key determination:
        # Use dynamic setup_instance_id or trade_id to avoid blocking future setups
        if event_type in ("setup.ready", "setup.near_entry"):
            key_id = payload.get("setup_instance_id") or aggregate_id
        elif event_type in ("trade.opened", "trade.closed", "trade.liquidated"):
            key_id = payload.get("trade_id") or aggregate_id
        elif event_type in ("order.armed", "order.rejected"):
            key_id = payload.get("order_id") or aggregate_id
        else:
            key_id = aggregate_id

        dedupe_key = f"{key_id}:{notif_type}"
        existing = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.dedupe_key == dedupe_key).first()
        if existing:
            return  # Already queued or sent

        priority = "CRITICAL" if notif_type in CRITICAL_NOTIF_TYPES else "STANDARD"

        outbox_entry = models.NotificationOutbox(
            event_id=event_id,
            channel="TELEGRAM",
            recipient=tg_cfg.chat_id,
            message_type=notif_type,
            dedupe_key=dedupe_key,
            payload=json.dumps({
                "notif_type": notif_type,
                "event_type": event_type,
                "aggregate_id": aggregate_id,
                "data": payload,
                "occurred_at": occurred_at_ms
            }),
            status="PENDING",
            priority=priority,
            attempts=0,
            next_attempt_at=now_ms,
            occurred_at=occurred_at_ms,
            created_at=now_ms
        )
        db.add(outbox_entry)

    def dispatch_websocket_broadcast(self, event_dict: Dict[str, Any]):
        """Schedule non-blocking broadcast through market_broadcaster, thread-safe."""
        from services.market_broadcaster import market_broadcaster

        try:
            current_loop = asyncio.get_running_loop()
            if current_loop == self._main_loop or self._main_loop is None:
                current_loop.create_task(market_broadcaster.broadcast_domain_event(event_dict))
            else:
                asyncio.run_coroutine_threadsafe(market_broadcaster.broadcast_domain_event(event_dict), self._main_loop)
        except RuntimeError:
            # Called from a worker thread or synchronous test without running loop
            if self._main_loop and not self._main_loop.is_closed():
                asyncio.run_coroutine_threadsafe(market_broadcaster.broadcast_domain_event(event_dict), self._main_loop)
            else:
                logger.debug("Cannot dispatch domain event: event loop not running or closed.")


# Global Singleton Event Bus
event_bus = EventBus()

