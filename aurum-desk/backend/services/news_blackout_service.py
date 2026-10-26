"""
V14.2 - News blackout (structured, single source of window formulas).

`compute_news_blackout` is a pure function over already-read events.
`evaluate_news_blackout` is the ORM adapter (read-only, no commit, no network).
`crud.check_news_blackout` keeps its legacy tuple contract and delegates here, so
policy / entry guard / UI / replay all agree on the same event + end time.

Notes
- An empty result only means "no ACTIVE blackout event in the events read".
  It does not prove the calendar is complete; see `calendar_status`.
- All timestamps are epoch milliseconds (UTC).
- Window bounds are inclusive on both ends (same as the legacy implementation).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from math import ceil
from typing import Any, Dict, Iterable, List, Optional, Tuple

import models

MINUTE_MS = 60_000
# Widest window is (-60m, +30m); legacy DB read used a +-90m envelope.
READ_ENVELOPE_MS = 90 * MINUTE_MS
UPCOMING_LOOKAHEAD_MS = 4 * 60 * MINUTE_MS

_IMPACT_MAP = {
    "high": "High",
    "red": "High",
    "đỏ": "High",
    "medium": "Medium",
    "orange": "Medium",
    "low": "Low",
    "yellow": "Low",
    "holiday": "Holiday",
}

_FED_RE = re.compile(r"\b(?:FOMC|FED|FEDERAL\s+RESERVE)\b", re.I)
_RATE_DECISION_RE = re.compile(r"\bRATE\s+DECISION\b", re.I)


def normalize_impact(value: Optional[str]) -> str:
    key = (value or "").strip().casefold()
    return _IMPACT_MAP.get(key, "Unknown")


def is_fed_event(title: Optional[str]) -> bool:
    """Conservative classifier; never matches FED inside an unrelated word."""
    return bool(_FED_RE.search(title or ""))


@dataclass(frozen=True)
class NewsEventInput:
    event_id: str
    title: str
    currency: str
    impact: str
    scheduled_at_ms: int
    known_at_ms: Optional[int] = None
    received_at_ms: Optional[int] = None


@dataclass(frozen=True)
class NewsBlackoutView:
    is_blackout: bool
    evaluated_at_ms: int
    event_id: Optional[str] = None
    title: Optional[str] = None
    currency: Optional[str] = None
    impact: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    before_minutes: int = 0
    after_minutes: int = 0
    remaining_minutes: int = 0
    scheduled_at_ms: Optional[int] = None
    covered_event_ids: Tuple[str, ...] = ()
    # Informational only (never blocks): next High USD event outside any window.
    upcoming_title: Optional[str] = None
    upcoming_scheduled_ms: Optional[int] = None
    # "AVAILABLE" | "EMPTY" | "UNKNOWN": whether any calendar data exists at all.
    calendar_status: str = "UNKNOWN"
    calendar_updated_at_ms: Optional[int] = None

    def reason_text(self) -> Optional[str]:
        if not self.is_blackout:
            return None
        return (
            f"Tin High Impact: {self.title} "
            f"(Blackout -{self.before_minutes}m/+{self.after_minutes}m)"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_blackout": self.is_blackout,
            "evaluated_at_ms": self.evaluated_at_ms,
            "event_id": self.event_id,
            "title": self.title,
            "currency": self.currency,
            "impact": self.impact,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "before_minutes": self.before_minutes,
            "after_minutes": self.after_minutes,
            "remaining_minutes": self.remaining_minutes,
            "scheduled_at_ms": self.scheduled_at_ms,
            "covered_event_ids": list(self.covered_event_ids),
            "upcoming_title": self.upcoming_title,
            "upcoming_scheduled_ms": self.upcoming_scheduled_ms,
            "calendar_status": self.calendar_status,
            "calendar_updated_at_ms": self.calendar_updated_at_ms,
            "reason": self.reason_text(),
        }


def _blocks_gold(event: NewsEventInput) -> bool:
    """Default scope for XAUUSDT: High-impact USD, or an explicit Fed event when currency is missing."""
    if normalize_impact(event.impact) != "High":
        return False
    currency = (event.currency or "").strip().upper()
    if currency == "USD":
        return True
    return (not currency) and is_fed_event(event.title)


def _is_known(event: NewsEventInput, now_ms: int) -> bool:
    """Causal filter: an event received/known after now_ms cannot affect now_ms."""
    for t in (event.known_at_ms, event.received_at_ms):
        if t is not None and t > now_ms:
            return False
    return True


def _window_minutes(event: NewsEventInput) -> Tuple[int, int]:
    special = is_fed_event(event.title) or bool(_RATE_DECISION_RE.search(event.title or ""))
    return (60, 30) if special else (30, 15)


def compute_news_blackout(events: Iterable[NewsEventInput], now_ms: int) -> NewsBlackoutView:
    active: List[tuple] = []
    upcoming: List[NewsEventInput] = []
    for event in events:
        if not _blocks_gold(event) or not _is_known(event, now_ms):
            continue
        before, after = _window_minutes(event)
        start = event.scheduled_at_ms - before * MINUTE_MS
        end = event.scheduled_at_ms + after * MINUTE_MS
        if start <= now_ms <= end:
            active.append((end, str(event.event_id), start, event, before, after))
        elif now_ms < start and (start - now_ms) <= UPCOMING_LOOKAHEAD_MS:
            upcoming.append(event)

    next_event = min(upcoming, key=lambda e: (e.scheduled_at_ms, str(e.event_id)), default=None)
    up_title = next_event.title if next_event else None
    up_ms = next_event.scheduled_at_ms if next_event else None

    if not active:
        return NewsBlackoutView(False, now_ms, upcoming_title=up_title, upcoming_scheduled_ms=up_ms)

    # Primary = latest end; ties broken by stable event id. Resume time = max end.
    active.sort(key=lambda row: (row[0], row[1]))
    end, _, start, event, before, after = active[-1]
    return NewsBlackoutView(
        is_blackout=True,
        evaluated_at_ms=now_ms,
        event_id=str(event.event_id),
        title=event.title,
        currency=(event.currency or "").strip().upper() or None,
        impact="High",
        start_ms=start,
        end_ms=end,
        before_minutes=before,
        after_minutes=after,
        remaining_minutes=max(0, ceil((end - now_ms) / MINUTE_MS)),
        scheduled_at_ms=event.scheduled_at_ms,
        covered_event_ids=tuple(sorted(row[1] for row in active)),
        upcoming_title=up_title,
        upcoming_scheduled_ms=up_ms,
    )


def _row_to_input(row: Any) -> NewsEventInput:
    return NewsEventInput(
        event_id=str(row.id),
        title=row.title or "",
        currency=(getattr(row, "currency", None) or ""),
        impact=getattr(row, "impact", None) or "",
        scheduled_at_ms=int(row.scheduled_at),
        known_at_ms=getattr(row, "known_at", None),
        received_at_ms=getattr(row, "received_at", None),
    )


def evaluate_news_blackout(db, now_ms: int) -> NewsBlackoutView:
    """
    Read-only ORM adapter. Queries all impacts in the envelope (legacy 'Red' / casing
    variants are normalised in Python instead of being dropped by SQL).
    Never commits, never touches the network.
    """
    from sqlalchemy import func

    rows = (
        db.query(models.EconomicNews)
        .filter(
            models.EconomicNews.scheduled_at >= now_ms - READ_ENVELOPE_MS,
            models.EconomicNews.scheduled_at <= now_ms + UPCOMING_LOOKAHEAD_MS + READ_ENVELOPE_MS,
        )
        .all()
    )
    view = compute_news_blackout([_row_to_input(r) for r in rows], now_ms)

    total, last_received = db.query(
        func.count(models.EconomicNews.id), func.max(models.EconomicNews.received_at)
    ).one()
    return replace(
        view,
        calendar_status="AVAILABLE" if (total or 0) > 0 else "EMPTY",
        calendar_updated_at_ms=int(last_received) if last_received is not None else None,
    )
