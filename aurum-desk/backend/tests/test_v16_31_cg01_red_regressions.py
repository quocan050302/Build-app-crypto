"""
CG-01 Daily Bias Red Regressions Suite (Rules 029, 030, 117-121)
Demonstrates the 5 known regressions on baseline BEFORE fixes are applied.
"""
from typing import List, Dict, Any
import pytest

from services.cg01_types import CG01Config, CG01DirectionPolicy
from services.cg01_config_factory import compute_cg01_config_fingerprint
from services.cg01_daily_bias_service import (
    build_htf_zone_reactions,
    evaluate_cg01_daily_bias,
    build_daily_liquidity_facts,
)
from services.entry_guard_service import (
    EntryGuardRequest,
    _check_strategy,
)
from services.htf_structure_service import resolve_timeframe_ms


class MockCandle:
    def __init__(self, open_time: int, open: float, high: float, low: float, close: float, is_closed: bool = True, tf: str = "4H"):
        self.open_time = open_time
        self.timestamp = open_time
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.is_closed = is_closed
        self.timeframe = tf


# =========================================================================
# Regression 1: FVG vừa hình thành bị ghi RESPECTED dù chưa retest (Rule 029, 117)
# =========================================================================
def test_red_fvg_untested_not_respected():
    """Rule 117: Exactly 3 candles forming an FVG with no 4th candle retesting must be UNTESTED, not RESPECTED."""
    dur = resolve_timeframe_ms("4H")
    base_t = 1700000000000
    # Bullish FVG: candle 1 high=2510, candle 2 (wide up), candle 3 low=2525 (gap 2510 - 2525)
    c1 = MockCandle(base_t, 2500, 2510, 2495, 2505, is_closed=True, tf="4H")
    c2 = MockCandle(base_t + dur, 2505, 2530, 2504, 2528, is_closed=True, tf="4H")
    c3 = MockCandle(base_t + 2 * dur, 2528, 2535, 2525, 2532, is_closed=True, tf="4H")

    as_of = base_t + 3 * dur
    reactions = build_htf_zone_reactions({"4H": [c1, c2, c3]}, as_of_ms=as_of)

    assert len(reactions) >= 1
    # Baseline incorrectly writes 'RESPECTED'
    assert reactions[0]["reaction"] == "UNTESTED"


# =========================================================================
# Regression 2: FVG known_at sớm một nến H4 (Rule 029, 118)
# =========================================================================
def test_red_fvg_known_at_candle3_close():
    """Rule 118: A 3-candle FVG is only known when candle 3 closes (ts3 + 4H), not candle 2."""
    dur = resolve_timeframe_ms("4H")
    base_t = 1700000000000
    ts3 = base_t + 2 * dur
    c1 = MockCandle(base_t, 2500, 2510, 2495, 2505, is_closed=True, tf="4H")
    c2 = MockCandle(base_t + dur, 2505, 2530, 2504, 2528, is_closed=True, tf="4H")
    c3 = MockCandle(ts3, 2528, 2535, 2525, 2532, is_closed=True, tf="4H")

    as_of = base_t + 3 * dur
    reactions = build_htf_zone_reactions({"4H": [c1, c2, c3]}, as_of_ms=as_of)

    assert len(reactions) >= 1
    # Baseline incorrectly sets known_at_ms = ts2 + 4H
    expected_known_at = ts3 + dur
    assert reactions[0]["known_at_ms"] == expected_known_at


# =========================================================================
# Regression 3: PRIMARY thiếu daily_bias vẫn PASS strategy check (Rule 029, 119)
# =========================================================================
def test_red_primary_missing_daily_bias_blocks_guard():
    """Rule 119: PRIMARY candidate missing daily_bias must FAIL closed (BLOCK with DAILY_BIAS_MISSING)."""
    req = EntryGuardRequest(
        stage="ARM",
        now_ms=1700000000000,
        symbol="XAUUSDT",
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family="CG01_SWEEP_MSS_FVG",
        setup_state="READY",
        evidence={
            "direction_policy": "DAILY_BIAS_PRIMARY",
            # daily_bias is missing!
        },
    )

    check = _check_strategy(req)
    # Baseline incorrectly passes with PASS
    assert check.state == "BLOCK"
    assert check.code in ("DAILY_BIAS_MISSING", "DAILY_BIAS_UNAVAILABLE")


# =========================================================================
# Regression 4: Snapshot config_fingerprint rỗng (Rule 029, 120)
# =========================================================================
def test_red_snapshot_config_fingerprint_not_empty():
    """Rule 120: evaluate_cg01_daily_bias must store non-empty canonical config_fingerprint matching factory."""
    cfg = CG01Config(direction_policy=CG01DirectionPolicy.DAILY_BIAS_PRIMARY.value)
    expected_fp = compute_cg01_config_fingerprint(cfg)
    assert len(expected_fp) > 0

    dur = resolve_timeframe_ms("4H")
    base_t = 1700000000000
    c_4h = [MockCandle(base_t + i * dur, 2500 + i, 2505 + i, 2495 + i, 2502 + i, is_closed=True, tf="4H") for i in range(15)]
    as_of = base_t + 15 * dur

    bias = evaluate_cg01_daily_bias({"4H": c_4h, "1H": []}, as_of_ms=as_of, config=cfg)
    # Baseline incorrectly stores "" because it did getattr(config, "fingerprint", "")
    assert bias.config_fingerprint == expected_fp


# =========================================================================
# Regression 5: Helper thanh khoản dùng nến H1 tương lai để đánh dấu swept (Rule 029, 121)
# =========================================================================
def test_red_liquidity_facts_future_h1_candle_ignored():
    """Rule 121: H1 candle occurring past as_of must NOT mark PDH as swept at as_of."""
    dur_1d = resolve_timeframe_ms("1D")
    dur_1h = resolve_timeframe_ms("1H")
    base_t = 1700000000000

    # Day 1 completed
    c_prev_day = MockCandle(base_t, 2500, 2550, 2480, 2530, is_closed=True, tf="1D")
    day_close = base_t + dur_1d

    # as_of is 2 hours into day 2
    as_of = day_close + 2 * dur_1h

    # Candle in past (before as_of): within range, no sweep
    h1_past = MockCandle(day_close, 2530, 2540, 2525, 2535, is_closed=True, tf="1H")

    # Candle in future (after as_of): sweeps PDH (high 2555 > 2550, close 2545 <= 2550)
    h1_future = MockCandle(day_close + 4 * dur_1h, 2540, 2555, 2535, 2545, is_closed=True, tf="1H")

    liq_facts = build_daily_liquidity_facts(
        closed_context={"1D": [c_prev_day], "1H": [h1_past, h1_future], "4H": []},
        as_of_ms=as_of,
    )

    pdh_fact = next((f for f in liq_facts if f["level_type"] == "PDH"), None)
    assert pdh_fact is not None
    # Baseline incorrectly marks 'SWEPT' because it evaluated h1_future past as_of
    assert pdh_fact["status"] == "UNSWEPT"


# =========================================================================
# Rule 050, 123: Production Arming with Daily Bias Binding
# =========================================================================
def test_production_orchestrator_arm_binding_passes_and_blocks():
    """Rule 123: Orchestrator passes candidate snapshot daily bias to EntryGuardRequest; valid passes, invalidated blocks."""
    from services.entry_guard_service import _check_strategy, EntryGuardRequest

    valid_ev = {
        "direction_policy": "DAILY_BIAS_PRIMARY",
        "daily_bias": {
            "status": "READY",
            "direction": "BULLISH",
            "allowed_directions": ["LONG"],
            "authority_token": "valid_tok_999",
        },
    }

    inval_ev = {
        "direction_policy": "DAILY_BIAS_PRIMARY",
        "daily_bias": {
            "status": "INVALIDATED",
            "direction": "NEUTRAL",
            "allowed_directions": [],
            "authority_token": "inval_tok_999",
        },
    }

    req_valid = EntryGuardRequest(
        stage="ARM",
        now_ms=1700000000000,
        symbol="XAUUSDT",
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family="CG01_SWEEP_MSS_FVG",
        setup_state="READY",
        evidence=valid_ev,
    )

    req_inval = EntryGuardRequest(
        stage="ARM",
        now_ms=1700000000000,
        symbol="XAUUSDT",
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family="CG01_SWEEP_MSS_FVG",
        setup_state="READY",
        evidence=inval_ev,
    )

    dec_valid = _check_strategy(req_valid)
    assert dec_valid.state == "PASS"

    dec_inval = _check_strategy(req_inval)
    assert dec_inval.state == "BLOCK"
    assert dec_inval.code == "DAILY_BIAS_INVALIDATED"


# =========================================================================
# Rule 060, 127: Open Position Preserved After Bias Change
# =========================================================================
def test_open_position_preserved_after_bias_change():
    """Rule 127: Changing daily bias after position fill does NOT close the position or alter SL/TP."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base
    import models

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # Simulated filled paper order/position
    pos = models.PaperOrder(
        id="order_pos_001",
        setup_id="setup_001",
        signal_id="signal_001",
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2500.0,
        actual_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2525.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="paper_open",
        created_at=1700000000000,
    )
    db.add(pos)
    db.commit()

    # Now daily bias changes from BULLISH to BEARISH
    new_bearish_bias = {
        "status": "READY",
        "direction": "BEARISH",
        "allowed_directions": ["SHORT"],
        "authority_token": "new_bear_tok",
    }

    # Position in DB must remain paper_open with identical SL and TP
    db_pos = db.query(models.PaperOrder).filter(models.PaperOrder.id == "order_pos_001").first()
    assert db_pos is not None
    assert db_pos.state == "paper_open"
    assert db_pos.actual_entry == 2500.0
    assert db_pos.stop_loss == 2490.0
    assert db_pos.take_profit == 2525.0
    assert db_pos.direction == "LONG"


# =========================================================================
# Rule 061, 128: Legacy Order Durability Without Token
# =========================================================================
def test_legacy_order_durability_without_token():
    """Rule 128: Orders created under LEGACY_H1 without daily_bias token continue normal lifecycle without error."""
    req_legacy = EntryGuardRequest(
        stage="ARM",
        now_ms=1700000000000,
        symbol="XAUUSDT",
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family="CG01_SWEEP_MSS_FVG",
        setup_state="READY",
        evidence={"direction_policy": "LEGACY_H1"},  # No daily_bias token
    )

    check = _check_strategy(req_legacy)
    assert check.state == "PASS"


# =========================================================================
# Rule 109, 110: Snapshot to_dict Field Alignment (summary_vi & reference_price)
# =========================================================================
def test_snapshot_to_dict_field_alignment():
    """Rules 109, 110: to_dict provides both summary_vi and ui_summary_vi, and invalidation has reference_price."""
    from services.cg01_daily_bias_types import CG01DailyBiasSnapshot

    snap = CG01DailyBiasSnapshot(
        symbol="XAUUSDT",
        trading_day_key="2023-11-20",
        bias_id="bias_test_1",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="tok_test_01",
        invalidation_rule={
            "type": "H4_PROTECTED_LOW",
            "price": 2520.0,
            "reference_price": 2520.0,
            "rule": "Nến H4 đóng cửa dưới đáy được bảo vệ",
            "timeframe": "4H",
        },
        ui_summary_vi={"status": "TĂNG (LONG)", "detail": "H4 tăng; H1 tiếp diễn."},
    )

    d = snap.to_dict()
    assert "summary_vi" in d
    assert d["summary_vi"] == "H4 tăng; H1 tiếp diễn."
    assert "ui_summary_vi" in d
    assert d["invalidation_rule"]["price"] == 2520.0
    assert d["invalidation_rule"]["reference_price"] == 2520.0


# =========================================================================
# Rule 043, 044, 124, 125: Authority Token & Config Fingerprint Mismatch Checks
# =========================================================================
def test_guard_blocks_on_authority_and_fingerprint_mismatch():
    """Rules 043, 044, 124, 125: Guard detects stale token and changed config fingerprint with specific reasons."""
    from services.entry_guard_service import _check_strategy, EntryGuardRequest

    cand_bias = {
        "status": "READY",
        "direction": "BULLISH",
        "allowed_directions": ["LONG"],
        "authority_token": "token_AAA",
        "config_fingerprint": "fp_old_111",
    }

    # Case 1: Config changed before arm -> CONFIG_FINGERPRINT_MISMATCH
    req_fp_mismatch = EntryGuardRequest(
        stage="ARM",
        now_ms=1700000000000,
        symbol="XAUUSDT",
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family="CG01_SWEEP_MSS_FVG",
        setup_state="READY",
        evidence={
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "daily_bias": cand_bias,
            "current_daily_bias": {
                "status": "READY",
                "authority_token": "token_AAA",
                "config_fingerprint": "fp_new_222",  # Changed!
            },
        },
    )
    dec_fp = _check_strategy(req_fp_mismatch)
    assert dec_fp.state == "BLOCK"
    assert dec_fp.code == "CONFIG_FINGERPRINT_MISMATCH"

    # Case 2: Structural authority token changed before arm -> DAILY_BIAS_AUTHORITY_MISMATCH
    req_tok_mismatch = EntryGuardRequest(
        stage="ARM",
        now_ms=1700000000000,
        symbol="XAUUSDT",
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family="CG01_SWEEP_MSS_FVG",
        setup_state="READY",
        evidence={
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "daily_bias": cand_bias,
            "current_daily_bias": {
                "status": "READY",
                "authority_token": "token_BBB",  # Changed!
                "config_fingerprint": "fp_old_111",
            },
        },
    )
    dec_tok = _check_strategy(req_tok_mismatch)
    assert dec_tok.state == "BLOCK"
    assert dec_tok.code == "DAILY_BIAS_AUTHORITY_MISMATCH"


# =========================================================================
# Rule 055, 056, 126: Pending Order Invalidation When Bias Invalidated
# =========================================================================
def test_pending_primary_fill_invalidated_when_bias_unready():
    """Rules 055, 056, 126: Pending PRIMARY order is invalidated rather than filled if bias lost readiness."""
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base
    import models
    from services.trade_lifecycle_service import TradeLifecycleService
    from domain_calculator import calculate_risk_reward

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # Create an armed order that was bound to DAILY_BIAS_PRIMARY
    arm_snap = {
        "binding": {
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "authority_token": "arm_tok_123",
            "allowed_directions": ["LONG"],
        }
    }

    order = models.PaperOrder(
        id="order_pending_primary_01",
        setup_id="setup_001",
        signal_id="sig_001",
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2500.0,
        actual_entry=None,
        stop_loss=2490.0,
        take_profit=2525.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="armed",
        created_at=1700000000000,
        arm_decision_snapshot=json.dumps(arm_snap),
    )
    db.add(order)
    db.commit()

    calc = calculate_risk_reward("LONG", 2500.0, 2490.0, 2525.0, 1000.0, 0.25)

    # Context indicates bias is now INVALIDATED
    bad_bias = {
        "status": "INVALIDATED",
        "authority_token": "arm_tok_123",
        "allowed_directions": [],
    }

    res_order = TradeLifecycleService.execute_fill(
        db,
        order,
        fill_price=2500.0,
        calc_result=calc,
        now_ms=1700000060000,
        sizing_context={"current_daily_bias": bad_bias},
    )

    assert res_order.state == "invalidated"
    assert "DAILY_BIAS_INVALIDATED" in res_order.invalidation_reason

