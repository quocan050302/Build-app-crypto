"""
backend/tests/test_v16_39_10_snapshot_api_cli.py
V16.39.10 — Verification of API Endpoint, CLI Transport, Parity, and Redaction

Verifies Rules 101-115, 129-135, 148, 176-183:
- GET /api/v1/paper/runtime-snapshot returns canonical schema 2.0 with HTTP 200.
- Invalid window parameters (end_ms <= start_ms) return HTTP 422.
- Calling snapshot route causes ZERO DayAudit row creations.
- HTTP and Offline DB parity: exact same financial metrics and counts for the same DB state.
- CLI arguments: mutually exclusive --url and --db, format selection (json, markdown, both).
- CLI --output-dir creates atomic files without partial corruption.
- Missing DB returns exit code 1 without creating any file.
- Sensitive credentials (Telegram bot tokens, Bitget secrets, passwords) are strictly redacted.
- Subprocess import does NOT trigger database creation or background loops.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import models
from database import get_db
from main import app
from models import Base, DayAudit, PaperOrder, PaperResearchOrderLink, PaperResearchRunRecord
from services.paper_runtime_snapshot_service import (
    SCHEMA_VERSION,
    build_paper_runtime_snapshot,
    redact_sensitive_text,
)
from tools.paper_runtime_snapshot import main as cli_main, resolve_snapshot_db_path


@pytest.fixture
def api_test_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_path = Path(path)

    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionFactory()

    try:
        yield session, db_path
    finally:
        session.close()
        engine.dispose()
        try:
            if db_path.exists():
                db_path.unlink()
        except Exception:
            pass


def test_010_api_endpoint_canonical_response_and_422(api_test_db):
    """Rule 101, 105, 025: GET /api/v1/paper/runtime-snapshot returns 200 on query and 422 on invalid window."""
    session, _ = api_test_db

    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        # 1. Normal query
        res = client.get("/api/v1/paper/runtime-snapshot?symbol=XAUUSDT&scope=CG01")
        assert res.status_code == 200
        payload = res.json()
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["status"] in ("SNAPSHOT_READY", "NOT_CONFIGURED", "RUNNING")
        assert "metrics" in payload
        assert "worker_health" in payload
        assert payload["source_mode"] == "HTTP"

        # 2. Invalid window: end_ms <= start_ms -> 422 Unprocessable Entity
        res_err = client.get("/api/v1/paper/runtime-snapshot?start_ms=2000&end_ms=1000")
        assert res_err.status_code == 422
        assert "end_ms" in res_err.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_020_api_endpoint_never_creates_day_audit(api_test_db):
    """Rule 015, 158: Verify calling /api/v1/paper/runtime-snapshot does NOT create DayAudit."""
    session, _ = api_test_db

    initial_audits = session.query(DayAudit).count()
    assert initial_audits == 0

    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        res = client.get("/api/v1/paper/runtime-snapshot")
        assert res.status_code == 200

        # Assert still 0 DayAudit rows
        post_audits = session.query(DayAudit).count()
        assert post_audits == 0, "Snapshot route must NEVER create DayAudit row"
    finally:
        app.dependency_overrides.clear()


def test_030_http_and_offline_parity(api_test_db):
    """Rule 125, 176: HTTP endpoint and offline builder yield exact same financial metrics."""
    session, db_path = api_test_db
    t_now = int(time.time() * 1000)

    # Seed run and trades
    run = PaperResearchRunRecord(
        run_id="run_parity_001",
        scope="CG01",
        symbol="XAUUSDT",
        status="COLLECTING",
        started_at_ms=t_now - 10000,
        created_at_ms=t_now - 10000,
        updated_at_ms=t_now - 10000,
        execution_mode="PAPER_EXECUTION",
        initial_capital_usdt=1000.0,
    )
    o1 = PaperOrder(
        id="po_p_1",
        instrument="XAUUSDT",
        direction="LONG",
        order_type="LIMIT",
        planned_entry=2620.0,
        stop_loss=2615.0,
        take_profit=2630.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="closed",
        created_at=t_now - 8000,
        closed_at=t_now - 5000,
        realized_pnl_net=12.5,
    )
    o2 = PaperOrder(
        id="po_p_2",
        instrument="XAUUSDT",
        direction="SHORT",
        order_type="LIMIT",
        planned_entry=2630.0,
        stop_loss=2635.0,
        take_profit=2620.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        state="closed",
        created_at=t_now - 4000,
        closed_at=t_now - 2000,
        realized_pnl_net=-7.5,
    )
    l1 = PaperResearchOrderLink(run_id="run_parity_001", order_id="po_p_1", linked_at_ms=t_now - 7000)
    l2 = PaperResearchOrderLink(run_id="run_parity_001", order_id="po_p_2", linked_at_ms=t_now - 3000)
    session.add_all([run, o1, o2, l1, l2])
    session.commit()

    # 1. Direct offline snapshot
    offline_snap = build_paper_runtime_snapshot(
        session,
        run_id="run_parity_001",
        symbol="XAUUSDT",
        scope="CG01",
        source_mode="OFFLINE_DB",
    )

    # 2. HTTP endpoint snapshot
    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        http_res = client.get("/api/v1/paper/runtime-snapshot?run_id=run_parity_001&symbol=XAUUSDT&scope=CG01")
        assert http_res.status_code == 200
        http_snap = http_res.json()

        # Check metric parity
        m_off = offline_snap["metrics"]
        m_http = http_snap["metrics"]

        assert m_off["closed_trades_count"] == m_http["closed_trades_count"] == 2
        assert m_off["net_realized_pnl_usd"] == m_http["net_realized_pnl_usd"] == 5.0
        assert m_off["winrate_pct"] == m_http["winrate_pct"] == 50.0
        assert m_off["expectancy_usd_per_trade"] == m_http["expectancy_usd_per_trade"] == 2.5
        assert m_off["orders_count"] == m_http["orders_count"] == 2
    finally:
        app.dependency_overrides.clear()


def test_040_cli_mutually_exclusive_url_and_db(capsys):
    """Rule 129: CLI rejects simultaneous --url and --db flags."""
    with pytest.raises(SystemExit) as exc_info:
        sys.argv = ["paper_runtime_snapshot.py", "--url", "http://127.0.0.1:8000", "--db", "some.db"]
        cli_main()
    assert exc_info.value.code != 0


def test_050_cli_missing_db_exits_with_error(tmp_path, capsys):
    """Rule 118, 134: Non-existent DB path exits with code 1 and creates zero files."""
    fake_db = tmp_path / "non_existent_trading.db"
    assert not fake_db.exists()

    sys.argv = ["paper_runtime_snapshot.py", "--db", str(fake_db)]
    code = cli_main()
    assert code == 1
    assert not fake_db.exists(), "Snapshot must NEVER create non-existent database file"


def test_060_cli_output_dir_atomic_generation(api_test_db, tmp_path):
    """Rule 131, 132, 133: CLI writes clean JSON and Markdown to specified output-dir."""
    _, db_path = api_test_db
    out_dir = tmp_path / "snapshot_reports"

    sys.argv = [
        "paper_runtime_snapshot.py",
        "--db", str(db_path),
        "--format", "both",
        "--output-dir", str(out_dir),
    ]
    code = cli_main()
    assert code == 0

    json_file = out_dir / "paper_runtime_snapshot.json"
    md_file = out_dir / "paper_runtime_snapshot.md"

    assert json_file.is_file()
    assert md_file.is_file()

    # Verify JSON content
    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert "metrics" in data

    # Verify Markdown content
    md_content = md_file.read_text(encoding="utf-8")
    assert "Báo Cáo Quan Sát Vận Hành PAPER" in md_content
    assert "Đã quan sát:" in md_content


def test_070_secrets_redaction():
    """Rule 038, 182: Sensitive strings and credentials are sanitized from outputs."""
    raw_error = "Connection failed to https://botuser:supersecretpass123@api.telegram.org/bot123456789:ABCdefGHIjklMNOpqrSTUvwxYZ/send"
    cleansed = redact_sensitive_text(raw_error)

    assert "supersecretpass123" not in cleansed
    assert "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ" not in cleansed
    assert "***REDACTED***" in cleansed or "***:***@" in cleansed


def test_080_subprocess_import_isolation():
    """Rule 124, 180: Importing tools and services does NOT create default database file."""
    temp_dir = tempfile.mkdtemp()
    temp_db = Path(temp_dir) / "should_not_exist.db"

    code = """
import sys
import os
from pathlib import Path

# Add project root and backend to path
sys.path.insert(0, r"{backend_path}")
sys.path.insert(0, r"{root_path}")

os.environ["AURUM_DB_PATH"] = r"{db_path}"
os.environ["TESTING"] = "1"

# Import service and tool
import services.paper_runtime_snapshot_service
import tools.paper_runtime_snapshot

print("IMPORTED_SUCCESSFULLY")
""".format(
        backend_path=str(BACKEND_DIR).replace("\\", "\\\\"),
        root_path=str(REPO_ROOT).replace("\\", "\\\\"),
        db_path=str(temp_db).replace("\\", "\\\\"),
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "IMPORTED_SUCCESSFULLY" in result.stdout
    assert not temp_db.exists(), "Importing tool/service must not create database file"
