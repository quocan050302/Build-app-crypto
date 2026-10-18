#!/usr/bin/env python3
"""
tools/download_v16_27_m1_dataset.py
Downloads and strictly validates authoritative Bitget historical M1 candles for XAUUSDT.
Coverage:
- 30-day: 2026-09-18T00:00:00Z (1789689600000) to 2026-10-18T00:00:00Z (1792281600000)
- 14-day: 2026-10-04T00:00:00Z (1791072000000) to 2026-10-18T00:00:00Z (1792281600000)

Saves normalized datasets and metadata to research_artifacts/cg01_v16_27_baseline_20261018/data/
Zero modifications to production source code.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
import httpx

BITGET_HISTORY_CANDLES_URL = "https://api.bitget.com/api/v2/mix/market/history-candles"
SYMBOL = "XAUUSDT"
PRODUCT_TYPE = "USDT-FUTURES"
GRANULARITY = "1m"
CADENCE_MS = 60_000

# 30-day UTC: 2026-09-18T00:00:00Z to 2026-10-18T00:00:00Z
T30_START_MS = 1789689600000
# 14-day UTC: 2026-10-04T00:00:00Z to 2026-10-18T00:00:00Z
T14_START_MS = 1791072000000
# End exclusive: 2026-10-18T00:00:00Z
T_END_EXCLUSIVE_MS = 1792281600000
# Last closed bar has open_time = T_END_EXCLUSIVE_MS - CADENCE_MS
T_LAST_BAR_OPEN_MS = T_END_EXCLUSIVE_MS - CADENCE_MS

DEST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research_artifacts",
    "cg01_v16_27_baseline_20261018",
    "data"
)


def compute_sha256(candles: List[Dict[str, Any]]) -> str:
    hasher = hashlib.sha256()
    for c in candles:
        line = f"{c['open_time']}:{c['open']:.2f}:{c['high']:.2f}:{c['low']:.2f}:{c['close']:.2f}:{c.get('volume', 0.0):.4f}\n"
        hasher.update(line.encode("utf-8"))
    return hasher.hexdigest()


def download_m1_candles(start_ms: int, end_exclusive_ms: int) -> List[List[Any]]:
    current_end = end_exclusive_ms
    all_raw: List[List[Any]] = []
    seen_ts = set()
    pages = 0

    print(f"Downloading Bitget M1 candles for {SYMBOL} from {start_ms} to {end_exclusive_ms}...")
    with httpx.Client(timeout=15.0) as client:
        while current_end > start_ms:
            pages += 1
            params = {
                "symbol": SYMBOL,
                "productType": PRODUCT_TYPE,
                "granularity": GRANULARITY,
                "limit": 200,
                "endTime": current_end,
            }

            data = None
            for attempt in range(4):
                try:
                    resp = client.get(BITGET_HISTORY_CANDLES_URL, params=params)
                    if resp.status_code == 200:
                        payload = resp.json()
                        if payload.get("code") == "00000":
                            data = payload.get("data", [])
                            break
                        else:
                            print(f"Warning: Bitget code {payload.get('code')}: {payload.get('msg')}")
                    time.sleep(0.1 * (attempt + 1))
                except Exception as e:
                    print(f"Warning: retry {attempt} on page {pages}: {e}")
                    time.sleep(0.2 * (attempt + 1))

            if not data:
                print(f"No more data at page {pages}, current_end={current_end}")
                break

            for row in data:
                ts = int(row[0])
                if ts not in seen_ts:
                    seen_ts.add(ts)
                    all_raw.append(row)

            earliest = min(int(r[0]) for r in data)
            if earliest >= current_end:
                break
            current_end = earliest - 1

            if pages % 20 == 0:
                dt_str = datetime.fromtimestamp(earliest / 1000, tz=timezone.utc).isoformat()
                print(f"  Page {pages:3d}: reached {dt_str} ({len(seen_ts)} unique bars collected)")

            time.sleep(0.06)

            if earliest <= start_ms:
                break

    print(f"Download complete: {len(all_raw)} raw rows across {pages} pages.")
    return all_raw


def validate_and_normalize(
    raw_rows: List[Any],
    start_ms: int,
    end_exclusive_ms: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    identical_dups = 0
    conflicting_dups = 0
    invalid_geometry = 0
    non_finite_count = 0
    seen_map: Dict[int, Dict[str, Any]] = {}
    valid_candles: List[Dict[str, Any]] = []

    for r in raw_rows:
        try:
            ts = int(r[0])
            o = float(r[1])
            h = float(r[2])
            l = float(r[3])
            c = float(r[4])
            v = float(r[5]) if len(r) > 5 else 0.0
        except Exception:
            non_finite_count += 1
            continue

        if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(l) and math.isfinite(c) and math.isfinite(v)):
            non_finite_count += 1
            continue

        if o <= 0 or h <= 0 or l <= 0 or c <= 0 or v < 0:
            non_finite_count += 1
            continue

        if h < l or o < (l - 1e-4) or o > (h + 1e-4) or c < (l - 1e-4) or c > (h + 1e-4) or ts <= 0:
            invalid_geometry += 1
            continue

        candle_dict = {
            "open_time": ts,
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": round(v, 4),
            "is_closed": True,
        }

        if ts in seen_map:
            prev = seen_map[ts]
            if (prev["open"] == candle_dict["open"] and prev["high"] == candle_dict["high"] and
                prev["low"] == candle_dict["low"] and prev["close"] == candle_dict["close"]):
                identical_dups += 1
            else:
                conflicting_dups += 1
            continue

        seen_map[ts] = candle_dict

    # Filter strictly within [start_ms, end_exclusive_ms)
    filtered = [
        c for ts, c in seen_map.items()
        if start_ms <= ts < end_exclusive_ms
    ]
    filtered.sort(key=lambda x: x["open_time"])

    # Gap detection
    gaps: List[Dict[str, Any]] = []
    if len(filtered) > 1:
        for i in range(1, len(filtered)):
            diff = filtered[i]["open_time"] - filtered[i - 1]["open_time"]
            if diff > (CADENCE_MS + 1000):
                gap_mins = diff // 60_000
                from_ts = filtered[i - 1]["open_time"]
                to_ts = filtered[i]["open_time"]
                from_dt = datetime.fromtimestamp(from_ts / 1000, tz=timezone.utc).isoformat()
                to_dt = datetime.fromtimestamp(to_ts / 1000, tz=timezone.utc).isoformat()
                gaps.append({
                    "from_timestamp": from_ts,
                    "to_timestamp": to_ts,
                    "from_datetime_utc": from_dt,
                    "to_datetime_utc": to_dt,
                    "gap_ms": diff,
                    "gap_minutes": gap_mins,
                })

    # Summary
    first_ts = filtered[0]["open_time"] if filtered else None
    last_ts = filtered[-1]["open_time"] if filtered else None
    first_dt = datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).isoformat() if first_ts else None
    last_dt = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).isoformat() if last_ts else None

    # Expected bars:
    expected_bars = (end_exclusive_ms - start_ms) // CADENCE_MS
    coverage_pct = round(len(filtered) / expected_bars * 100.0, 2) if expected_bars > 0 else 0.0

    stats = {
        "total_candles": len(filtered),
        "expected_candles": expected_bars,
        "coverage_pct": coverage_pct,
        "first_timestamp": first_ts,
        "first_datetime_utc": first_dt,
        "last_timestamp": last_ts,
        "last_datetime_utc": last_dt,
        "identical_duplicates": identical_dups,
        "conflicting_duplicates": conflicting_dups,
        "invalid_geometry_rejected": invalid_geometry,
        "non_finite_rejected": non_finite_count,
        "gaps_count": len(gaps),
        "gaps": gaps,
        "sha256": compute_sha256(filtered),
    }

    return filtered, stats


def main():
    os.makedirs(DEST_DIR, exist_ok=True)
    download_time_utc = datetime.now(timezone.utc).isoformat()

    # Download 30-day data (from start_ms to end_exclusive_ms)
    raw_rows = download_m1_candles(T30_START_MS, T_END_EXCLUSIVE_MS)

    # Validate 30-day dataset
    candles_30d, stats_30d = validate_and_normalize(raw_rows, T30_START_MS, T_END_EXCLUSIVE_MS)
    path_30d = os.path.join(DEST_DIR, "xauusdt_m1_30d.json")
    with open(path_30d, "w", encoding="utf-8") as f:
        json.dump(candles_30d, f, indent=2)
    print(f"\nSaved 30-day dataset ({len(candles_30d)} candles) -> {path_30d}")
    print(f"  Range: {stats_30d['first_datetime_utc']} to {stats_30d['last_datetime_utc']}")
    print(f"  SHA-256: {stats_30d['sha256']}")
    print(f"  Coverage: {stats_30d['coverage_pct']}% (expected {stats_30d['expected_candles']} bars)")
    print(f"  Gaps count: {stats_30d['gaps_count']}")

    # Slice and validate 14-day dataset
    candles_14d, stats_14d = validate_and_normalize(raw_rows, T14_START_MS, T_END_EXCLUSIVE_MS)
    path_14d = os.path.join(DEST_DIR, "xauusdt_m1_14d.json")
    with open(path_14d, "w", encoding="utf-8") as f:
        json.dump(candles_14d, f, indent=2)
    print(f"\nSaved 14-day dataset ({len(candles_14d)} candles) -> {path_14d}")
    print(f"  Range: {stats_14d['first_datetime_utc']} to {stats_14d['last_datetime_utc']}")
    print(f"  SHA-256: {stats_14d['sha256']}")
    print(f"  Coverage: {stats_14d['coverage_pct']}% (expected {stats_14d['expected_candles']} bars)")
    print(f"  Gaps count: {stats_14d['gaps_count']}")

    manifest = {
        "instrument": SYMBOL,
        "product_type": PRODUCT_TYPE,
        "provider": "Bitget",
        "endpoint": BITGET_HISTORY_CANDLES_URL,
        "granularity": GRANULARITY,
        "cadence_ms": CADENCE_MS,
        "download_time_utc": download_time_utc,
        "datasets": {
            "30_day": {
                "start_inclusive_utc": "2026-09-18T00:00:00Z",
                "start_inclusive_ms": T30_START_MS,
                "end_exclusive_utc": "2026-10-18T00:00:00Z",
                "end_exclusive_ms": T_END_EXCLUSIVE_MS,
                "file_path": path_30d,
                "stats": stats_30d,
            },
            "14_day": {
                "start_inclusive_utc": "2026-10-04T00:00:00Z",
                "start_inclusive_ms": T14_START_MS,
                "end_exclusive_utc": "2026-10-18T00:00:00Z",
                "end_exclusive_ms": T_END_EXCLUSIVE_MS,
                "file_path": path_14d,
                "stats": stats_14d,
            }
        }
    }

    manifest_path = os.path.join(DEST_DIR, "dataset_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nSaved dataset manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
