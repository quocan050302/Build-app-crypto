import pytest
from fastapi.testclient import TestClient
from main import app
from database import SessionLocal, engine
import models
import time

@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=engine)
    yield

def test_arm_setup_accepts_custom_chart_levels_and_rejects_insufficient_rr():
    client = TestClient(app)
    db = SessionLocal()
    now_ms = int(time.time() * 1000)

    # Clean existing setups and orders
    db.query(models.PaperOrder).delete()
    db.query(models.WatchSetup).delete()
    db.commit()

    # Create a watch setup
    setup = models.WatchSetup(
        id="watch-test-15M",
        version=1,
        strategy="SMC_V5",
        direction="LONG",
        timeframe="15M",
        state="READY",
        provisional_entry=4120.0,
        provisional_sl=4110.0,
        provisional_tp=4135.0, # Stop dist 10, Target dist 15 -> Net RR < 2.0
        invalidation_price=4100.0,
        leverage=5,
        margin_mode="ISOLATED",
        risk_pct=0.25,
        created_at=now_ms,
        updated_at=now_ms
    )
    db.add(setup)
    db.commit()

    # Case 1: Arm with insufficient Net RR -> expect 400 INSUFFICIENT_RR with descriptive message
    res = client.post(
        f"/api/v1/setups/arm/{setup.id}",
        json={
            "setup_id": setup.id,
            "expected_direction": "LONG",
            "planned_entry": 4120.0,
            "stop_loss": 4110.0,
            "take_profit": 4135.0 # ~1.5 gross, <1.5 net
        }
    )
    assert res.status_code == 400
    data = res.json()
    assert "detail" in data
    assert data["detail"]["code"] == "INSUFFICIENT_RR"
    assert "không đạt ngưỡng tối thiểu 2.0" in data["detail"]["message"]

    # Case 2: Arm with expanded TP (Net RR >= 2.0) -> succeeds and persists custom levels
    res_ok = client.post(
        f"/api/v1/setups/arm/{setup.id}",
        json={
            "setup_id": setup.id,
            "expected_direction": "LONG",
            "planned_entry": 4120.0,
            "stop_loss": 4110.0,
            "take_profit": 4160.0 # Stop dist 10, Target dist 40 -> Net RR > 2.7
        }
    )
    assert res_ok.status_code == 200
    res_data = res_ok.json()
    assert res_data["status"] == "success"

    # Verify db persistence
    db.expire_all()
    armed_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == setup.id).first()
    assert armed_setup.state == "ARMED"
    assert armed_setup.confirmed_entry == 4120.0
    assert armed_setup.confirmed_sl == 4110.0
    assert armed_setup.confirmed_tp == 4160.0
    assert armed_setup.net_rr >= 2.0

    order = db.query(models.PaperOrder).filter(models.PaperOrder.id == res_data["order_id"]).first()
    assert order is not None
    assert order.planned_entry == 4120.0
    assert order.stop_loss == 4110.0
    assert order.take_profit == 4160.0
    assert order.estimated_net_rr >= 2.0
    db.close()

def test_arm_setup_cross_margin_unsupported():
    client = TestClient(app)
    db = SessionLocal()
    now_ms = int(time.time() * 1000)

    # Clean existing setups and orders
    db.query(models.PaperOrder).delete()
    db.query(models.WatchSetup).delete()
    db.commit()

    # Create a watch setup configured with CROSS margin
    setup = models.WatchSetup(
        id="watch-cross-15M",
        version=1,
        strategy="SMC_V5",
        direction="LONG",
        timeframe="15M",
        state="READY",
        provisional_entry=4120.0,
        provisional_sl=4110.0,
        provisional_tp=4160.0,
        invalidation_price=4100.0,
        leverage=5,
        margin_mode="CROSS",
        risk_pct=0.25,
        created_at=now_ms,
        updated_at=now_ms
    )
    db.add(setup)
    db.commit()

    res = client.post(
        f"/api/v1/setups/arm/{setup.id}",
        json={
            "setup_id": setup.id,
            "expected_direction": "LONG",
            "planned_entry": 4120.0,
            "stop_loss": 4110.0,
            "take_profit": 4160.0
        }
    )
    assert res.status_code == 400
    data = res.json()
    assert data["detail"]["code"] == "CANNOT_EXECUTE"
    assert "CROSS_MARGIN_UNSUPPORTED" in data["detail"]["message"]
    db.close()
