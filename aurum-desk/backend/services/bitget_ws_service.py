import json
import time
import asyncio
import random
import logging
import ssl
import certifi
from typing import Optional, Dict, Any, List
import httpx
import websockets
from services.quote_validator import CanonicalQuote, QuoteValidator
from services.execution_consumer import execution_consumer
from services.candle_cache_service import candle_cache_service

logger = logging.getLogger(__name__)

BITGET_WS_URL = "wss://ws.bitget.com/v2/ws/public"
BITGET_REST_TICKER_URL = "https://api.bitget.com/api/v2/mix/market/ticker"

# Case-sensitive Bitget Classic channel mappings:
# 1M -> candle1m (candle1M is monthly), 5M -> candle5m, 15M -> candle15m, 1H -> candle1H, 4H -> candle4H, D -> candle1D
CANDLE_CHANNELS: Dict[str, str] = {
    "candle1m": "1M",
    "candle5m": "5M",
    "candle15m": "15M",
    "candle1H": "1H",
    "candle4H": "4H",
    "candle1D": "D"
}

ALL_SUBSCRIBE_CHANNELS: List[str] = ["ticker"] + list(CANDLE_CHANNELS.keys())


class BitgetWSService:
    """
    Bitget Public WebSocket Adapter (V7.2):
    - Canonical Market Feed Owner for XAUUSDT (USDT-FUTURES).
    - Subscribes to Classic v2 ticker channel + 6 multi-timeframe candle channels:
      (candle1m, candle5m, candle15m, candle1H, candle4H, candle1D).
    - Tracks per-channel subscription registry (PENDING / ACKED / FAILED).
    - Supports Bitget Classic ack format (success with code=None or code=0/"0").
    - Handles text ping/pong heartbeats every 25s with 10s pong timeout.
    - Decoupled heartbeat starts immediately upon socket opening.
    - Dispatches real multi-row OHLCV candle streams to candle_cache_service.
    - Controlled, single-flight REST fallback using item.ts (not requestTime) for quote age.
    - Exponential backoff with jitter and 30s cap for auto-reconnection.
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
        self.last_ping_time = 0.0
        self.last_pong_time = 0.0
        self.last_error: Optional[str] = None

        # Per-channel subscription registry: channel -> "PENDING" | "ACKED" | "FAILED"
        self.channel_registry: Dict[str, str] = {ch: "PENDING" for ch in ALL_SUBSCRIBE_CHANNELS}

        self._running = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._rest_fallback_task: Optional[asyncio.Task] = None
        self._ws_client = None
        self._broadcaster_callback = None

    def register_broadcaster(self, callback):
        self._broadcaster_callback = callback

    def is_channel_acked(self, channel: str) -> bool:
        return self.channel_registry.get(channel) == "ACKED"

    def all_channels_acked(self) -> bool:
        return all(status == "ACKED" for status in self.channel_registry.values())

    def _reset_registry(self):
        for ch in ALL_SUBSCRIBE_CHANNELS:
            self.channel_registry[ch] = "PENDING"

    def handle_ack(self, payload: Dict[str, Any]) -> bool:
        """
        Validates Bitget subscription ack payload:
        Format: {"event": "subscribe", "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "XAUUSDT"}}
        Classic v2 subscribe success does not require 'code' field, or code is 0 / "0".
        """
        event = payload.get("event")
        arg = payload.get("arg", {})
        ch = arg.get("channel")
        inst = arg.get("instId")
        code = payload.get("code")

        if event == "subscribe":
            if ch in self.channel_registry and (inst == self.symbol or inst is None):
                # Valid ack: code is None or 0 or "0"
                if code is None or code == 0 or str(code) == "0":
                    self.channel_registry[ch] = "ACKED"
                    logger.info(f"Bitget subscription acked for channel {ch} ({inst or self.symbol})")
                    if self.channel_registry.get("ticker") == "ACKED":
                        self.transport_state = "CONNECTED"
                        self.feed_source = "WS"
                    return True
                else:
                    self.channel_registry[ch] = "FAILED"
                    self.last_error = f"Subscription failed for {ch}: code={code}"
                    logger.error(self.last_error)
                    return False
        elif event == "error":
            if ch in self.channel_registry:
                self.channel_registry[ch] = "FAILED"
            self.last_error = f"Bitget error: {payload.get('msg', payload)}"
            logger.error(self.last_error)
            return False

        return False

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

        # 2. Candle Cache (Pure display check; does not synthesize synthetic bars)
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
                            # Authoritative quote timestamp from item.ts
                            ex_ts = int(item.get("ts")) if item.get("ts") is not None else None
                            raw = {
                                "symbol": self.symbol,
                                "bid": item.get("bidPr"),
                                "ask": item.get("askPr"),
                                "last": item.get("lastPr"),
                                "mark_price": item.get("markPrice"),
                                "exchange_ts_ms": ex_ts,
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

    async def _heartbeat_loop(self, ws, ping_interval: float = 25.0, pong_timeout: float = 10.0):
        """
        Bitget public WS heartbeat: sends text 'ping' every ping_interval.
        Expects text 'pong' within pong_timeout.
        Runs for the lifetime of the socket connection (starts immediately).
        """
        logger.debug("Bitget WS heartbeat loop started.")
        while self._running:
            try:
                await asyncio.sleep(ping_interval)
                if not self._running:
                    break

                self.last_ping_time = time.time()
                await ws.send("ping")

                # Allow pong_timeout for pong response
                await asyncio.sleep(pong_timeout)
                if not self._running:
                    break

                # If no pong received since ping was sent
                if self.last_pong_time < self.last_ping_time:
                    logger.warning(
                        f"Bitget WS pong timeout: no pong received within {pong_timeout}s. Closing socket to reconnect."
                    )
                    self.last_error = "PONG_TIMEOUT"
                    await ws.close()
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Heartbeat loop exception: {e}")
                break

    async def run_ws_loop(self):
        """Main WebSocket loop with multi-channel subscriptions, backoff, and fallback."""
        self._running = True
        attempt = 0

        while self._running:
            self.connection_epoch += 1
            epoch = self.connection_epoch
            self.transport_state = "CONNECTING"
            self._reset_registry()

            # Start REST fallback if not running
            if not self._rest_fallback_task or self._rest_fallback_task.done():
                self._rest_fallback_task = asyncio.create_task(self._run_rest_fallback())

            heartbeat_task: Optional[asyncio.Task] = None
            try:
                logger.info(f"Connecting to Bitget WebSocket ({self.ws_url}), epoch {epoch}...")
                ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                async with websockets.connect(
                    self.ws_url,
                    ssl=ssl_ctx,
                    ping_interval=None,  # We manage Bitget's application-level text ping
                    close_timeout=5.0
                ) as ws:
                    self._ws_client = ws

                    # 1. Send subscription for ticker + 6 multi-timeframe candle channels
                    sub_args = [
                        {"instType": "USDT-FUTURES", "channel": ch, "instId": self.symbol}
                        for ch in ALL_SUBSCRIBE_CHANNELS
                    ]
                    sub_msg = {
                        "op": "subscribe",
                        "args": sub_args
                    }
                    await ws.send(json.dumps(sub_msg))

                    # 2. Start application text ping heartbeat immediately
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

                    try:
                        async for msg in ws:
                            if not self._running:
                                break

                            if msg == "pong":
                                self.last_pong_time = time.time()
                                self.last_heartbeat_time = int(time.time() * 1000)
                                continue

                            try:
                                payload = json.loads(msg)
                            except Exception:
                                continue

                            # 3. Handle subscription ack or error events
                            if "event" in payload:
                                self.handle_ack(payload)
                                continue

                            # 4. Handle stream data payloads
                            arg = payload.get("arg", {})
                            ch = arg.get("channel")
                            action = payload.get("action", "update")
                            data = payload.get("data")

                            if ch == "ticker" and data:
                                quote = self._parse_ticker_payload(data, epoch)
                                if quote and quote.is_valid:
                                    self.transport_state = "CONNECTED"
                                    self.feed_source = "WS"
                                    attempt = 0
                                    self._dispatch_quote(quote)

                            elif ch in CANDLE_CHANNELS and data:
                                tf = CANDLE_CHANNELS[ch]
                                candle_cache_service.process_candle_payload(
                                    symbol=self.symbol,
                                    timeframe=tf,
                                    data_rows=data,
                                    action=action
                                )
                                self.transport_state = "CONNECTED"
                                self.feed_source = "WS"
                                attempt = 0

                    finally:
                        if heartbeat_task and not heartbeat_task.done():
                            heartbeat_task.cancel()
                            try:
                                await heartbeat_task
                            except (asyncio.CancelledError, Exception):
                                pass

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
        if self._rest_fallback_task and not self._rest_fallback_task.done():
            self._rest_fallback_task.cancel()

bitget_ws_service = BitgetWSService()
