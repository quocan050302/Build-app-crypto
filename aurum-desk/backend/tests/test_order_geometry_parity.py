"""
backend/tests/test_order_geometry_parity.py
V14.4.2 — Repro 2 / GEOMSRC01: Canonical Order Geometry Across Views.

Enforces:
1. When an active order is bound to a setup (e.g. state is paper_open or armed),
   the setup display plan MUST use the order's authoritative geometry
   (actual_entry for open/closed orders, planned_entry for armed orders, own SL/TP).
2. If setup provisional/confirmed entry is 4180.0, but bound PaperOrder actual_entry is 4182.0,
   the resolved display plan returned by both upcoming setups API and build_setup_display_plan
   MUST have entry=4182.0 and actual_entry=4182.0, NOT 4180.0.
3. Order chart evidence and upcoming setup item must agree on the same actual entry.
"""
import pytest
import json
import models
from fastapi.testclient import TestClient
from main import app, get_db
from services.setup_display_plan_service import build_setup_display_plan
from services.order_plan_snapshot_service import resolve_order_display_snapshot


@pytest.fixture
def client(isolated_db):
    app.dependency_overrides[get_db] = lambda: isolated_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_geomsrc01_open_order_uses_actual_entry_across_views(isolated_db, client):
    """GEOMSRC01 (PHẦN 042, 043, 222, 223):
    Setup has provisional_entry=4180.0, provisional_sl=4175.0, provisional_tp=4200.0, state=PAPER_OPEN.
    Bound PaperOrder has actual_entry=4182.0, planned_entry=4180.0, stop_loss=4175.0, take_profit=4200.0, state=paper_open.
    Upcoming API and build_setup_display_plan MUST report entry=4182.0, NOT 4180.0!
    """
    now_ms = 1728000000000
    setup_id = "setup-geom-parity-01"

    setup = models.WatchSetup(
        id=setup_id,
        direction="LONG",
        timeframe="5m",
        state="PAPER_OPEN",
        provisional_entry=4180.0,
        provisional_sl=4175.0,
        provisional_tp=4200.0,
        confirmed_entry=4180.0,
        confirmed_sl=4175.0,
        confirmed_tp=4200.0,
        invalidation_price=4175.0,
        gross_rr=4.0,
        net_rr=3.5,
        created_at=now_ms,
        updated_at=now_ms,
    )
    isolated_db.add(setup)

    cost_snap = {
        "phase": "FILLED_ESTIMATE",
        "pricing_mode": "FROZEN_ORDER",
        "order_id": "ord-geom-parity-01",
        "direction": "LONG",
        "actual_entry": 4182.0,
        "planned_entry": 4180.0,
        "stop_loss": 4175.0,
        "take_profit": 4200.0,
        "quantity": 0.5,
        "net_rr": 2.5,
        "gross_rr": 2.57,
        "risk_usdt": 3.5,
        "net_risk_usdt": 3.5,
        "net_reward_usdt": 8.75,
        "leverage": 10,
        "cost_source": "FROZEN_ORDER_SNAPSHOT",
        "calculated_at": now_ms,
    }

    order = models.PaperOrder(
        id="ord-geom-parity-01",
        setup_id=setup_id,
        direction="LONG",
        order_type="LIMIT",
        planned_entry=4180.0,
        actual_entry=4182.0,
        stop_loss=4175.0,
        take_profit=4200.0,
        quantity=0.5,
        initial_risk_usdt=3.5,
        estimated_net_rr=2.5,
        gross_rr=2.57,
        state="paper_open",
        opened_at=now_ms,
        created_at=now_ms,
        cost_snapshot=json.dumps(cost_snap),
    )
    isolated_db.add(order)
    isolated_db.commit()

    # 1. Test build_setup_display_plan with bound order snapshot
    order_snap = resolve_order_display_snapshot(order)
    assert order_snap["entry"] == 4182.0
    assert order_snap["actual_entry"] == 4182.0

    plan = build_setup_display_plan(
        setup,
        frozen_snapshot=order_snap,
        pricing_mode="FROZEN_ORDER",
    )

    assert plan["entry"] == 4182.0, f"Expected display plan entry to be actual 4182.0, but got {plan['entry']}"
    assert plan.get("actual_entry") == 4182.0
    assert plan.get("planned_entry") == 4180.0

    # 2. Test GET /api/v1/setups/upcoming endpoint parity
    resp = client.get("/api/v1/setups/upcoming")
    assert resp.status_code == 200
    data = resp.json()
    item = next((s for s in data["setups"] if s["id"] == setup_id), None)
    assert item is not None, f"Setup {setup_id} not found in upcoming API response"

    resolved = item.get("resolved_display_plan")
    assert resolved is not None
    assert resolved["entry"] == 4182.0, (
        f"API resolved_display_plan['entry'] should be actual_entry 4182.0, got {resolved['entry']}"
    )
