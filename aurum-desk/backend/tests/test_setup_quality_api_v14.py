import pytest
from fastapi.testclient import TestClient
from main import app
from database import SessionLocal
import models
from services.setup_quality_repository import setup_quality_repository
from services.setup_quality_service import build_quality_snapshot, QualityAssessment, QualitySummary

client = TestClient(app)

def test_get_setup_quality_not_found():
    resp = client.get("/api/v1/setups/non-existent-setup-id-999/quality")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "SETUP_NOT_FOUND"

def test_get_setup_quality_revision_mismatch():
    db = SessionLocal()
    try:
        # Create a test watch setup with version 2
        setup = models.WatchSetup(
            id="watch-test-rev",
            timeframe="15M",
            direction="LONG",
            state="READY",
            setup_instance_id="inst-test-rev-1",
            version=2,
            provisional_entry=2650.0,
            provisional_sl=2640.0,
            provisional_tp=2670.0,
            invalidation_price=2640.0,
            gross_rr=2.0,
            net_rr=1.8,
            risk_usdt=2.5,
            quantity=0.05,
            leverage=5,
            margin_mode="ISOLATED",
            risk_pct=0.25,
            created_at=1700000000000,
            updated_at=1700000000000,
        )
        db.merge(setup)
        db.commit()

        # Query with expected_revision = 1 (mismatch with version 2)
        resp = client.get("/api/v1/setups/watch-test-rev/quality?expected_revision=1")
        assert resp.status_code == 409
        assert "SETUP_REVISION_MISMATCH" in resp.json()["detail"]
    finally:
        db.close()

def test_preview_setup_quality():
    payload = {
        "symbol": "XAUUSDT",
        "timeframe": "15M",
        "direction": "LONG",
        "entry_price": 2650.0,
        "stop_loss": 2640.0,
        "take_profit": 2680.0,
        "quantity": 0.05,
        "leverage": 30,
        "margin_mode": "ISOLATED",
        "risk_pct": 0.25
    }
    resp = client.post("/api/v1/quality/preview", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["preview_type"] == "DRAFT_PREVIEW"
    assert "plan_fingerprint" in data
    assert "assessment" in data
    assert "checks" in data["assessment"]

def test_get_upcoming_setups_includes_quality_fields():
    resp = client.get("/api/v1/setups/upcoming")
    assert resp.status_code == 200
    data = resp.json()
    assert "setups" in data
    if data["setups"]:
        s = data["setups"][0]
        assert "quality_summary" in s
        assert "quality_snapshot_id" in s
