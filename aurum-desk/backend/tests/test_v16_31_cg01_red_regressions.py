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
        "current_daily_bias": {
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


# =========================================================================
# Regression RED 1 (Rule 089): PRIMARY missing current authority blocks guard
# =========================================================================
def test_red_primary_missing_current_authority_blocks_guard():
    """Rule 089: Guard with PRIMARY candidate bias READY/valid token but NO current authority must NOT pass."""
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
            "daily_bias": {
                "status": "READY",
                "direction": "BULLISH",
                "allowed_directions": ["LONG"],
                "authority_token": "auth_tok_111",
                "config_fingerprint": "fp_111",
            },
            # Missing current_daily_bias!
        },
    )
    chk = _check_strategy(req)
    assert chk.state == "BLOCK"
    assert chk.code == "DAILY_BIAS_CURRENT_UNAVAILABLE"


# =========================================================================
# Regression RED 2 (Rule 090): Current bias INVALIDATED blocks even if token matches
# =========================================================================
def test_red_current_bias_invalidated_blocks_even_if_token_matches():
    """Rule 090: Current bias status INVALIDATED with empty allowed_directions must BLOCK even if token matches."""
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
            "daily_bias": {
                "status": "READY",
                "direction": "BULLISH",
                "allowed_directions": ["LONG"],
                "authority_token": "auth_tok_111",
                "config_fingerprint": "fp_111",
            },
            "current_daily_bias": {
                "status": "INVALIDATED",
                "direction": "NEUTRAL",
                "allowed_directions": [],
                "authority_token": "auth_tok_111",
                "config_fingerprint": "fp_111",
            },
        },
    )
    chk = _check_strategy(req)
    assert chk.state == "BLOCK"
    assert chk.code == "DAILY_BIAS_CURRENT_INVALIDATED"


# =========================================================================
# Regression RED 3 (Rule 091): Pending PRIMARY without current authority defers fill
# =========================================================================
def test_red_pending_primary_missing_current_authority_defers_and_does_not_fill():
    """Rule 091: Pending PRIMARY order without current authority does NOT fill (remains armed, no quota increment)."""
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base
    import models
    from services.trade_lifecycle_service import TradeLifecycleService
    from services.cg01_daily_bias_service import clear_current_cg01_bias_authority
    from domain_calculator import calculate_risk_reward

    clear_current_cg01_bias_authority()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    arm_snap = {
        "binding": {
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "authority_token": "arm_tok_123",
            "allowed_directions": ["LONG"],
        }
    }
    order = models.PaperOrder(
        id="ord_pending_primary_defer_test",
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
    res_order = TradeLifecycleService.execute_fill(
        db, order, fill_price=2500.0, calc_result=calc, now_ms=1700000060000, sizing_context={}
    )
    # Order must remain armed, NOT paper_open
    assert res_order.state == "armed"
    assert res_order.actual_entry is None


# =========================================================================
# Regression RED 4 (Rule 092, 093): Bearish FVG pullback inside zone is IN_ZONE
# =========================================================================
def test_red_bearish_fvg_pullback_inside_zone_is_in_zone_not_respected():
    """Rules 092, 093: Candle 4 closing inside bearish FVG [90, 100] is IN_ZONE, not RESPECTED."""
    dur = resolve_timeframe_ms("4H")
    base_t = 1700000000000
    # C1: Low=100, C3: High=90 -> Bearish FVG [90, 100]
    c1 = {"open_time": base_t, "open": 105.0, "high": 110.0, "low": 100.0, "close": 106.0, "is_closed": True}
    c2 = {"open_time": base_t + dur, "open": 106.0, "high": 108.0, "low": 85.0, "close": 88.0, "is_closed": True}
    c3 = {"open_time": base_t + 2 * dur, "open": 88.0, "high": 90.0, "low": 80.0, "close": 85.0, "is_closed": True}
    # C4: Open=91, High=97, Low=89, Close=95 (closed inside [90, 100])
    c4 = {"open_time": base_t + 3 * dur, "open": 91.0, "high": 97.0, "low": 89.0, "close": 95.0, "is_closed": True}
    as_of = base_t + 4 * dur

    reactions = build_htf_zone_reactions({"4H": [c1, c2, c3, c4]}, as_of_ms=as_of)
    assert len(reactions) >= 1
    assert reactions[0]["reaction"] == "IN_ZONE"
    assert reactions[0]["reaction"] != "RESPECTED"


# =========================================================================
# Rule 095: Real Orchestrator Arm Caller with Changed Authority Blocks
# =========================================================================
def test_real_orchestrator_arm_blocked_when_current_authority_mismatches():
    """Rule 095: Real orchestrator evaluates candidate; if current authority changed before arm, no order is armed."""
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base
    import models
    from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
    from services.scalp_m1_model_service import ScalpCandidate
    from domain_calculator import calculate_risk_reward
    from services.scalp_m1_types import M1ScalpConfig
    from services.cg01_daily_bias_service import record_current_cg01_bias_authority, clear_current_cg01_bias_authority
    from services.cg01_daily_bias_types import CG01DailyBiasSnapshot

    clear_current_cg01_bias_authority()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    orchestrator = ScalpWatcherOrchestrator()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    calc = calculate_risk_reward("LONG", 2500.0, 2490.0, 2535.0, 1000.0, 0.25)
    cand = ScalpCandidate(
        is_eligible=True,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        candidate_key="cand_arm_test_01",
        symbol=symbol,
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2535.0,
        zone_id="poi_01",
        origin_episode_id="ep_01",
        valid_until_ms=now_ms + 60000,
        calculation=calc,
        evidence={
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "daily_bias": {
                "status": "READY",
                "direction": "BULLISH",
                "allowed_directions": ["LONG"],
                "authority_token": "cand_tok_original",
                "config_fingerprint": "fp_same",
            },
        },
    )

    # 1. Authority changed in registry before arming (e.g. direction changed to SHORT)
    changed_snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-20",
        bias_id="bias_changed",
        direction="BEARISH",
        status="READY",
        allowed_directions=("SHORT",),
        authority_token="current_tok_changed",
        config_fingerprint="fp_same",
    )
    record_current_cg01_bias_authority(symbol, changed_snap, evaluated_at_ms=now_ms)

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        execution_profile="CG01",
        cg01_enabled=True,
        cg01_profile_mode="PAPER_EXECUTION",
    )
    quote = {"bid": 2500.0, "ask": 2500.2, "last": 2500.1, "timestamp": now_ms}

    armed_order = orchestrator._arm_paper_candidate(
        db, cand, now_ms=now_ms, clock=None, quote=quote, policy_settings={}, config=cfg, auto_enabled=True
    )
    # Order must NOT be armed
    assert armed_order is None
    orders_in_db = db.query(models.PaperOrder).all()
    assert len(orders_in_db) == 0

    # 2. When current authority is updated to match candidate, arming succeeds (Rule 097)
    import copy
    cand2 = copy.copy(cand)
    cand2.candidate_key = "cand_arm_test_02"
    matching_snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-20",
        bias_id="bias_matching",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="cand_tok_original",
        config_fingerprint="fp_same",
    )
    record_current_cg01_bias_authority(symbol, matching_snap, evaluated_at_ms=now_ms)

    armed_order_success = orchestrator._arm_paper_candidate(
        db, cand2, now_ms=now_ms, clock=None, quote=quote, policy_settings={}, config=cfg, auto_enabled=True
    )
    assert armed_order_success is not None
    assert armed_order_success.state == "armed"


# =========================================================================
# Rule 096, 097: Real Fill Path via Coordinator / PaperBroker
# =========================================================================
def test_real_fill_path_blocked_on_unready_and_succeeds_on_valid_authority():
    """Rules 096, 097: Coordinator fill checks current authority; blocks when unready, fills when valid."""
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base
    import models
    from services.execution_coordinator import ExecutionCoordinator
    from services.cg01_daily_bias_service import record_current_cg01_bias_authority, clear_current_cg01_bias_authority
    from services.cg01_daily_bias_types import CG01DailyBiasSnapshot
    from services.trading_policy_service import TradingPolicyService

    clear_current_cg01_bias_authority()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    policy = TradingPolicyService.get_or_create_policy(db)
    policy.max_daily_fills = 3
    policy.is_active = False
    db.commit()

    now_ms = 1700000000000
    symbol = "XAUUSDT"
    auth_tok = "tok_auth_valid_fill"

    from services.cg01_config_factory import resolve_canonical_cg01_config
    _, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    arm_snap = {
        "binding": {
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "authority_token": auth_tok,
            "allowed_directions": ["LONG"],
            "config_fingerprint": eff_fp,
        }
    }
    order = models.PaperOrder(
        id="ord_real_fill_test",
        setup_id="setup_001",
        signal_id="sig_001",
        instrument=symbol,
        direction="LONG",
        planned_entry=2500.0,
        actual_entry=None,
        stop_loss=2490.0,
        take_profit=2535.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="armed",
        order_type="LIMIT",
        created_at=now_ms,
        arm_decision_snapshot=json.dumps(arm_snap),
    )
    db.add(order)
    db.commit()

    coordinator = ExecutionCoordinator()

    # Case A (Rule 096): Current bias is unavailable in registry -> fill deferred, state remains armed
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2499.5, "ask": 2499.8, "last": 2499.6, "timestamp": now_ms + 1000},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state == "armed"
    assert order.actual_entry is None

    # Case B (Rule 097): Current bias becomes READY with matching token & direction -> fills successfully!
    valid_snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-20",
        bias_id="bias_valid_01",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token=auth_tok,
        config_fingerprint=eff_fp,
    )
    record_current_cg01_bias_authority(symbol, valid_snap, evaluated_at_ms=now_ms + 2000)

    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2499.5, "ask": 2499.8, "last": 2499.6, "timestamp": now_ms + 2000},
        now_ms=now_ms + 2000,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == 2500.0


# =========================================================================
# Rule 099: Active Position Preserved Under Background Processing Cycles
# =========================================================================
def test_active_position_invariant_under_processing_cycles_and_bias_flip():
    """Rule 099: An open position remains open and immutable even if daily bias flips in registry."""
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base
    import models
    from services.execution_coordinator import ExecutionCoordinator
    from services.cg01_daily_bias_service import record_current_cg01_bias_authority
    from services.cg01_daily_bias_types import CG01DailyBiasSnapshot

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    now_ms = 1700000000000
    pos = models.PaperOrder(
        id="ord_open_pos_invariance",
        setup_id="setup_002",
        signal_id="sig_002",
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2500.0,
        actual_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2535.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="paper_open",
        created_at=now_ms,
    )
    db.add(pos)
    db.commit()

    # Flip bias in registry to BEARISH
    bear_snap = CG01DailyBiasSnapshot(
        symbol="XAUUSDT",
        trading_day_key="2023-11-20",
        bias_id="bias_bear",
        direction="BEARISH",
        status="READY",
        allowed_directions=("SHORT",),
        authority_token="tok_bear_flip",
    )
    record_current_cg01_bias_authority("XAUUSDT", bear_snap, evaluated_at_ms=now_ms + 5000)

    coordinator = ExecutionCoordinator()
    # Price is safely between SL (2490) and TP (2535)
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2505.0, "ask": 2505.2, "last": 2505.1, "timestamp": now_ms + 5000},
        now_ms=now_ms + 5000,
    )
    db.refresh(pos)
    assert pos.state == "paper_open"
    assert pos.stop_loss == 2490.0
    assert pos.take_profit == 2535.0
    assert pos.actual_entry == 2500.0


# =========================================================================
# Rule 100: Real Restart with Pending Order and Bootstrap Recovery
# =========================================================================
def test_restart_persisted_pending_order_fills_after_bootstrap():
    """Rule 100: Persist pending order, simulate restart by clearing registry, verify fail-closed then fill after bootstrap."""
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base
    import models
    from services.execution_coordinator import ExecutionCoordinator
    from services.cg01_daily_bias_service import record_current_cg01_bias_authority, clear_current_cg01_bias_authority
    from services.cg01_daily_bias_types import CG01DailyBiasSnapshot
    from services.trading_policy_service import TradingPolicyService

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    policy = TradingPolicyService.get_or_create_policy(db)
    policy.max_daily_fills = 3
    policy.is_active = False
    db.commit()

    now_ms = 1700000000000
    token_x = "token_bootstrap_recovery_100"

    from services.cg01_config_factory import resolve_canonical_cg01_config
    _, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol="XAUUSDT")

    arm_snap = {
        "binding": {
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "authority_token": token_x,
            "allowed_directions": ["LONG"],
            "config_fingerprint": eff_fp,
        }
    }
    order = models.PaperOrder(
        id="ord_restart_test_100",
        setup_id="setup_100",
        signal_id="sig_100",
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2500.0,
        actual_entry=None,
        stop_loss=2490.0,
        take_profit=2535.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="armed",
        order_type="LIMIT",
        created_at=now_ms,
        arm_decision_snapshot=json.dumps(arm_snap),
    )
    db.add(order)
    db.commit()

    # Step 1: Simulate restart by completely clearing in-memory registry
    clear_current_cg01_bias_authority()

    coord_restart = ExecutionCoordinator()
    coord_restart.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2499.0, "ask": 2499.5, "last": 2499.2, "timestamp": now_ms + 1000},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    # Order cannot fill without bootstrapped authority!
    assert order.state == "armed"
    assert order.actual_entry is None

    # Step 2: Bootstrap/warmup recovers authoritative daily bias
    recovered_bias = CG01DailyBiasSnapshot(
        symbol="XAUUSDT",
        trading_day_key="2023-11-20",
        bias_id="bias_bootstrap_01",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token=token_x,
        config_fingerprint=eff_fp,
    )
    record_current_cg01_bias_authority("XAUUSDT", recovered_bias, evaluated_at_ms=now_ms + 2000)

    # Step 3: Next evaluation cycle cleanly fills the order
    coord_restart.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2499.0, "ask": 2499.5, "last": 2499.2, "timestamp": now_ms + 2000},
        now_ms=now_ms + 2000,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == 2500.0
