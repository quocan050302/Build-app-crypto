"""
aurum-desk/backend/services/kh_03_r6_market_data_service.py

AURUM DESK — KH-03.R6 (M4) | MARKET DATA CONTRACTS, STRICT DECIMAL NORMALIZATION & COVERAGE
SPEC VERSION: KH-03.R6-v1.0 | STRICT DECIMAL & UTC-MS | ZERO FABRICATION OR SILENT IMPUTATION

Implements:
  - F19: paginate_r6_ohlcv_history(...) — deterministic half-open [start,end) pagination, dedup,
         contradictory duplicate quarantine (never last-wins), unclosed/future exclusion, strict
         Decimal OHLC geometry, cursor-replay/no-progress stop, calendar/DST gap accounting.
  - F20: normalize_r6_bbo_event(...) & validate_r6_bbo_epoch(...) — strict Decimal bid/ask/sizes,
         tick alignment, crossed/locked/out-of-order/stale/missing-snapshot detection, never REST
         snapshot classified as streaming.
  - F21: build_r6_bbo_coverage_report(...) — separates observed-event coverage vs valid
         state-known 1s slot coverage (carry-forward ONLY inside healthy epoch within bounded
         freshness; NEVER across disconnects, missing snapshots, stale quotes, or duplicate inflation;
         unknown calendar -> DENOMINATOR_UNKNOWN_BLOCKED).
  - F22: acquire_r6_funding_history(...) / validate_r6_funding_settlements(...) — signed Decimal
         realized vs indicative separation, observed schedule interval (NOT hardcoded 8h), missing
         windows -> PARTIAL/MISSING (NEVER zero-filled!).
  - F23: validate_r6_owner_fee_evidence(...) / build_r6_slippage_registry(...) — redacted owner
         proof or UNKNOWN_NOT_LOCALLY_VERIFIED / MISSING_NO_OWNER_FILL_EVIDENCE (public quotes/fees
         never certify owner effective fee or execution slippage).
  - F24: build_r6_market_data_gap_ledger(...) — deterministic canonical gap/anomaly ledger.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from services.kh_03_r6_durable_grant_service import _scan_for_secrets_or_bypass, _sha256_canonical
from services.kh_03_r6_source_contract_service import (
    API_FAMILY_CLASSIC_V2,
    APPROVED_SYMBOL_R6,
    APPROVED_VENUE_R6,
    SCHEMA_VERSION_KH03_R6,
    SPEC_VERSION_KH03_R6,
)

BAR_MS_BY_GRANULARITY_R6: Dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1H": 3_600_000,
    "4H": 14_400_000,
    "1D": 86_400_000,
}


def _strict_dec(val: Any, name: str) -> Decimal:
    if isinstance(val, bool) or val is None:
        raise ValueError(f"INVALID_DECIMAL:{name}")
    if isinstance(val, float) and not math.isfinite(val):
        raise ValueError(f"NONFINITE_DECIMAL:{name}")
    s = str(val).strip()
    if not s:
        raise ValueError(f"EMPTY_DECIMAL:{name}")
    try:
        d = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"UNPARSEABLE_DECIMAL:{name}:{s}") from exc
    if not d.is_finite():
        raise ValueError(f"NONFINITE_DECIMAL:{name}")
    return d


def _dec_str(d: Decimal) -> str:
    return format(d.normalize(), "f") if d != 0 else "0"


def _strict_ms(val: Any, name: str) -> int:
    if isinstance(val, bool) or val is None:
        raise ValueError(f"INVALID_MS:{name}")
    if isinstance(val, float):
        if not math.isfinite(val) or not val.is_integer():
            raise ValueError(f"NON_INT_MS:{name}")
        return int(val)
    iv = int(str(val).strip())
    if iv <= 0:
        raise ValueError(f"NON_POSITIVE_MS:{name}:{iv}")
    return iv


# =====================================================================
# F19: OHLCV Pagination, Validation, Dedup, Conflict Quarantine & Gaps
# =====================================================================
def paginate_r6_ohlcv_history(
    *,
    symbol: str,
    granularity: str,
    window_start_ms: int,
    window_end_ms: int,
    server_time_ms: int,
    page_fetcher: Callable[[Optional[int], int], Mapping[str, Any]],
    max_pages: int,
    calendar_expected_open_times_ms: Optional[Sequence[int]] = None,
    tick_size: Optional[str] = None,
    provenance_class: str = "QA_LOOPBACK",
) -> Dict[str, Any]:
    """
    Paginates half-open UTC [window_start_ms, window_end_ms) OHLCV bars backwards from `window_end_ms`.
    Stops on cursor replay, no progress, max_pages, out-of-range, or page error.
    Excludes unclosed/future bars from canonical rows (retains raw + reason).
    Quarantines contradictory duplicates rather than applying last-wins.
    """
    if symbol != APPROVED_SYMBOL_R6:
        return {"ok": False, "status": "REJECTED_SYMBOL_MISMATCH", "violations": [f"BAD_SYMBOL:{symbol}"]}
    bar_ms = BAR_MS_BY_GRANULARITY_R6.get(granularity)
    if bar_ms is None:
        return {"ok": False, "status": "REJECTED_UNSUPPORTED_GRANULARITY", "violations": [f"BAD_GRANULARITY:{granularity}"]}
    if window_start_ms >= window_end_ms:
        return {"ok": False, "status": "REJECTED_INVALID_WINDOW", "violations": ["START_GE_END"]}

    tick_dec = _strict_dec(tick_size, "tick_size") if tick_size is not None else None

    seen_cursors: set[int] = set()
    pages_fetched: List[Dict[str, Any]] = []
    raw_rows_count = 0
    exact_duplicates_count = 0
    unclosed_or_future_excluded: List[Dict[str, Any]] = []
    invalid_rows: List[Dict[str, Any]] = []
    quarantined_conflicts: List[Dict[str, Any]] = []
    accepted_by_ts: Dict[int, Dict[str, Any]] = {}
    stop_reason = "COMPLETE_REACHED_WINDOW_START"
    violations: List[str] = []

    cursor_end_ms = int(window_end_ms)
    for page_idx in range(1, int(max_pages) + 1):
        if cursor_end_ms in seen_cursors:
            stop_reason = "STOPPED_CURSOR_REPLAY_GUARD"
            violations.append(f"PAGINATION_CURSOR_REPEATED:{cursor_end_ms}")
            break
        seen_cursors.add(cursor_end_ms)

        page_resp = page_fetcher(cursor_end_ms, page_idx)
        if not isinstance(page_resp, Mapping) or page_resp.get("ok") is not True:
            stop_reason = "STOPPED_PAGE_FETCH_ERROR"
            violations.append(f"PAGE_ERROR:page={page_idx}:reason={page_resp.get('error') if isinstance(page_resp, Mapping) else 'INVALID'}")
            break

        rows = page_resp.get("rows") or []
        raw_sha = str(page_resp.get("raw_sha256") or _sha256_canonical(rows))
        pages_fetched.append({
            "page_no": page_idx,
            "requested_end_ms": cursor_end_ms,
            "returned_rows": len(rows),
            "raw_sha256": raw_sha,
        })

        if not rows:
            stop_reason = "COMPLETE_EMPTY_PAGE"
            break

        min_ts_in_page: Optional[int] = None
        for r_idx, row in enumerate(rows):
            raw_rows_count += 1
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                invalid_rows.append({"page_no": page_idx, "row_idx": r_idx, "reason": "MALFORMED_OHLCV_ROW_SHAPE"})
                continue
            try:
                ts = _strict_ms(row[0], "open_time_ms")
                o = _strict_dec(row[1], "open")
                h = _strict_dec(row[2], "high")
                l = _strict_dec(row[3], "low")
                c = _strict_dec(row[4], "close")
                v = _strict_dec(row[5], "volume")
            except ValueError as exc:
                invalid_rows.append({"page_no": page_idx, "row_idx": r_idx, "reason": str(exc)})
                continue

            if min_ts_in_page is None or ts < min_ts_in_page:
                min_ts_in_page = ts

            # Check timestamp alignment to granularity grid
            if ts % bar_ms != 0:
                invalid_rows.append({"page_no": page_idx, "row_idx": r_idx, "open_time_ms": ts, "reason": "TIMESTAMP_NOT_ALIGNED_TO_BAR_GRID"})
                continue

            # OHLC geometry & non-negativity
            if not (l > Decimal("0") and l <= o <= h and l <= c <= h and h >= l and v >= Decimal("0")):
                invalid_rows.append({"page_no": page_idx, "row_idx": r_idx, "open_time_ms": ts, "reason": "INVALID_OHLC_GEOMETRY_OR_NEGATIVE"})
                continue

            if tick_dec is not None:
                if any((px % tick_dec) != Decimal("0") for px in (o, h, l, c)):
                    invalid_rows.append({"page_no": page_idx, "row_idx": r_idx, "open_time_ms": ts, "reason": "PRICE_NOT_ALIGNED_TO_TICK"})
                    continue

            # Exclude out-of-window bars ([window_start_ms, window_end_ms))
            if ts < window_start_ms or ts >= window_end_ms:
                continue

            # No lookahead: exclude unclosed or future bars from canonical DEV (preserve raw + reason!)
            if ts + bar_ms > server_time_ms:
                unclosed_or_future_excluded.append({
                    "open_time_ms": ts,
                    "close_time_ms": ts + bar_ms,
                    "server_time_ms": server_time_ms,
                    "reason": "UNCLOSED_OR_FUTURE_BAR_EXCLUDED_FROM_CANONICAL",
                    "raw_page_sha256": raw_sha,
                })
                continue

            norm_row = {
                "venue": APPROVED_VENUE_R6,
                "api_family": API_FAMILY_CLASSIC_V2,
                "symbol": symbol,
                "granularity": granularity,
                "open_time_ms": ts,
                "close_time_ms": ts + bar_ms,
                "open": _dec_str(o),
                "high": _dec_str(h),
                "low": _dec_str(l),
                "close": _dec_str(c),
                "volume": _dec_str(v),
                "is_closed": True,
                "provenance_class": provenance_class,
                "raw_page_sha256": raw_sha,
                "row_locator": f"p{page_idx}:r{r_idx}",
            }
            semantic_key = (norm_row["open"], norm_row["high"], norm_row["low"], norm_row["close"], norm_row["volume"])

            if ts in accepted_by_ts:
                prev = accepted_by_ts[ts]
                prev_sem = (prev["open"], prev["high"], prev["low"], prev["close"], prev["volume"])
                if prev_sem == semantic_key:
                    exact_duplicates_count += 1
                else:
                    # Contradictory duplicate -> QUARANTINE BOTH, NEVER last-wins!
                    del accepted_by_ts[ts]
                    quarantined_conflicts.append({
                        "open_time_ms": ts,
                        "reason": "CONTRADICTORY_DUPLICATE_OHLCV_QUARANTINED",
                        "first_values": prev_sem,
                        "conflicting_values": semantic_key,
                    })
                    violations.append(f"CONTRADICTORY_OHLCV_DUPLICATE:{ts}")
            elif any(q["open_time_ms"] == ts for q in quarantined_conflicts):
                quarantined_conflicts.append({"open_time_ms": ts, "reason": "REPEAT_OF_QUARANTINED_TIMESTAMP"})
            else:
                accepted_by_ts[ts] = norm_row

        if min_ts_in_page is None:
            stop_reason = "STOPPED_NO_VALID_TIMESTAMPS_IN_PAGE"
            violations.append("PAGINATION_NO_PROGRESS_NO_VALID_TS")
            break
        if min_ts_in_page <= window_start_ms:
            stop_reason = "COMPLETE_REACHED_WINDOW_START"
            break
        next_cursor = min_ts_in_page
        if next_cursor >= cursor_end_ms:
            stop_reason = "STOPPED_NON_PROGRESSING_CURSOR"
            violations.append(f"PAGINATION_NO_PROGRESS:next={next_cursor}>=cur={cursor_end_ms}")
            break
        cursor_end_ms = next_cursor
    else:
        if cursor_end_ms > window_start_ms:
            stop_reason = "STOPPED_MAX_PAGES_CAP"

    canonical_rows = [accepted_by_ts[k] for k in sorted(accepted_by_ts.keys())]

    # Calendar / DST denominator honesty (never synthetic zero-fill!)
    missing_intervals: List[Dict[str, Any]] = []
    if calendar_expected_open_times_ms is None:
        calendar_status = "CALENDAR_DENOMINATOR_UNKNOWN_INCOMPLETE"
        coverage_ratio = None
    else:
        exp_set = sorted(int(t) for t in calendar_expected_open_times_ms if window_start_ms <= int(t) < window_end_ms and int(t) + bar_ms <= server_time_ms)
        got_set = set(accepted_by_ts.keys())
        for et in exp_set:
            if et not in got_set:
                missing_intervals.append({"open_time_ms": et, "close_time_ms": et + bar_ms, "reason": "MISSING_EXPECTED_IN_SESSION_BAR"})
        calendar_status = "VERIFIED_CALENDAR_COMPLETE" if not missing_intervals else "VERIFIED_CALENDAR_HAS_GAPS"
        coverage_ratio = round(len(got_set & set(exp_set)) / float(len(exp_set)), 6) if exp_set else 0.0

    ok = len(violations) == 0 and len(quarantined_conflicts) == 0 and len(invalid_rows) == 0 and calendar_status == "VERIFIED_CALENDAR_COMPLETE"
    return {
        "ok": ok,
        "status": "OHLCV_VALID_COMPLETE" if ok else ("OHLCV_INCOMPLETE_UNKNOWN_CALENDAR" if calendar_status == "CALENDAR_DENOMINATOR_UNKNOWN_INCOMPLETE" and not violations else "OHLCV_PARTIAL_OR_INVALID"),
        "symbol": symbol,
        "granularity": granularity,
        "window_start_ms": window_start_ms,
        "window_end_ms": window_end_ms,
        "stop_reason": stop_reason,
        "pages_fetched_count": len(pages_fetched),
        "pages_fetched": pages_fetched,
        "raw_rows_count": raw_rows_count,
        "canonical_valid_count": len(canonical_rows),
        "exact_duplicates_deduped_count": exact_duplicates_count,
        "unclosed_or_future_excluded_count": len(unclosed_or_future_excluded),
        "unclosed_or_future_excluded": unclosed_or_future_excluded,
        "invalid_rows_count": len(invalid_rows),
        "invalid_rows": invalid_rows,
        "quarantined_conflicts_count": len(quarantined_conflicts),
        "quarantined_conflicts": quarantined_conflicts,
        "calendar_status": calendar_status,
        "coverage_ratio": coverage_ratio,
        "missing_intervals_count": len(missing_intervals),
        "missing_intervals": missing_intervals,
        "canonical_rows": canonical_rows,
        "canonical_rows_sha256": _sha256_canonical(canonical_rows),
        "violations": violations,
    }


# =====================================================================
# F20: BBO Event Normalization & Epoch Validation
# =====================================================================
def normalize_r6_bbo_event(
    raw_event: Mapping[str, Any],
    *,
    expected_symbol: str = APPROVED_SYMBOL_R6,
    tick_size: Optional[str] = None,
    max_staleness_ms: int = 5_000,
    prev_event_ts_ms: Optional[int] = None,
    source_transport: str = "WS_BOOKS1",  # "WS_BOOKS1" | "REST_TICKER_SNAPSHOT"
) -> Dict[str, Any]:
    """
    Strictly validates a single L1 BBO observation using Decimal.
    Distinguishes VALID, LOCKED, CROSSED, INVALID_TICK, OUT_OF_ORDER, STALE,
    and REST_SNAPSHOT_NOT_STREAMING.
    """
    if source_transport != "WS_BOOKS1":
        return {
            "valid_for_streaming_bbo": False,
            "quality_state": "REST_SNAPSHOT_NOT_STREAMING",
            "violations": ["REST_QUOTE_SNAPSHOT_CANNOT_BE_CLASSIFIED_CONTINUOUS_BBO"],
            "normalized": None,
        }

    viols: List[str] = []
    sym = str(raw_event.get("symbol") or "").strip()
    if sym != expected_symbol:
        viols.append(f"BBO_SYMBOL_MISMATCH:{sym}")

    try:
        bid_px = _strict_dec(raw_event.get("bid_price"), "bid_price")
        bid_sz = _strict_dec(raw_event.get("bid_size"), "bid_size")
        ask_px = _strict_dec(raw_event.get("ask_price"), "ask_price")
        ask_sz = _strict_dec(raw_event.get("ask_size"), "ask_size")
        ev_ts_ms = _strict_ms(raw_event.get("event_ts_ms"), "event_ts_ms")
        rx_ts_ms = _strict_ms(raw_event.get("receive_utc_ms"), "receive_utc_ms")
    except ValueError as exc:
        return {"valid_for_streaming_bbo": False, "quality_state": "MALFORMED_BBO", "violations": [str(exc)], "normalized": None}

    if bid_px <= Decimal("0") or ask_px <= Decimal("0") or bid_sz < Decimal("0") or ask_sz < Decimal("0"):
        viols.append("NON_POSITIVE_PRICE_OR_NEGATIVE_SIZE")

    quality_state = "VALID"
    if bid_px > ask_px:
        quality_state = "CROSSED"
        viols.append(f"CROSSED_BBO:bid={bid_px}>ask={ask_px}")
    elif bid_px == ask_px:
        quality_state = "LOCKED"

    if tick_size is not None:
        tdec = _strict_dec(tick_size, "tick_size")
        if (bid_px % tdec) != Decimal("0") or (ask_px % tdec) != Decimal("0"):
            quality_state = "INVALID_TICK"
            viols.append("BBO_PRICE_NOT_ALIGNED_TO_TICK")

    if prev_event_ts_ms is not None and ev_ts_ms < prev_event_ts_ms:
        quality_state = "OUT_OF_ORDER"
        viols.append(f"BBO_EVENT_OUT_OF_ORDER:{ev_ts_ms}<{prev_event_ts_ms}")

    latency_ms = rx_ts_ms - ev_ts_ms
    if latency_ms > int(max_staleness_ms):
        quality_state = "STALE"
        viols.append(f"BBO_EVENT_STALE:age_ms={latency_ms}>{max_staleness_ms}")

    epoch = raw_event.get("epoch")
    epoch_healthy = raw_event.get("epoch_healthy") is True
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch <= 0:
        viols.append("INVALID_CONNECTION_EPOCH")
    if not epoch_healthy:
        quality_state = "MISSING_SNAPSHOT_OR_UNHEALTHY_EPOCH"
        viols.append("BBO_EVENT_BEFORE_HEALTHY_EPOCH_SNAPSHOT")

    spread = ask_px - bid_px
    mid = (ask_px + bid_px) / Decimal("2")
    norm = {
        "symbol": sym,
        "epoch": epoch,
        "epoch_healthy": epoch_healthy,
        "event_ts_ms": ev_ts_ms,
        "receive_utc_ms": rx_ts_ms,
        "latency_ms": latency_ms,
        "bid_price": _dec_str(bid_px),
        "bid_size": _dec_str(bid_sz),
        "ask_price": _dec_str(ask_px),
        "ask_size": _dec_str(ask_sz),
        "spread": _dec_str(spread),
        "mid_price": _dec_str(mid),
        "seq": raw_event.get("seq"),
        "quality_state": quality_state,
    }
    return {
        "valid_for_streaming_bbo": len(viols) == 0,
        "quality_state": quality_state,
        "violations": viols,
        "normalized": norm,
    }


def validate_r6_bbo_epoch(epoch_events: Sequence[Mapping[str, Any]], *, epoch_id: int) -> Dict[str, Any]:
    """Validates that a connection epoch starts with a valid snapshot before updates and tracks anomalies."""
    if not epoch_events:
        return {"valid_epoch": False, "epoch": epoch_id, "snapshot_present": False, "violations": ["EMPTY_EPOCH"]}
    first = epoch_events[0]
    snap_ok = str(first.get("action") or "").lower() == "snapshot" and first.get("epoch_healthy") is True
    viols: List[str] = []
    if not snap_ok:
        viols.append(f"EPOCH_MISSING_INITIAL_SNAPSHOT:epoch={epoch_id}")
    prev_ts: Optional[int] = None
    out_of_order = 0
    for ev in epoch_events:
        if ev.get("epoch") != epoch_id:
            viols.append("MIXED_EPOCH_ID_IN_SLICE")
        ts = ev.get("event_ts_ms")
        if isinstance(ts, int) and prev_ts is not None and ts < prev_ts:
            out_of_order += 1
        if isinstance(ts, int):
            prev_ts = ts
    if out_of_order > 0:
        viols.append(f"EPOCH_OUT_OF_ORDER_EVENTS:{out_of_order}")
    return {
        "valid_epoch": len(viols) == 0,
        "epoch": epoch_id,
        "snapshot_present": snap_ok,
        "event_count": len(epoch_events),
        "out_of_order_count": out_of_order,
        "violations": viols,
    }


# =====================================================================
# F21: BBO Coverage Report (Event vs Valid State-Known Separate!)
# =====================================================================
def build_r6_bbo_coverage_report(
    events: Sequence[Mapping[str, Any]],
    connection_intervals: Sequence[Mapping[str, Any]],
    *,
    calendar_expected_1s_slots_ms: Optional[Sequence[int]],
    max_state_freshness_ms: int = 10_000,
    target_state_known_ratio: float = 0.99,
) -> Dict[str, Any]:
    """
    Computes exact 1-second slot BBO coverage distinguishing:
      - directly_observed_1s_slots (`observed_event_coverage_ratio`)
      - valid_state_known_1s_slots (`state_known_coverage_ratio`) — inferred quote-state carried
        forward ONLY during a verified healthy connection epoch (`epoch_healthy=True`) AND within
        `max_state_freshness_ms`!
    NEVER inflates coverage via duplicates, NEVER carries across disconnects or missing snapshots,
    and NEVER treats ping/pong alone without fresh valid quote as valid coverage!
    Unknown calendar -> DENOMINATOR_UNKNOWN_BLOCKED (no readiness PASS!).
    """
    if calendar_expected_1s_slots_ms is None or len(calendar_expected_1s_slots_ms) == 0:
        return {
            "status": "DENOMINATOR_UNKNOWN_BLOCKED",
            "readiness_eligible": False,
            "expected_in_session_1s_slots": None,
            "observed_event_coverage_ratio": None,
            "state_known_coverage_ratio": None,
            "violations": ["UNKNOWN_CALENDAR_DENOMINATOR_NO_READINESS_PASS"],
        }

    # Index healthy connection intervals: list of (epoch, start_ms, end_ms)
    healthy_intervals: List[Tuple[int, int, int]] = []
    for ci in connection_intervals:
        if ci.get("epoch_healthy") is True and ci.get("snapshot_received") is True:
            healthy_intervals.append((int(ci["epoch"]), int(ci["start_ms"]), int(ci["end_ms"])))

    def _epoch_for_slot(slot_ms: int) -> Optional[int]:
        for ep, s_ms, e_ms in healthy_intervals:
            if s_ms <= slot_ms < e_ms:
                return ep
        return None

    # Deduplicate valid events by (epoch, event_ts_ms, bid_price, ask_price, bid_size, ask_size)
    raw_observations_count = len(events)
    valid_events_by_epoch: Dict[int, List[Dict[str, Any]]] = {}
    seen_event_sigs: set[Tuple[Any, ...]] = set()
    duplicate_events_count = 0
    invalid_or_stale_events_count = 0
    valid_unique_events_count = 0

    for ev in events:
        if ev.get("valid_for_streaming_bbo") is not True or ev.get("epoch_healthy") is not True:
            invalid_or_stale_events_count += 1
            continue
        sig = (
            ev.get("epoch"),
            ev.get("event_ts_ms"),
            ev.get("bid_price"),
            ev.get("ask_price"),
            ev.get("bid_size"),
            ev.get("ask_size"),
        )
        if sig in seen_event_sigs:
            duplicate_events_count += 1
            continue
        seen_event_sigs.add(sig)
        valid_unique_events_count += 1
        valid_events_by_epoch.setdefault(int(ev["epoch"]), []).append(dict(ev))

    for ep in valid_events_by_epoch:
        valid_events_by_epoch[ep].sort(key=lambda x: int(x["event_ts_ms"]))

    slots = sorted(set(int(s) for s in calendar_expected_1s_slots_ms))
    directly_observed_slots = 0
    valid_state_known_slots = 0
    no_event_valid_state_slots = 0
    disconnect_gap_slots = 0
    stale_or_no_quote_slots = 0

    for slot_ms in slots:
        ep = _epoch_for_slot(slot_ms)
        if ep is None:
            disconnect_gap_slots += 1
            continue
        ep_evs = valid_events_by_epoch.get(ep) or []
        # Events falling inside [slot_ms, slot_ms + 1000)
        direct_hit = any(slot_ms <= int(e["event_ts_ms"]) < slot_ms + 1000 for e in ep_evs)
        if direct_hit:
            directly_observed_slots += 1
            valid_state_known_slots += 1
            continue
        # Latest valid event strictly at or before slot_ms + 999 within SAME healthy epoch!
        prior_evs = [e for e in ep_evs if int(e["event_ts_ms"]) <= slot_ms + 999]
        if not prior_evs:
            # Healthy heartbeat / connection alive, but NO quote yet -> NOT observed or state-known!
            stale_or_no_quote_slots += 1
            continue
        latest = prior_evs[-1]
        age_ms = (slot_ms + 999) - int(latest["event_ts_ms"])
        if age_ms <= int(max_state_freshness_ms):
            valid_state_known_slots += 1
            no_event_valid_state_slots += 1
        else:
            stale_or_no_quote_slots += 1

    total_slots = len(slots)
    obs_ratio = round(directly_observed_slots / float(total_slots), 6)
    state_ratio = round(valid_state_known_slots / float(total_slots), 6)
    target_met = state_ratio >= float(target_state_known_ratio)

    return {
        "status": "BBO_COVERAGE_TARGET_MET" if target_met else "BBO_COVERAGE_BELOW_TARGET",
        "readiness_eligible": target_met,
        "expected_in_session_1s_slots": total_slots,
        "raw_observations_count": raw_observations_count,
        "valid_unique_events_count": valid_unique_events_count,
        "duplicate_events_excluded_from_coverage": duplicate_events_count,
        "invalid_or_stale_events_count": invalid_or_stale_events_count,
        "directly_observed_1s_slots": directly_observed_slots,
        "valid_state_known_1s_slots": valid_state_known_slots,
        "no_event_inferred_state_slots": no_event_valid_state_slots,
        "disconnect_gap_slots": disconnect_gap_slots,
        "stale_or_no_quote_slots": stale_or_no_quote_slots,
        "observed_event_coverage_ratio": obs_ratio,
        "state_known_coverage_ratio": state_ratio,
        "target_state_known_ratio": float(target_state_known_ratio),
        "max_state_freshness_ms": int(max_state_freshness_ms),
        "carry_forward_disclosure": "Carry-forward counts ONLY as inferred state-known within verified healthy epoch and freshness bound; NEVER as directly observed event.",
        "violations": [] if target_met else [f"STATE_KNOWN_COVERAGE_BELOW_TARGET:{state_ratio}<{target_state_known_ratio}"],
    }


# =====================================================================
# F22: Funding Settlement Completeness (No Hardcoded 8h, Never Zero-Fill!)
# =====================================================================
def validate_r6_funding_settlements(
    raw_rows: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    window_start_ms: int,
    window_end_ms: int,
    observed_fund_interval_hours: Optional[int],
    explicit_non_funding_instrument_evidence: bool = False,
    provenance_class: str = "QA_LOOPBACK",
) -> Dict[str, Any]:
    """
    Validates realized historical funding settlements against observed instrument `fund_interval_hours`.
    Never assumes fixed 8h, never zero-fills missing settlements, and distinguishes realized historical
    vs indicative/current funding rates.
    """
    violations: List[str] = []
    if symbol != APPROVED_SYMBOL_R6:
        return {"ok": False, "status": "REJECTED_SYMBOL_MISMATCH", "violations": [f"BAD_SYMBOL:{symbol}"]}

    if explicit_non_funding_instrument_evidence and not raw_rows:
        return {
            "ok": True,
            "status": "NOT_APPLICABLE_WITH_SOURCE_EVIDENCE",
            "realized_settlements": [],
            "missing_settlement_times_ms": [],
            "violations": [],
        }

    if observed_fund_interval_hours is None or not isinstance(observed_fund_interval_hours, int) or isinstance(observed_fund_interval_hours, bool) or observed_fund_interval_hours <= 0:
        return {
            "ok": False,
            "status": "BLOCKED_UNKNOWN_FUNDING_INTERVAL",
            "realized_settlements": [],
            "missing_settlement_times_ms": [],
            "violations": ["FUNDING_INTERVAL_NOT_OBSERVED_DO_NOT_ASSUME_8H"],
        }

    if not raw_rows:
        return {
            "ok": False,
            "status": "MISSING_OR_UNVERIFIED_EMPTY_FUNDING",
            "realized_settlements": [],
            "missing_settlement_times_ms": [],
            "violations": ["EMPTY_FUNDING_ARRAY_WITHOUT_NON_FUNDING_EVIDENCE"],
        }

    step_ms = int(observed_fund_interval_hours) * 3_600_000
    by_ts: Dict[int, Dict[str, Any]] = {}
    exact_dups = 0
    conflicts: List[Dict[str, Any]] = []
    indicative_excluded = 0
    invalid_rows: List[Dict[str, Any]] = []

    for idx, r in enumerate(raw_rows):
        if str(r.get("symbol") or symbol) != symbol:
            invalid_rows.append({"idx": idx, "reason": "SYMBOL_MISMATCH"})
            continue
        if str(r.get("rate_kind") or "REALIZED_HISTORICAL").upper() != "REALIZED_HISTORICAL":
            indicative_excluded += 1
            continue
        try:
            ft_ms = _strict_ms(r.get("fundingTime"), "fundingTime")
            rate = _strict_dec(r.get("fundingRate"), "fundingRate")
        except ValueError as exc:
            invalid_rows.append({"idx": idx, "reason": str(exc)})
            continue

        if ft_ms < window_start_ms or ft_ms >= window_end_ms:
            continue
        if ft_ms % step_ms != 0:
            invalid_rows.append({"idx": idx, "fundingTime": ft_ms, "reason": f"OFF_SCHEDULE_FOR_{observed_fund_interval_hours}H"})
            continue

        rate_s = _dec_str(rate)
        rec = {
            "symbol": symbol,
            "funding_time_ms": ft_ms,
            "funding_rate": rate_s,
            "rate_kind": "REALIZED_HISTORICAL",
            "fund_interval_hours": observed_fund_interval_hours,
            "provenance_class": provenance_class,
        }
        if ft_ms in by_ts:
            if by_ts[ft_ms]["funding_rate"] == rate_s:
                exact_dups += 1
            else:
                del by_ts[ft_ms]
                conflicts.append({"funding_time_ms": ft_ms, "reason": "CONFLICTING_FUNDING_RATE_QUARANTINED"})
                violations.append(f"CONFLICTING_FUNDING_SETTLEMENT:{ft_ms}")
        else:
            by_ts[ft_ms] = rec

    # Expected UTC settlement times aligned to observed_fund_interval_hours within [window_start_ms, window_end_ms)
    first_aligned = ((window_start_ms + step_ms - 1) // step_ms) * step_ms
    expected_times = list(range(first_aligned, window_end_ms, step_ms))
    missing_times = [t for t in expected_times if t not in by_ts]
    if missing_times:
        violations.append(f"MISSING_FUNDING_SETTLEMENTS_NEVER_ZERO_FILLED:{len(missing_times)}")
    if invalid_rows:
        violations.append(f"INVALID_FUNDING_ROWS:{len(invalid_rows)}")

    realized = [by_ts[k] for k in sorted(by_ts.keys())]
    ok = len(violations) == 0 and len(conflicts) == 0 and len(missing_times) == 0
    return {
        "ok": ok,
        "status": "FUNDING_COMPLETE_VERIFIED" if ok else "FUNDING_PARTIAL_OR_INVALID",
        "observed_fund_interval_hours": observed_fund_interval_hours,
        "expected_settlements_count": len(expected_times),
        "realized_settlements_count": len(realized),
        "missing_settlements_count": len(missing_times),
        "missing_settlement_times_ms": missing_times,
        "exact_duplicates_count": exact_dups,
        "conflicts_count": len(conflicts),
        "indicative_excluded_count": indicative_excluded,
        "invalid_rows_count": len(invalid_rows),
        "zero_filled_missing_count": 0,
        "realized_settlements": realized,
        "realized_sha256": _sha256_canonical(realized),
        "violations": violations,
    }


def acquire_r6_funding_history(
    *,
    symbol: str,
    window_start_ms: int,
    window_end_ms: int,
    page_fetcher: Callable[[int], Mapping[str, Any]],
    max_pages: int,
    observed_fund_interval_hours: Optional[int],
    provenance_class: str = "QA_LOOPBACK",
) -> Dict[str, Any]:
    """Bounded funding pagination with cursor/page replay guard feeding `validate_r6_funding_settlements`."""
    all_rows: List[Mapping[str, Any]] = []
    seen_page_hashes: set[str] = set()
    pages_fetched = 0
    cursor_viols: List[str] = []

    for page_no in range(1, int(max_pages) + 1):
        resp = page_fetcher(page_no)
        if not isinstance(resp, Mapping) or resp.get("ok") is not True:
            cursor_viols.append(f"FUNDING_PAGE_ERROR:{page_no}")
            break
        rows = resp.get("rows") or []
        pages_fetched += 1
        if not rows:
            break
        ph = _sha256_canonical(rows)
        if ph in seen_page_hashes:
            cursor_viols.append(f"FUNDING_CURSOR_REPLAY_PAGE:{page_no}")
            break
        seen_page_hashes.add(ph)
        all_rows.extend(rows)

    res = validate_r6_funding_settlements(
        all_rows,
        symbol=symbol,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        observed_fund_interval_hours=observed_fund_interval_hours,
        provenance_class=provenance_class,
    )
    res["pages_fetched"] = pages_fetched
    if cursor_viols:
        res["ok"] = False
        res["status"] = "FUNDING_PARTIAL_OR_CURSOR_ERROR"
        res["violations"] = list(res.get("violations") or ()) + cursor_viols
    return res


# =====================================================================
# F23: Owner Fee Honesty & Slippage Execution Fidelity Registry
# =====================================================================
def validate_r6_owner_fee_evidence(
    evidence: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    Validates voluntarily supplied redacted local owner fee-tier evidence.
    Rejects API keys/account IDs/secrets; public fee schedules are CONTEXTUAL_PUBLIC_ONLY and NEVER
    certify the owner's effective account fee rate!
    """
    if evidence is None:
        return {
            "verified_owner_fee": False,
            "status": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "maker_fee_rate": None,
            "taker_fee_rate": None,
            "violations": [],
            "disclosure": "No local redacted owner fee confirmation supplied; generic public rates cannot assert owner effective fee.",
        }

    sec_hits = _scan_for_secrets_or_bypass(evidence)
    if sec_hits or "account_id" in evidence or "uid" in evidence or "user_id" in evidence:
        return {
            "verified_owner_fee": False,
            "status": "REJECTED_SENSITIVE_ACCOUNT_OR_SECRET_FIELD",
            "maker_fee_rate": None,
            "taker_fee_rate": None,
            "violations": sec_hits + (["UNREDACTED_ACCOUNT_IDENTIFIER_FORBIDDEN"] if any(k in evidence for k in ("account_id", "uid", "user_id")) else []),
        }

    src = str(evidence.get("evidence_source") or "").strip().upper()
    if src in ("PUBLIC_DOCS", "PUBLIC_FEE_SCHEDULE", "ASSUMED_DEFAULT", "VIP0_PUBLIC"):
        return {
            "verified_owner_fee": False,
            "status": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "contextual_public_only": True,
            "maker_fee_rate": None,
            "taker_fee_rate": None,
            "violations": ["PUBLIC_FEE_SCHEDULE_CANNOT_ASSERT_OWNER_EFFECTIVE_FEE"],
        }

    if src != "OWNER_REDACTED_LOCAL_CONFIRMATION":
        return {
            "verified_owner_fee": False,
            "status": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "maker_fee_rate": None,
            "taker_fee_rate": None,
            "violations": [f"UNVERIFIED_FEE_EVIDENCE_SOURCE:{src}"],
        }

    try:
        maker = _strict_dec(evidence.get("maker_fee_rate"), "maker_fee_rate")
        taker = _strict_dec(evidence.get("taker_fee_rate"), "taker_fee_rate")
    except ValueError as exc:
        return {"verified_owner_fee": False, "status": "REJECTED_INVALID_FEE_RATE", "maker_fee_rate": None, "taker_fee_rate": None, "violations": [str(exc)]}

    if taker < Decimal("0") or maker < Decimal("-0.005") or taker > Decimal("0.01"):
        return {"verified_owner_fee": False, "status": "REJECTED_OUT_OF_RANGE_FEE", "maker_fee_rate": None, "taker_fee_rate": None, "violations": ["FEE_RATE_OUT_OF_SANE_RANGE"]}

    if str(evidence.get("symbol") or "") != APPROVED_SYMBOL_R6 or not str(evidence.get("observed_utc") or "").strip():
        return {"verified_owner_fee": False, "status": "REJECTED_INCOMPLETE_FEE_EVIDENCE", "maker_fee_rate": None, "taker_fee_rate": None, "violations": ["MISSING_SYMBOL_OR_OBSERVED_UTC"]}

    return {
        "verified_owner_fee": True,
        "status": "LOCALLY_VERIFIED_OWNER_REDACTED",
        "symbol": APPROVED_SYMBOL_R6,
        "maker_fee_rate": _dec_str(maker),
        "taker_fee_rate": _dec_str(taker),
        "observed_utc": str(evidence["observed_utc"]),
        "verification_method": str(evidence.get("verification_method") or "owner_redacted_export"),
        "violations": [],
    }


def build_r6_slippage_registry(
    *,
    bbo_coverage_available: bool = False,
    redacted_preexisting_fill_evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Public BBO/quotes NEVER certify owner execution slippage or fill mix.
    Orders (including paper orders) are FORBIDDEN in R6.
    """
    if redacted_preexisting_fill_evidence is None:
        return {
            "slippage_status": "MISSING_NO_OWNER_FILL_EVIDENCE",
            "execution_fidelity_status": "INCOMPLETE_NO_OWNER_EXECUTION_FILLS",
            "public_bbo_proves_execution_slippage": False,
            "bbo_quote_context_available": bool(bbo_coverage_available),
            "orders_placed_count": 0,
            "paper_orders_placed_count": 0,
            "violations": [],
        }

    sec_hits = _scan_for_secrets_or_bypass(redacted_preexisting_fill_evidence)
    if sec_hits or redacted_preexisting_fill_evidence.get("source") in ("PUBLIC_BBO", "SYNTHETIC_FILL", "PAPER_ORDER"):
        return {
            "slippage_status": "REJECTED_INVALID_OR_FORBIDDEN_FILL_SOURCE",
            "execution_fidelity_status": "BLOCKED_INVALID_FILL_SOURCE",
            "public_bbo_proves_execution_slippage": False,
            "orders_placed_count": 0,
            "paper_orders_placed_count": 0,
            "violations": sec_hits + ["FORBIDDEN_NON_OWNER_OR_SYNTHETIC_FILL_EVIDENCE"],
        }

    return {
        "slippage_status": "PREEXISTING_REDACTED_OWNER_FILLS_REGISTERED",
        "execution_fidelity_status": "PARTIAL_OWNER_FILL_EVIDENCE_REVIEW_REQUIRED",
        "public_bbo_proves_execution_slippage": False,
        "orders_placed_count": 0,
        "paper_orders_placed_count": 0,
        "violations": [],
    }


# =====================================================================
# F24: Deterministic Gap & Anomaly Ledger
# =====================================================================
def build_r6_market_data_gap_ledger(
    *,
    dataset_scope: str,  # "DEV_ONLY" | "QA_FIXTURE"
    ohlcv_result: Optional[Mapping[str, Any]] = None,
    bbo_result: Optional[Mapping[str, Any]] = None,
    funding_result: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Collects every gap, invalid row, conflict, disconnect, or missing settlement deterministically."""
    entries: List[Dict[str, Any]] = []

    if ohlcv_result:
        for m in ohlcv_result.get("missing_intervals") or ():
            entries.append({"domain": "OHLCV", "kind": "MISSING_INTERVAL", "start_ms": m["open_time_ms"], "end_ms": m["close_time_ms"], "reason": m["reason"], "recoverable": True})
        for q in ohlcv_result.get("quarantined_conflicts") or ():
            entries.append({"domain": "OHLCV", "kind": "CONFLICT_QUARANTINED", "start_ms": q["open_time_ms"], "end_ms": q["open_time_ms"], "reason": q["reason"], "recoverable": False})
        for inv in ohlcv_result.get("invalid_rows") or ():
            entries.append({"domain": "OHLCV", "kind": "INVALID_ROW", "start_ms": inv.get("open_time_ms", 0), "end_ms": inv.get("open_time_ms", 0), "reason": inv["reason"], "recoverable": False})
        for unc in ohlcv_result.get("unclosed_or_future_excluded") or ():
            entries.append({"domain": "OHLCV", "kind": "UNCLOSED_EXCLUDED", "start_ms": unc["open_time_ms"], "end_ms": unc["close_time_ms"], "reason": unc["reason"], "recoverable": True})

    if bbo_result:
        disc = int(bbo_result.get("disconnect_gap_slots") or 0)
        if disc > 0:
            entries.append({"domain": "BBO", "kind": "DISCONNECT_GAP_SLOTS", "start_ms": 0, "end_ms": 0, "count": disc, "reason": "WS_DISCONNECT_OR_UNHEALTHY_EPOCH", "recoverable": False})
        stale = int(bbo_result.get("stale_or_no_quote_slots") or 0)
        if stale > 0:
            entries.append({"domain": "BBO", "kind": "STALE_OR_NO_QUOTE_SLOTS", "start_ms": 0, "end_ms": 0, "count": stale, "reason": "NO_FRESH_QUOTE_IN_HEALTHY_EPOCH", "recoverable": False})

    if funding_result:
        for mt in funding_result.get("missing_settlement_times_ms") or ():
            entries.append({"domain": "FUNDING", "kind": "MISSING_SETTLEMENT", "start_ms": int(mt), "end_ms": int(mt), "reason": "MISSING_FUNDING_SETTLEMENT_NOT_ZERO_FILLED", "recoverable": True})

    entries.sort(key=lambda e: (e["domain"], e["kind"], int(e.get("start_ms", 0)), str(e.get("reason", ""))))
    return {
        "spec_version": SPEC_VERSION_KH03_R6,
        "schema_version": SCHEMA_VERSION_KH03_R6,
        "dataset_scope": dataset_scope,
        "entries_count": len(entries),
        "entries": entries,
        "gap_ledger_sha256": _sha256_canonical(entries),
    }
