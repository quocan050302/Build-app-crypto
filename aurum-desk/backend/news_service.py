import csv
import io
import json
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Tuple, Optional
import schemas, models, crud

DATE_FORMATS = [
    "%m-%d-%Y",
    "%m/%d/%Y",
    "%Y-%m-%d",
    "%b %d, %Y",
    "%b %d %Y",
    "%B %d, %Y",
    "%d-%m-%Y",
    "%d/%m/%Y",
]

TIME_FORMATS = [
    "%I:%M%p",
    "%I:%M %p",
    "%H:%M",
    "%H:%M:%S",
]

DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m-%d-%Y %H:%M:%S",
    "%m-%d-%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
]

def determine_gold_relevance(currency: str, impact: str, title: str) -> str:
    """Determine XAUUSDT relevance based on currency, impact and indicator keywords."""
    curr = (currency or "").upper().strip()
    imp = (impact or "").upper().strip()
    t = (title or "").lower().strip()

    # Core high impact USD indicators directly impacting Gold via USD & yields
    core_usd_high = any(k in t for k in [
        "cpi", "non-farm", "nfp", "employment", "unemployment", "fed", "fomc",
        "funds rate", "gdp", "retail sales", "ppi", "pce", "ism manufacturing", "ism services"
    ])

    if curr == "USD":
        if imp == "HIGH" or core_usd_high:
            return "HIGH"
        elif imp in ("MEDIUM", "MED"):
            return "MEDIUM"
        else:
            return "LOW"
    elif curr in ("EUR", "GBP", "JPY", "CNY") and imp == "HIGH":
        return "MEDIUM"
    return "LOW"


def extract_event_type_id(url: Optional[str], title: str) -> str:
    """Extract event series ID from Forex Factory calendar URL or slugify title."""
    if url:
        # e.g. https://www.forexfactory.com/calendar/11-us-unemployment-claims
        match = re.search(r'/calendar/([0-9a-zA-Z\-_]+)', url)
        if match:
            return match.group(1).lower()
    # Fallback to slugified title
    clean_title = re.sub(r'[^a-zA-Z0-9]+', '-', title).strip('-').lower()
    return clean_title[:60]


def parse_date_and_time(
    date_str: str,
    time_str: str,
    source_tz_str: str = "America/New_York"
) -> Tuple[Optional[int], str, Optional[str]]:
    """
    Parse Date and Time strings honoring source timezone.
    Returns: (utc_ms, schedule_kind, error_message)
    Never falls back to current time.
    """
    date_clean = (date_str or "").strip()
    time_clean = (time_str or "").strip()

    if not date_clean:
        return None, "ERROR", "Cột Date bị trống."

    # Check for ISO format with offset first (e.g. 2026-10-08T08:30:00-04:00)
    if "T" in date_clean:
        try:
            dt = datetime.fromisoformat(date_clean.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000), "EXACT", None
        except Exception:
            pass

    # Check for special schedule kinds (All Day, Tentative, TBA)
    time_lower = time_clean.lower()
    schedule_kind = "EXACT"
    is_special_time = False

    if any(k in time_lower for k in ["all day", "day 1", "day 2", "tentative", "tba", "unknown"]):
        is_special_time = True
        if "all day" in time_lower:
            schedule_kind = "ALL_DAY"
        elif "tentative" in time_lower:
            schedule_kind = "TENTATIVE"
        elif "tba" in time_lower:
            schedule_kind = "TBA"
        else:
            schedule_kind = "SPECIAL"
        time_clean = "00:00"

    # Resolve source timezone
    try:
        source_tz = ZoneInfo(source_tz_str)
    except Exception:
        source_tz = ZoneInfo("America/New_York")

    parsed_dt = None

    # If date_clean already contains time (e.g. 2026-10-08 18:00:00) and time_clean is empty
    if not time_clean or time_clean == "00:00":
        for dt_fmt in DATETIME_FORMATS:
            try:
                dt_naive = datetime.strptime(date_clean, dt_fmt)
                parsed_dt = dt_naive.replace(tzinfo=source_tz)
                break
            except ValueError:
                continue

    # Try matching Date + Time combinations
    if not parsed_dt:
        time_candidates = [time_clean] if time_clean else ["00:00"]

        for d_fmt in DATE_FORMATS:
            for t_fmt in TIME_FORMATS:
                for t_val in time_candidates:
                    combined_str = f"{date_clean} {t_val}"
                    combined_fmt = f"{d_fmt} {t_fmt}"
                    try:
                        dt_naive = datetime.strptime(combined_str, combined_fmt)
                        parsed_dt = dt_naive.replace(tzinfo=source_tz)
                        break
                    except ValueError:
                        continue
                if parsed_dt:
                    break
            if parsed_dt:
                break

    # If still not parsed, try date alone
    if not parsed_dt:
        for d_fmt in DATE_FORMATS:
            try:
                dt_naive = datetime.strptime(date_clean, d_fmt)
                parsed_dt = dt_naive.replace(hour=0, minute=0, second=0, tzinfo=source_tz)
                if not is_special_time:
                    schedule_kind = "ALL_DAY"
                break
            except ValueError:
                continue

    if not parsed_dt:
        return None, "ERROR", f"Không thể phân tích định dạng ngày giờ: '{date_clean} {time_clean}'"

    # Convert to UTC milliseconds
    utc_ms = int(parsed_dt.astimezone(timezone.utc).timestamp() * 1000)
    return utc_ms, schedule_kind, None


def parse_csv_calendar_preview(
    csv_content: str,
    source_timezone: str = "America/New_York"
) -> schemas.NewsImportPreviewResponse:
    """
    Parse Forex Factory CSV calendar into preview rows with validation,
    time conversion to UTC+7 and source timezone, and error quarantine.
    """
    # Remove BOM if present
    content = csv_content.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(content))

    preview_rows: List[schemas.NewsImportRowPreview] = []
    errors: List[str] = []
    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")

    row_index = 0
    valid_count = 0
    invalid_count = 0
    last_date = ""

    for raw_row in reader:
        row_index += 1
        # Normalize headers and strip values
        row = {str(k).strip().lower(): str(v).strip() if v is not None else "" for k, v in raw_row.items() if k}

        title = row.get("title") or row.get("event") or "Economic Event"
        country = (row.get("country") or row.get("currency") or "USD").upper()
        date_raw = row.get("date", "").strip()
        
        if date_raw:
            last_date = date_raw
        else:
            date_raw = last_date
            
        time_raw = row.get("time", "").strip()
        impact_raw = row.get("impact", "Low").capitalize()
        forecast = row.get("forecast") or None
        previous = row.get("previous") or None
        actual = row.get("actual") or None
        url = row.get("url") or None

        # Clean forecast/previous/actual
        if forecast == "":
            forecast = None
        if previous == "":
            previous = None
        if actual == "":
            actual = None

        utc_ms, schedule_kind, err_msg = parse_date_and_time(date_raw, time_raw, source_timezone)

        if err_msg or utc_ms is None:
            invalid_count += 1
            err_line = f"Dòng {row_index} ({title}): {err_msg}"
            errors.append(err_line)
            preview_rows.append(
                schemas.NewsImportRowPreview(
                    row_index=row_index,
                    title=title,
                    country=country,
                    impact=impact_raw,
                    source_time_str=f"{date_raw} {time_raw}".strip(),
                    scheduled_at_utc_ms=0,
                    time_vn_str="Lỗi định dạng",
                    forecast=forecast,
                    previous=previous,
                    actual=actual,
                    url=url,
                    gold_relevance=determine_gold_relevance(country, impact_raw, title),
                    is_valid=False,
                    error_message=err_msg
                )
            )
        else:
            valid_count += 1
            dt_vn = datetime.fromtimestamp(utc_ms / 1000.0, vn_tz)
            time_vn_str = dt_vn.strftime("%H:%M %d/%m/%Y")
            if schedule_kind != "EXACT":
                time_vn_str += f" ({schedule_kind})"

            preview_rows.append(
                schemas.NewsImportRowPreview(
                    row_index=row_index,
                    title=title,
                    country=country,
                    impact=impact_raw,
                    source_time_str=f"{date_raw} {time_raw}".strip(),
                    scheduled_at_utc_ms=utc_ms,
                    time_vn_str=time_vn_str,
                    forecast=forecast,
                    previous=previous,
                    actual=actual,
                    url=url,
                    gold_relevance=determine_gold_relevance(country, impact_raw, title),
                    is_valid=True,
                    error_message=None
                )
            )

    return schemas.NewsImportPreviewResponse(
        total_rows=row_index,
        valid_count=valid_count,
        invalid_count=invalid_count,
        source_timezone=source_timezone,
        preview_rows=preview_rows,
        errors=errors
    )


def commit_parsed_news(
    db: Any,
    csv_content: str,
    source_timezone: str = "America/New_York"
) -> Tuple[int, int, int]:
    """
    Commit validated CSV rows to database.
    Returns: (imported_count, skipped_duplicates_count, error_count)
    """
    preview = parse_csv_calendar_preview(csv_content, source_timezone)
    now_ms = int(time.time() * 1000)

    imported = 0
    skipped = 0
    errors = preview.invalid_count

    for row in preview.preview_rows:
        if not row.is_valid or row.scheduled_at_utc_ms == 0:
            continue

        event_type_id = extract_event_type_id(row.url, row.title)
        source_id = f"ff-{row.country.lower()}-{event_type_id}-{row.scheduled_at_utc_ms}".replace(" ", "_")

        # Idempotency check: check if event already exists
        existing = db.query(models.EconomicNews).filter(
            (models.EconomicNews.source_id == source_id) |
            (
                (models.EconomicNews.title == row.title) &
                (models.EconomicNews.scheduled_at == row.scheduled_at_utc_ms)
            )
        ).first()

        if existing:
            # Update mutable fields (forecast, previous, source_url, gold_relevance) if changed
            existing.forecast = row.forecast or existing.forecast
            existing.previous = row.previous or existing.previous
            existing.source_url = row.url or existing.source_url
            existing.gold_relevance = row.gold_relevance
            existing.event_type_id = event_type_id
            skipped += 1
            continue

        news_record = models.EconomicNews(
            source_id=source_id,
            title=row.title,
            country=row.country,
            currency=row.country,
            impact=row.impact,
            scheduled_at=row.scheduled_at_utc_ms,
            received_at=now_ms,
            forecast=row.forecast,
            previous=row.previous,
            actual=row.actual if row.actual else None,  # Missing actual is None, not 0
            revised=None,
            source_url=row.url,
            event_type_id=event_type_id,
            source_timezone=source_timezone,
            source_time_raw=row.source_time_str,
            schedule_kind="EXACT" if "(" not in row.time_vn_str else row.time_vn_str.split("(")[-1].replace(")", ""),
            gold_relevance=row.gold_relevance,
            research_status="NOT_FETCHED",
            research_assessment=None,
            research_fetched_at=None,
            raw_json=json.dumps({
                "source": "FOREX_FACTORY_CSV",
                "imported_at": now_ms,
                "source_timezone": source_timezone,
                "url": row.url
            })
        )
        db.add(news_record)
        imported += 1

    db.commit()
    return imported, skipped, errors


# Backwards compatibility wrappers
def parse_forex_factory_json(json_content: str) -> List[schemas.EconomicNewsItem]:
    """Parse Forex Factory weekly JSON export (kept for backwards compatibility)."""
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
        url = item.get("url")
        event_id = extract_event_type_id(url, title)

        utc_ms, schedule_kind, err = parse_date_and_time(date_str, "", "UTC")
        scheduled_at = utc_ms if utc_ms else now_ms
        forecast = str(item.get("forecast") or "")
        previous = str(item.get("previous") or "")
        actual = str(item.get("actual") or "") if item.get("actual") else None

        source_id = f"ff-{country.lower()}-{event_id}-{scheduled_at}".replace(" ", "_")

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
                actual=actual,
                source_url=url,
                event_type_id=event_id,
                source_timezone="UTC",
                gold_relevance=determine_gold_relevance(country, impact, title)
            )
        )
    return parsed


def parse_csv_calendar(csv_content: str) -> List[schemas.EconomicNewsItem]:
    """Legacy helper returning schemas.EconomicNewsItem list."""
    preview = parse_csv_calendar_preview(csv_content, "America/New_York")
    now_ms = int(time.time() * 1000)
    items: List[schemas.EconomicNewsItem] = []
    for r in preview.preview_rows:
        if not r.is_valid:
            continue
        event_id = extract_event_type_id(r.url, r.title)
        items.append(
            schemas.EconomicNewsItem(
                source_id=f"csv-{r.country.lower()}-{event_id}-{r.scheduled_at_utc_ms}".replace(" ", "_"),
                title=r.title,
                country=r.country,
                currency=r.country,
                impact=r.impact,
                scheduled_at=r.scheduled_at_utc_ms,
                received_at=now_ms,
                forecast=r.forecast,
                previous=r.previous,
                actual=r.actual,
                source_url=r.url,
                event_type_id=event_id,
                source_timezone="America/New_York",
                source_time_raw=r.source_time_str,
                gold_relevance=r.gold_relevance
            )
        )
    return items
