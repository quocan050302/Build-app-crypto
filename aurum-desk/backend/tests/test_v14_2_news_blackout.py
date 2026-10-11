"""
V14.2 - structured news blackout (pure compute, ORM adapter, crud tuple wrapper, import normalisation).
All timestamps are fixed epoch milliseconds (UTC); no network, no runtime DB.
"""
import json
from datetime import datetime, timezone

import pytest

import crud
import models
from news_service import parse_csv_calendar_preview, parse_forex_factory_json
from services.news_blackout_service import (
    NewsEventInput,
    compute_news_blackout,
    evaluate_news_blackout,
    is_fed_event,
    normalize_impact,
)

MIN = 60_000
T = 1_800_000_000_000  # scheduled time of the main event


def ev(event_id="1", title="Core CPI m/m", currency="USD", impact="High", at=T, **kw):
    return NewsEventInput(event_id=event_id, title=title, currency=currency, impact=impact,
                          scheduled_at_ms=at, **kw)


# --------------------------------------------------------------------------- pure compute

@pytest.mark.parametrize("raw", ["High", "high", "HIGH", " high ", "red", "Red", "RED", "đỏ", "Đỏ"])
def test_impact_casing_and_red_label_normalise_to_high(raw):
    assert normalize_impact(raw) == "High"
    assert compute_news_blackout([ev(impact=raw)], T).is_blackout is True


@pytest.mark.parametrize("raw", ["Medium", "Low", "Holiday", "", None, "weird"])
def test_non_high_impact_never_blocks(raw):
    assert compute_news_blackout([ev(impact=raw)], T).is_blackout is False


def test_default_scope_is_usd_for_gold():
    assert compute_news_blackout([ev(currency="USD")], T).is_blackout is True
    for cur in ("EUR", "JPY", "GBP"):
        view = compute_news_blackout([ev(currency=cur)], T)
        assert view.is_blackout is False, cur


def test_missing_currency_only_blocks_explicit_fed_events():
    assert compute_news_blackout([ev(currency="", title="FOMC Press Conference")], T).is_blackout is True
    assert compute_news_blackout([ev(currency="", title="Fed Chair Powell Speaks")], T).is_blackout is True
    assert compute_news_blackout([ev(currency="", title="Core CPI m/m")], T).is_blackout is False


def test_fed_token_uses_word_boundary_not_substring():
    assert is_fed_event("FOMC Meeting Minutes")
    assert is_fed_event("Federal Reserve Chair Speaks")
    assert not is_fed_event("Federated Retail Sales")
    assert not is_fed_event("Unfed Index")
    # A non-Fed title keeps the standard -30/+15 window even though it contains "fed" inside a word.
    view = compute_news_blackout([ev(title="Federated Retail Sales")], T)
    assert (view.before_minutes, view.after_minutes) == (30, 15)


def test_fed_and_rate_decision_use_wide_window():
    view = compute_news_blackout([ev(title="FOMC Rate Decision")], T)
    assert (view.before_minutes, view.after_minutes) == (60, 30)
    assert view.start_ms == T - 60 * MIN and view.end_ms == T + 30 * MIN
    view = compute_news_blackout([ev(title="Core CPI m/m")], T)
    assert (view.before_minutes, view.after_minutes) == (30, 15)


def test_future_known_or_received_events_do_not_apply_yet():
    assert compute_news_blackout([ev(known_at_ms=T + 1)], T).is_blackout is False
    assert compute_news_blackout([ev(received_at_ms=T + 1)], T).is_blackout is False
    # Once known, the same event applies.
    assert compute_news_blackout([ev(known_at_ms=T, received_at_ms=T - 5)], T).is_blackout is True
    # Missing metadata: treated as live data, not as a fabricated past timestamp.
    assert compute_news_blackout([ev()], T).is_blackout is True


def test_overlapping_windows_resume_at_latest_end_with_stable_primary():
    a = ev("a", "Core CPI m/m", at=T)                      # end = T + 15m
    b = ev("b", "FOMC Rate Decision", at=T + 10 * MIN)     # end = T + 40m
    view = compute_news_blackout([a, b], T)
    assert view.is_blackout
    assert view.end_ms == T + 40 * MIN
    assert view.event_id == "b"
    assert view.covered_event_ids == ("a", "b")
    # Input order must not change the answer.
    assert compute_news_blackout([b, a], T) == view


@pytest.mark.parametrize(
    "offset_ms, expected",
    [(-30 * MIN - 1, False), (-30 * MIN, True), (15 * MIN, True), (15 * MIN + 1, False)],
)
def test_inclusive_boundaries_plus_minus_one_millisecond(offset_ms, expected):
    assert compute_news_blackout([ev()], T + offset_ms).is_blackout is expected


def test_remaining_minutes_uses_ceil_and_zero_at_exact_end():
    end = T + 15 * MIN
    assert compute_news_blackout([ev()], end).remaining_minutes == 0
    assert compute_news_blackout([ev()], end - 1).remaining_minutes == 1
    assert compute_news_blackout([ev()], end - MIN).remaining_minutes == 1
    assert compute_news_blackout([ev()], end - MIN - 1).remaining_minutes == 2


def test_upcoming_event_outside_window_is_reminder_only():
    view = compute_news_blackout([ev(at=T + 2 * 60 * MIN, title="NFP")], T)
    assert view.is_blackout is False
    assert view.upcoming_title == "NFP"
    assert view.upcoming_scheduled_ms == T + 2 * 60 * MIN


# --------------------------------------------------------------------------- ORM adapter + crud wrapper

def add_news(db, title="Core CPI m/m", currency="USD", impact="High", at=T, received_at=None, sid=None):
    row = models.EconomicNews(
        source_id=sid or f"{title}-{at}-{currency}", title=title, country=currency, currency=currency,
        impact=impact, scheduled_at=at, received_at=received_at if received_at is not None else T - 3600_000,
    )
    db.add(row)
    db.commit()
    return row


def test_wrapper_tuple_and_dto_describe_the_same_event_and_end(isolated_db):
    db = isolated_db
    add_news(db, "Core CPI m/m", at=T)
    add_news(db, "FOMC Rate Decision", at=T + 10 * MIN)
    now = T + 5 * MIN
    is_b, reason, remaining = crud.check_news_blackout(db, now)
    view = evaluate_news_blackout(db, now)
    assert is_b is True and view.is_blackout is True
    assert reason == view.reason_text()
    assert "FOMC Rate Decision" in reason
    assert remaining == max(1, view.remaining_minutes)
    assert view.end_ms == T + 40 * MIN


def test_wrapper_keeps_legacy_tuple_when_clear(isolated_db):
    assert crud.check_news_blackout(isolated_db, T) == (False, None, 0)


def test_adapter_reads_legacy_red_label_and_ignores_non_usd(isolated_db):
    db = isolated_db
    add_news(db, "Legacy red event", impact="Red", at=T, sid="legacy")
    assert crud.check_news_blackout(db, T)[0] is True


def test_adapter_non_usd_high_is_visible_as_upcoming_but_not_blocking(isolated_db):
    db = isolated_db
    add_news(db, "ECB Rate Decision", currency="EUR", at=T)
    assert crud.check_news_blackout(db, T)[0] is False


def test_adapter_causal_received_at_filter(isolated_db):
    db = isolated_db
    add_news(db, "Late import", at=T, received_at=T + 1000, sid="late")
    assert crud.check_news_blackout(db, T)[0] is False  # not known yet at T
    assert crud.check_news_blackout(db, T + 1000)[0] is True


def test_calendar_status_distinguishes_empty_calendar_from_no_event(isolated_db):
    db = isolated_db
    empty = evaluate_news_blackout(db, T)
    assert empty.is_blackout is False and empty.calendar_status == "EMPTY"
    add_news(db, "Far away NFP", at=T + 5 * 24 * 60 * MIN, sid="far")
    loaded = evaluate_news_blackout(db, T)
    assert loaded.is_blackout is False and loaded.calendar_status == "AVAILABLE"
    assert loaded.calendar_updated_at_ms is not None


# --------------------------------------------------------------------------- import boundary

def _utc_ms(y, m, d, hh, mm):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() * 1000)


def test_csv_import_normalises_impact_and_does_not_guess_currency():
    csv_text = (
        "title,country,date,time,impact\n"
        "Core CPI m/m,USD,2026-10-08,08:30,Red\n"
        "ECB Rate Decision,EUR,2026-10-08,07:45,high\n"
        "FOMC Press Conference,,2026-10-08,14:30,\u0111\u1ecf\n"
        "Some Holiday,USD,2026-10-08,09:00,Holiday\n"
    )
    res = parse_csv_calendar_preview(csv_text, source_timezone="America/New_York")
    rows = {r.title: r for r in res.preview_rows}
    assert all(r.is_valid for r in res.preview_rows)
    assert rows["Core CPI m/m"].impact == "High"
    assert rows["ECB Rate Decision"].impact == "High"
    assert rows["FOMC Press Conference"].impact == "High"
    assert rows["Some Holiday"].impact == "Holiday"
    # Missing country stays unknown (NOT silently turned into USD).
    assert rows["FOMC Press Conference"].country == ""
    # 08:30 New York in October (EDT, UTC-4) == 12:30 UTC, in epoch milliseconds.
    assert rows["Core CPI m/m"].scheduled_at_utc_ms == _utc_ms(2026, 10, 8, 12, 30)


def test_json_import_normalises_impact_and_keeps_timezone_offsets():
    payload = json.dumps([
        {"title": "Core CPI m/m", "country": "USD", "date": "2026-10-08T08:30:00-04:00", "impact": "RED"},
        {"title": "Fed Chair Speaks", "date": "2026-10-08T12:00:00-04:00", "impact": "high"},
    ])
    items = parse_forex_factory_json(payload)
    assert [i.impact for i in items] == ["High", "High"]
    assert items[0].scheduled_at == _utc_ms(2026, 10, 8, 12, 30)
    assert items[1].currency == ""  # unknown source currency is not guessed as USD
    # Imported rows feed the same blackout rule: USD High blocks, the currency-less Fed event blocks too.
    views = [
        compute_news_blackout(
            [NewsEventInput(str(n), i.title, i.currency or "", i.impact, i.scheduled_at)], i.scheduled_at
        ).is_blackout
        for n, i in enumerate(items)
    ]
    assert views == [True, True]
