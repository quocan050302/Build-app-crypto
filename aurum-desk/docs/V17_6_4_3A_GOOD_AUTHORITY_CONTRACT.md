# AURUM V17.6.4.3a Evidence Authority Contract

## Executive Summary
Prior to V17.6.4.3a, an observation evidence record could claim `quality="GOOD"` simply by presenting a typed `ObservationEvidenceQuality` dataclass or dictionary with boolean flags set to `True` (e.g. `quote_valid=True`, `owner_verified=True`, `transport_state="CONNECTED"`).
This represented a data-label authorization flaw (**AC43A-03, AC43A-04**): callers could manufacture the appearance of verified feed quality without providing canonical quote data from the exchange.

Under **Master Prompt V17.6.4.3a**, **positive GOOD observation quality can only be authorized by authenticating canonical quote provenance**.

---

## Canonical Quote Verification Requirements

To establish authoritative `GOOD` coverage, every cycle and heartbeat must provide a canonical quote snapshot meeting all of the following requirements:

1. **Symbol Verification**: Canonical symbol must strictly match `XAUUSDT`.
2. **Trusted Feed Boundary**: Provenance must originate from trusted Bitget WebSocket or authoritative REST feed.
3. **Transport State**: Transport state must be actively `CONNECTED` or verified reconnecting.
4. **Finite & Positive Bid/Ask**:
   - `bid > 0.0` and `math.isfinite(bid)`
   - `ask >= bid` and `math.isfinite(ask)`
5. **Bid/Ask Spread Integrity**: Spread must be non-negative and within reasonable market thresholds.
6. **Quote Timestamp Validity**:
   - Server timestamp must be present and valid integer millisecond epoch.
   - Future skew cannot exceed maximum acceptable drift (5,000 ms).
7. **Quote Freshness & Age**:
   - Calculated age `(now_ms - quote_timestamp_ms)` must be <= 15,000 ms (or session max threshold).
   - Local receive timestamp must be fresh.
   - Contradictory age fields are rejected.
8. **Origin & Mode Separation**:
   - Only `LIVE_FORWARD` observation origin can establish live forward uptime.
   - `REPLAY` and `TEST` runs are strictly isolated and cannot update live forward health.

---

## Prohibited Patterns

### 1. No Typed Dataclass Bypass
Constructing `ObservationEvidenceQuality(quality="GOOD", ...)` without an accompanying canonical quote is rejected. The engine observability service automatically degrades such heartbeats to `UNVERIFIED`.

### 2. No Worker Role Source Fabrication
Claiming a worker role of `WATCHER_PROCESS` or `SCHEDULER` does not authorize `quote_source="WS"`. If no verified quote snapshot is attached to the evaluation cycle, the system will not fabricate transport proof.

### 3. Preserved Legitimate Negative Proof
While positive `GOOD` requires complete canonical quote proof, negative evidence (e.g., explicit socket disconnection or server error) can legitimately establish `DOWN` or `DEGRADED` status without requiring a fresh quote.
