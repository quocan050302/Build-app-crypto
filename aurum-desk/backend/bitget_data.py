import httpx
import time
import schemas
from typing import List

BITGET_REST_URL = "https://api.bitget.com/api/v2/mix/market/candles"

def fetch_candles(symbol: str, timeframe: str, limit: int = 100) -> List[schemas.CandleCreate]:
    # timeframe in Bitget: 1m, 5m, 15m, 1H, 4H, 1D
    # XAUUSDT is symbol
    # granularity for Bitget: 1m, 5m, 15m, 1H, 4H, 1D (case sensitive)
    
    # Map our timeframes to Bitget granularity
    tf_map = {
        "1M": "1m",
        "5M": "5m",
        "15M": "15m",
        "1H": "1H",
        "4H": "4H",
        "D": "1D"
    }
    granularity = tf_map.get(timeframe, "15m")
    
    params = {
        "symbol": symbol,
        "productType": "USDT-FUTURES",
        "granularity": granularity,
        "limit": limit
    }
    
    try:
        response = httpx.get(BITGET_REST_URL, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        
        if data.get("code") != "00000":
            print(f"Error fetching candles: {data.get('msg')}")
            return []
            
        candles = []
        for row in data.get("data", []):
            # row: [timestamp, open, high, low, close, volume, quote_volume]
            candles.append(
                schemas.CandleCreate(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    is_closed=True # We assume historical candles are closed
                )
            )
        return candles
    except Exception as e:
        print(f"Request failed: {e}")
        return []
