"""V14.3 frequency audit (lab) - functional proof, NOT a performance claim.

The multi-day fixture is synthetic by construction: it proves that days with a real FVG retest setup reach a
fill through the PRODUCTION chain, flat days report no candidate (no fake trade), and days without data are
coverage gaps (null counters), never zero-filled.
"""
import csv
import json
from datetime import date

import pytest

from lab import ny_quota_frequency_audit as fa
from ny_quota_fixtures import LONG_BARS, FIVE, ny_ms

FLAT = (2672.0, 2672.5, 2671.5, 2672.0)
ZONE_FLAT = (2665.6, 2665.9, 2665.4, 2665.65)


def _day_bars(y, m, d, *, story=False, gap=False):
    start = ny_ms(y, m, d, 8, 0) - fa.WARMUP_BARS * FIVE
    end = ny_ms(y, m, d, 11, 0)
    story_first = ny_ms(y, m, d, 10, 35) - len(LONG_BARS) * FIVE      # last story bar closes at 10:35
    bars = []
    for ts in range(start, end, FIVE):
        if gap and ts == ny_ms(y, m, d, 9, 0):
            continue
        if story and story_first <= ts < story_first + len(LONG_BARS) * FIVE:
            o, h, l, c = LONG_BARS[(ts - story_first) // FIVE]
        elif story and ts >= story_first + len(LONG_BARS) * FIVE:
            o, h, l, c = ZONE_FLAT
        else:
            o, h, l, c = FLAT
        bars.append({"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    return bars


def _htf(y, m, d):
    return [{"known_at": ny_ms(y, m, d, 0, 0) - 86_400_000, "htf_bias": "BULLISH", "h1_alignment": "ALIGNED"}]


def test_multi_day_audit_is_truthful(tmp_path):
    bars = (_day_bars(2026, 10, 6, gap=True) + _day_bars(2026, 10, 8) + _day_bars(2026, 10, 9, story=True))
    htf = _htf(2026, 10, 5)
    days = [date(2026, 10, d) for d in (6, 7, 8, 9)]
    report = fa.run_audit(days, bars, htf, dataset_hash_value="fixture-hash")
    by = {d["session_date_ny"]: d for d in report["daily"]}

    # Oct 6: one hole in the data -> PARTIAL, counters NOT measured
    assert by["2026-10-06"]["data_coverage_status"] == "PARTIAL"
    assert by["2026-10-06"]["coverage"]["missing_intervals"] == 1
    assert by["2026-10-06"]["actual_fills"] is None and by["2026-10-06"]["armed_orders"] is None
    # Oct 7: no bars at all -> MISSING, null (never a fabricated 0 / synthetic trade)
    assert by["2026-10-07"]["data_coverage_status"] == "MISSING"
    assert by["2026-10-07"]["actual_fills"] is None
    assert by["2026-10-07"]["primary_no_fill_category"] == "DATA_UNAVAILABLE"
    # Oct 8: valid but flat -> measured zero, no candidate, no trade
    assert by["2026-10-08"]["data_coverage_status"] == "VALID"
    assert by["2026-10-08"]["unique_fallback_candidates"] == 0
    assert by["2026-10-08"]["actual_fills"] == 0
    assert by["2026-10-08"]["primary_no_fill_category"] is not None
    # Oct 9: real setup -> production chain arms and fills; quota fulfilled by an ACTUAL fill
    d9 = by["2026-10-09"]
    assert d9["data_coverage_status"] == "VALID"
    assert d9["unique_fallback_candidates"] >= 1
    assert d9["armed_orders"] == 1 and d9["actual_fills"] == 1
    assert d9["quota_status"] == "FULFILLED"
    assert d9["strategy_version"] == "V14.3" and d9["dataset_hash"] == "fixture-hash"
    assert "COUNTERFACTUAL_NO_NEWS_NO_LESSONS" in d9["execution_assumptions"]

    s = report["summary"]
    assert (s["requested_days"], s["covered_days"], s["partial_days"], s["missing_days"]) == (4, 2, 1, 1)
    assert s["days_with_fill"] == 1 and s["total_fills"] == 1 and s["status"] == "MEASURED"
    assert s["settings"]["capital_usdt"] == 1000.0 and s["settings"]["leverage"] == 30

    fa.write_outputs(report, str(tmp_path))
    for name in ("daily_funnel.csv", "sessions.csv", "blockers.csv", "summary.json"):
        assert (tmp_path / name).exists()
    rows = list(csv.DictReader(open(tmp_path / "daily_funnel.csv")))
    miss = next(r for r in rows if r["session_date_ny"] == "2026-10-07")
    assert miss["actual_fills"] == "" and miss["armed_orders"] == ""            # null stays empty, not "0"
    assert json.load(open(tmp_path / "summary.json"))["missing_days"] == 1


def test_no_dataset_is_not_verified_not_zero(tmp_path):
    report = fa.run_audit([date(2026, 10, 9)], [], None)
    assert report["summary"]["status"] == "NOT_VERIFIED"
    assert report["summary"]["covered_days"] == 0
    assert report["daily"][0]["actual_fills"] is None
    assert report["summary"]["total_fills"] is None and report["summary"]["days_with_fill"] is None


def test_lab_never_uses_runtime_db_or_live_quota(tmp_path):
    import models
    from database import SessionLocal
    live = SessionLocal()
    try:
        before = live.query(models.PaperOrder).count()
        fa.run_audit([date(2026, 10, 9)], _day_bars(2026, 10, 9, story=True), _htf(2026, 10, 5))
        assert live.query(models.PaperOrder).count() == before
    finally:
        live.close()


def test_loader_explicit_units_and_conflicts(tmp_path):
    p = tmp_path / "m5.csv"
    p.write_text("timestamp_ms,open,high,low,close,volume\n1700000000,1,2,1,2,3\n")
    assert fa.load_m5_csv(str(p), unit="s")[0]["timestamp"] == 1_700_000_000_000
    p.write_text("timestamp_ms,open,high,low,close\n1000,1,2,1,2\n1000,1,3,1,2\n")
    with pytest.raises(ValueError, match="CONFLICTING_CANDLE_DUPLICATE"):
        fa.load_m5_csv(str(p))
    with pytest.raises(ValueError, match="INVALID_TIMESTAMP_UNIT"):
        fa.load_m5_csv(str(p), unit="guess")


def test_htf_as_of_is_causal():
    htf = [{"known_at": 100, "htf_bias": "BULLISH", "h1_alignment": "ALIGNED"},
           {"known_at": 200, "htf_bias": "BEARISH", "h1_alignment": "ALIGNED"}]
    assert fa.htf_as_of(htf, 99) is None
    assert fa.htf_as_of(htf, 150)["htf_bias"] == "BULLISH"
    assert fa.htf_as_of(htf, 200)["htf_bias"] == "BEARISH"
