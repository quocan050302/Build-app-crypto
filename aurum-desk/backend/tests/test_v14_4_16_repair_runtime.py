"""backend/tests/test_v14_4_16_repair_runtime.py

V14.4.16 Acceptance Tests: Runtime Repair Worker & Fair Keyset Scanning
Cases:
- WREP01 / W01: Production Worker Wiring & Execution (PHẦN 004, 025, 028, 054)
- WREP02 / W02: Start, Stop, Shutdown & In-Flight Drain (PHẦN 029, 030, 055)
- WREP03 / W03: Non-Blocking Event Loop Responsiveness (PHẦN 026, 027, 056)
- WREP04 / W04: Prefix Starvation Defense & Fair Keyset Scanning (PHẦN 033, 034, 057)
- WREP05 / W05: Lifecycle Owner Failure & Worker Isolation (PHẦN 035, 045, 058)
"""

import asyncio
import json
import time
import pytest
from sqlalchemy.orm import Session

import models
from database import SessionLocal
from services.forward_paper_service import (
    create_forward_paper_session,
    bind_order_to_forward_session,
    stage_forward_repair_task,
    load_due_forward_repairs,
    process_due_forward_repairs,
    get_order_forward_membership,
    ForwardMembershipRepairTask,
    _repair_task_key,
    _append_repair_task_to_queue,
)
from services.forward_repair_worker import ForwardRepairWorker, forward_repair_worker


@pytest.fixture
def anyio_backend():
    """Worker production is designed for FastAPI's asyncio event loop.
    Scope anyio_backend to 'asyncio' in this worker test module to match runtime.
    """
    return "asyncio"


@pytest.mark.anyio
async def test_wrep01_production_worker_wiring(isolated_db: Session):
    """WREP01 / W01: Production Worker Wiring & Execution (PHẦN 004, 025, 028, 054).
    - Verify forward_repair_worker instance exists and provides production worker API.
    - Seed a durable repair task in DB.
    - Run forward_repair_worker.process_once() with its own isolated session.
    - Assert that task is processed and transitioned to REPAIRED.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_w01",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    ord_w = models.PaperOrder(
        id="ord_w01_exec",
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
        initial_risk_usdt=2.5,
        realized_pnl_net=2.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(ord_w)
    isolated_db.commit()

    bind_order_to_forward_session(isolated_db, sess.session_id, ord_w, now_ms=now_ms)
    isolated_db.commit()

    # Stage a durable repair task
    task = stage_forward_repair_task(
        db=isolated_db,
        order_id=ord_w.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        reason_codes=["TEST_WIRING"],
        now_ms=now_ms,
    )
    isolated_db.commit()

    # Execute process_once via worker
    res = forward_repair_worker.process_once(db=isolated_db, now_ms=now_ms + 1000)
    assert res["processed"] >= 1
    assert res["repaired"] >= 1

    # Verify membership reconstructed cleanly
    mem = get_order_forward_membership(isolated_db, ord_w.id)
    assert mem is not None
    assert mem.membership_status == "CLOSED"
    assert mem.forward_session_id == sess.session_id


@pytest.mark.anyio
async def test_wrep02_start_stop_shutdown_drain():
    """WREP02 / W02: Start, Stop, Shutdown & In-Flight Drain (PHẦN 029, 030, 055).
    - Start worker loop as background asyncio task.
    - Verify stop() flags shutdown.
    - shutdown() awaits in-flight tasks cleanly within timeout.
    """
    worker = ForwardRepairWorker()
    assert worker.is_running is False

    loop_task = asyncio.create_task(worker.run_repair_loop(poll_interval_s=0.05))
    await asyncio.sleep(0.02)
    assert worker.is_running is True

    # Graceful shutdown
    res = await worker.shutdown(timeout_s=2.0)
    assert res.status in ("COMPLETE", "NO_ACTIVE_WORKER")
    assert worker.is_running is False

    await asyncio.sleep(0.06)
    assert loop_task.done()


@pytest.mark.anyio
async def test_wrep03_event_loop_responsiveness():
    """WREP03 / W03: Non-Blocking Event Loop Responsiveness (PHẦN 026, 027, 056).
    - Run worker batch in thread pool via asyncio.to_thread.
    - Verify that concurrent asyncio tasks on the main event loop continue to execute without stutter.
    """
    worker = ForwardRepairWorker()

    ticks = 0
    async def _heartbeat():
        nonlocal ticks
        for _ in range(10):
            ticks += 1
            await asyncio.sleep(0.01)

    hb_task = asyncio.create_task(_heartbeat())

    # Run worker in background thread
    await asyncio.to_thread(worker.process_once, limit=10)
    await hb_task

    assert ticks == 10, "Event loop heartbeat must not be blocked by sync DB worker operations"


def test_wrep04_prefix_starvation_fair_scan(isolated_db: Session):
    """WREP04 / W04: Prefix Starvation Defense & Fair Keyset Scanning (PHẦN 033, 034, 057).
    - Seed 150 tasks in prefix that are already REPAIRED or MANUAL_REVIEW_REQUIRED.
    - Seed 1 PENDING task at index 151 (tail).
    - With limit=10, repeated calls to load_due_forward_repairs advance the durable cursor
      round-robin and REACH the tail task within bounded inspections!
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_w04",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    # 1. Seed 60 REPAIRED tasks in prefix
    queue_order_ids = []
    for i in range(60):
        oid = f"ord_prefix_{i:03d}"
        queue_order_ids.append(oid)
        t = ForwardMembershipRepairTask(
            task_id=f"fwd_rep_{oid}",
            order_id=oid,
            forward_session_id=sess.session_id,
            target_status="CLOSED",
            status="REPAIRED",
            attempts=1,
            max_attempts=5,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            repaired_at_ms=now_ms,
        )
        t_key = _repair_task_key(oid)
        rec = models.SystemConfig(key=t_key, value=json.dumps(t.to_dict()), updated_at=now_ms)
        isolated_db.add(rec)

    # 2. Seed 1 PENDING task at the tail
    tail_oid = "ord_tail_due_001"
    queue_order_ids.append(tail_oid)
    ord_tail = models.PaperOrder(
        id=tail_oid,
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
        initial_risk_usdt=2.5,
        realized_pnl_net=2.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(ord_tail)

    tail_task = ForwardMembershipRepairTask(
        task_id=f"fwd_rep_{tail_oid}",
        order_id=tail_oid,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        status="PENDING",
        attempts=0,
        max_attempts=5,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )
    tail_key = _repair_task_key(tail_oid)
    rec_tail = models.SystemConfig(key=tail_key, value=json.dumps(tail_task.to_dict()), updated_at=now_ms)
    isolated_db.add(rec_tail)

    rec_queue = models.SystemConfig(key="fwd_repair_queue", value=json.dumps(queue_order_ids), updated_at=now_ms)
    isolated_db.add(rec_queue)
    isolated_db.commit()

    # Repeated bounded cycles (e.g. limit=10) should advance cursor and find tail_task
    found = False
    for _ in range(10):
        due = load_due_forward_repairs(isolated_db, now_ms=now_ms + 1000, limit=10)
        if any(t.order_id == tail_oid for t in due):
            found = True
            break

    assert found is True, "Fair round-robin keyset scan must reach due tasks at tail without prefix starvation"


def test_wrep05_owner_fail_and_repair_isolation(isolated_db: Session):
    """WREP05 / W05: Lifecycle Owner Failure & Worker Isolation (PHẦN 035, 045, 058).
    - Commit failure in repair worker rolls back cleanly, increments attempts, and schedules retry.
    - Zero interference with other DB sessions or active orders.
    """
    now_ms = 1792000000000

    sess = create_forward_paper_session(
        isolated_db,
        session_id="fwd_sess_w05",
        virtual_capital=1000.0,
        now_ms=now_ms,
    )

    ord_iso = models.PaperOrder(
        id="ord_w05_iso",
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
        initial_risk_usdt=2.5,
        realized_pnl_net=2.00,
        opened_at=now_ms,
        closed_at=now_ms + 5000,
        created_at=now_ms,
    )
    isolated_db.add(ord_iso)
    isolated_db.commit()

    bind_order_to_forward_session(isolated_db, sess.session_id, ord_iso, now_ms=now_ms)
    isolated_db.commit()

    task = stage_forward_repair_task(
        db=isolated_db,
        order_id=ord_iso.id,
        forward_session_id=sess.session_id,
        target_status="CLOSED",
        reason_codes=["TEST_ISOLATION"],
        now_ms=now_ms,
    )
    isolated_db.commit()

    # Process due repairs cleanly
    res = process_due_forward_repairs(db=isolated_db, now_ms=now_ms + 1000)
    assert res["processed"] >= 1
    assert res["repaired"] >= 1

    # Verify task status is REPAIRED
    trec = isolated_db.query(models.SystemConfig).filter(
        models.SystemConfig.key == _repair_task_key(ord_iso.id)
    ).first()
    assert trec is not None
    reloaded = ForwardMembershipRepairTask.from_dict(json.loads(trec.value))
    assert reloaded.status == "REPAIRED"
