import pytest
from lab.daily_research_scheduler import derive_cadence_status, summarize_cadence

def test_cadence_status_unmet_when_unmet_sessions_exist():
    # PHẦN 04, 55, 104: 92 executable, 80 met, 12 unmet must be UNMET, not PASS!
    summary = {
        "executable_sessions": 92,
        "sessions_with_fills": 80,
        "unmet_sessions": 12,
        "in_progress_sessions": 0
    }
    status = derive_cadence_status(summary)
    assert status == "UNMET"

def test_cadence_status_pass_when_zero_unmet():
    # When 0 unmet sessions, cadence passes
    summary = {
        "executable_sessions": 80,
        "sessions_with_fills": 80,
        "unmet_sessions": 0,
        "in_progress_sessions": 0
    }
    status = derive_cadence_status(summary)
    assert status == "PASS"

def test_cadence_status_not_applicable_when_zero_executable():
    summary = {
        "executable_sessions": 0,
        "sessions_with_fills": 0,
        "unmet_sessions": 0,
        "in_progress_sessions": 0
    }
    status = derive_cadence_status(summary)
    assert status == "NOT_APPLICABLE"

def test_cadence_status_in_progress_when_pending():
    summary = {
        "executable_sessions": 1,
        "sessions_with_fills": 0,
        "unmet_sessions": 0,
        "in_progress_sessions": 1
    }
    status = derive_cadence_status(summary)
    assert status == "IN_PROGRESS"

def test_past_unmet_not_masked_by_in_progress():
    # If past sessions have unmet, status must be UNMET even if in_progress > 0
    summary = {
        "executable_sessions": 10,
        "sessions_with_fills": 8,
        "unmet_sessions": 2,
        "in_progress_sessions": 1
    }
    status = derive_cadence_status(summary)
    assert status == "UNMET"
