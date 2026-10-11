import pytest
from decimal import Decimal
from lab.replay_evidence_utils import (
    read_required,
    finite_number,
    validate_finite_positive,
    index_unique,
    stable_id,
    compare_trade_numbers,
    LedgerState,
    apply_posting_once,
    normalize_order_status
)

def test_read_required_zero():
    # 1. read_required({"net_pnl":0},"net_pnl") trả 0.
    assert read_required({"net_pnl": 0}, "net_pnl") == 0
    assert read_required({"flag": False}, "flag") is False

def test_read_required_missing_or_none():
    # 2. Missing key/None đều raise MISSING_FIELD.
    with pytest.raises(ValueError, match="MISSING_FIELD:missing_key"):
        read_required({}, "missing_key")
    with pytest.raises(ValueError, match="MISSING_FIELD:none_key"):
        read_required({"none_key": None}, "none_key")

def test_finite_number_nan_inf():
    # 3. finite_number(float("nan"),"p") raise NONFINITE_NUMBER.
    with pytest.raises(ValueError, match="NONFINITE_NUMBER:p"):
        finite_number(float("nan"), "p")
    with pytest.raises(ValueError, match="NONFINITE_NUMBER:p"):
        finite_number(float("inf"), "p")
    with pytest.raises(ValueError, match="INVALID_NUMBER:p"):
        finite_number(True, "p")
    with pytest.raises(ValueError, match="INVALID_NUMBER:p"):
        finite_number(None, "p")

def test_index_unique_duplicates():
    # 4. index_unique hai rows cùng id raise DUPLICATE_ID.
    rows = [{"id": "t1", "val": 1}, {"id": "t1", "val": 2}]
    with pytest.raises(ValueError, match="DUPLICATE_ID:t1"):
        index_unique(rows, "id")

def test_stable_id_key_ordering():
    # 5. stable_id cùng payload khác thứ tự keys trả cùng ID.
    payload1 = {"b": 2, "a": 1, "z": [3, 4]}
    payload2 = {"a": 1, "z": [3, 4], "b": 2}
    assert stable_id("test", payload1) == stable_id("test", payload2)

def test_ledger_state_and_posting_idempotency():
    # 6. LedgerState(Decimal("1000")); posting ENTRY_FEE amount=-0.2 → cash 999.8.
    ledger = LedgerState(cash=Decimal("1000"))
    raw_p = {
        "posting_id": "p-1",
        "trade_id": "t-1",
        "event_id": "e-1",
        "posting_type": "ENTRY_FEE",
        "amount": "-0.2",
        "timestamp": 1000,
        "currency": "USDT"
    }
    applied = apply_posting_once(ledger, raw_p)
    assert applied is True
    assert ledger.cash == Decimal("999.8")
    assert len(ledger.postings) == 1

    # 7. Apply lại posting đó → False, cash vẫn 999.8, postings length 1.
    reapplied = apply_posting_once(ledger, raw_p)
    assert reapplied is False
    assert ledger.cash == Decimal("999.8")
    assert len(ledger.postings) == 1

    # 8. Cùng posting_id amount=-0.3 → POSTING_ID_PAYLOAD_CONFLICT.
    conflict_p = dict(raw_p)
    conflict_p["amount"] = "-0.3"
    with pytest.raises(ValueError, match="POSTING_ID_PAYLOAD_CONFLICT:p-1"):
        apply_posting_once(ledger, conflict_p)

def test_posting_amount_alias_conflict():
    # 9. amount=0, amount_usdt=1 → POSTING_AMOUNT_ALIAS_CONFLICT.
    ledger = LedgerState(cash=Decimal("1000"))
    bad_p = {
        "posting_id": "p-2",
        "trade_id": "t-1",
        "event_id": "e-1",
        "posting_type": "ENTRY_FEE",
        "amount": "0",
        "amount_usdt": "1",
        "timestamp": 1000,
        "currency": "USDT"
    }
    with pytest.raises(ValueError, match="POSTING_AMOUNT_ALIAS_CONFLICT"):
        apply_posting_once(ledger, bad_p)

def test_compare_trade_numbers():
    # 10. compare_trade_numbers expected TP4103/actual4151 → mismatch take_profit.
    exp = {"take_profit": 4103.0, "net_pnl": -10.0}
    act = {"take_profit": 4151.0, "net_pnl": -10.0}
    fields = {"take_profit": 0.01, "net_pnl": 0.01}
    errs = compare_trade_numbers(exp, act, fields, "t-100")
    assert len(errs) == 1
    assert "MISMATCH:trade[t-100].take_profit:expected=4103:actual=4151" in errs[0]

def test_compare_trade_numbers_nan():
    # 11. actual numeric NaN/missing → errors, không PASS do so sánh abs(NaN).
    exp = {"net_pnl": 10.0}
    act = {"net_pnl": float("nan")}
    errs = compare_trade_numbers(exp, act, {"net_pnl": 0.01}, "t-101")
    assert len(errs) >= 1
    assert "NONFINITE_DECIMAL" in errs[0] or "INVALID_DECIMAL" in errs[0]

def test_normalize_order_status():
    assert normalize_order_status("SUBMITTED") == "SUBMITTED"
    assert normalize_order_status("FILLED") == "FILLED"
    assert normalize_order_status("REJECTED") == "REJECTED"
    with pytest.raises(ValueError, match="INVALID_ORDER_STATUS:INVALID"):
        normalize_order_status("INVALID")
