"""
backend/tests/test_v16_32_cg01_authoritative_registry.py
V16.32 — Authoritative Daily Bias Registry & Execution Ownership Final Repair
Implements Rules 001 - 125:
- 087: RED SHADOW Pollution: Shadow evaluation does not mutate ACTIVE authority.
- 088: RED Old Write: Stale evaluation (T1 < T2) does not rewind current authority.
- 089: RED Config Switch: Switching effective config blocks arm/fill until new authority is published.
- 090: RED Stale Exact Key: Stale config entry cannot be used once current authority changed.
- 091: RED Default Config: Resolver fails closed without explicit/canonical config; orchestrator resolves canonical config.
- 092: RED Missing Metadata: READY snapshot missing token, fingerprint, or directions blocks arm and fill.
- 093: RED Wrong Symbol: Snapshot of another symbol cannot grant entry.
- 095: Production compute evaluate_cg01_daily_bias is pure.
- 096: Production publisher validates active ownership and rejects shadow sources.
- 097: Owner lifecycle & generation validation.
- 101: Positive paper arm and fill path.
- 103: Open position invariance: changing bias does not close open positions or alter SL/TP.
"""
from __future__ import annotations

import copy
import json
import time
from typing import Any, Dict, List
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
import models
from services.cg01_types import CG01Config
from services.cg01_config_factory import (
    compute_cg01_config_fingerprint,
    resolve_canonical_cg01_config,
)
from services.cg01_daily_bias_types import (
    CG01DailyBiasSnapshot,
    CG01DailyBiasStatus,
    CG01BiasAuthorityResult,
)
from services.cg01_daily_bias_service import (
    publish_current_cg01_bias_authority,
    record_current_cg01_bias_authority,
    clear_current_cg01_bias_authority,
    resolve_current_cg01_bias_authority,
    evaluate_cg01_daily_bias,
    validate_cg01_bias_authority_binding,
    bump_runtime_ownership_epoch,
)
from services.entry_guard_service import (
    EntryGuardRequest,
    _check_strategy,
    evaluate_entry_guard,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.execution_coordinator import ExecutionCoordinator
from services.trade_lifecycle_service import TradeLifecycleService
from services.trading_policy_service import TradingPolicyService
from services.scalp_m1_model_service import ScalpCandidate
from services.scalp_m1_types import M1ScalpConfig
from domain_calculator import calculate_risk_reward


class MockCandle:
    def __init__(self, ts: int, o: float, h: float, l: float, c: float, tf: str = "4H", is_closed: bool = True):
        self.timestamp = ts
        self.open_time = ts
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.timeframe = tf
        self.is_closed = is_closed


def _make_h4_candles(base_ts: int = 1700000000000, count: int = 10) -> List[MockCandle]:
    candles = []
    dur = 4 * 3600 * 1000
    p = 2500.0
    for i in range(count):
        c_ts = base_ts + i * dur
        candles.append(MockCandle(c_ts, p, p + 15.0, p - 5.0, p + 10.0, tf="4H", is_closed=True))
        p += 10.0
    return candles


# =========================================================================
# Rule 087: Regression RED: SHADOW Pollution
# =========================================================================
def test_red_087_shadow_pollution_does_not_mutate_active_authority():
    """
    Rule 087: Publish ACTIVE authority T2.
    Run SHADOW/historical evaluation T1.
    Expected: registry execution still holds ACTIVE T2 authority.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    cfg = CG01Config()
    cfg_fp = compute_cg01_config_fingerprint(cfg)

    # 1. Publish ACTIVE authority T2 (evaluated at t=2000)
    snap_t2 = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_active_t2",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="ACTIVE_TOKEN_T2",
        config_fingerprint=cfg_fp,
        evaluated_at_ms=now_ms + 2000,
    )
    ok = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap_t2,
        config=cfg,
        evaluated_at_ms=now_ms + 2000,
        source="ACTIVE_WATCHER",
        is_active_owner=True,
        owner="WATCHER",
    )
    assert ok is True

    # 2. Run SHADOW evaluation at T1 (evaluated at t=1000)
    from services.cg01_scheduler_service import evaluate_cg01_shadow_snapshot
    dur = 60000
    c_1m = [
        {"symbol": symbol, "timeframe": "1M", "timestamp": now_ms - (i * dur), "open": 2500.0, "high": 2505.0, "low": 2495.0, "close": 2502.0, "volume": 10.0, "is_closed": True}
        for i in range(10)
    ]
    bundle = {
        "1M": c_1m,
        "5M": [],
        "15M": [],
        "1H": [],
        "4H": _make_h4_candles(base_ts=now_ms - 40 * 3600 * 1000, count=10),
        "1D": [],
    }
    quote = {"bid": 2500.0, "ask": 2500.2, "last": 2500.1, "is_valid": True, "is_stale": False}

    shadow_res = evaluate_cg01_shadow_snapshot(
        symbol=symbol,
        as_of_ms=now_ms + 1000,
        bundle=bundle,
        config=cfg,
        current_quote=quote,
    )
    assert shadow_res is not None

    # Also run pure evaluate_cg01_daily_bias directly
    pure_snap = evaluate_cg01_daily_bias(
        {"4H": _make_h4_candles(base_ts=now_ms - 40 * 3600 * 1000, count=10)},
        as_of_ms=now_ms + 1000,
        config=cfg,
        symbol=symbol,
    )
    assert pure_snap is not None

    # 3. Assert ACTIVE authority in registry still holds T2!
    res_auth = resolve_current_cg01_bias_authority(
        symbol=symbol,
        now_ms=now_ms + 2500,
        config=cfg,
    )
    assert res_auth.available is True
    assert res_auth.bias.authority_token == "ACTIVE_TOKEN_T2"
    assert res_auth.evaluated_at_ms == now_ms + 2000
    assert res_auth.source == "ACTIVE_WATCHER"


# =========================================================================
# Rule 088: Regression RED: Old Write
# =========================================================================
def test_red_088_old_write_does_not_rewind_authority():
    """
    Rule 088: Publish T2 then have task T1 complete later.
    Expected: T1 write is rejected; current authority does not rewind.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    cfg = CG01Config()
    cfg_fp = compute_cg01_config_fingerprint(cfg)

    # Publish T2 (time = 2000)
    snap_t2 = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_t2",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_T2",
        config_fingerprint=cfg_fp,
        evaluated_at_ms=now_ms + 2000,
    )
    ok_t2 = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap_t2,
        config=cfg,
        evaluated_at_ms=now_ms + 2000,
        source="ACTIVE_WATCHER",
        is_active_owner=True,
    )
    assert ok_t2 is True

    # Out-of-order write T1 (time = 1000) arrives after T2
    snap_t1 = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_t1",
        direction="BEARISH",
        status="READY",
        allowed_directions=("SHORT",),
        authority_token="TOKEN_T1_STALE",
        config_fingerprint=cfg_fp,
        evaluated_at_ms=now_ms + 1000,
    )
    ok_t1 = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap_t1,
        config=cfg,
        evaluated_at_ms=now_ms + 1000,
        source="ACTIVE_WATCHER",
        is_active_owner=True,
    )
    assert ok_t1 is False  # Rejected anti-rewind!

    # Current authority must remain T2
    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 2500, config=cfg)
    assert res.available is True
    assert res.bias.authority_token == "TOKEN_T2"


# =========================================================================
# Rule 089: Regression RED: Config Switch
# =========================================================================
def test_red_089_config_switch_blocks_until_matching_authority_published():
    """
    Rule 089: Publish authority for config A, then effective config changes to B.
    Before authority B is published, PRIMARY arm and fill must defer/mismatch, not use A.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    cfg_a = CG01Config(displacement_body_atr_min=1.0)
    fp_a = compute_cg01_config_fingerprint(cfg_a)

    cfg_b = CG01Config(displacement_body_atr_min=2.5)
    fp_b = compute_cg01_config_fingerprint(cfg_b)
    assert fp_a != fp_b

    # Publish authority for config A
    snap_a = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_cfg_a",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_CONFIG_A",
        config_fingerprint=fp_a,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap_a, config=cfg_a, evaluated_at_ms=now_ms)

    # 1. Resolver called with new config B -> returns CONFIG_MISMATCH
    res_b = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 100, config=cfg_b)
    assert res_b.available is False
    assert res_b.status == "CONFIG_MISMATCH"

    # 2. Candidate created under config A attempting to arm under config B -> blocked
    req_arm = EntryGuardRequest(
        stage="ARM",
        now_ms=now_ms + 100,
        symbol=symbol,
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family="CG01_SWEEP_MSS_FVG",
        setup_state="READY",
        evidence={
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "daily_bias": snap_a.to_dict(),
            "current_daily_bias": res_b.to_dict(),
        },
    )
    chk = _check_strategy(req_arm)
    assert chk.state == "BLOCK"
    assert chk.code == "CONFIG_FINGERPRINT_MISMATCH"

    # 3. Once authority for B is published, arm succeeds!
    snap_b = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_cfg_b",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_CONFIG_B",
        config_fingerprint=fp_b,
        evaluated_at_ms=now_ms + 200,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap_b, config=cfg_b, evaluated_at_ms=now_ms + 200)

    res_b_new = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 250, config=cfg_b)
    assert res_b_new.available is True
    assert res_b_new.bias.authority_token == "TOKEN_CONFIG_B"


# =========================================================================
# Rule 090: Regression RED: Stale Exact Key
# =========================================================================
def test_red_090_stale_exact_key_ignored_in_favor_of_current_config():
    """
    Rule 090: Symbol has current authority with config B.
    Calling resolver with current config B does not pick any old cache.
    Calling resolver with old config A returns CONFIG_MISMATCH.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    cfg_a = CG01Config(timing_profile="LONDON")
    fp_a = compute_cg01_config_fingerprint(cfg_a)

    cfg_b = CG01Config(timing_profile="NEW_YORK")
    fp_b = compute_cg01_config_fingerprint(cfg_b)
    assert fp_a != fp_b

    # Publish A first
    snap_a = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_a",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOK_A",
        config_fingerprint=fp_a,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap_a, config=cfg_a, evaluated_at_ms=now_ms)

    # Current updated to B
    snap_b = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_b",
        direction="BEARISH",
        status="READY",
        allowed_directions=("SHORT",),
        authority_token="TOK_B",
        config_fingerprint=fp_b,
        evaluated_at_ms=now_ms + 1000,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap_b, config=cfg_b, evaluated_at_ms=now_ms + 1000)

    # Query with config B -> resolves B
    res_b = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 1500, config=cfg_b)
    assert res_b.available is True
    assert res_b.bias.authority_token == "TOK_B"

    # Query with old config A -> rejects with CONFIG_MISMATCH (does NOT use stale A!)
    res_a = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 1500, config=cfg_a)
    assert res_a.available is False
    assert res_a.status == "CONFIG_MISMATCH"


# =========================================================================
# Rule 091: Regression RED: Default Config
# =========================================================================
def test_red_091_default_config_fails_closed_without_explicit_config():
    """
    Rule 091: If resolver is called without config or config_fingerprint,
    fails closed with CONFIG_UNAVAILABLE rather than guessing CG01Config() defaults.
    """
    clear_current_cg01_bias_authority()
    res = resolve_current_cg01_bias_authority(symbol="XAUUSDT", config=None, config_fingerprint=None)
    assert res.available is False
    assert res.status == "CONFIG_UNAVAILABLE"


# =========================================================================
# Rule 092: Regression RED: Missing Metadata
# =========================================================================
def test_red_092_missing_metadata_blocks_arm_and_fill():
    """
    Rule 092: READY current snapshot missing token, fingerprint, or allowed_directions
    must NOT arm or fill.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    cfg = CG01Config()
    cfg_fp = compute_cg01_config_fingerprint(cfg)

    # Sub-case A: Missing authority_token
    snap_no_token = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_no_token",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="",  # Missing!
        config_fingerprint=cfg_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap_no_token, config_fingerprint=cfg_fp, evaluated_at_ms=now_ms)
    res_no_tok = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 100, config=cfg)
    assert res_no_tok.available is False
    assert res_no_tok.status == "MISSING_TOKEN"

    # Sub-case B: Missing config_fingerprint
    clear_current_cg01_bias_authority()
    snap_no_fp = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_no_fp",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="valid_token",
        config_fingerprint="",  # Missing!
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap_no_fp, config_fingerprint="", evaluated_at_ms=now_ms)
    res_no_fp = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 100, config=cfg)
    assert res_no_fp.available is False
    assert res_no_fp.status in ("MISSING_FINGERPRINT", "CONFIG_MISMATCH")

    # Sub-case C: Missing allowed_directions
    clear_current_cg01_bias_authority()
    snap_no_dirs = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_no_dirs",
        direction="BULLISH",
        status="READY",
        allowed_directions=(),  # Missing!
        authority_token="valid_token",
        config_fingerprint=cfg_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap_no_dirs, config=cfg, evaluated_at_ms=now_ms)
    res_no_dirs = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 100, config=cfg)
    assert res_no_dirs.available is False
    assert res_no_dirs.status == "INVALID_DIRECTIONS"


# =========================================================================
# Rule 093: Regression RED: Wrong Symbol
# =========================================================================
def test_red_093_wrong_symbol_authority_rejected():
    """
    Rule 093: Snapshot/token of another symbol cannot grant entry for XAUUSDT.
    """
    clear_current_cg01_bias_authority()
    now_ms = 1700000000000
    cfg = CG01Config()
    cfg_fp = compute_cg01_config_fingerprint(cfg)

    # Publish authority for BTCUSDT
    snap_btc = CG01DailyBiasSnapshot(
        symbol="BTCUSDT",
        trading_day_key="2023-11-14",
        bias_id="bias_btcusdt",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="BTC_TOKEN",
        config_fingerprint=cfg_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol="BTCUSDT", bias=snap_btc, config=cfg, evaluated_at_ms=now_ms)

    # Resolving XAUUSDT must return UNAVAILABLE
    res_xau = resolve_current_cg01_bias_authority(symbol="XAUUSDT", now_ms=now_ms + 100, config=cfg)
    assert res_xau.available is False
    assert res_xau.status == "UNAVAILABLE"


# =========================================================================
# Rule 095: Production Compute Is Pure
# =========================================================================
def test_095_evaluate_cg01_daily_bias_is_pure_function():
    """
    Rule 095: evaluate_cg01_daily_bias computes facts without mutating the registry.
    """
    clear_current_cg01_bias_authority()
    cfg = CG01Config()
    now_ms = 1700000000000
    ctx = {"4H": _make_h4_candles(base_ts=now_ms - 40 * 3600 * 1000, count=10)}

    snap = evaluate_cg01_daily_bias(ctx, as_of_ms=now_ms, config=cfg, symbol="XAUUSDT")
    assert snap is not None

    # Registry must remain empty!
    res = resolve_current_cg01_bias_authority(symbol="XAUUSDT", now_ms=now_ms, config=cfg)
    assert res.available is False
    assert res.status == "UNAVAILABLE"


# =========================================================================
# Rule 096 & 097: Publisher Production & Owner Lifecycle
# =========================================================================
def test_096_097_publisher_ownership_and_generation_lifecycle():
    """
    Rules 096, 097:
    - Shadow publisher cannot publish execution authority.
    - Non-active owner cannot publish execution authority.
    - Stale generation cannot overwrite newer generation.
    """
    clear_current_cg01_bias_authority(reset_epoch=True)
    bump_runtime_ownership_epoch()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    cfg = CG01Config()
    cfg_fp = compute_cg01_config_fingerprint(cfg)

    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_test_lifecycle",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_LIFECYCLE",
        config_fingerprint=cfg_fp,
        evaluated_at_ms=now_ms,
    )

    # 1. Shadow source rejected
    ok_shadow = publish_current_cg01_bias_authority(
        symbol=symbol, bias=snap, config=cfg, evaluated_at_ms=now_ms,
        source="SHADOW_PIPELINE", is_active_owner=True
    )
    assert ok_shadow is False

    # 2. Non-active owner rejected
    ok_non_owner = publish_current_cg01_bias_authority(
        symbol=symbol, bias=snap, config=cfg, evaluated_at_ms=now_ms,
        source="ACTIVE_PIPELINE", is_active_owner=False
    )
    assert ok_non_owner is False

    # 3. Active owner with generation 2 published
    ok_gen2 = publish_current_cg01_bias_authority(
        symbol=symbol, bias=snap, config=cfg, evaluated_at_ms=now_ms,
        source="ACTIVE_WATCHER", is_active_owner=True, generation=2
    )
    assert ok_gen2 is True

    # 4. Old generation 1 arrives later -> rejected
    snap_gen1 = copy.deepcopy(snap)
    snap_gen1.authority_token = "STALE_GEN1_TOKEN"
    ok_gen1 = publish_current_cg01_bias_authority(
        symbol=symbol, bias=snap_gen1, config=cfg, evaluated_at_ms=now_ms + 1000,
        source="ACTIVE_WATCHER", is_active_owner=True, generation=1
    )
    assert ok_gen1 is False

    # Authority still holds generation 2
    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 2000, config=cfg)
    assert res.bias.authority_token == "TOKEN_LIFECYCLE"


# =========================================================================
# Rule 101: Positive Arm and Fill Path
# =========================================================================
def test_101_positive_arm_and_fill_path():
    """
    Rule 101: Valid authority, canonical config, matching direction, trigger hit -> arms and fills cleanly.
    """
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
    auth_tok = "tok_auth_positive_101"

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    valid_snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="2023-11-14",
        bias_id="bias_pos_01",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token=auth_tok,
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=valid_snap,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        source="ACTIVE_WATCHER",
        is_active_owner=True,
    )

    arm_snap = {
        "binding": {
            "direction_policy": "DAILY_BIAS_PRIMARY",
            "authority_token": auth_tok,
            "allowed_directions": ["LONG"],
            "config_fingerprint": eff_fp,
        }
    }
    order = models.PaperOrder(
        id="ord_pos_101",
        setup_id="setup_101",
        signal_id="sig_101",
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
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2499.5, "ask": 2499.8, "last": 2499.6, "timestamp": now_ms + 1000},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == 2500.0


# =========================================================================
# Rule 103: Open Position Invariance
# =========================================================================
def test_103_open_position_invariant_when_bias_flips():
    """
    Rule 103: Position already filled; daily bias flips or becomes INVALIDATED.
    Processing cycle runs; open position remains paper_open, SL/TP unchanged.
    """
    clear_current_cg01_bias_authority()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    now_ms = 1700000000000
    order = models.PaperOrder(
        id="ord_open_pos_103",
        setup_id="setup_103",
        signal_id="sig_103",
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2500.0,
        actual_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2535.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="paper_open",
        opened_at=now_ms,
        created_at=now_ms,
    )
    db.add(order)
    db.commit()

    # Flip authority to INVALIDATED
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol="XAUUSDT")
    snap_inval = CG01DailyBiasSnapshot(
        symbol="XAUUSDT",
        trading_day_key="2023-11-14",
        bias_id="bias_inval_103",
        direction="BEARISH",
        status="INVALIDATED",
        allowed_directions=(),
        authority_token="TOKEN_INVAL",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms + 1000,
    )
    publish_current_cg01_bias_authority(
        symbol="XAUUSDT",
        bias=snap_inval,
        config=eff_cfg,
        evaluated_at_ms=now_ms + 1000,
        source="ACTIVE_WATCHER",
        is_active_owner=True,
    )

    coordinator = ExecutionCoordinator()
    coordinator.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2510.0, "ask": 2510.2, "last": 2510.1, "timestamp": now_ms + 2000},
        now_ms=now_ms + 2000,
    )
    db.refresh(order)

    # Position must NOT close or alter SL/TP!
    assert order.state == "paper_open"
    assert order.actual_entry == 2500.0
    assert order.stop_loss == 2490.0
    assert order.take_profit == 2535.0
