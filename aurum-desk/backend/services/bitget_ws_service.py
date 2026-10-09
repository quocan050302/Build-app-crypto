import json
import time
import asyncio
import random
import logging
from typing import Optional, Dict, Any
import httpx
import websockets
from services.quote_validator import CanonicalQuote, QuoteValidator
from services.execution_consumer import execution_consumer
from services.candle_cache_service import candle_cache_service

logger = logging.getLogger(__name__)

BITGET_WS_URL = "wss://ws.bitget.com/v2/ws/public"
BITGET_REST_TICKER_URL = "https://api.bitget.com/api/v2/mix/market/ticker"

class BitgetWSService:
    """
    Bitget Public WebSocket Adapter (V7.1):
    - Canonical Market Feed Owner for XAUUSDT (USDT-FUTURES).
    - Subscribes to Classic v2 ticker channel.
    - Handles text ping/pong heartbeats every 25s.
    - Exponential backoff with jitter and cap for robust auto-reconnection.
    - Controlled, single-flight REST fallback when WebSocket is degraded.
    - Directly pipes canonical quotes to the ordered execution consumer and candle cache.
    """
    def __init__(self, ws_url: str = BITGET_WS_URL, symbol: str = "XAUUSDT"):
        self.ws_url = ws_url
        self.symbol = symbol
        self.transport_state = "DISCONNECTED"  # CONNECTING, CONNECTED, DEGRADED, DISCONNECTED
        self.feed_source = "WS"  # WS or REST_FALLBACK
        self.connection_epoch = 0
        self.reconnect_count = 0
        self.latest_quote: Optional[CanonicalQuote] = None
        self.last_success_time = 0
        self.last_heartbeat_time = 0
        self.last_error: Optional[str] = None

        self._running = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._rest_fallback_task: Optional[asyncio.Task] = None
        self._ws_client = None
        self._broadcaster_callback = None

    def register_broadcaster(self, callback):
        self._broadcaster_callback = callback

    def _parse_ticker_payload(self, data_list: list, epoch: int) -> Optional[CanonicalQuote]:
        if not data_list:
            return None
        item = data_list[0]
        raw_quote = {
            "symbol": self.symbol,
            "bid": item.get("bidPr"),
            "ask": item.get("askPr"),
            "last": item.get("lastPr"),
            "mark_price": item.get("markPrice"),
            "exchange_ts_ms": item.get("ts"),
            "received_at_ms": int(time.time() * 1000)
        }
        canonical = QuoteValidator.validate_canonical(
            raw_quote,
            source="WS",
            connection_epoch=epoch
        )
        return canonical

    def _dispatch_quote(self, quote: CanonicalQuote):
        if not quote.is_valid:
            return

        self.latest_quote = quote
        self.last_success_time = quote.received_at_ms

        # 1. Non-coalesced Ordered Execution Consumer (Exits & Entries)
        execution_consumer.enqueue_quote(quote)

        # 2. Candle Cache (Live bar update)
        candle_cache_service.process_quote(quote)

        # 3. Market Broadcaster for UI (Throttled deltas)
        if self._broadcaster_callback:
            try:
                self._broadcaster_callback(quote)
            except Exception as e:
                logger.error(f"Broadcaster dispatch error: {e}")

    async def _run_rest_fallback(self):
        """Controlled REST fallback when WebSocket connection is unavailable."""
        logger.info("Starting REST fallback loop...")
        async with httpx.AsyncClient(timeout=5.0) as client:
            while self.transport_state in ("DEGRADED", "CONNECTING", "DISCONNECTED") and self._running:
                try:
                    params = {"symbol": self.symbol, "productType": "USDT-FUTURES"}
                    resp = await client.get(BITGET_REST_TICKER_URL, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("code") == "00000" and data.get("data"):
                            item = data["data"][0]
                            now_ms = int(time.time() * 1000)
                            raw = {
                                "symbol": self.symbol,
                                "bid": item.get("bidPr"),
                                "ask": item.get("askPr"),
                                "last": item.get("lastPr"),
                                "mark_price": item.get("markPrice"),
                                "exchange_ts_ms": data.get("requestTime"),
                                "received_at_ms": now_ms
                            }
                            quote = QuoteValidator.validate_canonical(
                                raw,
                                source="REST_FALLBACK",
                                connection_epoch=self.connection_epoch
                            )
                            if quote.is_valid:
                                self.feed_source = "REST_FALLBACK"
                                self._dispatch_quote(quote)
                except Exception as e:
                    logger.debug(f"REST fallback tick error: {e}")

                await asyncio.sleep(2.5)
        logger.info("REST fallback loop exited.")

    async def _heartbeat_loop(self, ws):
        """Bitget public WS heartbeat: sends text 'ping' every 25s."""
        while self.transport_state == "CONNECTED" and self._running:
            try:
                await asyncio.sleep(25.0)
                await ws.send("ping")
            except Exception:
                break

    async def run_ws_loop(self):
        """Main WebSocket loop with backoff and fallback."""
        self._running = True
        attempt = 0

        while self._running:
            self.connection_epoch += 1
            epoch = self.connection_epoch
            self.transport_state = "CONNECTING"

            # Start REST fallback if not running
            if not self._rest_fallback_task or self._rest_fallback_task.done():
                self._rest_fallback_task = asyncio.create_task(self._run_rest_fallback())

            try:
                logger.info(f"Connecting to Bitget WebSocket ({self.ws_url}), epoch {epoch}...")
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=None,  # We manage Bitget's application-level text ping
                    close_timeout=5.0
                ) as ws:
                    self._ws_client = ws
                    # Subscribe to ticker
                    sub_msg = {
                        "op": "subscribe",
                        "args": [
                            {"instType": "USDT-FUTURES", "channel": "ticker", "instId": self.symbol}
                        ]
                    }
                    await ws.send(json.dumps(sub_msg))

                    # Start application ping loop
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

                    try:
                        async for msg in ws:
                            if not self._running:
                                break

                            if msg == "pong":
                                self.last_heartbeat_time = int(time.time() * 1000)
                                continue

                            try:
                                payload = json.loads(msg)
                            except Exception:
                                continue

                            # Handle subscription ack
                            if payload.get("event") == "subscribe":
                                if payload.get("code") == 0:
                                    logger.info(f"Subscribed successfully to Bitget {self.symbol} ticker.")
                                    self.transport_state = "CONNECTED"
                                    self.feed_source = "WS"
                                    attempt = 0  # Reset backoff
                                    continue
                                else:
                                    self.last_error = f"Subscription failed: {payload}"
                                    logger.error(self.last_error)
                                    break

                            if payload.get("event") == "error":
                                self.last_error = f"Bitget WS error: {payload}"
                                logger.error(self.last_error)
                                break

                            # Handle ticker data update / snapshot
                            arg = payload.get("arg", {})
                            if arg.get("channel") == "ticker" and "data" in payload:
                                quote = self._parse_ticker_payload(payload["data"], epoch)
                                if quote and quote.is_valid:
                                    self.transport_state = "CONNECTED"
                                    self.feed_source = "WS"
                                    attempt = 0
                                    self._dispatch_quote(quote)

                    finally:
                        heartbeat_task.cancel()

            except Exception as e:
                self.last_error = str(e)
                logger.warning(f"Bitget WS connection error (epoch {epoch}): {e}")

            # Connection lost or error
            self.transport_state = "DEGRADED"
            self.reconnect_count += 1
            attempt += 1

            if self._running:
                delay = min(30.0, 1.0 * (1.8 ** min(attempt, 6))) + random.uniform(0.1, 0.8)
                logger.info(f"Reconnecting in {delay:.1f}s (attempt {attempt})...")
                await asyncio.sleep(delay)

        self.transport_state = "DISCONNECTED"

    def stop(self):
        self._running = False
        if self._ws_client:
            asyncio.create_task(self._ws_client.close())
        if self._rest_fallback_task:
            self._rest_fallback_task.cancel()

bitget_ws_service = BitgetWSService()
