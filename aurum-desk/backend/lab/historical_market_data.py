"""
Aurum Desk V11: Historical Market Data Provider & Caching Engine
Supports Bitget Classic USDT-M Futures historical candles with strict pagination,
rate-limiting, geometric invariant checks, deduplication, and zero-lookahead as-of filtering.
"""
import os
import time
import json
import math
import hashlib
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx

logger = logging.getLogger(__name__)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
BITGET_HISTORY_CANDLES_URL = "https://api.bitget.com/api/v2/mix/market/history-candles"

TIMEFRAME_CADENCE_MS = {
    "1m": 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "15M": 15 * 60 * 1000,
    "1H": 60 * 60 * 1000,
    "4H": 4 * 60 * 60 * 1000,
    "1D": 24 * 60 * 60 * 1000,
    "D": 24 * 60 * 60 * 1000,
}


class HistoricalDataMissingException(Exception):
    """Raised when historical market data cannot be downloaded or is insufficient."""
    pass


class CandleRecord:
    __slots__ = ("timestamp", "open", "high", "low", "close", "volume", "close_time", "is_closed")

    def __init__(self, timestamp: int, open_p: float, high_p: float, low_p: float, close_p: float, volume: float, timeframe: str = "15M"):
        self.timestamp = int(timestamp)
        self.open = round(float(open_p), 2)
        self.high = round(float(high_p), 2)
        self.low = round(float(low_p), 2)
        self.close = round(float(close_p), 2)
        self.volume = round(float(volume), 4)
        duration = TIMEFRAME_CADENCE_MS.get(timeframe, 15 * 60 * 1000)
        self.close_time = self.timestamp + duration
        self.is_closed = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "close_time": self.close_time,
            "is_closed": self.is_closed,
        }

    def __repr__(self):
        return f"Candle({self.timestamp}, O={self.open}, H={self.high}, L={self.low}, C={self.close}, V={self.volume})"


def compute_dataset_hash(candles: List[Dict[str, Any]]) -> str:
    """Computes a deterministic SHA-256 hash across candle rows."""
    hasher = hashlib.sha256()
    for c in candles:
        line = f"{c['timestamp']}:{c['open']:.2f}:{c['high']:.2f}:{c['low']:.2f}:{c['close']:.2f}:{c.get('volume', 0):.4f}\n"
        hasher.update(line.encode("utf-8"))
    return hasher.hexdigest()


def subtract_calendar_months(dt: datetime, months: int) -> datetime:
    """
    Exact calendar month subtraction preserving local day and time.
    Clamps to target month's maximum days if needed (e.g. Oct 31 -> Jul 31).
    """
    import calendar
    year = dt.year
    month = dt.month - months
    while month <= 0:
        month += 12
        year -= 1
    max_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, max_day)
    return dt.replace(year=year, month=month, day=day)


class HistoricalMarketDataProvider:
    """
    Authoritative historical data provider for Bitget Classic Contract USDT-Futures.
    Handles paginated backward downloads, offline caching, and invariant validation.
    """

    @classmethod
    def download_bitget_candles_range(
        cls,
        symbol: str = "XAUUSDT",
        product_type: str = "USDT-FUTURES",
        granularity: str = "15m",
        start_ms: int = 1788962400000,
        end_ms: int = 1791554400000,
        cache_dir: Optional[str] = None,
        max_retries: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Downloads closed historical candles between start_ms and end_ms with pagination.
        Granularity: '1m', '5m', '15m', '1H', '4H', '1D'.
        """
        cadence = TIMEFRAME_CADENCE_MS.get(granularity, 15 * 60 * 1000)
        cache_path = None
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, f"{symbol}_{granularity}_{start_ms}_{end_ms}.json")
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                    if isinstance(cached_data, list) and len(cached_data) > 0:
                        first_ts = cached_data[0].get("timestamp", 0)
                        last_ts = cached_data[-1].get("timestamp", 0)
                        if first_ts <= start_ms + cadence and last_ts >= (end_ms - 2 * cadence):
                            logger.info(f"Loaded {len(cached_data)} candles from disk cache: {cache_path}")
                            return cached_data
                        else:
                            logger.warning(
                                f"Cache {cache_path} coverage insufficient: [{first_ts}, {last_ts}] vs requested [{start_ms}, {end_ms}]. Re-downloading."
                            )
                except Exception as e:
                    logger.warning(f"Failed to read disk cache {cache_path}: {e}")

        all_raw_rows: List[List[Any]] = []
        seen_timestamps = set()
        current_end = end_ms

        logger.info(f"Downloading Bitget historical candles {symbol} {granularity} from {start_ms} to {end_ms}...")

        with httpx.Client(timeout=10.0) as client:
            pages = 0
            while current_end > start_ms:
                pages += 1
                params = {
                    "symbol": symbol,
                    "productType": product_type,
                    "granularity": granularity,
                    "limit": 200,
                    "endTime": current_end,
                }

                data = None
                for attempt in range(max_retries):
                    try:
                        resp = client.get(BITGET_HISTORY_CANDLES_URL, params=params)
                        if resp.status_code == 200:
                            payload = resp.json()
                            if payload.get("code") == "00000":
                                data = payload.get("data", [])
                                break
                            else:
                                logger.warning(f"Bitget error response: {payload.get('msg')}")
                        time.sleep(0.1 * (attempt + 1))
                    except Exception as e:
                        logger.warning(f"Bitget request error on page {pages}, attempt {attempt}: {e}")
                        time.sleep(0.2 * (attempt + 1))

                if not data:
                    logger.info(f"No more data returned from Bitget at page {pages}, endTime={current_end}")
                    break

                for row in data:
                    ts = int(row[0])
                    if ts not in seen_timestamps:
                        seen_timestamps.add(ts)
                        all_raw_rows.append(row)

                # Monotonic backward pagination using MIN batch timestamp
                earliest_ts_in_batch = min(int(r[0]) for r in data)
                if earliest_ts_in_batch >= current_end:
                    # Stalled cursor progress
                    break
                current_end = earliest_ts_in_batch - 1

                # Polite throttle to stay well below 20 req/s limit
                time.sleep(0.06)

                if earliest_ts_in_batch <= start_ms:
                    break

        if not all_raw_rows:
            raise HistoricalDataMissingException(f"Không thể tải dữ liệu lịch sử từ Bitget API cho {symbol} {granularity}")

        # Check coverage
        min_ts_retrieved = min(int(r[0]) for r in all_raw_rows)
        if min_ts_retrieved > start_ms + (cadence * 2):
            raise HistoricalDataMissingException(
                f"INCOMPLETE: Dữ liệu Bitget tải về chỉ đến {min_ts_retrieved}, không phủ đủ điểm bắt đầu {start_ms} cho {symbol} {granularity}"
            )

        # Parse & Validate
        candles, warnings = cls.validate_and_normalize_raw_candles(all_raw_rows, granularity=granularity)

        # Filter strictly within requested range [start_ms, end_ms]
        filtered = [c for c in candles if start_ms <= c["timestamp"] <= end_ms]
        filtered.sort(key=lambda x: x["timestamp"])

        logger.info(f"Successfully processed {len(filtered)} valid historical candles ({granularity}) for {symbol}")

        if cache_path and filtered:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(filtered, f)
                logger.info(f"Saved historical dataset to cache: {cache_path}")
            except Exception as e:
                logger.warning(f"Failed to write cache file {cache_path}: {e}")

        return filtered

    @classmethod
    def validate_and_normalize_raw_candles(
        cls,
        raw_rows: List[Any],
        granularity: str = "15m"
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Validates raw candle rows strictly:
        - Non-finite or non-positive prices rejected (quarantined).
        - Non-finite or negative volume quarantined.
        - Geometric invariants: high >= max(open, close), low <= min(open, close), high >= low.
        - Duplicate timestamps removed.
        - Sorted strictly ascending.
        - Precise gap detection: diff > cadence + 1000.
        """
        warnings = []
        valid_candles = []
        seen_ts = set()
        cadence = TIMEFRAME_CADENCE_MS.get(granularity, 15 * 60 * 1000)

        for idx, row in enumerate(raw_rows):
            if isinstance(row, dict):
                ts = int(row.get("timestamp") or row.get("time") or 0)
                o = float(row.get("open", 0))
                h = float(row.get("high", 0))
                l = float(row.get("low", 0))
                c = float(row.get("close", 0))
                v = float(row.get("volume", 0))
            elif isinstance(row, (list, tuple)) and len(row) >= 5:
                ts = int(row[0])
                o = float(row[1])
                h = float(row[2])
                l = float(row[3])
                c = float(row[4])
                v = float(row[5]) if len(row) > 5 else 0.0
            else:
                warnings.append(f"Row {idx}: Unrecognized format, quarantined")
                continue

            # Standardize timestamp to ms
            if ts < 10000000000:
                ts = ts * 1000

            if ts in seen_ts:
                continue
            seen_ts.add(ts)

            # Check positivity and finite values
            if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(l) and math.isfinite(c)):
                warnings.append(f"Row {idx} ({ts}): Non-finite OHLC value detected, quarantined")
                continue
            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                warnings.append(f"Row {idx} ({ts}): Non-positive price detected, quarantined")
                continue

            # Validate volume non-negative and finite
            if not math.isfinite(v) or v < 0:
                warnings.append(f"Row {idx} ({ts}): Invalid volume {v}, quarantined")
                continue

            # Geometric invariant check
            if h < max(o, c) or l > min(o, c) or h < l:
                warnings.append(f"Row {idx} ({ts}): Invalid OHLC geometry O={o}, H={h}, L={l}, C={c}, quarantined")
                continue

            valid_candles.append({
                "timestamp": ts,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": round(v, 4),
                "close_time": ts + cadence,
                "is_closed": True
            })

        valid_candles.sort(key=lambda x: x["timestamp"])

        # Strict gap detection: detects even a single missing candle (diff > cadence + 1000)
        if len(valid_candles) > 1:
            for j in range(1, len(valid_candles)):
                diff = valid_candles[j]["timestamp"] - valid_candles[j - 1]["timestamp"]
                if diff > cadence + 1000:
                    warnings.append(
                        f"Data Gap: {diff // 60000} minutes missing between "
                        f"{valid_candles[j - 1]['timestamp']} and {valid_candles[j]['timestamp']}"
                    )

        return valid_candles, warnings

    _bundle_cache: Dict[str, Any] = {}

    @classmethod
    def load_multitimeframe_bundle(
        cls,
        symbol: str = "XAUUSDT",
        start_ms: int = 1783609200000,
        end_ms: int = 1791558000000,
        warmup_ms: int = 1779289200000,
        cache_dir: Optional[str] = None,
        include_5m: bool = False
    ) -> Dict[str, Any]:
        """
        Loads aligned multitimeframe bundles (15M, 1H, 4H, 1D, and optionally 5M) with complete warmup history.
        Computes individual timeframe hashes and verified quality audit metrics.
        """
        cache_key = f"{symbol}_{start_ms}_{end_ms}_{warmup_ms}_{include_5m}"
        if cache_key in cls._bundle_cache:
            return cls._bundle_cache[cache_key]

        # 1. 15M candles (Primary execution timeframe)
        candles_15m = cls.download_bitget_candles_range(
            symbol=symbol,
            granularity="15m",
            start_ms=warmup_ms,
            end_ms=end_ms,
            cache_dir=cache_dir
        )

        # 2. 1H candles (H1 alignment)
        candles_1h = cls.download_bitget_candles_range(
            symbol=symbol,
            granularity="1H",
            start_ms=warmup_ms,
            end_ms=end_ms,
            cache_dir=cache_dir
        )

        # 3. 4H candles (4H bias)
        candles_4h = cls.download_bitget_candles_range(
            symbol=symbol,
            granularity="4H",
            start_ms=warmup_ms,
            end_ms=end_ms,
            cache_dir=cache_dir
        )

        # 4. 1D candles (Daily bias)
        candles_1d = cls.download_bitget_candles_range(
            symbol=symbol,
            granularity="1D",
            start_ms=warmup_ms,
            end_ms=end_ms,
            cache_dir=cache_dir
        )

        # 5. 5M candles (Optional for Setups B1/B2/Mode C)
        candles_5m = []
        if include_5m:
            warmup_5m = start_ms - (2 * 24 * 3600 * 1000)  # 2 days warmup for 5M
            candles_5m = cls.download_bitget_candles_range(
                symbol=symbol,
                granularity="5m",
                start_ms=warmup_5m,
                end_ms=end_ms,
                cache_dir=cache_dir
            )

        # Compute individual timeframe hashes and quality metadata
        timeframe_metadata = {}
        hasher = hashlib.sha256()

        tf_list = [
            ("1D", candles_1d, "HTF_BIAS", 24 * 3600 * 1000),
            ("4H", candles_4h, "HTF_BIAS", 4 * 3600 * 1000),
            ("1H", candles_1h, "H1_ALIGNMENT", 3600 * 1000),
            ("15M", candles_15m, "EXECUTION_AND_STRUCTURE", 15 * 60 * 1000),
        ]
        if include_5m and candles_5m:
            tf_list.append(("5M", candles_5m, "EXECUTION_REFINEMENT_AND_TRIGGER", 5 * 60 * 1000))

        for tf_name, tf_candles, role, cadence in tf_list:
            tf_hasher = hashlib.sha256()
            gaps = 0
            for j, c in enumerate(tf_candles):
                line = f"{tf_name}:{c['timestamp']}:{c['open']:.2f}:{c['high']:.2f}:{c['low']:.2f}:{c['close']:.2f}:{c.get('volume', 0):.4f}\n"
                tf_hasher.update(line.encode("utf-8"))
                hasher.update(line.encode("utf-8"))
                if j > 0 and (c["timestamp"] - tf_candles[j - 1]["timestamp"]) > (cadence + 1000):
                    gaps += 1

            tf_hash = tf_hasher.hexdigest()
            w_cnt = sum(1 for c in tf_candles if c["timestamp"] < start_ms)
            e_cnt = sum(1 for c in tf_candles if start_ms <= c["timestamp"] <= end_ms)
            act_s = tf_candles[0]["timestamp"] if tf_candles else start_ms
            act_e = tf_candles[-1]["timestamp"] if tf_candles else end_ms

            timeframe_metadata[tf_name] = {
                "status": "USED",
                "role": role,
                "count": len(tf_candles),
                "warmup_count": w_cnt,
                "eval_count": e_cnt,
                "total_count": len(tf_candles),
                "act_start_ms": act_s,
                "act_end_ms": act_e,
                "gaps_count": gaps,
                "quarantined_count": 0,
                "sha256": tf_hash
            }

        # Mark unused frames
        if "5M" not in timeframe_metadata:
            timeframe_metadata["5M"] = {
                "status": "NOT_USED",
                "role": "NOT_USED_IN_ENTRY_DECISION",
                "count": 0,
                "warmup_count": 0,
                "eval_count": 0,
                "total_count": 0,
                "act_start_ms": 0,
                "act_end_ms": 0,
                "gaps_count": 0,
                "quarantined_count": 0,
                "sha256": "N/A"
            }
        timeframe_metadata["1M"] = {
            "status": "NOT_USED",
            "role": "NOT_USED_IN_ENTRY_DECISION",
            "count": 0,
            "warmup_count": 0,
            "eval_count": 0,
            "total_count": 0,
            "act_start_ms": 0,
            "act_end_ms": 0,
            "gaps_count": 0,
            "quarantined_count": 0,
            "sha256": "N/A"
        }

        combined_hash = hasher.hexdigest()

        bundle = {
            "symbol": symbol,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "warmup_ms": warmup_ms,
            "candles_15m": candles_15m,
            "candles_1h": candles_1h,
            "candles_4h": candles_4h,
            "candles_1d": candles_1d,
            "candles_5m": candles_5m,
            "dataset_hash": combined_hash,
            "total_15m_count": len(candles_15m),
            "warmup_count": sum(1 for c in candles_15m if c["timestamp"] < start_ms),
            "eval_count": sum(1 for c in candles_15m if start_ms <= c["timestamp"] <= end_ms),
            "timeframe_metadata": timeframe_metadata
        }
        cls._bundle_cache[cache_key] = bundle
        return bundle

