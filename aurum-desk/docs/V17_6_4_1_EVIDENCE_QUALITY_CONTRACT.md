# AURUM DESK V17.6.4.1 — EVIDENCE QUALITY CONTRACT
## Authoritative Telemetry Quality Determination & Boundary Transition Semantics

### 1. Architectural Philosophy
In AURUM V17.6.4.1, observation telemetry is governed by the **Fail-Closed Principle**:
> *A process heartbeat without verified fresh quote evidence MUST NEVER produce GOOD observation coverage.*

Prior versions contained a defect where an active process heartbeat defaulted to `GOOD` feed quality even when market data was unavailable or unverified. V17.6.4.1 completely eliminates optimistic defaults. Missing evidence is strictly classified as `UNVERIFIED` or `DEGRADED`, never `GOOD`.

---

### 2. Evidence Quality Classification Matrix

The pure evaluation contract `resolve_observation_evidence_quality(...)` evaluates telemetry snapshots deterministically against strict rules:

| Condition / Symptom | Evidence Quality | Reason Code | Telemetry Action |
| :--- | :---: | :--- | :--- |
| Missing feed metadata / null quote | `UNVERIFIED` | `FEED_EVIDENCE_UNAVAILABLE` | No `GOOD` interval created |
| Connecting transport / warm-up | `UNVERIFIED` | `QUOTE_MISSING_BID_OR_ASK` | Unverified interval |
| Symbol mismatch (not `XAUUSDT`) | `UNVERIFIED` | `SYMBOL_MISMATCH_{symbol}` | Fail-closed |
| Future quote clock skew (`> 60s`) | `DEGRADED` | `QUOTE_FUTURE_CLOCK_SKEW` | Degraded interval |
| Past quote age (`> 3.0s`) | `DEGRADED` | `QUOTE_STALE_{age}S` | Degraded interval |
| Crossed book (`ask < bid`) | `DEGRADED` | `QUOTE_CROSSED_BOOK_ASK_LESS_THAN_BID` | Degraded interval |
| Non-positive bid (`bid <= 0`) | `DEGRADED` | `QUOTE_INVALID_NON_POSITIVE_BID` | Degraded interval |
| Non-finite numbers (`NaN`, `Inf`) | `DEGRADED` | `QUOTE_INVALID_NON_POSITIVE_BID` | Degraded interval |
| Disconnected transport (`WS DOWN`) | `DOWN` / `DEGRADED` | `TRANSPORT_DISCONNECTED` | Down/degraded interval |
| REST fallback active (`WS DOWN`) | `DEGRADED` | `REST_FALLBACK_ACTIVE_TRANSPORT_DISCONNECTED` | Degraded (never full GOOD) |
| Valid, fresh quote (`age <= 3.0s`, finite, bid > 0, ask >= bid) | **`GOOD`** | `AUTHORITATIVE_QUOTE_VERIFIED_FRESH` | Eligible for `GOOD` interval |

---

### 3. Conservative Boundary Transition Semantics (EVID-05)

When observation quality transitions (e.g. from `GOOD` to `DEGRADED` or vice-versa), optimistic boundary assignment would overstate `GOOD` coverage.

V17.6.4.1 implements conservative boundary semantics in `split_observation_quality_transition(...)`:
```
   [Active GOOD Interval]              [Gap]               [New Interval]
|----------------------------|........................|----------------------->
active.start_ms           active.last_seen_ms       new_event_ts
```

1. **Closing Active Interval**:
   - The active interval is closed strictly at its last proven heartbeat timestamp: `active.last_seen_ms` (or `last_proven_ts`).
   - Duration: `active.last_seen_ms - active.start_ms`.
   - Quality: retains its proven quality (e.g. `GOOD`).

2. **Bridging the Unobserved Gap**:
   - If `new_event_ts > active.last_seen_ms`, the intervening span `[active.last_seen_ms, new_event_ts]` is unproven.
   - It is materialized as an `UNVERIFIED` gap interval (or `DEGRADED` if both sides were degraded).
   - It is **NEVER** assigned to `GOOD`.

3. **Opening New Interval**:
   - The new interval begins strictly at `new_event_ts` with the new quality status.

---

### 4. Mathematical Invariants
For any sequence of transitions across window $[T_{start}, T_{end}]$:
1. $\sum \text{Duration}(Interval_i) = T_{end} - T_{start}$ (no temporal inflation or omission).
2. For all $i$ where $\text{Quality}(Interval_i) = \text{GOOD}$, every sub-millisecond is backed by confirmed fresh quotes with $\text{age} \le 3.0\text{s}$.
3. Zero tolerance for unverified data interpolation.
