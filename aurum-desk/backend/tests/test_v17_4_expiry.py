"""
backend/tests/test_v17_4_expiry.py
V17.4 Parts 155 & 156:
- Verify resolve_candidate_order_expiry reads authoritative session_context.expiry_cap_ms.
- Test cap is None (all-session setup-first) preserves candidate TTL.
- Test cap finite truncates candidate expiry to session end.
- Test conflict between expiry_cap_ms and window_end_ms raises ValueError.
- Test deadline does not drift or renew on repeated evaluation.
"""
from types import SimpleNamespace
import pytest

from services.cg01_types import (
    ModelIdentifier,
    resolve_candidate_order_expiry,
    resolve_cg01_execution_deadline,
)
from services.trading_policy_service import EntrySessionContext


def test_part_155_expiry_cap_none_preserves_ttl():
    cand_ttl_ms = 300_000  # 5 minutes
    trigger_known_at = 1700000000000
    expected_expiry = trigger_known_at + cand_ttl_ms

    cand = SimpleNamespace(
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        valid_until_ms=expected_expiry,
    )

    # Setup-first context: expiry_cap_ms is None
    ctx_setup_first = EntrySessionContext(
        policy_mode="ALL_SESSIONS_SETUP_FIRST",
        policy_version=1,
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        analysis_session="LONDON",
        as_of_ms=trigger_known_at,
        in_ny_entry_window=False,
        ny_start_ms=0,
        ny_end_ms=0,
        standard_window_restriction=None,
        expiry_cap_ms=None,
    )

    resolved = resolve_candidate_order_expiry(
        cand=cand,
        now_ms=trigger_known_at + 60_000,
        session_context=ctx_setup_first,
    )
    assert resolved == expected_expiry


def test_part_155_expiry_cap_finite_takes_min():
    trigger_known_at = 1700000000000
    expected_expiry = trigger_known_at + 3_600_000  # 1 hour
    session_cap = trigger_known_at + 600_000        # 10 minutes

    cand = SimpleNamespace(
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        valid_until_ms=expected_expiry,
    )

    ctx_legacy = EntrySessionContext(
        policy_mode="NY_ONLY",
        policy_version=1,
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        analysis_session="NY",
        as_of_ms=trigger_known_at,
        in_ny_entry_window=True,
        ny_start_ms=trigger_known_at - 1_800_000,
        ny_end_ms=session_cap,
        standard_window_restriction=(trigger_known_at - 1_800_000, session_cap),
        expiry_cap_ms=session_cap,
    )

    resolved = resolve_candidate_order_expiry(
        cand=cand,
        now_ms=trigger_known_at,
        session_context=ctx_legacy,
    )
    assert resolved == session_cap


def test_part_155_expiry_cap_conflict_detected():
    cand = SimpleNamespace(
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        valid_until_ms=1700003600000,
    )

    # Incompatible legacy context with conflicting fields
    bad_ctx = SimpleNamespace(
        expiry_cap_ms=1700001000000,
        window_end_ms=1700002000000,
    )

    with pytest.raises(ValueError, match="SESSION_CONTEXT_EXPIRY_CONFLICT"):
        resolve_candidate_order_expiry(
            cand=cand,
            now_ms=1700000000000,
            session_context=bad_ctx,
        )


def test_part_156_deadline_does_not_renew_on_repeated_scan():
    trigger_time = 1700000000000
    cand_ttl_ms = 300_000  # 5 min
    max_age_ms = 360_000   # 6 min

    d0 = resolve_cg01_execution_deadline(
        trigger_known_at_ms=trigger_time,
        candidate_ttl_ms=cand_ttl_ms,
        max_trigger_age_ms=max_age_ms,
    )
    assert d0.expires_at_ms == trigger_time + cand_ttl_ms

    # Scanning at T + 1 min, passing existing deadline
    d1 = resolve_cg01_execution_deadline(
        trigger_known_at_ms=trigger_time,
        candidate_ttl_ms=cand_ttl_ms,
        max_trigger_age_ms=max_age_ms,
        existing_deadline_ms=d0.expires_at_ms,
    )
    assert d1.expires_at_ms == d0.expires_at_ms

    # Scanning at T + 2 min, passing existing deadline
    d2 = resolve_cg01_execution_deadline(
        trigger_known_at_ms=trigger_time,
        candidate_ttl_ms=cand_ttl_ms,
        max_trigger_age_ms=max_age_ms,
        existing_deadline_ms=d1.expires_at_ms,
    )
    assert d2.expires_at_ms == d0.expires_at_ms


def test_part_156_missing_cg01_expiry_fails_closed():
    cand_no_exp = SimpleNamespace(
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        valid_until_ms=None,
        entry_intent=None,
    )

    with pytest.raises(ValueError, match="CG01_CANDIDATE_MISSING_EXPIRY"):
        resolve_candidate_order_expiry(
            cand=cand_no_exp,
            now_ms=1700000000000,
            session_context=None,
        )
