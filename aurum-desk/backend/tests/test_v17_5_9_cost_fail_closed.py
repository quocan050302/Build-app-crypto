"""
backend/tests/test_v17_5_9_cost_fail_closed.py
AURUM V17.5.9 — Cost Configuration Authority & Fail-Closed Suite (D04, Section 06 & Section 09 Part 075).

Cases:
- C01: Highest-priority setting valid
- C02: Highest-priority setting missing, valid fallback
- C03: Highest-priority setting invalid, valid fallback exists -> FAILS CLOSED (does not fall through!)
- C04: Negative slippage
- C05: NaN slippage
- C06: Infinite fee
- C07: Explicit zero slippage
- C08: Custom maker/taker fees
- C09: Frozen snapshot unaffected by configuration change
- C10: Invalid effective costs block real ARM
"""

import json
import math
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import crud
import models
from services.clock import FakeClock
from services.plan_financial_context_service import resolve_execution_cost_context
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import M1ScalpConfig, ScalpMode, WatchedZone, ZoneState
from services.scalp_watcher_orchestrator import (
    ScalpWatcherOrchestrator,
    resolve_ordered_cost_setting,
    get_system_config_cost_value,
)
from tests.test_v15_1_execution_lifecycle import (
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def cost_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session = SessionFactory()
    _setup_positive_trading_policy(session)

    session.add(models.SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1000000))
    session.add(models.SystemConfig(key="execution_scope", value="PAPER", updated_at=1000000))
    session.add(models.SystemConfig(key="stop_placement_v1.mode", value="PAPER_STRUCTURAL", updated_at=1000000))
    session.add(models.SystemConfig(key="stop_placement_v1.version", value="1", updated_at=1000000))

    clock = FakeClock(1_000_000)
    audit = crud.get_or_create_today_audit(session, clock=clock)
    audit.initial_equity = 5000.0
    audit.current_equity = 5000.0
    session.commit()

    yield session, SessionFactory

    session.close()
    engine.dispose()


def test_c01_highest_priority_setting_valid(cost_db):
    """C01: Highest-priority setting (slippage_usd) is valid and takes precedence over fallback."""
    session, _ = cost_db
    session.add(models.SystemConfig(key="slippage_usd", value="0.30", updated_at=1000000))
    session.add(models.SystemConfig(key="slippage", value="0.20", updated_at=1000000))
    session.add(models.SystemConfig(key="default_slippage", value="0.10", updated_at=1000000))
    session.commit()

    res = resolve_ordered_cost_setting(session, ["slippage_usd", "slippage", "default_slippage"], setting_name="slippage")
    assert res["status"] == "VALID"
    assert res["key"] == "slippage_usd"
    assert res["value"] == 0.30


def test_c02_highest_priority_missing_valid_fallback(cost_db):
    """C02: Highest-priority setting missing, lower-priority valid setting is used."""
    session, _ = cost_db
    session.add(models.SystemConfig(key="default_slippage", value="0.15", updated_at=1000000))
    session.commit()

    res = resolve_ordered_cost_setting(session, ["slippage_usd", "slippage", "default_slippage"], setting_name="slippage")
    assert res["status"] == "VALID"
    assert res["key"] == "default_slippage"
    assert res["value"] == 0.15


def test_c03_highest_priority_invalid_does_not_fall_through(cost_db):
    """C03: Part 047 (D04): Highest-priority key exists but is invalid -> FAILS CLOSED, does NOT fall back."""
    session, _ = cost_db
    session.add(models.SystemConfig(key="slippage_usd", value="INVALID_STRING", updated_at=1000000))
    session.add(models.SystemConfig(key="default_slippage", value="0.10", updated_at=1000000))
    session.commit()

    res = resolve_ordered_cost_setting(session, ["slippage_usd", "slippage", "default_slippage"], setting_name="slippage")
    assert res["status"] == "INVALID"
    assert res["key"] == "slippage_usd"
    assert res["value"] is None
    assert "INVALID_SLIPPAGE" in res["reason"]

    # get_system_config_cost_value compatibility adapter also returns None without falling back
    val = get_system_config_cost_value(session, ["slippage_usd", "slippage", "default_slippage"])
    assert val is None


def test_c04_negative_slippage_rejected(cost_db):
    """C04: Negative slippage setting rejected with INVALID_SLIPPAGE_NEGATIVE."""
    session, _ = cost_db
    session.add(models.SystemConfig(key="slippage_usd", value="-0.25", updated_at=1000000))
    session.commit()

    res = resolve_ordered_cost_setting(session, ["slippage_usd", "default_slippage"], setting_name="slippage")
    assert res["status"] == "INVALID"
    assert res["reason"] == "INVALID_SLIPPAGE_NEGATIVE"


def test_c05_nan_slippage_rejected(cost_db):
    """C05: NaN slippage setting rejected with INVALID_SLIPPAGE_NON_FINITE."""
    session, _ = cost_db
    session.add(models.SystemConfig(key="slippage_usd", value="NaN", updated_at=1000000))
    session.commit()

    res = resolve_ordered_cost_setting(session, ["slippage_usd", "default_slippage"], setting_name="slippage")
    assert res["status"] == "INVALID"
    assert res["reason"] == "INVALID_SLIPPAGE_NON_FINITE"


def test_c06_infinite_fee_rejected(cost_db):
    """C06: Infinite fee rate setting rejected with INVALID_MAKER_FEE_NON_FINITE."""
    session, _ = cost_db
    session.add(models.SystemConfig(key="maker_fee_rate", value="inf", updated_at=1000000))
    session.commit()

    res = resolve_ordered_cost_setting(session, ["maker_fee_rate", "default_maker_fee_rate"], setting_name="maker_fee")
    assert res["status"] == "INVALID"
    assert res["reason"] == "INVALID_MAKER_FEE_NON_FINITE"


def test_c07_explicit_zero_slippage_preserved(cost_db):
    """C07: Explicit zero slippage (0.0) is valid and preserved."""
    session, _ = cost_db
    session.add(models.SystemConfig(key="slippage_usd", value="0.0", updated_at=1000000))
    session.commit()

    res = resolve_ordered_cost_setting(session, ["slippage_usd", "default_slippage"], setting_name="slippage")
    assert res["status"] == "VALID"
    assert res["value"] == 0.0


def test_c08_custom_maker_taker_fees_valid(cost_db):
    """C08: Custom positive maker/taker fee rates resolved accurately."""
    session, _ = cost_db
    session.add(models.SystemConfig(key="maker_fee_rate", value="0.00015", updated_at=1000000))
    session.add(models.SystemConfig(key="taker_fee_rate", value="0.00035", updated_at=1000000))
    session.commit()

    m_res = resolve_ordered_cost_setting(session, ["maker_fee_rate"], setting_name="maker_fee")
    t_res = resolve_ordered_cost_setting(session, ["taker_fee_rate"], setting_name="taker_fee")
    assert m_res["status"] == "VALID"
    assert abs(m_res["value"] - 0.00015) < 1e-6
    assert t_res["status"] == "VALID"
    assert abs(t_res["value"] - 0.00035) < 1e-6


def test_c09_frozen_snapshot_unaffected_by_configuration_change():
    """C09: Post-ARM snapshot retains frozen costs even if live settings are updated."""
    frozen_snap = {
        "version": 1,
        "slippage_usd": 0.20,
        "maker_fee_rate": 0.0002,
        "taker_fee_rate": 0.0004,
        "resolved_execution_costs": {
            "entry_slippage_usd": 0.20,
            "tp_slippage_usd": 0.0,
            "sl_slippage_usd": 0.20,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        },
    }

    # resolve_execution_cost_context with snapshot ignores modified settings
    modified_settings = {"slippage_usd": 0.99, "maker_fee_rate": 0.005}
    ctx = resolve_execution_cost_context(snapshot=frozen_snap, settings=modified_settings)
    assert ctx["available"] is True
    assert ctx["entry_slippage_usd"] == 0.20
    assert ctx["maker_fee_rate"] == 0.0002
    assert ctx["provenance"] == "FROZEN_ORDER_SNAPSHOT"


def test_c10_invalid_effective_costs_block_real_arm(cost_db):
    """C10: When authoritative costs in DB are corrupt, real ARM pipeline fails closed with zero persisted orders."""
    session, _ = cost_db
    now_ms = 1_300_000
    clock = FakeClock(now_ms)
    orch = ScalpWatcherOrchestrator()

    # Corrupt taker_fee_rate with negative value
    session.add(models.SystemConfig(key="taker_fee_rate", value="-0.0004", updated_at=now_ms))
    session.commit()
    session.commit()

    zone = WatchedZone(
        zone_id="z_c10", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2647.50, ask=2648.00, last=2647.75,
        status="VALID", exchange_ts_ms=now_ms, received_at_ms=now_ms,
    )

    base_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
        min_net_rr=1.5,
    )

    res = orch.evaluate_cycle(
        session, "XAUUSDT",
        now_ms=now_ms, clock=clock, quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res.get("armed_order_id") is None
    assert session.query(models.PaperOrder).count() == 0
