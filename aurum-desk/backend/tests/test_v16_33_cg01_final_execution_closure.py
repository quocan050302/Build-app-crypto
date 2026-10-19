"""
backend/tests/test_v16_33_cg01_final_execution_closure.py
V16.33 — Reproduction & Acceptance Tests for CG-01 Final Execution Authority Closure.

Tests defects 021 - 025, 039, 042, 043, 061, 077, 079, 083:
1. test_red_arm_candidate_fp_cannot_override_effective_config_switch (Rule 021, 042, 079)
2. test_red_evaluate_cycle_shadow_or_non_owner_cannot_publish_active (Rule 022, 077)
3. test_red_execute_fill_sizing_context_cannot_bypass_resolver (Rule 023, 061, 083)
4. test_red_resolve_conflicting_config_and_fingerprint_rejected (Rule 043)
5. test_red_registry_and_resolver_deep_immutability (Rule 039, 078)
6. test_red_validator_rejects_empty_or_none_allowed_directions (Rule 024, 055)
"""
import copy
import json
import time
import pytest
from sqlalchemy.orm import Session

import models
from database import SessionLocal
from domain_calculator import CalculationResult
from services.cg01_types import CG01Config
from services.cg01_daily_bias_types import (
    CG01DailyBiasDirection,
    CG01DailyBiasStatus,
    CG01DailyBiasSnapshot,
    CG01BiasAuthorityResult,
)
from services.cg01_config_factory import compute_cg01_config_fingerprint
from services.cg01_daily_bias_service import (
    publish_current_cg01_bias_authority,
    resolve_current_cg01_bias_authority,
    clear_current_cg01_bias_authority,
    validate_cg01_bias_authority_binding,
    resolve_trading_day_key,
)
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
from services.trade_lifecycle_service import TradeLifecycleService


@pytest.fixture
def db(isolated_db):
    yield isolated_db



def test_red_arm_candidate_fp_cannot_override_effective_config_switch(db: Session):
    """
    Rule 021, 042, 079:
    Setup:
    - Authority A is published for config A.
    - Candidate was created with config A (cand_fp = fp_a).
    - Effective config is switched to config B (eff_fp = fp_b).
    - Authority B has NOT been evaluated or published yet.
    Bug in baseline: _arm_paper_candidate used `target_fp = cand_fp or eff_fp`,
    which resolved authority A and armed the order!
    Expected: Must FAIL CLOSED (no armed order created) because effective config is B
    and no authority exists for B.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    day_key = resolve_trading_day_key(now_ms)

    cfg_a = CG01Config(htf_min_bars=12)
    fp_a = compute_cg01_config_fingerprint(cfg_a)

    cfg_b = CG01Config(htf_min_bars=24)
    fp_b = compute_cg01_config_fingerprint(cfg_b)
    assert fp_a != fp_b

    # Publish Authority A for config A
    snap_a = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=day_key,
        bias_id="bias_a_01",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="tok_auth_a",
        config_fingerprint=fp_a,
        evaluated_at_ms=now_ms,
    )
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap_a,
        config=cfg_a,
        evaluated_at_ms=now_ms,
    )
    assert ok_pub is True

    # Candidate has snapshot from config A
    from services.scalp_m1_model_service import ScalpCandidate
    from services.scalp_m1_types import M1ScalpConfig
    from domain_calculator import calculate_risk_reward

    calc = calculate_risk_reward(
        direction="LONG",
        entry=2500.0,
        sl=2490.0,
        tp=2530.0,
        capital=1000.0,
        risk_pct=0.25,
    )

    cand = ScalpCandidate(
        is_eligible=True,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        candidate_key="cand_cfg_switch_01",
        symbol=symbol,
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2530.0,
        gross_rr=calc.gross_rr,
        net_rr=calc.net_rr,
        calculation=calc,
        evidence={
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "daily_bias": snap_a.to_dict(),
        },
        trigger_chain={},
        snapshot=None,
        as_of_ms=now_ms,
        zone_id="zone_cfg_switch_01",
        origin_episode_id="ep_cfg_switch_01",
        valid_until_ms=now_ms + 3_600_000,
    )

    scalp_cfg = M1ScalpConfig(cg01_config=cfg_b)  # Effective config is B!

    quote = {
        "bid": 2500.0,
        "ask": 2500.10,
        "last": 2500.05,
        "timestamp": now_ms + 1000,
        "is_valid": True,
        "is_stale": False,
    }
    policy_settings = {
        "capital_usdt": 1000.0,
        "risk_pct": 0.25,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "capital_source": "INITIAL_CAPITAL_FIXED",
    }

    armed_order = scalp_watcher_orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=now_ms + 1000,
        clock=None,
        quote=quote,
        policy_settings=policy_settings,
        config=scalp_cfg,
        auto_enabled=True,
    )
    # Must NOT arm!
    assert armed_order is None, "Arm passed by using candidate's old authority fingerprint instead of effective config B!"


def test_red_evaluate_cycle_shadow_or_non_owner_cannot_publish_active(db: Session):
    """
    Rule 022, 077:
    evaluate_cycle should NOT publish ACTIVE authority if running in SHADOW mode,
    or under non-owner scheduler, or historical context.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    from services.cg01_types import CG01ResearchContext
    res_ctx = CG01ResearchContext(
        trace_id="trc_shadow_01",
        run_id="run_shadow_01",
        segment_id=None,
        symbol=symbol,
        config_fingerprint="fp_shadow",
        input_versions={},
        evaluation_as_of_ms=now_ms,
        observation_mode="SHADOW",  # SHADOW MODE!
    )

    candles_1m = [
        {"timestamp": now_ms - 60000 * i, "open": 2500.0, "high": 2505.0, "low": 2495.0, "close": 2502.0, "volume": 100.0, "is_closed": True}
        for i in range(30, 0, -1)
    ]
    candles_4h = [
        {"timestamp": now_ms - 4 * 3600000 * i, "open": 2480.0, "high": 2510.0, "low": 2470.0, "close": 2500.0, "volume": 1000.0, "is_closed": True}
        for i in range(15, 0, -1)
    ]
    candles_1h = [
        {"timestamp": now_ms - 3600000 * i, "open": 2490.0, "high": 2505.0, "low": 2485.0, "close": 2501.0, "volume": 500.0, "is_closed": True}
        for i in range(24, 0, -1)
    ]

    from services.scalp_m1_types import M1ScalpConfig
    cfg = M1ScalpConfig(
        cg01_enabled=True,
        cg01_config=CG01Config(enabled=True, profile_mode="PAPER_SHADOW"),
    )

    # Call evaluate_cycle with SHADOW research context
    scalp_watcher_orchestrator.evaluate_cycle(
        db=db,
        symbol=symbol,
        now_ms=now_ms,
        candles_1m=candles_1m,
        candles_4h=candles_4h,
        candles_1h=candles_1h,
        config=cfg,
        research_context=res_ctx,
    )

    # Active registry should NOT have been published!
    res = resolve_current_cg01_bias_authority(symbol=symbol, config=cfg.cg01_config, now_ms=now_ms)
    assert res.available is False, "evaluate_cycle with SHADOW research context published to ACTIVE registry!"


def test_red_execute_fill_sizing_context_cannot_bypass_resolver(db: Session):
    """
    Rule 023, 061, 083:
    Pending order is PRIMARY with LONG bias.
    Active registry has NO authority (or has SHORT authority).
    Caller invokes execute_fill with sizing_context containing a fake 'current_daily_bias'.
    Bug in baseline: execute_fill checks `trusted_bias = sizing_context.get("current_daily_bias")`
    and uses it instead of resolving from the authoritative registry!
    Expected: execute_fill must use the authoritative registry, ignore sizing_context bypass,
    and NOT fill the order!
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    day_key = resolve_trading_day_key(now_ms)

    from services.cg01_config_factory import resolve_canonical_cg01_config
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    # Publish SHORT authority in registry
    snap_short = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=day_key,
        bias_id="bias_reg_short",
        direction="BEARISH",
        status="READY",
        allowed_directions=("SHORT",),
        authority_token="tok_short_reg",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap_short, config=eff_cfg, evaluated_at_ms=now_ms)

    arm_snap = {
        "binding": {
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "authority_token": "tok_old_long",
            "allowed_directions": ["LONG"],
            "config_fingerprint": eff_fp,
            "status": "READY",
        }
    }
    order = models.PaperOrder(
        id="ord_sizing_bypass_test",
        setup_id="setup_002",
        signal_id="sig_002",
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

    # Pass spoofed sizing_context claiming LONG is READY
    fake_sizing_context = {
        "sizing_capital": 1000.0,
        "current_daily_bias": {
            "status": "READY",
            "authority_token": "tok_old_long",
            "allowed_directions": ["LONG"],
            "config_fingerprint": eff_fp,
            "symbol": symbol,
            "available": True,
        }
    }

    from domain_calculator import calculate_risk_reward
    calc = calculate_risk_reward(
        direction="LONG",
        entry=2499.5,
        sl=2490.0,
        tp=2535.0,
        capital=1000.0,
        risk_pct=0.25,
    )

    res_order = TradeLifecycleService.execute_fill(
        db,
        order=order,
        fill_price=2499.5,
        calc_result=calc,
        now_ms=now_ms + 1000,
        sizing_context=fake_sizing_context,
    )
    db.refresh(order)
    # Order must NOT be filled as paper_open!
    assert order.state != "paper_open", "execute_fill allowed sizing_context dictionary to bypass registry authority!"


def test_red_resolve_conflicting_config_and_fingerprint_rejected():
    """
    Rule 043:
    If resolver receives both config=A and config_fingerprint=B,
    and fingerprint(A) != B:
    Resolver must return CONFIG_MISMATCH (not silently prefer B).
    """
    cfg_a = CG01Config(htf_min_bars=12)
    fp_a = compute_cg01_config_fingerprint(cfg_a)
    fp_b = "custom_fingerprint_b_different"

    res = resolve_current_cg01_bias_authority(
        symbol="XAUUSDT",
        config=cfg_a,
        config_fingerprint=fp_b,
        now_ms=1700000000000,
    )
    assert res.available is False
    assert res.status == "CONFIG_MISMATCH"
    assert "conflict" in (res.reason or "").lower() or "mismatch" in (res.reason or "").lower()


def test_red_registry_and_resolver_deep_immutability():
    """
    Rule 039, 078:
    1. Publish snapshot.
    2. Mutate original object.
    3. Resolve must maintain old data.
    4. Mutate resolved object.
    5. Resolve again must still maintain old data.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    day_key = resolve_trading_day_key(now_ms)
    cfg = CG01Config()
    fp = compute_cg01_config_fingerprint(cfg)

    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=day_key,
        bias_id="bias_immut_01",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="tok_immut_01",
        config_fingerprint=fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap, config=cfg, evaluated_at_ms=now_ms)

    # Mutate original object
    snap.status = "MUTATED_INVALIDATED"
    snap.allowed_directions = ("SHORT",)

    # Resolve 1: must still be READY and LONG
    res1 = resolve_current_cg01_bias_authority(symbol=symbol, config=cfg, now_ms=now_ms)
    assert res1.bias is not None
    assert res1.bias.status == "READY"
    assert res1.bias.allowed_directions == ("LONG",)

    # Mutate resolved object
    res1.bias.status = "MUTATED_IN_CALLER"
    res1.bias.authority_token = "tok_hacked"

    # Resolve 2: must still be pristine
    res2 = resolve_current_cg01_bias_authority(symbol=symbol, config=cfg, now_ms=now_ms)
    assert res2.bias is not None
    assert res2.bias.status == "READY", "Mutating resolved object corrupted registry storage!"
    assert res2.bias.authority_token == "tok_immut_01"


def test_red_validator_rejects_empty_or_none_allowed_directions():
    """
    Rule 024, 055:
    Current READY authority must have a valid non-empty tuple/list of LONG/SHORT.
    If current allowed_directions is None or (), validator must reject with
    DAILY_BIAS_CURRENT_INVALID_DIRECTIONS (not skip the check).
    """
    fp = "fp_test_123"
    candidate = {
        "status": "READY",
        "authority_token": "tok_valid",
        "config_fingerprint": fp,
        "allowed_directions": ["LONG"],
    }
    current_with_empty_dirs = {
        "status": "READY",
        "authority_token": "tok_valid",
        "config_fingerprint": fp,
        "allowed_directions": (),
        "symbol": "XAUUSDT",
    }

    valid, code, _, _ = validate_cg01_bias_authority_binding(
        candidate_bias=candidate,
        current_authority=current_with_empty_dirs,
        order_direction="LONG",
        symbol="XAUUSDT",
        effective_fingerprint=fp,
    )
    assert not valid
    assert code == "DAILY_BIAS_CURRENT_INVALID_DIRECTIONS"


def test_084_full_positive_long_primary_arm_fill_close(db: Session):
    """
    Rule 084, 086: Full End-to-End LONG Pipeline under DAILY_BIAS_PRIMARY:
    Owner publishes valid LONG bias -> candidate created -> arm -> fill -> close (TP).
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    day_key = resolve_trading_day_key(now_ms)

    from services.cg01_config_factory import resolve_canonical_cg01_config
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    # 1. Publish authoritative LONG daily bias
    token_long = "tok_auth_long_full_084"
    bias_snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=day_key,
        bias_id="bias_long_084",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token=token_long,
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=bias_snap,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        source="ACTIVE_PIPELINE",
        is_active_owner=True,
        owner="WATCHER",
    )
    assert ok_pub is True

    # 2. Candidate under PRIMARY
    from services.scalp_m1_model_service import ScalpCandidate
    from services.scalp_m1_types import M1ScalpConfig
    from domain_calculator import calculate_risk_reward

    calc = calculate_risk_reward(
        direction="LONG",
        entry=2500.0,
        sl=2490.0,
        tp=2530.0,
        capital=1000.0,
        risk_pct=0.25,
    )

    cand = ScalpCandidate(
        is_eligible=True,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        candidate_key="cand_long_084",
        symbol=symbol,
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2530.0,
        gross_rr=calc.gross_rr,
        net_rr=calc.net_rr,
        calculation=calc,
        evidence={
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "daily_bias": bias_snap.to_dict(),
        },
        trigger_chain={},
        snapshot=None,
        as_of_ms=now_ms,
        zone_id="zone_long_084",
        origin_episode_id="ep_long_084",
        valid_until_ms=now_ms + 3_600_000,
    )

    scalp_cfg = M1ScalpConfig(cg01_config=eff_cfg)
    quote = {
        "bid": 2500.0,
        "ask": 2500.10,
        "last": 2500.05,
        "timestamp": now_ms + 1000,
        "is_valid": True,
        "is_stale": False,
    }
    policy_settings = {
        "capital_usdt": 1000.0,
        "risk_pct": 0.25,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "capital_source": "INITIAL_CAPITAL_FIXED",
    }

    # 3. Arm candidate
    armed_order = scalp_watcher_orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=now_ms + 1000,
        clock=None,
        quote=quote,
        policy_settings=policy_settings,
        config=scalp_cfg,
        auto_enabled=True,
    )
    assert armed_order is not None
    assert armed_order.state == "armed"
    assert armed_order.direction == "LONG"
    db.commit()

    # 4. Fill order when price reaches entry
    fill_time = now_ms + 2000
    calc_fill = calculate_risk_reward(
        direction="LONG",
        entry=2500.0,
        sl=2490.0,
        tp=2530.0,
        capital=1000.0,
        risk_pct=0.25,
    )
    filled_order = TradeLifecycleService.execute_fill(
        db,
        order=armed_order,
        fill_price=2500.0,
        calc_result=calc_fill,
        now_ms=fill_time,
    )
    assert filled_order.state == "paper_open"
    assert filled_order.actual_entry == 2500.0
    assert filled_order.opened_at == fill_time

    # 5. Close order at TP
    exit_time = fill_time + 5000
    closed_order = TradeLifecycleService.execute_close(
        db,
        order_id=filled_order.id,
        exit_price=2530.0,
        exit_cause="TP_HIT",
        now_ms=exit_time,
    )
    assert closed_order.state == "closed"
    assert closed_order.realized_pnl_net > 0.0
    assert closed_order.closed_at == exit_time


def test_085_full_positive_short_primary_arm_fill_close(db: Session):
    """
    Rule 085, 086: Full End-to-End SHORT Pipeline under DAILY_BIAS_PRIMARY:
    Asserts stop_loss > entry > take_profit geometry and direction SHORT preservation.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    day_key = resolve_trading_day_key(now_ms)

    from services.cg01_config_factory import resolve_canonical_cg01_config
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    # 1. Publish authoritative SHORT daily bias
    token_short = "tok_auth_short_full_085"
    bias_snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=day_key,
        bias_id="bias_short_085",
        direction="BEARISH",
        status="READY",
        allowed_directions=("SHORT",),
        authority_token=token_short,
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=bias_snap,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        source="ACTIVE_PIPELINE",
        is_active_owner=True,
        owner="WATCHER",
    )
    assert ok_pub is True

    # 2. Candidate under PRIMARY
    from services.scalp_m1_model_service import ScalpCandidate
    from services.scalp_m1_types import M1ScalpConfig
    from domain_calculator import calculate_risk_reward

    calc = calculate_risk_reward(
        direction="SHORT",
        entry=2500.0,
        sl=2510.0,
        tp=2470.0,
        capital=1000.0,
        risk_pct=0.25,
    )

    cand = ScalpCandidate(
        is_eligible=True,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="SHORT",
        candidate_key="cand_short_085",
        symbol=symbol,
        planned_entry=2500.0,
        stop_loss=2510.0,
        take_profit=2470.0,
        gross_rr=calc.gross_rr,
        net_rr=calc.net_rr,
        calculation=calc,
        evidence={
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "daily_bias": bias_snap.to_dict(),
        },
        trigger_chain={},
        snapshot=None,
        as_of_ms=now_ms,
        zone_id="zone_short_085",
        origin_episode_id="ep_short_085",
        valid_until_ms=now_ms + 3_600_000,
    )

    scalp_cfg = M1ScalpConfig(cg01_config=eff_cfg)
    quote = {
        "bid": 2500.0,
        "ask": 2500.10,
        "last": 2500.05,
        "timestamp": now_ms + 1000,
        "is_valid": True,
        "is_stale": False,
    }
    policy_settings = {
        "capital_usdt": 1000.0,
        "risk_pct": 0.25,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "capital_source": "INITIAL_CAPITAL_FIXED",
    }

    # 3. Arm candidate
    armed_order = scalp_watcher_orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=now_ms + 1000,
        clock=None,
        quote=quote,
        policy_settings=policy_settings,
        config=scalp_cfg,
        auto_enabled=True,
    )
    assert armed_order is not None
    assert armed_order.state == "armed"
    assert armed_order.direction == "SHORT"
    assert armed_order.stop_loss > armed_order.planned_entry > armed_order.take_profit
    db.commit()

    # 4. Fill order when price reaches entry
    fill_time = now_ms + 2000
    calc_fill = calculate_risk_reward(
        direction="SHORT",
        entry=2500.0,
        sl=2510.0,
        tp=2470.0,
        capital=1000.0,
        risk_pct=0.25,
    )
    filled_order = TradeLifecycleService.execute_fill(
        db,
        order=armed_order,
        fill_price=2500.0,
        calc_result=calc_fill,
        now_ms=fill_time,
    )
    assert filled_order.state == "paper_open"
    assert filled_order.actual_entry == 2500.0
    assert filled_order.direction == "SHORT"

    # 5. Close order at TP
    exit_time = fill_time + 5000
    closed_order = TradeLifecycleService.execute_close(
        db,
        order_id=filled_order.id,
        exit_price=2470.0,
        exit_cause="TP_HIT",
        now_ms=exit_time,
    )
    assert closed_order.state == "closed"
    assert closed_order.realized_pnl_net > 0.0


def test_087_restart_recovery(db: Session):
    """
    Rule 087: Pending PRIMARY order exists across restart.
    Before bootstrap: registry is cleared, fill is deferred.
    After bootstrap: matching authority is published, fill succeeds!
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    day_key = resolve_trading_day_key(now_ms)

    from services.cg01_config_factory import resolve_canonical_cg01_config
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    token_restart = "tok_restart_087"
    arm_snap = {
        "binding": {
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "authority_token": token_restart,
            "allowed_directions": ["LONG"],
            "config_fingerprint": eff_fp,
            "status": "READY",
            "symbol": symbol,
        }
    }
    order = models.PaperOrder(
        id="ord_restart_087",
        setup_id="setup_087",
        signal_id="sig_087",
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

    from domain_calculator import calculate_risk_reward
    calc = calculate_risk_reward(
        direction="LONG",
        entry=2500.0,
        sl=2490.0,
        tp=2535.0,
        capital=1000.0,
        risk_pct=0.25,
    )

    # Step 1: Before bootstrap -> registry is empty
    res_before = TradeLifecycleService.execute_fill(
        db,
        order=order,
        fill_price=2500.0,
        calc_result=calc,
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state == "armed"
    assert order.actual_entry is None

    # Step 2: Bootstrap occurs and publishes matching valid authority
    bias_boot = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=day_key,
        bias_id="bias_boot_087",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token=token_restart,
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms + 1500,
    )
    publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=bias_boot,
        config=eff_cfg,
        evaluated_at_ms=now_ms + 1500,
    )

    # Step 3: Next tick arrives -> fill succeeds
    res_after = TradeLifecycleService.execute_fill(
        db,
        order=order,
        fill_price=2500.0,
        calc_result=calc,
        now_ms=now_ms + 2000,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == 2500.0
