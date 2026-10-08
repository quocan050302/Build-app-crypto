import csv
import io
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Tuple
import schemas, models, crud

def parse_iso_or_custom_date(date_str: str) -> int:
    """Parse various datetime string formats into UTC timestamp in milliseconds"""
    date_str = date_str.strip()
    try:
        # ISO format like 2026-10-08T08:30:00-04:00 or 2026-10-08T12:30:00Z
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        pass

    # Try common formats
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M",
        "%m/%d/%Y %H:%M",
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y %H:%M"
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return int(dt.timestamp() * 1000)
        except Exception:
            continue

    # Fallback to current time
    return int(time.time() * 1000)


def parse_forex_factory_json(json_content: str) -> List[schemas.EconomicNewsItem]:
    """Parse Forex Factory weekly JSON export"""
    raw_data = json.loads(json_content)
    if not isinstance(raw_data, list):
        raise ValueError("Forex Factory JSON phải là một mảng các sự kiện.")

    now_ms = int(time.time() * 1000)
    parsed: List[schemas.EconomicNewsItem] = []

    for item in raw_data:
        title = item.get("title") or item.get("name") or "Economic Event"
        country = item.get("country") or item.get("currency") or "USD"
        impact = item.get("impact") or "Low"
        date_str = item.get("date") or item.get("datetime") or ""
        scheduled_at = parse_iso_or_custom_date(date_str) if date_str else now_ms
        forecast = str(item.get("forecast") or "")
        previous = str(item.get("previous") or "")
        actual = str(item.get("actual") or "") if item.get("actual") else None

        source_id = f"ff-{country}-{title}-{scheduled_at}".replace(" ", "_")

        parsed.append(
            schemas.EconomicNewsItem(
                source_id=source_id,
                title=title,
                country=country,
                currency=country,
                impact=impact.capitalize(),
                scheduled_at=scheduled_at,
                received_at=now_ms,
                forecast=forecast or None,
                previous=previous or None,
                actual=actual
            )
        )
    return parsed


def parse_csv_calendar(csv_content: str) -> List[schemas.EconomicNewsItem]:
    """Parse CSV economic calendar with title, country, date, impact, forecast, previous, actual"""
    reader = csv.DictReader(io.StringIO(csv_content))
    now_ms = int(time.time() * 1000)
    parsed: List[schemas.EconomicNewsItem] = []

    for row in reader:
        # Normalize key lookups
        keys_lower = {k.strip().lower(): v for k, v in row.items() if k}
        title = keys_lower.get("title") or keys_lower.get("event") or "Economic Event"
        country = keys_lower.get("country") or keys_lower.get("currency") or "USD"
        impact = keys_lower.get("impact") or "Medium"
        date_str = keys_lower.get("date") or keys_lower.get("time") or ""
        scheduled_at = parse_iso_or_custom_date(date_str) if date_str else now_ms

        forecast = keys_lower.get("forecast")
        previous = keys_lower.get("previous")
        actual = keys_lower.get("actual")

        source_id = f"csv-{country}-{title}-{scheduled_at}".replace(" ", "_")

        parsed.append(
            schemas.EconomicNewsItem(
                source_id=source_id,
                title=title,
                country=country,
                currency=country,
                impact=impact.capitalize(),
                scheduled_at=scheduled_at,
                received_at=now_ms,
                forecast=forecast or None,
                previous=previous or None,
                actual=actual or None
            )
        )
    return parsed
