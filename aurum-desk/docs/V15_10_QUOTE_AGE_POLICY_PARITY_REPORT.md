# V15.10 EXECUTION QUOTE AGE POLICY PARITY REPORT

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline SHA:** `aedf86d77b5359058ced7193c94972e10e668489`
- **Version:** V15.10
- **Date:** 2026-10-15

---

## 1. Executive Summary & Defect Root Cause

### The Defect: Hardcoded Freshness Threshold in Source-State Extractor
In V15.9, `extract_execution_quote_source_state()` in `backend/services/quote_validator.py` was introduced to preserve source rejection authority across representations. However, in the `CanonicalQuote` branch, the logic included:
```python
rejected = (
    status_str != "VALID"
    or not raw_quote.is_valid
    or rej_code is not None
    or raw_quote.freshness_sec > 15.0  # <-- BUG
)
is_stale = bool(status_str == "STALE" or rej_code == "TICKER_STALE" or raw_quote.freshness_sec > 15.0)  # <-- BUG
```

### Impact & Asymmetry:
1. **Double Age Threshold:** This created a hardcoded 15.0s age check inside source-state extraction, completely bypassing the caller's `max_age_sec` argument passed to `normalize_execution_quote(quote, max_age_sec=...)`.
2. **Representation Parity Mismatch:**
   - A `CanonicalQuote` object with age 20.0s, `status="VALID"`, and caller specifying `max_age_sec=60.0` was rejected by the object path as `TICKER_STALE`.
   - The equivalent dictionary (`cq.to_dict()`) did NOT have `freshness_sec > 15.0` in its extraction branch, and was evaluated by `QuoteValidator.validate_ticker()` with `max_age_sec=60.0`, resulting in `is_valid=True`.
   - Result: `normalize(obj) != normalize(obj.to_dict())` under custom `max_age_sec`.
3. **Stale Metadata on Fresh Timestamps:**
   - A `CanonicalQuote` object with fresh timestamp (e.g. timestamp at `now_ms`, age 0.0s) but stale metadata `freshness_sec=20.0` was rejected as `TICKER_STALE`, even though the quote was freshly received.

---

## 2. Before & After Reproduction

### Reproduction 1: Age 20.0s with `max_age_sec=60.0`
#### Before (Baseline V15.9):
```python
cq = CanonicalQuote(
    symbol="XAUUSDT",
    bid=2620.0, ask=2620.5, last=2620.25,
    status="VALID",
    exchange_ts_ms=980_000, received_at_ms=980_000,
    freshness_sec=20.0,
)
res_obj = normalize_execution_quote(cq, now_ms=1_000_000, max_age_sec=60.0)
res_dict = normalize_execution_quote(cq.to_dict(), now_ms=1_000_000, max_age_sec=60.0)

# res_obj: is_valid=False, is_stale=True, code="TICKER_STALE"
# res_dict: is_valid=True, is_stale=False, code=None
# Result: AssertionError: PARITY MISMATCH!
```

#### After (Patched V15.10):
```python
res_obj = normalize_execution_quote(cq, now_ms=1_000_000, max_age_sec=60.0)
res_dict = normalize_execution_quote(cq.to_dict(), now_ms=1_000_000, max_age_sec=60.0)

# res_obj: is_valid=True, is_stale=False, code=None, freshness_sec=20.0
# res_dict: is_valid=True, is_stale=False, code=None, freshness_sec=20.0
# Result: FULL PARITY & VALID
```

---

### Reproduction 2: Default Threshold (15.0s) Preserved
#### Verification:
```python
# Same quote evaluated without max_age_sec (default 15.0s)
res_obj = normalize_execution_quote(cq, now_ms=1_000_000)
res_dict = normalize_execution_quote(cq.to_dict(), now_ms=1_000_000)

# res_obj: is_valid=False, is_stale=True, code="TICKER_STALE", freshness_sec=20.0
# res_dict: is_valid=False, is_stale=True, code="TICKER_STALE", freshness_sec=20.0
# Result: FULL PARITY & REJECTED UNDER DEFAULT THRESHOLD
```

---

### Reproduction 3: Fresh Timestamps with Stale Metadata
#### Before (Baseline V15.9):
```python
cq_fresh_ts = CanonicalQuote(
    symbol="XAUUSDT",
    bid=2620.0, ask=2620.5, last=2620.25,
    status="VALID",
    exchange_ts_ms=now_ms, received_at_ms=now_ms,  # 0s old
    freshness_sec=20.0,  # Stale metadata
)
res_obj = normalize_execution_quote(cq_fresh_ts, now_ms=now_ms)
# Result: is_valid=False, code="TICKER_STALE" (Erroneously rejected due to metadata)
```

#### After (Patched V15.10):
```python
res_obj = normalize_execution_quote(cq_fresh_ts, now_ms=now_ms)
res_dict = normalize_execution_quote(cq_fresh_ts.to_dict(), now_ms=now_ms)
# Result: is_valid=True, is_stale=False, freshness_sec=0.0 on both object and dict
```

---

## 3. Production Code Implementation Details

### File Modified: `backend/services/quote_validator.py`
In `extract_execution_quote_source_state()`, removed the hardcoded `raw_quote.freshness_sec > 15.0` check from the `CanonicalQuote` branch:

```diff
     if isinstance(raw_quote, CanonicalQuote):
         raw_status = raw_quote.status
         status_str = str(raw_status).strip().upper() if raw_status is not None else None
         rej_code = raw_quote.rejection_code
         rej_detail = raw_quote.rejection_detail
 
         rejected = (
             status_str != "VALID"
             or not raw_quote.is_valid
             or rej_code is not None
-            or raw_quote.freshness_sec > 15.0
         )
-        is_stale = bool(status_str == "STALE" or rej_code == "TICKER_STALE" or raw_quote.freshness_sec > 15.0)
+        is_stale = bool(status_str == "STALE" or rej_code == "TICKER_STALE")
```

### Clean Separation of Concerns Established:
1. **`extract_execution_quote_source_state()`:**
   - Pure function with zero DB/network I/O, zero global mutations.
   - Strictly answers: *"Has the quote source explicitly rejected this quote?"*
   - Checks explicit source flags: `status_str != "VALID"`, `not raw_quote.is_valid`, or `rej_code is not None`.
   - Does NOT apply age/freshness thresholds.
2. **`QuoteValidator.validate_ticker()`:**
   - Strictly answers: *"Is the price finite, geometry valid, and timestamp fresh at the point of use?"*
   - Strictly evaluates quote age against `now_ms` and `max_age_sec` (falling back to `DEFAULT_TICKER_MAX_AGE_SEC = 15.0`).
3. **`apply_quote_source_rejection()`:**
   - Purely combines numeric/time validation with source rejection authority:
     `final_is_valid = validated_quote.is_valid and not source_state.rejected`.

---

## 4. Representation Parity Matrix Across Ages and Thresholds

| Quote Properties | `max_age_sec` | CanonicalQuote Object | Dictionary (`to_dict()`) | Parity Status |
|---|---|---|---|---|
| Age 5s, Status VALID | Default (15s) | **VALID** (`is_stale=False`) | **VALID** (`is_stale=False`) | **MATCH** |
| Age 14.9s, Status VALID | Default (15s) | **VALID** (`is_stale=False`) | **VALID** (`is_stale=False`) | **MATCH** |
| Age 15.0s, Status VALID | Default (15s) | **VALID** (`is_stale=False`) | **VALID** (`is_stale=False`) | **MATCH** |
| Age 15.1s, Status VALID | Default (15s) | **STALE** (`TICKER_STALE`) | **STALE** (`TICKER_STALE`) | **MATCH** |
| Age 20s, Status VALID | Default (15s) | **STALE** (`TICKER_STALE`) | **STALE** (`TICKER_STALE`) | **MATCH** |
| Age 20s, Status VALID | `60.0` | **VALID** (`is_stale=False`) | **VALID** (`is_stale=False`) | **MATCH** |
| Age 59.9s, Status VALID | `60.0` | **VALID** (`is_stale=False`) | **VALID** (`is_stale=False`) | **MATCH** |
| Age 60.0s, Status VALID | `60.0` | **VALID** (`is_stale=False`) | **VALID** (`is_stale=False`) | **MATCH** |
| Age 60.1s, Status VALID | `60.0` | **STALE** (`TICKER_STALE`) | **STALE** (`TICKER_STALE`) | **MATCH** |
| Age 0s, Status DISCONNECTED | `60.0` | **REJECTED** (`FEED_DISCONNECTED`) | **REJECTED** (`FEED_DISCONNECTED`) | **MATCH** |
| Age 0s, Status STALE | `60.0` | **REJECTED** (`TICKER_STALE`) | **REJECTED** (`TICKER_STALE`) | **MATCH** |
| Age 0s, Price NaN | `60.0` | **REJECTED** (`NON_FINITE_QUOTE`) | **REJECTED** (`NON_FINITE_QUOTE`) | **MATCH** |

---

## 5. Test Suite Verification & Coverage

### Newly Created Test Suite: `backend/tests/test_v15_10_quote_age_policy_parity.py`
Contains **37 tests** covering:
- Baseline reproduction: age 20s with `max_age_sec=60.0` valid on object and dict.
- Default threshold: age 20s rejected as `TICKER_STALE` on object and dict.
- Metadata 20.0s on fresh timestamp recalculated and accepted.
- Metadata 0.0s on old timestamp rejected under default threshold.
- Custom small threshold (age 6s, max_age 5s) rejected.
- Custom large threshold (age 30s, max_age 60s) accepted.
- Boundary checks at 14.9s, 15.0s, 15.1s (default) and 59.9s, 60.0s, 60.1s (custom 60s).
- Parameter parity for `max_age_sec=None` vs omitted argument.
- Sequential independent calls with alternating thresholds (5s -> 60s -> 5s).
- Increasing `now_ms` aging progression.
- Repeated normalization timestamp non-mutation.
- Explicit source rejections (`status="STALE"`, `status="DISCONNECTED"`) preserved under large `max_age_sec`.
- Status rejection matrix parity under custom age.
- JSON round-trip parity under custom age.
- Three representations parity (`CanonicalQuote`, `dict`, `ValidatedQuote`).
- Non-mutation of input objects and dictionaries.
- Mathematical errors (NaN, Inf, negative price, inverted spread) not bypassed by large `max_age_sec`.
- Pure source-state extractor threshold-free verification.
- Production flow: causal LONG & SHORT fresh entry under default policy.
- Production flow: causal LONG & SHORT blocked when quote is 20s old under default policy.
- Arm direct: fresh quote arms, 20s old quote returns None with zero DB mutations.
- Committed order in DB completely untouched across rejected cycles.

---

## 6. Preservation Guarantees (Rules 007 - 016)

1. **Flow Preservation:** Setup -> Arm -> Fill -> Manage -> Close workflow intact.
2. **SMC Core Logic:** Sweep, break, displacement, OB, FVG, retest, and targets unmodified.
3. **Session Configuration:** Three-session policy (Asian, London, NY) fully preserved.
4. **Trading Quotas:** Daily max fills, position limits, and NY research configurations intact.
5. **Risk and Sizing:** Capital, leverage, margin mode, quantity, and friction calculations intact.
6. **SL / TP Geometry:** Planned entry, stop loss, and take profit validation intact.
7. **Guard Pipeline:** Lifecycle, news, lessons, account, feed, and quote guards active.
8. **Transaction Recovery (V15.8):** `_finalize_failed_cycle()`, rollback on database flush failures, and cache invalidation intact.
9. **Environment Isolation:** Zero modifications to live DB, zero real Bitget order executions, zero real Telegram transmissions.
