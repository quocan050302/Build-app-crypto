#!/usr/bin/env python3
"""
aurum-desk/tools/kh_01b_r1_collect_market_data.py

AURUM DESK — KH-01B.R1 Prospective Market Data Auditor, Extended DEV Collector & OOS Escrow Validator.
SPEC VERSION: KH-01B.R1-v1.0 (2026-11-19)

HARD NON-NEGOTIABLES (§F & §G15-G16):
  - Default mode is `OFFLINE_VALIDATE` (zero network requests by default).
  - Never erases, overwrites, or appends to `backtest/historical_dataset_XAUUSDT.json.gz`.
  - Never uses API keys/secrets and never logs credentials or headers containing secrets.
  - Never synthesizes real L1 BBO quotes for historical OHLCV bars; explicitly labels historical
    bars `MODELED_BID_ASK` (`OHLCV_CANDLES_ONLY_NO_HISTORICAL_BBO`) and live ticker/depth probes
    `CAPTURED_REAL_BBO` (`PROSPECTIVE_SNAPSHOT_ONLY_NOT_HISTORICAL_SERIES`).
  - Separates `DEV_EXTENDED_RESEARCH` strictly from `FUTURE_FRESH_OOS_ESCROW` (`strategy_evaluation_count=0`,
    `embargo_minutes >= 180` after BOTH canonical sample end and code freeze timestamp).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.v17_9_1_decision_funnel import classify_historical_dataset_gaps
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from tools.fresh_junit_io import write_json_durable_atomic, write_jsonl_gz_durable_atomic

CANONICAL_DATASET_REL = "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz"
CANONICAL_DATASET_SHA256 = "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"
CANONICAL_SAMPLE_END_MS = 1_793_195_940_000  # 2026-10-28T13:59:00Z
MIN_OOS_EMBARGO_MINUTES = 180
MIN_OOS_EMBARGO_MS = MIN_OOS_EMBARGO_MINUTES * 60_000

# Predeclared bounded pre-freeze development window (2026-11-16T00:00:00Z .. 2026-11-18T00:00:00Z)
# Strictly > CANONICAL_SAMPLE_END_MS + 180m and strictly < KH-01B.R1 freeze date (2026-11-19).
DEFAULT_DEV_EXTENDED_START_MS = 1_794_787_200_000  # 2026-11-16T00:00:00Z
DEFAULT_DEV_EXTENDED_END_MS = 1_794_960_000_000    # 2026-11-18T00:00:00Z

BITGET_PUBLIC_CONTRACTS_URL = "https://api.bitget.com/api/v2/mix/market/contracts"
BITGET_PUBLIC_HISTORY_CANDLES_URL = "https://api.bitget.com/api/v2/mix/market/history-candles"
BITGET_PUBLIC_TICKER_URL = "https://api.bitget.com/api/v2/mix/market/ticker"
BITGET_PUBLIC_MERGE_DEPTH_URL = "https://api.bitget.com/api/v2/mix/market/merge-depth"

SUPPORTED_GRANULARITIES: Tuple[Tuple[str, int], ...] = (
    ("1m", 60_000),
    ("5m", 300_000),
    ("15m", 900_000),
    ("1H", 3_600_000),
    ("4H", 14_400_000),
    ("1D", 86_400_000),
)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_and_normalize_paginated_candles(
    pages: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    granularity: str,
    duration_ms: int,
    require_origin_sha: bool = True,
) -> Dict[str, Any]:
    """
    G15 validator for paginated OHLCV candle pages:
      - Rejects missing `raw_http_sha256` when `require_origin_sha=True`.
      - Detects out-of-order / reversed intrabar timestamps within a page and across pages,
        conflicting duplicate timestamps, invalid OHLC geometry, non-grid open_time, and
        unclosed candles (`server_time_ms < open_time_ms + duration_ms`).
      - Dedupes exact identical boundary bars if `allow_exact_boundary_dedup=False` is NOT set,
        recording `exact_boundary_duplicates_count` vs `conflicting_duplicates_count` (FAIL).
    """
    violations: List[str] = []
    seen_by_open: Dict[int, Tuple[float, float, float, float, float]] = {}
    accepted_bars: List[Dict[str, Any]] = []
    reversed_page_orders_detected = 0
    exact_boundary_dups = 0
    conflicting_dups = 0
    unclosed_dropped = 0
    invalid_ohlc_count = 0
    raw_rows_total = 0

    for p_idx, page in enumerate(pages):
        origin_sha = str(page.get("raw_http_sha256") or "").strip()
        if require_origin_sha and (len(origin_sha) != 64 or any(c not in "0123456789abcdef" for c in origin_sha.lower())):
            violations.append(f"MISSING_OR_INVALID_ORIGIN_HTTP_SHA256:page={p_idx}")
        server_ts = int(page.get("server_time_ms") or 0)
        if server_ts <= 0:
            violations.append(f"MISSING_SERVER_TIME_MS:page={p_idx}")

        rows = page.get("rows")
        if not isinstance(rows, Sequence):
            violations.append(f"MALFORMED_PAGE_ROWS:page={p_idx}")
            continue

        page_ts_list: List[int] = []
        for r_idx, row in enumerate(rows):
            raw_rows_total += 1
            if not isinstance(row, Sequence) or len(row) < 6:
                invalid_ohlc_count += 1
                violations.append(f"MALFORMED_CANDLE_ROW:page={p_idx}:row={r_idx}")
                continue
            try:
                open_ms = int(row[0])
                o = float(row[1])
                h = float(row[2])
                l = float(row[3])
                c = float(row[4])
                vol = float(row[5])
            except (TypeError, ValueError):
                invalid_ohlc_count += 1
                violations.append(f"NON_NUMERIC_CANDLE_FIELD:page={p_idx}:row={r_idx}")
                continue

            page_ts_list.append(open_ms)
            if open_ms <= 0 or (open_ms % 60_000) != 0:
                violations.append(f"INVALID_BAR_OPEN_ALIGNMENT:{granularity}:{open_ms}")
                continue
            if not all(math.isfinite(x) for x in (o, h, l, c, vol)) or l <= 0.0 or vol < 0.0 or not (l <= o <= h and l <= c <= h and h >= l):
                invalid_ohlc_count += 1
                violations.append(f"INVALID_OHLC_GEOMETRY:{granularity}:{open_ms}")
                continue

            if server_ts > 0 and server_ts < (open_ms + duration_ms):
                unclosed_dropped += 1
                continue

            tup = (round(o, 4), round(h, 4), round(l, 4), round(c, 4), round(vol, 6))
            if open_ms in seen_by_open:
                if seen_by_open[open_ms] == tup:
                    exact_boundary_dups += 1
                    violations.append(f"DUPLICATE_BAR_TIMESTAMP_BOUNDARY:{granularity}:{open_ms}")
                else:
                    conflicting_dups += 1
                    violations.append(f"CONFLICTING_DUPLICATE_BAR_TIMESTAMP:{granularity}:{open_ms}")
                continue

            seen_by_open[open_ms] = tup
            accepted_bars.append({
                "symbol": symbol,
                "timeframe": granularity,
                "timestamp": open_ms,
                "open_time": open_ms,
                "close_time": open_ms + duration_ms,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": round(vol, 4),
                "is_closed": True,
                "quote_mode": "MODELED_BID_ASK",
                "origin_page_http_sha256": origin_sha,
            })

        if len(page_ts_list) >= 2:
            is_strictly_asc = all(page_ts_list[i] < page_ts_list[i + 1] for i in range(len(page_ts_list) - 1))
            if not is_strictly_asc:
                reversed_page_orders_detected += 1
                violations.append(f"REVERSED_OR_NON_ASCENDING_PAGINATION_ORDER:page={p_idx}")

    accepted_bars.sort(key=lambda b: int(b["open_time"]))
    missing_intervals = 0
    for i in range(1, len(accepted_bars)):
        diff = int(accepted_bars[i]["open_time"]) - int(accepted_bars[i - 1]["open_time"])
        if diff > duration_ms:
            missing_intervals += (diff // duration_ms) - 1

    return {
        "symbol": symbol,
        "granularity": granularity,
        "duration_ms": duration_ms,
        "raw_rows_total": raw_rows_total,
        "accepted_closed_bars_count": len(accepted_bars),
        "unclosed_bars_excluded_count": unclosed_dropped,
        "exact_boundary_duplicates_count": exact_boundary_dups,
        "conflicting_duplicates_count": conflicting_dups,
        "invalid_ohlc_count": invalid_ohlc_count,
        "reversed_pagination_pages_count": reversed_page_orders_detected,
        "missing_bar_intervals_count": missing_intervals,
        "earliest_open_ms": int(accepted_bars[0]["open_time"]) if accepted_bars else None,
        "latest_close_ms": int(accepted_bars[-1]["close_time"]) if accepted_bars else None,
        "bars_sha256": canonical_hash(accepted_bars) if accepted_bars else None,
        "status": "PASS" if (len(violations) == 0 and len(accepted_bars) > 0) else "FAIL",
        "violations": violations,
        "bars": accepted_bars,
    }


def validate_prospective_bbo_snapshot(
    snapshot: Mapping[str, Any],
    *,
    max_staleness_ms: int = 15_000,
    calendar_verified: bool = False,
    require_verified_calendar: bool = False,
) -> Dict[str, Any]:
    """
    F4 & G15 validator for prospective captured L1 BBO quotes:
      - Verifies `raw_http_sha256`, `receive_ts_ms`, `exchange_ts_ms`, `0 < bid <= ask`,
        finite non-zero sizes, staleness `abs(receive_ts_ms - exchange_ts_ms) <= max_staleness_ms`,
        and rejects synthetic/mocked BBO claims or unverified calendar claims when required.
    """
    violations: List[str] = []
    if bool(snapshot.get("is_synthetic", False)) or str(snapshot.get("quote_mode") or "") == "SYNTHETIC_AS_REAL":
        violations.append("SYNTHETIC_BBO_SUBSTITUTED_FOR_REAL_EXCHANGE_L1")

    origin_sha = str(snapshot.get("raw_http_sha256") or "").strip()
    if len(origin_sha) != 64 or any(c not in "0123456789abcdef" for c in origin_sha.lower()):
        violations.append("MISSING_OR_INVALID_BBO_ORIGIN_SHA256")

    recv_ms = int(snapshot.get("receive_ts_ms") or 0)
    exch_ms = int(snapshot.get("exchange_ts_ms") or 0)
    if recv_ms <= 0 or exch_ms <= 0:
        violations.append(f"MISSING_BBO_TIMESTAMPS:recv={recv_ms},exch={exch_ms}")

    bid = float(snapshot.get("bid") or 0.0)
    ask = float(snapshot.get("ask") or 0.0)
    bid_sz = float(snapshot.get("bid_size") or 0.0)
    ask_sz = float(snapshot.get("ask_size") or 0.0)
    if not all(math.isfinite(x) for x in (bid, ask, bid_sz, ask_sz)) or bid <= 0.0 or ask <= 0.0:
        violations.append(f"NON_POSITIVE_OR_NONFINITE_BID_ASK:bid={bid},ask={ask}")
    elif bid > ask:
        violations.append(f"CROSSED_BOOK_BID_GT_ASK:bid={bid}>ask={ask}")
    if bid_sz <= 0.0 or ask_sz <= 0.0:
        violations.append(f"NON_POSITIVE_TOP_BOOK_SIZE:bid_sz={bid_sz},ask_sz={ask_sz}")

    staleness_ms = abs(recv_ms - exch_ms) if (recv_ms > 0 and exch_ms > 0) else 999_999_999
    if staleness_ms > max_staleness_ms:
        violations.append(f"STALE_BBO_QUOTE:staleness_ms={staleness_ms}>max={max_staleness_ms}")

    if require_verified_calendar and not calendar_verified:
        violations.append("EXCHANGE_SESSION_CALENDAR_UNVERIFIED")

    spread_usd = round(ask - bid, 4) if (bid > 0 and ask >= bid) else None
    return {
        "symbol": str(snapshot.get("symbol") or "XAUUSDT"),
        "quote_classification": "CAPTURED_REAL_BBO_PROSPECTIVE_POINT_SNAPSHOT",
        "historical_backtest_usable": False,
        "receive_ts_ms": recv_ms,
        "exchange_ts_ms": exch_ms,
        "latency_or_clock_skew_ms": staleness_ms,
        "bid": bid,
        "ask": ask,
        "spread_usd": spread_usd,
        "bid_size": bid_sz,
        "ask_size": ask_sz,
        "exchange_calendar_verified": bool(calendar_verified),
        "raw_http_sha256": origin_sha,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
    }


def build_and_validate_future_oos_escrow_registry(
    *,
    freeze_timestamp_ms: int,
    frozen_candidate_code_sha: str,
    frozen_config_sha256: str,
    proposed_oos_entries: Sequence[Mapping[str, Any]] = (),
    known_dev_dataset_shas: Sequence[str] = (CANONICAL_DATASET_SHA256,),
    claimed_fresh_oos_verdict: Optional[str] = None,
) -> Dict[str, Any]:
    """
    F3 & G16 write-once Future OOS Escrow validator:
      - Enforces `min_allowed_oos_start_ms = max(CANONICAL_SAMPLE_END_MS, freeze_timestamp_ms) + 180m`.
      - Rejects any dataset SHA already present in `known_dev_dataset_shas` (`REUSED_RESEARCH` or `DEV_EXTENDED_RESEARCH`).
      - Enforces `strategy_evaluation_count == 0` during KH-01B.R1.
      - Fails closed if `claimed_fresh_oos_verdict` attempts to assert `PASS` or `PASS_FINANCIAL_EDGE`.
    """
    violations: List[str] = []
    dev_shas: set[str] = {str(s).strip().lower() for s in known_dev_dataset_shas if str(s).strip()}
    dev_shas.add(CANONICAL_DATASET_SHA256.lower())

    min_oos_start_ms = max(int(CANONICAL_SAMPLE_END_MS), int(freeze_timestamp_ms)) + MIN_OOS_EMBARGO_MS

    if claimed_fresh_oos_verdict is not None:
        cv = str(claimed_fresh_oos_verdict).strip().upper()
        if cv not in ("BLOCKED_NO_UNTOUCHED_DATA", "SEALED_UNTOUCHED_NOT_EVALUATED"):
            violations.append(f"ILLEGAL_FRESH_OOS_VERDICT_IN_KH01B_R1:{claimed_fresh_oos_verdict}")

    validated_entries: List[Dict[str, Any]] = []
    for idx, entry in enumerate(proposed_oos_entries):
        ds_id = str(entry.get("dataset_id") or f"oos_{idx}")
        ds_sha = str(entry.get("dataset_sha256") or "").strip().lower()
        ev_cnt = int(entry.get("strategy_evaluation_count", -1))
        st_ms = int(entry.get("earliest_bar_open_ms") or 0)
        en_ms = int(entry.get("latest_bar_close_ms") or 0)
        role = str(entry.get("partition_role") or "").upper()
        used_for_entry_pick = bool(entry.get("used_for_entry_selection", False))

        if not ds_sha or len(ds_sha) != 64:
            violations.append(f"OOS_ENTRY_MISSING_SHA256:{ds_id}")
        if ds_sha in dev_shas:
            violations.append(f"REUSED_OR_DEV_DATASET_REJECTED_FROM_OOS_ESCROW:{ds_id}:{ds_sha}")
        if role in ("REUSED_RESEARCH", "DEV_EXTENDED_RESEARCH", "REUSED_DEV"):
            violations.append(f"DEV_ROLE_CANNOT_BE_PROMOTED_TO_FRESH_OOS:{ds_id}:{role}")
        if ev_cnt != 0:
            violations.append(f"OOS_EVALUATION_COUNT_NONZERO_IN_R1:{ds_id}:count={ev_cnt}")
        if used_for_entry_pick:
            violations.append(f"FUTURE_OOS_LABELS_USED_TO_PICK_ENTRY:{ds_id}")
        if st_ms < min_oos_start_ms:
            violations.append(f"OOS_EMBARGO_VIOLATION:{ds_id}:start={st_ms}<min_required={min_oos_start_ms}")
        if en_ms < st_ms:
            violations.append(f"OOS_WINDOW_NON_MONOTONIC:{ds_id}:{st_ms}>{en_ms}")

        validated_entries.append({
            "dataset_id": ds_id,
            "dataset_sha256": ds_sha,
            "earliest_bar_open_ms": st_ms,
            "latest_bar_close_ms": en_ms,
            "strategy_evaluation_count": ev_cnt,
            "partition_role": role,
        })

    if violations:
        verdict = "FAIL_ESCROW_CONTAMINATION"
    elif validated_entries:
        verdict = "SEALED_UNTOUCHED_NOT_EVALUATED"
    else:
        verdict = "BLOCKED_NO_UNTOUCHED_DATA"

    body = {
        "escrow_schema_version": "kh_01b_r1.future_oos_escrow.v1",
        "write_once_policy": "APPEND_ONLY_NO_EVALUATION_IN_KH01B_R1",
        "canonical_reused_dataset_sha256": CANONICAL_DATASET_SHA256,
        "canonical_reused_sample_end_ms": CANONICAL_SAMPLE_END_MS,
        "freeze_timestamp_ms": int(freeze_timestamp_ms),
        "embargo_minutes_required": MIN_OOS_EMBARGO_MINUTES,
        "earliest_permissible_future_oos_open_ms": min_oos_start_ms,
        "earliest_permissible_future_oos_utc": datetime.fromtimestamp(
            min_oos_start_ms / 1000.0, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frozen_candidate_code_sha": frozen_candidate_code_sha,
        "frozen_config_sha256": frozen_config_sha256,
        "known_excluded_dev_shas": sorted(dev_shas),
        "sealed_future_oos_datasets_count": len(validated_entries),
        "strategy_evaluation_count_total": sum(int(e["strategy_evaluation_count"]) for e in validated_entries),
        "sealed_entries": validated_entries,
        "status": "PASS" if not violations else "FAIL",
        "FRESH_OOS_VERDICT": verdict,
        "violations": violations,
    }
    body["escrow_registry_sha256"] = canonical_hash(body)
    return body


def collect_online_bitget_audit_and_extended_dev(
    output_dir: Path,
    *,
    dev_start_ms: int = DEFAULT_DEV_EXTENDED_START_MS,
    dev_end_ms: int = DEFAULT_DEV_EXTENDED_END_MS,
    freeze_ts_ms: Optional[int] = None,
    max_pages_per_tf: int = 8,
) -> Dict[str, Any]:
    """
    Executes F1-F5 read-only public Bitget capability audit + bounded `DEV_EXTENDED_RESEARCH`
    multi-timeframe OHLCV collection + single point-in-time live BBO capability probe.
    Never modifies `historical_dataset_XAUUSDT.json.gz`. Never uses secrets.
    Falls back cleanly to `BLOCKED_UNAVAILABLE` if network or public endpoint fails.
    """
    import httpx

    now_ms = int(freeze_ts_ms or int(time.time() * 1000))
    if dev_start_ms < CANONICAL_SAMPLE_END_MS + MIN_OOS_EMBARGO_MS:
        raise ValueError("DEV_EXTENDED_WINDOW_OVERLAPS_CANONICAL_OR_EMBARGO")
    if dev_end_ms >= now_ms:
        raise ValueError("DEV_EXTENDED_WINDOW_MUST_END_STRICTLY_BEFORE_FREEZE_TIME")

    canonical_path = REPO_ROOT / CANONICAL_DATASET_REL
    canon_sha_before = _file_sha256(canonical_path) if canonical_path.is_file() else "MISSING"
    if canon_sha_before != CANONICAL_DATASET_SHA256:
        raise RuntimeError(f"CANONICAL_DATASET_SHA_INVALID_BEFORE_COLLECTION:{canon_sha_before}")

    http_receipts: List[Dict[str, Any]] = []
    tf_summaries: Dict[str, Any] = {}
    tf_bars_for_gap_audit: Dict[str, List[Dict[str, Any]]] = {}
    all_dev_rows: List[Dict[str, Any]] = []

    try:
        with httpx.Client(timeout=8.0, headers={"Accept": "application/json"}) as client:
            # 1. Public contract specification audit
            t_req = int(time.time() * 1000)
            r_ct = client.get(BITGET_PUBLIC_CONTRACTS_URL, params={"productType": "USDT-FUTURES", "symbol": "XAUUSDT"})
            r_ct.raise_for_status()
            ct_bytes = r_ct.content
            ct_sha = _sha256_bytes(ct_bytes)
            ct_js = json.loads(ct_bytes.decode("utf-8"))
            if ct_js.get("code") != "00000" or not ct_js.get("data"):
                return {
                    "DATA_ACQUISITION_VERDICT": "BLOCKED_UNAVAILABLE",
                    "extended_dev_status": "EXTENDED_DEV_DATA_BLOCKED",
                    "blocker_reason": f"BITGET_CONTRACT_CODE:{ct_js.get('code')}:{ct_js.get('msg')}",
                    "http_receipts": http_receipts,
                }
            ct_item = ct_js["data"][0]
            http_receipts.append({
                "endpoint": "contracts",
                "symbol": "XAUUSDT",
                "request_ts_ms": t_req,
                "receive_ts_ms": int(time.time() * 1000),
                "http_status": r_ct.status_code,
                "raw_http_sha256": ct_sha,
                "byte_length": len(ct_bytes),
            })

            # 2. Live point-in-time Ticker + Merge-Depth L1 BBO capability probe
            t_bbo_req = int(time.time() * 1000)
            r_tk = client.get(BITGET_PUBLIC_TICKER_URL, params={"productType": "USDT-FUTURES", "symbol": "XAUUSDT"})
            r_tk.raise_for_status()
            t_bbo_recv = int(time.time() * 1000)
            tk_bytes = r_tk.content
            tk_sha = _sha256_bytes(tk_bytes)
            tk_js = json.loads(tk_bytes.decode("utf-8"))
            http_receipts.append({
                "endpoint": "ticker",
                "symbol": "XAUUSDT",
                "request_ts_ms": t_bbo_req,
                "receive_ts_ms": t_bbo_recv,
                "http_status": r_tk.status_code,
                "raw_http_sha256": tk_sha,
                "byte_length": len(tk_bytes),
            })
            tk_row = (tk_js.get("data") or [{}])[0]
            bbo_probe = validate_prospective_bbo_snapshot(
                {
                    "symbol": "XAUUSDT",
                    "raw_http_sha256": tk_sha,
                    "receive_ts_ms": t_bbo_recv,
                    "exchange_ts_ms": int(tk_row.get("ts") or tk_js.get("requestTime") or 0),
                    "bid": float(tk_row.get("bidPr") or 0.0),
                    "ask": float(tk_row.get("askPr") or 0.0),
                    "bid_size": float(tk_row.get("bidSz") or 0.0),
                    "ask_size": float(tk_row.get("askSz") or 0.0),
                },
                max_staleness_ms=30_000,
                calendar_verified=False,
                require_verified_calendar=False,
            )

            # 3. Paginated closed OHLCV bars for predeclared DEV_EXTENDED_RESEARCH window
            for gran, dur_ms in SUPPORTED_GRANULARITIES:
                cursor_end = dev_end_ms
                pages_collected: List[Dict[str, Any]] = []
                seen_page_min: Optional[int] = None
                for _p in range(max_pages_per_tf):
                    if cursor_end <= dev_start_ms:
                        break
                    p_req_ms = int(time.time() * 1000)
                    resp = client.get(
                        BITGET_PUBLIC_HISTORY_CANDLES_URL,
                        params={
                            "productType": "USDT-FUTURES",
                            "symbol": "XAUUSDT",
                            "granularity": gran,
                            "endTime": str(cursor_end),
                            "limit": "200",
                        },
                    )
                    resp.raise_for_status()
                    p_recv_ms = int(time.time() * 1000)
                    raw_b = resp.content
                    p_sha = _sha256_bytes(raw_b)
                    p_js = json.loads(raw_b.decode("utf-8"))
                    http_receipts.append({
                        "endpoint": "history-candles",
                        "granularity": gran,
                        "cursor_end_ms": cursor_end,
                        "request_ts_ms": p_req_ms,
                        "receive_ts_ms": p_recv_ms,
                        "http_status": resp.status_code,
                        "raw_http_sha256": p_sha,
                        "byte_length": len(raw_b),
                    })
                    if p_js.get("code") != "00000":
                        raise RuntimeError(f"BITGET_CANDLES_ERROR:{gran}:{p_js.get('code')}:{p_js.get('msg')}")
                    raw_rows = list(p_js.get("data") or [])
                    if not raw_rows:
                        break
                    # Sort ascending inside page and filter to [dev_start_ms, dev_end_ms - dur_ms]
                    raw_rows_sorted = sorted(raw_rows, key=lambda r: int(r[0]))
                    filtered_rows = [
                        r for r in raw_rows_sorted
                        if dev_start_ms <= int(r[0]) and (int(r[0]) + dur_ms) <= dev_end_ms
                    ]
                    if filtered_rows:
                        pages_collected.append({
                            "raw_http_sha256": p_sha,
                            "server_time_ms": int(p_js.get("requestTime") or p_recv_ms),
                            "rows": filtered_rows,
                        })
                    earliest_in_raw = int(raw_rows_sorted[0][0])
                    if seen_page_min is not None and earliest_in_raw >= seen_page_min:
                        break
                    seen_page_min = earliest_in_raw
                    if earliest_in_raw <= dev_start_ms:
                        break
                    cursor_end = earliest_in_raw
                    time.sleep(0.06)  # Gentle public rate-limit pacing

                # Order pages chronologically from earliest to latest before validation
                pages_collected.sort(key=lambda pg: int(pg["rows"][0][0]) if pg["rows"] else 0)
                val_tf = validate_and_normalize_paginated_candles(
                    pages_collected,
                    symbol="XAUUSDT",
                    granularity=gran,
                    duration_ms=dur_ms,
                    require_origin_sha=True,
                )
                bars_tf = val_tf.pop("bars")
                tf_summaries[gran] = val_tf
                tf_bars_for_gap_audit[gran] = bars_tf
                all_dev_rows.extend(bars_tf)

    except Exception as exc:
        return {
            "DATA_ACQUISITION_VERDICT": "BLOCKED_UNAVAILABLE",
            "extended_dev_status": "EXTENDED_DEV_DATA_BLOCKED",
            "blocker_reason": f"{type(exc).__name__}:{exc}",
            "missing_fields_when_blocked": [
                "post_canonical_closed_ohlcv_bars",
                "prospective_live_bbo_snapshot_receipt",
            ],
            "synthetic_substitution_performed": False,
            "http_receipts_captured_before_block": len(http_receipts),
        }

    canon_sha_after = _file_sha256(canonical_path)
    if canon_sha_after != CANONICAL_DATASET_SHA256:
        raise RuntimeError("CANONICAL_DATASET_MUTATED_DURING_COLLECTION")

    gap_audit = classify_historical_dataset_gaps(tf_bars_for_gap_audit)
    (output_dir / "ledgers").mkdir(parents=True, exist_ok=True)
    (output_dir / "receipts").mkdir(parents=True, exist_ok=True)

    dev_gz_rel = "ledgers/dev_extended_xauusdt_ohlcv_snapshot.jsonl.gz"
    dev_gz_sha = write_jsonl_gz_durable_atomic(output_dir / dev_gz_rel, all_dev_rows)
    origin_rcpt_rel = "receipts/bitget_origin_http_receipts.json"
    origin_rcpt_sha = write_json_durable_atomic(
        output_dir / origin_rcpt_rel,
        {
            "captured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "requests_count": len(http_receipts),
            "credentials_used": False,
            "receipts": http_receipts,
        },
    )

    all_tfs_ok = all(v.get("status") == "PASS" for v in tf_summaries.values()) and len(tf_summaries) == len(SUPPORTED_GRANULARITIES)
    verdict = "PASS_VERIFIED_WITH_ORIGIN_RECEIPTS" if (all_tfs_ok and bbo_probe["status"] == "PASS") else "BLOCKED_UNAVAILABLE"

    return {
        "DATA_ACQUISITION_VERDICT": verdict,
        "extended_dev_status": "DEV_EXTENDED_RESEARCH_INGESTED_IMMUTABLY" if all_tfs_ok else "EXTENDED_DEV_DATA_BLOCKED",
        "partition_role": "DEV_EXTENDED_RESEARCH",
        "prior_use_status_after_inspection": "REUSED_DEV",
        "eligible_for_future_fresh_oos": False,
        "quote_provenance_for_historical_bars": "MODELED_BID_ASK",
        "historical_l1_bbo_rest_availability": "UNAVAILABLE_ON_BITGET_PUBLIC_REST_OHLCV_ONLY",
        "exchange_holiday_maintenance_calendar_status": "CALENDAR_NOT_VERIFIED_NO_PUBLIC_HISTORICAL_CALENDAR_ENDPOINT",
        "contract_specification_audit": {
            "symbol": ct_item.get("symbol"),
            "productType": "USDT-FUTURES",
            "symbolStatus": ct_item.get("symbolStatus"),
            "makerFeeRate": ct_item.get("makerFeeRate"),
            "takerFeeRate": ct_item.get("takerFeeRate"),
            "minTradeNum": ct_item.get("minTradeNum"),
            "pricePlace": ct_item.get("pricePlace"),
            "sizeMultiplier": ct_item.get("sizeMultiplier"),
        },
        "prospective_bbo_capability_probe": bbo_probe,
        "dev_extended_window_ms": [dev_start_ms, dev_end_ms],
        "dev_extended_snapshot_rel_path": dev_gz_rel,
        "dev_extended_snapshot_sha256": dev_gz_sha,
        "dev_extended_total_bars_count": len(all_dev_rows),
        "origin_http_receipts_rel_path": origin_rcpt_rel,
        "origin_http_receipts_sha256": origin_rcpt_sha,
        "http_requests_count": len(http_receipts),
        "canonical_dataset_untouched_sha256": canon_sha_after,
        "per_timeframe_validation": tf_summaries,
        "gap_classification_summary": {
            tf_k: {
                "actual_bars": (gap_audit.get("per_timeframe", {}).get(tf_k) or {}).get("actual_bars"),
                "total_missing_bars": (gap_audit.get("per_timeframe", {}).get(tf_k) or {}).get("total_missing_bars"),
                "gap_class_counts": (gap_audit.get("per_timeframe", {}).get(tf_k) or {}).get("gap_class_counts"),
            }
            for tf_k in sorted(tf_bars_for_gap_audit.keys())
        },
        "synthetic_substitution_performed": False,
    }


def run_offline_collector_self_audit() -> Dict[str, Any]:
    """Default OFFLINE_VALIDATE mode: zero network requests; verifies canonical dataset & G15/G16 guards."""
    canonical_path = REPO_ROOT / CANONICAL_DATASET_REL
    canon_sha = _file_sha256(canonical_path) if canonical_path.is_file() else "MISSING"
    escrow = build_and_validate_future_oos_escrow_registry(
        freeze_timestamp_ms=int(time.time() * 1000),
        frozen_candidate_code_sha="OFFLINE_VALIDATE_ONLY",
        frozen_config_sha256="OFFLINE_VALIDATE_ONLY",
    )
    return {
        "mode": "OFFLINE_VALIDATE",
        "network_requests_made": 0,
        "canonical_dataset_sha256": canon_sha,
        "canonical_dataset_verified": canon_sha == CANONICAL_DATASET_SHA256,
        "DATA_ACQUISITION_VERDICT": "BLOCKED_UNAVAILABLE",
        "future_oos_escrow": escrow,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="KH-01B.R1 Market Data Auditor & OOS Escrow Validator")
    ap.add_argument(
        "--mode",
        choices=("OFFLINE_VALIDATE", "ONLINE_AUDIT_AND_EXTENDED_DEV"),
        default="OFFLINE_VALIDATE",
    )
    ap.add_argument("--output-dir", default="")
    args = ap.parse_args()

    if args.mode == "OFFLINE_VALIDATE":
        out = run_offline_collector_self_audit()
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["canonical_dataset_verified"] else 1

    if not args.output_dir:
        raise SystemExit("--output-dir is required for ONLINE_AUDIT_AND_EXTENDED_DEV")
    res = collect_online_bitget_audit_and_extended_dev(Path(args.output_dir).resolve())
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
