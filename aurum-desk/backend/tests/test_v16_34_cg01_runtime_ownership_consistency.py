"""
backend/tests/test_v16_34_cg01_runtime_ownership_consistency.py
V16.34 — Final Runtime Ownership & Authority Consistency Repair Test Suite for CG-01.

Validates Rules 001 - 100:
1. Scheduler owner publishes execution authority via production boundary.
2. Watcher owner publishes execution authority when scheduler is not owner.
3. Watchdog and non-owner callers cannot overwrite active authority.
4. Old lease rejected after owner handoff even if registry is empty.
5. Epoch is a true runtime ownership lifecycle counter, not a constant.
6. Historical and replay scopes cannot pollute ACTIVE registry.
7. Missing or empty trading_day_key rejected (MISSING_TRADING_DAY).
8. Snapshot config fingerprint mismatch rejected (CONFIG_FINGERPRINT_MISMATCH).
9. Deepcopy immutability across all resolver branches.
10. Full candle-to-trade PRIMARY LONG via true engine evaluation.
11. Full candle-to-trade PRIMARY SHORT via true engine evaluation.
12. Full TP and SL lifecycle and accounting correctness.
"""
import copy
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session

import models
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series, build_short_fixture_series, make_bar
from tests.test_v16_30_cg01_daily_bias import generate_bullish_candles, generate_bearish_candles
from services.cg01_types import (
    CG01Config,
    CG01DirectionPolicy,
    CG01EntryMode,
    CG01Stage,
    CG01ResearchContext,
)
from services.cg01_daily_bias_types import (
    CG01DailyBiasSnapshot,
    CG01DailyBiasStatus,
)
from services.cg01_config_factory import (
    compute_cg01_config_fingerprint,
    resolve_canonical_cg01_config,
)
from services.cg01_daily_bias_service import (
    publish_current_cg01_bias_authority,
    resolve_current_cg01_bias_authority,
    clear_current_cg01_bias_authority,
    build_cg01_authority_publish_context,
    validate_cg01_bias_authority_binding,
    resolve_trading_day_key,
    get_current_runtime_ownership_epoch,
    bump_runtime_ownership_epoch,
    reset_runtime_ownership_epoch,
)
from services.cg01_scheduler_service import (
    cg01_evaluation_scheduler,
    resolve_entry_execution_owner,
)
from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
from services.trade_lifecycle_service import TradeLifecycleService
from services.execution_coordinator import ExecutionCoordinator
from services.cg01_engine import evaluate_cg01_candidate
from domain_calculator import calculate_risk_reward


@pytest.fixture
def db(isolated_db):
    yield isolated_db


@pytest.fixture(autouse=True)
def clean_registry():
    clear_current_cg01_bias_authority(reset_epoch=True)
    cg01_evaluation_scheduler.reset_state()
    cg01_evaluation_scheduler.enabled = True
    cg01_evaluation_scheduler.is_owner = False
    cg01_evaluation_scheduler.shadow_mode = True
    yield
    clear_current_cg01_bias_authority(reset_epoch=True)
    cg01_evaluation_scheduler.reset_state()


# =========================================================================
# 1. Scheduler Active Owner Publishes via Production Boundary (Rule 071)
# =========================================================================
def test_scheduler_active_owner_publishes_via_production_boundary(db: Session):
    """
    Rule 071:
    When scheduler is active owner (enabled=True, is_owner=True, running=True, shadow_mode=False),
    production evaluation passes caller_identity="SCHEDULER", computes bias, and successfully
    publishes authority under owner="SCHEDULER".
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    # Configure scheduler as active owner
    cg01_evaluation_scheduler.enabled = True
    cg01_evaluation_scheduler.is_owner = True
    cg01_evaluation_scheduler._running = True
    cg01_evaluation_scheduler.shadow_mode = False

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    eff_cfg.direction_policy = CG01DirectionPolicy.DAILY_BIAS_PRIMARY.value
    eff_cfg.profile_mode = "PAPER_EXECUTION"
    eff_fp = compute_cg01_config_fingerprint(eff_cfg)

    # Build context as SCHEDULER
    pub_ctx = build_cg01_authority_publish_context(
        symbol=symbol,
        config=eff_cfg,
        db=db,
        caller_identity="SCHEDULER",
        evaluation_origin="SCHEDULER",
        auto_enabled=True,
        now_ms=now_ms,
    )
    assert pub_ctx["is_active_owner"] is True
    assert pub_ctx["owner"] == "SCHEDULER"
    assert pub_ctx["scope"] == "PRODUCTION"

    # Publish authority
    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_sched_01",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_SCHED_01",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )

    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        source=pub_ctx["source"],
        is_active_owner=pub_ctx["is_active_owner"],
        owner=pub_ctx["owner"],
        generation=pub_ctx["generation"],
    )
    assert ok_pub is True

    # Resolver must resolve ready authority
    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 100, config=eff_cfg)
    assert res.available is True
    assert res.status == "READY"
    assert res.bias.authority_token == "TOKEN_SCHED_01"


# =========================================================================
# 2. Watcher Active Owner Publishes when Scheduler is not Owner (Rule 072)
# =========================================================================
def test_watcher_active_owner_publishes_when_scheduler_is_not_owner(db: Session):
    """
    Rule 072:
    When scheduler is not execution owner, background watcher is active owner
    and publishes authority successfully.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    cg01_evaluation_scheduler.enabled = True
    cg01_evaluation_scheduler.is_owner = False
    cg01_evaluation_scheduler.shadow_mode = True

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    eff_cfg.profile_mode = "PAPER_EXECUTION"
    eff_fp = compute_cg01_config_fingerprint(eff_cfg)

    pub_ctx = build_cg01_authority_publish_context(
        symbol=symbol,
        config=eff_cfg,
        db=db,
        caller_identity="WATCHER",
        evaluation_origin="WATCHER",
        auto_enabled=True,
        now_ms=now_ms,
    )
    assert pub_ctx["is_active_owner"] is True
    assert pub_ctx["owner"] == "WATCHER"

    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_watcher_01",
        direction="BEARISH",
        status="READY",
        allowed_directions=("SHORT",),
        authority_token="TOKEN_WATCHER_01",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )

    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        source=pub_ctx["source"],
        is_active_owner=pub_ctx["is_active_owner"],
        owner=pub_ctx["owner"],
        generation=pub_ctx["generation"],
    )
    assert ok_pub is True

    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 100, config=eff_cfg)
    assert res.available is True
    assert res.bias.authority_token == "TOKEN_WATCHER_01"


# =========================================================================
# 3. Watchdog and Non-Owner Cannot Overwrite Active Authority (Rule 073)
# =========================================================================
def test_watchdog_and_non_owner_cannot_overwrite_active_authority(db: Session):
    """
    Rule 073:
    - When scheduler is owner, watcher watchdog CANNOT overwrite active authority.
    - When watcher is owner, scheduler non-owner CANNOT overwrite active authority.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    eff_cfg.profile_mode = "PAPER_EXECUTION"
    eff_fp = compute_cg01_config_fingerprint(eff_cfg)

    # Part A: Scheduler is active owner
    cg01_evaluation_scheduler.enabled = True
    cg01_evaluation_scheduler.is_owner = True
    cg01_evaluation_scheduler._running = True
    cg01_evaluation_scheduler.shadow_mode = False

    snap_sched = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_sched_a",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_SCHED_AUTH_A",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    pub_ctx_sched = build_cg01_authority_publish_context(
        symbol=symbol, config=eff_cfg, db=db, caller_identity="SCHEDULER", auto_enabled=True, now_ms=now_ms
    )
    ok_sched = publish_current_cg01_bias_authority(
        symbol=symbol, bias=snap_sched, config=eff_cfg, evaluated_at_ms=now_ms,
        source=pub_ctx_sched["source"], is_active_owner=pub_ctx_sched["is_active_owner"],
        owner=pub_ctx_sched["owner"], generation=pub_ctx_sched["generation"]
    )
    assert ok_sched is True

    # Watcher acting as watchdog tries to publish
    pub_ctx_watchdog = build_cg01_authority_publish_context(
        symbol=symbol, config=eff_cfg, db=db, caller_identity="WATCHER", auto_enabled=True, now_ms=now_ms + 100
    )
    assert pub_ctx_watchdog["is_active_owner"] is False
    assert pub_ctx_watchdog["owner"] == "WATCHDOG"

    snap_watchdog = copy.deepcopy(snap_sched)
    snap_watchdog.authority_token = "POISON_WATCHDOG_TOKEN"
    ok_wd = publish_current_cg01_bias_authority(
        symbol=symbol, bias=snap_watchdog, config=eff_cfg, evaluated_at_ms=now_ms + 100,
        source=pub_ctx_watchdog["source"], is_active_owner=pub_ctx_watchdog["is_active_owner"],
        owner=pub_ctx_watchdog["owner"], generation=pub_ctx_watchdog["generation"]
    )
    assert ok_wd is False

    # Authority still holds scheduler token
    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 200, config=eff_cfg)
    assert res.bias.authority_token == "TOKEN_SCHED_AUTH_A"


# =========================================================================
# 4. Old Lease Rejected After Owner Handoff Even if Registry is Empty (Rule 074, 075)
# =========================================================================
def test_owner_handoff_rejects_old_lease_even_if_registry_empty(db: Session):
    """
    Rule 074, 075:
    - Caller A captures lease at epoch 1 under Owner A.
    - Owner handoff occurs to Owner B (epoch advances to 2).
    - Registry is cleared/empty.
    - Caller A attempts to publish using old lease 1.
    - MUST be rejected even without a current entry in registry!
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    eff_cfg.profile_mode = "PAPER_EXECUTION"
    eff_fp = compute_cg01_config_fingerprint(eff_cfg)

    # 1. Watcher captures lease at epoch 1
    lease_a = build_cg01_authority_publish_context(
        symbol=symbol, config=eff_cfg, db=db, caller_identity="WATCHER", auto_enabled=True, now_ms=now_ms
    )
    assert lease_a["generation"] == 1
    assert lease_a["owner"] == "WATCHER"

    # 2. Handoff occurs: scheduler promoted to owner
    cg01_evaluation_scheduler.set_flags(is_owner=True, shadow_mode=False)
    cg01_evaluation_scheduler._running = True
    epoch_after = get_current_runtime_ownership_epoch()
    assert epoch_after > lease_a["generation"]

    # 3. Registry is cleared
    clear_current_cg01_bias_authority(symbol=symbol)

    # 4. Caller A tries to publish using lease_a (generation 1 < epoch_after)
    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_stale_lease",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="STALE_LEASE_TOKEN",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms + 500,
    )
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap,
        config=eff_cfg,
        evaluated_at_ms=now_ms + 500,
        source=lease_a["source"],
        is_active_owner=lease_a["is_active_owner"],
        owner=lease_a["owner"],
        generation=lease_a["generation"],  # 1 < 2!
    )
    assert ok_pub is False, "Publish succeeded with stale lease generation on empty registry!"

    # Resolver must remain UNAVAILABLE
    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 600, config=eff_cfg)
    assert res.available is False
    assert res.status == "UNAVAILABLE"


# =========================================================================
# 5. Historical and Replay Scope Cannot Pollute ACTIVE Registry (Rule 076)
# =========================================================================
def test_historical_and_replay_scope_cannot_pollute_active_registry(db: Session):
    """
    Rule 076:
    Historical / replay runs must not mutate or pollute ACTIVE execution registry.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    # 1. Prepopulate ACTIVE authority token A
    snap_active = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_active_auth",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="ACTIVE_TOKEN_A",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(
        symbol=symbol, bias=snap_active, config=eff_cfg, evaluated_at_ms=now_ms,
        source="ACTIVE_PIPELINE", is_active_owner=True, owner="WATCHER"
    )

    # 2. Historical run with fake past clock
    hist_ctx = CG01ResearchContext(
        trace_id="trc_hist_01",
        run_id="run_hist_replay_01",
        segment_id=None,
        symbol=symbol,
        config_fingerprint=eff_fp,
        input_versions={},
        evaluation_as_of_ms=now_ms - 86400000 * 10,
        observation_mode="HISTORICAL",
    )
    pub_ctx_hist = build_cg01_authority_publish_context(
        symbol=symbol,
        config=eff_cfg,
        research_context=hist_ctx,
        evaluation_origin="HISTORICAL",
        caller_identity="HISTORICAL",
        auto_enabled=False,
        now_ms=now_ms - 86400000 * 10,
    )
    assert pub_ctx_hist["is_active_owner"] is False
    assert "HISTORICAL" in pub_ctx_hist["owner"]

    snap_hist = copy.deepcopy(snap_active)
    snap_hist.authority_token = "HISTORICAL_POISON_TOKEN"
    ok_hist = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap_hist,
        config=eff_cfg,
        evaluated_at_ms=now_ms - 86400000 * 10,
        source=pub_ctx_hist["source"],
        is_active_owner=pub_ctx_hist["is_active_owner"],
        owner=pub_ctx_hist["owner"],
        generation=pub_ctx_hist["generation"],
    )
    assert ok_hist is False

    # ACTIVE registry must remain TOKEN_A
    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 100, config=eff_cfg)
    assert res.available is True
    assert res.bias.authority_token == "ACTIVE_TOKEN_A"


# =========================================================================
# 6. Missing or Empty Trading Day Rejected (Rule 078)
# =========================================================================
def test_missing_or_empty_trading_day_rejected(db: Session):
    """
    Rule 078:
    Snapshot with status READY but missing/empty trading_day_key must be rejected
    by publisher or returned as MISSING_TRADING_DAY by resolver, blocking ARM and FILL.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    snap_no_day = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key="",  # EMPTY!
        bias_id="bias_no_day",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_NO_DAY",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )

    # Publisher rejects READY snapshot missing trading day
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol, bias=snap_no_day, config=eff_cfg, evaluated_at_ms=now_ms
    )
    assert ok_pub is False

    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 100, config=eff_cfg)
    assert res.available is False

    # Also test validator helper directly with missing trading day snapshot
    is_valid, code, msg, action = validate_cg01_bias_authority_binding(
        candidate_bias=snap_no_day.to_dict(),
        current_authority=None,
        order_direction="LONG",
        symbol=symbol,
        effective_fingerprint=eff_fp,
    )
    assert is_valid is False
    assert action in ("DEFER", "INVALIDATE", "BLOCK")


# =========================================================================
# 7. Snapshot Config Fingerprint Mismatch Rejected (Rule 079)
# =========================================================================
def test_snapshot_config_fingerprint_mismatch_rejected(db: Session):
    """
    Rule 079:
    When config has fingerprint A and explicit fp A, but snapshot inside has
    fingerprint B, publisher rejects or resolver returns CONFIG_FINGERPRINT_MISMATCH.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    snap_mismatch = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_mismatch_fp",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_MISMATCH_FP",
        config_fingerprint="FINGERPRINT_B_OTHER",  # Contradicts eff_fp!
        evaluated_at_ms=now_ms,
    )

    # Publisher must reject snapshot fingerprint contradiction
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap_mismatch,
        config=eff_cfg,
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    assert ok_pub is False

    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 100, config=eff_cfg)
    assert res.available is False


# =========================================================================
# 8. Deepcopy Immutability Across All Resolver Branches (Rule 081)
# =========================================================================
def test_registry_deep_immutability_across_all_branches(db: Session):
    """
    Rule 081:
    Mutating returned bias objects on READY or unavailable branches must not mutate
    the registry entry.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_immut_01",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_ORIGINAL",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap, config=eff_cfg, evaluated_at_ms=now_ms)

    # 1. Mutate original object
    snap.authority_token = "POISON_ORIGINAL"

    # 2. Resolve READY and mutate
    res1 = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 100, config=eff_cfg)
    assert res1.available is True
    assert res1.bias.authority_token == "TOKEN_ORIGINAL"
    res1.bias.authority_token = "POISON_RESOLVED_READY"

    # 3. Resolve again -> still pristine
    res2 = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 200, config=eff_cfg)
    assert res2.bias.authority_token == "TOKEN_ORIGINAL"

    # 4. Resolve on error/mismatch branch and mutate returned bias
    cfg_diff = CG01Config(timing_profile="LONDON")
    res_err = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 300, config=cfg_diff)
    assert res_err.available is False
    assert res_err.bias is not None
    res_err.bias.authority_token = "POISON_RESOLVED_ERR"

    # 5. Resolve with correct config again -> still pristine
    res3 = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms + 400, config=eff_cfg)
    assert res3.bias.authority_token == "TOKEN_ORIGINAL"


# =========================================================================
# 9. Engine Integration with Injected POI/Retest LONG (Rule 056)
# =========================================================================
def test_engine_integration_with_injected_poi_retest_long(db: Session, monkeypatch):
    """
    Rule 056:
    Engine integration with injected POI/retest (LONG):
    Validates candidate generation, authority publishing, ARM, and fill with isolated component mocks.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    db.query(models.PaperOrder).delete()
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000
    day_key = resolve_trading_day_key(now_ms)

    for k, v in [
        ("scalp_m1_mode", "PAPER"),
        ("cg01_enabled", "true"),
        ("cg01_profile_mode", "PAPER_EXECUTION"),
        ("execution_profile", "CG01"),
        ("cg01_direction_policy", "DAILY_BIAS_PRIMARY"),
    ]:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if not row:
            db.add(models.SystemConfig(key=k, value=v, updated_at=now_ms))
        else:
            row.value = v
            row.updated_at = now_ms
    db.commit()

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    _, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    c_4h = generate_bullish_candles(base_time=t_start - 16 * 14_400_000, tf="4H")

    from services.zone_watchlist_service import WatchedZone, ZoneWatchlistService
    from services.cg01_context_adapter import CG01RetestResult
    import services.cg01_engine as cg01_engine

    poi_long = WatchedZone(
        zone_id="POI_LONG_ENGINE",
        symbol=symbol,
        timeframe="15M",
        zone_type="OB",
        direction="LONG",
        top=2623.8,
        bottom=2620.2,
        created_at=t_start,
        known_at=t_start,
        touch_count=1,
    )
    monkeypatch.setattr(ZoneWatchlistService, "extract_zones_from_candles", lambda *a, **k: [poi_long])
    monkeypatch.setattr(
        cg01_engine,
        "resolve_cg01_retest",
        lambda *a, **k: CG01RetestResult(
            confirmed=True, reason="CONFIRMED_RETEST", candle_id="c_long_ret",
            occurred_at=now_ms - 30000, known_at=now_ms, touch_price=2623.75, close_price=2624.2
        )
    )

    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m, "context_source": "TEST_ENGINE"}
    quote = {"bid": 2623.75, "ask": 2623.85, "last": 2623.80, "timestamp": now_ms, "is_valid": True, "is_stale": False}

    # Evaluate via production engine
    dec = evaluate_cg01_candidate(ctx, quote, config=eff_cfg, now_ms=now_ms, capital=1000.0, risk_pct=0.25)
    assert dec.stage == CG01Stage.EXECUTABLE
    assert dec.candidate is not None
    assert dec.candidate.direction == "LONG"
    assert dec.daily_bias is not None

    # Owner publishes daily bias authority
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=dec.daily_bias,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        source="ACTIVE_PIPELINE",
        is_active_owner=True,
        owner="WATCHER",
    )
    assert ok_pub is True

    # Arm candidate via orchestrator
    policy_settings = {
        "capital_usdt": 1000.0,
        "risk_pct": 0.25,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "capital_source": "INITIAL_CAPITAL_FIXED",
    }
    from services.scalp_m1_types import M1ScalpConfig
    scalp_cfg = M1ScalpConfig(cg01_config=eff_cfg)
    armed_order = scalp_watcher_orchestrator._arm_paper_candidate(
        db,
        dec.candidate,
        now_ms=now_ms,
        clock=None,
        quote=quote,
        policy_settings=policy_settings,
        config=scalp_cfg,
        auto_enabled=True,
    )
    assert armed_order is not None
    assert armed_order.state == "armed"
    assert armed_order.direction == "LONG"

    # Fill order via TradeLifecycleService
    res_fill = TradeLifecycleService.execute_fill(
        db,
        order=armed_order,
        fill_price=armed_order.planned_entry,
        calc_result=dec.candidate.calculation,
        now_ms=now_ms + 1000,
    )
    db.refresh(armed_order)
    assert armed_order.state == "paper_open"
    assert armed_order.actual_entry == armed_order.planned_entry


# =========================================================================
# 10. Engine Integration with Injected POI/Retest SHORT (Rule 056)
# =========================================================================
def test_engine_integration_with_injected_poi_retest_short(db: Session, monkeypatch):
    """
    Rule 056:
    Engine integration with injected POI/retest (SHORT):
    Validates SHORT candidate generation, authority publishing, ARM, and fill with isolated component mocks.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    db.query(models.PaperOrder).delete()
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000
    day_key = resolve_trading_day_key(now_ms)

    for k, v in [
        ("scalp_m1_mode", "PAPER"),
        ("cg01_enabled", "true"),
        ("cg01_profile_mode", "PAPER_EXECUTION"),
        ("execution_profile", "CG01"),
        ("cg01_direction_policy", "DAILY_BIAS_PRIMARY"),
    ]:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if not row:
            db.add(models.SystemConfig(key=k, value=v, updated_at=now_ms))
        else:
            row.value = v
            row.updated_at = now_ms
    db.commit()

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    _, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    c_4h = generate_bearish_candles(base_time=t_start - 16 * 14_400_000, tf="4H")

    from services.zone_watchlist_service import WatchedZone, ZoneWatchlistService
    from services.cg01_context_adapter import CG01RetestResult
    import services.cg01_engine as cg01_engine

    poi_short = WatchedZone(
        zone_id="POI_SHORT_ENGINE",
        symbol=symbol,
        timeframe="15M",
        zone_type="OB",
        direction="SHORT",
        top=2655.0,
        bottom=2650.0,
        created_at=t_start,
        known_at=t_start,
        touch_count=1,
    )
    monkeypatch.setattr(ZoneWatchlistService, "extract_zones_from_candles", lambda *a, **k: [poi_short])
    monkeypatch.setattr(
        cg01_engine,
        "resolve_cg01_retest",
        lambda *a, **k: CG01RetestResult(
            confirmed=True, reason="CONFIRMED_RETEST", candle_id="c_short_ret",
            occurred_at=now_ms - 30000, known_at=now_ms, touch_price=2651.15, close_price=2650.8
        )
    )

    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m, "context_source": "TEST_ENGINE"}
    quote = {"bid": 2651.10, "ask": 2651.20, "last": 2651.15, "timestamp": now_ms, "is_valid": True, "is_stale": False}

    dec = evaluate_cg01_candidate(ctx, quote, config=eff_cfg, now_ms=now_ms, capital=1000.0, risk_pct=0.25)
    assert dec.stage == CG01Stage.EXECUTABLE
    assert dec.candidate is not None
    assert dec.candidate.direction == "SHORT"
    assert dec.candidate.stop_loss > dec.candidate.planned_entry > dec.candidate.take_profit

    # Publish authority
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=dec.daily_bias,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        source="ACTIVE_PIPELINE",
        is_active_owner=True,
        owner="WATCHER",
    )
    assert ok_pub is True

    # Arm candidate
    policy_settings = {
        "capital_usdt": 1000.0,
        "risk_pct": 0.25,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "capital_source": "INITIAL_CAPITAL_FIXED",
    }
    from services.scalp_m1_types import M1ScalpConfig
    scalp_cfg = M1ScalpConfig(cg01_config=eff_cfg)
    armed_order = scalp_watcher_orchestrator._arm_paper_candidate(
        db,
        dec.candidate,
        now_ms=now_ms,
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

    # Fill order
    TradeLifecycleService.execute_fill(
        db,
        order=armed_order,
        fill_price=armed_order.planned_entry,
        calc_result=dec.candidate.calculation,
        now_ms=now_ms + 1000,
    )
    db.refresh(armed_order)
    assert armed_order.state == "paper_open"
    assert armed_order.actual_entry == armed_order.planned_entry


# =========================================================================
# 11. Full TP and SL Lifecycle and Accounting Correctness (Rule 054, 085)
# =========================================================================
def test_full_close_tp_and_sl_financial_correctness(db: Session):
    """
    Rule 054, 085:
    - Uses production "binding" key in arm_decision_snapshot.
    - Demonstrates negative control: PRIMARY guard halts fill when authority is missing.
    - Demonstrates positive control: fill succeeds with valid authority.
    - Closes at TP and SL with exact net PnL and fee accounting verification.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    for k, v in [
        ("scalp_m1_mode", "PAPER"),
        ("cg01_enabled", "true"),
        ("cg01_profile_mode", "PAPER_EXECUTION"),
        ("execution_profile", "CG01"),
        ("cg01_direction_policy", "DAILY_BIAS_PRIMARY"),
    ]:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if not row:
            db.add(models.SystemConfig(key=k, value=v, updated_at=now_ms))
        else:
            row.value = v
            row.updated_at = now_ms
    db.commit()

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    calc = calculate_risk_reward(
        direction="LONG",
        entry=2500.0,
        sl=2490.0,
        tp=2535.0,
        capital=1000.0,
        risk_pct=0.25,
    )

    # 1. Test Take-Profit Close
    order_tp = models.PaperOrder(
        id="ord_tp_test",
        setup_id="setup_tp",
        signal_id="sig_tp",
        instrument=symbol,
        direction="LONG",
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2535.0,
        quantity=calc.quantity,
        initial_risk_usdt=calc.net_risk_usdt,
        state="armed",
        order_type="LIMIT",
        created_at=now_ms,
        arm_decision_snapshot=json.dumps({
            "binding": {
                "binding_version": 2,
                "direction_policy": "DAILY_BIAS_PRIMARY",
                "authority_token": "TOKEN_TP_SL",
                "allowed_directions": ["LONG"],
                "config_fingerprint": eff_fp,
                "status": "READY",
                "symbol": symbol,
            }
        }),
    )
    db.add(order_tp)
    db.commit()

    # Negative control: authority is NOT published yet -> execute_fill must NOT fill
    TradeLifecycleService.execute_fill(db, order=order_tp, fill_price=2500.0, calc_result=calc, now_ms=now_ms + 500)
    db.refresh(order_tp)
    assert order_tp.state == "armed", "Order filled despite missing PRIMARY authority!"

    # Positive control: publish valid authority
    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_tp_sl",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_TP_SL",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    ok_pub = publish_current_cg01_bias_authority(symbol=symbol, bias=snap, config=eff_cfg, evaluated_at_ms=now_ms)
    assert ok_pub is True

    TradeLifecycleService.execute_fill(db, order=order_tp, fill_price=2500.0, calc_result=calc, now_ms=now_ms + 1000)
    db.refresh(order_tp)
    assert order_tp.state == "paper_open"

    TradeLifecycleService.execute_close(
        db,
        order_id=order_tp.id,
        exit_price=2535.0,
        exit_cause="TP_HIT",
        now_ms=now_ms + 5000,
    )
    db.refresh(order_tp)
    assert order_tp.state == "closed"
    assert order_tp.actual_exit == 2535.0
    assert order_tp.exit_cause == "TP_HIT"
    # Gross = (2535 - 2500) * 0.2 = 7.0. Fees = 2500*0.2*0.0004 + 2535*0.2*0.0004 = 0.4028. Net = 6.5972 -> 6.60
    assert order_tp.realized_pnl_net == pytest.approx(6.60, abs=0.01)
    assert order_tp.realized_r > 0.0

    # 2. Test Stop-Loss Close
    order_sl = models.PaperOrder(
        id="ord_sl_test",
        setup_id="setup_sl",
        signal_id="sig_sl",
        instrument=symbol,
        direction="LONG",
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2535.0,
        quantity=calc.quantity,
        initial_risk_usdt=calc.net_risk_usdt,
        state="armed",
        order_type="LIMIT",
        created_at=now_ms,
        arm_decision_snapshot=json.dumps({
            "binding": {
                "binding_version": 2,
                "direction_policy": "DAILY_BIAS_PRIMARY",
                "authority_token": "TOKEN_TP_SL",
                "allowed_directions": ["LONG"],
                "config_fingerprint": eff_fp,
                "status": "READY",
                "symbol": symbol,
            }
        }),
    )
    db.add(order_sl)
    db.commit()

    TradeLifecycleService.execute_fill(db, order=order_sl, fill_price=2500.0, calc_result=calc, now_ms=now_ms + 1000)
    db.refresh(order_sl)
    assert order_sl.state == "paper_open"

    TradeLifecycleService.execute_close(
        db,
        order_id=order_sl.id,
        exit_price=2490.0,
        exit_cause="SL_HIT",
        now_ms=now_ms + 6000,
    )
    db.refresh(order_sl)
    assert order_sl.state == "closed"
    assert order_sl.actual_exit == 2490.0
    assert order_sl.exit_cause == "SL_HIT"
    # Gross = (2490 - 2500) * 0.2 = -2.0. Fees = 2500*0.2*0.0004 + 2490*0.2*0.0004 = 0.3992. Net = -2.3992 -> -2.40
    assert order_sl.realized_pnl_net == pytest.approx(-2.40, abs=0.01)
    assert order_sl.realized_r < 0.0


# =========================================================================
# 12. Full Candle-to-Trade Primary LONG Unmocked Recognition (Rules 057, 061)
# =========================================================================
def test_candle_to_trade_primary_long_true_engine_unmocked_recognition(db: Session):
    """
    Rules 057, 061:
    Pure candle fixtures without monkeypatching ZoneWatchlistService or resolve_cg01_retest.
    Evaluates candidate -> arms order -> coordinator fills order.
    Asserts: SL < planned_entry < TP, exact net RR, single fill.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    db.query(models.PaperOrder).delete()
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    for k, v in [
        ("scalp_m1_mode", "PAPER"),
        ("cg01_enabled", "true"),
        ("cg01_profile_mode", "PAPER_EXECUTION"),
        ("execution_profile", "CG01"),
        ("cg01_direction_policy", "DAILY_BIAS_PRIMARY"),
    ]:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if not row:
            db.add(models.SystemConfig(key=k, value=v, updated_at=now_ms))
        else:
            row.value = v
            row.updated_at = now_ms
    db.commit()

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    _, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    c_4h = generate_bullish_candles(base_time=t_start - 16 * 14_400_000, tf="4H")
    # Increase 15M bar 20 high for target RR >= 1.5
    c_15m[20] = make_bar(c_15m[20]["timestamp"], 2638.0, 2645.0, 2636.0, 2642.0)
    # Append real retest bar touching CE (2623.6) and closing above CE (2624.2)
    c_1m.append(make_bar(t_start + 540_000, 2625.8, 2626.0, 2623.6, 2624.2))

    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m, "context_source": "TEST_ENGINE"}
    quote = {"bid": 2623.75, "ask": 2623.85, "last": 2623.80, "timestamp": now_ms, "is_valid": True, "is_stale": False}

    # Evaluate candidate with ZERO monkeypatches on zone or retest logic
    dec = evaluate_cg01_candidate(ctx, quote, config=eff_cfg, now_ms=now_ms, capital=1000.0, risk_pct=0.25)
    assert dec.stage == CG01Stage.EXECUTABLE
    assert dec.candidate is not None
    assert dec.candidate.direction == "LONG"
    assert dec.candidate.stop_loss < dec.candidate.planned_entry < dec.candidate.take_profit
    assert dec.candidate.net_rr >= 1.5
    assert dec.candidate.planned_entry == 2623.75

    # Publish authority
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=dec.daily_bias,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        source="ACTIVE_PIPELINE",
        is_active_owner=True,
        owner="WATCHER",
    )
    assert ok_pub is True

    # Arm candidate
    policy_settings = {
        "capital_usdt": 1000.0,
        "risk_pct": 0.25,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "capital_source": "INITIAL_CAPITAL_FIXED",
    }
    from services.scalp_m1_types import M1ScalpConfig
    scalp_cfg = M1ScalpConfig(cg01_config=eff_cfg)
    armed_order = scalp_watcher_orchestrator._arm_paper_candidate(
        db,
        dec.candidate,
        now_ms=now_ms,
        clock=None,
        quote=quote,
        policy_settings=policy_settings,
        config=scalp_cfg,
        auto_enabled=True,
    )
    assert armed_order is not None
    assert armed_order.state == "armed"
    assert armed_order.direction == "LONG"
    assert armed_order.stop_loss < armed_order.planned_entry < armed_order.take_profit

    # Coordinator quote-to-fill: Ask touches or dips below planned_entry (2623.75)
    coord = ExecutionCoordinator()
    coord.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2623.65, "ask": 2623.75, "last": 2623.70, "timestamp": now_ms + 1000, "is_valid": True, "is_stale": False},
        now_ms=now_ms + 1000,
    )
    db.refresh(armed_order)
    assert armed_order.state == "paper_open"
    assert armed_order.actual_entry == armed_order.planned_entry


# =========================================================================
# 13. Full Candle-to-Trade Primary SHORT Unmocked Recognition (Rules 057, 062)
# =========================================================================
def test_candle_to_trade_primary_short_true_engine_unmocked_recognition(db: Session):
    """
    Rules 057, 062:
    Pure candle fixtures without monkeypatching ZoneWatchlistService or resolve_cg01_retest.
    Evaluates candidate -> arms order -> coordinator fills order.
    Asserts: SL > planned_entry > TP, exact net RR, single fill.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    db.query(models.PaperOrder).delete()
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    for k, v in [
        ("scalp_m1_mode", "PAPER"),
        ("cg01_enabled", "true"),
        ("cg01_profile_mode", "PAPER_EXECUTION"),
        ("execution_profile", "CG01"),
        ("cg01_direction_policy", "DAILY_BIAS_PRIMARY"),
    ]:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if not row:
            db.add(models.SystemConfig(key=k, value=v, updated_at=now_ms))
        else:
            row.value = v
            row.updated_at = now_ms
    db.commit()

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    _, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    c_4h = generate_bearish_candles(base_time=t_start - 16 * 14_400_000, tf="4H")
    # Append real retest bar touching CE (2651.2)
    c_1m.append(make_bar(t_start + 540_000, 2649.8, 2651.25, 2648.5, 2651.0))

    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m, "context_source": "TEST_ENGINE"}
    quote = {"bid": 2651.10, "ask": 2651.20, "last": 2651.15, "timestamp": now_ms, "is_valid": True, "is_stale": False}

    dec = evaluate_cg01_candidate(ctx, quote, config=eff_cfg, now_ms=now_ms, capital=1000.0, risk_pct=0.25)
    assert dec.stage == CG01Stage.EXECUTABLE
    assert dec.candidate is not None
    assert dec.candidate.direction == "SHORT"
    assert dec.candidate.stop_loss > dec.candidate.planned_entry > dec.candidate.take_profit
    assert dec.candidate.net_rr >= 1.5
    assert dec.candidate.planned_entry == 2651.15

    # Publish authority
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=dec.daily_bias,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        source="ACTIVE_PIPELINE",
        is_active_owner=True,
        owner="WATCHER",
    )
    assert ok_pub is True

    # Arm candidate
    policy_settings = {
        "capital_usdt": 1000.0,
        "risk_pct": 0.25,
        "capital_mode": "FIXED_INITIAL_CAPITAL",
        "capital_source": "INITIAL_CAPITAL_FIXED",
    }
    from services.scalp_m1_types import M1ScalpConfig
    scalp_cfg = M1ScalpConfig(cg01_config=eff_cfg)
    armed_order = scalp_watcher_orchestrator._arm_paper_candidate(
        db,
        dec.candidate,
        now_ms=now_ms,
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

    # Coordinator quote-to-fill: Bid touches or rises above planned_entry (2651.15)
    coord = ExecutionCoordinator()
    coord.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2651.15, "ask": 2651.25, "last": 2651.20, "timestamp": now_ms + 1000, "is_valid": True, "is_stale": False},
        now_ms=now_ms + 1000,
    )
    db.refresh(armed_order)
    assert armed_order.state == "paper_open"
    assert armed_order.actual_entry == armed_order.planned_entry


# =========================================================================
# 14. Negative Retest Control Unmocked (Rule 063)
# =========================================================================
def test_negative_retest_control_unmocked(db: Session):
    """
    Rule 063:
    Using positive LONG fixture with entry_mode=CONFIRMED_RETEST_MARKET,
    but retest bar does NOT touch CE (stays high).
    Asserts candidate is NOT EXECUTABLE (unconfirmed retest).
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    eff_cfg, _, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    eff_cfg.entry_mode = CG01EntryMode.CONFIRMED_RETEST_MARKET.value
    _, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    c_4h = generate_bullish_candles(base_time=t_start - 16 * 14_400_000, tf="4H")
    c_15m[20] = make_bar(c_15m[20]["timestamp"], 2638.0, 2645.0, 2636.0, 2642.0)
    # Retest bar low is 2625.0, never touching CE (2623.75)
    c_1m.append(make_bar(t_start + 540_000, 2625.8, 2626.0, 2625.0, 2625.5))

    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m, "context_source": "TEST_ENGINE"}
    quote = {"bid": 2625.4, "ask": 2625.5, "last": 2625.45, "timestamp": now_ms, "is_valid": True, "is_stale": False}

    dec = evaluate_cg01_candidate(ctx, quote, config=eff_cfg, now_ms=now_ms, capital=1000.0, risk_pct=0.25)
    assert dec.stage != CG01Stage.EXECUTABLE
    assert dec.candidate is None


# =========================================================================
# 15. Negative POI Control Unmocked (Rule 064)
# =========================================================================
def test_negative_poi_control_unmocked(db: Session):
    """
    Rule 064:
    Using positive LONG fixture, but 15M candles have no valid POI/OB.
    Asserts candidate is NOT EXECUTABLE.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    now_ms = t_start + 540_000

    eff_cfg, _, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    _, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    c_4h = generate_bullish_candles(base_time=t_start - 16 * 14_400_000, tf="4H")
    # Neutralize 15M bars to flat candles with no OB/FVG
    flat_15m = [make_bar(c["timestamp"], 2630.0, 2630.5, 2629.5, 2630.0) for c in c_15m]

    ctx = {"4H": c_4h, "1H": c_1h, "15M": flat_15m, "1M": c_1m, "context_source": "TEST_ENGINE"}
    quote = {"bid": 2630.0, "ask": 2630.1, "last": 2630.05, "timestamp": now_ms, "is_valid": True, "is_stale": False}

    dec = evaluate_cg01_candidate(ctx, quote, config=eff_cfg, now_ms=now_ms, capital=1000.0, risk_pct=0.25)
    assert dec.stage != CG01Stage.EXECUTABLE
    assert dec.candidate is None


# =========================================================================
# 16. Coordinator Entry Not Touched Leaves Order Pending (Rule 066)
# =========================================================================
def test_coordinator_entry_not_touched_leaves_order_pending(db: Session):
    """
    Rule 066:
    When quote does not touch entry, coordinator leaves order in 'armed' state.
    Zero fills, zero fees paid.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    for k, v in [
        ("scalp_m1_mode", "PAPER"),
        ("cg01_enabled", "true"),
        ("cg01_profile_mode", "PAPER_EXECUTION"),
        ("execution_profile", "CG01"),
        ("cg01_direction_policy", "DAILY_BIAS_PRIMARY"),
    ]:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if not row:
            db.add(models.SystemConfig(key=k, value=v, updated_at=now_ms))
        else:
            row.value = v
            row.updated_at = now_ms
    db.commit()

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_pending",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_PENDING",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap, config=eff_cfg, evaluated_at_ms=now_ms)

    order = models.PaperOrder(
        id="ord_pending_test",
        instrument=symbol,
        direction="LONG",
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2535.0,
        quantity=0.2,
        initial_risk_usdt=2.0,
        state="armed",
        order_type="LIMIT",
        created_at=now_ms,
        arm_decision_snapshot=json.dumps({
            "binding": {
                "binding_version": 2,
                "direction_policy": "DAILY_BIAS_PRIMARY",
                "authority_token": "TOKEN_PENDING",
                "allowed_directions": ["LONG"],
                "config_fingerprint": eff_fp,
                "status": "READY",
                "symbol": symbol,
            }
        }),
    )
    db.add(order)
    db.commit()

    # Quote ask is 2505.0 > planned_entry (2500.0) -> limit order NOT reached!
    coord = ExecutionCoordinator()
    coord.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2504.8, "ask": 2505.0, "last": 2504.9, "timestamp": now_ms + 1000, "is_valid": True, "is_stale": False},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state == "armed"
    assert order.actual_entry is None
    assert order.opened_at is None
    assert order.realized_pnl_net is None


# =========================================================================
# 17. Coordinator Primary Bias Invalid Defers Fill (Rule 067)
# =========================================================================
def test_coordinator_primary_bias_invalid_defers_fill(db: Session):
    """
    Rule 067:
    When quote touches entry but daily bias authority is invalid/missing,
    coordinator does NOT fill the PRIMARY order.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    for k, v in [
        ("scalp_m1_mode", "PAPER"),
        ("cg01_enabled", "true"),
        ("cg01_profile_mode", "PAPER_EXECUTION"),
        ("execution_profile", "CG01"),
        ("cg01_direction_policy", "DAILY_BIAS_PRIMARY"),
    ]:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if not row:
            db.add(models.SystemConfig(key=k, value=v, updated_at=now_ms))
        else:
            row.value = v
            row.updated_at = now_ms
    db.commit()

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    order = models.PaperOrder(
        id="ord_invalid_bias_test",
        instrument=symbol,
        direction="LONG",
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2535.0,
        quantity=0.2,
        initial_risk_usdt=2.0,
        state="armed",
        order_type="LIMIT",
        created_at=now_ms,
        arm_decision_snapshot=json.dumps({
            "binding": {
                "binding_version": 2,
                "direction_policy": "DAILY_BIAS_PRIMARY",
                "authority_token": "TOKEN_UNAVAILABLE",
                "allowed_directions": ["LONG"],
                "config_fingerprint": eff_fp,
                "status": "READY",
                "symbol": symbol,
            }
        }),
    )
    db.add(order)
    db.commit()

    # Quote touches entry (ask = 2500.0), but authority is cleared/missing
    coord = ExecutionCoordinator()
    coord.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2499.8, "ask": 2500.0, "last": 2499.9, "timestamp": now_ms + 1000, "is_valid": True, "is_stale": False},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state != "paper_open", "Order filled despite missing daily bias authority!"


# =========================================================================
# 18. Coordinator Full Lifecycle and Idempotency (Rule 069)
# =========================================================================
def test_coordinator_full_lifecycle_and_idempotency(db: Session):
    """
    Rule 069:
    Repeated coordinator evaluation does not produce duplicate fills.
    Repeated close call does not produce duplicate closes or fees.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    for k, v in [
        ("scalp_m1_mode", "PAPER"),
        ("cg01_enabled", "true"),
        ("cg01_profile_mode", "PAPER_EXECUTION"),
        ("execution_profile", "CG01"),
        ("cg01_direction_policy", "DAILY_BIAS_PRIMARY"),
    ]:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if not row:
            db.add(models.SystemConfig(key=k, value=v, updated_at=now_ms))
        else:
            row.value = v
            row.updated_at = now_ms
    db.commit()

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_idem",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_IDEM",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap, config=eff_cfg, evaluated_at_ms=now_ms)

    order = models.PaperOrder(
        id="ord_idem_test",
        instrument=symbol,
        direction="LONG",
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2535.0,
        quantity=0.2,
        initial_risk_usdt=2.0,
        state="armed",
        order_type="LIMIT",
        created_at=now_ms,
        arm_decision_snapshot=json.dumps({
            "binding": {
                "binding_version": 2,
                "direction_policy": "DAILY_BIAS_PRIMARY",
                "authority_token": "TOKEN_IDEM",
                "allowed_directions": ["LONG"],
                "config_fingerprint": eff_fp,
                "status": "READY",
                "symbol": symbol,
            }
        }),
    )
    db.add(order)
    db.commit()

    coord = ExecutionCoordinator()
    ticker = {"bid": 2499.8, "ask": 2500.0, "last": 2499.9, "timestamp": now_ms + 1000, "is_valid": True, "is_stale": False}

    # 1st coordinator pass: triggers fill
    coord.evaluate_orders_sync(db, ticker_override=ticker, now_ms=now_ms + 1000)
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == 2500.0

    # 2nd coordinator pass: idempotent (already open, no new fill)
    coord.evaluate_orders_sync(db, ticker_override=ticker, now_ms=now_ms + 2000)
    db.refresh(order)
    assert order.state == "paper_open"
    assert order.actual_entry == 2500.0

    # 1st close: closes order
    TradeLifecycleService.execute_close(db, order_id=order.id, exit_price=2535.0, exit_cause="TP_HIT", now_ms=now_ms + 5000)
    db.refresh(order)
    assert order.state == "closed"
    assert order.realized_pnl_net == pytest.approx(6.60, abs=0.01)
    closed_pnl = order.realized_pnl_net

    # 2nd close: idempotent
    TradeLifecycleService.execute_close(db, order_id=order.id, exit_price=2535.0, exit_cause="TP_HIT", now_ms=now_ms + 6000)
    db.refresh(order)
    assert order.state == "closed"
    assert order.realized_pnl_net == closed_pnl


# =========================================================================
# 19. Open Position Invariance (Rule 070)
# =========================================================================
def test_open_position_invariance_after_fill(db: Session):
    """
    Rule 070:
    After order fill, owner transition or authority invalidation does NOT alter
    the open position while price has not hit SL or TP.
    """
    clear_current_cg01_bias_authority()
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    for k, v in [
        ("scalp_m1_mode", "PAPER"),
        ("cg01_enabled", "true"),
        ("cg01_profile_mode", "PAPER_EXECUTION"),
        ("execution_profile", "CG01"),
        ("cg01_direction_policy", "DAILY_BIAS_PRIMARY"),
    ]:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if not row:
            db.add(models.SystemConfig(key=k, value=v, updated_at=now_ms))
        else:
            row.value = v
            row.updated_at = now_ms
    db.commit()

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_inv",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_INV",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap, config=eff_cfg, evaluated_at_ms=now_ms)

    order = models.PaperOrder(
        id="ord_invariance_test",
        instrument=symbol,
        direction="LONG",
        planned_entry=2500.0,
        stop_loss=2490.0,
        take_profit=2535.0,
        quantity=0.2,
        initial_risk_usdt=2.0,
        state="armed",
        order_type="LIMIT",
        created_at=now_ms,
        arm_decision_snapshot=json.dumps({
            "binding": {
                "binding_version": 2,
                "direction_policy": "DAILY_BIAS_PRIMARY",
                "authority_token": "TOKEN_INV",
                "allowed_directions": ["LONG"],
                "config_fingerprint": eff_fp,
                "status": "READY",
                "symbol": symbol,
            }
        }),
    )
    db.add(order)
    db.commit()

    coord = ExecutionCoordinator()
    coord.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2499.8, "ask": 2500.0, "last": 2499.9, "timestamp": now_ms + 1000, "is_valid": True, "is_stale": False},
        now_ms=now_ms + 1000,
    )
    db.refresh(order)
    assert order.state == "paper_open"

    # Now change owner epoch and invalidate authority
    bump_runtime_ownership_epoch()
    clear_current_cg01_bias_authority()

    # Coordinator runs with price in between entry and SL/TP
    coord.evaluate_orders_sync(
        db,
        ticker_override={"bid": 2505.0, "ask": 2505.2, "last": 2505.1, "timestamp": now_ms + 2000, "is_valid": True, "is_stale": False},
        now_ms=now_ms + 2000,
    )
    db.refresh(order)
    # Invariant: position stays open without modification
    assert order.state == "paper_open"
    assert order.planned_entry == 2500.0
    assert order.stop_loss == 2490.0
    assert order.take_profit == 2535.0
    assert order.quantity == 0.2


# =========================================================================
# 20. Lease Captured Before Compute Rejects Mid-Compute Epoch Bump (Rules 019, 027, 071)
# =========================================================================
def test_lease_captured_before_compute_rejects_mid_compute_epoch_bump(db: Session):
    """
    Rules 019, 027, 071:
    Publish context / lease is captured before computation starts.
    If another owner/process bumps the runtime epoch mid-computation,
    the resulting publish attempt is rejected and does not mutate the registry.
    """
    clear_current_cg01_bias_authority(reset_epoch=True)
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    eff_cfg.profile_mode = "PAPER_EXECUTION"

    # 1. Capture lease before compute at initial epoch (epoch 1)
    pub_ctx = build_cg01_authority_publish_context(
        symbol=symbol,
        config=eff_cfg,
        db=db,
        caller_identity="WATCHER",
        evaluation_origin="WATCHER",
        auto_enabled=True,
        now_ms=now_ms,
    )
    assert pub_ctx["generation"] == 1

    # 2. Simulate owner transition mid-compute: epoch bumped to 2
    bump_runtime_ownership_epoch()
    assert get_current_runtime_ownership_epoch() == 2

    # 3. Compute completes, attempts to publish with the pre-compute lease
    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_stale_compute",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_STALE_COMPUTE",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    ok_pub = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        generation=pub_ctx["generation"],
        owner=pub_ctx["owner"],
        is_active_owner=pub_ctx["is_active_owner"],
    )
    # Must be rejected because generation 1 != current epoch 2
    assert ok_pub is False

    # Verify registry remains clean
    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms, config=eff_cfg)
    assert res.available is False


# =========================================================================
# 21. Owner Resolution Exception Fails Closed (Rules 020, 041, 072)
# =========================================================================
def test_owner_resolution_exception_fail_closed(db: Session, monkeypatch):
    """
    Rules 020, 041, 072:
    If owner resolution throws an unhandled exception at publish time,
    the publisher must fail closed (return False) and never mutate the registry.
    """
    clear_current_cg01_bias_authority(reset_epoch=True)
    symbol = "XAUUSDT"
    now_ms = 1700000000000

    for k, v in [
        ("scalp_m1_mode", "PAPER"),
        ("cg01_enabled", "true"),
        ("cg01_profile_mode", "PAPER_EXECUTION"),
        ("execution_profile", "CG01"),
        ("cg01_direction_policy", "DAILY_BIAS_PRIMARY"),
    ]:
        row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
        if not row:
            db.add(models.SystemConfig(key=k, value=v, updated_at=now_ms))
        else:
            row.value = v
            row.updated_at = now_ms
    db.commit()

    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    # Prepopulate with token A
    snap_a = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_pre",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_PRE_EXISTING",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    publish_current_cg01_bias_authority(symbol=symbol, bias=snap_a, config=eff_cfg, evaluated_at_ms=now_ms)

    # Monkeypatch resolve_entry_execution_owner inside services.cg01_scheduler_service to raise an error
    monkeypatch.setattr(
        "services.cg01_scheduler_service.resolve_entry_execution_owner",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("DB lock failure")),
    )

    # Attempt to publish token B without explicit owner (triggers resolve_entry_execution_owner)
    snap_b = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_new",
        direction="BEARISH",
        status="READY",
        allowed_directions=("SHORT",),
        authority_token="TOKEN_NEW_POISON",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    ok_pub = publish_current_cg01_bias_authority(symbol=symbol, bias=snap_b, config=eff_cfg, evaluated_at_ms=now_ms)
    assert ok_pub is False, "Publisher failed open on owner resolution exception!"

    # Registry must still contain token A intact
    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms, config=eff_cfg)
    assert res.available is True
    assert res.bias.authority_token == "TOKEN_PRE_EXISTING"


# =========================================================================
# 22. Future Epoch Rejection (Rules 035, 073)
# =========================================================================
def test_future_epoch_rejection(db: Session):
    """
    Rules 035, 073:
    Caller passing a future generation (N + 100) must be rejected.
    Subsequent publish with current epoch N must succeed.
    """
    clear_current_cg01_bias_authority(reset_epoch=True)
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    curr_epoch = get_current_runtime_ownership_epoch()

    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_future",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_FUTURE",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )

    # 1. Attempt publish with future generation
    ok_future = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        generation=curr_epoch + 100,
    )
    assert ok_future is False

    # 2. Subsequent publish with current epoch succeeds
    snap.authority_token = "TOKEN_VALID_EPOCH"
    ok_curr = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        generation=curr_epoch,
    )
    assert ok_curr is True
    res = resolve_current_cg01_bias_authority(symbol=symbol, now_ms=now_ms, config=eff_cfg)
    assert res.bias.authority_token == "TOKEN_VALID_EPOCH"


# =========================================================================
# 23. Owner Transition A -> B -> A Rejects Stale Lease (Rule 039)
# =========================================================================
def test_owner_transition_a_b_a_rejects_stale_lease(db: Session):
    """
    Rule 039:
    Evaluation starts under WATCHER (epoch 1).
    Ownership moves to SCHEDULER (epoch 2), then back to WATCHER (epoch 3).
    Evaluation epoch 1 completes.
    Even though the current owner name is again 'WATCHER', the lease is stale and rejected.
    """
    clear_current_cg01_bias_authority(reset_epoch=True)
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    eff_cfg, eff_fp, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)
    eff_cfg.profile_mode = "PAPER_EXECUTION"

    # Evaluation starts under WATCHER at epoch 1
    pub_ctx1 = build_cg01_authority_publish_context(
        symbol=symbol,
        config=eff_cfg,
        db=db,
        caller_identity="WATCHER",
        evaluation_origin="WATCHER",
        auto_enabled=True,
        now_ms=now_ms,
    )
    assert pub_ctx1["generation"] == 1
    assert pub_ctx1["owner"] == "WATCHER"

    # Transition to SCHEDULER (epoch 2)
    bump_runtime_ownership_epoch()

    # Transition back to WATCHER (epoch 3)
    bump_runtime_ownership_epoch()
    assert get_current_runtime_ownership_epoch() == 3

    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_aba",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_ABA_STALE",
        config_fingerprint=eff_fp,
        evaluated_at_ms=now_ms,
    )
    # Publishing using epoch 1 lease must be rejected even though owner is WATCHER
    ok = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap,
        config=eff_cfg,
        evaluated_at_ms=now_ms,
        generation=pub_ctx1["generation"],
        owner=pub_ctx1["owner"],
        is_active_owner=True,
    )
    assert ok is False


# =========================================================================
# 24. Config Changed During Compute Rejects Publish (Rules 040, 079)
# =========================================================================
def test_config_changed_during_compute_rejects_publish(db: Session):
    """
    Rules 040, 079:
    Computation starts with Config A (fingerprint A).
    Config changes to Config B (fingerprint B) during computation.
    Completed result with fingerprint A cannot be published under Config B.
    """
    clear_current_cg01_bias_authority(reset_epoch=True)
    symbol = "XAUUSDT"
    now_ms = 1700000000000
    cfg_a, fp_a, _ = resolve_canonical_cg01_config(db=db, symbol=symbol)

    # Config B has different settings
    cfg_b = copy.deepcopy(cfg_a)
    cfg_b.min_net_rr = 3.5
    fp_b = compute_cg01_config_fingerprint(cfg_b)
    assert fp_a != fp_b

    # Snapshot computed with config A
    snap = CG01DailyBiasSnapshot(
        symbol=symbol,
        trading_day_key=resolve_trading_day_key(now_ms),
        bias_id="bias_cfg_change",
        direction="BULLISH",
        status="READY",
        allowed_directions=("LONG",),
        authority_token="TOKEN_CFG_A",
        config_fingerprint=fp_a,
        evaluated_at_ms=now_ms,
    )

    # Publish attempted against Config B
    ok = publish_current_cg01_bias_authority(
        symbol=symbol,
        bias=snap,
        config=cfg_b,
        evaluated_at_ms=now_ms,
    )
    assert ok is False


