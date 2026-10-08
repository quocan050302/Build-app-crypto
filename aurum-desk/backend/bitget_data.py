import time
from typing import List, Dict, Any, Optional, Tuple
import httpx
import schemas

BITGET_CANDLES_URL = "https://api.bitget.com/api/v2/mix/market/candles"
BITGET_TICKER_URL = "https://api.bitget.com/api/v2/mix/market/ticker"
BITGET_CONTRACTS_URL = "https://api.bitget.com/api/v2/mix/market/contracts"

TIMEFRAME_MAP = {
    "1M": ("1m", 60 * 1000),
    "5M": ("5m", 5 * 60 * 1000),
    "15M": ("15m", 15 * 60 * 1000),
    "1H": ("1H", 60 * 60 * 1000),
    "4H": ("4H", 4 * 60 * 60 * 1000),
    "D": ("1D", 24 * 60 * 60 * 1000)
}

class BitgetDataError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def validate_timeframe(timeframe: str) -> Tuple[str, int]:
    """Validate timeframe enum; reject unknown timeframes with typed error."""
    if timeframe not in TIMEFRAME_MAP:
        raise BitgetDataError(
            f"Khung thời gian '{timeframe}' không hợp lệ. Hỗ trợ: {list(TIMEFRAME_MAP.keys())}",
            status_code=400
        )
    return TIMEFRAME_MAP[timeframe]


def _parse_ticker_data(symbol: str, data: dict) -> Dict[str, Any]:
    if data.get("code") != "00000":
        raise BitgetDataError(f"Bitget ticker error: {data.get('msg')}")

    items = data.get("data", [])
    if not items:
        raise BitgetDataError(f"Không có dữ liệu ticker cho symbol {symbol}")

    ticker_item = items[0]
    server_time = int(data.get("requestTime", int(time.time() * 1000)))
    return {
        "symbol": symbol,
        "last": float(ticker_item.get("lastPr", 0.0)),
        "bid": float(ticker_item.get("bidPr", 0.0)),
        "ask": float(ticker_item.get("askPr", 0.0)),
        "mark_price": float(ticker_item.get("markPrice", ticker_item.get("lastPr", 0.0))),
        "index_price": float(ticker_item.get("indexPrice", 0.0)),
        "funding_rate": float(ticker_item.get("fundingRate", 0.0)),
        "high_24h": float(ticker_item.get("high24h", 0.0)),
        "low_24h": float(ticker_item.get("low24h", 0.0)),
        "server_time": server_time
    }


def _parse_candles_data(
    symbol: str,
    timeframe: str,
    duration_ms: int,
    data: dict
) -> Tuple[List[schemas.CandleCreate], int]:
    if data.get("code") != "00000":
        raise BitgetDataError(f"Lỗi từ Bitget API: {data.get('msg')} (code: {data.get('code')})")

    server_time = int(data.get("requestTime", int(time.time() * 1000)))
    raw_candles = data.get("data", [])

    validated_candles: List[schemas.CandleCreate] = []
    for row in raw_candles:
        try:
            ts = int(row[0])
            o = float(row[1])
            h = float(row[2])
            l = float(row[3])
            c = float(row[4])
            vol = float(row[5]) if len(row) > 5 else 0.0

            # Validate finite and logical OHLC
            if not (l <= o <= h and l <= c <= h and h >= l and l > 0):
                continue

            # Candle is closed only if server_time >= ts + duration_ms
            is_closed = (server_time >= ts + duration_ms)

            validated_candles.append(
                schemas.CandleCreate(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts,
                    open=round(o, 2),
                    high=round(h, 2),
                    low=round(l, 2),
                    close=round(c, 2),
                    volume=round(vol, 4),
                    is_closed=is_closed
                )
            )
        except (ValueError, TypeError, IndexError):
            continue

    validated_candles.sort(key=lambda x: x.timestamp)
    return validated_candles, server_time


def fetch_ticker(symbol: str = "XAUUSDT") -> Dict[str, Any]:
    """Synchronous fetch live ticker."""
    params = {"symbol": symbol, "productType": "USDT-FUTURES"}
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(BITGET_TICKER_URL, params=params)
            resp.raise_for_status()
            return _parse_ticker_data(symbol, resp.json())
    except httpx.RequestError as e:
        raise BitgetDataError(f"Lỗi kết nối Bitget Ticker: {str(e)}", status_code=503)


async def async_fetch_ticker(client: httpx.AsyncClient, symbol: str = "XAUUSDT") -> Dict[str, Any]:
    """Asynchronous fetch live ticker without blocking event loop."""
    params = {"symbol": symbol, "productType": "USDT-FUTURES"}
    try:
        resp = await client.get(BITGET_TICKER_URL, params=params, timeout=6.0)
        resp.raise_for_status()
        return _parse_ticker_data(symbol, resp.json())
    except httpx.RequestError as e:
        raise BitgetDataError(f"Lỗi kết nối Bitget Ticker async: {str(e)}", status_code=503)


def fetch_candles(
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    limit: int = 150
) -> Tuple[List[schemas.CandleCreate], int]:
    """Synchronous fetch candles from Bitget REST API."""
    granularity, duration_ms = validate_timeframe(timeframe)
    params = {
        "symbol": symbol,
        "productType": "USDT-FUTURES",
        "granularity": granularity,
        "limit": min(max(limit, 10), 200)
    }
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(BITGET_CANDLES_URL, params=params)
            resp.raise_for_status()
            return _parse_candles_data(symbol, timeframe, duration_ms, resp.json())
    except httpx.RequestError as e:
        raise BitgetDataError(f"Không thể kết nối đến Bitget API: {str(e)}", status_code=503)


async def async_fetch_candles(
    client: httpx.AsyncClient,
    symbol: str = "XAUUSDT",
    timeframe: str = "15M",
    limit: int = 150
) -> Tuple[List[schemas.CandleCreate], int]:
    """Asynchronous fetch candles without blocking event loop."""
    granularity, duration_ms = validate_timeframe(timeframe)
    params = {
        "symbol": symbol,
        "productType": "USDT-FUTURES",
        "granularity": granularity,
        "limit": min(max(limit, 10), 200)
    }
    try:
        resp = await client.get(BITGET_CANDLES_URL, params=params, timeout=8.0)
        resp.raise_for_status()
        return _parse_candles_data(symbol, timeframe, duration_ms, resp.json())
    except httpx.RequestError as e:
        raise BitgetDataError(f"Không thể kết nối đến Bitget API async: {str(e)}", status_code=503)
