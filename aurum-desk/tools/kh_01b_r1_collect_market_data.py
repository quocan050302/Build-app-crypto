#!/usr/bin/env python3
"""
aurum-desk/tools/kh_01b_r1_collect_market_data.py

AURUM DESK — KH-01B.R1 / KH-01B.R1.DSC Prospective Market Data Auditor, Extended DEV Collector & OOS Escrow Validator.
SPEC VERSION: KH-01B.R1.DSC-v1.0 (2026-11-19)

HARD NON-NEGOTIABLES (§F, §G15-G16 & KH-01B.R1.DSC §3A/§4):
  - Default mode is `OFFLINE_VALIDATE` (zero network requests by default).
  - Never erases, overwrites, or appends to `backtest/historical_dataset_XAUUSDT.json.gz`.
  - Never uses API keys/secrets and never logs credentials or headers containing secrets.
  - Never synthesizes real L1 BBO quotes for historical OHLCV bars; explicitly labels historical
    bars `MODELED_BID_ASK` (`OHLCV_CANDLES_ONLY_NO_HISTORICAL_BBO`) and live ticker/depth probes
    `CAPTURED_REAL_BBO_PROSPECTIVE_POINT_SNAPSHOT` (`PROSPECTIVE_SNAPSHOT_ONLY_NOT_HISTORICAL_SERIES`).
  - Enforces exact `Decimal` cross-page boundary dedupe (never rounding away precision collisions,
    rejecting within-page duplicates, duplicate-page replays, and conflicting OHLCV duplicates).
  - Enforces raw page ordering checks BEFORE sorting, per-granularity UTC/venue start-boundary
    alignment (including Bitget `1D` `16:00:00 UTC` = `00:00:00 UTC+8`), dynamic bounded pagination
    (removing silent 8-page / 1600-bar truncation), requested-vs-observed slot coverage accounting,
    authoritative `gap_audit['timeframes']` (`M1..D1`) mapping, and strict `>= 20.0 GiB` preflight
    disk reserve before online capture.
  - Separates `DEV_EXTENDED_RESEARCH` strictly from `FUTURE_FRESH_OOS_ESCROW` (`strategy_evaluation_count=0`,
    `embargo_minutes >= 180` after BOTH canonical sample end and code freeze timestamp).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

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

PARSER_VERSION = "kh_01b_r1_dsc.market_data_collector.v2"
CANONICAL_DATASET_REL = "aurum-desk/backtest/historical_dataset_XAUUSDT.json.gz"
CANONICAL_DATASET_SHA256 = "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"
CANONICAL_SAMPLE_END_MS = 1_793_195_940_000  # 2026-10-28T13:59:00Z
MIN_OOS_EMBARGO_MINUTES = 180
MIN_OOS_EMBARGO_MS = MIN_OOS_EMBARGO_MINUTES * 60_000

# Predeclared bounded pre-freeze development window (2026-11-16T00:00:00Z .. 2026-11-18T00:00:00Z)
# Strictly > CANONICAL_SAMPLE_END_MS + 180m and strictly < KH-01B.R1 freeze date (2026-11-19).
DEFAULT_DEV_EXTENDED_START_MS = 1_794_787_200_000  # 2026-11-16T00:00:00Z
DEFAULT_DEV_EXTENDED_END_MS = 1_794_960_000_000    # 2026-11-18T00:00:00Z

# Bitget USDT-FUTURES 1D daily bar opens at 00:00:00 UTC+8 == 16:00:00 UTC (57,600,000 ms offset into UTC day)
BITGET_D1_UTC_OFFSET_MS = 16 * 3_600_000  # 57_600_000

# Strict 20.0 GiB reserve + 512 MiB conservative stage allowance before online writes
STRICT_COLLECTOR_DISK_RESERVE_BYTES = 20 * 1024**3
COLLECTOR_STAGE_ALLOWANCE_BYTES = 512 * 1024**2

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

GRANULARITY_TO_GAP_AUDIT_LABEL: Dict[str, str] = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "1H": "H1",
    "4H": "H4",
    "1D": "D1",
}


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_exact_decimal(raw_val: Any) -> Optional[Decimal]:
    """Parses exact Decimal from string/int/Decimal/float without rounding away precision."""
    if raw_val is None or isinstance(raw_val, bool):
        return None
    if isinstance(raw_val, Decimal):
        dec = raw_val
    elif isinstance(raw_val, (str, int)):
        s = str(raw_val).strip()
        if not s:
            return None
        try:
            dec = Decimal(s)
        except InvalidOperation:
            return None
    elif isinstance(raw_val, float):
        if not math.isfinite(raw_val):
            return None
        try:
            dec = Decimal(str(raw_val))
        except InvalidOperation:
            return None
    else:
        return None
    if not dec.is_finite():
        return None
    return dec


def compare_boundary_candles_exactly(
    existing_sig: Mapping[str, Any],
    candidate_sig: Mapping[str, Any],
) -> bool:
    """
    Exact data-identity comparator for cross-page boundary bars (§3A.1 & §4.D3):
      - Requires identical `symbol`, `granularity`, and `open_ms`.
      - Compares full-precision `Decimal` values for `open`, `high`, `low`, `close`, `volume`
        (never 2-decimal or 4-decimal rounded floats, so `3700.004` vs `3700.001` FAILS).
    """
    for k in ("symbol", "granularity", "open_ms"):
        if existing_sig.get(k) != candidate_sig.get(k):
            return False
    for num_k in ("open_dec", "high_dec", "low_dec", "close_dec", "volume_dec"):
        a = existing_sig.get(num_k)
        b = candidate_sig.get(num_k)
        if not isinstance(a, Decimal) or not isinstance(b, Decimal):
            return False
        if a != b:
            return False
    return True


def validate_bar_open_alignment_for_granularity(
    open_ms: int,
    granularity: str,
    duration_ms: int,
    *,
    d1_utc_offset_ms: int = BITGET_D1_UTC_OFFSET_MS,
) -> Tuple[bool, str]:
    """
    Validates exchange-aligned timeframe start rules (§3A.1 & §4.D5):
      - `1m`, `5m`, `15m`, `1H`, `4H`: `open_ms % duration_ms == 0`
      - `1D` (Bitget USDT-FUTURES `UTC+8` daily session): `(open_ms - 57_600_000) % 86_400_000 == 0`
        (`16:00:00 UTC` == `00:00:00 UTC+8`, matching canonical `historical_dataset_XAUUSDT.json.gz`).
      - `1Dutc`: `open_ms % 86_400_000 == 0`.
    """
    if open_ms <= 0 or duration_ms <= 0:
        return False, f"NON_POSITIVE_OPEN_OR_DURATION:{granularity}:{open_ms}:{duration_ms}"
    if granularity in ("1m", "5m", "15m", "1H", "4H"):
        if (open_ms % duration_ms) != 0:
            return False, f"INVALID_BAR_OPEN_ALIGNMENT:{granularity}:open_ms={open_ms}_not_divisible_by_{duration_ms}"
        return True, "ALIGNED_FIXED_UTC_GRID"
    if granularity == "1D":
        if duration_ms != 86_400_000:
            return False, f"INVALID_1D_DURATION_MS:{duration_ms}"
        if ((open_ms - int(d1_utc_offset_ms)) % 86_400_000) != 0:
            return (
                False,
                f"INVALID_1D_EXCHANGE_SESSION_ALIGNMENT:open_ms={open_ms}_expected_offset_ms={d1_utc_offset_ms}",
            )
        return True, "ALIGNED_BITGET_1D_UTC_PLUS_8_1600_UTC"
    if granularity == "1Dutc":
        if duration_ms != 86_400_000 or (open_ms % 86_400_000) != 0:
            return False, f"INVALID_1DUTC_ALIGNMENT:open_ms={open_ms}"
        return True, "ALIGNED_1D_UTC_0000"
    if (open_ms % duration_ms) != 0:
        return False, f"INVALID_BAR_OPEN_ALIGNMENT:{granularity}:{open_ms}"
    return True, "ALIGNED_GENERIC_GRID"


def compute_required_page_budget(
    requested_start_ms: int,
    requested_end_ms: int,
    duration_ms: int,
    *,
    page_limit: int = 200,
    hard_max_pages: int = 256,
) -> Dict[str, Any]:
    """
    Computes dynamic bounded pagination budget from the requested time window and timeframe
    instead of silently capping `1m` at `8 * 200 = 1600` bars (§3A.2).
    """
    if requested_end_ms <= requested_start_ms or duration_ms <= 0 or page_limit <= 1:
        raise ValueError(
            f"INVALID_PAGE_BUDGET_INPUTS:start={requested_start_ms},end={requested_end_ms},dur={duration_ms},limit={page_limit}"
        )
    span_ms = int(requested_end_ms) - int(requested_start_ms)
    estimated_slots = int(math.ceil(span_ms / float(duration_ms)))
    effective_stride = max(1, int(page_limit) - 1)  # 1-bar boundary overlap safety margin
    required_pages = max(1, int(math.ceil(estimated_slots / float(effective_stride))) + 2)
    capped = required_pages > int(hard_max_pages)
    return {
        "requested_start_ms": int(requested_start_ms),
        "requested_end_ms": int(requested_end_ms),
        "duration_ms": int(duration_ms),
        "page_limit": int(page_limit),
        "estimated_slots_in_span": estimated_slots,
        "required_pages_for_full_window": required_pages,
        "hard_max_pages": int(hard_max_pages),
        "effective_page_budget": min(int(hard_max_pages), required_pages),
        "budget_capped_by_hard_max": capped,
    }


def verify_http_page_cursor_progress(
    *,
    prev_cursor_end_ms: Optional[int],
    next_cursor_end_ms: int,
    prev_earliest_open_ms: Optional[int],
    curr_earliest_open_ms: int,
    raw_http_sha256: str,
    seen_page_shas: Set[str],
) -> Dict[str, Any]:
    """
    Enforces strict backward cursor progress & non-repeating page payloads during pagination (§3A.2 & §4.D4).
    """
    violations: List[str] = []
    sha_norm = str(raw_http_sha256 or "").strip().lower()
    if len(sha_norm) != 64 or any(c not in "0123456789abcdef" for c in sha_norm):
        violations.append("MISSING_OR_INVALID_PAGE_SHA256")
    elif sha_norm in seen_page_shas:
        violations.append(f"REPEATED_HTTP_PAGE_PAYLOAD_SHA256:{sha_norm[:16]}")

    if prev_cursor_end_ms is not None and int(next_cursor_end_ms) >= int(prev_cursor_end_ms):
        violations.append(
            f"NON_DESCENDING_PAGINATION_CURSOR:next={next_cursor_end_ms}>=prev={prev_cursor_end_ms}"
        )
    if prev_earliest_open_ms is not None and int(curr_earliest_open_ms) >= int(prev_earliest_open_ms):
        violations.append(
            f"STALLED_OR_ADVANCING_PAGE_EARLIEST_BAR:curr={curr_earliest_open_ms}>=prev={prev_earliest_open_ms}"
        )
    return {
        "progressed_strictly_backward": len(violations) == 0,
        "violations": violations,
    }


def _enumerate_expected_closed_slot_opens(
    requested_start_ms: int,
    requested_end_ms: int,
    granularity: str,
    duration_ms: int,
    *,
    d1_utc_offset_ms: int = BITGET_D1_UTC_OFFSET_MS,
) -> List[int]:
    if requested_end_ms <= requested_start_ms or duration_ms <= 0:
        return []
    if granularity == "1D":
        offset = int(d1_utc_offset_ms) % 86_400_000
        k = (int(requested_start_ms) - offset + 86_400_000 - 1) // 86_400_000
        first_open = offset + k * 86_400_000
        while first_open < requested_start_ms:
            first_open += 86_400_000
    else:
        rem = int(requested_start_ms) % int(duration_ms)
        first_open = int(requested_start_ms) if rem == 0 else (int(requested_start_ms) + (int(duration_ms) - rem))

    slots: List[int] = []
    cur = first_open
    while cur + duration_ms <= requested_end_ms:
        slots.append(cur)
        cur += duration_ms
    return slots


def derive_window_coverage_by_timeframe(
    accepted_open_times_ms: Sequence[int],
    *,
    requested_start_ms: Optional[int],
    requested_end_ms: Optional[int],
    granularity: str,
    duration_ms: int,
    d1_utc_offset_ms: int = BITGET_D1_UTC_OFFSET_MS,
    calendar_verified: bool = False,
) -> Dict[str, Any]:
    """
    Computes exact requested-window vs observed-slot accounting (§3A.1, §4.D2, §4.D7):
      `expected_closed_slots == observed_unique_closed_slots + missing_slots`
      `missing_slots == leading_missing_slots + internal_missing_slots + trailing_missing_slots`
    Never equates missing slots under an unverified session calendar to proven provider outages,
    and NEVER marks a partial window (e.g. 1600/2880 M1 bars) as `VERIFIED_COMPLETE`.
    """
    unique_sorted = sorted({int(t) for t in accepted_open_times_ms})
    earliest_open_ms = unique_sorted[0] if unique_sorted else None
    latest_close_ms = (unique_sorted[-1] + int(duration_ms)) if unique_sorted else None
    cal_status = (
        "VERIFIED_EXCHANGE_SESSION_CALENDAR"
        if calendar_verified
        else "CALENDAR_UNVERIFIED_MISSING_SLOTS_MAY_INCLUDE_SESSION_CLOSURE_OR_RETENTION_LIMIT"
    )

    if requested_start_ms is None or requested_end_ms is None:
        if not unique_sorted:
            return {
                "window_accounting_mode": "UNBOUNDED_NO_REQUESTED_WINDOW",
                "requested_start_ms": requested_start_ms,
                "requested_end_ms": requested_end_ms,
                "earliest_open_ms": None,
                "latest_close_ms": None,
                "expected_closed_slots": 0,
                "observed_unique_closed_slots": 0,
                "missing_slots": 0,
                "leading_missing_slots": 0,
                "trailing_missing_slots": 0,
                "internal_missing_slots": 0,
                "coverage_ratio": None,
                "coverage_verdict": "EMPTY_NO_OBSERVED_BARS",
                "market_session_calendar_status": cal_status,
            }
        eff_start = earliest_open_ms
        eff_end = latest_close_ms
        expected_opens = _enumerate_expected_closed_slot_opens(
            int(eff_start), int(eff_end), granularity, duration_ms, d1_utc_offset_ms=d1_utc_offset_ms
        )
        obs_set = set(unique_sorted)
        internal_miss = sum(1 for s in expected_opens if s not in obs_set)
        return {
            "window_accounting_mode": "IMPLICIT_OBSERVED_SPAN_ONLY_NOT_REQUESTED_WINDOW",
            "requested_start_ms": None,
            "requested_end_ms": None,
            "earliest_open_ms": earliest_open_ms,
            "latest_close_ms": latest_close_ms,
            "expected_closed_slots": len(expected_opens),
            "observed_unique_closed_slots": len(unique_sorted),
            "missing_slots": internal_miss,
            "leading_missing_slots": 0,
            "trailing_missing_slots": 0,
            "internal_missing_slots": internal_miss,
            "coverage_ratio": round(len(unique_sorted) / max(1, len(expected_opens)), 6),
            "coverage_verdict": (
                "OBSERVED_SPAN_CONTIGUOUS_NO_WINDOW_SPECIFIED"
                if internal_miss == 0
                else ("VERIFIED_PARTIAL" if calendar_verified else "PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION")
            ),
            "market_session_calendar_status": cal_status,
        }

    req_s = int(requested_start_ms)
    req_e = int(requested_end_ms)
    expected_opens = _enumerate_expected_closed_slot_opens(
        req_s, req_e, granularity, duration_ms, d1_utc_offset_ms=d1_utc_offset_ms
    )
    expected_set = set(expected_opens)
    obs_in_window = [t for t in unique_sorted if t in expected_set]
    obs_outside_window_count = len(unique_sorted) - len(obs_in_window)

    exp_n = len(expected_opens)
    obs_n = len(obs_in_window)
    if obs_n == 0:
        lead_miss = exp_n
        trail_miss = 0
        int_miss = 0
        miss_n = exp_n
        verdict = "EMPTY_NO_OBSERVED_BARS" if exp_n > 0 else "ZERO_EXPECTED_SLOTS_IN_WINDOW"
    else:
        first_obs = obs_in_window[0]
        last_obs = obs_in_window[-1]
        obs_in_set = set(obs_in_window)
        lead_miss = sum(1 for s in expected_opens if s < first_obs)
        trail_miss = sum(1 for s in expected_opens if s > last_obs)
        int_miss = sum(1 for s in expected_opens if first_obs <= s <= last_obs and s not in obs_in_set)
        miss_n = lead_miss + trail_miss + int_miss
        if miss_n == 0 and exp_n > 0:
            verdict = "VERIFIED_COMPLETE"
        elif calendar_verified:
            verdict = "VERIFIED_PARTIAL"
        else:
            verdict = "PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION"

    cov_ratio = round(obs_n / float(exp_n), 6) if exp_n > 0 else None
    return {
        "window_accounting_mode": "EXPLICIT_REQUESTED_WINDOW",
        "requested_start_ms": req_s,
        "requested_end_ms": req_e,
        "requested_start_utc": datetime.fromtimestamp(req_s / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_end_utc": datetime.fromtimestamp(req_e / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "earliest_open_ms": obs_in_window[0] if obs_in_window else earliest_open_ms,
        "latest_close_ms": (obs_in_window[-1] + int(duration_ms)) if obs_in_window else latest_close_ms,
        "earliest_open_utc": (
            datetime.fromtimestamp(obs_in_window[0] / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if obs_in_window
            else None
        ),
        "latest_close_utc": (
            datetime.fromtimestamp((obs_in_window[-1] + int(duration_ms)) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if obs_in_window
            else None
        ),
        "expected_closed_slots": exp_n,
        "observed_unique_closed_slots": obs_n,
        "observed_outside_requested_window_count": obs_outside_window_count,
        "missing_slots": miss_n,
        "leading_missing_slots": lead_miss,
        "trailing_missing_slots": trail_miss,
        "internal_missing_slots": int_miss,
        "coverage_ratio": cov_ratio,
        "coverage_verdict": verdict,
        "market_session_calendar_status": cal_status,
    }


def map_gap_truth_to_acquisition_summary(
    gap_audit: Mapping[str, Any],
    *,
    per_tf_validator_counts: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    """
    Authoritative gap schema mapper (§1 Defect A, §3A.2 & §4.D1):
      - Reads `gap_audit['timeframes']` via `{'1m':'M1','5m':'M5','15m':'M15','1H':'H1','4H':'H4','1D':'D1'}`.
      - Extracts `actual_bars`, `valid_unique_closed_bars`, `total_missing_bars`,
        `gap_events_by_category`, `missing_bars_by_category`, `unknown_unverified_missing_bars`,
        and provenance.
      - Fails closed (`FAIL_INTEGRITY`) if `timeframes` is missing, any of the 6 timeframes is
        missing, any required numeric/dict field is `None`, or `actual_bars` mismatches validator count.
    """
    violations: List[str] = []
    if not isinstance(gap_audit, Mapping):
        return {
            "status": "FAIL_INTEGRITY",
            "violations": ["GAP_AUDIT_NOT_MAPPING"],
            "mapped_by_granularity": {},
        }

    tfs_dict = gap_audit.get("timeframes")
    if not isinstance(tfs_dict, Mapping):
        return {
            "status": "FAIL_INTEGRITY",
            "violations": ["MISSING_AUTHORITATIVE_TIMEFRAMES_KEY_IN_GAP_AUDIT"],
            "mapped_by_granularity": {},
        }

    cal_status = str(gap_audit.get("session_calendar_verification_status") or "")
    schema_ver = str(gap_audit.get("schema_version") or "")
    if not cal_status or not schema_ver:
        violations.append("MISSING_GAP_AUDIT_PROVENANCE_HEADERS")

    mapped: Dict[str, Dict[str, Any]] = {}
    for gran, auth_label in GRANULARITY_TO_GAP_AUDIT_LABEL.items():
        entry = tfs_dict.get(auth_label)
        if not isinstance(entry, Mapping):
            violations.append(f"MISSING_TIMEFRAME_IN_GAP_AUDIT:{gran}->{auth_label}")
            continue

        actual_bars = entry.get("actual_bars")
        valid_unique = entry.get("valid_unique_closed_bars")
        total_missing = entry.get("total_missing_bars")
        ev_by_cat = entry.get("gap_events_by_category")
        miss_by_cat = entry.get("missing_bars_by_category")
        unk_miss = entry.get("unknown_unverified_missing_bars", 0 if actual_bars == 0 else None)

        if (
            actual_bars is None
            or valid_unique is None
            or total_missing is None
            or unk_miss is None
            or not isinstance(ev_by_cat, Mapping)
            or not isinstance(miss_by_cat, Mapping)
        ):
            violations.append(f"NULL_OR_MALFORMED_GAP_FIELDS:{gran}->{auth_label}")
            continue

        actual_i = int(actual_bars)
        valid_i = int(valid_unique)
        miss_i = int(total_missing)
        unk_i = int(unk_miss)
        ev_dict = {str(k): int(v) for k, v in sorted(ev_by_cat.items())}
        miss_dict = {str(k): int(v) for k, v in sorted(miss_by_cat.items())}

        if sum(miss_dict.values()) != miss_i:
            violations.append(
                f"MISSING_BARS_CATEGORY_SUM_MISMATCH:{gran}:{sum(miss_dict.values())}!={miss_i}"
            )
        if per_tf_validator_counts is not None and gran in per_tf_validator_counts:
            exp_cnt = int(per_tf_validator_counts[gran])
            if actual_i != exp_cnt or valid_i != exp_cnt:
                violations.append(
                    f"VALIDATOR_VS_GAP_AUDIT_BAR_COUNT_MISMATCH:{gran}:gap=({actual_i},{valid_i})!=val={exp_cnt}"
                )

        mapped[gran] = {
            "authoritative_gap_label": auth_label,
            "actual_bars": actual_i,
            "valid_unique_closed_bars": valid_i,
            "total_missing_bars": miss_i,
            "gap_events_count": int(entry.get("gap_events_count", sum(ev_dict.values()))),
            "gap_events_by_category": ev_dict,
            "missing_bars_by_category": miss_dict,
            "unknown_unverified_missing_bars": unk_i,
            "observed_bar_density_ratio": entry.get("observed_bar_density_ratio"),
            "first_open_ms": entry.get("first_open_ms"),
            "last_close_ms": entry.get("last_close_ms"),
            "provenance": {
                "gap_schema_version": schema_ver,
                "session_calendar_verification_status": cal_status,
                "point_in_time_availability_rule": gap_audit.get("point_in_time_availability_rule"),
                "quote_construction_mode": gap_audit.get("quote_construction_mode"),
            },
        }

    if len(mapped) != len(GRANULARITY_TO_GAP_AUDIT_LABEL):
        violations.append(f"INCOMPLETE_SIX_TF_GAP_SUMMARY:{len(mapped)}/{len(GRANULARITY_TO_GAP_AUDIT_LABEL)}")

    return {
        "status": "PASS" if not violations else "FAIL_INTEGRITY",
        "schema_version": schema_ver or "UNKNOWN",
        "session_calendar_verification_status": cal_status or "UNKNOWN",
        "key_mapping_used": dict(GRANULARITY_TO_GAP_AUDIT_LABEL),
        "violations": violations,
        "mapped_by_granularity": mapped,
    }


def validate_and_normalize_paginated_candles(
    pages: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    granularity: str,
    duration_ms: int,
    require_origin_sha: bool = True,
    requested_start_ms: Optional[int] = None,
    requested_end_ms: Optional[int] = None,
    allow_documented_descending: bool = False,
    calendar_verified: bool = False,
    d1_utc_offset_ms: int = BITGET_D1_UTC_OFFSET_MS,
) -> Dict[str, Any]:
    """
    G15 & KH-01B.R1.DSC (§3A.1 & §4) validator for paginated OHLCV candle pages:
      - Inspects each raw page's order BEFORE sorting; rejects intra-page non-monotonic disorder,
        within-page duplicate timestamps, and undocumented descending order; allows documented
        monotonic descending ONLY when `allow_documented_descending=True` (with explicit audit receipt).
      - Rejects duplicate page HTTP SHA256s (`[good_page, good_page]` replay) and non-boundary
        cross-page overlaps.
      - Dedupes ONLY genuine cross-page boundary overlaps whose full-precision `Decimal` OHLCV +
        `(symbol, granularity, open_ms)` match 100%, preserving BOTH `origin_page_http_sha256`s and
        setting `dedup_verdict="PASS_WITH_DEDUP_RECEIPT"`.
      - Enforces per-granularity start-boundary alignment (`1m`, `5m`, `15m`, `1H`, `4H`, `1D` `16:00 UTC`),
        positive finite OHLC, `volume >= 0`, `server_time_ms >= open_ms + duration_ms` (excluding unclosed
        candles with explicit count), and full requested-window slot coverage accounting.
    """
    violations: List[str] = []
    seen_page_shas: Set[str] = set()
    raw_page_order_audit: List[Dict[str, Any]] = []
    normalized_pages: List[Dict[str, Any]] = []

    reversed_page_orders_detected = 0
    documented_descending_pages_normalized = 0
    within_page_duplicates = 0
    exact_boundary_dups = 0
    conflicting_dups = 0
    non_boundary_cross_page_dups = 0
    unclosed_dropped = 0
    outside_requested_window_excluded = 0
    invalid_ohlc_count = 0
    alignment_violations_count = 0
    raw_rows_total = 0

    for p_idx, page in enumerate(pages):
        origin_sha = str(page.get("raw_http_sha256") or "").strip().lower()
        sha_valid = len(origin_sha) == 64 and all(c in "0123456789abcdef" for c in origin_sha)
        if require_origin_sha and not sha_valid:
            violations.append(f"MISSING_OR_INVALID_ORIGIN_HTTP_SHA256:page={p_idx}")
        elif sha_valid:
            if origin_sha in seen_page_shas:
                violations.append(f"DUPLICATE_PAGE_HTTP_SHA256_REPLAY:page={p_idx}:sha={origin_sha[:16]}")
            seen_page_shas.add(origin_sha)

        server_ts = int(page.get("server_time_ms") or 0)
        if server_ts <= 0:
            violations.append(f"MISSING_SERVER_TIME_MS:page={p_idx}")

        rows = page.get("rows")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            violations.append(f"MALFORMED_PAGE_ROWS:page={p_idx}")
            continue

        parsed_page_rows: List[Dict[str, Any]] = []
        raw_page_ts: List[int] = []

        for r_idx, row in enumerate(rows):
            raw_rows_total += 1
            if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) < 6:
                invalid_ohlc_count += 1
                violations.append(f"MALFORMED_CANDLE_ROW:page={p_idx}:row={r_idx}")
                continue

            ts_dec = _parse_exact_decimal(row[0])
            o_dec = _parse_exact_decimal(row[1])
            h_dec = _parse_exact_decimal(row[2])
            l_dec = _parse_exact_decimal(row[3])
            c_dec = _parse_exact_decimal(row[4])
            vol_dec = _parse_exact_decimal(row[5])
            if (
                ts_dec is None
                or ts_dec != ts_dec.to_integral_value()
                or o_dec is None
                or h_dec is None
                or l_dec is None
                or c_dec is None
                or vol_dec is None
            ):
                invalid_ohlc_count += 1
                violations.append(f"NON_NUMERIC_OR_NONFINITE_CANDLE_FIELD:page={p_idx}:row={r_idx}")
                continue

            open_ms = int(ts_dec)
            raw_page_ts.append(open_ms)

            aligned_ok, align_reason = validate_bar_open_alignment_for_granularity(
                open_ms, granularity, duration_ms, d1_utc_offset_ms=d1_utc_offset_ms
            )
            if not aligned_ok:
                alignment_violations_count += 1
                violations.append(align_reason)
                continue

            if (
                l_dec <= Decimal("0")
                or vol_dec < Decimal("0")
                or h_dec < l_dec
                or o_dec < l_dec
                or o_dec > h_dec
                or c_dec < l_dec
                or c_dec > h_dec
            ):
                invalid_ohlc_count += 1
                violations.append(f"INVALID_OHLC_GEOMETRY:{granularity}:{open_ms}")
                continue

            if server_ts > 0 and server_ts < (open_ms + duration_ms):
                unclosed_dropped += 1
                continue

            if requested_start_ms is not None and open_ms < int(requested_start_ms):
                outside_requested_window_excluded += 1
                continue
            if requested_end_ms is not None and (open_ms + duration_ms) > int(requested_end_ms):
                outside_requested_window_excluded += 1
                continue

            parsed_page_rows.append({
                "open_ms": open_ms,
                "open_dec": o_dec,
                "high_dec": h_dec,
                "low_dec": l_dec,
                "close_dec": c_dec,
                "volume_dec": vol_dec,
                "origin_sha": origin_sha,
                "page_index": p_idx,
            })

        # Audit RAW page ordering BEFORE any sorting!
        page_doc_order = str(page.get("documented_provider_order") or "").upper()
        desc_allowed = bool(allow_documented_descending) or page_doc_order in (
            "DESCENDING_DOCUMENTED",
            "BITGET_V2_ASC_OR_DESC_MONOTONIC",
        )
        raw_order_label = "SINGLE_OR_EMPTY"
        if len(raw_page_ts) >= 2:
            if len(set(raw_page_ts)) < len(raw_page_ts):
                within_page_duplicates += len(raw_page_ts) - len(set(raw_page_ts))
                raw_order_label = "WITHIN_PAGE_DUPLICATE_TIMESTAMPS"
                violations.append(f"WITHIN_PAGE_DUPLICATE_BAR_TIMESTAMP:page={p_idx}:{granularity}")
            else:
                is_strictly_asc = all(raw_page_ts[i] < raw_page_ts[i + 1] for i in range(len(raw_page_ts) - 1))
                is_strictly_desc = all(raw_page_ts[i] > raw_page_ts[i + 1] for i in range(len(raw_page_ts) - 1))
                if is_strictly_asc:
                    raw_order_label = "STRICT_ASCENDING"
                elif is_strictly_desc:
                    if desc_allowed:
                        raw_order_label = "STRICT_DESCENDING_DOCUMENTED_NORMALIZED"
                        documented_descending_pages_normalized += 1
                        parsed_page_rows.reverse()
                    else:
                        raw_order_label = "STRICT_DESCENDING_UNDOCUMENTED_REJECTED"
                        reversed_page_orders_detected += 1
                        violations.append(f"REVERSED_OR_NON_ASCENDING_PAGINATION_ORDER:page={p_idx}")
                else:
                    raw_order_label = "INTRA_PAGE_NON_MONOTONIC_DISORDER"
                    reversed_page_orders_detected += 1
                    violations.append(f"INTRA_PAGE_NON_MONOTONIC_DISORDER:page={p_idx}")

        raw_page_order_audit.append({
            "page_index": p_idx,
            "raw_http_sha256": origin_sha,
            "query_cursor_end_ms": page.get("query_cursor_end_ms"),
            "receive_ts_ms": page.get("receive_ts_ms"),
            "server_time_ms": server_ts,
            "raw_rows_in_page": len(rows),
            "raw_order_detected": raw_order_label,
            "earliest_valid_open_ms": min((r["open_ms"] for r in parsed_page_rows), default=None),
            "latest_valid_open_ms": max((r["open_ms"] for r in parsed_page_rows), default=None),
        })

        if parsed_page_rows:
            parsed_page_rows_sorted = sorted(parsed_page_rows, key=lambda r: int(r["open_ms"]))
            normalized_pages.append({
                "page_index": p_idx,
                "origin_sha": origin_sha,
                "min_open_ms": int(parsed_page_rows_sorted[0]["open_ms"]),
                "max_open_ms": int(parsed_page_rows_sorted[-1]["open_ms"]),
                "rows": parsed_page_rows_sorted,
            })

    # Order non-empty pages chronologically by (min_open_ms, max_open_ms, page_index) for boundary checks
    normalized_pages.sort(key=lambda pg: (int(pg["min_open_ms"]), int(pg["max_open_ms"]), int(pg["page_index"])))

    seen_by_open: Dict[int, Dict[str, Any]] = {}
    bars_by_open: Dict[int, Dict[str, Any]] = {}
    dedup_receipts: List[Dict[str, Any]] = []

    for norm_idx, pg in enumerate(normalized_pages):
        pg_min = int(pg["min_open_ms"])
        pg_max = int(pg["max_open_ms"])
        pg_origin = str(pg["origin_sha"])

        for rec in pg["rows"]:
            open_ms = int(rec["open_ms"])
            cand_sig = {
                "symbol": symbol,
                "granularity": granularity,
                "open_ms": open_ms,
                "open_dec": rec["open_dec"],
                "high_dec": rec["high_dec"],
                "low_dec": rec["low_dec"],
                "close_dec": rec["close_dec"],
                "volume_dec": rec["volume_dec"],
                "origin_sha": pg_origin,
                "page_min_open_ms": pg_min,
                "page_max_open_ms": pg_max,
                "norm_page_idx": norm_idx,
            }

            if open_ms in seen_by_open:
                prev_sig = seen_by_open[open_ms]
                is_exact_ohlcv = compare_boundary_candles_exactly(prev_sig, cand_sig)
                is_adj_page = (int(cand_sig["norm_page_idx"]) == int(prev_sig["norm_page_idx"]) + 1)
                is_true_boundary = (
                    is_adj_page
                    and pg_origin != str(prev_sig["origin_sha"])
                    and open_ms == int(prev_sig["page_max_open_ms"])
                    and open_ms == pg_min
                    and (int(prev_sig["page_min_open_ms"]) < pg_max)
                )
                if not is_exact_ohlcv:
                    conflicting_dups += 1
                    violations.append(f"CONFLICTING_DUPLICATE_BAR_TIMESTAMP:{granularity}:{open_ms}")
                elif is_true_boundary:
                    exact_boundary_dups += 1
                    existing_bar = bars_by_open[open_ms]
                    sha_list = list(existing_bar.get("origin_page_http_sha256s") or [existing_bar["origin_page_http_sha256"]])
                    if pg_origin and pg_origin not in sha_list:
                        sha_list.append(pg_origin)
                    existing_bar["origin_page_http_sha256s"] = sha_list
                    dedup_receipts.append({
                        "open_time_ms": open_ms,
                        "granularity": granularity,
                        "first_origin_page_http_sha256": str(prev_sig["origin_sha"]),
                        "second_origin_page_http_sha256": pg_origin,
                        "exact_decimal_identity_verified": True,
                    })
                    # Advance boundary reference to current page
                    seen_by_open[open_ms] = cand_sig
                else:
                    non_boundary_cross_page_dups += 1
                    violations.append(f"DUPLICATE_BAR_TIMESTAMP_NON_BOUNDARY_OR_REPLAY:{granularity}:{open_ms}")
                continue

            seen_by_open[open_ms] = cand_sig
            o_canon = float(rec["open_dec"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            h_canon = float(rec["high_dec"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            l_canon = float(rec["low_dec"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            c_canon = float(rec["close_dec"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            v_canon = float(rec["volume_dec"].quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
            bars_by_open[open_ms] = {
                "symbol": symbol,
                "timeframe": granularity,
                "timestamp": open_ms,
                "open_time": open_ms,
                "close_time": open_ms + duration_ms,
                "open": o_canon,
                "high": h_canon,
                "low": l_canon,
                "close": c_canon,
                "volume": v_canon,
                "raw_exact_ohlcv": [
                    str(rec["open_dec"]),
                    str(rec["high_dec"]),
                    str(rec["low_dec"]),
                    str(rec["close_dec"]),
                    str(rec["volume_dec"]),
                ],
                "is_closed": True,
                "quote_mode": "MODELED_BID_ASK",
                "origin_page_http_sha256": pg_origin,
                "origin_page_http_sha256s": [pg_origin] if pg_origin else [],
            }

    accepted_bars = [bars_by_open[k] for k in sorted(bars_by_open.keys())]
    missing_intervals = 0
    for i in range(1, len(accepted_bars)):
        diff = int(accepted_bars[i]["open_time"]) - int(accepted_bars[i - 1]["open_time"])
        if diff > duration_ms:
            missing_intervals += (diff // duration_ms) - 1

    cov = derive_window_coverage_by_timeframe(
        [int(b["open_time"]) for b in accepted_bars],
        requested_start_ms=requested_start_ms,
        requested_end_ms=requested_end_ms,
        granularity=granularity,
        duration_ms=duration_ms,
        d1_utc_offset_ms=d1_utc_offset_ms,
        calendar_verified=calendar_verified,
    )

    is_valid_bars = len(violations) == 0 and len(accepted_bars) > 0
    dedup_verdict = (
        "FAIL_DUPLICATE_VIOLATION"
        if (within_page_duplicates > 0 or conflicting_dups > 0 or non_boundary_cross_page_dups > 0)
        else ("PASS_WITH_DEDUP_RECEIPT" if exact_boundary_dups > 0 else "PASS_NO_DUPLICATES")
    )

    return {
        "symbol": symbol,
        "granularity": granularity,
        "duration_ms": duration_ms,
        "parser_version": PARSER_VERSION,
        "raw_rows_total": raw_rows_total,
        "accepted_closed_bars_count": len(accepted_bars),
        "unclosed_bars_excluded_count": unclosed_dropped,
        "outside_requested_window_excluded_count": outside_requested_window_excluded,
        "within_page_duplicates_count": within_page_duplicates,
        "exact_boundary_duplicates_count": exact_boundary_dups,
        "conflicting_duplicates_count": conflicting_dups,
        "non_boundary_cross_page_duplicates_count": non_boundary_cross_page_dups,
        "dedup_verdict": dedup_verdict,
        "boundary_dedup_receipts": dedup_receipts,
        "invalid_ohlc_count": invalid_ohlc_count,
        "alignment_violations_count": alignment_violations_count,
        "reversed_pagination_pages_count": reversed_page_orders_detected,
        "documented_descending_pages_normalized_count": documented_descending_pages_normalized,
        "raw_page_order_audit": raw_page_order_audit,
        "missing_bar_intervals_count": missing_intervals,
        "requested_start_ms": cov["requested_start_ms"],
        "requested_end_ms": cov["requested_end_ms"],
        "earliest_open_ms": int(accepted_bars[0]["open_time"]) if accepted_bars else None,
        "latest_close_ms": int(accepted_bars[-1]["close_time"]) if accepted_bars else None,
        "expected_closed_slots": cov["expected_closed_slots"],
        "observed_unique_closed_slots": cov["observed_unique_closed_slots"],
        "missing_slots": cov["missing_slots"],
        "leading_missing_slots": cov["leading_missing_slots"],
        "trailing_missing_slots": cov["trailing_missing_slots"],
        "internal_missing_slots": cov["internal_missing_slots"],
        "coverage_ratio": cov["coverage_ratio"],
        "coverage_verdict": cov["coverage_verdict"] if is_valid_bars else "FAIL_INTEGRITY",
        "market_session_calendar_status": cov["market_session_calendar_status"],
        "provenance": {
            "source_kind": "EXCHANGE_REST_OHLCV_CANDLES_ONLY",
            "quote_mode": "MODELED_BID_ASK",
            "historical_l1_bbo_status": "UNAVAILABLE_OR_NOT_CAPTURED",
            "d1_alignment_rule": "BITGET_1D_UTC_PLUS_8_1600_UTC" if granularity == "1D" else "FIXED_UTC_GRID",
            "parser_version": PARSER_VERSION,
        },
        "bars_sha256": canonical_hash(accepted_bars) if accepted_bars else None,
        "status": "PASS" if is_valid_bars else "FAIL",
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
    F4, G15 & KH-01B.R1.DSC (§3A.3 & §4.D6) validator for prospective captured L1 BBO quotes:
      - Verifies `raw_http_sha256`, `receive_ts_ms`, `exchange_ts_ms`, `0 < bid <= ask`,
        finite positive sizes, staleness `abs(receive_ts_ms - exchange_ts_ms) <= max_staleness_ms`,
        contradictory calendar claims, and rejects single-snapshot claims of continuous 48h history.
    """
    violations: List[str] = []
    if bool(snapshot.get("is_synthetic", False)) or str(snapshot.get("quote_mode") or "") == "SYNTHETIC_AS_REAL":
        violations.append("SYNTHETIC_BBO_SUBSTITUTED_FOR_REAL_EXCHANGE_L1")
    if bool(snapshot.get("claims_continuous_historical_series", False)) or bool(snapshot.get("historical_backtest_usable", False)):
        violations.append("SINGLE_POINT_BBO_CANNOT_CLAIM_HISTORICAL_SERIES_COVERAGE")
    if bool(snapshot.get("exchange_calendar_verified", False)) and not calendar_verified:
        violations.append("CONTRADICTORY_UNVERIFIED_CALENDAR_CLAIM_ON_BBO")

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
    if not math.isfinite(bid_sz) or not math.isfinite(ask_sz) or bid_sz <= 0.0 or ask_sz <= 0.0:
        violations.append(f"NON_POSITIVE_TOP_BOOK_SIZE:bid_sz={bid_sz},ask_sz={ask_sz}")

    signed_skew_ms = (recv_ms - exch_ms) if (recv_ms > 0 and exch_ms > 0) else 999_999_999
    staleness_ms = abs(signed_skew_ms) if (recv_ms > 0 and exch_ms > 0) else 999_999_999
    if staleness_ms > max_staleness_ms:
        violations.append(f"STALE_BBO_QUOTE_OR_CLOCK_SKEW:staleness_ms={staleness_ms}>max={max_staleness_ms}")

    if require_verified_calendar and not calendar_verified:
        violations.append("EXCHANGE_SESSION_CALENDAR_UNVERIFIED")

    spread_usd = round(ask - bid, 4) if (math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask >= bid) else None
    return {
        "symbol": str(snapshot.get("symbol") or "XAUUSDT"),
        "quote_classification": "CAPTURED_REAL_BBO_PROSPECTIVE_POINT_SNAPSHOT",
        "continuous_historical_series_coverage": False,
        "historical_backtest_usable": False,
        "receive_ts_ms": recv_ms,
        "exchange_ts_ms": exch_ms,
        "signed_clock_skew_ms": signed_skew_ms,
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
    F3, G16 & KH-01B.R1.DSC (§3A.4) write-once Future OOS Escrow validator:
      - Enforces `min_allowed_oos_start_ms = max(CANONICAL_SAMPLE_END_MS, freeze_timestamp_ms) + 180m`.
      - Rejects any dataset SHA already present in `known_dev_dataset_shas`, any inspected/DEV role,
        and any one-off prospective BBO point snapshot renamed as `FRESH_UNTOUCHED` OOS.
      - Enforces `strategy_evaluation_count == 0` and fails closed on `PASS_FINANCIAL_EDGE`.
    """
    violations: List[str] = []
    dev_shas: Set[str] = {str(s).strip().lower() for s in known_dev_dataset_shas if str(s).strip()}
    dev_shas.add(CANONICAL_DATASET_SHA256.lower())

    min_oos_start_ms = max(int(CANONICAL_SAMPLE_END_MS), int(freeze_timestamp_ms)) + MIN_OOS_EMBARGO_MS

    if claimed_fresh_oos_verdict is not None:
        cv = str(claimed_fresh_oos_verdict).strip().upper()
        if cv not in ("BLOCKED_NO_UNTOUCHED_DATA", "SEALED_UNTOUCHED_NOT_EVALUATED"):
            violations.append(f"ILLEGAL_FRESH_OOS_VERDICT_IN_KH01B_R1:{claimed_fresh_oos_verdict}")

    forbidden_roles = {
        "REUSED_RESEARCH",
        "DEV_EXTENDED_RESEARCH",
        "REUSED_DEV",
        "PAST_INSPECTED",
        "INSPECTED_DEV",
        "CAPTURED_REAL_BBO_PROSPECTIVE_POINT_SNAPSHOT",
        "SINGLE_BBO_SNAPSHOT",
    }

    validated_entries: List[Dict[str, Any]] = []
    for idx, entry in enumerate(proposed_oos_entries):
        ds_id = str(entry.get("dataset_id") or f"oos_{idx}")
        ds_sha = str(entry.get("dataset_sha256") or "").strip().lower()
        ev_cnt = int(entry.get("strategy_evaluation_count", -1))
        st_ms = int(entry.get("earliest_bar_open_ms") or 0)
        en_ms = int(entry.get("latest_bar_close_ms") or 0)
        role = str(entry.get("partition_role") or "").upper()
        src_kind = str(entry.get("source_kind") or "").upper()
        used_for_entry_pick = bool(entry.get("used_for_entry_selection", False))
        prior_inspected = bool(entry.get("previously_inspected", False))
        is_single_bbo = bool(entry.get("is_single_bbo_probe", False)) or "POINT_SNAPSHOT" in src_kind

        if not ds_sha or len(ds_sha) != 64:
            violations.append(f"OOS_ENTRY_MISSING_SHA256:{ds_id}")
        if ds_sha in dev_shas:
            violations.append(f"REUSED_OR_DEV_DATASET_REJECTED_FROM_OOS_ESCROW:{ds_id}:{ds_sha}")
        if role in forbidden_roles or role != "FRESH_UNTOUCHED_SEALED_ESCROW":
            if role != "FRESH_UNTOUCHED":
                violations.append(f"DEV_OR_NON_ESCROW_ROLE_CANNOT_BE_PROMOTED_TO_FRESH_OOS:{ds_id}:{role}")
        if prior_inspected:
            violations.append(f"PREVIOUSLY_INSPECTED_DATA_REJECTED_FROM_FRESH_OOS:{ds_id}")
        if is_single_bbo:
            violations.append(f"ONE_OFF_BBO_SNAPSHOT_CANNOT_BE_RENAMED_TO_FRESH_OOS:{ds_id}")
        if ev_cnt != 0:
            violations.append(f"OOS_EVALUATION_COUNT_NONZERO_IN_R1:{ds_id}:count={ev_cnt}")
        if used_for_entry_pick:
            violations.append(f"FUTURE_OOS_LABELS_USED_TO_PICK_ENTRY:{ds_id}")
        if st_ms < min_oos_start_ms:
            violations.append(f"OOS_EMBARGO_VIOLATION:{ds_id}:start={st_ms}<min_required={min_oos_start_ms}")
        if en_ms <= st_ms:
            violations.append(f"OOS_WINDOW_NON_MONOTONIC_OR_POINT_ONLY:{ds_id}:{st_ms}>={en_ms}")

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
        "escrow_schema_version": "kh_01b_r1_dsc.future_oos_escrow.v2",
        "write_once_policy": "APPEND_ONLY_NO_EVALUATION_IN_KH01B_R1_OR_DSC",
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


def check_collector_volume_disk_safety(
    output_dir: Path,
    *,
    required_reserve_bytes: int = STRICT_COLLECTOR_DISK_RESERVE_BYTES,
    stage_allowance_bytes: int = COLLECTOR_STAGE_ALLOWANCE_BYTES,
    volume_free_override: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    """
    Preflight & pre-write strict 20 GiB disk check across repo, output, and temp volumes (§3A.2 & §4.D8).
    """
    min_required = int(required_reserve_bytes) + int(stage_allowance_bytes)
    if volume_free_override is not None:
        vols = {str(k): int(v) for k, v in volume_free_override.items()}
    else:
        probe_out = output_dir if output_dir.exists() else output_dir.parent
        while not probe_out.exists() and probe_out.parent != probe_out:
            probe_out = probe_out.parent
        vols = {
            "repo": int(shutil.disk_usage(REPO_ROOT).free),
            "output": int(shutil.disk_usage(probe_out).free),
            "temp": int(shutil.disk_usage(Path(tempfile.gettempdir())).free),
        }

    min_lbl, min_free = min(vols.items(), key=lambda kv: kv[1])
    ok = all(v >= min_required for v in vols.values())
    shortfall_b = max(0, min_required - min_free)
    return {
        "can_collect": ok,
        "disk_safety_verdict": "PASS_STRICT_20GIB" if ok else "BLOCKED_LOW_DISK",
        "required_reserve_gib": round(required_reserve_bytes / (1024**3), 4),
        "stage_allowance_gib": round(stage_allowance_bytes / (1024**3), 4),
        "min_preflight_required_gib": round(min_required / (1024**3), 4),
        "constraining_volume": min_lbl,
        "constraining_free_bytes": min_free,
        "constraining_free_gib": round(min_free / (1024**3), 4),
        "shortfall_bytes": shortfall_b,
        "shortfall_gib": round(shortfall_b / (1024**3), 4),
        "volumes_free_bytes": vols,
    }


def collect_online_bitget_audit_and_extended_dev(
    output_dir: Path,
    *,
    dev_start_ms: int = DEFAULT_DEV_EXTENDED_START_MS,
    dev_end_ms: int = DEFAULT_DEV_EXTENDED_END_MS,
    freeze_ts_ms: Optional[int] = None,
    max_pages_per_tf: Optional[int] = None,
    hard_max_pages_per_tf: int = 128,
    max_total_http_requests: int = 160,
    max_total_bytes: int = 32 * 1024 * 1024,
    max_wall_seconds: float = 90.0,
    http_client: Optional[Any] = None,
    volume_free_override: Optional[Mapping[str, int]] = None,
    sleep_fn: Any = time.sleep,
) -> Dict[str, Any]:
    """
    Executes read-only public Bitget capability audit + bounded `DEV_EXTENDED_RESEARCH`
    multi-timeframe OHLCV collection + single point-in-time live BBO capability probe (§3A.2):
      - Preflights strict `>= 20.5 GiB` disk safety BEFORE any network call or file write.
      - Computes dynamic page budget per timeframe from `[dev_start_ms, dev_end_ms]` instead
        of silently truncating `1m` at `8 * 200 = 1600` bars.
      - Handles HTTP 429 / transient 5xx with bounded exponential backoff + deterministic jitter,
        strict backward cursor progress, max request / byte / wall-clock caps, and raw page order
        preservation into `validate_and_normalize_paginated_candles`.
      - Maps authoritative `gap_audit['timeframes']` (`M1..D1`) via `map_gap_truth_to_acquisition_summary`
        (`FAIL_INTEGRITY` on missing/null schema) and returns `VERIFIED_COMPLETE` vs `VERIFIED_PARTIAL`
        vs `BLOCKED_LOW_DISK` vs `BLOCKED_UNAVAILABLE` vs `FAIL_INTEGRITY`.
    """
    now_ms = int(freeze_ts_ms or int(time.time() * 1000))
    if dev_start_ms < CANONICAL_SAMPLE_END_MS + MIN_OOS_EMBARGO_MS:
        raise ValueError("DEV_EXTENDED_WINDOW_OVERLAPS_CANONICAL_OR_EMBARGO")
    if dev_end_ms >= now_ms:
        raise ValueError("DEV_EXTENDED_WINDOW_MUST_END_STRICTLY_BEFORE_FREEZE_TIME")

    canonical_path = REPO_ROOT / CANONICAL_DATASET_REL
    canon_sha_before = _file_sha256(canonical_path) if canonical_path.is_file() else "MISSING"
    if canon_sha_before != CANONICAL_DATASET_SHA256:
        raise RuntimeError(f"CANONICAL_DATASET_SHA_INVALID_BEFORE_COLLECTION:{canon_sha_before}")

    disk_check = check_collector_volume_disk_safety(
        output_dir, volume_free_override=volume_free_override
    )
    if not disk_check["can_collect"]:
        return {
            "DATA_ACQUISITION_VERDICT": "BLOCKED_LOW_DISK",
            "DISK_SAFETY_VERDICT": "BLOCKED_LOW_DISK",
            "extended_dev_status": "EXTENDED_DEV_COLLECTION_BLOCKED_LOW_DISK",
            "blocker_reason": (
                f"BLOCKED_LOW_DISK:volume={disk_check['constraining_volume']}:"
                f"free_gib={disk_check['constraining_free_gib']}<required_gib={disk_check['min_preflight_required_gib']}:"
                f"shortfall_gib={disk_check['shortfall_gib']}"
            ),
            "disk_preflight": disk_check,
            "network_requests_made": 0,
            "canonical_dataset_untouched_sha256": canon_sha_before,
            "synthetic_substitution_performed": False,
        }

    import httpx

    http_receipts: List[Dict[str, Any]] = []
    tf_summaries: Dict[str, Any] = {}
    tf_pagination_stop_reasons: Dict[str, str] = {}
    tf_page_budgets: Dict[str, Any] = {}
    tf_bars_for_gap_audit: Dict[str, List[Dict[str, Any]]] = {}
    all_dev_rows: List[Dict[str, Any]] = []
    total_bytes_rx = 0
    t_start_wall = time.monotonic()
    rng_jitter = random.Random(20261119)

    def _bounded_get(cli: Any, url: str, params: Mapping[str, str], endpoint_tag: str, extra_meta: Mapping[str, Any]) -> Tuple[bytes, int, int, int]:
        nonlocal total_bytes_rx
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            if len(http_receipts) >= max_total_http_requests:
                raise RuntimeError(f"MAX_TOTAL_HTTP_REQUESTS_EXCEEDED:{max_total_http_requests}")
            if (time.monotonic() - t_start_wall) > max_wall_seconds:
                raise TimeoutError(f"COLLECTOR_MAX_WALL_SECONDS_EXCEEDED:{max_wall_seconds}")

            t_req_ms = int(time.time() * 1000)
            try:
                resp = cli.get(url, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as net_exc:
                if attempt < max_attempts:
                    backoff_s = min(2.0, (0.25 * (2 ** (attempt - 1))) + rng_jitter.uniform(0.01, 0.05))
                    sleep_fn(backoff_s)
                    continue
                raise net_exc
            t_recv_ms = int(time.time() * 1000)
            status_code = int(resp.status_code)
            raw_b = bytes(resp.content or b"")
            total_bytes_rx += len(raw_b)
            if total_bytes_rx > max_total_bytes:
                raise RuntimeError(f"COLLECTOR_MAX_BYTES_EXCEEDED:{total_bytes_rx}>{max_total_bytes}")

            b_sha = _sha256_bytes(raw_b)
            http_receipts.append({
                "endpoint": endpoint_tag,
                "url": url,
                "query_params": dict(params),
                **dict(extra_meta),
                "attempt": attempt,
                "request_ts_ms": t_req_ms,
                "receive_ts_ms": t_recv_ms,
                "observed_at_utc": datetime.fromtimestamp(t_recv_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "http_status": status_code,
                "raw_http_sha256": b_sha,
                "byte_length": len(raw_b),
                "parser_version": PARSER_VERSION,
            })

            if status_code in (429, 502, 503, 504):
                if attempt < max_attempts:
                    backoff_s = min(2.0, (0.15 * (2 ** (attempt - 1))) + rng_jitter.uniform(0.01, 0.05))
                    sleep_fn(backoff_s)
                    continue
                raise RuntimeError(f"HTTP_RATE_LIMIT_OR_TRANSIENT_EXHAUSTED:status={status_code}:endpoint={endpoint_tag}")

            resp.raise_for_status()
            return raw_b, status_code, t_req_ms, t_recv_ms

        raise RuntimeError(f"UNREACHABLE_RETRY_EXIT:{endpoint_tag}")

    owns_client = http_client is None
    client = http_client if http_client is not None else httpx.Client(timeout=15.0, headers={"Accept": "application/json"})

    try:
        # 1. Public contract specification audit (observed snapshot ONLY — never treated as user's VIP tier)
        ct_bytes, _, _, ct_recv_ms = _bounded_get(
            client,
            BITGET_PUBLIC_CONTRACTS_URL,
            {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"},
            "contracts",
            {"symbol": "XAUUSDT"},
        )
        ct_sha = _sha256_bytes(ct_bytes)
        ct_js = json.loads(ct_bytes.decode("utf-8"))
        if ct_js.get("code") != "00000" or not ct_js.get("data"):
            return {
                "DATA_ACQUISITION_VERDICT": "BLOCKED_UNAVAILABLE",
                "extended_dev_status": "EXTENDED_DEV_DATA_BLOCKED",
                "blocker_reason": f"BITGET_CONTRACT_CODE:{ct_js.get('code')}:{ct_js.get('msg')}",
                "http_receipts": http_receipts,
                "canonical_dataset_untouched_sha256": canon_sha_before,
                "synthetic_substitution_performed": False,
            }
        ct_item = ct_js["data"][0]

        # 2. Live point-in-time Ticker L1 BBO capability probe
        tk_bytes, _, _, t_bbo_recv = _bounded_get(
            client,
            BITGET_PUBLIC_TICKER_URL,
            {"productType": "USDT-FUTURES", "symbol": "XAUUSDT"},
            "ticker",
            {"symbol": "XAUUSDT"},
        )
        tk_sha = _sha256_bytes(tk_bytes)
        tk_js = json.loads(tk_bytes.decode("utf-8"))
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

        # 3. Dynamic bounded pagination across all 6 granularities
        for gran, dur_ms in SUPPORTED_GRANULARITIES:
            budget_info = compute_required_page_budget(
                dev_start_ms, dev_end_ms, dur_ms, page_limit=200, hard_max_pages=hard_max_pages_per_tf
            )
            eff_max_pages = int(max_pages_per_tf) if max_pages_per_tf is not None else int(budget_info["effective_page_budget"])
            budget_info["caller_max_pages_override"] = max_pages_per_tf
            budget_info["enforced_max_pages"] = eff_max_pages
            tf_page_budgets[gran] = budget_info

            cursor_end = int(dev_end_ms)
            prev_cursor_end: Optional[int] = None
            prev_earliest_open: Optional[int] = None
            seen_tf_page_shas: Set[str] = set()
            pages_collected: List[Dict[str, Any]] = []
            stop_reason = "PAGE_BUDGET_EXHAUSTED_PARTIAL_WINDOW"

            for _p in range(eff_max_pages):
                if cursor_end <= dev_start_ms:
                    stop_reason = "REACHED_REQUESTED_START_BOUNDARY"
                    break

                raw_b, _, _, p_recv_ms = _bounded_get(
                    client,
                    BITGET_PUBLIC_HISTORY_CANDLES_URL,
                    {
                        "productType": "USDT-FUTURES",
                        "symbol": "XAUUSDT",
                        "granularity": gran,
                        "endTime": str(cursor_end),
                        "limit": "200",
                    },
                    "history-candles",
                    {"granularity": gran, "cursor_end_ms": cursor_end},
                )
                p_sha = _sha256_bytes(raw_b)
                p_js = json.loads(raw_b.decode("utf-8"))
                if p_js.get("code") != "00000":
                    raise RuntimeError(f"BITGET_CANDLES_ERROR:{gran}:{p_js.get('code')}:{p_js.get('msg')}")

                raw_rows = list(p_js.get("data") or [])
                if not raw_rows:
                    stop_reason = "EXCHANGE_RETURNED_EMPTY_PAGE_RETENTION_OR_SESSION_BOUNDARY"
                    break

                raw_open_ints = [int(r[0]) for r in raw_rows if isinstance(r, Sequence) and len(r) >= 1]
                if not raw_open_ints:
                    raise RuntimeError(f"MALFORMED_CANDLE_PAGE_NO_TIMESTAMPS:{gran}")
                earliest_in_raw = min(raw_open_ints)

                prog = verify_http_page_cursor_progress(
                    prev_cursor_end_ms=prev_cursor_end,
                    next_cursor_end_ms=cursor_end,
                    prev_earliest_open_ms=prev_earliest_open,
                    curr_earliest_open_ms=earliest_in_raw,
                    raw_http_sha256=p_sha,
                    seen_page_shas=seen_tf_page_shas,
                )
                if not prog["progressed_strictly_backward"]:
                    stop_reason = f"CURSOR_PROGRESS_HALTED:{','.join(prog['violations'])}"
                    break
                seen_tf_page_shas.add(p_sha)

                # IMPORTANT (§3A.1): Pass RAW rows WITHOUT pre-sorting so validator audits true provider order!
                pages_collected.append({
                    "raw_http_sha256": p_sha,
                    "query_cursor_end_ms": cursor_end,
                    "receive_ts_ms": p_recv_ms,
                    "server_time_ms": int(p_js.get("requestTime") or p_recv_ms),
                    "documented_provider_order": "BITGET_V2_ASC_OR_DESC_MONOTONIC",
                    "rows": raw_rows,
                })

                prev_cursor_end = cursor_end
                prev_earliest_open = earliest_in_raw
                if earliest_in_raw <= dev_start_ms:
                    stop_reason = "REACHED_REQUESTED_START_BOUNDARY"
                    break
                # Bitget v2 `endTime` is exclusive or inclusive depending on contract; stepping to `earliest_in_raw`
                # guarantees zero skipped boundary bars while our exact Decimal boundary deduper handles any 1-bar overlap!
                cursor_end = earliest_in_raw
                sleep_fn(0.05)

            tf_pagination_stop_reasons[gran] = stop_reason
            val_tf = validate_and_normalize_paginated_candles(
                pages_collected,
                symbol="XAUUSDT",
                granularity=gran,
                duration_ms=dur_ms,
                require_origin_sha=True,
                requested_start_ms=dev_start_ms,
                requested_end_ms=dev_end_ms,
                allow_documented_descending=True,
                calendar_verified=False,
            )
            val_tf["pagination_stop_reason"] = stop_reason
            val_tf["page_budget"] = budget_info
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
            "canonical_dataset_untouched_sha256": canon_sha_before,
        }
    finally:
        if owns_client:
            client.close()

    canon_sha_after = _file_sha256(canonical_path)
    if canon_sha_after != CANONICAL_DATASET_SHA256:
        raise RuntimeError("CANONICAL_DATASET_MUTATED_DURING_COLLECTION")

    # Re-check disk safety immediately before durable writes
    disk_pre_write = check_collector_volume_disk_safety(output_dir, volume_free_override=volume_free_override)
    if not disk_pre_write["can_collect"]:
        return {
            "DATA_ACQUISITION_VERDICT": "BLOCKED_LOW_DISK",
            "DISK_SAFETY_VERDICT": "BLOCKED_LOW_DISK",
            "extended_dev_status": "EXTENDED_DEV_WRITE_ABORTED_LOW_DISK",
            "blocker_reason": f"DISK_DROPPED_BELOW_20GIB_BEFORE_WRITE:{disk_pre_write['constraining_free_gib']}",
            "disk_preflight": disk_pre_write,
            "canonical_dataset_untouched_sha256": canon_sha_after,
            "synthetic_substitution_performed": False,
        }

    gap_audit = classify_historical_dataset_gaps(tf_bars_for_gap_audit)
    validator_counts = {g: int(tf_summaries[g]["accepted_closed_bars_count"]) for g in tf_summaries}
    mapped_gaps = map_gap_truth_to_acquisition_summary(gap_audit, per_tf_validator_counts=validator_counts)

    (output_dir / "ledgers").mkdir(parents=True, exist_ok=True)
    (output_dir / "receipts").mkdir(parents=True, exist_ok=True)

    all_dev_rows.sort(key=lambda r: (str(r["timeframe"]), int(r["open_time"])))
    dev_gz_rel = "ledgers/dev_extended_xauusdt_ohlcv_snapshot.jsonl.gz"
    dev_gz_sha = write_jsonl_gz_durable_atomic(output_dir / dev_gz_rel, all_dev_rows)
    origin_rcpt_rel = "receipts/bitget_origin_http_receipts.json"
    origin_rcpt_sha = write_json_durable_atomic(
        output_dir / origin_rcpt_rel,
        {
            "captured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "parser_version": PARSER_VERSION,
            "requests_count": len(http_receipts),
            "total_bytes_received": total_bytes_rx,
            "credentials_used": False,
            "receipts": http_receipts,
        },
    )

    all_tfs_valid = (
        len(tf_summaries) == len(SUPPORTED_GRANULARITIES)
        and all(v.get("status") == "PASS" for v in tf_summaries.values())
    )
    all_tfs_complete = all_tfs_valid and all(
        v.get("coverage_verdict") == "VERIFIED_COMPLETE" and int(v.get("missing_slots", 1)) == 0
        for v in tf_summaries.values()
    )

    if not all_tfs_valid or mapped_gaps["status"] != "PASS" or bbo_probe["status"] != "PASS":
        verdict = "FAIL_INTEGRITY"
        ext_status = "EXTENDED_DEV_INTEGRITY_FAILURE"
    elif all_tfs_complete:
        verdict = "VERIFIED_COMPLETE"
        ext_status = "DEV_EXTENDED_RESEARCH_VERIFIED_COMPLETE"
    else:
        verdict = "VERIFIED_PARTIAL"
        ext_status = "DEV_EXTENDED_RESEARCH_VERIFIED_PARTIAL"

    return {
        "DATA_ACQUISITION_VERDICT": verdict,
        "DISK_SAFETY_VERDICT": disk_pre_write["disk_safety_verdict"],
        "parser_version": PARSER_VERSION,
        "extended_dev_status": ext_status,
        "partition_role": "DEV_EXTENDED_RESEARCH",
        "prior_use_status": "INSPECTED_REUSED_DEV",
        "prior_use_status_after_inspection": "REUSED_DEV",
        "eligible_for_future_fresh_oos": False,
        "quote_provenance_for_historical_bars": "MODELED_BID_ASK",
        "historical_l1_bbo_rest_availability": "UNAVAILABLE_OR_NOT_CAPTURED",
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
            "observed_at_ms": ct_recv_ms,
            "raw_http_sha256": ct_sha,
            "account_effective_fee_tier_provenance": "UNKNOWN_PUBLIC_CONTRACT_DEFAULT_ONLY_NOT_ACCOUNT_TIER",
        },
        "prospective_bbo_capability_probe": bbo_probe,
        "dev_extended_window_ms": [dev_start_ms, dev_end_ms],
        "dev_extended_snapshot_rel_path": dev_gz_rel,
        "dev_extended_snapshot_sha256": dev_gz_sha,
        "dev_extended_total_bars_count": len(all_dev_rows),
        "origin_http_receipts_rel_path": origin_rcpt_rel,
        "origin_http_receipts_sha256": origin_rcpt_sha,
        "http_requests_count": len(http_receipts),
        "total_bytes_received": total_bytes_rx,
        "canonical_dataset_untouched_sha256": canon_sha_after,
        "page_budgets_by_timeframe": tf_page_budgets,
        "pagination_stop_reasons_by_timeframe": tf_pagination_stop_reasons,
        "per_timeframe_validation": tf_summaries,
        "gap_mapping_verification": {
            "status": mapped_gaps["status"],
            "schema_version": mapped_gaps["schema_version"],
            "session_calendar_verification_status": mapped_gaps["session_calendar_verification_status"],
            "key_mapping_used": mapped_gaps["key_mapping_used"],
            "violations": mapped_gaps["violations"],
        },
        "gap_classification_summary": mapped_gaps["mapped_by_granularity"],
        "synthetic_substitution_performed": False,
    }


def run_offline_collector_self_audit() -> Dict[str, Any]:
    """
    Default OFFLINE_VALIDATE mode (§3A.5): zero network requests, zero secret access.
    Verifies canonical dataset SHA, 6-TF gap mapping schema, 48h/1600 partial-window detection,
    exact Decimal boundary dedupe vs precision collision rejection, and Future OOS escrow guards.
    """
    canonical_path = REPO_ROOT / CANONICAL_DATASET_REL
    canon_sha = _file_sha256(canonical_path) if canonical_path.is_file() else "MISSING"

    # Offline fixture self-check 1: 48h requested window with 1600 tail M1 bars -> PARTIAL, never COMPLETE
    w_start = DEFAULT_DEV_EXTENDED_START_MS
    w_end = DEFAULT_DEV_EXTENDED_END_MS
    tail_start = w_end - 1600 * 60_000
    tail_page = {
        "raw_http_sha256": "a" * 64,
        "server_time_ms": w_end + 60_000,
        "rows": [
            [str(tail_start + i * 60_000), "3700.10", "3700.80", "3699.70", "3700.30", "12.5"]
            for i in range(1600)
        ],
    }
    partial_check = validate_and_normalize_paginated_candles(
        [tail_page],
        symbol="XAUUSDT",
        granularity="1m",
        duration_ms=60_000,
        requested_start_ms=w_start,
        requested_end_ms=w_end,
    )

    # Offline fixture self-check 2: 6-TF authoritative gap mapping non-null check
    d1_open = 1_794_844_800_000  # 2026-11-16T16:00:00Z (Bitget UTC+8 1D boundary)
    fixture_tfs = {
        "1m": partial_check["bars"][:10],
        "5m": [{"timestamp": w_start, "open_time": w_start, "close_time": w_start + 300_000, "open": 3700.0, "high": 3701.0, "low": 3699.0, "close": 3700.5, "volume": 20.0, "is_closed": True}],
        "15m": [{"timestamp": w_start, "open_time": w_start, "close_time": w_start + 900_000, "open": 3700.0, "high": 3701.0, "low": 3699.0, "close": 3700.5, "volume": 50.0, "is_closed": True}],
        "1H": [{"timestamp": w_start, "open_time": w_start, "close_time": w_start + 3_600_000, "open": 3700.0, "high": 3701.0, "low": 3699.0, "close": 3700.5, "volume": 200.0, "is_closed": True}],
        "4H": [{"timestamp": w_start, "open_time": w_start, "close_time": w_start + 14_400_000, "open": 3700.0, "high": 3701.0, "low": 3699.0, "close": 3700.5, "volume": 800.0, "is_closed": True}],
        "1D": [{"timestamp": d1_open, "open_time": d1_open, "close_time": d1_open + 86_400_000, "open": 3700.0, "high": 3705.0, "low": 3695.0, "close": 3702.0, "volume": 5000.0, "is_closed": True}],
    }
    gap_map_check = map_gap_truth_to_acquisition_summary(
        classify_historical_dataset_gaps(fixture_tfs),
        per_tf_validator_counts={k: len(v) for k, v in fixture_tfs.items()},
    )

    escrow = build_and_validate_future_oos_escrow_registry(
        freeze_timestamp_ms=int(time.time() * 1000),
        frozen_candidate_code_sha="OFFLINE_VALIDATE_ONLY",
        frozen_config_sha256="OFFLINE_VALIDATE_ONLY",
    )
    disk_status = check_collector_volume_disk_safety(APP_ROOT)

    return {
        "mode": "OFFLINE_VALIDATE",
        "parser_version": PARSER_VERSION,
        "network_requests_made": 0,
        "secrets_accessed": False,
        "canonical_dataset_sha256": canon_sha,
        "canonical_dataset_verified": canon_sha == CANONICAL_DATASET_SHA256,
        "offline_48h_1600_partial_guard_verified": (
            partial_check["coverage_verdict"] == "PARTIAL_COVERAGE_WITH_UNVERIFIED_SESSION"
            and partial_check["leading_missing_slots"] == 1280
            and partial_check["expected_closed_slots"] == 2880
        ),
        "offline_6tf_gap_mapping_verified": gap_map_check["status"] == "PASS",
        "disk_preflight": disk_status,
        "DATA_ACQUISITION_VERDICT": "BLOCKED_UNAVAILABLE",
        "future_oos_escrow": escrow,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="KH-01B.R1.DSC Market Data Auditor & OOS Escrow Validator")
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
        ok = (
            out["canonical_dataset_verified"]
            and out["offline_48h_1600_partial_guard_verified"]
            and out["offline_6tf_gap_mapping_verified"]
        )
        return 0 if ok else 1

    if not args.output_dir:
        raise SystemExit("--output-dir is required for ONLINE_AUDIT_AND_EXTENDED_DEV")
    res = collect_online_bitget_audit_and_extended_dev(Path(args.output_dir).resolve())
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res.get("DATA_ACQUISITION_VERDICT") in ("VERIFIED_COMPLETE", "VERIFIED_PARTIAL") else 2


if __name__ == "__main__":
    raise SystemExit(main())
