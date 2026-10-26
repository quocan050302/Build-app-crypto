"""
V14.3 NY session quota / fallback test suite.

Integrated tests run the REAL chain: NY cycle -> real generator -> dedicated upsert (real flush/commit) ->
auto-arm (V14.2 guard) -> execution coordinator -> TradeLifecycleService -> quota. Only data (candles),
the injected clock and the quote are fixtures; calculator / guard / lifecycle are never mocked.
"""
import json
import math

import pytest

import crud
import models
from services.clock import FakeClock
from services.execution_coordinator import execution_coordinator
from services.lesson_policy_service import LessonPolicyService
from services.lesson_rule_service import LessonRuleService
from services.ny_fallback_service import NYFallbackService
from services.ny_opportunity_audit_service import NYOpportunityAuditService
from services.ny_quota_helpers import (
    closed_candles_at, effective_fallback_risk_pct, remaining_ny_risk_budget, should_search_ny_fallback,
)
from services.ny_quota_service import upsert_ny_fallback_setup, compute_remaining_ny_risk_budget, UpsertError
from services.strategy_service import strategy_service
from services.trading_policy_service import TradingPolicyService

from ny_quota_fixtures import (
    BIAS, QUOTE, FIVE, RED_ALWAYS, bars_for, candle_objects, make_lesson, make_news, ny_ms, seed_candles,
    seed_candles_15m, set_policy, set_saved_risk, ticker,
)

DIRS = ["LONG", "SHORT"]
PRE_BARS = [(2672, 2674, 2670, 2673)] * 3        # flat lead-in so truncated stories keep the 15-bar ATR warmup
OK_FEED = {"has_execution_gap": False, "is_lagging": False}
T_1035 = (2026, 10, 9, 10, 35)


@pytest.fixture
def db(isolated_db):
    LessonPolicyService.reset_to_defaults(isolated_db)
    LessonRuleService.invalidate_cache()
    set_saved_risk(isolated_db)
    set_policy(isolated_db, ny_fallback_enabled=True, entry_session_policy="NY_ONLY")
    return isolated_db


def run_cycle(db, now_ms, direction, *, auto=True, feed=OK_FEED, quote_age_ms=500, tick=None, htf=None, h1="ALIGNED"):
    clock = FakeClock(now_ms)
    strategy_service.evaluate_upcoming_setups(
        "XAUUSDT", "15M", db=db, clock=clock,
        ticker=tick if tick is not None else ticker(direction, now_ms, quote_age_ms),
        htf_bias=htf or BIAS[direction], h1_alignment=h1, feed_health=feed, auto_enabled=auto)


def fill_tick(db, now_ms, direction):
    clock = FakeClock(now_ms)
    execution_coordinator.evaluate_orders_sync(
        db, ticker_override=ticker(direction, now_ms, 200), clock=clock, now_ms=now_ms)


def quota_of(db, now_ms):
    return TradingPolicyService.update_quota_lifecycle_state(db, now_ms=now_ms, clock=FakeClock(now_ms))


def count(db, model, **flt):
    return db.query(model).filter_by(**flt).count()


def events_of(db, event_type):
    return db.query(models.DomainEvent).filter(models.DomainEvent.event_type == event_type).all()


# =====================================================================================================
# Pure helpers (Appendix D)
# =====================================================================================================

def test_search_disabled_even_after_1030():
    assert should_search_ny_fallback(
        policy_active=True, mode='PAPER', fallback_enabled=False,
        now_ms=1035, window_start_ms=800, fallback_start_ms=1030,
        window_end_ms=1100, target_fills=1, total_fills=0) is False


def test_zero_remaining_budget_never_gets_a_minimum_trade():
    assert effective_fallback_risk_pct(
        equity=1000.0, remaining_usdt=0.0, configured_cap_pct=0.10, requested_risk_pct=0.25) == 0.0


@pytest.mark.parametrize("kw,expected", [
    (dict(now_ms=1030), True), (dict(now_ms=1029), False), (dict(now_ms=1100), False),
    (dict(now_ms=1099), True), (dict(total_fills=1), False), (dict(target_fills=0), False),
    (dict(target_fills=2, total_fills=1), True), (dict(policy_active=False), False), (dict(mode="LIVE"), False),
    (dict(total_fills=-1), False),
])
def test_search_helper_boundaries(kw, expected):
    base = dict(policy_active=True, mode='PAPER', fallback_enabled=True, now_ms=1035, window_start_ms=800,
                fallback_start_ms=1030, window_end_ms=1100, target_fills=1, total_fills=0)
    base.update(kw)
    assert should_search_ny_fallback(**base) is expected


def test_remaining_budget_semantics():
    assert remaining_ny_risk_budget(daily_loss_budget_usdt=15, realized_pnl_today=-5, reserved_net_risk_usdt=2) == 8
    # profit never raises the daily budget
    assert remaining_ny_risk_budget(daily_loss_budget_usdt=15, realized_pnl_today=+40, reserved_net_risk_usdt=0) == 15
    assert remaining_ny_risk_budget(daily_loss_budget_usdt=15, realized_pnl_today=-20, reserved_net_risk_usdt=0) == 0
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="RISK_BUDGET_DATA_UNAVAILABLE"):
            remaining_ny_risk_budget(daily_loss_budget_usdt=bad, realized_pnl_today=0, reserved_net_risk_usdt=0)
    with pytest.raises(ValueError, match="INVALID_RISK_BUDGET"):
        remaining_ny_risk_budget(daily_loss_budget_usdt=-1, realized_pnl_today=0, reserved_net_risk_usdt=0)


def test_effective_risk_pct_is_min_of_all_caps_and_never_rounds_up():
    f = effective_fallback_risk_pct
    assert f(equity=1000, remaining_usdt=15, configured_cap_pct=0.10, requested_risk_pct=0.25) == 0.10
    assert f(equity=1000, remaining_usdt=0.5, configured_cap_pct=0.10, requested_risk_pct=0.25) == 0.05
    assert f(equity=1000, remaining_usdt=15, configured_cap_pct=0.05, requested_risk_pct=0.25) == 0.05
    assert f(equity=1000, remaining_usdt=15, configured_cap_pct=0.50, requested_risk_pct=0.25) == 0.10  # hard cap
    assert f(equity=1000, remaining_usdt=0.99999999, configured_cap_pct=0.1, requested_risk_pct=0.25) <= 0.0999999999
    for bad in (float("nan"), float("-inf")):
        with pytest.raises(ValueError):
            f(equity=bad, remaining_usdt=1, configured_cap_pct=0.1, requested_risk_pct=0.2)
    assert f(equity=-5, remaining_usdt=1, configured_cap_pct=0.1, requested_risk_pct=0.2) == 0.0


def test_closed_candles_helper_rules():
    from types import SimpleNamespace as N
    def c(ts, closed=True, o=1.0):
        return N(timestamp=ts, is_closed=closed, open=o, high=2, low=0.5, close=1.5)
    now = 10 * FIVE
    got = closed_candles_at([c(5 * FIVE), c(9 * FIVE), c(9 * FIVE), c(8 * FIVE, closed=False), c(10 * FIVE)],
                            now_ms=now, duration_ms=FIVE)
    assert [x.timestamp for x in got] == [5 * FIVE, 9 * FIVE]     # open-at-cutoff, unclosed, future excluded; dedupe+sort
    with pytest.raises(ValueError, match="CONFLICTING_CANDLE_DUPLICATE"):
        closed_candles_at([c(FIVE), c(FIVE, o=1.2)], now_ms=now, duration_ms=FIVE)
    with pytest.raises(ValueError, match="INVALID_CANDLE_TIMESTAMP"):
        closed_candles_at([c(0)], now_ms=now, duration_ms=FIVE)
    with pytest.raises(ValueError, match="INVALID_TIMEFRAME"):
        closed_candles_at([], now_ms=now, duration_ms=0)
    # missing flag is "unavailable", never assumed closed
    assert closed_candles_at([N(timestamp=FIVE, open=1, high=2, low=1, close=1)], now_ms=now, duration_ms=FIVE) == []


# =====================================================================================================
# Generator: positive controls + data / structure edges (pure, no DB)
# =====================================================================================================

def _gen(direction, now_ms, policy, **over):
    from services.quote_validator import QuoteValidator
    t = ticker(direction, now_ms)
    kw = dict(
        symbol="XAUUSDT", candles_5m=candle_objects(direction, now_ms), current_price=t["bid"],
        htf_bias=BIAS[direction], h1_alignment="ALIGNED", policy=policy, account_equity=1000.0,
        remaining_risk_allowance_usdt=15.0, leverage=30, now_ms=now_ms,
        window={"window_end_ms": ny_ms(2026, 10, 9, 11, 0), "session_instance_id": "NY-2026-10-09"},
        quote=QuoteValidator.validate_ticker(t, now_ms=now_ms), requested_risk_pct=0.25)
    kw.update(over)
    return NYFallbackService.evaluate_fallback_setup(**kw)


@pytest.mark.parametrize("direction", DIRS)
def test_generator_positive_fixture_is_eligible_unconditionally(db, direction):
    now = ny_ms(*T_1035)
    r = _gen(direction, now, TradingPolicyService.get_policy(db))
    assert r["is_eligible"] is True, r
    c = r["candidate"]
    assert c is not None and c["direction"] == direction
    assert c["risk_pct"] <= 0.10 and c["strategy_family"] == "NY_QUOTA_PAPER"
    assert c["estimated_net_rr"] >= 2.0
    assert c["expires_at"] == ny_ms(2026, 10, 9, 11, 0) if now + 45 * 60000 > ny_ms(2026, 10, 9, 11, 0) else True
    assert c["expires_at"] <= ny_ms(2026, 10, 9, 11, 0)
    ev = c["evidence"]
    assert ev["is_experimental"] is True and ev["structure_chain_verified"] is False and ev["sweep"] == "ABSENT"
    assert ev["retest"]["source"] == "TICK_IN_ZONE" and ev["retest"]["quote_age_ms"] == 500
    assert ev["fvg_id"] and ev["liquidity_target"]["pivot_id"] and ev["atr"]["period"] == 14
    if direction == "LONG":
        assert c["planned_entry"] == QUOTE["LONG"]["ask"] and c["stop_loss"] < c["planned_entry"] < c["targets"][0]["price"]
    else:
        assert c["planned_entry"] == QUOTE["SHORT"]["bid"] and c["targets"][0]["price"] < c["planned_entry"] < c["stop_loss"]


# =====================================================================================================
# Integrated positive pipeline (LONG + SHORT): cycle -> upsert -> arm -> coordinator -> lifecycle -> quota
# =====================================================================================================

@pytest.mark.parametrize("direction", DIRS)
def test_positive_full_pipeline_fills_and_fulfils_quota(db, direction):
    now = ny_ms(*T_1035)
    seed_candles(db, direction, now)

    run_cycle(db, now, direction)

    # real WatchSetup row exists (NOT NULL invalidation_price satisfied), session bound, experimental family
    db.expire_all()
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    assert ws.direction == direction and ws.state == "ARMED"
    assert ws.invalidation_price == ws.provisional_sl
    assert ws.strategy_family == "NY_QUOTA_PAPER" and ws.session_instance_id == "NY-2026-10-09"
    assert ws.direction_source == "FALLBACK_STRUCTURE" and ws.plan_kind == "CONFIRMED"
    assert ws.leverage == 30                                   # saved leverage, not a default 50
    assert ws.effective_risk_pct <= 0.10
    # exactly one armed order bound to the watch identity, nothing filled yet
    armed = db.query(models.PaperOrder).filter_by(state="armed").all()
    assert len(armed) == 1
    o = armed[0]
    assert o.order_type == "MARKET" and o.strategy_family == "NY_QUOTA_PAPER"
    assert o.setup_id == ws.id and o.setup_instance_id == ws.setup_instance_id
    assert o.session_instance_id == "NY-2026-10-09"
    assert o.expires_at <= ny_ms(2026, 10, 9, 11, 0)
    assert quota_of(db, now).total_fills == 0                  # ARMED is not a fill
    assert quota_of(db, now).quota_status == "ARMED_PENDING_FILL"

    fill_tick(db, now + 1000, direction)

    db.expire_all()
    assert count(db, models.PaperOrder, state="paper_open") == 1
    q = db.query(models.SessionQuota).filter_by(session_instance_id="NY-2026-10-09").one()
    assert (q.total_fills, q.fallback_fills, q.standard_fills) == (1, 1, 0)
    assert q.quota_status == "FULFILLED"
    assert crud.get_or_create_today_audit(db, clock=FakeClock(now)).fills_count == 1
    assert len(events_of(db, "trade.opened")) == 1
    opened_outbox = [x for x in db.query(models.NotificationOutbox).all() if "trade.opened" in (x.event_type or "")]
    assert len(opened_outbox) <= 1
    # one more tick / cycle changes nothing (exactly once)
    fill_tick(db, now + 2000, direction)
    run_cycle(db, now + 6000, direction)
    db.expire_all()
    assert db.query(models.SessionQuota).filter_by(session_instance_id="NY-2026-10-09").one().total_fills == 1
    assert count(db, models.PaperOrder) == 1 and len(events_of(db, "trade.opened")) == 1
    # funnel reflects the fill
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert (diag["unique_candidates"], diag["financially_eligible"], diag["armed"], diag["filled"]) == (1, 1, 1, 1)


# =====================================================================================================
# Search policy: flag / window / quota / auto / policy modes + DTO parity
# =====================================================================================================

class SpyGenerator:
    """Wraps the REAL generator, recording calls (an AssertionError raised inside would be swallowed by the
    outer loop, so a call list is the reliable trap)."""
    def __init__(self, monkeypatch, forbid=False):
        self.calls = 0
        self._orig = NYFallbackService.evaluate_fallback_setup
        spy = self

        def _wrapped(*a, **k):
            spy.calls += 1
            if forbid:
                raise AssertionError("generator must NOT be called")
            return spy._orig(*a, **k)
        monkeypatch.setattr(NYFallbackService, "evaluate_fallback_setup", staticmethod(_wrapped))


def test_disabled_flag_after_1030_never_calls_generator_and_says_so(db, monkeypatch):
    set_policy(db, ny_fallback_enabled=False)
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)               # standard SMC keeps running normally (it has its data)
    spy = SpyGenerator(monkeypatch, forbid=True)
    run_cycle(db, now, "LONG")
    assert spy.calls == 0
    assert count(db, models.PaperOrder) == 0
    assert count(db, models.WatchSetup, id="watch-XAUUSDT-5M-fallback") == 0   # no fallback row
    q = quota_of(db, now)
    assert q.quota_status == "SEEKING_STANDARD" and "Fallback đang tắt" in q.last_status_reason
    assert "Đang tìm fallback" not in q.last_status_reason
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert diag["scans"] == 1 and diag["top_blockers"][0]["code"] == "FALLBACK_DISABLED"
    assert diag["unique_candidates"] == 0


def test_enabled_flag_after_1030_calls_generator(db, monkeypatch):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    spy = SpyGenerator(monkeypatch)
    run_cycle(db, now, "LONG", auto=False)
    assert spy.calls == 1


@pytest.mark.parametrize("when,label", [
    ((2026, 10, 9, 10, 15, 0), "before_fallback_start"),
    ((2026, 10, 9, 10, 29, 59), "one_second_before_fallback"),
    ((2026, 10, 9, 11, 0, 0), "exactly_at_window_end"),
    ((2026, 10, 9, 11, 30, 0), "after_window"),
    ((2026, 10, 9, 7, 59, 59), "before_window"),
])
def test_generator_not_called_outside_fallback_interval(db, monkeypatch, when, label):
    now = ny_ms(*when)
    seed_candles(db, "LONG", now)
    spy = SpyGenerator(monkeypatch, forbid=True)
    run_cycle(db, now, "LONG")
    assert spy.calls == 0, label
    assert count(db, models.PaperOrder) == 0


def test_generator_runs_exactly_at_fallback_start(db, monkeypatch):
    now = ny_ms(2026, 10, 9, 10, 30, 0)
    seed_candles(db, "LONG", now)
    spy = SpyGenerator(monkeypatch)
    run_cycle(db, now, "LONG", auto=False)
    assert spy.calls == 1


def test_fulfilled_quota_stops_searching(db, monkeypatch):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    q = TradingPolicyService.get_or_create_session_quota(db, now_ms=now, clock=FakeClock(now))
    q.total_fills, q.standard_fills = 1, 1
    db.commit()
    spy = SpyGenerator(monkeypatch, forbid=True)
    run_cycle(db, now, "LONG")
    assert spy.calls == 0 and quota_of(db, now).quota_status == "FULFILLED"


def test_target_two_keeps_searching_after_first_fill(db, monkeypatch):
    set_policy(db, ny_min_fills=2)
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    q = TradingPolicyService.get_or_create_session_quota(db, now_ms=now, clock=FakeClock(now))
    assert q.target_fills == 2
    q.total_fills, q.standard_fills = 1, 1
    db.commit()
    spy = SpyGenerator(monkeypatch)
    run_cycle(db, now, "LONG", auto=False)
    assert spy.calls == 1                       # target-aware, not "fills == 0"


def test_auto_off_records_candidate_but_never_arms(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    run_cycle(db, now, "LONG", auto=False)
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    assert ws.state == "READY"
    assert count(db, models.PaperOrder) == 0
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert diag["unique_candidates"] == 1 and diag["financially_eligible"] == 1 and diag["armed"] == 0
    assert diag["top_blockers"][0]["code"] == "AUTO_PAPER_OFF"
    assert diag["candidates"][0]["reasons"][0]["category"] == "EXECUTION"


@pytest.mark.parametrize("policy_name", ["NY_ONLY", "ALL_SESSIONS_WITH_NY_RESERVE"])
@pytest.mark.parametrize("direction", DIRS)
def test_both_session_policies_complete_the_flow(db, policy_name, direction):
    set_policy(db, entry_session_policy=policy_name)
    now = ny_ms(*T_1035)
    seed_candles(db, direction, now)
    run_cycle(db, now, direction)
    fill_tick(db, now + 1000, direction)
    db.expire_all()
    q = db.query(models.SessionQuota).filter_by(session_instance_id="NY-2026-10-09").one()
    assert q.fallback_fills == 1 and q.quota_status == "FULFILLED"


@pytest.mark.parametrize("enabled", [True, False])
def test_window_policy_quota_dtos_agree_on_fallback_flag(db, enabled):
    set_policy(db, ny_fallback_enabled=enabled)
    now = ny_ms(*T_1035)
    clock = FakeClock(now)
    pol = TradingPolicyService.get_policy(db)
    window = TradingPolicyService.get_ny_session_window(pol, now_ms=now, clock=clock)
    ev = TradingPolicyService.evaluate_entry_policy(db, "XAUUSDT", now_ms=now, clock=clock)
    assert window["is_fallback_active"] is enabled
    assert ev["is_fallback_active"] is enabled is ev["window_info"]["is_fallback_active"]
    assert ev["quota_status"] == ("SEEKING_FALLBACK" if enabled else "SEEKING_STANDARD")
    for alias_a, alias_b in (("can_enter", "allowed"), ("quota_state", "quota_status"), ("ny_fills", "ny_fill_count")):
        assert ev[alias_a] == ev[alias_b]                     # existing aliases preserved


def test_fallback_family_cannot_enter_outside_window_even_with_all_sessions_policy(db):
    set_policy(db, entry_session_policy="ALL_SESSIONS_WITH_NY_RESERVE")
    after = ny_ms(2026, 10, 9, 11, 0, 0)
    clock = FakeClock(after)
    std = TradingPolicyService.evaluate_entry_policy(db, "XAUUSDT", now_ms=after, clock=clock, strategy_family="STANDARD_SMC")
    fb = TradingPolicyService.evaluate_entry_policy(db, "XAUUSDT", now_ms=after, clock=clock, strategy_family="NY_QUOTA_PAPER")
    assert std["allowed"] is True                             # policy of the user is untouched for standard
    assert fb["allowed"] is False and "OUTSIDE_ENTRY_WINDOW" in fb["reason_codes"]


def test_session_quota_creation_with_explicit_policy_none_does_not_crash(db):
    q = TradingPolicyService.get_or_create_session_quota(db, None, now_ms=ny_ms(*T_1035), clock=FakeClock(ny_ms(*T_1035)))
    assert q.target_fills == 1


# =====================================================================================================
# NOT NULL regression + dedicated upsert
# =====================================================================================================

def _candidate(db, direction, now, **gen_over):
    pol = TradingPolicyService.get_policy(db)
    r = _gen(direction, now, pol, **gen_over)
    assert r["is_eligible"] is True, r
    return r["candidate"]


def _window(db, now):
    return TradingPolicyService.get_ny_session_window(TradingPolicyService.get_policy(db), now_ms=now, clock=FakeClock(now))


@pytest.mark.parametrize("direction", DIRS)
def test_generator_to_upsert_to_commit_satisfies_not_null(db, direction):
    now = ny_ms(*T_1035)
    cand = _candidate(db, direction, now)
    out = upsert_ny_fallback_setup(db, cand, _window(db, now), now)
    db.commit()
    assert out["outcome"] == "CREATED"
    row = out["row"]
    assert row.invalidation_price == row.provisional_sl == cand["stop_loss"]
    assert row.invalidation_reason and "FVG" in row.invalidation_reason
    assert models.WatchSetup.__table__.c.invalidation_price.nullable is False   # constraint is NOT relaxed
    assert row.version == 1 and row.created_at == row.updated_at == now
    assert row.confirmed_entry is None and row.confirmed_sl is None and row.confirmed_tp is None
    assert row.expires_at == cand["expires_at"] <= _window(db, now)["window_end_ms"]


def test_missing_sl_rejected_before_any_write(db):
    now = ny_ms(*T_1035)
    cand = _candidate(db, "LONG", now)
    for bad in (None, 0, float("nan"), -1):
        broken = dict(cand, stop_loss=bad)
        with pytest.raises(UpsertError) as ei:
            upsert_ny_fallback_setup(db, broken, _window(db, now), now)
        assert ei.value.code == "MISSING_PRICE"
        assert count(db, models.WatchSetup) == 0           # no orphan, no 0-price substitute
    wrong_geom = dict(cand, stop_loss=cand["planned_entry"] + 5)
    with pytest.raises(UpsertError) as ei:
        upsert_ny_fallback_setup(db, wrong_geom, _window(db, now), now)
    assert ei.value.code == "INVALID_GEOMETRY" and count(db, models.WatchSetup) == 0
    with pytest.raises(UpsertError) as ei:                 # outside the entry window
        upsert_ny_fallback_setup(db, cand, _window(db, now), ny_ms(2026, 10, 9, 11, 0, 0))
    assert ei.value.code == "OUTSIDE_ENTRY_WINDOW"


def test_flush_error_propagates_and_rolls_back(db, monkeypatch):
    now = ny_ms(*T_1035)
    cand = _candidate(db, "LONG", now)
    cand2 = dict(cand)
    # force a real IntegrityError: tamper constraint-critical field after validation (direction NOT NULL)
    from services import ny_quota_service as svc
    orig = svc._apply_candidate

    def broken(row, *a, **k):
        orig(row, *a, **k)
        row.direction = None
    monkeypatch.setattr(svc, "_apply_candidate", broken)
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        upsert_ny_fallback_setup(db, cand2, _window(db, now), now)
    db.rollback()
    assert count(db, models.WatchSetup) == 0


def _flip_candidates(db):
    d0 = ny_ms(2026, 10, 8, 10, 35)
    d1 = ny_ms(2026, 10, 9, 10, 35)
    return (d0, _candidate(db, "LONG", d0, window={"window_end_ms": ny_ms(2026, 10, 8, 11, 0), "session_instance_id": "NY-2026-10-08"}),
            d1, _candidate(db, "SHORT", d1))


def test_second_day_short_replaces_yesterdays_long_whole_batch_and_persists(db):
    d0, long_c, d1, short_c = _flip_candidates(db)
    w0 = TradingPolicyService.get_ny_session_window(TradingPolicyService.get_policy(db), now_ms=d0, clock=FakeClock(d0))
    first = upsert_ny_fallback_setup(db, long_c, w0, d0)
    row = first["row"]
    row.confirmed_entry, row.confirmed_sl, row.confirmed_tp = 1.0, 2.0, 3.0   # stale confirmed triple must be cleared
    row.near_entry_alerted_at = 123
    db.commit()
    assert row.direction == "LONG" and row.version == 1

    out = upsert_ny_fallback_setup(db, short_c, _window(db, d1), d1)
    db.commit()
    assert out["outcome"] == "UPDATED"

    # reopen with a brand-new session: proves persistence, not identity-map caching
    from sqlalchemy.orm import sessionmaker
    s2 = sessionmaker(bind=db.get_bind())()
    try:
        r = s2.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
        assert r.direction == "SHORT" and r.state == "READY" and r.version == 2
        assert r.provisional_entry == short_c["planned_entry"]
        assert r.provisional_sl == short_c["stop_loss"] == r.invalidation_price
        assert r.provisional_tp == short_c["targets"][0]["price"]
        assert r.provisional_tp < r.provisional_entry < r.provisional_sl
        assert (r.confirmed_entry, r.confirmed_sl, r.confirmed_tp) == (None, None, None)
        assert r.near_entry_alerted_at is None
        assert r.setup_instance_id == short_c["instance_id"] != long_c["instance_id"]
        assert r.session_instance_id == "NY-2026-10-09"
        assert r.expires_at == short_c["expires_at"] and r.created_at == d1 and r.updated_at == d1
        assert json.loads(r.evidence_timeline)["instance_id"] == short_c["instance_id"]
        assert r.net_rr == short_c["estimated_net_rr"] and r.risk_usdt == short_c["initial_risk_usdt"]
        assert r.quantity == short_c["quantity"] and r.leverage == 30
    finally:
        s2.close()


@pytest.mark.parametrize("state", ["ARMED", "PAPER_OPEN", "TRIGGERED"])
def test_frozen_rows_are_never_overwritten(db, state):
    d0, long_c, d1, short_c = _flip_candidates(db)
    w0 = TradingPolicyService.get_ny_session_window(TradingPolicyService.get_policy(db), now_ms=d0, clock=FakeClock(d0))
    row = upsert_ny_fallback_setup(db, long_c, w0, d0)["row"]
    row.state = state
    db.commit()
    before = (row.direction, row.provisional_entry, row.provisional_sl, row.provisional_tp, row.version, row.setup_instance_id)
    out = upsert_ny_fallback_setup(db, short_c, _window(db, d1), d1)
    db.commit()
    assert out["outcome"] == "FROZEN"
    db.refresh(row)
    assert (row.direction, row.provisional_entry, row.provisional_sl, row.provisional_tp, row.version, row.setup_instance_id) == before
    assert row.state == state


def test_same_identity_terminal_is_retained_but_new_identity_is_reconsidered(db):
    now = ny_ms(*T_1035)
    cand = _candidate(db, "LONG", now)
    row = upsert_ny_fallback_setup(db, cand, _window(db, now), now)["row"]
    row.state = "REJECTED"
    db.commit()
    out = upsert_ny_fallback_setup(db, cand, _window(db, now), now + 6000)
    db.commit()
    assert out["outcome"] == "TERMINAL_RETAINED" and out["row"].state == "REJECTED"
    # other FVG anchor -> new identity -> may be reconsidered
    other = dict(cand, instance_id=cand["instance_id"] + "x")
    again = upsert_ny_fallback_setup(db, other, _window(db, now), now + 12000)
    db.commit()
    assert again["outcome"] == "UPDATED" and again["row"].state == "READY" and again["row"].version == 2


def test_same_identity_only_updates_on_material_change(db):
    now = ny_ms(*T_1035)
    cand = _candidate(db, "LONG", now)
    w = _window(db, now)
    assert upsert_ny_fallback_setup(db, cand, w, now)["outcome"] == "CREATED"
    db.commit()
    assert upsert_ny_fallback_setup(db, dict(cand, planned_entry=cand["planned_entry"] + 0.1), w, now + 6000)["outcome"] == "UNCHANGED"
    db.commit()
    moved = dict(cand, planned_entry=cand["planned_entry"] + 0.6)
    up = upsert_ny_fallback_setup(db, moved, w, now + 12000)
    db.commit()
    assert up["outcome"] == "UPDATED" and up["row"].version == 2
    tp_changed = dict(moved, targets=[dict(moved["targets"][0], price=moved["targets"][0]["price"] + 3)])
    up2 = upsert_ny_fallback_setup(db, tp_changed, w, now + 18000)
    db.commit()
    assert up2["outcome"] == "UPDATED" and up2["row"].version == 3 and up2["row"].provisional_tp == tp_changed["targets"][0]["price"]


def test_identity_is_stable_for_same_evidence_and_changes_for_new_day_or_direction(db):
    now = ny_ms(*T_1035)
    a = _candidate(db, "LONG", now)["instance_id"]
    b = _candidate(db, "LONG", now + 6000 - 6000)["instance_id"]
    c = _candidate(db, "SHORT", now)["instance_id"]
    assert a == b and a != c and len(a) < 60
    d1 = ny_ms(2026, 10, 10, 10, 35)
    n = _candidate(db, "LONG", d1, window={"window_end_ms": ny_ms(2026, 10, 10, 11, 0), "session_instance_id": "NY-2026-10-10"})
    assert n["instance_id"] != a


# =====================================================================================================
# Closed bars / causality / data issues (pure generator)
# =====================================================================================================

def test_forming_fvg_bar_never_creates_a_candidate(db):
    """The bar that would complete the bullish FVG is still forming (is_closed False)."""
    now = ny_ms(*T_1035)
    pol = TradingPolicyService.get_policy(db)
    bars = PRE_BARS + bars_for("LONG")[:13]      # idx 12 completes the FVG [2664, 2666]
    c = candle_objects("LONG", now, bars=bars)
    c[-1].is_closed = False
    r = _gen("LONG", now, pol, candles_5m=c)
    assert r["is_eligible"] is False and r["strategy_candidate"] is False
    assert r["reason_code"] in ("FALLBACK_NO_FVG", "FALLBACK_DATA_MISSING")
    # same bar flagged closed but its close time is in the future relative to the decision -> cutoff drops it
    c2 = candle_objects("LONG", now, bars=bars)
    c2[-1].timestamp = now - 60_000              # opened 1 minute ago: closes in 4 minutes
    r2 = _gen("LONG", now, pol, candles_5m=c2)
    assert r2["is_eligible"] is False and r2["strategy_candidate"] is False


def test_future_closed_bar_is_cut_off(db):
    now = ny_ms(*T_1035)
    pol = TradingPolicyService.get_policy(db)
    ok = candle_objects("LONG", now)
    future = candle_objects("LONG", now + 3600_000)[-1]      # closed flag True but belongs to the future
    r = _gen("LONG", now, pol, candles_5m=ok + [future])
    assert r["is_eligible"] is True
    assert r["diagnostics"]["closed_bars"] == len(ok)
    assert r["candidate"]["evidence"]["last_closed_bar_ts"] == ok[-1].timestamp


def test_retest_before_fvg_confirmation_does_not_pass(db):
    now = ny_ms(*T_1035)
    pol = TradingPolicyService.get_policy(db)
    bars = PRE_BARS + bars_for("LONG")[:13]
    c = candle_objects("LONG", now, bars=bars)               # FVG confirmed exactly at `now`
    from services.quote_validator import ValidatedQuote
    early = ValidatedQuote(is_valid=True, bid=2665.5, ask=2665.8, last=2665.5, spread=0.3,
                           exchange_time=now - 120_000, observed_at=now - 120_000, freshness_sec=0.5,
                           is_stale=False, rejection_code=None, rejection_detail=None)
    r = _gen("LONG", now, pol, candles_5m=c, quote=early)
    assert r["is_eligible"] is False and r["reason_code"] == "FALLBACK_WAITING_RETRACE"


def test_closed_bar_wick_retest_source_when_tick_outside_zone(db):
    now = ny_ms(*T_1035)
    pol = TradingPolicyService.get_policy(db)
    from services.quote_validator import ValidatedQuote
    q = ValidatedQuote(is_valid=True, bid=2667.0, ask=2667.4, last=2667.0, spread=0.4, exchange_time=now - 500,
                       observed_at=now - 500, freshness_sec=0.5, is_stale=False, rejection_code=None, rejection_detail=None)
    r = _gen("LONG", now, pol, quote=q)
    assert r["is_eligible"] is True and r["candidate"]["evidence"]["retest"]["source"] == "CLOSED_BAR_WICK"


def test_price_never_retested_means_waiting_with_zone_in_message(db):
    now = ny_ms(*T_1035)
    pol = TradingPolicyService.get_policy(db)
    bars = list(bars_for("LONG"))
    bars[-1] = (2669, 2672, 2668.5, 2671)                    # last closed bar stays above the zone (top 2666)
    from services.quote_validator import ValidatedQuote
    q = ValidatedQuote(is_valid=True, bid=2671.0, ask=2671.3, last=2671.0, spread=0.3, exchange_time=now - 500,
                       observed_at=now - 500, freshness_sec=0.5, is_stale=False, rejection_code=None, rejection_detail=None)
    r = _gen("LONG", now, pol, candles_5m=candle_objects("LONG", now, bars=bars), quote=q)
    assert r["reason_code"] == "FALLBACK_WAITING_RETRACE" and "2664.00 - 2666.00" in r["missing_conditions"][0]
    assert r["reason_category"] == "STRATEGY"


def test_warmup_requires_fifteen_closed_bars_and_no_default_atr(db):
    now = ny_ms(*T_1035)
    pol = TradingPolicyService.get_policy(db)
    r = _gen("LONG", now, pol, candles_5m=candle_objects("LONG", now)[-14:])
    assert r["reason_code"] == "FALLBACK_DATA_MISSING" and r["reason_category"] == "DATA"
    assert r["is_eligible"] is False and r["candidate"] is None


def test_invalid_ohlc_and_conflicting_duplicates_are_data_errors(db):
    now = ny_ms(*T_1035)
    pol = TradingPolicyService.get_policy(db)
    for mutate in (lambda c: setattr(c[3], "high", float("nan")), lambda c: setattr(c[3], "low", -1.0),
                   lambda c: setattr(c[3], "high", c[3].low - 1)):
        c = candle_objects("LONG", now)
        mutate(c)
        r = _gen("LONG", now, pol, candles_5m=c)
        assert r["reason_code"] == "DATA_INVALID_CANDLE" and r["reason_category"] == "DATA"
    c = candle_objects("LONG", now)
    dup = candle_objects("LONG", now)[5]
    dup.close = dup.close + 0.4
    r = _gen("LONG", now, pol, candles_5m=c + [dup])
    assert r["reason_code"] == "DATA_INVALID_CANDLE"


@pytest.mark.parametrize("bias,h1,code", [("UNKNOWN", "ALIGNED", "FALLBACK_HTF_NOT_CONFIRMED"),
                                           ("BULLISH", "OPPOSING", "FALLBACK_H1_NOT_ALIGNED")])
def test_bias_and_alignment_reasons_are_typed(db, bias, h1, code):
    r = _gen("LONG", ny_ms(*T_1035), TradingPolicyService.get_policy(db), htf_bias=bias, h1_alignment=h1)
    assert r["reason_code"] == code and r["reason_category"] == "STRATEGY"


def test_target_must_be_a_confirmed_pivot_on_the_entry_side(db):
    now = ny_ms(*T_1035)
    pol = TradingPolicyService.get_policy(db)
    bars = list(bars_for("LONG"))
    bars[15] = (2688, 2692, 2686, 2690)                      # peak lowered to 2692 -> target 2692 (still a pivot)
    r = _gen("LONG", now, pol, candles_5m=candle_objects("LONG", now, bars=bars))
    assert r["strategy_candidate"] is True
    tgt = r["structure"]["take_profit"]
    assert tgt > r["structure"]["entry_ref"]                 # right side of the entry
    # a pivot below entry is never accepted as a LONG target: remove all highs above entry
    flat = [(2664.5, 2666.5, 2664.2, 2665.0)] * 6 + [(2665, 2666.2, 2664.6, 2665.5)] * 15
    r2 = _gen("LONG", now, pol, candles_5m=candle_objects("LONG", now, bars=flat))
    assert r2["is_eligible"] is False and r2["reason_code"] in ("FALLBACK_NO_FVG", "FALLBACK_NO_LIQUIDITY_TARGET")


def test_low_net_rr_is_counted_as_candidate_but_never_as_eligible_or_armed(db):
    set_policy(db, min_net_rr=50.0)
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    run_cycle(db, now, "LONG")
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert (diag["unique_candidates"], diag["financially_eligible"], diag["armed"], diag["filled"]) == (1, 0, 0, 0)
    assert diag["top_blockers"][0]["code"] == "NET_RR_TOO_LOW"
    assert diag["candidates"][0]["reasons"][0]["category"] == "STRATEGY"
    assert count(db, models.PaperOrder) == 0 and count(db, models.WatchSetup, id="watch-XAUUSDT-5M-fallback") == 0


def test_min_net_rr_comes_from_saved_policy_not_a_constant(db):
    now = ny_ms(*T_1035)
    pol = TradingPolicyService.get_policy(db)
    ok = _gen("LONG", now, pol)
    assert ok["is_eligible"] is True
    pol.min_net_rr = ok["candidate"]["estimated_net_rr"] + 0.5      # saved policy asks for more than this plan has
    r = _gen("LONG", now, pol)
    assert r["is_eligible"] is False and r["reason_code"] == "NET_RR_TOO_LOW"
    assert f"{pol.min_net_rr:.1f}" in r["missing_conditions"][0]
    pol.min_net_rr = float("nan")
    assert _gen("LONG", now, pol)["reason_code"] == "CALCULATOR_REJECTED"


# =====================================================================================================
# Budget
# =====================================================================================================

@pytest.mark.parametrize("remaining,code", [(0.0, "NO_REMAINING_RISK"), (-3.0, "NO_REMAINING_RISK"),
                                            (float("nan"), "RISK_BUDGET_DATA_UNAVAILABLE"),
                                            (float("inf"), "RISK_BUDGET_DATA_UNAVAILABLE")])
def test_budget_zero_negative_nan_never_make_a_candidate(db, remaining, code):
    r = _gen("LONG", ny_ms(*T_1035), TradingPolicyService.get_policy(db), remaining_risk_allowance_usdt=remaining)
    assert r["is_eligible"] is False and r["candidate"] is None and r["reason_code"] == code
    assert r["strategy_candidate"] is True            # the signal existed; money is what blocked it


def test_below_min_step_is_a_controlled_calculator_rejection_not_attribute_error(db):
    r = _gen("LONG", ny_ms(*T_1035), TradingPolicyService.get_policy(db), remaining_risk_allowance_usdt=0.05)
    assert r["is_eligible"] is False and r["reason_code"] == "CALCULATOR_REJECTED"
    assert r["reason_category"] == "EXECUTION" and "MIN_QTY_EXCEEDS_BUDGET" in r["missing_conditions"][0]
    assert r["diagnostics"]["calc"]["net_rr"] >= 2.0   # R:R was good; quantity/budget is what failed
    r2 = _gen("LONG", ny_ms(*T_1035), TradingPolicyService.get_policy(db), account_equity=5.0)
    assert r2["reason_code"] == "CALCULATOR_REJECTED"


def test_net_risk_never_exceeds_remaining_budget_cap_or_ten_bps(db):
    pol = TradingPolicyService.get_policy(db)
    for remaining in (15.0, 3.0, 1.0, 0.5, 0.2):
        r = _gen("LONG", ny_ms(*T_1035), pol, remaining_risk_allowance_usdt=remaining)
        if r["is_eligible"]:
            c = r["candidate"]
            assert c["initial_risk_usdt"] <= remaining + 1e-9
            assert c["initial_risk_usdt"] <= 1000.0 * 0.0010 + 1e-9      # 0.10% cap
            assert c["risk_pct"] <= 0.10 and c["leverage"] == 30
    pol.ny_fallback_risk_pct_cap = 0.05
    c = _gen("LONG", ny_ms(*T_1035), pol)["candidate"]
    assert c["risk_pct"] <= 0.05 and c["initial_risk_usdt"] <= 0.5 + 1e-9


def test_remaining_budget_tracks_realized_loss_equity_and_matches_broker(db):
    from paper_broker import PaperBroker
    now = ny_ms(*T_1035)
    clock = FakeClock(now)
    audit = crud.get_or_create_today_audit(db, clock=clock)
    rem, eq, info = compute_remaining_ny_risk_budget(db, clock=clock)
    assert (rem, eq) == (pytest.approx(15.0), 1000.0)
    audit.realized_pnl_today = -10.0
    db.commit()
    rem, _, _ = compute_remaining_ny_risk_budget(db, clock=clock)
    assert rem == pytest.approx(5.0)
    audit.current_equity = 900.0
    db.commit()
    rem2, eq2, _ = compute_remaining_ny_risk_budget(db, clock=clock)
    assert rem2 == pytest.approx(5.0) and eq2 == 900.0   # budget is anchored on initial equity (broker rule)
    # parity with the broker guard: the remaining budget is allowed, a cent more is not
    assert PaperBroker.evaluate_account_guard(db, rem2, now_ms=now, clock=clock) is None
    blocked = PaperBroker.evaluate_account_guard(db, rem2 + 0.01, now_ms=now, clock=clock)
    assert blocked and blocked[0] == "DAILY_LOSS_BUDGET_EXCEEDED"
    audit.realized_pnl_today = -15.0
    db.commit()
    assert compute_remaining_ny_risk_budget(db, clock=clock)[0] == 0.0
    audit.current_equity = float("nan")
    with pytest.raises(ValueError, match="RISK_BUDGET_DATA_UNAVAILABLE"):
        compute_remaining_ny_risk_budget(db, clock=clock)
    db.rollback()


def test_zero_budget_cycle_arms_nothing_and_reports_no_remaining_risk(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    audit = crud.get_or_create_today_audit(db, clock=FakeClock(now))
    audit.realized_pnl_today = -15.0
    db.commit()
    run_cycle(db, now, "LONG")
    assert count(db, models.PaperOrder) == 0
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert diag["unique_candidates"] == 1 and diag["financially_eligible"] == 0
    assert any(b["code"] == "NO_REMAINING_RISK" for b in diag["top_blockers"])


# =====================================================================================================
# Clock / DST determinism
# =====================================================================================================

@pytest.mark.parametrize("when", [(2026, 10, 9, 10, 35), (2026, 11, 5, 10, 35), (2026, 3, 12, 10, 35), (2026, 1, 15, 10, 35)])
def test_pipeline_is_deterministic_under_dst_and_ignores_the_machine_clock(db, when):
    now = ny_ms(*when)
    seed_candles(db, "LONG", now)
    run_cycle(db, now, "LONG")
    fill_tick(db, now + 1000, "LONG")
    db.expire_all()
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    order = db.query(models.PaperOrder).one()
    assert ws.created_at == now and ws.updated_at <= now + 1000
    assert ws.expires_at == min(now + 45 * 60_000, ny_ms(*when[:3], 11, 0))
    assert order.created_at == order.armed_at == now
    assert order.opened_at == now + 1000 and order.state == "paper_open"
    q = db.query(models.SessionQuota).one()
    assert q.session_instance_id == f"NY-{when[0]:04d}-{when[1]:02d}-{when[2]:02d}"
    assert q.window_start_ms == ny_ms(*when[:3], 8, 0) and q.window_end_ms == ny_ms(*when[:3], 11, 0)
    assert q.total_fills == 1 and q.quota_status == "FULFILLED"
    assert q.last_evaluated_at >= now and q.last_evaluated_at <= now + 1000     # injected clock only


def test_vn_date_differs_from_ny_date_but_session_identity_follows_ny(db):
    late = ny_ms(2026, 10, 9, 13, 30)                      # 00:30 VN on the 10th
    w = _window(db, late)
    assert w["session_instance_id"] == "NY-2026-10-09" and w["session_date_ny"] == "2026-10-09"
    assert w["session_date_vn"] == "2026-10-09"            # window START is 19:00 VN on the 9th
    assert FakeClock(late).get_today_str_vn() == "2026-10-10"


# =====================================================================================================
# Negative full pipeline: signal exists, guard/quote/feed/account blocks -> counter stays 0, reason is typed
# =====================================================================================================

def _assert_blocked(db, now, *, code, direction="LONG"):
    db.expire_all()
    assert count(db, models.PaperOrder) == 0
    q = db.query(models.SessionQuota).filter_by(session_instance_id="NY-2026-10-09").one()
    assert q.total_fills == 0 and q.fallback_fills == 0 and q.quota_status != "FULFILLED"
    assert crud.get_or_create_today_audit(db, clock=FakeClock(now)).fills_count == 0
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert diag["unique_candidates"] == 1, diag                    # the signal is still counted
    assert diag["armed"] == 0 and diag["filled"] == 0
    assert diag["top_blockers"][0]["code"] == code, diag["top_blockers"]
    return diag


@pytest.mark.parametrize("direction", DIRS)
def test_news_blackout_blocks_fallback_arm_and_fill(db, direction):
    now = ny_ms(*T_1035)
    seed_candles(db, direction, now)
    seed_candles_15m(db, now)
    make_news(db, now)                                             # high-impact USD news right now
    run_cycle(db, now, direction)
    diag = _assert_blocked(db, now, code="NEWS_BLACKOUT")
    assert diag["financially_eligible"] == 1                       # RR/size were fine: purely a safety block
    assert diag["candidates"][0]["reasons"][0]["category"] == "SAFETY"
    assert quota_of(db, now).quota_status == "BLOCKED"


@pytest.mark.parametrize("direction", DIRS)
def test_red_lesson_blocks_fallback_arm(db, direction):
    now = ny_ms(*T_1035)
    seed_candles(db, direction, now)
    seed_candles_15m(db, now)
    make_lesson(db, "Không vào lệnh khi spread rộng", now)
    run_cycle(db, now, direction)
    diag = _assert_blocked(db, now, code=diag_code(db, now))
    assert diag["candidates"][0]["reasons"][0]["category"] == "SAFETY"


def diag_code(db, now):
    d = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    return d["top_blockers"][0]["code"] if d["top_blockers"] and d["top_blockers"][0]["code"].startswith("LESSON") else "LESSON_RULE_BLOCKED"


def test_stale_quote_blocks_with_execution_reason_but_candidate_is_counted(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    run_cycle(db, now, "LONG", quote_age_ms=60_000)
    diag = _assert_blocked(db, now, code="QUOTE_STALE")
    assert diag["financially_eligible"] == 0 and diag["candidates"][0]["reasons"][0]["category"] == "EXECUTION"


def test_feed_gap_blocks_arm(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    run_cycle(db, now, "LONG", feed={"has_execution_gap": True, "is_lagging": False})
    diag = _assert_blocked(db, now, code="EXECUTION_FEED_DEGRADED")
    assert diag["financially_eligible"] == 1
    # transient blocker cleared -> the very same candidate can be armed on the next scan
    run_cycle(db, now + 6000, "LONG")
    assert count(db, models.PaperOrder, state="armed") == 1


def test_account_limit_blocks_arm(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    audit = crud.get_or_create_today_audit(db, clock=FakeClock(now))
    audit.fills_count = 3
    db.commit()
    run_cycle(db, now, "LONG")
    db.expire_all()
    assert count(db, models.PaperOrder) == 0
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert diag["unique_candidates"] == 1 and diag["armed"] == 0
    assert diag["top_blockers"][0]["code"] in ("DAILY_FILL_CAP", "MAX_DAILY_ENTRIES")
    assert quota_of(db, now).quota_status == "BLOCKED"


def test_news_cleared_next_scan_can_arm_the_same_candidate(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    news = make_news(db, now)
    run_cycle(db, now, "LONG")
    assert count(db, models.PaperOrder) == 0
    db.delete(news)
    db.commit()
    run_cycle(db, now + 6000, "LONG")
    assert count(db, models.PaperOrder, state="armed") == 1
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert diag["unique_candidates"] == 1 and diag["armed"] == 1       # still ONE opportunity


def test_terminal_candidate_is_not_re_armed_every_scan(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    run_cycle(db, now, "LONG")
    order = db.query(models.PaperOrder).one()
    from services.trade_lifecycle_service import TradeLifecycleService
    TradeLifecycleService.execute_reject(db, order, reason="TEST_REJECT", now_ms=now + 1000, clock=FakeClock(now + 1000))
    db.commit()
    for i in range(1, 6):
        run_cycle(db, now + 6000 * i, "LONG")
    db.expire_all()
    assert count(db, models.PaperOrder) == 1                            # no spam, no "retry until lucky"
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    assert ws.state in ("REJECTED", "ARMED")
    assert db.query(models.PaperOrder).one().state == "rejected"
    assert db.query(models.SessionQuota).one().total_fills == 0


# =====================================================================================================
# Window deadline / pending / reserved slot
# =====================================================================================================

def test_arm_at_1059_fills_at_105959_and_counts(db):
    t_arm = ny_ms(2026, 10, 9, 10, 59, 0)
    seed_candles(db, "LONG", t_arm)
    run_cycle(db, t_arm, "LONG")
    order = db.query(models.PaperOrder).one()
    assert order.expires_at == ny_ms(2026, 10, 9, 11, 0, 0)           # never past the entry window
    t_fill = ny_ms(2026, 10, 9, 10, 59, 59)
    fill_tick(db, t_fill, "LONG")
    db.expire_all()
    assert db.query(models.PaperOrder).one().state == "paper_open"
    q = db.query(models.SessionQuota).one()
    assert q.total_fills == 1 and q.quota_status == "FULFILLED"


def test_armed_order_does_not_fill_at_1100_and_quota_is_missed(db):
    t_arm = ny_ms(2026, 10, 9, 10, 59, 0)
    seed_candles(db, "LONG", t_arm)
    run_cycle(db, t_arm, "LONG")
    assert quota_of(db, t_arm).total_fills == 0 and quota_of(db, t_arm).quota_status == "ARMED_PENDING_FILL"
    fill_tick(db, ny_ms(2026, 10, 9, 11, 0, 0), "LONG")
    db.expire_all()
    order = db.query(models.PaperOrder).one()
    assert order.state != "paper_open" and order.opened_at is None
    q = quota_of(db, ny_ms(2026, 10, 9, 11, 0, 1))
    assert q.total_fills == 0 and q.quota_status == "MISSED"
    assert crud.get_or_create_today_audit(db, clock=FakeClock(t_arm)).fills_count == 0
    assert "OUTSIDE_ENTRY_WINDOW" in (order.invalidation_reason or "") or order.state == "expired"


def test_position_exit_still_works_after_window_close(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    run_cycle(db, now, "LONG")
    fill_tick(db, now + 1000, "LONG")
    from services.trade_lifecycle_service import TradeLifecycleService
    late = ny_ms(2026, 10, 9, 11, 30)
    closed = TradeLifecycleService.process_exit_tick(db, current_bid=3000.0, current_ask=3000.2, now_ms=late, clock=FakeClock(late))
    db.expire_all()
    assert closed is not None and db.query(models.PaperOrder).one().state == "closed"
    assert db.query(models.SessionQuota).one().total_fills == 1          # close never re-counts


def test_open_position_blocks_search_with_reason_and_is_not_closed(db, monkeypatch):
    set_policy(db, ny_min_fills=2)          # target 2: one fill leaves the quota unfinished
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    run_cycle(db, now, "LONG")
    fill_tick(db, now + 1000, "LONG")
    spy = SpyGenerator(monkeypatch, forbid=True)
    run_cycle(db, now + 12_000, "LONG")
    assert spy.calls == 0
    assert count(db, models.PaperOrder, state="paper_open") == 1
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert any(b["code"] == "ACTIVE_POSITION_EXISTS" for b in diag["top_blockers"])


def test_armed_order_blocks_second_search(db, monkeypatch):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    run_cycle(db, now, "LONG")
    spy = SpyGenerator(monkeypatch, forbid=True)
    run_cycle(db, now + 6000, "LONG")
    assert spy.calls == 0 and count(db, models.PaperOrder) == 1
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert any(b["code"] == "ARMED_ORDER_EXISTS" for b in diag["top_blockers"])


def test_expired_pending_of_the_same_evidence_is_not_resurrected(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    run_cycle(db, now, "LONG")
    order = db.query(models.PaperOrder).one()
    order.state, order.exit_cause = "expired", "EXPIRED"
    db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one().state = "EXPIRED"
    db.commit()
    run_cycle(db, now + 6000, "LONG")                     # same evidence -> same identity -> not resurrected
    assert count(db, models.PaperOrder) == 1
    assert db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one().state == "EXPIRED"


# =====================================================================================================
# Two sessions / quota immutability
# =====================================================================================================

def test_two_consecutive_sessions_are_independent(db):
    d0 = ny_ms(2026, 10, 8, 10, 35)
    seed_candles(db, "LONG", d0)
    run_cycle(db, d0, "LONG")
    fill_tick(db, d0 + 1000, "LONG")
    from services.trade_lifecycle_service import TradeLifecycleService
    TradeLifecycleService.execute_close(db, db.query(models.PaperOrder).one().id, 2690.0, "TP_HIT",
                                        now_ms=d0 + 60_000, clock=FakeClock(d0 + 60_000))
    db.expire_all()
    q0 = db.query(models.SessionQuota).filter_by(session_instance_id="NY-2026-10-08").one()
    snap0 = (q0.total_fills, q0.fallback_fills, q0.quota_status, q0.updated_at)

    d1 = ny_ms(2026, 10, 9, 10, 35)
    seed_candles(db, "SHORT", d1)
    run_cycle(db, d1, "SHORT")
    fill_tick(db, d1 + 1000, "SHORT")
    db.expire_all()

    q0b = db.query(models.SessionQuota).filter_by(session_instance_id="NY-2026-10-08").one()
    q1 = db.query(models.SessionQuota).filter_by(session_instance_id="NY-2026-10-09").one()
    assert (q0b.total_fills, q0b.fallback_fills, q0b.quota_status) == snap0[:3]       # D is immutable
    assert (q1.total_fills, q1.fallback_fills, q1.quota_status) == (1, 1, "FULFILLED")
    orders = db.query(models.PaperOrder).order_by(models.PaperOrder.created_at).all()
    assert [o.direction for o in orders] == ["LONG", "SHORT"]
    assert [o.session_instance_id for o in orders] == ["NY-2026-10-08", "NY-2026-10-09"]
    assert orders[0].state == "closed" and orders[1].state == "paper_open"
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    assert ws.direction == "SHORT" and ws.session_instance_id == "NY-2026-10-09"
    assert ws.provisional_tp < ws.provisional_entry < ws.provisional_sl     # no stale LONG prices
    # restart (fresh session on the same engine) must not duplicate fills
    from sqlalchemy.orm import sessionmaker
    s2 = sessionmaker(bind=db.get_bind())()
    try:
        run_cycle(s2, d1 + 6000, "SHORT")
        fill_tick(s2, d1 + 7000, "SHORT")
        assert s2.query(models.PaperOrder).count() == 2
        assert s2.query(models.SessionQuota).filter_by(session_instance_id="NY-2026-10-09").one().total_fills == 1
    finally:
        s2.close()


def test_yesterdays_pending_order_is_not_overwritten_by_todays_candidate(db):
    d0 = ny_ms(2026, 10, 8, 10, 58)
    seed_candles(db, "LONG", d0)
    run_cycle(db, d0, "LONG")
    order = db.query(models.PaperOrder).one()
    ws_before = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    snap = (ws_before.direction, ws_before.provisional_entry, ws_before.provisional_sl, ws_before.setup_instance_id)
    d1 = ny_ms(2026, 10, 9, 10, 35)
    cand = _candidate(db, "SHORT", d1)
    out = upsert_ny_fallback_setup(db, cand, _window(db, d1), d1)
    assert out["outcome"] == "FROZEN"
    db.expire_all()
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    assert (ws.direction, ws.provisional_entry, ws.provisional_sl, ws.setup_instance_id) == snap
    assert db.query(models.PaperOrder).one().id == order.id


def test_direction_flip_before_arm_replaces_the_whole_plan(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    run_cycle(db, now, "LONG", auto=False)
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    assert ws.direction == "LONG" and ws.state == "READY"
    long_prices = (ws.provisional_entry, ws.provisional_sl, ws.provisional_tp)
    long_inst = ws.setup_instance_id
    # market flips: bearish story at the same session, later bar
    later = now + 15 * 60_000                # 10:50 NY, still inside the window
    seed_candles(db, "SHORT", later, replace=True)
    run_cycle(db, later, "SHORT", auto=False)
    db.expire_all()
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    assert ws.direction == "SHORT" and ws.setup_instance_id != long_inst and ws.version == 2
    assert (ws.provisional_entry, ws.provisional_sl, ws.provisional_tp) != long_prices
    assert ws.provisional_tp < ws.provisional_entry < ws.provisional_sl
    assert (ws.confirmed_entry, ws.confirmed_sl, ws.confirmed_tp) == (None, None, None)


# =====================================================================================================
# Standard vs fallback competition (one order, standard first, guards shared)
# =====================================================================================================

def _standard_ready(db, now, direction="LONG"):
    plan = dict(LONG=(2650.0, 2640.0, 2685.0), SHORT=(2650.0, 2660.0, 2615.0))[direction]
    e, sl, tp = plan
    ws = models.WatchSetup(
        id="watch-XAUUSDT-15M", timeframe="15M", direction=direction, state="READY", plan_kind="CONFIRMED",
        direction_source="SIGNAL", provisional_entry=e, provisional_sl=sl, provisional_tp=tp,
        confirmed_entry=e, confirmed_sl=sl, confirmed_tp=tp, invalidation_price=sl, leverage=5,
        margin_mode="ISOLATED", risk_pct=0.25, version=1, created_at=now, updated_at=now,
        setup_instance_id="std-1", strategy_family="STANDARD_SMC")
    db.add(ws)
    db.commit()
    return ws


def _arm_standard(db, now, direction="LONG", tick_dir=None):
    from datetime import datetime, timezone
    from services.quote_validator import QuoteValidator
    ws = _standard_ready(db, now, direction)
    q = QuoteValidator.validate_ticker({"bid": 2649.8, "ask": 2650.0, "last": 2649.8, "timestamp": now - 300,
                                        "observed_at": now - 300}, now_ms=now)
    res = strategy_service._auto_arm_candidate(
        db, ws, {"signal_id": "sig-std-1"}, now, datetime.fromtimestamp(now / 1000, tz=timezone.utc),
        clock=FakeClock(now), quote=q, feed_health=OK_FEED)
    return ws, res


def test_standard_armed_first_means_fallback_creates_no_second_order(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    ws, res = _arm_standard(db, now)
    assert res["armed"] is True, res
    run_cycle(db, now, "LONG")
    db.expire_all()
    orders = db.query(models.PaperOrder).all()
    assert len(orders) == 1 and orders[0].strategy_family == "STANDARD_SMC"
    assert count(db, models.WatchSetup, id="watch-XAUUSDT-5M-fallback") == 0


def test_standard_filled_means_fallback_never_searches_and_quota_counts_standard(db, monkeypatch):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    _arm_standard(db, now)
    execution_coordinator.evaluate_orders_sync(
        db, ticker_override={"symbol": "XAUUSDT", "bid": 2649.8, "ask": 2650.0, "timestamp": now + 500,
                             "observed_at": now + 500}, clock=FakeClock(now + 1000), now_ms=now + 1000)
    db.expire_all()
    assert count(db, models.PaperOrder, state="paper_open") == 1
    q = db.query(models.SessionQuota).one()
    assert (q.standard_fills, q.fallback_fills, q.total_fills) == (1, 0, 1)
    spy = SpyGenerator(monkeypatch, forbid=True)
    run_cycle(db, now + 6000, "LONG")
    assert spy.calls == 0 and count(db, models.PaperOrder) == 1


def test_news_blocks_standard_and_fallback_alike(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    make_news(db, now)
    ws, res = _arm_standard(db, now)
    assert res["armed"] is False and "NEWS_BLACKOUT" in res["codes"]
    run_cycle(db, now, "LONG")
    assert count(db, models.PaperOrder) == 0


def test_standard_without_sweep_but_valid_fallback_gets_the_actual_fill(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "SHORT", now)
    seed_candles_15m(db, now)               # quiet standard tape: nothing READY there
    run_cycle(db, now, "SHORT")
    fill_tick(db, now + 1000, "SHORT")
    db.expire_all()
    o = db.query(models.PaperOrder).one()
    assert o.strategy_family == "NY_QUOTA_PAPER" and o.state == "paper_open" and o.direction == "SHORT"
    assert db.query(models.SessionQuota).one().fallback_fills == 1


# =====================================================================================================
# Fallback toggled inside the session
# =====================================================================================================

def test_disabling_fallback_mid_session_keeps_pending_order_and_never_closes_position(db, monkeypatch):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    run_cycle(db, now, "LONG")
    assert count(db, models.PaperOrder, state="armed") == 1
    set_policy(db, ny_fallback_enabled=False)                      # user switches fallback off
    spy = SpyGenerator(monkeypatch, forbid=True)
    run_cycle(db, now + 6000, "LONG")
    assert spy.calls == 0                                          # nothing new is searched...
    assert count(db, models.PaperOrder, state="armed") == 1       # ...but the already-armed order is not auto-cancelled
    q = quota_of(db, now + 6000)
    assert q.quota_status == "ARMED_PENDING_FILL"
    fill_tick(db, now + 12_000, "LONG")
    assert count(db, models.PaperOrder, state="paper_open") == 1   # and the position is never closed by the toggle
    assert quota_of(db, now + 13_000).total_fills == 1


# =====================================================================================================
# Diagnostics: dedupe, restart, summary, taxonomy
# =====================================================================================================

def test_hundred_scans_are_one_unique_opportunity_without_spam(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    for i in range(100):
        run_cycle(db, now + 5 * i, "LONG", auto=False)           # same bar, same quote shape
    db.expire_all()
    diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert diag["scans"] == 100
    assert (diag["unique_candidates"], diag["financially_eligible"], diag["armed"], diag["filled"]) == (1, 1, 0, 0)
    assert diag["top_blockers"][0] == {"code": "AUTO_PAPER_OFF", "category": "EXECUTION",
                                       "message_vi": "Tự động đang tắt", "count": 100}
    row = db.query(models.NYOpportunityAudit).one()
    assert row.seen_count == 100 and row.revision == 2             # created + the first blocker; never grows per scan
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    assert ws.version == 1                                         # no rewrite per tick
    assert len([e for e in events_of(db, "setup.ready")
                if json.loads(e.payload).get("setup_id") == "watch-XAUUSDT-5M-fallback"]) == 1
    assert count(db, models.PaperOrder) == 0


def test_new_blocker_is_traced_as_revision_but_creates_no_order_or_candidate(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    run_cycle(db, now, "LONG", auto=False)
    make_news(db, now + 1000)
    run_cycle(db, now + 6000, "LONG", auto=True)
    row = db.query(models.NYOpportunityAudit).one()
    assert row.revision == 3 and row.primary_reason_code == "NEWS_BLACKOUT"
    assert [r["reasons"] for r in json.loads(row.revisions_json)][-1] == ["NEWS_BLACKOUT"]
    assert count(db, models.PaperOrder) == 0 and count(db, models.NYOpportunityAudit) == 1


def test_restart_keeps_counts_and_writes_one_summary(db):
    from sqlalchemy.orm import sessionmaker
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    for i in range(5):
        run_cycle(db, now + 5 * i, "LONG", auto=False)
    Session = sessionmaker(bind=db.get_bind())
    s2 = Session()                                                   # "restart": fresh session/process state
    try:
        for i in range(5, 8):
            run_cycle(s2, now + 5 * i, "LONG", auto=False)
        d = NYOpportunityAuditService.build_diagnostics(s2, session_instance_id="NY-2026-10-09")
        assert d["scans"] == 8 and d["unique_candidates"] == 1
        after = ny_ms(2026, 10, 9, 11, 5)
        run_cycle(s2, after, "LONG")
        first = s2.query(models.NYSessionAudit).one()
        written = first.summary_written_at
        summary = json.loads(first.summary_json)
        assert written == after and summary["missed_category"] == "SIGNAL_BLOCKED"
        assert summary["funnel"]["unique_candidates"] == 1 and summary["top_blockers"][0]["code"] == "AUTO_PAPER_OFF"
        assert first.primary_missed_reason == "AUTO_PAPER_OFF"
    finally:
        s2.close()
    s3 = Session()
    try:
        run_cycle(s3, ny_ms(2026, 10, 9, 12, 0), "LONG")             # another restart after the window
        again = s3.query(models.NYSessionAudit).one()
        assert again.summary_written_at == written and again.scan_count == 8     # no repeat, no extra scan
        q = TradingPolicyService.update_quota_lifecycle_state(s3, now_ms=after, clock=FakeClock(after))
        assert q.quota_status == "MISSED" and "có tín hiệu nhưng bị chặn" in q.last_status_reason
        assert "Hết cửa sổ" in q.last_status_reason and len(q.last_status_reason) <= 255
    finally:
        s3.close()


def test_missed_classification_never_calls_missing_data_a_lack_of_edge():
    cls = NYOpportunityAuditService.classify_missed
    empty = dict(unique_candidates=0, financially_eligible=0, armed=0, filled=0)
    assert cls(scans=0, data_issue_scans=0, funnel=empty, fills=0, top=[], last_system_error=None)["missed_category"] == "DATA_UNAVAILABLE"
    top_data = [dict(code="FALLBACK_DATA_MISSING", category="DATA", message_vi="x", count=9)]
    v = cls(scans=10, data_issue_scans=9, funnel=empty, fills=0, top=top_data, last_system_error=None)
    assert v == {"missed_category": "DATA_UNAVAILABLE", "primary_missed_reason": "FALLBACK_DATA_MISSING"}
    top_sig = [dict(code="FALLBACK_NO_FVG", category="STRATEGY", message_vi="x", count=9)]
    assert cls(scans=10, data_issue_scans=0, funnel=empty, fills=0, top=top_sig, last_system_error=None)["missed_category"] == "NO_SIGNAL"
    assert cls(scans=3, data_issue_scans=0, funnel=empty, fills=0, top=[], last_system_error="diag-1 boom")["missed_category"] == "SYSTEM"
    f1 = dict(unique_candidates=1, financially_eligible=1, armed=1, filled=0)
    assert cls(scans=3, data_issue_scans=0, funnel=f1, fills=0, top=[], last_system_error=None)["missed_category"] == "ARMED_NOT_FILLED"
    assert cls(scans=3, data_issue_scans=0, funnel=f1, fills=1, top=[], last_system_error=None)["missed_category"] == "FILLED"


def test_reason_taxonomy_separates_data_strategy_safety_execution_system():
    from services.ny_quota_helpers import classify_reason, primary_reason
    cats = {c: classify_reason(c)[0] for c in (
        "FALLBACK_DATA_MISSING", "STANDARD_DATA_MISSING", "FALLBACK_NO_FVG", "NET_RR_TOO_LOW", "NEWS_BLACKOUT",
        "LESSON_RULE_BLOCKED", "NO_REMAINING_RISK", "QUOTE_STALE", "EXECUTION_FEED_DEGRADED", "CALCULATOR_REJECTED",
        "SYSTEM_ERROR", "UPSERT_FAILED")}
    assert cats == {"FALLBACK_DATA_MISSING": "DATA", "STANDARD_DATA_MISSING": "DATA", "FALLBACK_NO_FVG": "STRATEGY",
                    "NET_RR_TOO_LOW": "STRATEGY", "NEWS_BLACKOUT": "SAFETY", "LESSON_RULE_BLOCKED": "SAFETY",
                    "NO_REMAINING_RISK": "SAFETY", "QUOTE_STALE": "EXECUTION", "EXECUTION_FEED_DEGRADED": "EXECUTION",
                    "CALCULATOR_REJECTED": "EXECUTION", "SYSTEM_ERROR": "SYSTEM", "UPSERT_FAILED": "SYSTEM"}
    assert primary_reason(["NET_RR_TOO_LOW", "NEWS_BLACKOUT", "SYSTEM_ERROR"]) == "SYSTEM_ERROR"
    assert primary_reason(["NET_RR_TOO_LOW", "NEWS_BLACKOUT"]) == "NEWS_BLACKOUT"
    assert primary_reason([]) is None
    assert classify_reason("SOMETHING_NEW")[0] == "SAFETY"      # unknown guard codes stay visible, never "no signal"


def test_standard_data_missing_is_recorded_and_fallback_still_works(db):
    """<20 15M candles must not silence the NY flow (previously an early return skipped everything)."""
    early = ny_ms(2026, 10, 9, 9, 0)
    run_cycle(db, early, "LONG")                         # no data at all, inside the standard window
    d = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert d["scans"] == 1 and d["top_blockers"][0]["code"] == "STANDARD_DATA_MISSING"
    assert d["top_blockers"][0]["category"] == "DATA" and d["unique_candidates"] == 0
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)                        # still no 15M data
    run_cycle(db, now, "LONG")
    assert count(db, models.PaperOrder, state="armed") == 1


def test_missing_five_minute_data_is_data_unavailable_not_no_signal(db):
    now = ny_ms(*T_1035)
    run_cycle(db, now, "LONG")                           # fallback window, zero 5M candles
    d = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert any(b["code"] == "FALLBACK_DATA_MISSING" and b["category"] == "DATA" for b in d["top_blockers"])
    after = ny_ms(2026, 10, 9, 11, 5)
    run_cycle(db, after, "LONG")
    row = db.query(models.NYSessionAudit).one()
    assert row.missed_category == "DATA_UNAVAILABLE"


def test_system_error_is_audited_with_diagnostic_id_and_never_reports_success(db, monkeypatch):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    from services import ny_quota_service as svc

    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(svc, "upsert_ny_fallback_setup", boom)
    run_cycle(db, now, "LONG")
    db.expire_all()
    assert count(db, models.PaperOrder) == 0
    assert count(db, models.WatchSetup, id="watch-XAUUSDT-5M-fallback") == 0
    a = db.query(models.NYSessionAudit).one()
    assert a.last_system_error and a.last_system_error.startswith("diag-") and "stage=upsert" in a.last_system_error
    assert "candidate=nyfb-NY-2026-10-09-" in a.last_system_error and "session=NY-2026-10-09" in a.last_system_error
    d = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert d["top_blockers"][0]["category"] == "SYSTEM" and d["last_system_error"]
    run_cycle(db, now + 6000, "LONG")                    # the loop survives and tries again next scan
    assert db.query(models.NYSessionAudit).one().scan_count == 2


# =====================================================================================================
# Calculator rejection branch + the SHORT 4198.29 photo must stay blocked
# =====================================================================================================

def test_short_photo_plan_is_blocked_by_the_guard_and_tp_is_not_moved(db):
    from datetime import datetime, timezone
    from services.quote_validator import QuoteValidator
    now = ny_ms(*T_1035)
    cand = _candidate(db, "SHORT", now)
    photo = dict(cand, planned_entry=4198.29, stop_loss=4203.29, targets=[dict(cand["targets"][0], price=4198.00)],
                 quantity=0.29, invalidation_price=4203.29)
    out = upsert_ny_fallback_setup(db, photo, _window(db, now), now)
    db.commit()
    row = out["row"]
    q = QuoteValidator.validate_ticker({"bid": 4198.29, "ask": 4198.5, "last": 4198.29, "timestamp": now - 200,
                                        "observed_at": now - 200}, now_ms=now)
    res = strategy_service._auto_arm_candidate(db, row, {"signal_id": out["binding"]["signal_id"]}, now,
                                               datetime.fromtimestamp(now / 1000, tz=timezone.utc),
                                               clock=FakeClock(now), quote=q, feed_health=OK_FEED)
    assert res["armed"] is False and res["codes"]
    db.expire_all()
    assert count(db, models.PaperOrder) == 0
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M-fallback").one()
    assert (ws.provisional_entry, ws.provisional_sl, ws.provisional_tp) == (4198.29, 4203.29, 4198.00)


def test_calculator_rejection_inside_the_cycle_is_audited_not_swallowed(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    audit = crud.get_or_create_today_audit(db, clock=FakeClock(now))
    audit.realized_pnl_today = -14.95                     # 0.05 USDT of budget left: min lot cannot fit
    db.commit()
    run_cycle(db, now, "LONG")
    d = NYOpportunityAuditService.build_diagnostics(db, session_instance_id="NY-2026-10-09")
    assert d["unique_candidates"] == 1 and d["financially_eligible"] == 0
    assert d["top_blockers"][0]["code"] == "CALCULATOR_REJECTED" and d["last_system_error"] is None
    assert count(db, models.PaperOrder) == 0


# =====================================================================================================
# Read-model endpoint, migration, events/outbox
# =====================================================================================================

def test_diagnostics_endpoint_is_get_only_typed_and_nullable_for_legacy(client, db):
    from sqlalchemy import func
    before = (count(db, models.PaperOrder), count(db, models.DomainEvent), count(db, models.NotificationOutbox),
              count(db, models.SessionQuota))
    r = client.get("/api/v1/ny-quota/diagnostics", params={"date": "2026-10-09"})
    assert r.status_code == 200
    body = r.json()
    assert body["availability"] == "UNAVAILABLE"
    assert body["scans"] is None and body["unique_candidates"] is None and body["filled"] is None   # never fake zeros
    assert body["session_instance_id"] == "NY-2026-10-09" and body["window"]["ny_window"]
    assert client.get("/api/v1/ny-quota/diagnostics", params={"date": "2099-01-01"}).json()["availability"] == "NOT_STARTED"
    assert client.get("/api/v1/ny-quota/diagnostics", params={"date": "09/10/2026"}).status_code == 422
    assert client.post("/api/v1/ny-quota/diagnostics").status_code in (404, 405)
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    seed_candles_15m(db, now)
    run_cycle(db, now, "LONG", auto=False)
    snap = (count(db, models.PaperOrder), count(db, models.DomainEvent), count(db, models.NotificationOutbox),
            count(db, models.SessionQuota))
    b2 = client.get("/api/v1/ny-quota/diagnostics", params={"date": "2026-10-09"}).json()
    assert b2["availability"] == "MEASURED" and b2["scans"] == 1 and b2["unique_candidates"] == 1
    assert b2["top_blockers"][0]["code"] == "AUTO_PAPER_OFF" and b2["top_blockers"][0]["category"] == "EXECUTION"
    assert b2["candidates"][0]["candidate_key"].startswith("nyfb-NY-2026-10-09-")
    assert (count(db, models.PaperOrder), count(db, models.DomainEvent), count(db, models.NotificationOutbox),
            count(db, models.SessionQuota)) == snap                      # GET wrote nothing
    assert before[0] == 0


def test_ny_session_endpoint_reports_same_flag_start_end_and_reason(client, db):
    set_policy(db, ny_fallback_enabled=False)
    body = client.get("/api/v1/policy/ny-session").json()
    assert body["fallback_enabled"] is False and body["fallback_start_ny"] == "10:30" and body["window_end_ny"] == "11:00"
    assert body["is_fallback_active"] is False and "target_fills" in body


def test_migration_adds_watch_session_column_idempotently_and_preserves_rows():
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from database import run_schema_migrations
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with eng.begin() as c:                                  # LEGACY schema (pre-V14.3) with a real row
        c.execute(text("""CREATE TABLE watch_setups (id VARCHAR(50) PRIMARY KEY, direction VARCHAR(10) NOT NULL,
            provisional_entry FLOAT NOT NULL, provisional_sl FLOAT NOT NULL, provisional_tp FLOAT NOT NULL,
            invalidation_price FLOAT NOT NULL, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)"""))
        c.execute(text("INSERT INTO watch_setups VALUES ('legacy','LONG',1,0.5,2,0.5,1,1)"))
    run_schema_migrations(eng)
    run_schema_migrations(eng)                              # twice: must be a no-op
    with eng.connect() as c:
        cols = {r[1] for r in c.execute(text("PRAGMA table_info(watch_setups)")).fetchall()}
        row = c.execute(text("SELECT id, direction, session_instance_id FROM watch_setups")).fetchone()
    assert "session_instance_id" in cols and "plan_kind" in cols
    assert tuple(row) == ("legacy", "LONG", None)            # old row preserved; provenance NOT invented
    # the NOT NULL invariant on the legacy-migrated table is untouched
    with eng.begin() as c:
        with pytest.raises(Exception):
            c.execute(text("INSERT INTO watch_setups (id,direction,provisional_entry,provisional_sl,provisional_tp,created_at,updated_at) VALUES ('x','LONG',1,1,1,1,1)"))


def test_fill_writes_one_trade_opened_event_and_outbox_row_without_telegram(db):
    now = ny_ms(*T_1035)
    seed_candles(db, "LONG", now)
    run_cycle(db, now, "LONG")
    fill_tick(db, now + 1000, "LONG")
    db.expire_all()
    opened = events_of(db, "trade.opened")
    assert len(opened) == 1
    armed = events_of(db, "order.armed")
    assert len(armed) == 1 and json.loads(armed[0].payload)["strategy_family"] == "NY_QUOTA_PAPER"
    # no direct send from generator/cycle: nothing but the outbox pipeline rows exist
    outbox = db.query(models.NotificationOutbox).all()
    assert all(o.provider_message_id is None and o.status != "SENT" for o in outbox)   # nothing sent inline
    assert len({o.dedupe_key for o in outbox}) == len(outbox)                          # one row per event


def test_session_quota_creation_race_returns_the_winner_row(db):
    """UI poll + strategy loop can create the same session quota concurrently: losing the race is not an error."""
    from sqlalchemy.exc import IntegrityError
    now = ny_ms(*T_1035)
    window = TradingPolicyService.get_ny_session_window(TradingPolicyService.get_or_create_policy(db), now_ms=now,
                                                        clock=FakeClock(now))
    real_commit = db.commit
    state = {"raced": False}

    def racing_commit():
        if not state["raced"]:
            state["raced"] = True
            db.rollback()                                    # our insert never lands...
            db.add(models.SessionQuota(                      # ...because "the other thread" won
                account_namespace="default", symbol="XAUUSDT", session_instance_id=window["session_instance_id"],
                session_date_utc="2026-10-09", session_date_vn=window["session_date_vn"],
                session_date_ny=window["session_date_ny"], window_start_ms=window["window_start_ms"],
                window_end_ms=window["window_end_ms"], fallback_start_ms=window["fallback_start_ms"],
                target_fills=1, standard_fills=0, fallback_fills=0, total_fills=0,
                quota_status="SEEKING_STANDARD", last_evaluated_at=now, created_at=now, updated_at=now))
            real_commit()
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed: session_quotas.session_instance_id"))
        return real_commit()

    db.commit = racing_commit
    try:
        q = TradingPolicyService.get_or_create_session_quota(db, now_ms=now, clock=FakeClock(now))
    finally:
        db.commit = real_commit
    assert state["raced"] is True
    assert q.session_instance_id == window["session_instance_id"]
    assert count(db, models.SessionQuota, session_instance_id=window["session_instance_id"]) == 1
