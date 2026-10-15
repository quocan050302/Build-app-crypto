# V15.9 EXECUTION QUOTE SOURCE REJECTION AUTHORITY ACROSS REPRESENTATIONS REPORT

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline SHA:** `3759fe5e2d2efe218b1e6fb0ac064b5f676fd3ab`
- **Version:** V15.9
- **Date:** 2026-10-15

---

## 1. Executive Summary & Defect Root Causes

### Defect 1: Execution Quote Source Rejection Lost Across Representations
- **Mechanism in Baseline:** In `normalize_execution_quote()`, the dictionary processing branch only verified rejection via:
  ```python
  if raw_quote.get("is_valid") is False:
      ...
  ```
  When a quote originated from `CanonicalQuote.to_dict()` or another dictionary source with `status="DISCONNECTED"` or `is_stale=True` and `rejection_code="FEED_DISCONNECTED"` / `"TICKER_STALE"`, the dictionary lacked an explicit `is_valid: False` field (or set `is_valid: True` while maintaining `is_stale: True`).
- **Timestamp Washing:** The dictionary branch extracted prices and timestamps into a temporary dictionary `t_dict` and passed them to `QuoteValidator.validate_ticker()`. If the timestamps were fresh (`server_time` / `observed_at` within 15 seconds), numeric and freshness validation evaluated to `is_valid=True`. The prior source rejections (`status`, `is_stale`, `rejection_code`) were dropped entirely, "washing" a rejected quote into a valid execution quote.
- **System Impact:** Orders could be armed in production cycles against disconnected feeds or stale quotes simply because the quote was serialized to a dictionary before normalization.

### Defect 2: Representation Parity Asymmetry Between Object and Dictionary
- **Mechanism in Baseline:** In baseline V15.8:
  - Passing a `CanonicalQuote` object with `status="DISCONNECTED"` was correctly rejected because `normalize_execution_quote()` checked `if not raw_quote.is_valid:`.
  - Serializing the exact same object via `to_dict()` and passing it to `normalize_execution_quote()` caused it to be accepted as valid if timestamps were fresh.
- **System Impact:** Round-trip invariance was violated: `normalize(obj) != normalize(obj.to_dict())`. Downstream subsystems receiving serialized quotes through internal queues, REST fallbacks, or dictionaries had execution quote rejection authority stripped away.

---

## 2. Before & After Reproduction

### Reproduction 1: Stale Dictionary Quote with Fresh Timestamps
#### Before (Baseline V15.8):
```python
stale_dict = {
    "bid": 2620.0,
    "ask": 2620.5,
    "last": 2620.25,
    "is_valid": True,
    "is_stale": True,
    "rejection_code": "TICKER_STALE",
    "server_time": now_ms,
    "observed_at": now_ms,
}
vq = normalize_execution_quote(stale_dict, now_ms=now_ms)
# Result: vq.is_valid == True, vq.is_stale == False (Source rejection dropped!)
```

#### After (Patched V15.9):
```python
vq = normalize_execution_quote(stale_dict, now_ms=now_ms)
# Result:
assert vq.is_valid is False
assert vq.is_stale is True
assert vq.rejection_code == "TICKER_STALE"
```

---

### Reproduction 2: Disconnected Feed Dictionary with Fresh Timestamps
#### Before (Baseline V15.8):
```python
disconn_dict = {
    "bid": 2620.0,
    "ask": 2620.5,
    "last": 2620.25,
    "status": "DISCONNECTED",
    "rejection_code": "FEED_DISCONNECTED",
    "server_time": now_ms,
    "observed_at": now_ms,
}
vq = normalize_execution_quote(disconn_dict, now_ms=now_ms)
# Result: vq.is_valid == True (Washed into valid quote!)
```

#### After (Patched V15.9):
```python
vq = normalize_execution_quote(disconn_dict, now_ms=now_ms)
# Result:
assert vq.is_valid is False
assert vq.rejection_code == "FEED_DISCONNECTED"
assert vq.is_stale is False  # Genuine freshness preserved, not fabricated to 9999
```

---

### Reproduction 3: Round-Trip Parity Between Object and Dictionary
#### Before (Baseline V15.8):
```python
cq = CanonicalQuote(
    symbol="XAUUSDT",
    bid=2620.0,
    ask=2620.5,
    last=2620.25,
    status="DISCONNECTED",
    rejection_code="FEED_DISCONNECTED",
    exchange_ts_ms=now_ms,
    received_at_ms=now_ms,
)
norm_obj = normalize_execution_quote(cq, now_ms=now_ms)
norm_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms)
# Result: norm_obj.is_valid (False) != norm_dict.is_valid (True)  -> INVARIANT VIOLATION!
```

#### After (Patched V15.9):
```python
norm_obj = normalize_execution_quote(cq, now_ms=now_ms)
norm_dict = normalize_execution_quote(cq.to_dict(), now_ms=now_ms)
# Result:
assert norm_obj.is_valid == norm_dict.is_valid == False
assert norm_obj.is_stale == norm_dict.is_stale == False
assert norm_obj.rejection_code == norm_dict.rejection_code == "FEED_DISCONNECTED"
```

---

## 3. Production Code Implementation Details

### 1. `CanonicalQuote.to_dict()` Additive Extension
- Added `is_valid: self.is_valid` and `rejection_detail: self.rejection_detail` additively to `CanonicalQuote.to_dict()`.
- Backward-compatible: retains all existing fields (`symbol`, `last`, `bid`, `ask`, `status`, `server_time`, `observed_at`, etc.).

### 2. `QuoteSourceState` Dataclass
Encapsulates extracted source-level authority across representations:
```python
@dataclass
class QuoteSourceState:
    rejected: bool
    is_stale: bool
    rejection_code: Optional[str]
    rejection_detail: Optional[str]
    status: Optional[str] = None
```

### 3. `parse_optional_bool()` Helper
- Safeguards boolean parsing to prevent the Python pitfall where `bool("false") == True`.
- Correctly parses `True`, `False`, `"true"`, `"false"`, `"0"`, `"1"`, `0`, `1`.
- Returns `None` for unparseable strings or objects, enabling detection of malformed booleans (`MALFORMED_QUOTE`).

### 4. `extract_execution_quote_source_state(raw_quote) -> QuoteSourceState`
- Pure function with zero DB/network I/O, zero global mutations.
- **For `ValidatedQuote`:** Evaluates `not is_valid or is_stale or rejection_code is not None`.
- **For `CanonicalQuote`:** Evaluates `status != "VALID" or not is_valid or rejection_code is not None or freshness_sec > 15.0`. Maps canonical statuses (`DISCONNECTED`, `STALE`, `INVERTED`, `TIME_UNAVAILABLE`, `FUTURE_TIMESTAMP`, `MALFORMED`).
- **For `dict`:** Parses boolean flags (`is_valid`, `is_stale`), reads `status` and `rejection_code`. Enforces conflict resolution:
  * `is_valid=True` cannot override `is_stale=True`.
  * `is_valid=True` cannot override `status="DISCONNECTED"`.
  * Status outside known contract is rejected safely.
  * Raw tickers without `status` or `is_valid` are NOT rejected at source state (passed to numeric/time validation).

### 5. `apply_quote_source_rejection(validated_quote, source_state) -> ValidatedQuote`
- Combines numeric/time validation with source rejection authority:
  * Rule: `final_is_valid = validated_quote.is_valid and not source_state.rejected`.
  * Stale rule: `final_is_stale = bool(validated_quote.is_stale or source_state.is_stale)`.
  * Deterministic reason priority:
    1. `FEED_DISCONNECTED` takes precedence when source is disconnected.
    2. Mathematical/numeric corruption (`NON_FINITE_QUOTE`, `ZERO_OR_NEGATIVE_PRICE`, `INVERTED_SPREAD`, `MALFORMED_QUOTE`) takes precedence when prices are corrupted.
    3. Source rejection code (`TICKER_STALE`, etc.) applied next.
    4. Numeric validation reason applied as fallback.
  * Preserves actual `freshness_sec` without spoofing or fabricating 9999.

### 6. Refactored `normalize_execution_quote(raw_quote, *, now_ms=None, max_age_sec=None) -> ValidatedQuote`
- Standardized pipeline across all input representations:
  1. Input type validation (None -> `FEED_DISCONNECTED`, unsupported type -> `MALFORMED_QUOTE`).
  2. Source state extraction: `source_state = extract_execution_quote_source_state(raw_quote)`.
  3. Payload mapping to standard price/time dict (`t_dict`).
  4. Numeric & freshness validation: `vq_num = QuoteValidator.validate_ticker(t_dict, now_ms=now_ms, max_age_sec=max_age_sec)`.
  5. Combination step: `return apply_quote_source_rejection(vq_num, source_state)`.

---

## 4. Representation Parity Matrix

| Source Representation | Input State | Numeric/Time State | Normalized `is_valid` | Normalized `is_stale` | Normalized `rejection_code` |
|---|---|---|---|---|---|
| `CanonicalQuote` | `status="VALID"` | Fresh (`freshness_sec=0.1`) | `True` | `False` | `None` |
| `dict` (via `to_dict()`) | `status="VALID"` | Fresh (`freshness_sec=0.1`) | `True` | `False` | `None` |
| `JSON string` | `status="VALID"` | Fresh (`freshness_sec=0.1`) | `True` | `False` | `None` |
| `CanonicalQuote` | `status="DISCONNECTED"` | Fresh timestamps | `False` | `False` | `FEED_DISCONNECTED` |
| `dict` (via `to_dict()`) | `status="DISCONNECTED"` | Fresh timestamps | `False` | `False` | `FEED_DISCONNECTED` |
| `JSON string` | `status="DISCONNECTED"` | Fresh timestamps | `False` | `False` | `FEED_DISCONNECTED` |
| `CanonicalQuote` | `status="STALE"` | Fresh timestamps | `False` | `True` | `TICKER_STALE` |
| `dict` | `is_stale=True, is_valid=True` | Fresh timestamps | `False` | `True` | `TICKER_STALE` |
| `ValidatedQuote` | `is_valid=True, is_stale=True` | Fresh timestamps | `False` | `True` | `TICKER_STALE` |
| `Raw ticker dict` | No `status`, no `is_valid` | Fresh timestamps | `True` | `False` | `None` |
| `Raw ticker dict` | No `status`, no `is_valid` | 999s stale | `False` | `True` | `TICKER_STALE` |
| `Any` | Any | Corrupted price (NaN) | `False` | - | `NON_FINITE_QUOTE` |

---

## 5. Test Suite Verification & Coverage

### Newly Created Test Suite: `backend/tests/test_v15_9_quote_source_rejection.py`
Contains **65 tests** covering:
- **Baseline reproductions:** stale dict rejection, disconnected dict rejection, round-trip parity.
- **Source-state interpretation:** stale flag without code, disconnected status without code, `is_valid=False` preservation.
- **Conflict resolution:** `is_valid=True` + `is_stale=True`, `status="VALID"` + `is_stale=True`, `status="DISCONNECTED"` + `is_valid=True`.
- **Rejection metadata:** non-empty `rejection_code`, `rejection_detail` preservation.
- **Status matrix:** `DISCONNECTED`, `STALE`, `MALFORMED`, `INVERTED`, `TIME_UNAVAILABLE`, `FUTURE_TIMESTAMP`.
- **Schema edge cases:** unknown status, missing status, missing `is_valid`, boolean strings (`"true"`, `"false"`, `"0"`, `"1"`), malformed boolean types.
- **Object handling:** `ValidatedQuote` invalid and stale conflict, `CanonicalQuote` valid and disconnected object.
- **Serializations:** JSON serialization round-trip preservation.
- **Immutability:** non-mutation of input dictionaries and objects.
- **Lifecycle & recovery:** repeated normalization idempotency, clean recovery when fresh valid quote arrives (no sticky global block).
- **Numeric & freshness validation:** 999s aging, missing timestamp, NaN price, inverted spread, future timestamp, dual failure priority.
- **Production integration:**
  * Direct arming `_arm_paper_candidate()` with stale dict returns `None`, zero mutations (`WatchSetup`, `SMCScenarioSnapshot`, `PaperOrder`).
  * Direct arming with disconnected dict returns `None`, zero mutations.
  * Direct arming round-trip parity (`cq` vs `cq.to_dict()`).
  * Causal LONG entry blocked with stale dict.
  * Causal LONG entry blocked with disconnected dict.
  * Causal SHORT entry blocked with stale dict.
  * Causal SHORT entry blocked with disconnected dict.
  * Positive control causal LONG with fresh quote arms order properly.
  * Positive control causal SHORT with fresh quote arms order properly.
  * Positive control raw ticker dict arms order properly.
  * Positive control canonical quote dict arms order properly.
  * Multi-cycle recovery: rejected cycle followed by valid cycle arms order cleanly.
  * Existing committed order remains untouched across rejected cycles.

---

## 6. Preservation Guarantees (Rules 005 - 013)

1. **Flow Preservation:** Setup -> Arm -> Fill -> Manage -> Close workflow remains intact.
2. **SMC Core Logic:** No changes to OB/FVG, liquidity sweep, break, displacement, retest, or target selection.
3. **Session Configuration:** Three-session policy (Asian, London, NY) fully preserved.
4. **Trading Quotas:** Daily max fills, position limits, and NY research configurations intact.
5. **Risk and Sizing:** Capital, leverage, margin mode, quantity, and friction calculations intact.
6. **SL / TP Geometry:** Long and Short planned entry, stop loss, and take profit validation intact.
7. **Guard Pipeline:** Lifecycle, news, lessons, account, feed, and quote guards active.
8. **Transaction Recovery (V15.8):** `_finalize_failed_cycle()`, rollback on database flush failures, and cache invalidation intact.
9. **Environment Isolation:** Zero modifications to live DB, zero real Bitget order executions, zero real Telegram transmissions.
