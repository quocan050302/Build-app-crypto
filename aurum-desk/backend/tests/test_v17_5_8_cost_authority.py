"""
backend/tests/test_v17_5_8_cost_authority.py
AURUM V17.5.8 — Financial Cost Authority & True Cost Parity Suite (Parts 041–050, 086).

Audits and proves:
1. Single authoritative financial resolution via build_verified_cost_context / resolve_execution_cost_context.
2. Pre-ARM vs Post-ARM authority: frozen order snapshot is authoritative post-ARM; global setting updates
   do NOT alter costs for existing orders.
3. Explicit 0.0 slippage is preserved across plan, snapshot, and execution (not replaced with defaults).
4. Strict validation: negative slippage, negative fee rates, NaN, and Infinity are rejected with fail-closed status.
5. Deterministic SystemConfig key precedence: specific keys (slippage_usd) override fallback keys (default_slippage).
6. assert_order_financial_parity detects discrepancies across candidate calc, comparison plan, snapshot, and fill.
"""

import math
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.plan_financial_context_service import (
    resolve_execution_cost_context,
    build_verified_cost_context,
    assert_order_financial_parity,
)


def test_p086_custom_slippage_and_fees_preserved():
    """Part 041 & 086: Custom slippage 0.27 and non-default fees preserved in cost context."""
    ctx = build_verified_cost_context(
        settings={
            "slippage_usd": 0.27,
            "maker_fee_rate": 0.0003,
            "taker_fee_rate": 0.0006,
        }
    )
    assert ctx["available"] is True
    assert ctx["entry_slippage_usd"] == 0.27
    assert ctx["sl_slippage_usd"] == 0.27
    assert ctx["maker_fee_rate"] == 0.0003
    assert ctx["taker_fee_rate"] == 0.0006
    assert ctx["provenance"] == "EFFECTIVE_SETTINGS_AUTHORITY"


def test_p086_explicit_zero_slippage_preserved():
    """Part 043 & 086: Explicit 0.0 slippage is preserved and NOT replaced by default 0.10."""
    ctx = build_verified_cost_context(
        settings={
            "slippage_usd": 0.0,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        }
    )
    assert ctx["available"] is True
    assert ctx["entry_slippage_usd"] == 0.0
    assert ctx["sl_slippage_usd"] == 0.0
    assert ctx["entry_slippage_usd"] is not None


def test_p086_negative_slippage_rejected():
    """Part 044 & 086: Negative slippage is strictly rejected with fail-closed unavailable status."""
    ctx = build_verified_cost_context(
        settings={
            "slippage_usd": -0.15,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        }
    )
    assert ctx["available"] is False
    assert ctx["reason"] in ("INVALID_CANDIDATE_SLIPPAGE", "INVALID_SLIPPAGE_VALUE")


def test_p086_negative_fee_rates_rejected():
    """Part 044 & 086: Negative fee rate is strictly rejected with fail-closed unavailable status."""
    ctx = build_verified_cost_context(
        settings={
            "slippage_usd": 0.10,
            "maker_fee_rate": -0.0002,
            "taker_fee_rate": 0.0004,
        }
    )
    assert ctx["available"] is False
    assert ctx["reason"] in ("INVALID_FEE_RATES", "INVALID_CONFIG_FEES")


def test_p086_nan_and_infinite_costs_rejected():
    """Part 044 & 086: NaN and Infinity are strictly rejected."""
    # NaN slippage
    ctx_nan = build_verified_cost_context(
        settings={"slippage_usd": float("nan"), "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004}
    )
    assert ctx_nan["available"] is False

    # Inf fee
    ctx_inf = build_verified_cost_context(
        settings={"slippage_usd": 0.10, "maker_fee_rate": float("inf"), "taker_fee_rate": 0.0004}
    )
    assert ctx_inf["available"] is False


def test_p086_snapshot_precedence_post_arm():
    """Part 042 & 086: Frozen order snapshot takes precedence over subsequent settings changes."""
    frozen_snapshot = {
        "slippage_usd": 0.15,
        "resolved_execution_costs": {
            "entry_slippage_usd": 0.15,
            "tp_slippage_usd": 0.0,
            "sl_slippage_usd": 0.15,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
            "provenance": "FROZEN_ORDER_SNAPSHOT",
            "version": 1,
        }
    }

    # Settings changed after ARM to 0.50
    subsequent_settings = {
        "slippage_usd": 0.50,
        "maker_fee_rate": 0.0010,
        "taker_fee_rate": 0.0020,
    }

    ctx = build_verified_cost_context(
        snapshot=frozen_snapshot,
        settings=subsequent_settings,
    )
    assert ctx["available"] is True
    # Must preserve frozen snapshot slippage (0.15), NOT new settings (0.50)
    assert ctx["entry_slippage_usd"] == 0.15
    assert ctx["sl_slippage_usd"] == 0.15
    assert ctx["provenance"] == "FROZEN_ORDER_SNAPSHOT"


def test_p086_deterministic_system_config_key_precedence():
    """Part 045: Specific 'slippage_usd' takes precedence over generic 'default_slippage'."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Add both keys into SystemConfig
    session.add(models.SystemConfig(key="default_slippage", value="0.50", updated_at=1000))
    session.add(models.SystemConfig(key="slippage_usd", value="0.25", updated_at=1000))
    session.commit()

    from services.scalp_watcher_orchestrator import get_system_config_cost_value

    # Retrieval of slippage must deterministically choose slippage_usd = 0.25
    val = get_system_config_cost_value(session, ["slippage_usd", "slippage", "default_slippage"])
    assert val == 0.25

    session.close()
    engine.dispose()


def test_p086_assert_order_financial_parity():
    """Part 049: assert_order_financial_parity reconciles snapshot, comparison plan, and fill result."""
    order_snap = {
        "slippage_usd": 0.20,
        "resolved_execution_costs": {
            "entry_slippage_usd": 0.20,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        }
    }
    matching_plan = {
        "cost_snapshot": {
            "entry_slippage_usd": 0.20,
        }
    }
    matching_fill = {
        "realized_slippage": 0.20,
    }

    res_parity = assert_order_financial_parity(
        order_snapshot=order_snap,
        comparison_plan=matching_plan,
        fill_result=matching_fill,
    )
    assert res_parity["is_parity"] is True
    assert len(res_parity["mismatches"]) == 0

    # Discrepant fill slippage
    mismatched_fill = {
        "realized_slippage": 0.35,
    }
    res_mismatch = assert_order_financial_parity(
        order_snapshot=order_snap,
        comparison_plan=matching_plan,
        fill_result=mismatched_fill,
    )
    assert res_mismatch["is_parity"] is False
    assert len(res_mismatch["mismatches"]) == 1
    assert "Fill slippage mismatch" in res_mismatch["mismatches"][0]
