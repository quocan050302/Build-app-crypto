import pytest
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models, crud
from news_service import parse_forex_factory_json, parse_csv_calendar

@pytest.fixture
def news_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


def test_parse_forex_factory_json():
    json_sample = """[
        {
            "title": "Core CPI m/m",
            "country": "USD",
            "date": "2026-10-08T08:30:00-04:00",
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.2%"
        },
        {
            "title": "Unemployment Claims",
            "country": "USD",
            "date": "2026-10-08T08:30:00-04:00",
            "impact": "Medium",
            "forecast": "220K",
            "previous": "225K"
        }
    ]"""
    items = parse_forex_factory_json(json_sample)
    assert len(items) == 2
    assert items[0].title == "Core CPI m/m"
    assert items[0].impact == "High"
    assert items[0].country == "USD"
    assert items[0].forecast == "0.3%"


def test_parse_csv_calendar():
    csv_sample = """title,country,date,impact,forecast,previous,actual
FOMC Rate Decision,USD,2026-10-08 18:00:00,High,5.25%,5.25%,5.25%
ISM Services PMI,USD,2026-10-08 14:00:00,Medium,51.5,50.8,
"""
    items = parse_csv_calendar(csv_sample)
    assert len(items) == 2
    assert items[0].title == "FOMC Rate Decision"
    assert items[0].impact == "High"
    assert items[0].actual == "5.25%"


def test_news_blackout_window(news_db):
    """
    Test news blackout window:
    - High impact: -30m / +15m
    - FOMC: -60m / +30m
    """
    now_ms = 1791450000000 # Reference time

    # Event 1: High impact CPI at now + 20 minutes (within 30m window)
    cpi_event = models.EconomicNews(
        title="CPI m/m",
        country="USD",
        currency="USD",
        impact="High",
        scheduled_at=now_ms + (20 * 60 * 1000),
        received_at=now_ms
    )
    news_db.add(cpi_event)
    news_db.commit()

    is_blackout, reason, remaining_min = crud.check_news_blackout(news_db, now_ms)
    assert is_blackout is True
    assert "CPI m/m" in reason
    assert remaining_min > 0

    # Test time after blackout (+20m after scheduled time, outside +15m window)
    time_after = cpi_event.scheduled_at + (20 * 60 * 1000)
    is_blackout_after, _, _ = crud.check_news_blackout(news_db, time_after)
    assert is_blackout_after is False
