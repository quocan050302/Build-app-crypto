"""
backend/tests/test_v17_5_3_boundary_probes.py
AURUM V17.5.3 — Boundary Probes R1–R7 with Positive Twins
Reproduces and verifies:
R1: Quote missing source timestamp cannot open variant.
R2: Quote with future source timestamp relative to processing time cannot open variant.
R3: Expired plan (expires_at_ms) cannot open variant.
R4: Intent-aware entry (SHORT LIMIT at 100, bid 95) cannot fill at planned price; requires executable bid >= limit.
R5: Future break cannot bind anchor; helper rejects break when break time/known time > decision_cutoff_ms.
R6: Durable error returns structured ERROR without NameError, rolls back and closes session.
R7: Actual worker must not research fill stale backlog when quote freshness is violated.
"""

from types import SimpleNamespace
import pytest
from services.stop_placement_research_service import advance_stop_comparison
from services.stop_placement_service import build_selected_break_descriptor, resolve_bound_stop_leg


def plan(**kw):
    p = {
        "symbol": "XAUUSDT",
        "direction": "SHORT",
        "planned_entry": 100.0,
        "take_profit": 90.0,
        "as_of_ms": 5000,
        "plan_cutoff_ms": 5000,
        "decision_cutoff_ms": 5000,
        "entry_intent": "LIMIT",
        "order_type": "LIMIT",
        "expires_at_ms": 7000,
        "context_status": "VERIFIED",
        "cost_snapshot": {
            "entry_slippage_usd": 0.05,
            "tp_slippage_usd": 0.05,
            "sl_slippage_usd": 0.05,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
            "available": True,
        },
        "variant_c": {
            "is_valid": True,
            "can_execute": True,
            "meets_min_rr": True,
            "stop_loss": 105.0,
            "quantity": 1.0,
            "execution_state": "WAIT_ENTRY",
        },
    }
    p.update(kw)
    return p


# ---------------------------------------------------------------------------
# R1 Probe & Positive Twin: Quote missing source timestamp
# ---------------------------------------------------------------------------

def test_r1_missing_quote_time_cannot_open():
    """R1: Missing quote timestamp must NOT open variant."""
    s = advance_stop_comparison(plan(), {"symbol": "XAUUSDT", "bid": 100, "ask": 100.1}, as_of_ms=6000)
    assert s["variant_c"]["execution_state"] == "WAIT_ENTRY", s


def test_r1_positive_twin_valid_quote_time_opens():
    """Positive twin: Quote with valid source timestamp opens matching variant."""
    s = advance_stop_comparison(
        plan(entry_intent="MARKET", order_type="MARKET"),
        {"symbol": "XAUUSDT", "bid": 100, "ask": 100.1, "exchange_ts_ms": 6000, "received_at_ms": 6000},
        as_of_ms=6000,
    )
    assert s["variant_c"]["execution_state"] == "OPEN", s
    assert s["variant_c"].get("actual_entry") is not None


# ---------------------------------------------------------------------------
# R2 Probe & Positive Twin: Quote future timestamp
# ---------------------------------------------------------------------------

def test_r2_future_quote_cannot_open():
    """R2: Quote timestamp far in future relative to processing clock must NOT open."""
    s = advance_stop_comparison(
        plan(),
        {"symbol": "XAUUSDT", "bid": 100, "ask": 100.1, "exchange_ts_ms": 1000000000, "received_at_ms": 6000},
        as_of_ms=6000,
    )
    assert s["variant_c"]["execution_state"] == "WAIT_ENTRY", s


def test_r2_positive_twin_synchronous_quote_opens():
    """Positive twin: Synchronous quote timestamp within tolerance opens matching variant."""
    s = advance_stop_comparison(
        plan(entry_intent="MARKET", order_type="MARKET"),
        {"symbol": "XAUUSDT", "bid": 100, "ask": 100.1, "exchange_ts_ms": 6000, "received_at_ms": 6000},
        as_of_ms=6005,
    )
    assert s["variant_c"]["execution_state"] == "OPEN", s


# ---------------------------------------------------------------------------
# R3 Probe & Positive Twin: Expired plan
# ---------------------------------------------------------------------------

def test_r3_expired_plan_cannot_open():
    """R3: Plan with expires_at_ms in the past must NOT open (remains WAIT_ENTRY or transitions to EXPIRED)."""
    s = advance_stop_comparison(
        plan(),
        {"symbol": "XAUUSDT", "bid": 100, "ask": 100.1, "exchange_ts_ms": 8000, "received_at_ms": 8000},
        as_of_ms=8000,
    )
    assert s["variant_c"]["execution_state"] != "OPEN", s


def test_r3_positive_twin_unexpired_plan_opens():
    """Positive twin: Plan before expires_at_ms opens when market matches."""
    s = advance_stop_comparison(
        plan(entry_intent="MARKET", order_type="MARKET", expires_at_ms=10000),
        {"symbol": "XAUUSDT", "bid": 100, "ask": 100.1, "exchange_ts_ms": 6500, "received_at_ms": 6500},
        as_of_ms=6500,
    )
    assert s["variant_c"]["execution_state"] == "OPEN", s


# ---------------------------------------------------------------------------
# R4 Probe & Positive Twin: Intent-aware Limit Entry
# ---------------------------------------------------------------------------

def test_r4_short_limit_away_from_entry_cannot_fill_at_planned_price():
    """R4: SHORT sell limit 100; executable bid 95 has not reached limit, must remain WAIT_ENTRY."""
    s = advance_stop_comparison(
        plan(entry_intent="LIMIT", planned_entry=100.0),
        {"symbol": "XAUUSDT", "bid": 95, "ask": 95.1, "exchange_ts_ms": 6000, "received_at_ms": 6000},
        as_of_ms=6000,
    )
    assert s["variant_c"]["execution_state"] == "WAIT_ENTRY", s
    assert "actual_entry" not in s["variant_c"]


def test_r4_positive_twin_short_limit_reached_fills():
    """Positive twin: SHORT sell limit 100; bid reaches 100.0, fills with actual_entry >= 100.0."""
    s = advance_stop_comparison(
        plan(entry_intent="LIMIT", planned_entry=100.0),
        {"symbol": "XAUUSDT", "bid": 100.0, "ask": 100.1, "exchange_ts_ms": 6000, "received_at_ms": 6000},
        as_of_ms=6000,
    )
    assert s["variant_c"]["execution_state"] == "OPEN", s
    assert s["variant_c"].get("actual_entry") == 100.0


# ---------------------------------------------------------------------------
# R5 Probe & Positive Twin: Break descriptor / bound leg causal cutoff
# ---------------------------------------------------------------------------

def test_r5_future_break_cannot_bind_anchor():
    """R5: Break occurring or known after decision_cutoff_ms must not bind anchor."""
    origin = SimpleNamespace(open_time=60000, timestamp=60000, open=100, high=101, low=99, close=100)
    b = SimpleNamespace(open_time=720000, timestamp=720000, open=100, high=100, low=98, close=98.5)
    d = build_selected_break_descriptor(
        break_bar=b, broken_reference={"id": "ref", "price": 99}, decision_cutoff_ms=600000, direction="SHORT"
    )
    got = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        closed_bars=[origin, b],
        selected_break=d,
        candidate_pivots=[{"id": "origin", "price": 101, "occurred_at_ms": 60000, "known_at_ms": 180000}],
        decision_cutoff_ms=600000,
    )
    assert got is None, got


def test_r5_positive_twin_causal_break_binds_anchor():
    """Positive twin: Break strictly closed at or before cutoff binds valid structural leg."""
    origin = SimpleNamespace(open_time=60000, timestamp=60000, open=100, high=101, low=99, close=100)
    b = SimpleNamespace(open_time=240000, timestamp=240000, open=100, high=100, low=98, close=98.5)
    d = build_selected_break_descriptor(
        break_bar=b, broken_reference={"id": "ref", "price": 99}, decision_cutoff_ms=600000, direction="SHORT"
    )
    got = resolve_bound_stop_leg(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        closed_bars=[origin, b],
        selected_break=d,
        candidate_pivots=[{"id": "origin", "price": 101, "occurred_at_ms": 60000, "known_at_ms": 180000}],
        decision_cutoff_ms=600000,
    )
    assert got is not None, "Valid causal break should resolve bound stop leg"
    assert got.origin_price == 101.0


# ---------------------------------------------------------------------------
# R6 Probe & Positive Twin: Durable error handling without NameError
# ---------------------------------------------------------------------------

def test_r6_durable_error_returns_error_without_nameerror(monkeypatch):
    """R6: advance_stop_comparisons_durable must handle exceptions cleanly without NameError."""
    import services.stop_placement_research_service as mod

    class BrokenSession:
        def __init__(self):
            self.rolled_back = False
            self.closed = False

        def commit(self):
            pass

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    s = BrokenSession()

    def broken(*a, **kw):
        raise RuntimeError("injected research DB failure")

    monkeypatch.setattr(mod, "advance_active_stop_comparisons", broken)
    out = mod.advance_stop_comparisons_durable(session_factory=lambda: s, quote={}, as_of_ms=6000)
    assert out["status"] == "ERROR"
    assert s.rolled_back and s.closed


def test_r6_positive_twin_durable_success(monkeypatch):
    """Positive twin: Normal durable execution commits changes and returns COMMITTED status."""
    import services.stop_placement_research_service as mod

    class GoodSession:
        def __init__(self):
            self.committed = False
            self.closed = False

        def commit(self):
            self.committed = True

        def rollback(self):
            pass

        def close(self):
            self.closed = True

    s = GoodSession()
    monkeypatch.setattr(mod, "advance_active_stop_comparisons", lambda *a, **kw: 1)
    out = mod.advance_stop_comparisons_durable(session_factory=lambda: s, quote={"bid": 100}, as_of_ms=6000)
    assert out["status"] == "COMMITTED"
    assert out["changed"] == 1
    assert s.committed and s.closed


# ---------------------------------------------------------------------------
# R7 Probe & Positive Twin: Actual worker backlog / freshness handling
# ---------------------------------------------------------------------------

def test_r7_actual_worker_must_not_research_fill_stale_backlog(tmp_path, monkeypatch):
    """R7: Worker must not research-fill variants when incoming quote is stale backlog."""
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base, PaperObservation
    import services.execution_consumer as consumer_mod
    from services.quote_validator import CanonicalQuote
    from services.clock import ReplayClock
    from services.stop_placement_research_service import persist_stop_comparison

    eng = create_engine("sqlite:///" + str(tmp_path / "worker.db"))
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, autoflush=False)
    monkeypatch.setattr(consumer_mod, "SessionLocal", factory)

    p = plan(opportunity_key="stale_worker", entry_intent="MARKET", order_type="MARKET", expires_at_ms=200000)
    with factory() as db:
        persist_stop_comparison(db, p, now_ms=5000)
        db.commit()

    # Quote is from ts=6000, but worker clock is at ts=100000 (age 94000ms > 15000ms threshold)
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=100,
        ask=100.1,
        last=100.05,
        exchange_ts_ms=6000,
        received_at_ms=6000,
        status="VALID",
    )
    r = consumer_mod.ExecutionConsumer()._process_quote_worker(quote, clock=ReplayClock(100000))
    assert r["is_stale"] is True, r

    with factory() as db:
        saved = json.loads(
            db.query(PaperObservation).filter_by(event_kind="COMPARISON_PLAN").one().payload_json
        )
        assert saved["variant_c"]["execution_state"] == "WAIT_ENTRY", saved


def test_r7_positive_twin_actual_worker_fills_fresh_quote(tmp_path, monkeypatch):
    """Positive twin: Worker research-fills variants when incoming quote is fresh."""
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base, PaperObservation
    import services.execution_consumer as consumer_mod
    from services.quote_validator import CanonicalQuote
    from services.clock import ReplayClock
    from services.stop_placement_research_service import persist_stop_comparison

    eng = create_engine("sqlite:///" + str(tmp_path / "worker_fresh.db"))
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, autoflush=False)
    monkeypatch.setattr(consumer_mod, "SessionLocal", factory)

    p = plan(opportunity_key="fresh_worker", entry_intent="MARKET", order_type="MARKET", expires_at_ms=200000)
    with factory() as db:
        persist_stop_comparison(db, p, now_ms=5000)
        db.commit()

    # Fresh quote: exchange_ts_ms=98000, clock=100000 (age 2000ms <= 15000ms)
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=100,
        ask=100.1,
        last=100.05,
        exchange_ts_ms=98000,
        received_at_ms=98000,
        status="VALID",
    )
    r = consumer_mod.ExecutionConsumer()._process_quote_worker(quote, clock=ReplayClock(100000))
    assert not r.get("is_stale"), r

    with factory() as db:
        saved = json.loads(
            db.query(PaperObservation).filter_by(event_kind="COMPARISON_PLAN").one().payload_json
        )
        assert saved["variant_c"]["execution_state"] == "OPEN", saved
