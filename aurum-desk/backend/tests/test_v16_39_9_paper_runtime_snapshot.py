"""
backend/tests/test_v16_39_9_paper_runtime_snapshot.py
V16.39.9 — Verification of Forward Paper Runtime Snapshot Tool

Enforces Rules 161-165:
- Rule 161: No run exists -> NOT_FOUND / NOT_CONFIGURED, zero fake profit.
- Rule 162: Backend unreachable -> RUNTIME_NOT_AVAILABLE, no exceptions.
- Rule 163: Zero closed trades -> winrate N/A, expectancy N/A, valid JSON.
- Rule 164: Worker STOPPED -> snapshot reflects STOPPED honestly.
- Rule 165: Read-only contract -> zero DB mutations, no new runs/sessions created.
"""
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch, MagicMock

# Ensure repo root and backend are on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

import models
from models import Base, PaperOrder, OpportunityFunnelRecord, PaperResearchRunRecord
from tools.paper_runtime_snapshot import (
    load_runtime_sources,
    collect_existing_run_summary,
    collect_funnel_summary,
    build_runtime_snapshot,
    render_markdown,
)


@pytest.fixture
def snapshot_test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_010_snapshot_no_run_reports_not_found(snapshot_test_db: Session):
    """
    Rule 161: When no research run is active or exists in DB:
    - Returns NOT_FOUND / NOT_CONFIGURED.
    - Does NOT create a run or fake profits.
    - Winrate is None (N/A).
    """
    sources = {
        "mode": "OFFLINE_DB",
        "status": "CONNECTED",
        "session": snapshot_test_db,
    }
    summary = collect_existing_run_summary(sources, run_id="non_existent_run_999")
    assert summary["status"] == "NOT_FOUND"
    assert summary["total_closed_trades"] == 0
    assert summary["net_realized_pnl_usd"] == 0.0
    assert summary["winrate_pct"] is None

    # Assert no PaperResearchRunRecord was created
    count = snapshot_test_db.query(PaperResearchRunRecord).count()
    assert count == 0, "Read-only summary must never create new runs"


def test_020_snapshot_backend_unreachable_reports_runtime_not_available():
    """
    Rule 162: When HTTP backend is down or unreachable:
    - Reports RUNTIME_NOT_AVAILABLE.
    - Does not raise unhandled connection exceptions.
    - Limitations clearly mention backend unavailability.
    """
    sources = load_runtime_sources(url="http://127.0.0.1:59999")  # Unused port
    assert sources["mode"] == "HTTP"
    assert sources["status"] == "RUNTIME_NOT_AVAILABLE"

    snapshot = build_runtime_snapshot(sources)
    assert snapshot["status"] == "RUNTIME_NOT_AVAILABLE"
    assert any("RUNTIME_NOT_AVAILABLE" in lim for lim in snapshot["limitations"])


def test_030_snapshot_zero_closed_trades_has_na_winrate_and_clean_json(snapshot_test_db: Session):
    """
    Rule 163: Zero closed trades must result in:
    - winrate_pct is None.
    - expectancy is None.
    - JSON serialization is clean without NaN, Infinity, or division by zero.
    """
    # Seed 1 armed order (not closed)
    order = PaperOrder(
        id="po_open_only",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="LIMIT",
        planned_entry=2625.0,
        stop_loss=2620.0,
        take_profit=2635.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="paper_open",
        created_at=int(time.time() * 1000),
    )
    snapshot_test_db.add(order)
    snapshot_test_db.commit()

    sources = {
        "mode": "OFFLINE_DB",
        "status": "CONNECTED",
        "session": snapshot_test_db,
    }

    snapshot = build_runtime_snapshot(sources)
    metrics = snapshot["metrics"]

    assert metrics["closed_trades_count"] == 0
    assert metrics["winrate_pct"] is None
    assert metrics["expectancy_usd_per_trade"] is None

    # Verify JSON encoding succeeds without NaN or Inf
    json_str = json.dumps(snapshot, indent=2)
    assert "NaN" not in json_str
    assert "Infinity" not in json_str

    md = render_markdown(snapshot)
    assert "N/A (chưa đủ mẫu lệnh đóng)" in md


def test_040_snapshot_worker_stopped_reported_honestly(snapshot_test_db: Session):
    """
    Rule 164: When worker health is STOPPED:
    - Overall status reports STOPPED.
    - Limitations include WORKER_STOPPED.
    """
    sources = {
        "mode": "HTTP",
        "status": "CONNECTED",
        "data": {
            "health": {
                "status": "STOPPED",
                "running": False,
            },
            "daily_status": {
                "cap": 3,
                "min_goal": 2,
            },
        },
    }

    snapshot = build_runtime_snapshot(sources)
    assert snapshot["status"] == "STOPPED"
    assert snapshot["worker_health"]["running"] is False
    assert any("WORKER_STOPPED" in lim for lim in snapshot["limitations"])


def test_050_snapshot_strict_read_only_contract(snapshot_test_db: Session):
    """
    Rule 165: Verify that invoking snapshot generation causes ZERO mutations on DB.
    Does not call start_observation_run or create_forward_paper_session.
    """
    initial_orders_count = snapshot_test_db.query(PaperOrder).count()
    initial_runs_count = snapshot_test_db.query(PaperResearchRunRecord).count()
    initial_funnel_count = snapshot_test_db.query(OpportunityFunnelRecord).count()

    sources = {
        "mode": "OFFLINE_DB",
        "status": "CONNECTED",
        "session": snapshot_test_db,
    }

    with patch("services.forward_paper_service.create_forward_paper_session") as mock_fwd:
        with patch("services.paper_research_run_service.start_observation_run") as mock_start:
            snapshot = build_runtime_snapshot(sources)
            assert mock_fwd.call_count == 0, "Must not call create_forward_paper_session"
            assert mock_start.call_count == 0, "Must not call start_observation_run"

    final_orders_count = snapshot_test_db.query(PaperOrder).count()
    final_runs_count = snapshot_test_db.query(PaperResearchRunRecord).count()
    final_funnel_count = snapshot_test_db.query(OpportunityFunnelRecord).count()

    assert initial_orders_count == final_orders_count
    assert initial_runs_count == final_runs_count
    assert initial_funnel_count == final_funnel_count
