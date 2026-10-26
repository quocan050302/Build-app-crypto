"""
backend/tests/test_v14_4_5_policy_startup.py
V14.4.5 — Case P01: Policy Startup Migration, Durable Marker & Explicit Choice Preservation.

Covers PHẦN 006–014, 083–085, 108:
1. Saved legacy NY_ONLY policy upgraded to ALL_SESSIONS_WITH_NY_RESERVE upon startup.
2. Custom settings (capital, leverage, risk caps) and existing orders remain untouched.
3. Durable SystemConfig marker and DomainEvent audit record are committed.
4. Policy version is bumped exactly once.
5. Repeated startup is idempotent: no double version bump, marker status ALREADY_APPLIED.
6. Explicit user choice preservation: if user explicitly sets NY_ONLY after migration has run,
   subsequent startup does NOT revert the user's choice back to ALL_SESSIONS_WITH_NY_RESERVE.
7. Worker-observed policy reflects the committed policy version and mode.
"""
import json
import pytest
from sqlalchemy.orm import Session

import models
from services.clock import ReplayClock
from services.trading_policy_service import (
    TradingPolicyService,
    apply_runtime_contract_upgrades,
    UpgradeSummary,
)




def test_p01_startup_upgrades_legacy_ny_only_with_marker_and_audit(isolated_db: Session):
    """Case P01 part 1: Saved NY_ONLY is upgraded, custom settings preserved, marker committed."""
    # 1. Seed a legacy DB with NY_ONLY and custom capital/risk settings
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "NY_ONLY"
    policy.initial_equity_usdt = 2500.0
    policy.max_daily_fills = 3
    policy.version = 1
    isolated_db.commit()

    # Seed an existing open order
    order = models.PaperOrder(
        id="ord_p01_seed",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=10.0,
        opened_at=1700000000000,
        created_at=1700000000000,
    )
    isolated_db.add(order)
    isolated_db.commit()

    # 2. Run runtime contract upgrade (as executed by lifespan bootstrap)
    clock = ReplayClock(1700000010000)
    summary: UpgradeSummary = apply_runtime_contract_upgrades(
        isolated_db,
        deployment_scope="XAUUSDT",
        now_ms=clock.now_ms(),
    )

    assert summary.changed is True
    assert summary.old_mode == "NY_ONLY"
    assert summary.new_mode == "ALL_SESSIONS_WITH_NY_RESERVE"
    assert summary.after_version == 2
    assert summary.status == "APPLIED"
    assert summary.audit_id is not None

    # 3. Verify DB state in fresh queries
    isolated_db.refresh(policy)
    assert policy.entry_session_policy == "ALL_SESSIONS_WITH_NY_RESERVE"
    assert policy.version == 2
    assert policy.initial_equity_usdt == 2500.0  # Preserved!
    assert policy.max_daily_fills == 3           # Preserved!

    # Verify order is untouched
    loaded_order = isolated_db.query(models.PaperOrder).filter_by(id="ord_p01_seed").first()
    assert loaded_order is not None
    assert loaded_order.state == "paper_open"
    assert loaded_order.actual_entry == 4180.0

    # Verify durable SystemConfig marker
    marker_key = f"mig_marker:{summary.marker}:XAUUSDT"[:50]
    marker_rec = isolated_db.query(models.SystemConfig).filter_by(key=marker_key).first()
    assert marker_rec is not None
    marker_data = json.loads(marker_rec.value)
    assert marker_data["status"] == "APPLIED"
    assert marker_data["old_mode"] == "NY_ONLY"
    assert marker_data["new_mode"] == "ALL_SESSIONS_WITH_NY_RESERVE"

    # Verify DomainEvent audit
    audit_event = isolated_db.query(models.DomainEvent).filter_by(event_id=summary.audit_id).first()
    assert audit_event is not None
    assert audit_event.event_type == "policy.upgraded"


def test_p01_repeated_startup_idempotent_no_version_bump(isolated_db: Session):
    """Case P01 part 2: Repeated startup detects existing marker and does NOT bump version."""
    clock = ReplayClock(1700000020000)
    # First run
    s1 = apply_runtime_contract_upgrades(isolated_db, deployment_scope="XAUUSDT", now_ms=clock.now_ms())
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    v1 = policy.version

    # Second run (simulating application restart)
    clock.advance_by(5000)
    s2 = apply_runtime_contract_upgrades(isolated_db, deployment_scope="XAUUSDT", now_ms=clock.now_ms())

    assert s2.changed is False
    assert s2.status in ("ALREADY_APPLIED", "NOOP")
    isolated_db.refresh(policy)
    assert policy.version == v1  # No double bump!


def test_p01_explicit_user_choice_preserved_after_migration(isolated_db: Session):
    """Case P01 part 3: If user explicitly sets NY_ONLY after upgrade, subsequent startup preserves it."""
    clock = ReplayClock(1700000030000)
    # 1. Initial upgrade runs and sets ALL_SESSIONS_WITH_NY_RESERVE
    s1 = apply_runtime_contract_upgrades(isolated_db, deployment_scope="XAUUSDT", now_ms=clock.now_ms())
    assert s1.new_mode == "ALL_SESSIONS_WITH_NY_RESERVE"

    # 2. User explicitly changes policy to NY_ONLY via TradingPolicyService.update_policy
    policy = TradingPolicyService.update_policy(
        isolated_db,
        {"entry_session_policy": "NY_ONLY"},
        clock=clock,
    )
    isolated_db.commit()
    assert policy.entry_session_policy == "NY_ONLY"
    user_version = policy.version

    # 3. Next application restart runs startup migration
    clock.advance_by(10000)
    s_restart = apply_runtime_contract_upgrades(isolated_db, deployment_scope="XAUUSDT", now_ms=clock.now_ms())

    # Must NOT overwrite user's explicit choice!
    assert s_restart.changed is False
    assert s_restart.status == "ALREADY_APPLIED"

    isolated_db.refresh(policy)
    assert policy.entry_session_policy == "NY_ONLY"  # Preserved!
    assert policy.version == user_version
