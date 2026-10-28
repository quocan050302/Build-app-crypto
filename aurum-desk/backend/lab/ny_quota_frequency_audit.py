"""V14.3 NY quota frequency audit (isolated lab tool).

Answers ONE question: on which requested NY days did the PRODUCTION fallback flow find / arm / fill an entry,
and when it did not - why. It is NOT a profit/win-rate backtest and never touches the live database.

Isolation
  * every NY day runs on its own fresh in-memory SQLite DB (no journal/live quota can be polluted);
  * the PRODUCTION `strategy_service.evaluate_upcoming_setups` (NY cycle -> generator -> upsert -> guarded
    auto-arm) and `execution_coordinator.evaluate_orders_sync` are called - no strategy code is duplicated;
  * time comes only from an injected `FakeClock`; bars are fed only if closed <= decision time; the HTF bias
    is an as-of table (`known_at_ms <= decision`). Lesson rules / news are NOT replayed as-of (none loaded):
    the audit is labelled `COUNTERFACTUAL_NO_NEWS_NO_LESSONS`.

Truthfulness
  * `None` (null) = not measured (no/insufficient data). 0 only when the day really ran on VALID coverage.
  * Missing days are reported as coverage gaps - never filled with synthetic bars or trades.
  * Fills use a SYNTHETIC quote derived from the M5 close +/- an assumed spread: execution is an assumption
    (stated in every output), so no PnL/win-rate is produced.

Dataset (provided by the user; the product trades Bitget XAUUSDT, do not mix brokers):
  --m5-csv   header: timestamp_ms,open,high,low,close[,volume]   (use --m5-unit s if seconds)
  --htf-csv  header: known_at_ms,htf_bias,h1_alignment           (as-of table, causal)

Usage:
  python backend/lab/ny_quota_frequency_audit.py --m5-csv m5.csv --htf-csv htf.csv \
      --start 2026-07-01 --end 2026-09-30 --out logs/acceptance/v14_3/frequency_audit
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from database import get_or_create_test_root
test_root = get_or_create_test_root()
# The lab must never write to the runtime DB, even if `database` creates its engine at import time.
os.environ.setdefault("AURUM_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="aurum_lab_ny_", dir=str(test_root)), "lab.db"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

STRATEGY_VERSION = "V14.3"
FIVE_MIN_MS = 300_000
WARMUP_BARS = 60                      # closed M5 bars required before the window opens (needs >= 15 for ATR)
DEFAULT_SPREAD = 0.20                 # assumed XAUUSDT spread (USDT); only used for the synthetic quote
EXECUTION_ASSUMPTIONS = (
    "SYNTHETIC_QUOTE_FROM_M5_CLOSE_PLUS_MINUS_HALF_ASSUMED_SPREAD; NO_INTRABAR_ORDER_KNOWLEDGE; "
    "NO_EXIT_SIMULATION; COUNTERFACTUAL_NO_NEWS_NO_LESSONS; STANDARD_15M_FLOW_NOT_MEASURED"
)


# ----------------------------------------------------------------------------------------------
# Dataset loading + coverage (pure)
# ----------------------------------------------------------------------------------------------
def load_m5_csv(path: str, *, unit: str = "ms") -> List[Dict[str, float]]:
    """Explicit unit conversion (never guessed from magnitude). Rows are sorted + exact-deduped."""
    if unit not in ("ms", "s"):
        raise ValueError("INVALID_TIMESTAMP_UNIT")
    mult = 1 if unit == "ms" else 1000
    rows: Dict[int, Dict[str, float]] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            ts = int(float(r["timestamp_ms"])) * mult
            bar = {"timestamp": ts, "open": float(r["open"]), "high": float(r["high"]),
                   "low": float(r["low"]), "close": float(r["close"]), "volume": float(r.get("volume") or 0.0)}
            if ts in rows and rows[ts] != bar:
                raise ValueError("CONFLICTING_CANDLE_DUPLICATE")
            rows[ts] = bar
    return [rows[t] for t in sorted(rows)]


def load_htf_csv(path: str) -> List[Dict[str, Any]]:
    with open(path, newline="") as fh:
        out = [{"known_at": int(float(r["known_at_ms"])), "htf_bias": r["htf_bias"].strip().upper(),
                "h1_alignment": r["h1_alignment"].strip().upper()} for r in csv.DictReader(fh)]
    return sorted(out, key=lambda r: r["known_at"])


def dataset_hash(paths: Iterable[Optional[str]]) -> Optional[str]:
    h = hashlib.sha256()
    seen = False
    for p in paths:
        if p and os.path.exists(p):
            seen = True
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
    return h.hexdigest() if seen else None


def htf_as_of(htf: Optional[List[Dict[str, Any]]], now_ms: int) -> Optional[Dict[str, Any]]:
    """Latest HTF row KNOWN at `now_ms` (known_at <= now). None when nothing was known yet."""
    best = None
    for r in htf or []:
        if r["known_at"] <= now_ms:
            best = r
        else:
            break
    return best


def day_coverage(bars: List[Dict[str, float]], window: Dict[str, Any], htf: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Coverage of ONE requested NY day. Counters are only measured when status == VALID."""
    start, end = int(window["window_start_ms"]), int(window["window_end_ms"])
    first_needed = start - WARMUP_BARS * FIVE_MIN_MS
    have = {b["timestamp"] for b in bars if first_needed <= b["timestamp"] < end}
    expected = list(range(first_needed, end, FIVE_MIN_MS))
    missing = [t for t in expected if t not in have]
    reasons: List[str] = []
    if not have:
        status = "MISSING"
        reasons.append("NO_M5_BARS_FOR_DAY")
    elif missing:
        status = "PARTIAL"
        reasons.append(f"M5_MISSING_INTERVALS:{len(missing)}")
    else:
        status = "VALID"
    if status == "VALID" and htf_as_of(htf, start) is None:
        status = "PARTIAL"
        reasons.append("HTF_BIAS_UNKNOWN_AT_WINDOW_START")
    return {"status": status, "bars_expected": len(expected), "bars_present": len(have),
            "missing_intervals": len(missing),
            "missing_first": (min(missing) if missing else None), "reasons": reasons}


# ----------------------------------------------------------------------------------------------
# One day through the PRODUCTION flow
# ----------------------------------------------------------------------------------------------
def _new_db():
    import models
    from database import run_schema_migrations
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def _configure(db, *, leverage: int, margin_mode: str, risk_pct: float, policy_mode: str) -> None:
    import models
    from services.trading_policy_service import TradingPolicyService
    for k, v in (("default_leverage", str(leverage)), ("default_margin_mode", margin_mode),
                 ("default_risk_pct", str(risk_pct))):
        db.add(models.SystemConfig(key=k, value=v, updated_at=0))
    db.commit()
    p = TradingPolicyService.get_or_create_policy(db)
    p.ny_fallback_enabled = True
    p.entry_session_policy = policy_mode
    db.commit()


def run_day(day: date, bars: List[Dict[str, float]], htf: Optional[List[Dict[str, Any]]], *,
            symbol: str = "XAUUSDT", spread: float = DEFAULT_SPREAD, leverage: int = 30,
            margin_mode: str = "ISOLATED", risk_pct: float = 0.25, policy_mode: str = "NY_ONLY",
            dataset_hash_value: Optional[str] = None) -> Dict[str, Any]:
    """Audit one NY session day. Returns the Appendix-E daily summary (null = not measured)."""
    from services.clock import FakeClock
    from services.execution_coordinator import execution_coordinator
    from services.ny_opportunity_audit_service import NYOpportunityAuditService
    from services.strategy_service import strategy_service
    from services.trading_policy_service import TradingPolicyService
    import models

    engine, db = _new_db()
    try:
        _configure(db, leverage=leverage, margin_mode=margin_mode, risk_pct=risk_pct, policy_mode=policy_mode)
        policy = TradingPolicyService.get_active_policy(db, symbol)
        # Anchor at NY noon of the day (inside that NY date; DST handled by the window function itself).
        from zoneinfo import ZoneInfo
        noon = int(datetime(day.year, day.month, day.day, 12, 0, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
        window = TradingPolicyService.get_ny_session_window(policy, now_ms=noon, clock=FakeClock(noon))
        cov = day_coverage(bars, window, htf)
        result: Dict[str, Any] = {
            "session_date_ny": day.isoformat(), "requested_day": True,
            "data_coverage_status": cov["status"], "coverage": cov,
            "unique_standard_candidates": None, "unique_fallback_candidates": None,
            "financially_eligible_candidates": None, "armed_orders": None, "actual_fills": None,
            "quota_target": int(getattr(policy, "ny_min_fills", 1) or 1), "quota_status": None,
            "primary_no_fill_category": None, "top_blockers": [], "system_errors": [],
            "dataset_hash": dataset_hash_value, "strategy_version": STRATEGY_VERSION,
            "execution_assumptions": EXECUTION_ASSUMPTIONS,
        }
        if cov["status"] != "VALID":
            result["primary_no_fill_category"] = "DATA_UNAVAILABLE"
            return result

        start, end = int(window["window_start_ms"]), int(window["window_end_ms"])
        sid = window["session_instance_id"]
        by_ts = sorted(bars, key=lambda b: b["timestamp"])
        t = start
        while t < end:
            closed = [SimpleNamespace(timestamp=b["timestamp"], open=b["open"], high=b["high"], low=b["low"],
                                      close=b["close"], volume=b["volume"], is_closed=True)
                      for b in by_ts if b["timestamp"] + FIVE_MIN_MS <= t][-400:]
            last = closed[-1].close
            tick = {"symbol": symbol, "bid": round(last - spread / 2, 4), "ask": round(last + spread / 2, 4),
                    "last": last, "timestamp": t - 500, "observed_at": t - 500}
            ctx = htf_as_of(htf, t)
            clock = FakeClock(t)
            strategy_service.evaluate_upcoming_setups(
                symbol, "15M", db=db, clock=clock, ticker=tick, htf_bias=ctx["htf_bias"],
                h1_alignment=ctx["h1_alignment"], candles_5m=closed,
                feed_health={"has_execution_gap": False, "is_lagging": False}, auto_enabled=True)
            execution_coordinator.evaluate_orders_sync(db, ticker_override=tick, clock=clock, now_ms=t)
            t += FIVE_MIN_MS
        # one scan at the (exclusive) end finalizes the session summary exactly once
        clock = FakeClock(end)
        strategy_service.evaluate_upcoming_setups(
            symbol, "15M", db=db, clock=clock, ticker=None, htf_bias=htf_as_of(htf, end)["htf_bias"] if htf_as_of(htf, end) else "UNKNOWN",
            h1_alignment="UNKNOWN", candles_5m=[], feed_health=None, auto_enabled=True)

        diag = NYOpportunityAuditService.build_diagnostics(db, session_instance_id=sid, session_date_ny=day.isoformat())
        quota = diag.get("quota") or {}
        measured = diag.get("availability") == "MEASURED"
        result.update(
            unique_fallback_candidates=diag["unique_candidates"] if measured else None,
            financially_eligible_candidates=diag["financially_eligible"] if measured else None,
            armed_orders=diag["armed"] if measured else None,
            actual_fills=int(quota.get("total_fills") or 0) if quota else None,
            quota_status=quota.get("status"),
            top_blockers=diag.get("top_blockers") or [],
            system_errors=[diag["last_system_error"]] if diag.get("last_system_error") else [],
        )
        if (result["actual_fills"] or 0) == 0:
            result["primary_no_fill_category"] = diag.get("missed_category") or "UNKNOWN"
        return result
    finally:
        db.close()
        engine.dispose()


# ----------------------------------------------------------------------------------------------
# Range + outputs
# ----------------------------------------------------------------------------------------------
def daterange(start: date, end: date) -> List[date]:
    if end < start:
        raise ValueError("INVALID_DATE_RANGE")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def run_audit(days: List[date], bars: List[Dict[str, float]], htf: Optional[List[Dict[str, Any]]], **kw) -> Dict[str, Any]:
    daily = [run_day(d, bars, htf, **kw) for d in days]
    return {"daily": daily, "summary": summarize(daily, **{k: kw[k] for k in ("leverage", "risk_pct", "spread", "policy_mode") if k in kw})}


def summarize(daily: List[Dict[str, Any]], *, leverage: int = 30, risk_pct: float = 0.25,
              spread: float = DEFAULT_SPREAD, policy_mode: str = "NY_ONLY") -> Dict[str, Any]:
    tested = [d for d in daily if d["data_coverage_status"] == "VALID"]
    cause = Counter(d["primary_no_fill_category"] for d in tested if d["primary_no_fill_category"])
    blockers: Counter = Counter()
    for d in tested:
        for b in d["top_blockers"]:
            blockers[b["code"]] += b["count"]
    ds_hash = next((d["dataset_hash"] for d in daily if d["dataset_hash"]), None)

    def measured(value):
        """No VALID day => nothing was measured => null, never a fake 0."""
        return value if tested else None

    return {
        "strategy_version": STRATEGY_VERSION,
        "status": "MEASURED" if tested else "NOT_VERIFIED",
        "requested_days": len(daily),
        "covered_days": len(tested),
        "partial_days": sum(1 for d in daily if d["data_coverage_status"] == "PARTIAL"),
        "missing_days": sum(1 for d in daily if d["data_coverage_status"] == "MISSING"),
        "days_with_candidate": measured(sum(1 for d in tested if (d["unique_fallback_candidates"] or 0) > 0)),
        "days_with_eligible": measured(sum(1 for d in tested if (d["financially_eligible_candidates"] or 0) > 0)),
        "days_with_arm": measured(sum(1 for d in tested if (d["armed_orders"] or 0) > 0)),
        "days_with_fill": measured(sum(1 for d in tested if (d["actual_fills"] or 0) > 0)),
        "total_fills": measured(sum((d["actual_fills"] or 0) for d in tested)),
        "no_fill_categories": measured(dict(cause)),
        "top_blockers": measured([{"code": c, "count": n} for c, n in blockers.most_common(10)]),
        "dataset_hash": ds_hash,
        "settings": {"capital_usdt": 1000.0, "leverage": leverage, "risk_pct": risk_pct,
                     "policy_mode": policy_mode, "assumed_spread": spread},
        "execution_assumptions": EXECUTION_ASSUMPTIONS,
        "note": "Frequency of entry opportunities only; no PnL/win-rate. Null/NOT_VERIFIED = not measured.",
    }


def write_outputs(report: Dict[str, Any], out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    daily = report["daily"]
    with open(out / "daily_funnel.csv", "w", newline="") as fh:
        cols = ["session_date_ny", "data_coverage_status", "unique_standard_candidates", "unique_fallback_candidates",
                "financially_eligible_candidates", "armed_orders", "actual_fills", "quota_target", "quota_status",
                "primary_no_fill_category", "dataset_hash", "strategy_version"]
        w = csv.writer(fh)
        w.writerow(cols)
        for d in daily:
            w.writerow(["" if d[c] is None else d[c] for c in cols])      # null stays EMPTY, never 0
    with open(out / "sessions.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["session_date_ny", "bars_expected", "bars_present", "missing_intervals", "coverage_reasons", "system_errors"])
        for d in daily:
            c = d["coverage"]
            w.writerow([d["session_date_ny"], c["bars_expected"], c["bars_present"], c["missing_intervals"],
                        ";".join(c["reasons"]), ";".join(d["system_errors"])])
    with open(out / "blockers.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["session_date_ny", "code", "category", "count"])
        for d in daily:
            for b in d["top_blockers"]:
                w.writerow([d["session_date_ny"], b["code"], b["category"], b["count"]])
    with open(out / "summary.json", "w") as fh:
        json.dump(report["summary"], fh, indent=2, ensure_ascii=False)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--m5-csv")
    ap.add_argument("--m5-unit", default="ms", choices=["ms", "s"])
    ap.add_argument("--htf-csv")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="logs/acceptance/v14_3/frequency_audit")
    ap.add_argument("--spread", type=float, default=DEFAULT_SPREAD)
    ap.add_argument("--leverage", type=int, default=30)
    ap.add_argument("--risk-pct", type=float, default=0.25)
    ap.add_argument("--policy-mode", default="NY_ONLY", choices=["NY_ONLY", "ALL_SESSIONS_WITH_NY_RESERVE"])
    a = ap.parse_args(argv)
    days = daterange(date.fromisoformat(a.start), date.fromisoformat(a.end))
    bars = load_m5_csv(a.m5_csv, unit=a.m5_unit) if a.m5_csv and os.path.exists(a.m5_csv) else []
    htf = load_htf_csv(a.htf_csv) if a.htf_csv and os.path.exists(a.htf_csv) else None
    report = run_audit(days, bars, htf, spread=a.spread, leverage=a.leverage, risk_pct=a.risk_pct,
                       policy_mode=a.policy_mode, dataset_hash_value=dataset_hash([a.m5_csv, a.htf_csv]))
    write_outputs(report, a.out)
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
