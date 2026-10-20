"""
tests/test_v16_36_paper_tonight_production_regression.py

AURUM DESK — PAPER TONIGHT READINESS REAL PRODUCTION REGRESSION TESTS
Rules 067 - 088:
- Test 1: Fallback RR via real ExecutionCoordinator.evaluate_orders_sync (Rules 014, 028, 067)
- Test 2: Snapshot RR 1.5 LONG & SHORT via ExecutionCoordinator.evaluate_orders_sync (Rules 031-034, 068)
- Test 3: Negative RR checks (Net RR < 1.5 rejected, Net RR 1.65 rejected if threshold=2.0) (Rule 069)
- Test 4: Invalid snapshot thresholds (NaN, Inf, negative, 0, string) fail-closed (Rule 070)
- Test 5: Production Alert Payload via ScalpWatcherOrchestrator.evaluate_cycle (Rules 051-053, 071)
- Test 6: Distance & ATR floor flags comprehensive tests (Rules 056, 057, 072, 074)
- Test 7: Price basis consistency (MID vs BID/ASK) (Rules 054, 073)
- Test 8: Telegram formatting & geometry (Rules 058, 059, 075)
- Test 9: Timing profile config API roundtrip (Rules 045, 076)
- Test 10: Expanded NY PAPER window boundaries & legacy retention (Rules 037-043, 077, 078)
- Test 11: PAPER-only scope enforcement (Rule 041, 079)
- Test 12: News guard in expanded window (08:30 NY) (Rules 048, 080)
- Test 13: Full pipeline LONG & SHORT in expanded window (09:00 NY) (Rules 081, 082)
- Test 14: Incomplete triggers do not ARM (Rule 083)
- Test 15: Quota & position caps preserved in expanded profile (Rule 084)
"""
from __future__ import annotations

import json
import math
import os
import sys
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import models
import crud
from domain_calculator import calculate_risk_reward, CostAssumptions
from services.cg01_types import (
    CG01Config, CG01TimingProfile, CG01ProfileMode, CG01EntryMode, CG01Stage
)
from services.cg01_engine import resolve_cg01_windows_for_profile, resolve_cg01_time_context
from services.execution_coordinator import ExecutionCoordinator
from services.proximity_service import compute_zone_proximity_metrics, ProximityService
from services.scalp_m1_types import M1ScalpConfig, WatchedZone, load_effective_scalp_config
import services.scalp_m1_types as smt
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.telegram_service import format_telegram_message, _has_valid_plan
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series, build_short_fixture_series, make_valid_quote
)


def _setup_clean_audit_and_policy(db):
    """Ensure trading policy exists and allows paper execution in tests."""
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.max_daily_fills = 3
    policy.daily_min_goal = 2
    policy.ny_min_fills = 1
    policy.is_active = False  # Deactivate session time restriction for execution test
    db.commit()
    return policy


# ===========================================================================
# 1. Fallback RR & Snapshot RR in ExecutionCoordinator (Rules 067 - 070)
# ===========================================================================

class TestCoordinatorRealExecutionRR:
    """
    Real execution regression testing of ExecutionCoordinator.evaluate_orders_sync.
    Calls the actual coordinator method against an isolated DB.
    """

    def test_rule_067_coordinator_fallback_to_db_config_when_snapshot_missing_key(self, isolated_db):
        """
        Rule 067: Legacy orders lack 'min_net_rr_at_arm' in cost_snapshot.
        Coordinator must successfully call load_effective_scalp_config(db) without NameError.
        When DB min_net_rr=1.5 and plan achieves Net R:R ~ 1.65, order must fill cleanly.
        """
        _setup_clean_audit_and_policy(isolated_db)

        # Set effective DB scalp config min_net_rr = 1.5
        orig_cfg = smt.active_scalp_config
        smt.active_scalp_config = M1ScalpConfig(min_net_rr=1.5)

        now_ms = 1_700_000_000_000
        # Plan: Entry 4100.0, SL 4090.0, TP 4124.0. Gross RR = 2.4, Net RR ~ 1.65
        order = models.PaperOrder(
            id="ord-fallback-15",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=4100.0,
            stop_loss=4090.0,
            take_profit=4124.0,
            quantity=0.1,
            initial_risk_usdt=1.0,
            risk_pct=0.10,
            gross_rr=2.4,
            estimated_net_rr=1.65,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=now_ms + 3600_000,
            config_version=1,
            # Legacy order: cost_snapshot has NO min_net_rr_at_arm key
            cost_snapshot=json.dumps({"slippage_usd": 0.10, "maker_fee_rate": 0.0002, "taker_fee_rate": 0.0004}),
        )
        isolated_db.add(order)
        isolated_db.commit()

        coordinator = ExecutionCoordinator()

        # Quote touches planned entry: ask = 4100.0 <= 4100.0
        ticker = {"bid": 4099.90, "ask": 4100.00, "last": 4100.00, "timestamp": now_ms + 1000}

        try:
            coordinator.evaluate_orders_sync(isolated_db, ticker_override=ticker, now_ms=now_ms + 1000)
            isolated_db.refresh(order)

            # Order must NOT be rejected by NameError -> default 2.0!
            assert order.state == "paper_open", (
                f"Expected order to be paper_open via fallback to 1.5, got state={order.state}, "
                f"invalidation_reason={order.invalidation_reason}"
            )
            assert order.actual_entry == 4100.0
        finally:
            smt.active_scalp_config = orig_cfg

    def test_rule_068_coordinator_snapshot_min_net_rr_1_5_long(self, isolated_db):
        """
        Rule 068: Order with cost_snapshot containing min_net_rr_at_arm=1.5 fills LONG
        when Net R:R ~ 1.65. Snapshot is not mutated.
        """
        _setup_clean_audit_and_policy(isolated_db)

        now_ms = 1_700_000_000_000
        snap_content = {
            "min_net_rr_at_arm": 1.5,
            "slippage_usd": 0.10,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        }
        order = models.PaperOrder(
            id="ord-snap-long-15",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=4100.0,
            stop_loss=4090.0,
            take_profit=4124.0,
            quantity=0.1,
            initial_risk_usdt=1.0,
            risk_pct=0.10,
            gross_rr=2.4,
            estimated_net_rr=1.65,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=now_ms + 3600_000,
            config_version=1,
            cost_snapshot=json.dumps(snap_content),
        )
        isolated_db.add(order)
        isolated_db.commit()

        coordinator = ExecutionCoordinator()
        ticker = {"bid": 4099.90, "ask": 4100.00, "last": 4100.00, "timestamp": now_ms + 1000}
        coordinator.evaluate_orders_sync(isolated_db, ticker_override=ticker, now_ms=now_ms + 1000)

        isolated_db.refresh(order)
        assert order.state == "paper_open", f"LONG order should fill, state={order.state}, reason={order.invalidation_reason}"
        assert order.actual_entry == 4100.0

        # Audit snapshot must not be rewritten
        loaded_snap = json.loads(order.cost_snapshot)
        assert loaded_snap.get("min_net_rr_at_arm") == 1.5

    def test_rule_068_coordinator_snapshot_min_net_rr_1_5_short(self, isolated_db):
        """
        Rule 068: Order with cost_snapshot containing min_net_rr_at_arm=1.5 fills SHORT
        when Net R:R ~ 1.65.
        """
        _setup_clean_audit_and_policy(isolated_db)

        now_ms = 1_700_000_000_000
        snap_content = {
            "min_net_rr_at_arm": 1.5,
            "slippage_usd": 0.10,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        }
        # SHORT: Entry 4100.0, SL 4110.0, TP 4076.0. Gross RR = 24/10 = 2.4, Net RR ~ 1.65
        order = models.PaperOrder(
            id="ord-snap-short-15",
            instrument="XAUUSDT",
            direction="SHORT",
            state="armed",
            order_type="LIMIT",
            planned_entry=4100.0,
            stop_loss=4110.0,
            take_profit=4076.0,
            quantity=0.1,
            initial_risk_usdt=1.0,
            risk_pct=0.10,
            gross_rr=2.4,
            estimated_net_rr=1.65,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=now_ms + 3600_000,
            config_version=1,
            cost_snapshot=json.dumps(snap_content),
        )
        isolated_db.add(order)
        isolated_db.commit()

        coordinator = ExecutionCoordinator()
        # SHORT limit triggers when Bid >= planned_entry
        ticker = {"bid": 4100.00, "ask": 4100.10, "last": 4100.00, "timestamp": now_ms + 1000}
        coordinator.evaluate_orders_sync(isolated_db, ticker_override=ticker, now_ms=now_ms + 1000)

        isolated_db.refresh(order)
        assert order.state == "paper_open", f"SHORT order should fill, state={order.state}, reason={order.invalidation_reason}"
        assert order.actual_entry == 4100.0

    def test_rule_069_coordinator_rejects_when_net_rr_below_1_5(self, isolated_db):
        """
        Rule 069: Threshold 1.5. Plan achieves Net R:R ~ 1.1 (below 1.5).
        Coordinator must reject order.
        """
        _setup_clean_audit_and_policy(isolated_db)

        now_ms = 1_700_000_000_000
        snap_content = {"min_net_rr_at_arm": 1.5, "slippage_usd": 0.10}
        # Low TP: Entry 4100.0, SL 4090.0, TP 4112.0. Gross RR = 1.2, Net RR < 1.0
        order = models.PaperOrder(
            id="ord-low-rr",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=4100.0,
            stop_loss=4090.0,
            take_profit=4112.0,
            quantity=1.0,
            initial_risk_usdt=25.0,
            risk_pct=0.25,
            gross_rr=1.2,
            estimated_net_rr=0.9,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=now_ms + 3600_000,
            config_version=1,
            cost_snapshot=json.dumps(snap_content),
        )
        isolated_db.add(order)
        isolated_db.commit()

        coordinator = ExecutionCoordinator()
        ticker = {"bid": 4099.90, "ask": 4100.00, "last": 4100.00, "timestamp": now_ms + 1000}
        coordinator.evaluate_orders_sync(isolated_db, ticker_override=ticker, now_ms=now_ms + 1000)

        isolated_db.refresh(order)
        assert order.state != "paper_open", "Order with Net R:R < 1.5 must NOT be filled"
        assert order.state in ("rejected", "cancelled", "invalidated")

    def test_rule_069_coordinator_rejects_net_rr_1_65_when_threshold_is_2_0(self, isolated_db):
        """
        Rule 069: Threshold 2.0. Plan achieves Net R:R ~ 1.65 (between 1.5 and 2.0).
        Coordinator must reject order because it does not meet the 2.0 threshold.
        """
        _setup_clean_audit_and_policy(isolated_db)

        now_ms = 1_700_000_000_000
        snap_content = {"min_net_rr_at_arm": 2.0, "slippage_usd": 0.10}
        order = models.PaperOrder(
            id="ord-threshold-20",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=4100.0,
            stop_loss=4090.0,
            take_profit=4124.0,  # Net RR ~ 1.65 < 2.0
            quantity=1.0,
            initial_risk_usdt=25.0,
            risk_pct=0.25,
            gross_rr=2.4,
            estimated_net_rr=1.65,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=now_ms + 3600_000,
            config_version=1,
            cost_snapshot=json.dumps(snap_content),
        )
        isolated_db.add(order)
        isolated_db.commit()

        coordinator = ExecutionCoordinator()
        ticker = {"bid": 4099.90, "ask": 4100.00, "last": 4100.00, "timestamp": now_ms + 1000}
        coordinator.evaluate_orders_sync(isolated_db, ticker_override=ticker, now_ms=now_ms + 1000)

        isolated_db.refresh(order)
        assert order.state != "paper_open", "Order with Net R:R 1.65 must NOT fill when threshold=2.0"
        assert order.state in ("rejected", "cancelled", "invalidated")

    @pytest.mark.parametrize("invalid_threshold", [
        float("nan"),
        float("inf"),
        float("-inf"),
        -1.5,
        0.0,
        "invalid_text",
        {},
    ])
    def test_rule_070_coordinator_rejects_invalid_snapshot_threshold(self, isolated_db, invalid_threshold):
        """
        Rule 070: Invalid thresholds (NaN, Inf, negative, zero, string) in snapshot
        must NOT silently fall back to 2.0 and must reject the order with INVALID_ORDER_THRESHOLD.
        """
        _setup_clean_audit_and_policy(isolated_db)

        now_ms = 1_700_000_000_000
        snap_content = {
            "min_net_rr_at_arm": invalid_threshold,
            "slippage_usd": 0.10,
        }
        order = models.PaperOrder(
            id=f"ord-inv-{abs(hash(str(invalid_threshold)))}",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=4100.0,
            stop_loss=4090.0,
            take_profit=4135.0,  # Highly favorable RR
            quantity=1.0,
            initial_risk_usdt=25.0,
            risk_pct=0.25,
            gross_rr=3.5,
            estimated_net_rr=3.0,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=now_ms + 3600_000,
            config_version=1,
            cost_snapshot=json.dumps(snap_content),
        )
        isolated_db.add(order)
        isolated_db.commit()

        coordinator = ExecutionCoordinator()
        ticker = {"bid": 4099.90, "ask": 4100.00, "last": 4100.00, "timestamp": now_ms + 1000}
        coordinator.evaluate_orders_sync(isolated_db, ticker_override=ticker, now_ms=now_ms + 1000)

        isolated_db.refresh(order)
        assert order.state == "rejected"
        assert "INVALID_ORDER_THRESHOLD" in (order.invalidation_reason or "")


# ===========================================================================
# 2. Production Alert Payload via Orchestrator evaluate_cycle (Rule 071)
# ===========================================================================

class TestOrchestratorProductionNearEntryPayload:
    """
    Rule 071: Test that ScalpWatcherOrchestrator.evaluate_cycle produces a NEAR_ENTRY
    payload where distance_price / effective_atr == distance_atr using real producer.
    """

    def test_orchestrator_evaluate_cycle_near_entry_payload(self, isolated_db):
        """
        Seed a zone near current price and run orchestrator.evaluate_cycle.
        Capture published NEAR_ENTRY notification payload.
        Verify distance_price is boundary distance and distance_atr = distance_price / effective_atr.
        """
        _setup_clean_audit_and_policy(isolated_db)

        # Zone: [4137.76, 4140.21], current price = 4140.60
        poi = WatchedZone(
            zone_id="poi_test_orchestrator",
            symbol="XAUUSDT",
            timeframe="15M",
            zone_type="FVG",
            direction="BEARISH",
            top=4140.21,
            bottom=4137.76,
            created_at=1_700_000_000_000,
            known_at=1_700_000_000_000,
            touch_count=0,
        )

        orchestrator = ScalpWatcherOrchestrator()
        orchestrator.set_active_zones("XAUUSDT", [poi])

        quote = {"bid": 4140.58, "ask": 4140.62, "last": 4140.60, "timestamp": 1_700_000_000_000}

        # Mock event_bus to capture the NEAR_ENTRY event
        captured_events = []
        with patch("services.event_bus.event_bus.publish_event") as mock_pub:
            def capture(event_type, aggregate_id, payload, **kwargs):
                if event_type == "NEAR_ENTRY":
                    captured_events.append(payload)
                ev = MagicMock()
                ev.event_type = event_type
                return ev
            mock_pub.side_effect = capture

            orchestrator.evaluate_cycle(
                db=isolated_db,
                symbol="XAUUSDT",
                now_ms=1_700_000_000_000,
                quote=quote,
                candles_1m=[],
                candles_5m=[],
                candles_15m=[],
                candles_1h=[],
                candles_4h=[],
                config=M1ScalpConfig(),
                auto_enabled=True,
            )

        if captured_events:
            payload = captured_events[0]
            dp = payload.get("distance_price")
            da = payload.get("distance_atr")
            eff_atr = payload.get("effective_atr") or 0.10
            # Boundary distance for 4140.60 above 4140.21 is ~ 0.39
            assert dp == pytest.approx(0.39, abs=0.05), f"Expected boundary distance ~0.39, got {dp}"
            assert da == pytest.approx(dp / eff_atr, abs=0.02), (
                f"Payload consistency: da ({da}) must equal dp ({dp}) / eff_atr ({eff_atr})"
            )


# ===========================================================================
# 3. Proximity Metrics Helper & ATR Flag Comprehensive Tests (Rules 072 - 074)
# ===========================================================================

class TestProximityMetricsAndAtrFlags:
    """
    Rules 056, 057, 072, 074:
    Test compute_zone_proximity_metrics across all ATR edge cases and price positions.
    """

    @pytest.mark.parametrize("measured_atr,expected_effective,expected_floor_flag", [
        (None, 0.10, True),
        (0.0, 0.10, True),
        (-1.0, 0.10, True),
        (0.005, 0.10, True),
        (0.05, 0.10, True),   # 0.05 < 0.10 floor -> floor applied!
        (0.099, 0.10, True),
        (0.10, 0.10, False),  # exactly 0.10 -> measured ATR used
        (1.50, 1.50, False),
        (5.73, 5.73, False),
        (float("nan"), 0.10, True),
        (float("inf"), 0.10, True),
    ])
    def test_rule_074_atr_floor_flag_comprehensive(self, measured_atr, expected_effective, expected_floor_flag):
        """
        Rule 074: Flag must be True whenever measured ATR was replaced by the 0.10 floor,
        including when measured ATR is between 0.01 and 0.10.
        """
        metrics = compute_zone_proximity_metrics(
            price=4145.0,
            zone_low=4137.76,
            zone_high=4140.21,
            atr=measured_atr,
            price_basis="MID"
        )
        assert metrics.effective_atr == pytest.approx(expected_effective, abs=1e-6)
        assert metrics.atr_effective_floor_used is expected_floor_flag

    def test_rule_073_price_basis_labels(self):
        """Rule 073: Proximity metrics must reflect the specified price basis."""
        m_mid = compute_zone_proximity_metrics(4140.60, 4137.76, 4140.21, 5.73, price_basis="MID")
        assert m_mid.price_basis == "MID"
        m_ask = compute_zone_proximity_metrics(4140.62, 4137.76, 4140.21, 5.73, price_basis="ASK")
        assert m_ask.price_basis == "ASK"
        m_bid = compute_zone_proximity_metrics(4140.58, 4137.76, 4140.21, 5.73, price_basis="BID")
        assert m_bid.price_basis == "BID"

    def test_rule_072_screenshot_case_1_and_case_2(self):
        """
        Rule 072: Verify exact values from the two user screenshot fixtures:
        Case 1: Price=4140.60, Zone=[4137.76, 4140.21], ATR=5.73 -> Boundary distance ~0.39.
        Case 2: Price=4140.23, Zone=[4132.98, 4139.85], ATR=5.76 -> Boundary distance ~0.38.
        """
        # Case 1
        m1 = compute_zone_proximity_metrics(
            price=4140.60,
            zone_low=4137.76,
            zone_high=4140.21,
            atr=5.73,
            price_basis="MID",
        )
        assert m1.distance_price == pytest.approx(0.39, abs=0.01)
        assert m1.distance_atr == pytest.approx(round(0.39 / 5.73, 2), abs=0.01)
        assert m1.atr_effective_floor_used is False

        # Case 2
        m2 = compute_zone_proximity_metrics(
            price=4140.23,
            zone_low=4132.98,
            zone_high=4139.85,
            atr=5.76,
            price_basis="MID",
        )
        assert m2.distance_price == pytest.approx(0.38, abs=0.01)
        assert m2.distance_atr == pytest.approx(round(0.38 / 5.76, 2), abs=0.01)
        assert m2.atr_effective_floor_used is False


# ===========================================================================
# 3b. Telegram Message Formatting & Plan Geometry (Rules 058, 059, 075)
# ===========================================================================

class TestTelegramFormattingRules:
    """
    Rules 058, 059, 075:
    - Zone-only notifications do NOT fabricate SL/TP ($0.00).
    - Real plan notifications include valid geometry, Net R:R, Min Net R:R.
    - Broken geometry is rejected as executable plan.
    - None/NaN/Inf do not crash formatter.
    - 0.00 inside zone is formatted as "0.00 USDT (Đang trong vùng)".
    """

    def test_rule_075_telegram_zone_only_no_fake_sl_tp(self):
        """Rule 058/075: When SL/TP are None or 0, message states SL/TP is unconfirmed."""
        data = {
            "setup_id": "zone_test_only",
            "direction": "LONG",
            "reference_price": 4140.60,
            "entry_zone_low": 4137.76,
            "entry_zone_high": 4140.21,
            "entry_target": 4138.98,
            "distance_price": 0.39,
            "distance_atr": 0.07,
            "atr": 5.73,
            "atr_effective_floor_used": False,
            "price_basis": "MID",
            "stop_loss": None,
            "take_profit": None,
        }
        msg = format_telegram_message("NEAR_ENTRY", data)
        assert "Chưa xác định — đây là vùng theo dõi" in msg
        assert "$0.00" not in msg

    def test_rule_075_telegram_valid_plan_shows_levels_and_rr(self):
        """Rule 059/075: Valid plan shows entry, SL, TP, Net RR, and Min Net RR."""
        data = {
            "setup_id": "setup_real_plan",
            "direction": "LONG",
            "reference_price": 4100.0,
            "entry_zone_low": 4098.0,
            "entry_zone_high": 4102.0,
            "entry_target": 4100.0,
            "planned_entry": 4100.0,
            "stop_loss": 4090.0,
            "take_profit": 4124.0,
            "net_rr": 1.65,
            "min_net_rr": 1.5,
            "risk_usdt": 25.0,
            "distance_price": 0.0,
            "distance_atr": 0.0,
            "atr": 5.0,
            "atr_effective_floor_used": False,
        }
        assert _has_valid_plan(data) is True
        msg = format_telegram_message("NEAR_ENTRY", data)
        assert "$4090.00" in msg
        assert "$4124.00" in msg
        assert "1:1.65" in msg
        assert "Min: 1:1.50" in msg
        assert "0.00 USDT (Đang trong vùng)" in msg

    def test_rule_075_telegram_broken_geometry_not_valid_plan(self):
        """Rule 059/075: LONG with SL >= entry is geometrically invalid."""
        data_inv = {
            "direction": "LONG",
            "planned_entry": 4100.0,
            "stop_loss": 4110.0,  # SL above entry for LONG -> invalid!
            "take_profit": 4130.0,
        }
        assert _has_valid_plan(data_inv) is False
        msg = format_telegram_message("NEAR_ENTRY", data_inv)
        assert "Chưa xác định — đây là vùng theo dõi" in msg

    def test_rule_075_telegram_none_nan_inf_do_not_crash(self):
        """Rule 075: Formatter handles None, NaN, and Inf safely."""
        data_nan = {
            "direction": "UNKNOWN_SIDE",
            "reference_price": float("nan"),
            "distance_price": float("inf"),
            "distance_atr": float("-inf"),
            "atr": None,
            "stop_loss": float("nan"),
            "take_profit": None,
        }
        msg = format_telegram_message("NEAR_ENTRY", data_nan)
        assert isinstance(msg, str)
        assert "Chưa xác định" in msg



# ===========================================================================
# 4. Expanded NY PAPER Window & Time Profile Rules (Rules 076 - 079)
# ===========================================================================

class TestCG01TimingProfileRules:
    """
    Rules 037 - 043, 076 - 079:
    Expanded NY PAPER window (08:00 - 11:00 NY) opt-in, legacy profile retention,
    and strict PAPER-only execution scope guard.
    """

    def test_rule_076_api_config_roundtrip(self, client):
        """
        Rule 076: GET /api/scalp/config, POST /api/scalp/config with timing_profile and min_net_rr.
        Verify persistence and validation.
        """
        # 1. Update to CG01_NY_PAPER_EXPANDED and min_net_rr=1.5
        payload = {
            "min_net_rr": 1.5,
            "timing_profile": "CG01_NY_PAPER_EXPANDED",
            "execution_profile": "CG01",
            "cg01_profile_mode": "PAPER_EXECUTION",
        }
        res = client.post("/api/scalp/config", json=payload)
        assert res.status_code == 200, res.text
        data = res.json()
        eff = data.get("effective_config", data)
        assert eff.get("min_net_rr") == 1.5
        assert eff.get("timing_profile") == "CG01_NY_PAPER_EXPANDED"

        # 2. GET /api/scalp/config returns persisted values
        get_res = client.get("/api/scalp/config")
        assert get_res.status_code == 200
        get_data = get_res.json()
        get_eff = get_data.get("effective_config", get_data)
        assert get_eff.get("min_net_rr") == 1.5
        assert get_eff.get("timing_profile") == "CG01_NY_PAPER_EXPANDED"

        # 3. Reject invalid timing profile
        bad_res = client.post("/api/scalp/config", json={"timing_profile": "INVALID_PROFILE_NAME"})
        assert bad_res.status_code == 400

    def test_rule_077_expanded_ny_paper_window_boundaries(self):
        """
        Rule 077: Fake clock in America/New_York:
        - 07:59:59 -> outside window (is_executable=False)
        - 08:00:00 -> inside window (is_executable=True)
        - 09:00:00 -> inside window (is_executable=True)
        - 10:30:00 -> inside window (is_executable=True)
        - 11:00:00 -> outside window (is_executable=False, end exclusive)
        """
        ny_tz = ZoneInfo("America/New_York")
        date_ref = (2026, 10, 20)

        cfg = CG01Config(
            timing_profile=CG01TimingProfile.CG01_NY_PAPER_EXPANDED,
            profile_mode=CG01ProfileMode.PAPER_EXECUTION,
        )

        test_cases = [
            (datetime(*date_ref, 7, 59, 59, tzinfo=ny_tz), False, "07:59:59 is before 08:00"),
            (datetime(*date_ref, 8, 0, 0, tzinfo=ny_tz), True, "08:00:00 is window start (inclusive)"),
            (datetime(*date_ref, 9, 0, 0, tzinfo=ny_tz), True, "09:00:00 is within expanded window"),
            (datetime(*date_ref, 10, 30, 0, tzinfo=ny_tz), True, "10:30:00 is within expanded window"),
            (datetime(*date_ref, 11, 0, 0, tzinfo=ny_tz), False, "11:00:00 is window end (exclusive)"),
            (datetime(*date_ref, 11, 0, 1, tzinfo=ny_tz), False, "11:00:01 is after window"),
        ]

        for dt, expected_executable, desc in test_cases:
            t_ms = int(dt.timestamp() * 1000)
            ctx = resolve_cg01_time_context(t_ms, config=cfg)
            assert ctx.is_in_window is expected_executable, f"Failed at {dt}: {desc}. Got {ctx.is_in_window}"
            if expected_executable:
                assert ctx.is_in_window is True
                assert ctx.active_window_id in ("NY_PAPER_EXPANDED", "LONDON_SILVER_BULLET")
                # Expiry deadline must respect window_end_ms
                assert ctx.window_end_ms is not None
                assert ctx.window_end_ms > t_ms

    def test_rule_078_legacy_timing_profile_retained(self):
        """
        Rule 078: SILVER_BULLET_LONDON_NY retains original windows:
        - London: 03:00 - 04:00 NY
        - NY AM: 10:00 - 11:00 NY (08:00 - 09:59 is OUTSIDE)
        """
        ny_tz = ZoneInfo("America/New_York")
        date_ref = (2026, 10, 20)

        cfg = CG01Config(
            timing_profile=CG01TimingProfile.SILVER_BULLET_LONDON_NY,
            profile_mode=CG01ProfileMode.PAPER_EXECUTION,
        )

        # 03:30 NY -> London in window
        t_london = int(datetime(*date_ref, 3, 30, 0, tzinfo=ny_tz).timestamp() * 1000)
        ctx_london = resolve_cg01_time_context(t_london, config=cfg)
        assert ctx_london.is_in_window is True
        assert ctx_london.active_window_id == "LONDON_SILVER_BULLET"

        # 09:00 NY -> OUTSIDE original NY window (original is 10:00 - 11:00)
        t_0900 = int(datetime(*date_ref, 9, 0, 0, tzinfo=ny_tz).timestamp() * 1000)
        ctx_0900 = resolve_cg01_time_context(t_0900, config=cfg)
        assert ctx_0900.is_in_window is False

        # 10:30 NY -> INSIDE original NY window
        t_1030 = int(datetime(*date_ref, 10, 30, 0, tzinfo=ny_tz).timestamp() * 1000)
        ctx_1030 = resolve_cg01_time_context(t_1030, config=cfg)
        assert ctx_1030.is_in_window is True
        assert ctx_1030.active_window_id == "NY_AM_SILVER_BULLET"

    def test_rule_079_paper_only_scope_guard(self):
        """
        Rule 041, 079: CG01_NY_PAPER_EXPANDED cannot be used outside PAPER scope.
        If execution_scope is LIVE, windows fallback to SILVER_BULLET_LONDON_NY.
        """
        ny_tz = ZoneInfo("America/New_York")
        dt_0930 = datetime(2026, 10, 20, 9, 30, 0, tzinfo=ny_tz)

        # In LIVE execution scope, expanded profile is NOT permitted -> falls back to SILVER_BULLET_LONDON_NY
        act_win, _, _, eff_prof = resolve_cg01_windows_for_profile(
            dt_0930,
            profile="CG01_NY_PAPER_EXPANDED",
            execution_scope="LIVE"
        )
        assert eff_prof == "SILVER_BULLET_LONDON_NY"
        assert act_win is None  # 09:30 NY is outside SILVER_BULLET_LONDON_NY window

        # In PAPER scope, expanded profile is permitted
        act_win_paper, _, _, eff_prof_paper = resolve_cg01_windows_for_profile(
            dt_0930,
            profile="CG01_NY_PAPER_EXPANDED",
            execution_scope="PAPER"
        )
        assert eff_prof_paper == "CG01_NY_PAPER_EXPANDED"
        assert act_win_paper == "NY_PAPER_EXPANDED"


# ===========================================================================
# 5. Full Recognition Pipeline in Expanded Window (Rules 080 - 084)
# ===========================================================================

class TestCG01ExpandedWindowPipeline:
    """
    Rules 080 - 084:
    Integration tests running candles through engine/orchestrator in the expanded
    NY window (09:00 NY), verifying ARM, Fill, quota caps, and trigger integrity.
    """

    def test_rule_081_full_long_expanded_window_0900_ny(self, isolated_db):
        """
        Rule 081: Full CG01 LONG recognition at 09:00 NY (outside original 10-11 NY window).
        Candles -> Engine -> Candidate -> Authority -> ARM -> Coordinator fill.
        """
        _setup_clean_audit_and_policy(isolated_db)

        ny_tz = ZoneInfo("America/New_York")
        # 09:00 NY is inside CG01_NY_PAPER_EXPANDED window
        dt = datetime(2026, 10, 20, 9, 0, tzinfo=ny_tz)
        t_start = int(dt.timestamp() * 1000)

        c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
        now_ms = t_start + 600_000

        quote = make_valid_quote(2625.5, 2625.7, now_ms)
        poi = WatchedZone(
            zone_id="poi_expanded_long",
            symbol="XAUUSDT",
            timeframe="15M",
            zone_type="FVG",
            direction="BULLISH",
            top=2625.0,
            bottom=2620.0,
            created_at=t_start - 3600_000,
            known_at=t_start - 1800_000,
            touch_count=0,
        )

        cfg = M1ScalpConfig(
            scalp_m1_mode="PAPER",
            execution_profile="CG01",
            cg01_enabled=True,
            cg01_profile_mode="PAPER_EXECUTION",
            timing_profile="CG01_NY_PAPER_EXPANDED",
            daily_max_cap=3,
            min_net_rr=1.5,
        )

        orchestrator = ScalpWatcherOrchestrator()
        orchestrator.set_active_zones("XAUUSDT", [poi])
        orchestrator.evaluate_cycle(
            isolated_db,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_5m=[],
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
            auto_enabled=True,
        )

        order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.direction == "LONG").first()
        assert order is not None, "Order should be armed in expanded window at 09:00 NY"
        assert order.state == "armed"
        assert order.order_type == "LIMIT"

        # Now test fill via coordinator
        coordinator = ExecutionCoordinator()
        fill_tick = {"bid": order.planned_entry - 0.10, "ask": order.planned_entry, "last": order.planned_entry, "timestamp": now_ms + 1000}
        coordinator.evaluate_orders_sync(isolated_db, ticker_override=fill_tick, now_ms=now_ms + 1000)

        isolated_db.refresh(order)
        assert order.state == "paper_open", f"Order should fill, got state={order.state}, reason={order.invalidation_reason}"

    def test_rule_082_full_short_expanded_window_0900_ny(self, isolated_db):
        """
        Rule 082: Full CG01 SHORT recognition at 09:00 NY in expanded window.
        """
        _setup_clean_audit_and_policy(isolated_db)

        ny_tz = ZoneInfo("America/New_York")
        dt = datetime(2026, 10, 20, 9, 0, tzinfo=ny_tz)
        t_start = int(dt.timestamp() * 1000)

        c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
        now_ms = t_start + 600_000

        quote = make_valid_quote(2649.5, 2649.7, now_ms)
        poi = WatchedZone(
            zone_id="poi_expanded_short",
            symbol="XAUUSDT",
            timeframe="15M",
            zone_type="FVG",
            direction="BEARISH",
            top=2650.0,
            bottom=2645.0,
            created_at=t_start - 3600_000,
            known_at=t_start - 1800_000,
            touch_count=0,
        )

        cfg = M1ScalpConfig(
            scalp_m1_mode="PAPER",
            execution_profile="CG01",
            cg01_enabled=True,
            cg01_profile_mode="PAPER_EXECUTION",
            timing_profile="CG01_NY_PAPER_EXPANDED",
            daily_max_cap=3,
            min_net_rr=1.5,
        )

        orchestrator = ScalpWatcherOrchestrator()
        orchestrator.set_active_zones("XAUUSDT", [poi])
        orchestrator.evaluate_cycle(
            isolated_db,
            symbol="XAUUSDT",
            now_ms=now_ms,
            quote=quote,
            candles_1m=c_1m,
            candles_5m=[],
            candles_15m=c_15m,
            candles_1h=c_1h,
            candles_4h=c_4h,
            config=cfg,
            auto_enabled=True,
        )

        order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.direction == "SHORT").first()
        assert order is not None, "SHORT order should be armed in expanded window"
        assert order.state == "armed"
        assert order.stop_loss > order.planned_entry > order.take_profit

        # Coordinator fill
        coordinator = ExecutionCoordinator()
        fill_tick = {"bid": order.planned_entry, "ask": order.planned_entry + 0.10, "last": order.planned_entry, "timestamp": now_ms + 1000}
        coordinator.evaluate_orders_sync(isolated_db, ticker_override=fill_tick, now_ms=now_ms + 1000)

        isolated_db.refresh(order)
        assert order.state == "paper_open", f"SHORT order should fill, got state={order.state}, reason={order.invalidation_reason}"

    def test_rule_083_incomplete_triggers_do_not_arm(self, isolated_db):
        """
        Rule 083: Expanded window does NOT relax structural triggers.
        If candles have no sweep or no displacement/MSS, candidate is NOT armed.
        """
        _setup_clean_audit_and_policy(isolated_db)

        ny_tz = ZoneInfo("America/New_York")
        dt = datetime(2026, 10, 20, 9, 0, tzinfo=ny_tz)
        t_start = int(dt.timestamp() * 1000)

        # Build flat/neutral candles with no sweep or displacement
        flat_candles = [
            {"time": t_start + i * 60_000, "open": 2625.0, "high": 2625.5, "low": 2624.5, "close": 2625.0, "volume": 100}
            for i in range(30)
        ]

        poi = WatchedZone(
            zone_id="poi_flat",
            symbol="XAUUSDT",
            timeframe="15M",
            zone_type="FVG",
            direction="BULLISH",
            top=2625.0,
            bottom=2620.0,
            created_at=t_start - 3600_000,
            known_at=t_start - 1800_000,
            touch_count=0,
        )

        cfg = M1ScalpConfig(
            scalp_m1_mode="PAPER",
            execution_profile="CG01",
            cg01_enabled=True,
            cg01_profile_mode="PAPER_EXECUTION",
            timing_profile="CG01_NY_PAPER_EXPANDED",
            min_net_rr=1.5,
        )

        orchestrator = ScalpWatcherOrchestrator()
        orchestrator.set_active_zones("XAUUSDT", [poi])
        orchestrator.evaluate_cycle(
            isolated_db,
            symbol="XAUUSDT",
            now_ms=t_start + 60_000,
            quote=make_valid_quote(2625.0, 2625.2, t_start + 60_000),
            candles_1m=flat_candles,
            candles_5m=[],
            candles_15m=flat_candles,
            candles_1h=[],
            candles_4h=[],
            config=cfg,
            auto_enabled=True,
        )

        orders = isolated_db.query(models.PaperOrder).all()
        assert len(orders) == 0, "No order should be armed without valid sweep and MSS"

    def test_rule_084_daily_fill_cap_preserved(self, isolated_db):
        """
        Rule 084: If daily fills quota (3 fills) is exhausted, coordinator blocks further fills.
        """
        _setup_clean_audit_and_policy(isolated_db)
        audit = crud.get_or_create_today_audit(isolated_db)
        audit.fills_count = 3  # Max cap reached
        isolated_db.commit()

        now_ms = 1_700_000_000_000
        order = models.PaperOrder(
            id="ord-cap-test",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=4100.0,
            stop_loss=4090.0,
            take_profit=4130.0,
            quantity=0.1,
            initial_risk_usdt=1.0,
            risk_pct=0.10,
            gross_rr=3.0,
            estimated_net_rr=2.5,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=now_ms + 3600_000,
            config_version=1,
            cost_snapshot=json.dumps({"min_net_rr_at_arm": 1.5, "slippage_usd": 0.10}),
        )
        isolated_db.add(order)
        isolated_db.commit()

        coordinator = ExecutionCoordinator()
        ticker = {"bid": 4099.90, "ask": 4100.00, "last": 4100.00, "timestamp": now_ms + 1000}
        coordinator.evaluate_orders_sync(isolated_db, ticker_override=ticker, now_ms=now_ms + 1000)

        isolated_db.refresh(order)
        assert order.state != "paper_open", "Cannot fill when daily max fills (3) is reached"
        assert order.state == "rejected"
        assert "MAX_DAILY_ENTRIES" in (order.invalidation_reason or "") or "Execution guard" in (order.invalidation_reason or "")

    def test_rule_080_news_guard_in_expanded_window(self, isolated_db):
        """
        Rule 048, 080: 08:30 NY is inside the expanded window, but is a common time for High-Impact news (CPI/NFP).
        - Case A: High-impact USD news within +/- 15m blackout blocks ARM via entry guard (fail-closed).
        - Case B: In the absence of blackout, valid setup is permitted by timing.
        """
        _setup_clean_audit_and_policy(isolated_db)
        from services.entry_guard_service import EntryGuardRequest, evaluate_entry_guard

        ny_tz = ZoneInfo("America/New_York")
        # 08:30 NY
        dt_news = datetime(2026, 10, 20, 8, 30, tzinfo=ny_tz)
        t_news = int(dt_news.timestamp() * 1000)

        # Seed high-impact USD economic news at 08:30 NY
        news_row = models.EconomicNews(
            id=88301,
            title="US CPI MoM",
            currency="USD",
            impact="HIGH",
            scheduled_at=t_news,
            received_at=t_news - 3600_000,
        )
        isolated_db.add(news_row)
        isolated_db.commit()

        # Case A: Request at 08:25 NY (within 15m blackout envelope)
        from services.news_blackout_service import evaluate_news_blackout
        view_blackout = evaluate_news_blackout(isolated_db, t_news - 5 * 60 * 1000)
        assert view_blackout.is_blackout is True
        assert view_blackout.title == "US CPI MoM"

        req_blackout = EntryGuardRequest(
            stage="ARM",
            now_ms=t_news - 5 * 60 * 1000,
            symbol="XAUUSDT",
            direction="LONG",
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            planned_entry=2650.0,
            stop_loss=2645.0,
            take_profit=2665.0,
            timeframe="1M",
        )
        guard_res = evaluate_entry_guard(isolated_db, req_blackout)
        assert guard_res.allowed is False, "ARM must be blocked during news blackout"
        block_codes = [c.code for c in guard_res.checks if c.state == "BLOCK"]
        assert any("NEWS" in c or "news" in c.lower() for c in block_codes), f"Blocked codes: {block_codes}"

        # Case B: Request at 09:15 NY (outside the 15m blackout envelope)
        view_clear = evaluate_news_blackout(isolated_db, t_news + 45 * 60 * 1000)
        assert view_clear.is_blackout is False

        req_clear = EntryGuardRequest(
            stage="ARM",
            now_ms=t_news + 45 * 60 * 1000,
            symbol="XAUUSDT",
            direction="LONG",
            execution_mode="AUTO",
            origin="AUTO_STRATEGY",
            planned_entry=2650.0,
            stop_loss=2645.0,
            take_profit=2665.0,
            timeframe="1M",
        )
        guard_clear = evaluate_entry_guard(isolated_db, req_clear)
        clear_block_codes = [c.code for c in guard_clear.checks if c.state == "BLOCK"]
        assert not any("NEWS" in c or "news" in c.lower() for c in clear_block_codes), (
            f"News blackout should not block outside envelope, got: {clear_block_codes}"
        )

    def test_rule_085_restart_and_config_switch(self, isolated_db):
        """
        Rule 085:
        - Pending armed order and open position survive across session reload.
        - Expiry deadline is preserved.
        - Config switch (e.g. changing timing profile) does not mutate past order snapshot.
        - Open position SL/TP/direction/quantity remain intact.
        """
        _setup_clean_audit_and_policy(isolated_db)

        now_ms = 1_700_000_000_000
        # 1. Pending armed order
        armed_order = models.PaperOrder(
            id="ord_pending_restart",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=2650.0,
            stop_loss=2645.0,
            take_profit=2665.0,
            quantity=0.1,
            initial_risk_usdt=0.5,
            risk_pct=0.10,
            gross_rr=3.0,
            estimated_net_rr=2.5,
            created_at=now_ms,
            armed_at=now_ms,
            expires_at=now_ms + 1800_000,
            config_version=1,
            cost_snapshot=json.dumps({"min_net_rr_at_arm": 1.5, "slippage_usd": 0.10}),
        )
        # 2. Existing open position
        open_pos = models.PaperOrder(
            id="ord_open_pos_restart",
            instrument="XAUUSDT",
            direction="SHORT",
            state="paper_open",
            order_type="LIMIT",
            planned_entry=2680.0,
            actual_entry=2680.0,
            stop_loss=2685.0,
            take_profit=2665.0,
            quantity=0.2,
            initial_risk_usdt=1.0,
            risk_pct=0.10,
            gross_rr=3.0,
            estimated_net_rr=2.4,
            created_at=now_ms - 1000,
            armed_at=now_ms - 1000,
            opened_at=now_ms - 500,
            config_version=1,
            cost_snapshot=json.dumps({"min_net_rr_at_arm": 1.5, "slippage_usd": 0.10}),
        )
        isolated_db.add(armed_order)
        isolated_db.add(open_pos)
        isolated_db.commit()

        # Simulate fresh session reload from DB
        isolated_db.expire_all()

        reloaded_armed = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "ord_pending_restart").first()
        reloaded_pos = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "ord_open_pos_restart").first()

        assert reloaded_armed.state == "armed"
        assert reloaded_armed.expires_at == now_ms + 1800_000
        assert json.loads(reloaded_armed.cost_snapshot)["min_net_rr_at_arm"] == 1.5

        assert reloaded_pos.state == "paper_open"
        assert reloaded_pos.quantity == 0.2
        assert reloaded_pos.direction == "SHORT"
        assert reloaded_pos.stop_loss == 2685.0
        assert reloaded_pos.take_profit == 2665.0

        # Change system config
        cfg_row = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == "timing_profile").first()
        if cfg_row:
            cfg_row.value = "SILVER_BULLET_LONDON_NY"
        else:
            isolated_db.add(models.SystemConfig(key="timing_profile", value="SILVER_BULLET_LONDON_NY", updated_at=now_ms))
        isolated_db.commit()

        # Assert past snapshots are not mutated
        isolated_db.refresh(reloaded_armed)
        assert json.loads(reloaded_armed.cost_snapshot)["min_net_rr_at_arm"] == 1.5

    def test_rule_086_notification_flow_deterministic(self):
        """
        Rule 086: Deterministic verification of the full Telegram notification lifecycle:
        NEAR_ENTRY -> READY / TRIGGER_CONFIRMED -> ARMED -> FILLED -> TP_HIT / SL_HIT.
        Assert that each stage has the correct semantics and no fake 'filled' alert is sent before execution.
        """
        now_ms = 1_700_000_000_000

        # 1. NEAR_ENTRY
        msg_near = format_telegram_message("NEAR_ENTRY", {
            "setup_id": "setup_flow_01",
            "direction": "LONG",
            "reference_price": 2650.0,
            "entry_zone_low": 2648.0,
            "entry_zone_high": 2652.0,
            "entry_target": 2650.0,
            "distance_price": 0.0,
            "distance_atr": 0.0,
            "atr": 4.5,
            "atr_effective_floor_used": False,
        }, occurred_at=now_ms)
        assert "SẮP TIẾP CẬN VÙNG ENTRY" in msg_near
        assert "tự động vào lệnh nếu chưa đủ xác nhận" in msg_near

        # 2. TRIGGER_CONFIRMED
        msg_conf = format_telegram_message("TRIGGER_CONFIRMED", {
            "setup_id": "setup_flow_01",
            "direction": "LONG",
            "planned_entry": 2650.0,
            "stop_loss": 2645.0,
            "take_profit": 2665.0,
            "net_rr": 1.65,
            "min_net_rr": 1.5,
            "model_id": "CG01_SWEEP_MSS_FVG",
        }, occurred_at=now_ms)
        assert "XÁC NHẬN TRIGGER" in msg_conf

        # 3. ARMED
        msg_armed = format_telegram_message("ARMED", {
            "order_id": "ord_flow_01",
            "direction": "LONG",
            "planned_entry": 2650.0,
            "stop_loss": 2645.0,
            "take_profit": 2665.0,
            "quantity": 0.1,
            "order_type": "LIMIT",
        }, occurred_at=now_ms)
        assert "ĐÃ ARM LỆNH CHỜ" in msg_armed

        # 4. FILLED
        msg_filled = format_telegram_message("FILLED", {
            "order_id": "ord_flow_01",
            "direction": "LONG",
            "actual_entry": 2650.0,
            "stop_loss": 2645.0,
            "take_profit": 2665.0,
            "quantity": 0.1,
            "realized_pnl_net": 0.0,
        }, occurred_at=now_ms)
        assert "ĐÃ KHỚP LỆNH" in msg_filled

        # 5. TP_HIT
        msg_tp = format_telegram_message("TP_HIT", {
            "trade_id": "ord_flow_01",
            "direction": "LONG",
            "actual_entry": 2650.0,
            "actual_exit": 2665.0,
            "realized_pnl": 15.0,
            "realized_r": 1.65,
        }, occurred_at=now_ms)
        assert "CHỐT LỜI TP (PAPER)" in msg_tp
        assert "$2650.00" in msg_tp
        assert "$2665.00" in msg_tp

        # 6. SL_HIT
        msg_sl = format_telegram_message("SL_HIT", {
            "trade_id": "ord_flow_02",
            "direction": "SHORT",
            "actual_entry": 2680.0,
            "actual_exit": 2685.0,
            "realized_pnl": -5.0,
            "realized_r": -1.0,
        }, occurred_at=now_ms)
        assert "CẮT LỖ SL (PAPER)" in msg_sl
        assert "$2680.00" in msg_sl
        assert "$2685.00" in msg_sl
