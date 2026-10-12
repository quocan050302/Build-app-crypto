"""
V14.4.1 three-session conditional forecasts: causal as-of, partial Europe at NY 08:30, DST, gaps, UNKNOWN is valid.
"""
import copy
from datetime import datetime, timezone

import pytest

from services.smc_scenario_config import active_scenario_config as CFG
from services.smc_scenario_service import load_closed_context
from services.smc_session_context import NY_TZ, build_session_range, get_session_window_bounds
from services.smc_session_features import compute_asia_expansion, compute_clv, compute_europe_position
from services.smc_session_forecast_service import evaluate_session_forecasts, rank_forecasts

import smc_fixtures as F


def forecasts_at(now_ms, direction="LONG", candles=None):
    cbt = candles or F.make_candles_by_tf(direction, now_ms)
    ctx = load_closed_context(cbt, now_ms, CFG)
    return evaluate_session_forecasts("XAUUSDT", ctx, now_ms, CFG)


def test_asia_to_london_to_ny():
    fc = forecasts_at(F.NOW_MS)
    assert set(fc) == {"ASIA", "LONDON", "NY"}
    assert fc["ASIA"].data_status == "OK" and fc["ASIA"].is_partial is False       # Asia ended 02:00 NY
    assert fc["LONDON"].is_partial is True and fc["NY"].is_partial is True
    for f in fc.values():
        assert f.entry_eligible is False                                            # forecast never enables entry
        assert f.probability["status"] == "UNAVAILABLE" and f.probability["p_session_event"] is None
        assert "nếu" in f.summary_vi.lower() or f.hypothesis_type == "UNKNOWN"      # conditional wording
        assert "market maker" not in f.summary_vi.lower()
    assert fc["ASIA"].primary["direction"] is None                                   # null direction = observation
    assert fc["LONDON"].hypothesis_type in {"BREAKOUT_HOLD_PENDING", "FALSE_BREAKOUT_PENDING", "RANGE_OBSERVATION"}
    assert fc["NY"].session_id.endswith("2026-04-14")
    order = rank_forecasts(fc, "NY")
    assert order[0].session == "NY"


def test_europe_partial_at_ny_uses_asof_range_not_final_close():
    now = F.ny_ms(2026, 4, 14, 8, 30)
    cbt = F.make_candles_by_tf("LONG", F.NOW_MS)          # full tape that also holds bars AFTER 08:30
    ctx_full = load_closed_context(cbt, now, CFG)         # cutoff is applied by now_ms
    fc = evaluate_session_forecasts("XAUUSDT", ctx_full, now, CFG)
    lon = fc["LONDON"].session_range
    assert lon["is_partial"] is True and lon["is_complete"] is False
    assert lon["cutoff_ms"] == now                        # NOT 11:30
    last_closed = [c for c in cbt["5M"] if c.timestamp + F.M5 <= now][-1]
    assert lon["close_as_of"] == round(last_closed.close, 2)
    assert lon["high"] <= max(c.high for c in cbt["5M"] if c.timestamp + F.M5 <= now)


def test_future_bars_never_change_an_earlier_forecast():
    t = F.ny_ms(2026, 4, 14, 8, 45)
    base = F.make_candles_by_tf("LONG", F.NOW_MS)
    cut = {k: [c for c in v if c.timestamp + (F.M5 if k == "5M" else 0) <= t] for k, v in base.items()}
    a = {k: v.to_dict() for k, v in forecasts_at(t, candles=cut).items()}
    mutated = copy.deepcopy(base)
    for c in mutated["5M"]:
        if c.timestamp + F.M5 > t:
            c.high += 500.0; c.low -= 500.0; c.close += 400.0
    b = {k: v.to_dict() for k, v in forecasts_at(t, candles=mutated).items()}
    assert a == b


def test_asia_data_missing_is_data_unavailable_not_a_story():
    cbt = F.make_candles_by_tf("LONG")
    start, end = get_session_window_bounds("ASIA", "2026-04-14")
    cbt["5M"] = [c for c in cbt["5M"] if not (start <= c.timestamp < start + (end - start) // 2)]   # half the Asia bars gone
    fc = forecasts_at(F.NOW_MS, candles=cbt)
    assert fc["ASIA"].data_status == "DATA_UNAVAILABLE" and fc["ASIA"].hypothesis_type == "UNKNOWN"
    assert fc["LONDON"].data_status == "DATA_UNAVAILABLE"
    assert "quét" not in fc["LONDON"].summary_vi        # must not narrate a sweep of an unknown Asia low


def test_before_the_session_starts_it_is_not_started():
    early = F.ny_ms(2026, 4, 14, 1, 0)
    fc = forecasts_at(early, candles=F.make_candles_by_tf("LONG", early))
    assert fc["LONDON"].data_status in ("NOT_STARTED", "DATA_UNAVAILABLE")
    assert fc["NY"].data_status in ("NOT_STARTED", "DATA_UNAVAILABLE")


def test_dst_session_windows_are_ny_local():
    # Asia for NY date D opens at 19:00 NY of D-1: 00:00 UTC in winter (EST), 23:00 UTC in summer (EDT)
    s_win, _ = get_session_window_bounds("ASIA", "2026-01-15")
    s_sum, _ = get_session_window_bounds("ASIA", "2026-07-15")
    assert datetime.fromtimestamp(s_win / 1000, timezone.utc).hour == 0
    assert datetime.fromtimestamp(s_sum / 1000, timezone.utc).hour == 23
    ny_win, _ = get_session_window_bounds("NY", "2026-01-15")
    ny_sum, _ = get_session_window_bounds("NY", "2026-07-15")
    assert datetime.fromtimestamp(ny_win / 1000, timezone.utc).hour == 13
    assert datetime.fromtimestamp(ny_sum / 1000, timezone.utc).hour == 12
    # day after the spring-forward date: Asia opens 19:00 EDT of 2026-03-08
    s_dst, e_dst = get_session_window_bounds("ASIA", "2026-03-09")
    assert datetime.fromtimestamp(s_dst / 1000, timezone.utc) == datetime(2026, 3, 8, 23, 0, tzinfo=timezone.utc)
    assert (e_dst - s_dst) == 7 * 3600_000


def test_partial_session_range_reports_coverage_not_just_bar_count():
    start, end = get_session_window_bounds("LONDON", "2026-10-12")
    now = start + 2 * 3600_000

    class B:
        def __init__(s, ts, o, h, l, c):
            s.timestamp, s.open, s.high, s.low, s.close, s.is_closed = ts, o, h, l, c, True
    bars = [B(start + i * F.M5, 2650.0, 2655.0 + i * 0.1, 2648.0, 2652.0) for i in range(24)]   # 2h of a 2h partial
    rng = build_session_range(session_name="LONDON", ny_date_str_="2026-10-12", closed_candles=bars, now_ms=now)
    assert rng.is_partial and not rng.is_complete and rng.expected_bars == 24 and rng.coverage_status == "VALID"
    sparse = build_session_range(session_name="LONDON", ny_date_str_="2026-10-12", closed_candles=bars[:5], now_ms=now)
    assert sparse.coverage_status == "LOW_COVERAGE" and sparse.gap_bars == 19    # 10 bars used to be enough for VALID


def test_feature_formulas_and_null_on_missing_history():
    assert compute_clv(110.0, 100.0, 108.0) == pytest.approx(0.6)
    assert compute_clv(100.0, 100.0, 100.0) is None                  # zero range
    assert compute_europe_position(105.0, 110.0, 100.0) == pytest.approx(0.0)
    assert compute_europe_position(115.0, 110.0, 100.0) == pytest.approx(1.0)
    assert compute_europe_position(105.0, 100.0, 100.0) is None
    assert compute_asia_expansion(10.0, []) is None                  # no completed-Asia history -> null, not a default
    assert compute_asia_expansion(10.0, [5.0] * 20) == pytest.approx(2.0)
