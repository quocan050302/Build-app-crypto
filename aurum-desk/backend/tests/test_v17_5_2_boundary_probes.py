import json
import time
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, PaperObservation
from services.stop_placement_service import resolve_stop_anchor
from services.stop_placement_research_service import (
    advance_stop_comparison,
    persist_stop_comparison,
)
from services.execution_consumer import ExecutionConsumer
from services.quote_validator import CanonicalQuote


def test_probe_p1_caller_descriptor_shape_rejects_post_break_pivot():
    """
    P1 Probe: Caller in evaluate_m1_continuation_candidate passes
    selected_break={"id": "bos_240000", "bar": breaking_bar}.
    In HEAD, resolve_stop_anchor only reads broken_at_ms/break_time_ms/timestamp/break_open_ms
    at top-level of selected_break. When missing, leg_cutoff_ms is None and
    the post-break pivot at 420000 is incorrectly picked as the anchor.
    Must guarantee that any picked anchor occurred <= break bar time (240000) or fails closed.
    """
    class MockBar:
        def __init__(self, open_time, o, h, l, c):
            self.open_time = open_time
            self.timestamp = open_time
            self.open = o
            self.high = h
            self.low = l
            self.close = c

    # Break bar opened at 240000, closed at 300000
    break_bar = MockBar(240000, 100.5, 100.6, 99.8, 99.9)

    # Swings: pivot 1 before break (occurred 60000), pivot 2 after break (occurred 420000)
    origin_sh = {"id": "sh_origin_60000", "price": 101.0, "occurred_at_ms": 60000, "known_at_ms": 180000}
    post_break_sh = {"id": "sh_noise_420000", "price": 100.2, "occurred_at_ms": 420000, "known_at_ms": 540000}

    # Caller shape matches evaluate_m1_continuation_candidate: selected_break={"id": ..., "bar": ...}
    res = resolve_stop_anchor(
        model_id="SMC_M1_CONTINUATION",
        direction="SHORT",
        closed_bars=[break_bar],
        as_of_ms=600000,
        selected_break={"id": "bos_240000", "bar": break_bar},
        confirmed_swings=([origin_sh, post_break_sh], []),
    )

    # In HEAD, res.anchor picks sh_noise_420000 because leg_cutoff_ms was None!
    # Valid behavior: Anchor must either be None (fail-closed) or must have occurred <= 240000.
    if res.anchor is not None:
        assert res.anchor.occurred_at_ms <= 240000, (
            f"P1 FAILS AT HEAD: Picked post-break pivot occurred_at_ms={res.anchor.occurred_at_ms} "
            f"for break bar at 240000"
        )


def test_probe_p2_worker_durability(tmp_path, monkeypatch):
    """
    P2 Probe: Real ExecutionConsumer._process_quote_worker executes evaluate_quote_sync
    and closes SessionLocal() without committing advance_active_stop_comparisons.
    A fresh session in HEAD cannot see variant OPEN state.
    """
    db_file = str(tmp_path / "p2_worker.db")
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)

    # Monkeypatch SessionLocal in execution_consumer to our test factory
    monkeypatch.setattr("services.execution_consumer.SessionLocal", SessionFactory)

    # Seed comparison plan at plan_cutoff_ms = 10000
    plan = {
        "opportunity_key": "opp_probe_p2",
        "model_id": "SMC_M1_CONTINUATION",
        "symbol": "XAUUSDT",
        "direction": "SHORT",
        "planned_entry": 2000.0,
        "take_profit": 1980.0,
        "plan_cutoff_ms": 10000,
        "decision_cutoff_ms": 10000,
        "as_of_ms": 10000,
        "context_status": "VERIFIED",
        "cost_snapshot": {
            "entry_slippage_usd": 0.05,
            "tp_slippage_usd": 0.05,
            "sl_slippage_usd": 0.05,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
            "available": True,
        },
        "variant_a": {
            "is_valid": True,
            "can_execute": True,
            "meets_min_rr": True,
            "stop_loss": 2010.0,
            "execution_state": "WAIT_ENTRY",
        },
    }

    with SessionFactory() as init_db:
        persist_stop_comparison(init_db, plan, now_ms=10000)
        init_db.commit()

    # Valid incoming quote after plan cutoff (ts = 15000)
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2000.0,
        ask=2000.2,
        last=2000.0,
        exchange_ts_ms=15000,
        received_at_ms=15010,
    )

    consumer = ExecutionConsumer()
    clock = SimpleNamespace(now_ms=lambda: 15020)

    # Run actual worker (NO manual db.commit() in test!)
    res = consumer._process_quote_worker(quote, clock=clock)

    # Open completely NEW session and inspect persisted record
    with SessionFactory() as fresh_db:
        obs = fresh_db.query(PaperObservation).filter(
            PaperObservation.event_kind == "COMPARISON_PLAN"
        ).first()
        assert obs is not None, "Observation plan row missing"
        data = json.loads(obs.payload_json)
        state_a = data.get("variant_a", {}).get("execution_state")
        assert state_a == "OPEN", (
            f"P2 FAILS AT HEAD: Worker did not commit research durability. State is '{state_a}', expected 'OPEN'"
        )


def test_probe_p3_pre_plan_quote_rejected():
    """
    P3 Probe: advance_stop_comparison in HEAD does not check quote timestamp
    against plan cutoff / activation time.
    A quote with exchange_ts = 1000 and processing_time = 2000 must NOT open
    a plan with plan_cutoff = 5000.
    """
    state = {
        "direction": "SHORT",
        "planned_entry": 100.0,
        "take_profit": 90.0,
        "plan_cutoff_ms": 5000,
        "decision_cutoff_ms": 5000,
        "as_of_ms": 5000,
        "variant_a": {
            "is_valid": True,
            "can_execute": True,
            "meets_min_rr": True,
            "stop_loss": 105.0,
            "execution_state": "WAIT_ENTRY",
        },
    }

    quote = {
        "bid": 100.0,
        "ask": 100.1,
        "exchange_ts_ms": 1000,
        "timestamp": 1000,
    }

    # Feed quote before plan cutoff (quote_ts 1000 < plan 5000)
    advanced = advance_stop_comparison(state, quote, as_of_ms=2000)
    state_a = advanced.get("variant_a", {}).get("execution_state")
    assert state_a == "WAIT_ENTRY", (
        f"P3 FAILS AT HEAD: Pre-plan quote (ts=1000 < plan=5000) transitioned variant to '{state_a}'"
    )
    assert "opened_at_ms" not in advanced.get("variant_a", {})


def test_probe_p4_rejected_net_rr_cannot_open():
    """
    P4 Probe: advance_stop_comparison in HEAD only checks `can_execute`.
    When `meets_min_rr=False`, variant must NOT be opened or simulated.
    """
    state = {
        "direction": "SHORT",
        "planned_entry": 100.0,
        "take_profit": 90.0,
        "plan_cutoff_ms": 5000,
        "decision_cutoff_ms": 5000,
        "as_of_ms": 5000,
        "variant_c": {
            "is_valid": True,
            "can_execute": True,
            "meets_min_rr": False,  # Rejected by R:R Settings!
            "stop_loss": 108.0,
            "execution_state": "WAIT_ENTRY",
        },
    }

    quote = {
        "bid": 100.0,
        "ask": 100.1,
        "exchange_ts_ms": 6000,
        "timestamp": 6000,
    }

    advanced = advance_stop_comparison(state, quote, as_of_ms=6000)
    state_c = advanced.get("variant_c", {}).get("execution_state")
    assert state_c == "WAIT_ENTRY", (
        f"P4 FAILS AT HEAD: Variant C with meets_min_rr=False transitioned to '{state_c}'"
    )
