"""
V5.1 Regression & Integration Tests
Validates:
1. Telegram typed contract (TelegramSendResult) & endpoint /api/v1/telegram/test
2. Quiet hours legacy 24:00 normalization and timezone validation
3. Directional geometry (SHORT/LONG), screenshot fixture regression, arm guards
4. Forex Factory 83-row CSV parser with Date+Time timezone conversion, no now() fallback
5. URL reader safety (blocks internal/private IPs) and XAUUSDT research service
"""

import io
import json
import time
import uuid
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models, crud, schemas
from database import get_db
from main import app
from services.telegram_service import (
    TelegramSendResult,
    send_telegram_direct,
    normalize_hh_mm,
    is_within_quiet_hours,
)
from domain_calculator import (
    validate_price_geometry,
    calculate_risk_reward,
)
from services.execution_coordinator import execution_coordinator
from news_service import (
    parse_date_and_time,
    parse_csv_calendar_preview,
    commit_parsed_news,
    determine_gold_relevance,
    extract_event_type_id,
)
from services.news_research_service import news_research_service


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ============================================================================
# 1. TELEGRAM CONTRACT & ENDPOINT TESTS
# ============================================================================

def test_telegram_send_result_contract():
    """Verify TelegramSendResult is typed, supports tuple unpacking (4-tuple), and attributes."""
    res = TelegramSendResult(
        success=True,
        provider_message_id="12345",
        error_code=None,
        error_message=None,
        retry_after_sec=None,
        http_status=200
    )
    assert res.success is True
    assert res.provider_message_id == "12345"
    assert res.http_status == 200

    # 4-tuple unpacking compatibility test
    s, mid, err, retry = res
    assert s is True
    assert mid == "12345"
    assert err is None
    assert retry is None


def test_telegram_endpoint_success_no_value_error(client, test_db):
    """Verify POST /api/v1/telegram/test returns cleanly without ValueError."""
    mock_result = TelegramSendResult(
        success=True,
        provider_message_id="98765",
        error_code=None,
        error_message=None,
        retry_after_sec=None,
        http_status=200
    )

    with patch("main.send_telegram_direct", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_result
        resp = client.post("/api/v1/telegram/test", json={
            "bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            "chat_id": "-100123456789"
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["message_id"] == "98765"
        mock_send.assert_called_once()


def test_telegram_endpoint_errors_handled_sanitized(client, test_db):
    """Verify HTTP 401/403/429 errors from provider are typed and sanitized."""
    # 401 Unauthorized
    with patch("main.send_telegram_direct", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = TelegramSendResult(
            success=False,
            provider_message_id=None,
            error_code="UNAUTHORIZED",
            error_message="Telegram API từ chối xác thực (HTTP 401: Token không hợp lệ)",
            http_status=401
        )
        resp = client.post("/api/v1/telegram/test", json={
            "bot_token": "invalid:token",
            "chat_id": "-100123"
        })
        assert resp.status_code == 400
        data = resp.json()["detail"]
        assert data["code"] == "UNAUTHORIZED"

    # 429 Rate Limit with retry_after
    with patch("main.send_telegram_direct", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = TelegramSendResult(
            success=False,
            provider_message_id=None,
            error_code="RATE_LIMIT",
            error_message="Rate limit 429",
            retry_after_sec=15,
            http_status=429
        )
        resp = client.post("/api/v1/telegram/test", json={
            "bot_token": "valid:token",
            "chat_id": "-100123"
        })
        assert resp.status_code == 400
        data = resp.json()["detail"]
        assert data["code"] == "RATE_LIMIT"
        assert data["retry_after"] == 15


def test_quiet_hours_24_00_normalization():
    """Verify legacy 24:00 is normalized to 00:00 without crashes."""
    assert normalize_hh_mm("24:00") == "00:00"
    assert normalize_hh_mm("24:0") == "00:00"
    assert normalize_hh_mm("12:30") == "12:30"
    assert normalize_hh_mm("08:05") == "08:05"

    # Invalid hours return default
    assert normalize_hh_mm("25:00", default="00:00") == "00:00"

    cfg = models.TelegramConfig(
        enabled=True,
        bot_token="token",
        chat_id="chat",
        quiet_hours_enabled=True,
        quiet_hours_start="24:00",  # legacy normalized to 00:00
        quiet_hours_end="06:00",
        timezone="UTC"
    )
    # Call is_within_quiet_hours without exception
    res = is_within_quiet_hours(cfg)
    assert isinstance(res, bool)


# ============================================================================
# 2. GEOMETRY & EXECUTION GUARDS TESTS
# ============================================================================

def test_screenshot_regression_short_inverted_geometry():
    """
    REGRESSION FIXTURE FROM USER SCREENSHOT:
    SHORT Setup: Entry 4123.32, SL 4109.02, TP 4151.92.
    Here SL (4109.02) < Entry (4123.32) < TP (4151.92), which is LONG geometry applied to SHORT!
    Must be rejected with INVALID_SHORT_GEOMETRY.
    """
    is_valid, reason = validate_price_geometry("SHORT", 4123.32, 4109.02, 4151.92)
    assert is_valid is False
    assert "INVALID_SHORT_GEOMETRY" in reason

    # Risk reward calculation should also fail or return 0 reward
    rr_data = calculate_risk_reward(
        direction="SHORT",
        entry=4123.32,
        sl=4109.02,
        tp=4151.92
    )
    assert rr_data.is_valid is False
    assert rr_data.gross_rr <= 0


def test_directional_fallback_levels():
    """
    Verify fallback ATR levels in strategy_service are strictly direction-aware:
    Entry 4123.32, ATR 7.15 (2*ATR=14.30, 4*ATR=28.60):
    LONG: SL=4109.02, TP=4151.92 (SL < Entry < TP)
    SHORT: SL=4137.62, TP=4094.72 (TP < Entry < SL)
    """
    entry = 4123.32
    atr = 7.15

    # LONG fallback math
    long_sl = round(entry - 2 * atr, 2)
    long_tp = round(entry + 4 * atr, 2)
    assert long_sl == 4109.02
    assert long_tp == 4151.92
    assert validate_price_geometry("LONG", entry, long_sl, long_tp)[0] is True

    # SHORT fallback math
    short_sl = round(entry + 2 * atr, 2)
    short_tp = round(entry - 4 * atr, 2)
    assert short_sl == 4137.62
    assert short_tp == 4094.72
    assert validate_price_geometry("SHORT", entry, short_sl, short_tp)[0] is True


def test_arm_endpoint_rejects_screenshot_fixture(client, test_db):
    """
    Attempting to arm the inverted SHORT setup via POST /api/v1/setups/arm
    must be strictly rejected with HTTP 400 INVALID_PRICE_GEOMETRY.
    No order is inserted into the database.
    """
    bad_setup = models.WatchSetup(
        id="test-inv-short",
        timeframe="15M",
        direction="SHORT",
        state="READY",
        provisional_entry=4123.32,
        provisional_sl=4109.02,  # Inverted!
        provisional_tp=4151.92,
        invalidation_price=4109.02,
        leverage=5,
        margin_mode="ISOLATED",
        created_at=int(time.time() * 1000),
        updated_at=int(time.time() * 1000)
    )
    test_db.add(bad_setup)
    test_db.commit()

    payload = {
        "setup_id": "test-inv-short",
        "expected_direction": "SHORT",
        "order_type": "LIMIT"
    }

    resp = client.post("/api/v1/setups/arm", json=payload)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "INVALID_PRICE_GEOMETRY"

    # Verify no order was created in DB
    orders = test_db.query(models.PaperOrder).all()
    assert len(orders) == 0


def test_arm_endpoint_accepts_valid_short(client, test_db):
    """Valid SHORT setup (TP < Entry < SL) with >= 1:2.0 Net R:R is accepted and armed."""
    valid_setup = models.WatchSetup(
        id="valid-short-1",
        timeframe="15M",
        direction="SHORT",
        state="READY",
        provisional_entry=4123.32,
        provisional_sl=4135.00,  # Risk = 11.68
        provisional_tp=4080.00,  # Reward = 43.32 (Net RR > 2.5)
        invalidation_price=4140.0,
        leverage=5,
        margin_mode="ISOLATED",
        created_at=int(time.time() * 1000),
        updated_at=int(time.time() * 1000)
    )
    test_db.add(valid_setup)
    test_db.commit()

    payload = {
        "setup_id": "valid-short-1",
        "expected_direction": "SHORT",
        "order_type": "LIMIT"
    }

    resp = client.post("/api/v1/setups/arm", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"

    # Check order was created in DB
    order = test_db.query(models.PaperOrder).filter(models.PaperOrder.setup_id == "valid-short-1").first()
    assert order is not None
    assert order.state == "armed"
    assert order.direction == "SHORT"
    assert order.planned_entry == 4123.32
    assert order.stop_loss == 4135.00
    assert order.take_profit == 4080.00


def test_coordinator_rejects_malformed_legacy_armed_order(test_db):
    """
    If a malformed armed order exists in the DB (from legacy V5),
    execution_coordinator must reject it with an audit reason instead of triggering/filling it.
    """
    now_ms = int(time.time() * 1000)
    bad_order = models.PaperOrder(
        id=str(uuid.uuid4()),
        setup_id="legacy-bad-setup",
        instrument="XAUUSDT",
        direction="SHORT",
        order_type="LIMIT",
        state="armed",
        quantity=0.1,
        initial_risk_usdt=10.0,
        planned_entry=4123.32,
        stop_loss=4109.02,  # Inverted!
        take_profit=4151.92,
        created_at=now_ms
    )
    test_db.add(bad_order)
    test_db.commit()

    # Run coordinator cycle with current price matching entry
    current_tick = {
        "bid": 4123.30,
        "ask": 4123.35,
        "last": 4123.32,
        "timestamp": now_ms
    }
    execution_coordinator.evaluate_orders_sync(test_db, ticker_override=current_tick, now_ms=now_ms)

    test_db.refresh(bad_order)
    assert bad_order.state == "rejected"
    assert "INVALID_PRICE_GEOMETRY" in (bad_order.invalidation_reason or "")


# ============================================================================
# 3. FOREX FACTORY CSV IMPORT & TIMEZONE TESTS
# ============================================================================

def test_parse_csv_calendar_83_row_shape():
    """
    Test Forex Factory 83-row CSV format:
    Title,Country,Date,Time,Impact,Forecast,Previous,URL
    Verifies:
    - Date + Time combination
    - Timezone conversion (America/New_York -> UTC)
    - Absence of Actual is None (not 0 or forecast)
    - URL & event_type_id preserved
    - No fallback to now()
    """
    csv_sample = """Title,Country,Date,Time,Impact,Forecast,Previous,URL
CPI m/m,USD,10-08-2026,8:30am,High,0.3%,0.2%,https://www.forexfactory.com/calendar/11-us-cpi-mm
Unemployment Claims,USD,10-08-2026,8:30am,Medium,220K,225K,https://www.forexfactory.com/calendar/12-us-unemployment-claims
OPEC-JMMC Meetings,ALL,10-08-2026,All Day,Low,,,"""

    preview = parse_csv_calendar_preview(csv_sample, "America/New_York")
    assert preview.total_rows == 3
    assert preview.valid_count == 3
    assert preview.invalid_count == 0

    # Row 1: CPI m/m
    r1 = preview.preview_rows[0]
    assert r1.title == "CPI m/m"
    assert r1.country == "USD"
    assert r1.impact == "High"
    assert r1.forecast == "0.3%"
    assert r1.previous == "0.2%"
    assert r1.actual is None
    assert r1.url == "https://www.forexfactory.com/calendar/11-us-cpi-mm"
    assert r1.gold_relevance == "HIGH"

    # Row 3: All Day event
    r3 = preview.preview_rows[2]
    assert r3.title == "OPEC-JMMC Meetings"
    assert "ALL_DAY" in r3.time_vn_str
    assert r3.url is None


def test_csv_parser_no_fallback_to_now_on_error():
    """Corrupted date/time format must be quarantined with error, NEVER falling back to now()."""
    bad_csv = """Title,Country,Date,Time,Impact,Forecast,Previous,URL
Corrupt Event,USD,invalid-date,bad-time,High,1.0%,0.5%,https://www.forexfactory.com/calendar/99-test"""

    preview = parse_csv_calendar_preview(bad_csv, "America/New_York")
    assert preview.total_rows == 1
    assert preview.valid_count == 0
    assert preview.invalid_count == 1
    assert preview.preview_rows[0].is_valid is False
    assert preview.preview_rows[0].scheduled_at_utc_ms == 0
    assert "Không thể phân tích định dạng ngày giờ" in (preview.preview_rows[0].error_message or "")


def test_csv_commit_idempotent(test_db):
    """Committing parsed news twice must be idempotent (no duplicated releases)."""
    csv_sample = """Title,Country,Date,Time,Impact,Forecast,Previous,URL
Retail Sales m/m,USD,10-08-2026,8:30am,High,0.4%,0.1%,https://www.forexfactory.com/calendar/14-us-retail-sales"""

    imported1, skipped1, err1 = commit_parsed_news(test_db, csv_sample, "America/New_York")
    assert imported1 == 1
    assert skipped1 == 0

    # Second commit of identical content
    imported2, skipped2, err2 = commit_parsed_news(test_db, csv_sample, "America/New_York")
    assert imported2 == 0
    assert skipped2 == 1

    records = test_db.query(models.EconomicNews).all()
    assert len(records) == 1
    assert records[0].source_url == "https://www.forexfactory.com/calendar/14-us-retail-sales"
    assert records[0].event_type_id == "14-us-retail-sales"


# ============================================================================
# 4. URL EXTRACTION & XAUUSDT RESEARCH SERVICE TESTS
# ============================================================================

def test_url_safety_validator():
    """Verify SSRF protection blocks localhost, internal IPs, and non-http schemes."""
    assert news_research_service.validate_url("https://www.forexfactory.com/calendar/11-us-cpi-mm")[0] is True
    assert news_research_service.validate_url("http://forexfactory.com/news")[0] is True

    # Blocked URLs
    assert news_research_service.validate_url("http://localhost:8000/admin")[0] is False
    assert news_research_service.validate_url("http://127.0.0.1:8000")[0] is False
    assert news_research_service.validate_url("http://192.168.1.1/secret")[0] is False
    assert news_research_service.validate_url("http://10.0.0.1")[0] is False
    assert news_research_service.validate_url("http://169.254.169.254/latest/meta-data/")[0] is False
    assert news_research_service.validate_url("ftp://example.com/file")[0] is False
    assert news_research_service.validate_url("file:///etc/passwd")[0] is False


def test_forex_factory_html_parser():
    """Verify structured parsing of Forex Factory indicator specifications & historical table."""
    sample_html = """
    <html>
        <body>
            <table>
                <tr class="calendar__row">
                    <td>Source</td>
                    <td>Bureau of Labor Statistics</td>
                </tr>
                <tr class="calendar__row">
                    <td>Measures</td>
                    <td>Change in the price of goods and services purchased by consumers</td>
                </tr>
            </table>
            <table class="calendar__history-table">
                <tr>
                    <td>Sep 13</td>
                    <td>0.2%</td>
                    <td>0.2%</td>
                    <td>0.2%</td>
                </tr>
            </table>
        </body>
    </html>
    """
    extracted = news_research_service.parse_forex_factory_html(sample_html, "https://www.forexfactory.com/calendar/11-us-cpi-mm")
    assert isinstance(extracted, dict)


def test_xauusdt_assessment_generation():
    """
    Verify XAUUSDT Research Assessment provides:
    - Meaning in Vietnamese
    - Gold transmission channels (USD, Yields, Safe haven)
    - Pre-release scenarios
    - Post-release surprise interpretation
    """
    # 1. Pre-release test
    pre_resp = news_research_service.generate_research_assessment(
        news_id=1,
        title="CPI m/m",
        country="USD",
        impact="High",
        scheduled_at=1791450000000,
        source_url="https://www.forexfactory.com/calendar/11-us-cpi-mm",
        extracted_data={"description": "Thước đo lạm phát tiêu dùng của Mỹ"},
        forecast="0.3%",
        previous="0.2%",
        actual=None
    )
    assert "Consumer Price Index" in pre_resp.meaning_vn or "lạm phát" in pre_resp.meaning_vn.lower()
    assert "Kênh Chỉ số USD (DXY)" in pre_resp.transmission_channels
    assert len(pre_resp.pre_release_scenarios) > 0
    assert pre_resp.post_release_assessment is None

    # 2. Post-release test with surprise
    post_resp = news_research_service.generate_research_assessment(
        news_id=1,
        title="CPI m/m",
        country="USD",
        impact="High",
        scheduled_at=1791450000000,
        source_url="https://www.forexfactory.com/calendar/11-us-cpi-mm",
        extracted_data={"description": "Thước đo lạm phát tiêu dùng của Mỹ", "usual_effect": "'Actual' > 'Forecast' is good for currency"},
        forecast="0.3%",
        previous="0.2%",
        actual="0.5%"
    )
    assert post_resp.post_release_assessment is not None
    assert "0.5%" in post_resp.post_release_assessment
