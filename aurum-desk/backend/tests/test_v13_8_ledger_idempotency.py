import pytest
from decimal import Decimal
from lab.replay_engine import ReplayContext
from lab.replay_evidence_utils import LedgerState, apply_posting_once
from domain_calculator import CostAssumptions

def test_replay_context_record_posting_idempotent_no_duplicate_cash_deduction():
    # PHẦN 32, 124, 127: ReplayContext.record_posting must check posting_index BEFORE mutating current_cash
    ctx = ReplayContext(run_id="test_v13_8_ledger", costs=CostAssumptions())
    ctx.current_cash = 1000.0
    
    posting_payload = {
        "posting_id": "POST-TEST-001",
        "trade_id": "TR-001",
        "event_id": "EV-001",
        "posting_type": "ENTRY_FEE",
        "amount_usdt": -0.25,
        "timestamp_ms": 1700000000000,
        "currency": "USDT"
    }
    
    # 1. First emission: cash is decremented by 0.25 -> 999.75
    rec1 = ctx.record_posting(posting_payload)
    assert ctx.current_cash == 999.75
    assert len(ctx.ledger_postings) == 1
    assert len(ctx.posting_index) == 1
    assert rec1["amount_usdt"] == -0.25
    assert rec1["balance_after_usdt"] == 999.75
    
    # 2. Call again 5 times with identical payload: cash MUST remain 999.75, postings len = 1
    for _ in range(5):
        rec_dup = ctx.record_posting(posting_payload)
        assert ctx.current_cash == 999.75
        assert len(ctx.ledger_postings) == 1
        assert rec_dup["posting_id"] == "POST-TEST-001"
        assert rec_dup["balance_after_usdt"] == 999.75

def test_replay_context_conflicting_posting_raises_and_preserves_cash():
    # PHẦN 32, 124, 127: Calling same posting_id with different amount MUST raise before cash mutation
    ctx = ReplayContext(run_id="test_v13_8_conflict", costs=CostAssumptions())
    ctx.current_cash = 1000.0
    
    p1 = {
        "posting_id": "POST-SAME-ID",
        "trade_id": "TR-001",
        "event_id": "EV-001",
        "posting_type": "ENTRY_FEE",
        "amount_usdt": -0.25,
        "timestamp_ms": 1700000000000
    }
    ctx.record_posting(p1)
    assert ctx.current_cash == 999.75
    
    # Conflicting amount with same posting_id
    p_conflict = {
        "posting_id": "POST-SAME-ID",
        "trade_id": "TR-001",
        "event_id": "EV-001",
        "posting_type": "ENTRY_FEE",
        "amount_usdt": -0.50, # Conflict!
        "timestamp_ms": 1700000000000
    }
    with pytest.raises(ValueError, match="POSTING_ID_PAYLOAD_CONFLICT"):
        ctx.record_posting(p_conflict)
        
    # Cash MUST NOT be mutated!
    assert ctx.current_cash == 999.75
    assert len(ctx.ledger_postings) == 1

def test_replay_context_conflicting_metadata_raises_before_mutation():
    # PHẦN 127: Same posting_id with different trade_id or posting_type must raise conflict before mutating cash
    ctx = ReplayContext(run_id="test_v13_8_meta_conflict", costs=CostAssumptions())
    ctx.current_cash = 1000.0
    p1 = {
        "posting_id": "POST-META-1",
        "trade_id": "TR-001",
        "event_id": "EV-001",
        "posting_type": "ENTRY_FEE",
        "amount_usdt": -0.25,
        "timestamp_ms": 1700000000000
    }
    ctx.record_posting(p1)
    assert ctx.current_cash == 999.75

    # Same pid, different trade_id
    p_bad_trade = {
        "posting_id": "POST-META-1",
        "trade_id": "TR-DIFFERENT",
        "event_id": "EV-001",
        "posting_type": "ENTRY_FEE",
        "amount_usdt": -0.25,
        "timestamp_ms": 1700000000000
    }
    with pytest.raises(ValueError, match="POSTING_ID_PAYLOAD_CONFLICT"):
        ctx.record_posting(p_bad_trade)
    assert ctx.current_cash == 999.75

def test_replay_context_rejects_nan_and_bool_amount_without_mutation():
    # PHẦN 12, 123: Nonfinite/bool amount must be rejected before cash change
    ctx = ReplayContext(run_id="test_v13_8_invalid", costs=CostAssumptions())
    ctx.current_cash = 1000.0
    
    bad_nan = {
        "posting_id": "POST-NAN",
        "trade_id": "TR-001",
        "posting_type": "ENTRY_FEE",
        "amount_usdt": float("nan"),
        "timestamp_ms": 1700000000000
    }
    with pytest.raises(ValueError):
        ctx.record_posting(bad_nan)
    assert ctx.current_cash == 1000.0
    
    bad_bool = {
        "posting_id": "POST-BOOL",
        "trade_id": "TR-001",
        "posting_type": "ENTRY_FEE",
        "amount_usdt": True, # bool is not numeric cash!
        "timestamp_ms": 1700000000000
    }
    with pytest.raises(ValueError):
        ctx.record_posting(bad_bool)
    assert ctx.current_cash == 1000.0
