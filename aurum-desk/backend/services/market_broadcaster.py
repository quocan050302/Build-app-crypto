import json
import time
import asyncio
import logging
from typing import Dict, List, Optional, Set, Any
from fastapi import WebSocket, WebSocketDisconnect
from services.quote_validator import CanonicalQuote
import schemas

logger = logging.getLogger(__name__)

class MarketBroadcaster:
    """
    Market Broadcaster for Frontend Clients (V7.1):
    - Multiplexes real-time market telemetry over /ws.
    - Uses versioned message envelopes (protocol_version: 7.1.0).
    - Throttles UI quote ticks to 2-4 fps (250-400ms) to eliminate browser UI freeze.
    - Prioritizes critical events (CANDLE_CLOSED, DOMAIN_EVENT) with immediate delivery.
    - Provides backpressure isolation: a slow client cannot block server execution or other clients.
    """
    def __init__(self, throttle_ms: int = 250):
        self.throttle_ms = throttle_ms
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._server_sequence = 0
        self._last_broadcast_quote_ts = 0
        self._pending_quote: Optional[CanonicalQuote] = None
        self._throttle_task: Optional[asyncio.Task] = None

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket):
        async with self._lock:
            self._clients.add(ws)
            logger.info(f"Frontend client connected to MarketBroadcaster (total: {len(self._clients)})")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._clients.discard(ws)
            logger.info(f"Frontend client disconnected from MarketBroadcaster (total: {len(self._clients)})")

    def _next_sequence(self) -> int:
        self._server_sequence += 1
        return self._server_sequence

    async def _send_to_all(self, message: Dict[str, Any], timeout: float = 1.0):
        if not self._clients:
            return

        text = json.dumps(message)
        dead_clients = []

        async with self._lock:
            clients_snapshot = list(self._clients)

        for ws in clients_snapshot:
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=timeout)
            except (asyncio.TimeoutError, WebSocketDisconnect, Exception) as e:
                dead_clients.append(ws)

        if dead_clients:
            async with self._lock:
                for ws in dead_clients:
                    self._clients.discard(ws)

    def broadcast_quote(self, quote: CanonicalQuote):
        """Buffers quote and schedules throttled broadcast for UI."""
        self._pending_quote = quote
        now = time.time() * 1000
        if now - self._last_broadcast_quote_ts >= self.throttle_ms:
            self._last_broadcast_quote_ts = now
            asyncio.create_task(self._flush_quote_broadcast(quote))

    async def _flush_quote_broadcast(self, quote: CanonicalQuote):
        now_ms = int(time.time() * 1000)
        envelope = {
            "protocol_version": "7.1.0",
            "type": "QUOTE_UPDATE",
            "symbol": quote.symbol,
            "connection_epoch": quote.connection_epoch,
            "server_sequence": self._next_sequence(),
            "exchange_ts_ms": quote.exchange_ts_ms,
            "server_received_at_ms": quote.received_at_ms,
            "published_at_ms": now_ms,
            "payload": {
                "last": quote.last,
                "bid": quote.bid,
                "ask": quote.ask,
                "spread": quote.spread,
                "source": quote.source,
                "freshness_sec": quote.freshness_sec
            }
        }
        await self._send_to_all(envelope, timeout=0.5)

    async def broadcast_candle_update(self, candle: schemas.CandleCreate):
        """Broadcasts current open candle update to chart."""
        now_ms = int(time.time() * 1000)
        envelope = {
            "protocol_version": "7.1.0",
            "type": "CANDLE_UPDATE",
            "symbol": candle.symbol,
            "timeframe": candle.timeframe,
            "server_sequence": self._next_sequence(),
            "published_at_ms": now_ms,
            "payload": {
                "time": candle.timestamp // 1000,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "is_closed": candle.is_closed
            }
        }
        await self._send_to_all(envelope, timeout=0.8)

    async def broadcast_candle_closed(self, candle: schemas.CandleCreate):
        """Immediately broadcasts finalized closed bar to chart."""
        now_ms = int(time.time() * 1000)
        envelope = {
            "protocol_version": "7.1.0",
            "type": "CANDLE_CLOSED",
            "symbol": candle.symbol,
            "timeframe": candle.timeframe,
            "server_sequence": self._next_sequence(),
            "published_at_ms": now_ms,
            "payload": {
                "time": candle.timestamp // 1000,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "is_closed": True
            }
        }
        await self._send_to_all(envelope, timeout=1.5)

    async def broadcast_domain_event(self, event_dict: Dict[str, Any]):
        """Broadcasts transactional business event (FILLED, TP_HIT, SL_HIT) immediately."""
        now_ms = int(time.time() * 1000)
        envelope = {
            "protocol_version": "7.1.0",
            "type": "DOMAIN_EVENT",
            "server_sequence": self._next_sequence(),
            "published_at_ms": now_ms,
            "event": event_dict
        }
        await self._send_to_all(envelope, timeout=2.0)

market_broadcaster = MarketBroadcaster()
