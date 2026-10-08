import json
import time
import uuid
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from database import SessionLocal
import models

class EventBus:
    """
    Central Domain Event Bus:
    - Persists typed domain events into SQLite with monotonic sequence numbers.
    - Broadcasts events to all active WebSocket clients.
    - Enqueues events into notification_outbox when matching subscription rules.
    """
    def __init__(self):
        self._active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()
        self._sequence = 0

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._active_connections.append(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self._active_connections:
                self._active_connections.remove(websocket)

    def publish_event(
        self,
        event_type: str,
        aggregate_id: str,
        payload: Dict[str, Any],
        schema_version: str = "1.0.0",
        aggregate_version: int = 1
    ) -> models.DomainEvent:
        """
        Synchronously commit domain event to DB and enqueue notification if needed,
        then schedule async broadcast to WebSocket clients.
        """
        now_ms = int(time.time() * 1000)
        event_id = str(uuid.uuid4())

        db: Session = SessionLocal()
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
                occurred_at=now_ms,
                published_at=now_ms,
                payload=json.dumps(payload)
            )
            db.add(domain_ev)

            # Check if Telegram notification is subscribed for this event type
            self._check_and_enqueue_notification(db, event_type, aggregate_id, payload, event_id, now_ms)

            db.commit()
            db.refresh(domain_ev)

            # Broadcast to WebSockets asynchronously if event loop is running
            event_dict = {
                "event_id": event_id,
                "sequence": seq,
                "schema_version": schema_version,
                "event_type": event_type,
                "aggregate_id": aggregate_id,
                "aggregate_version": aggregate_version,
                "occurred_at": now_ms,
                "published_at": now_ms,
                "payload": payload
            }
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._broadcast(event_dict))
            except RuntimeError:
                pass

            return domain_ev
        finally:
            db.close()

    def _check_and_enqueue_notification(
        self,
        db: Session,
        event_type: str,
        aggregate_id: str,
        payload: Dict[str, Any],
        event_id: str,
        now_ms: int
    ):
        """Enqueue to notification_outbox if Telegram is configured and subscribed."""
        # Mapping domain event type to notification alert category
        category_map = {
            "setup.ready": "READY",
            "order.armed": "ARMED_NEAR_ENTRY",
            "trade.opened": "FILLED",
            "setup.invalidated": "INVALIDATED",
            "setup.expired": "EXPIRED",
            "trade.closed": "CLOSED",
            "trade.liquidated": "LIQUIDATED",
            "feed.degraded": "FEED_DOWN",
            "feed.recovered": "RECOVERED"
        }
        notif_type = category_map.get(event_type)
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

        if notif_type not in subscribed:
            return

        # Dedupe key: aggregate_id + notif_type
        dedupe_key = f"{aggregate_id}:{notif_type}"
        existing = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.dedupe_key == dedupe_key).first()
        if existing:
            return  # Already queued or sent

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
                "occurred_at": now_ms
            }),
            status="PENDING",
            attempts=0,
            created_at=now_ms
        )
        db.add(outbox_entry)

    async def _broadcast(self, event_dict: Dict[str, Any]):
        msg_str = json.dumps(event_dict)
        dead_connections = []
        async with self._lock:
            for ws in self._active_connections:
                try:
                    await ws.send_text(msg_str)
                except Exception:
                    dead_connections.append(ws)
            for ws in dead_connections:
                if ws in self._active_connections:
                    self._active_connections.remove(ws)


# Global Singleton Event Bus
event_bus = EventBus()
