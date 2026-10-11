from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, List, Dict, Optional, Tuple
import hashlib
import json
import math

TERMINAL_ORDER_STATUSES = frozenset(
    {"FILLED", "REJECTED", "EXPIRED", "CANCELLED"}
)
ACTIVE_ORDER_STATUSES = frozenset({"SUBMITTED", "PENDING"})

def normalize_order_status(value: Any) -> str:
    status = value.value if isinstance(value, Enum) else value
    allowed = TERMINAL_ORDER_STATUSES | ACTIVE_ORDER_STATUSES | {"CREATED"}
    if not isinstance(status, str) or status not in allowed:
        raise ValueError(f"INVALID_ORDER_STATUS:{status}")
    return status

def read_required(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key not in obj or obj[key] is None:
            raise ValueError(f"MISSING_FIELD:{key}")
        return obj[key]
    if not hasattr(obj, key):
        raise ValueError(f"MISSING_FIELD:{key}")
    value = getattr(obj, key)
    if value is None:
        raise ValueError(f"MISSING_FIELD:{key}")
    return value

def finite_number(value: Any, name: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"INVALID_NUMBER:{name}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"INVALID_NUMBER:{name}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"NONFINITE_NUMBER:{name}")
    return parsed

def validate_finite_positive(value: Any, name: str) -> float:
    parsed = finite_number(value, name)
    if parsed <= 0:
        raise ValueError(f"NONPOSITIVE_NUMBER:{name}")
    return parsed

def decimal_amount(value: Any, name: str = "amount") -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"INVALID_DECIMAL:{name}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"INVALID_DECIMAL:{name}") from exc
    if not result.is_finite():
        raise ValueError(f"NONFINITE_DECIMAL:{name}")
    return result

def require_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"INVALID_ID:{name}")
    return value

def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")

def sha256_canonical(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

def stable_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"{prefix}-{digest[:24]}"

def index_unique(rows: List[Any], key: str = "id") -> Dict[str, Any]:
    result = {}
    for row in rows:
        row_id = require_id(read_required(row, key), key)
        if row_id in result:
            raise ValueError(f"DUPLICATE_ID:{row_id}")
        result[row_id] = row
    return result

def compare_trade_numbers(
    expected: Any,
    actual: Any,
    fields: Dict[str, float],
    trade_id: str,
) -> List[str]:
    errors = []
    for name, tolerance in fields.items():
        path = f"trade[{trade_id}].{name}"
        try:
            limit = finite_number(tolerance, f"{path}.tolerance")
            if limit < 0:
                raise ValueError(f"NEGATIVE_TOLERANCE:{path}")
            e = decimal_amount(read_required(expected, name), path)
            a = decimal_amount(read_required(actual, name), path)
            if abs(e - a) > Decimal(str(limit)):
                e_str = str(e.normalize()) if e == e.to_integral() else str(e)
                a_str = str(a.normalize()) if a == a.to_integral() else str(a)
                errors.append(f"MISMATCH:{path}:expected={e_str}:actual={a_str}")
        except ValueError as exc:
            errors.append(str(exc))
    return errors

@dataclass
class LedgerState:
    cash: Decimal
    postings: List[Dict[str, Any]] = field(default_factory=list)
    postings_by_id: Dict[str, Dict[str, Any]] = field(default_factory=dict)

def apply_posting_once(
    ledger: LedgerState,
    raw_posting: Dict[str, Any],
) -> bool:
    posting_id = require_id(read_required(raw_posting, "posting_id"), "posting_id")
    trade_id = require_id(read_required(raw_posting, "trade_id"), "trade_id")
    event_id = require_id(read_required(raw_posting, "event_id"), "event_id")
    posting_type = require_id(read_required(raw_posting, "posting_type"), "posting_type")
    amount = decimal_amount(read_required(raw_posting, "amount"))
    if "amount_usdt" in raw_posting:
        alias = decimal_amount(raw_posting["amount_usdt"], "amount_usdt")
        if amount != alias:
            raise ValueError("POSTING_AMOUNT_ALIAS_CONFLICT")
    timestamp = read_required(raw_posting, "timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
        raise ValueError("INVALID_POSTING_TIMESTAMP")
    currency = raw_posting.get("currency", "USDT")
    if currency != "USDT":
        raise ValueError(f"UNSUPPORTED_LEDGER_CURRENCY:{currency}")
    canonical = {
        "posting_id": posting_id,
        "trade_id": trade_id,
        "event_id": event_id,
        "posting_type": posting_type,
        "amount": str(amount.normalize()),
        "timestamp": timestamp,
        "currency": currency,
    }
    previous = ledger.postings_by_id.get(posting_id)
    if previous is not None:
        if previous != canonical:
            raise ValueError(f"POSTING_ID_PAYLOAD_CONFLICT:{posting_id}")
        return False
    cash_before = decimal_amount(ledger.cash, "cash")
    cash_after = cash_before + amount
    ledger.postings_by_id[posting_id] = canonical
    ledger.postings.append(canonical)
    ledger.cash = cash_after
    return True


def assert_exact_id_set(expected_rows: List[Any], actual_rows: List[Any], id_field: str = "id") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    PHẦN 74, 139: Strictly verifies that the set of actual row IDs exactly matches the expected row IDs.
    Fails on any missing, extra, empty, or duplicate ID.
    """
    def _index(rows):
        out = {}
        for row in rows:
            key = str(read_required(row, id_field)).strip()
            if not key:
                raise ValueError("ARTIFACT_MISSING_TRADE_ID")
            if key in out:
                raise ValueError(f"ARTIFACT_DUPLICATE_TRADE_ID:{key}")
            out[key] = row
        return out

    exp = _index(expected_rows)
    act = _index(actual_rows)
    if set(exp.keys()) != set(act.keys()):
        diff_missing = sorted(list(set(exp.keys()) - set(act.keys())))
        diff_extra = sorted(list(set(act.keys()) - set(exp.keys())))
        raise ValueError(
            f"ARTIFACT_ID_SET_MISMATCH:missing={diff_missing}:extra={diff_extra}"
        )
    return exp, act


def assert_finite_equal(expected: Any, actual: Any, tolerance: float, path: str):
    """
    PHẦN 76, 139: Compares numeric values strictly ensuring both are finite numbers.
    Rejects None, bool, NaN, and Inf immediately.
    """
    for val in (expected, actual, tolerance):
        if val is None or isinstance(val, bool):
            raise ValueError(f"ARTIFACT_INVALID_NUMBER:{path}")
        try:
            parsed = float(val)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"ARTIFACT_INVALID_NUMBER:{path}") from exc
        if not math.isfinite(parsed):
            raise ValueError(f"ARTIFACT_NONFINITE_NUMBER:{path}")

    e, a, tol = float(expected), float(actual), float(tolerance)
    if tol < 0:
        raise ValueError(f"ARTIFACT_INVALID_TOLERANCE:{path}")
    if abs(e - a) > tol:
        raise ValueError(f"ARTIFACT_VALUE_MISMATCH:{path}:expected={e}:actual={a}")
