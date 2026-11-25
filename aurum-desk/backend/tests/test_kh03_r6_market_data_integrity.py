"""
aurum-desk/backend/tests/test_kh03_r6_market_data_integrity.py

KH-03.R6 Paired Controls D01..D11 (OHLCV Pagination/Dedup/Quarantine/Unclosed/Calendar,
BBO Crossed/Tick/OOO/Disconnect/Duplicates/Stale/REST-Not-Streaming, Funding Completeness &
Owner Fee/Slippage Honesty).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.kh_03_r6_acceptance_service import (
    R6FaultCaseSpec,
    derive_r6_fault_verdict,
    execute_r6_fault_pair,
)
from services.kh_03_r6_market_data_service import (
    acquire_r6_funding_history,
    build_r6_bbo_coverage_report,
    build_r6_market_data_gap_ledger,
    build_r6_slippage_registry,
    normalize_r6_bbo_event,
    paginate_r6_ohlcv_history,
    validate_r6_bbo_epoch,
    validate_r6_funding_settlements,
    validate_r6_owner_fee_evidence,
)


def run_kh03_r6_controls_d01_to_d11(*, executable_sha: str, run_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    receipts: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}
    base = 1794009600000  # aligned UTC hour/8h boundary
    win_start = base
    win_end = base + 4 * 60_000
    server_ms = win_end + 60_000
    exp_ts = [base + i * 60_000 for i in range(4)]

    valid_bars = [
        [str(base + 0 * 60_000), "2650.10", "2651.00", "2649.50", "2650.80", "12.5"],
        [str(base + 1 * 60_000), "2650.80", "2652.00", "2650.00", "2651.20", "10.0"],
        [str(base + 2 * 60_000), "2651.20", "2652.50", "2651.00", "2652.10", "8.0"],
        [str(base + 3 * 60_000), "2652.10", "2653.00", "2651.50", "2652.90", "9.5"],
    ]

    # D01: OHLC geometry/nonfinite/wrong timestamp rejected vs valid closed bar
    bad_bars_d01 = [
        [str(base + 123), "2650.10", "2649.00", "2651.00", "2650.50", "-5"],  # unaligned + high<low + neg vol
    ]
    r_d01_neg = paginate_r6_ohlcv_history(symbol="XAUUSDT", granularity="1m", window_start_ms=win_start, window_end_ms=win_end, server_time_ms=server_ms, page_fetcher=lambda _c, _p: {"ok": True, "rows": bad_bars_d01}, max_pages=2, calendar_expected_open_times_ms=exp_ts, tick_size="0.01")
    r_d01_pos = paginate_r6_ohlcv_history(symbol="XAUUSDT", granularity="1m", window_start_ms=win_start, window_end_ms=win_end, server_time_ms=server_ms, page_fetcher=lambda _c, _p: {"ok": True, "rows": valid_bars}, max_pages=2, calendar_expected_open_times_ms=exp_ts, tick_size="0.01")
    summary["valid_closed_bars_accepted"] = (r_d01_pos["ok"] is True and r_d01_pos["canonical_valid_count"] == 4)
    summary["invalid_geometry_rejected"] = (r_d01_neg["ok"] is False and r_d01_neg["invalid_rows_count"] == 1)
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D01", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.paginate_r6_ohlcv_history", "data_anomaly_caught", "data_clean_valid", "Invalid OHLC geometry/unaligned ts rejected vs valid closed M1 bars"),
        negative_fn=lambda: ({"invalid_rows": r_d01_neg["invalid_rows_count"]}, {"anomaly_caught": r_d01_neg["invalid_rows_count"] >= 1 and r_d01_neg["canonical_valid_count"] == 0, "fabricated_or_inflated": False}),
        positive_fn=lambda: ({"valid_bars": r_d01_pos["canonical_valid_count"]}, {"clean_valid": r_d01_pos["ok"], "violations_count": len(r_d01_pos["violations"])}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    # D02: pagination cursor repetition / no progress stopped vs clean multi-page pagination
    def _stuck_fetcher(cur_end: Optional[int], pno: int) -> Dict[str, Any]:
        # Always returns only the last bar at cur_end -> no backward progress!
        return {"ok": True, "rows": [[str(win_end), "2650.00", "2651.00", "2649.00", "2650.50", "1.0"]]}

    def _clean_2page_fetcher(cur_end: Optional[int], pno: int) -> Dict[str, Any]:
        return {"ok": True, "rows": valid_bars[2:] if pno == 1 else valid_bars[:2]}

    r_d02_neg = paginate_r6_ohlcv_history(symbol="XAUUSDT", granularity="1m", window_start_ms=win_start, window_end_ms=win_end, server_time_ms=server_ms, page_fetcher=_stuck_fetcher, max_pages=5, calendar_expected_open_times_ms=exp_ts)
    r_d02_pos = paginate_r6_ohlcv_history(symbol="XAUUSDT", granularity="1m", window_start_ms=win_start, window_end_ms=win_end, server_time_ms=server_ms, page_fetcher=_clean_2page_fetcher, max_pages=5, calendar_expected_open_times_ms=exp_ts)
    summary["cursor_replay_stopped"] = ("NON_PROGRESSING" in r_d02_neg["stop_reason"] or "CURSOR" in r_d02_neg["stop_reason"])
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D02", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.paginate_r6_ohlcv_history", "data_anomaly_caught", "data_clean_valid", "Non-progressing pagination cursor stopped vs clean 2-page fetch"),
        negative_fn=lambda: ({"stop_reason": r_d02_neg["stop_reason"]}, {"anomaly_caught": not r_d02_neg["ok"] and len(r_d02_neg["violations"]) >= 1, "fabricated_or_inflated": False}),
        positive_fn=lambda: ({"pages": r_d02_pos["pages_fetched_count"]}, {"clean_valid": r_d02_pos["ok"] and r_d02_pos["pages_fetched_count"] == 2, "violations_count": len(r_d02_pos["violations"])}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    # D03: overlapping pages exact duplicate deduped vs contradictory duplicate quarantined (NEVER last-wins!)
    conflict_bar = [str(base + 2 * 60_000), "2651.20", "2699.99", "2651.00", "2652.10", "8.0"]  # conflicting high!
    r_d03_neg = paginate_r6_ohlcv_history(symbol="XAUUSDT", granularity="1m", window_start_ms=win_start, window_end_ms=win_end, server_time_ms=server_ms, page_fetcher=lambda _c, pno: {"ok": True, "rows": valid_bars[2:] if pno == 1 else valid_bars[:2] + [conflict_bar]}, max_pages=3, calendar_expected_open_times_ms=exp_ts)
    r_d03_pos = paginate_r6_ohlcv_history(symbol="XAUUSDT", granularity="1m", window_start_ms=win_start, window_end_ms=win_end, server_time_ms=server_ms, page_fetcher=lambda _c, pno: {"ok": True, "rows": valid_bars[2:] if pno == 1 else valid_bars[:3]}, max_pages=3, calendar_expected_open_times_ms=exp_ts)
    summary["contradictory_duplicate_quarantined"] = (r_d03_neg["quarantined_conflicts_count"] == 1 and r_d03_neg["canonical_valid_count"] == 3 and r_d03_pos["exact_duplicates_deduped_count"] == 1 and r_d03_pos["ok"] is True)
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D03", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.paginate_r6_ohlcv_history", "data_anomaly_caught", "data_clean_valid", "Contradictory overlap duplicate quarantined (no last-wins) vs exact overlap deduped"),
        negative_fn=lambda: ({"conflicts": r_d03_neg["quarantined_conflicts_count"]}, {"anomaly_caught": r_d03_neg["quarantined_conflicts_count"] == 1 and not r_d03_neg["ok"], "fabricated_or_inflated": False}),
        positive_fn=lambda: ({"exact_dups": r_d03_pos["exact_duplicates_deduped_count"]}, {"clean_valid": r_d03_pos["ok"] and r_d03_pos["exact_duplicates_deduped_count"] == 1, "violations_count": len(r_d03_pos["violations"])}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    # D04: unclosed/future candle excluded from canonical DEV while raw retained
    early_server = base + 3 * 60_000 + 10_000  # 4th bar (index 3) is still open!
    r_d04_neg = paginate_r6_ohlcv_history(symbol="XAUUSDT", granularity="1m", window_start_ms=win_start, window_end_ms=win_end, server_time_ms=early_server, page_fetcher=lambda _c, _p: {"ok": True, "rows": valid_bars}, max_pages=2, calendar_expected_open_times_ms=exp_ts[:3])
    summary["unclosed_future_excluded_from_canonical"] = (r_d04_neg["unclosed_or_future_excluded_count"] == 1 and r_d04_neg["canonical_valid_count"] == 3)
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D04", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.paginate_r6_ohlcv_history", "data_anomaly_caught", "data_clean_valid", "Unclosed/future bar excluded from canonical DEV while raw preserved vs all closed"),
        negative_fn=lambda: ({"unclosed_excluded": r_d04_neg["unclosed_or_future_excluded_count"]}, {"anomaly_caught": r_d04_neg["unclosed_or_future_excluded_count"] == 1 and r_d04_neg["canonical_valid_count"] == 3, "fabricated_or_inflated": False}),
        positive_fn=lambda: ({"all_closed": r_d01_pos["canonical_valid_count"]}, {"clean_valid": r_d01_pos["ok"] and r_d01_pos["unclosed_or_future_excluded_count"] == 0, "violations_count": 0}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    # D05: unknown session/DST calendar denominator -> INCOMPLETE (no readiness PASS!) vs verified calendar
    r_d05_neg = paginate_r6_ohlcv_history(symbol="XAUUSDT", granularity="1m", window_start_ms=win_start, window_end_ms=win_end, server_time_ms=server_ms, page_fetcher=lambda _c, _p: {"ok": True, "rows": valid_bars}, max_pages=2, calendar_expected_open_times_ms=None)
    summary["unknown_calendar_marked_incomplete"] = (r_d05_neg["ok"] is False and r_d05_neg["calendar_status"] == "CALENDAR_DENOMINATOR_UNKNOWN_INCOMPLETE" and r_d05_neg["coverage_ratio"] is None)
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D05", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.paginate_r6_ohlcv_history", "data_anomaly_caught", "data_clean_valid", "Unknown calendar denominator blocks readiness vs verified calendar"),
        negative_fn=lambda: ({"calendar_status": r_d05_neg["calendar_status"]}, {"anomaly_caught": (not r_d05_neg["ok"]) and r_d05_neg["coverage_ratio"] is None, "fabricated_or_inflated": False}),
        positive_fn=lambda: ({"calendar_status": r_d01_pos["calendar_status"]}, {"clean_valid": r_d01_pos["ok"] and r_d01_pos["coverage_ratio"] == 1.0, "violations_count": 0}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    # D06: BBO crossed / invalid tick / out-of-order / REST snapshot rejected vs valid WS books1 event
    b_cross = normalize_r6_bbo_event({"symbol": "XAUUSDT", "bid_price": "2651.00", "bid_size": "2", "ask_price": "2650.50", "ask_size": "2", "event_ts_ms": base + 2000, "receive_utc_ms": base + 2010, "epoch": 1, "epoch_healthy": True}, tick_size="0.01")
    b_tick = normalize_r6_bbo_event({"symbol": "XAUUSDT", "bid_price": "2650.105", "bid_size": "2", "ask_price": "2650.20", "ask_size": "2", "event_ts_ms": base + 2000, "receive_utc_ms": base + 2010, "epoch": 1, "epoch_healthy": True}, tick_size="0.01")
    b_ooo = normalize_r6_bbo_event({"symbol": "XAUUSDT", "bid_price": "2650.10", "bid_size": "2", "ask_price": "2650.20", "ask_size": "2", "event_ts_ms": base + 1000, "receive_utc_ms": base + 2010, "epoch": 1, "epoch_healthy": True}, tick_size="0.01", prev_event_ts_ms=base + 2000)
    b_rest = normalize_r6_bbo_event({"symbol": "XAUUSDT", "bid_price": "2650.10", "bid_size": "2", "ask_price": "2650.20", "ask_size": "2", "event_ts_ms": base + 2000, "receive_utc_ms": base + 2010, "epoch": 1, "epoch_healthy": True}, source_transport="REST_TICKER_SNAPSHOT")
    b_ok = normalize_r6_bbo_event({"symbol": "XAUUSDT", "bid_price": "2650.10", "bid_size": "2", "ask_price": "2650.20", "ask_size": "2", "event_ts_ms": base + 2000, "receive_utc_ms": base + 2020, "epoch": 1, "epoch_healthy": True}, tick_size="0.01")
    summary["crossed_tick_ooo_rejected"] = (not b_cross["valid_for_streaming_bbo"] and not b_tick["valid_for_streaming_bbo"] and not b_ooo["valid_for_streaming_bbo"])
    summary["rest_snapshot_rejected_as_streaming_bbo"] = (b_rest["quality_state"] == "REST_SNAPSHOT_NOT_STREAMING" and not b_rest["valid_for_streaming_bbo"])
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D06", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.normalize_r6_bbo_event", "data_anomaly_caught", "data_clean_valid", "Crossed/bad-tick/OOO/REST-snapshot BBO rejected vs valid WS books1 event"),
        negative_fn=lambda: ({"rejected_4": True}, {"anomaly_caught": summary["crossed_tick_ooo_rejected"] and summary["rest_snapshot_rejected_as_streaming_bbo"], "fabricated_or_inflated": False}),
        positive_fn=lambda: ({"valid_bbo": True}, {"clean_valid": b_ok["valid_for_streaming_bbo"], "violations_count": len(b_ok["violations"])}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    # D07: disconnect/reconnect missing snapshot does NOT carry forward over gap vs valid epoch
    slots_10 = [base + i * 1000 for i in range(10)]
    ev_ep1 = {"valid_for_streaming_bbo": True, "epoch": 1, "epoch_healthy": True, "action": "snapshot", "event_ts_ms": base, "bid_price": "2650.10", "ask_price": "2650.20", "bid_size": "2", "ask_size": "2"}
    # Gap from [base+3000, base+7000); epoch 2 reconnects WITHOUT snapshot -> NOT healthy!
    cov_gap = build_r6_bbo_coverage_report(
        [ev_ep1],
        [
            {"epoch": 1, "start_ms": base, "end_ms": base + 3000, "epoch_healthy": True, "snapshot_received": True},
            {"epoch": 2, "start_ms": base + 7000, "end_ms": base + 10000, "epoch_healthy": False, "snapshot_received": False},
        ],
        calendar_expected_1s_slots_ms=slots_10,
        max_state_freshness_ms=10_000,
    )
    ep_bad = validate_r6_bbo_epoch([{"epoch": 2, "action": "update", "epoch_healthy": False, "event_ts_ms": base + 7500}], epoch_id=2)
    cov_full = build_r6_bbo_coverage_report(
        [ev_ep1],
        [{"epoch": 1, "start_ms": base, "end_ms": base + 10000, "epoch_healthy": True, "snapshot_received": True}],
        calendar_expected_1s_slots_ms=slots_10,
        max_state_freshness_ms=15_000,
    )
    summary["no_carry_across_disconnect_or_missing_snapshot"] = (cov_gap["valid_state_known_1s_slots"] == 3 and cov_gap["disconnect_gap_slots"] == 7 and not ep_bad["valid_epoch"])
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D07", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.build_r6_bbo_coverage_report", "data_anomaly_caught", "data_clean_valid", "Disconnect/missing-snapshot blocks carry-forward vs healthy epoch state coverage"),
        negative_fn=lambda: ({"disconnect_gaps": cov_gap["disconnect_gap_slots"]}, {"anomaly_caught": summary["no_carry_across_disconnect_or_missing_snapshot"], "fabricated_or_inflated": cov_gap["valid_state_known_1s_slots"] > 3}),
        positive_fn=lambda: ({"state_cov": cov_full["state_known_coverage_ratio"]}, {"clean_valid": cov_full["readiness_eligible"] and cov_full["state_known_coverage_ratio"] == 1.0, "violations_count": len(cov_full["violations"])}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    # D08: duplicates do NOT inflate 1s slot coverage; observed-event vs valid state-known separate!
    duped_evs = [ev_ep1] * 50  # 50 identical duplicates at slot 0 ONLY!
    cov_dup = build_r6_bbo_coverage_report(
        duped_evs,
        [{"epoch": 1, "start_ms": base, "end_ms": base + 10000, "epoch_healthy": True, "snapshot_received": True}],
        calendar_expected_1s_slots_ms=slots_10,
        max_state_freshness_ms=15_000,
    )
    summary["duplicates_do_not_inflate_coverage"] = (cov_dup["duplicate_events_excluded_from_coverage"] == 49 and cov_dup["directly_observed_1s_slots"] == 1 and cov_dup["valid_state_known_1s_slots"] == 10)
    summary["observed_vs_state_known_reported_separately"] = (cov_dup["observed_event_coverage_ratio"] == 0.1 and cov_dup["state_known_coverage_ratio"] == 1.0)
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D08", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.build_r6_bbo_coverage_report", "data_anomaly_caught", "data_clean_valid", "50 duplicate events at slot 0 do not inflate observed slots (0.1 vs 1.0 state)"),
        negative_fn=lambda: ({"dups_excluded": cov_dup["duplicate_events_excluded_from_coverage"]}, {"anomaly_caught": cov_dup["duplicate_events_excluded_from_coverage"] == 49 and cov_dup["directly_observed_1s_slots"] == 1, "fabricated_or_inflated": cov_dup["directly_observed_1s_slots"] > 1}),
        positive_fn=lambda: ({"obs_vs_state": [cov_dup["observed_event_coverage_ratio"], cov_dup["state_known_coverage_ratio"]]}, {"clean_valid": summary["observed_vs_state_known_reported_separately"], "violations_count": 0}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    # D09: missing/stale quote despite healthy heartbeat -> NOT observed or state-known!
    cov_stale = build_r6_bbo_coverage_report(
        [ev_ep1],  # only 1 event at t=0; freshness bound=2000ms -> slots 3..9 stale despite healthy WS!
        [{"epoch": 1, "start_ms": base, "end_ms": base + 10000, "epoch_healthy": True, "snapshot_received": True}],
        calendar_expected_1s_slots_ms=slots_10,
        max_state_freshness_ms=2_000,
    )
    summary["stale_quote_not_observed_despite_heartbeat"] = (cov_stale["stale_or_no_quote_slots"] >= 7 and not cov_stale["readiness_eligible"])
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D09", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.build_r6_bbo_coverage_report", "data_anomaly_caught", "data_clean_valid", "Stale quote past freshness cap not counted as covered despite healthy WS heartbeat"),
        negative_fn=lambda: ({"stale_slots": cov_stale["stale_or_no_quote_slots"]}, {"anomaly_caught": summary["stale_quote_not_observed_despite_heartbeat"], "fabricated_or_inflated": cov_stale["readiness_eligible"]}),
        positive_fn=lambda: ({"fresh_bound_ok": cov_full["readiness_eligible"]}, {"clean_valid": cov_full["readiness_eligible"], "violations_count": 0}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    # D10: funding missing / conflict / unknown interval blocked (NEVER zero-fill or assume 8h!) vs complete
    step8 = 8 * 3_600_000
    f_start = (base // step8) * step8
    f_end = f_start + 3 * step8
    f_miss = validate_r6_funding_settlements([{"symbol": "XAUUSDT", "fundingTime": f_start, "fundingRate": "0.0001"}], symbol="XAUUSDT", window_start_ms=f_start, window_end_ms=f_end, observed_fund_interval_hours=8)
    f_unk_int = validate_r6_funding_settlements([{"symbol": "XAUUSDT", "fundingTime": f_start, "fundingRate": "0.0001"}], symbol="XAUUSDT", window_start_ms=f_start, window_end_ms=f_end, observed_fund_interval_hours=None)
    f_conf = validate_r6_funding_settlements([{"symbol": "XAUUSDT", "fundingTime": f_start, "fundingRate": "0.0001"}, {"symbol": "XAUUSDT", "fundingTime": f_start, "fundingRate": "0.0009"}], symbol="XAUUSDT", window_start_ms=f_start, window_end_ms=f_start + step8, observed_fund_interval_hours=8)
    f_ok = acquire_r6_funding_history(
        symbol="XAUUSDT", window_start_ms=f_start, window_end_ms=f_end,
        page_fetcher=lambda pno: {"ok": True, "rows": [{"symbol": "XAUUSDT", "fundingTime": f_start + i * step8, "fundingRate": "0.0001"} for i in range(3)] if pno == 1 else []},
        max_pages=3, observed_fund_interval_hours=8,
    )
    summary["documented_settlements_validated"] = (f_ok["ok"] is True and f_ok["realized_settlements_count"] == 3)
    summary["missing_settlements_not_zero_filled"] = (f_miss["ok"] is False and f_miss["missing_settlements_count"] == 2 and f_miss["zero_filled_missing_count"] == 0)
    summary["unknown_interval_blocked_no_8h_default"] = (f_unk_int["status"] == "BLOCKED_UNKNOWN_FUNDING_INTERVAL")
    summary["conflicting_settlement_quarantined"] = (f_conf["conflicts_count"] == 1 and not f_conf["ok"])
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D10", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.validate_r6_funding_settlements", "data_anomaly_caught", "data_clean_valid", "Missing/conflicting/unknown-interval funding never zero-filled vs complete schedule"),
        negative_fn=lambda: ({"missing_zero_filled": f_miss["zero_filled_missing_count"]}, {"anomaly_caught": summary["missing_settlements_not_zero_filled"] and summary["unknown_interval_blocked_no_8h_default"] and summary["conflicting_settlement_quarantined"], "fabricated_or_inflated": f_miss["zero_filled_missing_count"] != 0}),
        positive_fn=lambda: ({"realized": f_ok["realized_settlements_count"]}, {"clean_valid": f_ok["ok"], "violations_count": len(f_ok["violations"])}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    # D11: public fee cannot assert owner effective fee; public BBO cannot assert slippage; redacted owner proof valid
    fee_pub = validate_r6_owner_fee_evidence({"evidence_source": "PUBLIC_FEE_SCHEDULE", "symbol": "XAUUSDT", "maker_fee_rate": "0.0002", "taker_fee_rate": "0.0006", "observed_utc": "2026-11-25T10:00:00Z"})
    fee_none = validate_r6_owner_fee_evidence(None)
    fee_ok = validate_r6_owner_fee_evidence({"evidence_source": "OWNER_REDACTED_LOCAL_CONFIRMATION", "symbol": "XAUUSDT", "maker_fee_rate": "0.00018", "taker_fee_rate": "0.00045", "observed_utc": "2026-11-25T10:00:00Z"})
    slip_fake = build_r6_slippage_registry(bbo_coverage_available=True, redacted_preexisting_fill_evidence={"source": "PUBLIC_BBO"})
    slip_honest = build_r6_slippage_registry(bbo_coverage_available=True, redacted_preexisting_fill_evidence=None)
    summary["owner_fee_status"] = fee_none["status"]
    summary["public_fee_rejected_as_owner_effective"] = (fee_pub["verified_owner_fee"] is False and fee_pub["status"] == "UNKNOWN_NOT_LOCALLY_VERIFIED")
    summary["slippage_status"] = slip_honest["slippage_status"]
    summary["public_bbo_proves_execution_slippage"] = slip_honest["public_bbo_proves_execution_slippage"]
    summary["orders_placed_count"] = slip_honest["orders_placed_count"]
    receipts.append(execute_r6_fault_pair(
        R6FaultCaseSpec("R6-D11", "D_DATA_CUSTODY", "NEGATIVE_CONTRACT", "services.kh_03_r6_market_data_service.validate_r6_owner_fee_evidence", "data_anomaly_caught", "data_clean_valid", "Public fee schedule & BBO-as-slippage rejected vs honest unknown/redacted owner proof"),
        negative_fn=lambda: ({"pub_fee_and_bbo_slip": True}, {"anomaly_caught": (not fee_pub["verified_owner_fee"]) and ("REJECTED" in slip_fake["slippage_status"]), "fabricated_or_inflated": fee_pub["verified_owner_fee"]}),
        positive_fn=lambda: ({"redacted_owner_fee_ok": fee_ok["verified_owner_fee"], "honest_missing_slip": slip_honest["slippage_status"]}, {"clean_valid": fee_ok["verified_owner_fee"] and slip_honest["orders_placed_count"] == 0, "violations_count": 0}),
        executable_sha=executable_sha, run_id=run_id,
    ))

    gap_ledger = build_r6_market_data_gap_ledger(dataset_scope="QA_FIXTURE", ohlcv_result=r_d04_neg, bbo_result=cov_gap, funding_result=f_miss)
    summary["gap_ledger_entries_count"] = gap_ledger["entries_count"]
    summary["gap_ledger_sha256"] = gap_ledger["gap_ledger_sha256"]
    return receipts, summary


def test_kh03_r6_paired_controls_d01_to_d11() -> None:
    recs, summary = run_kh03_r6_controls_d01_to_d11(executable_sha="4" * 40, run_id="test_r6_d11")
    assert len(recs) == 11
    verd = derive_r6_fault_verdict(recs, expected_executable_sha="4" * 40, expected_run_id="test_r6_d11")
    assert verd["valid"] is True, verd["violations"]
    assert verd["passed_controls"] == 11
    assert summary["contradictory_duplicate_quarantined"] is True
    assert summary["duplicates_do_not_inflate_coverage"] is True
    assert summary["missing_settlements_not_zero_filled"] is True
    assert summary["public_fee_rejected_as_owner_effective"] is True
