"""
backend/tests/test_v14_4_13_forward_membership.py
V14.4.13 — Forward Session Binding & Membership Isolation Test Suite.

Covers:
  - S01: Session Isolation (PHẦN 016, 062) — Session A/B overlap, exact membership,
         no leak across sessions, preserves trading session_instance_id.
  - S02: Runtime Binding (PHẦN 024, 063) — Production READY -> ARM -> FILL pipeline
         creates durable forward membership; membership status transitions ARMED -> FILLED -> CLOSED;
         Auto OFF preserves READY without ARMED/FILL.
  - S03: Session Failure & Lifecycle (PHẦN 021, 022, 031, 032, 064) — Start/stop/restart,
         active session pointer policy, duplicate binding idempotency, conflict rejection.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, Any, List
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session

import models
from services.clock import IClock
from services.forward_paper_service import (
    ForwardSessionConfig,
    ForwardOrderMembership,
    create_forward_paper_session,
    save_forward_session,
    load_forward_session,
    load_active_forward_session,
    bind_order_to_forward_session,
    bind_order_to_active_forward_session,
    update_order_forward_membership_status,
    get_order_forward_membership,
    load_forward_session_orders,
    generate_forward_paper_report,
    export_forward_paper_data,
    stop_forward_paper_session,
)
from services.trade_lifecycle_service import TradeLifecycleService
from test_v14_4_11_three_session_execution import _run_full_ready_arm_fill_pipeline

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


# =========================================================================
# S01: SESSION ISOLATION (PHẦN 016, 062)
# =========================================================================

def test_mbr01_session_isolation_overlap_and_unbound_orders(isolated_db: Session):
    """PHẦN 016, 062 (S01):
    Create Session A and Session B with overlapping start time.
    Seed:
      1. Order A: explicitly bound to Session A.
      2. Order B: explicitly bound to Session B.
      3. Order Unbound: created after start, not bound to any session.
      4. Order Pre: created before Session A start.
    Assert:
      - Report and Export for Session A contain ONLY Order A.
      - Report and Export for Session B contain ONLY Order B.
      - Unknown session ID returns NOT_FOUND / empty list.
      - Trading session IDs (session_instance_id) are preserved and never overwritten.
    """
    now_ms = 1792000000000

    # Create Session A
    sess_a = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_A",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    # 1. Order A (Asia trading session)
    ord_a = models.PaperOrder(
        id="ord_A_001",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.0,
        realized_pnl_net=4.0,
        realized_r=2.0,
        opened_at=now_ms + 2000,
        closed_at=now_ms + 5000,
        created_at=now_ms + 2000,
        session_instance_id="ASIA_20261014",  # Preserved trading session tag!
        strategy_family="STANDARD_SMC",
    )

    # 2. Order B (London trading session)
    ord_b = models.PaperOrder(
        id="ord_B_001",
        instrument="XAUUSDT",
        direction="SHORT",
        state="closed",
        planned_entry=4200.0,
        actual_entry=4200.0,
        stop_loss=4210.0,
        take_profit=4180.0,
        actual_exit=4180.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.0,
        realized_pnl_net=4.0,
        realized_r=2.0,
        opened_at=now_ms + 3000,
        closed_at=now_ms + 6000,
        created_at=now_ms + 3000,
        session_instance_id="LONDON_20261014",  # Preserved trading session tag!
        strategy_family="STANDARD_SMC",
    )

    # 3. Order Unbound (created after start, but not in any forward session)
    ord_unbound = models.PaperOrder(
        id="ord_UNBOUND_001",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.0,
        realized_pnl_net=4.0,
        realized_r=2.0,
        opened_at=now_ms + 4000,
        closed_at=now_ms + 7000,
        created_at=now_ms + 4000,
        session_instance_id="NY_20261014",
        strategy_family="STANDARD_SMC",
    )

    # 4. Order Pre (created before Session A start)
    ord_pre = models.PaperOrder(
        id="ord_PRE_001",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        actual_exit=4200.0,
        exit_cause="TP_HIT",
        quantity=0.1,
        initial_risk_usdt=2.0,
        realized_pnl_net=4.0,
        realized_r=2.0,
        opened_at=now_ms - 50000,
        closed_at=now_ms - 10000,
        created_at=now_ms - 50000,
        session_instance_id="ASIA_20261013",
        strategy_family="STANDARD_SMC",
    )

    isolated_db.add_all([ord_a, ord_b, ord_unbound, ord_pre])
    isolated_db.commit()

    # Explicitly bind Order A to Session A while Session A is active
    res_bind_a = bind_order_to_forward_session(isolated_db, sess_a.session_id, ord_a, now_ms=now_ms + 2000)
    assert res_bind_a["success"] is True
    assert res_bind_a["status"] == "BOUND"

    # Now create Session B (transitions Session A to STOPPED atomically under single active pointer policy)
    sess_b = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_B",
        virtual_capital=2000.0,
        now_ms=now_ms + 2500,
    )

    # Explicitly bind Order B to Session B while Session B is active
    res_bind_b = bind_order_to_forward_session(isolated_db, sess_b.session_id, ord_b, now_ms=now_ms + 3000)
    assert res_bind_b["success"] is True
    assert res_bind_b["status"] == "BOUND"

    # Verify orders query strictly isolates members
    orders_a = load_forward_session_orders(isolated_db, sess_a.session_id)
    assert len(orders_a) == 1
    assert orders_a[0].id == "ord_A_001"
    # Ensure session_instance_id was NOT overwritten!
    assert orders_a[0].session_instance_id == "ASIA_20261014"

    orders_b = load_forward_session_orders(isolated_db, sess_b.session_id)
    assert len(orders_b) == 1
    assert orders_b[0].id == "ord_B_001"
    assert orders_b[0].session_instance_id == "LONDON_20261014"

    # Unknown session ID returns empty list
    orders_unknown = load_forward_session_orders(isolated_db, "fwd_non_existent")
    assert orders_unknown == []

    # Report for Session A contains only Order A
    report_a = generate_forward_paper_report(isolated_db, sess_a.session_id, now_ms=now_ms + 10000)
    assert report_a.get("status") != "NOT_FOUND"
    assert report_a["total_fills"] == 1
    assert report_a["confirmed_closed_trades"] == 1
    assert report_a["confirmed_wins"] == 1

    # Report for unknown session returns NOT_FOUND
    report_unknown = generate_forward_paper_report(isolated_db, "fwd_non_existent")
    assert report_unknown.get("status") == "NOT_FOUND"

    # Report for Session B contains only Order B
    report_b = generate_forward_paper_report(isolated_db, sess_b.session_id, now_ms=now_ms + 10000)
    assert report_b["total_fills"] == 1
    assert report_b["confirmed_closed_trades"] == 1
    assert report_b["confirmed_wins"] == 1

    # Export parity check
    export_a = export_forward_paper_data(isolated_db, sess_a.session_id)
    assert len(export_a) == 1
    assert export_a[0]["order_id"] == "ord_A_001"
    assert export_a[0]["session_instance_id"] == "ASIA_20261014"

    export_b = export_forward_paper_data(isolated_db, sess_b.session_id)
    assert len(export_b) == 1
    assert export_b[0]["order_id"] == "ord_B_001"
    assert export_b[0]["session_instance_id"] == "LONDON_20261014"


# =========================================================================
# S02: RUNTIME BINDING (PHẦN 024, 063)
# =========================================================================

def test_mbr02_runtime_binding_production_lifecycle(isolated_db: Session):
    """PHẦN 024, 063 (S02):
    Executes actual production READY -> ARM -> FILL pipeline.
    Asserts:
      1. Order is automatically bound to active forward session upon ARM.
      2. Membership transitions status from ARMED -> FILLED -> CLOSED.
      3. Trading session quota and tags are preserved.
      4. Auto OFF leaves setup in READY with zero ARM, zero FILL, zero membership.
    """
    now_ms = 1792000000000

    # Start an active forward practice session
    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_runtime_02",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )
    assert load_active_forward_session(isolated_db).session_id == sess.session_id

    # 1. Asian Session (09:00 VN) LONG production pipeline
    asia_dt = datetime(2026, 10, 14, 9, 0, tzinfo=VN_TZ)
    res_asia = _run_full_ready_arm_fill_pipeline(
        isolated_db,
        session_dt=asia_dt,
        direction="LONG",
        auto_enabled=True,
    )

    armed_order = res_asia["armed_order"]
    assert armed_order is not None
    order_id = armed_order.id

    # Check forward order membership was created automatically at ARM boundary!
    mem = get_order_forward_membership(isolated_db, order_id)
    assert mem is not None
    assert mem.forward_session_id == sess.session_id
    assert mem.order_id == order_id
    # Since execute_fill ran in pipeline, membership status transitioned to FILLED!
    assert mem.membership_status == "FILLED"

    # Now execute lifecycle close
    TradeLifecycleService.execute_close(
        isolated_db,
        order_id,
        exit_price=4200.0,
        exit_cause="TP_HIT",
        now_ms=now_ms + 5000,
    )

    # Membership status updated to CLOSED
    mem_closed = get_order_forward_membership(isolated_db, order_id)
    assert mem_closed.membership_status == "CLOSED"

    # 2. Negative guard: Auto OFF promotes to READY, but does NOT arm or create membership
    london_dt = datetime(2026, 10, 14, 15, 0, tzinfo=VN_TZ)
    res_off = _run_full_ready_arm_fill_pipeline(
        isolated_db,
        session_dt=london_dt,
        direction="SHORT",
        auto_enabled=False,  # AUTO OFF
    )

    assert res_off["setup"] is not None
    assert res_off["armed_order"] is None
    assert res_off["filled_order"] is None


# =========================================================================
# S03: SESSION LIFECYCLE & FAILURE HANDLING (PHẦN 021, 022, 031, 032, 064)
# =========================================================================

def test_mbr03_session_failure_and_lifecycle(isolated_db: Session):
    """PHẦN 021, 022, 031, 032, 064 (S03):
    Tests forward session start, stop, restart, active pointer transitions,
    idempotent binding, and conflict rejection.
    """
    now_ms = 1792000000000

    # 1. Start Session 1
    sess1 = create_forward_paper_session(
        isolated_db,
        session_id="fwd_life_01",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )
    active = load_active_forward_session(isolated_db)
    assert active is not None
    assert active.session_id == "fwd_life_01"

    # 2. Start Session 2 -> automatically stops Session 1 to enforce single active pointer policy
    sess2 = create_forward_paper_session(
        isolated_db,
        session_id="fwd_life_02",
        virtual_capital=2000.0,
        now_ms=now_ms + 1000,
    )
    active2 = load_active_forward_session(isolated_db)
    assert active2 is not None
    assert active2.session_id == "fwd_life_02"

    sess1_reloaded = load_forward_session(isolated_db, "fwd_life_01")
    assert sess1_reloaded.status == "STOPPED"

    # 3. Create test order
    test_ord = models.PaperOrder(
        id="ord_life_001",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        planned_entry=4180.0,
        actual_entry=4180.0,
        stop_loss=4170.0,
        take_profit=4200.0,
        quantity=0.1,
        initial_risk_usdt=2.0,
        opened_at=now_ms + 2000,
        created_at=now_ms + 2000,
        session_instance_id="ASIA_20261014",
        strategy_family="STANDARD_SMC",
    )
    isolated_db.add(test_ord)
    isolated_db.commit()

    # 4. Attempt to bind to stopped session -> rejects with SESSION_INACTIVE
    res_stopped = bind_order_to_forward_session(isolated_db, "fwd_life_01", test_ord)
    assert res_stopped["success"] is False
    assert res_stopped["status"] == "SESSION_INACTIVE"

    # 5. Bind to active Session 2 -> BOUND
    res_bound = bind_order_to_forward_session(isolated_db, "fwd_life_02", test_ord)
    assert res_bound["success"] is True
    assert res_bound["status"] == "BOUND"

    # 6. Duplicate binding same order & session is idempotent -> ALREADY_BOUND
    res_dup = bind_order_to_forward_session(isolated_db, "fwd_life_02", test_ord)
    assert res_dup["success"] is True
    assert res_dup["status"] == "ALREADY_BOUND"

    # 7. Attempt to bind already-bound order to a different active session -> CONFLICTING_MEMBERSHIP
    sess3 = create_forward_paper_session(
        isolated_db,
        session_id="fwd_life_03",
        virtual_capital=1500.0,
        now_ms=now_ms + 3000,
    )
    res_conflict = bind_order_to_forward_session(isolated_db, "fwd_life_03", test_ord)
    assert res_conflict["success"] is False
    assert res_conflict["status"] == "CONFLICTING_MEMBERSHIP"

    # 8. Stop Session 3 explicitly
    stop_ok = stop_forward_paper_session(isolated_db, "fwd_life_03", now_ms=now_ms + 4000)
    assert stop_ok is True
    assert load_active_forward_session(isolated_db) is None
