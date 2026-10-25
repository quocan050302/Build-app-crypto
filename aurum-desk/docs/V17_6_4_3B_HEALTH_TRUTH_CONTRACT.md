# AURUM V17.6.4.3b — CURRENT EVIDENCE HEALTH TRUTH CONTRACT
**Version:** V17.6.4.3b  
**Scope:** Reconciled Observation Evidence Health & Dashboard Snapshot Truth  
**Contract Author:** Senior Engine Observability Engineer & Systems Auditor  

---

## 1. Problem Statement & Defect CFP-06 / CFP-08

In earlier versions, `build_observation_dashboard_snapshot()` evaluated `current_observation_evidence_health` based solely on the existence of a recent process heartbeat, cycle, and interval. If the market feed was `UNVERIFIED` (e.g. quote from an unknown source or missing timestamps), the snapshot could fall through to returning `"HEALTHY"`. This created a dangerous disagreement where the feed was unverified, yet the dashboard indicated a healthy observation state.

In V17.6.4.3b:
1. `feed_qual` is authoritatively derived from `build_market_feed_health_snapshot()` and `resolve_observation_evidence_quality()`.
2. `current_observation_evidence_health` **requires** `feed_qual == "GOOD"` as a mandatory precondition before it can ever be `"HEALTHY"`.
3. If `feed_qual` is `UNVERIFIED`, `current_observation_evidence_health` must be `UNVERIFIED`.
4. If `feed_qual` is `DOWN`, `current_observation_evidence_health` must be `DOWN`.
5. If `feed_qual` is `DEGRADED`, `current_observation_evidence_health` must be `DEGRADED`.
6. Disagreements between process health and feed health are transparently preserved: the engine process state will accurately show `RUNNING`, but observation evidence health will be `UNVERIFIED` or `DEGRADED`.

---

## 2. Decision Tree Specification

```
                  ┌─────────────────────────────────────┐
                  │ Engine Heartbeat Age > 45s          │
                  │ or State in (STOPPED, STALLED)      │
                  └──────────────────┬──────────────────┘
                                     │ YES
                    ┌────────────────┴────────────────┐
                    │ "OFFLINE" if STOPPED else       │
                    │ "STALLED"                       │
                    └─────────────────────────────────┘
                                     │ NO
                  ┌──────────────────┴──────────────────┐
                  │ Has Pending Commit Errors           │
                  │ or Pending Intervals > 0            │
                  └──────────────────┬──────────────────┘
                                     │ YES
                    ┌────────────────┴────────────────┐
                    │ "DEGRADED"                      │
                    └─────────────────────────────────┘
                                     │ NO
                  ┌──────────────────┴──────────────────┐
                  │ Feed Quality == "DOWN"              │
                  └──────────────────┬──────────────────┘
                                     │ YES
                    ┌────────────────┴────────────────┐
                    │ "DOWN"                          │
                    └─────────────────────────────────┘
                                     │ NO
                  ┌──────────────────┴──────────────────┐
                  │ Feed Quality == "DEGRADED"          │
                  └──────────────────┬──────────────────┘
                                     │ YES
                    ┌────────────────┴────────────────┐
                    │ "DEGRADED"                      │
                    └─────────────────────────────────┘
                                     │ NO
                  ┌──────────────────┴──────────────────┐
                  │ Feed Quality != "GOOD"              │
                  │ (e.g. "UNVERIFIED", None)           │
                  └──────────────────┬──────────────────┘
                                     │ YES
                    ┌────────────────┴────────────────┐
                    │ "UNVERIFIED"                    │
                    └─────────────────────────────────┘
                                     │ NO (Feed is GOOD)
                  ┌──────────────────┴──────────────────┐
                  │ Last Verified Interval Gap > 90s    │
                  └──────────────────┬──────────────────┘
                                     │ YES
                    ┌────────────────┴────────────────┐
                    │ "STALLED"                       │
                    └─────────────────────────────────┘
                                     │ NO
                  ┌──────────────────┴──────────────────┐
                  │ Missing Cycle or Missing Interval   │
                  └──────────────────┬──────────────────┘
                                     │ YES
                    ┌────────────────┴────────────────┐
                    │ "UNVERIFIED"                    │
                    └─────────────────────────────────┘
                                     │ NO
                    ┌────────────────┴────────────────┐
                    │ "HEALTHY"                       │
                    └─────────────────────────────────┘
```

---

## 3. Verified Verification Matrix (EH01 – EH16)

All 16 invariants (`EH01` through `EH16`) are actively verified by `backend/tests/test_v17_6_4_3b_health_truth.py` with 100% pass rate.
