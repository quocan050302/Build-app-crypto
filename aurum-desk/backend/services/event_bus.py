import json
import time
import uuid
import asyncio
import logging
import copy
import threading
from typing import List, Dict, Any, Optional, Tuple, Set
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
    elif event_type in ("setup.cancelled", "order.cancelled"):
        return "CANCELLED"
    elif event_type == "setup.invalidated":
        return "INVALIDATED"
    elif event_type in ("setup.expired", "order.expired"):
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
    # Legacy INVALIDATED covers EXPIRED and CANCELLED
    if notif_type in ("EXPIRED", "CANCELLED") and "INVALIDATED" in subscribed_list:
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
        self._dispatch_lock = threading.Lock()
        self._generation: int = 0
        self._is_accepting: bool = True
        self._pending_tokens: Set[str] = set()
        self._active_tasks: Set[asyncio.Task] = set()

    def set_main_loop(self, loop: asyncio.AbstractEventLoop):
        """Bind main loop and start a new lifecycle generation."""
        with self._dispatch_lock:
            self._main_loop = loop
            self._generation += 1
            self._is_accepting = True
            logger.info("EventBus: main loop bound (generation=%d)", self._generation)

    def begin_dispatch_shutdown(self):
        """Stop accepting new dispatches, preserving accepted pending/active work."""
        with self._dispatch_lock:
            self._is_accepting = False
            logger.info("EventBus: dispatch shutdown initiated (generation=%d)", self._generation)

    def unbind_main_loop(self, expected_loop: Optional[asyncio.AbstractEventLoop] = None, expected_gen: Optional[int] = None):
        """
        Unbind main loop reference if it matches expected identity/generation.
        Prevents stale shutdown from clearing a newly started lifecycle.
        """
        with self._dispatch_lock:
            if expected_loop is not None and self._main_loop is not expected_loop:
                return
            if expected_gen is not None and self._generation != expected_gen:
                return
            self._main_loop = None
            self._is_accepting = False
            logger.info("EventBus: main loop unbound")

    def reset_for_test(self):
        """Test fixture helper to reset internal lifecycle cleanly."""
        with self._dispatch_lock:
            self._main_loop = None
            self._generation += 1
            self._is_accepting = True
            self._pending_tokens.clear()
            self._active_tasks.clear()

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
                    try:
                        sa_event.remove(db, "after_rollback", on_after_rollback)
                    except Exception:
                        pass
                    self.dispatch_websocket_broadcast(event_dict)

                def on_after_rollback(session):
                    try:
                        sa_event.remove(db, "after_commit", on_after_commit)
                    except Exception:
                        pass

                sa_event.listen(db, "after_commit", on_after_commit, once=True)
                sa_event.listen(db, "after_rollback", on_after_rollback, once=True)

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

    def _resolve_dispatch_loop(self) -> Tuple[Optional[asyncio.AbstractEventLoop], str]:
        """
        Determine target loop for dispatch without instantiating any coroutine.
        Returns (target_loop, reason).
        """
        with self._dispatch_lock:
            if not self._is_accepting:
                return None, "SHUTDOWN_NOT_ACCEPTING"

            if self._main_loop is not None:
                if self._main_loop.is_closed():
                    return None, "BOUND_LOOP_CLOSED"
                if not self._main_loop.is_running():
                    return None, "BOUND_LOOP_NOT_RUNNING"
                return self._main_loop, "BOUND_MAIN_LOOP"

        # Unbound legacy mode: check if current thread has a running loop
        try:
            curr = asyncio.get_running_loop()
            if curr and not curr.is_closed() and curr.is_running():
                return curr, "RUNNING_FALLBACK_LOOP"
        except RuntimeError:
            pass

        return None, "NO_RUNNING_LOOP"

    def dispatch_websocket_broadcast(self, event_dict: Dict[str, Any]):
        """Schedule non-blocking broadcast through market_broadcaster, thread-safe and coroutine-leak-free."""
        loop, reason = self._resolve_dispatch_loop()
        if loop is None:
            logger.debug("Cannot dispatch domain event: target loop unavailable (%s)", reason)
            return

        # Snapshot payload for immutability
        try:
            payload_snapshot = copy.deepcopy(event_dict)
        except Exception:
            payload_snapshot = dict(event_dict)

        token = str(uuid.uuid4())
        with self._dispatch_lock:
            if not self._is_accepting:
                logger.debug("Cannot dispatch domain event: dispatch stopped")
                return
            current_gen = self._generation
            self._pending_tokens.add(token)

        def callback():
            self._start_broadcast_on_loop(token, payload_snapshot, current_gen, loop)

        try:
            loop.call_soon_threadsafe(callback)
        except RuntimeError as e:
            with self._dispatch_lock:
                self._pending_tokens.discard(token)
            logger.debug("Failed to schedule broadcast callback onto target loop (%s): %s", reason, e)

    def _start_broadcast_on_loop(
        self,
        token: str,
        payload: Dict[str, Any],
        gen: int,
        loop: asyncio.AbstractEventLoop
    ):
        """
        Synchronous callback executed on the target event loop.
        Validates lifecycle, creates coroutine and asyncio.Task,
        and transitions ownership from pending to active.
        """
        with self._dispatch_lock:
            # Rule 062: Obsolete generation callbacks must be dropped
            if self._generation != gen:
                self._pending_tokens.discard(token)
                logger.debug("Dropping broadcast callback for obsolete generation (%d != %d)", gen, self._generation)
                return

        if loop.is_closed():
            with self._dispatch_lock:
                self._pending_tokens.discard(token)
            return

        from services.market_broadcaster import market_broadcaster

        coro = market_broadcaster.broadcast_domain_event(payload)
        if not asyncio.iscoroutine(coro):
            with self._dispatch_lock:
                self._pending_tokens.discard(token)
            return

        try:
            task = loop.create_task(coro)
        except RuntimeError as e:
            coro.close()
            with self._dispatch_lock:
                self._pending_tokens.discard(token)
            logger.debug("Failed to create task on target loop: %s", e)
            return

        with self._dispatch_lock:
            self._pending_tokens.discard(token)
            self._active_tasks.add(task)

        def on_done(t: asyncio.Task):
            self._on_broadcast_done(t, payload.get("event_id"))

        task.add_done_callback(on_done)

    def _on_broadcast_done(self, task: asyncio.Task, event_id: Optional[str]):
        """Done callback for broadcast task. Cleans up active tasks and observes exceptions."""
        with self._dispatch_lock:
            self._active_tasks.discard(task)

        if task.cancelled():
            logger.debug("Domain event broadcast task cancelled (event_id=%s)", event_id)
            return

        try:
            exc = task.exception()
            if exc is not None:
                logger.error("Domain event broadcast failed (event_id=%s): %s", event_id, exc)
        except (asyncio.CancelledError, Exception) as e:
            logger.debug("Error retrieving broadcast task exception: %s", e)

    async def drain_websocket_broadcasts(self, timeout_s: float = 3.0) -> Dict[str, Any]:
        """
        Drain all accepted pending callbacks and active broadcast tasks.
        Uses monotonic clock and does not block the event loop.
        """
        start_mono = time.monotonic()
        deadline = start_mono + max(0.0, timeout_s)

        while True:
            with self._dispatch_lock:
                pending_cnt = len(self._pending_tokens)
                active_cnt = len(self._active_tasks)
                tasks_snapshot = list(self._active_tasks)

            if pending_cnt == 0 and active_cnt == 0:
                elapsed_ms = int((time.monotonic() - start_mono) * 1000)
                return {
                    "status": "COMPLETED",
                    "pending_count": 0,
                    "active_task_count": 0,
                    "elapsed_ms": elapsed_ms,
                }

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._dispatch_lock:
                    to_cancel = list(self._active_tasks)
                for t in to_cancel:
                    t.cancel()
                if to_cancel:
                    await asyncio.gather(*to_cancel, return_exceptions=True)
                return {
                    "status": "TIMED_OUT",
                    "pending_count": pending_cnt,
                    "active_task_count": active_cnt,
                    "elapsed_ms": int((time.monotonic() - start_mono) * 1000),
                }

            if tasks_snapshot:
                try:
                    done, _ = await asyncio.wait(
                        tasks_snapshot,
                        timeout=min(0.05, remaining),
                        return_when=asyncio.FIRST_COMPLETED
                    )
                except Exception:
                    await asyncio.sleep(min(0.01, remaining))
            else:
                await asyncio.sleep(min(0.01, remaining))


# Global Singleton Event Bus
event_bus = EventBus()

