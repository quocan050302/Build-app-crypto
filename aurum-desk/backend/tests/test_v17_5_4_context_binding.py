"""
backend/tests/test_v17_5_4_context_binding.py
AURUM V17.5.4 — Context Binding & Production Provenance Tests (Appendix A & Parts 011-060).
Verifies:
1. Pure resolver resolve_stop_comparison_execution_context produces VERIFIED context from production candidates.
2. Negative twin: missing expiry produces CONTEXT_INCOMPLETE without crashing.
3. Immutability & purity of bind_stop_comparison_execution_context.
4. Schema & JSON serialization roundtrip without loss of context fields.
5. Production caller ScalpWatcherOrchestrator.evaluate_cycle binds and persists verified context.
"""

import json
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import crud
from services.clock import ReplayClock
from services.cg01_types import ModelIdentifier
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.stop_placement_types import StopComparisonPlan, VariantPlan
from services.stop_placement_research_service import (
    resolve_stop_comparison_execution_context,
    bind_stop_comparison_execution_context,
    build_stop_comparison_plan,
)


def test_resolve_context_positive_verified():
    """Positive: valid candidate with intent and expiry resolves to VERIFIED context."""
    cand = SimpleNamespace(
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        candidate_key="cand_pos_cg01",
        direction="SHORT",
        planned_entry=2000.0,
        stop_loss=2010.0,
        take_profit=1980.0,
        valid_until_ms=1700000000000,
        entry_intent={"order_type": "LIMIT", "valid_until": 1700000000000},
        calculation=SimpleNamespace(slippage_usd=0.08),
    )

    policy_settings = {
        "capital_usdt": 5000.0,
        "risk_pct": 0.10,
        "config_version": 2,
    }

    ctx = resolve_stop_comparison_execution_context(
        cand=cand,
        policy_settings=policy_settings,
        decision_as_of_ms=1600000000000,
    )

    assert ctx["status"] == "VERIFIED"
    assert ctx["reason"] is None
    assert ctx["order_type"] == "LIMIT"
    assert ctx["entry_intent"] == "LIMIT"
    assert ctx["expires_at_ms"] == 1700000000000
    assert ctx["decision_cutoff_ms"] == 1600000000000
    assert ctx["cost_snapshot"]["entry_slippage_usd"] == 0.08
    assert ctx["cost_snapshot"]["version"] == 2
    assert "CG01" in ctx["provenance"]
    assert "cand_pos_cg01" in ctx["provenance"]


def test_resolve_context_negative_incomplete_missing_expiry():
    """Negative twin: CG-01 candidate missing expiry resolves to CONTEXT_INCOMPLETE."""
    cand = SimpleNamespace(
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        candidate_key="cand_no_exp",
        direction="LONG",
        planned_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        valid_until_ms=None,
        entry_intent={"order_type": "MARKET"},
    )

    ctx = resolve_stop_comparison_execution_context(
        cand=cand,
        policy_settings={"capital_usdt": 1000.0},
        decision_as_of_ms=1600000000000,
    )

    assert ctx["status"] == "CONTEXT_INCOMPLETE"
    assert ctx["reason"] == "MISSING_EXPIRY_OR_INTENT"
    assert ctx["expires_at_ms"] is None


def test_bind_context_immutability_and_types():
    """Purity: binding context returns updated dataclass copy without mutating original."""
    orig_plan = build_stop_comparison_plan(
        opportunity_key="opp_test_purity",
        model_id="M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="SHORT",
        planned_entry=2050.0,
        take_profit=2020.0,
        legacy_sl=2060.0,
        anchor=None,
        buffer_result=None,
        policy_settings={"capital_usdt": 1000.0},
        now_ms=1500000,
    )

    assert orig_plan.context_status == "CONTEXT_INCOMPLETE"
    assert orig_plan.expires_at_ms is None

    context = {
        "status": "VERIFIED",
        "reason": None,
        "order_type": "STOP",
        "entry_intent": "STOP",
        "decision_cutoff_ms": 1500000,
        "expires_at_ms": 1800000,
        "cost_snapshot": {"entry_slippage_usd": 0.15, "tp_slippage_usd": 0.0},
        "provenance": "M1_CONTINUATION:opp_test_purity:decision@1500000",
    }

    bound = bind_stop_comparison_execution_context(orig_plan, context)

    # Original plan remains untouched (immutable)
    assert orig_plan.context_status == "CONTEXT_INCOMPLETE"
    assert orig_plan.expires_at_ms is None

    # Bound copy has verified context
    assert bound.context_status == "VERIFIED"
    assert bound.order_type == "STOP"
    assert bound.expires_at_ms == 1800000
    assert bound.decision_cutoff_ms == 1500000
    assert bound.cost_snapshot["entry_slippage_usd"] == 0.15
    assert bound.provenance == "M1_CONTINUATION:opp_test_purity:decision@1500000"


def test_context_serialization_roundtrip():
    """Compatibility: serialization and deserialization retains all V17.5.4 context fields."""
    plan = build_stop_comparison_plan(
        opportunity_key="opp_roundtrip",
        model_id="SMC_M1_CONTINUATION",
        symbol="XAUUSDT",
        direction="LONG",
        planned_entry=2000.0,
        take_profit=2030.0,
        legacy_sl=1990.0,
        anchor=None,
        buffer_result=None,
        policy_settings={"capital_usdt": 1000.0},
        now_ms=1200000,
        entry_intent="LIMIT",
        order_type="LIMIT",
        decision_cutoff_ms=1200000,
        expires_at_ms=1500000,
        cost_snapshot={"entry_slippage_usd": 0.10, "maker_fee_rate": 0.0002},
        context_status="VERIFIED",
        provenance="SMC_M1_CONTINUATION:opp_roundtrip:decision@1200000",
    )

    d = plan.to_dict()
    assert d["context_status"] == "VERIFIED"
    assert d["order_type"] == "LIMIT"
    assert d["expires_at_ms"] == 1500000
    assert d["decision_cutoff_ms"] == 1200000
    assert d["provenance"] == "SMC_M1_CONTINUATION:opp_roundtrip:decision@1200000"

    # Json roundtrip
    serialized = json.dumps(d)
    deserialized = json.loads(serialized)
    assert deserialized["context_status"] == "VERIFIED"
    assert deserialized["cost_snapshot"]["entry_slippage_usd"] == 0.10


class CandleBar:
    def __init__(self, open_time, o, h, l, c):
        self.open_time = open_time
        self.timestamp = open_time
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.is_closed = True


def test_production_caller_evaluate_cycle_binds_context(tmp_path):
    """Production integration: ScalpWatcherOrchestrator.evaluate_cycle binds verified context to PaperObservation."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()

    # Seed core configuration
    db.add(models.SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1000000))
    db.add(models.SystemConfig(key="execution_scope", value="PAPER", updated_at=1000000))
    db.add(models.SystemConfig(key="stop_placement_v1.mode", value="SHADOW_COMPARE", updated_at=1000000))

    t_base = 100_000
    warmup_prices = [(2004 + (i % 3), 2007, 2003, 2005) for i in range(10)]
    pattern_prices = [
        (2005, 2007, 2004, 2006),
        (2006, 2009, 2005, 2008),
        (2008, 2010, 2007, 2009),
        (2009, 2011, 2008, 2010),
        (2010, 2010, 2006, 2007),
        (2007, 2008, 2003, 2004),
        (2004, 2005, 2001, 2002),
        (2002, 2006, 2002, 2005),
        (2005, 2008, 2004, 2007),
        (2007, 2007, 2003, 2004),
        (2004, 2005, 2002, 2003),
        (2003, 2004, 2001, 2002),
        (2002, 2003, 1998, 1999),
        (1999, 2001, 1998, 2000),
    ]
    prices = warmup_prices + pattern_prices
    candles = []
    for i, (o, h, l, c) in enumerate(prices):
        t_open = t_base + i * 60_000
        candles.append(CandleBar(t_open, o, h, l, c))

    now_ms = t_base + len(prices) * 60_000
    clock = ReplayClock(now_ms)
    audit = crud.get_or_create_today_audit(db, clock=clock)
    audit.initial_equity = 5000.0
    audit.current_equity = 5000.0
    db.commit()

    quote = {"bid": 2000.0, "ask": 2000.10, "last": 2000.05, "timestamp": now_ms, "is_valid": True, "is_stale": False}

    from services.scalp_m1_model_service import M1ScalpConfig
    cfg = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode="PAPER",
        pivot_left_bars=2,
        pivot_right_bars=2,
        displacement_atr_period=7,
        sl_buffer_ticks=10,
        min_net_rr=1.2,
    )

    orchestrator = ScalpWatcherOrchestrator()
    orchestrator.evaluate_cycle(
        db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        clock=clock,
        quote=quote,
        candles_1m=candles,
        htf_bias="BEARISH",
        h1_alignment="ALIGNED",
        config=cfg,
        auto_enabled=True,
    )

    # Verify persisted PaperObservation row written during candidate cycle
    obs = db.query(models.PaperObservation).filter_by(
        namespace="STOP_PLACEMENT_V1",
        event_kind="COMPARISON_PLAN",
    ).first()

    assert obs is not None, "PaperObservation must be written by evaluate_cycle"
    data = json.loads(obs.payload_json)

    # Prove that production evaluate_cycle bound the context before saving!
    assert data["context_status"] == "VERIFIED"
    assert data["order_type"] in ("MARKET", "LIMIT", "STOP")
    assert data["decision_cutoff_ms"] == now_ms
    assert data["expires_at_ms"] is not None
    assert data["expires_at_ms"] > now_ms
    assert data["cost_snapshot"] is not None
    assert data["cost_snapshot"]["entry_slippage_usd"] >= 0.0
    assert "decision@" in data["provenance"]
