import pytest
from decimal import Decimal
from lab.replay_engine import ReplayContext
from lab.replay_evidence_utils import LedgerState, apply_posting_once

def test_replay_context_record_posting_idempotent():
    ctx = ReplayContext()
    p1 = {
        "posting_id": "post-canonical-1",
        "trade_id": "tr-1",
        "event_id": "ev-1",
        "posting_type": "ENTRY_FEE",
        "amount": -0.25,
        "amount_usdt": -0.25,
        "timestamp_ms": 1000
    }
    rec1 = ctx.record_posting(p1)
    assert len(ctx.ledger_postings) == 1
    assert rec1["posting_id"] == "post-canonical-1"

    # Re-record exact same posting
    rec2 = ctx.record_posting(p1)
    assert len(ctx.ledger_postings) == 1  # Not duplicated!
    assert rec2["posting_id"] == "post-canonical-1"

def test_replay_context_record_posting_payload_conflict_raises():
    ctx = ReplayContext()
    p1 = {
        "posting_id": "post-canonical-2",
        "trade_id": "tr-2",
        "event_id": "ev-2",
        "posting_type": "ENTRY_FEE",
        "amount": -0.25,
        "amount_usdt": -0.25,
        "timestamp_ms": 1000
    }
    ctx.record_posting(p1)

    # Conflicting amount on same posting_id
    p2 = dict(p1)
    p2["amount"] = -0.50
    p2["amount_usdt"] = -0.50
    with pytest.raises(ValueError, match="POSTING_ID_PAYLOAD_CONFLICT"):
        ctx.record_posting(p2)

def test_ledger_state_cash_deduction_and_reapplication():
    ledger = LedgerState(cash=Decimal("1000.0"))
    raw = {
        "posting_id": "post-entry-1",
        "trade_id": "tr-3",
        "event_id": "ev-3",
        "posting_type": "ENTRY_FEE",
        "amount": "-0.20",
        "timestamp": 1000,
        "currency": "USDT"
    }
    res1 = apply_posting_once(ledger, raw)
    assert res1 is True
    assert ledger.cash == Decimal("999.80")
    assert len(ledger.postings) == 1

    # Apply again
    res2 = apply_posting_once(ledger, raw)
    assert res2 is False
    assert ledger.cash == Decimal("999.80")  # Unchanged!
    assert len(ledger.postings) == 1
